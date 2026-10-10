"""Exercise link accounting across primitive and derived graph edits."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import replace
from typing import cast

import pytest
from hypothesis import given, settings

from tests import test_reconcile, test_replacement, test_shift
from tests.generated_graphs import GENERATED_HIERARCHIES, GeneratedHierarchy
from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    BlobProfile,
    Boundary,
    BoundaryRef,
    BoundarySide,
    ClockBindingChange,
    ClockEditOperation,
    ClockEditor,
    ClockEditReport,
    ClockRebindingPolicy,
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
from tiergraph.edit import (
    _clock_endpoint_match,
    _detachment_has_link,
    _displacement_between,
    _graph_links,
    _link_positions_match,
    _owner_images,
    _shift_endpoint_match,
    _shift_endpoint_moves,
    _value_images,
    link_ledger,
)

type Edit = Callable[[GraphEditor], object]

_REFUSED_UNREPORTED_DROPS = frozenset(
    {
        "promote-demote-boundary",
        "promote-demote-relation",
        "remove-fact",
        "remove-attribute",
        "set-attribute",
        "substitute",
        "endpoints",
        "boundary-value",
        "blob-attachment",
        "prune-orphans",
        "compact",
    }
)


def _expected_shift_endpoint_moves(
    before: Graph, after: Graph, record: JournalRecord
) -> frozenset[tuple[object, object]]:
    """Derive a recorded shift's exact endpoint pairs without its matcher."""
    report = record.report
    correspondence = report.correspondence
    assert correspondence is not None
    moved = frozenset(correspondence.identity_correspondence)
    changed = tuple(
        touch.index
        for touch in report.touched_relations
        if touch.carrier == "polyadic_relations"
        and before.polyadic_relations[touch.index].targets
        != after.polyadic_relations[touch.index].targets
    )
    sources = tuple(
        index
        for index in changed
        if len(before.polyadic_relations[index].targets)
        > len(after.polyadic_relations[index].targets)
    )
    targets = tuple(
        index
        for index in changed
        if len(before.polyadic_relations[index].targets)
        < len(after.polyadic_relations[index].targets)
    )
    assert len(sources) == len(targets) == 1
    source_owner = ("polyadic_relations", sources[0])
    target_owner = ("polyadic_relations", targets[0])
    old = tuple(
        link
        for link in _graph_links(before)
        if link.kind == "endpoint"
        and link.owner == source_owner
        and link.side == "targets"
        and before.resolve_item(cast(ItemRef | DurableItemRef, link.value)) in moved
    )
    new = tuple(
        link
        for link in _graph_links(after)
        if link.kind == "endpoint"
        and link.owner == target_owner
        and link.side == "targets"
        and after.resolve_item(cast(ItemRef | DurableItemRef, link.value)) in moved
    )
    assert tuple(link.value for link in old) == tuple(link.value for link in new)
    return frozenset(zip(old, new, strict=True))


