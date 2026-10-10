"""Split and merge ordered containment without changing child order."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any, Literal, cast

import pytest

from tests.test_shift import (
    CLOCK_BINDING,
    PHRASE,
    PHRASE_WORDS,
    SEGMENT,
    SOURCE_LAYER,
    SOURCE_OFFSET,
    SYLLABLE,
    TOKEN,
    UNTIMED,
    UTTERANCE,
    UTTERANCE_PHRASES,
    WORD,
    containment,
    fully_timed_hierarchy,
    hierarchy,
    name,
    rich_content,
)
from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    Boundary,
    BoundaryRef,
    BoundarySide,
    ClockEditOperation,
    ClockProfile,
    ClockRebindingPolicy,
    CostTable,
    DurableBoundaryRef,
    DurableItemRef,
    Graph,
    GraphValidationError,
    Item,
    ItemRef,
    Journal,
    Layer,
    LayerFact,
    LayerName,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    QualifiedName,
    Refusal,
    RegroupPolicies,
    RegroupRestoration,
    RelationEndpointKind,
    RelationInstance,
    RelationSideDeclaration,
    ReplacementAction,
    SimpleRelationDeclaration,
    SubtreeCorrespondence,
    Tier,
    TierDeclaration,
    XsdType,
    merge_containers,
    patch_dumps,
    patch_loads,
    split_container,
)
from tiergraph.container_edit import _merge_outcome, _split_outcome
from tiergraph.distance import _atom_multiset_lower_bound
from tiergraph.edit import _operation, _OperationPair, link_ledger
from tiergraph.equivalence import EquivalenceView
from tiergraph.machine import DeltaOpcode, _argument_data, _decode_edit_argument


def _durable_ids(graph: Graph, tier: QualifiedName) -> list[str | None]:
    """Return tier item IDs from a graph fixture."""
    return [item.durable_id for item in graph._tiers_by_name[tier].items]


def _cross_tier_phrase_link(*targets: str) -> tuple[Graph, QualifiedName]:
    """Add a third phrase and one Token-to-Phrase polyadic link."""
    relation = name("token-phrases")
    source = hierarchy()
    tiers = tuple(
        replace(tier, items=(*tier.items, Item("p2")))
        if tier.declaration.name == PHRASE
        else tier
        for tier in source.tiers
    )
    tiers = (*tiers, Tier(TierDeclaration(TOKEN, "Tokens"), (Item("t0"),)))
    endpoint = (RelationEndpointKind.ITEM,)
    declaration = PolyadicRelationDeclaration(
        relation,
        RelationSideDeclaration(endpoint, tiers=(TOKEN,), maximum=1),
        RelationSideDeclaration(endpoint, tiers=(PHRASE,), allow_empty=True),
    )
    utterance = replace(
        source.polyadic_relations[0],
        targets=(*source.polyadic_relations[0].targets, DurableItemRef("p2")),
    )
    linked = PolyadicRelationInstance(
        relation,
        (DurableItemRef("t0"),),
        tuple(DurableItemRef(target) for target in targets),
        "token-phrases-0",
    )
    graph = replace(
        source,
        tiers=tiers,
        relation_declarations=(*source.relation_declarations, declaration),
        polyadic_relations=(utterance, *source.polyadic_relations[1:], linked),
    )
    return graph, relation


@pytest.mark.parametrize(
    ("side", "expected", "original_targets", "new_targets"),
    [
        ("after", ["p0", "px", "p1"], ["w0"], ["w1", "w2"]),
        ("before", ["px", "p0", "p1"], ["w1", "w2"], ["w0"]),
    ],
)
def test_split_preserves_declared_order_and_linear_identity(
    side: Literal["before", "after"],
    expected: list[str],
    original_targets: list[str],
    new_targets: list[str],
) -> None:
    """Either split side keeps order while only the original keeps identity."""
    graph = hierarchy()
    editor = graph.edit()
    editor.split_container(
        DurableItemRef("p0"),
        1,
        PHRASE_WORDS,
        Item("px"),
        side,
    )
    changed = editor.freeze()
    assert _durable_ids(changed, PHRASE) == expected
    memberships = [
        relation
        for relation in changed.polyadic_relations
        if relation.declaration == PHRASE_WORDS
    ]
    by_source = {}
    for relation in memberships:
        source = relation.sources[0]
        assert isinstance(source, ItemRef | DurableItemRef)
        by_source[changed.resolve_item(source)] = relation
    original = changed.resolve_item(DurableItemRef("p0"))
    new = changed.resolve_item(DurableItemRef("px"))
    assert [
        changed._tiers_by_name[WORD]
        .items[changed.resolve_item(cast(ItemRef | DurableItemRef, target)).index]
        .durable_id
        for target in by_source[original].targets
    ] == original_targets
    assert [
        changed._tiers_by_name[WORD]
        .items[changed.resolve_item(cast(ItemRef | DurableItemRef, target)).index]
        .durable_id
        for target in by_source[new].targets
    ] == new_targets
    correspondence = editor.last_correspondence
    assert correspondence is not None
    before = ItemRef(PHRASE, 0)
    assert len(correspondence.items[before]) == 2
    assert correspondence.identity_correspondence[before] == (
        changed.resolve_item(DurableItemRef("p0")),
    )
    assert editor.last_detachment is None


@pytest.mark.parametrize("survivor", ["p0", "p1"])
def test_merge_and_restoring_split_are_exact_for_either_survivor(survivor: str) -> None:
    """Merge retains the selected durable identity and its journal inverse is exact."""
    graph = hierarchy()
    journal = Journal()
    editor = graph.edit(journal=journal)
    editor.merge_containers(
        DurableItemRef("p0"),
        DurableItemRef("p1"),
        DurableItemRef(survivor),
        PHRASE_WORDS,
    )
    changed = editor.freeze()
    assert _durable_ids(changed, PHRASE) == [survivor]
    assert [
        changed._tiers_by_name[WORD]
        .items[changed.resolve_item(cast(ItemRef | DurableItemRef, target)).index]
        .durable_id
        for relation in changed.polyadic_relations
        if relation.declaration == PHRASE_WORDS
        for target in relation.targets
    ] == ["w0", "w1", "w2", "w3", "w4"]
    editor.undo()
    assert editor.freeze() == graph
    editor.redo()
    assert editor.freeze() == changed
    patch = journal.to_patch()
    assert isinstance(patch.operations[0].opcode, DeltaOpcode)
    assert patch.operations[0].opcode.changes == ()
    inverse = patch.operations[0].inverse
    assert isinstance(inverse, DeltaOpcode)
    assert inverse.changes == ()
    assert patch_loads(patch_dumps(patch)).apply(graph) == changed


def test_merge_repoints_a_cross_tier_polyadic_target() -> None:
    """A link into the departing container follows it to the survivor."""
    graph, relation = _cross_tier_phrase_link("p1")
    merged = graph.merge_containers(
        DurableItemRef("p0"),
        DurableItemRef("p1"),
        DurableItemRef("p0"),
        PHRASE_WORDS,
    )
    linked = next(
        instance
        for instance in merged.polyadic_relations
        if instance.declaration == relation
    )
    assert linked.targets == (DurableItemRef("p0"),)
    assert tuple(tier for tier in merged.tiers if tier.declaration.name != PHRASE) == (
        tuple(tier for tier in graph.tiers if tier.declaration.name != PHRASE)
    )
    assert merged.relations == graph.relations
    assert merged.boundary_values == graph.boundary_values
    assert merged.attributes == graph.attributes
    assert merged.layers == graph.layers
    unaffected = {relation, PHRASE_WORDS, UTTERANCE_PHRASES}
    assert tuple(
        instance
        for instance in merged.polyadic_relations
        if instance.declaration not in unaffected
    ) == tuple(
        instance
        for instance in graph.polyadic_relations
        if instance.declaration not in unaffected
    )


def test_merge_refuses_collapsing_distinct_polyadic_targets() -> None:
    """A relation cannot silently turn two container targets into duplicates."""
    graph, relation = _cross_tier_phrase_link("p0", "p1")
    with pytest.raises(GraphValidationError, match="duplicate or collapse"):
        graph.merge_containers(
            DurableItemRef("p0"),
            DurableItemRef("p1"),
            DurableItemRef("p0"),
            PHRASE_WORDS,
        )
    dropped = merge_containers(
        graph,
        DurableItemRef("p0"),
        DurableItemRef("p1"),
        DurableItemRef("p0"),
        PHRASE_WORDS,
        RegroupPolicies(relations={relation: ReplacementAction.DROP}),
    )
    assert all(
        instance.declaration != relation
        for instance in dropped.graph.polyadic_relations
    )
    assert any(
        instance.declaration == relation for _, instance in dropped.report.relations
    )


def test_merge_refuses_collapsing_distinct_binary_endpoints() -> None:
    """A binary relation cannot collapse its two container endpoints."""
    graph = hierarchy()
    relation = name("phrase-link")
    linked = replace(
        graph,
        relation_declarations=(
            *graph.relation_declarations,
            BipartiteRelationDeclaration(
                relation, name("phrase-type"), name("phrase-type")
            ),
        ),
        relations=(
            *graph.relations,
            RelationInstance(relation, DurableItemRef("p0"), DurableItemRef("p1")),
        ),
    )
    with pytest.raises(GraphValidationError, match="collapse distinct"):
        linked.merge_containers(
            DurableItemRef("p0"),
            DurableItemRef("p1"),
            DurableItemRef("p0"),
            PHRASE_WORDS,
        )


def test_checked_merge_refuses_a_lost_noncontainment_sister_target() -> None:
    """Only a containment parent's two sister slots may share one ledger image."""
    graph, relation = _cross_tier_phrase_link("p0", "p1")
    base = replace(
        graph,
        relation_declarations=tuple(
            declaration
            for declaration in graph.relation_declarations
            if declaration.name != relation
        ),
        polyadic_relations=tuple(
            instance
            for instance in graph.polyadic_relations
            if instance.declaration != relation
        ),
    )
    outcome = _merge_outcome(
        base,
        DurableItemRef("p0"),
        DurableItemRef("p1"),
        DurableItemRef("p0"),
        PHRASE_WORDS,
    )
    lost = replace(
        outcome.graph,
        relation_declarations=graph.relation_declarations,
        polyadic_relations=(
            *outcome.graph.polyadic_relations,
            PolyadicRelationInstance(
                relation,
                (DurableItemRef("t0"),),
                (DurableItemRef("p0"),),
                "token-phrases-0",
            ),
        ),
    )
    with pytest.raises(GraphValidationError, match="unreported dropped endpoint link"):
        link_ledger(
            graph,
            lost,
            (outcome.report, outcome.displacement, outcome.correspondence),
            operation="merge_containers",
        )


