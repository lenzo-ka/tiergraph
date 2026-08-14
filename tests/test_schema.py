"""The reference declaration satisfies the reusable generated-schema laws."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from scripts.generate_schema import (
    generated_bytes,
    refuse_unversioned_shape_change,
    stamp_bytes,
)
from scripts.generate_schema import (
    main as generate_main,
)

from tests.conformance.schema import SchemaLawSuite
from tests.test_wire import rich_graph
from tiergraph.core import JsonValue
from tiergraph.schema import (
    DECLARATIONS,
    DOCUMENT,
    NULLABLE_STRING,
    STRING,
    TIER,
    Field,
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


def test_shape_change_requires_format_version_change() -> None:
    """The prior stamp refuses a changed declaration under the same version."""
    prior = cast(dict[str, object], json.loads(STAMP_PATH.read_text()))
    altered = {**prior, "shape_sha256": "edited"}
    with pytest.raises(ValueError, match="shape changed without moving FORMAT_VERSION"):
        refuse_unversioned_shape_change(prior, altered)
    altered["format_version"] = str(int(FORMAT_VERSION) - 1)
    refuse_unversioned_shape_change(prior, altered)


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
            SystemExit, match="shape changed without moving FORMAT_VERSION"
        ):
            generate_main(["--check"], baseline)
    finally:
        object.__setattr__(TIER, "fields", original)


def test_validation_rejects_each_declared_json_construction() -> None:
    """Near-valid edits exercise the validator produced from every shape kind."""
    valid = to_data(rich_graph())
    assert validation_errors(valid, "3") == [
        "format_version '2' is unsupported; expected '3'"
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


def test_declared_scalar_facets_match_codec_acceptance() -> None:
    """Lexicals, durable ids, and coordinates rejected by the codec fail the schema."""

    def parts(
        document: dict[str, object],
    ) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        graph = cast(dict[str, object], document["graph"])
        tier = cast(list[dict[str, object]], graph["tiers"])[0]
        item = cast(list[dict[str, object]], tier["items"])[0]
        value = cast(list[dict[str, object]], item["attributes"])[0]
        relation = cast(list[dict[str, object]], graph["relations"])[0]
        endpoint = cast(dict[str, object], relation["left"])
        return item, value, endpoint

    def bad_lexical(document: dict[str, object]) -> None:
        _, value, _ = parts(document)
        value.update(value_type="integer", lexical="1.0")

    def empty_durable_id(document: dict[str, object]) -> None:
        item, _, _ = parts(document)
        item["durable_id"] = ""

    def negative_index(document: dict[str, object]) -> None:
        _, _, endpoint = parts(document)
        endpoint["index"] = -1

    def empty_anchor_id(document: dict[str, object]) -> None:
        graph = cast(dict[str, object], document["graph"])
        positions = cast(list[dict[str, object]], graph["position_values"])
        reference = cast(dict[str, object], positions[0]["reference"])
        anchor = cast(dict[str, object], reference["anchor"])
        anchor["durable_id"] = ""

    edits = (bad_lexical, empty_durable_id, negative_index, empty_anchor_id)
    for edit in edits:
        document = cast(dict[str, object], json.loads(dumps(rich_graph())))
        edit(document)
        assert validation_errors(document, FORMAT_VERSION)
        with pytest.raises(ValueError):
            loads(json.dumps(document))


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
