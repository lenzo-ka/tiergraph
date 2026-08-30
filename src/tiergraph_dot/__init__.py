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
    BoundaryRef,
    ClockCoordinate,
    ClockProfile,
    DurableBoundaryRef,
    DurableItemRef,
    Graph,
    Item,
    ItemRef,
    PolyadicRelationInstance,
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

    ``tier_name`` is called as ``tier_name(tier)`` with the
    :class:`tiergraph.Tier`; ``node_id`` as ``node_id(reference)`` with the
    item's :class:`tiergraph.ItemRef`; and ``item_label`` as
    ``item_label(item, tier)`` with the :class:`tiergraph.Item` and its owning
    :class:`tiergraph.Tier`, so a consumer can fall back to a tier-derived
    label. When ``item_label`` is absent or returns ``None`` the default label
    is built from the item's durable id and attributes without querying clock
    timing, so the default holds under a structural clock as well.

    Two further hooks shape relation rendering on the occupied-spine path.
    ``relation_style`` is called as ``relation_style(relation)`` with the
    relation instance; when it returns ``"bipartite"`` for a polyadic relation
    that relation is drawn as individual parent-to-child edges (one per
    source-target pair) under a ``// Declared relations.`` header rather than as
    the default polyadic fan-out. ``relation_name`` is called as
    ``relation_name(relation)`` and supplies each such edge's label, defaulting
    to the relation's local name. Both are per-relation: absent hooks, a ``None``
    return, or any non-``"bipartite"`` style leave relations rendered exactly as
    before.
    """

    tier_name: Callable[..., str | None] | None = None
    node_id: Callable[..., str | None] | None = None
    item_label: Callable[..., str | None] | None = None
    relation_name: Callable[..., str | None] | None = None
    relation_style: Callable[..., str | None] | None = None


def dumps(
    graph: Graph,
    *,
    clock: ClockProfile | None = None,
    presentation: DotPresentation | None = None,
    binding: Callable[..., tuple[ClockCoordinate, ClockCoordinate]] | None = None,
    include_empty_tiers: bool = False,
) -> str:
    """Return byte-stable DOT for ``graph``.

    With ``clock``, the complete refined clock is the horizontal spine. Timed
    tier boundaries align with that spine, event extents end at their bound
    refined coordinates, and physical timing is included when the profile exposes
    it. Explicitly untimed tiers are still drawn on their own structural axes.
    Without ``clock``, every tier uses its own ordered structural boundaries.

    Empty tiers are omitted by default and included when
    ``include_empty_tiers`` is true. Attribute names and values are rendered as
    data; the renderer assigns no domain-specific meaning to them. A clock
    profile must belong to this exact graph instance, not merely an equal graph,
    because its cached derived state was computed from that instance.

    A structural clock (built by :meth:`ClockProfile.from_boundary_values`)
    selects the occupied-spine rendering: the clock tier is drawn only as the
    spine, an occupied clock column is anchored on its item node, and empty
    columns keep a guide point. ``binding`` places the non-clock items: when it
    is supplied it MUST return, for every visible non-clock item, the
    ``(start, end)`` :class:`tiergraph.ClockCoordinate` pair naming the collapsed
    columns the item occupies. There is no untimed lane, so returning ``None``
    is refused with the offending item named. The kernel never parses domain
    identifiers; the caller supplies the placement.
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
    if binding is not None and not callable(binding):
        raise TypeError(
            f"binding must be a callable or None, got {type(binding).__name__}"
        )

    if binding is not None and (clock is None or not clock.is_structural):
        raise ValueError(
            "binding is only used with a structural ClockProfile built by "
            "from_boundary_values"
        )

    if clock is not None and clock.is_structural:
        return _dumps_occupied_spine(
            graph, clock, presentation, binding, include_empty_tiers
        )

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
    clock_coordinates: tuple[ClockCoordinate, ...] = ()
    if clock is not None:
        clock_coordinates = clock.coordinates
        clock_ids = tuple(f"clock_{index}" for index in range(len(clock_coordinates)))
        lines.extend(
            ("", "  // The refined clock spine is the total order.", "  { rank=same;")
        )
        lines.append('    score_start_clock [shape=plaintext, label="clock"];')
        for index, coordinate in enumerate(clock_coordinates):
            lines.append(
                f"    {clock_ids[index]} [shape=circle, width=0.46, fixedsize=true, "
                f'group="time_{index}", label="{_coordinate_label(coordinate)}"];'
            )
        for left, right in zip(clock_ids, clock_ids[1:], strict=False):
            lines.append(f"    {left} -> {right} [weight=100];")
        lines.append("  }")

    tier_labels: list[str] = []
    tier_slots: list[tuple[str, ...]] = []
    item_nodes: dict[ItemRef, str] = {}
    boundary_nodes: dict[BoundaryRef, str] = {}
    for tier_index, tier in visible:
        tier_name = tier.declaration.name
        timed = clock is not None and (
            tier_name == clock.clock_tier or clock.is_timed(tier_name)
        )
        boundaries = graph.boundaries(tier_name)
        if timed:
            assert clock is not None
            slot_count = len(clock_ids)
            boundary_indexes = tuple(
                _clock_index(clock, boundary.reference, clock_coordinates)
                for boundary in boundaries
            )
        else:
            slot_count = len(boundaries)
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
                    presentation, tier.items[item_index], tier, graph, reference, clock
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
        for local_index, boundary_index in enumerate(boundary_indexes):
            boundary_nodes[BoundaryRef(tier_name, local_index)] = (
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
        for boundary_index, clock_id in enumerate(clock_ids):
            column = [clock_id]
            for (_tier_index, tier), row_slots in zip(visible, tier_slots, strict=True):
                if tier.declaration.name == clock.clock_tier or clock.is_timed(
                    tier.declaration.name
                ):
                    column.append(row_slots[boundary_index])
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
                start = boundary_nodes[BoundaryRef(reference.tier, item_index)]
                lines.append(
                    f"  {start} -> {item_nodes[reference]} "
                    '[color="#2f6f9f", penwidth=1.35, arrowsize=0.65, weight=100];'
                )

    _relation_lines(lines, graph, item_nodes, boundary_nodes)
    lines.append("}")
    return "\n".join(lines) + "\n"


def _sanitize_structural_id(name: str) -> str:
    """Map a tier name to a safe bare DOT identifier, verbatim when already safe.

    A name that is already a run of ASCII letters, digits, and underscores is
    returned unchanged, so tiers with such long names keep the ids they render
    today. Any other byte is escaped as
    ``_<hex>_`` so hyphens, spaces, and non-ASCII never produce an invalid or
    ambiguous identifier.
    """
    safe = "".join(
        character
        if character.isascii() and (character.isalnum() or character == "_")
        else f"_{ord(character):02x}_"
        for character in name
    )
    return safe or "_"


def _structural_tier_ids(visible: tuple[tuple[int, Tier], ...]) -> list[str]:
    """Return one collision-free sanitized id per visible tier, in order.

    Distinct sanitized names keep their sanitized form, preserving their bytes;
    a collision is broken with a
    deterministic numeric suffix.
    """
    used: set[str] = set()
    ids: list[str] = []
    for _tier_index, tier in visible:
        base = _sanitize_structural_id(tier.declaration.long_name)
        candidate = base
        suffix = 2
        while candidate in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        used.add(candidate)
        ids.append(candidate)
    return ids


def _resolve_binding_columns(
    binding: Callable[..., tuple[ClockCoordinate, ClockCoordinate]] | None,
    item: Item,
    reference: ItemRef,
    column_of: dict[ClockCoordinate, int],
) -> tuple[int, int]:
    """Resolve one item's binding to its ``(start, end)`` collapsed columns.

    Every visible non-clock item must be placed: the occupied-spine view has no
    untimed lane. The seam is hardened so a missing, malformed, mistyped,
    off-spine, or reversed placement is refused with the offending item named,
    never an incidental crash or partial output.
    """
    if binding is None:
        raise ValueError(
            f"item {reference.to_data()!r} has no clock placement: the occupied "
            "spine needs a binding callable that places every visible non-clock "
            "item"
        )
    placement = binding(item)
    if placement is None:
        raise ValueError(
            f"item {reference.to_data()!r} has no clock placement: the binding "
            "returned None, but the occupied spine has no untimed lane"
        )
    try:
        start_coordinate, end_coordinate = placement
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"binding for item {reference.to_data()!r} must return a "
            "(start, end) pair of ClockCoordinates"
        ) from error
    if not isinstance(start_coordinate, ClockCoordinate) or not isinstance(
        end_coordinate, ClockCoordinate
    ):
        raise ValueError(
            f"binding for item {reference.to_data()!r} must return ClockCoordinates, "
            f"got ({type(start_coordinate).__name__}, {type(end_coordinate).__name__})"
        )
    try:
        start_column = column_of[start_coordinate]
        end_column = column_of[end_coordinate]
    except KeyError as error:
        raise ValueError(
            f"clock placement {error.args[0]!r} for item {reference.to_data()!r} "
            "is not an occupied spine coordinate"
        ) from error
    if start_column > end_column:
        raise ValueError(
            f"binding for item {reference.to_data()!r} is reversed: start column "
            f"{start_column} is after end column {end_column}"
        )
    return start_column, end_column