def _assert_complete_partition(
    before: Graph, after: Graph, record: JournalRecord | None
) -> None:
    """Name every source link and require a report for every withdrawal."""
    ledger = link_ledger(before, after, record)
    assert Counter((*ledger.carried, *ledger.repointed, *ledger.dropped)) == Counter(
        _graph_links(before)
    )
    report = record.report.detached_content if record is not None else None
    displacement = (
        record.report.displacement
        if record is not None
        else _displacement_between(before, after)
    )
    correspondence = record.report.correspondence if record is not None else None
    assert all(
        report is not None
        and _detachment_has_link(
            link,
            before,
            after,
            report,
            displacement,
            correspondence,
        )
        for link in ledger.dropped
    )

    targets = _graph_links(after)
    used: set[int] = set()
    endpoint_positions: dict[tuple[object, object, str | None], int] = {}
    expected: dict[str, list[object]] = {
        "carried": [],
        "repointed": [],
        "dropped": [],
    }
    shift_moves = (
        _expected_shift_endpoint_moves(before, after, record)
        if record is not None and record.operation == "shift"
        else frozenset()
    )
    for link in _graph_links(before):
        reported = report is not None and _detachment_has_link(
            link,
            before,
            after,
            report,
            displacement,
            correspondence,
        )
        if reported:
            expected["dropped"].append(link)
            continue
        assert displacement is not None
        owners = _owner_images(link, before, after, displacement, correspondence)
        structural_fallback = not owners and link.carrier in {
            "relations",
            "polyadic_relations",
        }
        if structural_fallback:
            owners = (link.owner,)
        values = _value_images(link, displacement, correspondence)
        if structural_fallback and link.value not in values:
            values = (*values, link.value)
        match = next(
            (
                (index, candidate)
                for index, candidate in enumerate(targets)
                if index not in used
                and candidate.kind == link.kind
                and candidate.carrier == link.carrier
                and candidate.owner in owners
                and candidate.value in values
                and candidate.side == link.side
                and _link_positions_match(link, candidate, endpoint_positions)
            ),
            None,
        )
        if match is None and record is not None and record.operation == "shift":
            match = next(
                (
                    (index, candidate)
                    for index, candidate in enumerate(targets)
                    if index not in used
                    and (link, candidate) in shift_moves
                    and _link_positions_match(link, candidate, endpoint_positions)
                ),
                None,
            )
        assert match is not None
        index, candidate = match
        used.add(index)
        if link.kind == "endpoint" and link.carrier == "polyadic_relations":
            assert candidate.position is not None
            endpoint_positions[(link.owner, candidate.owner, link.side)] = (
                candidate.position
            )
        expected["carried" if candidate == link else "repointed"].append(link)
    for partition in ("carried", "repointed", "dropped"):
        actual = getattr(ledger, partition)
        assert Counter(map(repr, actual)) == Counter(map(repr, expected[partition]))


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


def test_checked_shift_repoints_containment_on_both_editor_paths() -> None:
    """Default and cross-parent shifts account for the sister re-pointing."""
    source = test_shift.hierarchy()
    cases = (
        (
            ItemRef(test_shift.PHRASE, 0),
            test_shift.PHRASE_WORDS,
            False,
        ),
        (
            ItemRef(test_shift.SYLLABLE, 0),
            test_shift.SYLLABLE_SEGMENTS,
            True,
        ),
    )
    for container, containment, across_parent in cases:
        results = []
        for journaled in (False, True):
            journal = Journal() if journaled else None
            editor = (
                source.edit(journal=journal, check_links=True)
                if journal is not None
                else source.edit(check_links=True)
            )
            editor.shift(
                container,
                1,
                "right",
                containment,
                across_parent=across_parent,
            )
            result = editor.freeze()
            results.append(result)
            if journal is not None:
                _assert_complete_partition(source, result, journal.records[0])
        assert results[0] == results[1]


def test_plain_checked_insert_then_shift_uses_per_operation_displacement() -> None:
    """A prior insertion does not offset the coordinates audited for a shift."""
    source = test_shift.hierarchy()
    expected = source.edit()
    expected.insert_item(test_shift.WORD, 0, Item("inserted-word"))
    expected.shift(
        ItemRef(test_shift.PHRASE, 0),
        1,
        "right",
        test_shift.PHRASE_WORDS,
    )

    checked = source.edit(check_links=True)
    checked.insert_item(test_shift.WORD, 0, Item("inserted-word"))
    checked.shift(
        ItemRef(test_shift.PHRASE, 0),
        1,
        "right",
        test_shift.PHRASE_WORDS,
    )
    assert checked.freeze() == expected.freeze()


