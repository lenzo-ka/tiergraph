"""Structured native attributes retain literal JSON and scalar distinctions."""

from __future__ import annotations

import json
import math
from dataclasses import replace
from typing import cast

import pytest

from tiergraph import (
    AttachValue,
    AttributeDeclaration,
    AttributeDomain,
    DeclareAttribute,
    DeclareNamespace,
    Graph,
    GraphValidationError,
    JsonAttributeValue,
    JsonType,
    NamespaceDeclaration,
    Program,
    QualifiedName,
    execute,
    program_dumps,
    program_loads,
    wire,
)
from tiergraph.core import JsonValue, _scalar_attribute
from tiergraph.schema import validation_errors

NAME = QualifiedName("urn:json-attribute-test", "payload")


def document(value: JsonValue) -> Graph:
    """Construct a minimal declared document-valued concern."""
    return Graph(
        namespaces=(NamespaceDeclaration("j", NAME.namespace),),
        tiers=(),
        relation_declarations=(),
        attribute_declarations=(
            AttributeDeclaration(NAME, AttributeDomain.DOCUMENT, JsonType.JSON),
        ),
        attributes=(JsonAttributeValue(NAME, value),),
    )


@pytest.mark.parametrize(
    "value",
    [
        None,
        False,
        0,
        0.0,
        -0.0,
        1,
        1.0,
        "",
        [],
        {},
        {"namespace": "unbound", "local_name": "x"},
        {"empty": [], "null": None, "雪": "好"},
    ],
)
def test_native_literal_roundtrip(value: JsonValue) -> None:
    """Native wire retains QName-shaped user data, nulls and empty containers."""
    graph = document(value)
    data = wire.to_data(graph)
    assert validation_errors(data, wire.FORMAT_VERSION) == []
    loaded = wire.loads(wire.dump_compact(graph))
    assert loaded == graph
    assert wire.to_data(loaded) == data
    assert (
        json.loads(wire.dump_compact(graph))["graph"]["attributes"][0]["value"] == value
    )


def test_equality_hash_and_signed_zero() -> None:
    """Kinds and float signs are part of immutable native identity."""
    values: list[JsonValue] = [False, 0, 0.0, -0.0, None, [], {}, 1, 1.0]
    attributes = [JsonAttributeValue(NAME, value) for value in values]
    assert len(set(attributes)) == len(values)
    negative = wire.loads(wire.dump_compact(document(-0.0))).attributes[0]
    assert isinstance(negative, JsonAttributeValue)
    assert math.copysign(1.0, cast(float, negative.to_value())) == -1.0
    assert '"value":-0.0' in wire.dump_compact(document(-0.0))


def test_caller_mutation_and_object_order() -> None:
    """Both input and returned containers are owned by their caller."""
    original: dict[str, JsonValue] = {"z": [1], "a": None}
    attribute = JsonAttributeValue(NAME, original)
    original["z"] = [2]
    returned = cast(dict[str, JsonValue], attribute.to_value())
    returned["z"] = []
    assert attribute.to_value() == {"a": None, "z": [1]}
    assert attribute == JsonAttributeValue(NAME, {"a": None, "z": [1]})


@pytest.mark.parametrize(
    "value",
    [float("inf"), float("nan"), object(), {1: "x"}, (1,), "\ud800", {"\ud800": 1}],
)
def test_non_json_values_are_refused(value: object) -> None:
    """The kernel has no opaque or escaped-string recovery path."""
    with pytest.raises(GraphValidationError):
        JsonAttributeValue(NAME, cast(JsonValue, value))


def test_cycle_refusal_and_shared_acyclic_container() -> None:
    """Sharing is copied, while recursion is refused before graph exposure."""
    array: list[JsonValue] = []
    array.append(array)
    with pytest.raises(GraphValidationError, match="acyclic"):
        JsonAttributeValue(NAME, array)
    shared: list[JsonValue] = [1]
    assert JsonAttributeValue(NAME, [shared, shared]).to_value() == [[1], [1]]


def test_editor_and_machine_roundtrip() -> None:
    """The native editor and opcode wire preserve structured concerns."""
    graph = document({"a": []})
    changed = graph.set_attribute(None, JsonAttributeValue(NAME, {"a": False}))
    assert changed == document({"a": False})
    program = Program(
        (
            DeclareNamespace(graph.namespaces[0]),
            DeclareAttribute(graph.attribute_declarations[0]),
            AttachValue(AttributeDomain.DOCUMENT, None, graph.attributes[0]),
        )
    )
    assert execute(program.opcodes) == graph
    assert execute(program_loads(program_dumps(program)).opcodes) == graph


@pytest.mark.parametrize("mutation", ["lexical", "missing", "extra", "scalar-type"])
def test_closed_wire_variants(mutation: str) -> None:
    """A JSON literal has one required value and no scalar lexical member."""
    data = json.loads(wire.dump_compact(document(None)))
    value = data["graph"]["attributes"][0]
    if mutation == "lexical":
        value["lexical"] = "null"
    elif mutation == "missing":
        del value["value"]
    elif mutation == "extra":
        value["extra"] = 1
    else:
        value["value_type"] = "string"
    with pytest.raises(ValueError):
        wire.loads(json.dumps(data))


def test_wrong_domain_and_scalar_boundary_refusal() -> None:
    """Declared domains and scalar profile gates remain explicit."""
    graph = document(None)
    with pytest.raises(GraphValidationError):
        replace(
            graph,
            attribute_declarations=(
                AttributeDeclaration(NAME, AttributeDomain.ITEM, JsonType.JSON),
            ),
        )
    with pytest.raises(GraphValidationError, match="scalar"):
        _scalar_attribute(graph.attributes[0])
