"""Pin the generic DOT renderer and its declared ordering."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
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
DOT = shutil.which("dot")


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


def graph_with_durable_id(value: str) -> Graph:
    """Put arbitrary renderer input into a valid graph through public dataclasses."""
    graph, _ = graph_and_clock()
    tier = graph.tiers[0]
    items = (replace(tier.items[0], durable_id=value), *tier.items[1:])
    return replace(graph, tiers=(replace(tier, items=items), *graph.tiers[1:]))


def assert_graphviz_accepts(rendered: str) -> None:
    """Ask Graphviz to parse DOT when its executable is installed."""
    if DOT is None:
        pytest.skip(
            "Graphviz 'dot' binary is not installed; parser check is conditional"
        )
    completed = subprocess.run(
        (DOT, "-Tsvg"),
        input=rendered,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


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


def test_clock_view_renders_are_accepted_by_graphviz() -> None:
    """Graphviz accepts the full clock view, including carried hostile Unicode."""
    graph, profile = graph_and_clock()
    assert_graphviz_accepts(tiergraph_dot.dumps(graph, clock=profile))

    hostile_graph = graph_with_durable_id(
        "astral \U0001f642 bidi \u202e nonchar \ufdd0"
    )
    hostile_profile = ClockProfile(
        hostile_graph,
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
    assert_graphviz_accepts(tiergraph_dot.dumps(hostile_graph, clock=hostile_profile))


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


def test_original_nul_durable_id_counterexample_is_refused_by_field() -> None:
    """The kernel-valid counterexample never reaches Graphviz as broken DOT."""
    with pytest.raises(
        ValueError,
        match=r"DOT cannot render item durable ID value 'bad\\x00label'.*U\+0000",
    ):
        tiergraph_dot.dumps(graph_with_durable_id("bad\x00label"))


@pytest.mark.parametrize("codepoint", range(0x20))
def test_every_c0_control_is_deliberately_carried_or_refused(codepoint: int) -> None:
    """LF is representable as a label line break; every other C0 value is refused."""
    value = f"left{chr(codepoint)}right"
    graph = graph_with_durable_id(value)
    if codepoint == 0x0A:
        rendered = tiergraph_dot.dumps(graph)
        assert 'label="left\\nright"' in rendered
        assert_graphviz_accepts(rendered)
    else:
        with pytest.raises(ValueError, match="item durable ID") as refusal:
            tiergraph_dot.dumps(graph)
        assert repr(value) in str(refusal.value)
        assert f"U+{codepoint:04X}" in str(refusal.value)


def test_del_is_deliberately_refused() -> None:
    """DEL is not silently carried as an invisible label character."""
    with pytest.raises(ValueError, match=r"item durable ID.*U\+007F"):
        tiergraph_dot.dumps(graph_with_durable_id("left\x7fright"))


@pytest.mark.parametrize("codepoint", range(0x80, 0xA0))
def test_every_c1_control_is_deliberately_refused(codepoint: int) -> None:
    """C1 controls have no portable visual label representation."""
    value = f"left{chr(codepoint)}right"
    with pytest.raises(ValueError, match="item durable ID") as refusal:
        tiergraph_dot.dumps(graph_with_durable_id(value))
    assert repr(value) in str(refusal.value)
    assert f"U+{codepoint:04X}" in str(refusal.value)


@pytest.mark.parametrize("codepoint", (0xD800, 0xDBFF, 0xDC00, 0xDFFF))
def test_unicode_surrogate_classes_are_deliberately_refused(codepoint: int) -> None:
    """Invalid Unicode scalar values are refused before output encoding."""
    value = f"left{chr(codepoint)}right"
    with pytest.raises(ValueError, match="item durable ID") as refusal:
        tiergraph_dot.dumps(graph_with_durable_id(value))
    assert repr(value) in str(refusal.value)
    assert f"U+{codepoint:04X}" in str(refusal.value)


@pytest.mark.parametrize(
    "value",
    (
        r"backslash\\run",
        'quote""run',
        'mixed\\"\\\\"run',
        "line one\nline two",
        "snowman \N{SNOWMAN}",
    ),
)
def test_representable_quoted_string_classes_are_accepted_by_graphviz(
    value: str,
) -> None:
    """Escapes and printable Unicode remain data in Graphviz-accepted DOT."""
    assert_graphviz_accepts(tiergraph_dot.dumps(graph_with_durable_id(value)))


@pytest.mark.parametrize("value", ("left\rright", "left\r\nright"))
def test_non_lf_newline_variants_are_refused(value: str) -> None:
    """CR and CRLF are refused rather than normalized to LF."""
    with pytest.raises(ValueError, match=r"item durable ID.*U\+000D") as refusal:
        tiergraph_dot.dumps(graph_with_durable_id(value))
    assert repr(value) in str(refusal.value)


@pytest.mark.parametrize(
    "value", ("left\N{LINE SEPARATOR}right", "left\N{PARAGRAPH SEPARATOR}right")
)
def test_printable_unicode_newline_variants_are_carried(value: str) -> None:
    """Unicode separators are valid scalar data and remain byte-for-byte label data."""
    rendered = tiergraph_dot.dumps(graph_with_durable_id(value))
    assert value in rendered
    assert_graphviz_accepts(rendered)


def test_each_structural_rendered_string_surface_names_its_field() -> None:
    """Tier, attribute, and relation label refusals identify their source field."""
    tier_name = name("bad\x00tier")
    item_type = name("type")
    tier_graph = Graph(
        (NamespaceDeclaration("d", NS),),
        (Tier(TierDeclaration(tier_name, "Tier"), (Item("item"),)),),
        (SimpleRelationDeclaration(name("members"), tier_name, item_type),),
    )
    with pytest.raises(ValueError, match="tier name"):
        tiergraph_dot.dumps(tier_graph)

    attribute_name = name("bad\x00attribute")
    attribute_graph = Graph(
        (NamespaceDeclaration("d", NS),),
        (
            Tier(
                TierDeclaration(name("tier"), "Tier"),
                (
                    Item(
                        "item",
                        (AttributeValue(attribute_name, XsdType.STRING, "value"),),
                    ),
                ),
            ),
        ),
        (SimpleRelationDeclaration(name("members"), name("tier"), item_type),),
        attribute_declarations=(
            AttributeDeclaration(attribute_name, AttributeDomain.ITEM, XsdType.STRING),
        ),
    )
    with pytest.raises(ValueError, match="attribute name"):
        tiergraph_dot.dumps(attribute_graph)

    lexical_name = name("attribute")
    lexical_graph = replace(
        attribute_graph,
        tiers=(
            Tier(
                TierDeclaration(name("tier"), "Tier"),
                (
                    Item(
                        "item",
                        (AttributeValue(lexical_name, XsdType.STRING, "bad\x00value"),),
                    ),
                ),
            ),
        ),
        attribute_declarations=(
            AttributeDeclaration(lexical_name, AttributeDomain.ITEM, XsdType.STRING),
        ),
    )
    with pytest.raises(ValueError, match="item attribute lexical value"):
        tiergraph_dot.dumps(lexical_graph)

    graph, _ = graph_and_clock()
    relation = graph.relations[-1]
    bad_relation = name("bad\x00relation")
    relation_graph = replace(
        graph,
        relation_declarations=tuple(
            replace(declaration, name=bad_relation)
            if declaration.name == relation.declaration
            else declaration
            for declaration in graph.relation_declarations
        ),
        relations=(*graph.relations[:-1], replace(relation, declaration=bad_relation)),
    )
    with pytest.raises(ValueError, match="relation name"):
        tiergraph_dot.dumps(relation_graph)


def test_clock_view_refuses_its_rendered_unit_lexical_value() -> None:
    """The profile view uses the same quoting/refusal boundary as structure."""
    graph, _ = graph_and_clock()
    unit_name = name("unit")
    timed_graph = replace(
        graph,
        attributes=(AttributeValue(unit_name, XsdType.STRING, "bad\x00unit"),),
    )
    profile = ClockProfile(
        timed_graph,
        name("clock"),
        name("binding"),
        None,
        unit_name,
        name("tick"),
        name("gap"),
        name("untimed"),
        name("start"),
        name("duration"),
    )
    with pytest.raises(ValueError, match="clock unit attribute lexical value"):
        tiergraph_dot.dumps(timed_graph, clock=profile)


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
