"""Opt-in edit journal round trips, reports, guards, and clock sessions."""

from __future__ import annotations

import random
import sys
from collections.abc import Callable, Mapping
from dataclasses import fields, is_dataclass, replace
from enum import Enum
from typing import cast

import pytest

import tiergraph.clock as clock_module
import tiergraph.edit as edit_module
from tests.test_clock import (
    BINDING,
    CLOCK,
    RATE,
    SEGMENT,
    SYNTAX,
    UNIT,
    advanced_profile,
    fixture_with_polyadic_parent,
    reference_shape,
)
from tests.test_clock import fixture as clock_fixture
from tests.test_edit_primitives import DomainFixture
from tests.test_edit_primitives import fixture as domain_fixture
from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    BoundaryRef,
    BoundarySide,
    ClockBindingChange,
    ClockEditOperation,
    ClockEditReport,
    ClockJournalEditor,
    ClockProfile,
    ClockRebindingPolicy,
    Displacement,
    DocumentRef,
    DurableBoundaryRef,
    DurableItemRef,
    DurablePolyadicRef,
    DurableRelationRef,
    EditAnnotations,
    EditReport,
    EquivalenceView,
    Graph,
    GraphCarrier,
    GraphValidationError,
    Item,
    ItemRef,
    Journal,
    JournalEditor,
    JsonAttributeValue,
    JsonType,
    LayerFact,
    LayerName,
    NamespaceDeclaration,
    OrphanedSubject,
    PolyadicInstanceRef,
    QualifiedName,
    RelationDeclarationRef,
    RelationInstance,
    RelationInstanceRef,
    RelationTouch,
    Seal,
    TierDeclaration,
    TierRef,
    XsdType,
    equivalent,
)
from tiergraph.core import JsonValue

EditCase = tuple[Graph, Callable[[JournalEditor], object], EquivalenceView]


def _case(case: DomainFixture, operation: str) -> EditCase:
    """Build the precondition and call for one journaled graph primitive."""
    graph = case.graph
    layer = LayerName(case.namespace, "manual")
    fact = LayerFact(
        ItemRef(case.unit, 0),
        AttributeValue(case.note, XsdType.STRING, "checked"),
    )
    temporary_tier = TierDeclaration(QualifiedName(case.namespace, "temporary"), "Temp")
    new_attribute = AttributeDeclaration(
        QualifiedName(case.namespace, "temporary-attribute"),
        AttributeDomain.DOCUMENT,
        XsdType.STRING,
    )
    if operation == "declare":
        return (
            graph,
            lambda editor: editor.declare(temporary_tier, at=1),
            EquivalenceView.EXACT,
        )
    if operation == "undeclare":
        graph = graph.declare(new_attribute)
        return (
            graph,
            lambda editor: editor.undeclare(new_attribute),
            EquivalenceView.EXACT,
        )
    if operation == "promote_item":
        return (
            graph,
            lambda editor: editor.promote_item(ItemRef(case.spare, 0), "spare-0"),
            EquivalenceView.EXACT,
        )
    if operation == "demote_item":
        graph = graph.edit().promote_item(ItemRef(case.spare, 0), "spare-0").freeze()
        return (
            graph,
            lambda editor: editor.demote_item(DurableItemRef("spare-0")),
            EquivalenceView.EXACT,
        )
    if operation == "promote_boundary":
        return (
            graph,
            lambda editor: editor.promote_boundary(BoundaryRef(case.unit, 1), "u1"),
            EquivalenceView.EXACT,
        )
    if operation == "demote_boundary":
        graph = graph.edit().promote_boundary(BoundaryRef(case.unit, 1), "u1").freeze()
        reference = DurableBoundaryRef(DurableItemRef("u1"), BoundarySide.BEFORE)
        return (
            graph,
            lambda editor: editor.demote_boundary(reference),
            EquivalenceView.EXACT,
        )
    if operation == "promote_relation":
        return (
            graph,
            lambda editor: editor.promote_relation(
                RelationInstanceRef(0), "relation-0"
            ),
            EquivalenceView.EXACT,
        )
    if operation == "demote_relation":
        graph = (
            graph.edit().promote_relation(RelationInstanceRef(0), "relation-0").freeze()
        )
        return (
            graph,
            lambda editor: editor.demote_relation(DurableRelationRef("relation-0")),
            EquivalenceView.EXACT,
        )
    if operation == "promote_polyadic":
        return (
            graph,
            lambda editor: editor.promote_relation(
                PolyadicInstanceRef(0), "polyadic-0"
            ),
            EquivalenceView.EXACT,
        )
    if operation == "demote_polyadic":
        graph = (
            graph.edit().promote_relation(PolyadicInstanceRef(0), "polyadic-0").freeze()
        )
        return (
            graph,
            lambda editor: editor.demote_relation(DurablePolyadicRef("polyadic-0")),
            EquivalenceView.EXACT,
        )
    if operation == "seal":
        return graph, lambda editor: editor.seal(case.spare, 1), EquivalenceView.EXACT
    if operation == "unseal":
        graph = replace(graph, seals=(Seal(case.spare, 2),))
        return graph, lambda editor: editor.unseal(case.spare, 1), EquivalenceView.EXACT
    if operation == "drop_seal":
        graph = replace(graph, seals=(Seal(case.spare, 1),))
        return graph, lambda editor: editor.drop_seal(case.spare), EquivalenceView.EXACT
    if operation == "add_layer":
        return graph, lambda editor: editor.add_layer(layer), EquivalenceView.EXACT
    if operation == "remove_layer":
        graph = graph.add_layer(layer)
        return graph, lambda editor: editor.remove_layer(layer), EquivalenceView.EXACT
    if operation == "put_fact":
        graph = graph.add_layer(layer)
        return graph, lambda editor: editor.put_fact(layer, fact), EquivalenceView.EXACT
    if operation == "remove_fact":
        graph = graph.add_layer(layer).put_fact(layer, fact)
        return (
            graph,
            lambda editor: editor.remove_fact(layer, fact.subject, fact.value.name),
            EquivalenceView.EXACT,
        )
    if operation == "set_attribute":
        value = AttributeValue(case.note, XsdType.STRING, "new")
        return (
            graph,
            lambda editor: editor.set_attribute(ItemRef(case.spare, 0), value),
            EquivalenceView.EXACT,
        )
    if operation == "remove_attribute":
        return (
            graph,
            lambda editor: editor.remove_attribute(ItemRef(case.spare, 0), case.note),
            EquivalenceView.EXACT,
        )
    if operation == "insert_item":
        return (
            graph,
            lambda editor: editor.insert_item(case.spare, 1, Item("inserted")),
            EquivalenceView.EXACT,
        )
    if operation == "insert_items":
        return (
            graph,
            lambda editor: editor.insert_items(case.spare, 1, (Item("a"), Item("b"))),
            EquivalenceView.EXACT,
        )
    if operation == "remove_item":
        return (
            graph,
            lambda editor: editor.remove_item(ItemRef(case.spare, 1)),
            EquivalenceView.EXACT,
        )
    if operation == "remove_items":
        return (
            graph,
            lambda editor: editor.remove_items(case.spare, 1, 2),
            EquivalenceView.EXACT,
        )
    if operation == "replace_item":
        item = Item(
            attributes=(AttributeValue(case.note, XsdType.STRING, "replacement"),)
        )
        return (
            graph,
            lambda editor: editor.replace_item(ItemRef(case.spare, 0), item),
            EquivalenceView.EXACT,
        )
    if operation == "move_item":
        return (
            graph,
            lambda editor: editor.move_item(ItemRef(case.spare, 0), 2),
            EquivalenceView.EXACT,
        )
    if operation == "swap_items":
        return (
            graph,
            lambda editor: editor.swap_items(
                ItemRef(case.spare, 0), ItemRef(case.spare, 2)
            ),
            EquivalenceView.EXACT,
        )
    if operation == "add_relation":
        relation = RelationInstance(
            case.link, ItemRef(case.unit, 1), ItemRef(case.unit, 2)
        )
        return (
            graph,
            lambda editor: editor.add_relation(relation, at=0),
            EquivalenceView.EXACT,
        )
    if operation == "remove_relation":
        return (
            graph,
            lambda editor: editor.remove_relation(RelationInstanceRef(0)),
            EquivalenceView.EXACT,
        )
    if operation == "set_endpoints":
        return (
            graph,
            lambda editor: editor.set_endpoints(
                RelationInstanceRef(0), ItemRef(case.unit, 1), ItemRef(case.unit, 2)
            ),
            EquivalenceView.EXACT,
        )
    if operation == "set_polyadic_endpoints":
        return (
            graph,
            lambda editor: editor.set_endpoints(
                PolyadicInstanceRef(0),
                (ItemRef(case.unit, 1),),
                (ItemRef(case.unit, 0),),
            ),
            EquivalenceView.EXACT,
        )
    raise AssertionError(operation)


