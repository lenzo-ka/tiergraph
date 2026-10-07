"""Construction replay and portable patch round trips."""

from __future__ import annotations

import json
import random
from collections.abc import Callable
from dataclasses import replace
from typing import cast

import pytest

import tiergraph.edit as edit_module
import tiergraph.machine as machine
import tiergraph.machine_codec as machine_codec
import tiergraph.patch as patch_codec
from tests import test_wire as wire_fixtures
from tests.test_edit_primitives import fixture
from tests.test_journal import OPERATIONS, _case
from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    BoundaryRef,
    BoundarySide,
    DocumentRef,
    DurableBoundaryRef,
    DurableItemRef,
    DurablePolyadicRef,
    DurableRelationRef,
    EditAnnotations,
    EquivalenceView,
    Graph,
    GraphCarrier,
    Item,
    ItemRef,
    Journal,
    JsonAttributeValue,
    LayerFact,
    LayerName,
    NamespaceDeclaration,
    OrphanedSubject,
    Patch,
    PolyadicInstanceRef,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    Program,
    QualifiedName,
    Refusal,
    RefusalStage,
    RelationDeclarationRef,
    RelationEndpointKind,
    RelationInstance,
    RelationInstanceRef,
    RelationSideDeclaration,
    Seal,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    TierRef,
    XsdType,
    apply_patch,
    compose_patches,
    dumps,
    equivalent,
    fingerprint,
    graph_to_program,
    invert_patch,
    loads,
    patch_dumps,
    patch_loads,
    program_dumps,
    program_loads,
)
from tiergraph.core import JsonValue
from tiergraph.machine import DeltaOpcode


def _transition() -> tuple[Graph, Graph, Patch]:
    case = fixture("speech")
    journal = Journal(
        author="editor",
        reason="test",
        tool="pytest",
        timestamp="2026-10-07T00:00:00Z",
        fields={"ticket": 6},
    )
    editor = case.graph.edit(journal=journal)
    with journal.annotate(stage="review", confidence=0.9, iteration=2):
        editor.insert_item(case.spare, 1, Item("inserted"))
    target = editor.freeze()
    return case.graph, target, journal.to_patch()


@pytest.mark.parametrize("domain", ["speech", "text", "music"])
def test_graph_to_program_replays_domain_fixtures_exactly(domain: str) -> None:
    graph = fixture(domain).graph
    program = graph_to_program(graph)

    assert equivalent(program.unroll().graph, graph, EquivalenceView.EXACT)
    assert program_loads(program_dumps(program)).unroll().graph == graph


def test_graph_to_program_replays_every_reference_and_zero_seal() -> None:
    graph = wire_fixtures.every_reference_variant_graph()
    graph = replace(
        graph, seals=(*graph.seals, Seal(GraphCarrier.POLYADIC_RELATIONS, 0))
    )
    loaded = loads(dumps(graph))

    replayed = program_loads(program_dumps(graph_to_program(loaded))).unroll().graph

    assert equivalent(replayed, loaded, EquivalenceView.EXACT)


def test_graph_to_program_replays_seeded_random_graphs() -> None:
    randomizer = random.Random(6)
    namespace = "urn:random-replay"
    tier_name = QualifiedName(namespace, "events")
    for _ in range(40):
        items = tuple(
            Item(f"id-{index}" if randomizer.randrange(2) else None)
            for index in range(randomizer.randrange(12))
        )
        graph = Graph(
            (NamespaceDeclaration("r", namespace),),
            (Tier(TierDeclaration(tier_name, "Events"), items),),
            (),
            seals=(Seal(tier_name, randomizer.randrange(len(items) + 1)),),
        )
        assert graph_to_program(graph).unroll().graph == graph


