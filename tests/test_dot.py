"""Pin the generic DOT renderer and its declared ordering."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import replace

import pytest

import tiergraph_dot
from tests.test_spanview import fixture as span_fixture
from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    BoundarySide,
    ClockPosition,
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


@pytest.mark.parametrize("alternatives", [False, True])
def test_span_renderer_is_stable_and_graphviz_accepts(alternatives: bool) -> None:
    """Span-aware DOT remains parseable and byte-stable for both detail modes."""
    graph, profile = span_fixture()
    rendered = tiergraph_dot.dumps_spans(
        graph, profile, alternatives=alternatives, include_empty_tiers=True
    )
    assert rendered == tiergraph_dot.dumps_spans(
        graph, profile, alternatives=alternatives, include_empty_tiers=True
    )
    assert 'xlabel="extent"' in rendered
    assert_graphviz_accepts(rendered)


def test_span_renderer_omits_external_character_ranges_without_offsets() -> None:
    """The span renderer handles profiles without external character offsets."""
    graph, profile = span_fixture(offsets=False)
    rendered = tiergraph_dot.dumps_spans(graph, profile)
    assert "chars=" not in rendered
    assert_graphviz_accepts(rendered)


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


# --- Presentation hooks (Deliverable A) ------------------------------------


def _node_lines(rendered: str) -> tuple[set[str], set[str]]:
    """Return (defined node ids, edge-endpoint ids) parsed from DOT."""
    defined: set[str] = set()
    endpoints: set[str] = set()
    for line in rendered.splitlines():
        stripped = line.strip()
        if " -> " in stripped:
            left, _, rest = stripped.partition(" -> ")
            right = rest.split(" ", 1)[0].rstrip(";")
            endpoints.add(left.strip())
            endpoints.add(right.strip())
        elif " [" in stripped and not stripped.startswith(("{", "subgraph", "//")):
            defined.add(stripped.split(" [", 1)[0].strip())
    return defined, endpoints


def test_presentation_default_is_byte_identical() -> None:
    """No profile, an empty profile, and None hooks all reproduce the default."""
    graph, profile = graph_and_clock()
    baseline = tiergraph_dot.dumps(graph, clock=profile)
    assert tiergraph_dot.dumps(graph, clock=profile, presentation=None) == baseline
    assert (
        tiergraph_dot.dumps(
            graph, clock=profile, presentation=tiergraph_dot.DotPresentation()
        )
        == baseline
    )
    all_none = tiergraph_dot.DotPresentation(
        tier_name=lambda _tier: None,
        node_id=lambda _reference: None,
        item_label=lambda _item: None,
    )
    assert tiergraph_dot.dumps(graph, clock=profile, presentation=all_none) == baseline


def test_presentation_overrides_are_applied_without_dangling_ids() -> None:
    """Custom tier names, node ids, and labels reach every reference site."""
    graph, profile = graph_and_clock()
    presentation = tiergraph_dot.DotPresentation(
        tier_name=lambda tier: f"T:{tier.declaration.short_name}",
        node_id=lambda reference: f"N_{reference.tier.local_name}_{reference.index}",
        item_label=lambda item: item.durable_id or "item",
    )
    rendered = tiergraph_dot.dumps(graph, clock=profile, presentation=presentation)
    # Custom ids are defined and referenced; no default item id survives anywhere.
    assert "N_segment_0" in rendered
    assert 'label="T:segment"' in rendered
    assert "item_0_0" not in rendered
    assert "item_1_0" not in rendered
    # Every edge endpoint resolves to a defined node: no dangling reference.
    defined, endpoints = _node_lines(rendered)
    assert endpoints <= defined
    assert_graphviz_accepts(rendered)


def test_presentation_none_return_falls_back_per_element() -> None:
    """A hook returning None for some elements defaults exactly those."""
    graph, profile = graph_and_clock()
    segment_name = name("segment")

    def node_id(reference: ItemRef) -> str | None:
        if reference.tier == segment_name:
            return None
        return f"N_{reference.tier.local_name}_{reference.index}"

    rendered = tiergraph_dot.dumps(
        graph,
        clock=profile,
        presentation=tiergraph_dot.DotPresentation(node_id=node_id),
    )
    # Segment items keep the default id; the note item takes the override.
    assert "item_1_0" in rendered
    assert "N_note_0" in rendered
    defined, endpoints = _node_lines(rendered)
    assert endpoints <= defined
    assert_graphviz_accepts(rendered)


def test_presentation_type_is_checked() -> None:
    """A non-profile presentation argument is refused by type."""
    graph, profile = graph_and_clock()
    with pytest.raises(TypeError, match="got str"):
        tiergraph_dot.dumps(graph, clock=profile, presentation="hooks")  # type: ignore[arg-type]


# --- Structural spine (Deliverable B) --------------------------------------

_EMBEDDED_SPINE = '  // The clock spine is the total order.\n  { rank=same;\n    score_start_clock [shape=plaintext, label="clock"];\n    clock_0_gap_0 [shape=circle, width=0.46, fixedsize=true, group="time_0", label="0.0"];\n    clock_0_gap_1 [shape=circle, width=0.46, fixedsize=true, group="time_1", label="0.1"];\n    clock_1_gap_0 [shape=circle, width=0.46, fixedsize=true, group="time_2", label="1.0"];\n    clock_1_gap_1 [shape=circle, width=0.46, fixedsize=true, group="time_3", label="1.1"];\n    clock_1_gap_2 [shape=circle, width=0.46, fixedsize=true, group="time_4", label="1.2"];\n    clock_2_gap_0 [shape=circle, width=0.46, fixedsize=true, group="time_5", label="2.0"];\n    clock_2_gap_1 [shape=circle, width=0.46, fixedsize=true, group="time_6", label="2.1"];\n    clock_0_gap_0 -> clock_0_gap_1 [weight=100];\n    clock_0_gap_1 -> clock_1_gap_0 [weight=100];\n    clock_1_gap_0 -> clock_1_gap_1 [weight=100];\n    clock_1_gap_1 -> clock_1_gap_2 [weight=100];\n    clock_1_gap_2 -> clock_2_gap_0 [weight=100];\n    clock_2_gap_0 -> clock_2_gap_1 [weight=100];\n  }'

_KAT_SPINE = '  // The clock spine is the total order.\n  { rank=same;\n    score_start_clock [shape=plaintext, label="clock"];\n    clock_0 [shape=circle, width=0.46, fixedsize=true, group="time_0", label="0"];\n    clock_1 [shape=circle, width=0.46, fixedsize=true, group="time_1", label="1"];\n    clock_2 [shape=circle, width=0.46, fixedsize=true, group="time_2", label="2"];\n    clock_3 [shape=circle, width=0.46, fixedsize=true, group="time_3", label="3"];\n    clock_0 -> clock_1 [weight=100];\n    clock_1 -> clock_2 [weight=100];\n    clock_2 -> clock_3 [weight=100];\n  }'


def clock_only_graph(raw: tuple[tuple[int, int], ...]) -> Graph:
    """Build a clock-only graph with tick/gap boundary positions, no relations."""
    clock, tick, gap = name("clock"), name("tick"), name("gap")
    tiers = (
        Tier(
            TierDeclaration(clock, "clock"),
            tuple(Item(f"cell-{index}") for index in range(len(raw) - 1)),
        ),
    )
    attributes = (
        AttributeDeclaration(tick, AttributeDomain.POSITION, XsdType.INTEGER),
        AttributeDeclaration(gap, AttributeDomain.POSITION, XsdType.INTEGER),
    )
    positions = tuple(
        Position(
            PositionRef(clock, index),
            (
                AttributeValue(tick, XsdType.INTEGER, str(coarse)),
                AttributeValue(gap, XsdType.INTEGER, str(refined)),
            ),
        )
        for index, (coarse, refined) in enumerate(raw)
    )
    return Graph(
        (NamespaceDeclaration("d", NS),),
        tiers,
        (),
        attribute_declarations=attributes,
        position_values=positions,
    )


def _structural_spine(raw: tuple[tuple[int, int], ...]) -> str:
    graph = clock_only_graph(raw)
    clock = ClockProfile.from_position_values(
        graph,
        name("clock"),
        tick_attribute=name("tick"),
        gap_attribute=name("gap"),
        collapse_shared_boundaries=True,
    )
    rendered = tiergraph_dot.dumps(graph, clock=clock)
    start = rendered.index("  // The clock spine is the total order.")
    end = rendered.index("\n  }\n", start) + len("\n  }")
    return rendered[start:end]


def test_structural_spine_matches_ipakit_embedded_golden() -> None:
    """The collapsed spine reproduces ipakit's '#a..b#' golden byte-for-byte."""
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
    assert _structural_spine(raw) == _EMBEDDED_SPINE


