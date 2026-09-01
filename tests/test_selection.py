"""The reference implementation satisfies reusable selection laws."""

from __future__ import annotations

import importlib
import json

import pytest

import tiergraph
from tests.conformance.selection import SelectionLawSuite
from tiergraph import (
    AttributeDomain,
    AttributeSelector,
    BoundariesSelector,
    BoundaryPathSelector,
    BoundarySelector,
    DifferenceSelector,
    IntersectionSelector,
    ItemPathSelector,
    ItemRef,
    ItemSelector,
    ItemsSelector,
    Node,
    NodeKind,
    TierSelector,
    TypeSelector,
    UnionSelector,
    evaluate_selection,
    selection_loads,
)

LAWS = SelectionLawSuite(evaluate_selection)


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
        ({"select": "tier", "tier": _name("left")}, TierSelector(LAWS.name("left"))),
        (
            {"select": "type", "type": _name("shared")},
            TypeSelector(LAWS.name("shared")),
        ),
        ({"select": "items", "tier": _name("left")}, ItemsSelector(LAWS.name("left"))),
        (
            {"select": "boundaries", "tier": _name("right")},
            BoundariesSelector(LAWS.name("right")),
        ),
        (
            {"select": "item", "path": "/items/durable/left-0"},
            ItemPathSelector("/items/durable/left-0"),
        ),
        (
            {
                "select": "boundary",
                "path": "/positions/structural/urn:test:selection/left/1",
            },
            BoundaryPathSelector("/positions/structural/urn:test:selection/left/1"),
        ),
        (
            {
                "select": "attribute",
                "attribute": _name("mark"),
                "domain": "relation_instance",
            },
            AttributeSelector(LAWS.name("mark"), AttributeDomain.RELATION_INSTANCE),
        ),
    )
    for source, expected in cases:
        query = selection_loads(json.dumps(source))
        assert query == expected
        assert evaluate_selection(graph, query).nodes


def test_selection_query_set_algebra_and_nesting() -> None:
    """Union, intersection, difference, and nested compounds retain set semantics."""
    graph = LAWS.graph()
    query = selection_loads(
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
    assert isinstance(query, DifferenceSelector)
    assert isinstance(query.left, UnionSelector)
    assert isinstance(query.left.args[1], IntersectionSelector)
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
        ({"select": "tier", "tier": _name("left"), "extra": 1}, "unknown fields"),
        ({"select": "tier"}, "missing fields"),
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
        selection_loads(json.dumps(source))


def test_selection_query_wrong_path_kinds_and_empty_public_operations() -> None:
    """Path leaves require their promised kind and public operations stay nonempty."""
    graph = LAWS.graph()
    with pytest.raises(ValueError, match="did not resolve to an item"):
        evaluate_selection(
            graph,
            ItemPathSelector("/positions/structural/urn:test:selection/left/0"),
        )
    with pytest.raises(ValueError, match="did not resolve to a boundary"):
        evaluate_selection(graph, BoundaryPathSelector("/items/durable/left-0"))
    with pytest.raises(ValueError, match="union selector"):
        UnionSelector(())
    with pytest.raises(ValueError, match="intersection selector"):
        IntersectionSelector(())


def test_graph_free_selector_validation_occurs_at_evaluation() -> None:
    """A selector is portable until evaluation binds and validates its graph."""
    missing = TierSelector(LAWS.name("missing"))
    with pytest.raises(ValueError, match="is undeclared"):
        evaluate_selection(LAWS.graph(), missing)


def test_reference_and_path_item_selectors_remain_distinct() -> None:
    """Reference and profile-resolved item leaves agree without becoming one type."""
    graph = LAWS.graph()
    reference = ItemSelector(ItemRef(LAWS.name("left"), 0))
    path = ItemPathSelector("/items/structural/urn:test:selection/left/0")
    assert isinstance(reference, ItemSelector)
    assert isinstance(path, ItemPathSelector)
    assert evaluate_selection(graph, reference) == evaluate_selection(graph, path)


def test_every_node_kind_has_a_leaf_selector() -> None:
    """Adding a node kind requires an explicit leaf capable of addressing it."""
    leaf_by_kind = {
        NodeKind.DOCUMENT: AttributeSelector,
        NodeKind.TIER: TierSelector,
        NodeKind.ITEM: ItemSelector,
        NodeKind.BOUNDARY: BoundarySelector,
        NodeKind.RELATION_DECLARATION: AttributeSelector,
        NodeKind.RELATION_INSTANCE: AttributeSelector,
        NodeKind.POLYADIC_RELATION_INSTANCE: AttributeSelector,
    }
    # Set equality rather than containment: a mapping still naming a kind the
    # vocabulary dropped satisfies `all(... in ...)` while claiming to be the
    # vocabulary.  The named leaves are held to being public too, because a
    # leaf a caller cannot import addresses nothing for them.
    assert set(leaf_by_kind) == set(NodeKind)
    for leaf in set(leaf_by_kind.values()):
        assert leaf.__name__ in tiergraph.__all__
        assert getattr(tiergraph, leaf.__name__) is leaf


def test_removed_select_is_not_public() -> None:
    """The clean break removes every legacy name, as attribute and as export."""
    removed = (
        "select",
        "SelectionQuery",
        "TierQuery",
        "TypeQuery",
        "ItemsQuery",
        "BoundariesQuery",
        "ItemQuery",
        "BoundaryQuery",
        "AttributeQuery",
        "UnionQuery",
        "IntersectionQuery",
        "DifferenceQuery",
        "selection_query_loads",
    )
    for name in removed:
        assert name not in tiergraph.__all__
        assert not hasattr(tiergraph, name)
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("tiergraph.selection_query")


@pytest.mark.parametrize(
    "source, message",
    [
        (
            {"op": "unknown", "args": []},
            "$.op has unknown operation 'unknown'",
        ),
        (
            {"select": "tier"},
            "$ is missing fields ['tier']",
        ),
        (
            {"op": "union", "select": "tier", "args": []},
            "$ must contain exactly one of 'op' or 'select'",
        ),
    ],
)
def test_selection_loads_preserves_exact_strict_errors(
    source: object, message: str
) -> None:
    """The renamed decoder preserves strict JSON diagnostics and dollar paths."""
    with pytest.raises(ValueError) as caught:
        selection_loads(json.dumps(source))
    assert str(caught.value) == message
