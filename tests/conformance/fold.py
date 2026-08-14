"""Executable conformance fixture for the recognize and action fold halves."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, TypeVar

from tiergraph import (
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
from tiergraph.semiring import Semiring

Value = TypeVar("Value")
Carrier = TypeVar("Carrier", contravariant=True)
ActionResult = TypeVar("ActionResult", covariant=True)
State = tuple[ItemRef, str]
Provenance = tuple[tuple[str, ...], ...]


class Valuation(Protocol):
    """Map a graph item to a decimal field value without choosing an algebra."""

    def __call__(self, graph: Graph, reference: ItemRef, /) -> Decimal:
        """Read one declared item attribute."""


class Action(Protocol[Carrier, ActionResult]):
    """Apply recognized coordinates to an otherwise opaque carrier."""

    def __call__(
        self, carrier: Carrier, coordinates: tuple[int, ...], /
    ) -> ActionResult:
        """Return the carrier-specific result."""


@dataclass(frozen=True)
class Recognition[Value]:
    """Keep a state value and reconstruction provenance as separate products."""

    value: Value
    provenance: Provenance | None
    truncated: bool

    def to_data(self, semiring: Semiring[Value]) -> dict[str, object]:
        """Return strict-JSON data without exposing the carrier representation."""
        return {
            "value": semiring.encode(self.value),
            "provenance": (
                None
                if self.provenance is None
                else [list(path) for path in self.provenance]
            ),
            "truncated": self.truncated,
        }


@dataclass(frozen=True)
class ComplexityAccount:
    """State the fold bound using the quantities required by its contract."""

    document_size: int
    relation_incidence: int
    index_product_size: int
    carrier_operation_cost: int
    witness_count: int
    output_cap: int
    action_cost: int

    def bound(self) -> int:
        """Bound recognition plus capped action work."""
        recognition = (
            (self.document_size + self.relation_incidence)
            * self.index_product_size
            * self.carrier_operation_cost
        )
        action = min(self.witness_count, self.output_cap) * self.action_cost
        return recognition + action


@dataclass(frozen=True)
class FoldFixture:
    """Build and interpret the page-sized mix dependency diamond."""

    namespace: str = "urn:tiergraph:witness:fold"
    channel: str = "main"
    tie_policy: str = "all minimum-valued paths in canonical lexicographic order"

    def name(self, local: str) -> QualifiedName:
        """Return a name in the fixture's declared namespace."""
        return QualifiedName(self.namespace, local)

    def graph(self) -> Graph:
        """Return start→bed→out and start→sting→out with hand-sized weights."""
        placement = self.name("placement")
        cost = self.name("cost")
        gain = self.name("gain")
        tie = self.name("tie")
        coordinate = self.name("coordinate")

        def decimal(name: QualifiedName, lexical: str) -> AttributeValue:
            return AttributeValue(name, XsdType.DECIMAL, lexical)

        def integer(name: QualifiedName, lexical: str) -> AttributeValue:
            return AttributeValue(name, XsdType.INTEGER, lexical)

        items = (
            Item(
                "start",
                (
                    decimal(cost, "0"),
                    decimal(gain, "0"),
                    decimal(tie, "0"),
                    integer(coordinate, "0"),
                ),
            ),
            Item(
                "bed",
                (
                    decimal(cost, "2"),
                    decimal(gain, "1"),
                    decimal(tie, "0"),
                    integer(coordinate, "4"),
                ),
            ),
            Item(
                "sting",
                (
                    decimal(cost, "1"),
                    decimal(gain, "4"),
                    decimal(tie, "0"),
                    integer(coordinate, "8"),
                ),
            ),
            Item(
                "out",
                (
                    decimal(cost, "3"),
                    decimal(gain, "1"),
                    decimal(tie, "0"),
                    integer(coordinate, "12"),
                ),
            ),
        )
        members = SimpleRelationDeclaration(
            self.name("placements"), placement, self.name("placement-type")
        )
        dependency = BipartiteRelationDeclaration(
            self.name("depends"),
            self.name("placement-type"),
            self.name("placement-type"),
            acyclic=True,
        )
        edges = (
            RelationInstance(
                dependency.name, ItemRef(placement, 0), ItemRef(placement, 1)
            ),
            RelationInstance(
                dependency.name, ItemRef(placement, 0), ItemRef(placement, 2)
            ),
            RelationInstance(
                dependency.name, ItemRef(placement, 1), ItemRef(placement, 3)
            ),
            RelationInstance(
                dependency.name, ItemRef(placement, 2), ItemRef(placement, 3)
            ),
        )
        declarations = (
            AttributeDeclaration(cost, AttributeDomain.ITEM, XsdType.DECIMAL),
            AttributeDeclaration(gain, AttributeDomain.ITEM, XsdType.DECIMAL),
            AttributeDeclaration(tie, AttributeDomain.ITEM, XsdType.DECIMAL),
            AttributeDeclaration(coordinate, AttributeDomain.ITEM, XsdType.INTEGER),
        )
        return Graph(
            (NamespaceDeclaration("mix", self.namespace),),
            (Tier(TierDeclaration(placement, "Mix placements"), items),),
            (members, dependency),
            edges,
            declarations,
        )

    def valuation(self, attribute: str) -> Valuation:
        """Return the only domain-aware part of the fold."""
        attribute_name = self.name(attribute)

        def read(graph: Graph, reference: ItemRef) -> Decimal:
            tier = next(
                tier for tier in graph.tiers if tier.declaration.name == reference.tier
            )
            item = tier.items[reference.index]
            value = next(
                value for value in item.attributes if value.name == attribute_name
            )
            return Decimal(value.lexical)

        return read

    def coordinates(self, graph: Graph, provenance: Provenance) -> tuple[int, ...]:
        """Yield structural coordinates after recognition has finished."""
        by_id = {
            item.durable_id: item
            for tier in graph.tiers
            for item in tier.items
            if item.durable_id is not None
        }
        result: list[int] = []
        coordinate_name = self.name("coordinate")
        for path in provenance:
            for durable_id in path:
                item = by_id[durable_id]
                value = next(
                    value for value in item.attributes if value.name == coordinate_name
                )
                result.append(int(value.lexical))
        return tuple(result)

    def states(self, graph: Graph) -> tuple[State, ...]:
        """Declare the finite placement-reference × output-channel product."""
        return tuple((reference, self.channel) for reference in graph.canonical_items())


