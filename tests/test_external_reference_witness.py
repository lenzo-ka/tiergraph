"""Witness external regions as ordinary items with durable source references."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace

import pytest

from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    DurableItemRef,
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

NS = "urn:tiergraph:witness:external-reference"
SOURCE_TIER = QualifiedName(NS, "source")
SELECTION_TIER = QualifiedName(NS, "selection")
PLACEMENT_TIER = QualifiedName(NS, "placement")
NOTE_TIER = QualifiedName(NS, "note")
SOURCE_TYPE = QualifiedName(NS, "source-type")
SELECTION_TYPE = QualifiedName(NS, "selection-type")
PLACEMENT_TYPE = QualifiedName(NS, "placement-type")
NOTE_TYPE = QualifiedName(NS, "note-type")
SOURCE_MEMBERS = QualifiedName(NS, "sources")
SELECTION_MEMBERS = QualifiedName(NS, "selections")
PLACEMENT_MEMBERS = QualifiedName(NS, "placements")
NOTE_MEMBERS = QualifiedName(NS, "notes")
USES = QualifiedName(NS, "uses-selection")
DESCRIBES = QualifiedName(NS, "describes")
LENGTH = QualifiedName(NS, "source-length")
SOURCE_REFERENCE = QualifiedName(NS, "source-reference")
ORIGIN = QualifiedName(NS, "origin")
EXTENT = QualifiedName(NS, "extent")
AT = QualifiedName(NS, "at")
LABEL = QualifiedName(NS, "label")
PRIMITIVE_SELECTIONS = QualifiedName(NS, "primitive-selections")


def _value(name: QualifiedName, value_type: XsdType, lexical: str) -> AttributeValue:
    return AttributeValue(name, value_type, lexical)


def _lexical(item: Item, name: QualifiedName) -> str:
    return next(value.lexical for value in item.attributes if value.name == name)


def _canonical_bytes(graph: Graph) -> bytes:
    return json.dumps(
        graph.to_data(), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


@dataclass(frozen=True)
class ExternalReferenceFixture:
    """Build and edit the page-sized mix through checked profile operations."""

    source_lengths: tuple[tuple[str, int], ...] = (("source-b", 80), ("source-a", 100))

    def graph(self, *, integer_spelling: str = "10") -> Graph:
        """Declare one region, two uses, and an ordinary relation targeting it."""
        source_items = tuple(
            Item(identity, (_value(LENGTH, XsdType.INTEGER, str(length)),))
            for identity, length in self.source_lengths
        )
        selection = self.selection(
            source_items, "source-a", integer_spelling, "20", "selection-main"
        )
        tiers = (
            Tier(TierDeclaration(SOURCE_TIER, "External sources"), source_items),
            Tier(TierDeclaration(SELECTION_TIER, "Source selections"), (selection,)),
            Tier(
                TierDeclaration(PLACEMENT_TIER, "Mix placements"),
                (
                    Item("placement-intro", (_value(AT, XsdType.INTEGER, "0"),)),
                    Item("placement-return", (_value(AT, XsdType.INTEGER, "50"),)),
                ),
            ),
            Tier(
                TierDeclaration(NOTE_TIER, "Mix notes"),
                (Item(attributes=(_value(LABEL, XsdType.STRING, "hook"),)),),
            ),
        )
        simple = (
            SimpleRelationDeclaration(SOURCE_MEMBERS, SOURCE_TIER, SOURCE_TYPE),
            SimpleRelationDeclaration(
                SELECTION_MEMBERS, SELECTION_TIER, SELECTION_TYPE
            ),
            SimpleRelationDeclaration(
                PLACEMENT_MEMBERS, PLACEMENT_TIER, PLACEMENT_TYPE
            ),
            SimpleRelationDeclaration(NOTE_MEMBERS, NOTE_TIER, NOTE_TYPE),
        )
        relations = (
            RelationInstance(
                USES, ItemRef(PLACEMENT_TIER, 0), ItemRef(SELECTION_TIER, 0)
            ),
            RelationInstance(
                USES, ItemRef(PLACEMENT_TIER, 1), ItemRef(SELECTION_TIER, 0)
            ),
            RelationInstance(
                DESCRIBES, ItemRef(NOTE_TIER, 0), ItemRef(SELECTION_TIER, 0)
            ),
        )
        return Graph(
            (NamespaceDeclaration("mix", NS),),
            tiers,
            (
                *simple,
                BipartiteRelationDeclaration(USES, PLACEMENT_TYPE, SELECTION_TYPE),
                BipartiteRelationDeclaration(DESCRIBES, NOTE_TYPE, SELECTION_TYPE),
            ),
            relations,
            (
                AttributeDeclaration(LENGTH, AttributeDomain.ITEM, XsdType.INTEGER),
                AttributeDeclaration(
                    SOURCE_REFERENCE, AttributeDomain.ITEM, XsdType.STRING
                ),
                AttributeDeclaration(ORIGIN, AttributeDomain.ITEM, XsdType.INTEGER),
                AttributeDeclaration(EXTENT, AttributeDomain.ITEM, XsdType.INTEGER),
                AttributeDeclaration(AT, AttributeDomain.ITEM, XsdType.INTEGER),
                AttributeDeclaration(LABEL, AttributeDomain.ITEM, XsdType.STRING),
            ),
        )

    def selection(
        self,
        sources: tuple[Item, ...],
        source_id: str,
        origin_text: str,
        extent_text: str,
        selection_id: str,
    ) -> Item:
        """Refuse an invalid selection at the declaration that introduced it."""
        lengths = {
            source.durable_id: int(_lexical(source, LENGTH)) for source in sources
        }
        if source_id not in lengths:
            raise ValueError(
                f"declare selection {selection_id!r}: source {source_id!r} was never declared"
            )
        origin = int(origin_text)
        extent = int(extent_text)
        if origin < 0 or extent < 0 or origin + extent > lengths[source_id]:
            raise ValueError(
                f"declare selection {selection_id!r}: region origin {origin}, extent "
                f"{extent} exceeds source {source_id!r} length {lengths[source_id]}"
            )
        return Item(
            selection_id,
            (
                _value(SOURCE_REFERENCE, XsdType.STRING, source_id),
                _value(ORIGIN, XsdType.INTEGER, origin_text),
                _value(EXTENT, XsdType.INTEGER, extent_text),
            ),
        )

    def resolve(self, graph: Graph) -> tuple[tuple[str, int, int], ...]:
        """Resolve every placement through its edge and the region's durable source id."""
        selection_tier = next(
            tier for tier in graph.tiers if tier.declaration.name == SELECTION_TIER
        )
        placement_tier = next(
            tier for tier in graph.tiers if tier.declaration.name == PLACEMENT_TIER
        )
        answers = []
        for relation in graph.relations:
            if relation.declaration != USES:
                continue
            selection = selection_tier.items[relation.right.index]
            source_id = _lexical(selection, SOURCE_REFERENCE)
            source = graph.resolve_item(DurableItemRef(source_id))
            placement = placement_tier.items[relation.left.index]
            answers.append(
                (
                    source_id,
                    int(_lexical(selection, ORIGIN)),
                    int(_lexical(placement, AT)),
                )
            )
            assert source.tier == SOURCE_TIER
        return tuple(answers)

    def move_source_first(self, graph: Graph, source_id: str) -> Graph:
        """Move a source without rewriting references that promise to survive it."""
        tiers = list(graph.tiers)
        source_index = next(
            index
            for index, tier in enumerate(tiers)
            if tier.declaration.name == SOURCE_TIER
        )
        source_tier = tiers[source_index]
        moved = next(item for item in source_tier.items if item.durable_id == source_id)
        remaining = tuple(item for item in source_tier.items if item is not moved)
        tiers[source_index] = replace(source_tier, items=(moved, *remaining))
        return Graph(
            graph.namespaces,
            tuple(tiers),
            graph.relation_declarations,
            graph.relations,
            graph.attribute_declarations,
        )


