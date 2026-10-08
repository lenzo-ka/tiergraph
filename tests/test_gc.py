"""Explicit graph cleanup and bounded edit-history retention."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

import tiergraph.cli as cli
import tiergraph.edit as edit_module
from tests.test_edit_primitives import DomainFixture, fixture
from tiergraph import (
    AttributeValue,
    DurableItemRef,
    Graph,
    GraphValidationError,
    Item,
    ItemRef,
    Journal,
    JournalEditor,
    JournalHorizon,
    LayerFact,
    LayerName,
    OrphanedSubject,
    PolyadicRelationInstance,
    Program,
    PrunedFact,
    Refusal,
    RelationInstance,
    XsdType,
    dump_bytes,
    patch_dumps,
    patch_loads,
)


def _orphaned_graph() -> tuple[DomainFixture, LayerName, LayerFact, LayerFact, Graph]:
    """Return a domain fixture carrying two explicit orphan facts."""
    case = fixture("text")
    layer = LayerName(case.namespace, "legacy")
    first = LayerFact(
        OrphanedSubject(case.unit, ItemRef(case.unit, 7)),
        AttributeValue(case.note, XsdType.STRING, "first"),
    )
    second = LayerFact(
        OrphanedSubject(case.unit, ItemRef(case.unit, 8)),
        AttributeValue(case.note, XsdType.STRING, "second"),
    )
    graph = case.graph.add_layer(layer).put_fact(layer, first).put_fact(layer, second)
    return case, layer, first, second, graph


def test_prune_orphans_is_explicit_reported_and_exactly_undoable() -> None:
    """Cleanup names every removed fact and retains an exact inverse."""
    _, layer, first, second, graph = _orphaned_graph()
    journal = Journal()
    editor = graph.edit(journal=journal)

    editor.prune_orphans()

    cleaned = editor.freeze()
    assert cleaned.layers[-1].facts == ()
    assert journal.records[-1].report.pruned_orphans == (
        PrunedFact(layer, first),
        PrunedFact(layer, second),
    )
    assert journal.records[-1].report.to_data()["pruned_orphans"] == [
        PrunedFact(layer, first).to_data(),
        PrunedFact(layer, second).to_data(),
    ]
    editor.undo()
    assert editor.freeze() == graph
    editor.redo()
    assert editor.freeze() == cleaned


@pytest.mark.parametrize("operation", ["prune_orphans", "compact"])
def test_cleanup_patch_round_trips(operation: str) -> None:
    """Both cleanup edits serialize with executable recorded inverses."""
    _, _, _, _, graph = _orphaned_graph()
    journal = Journal()
    editor = graph.edit(journal=journal)
    getattr(editor, operation)()
    cleaned = editor.freeze()

    patch = patch_loads(patch_dumps(journal.to_patch()))
    assert patch.apply(graph) == cleaned
    assert patch.invert().apply(cleaned) == graph


def test_cleanup_patch_inverse_is_an_admitted_restoration_opcode() -> None:
    """A restoration-only cleanup inverse is valid construction vocabulary."""
    _, _, _, _, graph = _orphaned_graph()
    journal = Journal()
    graph.edit(journal=journal).compact()
    inverse = journal.to_patch().operations[0].inverse

    assert Program((inverse,)).opcodes == (inverse,)


@pytest.mark.parametrize("command", ["prune-orphans", "compact"])
def test_cleanup_cli_writes_the_graph_and_exact_report(
    command: str, tmp_path: Path
) -> None:
    """Both named commands expose explicit cleanup through the CLI."""
    _, layer, first, second, graph = _orphaned_graph()
    source = tmp_path / "graph.json"
    output = tmp_path / "cleaned.json"
    report = tmp_path / "report.json"
    source.write_bytes(dump_bytes(graph))

    assert (
        cli.main(
            [
                "edit",
                str(source),
                command,
                "--report",
                str(report),
                "-o",
                str(output),
            ]
        )
        == 0
    )
    cleaned = json.loads(report.read_text(encoding="utf-8"))
    assert cleaned["reports"][0]["pruned_orphans"] == [
        PrunedFact(layer, first).to_data(),
        PrunedFact(layer, second).to_data(),
    ]


def test_compact_prunes_and_uses_the_graph_value_sharing_contract() -> None:
    """Compaction combines visible cleanup with opt-in value interning."""
    case, _, _, _, graph = _orphaned_graph()
    value = AttributeValue(case.note, XsdType.STRING, "same")
    other = AttributeValue(case.note, XsdType.STRING, "same")
    assert value == other and value is not other
    graph = graph.insert_items(
        case.spare,
        0,
        (Item(attributes=(value,)), Item(attributes=(other,))),
    )

    pruned = graph.prune_orphans()
    compacted = graph.compact()

    assert pruned.layers[-1].facts == ()
    assert compacted.layers[-1].facts == ()
    first = compacted.tiers[1].items[0].attributes[0]
    second = compacted.tiers[1].items[1].attributes[0]
    assert first is second


def test_compact_reinterns_values_retained_by_earlier_inverses() -> None:
    """A retained removed item shares its equal value when later restored."""
    case = fixture("text")
    value = AttributeValue(case.note, XsdType.STRING, "retained")
    other = AttributeValue(case.note, XsdType.STRING, "retained")
    graph = case.graph.insert_items(
        case.spare,
        0,
        (Item(attributes=(value,)), Item(attributes=(other,))),
    )
    journal = Journal(horizon=JournalHorizon(bytes=10_000_000))
    editor = graph.edit(journal=journal)
    editor.remove_item(ItemRef(case.spare, 0))

    editor.compact()
    editor.undo()
    editor.undo()

    restored = editor.freeze().tiers[1].items
    assert restored[0].attributes[0] is restored[1].attributes[0]


def test_compact_history_accepts_mixed_endpoint_reference_forms() -> None:
    """Compaction retains both structural and durable relation endpoints."""
    case = fixture("text")
    graph = replace(
        case.graph,
        relations=(
            RelationInstance(
                case.link,
                DurableItemRef("u0"),
                ItemRef(case.unit, 1),
            ),
        ),
        polyadic_relations=(
            PolyadicRelationInstance(
                case.group,
                (DurableItemRef("u0"),),
                (ItemRef(case.unit, 2),),
            ),
        ),
    )
    journal = Journal()
    editor = graph.edit(journal=journal)
    editor.move_item(ItemRef(case.spare, 0), 2)

    editor.compact()

    assert editor.freeze().relations == graph.relations
    assert editor.freeze().polyadic_relations == graph.polyadic_relations


def test_checkpoint_releases_history_and_starts_a_new_patch_base() -> None:
    """A checkpoint keeps graph state but prevents undo across its boundary."""
    case = fixture("text")
    journal = Journal()
    editor = case.graph.edit(journal=journal)
    editor.move_item(ItemRef(case.spare, 0), 2)
    checkpoint = editor.freeze()

    assert journal.checkpoint() is journal
    assert journal.records == ()
    assert journal.redo_records == ()
    assert journal.to_patch().apply(checkpoint) == checkpoint
    with pytest.raises(GraphValidationError, match="retained history boundary"):
        editor.undo()

    editor.move_item(ItemRef(case.spare, 0), 1)
    changed = editor.freeze()
    assert journal.to_patch().apply(checkpoint) == changed
    editor.undo()
    assert editor.freeze() == checkpoint


def test_count_and_byte_horizons_bound_history_without_changing_edits() -> None:
    """Automatic trimming advances the retained base and keeps replay valid."""
    case = fixture("text")
    journal = Journal(horizon=2)
    editor = case.graph.edit(journal=journal)
    editor.move_item(ItemRef(case.spare, 0), 2)
    first = editor.freeze()
    editor.move_item(ItemRef(case.spare, 0), 1)
    editor.move_item(ItemRef(case.spare, 0), 2)
    final = editor.freeze()

    assert journal.horizon == JournalHorizon(count=2)
    assert len(journal.records) == 2
    assert journal.retained_bytes > 0
    assert journal.to_patch().apply(first) == final
    editor.undo()
    editor.undo()
    assert editor.freeze() == first
    with pytest.raises(GraphValidationError, match="retained history boundary"):
        editor.undo()

    zero = Journal(horizon=JournalHorizon(bytes=0))
    zero_editor = case.graph.edit(journal=zero)
    zero_editor.move_item(ItemRef(case.spare, 0), 2)
    assert zero.records == ()
    assert zero_editor.freeze() != case.graph
    assert zero.retained_bytes == 0

    byte_limit = 100_000
    bounded = Journal(horizon=JournalHorizon(bytes=byte_limit))
    bounded_editor = case.graph.edit(journal=bounded)
    bounded_editor.move_item(ItemRef(case.spare, 0), 2)
    assert bounded.records
    assert bounded.retained_bytes <= byte_limit


def test_dry_run_temporarily_suspends_history_trimming() -> None:
    """A zero-record horizon still rolls a dry run back exactly."""
    case = fixture("text")
    journal = Journal(horizon=0)
    editor = case.graph.edit(journal=journal)

    reports = editor.dry_run(lambda target: target.move_item(ItemRef(case.spare, 0), 2))

    assert len(reports) == 1
    assert editor.freeze() == case.graph
    assert journal.records == ()


def test_checkpoint_refuses_inside_dry_run_and_preserves_state() -> None:
    """A callback cannot discard the records needed to roll its edits back."""
    case = fixture("text")
    journal = Journal()
    editor = case.graph.edit(journal=journal)

    def checkpoint_after_edit(target: JournalEditor) -> None:
        target.move_item(ItemRef(case.spare, 0), 2)
        journal.checkpoint()

    with pytest.raises(GraphValidationError, match="checkpoint during a dry run"):
        editor.dry_run(checkpoint_after_edit)

    assert editor.freeze() == case.graph
    assert journal.records == ()
    assert journal.redo_records == ()


def test_byte_horizon_sizes_each_new_record_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Byte-bound enforcement does not rescan previously retained records."""
    case = fixture("text")
    calls = 0
    retained_size = edit_module._retained_size

    def counted_retained_size(value: object) -> int:
        nonlocal calls
        calls += 1
        return retained_size(value)

    monkeypatch.setattr(edit_module, "_retained_size", counted_retained_size)
    journal = Journal(horizon=JournalHorizon(bytes=10_000_000))
    editor = case.graph.edit(journal=journal)
    for destination in (2, 1, 2):
        editor.move_item(ItemRef(case.spare, 0), destination)

    before = journal.retained_bytes
    assert journal.retained_bytes == before
    assert calls == 3


