"""Validated selection axes and canonically ordered node sets."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from tiergraph.core import (
    AttributeDomain,
    DurableItemRef,
    DurablePositionRef,
    Graph,
    ItemRef,
    JsonValue,
    PositionRef,
    QualifiedName,
    SimpleRelationDeclaration,
)


class NodeKind(StrEnum):
    """Distinguish identities belonging to different graph node classes."""

    DOCUMENT = "document"
    TIER = "tier"
    ITEM = "item"
    POSITION = "position"
    RELATION_DECLARATION = "relation_declaration"
    RELATION_INSTANCE = "relation_instance"


@dataclass(frozen=True, slots=True)
class Node:
    """Identify a node by its kind and its graph-local coordinate.

    Item and boundary coordinates include their tier, declaration nodes use their
    qualified name, and relation instances use their graph-local index.  The kind
    is part of identity, so coordinates from unlike node classes never alias.
    """

    kind: NodeKind
    reference: QualifiedName | ItemRef | PositionRef | int | None

    def to_data(self) -> dict[str, JsonValue]:
        """Return a tagged strict-JSON representation of this identity."""
        if self.reference is None or isinstance(self.reference, int):
            reference: JsonValue = self.reference
        else:
            reference = self.reference.to_data()
        return {"kind": self.kind.value, "reference": reference}


@dataclass(frozen=True, slots=True)
class NodeSet:
    """Hold unique nodes in the graph's canonical mixed-node order.

    Nodes sort first by kind rank. Within tier-addressed kinds they sort by tier
    declaration index, then item or position index, so reproducible selection
    output depends on the graph's tier declaration order.
    """

    graph: Graph
    nodes: tuple[Node, ...]

    def __post_init__(self) -> None:
        """Normalize caller order and repeated identities."""
        unique = set(self.nodes)
        object.__setattr__(self, "nodes", tuple(sorted(unique, key=self._key)))

    def _key(self, node: Node) -> tuple[int, ...]:
        kind_order = {
            NodeKind.DOCUMENT: 0,
            NodeKind.TIER: 1,
            NodeKind.ITEM: 2,
            NodeKind.POSITION: 3,
            NodeKind.RELATION_DECLARATION: 4,
            NodeKind.RELATION_INSTANCE: 5,
        }
        tier_order = {
            tier.declaration.name: index for index, tier in enumerate(self.graph.tiers)
        }
        declaration_order = {
            declaration.name: index
            for index, declaration in enumerate(self.graph.relation_declarations)
        }
        reference = node.reference
        detail: tuple[int, ...]
        if isinstance(reference, ItemRef | PositionRef):
            detail = (tier_order[reference.tier], reference.index)
        elif isinstance(reference, QualifiedName):
            detail = (tier_order.get(reference, declaration_order.get(reference, 0)),)
        elif isinstance(reference, int):
            relation = self.graph.relations[reference]
            detail = (
                declaration_order[relation.declaration],
                *self._endpoint_key(relation.left),
                *self._endpoint_key(relation.right),
                reference,
            )
        else:
            detail = ()
        return (kind_order[node.kind], *detail)

    def _endpoint_key(self, reference: ItemRef | DurablePositionRef) -> tuple[int, int]:
        if isinstance(reference, ItemRef):
            resolved: ItemRef | PositionRef = reference
        else:
            resolved = self.graph.resolve_position(reference)
        tier_order = {
            tier.declaration.name: index for index, tier in enumerate(self.graph.tiers)
        }
        return tier_order[resolved.tier], resolved.index

    def _same_graph(self, other: NodeSet) -> None:
        if other.graph is not self.graph:
            raise ValueError(
                "node-set operation requires selections from the same graph"
            )

    def __or__(self, other: NodeSet) -> NodeSet:
        """Return the canonical union of two selections."""
        self._same_graph(other)
        return NodeSet(self.graph, self.nodes + other.nodes)

    def __and__(self, other: NodeSet) -> NodeSet:
        """Return the canonical intersection of two selections."""
        self._same_graph(other)
        admitted = set(other.nodes)
        return NodeSet(
            self.graph, tuple(node for node in self.nodes if node in admitted)
        )

    def __sub__(self, other: NodeSet) -> NodeSet:
        """Return the canonical difference of two selections."""
        self._same_graph(other)
        excluded = set(other.nodes)
        return NodeSet(
            self.graph, tuple(node for node in self.nodes if node not in excluded)
        )

    def to_data(self) -> list[JsonValue]:
        """Return the ordered set as strict-JSON data."""
        return [node.to_data() for node in self.nodes]


@dataclass(frozen=True, slots=True)
class TierSelector:
    """Select one declared tier node."""

    graph: Graph
    tier: QualifiedName

    def __post_init__(self) -> None:
        """Refuse a tier name that the graph does not declare."""
        if all(
            candidate.declaration.name != self.tier for candidate in self.graph.tiers
        ):
            raise ValueError(f"tier selector {str(self.tier)!r} is undeclared")

    def evaluate(self) -> NodeSet:
        """Return the selected tier."""
        return NodeSet(self.graph, (Node(NodeKind.TIER, self.tier),))


@dataclass(frozen=True, slots=True)
class TypeSelector:
    """Select every item assigned one declared type by simple membership."""

    graph: Graph
    item_type: QualifiedName

    def __post_init__(self) -> None:
        """Refuse a type absent from every simple membership declaration."""
        if not any(
            isinstance(declaration, SimpleRelationDeclaration)
            and declaration.item_type == self.item_type
            for declaration in self.graph.relation_declarations
        ):
            raise ValueError(f"type selector {str(self.item_type)!r} is undeclared")

    def evaluate(self) -> NodeSet:
        """Return all items of the declared type."""
        tiers = {
            declaration.tier
            for declaration in self.graph.relation_declarations
            if isinstance(declaration, SimpleRelationDeclaration)
            and declaration.item_type == self.item_type
        }
        return NodeSet(
            self.graph,
            tuple(
                Node(NodeKind.ITEM, reference)
                for reference in self.graph.canonical_items()
                if reference.tier in tiers
            ),
        )


@dataclass(frozen=True, slots=True)
class ItemsSelector:
    """Select all items owned by one declared tier."""

    graph: Graph
    tier: QualifiedName

    def __post_init__(self) -> None:
        """Reuse tier validation at selector construction."""
        TierSelector(self.graph, self.tier)

    def evaluate(self) -> NodeSet:
        """Return the tier's items in coordinate order."""
        return NodeSet(
            self.graph,
            tuple(
                Node(NodeKind.ITEM, reference)
                for reference in self.graph.canonical_items()
                if reference.tier == self.tier
            ),
        )


