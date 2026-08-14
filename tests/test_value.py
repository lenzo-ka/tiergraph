"""Structured JSON values use ordinary, explicitly ordered graph structure."""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

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

    bad_bound = to_data(graph)
    bound_graph = bad_bound["graph"]
    assert isinstance(bound_graph, dict)
    declarations = bound_graph["relation_declarations"]
    assert isinstance(declarations, list)
    declaration = declarations[0]
    assert isinstance(declaration, dict)
    targets = declaration["targets"]
    assert isinstance(targets, dict)
    targets["maximum"] = -2
    assert validation_errors(bad_bound, FORMAT_VERSION) == [
        "document.graph.relation_declarations[0].targets.maximum must be at least -1"
    ]
    with pytest.raises(ValueError, match=r"maximum -2 must be a nonnegative integer"):
        loads(json.dumps(bad_bound))


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
    changed = replace(
        graph, tiers=(Tier(tier.declaration, (changed_item,)), *graph.tiers[1:])
    )
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
    with pytest.raises(ValueError, match="membership tier .* is not declared"):
        replace(profile, occurrence_tier=absent)
    with pytest.raises(ValueError, match="membership-value relation must have"):
        replace(profile, value_relation=absent)


def test_profile_refuses_noncanonical_membership_shapes() -> None:
    """Every occurrence has one owner edge and exactly one value edge."""
    graph, profile, root = json_value_graph(["kept"])
    member = next(
        item
        for item in graph.polyadic_relations
        if item.declaration == profile.member_relation
    )
    value = next(
        item
        for item in graph.polyadic_relations
        if item.declaration == profile.value_relation
    )

    def refused(relations: tuple[PolyadicRelationInstance, ...], message: str) -> None:
        changed = replace(graph)
        object.__setattr__(changed, "polyadic_relations", relations)
        with pytest.raises(ValueError, match=message):
            rebound(changed, profile)

    refused((replace(member, sources=()), value), "noncanonical source arity")
    refused((member, member, value), "multiple member relations")
    refused((member, replace(value, targets=())), "noncanonical arity")
    refused((member, value, value), "has multiple values")

    second_owner = replace(member, sources=(value.targets[0],))
    refused((member, second_owner, value), "owned by multiple containers")

    missing_value = replace(graph, polyadic_relations=(member,))
    with pytest.raises(ValueError, match="membership .* has no value"):
        rebound(missing_value, profile)

    orphan_value = replace(graph, polyadic_relations=(value,))
    with pytest.raises(ValueError, match="membership .* has no container"):
        rebound(orphan_value, profile)

    graph_two, profile_two, _ = json_value_graph(["x", "y"])
    value_relations = [
        item
        for item in graph_two.polyadic_relations
        if item.declaration == profile_two.value_relation
    ]
    aliased_value = replace(value_relations[1], targets=value_relations[0].targets)
    changed_two = replace(graph_two)
    object.__setattr__(
        changed_two,
        "polyadic_relations",
        tuple(
            aliased_value if item is value_relations[1] else item
            for item in graph_two.polyadic_relations
        ),
    )
    with pytest.raises(ValueError, match="alias a value target"):
        rebound(changed_two, profile_two)


