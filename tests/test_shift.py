"""Held run moves, unequal run swaps, and sister-container shifts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from tests import test_clock as clock_cases
from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    Boundary,
    BoundaryRef,
    BoundarySide,
    ClockProfile,
    CostTable,
    DurableBoundaryRef,
    DurableItemRef,
    EquivalenceView,
    Graph,
    GraphValidationError,
    Item,
    ItemRef,
    ItemRun,
    Journal,
    NamespaceDeclaration,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    QualifiedName,
    RelationEndpointKind,
    RelationInstance,
    RelationSideDeclaration,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    XsdType,
    anchored_boundary,
    cli,
    diff,
    dump_bytes,
    equivalent,
    loads,
    patch_dumps,
    patch_loads,
    price_patch,
)
from tiergraph.diff import _shift_patch
from tiergraph.distance import _atom_multiset_lower_bound
from tiergraph.machine import DeltaOpcode

NS = "urn:test:shift"


def name(local: str) -> QualifiedName:
    """Return one fixture-local qualified name."""
    return QualifiedName(NS, local)


UTTERANCE = name("utterance")
PHRASE = name("phrase")
WORD = name("word")
SYLLABLE = name("syllable")
SEGMENT = name("segment")
UTTERANCE_PHRASES = name("utterance-phrases")
PHRASE_WORDS = name("phrase-words")
WORD_SYLLABLES = name("word-syllables")
SYLLABLE_SEGMENTS = name("syllable-segments")
BOUND = name("bound")
BOUNDARY_NOTE = name("boundary-note")
CLOCK = name("clock")
CLOCK_TYPE = name("clock-type")
CLOCK_MEMBERS = name("clock-members")
CLOCK_BINDING = name("clock-binding")
UNIT = name("unit")
UNTIMED = name("untimed")


def side(tier: QualifiedName, *, one: bool = False) -> RelationSideDeclaration:
    """Return an item-only containment side for one tier."""
    return RelationSideDeclaration(
        (RelationEndpointKind.ITEM,), tiers=(tier,), maximum=1 if one else None
    )


def containment(
    relation: QualifiedName, parent: QualifiedName, child: QualifiedName
) -> PolyadicRelationDeclaration:
    """Return the fixture's ordered containment declaration shape."""
    return PolyadicRelationDeclaration(
        relation,
        side(parent, one=True),
        side(child),
        unique_sources=True,
        single_parent=True,
        acyclic=True,
    )


def hierarchy() -> Graph:
    """Return utterance > phrase > word > syllable > segment containment."""
    tiers = (
        Tier(TierDeclaration(UTTERANCE, "Utterances"), (Item("u0"),)),
        Tier(
            TierDeclaration(PHRASE, "Phrases"),
            (Item("p0"), Item("p1")),
        ),
        Tier(
            TierDeclaration(WORD, "Words"),
            tuple(Item(f"w{index}") for index in range(5)),
        ),
        Tier(
            TierDeclaration(SYLLABLE, "Syllables"),
            tuple(Item(f"y{index}") for index in range(5)),
        ),
        Tier(
            TierDeclaration(SEGMENT, "Segments"),
            tuple(Item(f"s{index}") for index in range(9)),
        ),
    )
    declarations: tuple[
        SimpleRelationDeclaration | PolyadicRelationDeclaration, ...
    ] = (
        *(
            SimpleRelationDeclaration(
                name(f"{tier.local_name}-members"),
                tier,
                name(f"{tier.local_name}-type"),
            )
            for tier in (UTTERANCE, PHRASE, WORD, SYLLABLE, SEGMENT)
        ),
        containment(UTTERANCE_PHRASES, UTTERANCE, PHRASE),
        containment(PHRASE_WORDS, PHRASE, WORD),
        containment(WORD_SYLLABLES, WORD, SYLLABLE),
        containment(SYLLABLE_SEGMENTS, SYLLABLE, SEGMENT),
    )
    instances = (
        PolyadicRelationInstance(
            UTTERANCE_PHRASES,
            (ItemRef(UTTERANCE, 0),),
            (ItemRef(PHRASE, 0), ItemRef(PHRASE, 1)),
        ),
        PolyadicRelationInstance(
            PHRASE_WORDS,
            (ItemRef(PHRASE, 0),),
            tuple(ItemRef(WORD, index) for index in range(3)),
        ),
        PolyadicRelationInstance(
            PHRASE_WORDS,
            (ItemRef(PHRASE, 1),),
            tuple(ItemRef(WORD, index) for index in range(3, 5)),
        ),
        *(
            PolyadicRelationInstance(
                WORD_SYLLABLES,
                (ItemRef(WORD, index),),
                (ItemRef(SYLLABLE, index),),
            )
            for index in range(5)
        ),
        PolyadicRelationInstance(
            SYLLABLE_SEGMENTS,
            (ItemRef(SYLLABLE, 0),),
            tuple(ItemRef(SEGMENT, index) for index in range(3)),
        ),
        PolyadicRelationInstance(
            SYLLABLE_SEGMENTS,
            (ItemRef(SYLLABLE, 1),),
            tuple(ItemRef(SEGMENT, index) for index in range(3, 6)),
        ),
        PolyadicRelationInstance(
            SYLLABLE_SEGMENTS,
            (ItemRef(SYLLABLE, 2),),
            (ItemRef(SEGMENT, 6),),
        ),
        PolyadicRelationInstance(
            SYLLABLE_SEGMENTS,
            (ItemRef(SYLLABLE, 3),),
            (ItemRef(SEGMENT, 7),),
        ),
    )
    return Graph(
        (NamespaceDeclaration("s", NS),),
        tiers,
        declarations,
        polyadic_relations=instances,
    )