def test_structural_spine_matches_ipakit_kat_golden() -> None:
    """The collapsed spine reproduces ipakit's 'kat' golden byte-for-byte."""
    raw = ((0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1), (3, 0), (3, 1))
    assert _structural_spine(raw) == _KAT_SPINE


def test_structural_spine_draws_without_raising_and_graphviz_accepts() -> None:
    """A structural clock renders the spine cleanly on a clock-only graph."""
    graph = clock_only_graph(((0, 0), (0, 1), (1, 0)))
    clock = ClockProfile.from_position_values(
        graph, name("clock"), tick_attribute=name("tick"), gap_attribute=name("gap")
    )
    rendered = tiergraph_dot.dumps(graph, clock=clock)
    assert "  // The clock spine is the total order." in rendered
    assert_graphviz_accepts(rendered)


def test_empty_polyadic_relation_emits_nothing() -> None:
    """A declared polyadic relation with no endpoints emits no header or line."""
    graph, _ = graph_and_clock()
    roots = name("roots")
    empty_side = RelationSideDeclaration(
        (RelationEndpointKind.ITEM,), None, minimum=0, maximum=0, allow_empty=True
    )
    empty = replace(
        graph,
        relation_declarations=(
            *graph.relation_declarations,
            PolyadicRelationDeclaration(roots, empty_side, empty_side),
        ),
        polyadic_relations=(
            *graph.polyadic_relations,
            PolyadicRelationInstance(roots, (), ()),
        ),
    )
    rendered = tiergraph_dot.dumps(empty)
    # The non-empty 'choices' relation still renders; only the empty one is gone.
    assert "// Declared polyadic relations." in rendered
    assert "roots" not in rendered