def test_reused_id_does_not_retarget_a_patch_across_a_checkpoint() -> None:
    """Identified fingerprints distinguish the old and reused-id bases."""
    case = fixture("text")
    original = case.graph.promote_item(ItemRef(case.spare, 0), "reused")[0]
    patch_journal = Journal()
    patch_editor = original.edit(journal=patch_journal)
    patch_editor.replace_item(
        DurableItemRef("reused"), Item("reused", original.tiers[1].items[0].attributes)
    )
    patch = patch_journal.to_patch()

    reuse_journal = Journal()
    reuse_editor = original.edit(journal=reuse_journal)
    reuse_editor.remove_item(DurableItemRef("reused"))
    reuse_journal.checkpoint()
    reuse_editor.promote_item(ItemRef(case.spare, 0), "reused")
    reused = reuse_editor.freeze()

    with pytest.raises(Refusal, match="base fingerprint mismatch"):
        patch.apply(reused)


@pytest.mark.parametrize(
    "value",
    [-1, True, 1.5, "1", JournalHorizon],
)
def test_horizon_validation_refuses_invalid_values(value: object) -> None:
    """Retention policy inputs fail before a journal can attach."""
    with pytest.raises((TypeError, ValueError)):
        Journal(horizon=value)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="needs a count or byte bound"):
        JournalHorizon()
    with pytest.raises(ValueError, match="must be nonnegative"):
        JournalHorizon(bytes=-1)


def test_checkpoint_requires_an_attached_editor() -> None:
    """An unattached journal has no graph state that can become a base."""
    with pytest.raises(GraphValidationError, match="not attached"):
        Journal().checkpoint()
