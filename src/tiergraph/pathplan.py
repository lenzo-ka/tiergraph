"""Compile a path fold once and evaluate it again under new values.

A ``FoldDeclaration`` over one ``OR`` relation on an acyclic dependency graph is
a finite path graph: every derivation is a root-to-sink path, and its value is
the product of the local values along it. ``PathPlan`` compiles that
declaration's topology once -- its domain items in canonical order, their child
incidence, an evaluation order -- and evaluates it under any vector of carrier
values given in that order. A caller whose values change between runs while
the graph does not (an expectation step, a reweighting, a sweep over
parameters) pays for the traversal and the topology checks once rather than at
every run.

``evaluate`` returns what ``FoldDeclaration.run`` returns for the same values,
and ``marginals`` adds the outside pass: for every item, the sum over the
derivations that pass through it, which the log-probability algebra reads out
as a posterior.
"""

from __future__ import annotations

import math
import operator
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from itertools import repeat
from typing import Any, cast

from tiergraph.core import Graph, ItemRef, QualifiedName
from tiergraph.fold import (
    ChildCombination,
    DerivationProvenance,
    FoldCost,
    FoldDeclaration,
    FoldResult,
    State,
    TiePolicy,
)
from tiergraph.semiring import DoubleExtremumSemiring, LogProbabilitySemiring, Semiring

type _Gather = Callable[[Sequence[Any]], tuple[Any, ...]]
type _Selected[Value] = tuple[Value, DerivationProvenance]
type _InsideSchedule = tuple[tuple[int, int, Any], ...]
type _OutsideSchedule = tuple[tuple[int, int, Any, bool], ...]
type _Extreme = Callable[..., float]
_NEGATIVE = -math.inf
_POSITIVE = math.inf


@dataclass(frozen=True, slots=True)
class AlgebraOrder[Value]:
    """Compare carrier values by the algebra's own selective addition.

    A fold accepts it as a ``witness_order``: the preferred operand is the one
    the algebra's addition returns, and equal values tie. ``PathPlan``
    recognizes it and fuses the selection into its schedule, so a best path
    under ``ARCTIC`` or ``TROPICAL`` is found in the same pass that values the
    graph, with no comparator calls. The algebra must declare
    ``add_selective``: an aggregating addition names no winner to order by.
    """

    algebra: Semiring[Value]

    def __post_init__(self) -> None:
        """Refuse an algebra whose addition names no winner."""
        if not self.algebra.add_selective:
            raise ValueError(
                f"algebra {type(self.algebra).__name__!r} does not declare "
                "add_selective, so its addition names no winner to order by"
            )

    def __call__(self, left: Value, right: Value, /) -> int:
        """Return negative for left, positive for right, or zero for a tie."""
        if left == right:
            return 0
        return -1 if self.algebra.add(left, right) == left else 1


@dataclass(frozen=True, slots=True)
class PathPosteriors:
    """Probabilities read out of a plan's marginals, with the readout named.

    ``readout`` is the name the caller declared and the algebra method that
    produced ``values``, so the result says which post-pass above the algebra
    it applied. ``zero_mass`` is
    true when the total was the algebra's zero; ``values`` is then ``None``,
    because there is no distribution to report and none is fabricated.
    """

    readout: str
    zero_mass: bool
    values: tuple[float, ...] | None


@dataclass(frozen=True, slots=True)
class PathMarginals[Value]:
    """The inside and outside passes of one evaluation, per item in plan order.

    ``inside[i]`` is the fold value at item ``i``, the sum over the derivations
    rooted there. ``outside[i]`` is the sum over the prefixes that reach it
    from a root, with the multiplicative identity contributed at every root.
    ``marginals[i]`` is their product: the sum over every complete derivation
    through ``i``, which is the zero at a dead end and at an unreachable item.
    ``total`` is the fold value over the roots, and ``cost`` accounts for both
    passes.
    """

    plan: PathPlan[Value]
    total: Value
    inside: tuple[Value, ...]
    outside: tuple[Value, ...]
    marginals: tuple[Value, ...]
    cost: FoldCost

    def posteriors(self, *, readout: str) -> PathPosteriors:
        """Read every marginal as a probability of the total through a declared readout.

        A readout is a division above the algebra, so the caller declares it by
        name and the algebra must publish it in its ``readouts``:
        ``readout="normalize"`` is the one the log-probability carrier
        publishes, and a name the algebra does not list there is refused rather
        than divided by hand, whatever other methods the algebra happens to
        have. The result records the readout it applied. A zero total reports
        ``zero_mass`` with no values.
        """
        algebra = self.plan.declaration.semiring
        published: tuple[str, ...] = getattr(algebra, "readouts", ())
        if readout not in published:
            raise ValueError(
                f"algebra {type(algebra).__name__!r} publishes no {readout!r} "
                "readout; a posterior is read only through one the algebra lists "
                "in its readouts and the caller declares"
            )
        if self.total == algebra.zero:
            return PathPosteriors(readout, True, None)
        method = getattr(algebra, readout)
        return PathPosteriors(readout, False, tuple(method(self.marginals, self.total)))