@pytest.mark.parametrize("survivor", ["p0", "p1"])
def test_merge_inverse_restores_interleaved_membership_positions(
    survivor: str,
) -> None:
    """The restoring split preserves document order outside the merged pair."""
    source = hierarchy()
    relations = source.polyadic_relations
    graph = replace(
        source,
        polyadic_relations=(
            relations[0],
            relations[1],
            relations[3],
            relations[2],
            *relations[4:],
        ),
    )
    journal = Journal()
    editor = graph.edit(journal=journal, check_links=True)
    editor.merge_containers(
        DurableItemRef("p0"),
        DurableItemRef("p1"),
        DurableItemRef(survivor),
        PHRASE_WORDS,
    )
    changed = editor.freeze()
    patch = journal.to_patch()
    assert patch.invert().apply(changed) == graph
    editor.undo()
    assert editor.freeze() == graph


def test_split_journal_patch_is_semantic_and_wire_round_trips() -> None:
    """A split and its merge inverse replay without residual document edits."""
    graph = hierarchy()
    journal = Journal()
    editor = graph.edit(journal=journal)
    editor.split_container(ItemRef(PHRASE, 0), 2, PHRASE_WORDS, Item("px"))
    changed = editor.freeze()
    patch = journal.to_patch()
    opcode = patch.operations[0].opcode
    assert isinstance(opcode, DeltaOpcode)
    assert opcode.calls[0].method == "split_container"
    assert opcode.changes == ()
    inverse = patch.operations[0].inverse
    assert isinstance(inverse, DeltaOpcode)
    assert inverse.calls[0].method == "merge_containers"
    assert inverse.changes == ()
    decoded = patch_loads(patch_dumps(patch))
    assert decoded.apply(graph) == changed
    assert decoded.invert().apply(changed) == graph


def test_regroup_residue_refusal_is_not_rewritten_as_delta() -> None:
    """A bad semantic split call refuses outside the legacy delta fallback."""
    graph = hierarchy()
    journal = Journal()
    editor = graph.edit(journal=journal)
    editor.split_container(ItemRef(PHRASE, 0), 1, PHRASE_WORDS, Item("px"))
    record = journal.records[0]
    operations = record._patch_operations
    assert operations is not None
    wrong = _OperationPair(
        _operation(
            "split_container",
            ItemRef(PHRASE, 0),
            2,
            PHRASE_WORDS,
            Item("px"),
            "after",
            None,
            None,
        ),
        operations.inverse,
    )
    object.__setattr__(record, "_patch_operations", wrong)
    with pytest.raises(GraphValidationError, match="does not reproduce its target"):
        journal.to_patch()

    inverse_journal = Journal()
    inverse_editor = graph.edit(journal=inverse_journal)
    inverse_editor.split_container(ItemRef(PHRASE, 0), 1, PHRASE_WORDS, Item("px"))
    inverse_record = inverse_journal.records[0]
    inverse_operations = inverse_record._patch_operations
    assert inverse_operations is not None
    wrong_inverse = _OperationPair(
        inverse_operations.forward,
        _operation(
            "merge_containers",
            DurableItemRef("p0"),
            DurableItemRef("px"),
            DurableItemRef("px"),
            PHRASE_WORDS,
            None,
        ),
    )
    object.__setattr__(inverse_record, "_patch_operations", wrong_inverse)
    with pytest.raises(GraphValidationError, match="does not reproduce its target"):
        inverse_journal.to_patch()