def targets(
    graph: Graph, relation: QualifiedName, parent: ItemRef
) -> tuple[ItemRef, ...]:
    """Return resolved targets for one containment source."""
    for candidate in graph.polyadic_relations:
        if candidate.declaration != relation:
            continue
        source = candidate.sources[0]
        assert isinstance(source, (ItemRef, DurableItemRef))
        if graph.resolve_item(source) == parent:
            instance = candidate
            break
    else:
        raise AssertionError("containment instance not found")
    resolved: list[ItemRef] = []
    for target in instance.targets:
        assert isinstance(target, (ItemRef, DurableItemRef))
        resolved.append(graph.resolve_item(target))
    return tuple(resolved)


@pytest.mark.parametrize(
    ("relation", "container", "direction", "count", "expected"),
    [
        (PHRASE_WORDS, ItemRef(PHRASE, 0), "right", 1, ((0, 1), (2, 3, 4))),
        (PHRASE_WORDS, ItemRef(PHRASE, 0), "right", 2, ((0,), (1, 2, 3, 4))),
        (PHRASE_WORDS, ItemRef(PHRASE, 1), "left", 1, ((0, 1, 2, 3), (4,))),
        (SYLLABLE_SEGMENTS, ItemRef(SYLLABLE, 0), "right", 1, ((0, 1), (2, 3, 4, 5))),
        (SYLLABLE_SEGMENTS, ItemRef(SYLLABLE, 0), "right", 2, ((0,), (1, 2, 3, 4, 5))),
        (SYLLABLE_SEGMENTS, ItemRef(SYLLABLE, 1), "left", 2, ((0, 1, 2, 3, 4), (5,))),
    ],
)
def test_phrase_breaks_and_resyllabification_shift_both_directions(
    relation: QualifiedName,
    container: ItemRef,
    direction: str,
    count: int,
    expected: tuple[tuple[int, ...], tuple[int, ...]],
) -> None:
    """Edge runs of one or several children cross either sister boundary."""
    source = hierarchy()
    result = source.shift(container, count, direction, relation)
    left = ItemRef(container.tier, 0)
    right = ItemRef(container.tier, 1)
    assert tuple(item.index for item in targets(result, relation, left)) == expected[0]
    assert tuple(item.index for item in targets(result, relation, right)) == expected[1]
    assert result.tiers == source.tiers


def test_shift_is_one_identified_journal_hole_with_exact_undo() -> None:
    """The held children retain identity and one opposite shift undoes the record."""
    source = hierarchy()
    journal = Journal()
    editor = source.edit(journal=journal)
    editor.shift(ItemRef(PHRASE, 0), 2, "right", PHRASE_WORDS)

    assert len(journal.records) == 1
    assert journal.records[0].operation == "shift"
    correspondence = journal.records[0].report.correspondence
    assert correspondence is not None
    assert correspondence.identity_correspondence == correspondence.items
    editor.undo()
    assert editor.freeze() == source
    editor.redo()
    assert len(journal.records) == 1

    left_journal = Journal()
    left = source.edit(journal=left_journal)
    left.shift(ItemRef(PHRASE, 1), 1, "left", PHRASE_WORDS)
    left.undo()
    assert left.freeze() == source


