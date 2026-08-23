"""Declare the primitive wire shape and derive its validators and schema."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

from tiergraph.core import JsonValue


class ShapeKind(StrEnum):
    """Name the JSON constructions admitted by a wire declaration."""

    OBJECT = "object"
    ARRAY = "array"
    STRING = "string"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    NULLABLE_STRING = "nullable_string"
    REFERENCE = "reference"


@dataclass(frozen=True, slots=True)
class Field:
    """Bind an object field name to a declared shape."""

    name: str
    shape: Shape
    required: bool = True


@dataclass(frozen=True, slots=True)
class Shape:
    """Describe JSON syntax; graph-wide semantic constraints remain codec-only."""

    kind: ShapeKind
    fields: tuple[Field, ...] = ()
    item: Shape | None = None
    values: tuple[str, ...] = ()
    variants: tuple[str, ...] = ()
    pattern: str | None = None
    minimum: int | None = None
    min_length: int | None = None


STRING = Shape(ShapeKind.STRING)
INTEGER = Shape(ShapeKind.INTEGER)
NON_NEGATIVE_INTEGER = Shape(ShapeKind.INTEGER, minimum=0)
ARITY_MAXIMUM = Shape(ShapeKind.INTEGER, minimum=-1)
BOOLEAN = Shape(ShapeKind.BOOLEAN)
NULLABLE_STRING = Shape(ShapeKind.NULLABLE_STRING)
NULLABLE_NON_EMPTY_STRING = Shape(ShapeKind.NULLABLE_STRING, min_length=1)
NON_EMPTY_STRING = Shape(ShapeKind.STRING, min_length=1)


def _field(name: str, shape: Shape) -> Field:
    return Field(name, shape)


def _object(*fields: Field) -> Shape:
    return Shape(ShapeKind.OBJECT, fields=fields)


def _array(item: Shape) -> Shape:
    return Shape(ShapeKind.ARRAY, item=item)


def _reference(*variants: str) -> Shape:
    return Shape(ShapeKind.REFERENCE, variants=variants)


QUALIFIED_NAME = Shape(ShapeKind.STRING, pattern=r"^[^:]+:[\s\S]+$", min_length=3)
_COLLAPSED = r"[ \t\r\n]*"
_LEXICAL_PATTERNS = {
    "boolean": rf"^{_COLLAPSED}(?:true|false|1|0){_COLLAPSED}$",
    "integer": rf"^{_COLLAPSED}[+-]?[0-9]+{_COLLAPSED}$",
    "decimal": rf"^{_COLLAPSED}[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+){_COLLAPSED}$",
    "double": rf"^{_COLLAPSED}(?:NaN|[+-]?INF|[+-]?(?:(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)){_COLLAPSED}$",
}


def _attribute_value(value_type: str) -> Shape:
    return _object(
        _field("name", QUALIFIED_NAME),
        _field("value_type", Shape(ShapeKind.STRING, values=(value_type,))),
        _field(
            "lexical",
            Shape(ShapeKind.STRING, pattern=_LEXICAL_PATTERNS.get(value_type)),
        ),
    )


ATTRIBUTE_VALUE = _reference(
    *(
        f"{value_type}_attribute_value"
        for value_type in ("string", "boolean", "integer", "decimal", "double")
    )
)
ATTRIBUTES = _array(ATTRIBUTE_VALUE)
ITEM_REFERENCE = _object(
    _field("tier", QUALIFIED_NAME), _field("index", NON_NEGATIVE_INTEGER)
)
ANCHOR = Shape(ShapeKind.REFERENCE, variants=("item_anchor", "tier_anchor"))
DURABLE_POSITION = _object(
    _field("anchor", ANCHOR),
    _field("side", Shape(ShapeKind.STRING, values=("before", "after"))),
)

DECLARATIONS: dict[str, Shape] = {
    **{
        f"{value_type}_attribute_value": _attribute_value(value_type)
        for value_type in ("string", "boolean", "integer", "decimal", "double")
    },
    "item_anchor": _object(
        _field("kind", Shape(ShapeKind.STRING, values=("item",))),
        _field("durable_id", NON_EMPTY_STRING),
    ),
    "tier_anchor": _object(
        _field("kind", Shape(ShapeKind.STRING, values=("tier",))),
        _field("tier", QUALIFIED_NAME),
    ),
    "item_reference": ITEM_REFERENCE,
    "durable_position": DURABLE_POSITION,
    "simple_relation": _object(
        _field("kind", Shape(ShapeKind.STRING, values=("simple",))),
        _field("name", QUALIFIED_NAME),
        _field("tier", QUALIFIED_NAME),
        _field("item_type", QUALIFIED_NAME),
        _field("attributes", ATTRIBUTES),
    ),
    "bipartite_relation": _object(
        _field("kind", Shape(ShapeKind.STRING, values=("bipartite",))),
        _field("name", QUALIFIED_NAME),
        _field("left_type", QUALIFIED_NAME),
        _field("right_type", QUALIFIED_NAME),
        _field("left_endpoint", Shape(ShapeKind.STRING, values=("item", "boundary"))),
        _field("right_endpoint", Shape(ShapeKind.STRING, values=("item", "boundary"))),
        _field("single_parent", BOOLEAN),
        _field("acyclic", BOOLEAN),
        _field("attributes", ATTRIBUTES),
    ),
    "relation_side": _object(
        _field(
            "endpoint_kinds",
            _array(Shape(ShapeKind.STRING, values=("item", "boundary"))),
        ),
        _field("tiers", _array(QUALIFIED_NAME)),
        _field("minimum", NON_NEGATIVE_INTEGER),
        # -1 is the sole wire sentinel for an unbounded side.
        _field("maximum", ARITY_MAXIMUM),
        _field("allow_empty", BOOLEAN),
    ),
    "polyadic_relation": _object(
        _field("kind", Shape(ShapeKind.STRING, values=("polyadic",))),
        _field("name", QUALIFIED_NAME),
        _field("sources", _reference("relation_side")),
        _field("targets", _reference("relation_side")),
        _field("unique_sources", BOOLEAN),
        _field("distinct_targets", BOOLEAN),
        _field("single_parent", BOOLEAN),
        _field("acyclic", BOOLEAN),
        _field("targets_subset_of", _array(QUALIFIED_NAME)),
        _field("attributes", ATTRIBUTES),
    ),
}

RELATION_DECLARATION = _reference(
    "simple_relation", "bipartite_relation", "polyadic_relation"
)
ENDPOINT = _reference("item_reference", "durable_position")
DECLARATIONS.update(
    {
        "binary_relation_instance": _object(
            _field("declaration", QUALIFIED_NAME),
            _field("left", ENDPOINT),
            _field("right", ENDPOINT),
            _field("durable_id", NULLABLE_NON_EMPTY_STRING),
            _field("attributes", ATTRIBUTES),
        ),
        "polyadic_relation_instance": _object(
            _field("declaration", QUALIFIED_NAME),
            _field("sources", _array(ENDPOINT)),
            _field("targets", _array(ENDPOINT)),
            _field("durable_id", NULLABLE_NON_EMPTY_STRING),
            _field("attributes", ATTRIBUTES),
        ),
    }
)
TIER_DECLARATION = _object(
    _field("name", QUALIFIED_NAME), _field("long_name", NON_EMPTY_STRING)
)
ITEM = _object(
    _field("durable_id", NULLABLE_NON_EMPTY_STRING), _field("attributes", ATTRIBUTES)
)
TIER = _object(
    _field("declaration", TIER_DECLARATION),
    _field("items", _array(ITEM)),
    _field("attributes", ATTRIBUTES),
)
GRAPH = _object(
    _field(
        "namespaces",
        _array(
            _object(
                _field("prefix", NON_EMPTY_STRING),
                _field("namespace", NON_EMPTY_STRING),
            )
        ),
    ),
    _field("tiers", _array(TIER)),
    _field("relation_declarations", _array(RELATION_DECLARATION)),
    _field(
        "relations",
        _array(_reference("binary_relation_instance", "polyadic_relation_instance")),
    ),
    _field(
        "attribute_declarations",
        _array(
            _object(
                _field("name", QUALIFIED_NAME),
                _field(
                    "domain",
                    Shape(
                        ShapeKind.STRING,
                        values=(
                            "item",
                            "tier",
                            "relation_declaration",
                            "relation_instance",
                            "position",
                            "document",
                        ),
                    ),
                ),
                _field(
                    "value_type",
                    Shape(
                        ShapeKind.STRING,
                        values=("string", "boolean", "integer", "decimal", "double"),
                    ),
                ),
            )
        ),
    ),
    _field(
        "position_values",
        _array(
            _object(
                _field("reference", _reference("item_reference", "durable_position")),
                _field("attributes", ATTRIBUTES),
            )
        ),
    ),
    _field("attributes", ATTRIBUTES),
)
DOCUMENT = _object(_field("format_version", STRING), _field("graph", GRAPH))


def _mark_omittable_fields(shape: Shape, seen: set[int] | None = None) -> None:
    """Make empty collections and null-valued fields optional throughout the shape."""
    visited = set() if seen is None else seen
    if id(shape) in visited:
        return
    visited.add(id(shape))
    if shape.kind is ShapeKind.OBJECT:
        object.__setattr__(
            shape,
            "fields",
            tuple(
                Field(
                    field.name,
                    field.shape,
                    field.shape.kind
                    not in (ShapeKind.ARRAY, ShapeKind.NULLABLE_STRING),
                )
                for field in shape.fields
            ),
        )
        for field in shape.fields:
            _mark_omittable_fields(field.shape, visited)
    elif shape.kind is ShapeKind.ARRAY and shape.item is not None:
        _mark_omittable_fields(shape.item, visited)
    elif shape.kind is ShapeKind.REFERENCE:
        for name in shape.variants:
            _mark_omittable_fields(DECLARATIONS[name], visited)


_mark_omittable_fields(DOCUMENT)


def object_fields(shape: Shape) -> set[str]:
    """Return parser metadata for an object declaration."""
    if shape.kind is not ShapeKind.OBJECT:
        raise TypeError("object field metadata requires an object shape")
    return {field.name for field in shape.fields}


def required_object_fields(shape: Shape) -> set[str]:
    """Return the fields that cannot be recovered from an omitted value."""
    if shape.kind is not ShapeKind.OBJECT:
        raise TypeError("required field metadata requires an object shape")
    return {field.name for field in shape.fields if field.required}


def field_shape(shape: Shape, name: str) -> Shape:
    """Return a declared child shape for parser metadata traversal."""
    if shape.kind is not ShapeKind.OBJECT:
        raise TypeError("field metadata requires an object shape")
    return next(field.shape for field in shape.fields if field.name == name)


def array_item(shape: Shape) -> Shape:
    """Return the declared array member shape for parser metadata traversal."""
    if shape.kind is not ShapeKind.ARRAY or shape.item is None:
        raise TypeError("item metadata requires an array shape")
    return shape.item


def declaration_data() -> dict[str, JsonValue]:
    """Return the declaration itself as deterministic JSON data."""
    return {
        "document": _shape_data(DOCUMENT),
        "definitions": {
            name: _shape_data(shape) for name, shape in sorted(DECLARATIONS.items())
        },
    }


def shape_hash() -> str:
    """Hash the declaration independently of JSON Schema presentation."""
    encoded = json.dumps(
        declaration_data(), sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def json_schema(format_version: str) -> dict[str, JsonValue]:
    """Generate the JSON Schema document for one codec format version."""
    return json_schema_for(DOCUMENT, DECLARATIONS, format_version)


def json_schema_for(
    document: Shape, definitions_source: dict[str, Shape], format_version: str
) -> dict[str, JsonValue]:
    """Generate JSON Schema from an explicitly supplied declaration graph."""
    definitions = {
        name: _json_schema(shape) for name, shape in sorted(definitions_source.items())
    }
    root = cast(dict[str, JsonValue], _json_schema(document))
    properties = cast(dict[str, JsonValue], root["properties"])
    properties["format_version"] = {"const": format_version}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://tiergraph.org/schema/format-{format_version}.json",
        "title": "tiergraph primitive document",
        "description": (
            "Structural validation is necessary but not sufficient for codec "
            "acceptance. Codec-only checks include, but are not limited to, "
            "referential integrity; unique declaration, tier, item, durable-id, "
            "position, and relation names; one namespace prefix per URI; one "
            "simple relation per tier; relation-instance references to bipartite "
            "declarations; endpoint typing; non-empty position attributes; and "
            "single_parent and acyclic promises. The codec also requires structural "
            "indices to use an integral JSON spelling: JSON Schema considers a "
            "number such as 1.0 an integer, while the codec deliberately refuses "
            "it. This list is illustrative rather than exhaustive; JSON Schema is "
            "the structural contract and codec acceptance remains authoritative. "
            "Older format versions are refused rather than migrated because "
            "migration requires an explicit, loss-aware conversion outside the "
            "primitive codec."
        ),
        **root,
        "$defs": definitions,
    }


def validation_errors(value: object, format_version: str) -> list[str]:
    """Return deterministic structural errors derived from the wire declaration."""
    errors = _validation_errors(value, DOCUMENT, "document")
    if (
        not errors
        and cast(dict[str, object], value)["format_version"] != format_version
    ):
        errors.append(
            f"format_version {cast(dict[str, object], value)['format_version']!r} "
            f"is unsupported; expected {format_version!r}"
        )
    return errors


def _validation_errors(value: object, shape: Shape, path: str) -> list[str]:
    if shape.kind is ShapeKind.REFERENCE:
        alternatives = [
            _validation_errors(value, DECLARATIONS[name], path)
            for name in shape.variants
        ]
        if any(not errors for errors in alternatives):
            return []
        best_index = max(
            range(len(shape.variants)),
            key=lambda index: _shape_match_score(
                value, DECLARATIONS[shape.variants[index]]
            ),
        )
        return [alternatives[best_index][0]]
    if shape.kind is ShapeKind.OBJECT:
        if not isinstance(value, dict) or not all(
            isinstance(key, str) for key in value
        ):
            return [f"{path} must be an object"]
        expected = object_fields(shape)
        missing = required_object_fields(shape) - value.keys()
        extra = value.keys() - expected
        if missing:
            return [f"{path} is missing field {min(missing)!r}"]
        if extra:
            return [f"{path} has unknown field {min(extra)!r}"]
        for field in shape.fields:
            if field.name not in value:
                continue
            errors = _validation_errors(
                value[field.name], field.shape, f"{path}.{field.name}"
            )
            if errors:
                return errors
        return []
    if shape.kind is ShapeKind.ARRAY:
        if not isinstance(value, list):
            return [f"{path} must be an array"]
        assert shape.item is not None
        for index, item in enumerate(value):
            errors = _validation_errors(item, shape.item, f"{path}[{index}]")
            if errors:
                return errors
        return []
    if shape.kind is ShapeKind.NULLABLE_STRING:
        if value is None or (
            isinstance(value, str)
            and (shape.min_length is None or len(value) >= shape.min_length)
        ):
            return []
        return [f"{path} must be a string or null"]
    expected_type = {
        ShapeKind.STRING: str,
        ShapeKind.INTEGER: int,
        ShapeKind.BOOLEAN: bool,
    }[shape.kind]
    if type(value) is not expected_type:
        return [f"{path} must be a {shape.kind.value}"]
    if shape.values and value not in shape.values:
        return [f"{path} has unsupported value {value!r}"]
    if shape.kind is ShapeKind.STRING:
        if shape.min_length is not None and len(cast(str, value)) < shape.min_length:
            return [f"{path} must not be empty"]
        if (
            shape.pattern is not None
            and re.fullmatch(shape.pattern, cast(str, value)) is None
        ):
            return [f"{path} has an invalid lexical form"]
    if (
        shape.kind is ShapeKind.INTEGER
        and shape.minimum is not None
        and cast(int, value) < shape.minimum
    ):
        return [f"{path} must be at least {shape.minimum}"]
    return []


def _shape_match_score(value: object, shape: Shape) -> int:
    """Score how specifically a value matches a union alternative."""
    if shape.kind is not ShapeKind.OBJECT or not isinstance(value, dict):
        return 0
    score = sum(field.name in value for field in shape.fields)
    score += sum(
        field.shape.kind is ShapeKind.STRING
        and bool(field.shape.values)
        and value[field.name] in field.shape.values
        for field in shape.fields
        if field.name in value
    )
    return score


def _shape_data(shape: Shape) -> dict[str, JsonValue]:
    return {
        "kind": shape.kind.value,
        "fields": [
            {
                "name": field.name,
                "shape": _shape_data(field.shape),
                "required": field.required,
            }
            for field in shape.fields
        ],
        "item": _shape_data(shape.item) if shape.item is not None else None,
        "values": list(shape.values),
        "variants": list(shape.variants),
        "pattern": shape.pattern,
        "minimum": shape.minimum,
        "min_length": shape.min_length,
    }


def _json_schema(shape: Shape) -> JsonValue:
    if shape.kind is ShapeKind.OBJECT:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": [field.name for field in shape.fields if field.required],
            "properties": {
                field.name: _json_schema(field.shape) for field in shape.fields
            },
        }
    if shape.kind is ShapeKind.ARRAY:
        assert shape.item is not None
        return {"type": "array", "items": _json_schema(shape.item)}
    if shape.kind is ShapeKind.REFERENCE:
        return {"oneOf": [{"$ref": f"#/$defs/{name}"} for name in shape.variants]}
    if shape.kind is ShapeKind.NULLABLE_STRING:
        string_schema: dict[str, JsonValue] = {"type": "string"}
        if shape.min_length is not None:
            string_schema["minLength"] = shape.min_length
        return {"anyOf": [string_schema, {"type": "null"}]}
    result: dict[str, JsonValue] = {"type": shape.kind.value}
    if shape.values:
        result["enum"] = list(shape.values)
    if shape.pattern is not None:
        result["pattern"] = shape.pattern
    if shape.minimum is not None:
        result["minimum"] = shape.minimum
    if shape.min_length is not None:
        result["minLength"] = shape.min_length
    return result
