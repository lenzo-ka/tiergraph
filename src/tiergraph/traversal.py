"""Bounded and acyclicity-backed walks over selected graph nodes."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from tiergraph.core import (
    BipartiteRelationDeclaration,
    BoundaryRef,
    DurableBoundaryRef,
    DurableItemRef,
    Graph,
    ItemRef,
    JsonValue,
    PolyadicRelationDeclaration,
    QualifiedName,
    RelationEndpointKind,
    RelationEndpointRef,
)
from tiergraph.selection import Node, NodeKind, NodeSet

type TraversalEndpointRef = ItemRef | DurableItemRef | BoundaryRef | DurableBoundaryRef


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


class PolyadicSide(StrEnum):
    """Choose one stored side of a polyadic relation declaration."""

    SOURCES = "sources"
    TARGETS = "targets"


@dataclass(frozen=True, slots=True)
class PolyadicIncidence:
    """Hold one instance's graph-local index and both sides in stored order.

    Sides are named for the declaration, not for a traversal direction, so
    ``sources`` and ``targets`` mean the same thing whichever way a caller
    walks.  Each side is a :class:`NodeSequence` because its order is graph
    content: a correspondence that reorders its two sides is a different fact
    from one that does not, and a pair-per-endpoint reading would lose that.
    """

    index: int
    sources: NodeSequence
    targets: NodeSequence

    def to_data(self) -> dict[str, JsonValue]:
        """Return the index and both ordered sides as strict-JSON data."""
        return {
            "index": self.index,
            "sources": self.sources.to_data(),
            "targets": self.targets.to_data(),
        }


@dataclass(frozen=True, slots=True)
class OrderedPolyadicTraversal:
    """Traverse between either pair of sides of one ordered polyadic relation.

    Direct and transitive results retain instance order, opposite-side endpoint
    order, and repetition.  Relational inversion is set-valued; callers that
    need stored order can instead request the opposite sequence of one instance.
    """

    graph: Graph
    relation: QualifiedName
    source_side: PolyadicSide
    target_side: PolyadicSide
    _declaration: PolyadicRelationDeclaration = field(init=False, repr=False)
    _containment_profile: bool = field(
        init=False, default=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if not isinstance(self.source_side, PolyadicSide):
            raise ValueError(
                f"ordered polyadic relation {str(self.relation)!r} has invalid "
                f"source side {self.source_side!r}"
            )
        if not isinstance(self.target_side, PolyadicSide):
            raise ValueError(
                f"ordered polyadic relation {str(self.relation)!r} has invalid "
                f"target side {self.target_side!r}"
            )
        if self.source_side is self.target_side:
            raise ValueError(
                f"ordered polyadic relation {str(self.relation)!r} requires "
                f"distinct source and target sides; both are {self.source_side.value!r}"
            )
        declaration = self._live_declaration()
        object.__setattr__(self, "_declaration", declaration)

    def _live_declaration(self) -> PolyadicRelationDeclaration:
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
                f"ordered polyadic relation {str(self.relation)!r} requires "
                "a polyadic declaration"
            )
        return declaration

    def _node(self, reference: TraversalEndpointRef, subject: str) -> Node:
        try:
            if isinstance(reference, ItemRef | DurableItemRef):
                resolved: ItemRef | BoundaryRef = self.graph.resolve_item(reference)
                kind = NodeKind.ITEM
            elif isinstance(reference, BoundaryRef | DurableBoundaryRef):
                resolved = self.graph.resolve_boundary(reference)
                kind = NodeKind.POSITION
            else:
                raise TypeError(f"unsupported endpoint type {type(reference).__name__}")
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"ordered polyadic relation {str(self.relation)!r} {subject} "
                f"{reference!r} is outside its graph"
            ) from error
        return Node(kind, resolved)

    def _instances(
        self, *, check_cycle: bool
    ) -> tuple[tuple[int, tuple[Node, ...], tuple[Node, ...]], ...]:
        declaration = self._live_declaration()
        result: list[tuple[int, tuple[Node, ...], tuple[Node, ...]]] = []
        for instance_index, instance in enumerate(self.graph.polyadic_relations):
            if instance.declaration != self.relation:
                continue
            resolved_sides: list[tuple[Node, ...]] = []
            for side_name, endpoints in (
                (PolyadicSide.SOURCES, instance.sources),
                (PolyadicSide.TARGETS, instance.targets),
            ):
                side = getattr(declaration, side_name.value)
                label = side_name.value[:-1]
                if not endpoints:
                    if not side.allow_empty:
                        if self._containment_profile:
                            raise ValueError(
                                f"ordered containment relation {str(self.relation)!r} "
                                f"instance {instance_index} has an empty {label} side"
                            )
                        raise ValueError(
                            f"ordered polyadic relation {str(self.relation)!r} "
                            f"instance {instance_index} has an empty {label} side"
                        )
                    resolved_sides.append(())
                    continue
                if len(endpoints) < side.minimum or (
                    side.maximum is not None and len(endpoints) > side.maximum
                ):
                    if self._containment_profile:
                        raise ValueError(
                            f"ordered containment relation {str(self.relation)!r} "
                            f"instance {instance_index} {label} arity "
                            f"{len(endpoints)} is outside declared bounds "
                            f"{side.minimum}..{side.maximum}"
                        )
                    raise ValueError(
                        f"ordered polyadic relation {str(self.relation)!r} instance "
                        f"{instance_index} {label} arity {len(endpoints)} is outside "
                        f"declared bounds {side.minimum}..{side.maximum}"
                    )
                nodes: list[Node] = []
                for endpoint_index, endpoint in enumerate(endpoints):
                    if self._containment_profile:
                        if not isinstance(endpoint, ItemRef):
                            raise ValueError(
                                f"ordered containment relation "
                                f"{str(self.relation)!r} instance {instance_index} "
                                f"{label} {endpoint_index} is not an item"
                            )
                        if endpoint not in self.graph.canonical_items():
                            raise ValueError(
                                f"ordered containment relation "
                                f"{str(self.relation)!r} instance {instance_index} "
                                f"{label} {str(endpoint)!r} is outside its graph"
                            )
                        node = Node(NodeKind.ITEM, endpoint)
                    else:
                        node = self._node(
                            endpoint,
                            f"instance {instance_index} {label} endpoint {endpoint_index}",
                        )
                    expected_kind = (
                        RelationEndpointKind.ITEM
                        if node.kind is NodeKind.ITEM
                        else RelationEndpointKind.BOUNDARY
                    )
                    if expected_kind not in side.endpoint_kinds:
                        if side.endpoint_kinds == (RelationEndpointKind.ITEM,):
                            detail = "is not an item"
                        else:
                            detail = "is not a boundary"
                        raise ValueError(
                            f"ordered polyadic relation {str(self.relation)!r} instance "
                            f"{instance_index} {label} {endpoint_index} "
                            f"{str(endpoint)!r} {detail}"
                        )
                    reference = node.reference
                    assert isinstance(reference, ItemRef | BoundaryRef)
                    if side.tiers is not None and reference.tier not in side.tiers:
                        raise ValueError(
                            f"ordered polyadic relation {str(self.relation)!r} instance "
                            f"{instance_index} {label} endpoint {endpoint_index} "
                            f"{str(endpoint)!r} belongs to tier "
                            f"{str(reference.tier)!r}; tier is not allowed"
                        )
                    nodes.append(node)
                resolved_sides.append(tuple(nodes))
            result.append((instance_index, resolved_sides[0], resolved_sides[1]))
        if check_cycle:
            self._require_acyclic(tuple(result))
        return tuple(result)

    def _require_acyclic(
        self, instances: tuple[tuple[int, tuple[Node, ...], tuple[Node, ...]], ...]
    ) -> None:
        outgoing: dict[Node, list[tuple[int, Node]]] = {}
        for instance_index, sources, targets in instances:
            origin = sources if self.source_side is PolyadicSide.SOURCES else targets
            opposite = targets if self.target_side is PolyadicSide.TARGETS else sources
            for source in origin:
                outgoing.setdefault(source, []).extend(
                    (instance_index, target) for target in opposite
                )
        visited: set[Node] = set()
        for root in tuple(outgoing):
            if root in visited:
                continue
            visiting = {root}
            stack: list[tuple[Node, int]] = [(root, 0)]
            while stack:
                node, index = stack[-1]
                edges = outgoing.get(node, [])
                if index == len(edges):
                    stack.pop()
                    visiting.remove(node)
                    visited.add(node)
                    continue
                instance_index, target = edges[index]
                stack[-1] = (node, index + 1)
                if target in visiting:
                    if self._containment_profile:
                        reference = target.reference
                        assert isinstance(reference, ItemRef)
                        raise ValueError(
                            f"ordered containment relation {str(self.relation)!r} "
                            f"instance {instance_index} closes a cycle at "
                            f"{str(reference)!r}"
                        )
                    raise ValueError(
                        f"ordered polyadic relation {str(self.relation)!r} instance "
                        f"{instance_index} closes a cycle at {str(target.reference)!r}"
                    )
                if target not in visited:
                    visiting.add(target)
                    stack.append((target, 0))

    def _origin(self, reference: TraversalEndpointRef, side_name: PolyadicSide) -> Node:
        node = self._node(reference, "origin")
        side = getattr(self._live_declaration(), side_name.value)
        kind = (
            RelationEndpointKind.ITEM
            if node.kind is NodeKind.ITEM
            else RelationEndpointKind.BOUNDARY
        )
        if kind not in side.endpoint_kinds:
            raise ValueError(
                f"ordered polyadic relation {str(self.relation)!r} origin "
                f"{reference!r} has kind {kind.value!r}, which is not admitted "
                f"by its {side_name.value[:-1]} side"
            )
        resolved = node.reference
        assert isinstance(resolved, ItemRef | BoundaryRef)
        if side.tiers is not None and resolved.tier not in side.tiers:
            raise ValueError(
                f"ordered polyadic relation {str(self.relation)!r} origin "
                f"{reference!r} belongs to tier {str(resolved.tier)!r}, which is "
                f"not admitted by its {side_name.value[:-1]} side"
            )
        return node

    def direct(self, origin: TraversalEndpointRef) -> NodeSequence:
        """Return one ordered step from ``origin``, retaining all incidence."""
        source = self._origin(origin, self.source_side)
        result: list[Node] = []
        for _, sources, targets in self._instances(check_cycle=False):
            selected = sources if self.source_side is PolyadicSide.SOURCES else targets
            opposite = targets if self.target_side is PolyadicSide.TARGETS else sources
            for candidate in selected:
                if candidate == source:
                    result.extend(opposite)
        return NodeSequence(self.graph, tuple(result))

    def transitive(self, origin: TraversalEndpointRef) -> NodeSequence:
        """Return depth-first pre-order reachability in stored incidence order."""
        declaration = self._live_declaration()
        if not declaration.acyclic:
            raise ValueError(
                f"ordered polyadic relation {str(self.relation)!r} is not declared acyclic"
            )
        source = self._origin(origin, self.source_side)
        instances = self._instances(check_cycle=True)
        outgoing: dict[Node, list[Node]] = {}
        for _, sources, targets in instances:
            selected = sources if self.source_side is PolyadicSide.SOURCES else targets
            opposite = targets if self.target_side is PolyadicSide.TARGETS else sources
            for candidate in selected:
                outgoing.setdefault(candidate, []).extend(opposite)
        result: list[Node] = []
        stack = list(reversed(outgoing.get(source, ())))
        while stack:
            target = stack.pop()
            result.append(target)
            stack.extend(reversed(outgoing.get(target, ())))
        return NodeSequence(self.graph, tuple(result))

    def inverse(self, endpoint: TraversalEndpointRef) -> NodeSet:
        """Return the deduplicated computed fiber over the target endpoint."""
        target = self._origin(endpoint, self.target_side)
        result: list[Node] = []
        for _, sources, targets in self._instances(check_cycle=False):
            selected = sources if self.source_side is PolyadicSide.SOURCES else targets
            opposite = targets if self.target_side is PolyadicSide.TARGETS else sources
            if target in opposite:
                result.extend(selected)
        return NodeSet(self.graph, tuple(result))

    def instances(self) -> tuple[PolyadicIncidence, ...]:
        """Return every validated instance of this relation in stored order.

        Origin-keyed steps answer "what does this endpoint correspond to"; a
        correspondence read as a whole, one ordered side against another with
        no positional pairing between them, has no origin to key on, so it is
        reachable only by enumeration.  Both sides keep their stored order.
        """
        return tuple(
            PolyadicIncidence(
                index,
                NodeSequence(self.graph, sources),
                NodeSequence(self.graph, targets),
            )
            for index, sources, targets in self._instances(check_cycle=False)
        )

    def stored_opposite(self, instance_index: int) -> NodeSequence:
        """Return one instance's stored target-side sequence without inversion."""
        if isinstance(instance_index, bool) or not isinstance(instance_index, int):
            raise ValueError(
                f"ordered polyadic relation {str(self.relation)!r} has invalid "
                f"instance index {instance_index!r}"
            )
        for index, sources, targets in self._instances(check_cycle=False):
            if index == instance_index:
                opposite = (
                    targets if self.target_side is PolyadicSide.TARGETS else sources
                )
                return NodeSequence(self.graph, opposite)
        raise ValueError(
            f"ordered polyadic relation {str(self.relation)!r} has no instance "
            f"at index {instance_index}"
        )


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
    _traversal: OrderedPolyadicTraversal = field(init=False, repr=False)
    _children: dict[ItemRef, tuple[ItemRef, ...]] = field(init=False, repr=False)
    _parents: dict[ItemRef, set[ItemRef]] = field(init=False, repr=False)

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
        traversal = OrderedPolyadicTraversal(
            self.graph,
            self.relation,
            PolyadicSide.SOURCES,
            PolyadicSide.TARGETS,
        )
        object.__setattr__(traversal, "_containment_profile", True)
        object.__setattr__(self, "_traversal", traversal)
        children, parents = self._build_incidence()
        object.__setattr__(self, "_children", children)
        object.__setattr__(self, "_parents", parents)

    def _node(self, reference: ItemRef) -> Node:
        if reference not in self.graph.canonical_items():
            raise ValueError(
                f"ordered containment relation {str(self.relation)!r} received "
                f"item {str(reference)!r} outside its graph"
            )
        return Node(NodeKind.ITEM, reference)

    def _incidence(
        self,
    ) -> tuple[dict[ItemRef, tuple[ItemRef, ...]], dict[ItemRef, set[ItemRef]]]:
        """Return incidence validated and cached when the profile was built."""
        return self._children, self._parents

    def _build_incidence(
        self,
    ) -> tuple[dict[ItemRef, tuple[ItemRef, ...]], dict[ItemRef, set[ItemRef]]]:
        """Build containment incidence once from the immutable graph."""
        children: dict[ItemRef, tuple[ItemRef, ...]] = {}
        parents: dict[ItemRef, set[ItemRef]] = {}
        source_instances: dict[ItemRef, int] = {}
        for instance_index, source_nodes, target_nodes in self._traversal._instances(
            check_cycle=True
        ):
            sources = self._references(source_nodes)
            targets = self._references(target_nodes)
            for source in sources:
                previous = source_instances.get(source)
                if previous is not None:
                    raise ValueError(
                        f"ordered containment relation {str(self.relation)!r} "
                        f"source {str(source)!r} occurs in instances "
                        f"{previous} and {instance_index}, violating source uniqueness"
                    )
                source_instances[source] = instance_index
                children[source] = tuple(targets)
                for target in targets:
                    parents.setdefault(target, set()).add(source)
        return children, parents

    @staticmethod
    def _references(nodes: tuple[Node, ...]) -> tuple[ItemRef, ...]:
        return tuple(
            node.reference for node in nodes if isinstance(node.reference, ItemRef)
        )

    def direct_children(self, parent: ItemRef) -> NodeSequence:
        """Return direct children in declared target incidence order."""
        self._node(parent)
        self._incidence()
        return self._traversal.direct(parent)

    def descendants(self, parent: ItemRef) -> NodeSequence:
        """Return descendants in depth-first pre-order, preserving repetition."""
        self._node(parent)

        self._incidence()
        return self._traversal.transitive(parent)

    def leaves(self, parent: ItemRef) -> NodeSequence:
        """Return descendant leaves, or the source itself when it has no children."""
        self._node(parent)

        children, _ = self._incidence()
        result: list[Node] = []
        stack = [parent]
        while stack:
            item = stack.pop()
            direct = children.get(item, ())
            if direct:
                stack.extend(reversed(direct))
            else:
                result.append(Node(NodeKind.ITEM, item))
        return NodeSequence(self.graph, tuple(result))

    def parents(self, child: ItemRef) -> NodeSet:
        """Return the canonical set-valued inverse fiber over one child."""
        self._node(child)
        _, parents = self._incidence()
        return NodeSet(
            self.graph,
            tuple(Node(NodeKind.ITEM, source) for source in parents.get(child, set())),
        )

    def ancestors(self, child: ItemRef) -> NodeSet:
        """Return the transitive inverse fiber as a canonical reachable set."""
        self._node(child)
        _, parents = self._incidence()
        reached = NodeSet(self.graph, ())
        frontier = NodeSet(
            self.graph,
            tuple(Node(NodeKind.ITEM, parent) for parent in parents.get(child, set())),
        )
        while frontier.nodes:
            fresh = frontier - reached
            if not fresh.nodes:
                break
            reached = reached | fresh
            frontier = NodeSet(
                self.graph,
                tuple(
                    Node(NodeKind.ITEM, parent)
                    for item in self._references(fresh.nodes)
                    for parent in parents.get(item, set())
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
    if isinstance(reference, ItemRef | DurableItemRef):
        return Node(NodeKind.ITEM, graph.resolve_item(reference))
    assert isinstance(reference, DurableBoundaryRef)
    resolved: BoundaryRef = graph.resolve_boundary(reference)
    return Node(NodeKind.POSITION, resolved)
