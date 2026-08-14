"""Structured JSON values use ordinary, explicitly ordered graph structure."""

from __future__ import annotations

from dataclasses import replace

import pytest

from tiergraph import (
    AttributeValue,
    Graph,
    Item,
    ItemRef,
    JsonValueProfile,
    PolyadicRelationInstance,
    QualifiedName,
    Tier,
    XsdType,
    dump_bytes,
    json_value_graph,
    loads,
)
from tiergraph.core import JsonValue


def segment_value() -> JsonValue:
    """Return a representative recursively frozen ipakit feature value."""
    return {
        "features": {
            "consonantal": True,
            "place": ["labial", "velar"],
            "voice": None,
        },
        "symbol": "k͡p",
        "weight": 1.25,
    }


def rebound(graph: Graph, profile: JsonValueProfile) -> JsonValueProfile:
    """Bind the same declared roles to a wire-decoded graph."""
    return replace(profile, graph=graph)


def test_ipakit_feature_value_round_trips_with_identical_bytes() -> None:
    """A nested feature value survives construction and the canonical wire."""
    expected = segment_value()
    graph, profile, root = json_value_graph(expected)
    encoded = dump_bytes(graph)
    decoded_graph = loads(encoded)
    decoded = rebound(decoded_graph, profile)
    assert decoded.value(root) == expected
    assert dump_bytes(decoded_graph) == encoded


def test_order_is_declared_by_member_targets() -> None:
    """Array order remains visible in relation targets rather than tier storage."""
    graph, profile, root = json_value_graph(["first", "second"])
    relation = next(
        item for item in graph.polyadic_relations if item.sources == (root,)
    )
    reversed_relation = replace(relation, targets=tuple(reversed(relation.targets)))
    changed = replace(
        graph,
        polyadic_relations=tuple(
            reversed_relation if item is relation else item
            for item in graph.polyadic_relations
        ),
    )
    assert rebound(changed, profile).value(root) == ["second", "first"]


def test_malformed_value_names_offender_and_neighbour_passes() -> None:
    """A scalar with children is refused while its well-formed neighbour reads."""
    graph, profile, root = json_value_graph(["kept"])
    assert profile.value(root) == ["kept"]
    child = ItemRef(root.tier, 1)
    relation = next(
        item for item in graph.polyadic_relations if item.sources == (root,)
    )
    malformed = replace(relation, sources=(child,))
    changed = replace(
        graph,
        polyadic_relations=tuple(
            malformed if item is relation else item for item in graph.polyadic_relations
        ),
    )
    with pytest.raises(
        ValueError, match=r"JSON scalar node .*index': 1.*member relation"
    ):
        rebound(changed, profile).value(child)


def test_codec_and_schema_accept_and_refuse_same_new_surface() -> None:
    """Generated-schema validation and the codec agree around structured graphs."""
    from tiergraph.schema import validation_errors
    from tiergraph.wire import FORMAT_VERSION, to_data

    graph, _, _ = json_value_graph({"place": "velar"})
    document = to_data(graph)
    assert validation_errors(document, FORMAT_VERSION) == []
    assert loads(dump_bytes(graph)) == graph
    bad = to_data(graph)
    graph_data = bad["graph"]
    assert isinstance(graph_data, dict)
    relations = graph_data["relations"]
    assert isinstance(relations, list)
    relation = relations[0]
    assert isinstance(relation, dict)
    relation["targets"] = "not-an-array"
    schema_errors = validation_errors(bad, FORMAT_VERSION)
    assert schema_errors == ["document.graph.relations[0].targets must be an array"]
    import json

    with pytest.raises(ValueError, match=r"relations\[0\].targets must be an array"):
        loads(json.dumps(bad))


def test_nonfinite_double_and_non_string_key_name_the_offender() -> None:
    """Near-valid JSON neighbours distinguish supported recursive values."""
    graph, profile, root = json_value_graph({"finite": 1.0})
    assert profile.value(root) == {"finite": 1.0}
    with pytest.raises(ValueError, match="JSON value double inf is not finite"):
        json_value_graph(float("inf"))
    with pytest.raises(ValueError, match="JSON object key must be a string"):
        json_value_graph({1: "value"})  # type: ignore[dict-item]


def test_missing_kind_names_the_node() -> None:
    """Profile construction succeeds before a malformed node is read loudly."""
    graph, profile, root = json_value_graph("value")
    tier = graph.tiers[0]
    item = tier.items[0]
    changed_item = Item(item.durable_id, item.attributes[1:])
    changed = replace(graph, tiers=(Tier(tier.declaration, (changed_item,)),))
    with pytest.raises(ValueError, match=r"JSON value node .*unsupported kind None"):
        rebound(changed, profile).value(root)


def test_profile_role_declarations_name_missing_or_wrong_roles() -> None:
    """Tier, relation, and scalar roles are checked before interpretation."""
    graph, profile, _ = json_value_graph(None)
    absent = QualifiedName(profile.node_tier.namespace, "absent")
    with pytest.raises(ValueError, match="node tier .* is not declared"):
        replace(profile, node_tier=absent)
    with pytest.raises(ValueError, match="JSON member relation must have"):
        replace(profile, member_relation=absent)
    with pytest.raises(ValueError, match="JSON kind role .* must be an item string"):
        replace(profile, kind_attribute=absent)


