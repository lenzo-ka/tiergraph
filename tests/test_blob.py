"""Exercise the fixed blob vocabulary and its metadata-only profile."""

from __future__ import annotations

import hashlib
import io
from dataclasses import replace
from decimal import Decimal
from typing import BinaryIO, cast

import pytest

from tiergraph import (
    BLOB_NAMESPACE,
    PROFILES,
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    BlobProfile,
    BlobRef,
    BlobResolver,
    BlobSink,
    BlobSpan,
    BoundaryRef,
    BoundarySide,
    ChainResolver,
    ClockProfile,
    DurableBoundaryRef,
    DurableItemRef,
    DurableRelationRef,
    EquivalenceView,
    Graph,
    GraphEditor,
    GraphValidationError,
    Item,
    ItemRef,
    Journal,
    JsonAttributeValue,
    LayerFact,
    LayerName,
    MappingResolver,
    NamespaceDeclaration,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    QualifiedName,
    RelationEndpointKind,
    RelationInstance,
    RelationInstanceRef,
    RelationSideDeclaration,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    VerifiedReader,
    XsdType,
    anchored_boundary,
    apply_patch,
    declare_blob_vocabulary,
    diff,
    dumps,
    fingerprint,
    graph_to_program,
    hash_blob,
    loads,
)
from tiergraph.machine import DeltaOpcode

NS = "urn:tiergraph:test:blob"
WORDS = QualifiedName(NS, "words")
BLOBS = QualifiedName(NS, "blobs")
WORD_TYPE = QualifiedName(NS, "word")
BLOB_TYPE = QualifiedName(NS, "blob")
WORD_MEMBERS = QualifiedName(NS, "word-members")
BLOB_MEMBERS = QualifiedName(NS, "blob-members")
RESOURCE = QualifiedName(NS, "resource")
METADATA = QualifiedName(NS, "metadata")
TITLE = QualifiedName(NS, "title")
CLOCK = QualifiedName(NS, "clock")
CLOCK_TYPE = QualifiedName(NS, "clock-position")
CLOCK_MEMBERS = QualifiedName(NS, "clock-members")
CLOCK_BINDING = QualifiedName(NS, "clock-binding")
CLOCK_RATE = QualifiedName(NS, "clock-rate")
CLOCK_UNIT = QualifiedName(NS, "clock-unit")
CLOCK_UNTIMED = QualifiedName(NS, "clock-untimed")
CLOCK_START = QualifiedName(NS, "clock-start")
CLOCK_DURATION = QualifiedName(NS, "clock-duration")


def blob_name(local: str) -> QualifiedName:
    """Return one name in the fixed blob namespace."""
    return QualifiedName(BLOB_NAMESPACE, local)


SHA256 = blob_name("sha256")
SIZE = blob_name("size")
MEDIA_TYPE = blob_name("media-type")
SCHEMA = blob_name("schema")
RATE = blob_name("rate")
UNIT = blob_name("unit")
ORIGIN = blob_name("origin")
EXTENT = blob_name("extent")
OFFSET = blob_name("offset")
LENGTH = blob_name("length")


def value(name: QualifiedName, kind: XsdType, lexical: str) -> AttributeValue:
    """Build one scalar attribute value."""
    return AttributeValue(name, kind, lexical)


def blob_item(
    durable_id: str | None,
    digest: str,
    size: int,
    media_type: str,
    schema: str,
    *extra: AttributeValue,
) -> Item:
    """Build one descriptor item in the fixed vocabulary."""
    return Item(
        durable_id,
        (
            value(SHA256, XsdType.STRING, digest),
            value(SIZE, XsdType.INTEGER, str(size)),
            value(MEDIA_TYPE, XsdType.STRING, media_type),
            value(SCHEMA, XsdType.STRING, schema),
            *extra,
        ),
    )


def base_editor() -> GraphEditor:
    """Return a mutable graph with the blob and test declarations installed."""
    editor = Graph((), (), ()).edit()
    assert declare_blob_vocabulary(editor) is editor
    editor.declare(NamespaceDeclaration("test", NS))
    editor.declare(TierDeclaration(WORDS, "Words"))
    editor.declare(TierDeclaration(BLOBS, "External resources"))
    editor.declare(SimpleRelationDeclaration(WORD_MEMBERS, WORDS, WORD_TYPE))
    editor.declare(SimpleRelationDeclaration(BLOB_MEMBERS, BLOBS, BLOB_TYPE))
    editor.declare(BipartiteRelationDeclaration(RESOURCE, WORD_TYPE, BLOB_TYPE))
    editor.declare(BipartiteRelationDeclaration(METADATA, BLOB_TYPE, BLOB_TYPE))
    editor.declare(AttributeDeclaration(TITLE, AttributeDomain.ITEM, XsdType.STRING))
    return editor