@dataclass(frozen=True, slots=True)
class _Compiled:
    """Schedules and counts derived once from the topology."""

    states: tuple[State, ...]
    root_states: tuple[State, ...]
    document_size: int
    incidence: int
    inside_additions: int
    inside_multiplications: int
    outside_additions: int
    outside_multiplications: int
    fused: str | None
    select: bool
    doubles: bool
    excluded: float
    positions: dict[ItemRef, int]
    inside_schedule: _InsideSchedule
    outside_schedule: _OutsideSchedule


@dataclass(frozen=True, slots=True)
class PathPlan[Value]:
    """A fold declaration compiled to its path topology, evaluable under new values.

    ``items`` are the declaration's domain items in the graph's canonical
    order, and that order is the plan's value order: ``evaluate`` and
    ``marginals`` take one carrier value per item in it, and ``values`` holds
    the values the declaration itself lifts, so a caller can start from those
    and replace what changed. ``labels`` are the items' durable identities or
    structural labels, the names a fold's provenance spells. A vector of
    another length is refused, because a vector from a different inventory has
    no position that means anything here.

    ``children[i]`` are the indices of item ``i``'s alternatives in canonical
    order, ``parents[i]`` the items it is an alternative of, ``roots`` the
    declared or inferred roots, and ``order`` a children-first evaluation
    order. The plan is what the declaration is: a sink accepts with its own
    value, so a dead end is a sink the caller values at the zero, and an item
    no root reaches contributes nothing.

    ``evaluate`` reproduces ``FoldDeclaration.run`` for the same values, with
    the same provenance under the same ``witness_order`` and ``tie_policy``,
    and the same cost account. Under the log-probability, arctic, and tropical
    carriers the plan runs the algebra's operations in a fused schedule that
    gathers each item's alternatives at once. The operation counts it reports
    are the general schedule's, which the fused schedule performs in gathered
    form, sharing one product across the children it reaches. A gathered
    log-sum-exp sums its exponentials in a different order than pairwise
    addition does, so under the log carrier the two schedules agree within the
    algebra's declared approximation -- at the rounding scale of the operands,
    which on a total near cancellation can be visible in the result -- and
    under the extremum carriers they agree exactly. A selective carrier with
    an ``AlgebraOrder`` and ``CHOOSE_FIRST``
    fuses its selection too. Every other declaration runs the general schedule
    through the algebra's own methods.
    """

    declaration: FoldDeclaration[Value]
    items: tuple[ItemRef, ...]
    labels: tuple[str, ...]
    values: tuple[Value, ...]
    children: tuple[tuple[int, ...], ...]
    parents: tuple[tuple[int, ...], ...]
    roots: tuple[int, ...]
    order: tuple[int, ...]
    _compiled: _Compiled = field(repr=False, compare=False)

    @classmethod
    def prepare(cls, declaration: FoldDeclaration[Value]) -> PathPlan[Value]:
        """Compile the declaration's topology, refusing what a path cannot carry."""
        name = declaration.name
        if declaration.ranked_output:
            raise ValueError(
                f"path plan {name!r} does not carry ranked output; declare a "
                "witness_order and a tie policy instead"
            )
        if declaration.index_axes:
            raise ValueError(
                f"path plan {name!r} does not carry an index product; declare "
                "no index_axes"
            )
        if len(declaration.transitions) != 1:
            raise ValueError(
                f"path plan {name!r} folds exactly one dependency relation, and "
                f"the declaration names {len(declaration.transitions)}"
            )
        transition = declaration.transitions[0]
        if transition.combination is not ChildCombination.OR:
            raise ValueError(
                f"path plan {name!r} relation {str(transition.relation)!r} is "
                "declared AND; a path folds alternatives, and joint children "
                "make a hypergraph rather than a path graph"
            )
        graph = declaration.graph
        tiers = set(declaration.valuation.tiers)
        items = tuple(
            reference
            for reference in graph.canonical_items()
            if reference.tier in tiers
        )
        index = {reference: position for position, reference in enumerate(items)}
        child_lists: list[list[int]] = [[] for _ in items]
        for relation in graph.relations:
            if (
                relation.declaration == transition.relation
                and isinstance(relation.left, ItemRef)
                and isinstance(relation.right, ItemRef)
                and relation.left in index
                and relation.right in index
            ):
                child_lists[index[relation.left]].append(index[relation.right])
        children = tuple(tuple(sorted(links)) for links in child_lists)
        parent_lists: list[list[int]] = [[] for _ in items]
        for parent, links in enumerate(children):
            for child in links:
                parent_lists[child].append(parent)
        parents = tuple(tuple(links) for links in parent_lists)
        if len(set(declaration.roots)) != len(declaration.roots):
            repeated = next(
                reference
                for reference in declaration.roots
                if declaration.roots.count(reference) > 1
            )
            raise ValueError(
                f"path plan {name!r} lists root {repeated.to_data()!r} more than "
                "once; the fold would count its value once per listing and the "
                "outside pass would seat it once, so the passes could not agree"
            )
        roots = tuple(index[reference] for reference in declaration.roots) or tuple(
            position for position, links in enumerate(parents) if not links
        )
        order = _postorder(name, transition.relation, items, children)
        labels = tuple(_label(graph, reference) for reference in items)
        values = tuple(
            declaration.lift(declaration.valuation.read(graph, reference), label)
            for reference, label in zip(items, labels, strict=True)
        )
        compiled = _compile(declaration, items, children, parents, roots, order)
        if compiled.doubles:
            _check_doubles(name, values, compiled.excluded)
        return cls(
            declaration,
            items,
            labels,
            values,
            children,
            parents,
            roots,
            order,
            compiled,
        )

    def index(self, reference: ItemRef) -> int:
        """Return an item's position in the plan's value order."""
        try:
            return self._compiled.positions[reference]
        except KeyError:
            raise ValueError(
                f"path plan {self.declaration.name!r} has no item "
                f"{reference.to_data()!r}"
            ) from None

    def evaluate(self, values: Sequence[Value] | None = None) -> FoldResult[Value]:
        """Fold the compiled topology under these values, or the declaration's own."""
        vector = self._vector(values)
        compiled = self._compiled
        if compiled.fused is None:
            inside, selected = self._inside_general(vector)
        else:
            inside, selected = self._inside_fused(vector)
        provenance: DerivationProvenance | None = None
        witness_count = 0
        if selected is not None:
            provenance = selected[1][: self.declaration.output_cap]
            witness_count = len(selected[1])
        return FoldResult(
            values=tuple(zip(compiled.states, inside, strict=True)),
            roots=compiled.root_states,
            value=self._total(inside),
            provenance=provenance,
            truncated=witness_count > self.declaration.output_cap,
            cost=self._cost(
                compiled.inside_additions,
                compiled.inside_multiplications,
                witness_count,
                0 if provenance is None else len(provenance),
            ),
            ranked_witnesses=None,
        )

    def marginals(self, values: Sequence[Value] | None = None) -> PathMarginals[Value]:
        """Run the inside and outside passes under these values."""
        vector = self._vector(values)
        compiled = self._compiled
        if compiled.fused is None:
            inside, _selected = self._inside_general(vector)
            outside, through = self._outside_general(vector, inside)
        else:
            inside, _selected = self._inside_fused(vector, select=False)
            outside, through = self._outside_fused(vector, inside)
        return PathMarginals(
            self,
            self._total(inside),
            tuple(inside),
            tuple(outside),
            tuple(through),
            self._cost(
                compiled.inside_additions + compiled.outside_additions,
                compiled.inside_multiplications + compiled.outside_multiplications,
                0,
                0,
            ),
        )

    def _vector(self, values: Sequence[Value] | None) -> tuple[Value, ...]:
        """Bind a value vector to the plan order, or take the declaration's."""
        if values is None:
            return self.values
        vector = tuple(values)
        if len(vector) != len(self.items):
            raise ValueError(
                f"path plan {self.declaration.name!r} takes {len(self.items)} "
                f"values in plan item order and was given {len(vector)}"
            )
        compiled = self._compiled
        if compiled.doubles:
            _check_doubles(self.declaration.name, vector, compiled.excluded)
        return vector

    def _total(self, inside: Sequence[Value]) -> Value:
        """Combine the root values the way the fold's accumulator does."""
        semiring = self.declaration.semiring
        total = semiring.zero
        for root in self.roots:
            total = semiring.add(total, inside[root])
        return total

    def _cost(
        self, additions: int, multiplications: int, witness_count: int, emitted: int
    ) -> FoldCost:
        """Account for one run in the fold's own terms."""
        compiled = self._compiled
        return FoldCost(
            document_size=compiled.document_size,
            relation_incidence=compiled.incidence,
            index_product_size=1,
            carrier_additions=additions,
            carrier_multiplications=multiplications,
            carrier_operation_cost=self.declaration.carrier_operation_cost,
            witness_count=witness_count,
            emitted_count=emitted,
            output_cap=self.declaration.output_cap,
        )

    def _select(
        self, left: _Selected[Value], right: _Selected[Value]
    ) -> _Selected[Value]:
        """Apply the declared witness order and tie policy, as the fold does."""
        order = self.declaration.witness_order
        assert order is not None
        comparison = order(left[0], right[0])
        if comparison < 0:
            return left
        if comparison > 0:
            return right
        if self.declaration.tie_policy is TiePolicy.CHOOSE_FIRST:
            return left
        return left[0], tuple(dict.fromkeys((*left[1], *right[1])))

    def _inside_general(
        self, vector: tuple[Value, ...]
    ) -> tuple[list[Value], _Selected[Value] | None]:
        """Evaluate every item through the algebra's methods, children first."""
        semiring = self.declaration.semiring
        add = semiring.add
        multiply = semiring.multiply
        one = semiring.one
        witness = self.declaration.witness_order is not None
        labels = self.labels
        children = self.children
        inside: list[Value] = [semiring.zero] * len(vector)
        paths: list[DerivationProvenance] = [()] * len(vector)
        for item in self.order:
            links = children[item]
            if not links:
                inside[item] = multiply(vector[item], one)
                if witness:
                    paths[item] = ((labels[item],),)
                continue
            relation_value = inside[links[0]]
            best = (relation_value, paths[links[0]])
            for child in links[1:]:
                relation_value = add(relation_value, inside[child])
                if witness:
                    best = self._select(best, (inside[child], paths[child]))
            inside[item] = multiply(vector[item], relation_value)
            if witness:
                head = (labels[item],)
                paths[item] = tuple(head + path for path in best[1])
        selected: _Selected[Value] | None = None
        if witness:
            for root in self.roots:
                candidate = (inside[root], paths[root])
                selected = (
                    candidate if selected is None else self._select(selected, candidate)
                )
        return inside, selected

    def _outside_general(
        self, vector: tuple[Value, ...], inside: list[Value]
    ) -> tuple[list[Value], list[Value]]:
        """Sum the prefixes reaching every item, then multiply through it."""
        semiring = self.declaration.semiring
        add = semiring.add
        multiply = semiring.multiply
        zero = semiring.zero
        one = semiring.one
        roots = set(self.roots)
        parents = self.parents
        outside: list[Value] = [zero] * len(vector)
        for item in reversed(self.order):
            contributions = [
                multiply(outside[parent], vector[parent]) for parent in parents[item]
            ]
            if item in roots:
                value = one
                for contribution in contributions:
                    value = add(value, contribution)
            elif contributions:
                value = contributions[0]
                for contribution in contributions[1:]:
                    value = add(value, contribution)
            else:
                value = zero
            outside[item] = value
        through = [
            multiply(prefix, suffix)
            for prefix, suffix in zip(outside, inside, strict=True)
        ]
        return outside, through

    def _inside_fused(
        self, vector: tuple[Value, ...], *, select: bool = True
    ) -> tuple[list[Value], _Selected[Value] | None]:
        """Evaluate a double carrier with the algebra's operations inlined."""
        compiled = self._compiled
        v = cast(tuple[float, ...], vector)
        if compiled.fused == "log":
            return cast(
                list[Value], _log_inside(self, compiled.inside_schedule, v)
            ), None
        extreme: _Extreme = min if compiled.fused == "min" else max
        choice: list[int] | None = [-1] * len(v) if compiled.select and select else None
        inside = _extreme_inside(
            self, compiled.inside_schedule, v, extreme, compiled.excluded, choice
        )
        result = cast(list[Value], inside)
        if choice is None:
            return result, None
        total = extreme(inside[root] for root in self.roots)
        start = next(root for root in self.roots if inside[root] == total)
        path: list[str] = []
        current = start
        while current >= 0:
            path.append(self.labels[current])
            links = self.children[current]
            current = links[0] if len(links) == 1 else choice[current]
        return result, (cast(Value, inside[start]), (tuple(path),))

    def _outside_fused(
        self, vector: tuple[Value, ...], inside: list[Value]
    ) -> tuple[list[Value], list[Value]]:
        """Sum prefixes with the algebra's operations inlined, parents first."""
        compiled = self._compiled
        v = cast(tuple[float, ...], vector)
        suffix = cast(list[float], inside)
        if compiled.fused == "log":
            outside, carried = _log_outside(self, compiled.outside_schedule, v)
        else:
            extreme: _Extreme = min if compiled.fused == "min" else max
            outside, carried = _extreme_outside(
                self, compiled.outside_schedule, v, extreme, compiled.excluded
            )
        # A product that left the carrier toward the excluded infinity is still
        # in ``carried`` even where a later gather turned it into NaN.
        if compiled.excluded in carried or compiled.excluded in outside:
            raise _overflow(self)
        through = list(map(operator.add, outside, suffix))
        if compiled.excluded in through:
            raise _overflow(self)
        # The zero annihilates, so a product is the zero exactly where an
        # operand is, unless it overflowed there. Counting both sides is the
        # same test as reading every position, without a Python-level loop.
        zero = -compiled.excluded
        annihilated: _Extreme = min if zero == _NEGATIVE else max
        if through.count(zero) != list(map(annihilated, outside, suffix)).count(zero):
            raise _overflow(self)
        return cast(list[Value], outside), cast(list[Value], through)


