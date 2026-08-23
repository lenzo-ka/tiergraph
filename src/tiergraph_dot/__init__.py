"""Render tiergraph values as deterministic Graphviz DOT.

The renderer is a read-only view over the public tiergraph API. Tier and item
order comes from the graph, clock order comes from the clock profile, and
relation endpoints retain their declared sequence. No layout data is stored in
the graph. This import package ships in the same ``tiergraph`` distribution as
the kernel, so it is versioned and installed with the kernel rather than as an
independently installable renderer.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from tiergraph import (
    AttributeValue,
    ClockPosition,
    ClockProfile,
    DurablePositionRef,
    Graph,
    Item,
    ItemRef,
    PolyadicRelationInstance,
    PositionRef,
    QualifiedName,
    RelationInstance,
    Tier,
)
from tiergraph.path import ItemBinding, StructuralPathProfile
from tiergraph.spanview import SpanViewProfile, span_view


@dataclass(frozen=True, slots=True)
class DotPresentation:
    """Optional overrides for tier labels, node ids, and item labels in DOT.

    Each hook is optional and may return ``None`` for any element to fall back
    to the renderer's default. When the whole profile is ``None`` -- or a hook
    is absent or returns ``None`` -- the emitted DOT is byte-identical to the
    default rendering; the hooks are the only surface through which output can
    differ. Overridden tier names and item labels are quoted through the same
    ``_quote`` path as the defaults. An overridden node id is emitted verbatim
    as a DOT identifier and is applied consistently at the node definition and
    at every edge endpoint that references it, so an override never leaves a
    dangling reference.

    ``tier_name`` receives the :class:`tiergraph.Tier`, ``node_id`` receives the
    :class:`tiergraph.ItemRef` of the item, and ``item_label`` receives the
    :class:`tiergraph.Item`.
    """

    tier_name: Callable[..., str | None] | None = None
    node_id: Callable[..., str | None] | None = None
    item_label: Callable[..., str | None] | None = None


def dumps(
    graph: Graph,
    *,
    clock: ClockProfile | None = None,
    presentation: DotPresentation | None = None,
    include_empty_tiers: bool = False,
) -> str:
    """Return byte-stable DOT for ``graph``.

    With ``clock``, the complete refined clock is the horizontal spine. Timed
    tier boundaries align with that spine, event extents end at their bound
    refined positions, and physical timing is included when the profile exposes
    it. Explicitly untimed tiers are still drawn on their own structural axes.
    Without ``clock``, every tier uses its own ordered structural boundaries.

    Empty tiers are omitted by default and included when
    ``include_empty_tiers`` is true. Attribute names and values are rendered as
    data; the renderer assigns no domain-specific meaning to them. A clock
    profile must belong to this exact graph instance, not merely an equal graph,
    because its cached derived state was computed from that instance.
    """
    if not isinstance(graph, Graph):
        raise TypeError(f"graph must be a tiergraph.Graph, got {type(graph).__name__}")
    if clock is not None:
        if not isinstance(clock, ClockProfile):
            raise TypeError(
                "clock must be a tiergraph.ClockProfile or None, "
                f"got {type(clock).__name__}"
            )
        if clock.graph is not graph:
            raise ValueError("clock profile graph is not the graph being rendered")
    if presentation is not None and not isinstance(presentation, DotPresentation):
        raise TypeError(
            "presentation must be a tiergraph_dot.DotPresentation or None, "
            f"got {type(presentation).__name__}"
        )

    structural = clock is not None and clock._structural
    visible = tuple(
        (tier_index, tier)
        for tier_index, tier in enumerate(graph.tiers)
        if (tier.items or include_empty_tiers)
        and not (
            structural
            and clock is not None
            and tier.declaration.name == clock.clock_tier
        )
    )
    lines = [
        "digraph tiergraph {",
        '  graph [rankdir=TB, newrank=true, ranksep="0.62 equally", nodesep=0.28, splines=line];',
        '  node [fontname="Helvetica"];',
        '  edge [fontname="Helvetica", fontsize=9];',
    ]

    clock_ids: tuple[str, ...] = ()
    clock_labels: tuple[str, ...] = ()
    clock_positions: tuple[ClockPosition, ...] = ()
    if clock is not None:
        clock_positions = clock.positions
        if structural:
            clock_ids, clock_labels = _occupied_spine_identity(clock_positions)
            spine_comment = "  // The clock spine is the total order."
        else:
            clock_ids = tuple(f"clock_{index}" for index in range(len(clock_positions)))
            clock_labels = tuple(
                _position_label(position) for position in clock_positions
            )
            spine_comment = "  // The refined clock spine is the total order."
        lines.extend(("", spine_comment, "  { rank=same;"))
        lines.append('    score_start_clock [shape=plaintext, label="clock"];')
        for index in range(len(clock_positions)):
            lines.append(
                f"    {clock_ids[index]} [shape=circle, width=0.46, fixedsize=true, "
                f'group="time_{index}", label="{clock_labels[index]}"];'
            )
        for left, right in zip(clock_ids, clock_ids[1:], strict=False):
            lines.append(f"    {left} -> {right} [weight=100];")
        lines.append("  }")

    tier_labels: list[str] = []
    tier_slots: list[tuple[str, ...]] = []
    item_nodes: dict[ItemRef, str] = {}
    boundary_nodes: dict[PositionRef, str] = {}
    for tier_index, tier in visible:
        tier_name = tier.declaration.name
        timed = clock is not None and (
            tier_name == clock.clock_tier or clock.is_timed(tier_name)
        )
        positions = graph.positions(tier_name)
        if timed:
            assert clock is not None
            slot_count = len(clock_ids)
            boundary_indexes = tuple(
                _clock_index(clock, position.reference, clock_positions)
                for position in positions
                if isinstance(position.reference, PositionRef)
            )
        else:
            slot_count = len(positions)
            boundary_indexes = tuple(range(slot_count))

        label_id = f"tier_label_{tier_index}"
        tier_labels.append(label_id)
        lines.extend(("", f"  subgraph tier_{tier_index} {{", "    rank=same;"))
        lines.append(
            f"    {label_id} [shape=plaintext, "
            f'label="{_resolve_tier_name(presentation, tier)}"];'
        )
        starts: list[list[int]] = [[] for _ in range(slot_count)]
        for item_index in range(len(tier.items)):
            starts[boundary_indexes[item_index]].append(item_index)

        slots: list[str] = []
        for slot_index, starting_items in enumerate(starts):
            slot = f"guide_{tier_index}_{slot_index}"
            group = f"time_{slot_index}" if timed else f"tier_{tier_index}_{slot_index}"
            lines.append(
                f'    {slot} [shape=point, width=0.01, label="", '
                f'group="{group}", style=invis];'
            )
            for item_index in starting_items:
                reference = ItemRef(tier_name, item_index)
                item_nodes[reference] = _resolve_node_id(
                    presentation, reference, f"item_{tier_index}_{item_index}"
                )
                label = _resolve_item_label(
                    presentation, tier.items[item_index], graph, reference, clock
                )
                lines.append(
                    f'    {item_nodes[reference]} [shape=box, group="{group}", '
                    f'label="{label}"];'
                )
            slots.append(slot)
        for left, right in zip(slots, slots[1:], strict=False):
            lines.append(f"    {left} -> {right} [style=invis, weight=100];")
        for item_index in range(len(tier.items) - 1):
            left = item_nodes[ItemRef(tier_name, item_index)]
            right = item_nodes[ItemRef(tier_name, item_index + 1)]
            lines.append(
                f"    {left} -> {right} "
                '[color="#888888", penwidth=0.8, arrowsize=0.55, constraint=false];'
            )
        for item_index in range(len(tier.items)):
            start_index = boundary_indexes[item_index]
            end_index = boundary_indexes[item_index + 1]
            if start_index != end_index:
                lines.append(
                    f"    {item_nodes[ItemRef(tier_name, item_index)]} -> {slots[end_index]} "
                    '[xlabel="extent", color="#777777", style=dashed, arrowhead=tee, '
                    "arrowsize=0.6, fontsize=8, constraint=false];"
                )
        lines.append("  }")
        tier_slots.append(tuple(slots))
        for position_index, boundary_index in enumerate(boundary_indexes):
            boundary_nodes[PositionRef(tier_name, position_index)] = (
                clock_ids[boundary_index] if timed else slots[boundary_index]
            )

    if clock is not None or tier_labels:
        brace_comment = (
            "  // The score brace joins lane starts in declaration order."
            if structural
            else "  // The score brace joins rows in tier order."
        )
        lines.extend(("", brace_comment))
        row_anchors = (["score_start_clock"] if clock is not None else []) + tier_labels
        for upper, lower in zip(row_anchors, row_anchors[1:], strict=False):
            lines.append(
                f'  {upper} -> {lower} [dir=none, color="#333333", penwidth=2.4, weight=100];'
            )

    if clock is not None:
        register_comment = (
            "  // Register every lane to the clock's time columns."
            if structural
            else "  // Register timed lanes to refined clock positions."
        )
        lines.extend(("", register_comment))
        for position_index, clock_id in enumerate(clock_ids):
            column = [clock_id]
            for (_tier_index, tier), row_slots in zip(visible, tier_slots, strict=True):
                if tier.declaration.name == clock.clock_tier or clock.is_timed(
                    tier.declaration.name
                ):
                    column.append(row_slots[position_index])
            for upper, lower in zip(column, column[1:], strict=False):
                lines.append(
                    f"  {upper} -> {lower} [style=invis, weight=1000, arrowhead=none];"
                )

        trigger_comment = (
            "  // Trigger every event from the clock position it occupies."
            if structural
            else "  // Trigger timed events from their refined positions."
        )
        lines.extend(("", trigger_comment))
        for _tier_index, tier in visible:
            if tier.declaration.name != clock.clock_tier and not clock.is_timed(
                tier.declaration.name
            ):
                continue
            for item_index in range(len(tier.items)):
                reference = ItemRef(tier.declaration.name, item_index)
                start = boundary_nodes[PositionRef(reference.tier, item_index)]
                lines.append(
                    f"  {start} -> {item_nodes[reference]} "
                    '[color="#2f6f9f", penwidth=1.35, arrowsize=0.65, weight=100];'
                )

    _relation_lines(lines, graph, item_nodes, boundary_nodes)
    lines.append("}")
    return "\n".join(lines) + "\n"


def dumps_spans(
    graph: Graph,
    profile: SpanViewProfile,
    *,
    alternatives: bool = False,
    include_empty_tiers: bool = False,
) -> str:
    """Return deterministic DOT focused on a segmentation and its span extents."""
    view = span_view(graph, profile, alternatives=alternatives)
    selected_tiers = (profile.base_tier, *profile.span_tiers)
    tiers = tuple(
        (index, tier)
        for index, tier in enumerate(graph.tiers)
        if tier.declaration.name in selected_tiers
        and (tier.items or include_empty_tiers)
    )
    lines = [
        "digraph tiergraph_spans {",
        '  graph [rankdir=TB, newrank=true, ranksep="0.62 equally", nodesep=0.28, splines=line];',
        '  node [fontname="Helvetica"];',
        '  edge [fontname="Helvetica", fontsize=9];',
    ]
    node_ids: dict[ItemRef, str] = {}
    for tier_index, tier in tiers:
        lines.extend(("", f"  subgraph tier_{tier_index} {{", "    rank=same;"))
        lines.append(
            f'    tier_label_{tier_index} [shape=plaintext, label="{_quote(tier.declaration.short_name, "tier name")}"];'
        )
        for item_index, _item in enumerate(tier.items):
            reference = ItemRef(tier.declaration.name, item_index)
            node = f"item_{tier_index}_{item_index}"
            node_ids[reference] = node
            label = str(item_index)
            matching = next(
                (
                    span
                    for span in view.spans
                    if span.path
                    == str(StructuralPathProfile().spell(ItemBinding(reference), graph))
                ),
                None,
            )
            if matching is not None:
                fields = [matching.label, f"index={matching.start}..{matching.end}"]
                if matching.char_start is not None:
                    fields.append(f"chars={matching.char_start}..{matching.char_end}")
                if matching.value is not None:
                    fields.append(f"value={matching.value}")
                if matching.score is not None:
                    fields.append(f"score={matching.score}")
                if alternatives:
                    fields.extend(
                        f"alternative={candidate.value or '-'} ({candidate.score or '-'})"
                        for candidate in matching.alternatives
                    )
                label = "\\n".join(_quote(field, "span label") for field in fields)
            lines.append(f'    {node} [shape=box, label="{label}"];')
        lines.append("  }")
    lines.extend(("", "  // Span extents over ordered base atoms."))
    for span in view.spans:
        span_reference = next(
            reference
            for reference in node_ids
            if str(StructuralPathProfile().spell(ItemBinding(reference), graph))
            == span.path
        )
        first = node_ids[ItemRef(profile.base_tier, span.start)]
        last = node_ids[ItemRef(profile.base_tier, span.end - 1)]
        lines.append(
            f"  {node_ids[span_reference]} -> {first} "
            '[xlabel="extent", color="#777777", style=dashed, arrowhead=tee, '
            "arrowsize=0.6, fontsize=8, constraint=false];"
        )
        if first != last:
            lines.append(
                f"  {node_ids[span_reference]} -> {last} "
                '[xlabel="extent", color="#777777", style=dashed, arrowhead=tee, '
                "arrowsize=0.6, fontsize=8, constraint=false];"
            )
    lines.append("}")
    return "\n".join(lines) + "\n"


def _clock_index(
    clock: ClockProfile,
    reference: PositionRef | DurablePositionRef,
    positions: tuple[ClockPosition, ...],
) -> int:
    resolved = clock.graph.resolve_position(reference)
    if resolved.tier == clock.clock_tier:
        return resolved.index
    return positions.index(clock.refined_position(resolved))


def _resolve_tier_name(presentation: DotPresentation | None, tier: Tier) -> str:
    if presentation is not None and presentation.tier_name is not None:
        override = presentation.tier_name(tier)
        if override is not None:
            return _quote(override, "tier name")
    return _quote(tier.declaration.short_name, "tier name")


def _resolve_node_id(
    presentation: DotPresentation | None, reference: ItemRef, default: str
) -> str:
    if presentation is not None and presentation.node_id is not None:
        override = presentation.node_id(reference)
        if override is not None:
            return override
    return default


def _resolve_item_label(
    presentation: DotPresentation | None,
    item: Item,
    graph: Graph,
    reference: ItemRef,
    clock: ClockProfile | None,
) -> str:
    if presentation is not None and presentation.item_label is not None:
        override = presentation.item_label(item)
        if override is not None:
            return _quote(override, "item label")
    return _item_label(graph, reference, clock)


def _occupied_spine_identity(
    positions: tuple[ClockPosition, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Name each occupied spine node from its tick, sharing single-gap ticks.

    A coarse tick with exactly one occupied position is drawn as ``clock_{t}``
    labeled ``{t}``; a tick with several occupied gaps distinguishes each as
    ``clock_{t}_gap_{g}`` labeled ``{t}.{g}``.
    """
    counts = Counter(position.tick for position in positions)
    ids: list[str] = []
    labels: list[str] = []
    for position in positions:
        if counts[position.tick] == 1:
            ids.append(f"clock_{position.tick}")
            labels.append(str(position.tick))
        else:
            ids.append(f"clock_{position.tick}_gap_{position.gap}")
            labels.append(f"{position.tick}.{position.gap}")
    return tuple(ids), tuple(labels)