OPERATIONS = (
    "declare",
    "undeclare",
    "promote_item",
    "demote_item",
    "promote_boundary",
    "demote_boundary",
    "promote_relation",
    "demote_relation",
    "promote_polyadic",
    "demote_polyadic",
    "seal",
    "unseal",
    "drop_seal",
    "add_layer",
    "remove_layer",
    "put_fact",
    "remove_fact",
    "set_attribute",
    "remove_attribute",
    "insert_item",
    "insert_items",
    "remove_item",
    "remove_items",
    "replace_item",
    "move_item",
    "swap_items",
    "add_relation",
    "remove_relation",
    "set_endpoints",
    "set_polyadic_endpoints",
)


@pytest.mark.parametrize("domain", ("text", "music"))
@pytest.mark.parametrize("operation", OPERATIONS)
def test_every_graph_primitive_undoes_and_redoes_on_two_domains(
    domain: str, operation: str
) -> None:
    """Every S3/S4 primitive restores its claimed view and redoes exactly."""
    start, apply, view = _case(domain_fixture(domain), operation)
    journal = Journal(author="editor", reason=operation, iteration=3)
    editor = start.edit(journal=journal)
    apply(editor)
    changed = editor.freeze()
    assert len(journal.records) == 1
    assert journal.records[0].report.annotations == journal.records[0].annotations
    annotations = cast(
        dict[str, JsonValue],
        journal.records[0].report.to_data()["annotations"],
    )
    assert annotations["reason"] == operation
    editor.undo()
    assert equivalent(editor.freeze(), start, view)
    editor.redo()
    assert equivalent(editor.freeze(), changed, EquivalenceView.EXACT)


def test_annotations_are_typed_nested_and_caller_timestamps_only() -> None:
    """Defaults and contextual typed extras round-trip through record and report."""
    case = domain_fixture("text")
    journal = Journal(
        author="kal",
        confidence=0.75,
        timestamp="2026-10-07T12:00:00-04:00",
        fields={"flag": True, "nested": [1, {"kind": "manual"}]},
    )
    editor = case.graph.edit(journal=journal)
    with journal.annotate(reason="correction", stage="hand", pass_number=4):
        editor.move_item(ItemRef(case.spare, 0), 1)
    annotations = journal.reports[0].annotations
    assert annotations.to_data() == {
        "author": "kal",
        "reason": "correction",
        "stage": "hand",
        "confidence": 0.75,
        "timestamp": "2026-10-07T12:00:00-04:00",
        "fields": {
            "flag": True,
            "nested": [1, {"kind": "manual"}],
            "pass_number": 4,
        },
    }
    assert journal.records[0].to_data()["annotations"] == annotations.to_data()


def test_dry_run_rolls_back_success_and_partial_failure_exactly() -> None:
    """Dry-run uses recorded inverses for success and after a later refusal."""
    case = domain_fixture("music")
    journal = Journal()
    editor = case.graph.edit(journal=journal)
    reports = editor.dry_run(
        lambda session: session.move_item(ItemRef(case.spare, 0), 2).set_attribute(
            ItemRef(case.spare, 0),
            AttributeValue(case.note, XsdType.STRING, "dry"),
        )
    )
    assert [report.operation for report in reports] == ["move_item", "set_attribute"]
    assert equivalent(editor.freeze(), case.graph, EquivalenceView.EXACT)
    assert journal.records == ()
    with pytest.raises(GraphValidationError, match="outside tier"):
        editor.dry_run(
            lambda session: session.move_item(ItemRef(case.spare, 0), 2).move_item(
                ItemRef(case.spare, 0), 99
            )
        )
    assert equivalent(editor.freeze(), case.graph, EquivalenceView.EXACT)
    assert journal.records == ()


def test_json_attribute_edits_round_trip_twice_and_in_a_dry_run() -> None:
    """Opaque JSON storage falls back to an atom instead of invalid replace kwargs."""
    case = domain_fixture("text")
    name = QualifiedName(case.namespace, "json-item-note")
    graph = case.graph.declare(
        AttributeDeclaration(name, AttributeDomain.ITEM, JsonType.JSON)
    )
    graph = graph.set_attribute(
        ItemRef(case.spare, 0), JsonAttributeValue(name, {"version": 0})
    )
    journal = Journal()
    editor = graph.edit(journal=journal)
    editor.set_attribute(
        ItemRef(case.spare, 0), JsonAttributeValue(name, {"version": 1})
    )
    editor.set_attribute(
        ItemRef(case.spare, 0), JsonAttributeValue(name, {"version": 2})
    )
    changed = editor.freeze()
    editor.undo()
    editor.undo()
    assert editor.freeze() == graph
    editor.redo()
    editor.redo()
    assert editor.freeze() == changed
    reports = editor.dry_run(
        lambda session: session.set_attribute(
            ItemRef(case.spare, 0), JsonAttributeValue(name, {"version": 3})
        ).set_attribute(
            ItemRef(case.spare, 0), JsonAttributeValue(name, {"version": 4})
        )
    )
    assert [report.operation for report in reports] == [
        "set_attribute",
        "set_attribute",
    ]
    assert editor.freeze() == changed


def test_json_layer_fact_edits_round_trip_twice_and_in_a_dry_run() -> None:
    """Repeated JSON fact replacement remains undoable, redoable, and temporary."""
    case = domain_fixture("text")
    name = QualifiedName(case.namespace, "json-layer-note")
    layer = LayerName(case.namespace, "json-facts")
    subject = ItemRef(case.spare, 0)
    graph = (
        case.graph.declare(
            AttributeDeclaration(name, AttributeDomain.ITEM, JsonType.JSON)
        )
        .add_layer(layer)
        .put_fact(layer, LayerFact(subject, JsonAttributeValue(name, {"version": 0})))
    )
    journal = Journal()
    editor = graph.edit(journal=journal)
    editor.put_fact(layer, LayerFact(subject, JsonAttributeValue(name, {"version": 1})))
    editor.put_fact(layer, LayerFact(subject, JsonAttributeValue(name, {"version": 2})))
    changed = editor.freeze()
    editor.undo()
    editor.undo()
    assert editor.freeze() == graph
    editor.redo()
    editor.redo()
    assert editor.freeze() == changed
    reports = editor.dry_run(
        lambda session: session.put_fact(
            layer, LayerFact(subject, JsonAttributeValue(name, {"version": 3}))
        ).put_fact(layer, LayerFact(subject, JsonAttributeValue(name, {"version": 4})))
    )
    assert [report.operation for report in reports] == ["put_fact", "put_fact"]
    assert editor.freeze() == changed


def test_provenance_stamps_live_touched_content_and_undo_removes_scaffolding() -> None:
    """Requested graph provenance is separate content and part of the inverse."""
    case = domain_fixture("text")
    layer = LayerName("urn:test:journal-provenance", "hand")
    journal = Journal(provenance=layer, stage="hand", reason="fix")
    editor = case.graph.edit(journal=journal)
    editor.set_attribute(
        ItemRef(case.unit, 0),
        AttributeValue(case.note, XsdType.STRING, "stamped"),
    )
    changed = editor.freeze()
    provenance = next(item for item in changed.layers if item.name == layer)
    assert provenance.facts
    assert all(isinstance(fact.value, JsonAttributeValue) for fact in provenance.facts)
    for fact in provenance.facts:
        value = cast(
            dict[str, JsonValue], cast(JsonAttributeValue, fact.value).to_value()
        )
        annotations = cast(dict[str, JsonValue], value["annotations"])
        assert annotations["stage"] == "hand"
    assert any(binding.namespace == layer.vocabulary for binding in changed.namespaces)
    editor.undo()
    assert equivalent(editor.freeze(), case.graph, EquivalenceView.EXACT)
    editor.redo()
    assert equivalent(editor.freeze(), changed, EquivalenceView.EXACT)


def _without_provenance(graph: Graph, layer: LayerName) -> Graph:
    """Remove journal-only output before comparing with a no-provenance control."""
    return replace(
        graph,
        attribute_declarations=tuple(
            declaration
            for declaration in graph.attribute_declarations
            if not (
                declaration.name.namespace == layer.vocabulary
                and declaration.name.local_name.startswith("journal-provenance-")
            )
        ),
        layers=tuple(
            candidate for candidate in graph.layers if candidate.name != layer
        ),
    )