def _log_inside(
    plan: PathPlan[Any], schedule: _InsideSchedule, v: tuple[float, ...]
) -> list[float]:
    """Run the inside pass under log-sum-exp addition and log-product multiplication."""
    inside = [0.0] * len(v)
    log = math.log
    fsum = math.fsum
    exp = math.exp
    sub = operator.sub
    for item, kind, argument in schedule:
        if kind == 1:
            child_value = inside[argument]
            value = v[item] + child_value
            if value == _NEGATIVE and v[item] != _NEGATIVE and child_value != _NEGATIVE:
                raise _overflow(plan)
        elif kind:
            candidates = argument(inside)
            high = max(candidates)
            if high == _NEGATIVE:
                value = _NEGATIVE
            else:
                # Associated as the fold does it, local ⊗ (sum of alternatives),
                # so the two schedules differ only in the order the
                # exponentials are summed.
                value = v[item] + (
                    high + log(fsum(map(exp, map(sub, candidates, repeat(high)))))
                )
                if value == _NEGATIVE and v[item] != _NEGATIVE:
                    raise _overflow(plan)
        else:
            value = v[item]
        inside[item] = value
    if _POSITIVE in inside:
        raise _overflow(plan)
    return inside


def _extreme_inside(
    plan: PathPlan[Any],
    schedule: _InsideSchedule,
    v: tuple[float, ...],
    extreme: _Extreme,
    excluded: float,
    choice: list[int] | None,
) -> list[float]:
    """Run the inside pass under a selective extremum, recording winners if asked."""
    inside = [0.0] * len(v)
    zero = -excluded
    children = plan.children
    for item, kind, argument in schedule:
        if kind == 1:
            best = inside[argument]
        elif kind:
            candidates = argument(inside)
            best = extreme(candidates)
            if choice is not None:
                choice[item] = children[item][candidates.index(best)]
        else:
            best = 0.0
        value = v[item] + best
        if value == zero and v[item] != zero and best != zero:
            raise _overflow(plan)
        inside[item] = value
    if excluded in inside:
        raise _overflow(plan)
    return inside


