"""Declare the primitive wire shape and derive its validators and schema."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

# RefusalStage is declared in the base module, the one place both refusal
# channels can reach without a cycle.  It stays named here, and in __all__, so
# that this remains the module a reader of refusals imports the stage from.
from tiergraph.core import JsonValue, RefusalStage


class Refusal(ValueError):
    """Refuse one read, naming its stage and every further applicable condition.

    ``stage`` places the refusal in the declared total order, and ``also``
    carries the conditions that remain applicable once this one is known, each a
    refusal in its own right.  Both are data rather than prose, so a caller acts
    on the order without matching message text.  A ``Refusal`` is a
    ``ValueError``, so every caller that already catches one still does.
    """

    def __init__(
        self,
        stage: RefusalStage,
        message: str,
        also: Iterable[Refusal] = (),
    ) -> None:
        """Record the stage and the further conditions that still apply."""
        super().__init__(message)
        self.stage = stage
        self.also = tuple(also)


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
DURABLE_BOUNDARY = _object(
    _field("anchor", ANCHOR),
    _field("side", Shape(ShapeKind.STRING, values=("before", "after"))),
)
DURABLE_ITEM_ENDPOINT = _object(
    _field("kind", Shape(ShapeKind.STRING, values=("durable-item",))),
    _field("durable_id", NON_EMPTY_STRING),
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
    "durable_item_endpoint": DURABLE_ITEM_ENDPOINT,
    "durable_position": DURABLE_BOUNDARY,
    "tier_seal_carrier": _object(
        _field("kind", Shape(ShapeKind.STRING, values=("tier",))),
        _field("tier", QUALIFIED_NAME),
    ),
    "graph_seal_carrier": _object(
        _field("kind", Shape(ShapeKind.STRING, values=("graph",))),
        _field(
            "name",
            Shape(
                ShapeKind.STRING,
                values=("relations", "polyadic_relations"),
            ),
        ),
    ),
    "layer_name": _object(
        _field("vocabulary", NON_EMPTY_STRING), _field("source", NON_EMPTY_STRING)
    ),
    "layer_item": _object(
        _field("kind", Shape(ShapeKind.STRING, values=("item-coordinate",))),
        _field("tier", QUALIFIED_NAME),
        _field("index", NON_NEGATIVE_INTEGER),
    ),
    "layer_boundary": _object(
        _field("kind", Shape(ShapeKind.STRING, values=("boundary-coordinate",))),
        _field("tier", QUALIFIED_NAME),
        _field("index", NON_NEGATIVE_INTEGER),
    ),
    "layer_durable_item": _object(
        _field("kind", Shape(ShapeKind.STRING, values=("durable-item",))),
        _field("durable_id", NON_EMPTY_STRING),
    ),
    "layer_durable_boundary": _object(
        _field("kind", Shape(ShapeKind.STRING, values=("durable-boundary",))),
        _field("anchor", ANCHOR),
        _field("side", Shape(ShapeKind.STRING, values=("before", "after"))),
    ),
    "layer_tier": _object(
        _field("kind", Shape(ShapeKind.STRING, values=("tier",))),
        _field("tier", QUALIFIED_NAME),
    ),
    "layer_relation_declaration": _object(
        _field("kind", Shape(ShapeKind.STRING, values=("relation-declaration",))),
        _field("relation", QUALIFIED_NAME),
    ),
    "layer_relation_instance": _object(
        _field("kind", Shape(ShapeKind.STRING, values=("relation-instance",))),
        _field("index", NON_NEGATIVE_INTEGER),
    ),
    "layer_durable_relation": _object(
        _field("kind", Shape(ShapeKind.STRING, values=("durable-relation",))),
        _field("durable_id", NON_EMPTY_STRING),
    ),
    "layer_polyadic_instance": _object(
        _field("kind", Shape(ShapeKind.STRING, values=("polyadic-instance",))),
        _field("index", NON_NEGATIVE_INTEGER),
    ),
    "layer_durable_polyadic": _object(
        _field("kind", Shape(ShapeKind.STRING, values=("durable-polyadic",))),
        _field("durable_id", NON_EMPTY_STRING),
    ),
    "layer_document": _object(
        _field("kind", Shape(ShapeKind.STRING, values=("document",)))
    ),
    "layer_orphan_index": _object(
        _field("kind", Shape(ShapeKind.STRING, values=("index",))),
        _field("index", NON_NEGATIVE_INTEGER),
    ),
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
LAYER_LIVE_SUBJECT = _reference(
    "layer_item",
    "layer_durable_item",
    "layer_boundary",
    "layer_durable_boundary",
    "layer_tier",
    "layer_relation_declaration",
    "layer_relation_instance",
    "layer_durable_relation",
    "layer_polyadic_instance",
    "layer_durable_polyadic",
    "layer_document",
)
DECLARATIONS["layer_orphan"] = _object(
    _field("kind", Shape(ShapeKind.STRING, values=("orphaned",))),
    _field("carrier", _reference("tier_seal_carrier", "graph_seal_carrier")),
    _field("was", _reference("layer_item", "layer_boundary", "layer_orphan_index")),
)
LAYER_SUBJECT = _reference(*LAYER_LIVE_SUBJECT.variants, "layer_orphan")
DECLARATIONS["layer_fact"] = _object(
    _field("subject", LAYER_SUBJECT), _field("value", ATTRIBUTE_VALUE)
)
DECLARATIONS["layer"] = _object(
    _field("name", _reference("layer_name")),
    _field("facts", _array(_reference("layer_fact"))),
)

RELATION_DECLARATION = _reference(
    "simple_relation", "bipartite_relation", "polyadic_relation"
)
ENDPOINT = _reference("item_reference", "durable_item_endpoint", "durable_position")
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
                            "boundary",
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
    _field(
        "seals",
        _array(
            _object(
                _field(
                    "carrier",
                    _reference("tier_seal_carrier", "graph_seal_carrier"),
                ),
                _field("sealed", NON_NEGATIVE_INTEGER),
            )
        ),
    ),
    _field("layers", _array(_reference("layer"))),
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


def _field_set_refusal(
    actual: AbstractSet[str],
    declared: AbstractSet[str],
    required: AbstractSet[str],
    path: str,
) -> Refusal | None:
    """Return one refusal naming every missing and every unknown field, or None.

    Both directions are named in a single message so a consumer holding a
    document this reader cannot read learns the whole difference from one
    attempt rather than one lexically first name per attempt.  A declared field
    that is not required is absent from both lists, so an optional field is
    never reported as missing.

    The two directions are separate conditions of one node, so when both hold
    the unknown-field condition is also carried as data on ``also`` rather than
    being readable only by parsing the combined message.
    """
    missing = sorted(required - actual)
    unknown = sorted(actual - declared)
    if missing and unknown:
        return Refusal(
            RefusalStage.SHAPE,
            f"{path} is missing fields {missing!r} and has unknown fields {unknown!r}",
            (Refusal(RefusalStage.SHAPE, f"{path} has unknown fields {unknown!r}"),),
        )
    if missing:
        return Refusal(RefusalStage.SHAPE, f"{path} is missing fields {missing!r}")
    if unknown:
        return Refusal(RefusalStage.SHAPE, f"{path} has unknown fields {unknown!r}")
    return None


def _refuse_field_set(
    actual: AbstractSet[str],
    declared: AbstractSet[str],
    required: AbstractSet[str],
    path: str,
) -> None:
    """Refuse an object whose field set is not the declared one."""
    refusal = _field_set_refusal(actual, declared, required, path)
    if refusal is not None:
        raise refusal


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


def declared_minimum(shape: Shape) -> int:
    """Return a declared integer lower bound for parser metadata traversal.

    A reader that spelled the bound itself would keep admitting what the
    declaration had stopped admitting, so the bound is read from here.
    """
    if shape.kind is not ShapeKind.INTEGER or shape.minimum is None:
        raise TypeError("minimum metadata requires a bounded integer shape")
    return shape.minimum


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
    """Generate the JSON Schema document for the format this release implements."""
    from tiergraph.wire import FORMAT_VERSION  # noqa: PLC0415 -- cycle breaker

    if format_version != FORMAT_VERSION:
        raise Refusal(
            RefusalStage.DISCRIMINATOR,
            f"format_version {format_version!r} is unsupported; expected "
            f"{FORMAT_VERSION!r}; this release publishes only the schema for "
            f"the format it implements",
        )
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
            "referential integrity; unique declaration, tier, item, and relation "
            "names; unique item and boundary durable ids; one namespace prefix per "
            "URI; one "
            "simple relation per tier; relation-instance references to bipartite "
            "declarations; endpoint typing; non-empty boundary attributes; and "
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
    """Return every applicable structural error, in the declared refusal order.

    A document carrying several problems at once reports all of them from one
    attempt, ordered outside in and by :class:`RefusalStage` within a node, so
    the first entry is the primary refusal and the rest are the conditions that
    remain applicable beside it.  A foreign version is reported alone: the field
    sets of a declaration the document never selected are not judged.
    """
    declared = _declared_format_version(value)
    if declared is not None and declared != format_version:
        return [
            f"format_version {declared!r} is unsupported; expected {format_version!r}"
        ]
    return _validation_errors(value, DOCUMENT, "document")


def _declared_format_version(value: object) -> str | None:
    """Return the announced version, or None when none is declared as a string."""
    if not isinstance(value, dict):
        return None
    announced = value.get("format_version")
    if not isinstance(announced, str):
        return None
    return announced


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
        return alternatives[best_index]
    if shape.kind is ShapeKind.OBJECT:
        if not isinstance(value, dict) or not all(
            isinstance(key, str) for key in value
        ):
            return [f"{path} must be an object"]
        refusal = _field_set_refusal(
            value.keys(), object_fields(shape), required_object_fields(shape), path
        )
        if refusal is not None:
            return [str(refusal)]
        errors: list[str] = []
        for field in shape.fields:
            if field.name in value:
                errors.extend(
                    _validation_errors(
                        value[field.name], field.shape, f"{path}.{field.name}"
                    )
                )
        return errors
    if shape.kind is ShapeKind.ARRAY:
        if not isinstance(value, list):
            return [f"{path} must be an array"]
        assert shape.item is not None
        return [
            error
            for index, item in enumerate(value)
            for error in _validation_errors(item, shape.item, f"{path}[{index}]")
        ]
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
        # The article follows the spelling rather than the kind, so a
        # construction named later cannot reintroduce 'a integer'.
        article = "an" if shape.kind.value[0] in "aeiou" else "a"
        return [f"{path} must be {article} {shape.kind.value}"]
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


__all__ = ["Refusal", "RefusalStage", "json_schema", "shape_hash"]