def test_patch_codec_apply_and_inverse_round_trip() -> None:
    base, target, patch = _transition()
    encoded = patch_dumps(patch)
    decoded = patch_loads(encoded)

    assert encoded.endswith("\n")
    assert patch_dumps(decoded) == encoded
    assert apply_patch(decoded, base) == target
    assert decoded.apply(base) == target
    assert apply_patch(invert_patch(decoded), target) == base
    assert decoded.invert().apply(target) == base
    assert decoded.operations[0].annotations.stage == "review"
    assert decoded.annotations.author == "editor"
    assert decoded.operations[0].opcode.to_data()["opcode"] == "insert_item"
    assert decoded.operations[0].inverse.to_data()["opcode"] == "remove_item"


def test_patch_records_change_sized_executable_deltas() -> None:
    namespace = "urn:patch-size"
    tier = QualifiedName(namespace, "items")
    base = Graph(
        (NamespaceDeclaration("p", namespace),),
        (
            Tier(
                TierDeclaration(tier, "Items"),
                tuple(Item(f"i-{i}") for i in range(1000)),
            ),
        ),
        (),
    )
    journal = Journal()
    editor = base.edit(journal=journal)
    editor.insert_item(tier, 500, Item("inserted"))
    target = editor.freeze()

    encoded = patch_dumps(journal.to_patch())
    operation = json.loads(encoded.splitlines()[1])

    assert "graph" not in operation
    assert "graph" not in operation["inverse"]
    assert len(encoded.encode()) < 10_000
    assert patch_loads(encoded).apply(base) == target


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ({"same": [1]}, {"same": [1]}),
        ({"drop": 1, "change": 2}, {"add": 3, "change": 4}),
        ([1, 2], [2]),
        ([1, 2], [1, 3, 2]),
        ([1, 2, 3], [2, 3, 1]),
        ([{"value": 1}], [{"value": 2}]),
        ([1], [1, 2]),
        ([1, 2], [1]),
    ],
)
def test_guarded_data_edits_apply_serialize_and_reverse(
    before: object, after: object
) -> None:
    """Every delta shape changes only its guarded location and reverses."""
    source = json.loads(json.dumps(before))
    target = json.loads(json.dumps(after))
    edits = machine._data_edits(source, target)
    decoded = tuple(
        machine._decode_data_edit(edit.to_data(), f"edit[{index}]")
        for index, edit in enumerate(edits)
    )

    for edit in decoded:
        edit.apply(source)
    assert source == target
    assert type(source) is type(target)

    for edit in reversed(decoded):
        edit.reverse().apply(source)
    assert source == before
    assert type(source) is type(before)


def test_guarded_data_edit_refusals_are_staged() -> None:
    """Malformed paths, stale guards, and wrong container kinds refuse."""
    with pytest.raises(Refusal, match="path must be an array"):
        machine._decode_data_path({}, "path")
    for part in (True, -1, 1.5):
        with pytest.raises(Refusal, match="string or nonnegative integer"):
            machine._decode_data_path([part], "path")
    with pytest.raises(Refusal, match="does not exist"):
        machine._at_data_path({}, ("missing",))
    with pytest.raises(Refusal, match="does not exist"):
        machine._at_data_path([], (0,))
    assert machine._at_data_path({"outer": {"value": 1}}, ("outer", "value")) == 1
    with pytest.raises(Refusal, match="cannot replace the document root"):
        machine._data_edits(1, 2)

    replace_value = machine._ReplaceData(("value",), 1, 2)
    replace_value.apply({"value": 1})
    replace_index = machine._ReplaceData((0,), 1, 2)
    replace_index.apply([1])
    for stale_document in ({"value": 0}, [0]):
        edit = replace_value if isinstance(stale_document, dict) else replace_index
        with pytest.raises(Refusal, match="old value mismatch"):
            edit.apply(cast(JsonValue, stale_document))
    with pytest.raises(Refusal, match="document root"):
        machine._ReplaceData((), 1, 2).apply(1)
    with pytest.raises(Refusal, match="replacement path is absent"):
        machine._ReplaceData(("missing",), 1, 2).apply({})

    add_member = machine._SetMember((), "value", False, None, True, 1)
    member_document = cast(JsonValue, {})
    add_member.apply(member_document)
    machine._SetMember((), "value", True, 1, False, None).apply(member_document)
    with pytest.raises(Refusal, match="not an object"):
        add_member.apply([])
    with pytest.raises(Refusal, match="old member mismatch"):
        add_member.apply({"value": 1})
    with pytest.raises(Refusal, match="old member mismatch"):
        machine._SetMember((), "value", True, 2, False, None).apply({"value": 1})

    splice = machine._SpliceData((), 0, (1,), (2,))
    splice.apply([1])
    with pytest.raises(Refusal, match="not an array"):
        splice.apply({})
    for stale in (
        machine._SpliceData((), -1, (), ()),
        machine._SpliceData((), 2, (), ()),
        machine._SpliceData((), 0, (2,), ()),
    ):
        with pytest.raises(Refusal, match="old array slice mismatch"):
            stale.apply([1])

    with pytest.raises(Refusal, match="changes must be an array"):
        machine._decode_data_edits({}, "changes")
    with pytest.raises(Refusal, match="must be a data edit object"):
        machine._decode_data_edit(None, "change")
    with pytest.raises(Refusal, match="must be arrays"):
        machine._decode_data_edit(
            {"kind": "splice", "path": [], "index": 0, "before": {}, "after": []},
            "change",
        )
    with pytest.raises(Refusal, match="kind 'unknown' is unknown"):
        machine._decode_data_edit({"kind": "unknown"}, "change")