def test_cut_insert_move_and_run_swaps_preserve_references_and_round_trip() -> None:
    """Cut/insert is a move, swaps admit unequal and empty sides, and undo is exact."""
    source = hierarchy()
    editor = source.edit()
    held = editor.cut(ItemRun(WORD, 1, 2))
    editor.insert_held(held, BoundaryRef(WORD, 3))
    assert editor.freeze() == source.move_run(ItemRun(WORD, 1, 2), 3)

    deleted = source.edit()
    deleted.cut(ItemRun(SEGMENT, 8, 1))
    assert deleted.freeze() == source.remove_items(SEGMENT, 8, 1)

    adjacent = source.swap_runs(ItemRun(WORD, 0, 2), ItemRun(WORD, 2, 3))
    assert tuple(item.durable_id for item in adjacent.tiers[2].items) == (
        "w2",
        "w3",
        "w4",
        "w0",
        "w1",
    )
    separated = source.swap_runs(ItemRun(SEGMENT, 0, 2), ItemRun(SEGMENT, 5, 3))
    assert tuple(item.durable_id for item in separated.tiers[4].items) == (
        "s5",
        "s6",
        "s7",
        "s2",
        "s3",
        "s4",
        "s0",
        "s1",
        "s8",
    )
    boundary_swap = source.swap_runs(ItemRun(WORD, 0, 2), ItemRun(WORD, 4, 0))
    assert tuple(item.durable_id for item in boundary_swap.tiers[2].items) == (
        "w2",
        "w3",
        "w0",
        "w1",
        "w4",
    )
    left_boundary_swap = source.swap_runs(ItemRun(WORD, 0, 0), ItemRun(WORD, 2, 2))
    assert tuple(item.durable_id for item in left_boundary_swap.tiers[2].items) == (
        "w2",
        "w3",
        "w0",
        "w1",
        "w4",
    )
    same = ItemRun(WORD, 2, 0)
    assert source.swap_runs(same, same) == source
    assert source.swap_runs(
        ItemRun(SEGMENT, 5, 3), ItemRun(SEGMENT, 0, 2)
    ) == source.swap_runs(ItemRun(SEGMENT, 0, 2), ItemRun(SEGMENT, 5, 3))
    assert source.swap_items(ItemRef(WORD, 0), ItemRef(WORD, 3)) == source.swap_runs(
        ItemRun(WORD, 0, 1), ItemRun(WORD, 3, 1)
    )

    journal = Journal()
    recorded = source.edit(journal=journal)
    recorded.swap_runs(ItemRun(WORD, 0, 2), ItemRun(WORD, 2, 3))
    recorded.undo()
    assert recorded.freeze() == source

    moved_journal = Journal()
    moved = source.edit(journal=moved_journal)
    moved.move_run(ItemRun(WORD, 0, 2), BoundaryRef(WORD, 3))
    assert moved_journal.records[0].report.correspondence is not None
    moved_patch = patch_loads(patch_dumps(moved_journal.to_patch()))
    assert moved_patch.apply(source) == moved.freeze()
    move_price = price_patch(moved_patch, CostTable(), source)
    assert move_price == CostTable().operation("move_item", WORD)
    assert (
        _atom_multiset_lower_bound(
            source, moved.freeze(), CostTable(), EquivalenceView.EXACT
        )
        <= move_price
    )
    moved.undo()
    assert moved.freeze() == source

    reverse_journal = Journal()
    reversed_swap = source.edit(journal=reverse_journal)
    reversed_swap.swap_runs(ItemRun(WORD, 3, 2), ItemRun(WORD, 0, 2))
    reversed_swap.undo()
    assert reversed_swap.freeze() == source


@pytest.mark.parametrize(
    ("start", "count"),
    [(True, 1), (0, True), (-1, 1), (0, -1), ("0", 1)],
)
def test_item_run_rejects_non_integral_or_negative_coordinates(
    start: Any, count: Any
) -> None:
    """Runs admit only nonnegative integral boundaries and lengths."""
    with pytest.raises(GraphValidationError, match="nonnegative integer"):
        ItemRun(WORD, start, count)


def test_cut_move_and_swap_refusal_guards_are_atomic() -> None:
    """Malformed, foreign, overlapping, and out-of-range held operations refuse."""
    source = hierarchy()
    editor = source.edit()
    with pytest.raises(GraphValidationError, match="requires an ItemRun"):
        editor.cut(cast(Any, ItemRef(WORD, 0)))
    with pytest.raises(GraphValidationError, match="must not be empty"):
        editor.cut(ItemRun(WORD, 0, 0))
    with pytest.raises(GraphValidationError, match="outside tier"):
        editor.cut(ItemRun(WORD, 5, 1))

    held = editor.cut(ItemRun(WORD, 0, 1))
    with pytest.raises(GraphValidationError, match="only one cut"):
        editor.cut(ItemRun(WORD, 1, 1))
    with pytest.raises(GraphValidationError, match="different tier"):
        editor.insert_held(held, BoundaryRef(SEGMENT, 0))
    with pytest.raises(GraphValidationError, match="must be an integer"):
        editor.insert_held(held, cast(Any, "one"))
    with pytest.raises(GraphValidationError, match="does not belong"):
        source.edit().insert_held(held, 0)
    editor.insert_held(held, 0)

    with pytest.raises(GraphValidationError, match="insertion point"):
        editor.move_run(ItemRun(WORD, 0, 1), 5)
    editor.move_run(ItemRun(WORD, 0, 1), 0)
    with pytest.raises(GraphValidationError, match="different tiers"):
        editor.swap_runs(ItemRun(WORD, 0, 1), ItemRun(SEGMENT, 0, 1))
    with pytest.raises(GraphValidationError, match="overlap"):
        editor.swap_runs(ItemRun(WORD, 0, 2), ItemRun(WORD, 1, 2))


