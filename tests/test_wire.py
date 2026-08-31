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
    MAX_DOCUMENT_BYTES,
    MAX_JSON_DEPTH,
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    Boundary,
    BoundaryRef,
    BoundarySide,
    Consensus,
    Delivery,
    DocumentRef,
    DurableBoundaryRef,
    DurableItemRef,
    DurablePolyadicRef,
    DurableRelationRef,
    Graph,
    GraphValidationError,
    Item,
    ItemRef,
    Layer,
    LayerFact,
    LayerName,
    LayerRead,
    NamespaceDeclaration,
    OrphanedSubject,
    PolyadicInstanceRef,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    QualifiedName,
    RelationDeclarationRef,
    RelationEndpointKind,
    RelationInstance,
    RelationInstanceRef,
    RelationSideDeclaration,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    TierRef,
    XsdType,
    dump_bytes,
    dump_compact,
    dumps,
    loads,
    to_data,
    wire,
)
from tiergraph.schema import Refusal, RefusalStage

NS = "urn:wire-test"
META_NS = "urn:wire-meta"


def test_compact_serializer_has_exact_canonical_spelling() -> None:
    graph = Graph((), (), ())
    assert dump_compact(graph) == '{"format_version":"0.2.0","graph":{}}\n'


def test_deep_json_is_cleanly_refused_before_parser_recursion() -> None:
    """Ten thousand nested arrays produce a typed policy error, not recursion."""
    document = "[" * 10_000 + "0" + "]" * 10_000
    with pytest.raises(
        ValueError, match=f"JSON nesting depth exceeds limit {MAX_JSON_DEPTH}"
    ):
        loads(document)


def test_document_size_budget_discriminates_at_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exact UTF-8 bytes are accepted while one byte over is cleanly refused."""
    document = json.dumps(to_data(Graph((), (), ())))
    monkeypatch.setattr(wire, "MAX_DOCUMENT_BYTES", len(document.encode("utf-8")))
    assert loads(document) == Graph((), (), ())
    with pytest.raises(ValueError, match="document size .*exceeds limit"):
        loads(document + " ")

    with pytest.raises(ValueError, match="document size .*exceeds limit"):
        loads((document + " ").encode("utf-8"))


def test_document_size_counts_multibyte_utf8(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """String documents are bounded by encoded bytes, not code point count."""
    monkeypatch.setattr(wire, "MAX_DOCUMENT_BYTES", 1)
    with pytest.raises(ValueError, match="document size 2 bytes exceeds limit 1"):
        loads("é")


def test_string_encoding_and_parser_recursion_are_cleanly_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Neither string encoding nor parser depth failures escape the codec boundary."""
    with pytest.raises(ValueError, match="encode UTF-8 failed"):
        loads("\ud800")

    def recursive_parser(_document: str, **_kwargs: object) -> object:
        raise RecursionError("parser depth")

    monkeypatch.setattr(json, "loads", recursive_parser)
    with pytest.raises(
        ValueError, match="parse JSON failed: document nesting is too deep"
    ):
        loads("{}")


def test_document_size_default_is_a_public_policy_value() -> None:
    """An input over the shipped size policy is refused without parsing it."""
    assert MAX_DOCUMENT_BYTES == 16 * 1024 * 1024
    with pytest.raises(ValueError, match="document size .*exceeds limit"):
        loads(b" " * (MAX_DOCUMENT_BYTES + 1))


def test_reasonable_nested_json_reaches_normal_typed_validation() -> None:
    """Ordinary nesting is parsed normally rather than rejected by the scanner."""
    document = "[" * 32 + "0" + "]" * 32
    with pytest.raises(ValueError, match="document must be an object"):
        loads(document)