# --- Occupied-spine full goldens (Deliverable B, item-as-anchor) ------------
#
# These pin the exact DOT bytes of ipakit's authoritative to_dot goldens. The
# graph fixtures are rebuilt here (goldens live outside the repo), and the
# binding/presentation hooks mirror exactly what ipakit computes on its side:
# a render-time binding derived from the /clock/N/tier/I durable ids (the
# kernel never parses them) plus tier-name/node-id/item-label hooks.

_IPAKIT_NS = "urn:ipakit:todot:test"


def _ik(local: str) -> QualifiedName:
    return QualifiedName(_IPAKIT_NS, local)


_IK_CLOCK = _ik("clock")
_IK_TICK = _ik("tick")
_IK_GAP = _ik("gap")
_IK_TEXT = _ik("text")
_IK_DURATION = _ik("structural-duration")


def _ik_item(durable: str, text: str, duration: int) -> Item:
    return Item(
        durable,
        (
            AttributeValue(_IK_TEXT, XsdType.STRING, text),
            AttributeValue(_IK_DURATION, XsdType.INTEGER, str(duration)),
        ),
    )


def build_ipakit_graph(
    clock_raw: tuple[tuple[int, int], ...],
    segment_items: tuple[tuple[str, str, int], ...],
    boundary_items: tuple[tuple[str, str, int], ...],
) -> Graph:
    """Rebuild ipakit's authoritative containment projection for the renderer."""
    tiers = (
        Tier(
            TierDeclaration(_ik("tier-segment"), "segment"),
            tuple(_ik_item(*fields) for fields in segment_items),
        ),
        Tier(
            TierDeclaration(_ik("tier-boundary"), "boundary"),
            tuple(_ik_item(*fields) for fields in boundary_items),
        ),
        Tier(
            TierDeclaration(_IK_CLOCK, "clock"),
            tuple(Item(f"ipakit-clockcell-{i}") for i in range(len(clock_raw) - 1)),
        ),
    )
    declarations = (
        AttributeDeclaration(_IK_TICK, AttributeDomain.POSITION, XsdType.INTEGER),
        AttributeDeclaration(_IK_GAP, AttributeDomain.POSITION, XsdType.INTEGER),
        AttributeDeclaration(_IK_TEXT, AttributeDomain.ITEM, XsdType.STRING),
        AttributeDeclaration(_IK_DURATION, AttributeDomain.ITEM, XsdType.INTEGER),
    )
    positions = tuple(
        Position(
            PositionRef(_IK_CLOCK, index),
            (
                AttributeValue(_IK_TICK, XsdType.INTEGER, str(tick)),
                AttributeValue(_IK_GAP, XsdType.INTEGER, str(gap)),
            ),
        )
        for index, (tick, gap) in enumerate(clock_raw)
    )
    return Graph(
        (NamespaceDeclaration("k", _IPAKIT_NS),),
        tiers,
        (),
        attribute_declarations=declarations,
        position_values=positions,
    )