def fixture() -> Graph:
    """Build mixed transcript, audio, nested-graph, and key-value resources."""
    editor = base_editor()
    editor.insert_item(WORDS, 0, Item("word-0"))
    editor.insert_item(WORDS, 1, Item("word-1"))
    transcript_digest = "cc" * 32
    editor.insert_item(
        BLOBS,
        0,
        blob_item(
            "transcript",
            transcript_digest,
            12,
            "application/vnd.example.transcript+json",
            "urn:example:transcript:v1",
            value(UNIT, XsdType.STRING, "character"),
            value(EXTENT, XsdType.INTEGER, "12"),
        ),
    )
    editor.insert_item(
        BLOBS,
        1,
        blob_item(
            "audio",
            "11" * 32,
            48,
            "audio/x.example",
            "urn:example:audio:pcm",
            value(RATE, XsdType.DECIMAL, "16000"),
            value(UNIT, XsdType.STRING, "sample"),
            value(ORIGIN, XsdType.DECIMAL, "0"),
            value(EXTENT, XsdType.INTEGER, "24"),
            value(TITLE, XsdType.STRING, "recording"),
        ),
    )
    editor.insert_item(
        BLOBS,
        2,
        blob_item(
            "recording-metadata",
            "ff" * 32,
            9,
            "application/json",
            "urn:example:recording-metadata:v1",
        ),
    )
    editor.insert_item(
        BLOBS,
        3,
        blob_item(
            "recognizer-output",
            "22" * 32,
            10,
            "application/json",
            "urn:example:recognizer-output:v2",
        ),
    )
    editor.insert_item(
        BLOBS,
        4,
        blob_item(
            "nested-graph",
            transcript_digest,
            12,
            "application/vnd.tiergraph+json",
            "urn:example:nested-graph:v1",
        ),
    )
    editor.add_relation(
        RelationInstance(
            RESOURCE,
            ItemRef(WORDS, 0),
            DurableItemRef("transcript"),
            "word-transcript",
            (
                value(OFFSET, XsdType.INTEGER, "0"),
                value(LENGTH, XsdType.INTEGER, "4"),
            ),
        )
    )
    editor.add_relation(
        RelationInstance(
            RESOURCE,
            DurableItemRef("word-0"),
            DurableItemRef("audio"),
            "word-0-audio",
            (
                value(OFFSET, XsdType.INTEGER, "0"),
                value(LENGTH, XsdType.INTEGER, "8"),
            ),
        )
    )
    editor.add_relation(
        RelationInstance(
            RESOURCE,
            DurableItemRef("word-1"),
            DurableItemRef("audio"),
            "word-1-audio",
            (value(LENGTH, XsdType.INTEGER, "8"),),
        )
    )
    editor.add_relation(
        RelationInstance(
            METADATA,
            DurableItemRef("audio"),
            DurableItemRef("recording-metadata"),
            "audio-recording-metadata",
        )
    )
    editor.add_relation(
        RelationInstance(
            METADATA,
            DurableItemRef("audio"),
            DurableItemRef("recognizer-output"),
            "audio-recognizer-output",
        )
    )
    return editor.freeze()


def with_item_value(
    graph: Graph, reference: ItemRef, name: QualifiedName, lexical: str
) -> Graph:
    """Replace one existing scalar item value with the given lexical form."""
    declaration = next(
        declaration
        for declaration in graph.attribute_declarations
        if declaration.name == name
    )
    assert isinstance(declaration.value_type, XsdType)
    return graph.set_attribute(reference, value(name, declaration.value_type, lexical))


def clock_fixture(
    *,
    clock_rate: str = "1",
    transcript_rate: str = "4",
    audio_rate: str = "8",
    stored_timing: bool = False,
) -> tuple[Graph, ClockProfile]:
    """Add exact word timing and an explicitly untimed blob tier."""
    graph = fixture()
    graph = graph.set_attribute(
        ItemRef(BLOBS, 0), value(RATE, XsdType.DECIMAL, transcript_rate)
    )
    graph = with_item_value(graph, ItemRef(BLOBS, 0), UNIT, "second")
    graph = with_item_value(graph, ItemRef(BLOBS, 1), RATE, audio_rate)
    graph = with_item_value(graph, ItemRef(BLOBS, 1), UNIT, "second")
    words = graph.tiers[0]
    if stored_timing:
        words = replace(
            words,
            items=tuple(
                replace(
                    item,
                    attributes=(
                        value(CLOCK_START, XsdType.DECIMAL, str(index)),
                        value(CLOCK_DURATION, XsdType.DECIMAL, "1"),
                    ),
                )
                for index, item in enumerate(words.items)
            ),
        )
    blobs = replace(
        graph.tiers[1],
        attributes=(value(CLOCK_UNTIMED, XsdType.BOOLEAN, "true"),),
    )
    clock = Tier(
        TierDeclaration(CLOCK, "Clock"),
        (Item("clock-0"), Item("clock-1")),
    )
    bare = replace(
        graph,
        tiers=(words, blobs, clock),
        relation_declarations=(
            *graph.relation_declarations,
            SimpleRelationDeclaration(CLOCK_MEMBERS, CLOCK, CLOCK_TYPE),
            BipartiteRelationDeclaration(
                CLOCK_BINDING,
                WORD_TYPE,
                CLOCK_TYPE,
                RelationEndpointKind.BOUNDARY,
                RelationEndpointKind.BOUNDARY,
            ),
        ),
        attribute_declarations=(
            *graph.attribute_declarations,
            AttributeDeclaration(CLOCK_RATE, AttributeDomain.DOCUMENT, XsdType.DECIMAL),
            AttributeDeclaration(CLOCK_UNIT, AttributeDomain.DOCUMENT, XsdType.STRING),
            AttributeDeclaration(CLOCK_UNTIMED, AttributeDomain.TIER, XsdType.BOOLEAN),
            AttributeDeclaration(CLOCK_START, AttributeDomain.ITEM, XsdType.DECIMAL),
            AttributeDeclaration(CLOCK_DURATION, AttributeDomain.ITEM, XsdType.DECIMAL),
        ),
        attributes=(
            *graph.attributes,
            value(CLOCK_RATE, XsdType.DECIMAL, clock_rate),
            value(CLOCK_UNIT, XsdType.STRING, "second"),
        ),
    )
    bindings = tuple(
        RelationInstance(
            CLOCK_BINDING,
            anchored_boundary(bare, BoundaryRef(WORDS, index)),
            anchored_boundary(bare, BoundaryRef(CLOCK, index)),
            f"clock-binding-{index}",
        )
        for index in range(3)
    )
    timed = replace(bare, relations=(*bare.relations, *bindings))
    return timed, ClockProfile(
        timed,
        CLOCK,
        CLOCK_BINDING,
        None if stored_timing else CLOCK_RATE,
        CLOCK_UNIT,
        untimed_attribute=CLOCK_UNTIMED,
        start_attribute=CLOCK_START if stored_timing else None,
        duration_attribute=CLOCK_DURATION if stored_timing else None,
    )