@pytest.mark.parametrize(
    "document, key",
    (
        (
            '{"format_version":"0.2.0","format_version":"0.2.0","graph":{"namespaces":[]}}',
            "format_version",
        ),
        (
            '{"format_version":"0.2.0","graph":{"namespaces":[],"namespaces":[]}}',
            "namespaces",
        ),
    ),
)
def test_duplicate_object_keys_are_refused(document: str, key: str) -> None:
    """Repeated keys are ambiguous at both the envelope and graph levels."""
    with pytest.raises(
        ValueError, match=rf"parse JSON failed: duplicate object key '{key}'"
    ):
        loads(document)


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
        name("marker"), AttributeDomain.BOUNDARY, XsdType.BOOLEAN
    )
    offset = AttributeDeclaration(
        name("offset"), AttributeDomain.BOUNDARY, XsdType.INTEGER
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
    boundary = DurableBoundaryRef(DurableItemRef("bed"), BoundarySide.BEFORE)
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
            Boundary(
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
    """Reverse keyed binary and polyadic collections across graph domains."""
    original = rich_graph()
    polyadic = PolyadicRelationDeclaration(
        name("groups"),
        RelationSideDeclaration(
            (RelationEndpointKind.BOUNDARY, RelationEndpointKind.ITEM),
            (name("notes"), name("placements")),
            1,
        ),
        RelationSideDeclaration(
            (RelationEndpointKind.ITEM,), (name("notes"), name("placements")), 1
        ),
    )
    instance = PolyadicRelationInstance(
        polyadic.name,
        (ItemRef(name("placements"), 0),),
        (ItemRef(name("notes"), 0),),
        "group-1",
    )
    baseline = replace(
        original,
        relation_declarations=(*original.relation_declarations, polyadic),
        polyadic_relations=(instance,),
    )
    extra_boundary = Boundary(
        BoundaryRef(name("placements"), 0),
        tuple(
            AttributeValue(value.name, value.value_type, "0")
            for value in baseline.boundary_values[0].attributes
        ),
    )
    left = Graph(
        baseline.namespaces,
        baseline.tiers,
        baseline.relation_declarations,
        baseline.relations,
        baseline.attribute_declarations,
        (*baseline.boundary_values, extra_boundary),
        baseline.attributes,
        baseline.polyadic_relations,
    )
    supplied_base = rich_graph(reverse_unordered=True)
    reversed_polyadic = PolyadicRelationDeclaration(
        polyadic.name,
        RelationSideDeclaration(
            tuple(reversed(polyadic.sources.endpoint_kinds)),
            tuple(reversed((name("notes"), name("placements")))),
            polyadic.sources.minimum,
            polyadic.sources.maximum,
        ),
        RelationSideDeclaration(
            polyadic.targets.endpoint_kinds,
            tuple(reversed((name("notes"), name("placements")))),
            polyadic.targets.minimum,
            polyadic.targets.maximum,
        ),
    )
    supplied = replace(
        supplied_base,
        relation_declarations=(
            reversed_polyadic,
            *supplied_base.relation_declarations,
        ),
        polyadic_relations=(instance,),
    )
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
        boundary_values=tuple(
            replace(boundary, attributes=tuple(reversed(boundary.attributes)))
            for boundary in reversed((*supplied.boundary_values, extra_boundary))
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
            graph.boundary_values,
            graph.attributes,
        ),
        Graph(
            graph.namespaces,
            (reversed_items, graph.tiers[1]),
            graph.relation_declarations,
            graph.relations,
            graph.attribute_declarations,
            graph.boundary_values,
            graph.attributes,
        ),
    )


def _tier_graph(
    *,
    tier_local_name: str = "tier",
    long_name: str | None = None,
    durable_id: str | None = None,
) -> Graph:
    """Build a minimal namespaced tier graph for focused string coverage."""
    items = () if durable_id is None else (Item(durable_id),)
    tier = Tier(TierDeclaration(name(tier_local_name), long_name or "Tier"), items)
    return Graph((NamespaceDeclaration("w", NS),), (tier,), ())


def _attribute_graph(*, attribute_local_name: str, lexical: str) -> Graph:
    """Build a minimal graph carrying one item attribute value."""
    declaration = AttributeDeclaration(
        name(attribute_local_name), AttributeDomain.ITEM, XsdType.STRING
    )
    tier = Tier(
        TierDeclaration(name("tier"), "Tier"),
        (Item("item", (AttributeValue(declaration.name, XsdType.STRING, lexical),)),),
    )
    return Graph(
        (NamespaceDeclaration("w", NS),),
        (tier,),
        (),
        attribute_declarations=(declaration,),
    )


def read_back_corpus() -> tuple[Graph, ...]:
    """Exercise canonical read-back across every user-controlled string location."""
    return (
        Graph((), (), ()),
        rich_graph(),
        rich_graph(reverse_unordered=True),
        Graph(
            (NamespaceDeclaration("p", "urn:café:😀:e\N{COMBINING ACUTE ACCENT}"),),
            (),
            (),
        ),
        Graph((NamespaceDeclaration("pré😀", NS),), (), ()),
        _tier_graph(long_name='line one\n"line two"'),
        _tier_graph(tier_local_name='quoted"name'),
        _tier_graph(durable_id=r"durable\\identifier"),
        _attribute_graph(
            attribute_local_name="label", lexical="combine e\N{COMBINING ACUTE ACCENT}"
        ),
        _attribute_graph(attribute_local_name="line\nname", lexical="value"),
    )


def refused_corpus() -> tuple[tuple[Graph, str], ...]:
    """Cover every constructible wire string location with a lone surrogate."""
    surrogate = "\ud800"
    return (
        (
            Graph((NamespaceDeclaration("p", surrogate),), (), ()),
            "namespaces[0].namespace",
        ),
        (
            Graph((NamespaceDeclaration(surrogate, NS),), (), ()),
            "namespaces[0].prefix",
        ),
        (_tier_graph(long_name=surrogate), "tiers[0].declaration.long_name"),
        (_tier_graph(tier_local_name=surrogate), "tiers[0].declaration.name"),
        (_tier_graph(durable_id=surrogate), "tiers[0].items[0].durable_id"),
        (
            _attribute_graph(attribute_local_name="label", lexical=surrogate),
            "tiers[0].items[0].attributes[0].lexical",
        ),
        (
            _attribute_graph(attribute_local_name=surrogate, lexical="value"),
            "tiers[0].items[0].attributes[0].name",
        ),
    )


LAWS = WireLawSuite(
    dump_bytes,
    loads,
    rich_graph,
    canonical_variants,
    ordered_variants,
    read_back_corpus,
    refused_corpus,
)


@pytest.mark.parametrize(
    "law",
    [
        LAWS.check_round_trip,
        LAWS.check_presentation_variants_have_equal_bytes,
        LAWS.check_ordered_graphs_have_different_bytes,
        LAWS.check_strict_json,
        LAWS.check_canonical_read_back,
        LAWS.check_writer_refuses_unreadable_text,
    ],
    ids=lambda law: law.__name__,
)
def test_wire_law(law: object) -> None:
    """Run each reusable law against the reference codec."""
    assert callable(law)
    law()


def test_presentation_variant_law_does_not_delegate_its_domain_to_equality() -> None:
    """Order-sensitive derived equality cannot hide a byte-level counterexample."""
    left, right = canonical_variants()
    object.__setattr__(right, "attributes", tuple(reversed(right.attributes)))
    assert left != right

    # This is the former equality-quantified domain: the counterexample is skipped.
    if left == right:  # pragma: no cover - the skipped body is the demonstrated bug
        assert dump_bytes(left) == dump_bytes(right)

    mutant = WireLawSuite(
        dump_bytes,
        loads,
        rich_graph,
        lambda: (left, right),
        ordered_variants,
        read_back_corpus,
        refused_corpus,
    )
    with pytest.raises(AssertionError):
        mutant.check_presentation_variants_have_equal_bytes()


def test_canonical_read_back_corpus_denominator_is_pinned() -> None:
    """Pin the denominator so canonical coverage cannot silently shrink to one."""
    assert len(read_back_corpus()) == 10


def test_no_writer_returns_text_this_reader_would_refuse() -> None:
    """The reader-accepted surrogate document cannot become unreadable output.

    This is the whole defect in one place: the reader accepts this 87-byte
    ASCII document, and every writer used to hand back either a string holding
    a raw lone surrogate or the encoder's own ``UnicodeEncodeError``.  All four
    writers must now make the same decision, name the same field path, and
    raise this reader's own encoding-staged ``Refusal`` rather than leaking an
    encoder exception.
    """
    document = (
        '{"format_version":"0.2.0","graph":'
        '{"namespaces":[{"namespace":"\\ud800","prefix":"p"}]}}'
    )
    assert len(document.encode("ascii")) == 87
    graph = loads(document)
    for writer in (to_data, dumps, dump_compact, dump_bytes):
        with pytest.raises(ValueError) as refusal:
            writer(graph)
        assert type(refusal.value) is Refusal
        assert refusal.value.stage is RefusalStage.ENCODING
        assert "namespaces[0].namespace" in str(refusal.value)


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
        original.boundary_values,
        original.attributes,
        (relation,),
    )
    return cast(dict[str, object], deepcopy(to_data(extended)))


