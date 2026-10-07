"""Exercise the clock profile against timings drawn from a real domain."""

from __future__ import annotations

import decimal
from collections.abc import Callable, Iterable
from dataclasses import replace
from decimal import ROUND_DOWN, Decimal, Inexact, localcontext
from typing import cast

import pytest

from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    Boundary,
    BoundaryRef,
    BoundarySide,
    ClockBindingChange,
    ClockCoordinate,
    ClockEditOperation,
    ClockEditor,
    ClockProfile,
    ClockRebindingPolicy,
    DurableBoundaryRef,
    DurableItemRef,
    EquivalenceView,
    Graph,
    GraphValidationError,
    Item,
    ItemRef,
    NamespaceDeclaration,
    PhysicalTiming,
    PolyadicInstanceRef,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    QualifiedName,
    RefusalStage,
    RelationEndpointKind,
    RelationInstance,
    RelationInstanceRef,
    RelationSideDeclaration,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    TierRef,
    XsdType,
    anchored_boundary,
    equivalent,
)

# A Decimal context is more than its precision. The sweeps below name the three
# settings that can change the outcome of an arithmetic decision: how many
# digits are kept, which way a dropped digit moves the ones that are kept, and
# whether dropping a digit at all raises instead of returning. Precision alone
# would have left every claim about "the Decimal context" in this file resting
# on a single rounding mode and a single trap setting, which is the much weaker
# claim that these decisions survive rounding away from zero with Inexact
# trapped. The rounding modes are read out of the module rather than
# transcribed, so a mode added to a future Python is swept without this file
# being edited.
ROUNDING_MODES = tuple(
    sorted(member for member in dir(decimal) if member.startswith("ROUND_"))
)
# Precision 1 is included deliberately. It is the harshest setting the decimal
# module accepts and it is narrower than every lexeme these tests store, so a
# decision that had quietly run through the ambient context would diverge here
# before it diverged anywhere else.
PRECISIONS = (1, 2, 3, 12, 28, 40)


def test_clock_coordinate_to_data() -> None:
    assert ClockCoordinate(2, 3).to_data() == {"tick": 2, "gap": 3}


def test_physical_timing_to_data_uses_canonical_decimal_lexemes() -> None:
    assert PhysicalTiming(Decimal("0.100"), Decimal("1E-7"), "s").to_data() == {
        "start": "0.1",
        "duration": "0.0000001",
        "unit": "s",
    }


NS = "urn:tiergraph:profile:clock:test"
CLOCK = QualifiedName(NS, "clock")
SEGMENT = QualifiedName(NS, "segment")
CLOCK_TYPE = QualifiedName(NS, "tick")
SEGMENT_TYPE = QualifiedName(NS, "phone")
TICKS = QualifiedName(NS, "ticks")
SEGMENTS = QualifiedName(NS, "segments")
BINDING = QualifiedName(NS, "at-clock-position")
RATE = QualifiedName(NS, "ticks-per-second")
UNIT = QualifiedName(NS, "timing-unit")
OTHER_BINDING = QualifiedName(NS, "other-clock-binding")
TICK = QualifiedName(NS, "coarse-tick")
GAP = QualifiedName(NS, "gap-in-tick")
UNTIMED = QualifiedName(NS, "untimed")
START = QualifiedName(NS, "physical-start")
DURATION = QualifiedName(NS, "physical-duration")
SYNTAX = QualifiedName(NS, "syntax")
ALTERNATE = QualifiedName(NS, "alternate")
PARENT = QualifiedName(NS, "parent")
BOUNDARY_PARENT = QualifiedName(NS, "boundary-parent")
POLYADIC_PARENT = QualifiedName(NS, "polyadic-parent")


def fixture(rate: str = "10") -> Graph:
    """Encode a 0.1-second unit timing on a tier with a partial extent."""
    clock = Tier(
        TierDeclaration(CLOCK, "Clock ticks"),
        tuple(Item(f"clock-{index}") for index in range(4)),
    )
    segments = Tier(
        TierDeclaration(SEGMENT, "Segments"),
        (Item("segment-0"), Item("segment-1")),
    )
    bare = Graph(
        (NamespaceDeclaration("clock", NS),),
        (clock, segments),
        (
            SimpleRelationDeclaration(TICKS, CLOCK, CLOCK_TYPE),
            SimpleRelationDeclaration(SEGMENTS, SEGMENT, SEGMENT_TYPE),
            BipartiteRelationDeclaration(
                BINDING,
                SEGMENT_TYPE,
                CLOCK_TYPE,
                RelationEndpointKind.BOUNDARY,
                RelationEndpointKind.BOUNDARY,
            ),
        ),
        attribute_declarations=(
            AttributeDeclaration(RATE, AttributeDomain.DOCUMENT, XsdType.DECIMAL),
            AttributeDeclaration(UNIT, AttributeDomain.DOCUMENT, XsdType.STRING),
        ),
        attributes=(
            AttributeValue(RATE, XsdType.DECIMAL, rate),
            AttributeValue(UNIT, XsdType.STRING, "s"),
        ),
    )
    relations = tuple(
        RelationInstance(
            BINDING,
            anchored_boundary(bare, BoundaryRef(SEGMENT, source)),
            anchored_boundary(bare, BoundaryRef(CLOCK, target)),
        )
        for source, target in ((0, 1), (1, 2), (2, 3))
    )
    return Graph(
        bare.namespaces,
        bare.tiers,
        bare.relation_declarations,
        relations,
        bare.attribute_declarations,
        attributes=bare.attributes,
    )


def fixture_with_parent() -> Graph:
    """Add one structural relation whose endpoints can be reparented."""
    graph = fixture()
    declaration = BipartiteRelationDeclaration(
        PARENT,
        SEGMENT_TYPE,
        SEGMENT_TYPE,
        RelationEndpointKind.ITEM,
        RelationEndpointKind.ITEM,
    )
    return replace(
        graph,
        relation_declarations=(*graph.relation_declarations, declaration),
        relations=(
            *graph.relations,
            RelationInstance(
                PARENT,
                DurableItemRef("segment-0"),
                DurableItemRef("segment-1"),
            ),
        ),
    )


def fixture_with_unpromoted_item() -> Graph:
    """Bind an interior boundary through the preceding item's after side."""
    editor = fixture().edit()
    editor.insert_item(SEGMENT, 1, Item())
    graph = editor.freeze()
    binding = RelationInstance(
        BINDING,
        anchored_boundary(graph, BoundaryRef(SEGMENT, 1)),
        anchored_boundary(graph, BoundaryRef(CLOCK, 2)),
    )
    return replace(graph, relations=(*graph.relations, binding))


def fixture_with_boundary_parent() -> Graph:
    """Add a non-clock relation between boundaries on the timed tier."""
    graph = fixture()
    declaration = BipartiteRelationDeclaration(
        BOUNDARY_PARENT,
        SEGMENT_TYPE,
        SEGMENT_TYPE,
        RelationEndpointKind.BOUNDARY,
        RelationEndpointKind.BOUNDARY,
    )
    return replace(
        graph,
        relation_declarations=(*graph.relation_declarations, declaration),
        relations=(
            *graph.relations,
            RelationInstance(
                BOUNDARY_PARENT,
                anchored_boundary(graph, BoundaryRef(SEGMENT, 0)),
                anchored_boundary(graph, BoundaryRef(SEGMENT, 1)),
            ),
        ),
    )


def fixture_with_polyadic_parent() -> Graph:
    """Add an ordered structural parent relation on timed-tier items."""
    graph = fixture()
    side = RelationSideDeclaration(
        (RelationEndpointKind.ITEM,),
        (SEGMENT,),
    )
    declaration = PolyadicRelationDeclaration(POLYADIC_PARENT, side, side)
    return replace(
        graph,
        relation_declarations=(*graph.relation_declarations, declaration),
        polyadic_relations=(
            PolyadicRelationInstance(
                POLYADIC_PARENT,
                (DurableItemRef("segment-0"),),
                (DurableItemRef("segment-1"),),
            ),
        ),
    )


