"""Witness addressable mix boundaries and their identity under insertion."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    Boundary,
    BoundaryRef,
    BoundarySide,
    DurableBoundaryRef,
    DurableItemRef,
    Graph,
    Item,
    NamespaceDeclaration,
    QualifiedName,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    XsdType,
)

NS = "urn:tiergraph:witness:mix-boundary"
NAMESPACE = (NamespaceDeclaration("mix", NS),)
PLACEMENT_TIER = QualifiedName(NS, "placement")
PLACEMENT_TYPE = QualifiedName(NS, "placement-type")
PLACEMENTS = QualifiedName(NS, "placements")
STEM_TIER = QualifiedName(NS, "stem")
STEM_TYPE = QualifiedName(NS, "stem-type")
STEMS = QualifiedName(NS, "stems")
LABEL = QualifiedName(NS, "label")
CUE = QualifiedName(NS, "cue")
AUTOMATION = QualifiedName(NS, "automation")
COORDINATE = QualifiedName(NS, "coordinate")


def _value(name: QualifiedName, value_type: XsdType, lexical: str) -> AttributeValue:
    return AttributeValue(name, value_type, lexical)


def _item(label: str) -> Item:
    return Item(label, (_value(LABEL, XsdType.STRING, label),))


def _canonical_bytes(graph: Graph) -> bytes:
    return json.dumps(
        graph.to_data(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


@dataclass(frozen=True)
class BoundaryFixture:
    """Provide the complete small mix graph and its insertion operation."""

    coordinate_lexical: str = "+001"

    def graph(self) -> Graph:
        """Build two placements and one stem with their referenced boundaries."""
        declarations = (
            SimpleRelationDeclaration(PLACEMENTS, PLACEMENT_TIER, PLACEMENT_TYPE),
            SimpleRelationDeclaration(STEMS, STEM_TIER, STEM_TYPE),
        )
        attributes = (
            AttributeDeclaration(LABEL, AttributeDomain.ITEM, XsdType.STRING),
            AttributeDeclaration(CUE, AttributeDomain.BOUNDARY, XsdType.STRING),
            AttributeDeclaration(AUTOMATION, AttributeDomain.BOUNDARY, XsdType.STRING),
            AttributeDeclaration(COORDINATE, AttributeDomain.BOUNDARY, XsdType.INTEGER),
        )
        boundaries = (
            Boundary(
                DurableBoundaryRef(DurableItemRef("lead-vocal"), BoundarySide.BEFORE),
                (
                    _value(CUE, XsdType.STRING, "vocal-in"),
                    _value(COORDINATE, XsdType.INTEGER, self.coordinate_lexical),
                ),
            ),
            Boundary(
                DurableBoundaryRef(STEM_TIER, BoundarySide.BEFORE),
                (_value(AUTOMATION, XsdType.STRING, "gain=0.25"),),
            ),
        )
        return Graph(
            NAMESPACE,
            (
                Tier(
                    TierDeclaration(PLACEMENT_TIER, "Placement"),
                    (_item("ambient-bed"), _item("lead-vocal")),
                ),
                Tier(TierDeclaration(STEM_TIER, "Stem"), (_item("ambient"),)),
            ),
            declarations,
            attribute_declarations=attributes,
            boundary_values=boundaries,
        )

    def insert_placement(self, graph: Graph, index: int, label: str) -> Graph:
        """Insert one placement; anchored boundaries need no coordinate repair."""
        tiers: list[Tier] = []
        for tier in graph.tiers:
            if tier.declaration.name != PLACEMENT_TIER:
                tiers.append(tier)
                continue
            items = (*tier.items[:index], _item(label), *tier.items[index:])
            tiers.append(Tier(tier.declaration, items, tier.attributes))
        return Graph(
            graph.namespaces,
            tuple(tiers),
            graph.relation_declarations,
            graph.relations,
            graph.attribute_declarations,
            graph.boundary_values,
            graph.attributes,
        )

    def neighbors(self, graph: Graph, reference: BoundaryRef) -> tuple[str, str]:
        """Name the items on either side so boundary meaning is observable."""
        tier = next(
            candidate
            for candidate in graph.tiers
            if candidate.declaration.name == reference.tier
        )

        def label(item: Item) -> str:
            return next(
                value.lexical for value in item.attributes if value.name == LABEL
            )

        before = (
            "<start>"
            if reference.index == 0
            else label(tier.items[reference.index - 1])
        )
        after = (
            "<end>"
            if reference.index == len(tier.items)
            else label(tier.items[reference.index])
        )
        return before, after


FIXTURE = BoundaryFixture()


def test_page_sized_oracle_addresses_every_boundary() -> None:
    """Two placements have three boundaries; one stem has two, all distinct."""
    graph = FIXTURE.graph()
    placement_positions = tuple(
        boundary.reference for boundary in graph.boundaries(PLACEMENT_TIER)
    )
    stem_positions = tuple(
        boundary.reference for boundary in graph.boundaries(STEM_TIER)
    )
    assert placement_positions == (
        BoundaryRef(PLACEMENT_TIER, 0),
        BoundaryRef(PLACEMENT_TIER, 1),
        BoundaryRef(PLACEMENT_TIER, 2),
    )
    assert stem_positions == (
        BoundaryRef(STEM_TIER, 0),
        BoundaryRef(STEM_TIER, 1),
    )
    assert len(set((*placement_positions, *stem_positions))) == 5
    assert FIXTURE.neighbors(graph, placement_positions[0]) == (
        "<start>",
        "ambient-bed",
    )
    assert FIXTURE.neighbors(graph, placement_positions[1]) == (
        "ambient-bed",
        "lead-vocal",
    )
    assert FIXTURE.neighbors(graph, placement_positions[2]) == (
        "lead-vocal",
        "<end>",
    )
    assert (
        graph.resolve_boundary(
            DurableBoundaryRef(DurableItemRef("lead-vocal"), BoundarySide.BEFORE)
        )
        == placement_positions[1]
    )
    assert (
        graph.resolve_boundary(DurableBoundaryRef(STEM_TIER, BoundarySide.BEFORE))
        == stem_positions[0]
    )


def test_durable_boundary_survives_insertion_while_structural_reference_moves() -> None:
    """Reject bare offsets for undetectable drift when insertion precedes a seam."""
    original = FIXTURE.graph()
    bare_offset = BoundaryRef(PLACEMENT_TIER, 1)
    durable = DurableBoundaryRef(DurableItemRef("lead-vocal"), BoundarySide.BEFORE)
    assert FIXTURE.neighbors(original, original.resolve_boundary(bare_offset)) == (
        "ambient-bed",
        "lead-vocal",
    )
    inserted = FIXTURE.insert_placement(original, 1, "pickup")
    assert inserted.resolve_boundary(durable) == BoundaryRef(PLACEMENT_TIER, 2)
    assert FIXTURE.neighbors(inserted, inserted.resolve_boundary(durable)) == (
        "pickup",
        "lead-vocal",
    )
    values = inserted.boundaries(PLACEMENT_TIER)
    assert {value.name for value in values[2].attributes} == {CUE, COORDINATE}
    assert values[1].attributes == ()
    assert inserted.resolve_boundary(bare_offset) == BoundaryRef(PLACEMENT_TIER, 1)
    assert FIXTURE.neighbors(inserted, inserted.resolve_boundary(bare_offset)) == (
        "ambient-bed",
        "pickup",
    )
    assert inserted.resolve_boundary(bare_offset) != inserted.resolve_boundary(durable)
    assert inserted.resolve_boundary(
        DurableBoundaryRef(STEM_TIER, BoundarySide.BEFORE)
    ) == BoundaryRef(STEM_TIER, 0)


@pytest.mark.parametrize(("index", "resolved_index"), [(0, 2), (1, 2), (2, 1)])
def test_anchor_survives_insertion_anywhere(index: int, resolved_index: int) -> None:
    """Insertion before, at, or after a seam leaves its item anchor unchanged."""
    graph = FIXTURE.insert_placement(FIXTURE.graph(), index, f"insert-{index}")
    durable = DurableBoundaryRef(DurableItemRef("lead-vocal"), BoundarySide.BEFORE)
    resolved = graph.resolve_boundary(durable)
    assert resolved == BoundaryRef(PLACEMENT_TIER, resolved_index)
    assert FIXTURE.neighbors(graph, resolved)[1] == "lead-vocal"


def test_promotion_for_identity_alone_stays_sparse() -> None:
    """Promoting the trailing boundary stores no invented attribute value."""
    graph = FIXTURE.graph()
    trailing = BoundaryRef(PLACEMENT_TIER, 2)
    promoted, durable = graph.promote_boundary(trailing, "mix-end")
    assert durable == DurableBoundaryRef(PLACEMENT_TIER, BoundarySide.AFTER)
    assert promoted is graph
    assert promoted.boundary_values == graph.boundary_values


def test_presentation_only_numeric_spelling_has_canonical_bytes() -> None:
    """Equivalent XSD integer spellings cannot alter serialized graph bytes."""
    signed_padded = BoundaryFixture("+001").graph()
    plain = BoundaryFixture("1").graph()
    assert signed_padded == plain
    assert _canonical_bytes(signed_padded) == _canonical_bytes(plain)