@pytest.mark.parametrize("journaled", [False, True])
@pytest.mark.parametrize("operation", ["split", "merge"])
def test_checked_regroup_accounts_for_cross_membership_endpoints(
    journaled: bool, operation: str
) -> None:
    """The link ledger follows children moved across membership instances."""
    graph = hierarchy()
    journal = Journal() if journaled else None
    editor = (
        graph.edit(journal=journal, check_links=True)
        if journal is not None
        else graph.edit(check_links=True)
    )
    if operation == "split":
        editor.split_container(DurableItemRef("p0"), 1, PHRASE_WORDS, Item("px"))
        assert _durable_ids(editor.freeze(), PHRASE) == ["p0", "px", "p1"]
    else:
        editor.merge_containers(
            DurableItemRef("p0"),
            DurableItemRef("p1"),
            DurableItemRef("p0"),
            PHRASE_WORDS,
        )
        assert _durable_ids(editor.freeze(), PHRASE) == ["p0"]


def test_split_clears_a_previous_detachment_report() -> None:
    """A prior lossy edit cannot authorize an unreported split loss."""
    graph = hierarchy()
    editor = graph.edit()
    editor._last_detachment = _merge_outcome(
        graph,
        DurableItemRef("p0"),
        DurableItemRef("p1"),
        DurableItemRef("p0"),
        PHRASE_WORDS,
        None,
    ).report
    editor.split_container(DurableItemRef("p0"), 1, PHRASE_WORDS, Item("px"))
    assert editor.last_detachment is None


def test_merge_restoration_returns_a_carried_attribute_to_its_item() -> None:
    """The original item payload restores a value carried during merge."""
    graph = rich_content().set_attribute(
        DurableItemRef("p1"),
        AttributeValue(SOURCE_OFFSET, XsdType.INTEGER, "17"),
    )
    outcome = _merge_outcome(
        graph,
        DurableItemRef("p0"),
        DurableItemRef("p1"),
        DurableItemRef("p0"),
        PHRASE_WORDS,
        None,
    )
    assert outcome.restoration is not None
    restored = outcome.graph.split_container(
        DurableItemRef("p0"),
        3,
        PHRASE_WORDS,
        graph._tiers_by_name[PHRASE].items[1],
        "after",
        None,
        outcome.restoration,
    )
    assert restored == graph


def test_clock_reports_use_regroup_operation_names() -> None:
    """Timed regroup reports split and merge rather than a surrogate operation."""
    profile = fully_timed_hierarchy()
    split = profile.edit(ClockRebindingPolicy.KEEP_EARLIER)
    split.split_container(DurableItemRef("p0"), 1, PHRASE_WORDS, Item("px"))
    assert split.reports[-1].operation is ClockEditOperation.SPLIT_CONTAINER
    assert split.profile.clock_index(BoundaryRef(PHRASE, 1)) == 3

    merge = profile.edit(ClockRebindingPolicy.DROP_TO_PROVISIONAL)
    merge.merge_containers(
        DurableItemRef("p0"),
        DurableItemRef("p1"),
        DurableItemRef("p0"),
        PHRASE_WORDS,
        RegroupPolicies(clock=ClockRebindingPolicy.DROP_TO_PROVISIONAL),
    )
    assert merge.reports[-1].operation is ClockEditOperation.MERGE_CONTAINERS
    assert [
        merge.profile.clock_index(BoundaryRef(PHRASE, index)) for index in range(2)
    ] == [0, 9]
    assert (
        sum(
            relation.declaration == CLOCK_BINDING
            and merge.freeze()
            .resolve_boundary(cast(BoundaryRef | DurableBoundaryRef, relation.left))
            .tier
            == PHRASE
            for relation in merge.freeze().relations
        )
        == 2
    )


@pytest.mark.parametrize(
    ("operation", "policy"),
    [
        ("split", ClockRebindingPolicy.KEEP_EARLIER),
        ("merge", ClockRebindingPolicy.DROP_TO_PROVISIONAL),
    ],
)
def test_clock_journal_regroup_has_exact_semantic_patch(
    operation: str, policy: ClockRebindingPolicy
) -> None:
    """Clock journals replay regrouping without a residual delta."""
    profile = fully_timed_hierarchy()
    journal = Journal()
    editor = profile.edit(policy, journal=journal, check_links=True)
    if operation == "split":
        editor.split_container(
            DurableItemRef("p0"), 1, PHRASE_WORDS, Item("clock-split")
        )
    else:
        editor.merge_containers(
            DurableItemRef("p0"),
            DurableItemRef("p1"),
            DurableItemRef("p0"),
            PHRASE_WORDS,
        )
    changed = editor.freeze()
    patch = journal.to_patch()
    opcode = patch.operations[0].opcode
    inverse = patch.operations[0].inverse
    assert isinstance(opcode, DeltaOpcode)
    assert isinstance(inverse, DeltaOpcode)
    assert opcode.changes == ()
    assert inverse.changes == ()
    assert patch_loads(patch_dumps(patch)).apply(profile.graph) == changed
    editor.undo()
    assert editor.freeze() == profile.graph


def test_clock_merge_inverse_restores_interleaved_binding_position() -> None:
    """The restoring split returns a retired seam binding to document order."""
    source = fully_timed_hierarchy()
    relations = source.graph.relations
    graph = replace(
        source.graph,
        relations=(*relations[:3], *relations[4:], relations[3]),
    )
    profile = replace(source, graph=graph)
    journal = Journal()
    editor = profile.edit(
        ClockRebindingPolicy.DROP_TO_PROVISIONAL,
        journal=journal,
        check_links=True,
    )
    editor.merge_containers(
        DurableItemRef("p0"),
        DurableItemRef("p1"),
        DurableItemRef("p0"),
        PHRASE_WORDS,
    )
    changed = editor.freeze()
    assert journal.to_patch().invert().apply(changed) == graph
    editor.undo()
    assert editor.freeze() == graph