def test_provenance_does_not_change_structural_edit_admission() -> None:
    """Three hundred durable random sequences have zero control divergences."""
    case = domain_fixture("text")
    layer = LayerName(case.namespace, "provenance")
    tiers = tuple(
        replace(
            tier,
            items=tuple(
                Item(f"initial-{index}", item.attributes)
                for index, item in enumerate(tier.items)
            ),
        )
        if tier.declaration.name == case.spare
        else tier
        for tier in case.graph.tiers
    )
    start = replace(case.graph, tiers=tiers)
    operations = (
        "insert",
        "remove",
        "move",
        "swap",
        "replace",
        "promote",
        "demote",
        "promote_boundary",
        "demote_boundary",
    )
    for seed in range(300):
        generator = random.Random(seed)
        plain = start.edit(journal=Journal())
        stamped = start.edit(journal=Journal(provenance=layer))
        next_id = 0
        for step in range(30):
            items = plain.freeze()._tiers_by_name[case.spare].items
            count = len(items)
            operation = generator.choice(operations)
            method: str
            arguments: tuple[object, ...]
            if operation == "insert":
                index = generator.randrange(count + 1)
                argument = Item(f"seed-{seed}-insert-{next_id}")
                next_id += 1
                method = "insert_item"
                arguments = (case.spare, index, argument)
            elif operation == "remove" and count > 2:
                index = generator.randrange(count)
                method = "remove_item"
                arguments = (ItemRef(case.spare, index),)
            elif operation == "remove":
                continue
            elif operation == "move":
                source = generator.randrange(count)
                target = generator.randrange(count)
                method = "move_item"
                arguments = (ItemRef(case.spare, source), target)
            elif operation == "swap":
                left = generator.randrange(count)
                right = generator.randrange(count)
                method = "swap_items"
                arguments = (
                    ItemRef(case.spare, left),
                    ItemRef(case.spare, right),
                )
            elif operation == "replace":
                index = generator.randrange(count)
                current = items[index]
                replacement = Item(
                    current.durable_id,
                    (
                        AttributeValue(
                            case.note, XsdType.STRING, f"seed-{seed}-step-{step}"
                        ),
                    ),
                )
                method = "replace_item"
                arguments = (ItemRef(case.spare, index), replacement)
            elif operation == "promote":
                anonymous = [
                    index for index, item in enumerate(items) if item.durable_id is None
                ]
                if not anonymous:
                    continue
                index = generator.choice(anonymous)
                durable_id = f"seed-{seed}-promoted-{next_id}"
                next_id += 1
                method = "promote_item"
                arguments = (ItemRef(case.spare, index), durable_id)
            elif operation == "demote":
                durable_ids = [
                    item.durable_id for item in items if item.durable_id is not None
                ]
                if not durable_ids:
                    continue
                durable_id = generator.choice(durable_ids)
                method = "demote_item"
                arguments = (DurableItemRef(durable_id),)
            else:
                boundary_candidates = [
                    (index, item.durable_id)
                    for index, item in enumerate(items[1:], 1)
                    if item.durable_id is not None
                ]
                if not boundary_candidates:
                    continue
                index, durable_id = generator.choice(boundary_candidates)
                boundary = DurableBoundaryRef(
                    DurableItemRef(durable_id), BoundarySide.BEFORE
                )
                if operation == "promote_boundary":
                    method = "promote_boundary"
                    arguments = (BoundaryRef(case.spare, index), durable_id)
                else:
                    method = "demote_boundary"
                    arguments = (boundary,)
            for editor in (plain, stamped):
                call = cast(Callable[..., object], getattr(editor, method))
                call(*arguments)
            assert _without_provenance(stamped.freeze(), layer) == plain.freeze()


def test_nonjournal_fact_in_provenance_layer_keeps_kernel_protection() -> None:
    """Retirement is limited to journal-shaped facts, not every fact in its layer."""
    case = domain_fixture("text")
    layer = LayerName(case.namespace, "provenance")
    graph = (
        case.graph.edit()
        .promote_item(ItemRef(case.spare, 0), "durable-spare")
        .freeze()
        .add_layer(layer)
        .put_fact(
            layer,
            LayerFact(
                DurableItemRef("durable-spare"),
                AttributeValue(case.note, XsdType.STRING, "external"),
            ),
        )
    )
    journal = Journal(provenance=layer)
    editor = graph.edit(journal=journal)
    with pytest.raises(GraphValidationError, match="invalidate a live fact"):
        editor.remove_item(DurableItemRef("durable-spare"))
    assert editor.freeze() is graph
    assert journal.records == ()

    provenance_name = QualifiedName(case.namespace, "journal-provenance-item")
    spoof = (
        case.graph.edit()
        .promote_item(ItemRef(case.spare, 0), "spoofed-spare")
        .freeze()
        .declare(
            AttributeDeclaration(provenance_name, AttributeDomain.ITEM, JsonType.JSON)
        )
        .add_layer(layer)
        .put_fact(
            layer,
            LayerFact(
                DurableItemRef("spoofed-spare"),
                JsonAttributeValue(
                    provenance_name,
                    {"operation": "external", "annotations": {}},
                ),
            ),
        )
    )
    journal = Journal(provenance=layer)
    editor = spoof.edit(journal=journal)
    editor.set_attribute(
        DurableItemRef("spoofed-spare"),
        AttributeValue(case.note, XsdType.STRING, "temporarily stamped"),
    )
    editor.undo()
    with pytest.raises(GraphValidationError, match="invalidate a live fact"):
        editor.remove_item(DurableItemRef("spoofed-spare"))
    assert editor.freeze() == spoof


def test_protected_layer_refuses_subject_or_fact_change_before_writing() -> None:
    """Protected hand facts constrain later automatic edits atomically."""
    case = domain_fixture("text")
    layer = LayerName(case.namespace, "manual")
    fact = LayerFact(
        ItemRef(case.spare, 0),
        AttributeValue(case.note, XsdType.STRING, "fixed"),
    )
    graph = case.graph.add_layer(layer).put_fact(layer, fact)
    journal = Journal().protect(layer)
    editor = graph.edit(journal=journal)
    with pytest.raises(GraphValidationError, match="protected by layer"):
        editor.set_attribute(
            ItemRef(case.spare, 0),
            AttributeValue(case.note, XsdType.STRING, "automatic"),
        )
    assert editor.freeze() is graph
    assert journal.records == ()
    with pytest.raises(GraphValidationError, match="change protected layer"):
        editor.remove_fact(layer, fact.subject, fact.value.name)
    assert editor.freeze() is graph
    editor.set_attribute(
        ItemRef(case.spare, 1),
        AttributeValue(case.note, XsdType.STRING, "allowed"),
    )
    assert len(journal.records) == 1


def test_protection_checks_the_final_provenance_candidate() -> None:
    """A journal cannot write its own provenance into a protected layer."""
    case = domain_fixture("text")
    layer = LayerName(case.namespace, "provenance")
    seed = case.graph.edit(journal=Journal(provenance=layer))
    seed.set_attribute(
        ItemRef(case.unit, 0),
        AttributeValue(case.note, XsdType.STRING, "seed"),
    )
    graph = seed.freeze()
    journal = Journal(provenance=layer).protect(layer)
    editor = graph.edit(journal=journal)
    with pytest.raises(GraphValidationError, match="change protected layer"):
        editor.set_attribute(
            ItemRef(case.unit, 1),
            AttributeValue(case.note, XsdType.STRING, "changed"),
        )
    assert editor.freeze() is graph
    assert journal.records == ()


def test_clock_journal_round_trips_bound_edits_and_reports_policy() -> None:
    """Clock-session inverses restore bindings that primitive pairs cannot."""
    start = clock_fixture()
    journal = Journal(author="aligner")
    editor = ClockProfile(start, CLOCK, BINDING, RATE, UNIT).edit(
        "keep-earlier", journal=journal
    )
    assert isinstance(editor, ClockJournalEditor)
    editor.remove_item(ItemRef(SEGMENT, 0))
    changed = editor.freeze()
    assert journal.reports[0].clock_reports == editor.reports
    assert journal.reports[0].clock_reports[0].changes
    editor.undo()
    assert equivalent(editor.freeze(), start, EquivalenceView.EXACT)
    assert editor.profile.graph == start
    editor.redo()
    assert equivalent(editor.freeze(), changed, EquivalenceView.EXACT)
    assert editor.profile.graph == changed


@pytest.mark.parametrize("with_provenance", (False, True))
def test_clock_removal_preserves_relation_layer_facts(
    with_provenance: bool,
) -> None:
    """Temporary binding removal remaps facts before intermediate validation."""
    graph = clock_fixture()
    note = QualifiedName(graph.namespaces[0].namespace, "relation-note")
    layer = LayerName(graph.namespaces[0].namespace, "relation-facts")
    graph = (
        graph.declare(
            AttributeDeclaration(
                note, AttributeDomain.RELATION_INSTANCE, XsdType.STRING
            )
        )
        .add_layer(layer)
        .put_fact(
            layer,
            LayerFact(
                RelationInstanceRef(2),
                AttributeValue(note, XsdType.STRING, "last-binding"),
            ),
        )
    )
    provenance = (
        LayerName(graph.namespaces[0].namespace, "provenance")
        if with_provenance
        else None
    )
    editor = ClockProfile(graph, CLOCK, BINDING, RATE, UNIT).edit(
        "keep-earlier", journal=Journal(provenance=provenance)
    )
    editor.remove_item(ItemRef(SEGMENT, 0))
    fact_layer = next(item for item in editor.freeze().layers if item.name == layer)
    assert fact_layer.facts == (
        LayerFact(
            RelationInstanceRef(1),
            AttributeValue(note, XsdType.STRING, "last-binding"),
        ),
    )


