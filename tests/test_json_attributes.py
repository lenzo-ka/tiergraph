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
    AttributeValuation,
    AttributeValue,
    BoundaryRef,
    DeclareAttribute,
    DeclareNamespace,
    Delivery,
    GrammarRule,
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
    Refusal,
    RefusalStage,
    RewriteDeclaration,
    RewriteEffect,
    XsdType,
    execute,
    program_dumps,
    program_loads,
    wire,
)
from tiergraph.build import Document, item
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


def test_scalar_constructor_refuses_json_type() -> None:
    """A widened declaration type cannot manufacture a JSON lexical carrier."""
    with pytest.raises(GraphValidationError, match="scalar XSD type"):
        AttributeValue(NAME, cast(XsdType, JsonType.JSON), "1")


def test_grammar_weight_refuses_json_carrier() -> None:
    """A wrong-family weight is refused before diagnostic lexical access."""
    with pytest.raises(GraphValidationError, match="scalar XSD value"):
        GrammarRule(
            NAME, (), (), weight=cast(AttributeValue, JsonAttributeValue(NAME, 1))
        )


def test_builder_json_snapshot_and_fold_scalar_refusal() -> None:
    """Native builder lowering snapshots JSON and fold refuses its declared family."""
    builder = Document(NAME.namespace, prefix="j")
    builder.attribute(NAME, JsonType.JSON)
    payload: dict[str, JsonValue] = {"array": [1]}
    tier = builder.tier("values", (item(attrs={NAME: payload}),))
    graph = builder.build()
    payload["array"] = [2]
    value = graph.tiers[0].items[0].attributes[0]
    assert isinstance(value, JsonAttributeValue)
    assert value.to_value() == {"array": [1]}
    assert wire.loads(wire.dump_compact(graph)) == graph
    with pytest.raises(ValueError, match="scalar XSD"):
        AttributeValuation("payload", NAME, (tier.name,)).declaration_type(graph)


def test_rewrite_preserves_json_signed_zero_disturbance() -> None:
    """Actual generic fact diagnostics distinguish JSON double signs."""
    disturbances = RewriteDeclaration(
        "signed", document(-0.0), document(0.0)
    ).disturbances()
    assert len(disturbances) == 1
    assert disturbances[0].effect is RewriteEffect.REVISE


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


#: The largest integer a JSON peer decodes exactly. Spelled here rather
#: than imported, so a change to the library's bound fails this test.
SAFE = 2**53 - 1


@pytest.mark.parametrize("value", [SAFE, -SAFE, 0, 1, -1])
def test_an_integer_a_json_peer_holds_exactly_is_admitted(value: int) -> None:
    """The budget admits every integer a double represents exactly."""
    assert JsonAttributeValue(NAME, value).to_value() == value


@pytest.mark.parametrize(
    "value",
    [SAFE + 1, -SAFE - 1, 2**64, 10**5000],
    # pytest names a case by str() of its parameter, and str(10**5000)
    # is itself past the digit limit this test is about.
    ids=["2**53", "-2**53", "2**64", "10**5000"],
)
def test_an_integer_a_json_peer_would_corrupt_is_refused(value: int) -> None:
    """Refused where it is built, as a declared refusal.

    2**53 is the first integer a double cannot tell apart from its neighbour,
    so a peer would read it back as a different number with no error.
    10**5000 is also past the process's integer-to-text limit; it is refused
    by comparison, before anything tries to spell it, so the refusal is
    this one rather than a bare ValueError from a writer.
    """
    with pytest.raises(GraphValidationError, match="2\\*\\*53"):
        JsonAttributeValue(NAME, value)


def test_the_largest_admitted_integer_crosses_the_wire_unchanged() -> None:
    graph = document(SAFE)
    back = wire.loads(wire.dump_compact(graph)).attributes[0]
    assert isinstance(back, JsonAttributeValue)
    assert back.to_value() == SAFE


@pytest.mark.parametrize(
    "literal",
    [
        # past the budget, but short enough to convert: refused by value
        "9" * 20,
        # past the process's conversion limit: refused before converting
        "9" * 5000,
    ],
    ids=["20-digits", "5000-digits"],
)
def test_the_reader_refuses_an_out_of_budget_integer_as_staged(literal: str) -> None:
    """A document written elsewhere is held to the same budget, and says so.

    Before the budget, the 20-digit literal loaded silently -- a number a
    JSON peer would already have corrupted -- and the 5000-digit literal
    escaped as a bare ValueError rather than a staged refusal.
    """
    text = wire.dump_compact(document(7)).replace('"value":7', '"value":' + literal)
    with pytest.raises(Refusal) as refused:
        wire.loads(text)
    assert refused.value.stage is RefusalStage.VALUE
