"""Pool path mass by the complete string emitted along each path.

An emission is a tuple of strings attached to an item. A path emits their
concatenation in path order; an empty tuple is the identity. ``OutputPlan``
builds the reachable product of a ``PathPlan`` with a caller-supplied candidate
trie and an absorbing state for every other output. It can therefore compute a
finite candidate set and the residual in one pass, or condition the product on
one candidate and pool item marginals back onto the base plan.

The mass of a given string, a finite candidate set with its residual, and
conditioned item marginals are exact and tractable. Finding the pooled argmax
over all strings is the consensus-string problem and is NP-hard even for
acyclic hidden Markov models. Full determinization under the log-probability
semiring can grow exponentially. Caller-supplied candidates and the residual
certificate provide a bounded alternative.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import cast

from tiergraph.core import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    Graph,
    Item,
    ItemRef,
    NamespaceDeclaration,
    QualifiedName,
    RelationInstance,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    XsdType,
)
from tiergraph.fold import (
    AttributeValuation,
    ChildCombination,
    FoldCost,
    FoldDeclaration,
    FoldTransition,
)
from tiergraph.pathplan import PathPlan
from tiergraph.semiring import COUNTING, LOG_PROBABILITY, Semiring

_NAMESPACE = "urn:tiergraph:path-output"
_TIER = QualifiedName(_NAMESPACE, "items")
_TYPE = QualifiedName(_NAMESPACE, "item")
_MEMBERSHIP = QualifiedName(_NAMESPACE, "membership")
_NEXT = QualifiedName(_NAMESPACE, "next")
_SOURCE = QualifiedName(_NAMESPACE, "source")
_OFF = -1


@dataclass(frozen=True, slots=True)
class Emissions[Value]:
    """Per-item token tuples bound to one path plan's label inventory."""

    plan: PathPlan[Value]
    per_item: tuple[tuple[str, ...], ...]

    @classmethod
    def bind(
        cls, plan: PathPlan[Value], by_label: Mapping[str, Sequence[str]]
    ) -> Emissions[Value]:
        """Bind emissions by item label, leaving unlisted items non-emitting."""
        for label in by_label:
            if type(label) is not str:
                raise TypeError(
                    f"emission label {label!r} must be a string naming a plan item"
                )
        known = set(plan.labels)
        unknown = sorted(set(by_label) - known)
        if unknown:
            raise ValueError(
                f"emission label {unknown[0]!r} is not an item label in path plan "
                f"{plan.declaration.name!r}"
            )
        bound: list[tuple[str, ...]] = []
        for label in plan.labels:
            raw = by_label.get(label, ())
            if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
                raise TypeError(
                    f"emission for item label {label!r} must be a sequence of strings"
                )
            tokens = tuple(raw)
            for token in tokens:
                if type(token) is not str:
                    raise TypeError(
                        f"emission for item label {label!r} contains symbol "
                        f"{token!r}; every symbol must be a string"
                    )
            bound.append(tokens)
        return cls(plan, tuple(bound))


@dataclass(frozen=True, slots=True)
class OutputMasses[Value]:
    """Candidate and residual masses from one product-plan marginal pass."""

    total: Value
    per_candidate: tuple[Value, ...]
    residual: Value
    zero_mass: bool
    decided: bool
    tied: tuple[int, ...]
    cost: FoldCost


@dataclass(frozen=True, slots=True)
class OutputItemMarginals[Value]:
    """Conditioned marginals pooled into the base plan's item order."""

    total: Value
    zero_mass: bool
    values: tuple[Value, ...] | None
    cost: FoldCost


