"""Bounded and acyclicity-backed walks over selected graph nodes."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from tiergraph.core import (
    BipartiteRelationDeclaration,
    DurablePositionRef,
    Graph,
    ItemRef,
    JsonValue,
    PolyadicRelationDeclaration,
    PositionRef,
    QualifiedName,
    RelationEndpointKind,
    RelationEndpointRef,
)
from tiergraph.selection import Node, NodeKind, NodeSet


class WalkDirection(StrEnum):
    """Choose the declared descending direction or its computed inverse view."""

    FORWARD = "forward"
    INVERSE = "inverse"


@dataclass(frozen=True, slots=True)
class WalkResult:
    """Return reached nodes and disclose whether a step cap stopped the walk."""

    nodes: NodeSet
    truncated: bool
    cap: int | None

    def to_data(self) -> dict[str, JsonValue]:
        """Return strict-JSON traversal data in canonical node order."""
        return {
            "nodes": self.nodes.to_data(),
            "truncated": self.truncated,
            "cap": self.cap,
        }


@dataclass(frozen=True, slots=True)
class NodeSequence:
    """Hold graph nodes without sorting or deduplicating them.

    Unlike :class:`NodeSet`, this value carries semantic sequence order and may
    contain the same node more than once. It deliberately provides no set
    algebra: callers must explicitly construct a ``NodeSet`` for set-valued
    reachability.
    """

    graph: Graph
    nodes: tuple[Node, ...]

    def to_data(self) -> list[JsonValue]:
        """Return nodes as strict-JSON data in their carried order."""
        return [node.to_data() for node in self.nodes]


@dataclass(frozen=True, slots=True)
class OrderedContainment:
    """Traverse one ordered, item-only polyadic containment relation.

    Descending order is exactly stored target incidence order. Descendants are
    depth-first pre-order and leaves are depth-first leaf order; repeated
    incidence remains repeated. Parents and ancestors are computed inverse
    fibers, so their result is intentionally a :class:`NodeSet`.
    """

    graph: Graph
    relation: QualifiedName
    _declaration: PolyadicRelationDeclaration = field(init=False, repr=False)

    def __post_init__(self) -> None:
        declaration = next(
            (
                candidate
                for candidate in self.graph.relation_declarations
                if candidate.name == self.relation
            ),
            None,
        )
        if not isinstance(declaration, PolyadicRelationDeclaration):
            raise ValueError(
                f"ordered containment relation {str(self.relation)!r} "
                "requires a polyadic declaration"
            )
        item_only = (RelationEndpointKind.ITEM,)
        if (
            declaration.sources.endpoint_kinds != item_only
            or declaration.targets.endpoint_kinds != item_only
        ):
            raise ValueError(
                f"ordered containment relation {str(self.relation)!r} "
                "requires item-only sides"
            )
        if not declaration.unique_sources:
            raise ValueError(
                f"ordered containment relation {str(self.relation)!r} "
                "requires source uniqueness"
            )
        if not declaration.acyclic:
            raise ValueError(
                f"ordered containment relation {str(self.relation)!r} "
                "requires declared acyclicity"
            )
        object.__setattr__(self, "_declaration", declaration)

    def _node(self, reference: ItemRef) -> Node:
        if reference not in self.graph.canonical_items():
            raise ValueError(
                f"ordered containment relation {str(self.relation)!r} received "
                f"item {reference.to_data()!r} outside its graph"
            )
        return Node(NodeKind.ITEM, reference)

    @staticmethod
    def _references(nodes: tuple[Node, ...]) -> tuple[ItemRef, ...]:
        return tuple(
            node.reference for node in nodes if isinstance(node.reference, ItemRef)
        )

    def direct_children(self, parent: ItemRef) -> NodeSequence:
        """Return direct children in declared target incidence order."""
        self._node(parent)
        children: list[Node] = []
        for instance in self.graph.polyadic_relations:
            if instance.declaration == self.relation and parent in instance.sources:
                children.extend(
                    self._node(target)
                    for target in instance.targets
                    if isinstance(target, ItemRef)
                )
        return NodeSequence(self.graph, tuple(children))

    def descendants(self, parent: ItemRef) -> NodeSequence:
        """Return descendants in depth-first pre-order, preserving repetition."""
        self._node(parent)

        def descend(item: ItemRef) -> tuple[Node, ...]:
            """Expand one item's ordered descendants recursively."""
            result: list[Node] = []
            for child in self._references(self.direct_children(item).nodes):
                result.append(self._node(child))
                result.extend(descend(child))
            return tuple(result)

        return NodeSequence(self.graph, descend(parent))

    def leaves(self, parent: ItemRef) -> NodeSequence:
        """Return descendant leaves, or the source itself when it has no children."""
        self._node(parent)

        def descend(item: ItemRef) -> tuple[Node, ...]:
            """Expand one item's ordered leaves recursively."""
            children = self._references(self.direct_children(item).nodes)
            if not children:
                return (self._node(item),)
            return tuple(node for child in children for node in descend(child))

        return NodeSequence(self.graph, descend(parent))

    def parents(self, child: ItemRef) -> NodeSet:
        """Return the canonical set-valued inverse fiber over one child."""
        self._node(child)
        return NodeSet(
            self.graph,
            tuple(
                self._node(source)
                for instance in self.graph.polyadic_relations
                if instance.declaration == self.relation and child in instance.targets
                for source in instance.sources
                if isinstance(source, ItemRef)
            ),
        )

    def ancestors(self, child: ItemRef) -> NodeSet:
        """Return the transitive inverse fiber as a canonical reachable set."""
        self._node(child)
        reached = NodeSet(self.graph, ())
        frontier = self.parents(child)
        while frontier.nodes:
            fresh = frontier - reached
            if not fresh.nodes:
                break
            reached = reached | fresh
            frontier = NodeSet(
                self.graph,
                tuple(
                    parent
                    for item in self._references(fresh.nodes)
                    for parent in self.parents(item).nodes
                ),
            )
        return reached