def _ipakit_node_id(durable: str) -> str:
    return "event_" + "".join(
        character if character.isalnum() else f"_{ord(character):02x}_"
        for character in durable
    )


def _durable(item: Item) -> str:
    assert item.durable_id is not None
    return item.durable_id


def ipakit_hooks_and_binding(
    graph: Graph,
) -> tuple[
    tiergraph_dot.DotPresentation,
    Callable[[Item], tuple[ClockPosition, ClockPosition]],
]:
    """Mirror ipakit's render-time hooks and clock binding from durable ids."""
    items_by_ref = {
        ItemRef(tier.declaration.name, index): item
        for tier in graph.tiers
        for index, item in enumerate(tier.items)
    }

    def attribute(item: Item, local: str) -> str:
        return next(
            value.lexical for value in item.attributes if value.name.local_name == local
        )

    def binding(item: Item) -> tuple[ClockPosition, ClockPosition]:
        tick = int(_durable(item).split("/")[2])
        duration = int(attribute(item, "structural-duration"))
        return (ClockPosition(tick, 0), ClockPosition(tick + duration, 0))

    presentation = tiergraph_dot.DotPresentation(
        tier_name=lambda tier: tier.declaration.long_name,
        node_id=lambda reference: _ipakit_node_id(_durable(items_by_ref[reference])),
        item_label=lambda item: attribute(item, "text"),
    )
    return presentation, binding


def _render_ipakit(graph: Graph) -> str:
    clock = ClockProfile.from_position_values(
        graph,
        _IK_CLOCK,
        tick_attribute=_IK_TICK,
        gap_attribute=_IK_GAP,
        collapse_shared_boundaries=True,
    )
    presentation, binding = ipakit_hooks_and_binding(graph)
    return tiergraph_dot.dumps(
        graph, clock=clock, presentation=presentation, binding=binding
    )