def _item_label(graph: Graph, reference: ItemRef, clock: ClockProfile | None) -> str:
    tier = next(tier for tier in graph.tiers if tier.declaration.name == reference.tier)
    item = tier.items[reference.index]
    heading = (
        _quote(item.durable_id, "item durable ID")
        if item.durable_id is not None
        else str(reference.index)
    )
    fields = [_attribute_label(value) for value in item.attributes]
    if clock is not None and reference.tier != clock.clock_tier:
        timing = clock.timing(reference.tier, reference.index)
        if timing is not None:
            fields.append(
                f"time={timing.start}+{timing.duration} "
                f"{_quote(timing.unit, 'clock unit attribute lexical value')}"
            )
    return "\\n".join((heading, *fields))


def _attribute_label(value: AttributeValue) -> str:
    return (
        f"{_quote(value.name.local_name, 'attribute name')}"
        f"={_quote(value.lexical, 'item attribute lexical value')}"
    )


def _position_label(position: ClockPosition) -> str:
    return (
        str(position.tick) if position.gap == 0 else f"{position.tick}.{position.gap}"
    )


def _relation_lines(
    lines: list[str],
    graph: Graph,
    items: dict[ItemRef, str],
    boundaries: dict[PositionRef, str],
) -> None:
    if graph.relations:
        lines.extend(("", "  // Declared bipartite relations."))
    for relation in graph.relations:
        lines.append(_bipartite_line(graph, relation, items, boundaries))
    polyadic = tuple(
        relation
        for relation in graph.polyadic_relations
        if relation.sources and relation.targets
    )
    if polyadic:
        lines.extend(("", "  // Declared polyadic relations."))
    for polyadic_relation in polyadic:
        lines.extend(_polyadic_lines(graph, polyadic_relation, items, boundaries))