def test_edit_argument_codec_covers_the_public_edit_value_vocabulary() -> None:
    """Every typed edit argument keeps its type and value through JSON data."""
    namespace = "urn:patch-arguments"
    tier = QualifiedName(namespace, "tier")
    item_type = QualifiedName(namespace, "item-type")
    relation = QualifiedName(namespace, "relation")
    attribute = QualifiedName(namespace, "attribute")
    side = RelationSideDeclaration((RelationEndpointKind.ITEM,), (tier,))
    values = (
        None,
        True,
        3,
        "text",
        (Item("nested"),),
        tier,
        NamespaceDeclaration("p", namespace),
        TierDeclaration(tier, "Tier"),
        AttributeDeclaration(attribute, AttributeDomain.ITEM, XsdType.STRING),
        SimpleRelationDeclaration(relation, tier, item_type),
        BipartiteRelationDeclaration(relation, item_type, item_type),
        PolyadicRelationDeclaration(relation, side, side),
        Item("item"),
        AttributeValue(attribute, XsdType.STRING, "value"),
        JsonAttributeValue(attribute, {"value": 1}),
        RelationInstance(relation, ItemRef(tier, 0), DurableItemRef("right")),
        PolyadicRelationInstance(
            relation, (ItemRef(tier, 0),), (DurableItemRef("right"),)
        ),
        LayerName(namespace, "manual"),
        LayerFact(ItemRef(tier, 0), AttributeValue(attribute, XsdType.STRING, "fact")),
        GraphCarrier.RELATIONS,
        ItemRef(tier, 0),
        DurableItemRef("item"),
        BoundaryRef(tier, 0),
        DurableBoundaryRef(tier, BoundarySide.BEFORE),
        RelationInstanceRef(0),
        PolyadicInstanceRef(0),
        DurableRelationRef("relation"),
        DurablePolyadicRef("polyadic"),
        DocumentRef(),
        TierRef(tier),
        RelationDeclarationRef(relation),
        OrphanedSubject(GraphCarrier.RELATIONS, 0),
    )

    for index, value in enumerate(values):
        encoded = machine._argument_data(value)
        decoded = machine._decode_edit_argument(encoded, f"argument[{index}]")
        assert decoded == value
        assert type(decoded) is type(value)

    with pytest.raises(TypeError, match="cannot encode edit argument"):
        machine._argument_data(object())


