"""A recursive JSON-value profile over ordinary tiergraph structure."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import cast

from tiergraph.core import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    Graph,
    Item,
    ItemRef,
    JsonValue,
    NamespaceDeclaration,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    QualifiedName,
    RelationEndpointKind,
    RelationSideDeclaration,
    Tier,
    TierDeclaration,
    XsdType,
)

_KINDS = frozenset(
    {"null", "array", "object", "string", "boolean", "integer", "double"}
)


@dataclass(frozen=True, slots=True)
class JsonValueProfile:
    """Interpret a recursive JSON value as items joined by ordered relations.

    Each value node is an ordinary item.  Container membership is an ordered
    polyadic relation whose one source is the container and whose targets are
    its children.  Object member keys are string attributes on child nodes;
    they are required in lexical order so equivalent objects have one encoding.
    Scalar leaves retain the kernel's canonical XSD lexical spelling.

    Provenance is deliberately not part of this profile.  A caller relates the
    root item to an ordinary source item with a declared relation.  That admits
    chained provenance without turning a source reference into scalar data.
    """

    graph: Graph
    node_tier: QualifiedName
    member_relation: QualifiedName
    kind_attribute: QualifiedName
    key_attribute: QualifiedName
    string_attribute: QualifiedName
    boolean_attribute: QualifiedName
    integer_attribute: QualifiedName
    double_attribute: QualifiedName
    _members: dict[ItemRef, tuple[ItemRef, ...]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Validate every role before any structured value can be read."""
        tier = next(
            (
                item
                for item in self.graph.tiers
                if item.declaration.name == self.node_tier
            ),
            None,
        )
        if tier is None:
            raise ValueError(
                f"JSON value node tier {str(self.node_tier)!r} is not declared"
            )
        declaration = next(
            (
                item
                for item in self.graph.relation_declarations
                if item.name == self.member_relation
            ),
            None,
        )
        expected_side = RelationSideDeclaration(
            (RelationEndpointKind.ITEM,), (self.node_tier,), 1, 1
        )
        expected_targets = RelationSideDeclaration(
            (RelationEndpointKind.ITEM,), (self.node_tier,), 0, None, True
        )
        if not (
            isinstance(declaration, PolyadicRelationDeclaration)
            and declaration.sources == expected_side
            and declaration.targets == expected_targets
            and declaration.unique_sources
        ):
            raise ValueError(
                "JSON member relation must have one node source, ordered node targets, "
                "and unique sources"
            )
        for name, value_type, role in (
            (self.kind_attribute, XsdType.STRING, "kind"),
            (self.key_attribute, XsdType.STRING, "key"),
            (self.string_attribute, XsdType.STRING, "string"),
            (self.boolean_attribute, XsdType.BOOLEAN, "boolean"),
            (self.integer_attribute, XsdType.INTEGER, "integer"),
            (self.double_attribute, XsdType.DOUBLE, "double"),
        ):
            attribute = next(
                (
                    item
                    for item in self.graph.attribute_declarations
                    if item.name == name
                ),
                None,
            )
            if attribute != AttributeDeclaration(
                name, AttributeDomain.ITEM, value_type
            ):
                raise ValueError(
                    f"JSON {role} role {str(name)!r} must be an item "
                    f"{value_type.value} attribute"
                )
        members: dict[ItemRef, tuple[ItemRef, ...]] = {}
        for relation in self.graph.polyadic_relations:
            if relation.declaration != self.member_relation:
                continue
            # Graph construction already enforced the declaration's item kind.
            source = cast(ItemRef, relation.sources[0])
            members[source] = tuple(
                cast(ItemRef, target) for target in relation.targets
            )
        object.__setattr__(self, "_members", members)

    def value(self, root: ItemRef) -> JsonValue:
        """Return the JSON value rooted at ``root``, refusing malformed neighbours."""
        if root.tier != self.node_tier:
            raise ValueError(
                f"JSON value root {root.to_data()!r} is not on the node tier"
            )
        visiting: set[ItemRef] = set()
        return self._value(root, visiting)

    def _value(self, reference: ItemRef, visiting: set[ItemRef]) -> JsonValue:
        if reference in visiting:
            raise ValueError(f"JSON value node {reference.to_data()!r} is recursive")
        try:
            item = next(
                tier
                for tier in self.graph.tiers
                if tier.declaration.name == reference.tier
            ).items[reference.index]
        except (StopIteration, IndexError) as error:
            raise ValueError(
                f"JSON value node {reference.to_data()!r} does not exist"
            ) from error
        values = {value.name: value for value in item.attributes}
        kind_value = values.get(self.kind_attribute)
        if kind_value is None or kind_value.lexical not in _KINDS:
            offender = None if kind_value is None else kind_value.lexical
            raise ValueError(
                f"JSON value node {reference.to_data()!r} has unsupported kind {offender!r}"
            )
        kind = kind_value.lexical
        payload_names = {
            "string": self.string_attribute,
            "boolean": self.boolean_attribute,
            "integer": self.integer_attribute,
            "double": self.double_attribute,
        }
        present = [name for name in payload_names.values() if name in values]
        expected = payload_names.get(kind)
        if present != ([] if expected is None else [expected]):
            raise ValueError(
                f"JSON value node {reference.to_data()!r} has payload attributes "
                f"{[str(name) for name in present]!r}; kind {kind!r} requires "
                f"{None if expected is None else str(expected)!r}"
            )
        children = self._members.get(reference)
        if kind not in {"array", "object"}:
            if children is not None:
                raise ValueError(
                    f"JSON scalar node {reference.to_data()!r} has a member relation"
                )
            if kind == "null":
                return None
            lexical = values[payload_names[kind]].lexical
            if kind == "string":
                return lexical
            if kind == "boolean":
                return lexical == "true"
            if kind == "integer":
                return int(lexical)
            number = float(lexical)
            if not math.isfinite(number):
                raise ValueError(
                    f"JSON double node {reference.to_data()!r} is not finite"
                )
            return number
        if children is None:
            raise ValueError(
                f"JSON container node {reference.to_data()!r} has no member relation"
            )
        visiting.add(reference)
        decoded = [self._value(child, visiting) for child in children]
        visiting.remove(reference)
        if kind == "array":
            for child in children:
                child_values = self._item_values(child)
                if self.key_attribute in child_values:
                    raise ValueError(
                        f"JSON array member {child.to_data()!r} has an object key"
                    )
            return decoded
        keys: list[str] = []
        for child in children:
            key = self._item_values(child).get(self.key_attribute)
            if key is None:
                raise ValueError(f"JSON object member {child.to_data()!r} has no key")
            keys.append(key.lexical)
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError(
                f"JSON object node {reference.to_data()!r} has noncanonical keys {keys!r}"
            )
        return dict(zip(keys, decoded, strict=True))

    def _item_values(self, reference: ItemRef) -> dict[QualifiedName, AttributeValue]:
        tier = next(
            item for item in self.graph.tiers if item.declaration.name == reference.tier
        )
        return {value.name: value for value in tier.items[reference.index].attributes}