def graph_data(document: dict[str, object]) -> dict[str, object]:
    """Return the graph object from fixture data."""
    return cast(dict[str, object], document["graph"])


def test_empty_and_null_values_are_omitted_and_recovered() -> None:
    """Every omitted empty collection and absent identifier has one decoded value."""
    document = to_data(Graph((), (), ()))
    assert document == {"format_version": FORMAT_VERSION, "graph": {}}
    assert loads(json.dumps(document)) == Graph((), (), ())

    anonymous = Graph(
        (NamespaceDeclaration("w", NS),),
        (Tier(TierDeclaration(name("empty"), "Empty"), (Item(),)),),
        (),
    )
    encoded = json.loads(dump_bytes(anonymous))

    def assert_compact(value: object) -> None:
        assert value is not None
        assert value != []
        if isinstance(value, dict):
            for child in value.values():
                assert_compact(child)
        elif isinstance(value, list):
            for child in value:
                assert_compact(child)

    assert_compact(encoded)
    assert loads(json.dumps(encoded)) == anonymous


def test_qualified_name_colons_split_once_and_prefix_colons_are_refused() -> None:
    """Colon-bearing locals round-trip while the prefix delimiter stays unique."""
    colon_name = name("section:voice:entry")
    graph = Graph(
        (NamespaceDeclaration("w", NS),),
        (Tier(TierDeclaration(colon_name, "Colon local"), (Item(),)),),
        (),
    )
    assert '"name": "w:section:voice:entry"' in dumps(graph)
    assert loads(dumps(graph)) == graph

    with pytest.raises(
        GraphValidationError, match="namespace prefix 'bad:prefix' must not contain"
    ):
        NamespaceDeclaration("bad:prefix", NS)

    # A construction-time refusal does not retire the writer's own guard: a
    # binding that evaded construction must still not reach a document.
    smuggled = Graph((NamespaceDeclaration("w", NS),), (), ())
    object.__setattr__(smuggled.namespaces[0], "prefix", "bad:prefix")
    with pytest.raises(ValueError, match="prefix must not contain ':' in wire format"):
        dump_bytes(smuggled)

    document = to_data(graph)
    graph_object = cast(dict[str, object], document["graph"])
    namespaces = cast(list[dict[str, object]], graph_object["namespaces"])
    namespaces[0]["prefix"] = "bad:prefix"
    with pytest.raises(ValueError, match="prefix must not contain ':'"):
        loads(json.dumps(document))


def test_polyadic_relation_durable_id_round_trips() -> None:
    """The guarded polyadic carrier retains a present durable identifier."""
    decoded = loads(json.dumps(polyadic_document()))
    assert decoded.polyadic_relations[0].durable_id == "group-1"


def test_empty_polyadic_sides_remain_distinguishable_after_omission() -> None:
    """Omitted empty sides still select the polyadic instance object branch."""
    tier_name = name("empty-polyadic-tier")
    declaration = PolyadicRelationDeclaration(
        name("empty-polyadic"),
        RelationSideDeclaration(
            (RelationEndpointKind.ITEM,), (tier_name,), 0, allow_empty=True
        ),
        RelationSideDeclaration(
            (RelationEndpointKind.ITEM,), (tier_name,), 0, allow_empty=True
        ),
    )
    graph = Graph(
        (NamespaceDeclaration("w", NS),),
        (Tier(TierDeclaration(tier_name, "Empty polyadic")),),
        (declaration,),
        polyadic_relations=(PolyadicRelationInstance(declaration.name, (), ()),),
    )
    document = cast(dict[str, object], to_data(graph)["graph"])
    relation = cast(list[dict[str, object]], document["relations"])[0]
    assert "sources" not in relation and "targets" not in relation
    assert loads(dump_bytes(graph)) == graph