def test_real_reference_rate_derives_timing_on_a_partial_document_tier() -> None:
    """The source fixture's 0.1-second units occupy clock coordinates 1 through 3."""
    profile = ClockProfile(fixture(), CLOCK, BINDING, RATE, UNIT)
    assert profile.rate == Decimal("10.0")
    assert profile.extent(SEGMENT) == (ClockCoordinate(1), ClockCoordinate(3))
    assert profile.clock_index(BoundaryRef(SEGMENT, 1)) == 2
    assert profile.duration(SEGMENT, 0) == (1, Decimal("10.0"))
    assert profile.duration(SEGMENT, 1) == (1, Decimal("10.0"))


@pytest.mark.parametrize(
    "edit",
    (
        lambda editor: editor.move_item(ItemRef(SEGMENT, 0), 1),
        lambda editor: editor.swap_items(ItemRef(SEGMENT, 0), ItemRef(SEGMENT, 1)),
        lambda editor: editor.insert_item(SEGMENT, 1, Item("inserted")),
        lambda editor: editor.remove_item(ItemRef(SEGMENT, 0)),
    ),
    ids=("move", "swap", "insert", "remove"),
)
def test_bound_tier_structural_edits_require_a_policy_and_change_nothing(
    edit: Callable[[ClockEditor], object],
) -> None:
    """Every covered timed-tier restructure refuses atomically without policy."""
    graph = fixture()
    editor = ClockProfile(graph, CLOCK, BINDING, RATE, UNIT).edit()
    with pytest.raises(GraphValidationError, match="requires a named rebinding policy"):
        edit(editor)
    assert editor.freeze() is graph
    assert editor.reports == ()


def test_bound_tier_reparent_requires_a_policy_and_changes_nothing() -> None:
    """Reparenting endpoints on a timed tier has the same policy gate."""
    graph = fixture_with_parent()
    editor = ClockProfile(graph, CLOCK, BINDING, RATE, UNIT).edit()
    with pytest.raises(GraphValidationError, match="requires a named rebinding policy"):
        editor.reparent(
            RelationInstanceRef(3),
            DurableItemRef("segment-1"),
            DurableItemRef("segment-0"),
        )
    assert editor.freeze() is graph
    assert editor.reports == ()


def test_keep_earlier_moves_items_across_fixed_times_and_reports_rebinding() -> None:
    """Keep-earlier preserves ordered times while boundary anchors follow items."""
    graph = fixture()
    editor = ClockProfile(graph, CLOCK, BINDING, RATE, UNIT).edit(
        ClockRebindingPolicy.KEEP_EARLIER
    )
    editor.move_item(ItemRef(SEGMENT, 0), 1)
    changed = editor.freeze()
    profile = ClockProfile(changed, CLOCK, BINDING, RATE, UNIT)
    assert [item.durable_id for item in changed.tiers[1].items] == [
        "segment-1",
        "segment-0",
    ]
    assert [profile.clock_index(BoundaryRef(SEGMENT, index)) for index in range(3)] == [
        1,
        2,
        3,
    ]
    assert editor.reports == (editor.reports[0],)
    report = editor.reports[0]
    assert report.operation is ClockEditOperation.ITEM_MOVE
    assert report.policy is ClockRebindingPolicy.KEEP_EARLIER
    assert report.tier == SEGMENT
    assert report.needs_realignment is False
    assert report.changes
    assert all(isinstance(change, ClockBindingChange) for change in report.changes)
    assert all(change.provisional is False for change in report.changes)


def test_drop_to_provisional_collapses_times_and_records_realigning_fact() -> None:
    """The provisional policy makes its timing loss visible in report and graph."""
    graph = fixture()
    editor = ClockProfile(graph, CLOCK, BINDING, RATE, UNIT).edit("drop-to-provisional")
    editor.swap_items(ItemRef(SEGMENT, 0), ItemRef(SEGMENT, 1))
    changed = editor.freeze()
    profile = ClockProfile(changed, CLOCK, BINDING, RATE, UNIT)
    assert [profile.clock_index(BoundaryRef(SEGMENT, index)) for index in range(3)] == [
        1,
        1,
        1,
    ]
    report = editor.reports[0]
    assert report.policy is ClockRebindingPolicy.DROP_TO_PROVISIONAL
    assert report.needs_realignment is True
    assert report.changes
    fact = next(
        fact
        for layer in changed.layers
        for fact in layer.facts
        if fact.value.name.local_name == "needs-realignment"
    )
    assert fact.subject == TierRef(SEGMENT)
    assert cast(AttributeValue, fact.value).lexical == "true"


def test_insert_and_bound_remove_keep_clock_profile_valid_and_reported() -> None:
    """Insertion binds its new boundary and removal keeps a surviving binding."""
    profile = ClockProfile(fixture(), CLOCK, BINDING, RATE, UNIT)
    inserted_editor = profile.edit("keep-earlier")
    inserted_editor.insert_item(SEGMENT, 1, Item("inserted"))
    inserted = inserted_editor.freeze()
    inserted_profile = ClockProfile(inserted, CLOCK, BINDING, RATE, UNIT)
    assert [
        inserted_profile.clock_index(BoundaryRef(SEGMENT, index)) for index in range(4)
    ] == [1, 2, 2, 3]
    insertion_report = inserted_editor.reports[0]
    assert insertion_report.needs_realignment is True
    assert any(change.provisional for change in insertion_report.changes)
    assert any(
        fact.subject == TierRef(SEGMENT)
        and fact.value.name.local_name == "needs-realignment"
        for layer in inserted.layers
        for fact in layer.facts
    )

    removed_editor = profile.edit("keep-earlier")
    removed_editor.remove_item(DurableItemRef("segment-0"))
    removed = removed_editor.freeze()
    removed_profile = ClockProfile(removed, CLOCK, BINDING, RATE, UNIT)
    assert [
        removed_profile.clock_index(BoundaryRef(SEGMENT, index)) for index in range(2)
    ] == [1, 3]
    assert [item.durable_id for item in removed.tiers[1].items] == ["segment-1"]
    assert removed_editor.reports[0].changes


def test_reparent_policies_keep_clock_profile_valid_and_report_outcomes() -> None:
    """Both named policies validate after changing a timed tier's parent link."""
    graph = fixture_with_parent()
    keep = ClockProfile(graph, CLOCK, BINDING, RATE, UNIT).edit("keep-earlier")
    keep.reparent(
        RelationInstanceRef(3),
        DurableItemRef("segment-1"),
        DurableItemRef("segment-0"),
    )
    kept = keep.freeze()
    kept_profile = ClockProfile(kept, CLOCK, BINDING, RATE, UNIT)
    assert kept_profile.extent(SEGMENT) == (
        ClockCoordinate(1),
        ClockCoordinate(3),
    )
    assert keep.reports[0].changes == ()

    provisional = ClockProfile(graph, CLOCK, BINDING, RATE, UNIT).edit(
        "drop-to-provisional"
    )
    provisional.reparent(
        RelationInstanceRef(3),
        DurableItemRef("segment-1"),
        DurableItemRef("segment-0"),
    )
    provisional_profile = ClockProfile(provisional.freeze(), CLOCK, BINDING, RATE, UNIT)
    assert [
        provisional_profile.clock_index(BoundaryRef(SEGMENT, index))
        for index in range(3)
    ] == [1, 1, 1]
    assert provisional.reports[0].needs_realignment is True


def test_keep_earlier_move_and_swap_inverses_restore_every_s1_view() -> None:
    """Move and swap inverses restore functional, identified, and exact views."""
    graph = fixture()
    moved = ClockProfile(graph, CLOCK, BINDING, RATE, UNIT).edit("keep-earlier")
    moved.move_item(ItemRef(SEGMENT, 0), 1)
    moved.move_item(ItemRef(SEGMENT, 1), 0)
    swapped = ClockProfile(graph, CLOCK, BINDING, RATE, UNIT).edit("keep-earlier")
    swapped.swap_items(ItemRef(SEGMENT, 0), ItemRef(SEGMENT, 1))
    swapped.swap_items(ItemRef(SEGMENT, 0), ItemRef(SEGMENT, 1))
    for view in EquivalenceView:
        assert equivalent(graph, moved.freeze(), view)
        assert equivalent(graph, swapped.freeze(), view)


