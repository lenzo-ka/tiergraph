"""Ordered traversal laws for polyadic containment."""

from dataclasses import replace

import pytest

from tiergraph import (
    BipartiteRelationDeclaration,
    Graph,
    Item,
    ItemRef,
    NamespaceDeclaration,
    Node,
    NodeKind,
    NodeSequence,
    OrderedContainment,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    QualifiedName,
    RelationEndpointKind,
    RelationSideDeclaration,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
)

NS = "urn:test:ordered-containment"


def name(local: str) -> QualifiedName:
    return QualifiedName(NS, local)


PARENTS = name("parents")
CHILDREN = name("children")
CONTAINS = name("contains")


def declaration() -> PolyadicRelationDeclaration:
    return PolyadicRelationDeclaration(
        CONTAINS,
        RelationSideDeclaration(
            (RelationEndpointKind.ITEM,), tiers=(PARENTS, CHILDREN), maximum=1
        ),
        RelationSideDeclaration(
            (RelationEndpointKind.ITEM,), tiers=(PARENTS, CHILDREN)
        ),
        unique_sources=True,
        acyclic=True,
    )


def graph(
    declared: PolyadicRelationDeclaration | BipartiteRelationDeclaration | None = None,
) -> Graph:
    relation = declaration() if declared is None else declared
    polyadic: tuple[PolyadicRelationInstance, ...] = ()
    if isinstance(relation, PolyadicRelationDeclaration):
        polyadic = (
            PolyadicRelationInstance(
                CONTAINS,
                (ItemRef(PARENTS, 0),),
                (
                    ItemRef(CHILDREN, 1),
                    ItemRef(PARENTS, 1),
                    ItemRef(CHILDREN, 1),
                ),
            ),
            PolyadicRelationInstance(
                CONTAINS,
                (ItemRef(PARENTS, 1),),
                (ItemRef(CHILDREN, 2), ItemRef(CHILDREN, 0)),
            ),
        )
    return Graph(
        (NamespaceDeclaration("o", NS),),
        (
            Tier(
                TierDeclaration(PARENTS, "Parents"),
                (Item("root"), Item("branch")),
            ),
            Tier(
                TierDeclaration(CHILDREN, "Children"),
                (Item("a"), Item("b"), Item("c")),
            ),
        ),
        (
            SimpleRelationDeclaration(name("parent-members"), PARENTS, name("node")),
            SimpleRelationDeclaration(name("child-members"), CHILDREN, name("node")),
            relation,
        ),
        polyadic_relations=polyadic,
    )


def nodes(*refs: ItemRef) -> tuple[Node, ...]:
    return tuple(Node(NodeKind.ITEM, ref) for ref in refs)


def test_declared_order_repetition_descendants_leaves_and_inverse_fiber() -> None:
    value = graph()
    traversal = OrderedContainment(value, CONTAINS)
    root = ItemRef(PARENTS, 0)
    branch = ItemRef(PARENTS, 1)
    a, b, c = (ItemRef(CHILDREN, index) for index in range(3))

    direct = traversal.direct_children(root)
    assert isinstance(direct, NodeSequence)
    # Canonical item order is root, branch, a, b, c; containment says b, branch, b.
    assert direct.nodes == nodes(b, branch, b)
    assert traversal.descendants(root).nodes == nodes(b, branch, c, a, b)
    assert traversal.leaves(root).nodes == nodes(b, c, a, b)
    assert traversal.parents(b).nodes == nodes(root)
    assert traversal.ancestors(c).nodes == nodes(root, branch)
    assert direct.to_data() == [node.to_data() for node in nodes(b, branch, b)]


def test_leaf_source_and_missing_children_are_well_formed() -> None:
    traversal = OrderedContainment(graph(), CONTAINS)
    leaf = ItemRef(CHILDREN, 0)
    assert traversal.direct_children(leaf).nodes == ()
    assert traversal.leaves(leaf).nodes == nodes(leaf)


def test_ancestor_fiber_stops_after_converging_on_an_already_reached_node() -> None:
    """A shortcut and a longer path to one ancestor still yield a finite set."""
    value = graph()
    root_instance, branch_instance = value.polyadic_relations
    child = ItemRef(CHILDREN, 2)
    shortcut = replace(root_instance, targets=(*root_instance.targets, child))
    value = replace(value, polyadic_relations=(shortcut, branch_instance))
    assert OrderedContainment(value, CONTAINS).ancestors(child).nodes == nodes(
        ItemRef(PARENTS, 0), ItemRef(PARENTS, 1)
    )


def test_role_refusals_name_each_offending_relation_beside_valid_neighbour() -> None:
    assert OrderedContainment(graph(), CONTAINS)

    bipartite = BipartiteRelationDeclaration(
        CONTAINS, name("node"), name("node"), acyclic=True
    )
    with pytest.raises(ValueError, match=r"contains.*polyadic"):
        OrderedContainment(graph(bipartite), CONTAINS)

    boundary = replace(
        declaration(),
        targets=RelationSideDeclaration(
            endpoint_kinds=(
                RelationEndpointKind.ITEM,
                RelationEndpointKind.BOUNDARY,
            ),
            tiers=(PARENTS, CHILDREN),
        ),
    )
    with pytest.raises(ValueError, match=r"contains.*item-only"):
        OrderedContainment(graph(boundary), CONTAINS)

    with pytest.raises(ValueError, match=r"contains.*source uniqueness"):
        OrderedContainment(
            graph(replace(declaration(), unique_sources=False)), CONTAINS
        )
    with pytest.raises(ValueError, match=r"contains.*acyclicity"):
        OrderedContainment(graph(replace(declaration(), acyclic=False)), CONTAINS)


def test_item_refusal_names_offender_beside_valid_neighbour() -> None:
    traversal = OrderedContainment(graph(), CONTAINS)
    assert traversal.direct_children(ItemRef(CHILDREN, 0)).nodes == ()
    offender = ItemRef(name("missing-tier"), 7)
    with pytest.raises(ValueError, match=r"missing-tier.*7.*outside"):
        traversal.direct_children(offender)
