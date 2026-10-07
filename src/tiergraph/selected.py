"""Apply one edit callback to a materialized graph selection."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Never, cast, get_args

from tiergraph.budget import WorkBudget, WorkMeter, _ChargeMeter, _metered
from tiergraph.core import (
    BoundaryRef,
    Displacement,
    Graph,
    GraphEditor,
    ItemRef,
    QualifiedName,
    Refusal,
    RefusalStage,
)
from tiergraph.path import PathProfile
from tiergraph.selection import Node, NodeKind, NodeSet, Selector, evaluate_selection

if TYPE_CHECKING:
    from tiergraph.match import SpanMatches


_SELECTOR_TYPES = get_args(Selector.__value__)


def apply_selected(
    graph: Graph,
    selected: NodeSet | Selector | SpanMatches,
    operation: Callable[[GraphEditor, Node], object],
    *,
    path_profile: PathProfile | None = None,
    budget: WorkBudget | WorkMeter | None = None,
) -> Graph:
    """Apply ``operation`` once to every materialized selected node.

    ``selected`` may be a graph-free selector, a node set belonging to
    ``graph``, or an exhaustive set of regular-pattern spans. Selectors are
    evaluated once before editing starts. ``path_profile`` applies only to this
    selector form. Span items are combined as a set, so nodes shared by
    overlapping spans are edited once. Spans carry no graph provenance; their
    item coordinates are validated and interpreted against ``graph``.

    Nodes are visited in reverse canonical order. Before every call, the
    original node is remapped through all preceding edits. This puts relation
    instances before their endpoints, tiers after their contents, and positions
    within one carrier in descending order while keeping arbitrary positional
    shifts safe. If a preceding callback removes a later selected node, the
    bulk edit refuses instead of retargeting the callback.

    Every callback receives the same editor. Outside the callback,
    ``apply_selected`` constructs one editor and performs one final validation.
    The input graph is immutable, so a callback refusal, invalid final graph, or
    exhausted budget exposes no partial graph result. The callback's return
    value is ignored.

    One optional work budget covers selector evaluation, span materialization,
    and one step per callback. Nested budget-aware work in the callback shares
    the active meter. This graph-level entry point records no journal and
    applies no clock rebinding policy. Callers requiring either must perform the
    equivalent selected structural operations through the appropriate editor.
    """
    if not isinstance(graph, Graph):
        raise TypeError("apply_selected graph must be a Graph")
    if not callable(operation):
        raise TypeError("apply_selected operation must be callable")
    shared = WorkMeter(budget) if isinstance(budget, WorkBudget) else budget
    with _metered(shared, "edit.apply_selected") as metered:
        nodes = _materialize(graph, selected, path_profile, shared, metered.meter)
        for node in nodes.nodes:
            _validate_node(graph, node)
        editor = graph.edit()
        for original in reversed(nodes.nodes):
            if metered.meter is not None:
                metered.meter.charge(1)
            current = _remap_node(editor, original, editor.displacement())
            operation(editor, current)
            if metered.meter is not None:
                metered.meter.charge(0)
        return editor.freeze()


def _materialize(
    graph: Graph,
    selected: NodeSet | Selector | SpanMatches,
    path_profile: PathProfile | None,
    budget: WorkMeter | None,
    active: _ChargeMeter | None,
) -> NodeSet:
    """Turn every accepted selection form into one node set over ``graph``."""
    if isinstance(selected, NodeSet):
        if path_profile is not None:
            _path_profile_refusal()
        if selected.graph is not graph:
            raise Refusal(
                RefusalStage.SEMANTICS,
                "selected node set must belong to the graph being edited",
            )
        return selected

    from tiergraph.match import Extent, SpanMatches  # noqa: PLC0415

    if isinstance(selected, SpanMatches):
        if path_profile is not None:
            _path_profile_refusal()
        if selected.extent is not Extent.EXHAUSTIVE:
            raise Refusal(
                RefusalStage.SEMANTICS,
                "selected match spans must be exhaustive before editing",
            )
        items = tuple(node for match in selected.matches for node in match.items)
        for node in items:
            _validate_node(graph, node)
        if active is not None:
            # Account for flattening and the comparison work used to canonicalize
            # a mixed node set. The callback charge is separate.
            active.charge(len(items) * max(1, len(items).bit_length()))
        return NodeSet(graph, items)

    if not isinstance(selected, _SELECTOR_TYPES):
        raise TypeError(
            "apply_selected selection must be a NodeSet, Selector, or SpanMatches"
        )
    selector = selected
    if path_profile is None:
        return evaluate_selection(graph, selector, budget=budget)
    return evaluate_selection(
        graph,
        selector,
        path_profile=path_profile,
        budget=budget,
    )


def _path_profile_refusal() -> Never:
    """Refuse a path interpretation when selection is already materialized."""
    raise Refusal(
        RefusalStage.SEMANTICS,
        "apply_selected path_profile applies only when selection is a selector",
    )


def _validate_node(graph: Graph, node: Node) -> None:
    """Require one selected node to identify a node in the input graph."""
    reference = node.reference
    if node.kind is NodeKind.DOCUMENT:
        if reference is not None:
            _invalid_coordinate(node, "None")
        return
    if node.kind is NodeKind.TIER:
        if not isinstance(reference, QualifiedName):
            _invalid_coordinate(node, "QualifiedName")
        present = reference in graph._tiers_by_name
    elif node.kind is NodeKind.RELATION_DECLARATION:
        if not isinstance(reference, QualifiedName):
            _invalid_coordinate(node, "QualifiedName")
        present = any(
            declaration.name == reference for declaration in graph.relation_declarations
        )
    elif node.kind is NodeKind.ITEM:
        if not isinstance(reference, ItemRef):
            _invalid_coordinate(node, "ItemRef")
        tier = graph._tiers_by_name.get(reference.tier)
        present = tier is not None and 0 <= reference.index < len(tier.items)
    elif node.kind is NodeKind.BOUNDARY:
        if not isinstance(reference, BoundaryRef):
            _invalid_coordinate(node, "BoundaryRef")
        tier = graph._tiers_by_name.get(reference.tier)
        present = tier is not None and 0 <= reference.index <= len(tier.items)
    elif node.kind is NodeKind.RELATION_INSTANCE:
        if isinstance(reference, bool) or not isinstance(reference, int):
            _invalid_coordinate(node, "int")
        present = 0 <= reference < len(graph.relations)
    elif node.kind is NodeKind.POLYADIC_RELATION_INSTANCE:
        if isinstance(reference, bool) or not isinstance(reference, int):
            _invalid_coordinate(node, "int")
        present = 0 <= reference < len(graph.polyadic_relations)
    else:
        raise Refusal(
            RefusalStage.VALUE,
            f"selected node has unsupported kind {node.kind!r}",
        )
    if not present:
        raise Refusal(
            RefusalStage.REFERENCE,
            f"selected {_kind_name(node)} {reference!s} does not identify a node "
            "in the graph being edited",
        )


def _invalid_coordinate(node: Node, expected: str) -> Never:
    """Refuse a coordinate whose shape does not match its node kind."""
    raise Refusal(
        RefusalStage.VALUE,
        f"selected {_kind_name(node)} node requires coordinate type {expected}; "
        f"got {type(node.reference).__name__}",
    )


def _kind_name(node: Node) -> str:
    """Return one node kind's stable diagnostic spelling."""
    return node.kind.value.replace("_", " ")