def _structural_endpoint_id(
    graph: Graph,
    clock: ClockProfile,
    endpoint: ItemRef | DurableItemRef | DurableBoundaryRef,
    item_nodes: dict[ItemRef, str],
    declaration: QualifiedName,
) -> str:
    """Resolve a relation endpoint to a drawn item node, or refuse it clearly."""
    name = str(declaration.local_name)
    if isinstance(endpoint, ItemRef | DurableItemRef):
        resolved = graph.resolve_item(endpoint)
        if resolved.tier == clock.clock_tier:
            raise ValueError(
                f"relation {name!r} targets clock-tier item "
                f"{resolved.to_data()!r}; the clock tier is drawn only as the spine"
            )
        # ``resolve_item`` guarantees a real item on a real tier, and every tier
        # with items is visible, so its nodes are all in ``item_nodes``.
        return item_nodes[resolved]
    raise ValueError(
        f"relation {name!r} has a boundary endpoint {endpoint.to_data()!r}; the "
        "occupied-spine view relates item endpoints only"
    )


def _relation_style(
    presentation: DotPresentation | None, relation: PolyadicRelationInstance
) -> str | None:
    if presentation is not None and presentation.relation_style is not None:
        return presentation.relation_style(relation)
    return None


def _relation_label(
    presentation: DotPresentation | None, relation: PolyadicRelationInstance
) -> str:
    if presentation is not None and presentation.relation_name is not None:
        override = presentation.relation_name(relation)
        if override is not None:
            return override
    return str(relation.declaration.local_name)