GOLDEN_TODOT_EMBEDDED = """digraph tiergraph {
  graph [rankdir=TB, newrank=true, ranksep="0.62 equally", nodesep=0.28, splines=line];
  node [fontname="Helvetica"];
  edge [fontname="Helvetica", fontsize=9];

  // The clock spine is the total order.
  { rank=same;
    score_start_clock [shape=plaintext, label="clock"];
    clock_0_gap_0 [shape=circle, width=0.46, fixedsize=true, group="time_0", label="0.0"];
    clock_0_gap_1 [shape=circle, width=0.46, fixedsize=true, group="time_1", label="0.1"];
    clock_1_gap_0 [shape=circle, width=0.46, fixedsize=true, group="time_2", label="1.0"];
    clock_1_gap_1 [shape=circle, width=0.46, fixedsize=true, group="time_3", label="1.1"];
    clock_1_gap_2 [shape=circle, width=0.46, fixedsize=true, group="time_4", label="1.2"];
    clock_2_gap_0 [shape=circle, width=0.46, fixedsize=true, group="time_5", label="2.0"];
    clock_2_gap_1 [shape=circle, width=0.46, fixedsize=true, group="time_6", label="2.1"];
    clock_0_gap_0 -> clock_0_gap_1 [weight=100];
    clock_0_gap_1 -> clock_1_gap_0 [weight=100];
    clock_1_gap_0 -> clock_1_gap_1 [weight=100];
    clock_1_gap_1 -> clock_1_gap_2 [weight=100];
    clock_1_gap_2 -> clock_2_gap_0 [weight=100];
    clock_2_gap_0 -> clock_2_gap_1 [weight=100];
  }

  subgraph tier_segment {
    rank=same;
    tier_label_segment [shape=plaintext, label="segment"];
    event__2f_clock_2f_0_2f_segment_2f_0 [shape=box, group="time_0", label="a"];
    guide_segment_1 [shape=point, width=0.01, label="", group="time_1", style=invis];
    event__2f_clock_2f_1_2f_segment_2f_0 [shape=box, group="time_2", label="b"];
    guide_segment_3 [shape=point, width=0.01, label="", group="time_3", style=invis];
    guide_segment_4 [shape=point, width=0.01, label="", group="time_4", style=invis];
    guide_segment_5 [shape=point, width=0.01, label="", group="time_5", style=invis];
    guide_segment_6 [shape=point, width=0.01, label="", group="time_6", style=invis];
    event__2f_clock_2f_0_2f_segment_2f_0 -> guide_segment_1 [style=invis, weight=100];
    guide_segment_1 -> event__2f_clock_2f_1_2f_segment_2f_0 [style=invis, weight=100];
    event__2f_clock_2f_1_2f_segment_2f_0 -> guide_segment_3 [style=invis, weight=100];
    guide_segment_3 -> guide_segment_4 [style=invis, weight=100];
    guide_segment_4 -> guide_segment_5 [style=invis, weight=100];
    guide_segment_5 -> guide_segment_6 [style=invis, weight=100];
    event__2f_clock_2f_0_2f_segment_2f_0 -> event__2f_clock_2f_1_2f_segment_2f_0 [color="#888888", penwidth=0.8, arrowsize=0.55, constraint=false];
    event__2f_clock_2f_0_2f_segment_2f_0 -> event__2f_clock_2f_1_2f_segment_2f_0 [xlabel="extent", color="#777777", style=dashed, arrowhead=tee, arrowsize=0.6, fontsize=8, constraint=false];
    event__2f_clock_2f_1_2f_segment_2f_0 -> guide_segment_5 [xlabel="extent", color="#777777", style=dashed, arrowhead=tee, arrowsize=0.6, fontsize=8, constraint=false];
  }

  subgraph tier_boundary {
    rank=same;
    tier_label_boundary [shape=plaintext, label="boundary"];
    event__2f_clock_2f_0_2f_boundary_2f_0 [shape=box, group="time_0", label="#"];
    guide_boundary_1 [shape=point, width=0.01, label="", group="time_1", style=invis];
    event__2f_clock_2f_1_2f_boundary_2f_0 [shape=box, group="time_2", label="."];
    event__2f_clock_2f_1_2f_boundary_2f_1 [shape=box, group="time_2", label="."];
    guide_boundary_3 [shape=point, width=0.01, label="", group="time_3", style=invis];
    guide_boundary_4 [shape=point, width=0.01, label="", group="time_4", style=invis];
    event__2f_clock_2f_2_2f_boundary_2f_0 [shape=box, group="time_5", label="#"];
    guide_boundary_6 [shape=point, width=0.01, label="", group="time_6", style=invis];
    event__2f_clock_2f_0_2f_boundary_2f_0 -> guide_boundary_1 [style=invis, weight=100];
    guide_boundary_1 -> event__2f_clock_2f_1_2f_boundary_2f_0 [style=invis, weight=100];
    event__2f_clock_2f_1_2f_boundary_2f_0 -> guide_boundary_3 [style=invis, weight=100];
    guide_boundary_3 -> guide_boundary_4 [style=invis, weight=100];
    guide_boundary_4 -> event__2f_clock_2f_2_2f_boundary_2f_0 [style=invis, weight=100];
    event__2f_clock_2f_2_2f_boundary_2f_0 -> guide_boundary_6 [style=invis, weight=100];
    event__2f_clock_2f_0_2f_boundary_2f_0 -> event__2f_clock_2f_1_2f_boundary_2f_0 [color="#888888", penwidth=0.8, arrowsize=0.55, constraint=false];
    event__2f_clock_2f_1_2f_boundary_2f_0 -> event__2f_clock_2f_1_2f_boundary_2f_1 [color="#888888", penwidth=0.8, arrowsize=0.55, constraint=false];
    event__2f_clock_2f_1_2f_boundary_2f_1 -> event__2f_clock_2f_2_2f_boundary_2f_0 [color="#888888", penwidth=0.8, arrowsize=0.55, constraint=false];
  }

  // The score brace joins lane starts in declaration order.
  score_start_clock -> tier_label_segment [dir=none, color="#333333", penwidth=2.4, weight=100];
  tier_label_segment -> tier_label_boundary [dir=none, color="#333333", penwidth=2.4, weight=100];

  // Register every lane to the clock's time columns.
  clock_0_gap_0 -> event__2f_clock_2f_0_2f_segment_2f_0 [style=invis, weight=1000, arrowhead=none];
  event__2f_clock_2f_0_2f_segment_2f_0 -> event__2f_clock_2f_0_2f_boundary_2f_0 [style=invis, weight=1000, arrowhead=none];
  clock_0_gap_1 -> guide_segment_1 [style=invis, weight=1000, arrowhead=none];
  guide_segment_1 -> guide_boundary_1 [style=invis, weight=1000, arrowhead=none];
  clock_1_gap_0 -> event__2f_clock_2f_1_2f_segment_2f_0 [style=invis, weight=1000, arrowhead=none];
  event__2f_clock_2f_1_2f_segment_2f_0 -> event__2f_clock_2f_1_2f_boundary_2f_0 [style=invis, weight=1000, arrowhead=none];
  clock_1_gap_1 -> guide_segment_3 [style=invis, weight=1000, arrowhead=none];
  guide_segment_3 -> guide_boundary_3 [style=invis, weight=1000, arrowhead=none];
  clock_1_gap_2 -> guide_segment_4 [style=invis, weight=1000, arrowhead=none];
  guide_segment_4 -> guide_boundary_4 [style=invis, weight=1000, arrowhead=none];
  clock_2_gap_0 -> guide_segment_5 [style=invis, weight=1000, arrowhead=none];
  guide_segment_5 -> event__2f_clock_2f_2_2f_boundary_2f_0 [style=invis, weight=1000, arrowhead=none];
  clock_2_gap_1 -> guide_segment_6 [style=invis, weight=1000, arrowhead=none];
  guide_segment_6 -> guide_boundary_6 [style=invis, weight=1000, arrowhead=none];

  // Trigger every event from the clock position it occupies.
  clock_0_gap_0 -> event__2f_clock_2f_0_2f_segment_2f_0 [color="#2f6f9f", penwidth=1.35, arrowsize=0.65, weight=100];
  clock_0_gap_0 -> event__2f_clock_2f_0_2f_boundary_2f_0 [color="#2f6f9f", penwidth=1.35, arrowsize=0.65, weight=100];
  clock_1_gap_0 -> event__2f_clock_2f_1_2f_segment_2f_0 [color="#2f6f9f", penwidth=1.35, arrowsize=0.65, weight=100];
  clock_1_gap_0 -> event__2f_clock_2f_1_2f_boundary_2f_0 [color="#2f6f9f", penwidth=1.35, arrowsize=0.65, weight=100];
  clock_1_gap_0 -> event__2f_clock_2f_1_2f_boundary_2f_1 [color="#2f6f9f", penwidth=1.35, arrowsize=0.65, weight=100];
  clock_2_gap_0 -> event__2f_clock_2f_2_2f_boundary_2f_0 [color="#2f6f9f", penwidth=1.35, arrowsize=0.65, weight=100];
}
"""

