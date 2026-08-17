"""Contract and discrimination tests for role-neutral polyadic traversal."""

from __future__ import annotations

import json
from typing import Any, cast

import pytest

from tiergraph import (
    BoundarySide,
    DurableItemRef,
    DurablePositionRef,
    Graph,
    Item,
    ItemRef,
    NamespaceDeclaration,
    Node,
    NodeKind,
    OrderedPolyadicTraversal,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    PolyadicSide,
    PositionRef,
    QualifiedName,
    RelationEndpointKind,
    RelationSideDeclaration,
    Tier,
    TierDeclaration,
)

NS = "urn:test:ordered-polyadic"
NODES = QualifiedName(NS, "nodes")
LINKS = QualifiedName(NS, "links")


def ref(index: int) -> ItemRef:
    return ItemRef(NODES, index)


def node(index: int) -> Node:
    return Node(NodeKind.ITEM, ref(index))


def graph(
    *,
    acyclic: bool = True,
    targets: tuple[ItemRef, ...] = (ref(1), ref(1), ref(2)),
) -> Graph:
    declaration = PolyadicRelationDeclaration(
        LINKS,
        RelationSideDeclaration(
            (RelationEndpointKind.ITEM,), tiers=(NODES,), maximum=None
        ),
        RelationSideDeclaration(
            (RelationEndpointKind.ITEM,), tiers=(NODES,), maximum=None
        ),
        acyclic=acyclic,
    )
    return Graph(
        (NamespaceDeclaration("p", NS),),
        (
            Tier(
                TierDeclaration(NODES, "Nodes"),
                (Item("a"), Item("b"), Item("c"), Item("d")),
            ),
        ),
        (declaration,),
        polyadic_relations=(
            PolyadicRelationInstance(LINKS, (ref(0),), targets),
            PolyadicRelationInstance(LINKS, (ref(1),), (ref(3),)),
        ),
    )


def traversal(value: Graph) -> OrderedPolyadicTraversal:
    return OrderedPolyadicTraversal(
        value, LINKS, PolyadicSide.SOURCES, PolyadicSide.TARGETS
    )


