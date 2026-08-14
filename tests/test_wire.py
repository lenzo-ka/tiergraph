"""The reference codec satisfies the reusable primitive wire laws."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from typing import cast

import pytest

from tests.conformance.wire import WireLawSuite
from tiergraph import (
    FORMAT_VERSION,
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
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    Position,
    PositionRef,
    QualifiedName,
    RelationEndpointKind,
    RelationInstance,
    RelationSideDeclaration,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    XsdType,
    dump_bytes,
    loads,
    to_data,
)

NS = "urn:wire-test"
META_NS = "urn:wire-meta"


def name(local: str) -> QualifiedName:
    """Return a fixture name in the declared namespace."""
    return QualifiedName(NS, local)


def rich_graph(*, reverse_unordered: bool = False) -> Graph:
    """Build a graph where incidental wire changes affect visible structure."""
    gain = AttributeDeclaration(name("gain"), AttributeDomain.ITEM, XsdType.DECIMAL)
    item_label = AttributeDeclaration(
        name("item-label"), AttributeDomain.ITEM, XsdType.STRING
    )
    label = AttributeDeclaration(
        name("label"), AttributeDomain.DOCUMENT, XsdType.STRING
    )
    revision = AttributeDeclaration(
        name("revision"), AttributeDomain.DOCUMENT, XsdType.INTEGER
    )
    marker = AttributeDeclaration(
        name("marker"), AttributeDomain.POSITION, XsdType.BOOLEAN
    )
    offset = AttributeDeclaration(
        name("offset"), AttributeDomain.POSITION, XsdType.INTEGER
    )
    placements = name("placements")
    members = SimpleRelationDeclaration(name("members"), placements, name("event"))
    cues = BipartiteRelationDeclaration(
        name("cues"),
        name("event"),
        name("event"),
        right_endpoint=RelationEndpointKind.BOUNDARY,
    )
    value_spellings: tuple[tuple[str, str], ...] = (
        ("lead", "01.00"),
        ("bed", "0.5"),
    )
    if reverse_unordered:
        value_spellings = tuple(reversed(value_spellings))
    item_values = {
        durable_id: AttributeValue(gain.name, XsdType.DECIMAL, lexical)
        for durable_id, lexical in value_spellings
    }
    placements_tier = Tier(
        TierDeclaration(placements, "Placements"),
        (
            Item(
                "lead",
                (
                    item_values["lead"],
                    AttributeValue(item_label.name, XsdType.STRING, "Lead"),
                ),
            ),
            Item(
                "bed",
                (
                    item_values["bed"],
                    AttributeValue(item_label.name, XsdType.STRING, "Bed"),
                ),
            ),
        ),
    )
    notes = name("notes")
    notes_tier = Tier(
        TierDeclaration(notes, "Notes"),
        (Item("intro"), Item("outro")),
    )
    boundary = DurablePositionRef(DurableItemRef("bed"), BoundarySide.BEFORE)
    namespaces: tuple[NamespaceDeclaration, ...] = (
        NamespaceDeclaration("w", NS),
        NamespaceDeclaration("meta", META_NS),
    )
    relation_declarations: tuple[
        SimpleRelationDeclaration | BipartiteRelationDeclaration, ...
    ] = (members, cues)
    attribute_declarations: tuple[AttributeDeclaration, ...] = (
        gain,
        item_label,
        label,
        revision,
        marker,
        offset,
    )
    if reverse_unordered:
        namespaces = tuple(reversed(namespaces))
        relation_declarations = tuple(reversed(relation_declarations))
        attribute_declarations = tuple(reversed(attribute_declarations))
    return Graph(
        namespaces,
        (placements_tier, notes_tier),
        relation_declarations,
        (RelationInstance(cues.name, ItemRef(placements, 0), boundary, "cue-1"),),
        attribute_declarations,
        (
            Position(
                boundary,
                (
                    AttributeValue(marker.name, XsdType.BOOLEAN, "1"),
                    AttributeValue(offset.name, XsdType.INTEGER, "1"),
                ),
            ),
        ),
        (
            AttributeValue(label.name, XsdType.STRING, "mix α"),
            AttributeValue(revision.name, XsdType.INTEGER, "1"),
        ),
    )


def canonical_variants() -> tuple[Graph, Graph]:
    """Reverse declarations and keyed values across multiple graph domains."""
    baseline = rich_graph()
    extra_position = Position(
        PositionRef(name("placements"), 0),
        tuple(
            replace(value, lexical="0")
            for value in baseline.position_values[0].attributes
        ),
    )
    left = replace(
        baseline, position_values=(*baseline.position_values, extra_position)
    )
    supplied = rich_graph(reverse_unordered=True)
    right = replace(
        supplied,
        tiers=tuple(
            replace(
                tier,
                items=tuple(
                    replace(item, attributes=tuple(reversed(item.attributes)))
                    for item in tier.items
                ),
                attributes=tuple(reversed(tier.attributes)),
            )
            for tier in supplied.tiers
        ),
        position_values=tuple(
            replace(position, attributes=tuple(reversed(position.attributes)))
            for position in reversed((*supplied.position_values, extra_position))
        ),
        attributes=tuple(reversed(supplied.attributes)),
    )
    return left, right


def ordered_variants() -> tuple[Graph, Graph, Graph]:
    """Reverse tiers and reverse items independently in meaningful collections."""
    graph = rich_graph()
    reversed_items = Tier(
        graph.tiers[0].declaration,
        tuple(reversed(graph.tiers[0].items)),
        graph.tiers[0].attributes,
    )
    return (
        graph,
        Graph(
            graph.namespaces,
            tuple(reversed(graph.tiers)),
            graph.relation_declarations,
            graph.relations,
            graph.attribute_declarations,
            graph.position_values,
            graph.attributes,
        ),
        Graph(
            graph.namespaces,
            (reversed_items, graph.tiers[1]),
            graph.relation_declarations,
            graph.relations,
            graph.attribute_declarations,
            graph.position_values,
            graph.attributes,
        ),
    )


LAWS = WireLawSuite(dump_bytes, loads, rich_graph, canonical_variants, ordered_variants)


@pytest.mark.parametrize(
    "law",
    [
        LAWS.check_round_trip,
        LAWS.check_equal_graphs_have_equal_bytes,
        LAWS.check_ordered_graphs_have_different_bytes,
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


def polyadic_document() -> dict[str, object]:
    """Return JSON-shaped fixture data containing one polyadic instance."""
    original = rich_graph()
    placements = name("placements")
    notes = name("notes")
    declaration = PolyadicRelationDeclaration(
        name("groups"),
        RelationSideDeclaration((RelationEndpointKind.ITEM,), (placements,), 1),
        RelationSideDeclaration((RelationEndpointKind.ITEM,), (notes,), 1),
    )
    relation = PolyadicRelationInstance(
        declaration.name,
        (ItemRef(placements, 0),),
        (ItemRef(notes, 0),),
        "group-1",
    )
    extended = Graph(
        original.namespaces,
        original.tiers,
        (*original.relation_declarations, declaration),
        original.relations,
        original.attribute_declarations,
        original.position_values,
        original.attributes,
        (relation,),
    )
    return cast(dict[str, object], deepcopy(to_data(extended)))


def graph_data(document: dict[str, object]) -> dict[str, object]:
    """Return the graph object from fixture data."""
    return cast(dict[str, object], document["graph"])


def test_polyadic_relation_durable_id_round_trips() -> None:
    """The guarded polyadic carrier retains a present durable identifier."""
    decoded = loads(json.dumps(polyadic_document()))
    assert decoded.polyadic_relations[0].durable_id == "group-1"


def test_read_edit_write_changes_only_declared_value_line() -> None:
    """Editing one value in an externally serialized document changes only its line."""
    external_document = {
        "format_version": FORMAT_VERSION,
        "graph": rich_graph().to_data(),
    }
    before = (
        json.dumps(
            external_document,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()
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
    wrong = str(int(FORMAT_VERSION) + 1)
    wrong_version["format_version"] = wrong
    cases.append((wrong_version, rf"format_version '{wrong}' is unsupported"))

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
    declarations[0]["acyclic"] = 0
    cases.append((bad_boolean, r"relation_declarations\[0\].acyclic must be a boolean"))

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


@pytest.mark.parametrize(
    ("document", "index"), [(mutable_document, 0), (polyadic_document, 1)]
)
def test_relation_instance_refuses_unknown_field(document: object, index: int) -> None:
    """Both relation carriers refuse fields outside their published shape."""
    assert callable(document)
    value = document()
    relations = cast(list[dict[str, object]], graph_data(value)["relations"])
    relations[-1]["bogus"] = 1
    LAWS.check_refusal(rf"relations\[{index}\] has unknown field 'bogus'", value)


@pytest.mark.parametrize(
    ("document", "index", "required"),
    [(mutable_document, 0, "right"), (polyadic_document, 1, "targets")],
)
def test_relation_instance_refuses_missing_field(
    document: object, index: int, required: str
) -> None:
    """Both relation carriers name a missing required instance field."""
    assert callable(document)
    value = document()
    relations = cast(list[dict[str, object]], graph_data(value)["relations"])
    del relations[-1][required]
    LAWS.check_refusal(rf"relations\[{index}\] is missing field '{required}'", value)
