"""Pin the generic DOT renderer and its declared ordering."""

from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

import tiergraph_dot
from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    BoundarySide,
    ClockProfile,
    DurablePositionRef,
    Graph,
    Item,
    ItemRef,
    NamespaceDeclaration,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    Position,
    PositionRef,
    QualifiedName,
    RelationEndpointKind,
    RelationInstance,
    RelationSideDeclaration,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    XsdType,
    anchored_position,
)

NS = "urn:tiergraph:dot:test"


def name(local: str) -> QualifiedName:
    """Return one fixture name."""
    return QualifiedName(NS, local)


def graph_and_clock() -> tuple[Graph, ClockProfile]:
    """Build refined, physically timed, timed-and-untimed renderer input."""
    clock_name, segment, note = name("clock"), name("segment"), name("note")
    clock_type, segment_type, note_type = (
        name("clock-type"),
        name("segment-type"),
        name("note-type"),
    )
    tick, gap, untimed = name("tick"), name("gap"), name("untimed")
    unit, start, duration = name("unit"), name("start"), name("duration")
    binding, link, choices = name("binding"), name("link"), name("choices")
    attributes = (
        AttributeDeclaration(tick, AttributeDomain.POSITION, XsdType.INTEGER),
        AttributeDeclaration(gap, AttributeDomain.POSITION, XsdType.INTEGER),
        AttributeDeclaration(untimed, AttributeDomain.TIER, XsdType.BOOLEAN),
        AttributeDeclaration(unit, AttributeDomain.DOCUMENT, XsdType.STRING),
        AttributeDeclaration(start, AttributeDomain.ITEM, XsdType.DECIMAL),
        AttributeDeclaration(duration, AttributeDomain.ITEM, XsdType.DECIMAL),
    )
    tiers = (
        Tier(
            TierDeclaration(clock_name, "Clock"),
            tuple(Item(f"tick-{i}") for i in range(3)),
        ),
        Tier(
            TierDeclaration(segment, "Segments"),
            tuple(
                Item(
                    f"seg-{i}",
                    (
                        AttributeValue(start, XsdType.DECIMAL, value_start),
                        AttributeValue(duration, XsdType.DECIMAL, value_duration),
                    ),
                )
                for i, (value_start, value_duration) in enumerate(
                    (("0.05", "0.15"), ("0.2", "0.3"))
                )
            ),
        ),
        Tier(
            TierDeclaration(note, "Notes"),
            (Item("note-0"),),
            (AttributeValue(untimed, XsdType.BOOLEAN, "true"),),
        ),
    )
    declarations = (
        SimpleRelationDeclaration(name("clock-members"), clock_name, clock_type),
        SimpleRelationDeclaration(name("segments"), segment, segment_type),
        SimpleRelationDeclaration(name("notes"), note, note_type),
        BipartiteRelationDeclaration(
            binding,
            segment_type,
            clock_type,
            RelationEndpointKind.BOUNDARY,
            RelationEndpointKind.BOUNDARY,
        ),
        BipartiteRelationDeclaration(link, segment_type, note_type),
        PolyadicRelationDeclaration(
            choices,
            RelationSideDeclaration((RelationEndpointKind.ITEM,), (segment,)),
            RelationSideDeclaration((RelationEndpointKind.ITEM,), (note,)),
        ),
    )
    positions = tuple(
        Position(
            PositionRef(clock_name, index),
            (
                AttributeValue(tick, XsdType.INTEGER, str(coarse)),
                AttributeValue(gap, XsdType.INTEGER, str(refined_gap)),
            ),
        )
        for index, (coarse, refined_gap) in enumerate(((0, 0), (0, 1), (1, 0), (2, 0)))
    )
    bare = Graph(
        (NamespaceDeclaration("d", NS),),
        tiers,
        declarations,
        attribute_declarations=attributes,
        position_values=positions,
        attributes=(AttributeValue(unit, XsdType.STRING, "s"),),
    )
    bindings = tuple(
        RelationInstance(
            binding,
            anchored_position(bare, PositionRef(segment, source)),
            anchored_position(bare, PositionRef(clock_name, target)),
        )
        for source, target in ((0, 0), (1, 2), (2, 3))
    )
    graph = replace(
        bare,
        relations=(
            *bindings,
            RelationInstance(link, ItemRef(segment, 1), ItemRef(note, 0)),
        ),
        polyadic_relations=(
            PolyadicRelationInstance(
                choices, (ItemRef(segment, 0), ItemRef(segment, 1)), (ItemRef(note, 0),)
            ),
        ),
    )
    return graph, ClockProfile(
        graph, clock_name, binding, None, unit, tick, gap, untimed, start, duration
    )