def test_clock_regroup_requires_the_operation_specific_policy() -> None:
    """Timed split needs a policy and timed merge admits only seam withdrawal."""
    profile = fully_timed_hierarchy()
    with pytest.raises(GraphValidationError, match="requires a named rebinding policy"):
        profile.edit().split_container(
            DurableItemRef("p0"), 1, PHRASE_WORDS, Item("px")
        )
    with pytest.raises(GraphValidationError, match="requires 'drop-to-provisional'"):
        profile.edit(ClockRebindingPolicy.KEEP_EARLIER).merge_containers(
            DurableItemRef("p0"),
            DurableItemRef("p1"),
            DurableItemRef("p0"),
            PHRASE_WORDS,
        )
    with pytest.raises(GraphValidationError, match="requires 'keep-earlier'"):
        profile.edit(ClockRebindingPolicy.DROP_TO_PROVISIONAL).split_container(
            DurableItemRef("p0"), 1, PHRASE_WORDS, Item("px")
        )
    with pytest.raises(GraphValidationError, match="requires a named clock policy"):
        profile.graph.split_container(
            DurableItemRef("p0"), 1, PHRASE_WORDS, Item("plain-px")
        )
    with pytest.raises(GraphValidationError, match="requires a named clock policy"):
        profile.graph.merge_containers(
            DurableItemRef("p0"),
            DurableItemRef("p1"),
            DurableItemRef("p0"),
            PHRASE_WORDS,
        )
    with pytest.raises(GraphValidationError, match="requires 'drop-to-provisional'"):
        profile.graph.merge_containers(
            DurableItemRef("p0"),
            DurableItemRef("p1"),
            DurableItemRef("p0"),
            PHRASE_WORDS,
            RegroupPolicies(clock=ClockRebindingPolicy.KEEP_EARLIER),
        )
    with pytest.raises(GraphValidationError, match="requires 'keep-earlier'"):
        profile.graph.split_container(
            DurableItemRef("p0"),
            1,
            PHRASE_WORDS,
            Item("wrong-policy"),
            policies=RegroupPolicies(clock=ClockRebindingPolicy.DROP_TO_PROVISIONAL),
        )
    plain = profile.graph.split_container(
        DurableItemRef("p0"),
        1,
        PHRASE_WORDS,
        Item("plain-px"),
        policies=RegroupPolicies(clock=ClockRebindingPolicy.KEEP_EARLIER),
    )
    ClockProfile(plain, profile.clock_tier, CLOCK_BINDING, None, profile.unit_attribute)

    child_seam = BoundaryRef(WORD, 1)
    missing_child_seam = replace(
        profile.graph,
        relations=tuple(
            relation
            for relation in profile.graph.relations
            if not (
                relation.declaration == CLOCK_BINDING
                and profile.graph.resolve_boundary(
                    cast(DurableBoundaryRef, relation.left)
                )
                == child_seam
            )
        ),
    )
    with pytest.raises(GraphValidationError, match="unambiguous clock binding"):
        missing_child_seam.split_container(
            DurableItemRef("p0"),
            1,
            PHRASE_WORDS,
            Item("missing-child-seam"),
            policies=RegroupPolicies(clock=ClockRebindingPolicy.KEEP_EARLIER),
        )


def test_clock_merge_refuses_containment_seam_disagreement() -> None:
    """A retired parent seam must resolve to its contiguous child seam."""
    profile = fully_timed_hierarchy()
    relations = list(profile.graph.relations)
    seam = BoundaryRef(PHRASE, 1)
    index = next(
        position
        for position, relation in enumerate(relations)
        if profile.graph.resolve_boundary(cast(DurableBoundaryRef, relation.left))
        == seam
    )
    relations[index] = replace(
        relations[index],
        right=DurableBoundaryRef(DurableItemRef("c8"), BoundarySide.BEFORE),
    )
    disagreeing = replace(profile.graph, relations=tuple(relations))
    disagreeing_profile = ClockProfile(
        disagreeing,
        profile.clock_tier,
        CLOCK_BINDING,
        None,
        profile.unit_attribute,
    )
    with pytest.raises(GraphValidationError, match="parent seam and child seam"):
        disagreeing_profile.edit(
            ClockRebindingPolicy.DROP_TO_PROVISIONAL
        ).merge_containers(
            DurableItemRef("p0"),
            DurableItemRef("p1"),
            DurableItemRef("p0"),
            PHRASE_WORDS,
        )

    child_seam = BoundaryRef(WORD, 3)
    missing_child_seam = replace(
        profile.graph,
        relations=tuple(
            relation
            for relation in profile.graph.relations
            if not (
                relation.declaration == CLOCK_BINDING
                and profile.graph.resolve_boundary(
                    cast(DurableBoundaryRef, relation.left)
                )
                == child_seam
            )
        ),
    )
    with pytest.raises(GraphValidationError, match="unambiguous clock binding"):
        missing_child_seam.merge_containers(
            DurableItemRef("p0"),
            DurableItemRef("p1"),
            DurableItemRef("p0"),
            PHRASE_WORDS,
            RegroupPolicies(clock=ClockRebindingPolicy.DROP_TO_PROVISIONAL),
        )


def _untimed_container_profile() -> tuple[ClockProfile, QualifiedName]:
    """Return a valid clock profile with an untimed Token containment tier."""
    source = hierarchy()
    token_words = name("token-words")
    graph = replace(
        source,
        tiers=(
            *source.tiers,
            Tier(TierDeclaration(TOKEN, "Tokens"), (Item("t0"), Item("t1"))),
        ),
        relation_declarations=(
            *source.relation_declarations,
            SimpleRelationDeclaration(name("token-members"), TOKEN, name("timed-type")),
            containment(token_words, TOKEN, WORD),
        ),
        polyadic_relations=(
            *source.polyadic_relations,
            PolyadicRelationInstance(
                token_words,
                (DurableItemRef("t0"),),
                tuple(ItemRef(WORD, index) for index in range(3)),
            ),
            PolyadicRelationInstance(
                token_words,
                (DurableItemRef("t1"),),
                tuple(ItemRef(WORD, index) for index in range(3, 5)),
            ),
        ),
    )
    profile = fully_timed_hierarchy(
        graph,
        {
            UTTERANCE: (0, 9),
            PHRASE: (0, 7, 9),
            WORD: (0, 3, 6, 7, 8, 9),
            SYLLABLE: (0, 3, 6, 7, 8, 9),
            SEGMENT: tuple(range(10)),
            TOKEN: (0, 3, 9),
        },
    )
    marked = replace(
        profile.graph,
        tiers=tuple(
            replace(
                tier,
                attributes=(AttributeValue(UNTIMED, XsdType.BOOLEAN, "true"),),
            )
            if tier.declaration.name == TOKEN
            else tier
            for tier in profile.graph.tiers
        ),
        attribute_declarations=(
            *profile.graph.attribute_declarations,
            AttributeDeclaration(UNTIMED, AttributeDomain.TIER, XsdType.BOOLEAN),
        ),
        relations=tuple(
            relation
            for relation in profile.graph.relations
            if profile.graph.resolve_boundary(
                cast(BoundaryRef | DurableBoundaryRef, relation.left)
            ).tier
            != TOKEN
        ),
    )
    return replace(profile, graph=marked, untimed_attribute=UNTIMED), token_words