def test_relation_side_tier_restriction_preserves_all_three_states() -> None:
    """Absent, explicitly empty, and nonempty tier restrictions remain distinct."""
    tier_name = name("restriction-tier")

    def declaration(
        local_name: str, tiers: tuple[QualifiedName, ...] | None
    ) -> PolyadicRelationDeclaration:
        side = RelationSideDeclaration(
            (RelationEndpointKind.ITEM,), tiers, minimum=0, allow_empty=True
        )
        return PolyadicRelationDeclaration(name(local_name), side, side)

    declarations = (
        declaration("any-tier", None),
        declaration("no-tier", ()),
        declaration("one-tier", (tier_name,)),
    )
    graph = Graph(
        (NamespaceDeclaration("w", NS),),
        (Tier(TierDeclaration(tier_name, "Restriction tier")),),
        declarations,
    )
    document = cast(dict[str, object], to_data(graph)["graph"])
    encoded = cast(list[dict[str, object]], document["relation_declarations"])
    sides = {
        cast(str, entry["name"]): cast(dict[str, object], entry["sources"])
        for entry in encoded
    }
    assert "tiers" not in sides["w:any-tier"]
    assert sides["w:no-tier"]["tiers"] == []
    assert sides["w:one-tier"]["tiers"] == ["w:restriction-tier"]

    decoded = loads(dump_bytes(graph))
    assert decoded == graph
    decoded_tiers = tuple(
        cast(PolyadicRelationDeclaration, item).sources.tiers
        for item in decoded.relation_declarations
    )
    assert decoded_tiers == (None, (), (tier_name,))


def test_read_edit_write_changes_only_declared_value_line() -> None:
    """Editing one value in an externally serialized document changes only its line."""
    external_document = to_data(rich_graph())
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
    tier_anchor = DurableBoundaryRef(name("placements"), BoundarySide.AFTER)
    extended = Graph(
        graph.namespaces,
        graph.tiers,
        graph.relation_declarations,
        graph.relations,
        graph.attribute_declarations,
        (
            *graph.boundary_values,
            Boundary(tier_anchor, graph.boundary_values[0].attributes),
        ),
        graph.attributes,
    )
    decoded = loads(dump_bytes(extended))
    assert isinstance(decoded.relations[0].left, ItemRef)
    item_anchor = cast(DurableBoundaryRef, decoded.relations[0].right)
    assert isinstance(item_anchor.anchor, DurableItemRef)
    outer_anchor = cast(DurableBoundaryRef, decoded.boundary_values[1].reference)
    assert isinstance(outer_anchor.anchor, QualifiedName)
    assert item_anchor != outer_anchor


def test_format_0_2_0_round_trip_uses_boundary_domain_and_durable_item_tag() -> None:
    """REGRESSION: fails on parent with position and no durable endpoint wire arm."""
    graph = rich_graph()
    durable = DurableItemRef("lead")
    relation = RelationInstance(
        graph.relations[0].declaration,
        durable,
        graph.relations[0].right,
    )
    extended = Graph(
        graph.namespaces,
        graph.tiers,
        graph.relation_declarations,
        (relation, *graph.relations[1:]),
        graph.attribute_declarations,
        graph.boundary_values,
        graph.attributes,
    )

    document = json.loads(dumps(extended))
    assert document["format_version"] == "0.2.0"
    declarations = document["graph"]["attribute_declarations"]
    boundary_domains = [
        declaration["domain"]
        for declaration in declarations
        if declaration["name"].endswith((":marker", ":offset"))
    ]
    assert boundary_domains == ["boundary", "boundary"]
    assert document["graph"]["relations"][0]["left"] == {
        "kind": "durable-item",
        "durable_id": "lead",
    }
    assert loads(json.dumps(document)) == extended


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
    boundaries = cast(list[dict[str, object]], graph_data(document)["position_values"])
    reference = cast(dict[str, object], boundaries[0]["reference"])
    anchor = cast(dict[str, object], reference["anchor"])
    anchor["durable_id"] = "absent"
    LAWS.check_refusal("durable boundary anchor item 'absent' was not found", document)