def _log_outside(
    plan: PathPlan[Any], schedule: _OutsideSchedule, v: tuple[float, ...]
) -> tuple[list[float], list[float]]:
    """Run the outside pass in the log carrier, carrying each item's product forward."""
    outside = [_NEGATIVE] * len(v)
    carried = [_NEGATIVE] * len(v)
    log = math.log
    log1p = math.log1p
    fsum = math.fsum
    exp = math.exp
    sub = operator.sub
    for item, kind, argument, root in schedule:
        if kind == 1:
            value = carried[argument]
            if root and value != _NEGATIVE:
                # The algebra's own addition of the identity, bit for bit.
                high = value if value > 0.0 else 0.0
                low = value if value <= 0.0 else 0.0
                value = high + log1p(exp(low - high))
            elif root:
                value = 0.0
        elif kind:
            candidates = argument(carried)
            if root:
                candidates = (*candidates, 0.0)
            high = max(candidates)
            value = (
                _NEGATIVE
                if high == _NEGATIVE
                else high + log(fsum(map(exp, map(sub, candidates, repeat(high)))))
            )
        else:
            value = 0.0 if root else _NEGATIVE
        outside[item] = value
        product = value + v[item]
        if product == _NEGATIVE and value != _NEGATIVE and v[item] != _NEGATIVE:
            raise _overflow(plan)
        carried[item] = product
    return outside, carried