def test_unresolved_cut_refuses_intervening_structural_edits() -> None:
    """A pending cut never resolves against coordinates changed afterward."""
    editor = hierarchy().edit()
    held = editor.cut(ItemRun(WORD, 1, 2))
    editor.insert_item(WORD, 0, Item("new"))

    with pytest.raises(GraphValidationError, match="after another structural edit"):
        editor.insert_held(held, 0)
    with pytest.raises(GraphValidationError, match="after another structural edit"):
        editor.freeze()


def test_legacy_item_moves_keep_their_seal_refusal_subjects() -> None:
    """Length-one aliases retain their established public diagnostics."""
    sealed = hierarchy().seal(WORD, 2)
    with pytest.raises(GraphValidationError, match="^item move would move"):
        sealed.move_item(ItemRef(WORD, 0), 3)
    with pytest.raises(GraphValidationError, match="^item swap would move"):
        sealed.swap_items(ItemRef(WORD, 0), ItemRef(WORD, 3))


def test_journal_refusals_leave_no_partial_run_or_shift_record() -> None:
    """Journal planning delegates malformed generalized edits without recording."""
    source = hierarchy()
    invalid_calls: tuple[Callable[[Any], object], ...] = (
        lambda editor: editor.move_run(ItemRun(name("missing"), 0, 1), 0),
        lambda editor: editor.move_run(ItemRun(WORD, 0, 1), cast(Any, True)),
        lambda editor: editor.swap_runs(ItemRun(WORD, 0, 1), ItemRun(SEGMENT, 0, 1)),
        lambda editor: editor.swap_runs(ItemRun(WORD, 0, 2), ItemRun(WORD, 1, 2)),
        lambda editor: editor.shift(ItemRef(PHRASE, 0), 1, "around", PHRASE_WORDS),
        lambda editor: editor.shift(ItemRef(PHRASE, 0), True, "right", PHRASE_WORDS),
        lambda editor: editor.shift(ItemRef(PHRASE, 0), 1, "right", UTTERANCE_PHRASES),
    )
    for operation in invalid_calls:
        journal = Journal()
        editor = source.edit(journal=journal)
        with pytest.raises(GraphValidationError):
            operation(editor)
        assert journal.records == ()


def test_timed_run_moves_and_swaps_refuse_without_a_policy() -> None:
    """General item-tier movement never changes clock positions silently."""
    graph = clock_cases.fixture()
    profile = ClockProfile(
        graph,
        clock_cases.CLOCK,
        clock_cases.BINDING,
        clock_cases.RATE,
        clock_cases.UNIT,
    )
    with pytest.raises(GraphValidationError, match="rebinding policy"):
        profile.edit().move_run(ItemRun(clock_cases.SEGMENT, 0, 1), 1)
    with pytest.raises(GraphValidationError, match="rebinding policy"):
        profile.edit().swap_runs(
            ItemRun(clock_cases.SEGMENT, 0, 1),
            ItemRun(clock_cases.SEGMENT, 1, 1),
        )
    assert (
        profile.edit("keep-earlier")
        .move_run(ItemRun(clock_cases.SEGMENT, 0, 1), 1)
        .freeze()
    )

    with pytest.raises(GraphValidationError, match="must not be empty"):
        profile.edit("keep-earlier").move_run(ItemRun(clock_cases.SEGMENT, 0, 0), 0)
    with pytest.raises(GraphValidationError, match="different tiers"):
        profile.edit("keep-earlier").swap_runs(
            ItemRun(clock_cases.SEGMENT, 0, 1),
            ItemRun(clock_cases.CLOCK, 0, 1),
        )