@settings(max_examples=10, deadline=None)
@given(case=GENERATED_HIERARCHIES)
def test_plain_checked_insert_then_replace_uses_per_operation_displacement(
    case: GeneratedHierarchy,
) -> None:
    """A replacement is audited from its immediate post-insertion source."""
    policies = ReplacementPolicies(ReplacementAction.DROP)
    expected = case.graph.edit()
    expected.insert_item(case.word, 0, Item("inserted-word"))
    expected.replace_subtree(
        DurableItemRef("phrase-0"), case.phrase_words, case.donor, policies
    )

    checked = case.graph.edit(check_links=True)
    checked.insert_item(case.word, 0, Item("inserted-word"))
    checked.replace_subtree(
        DurableItemRef("phrase-0"), case.phrase_words, case.donor, policies
    )
    assert checked.freeze() == expected.freeze()


def test_plain_checked_shift_uses_the_same_detachment_report_as_journaled() -> None:
    """A reported boundary withdrawal has one outcome on both checked paths."""
    source = test_shift.hierarchy()
    value = AttributeValue(test_shift.BOUNDARY_NOTE, XsdType.STRING, "break")
    source = replace(
        source,
        attribute_declarations=(
            *source.attribute_declarations,
            AttributeDeclaration(
                test_shift.BOUNDARY_NOTE,
                AttributeDomain.BOUNDARY,
                XsdType.STRING,
            ),
        ),
        boundary_values=(Boundary(BoundaryRef(test_shift.PHRASE, 1), (value,)),),
    )
    results = []
    journaled_ledger = None
    for journaled in (False, True):
        journal = Journal() if journaled else None
        editor = (
            source.edit(journal=journal, check_links=True)
            if journal is not None
            else source.edit(check_links=True)
        )
        editor.shift(
            ItemRef(test_shift.PHRASE, 0),
            1,
            "right",
            test_shift.PHRASE_WORDS,
            "drop-to-provisional",
        )
        result = editor.freeze()
        results.append(result)
        if journal is not None:
            journaled_ledger = link_ledger(source, result, journal.records[0])
    assert results[0] == results[1]
    assert results[0].boundary_values == ()
    assert journaled_ledger is not None
    moved = ItemRef(test_shift.WORD, 2)
    source_endpoint = next(
        link
        for link in _graph_links(source)
        if link.kind == "endpoint"
        and link.owner == ("polyadic_relations", 1)
        and link.value == moved
    )
    intended_endpoint = next(
        link
        for link in _graph_links(results[1])
        if link.kind == "endpoint"
        and link.owner == ("polyadic_relations", 2)
        and link.value == moved
    )
    assert source_endpoint in journaled_ledger.repointed
    assert intended_endpoint not in journaled_ledger.introduced


def test_clock_and_commit_path_expose_checked_link_accounting() -> None:
    """Clock shifts and complete path commits audit plain and journaled edits."""
    basic = test_reconcile.clock_profile(test_reconcile.timing_graph())
    moved = []
    for journaled in (False, True):
        journal = Journal() if journaled else None
        editor = (
            basic.edit("keep-earlier", journal=journal, check_links=True)
            if journal is not None
            else basic.edit("keep-earlier", check_links=True)
        )
        editor.move_item(ItemRef(test_reconcile.CHILDREN, 0), 1)
        moved.append(editor.freeze())
    assert moved[0] == moved[1]

    profile = test_shift.fully_timed_hierarchy()
    clock_results = []
    clock_reports = []
    for journaled in (False, True):
        journal = Journal() if journaled else None
        editor = (
            profile.edit(journal=journal, check_links=True)
            if journal is not None
            else profile.edit(check_links=True)
        )
        editor.shift(
            ItemRef(test_shift.PHRASE, 0),
            1,
            "right",
            test_shift.PHRASE_WORDS,
        )
        clock_results.append(editor.freeze())
        clock_reports.append(editor.reports)
    assert clock_results[0] == clock_results[1]
    assert clock_reports[0] == clock_reports[1]
    assert tuple(report.operation for report in clock_reports[0]) == (
        ClockEditOperation.SHIFT,
    )
    assert clock_reports[0][0].changes

    lattice_source = test_reconcile.path_graph()
    plans = []
    for journaled in (False, True):
        journal = Journal() if journaled else None
        plans.append(
            commit_path(
                test_reconcile.path_plan(lattice_source),
                ("s", "a", "f"),
                journal=journal,
                check_links=True,
            ).graph
        )
    assert plans[0] == plans[1]
    with pytest.raises(TypeError, match="check_links must be a boolean"):
        profile.edit(check_links=1)  # type: ignore[call-overload]
    with pytest.raises(TypeError, match="check_links must be a boolean"):
        ClockEditor(profile, check_links=1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="check_links must be a boolean"):
        commit_path(
            test_reconcile.path_plan(lattice_source),
            ("s", "a", "f"),
            check_links=1,  # type: ignore[arg-type]
        )


