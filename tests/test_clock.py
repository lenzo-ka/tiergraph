"""Exercise the clock profile against timings already carried by ipakit."""

from __future__ import annotations

from dataclasses import replace
from decimal import ROUND_UP, Decimal, Inexact, localcontext
from typing import cast

import pytest

from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    ClockProfile,
    DurableItemRef,
    DurablePositionRef,
    Graph,
    Item,
    NamespaceDeclaration,
    PositionRef,
    QualifiedName,
    RelationEndpointKind,
    RelationInstance,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    XsdType,
    anchored_position,
)

NS = "urn:tiergraph:profile:clock:test"
CLOCK = QualifiedName(NS, "clock")
SEGMENT = QualifiedName(NS, "segment")
CLOCK_TYPE = QualifiedName(NS, "tick")
SEGMENT_TYPE = QualifiedName(NS, "phone")
TICKS = QualifiedName(NS, "ticks")
SEGMENTS = QualifiedName(NS, "segments")
BINDING = QualifiedName(NS, "at-clock-position")
RATE = QualifiedName(NS, "ticks-per-second")
OTHER_BINDING = QualifiedName(NS, "other-clock-binding")


def fixture(rate: str = "10") -> Graph:
    """Encode ipakit's 0.1-second unit timing on a tier with a partial extent."""
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
        ),
        attributes=(AttributeValue(RATE, XsdType.DECIMAL, rate),),
    )
    relations = tuple(
        RelationInstance(
            BINDING,
            anchored_position(bare, PositionRef(SEGMENT, source)),
            anchored_position(bare, PositionRef(CLOCK, target)),
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


def test_real_ipakit_rate_derives_timing_on_a_partial_document_tier() -> None:
    """The source fixture's 0.1-second units occupy clock positions 1 through 3."""
    profile = ClockProfile(fixture(), CLOCK, BINDING, RATE)
    assert profile.rate == Decimal("10.0")
    assert profile.extent(SEGMENT) == (1, 3)
    assert profile.clock_position(PositionRef(SEGMENT, 1)) == 2
    assert profile.duration(SEGMENT, 0) == (1, Decimal("10.0"))
    assert profile.duration(SEGMENT, 1) == (1, Decimal("10.0"))


def test_rate_changes_the_derived_measure_without_moving_structure() -> None:
    """Continuous time has no stored copy that can disagree with the rate."""
    original = fixture("10")
    changed = fixture("20")
    before = ClockProfile(original, CLOCK, BINDING, RATE)
    after = ClockProfile(changed, CLOCK, BINDING, RATE)
    assert original.tiers == changed.tiers
    assert original.relations == changed.relations
    assert before.duration(SEGMENT, 0) == (1, Decimal("10"))
    assert after.duration(SEGMENT, 0) == (1, Decimal("20"))


def test_nonterminating_duration_is_exact_and_ignores_decimal_context() -> None:
    """The profile returns a ratio without performing context-sensitive division."""
    profile = ClockProfile(fixture("3"), CLOCK, BINDING, RATE)
    with localcontext() as context:
        context.prec = 5
        context.rounding = ROUND_UP
        context.traps[Inexact] = True
        duration = profile.duration(SEGMENT, 0)
    assert duration == (1, Decimal("3"))


def test_structural_positions_remain_integral() -> None:
    """A continuous profile does not relax the kernel's structural indices."""
    with pytest.raises(ValueError, match="non-integral index 1.5"):
        PositionRef(SEGMENT, 1.5)  # type: ignore[arg-type]


def test_zero_span_is_shared_and_cannot_carry_per_event_duration() -> None:
    """Equal adjacent bindings derive zero, with no event duration override."""
    graph = fixture()
    relations = list(graph.relations)
    relations[1] = replace(relations[1], right=relations[0].right)
    profile = ClockProfile(
        replace(graph, relations=tuple(relations)), CLOCK, BINDING, RATE
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
    assert ClockProfile(graph, CLOCK, BINDING, RATE).extent(SEGMENT) == (1, 3)


def test_runtime_boundary_invariant_refuses_malformed_endpoints() -> None:
    """Boundary bindings refuse corruption even when assertions are disabled."""
    graph = fixture()
    invalid_left = replace(
        graph.relations[0],
        left=DurableItemRef("segment-0"),  # type: ignore[arg-type]
    )
    object.__setattr__(graph, "relations", (invalid_left, *graph.relations[1:]))
    with pytest.raises(ValueError, match="left endpoint is not a boundary"):
        ClockProfile(graph, CLOCK, BINDING, RATE)

    graph = fixture()
    invalid_right = replace(
        graph.relations[0],
        right=DurableItemRef("clock-0"),  # type: ignore[arg-type]
    )
    object.__setattr__(graph, "relations", (invalid_right, *graph.relations[1:]))
    with pytest.raises(ValueError, match="right endpoint is not a boundary"):
        ClockProfile(graph, CLOCK, BINDING, RATE)


def test_profile_refusals_name_incomplete_or_contradictory_bindings() -> None:
    """Missing, duplicate, backward, and non-clock coordinates cannot go silent."""
    graph = fixture()
    with pytest.raises(ValueError, match="has no clock binding"):
        ClockProfile(
            replace(graph, relations=graph.relations[:-1]), CLOCK, BINDING, RATE
        )
    with pytest.raises(ValueError, match="has two bindings"):
        ClockProfile(
            replace(graph, relations=(*graph.relations, graph.relations[0])),
            CLOCK,
            BINDING,
            RATE,
        )
    backward = list(graph.relations)
    backward[1] = replace(backward[1], right=graph.relations[0].right)
    backward[0] = replace(backward[0], right=graph.relations[1].right)
    with pytest.raises(ValueError, match="go backward"):
        ClockProfile(replace(graph, relations=tuple(backward)), CLOCK, BINDING, RATE)
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
    first_target = cast(DurablePositionRef, graph.relations[0].right)
    wrong_target = replace(
        graph.relations[0],
        right=DurablePositionRef(shadow, first_target.side),
    )
    with pytest.raises(ValueError, match="target is not on the clock"):
        ClockProfile(
            replace(
                shadow_graph, relations=(wrong_target, *shadow_graph.relations[1:])
            ),
            CLOCK,
            BINDING,
            RATE,
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
        anchored_position(graph, PositionRef(CLOCK, 0)),
        anchored_position(graph, PositionRef(CLOCK, 1)),
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
        ClockProfile(self_graph, CLOCK, BINDING, RATE)


def test_profile_declaration_and_lookup_refusals_are_explicit() -> None:
    """Every profile role is declared with the required domain and endpoint kinds."""
    graph = fixture()
    missing = QualifiedName(NS, "missing")
    with pytest.raises(ValueError, match="clock tier.*not declared"):
        ClockProfile(graph, missing, BINDING, RATE)
    with pytest.raises(ValueError, match="clock rate.*not declared"):
        ClockProfile(graph, CLOCK, BINDING, missing)
    with pytest.raises(ValueError, match="clock binding.*not declared"):
        ClockProfile(graph, CLOCK, missing, RATE)
    bad_rate_declaration = replace(
        graph.attribute_declarations[0], value_type=XsdType.DOUBLE
    )
    bad_rate_graph = replace(
        graph,
        attribute_declarations=(bad_rate_declaration,),
        attributes=(AttributeValue(RATE, XsdType.DOUBLE, "10"),),
    )
    with pytest.raises(ValueError, match="document decimal"):
        ClockProfile(bad_rate_graph, CLOCK, BINDING, RATE)
    with pytest.raises(ValueError, match="has no value"):
        ClockProfile(replace(graph, attributes=()), CLOCK, BINDING, RATE)
    with pytest.raises(ValueError, match="must be positive"):
        ClockProfile(fixture("0"), CLOCK, BINDING, RATE)
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
        )
    with pytest.raises(ValueError, match="timed tier.*not declared"):
        ClockProfile(graph, CLOCK, BINDING, RATE).extent(missing)
    with pytest.raises(ValueError, match="has no clock binding"):
        ClockProfile(graph, CLOCK, BINDING, RATE).clock_position(PositionRef(CLOCK, 0))


def test_anchor_helper_refuses_missing_tiers_positions_and_anchors() -> None:
    """Stored bindings always use semantic boundary anchors, including interiors."""
    graph = fixture()
    missing = QualifiedName(NS, "missing")
    with pytest.raises(ValueError, match="outside its tier"):
        anchored_position(graph, PositionRef(missing, 0))
    with pytest.raises(ValueError, match="outside its tier"):
        anchored_position(graph, PositionRef(SEGMENT, 3))
    unanchored = replace(
        graph,
        tiers=(graph.tiers[0], replace(graph.tiers[1], items=(Item(), Item()))),
        relations=(),
    )
    with pytest.raises(ValueError, match="needs a durable right-hand anchor"):
        anchored_position(unanchored, PositionRef(SEGMENT, 1))
    assert isinstance(
        anchored_position(graph, PositionRef(SEGMENT, 0)), DurablePositionRef
    )
