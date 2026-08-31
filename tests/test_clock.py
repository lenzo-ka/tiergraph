"""Exercise the clock profile against timings drawn from a real domain."""

from __future__ import annotations

from dataclasses import replace
from decimal import ROUND_DOWN, ROUND_UP, Decimal, Inexact, localcontext
from typing import cast

import pytest

from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    Boundary,
    BoundaryRef,
    ClockCoordinate,
    ClockProfile,
    DurableBoundaryRef,
    DurableItemRef,
    Graph,
    Item,
    NamespaceDeclaration,
    PhysicalTiming,
    QualifiedName,
    RelationEndpointKind,
    RelationInstance,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    XsdType,
    anchored_boundary,
)


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


def test_real_reference_rate_derives_timing_on_a_partial_document_tier() -> None:
    """The source fixture's 0.1-second units occupy clock coordinates 1 through 3."""
    profile = ClockProfile(fixture(), CLOCK, BINDING, RATE, UNIT)
    assert profile.rate == Decimal("10.0")
    assert profile.extent(SEGMENT) == (ClockCoordinate(1), ClockCoordinate(3))
    assert profile.clock_index(BoundaryRef(SEGMENT, 1)) == 2
    assert profile.duration(SEGMENT, 0) == (1, Decimal("10.0"))
    assert profile.duration(SEGMENT, 1) == (1, Decimal("10.0"))


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


def test_nonterminating_duration_is_exact_and_ignores_decimal_context() -> None:
    """The profile returns a ratio without performing context-sensitive division."""
    profile = ClockProfile(fixture("3"), CLOCK, BINDING, RATE, UNIT)
    with localcontext() as context:
        context.prec = 5
        context.rounding = ROUND_UP
        context.traps[Inexact] = True
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


def test_anchor_helper_refuses_missing_tiers_positions_and_anchors() -> None:
    """Stored bindings always use semantic boundary anchors, including interiors."""
    graph = fixture()
    missing = QualifiedName(NS, "missing")
    with pytest.raises(ValueError, match="outside its tier"):
        anchored_boundary(graph, BoundaryRef(missing, 0))
    with pytest.raises(ValueError, match="outside its tier"):
        anchored_boundary(graph, BoundaryRef(SEGMENT, 3))
    unanchored = replace(
        graph,
        tiers=(graph.tiers[0], replace(graph.tiers[1], items=(Item(), Item()))),
        relations=(),
    )
    with pytest.raises(ValueError, match="needs a durable right-hand anchor"):
        anchored_boundary(unanchored, BoundaryRef(SEGMENT, 1))
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


@pytest.mark.parametrize("precision", (3, 12, 28, 40))
def test_timing_and_exact_agreement_ignore_decimal_context(precision: int) -> None:
    """Every stored and derived timing decision is independent of Decimal context."""
    graph = reference_shape(rate="8")
    graph = with_stored_timing(graph, SEGMENT, 1, "0.125", "0.125")
    graph = with_stored_timing(graph, ALTERNATE, 0, "0.125", "0.125")
    with localcontext() as context:
        context.prec = precision
        context.rounding = ROUND_UP
        context.traps[Inexact] = True
        profile = advanced_profile(graph, RATE)
        derived = profile.timing(SEGMENT, 0)
        stored = profile.timing(SEGMENT, 1)
    assert derived == PhysicalTiming(Decimal("0.125"), Decimal("0"), "s")
    assert stored == PhysicalTiming(Decimal("0.125"), Decimal("0.125"), "s")


@pytest.mark.parametrize("precision", (3, 12, 28, 40))
def test_inexact_stored_and_derived_timing_refuse_in_every_context(
    precision: int,
) -> None:
    """A finite approximation cannot masquerade as the exact ratio one seventh."""
    rounded = "0.1428571428571428571428571429"
    graph = reference_shape(rate="7")
    graph = with_stored_timing(graph, SEGMENT, 1, rounded, rounded)
    graph = with_stored_timing(graph, ALTERNATE, 0, rounded, rounded)
    with localcontext() as context:
        context.prec = precision
        context.rounding = ROUND_UP
        context.traps[Inexact] = False
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