def test_clock_regroup_validates_inputs_and_supports_untimed_containers() -> None:
    """Clock editors validate local inputs and delegate untimed regroup unchanged."""
    profile = fully_timed_hierarchy()
    editor = profile.edit(ClockRebindingPolicy.KEEP_EARLIER)
    with pytest.raises(TypeError, match="policies"):
        editor.split_container(
            DurableItemRef("p0"),
            1,
            PHRASE_WORDS,
            policies=object(),  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="restoration"):
        editor.split_container(
            DurableItemRef("p0"),
            1,
            PHRASE_WORDS,
            restoration=object(),  # type: ignore[arg-type]
        )
    with pytest.raises(GraphValidationError, match="clock tier"):
        editor.split_container(ItemRef(profile.clock_tier, 0), 1, PHRASE_WORDS)
    with pytest.raises(GraphValidationError, match="has no"):
        editor.split_container(DurableItemRef("p0"), 1, UTTERANCE_PHRASES)
    with pytest.raises(GraphValidationError, match="split position"):
        editor.split_container(DurableItemRef("p0"), 0, PHRASE_WORDS)
    with pytest.raises(TypeError, match="policies"):
        editor.merge_containers(
            DurableItemRef("p0"),
            DurableItemRef("p1"),
            DurableItemRef("p0"),
            PHRASE_WORDS,
            object(),  # type: ignore[arg-type]
        )
    with pytest.raises(GraphValidationError, match="clock tier"):
        editor.merge_containers(
            ItemRef(profile.clock_tier, 0),
            ItemRef(profile.clock_tier, 1),
            ItemRef(profile.clock_tier, 0),
            PHRASE_WORDS,
        )

    untimed, token_words = _untimed_container_profile()
    split = untimed.edit(ClockRebindingPolicy.KEEP_EARLIER)
    split.split_container(DurableItemRef("t0"), 1, token_words, Item("tx"))
    assert _durable_ids(split.freeze(), TOKEN) == ["t0", "tx", "t1"]
    merge = untimed.edit(ClockRebindingPolicy.KEEP_EARLIER)
    merge.merge_containers(
        DurableItemRef("t0"), DurableItemRef("t1"), DurableItemRef("t0"), token_words
    )
    assert _durable_ids(merge.freeze(), TOKEN) == ["t0"]
    with pytest.raises(GraphValidationError, match="requires a named rebinding policy"):
        profile.edit().merge_containers(
            DurableItemRef("p0"),
            DurableItemRef("p1"),
            DurableItemRef("p0"),
            PHRASE_WORDS,
        )

    untimed_split_journal = Journal()
    untimed.edit(
        ClockRebindingPolicy.KEEP_EARLIER, journal=untimed_split_journal
    ).split_container(DurableItemRef("t0"), 1, token_words, Item("tj"))
    untimed_merge_journal = Journal()
    untimed.edit(
        ClockRebindingPolicy.KEEP_EARLIER, journal=untimed_merge_journal
    ).merge_containers(
        DurableItemRef("t0"), DurableItemRef("t1"), DurableItemRef("t0"), token_words
    )

    journaled = profile.edit(ClockRebindingPolicy.KEEP_EARLIER, journal=Journal())
    with pytest.raises(TypeError, match="policies"):
        journaled.split_container(
            DurableItemRef("p0"),
            1,
            PHRASE_WORDS,
            policies=object(),  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="restoration"):
        journaled.split_container(
            DurableItemRef("p0"),
            1,
            PHRASE_WORDS,
            restoration=object(),  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="policies"):
        journaled.merge_containers(
            DurableItemRef("p0"),
            DurableItemRef("p1"),
            DurableItemRef("p0"),
            PHRASE_WORDS,
            object(),  # type: ignore[arg-type]
        )


def test_clock_split_refuses_an_untimed_child_seam() -> None:
    """A timed container cannot infer a clock coordinate from untimed children."""
    source = hierarchy()
    phrase_tokens = name("phrase-tokens")
    graph = replace(
        source,
        tiers=(
            *source.tiers,
            Tier(
                TierDeclaration(TOKEN, "Tokens"),
                (Item("t0"), Item("t1"), Item("t2")),
            ),
        ),
        relation_declarations=(
            *source.relation_declarations,
            SimpleRelationDeclaration(name("token-members"), TOKEN, name("timed-type")),
            containment(phrase_tokens, PHRASE, TOKEN),
        ),
        polyadic_relations=(
            *source.polyadic_relations,
            PolyadicRelationInstance(
                phrase_tokens,
                (DurableItemRef("p0"),),
                (DurableItemRef("t0"), DurableItemRef("t1")),
            ),
            PolyadicRelationInstance(
                phrase_tokens,
                (DurableItemRef("p1"),),
                (DurableItemRef("t2"),),
            ),
        ),
    )
    complete = fully_timed_hierarchy(
        graph,
        {
            UTTERANCE: (0, 9),
            PHRASE: (0, 7, 9),
            WORD: (0, 3, 6, 7, 8, 9),
            SYLLABLE: (0, 3, 6, 7, 8, 9),
            SEGMENT: tuple(range(10)),
            TOKEN: (0, 3, 7, 9),
        },
    )
    marked = replace(
        complete.graph,
        tiers=tuple(
            replace(
                tier,
                attributes=(AttributeValue(UNTIMED, XsdType.BOOLEAN, "true"),),
            )
            if tier.declaration.name == TOKEN
            else tier
            for tier in complete.graph.tiers
        ),
        attribute_declarations=(
            *complete.graph.attribute_declarations,
            AttributeDeclaration(UNTIMED, AttributeDomain.TIER, XsdType.BOOLEAN),
        ),
        relations=tuple(
            relation
            for relation in complete.graph.relations
            if complete.graph.resolve_boundary(
                cast(BoundaryRef | DurableBoundaryRef, relation.left)
            ).tier
            != TOKEN
        ),
    )
    profile = replace(complete, graph=marked, untimed_attribute=UNTIMED)
    with pytest.raises(GraphValidationError, match="child seam.*untimed"):
        profile.edit(ClockRebindingPolicy.KEEP_EARLIER).split_container(
            DurableItemRef("p0"), 1, phrase_tokens, Item("px")
        )