def json_value_graph(
    value: JsonValue, namespace: str = "urn:tiergraph:json-value"
) -> tuple[Graph, JsonValueProfile, ItemRef]:
    """Construct a standalone canonical graph for one recursively nested JSON value."""
    names = {
        local: QualifiedName(namespace, local)
        for local in (
            "nodes",
            "members",
            "kind",
            "key",
            "string",
            "boolean",
            "integer",
            "double",
        )
    }
    declarations = (
        AttributeDeclaration(names["kind"], AttributeDomain.ITEM, XsdType.STRING),
        AttributeDeclaration(names["key"], AttributeDomain.ITEM, XsdType.STRING),
        AttributeDeclaration(names["string"], AttributeDomain.ITEM, XsdType.STRING),
        AttributeDeclaration(names["boolean"], AttributeDomain.ITEM, XsdType.BOOLEAN),
        AttributeDeclaration(names["integer"], AttributeDomain.ITEM, XsdType.INTEGER),
        AttributeDeclaration(names["double"], AttributeDomain.ITEM, XsdType.DOUBLE),
    )
    items: list[Item] = []
    relations: list[PolyadicRelationInstance] = []

    def _add(node: JsonValue, key: str | None = None) -> ItemRef:
        reference = ItemRef(names["nodes"], len(items))
        kind: str
        payload: AttributeValue | None = None
        children: list[ItemRef] | None = None
        if node is None:
            kind = "null"
        elif isinstance(node, bool):
            kind = "boolean"
            payload = AttributeValue(
                names["boolean"], XsdType.BOOLEAN, str(node).lower()
            )
        elif isinstance(node, int):
            kind = "integer"
            payload = AttributeValue(names["integer"], XsdType.INTEGER, str(node))
        elif isinstance(node, float):
            if not math.isfinite(node):
                raise ValueError(f"JSON value double {node!r} is not finite")
            kind = "double"
            payload = AttributeValue(names["double"], XsdType.DOUBLE, repr(node))
        elif isinstance(node, str):
            kind = "string"
            payload = AttributeValue(names["string"], XsdType.STRING, node)
        elif isinstance(node, list):
            kind = "array"
            children = []
        elif isinstance(node, dict):
            if not all(isinstance(member, str) for member in node):
                raise ValueError("JSON object key must be a string")
            kind = "object"
            children = []
        else:
            raise ValueError(
                f"JSON value {node!r} has unsupported type {type(node).__name__}"
            )
        attributes = [AttributeValue(names["kind"], XsdType.STRING, kind)]
        if key is not None:
            attributes.append(AttributeValue(names["key"], XsdType.STRING, key))
        if payload is not None:
            attributes.append(payload)
        items.append(Item(attributes=tuple(attributes)))
        if children is not None:
            if isinstance(node, list):
                entries: Iterable[tuple[int | str, JsonValue]] = enumerate(node)
            else:
                object_value = cast(dict[str, JsonValue], node)
                entries = (
                    (member, object_value[member]) for member in sorted(object_value)
                )
            for member, child in entries:
                children.append(
                    _add(child, None if isinstance(member, int) else member)
                )
            relations.append(
                PolyadicRelationInstance(
                    names["members"], (reference,), tuple(children)
                )
            )
        return reference

    root = _add(value)
    side = RelationSideDeclaration(
        (RelationEndpointKind.ITEM,), (names["nodes"],), 1, 1
    )
    targets = RelationSideDeclaration(
        (RelationEndpointKind.ITEM,), (names["nodes"],), 0, None, True
    )
    graph = Graph(
        (NamespaceDeclaration("value", namespace),),
        (Tier(TierDeclaration(names["nodes"], "JSON value nodes"), tuple(items)),),
        (
            PolyadicRelationDeclaration(
                names["members"], side, targets, unique_sources=True
            ),
        ),
        attribute_declarations=declarations,
        polyadic_relations=tuple(relations),
    )
    profile = JsonValueProfile(
        graph,
        names["nodes"],
        names["members"],
        names["kind"],
        names["key"],
        names["string"],
        names["boolean"],
        names["integer"],
        names["double"],
    )
    return graph, profile, root