def _arc_labeled(left: str, right: str, label: str) -> str:
    return (
        f"  {left} -> {right} "
        f'[label="{_quote(label, "relation name")}", '
        'color="#5555aa", constraint=false];'
    )


type _RenderedRelation = RelationInstance | PolyadicRelationInstance
type _RelationEndpoint = ItemRef | DurableItemRef | DurableBoundaryRef


def _emit_relation_edges(
    lines: list[str],
    relations: Iterable[_RenderedRelation],
    endpoint: Callable[[_RelationEndpoint, QualifiedName], str],
    arc: Callable[[_RenderedRelation, str, str], str],
    prepare: Callable[[_RenderedRelation], None] | None = None,
) -> None:
    """Emit relation edges in declaration and source-target order."""
    for relation in relations:
        if prepare is not None:
            prepare(relation)
        pairs: Iterable[tuple[_RelationEndpoint, _RelationEndpoint]]
        if isinstance(relation, RelationInstance):
            pairs = ((relation.left, relation.right),)
        else:
            pairs = (
                (source, target)
                for source in relation.sources
                for target in relation.targets
            )
        for left, right in pairs:
            lines.append(
                arc(
                    relation,
                    endpoint(left, relation.declaration),
                    endpoint(right, relation.declaration),
                )
            )


