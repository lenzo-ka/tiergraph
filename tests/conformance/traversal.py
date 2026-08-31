"""Reusable laws for bounded and acyclicity-backed graph traversal."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

import pytest

from tiergraph import (
    BipartiteRelationDeclaration,
    BoundarySide,
    DurableBoundaryRef,
    DurableItemRef,
    Graph,
    Item,
    ItemRef,
    NamespaceDeclaration,
    Node,
    NodeKind,
    NodeSet,
    QualifiedName,
    RelationEndpointKind,
    RelationInstance,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    Walk,
    WalkDirection,
    WalkResult,
)

WalkFactory = Callable[[NodeSet, QualifiedName, WalkDirection, int | None], Walk]


@dataclass(frozen=True)
class TraversalLawSuite:
    """Apply traversal laws through a replaceable walk construction boundary."""

    build: WalkFactory
    namespace: str = "urn:test:traversal"

    def name(self, local: str) -> QualifiedName:
        """Return a name in the fixture namespace."""
        return QualifiedName(self.namespace, local)

    def graph(self, *, acyclic: bool) -> Graph:
        """Return a diamond, with an optional closing edge for bounded walks."""
        node_tier = self.name("nodes")
        node_type = self.name("node")
        relation = BipartiteRelationDeclaration(
            self.name("contains"), node_type, node_type, acyclic=acyclic
        )
        edges = [
            RelationInstance(
                relation.name, ItemRef(node_tier, 0), ItemRef(node_tier, 1)
            ),
            RelationInstance(
                relation.name, ItemRef(node_tier, 0), ItemRef(node_tier, 2)
            ),
            RelationInstance(
                relation.name, ItemRef(node_tier, 1), ItemRef(node_tier, 3)
            ),
            RelationInstance(
                relation.name, ItemRef(node_tier, 2), ItemRef(node_tier, 3)
            ),
        ]
        if not acyclic:
            edges.append(
                RelationInstance(
                    relation.name, ItemRef(node_tier, 3), ItemRef(node_tier, 0)
                )
            )
        return Graph(
            (NamespaceDeclaration("t", self.namespace),),
            (
                Tier(
                    TierDeclaration(node_tier, "Nodes"),
                    tuple(Item(f"node-{index}") for index in range(4)),
                ),
            ),
            (
                SimpleRelationDeclaration(self.name("members"), node_tier, node_type),
                relation,
            ),
            tuple(edges),
        )

    def incidence_graph(self, edges: tuple[tuple[int, int], ...]) -> Graph:
        """Construct a five-item graph whose only stored incidence is ``edges``."""
        node_tier = self.name("nodes")
        node_type = self.name("node")
        relation = BipartiteRelationDeclaration(
            self.name("contains"), node_type, node_type
        )
        return Graph(
            (NamespaceDeclaration("t", self.namespace),),
            (
                Tier(
                    TierDeclaration(node_tier, "Nodes"),
                    tuple(Item(f"node-{index}") for index in range(5)),
                ),
            ),
            (
                SimpleRelationDeclaration(self.name("members"), node_tier, node_type),
                relation,
            ),
            tuple(
                RelationInstance(
                    relation.name,
                    ItemRef(node_tier, parent),
                    ItemRef(node_tier, child),
                )
                for parent, child in edges
            ),
        )

    def check_inverse_fiber(self, edges: tuple[tuple[int, int], ...]) -> None:
        """Every ascending answer is exactly the set-valued fiber of descent."""
        graph = self.incidence_graph(edges)
        for child in range(5):
            upward = self.walk(self.selection(graph, child), WalkDirection.INVERSE, 1)
            expected = self.selection(
                graph, *(parent for parent, target in edges if target == child)
            )
            assert upward.nodes == expected

    def selection(self, graph: Graph, *indices: int) -> NodeSet:
        """Select fixture items by structural coordinate."""
        return NodeSet(
            graph,
            tuple(
                Node(NodeKind.ITEM, ItemRef(self.name("nodes"), index))
                for index in indices
            ),
        )

    def walk(
        self,
        source: NodeSet,
        direction: WalkDirection,
        cap: int | None,
    ) -> WalkResult:
        """Construct and evaluate one fixture walk."""
        return self.build(source, self.name("contains"), direction, cap).evaluate()

    def check_diamond_and_inverse_sets(self) -> None:
        """A diamond deduplicates its join and inverse access retains both parents."""
        graph = self.graph(acyclic=True)
        downward = self.walk(self.selection(graph, 0), WalkDirection.FORWARD, None)
        assert downward.nodes.nodes == self.selection(graph, 1, 2, 3).nodes
        assert downward.truncated is False
        upward = self.walk(self.selection(graph, 3), WalkDirection.INVERSE, 1)
        assert upward.nodes.nodes == self.selection(graph, 1, 2).nodes
        assert len(upward.nodes.nodes) == 2
        assert upward.truncated is True

    def check_cyclic_cap_is_visible(self) -> None:
        """A cyclic walk stopping on its cap discloses both facts in public data."""
        graph = self.graph(acyclic=False)
        result = self.walk(self.selection(graph, 0), WalkDirection.FORWARD, 2)
        assert result.nodes.nodes == self.selection(graph, 1, 2, 3).nodes
        assert result.truncated is True
        assert result.cap == 2
        encoded = json.dumps(
            result.to_data(), sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        assert json.loads(encoded) == result.to_data()
        complete = self.walk(self.selection(graph, 0), WalkDirection.FORWARD, 4)
        assert complete.nodes.nodes == self.selection(graph, 1, 2, 3).nodes
        assert complete.truncated is False

    def check_acyclic_root_walk_terminates(self) -> None:
        """Validated acyclicity admits an unbounded inverse walk without truncation."""
        graph = self.graph(acyclic=True)
        result = self.walk(self.selection(graph, 3), WalkDirection.INVERSE, None)
        assert result.nodes.nodes == self.selection(graph, 0, 1, 2).nodes
        assert result.truncated is False
        assert result.cap is None

    def check_unbounded_refusal_names_relation(self) -> None:
        """A near-valid unbounded cyclic walk refuses its relation at construction."""
        graph = self.graph(acyclic=False)
        self.walk(self.selection(graph, 0), WalkDirection.FORWARD, 1)
        with pytest.raises(ValueError, match=r"contains.*not declared acyclic"):
            self.build(
                self.selection(graph, 0),
                self.name("contains"),
                WalkDirection.FORWARD,
                None,
            )

    def check_anchored_boundaries_resolve(self) -> None:
        """Traversal returns the structural boundary denoted by an anchored endpoint."""
        graph = self.graph(acyclic=True)
        boundary_relation = BipartiteRelationDeclaration(
            self.name("cue"),
            self.name("node"),
            self.name("node"),
            right_endpoint=RelationEndpointKind.BOUNDARY,
            acyclic=True,
        )
        extended = Graph(
            graph.namespaces,
            graph.tiers,
            (*graph.relation_declarations, boundary_relation),
            (
                *graph.relations,
                RelationInstance(
                    boundary_relation.name,
                    ItemRef(self.name("nodes"), 0),
                    DurableBoundaryRef(DurableItemRef("node-2"), BoundarySide.BEFORE),
                ),
            ),
        )
        result = self.build(
            self.selection(extended, 0),
            boundary_relation.name,
            WalkDirection.FORWARD,
            None,
        ).evaluate()
        assert result.nodes.nodes == (
            Node(
                NodeKind.BOUNDARY,
                extended.resolve_boundary(
                    DurableBoundaryRef(DurableItemRef("node-2"), BoundarySide.BEFORE)
                ),
            ),
        )

    def check_construction_guards(self) -> None:
        """Near-valid relation and cap mistakes refuse with their offending values."""
        graph = self.graph(acyclic=True)
        source = self.selection(graph, 0)
        with pytest.raises(ValueError, match="missing"):
            self.build(source, self.name("missing"), WalkDirection.FORWARD, 1)
        with pytest.raises(ValueError, match=r"members.*bipartite"):
            self.build(source, self.name("members"), WalkDirection.FORWARD, 1)
        with pytest.raises(ValueError, match="sideways"):
            self.build(source, self.name("contains"), "sideways", 1)  # type: ignore[arg-type]
        for cap in (-1, True, 1.5):
            with pytest.raises(ValueError, match=repr(cap)):
                self.build(source, self.name("contains"), WalkDirection.FORWARD, cap)  # type: ignore[arg-type]
        empty = self.walk(source, WalkDirection.FORWARD, 0)
        assert not empty.nodes.nodes
        assert empty.truncated is True
        assert empty.cap == 0