def _remap_node(editor: GraphEditor, node: Node, displacement: Displacement) -> Node:
    """Resolve one original selected node in the current candidate graph."""
    reference = node.reference
    if node.kind is NodeKind.DOCUMENT:
        return node
    if node.kind is NodeKind.TIER:
        if not any(tier.declaration.name == reference for tier in editor._tiers):
            _departed(node)
        return node
    if node.kind is NodeKind.RELATION_DECLARATION:
        if not any(
            declaration.name == reference
            for declaration in editor._relation_declarations
        ):
            _departed(node)
        return node
    if node.kind is NodeKind.ITEM:
        reference = cast(ItemRef, reference)
        item_target = displacement.items.get(reference)
        if item_target is None:
            _departed(node)
        return Node(node.kind, item_target)
    if node.kind is NodeKind.BOUNDARY:
        reference = cast(BoundaryRef, reference)
        boundary_target = displacement.boundaries.get(reference)
        if boundary_target is None:
            _departed(node)
        return Node(node.kind, boundary_target)
    reference = cast(int, reference)
    positions = (
        displacement.relations
        if node.kind is NodeKind.RELATION_INSTANCE
        else displacement.polyadic_relations
    )
    relation_target = positions.get(reference)
    if relation_target is None:
        _departed(node)
    return Node(node.kind, relation_target)


def _departed(node: Node) -> Never:
    """Refuse rather than redirect a selected node removed by an earlier edit."""
    raise Refusal(
        RefusalStage.REFERENCE,
        f"selected {_kind_name(node)} {node.reference!s} "
        "was removed by an earlier bulk edit",
    )


__all__ = ["apply_selected"]
