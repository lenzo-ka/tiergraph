"""Declared valuations and semiring folds over finite dependency DAGs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from functools import cmp_to_key
from itertools import product
from typing import Protocol, TypeVar

from tiergraph.core import (
    AttributeDomain,
    BipartiteRelationDeclaration,
    Graph,
    Item,
    ItemRef,
    QualifiedName,
    XsdType,
)
from tiergraph.semiring import LawCheck, Semiring

Value = TypeVar("Value")
OtherValue = TypeVar("OtherValue")
LiftValue = TypeVar("LiftValue", covariant=True)
ReadValue = TypeVar("ReadValue", contravariant=True)
Coordinate = tuple[str, ...]
State = tuple[ItemRef, Coordinate]
Path = tuple[str, ...]
Provenance = tuple[Path, ...]
type RankedWitness[Value] = tuple[Value, Path]


class Lift(Protocol[LiftValue]):
    """Embed one typed attribute value and its stable label in a carrier."""

    def __call__(self, value: object, label: str, /) -> LiftValue:
        """Return one local carrier value."""


class WitnessOrder(Protocol[ReadValue]):
    """Compare carrier values for witness selection without enriching them."""

    def __call__(self, left: ReadValue, right: ReadValue, /) -> int:
        """Return negative for left, positive for right, or zero for a tie."""


class ChildCombination(Enum):
    """Declare whether one relation's incident children are alternatives or requirements."""

    OR = "or"
    AND = "and"


@dataclass(frozen=True, slots=True)
class FoldTransition:
    """Give one dependency relation its local AND/OR incidence meaning."""

    relation: QualifiedName
    combination: ChildCombination


class TiePolicy(Enum):
    """Supported, executable policies for equal-valued alternatives."""

    ALL = "all"
    CHOOSE_FIRST = "choose-first"


@dataclass(frozen=True, slots=True)
class AttributeValuation:
    """Read one declared item attribute over an explicit tier domain."""

    name: str
    attribute: QualifiedName
    tiers: tuple[QualifiedName, ...]

    def __post_init__(self) -> None:
        """Require names and a nonempty duplicate-free domain."""
        if not self.name:
            raise ValueError("valuation name '' must not be empty")
        if not self.tiers:
            raise ValueError(f"valuation {self.name!r} has an empty tier domain")
        if len(set(self.tiers)) != len(self.tiers):
            raise ValueError(f"valuation {self.name!r} has duplicate tier names")

    def declaration_type(self, graph: Graph) -> XsdType:
        """Return the declared XSD type, refusing the wrong domain or a missing name."""
        declaration = next(
            (
                candidate
                for candidate in graph.attribute_declarations
                if candidate.name == self.attribute
            ),
            None,
        )
        if declaration is None:
            raise ValueError(
                f"valuation {self.name!r} names undeclared attribute {str(self.attribute)!r}"
            )
        if declaration.domain is not AttributeDomain.ITEM:
            raise ValueError(
                f"valuation {self.name!r} attribute {str(self.attribute)!r} has domain "
                f"{declaration.domain.value!r}, not 'item'"
            )
        return declaration.value_type

    def read(self, graph: Graph, reference: ItemRef) -> object:
        """Decode the selected item's canonical lexical value by its XSD type."""
        if reference.tier not in self.tiers:
            raise ValueError(
                f"valuation {self.name!r} excludes tier {str(reference.tier)!r}"
            )
        item = _item(graph, reference)
        attribute = next(
            (
                candidate
                for candidate in item.attributes
                if candidate.name == self.attribute
            ),
            None,
        )
        if attribute is None:
            raise ValueError(
                f"valuation {self.name!r} item {reference.to_data()!r} lacks "
                f"attribute {str(self.attribute)!r}"
            )
        if attribute.value_type in {XsdType.INTEGER, XsdType.DECIMAL}:
            return Decimal(attribute.lexical)
        if attribute.value_type is XsdType.DOUBLE:
            return float(attribute.lexical.replace("INF", "inf"))
        if attribute.value_type is XsdType.BOOLEAN:
            return attribute.lexical == "true"
        return attribute.lexical