def test_checked_shift_refuses_an_unaccounted_binary_repoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A shift cannot conceal an unrelated binary endpoint change."""
    source = test_shift.hierarchy()
    declaration = test_shift.name("checked-shift-link")
    phrase_type = test_shift.name("phrase-type")
    source = replace(
        source,
        relation_declarations=(
            *source.relation_declarations,
            BipartiteRelationDeclaration(declaration, phrase_type, phrase_type),
        ),
        relations=(
            RelationInstance(
                declaration,
                ItemRef(test_shift.PHRASE, 0),
                ItemRef(test_shift.PHRASE, 0),
            ),
        ),
    )
    original = GraphEditor.shift

    def corrupt_shift(
        editor: GraphEditor,
        container: ItemRef | DurableItemRef,
        k: int,
        direction: ShiftDirection | str,
        containment: QualifiedName,
        policy: str | None = None,
        across_parent: bool = False,
    ) -> GraphEditor:
        result = original(
            editor,
            container,
            k,
            direction,
            containment,
            policy,
            across_parent,
        )
        editor.set_endpoints(
            RelationInstanceRef(0),
            ItemRef(test_shift.PHRASE, 0),
            ItemRef(test_shift.PHRASE, 1),
        )
        return result

    monkeypatch.setattr(GraphEditor, "shift", corrupt_shift)
    editor = source.edit(check_links=True)
    with pytest.raises(GraphValidationError, match="unreported dropped endpoint link"):
        editor.shift(
            ItemRef(test_shift.PHRASE, 0),
            1,
            "right",
            test_shift.PHRASE_WORDS,
        )
    assert editor.freeze() == source


def test_checked_shift_refuses_an_unrelated_polyadic_repoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A shift cannot conceal a move between unrelated containment instances."""
    source = test_shift.hierarchy()
    original = GraphEditor.shift

    def corrupt_shift(
        editor: GraphEditor,
        container: ItemRef | DurableItemRef,
        k: int,
        direction: ShiftDirection | str,
        containment: QualifiedName,
        policy: str | None = None,
        across_parent: bool = False,
    ) -> GraphEditor:
        result = original(
            editor,
            container,
            k,
            direction,
            containment,
            policy,
            across_parent,
        )
        first = editor._polyadic_relations[10]
        second = editor._polyadic_relations[11]
        editor._polyadic_relations[10] = replace(first, targets=second.targets)
        editor._polyadic_relations[11] = replace(second, targets=first.targets)
        return result

    monkeypatch.setattr(GraphEditor, "shift", corrupt_shift)
    editor = source.edit(check_links=True)
    with pytest.raises(GraphValidationError, match="unreported dropped endpoint link"):
        editor.shift(
            ItemRef(test_shift.SYLLABLE, 0),
            1,
            "right",
            test_shift.SYLLABLE_SEGMENTS,
            across_parent=True,
        )
    assert editor.freeze() == source


