"""The reference implementation satisfies reusable traversal laws."""

from __future__ import annotations

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from tests.conformance.traversal import TraversalLawSuite
from tiergraph import (
    BipartiteRelationDeclaration,
    BoundaryRef,
    BoundarySide,
    DurableBoundaryRef,
    DurableItemRef,
    Graph,
    Node,
    NodeKind,
    NodeSet,
    RelationEndpointKind,
    RelationInstance,
    Walk,
    WalkDirection,
)
from tiergraph.spanview import Span, SpanView

LAWS = TraversalLawSuite(Walk)


def test_walk_deduplicates_coincident_anchors_and_refuses_reentry() -> None:
    """Distinct durable anchors at one boundary are one visited walk identity."""
    anchors = SpanView(
        "ab",
        (
            Span("first", 1, 1, 1, 1, None, None, "first"),
            Span("second", 1, 1, 1, 1, None, None, "second"),
        ),
        ("a", "b"),
    )
    assert len(anchors.spans) == 2
    graph = LAWS.graph(acyclic=False)
    relation = BipartiteRelationDeclaration(
        LAWS.name("anchor-cycle"),
        LAWS.name("node"),
        LAWS.name("node"),
        left_endpoint=RelationEndpointKind.BOUNDARY,
        right_endpoint=RelationEndpointKind.BOUNDARY,
    )
    after_first = DurableBoundaryRef(DurableItemRef("node-0"), BoundarySide.AFTER)
    before_second = DurableBoundaryRef(DurableItemRef("node-1"), BoundarySide.BEFORE)
    extended = Graph(
        graph.namespaces,
        graph.tiers,
        (*graph.relation_declarations, relation),
        (
            *graph.relations,
            RelationInstance(relation.name, after_first, before_second),
            RelationInstance(relation.name, before_second, after_first),
        ),
    )
    boundary = BoundaryRef(LAWS.name("nodes"), 1)
    source = NodeSet(extended, (Node(NodeKind.POSITION, boundary),))
    result = Walk(source, relation.name, WalkDirection.FORWARD, 10).evaluate()
    assert result.nodes.nodes == ()
    assert result.truncated is False


@pytest.mark.parametrize(
    "law",
    [
        LAWS.check_diamond_and_inverse_sets,
        LAWS.check_cyclic_cap_is_visible,
        LAWS.check_acyclic_root_walk_terminates,
        LAWS.check_unbounded_refusal_names_relation,
        LAWS.check_anchored_boundaries_resolve,
        LAWS.check_construction_guards,
    ],
    ids=lambda law: law.__name__,
)
def test_traversal_law(law: object) -> None:
    """Run each reusable law against the reference walker."""
    assert callable(law)
    law()


@example(((0, 1), (0, 1)))  # one child appears twice under one parent
@example(((0, 2), (1, 2)))  # one child has two parents
@example(((0, 1), (0, 2), (1, 3), (2, 3)))  # diamond
@example(((2, 3), (0, 1), (0, 2)))  # reordered, with one diamond parent removed
@example(())  # every item has an empty fiber
@given(
    st.lists(
        st.sampled_from(
            tuple(
                (parent, child) for parent in range(5) for child in range(parent + 1, 5)
            )
        ),
        max_size=12,
    ).map(tuple)
)
def test_inverse_is_the_set_valued_fiber_of_descent(
    edges: tuple[tuple[int, int], ...],
) -> None:
    """Generated incidence cannot make ascending and descending views disagree."""
    LAWS.check_inverse_fiber(edges)