@dataclass(frozen=True, slots=True)
class OutputPlan[Value]:
    """A cached product plan for complete output candidates and their residual."""

    base: PathPlan[Value]
    emissions: Emissions[Value]
    candidates: tuple[tuple[str, ...], ...]
    plan: PathPlan[Value]
    base_index: Mapping[ItemRef, ItemRef]
    accepted: tuple[bool, ...]
    _product_base: tuple[int | None, ...] = field(repr=False, compare=False)
    _accept_indices: tuple[int, ...] = field(repr=False, compare=False)
    _residual_index: int = field(repr=False, compare=False)
    _conditioned_cache: dict[int, tuple[PathPlan[Value], tuple[int, ...]]] = field(
        default_factory=dict, repr=False, compare=False
    )

    @classmethod
    def prepare(  # noqa: PLR0915 -- construction validates one product contract
        cls,
        base: PathPlan[Value],
        emissions: Emissions[Value],
        candidates: Sequence[Sequence[str]],
    ) -> OutputPlan[Value]:
        """Build the reachable product with the candidates' trie and a residual."""
        if emissions.plan is not base:
            raise ValueError("emissions are bound to a different path plan")
        algebra = base.declaration.semiring
        if not algebra.multiply_commutative:
            raise ValueError(
                f"algebra {type(algebra).__name__!r} does not declare commutative "
                "multiplication required by output marginals"
            )
        parsed = tuple(
            _candidate(candidate, index) for index, candidate in enumerate(candidates)
        )
        if not parsed:
            raise ValueError("output plan candidates must not be empty")
        if len(set(parsed)) != len(parsed):
            repeated = next(
                candidate for candidate in parsed if parsed.count(candidate) > 1
            )
            raise ValueError(f"output plan candidate {repeated!r} is duplicated")
        if not base.roots:
            raise ValueError(
                f"path plan {base.declaration.name!r} has no root for an explicit "
                "product root"
            )

        transitions, terminals = _trie(parsed)

        def advance(state: int, tokens: tuple[str, ...]) -> int:
            """Advance a trie state through one item's complete emission."""
            if state == _OFF:
                return _OFF
            for token in tokens:
                state = transitions.get((state, token), _OFF)
                if state == _OFF:
                    break
            return state

        pairs: list[tuple[int, int]] = []
        positions: dict[tuple[int, int], int] = {}
        queue: deque[int] = deque()

        def seat(pair: tuple[int, int]) -> int:
            """Assign one stable product position and queue new pairs."""
            position = positions.get(pair)
            if position is None:
                position = len(pairs)
                positions[pair] = position
                pairs.append(pair)
                queue.append(position)
            return position

        roots = tuple(
            seat((root, advance(0, emissions.per_item[root]))) for root in base.roots
        )
        edges: list[tuple[int, int]] = []
        while queue:
            product_parent = queue.popleft()
            parent, state = pairs[product_parent]
            for child in base.children[parent]:
                product_child = seat((child, advance(state, emissions.per_item[child])))
                edges.append((product_parent, product_child))

        accepted = [False] * len(parsed)
        sink_targets: list[int] = []
        for base_item, state in pairs:
            if base.children[base_item]:
                sink_targets.append(-1)
                continue
            candidate_index = terminals.get(state)
            if candidate_index is not None:
                accepted[candidate_index] = True
                sink_targets.append(candidate_index)
            else:
                sink_targets.append(len(parsed))

        accept_local = tuple(len(pairs) + index for index in range(len(parsed)))
        residual_local = len(pairs) + len(parsed)
        for product_sink, target in enumerate(sink_targets):
            if target >= 0:
                child = (
                    residual_local if target == len(parsed) else accept_local[target]
                )
                edges.append((product_sink, child))

        product_base = tuple(base_item for base_item, _state in pairs) + (None,) * (
            len(parsed) + 1
        )
        product_plan = _prepare_derived(
            f"{base.declaration.name}:outputs",
            algebra,
            tuple(base.values[index] for index, _state in pairs)
            + (algebra.one,) * (len(parsed) + 1),
            edges,
            roots,
            carrier_operation_cost=base.declaration.carrier_operation_cost,
        )
        accept_indices = tuple(
            product_plan.index(ItemRef(_TIER, local)) for local in accept_local
        )
        residual_index = product_plan.index(ItemRef(_TIER, residual_local))
        mapping = {
            product_plan.items[product_index]: base.items[base_item]
            for product_index, base_item in enumerate(product_base)
            if base_item is not None
        }
        return cls(
            base,
            emissions,
            parsed,
            product_plan,
            mapping,
            tuple(accepted),
            product_base,
            accept_indices,
            residual_index,
        )

    def values(self, base_values: Sequence[Value] | None = None) -> tuple[Value, ...]:
        """Gather base values into the product plan's canonical item order."""
        vector = self.base.values if base_values is None else tuple(base_values)
        if len(vector) != len(self.base.items):
            raise ValueError(
                f"path plan {self.base.declaration.name!r} takes "
                f"{len(self.base.items)} values in plan item order and was given "
                f"{len(vector)}"
            )
        algebra = self.base.declaration.semiring
        return tuple(
            algebra.one if base_index is None else vector[base_index]
            for base_index in self._product_base
        )

    def masses(self, base_values: Sequence[Value] | None = None) -> OutputMasses[Value]:
        """Evaluate candidate and residual masses in one marginal pass."""
        algebra = self.base.declaration.semiring
        algebra_object = cast(object, algebra)
        if algebra_object is not LOG_PROBABILITY and algebra_object is not COUNTING:
            raise ValueError(
                f"algebra {type(algebra).__name__!r} has no output argmax "
                "certificate; only LOG_PROBABILITY and COUNTING are supported"
            )
        result = self.plan.marginals(self.values(base_values))
        per_candidate = tuple(result.marginals[index] for index in self._accept_indices)
        residual = result.marginals[self._residual_index]
        zero_mass = result.total == algebra.zero
        if zero_mass:
            decided = False
            tied: tuple[int, ...] = ()
        else:
            comparable = cast(tuple[float | int, ...], per_candidate)
            best = max(comparable)
            tied = tuple(
                index for index, value in enumerate(comparable) if value == best
            )
            decided = best >= cast(float | int, residual)
        return OutputMasses(
            result.total,
            per_candidate,
            residual,
            zero_mass,
            decided,
            tied,
            result.cost,
        )

    def conditioned(self, candidate: int) -> PathPlan[Value]:
        """Return the product restricted to paths accepting one candidate."""
        return self._conditioned(candidate)[0]

    def item_marginals(
        self, candidate: int, base_values: Sequence[Value] | None = None
    ) -> OutputItemMarginals[Value]:
        """Pool one candidate's conditioned product copies onto base items."""
        conditioned, product_indices = self._conditioned(candidate)
        product_values = self.values(base_values)
        result = conditioned.marginals(
            tuple(product_values[index] for index in product_indices)
        )
        algebra = self.base.declaration.semiring
        if result.total == algebra.zero:
            return OutputItemMarginals(result.total, True, None, result.cost)
        pooled = [algebra.zero for _item in self.base.items]
        for local_index, product_index in enumerate(product_indices):
            base_index = self._product_base[product_index]
            if base_index is not None:
                pooled[base_index] = algebra.add(
                    pooled[base_index], result.marginals[local_index]
                )
        return OutputItemMarginals(result.total, False, tuple(pooled), result.cost)

    def _conditioned(self, candidate: int) -> tuple[PathPlan[Value], tuple[int, ...]]:
        if type(candidate) is not int or not 0 <= candidate < len(self.candidates):
            raise ValueError(
                f"candidate index {candidate!r} is outside output plan candidates"
            )
        if not self.accepted[candidate]:
            raise ValueError(
                f"output candidate {candidate} {self.candidates[candidate]!r} has no "
                "structurally accepted path"
            )
        cached = self._conditioned_cache.get(candidate)
        if cached is not None:
            return cached
        target = self._accept_indices[candidate]
        keep = {target}
        queue = deque((target,))
        while queue:
            child = queue.popleft()
            for parent in self.plan.parents[child]:
                if parent not in keep:
                    keep.add(parent)
                    queue.append(parent)
        product_indices = tuple(
            index for index in range(len(self.plan.items)) if index in keep
        )
        local = {product: index for index, product in enumerate(product_indices)}
        edges = [
            (local[parent], local[child])
            for parent in product_indices
            for child in self.plan.children[parent]
            if child in keep
        ]
        roots = tuple(local[root] for root in self.plan.roots if root in keep)
        conditioned = _prepare_derived(
            f"{self.base.declaration.name}:output:{candidate}",
            self.base.declaration.semiring,
            tuple(self.plan.values[index] for index in product_indices),
            edges,
            roots,
            carrier_operation_cost=self.base.declaration.carrier_operation_cost,
        )
        cached = (conditioned, product_indices)
        self._conditioned_cache[candidate] = cached
        return cached