def test_clock_regroup_ignores_nonboundary_relations_during_inference() -> None:
    """Clock inference reads only complete boundary relations."""
    profile = fully_timed_hierarchy()
    link = name("timed-link")
    graph = replace(
        profile.graph,
        relation_declarations=(
            *profile.graph.relation_declarations,
            BipartiteRelationDeclaration(link, name("timed-type"), name("timed-type")),
        ),
        relations=(
            RelationInstance(link, DurableItemRef("p0"), DurableItemRef("p1")),
            *profile.graph.relations,
        ),
    )
    extended = replace(profile, graph=graph)
    extended.edit(ClockRebindingPolicy.KEEP_EARLIER).split_container(
        DurableItemRef("p0"), 1, PHRASE_WORDS
    )


def test_merge_requires_exact_attribute_and_seam_drop_policies() -> None:
    """Unequal values and independent seam content are never lost implicitly."""
    left = AttributeValue(SOURCE_OFFSET, XsdType.INTEGER, "1")
    right = AttributeValue(SOURCE_OFFSET, XsdType.INTEGER, "2")
    graph = rich_content().set_attribute(DurableItemRef("p0"), left)
    graph = graph.set_attribute(DurableItemRef("p1"), right)
    with pytest.raises(GraphValidationError, match="conflicts across merge"):
        graph.merge_containers(
            DurableItemRef("p0"),
            DurableItemRef("p1"),
            DurableItemRef("p0"),
            PHRASE_WORDS,
        )
    changed = graph.merge_containers(
        DurableItemRef("p0"),
        DurableItemRef("p1"),
        DurableItemRef("p0"),
        PHRASE_WORDS,
        RegroupPolicies(attributes={SOURCE_OFFSET: ReplacementAction.DROP}),
    )
    assert changed._tiers_by_name[PHRASE].items[0].attributes == (left,)

    equal = rich_content().set_attribute(DurableItemRef("p0"), left)
    equal = equal.set_attribute(DurableItemRef("p1"), left)
    coalesced = equal.merge_containers(
        DurableItemRef("p0"), DurableItemRef("p1"), DurableItemRef("p0"), PHRASE_WORDS
    )
    assert coalesced._tiers_by_name[PHRASE].items[0].attributes == (left,)


def test_partial_cost_table_returns_zero_atom_bound_after_vocabulary_growth() -> None:
    """Omitting regroup costs makes the general atom relaxation conservatively zero."""
    graph = hierarchy()
    changed = graph.split_container(DurableItemRef("p0"), 1, PHRASE_WORDS, Item("px"))
    partial = CostTable({"insert_item": 1, "remove_item": 1})
    assert _atom_multiset_lower_bound(
        graph, changed, partial, EquivalenceView.FUNCTIONAL
    ) == Decimal(0)


def test_regroup_policy_maps_are_detached_and_actions_are_narrow() -> None:
    """Callers cannot mutate policy routing and replacement-only actions refuse."""
    routes = {SOURCE_OFFSET: ReplacementAction.DROP}
    policies = RegroupPolicies(attributes=routes)
    routes.clear()
    assert policies.attributes[SOURCE_OFFSET] is ReplacementAction.DROP
    with pytest.raises(ValueError, match="follow.*drop"):
        replace(policies, container_values=ReplacementAction.SPLIT)


def test_regroup_policy_and_restoration_payload_validation() -> None:
    """Regroup policy and inverse payloads reject malformed public values."""
    with pytest.raises(ValueError, match="unknown action"):
        RegroupPolicies(container_values="unknown")  # type: ignore[arg-type]
    routes = RegroupPolicies(
        relations={PHRASE_WORDS: ReplacementAction.DROP},
        layers={SOURCE_LAYER: ReplacementAction.FOLLOW},
    )
    assert routes.relations[PHRASE_WORDS] is ReplacementAction.DROP
    assert routes.layers[SOURCE_LAYER] is ReplacementAction.FOLLOW

    required = {
        "survivor_item": Item(),
        "membership_index": 0,
        "relation_count": 0,
        "polyadic_relation_count": 1,
        "polyadic_relations": ((0, hierarchy().polyadic_relations[0]),),
        "removed_polyadic_relation_positions": (0,),
    }
    binary = RelationInstance(
        name("unused"), DurableItemRef("p0"), DurableItemRef("p1")
    )
    polyadic = hierarchy().polyadic_relations[0]
    invalid = (
        {"survivor_item": object(), "membership_index": 0},
        {"survivor_item": Item(), "membership_index": True},
        {"relation_count": True},
        {"polyadic_relation_count": 0},
        {
            "relations": ((-1, object()),),
        },
        {
            "polyadic_relations": ((-1, object()),),
        },
        {
            "relation_count": 1,
            "relations": ((0, binary), (0, binary)),
        },
        {
            "polyadic_relations": ((0, polyadic), (0, polyadic)),
        },
        {
            "relation_count": 1,
            "relations": ((0, binary),),
            "removed_relation_positions": (True,),
        },
        {
            "relation_count": 1,
            "relations": ((0, binary),),
            "removed_relation_positions": (0, 0),
        },
        {
            "relation_count": 1,
            "removed_relation_positions": (0,),
        },
        {
            "removed_polyadic_relation_positions": (),
        },
        {
            "layers": ((object(), ()),),
        },
        {
            "seam_values": ((-1, object(), ()),),
        },
    )
    for fields in invalid:
        with pytest.raises((TypeError, ValueError)):
            RegroupRestoration(**(required | fields))  # type: ignore[arg-type]


def test_regroup_public_functions_and_editor_type_checks() -> None:
    """Functional entry points report edits and every editor rejects wrong policies."""
    graph = hierarchy()
    result = split_container(graph, DurableItemRef("p0"), 1, PHRASE_WORDS, Item("px"))
    assert result.graph != graph
    anonymous = graph.split_container(DurableItemRef("p0"), 1, PHRASE_WORDS)
    assert anonymous._tiers_by_name[PHRASE].items[1].durable_id is None
    merged = merge_containers(
        graph,
        DurableItemRef("p0"),
        DurableItemRef("p1"),
        DurableItemRef("p0"),
        PHRASE_WORDS,
    )
    assert merged.report.items
    with pytest.raises(TypeError, match="policies"):
        _split_outcome(
            graph,
            DurableItemRef("p0"),
            1,
            PHRASE_WORDS,
            policies=object(),  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="policies"):
        _merge_outcome(
            graph,
            DurableItemRef("p0"),
            DurableItemRef("p1"),
            DurableItemRef("p0"),
            PHRASE_WORDS,
            object(),  # type: ignore[arg-type]
        )
    editors = (graph.edit(), graph.edit(journal=Journal()))
    for editor in editors:
        with pytest.raises(TypeError, match="policies"):
            editor.split_container(
                DurableItemRef("p0"),
                1,
                PHRASE_WORDS,
                policies=object(),  # type: ignore[arg-type]
            )
        with pytest.raises(TypeError, match="restoration"):
            editor.split_container(
                DurableItemRef("p0"),
                1,
                PHRASE_WORDS,
                restoration=object(),  # type: ignore[arg-type]
            )
        with pytest.raises(TypeError, match="policies"):
            editor.merge_containers(
                DurableItemRef("p0"),
                DurableItemRef("p1"),
                DurableItemRef("p0"),
                PHRASE_WORDS,
                object(),  # type: ignore[arg-type]
            )


