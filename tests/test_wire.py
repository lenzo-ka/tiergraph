"""The reference codec satisfies the reusable primitive wire laws."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import cast

import pytest

from tests.conformance.wire import WireLawSuite
from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    BoundarySide,
    DurableItemRef,
    DurablePositionRef,
    Graph,
    Item,
    ItemRef,
    NamespaceDeclaration,
    Position,
    PositionRef,
    QualifiedName,
    RelationEndpointKind,
    RelationInstance,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    XsdType,
    dump_bytes,
    loads,
    to_data,
)

NS = "urn:wire-test"


def name(local: str) -> QualifiedName:
    """Return a fixture name in the declared namespace."""
    return QualifiedName(NS, local)


def rich_graph() -> Graph:
    """Build a graph where incidental wire changes affect visible structure."""
    gain = AttributeDeclaration(name("gain"), AttributeDomain.ITEM, XsdType.DECIMAL)
    label = AttributeDeclaration(
        name("label"), AttributeDomain.DOCUMENT, XsdType.STRING
    )
    marker = AttributeDeclaration(
        name("marker"), AttributeDomain.POSITION, XsdType.BOOLEAN
    )
    placements = name("placements")
    members = SimpleRelationDeclaration(name("members"), placements, name("event"))
    cues = BipartiteRelationDeclaration(
        name("cues"),
        name("event"),
        name("event"),
        right_endpoint=RelationEndpointKind.BOUNDARY,
    )
    tier = Tier(
        TierDeclaration(placements, "Placements"),
        (
            Item("lead", (AttributeValue(gain.name, XsdType.DECIMAL, "01.00"),)),
            Item("bed", (AttributeValue(gain.name, XsdType.DECIMAL, "0.5"),)),
        ),
    )
    boundary = DurablePositionRef(DurableItemRef("bed"), BoundarySide.BEFORE)
    return Graph(
        (NamespaceDeclaration("w", NS),),
        (tier,),
        (members, cues),
        (RelationInstance(cues.name, ItemRef(placements, 0), boundary, "cue-1"),),
        (gain, label, marker),
        (Position(boundary, (AttributeValue(marker.name, XsdType.BOOLEAN, "1"),)),),
        (AttributeValue(label.name, XsdType.STRING, "mix α"),),
    )


LAWS = WireLawSuite(dump_bytes, loads, rich_graph)


@pytest.mark.parametrize(
    "law",
    [
        LAWS.check_round_trip,
        LAWS.check_equal_graphs_have_equal_bytes,
        LAWS.check_strict_json,
        LAWS.check_canonical_read_back,
    ],
    ids=lambda law: law.__name__,
)
def test_wire_law(law: object) -> None:
    """Run each reusable law against the reference codec."""
    assert callable(law)
    law()


def mutable_document() -> dict[str, object]:
    """Return independent JSON-shaped fixture data for near-valid edits."""
    return cast(dict[str, object], deepcopy(to_data(rich_graph())))


def graph_data(document: dict[str, object]) -> dict[str, object]:
    """Return the graph object from fixture data."""
    return cast(dict[str, object], document["graph"])


def test_read_edit_write_changes_only_declared_value_line() -> None:
    """Editing one canonical lexical value leaves every other byte in place."""
    before = dump_bytes(rich_graph())
    document = json.loads(before)
    graph = cast(dict[str, object], document["graph"])
    tiers = cast(list[dict[str, object]], graph["tiers"])
    items = cast(list[dict[str, object]], tiers[0]["items"])
    values = cast(list[dict[str, object]], items[1]["attributes"])
    values[0]["lexical"] = "0.75"
    edited = loads(json.dumps(document))
    after = dump_bytes(edited)
    differing = [
        (left, right)
        for left, right in zip(before.splitlines(), after.splitlines(), strict=True)
        if left != right
    ]
    assert differing == [
        (b'                "lexical": "0.5",', b'                "lexical": "0.75",')
    ]


def test_presentation_only_xsd_variation_returns_to_canonical_bytes() -> None:
    """Parsing an equivalent decimal spelling recovers the canonical document."""
    canonical = dump_bytes(rich_graph())
    document = json.loads(canonical)
    graph = cast(dict[str, object], document["graph"])
    tiers = cast(list[dict[str, object]], graph["tiers"])
    items = cast(list[dict[str, object]], tiers[0]["items"])
    values = cast(list[dict[str, object]], items[0]["attributes"])
    assert values[0]["lexical"] == "1.0"
    values[0]["lexical"] = "+001.000"
    assert dump_bytes(loads(json.dumps(document))) == canonical


def test_reference_kinds_and_anchor_union_round_trip_distinguishably() -> None:
    """Structural items and both durable boundary anchors retain their tags."""
    graph = rich_graph()
    tier_anchor = DurablePositionRef(name("placements"), BoundarySide.AFTER)
    extended = Graph(
        graph.namespaces,
        graph.tiers,
        graph.relation_declarations,
        graph.relations,
        graph.attribute_declarations,
        (
            *graph.position_values,
            Position(tier_anchor, graph.position_values[0].attributes),
        ),
        graph.attributes,
    )
    decoded = loads(dump_bytes(extended))
    assert isinstance(decoded.relations[0].left, ItemRef)
    item_anchor = cast(DurablePositionRef, decoded.relations[0].right)
    assert isinstance(item_anchor.anchor, DurableItemRef)
    outer_anchor = cast(DurablePositionRef, decoded.position_values[1].reference)
    assert isinstance(outer_anchor.anchor, QualifiedName)
    assert item_anchor != outer_anchor


def test_missing_declaration_refuses_at_attribute_validation() -> None:
    """A value whose declaration was removed names the undeclared attribute."""
    document = mutable_document()
    declarations = cast(
        list[dict[str, object]], graph_data(document)["attribute_declarations"]
    )
    graph_data(document)["attribute_declarations"] = declarations[1:]
    LAWS.check_refusal("attribute.*gain.*undeclared", document)


def test_out_of_range_endpoint_refuses_at_relation_validation() -> None:
    """A near-valid coordinate names the relation endpoint outside its tier."""
    document = mutable_document()
    relations = cast(list[dict[str, object]], graph_data(document)["relations"])
    left = cast(dict[str, object], relations[0]["left"])
    left["index"] = 9
    LAWS.check_refusal("relation instance 0 left endpoint.*outside tier", document)


def test_contradictory_xsd_lexical_form_refuses_at_value_construction() -> None:
    """A decimal declaration paired with exponent syntax names that value."""
    document = mutable_document()
    tiers = cast(list[dict[str, object]], graph_data(document)["tiers"])
    items = cast(list[dict[str, object]], tiers[0]["items"])
    values = cast(list[dict[str, object]], items[0]["attributes"])
    values[0]["lexical"] = "1e0"
    LAWS.check_refusal("attribute.*gain.*invalid decimal value '1e0'", document)


def test_missing_durable_anchor_refuses_at_position_resolution() -> None:
    """A durable boundary names the absent item anchor during graph construction."""
    document = mutable_document()
    positions = cast(list[dict[str, object]], graph_data(document)["position_values"])
    reference = cast(dict[str, object], positions[0]["reference"])
    anchor = cast(dict[str, object], reference["anchor"])
    anchor["durable_id"] = "absent"
    LAWS.check_refusal("durable position anchor item 'absent' was not found", document)


def test_structural_position_and_anonymous_values_round_trip() -> None:
    """Structural positions and absent durable ids take their distinct wire branches."""
    graph = rich_graph()
    structural = Position(
        PositionRef(name("placements"), 2), graph.position_values[0].attributes
    )
    anonymous_tier = Tier(TierDeclaration(name("anonymous"), "Anonymous"), (Item(),))
    extended = Graph(
        graph.namespaces,
        (*graph.tiers, anonymous_tier),
        graph.relation_declarations,
        (
            RelationInstance(
                graph.relations[0].declaration,
                graph.relations[0].left,
                graph.relations[0].right,
            ),
        ),
        graph.attribute_declarations,
        (structural,),
        graph.attributes,
    )
    assert loads(dump_bytes(extended)) == extended


def test_wire_shape_guards_name_their_paths() -> None:
    """Near-valid shape and tag errors fail at the field being decoded."""
    with pytest.raises(ValueError, match="parse JSON failed"):
        loads("{")
    with pytest.raises(ValueError, match="parse UTF-8 failed"):
        loads(b"\xff")

    cases: list[tuple[dict[str, object], str]] = []

    wrong_version = mutable_document()
    wrong_version["format_version"] = "2"
    cases.append((wrong_version, "format_version '2' is unsupported"))

    missing = mutable_document()
    del missing["format_version"]
    cases.append((missing, "document is missing field 'format_version'"))

    extra = mutable_document()
    extra["machine_version"] = "1"
    cases.append((extra, "document has unknown field 'machine_version'"))

    not_object = mutable_document()
    not_object["graph"] = []
    cases.append((not_object, "graph must be an object"))

    not_array = mutable_document()
    graph_data(not_array)["tiers"] = {}
    cases.append((not_array, "tiers must be an array"))

    bad_string = mutable_document()
    bad_string["format_version"] = 1
    cases.append((bad_string, "format_version must be a string"))

    bad_index = mutable_document()
    relations = cast(list[dict[str, object]], graph_data(bad_index)["relations"])
    left = cast(dict[str, object], relations[0]["left"])
    left["index"] = True
    cases.append((bad_index, r"relations\[0\].left.index must be an integer"))

    bad_boolean = mutable_document()
    declarations = cast(
        list[dict[str, object]], graph_data(bad_boolean)["relation_declarations"]
    )
    declarations[1]["acyclic"] = 0
    cases.append((bad_boolean, r"relation_declarations\[1\].acyclic must be a boolean"))

    bad_enum = mutable_document()
    attributes = cast(
        list[dict[str, object]], graph_data(bad_enum)["attribute_declarations"]
    )
    attributes[0]["domain"] = "node"
    cases.append(
        (bad_enum, r"attribute_declarations\[0\].domain has unsupported value 'node'")
    )

    bad_relation_kind = mutable_document()
    declarations = cast(
        list[dict[str, object]],
        graph_data(bad_relation_kind)["relation_declarations"],
    )
    declarations[0]["kind"] = "ternary"
    cases.append(
        (bad_relation_kind, r"relation_declarations\[0\].kind 'ternary' is unsupported")
    )

    bad_anchor_kind = mutable_document()
    positions = cast(
        list[dict[str, object]], graph_data(bad_anchor_kind)["position_values"]
    )
    reference = cast(dict[str, object], positions[0]["reference"])
    anchor = cast(dict[str, object], reference["anchor"])
    anchor["kind"] = "document"
    cases.append(
        (bad_anchor_kind, r"position_values\[0\].reference.anchor.kind 'document'")
    )

    for document, match in cases:
        LAWS.check_refusal(match, document)
