"""Render tiergraph values as deterministic Graphviz DOT.

The renderer is a read-only view over the public tiergraph API. Tier and item
order comes from the graph, clock order comes from the clock profile, and
relation endpoints retain their declared sequence. No layout data is stored in
the graph. This import package ships in the same ``tiergraph`` distribution as
the kernel, so it is versioned and installed with the kernel rather than as an
independently installable renderer.
"""

from __future__ import annotations

from collections.abc import Iterable

from tiergraph import (
    AttributeValue,
    ClockPosition,
    ClockProfile,
    DurablePositionRef,
    Graph,
    ItemRef,
    PolyadicRelationInstance,
    PositionRef,
    QualifiedName,
    RelationInstance,
)


def dumps(
    graph: Graph,
    *,
    clock: ClockProfile | None = None,
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

    visible = tuple(
        (tier_index, tier)
        for tier_index, tier in enumerate(graph.tiers)
        if tier.items or include_empty_tiers
    )
    lines = [
        "digraph tiergraph {",
        '  graph [rankdir=TB, newrank=true, ranksep="0.62 equally", nodesep=0.28, splines=line];',
        '  node [fontname="Helvetica"];',
        '  edge [fontname="Helvetica", fontsize=9];',
    ]

    clock_ids: tuple[str, ...] = ()
    clock_positions: tuple[ClockPosition, ...] = ()
    if clock is not None:
        clock_positions = clock.positions
        clock_ids = tuple(f"clock_{index}" for index in range(len(clock_positions)))
        lines.extend(
            ("", "  // The refined clock spine is the total order.", "  { rank=same;")
        )
        lines.append('    score_start_clock [shape=plaintext, label="clock"];')
        for index, position in enumerate(clock_positions):
            lines.append(
                f"    {clock_ids[index]} [shape=circle, width=0.46, fixedsize=true, "
                f'group="time_{index}", label="{_position_label(position)}"];'
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
            f'label="{_quote(tier.declaration.short_name, "tier name")}"];'
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
                item_nodes[reference] = f"item_{tier_index}_{item_index}"
                label = _item_label(graph, reference, clock)
                lines.append(
                    f'    {item_nodes[reference]} [shape=box, group="{group}", '
                    f'label="{label}"];'
                )
            slots.append(slot)
        for left, right in zip(slots, slots[1:], strict=False):
            lines.append(f"    {left} -> {right} [style=invis, weight=100];")
        for item_index in range(len(tier.items) - 1):
            lines.append(
                f"    item_{tier_index}_{item_index} -> item_{tier_index}_{item_index + 1} "
                '[color="#888888", penwidth=0.8, arrowsize=0.55, constraint=false];'
            )
        for item_index in range(len(tier.items)):
            start_index = boundary_indexes[item_index]
            end_index = boundary_indexes[item_index + 1]
            if start_index != end_index:
                lines.append(
                    f"    item_{tier_index}_{item_index} -> {slots[end_index]} "
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
        lines.extend(("", "  // The score brace joins rows in tier order."))
        row_anchors = (["score_start_clock"] if clock is not None else []) + tier_labels
        for upper, lower in zip(row_anchors, row_anchors[1:], strict=False):
            lines.append(
                f'  {upper} -> {lower} [dir=none, color="#333333", penwidth=2.4, weight=100];'
            )

    if clock is not None:
        lines.extend(("", "  // Register timed lanes to refined clock positions."))
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

        lines.extend(("", "  // Trigger timed events from their refined positions."))
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


def _clock_index(
    clock: ClockProfile,
    reference: PositionRef | DurablePositionRef,
    positions: tuple[ClockPosition, ...],
) -> int:
    resolved = clock.graph.resolve_position(reference)
    if resolved.tier == clock.clock_tier:
        return resolved.index
    return positions.index(clock.refined_position(resolved))


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
    if graph.polyadic_relations:
        lines.extend(("", "  // Declared polyadic relations."))
    for polyadic_relation in graph.polyadic_relations:
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


__all__ = ["dumps"]