def _structural_relation_lines(
    lines: list[str],
    graph: Graph,
    clock: ClockProfile,
    item_nodes: dict[ItemRef, str],
    presentation: DotPresentation | None,
) -> None:
    """Emit relations for the occupied-spine view, refusing unrenderable ones.

    Boundary endpoints and clock-tier targets are refused (they have no drawn
    node), and a declared polyadic relation with no endpoints emits nothing. A
    polyadic relation the ``relation_style`` hook marks ``"bipartite"`` is drawn
    as individual parent-to-child edges under a ``// Declared relations.``
    header, labeled by ``relation_name``; every other relation renders as
    before.
    """

    def _endpoint(
        endpoint_ref: ItemRef | DurableItemRef | DurableBoundaryRef,
        declaration: QualifiedName,
    ) -> str:
        return _structural_endpoint_id(
            graph, clock, endpoint_ref, item_nodes, declaration
        )

    if graph.relations:
        lines.extend(("", "  // Declared bipartite relations."))
        _emit_relation_edges(
            lines,
            graph.relations,
            _endpoint,
            lambda relation, left, right: _arc(left, right, relation.declaration),
        )
    # Evaluate relation_style exactly once per non-empty polyadic relation and
    # cache it, so a stateful or non-deterministic hook cannot place a relation
    # in neither section (or both) across two separate evaluations.
    styled = tuple(
        (polyadic, _relation_style(presentation, polyadic))
        for polyadic in graph.polyadic_relations
        if polyadic.sources and polyadic.targets
    )
    fanned = tuple(polyadic for polyadic, style in styled if style != "bipartite")
    bipartite = tuple(polyadic for polyadic, style in styled if style == "bipartite")
    if fanned:
        lines.extend(("", "  // Declared polyadic relations."))
        _emit_relation_edges(
            lines,
            fanned,
            _endpoint,
            lambda relation, left, right: _arc(left, right, relation.declaration),
        )
    if bipartite:
        lines.extend(("", "  // Declared relations."))
        labels: dict[int, str] = {}

        def _prepare_labeled(relation: _RenderedRelation) -> None:
            assert isinstance(relation, PolyadicRelationInstance)
            labels[id(relation)] = _relation_label(presentation, relation)

        def _labeled(relation: _RenderedRelation, left: str, right: str) -> str:
            return _arc_labeled(left, right, labels[id(relation)])

        _emit_relation_edges(
            lines,
            bipartite,
            _endpoint,
            _labeled,
            _prepare_labeled,
        )


