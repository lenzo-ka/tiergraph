"""Witness addressable mix boundaries and their identity under insertion."""

from __future__ import annotations

import json
from dataclasses import dataclass

from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    DurablePositionRef,
    Graph,
    Item,
    NamespaceDeclaration,
    Position,
    PositionRef,
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
CUE_ID = "vocal-entry"
AUTOMATION_ID = "ambient-start"


def _value(name: QualifiedName, value_type: XsdType, lexical: str) -> AttributeValue:
    return AttributeValue(name, value_type, lexical)


def _item(label: str) -> Item:
    return Item(attributes=(_value(LABEL, XsdType.STRING, label),))


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
            AttributeDeclaration(CUE, AttributeDomain.POSITION, XsdType.STRING),
            AttributeDeclaration(AUTOMATION, AttributeDomain.POSITION, XsdType.STRING),
            AttributeDeclaration(COORDINATE, AttributeDomain.POSITION, XsdType.INTEGER),
        )
        positions = (
            Position(
                PositionRef(PLACEMENT_TIER, 1),
                (
                    _value(CUE, XsdType.STRING, "vocal-in"),
                    _value(COORDINATE, XsdType.INTEGER, self.coordinate_lexical),
                ),
                CUE_ID,
            ),
            Position(
                PositionRef(STEM_TIER, 0),
                (_value(AUTOMATION, XsdType.STRING, "gain=0.25"),),
                AUTOMATION_ID,
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
            position_values=positions,
        )

    def insert_placement(self, graph: Graph, index: int, label: str) -> Graph:
        """Insert one placement while carrying later position identities forward.

        Inserting exactly at a promoted boundary splits it, and the two halves
        cannot both keep the identity. This carries it to the right-hand half,
        so a boundary promoted as "before lead-vocal" still sits before that
        placement afterwards. Carrying it left instead would keep it "after
        ambient-bed", which is equally coherent and gives a different answer.

        The kernel implements no edit operations, so this is the witness's own
        tie-break rather than a settled rule. It belongs to the same undecided
        family as removal, where the boundaries either side of a removed item
        become one.
        """
        tiers: list[Tier] = []
        for tier in graph.tiers:
            if tier.declaration.name != PLACEMENT_TIER:
                tiers.append(tier)
                continue
            items = (*tier.items[:index], _item(label), *tier.items[index:])
            tiers.append(Tier(tier.declaration, items, tier.attributes))
        positions = tuple(
            Position(
                PositionRef(
                    position.reference.tier,
                    position.reference.index + 1
                    if position.reference.tier == PLACEMENT_TIER
                    and position.reference.index >= index
                    else position.reference.index,
                ),
                position.attributes,
                position.durable_id,
            )
            for position in graph.position_values
        )
        return Graph(
            graph.namespaces,
            tuple(tiers),
            graph.relation_declarations,
            graph.relations,
            graph.attribute_declarations,
            positions,
            graph.attributes,
        )

    def neighbors(self, graph: Graph, reference: PositionRef) -> tuple[str, str]:
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
        position.reference for position in graph.positions(PLACEMENT_TIER)
    )
    stem_positions = tuple(
        position.reference for position in graph.positions(STEM_TIER)
    )
    assert placement_positions == (
        PositionRef(PLACEMENT_TIER, 0),
        PositionRef(PLACEMENT_TIER, 1),
        PositionRef(PLACEMENT_TIER, 2),
    )
    assert stem_positions == (
        PositionRef(STEM_TIER, 0),
        PositionRef(STEM_TIER, 1),
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
    assert graph.resolve_position(DurablePositionRef(CUE_ID)) == placement_positions[1]
    assert (
        graph.resolve_position(DurablePositionRef(AUTOMATION_ID)) == stem_positions[0]
    )


def test_durable_boundary_survives_insertion_while_structural_reference_moves() -> None:
    """Reject bare offsets for undetectable drift when insertion precedes a seam."""
    original = FIXTURE.graph()
    bare_offset = PositionRef(PLACEMENT_TIER, 1)
    durable = DurablePositionRef(CUE_ID)
    assert FIXTURE.neighbors(original, original.resolve_position(bare_offset)) == (
        "ambient-bed",
        "lead-vocal",
    )
    inserted = FIXTURE.insert_placement(original, 1, "pickup")
    assert inserted.resolve_position(durable) == PositionRef(PLACEMENT_TIER, 2)
    assert FIXTURE.neighbors(inserted, inserted.resolve_position(durable)) == (
        "pickup",
        "lead-vocal",
    )
    assert inserted.resolve_position(bare_offset) == PositionRef(PLACEMENT_TIER, 1)
    assert FIXTURE.neighbors(inserted, inserted.resolve_position(bare_offset)) == (
        "ambient-bed",
        "pickup",
    )
    assert inserted.resolve_position(bare_offset) != inserted.resolve_position(durable)
    assert inserted.resolve_position(DurablePositionRef(AUTOMATION_ID)) == PositionRef(
        STEM_TIER, 0
    )


def test_promotion_for_identity_alone_stays_sparse() -> None:
    """Promoting the trailing boundary stores no invented attribute value."""
    graph = FIXTURE.graph()
    trailing = PositionRef(PLACEMENT_TIER, 2)
    promoted, durable = graph.promote_position(trailing, "mix-end")
    stored = next(
        position
        for position in promoted.position_values
        if position.durable_id == durable.durable_id
    )
    assert stored.reference == trailing
    assert stored.attributes == ()
    assert len(promoted.position_values) == len(graph.position_values) + 1


def test_presentation_only_numeric_spelling_has_canonical_bytes() -> None:
    """Equivalent XSD integer spellings cannot alter serialized graph bytes."""
    signed_padded = BoundaryFixture("+001").graph()
    plain = BoundaryFixture("1").graph()
    assert signed_padded == plain
    assert _canonical_bytes(signed_padded) == _canonical_bytes(plain)