def test_root_and_recursive_member_refusals_name_coordinates() -> None:
    """Wrong-tier, absent, and cyclic roots are distinct malformed neighbours."""
    graph, profile, root = json_value_graph([])
    other = QualifiedName(root.tier.namespace, "other")
    with pytest.raises(ValueError, match="is not on the node tier"):
        profile.value(ItemRef(other, 0))
    with pytest.raises(ValueError, match="does not exist"):
        profile.value(ItemRef(root.tier, 9))
    # Give the empty array a membership whose value is the array itself.
    occurrence = ItemRef(profile.occurrence_tier, 0)
    occurrence_tier = graph.tiers[1]
    recursive = replace(
        graph,
        tiers=(graph.tiers[0], replace(occurrence_tier, items=(Item(),))),
        polyadic_relations=(
            PolyadicRelationInstance(profile.member_relation, (root,), (occurrence,)),
            PolyadicRelationInstance(profile.value_relation, (occurrence,), (root,)),
        ),
    )
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
            *graph.tiers[1:],
        ),
    )
    with pytest.raises(ValueError, match="unsupported kind 'record'"):
        rebound(changed, profile).value(root)
    missing_payload = replace(
        graph,
        tiers=(
            replace(tier, items=(replace(item, attributes=(kind,)),)),
            *graph.tiers[1:],
        ),
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
    occurrence_tier = graph.tiers[1]
    occurrence = occurrence_tier.items[0]
    keyed = replace(
        occurrence,
        attributes=(
            *occurrence.attributes,
            AttributeValue(profile.key_attribute, XsdType.STRING, "wrong"),
        ),
    )
    changed = replace(
        graph, tiers=(graph.tiers[0], replace(occurrence_tier, items=(keyed,)))
    )
    with pytest.raises(ValueError, match="array membership .* has an object key"):
        rebound(changed, profile).value(root)

    scalar_graph, scalar_profile, scalar_root = json_value_graph("root")
    scalar_tier = scalar_graph.tiers[0]
    scalar = scalar_tier.items[0]
    keyed_scalar = replace(
        scalar,
        attributes=(
            *scalar.attributes,
            AttributeValue(scalar_profile.key_attribute, XsdType.STRING, "orphan"),
        ),
    )
    keyed_scalar_graph = replace(
        scalar_graph,
        tiers=(replace(scalar_tier, items=(keyed_scalar,)), *scalar_graph.tiers[1:]),
    )
    with pytest.raises(ValueError, match="value node .* carries an object-member key"):
        rebound(keyed_scalar_graph, scalar_profile).value(scalar_root)

    object_graph, object_profile, object_root = json_value_graph({"b": 2, "a": 1})
    relation = next(
        item
        for item in object_graph.polyadic_relations
        if item.declaration == object_profile.member_relation
    )
    reversed_graph = replace(
        object_graph,
        polyadic_relations=tuple(
            replace(item, targets=tuple(reversed(item.targets)))
            if item is relation
            else item
            for item in object_graph.polyadic_relations
        ),
    )
    with pytest.raises(ValueError, match="has noncanonical keys"):
        rebound(reversed_graph, object_profile).value(object_root)
    object_tier = object_graph.tiers[1]
    first = object_tier.items[0]
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
            object_graph.tiers[0],
            replace(
                object_tier,
                items=(without_key, *object_tier.items[1:]),
            ),
        ),
    )
    with pytest.raises(ValueError, match="object membership .* has no key"):
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
            *double_graph.tiers[1:],
        ),
    )
    with pytest.raises(ValueError, match="JSON double node .* is not finite"):
        rebound(infinite_graph, double_profile).value(double_root)


def test_frozen_json_carriers_are_accepted() -> None:
    """Immutable array and object carriers are the ordinary frozen input shape."""
    tuple_graph, tuple_profile, tuple_root = json_value_graph(("value",))  # type: ignore[arg-type]
    assert tuple_profile.value(tuple_root) == ["value"]
    mapping_graph, mapping_profile, mapping_root = json_value_graph(
        MappingProxyType({"a": 1})  # type: ignore[arg-type]
    )
    assert mapping_profile.value(mapping_root) == {"a": 1}
    with pytest.raises(ValueError, match="unsupported type set"):
        json_value_graph({"not-json"})  # type: ignore[arg-type]


def test_aliased_membership_occurrence_is_refused_and_neighbour_passes() -> None:
    """One occurrence cannot stand for two written JSON array positions."""
    graph, profile, root = json_value_graph(("x", "y"))  # type: ignore[arg-type]
    assert profile.value(root) == ["x", "y"]
    relation = next(
        item
        for item in graph.polyadic_relations
        if item.declaration == profile.member_relation
    )
    aliased = replace(relation, targets=(relation.targets[0], relation.targets[0]))
    # Exercise profile validation independently of Graph's matching kernel promise.
    object.__setattr__(
        graph,
        "polyadic_relations",
        tuple(
            aliased if item is relation else item for item in graph.polyadic_relations
        ),
    )
    with pytest.raises(ValueError, match="aliases a membership target"):
        rebound(graph, profile)


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