@dataclass(frozen=True, slots=True)
class FoldCost:
    """Report measured structural quantities and carrier work for one run."""

    document_size: int
    relation_incidence: int
    index_product_size: int
    carrier_additions: int
    carrier_multiplications: int
    carrier_operation_cost: int
    witness_count: int
    emitted_count: int
    output_cap: int
    witness_operations: int = 0
    ranked_multiplications: int = 0

    @property
    def bound(self) -> int:
        """Return the declared structural/carrier/output work bound."""
        structural = (
            self.document_size + self.relation_incidence
        ) * self.index_product_size
        base = structural * self.carrier_operation_cost
        ranked = (
            (self.document_size + self.relation_incidence) ** 2
            * self.index_product_size
            * self.output_cap**4
            * self.carrier_operation_cost
            if self.ranked_multiplications or self.witness_operations
            else 0
        )
        return base + ranked + min(self.witness_count, self.output_cap)

    @property
    def measured_work(self) -> int:
        """Return measured traversal work plus actually emitted output."""
        structural = (
            self.document_size + self.relation_incidence
        ) * self.index_product_size
        base = structural * self.carrier_operation_cost
        ranked = (
            self.ranked_multiplications + self.witness_operations
        ) * self.carrier_operation_cost
        return base + ranked + self.emitted_count

    @property
    def carrier_work(self) -> int:
        """Return measured semiring-operation work at the declared unit cost."""
        return (
            self.carrier_additions
            + self.carrier_multiplications
            + self.witness_operations
        ) * self.carrier_operation_cost

    def to_data(self) -> dict[str, int]:
        """Return a strict-JSON cost account."""
        data = {
            "document_size": self.document_size,
            "relation_incidence": self.relation_incidence,
            "index_product_size": self.index_product_size,
            "carrier_additions": self.carrier_additions,
            "carrier_multiplications": self.carrier_multiplications,
            "carrier_operation_cost": self.carrier_operation_cost,
            "carrier_work": self.carrier_work,
            "witness_count": self.witness_count,
            "emitted_count": self.emitted_count,
            "output_cap": self.output_cap,
            "bound": self.bound,
            "measured_work": self.measured_work,
        }
        if self.witness_operations or self.ranked_multiplications:
            data["witness_operations"] = self.witness_operations
            data["ranked_multiplications"] = self.ranked_multiplications
        return data


@dataclass(frozen=True, slots=True)
class FoldResult[Value]:
    """Keep semiring values, witness provenance, and measured work separate."""

    values: tuple[tuple[State, Value], ...]
    roots: tuple[State, ...]
    value: Value
    provenance: Provenance | None
    truncated: bool
    cost: FoldCost
    ranked_witnesses: tuple[RankedWitness[Value], ...] | None = None

    def to_data(self, semiring: Semiring[Value]) -> dict[str, object]:
        """Return deterministic strict-JSON data."""
        data: dict[str, object] = {
            "value": semiring.encode(self.value),
            "provenance": (
                None
                if self.provenance is None
                else [list(path) for path in self.provenance]
            ),
            "truncated": self.truncated,
            "roots": [_state_data(state) for state in self.roots],
            "states": [
                {"state": _state_data(state), "value": semiring.encode(value)}
                for state, value in self.values
            ],
            "cost": self.cost.to_data(),
        }
        if self.ranked_witnesses is not None:
            data["ranked_witnesses"] = [
                {"value": semiring.encode(value), "path": list(path)}
                for value, path in self.ranked_witnesses
            ]
        return data