def recognize[Value](
    graph: Graph,
    states: tuple[State, ...],
    valuation: Valuation,
    semiring: Semiring[Value],
    lift: Callable[[Decimal, str], Value],
    provenance: Callable[[Value], Provenance | None],
    *,
    output_cap: int,
) -> Recognition[Value]:
    """Recognize over the declared DAG using only the semiring's add and multiply."""
    if output_cap < 1:
        raise ValueError(f"output cap {output_cap!r} must be positive")
    references = tuple(state[0] for state in states)
    admitted = set(references)
    outgoing: dict[ItemRef, list[ItemRef]] = {reference: [] for reference in references}
    for relation in graph.relations:
        if relation.left in admitted and relation.right in admitted:
            outgoing[relation.left].append(relation.right)
    cache: dict[ItemRef, Value] = {}

    def visit(reference: ItemRef) -> Value:
        if reference in cache:
            return cache[reference]
        item = next(
            tier.items[reference.index]
            for tier in graph.tiers
            if tier.declaration.name == reference.tier
        )
        if item.durable_id is None:
            raise ValueError(f"fold item {reference.to_data()!r} has no durable id")
        local = lift(valuation(graph, reference), item.durable_id)
        alternatives = semiring.zero
        children = outgoing[reference]
        if not children:
            alternatives = semiring.one
        else:
            for child in children:
                alternatives = semiring.add(alternatives, visit(child))
        result = semiring.multiply(local, alternatives)
        cache[reference] = result
        return result

    value = visit(references[0])
    complete = provenance(value)
    if complete is None:
        return Recognition(value, None, False)
    return Recognition(value, complete[:output_cap], len(complete) > output_cap)


def act[Value, Carrier, ActionResult](
    recognition: Recognition[Value],
    graph: Graph,
    fixture: FoldFixture,
    carrier: Carrier,
    action: Action[Carrier, ActionResult],
) -> ActionResult:
    """Apply witness coordinates without allowing recognition to inspect the carrier."""
    if recognition.provenance is None:
        raise ValueError("recognition has no witnesses for action")
    return action(carrier, fixture.coordinates(graph, recognition.provenance))


def canonical_bytes(value: Mapping[str, object]) -> bytes:
    """Encode a recognized result independently of mapping insertion order."""
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