def test_clock_provenance_allows_repeated_durable_removals_and_round_trips() -> None:
    """Clock sessions retire their own item facts on every successive removal."""
    graph = clock_fixture()
    layer = LayerName(graph.namespaces[0].namespace, "provenance")
    plain = ClockProfile(graph, CLOCK, BINDING, RATE, UNIT).edit(
        "keep-earlier", journal=Journal()
    )
    journal = Journal(provenance=layer)
    stamped = ClockProfile(graph, CLOCK, BINDING, RATE, UNIT).edit(
        "keep-earlier", journal=journal
    )
    for editor in (plain, stamped):
        editor.swap_items(ItemRef(SEGMENT, 0), ItemRef(SEGMENT, 1))
    for _ in range(2):
        plain.remove_item(ItemRef(SEGMENT, 0))
        stamped.remove_item(ItemRef(SEGMENT, 0))
        assert _without_provenance(stamped.freeze(), layer) == plain.freeze()
    changed = stamped.freeze()
    for _ in range(3):
        stamped.undo()
    assert stamped.freeze() == graph
    for _ in range(3):
        stamped.redo()
    assert stamped.freeze() == changed


def test_clock_report_uses_native_positions_for_equal_items() -> None:
    """Clock-session reports do not infer equal-item identity from value matching."""
    graph = reference_shape()
    tiers = tuple(
        replace(tier, items=(Item(), Item(), Item()))
        if tier.declaration.name == SYNTAX
        else tier
        for tier in graph.tiers
    )
    graph = replace(graph, tiers=tiers)
    journal = Journal()
    editor = advanced_profile(graph).edit(journal=journal)
    editor.insert_item(SYNTAX, 0, Item())
    displacement = journal.reports[0].displacement
    assert displacement.items[ItemRef(SYNTAX, 0)] == ItemRef(SYNTAX, 1)
    assert displacement.items[ItemRef(SYNTAX, 1)] == ItemRef(SYNTAX, 2)
    assert displacement.items[ItemRef(SYNTAX, 2)] == ItemRef(SYNTAX, 3)


def test_clock_journal_requires_policy_and_dry_run_restores_exactly() -> None:
    """Journal attachment does not weaken clock policy or dry-run identity."""
    start = clock_fixture()
    journal = Journal()
    editor = ClockProfile(start, CLOCK, BINDING, RATE, UNIT).edit(journal=journal)
    with pytest.raises(GraphValidationError, match="requires a named rebinding policy"):
        editor.move_item(ItemRef(SEGMENT, 0), 1)
    assert editor.freeze() is start
    assert journal.records == ()

    journal = Journal()
    editor = ClockProfile(start, CLOCK, BINDING, RATE, UNIT).edit(
        "drop-to-provisional", journal=journal
    )
    reports = editor.dry_run(
        lambda session: session.swap_items(ItemRef(SEGMENT, 0), ItemRef(SEGMENT, 1))
    )
    assert reports[0].clock_reports[0].needs_realignment
    assert equivalent(editor.freeze(), start, EquivalenceView.EXACT)
    assert editor.profile.graph == start
    editor.move_item(ItemRef(SEGMENT, 0), 0)
    assert editor.displacement().items[ItemRef(SEGMENT, 0)] == ItemRef(SEGMENT, 0)


def test_displacement_data_and_journal_refusals_are_public_and_deterministic() -> None:
    """Position reports encode all spaces and empty history refuses clearly."""
    case = domain_fixture("text")
    data = Displacement.stationary(case.graph).to_data()
    assert set(data) == {
        "items",
        "boundaries",
        "relations",
        "polyadic_relations",
        "departed_items",
        "departed_boundaries",
        "departed_relations",
        "departed_polyadic_relations",
    }
    journal = Journal(EditAnnotations(tool="test"))
    with pytest.raises(GraphValidationError, match="not attached"):
        journal.undo()
    with pytest.raises(GraphValidationError, match="not attached"):
        journal.redo()
    editor = case.graph.edit(journal=journal)
    assert editor.displacement().to_data() == data
    with pytest.raises(GraphValidationError, match="no operation to undo"):
        editor.undo()
    with pytest.raises(GraphValidationError, match="no operation to redo"):
        editor.redo()
    with pytest.raises(GraphValidationError, match="already attached"):
        case.graph.edit(journal=journal)


def test_declaration_cascade_is_one_undoable_journal_record() -> None:
    """The S4 derived cascade gains its complete inverse from the journal."""
    case = domain_fixture("music")
    journal = Journal(reason="remove domain")
    editor = case.graph.edit(journal=journal)
    editor.undeclare_with_contents(case.spare)
    changed = editor.freeze()
    assert len(journal.records) == 1
    assert journal.records[0].operation == "undeclare_with_contents"
    editor.undo()
    assert equivalent(editor.freeze(), case.graph, EquivalenceView.EXACT)
    editor.redo()
    assert equivalent(editor.freeze(), changed, EquivalenceView.EXACT)

    layer = LayerName(case.namespace, "cascade-provenance")
    provenance_journal = Journal(provenance=layer)
    provenance_editor = case.graph.edit(journal=provenance_journal)
    provenance_editor.set_attribute(
        ItemRef(case.unit, 0),
        AttributeValue(case.note, XsdType.STRING, "survives-unrelated-cascade"),
    )
    provenance_editor.undeclare_with_contents(case.spare)
    provenance = next(
        item for item in provenance_editor.freeze().layers if item.name == layer
    )
    assert len(provenance.facts) == 1


def test_relation_and_graph_carrier_reports_name_touched_positions() -> None:
    """Reports cover binary, polyadic, and sealed carrier position changes."""
    case = domain_fixture("text")
    journal = Journal()
    editor = case.graph.edit(journal=journal)
    editor.remove_relation(RelationInstanceRef(0))
    assert journal.reports[-1].touched_relations[0].carrier == "relations"
    editor.remove_relation(PolyadicInstanceRef(0))
    assert journal.reports[-1].touched_relations[0].carrier == "polyadic_relations"
    editor.seal(GraphCarrier.RELATIONS, 0)
    assert journal.records[-1].inverse.operation == "restore_seal"


def test_zero_step_bulk_edits_still_record_and_round_trip() -> None:
    """A successful no-op is still one annotated per-operation journal event."""
    case = domain_fixture("text")
    journal = Journal(stage="automatic")
    editor = case.graph.edit(journal=journal)
    editor.insert_items(case.spare, 0, ())
    unchanged = editor.freeze()
    assert unchanged == case.graph
    assert journal.reports[0].touched_items == ()
    editor.undo()
    assert editor.freeze() == case.graph
    editor.redo()
    assert editor.freeze() == case.graph


def test_annotation_validation_rejects_non_json_and_invalid_scalars() -> None:
    """Annotation metadata refuses values that cannot round-trip faithfully."""
    with pytest.raises(TypeError, match="author"):
        EditAnnotations(author=cast(str, 1))
    with pytest.raises(TypeError, match="confidence"):
        EditAnnotations(confidence=cast(float, True))
    with pytest.raises(ValueError, match="finite"):
        EditAnnotations(confidence=float("inf"))
    with pytest.raises(TypeError, match="iteration"):
        EditAnnotations(iteration=cast(int, True))
    with pytest.raises(TypeError, match="string-keyed"):
        EditAnnotations(fields=cast(Mapping[str, JsonValue], cast(object, {1: "bad"})))
    with pytest.raises(GraphValidationError, match="ordinary JSON"):
        EditAnnotations(fields={"bad": cast(JsonValue, object())})


def test_provenance_reuses_existing_namespace_and_refuses_declaration_conflict() -> (
    None
):
    """Provenance creates only missing support and never overwrites a schema."""
    case = domain_fixture("text")
    layer = LayerName(case.namespace, "automatic")
    journal = Journal(provenance=layer)
    editor = case.graph.edit(journal=journal)
    editor.set_attribute(
        ItemRef(case.unit, 0),
        AttributeValue(case.note, XsdType.STRING, "changed"),
    )
    assert len(editor.freeze().namespaces) == len(case.graph.namespaces)

    conflict = AttributeDeclaration(
        QualifiedName(case.namespace, "journal-provenance-item"),
        AttributeDomain.ITEM,
        XsdType.STRING,
    )
    graph = case.graph.declare(conflict)
    editor = graph.edit(journal=Journal(provenance=layer))
    with pytest.raises(GraphValidationError, match="conflicts"):
        editor.set_attribute(
            ItemRef(case.unit, 0),
            AttributeValue(case.note, XsdType.STRING, "changed"),
        )
    assert editor.freeze() is graph


def test_protect_absent_layer_refuses_before_adopting_candidate() -> None:
    """A misspelled protection target cannot silently provide no protection."""
    case = domain_fixture("text")
    missing = LayerName(case.namespace, "missing")
    editor = case.graph.edit(journal=Journal().protect(missing))
    with pytest.raises(GraphValidationError, match="is absent"):
        editor.move_item(ItemRef(case.spare, 0), 1)
    assert editor.freeze() is case.graph

    graph = case.graph.add_layer(missing)
    editor = graph.edit(journal=Journal().protect(missing))
    with pytest.raises(GraphValidationError, match="would change protected layer"):
        editor.remove_layer(missing)
    assert editor.freeze() == graph