def test_clock_editor_constructor_refusals_are_explicit() -> None:
    """Structural profiles and unknown policy names cannot start a session."""
    structural = ClockProfile.from_boundary_values(
        spine_fixture(((0, 0), (1, 0))),
        CLOCK,
        tick_attribute=SPINE_TICK,
        gap_attribute=SPINE_GAP,
    )
    with pytest.raises(ValueError, match="structural clock-spine profile"):
        structural.edit()
    with pytest.raises(ValueError, match="unknown clock rebinding policy"):
        ClockProfile(fixture(), CLOCK, BINDING, RATE, UNIT).edit("guess")


def test_timed_no_ops_require_and_report_the_named_policy() -> None:
    """Even a no-op is an explicit, reported clock-policy outcome."""
    profile = ClockProfile(fixture(), CLOCK, BINDING, RATE, UNIT)
    without_policy = profile.edit()
    with pytest.raises(GraphValidationError, match="requires a named rebinding policy"):
        without_policy.move_item(ItemRef(SEGMENT, 0), 0)

    editor = profile.edit("keep-earlier")
    editor.insert_items(SEGMENT, 1, ())
    editor.remove_items(SEGMENT, 1, 0)
    editor.move_item(ItemRef(SEGMENT, 0), 0)
    editor.swap_items(ItemRef(SEGMENT, 0), ItemRef(SEGMENT, 0))
    assert editor.freeze() is profile.graph
    assert tuple(report.operation for report in editor.reports) == (
        ClockEditOperation.ITEM_INSERTION,
        ClockEditOperation.ITEM_REMOVAL,
        ClockEditOperation.ITEM_MOVE,
        ClockEditOperation.ITEM_SWAP,
    )
    assert all(report.changes == () for report in editor.reports)


def test_clock_and_untimed_tiers_take_their_distinct_edit_paths() -> None:
    """Clock structure refuses while declared untimed structure edits normally."""
    profile = ClockProfile(fixture(), CLOCK, BINDING, RATE, UNIT)
    with pytest.raises(GraphValidationError, match="cannot restructure the clock tier"):
        profile.edit("keep-earlier").insert_item(CLOCK, 1, Item("new-clock"))
    with pytest.raises(GraphValidationError, match="cannot restructure the clock tier"):
        profile.edit("keep-earlier").insert_items(CLOCK, 1, ())
    with pytest.raises(GraphValidationError, match="cannot restructure the clock tier"):
        profile.edit("keep-earlier").remove_item(ItemRef(CLOCK, 0))
    with pytest.raises(GraphValidationError, match="cannot restructure the clock tier"):
        profile.edit("keep-earlier").remove_items(CLOCK, 0, 1)

    untimed = advanced_profile(reference_shape()).edit()
    untimed.insert_item(SYNTAX, 1, Item("syntax-1"))
    untimed.insert_items(SYNTAX, 2, ())
    untimed.move_item(ItemRef(SYNTAX, 0), 1)
    untimed.swap_items(ItemRef(SYNTAX, 0), ItemRef(SYNTAX, 1))
    assert [item.durable_id for item in untimed.freeze().tiers[3].items] == [
        "syntax-0",
        "syntax-1",
    ]
    assert untimed.reports == ()
    assert untimed.profile.is_timed(SYNTAX) is False

    removed_one = advanced_profile(reference_shape()).edit()
    removed_one.remove_item(ItemRef(SYNTAX, 0))
    assert removed_one.freeze().tiers[3].items == ()
    assert removed_one.reports == ()

    removed_run = advanced_profile(reference_shape()).edit()
    removed_run.remove_items(SYNTAX, 0, 1)
    assert removed_run.freeze().tiers[3].items == ()
    assert removed_run.reports == ()


def test_unpromoted_items_use_the_other_adjacent_anchor_during_rebinding() -> None:
    """Timed insertion and movement accept the previous item's after anchor."""
    graph = fixture_with_unpromoted_item()
    profile = ClockProfile(graph, CLOCK, BINDING, RATE, UNIT)

    inserted = profile.edit("keep-earlier")
    inserted.insert_item(SEGMENT, 1, Item("inserted"))
    assert [
        inserted.profile.clock_index(BoundaryRef(SEGMENT, index)) for index in range(5)
    ] == [1, 2, 2, 2, 3]

    moved = profile.edit("keep-earlier")
    moved.move_item(DurableItemRef("segment-1"), 0)
    assert [item.durable_id for item in moved.freeze().tiers[1].items] == [
        "segment-1",
        "segment-0",
        None,
    ]
    assert moved.profile.graph is moved.freeze()


def test_rebinding_without_either_adjacent_anchor_is_a_staged_refusal() -> None:
    """An unavoidable anchor failure is a documented atomic graph refusal."""
    graph = fixture_with_unpromoted_item()
    editor = ClockProfile(graph, CLOCK, BINDING, RATE, UNIT).edit("keep-earlier")
    with pytest.raises(
        GraphValidationError, match="no adjacent durable anchor"
    ) as caught:
        editor.insert_item(SEGMENT, 1, Item())
    assert caught.value.stage is RefusalStage.REFERENCE
    assert editor.freeze() is graph
    assert editor.reports == ()


@pytest.mark.parametrize("items", [{Item("new")}, {"new": Item("new")}])
def test_clock_insert_items_refuses_unordered_inputs(items: object) -> None:
    """The clock editor preserves the plain editor's ordered-input contract."""
    graph = fixture()
    editor = ClockProfile(graph, CLOCK, BINDING, RATE, UNIT).edit("keep-earlier")
    with pytest.raises(GraphValidationError, match="ordered iterable"):
        editor.insert_items(SEGMENT, 1, cast(Iterable[Item], items))
    assert editor.freeze() is graph
    assert editor.reports == ()


def test_clock_editor_validates_coordinates_before_any_edit() -> None:
    """Invalid tiers, ranges, and cross-tier swaps fail without state changes."""
    graph = fixture()
    profile = ClockProfile(graph, CLOCK, BINDING, RATE, UNIT)
    editor = profile.edit("keep-earlier")
    missing = QualifiedName(NS, "missing")
    failures: tuple[Callable[[], object], ...] = (
        lambda: editor.insert_items(missing, 0, ()),
        lambda: editor.insert_item(SEGMENT, 3, Item()),
        lambda: editor.remove_items(SEGMENT, 0, -1),
        lambda: editor.remove_items(SEGMENT, 2, 1),
        lambda: editor.move_item(ItemRef(SEGMENT, 0), 2),
        lambda: editor.swap_items(ItemRef(SEGMENT, 0), ItemRef(CLOCK, 0)),
    )
    for fail in failures:
        with pytest.raises(GraphValidationError):
            fail()
    assert editor.freeze() is graph
    assert editor.reports == ()


def test_removing_the_last_item_withdraws_its_departing_anchor() -> None:
    """A trailing removal retains the earlier binding at the merged boundary."""
    editor = ClockProfile(fixture(), CLOCK, BINDING, RATE, UNIT).edit("keep-earlier")
    editor.remove_item(ItemRef(SEGMENT, 1))
    profile = editor.profile
    assert [profile.clock_index(BoundaryRef(SEGMENT, index)) for index in range(2)] == [
        1,
        2,
    ]
    assert editor.reports[0].changes