def test_edit_call_and_argument_decoder_refusals_are_staged() -> None:
    """The generic edit-call envelope rejects malformed and unknown values."""
    with pytest.raises(Refusal, match="calls must be an array"):
        machine._decode_edit_calls({}, "calls")
    with pytest.raises(Refusal, match=r"calls\[0\] must be an object"):
        machine._decode_edit_calls([None], "calls")
    with pytest.raises(Refusal, match="arguments must be an array"):
        machine._decode_edit_calls([{"method": "add_layer", "arguments": {}}], "calls")
    with pytest.raises(Refusal, match="must be an argument object"):
        machine._decode_edit_argument(None, "argument")
    with pytest.raises(Refusal, match="must be scalar"):
        machine._decode_edit_argument({"kind": "scalar", "value": 1.5}, "argument")
    with pytest.raises(Refusal, match="items must be an array"):
        machine._decode_edit_argument({"kind": "tuple", "items": {}}, "argument")
    with pytest.raises(Refusal, match="kind 'unknown' is unknown"):
        machine._decode_edit_argument({"kind": "unknown", "value": None}, "argument")
    with pytest.raises(Refusal, match="kind 'durable-item-ref' is unknown"):
        machine._decode_edit_argument(
            {
                "kind": "durable-item-ref",
                "value": DurableBoundaryRef(
                    QualifiedName("urn:patch", "tier"), BoundarySide.BEFORE
                ).to_data(),
            },
            "argument",
        )
    with pytest.raises(Refusal, match="kind 'durable-boundary-ref' is unknown"):
        machine._decode_edit_argument(
            {
                "kind": "durable-boundary-ref",
                "value": DurableItemRef("item").to_data(),
            },
            "argument",
        )

    with pytest.raises(Refusal, match="edit call 'unknown' is unknown"):
        machine.DeltaOpcode("delta", (machine._EditCall("unknown", ()),), ())
    with pytest.raises(Refusal, match="cannot execute call 'add_layer'"):
        machine.DeltaOpcode("move_item", (machine._EditCall("add_layer", ()),), ())


def test_unattached_journal_has_no_patch_base() -> None:
    with pytest.raises(ValueError, match="journal is not attached"):
        Journal().to_patch()


def test_patch_delta_fallback_replays_a_record_without_call_metadata() -> None:
    """Older journal records still export guarded executable deltas."""
    case = fixture("speech")
    journal = Journal()
    editor = case.graph.edit(journal=journal)
    editor.insert_item(case.spare, 1, Item("inserted"))
    target = editor.freeze()
    object.__setattr__(journal.records[0], "_patch_operations", None)

    patch = journal.to_patch()
    opcode = cast(DeltaOpcode, patch.operations[0].opcode)
    inverse = cast(DeltaOpcode, patch.operations[0].inverse)

    assert opcode.operation == "delta"
    assert inverse.operation == "delta"
    assert patch.apply(case.graph) == target
    assert patch.invert().apply(target) == case.graph


def test_delta_opcode_requires_calls_and_applies_residual_changes() -> None:
    """Named edits need calls, while a generic delta executes guarded changes."""
    case = fixture("speech")
    editor = case.graph.edit()
    editor.insert_item(case.spare, 1, Item("inserted"))
    target = editor.freeze()

    with pytest.raises(ValueError, match="requires an executable call"):
        DeltaOpcode.between("insert_item", case.graph, target)

    delta = DeltaOpcode.between("delta", case.graph, target)
    assert delta.apply(case.graph) == target


