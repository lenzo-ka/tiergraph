"""Declared valuations and semiring folds over finite dependency DAGs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
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

    @property
    def bound(self) -> int:
        """Return the declared structural/carrier/output work bound."""
        return (
            self.document_size + self.relation_incidence
        ) * self.index_product_size * self.carrier_operation_cost + min(
            self.witness_count, self.output_cap
        )

    @property
    def measured_work(self) -> int:
        """Return measured traversal work plus actually emitted output."""
        return (
            self.document_size + self.relation_incidence
        ) * self.index_product_size * self.carrier_operation_cost + self.emitted_count

    @property
    def carrier_work(self) -> int:
        """Return measured semiring-operation work at the declared unit cost."""
        return (
            self.carrier_additions + self.carrier_multiplications
        ) * self.carrier_operation_cost

    def to_data(self) -> dict[str, int]:
        """Return a strict-JSON cost account."""
        return {
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


@dataclass(frozen=True, slots=True)
class FoldResult[Value]:
    """Keep semiring values, witness provenance, and measured work separate."""

    values: tuple[tuple[State, Value], ...]
    roots: tuple[State, ...]
    value: Value
    provenance: Provenance | None
    truncated: bool
    cost: FoldCost

    def to_data(self, semiring: Semiring[Value]) -> dict[str, object]:
        """Return deterministic strict-JSON data."""
        return {
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


@dataclass(frozen=True, slots=True)
class FoldDeclaration[Value]:
    """Bind one named interpretation to a graph, valuation, algebra, and finite DAG."""

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
        if self.witness_order is None and self.tie_policy is not None:
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
        all_values: list[tuple[State, Value]] = []
        root_states: list[State] = []
        total = self.semiring.zero
        selected: tuple[Value, Provenance] | None = None
        for coordinate in coordinates:
            cache: dict[ItemRef, tuple[Value, Provenance]] = {}

            def visit(
                reference: ItemRef,
                state_cache: dict[ItemRef, tuple[Value, Provenance]] = cache,
            ) -> tuple[Value, Provenance]:
                """Evaluate one state once for the current index coordinate."""
                nonlocal additions, multiplications
                if reference in state_cache:
                    return state_cache[reference]
                item = _item(self.graph, reference)
                label = item.durable_id or _structural_label(reference)
                local = self.lift(self.valuation.read(self.graph, reference), label)
                value = local
                paths: Provenance = ((label,),)
                has_children = False
                for transition in self.transitions:
                    children = outgoing[reference][transition.relation]
                    if not children:
                        continue
                    has_children = True
                    child_results = [visit(child) for child in children]
                    if transition.combination is ChildCombination.AND:
                        relation_value = self.semiring.one
                        relation_paths: Provenance = ((),)
                        for child_value, child_paths in child_results:
                            relation_value = self.semiring.multiply(
                                relation_value, child_value
                            )
                            multiplications += 1
                            relation_paths = tuple(
                                left + right
                                for left in relation_paths
                                for right in child_paths
                            )
                    else:
                        relation_value = child_results[0][0]
                        # The value accumulates, because that is what OR means,
                        # while selection tracks the best sibling separately.
                        # Comparing against the accumulation instead would let
                        # a non-selective semiring outgrow every later sibling.
                        best = child_results[0]
                        for child_value, child_paths in child_results[1:]:
                            relation_value = self.semiring.add(
                                relation_value, child_value
                            )
                            additions += 1
                            best = self._select_paths(best, (child_value, child_paths))
                        relation_paths = best[1]
                    value = self.semiring.multiply(value, relation_value)
                    multiplications += 1
                    paths = tuple(
                        left + right for left in paths for right in relation_paths
                    )
                if not has_children:
                    value = self.semiring.multiply(value, self.semiring.one)
                    multiplications += 1
                state_cache[reference] = (value, paths)
                return value, paths

            for root in item_roots:
                state = (root, coordinate)
                root_states.append(state)
                root_value, _root_paths = visit(root)
                total = self.semiring.add(total, root_value)
                additions += 1
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
                    candidate = cache[root]
                    if selected is None:
                        selected = candidate
                    else:
                        selected = self._select_paths(selected, candidate)
        complete = None if selected is None else selected[1]
        provenance = None if complete is None else complete[: self.output_cap]
        witness_count = 0 if complete is None else len(complete)
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
            emitted_count=0 if provenance is None else len(provenance),
            output_cap=self.output_cap,
        )
        return FoldResult(
            tuple(all_values),
            tuple(root_states),
            total,
            provenance,
            witness_count > self.output_cap,
            cost,
        )

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