def test_clock_run_routes_and_journals_cover_timed_and_untimed_tiers() -> None:
    """Clock-aware run aliases retain ordinary routing and one-record undo."""
    untimed = clock_cases.advanced_profile(clock_cases.reference_shape()).edit()
    untimed.insert_item(clock_cases.SYNTAX, 1, Item("syntax-1"))
    untimed.move_run(ItemRun(clock_cases.SYNTAX, 0, 1), 1)
    untimed.swap_runs(
        ItemRun(clock_cases.SYNTAX, 0, 1), ItemRun(clock_cases.SYNTAX, 1, 1)
    )
    assert untimed.reports == ()

    profile = ClockProfile(
        clock_cases.fixture(),
        clock_cases.CLOCK,
        clock_cases.BINDING,
        clock_cases.RATE,
        clock_cases.UNIT,
    )
    move_journal = Journal()
    moved = profile.edit("keep-earlier", journal=move_journal)
    moved.move_run(ItemRun(clock_cases.SEGMENT, 0, 1), 1)
    assert len(move_journal.records) == 1
    moved.undo()
    assert moved.freeze() == profile.graph

    invalid_journal = Journal()
    invalid = profile.edit("keep-earlier", journal=invalid_journal)
    with pytest.raises(GraphValidationError, match="must be an integer"):
        invalid.move_run(ItemRun(clock_cases.SEGMENT, 0, 1), cast(Any, True))
    assert invalid_journal.records == ()

    swap_journal = Journal()
    swapped = profile.edit("keep-earlier", journal=swap_journal)
    swapped.swap_runs(
        ItemRun(clock_cases.SEGMENT, 0, 1),
        ItemRun(clock_cases.SEGMENT, 1, 1),
    )
    assert len(swap_journal.records) == 1
    swapped.undo()
    assert swapped.freeze() == profile.graph


def test_stored_and_shared_boundaries_require_a_named_policy() -> None:
    """Boundary state is never silently reinterpreted or dragged with a shift."""
    source = hierarchy()
    value = AttributeValue(BOUNDARY_NOTE, XsdType.STRING, "break")
    stored = replace(
        source,
        attribute_declarations=(
            AttributeDeclaration(
                BOUNDARY_NOTE, AttributeDomain.BOUNDARY, XsdType.STRING
            ),
        ),
        boundary_values=(Boundary(BoundaryRef(PHRASE, 1), (value,)),),
    )
    with pytest.raises(GraphValidationError, match="stored container boundary"):
        stored.shift(ItemRef(PHRASE, 0), 1, "right", PHRASE_WORDS)
    assert (
        stored.shift(
            ItemRef(PHRASE, 0), 1, "right", PHRASE_WORDS, "keep-earlier"
        ).boundary_values
        == stored.boundary_values
    )
    assert not stored.shift(
        ItemRef(PHRASE, 0), 1, "right", PHRASE_WORDS, "drop-to-provisional"
    ).boundary_values

    journal = Journal()
    recorded = stored.edit(journal=journal)
    recorded.shift(
        ItemRef(PHRASE, 0),
        1,
        "right",
        PHRASE_WORDS,
        "drop-to-provisional",
    )
    dropped = recorded.freeze()
    assert dropped.boundary_values == ()
    recorded.undo()
    assert recorded.freeze() == stored
    recorded.redo()
    assert recorded.freeze() == dropped
    assert journal.to_patch().invert().apply(dropped) == stored

    dry_journal = Journal()
    dry = stored.edit(journal=dry_journal)
    reports = dry.dry_run(
        lambda editor: editor.shift(
            ItemRef(PHRASE, 0),
            1,
            "right",
            PHRASE_WORDS,
            "drop-to-provisional",
        )
    )
    assert len(reports) == 1
    assert dry.freeze() == stored
    assert dry_journal.records == ()

    boundary_relation = BipartiteRelationDeclaration(
        BOUND,
        name("phrase-type"),
        name("segment-type"),
        left_endpoint=RelationEndpointKind.BOUNDARY,
        right_endpoint=RelationEndpointKind.BOUNDARY,
    )
    shared = replace(
        source,
        relation_declarations=(*source.relation_declarations, boundary_relation),
        relations=(
            RelationInstance(
                BOUND,
                DurableBoundaryRef(DurableItemRef("p1"), BoundarySide.BEFORE),
                DurableBoundaryRef(SEGMENT, BoundarySide.BEFORE),
            ),
        ),
    )
    with pytest.raises(GraphValidationError, match="shared with another tier"):
        shared.shift(ItemRef(PHRASE, 0), 1, "right", PHRASE_WORDS)
    shifted = shared.shift(ItemRef(PHRASE, 0), 1, "right", PHRASE_WORDS, "keep-earlier")
    assert shifted.relations == shared.relations