def test_attribute_lookup_covers_relation_carriers_and_absent_domains() -> None:
    """Patch inverses find prior values on both relation instance carriers."""
    case = fixture("text")
    binary_value = AttributeValue(case.relation_note, XsdType.STRING, "binary")
    polyadic_value = AttributeValue(case.relation_note, XsdType.STRING, "polyadic")
    editor = case.graph.edit()
    editor.set_attribute(RelationInstanceRef(0), binary_value)
    editor.set_attribute(PolyadicInstanceRef(0), polyadic_value)
    graph = editor.freeze()

    assert (
        edit_module._attribute_at(graph, RelationInstanceRef(0), case.relation_note)
        == binary_value
    )
    assert (
        edit_module._attribute_at(graph, PolyadicInstanceRef(0), case.relation_note)
        == polyadic_value
    )
    assert (
        edit_module._attribute_at(graph, None, QualifiedName(case.namespace, "missing"))
        is None
    )
    assert edit_module._attribute_at(graph, None, case.note) is None


def test_no_op_attribute_patch_has_an_empty_executable_recipe() -> None:
    """A no-op public edit remains a replayable no-op patch operation."""
    case = fixture("text")
    prior = case.graph.tiers[1].items[0].attributes[0]
    journal = Journal()
    editor = case.graph.edit(journal=journal)
    editor.set_attribute(ItemRef(case.spare, 0), prior)

    patch = journal.to_patch()
    opcode = cast(DeltaOpcode, patch.operations[0].opcode)

    assert opcode.calls == ()
    assert opcode.changes == ()
    assert patch.apply(case.graph) == case.graph


@pytest.mark.parametrize("operation", OPERATIONS)
def test_journal_patch_round_trips_every_primitive(operation: str) -> None:
    case = fixture("text")
    base, edit, view = _case(case, operation)
    journal = Journal(stage="primitive")
    editor = base.edit(journal=journal)
    edit(editor)
    target = editor.freeze()

    patch = patch_loads(patch_dumps(journal.to_patch()))

    assert equivalent(patch.apply(base), target, view)
    assert equivalent(patch.invert().apply(target), base, view)


def test_patch_composition_preserves_guards_and_annotations() -> None:
    base, middle, first = _transition()
    case = fixture("speech")
    journal = Journal(stage="second", tool="test")
    editor = middle.edit(journal=journal)
    editor.add_layer(wire_fixtures.SOURCE_B)
    second_target = editor.freeze()
    second = journal.to_patch()

    combined = compose_patches(first, second)

    assert combined.apply(base) == second_target
    assert combined.annotations.author == "editor"
    assert combined.annotations.stage == "second"
    with pytest.raises(Refusal, match="target and base fingerprints differ"):
        compose_patches(second, first)
    assert case.graph == base


def test_patch_refuses_any_other_identified_base() -> None:
    base, _, patch = _transition()
    reused = replace(
        base,
        tiers=(
            replace(
                base.tiers[0],
                items=(
                    replace(base.tiers[0].items[0], durable_id="reused"),
                    *base.tiers[0].items[1:],
                ),
            ),
            *base.tiers[1:],
        ),
    )

    with pytest.raises(Refusal, match="base fingerprint mismatch"):
        patch.apply(reused)


def test_patch_container_and_operation_guards() -> None:
    base, _, patch = _transition()
    identified = fingerprint(base, EquivalenceView.IDENTIFIED)
    empty = Graph((), (), ())

    with pytest.raises(Refusal, match="edit opcode 'unknown' is unknown"):
        DeltaOpcode("unknown", (), ())
    with pytest.raises(Refusal, match="patch_version must be '1'"):
        Patch(identified, identified, (), patch_version="2")

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(patch_codec, "MAX_TOTAL_OPCODES", 0)
        with pytest.raises(Refusal, match="operation count exceeds limit 0"):
            Patch(identified, identified, patch.operations)

    disconnected = Patch(
        identified,
        patch.target_fingerprint,
        (
            replace(
                patch.operations[0],
                base_fingerprint=fingerprint(empty, EquivalenceView.IDENTIFIED),
            ),
        ),
    )
    with pytest.raises(Refusal, match="operation 0 'insert_item' base fingerprint"):
        disconnected.apply(base)

    wrong_target = Patch(identified, "wrong", ())
    with pytest.raises(Refusal, match="patch target fingerprint mismatch"):
        wrong_target.apply(base)