def test_inverse_detects_out_of_band_carrier_divergence() -> None:
    """Recorded deltas refuse rather than overwrite an unexpected current state."""
    case = domain_fixture("text")
    journal = Journal()
    editor = case.graph.edit(journal=journal)
    editor.add_layer(LayerName(case.namespace, "first"))
    record = journal.records[0]
    editor._graph = editor.freeze().add_layer(LayerName(case.namespace, "outside"))
    with pytest.raises(GraphValidationError, match="no longer matches"):
        record.inverse._delta.reverse(editor.freeze())
    editor._graph = case.graph.add_layer(LayerName(case.namespace, "outside"))
    with pytest.raises(GraphValidationError, match="no longer matches"):
        record.inverse._delta.forward(editor.freeze())


def test_failed_undo_and_redo_keep_their_records() -> None:
    """History moves between stacks only after a restore succeeds."""
    case = domain_fixture("text")
    first = LayerName(case.namespace, "first")
    outside = LayerName(case.namespace, "outside")
    journal = Journal()
    editor = case.graph.edit(journal=journal)
    editor.add_layer(first)
    record = journal.records[0]
    editor._graph = editor.freeze().add_layer(outside)
    with pytest.raises(GraphValidationError, match="cannot undo"):
        editor.undo()
    assert journal.records == (record,)
    assert journal.redo_records == ()

    editor._graph = editor.freeze().remove_layer(outside)
    editor.undo()
    editor._graph = editor.freeze().add_layer(outside)
    with pytest.raises(GraphValidationError, match="cannot redo"):
        editor.redo()
    assert len(journal.records) == 0
    assert len(journal.redo_records) == 1
    assert journal.redo_records[0] is record


def test_dry_run_rollback_failure_preserves_the_original_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rollback diagnostics are chained without replacing the operation error."""
    case = domain_fixture("text")
    journal = Journal()
    editor = case.graph.edit(journal=journal)

    def fail(session: JournalEditor) -> None:
        session.add_layer(LayerName(case.namespace, "temporary"))

        def refuse_rollback() -> None:
            raise GraphValidationError("rollback refused")

        monkeypatch.setattr(journal, "undo", refuse_rollback)
        raise ValueError("operation failed")

    with pytest.raises(ValueError, match="operation failed") as failure:
        editor.dry_run(fail)
    assert any("rollback also failed" in note for note in failure.value.__notes__)
    assert journal.records == ()
    assert journal.redo_records == ()


def test_successful_dry_run_rollback_failure_restores_history_stacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A success-path rollback failure is chained after restoring both stacks."""
    case = domain_fixture("text")
    journal = Journal()
    editor = case.graph.edit(journal=journal)
    editor.add_layer(LayerName(case.namespace, "prior"))
    prior_record = editor.undo()

    def succeed_then_refuse_rollback(session: JournalEditor) -> None:
        session.add_layer(LayerName(case.namespace, "temporary"))

        def refuse_rollback() -> None:
            raise GraphValidationError("rollback refused")

        monkeypatch.setattr(journal, "undo", refuse_rollback)

    with pytest.raises(
        GraphValidationError, match="operation succeeded but rollback failed"
    ) as failure:
        editor.dry_run(succeed_then_refuse_rollback)
    assert isinstance(failure.value.__cause__, GraphValidationError)
    assert "rollback refused" in str(failure.value.__cause__)
    assert journal.records == ()
    assert journal.redo_records == (prior_record,)


def test_journal_record_data_names_inverse_carriers_without_graph_snapshots() -> None:
    """Public inverse data is operation-shaped and retains no Graph value."""
    case = domain_fixture("text")
    journal = Journal()
    editor = case.graph.edit(journal=journal)
    editor.move_item(ItemRef(case.spare, 0), 1)
    record = journal.records[0]
    assert record.inverse.to_data()["operation"] == "move_item"
    assert "tiers" in record.inverse.carriers
    assert all(
        not isinstance(change.delta, Graph) for change in record.inverse._delta.changes
    )


def test_namespace_provenance_prefix_avoids_existing_journal_prefix() -> None:
    """Synthesized provenance namespace prefixes remain unique."""
    case = domain_fixture("text")
    graph = replace(
        case.graph,
        namespaces=(
            *case.graph.namespaces,
            NamespaceDeclaration("journal", "urn:already-used"),
        ),
    )
    layer = LayerName("urn:new-provenance", "hand")
    editor = graph.edit(journal=Journal(provenance=layer))
    editor.set_attribute(
        ItemRef(case.unit, 0),
        AttributeValue(case.note, XsdType.STRING, "stamped"),
    )
    assert any(
        binding.prefix == "journal2" and binding.namespace == layer.vocabulary
        for binding in editor.freeze().namespaces
    )


def test_report_encodes_detached_subject_and_every_clock_endpoint_shape() -> None:
    """Report JSON covers orphan subjects and all clock endpoint encodings."""
    case = domain_fixture("text")
    orphan = OrphanedSubject(case.unit, ItemRef(case.unit, 99))
    displacement = Displacement.stationary(case.graph)
    change = ClockBindingChange(
        BoundaryRef(case.unit, 0),
        BoundaryRef(case.unit, 1),
        ItemRef(case.unit, 0),
        DurableItemRef("u0"),
        DurableBoundaryRef(case.unit, BoundarySide.BEFORE),
        DurableBoundaryRef(DurableItemRef("u0"), BoundarySide.AFTER),
        0,
        1,
        True,
    )
    clock = ClockEditReport(
        ClockEditOperation.ITEM_MOVE,
        ClockRebindingPolicy.KEEP_EARLIER,
        case.unit,
        (change,),
        False,
    )
    report = EditReport(
        "probe",
        (),
        (),
        (RelationTouch("relations", 0),),
        (orphan,),
        displacement,
        EditAnnotations(),
        (clock,),
    )
    data = report.to_data()
    assert data["detached_references"]
    assert report.detached_content is None
    assert "detached_content" not in data
    clock_reports = cast(list[dict[str, JsonValue]], data["clock_reports"])
    changes = cast(list[dict[str, JsonValue]], clock_reports[0]["changes"])
    previous_source = cast(dict[str, JsonValue], changes[0]["previous_source"])
    source = cast(dict[str, JsonValue], changes[0]["source"])
    previous_target = cast(dict[str, JsonValue], changes[0]["previous_target"])
    target = cast(dict[str, JsonValue], changes[0]["target"])
    assert previous_source["kind"] == "item"
    assert source["kind"] == "durable_item"
    assert previous_target["kind"] == "durable_boundary"
    assert target["kind"] == "durable_boundary"

    tier_boundary = DurableBoundaryRef(case.unit, BoundarySide.BEFORE)
    item_boundary = DurableBoundaryRef(DurableItemRef("u0"), BoundarySide.AFTER)
    tier_anchor = cast(
        dict[str, JsonValue], edit_module._endpoint_data(tier_boundary)["anchor"]
    )
    item_anchor = cast(
        dict[str, JsonValue], edit_module._endpoint_data(item_boundary)["anchor"]
    )
    assert tier_anchor["kind"] == "tier"
    assert item_anchor["kind"] == "item"


def test_detached_references_report_departed_subjects_not_fact_edits() -> None:
    """Detachment names lost subjects, not removed or replaced facts at live ones."""
    case = domain_fixture("text")
    layer = LayerName(case.namespace, "manual")
    orphan = OrphanedSubject(case.unit, ItemRef(case.unit, 99))
    orphan_fact = LayerFact(
        orphan,
        AttributeValue(case.note, XsdType.STRING, "historical"),
    )
    journal = Journal()
    editor = case.graph.add_layer(layer).edit(journal=journal)
    editor.put_fact(layer, orphan_fact)
    assert journal.reports[-1].detached_references == ()
    editor.remove_fact(layer, orphan, case.note)
    assert journal.reports[-1].detached_references == ()

    editor.put_fact(
        layer,
        LayerFact(
            ItemRef(case.spare, 0),
            AttributeValue(case.note, XsdType.STRING, "first"),
        ),
    )
    editor.put_fact(
        layer,
        LayerFact(
            ItemRef(case.spare, 0),
            AttributeValue(case.note, XsdType.STRING, "second"),
        ),
    )
    assert journal.reports[-1].detached_references == ()

    live_fact = LayerFact(
        ItemRef(case.spare, 0),
        AttributeValue(case.note, XsdType.STRING, "live"),
    )
    graph = case.graph.add_layer(layer).put_fact(layer, live_fact)
    journal = Journal()
    editor = graph.edit(journal=journal)
    editor.undeclare_with_contents(case.spare)
    assert ItemRef(case.spare, 0) in journal.reports[-1].detached_references


