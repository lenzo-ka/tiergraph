"""The reference declaration satisfies the reusable generated-schema laws."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from scripts.generate_schema import generated_bytes, refuse_unversioned_shape_change

from tests.conformance.schema import SchemaLawSuite
from tests.test_wire import rich_graph
from tiergraph.core import JsonValue
from tiergraph.schema import (
    DECLARATIONS,
    DOCUMENT,
    STRING,
    Field,
    json_schema,
    json_schema_for,
    object_fields,
    validation_errors,
)
from tiergraph.wire import FORMAT_VERSION, to_data

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


def test_shape_change_requires_format_version_change() -> None:
    """The prior stamp refuses a changed declaration under the same version."""
    prior = cast(dict[str, object], json.loads(STAMP_PATH.read_text()))
    altered = {**prior, "shape_sha256": "edited"}
    with pytest.raises(ValueError, match="shape changed without moving FORMAT_VERSION"):
        refuse_unversioned_shape_change(altered)
    altered["format_version"] = str(int(FORMAT_VERSION) - 1)
    refuse_unversioned_shape_change(altered)


def test_validation_rejects_each_declared_json_construction() -> None:
    """Near-valid edits exercise the validator produced from every shape kind."""
    valid = to_data(rich_graph())
    assert validation_errors(valid, "2") == [
        "format_version '1' is unsupported; expected '2'"
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
            "document.graph.relation_declarations[0] is missing field 'item_type'",
        )
    )

    for document, expected in cases:
        assert validation_errors(document, FORMAT_VERSION) == [expected]

    with pytest.raises(TypeError, match="requires an object shape"):
        object_fields(STRING)
