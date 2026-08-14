"""The reference declaration satisfies the reusable generated-schema laws."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from scripts.generate_schema import (
    committed_stamp,
    generated_bytes,
    refuse_unversioned_shape_change,
    stamp_bytes,
)
from scripts.generate_schema import (
    main as generate_main,
)

from tests.conformance.schema import SchemaLawSuite
from tests.test_wire import rich_graph
from tiergraph import schema as schema_module
from tiergraph.core import JsonValue
from tiergraph.schema import (
    DECLARATIONS,
    DOCUMENT,
    NULLABLE_STRING,
    STRING,
    TIER,
    Field,
    Shape,
    ShapeKind,
    array_item,
    field_shape,
    json_schema,
    json_schema_for,
    object_fields,
    validation_errors,
)
from tiergraph.wire import FORMAT_VERSION, dumps, loads, to_data

SCHEMA_PATH = Path("schema/tiergraph.schema.json")
STAMP_PATH = Path("schema/tiergraph.schema.sha256")


def changed_schema_bytes() -> bytes:
    """Generate from a real shape change without mutating the live declaration."""
    changed = replace(DOCUMENT, fields=(*DOCUMENT.fields, Field("extension", STRING)))
    schema = json_schema_for(changed, DECLARATIONS, FORMAT_VERSION)
    return (
        json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()


LAWS = SchemaLawSuite(
    generated_bytes,
    lambda: validation_errors(to_data(rich_graph()), FORMAT_VERSION),
    changed_schema_bytes,
)


@pytest.mark.parametrize(
    "law",
    [
        LAWS.check_fixture_validation,
        LAWS.check_generation_is_deterministic,
        LAWS.check_generation_observes_shape,
    ],
    ids=lambda law: law.__name__,
)
def test_schema_law(law: object) -> None:
    """Run each reusable law against the reference declaration."""
    assert callable(law)
    law()


def test_generated_schema_is_json_data() -> None:
    """Expose the generated document through the reusable JSON-data check."""
    schema = json_schema(FORMAT_VERSION)
    LAWS.check_schema_is_json_data(schema)
    json.dumps(schema, allow_nan=False)


def test_committed_artifact_and_hash_match_generation() -> None:
    """An edited or stale artifact cannot agree with its committed digest."""
    schema_bytes = SCHEMA_PATH.read_bytes()
    stamp = cast(dict[str, str], json.loads(STAMP_PATH.read_text()))
    assert schema_bytes == generated_bytes()
    assert stamp["schema_sha256"] == hashlib.sha256(schema_bytes).hexdigest()


def test_shape_or_artifact_change_requires_format_version_change() -> None:
    """The prior stamp refuses either bound digest under the same version."""
    prior = cast(dict[str, object], json.loads(STAMP_PATH.read_text()))
    for digest in ("shape_sha256", "schema_sha256"):
        altered = {**prior, digest: "edited"}
        with pytest.raises(
            ValueError,
            match="schema declaration or generated artifact changed without moving FORMAT_VERSION",
        ):
            refuse_unversioned_shape_change(prior, altered)
        altered["format_version"] = str(int(FORMAT_VERSION) - 1)
        refuse_unversioned_shape_change(prior, altered)


def test_committed_stamp_fails_closed_with_distinct_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing or corrupt committed baseline refuses cleanly and specifically."""

    def missing(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(128, ["git", "show"])

    monkeypatch.setattr(subprocess, "run", missing)
    with pytest.raises(ValueError, match="stamp is unavailable"):
        committed_stamp()
    with pytest.raises(SystemExit, match="stamp is unavailable"):
        generate_main(["--check"])

    def corrupt(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["git", "show"], 0, "not JSON", "")

    monkeypatch.setattr(subprocess, "run", corrupt)
    with pytest.raises(ValueError, match="stamp is not valid JSON"):
        committed_stamp()


def test_generator_weakening_is_caught_by_artifact_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opening generated object key sets fires the version gate."""
    baseline = json.loads(stamp_bytes(generated_bytes()))
    original = schema_module._json_schema

    def open_objects(shape: Shape) -> JsonValue:
        generated = original(shape)
        if isinstance(generated, dict):
            generated.pop("additionalProperties", None)
        return generated

    monkeypatch.setattr(schema_module, "_json_schema", open_objects)
    weakened = generated_bytes()
    assert hashlib.sha256(weakened).hexdigest() != baseline["schema_sha256"]
    assert schema_module.shape_hash() == baseline["shape_sha256"]
    with pytest.raises(
        ValueError,
        match="schema declaration or generated artifact changed without moving FORMAT_VERSION",
    ):
        refuse_unversioned_shape_change(baseline, json.loads(stamp_bytes(weakened)))


def test_check_refuses_honestly_regenerated_unversioned_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The check path compares an honest regeneration with the committed baseline."""
    schema_path = tmp_path / "tiergraph.schema.json"
    stamp_path = tmp_path / "tiergraph.schema.sha256"
    baseline = json.loads(stamp_bytes(generated_bytes()))
    monkeypatch.setattr("scripts.generate_schema.SCHEMA_PATH", schema_path)
    monkeypatch.setattr("scripts.generate_schema.STAMP_PATH", stamp_path)
    original = TIER.fields
    object.__setattr__(TIER, "fields", (*original, Field("extension", STRING)))
    try:
        schema_bytes = generated_bytes()
        schema_path.write_bytes(schema_bytes)
        stamp_path.write_bytes(stamp_bytes(schema_bytes))
        with pytest.raises(
            SystemExit,
            match="schema declaration or generated artifact changed without moving FORMAT_VERSION",
        ):
            generate_main(["--check"], baseline)
    finally:
        object.__setattr__(TIER, "fields", original)


def test_validation_rejects_each_declared_json_construction() -> None:
    """Near-valid edits exercise the validator produced from every shape kind."""
    valid = to_data(rich_graph())
    assert validation_errors(valid, "4") == [
        f"format_version {FORMAT_VERSION!r} is unsupported; expected '4'"
    ]
    assert validation_errors([], FORMAT_VERSION) == ["document must be an object"]
    assert validation_errors({1: "value"}, FORMAT_VERSION) == [
        "document must be an object"
    ]

    cases: list[tuple[dict[str, JsonValue], str]] = []
    missing = cast(dict[str, JsonValue], json.loads(json.dumps(valid)))
    del missing["graph"]
    cases.append((missing, "document is missing field 'graph'"))

    extra = cast(dict[str, JsonValue], json.loads(json.dumps(valid)))
    extra["extension"] = None
    cases.append((extra, "document has unknown field 'extension'"))

    not_array = cast(dict[str, JsonValue], json.loads(json.dumps(valid)))
    cast(dict[str, JsonValue], not_array["graph"])["tiers"] = {}
    cases.append((not_array, "document.graph.tiers must be an array"))

    bad_array_item = cast(dict[str, JsonValue], json.loads(json.dumps(valid)))
    cast(dict[str, JsonValue], bad_array_item["graph"])["tiers"] = [None]
    cases.append((bad_array_item, "document.graph.tiers[0] must be an object"))

    bad_nullable = cast(dict[str, JsonValue], json.loads(json.dumps(valid)))
    graph = cast(dict[str, JsonValue], bad_nullable["graph"])
    relation = cast(list[dict[str, JsonValue]], graph["relations"])[0]
    relation["durable_id"] = False
    cases.append(
        (
            bad_nullable,
            "document.graph.relations[0].durable_id must be a string or null",
        )
    )

    bad_scalar = cast(dict[str, JsonValue], json.loads(json.dumps(valid)))
    bad_scalar["format_version"] = False
    cases.append((bad_scalar, "document.format_version must be a string"))

    negative_index = cast(dict[str, JsonValue], json.loads(json.dumps(valid)))
    graph = cast(dict[str, JsonValue], negative_index["graph"])
    relation = cast(list[dict[str, JsonValue]], graph["relations"])[0]
    cast(dict[str, JsonValue], relation["left"])["index"] = -1
    cases.append(
        (negative_index, "document.graph.relations[0].left.index must be at least 0")
    )

    bad_enum = cast(dict[str, JsonValue], json.loads(json.dumps(valid)))
    graph = cast(dict[str, JsonValue], bad_enum["graph"])
    declaration = cast(list[dict[str, JsonValue]], graph["attribute_declarations"])[0]
    declaration["domain"] = "node"
    cases.append(
        (
            bad_enum,
            "document.graph.attribute_declarations[0].domain has unsupported value 'node'",
        )
    )

    bad_union = cast(dict[str, JsonValue], json.loads(json.dumps(valid)))
    graph = cast(dict[str, JsonValue], bad_union["graph"])
    declaration = cast(list[dict[str, JsonValue]], graph["relation_declarations"])[0]
    declaration["kind"] = "ternary"
    cases.append(
        (
            bad_union,
            "document.graph.relation_declarations[0].kind has unsupported value 'ternary'",
        )
    )

    bad_union_type = cast(dict[str, JsonValue], json.loads(json.dumps(valid)))
    graph = cast(dict[str, JsonValue], bad_union_type["graph"])
    graph["relation_declarations"] = [None]
    cases.append(
        (
            bad_union_type,
            "document.graph.relation_declarations[0] must be an object",
        )
    )

    for document, expected in cases:
        assert validation_errors(document, FORMAT_VERSION) == [expected]

    with pytest.raises(TypeError, match="requires an object shape"):
        object_fields(STRING)
    with pytest.raises(TypeError, match="requires an object shape"):
        field_shape(STRING, "field")
    with pytest.raises(TypeError, match="requires an array shape"):
        array_item(STRING)
    nullable_document = replace(
        DOCUMENT, fields=(*DOCUMENT.fields, Field("optional", NULLABLE_STRING))
    )
    nullable_schema = json_schema_for(nullable_document, DECLARATIONS, FORMAT_VERSION)
    nullable_properties = cast(dict[str, JsonValue], nullable_schema["properties"])
    assert "minLength" not in json.dumps(nullable_properties["optional"])


def _facet_paths(
    value: object, shape: Shape, path: tuple[str | int, ...] = ()
) -> list[tuple[tuple[str | int, ...], Shape]]:
    """Walk every scalar facet realized by a valid fixture."""
    if shape.kind is ShapeKind.REFERENCE:
        matching = next(
            DECLARATIONS[name]
            for name in shape.variants
            if not validation_errors_for_shape(value, DECLARATIONS[name])
        )
        return _facet_paths(value, matching, path)
    if shape.kind is ShapeKind.OBJECT:
        data = cast(dict[str, object], value)
        return [
            found
            for field in shape.fields
            for found in _facet_paths(
                data[field.name], field.shape, (*path, field.name)
            )
        ]
    if shape.kind is ShapeKind.ARRAY:
        assert shape.item is not None
        return [
            found
            for index, item in enumerate(cast(list[object], value))
            for found in _facet_paths(item, shape.item, (*path, index))
        ]
    return [(path, shape)] if shape.min_length is not None else []


def validation_errors_for_shape(value: object, shape: Shape) -> list[str]:
    """Validate a fixture fragment through a temporary document-shaped wrapper."""
    wrapper = Shape(ShapeKind.OBJECT, fields=(Field("value", shape),))
    schema = json_schema_for(wrapper, DECLARATIONS, FORMAT_VERSION)
    return [
        error.message
        for error in Draft202012Validator(schema).iter_errors({"value": value})
    ]


def _replace_path(document: object, path: tuple[str | int, ...], value: object) -> None:
    """Replace one known fixture leaf without weakening test-side typing."""
    target = document
    for part in path[:-1]:
        target = (
            cast(dict[str, object], target)[part]
            if isinstance(part, str)
            else cast(list[object], target)[part]
        )
    final = path[-1]
    if isinstance(final, str):
        cast(dict[str, object], target)[final] = value
    else:
        cast(list[object], target)[final] = value


def _string_paths(
    value: object, path: tuple[str | int, ...] = ()
) -> list[tuple[str | int, ...]]:
    """Enumerate realized string leaves from data, independently of the schema."""
    if isinstance(value, str):
        return [path]
    if isinstance(value, dict):
        return [
            found
            for key, child in value.items()
            for found in _string_paths(child, (*path, key))
        ]
    if isinstance(value, list):
        return [
            found
            for index, child in enumerate(value)
            for found in _string_paths(child, (*path, index))
        ]
    return []


def test_every_realized_string_leaf_has_matching_empty_string_acceptance() -> None:
    """Schema and codec agree when every realized string is emptied in turn."""
    valid = cast(dict[str, object], json.loads(dumps(rich_graph())))
    paths = _string_paths(valid)
    assert paths
    validator = Draft202012Validator(json_schema(FORMAT_VERSION))
    for path in paths:
        document = cast(dict[str, object], json.loads(json.dumps(valid)))
        _replace_path(document, path, "")
        schema_accepts = validator.is_valid(document)
        try:
            loads(json.dumps(document))
        except ValueError:
            codec_accepts = False
        else:
            codec_accepts = True
        assert schema_accepts == codec_accepts, path


def test_all_declared_nonempty_facets_match_codec_acceptance() -> None:
    """Every realized non-empty string facet rejects through schema and codec."""
    valid = cast(dict[str, object], json.loads(dumps(rich_graph())))
    paths = _facet_paths(valid, DOCUMENT)
    assert paths
    validator = Draft202012Validator(json.loads(SCHEMA_PATH.read_text()))
    for path, _ in paths:
        document = cast(dict[str, object], json.loads(json.dumps(valid)))
        _replace_path(document, path, "")
        assert not validator.is_valid(document)
        with pytest.raises(ValueError):
            loads(json.dumps(document))


def test_published_schema_declares_its_single_scalar_type_divergence() -> None:
    """Only integral-number spelling exceeds JSON Schema's scalar expressiveness."""
    valid = cast(dict[str, object], json.loads(dumps(rich_graph())))
    graph = cast(dict[str, object], valid["graph"])
    relation = cast(list[dict[str, object]], graph["relations"])[0]
    left = cast(dict[str, object], relation["left"])
    left["index"] = float(cast(int, left["index"]))
    schema = cast(dict[str, object], json.loads(SCHEMA_PATH.read_text()))
    assert "1.0" in cast(str, schema["description"])
    assert Draft202012Validator(schema).is_valid(valid)
    with pytest.raises(ValueError, match="index must be an integer"):
        loads(json.dumps(valid))


def test_reference_diagnostics_choose_the_best_matching_variant() -> None:
    """Union errors follow the discriminator and deepest matching structure."""
    document = cast(dict[str, object], json.loads(dumps(rich_graph())))
    graph = cast(dict[str, object], document["graph"])
    tier = cast(list[dict[str, object]], graph["tiers"])[0]
    item = cast(list[dict[str, object]], tier["items"])[0]
    attribute = cast(list[dict[str, object]], item["attributes"])[0]
    attribute.update(value_type="integer", lexical="1.0")
    assert validation_errors(document, FORMAT_VERSION) == [
        "document.graph.tiers[0].items[0].attributes[0].lexical has an invalid lexical form"
    ]

    document = cast(dict[str, object], json.loads(dumps(rich_graph())))
    graph = cast(dict[str, object], document["graph"])
    position = cast(list[dict[str, object]], graph["position_values"])[0]
    reference = cast(dict[str, object], position["reference"])
    anchor = cast(dict[str, object], reference["anchor"])
    anchor["durable_id"] = ""
    assert validation_errors(document, FORMAT_VERSION) == [
        "document.graph.position_values[0].reference.anchor.durable_id must not be empty"
    ]


def test_nested_declaration_change_moves_validator_parser_and_schema() -> None:
    """One nested declaration edit reaches all three declaration consumers."""
    original = TIER.fields
    object.__setattr__(TIER, "fields", (*original, Field("extension", STRING)))
    try:
        document = to_data(rich_graph())
        assert validation_errors(document, FORMAT_VERSION) == [
            "document.graph.tiers[0] is missing field 'extension'"
        ]
        tier_schema = cast(
            dict[str, JsonValue],
            cast(dict[str, JsonValue], json_schema(FORMAT_VERSION)["properties"])[
                "graph"
            ],
        )
        assert "extension" in json.dumps(tier_schema)
        with pytest.raises(
            ValueError, match="tiers\\[0\\] is missing field 'extension'"
        ):
            loads(json.dumps(document))
    finally:
        object.__setattr__(TIER, "fields", original)