@dataclass(frozen=True, slots=True)
class FoldDeclaration[Value]:
    """Bind one named interpretation to a graph, valuation, algebra, and finite DAG.

    With ``ranked_output`` the fold also returns up to ``output_cap`` witnesses ranked
    by the semiring's own order, which its multiplication must preserve
    (``multiply_preserves_witness_order``); a custom ``witness_order`` is refused. Among
    witnesses of equal carrier value the ranked selection is deterministic but not
    guaranteed to be a globally canonical one.
    """

    name: str
    graph: Graph
    valuation: AttributeValuation
    semiring: Semiring[Value]
    lift: Lift[Value]
    transitions: tuple[FoldTransition, ...]
    index_axes: tuple[tuple[str, ...], ...] = ()
    roots: tuple[ItemRef, ...] = ()
    witness_order: WitnessOrder[Value] | None = None
    tie_policy: TiePolicy | None = None
    output_cap: int = 1
    carrier_operation_cost: int = 1
    ranked_output: bool = False

    def __post_init__(self) -> None:
        """Validate every declaration-level refusal before a fold can run."""
        if not self.name:
            raise ValueError("fold name '' must not be empty")
        if self.output_cap < 1:
            raise ValueError(
                f"fold {self.name!r} output cap {self.output_cap!r} must be positive"
            )
        if self.carrier_operation_cost < 1:
            raise ValueError(
                f"fold {self.name!r} carrier operation cost "
                f"{self.carrier_operation_cost!r} must be positive"
            )
        if self.witness_order is not None and self.tie_policy is None:
            raise ValueError(
                f"fold {self.name!r} produces witnesses but has no tie policy"
            )
        if self.ranked_output and self.tie_policy is None:
            raise ValueError(
                f"fold {self.name!r} produces ranked witnesses but has no tie policy"
            )
        if self.ranked_output and self.witness_order is not None:
            raise ValueError(
                f"fold {self.name!r} ranked output uses the semiring's canonical "
                "order and conflicts with a custom witness_order"
            )
        if self.ranked_output and not getattr(
            self.semiring, "multiply_preserves_witness_order", False
        ):
            raise ValueError(
                f"fold {self.name!r} semiring {type(self.semiring).__name__!r} "
                "does not declare multiply_preserves_witness_order"
            )
        if (
            self.witness_order is None
            and not self.ranked_output
            and self.tie_policy is not None
        ):
            raise ValueError(
                f"fold {self.name!r} declares tie policy {self.tie_policy!r} "
                "but produces no witnesses"
            )
        if self.tie_policy is not None and not isinstance(self.tie_policy, TiePolicy):
            raise ValueError(
                f"fold {self.name!r} has unsupported tie policy {self.tie_policy!r}"
            )
        if any(not isinstance(item, FoldTransition) for item in self.transitions):
            raise ValueError(
                f"fold {self.name!r} transitions must declare AND/OR meaning"
            )
        declared_tiers = {tier.declaration.name for tier in self.graph.tiers}
        for tier in self.valuation.tiers:
            if tier not in declared_tiers:
                raise ValueError(
                    f"fold {self.name!r} domain names undeclared tier {str(tier)!r}"
                )
        value_type = self.valuation.declaration_type(self.graph)
        exact_laws = (
            self.semiring.add_associativity is LawCheck.EXACT
            and self.semiring.multiply_associativity is LawCheck.EXACT
        )
        if value_type is XsdType.DOUBLE and exact_laws:
            raise ValueError(
                f"fold {self.name!r} valuation {self.valuation.name!r} reads "
                f"xsd:double attribute {str(self.valuation.attribute)!r}, but semiring "
                f"{type(self.semiring).__name__!r} claims exact associativity"
            )
        for axis_index, axis in enumerate(self.index_axes):
            if not axis:
                raise ValueError(
                    f"fold {self.name!r} index axis {axis_index!r} is empty"
                )
            if len(set(axis)) != len(axis):
                raise ValueError(
                    f"fold {self.name!r} index axis {axis_index!r} has duplicates"
                )
        declarations = {
            declaration.name: declaration
            for declaration in self.graph.relation_declarations
            if isinstance(declaration, BipartiteRelationDeclaration)
        }
        if not self.transitions:
            raise ValueError(f"fold {self.name!r} has no declared dependency relations")
        relation_names = [transition.relation for transition in self.transitions]
        if len(set(relation_names)) != len(relation_names):
            raise ValueError(f"fold {self.name!r} has duplicate dependency relations")
        for transition in self.transitions:
            declaration = declarations.get(transition.relation)
            if declaration is None:
                raise ValueError(
                    f"fold {self.name!r} names undeclared bipartite relation "
                    f"{str(transition.relation)!r}"
                )
            if not declaration.acyclic:
                raise ValueError(
                    f"fold {self.name!r} relation {str(transition.relation)!r} does not declare acyclic"
                )
        admitted = set(self._references())
        for root in self.roots:
            if root not in admitted:
                raise ValueError(
                    f"fold {self.name!r} root {root.to_data()!r} is outside its domain"
                )
        self._topology()

    def _references(self) -> tuple[ItemRef, ...]:
        """Return domain items in the graph's canonical order."""
        tiers = set(self.valuation.tiers)
        return tuple(
            reference
            for reference in self.graph.canonical_items()
            if reference.tier in tiers
        )

    def coordinates(self) -> tuple[Coordinate, ...]:
        """Construct the declared finite index product in lexical axis order."""
        if not self.index_axes:
            return ((),)
        return tuple(product(*self.index_axes))

    def states(self) -> tuple[State, ...]:
        """Construct the finite domain-item by index-product state space."""
        return tuple(
            (reference, coordinate)
            for coordinate in self.coordinates()
            for reference in self._references()
        )

    def _topology(
        self,
    ) -> tuple[
        dict[ItemRef, dict[QualifiedName, tuple[ItemRef, ...]]], tuple[ItemRef, ...]
    ]:
        """Return canonical outgoing incidence and inferred or declared roots."""
        references = self._references()
        admitted = set(references)
        selected = {transition.relation for transition in self.transitions}
        outgoing_lists: dict[ItemRef, dict[QualifiedName, list[ItemRef]]] = {
            reference: {relation: [] for relation in selected}
            for reference in references
        }
        incoming = {reference: 0 for reference in references}
        for relation in self.graph.relations:
            if (
                relation.declaration in selected
                and isinstance(relation.left, ItemRef)
                and isinstance(relation.right, ItemRef)
                and relation.left in admitted
                and relation.right in admitted
            ):
                outgoing_lists[relation.left][relation.declaration].append(
                    relation.right
                )
                incoming[relation.right] += 1
        order = {reference: index for index, reference in enumerate(references)}
        outgoing = {
            reference: {
                relation: tuple(sorted(children, key=order.__getitem__))
                for relation, children in by_relation.items()
            }
            for reference, by_relation in outgoing_lists.items()
        }
        roots = self.roots or tuple(
            reference for reference in references if incoming[reference] == 0
        )
        if not roots:
            raise ValueError(f"fold {self.name!r} dependency DAG has no root")
        return outgoing, roots

    def run(self) -> FoldResult[Value]:
        """Evaluate every state using only the semiring's addition and multiplication."""
        outgoing, item_roots = self._topology()
        coordinates = self.coordinates()
        additions = 0
        multiplications = 0
        ranked_multiplications = 0
        ranked_additions = [0]
        witness_operations = [0]
        root_witness_count = 0
        all_values: list[tuple[State, Value]] = []
        root_states: list[State] = []
        total = self.semiring.zero
        selected: tuple[Value, Provenance] | None = None
        ranked_roots: list[RankedWitness[Value]] = []
        for coordinate in coordinates:
            cache: dict[
                ItemRef,
                tuple[Value, Provenance, tuple[RankedWitness[Value], ...], int],
            ] = {}

            def visit(
                reference: ItemRef,
                state_cache: dict[
                    ItemRef,
                    tuple[Value, Provenance, tuple[RankedWitness[Value], ...], int],
                ] = cache,
            ) -> tuple[Value, Provenance, tuple[RankedWitness[Value], ...], int]:
                """Evaluate one state once for the current index coordinate."""
                nonlocal additions, multiplications, ranked_multiplications
                prepared: dict[ItemRef, tuple[Value, str]] = {}
                work: list[tuple[ItemRef, bool]] = [(reference, False)]
                while work:
                    current, finish = work.pop()
                    if current in state_cache:
                        continue
                    if not finish:
                        item = _item(self.graph, current)
                        label = item.durable_id or _structural_label(current)
                        local = self.lift(
                            self.valuation.read(self.graph, current), label
                        )
                        prepared[current] = (local, label)
                        work.append((current, True))
                        pending_children = (
                            child
                            for transition in reversed(self.transitions)
                            for child in reversed(
                                outgoing[current][transition.relation]
                            )
                        )
                        work.extend(
                            (child, False)
                            for child in pending_children
                            if child not in state_cache
                        )
                        continue

                    local, label = prepared.pop(current)
                    value = local
                    paths: Provenance = ((label,),)
                    ranked: tuple[RankedWitness[Value], ...] = (
                        ()
                        if not self.ranked_output or local == self.semiring.zero
                        else ((local, (label,)),)
                    )
                    ranked_count = len(ranked)
                    has_children = False
                    for transition in self.transitions:
                        children = outgoing[current][transition.relation]
                        if not children:
                            continue
                        has_children = True
                        child_results = [state_cache[child] for child in children]
                        if transition.combination is ChildCombination.AND:
                            relation_value = self.semiring.one
                            relation_paths: Provenance = ((),)
                            relation_ranked: tuple[RankedWitness[Value], ...] = (
                                (self.semiring.one, ()),
                            )
                            relation_count = 1
                            for (
                                child_value,
                                child_paths,
                                child_ranked,
                                child_count,
                            ) in child_results:
                                relation_value = self.semiring.multiply(
                                    relation_value, child_value
                                )
                                multiplications += 1
                                relation_paths = tuple(
                                    left + right
                                    for left in relation_paths
                                    for right in child_paths
                                )
                                relation_count *= child_count
                                if self.ranked_output:
                                    ranked_products = len(relation_ranked) * len(
                                        child_ranked
                                    )
                                    multiplications += ranked_products
                                    ranked_multiplications += ranked_products
                                    relation_ranked = self._rank_candidates(
                                        tuple(
                                            (
                                                self.semiring.multiply(
                                                    left_value, right_value
                                                ),
                                                left_path + right_path,
                                            )
                                            for left_value, left_path in relation_ranked
                                            for right_value, right_path in child_ranked
                                        ),
                                        witness_operations,
                                        ranked_additions,
                                    )
                        else:
                            relation_value = child_results[0][0]
                            # The value accumulates, because that is what OR means,
                            # while selection tracks the best sibling separately.
                            # Comparing against the accumulation instead would let
                            # a non-selective semiring outgrow every later sibling.
                            best = child_results[0][:2]
                            for (
                                child_value,
                                child_paths,
                                _child_ranked,
                                _child_count,
                            ) in child_results[1:]:
                                relation_value = self.semiring.add(
                                    relation_value, child_value
                                )
                                additions += 1
                                best = self._select_paths(
                                    best, (child_value, child_paths)
                                )
                            relation_paths = best[1]
                            relation_count = sum(result[3] for result in child_results)
                            if self.ranked_output:
                                relation_ranked = self._rank_candidates(
                                    tuple(
                                        candidate
                                        for _child_value, _child_paths, child_ranked, _child_count in child_results
                                        for candidate in child_ranked
                                    ),
                                    witness_operations,
                                    ranked_additions,
                                )
                        value = self.semiring.multiply(value, relation_value)
                        multiplications += 1
                        paths = tuple(
                            left + right for left in paths for right in relation_paths
                        )
                        ranked_count *= relation_count
                        if self.ranked_output:
                            ranked_products = len(ranked) * len(relation_ranked)
                            multiplications += ranked_products
                            ranked_multiplications += ranked_products
                            ranked = self._rank_candidates(
                                tuple(
                                    (
                                        self.semiring.multiply(left_value, right_value),
                                        left_path + right_path,
                                    )
                                    for left_value, left_path in ranked
                                    for right_value, right_path in relation_ranked
                                ),
                                witness_operations,
                                ranked_additions,
                            )
                    if not has_children:
                        value = self.semiring.multiply(value, self.semiring.one)
                        multiplications += 1
                    state_cache[current] = (value, paths, ranked, ranked_count)
                return state_cache[reference]

            for root in item_roots:
                state = (root, coordinate)
                root_states.append(state)
                root_value, _root_paths, root_ranked, root_count = visit(root)
                total = self.semiring.add(total, root_value)
                additions += 1
                if self.ranked_output:
                    ranked_roots.extend(root_ranked)
                    root_witness_count += root_count
            for reference in self._references():
                visit(reference)
            all_values.extend(
                ((reference, coordinate), cache[reference][0])
                for reference in self._references()
            )
            if self.witness_order is not None:
                # Selection runs inside the coordinate loop so that provenance
                # folds over the same domain as the value. Reading it afterwards
                # would see only the last coordinate's cache.
                for root in item_roots:
                    candidate = cache[root][:2]
                    if selected is None:
                        selected = candidate
                    else:
                        selected = self._select_paths(selected, candidate)
        complete = None if selected is None else selected[1]
        provenance = None if complete is None else complete[: self.output_cap]
        ranked_witnesses = (
            None
            if not self.ranked_output
            else self._rank_candidates(
                tuple(ranked_roots), witness_operations, ranked_additions
            )
        )
        additions += ranked_additions[0]
        witness_count = (
            root_witness_count
            if ranked_witnesses is not None
            else 0
            if complete is None
            else len(complete)
        )
        cost = FoldCost(
            document_size=len(self.graph.canonical_items()),
            relation_incidence=sum(
                len(children)
                for by_relation in outgoing.values()
                for children in by_relation.values()
            ),
            index_product_size=len(coordinates),
            carrier_additions=additions,
            carrier_multiplications=multiplications,
            carrier_operation_cost=self.carrier_operation_cost,
            witness_count=witness_count,
            emitted_count=(
                len(ranked_witnesses)
                if ranked_witnesses is not None
                else 0
                if provenance is None
                else len(provenance)
            ),
            output_cap=self.output_cap,
            witness_operations=witness_operations[0],
            ranked_multiplications=ranked_multiplications,
        )
        return FoldResult(
            values=tuple(all_values),
            roots=tuple(root_states),
            value=total,
            provenance=provenance,
            truncated=(
                root_witness_count > len(ranked_witnesses)
                if ranked_witnesses is not None
                else witness_count > self.output_cap
            ),
            cost=cost,
            ranked_witnesses=ranked_witnesses,
        )

    def _rank_candidates(
        self,
        candidates: tuple[RankedWitness[Value], ...],
        witness_operations: list[int],
        ranked_additions: list[int],
    ) -> tuple[RankedWitness[Value], ...]:
        """Return distinct witnesses in declared value and canonical path order."""

        def compare(left: RankedWitness[Value], right: RankedWitness[Value]) -> int:
            """Compare carrier values before deterministic structural paths."""
            witness_operations[0] += 1
            if left[0] != right[0]:
                ranked_additions[0] += 1
                preferred = self.semiring.add(left[0], right[0])
                if preferred == left[0]:
                    return -1
                if preferred == right[0]:
                    return 1
            return (left[1] > right[1]) - (left[1] < right[1])

        distinct: list[RankedWitness[Value]] = []
        for candidate in sorted(candidates, key=cmp_to_key(compare)):
            duplicate = False
            for existing in distinct:
                witness_operations[0] += 1
                if candidate == existing:
                    duplicate = True
                    break
            if not duplicate:
                distinct.append(candidate)
        return tuple(distinct[: self.output_cap])

    def _select_paths(
        self,
        left: tuple[Value, Provenance],
        right: tuple[Value, Provenance],
    ) -> tuple[Value, Provenance]:
        """Apply the declared witness ordering and executable tie policy.

        Both the surviving value and its paths are returned together, so a
        caller cannot substitute an accumulated carrier value for the value
        that actually won. Comparing a candidate against a running total is
        only harmless when addition is selective: under a counting semiring
        the total grows past every alternative and later equals are never
        recognized as ties.
        """
        left_value, left_paths = left
        right_value, right_paths = right
        if self.witness_order is None:
            return left_value, ()
        comparison = self.witness_order(left_value, right_value)
        if comparison < 0:
            return left
        if comparison > 0:
            return right
        if self.tie_policy is TiePolicy.CHOOSE_FIRST:
            return left
        return left_value, tuple(dict.fromkeys((*left_paths, *right_paths)))