def _extreme_outside(
    plan: PathPlan[Any],
    schedule: _OutsideSchedule,
    v: tuple[float, ...],
    extreme: _Extreme,
    excluded: float,
) -> tuple[list[float], list[float]]:
    """Run the outside pass under a selective extremum."""
    zero = -excluded
    outside = [zero] * len(v)
    carried = [zero] * len(v)
    for item, kind, argument, root in schedule:
        if kind == 1:
            value = carried[argument]
            if root:
                value = extreme(value, 0.0)
        elif kind:
            candidates = argument(carried)
            if root:
                candidates = (*candidates, 0.0)
            value = extreme(candidates)
        else:
            value = 0.0 if root else zero
        outside[item] = value
        product = value + v[item]
        if product == zero and value != zero and v[item] != zero:
            raise _overflow(plan)
        carried[item] = product
    return outside, carried


def _overflow(plan: PathPlan[Any]) -> OverflowError:
    """Name the plan whose values left the finite carrier."""
    return OverflowError(
        f"path plan {plan.declaration.name!r} result leaves the finite "
        "IEEE-double carrier"
    )


def _check_doubles(name: str, vector: tuple[Any, ...], excluded: float) -> None:
    """Hold a fused vector to the double carrier's boundary, in bulk."""
    if set(map(type, vector)) - {float} or any(map(math.isnan, vector)):
        raise ValueError(
            f"path plan {name!r} values must be IEEE-double carrier values"
        )
    if excluded in vector:
        raise ValueError(
            f"path plan {name!r} values contain the excluded infinite bound"
        )