def test_clock_bound_container_shift_needs_no_rebinding_policy() -> None:
    """A bound sister boundary moves without restructuring either timed tier."""
    source = hierarchy()
    untimed = AttributeValue(UNTIMED, XsdType.BOOLEAN, "true")
    clock = Tier(
        TierDeclaration(CLOCK, "Clock"),
        tuple(Item(f"c{index}") for index in range(3)),
    )
    tiers = tuple(
        replace(tier, attributes=(untimed,))
        if tier.declaration.name != PHRASE
        else tier
        for tier in source.tiers
    )
    bare = replace(
        source,
        tiers=(*tiers, clock),
        relation_declarations=(
            *source.relation_declarations,
            SimpleRelationDeclaration(CLOCK_MEMBERS, CLOCK, CLOCK_TYPE),
            BipartiteRelationDeclaration(
                CLOCK_BINDING,
                name("phrase-type"),
                CLOCK_TYPE,
                RelationEndpointKind.BOUNDARY,
                RelationEndpointKind.BOUNDARY,
            ),
        ),
        attribute_declarations=(
            AttributeDeclaration(UNIT, AttributeDomain.DOCUMENT, XsdType.STRING),
            AttributeDeclaration(UNTIMED, AttributeDomain.TIER, XsdType.BOOLEAN),
        ),
        attributes=(AttributeValue(UNIT, XsdType.STRING, "s"),),
    )
    bindings = tuple(
        RelationInstance(
            CLOCK_BINDING,
            anchored_boundary(bare, BoundaryRef(PHRASE, index)),
            anchored_boundary(bare, BoundaryRef(CLOCK, index)),
        )
        for index in range(3)
    )
    bound = replace(bare, relations=bindings)
    profile = ClockProfile(
        bound,
        CLOCK,
        CLOCK_BINDING,
        None,
        UNIT,
        untimed_attribute=UNTIMED,
    )

    editor = profile.edit()
    editor.shift(ItemRef(PHRASE, 0), 1, "right", PHRASE_WORDS)
    result = editor.freeze()
    assert result.relations == bound.relations
    assert result.tiers == bound.tiers

    journal = Journal()
    recorded = profile.edit(journal=journal)
    recorded.shift(ItemRef(PHRASE, 0), 1, "right", PHRASE_WORDS)
    assert len(journal.records) == 1
    assert journal.records[0].report.correspondence is not None
    recorded.undo()
    assert recorded.freeze() == bound

    left_clock_journal = Journal()
    left_clock = profile.edit(journal=left_clock_journal)
    left_clock.shift(
        ItemRef(PHRASE, 1),
        1,
        "left",
        PHRASE_WORDS,
        "keep-earlier",
    )
    left_clock.undo()
    assert left_clock.freeze() == bound

    boundary_value = AttributeValue(BOUNDARY_NOTE, XsdType.STRING, "break")
    stored_bound = replace(
        bound,
        attribute_declarations=(
            *bound.attribute_declarations,
            AttributeDeclaration(
                BOUNDARY_NOTE, AttributeDomain.BOUNDARY, XsdType.STRING
            ),
        ),
        boundary_values=(Boundary(BoundaryRef(PHRASE, 1), (boundary_value,)),),
    )
    stored_profile = ClockProfile(
        stored_bound,
        CLOCK,
        CLOCK_BINDING,
        None,
        UNIT,
        untimed_attribute=UNTIMED,
    )
    with pytest.raises(GraphValidationError, match="stored container boundary"):
        stored_profile.edit().shift(ItemRef(PHRASE, 0), 1, "right", PHRASE_WORDS)

    for clock_editor in (profile.edit(), profile.edit(journal=Journal())):
        with pytest.raises(GraphValidationError, match="shift direction"):
            clock_editor.shift(ItemRef(PHRASE, 0), 1, "around", PHRASE_WORDS)
        with pytest.raises(GraphValidationError, match="has no.*membership"):
            clock_editor.shift(ItemRef(PHRASE, 0), 1, "right", UTTERANCE_PHRASES)
        with pytest.raises(GraphValidationError, match="clock tier"):
            clock_editor.shift(ItemRef(CLOCK, 0), 1, "right", PHRASE_WORDS)


@pytest.mark.parametrize(
    ("container", "count", "direction", "relation", "message"),
    [
        (ItemRef(PHRASE, 0), 0, "right", PHRASE_WORDS, "at least 1"),
        (ItemRef(PHRASE, 0), -1, "right", PHRASE_WORDS, "at least 1"),
        (ItemRef(PHRASE, 0), 4, "right", PHRASE_WORDS, "exceeds"),
        (ItemRef(PHRASE, 0), 3, "right", PHRASE_WORDS, "empty"),
        (ItemRef(PHRASE, 0), 1, "left", PHRASE_WORDS, "no left sister"),
        (ItemRef(PHRASE, 1), 1, "right", PHRASE_WORDS, "no right sister"),
        (ItemRef(PHRASE, 0), 1, "around", PHRASE_WORDS, "direction"),
        (ItemRef(PHRASE, 0), 1, "right", name("absent"), "not polyadic"),
    ],
)
def test_shift_refusals_are_local_and_leave_a_seam(
    container: ItemRef,
    count: int,
    direction: str,
    relation: QualifiedName,
    message: str,
) -> None:
    """Invalid runs, absent sisters, wrap requests, and wrong relations refuse."""
    with pytest.raises(GraphValidationError, match=message):
        hierarchy().shift(container, count, direction, relation)