def test_touch_detection_covers_document_tier_and_relation_declaration_subjects() -> (
    None
):
    """Provenance can stamp every nonstructural public subject domain."""
    case = domain_fixture("text")
    document_name = QualifiedName(case.namespace, "document-note")
    tier_name = QualifiedName(case.namespace, "tier-note")
    relation_name = QualifiedName(case.namespace, "relation-declaration-note")
    graph = (
        case.graph.declare(
            AttributeDeclaration(
                document_name, AttributeDomain.DOCUMENT, XsdType.STRING
            )
        )
        .declare(AttributeDeclaration(tier_name, AttributeDomain.TIER, XsdType.STRING))
        .declare(
            AttributeDeclaration(
                relation_name,
                AttributeDomain.RELATION_DECLARATION,
                XsdType.STRING,
            )
        )
    )
    layer = LayerName(case.namespace, "provenance")
    journal = Journal(provenance=layer)
    editor = graph.edit(journal=journal)
    editor.set_attribute(None, AttributeValue(document_name, XsdType.STRING, "doc"))
    editor.set_attribute(case.unit, AttributeValue(tier_name, XsdType.STRING, "tier"))
    editor.set_attribute(
        case.link, AttributeValue(relation_name, XsdType.STRING, "relation")
    )
    domains = {fact.value.name.local_name for fact in editor.freeze().layers[-1].facts}
    assert domains >= {
        "journal-provenance-document",
        "journal-provenance-tier",
        "journal-provenance-relation-declaration",
    }
    editor.remove_attribute(case.unit, tier_name)
    assert len(editor.freeze().layers) == 1


def test_provenance_handles_no_touch_existing_support_and_durable_relations() -> None:
    """Repeated stamping reuses support and durable relation subjects."""
    case = domain_fixture("text")
    layer = LayerName(case.namespace, "provenance")
    journal = Journal(provenance=layer)
    editor = case.graph.edit(journal=journal)
    editor.insert_items(case.spare, 0, ())
    assert editor.freeze() == case.graph
    editor.promote_relation(RelationInstanceRef(0), "binary")
    first = editor.freeze()
    assert any(
        fact.subject == DurableRelationRef("binary") for fact in first.layers[-1].facts
    )
    editor.set_endpoints(
        DurableRelationRef("binary"), ItemRef(case.unit, 1), ItemRef(case.unit, 2)
    )
    assert len(editor.freeze().namespaces) == len(case.graph.namespaces)

    editor.promote_relation(PolyadicInstanceRef(0), "polyadic")
    editor.set_endpoints(
        DurablePolyadicRef("polyadic"),
        (ItemRef(case.unit, 1),),
        (ItemRef(case.unit, 2),),
    )
    assert any(
        fact.subject == DurablePolyadicRef("polyadic")
        for fact in editor.freeze().layers[-1].facts
    )


def test_protection_resolves_durable_subjects_and_ignores_orphans() -> None:
    """Protection follows durable subjects while historical orphans stay inert."""
    case = domain_fixture("text")
    item = DurableItemRef("u0")
    boundary = DurableBoundaryRef(item, BoundarySide.BEFORE)
    binary_graph = case.graph.promote_relation(RelationInstanceRef(0), "binary")[0]
    poly_graph = binary_graph.promote_relation(PolyadicInstanceRef(0), "polyadic")[0]
    layer = LayerName(case.namespace, "manual")
    facts = (
        LayerFact(item, AttributeValue(case.note, XsdType.STRING, "item")),
        LayerFact(
            boundary,
            AttributeValue(case.boundary_note, XsdType.STRING, "boundary"),
        ),
        LayerFact(
            DurableRelationRef("binary"),
            AttributeValue(case.relation_note, XsdType.STRING, "binary"),
        ),
        LayerFact(
            DurablePolyadicRef("polyadic"),
            AttributeValue(case.relation_note, XsdType.STRING, "polyadic"),
        ),
        LayerFact(
            OrphanedSubject(case.spare, ItemRef(case.spare, 99)),
            AttributeValue(case.note, XsdType.STRING, "orphan"),
        ),
    )
    graph = poly_graph.add_layer(layer)
    for fact in facts:
        graph = graph.put_fact(layer, fact)
    editor = graph.edit(journal=Journal().protect(layer))
    operations: tuple[Callable[[], object], ...] = (
        lambda: editor.set_attribute(
            item, AttributeValue(case.note, XsdType.STRING, "changed")
        ),
        lambda: editor.set_attribute(
            boundary,
            AttributeValue(case.boundary_note, XsdType.STRING, "changed"),
        ),
        lambda: editor.set_endpoints(
            DurableRelationRef("binary"),
            ItemRef(case.unit, 1),
            ItemRef(case.unit, 2),
        ),
        lambda: editor.set_endpoints(
            DurablePolyadicRef("polyadic"),
            (ItemRef(case.unit, 1),),
            (ItemRef(case.unit, 2),),
        ),
    )
    for operation in operations:
        with pytest.raises(GraphValidationError, match="protected by layer"):
            operation()
    editor.set_attribute(
        ItemRef(case.spare, 1),
        AttributeValue(case.note, XsdType.STRING, "allowed"),
    )


def test_protection_follows_unchanged_content_to_remapped_coordinates() -> None:
    """Coordinate shifts do not count as protected fact or subject changes."""
    case = domain_fixture("text")
    layer = LayerName(case.namespace, "manual")
    fact = LayerFact(
        ItemRef(case.spare, 0),
        AttributeValue(case.note, XsdType.STRING, "fixed"),
    )
    graph = case.graph.add_layer(layer).put_fact(layer, fact)
    editor = graph.edit(journal=Journal().protect(layer))
    editor.insert_item(case.spare, 0, Item())
    moved_layer = next(item for item in editor.freeze().layers if item.name == layer)
    assert moved_layer.facts == (LayerFact(ItemRef(case.spare, 1), fact.value),)


def test_annotation_context_accepts_object_and_rejects_nonmapping_fields() -> None:
    """Annotation contexts merge explicit objects and validate their extras."""
    case = domain_fixture("text")
    journal = Journal(reason="default")
    editor = case.graph.edit(journal=journal)
    with journal.annotate(
        EditAnnotations(reason="object", fields={"left": 1}),
        fields={"right": 2},
    ):
        editor.move_item(ItemRef(case.spare, 0), 1)
    assert journal.reports[0].annotations.fields == {"left": 1, "right": 2}
    editor.undo()
    assert journal.redo_records
    editor.redo()
    with pytest.raises(TypeError, match="must be a mapping"):
        with journal.annotate(fields=cast(object, 1)):
            pass


def test_invalid_ordered_inputs_refuse_through_both_journal_editors() -> None:
    """Wrappers preserve the native ordered-input refusal branches."""
    case = domain_fixture("text")
    editor = case.graph.edit(journal=Journal())
    with pytest.raises(GraphValidationError, match="ordered iterable"):
        editor.insert_items(case.spare, 0, {Item("bad")})

    profile = ClockProfile(clock_fixture(), CLOCK, BINDING, RATE, UNIT)
    clock = profile.edit("keep-earlier", journal=Journal())
    with pytest.raises(GraphValidationError, match="ordered iterable"):
        clock.insert_items(SEGMENT, 0, {Item("bad")})


def test_clock_journal_all_operations_and_retired_session_state() -> None:
    """The clock wrapper exposes every clock-session operation and retirement."""
    start = clock_fixture()

    def session() -> ClockJournalEditor:
        return ClockProfile(start, CLOCK, BINDING, RATE, UNIT).edit(
            "keep-earlier", journal=Journal()
        )

    inserted = session()
    inserted.insert_item(SEGMENT, 1, Item("inserted"))
    assert inserted.reports
    inserted_many = session()
    inserted_many.insert_items(SEGMENT, 1, (Item("a"), Item("b")))
    removed_many = session()
    removed_many.remove_items(SEGMENT, 0, 2)
    assert removed_many.freeze().tiers[1].items == ()
    swapped = session()
    swapped.swap_items(ItemRef(SEGMENT, 0), ItemRef(SEGMENT, 1))

    parent_graph = fixture_with_polyadic_parent()
    reparented = ClockProfile(parent_graph, CLOCK, BINDING, RATE, UNIT).edit(
        "keep-earlier", journal=Journal()
    )
    reparented.reparent(
        PolyadicInstanceRef(0),
        (DurableItemRef("segment-1"),),
        (DurableItemRef("segment-0"),),
    )
    assert reparented.reports

    journal = Journal()
    retired = ClockProfile(start, CLOCK, BINDING, RATE, UNIT).edit(
        "keep-earlier", journal=journal
    )
    retired.undeclare_with_contents(BINDING)
    retired_graph = retired.freeze()
    with pytest.raises(GraphValidationError, match="retired"):
        _ = retired.profile

    layer = LayerName(start.namespaces[0].namespace, "clock-cascade-provenance")
    provenance = ClockProfile(start, CLOCK, BINDING, RATE, UNIT).edit(
        "keep-earlier", journal=Journal(provenance=layer)
    )
    provenance.swap_items(ItemRef(SEGMENT, 0), ItemRef(SEGMENT, 1))
    provenance.undeclare_with_contents(BINDING)
    facts = next(
        item for item in provenance.freeze().layers if item.name == layer
    ).facts
    assert len(facts) == 2
    with pytest.raises(GraphValidationError, match="retired"):
        retired.move_item(ItemRef(SEGMENT, 0), 1)
    retired.undo()
    assert retired.profile.graph == start
    retired.redo()
    assert retired.freeze() == retired_graph
    with pytest.raises(GraphValidationError, match="retired"):
        _ = retired.profile