def _postorder(
    name: str,
    relation: QualifiedName,
    items: tuple[ItemRef, ...],
    children: tuple[tuple[int, ...], ...],
) -> tuple[int, ...]:
    """Order every item after its children, refusing a cycle by its closing edge."""
    order: list[int] = []
    status = [0] * len(items)
    for start in range(len(items)):
        if status[start]:
            continue
        status[start] = 1
        frames = [(start, 0)]
        while frames:
            item, position = frames[-1]
            links = children[item]
            if position < len(links):
                frames[-1] = (item, position + 1)
                child = links[position]
                if status[child] == 1:
                    raise ValueError(
                        f"path plan {name!r} relation {str(relation)!r} closes a "
                        f"cycle from {items[item].to_data()!r} to "
                        f"{items[child].to_data()!r}; a path graph is acyclic"
                    )
                if status[child] == 0:
                    status[child] = 1
                    frames.append((child, 0))
                continue
            frames.pop()
            status[item] = 2
            order.append(item)
    return tuple(order)


def _label(graph: Graph, reference: ItemRef) -> str:
    """Return an item's durable identity or its canonical structural label."""
    tier = next(
        candidate
        for candidate in graph.tiers
        if candidate.declaration.name == reference.tier
    )
    durable = tier.items[reference.index].durable_id
    if durable is not None:
        return durable
    return f"{reference.tier.namespace}:{reference.tier.local_name}:{reference.index}"