def test_shift_refuses_bad_policy_membership_and_containment_shape() -> None:
    """Policy spelling, membership incidence, and containment form stay explicit."""
    source = hierarchy()
    with pytest.raises(GraphValidationError, match="shift policy"):
        source.shift(ItemRef(PHRASE, 0), 1, "right", PHRASE_WORDS, "invent-boundary")
    with pytest.raises(GraphValidationError, match="has no.*membership"):
        source.shift(ItemRef(PHRASE, 0), 1, "right", UTTERANCE_PHRASES)

    missing_sister = replace(
        source,
        polyadic_relations=tuple(
            instance
            for index, instance in enumerate(source.polyadic_relations)
            if index != 2
        ),
    )
    with pytest.raises(GraphValidationError, match="adjacent sister.*membership"):
        missing_sister.shift(ItemRef(PHRASE, 0), 1, "right", PHRASE_WORDS)

    declarations = list(source.relation_declarations)
    declaration_index = next(
        index
        for index, declaration in enumerate(declarations)
        if declaration.name == PHRASE_WORDS
    )
    original = declarations[declaration_index]
    assert isinstance(original, PolyadicRelationDeclaration)
    declarations[declaration_index] = replace(original, acyclic=False)
    malformed = replace(source, relation_declarations=tuple(declarations))
    with pytest.raises(GraphValidationError, match="not ordered containment"):
        malformed.shift(ItemRef(PHRASE, 0), 1, "right", PHRASE_WORDS)

    editor = source.edit()
    editor.add_relation(
        PolyadicRelationInstance(
            PHRASE_WORDS,
            (ItemRef(PHRASE, 0), ItemRef(PHRASE, 1)),
            (ItemRef(WORD, 0),),
        )
    )
    with pytest.raises(GraphValidationError, match="does not have one source"):
        editor.shift(ItemRef(PHRASE, 0), 1, "right", PHRASE_WORDS)


def test_diff_emits_one_shift_and_cost_uses_the_existing_swap_unit() -> None:
    """Identity-preserving segmentation change is recognized and cheaply priced."""
    source = hierarchy()
    target = source.shift(ItemRef(PHRASE, 0), 1, "right", PHRASE_WORDS)
    patch = diff(source, target, EquivalenceView.EXACT)
    assert len(patch.operations) == 1
    opcode = patch.operations[0].opcode
    assert isinstance(opcode, DeltaOpcode)
    assert opcode.operation == "shift"
    assert patch.apply(source) == target
    decoded = patch_loads(patch_dumps(patch))
    assert decoded.apply(source) == target
    assert decoded.invert().apply(target) == source
    shift_price = price_patch(patch, CostTable(), source)
    assert shift_price == CostTable().operation("swap_items", PHRASE)
    assert (
        _atom_multiset_lower_bound(source, target, CostTable(), EquivalenceView.EXACT)
        <= shift_price
    )

    alternative = replace(
        target,
        tiers=(
            target.tiers[0],
            replace(target.tiers[1], items=(Item(), Item())),
            *target.tiers[2:],
        ),
    )
    assert equivalent(target, alternative, EquivalenceView.FUNCTIONAL)
    assert not equivalent(target, alternative, EquivalenceView.IDENTIFIED)
    expensive = CostTable(
        {**CostTable().operations, "insert_item": 2, "remove_item": 2}
    )
    assert expensive.operation("swap_items") < (
        expensive.operation("insert_item") + expensive.operation("remove_item")
    )

    swap_journal = Journal()
    source.edit(journal=swap_journal).swap_runs(
        ItemRun(WORD, 0, 2), ItemRun(WORD, 3, 2)
    )
    swap_patch = patch_loads(patch_dumps(swap_journal.to_patch()))
    swap_price = price_patch(swap_patch, CostTable(), source)
    assert swap_price == CostTable().operation("swap_items", WORD)
    assert (
        _atom_multiset_lower_bound(
            source, swap_patch.apply(source), CostTable(), EquivalenceView.EXACT
        )
        <= swap_price
    )