def test_split_refuses_malformed_or_noninterior_requests() -> None:
    """Split validates its shape, membership, policies, and new identity."""
    graph = hierarchy()
    cases = (
        {"side": "middle"},
        {"at": True},
        {"new_container": object()},
        {"container": DurableItemRef("u0")},
        {"at": 0},
        {"new_container": Item("p1")},
        {"policies": RegroupPolicies(relations={PHRASE_WORDS: ReplacementAction.DROP})},
    )
    base = {
        "container": DurableItemRef("p0"),
        "at": 1,
        "containment": PHRASE_WORDS,
        "new_container": Item("px"),
    }
    for changes in cases:
        with pytest.raises((TypeError, GraphValidationError)):
            _split_outcome(graph, **(base | changes))  # type: ignore[arg-type]

    gapped = replace(
        graph,
        polyadic_relations=(
            graph.polyadic_relations[0],
            replace(
                graph.polyadic_relations[1],
                targets=(ItemRef(WORD, 0), ItemRef(WORD, 1), ItemRef(WORD, 4)),
            ),
            replace(
                graph.polyadic_relations[2],
                targets=(ItemRef(WORD, 2), ItemRef(WORD, 3)),
            ),
            *graph.polyadic_relations[3:],
        ),
    )
    with pytest.raises(GraphValidationError, match="contiguous child-tier span"):
        gapped.split_container(
            DurableItemRef("p0"), 1, PHRASE_WORDS, Item("gapped-split")
        )


def test_merge_refuses_invalid_pair_topology() -> None:
    """Merge accepts only distinct adjacent sisters at one contiguous child seam."""
    graph = hierarchy()
    calls = (
        (DurableItemRef("p0"), DurableItemRef("p0"), DurableItemRef("p0")),
        (DurableItemRef("p0"), DurableItemRef("p1"), DurableItemRef("u0")),
        (DurableItemRef("p0"), DurableItemRef("u0"), DurableItemRef("p0")),
    )
    for first, second, survivor in calls:
        with pytest.raises(GraphValidationError):
            graph.merge_containers(first, second, survivor, PHRASE_WORDS)

    missing = replace(
        graph,
        polyadic_relations=graph.polyadic_relations[:2] + graph.polyadic_relations[3:],
    )
    with pytest.raises(GraphValidationError, match="memberships"):
        missing.merge_containers(
            DurableItemRef("p0"),
            DurableItemRef("p1"),
            DurableItemRef("p0"),
            PHRASE_WORDS,
        )
    noncontiguous = replace(
        graph,
        polyadic_relations=(
            graph.polyadic_relations[0],
            replace(
                graph.polyadic_relations[1],
                targets=(ItemRef(WORD, 0), ItemRef(WORD, 1)),
            ),
            *graph.polyadic_relations[2:],
        ),
    )
    with pytest.raises(GraphValidationError, match="contiguous"):
        noncontiguous.merge_containers(
            DurableItemRef("p0"),
            DurableItemRef("p1"),
            DurableItemRef("p0"),
            PHRASE_WORDS,
        )
    different_parents = replace(
        graph,
        polyadic_relations=(
            replace(graph.polyadic_relations[0], targets=(ItemRef(PHRASE, 0),)),
            *graph.polyadic_relations[1:],
        ),
    )
    with pytest.raises(GraphValidationError, match="share every"):
        different_parents.merge_containers(
            DurableItemRef("p0"),
            DurableItemRef("p1"),
            DurableItemRef("p0"),
            PHRASE_WORDS,
        )
    reversed_parent = replace(
        graph,
        polyadic_relations=(
            replace(
                graph.polyadic_relations[0],
                targets=(ItemRef(PHRASE, 1), ItemRef(PHRASE, 0)),
            ),
            *graph.polyadic_relations[1:],
        ),
    )
    with pytest.raises(GraphValidationError, match="left-right order"):
        reversed_parent.merge_containers(
            DurableItemRef("p0"),
            DurableItemRef("p1"),
            DurableItemRef("p0"),
            PHRASE_WORDS,
        )


def test_merge_routes_seam_content_and_binary_relations() -> None:
    """An independent seam value and relation need their explicit drop routes."""
    value_name = name("seam-value")
    relation_name = name("seam-link")
    value = AttributeValue(value_name, XsdType.STRING, "note")
    declaration = BipartiteRelationDeclaration(
        relation_name,
        name("phrase-type"),
        name("phrase-type"),
        RelationEndpointKind.BOUNDARY,
        RelationEndpointKind.BOUNDARY,
    )
    graph = hierarchy()
    graph = replace(
        graph,
        relation_declarations=(*graph.relation_declarations, declaration),
        relations=(
            RelationInstance(
                relation_name,
                DurableBoundaryRef(DurableItemRef("p1"), BoundarySide.BEFORE),
                DurableBoundaryRef(DurableItemRef("p0"), BoundarySide.BEFORE),
            ),
        ),
        attribute_declarations=(
            AttributeDeclaration(value_name, AttributeDomain.BOUNDARY, XsdType.STRING),
        ),
        boundary_values=(Boundary(BoundaryRef(PHRASE, 1), (value,)),),
        layers=(Layer(SOURCE_LAYER, (LayerFact(BoundaryRef(PHRASE, 1), value),)),),
    )
    with pytest.raises(GraphValidationError, match="seam content"):
        graph.merge_containers(
            DurableItemRef("p0"),
            DurableItemRef("p1"),
            DurableItemRef("p0"),
            PHRASE_WORDS,
        )
    seam = RegroupPolicies(seam_content=ReplacementAction.DROP)
    with pytest.raises(GraphValidationError, match="seam relation"):
        graph.merge_containers(
            DurableItemRef("p0"),
            DurableItemRef("p1"),
            DurableItemRef("p0"),
            PHRASE_WORDS,
            seam,
        )
    policies = RegroupPolicies(
        seam_content=ReplacementAction.DROP,
        relations={relation_name: ReplacementAction.DROP},
    )
    journal = Journal()
    editor = graph.edit(journal=journal)
    editor.merge_containers(
        DurableItemRef("p0"),
        DurableItemRef("p1"),
        DurableItemRef("p0"),
        PHRASE_WORDS,
        policies,
    )
    assert journal.records[0].report.detached_content is not None
    assert journal.records[0].report.detached_content.boundary_values
    editor.undo()
    assert editor.freeze() == graph