def test_checked_shift_refuses_a_nonadjacent_held_run_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A shift cannot redirect its held run past the adjacent sister."""
    source = test_shift.hierarchy()
    original = GraphEditor.shift

    def corrupt_shift(
        editor: GraphEditor,
        container: ItemRef | DurableItemRef,
        k: int,
        direction: ShiftDirection | str,
        containment: QualifiedName,
        policy: str | None = None,
        across_parent: bool = False,
    ) -> GraphEditor:
        result = original(
            editor,
            container,
            k,
            direction,
            containment,
            policy,
            across_parent,
        )
        sister = editor._polyadic_relations[9]
        redirected = editor._polyadic_relations[10]
        held = sister.targets[:1]
        editor._polyadic_relations[9] = replace(sister, targets=sister.targets[1:])
        editor._polyadic_relations[10] = replace(
            redirected, targets=(*held, *redirected.targets)
        )
        return result

    monkeypatch.setattr(GraphEditor, "shift", corrupt_shift)
    editor = source.edit(check_links=True)
    with pytest.raises(GraphValidationError, match="unreported dropped endpoint link"):
        editor.shift(
            ItemRef(test_shift.SYLLABLE, 0),
            1,
            "right",
            test_shift.SYLLABLE_SEGMENTS,
            across_parent=True,
        )
    assert editor.freeze() == source


def test_checked_clock_refusal_restores_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clock ledger refusal restores its graph, profile, and reports."""
    profile = test_reconcile.clock_profile(test_reconcile.timing_graph())
    editor = profile.edit("keep-earlier", check_links=True)
    object.__setattr__(editor, "_link_check_depth", 1)
    assert callable(editor.move_item)
    object.__setattr__(editor, "_link_check_depth", 0)

    def refuse(*args: object, **kwargs: object) -> None:
        raise GraphValidationError("fault-injected clock ledger refusal")

    monkeypatch.setattr("tiergraph.edit.link_ledger", refuse)
    with pytest.raises(GraphValidationError, match="fault-injected"):
        editor.move_item(ItemRef(test_reconcile.CHILDREN, 0), 1)
    assert editor.freeze() == profile.graph
    assert editor.reports == ()


def test_clock_endpoint_match_refuses_an_equivalent_durable_spelling() -> None:
    """A reported clock index does not license another durable endpoint ref."""
    profile = test_shift.fully_timed_hierarchy()
    editor = profile.edit()
    editor.shift(
        ItemRef(test_shift.PHRASE, 0),
        1,
        "right",
        test_shift.PHRASE_WORDS,
    )
    result = editor.freeze()
    changed_index = next(
        index
        for index, (previous, current) in enumerate(
            zip(profile.graph.relations, result.relations, strict=True)
        )
        if previous.right != current.right
    )
    relation = result.relations[changed_index]
    clock_index = result.resolve_boundary(
        cast(DurableBoundaryRef, relation.right)
    ).index
    previous_tick = result._tiers_by_name[test_shift.CLOCK].items[clock_index - 1]
    assert previous_tick.durable_id is not None
    alternate = DurableBoundaryRef(
        DurableItemRef(previous_tick.durable_id), BoundarySide.AFTER
    )
    assert alternate != relation.right
    assert result.resolve_boundary(alternate).index == clock_index
    relations = list(result.relations)
    relations[changed_index] = replace(relation, right=alternate)
    corrupted = replace(result, relations=tuple(relations))

    with pytest.raises(GraphValidationError, match="unreported dropped endpoint link"):
        link_ledger(
            profile.graph,
            corrupted,
            (
                None,
                _displacement_between(profile.graph, corrupted),
                cast(SubtreeCorrespondence, editor.__dict__["_link_correspondence"]),
            ),
            operation="shift",
            clock_reports=editor.reports,
        )