def _dumps_occupied_spine(
    graph: Graph,
    clock: ClockProfile,
    presentation: DotPresentation | None,
    binding: Callable[..., tuple[ClockCoordinate, ClockCoordinate]] | None,
    include_empty_tiers: bool,
) -> str:
    """Render a structural clock: spine plus item-anchored occupied columns.

    The clock tier is drawn only as the spine. Each visible non-clock item is
    placed at the collapsed columns named by ``binding`` (there is no untimed
    lane).

    Anchor invariant. A tier's items must be supplied in non-decreasing
    start-column order, which is validated; this keeps items sharing a column
    contiguous. A clock column occupied by one or more of a tier's items is
    anchored on the first such item (least item index); a column with none of
    that tier's items uses an invisible guide point. Every occupant is defined,
    joined into the tier's adjacency chain, and triggered from its column; the
    per-column anchor alone carries the invisible alignment (the tier's slot
    chain and the cross-tier registration chain). Co-located items -- whether
    two items of one tier at a tick or items of different tiers at a tick --
    are thus all drawn and aligned, with a single deterministic representative
    per column.
    """
    clock_coordinates = clock.coordinates
    clock_ids, clock_labels = _occupied_spine_identity(clock_coordinates)
    column_of = {
        coordinate: index for index, coordinate in enumerate(clock_coordinates)
    }
    column_count = len(clock_coordinates)

    lines = [
        "digraph tiergraph {",
        '  graph [rankdir=TB, newrank=true, ranksep="0.62 equally", nodesep=0.28, splines=line];',
        '  node [fontname="Helvetica"];',
        '  edge [fontname="Helvetica", fontsize=9];',
    ]

    lines.extend(("", "  // The clock spine is the total order.", "  { rank=same;"))
    lines.append('    score_start_clock [shape=plaintext, label="clock"];')
    for index in range(column_count):
        lines.append(
            f"    {clock_ids[index]} [shape=circle, width=0.46, fixedsize=true, "
            f'group="time_{index}", label="{clock_labels[index]}"];'
        )
    for left, right in zip(clock_ids, clock_ids[1:], strict=False):
        lines.append(f"    {left} -> {right} [weight=100];")
    lines.append("  }")

    visible = tuple(
        (tier_index, tier)
        for tier_index, tier in enumerate(graph.tiers)
        if tier.declaration.name != clock.clock_tier
        and (tier.items or include_empty_tiers)
    )
    tier_ids = _structural_tier_ids(visible)

    tier_labels: list[str] = []
    item_nodes: dict[ItemRef, str] = {}
    tier_starts: list[list[list[int]]] = []
    tier_anchor_lists: list[list[str]] = []

    for (tier_index, tier), safe_id in zip(visible, tier_ids, strict=True):
        tier_name = tier.declaration.name
        starts_at: list[list[int]] = [[] for _ in range(column_count)]
        item_columns: list[int] = []
        item_end_columns: list[int] = []
        for item_index, item in enumerate(tier.items):
            reference = ItemRef(tier_name, item_index)
            start_column, end_column = _resolve_binding_columns(
                binding, item, reference, column_of
            )
            if item_columns and start_column < item_columns[-1]:
                raise ValueError(
                    f"item {reference.to_data()!r} is placed at clock column "
                    f"{start_column}, before the previous item at column "
                    f"{item_columns[-1]}; occupied-spine items must be supplied "
                    "in non-decreasing clock order"
                )
            item_columns.append(start_column)
            item_end_columns.append(end_column)
            starts_at[start_column].append(item_index)
        for item_index in range(len(tier.items)):
            reference = ItemRef(tier_name, item_index)
            item_nodes[reference] = _resolve_node_id(
                presentation, reference, f"item_{tier_index}_{item_index}"
            )
        anchors = [
            item_nodes[ItemRef(tier_name, starts_at[column][0])]
            if starts_at[column]
            else f"guide_{safe_id}_{column}"
            for column in range(column_count)
        ]

        label_id = f"tier_label_{safe_id}"
        tier_labels.append(label_id)
        lines.extend(("", f"  subgraph tier_{safe_id} {{", "    rank=same;"))
        lines.append(
            f"    {label_id} [shape=plaintext, "
            f'label="{_resolve_tier_name(presentation, tier)}"];'
        )
        for column in range(column_count):
            if starts_at[column]:
                for item_index in starts_at[column]:
                    reference = ItemRef(tier_name, item_index)
                    label = _resolve_item_label(
                        presentation,
                        tier.items[item_index],
                        tier,
                        graph,
                        reference,
                        None,
                    )
                    lines.append(
                        f"    {item_nodes[reference]} [shape=box, "
                        f'group="time_{column}", label="{label}"];'
                    )
            else:
                lines.append(
                    f"    guide_{safe_id}_{column} [shape=point, width=0.01, "
                    f'label="", group="time_{column}", style=invis];'
                )
        for left, right in zip(anchors, anchors[1:], strict=False):
            lines.append(f"    {left} -> {right} [style=invis, weight=100];")
        for item_index in range(len(tier.items) - 1):
            left = item_nodes[ItemRef(tier_name, item_index)]
            right = item_nodes[ItemRef(tier_name, item_index + 1)]
            lines.append(
                f"    {left} -> {right} "
                '[color="#888888", penwidth=0.8, arrowsize=0.55, constraint=false];'
            )
        for item_index in range(len(tier.items)):
            if item_columns[item_index] != item_end_columns[item_index]:
                anchor = anchors[item_end_columns[item_index]]
                lines.append(
                    f"    {item_nodes[ItemRef(tier_name, item_index)]} -> {anchor} "
                    '[xlabel="extent", color="#777777", style=dashed, arrowhead=tee, '
                    "arrowsize=0.6, fontsize=8, constraint=false];"
                )
        lines.append("  }")
        tier_starts.append(starts_at)
        tier_anchor_lists.append(anchors)

    lines.extend(("", "  // The score brace joins lane starts in declaration order."))
    row_anchors = ["score_start_clock", *tier_labels]
    for upper, lower in zip(row_anchors, row_anchors[1:], strict=False):
        lines.append(
            f'  {upper} -> {lower} [dir=none, color="#333333", penwidth=2.4, weight=100];'
        )

    lines.extend(("", "  // Register every lane to the clock's time columns."))
    for column in range(column_count):
        chain = [clock_ids[column], *(anchors[column] for anchors in tier_anchor_lists)]
        for upper, lower in zip(chain, chain[1:], strict=False):
            lines.append(
                f"  {upper} -> {lower} [style=invis, weight=1000, arrowhead=none];"
            )

    lines.extend(("", "  // Trigger every event from the clock position it occupies."))
    # Order trigger edges by (coarse tick, tier declaration index, event index):
    # the coarse tick, not the collapsed column, so an item placed in a
    # refined gap still sorts with its tick. The edge source stays the item's own
    # collapsed column.
    triggers: list[tuple[int, int, int, str, str]] = []
    for (tier_index, tier), starts_at in zip(visible, tier_starts, strict=True):
        for column in range(column_count):
            coarse_tick = clock_coordinates[column].tick
            for item_index in starts_at[column]:
                reference = ItemRef(tier.declaration.name, item_index)
                triggers.append(
                    (
                        coarse_tick,
                        tier_index,
                        item_index,
                        clock_ids[column],
                        item_nodes[reference],
                    )
                )
    triggers.sort(key=lambda trigger: (trigger[0], trigger[1], trigger[2]))
    for _tick, _tier_index, _item_index, source, node in triggers:
        lines.append(
            f"  {source} -> {node} "
            '[color="#2f6f9f", penwidth=1.35, arrowsize=0.65, weight=100];'
        )

    _structural_relation_lines(lines, graph, clock, item_nodes, presentation)
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
    base_tier_index = next(
        index for index, tier in tiers if tier.declaration.name == profile.base_tier
    )
    boundary_ids = {
        span.start: f"boundary_{base_tier_index}_{span.start}"
        for span in view.spans
        if span.start == span.end
    }
    for index, node in sorted(boundary_ids.items()):
        lines.append(
            f'  {node} [shape=point, width=0.08, label="", xlabel="boundary {index}"];'
        )
    for span in view.spans:
        span_reference = next(
            reference
            for reference in node_ids
            if str(StructuralPathProfile().spell(ItemBinding(reference), graph))
            == span.path
        )
        if span.start == span.end:
            first = last = boundary_ids[span.start]
        else:
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
    reference: BoundaryRef | DurableBoundaryRef,
    coordinates: tuple[ClockCoordinate, ...],
) -> int:
    resolved = clock.graph.resolve_boundary(reference)
    if resolved.tier == clock.clock_tier:
        return resolved.index
    return coordinates.index(clock.refined_coordinate(resolved))


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
    tier: Tier,
    graph: Graph,
    reference: ItemRef,
    clock: ClockProfile | None,
) -> str:
    if presentation is not None and presentation.item_label is not None:
        override = presentation.item_label(item, tier)
        if override is not None:
            return _quote(override, "item label")
    return _item_label(graph, reference, clock)