@dataclass(frozen=True, slots=True)
class Walk:
    """Declare a transitive walk along one bipartite relation.

    A bounded walk stops after ``cap`` relation steps.  An unbounded walk is
    admitted only when graph construction has validated the declaration's
    acyclicity promise.  Forward access reads the stored relation and inverse
    access computes its fiber over each selected item.  That fiber is a set:
    deduplication is a consequence of relational inversion, not an accommodation
    for any particular domain whose morphs happen to cross-cut.
    """

    source: NodeSet
    relation: QualifiedName
    direction: WalkDirection
    cap: int | None = None
    _declaration: BipartiteRelationDeclaration = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Refuse undeclared relations, invalid caps, and unsafe unbounded walks."""
        if not isinstance(self.direction, WalkDirection):
            raise ValueError(
                f"walk relation {str(self.relation)!r} has invalid direction "
                f"{self.direction!r}"
            )
        declaration = next(
            (
                candidate
                for candidate in self.source.graph.relation_declarations
                if candidate.name == self.relation
            ),
            None,
        )
        if not isinstance(declaration, BipartiteRelationDeclaration):
            raise ValueError(
                f"walk relation {str(self.relation)!r} is not a declared bipartite relation"
            )
        if self.cap is not None and (
            isinstance(self.cap, bool) or not isinstance(self.cap, int) or self.cap < 0
        ):
            raise ValueError(
                f"walk relation {str(self.relation)!r} has invalid cap {self.cap!r}; "
                "expected a nonnegative integer"
            )
        if self.cap is None and not declaration.acyclic:
            raise ValueError(
                f"unbounded walk relation {str(self.relation)!r} is not declared acyclic"
            )
        object.__setattr__(self, "_declaration", declaration)

    def evaluate(self) -> WalkResult:
        """Return the transitive reachable set, excluding the source selection."""
        graph = self.source.graph
        frontier = self.source
        reached = NodeSet(graph, ())
        steps = 0
        while frontier.nodes and (self.cap is None or steps < self.cap):
            following = self._step(frontier)
            frontier = following - reached - self.source
            reached = reached | frontier
            steps += 1
        return WalkResult(reached, bool(frontier.nodes), self.cap)

    def _step(self, source: NodeSet) -> NodeSet:
        """Follow stored incidence forward or compute its inverse fiber."""
        graph = source.graph
        admitted = set(source.nodes)
        targets: list[Node] = []
        for instance in graph.relations:
            if instance.declaration != self.relation:
                continue
            left = _endpoint_node(graph, instance.left)
            right = _endpoint_node(graph, instance.right)
            origin, target = (
                (left, right)
                if self.direction is WalkDirection.FORWARD
                else (right, left)
            )
            if origin in admitted:
                targets.append(target)
        return NodeSet(graph, tuple(targets))


def _endpoint_node(graph: Graph, reference: RelationEndpointRef) -> Node:
    """Resolve an anchored endpoint to the structural identity used by selections."""
    if isinstance(reference, ItemRef):
        return Node(NodeKind.ITEM, reference)
    assert isinstance(reference, DurablePositionRef)
    resolved: PositionRef = graph.resolve_position(reference)
    return Node(NodeKind.POSITION, resolved)