def test_root_and_recursive_member_refusals_name_coordinates() -> None:
    """Wrong-tier, absent, and cyclic roots are distinct malformed neighbours."""
    graph, profile, root = json_value_graph([])
    other = QualifiedName(root.tier.namespace, "other")
    with pytest.raises(ValueError, match="is not on the node tier"):
        profile.value(ItemRef(other, 0))
    with pytest.raises(ValueError, match="does not exist"):
        profile.value(ItemRef(root.tier, 9))
    relation = graph.polyadic_relations[0]
    recursive = replace(graph, polyadic_relations=(replace(relation, targets=(root,)),))
    with pytest.raises(ValueError, match="is recursive"):
        rebound(recursive, profile).value(root)


def test_payload_and_container_shape_refusals_name_nodes() -> None:
    """Kinds, payloads, and member relations cannot contradict one another."""
    graph, profile, root = json_value_graph("kept")
    tier = graph.tiers[0]
    item = tier.items[0]
    kind = item.attributes[0]
    bad_kind = replace(kind, lexical="record")
    changed = replace(
        graph,
        tiers=(
            replace(
                tier,
                items=(replace(item, attributes=(bad_kind, *item.attributes[1:])),),
            ),
        ),
    )
    with pytest.raises(ValueError, match="unsupported kind 'record'"):
        rebound(changed, profile).value(root)
    missing_payload = replace(
        graph, tiers=(replace(tier, items=(replace(item, attributes=(kind,)),)),)
    )
    with pytest.raises(ValueError, match="kind 'string' requires"):
        rebound(missing_payload, profile).value(root)

    container, container_profile, container_root = json_value_graph([])
    no_members = replace(container, polyadic_relations=())
    with pytest.raises(ValueError, match="container node .* has no member relation"):
        rebound(no_members, container_profile).value(container_root)


def test_member_key_rules_and_numeric_leaves() -> None:
    """Array/object key discipline and both numeric scalar branches are explicit."""
    graph, profile, root = json_value_graph([7])
    assert profile.value(root) == [7]
    tier = graph.tiers[0]
    child = tier.items[1]
    keyed = replace(
        child,
        attributes=(
            *child.attributes,
            AttributeValue(profile.key_attribute, XsdType.STRING, "wrong"),
        ),
    )
    changed = replace(graph, tiers=(replace(tier, items=(tier.items[0], keyed)),))
    with pytest.raises(ValueError, match="array member .* has an object key"):
        rebound(changed, profile).value(root)

    object_graph, object_profile, object_root = json_value_graph({"b": 2, "a": 1})
    relation = object_graph.polyadic_relations[-1]
    reversed_graph = replace(
        object_graph,
        polyadic_relations=(
            *object_graph.polyadic_relations[:-1],
            replace(relation, targets=tuple(reversed(relation.targets))),
        ),
    )
    with pytest.raises(ValueError, match="has noncanonical keys"):
        rebound(reversed_graph, object_profile).value(object_root)
    object_tier = object_graph.tiers[0]
    first = object_tier.items[1]
    without_key = replace(
        first,
        attributes=tuple(
            value
            for value in first.attributes
            if value.name != object_profile.key_attribute
        ),
    )
    missing_key = replace(
        object_graph,
        tiers=(
            replace(
                object_tier,
                items=(object_tier.items[0], without_key, *object_tier.items[2:]),
            ),
        ),
    )
    with pytest.raises(ValueError, match="object member .* has no key"):
        rebound(missing_key, object_profile).value(object_root)

    double_graph, double_profile, double_root = json_value_graph(1.0)
    double_tier = double_graph.tiers[0]
    double_item = double_tier.items[0]
    infinite = replace(double_item.attributes[1], lexical="INF")
    infinite_graph = replace(
        double_graph,
        tiers=(
            replace(
                double_tier,
                items=(
                    replace(
                        double_item, attributes=(double_item.attributes[0], infinite)
                    ),
                ),
            ),
        ),
    )
    with pytest.raises(ValueError, match="JSON double node .* is not finite"):
        rebound(infinite_graph, double_profile).value(double_root)


def test_unsupported_python_value_names_type() -> None:
    """A tuple is near recursive JSON but is not silently treated as an array."""
    with pytest.raises(ValueError, match="unsupported type tuple"):
        json_value_graph(("value",))  # type: ignore[arg-type]


def test_profile_ignores_other_relations() -> None:
    """Only the relation assigned the member role contributes children."""
    graph, profile, root = json_value_graph(None)
    member_declaration = graph.relation_declarations[0]
    other_name = QualifiedName(root.tier.namespace, "other-members")
    other_declaration = replace(member_declaration, name=other_name)
    other_relation = PolyadicRelationInstance(other_name, (root,), ())
    extended = replace(
        graph,
        relation_declarations=(*graph.relation_declarations, other_declaration),
        polyadic_relations=(other_relation,),
    )
    assert rebound(extended, profile).value(root) is None