def _gather(indices: tuple[int, ...]) -> _Gather:
    """Return a positional gather over at least two indices."""
    return cast(_Gather, operator.itemgetter(*indices))


def _compile(
    declaration: FoldDeclaration[Any],
    items: tuple[ItemRef, ...],
    children: tuple[tuple[int, ...], ...],
    parents: tuple[tuple[int, ...], ...],
    roots: tuple[int, ...],
    order: tuple[int, ...],
) -> _Compiled:
    """Derive the schedules and operation counts a plan reuses at every run."""
    semiring = declaration.semiring
    witness = declaration.witness_order
    if isinstance(witness, AlgebraOrder) and not _same_algebra(
        witness.algebra, semiring
    ):
        raise ValueError(
            f"path plan {declaration.name!r} orders witnesses by "
            f"{type(witness.algebra).__name__!r} while it sums with "
            f"{type(semiring).__name__!r}; an AlgebraOrder is declared over the "
            "declaration's own algebra"
        )
    fused: str | None = None
    select = False
    excluded = _POSITIVE
    doubles = isinstance(semiring, LogProbabilitySemiring | DoubleExtremumSemiring)
    if doubles and semiring.zero == _POSITIVE:
        excluded = _NEGATIVE
    # A subclass that overrides an operation is evaluated through that
    # operation, so only the base operations are fused.
    if (
        isinstance(semiring, LogProbabilitySemiring)
        and _base_operations(semiring, LogProbabilitySemiring)
        and witness is None
    ):
        fused = "log"
    elif isinstance(semiring, DoubleExtremumSemiring) and _base_operations(
        semiring, DoubleExtremumSemiring
    ):
        fusible = witness is None or (
            isinstance(witness, AlgebraOrder)
            and declaration.tie_policy is TiePolicy.CHOOSE_FIRST
        )
        if fusible:
            fused = "min" if semiring.zero == _POSITIVE else "max"
            select = witness is not None
    root_set = set(roots)
    inside_schedule = tuple(
        (item, 1, children[item][0])
        if len(children[item]) == 1
        else (item, 2, _gather(children[item]))
        if children[item]
        else (item, 0, None)
        for item in order
    )
    outside_schedule = tuple(
        (item, 1, parents[item][0], item in root_set)
        if len(parents[item]) == 1
        else (item, 2, _gather(parents[item]), item in root_set)
        if parents[item]
        else (item, 0, None, item in root_set)
        for item in reversed(order)
    )
    inside_additions = sum(max(len(links) - 1, 0) for links in children) + len(roots)
    outside_additions = sum(
        max(len(parents[item]) + (item in root_set) - 1, 0)
        for item in range(len(items))
    )
    return _Compiled(
        states=tuple((reference, ()) for reference in items),
        root_states=tuple((items[root], ()) for root in roots),
        document_size=len(declaration.graph.canonical_items()),
        incidence=sum(len(links) for links in children),
        inside_additions=inside_additions,
        inside_multiplications=len(items),
        outside_additions=outside_additions,
        outside_multiplications=sum(len(links) for links in parents) + len(items),
        fused=fused,
        select=select,
        doubles=doubles,
        excluded=excluded,
        positions={reference: position for position, reference in enumerate(items)},
        inside_schedule=inside_schedule,
        outside_schedule=outside_schedule,
    )


def _base_operations(
    semiring: Semiring[Any],
    base: type[LogProbabilitySemiring] | type[DoubleExtremumSemiring],
) -> bool:
    """Report whether the algebra's addition and multiplication are the base class's."""
    kind = type(semiring)
    return bool(
        getattr(kind, "add", None) is base.add
        and getattr(kind, "multiply", None) is base.multiply
    )


def _same_algebra(left: Semiring[Any], right: Semiring[Any]) -> bool:
    """Report whether two algebra instances are one algebra."""
    return left is right or (
        type(left) is type(right) and left.zero == right.zero and left.one == right.one
    )


__all__ = [
    "AlgebraOrder",
    "PathMarginals",
    "PathPlan",
    "PathPosteriors",
]