def test_clock_journal_cannot_reuse_attached_journal() -> None:
    """A journal has one unambiguous owning session."""
    profile = ClockProfile(clock_fixture(), CLOCK, BINDING, RATE, UNIT)
    journal = Journal()
    profile.edit("keep-earlier", journal=journal)
    with pytest.raises(GraphValidationError, match="already attached"):
        profile.edit("keep-earlier", journal=journal)


def test_private_touch_helpers_cover_detached_and_unresolvable_guard_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defensive report and protection branches remain deterministic."""
    case = domain_fixture("text")
    assert (
        edit_module._subject_domain(OrphanedSubject(case.unit, ItemRef(case.unit, 99)))
        is None
    )
    layer = LayerName(case.namespace, "manual")
    fact = LayerFact(
        ItemRef(case.unit, 0),
        AttributeValue(case.note, XsdType.STRING, "fixed"),
    )
    graph = case.graph.add_layer(layer).put_fact(layer, fact)
    monkeypatch.setattr(
        edit_module,
        "_resolved_subject",
        lambda _graph, _subject: (_ for _ in ()).throw(StopIteration()),
    )
    editor = graph.edit(journal=Journal().protect(layer))
    editor.set_attribute(
        ItemRef(case.spare, 1),
        AttributeValue(case.note, XsdType.STRING, "allowed"),
    )


def test_private_provenance_helpers_cover_anonymous_and_detached_subjects() -> None:
    """Anonymous relation and detached-subject helper paths remain explicit."""
    case = domain_fixture("text")
    layer = LayerName(case.namespace, "probe")
    orphan = OrphanedSubject(case.unit, ItemRef(case.unit, 99))
    assert (
        edit_module._stamp_provenance(
            case.graph,
            layer,
            "probe",
            EditAnnotations(),
            (orphan,),
        )
        == case.graph
    )
    assert (
        edit_module._retire_provenance(case.graph, layer, None, frozenset())
        is case.graph
    )
    assert edit_module._owned_provenance_facts(case.graph, layer, frozenset()) == ()
    assert (
        edit_module._restore_live_provenance_facts(case.graph, layer, ()) is case.graph
    )

    tier = TierDeclaration(QualifiedName(case.namespace, "helper-tier"), "Helper")
    assert (
        edit_module._new_declaration_subject(NamespaceDeclaration("other", "urn:other"))
        is None
    )
    assert edit_module._declaration_subject(case.graph, case.unit) == TierRef(case.unit)
    assert edit_module._declaration_subject(
        case.graph, case.link
    ) == RelationDeclarationRef(case.link)
    assert edit_module._new_declaration_subject(tier) == TierRef(tier.name)

    absent = QualifiedName(case.namespace, "absent")
    tier_note = QualifiedName(case.namespace, "helper-tier-note")
    relation_declaration_note = QualifiedName(
        case.namespace, "helper-relation-declaration-note"
    )
    helper_graph = case.graph.declare(
        AttributeDeclaration(tier_note, AttributeDomain.TIER, XsdType.STRING)
    ).declare(
        AttributeDeclaration(
            relation_declaration_note,
            AttributeDomain.RELATION_DECLARATION,
            XsdType.STRING,
        )
    )
    assert edit_module._attribute_subject(case.graph, None, absent) is None
    assert edit_module._attribute_subject(helper_graph, None, tier_note) is None
    assert edit_module._attribute_subject(case.graph, None, case.note) is None
    assert edit_module._attribute_subject(case.graph, None, case.boundary_note) is None
    assert (
        edit_module._attribute_subject(helper_graph, None, relation_declaration_note)
        is None
    )
    assert edit_module._attribute_subject(case.graph, None, case.relation_note) is None
    assert (
        edit_module._attribute_subject(
            case.graph, BoundaryRef(case.unit, 1), case.boundary_note
        )
        is None
    )
    assert (
        edit_module._attribute_subject(
            case.graph, RelationInstanceRef(0), case.relation_note
        )
        is None
    )

    boundary = BoundaryRef(case.unit, 1)
    graph = case.graph.set_attribute(
        boundary,
        AttributeValue(case.boundary_note, XsdType.STRING, "boundary"),
    )
    promoted, durable = graph.promote_boundary(boundary, "u1")
    assert edit_module._stable_subject(promoted, boundary) == durable

    wrong_name = LayerFact(
        DurableItemRef("u0"),
        JsonAttributeValue(case.note, {"operation": "probe", "annotations": {}}),
    )
    assert not edit_module._is_journal_provenance_fact(layer, wrong_name)
    wrong_value_type = LayerFact(
        DurableItemRef("u0"),
        AttributeValue(
            QualifiedName(case.namespace, "journal-provenance-item"),
            XsdType.STRING,
            "not-json",
        ),
    )
    assert not edit_module._is_journal_provenance_fact(layer, wrong_value_type)
    malformed = LayerFact(
        DurableItemRef("u0"),
        JsonAttributeValue(
            QualifiedName(case.namespace, "journal-provenance-item"),
            {"operation": 1, "annotations": {}},
        ),
    )
    assert not edit_module._is_journal_provenance_fact(layer, malformed)

    journal_editor = case.graph.edit(journal=Journal(provenance=layer))
    journal_editor.set_attribute(
        ItemRef(case.unit, 0),
        AttributeValue(case.note, XsdType.STRING, "journal-owned"),
    )
    stamped = journal_editor.freeze()
    owned_keys = edit_module._provenance_keys(stamped, layer)
    owned = edit_module._owned_provenance_facts(stamped, layer, owned_keys)
    assert len(owned) == 1
    assert edit_module._is_journal_provenance_fact(layer, owned[0])
    empty_layer = replace(
        stamped,
        layers=tuple(
            replace(candidate, facts=()) if candidate.name == layer else candidate
            for candidate in stamped.layers
        ),
    )
    assert (
        edit_module._restore_live_provenance_facts(empty_layer, layer, owned) == stamped
    )
    undeclared = replace(
        empty_layer,
        attribute_declarations=tuple(
            declaration
            for declaration in empty_layer.attribute_declarations
            if declaration.name != owned[0].value.name
        ),
    )
    assert (
        edit_module._restore_live_provenance_facts(undeclared, layer, owned)
        is undeclared
    )
    missing_subject = replace(owned[0], subject=DurableItemRef("missing"))
    assert (
        edit_module._restore_live_provenance_facts(
            empty_layer, layer, (missing_subject,)
        )
        is empty_layer
    )


def test_private_delta_maps_and_guard_branches_are_explicit() -> None:
    """Compact history values retain ordinary mapping and refusal semantics."""
    case = domain_fixture("text")
    item_map = edit_module._compact_reference_mapping(
        {
            ItemRef(case.unit, 0): ItemRef(case.unit, 2),
            ItemRef(case.unit, 1): ItemRef(case.unit, 0),
            ItemRef(case.unit, 2): ItemRef(case.unit, 1),
        },
        ItemRef,
    )
    assert tuple(item_map) == tuple(item_map.keys())
    assert item_map[ItemRef(case.unit, 1)] == ItemRef(case.unit, 0)
    assert item_map == dict(item_map)
    assert item_map != {}
    assert item_map != object()
    with pytest.raises(KeyError):
        _ = item_map[cast(ItemRef, BoundaryRef(case.unit, 0))]
    with pytest.raises(KeyError):
        _ = item_map[ItemRef(case.unit, 99)]

    integer_map = edit_module._compact_integer_mapping({0: 2, 1: 3, 3: 0})
    assert tuple(integer_map) == (0, 1, 3)
    assert integer_map[1] == 3
    assert integer_map == dict(integer_map)
    assert integer_map != {}
    assert integer_map != object()
    with pytest.raises(KeyError):
        _ = integer_map[2]

    with pytest.raises(NotImplementedError):
        edit_module._ValueDelta().apply(None, forward=True)
    with pytest.raises(GraphValidationError, match="changed value"):
        edit_module._AtomDelta("old", "new").apply("other", forward=True)
    with pytest.raises(GraphValidationError, match="no longer a tuple"):
        edit_module._TupleSpliceDelta(0, (), (), 0, 0).apply(
            "not a tuple", forward=True
        )
    with pytest.raises(GraphValidationError, match="changed tuple"):
        edit_module._TuplePositionsDelta(1, ()).apply((), forward=True)
    with pytest.raises(GraphValidationError, match="recorded type"):
        edit_module._DataclassDelta(Item, ()).apply("not an item", forward=True)
    assert isinstance(edit_module._value_delta(Item(), Item()), edit_module._AtomDelta)

    assert edit_module._subject_content(case.graph, DocumentRef()) == ()
    tier = next(tier for tier in case.graph.tiers if tier.declaration.name == case.unit)
    assert edit_module._subject_content(case.graph, TierRef(case.unit)) == (
        tier.declaration,
        tier.attributes,
    )
    relation = next(
        declaration
        for declaration in case.graph.relation_declarations
        if declaration.name == case.link
    )
    assert (
        edit_module._subject_content(case.graph, RelationDeclarationRef(case.link))
        == relation
    )
    assert edit_module._subject_content(case.graph, DurableItemRef("u0")) == (
        DurableItemRef("u0")
    )
    assert (
        edit_module._subject_domain(
            DurableBoundaryRef(DurableItemRef("u0"), BoundarySide.BEFORE)
        )
        is AttributeDomain.BOUNDARY
    )


def test_private_clock_layer_remap_branches_preserve_or_refuse_facts() -> None:
    """Temporary clock relation removal handles every relation-subject spelling."""
    graph = clock_fixture()
    note = QualifiedName(graph.namespaces[0].namespace, "private-relation-note")
    layer = LayerName(graph.namespaces[0].namespace, "private-relation-facts")
    graph = (
        graph.declare(
            AttributeDeclaration(
                note, AttributeDomain.RELATION_INSTANCE, XsdType.STRING
            )
        )
        .add_layer(layer)
        .put_fact(
            layer,
            LayerFact(
                RelationInstanceRef(2),
                AttributeValue(note, XsdType.STRING, "survives"),
            ),
        )
    )
    native = ClockProfile(graph, CLOCK, BINDING, RATE, UNIT).edit("keep-earlier")
    records = native._binding_records(SEGMENT)
    unbound, detached, _ = clock_module._unbind_relations(graph, (records[0],))
    assert detached == ()
    assert unbound.layers[0].facts[0].subject == RelationInstanceRef(1)

    promoted = graph.promote_relation(RelationInstanceRef(0), "binding-0")[0]
    promoted = promoted.put_fact(
        layer,
        LayerFact(
            DurableRelationRef("binding-0"),
            AttributeValue(note, XsdType.STRING, "durable"),
        ),
    )
    native = ClockProfile(promoted, CLOCK, BINDING, RATE, UNIT).edit("keep-earlier")
    record = native._binding_records(SEGMENT)[0]
    unbound, detached, _ = clock_module._unbind_relations(promoted, (record,))
    rebuilt = clock_module._rebuilt_layers(
        unbound.layers,
        {0: 1, 1: 2},
        detached,
        {record.position: 0},
    )
    assert any(
        fact.subject == DurableRelationRef("binding-0") for fact in rebuilt[0].facts
    )
    with pytest.raises(GraphValidationError, match="invalidate a live fact"):
        clock_module._rebuilt_layers(unbound.layers, {0: 0, 1: 1}, detached, {})


def test_provenance_stamps_new_relation_declaration_subject() -> None:
    """A newly declared relation is a provenance-capable touched subject."""
    case = domain_fixture("text")
    name = QualifiedName(case.namespace, "new-link")
    declaration = BipartiteRelationDeclaration(
        name,
        QualifiedName(case.namespace, "Token"),
        QualifiedName(case.namespace, "Token"),
    )
    editor = case.graph.edit(
        journal=Journal(provenance=LayerName(case.namespace, "schema"))
    )
    editor.declare(declaration)
    assert any(
        fact.subject == RelationDeclarationRef(name)
        for fact in editor.freeze().layers[-1].facts
    )


def _retained_size(value: object, seen: set[int] | None = None) -> int:
    """Measure record-owned immutable state while counting shared values once."""
    visited = set() if seen is None else seen
    identity = id(value)
    if identity in visited or isinstance(value, type | Enum):
        return 0
    visited.add(identity)
    size = sys.getsizeof(value)
    if is_dataclass(value) and not isinstance(value, type):
        return size + sum(
            _retained_size(getattr(value, member.name), visited)
            for member in fields(value)
        )
    if isinstance(value, Mapping):
        return size + sum(
            _retained_size(key, visited) + _retained_size(item, visited)
            for key, item in value.items()
        )
    if isinstance(value, tuple | list | set | frozenset):
        return size + sum(_retained_size(item, visited) for item in value)
    return size


def _large_record(items: int, operation: str, *, provenance: bool = False) -> object:
    """Return one record over a graph whose untouched tier size is controlled."""
    case = domain_fixture("text")
    tiers = tuple(
        replace(tier, items=tuple(Item(f"item-{index}") for index in range(items)))
        if tier.declaration.name == case.spare
        else tier
        for tier in case.graph.tiers
    )
    graph = replace(case.graph, tiers=tiers)
    journal = Journal(
        provenance=(
            LayerName(case.namespace, "retention-provenance") if provenance else None
        )
    )
    editor = graph.edit(journal=journal)
    middle = items // 2
    if operation == "move":
        editor.move_item(ItemRef(case.spare, 0), items - 1)
    elif operation == "insert":
        editor.insert_item(case.spare, middle, Item("inserted"))
    elif operation == "remove":
        editor.remove_item(ItemRef(case.spare, middle))
    else:
        editor.set_attribute(
            ItemRef(case.spare, middle),
            AttributeValue(case.note, XsdType.STRING, "changed"),
        )
    return journal.records[0]


@pytest.mark.parametrize("operation", ("move", "insert", "remove", "attribute"))
def test_journal_record_retention_does_not_scale_with_untouched_items(
    operation: str,
) -> None:
    """Structural recipes and value deltas retain change size, not graph size."""
    small = _retained_size(_large_record(100, operation))
    large = _retained_size(_large_record(1_000, operation))
    assert large <= small + 1_024


@pytest.mark.parametrize("operation", ("move", "insert", "remove", "attribute"))
def test_provenance_record_retention_does_not_scale_with_untouched_items(
    operation: str,
) -> None:
    """Provenance residuals also retain operation size, not tier size."""
    small = _retained_size(_large_record(100, operation, provenance=True))
    large = _retained_size(_large_record(1_000, operation, provenance=True))
    assert large <= small + 1_024


def test_provenance_long_move_stamps_one_fact() -> None:
    """A long durable move writes one fact for the moved item, not every shift."""
    case = domain_fixture("text")
    layer = LayerName(case.namespace, "retention-provenance")
    tiers = tuple(
        replace(
            tier,
            items=tuple(Item(f"item-{index}") for index in range(1_000)),
        )
        if tier.declaration.name == case.spare
        else tier
        for tier in case.graph.tiers
    )
    editor = replace(case.graph, tiers=tiers).edit(journal=Journal(provenance=layer))
    editor.move_item(ItemRef(case.spare, 0), 999)
    provenance = next(item for item in editor.freeze().layers if item.name == layer)
    assert len(provenance.facts) == 1


def test_structural_reports_expand_lazily_from_compact_runs() -> None:
    """A long move stores compact runs while preserving the public tuple report."""
    record = _large_record(1_000, "move")
    assert isinstance(record, edit_module.JournalRecord)
    assert isinstance(record.inverse._delta, edit_module._OperationalDelta)
    assert len(record._report_recipe.items.runs) <= 2
    first = record.report
    second = record.report
    assert first == second
    assert first is not second
    assert len(first.touched_items) == 1_000
    public = edit_module.JournalRecord(
        record.operation, record.inverse, first, record.annotations
    )
    assert public.report == first


def test_operational_inverse_refuses_out_of_band_structural_divergence() -> None:
    """Operational undo and redo retain the delta refusal contract."""
    case = domain_fixture("text")
    journal = Journal()
    editor = case.graph.edit(journal=journal)
    editor.move_item(ItemRef(case.spare, 0), 2)
    restoration = journal.records[0].inverse._delta
    assert isinstance(restoration, edit_module._OperationalDelta)
    empty_tiers = tuple(
        replace(tier, items=()) if tier.declaration.name == case.spare else tier
        for tier in case.graph.tiers
    )
    divergent = replace(case.graph, tiers=empty_tiers)
    with pytest.raises(GraphValidationError, match="cannot undo"):
        restoration.reverse(divergent)
    with pytest.raises(GraphValidationError, match="cannot redo"):
        restoration.forward(divergent)


def test_journal_boundary_promotion_operational_inverses_cover_all_anchors() -> None:
    """Edge anchors and an identity created for an interior anchor undo exactly."""
    case = domain_fixture("text")
    count = len(case.graph._tiers_by_name[case.spare].items)
    for boundary, durable_id in (
        (BoundaryRef(case.spare, 0), "start"),
        (BoundaryRef(case.spare, count), "end"),
        (BoundaryRef(case.spare, 1), "created-anchor"),
    ):
        journal = Journal()
        editor = case.graph.edit(journal=journal)
        editor.promote_boundary(boundary, durable_id)
        editor.undo()
        assert editor.freeze() == case.graph