def test_direct_sequence_has_a_byte_golden_and_retains_repetition() -> None:
    """The public data bytes pin stored incidence rather than canonical order."""
    result = traversal(graph()).direct(ref(0))
    encoded = json.dumps(
        result.to_data(), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    assert encoded == (
        b'[{"kind":"item","reference":{"index":1,"tier":{"local_name":"nodes",'
        b'"namespace":"urn:test:ordered-polyadic"}}},{"kind":"item","reference":'
        b'{"index":1,"tier":{"local_name":"nodes","namespace":"urn:test:ordered-'
        b'polyadic"}}},{"kind":"item","reference":{"index":2,"tier":{"local_name":'
        b'"nodes","namespace":"urn:test:ordered-polyadic"}}}]'
    )
    assert result.nodes == (node(1), node(1), node(2))


def test_shuffled_declared_incidence_changes_the_sequence() -> None:
    """Negative control: sorting or set-coercing the direct result cannot pass."""
    original = traversal(graph()).direct(ref(0)).to_data()
    result = traversal(graph(targets=(ref(2), ref(1), ref(1)))).direct(ref(0))
    shuffled = result.to_data()
    assert shuffled != original
    assert result.nodes == (node(2), node(1), node(1))


def test_transitive_is_depth_first_preorder_with_repeated_paths() -> None:
    result = traversal(graph()).transitive(ref(0))
    assert result.nodes == (node(1), node(3), node(1), node(3), node(2))


def test_nonacyclic_declaration_allows_direct_but_refuses_transitive() -> None:
    value = graph(acyclic=False)
    assert traversal(value).direct(ref(0)).nodes == (node(1), node(1), node(2))
    with pytest.raises(ValueError, match=r"links.*not declared acyclic"):
        traversal(value).transitive(ref(0))


def test_inverse_is_a_deduplicated_set_but_instance_order_is_requestable() -> None:
    value = graph()
    engine = traversal(value)
    assert engine.inverse(ref(1)).nodes == (node(0),)
    assert engine.stored_opposite(0).nodes == (node(1), node(1), node(2))

    object.__setattr__(value.polyadic_relations[0], "sources", (ref(0), ref(0)))
    assert engine.inverse(ref(1)).nodes == (node(0),)


def test_repeated_source_incidence_repeats_the_opposite_sequence() -> None:
    value = graph()
    first = value.polyadic_relations[0]
    object.__setattr__(first, "sources", (ref(0), ref(0)))
    assert traversal(value).direct(ref(0)).nodes == (
        node(1),
        node(1),
        node(2),
        node(1),
        node(1),
        node(2),
    )


def test_mixed_item_and_boundary_endpoints_resolve_role_neutrally() -> None:
    side = RelationSideDeclaration(
        (RelationEndpointKind.ITEM, RelationEndpointKind.BOUNDARY), tiers=(NODES,)
    )
    declaration = PolyadicRelationDeclaration(LINKS, side, side, acyclic=True)
    before_b = DurablePositionRef(DurableItemRef("b"), BoundarySide.BEFORE)
    after_c = DurablePositionRef(DurableItemRef("c"), BoundarySide.AFTER)
    value = Graph(
        (NamespaceDeclaration("p", NS),),
        (Tier(TierDeclaration(NODES, "Nodes"), (Item("a"), Item("b"), Item("c"))),),
        (declaration,),
        polyadic_relations=(
            PolyadicRelationInstance(LINKS, (ref(0), before_b), (ref(1), after_c)),
        ),
    )
    engine = traversal(value)
    expected = (
        node(1),
        Node(NodeKind.POSITION, value.resolve_position(after_c)),
    )
    assert engine.direct(ref(0)).nodes == expected
    assert engine.direct(DurableItemRef("a")).nodes == expected
    assert engine.direct(before_b).nodes == expected
    structural = value.resolve_position(before_b)
    assert isinstance(structural, PositionRef)
    assert engine.direct(structural).nodes == expected
    assert engine.inverse(after_c).nodes == (
        node(0),
        Node(NodeKind.POSITION, value.resolve_position(before_b)),
    )


def test_selected_sides_can_be_reversed_without_domain_roles() -> None:
    value = graph()
    reverse = OrderedPolyadicTraversal(
        value, LINKS, PolyadicSide.TARGETS, PolyadicSide.SOURCES
    )
    assert reverse.direct(ref(1)).nodes == (node(0), node(0))
    assert reverse.inverse(ref(0)).nodes == (node(1), node(2))


def test_constructor_and_instance_refusals_name_offenders() -> None:
    value = graph()
    with pytest.raises(ValueError, match=r"missing.*polyadic"):
        OrderedPolyadicTraversal(
            value,
            QualifiedName(NS, "missing"),
            PolyadicSide.SOURCES,
            PolyadicSide.TARGETS,
        )
    with pytest.raises(ValueError, match=r"distinct.*sources"):
        OrderedPolyadicTraversal(
            value, LINKS, PolyadicSide.SOURCES, PolyadicSide.SOURCES
        )
    with pytest.raises(ValueError, match=r"links.*instance.*99"):
        traversal(value).stored_opposite(99)
    with pytest.raises(ValueError, match=r"invalid source side"):
        OrderedPolyadicTraversal(
            value,
            LINKS,
            cast(Any, "left"),
            PolyadicSide.TARGETS,
        )
    with pytest.raises(ValueError, match=r"invalid target side"):
        OrderedPolyadicTraversal(
            value,
            LINKS,
            PolyadicSide.SOURCES,
            cast(Any, "right"),
        )
    for invalid_index in (True, "zero"):
        with pytest.raises(ValueError, match=r"invalid instance index"):
            traversal(value).stored_opposite(invalid_index)  # type: ignore[arg-type]


def test_live_side_constraints_name_kind_tier_and_arity() -> None:
    value = graph()
    engine = traversal(value)
    object.__setattr__(value.polyadic_relations[0], "targets", ())
    with pytest.raises(ValueError) as caught:
        engine.direct(ref(0))
    assert str(caught.value) == (
        "ordered polyadic relation '{urn:test:ordered-polyadic}links' "
        "instance 0 has an empty target side"
    )

    value = graph()
    engine = traversal(value)
    boundary = DurablePositionRef(DurableItemRef("a"), BoundarySide.BEFORE)
    object.__setattr__(value.polyadic_relations[0], "targets", (boundary,))
    with pytest.raises(ValueError, match=r"instance 0 target 0.*not an item"):
        engine.direct(ref(0))

    value = graph()
    engine = traversal(value)
    object.__setattr__(
        engine._declaration.targets, "endpoint_kinds", (RelationEndpointKind.BOUNDARY,)
    )
    with pytest.raises(ValueError, match=r"instance 0 target 0.*not a boundary"):
        engine.direct(ref(0))

    value = graph()
    engine = traversal(value)
    object.__setattr__(
        engine._declaration.targets, "tiers", (QualifiedName(NS, "elsewhere"),)
    )
    with pytest.raises(
        ValueError, match=r"instance 0 target endpoint 0.*tier.*not allowed"
    ):
        engine.direct(ref(0))

    value = graph()
    engine = traversal(value)
    object.__setattr__(engine._declaration.targets, "minimum", 4)
    with pytest.raises(ValueError, match=r"instance 0 target arity 3.*4"):
        engine.direct(ref(0))


def test_wrong_origin_type_is_an_offender_bearing_refusal() -> None:
    value = graph()
    with pytest.raises(ValueError, match=r"links.*origin.*outside"):
        traversal(value).direct("not-an-endpoint")  # type: ignore[arg-type]
    boundary = DurablePositionRef(DurableItemRef("a"), BoundarySide.BEFORE)
    with pytest.raises(ValueError, match=r"links.*origin.*boundary.*source side"):
        traversal(value).direct(boundary)

    engine = traversal(value)
    object.__setattr__(
        engine._declaration.sources, "tiers", (QualifiedName(NS, "elsewhere"),)
    )
    with pytest.raises(ValueError, match=r"links.*origin.*tier.*source side"):
        engine.direct(ref(0))


def test_live_cycle_and_wrong_endpoint_are_not_silently_traversed() -> None:
    value = graph(targets=(ref(1),))
    object.__setattr__(
        value,
        "polyadic_relations",
        (
            *value.polyadic_relations,
            PolyadicRelationInstance(LINKS, (ref(3),), (ref(0),)),
        ),
    )
    with pytest.raises(ValueError, match=r"links.*instance 2 closes a cycle"):
        traversal(value).transitive(ref(0))

    outside = ItemRef(QualifiedName(NS, "outside"), 7)
    object.__setattr__(value.polyadic_relations[0], "targets", (outside,))
    with pytest.raises(ValueError, match=r"instance 0 target endpoint 0.*outside"):
        traversal(value).direct(ref(0))
