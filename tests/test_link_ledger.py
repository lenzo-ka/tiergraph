"""Prove link accounting across primitive and derived graph edits."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import replace
from typing import cast

import pytest
from hypothesis import given, settings

from tests import test_reconcile, test_replacement
from tests.generated_graphs import GENERATED_HIERARCHIES, GeneratedHierarchy
from tiergraph import (
    AttributeValue,
    BlobProfile,
    BoundaryRef,
    BoundarySide,
    DetachedDependency,
    DetachmentReport,
    Displacement,
    DurableBoundaryRef,
    DurableItemRef,
    DurableRelationRef,
    Graph,
    GraphEditor,
    GraphValidationError,
    Item,
    ItemRef,
    ItemRun,
    Journal,
    JournalEditor,
    JournalRecord,
    LayerFact,
    LayerName,
    PolyadicInstanceRef,
    QualifiedName,
    RelationInstance,
    RelationInstanceRef,
    ReplacementAction,
    ReplacementPolicies,
    ShiftDirection,
    Subtree,
    SubtreeCorrespondence,
    TierDeclaration,
    XsdType,
    commit_path,
    contain_by_time,
    retime,
)
from tiergraph.edit import _detachment_has_link, _graph_links, link_ledger

type Edit = Callable[[GraphEditor], object]

_REFUSED_UNREPORTED_DROPS = frozenset(
    {
        "promote-demote-boundary",
        "promote-demote-relation",
        "remove-fact",
        "remove-attribute",
        "set-attribute",
        "substitute",
        "shift",
        "endpoints",
        "boundary-value",
        "blob-attachment",
        "prune-orphans",
        "compact",
    }
)


def _assert_complete_partition(
    before: Graph, after: Graph, record: JournalRecord | None
) -> None:
    """Name every source link and require a report for every withdrawal."""
    ledger = link_ledger(before, after, record)
    assert Counter((*ledger.carried, *ledger.repointed, *ledger.dropped)) == Counter(
        _graph_links(before)
    )
    report = record.report.detached_content if record is not None else None
    assert all(
        report is not None and _detachment_has_link(link, before, after, report)
        for link in ledger.dropped
    )


def _edits(case: GeneratedHierarchy) -> tuple[tuple[str, Edit], ...]:
    """Return successful cases spanning the native and derived edit families."""
    graph = case.graph
    replacement = ReplacementPolicies(ReplacementAction.DROP)
    trim_swap = ReplacementPolicies(
        ReplacementAction.DROP,
        relations={case.group: ReplacementAction.TRIM},
    )
    item_value = AttributeValue(case.label, XsdType.STRING, "changed")
    temporary_tier = TierDeclaration(
        QualifiedName(case.utterance.namespace, "ledger-temporary"),
        "Ledger temporary",
    )
    temporary_layer = LayerName(case.layer.vocabulary, "ledger-temporary")
    orphan_fact = graph.layers[0].facts[-1]
    segment_count = len(graph._tiers_by_name[case.segment].items)
    return (
        (
            "declare-undeclare",
            lambda editor: editor.declare(temporary_tier).undeclare(temporary_tier),
        ),
        (
            "promote-demote-item",
            lambda editor: (
                editor.insert_item(case.segment, segment_count, Item())
                .promote_item(
                    ItemRef(case.segment, segment_count), "ledger-temporary-item"
                )
                .demote_item(DurableItemRef("ledger-temporary-item"))
            ),
        ),
        (
            "promote-demote-boundary",
            lambda editor: editor.promote_boundary(
                BoundaryRef(case.segment, 4), "segment-4"
            ).demote_boundary(
                DurableBoundaryRef(DurableItemRef("segment-4"), BoundarySide.BEFORE)
            ),
        ),
        (
            "promote-demote-relation",
            lambda editor: (
                editor.add_relation(
                    RelationInstance(
                        case.attachment,
                        ItemRef(case.segment, 2),
                        ItemRef(case.blob, 0),
                    )
                )
                .promote_relation(
                    RelationInstanceRef(len(graph.relations)),
                    "ledger-temporary-relation",
                )
                .demote_relation(DurableRelationRef("ledger-temporary-relation"))
            ),
        ),
        (
            "seal-unseal-drop",
            lambda editor: (
                editor.seal(case.word, 4).unseal(case.word, 2).drop_seal(case.word)
            ),
        ),
        (
            "add-remove-layer",
            lambda editor: editor.add_layer(temporary_layer).remove_layer(
                temporary_layer
            ),
        ),
        (
            "remove-fact",
            lambda editor: editor.remove_fact(
                case.layer, orphan_fact.subject, orphan_fact.value.name
            ),
        ),
        (
            "remove-attribute",
            lambda editor: editor.remove_attribute(
                ItemRef(case.segment, 2), case.label
            ),
        ),
        (
            "set-attribute",
            lambda editor: editor.set_attribute(ItemRef(case.segment, 2), item_value),
        ),
        (
            "insert-remove-items",
            lambda editor: editor.insert_items(
                case.segment, segment_count, (Item(), Item())
            ).remove_items(case.segment, segment_count, 2),
        ),
        (
            "insert-delete",
            lambda editor: editor.insert_item(
                case.segment, 8, Item("inserted")
            ).remove_item(ItemRef(case.segment, 8)),
        ),
        (
            "substitute",
            lambda editor: editor.replace_item(
                ItemRef(case.segment, 2),
                Item(
                    "segment-2",
                    (AttributeValue(case.label, XsdType.STRING, "replacement"),),
                ),
            ),
        ),
        (
            "move-run",
            lambda editor: editor.move_run(ItemRun(case.word, 0, 2), 2),
        ),
        (
            "move-item",
            lambda editor: editor.move_item(ItemRef(case.word, 0), 3),
        ),
        (
            "cut-insert-held",
            lambda editor: editor.insert_held(editor.cut(ItemRun(case.word, 2, 1)), 0),
        ),
        (
            "shift",
            lambda editor: editor.shift(
                ItemRef(case.phrase, 0),
                1,
                ShiftDirection.RIGHT,
                case.phrase_words,
            ),
        ),
        (
            "swap-runs",
            lambda editor: editor.swap_runs(
                ItemRun(case.word, 0, 1), ItemRun(case.word, 3, 1)
            ),
        ),
        (
            "swap-items",
            lambda editor: editor.swap_items(
                ItemRef(case.word, 0), ItemRef(case.word, 3)
            ),
        ),
        (
            "replace-subtree",
            lambda editor: editor.replace_subtree(
                DurableItemRef("phrase-0"),
                case.phrase_words,
                case.donor,
                replacement,
            ),
        ),
        (
            "swap-subtrees",
            lambda editor: editor.swap_subtrees(
                DurableItemRef("phrase-0"),
                DurableItemRef("phrase-1"),
                case.phrase_words,
                replacement,
                replacement,
            ),
        ),
        (
            "swap-subtrees-trim",
            lambda editor: editor.swap_subtrees(
                DurableItemRef("phrase-0"),
                DurableItemRef("phrase-1"),
                case.phrase_words,
                trim_swap,
                trim_swap,
            ),
        ),
        (
            "endpoints",
            lambda editor: editor.set_endpoints(
                PolyadicInstanceRef(len(graph.polyadic_relations) - 1),
                (ItemRef(case.segment, 1),),
                (ItemRef(case.segment, 6),),
            ),
        ),
        (
            "fact",
            lambda editor: editor.put_fact(
                case.layer,
                LayerFact(DurableItemRef("segment-2"), item_value),
            ),
        ),
        (
            "boundary-value",
            lambda editor: editor.set_attribute(
                BoundaryRef(case.segment, 4),
                AttributeValue(case.boundary_note, XsdType.STRING, "changed"),
            ),
        ),
        (
            "blob-attachment",
            lambda editor: editor.add_relation(
                RelationInstance(
                    case.attachment,
                    ItemRef(case.segment, 2),
                    ItemRef(case.blob, 0),
                    "new-attachment",
                )
            ).remove_relation(RelationInstanceRef(len(graph.relations))),
        ),
        ("prune-orphans", lambda editor: editor.prune_orphans()),
        ("compact", lambda editor: editor.compact()),
    )


@settings(max_examples=10, deadline=None)
@given(case=GENERATED_HIERARCHIES)
def test_every_edit_balances_the_link_ledger(case: GeneratedHierarchy) -> None:
    """Every generated edit names its partition or refuses its first silent drop."""
    assert [tier.declaration.name for tier in case.graph.tiers[:5]] == [
        case.utterance,
        case.phrase,
        case.word,
        case.syllable,
        case.segment,
    ]
    assert BlobProfile(case.graph).attachments(ItemRef(case.blob, 0))
    for name, operation in _edits(case):
        for journaled in (False, True):
            if name == "cut-insert-held" and journaled:
                continue
            journal = Journal() if journaled else None
            editor = (
                case.graph.edit(journal=journal, check_links=True)
                if journal is not None
                else case.graph.edit(check_links=True)
            )
            if name in _REFUSED_UNREPORTED_DROPS:
                with pytest.raises(
                    GraphValidationError, match="unreported dropped .* link"
                ):
                    operation(editor)  # type: ignore[arg-type]
                editor.freeze()
                continue

            operation(editor)  # type: ignore[arg-type]
            result = editor.freeze()
            if journal is None:
                if name == "cut-insert-held":
                    _assert_complete_partition(case.graph, result, None)
                continue
            record = journal.records[0] if len(journal.records) == 1 else None
            _assert_complete_partition(case.graph, result, record)

    for journaled in (False, True):
        journal = Journal() if journaled else None
        editor = (
            case.graph.edit(journal=journal, check_links=True)
            if journal is not None
            else case.graph.edit(check_links=True)
        )
        with pytest.raises(GraphValidationError):
            editor.remove_item(ItemRef(case.segment, 0))
        assert editor.freeze() == case.graph

    with pytest.raises(TypeError, match="check_links must be a boolean"):
        case.graph.edit(check_links=1)  # type: ignore[call-overload]
    with pytest.raises(TypeError, match="check_links must be a boolean"):
        GraphEditor(case.graph, check_links=1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="check_links must be a boolean"):
        JournalEditor(case.graph, Journal(), check_links=1)  # type: ignore[arg-type]

    invalid_endpoints = GraphEditor(case.graph, check_links=True)
    original_relation = invalid_endpoints._relations[0]
    with pytest.raises(GraphValidationError):
        invalid_endpoints.set_endpoints(
            RelationInstanceRef(0),
            ItemRef(case.segment, 99),
            ItemRef(case.segment, 0),
        )
    assert invalid_endpoints._relations[0] == original_relation
    assert invalid_endpoints.freeze() == case.graph

    inserted = case.graph.insert_item(case.word, 4, Item())
    direct = GraphEditor(inserted, check_links=True)
    direct.cut(ItemRun(case.word, 4, 1))
    before_refusal = direct.freeze()
    with pytest.raises(GraphValidationError):
        direct.remove_item(ItemRef(case.segment, 99))
    assert direct.freeze() == before_refusal
    cut_result = direct.freeze()
    link_ledger(inserted, cut_result)

    blocked_cut = GraphEditor(case.graph, check_links=True)
    blocked_cut.cut(ItemRun(case.word, 0, 1))
    with pytest.raises(GraphValidationError):
        blocked_cut.remove_item(ItemRef(case.segment, 99))

    journal = Journal()
    recorded = case.graph.edit(journal=journal, check_links=True)
    recorded.put_fact(
        case.layer,
        LayerFact(
            DurableItemRef("segment-2"),
            AttributeValue(case.label, XsdType.STRING, "ledger-record"),
        ),
    )
    recorded_result = recorded.freeze()
    record = journal.records[-1]
    link_ledger(case.graph, recorded_result, record.report)
    with pytest.raises(GraphValidationError, match="inverse that cannot restore"):
        link_ledger(case.graph, case.graph, record)
    wrong_before = case.graph.set_attribute(
        ItemRef(case.word, 1),
        AttributeValue(case.label, XsdType.STRING, "wrong-before"),
    )
    with pytest.raises(GraphValidationError, match="complete source snapshot"):
        link_ledger(wrong_before, recorded_result, record)

    with pytest.raises(TypeError, match="before and after Graph"):
        link_ledger(None, case.graph)  # type: ignore[arg-type]
    pruned = case.graph.edit().prune_orphans().freeze()
    with pytest.raises(GraphValidationError, match="unreported dropped fact"):
        link_ledger(case.graph, pruned, DetachmentReport())

    lattice_source = test_reconcile.path_graph()
    committed = commit_path(test_reconcile.path_plan(lattice_source), ("s", "a", "f"))
    link_ledger(lattice_source, committed.graph, committed.report)

    timing_source = test_reconcile.timing_graph()
    retimed = retime(
        test_reconcile.clock_profile(timing_source),
        test_reconcile.CHILDREN,
        (0, 1, 2, 4, 5),
        offset=1,
    )
    with pytest.raises(GraphValidationError, match="unreported dropped endpoint link"):
        link_ledger(timing_source, retimed)
    regrouped = contain_by_time(
        test_reconcile.clock_profile(timing_source),
        test_reconcile.CONTAINS,
        test_reconcile.PARENTS,
        test_reconcile.CHILDREN,
    )
    with pytest.raises(GraphValidationError, match="unreported dropped endpoint link"):
        link_ledger(timing_source, regrouped)


@settings(max_examples=10, deadline=None)
@given(case=GENERATED_HIERARCHIES)
def test_checked_editors_refuse_an_unreported_plain_withdrawal(
    case: GeneratedHierarchy,
) -> None:
    """A valid primitive withdrawal is atomic unless its report names the link."""
    target = ItemRef(case.segment, 2)
    for journaled in (False, True):
        journal = Journal() if journaled else None
        editor = (
            case.graph.edit(journal=journal, check_links=True)
            if journal is not None
            else case.graph.edit(check_links=True)
        )
        with pytest.raises(
            GraphValidationError, match="unreported dropped attribute link"
        ):
            editor.remove_attribute(target, case.label)
        assert editor.freeze() == case.graph


@settings(max_examples=10, deadline=None)
@given(case=GENERATED_HIERARCHIES)
def test_idless_polyadic_endpoint_order_and_trim_are_accounted(
    case: GeneratedHierarchy,
) -> None:
    """ID-less positional endpoints retain order and need their own trim entry."""
    relation = case.graph.polyadic_relations[0]
    assert relation.durable_id is None
    assert len(relation.targets) == 2
    checked = case.graph.edit(check_links=True)
    with pytest.raises(GraphValidationError, match="unreported dropped endpoint link"):
        checked.set_endpoints(
            PolyadicInstanceRef(0), relation.sources, tuple(reversed(relation.targets))
        )
    assert checked.freeze() == case.graph

    index = 0
    source_relation = replace(
        case.graph.polyadic_relations[index],
        targets=(
            *case.graph.polyadic_relations[index].targets,
            case.graph.polyadic_relations[index].targets[0],
        ),
    )
    source = replace(
        case.graph,
        polyadic_relations=(source_relation, *case.graph.polyadic_relations[1:]),
    )
    assert source_relation.durable_id is None
    trimmed_relation = replace(source_relation, targets=source_relation.targets[:1])
    trimmed = replace(
        source,
        polyadic_relations=(trimmed_relation, *source.polyadic_relations[1:]),
    )
    incomplete = DetachmentReport(
        relations=((PolyadicInstanceRef(index), source_relation),)
    )
    with pytest.raises(GraphValidationError, match="unreported dropped endpoint link"):
        link_ledger(source, trimmed, incomplete)
    with pytest.raises(GraphValidationError, match="unreported dropped endpoint link"):
        link_ledger(
            source, trimmed, (incomplete, Displacement.stationary(source), None)
        )

    partial = replace(
        incomplete,
        dependencies=(
            DetachedDependency(
                "polyadic_endpoints",
                index,
                declaration=source_relation.declaration,
                endpoint=source_relation.targets[1],
                endpoint_side="targets",
                endpoint_index=1,
            ),
        ),
    )
    with pytest.raises(GraphValidationError, match="unreported dropped endpoint link"):
        link_ledger(source, trimmed, partial)


def test_checked_editors_accept_a_reported_nonfinal_polyadic_trim() -> None:
    """A reported early endpoint trim preserves later endpoints in order."""
    case = test_replacement.fixture("speech")
    correspondence = SubtreeCorrespondence(
        {ItemRef(case.middle, 1): (ItemRef(case.middle, 0),)}
    )
    policies = ReplacementPolicies(
        ReplacementAction.DROP,
        correspondence=correspondence,
        relations={
            case.group: ReplacementAction.TRIM,
            case.link: ReplacementAction.DROP,
        },
    )
    for journaled in (False, True):
        journal = Journal() if journaled else None
        editor = (
            case.graph.edit(journal=journal, check_links=True)
            if journal is not None
            else case.graph.edit(check_links=True)
        )
        editor.replace_subtree(
            DurableItemRef("root"),
            case.containment,
            Subtree(case.alternative, ItemRef(case.root, 0)),
            policies,
        )
        result = editor.freeze()
        group = next(
            relation
            for relation in result.polyadic_relations
            if relation.declaration == case.group
        )
        assert group.targets == (ItemRef(case.middle, 0),)
        report = (
            journal.records[-1].report.detached_content
            if journal is not None
            else cast(GraphEditor, editor).last_detachment
        )
        assert report is not None
        assert any(
            dependency.carrier == "polyadic_endpoints"
            and dependency.endpoint_side == "targets"
            and dependency.endpoint_index == 0
            for dependency in report.dependencies
        )