@dataclass(frozen=True, slots=True)
class FoldHomomorphism[Value, OtherValue]:
    """Declare a carrier map whose fold result must commute."""

    name: str
    source: FoldDeclaration[Value]
    target: FoldDeclaration[OtherValue]
    mapping: Callable[[Value], OtherValue]

    def __post_init__(self) -> None:
        """Require matching structural interpretations at declaration time."""
        if not self.name:
            raise ValueError("homomorphism name '' must not be empty")
        if (
            self.source.graph != self.target.graph
            or self.source.valuation != self.target.valuation
            or self.source.transitions != self.target.transitions
            or self.source.index_axes != self.target.index_axes
            or self.source.roots != self.target.roots
        ):
            raise ValueError(
                f"homomorphism {self.name!r} source and target structures differ"
            )
        self.check()

    def commutes(self) -> bool:
        """Execute both folds and compare the mapped source with the target."""
        return self.mapping(self.source.run().value) == self.target.run().value

    def check(self) -> None:
        """Refuse a declared homomorphism whose square does not commute."""
        if not self.commutes():
            raise ValueError(f"homomorphism {self.name!r} does not commute with fold")


def _item(graph: Graph, reference: ItemRef) -> Item:
    return next(
        tier.items[reference.index]
        for tier in graph.tiers
        if tier.declaration.name == reference.tier
    )


def _structural_label(reference: ItemRef) -> str:
    return f"{reference.tier.namespace}:{reference.tier.local_name}:{reference.index}"


def _state_data(state: State) -> dict[str, object]:
    return {"item": state[0].to_data(), "coordinate": list(state[1])}


__all__ = [
    "AttributeValuation",
    "ChildCombination",
    "Coordinate",
    "FoldCost",
    "FoldDeclaration",
    "FoldHomomorphism",
    "FoldResult",
    "FoldTransition",
    "Lift",
    "Path",
    "Provenance",
    "TiePolicy",
    "WitnessOrder",
    "State",
]