FIXTURE = ExternalReferenceFixture()


def test_page_sized_oracle_resolves_each_declared_object_after_source_move() -> None:
    """The region [10,30) in source-a appears at mix coordinates 0 and 50."""
    graph = FIXTURE.graph()
    assert FIXTURE.resolve(graph) == (
        ("source-a", 10, 0),
        ("source-a", 10, 50),
    )
    moved = FIXTURE.move_source_first(graph, "source-a")
    assert moved.resolve_item(DurableItemRef("source-a")) == ItemRef(SOURCE_TIER, 0)
    assert FIXTURE.resolve(moved) == (
        ("source-a", 10, 0),
        ("source-a", 10, 50),
    )


def test_selection_and_placements_remain_three_distinguishable_items() -> None:
    """Reuse points two placement identities at one separate selection identity."""
    graph = FIXTURE.graph()
    uses = tuple(edge for edge in graph.relations if edge.declaration == USES)
    assert uses[0].left != uses[1].left
    assert uses[0].right == uses[1].right == ItemRef(SELECTION_TIER, 0)
    identities = {
        graph.tiers[1].items[0].durable_id,
        graph.tiers[2].items[0].durable_id,
        graph.tiers[2].items[1].durable_id,
    }
    assert identities == {"selection-main", "placement-intro", "placement-return"}


