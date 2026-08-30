"""Validated selection axes and canonically ordered node sets."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

from tiergraph.core import (
    AttributeDomain,
    BoundaryRef,
    DurableBoundaryRef,
    DurableItemRef,
    Graph,
    ItemRef,
    JsonValue,
    QualifiedName,
    RelationEndpointRef,
    SimpleRelationDeclaration,
)
from tiergraph.machine import _decode_qname
from tiergraph.path import (
    PathProfile,
    ResolvedBoundary,
    ResolvedItem,
    StructuralPathProfile,
    resolve_path,
)
from tiergraph.schema import Refusal, RefusalStage


class NodeKind(StrEnum):
    """Distinguish identities belonging to different graph node classes."""

    DOCUMENT = "document"
    TIER = "tier"
    ITEM = "item"
    POSITION = "position"
    RELATION_DECLARATION = "relation_declaration"
    RELATION_INSTANCE = "relation_instance"
    POLYADIC_RELATION_INSTANCE = "polyadic_relation_instance"


@dataclass(frozen=True, slots=True)
class Node:
    """Identify a node by its kind and its graph-local coordinate.

    Item and boundary coordinates include their tier, declaration nodes use their
    qualified name, and relation instances use their graph-local index.  The kind
    is part of identity, so coordinates from unlike node classes never alias.

    Bipartite and polyadic instances live in separate graph collections, so they
    index separate spaces and index 0 names a different fact in each.  They are
    two node kinds over their own indices rather than one kind over a merged
    index, so a selection can neither confuse them nor answer for only one.
    """

    kind: NodeKind
    reference: QualifiedName | ItemRef | BoundaryRef | int | None

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
    declaration index, then item or boundary index, so reproducible selection
    output depends on the graph's tier declaration order.

    A polyadic instance sorts by its declaration, then its two side arities,
    then its endpoints read in stored order.  Side order is part of the key, so
    two instances over the same endpoints in different orders remain distinct.
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
            NodeKind.POLYADIC_RELATION_INSTANCE: 6,
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
        if isinstance(reference, ItemRef | BoundaryRef):
            detail = (tier_order[reference.tier], reference.index)
        elif isinstance(reference, QualifiedName):
            detail = (tier_order.get(reference, declaration_order.get(reference, 0)),)
        elif isinstance(reference, int):
            if node.kind is NodeKind.POLYADIC_RELATION_INSTANCE:
                polyadic = self.graph.polyadic_relations[reference]
                detail = (
                    declaration_order[polyadic.declaration],
                    len(polyadic.sources),
                    len(polyadic.targets),
                    *(
                        part
                        for endpoint in (*polyadic.sources, *polyadic.targets)
                        for part in self._endpoint_key(endpoint)
                    ),
                    reference,
                )
            else:
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

    def _endpoint_key(self, reference: RelationEndpointRef) -> tuple[int, int]:
        if isinstance(reference, ItemRef | DurableItemRef):
            resolved: ItemRef | BoundaryRef = self.graph.resolve_item(reference)
        else:
            resolved = self.graph.resolve_boundary(reference)
        tier_order = {
            tier.declaration.name: index for index, tier in enumerate(self.graph.tiers)
        }
        return tier_order[resolved.tier], resolved.index

    def _same_graph(self, other: NodeSet) -> None:
        if other.graph is not self.graph:
            raise Refusal(
                RefusalStage.SEMANTICS,
                "node-set operation requires selections from the same graph",
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

    tier: QualifiedName

    def evaluate(self, graph: Graph, *, path_profile: PathProfile) -> NodeSet:
        """Validate and return the selected tier."""
        if all(candidate.declaration.name != self.tier for candidate in graph.tiers):
            raise Refusal(
                RefusalStage.REFERENCE,
                f"tier selector {str(self.tier)!r} is undeclared",
            )
        return NodeSet(graph, (Node(NodeKind.TIER, self.tier),))


@dataclass(frozen=True, slots=True)
class TypeSelector:
    """Select every item assigned one declared type by simple membership."""

    item_type: QualifiedName

    def evaluate(self, graph: Graph, *, path_profile: PathProfile) -> NodeSet:
        """Validate and return all items of the declared type."""
        if not any(
            isinstance(declaration, SimpleRelationDeclaration)
            and declaration.item_type == self.item_type
            for declaration in graph.relation_declarations
        ):
            raise Refusal(
                RefusalStage.REFERENCE,
                f"type selector {str(self.item_type)!r} is undeclared",
            )
        tiers = {
            declaration.tier
            for declaration in graph.relation_declarations
            if isinstance(declaration, SimpleRelationDeclaration)
            and declaration.item_type == self.item_type
        }
        return NodeSet(
            graph,
            tuple(
                Node(NodeKind.ITEM, reference)
                for reference in graph.canonical_items()
                if reference.tier in tiers
            ),
        )


@dataclass(frozen=True, slots=True)
class ItemsSelector:
    """Select all items owned by one declared tier."""

    tier: QualifiedName

    def evaluate(self, graph: Graph, *, path_profile: PathProfile) -> NodeSet:
        """Validate and return the tier's items in coordinate order."""
        TierSelector(self.tier).evaluate(graph, path_profile=path_profile)
        return NodeSet(
            graph,
            tuple(
                Node(NodeKind.ITEM, reference)
                for reference in graph.canonical_items()
                if reference.tier == self.tier
            ),
        )