def test_reparent_validates_relation_shape_and_supports_all_endpoint_forms() -> None:
    """Binding, arity, boundary, and polyadic reparent paths stay explicit."""
    binding_editor = ClockProfile(fixture(), CLOCK, BINDING, RATE, UNIT).edit(
        "keep-earlier"
    )
    with pytest.raises(GraphValidationError, match="clock binding relation"):
        binding_editor.reparent(
            RelationInstanceRef(0),
            fixture().relations[0].left,
            fixture().relations[0].right,
        )

    binary_graph = fixture_with_parent()
    binary_editor = ClockProfile(binary_graph, CLOCK, BINDING, RATE, UNIT).edit(
        "keep-earlier"
    )
    with pytest.raises(GraphValidationError, match="must each be one"):
        binary_editor.reparent(
            RelationInstanceRef(3),
            (DurableItemRef("segment-0"), DurableItemRef("segment-1")),
            (DurableItemRef("segment-0"),),
        )

    boundary_graph = fixture_with_boundary_parent()
    boundary_relation = boundary_graph.relations[3]
    boundary_editor = ClockProfile(boundary_graph, CLOCK, BINDING, RATE, UNIT).edit(
        "keep-earlier"
    )
    boundary_editor.reparent(
        RelationInstanceRef(3),
        (cast(DurableBoundaryRef, boundary_relation.right),),
        (cast(DurableBoundaryRef, boundary_relation.left),),
    )
    assert boundary_editor.profile.extent(SEGMENT) == (
        ClockCoordinate(1),
        ClockCoordinate(3),
    )

    polyadic_graph = fixture_with_polyadic_parent()
    polyadic_editor = ClockProfile(polyadic_graph, CLOCK, BINDING, RATE, UNIT).edit(
        "keep-earlier"
    )
    polyadic_editor.reparent(
        PolyadicInstanceRef(0),
        (DurableItemRef("segment-1"),),
        (DurableItemRef("segment-0"),),
    )
    changed = polyadic_editor.freeze().polyadic_relations[0]
    assert changed.sources == (DurableItemRef("segment-1"),)
    assert changed.targets == (DurableItemRef("segment-0"),)


def test_realigning_fact_reuses_declarations_and_avoids_prefix_collisions() -> None:
    """Repeated collapses reuse their vocabulary and preserve namespace prefixes."""
    graph = fixture()
    graph = replace(
        graph,
        namespaces=(
            *graph.namespaces,
            NamespaceDeclaration("clock-edit", "urn:example:occupied-prefix"),
        ),
    )
    editor = ClockProfile(graph, CLOCK, BINDING, RATE, UNIT).edit("drop-to-provisional")
    editor.swap_items(ItemRef(SEGMENT, 0), ItemRef(SEGMENT, 1))
    editor.swap_items(ItemRef(SEGMENT, 0), ItemRef(SEGMENT, 1))
    changed = editor.freeze()
    assert any(namespace.prefix == "clock-edit-2" for namespace in changed.namespaces)
    assert (
        len(
            [
                declaration
                for declaration in changed.attribute_declarations
                if declaration.name.local_name == "needs-realignment"
            ]
        )
        == 1
    )
    assert (
        len([layer for layer in changed.layers if layer.name.source == "rebinding"])
        == 1
    )


def test_rate_changes_the_derived_measure_without_moving_structure() -> None:
    """Continuous time has no stored copy that can disagree with the rate."""
    original = fixture("10")
    changed = fixture("20")
    before = ClockProfile(original, CLOCK, BINDING, RATE, UNIT)
    after = ClockProfile(changed, CLOCK, BINDING, RATE, UNIT)
    assert original.tiers == changed.tiers
    assert original.relations == changed.relations
    assert before.duration(SEGMENT, 0) == (1, Decimal("10"))
    assert after.duration(SEGMENT, 0) == (1, Decimal("20"))


@pytest.mark.parametrize("trap_inexact", (True, False), ids=("trapped", "untrapped"))
@pytest.mark.parametrize("rounding", ROUNDING_MODES)
@pytest.mark.parametrize("precision", PRECISIONS)
def test_nonterminating_duration_is_exact_and_ignores_decimal_context(
    precision: int, rounding: str, trap_inexact: bool
) -> None:
    """The profile returns a ratio without performing context-sensitive division."""
    profile = ClockProfile(fixture("3"), CLOCK, BINDING, RATE, UNIT)
    with localcontext() as context:
        context.prec = precision
        context.rounding = getattr(decimal, rounding)
        context.traps[Inexact] = trap_inexact
        duration = profile.duration(SEGMENT, 0)
    assert duration == (1, Decimal("3"))


def test_structural_positions_remain_integral() -> None:
    """A continuous profile does not relax the kernel's structural indices."""
    with pytest.raises(ValueError, match="non-integral index 1.5"):
        BoundaryRef(SEGMENT, 1.5)  # type: ignore[arg-type]


def test_zero_span_is_shared_and_cannot_carry_per_event_duration() -> None:
    """Equal adjacent bindings derive zero, with no event duration override."""
    graph = fixture()
    relations = list(graph.relations)
    relations[1] = replace(relations[1], right=relations[0].right)
    profile = ClockProfile(
        replace(graph, relations=tuple(relations)), CLOCK, BINDING, RATE, UNIT
    )
    assert profile.duration(SEGMENT, 0) == (0, Decimal("10.0"))


def test_unrelated_boundary_relations_do_not_enter_the_binding() -> None:
    """The profile filters by the declared relation name, not endpoint shape."""
    graph = fixture()
    binding = cast(
        BipartiteRelationDeclaration,
        next(
            declaration
            for declaration in graph.relation_declarations
            if declaration.name == BINDING
        ),
    )
    unrelated = replace(binding, name=OTHER_BINDING)
    graph = replace(
        graph,
        relation_declarations=(*graph.relation_declarations, unrelated),
        relations=(
            replace(graph.relations[0], declaration=OTHER_BINDING),
            *graph.relations,
        ),
    )
    assert ClockProfile(graph, CLOCK, BINDING, RATE, UNIT).extent(SEGMENT) == (
        ClockCoordinate(1),
        ClockCoordinate(3),
    )


def test_runtime_boundary_invariant_refuses_malformed_endpoints() -> None:
    """Boundary bindings refuse corruption even when assertions are disabled."""
    graph = fixture()
    invalid_left = replace(
        graph.relations[0],
        left=DurableItemRef("segment-0"),
    )
    object.__setattr__(graph, "relations", (invalid_left, *graph.relations[1:]))
    with pytest.raises(ValueError, match="left endpoint is not a boundary"):
        ClockProfile(graph, CLOCK, BINDING, RATE, UNIT)

    graph = fixture()
    invalid_right = replace(
        graph.relations[0],
        right=DurableItemRef("clock-0"),
    )
    object.__setattr__(graph, "relations", (invalid_right, *graph.relations[1:]))
    with pytest.raises(ValueError, match="right endpoint is not a boundary"):
        ClockProfile(graph, CLOCK, BINDING, RATE, UNIT)


def test_profile_refusals_name_incomplete_or_contradictory_bindings() -> None:
    """Missing, duplicate, backward, and non-clock coordinates cannot go silent."""
    graph = fixture()
    with pytest.raises(ValueError, match="has no clock binding"):
        ClockProfile(
            replace(graph, relations=graph.relations[:-1]), CLOCK, BINDING, RATE, UNIT
        )
    with pytest.raises(ValueError, match="has two bindings"):
        ClockProfile(
            replace(graph, relations=(*graph.relations, graph.relations[0])),
            CLOCK,
            BINDING,
            RATE,
            UNIT,
        )
    backward = list(graph.relations)
    backward[1] = replace(backward[1], right=graph.relations[0].right)
    backward[0] = replace(backward[0], right=graph.relations[1].right)
    with pytest.raises(ValueError, match="go backward"):
        ClockProfile(
            replace(graph, relations=tuple(backward)), CLOCK, BINDING, RATE, UNIT
        )
    shadow = QualifiedName(NS, "shadow-clock")
    shadow_members = QualifiedName(NS, "shadow-ticks")
    shadow_tier = Tier(TierDeclaration(shadow, "Shadow clock"), (Item(),))
    shadow_graph = Graph(
        graph.namespaces,
        (*graph.tiers, shadow_tier),
        (
            *graph.relation_declarations,
            SimpleRelationDeclaration(shadow_members, shadow, CLOCK_TYPE),
        ),
        graph.relations,
        graph.attribute_declarations,
        attributes=graph.attributes,
    )
    first_target = cast(DurableBoundaryRef, graph.relations[0].right)
    wrong_target = replace(
        graph.relations[0],
        right=DurableBoundaryRef(shadow, first_target.side),
    )
    with pytest.raises(ValueError, match="target is not on the clock"):
        ClockProfile(
            replace(
                shadow_graph, relations=(wrong_target, *shadow_graph.relations[1:])
            ),
            CLOCK,
            BINDING,
            RATE,
            UNIT,
        )
    self_binding = replace(
        cast(
            BipartiteRelationDeclaration,
            next(
                declaration
                for declaration in graph.relation_declarations
                if declaration.name == BINDING
            ),
        ),
        left_type=CLOCK_TYPE,
    )
    self_relation = RelationInstance(
        BINDING,
        anchored_boundary(graph, BoundaryRef(CLOCK, 0)),
        anchored_boundary(graph, BoundaryRef(CLOCK, 1)),
    )
    self_graph = replace(
        graph,
        relation_declarations=tuple(
            self_binding if declaration.name == BINDING else declaration
            for declaration in graph.relation_declarations
        ),
        relations=(self_relation,),
    )
    with pytest.raises(ValueError, match="do not bind to themselves"):
        ClockProfile(self_graph, CLOCK, BINDING, RATE, UNIT)