def test_public_values_validate_context_free_identity_and_spans() -> None:
    """Canonical references and unit-neutral spans refuse malformed primitives."""
    assert BlobRef("00" * 32, 0) < BlobRef("11" * 32, 0)
    assert BlobSpan(None, 4) == BlobSpan(None, 4)
    assert BlobSpan(2, 4).offset == 2
    with pytest.raises(ValueError, match="64 lowercase"):
        BlobRef("AA" * 32, 0)
    with pytest.raises(ValueError, match="not integral"):
        BlobRef("00" * 32, True)
    with pytest.raises(ValueError, match="size .* negative"):
        BlobRef("00" * 32, -1)
    with pytest.raises(ValueError, match="offset .* not integral"):
        BlobSpan(False, 1)
    with pytest.raises(ValueError, match="length .* not integral"):
        BlobSpan(None, True)
    with pytest.raises(ValueError, match="offset .* negative"):
        BlobSpan(-1, 1)
    with pytest.raises(ValueError, match="length .* negative"):
        BlobSpan(None, -1)


def test_profile_preserves_declared_order_ids_roles_and_resource_types() -> None:
    """Arbitrary resources and blob chains remain ordered ordinary graph data."""
    graph = fixture()
    profile = BlobProfile(graph)
    assert [reference.index for reference, _ in profile.blobs()] == [0, 1, 2, 3, 4]
    assert [reference.sha256 for reference in profile.required()] == [
        "cc" * 32,
        "11" * 32,
        "ff" * 32,
        "22" * 32,
    ]
    assert profile.attachments(DurableItemRef("audio")) == (
        (RelationInstanceRef(1), ItemRef(WORDS, 0), BlobSpan(0, 8)),
        (RelationInstanceRef(2), ItemRef(WORDS, 1), BlobSpan(None, 8)),
    )
    assert profile.attachments(ItemRef(BLOBS, 2)) == (
        (RelationInstanceRef(3), ItemRef(BLOBS, 1), None),
    )
    assert profile.attachments(ItemRef(BLOBS, 3)) == (
        (RelationInstanceRef(4), ItemRef(BLOBS, 1), None),
    )
    assert profile.attachments(ItemRef(BLOBS, 4)) == ()
    with pytest.raises(ValueError, match="is not a blob"):
        profile.attachments(ItemRef(WORDS, 0))

    inspected = graph.add_layer(LayerName(NS, "media-inspector")).put_fact(
        LayerName(NS, "media-inspector"),
        LayerFact(DurableItemRef("audio"), value(TITLE, XsdType.STRING, "verified")),
    )
    assert BlobProfile(inspected).required() == profile.required()
    assert inspected.layers[0].name.source == "media-inspector"


def test_clock_agreement_accepts_absolute_and_duration_only_spans_exactly() -> None:
    """Uniform timing uses exact products even for nonterminating tick ratios."""
    graph, clock = clock_fixture(clock_rate="3", transcript_rate="12", audio_rate="24")
    assert clock.rate == Decimal("3")
    BlobProfile(graph).check_clock(clock)


def test_clock_agreement_accepts_stored_timing_without_a_uniform_rate() -> None:
    """Stored decimal timing supports both absolute and duration-only spans."""
    graph, clock = clock_fixture(stored_timing=True)
    assert clock.rate is None
    BlobProfile(graph).check_clock(clock)


def test_blob_tier_must_be_untimed_under_clock() -> None:
    """A blob tier cannot silently participate in structural clock timing."""
    graph, clock = clock_fixture()
    words = replace(
        graph.tiers[0],
        attributes=(value(CLOCK_UNTIMED, XsdType.BOOLEAN, "true"),),
    )
    blobs = replace(graph.tiers[1], attributes=())
    bare = replace(
        graph,
        tiers=(words, blobs, graph.tiers[2]),
        relation_declarations=tuple(
            replace(declaration, left_type=BLOB_TYPE)
            if isinstance(declaration, BipartiteRelationDeclaration)
            and declaration.name == CLOCK_BINDING
            else declaration
            for declaration in graph.relation_declarations
        ),
        relations=tuple(
            relation
            for relation in graph.relations
            if relation.declaration != CLOCK_BINDING
        ),
    )
    blob_bindings = tuple(
        RelationInstance(
            CLOCK_BINDING,
            anchored_boundary(bare, BoundaryRef(BLOBS, index)),
            anchored_boundary(bare, BoundaryRef(CLOCK, min(index, 2))),
            f"blob-clock-binding-{index}",
        )
        for index in range(len(bare.tiers[1].items) + 1)
    )
    timed = replace(bare, relations=(*bare.relations, *blob_bindings))
    timed_clock = replace(clock, graph=timed)
    assert timed_clock.is_timed(BLOBS)
    with pytest.raises(ValueError, match="blob tier .* must be explicitly untimed"):
        BlobProfile(timed).check_clock(timed_clock)


@pytest.mark.parametrize(
    ("attribute", "lexical", "message"),
    (
        (LENGTH, "3", "length disagrees"),
        (OFFSET, "1", "offset disagrees"),
    ),
)
def test_clock_agreement_refuses_uniform_span_disagreement(
    attribute: QualifiedName, lexical: str, message: str
) -> None:
    """Uniform-clock length and offset disagreements name edge and subject."""
    graph, clock = clock_fixture()
    changed = graph.set_attribute(
        RelationInstanceRef(0), value(attribute, XsdType.INTEGER, lexical)
    )
    with pytest.raises(ValueError, match=rf"attachment 0 .*subject .*{message}"):
        BlobProfile(changed).check_clock(replace(clock, graph=changed))


@pytest.mark.parametrize(
    ("attribute", "lexical", "message"),
    (
        (LENGTH, "3", "length disagrees"),
        (OFFSET, "1", "offset disagrees"),
    ),
)
def test_clock_agreement_refuses_stored_span_disagreement(
    attribute: QualifiedName, lexical: str, message: str
) -> None:
    """Stored-clock length and offset disagreements remain exact and named."""
    graph, clock = clock_fixture(stored_timing=True)
    changed = graph.set_attribute(
        RelationInstanceRef(0), value(attribute, XsdType.INTEGER, lexical)
    )
    with pytest.raises(ValueError, match=rf"attachment 0 .*subject .*{message}"):
        BlobProfile(changed).check_clock(replace(clock, graph=changed))