def test_merge_routes_polyadic_seam_relations_and_layer_collisions() -> None:
    """Polyadic seam links and conflicting layer facts refuse or drop by name."""
    seam_relation = name("seam-poly")
    side = RelationSideDeclaration((RelationEndpointKind.BOUNDARY,), tiers=(PHRASE,))
    declaration = PolyadicRelationDeclaration(seam_relation, side, side)
    value = AttributeValue(SOURCE_OFFSET, XsdType.INTEGER, "2")
    other = AttributeValue(SOURCE_OFFSET, XsdType.INTEGER, "3")
    source = rich_content()
    container_layer = LayerName(name("layers").namespace, "containers")
    graph = replace(
        source,
        relation_declarations=(*source.relation_declarations, declaration),
        polyadic_relations=(
            *source.polyadic_relations,
            PolyadicRelationInstance(
                seam_relation,
                (DurableBoundaryRef(DurableItemRef("p1"), BoundarySide.BEFORE),),
                (DurableBoundaryRef(DurableItemRef("p0"), BoundarySide.BEFORE),),
            ),
        ),
        layers=(
            *source.layers,
            Layer(
                container_layer,
                (
                    LayerFact(DurableItemRef("p0"), value),
                    LayerFact(DurableItemRef("p1"), other),
                ),
            ),
        ),
    )
    seam_policy = RegroupPolicies(relations={seam_relation: ReplacementAction.DROP})
    with pytest.raises(GraphValidationError, match="seam relation"):
        graph.merge_containers(
            DurableItemRef("p0"),
            DurableItemRef("p1"),
            DurableItemRef("p0"),
            PHRASE_WORDS,
        )
    with pytest.raises(GraphValidationError, match="conflicting facts"):
        graph.merge_containers(
            DurableItemRef("p0"),
            DurableItemRef("p1"),
            DurableItemRef("p0"),
            PHRASE_WORDS,
            seam_policy,
        )
    policies = replace(seam_policy, layers={container_layer: ReplacementAction.DROP})
    merged = graph.merge_containers(
        DurableItemRef("p0"),
        DurableItemRef("p1"),
        DurableItemRef("p0"),
        PHRASE_WORDS,
        policies,
    )
    assert (
        len(
            next(
                layer for layer in merged.layers if layer.name == container_layer
            ).facts
        )
        == 1
    )

    equal = replace(
        graph,
        layers=(
            *(layer for layer in graph.layers if layer.name != container_layer),
            replace(
                next(layer for layer in graph.layers if layer.name == container_layer),
                facts=(
                    LayerFact(DurableItemRef("p0"), value),
                    LayerFact(DurableItemRef("p1"), value),
                ),
            ),
        ),
    )
    coalesced = equal.merge_containers(
        DurableItemRef("p0"),
        DurableItemRef("p1"),
        DurableItemRef("p0"),
        PHRASE_WORDS,
        seam_policy,
    )
    assert (
        len(
            next(
                layer for layer in coalesced.layers if layer.name == container_layer
            ).facts
        )
        == 1
    )
    journal = Journal()
    journaled = equal.edit(journal=journal)
    journaled.merge_containers(
        DurableItemRef("p0"),
        DurableItemRef("p1"),
        DurableItemRef("p0"),
        PHRASE_WORDS,
        seam_policy,
    )
    journaled.undo()
    assert journaled.freeze() == equal


def test_split_restoration_positions_are_checked() -> None:
    """Restoration refuses carrier positions outside the original collections."""
    graph = hierarchy()
    relation = RelationInstance(
        name("unused"), DurableItemRef("p0"), DurableItemRef("p1")
    )
    polyadic = graph.polyadic_relations[0]
    required = {
        "survivor_item": Item("p0"),
        "membership_index": 0,
        "relation_count": 1,
        "polyadic_relation_count": 1,
        "polyadic_relations": ((0, polyadic),),
        "removed_polyadic_relation_positions": (0,),
    }
    malformed = (
        {"membership_index": 100},
        {"relations": ((1, relation),)},
        {"polyadic_relations": ((1, polyadic),)},
    )
    for changes in malformed:
        with pytest.raises((TypeError, ValueError), match="restoration"):
            RegroupRestoration(**(required | changes))  # type: ignore[arg-type]


def test_split_restoration_content_is_checked_against_the_merged_graph() -> None:
    """Restoration refuses inconsistent counts, layers, and boundary positions."""
    graph = hierarchy()
    outcome = _merge_outcome(
        graph,
        DurableItemRef("p0"),
        DurableItemRef("p1"),
        DurableItemRef("p0"),
        PHRASE_WORDS,
        None,
    )
    restoration = outcome.restoration
    assert restoration is not None
    invalid = (
        replace(restoration, relation_count=restoration.relation_count + 1),
        replace(
            restoration,
            layers=((LayerName(name("missing").namespace, "missing"), ()),),
        ),
        replace(
            restoration,
            seam_values=((100, BoundaryRef(PHRASE, 1), ()),),
        ),
    )
    removed_item = graph._tiers_by_name[PHRASE].items[1]
    for payload in invalid:
        with pytest.raises(GraphValidationError, match="restoration"):
            outcome.graph.split_container(
                DurableItemRef("p0"),
                3,
                PHRASE_WORDS,
                removed_item,
                restoration=payload,
            )


def test_regroup_machine_argument_refusals_are_staged() -> None:
    """The closed patch argument decoder rejects malformed regroup payloads."""
    correspondence = cast(dict[str, Any], _argument_data(SubtreeCorrespondence()))
    policies = cast(dict[str, Any], _argument_data(RegroupPolicies()))
    inverse = _merge_outcome(
        hierarchy(),
        DurableItemRef("p0"),
        DurableItemRef("p1"),
        DurableItemRef("p0"),
        PHRASE_WORDS,
        None,
    ).restoration
    assert inverse is not None
    restoration = cast(dict[str, Any], _argument_data(inverse))
    malformed = (
        {
            **correspondence,
            "value": {
                **correspondence["value"],
                "items": {"kind": "scalar", "value": None},
            },
        },
        {
            **correspondence,
            "value": {
                **correspondence["value"],
                "items": {
                    "kind": "tuple",
                    "items": [{"kind": "scalar", "value": "bad"}],
                },
            },
        },
        {
            **policies,
            "value": {
                **policies["value"],
                "relations": {"kind": "scalar", "value": None},
            },
        },
        {**policies, "value": {**policies["value"], "container_values": "unknown"}},
        {
            **restoration,
            "value": {
                **restoration["value"],
                "survivor_item": {"kind": "scalar", "value": None},
            },
        },
    )
    for value in malformed:
        with pytest.raises(Refusal):
            _decode_edit_argument(value, "argument")