def test_profile_declaration_and_lookup_refusals_are_explicit() -> None:
    """Every profile role is declared with the required domain and endpoint kinds."""
    graph = fixture()
    missing = QualifiedName(NS, "missing")
    with pytest.raises(ValueError, match="clock tier.*not declared"):
        ClockProfile(graph, missing, BINDING, RATE, UNIT)
    with pytest.raises(ValueError, match="clock rate.*not declared"):
        ClockProfile(graph, CLOCK, BINDING, missing, UNIT)
    with pytest.raises(ValueError, match="clock binding.*not declared"):
        ClockProfile(graph, CLOCK, missing, RATE, UNIT)
    bad_rate_declaration = replace(
        graph.attribute_declarations[0], value_type=XsdType.DOUBLE
    )
    bad_rate_graph = replace(
        graph,
        attribute_declarations=(bad_rate_declaration, graph.attribute_declarations[1]),
        attributes=(
            AttributeValue(RATE, XsdType.DOUBLE, "10"),
            AttributeValue(UNIT, XsdType.STRING, "s"),
        ),
    )
    with pytest.raises(ValueError, match="document decimal"):
        ClockProfile(bad_rate_graph, CLOCK, BINDING, RATE, UNIT)
    with pytest.raises(ValueError, match="has no value"):
        ClockProfile(replace(graph, attributes=()), CLOCK, BINDING, RATE, UNIT)
    with pytest.raises(ValueError, match="must be positive"):
        ClockProfile(fixture("0"), CLOCK, BINDING, RATE, UNIT)
    item_binding = replace(
        cast(
            BipartiteRelationDeclaration,
            next(
                declaration
                for declaration in graph.relation_declarations
                if declaration.name == BINDING
            ),
        ),
        left_endpoint=RelationEndpointKind.ITEM,
    )
    with pytest.raises(ValueError, match="boundary to boundary"):
        ClockProfile(
            replace(
                graph,
                relation_declarations=tuple(
                    item_binding if declaration.name == BINDING else declaration
                    for declaration in graph.relation_declarations
                ),
                relations=(),
            ),
            CLOCK,
            BINDING,
            RATE,
            UNIT,
        )
    with pytest.raises(ValueError, match="tier.*not declared"):
        ClockProfile(graph, CLOCK, BINDING, RATE, UNIT).extent(missing)
    with pytest.raises(ValueError, match="has no clock binding"):
        ClockProfile(graph, CLOCK, BINDING, RATE, UNIT).clock_index(
            BoundaryRef(CLOCK, 0)
        )


def test_anchor_helper_uses_either_side_and_stages_unavoidable_refusals() -> None:
    """Stored bindings always use semantic boundary anchors, including interiors."""
    graph = fixture()
    missing = QualifiedName(NS, "missing")
    with pytest.raises(GraphValidationError, match="outside its tier") as missing_tier:
        anchored_boundary(graph, BoundaryRef(missing, 0))
    assert missing_tier.value.stage is RefusalStage.REFERENCE
    with pytest.raises(GraphValidationError, match="outside its tier") as position:
        anchored_boundary(graph, BoundaryRef(SEGMENT, 3))
    assert position.value.stage is RefusalStage.REFERENCE
    fallback = replace(
        graph,
        tiers=(
            graph.tiers[0],
            replace(graph.tiers[1], items=(Item("left"), Item())),
        ),
        relations=(),
    )
    assert anchored_boundary(fallback, BoundaryRef(SEGMENT, 1)) == (
        DurableBoundaryRef(DurableItemRef("left"), BoundarySide.AFTER)
    )
    unanchored = replace(
        graph,
        tiers=(graph.tiers[0], replace(graph.tiers[1], items=(Item(), Item()))),
        relations=(),
    )
    with pytest.raises(
        GraphValidationError, match="no adjacent durable anchor"
    ) as anchor:
        anchored_boundary(unanchored, BoundaryRef(SEGMENT, 1))
    assert anchor.value.stage is RefusalStage.REFERENCE
    assert isinstance(
        anchored_boundary(graph, BoundaryRef(SEGMENT, 0)), DurableBoundaryRef
    )


def reference_shape(*, rate: str | None = None) -> Graph:
    """Build repeated IPA points, an untimed tier, and independently timed events."""
    clock = Tier(
        TierDeclaration(CLOCK, "Clock gaps"),
        tuple(Item(f"clock-{index}") for index in range(3)),
    )
    segments = Tier(
        TierDeclaration(SEGMENT, "Repeated point occurrences"),
        (
            Item("segment-0"),
            Item(
                "segment-1",
                (
                    AttributeValue(START, XsdType.DECIMAL, "0.10"),
                    AttributeValue(DURATION, XsdType.DECIMAL, "0.04"),
                ),
            ),
        ),
    )
    alternate = Tier(
        TierDeclaration(ALTERNATE, "Same-span event"),
        (
            Item(
                "alternate-0",
                (
                    AttributeValue(START, XsdType.DECIMAL, "0.12"),
                    AttributeValue(DURATION, XsdType.DECIMAL, "0.09"),
                ),
            ),
        ),
    )
    syntax = Tier(
        TierDeclaration(SYNTAX, "Untimed syntax"),
        (Item("syntax-0"),),
        (AttributeValue(UNTIMED, XsdType.BOOLEAN, "true"),),
    )
    declarations = (
        SimpleRelationDeclaration(TICKS, CLOCK, CLOCK_TYPE),
        SimpleRelationDeclaration(SEGMENTS, SEGMENT, SEGMENT_TYPE),
        SimpleRelationDeclaration(
            QualifiedName(NS, "alternates"), ALTERNATE, SEGMENT_TYPE
        ),
        SimpleRelationDeclaration(
            QualifiedName(NS, "syntax-members"), SYNTAX, SEGMENT_TYPE
        ),
        BipartiteRelationDeclaration(
            BINDING,
            SEGMENT_TYPE,
            CLOCK_TYPE,
            RelationEndpointKind.BOUNDARY,
            RelationEndpointKind.BOUNDARY,
        ),
    )
    attribute_declarations: tuple[AttributeDeclaration, ...] = (
        AttributeDeclaration(UNIT, AttributeDomain.DOCUMENT, XsdType.STRING),
        AttributeDeclaration(TICK, AttributeDomain.BOUNDARY, XsdType.INTEGER),
        AttributeDeclaration(GAP, AttributeDomain.BOUNDARY, XsdType.INTEGER),
        AttributeDeclaration(UNTIMED, AttributeDomain.TIER, XsdType.BOOLEAN),
        AttributeDeclaration(START, AttributeDomain.ITEM, XsdType.DECIMAL),
        AttributeDeclaration(DURATION, AttributeDomain.ITEM, XsdType.DECIMAL),
    )
    attributes: tuple[AttributeValue, ...] = (
        AttributeValue(UNIT, XsdType.STRING, "s"),
    )
    if rate is not None:
        attribute_declarations = (
            *attribute_declarations,
            AttributeDeclaration(RATE, AttributeDomain.DOCUMENT, XsdType.DECIMAL),
        )
        attributes = (*attributes, AttributeValue(RATE, XsdType.DECIMAL, rate))
    boundaries = tuple(
        Boundary(
            anchored_boundary(
                Graph(
                    (NamespaceDeclaration("clock", NS),),
                    (clock, segments, alternate, syntax),
                    declarations,
                    attribute_declarations=attribute_declarations,
                    attributes=attributes,
                ),
                BoundaryRef(CLOCK, index),
            ),
            (
                AttributeValue(TICK, XsdType.INTEGER, str(tick)),
                AttributeValue(GAP, XsdType.INTEGER, str(gap)),
            ),
        )
        for index, (tick, gap) in enumerate(((0, 0), (1, 0), (1, 1), (2, 0)))
    )
    bare = Graph(
        (NamespaceDeclaration("clock", NS),),
        (clock, segments, alternate, syntax),
        declarations,
        attribute_declarations=attribute_declarations,
        boundary_values=boundaries,
        attributes=attributes,
    )
    relations = tuple(
        RelationInstance(
            BINDING,
            anchored_boundary(bare, BoundaryRef(source_tier, source)),
            anchored_boundary(bare, BoundaryRef(CLOCK, target)),
        )
        for source_tier, source, target in (
            (SEGMENT, 0, 1),
            (SEGMENT, 1, 2),
            (SEGMENT, 2, 3),
            (ALTERNATE, 0, 2),
            (ALTERNATE, 1, 3),
        )
    )
    return replace(bare, relations=relations)