GOLDEN_TODOT_KAT = """digraph tiergraph {
  graph [rankdir=TB, newrank=true, ranksep="0.62 equally", nodesep=0.28, splines=line];
  node [fontname="Helvetica"];
  edge [fontname="Helvetica", fontsize=9];

  // The clock spine is the total order.
  { rank=same;
    score_start_clock [shape=plaintext, label="clock"];
    clock_0 [shape=circle, width=0.46, fixedsize=true, group="time_0", label="0"];
    clock_1 [shape=circle, width=0.46, fixedsize=true, group="time_1", label="1"];
    clock_2 [shape=circle, width=0.46, fixedsize=true, group="time_2", label="2"];
    clock_3 [shape=circle, width=0.46, fixedsize=true, group="time_3", label="3"];
    clock_0 -> clock_1 [weight=100];
    clock_1 -> clock_2 [weight=100];
    clock_2 -> clock_3 [weight=100];
  }

  subgraph tier_segment {
    rank=same;
    tier_label_segment [shape=plaintext, label="segment"];
    event__2f_clock_2f_0_2f_segment_2f_0 [shape=box, group="time_0", label="k"];
    event__2f_clock_2f_1_2f_segment_2f_0 [shape=box, group="time_1", label="a"];
    event__2f_clock_2f_2_2f_segment_2f_0 [shape=box, group="time_2", label="t"];
    guide_segment_3 [shape=point, width=0.01, label="", group="time_3", style=invis];
    event__2f_clock_2f_0_2f_segment_2f_0 -> event__2f_clock_2f_1_2f_segment_2f_0 [style=invis, weight=100];
    event__2f_clock_2f_1_2f_segment_2f_0 -> event__2f_clock_2f_2_2f_segment_2f_0 [style=invis, weight=100];
    event__2f_clock_2f_2_2f_segment_2f_0 -> guide_segment_3 [style=invis, weight=100];
    event__2f_clock_2f_0_2f_segment_2f_0 -> event__2f_clock_2f_1_2f_segment_2f_0 [color="#888888", penwidth=0.8, arrowsize=0.55, constraint=false];
    event__2f_clock_2f_1_2f_segment_2f_0 -> event__2f_clock_2f_2_2f_segment_2f_0 [color="#888888", penwidth=0.8, arrowsize=0.55, constraint=false];
    event__2f_clock_2f_0_2f_segment_2f_0 -> event__2f_clock_2f_1_2f_segment_2f_0 [xlabel="extent", color="#777777", style=dashed, arrowhead=tee, arrowsize=0.6, fontsize=8, constraint=false];
    event__2f_clock_2f_1_2f_segment_2f_0 -> event__2f_clock_2f_2_2f_segment_2f_0 [xlabel="extent", color="#777777", style=dashed, arrowhead=tee, arrowsize=0.6, fontsize=8, constraint=false];
    event__2f_clock_2f_2_2f_segment_2f_0 -> guide_segment_3 [xlabel="extent", color="#777777", style=dashed, arrowhead=tee, arrowsize=0.6, fontsize=8, constraint=false];
  }

  // The score brace joins lane starts in declaration order.
  score_start_clock -> tier_label_segment [dir=none, color="#333333", penwidth=2.4, weight=100];

  // Register every lane to the clock's time columns.
  clock_0 -> event__2f_clock_2f_0_2f_segment_2f_0 [style=invis, weight=1000, arrowhead=none];
  clock_1 -> event__2f_clock_2f_1_2f_segment_2f_0 [style=invis, weight=1000, arrowhead=none];
  clock_2 -> event__2f_clock_2f_2_2f_segment_2f_0 [style=invis, weight=1000, arrowhead=none];
  clock_3 -> guide_segment_3 [style=invis, weight=1000, arrowhead=none];

  // Trigger every event from the clock position it occupies.
  clock_0 -> event__2f_clock_2f_0_2f_segment_2f_0 [color="#2f6f9f", penwidth=1.35, arrowsize=0.65, weight=100];
  clock_1 -> event__2f_clock_2f_1_2f_segment_2f_0 [color="#2f6f9f", penwidth=1.35, arrowsize=0.65, weight=100];
  clock_2 -> event__2f_clock_2f_2_2f_segment_2f_0 [color="#2f6f9f", penwidth=1.35, arrowsize=0.65, weight=100];
}
"""