def test_item_selection_accepts_ordinary_attributes_and_relation_endpoints() -> None:
    """The selection carries item attributes and is the target of two relation kinds."""
    graph = FIXTURE.graph()
    selection = graph.tiers[1].items[0]
    assert _lexical(selection, EXTENT) == "20"
    assert tuple(
        edge.declaration
        for edge in graph.relations
        if edge.right.tier == SELECTION_TIER
    ) == (
        USES,
        USES,
        DESCRIBES,
    )


def test_invalid_external_references_fail_at_the_offending_declaration() -> None:
    """Near-valid source and extent errors name the selection operation and offender."""
    sources = FIXTURE.graph().tiers[0].items
    with pytest.raises(
        ValueError, match="declare selection 'missing'.*'source-z'.*never declared"
    ):
        FIXTURE.selection(sources, "source-z", "10", "20", "missing")
    with pytest.raises(
        ValueError, match="declare selection 'long'.*extent 91.*'source-a'.*100"
    ):
        FIXTURE.selection(sources, "source-a", "10", "91", "long")


def test_canonical_bytes_ignore_integer_presentation_variation() -> None:
    """Equivalent XSD integer spellings lower to the same graph bytes."""
    assert _canonical_bytes(
        FIXTURE.graph(integer_spelling=" +010 ")
    ) == _canonical_bytes(FIXTURE.graph(integer_spelling="10"))


@dataclass(frozen=True)
class PrimitiveSelection:
    """Model the rejected second population outside item tiers."""

    source_id: str
    origin: int
    extent: int

    def attributes(self) -> tuple[AttributeValue, ...]:
        """Encode attributes through machinery separate from item attributes."""
        return (
            _value(SOURCE_REFERENCE, XsdType.STRING, self.source_id),
            _value(ORIGIN, XsdType.INTEGER, str(self.origin)),
            _value(EXTENT, XsdType.INTEGER, str(self.extent)),
        )


@dataclass(frozen=True)
class SplitPopulation:
    """Expose the duplicated cases forced by a primitive selection population."""

    graph: Graph
    selections: tuple[PrimitiveSelection, ...]

    def traverse(self) -> tuple[tuple[ItemRef | PrimitiveSelection, ...], int]:
        """Run one traversal case for items and another for primitives."""
        populations = (self.graph.canonical_items(), self.selections)
        return tuple(
            member for population in populations for member in population
        ), len(populations)

    def attributes(
        self, owner: Item | PrimitiveSelection
    ) -> tuple[tuple[AttributeValue, ...], int]:
        """Dispatch attributes through distinct item and primitive cases."""
        owner_types = (Item, PrimitiveSelection)
        if isinstance(owner, Item):
            return owner.attributes, len(owner_types)
        return owner.attributes(), len(owner_types)

    def relate_note_to_selection(self, selection_index: int) -> Graph:
        """Attempt an ordinary relation to a primitive outside the tier structure."""
        relation = RelationInstance(
            DESCRIBES,
            ItemRef(NOTE_TIER, 0),
            ItemRef(PRIMITIVE_SELECTIONS, selection_index),
        )
        return Graph(
            self.graph.namespaces,
            self.graph.tiers,
            self.graph.relation_declarations,
            (*self.graph.relations, relation),
            self.graph.attribute_declarations,
        )


def test_rejected_primitive_requires_second_cases_and_cannot_be_an_endpoint() -> None:
    """The split form duplicates traversal and attributes yet rejects the relation."""
    graph = FIXTURE.graph()
    primitive = PrimitiveSelection("source-a", 10, 20)
    rejected = SplitPopulation(graph, (primitive,))
    traversed, traversal_cases = rejected.traverse()
    assert traversed[-1] == primitive
    assert len(traversed) == len(graph.canonical_items()) + 1
    assert traversal_cases == 2
    item_attributes, item_attribute_cases = rejected.attributes(graph.tiers[1].items[0])
    primitive_attributes, attribute_cases = rejected.attributes(primitive)
    assert _lexical(Item(attributes=item_attributes), EXTENT) == "20"
    assert _lexical(Item(attributes=primitive_attributes), EXTENT) == "20"
    assert item_attribute_cases == attribute_cases == 2
    with pytest.raises(
        ValueError,
        match="relation instance 3 right endpoint.*undeclared tier.*primitive-selections",
    ):
        rejected.relate_note_to_selection(0)
    assert any(edge.declaration == DESCRIBES for edge in graph.relations)
