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
    PositionRef,
    QualifiedName,
    RelationEndpointRef,
)
from tiergraph.selection import Node, NodeKind, NodeSet


class WalkDirection(StrEnum):
    """Choose the stored or inverse direction of a declared relation."""

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
class Walk:
    """Declare a transitive walk along one bipartite relation.

    A bounded walk stops after ``cap`` relation steps.  An unbounded walk is
    admitted only when graph construction has validated the declaration's
    acyclicity promise.
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
        """Follow one edge layer and normalize resolved endpoint identities."""
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