def advanced_profile(graph: Graph, rate: QualifiedName | None = None) -> ClockProfile:
    """Apply every optional clock role used by the reference-shaped fixture."""
    return ClockProfile(
        graph,
        CLOCK,
        BINDING,
        rate,
        UNIT,
        TICK,
        GAP,
        UNTIMED,
        START,
        DURATION,
    )


def clock_profile_data(rate: QualifiedName | None = None) -> dict[str, object]:
    """Encode the declarative profile used by the advanced fixture."""
    return {
        "clock_tier": CLOCK.to_data(),
        "binding_relation": BINDING.to_data(),
        "rate_attribute": None if rate is None else rate.to_data(),
        "unit_attribute": UNIT.to_data(),
        "tick_attribute": TICK.to_data(),
        "gap_attribute": GAP.to_data(),
        "untimed_attribute": UNTIMED.to_data(),
        "start_attribute": START.to_data(),
        "duration_attribute": DURATION.to_data(),
    }


def test_clock_profile_from_data_is_strict_and_constructs_full_profile() -> None:
    """Declarative profiles use exact fields and explicit nullable roles."""
    graph = reference_shape()
    decoded = ClockProfile.from_data(graph, clock_profile_data())
    expected = advanced_profile(graph)
    assert decoded.coordinates == expected.coordinates
    assert decoded.structural_span(SEGMENT, 1) == expected.structural_span(SEGMENT, 1)
    assert decoded.timing(SEGMENT, 1) == expected.timing(SEGMENT, 1)

    missing = clock_profile_data()
    del missing["gap_attribute"]
    with pytest.raises(ValueError, match="clock profile fields"):
        ClockProfile.from_data(graph, missing)
    malformed = clock_profile_data()
    malformed["clock_tier"] = None
    with pytest.raises(
        ValueError, match=r"clock profile\.clock_tier must be an object"
    ):
        ClockProfile.from_data(graph, malformed)
    malformed = clock_profile_data()
    malformed["rate_attribute"] = "rate"
    with pytest.raises(
        ValueError, match=r"clock profile\.rate_attribute must be an object"
    ):
        ClockProfile.from_data(graph, malformed)
    malformed = clock_profile_data()
    malformed["clock_tier"] = {"namespace": [NS], "local_name": "clock"}
    with pytest.raises(
        ValueError, match=r"clock profile\.clock_tier\.namespace must be a string"
    ):
        ClockProfile.from_data(graph, malformed)
    malformed = clock_profile_data()
    malformed["unit_attribute"] = {"namespace": 12, "local_name": "timing-unit"}
    with pytest.raises(
        ValueError, match=r"clock profile\.unit_attribute\.namespace must be a string"
    ):
        ClockProfile.from_data(graph, malformed)
    extra = clock_profile_data()
    extra["surprise"] = None
    with pytest.raises(ValueError, match="clock profile fields"):
        ClockProfile.from_data(graph, extra)


def with_stored_timing(
    graph: Graph,
    tier_name: QualifiedName,
    index: int,
    start: str,
    duration: str,
) -> Graph:
    """Replace one event's stored physical timing without changing its structure."""
    tiers = list(graph.tiers)
    tier_index = next(
        offset
        for offset, tier in enumerate(tiers)
        if tier.declaration.name == tier_name
    )
    tier = tiers[tier_index]
    items = list(tier.items)
    items[index] = replace(
        items[index],
        attributes=(
            AttributeValue(START, XsdType.DECIMAL, start),
            AttributeValue(DURATION, XsdType.DECIMAL, duration),
        ),
    )
    tiers[tier_index] = replace(tier, items=tuple(items))
    return replace(graph, tiers=tuple(tiers))


def test_repeated_reference_points_have_ordered_refined_spans() -> None:
    """Two point occurrences at tick one retain distinct gap endpoints for DOT."""
    profile = advanced_profile(reference_shape())
    assert profile.structural_span(SEGMENT, 0) == (
        ClockCoordinate(1, 0),
        ClockCoordinate(1, 1),
    )
    assert profile.structural_span(SEGMENT, 1) == (
        ClockCoordinate(1, 1),
        ClockCoordinate(2, 0),
    )


def test_one_graph_mixes_complete_timing_with_a_wholly_untimed_tier() -> None:
    """The syntax tier opts out while both event tiers retain total bindings."""
    profile = advanced_profile(reference_shape())
    assert not profile.is_timed(SYNTAX)
    assert profile.is_timed(SEGMENT)
    with pytest.raises(ValueError, match="tier .*syntax.* is untimed"):
        profile.extent(SYNTAX)


def test_same_span_events_keep_different_nonuniform_physical_timings() -> None:
    """Independent timings need neither a uniform rate nor span identity."""
    profile = advanced_profile(reference_shape())
    assert profile.structural_span(SEGMENT, 1) == profile.structural_span(ALTERNATE, 0)
    assert profile.timing(SEGMENT, 1) == PhysicalTiming(
        Decimal("0.1"), Decimal("0.04"), "s"
    )
    assert profile.timing(ALTERNATE, 0) == PhysicalTiming(
        Decimal("0.12"), Decimal("0.09"), "s"
    )
    assert not profile.has_uniform_rate
    with pytest.raises(ValueError, match="no uniform rate"):
        profile.duration(SEGMENT, 0)


def test_relaxations_refuse_partial_binding_and_timing_contradictions() -> None:
    """Opt-outs are whole-tier and dual physical sources must agree exactly."""
    graph = reference_shape()
    with pytest.raises(ValueError, match="has no clock binding"):
        advanced_profile(replace(graph, relations=graph.relations[:-1]))
    syntax_binding = RelationInstance(
        BINDING,
        anchored_boundary(graph, BoundaryRef(SYNTAX, 0)),
        anchored_boundary(graph, BoundaryRef(CLOCK, 0)),
    )
    with pytest.raises(ValueError, match="untimed tier.*has 1 clock bindings"):
        advanced_profile(replace(graph, relations=(*graph.relations, syntax_binding)))
    with pytest.raises(ValueError, match="stored timing contradicts clock"):
        advanced_profile(reference_shape(rate="10"), RATE)


def test_refinement_and_stored_timing_refusals_name_the_offender() -> None:
    """Malformed refinement, partial timing, and fractional structure fail loudly."""
    graph = reference_shape()
    bad_positions = list(graph.boundary_values)
    bad_positions[2] = replace(
        bad_positions[2],
        attributes=(
            AttributeValue(TICK, XsdType.INTEGER, "1"),
            AttributeValue(GAP, XsdType.INTEGER, "0"),
        ),
    )
    with pytest.raises(ValueError, match="not strictly ordered"):
        advanced_profile(replace(graph, boundary_values=tuple(bad_positions)))
    items = list(graph.tiers[1].items)
    items[1] = replace(
        items[1], attributes=(AttributeValue(START, XsdType.DECIMAL, "0.2"),)
    )
    tiers = list(graph.tiers)
    tiers[1] = replace(tiers[1], items=tuple(items))
    with pytest.raises(ValueError, match="item.*partial stored timing"):
        advanced_profile(replace(graph, tiers=tuple(tiers)))
    with pytest.raises(ValueError, match="non-integral index 1.5"):
        BoundaryRef(SEGMENT, 1.5)  # type: ignore[arg-type]