@dataclass(frozen=True, slots=True)
class BoundariesSelector:
    """Select every boundary owned by one declared tier."""

    tier: QualifiedName

    def evaluate(self, graph: Graph, *, path_profile: PathProfile) -> NodeSet:
        """Validate and return outer and inter-item boundaries."""
        TierSelector(self.tier).evaluate(graph, path_profile=path_profile)
        return NodeSet(
            graph,
            tuple(
                Node(NodeKind.POSITION, graph.resolve_boundary(boundary.reference))
                for boundary in graph.boundaries(self.tier)
            ),
        )


@dataclass(frozen=True, slots=True)
class ItemSelector:
    """Select one structural or durable item reference."""

    reference: ItemRef | DurableItemRef

    def evaluate(self, graph: Graph, *, path_profile: PathProfile) -> NodeSet:
        """Resolve and return the item identity."""
        return NodeSet(
            graph, (Node(NodeKind.ITEM, graph.resolve_item(self.reference)),)
        )


@dataclass(frozen=True, slots=True)
class BoundarySelector:
    """Select one structural or anchored durable boundary reference."""

    reference: BoundaryRef | DurableBoundaryRef

    def evaluate(self, graph: Graph, *, path_profile: PathProfile) -> NodeSet:
        """Resolve and return the boundary identity."""
        return NodeSet(
            graph, (Node(NodeKind.POSITION, graph.resolve_boundary(self.reference)),)
        )


@dataclass(frozen=True, slots=True)
class ItemPathSelector:
    """Select the item resolved by one path."""

    path: str

    def evaluate(self, graph: Graph, *, path_profile: PathProfile) -> NodeSet:
        """Resolve the path and require an item result."""
        resolved = resolve_path(graph, path_profile, self.path)
        if not isinstance(resolved, ResolvedItem):
            raise Refusal(
                RefusalStage.REFERENCE,
                f"item selection path {self.path!r} did not resolve to an item",
            )
        return ItemSelector(resolved.current).evaluate(graph, path_profile=path_profile)


@dataclass(frozen=True, slots=True)
class BoundaryPathSelector:
    """Select the boundary resolved by one path."""

    path: str

    def evaluate(self, graph: Graph, *, path_profile: PathProfile) -> NodeSet:
        """Resolve the path and require a boundary result."""
        resolved = resolve_path(graph, path_profile, self.path)
        if not isinstance(resolved, ResolvedBoundary):
            raise Refusal(
                RefusalStage.REFERENCE,
                f"boundary selection path {self.path!r} did not resolve to a boundary",
            )
        return BoundarySelector(resolved.current).evaluate(
            graph, path_profile=path_profile
        )