def test_diff_shift_recognizer_ignores_other_polyadic_shapes_and_reorders() -> None:
    """Only executable ordered-containment boundary changes become shifts."""
    source = hierarchy()
    assert (
        _shift_patch(
            source,
            replace(source, polyadic_relations=source.polyadic_relations[:-1]),
        )
        is None
    )
    assert (
        _shift_patch(
            source,
            source.shift(ItemRef(PHRASE, 1), 1, "left", PHRASE_WORDS),
        )
        is not None
    )

    relations = list(source.polyadic_relations)
    relations[1] = replace(relations[1], targets=tuple(reversed(relations[1].targets)))
    assert (
        _shift_patch(source, replace(source, polyadic_relations=tuple(relations)))
        is None
    )
    relations[2] = replace(relations[2], targets=tuple(reversed(relations[2].targets)))
    assert (
        _shift_patch(source, replace(source, polyadic_relations=tuple(relations)))
        is None
    )

    nonadjacent = list(source.polyadic_relations)
    moved = nonadjacent[8].targets[-1:]
    nonadjacent[8] = replace(nonadjacent[8], targets=nonadjacent[8].targets[:-1])
    nonadjacent[10] = replace(
        nonadjacent[10], targets=(*moved, *nonadjacent[10].targets)
    )
    assert (
        _shift_patch(source, replace(source, polyadic_relations=tuple(nonadjacent)))
        is None
    )

    many_source = name("many-source")
    boundary_source = name("boundary-source")
    declarations = (
        *source.relation_declarations,
        PolyadicRelationDeclaration(
            many_source,
            RelationSideDeclaration((RelationEndpointKind.ITEM,), tiers=(PHRASE,)),
            side(WORD),
        ),
        PolyadicRelationDeclaration(
            boundary_source,
            RelationSideDeclaration((RelationEndpointKind.BOUNDARY,), tiers=(PHRASE,)),
            side(WORD),
        ),
    )
    shapes = replace(
        source,
        relation_declarations=declarations,
        polyadic_relations=(
            *source.polyadic_relations,
            PolyadicRelationInstance(
                many_source,
                (ItemRef(PHRASE, 0), ItemRef(PHRASE, 1)),
                (ItemRef(WORD, 0),),
            ),
            PolyadicRelationInstance(
                boundary_source,
                (DurableBoundaryRef(DurableItemRef("p0"), BoundarySide.BEFORE),),
                (ItemRef(WORD, 1), ItemRef(WORD, 3)),
            ),
        ),
    )
    assert _shift_patch(shapes, shapes) is None

    boundary_shapes = replace(
        shapes,
        polyadic_relations=(
            *shapes.polyadic_relations,
            PolyadicRelationInstance(
                boundary_source,
                (DurableBoundaryRef(DurableItemRef("p1"), BoundarySide.BEFORE),),
                (ItemRef(WORD, 2),),
            ),
        ),
    )
    boundary_target = list(boundary_shapes.polyadic_relations)
    moved = boundary_target[-2].targets[-1:]
    boundary_target[-2] = replace(
        boundary_target[-2], targets=boundary_target[-2].targets[:-1]
    )
    boundary_target[-1] = replace(
        boundary_target[-1], targets=(*moved, *boundary_target[-1].targets)
    )
    assert (
        _shift_patch(
            boundary_shapes,
            replace(boundary_shapes, polyadic_relations=tuple(boundary_target)),
        )
        is None
    )


def test_rewrite_and_topological_views_agree_including_epsilon_sides() -> None:
    """The stated synchronous rewrite has the same graph as the boundary swap."""
    source = hierarchy()
    shifted = source.shift(ItemRef(PHRASE, 0), 1, "right", PHRASE_WORDS)
    relations = list(source.polyadic_relations)
    left, right = relations[1], relations[2]
    relations[1] = replace(left, targets=left.targets[:-1])
    relations[2] = replace(right, targets=(*left.targets[-1:], *right.targets))
    assert shifted == replace(source, polyadic_relations=tuple(relations))

    epsilon = ItemRun(SEGMENT, 8, 0)
    assert epsilon.to_data()["sequence"] == ""
    inserted = source.insert_item(SEGMENT, 8, Item("inserted"))
    assert inserted.remove_item(ItemRef(SEGMENT, 8)) == source
    deleted = source.remove_item(ItemRef(SEGMENT, 8))
    assert deleted.insert_item(SEGMENT, 8, source.tiers[4].items[8]) == source


def test_cli_shift_uses_the_public_containment_operation(tmp_path: Path) -> None:
    """The CLI names the container, direction, count, and containment relation."""
    source = hierarchy()
    graph_path = tmp_path / "source.json"
    output_path = tmp_path / "shifted.json"
    graph_path.write_bytes(dump_bytes(source))

    assert (
        cli.main(
            [
                "edit",
                str(graph_path),
                "shift",
                "/items/durable/p0",
                "--count",
                "1",
                "--direction",
                "right",
                "--containment",
                NS,
                "phrase-words",
                "-o",
                str(output_path),
            ]
        )
        == 0
    )
    expected = source.shift(ItemRef(PHRASE, 0), 1, "right", PHRASE_WORDS)
    assert loads(output_path.read_bytes()) == expected