def test_new_clock_role_declarations_and_values_are_checked() -> None:
    """Every optional role is paired and typed, and every coordinate has values."""
    graph = reference_shape()
    with pytest.raises(ValueError, match="requires both tick and gap"):
        ClockProfile(graph, CLOCK, BINDING, None, UNIT, TICK)
    with pytest.raises(ValueError, match="requires both start and duration"):
        ClockProfile(graph, CLOCK, BINDING, None, UNIT, TICK, GAP, UNTIMED, START)
    with pytest.raises(ValueError, match="lacks refinement"):
        advanced_profile(replace(graph, boundary_values=graph.boundary_values[:-1]))
    bad_unit = replace(
        graph,
        attributes=tuple(
            AttributeValue(UNIT, XsdType.STRING, "") if value.name == UNIT else value
            for value in graph.attributes
        ),
    )
    with pytest.raises(ValueError, match="clock unit.*is empty"):
        advanced_profile(bad_unit)
    bad_tick = tuple(
        replace(declaration, value_type=XsdType.DECIMAL)
        if declaration.name == TICK
        else declaration
        for declaration in graph.attribute_declarations
    )
    object.__setattr__(graph, "attribute_declarations", bad_tick)
    with pytest.raises(ValueError, match="clock tick must be a boundary integer"):
        advanced_profile(graph)


def test_gap_past_its_ticks_refinement_is_refused() -> None:
    """A near-valid clock cannot name a gap outside its tick's refinement."""
    graph = reference_shape()
    boundary_values = list(graph.boundary_values)
    boundary_values[2] = replace(
        boundary_values[2],
        attributes=(
            AttributeValue(TICK, XsdType.INTEGER, "1"),
            AttributeValue(GAP, XsdType.INTEGER, "9"),
        ),
    )
    malformed = replace(graph, boundary_values=tuple(boundary_values))

    message = None
    try:
        advanced_profile(malformed)
    except ValueError as error:
        message = str(error)
    assert message == "clock gap 9 for tick 1 exceeds refinement count 1"


def test_stored_timing_value_refusals_and_exact_agreement() -> None:
    """Untimed and negative values fail while exact stored/derived values reconcile."""
    graph = reference_shape()
    tiers = list(graph.tiers)
    syntax = tiers[3]
    tiers[3] = replace(
        syntax,
        items=(
            replace(
                syntax.items[0],
                attributes=(
                    AttributeValue(START, XsdType.DECIMAL, "0"),
                    AttributeValue(DURATION, XsdType.DECIMAL, "1"),
                ),
            ),
        ),
    )
    with pytest.raises(ValueError, match="untimed tier item.*has stored timing"):
        advanced_profile(replace(graph, tiers=tuple(tiers)))
    segments = tiers[1]
    tiers[1] = replace(
        segments,
        items=(
            replace(
                segments.items[0],
                attributes=(
                    AttributeValue(START, XsdType.DECIMAL, "0"),
                    AttributeValue(DURATION, XsdType.DECIMAL, "-1"),
                ),
            ),
            segments.items[1],
        ),
    )
    tiers[3] = syntax
    with pytest.raises(ValueError, match="item.*has negative duration"):
        advanced_profile(replace(graph, tiers=tuple(tiers)))

    calibrated = reference_shape(rate="10")
    calibrated_tiers = list(calibrated.tiers)
    for tier_index, item_index, start, duration in (
        (1, 1, "0.1", "0.1"),
        (2, 0, "0.1", "0.1"),
    ):
        tier = calibrated_tiers[tier_index]
        items = list(tier.items)
        items[item_index] = replace(
            items[item_index],
            attributes=(
                AttributeValue(START, XsdType.DECIMAL, start),
                AttributeValue(DURATION, XsdType.DECIMAL, duration),
            ),
        )
        calibrated_tiers[tier_index] = replace(tier, items=tuple(items))
    profile = advanced_profile(replace(calibrated, tiers=tuple(calibrated_tiers)), RATE)
    assert profile.unit == "s"
    assert profile.timing(SEGMENT, 0) == PhysicalTiming(
        Decimal("0.1"), Decimal("0"), "s"
    )
    assert profile.timing(SEGMENT, 1) == PhysicalTiming(
        Decimal("0.1"), Decimal("0.1"), "s"
    )
    assert advanced_profile(graph).timing(SEGMENT, 0) is None


@pytest.mark.parametrize("trap_inexact", (True, False), ids=("trapped", "untrapped"))
@pytest.mark.parametrize("rounding", ROUNDING_MODES)
@pytest.mark.parametrize("precision", PRECISIONS)
def test_timing_and_exact_agreement_ignore_decimal_context(
    precision: int, rounding: str, trap_inexact: bool
) -> None:
    """Every stored and derived timing decision is independent of Decimal context."""
    graph = reference_shape(rate="8")
    graph = with_stored_timing(graph, SEGMENT, 1, "0.125", "0.125")
    graph = with_stored_timing(graph, ALTERNATE, 0, "0.125", "0.125")
    with localcontext() as context:
        context.prec = precision
        context.rounding = getattr(decimal, rounding)
        context.traps[Inexact] = trap_inexact
        profile = advanced_profile(graph, RATE)
        derived = profile.timing(SEGMENT, 0)
        stored = profile.timing(SEGMENT, 1)
    assert derived == PhysicalTiming(Decimal("0.125"), Decimal("0"), "s")
    assert stored == PhysicalTiming(Decimal("0.125"), Decimal("0.125"), "s")


@pytest.mark.parametrize("trap_inexact", (True, False), ids=("trapped", "untrapped"))
@pytest.mark.parametrize("rounding", ROUNDING_MODES)
@pytest.mark.parametrize("precision", PRECISIONS)
def test_inexact_stored_and_derived_timing_refuse_in_every_context(
    precision: int, rounding: str, trap_inexact: bool
) -> None:
    """A finite approximation cannot masquerade as the exact ratio one seventh."""
    rounded = "0.1428571428571428571428571429"
    graph = reference_shape(rate="7")
    graph = with_stored_timing(graph, SEGMENT, 1, rounded, rounded)
    graph = with_stored_timing(graph, ALTERNATE, 0, rounded, rounded)
    with localcontext() as context:
        context.prec = precision
        context.rounding = getattr(decimal, rounding)
        context.traps[Inexact] = trap_inexact
        with pytest.raises(ValueError, match="stored timing contradicts clock"):
            advanced_profile(graph, RATE)

        derived_only = ClockProfile(fixture("7"), CLOCK, BINDING, RATE, UNIT)
        with pytest.raises(ValueError, match="cannot be represented exactly"):
            derived_only.timing(SEGMENT, 0)


def test_low_precision_cannot_accept_two_disagreeing_timing_sources() -> None:
    """A rounded match is not exact agreement with the clock-derived ratio."""
    graph = reference_shape(rate="7")
    graph = with_stored_timing(graph, SEGMENT, 1, "0.14", "0.14")
    graph = with_stored_timing(graph, ALTERNATE, 0, "0.14", "0.14")
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_DOWN
        with pytest.raises(ValueError, match="stored timing contradicts clock"):
            advanced_profile(graph, RATE)


def test_timing_is_silent_for_untimed_tiers_regardless_of_rate() -> None:
    """An untimed tier has no physical timing under either calibration mode."""
    assert advanced_profile(reference_shape()).timing(SYNTAX, 0) is None
    graph = reference_shape(rate="8")
    graph = with_stored_timing(graph, SEGMENT, 1, "0.125", "0.125")
    graph = with_stored_timing(graph, ALTERNATE, 0, "0.125", "0.125")
    assert advanced_profile(graph, RATE).timing(SYNTAX, 0) is None


def test_untimed_structural_queries_name_the_tier_opt_out() -> None:
    """Structural queries identify an explicit untimed-tier refusal."""
    profile = advanced_profile(reference_shape())
    with pytest.raises(ValueError, match="tier .*syntax.* is untimed"):
        profile.refined_coordinate(BoundaryRef(SYNTAX, 0))
    with pytest.raises(ValueError, match="tier .*syntax.* is untimed"):
        profile.structural_span(SYNTAX, 0)