@dataclass(frozen=True, slots=True)
class AttributeSelector:
    """Select nodes carrying one attribute on its declared domain.

    The kernel admits ``relation_instance`` values on bipartite and polyadic
    instances alike, so this selector reads both collections and reports each
    carrier under its own node kind.  Reading only one would answer a question
    about the whole domain from part of it.
    """

    attribute: QualifiedName
    domain: AttributeDomain

    def evaluate(self, graph: Graph, *, path_profile: PathProfile) -> NodeSet:
        """Validate and return owners carrying the named value."""
        declaration = next(
            (
                candidate
                for candidate in graph.attribute_declarations
                if candidate.name == self.attribute
            ),
            None,
        )
        if declaration is None:
            raise Refusal(
                RefusalStage.REFERENCE,
                f"attribute selector {str(self.attribute)!r} is undeclared",
            )
        if declaration.domain is not self.domain:
            raise Refusal(
                RefusalStage.SEMANTICS,
                f"attribute selector {str(self.attribute)!r} does not permit "
                f"domain {self.domain.value!r}; declared for "
                f"{declaration.domain.value!r}",
            )

        nodes: list[Node] = []
        if self.domain is AttributeDomain.DOCUMENT:
            if self._has(graph.attributes):
                nodes.append(Node(NodeKind.DOCUMENT, None))
        elif self.domain is AttributeDomain.TIER:
            nodes.extend(
                Node(NodeKind.TIER, tier.declaration.name)
                for tier in graph.tiers
                if self._has(tier.attributes)
            )
        elif self.domain is AttributeDomain.ITEM:
            nodes.extend(
                Node(NodeKind.ITEM, ItemRef(tier.declaration.name, index))
                for tier in graph.tiers
                for index, item in enumerate(tier.items)
                if self._has(item.attributes)
            )
        elif self.domain is AttributeDomain.POSITION:
            nodes.extend(
                Node(NodeKind.POSITION, graph.resolve_boundary(boundary.reference))
                for boundary in graph.boundary_values
                if self._has(boundary.attributes)
            )
        elif self.domain is AttributeDomain.RELATION_DECLARATION:
            nodes.extend(
                Node(NodeKind.RELATION_DECLARATION, declaration.name)
                for declaration in graph.relation_declarations
                if self._has(declaration.attributes)
            )
        else:
            nodes.extend(
                Node(NodeKind.RELATION_INSTANCE, index)
                for index, relation in enumerate(graph.relations)
                if self._has(relation.attributes)
            )
            nodes.extend(
                Node(NodeKind.POLYADIC_RELATION_INSTANCE, index)
                for index, polyadic in enumerate(graph.polyadic_relations)
                if self._has(polyadic.attributes)
            )
        return NodeSet(graph, tuple(nodes))

    def _has(self, values: tuple[object, ...]) -> bool:
        return any(getattr(value, "name", None) == self.attribute for value in values)


@dataclass(frozen=True, slots=True)
class UnionSelector:
    """Union one or more selectors."""

    args: tuple[Selector, ...]

    def __post_init__(self) -> None:
        if not self.args:
            raise Refusal(
                RefusalStage.VALUE,
                "union selector requires at least one argument",
            )

    def evaluate(self, graph: Graph, *, path_profile: PathProfile) -> NodeSet:
        """Evaluate and union the operands from left to right."""
        result = self.args[0].evaluate(graph, path_profile=path_profile)
        for child in self.args[1:]:
            result = result | child.evaluate(graph, path_profile=path_profile)
        return result


@dataclass(frozen=True, slots=True)
class IntersectionSelector:
    """Intersect one or more selectors."""

    args: tuple[Selector, ...]

    def __post_init__(self) -> None:
        if not self.args:
            raise Refusal(
                RefusalStage.VALUE,
                "intersection selector requires at least one argument",
            )

    def evaluate(self, graph: Graph, *, path_profile: PathProfile) -> NodeSet:
        """Evaluate and intersect the operands from left to right."""
        result = self.args[0].evaluate(graph, path_profile=path_profile)
        for child in self.args[1:]:
            result = result & child.evaluate(graph, path_profile=path_profile)
        return result


@dataclass(frozen=True, slots=True)
class DifferenceSelector:
    """Remove the right selection from the left selection."""

    left: Selector
    right: Selector

    def evaluate(self, graph: Graph, *, path_profile: PathProfile) -> NodeSet:
        """Evaluate both operands and remove right from left."""
        return self.left.evaluate(
            graph, path_profile=path_profile
        ) - self.right.evaluate(graph, path_profile=path_profile)


type Selector = (
    TierSelector
    | TypeSelector
    | ItemsSelector
    | BoundariesSelector
    | ItemSelector
    | BoundarySelector
    | ItemPathSelector
    | BoundaryPathSelector
    | AttributeSelector
    | UnionSelector
    | IntersectionSelector
    | DifferenceSelector
)


class _DefaultStructuralPathProfile(StructuralPathProfile):
    def __repr__(self) -> str:
        return "StructuralPathProfile()"


