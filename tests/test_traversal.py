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
    Item,
    ItemRef,
    NamespaceDeclaration,
    Node,
    NodeKind,
    NodeSet,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    QualifiedName,
    RelationEndpointKind,
    RelationInstance,
    RelationSideDeclaration,
    Tier,
    TierDeclaration,
    Walk,
    WalkDirection,
)
from tiergraph.spanview import Span, SpanView

LAWS = TraversalLawSuite(Walk)

POLYADIC_NAMESPACE = "urn:test:traversal-polyadic"
POLYADIC_NODES = QualifiedName(POLYADIC_NAMESPACE, "nodes")
POLYADIC_CONTAINS = QualifiedName(POLYADIC_NAMESPACE, "contains")
POLYADIC_SIDECAR = QualifiedName(POLYADIC_NAMESPACE, "sidecar")
POLYADIC_CYCLES = QualifiedName(POLYADIC_NAMESPACE, "cycles")


def polyadic_refs(*indices: int) -> tuple[ItemRef, ...]:
    """Return fixture item coordinates for the polyadic walk graph."""
    return tuple(ItemRef(POLYADIC_NODES, index) for index in indices)


def polyadic_selection(graph: Graph, *indices: int) -> NodeSet:
    """Select fixture items of the polyadic walk graph by coordinate."""
    return NodeSet(
        graph,
        tuple(Node(NodeKind.ITEM, reference) for reference in polyadic_refs(*indices)),
    )


def polyadic_graph() -> Graph:
    """Return one graph carrying three polyadic relations over six items.

    ``contains`` is the relation under test.  Its first instance is wide on both
    sides -- sources ``0, 1`` against targets ``2, 3`` -- which is what separates
    reaching the whole far side from pairing the sides off index by index: a
    positional reading would send ``1`` to ``3`` alone.  Its third instance
    repeats a target already carried by the first, so a step has an edge to
    deduplicate.  ``sidecar`` shares a source with it and reaches ``5``, which no
    ``contains`` walk may return.  ``cycles`` promises nothing and closes a loop,
    so an unbounded walk over it has to be refused.
    """
    sides = RelationSideDeclaration(
        (RelationEndpointKind.ITEM,), (POLYADIC_NODES,), 1, None
    )
    return Graph(
        (NamespaceDeclaration("t", POLYADIC_NAMESPACE),),
        (
            Tier(
                TierDeclaration(POLYADIC_NODES, "Nodes"),
                tuple(Item(f"node-{index}") for index in range(6)),
            ),
        ),
        (
            PolyadicRelationDeclaration(POLYADIC_CONTAINS, sides, sides, acyclic=True),
            PolyadicRelationDeclaration(POLYADIC_SIDECAR, sides, sides, acyclic=True),
            PolyadicRelationDeclaration(POLYADIC_CYCLES, sides, sides),
        ),
        polyadic_relations=(
            PolyadicRelationInstance(
                POLYADIC_CONTAINS, polyadic_refs(0, 1), polyadic_refs(2, 3)
            ),
            PolyadicRelationInstance(
                POLYADIC_CONTAINS, polyadic_refs(3), polyadic_refs(4)
            ),
            PolyadicRelationInstance(
                POLYADIC_CONTAINS, polyadic_refs(0, 1), polyadic_refs(2)
            ),
            PolyadicRelationInstance(
                POLYADIC_SIDECAR, polyadic_refs(0), polyadic_refs(5)
            ),
            PolyadicRelationInstance(
                POLYADIC_CYCLES, polyadic_refs(0), polyadic_refs(1)
            ),
            PolyadicRelationInstance(
                POLYADIC_CYCLES, polyadic_refs(1), polyadic_refs(0)
            ),
        ),
    )


def test_polyadic_step_reaches_the_whole_far_side_not_a_positional_partner() -> None:
    """Any endpoint of the near side reaches every endpoint of the far side."""
    graph = polyadic_graph()
    from_second_source = Walk(
        polyadic_selection(graph, 1), POLYADIC_CONTAINS, WalkDirection.FORWARD, 1
    ).evaluate()
    assert from_second_source.nodes == polyadic_selection(graph, 2, 3)
    from_first_source = Walk(
        polyadic_selection(graph, 0), POLYADIC_CONTAINS, WalkDirection.FORWARD, 1
    ).evaluate()
    assert from_first_source.nodes == from_second_source.nodes