def test_clock_agreement_refuses_missing_rate_unit_mismatch_and_missing_time() -> None:
    """Uncomparable span claims refuse with stable attachment context."""
    graph, clock = clock_fixture()
    missing_rate = graph.remove_attribute(ItemRef(BLOBS, 1), RATE)
    with pytest.raises(
        ValueError, match=r"attachment 1 .*subject .*requires blob:rate"
    ):
        BlobProfile(missing_rate).check_clock(replace(clock, graph=missing_rate))

    wrong_unit = with_item_value(graph, ItemRef(BLOBS, 1), UNIT, "millisecond")
    with pytest.raises(
        ValueError, match=r"attachment 1 .*subject .*not clock unit 'second'"
    ):
        BlobProfile(wrong_unit).check_clock(replace(clock, graph=wrong_unit))

    uncalibrated = replace(clock, rate_attribute=None)
    with pytest.raises(
        ValueError, match=r"attachment 0 .*subject .*no physical subject timing"
    ):
        BlobProfile(graph).check_clock(uncalibrated)


def test_clock_agreement_requires_profiles_for_the_same_graph() -> None:
    """Coordinates from another graph cannot be applied to blob attachments."""
    _, clock = clock_fixture()
    with pytest.raises(ValueError, match="profiles must describe the same graph"):
        BlobProfile(fixture()).check_clock(clock)


def test_clock_editor_blob_guard_refuses_until_span_is_updated() -> None:
    """A timing change cannot leave an opted-in attachment silently mistimed."""
    graph, clock = clock_fixture()
    editor = clock.edit("keep-earlier", blob=BlobProfile(graph))
    editor.move_item(DurableItemRef("word-0"), 1)
    with pytest.raises(
        GraphValidationError,
        match=r"clock edit violates blob span agreement: .*offset disagrees",
    ):
        editor.freeze()

    assert (
        editor.set_attribute(
            DurableRelationRef("word-0-audio"),
            value(OFFSET, XsdType.INTEGER, "8"),
        )
        is editor
    )
    editor.set_attribute(
        DurableRelationRef("word-transcript"),
        value(OFFSET, XsdType.INTEGER, "4"),
    )
    changed = editor.freeze()
    BlobProfile(changed).check_clock(editor.profile)


def test_clock_editor_attribute_refusals_are_atomic() -> None:
    """Invalid clock attributes leave the plain editor and profile unchanged."""
    graph, clock = clock_fixture()
    editor = clock.edit()

    with pytest.raises(
        GraphValidationError,
        match="attribute edit would invalidate.*clock unit.*is empty",
    ):
        editor.set_attribute(
            None,
            value(CLOCK_UNIT, XsdType.STRING, ""),
        )
    assert editor.freeze() is graph
    assert editor.profile is clock

    with pytest.raises(
        GraphValidationError,
        match="attribute edit would invalidate.*clock unit.*has no value",
    ):
        editor.remove_attribute(None, CLOCK_UNIT)
    assert editor.freeze() is graph
    assert editor.profile is clock


def test_clock_editor_blob_guard_allows_duration_only_repair_and_retirement() -> None:
    """The same session can drop absolute alignment or retire its clock profile."""
    graph, clock = clock_fixture()
    repaired = clock.edit("keep-earlier", blob=BlobProfile(graph))
    repaired.move_item(DurableItemRef("word-0"), 1)
    assert (
        repaired.remove_attribute(DurableRelationRef("word-0-audio"), OFFSET)
        is repaired
    )
    repaired.remove_attribute(DurableRelationRef("word-transcript"), OFFSET)
    repaired.freeze()

    retired = clock.edit("keep-earlier", blob=BlobProfile(graph))
    retired.undeclare_with_contents(CLOCK)
    result = retired.freeze()
    assert all(tier.declaration.name != CLOCK for tier in result.tiers)


def test_clock_journal_blob_guard_defers_until_freeze_and_tracks_span_edits() -> None:
    """Journaled clock and span operations share the same final agreement gate."""
    graph, clock = clock_fixture()
    editor = clock.edit("keep-earlier", blob=BlobProfile(graph), journal=Journal())
    duration_only = DurableRelationRef("word-1-audio")
    with pytest.raises(GraphValidationError, match="carries no attribute"):
        editor.remove_attribute(duration_only, OFFSET)
    assert (
        editor.set_attribute(duration_only, value(OFFSET, XsdType.INTEGER, "8"))
        is editor
    )
    assert editor.remove_attribute(duration_only, OFFSET) is editor

    editor.move_item(DurableItemRef("word-0"), 1)
    with pytest.raises(GraphValidationError, match="offset disagrees"):
        editor.freeze()
    editor.set_attribute(
        DurableRelationRef("word-transcript"),
        value(OFFSET, XsdType.INTEGER, "4"),
    )
    editor.set_attribute(
        DurableRelationRef("word-0-audio"),
        value(OFFSET, XsdType.INTEGER, "8"),
    )
    changed = editor.freeze()
    BlobProfile(changed).check_clock(editor.profile)
    editor.undo()
    with pytest.raises(GraphValidationError, match="offset disagrees"):
        editor.freeze()
    editor.redo()
    editor.freeze()


def test_clock_editor_blob_guard_requires_matching_valid_profiles() -> None:
    """A guarded session starts only from one graph with agreed metadata."""
    graph, clock = clock_fixture()
    with pytest.raises(ValueError, match="editor's same graph"):
        clock.edit(blob=BlobProfile(fixture()))

    mismatched = graph.set_attribute(
        DurableRelationRef("word-0-audio"),
        value(LENGTH, XsdType.INTEGER, "3"),
    )
    with pytest.raises(GraphValidationError, match="length disagrees"):
        replace(clock, graph=mismatched).edit(blob=BlobProfile(mismatched))