def test_program_refuses_patch_removal_opcode() -> None:
    graph = Graph((), (), ())
    operation = DeltaOpcode.between("remove_item", graph, graph)

    with pytest.raises(Refusal, match="Program refuses removal opcode 'remove_item'"):
        Program((operation,))

    record = operation.to_data()
    source = '{"machine_version":"2"}\n' + json.dumps(record) + "\n"
    with pytest.raises(Refusal, match="Program refuses removal opcode 'remove_item'"):
        program_loads(source)


def test_version_one_programs_still_read_but_keep_their_vocabulary() -> None:
    source = (
        '{"machine_version":"1"}\n'
        '{"opcode":"declare_namespace","declaration":'
        '{"prefix":"v","namespace":"urn:v1"}}\n'
    )
    assert program_loads(source).unroll().graph.namespaces == (
        NamespaceDeclaration("v", "urn:v1"),
    )
    with pytest.raises(Refusal, match="does not define opcode 'add_layer'"):
        program_loads(
            source + '{"opcode":"add_layer","name":'
            '{"vocabulary":"urn:v1","source":"legacy"}}\n'
        )
    with pytest.raises(Refusal, match="does not define opcode 'add_layer'"):
        program_loads(
            source + '{"opcode":"repeat","count":1,"body":['
            '{"opcode":"add_layer","name":'
            '{"vocabulary":"urn:v1","source":"legacy"}}]}\n'
        )


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (b"", "JSONL patch is missing its header line"),
        (b"[]\n", "header must be an object"),
        (b"{}\n", "header is missing field 'patch_version'"),
        (
            b'{"patch_version":"2","base_fingerprint":"a",'
            b'"target_fingerprint":"b","annotations":{}}\n',
            "header patch_version must be '1'",
        ),
        (b"{\n", "JSONL line 1: parse JSON failed"),
    ],
)
def test_patch_codec_refuses_malformed_or_wrong_version(
    source: bytes, message: str
) -> None:
    with pytest.raises(Refusal, match=message):
        patch_loads(source)


def test_patch_codec_refuses_oversized_input_and_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base, _, patch = _transition()
    monkeypatch.setattr(machine_codec, "MAX_DOCUMENT_BYTES", 4)
    with pytest.raises(Refusal, match="JSONL patch exceeds 4 bytes"):
        patch_loads(b"xxxxx")

    monkeypatch.setattr(patch_codec, "_JSONL_LINE_BYTES", 8)
    with pytest.raises(Refusal, match="JSONL line 1 exceeds 8 bytes"):
        patch_dumps(patch)
    assert patch.apply(base) != base

    monkeypatch.setattr(patch_codec, "_JSONL_LINE_BYTES", 1024 * 1024)
    monkeypatch.setattr(patch_codec, "MAX_DOCUMENT_BYTES", 8)
    with pytest.raises(Refusal, match="JSONL patch exceeds 8 bytes"):
        patch_dumps(patch)


def test_patch_writer_refuses_unencodable_annotation() -> None:
    base = Graph((), (), ())
    identified = fingerprint(base, EquivalenceView.IDENTIFIED)
    patch = Patch(
        identified,
        identified,
        (),
        EditAnnotations(author="\ud800"),
    )
    with pytest.raises(Refusal, match="JSONL line 1"):
        patch_dumps(patch)


def test_patch_writer_stages_nonfinite_annotation_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The writer converts JSON numeric failures into a value refusal."""
    _, _, patch = _transition()
    monkeypatch.setattr(
        EditAnnotations,
        "to_data",
        lambda self: {"fields": {"nonfinite": float("inf")}},
    )

    with pytest.raises(Refusal) as caught:
        patch_dumps(patch)
    assert caught.value.stage is RefusalStage.VALUE


def test_patch_apply_stages_ordinary_opcode_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unexpected value errors from an executable opcode remain refusals."""
    base, _, patch = _transition()

    def fail(self: DeltaOpcode, graph: Graph) -> Graph:
        raise ValueError("bad edit")

    monkeypatch.setattr(DeltaOpcode, "apply", fail)
    with pytest.raises(Refusal, match="refused: bad edit"):
        patch.apply(base)

    def refuse(self: DeltaOpcode, graph: Graph) -> Graph:
        raise Refusal(RefusalStage.SEMANTICS, "checked refusal")

    monkeypatch.setattr(DeltaOpcode, "apply", refuse)
    with pytest.raises(Refusal, match="checked refusal"):
        patch.apply(base)