def _bipartite_line(
    graph: Graph,
    relation: RelationInstance,
    items: dict[ItemRef, str],
    boundaries: dict[PositionRef, str],
) -> str:
    left = _endpoint_id(graph, relation.left, items, boundaries)
    right = _endpoint_id(graph, relation.right, items, boundaries)
    return _arc(left, right, relation.declaration)


def _polyadic_lines(
    graph: Graph,
    relation: PolyadicRelationInstance,
    items: dict[ItemRef, str],
    boundaries: dict[PositionRef, str],
) -> Iterable[str]:
    for source in relation.sources:
        for target in relation.targets:
            yield _arc(
                _endpoint_id(graph, source, items, boundaries),
                _endpoint_id(graph, target, items, boundaries),
                relation.declaration,
            )


def _endpoint_id(
    graph: Graph,
    endpoint: ItemRef | DurablePositionRef,
    items: dict[ItemRef, str],
    boundaries: dict[PositionRef, str],
) -> str:
    if isinstance(endpoint, ItemRef):
        return items[graph.resolve_item(endpoint)]
    resolved = graph.resolve_position(endpoint)
    try:
        return boundaries[resolved]
    except KeyError as error:
        raise ValueError(
            f"relation endpoint {endpoint.to_data()!r} belongs to an omitted empty "
            "tier; render with include_empty_tiers=True"
        ) from error


def _arc(left: str, right: str, declaration: QualifiedName) -> str:
    return (
        f"  {left} -> {right} "
        f'[label="{_quote(declaration.local_name, "relation name")}", '
        'color="#5555aa", constraint=false];'
    )


def _quote(value: str, field: str) -> str:
    """Quote one model string or refuse characters DOT cannot carry safely.

    DOT quoted strings carry printable Unicode, backslash, double quote, and LF;
    the latter three are escaped below. Other C0 controls, DEL, C1 controls, and
    Unicode surrogates are refused. They have no faithful, portable Graphviz
    label representation. Nothing is silently stripped or replaced.
    """
    for character in value:
        codepoint = ord(character)
        if (
            (codepoint < 0x20 and character != "\n")
            or 0x7F <= codepoint <= 0x9F
            or 0xD800 <= codepoint <= 0xDFFF
        ):
            raise ValueError(
                f"DOT cannot render {field} value {value!r}: unsupported character "
                f"U+{codepoint:04X}"
            )
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


__all__ = ["DotPresentation", "dumps", "dumps_spans"]