_STRUCTURAL_PATH_PROFILE = _DefaultStructuralPathProfile()


def evaluate_selection(
    graph: Graph,
    selector: Selector,
    *,
    path_profile: PathProfile = _STRUCTURAL_PATH_PROFILE,
) -> NodeSet:
    """Evaluate a graph-free selector into one canonical node set."""
    return selector.evaluate(graph, path_profile=path_profile)


def selection_loads(source: str | bytes) -> Selector:
    """Decode one strict declarative selector from JSON."""
    return _decode_selector(cast(JsonValue, json.loads(source)), "$")


def _decode_selector(value: JsonValue, path: str) -> Selector:
    node = _object(value, path)
    discriminators = {"op", "select"} & node.keys()
    if len(discriminators) != 1:
        raise Refusal(
            RefusalStage.DISCRIMINATOR,
            f"{path} must contain exactly one of 'op' or 'select'",
        )
    if "op" in discriminators:
        operation = _discriminator(node["op"], f"{path}.op")
        if operation in ("union", "intersection"):
            _keys(node, {"op", "args"}, path)
            args_value = node["args"]
            if not isinstance(args_value, list) or not args_value:
                raise Refusal(
                    RefusalStage.VALUE, f"{path}.args must be a non-empty list"
                )
            args = tuple(
                _decode_selector(child, f"{path}.args[{index}]")
                for index, child in enumerate(args_value)
            )
            return (
                UnionSelector(args)
                if operation == "union"
                else IntersectionSelector(args)
            )
        if operation == "difference":
            _keys(node, {"op", "left", "right"}, path)
            return DifferenceSelector(
                _decode_selector(node["left"], f"{path}.left"),
                _decode_selector(node["right"], f"{path}.right"),
            )
        raise Refusal(
            RefusalStage.DISCRIMINATOR,
            f"{path}.op has unknown operation {operation!r}",
        )
    kind = _discriminator(node["select"], f"{path}.select")
    if kind in ("tier", "items", "boundaries"):
        _keys(node, {"select", "tier"}, path)
        name = _qualified_name(node["tier"], f"{path}.tier")
        if kind == "tier":
            return TierSelector(name)
        if kind == "items":
            return ItemsSelector(name)
        return BoundariesSelector(name)
    if kind == "type":
        _keys(node, {"select", "type"}, path)
        return TypeSelector(_qualified_name(node["type"], f"{path}.type"))
    if kind in ("item", "boundary"):
        _keys(node, {"select", "path"}, path)
        text = _string(node["path"], f"{path}.path")
        return ItemPathSelector(text) if kind == "item" else BoundaryPathSelector(text)
    if kind == "attribute":
        _keys(node, {"select", "attribute", "domain"}, path)
        domain_text = _string(node["domain"], f"{path}.domain")
        try:
            domain = AttributeDomain(domain_text)
        except ValueError as error:
            raise Refusal(
                RefusalStage.VALUE,
                f"{path}.domain has invalid attribute domain {domain_text!r}",
            ) from error
        return AttributeSelector(
            _qualified_name(node["attribute"], f"{path}.attribute"), domain
        )
    raise Refusal(
        RefusalStage.DISCRIMINATOR, f"{path}.select has unknown selector {kind!r}"
    )


def _object(value: JsonValue, path: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise Refusal(RefusalStage.CONSTRUCTION, f"{path} must be an object")
    return value


def _keys(value: dict[str, JsonValue], expected: set[str], path: str) -> None:
    if value.keys() != expected:
        raise Refusal(
            RefusalStage.SHAPE,
            f"{path} must contain exactly {sorted(expected)!r}; "
            f"found {sorted(value)!r}",
        )


def _discriminator(value: JsonValue, path: str) -> str:
    """Read the member that selects a declaration, staged as such.

    Which declaration applies is settled before anything is judged against
    it, so a discriminator that cannot be read is a discriminator condition
    rather than an ordinary member of the wrong construction.
    """
    if not isinstance(value, str):
        raise Refusal(RefusalStage.DISCRIMINATOR, f"{path} must be a string")
    return value


def _string(value: JsonValue, path: str) -> str:
    if not isinstance(value, str):
        raise Refusal(RefusalStage.CONSTRUCTION, f"{path} must be a string")
    return value


def _qualified_name(value: JsonValue, path: str) -> QualifiedName:
    return _decode_qname(value, path)