def test_occupied_spine_reproduces_ipakit_embedded_golden() -> None:
    """dumps() reproduces ipakit's '#a..b#' authoritative to_dot byte-for-byte."""
    graph = build_ipakit_graph(
        (
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
        ),
        (("/clock/0/segment/0", "a", 1), ("/clock/1/segment/0", "b", 1)),
        (
            ("/clock/0/boundary/0", "#", 0),
            ("/clock/1/boundary/0", ".", 0),
            ("/clock/1/boundary/1", ".", 0),
            ("/clock/2/boundary/0", "#", 0),
        ),
    )
    rendered = _render_ipakit(graph)
    assert rendered == GOLDEN_TODOT_EMBEDDED
    assert_graphviz_accepts(rendered)


def test_occupied_spine_reproduces_ipakit_kat_golden() -> None:
    """dumps() reproduces ipakit's 'kat' authoritative to_dot byte-for-byte."""
    graph = build_ipakit_graph(
        ((0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1), (3, 0), (3, 1)),
        (
            ("/clock/0/segment/0", "k", 1),
            ("/clock/1/segment/0", "a", 1),
            ("/clock/2/segment/0", "t", 1),
        ),
        (),
    )
    rendered = _render_ipakit(graph)
    assert rendered == GOLDEN_TODOT_KAT
    assert_graphviz_accepts(rendered)