@dataclass(frozen=True, slots=True)
class BoundariesSelector:
    """Select every boundary owned by one declared tier."""

    graph: Graph
    tier: QualifiedName

    def __post_init__(self) -> None:
        """Reuse tier validation at selector construction."""
        TierSelector(self.graph, self.tier)

    def evaluate(self) -> NodeSet:
        """Return both outer boundaries and every boundary between items."""
        return NodeSet(
            self.graph,
            tuple(
                Node(NodeKind.POSITION, self.graph.resolve_position(position.reference))
                for position in self.graph.positions(self.tier)
            ),
        )


@dataclass(frozen=True, slots=True)
class ItemSelector:
    """Select one structural or durable item reference."""

    graph: Graph
    reference: ItemRef | DurableItemRef
    _resolved: ItemRef = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Resolve and validate the reference at selector construction."""
        object.__setattr__(self, "_resolved", self.graph.resolve_item(self.reference))

    def evaluate(self) -> NodeSet:
        """Return the resolved item identity."""
        return NodeSet(self.graph, (Node(NodeKind.ITEM, self._resolved),))


@dataclass(frozen=True, slots=True)
class BoundarySelector:
    """Select one structural or anchored durable boundary reference."""

    graph: Graph
    reference: PositionRef | DurablePositionRef
    _resolved: PositionRef = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Resolve and validate the reference at selector construction."""
        object.__setattr__(
            self, "_resolved", self.graph.resolve_position(self.reference)
        )

    def evaluate(self) -> NodeSet:
        """Return the resolved boundary identity."""
        return NodeSet(self.graph, (Node(NodeKind.POSITION, self._resolved),))


@dataclass(frozen=True, slots=True)
class AttributeSelector:
    """Select nodes carrying one attribute on its declared domain."""

    graph: Graph
    attribute: QualifiedName
    domain: AttributeDomain

    def __post_init__(self) -> None:
        """Refuse missing declarations and domains the declaration does not permit."""
        declaration = next(
            (
                candidate
                for candidate in self.graph.attribute_declarations
                if candidate.name == self.attribute
            ),
            None,
        )
        if declaration is None:
            raise ValueError(
                f"attribute selector {str(self.attribute)!r} is undeclared"
            )
        if declaration.domain is not self.domain:
            raise ValueError(
                f"attribute selector {str(self.attribute)!r} does not permit domain "
                f"{self.domain.value!r}; declared for {declaration.domain.value!r}"
            )

    def evaluate(self) -> NodeSet:
        """Return owners that carry the named value without following relations."""
        nodes: list[Node] = []
        if self.domain is AttributeDomain.DOCUMENT:
            if self._has(self.graph.attributes):
                nodes.append(Node(NodeKind.DOCUMENT, None))
        elif self.domain is AttributeDomain.TIER:
            nodes.extend(
                Node(NodeKind.TIER, tier.declaration.name)
                for tier in self.graph.tiers
                if self._has(tier.attributes)
            )
        elif self.domain is AttributeDomain.ITEM:
            nodes.extend(
                Node(NodeKind.ITEM, ItemRef(tier.declaration.name, index))
                for tier in self.graph.tiers
                for index, item in enumerate(tier.items)
                if self._has(item.attributes)
            )
        elif self.domain is AttributeDomain.POSITION:
            nodes.extend(
                Node(NodeKind.POSITION, self.graph.resolve_position(position.reference))
                for position in self.graph.position_values
                if self._has(position.attributes)
            )
        elif self.domain is AttributeDomain.RELATION_DECLARATION:
            nodes.extend(
                Node(NodeKind.RELATION_DECLARATION, declaration.name)
                for declaration in self.graph.relation_declarations
                if self._has(declaration.attributes)
            )
        else:
            nodes.extend(
                Node(NodeKind.RELATION_INSTANCE, index)
                for index, relation in enumerate(self.graph.relations)
                if self._has(relation.attributes)
            )
        return NodeSet(self.graph, tuple(nodes))

    def _has(self, values: tuple[object, ...]) -> bool:
        return any(getattr(value, "name", None) == self.attribute for value in values)


type Selector = (
    TierSelector
    | TypeSelector
    | ItemsSelector
    | BoundariesSelector
    | ItemSelector
    | BoundarySelector
    | AttributeSelector
)


def select(graph: Graph, selectors: tuple[Selector, ...]) -> NodeSet:
    """Union validated selector routes into one canonical node set."""
    result = NodeSet(graph, ())
    for selector in selectors:
        if selector.graph is not graph:
            raise ValueError("selector belongs to a different graph")
        result = result | selector.evaluate()
    return result
