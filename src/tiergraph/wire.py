"""Canonical JSON serialization and parsing for primitive graphs."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextvars import ContextVar
from typing import cast

from tiergraph.core import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    BoundarySide,
    DurableItemRef,
    DurablePositionRef,
    Graph,
    Item,
    ItemRef,
    JsonValue,
    NamespaceDeclaration,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    Position,
    PositionRef,
    QualifiedName,
    RelationDeclaration,
    RelationEndpointKind,
    RelationEndpointRef,
    RelationInstance,
    RelationSideDeclaration,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    XsdType,
)
from tiergraph.schema import (
    DECLARATIONS,
    DOCUMENT,
    DURABLE_POSITION,
    GRAPH,
    ITEM,
    ITEM_REFERENCE,
    TIER,
    TIER_DECLARATION,
    array_item,
    field_shape,
    object_fields,
    required_object_fields,
)

# FORMAT_VERSION is gate-bound to both the declared schema shape and its published
# artifact. Version 6 omits empty collections and nulls and spells qualified names
# with document prefixes. Older documents are deliberately refused.
FORMAT_VERSION = "6"
# Owner-tunable policy: bound parser memory while admitting substantial graphs.
MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
# Owner-tunable policy: stay well below interpreter/parser recursion limits.
MAX_JSON_DEPTH = 256


def to_data(graph: Graph) -> dict[str, JsonValue]:
    """Return the versioned primitive document as strict JSON data."""
    prefixes: dict[str, str] = {}
    for binding in graph.namespaces:
        if ":" in binding.prefix:
            raise ValueError("namespace prefix must not contain ':' in wire format")
        prefixes[binding.namespace] = binding.prefix
    encoded = _encode_value(graph.to_data(), prefixes)
    return {"format_version": FORMAT_VERSION, "graph": encoded}


def _encode_value(value: JsonValue, prefixes: dict[str, str]) -> JsonValue:
    """Compact expanded names and omit values recovered uniquely by the decoder."""
    if isinstance(value, list):
        return [_encode_value(item, prefixes) for item in value]
    if isinstance(value, dict):
        if set(value) == {"namespace", "local_name"}:
            namespace = cast(str, value["namespace"])
            local_name = cast(str, value["local_name"])
            return f"{prefixes[namespace]}:{local_name}"
        relation_side = set(value) == {
            "endpoint_kinds",
            "tiers",
            "minimum",
            "maximum",
            "allow_empty",
        }
        return {
            key: _encode_value(item, prefixes)
            for key, item in value.items()
            if item is not None and (item != [] or (relation_side and key == "tiers"))
        }
    return value


def dumps(graph: Graph) -> str:
    """Return the sole canonical JSON spelling, including its final newline."""
    return (
        json.dumps(
            to_data(graph),
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def dump_bytes(graph: Graph) -> bytes:
    """Encode the canonical document as UTF-8 bytes."""
    return dumps(graph).encode("utf-8")


def loads(document: str | bytes) -> Graph:
    """Parse the current format without implicitly migrating older documents.

    Migration is refused because choosing a loss-aware conversion belongs in an
    explicit version-to-version tool, not in the primitive codec.
    """
    try:
        text = _checked_document(document)
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"parse JSON failed: {error.msg}") from error
    except UnicodeDecodeError as error:
        raise ValueError(f"parse UTF-8 failed: {error.reason}") from error
    except UnicodeEncodeError as error:
        raise ValueError(f"encode UTF-8 failed: {error.reason}") from error
    except RecursionError as error:
        raise ValueError("parse JSON failed: document nesting is too deep") from error
    root = _object(value, "document")
    _materialize_defaults(root, DOCUMENT)
    _keys(root, object_fields(DOCUMENT), "document")
    version = _string(root["format_version"], "format_version")
    if version != FORMAT_VERSION:
        raise ValueError(
            f"format_version {version!r} is unsupported; expected {FORMAT_VERSION!r}"
        )
    return _graph(_object(root["graph"], "graph"))


def _checked_document(document: str | bytes) -> str:
    """Enforce byte and nesting policies before invoking the JSON parser."""
    if isinstance(document, bytes):
        size = len(document)
        if size > MAX_DOCUMENT_BYTES:
            raise ValueError(
                f"document size {size} bytes exceeds limit {MAX_DOCUMENT_BYTES}"
            )
        text = document.decode("utf-8")
    else:
        if len(document) > MAX_DOCUMENT_BYTES:
            raise ValueError(f"document size exceeds limit {MAX_DOCUMENT_BYTES} bytes")
        encoded = document.encode("utf-8")
        size = len(encoded)
        if size > MAX_DOCUMENT_BYTES:
            raise ValueError(
                f"document size {size} bytes exceeds limit {MAX_DOCUMENT_BYTES}"
            )
        text = document

    depth = 0
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
        elif character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise ValueError(f"JSON nesting depth exceeds limit {MAX_JSON_DEPTH}")
        elif character in "]}":
            depth -= 1
    return text


_namespace_by_prefix: ContextVar[tuple[tuple[str, str], ...]] = ContextVar(
    "wire_namespace_by_prefix", default=()
)


def _materialize_defaults(value: object, shape: object) -> None:
    """Restore omitted arrays and nullable strings for the existing decoder."""
    from tiergraph.schema import DECLARATIONS, Shape, ShapeKind

    declared = cast(Shape, shape)
    if declared.kind is ShapeKind.REFERENCE:
        alternatives = [DECLARATIONS[name] for name in declared.variants]
        keys = set(value) if isinstance(value, dict) else set()
        declared = max(
            alternatives,
            key=lambda candidate: (
                sum(field.name in keys for field in candidate.fields),
                required_object_fields(candidate) <= keys,
            ),
        )
    if declared.kind is ShapeKind.OBJECT and isinstance(value, dict):
        for field in declared.fields:
            if field.name not in value:
                if declared is DECLARATIONS["relation_side"] and field.name == "tiers":
                    continue
                if field.shape.kind is ShapeKind.ARRAY:
                    value[field.name] = []
                elif field.shape.kind is ShapeKind.NULLABLE_STRING:
                    value[field.name] = None
            if field.name in value:
                _materialize_defaults(value[field.name], field.shape)
    elif declared.kind is ShapeKind.ARRAY and isinstance(value, list):
        assert declared.item is not None
        for item in value:
            _materialize_defaults(item, declared.item)


def _graph(data: dict[str, object]) -> Graph:
    _keys(data, object_fields(GRAPH), "graph")
    namespaces = {
        _string(entry["prefix"], f"namespaces[{index}].prefix"): _string(
            entry["namespace"], f"namespaces[{index}].namespace"
        )
        for index, entry in enumerate(
            _objects(
                data["namespaces"],
                "namespaces",
                object_fields(array_item(field_shape(GRAPH, "namespaces"))),
            )
        )
    }
    if any(":" in prefix for prefix in namespaces):
        raise ValueError("namespace prefix must not contain ':'")
    token = _namespace_by_prefix.set(tuple(namespaces.items()))
    try:
        return _decode_graph(data)
    finally:
        _namespace_by_prefix.reset(token)


def _decode_graph(data: dict[str, object]) -> Graph:
    """Decode graph content after installing its document-local prefix table."""
    decoded_relations = tuple(
        _relation(entry, index)
        for index, entry in enumerate(_object_list(data["relations"], "relations"))
    )
    return Graph(
        tuple(
            NamespaceDeclaration(
                _string(entry["prefix"], f"namespaces[{index}].prefix"),
                _string(entry["namespace"], f"namespaces[{index}].namespace"),
            )
            for index, entry in enumerate(
                _objects(
                    data["namespaces"],
                    "namespaces",
                    object_fields(array_item(field_shape(GRAPH, "namespaces"))),
                )
            )
        ),
        tuple(
            _tier(entry, index)
            for index, entry in enumerate(
                _objects(data["tiers"], "tiers", object_fields(TIER))
            )
        ),
        tuple(
            _relation_declaration(entry, index)
            for index, entry in enumerate(
                _object_list(data["relation_declarations"], "relation_declarations")
            )
        ),
        tuple(
            relation
            for relation in decoded_relations
            if isinstance(relation, RelationInstance)
        ),
        tuple(
            _attribute_declaration(entry, index)
            for index, entry in enumerate(
                _objects(
                    data["attribute_declarations"],
                    "attribute_declarations",
                    object_fields(
                        array_item(field_shape(GRAPH, "attribute_declarations"))
                    ),
                )
            )
        ),
        tuple(
            _position(entry, index)
            for index, entry in enumerate(
                _objects(
                    data["position_values"],
                    "position_values",
                    object_fields(array_item(field_shape(GRAPH, "position_values"))),
                )
            )
        ),
        _attributes(data["attributes"], "attributes"),
        tuple(
            relation
            for relation in decoded_relations
            if isinstance(relation, PolyadicRelationInstance)
        ),
    )


def _tier(data: dict[str, object], index: int) -> Tier:
    path = f"tiers[{index}]"
    declaration = _named_object(
        data["declaration"], f"{path}.declaration", object_fields(TIER_DECLARATION)
    )
    return Tier(
        TierDeclaration(
            _name(declaration["name"], f"{path}.declaration.name"),
            _string(declaration["long_name"], f"{path}.declaration.long_name"),
        ),
        tuple(
            _item(item, item_index, path)
            for item_index, item in enumerate(
                _objects(data["items"], f"{path}.items", object_fields(ITEM))
            )
        ),
        _attributes(data["attributes"], f"{path}.attributes"),
    )


def _item(data: dict[str, object], index: int, tier_path: str) -> Item:
    path = f"{tier_path}.items[{index}]"
    durable = data["durable_id"]
    if durable is not None:
        durable = _string(durable, f"{path}.durable_id")
    return Item(durable, _attributes(data["attributes"], f"{path}.attributes"))


def _relation_declaration(data: dict[str, object], index: int) -> RelationDeclaration:
    path = f"relation_declarations[{index}]"
    kind = _string(data.get("kind"), f"{path}.kind")
    if kind == "simple":
        _keys(data, object_fields(DECLARATIONS["simple_relation"]), path)
        return SimpleRelationDeclaration(
            _name(data["name"], f"{path}.name"),
            _name(data["tier"], f"{path}.tier"),
            _name(data["item_type"], f"{path}.item_type"),
            _attributes(data["attributes"], f"{path}.attributes"),
        )
    if kind == "bipartite":
        _keys(
            data,
            object_fields(DECLARATIONS["bipartite_relation"]),
            path,
        )
        return BipartiteRelationDeclaration(
            _name(data["name"], f"{path}.name"),
            _name(data["left_type"], f"{path}.left_type"),
            _name(data["right_type"], f"{path}.right_type"),
            _enum(RelationEndpointKind, data["left_endpoint"], f"{path}.left_endpoint"),
            _enum(
                RelationEndpointKind, data["right_endpoint"], f"{path}.right_endpoint"
            ),
            _boolean(data["single_parent"], f"{path}.single_parent"),
            _boolean(data["acyclic"], f"{path}.acyclic"),
            _attributes(data["attributes"], f"{path}.attributes"),
        )
    if kind == "polyadic":
        _keys(data, object_fields(DECLARATIONS["polyadic_relation"]), path)
        subset = _array(data["targets_subset_of"], f"{path}.targets_subset_of")
        if len(subset) > 1:
            raise ValueError(f"{path}.targets_subset_of must contain at most one name")
        return PolyadicRelationDeclaration(
            _name(data["name"], f"{path}.name"),
            _relation_side(data["sources"], f"{path}.sources"),
            _relation_side(data["targets"], f"{path}.targets"),
            _boolean(data["unique_sources"], f"{path}.unique_sources"),
            _boolean(data["distinct_targets"], f"{path}.distinct_targets"),
            _boolean(data["single_parent"], f"{path}.single_parent"),
            _boolean(data["acyclic"], f"{path}.acyclic"),
            None if not subset else _name(subset[0], f"{path}.targets_subset_of[0]"),
            _attributes(data["attributes"], f"{path}.attributes"),
        )
    raise ValueError(f"{path}.kind {kind!r} is unsupported")


def _relation_side(value: object, path: str) -> RelationSideDeclaration:
    data = _object(value, path)
    expected = object_fields(DECLARATIONS["relation_side"])
    missing = (expected - {"tiers"}) - data.keys()
    extra = data.keys() - expected
    if missing:
        raise ValueError(f"{path} is missing field {min(missing)!r}")
    if extra:
        raise ValueError(f"{path} has unknown field {min(extra)!r}")
    kinds = _array(data["endpoint_kinds"], f"{path}.endpoint_kinds")
    tiers = None if "tiers" not in data else _array(data["tiers"], f"{path}.tiers")
    maximum = _integer(data["maximum"], f"{path}.maximum")
    return RelationSideDeclaration(
        tuple(
            _enum(RelationEndpointKind, item, f"{path}.endpoint_kinds[{index}]")
            for index, item in enumerate(kinds)
        ),
        None
        if tiers is None
        else tuple(
            _name(item, f"{path}.tiers[{index}]") for index, item in enumerate(tiers)
        ),
        _integer(data["minimum"], f"{path}.minimum"),
        None if maximum == -1 else maximum,
        _boolean(data["allow_empty"], f"{path}.allow_empty"),
    )


def _relation(
    data: dict[str, object], index: int
) -> RelationInstance | PolyadicRelationInstance:
    path = f"relations[{index}]"
    if "sources" in data:
        _keys(data, object_fields(DECLARATIONS["polyadic_relation_instance"]), path)
        durable = data["durable_id"]
        if durable is not None:
            durable = _string(durable, f"{path}.durable_id")
        return PolyadicRelationInstance(
            _name(data["declaration"], f"{path}.declaration"),
            tuple(
                _endpoint(item, f"{path}.sources[{item_index}]")
                for item_index, item in enumerate(
                    _array(data["sources"], f"{path}.sources")
                )
            ),
            tuple(
                _endpoint(item, f"{path}.targets[{item_index}]")
                for item_index, item in enumerate(
                    _array(data["targets"], f"{path}.targets")
                )
            ),
            durable,
            _attributes(data["attributes"], f"{path}.attributes"),
        )
    _keys(data, object_fields(DECLARATIONS["binary_relation_instance"]), path)
    durable = data["durable_id"]
    if durable is not None:
        durable = _string(durable, f"{path}.durable_id")
    return RelationInstance(
        _name(data["declaration"], f"{path}.declaration"),
        _endpoint(data["left"], f"{path}.left"),
        _endpoint(data["right"], f"{path}.right"),
        durable,
        _attributes(data["attributes"], f"{path}.attributes"),
    )


def _endpoint(value: object, path: str) -> RelationEndpointRef:
    data = _object(value, path)
    if "anchor" in data:
        return _durable_position(data, path)
    _keys(data, object_fields(ITEM_REFERENCE), path)
    return ItemRef(
        _name(data["tier"], f"{path}.tier"), _integer(data["index"], f"{path}.index")
    )


def _position(data: dict[str, object], index: int) -> Position:
    path = f"position_values[{index}]"
    reference_data = _object(data["reference"], f"{path}.reference")
    reference: PositionRef | DurablePositionRef
    if "anchor" in reference_data:
        reference = _durable_position(reference_data, f"{path}.reference")
    else:
        _keys(reference_data, object_fields(ITEM_REFERENCE), f"{path}.reference")
        reference = PositionRef(
            _name(reference_data["tier"], f"{path}.reference.tier"),
            _integer(reference_data["index"], f"{path}.reference.index"),
        )
    return Position(reference, _attributes(data["attributes"], f"{path}.attributes"))


def _durable_position(data: dict[str, object], path: str) -> DurablePositionRef:
    _keys(data, object_fields(DURABLE_POSITION), path)
    anchor = _object(data["anchor"], f"{path}.anchor")
    kind = _string(anchor.get("kind"), f"{path}.anchor.kind")
    if kind == "item":
        _keys(anchor, object_fields(DECLARATIONS["item_anchor"]), f"{path}.anchor")
        target: DurableItemRef | QualifiedName = DurableItemRef(
            _string(anchor["durable_id"], f"{path}.anchor.durable_id")
        )
    elif kind == "tier":
        _keys(anchor, object_fields(DECLARATIONS["tier_anchor"]), f"{path}.anchor")
        target = _name(anchor["tier"], f"{path}.anchor.tier")
    else:
        raise ValueError(f"{path}.anchor.kind {kind!r} is unsupported")
    return DurablePositionRef(target, _enum(BoundarySide, data["side"], f"{path}.side"))


def _attribute_declaration(data: dict[str, object], index: int) -> AttributeDeclaration:
    path = f"attribute_declarations[{index}]"
    return AttributeDeclaration(
        _name(data["name"], f"{path}.name"),
        _enum(AttributeDomain, data["domain"], f"{path}.domain"),
        _enum(XsdType, data["value_type"], f"{path}.value_type"),
    )


def _attributes(value: object, path: str) -> tuple[AttributeValue, ...]:
    return tuple(
        AttributeValue(
            _name(data["name"], f"{path}[{index}].name"),
            _enum(XsdType, data["value_type"], f"{path}[{index}].value_type"),
            _string(data["lexical"], f"{path}[{index}].lexical"),
        )
        for index, data in enumerate(
            _objects(
                value,
                path,
                object_fields(DECLARATIONS["string_attribute_value"]),
            )
        )
    )


def _name(value: object, path: str) -> QualifiedName:
    spelling = _string(value, path)
    prefix, separator, local_name = spelling.partition(":")
    if not separator or not prefix or not local_name:
        raise ValueError(f"{path} must be a qualified name spelled 'prefix:local'")
    namespace = dict(_namespace_by_prefix.get()).get(prefix)
    if namespace is None:
        raise ValueError(f"{path} uses undeclared namespace prefix {prefix!r}")
    return QualifiedName(namespace, local_name)


def _named_object(value: object, path: str, keys: set[str]) -> dict[str, object]:
    data = _object(value, path)
    _keys(data, keys, path)
    return data


def _object(value: object, path: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{path} must be an object")
    return cast(dict[str, object], value)


def _object_list(value: object, path: str) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ValueError(f"{path} must be an array")
    return [_object(entry, f"{path}[{index}]") for index, entry in enumerate(value)]


def _array(value: object, path: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{path} must be an array")
    return value


def _objects(value: object, path: str, keys: set[str]) -> list[dict[str, object]]:
    result = _object_list(value, path)
    for index, data in enumerate(result):
        _keys(data, keys, f"{path}[{index}]")
    return result


def _keys(data: dict[str, object], expected: set[str], path: str) -> None:
    missing = expected - data.keys()
    extra = data.keys() - expected
    if missing:
        raise ValueError(f"{path} is missing field {min(missing)!r}")
    if extra:
        raise ValueError(f"{path} has unknown field {min(extra)!r}")


def _string(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{path} must be a string")
    return value


def _integer(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{path} must be an integer")
    return value


def _boolean(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{path} must be a boolean")
    return value


def _enum[E](enum_type: Callable[[str], E], value: object, path: str) -> E:
    spelling = _string(value, path)
    try:
        return enum_type(spelling)
    except ValueError as error:
        raise ValueError(f"{path} has unsupported value {spelling!r}") from error
