"""The reference implementation satisfies reusable selection laws."""

from __future__ import annotations

import json

import pytest

from tests.conformance.selection import SelectionLawSuite
from tiergraph import (
    AttributeDomain,
    AttributeQuery,
    BoundariesQuery,
    BoundaryQuery,
    DifferenceQuery,
    IntersectionQuery,
    ItemQuery,
    ItemRef,
    ItemsQuery,
    Node,
    NodeKind,
    TierQuery,
    TypeQuery,
    UnionQuery,
    evaluate_selection,
    select,
    selection_query_loads,
)

LAWS = SelectionLawSuite(select)


@pytest.mark.parametrize(
    "law",
    [
        LAWS.check_axes_and_canonical_order,
        LAWS.check_duplicate_routes_and_set_operations,
        LAWS.check_refusals_name_offenders,
        LAWS.check_boundaries_and_anchors,
        LAWS.check_json_data,
        LAWS.check_every_attribute_domain,
        LAWS.check_remaining_construction_guards,
        LAWS.check_boundary_relation_order,
    ],
    ids=lambda law: law.__name__,
)
def test_selection_law(law: object) -> None:
    """Run each reusable law against the reference selector."""
    assert callable(law)
    law()


def _name(local: str) -> dict[str, str]:
    return {"namespace": LAWS.namespace, "local_name": local}


def test_selection_query_decodes_and_evaluates_every_leaf() -> None:
    """Every declarative leaf delegates to its matching validated selector."""
    graph = LAWS.graph()
    cases = (
        ({"select": "tier", "tier": _name("left")}, TierQuery(LAWS.name("left"))),
        ({"select": "type", "type": _name("shared")}, TypeQuery(LAWS.name("shared"))),
        ({"select": "items", "tier": _name("left")}, ItemsQuery(LAWS.name("left"))),
        (
            {"select": "boundaries", "tier": _name("right")},
            BoundariesQuery(LAWS.name("right")),
        ),
        (
            {"select": "item", "path": "/items/durable/left-0"},
            ItemQuery("/items/durable/left-0"),
        ),
        (
            {
                "select": "boundary",
                "path": "/positions/structural/urn:test:selection/left/1",
            },
            BoundaryQuery("/positions/structural/urn:test:selection/left/1"),
        ),
        (
            {
                "select": "attribute",
                "attribute": _name("mark"),
                "domain": "relation_instance",
            },
            AttributeQuery(LAWS.name("mark"), AttributeDomain.RELATION_INSTANCE),
        ),
    )
    for source, expected in cases:
        query = selection_query_loads(json.dumps(source))
        assert query == expected
        assert evaluate_selection(graph, query).nodes


def test_selection_query_set_algebra_and_nesting() -> None:
    """Union, intersection, difference, and nested compounds retain set semantics."""
    graph = LAWS.graph()
    query = selection_query_loads(
        b'{"op":"difference","left":{"op":"union","args":['
        b'{"select":"items","tier":{"namespace":"urn:test:selection",'
        b'"local_name":"left"}},{"op":"intersection","args":['
        b'{"select":"type","type":{"namespace":"urn:test:selection",'
        b'"local_name":"shared"}},{"select":"items","tier":{'
        b'"namespace":"urn:test:selection","local_name":"right"}}]}]},'
        b'"right":{"select":"attribute","attribute":{'
        b'"namespace":"urn:test:selection","local_name":"mark"},'
        b'"domain":"relation_instance"}}'
    )
    assert isinstance(query, DifferenceQuery)
    assert isinstance(query.left, UnionQuery)
    assert isinstance(query.left.args[1], IntersectionQuery)
    assert evaluate_selection(graph, query).nodes == (
        Node(NodeKind.ITEM, ItemRef(LAWS.name("left"), 0)),
        Node(NodeKind.ITEM, ItemRef(LAWS.name("left"), 1)),
        Node(NodeKind.ITEM, ItemRef(LAWS.name("right"), 0)),
    )


@pytest.mark.parametrize(
    "source, match",
    [
        ([], "must be an object"),
        ({"op": "union", "select": "tier", "args": []}, "exactly one"),
        ({"tier": _name("left")}, "exactly one"),
        ({"op": "unknown", "args": [{}]}, "unknown operation"),
        ({"select": "unknown"}, "unknown selector"),
        ({"op": "union", "args": []}, "non-empty list"),
        ({"select": "tier", "tier": _name("left"), "extra": 1}, "exactly"),
        ({"select": "tier"}, "exactly"),
        (
            {"select": "tier", "tier": {"namespace": [], "local_name": "left"}},
            r"\.namespace must be a string",
        ),
        (
            {"select": "type", "type": {"namespace": "urn:x", "local_name": 4}},
            r"\.local_name must be a string",
        ),
        (
            {"select": "attribute", "attribute": _name("mark"), "domain": "bad"},
            "invalid attribute domain",
        ),
        ({"op": 5, "args": [{}]}, r"\.op must be a string"),
        ({"select": "item", "path": 5}, r"\.path must be a string"),
    ],
)
def test_selection_query_strict_refusals(source: object, match: str) -> None:
    """Malformed declarative nodes fail with stable path-specific ValueErrors."""
    with pytest.raises(ValueError, match=match):
        selection_query_loads(json.dumps(source))


def test_selection_query_wrong_path_kinds_and_empty_public_operations() -> None:
    """Path leaves require their promised kind and public operations stay nonempty."""
    graph = LAWS.graph()
    with pytest.raises(ValueError, match="did not resolve to an item"):
        evaluate_selection(
            graph,
            ItemQuery("/positions/structural/urn:test:selection/left/0"),
        )
    with pytest.raises(ValueError, match="did not resolve to a boundary"):
        evaluate_selection(graph, BoundaryQuery("/items/durable/left-0"))
    with pytest.raises(ValueError, match="union query"):
        UnionQuery(())
    with pytest.raises(ValueError, match="intersection query"):
        IntersectionQuery(())
