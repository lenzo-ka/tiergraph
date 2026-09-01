"""Pin the generic DOT renderer and its declared ordering."""

from __future__ import annotations

import hashlib
import re
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
    Boundary,
    BoundaryRef,
    BoundarySide,
    ClockCoordinate,
    ClockProfile,
    DurableBoundaryRef,
    Graph,
    Item,
    ItemRef,
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
        AttributeDeclaration(tick, AttributeDomain.BOUNDARY, XsdType.INTEGER),
        AttributeDeclaration(gap, AttributeDomain.BOUNDARY, XsdType.INTEGER),
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
    boundaries = tuple(
        Boundary(
            BoundaryRef(clock_name, index),
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
        boundary_values=boundaries,
        attributes=(AttributeValue(unit, XsdType.STRING, "s"),),
    )
    bindings = tuple(
        RelationInstance(
            binding,
            anchored_boundary(bare, BoundaryRef(segment, source)),
            anchored_boundary(bare, BoundaryRef(clock_name, target)),
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
                DurableBoundaryRef(empty, BoundarySide.BEFORE),
                DurableBoundaryRef(empty, BoundarySide.AFTER),
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
    # The wire writer now refuses these graphs too; this fixture specifically
    # preserves the DOT renderer's independent, deliberately stricter refusal.
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

    # The presentation overrides render through the same boundary and name
    # their own fields.  Both were outside a test claiming each structural
    # rendered string surface, and an override is exactly where a caller's own
    # text reaches the renderer.
    clean = Graph(
        (NamespaceDeclaration("d", NS),),
        (Tier(TierDeclaration(name("tier"), "Tier"), (Item("item"),)),),
        (SimpleRelationDeclaration(name("members"), name("tier"), item_type),),
    )
    assert tiergraph_dot.dumps(clean)
    with pytest.raises(ValueError, match="item label"):
        tiergraph_dot.dumps(
            clean,
            presentation=tiergraph_dot.DotPresentation(
                item_label=lambda _item, _tier: "bad\x00label"
            ),
        )
    with pytest.raises(ValueError, match="tier name"):
        tiergraph_dot.dumps(
            clean,
            presentation=tiergraph_dot.DotPresentation(
                tier_name=lambda _tier: "bad\x00tier-name"
            ),
        )


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
        item_label=lambda _item, _tier: None,
    )
    assert tiergraph_dot.dumps(graph, clock=profile, presentation=all_none) == baseline


def test_presentation_overrides_are_applied_without_dangling_ids() -> None:
    """Custom tier names, node ids, and labels reach every reference site."""
    graph, profile = graph_and_clock()
    presentation = tiergraph_dot.DotPresentation(
        tier_name=lambda tier: f"T:{tier.declaration.short_name}",
        node_id=lambda reference: f"N_{reference.tier.local_name}_{reference.index}",
        item_label=lambda item, _tier: item.durable_id or "item",
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
    """Build a clock-only graph with tick/gap boundary values, no relations."""
    clock, tick, gap = name("clock"), name("tick"), name("gap")
    tiers = (
        Tier(
            TierDeclaration(clock, "clock"),
            tuple(Item(f"cell-{index}") for index in range(len(raw) - 1)),
        ),
    )
    attributes = (
        AttributeDeclaration(tick, AttributeDomain.BOUNDARY, XsdType.INTEGER),
        AttributeDeclaration(gap, AttributeDomain.BOUNDARY, XsdType.INTEGER),
    )
    boundaries = tuple(
        Boundary(
            BoundaryRef(clock, index),
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
        boundary_values=boundaries,
    )


def _structural_spine(raw: tuple[tuple[int, int], ...]) -> str:
    graph = clock_only_graph(raw)
    clock = ClockProfile.from_boundary_values(
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


def test_structural_spine_matches_reference_embedded_golden() -> None:
    """The collapsed spine reproduces the '#a..b#' golden byte-for-byte."""
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


def test_structural_spine_matches_reference_kat_golden() -> None:
    """The collapsed spine reproduces the 'kat' golden byte-for-byte."""
    raw = ((0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1), (3, 0), (3, 1))
    assert _structural_spine(raw) == _KAT_SPINE


def test_structural_spine_draws_without_raising_and_graphviz_accepts() -> None:
    """A structural clock renders the spine cleanly on a clock-only graph."""
    graph = clock_only_graph(((0, 0), (0, 1), (1, 0)))
    clock = ClockProfile.from_boundary_values(
        graph, name("clock"), tick_attribute=name("tick"), gap_attribute=name("gap")
    )
    rendered = tiergraph_dot.dumps(graph, clock=clock)
    assert "  // The clock spine is the total order." in rendered
    assert_graphviz_accepts(rendered)


# --- Occupied-spine full goldens + anchor model (Deliverable B) -------------
#
# These pin the exact DOT bytes of the four authoritative to_dot goldens
# (flat, kat, interval, degenerate). The graph fixtures are rebuilt here
# (goldens live outside the repo), and the binding/presentation hooks mirror
# exactly what the fixture computes: a render-time binding derived from the
# /clock/N/tier/I durable ids and, for interval tiers, the span-start/span-end
# attributes (the kernel parses none of these); plus tier-name, node-id, and
# item-label(item, tier) hooks. The item-label hook falls back to the tier's
# long name when an item has no text attribute (syllable/mora), exercising the
# widened call shape.

_FIXTURE_NS = "urn:tiergraph:todot:test"


def _ik(local: str) -> QualifiedName:
    return QualifiedName(_FIXTURE_NS, local)


_IK_CLOCK = _ik("clock")
_IK_TICK = _ik("tick")
_IK_GAP = _ik("gap")
_IK_TEXT = _ik("text")
_IK_DURATION = _ik("structural-duration")
_IK_SPAN_START = _ik("span-start")
_IK_SPAN_END = _ik("span-end")


def _ik_item(
    durable: str,
    *,
    text: str | None = None,
    duration: int | None = None,
    span: tuple[tuple[int, int], tuple[int, int]] | None = None,
) -> Item:
    attributes: list[AttributeValue] = []
    if text is not None:
        attributes.append(AttributeValue(_IK_TEXT, XsdType.STRING, text))
    if duration is not None:
        attributes.append(AttributeValue(_IK_DURATION, XsdType.INTEGER, str(duration)))
    if span is not None:
        (start_tick, start_gap), (end_tick, end_gap) = span
        attributes.append(
            AttributeValue(
                _IK_SPAN_START, XsdType.STRING, f"/clock/{start_tick}/gaps/{start_gap}"
            )
        )
        attributes.append(
            AttributeValue(
                _IK_SPAN_END, XsdType.STRING, f"/clock/{end_tick}/gaps/{end_gap}"
            )
        )
    return Item(durable, tuple(attributes))


def build_fixture_graph(
    clock_raw: tuple[tuple[int, int], ...],
    tier_specs: tuple[tuple[str, tuple[Item, ...]], ...],
) -> Graph:
    """Rebuild a containment projection with named tiers and a clock tier."""
    tiers = tuple(
        Tier(TierDeclaration(_ik(f"tier-{index}"), long_name), items)
        for index, (long_name, items) in enumerate(tier_specs)
    ) + (
        Tier(
            TierDeclaration(_IK_CLOCK, "clock"),
            tuple(Item(f"fixture-clockcell-{i}") for i in range(len(clock_raw) - 1)),
        ),
    )
    declarations = (
        AttributeDeclaration(_IK_TICK, AttributeDomain.BOUNDARY, XsdType.INTEGER),
        AttributeDeclaration(_IK_GAP, AttributeDomain.BOUNDARY, XsdType.INTEGER),
        AttributeDeclaration(_IK_TEXT, AttributeDomain.ITEM, XsdType.STRING),
        AttributeDeclaration(_IK_DURATION, AttributeDomain.ITEM, XsdType.INTEGER),
        AttributeDeclaration(_IK_SPAN_START, AttributeDomain.ITEM, XsdType.STRING),
        AttributeDeclaration(_IK_SPAN_END, AttributeDomain.ITEM, XsdType.STRING),
    )
    boundaries = tuple(
        Boundary(
            BoundaryRef(_IK_CLOCK, index),
            (
                AttributeValue(_IK_TICK, XsdType.INTEGER, str(tick)),
                AttributeValue(_IK_GAP, XsdType.INTEGER, str(gap)),
            ),
        )
        for index, (tick, gap) in enumerate(clock_raw)
    )
    return Graph(
        (NamespaceDeclaration("k", _FIXTURE_NS),),
        tiers,
        (),
        attribute_declarations=declarations,
        boundary_values=boundaries,
    )


def _fixture_node_id(durable: str) -> str:
    return "event_" + "".join(
        character if character.isalnum() else f"_{ord(character):02x}_"
        for character in durable
    )


def _durable(item: Item) -> str:
    assert item.durable_id is not None
    return item.durable_id


def _attr(item: Item, local: str) -> str | None:
    for value in item.attributes:
        if value.name.local_name == local:
            return value.lexical
    return None


def fixture_hooks_and_binding(
    graph: Graph,
) -> tuple[
    tiergraph_dot.DotPresentation,
    Callable[[Item], tuple[ClockCoordinate, ClockCoordinate]],
]:
    """Mirror the fixture's render-time hooks and clock binding.

    Placement comes from span-start/span-end (``/clock/<tick>/gaps/<gap>``) for
    interval tiers and from the durable-id tick plus structural-duration for
    flat tiers; the item label falls back to the tier long name when the item
    carries no text. ``contains-*`` polyadics render as bipartite parent-child
    edges labeled ``contains``.
    """
    items_by_ref = {
        ItemRef(tier.declaration.name, index): item
        for tier in graph.tiers
        for index, item in enumerate(tier.items)
    }

    def parse_ref(reference: str) -> ClockCoordinate:
        parts = reference.split("/")
        return ClockCoordinate(int(parts[2]), int(parts[4]))

    def binding(item: Item) -> tuple[ClockCoordinate, ClockCoordinate]:
        span_start = _attr(item, "span-start")
        span_end = _attr(item, "span-end")
        if span_start is not None and span_end is not None:
            return (parse_ref(span_start), parse_ref(span_end))
        tick = int(_durable(item).split("/")[2])
        duration = int(_attr(item, "structural-duration") or 0)
        return (ClockCoordinate(tick, 0), ClockCoordinate(tick + duration, 0))

    def item_label(item: Item, tier: Tier) -> str:
        text = _attr(item, "text")
        return text if text is not None else tier.declaration.long_name

    def relation_name(relation: PolyadicRelationInstance) -> str:
        return re.sub(r"-\d+$", "", relation.declaration.local_name)

    def relation_style(relation: PolyadicRelationInstance) -> str | None:
        if relation.declaration.local_name.startswith("contains"):
            return "bipartite"
        return None

    presentation = tiergraph_dot.DotPresentation(
        tier_name=lambda tier: tier.declaration.long_name,
        node_id=lambda reference: _fixture_node_id(_durable(items_by_ref[reference])),
        item_label=item_label,
        relation_name=relation_name,
        relation_style=relation_style,
    )
    return presentation, binding


def _render_fixture(graph: Graph) -> str:
    clock = ClockProfile.from_boundary_values(
        graph,
        _IK_CLOCK,
        tick_attribute=_IK_TICK,
        gap_attribute=_IK_GAP,
        collapse_shared_boundaries=True,
    )
    presentation, binding = fixture_hooks_and_binding(graph)
    return tiergraph_dot.dumps(
        graph, clock=clock, presentation=presentation, binding=binding
    )


def _embedded_graph() -> Graph:
    return build_fixture_graph(
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
        (
            (
                "segment",
                (
                    _ik_item("/clock/0/segment/0", text="a", duration=1),
                    _ik_item("/clock/1/segment/0", text="b", duration=1),
                ),
            ),
            (
                "boundary",
                (
                    _ik_item("/clock/0/boundary/0", text="#", duration=0),
                    _ik_item("/clock/1/boundary/0", text=".", duration=0),
                    _ik_item("/clock/1/boundary/1", text=".", duration=0),
                    _ik_item("/clock/2/boundary/0", text="#", duration=0),
                ),
            ),
        ),
    )


def _kat_graph() -> Graph:
    return build_fixture_graph(
        ((0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1), (3, 0), (3, 1)),
        (
            (
                "segment",
                (
                    _ik_item("/clock/0/segment/0", text="k", duration=1),
                    _ik_item("/clock/1/segment/0", text="a", duration=1),
                    _ik_item("/clock/2/segment/0", text="t", duration=1),
                ),
            ),
        ),
    )


def _interval_graph() -> Graph:
    return build_fixture_graph(
        ((0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1), (3, 0), (3, 1)),
        (
            ("syllable", (_ik_item("/clock/0/syllable/0", span=((0, 0), (3, 0))),)),
            ("mora", (_ik_item("/clock/1/mora/0", span=((1, 0), (3, 0))),)),
            (
                "segment",
                (
                    _ik_item("/clock/0/segment/0", text="k", duration=1),
                    _ik_item("/clock/1/segment/0", text="\u00e6", duration=1),
                    _ik_item("/clock/2/segment/0", text="t", duration=1),
                ),
            ),
        ),
    )


def _degenerate_graph() -> Graph:
    return build_fixture_graph(
        ((0, 0), (0, 1), (0, 2), (0, 3)),
        (
            (
                "boundary",
                (
                    _ik_item("/clock/0/boundary/0", text="#", duration=0),
                    _ik_item("/clock/0/boundary/1", text="#", duration=0),
                ),
            ),
        ),
    )


def _held_graph() -> Graph:
    """A held syllable pair plus a gap-refined mora, over the flat '#a..b#' base."""
    return build_fixture_graph(
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
        (
            (
                "syllable",
                (
                    _ik_item("/clock/0/syllable/0", span=((0, 0), (1, 1))),
                    _ik_item("/clock/0/syllable/1", span=((0, 0), (1, 1))),
                ),
            ),
            ("mora", (_ik_item("/clock/0/mora/0", span=((0, 1), (1, 2))),)),
            (
                "segment",
                (
                    _ik_item("/clock/0/segment/0", text="a", duration=1),
                    _ik_item("/clock/1/segment/0", text="b", duration=1),
                ),
            ),
            (
                "boundary",
                (
                    _ik_item("/clock/0/boundary/0", text="#", duration=0),
                    _ik_item("/clock/1/boundary/0", text=".", duration=0),
                    _ik_item("/clock/1/boundary/1", text=".", duration=0),
                    _ik_item("/clock/2/boundary/0", text="#", duration=0),
                ),
            ),
        ),
    )


def _hierarchy_graph() -> Graph:
    """An utterance containing three segments via a bipartite-styled polyadic.

    The fixture derives the utterance's clock extent from its containment; the test
    supplies it directly as a span, since the renderer consumes only the
    binding's (start, end). ``roots`` (empty sources) is present and omitted.
    """
    base = build_fixture_graph(
        ((0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1), (3, 0), (3, 1)),
        (
            ("utterance", (_ik_item("/clock/0/utterance/0", span=((0, 0), (3, 0))),)),
            (
                "segment",
                (
                    _ik_item("/clock/0/segment/0", text="k", duration=1),
                    _ik_item("/clock/1/segment/0", text="æ", duration=1),
                    _ik_item("/clock/2/segment/0", text="t", duration=1),
                ),
            ),
        ),
    )
    utterance, segment = _ik("tier-0"), _ik("tier-1")
    contains, roots = _ik("contains-0"), _ik("roots")
    roots_sources = RelationSideDeclaration(
        (RelationEndpointKind.ITEM,),
        (utterance,),
        minimum=0,
        maximum=0,
        allow_empty=True,
    )
    roots_targets = RelationSideDeclaration(
        (RelationEndpointKind.ITEM,),
        (utterance,),
        allow_empty=True,
    )
    return replace(
        base,
        relation_declarations=(
            *base.relation_declarations,
            PolyadicRelationDeclaration(
                contains,
                RelationSideDeclaration((RelationEndpointKind.ITEM,), (utterance,)),
                RelationSideDeclaration((RelationEndpointKind.ITEM,), (segment,)),
            ),
            PolyadicRelationDeclaration(roots, roots_sources, roots_targets),
        ),
        polyadic_relations=(
            PolyadicRelationInstance(
                contains,
                (ItemRef(utterance, 0),),
                (ItemRef(segment, 0), ItemRef(segment, 1), ItemRef(segment, 2)),
            ),
            PolyadicRelationInstance(roots, (), (ItemRef(utterance, 0),)),
        ),
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

GOLDEN_TODOT_INTERVAL = """digraph tiergraph {
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

  subgraph tier_syllable {
    rank=same;
    tier_label_syllable [shape=plaintext, label="syllable"];
    event__2f_clock_2f_0_2f_syllable_2f_0 [shape=box, group="time_0", label="syllable"];
    guide_syllable_1 [shape=point, width=0.01, label="", group="time_1", style=invis];
    guide_syllable_2 [shape=point, width=0.01, label="", group="time_2", style=invis];
    guide_syllable_3 [shape=point, width=0.01, label="", group="time_3", style=invis];
    event__2f_clock_2f_0_2f_syllable_2f_0 -> guide_syllable_1 [style=invis, weight=100];
    guide_syllable_1 -> guide_syllable_2 [style=invis, weight=100];
    guide_syllable_2 -> guide_syllable_3 [style=invis, weight=100];
    event__2f_clock_2f_0_2f_syllable_2f_0 -> guide_syllable_3 [xlabel="extent", color="#777777", style=dashed, arrowhead=tee, arrowsize=0.6, fontsize=8, constraint=false];
  }

  subgraph tier_mora {
    rank=same;
    tier_label_mora [shape=plaintext, label="mora"];
    guide_mora_0 [shape=point, width=0.01, label="", group="time_0", style=invis];
    event__2f_clock_2f_1_2f_mora_2f_0 [shape=box, group="time_1", label="mora"];
    guide_mora_2 [shape=point, width=0.01, label="", group="time_2", style=invis];
    guide_mora_3 [shape=point, width=0.01, label="", group="time_3", style=invis];
    guide_mora_0 -> event__2f_clock_2f_1_2f_mora_2f_0 [style=invis, weight=100];
    event__2f_clock_2f_1_2f_mora_2f_0 -> guide_mora_2 [style=invis, weight=100];
    guide_mora_2 -> guide_mora_3 [style=invis, weight=100];
    event__2f_clock_2f_1_2f_mora_2f_0 -> guide_mora_3 [xlabel="extent", color="#777777", style=dashed, arrowhead=tee, arrowsize=0.6, fontsize=8, constraint=false];
  }

  subgraph tier_segment {
    rank=same;
    tier_label_segment [shape=plaintext, label="segment"];
    event__2f_clock_2f_0_2f_segment_2f_0 [shape=box, group="time_0", label="k"];
    event__2f_clock_2f_1_2f_segment_2f_0 [shape=box, group="time_1", label="æ"];
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
  score_start_clock -> tier_label_syllable [dir=none, color="#333333", penwidth=2.4, weight=100];
  tier_label_syllable -> tier_label_mora [dir=none, color="#333333", penwidth=2.4, weight=100];
  tier_label_mora -> tier_label_segment [dir=none, color="#333333", penwidth=2.4, weight=100];

  // Register every lane to the clock's time columns.
  clock_0 -> event__2f_clock_2f_0_2f_syllable_2f_0 [style=invis, weight=1000, arrowhead=none];
  event__2f_clock_2f_0_2f_syllable_2f_0 -> guide_mora_0 [style=invis, weight=1000, arrowhead=none];
  guide_mora_0 -> event__2f_clock_2f_0_2f_segment_2f_0 [style=invis, weight=1000, arrowhead=none];
  clock_1 -> guide_syllable_1 [style=invis, weight=1000, arrowhead=none];
  guide_syllable_1 -> event__2f_clock_2f_1_2f_mora_2f_0 [style=invis, weight=1000, arrowhead=none];
  event__2f_clock_2f_1_2f_mora_2f_0 -> event__2f_clock_2f_1_2f_segment_2f_0 [style=invis, weight=1000, arrowhead=none];
  clock_2 -> guide_syllable_2 [style=invis, weight=1000, arrowhead=none];
  guide_syllable_2 -> guide_mora_2 [style=invis, weight=1000, arrowhead=none];
  guide_mora_2 -> event__2f_clock_2f_2_2f_segment_2f_0 [style=invis, weight=1000, arrowhead=none];
  clock_3 -> guide_syllable_3 [style=invis, weight=1000, arrowhead=none];
  guide_syllable_3 -> guide_mora_3 [style=invis, weight=1000, arrowhead=none];
  guide_mora_3 -> guide_segment_3 [style=invis, weight=1000, arrowhead=none];

  // Trigger every event from the clock position it occupies.
  clock_0 -> event__2f_clock_2f_0_2f_syllable_2f_0 [color="#2f6f9f", penwidth=1.35, arrowsize=0.65, weight=100];
  clock_0 -> event__2f_clock_2f_0_2f_segment_2f_0 [color="#2f6f9f", penwidth=1.35, arrowsize=0.65, weight=100];
  clock_1 -> event__2f_clock_2f_1_2f_mora_2f_0 [color="#2f6f9f", penwidth=1.35, arrowsize=0.65, weight=100];
  clock_1 -> event__2f_clock_2f_1_2f_segment_2f_0 [color="#2f6f9f", penwidth=1.35, arrowsize=0.65, weight=100];
  clock_2 -> event__2f_clock_2f_2_2f_segment_2f_0 [color="#2f6f9f", penwidth=1.35, arrowsize=0.65, weight=100];
}
"""

GOLDEN_TODOT_DEGENERATE = """digraph tiergraph {
  graph [rankdir=TB, newrank=true, ranksep="0.62 equally", nodesep=0.28, splines=line];
  node [fontname="Helvetica"];
  edge [fontname="Helvetica", fontsize=9];

  // The clock spine is the total order.
  { rank=same;
    score_start_clock [shape=plaintext, label="clock"];
    clock_0_gap_0 [shape=circle, width=0.46, fixedsize=true, group="time_0", label="0.0"];
    clock_0_gap_1 [shape=circle, width=0.46, fixedsize=true, group="time_1", label="0.1"];
    clock_0_gap_2 [shape=circle, width=0.46, fixedsize=true, group="time_2", label="0.2"];
    clock_0_gap_0 -> clock_0_gap_1 [weight=100];
    clock_0_gap_1 -> clock_0_gap_2 [weight=100];
  }

  subgraph tier_boundary {
    rank=same;
    tier_label_boundary [shape=plaintext, label="boundary"];
    event__2f_clock_2f_0_2f_boundary_2f_0 [shape=box, group="time_0", label="#"];
    event__2f_clock_2f_0_2f_boundary_2f_1 [shape=box, group="time_0", label="#"];
    guide_boundary_1 [shape=point, width=0.01, label="", group="time_1", style=invis];
    guide_boundary_2 [shape=point, width=0.01, label="", group="time_2", style=invis];
    event__2f_clock_2f_0_2f_boundary_2f_0 -> guide_boundary_1 [style=invis, weight=100];
    guide_boundary_1 -> guide_boundary_2 [style=invis, weight=100];
    event__2f_clock_2f_0_2f_boundary_2f_0 -> event__2f_clock_2f_0_2f_boundary_2f_1 [color="#888888", penwidth=0.8, arrowsize=0.55, constraint=false];
  }

  // The score brace joins lane starts in declaration order.
  score_start_clock -> tier_label_boundary [dir=none, color="#333333", penwidth=2.4, weight=100];

  // Register every lane to the clock's time columns.
  clock_0_gap_0 -> event__2f_clock_2f_0_2f_boundary_2f_0 [style=invis, weight=1000, arrowhead=none];
  clock_0_gap_1 -> guide_boundary_1 [style=invis, weight=1000, arrowhead=none];
  clock_0_gap_2 -> guide_boundary_2 [style=invis, weight=1000, arrowhead=none];

  // Trigger every event from the clock position it occupies.
  clock_0_gap_0 -> event__2f_clock_2f_0_2f_boundary_2f_0 [color="#2f6f9f", penwidth=1.35, arrowsize=0.65, weight=100];
  clock_0_gap_0 -> event__2f_clock_2f_0_2f_boundary_2f_1 [color="#2f6f9f", penwidth=1.35, arrowsize=0.65, weight=100];
}
"""


def test_occupied_spine_reproduces_reference_embedded_golden() -> None:
    """dumps() reproduces the flat '#a..b#' golden byte-for-byte."""
    rendered = _render_fixture(_embedded_graph())
    assert rendered == GOLDEN_TODOT_EMBEDDED
    assert_graphviz_accepts(rendered)


def test_occupied_spine_reproduces_reference_kat_golden() -> None:
    """dumps() reproduces the 'kat' golden byte-for-byte (collapsed==1)."""
    rendered = _render_fixture(_kat_graph())
    assert rendered == GOLDEN_TODOT_KAT
    assert_graphviz_accepts(rendered)


def test_occupied_spine_reproduces_reference_interval_golden() -> None:
    """dumps() reproduces the interval golden: span placement + co-location."""
    rendered = _render_fixture(_interval_graph())
    assert rendered == GOLDEN_TODOT_INTERVAL
    assert_graphviz_accepts(rendered)


def test_occupied_spine_reproduces_reference_degenerate_golden() -> None:
    """dumps() reproduces the degenerate '##' golden: same-tier co-location."""
    rendered = _render_fixture(_degenerate_graph())
    assert rendered == GOLDEN_TODOT_DEGENERATE
    assert_graphviz_accepts(rendered)


GOLDEN_TODOT_HELD = """digraph tiergraph {
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

  subgraph tier_syllable {
    rank=same;
    tier_label_syllable [shape=plaintext, label="syllable"];
    event__2f_clock_2f_0_2f_syllable_2f_0 [shape=box, group="time_0", label="syllable"];
    event__2f_clock_2f_0_2f_syllable_2f_1 [shape=box, group="time_0", label="syllable"];
    guide_syllable_1 [shape=point, width=0.01, label="", group="time_1", style=invis];
    guide_syllable_2 [shape=point, width=0.01, label="", group="time_2", style=invis];
    guide_syllable_3 [shape=point, width=0.01, label="", group="time_3", style=invis];
    guide_syllable_4 [shape=point, width=0.01, label="", group="time_4", style=invis];
    guide_syllable_5 [shape=point, width=0.01, label="", group="time_5", style=invis];
    guide_syllable_6 [shape=point, width=0.01, label="", group="time_6", style=invis];
    event__2f_clock_2f_0_2f_syllable_2f_0 -> guide_syllable_1 [style=invis, weight=100];
    guide_syllable_1 -> guide_syllable_2 [style=invis, weight=100];
    guide_syllable_2 -> guide_syllable_3 [style=invis, weight=100];
    guide_syllable_3 -> guide_syllable_4 [style=invis, weight=100];
    guide_syllable_4 -> guide_syllable_5 [style=invis, weight=100];
    guide_syllable_5 -> guide_syllable_6 [style=invis, weight=100];
    event__2f_clock_2f_0_2f_syllable_2f_0 -> event__2f_clock_2f_0_2f_syllable_2f_1 [color="#888888", penwidth=0.8, arrowsize=0.55, constraint=false];
    event__2f_clock_2f_0_2f_syllable_2f_0 -> guide_syllable_3 [xlabel="extent", color="#777777", style=dashed, arrowhead=tee, arrowsize=0.6, fontsize=8, constraint=false];
    event__2f_clock_2f_0_2f_syllable_2f_1 -> guide_syllable_3 [xlabel="extent", color="#777777", style=dashed, arrowhead=tee, arrowsize=0.6, fontsize=8, constraint=false];
  }

  subgraph tier_mora {
    rank=same;
    tier_label_mora [shape=plaintext, label="mora"];
    guide_mora_0 [shape=point, width=0.01, label="", group="time_0", style=invis];
    event__2f_clock_2f_0_2f_mora_2f_0 [shape=box, group="time_1", label="mora"];
    guide_mora_2 [shape=point, width=0.01, label="", group="time_2", style=invis];
    guide_mora_3 [shape=point, width=0.01, label="", group="time_3", style=invis];
    guide_mora_4 [shape=point, width=0.01, label="", group="time_4", style=invis];
    guide_mora_5 [shape=point, width=0.01, label="", group="time_5", style=invis];
    guide_mora_6 [shape=point, width=0.01, label="", group="time_6", style=invis];
    guide_mora_0 -> event__2f_clock_2f_0_2f_mora_2f_0 [style=invis, weight=100];
    event__2f_clock_2f_0_2f_mora_2f_0 -> guide_mora_2 [style=invis, weight=100];
    guide_mora_2 -> guide_mora_3 [style=invis, weight=100];
    guide_mora_3 -> guide_mora_4 [style=invis, weight=100];
    guide_mora_4 -> guide_mora_5 [style=invis, weight=100];
    guide_mora_5 -> guide_mora_6 [style=invis, weight=100];
    event__2f_clock_2f_0_2f_mora_2f_0 -> guide_mora_4 [xlabel="extent", color="#777777", style=dashed, arrowhead=tee, arrowsize=0.6, fontsize=8, constraint=false];
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
  score_start_clock -> tier_label_syllable [dir=none, color="#333333", penwidth=2.4, weight=100];
  tier_label_syllable -> tier_label_mora [dir=none, color="#333333", penwidth=2.4, weight=100];
  tier_label_mora -> tier_label_segment [dir=none, color="#333333", penwidth=2.4, weight=100];
  tier_label_segment -> tier_label_boundary [dir=none, color="#333333", penwidth=2.4, weight=100];

  // Register every lane to the clock's time columns.
  clock_0_gap_0 -> event__2f_clock_2f_0_2f_syllable_2f_0 [style=invis, weight=1000, arrowhead=none];
  event__2f_clock_2f_0_2f_syllable_2f_0 -> guide_mora_0 [style=invis, weight=1000, arrowhead=none];
  guide_mora_0 -> event__2f_clock_2f_0_2f_segment_2f_0 [style=invis, weight=1000, arrowhead=none];
  event__2f_clock_2f_0_2f_segment_2f_0 -> event__2f_clock_2f_0_2f_boundary_2f_0 [style=invis, weight=1000, arrowhead=none];
  clock_0_gap_1 -> guide_syllable_1 [style=invis, weight=1000, arrowhead=none];
  guide_syllable_1 -> event__2f_clock_2f_0_2f_mora_2f_0 [style=invis, weight=1000, arrowhead=none];
  event__2f_clock_2f_0_2f_mora_2f_0 -> guide_segment_1 [style=invis, weight=1000, arrowhead=none];
  guide_segment_1 -> guide_boundary_1 [style=invis, weight=1000, arrowhead=none];
  clock_1_gap_0 -> guide_syllable_2 [style=invis, weight=1000, arrowhead=none];
  guide_syllable_2 -> guide_mora_2 [style=invis, weight=1000, arrowhead=none];
  guide_mora_2 -> event__2f_clock_2f_1_2f_segment_2f_0 [style=invis, weight=1000, arrowhead=none];
  event__2f_clock_2f_1_2f_segment_2f_0 -> event__2f_clock_2f_1_2f_boundary_2f_0 [style=invis, weight=1000, arrowhead=none];
  clock_1_gap_1 -> guide_syllable_3 [style=invis, weight=1000, arrowhead=none];
  guide_syllable_3 -> guide_mora_3 [style=invis, weight=1000, arrowhead=none];
  guide_mora_3 -> guide_segment_3 [style=invis, weight=1000, arrowhead=none];
  guide_segment_3 -> guide_boundary_3 [style=invis, weight=1000, arrowhead=none];
  clock_1_gap_2 -> guide_syllable_4 [style=invis, weight=1000, arrowhead=none];
  guide_syllable_4 -> guide_mora_4 [style=invis, weight=1000, arrowhead=none];
  guide_mora_4 -> guide_segment_4 [style=invis, weight=1000, arrowhead=none];
  guide_segment_4 -> guide_boundary_4 [style=invis, weight=1000, arrowhead=none];
  clock_2_gap_0 -> guide_syllable_5 [style=invis, weight=1000, arrowhead=none];
  guide_syllable_5 -> guide_mora_5 [style=invis, weight=1000, arrowhead=none];
  guide_mora_5 -> guide_segment_5 [style=invis, weight=1000, arrowhead=none];
  guide_segment_5 -> event__2f_clock_2f_2_2f_boundary_2f_0 [style=invis, weight=1000, arrowhead=none];
  clock_2_gap_1 -> guide_syllable_6 [style=invis, weight=1000, arrowhead=none];
  guide_syllable_6 -> guide_mora_6 [style=invis, weight=1000, arrowhead=none];
  guide_mora_6 -> guide_segment_6 [style=invis, weight=1000, arrowhead=none];
  guide_segment_6 -> guide_boundary_6 [style=invis, weight=1000, arrowhead=none];

  // Trigger every event from the clock position it occupies.
  clock_0_gap_0 -> event__2f_clock_2f_0_2f_syllable_2f_0 [color="#2f6f9f", penwidth=1.35, arrowsize=0.65, weight=100];
  clock_0_gap_0 -> event__2f_clock_2f_0_2f_syllable_2f_1 [color="#2f6f9f", penwidth=1.35, arrowsize=0.65, weight=100];
  clock_0_gap_1 -> event__2f_clock_2f_0_2f_mora_2f_0 [color="#2f6f9f", penwidth=1.35, arrowsize=0.65, weight=100];
  clock_0_gap_0 -> event__2f_clock_2f_0_2f_segment_2f_0 [color="#2f6f9f", penwidth=1.35, arrowsize=0.65, weight=100];
  clock_0_gap_0 -> event__2f_clock_2f_0_2f_boundary_2f_0 [color="#2f6f9f", penwidth=1.35, arrowsize=0.65, weight=100];
  clock_1_gap_0 -> event__2f_clock_2f_1_2f_segment_2f_0 [color="#2f6f9f", penwidth=1.35, arrowsize=0.65, weight=100];
  clock_1_gap_0 -> event__2f_clock_2f_1_2f_boundary_2f_0 [color="#2f6f9f", penwidth=1.35, arrowsize=0.65, weight=100];
  clock_1_gap_0 -> event__2f_clock_2f_1_2f_boundary_2f_1 [color="#2f6f9f", penwidth=1.35, arrowsize=0.65, weight=100];
  clock_2_gap_0 -> event__2f_clock_2f_2_2f_boundary_2f_0 [color="#2f6f9f", penwidth=1.35, arrowsize=0.65, weight=100];
}
"""


GOLDEN_TODOT_HIERARCHY = """digraph tiergraph {
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

  subgraph tier_utterance {
    rank=same;
    tier_label_utterance [shape=plaintext, label="utterance"];
    event__2f_clock_2f_0_2f_utterance_2f_0 [shape=box, group="time_0", label="utterance"];
    guide_utterance_1 [shape=point, width=0.01, label="", group="time_1", style=invis];
    guide_utterance_2 [shape=point, width=0.01, label="", group="time_2", style=invis];
    guide_utterance_3 [shape=point, width=0.01, label="", group="time_3", style=invis];
    event__2f_clock_2f_0_2f_utterance_2f_0 -> guide_utterance_1 [style=invis, weight=100];
    guide_utterance_1 -> guide_utterance_2 [style=invis, weight=100];
    guide_utterance_2 -> guide_utterance_3 [style=invis, weight=100];
    event__2f_clock_2f_0_2f_utterance_2f_0 -> guide_utterance_3 [xlabel="extent", color="#777777", style=dashed, arrowhead=tee, arrowsize=0.6, fontsize=8, constraint=false];
  }

  subgraph tier_segment {
    rank=same;
    tier_label_segment [shape=plaintext, label="segment"];
    event__2f_clock_2f_0_2f_segment_2f_0 [shape=box, group="time_0", label="k"];
    event__2f_clock_2f_1_2f_segment_2f_0 [shape=box, group="time_1", label="æ"];
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
  score_start_clock -> tier_label_utterance [dir=none, color="#333333", penwidth=2.4, weight=100];
  tier_label_utterance -> tier_label_segment [dir=none, color="#333333", penwidth=2.4, weight=100];

  // Register every lane to the clock's time columns.
  clock_0 -> event__2f_clock_2f_0_2f_utterance_2f_0 [style=invis, weight=1000, arrowhead=none];
  event__2f_clock_2f_0_2f_utterance_2f_0 -> event__2f_clock_2f_0_2f_segment_2f_0 [style=invis, weight=1000, arrowhead=none];
  clock_1 -> guide_utterance_1 [style=invis, weight=1000, arrowhead=none];
  guide_utterance_1 -> event__2f_clock_2f_1_2f_segment_2f_0 [style=invis, weight=1000, arrowhead=none];
  clock_2 -> guide_utterance_2 [style=invis, weight=1000, arrowhead=none];
  guide_utterance_2 -> event__2f_clock_2f_2_2f_segment_2f_0 [style=invis, weight=1000, arrowhead=none];
  clock_3 -> guide_utterance_3 [style=invis, weight=1000, arrowhead=none];
  guide_utterance_3 -> guide_segment_3 [style=invis, weight=1000, arrowhead=none];

  // Trigger every event from the clock position it occupies.
  clock_0 -> event__2f_clock_2f_0_2f_utterance_2f_0 [color="#2f6f9f", penwidth=1.35, arrowsize=0.65, weight=100];
  clock_0 -> event__2f_clock_2f_0_2f_segment_2f_0 [color="#2f6f9f", penwidth=1.35, arrowsize=0.65, weight=100];
  clock_1 -> event__2f_clock_2f_1_2f_segment_2f_0 [color="#2f6f9f", penwidth=1.35, arrowsize=0.65, weight=100];
  clock_2 -> event__2f_clock_2f_2_2f_segment_2f_0 [color="#2f6f9f", penwidth=1.35, arrowsize=0.65, weight=100];

  // Declared relations.
  event__2f_clock_2f_0_2f_utterance_2f_0 -> event__2f_clock_2f_0_2f_segment_2f_0 [label="contains", color="#5555aa", constraint=false];
  event__2f_clock_2f_0_2f_utterance_2f_0 -> event__2f_clock_2f_1_2f_segment_2f_0 [label="contains", color="#5555aa", constraint=false];
  event__2f_clock_2f_0_2f_utterance_2f_0 -> event__2f_clock_2f_2_2f_segment_2f_0 [label="contains", color="#5555aa", constraint=false];
}
"""


def test_occupied_spine_reproduces_reference_held_golden() -> None:
    """dumps() reproduces the held golden: gap-refined placement + ordering."""
    rendered = _render_fixture(_held_graph())
    assert rendered == GOLDEN_TODOT_HELD
    assert_graphviz_accepts(rendered)


def test_occupied_spine_reproduces_reference_hierarchy_golden() -> None:
    """dumps() reproduces the hierarchy golden: bipartite-styled containment."""
    rendered = _render_fixture(_hierarchy_graph())
    assert rendered == GOLDEN_TODOT_HIERARCHY
    assert_graphviz_accepts(rendered)


def test_trigger_edges_order_by_coarse_tick_then_declaration_then_event() -> None:
    """A gap-refined item sorts by its coarse tick, not its collapsed column."""
    rendered = _render_fixture(_held_graph())
    trigger = rendered.split(
        "// Trigger every event from the clock position it occupies."
    )[1]
    mora = _fixture_node_id("/clock/0/mora/0")
    seg0 = _fixture_node_id("/clock/0/segment/0")
    syllable0 = _fixture_node_id("/clock/0/syllable/0")
    # mora starts at (0, gap1): its edge source is the refined column clock_0_gap_1,
    # but it sorts within coarse tick 0, after syllable (decl 0) and before
    # segment (decl 7).
    assert f"clock_0_gap_1 -> {mora} [color=" in trigger
    assert trigger.index(syllable0) < trigger.index(mora) < trigger.index(seg0)


def test_bipartite_styled_polyadic_renders_as_parent_child_edges() -> None:
    """contains-* is drawn as labeled parent->child edges under one header."""
    rendered = _render_fixture(_hierarchy_graph())
    assert "// Declared relations." in rendered
    assert "// Declared polyadic relations." not in rendered
    assert "// Declared bipartite relations." not in rendered
    parent = _fixture_node_id("/clock/0/utterance/0")
    for child in ("0", "1", "2"):
        target = _fixture_node_id(f"/clock/{child}/segment/0")
        assert (
            f'{parent} -> {target} [label="contains", color="#5555aa", '
            "constraint=false];" in rendered
        )
    # The empty 'roots' polyadic emits nothing.
    assert "roots" not in rendered


def test_same_tier_colocated_items_share_one_column_anchor() -> None:
    """Two items of one tier at a tick: both drawn/adjacent/triggered, one anchor."""
    rendered = _render_fixture(_degenerate_graph())
    first = _fixture_node_id("/clock/0/boundary/0")
    second = _fixture_node_id("/clock/0/boundary/1")
    # Both occupants are defined and both are triggered from their shared column.
    assert f"{first} [shape=box" in rendered
    assert f"{second} [shape=box" in rendered
    assert f"clock_0_gap_0 -> {first} [color=" in rendered
    assert f"clock_0_gap_0 -> {second} [color=" in rendered
    # They are joined in the adjacency chain.
    assert f"{first} -> {second} [color=" in rendered
    # Only the first item anchors the invisible chains; the second never does.
    assert f"{first} -> guide_boundary_1 [style=invis" in rendered
    assert f"{second} ->" not in rendered
    assert f"-> {second} [style=invis" not in rendered


def test_structural_default_item_label_holds_without_a_hook() -> None:
    """Absent item_label under a structural clock builds a timing-free default."""
    graph = _kat_graph()
    clock = ClockProfile.from_boundary_values(
        graph,
        _IK_CLOCK,
        tick_attribute=_IK_TICK,
        gap_attribute=_IK_GAP,
        collapse_shared_boundaries=True,
    )
    _, binding = fixture_hooks_and_binding(graph)
    rendered = tiergraph_dot.dumps(graph, clock=clock, binding=binding)
    # The default label carries the durable id and attributes, no clock timing.
    assert "/clock/0/segment/0" in rendered
    assert "time=" not in rendered
    assert_graphviz_accepts(rendered)


def test_structural_raw_single_boundary_tick_is_refused() -> None:
    """A tick with one raw boundary cannot be collapsed and is refused."""
    graph = build_fixture_graph(
        ((0, 0), (1, 0), (1, 1)),
        (("segment", (_ik_item("/clock/1/segment/0", text="x", duration=1),)),),
    )
    with pytest.raises(ValueError, match="single raw boundary"):
        ClockProfile.from_boundary_values(
            graph,
            _IK_CLOCK,
            tick_attribute=_IK_TICK,
            gap_attribute=_IK_GAP,
            collapse_shared_boundaries=True,
        )


def _structural_clock(graph: Graph) -> ClockProfile:
    return ClockProfile.from_boundary_values(
        graph,
        _IK_CLOCK,
        tick_attribute=_IK_TICK,
        gap_attribute=_IK_GAP,
        collapse_shared_boundaries=True,
    )


def test_structural_missing_binding_is_refused() -> None:
    """A visible non-clock item with no binding is refused (no untimed lane)."""
    graph = _kat_graph()
    with pytest.raises(ValueError, match="no clock placement"):
        tiergraph_dot.dumps(graph, clock=_structural_clock(graph))


def test_structural_binding_returning_none_is_refused() -> None:
    """A binding returning None for a visible item is refused, item named."""
    graph = _kat_graph()

    def binding(item: Item) -> tuple[ClockCoordinate, ClockCoordinate] | None:
        return None

    with pytest.raises(ValueError, match="binding returned None"):
        tiergraph_dot.dumps(
            graph,
            clock=_structural_clock(graph),
            binding=binding,  # type: ignore[arg-type]
        )


def test_structural_reversed_span_is_refused() -> None:
    """A binding whose start column follows its end column is refused."""
    graph = _kat_graph()

    def binding(item: Item) -> tuple[ClockCoordinate, ClockCoordinate]:
        return (ClockCoordinate(2, 0), ClockCoordinate(0, 0))

    with pytest.raises(ValueError, match="reversed"):
        tiergraph_dot.dumps(graph, clock=_structural_clock(graph), binding=binding)


def test_structural_malformed_binding_is_refused() -> None:
    """A binding returning a non-pair or non-ClockCoordinates is refused."""
    graph = _kat_graph()

    def not_a_pair(item: Item) -> object:
        return "nope"

    with pytest.raises(ValueError, match="must return"):
        tiergraph_dot.dumps(
            graph,
            clock=_structural_clock(graph),
            binding=not_a_pair,  # type: ignore[arg-type]
        )

    def wrong_types(item: Item) -> object:
        return (0, 3)

    with pytest.raises(ValueError, match="must return ClockCoordinates"):
        tiergraph_dot.dumps(
            graph,
            clock=_structural_clock(graph),
            binding=wrong_types,  # type: ignore[arg-type]
        )


def test_structural_off_spine_placement_is_refused() -> None:
    """A binding naming a non-occupied clock boundary is refused, item named."""
    graph = _kat_graph()

    def binding(item: Item) -> tuple[ClockCoordinate, ClockCoordinate]:
        return (ClockCoordinate(9, 9), ClockCoordinate(9, 9))

    with pytest.raises(ValueError, match="not an occupied spine coordinate"):
        tiergraph_dot.dumps(graph, clock=_structural_clock(graph), binding=binding)


def test_structural_binding_type_is_checked() -> None:
    """A non-callable binding argument is refused by type."""
    graph = _kat_graph()
    with pytest.raises(TypeError, match="binding must be a callable"):
        tiergraph_dot.dumps(graph, binding="nope")  # type: ignore[arg-type]


def test_binding_is_refused_when_the_renderer_cannot_use_it() -> None:
    """Placement cannot be silently ignored without a structural clock."""
    graph, profile = graph_and_clock()

    def binding(item: Item) -> tuple[ClockCoordinate, ClockCoordinate]:
        return ClockCoordinate(0), ClockCoordinate(1)

    with pytest.raises(ValueError, match="only used with a structural"):
        tiergraph_dot.dumps(graph, binding=binding)
    with pytest.raises(ValueError, match="only used with a structural"):
        tiergraph_dot.dumps(graph, clock=profile, binding=binding)


def test_structural_boundary_relation_endpoint_is_refused() -> None:
    """A relation with a boundary endpoint is refused, not crashed, in the view."""
    base = _kat_graph()
    link = _ik("boundary-link")
    graph = replace(
        base,
        relation_declarations=(
            *base.relation_declarations,
            SimpleRelationDeclaration(
                _ik("seg-type-rel"), _ik("tier-0"), _ik("seg-type")
            ),
            BipartiteRelationDeclaration(
                link,
                _ik("seg-type"),
                _ik("seg-type"),
                RelationEndpointKind.BOUNDARY,
                RelationEndpointKind.BOUNDARY,
            ),
        ),
        relations=(
            RelationInstance(
                link,
                DurableBoundaryRef(_ik("tier-0"), BoundarySide.BEFORE),
                DurableBoundaryRef(_ik("tier-0"), BoundarySide.AFTER),
            ),
        ),
    )
    with pytest.raises(ValueError, match="boundary endpoint"):
        tiergraph_dot.dumps(
            graph,
            clock=_structural_clock(graph),
            binding=fixture_hooks_and_binding(graph)[1],
        )


def test_structural_clock_tier_relation_target_is_refused() -> None:
    """A relation targeting a clock-tier item is refused (no drawn node)."""
    base = _kat_graph()
    rel = _ik("to-clock")
    graph = replace(
        base,
        relation_declarations=(
            *base.relation_declarations,
            PolyadicRelationDeclaration(
                rel,
                RelationSideDeclaration((RelationEndpointKind.ITEM,), (_ik("tier-0"),)),
                RelationSideDeclaration((RelationEndpointKind.ITEM,), (_IK_CLOCK,)),
            ),
        ),
        polyadic_relations=(
            PolyadicRelationInstance(
                rel, (ItemRef(_ik("tier-0"), 0),), (ItemRef(_IK_CLOCK, 0),)
            ),
        ),
    )
    with pytest.raises(ValueError, match="clock-tier item"):
        tiergraph_dot.dumps(
            graph,
            clock=_structural_clock(graph),
            binding=fixture_hooks_and_binding(graph)[1],
        )


def test_structural_ids_are_sanitized_and_collision_broken() -> None:
    """Unsafe tier long names are escaped and equal ones get a numeric suffix."""
    graph = build_fixture_graph(
        ((0, 0), (0, 1), (1, 0), (1, 1)),
        (
            ("m-n", (_ik_item("/clock/0/first/0", text="p", duration=0),)),
            ("m-n", (_ik_item("/clock/1/second/0", text="q", duration=0),)),
        ),
    )
    rendered = _render_fixture(graph)
    # The hyphen is escaped; the second identical name is disambiguated.
    assert "subgraph tier_m_2d_n {" in rendered
    assert "subgraph tier_m_2d_n_2 {" in rendered
    assert "guide_m_2d_n_" in rendered
    assert_graphviz_accepts(rendered)


def test_structural_renders_valid_item_relations() -> None:
    """Item-endpoint bipartite and polyadic relations are drawn as arcs."""
    base = _kat_graph()
    segment = _ik("tier-0")
    link, choose, seg_type = _ik("link"), _ik("choose"), _ik("seg-type")
    graph = replace(
        base,
        relation_declarations=(
            *base.relation_declarations,
            SimpleRelationDeclaration(_ik("seg-members"), segment, seg_type),
            BipartiteRelationDeclaration(link, seg_type, seg_type),
            PolyadicRelationDeclaration(
                choose,
                RelationSideDeclaration((RelationEndpointKind.ITEM,), (segment,)),
                RelationSideDeclaration((RelationEndpointKind.ITEM,), (segment,)),
            ),
        ),
        relations=(RelationInstance(link, ItemRef(segment, 0), ItemRef(segment, 1)),),
        polyadic_relations=(
            PolyadicRelationInstance(
                choose, (ItemRef(segment, 0),), (ItemRef(segment, 2),)
            ),
        ),
    )
    rendered = _render_fixture(graph)
    assert "// Declared bipartite relations." in rendered
    assert "// Declared polyadic relations." in rendered
    seg0 = _fixture_node_id("/clock/0/segment/0")
    seg1 = _fixture_node_id("/clock/1/segment/0")
    seg2 = _fixture_node_id("/clock/2/segment/0")
    assert f'{seg0} -> {seg1} [label="link"' in rendered
    assert f'{seg0} -> {seg2} [label="choose"' in rendered
    assert_graphviz_accepts(rendered)


def test_structural_non_monotonic_placement_is_refused() -> None:
    """Items must be supplied in non-decreasing clock order, else refused."""
    graph = build_fixture_graph(
        ((0, 0), (0, 1), (1, 0), (1, 1)),
        (
            (
                "segment",
                (
                    _ik_item("/clock/1/segment/0", text="late", duration=0),
                    _ik_item("/clock/0/segment/0", text="early", duration=0),
                ),
            ),
        ),
    )
    _, binding = fixture_hooks_and_binding(graph)
    with pytest.raises(ValueError, match="non-decreasing clock order"):
        tiergraph_dot.dumps(graph, clock=_structural_clock(graph), binding=binding)


def test_bipartite_relation_label_falls_back_to_local_name() -> None:
    """A bipartite edge falls back to the local name with no or None relation_name."""
    graph = _hierarchy_graph()
    base_presentation, binding = fixture_hooks_and_binding(graph)
    for relation_name in (None, lambda relation: None):
        presentation = replace(base_presentation, relation_name=relation_name)
        rendered = tiergraph_dot.dumps(
            graph,
            clock=_structural_clock(graph),
            presentation=presentation,
            binding=binding,
        )
        assert 'label="contains-0"' in rendered


def test_structural_polyadic_without_presentation_uses_default_rendering() -> None:
    """With no presentation, a non-empty polyadic fans out under the default header."""
    base = _kat_graph()
    segment = _ik("tier-0")
    choose, seg_type = _ik("choose"), _ik("seg-type")
    graph = replace(
        base,
        relation_declarations=(
            *base.relation_declarations,
            SimpleRelationDeclaration(_ik("seg-members"), segment, seg_type),
            PolyadicRelationDeclaration(
                choose,
                RelationSideDeclaration((RelationEndpointKind.ITEM,), (segment,)),
                RelationSideDeclaration((RelationEndpointKind.ITEM,), (segment,)),
            ),
        ),
        polyadic_relations=(
            PolyadicRelationInstance(
                choose, (ItemRef(segment, 0),), (ItemRef(segment, 1),)
            ),
        ),
    )
    _, binding = fixture_hooks_and_binding(graph)
    rendered = tiergraph_dot.dumps(
        graph, clock=_structural_clock(graph), binding=binding
    )
    assert "// Declared polyadic relations." in rendered
    assert '[label="choose"' in rendered
    assert "// Declared relations." not in rendered


def test_relation_style_hook_is_evaluated_exactly_once_per_relation() -> None:
    """A stateful relation_style is called once; the relation lands in one section.

    Regression: with two evaluations a hook returning "bipartite" then None would
    exclude the relation from both sections, silently dropping it.
    """
    graph = _hierarchy_graph()  # one non-empty polyadic (contains) + empty roots
    base_presentation, binding = fixture_hooks_and_binding(graph)
    calls: list[str] = []
    styles = iter(("bipartite", None, None))

    def relation_style(relation: PolyadicRelationInstance) -> str | None:
        calls.append(str(relation.declaration.local_name))
        return next(styles)

    presentation = replace(base_presentation, relation_style=relation_style)
    rendered = tiergraph_dot.dumps(
        graph,
        clock=_structural_clock(graph),
        presentation=presentation,
        binding=binding,
    )
    # Exactly one evaluation for the single non-empty polyadic (roots is filtered).
    assert calls == ["contains-0"]
    # The relation neither vanishes nor duplicates: it lands in exactly one section.
    assert "// Declared relations." in rendered
    assert "// Declared polyadic relations." not in rendered
    edge = (
        f"{_fixture_node_id('/clock/0/utterance/0')} -> "
        f'{_fixture_node_id("/clock/0/segment/0")} [label="contains"'
    )
    assert rendered.count(edge) == 1

    # One relation cannot tell "once per relation" from "once per render", so
    # the claim is measured again on a graph carrying a second instance: a hook
    # consulted once for the whole render would report one call here.
    two = replace(
        graph,
        polyadic_relations=(
            *graph.polyadic_relations,
            PolyadicRelationInstance(
                _ik("contains-0"),
                (ItemRef(_ik("tier-0"), 0),),
                (ItemRef(_ik("tier-1"), 2),),
            ),
        ),
    )
    assert len(two.polyadic_relations) == len(graph.polyadic_relations) + 1
    repeated: list[str] = []

    def counting_style(relation: PolyadicRelationInstance) -> str | None:
        repeated.append(str(relation.declaration.local_name))
        return "bipartite"

    tiergraph_dot.dumps(
        graph=two,
        clock=_structural_clock(two),
        presentation=replace(base_presentation, relation_style=counting_style),
        binding=binding,
    )
    assert repeated == ["contains-0", "contains-0"]