def test_endpoint_match_helpers_refuse_mismatches_and_cover_clock_sides() -> None:
    """Shift and clock matching require declared relation and report agreement."""
    case = test_replacement.fixture("speech")
    endpoints = [link for link in _graph_links(case.graph) if link.kind == "endpoint"]
    shift_source, shift_target = next(
        (source, target)
        for source in endpoints
        for target in endpoints
        if case.graph.relations[cast(tuple[str, int], source.owner)[1]].declaration
        != case.graph.relations[cast(tuple[str, int], target.owner)[1]].declaration
    )
    assert not _shift_endpoint_match(shift_source, shift_target, frozenset())
    hierarchy = test_shift.hierarchy()
    hierarchy_endpoints = [
        link for link in _graph_links(hierarchy) if link.kind == "endpoint"
    ]
    polyadic_source, polyadic_target = next(
        (source, target)
        for source in hierarchy_endpoints
        for target in hierarchy_endpoints
        if source.side == target.side
        and hierarchy.polyadic_relations[
            cast(tuple[str, int], source.owner)[1]
        ].declaration
        != hierarchy.polyadic_relations[
            cast(tuple[str, int], target.owner)[1]
        ].declaration
    )
    shifted = hierarchy.edit()
    shifted.shift(
        ItemRef(test_shift.PHRASE, 0),
        1,
        "right",
        test_shift.PHRASE_WORDS,
    )
    shifted_graph = shifted.freeze()
    moves = _shift_endpoint_moves(
        hierarchy,
        shifted_graph,
        cast(SubtreeCorrespondence, shifted.__dict__["_last_correspondence"]),
    )
    assert moves
    actual_source, actual_target = next(iter(moves))
    assert _shift_endpoint_match(actual_source, actual_target, moves)
    assert not _shift_endpoint_match(polyadic_source, polyadic_target, moves)
    target_index = cast(tuple[str, int], actual_target.owner)[1]

    missing_source_before = list(hierarchy.polyadic_relations)
    missing_source_after = list(shifted_graph.polyadic_relations)
    missing_source_before[target_index] = replace(
        missing_source_before[target_index], sources=()
    )
    missing_source_after[target_index] = replace(
        missing_source_after[target_index], sources=()
    )
    missing_source_source = replace(hierarchy)
    missing_source_result = replace(shifted_graph)
    object.__setattr__(
        missing_source_source,
        "polyadic_relations",
        tuple(missing_source_before),
    )
    object.__setattr__(
        missing_source_result,
        "polyadic_relations",
        tuple(missing_source_after),
    )
    assert not _shift_endpoint_moves(
        missing_source_source,
        missing_source_result,
        cast(SubtreeCorrespondence, shifted.__dict__["_last_correspondence"]),
    )

    boundary_source = (DurableBoundaryRef(test_shift.PHRASE, BoundarySide.BEFORE),)
    boundary_source_before = list(hierarchy.polyadic_relations)
    boundary_source_after = list(shifted_graph.polyadic_relations)
    boundary_source_before[target_index] = replace(
        boundary_source_before[target_index], sources=boundary_source
    )
    boundary_source_after[target_index] = replace(
        boundary_source_after[target_index], sources=boundary_source
    )
    boundary_source_source = replace(hierarchy)
    boundary_source_result = replace(shifted_graph)
    object.__setattr__(
        boundary_source_source,
        "polyadic_relations",
        tuple(boundary_source_before),
    )
    object.__setattr__(
        boundary_source_result,
        "polyadic_relations",
        tuple(boundary_source_after),
    )
    assert not _shift_endpoint_moves(
        boundary_source_source,
        boundary_source_result,
        cast(SubtreeCorrespondence, shifted.__dict__["_last_correspondence"]),
    )
    assert not _shift_endpoint_moves(hierarchy, hierarchy, None)
    assert not _shift_endpoint_moves(
        hierarchy,
        replace(hierarchy, polyadic_relations=hierarchy.polyadic_relations[:-1]),
        SubtreeCorrespondence(),
    )
    assert not _shift_endpoint_moves(hierarchy, hierarchy, SubtreeCorrespondence())
    changed_metadata = replace(
        shifted_graph,
        polyadic_relations=(
            replace(shifted_graph.polyadic_relations[0], durable_id="coverage-probe"),
            *shifted_graph.polyadic_relations[1:],
        ),
    )
    assert (
        _shift_endpoint_moves(
            hierarchy,
            changed_metadata,
            cast(SubtreeCorrespondence, shifted.__dict__["_last_correspondence"]),
        )
        == moves
    )
    assert not _shift_endpoint_moves(
        hierarchy,
        hierarchy,
        cast(SubtreeCorrespondence, shifted.__dict__["_last_correspondence"]),
    )
    checked = hierarchy.edit(check_links=True)
    checked._advance_displacement(Displacement.stationary(hierarchy))

    timing = test_reconcile.timing_graph()
    timing_links = _graph_links(timing)
    relation_link = next(link for link in timing_links if link.kind == "relation")
    assert not _clock_endpoint_match(
        relation_link, relation_link, timing, timing, (), ()
    )
    left = next(
        link
        for link in timing_links
        if link.kind == "endpoint"
        and link.owner == ("relations", 4)
        and link.side == "left"
    )
    assert not _clock_endpoint_match(left, left, timing, timing, (left.owner,), ())

    right = next(
        link
        for link in timing_links
        if link.kind == "endpoint"
        and link.owner == ("relations", 4)
        and link.side == "right"
    )
    later_right = next(
        link
        for link in timing_links
        if link.kind == "endpoint"
        and link.owner == ("relations", 5)
        and link.side == "right"
    )
    report = ClockEditReport(
        ClockEditOperation.ITEM_MOVE,
        ClockRebindingPolicy.KEEP_EARLIER,
        test_reconcile.CHILDREN,
        (
            ClockBindingChange(
                None,
                None,
                timing.relations[4].left,
                timing.relations[5].left,
                1,
                3,
                False,
            ),
        ),
        False,
    )
    nonmatching_change = replace(report.changes[0], previous_clock_index=None)
    assert _clock_endpoint_match(
        right,
        later_right,
        timing,
        timing,
        (later_right.owner,),
        (
            replace(report, changes=()),
            replace(report, changes=(nonmatching_change, *report.changes)),
        ),
    )

    wrong_declaration = next(
        link
        for link in endpoints
        if case.graph.relations[cast(tuple[str, int], link.owner)[1]].declaration
        != case.graph.relations[
            cast(tuple[str, int], endpoints[0].owner)[1]
        ].declaration
    )
    assert not _clock_endpoint_match(
        endpoints[0],
        wrong_declaration,
        case.graph,
        case.graph,
        (wrong_declaration.owner,),
        (report,),
    )


