"""Construct declaration-derived schema/codec conformance probes."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import cast

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from tests.conformance.declared_schema_codec_divergences import (
    DECLARED_DIVERGENCES,
)
from tiergraph.core import JsonValue
from tiergraph.schema import (
    DECLARATIONS,
    Field,
    Shape,
    ShapeKind,
    json_schema,
    json_schema_for,
    validation_errors,
)
from tiergraph.wire import FORMAT_VERSION, loads

type PathPart = str | int
type Path = tuple[PathPart, ...]


@dataclass(frozen=True, slots=True)
class Probe:
    """Describe one near-miss constructed from a declaration location."""

    seed: str
    path: Path
    mutation: str
    document: dict[str, JsonValue]

    @property
    def id(self) -> str:
        """Return a deterministic diagnostic identity."""
        rendered = "document" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}" for part in self.path
        )
        return f"{self.seed}:{rendered}:{self.mutation}"


@dataclass(frozen=True, slots=True)
class Drift:
    """Record an undeclared disagreement among structural acceptance paths."""

    probe: Probe
    schema_accepts: bool
    codec_diagnostic: str
    validation_diagnostic: str


def undeclared_drifts(probes: tuple[Probe, ...]) -> tuple[Drift, ...]:
    """Compare all acceptance paths and subtract only policy-listed divergence."""
    validator = Draft202012Validator(json_schema(FORMAT_VERSION))
    result: list[Drift] = []
    for probe in probes:
        schema_accepts = validator.is_valid(probe.document)
        try:
            validation_diagnostic = "; ".join(
                validation_errors(probe.document, FORMAT_VERSION)
            )
        except Exception as error:
            validation_accepts = False
            validation_diagnostic = f"{type(error).__name__}: {error}"
            result.append(
                Drift(
                    probe,
                    schema_accepts,
                    "not run",
                    validation_diagnostic,
                )
            )
            continue
        else:
            validation_accepts = not validation_diagnostic
            if validation_accepts:
                validation_diagnostic = "accepted"
        try:
            loads(encoded(probe.document))
        except Exception as error:
            codec_accepts = False
            diagnostic = f"{type(error).__name__}: {error}"
            if not isinstance(error, ValueError):
                result.append(
                    Drift(probe, schema_accepts, diagnostic, validation_diagnostic)
                )
                continue
        else:
            codec_accepts = True
            diagnostic = "accepted"
        if schema_accepts == codec_accepts == validation_accepts:
            continue
        declared = (
            schema_accepts
            and not codec_accepts
            and any(
                divergence.matches(probe.id)
                and validation_accepts is divergence.validation_accepts
                for divergence in DECLARED_DIVERGENCES
            )
        )
        if not declared:
            result.append(
                Drift(probe, schema_accepts, diagnostic, validation_diagnostic)
            )
    return tuple(result)


def conformance_probes(
    seeds: tuple[tuple[str, dict[str, JsonValue]], ...], document_shape: Shape
) -> tuple[Probe, ...]:
    """Construct every applicable near-miss from the live declaration and seeds."""
    probes = [
        probe
        for seed_name, seed in seeds
        for probe in _walk(seed_name, seed, seed, document_shape, ())
    ]
    return tuple(sorted(probes, key=lambda probe: probe.id))


def _walk(
    seed_name: str,
    seed: dict[str, JsonValue],
    value: JsonValue,
    shape: Shape,
    path: Path,
) -> list[Probe]:
    if shape.kind is ShapeKind.REFERENCE:
        matching = [
            name
            for name in shape.variants
            if Draft202012Validator(_fragment_schema(DECLARATIONS[name])).is_valid(
                {"value": value}
            )
        ]
        if len(matching) != 1:
            raise AssertionError(
                f"{seed_name}:{path!r} matches {matching!r}, expected one variant"
            )
        return _walk(seed_name, seed, value, DECLARATIONS[matching[0]], path)

    probes = (
        []
        if not path
        else [
            _replacement(seed_name, seed, path, label, replacement)
            for label, replacement in _replacements(shape, value)
        ]
    )
    if shape.kind is ShapeKind.OBJECT:
        data = cast(dict[str, JsonValue], value)
        for field in shape.fields:
            if field.name not in data:
                probes.append(
                    Probe(
                        seed_name, (*path, field.name), "missing", copy.deepcopy(seed)
                    )
                )
                expanded = copy.deepcopy(seed)
                expanded_data = cast(dict[str, JsonValue], _at(expanded, path))
                expanded_data[field.name] = _example(field.shape)
                probes.extend(
                    _walk(
                        seed_name,
                        expanded,
                        expanded_data[field.name],
                        field.shape,
                        (*path, field.name),
                    )
                )
                continue
            missing = copy.deepcopy(seed)
            del cast(dict[str, JsonValue], _at(missing, path))[field.name]
            probes.append(Probe(seed_name, (*path, field.name), "missing", missing))
            probes.extend(
                _walk(
                    seed_name,
                    seed,
                    data[field.name],
                    field.shape,
                    (*path, field.name),
                )
            )
        extra = copy.deepcopy(seed)
        cast(dict[str, JsonValue], _at(extra, path))["__unknown__"] = None
        probes.append(Probe(seed_name, path, "unknown-field", extra))
    elif shape.kind is ShapeKind.ARRAY:
        assert shape.item is not None
        for index, item in enumerate(cast(list[JsonValue], value)):
            probes.extend(_walk(seed_name, seed, item, shape.item, (*path, index)))
    return probes


def _example(shape: Shape) -> JsonValue:
    """Construct a deterministic declaration-only witness for newly added surface."""
    if shape.kind is ShapeKind.REFERENCE:
        return _example(DECLARATIONS[shape.variants[0]])
    if shape.kind is ShapeKind.OBJECT:
        return {field.name: _example(field.shape) for field in shape.fields}
    if shape.kind is ShapeKind.ARRAY:
        return []
    if shape.kind is ShapeKind.NULLABLE_STRING:
        return None
    if shape.kind is ShapeKind.STRING:
        if shape.values:
            return shape.values[0]
        if shape.pattern is not None:
            return "0"
        return "x" if shape.min_length is not None else ""
    if shape.kind is ShapeKind.INTEGER:
        return 0 if shape.minimum is None else shape.minimum
    if shape.kind is ShapeKind.BOOLEAN:
        return False
    raise AssertionError(f"unsupported shape kind {shape.kind}")


def _replacements(shape: Shape, value: JsonValue) -> tuple[tuple[str, JsonValue], ...]:
    """Return boundary and wrong-type values meaningful for one declared shape."""
    wrong: dict[ShapeKind, tuple[JsonValue, ...]] = {
        ShapeKind.OBJECT: (None, [], "wrong", 0, False),
        ShapeKind.ARRAY: (None, {}, "wrong", 0, False),
        ShapeKind.STRING: (None, [], {}, 0, False),
        ShapeKind.INTEGER: (None, [], {}, "wrong", False, 1.0),
        ShapeKind.BOOLEAN: (None, [], {}, "wrong", 0, 1),
        ShapeKind.NULLABLE_STRING: ([], {}, 0, False),
        ShapeKind.REFERENCE: (),
    }
    candidates: list[tuple[str, JsonValue]] = [
        (f"wrong-type-{type(candidate).__name__}", candidate)
        for candidate in wrong[shape.kind]
        if candidate != value or type(candidate) is not type(value)
    ]
    if shape.kind in (ShapeKind.STRING, ShapeKind.NULLABLE_STRING):
        candidates.append(("empty", ""))
        if shape.values:
            candidates.extend(
                (f"enum-{index}", spelling)
                for index, spelling in enumerate(shape.values)
                if spelling != value
            )
            candidates.append(("outside-enum", "__outside_enum__"))
        if shape.pattern is not None:
            candidates.extend(
                (
                    ("lexical-edge-whitespace", " \t0\r\n"),
                    ("lexical-edge-plus", "+0"),
                    ("lexical-edge-leading-dot", ".0"),
                    ("lexical-edge-nan", "NaN"),
                    ("lexical-edge-negative-infinity", "-INF"),
                    ("lexical-edge-exponent", "0e0"),
                    ("outside-lexical-empty", ""),
                    ("outside-lexical", "x"),
                )
            )
    elif shape.kind is ShapeKind.INTEGER:
        for number in (-2, -1, 0, 1, 2):
            if number != value:
                candidates.append((f"integer-{number}", number))
        if shape.minimum is not None:
            candidates.extend(
                (
                    ("minimum", shape.minimum),
                    ("below-minimum", shape.minimum - 1),
                )
            )
    return tuple(candidates)


def _replacement(
    seed_name: str,
    seed: dict[str, JsonValue],
    path: Path,
    label: str,
    replacement: JsonValue,
) -> Probe:
    document = copy.deepcopy(seed)
    parent = _at(document, path[:-1])
    part = path[-1]
    if isinstance(part, str):
        cast(dict[str, JsonValue], parent)[part] = replacement
    else:
        cast(list[JsonValue], parent)[part] = replacement
    return Probe(seed_name, path, label, document)


def _at(value: JsonValue, path: Path) -> JsonValue:
    for part in path:
        value = (
            cast(dict[str, JsonValue], value)[part]
            if isinstance(part, str)
            else cast(list[JsonValue], value)[part]
        )
    return value


def _fragment_schema(shape: Shape) -> dict[str, object]:
    """Build a self-contained schema for resolving a realized reference variant."""
    wrapper = Shape(ShapeKind.OBJECT, fields=(Field("value", shape),))
    generated = json_schema_for(wrapper, DECLARATIONS, "probe")
    properties = cast(dict[str, JsonValue], generated["properties"])
    properties.pop("format_version", None)
    return cast(dict[str, object], generated)


def encoded(document: object) -> str:
    """Serialize a probe exactly as the public codec receives it."""
    return json.dumps(document, allow_nan=False, sort_keys=True)