def test_polyadic_walk_reads_only_its_own_declaration() -> None:
    """A sibling polyadic relation sharing a source contributes nothing."""
    graph = polyadic_graph()
    contains = Walk(
        polyadic_selection(graph, 0), POLYADIC_CONTAINS, WalkDirection.FORWARD, None
    ).evaluate()
    assert contains.nodes == polyadic_selection(graph, 2, 3, 4)
    assert contains.truncated is False
    sidecar = Walk(
        polyadic_selection(graph, 0), POLYADIC_SIDECAR, WalkDirection.FORWARD, None
    ).evaluate()
    assert sidecar.nodes == polyadic_selection(graph, 5)


def test_polyadic_walk_reaches_nothing_from_an_unrelated_selection() -> None:
    """A selection occurring on no near side of any instance ends the walk."""
    graph = polyadic_graph()
    result = Walk(
        polyadic_selection(graph, 4), POLYADIC_CONTAINS, WalkDirection.FORWARD, None
    ).evaluate()
    assert result.nodes.nodes == ()
    assert result.truncated is False


def test_polyadic_inverse_is_the_deduplicated_fiber() -> None:
    """Inverse access returns sources once however many edges offered them."""
    graph = polyadic_graph()
    direct = Walk(
        polyadic_selection(graph, 2), POLYADIC_CONTAINS, WalkDirection.INVERSE, 1
    ).evaluate()
    assert direct.nodes == polyadic_selection(graph, 0, 1)
    assert len(direct.nodes.nodes) == 2
    transitive = Walk(
        polyadic_selection(graph, 4), POLYADIC_CONTAINS, WalkDirection.INVERSE, None
    ).evaluate()
    assert transitive.nodes == polyadic_selection(graph, 0, 1, 3)
    assert transitive.truncated is False


def test_polyadic_cap_keeps_truncated_one_sided() -> None:
    """The cap still reports only that a step which found nodes was the last one."""
    graph = polyadic_graph()
    source = polyadic_selection(graph, 0)
    first = Walk(source, POLYADIC_CONTAINS, WalkDirection.FORWARD, 1).evaluate()
    assert first.nodes == polyadic_selection(graph, 2, 3)
    assert first.truncated is True
    assert first.cap == 1
    second = Walk(source, POLYADIC_CONTAINS, WalkDirection.FORWARD, 2).evaluate()
    assert second.nodes == polyadic_selection(graph, 2, 3, 4)
    assert second.truncated is True
    third = Walk(source, POLYADIC_CONTAINS, WalkDirection.FORWARD, 3).evaluate()
    assert third.nodes == second.nodes
    assert third.truncated is False


def test_polyadic_acyclicity_governs_the_unbounded_walk() -> None:
    """The declared promise admits an uncapped walk and its absence refuses one."""
    graph = polyadic_graph()
    source = polyadic_selection(graph, 0)
    unbounded = Walk(source, POLYADIC_CONTAINS, WalkDirection.FORWARD, None).evaluate()
    assert unbounded.nodes == polyadic_selection(graph, 2, 3, 4)
    assert unbounded.truncated is False
    capped = Walk(source, POLYADIC_CYCLES, WalkDirection.FORWARD, 2).evaluate()
    assert capped.nodes == polyadic_selection(graph, 1)
    assert capped.truncated is False
    with pytest.raises(ValueError, match=r"cycles.*not declared acyclic"):
        Walk(source, POLYADIC_CYCLES, WalkDirection.FORWARD, None)


def test_walk_refusal_names_both_admitted_relation_shapes() -> None:
    """A relation of neither walkable shape refuses by naming what a walk takes."""
    graph = LAWS.graph(acyclic=True)
    with pytest.raises(ValueError, match=r"members.*bipartite or polyadic"):
        Walk(LAWS.selection(graph, 0), LAWS.name("members"), WalkDirection.FORWARD, 1)


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
    source = NodeSet(extended, (Node(NodeKind.BOUNDARY, boundary),))
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
