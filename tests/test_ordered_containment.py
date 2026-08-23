"""Ordered traversal laws for polyadic containment."""

from dataclasses import replace

import pytest

from tiergraph import (
    BipartiteRelationDeclaration,
    BoundarySide,
    DurableItemRef,
    DurablePositionRef,
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


def test_deep_containment_walks_are_iterative_in_both_directions() -> None:
    """All transitive containment walks admit the kernel's supported depth."""
    item_count = 1500
    tier_name = name("deep-nodes")
    deep_contains = name("deep-contains")
    tier = Tier(
        TierDeclaration(tier_name, "Deep nodes"),
        tuple(Item(str(index)) for index in range(item_count)),
    )
    relation = PolyadicRelationDeclaration(
        deep_contains,
        RelationSideDeclaration((RelationEndpointKind.ITEM,), tiers=(tier_name,)),
        RelationSideDeclaration((RelationEndpointKind.ITEM,), tiers=(tier_name,)),
        unique_sources=True,
        acyclic=True,
    )
    instances = tuple(
        PolyadicRelationInstance(
            deep_contains,
            (ItemRef(tier_name, index),),
            (ItemRef(tier_name, index + 1),),
        )
        for index in range(item_count - 1)
    )
    value = Graph(
        (NamespaceDeclaration("o", NS),),
        (tier,),
        (relation,),
        polyadic_relations=instances,
    )
    traversal = OrderedContainment(value, deep_contains)
    first = ItemRef(tier_name, 0)
    last = ItemRef(tier_name, item_count - 1)

    assert len(traversal.descendants(first).nodes) == item_count - 1
    assert traversal.leaves(first).nodes == nodes(last)
    assert traversal.parents(last).nodes == nodes(ItemRef(tier_name, item_count - 2))
    assert len(traversal.ancestors(last).nodes) == item_count - 1


def test_runtime_endpoint_kind_refusal_names_corrupt_instance() -> None:
    """Traversal never narrows a corrupt item-only incidence by filtering it."""
    value = graph()
    instance = value.polyadic_relations[0]
    boundary = DurablePositionRef(DurableItemRef("root"), BoundarySide.AFTER)
    object.__setattr__(instance, "targets", (*instance.targets, boundary))

    with pytest.raises(ValueError) as caught:
        OrderedContainment(value, CONTAINS)
    assert str(caught.value) == (
        "ordered containment relation "
        "'{urn:test:ordered-containment}contains' instance 0 target 3 is not an item"
    )


def test_construction_refuses_item_endpoint_outside_the_frozen_graph() -> None:
    """Cached incidence still validates item membership when it is constructed."""
    value = graph()
    instance = value.polyadic_relations[0]
    outside = ItemRef(name("missing-tier"), 7)
    object.__setattr__(instance, "targets", (*instance.targets, outside))

    with pytest.raises(ValueError, match=r"instance 0 target.*outside its graph"):
        OrderedContainment(value, CONTAINS)


@pytest.mark.parametrize("reverse", [False, True])
def test_runtime_source_uniqueness_refusal_is_independent_of_instance_order(
    reverse: bool,
) -> None:
    """Corrupt duplicate source fibers refuse instead of gaining tuple semantics."""
    value = graph()
    root = ItemRef(PARENTS, 0)
    duplicate = PolyadicRelationInstance(CONTAINS, (root,), (ItemRef(CHILDREN, 0),))
    instances = (*value.polyadic_relations, duplicate)
    object.__setattr__(
        value,
        "polyadic_relations",
        tuple(reversed(instances)) if reverse else instances,
    )

    with pytest.raises(
        ValueError, match=r"contains.*source.*index.*0.*instances.*violating"
    ):
        OrderedContainment(value, CONTAINS)


@pytest.mark.parametrize(
    "method_name", ["direct_children", "descendants", "leaves", "parents", "ancestors"]
)
def test_runtime_cycle_refusal_names_injected_closing_instance(
    method_name: str,
) -> None:
    """A live acyclicity promise is checked against live incidence structure."""
    tier_name = name("cycle-nodes")
    contains = name("cycle-contains")
    tier = Tier(
        TierDeclaration(tier_name, "Cycle nodes"),
        tuple(Item(str(index)) for index in range(3)),
    )
    relation = PolyadicRelationDeclaration(
        contains,
        RelationSideDeclaration(
            (RelationEndpointKind.ITEM,), tiers=(tier_name,), maximum=1
        ),
        RelationSideDeclaration((RelationEndpointKind.ITEM,), tiers=(tier_name,)),
        unique_sources=True,
        acyclic=True,
    )
    instances = tuple(
        PolyadicRelationInstance(
            contains,
            (ItemRef(tier_name, index),),
            (ItemRef(tier_name, index + 1),),
        )
        for index in range(2)
    )
    value = Graph(
        (NamespaceDeclaration("o", NS),),
        (tier,),
        (relation,),
        polyadic_relations=instances,
    )
    closing = PolyadicRelationInstance(
        contains, (ItemRef(tier_name, 2),), (ItemRef(tier_name, 0),)
    )
    object.__setattr__(value, "polyadic_relations", (*instances, closing))

    with pytest.raises(ValueError) as caught:
        traversal = OrderedContainment(value, contains)
        getattr(traversal, method_name)(ItemRef(tier_name, 0))
    assert str(caught.value) == (
        "ordered containment relation "
        "'{urn:test:ordered-containment}cycle-contains' instance 2 closes a cycle "
        "at {'tier': {'namespace': 'urn:test:ordered-containment', "
        "'local_name': 'cycle-nodes'}, 'index': 0}"
    )


def test_runtime_empty_side_refusal_names_corrupt_instance_and_side() -> None:
    """Live incidence validation rechecks the declaration's empty-side contract."""
    value = graph()
    object.__setattr__(value.polyadic_relations[0], "targets", ())

    with pytest.raises(ValueError) as caught:
        OrderedContainment(value, CONTAINS)
    assert str(caught.value) == (
        "ordered containment relation "
        "'{urn:test:ordered-containment}contains' instance 0 has an empty target side"
    )


def test_runtime_empty_side_remains_valid_when_explicitly_allowed() -> None:
    """The explicit empty-side exception still bypasses ordinary arity bounds."""
    value = graph()
    declaration = next(
        item for item in value.relation_declarations if item.name == CONTAINS
    )
    assert isinstance(declaration, PolyadicRelationDeclaration)
    object.__setattr__(declaration.targets, "allow_empty", True)
    object.__setattr__(value.polyadic_relations[0], "targets", ())

    traversal = OrderedContainment(value, CONTAINS)
    assert traversal.direct_children(ItemRef(PARENTS, 0)).nodes == ()


@pytest.mark.parametrize(
    ("bound_name", "bound", "expected"),
    [
        (
            "minimum",
            3,
            "ordered containment relation "
            "'{urn:test:ordered-containment}contains' instance 1 target arity 2 "
            "is outside declared bounds 3..None",
        ),
        (
            "maximum",
            2,
            "ordered containment relation "
            "'{urn:test:ordered-containment}contains' instance 0 target arity 3 "
            "is outside declared bounds 1..2",
        ),
    ],
)
def test_runtime_arity_refusal_names_corrupt_instance_and_side(
    bound_name: str, bound: int, expected: str
) -> None:
    """Nonempty live sides must remain inside both declared arity bounds."""
    value = graph()
    declaration = next(
        item for item in value.relation_declarations if item.name == CONTAINS
    )
    assert isinstance(declaration, PolyadicRelationDeclaration)
    object.__setattr__(declaration.targets, bound_name, bound)

    with pytest.raises(ValueError) as caught:
        OrderedContainment(value, CONTAINS)
    assert str(caught.value) == expected


def test_runtime_validation_ignores_instances_of_other_relations() -> None:
    """The live index consumes only the selected relation's incidences."""
    value = graph()
    object.__setattr__(value.polyadic_relations[1], "declaration", name("other"))

    traversal = OrderedContainment(value, CONTAINS)
    assert traversal.direct_children(ItemRef(PARENTS, 0)).nodes