def test_refined_coordinate_values_remain_nonnegative_integral_structure() -> None:
    """The profile's coordinate value object repeats the kernel's loud boundary."""
    for tick, gap, message in (
        (1.5, 0, "clock tick"),
        (0, 1.5, "clock gap"),
        (-1, 0, "negative"),
        (0, -1, "negative"),
    ):
        with pytest.raises(ValueError, match=message):
            ClockCoordinate(tick, gap)  # type: ignore[arg-type]


SPINE_TICK = QualifiedName(NS, "spine-tick")
SPINE_GAP = QualifiedName(NS, "spine-gap")
SPINE_UNIT = QualifiedName(NS, "spine-unit")


def spine_fixture(
    raw: tuple[tuple[int, int], ...], *, with_unit: bool = False
) -> Graph:
    """Build a clock-only graph carrying tick/gap boundary values alone.

    Relations and document attributes stay empty, mirroring the structural
    input the DOT spine is derived from without any tier-to-clock binding.
    """
    tiers = (
        Tier(
            TierDeclaration(CLOCK, "clock"),
            tuple(Item(f"cell-{index}") for index in range(len(raw) - 1)),
        ),
    )
    declarations: tuple[AttributeDeclaration, ...] = (
        AttributeDeclaration(SPINE_TICK, AttributeDomain.BOUNDARY, XsdType.INTEGER),
        AttributeDeclaration(SPINE_GAP, AttributeDomain.BOUNDARY, XsdType.INTEGER),
    )
    if with_unit:
        declarations = (
            *declarations,
            AttributeDeclaration(SPINE_UNIT, AttributeDomain.DOCUMENT, XsdType.STRING),
        )
    boundaries = tuple(
        Boundary(
            BoundaryRef(CLOCK, index),
            (
                AttributeValue(SPINE_TICK, XsdType.INTEGER, str(tick)),
                AttributeValue(SPINE_GAP, XsdType.INTEGER, str(gap)),
            ),
        )
        for index, (tick, gap) in enumerate(raw)
    )
    return Graph(
        (NamespaceDeclaration("s", NS),),
        tiers,
        (),
        attribute_declarations=declarations,
        boundary_values=boundaries,
        attributes=(
            (AttributeValue(SPINE_UNIT, XsdType.STRING, "cell"),) if with_unit else ()
        ),
    )


def test_from_boundary_values_derives_the_spine_without_relations_or_unit() -> None:
    """The structural factory reads the spine from boundary values alone."""
    graph = spine_fixture(((0, 0), (0, 1), (1, 0)))
    profile = ClockProfile.from_boundary_values(
        graph, CLOCK, tick_attribute=SPINE_TICK, gap_attribute=SPINE_GAP
    )
    assert profile.coordinates == (
        ClockCoordinate(0, 0),
        ClockCoordinate(0, 1),
        ClockCoordinate(1, 0),
    )
    assert profile.clock_tier == CLOCK
    assert profile.rate is None
    assert profile.unit == ""


def test_from_boundary_values_reads_an_optional_unit_when_named() -> None:
    """A unit is read only when its attribute is supplied."""
    graph = spine_fixture(((0, 0), (0, 1)), with_unit=True)
    profile = ClockProfile.from_boundary_values(
        graph,
        CLOCK,
        tick_attribute=SPINE_TICK,
        gap_attribute=SPINE_GAP,
        unit_attribute=SPINE_UNIT,
    )
    assert profile.unit == "cell"


def test_from_boundary_values_collapse_folds_each_tick_trailing_gap() -> None:
    """Collapsing drops each tick's closing boundary, leaving occupied gaps."""
    raw = (
        (0, 0),
        (0, 1),
        (0, 2),
        (1, 0),
        (1, 1),
        (1, 2),
        (1, 3),
        (2, 0),
        (2, 1),
        (2, 2),
    )
    graph = spine_fixture(raw)
    raw_profile = ClockProfile.from_boundary_values(
        graph, CLOCK, tick_attribute=SPINE_TICK, gap_attribute=SPINE_GAP
    )
    assert len(raw_profile.coordinates) == len(raw)
    collapsed = ClockProfile.from_boundary_values(
        graph,
        CLOCK,
        tick_attribute=SPINE_TICK,
        gap_attribute=SPINE_GAP,
        collapse_shared_boundaries=True,
    )
    assert collapsed.coordinates == (
        ClockCoordinate(0, 0),
        ClockCoordinate(0, 1),
        ClockCoordinate(1, 0),
        ClockCoordinate(1, 1),
        ClockCoordinate(1, 2),
        ClockCoordinate(2, 0),
        ClockCoordinate(2, 1),
    )
    # Sanity: sum(R_t) - num_ticks == 10 - 3 == 7.
    assert len(collapsed.coordinates) == len(raw) - 3


def test_from_boundary_values_collapse_refuses_single_boundary_tick() -> None:
    """A tick with one raw boundary cannot be collapsed away."""
    graph = spine_fixture(((0, 0), (1, 0), (1, 1)))
    with pytest.raises(ValueError, match="single raw boundary"):
        ClockProfile.from_boundary_values(
            graph,
            CLOCK,
            tick_attribute=SPINE_TICK,
            gap_attribute=SPINE_GAP,
            collapse_shared_boundaries=True,
        )


def test_structural_profile_refuses_every_non_spine_timing_query() -> None:
    """A spine-only profile refuses queries needing tier-to-clock bindings."""
    graph = spine_fixture(((0, 0), (0, 1), (1, 0)))
    profile = ClockProfile.from_boundary_values(
        graph, CLOCK, tick_attribute=SPINE_TICK, gap_attribute=SPINE_GAP
    )
    for call in (
        lambda: profile.is_timed(SEGMENT),
        lambda: profile.clock_index(BoundaryRef(SEGMENT, 0)),
        lambda: profile.refined_coordinate(BoundaryRef(SEGMENT, 0)),
        lambda: profile.extent(SEGMENT),
        lambda: profile.structural_span(SEGMENT, 0),
        lambda: profile.timing(SEGMENT, 0),
        lambda: profile.duration(SEGMENT, 0),
    ):
        with pytest.raises(ValueError, match="from_boundary_values"):
            call()


def test_from_boundary_values_refuses_bad_graph_and_missing_clock_tier() -> None:
    """Construction refusals name the offending input."""
    with pytest.raises(TypeError, match="got str"):
        ClockProfile.from_boundary_values(
            "graph",  # type: ignore[arg-type]
            CLOCK,
            tick_attribute=SPINE_TICK,
            gap_attribute=SPINE_GAP,
        )
    graph = spine_fixture(((0, 0), (0, 1)))
    with pytest.raises(ValueError, match="not declared"):
        ClockProfile.from_boundary_values(
            graph, SEGMENT, tick_attribute=SPINE_TICK, gap_attribute=SPINE_GAP
        )


def test_clock_profile_modes_and_required_full_profile_fields_are_explicit() -> None:
    """The public predicate and optional-in-fact fields describe both modes."""
    graph = fixture()
    full = ClockProfile(graph, CLOCK, BINDING, RATE, UNIT)
    assert not full.is_structural
    with pytest.raises(ValueError, match="binding relation is required"):
        ClockProfile(graph, CLOCK, None, RATE, UNIT)
    with pytest.raises(ValueError, match="unit attribute is required"):
        ClockProfile(graph, CLOCK, BINDING, RATE, None)

    structural_graph = spine_fixture(((0, 0), (0, 1)))
    structural = ClockProfile.from_boundary_values(
        structural_graph,
        CLOCK,
        tick_attribute=SPINE_TICK,
        gap_attribute=SPINE_GAP,
    )
    assert structural.is_structural
    assert structural.binding_relation is None
    assert structural.unit_attribute is None


def test_extent_distinguishes_missing_clock_and_untimed_tiers() -> None:
    """Each unsupported extent request identifies its distinct cause."""
    profile = advanced_profile(reference_shape())
    with pytest.raises(ValueError, match="is the clock tier"):
        profile.extent(CLOCK)
    with pytest.raises(ValueError, match="is untimed"):
        profile.extent(SYNTAX)
    missing = QualifiedName(NS, "missing-extent")
    with pytest.raises(ValueError, match="is not declared"):
        profile.extent(missing)