def test_structural_position_and_anonymous_values_round_trip() -> None:
    """Structural boundaries and absent durable ids take their distinct wire branches."""
    graph = rich_graph()
    structural = Boundary(
        BoundaryRef(name("placements"), 2), graph.boundary_values[0].attributes
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
    wrong = "foreign"
    wrong_version["format_version"] = wrong
    cases.append((wrong_version, rf"format_version '{wrong}' is unsupported"))

    missing = mutable_document()
    del missing["format_version"]
    cases.append((missing, "document is missing field 'format_version'"))

    extra = mutable_document()
    extra["machine_version"] = "1"
    cases.append((extra, "document has unknown fields \\['machine_version'\\]"))

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
    boundaries = cast(
        list[dict[str, object]], graph_data(bad_anchor_kind)["position_values"]
    )
    reference = cast(dict[str, object], boundaries[0]["reference"])
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
    LAWS.check_refusal(rf"relations\[{index}\] has unknown fields \['bogus'\]", value)


@pytest.mark.parametrize(
    ("document", "index", "required"), [(mutable_document, 0, "right")]
)
def test_relation_instance_refuses_missing_field(
    document: object, index: int, required: str
) -> None:
    """Both relation carriers name a missing required instance field."""
    assert callable(document)
    value = document()
    relations = cast(list[dict[str, object]], graph_data(value)["relations"])
    del relations[-1][required]
    LAWS.check_refusal(
        rf"relations\[{index}\] is missing fields \['{required}'\]", value
    )


def test_unsupported_version_is_reported_before_the_field_set() -> None:
    """REGRESSION: a foreign version is named as the cause, not a field.

    A document announcing a format this release does not implement is refused
    for its version whatever else it carries, so a consumer holding a newer
    document is told the version rather than whichever unknown field it
    happens to carry.
    """
    for announced in ("7", "5"):
        document = mutable_document()
        document["format_version"] = announced
        document["zz"] = 1
        with pytest.raises(ValueError) as refusal:
            loads(json.dumps(document))
        assert str(refusal.value) == (
            f"format_version {announced!r} is unsupported; expected {FORMAT_VERSION!r}"
        )


def test_every_unknown_document_field_is_named_in_one_refusal() -> None:
    """REGRESSION: one attempt names every unknown field, not the first."""
    document = mutable_document()
    document["aa"] = 1
    document["zz"] = 2
    with pytest.raises(ValueError) as refusal:
        loads(json.dumps(document))
    assert str(refusal.value) == "document has unknown fields ['aa', 'zz']"


def test_missing_and_unknown_fields_are_named_in_one_refusal() -> None:
    """REGRESSION: both directions of the difference reach one message."""
    document = mutable_document()
    del document["graph"]
    document["aa"] = 1
    document["zz"] = 2
    with pytest.raises(ValueError) as refusal:
        loads(json.dumps(document))
    assert str(refusal.value) == (
        "document is missing fields ['graph'] and has unknown fields ['aa', 'zz']"
    )


def test_optional_relation_side_field_is_not_reported_as_missing() -> None:
    """CHARACTERIZATION: an optional field is a difference in neither direction.

    The relation side's `tiers` is declared and not required, so its absence is
    neither missing nor unknown. This pins the `required` argument: passing the
    declared set instead would report `tiers` as missing.
    """
    document = polyadic_document()
    declarations = cast(
        list[dict[str, object]], graph_data(document)["relation_declarations"]
    )
    side = cast(dict[str, object], declarations[1]["sources"])
    side.pop("tiers", None)
    side["zz"] = 1
    with pytest.raises(ValueError) as refusal:
        loads(json.dumps(document))
    assert "tiers" not in str(refusal.value)


def test_missing_format_version_is_not_reported_as_a_field_set_difference() -> None:
    """CHARACTERIZATION: an unannounced version is not a field-set report.

    Until the stamp is read the reader does not know which declared field set
    applies, so it cannot honestly report a difference against this format's
    set. The refusal names the stamp alone even though `graph` is also absent.
    """
    with pytest.raises(ValueError) as refusal:
        loads("{}")
    assert str(refusal.value) == "document is missing field 'format_version'"


def test_unknown_field_values_never_reach_the_refusal() -> None:
    """CHARACTERIZATION: a refusal names what was unknown, never what it held.

    An unknown member carries arbitrary caller structure, so interpolating its
    value into a diagnostic would make the refusal path a log-injection
    surface.
    """
    document = mutable_document()
    document["zz"] = {"deep": [1, 2, {"payload": "SECRET"}]}
    with pytest.raises(ValueError) as refusal:
        loads(json.dumps(document))
    message = str(refusal.value)
    for absent in ("deep", "payload", "SECRET"):
        assert absent not in message


def test_unknown_field_probe_family_denominator_is_pinned() -> None:
    """CHARACTERIZATION: pin the family this workstream's claim runs over.

    `undeclared_drifts` reports nothing when a mutation family stops being
    generated, so the family's disappearance is invisible to the conformance
    file. The denominator is the only thing that can notice it.
    """
    from tests.conformance.schema_codec import conformance_probes
    from tests.test_schema_codec_conformance import _seeds
    from tiergraph.schema import DOCUMENT

    probes = conformance_probes(_seeds(), DOCUMENT)
    unknown = [probe for probe in probes if probe.mutation == "unknown-field"]
    assert len(unknown) == 148


LAYER_NS = "urn:layer-test"
VOCAB = "urn:layer-gloss"


def q(local: str) -> QualifiedName:
    return QualifiedName(
        LAYER_NS if local in {"words", "member", "links", "poly", "token"} else VOCAB,
        local,
    )


WORDS = q("words")
MEMBER = q("member")
LINKS = q("links")
TOKEN = q("token")
NAMES = {domain: q(domain.value) for domain in AttributeDomain}
SOURCE_A = LayerName(VOCAB, "reader-a")
SOURCE_B = LayerName(VOCAB, "reader-b")


def value(domain: AttributeDomain, lexical: str) -> AttributeValue:
    return AttributeValue(NAMES[domain], XsdType.STRING, lexical)


def graph_with_layers(*layers: Layer, seal: int | None = None) -> Graph:
    declarations = tuple(
        AttributeDeclaration(NAMES[domain], domain, XsdType.STRING)
        for domain in AttributeDomain
    )
    relation_declarations = (
        SimpleRelationDeclaration(MEMBER, WORDS, TOKEN),
        BipartiteRelationDeclaration(
            LINKS, TOKEN, TOKEN, RelationEndpointKind.ITEM, RelationEndpointKind.ITEM
        ),
    )
    return (
        Graph(
            (NamespaceDeclaration("o", LAYER_NS), NamespaceDeclaration("g", VOCAB)),
            (Tier(TierDeclaration(WORDS, "Words"), (Item("w0"), Item("w1"))),),
            relation_declarations,
            (RelationInstance(LINKS, ItemRef(WORDS, 0), ItemRef(WORDS, 1), "r0"),),
            declarations,
            seals=() if seal is None else (),
            layers=layers,
        ).seal(WORDS, seal)
        if seal is not None
        else Graph(
            (NamespaceDeclaration("o", LAYER_NS), NamespaceDeclaration("g", VOCAB)),
            (Tier(TierDeclaration(WORDS, "Words"), (Item("w0"), Item("w1"))),),
            relation_declarations,
            (RelationInstance(LINKS, ItemRef(WORDS, 0), ItemRef(WORDS, 1), "r0"),),
            declarations,
            layers=layers,
        )
    )


def six_domain_layer(name: LayerName = SOURCE_A) -> Layer:
    return Layer(
        name,
        (
            LayerFact(ItemRef(WORDS, 0), value(AttributeDomain.ITEM, "item")),
            LayerFact(TierRef(WORDS), value(AttributeDomain.TIER, "tier")),
            LayerFact(
                RelationDeclarationRef(LINKS),
                value(AttributeDomain.RELATION_DECLARATION, "declaration"),
            ),
            LayerFact(
                RelationInstanceRef(0),
                value(AttributeDomain.RELATION_INSTANCE, "instance"),
            ),
            LayerFact(
                BoundaryRef(WORDS, 1), value(AttributeDomain.BOUNDARY, "boundary")
            ),
            LayerFact(DocumentRef(), value(AttributeDomain.DOCUMENT, "document")),
        ),
    )


# REGRESSION: predicted FAIL against the parent because the layer API is absent.
def test_two_sources_and_first_last_all_are_distinct() -> None:
    first = Layer(
        SOURCE_A, (LayerFact(ItemRef(WORDS, 0), value(AttributeDomain.ITEM, "a")),)
    )
    last = Layer(
        SOURCE_B, (LayerFact(ItemRef(WORDS, 0), value(AttributeDomain.ITEM, "b")),)
    )
    graph = graph_with_layers(first, last)
    subject = ItemRef(WORDS, 0)
    deliveries = {read: Delivery((SOURCE_A, SOURCE_B), read) for read in LayerRead}
    answers = {
        read: tuple(
            item.lexical
            for item in graph.layer_values(
                subject, NAMES[AttributeDomain.ITEM], delivery
            )
        )
        for read, delivery in deliveries.items()
    }
    assert answers == {
        LayerRead.FIRST: ("a",),
        LayerRead.LAST: ("b",),
        LayerRead.ALL: ("a", "b"),
    }
    assert len(graph.layers) == 2
    with pytest.raises(GraphValidationError, match="duplicate attribute value"):
        Graph(
            graph.namespaces,
            (
                Tier(
                    TierDeclaration(WORDS, "Words"),
                    (
                        Item(
                            "w0",
                            (
                                value(AttributeDomain.ITEM, "a"),
                                value(AttributeDomain.ITEM, "b"),
                            ),
                        ),
                    ),
                ),
            ),
            graph.relation_declarations,
            attribute_declarations=graph.attribute_declarations,
        )


# REGRESSION: predicted FAIL against the parent because the layer API is absent.
def test_all_six_domains_read_back() -> None:
    layer = six_domain_layer()
    graph = graph_with_layers(layer)
    delivery = Delivery((SOURCE_A,), LayerRead.ALL)
    for fact in layer.facts:
        assert graph.layer_values(fact.subject, fact.value.name, delivery) == (
            fact.value,
        )


# REGRESSION: predicted FAIL against the parent because consensus is absent.
def test_consensus_and_disagreements() -> None:
    subject = ItemRef(WORDS, 0)
    left = Layer(SOURCE_A, (LayerFact(subject, value(AttributeDomain.ITEM, "same")),))
    same = Layer(SOURCE_B, (LayerFact(subject, value(AttributeDomain.ITEM, "same")),))
    delivery = Delivery((SOURCE_A, SOURCE_B), LayerRead.ALL)
    agreed = graph_with_layers(left, same)
    assert agreed.consensus(subject, NAMES[AttributeDomain.ITEM], delivery).agreed
    assert agreed.disagreements(delivery) == ()
    different = graph_with_layers(
        left,
        Layer(SOURCE_B, (LayerFact(subject, value(AttributeDomain.ITEM, "other")),)),
    )
    reports = different.disagreements(delivery)
    assert reports == (
        Consensus(
            subject,
            NAMES[AttributeDomain.ITEM],
            (
                (SOURCE_A, value(AttributeDomain.ITEM, "same")),
                (SOURCE_B, value(AttributeDomain.ITEM, "other")),
            ),
            False,
        ),
    )


# REGRESSION: predicted FAIL against the parent because layers cannot be orphaned.
def test_removal_orphans_fact_and_flatten_refuses() -> None:
    layer = Layer(
        SOURCE_A, (LayerFact(ItemRef(WORDS, 0), value(AttributeDomain.ITEM, "gone")),)
    )
    graph = graph_with_layers(layer)
    # Remove the relation first: structural references still veto their target.
    with pytest.raises(GraphValidationError, match="still references"):
        graph.remove_item(ItemRef(WORDS, 0))
    edited = graph.remove_relation(0).remove_item(ItemRef(WORDS, 0))
    orphan = edited.layers[0].facts[0].subject
    assert isinstance(orphan, OrphanedSubject)
    assert orphan.carrier == WORDS
    assert orphan.was == ItemRef(WORDS, 0)
    assert (
        edited.layer_values(
            ItemRef(WORDS, 0),
            NAMES[AttributeDomain.ITEM],
            Delivery((SOURCE_A,), LayerRead.ALL),
        )
        == ()
    )
    with pytest.raises(
        GraphValidationError, match="delivery cannot be flattened.*orphaned from"
    ):
        edited.flatten(Delivery((SOURCE_A,), LayerRead.FIRST))


def test_durable_item_layer_subject_orphans_and_invalid_subject_refuses_typed() -> None:
    """REGRESSION (F5): durable layer identity does not veto base removal."""
    layer = Layer(
        SOURCE_A,
        (LayerFact(DurableItemRef("w0"), value(AttributeDomain.ITEM, "gone")),),
    )
    edited = (
        graph_with_layers(layer).remove_relation(0).remove_item(DurableItemRef("w0"))
    )
    assert edited.layers[0].facts[0].subject == OrphanedSubject(
        WORDS, ItemRef(WORDS, 0)
    )

    invalid = Layer(
        SOURCE_A,
        (LayerFact(DurableItemRef("missing"), value(AttributeDomain.ITEM, "invalid")),),
    )
    with pytest.raises(GraphValidationError, match="unknown durable item id 'missing'"):
        graph_with_layers(invalid)


# REGRESSION: predicted FAIL against the parent because layers and seals cannot coexist there.
def test_seal_vetoes_even_where_layer_would_allow_edit() -> None:
    layer = Layer(
        SOURCE_A, (LayerFact(ItemRef(WORDS, 0), value(AttributeDomain.ITEM, "x")),)
    )
    graph = graph_with_layers(layer, seal=1)
    with pytest.raises(GraphValidationError, match="seal on that tier"):
        graph.insert_item(WORDS, 0, Item("new"))


# CHARACTERIZATION plus REGRESSION: omission preserves old bytes; populated layers round-trip.
def test_layerless_omission_and_layer_round_trip() -> None:
    layerless = graph_with_layers()
    assert "layers" not in cast(dict[str, object], to_data(layerless)["graph"])
    layered = graph_with_layers(six_domain_layer())
    assert loads(dumps(layered)) == layered
    assert json.loads(dumps(layered))["graph"]["layers"]


# REGRESSION: validation failures remain attributed to layer values and subjects.
def test_layer_validation_refusals() -> None:
    graph = graph_with_layers()
    item = ItemRef(WORDS, 0)
    good = value(AttributeDomain.ITEM, "x")
    cases = (
        (
            Layer(
                LayerName(VOCAB, "source"),
                (LayerFact(item, good), LayerFact(item, good)),
            ),
            "states.*twice",
        ),
        (
            Layer(LayerName(LAYER_NS, "source"), (LayerFact(item, good),)),
            "named vocabulary",
        ),
        (
            Layer(
                SOURCE_A,
                (
                    LayerFact(
                        item,
                        AttributeValue(
                            QualifiedName(VOCAB, "missing"), XsdType.STRING, "x"
                        ),
                    ),
                ),
            ),
            "undeclared",
        ),
        (
            Layer(
                SOURCE_A,
                (
                    LayerFact(
                        item,
                        AttributeValue(
                            NAMES[AttributeDomain.ITEM], XsdType.BOOLEAN, "true"
                        ),
                    ),
                ),
            ),
            "requires string",
        ),
        (
            Layer(SOURCE_A, (LayerFact(TierRef(WORDS), good),)),
            "declared for the item domain",
        ),
        (
            Layer(
                SOURCE_A,
                (
                    LayerFact(
                        TierRef(QualifiedName(LAYER_NS, "missing")),
                        value(AttributeDomain.TIER, "x"),
                    ),
                ),
            ),
            "undeclared tier",
        ),
        (
            Layer(
                SOURCE_A,
                (
                    LayerFact(
                        RelationDeclarationRef(QualifiedName(LAYER_NS, "missing")),
                        value(AttributeDomain.RELATION_DECLARATION, "x"),
                    ),
                ),
            ),
            "undeclared relation",
        ),
        (
            Layer(
                SOURCE_A,
                (
                    LayerFact(
                        RelationInstanceRef(9),
                        value(AttributeDomain.RELATION_INSTANCE, "x"),
                    ),
                ),
            ),
            "relation index 9",
        ),
        (
            Layer(
                SOURCE_A,
                (
                    LayerFact(
                        PolyadicInstanceRef(0),
                        value(AttributeDomain.RELATION_INSTANCE, "x"),
                    ),
                ),
            ),
            "polyadic relation index 0",
        ),
        (
            Layer(
                SOURCE_A,
                (
                    LayerFact(
                        DurableRelationRef("missing"),
                        value(AttributeDomain.RELATION_INSTANCE, "x"),
                    ),
                ),
            ),
            "unknown durable relation",
        ),
        (
            Layer(
                SOURCE_A,
                (
                    LayerFact(
                        DurablePolyadicRef("missing"),
                        value(AttributeDomain.RELATION_INSTANCE, "x"),
                    ),
                ),
            ),
            "unknown durable polyadic",
        ),
    )
    for layer, message in cases:
        with pytest.raises(GraphValidationError, match=message):
            replace(graph, layers=(layer,))


# REGRESSION: delivery errors and flatten ambiguity are explicit refusals.
def test_delivery_and_flatten_refusals_and_promotion() -> None:
    fact = LayerFact(ItemRef(WORDS, 0), value(AttributeDomain.ITEM, "a"))
    graph = graph_with_layers(Layer(SOURCE_A, (fact,)))
    with pytest.raises(GraphValidationError, match="names no layers"):
        graph.layer_values(fact.subject, fact.value.name, Delivery((), LayerRead.ALL))
    with pytest.raises(GraphValidationError, match="twice"):
        graph.layer_values(
            fact.subject, fact.value.name, Delivery((SOURCE_A, SOURCE_A), LayerRead.ALL)
        )
    with pytest.raises(GraphValidationError, match="does not hold"):
        graph.layer_values(
            fact.subject, fact.value.name, Delivery((SOURCE_B,), LayerRead.ALL)
        )
    other = Layer(
        SOURCE_B, (LayerFact(fact.subject, value(AttributeDomain.ITEM, "b")),)
    )
    with pytest.raises(GraphValidationError, match="both state"):
        graph_with_layers(graph.layers[0], other).flatten(
            Delivery((SOURCE_A, SOURCE_B), LayerRead.ALL)
        )
    flattened = graph.flatten(Delivery((SOURCE_A,), LayerRead.FIRST))
    assert flattened.layers == ()
    assert flattened.tiers[0].items[0].attributes == (fact.value,)
    assert graph.promotion(WORDS)
    assert not replace(
        graph,
        tiers=(Tier(TierDeclaration(WORDS, "Words"), (Item(), Item("w1"))),),
    ).promotion(WORDS)
    with pytest.raises(ValueError, match="undeclared tier"):
        graph.promotion(QualifiedName(LAYER_NS, "missing"))


# REGRESSION: durable subjects and relation orphans retain every wire arm.
def test_durable_subjects_and_relation_orphan_round_trip() -> None:
    subjects = (
        DurableItemRef("w0"),
        DurableBoundaryRef(DurableItemRef("w1"), BoundarySide.BEFORE),
        DurableRelationRef("r0"),
    )
    facts = (
        LayerFact(subjects[0], value(AttributeDomain.ITEM, "item")),
        LayerFact(subjects[1], value(AttributeDomain.BOUNDARY, "boundary")),
        LayerFact(subjects[2], value(AttributeDomain.RELATION_INSTANCE, "relation")),
    )
    graph = graph_with_layers(Layer(SOURCE_A, facts))
    assert loads(dumps(graph)) == graph
    indexed = replace(
        graph,
        layers=(Layer(SOURCE_A, (LayerFact(RelationInstanceRef(0), facts[2].value),)),),
    )
    orphaned = indexed.remove_relation(0)
    orphan = orphaned.layers[0].facts[0].subject
    assert isinstance(orphan, OrphanedSubject)
    assert loads(dumps(orphaned)) == orphaned
    assert (
        orphaned.layer_values(
            orphan, facts[2].value.name, Delivery((SOURCE_A,), LayerRead.ALL)
        )
        == ()
    )


# REGRESSION: flatten reaches every non-item edit target and polyadic subjects.
def test_flatten_six_domains_and_polyadic_subjects() -> None:
    graph = graph_with_layers(six_domain_layer())
    flattened = graph.flatten(Delivery((SOURCE_A,), LayerRead.FIRST))
    assert flattened.attributes[0].lexical == "document"
    assert flattened.tiers[0].attributes[0].lexical == "tier"
    assert flattened.relations[0].attributes[0].lexical == "instance"

    side = RelationSideDeclaration((RelationEndpointKind.ITEM,), (WORDS,))
    poly_name = q("poly")
    declaration = PolyadicRelationDeclaration(poly_name, side, side)
    instance = PolyadicRelationInstance(
        poly_name, (ItemRef(WORDS, 0),), (ItemRef(WORDS, 1),), "p0"
    )
    base = graph_with_layers()
    poly = replace(
        base,
        relation_declarations=(*base.relation_declarations, declaration),
        polyadic_relations=(instance,),
        layers=(
            Layer(
                SOURCE_A,
                (
                    LayerFact(
                        PolyadicInstanceRef(0),
                        value(AttributeDomain.RELATION_INSTANCE, "p"),
                    ),
                    LayerFact(
                        DurablePolyadicRef("p0"),
                        AttributeValue(
                            QualifiedName(VOCAB, "other-instance"), XsdType.STRING, "d"
                        ),
                    ),
                ),
            ),
        ),
        attribute_declarations=(
            *base.attribute_declarations,
            AttributeDeclaration(
                QualifiedName(VOCAB, "other-instance"),
                AttributeDomain.RELATION_INSTANCE,
                XsdType.STRING,
            ),
        ),
    )
    assert loads(dumps(poly)) == poly
    assert (
        poly.flatten(Delivery((SOURCE_A,), LayerRead.FIRST))
        .polyadic_relations[0]
        .attributes
    )
    departed = poly.remove_relation("p0")
    assert all(
        isinstance(fact.subject, OrphanedSubject) for fact in departed.layers[0].facts
    )
    no_id_instance = replace(instance, durable_id=None)
    no_id = replace(
        poly,
        polyadic_relations=(no_id_instance,),
        layers=(
            Layer(
                SOURCE_A,
                (
                    LayerFact(
                        PolyadicInstanceRef(0),
                        value(AttributeDomain.RELATION_INSTANCE, "p"),
                    ),
                ),
            ),
        ),
    )
    with pytest.raises(GraphValidationError, match="without durable relation identity"):
        no_id.flatten(Delivery((SOURCE_A,), LayerRead.FIRST))


# REGRESSION: the shared seal-carrier fragment rejects additions in layer subjects.
def test_layer_orphan_carrier_refuses_unknown_field() -> None:
    orphan = OrphanedSubject(WORDS, ItemRef(WORDS, 0))
    graph = graph_with_layers(
        Layer(SOURCE_A, (LayerFact(orphan, value(AttributeDomain.ITEM, "old")),))
    )
    document = json.loads(dumps(graph))
    carrier = document["graph"]["layers"][0]["facts"][0]["subject"]["carrier"]
    carrier["bogus"] = 1

    with pytest.raises(Refusal) as refusal:
        loads(json.dumps(document))
    assert str(refusal.value) == (
        "layers[0].facts[0].subject.carrier has unknown fields ['bogus']"
    )


# REGRESSION: orphan boundary wire and discriminator paths are explicit.
def test_boundary_orphan_wire_and_unknown_layer_subject() -> None:
    orphan = OrphanedSubject(WORDS, BoundaryRef(WORDS, 1))
    graph = graph_with_layers(
        Layer(SOURCE_A, (LayerFact(orphan, value(AttributeDomain.BOUNDARY, "old")),))
    )
    assert loads(dumps(graph)) == graph
    document = json.loads(dumps(graph_with_layers(six_domain_layer())))
    document["graph"]["layers"][0]["facts"][0]["subject"] = {"kind": "unknown"}
    with pytest.raises(Refusal, match="unsupported"):
        loads(json.dumps(document))
    live = graph_with_layers(
        Layer(
            SOURCE_A,
            (
                LayerFact(BoundaryRef(WORDS, 0), value(AttributeDomain.BOUNDARY, "a")),
                LayerFact(
                    BoundaryRef(WORDS, 1),
                    value(AttributeDomain.BOUNDARY, "b"),
                ),
            ),
        )
    )
    restructured = live.remove_relation(0).remove_item(ItemRef(WORDS, 0))
    assert any(
        isinstance(fact.subject, OrphanedSubject)
        for fact in restructured.layers[0].facts
    )