def test_refined_clock_mixed_tiers_extents_timing_and_relations_are_exact() -> None:
    """The checked sample pins exact bytes rather than plausible output alone."""
    graph, profile = graph_and_clock()
    rendered = tiergraph_dot.dumps(graph, clock=profile)
    assert hashlib.sha256(rendered.encode()).hexdigest() == (
        "d4ceb8f02db0945494ebfde54319ce999c47acd883e42d781a21ca1279c7452f"
    )
    assert 'label="0.1"' in rendered
    assert 'label="seg-0\\nduration=0.15\\nstart=0.05\\ntime=0.05+0.15 s"' in rendered
    assert 'item_1_0 -> guide_1_2 [xlabel="extent"' in rendered
    assert "subgraph tier_2" in rendered
    assert "clock_0 -> item_1_0" in rendered
    assert rendered.index("// Declared bipartite relations.") < rendered.index(
        "// Declared polyadic relations."
    )


def test_bare_graph_and_empty_tier_policy() -> None:
    """Graphs need no profile, while referenced hidden tiers are loudly refused."""
    graph, _ = graph_and_clock()
    assert "refined clock" not in tiergraph_dot.dumps(graph)
    empty, empty_type, boundary_link = (
        name("empty"),
        name("empty-type"),
        name("boundary-link"),
    )
    with_empty = replace(
        graph,
        tiers=(*graph.tiers, Tier(TierDeclaration(empty, "Empty"))),
        relation_declarations=(
            *graph.relation_declarations,
            SimpleRelationDeclaration(name("empty-members"), empty, empty_type),
            BipartiteRelationDeclaration(
                boundary_link,
                empty_type,
                empty_type,
                RelationEndpointKind.BOUNDARY,
                RelationEndpointKind.BOUNDARY,
            ),
        ),
        relations=(
            *graph.relations,
            RelationInstance(
                boundary_link,
                DurablePositionRef(empty, BoundarySide.BEFORE),
                DurablePositionRef(empty, BoundarySide.AFTER),
            ),
        ),
    )
    with pytest.raises(ValueError, match="omitted empty tier"):
        tiergraph_dot.dumps(with_empty)
    assert "subgraph tier_3" in tiergraph_dot.dumps(
        with_empty, include_empty_tiers=True
    )


def test_renderer_refuses_wrong_types_and_foreign_profile() -> None:
    """Refusals identify each offending renderer input."""
    graph, profile = graph_and_clock()
    with pytest.raises(TypeError, match="got str"):
        tiergraph_dot.dumps("graph")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="got str"):
        tiergraph_dot.dumps(graph, clock="clock")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="not the graph being rendered"):
        tiergraph_dot.dumps(replace(graph), clock=profile)


def test_empty_zero_span_and_unrefined_profiles_have_defined_output() -> None:
    """Degenerate public cases exercise omissions instead of relying on accidents."""
    assert tiergraph_dot.dumps(Graph((), (), ())) == (
        "digraph tiergraph {\n"
        '  graph [rankdir=TB, newrank=true, ranksep="0.62 equally", nodesep=0.28, splines=line];\n'
        '  node [fontname="Helvetica"];\n'
        '  edge [fontname="Helvetica", fontsize=9];\n'
        "}\n"
    )

    graph, _ = graph_and_clock()
    unrefined = ClockProfile(
        graph,
        name("clock"),
        name("binding"),
        None,
        name("unit"),
        untimed_attribute=name("untimed"),
        start_attribute=name("start"),
        duration_attribute=name("duration"),
    )
    assert 'label="1"' in tiergraph_dot.dumps(graph, clock=unrefined)

    relations = list(graph.relations)
    relations[1] = replace(relations[1], right=relations[0].right)
    zero_graph = replace(graph, relations=tuple(relations))
    zero_profile = ClockProfile(
        zero_graph,
        name("clock"),
        name("binding"),
        None,
        name("unit"),
        name("tick"),
        name("gap"),
        name("untimed"),
        name("start"),
        name("duration"),
    )
    rendered = tiergraph_dot.dumps(zero_graph, clock=zero_profile)
    assert 'item_1_0 -> guide_1_0 [xlabel="extent"' not in rendered
