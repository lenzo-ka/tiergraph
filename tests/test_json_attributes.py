"""Structured native attributes retain literal JSON and scalar distinctions."""

from __future__ import annotations

import json
import math
from dataclasses import replace
from typing import cast

import pytest
from examples.mix_paths import _document_int

import tiergraph_dot
from tests import test_wire as fixtures
from tiergraph import (
    AttachValue,
    AttributeDeclaration,
    AttributeDomain,
    BoundaryRef,
    DeclareAttribute,
    DeclareNamespace,
    Delivery,
    Graph,
    GraphValidationError,
    Item,
    ItemRef,
    JsonAttributeValue,
    JsonType,
    Layer,
    LayerFact,
    LayerRead,
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


def test_example_scalar_reader_refuses_json() -> None:
    """The example's actual reading path refuses JSON rather than inventing lexical data."""
    with pytest.raises(GraphValidationError, match="scalar XSD"):
        _document_int(document(1), NAME)


def test_every_carrier_layers_edit_transport_and_dot() -> None:
    """Every declared carrier and layer preserves native structured values."""
    base = fixtures.graph_with_layers()
    declarations = tuple(
        replace(declaration, value_type=JsonType.JSON)
        for declaration in base.attribute_declarations
    )
    old = fixtures.six_domain_layer()
    layer = Layer(
        old.name,
        tuple(
            LayerFact(
                fact.subject,
                JsonAttributeValue(fact.value.name, {"value": None, "array": []}),
            )
            for fact in old.facts
        ),
    )
    graph = replace(base, attribute_declarations=declarations, layers=(layer,))
    assert wire.loads(wire.dump_compact(graph)) == graph
    flat = graph.flatten(Delivery((old.name,), LayerRead.ALL))
    assert wire.loads(wire.dump_compact(flat)) == flat
    targets = {
        AttributeDomain.DOCUMENT: None,
        AttributeDomain.TIER: fixtures.WORDS,
        AttributeDomain.ITEM: ItemRef(fixtures.WORDS, 0),
        AttributeDomain.BOUNDARY: BoundaryRef(fixtures.WORDS, 1),
        AttributeDomain.RELATION_DECLARATION: fixtures.LINKS,
        AttributeDomain.RELATION_INSTANCE: 0,
    }
    for declaration in declarations:
        updated = flat.set_attribute(
            targets[declaration.domain],
            JsonAttributeValue(declaration.name, {"signed": -0.0}),
        )
        assert wire.loads(wire.dump_compact(updated)) == updated
    edited = flat.insert_item(fixtures.WORDS, 0, Item("new"))
    assert wire.loads(wire.dump_compact(edited)) == edited
    assert '\\"array\\":[]' in tiergraph_dot.dumps(flat)


def test_json_schema_value_refusal_and_duplicate_keys() -> None:
    """Python-data validation and parser duplicate-key limits remain active."""
    data = json.loads(wire.dump_compact(document(None)))
    data["graph"]["attributes"][0]["value"] = float("inf")
    assert validation_errors(data, wire.FORMAT_VERSION)
    with pytest.raises(ValueError):
        wire.loads(json.dumps(data))
    raw = wire.dump_compact(document({"a": 1})).replace('"a":1', '"a":1,"a":2')
    with pytest.raises(ValueError, match="duplicate"):
        wire.loads(raw)