def test_occupied_spine_needs_a_binding_for_timed_items() -> None:
    """A structural clock with placed items but no binding is refused clearly."""
    graph = build_ipakit_graph(
        ((0, 0), (0, 1), (1, 0), (1, 1)),
        (("/clock/0/segment/0", "k", 1),),
        (),
    )
    clock = ClockProfile.from_position_values(
        graph,
        _IK_CLOCK,
        tick_attribute=_IK_TICK,
        gap_attribute=_IK_GAP,
        collapse_shared_boundaries=True,
    )
    with pytest.raises(ValueError, match="binding callable returned None"):
        tiergraph_dot.dumps(graph, clock=clock)


def test_occupied_spine_binding_type_is_checked() -> None:
    """A non-callable binding argument is refused by type."""
    graph = build_ipakit_graph(((0, 0), (0, 1)), (), ())
    with pytest.raises(TypeError, match="binding must be a callable"):
        tiergraph_dot.dumps(graph, binding="nope")  # type: ignore[arg-type]


def test_occupied_spine_placement_outside_spine_is_refused() -> None:
    """A binding naming a non-occupied clock position is refused, item named."""
    graph = build_ipakit_graph(
        ((0, 0), (0, 1), (1, 0), (1, 1)),
        (("/clock/0/segment/0", "k", 1),),
        (),
    )
    clock = ClockProfile.from_position_values(
        graph,
        _IK_CLOCK,
        tick_attribute=_IK_TICK,
        gap_attribute=_IK_GAP,
        collapse_shared_boundaries=True,
    )
    presentation, _ = ipakit_hooks_and_binding(graph)

    def bad_binding(item: Item) -> tuple[ClockPosition, ClockPosition]:
        return (ClockPosition(9, 9), ClockPosition(9, 9))

    with pytest.raises(ValueError, match="not an occupied spine position"):
        tiergraph_dot.dumps(
            graph, clock=clock, presentation=presentation, binding=bad_binding
        )
