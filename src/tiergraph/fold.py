"""Declared valuations and semiring folds over finite dependency DAGs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
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


class ProvenanceReader(Protocol[ReadValue]):
    """Recover paths from an enriched carrier without changing its value."""

    def __call__(self, value: ReadValue, /) -> Provenance:
        """Return all paths selected by the carrier."""


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
    relations: tuple[QualifiedName, ...]
    index_axes: tuple[tuple[str, ...], ...] = ()
    roots: tuple[ItemRef, ...] = ()
    provenance_reader: ProvenanceReader[Value] | None = None
    tie_policy: str | None = None
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
        if self.provenance_reader is not None and not self.tie_policy:
            raise ValueError(
                f"fold {self.name!r} produces witnesses but has no tie policy"
            )
        if self.provenance_reader is None and self.tie_policy is not None:
            raise ValueError(
                f"fold {self.name!r} declares tie policy {self.tie_policy!r} "
                "but produces no witnesses"
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
        if not self.relations:
            raise ValueError(f"fold {self.name!r} has no declared dependency relations")
        for relation in self.relations:
            declaration = declarations.get(relation)
            if declaration is None:
                raise ValueError(
                    f"fold {self.name!r} names undeclared bipartite relation "
                    f"{str(relation)!r}"
                )
            if not declaration.acyclic:
                raise ValueError(
                    f"fold {self.name!r} relation {str(relation)!r} does not declare acyclic"
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
    ) -> tuple[dict[ItemRef, tuple[ItemRef, ...]], tuple[ItemRef, ...]]:
        """Return canonical outgoing incidence and inferred or declared roots."""
        references = self._references()
        admitted = set(references)
        selected = set(self.relations)
        outgoing_lists: dict[ItemRef, list[ItemRef]] = {
            reference: [] for reference in references
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
                outgoing_lists[relation.left].append(relation.right)
                incoming[relation.right] += 1
        order = {reference: index for index, reference in enumerate(references)}
        outgoing = {
            reference: tuple(sorted(children, key=order.__getitem__))
            for reference, children in outgoing_lists.items()
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
        for coordinate in coordinates:
            cache: dict[ItemRef, Value] = {}

            def visit(
                reference: ItemRef, state_cache: dict[ItemRef, Value] = cache
            ) -> Value:
                """Evaluate one state once for the current index coordinate."""
                nonlocal additions, multiplications
                if reference in state_cache:
                    return state_cache[reference]
                item = _item(self.graph, reference)
                label = item.durable_id or _structural_label(reference)
                local = self.lift(self.valuation.read(self.graph, reference), label)
                alternatives = self.semiring.zero
                children = outgoing[reference]
                if children:
                    for child in children:
                        alternatives = self.semiring.add(alternatives, visit(child))
                        additions += 1
                else:
                    alternatives = self.semiring.one
                result = self.semiring.multiply(local, alternatives)
                multiplications += 1
                state_cache[reference] = result
                return result

            for root in item_roots:
                state = (root, coordinate)
                root_states.append(state)
                total = self.semiring.add(total, visit(root))
                additions += 1
            for reference in self._references():
                visit(reference)
            all_values.extend(
                ((reference, coordinate), cache[reference])
                for reference in self._references()
            )
        complete = (
            None if self.provenance_reader is None else self.provenance_reader(total)
        )
        provenance = None if complete is None else complete[: self.output_cap]
        witness_count = 0 if complete is None else len(complete)
        cost = FoldCost(
            document_size=len(self.graph.canonical_items()),
            relation_incidence=sum(len(children) for children in outgoing.values()),
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
            or self.source.relations != self.target.relations
            or self.source.index_axes != self.target.index_axes
            or self.source.roots != self.target.roots
        ):
            raise ValueError(
                f"homomorphism {self.name!r} source and target structures differ"
            )

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
    "Coordinate",
    "FoldCost",
    "FoldDeclaration",
    "FoldHomomorphism",
    "FoldResult",
    "Lift",
    "Path",
    "Provenance",
    "ProvenanceReader",
    "State",
]