def _occupied_spine_identity(
    coordinates: tuple[ClockCoordinate, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Name each occupied spine node from its tick, sharing single-gap ticks.

    A coarse tick with exactly one occupied coordinate is drawn as ``clock_{t}``
    labeled ``{t}``; a tick with several occupied gaps distinguishes each as
    ``clock_{t}_gap_{g}`` labeled ``{t}.{g}``.
    """
    counts = Counter(coordinate.tick for coordinate in coordinates)
    ids: list[str] = []
    labels: list[str] = []
    for coordinate in coordinates:
        if counts[coordinate.tick] == 1:
            ids.append(f"clock_{coordinate.tick}")
            labels.append(str(coordinate.tick))
        else:
            ids.append(f"clock_{coordinate.tick}_gap_{coordinate.gap}")
            labels.append(f"{coordinate.tick}.{coordinate.gap}")
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


def _coordinate_label(coordinate: ClockCoordinate) -> str:
    return (
        str(coordinate.tick)
        if coordinate.gap == 0
        else f"{coordinate.tick}.{coordinate.gap}"
    )


def _relation_lines(
    lines: list[str],
    graph: Graph,
    items: dict[ItemRef, str],
    boundaries: dict[BoundaryRef, str],
) -> None:
    def _endpoint(endpoint_ref: _RelationEndpoint, declaration: QualifiedName) -> str:
        del declaration
        return _endpoint_id(graph, endpoint_ref, items, boundaries)

    def _render(relation: _RenderedRelation, left: str, right: str) -> str:
        return _arc(left, right, relation.declaration)

    if graph.relations:
        lines.extend(("", "  // Declared bipartite relations."))
    _emit_relation_edges(lines, graph.relations, _endpoint, _render)
    if graph.polyadic_relations:
        lines.extend(("", "  // Declared polyadic relations."))
    _emit_relation_edges(lines, graph.polyadic_relations, _endpoint, _render)


def _endpoint_id(
    graph: Graph,
    endpoint: _RelationEndpoint,
    items: dict[ItemRef, str],
    boundaries: dict[BoundaryRef, str],
) -> str:
    if isinstance(endpoint, ItemRef | DurableItemRef):
        return items[graph.resolve_item(endpoint)]
    resolved = graph.resolve_boundary(endpoint)
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