def _candidate(candidate: Sequence[str], index: int) -> tuple[str, ...]:
    if isinstance(candidate, (str, bytes)) or not isinstance(candidate, Sequence):
        raise TypeError(f"output candidate {index} must be a sequence of strings")
    parsed = tuple(candidate)
    for token in parsed:
        if type(token) is not str:
            raise TypeError(
                f"output candidate {index} contains symbol {token!r}; every symbol "
                "must be a string"
            )
    return parsed


def _trie(
    candidates: tuple[tuple[str, ...], ...],
) -> tuple[dict[tuple[int, str], int], dict[int, int]]:
    transitions: dict[tuple[int, str], int] = {}
    terminals: dict[int, int] = {}
    next_state = 1
    for candidate_index, candidate in enumerate(candidates):
        state = 0
        for token in candidate:
            key = (state, token)
            following = transitions.get(key)
            if following is None:
                following = next_state
                next_state += 1
                transitions[key] = following
            state = following
        terminals[state] = candidate_index
    return transitions, terminals


def _prepare_derived[Value](
    name: str,
    algebra: Semiring[Value],
    values: tuple[Value, ...],
    edges: Sequence[tuple[int, int]],
    roots: tuple[int, ...],
    *,
    carrier_operation_cost: int,
) -> PathPlan[Value]:
    if not roots:
        raise ValueError(f"derived path plan {name!r} requires explicit nonempty roots")
    items = tuple(
        Item(
            f"item-{index}",
            (AttributeValue(_SOURCE, XsdType.INTEGER, str(index)),),
        )
        for index in range(len(values))
    )
    graph = Graph(
        (NamespaceDeclaration("output", _NAMESPACE),),
        (Tier(TierDeclaration(_TIER, "Output product items"), items),),
        (
            SimpleRelationDeclaration(_MEMBERSHIP, _TIER, _TYPE),
            BipartiteRelationDeclaration(_NEXT, _TYPE, _TYPE, acyclic=True),
        ),
        tuple(
            RelationInstance(_NEXT, ItemRef(_TIER, parent), ItemRef(_TIER, child))
            for parent, child in edges
        ),
        (AttributeDeclaration(_SOURCE, AttributeDomain.ITEM, XsdType.INTEGER),),
    )

    def lift(source: object, _label: str) -> Value:
        """Read the carrier value stored outside the derived graph."""
        return values[int(cast(Decimal, source))]

    declaration = FoldDeclaration(
        name,
        graph,
        AttributeValuation("source", _SOURCE, (_TIER,)),
        algebra,
        lift,
        (FoldTransition(_NEXT, ChildCombination.OR),),
        roots=tuple(ItemRef(_TIER, root) for root in roots),
        carrier_operation_cost=carrier_operation_cost,
    )
    return PathPlan.prepare(declaration)


__all__ = ["Emissions", "OutputItemMarginals", "OutputMasses", "OutputPlan"]