def test_partition_assertion_rejects_a_fault_injected_repoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unmatched link filed as re-pointed fails the named partition check."""
    case = test_replacement.fixture("speech")
    journal = Journal()
    editor = case.graph.edit(journal=journal)
    editor.replace_subtree(
        DurableItemRef("root"),
        case.containment,
        Subtree(case.alternative, ItemRef(case.root, 0)),
        ReplacementPolicies(ReplacementAction.DROP),
    )
    result = editor.freeze()
    record = journal.records[0]
    actual = link_ledger(case.graph, result, record)
    assert actual.dropped
    injected = replace(
        actual,
        repointed=(*actual.repointed, actual.dropped[0]),
        dropped=actual.dropped[1:],
    )
    monkeypatch.setitem(globals(), "link_ledger", lambda *args, **kwargs: injected)
    with pytest.raises(AssertionError):
        _assert_complete_partition(case.graph, result, record)


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

    relation_level = replace(
        incomplete,
        dependencies=(
            DetachedDependency(
                "polyadic_relations",
                index,
                declaration=source_relation.declaration,
            ),
        ),
    )
    with pytest.raises(GraphValidationError, match="unreported dropped endpoint link"):
        link_ledger(
            source,
            trimmed,
            (relation_level, Displacement.stationary(source), None),
        )
    surviving_endpoint = next(
        link
        for link in _graph_links(source)
        if link.kind == "endpoint" and link.owner == ("polyadic_relations", index)
    )
    assert not _detachment_has_link(
        surviving_endpoint,
        source,
        source,
        relation_level,
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