def test_clock_agreement_refuses_untimed_and_unavailable_subjects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Untimed and structurally unqueryable subjects retain edge context."""
    graph, clock = clock_fixture()
    untimed_words = replace(
        graph.tiers[0],
        attributes=(value(CLOCK_UNTIMED, XsdType.BOOLEAN, "true"),),
    )
    untimed_graph = replace(
        graph,
        tiers=(untimed_words, *graph.tiers[1:]),
        relations=tuple(
            relation
            for relation in graph.relations
            if relation.declaration != CLOCK_BINDING
        ),
    )
    with pytest.raises(
        ValueError, match=r"attachment 0 .*subject .*no physical subject timing"
    ):
        BlobProfile(untimed_graph).check_clock(replace(clock, graph=untimed_graph))

    def unavailable_status(self: ClockProfile, tier: QualifiedName) -> bool:
        """Model a structural clock that cannot classify the blob tier."""
        del self, tier
        raise ValueError("structural clock has no event bindings")

    monkeypatch.setattr(ClockProfile, "is_timed", unavailable_status)
    with pytest.raises(
        ValueError, match=r"blob tier .*no comparable clock status.*structural clock"
    ):
        BlobProfile(graph).check_clock(clock)

    def unavailable_subject(self: ClockProfile, tier: QualifiedName) -> bool:
        """Model a clock profile that cannot answer event timing queries."""
        del self
        if tier == BLOBS:
            return False
        raise ValueError("structural clock has no event bindings")

    monkeypatch.setattr(ClockProfile, "is_timed", unavailable_subject)
    with pytest.raises(
        ValueError, match=r"attachment 0 .*subject .*no comparable.*structural clock"
    ):
        BlobProfile(graph).check_clock(clock)


def test_vocabulary_round_trips_through_existing_formats_and_diff() -> None:
    """Blob content needs no graph-wire, machine, or patch format change."""
    graph = fixture()
    wire_round_trip = loads(dumps(graph))
    machine_round_trip = graph_to_program(graph).unroll().graph
    assert wire_round_trip == graph
    assert machine_round_trip == graph
    changed = with_item_value(graph, ItemRef(BLOBS, 1), SHA256, "55" * 32)
    patch = diff(graph, changed, EquivalenceView.EXACT)
    assert apply_patch(patch, graph) == changed
    assert any(
        isinstance(operation.opcode, DeltaOpcode)
        and operation.opcode.operation == "replace_item"
        for operation in patch.operations
    )
    assert changed.tiers[1].items[1].durable_id == "audio"
    assert fingerprint(changed, EquivalenceView.EXACT) != fingerprint(
        graph, EquivalenceView.EXACT
    )


def test_core_never_interprets_media_type_or_schema() -> None:
    """Opaque resource types survive core operations without a closed registry."""
    graph = with_item_value(
        fixture(),
        ItemRef(BLOBS, 0),
        MEDIA_TYPE,
        "application/vnd.example.unrecognized+binary",
    )
    graph = with_item_value(
        graph,
        ItemRef(BLOBS, 0),
        SCHEMA,
        "tag:example.com,2026:unrecognized",
    )
    assert loads(dumps(graph)) == graph
    assert BlobProfile(graph).blobs()[0][1] == BlobRef("cc" * 32, 12)


def test_reordering_changes_graph_order_without_changing_blob_ids() -> None:
    """Position and durable identity remain separate under an explicit reorder."""
    graph = fixture()
    moved = graph.move_item(DurableItemRef("nested-graph"), 0)
    assert [item.durable_id for item in moved.tiers[1].items] == [
        "nested-graph",
        "transcript",
        "audio",
        "recording-metadata",
        "recognizer-output",
    ]
    assert [reference.index for reference, _ in BlobProfile(moved).blobs()] == [
        0,
        1,
        2,
        3,
        4,
    ]
    assert fingerprint(moved, EquivalenceView.EXACT) != fingerprint(
        graph, EquivalenceView.EXACT
    )


def test_reordering_metadata_attachments_is_a_visible_edit() -> None:
    """Multiple key-value attachments keep durable ids under reordered positions."""
    graph = fixture()
    relations = graph.relations
    reordered = replace(
        graph,
        relations=(*relations[:3], relations[4], relations[3]),
    )
    assert [relation.durable_id for relation in reordered.relations[3:]] == [
        "audio-recognizer-output",
        "audio-recording-metadata",
    ]
    assert fingerprint(reordered, EquivalenceView.EXACT) != fingerprint(
        graph, EquivalenceView.EXACT
    )
    patch = diff(graph, reordered, EquivalenceView.EXACT)
    assert apply_patch(patch, graph) == reordered


def test_declaring_vocabulary_twice_is_an_explicit_duplicate() -> None:
    """The helper uses normal declaration refusal instead of adopting state."""
    editor = Graph((), (), ()).edit()
    declare_blob_vocabulary(editor)
    with pytest.raises(GraphValidationError, match="duplicate namespace"):
        declare_blob_vocabulary(editor)


@pytest.mark.parametrize(
    ("name", "lexical", "message"),
    (
        (SHA256, "ABC", "64 lowercase"),
        (SIZE, "-1", "size .* negative"),
        (MEDIA_TYPE, "Audio/Wav", "invalid lowercase media type"),
        (MEDIA_TYPE, "text/plain; charset=utf-8", "invalid lowercase media type"),
        (SCHEMA, "relative/schema", "not an absolute URI"),
        (SCHEMA, "urn:example:bad schema", "not an absolute URI"),
        (SCHEMA, "http://[", "not an absolute URI"),
        (RATE, "0", "rate must be positive"),
        (RATE, "-2", "rate must be positive"),
        (EXTENT, "-1", "extent is negative"),
    ),
)
def test_profile_refuses_malformed_descriptor_values(
    name: QualifiedName, lexical: str, message: str
) -> None:
    """Every fixed descriptor value is checked with its item named."""
    graph = with_item_value(fixture(), ItemRef(BLOBS, 0), name, lexical)
    with pytest.raises(ValueError, match=message):
        BlobProfile(graph)


@pytest.mark.parametrize("missing", (SIZE, MEDIA_TYPE, SCHEMA))
def test_profile_refuses_missing_required_descriptor_values(
    missing: QualifiedName,
) -> None:
    """Digest, size, media type, and schema form one complete descriptor."""
    graph = fixture().remove_attribute(ItemRef(BLOBS, 0), missing)
    with pytest.raises(ValueError, match="lacks required"):
        BlobProfile(graph)


def test_rate_requires_unit_and_linear_span_requires_unit() -> None:
    """A rate or numeric span never leaves its resource unit implicit."""
    graph = fixture().remove_attribute(ItemRef(BLOBS, 1), UNIT)
    with pytest.raises(ValueError, match="with a rate requires"):
        BlobProfile(graph)
    graph = fixture().remove_attribute(ItemRef(BLOBS, 0), UNIT)
    with pytest.raises(ValueError, match="linear span.*no nonempty unit"):
        BlobProfile(graph)


def test_profile_refuses_missing_durable_blob_and_attachment_ids() -> None:
    """Every blob and attachment has stable identity in addition to position."""
    graph = fixture()
    editor = base_editor()
    editor.insert_item(
        BLOBS,
        0,
        blob_item(
            None,
            "66" * 32,
            1,
            "application/octet-stream",
            "urn:example:unidentified",
        ),
    )
    without_blob_id = editor.freeze()
    with pytest.raises(ValueError, match="blob item .* lacks a durable id"):
        BlobProfile(without_blob_id)
    relation = graph.relations[0]
    without_relation_id = replace(
        graph,
        relations=(replace(relation, durable_id=None), *graph.relations[1:]),
    )
    with pytest.raises(ValueError, match="attachment 0 lacks a durable id"):
        BlobProfile(without_relation_id)


def test_profile_refuses_conflicting_sizes_for_one_digest() -> None:
    """Two interpretations may share content only when its byte size agrees."""
    graph = with_item_value(fixture(), ItemRef(BLOBS, 4), SIZE, "13")
    with pytest.raises(ValueError, match="declared with sizes 12 and 13"):
        BlobProfile(graph)


def test_profile_refuses_reserved_values_on_non_blob_items() -> None:
    """A reserved item value cannot create an incomplete implicit descriptor."""
    graph = fixture().set_attribute(
        ItemRef(WORDS, 0), value(SIZE, XsdType.INTEGER, "1")
    )
    with pytest.raises(ValueError, match="non-blob item .* reserved attribute"):
        BlobProfile(graph)


def test_profile_refuses_bad_span_shapes_and_targets() -> None:
    """Linear spans are complete, bounded, and attached only to blob targets."""
    graph = fixture().remove_attribute(RelationInstanceRef(0), LENGTH)
    with pytest.raises(ValueError, match="offset without a length"):
        BlobProfile(graph)
    graph = with_item_value(fixture(), ItemRef(BLOBS, 0), EXTENT, "3")
    with pytest.raises(
        ValueError,
        match=r"attachment 0 \('word-transcript'\) for subject .* span exceeds",
    ):
        BlobProfile(graph)
    graph = fixture().set_attribute(
        RelationInstanceRef(0), value(OFFSET, XsdType.INTEGER, "-1")
    )
    with pytest.raises(ValueError, match="offset .* negative"):
        BlobProfile(graph)
    graph = fixture().set_attribute(
        RelationInstanceRef(0), value(LENGTH, XsdType.INTEGER, "-1")
    )
    with pytest.raises(ValueError, match="length .* negative"):
        BlobProfile(graph)
    graph = fixture()
    wrong_target = QualifiedName(NS, "wrong-target")
    graph = replace(
        graph,
        relations=(
            replace(
                graph.relations[0], declaration=wrong_target, right=ItemRef(WORDS, 1)
            ),
            *graph.relations[1:],
        ),
        relation_declarations=(
            *graph.relation_declarations,
            BipartiteRelationDeclaration(wrong_target, WORD_TYPE, WORD_TYPE),
        ),
    )
    with pytest.raises(ValueError, match="right endpoint is not a blob"):
        BlobProfile(graph)


def test_profile_refuses_reserved_span_values_on_polyadic_relations() -> None:
    """A polyadic relation cannot silently carry a bipartite blob span."""
    graph = fixture()
    grouped = QualifiedName(NS, "grouped-resource")
    graph = replace(
        graph,
        relation_declarations=(
            *graph.relation_declarations,
            PolyadicRelationDeclaration(
                grouped,
                RelationSideDeclaration(
                    (RelationEndpointKind.ITEM,), tiers=(WORDS,), maximum=None
                ),
                RelationSideDeclaration(
                    (RelationEndpointKind.ITEM,), tiers=(BLOBS,), maximum=None
                ),
            ),
        ),
        polyadic_relations=(
            PolyadicRelationInstance(
                grouped,
                (ItemRef(WORDS, 1),),
                (ItemRef(BLOBS, 1),),
                "grouped-audio-without-span",
            ),
            PolyadicRelationInstance(
                grouped,
                (ItemRef(WORDS, 0),),
                (ItemRef(BLOBS, 1),),
                "grouped-audio",
                (
                    value(OFFSET, XsdType.INTEGER, "0"),
                    value(LENGTH, XsdType.INTEGER, "999999"),
                ),
            ),
        ),
    )
    with pytest.raises(ValueError, match="polyadic relation instance 1"):
        BlobProfile(graph)


def test_profile_refuses_reserved_blob_values_in_layers() -> None:
    """A layer cannot silently replace a base blob descriptor or span value."""
    graph = (
        fixture()
        .add_layer(LayerName(BLOB_NAMESPACE, "inspector"))
        .put_fact(
            LayerName(BLOB_NAMESPACE, "inspector"),
            LayerFact(
                DurableItemRef("audio"), value(SHA256, XsdType.STRING, "44" * 32)
            ),
        )
    )
    with pytest.raises(ValueError, match="reserved blob attribute.*sha256"):
        BlobProfile(graph)


def test_profile_refuses_non_item_subject_for_blob_attachment() -> None:
    """A boundary-to-blob relation is not silently read as an attachment."""
    graph = fixture()
    declaration = next(
        item
        for item in graph.relation_declarations
        if isinstance(item, BipartiteRelationDeclaration) and item.name == RESOURCE
    )
    boundary_resource = QualifiedName(NS, "boundary-resource")
    boundary_declaration = replace(
        declaration,
        name=boundary_resource,
        left_endpoint=RelationEndpointKind.BOUNDARY,
        left_type=WORD_TYPE,
    )
    relation = replace(
        graph.relations[0],
        declaration=boundary_resource,
        left=DurableBoundaryRef(DurableItemRef("word-0"), BoundarySide.BEFORE),
    )
    graph = replace(
        graph,
        relation_declarations=(*graph.relation_declarations, boundary_declaration),
        relations=(relation, *graph.relations[1:]),
    )
    with pytest.raises(ValueError, match="non-item left endpoint"):
        BlobProfile(graph)


def test_profile_ignores_unrelated_relations_without_blob_span_values() -> None:
    """An ordinary relation stays outside the attachment reading."""
    graph = fixture()
    related = QualifiedName(NS, "related")
    graph = replace(
        graph,
        relation_declarations=(
            *graph.relation_declarations,
            BipartiteRelationDeclaration(related, WORD_TYPE, WORD_TYPE),
        ),
        relations=(
            *graph.relations,
            RelationInstance(
                related, ItemRef(WORDS, 0), ItemRef(WORDS, 1), "word-related"
            ),
        ),
    )
    assert BlobProfile(graph).attachments(ItemRef(BLOBS, 1))[-1][0].index == 2


def test_profile_refuses_wrong_or_unknown_vocabulary_declarations() -> None:
    """The fixed prefix and declaration table have one accepted shape."""
    editor = Graph((), (), ()).edit()
    declare_blob_vocabulary(editor)
    empty = editor.freeze()
    wrong_prefix = replace(
        empty,
        namespaces=(NamespaceDeclaration("resource", BLOB_NAMESPACE),),
    )
    with pytest.raises(ValueError, match="requires prefix 'blob'"):
        BlobProfile(wrong_prefix)
    missing = replace(
        empty,
        attribute_declarations=tuple(
            declaration
            for declaration in empty.attribute_declarations
            if declaration.name != ORIGIN
        ),
    )
    with pytest.raises(ValueError, match="origin.*must be"):
        BlobProfile(missing)
    wrong = replace(
        empty,
        attribute_declarations=tuple(
            replace(declaration, domain=AttributeDomain.DOCUMENT)
            if declaration.name == ORIGIN
            else declaration
            for declaration in empty.attribute_declarations
        ),
    )
    with pytest.raises(ValueError, match="item decimal"):
        BlobProfile(wrong)
    unknown_name = blob_name("unknown")
    unknown = replace(
        empty,
        attribute_declarations=(
            *empty.attribute_declarations,
            AttributeDeclaration(unknown_name, AttributeDomain.ITEM, XsdType.STRING),
        ),
    )
    with pytest.raises(ValueError, match="unknown attribute"):
        BlobProfile(unknown)


def test_registered_profile_accepts_and_refuses_its_fixed_vocabulary() -> None:
    """The package registry exposes the metadata check without role bindings."""
    assert "tiergraph.blob" in PROFILES.names()
    accepted = PROFILES.report(
        "tiergraph.blob", fixture(), {"vocabulary": BLOB_NAMESPACE}
    )
    assert accepted.outcome.value == "satisfied"
    refused = PROFILES.report(
        "tiergraph.blob", Graph((), (), ()), {"vocabulary": BLOB_NAMESPACE}
    )
    assert refused.outcome.value == "refused"
    assert "requires prefix" in (refused.reason or "")
    wrong_role = PROFILES.report(
        "tiergraph.blob", fixture(), {"vocabulary": "urn:example:not-blob"}
    )
    assert wrong_role.outcome.value == "refused"
    assert "must bind the fixed namespace" in (wrong_role.reason or "")


def test_blobless_graph_unchanged_bytes_and_fingerprints() -> None:
    """Blob support changes no bytes or fingerprints of a plain graph golden."""
    graph = Graph((), (), ())
    assert dumps(graph) == ('{\n  "format_version": "0.3.0",\n  "graph": {}\n}\n')
    assert (
        fingerprint(graph, EquivalenceView.EXACT)
        == "1259b8a271616381fdea12b8bed05d0a94703741cc7dc809c8ae26a3c3303e6b"
    )
    with pytest.raises(GraphValidationError, match="JSON values"):
        JsonAttributeValue(
            QualifiedName(NS, "bytes"),
            b"not graph data",  # type: ignore[arg-type]
        )


class _ObjectReader(io.BytesIO):
    def read(self, size: int | None = -1) -> bytes:
        del size
        return cast(bytes, "not bytes")


class _RecordingResolver:
    def __init__(
        self,
        label: str,
        calls: list[tuple[str, str | None]],
        payload: bytes | None,
    ) -> None:
        self.label = label
        self.calls = calls
        self.payload = payload

    def open(self, ref: BlobRef, href: str | None) -> BinaryIO | None:
        del ref
        self.calls.append((self.label, href))
        return None if self.payload is None else io.BytesIO(self.payload)


class _MemorySink:
    def __init__(self) -> None:
        self.payloads: dict[str, bytes] = {}

    def put(self, ref: BlobRef, source: BinaryIO) -> str | None:
        payload = source.read()
        self.payloads[ref.sha256] = payload
        return f"objects/{ref.sha256}"


def test_hash_blob_streams_from_current_position_with_bounded_reads() -> None:
    """Hashing uses canonical SHA-256 identity without rewinding or whole reads."""
    source = io.BytesIO(b"prefixpayload")
    source.seek(6)
    reference = hash_blob(source, chunk_size=3)
    assert reference == BlobRef(hashlib.sha256(b"payload").hexdigest(), 7)
    assert source.tell() == len(b"prefixpayload")

    for chunk_size in (0, -1):
        with pytest.raises(ValueError, match="not positive"):
            hash_blob(io.BytesIO(), chunk_size=chunk_size)
    with pytest.raises(ValueError, match="not integral"):
        hash_blob(io.BytesIO(), chunk_size=True)
    with pytest.raises(TypeError, match="must return bytes"):
        hash_blob(_ObjectReader())


def test_mapping_resolver_uses_digest_and_returns_fresh_readers() -> None:
    """Caller-held payloads resolve by content digest rather than href or size."""
    payload = b"caller-held payload"
    reference = hash_blob(io.BytesIO(payload))
    resolver: BlobResolver = MappingResolver({reference.sha256: payload})
    first = resolver.open(reference, "ignored/location")
    second = resolver.open(BlobRef(reference.sha256, reference.size + 1), None)
    assert first is not None
    assert second is not None
    assert first is not second
    assert first.read() == payload
    assert second.read() == payload
    assert resolver.open(BlobRef("00" * 32, 0), None) is None

    with pytest.raises(ValueError, match="64 lowercase"):
        MappingResolver({"BAD": b"payload"})
    with pytest.raises(TypeError, match="must be bytes"):
        MappingResolver(cast(dict[str, bytes], {reference.sha256: bytearray()}))


def test_chain_resolver_preserves_order_and_stops_at_first_answer() -> None:
    """Resolution order is declared order and integrity failure has no fallback."""
    payload = b"right payload"
    reference = hash_blob(io.BytesIO(payload))
    calls: list[tuple[str, str | None]] = []
    chain = ChainResolver(
        iter(
            (
                _RecordingResolver("missing", calls, None),
                _RecordingResolver("first-answer", calls, b"wrong payload"),
                _RecordingResolver("not-consulted", calls, payload),
            )
        )
    )
    source = chain.open(reference, "relative/object")
    assert source is not None
    reader = VerifiedReader(source, reference)
    with pytest.raises(ValueError, match="mismatch"):
        reader.read()
    assert calls == [
        ("missing", "relative/object"),
        ("first-answer", "relative/object"),
    ]
    assert ChainResolver(()).open(reference, None) is None


def test_blob_sink_protocol_accepts_a_third_party_store() -> None:
    """Storage remains structural and caller-defined rather than directory-bound."""
    payload = b"stored payload"
    reference = hash_blob(io.BytesIO(payload))
    memory_sink = _MemorySink()
    sink: BlobSink = memory_sink
    href = sink.put(reference, io.BytesIO(payload))
    assert href == f"objects/{reference.sha256}"
    assert memory_sink.payloads == {reference.sha256: payload}


def test_verified_reader_marks_only_a_complete_matching_read() -> None:
    """Sequential EOF verifies once, while bounded partial reads remain untrusted."""
    payload = b"abcdef"
    reference = hash_blob(io.BytesIO(payload))
    source = io.BytesIO(payload)
    reader = VerifiedReader(source, reference)
    assert reader.readable()
    assert reader.seekable()
    assert not reader.writable()
    assert reader.tell() == 0
    assert reader.read(0) == b""
    assert not reader.verified
    assert reader.read(3) == b"abc"
    assert reader.tell() == 3
    assert not reader.verified
    assert reader.read() == b"def"
    assert reader.verified
    assert reader.read() == b""
    assert reader.verified
    reader.close()
    assert reader.closed
    assert source.closed
    reader.close()

    exact_reader = VerifiedReader(io.BytesIO(payload), reference)
    assert exact_reader.read(len(payload)) == payload
    assert not exact_reader.verified
    assert exact_reader.read(1) == b""
    assert exact_reader.verified


def test_verified_reader_supports_binary_reader_methods() -> None:
    """Chunk, buffer, line, and iterator reads all pass through verification."""
    payload = b"a\nb"
    reference = hash_blob(io.BytesIO(payload))
    line_reader = VerifiedReader(io.BytesIO(payload), reference)
    assert line_reader.readline(0) == b""
    assert not line_reader.verified
    assert line_reader.readline() == b"a\n"
    assert not line_reader.verified
    assert line_reader.readline() == b"b"
    assert line_reader.verified

    chunk_reader = VerifiedReader(io.BytesIO(payload), reference)
    assert chunk_reader.read1(1) == b"a"
    buffer = bytearray(2)
    assert chunk_reader.readinto(buffer) == 2
    assert bytes(buffer) == b"\nb"
    assert not chunk_reader.verified
    assert chunk_reader.readinto1(bytearray(1)) == 0
    assert chunk_reader.verified
    with pytest.raises(TypeError, match="writable buffer"):
        VerifiedReader(io.BytesIO(payload), reference).readinto(b"abc")

    iter_reader = VerifiedReader(io.BytesIO(payload), reference)
    assert list(iter_reader) == [b"a\n", b"b"]
    assert iter_reader.verified


def test_verified_reader_refuses_wrong_size_digest_and_binary_shape() -> None:
    """Truncation, extra bytes, content changes, and text reads fail closed."""
    payload = b"payload"
    reference = hash_blob(io.BytesIO(payload))
    with pytest.raises(ValueError, match="expected 8, read 7"):
        VerifiedReader(
            io.BytesIO(payload), BlobRef(reference.sha256, reference.size + 1)
        ).read()
    with pytest.raises(ValueError, match="expected 6, read 7"):
        VerifiedReader(
            io.BytesIO(payload), BlobRef(reference.sha256, reference.size - 1)
        ).read()
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        VerifiedReader(io.BytesIO(b"payloae"), reference).read()
    with pytest.raises(TypeError, match="must return bytes"):
        VerifiedReader(_ObjectReader(), reference).read()


def test_partial_or_seeked_verified_reader_stays_unverified() -> None:
    """Closing or seeking a partial stream never turns a span read into trust."""
    payload = b"payload"
    reference = hash_blob(io.BytesIO(payload))
    partial = VerifiedReader(io.BytesIO(payload), reference)
    assert partial.read(2) == b"pa"
    partial.close()
    assert not partial.verified

    seeked = VerifiedReader(io.BytesIO(payload), reference)
    assert seeked.seek(2) == 2
    assert seeked.read() == b"yload"
    assert not seeked.verified