def test_patch_reader_stages_nonfinite_delta_values() -> None:
    _, _, patch = _transition()
    records = [json.loads(line) for line in patch_dumps(patch).splitlines()]
    records[1]["changes"] = [
        {
            "kind": "replace",
            "path": ["graph"],
            "before": {},
            "after": float("inf"),
        }
    ]
    source = "".join(json.dumps(record) + "\n" for record in records)

    with pytest.raises(Refusal) as caught:
        patch_loads(source)
    assert caught.value.stage is RefusalStage.VALUE


def test_patch_codec_refuses_tampered_operation_fingerprints() -> None:
    _, _, patch = _transition()
    records = [json.loads(line) for line in patch_dumps(patch).splitlines()]
    records[1]["target_fingerprint"] = "tampered"
    source = "".join(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        for record in records
    )

    decoded = patch_loads(source)
    with pytest.raises(Refusal, match="produced the wrong target fingerprint"):
        decoded.apply(_transition()[0])

    records[1]["target_fingerprint"] = patch.operations[0].target_fingerprint
    records[1]["base_fingerprint"] = "tampered"
    source = "".join(json.dumps(record) + "\n" for record in records)
    decoded = patch_loads(source)
    with pytest.raises(Refusal, match="base fingerprint mismatch"):
        decoded.apply(_transition()[0])


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda record: record["inverse"].update(opcode="move_item"),
            "cannot execute call",
        ),
        (lambda record: record.update(opcode="move_item"), "cannot execute call"),
        (lambda record: record.update(changes={}), "changes must be an array"),
        (lambda record: record.update(annotations=[]), "annotations must be an object"),
        (
            lambda record: record.update(annotations={"unknown": 1}),
            "annotations has unknown fields",
        ),
        (
            lambda record: record.update(annotations={"confidence": True}),
            "annotations.confidence must be numeric",
        ),
        (
            lambda record: record.update(annotations={"fields": []}),
            "annotations.fields must be an object",
        ),
    ],
)
def test_patch_codec_refuses_tampered_operation_shapes(
    mutate: Callable[[dict[str, object]], None], message: str
) -> None:
    _, _, patch = _transition()
    records = [json.loads(line) for line in patch_dumps(patch).splitlines()]
    mutate(records[1])
    source = "".join(json.dumps(record) + "\n" for record in records)
    with pytest.raises(Refusal, match=message):
        patch_loads(source)


@pytest.mark.parametrize(
    ("operation", "message"),
    [
        ([], "line 2 must be an object"),
        ({"opcode": "delta"}, "line 2 is missing fields"),
        (
            {
                "opcode": "repeat",
                "count": 0,
                "body": [],
                "base_fingerprint": "empty",
                "target_fingerprint": "empty",
                "annotations": {},
                "inverse": {"opcode": "repeat", "count": 0, "body": []},
            },
            "forward and inverse must be primitive opcodes",
        ),
    ],
)
def test_patch_codec_refuses_nonprimitive_operation_records(
    operation: object, message: str
) -> None:
    """Patch records must be complete objects containing primitive opcodes."""
    header = {
        "patch_version": "1",
        "base_fingerprint": "empty",
        "target_fingerprint": "empty",
        "annotations": {},
    }
    source = json.dumps(header) + "\n" + json.dumps(operation) + "\n"

    with pytest.raises(Refusal, match=message):
        patch_loads(source)
