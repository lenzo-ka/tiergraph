"""REGRESSION tests for total positional displacement across graph edits."""

from collections.abc import Callable
from random import Random

import pytest

from tiergraph import (
    BipartiteRelationDeclaration,
    BoundaryRef,
    Displacement,
    Graph,
    GraphEditor,
    GraphValidationError,
    Item,
    ItemRef,
    NamespaceDeclaration,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    QualifiedName,
    RelationEndpointKind,
    RelationInstance,
    RelationSideDeclaration,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
)

NS = "urn:displacement"
TIER = QualifiedName(NS, "tokens")
MEMBERSHIP = QualifiedName(NS, "membership")
ITEM_TYPE = QualifiedName(NS, "Token")
LINK = QualifiedName(NS, "link")
GROUP = QualifiedName(NS, "group")


def graph_with_spaces(
    item_count: int = 7, relation_count: int = 4, polyadic_count: int = 3
) -> Graph:
    """Return one graph with anonymous items and both instance index spaces."""
    side = RelationSideDeclaration((RelationEndpointKind.ITEM,), (TIER,))
    return Graph(
        (NamespaceDeclaration("d", NS),),
        (Tier(TierDeclaration(TIER, "tokens"), (Item(),) * item_count),),
        (
            SimpleRelationDeclaration(MEMBERSHIP, TIER, ITEM_TYPE),
            BipartiteRelationDeclaration(LINK, ITEM_TYPE, ITEM_TYPE),
            PolyadicRelationDeclaration(GROUP, side, side),
        ),
        tuple(
            RelationInstance(LINK, ItemRef(TIER, 0), ItemRef(TIER, 1), f"r{index}")
            for index in range(relation_count)
        ),
        (),
        (),
        (),
        tuple(
            PolyadicRelationInstance(
                GROUP,
                (ItemRef(TIER, 0),),
                (ItemRef(TIER, 1),),
                f"p{index}",
            )
            for index in range(polyadic_count)
        ),
    )


def assert_total(source: Graph, displacement: Displacement) -> None:
    """Assert the mapped/departed exclusive partition in all four spaces."""
    spaces = (
        (
            {
                ItemRef(tier.declaration.name, index)
                for tier in source.tiers
                for index in range(len(tier.items))
            },
            set(displacement.items),
            set(displacement.departed_items),
        ),
        (
            {
                BoundaryRef(tier.declaration.name, index)
                for tier in source.tiers
                for index in range(len(tier.items) + 1)
            },
            set(displacement.boundaries),
            set(displacement.departed_boundaries),
        ),
        (
            set(range(len(source.relations))),
            set(displacement.relations),
            set(displacement.departed_relations),
        ),
        (
            set(range(len(source.polyadic_relations))),
            set(displacement.polyadic_relations),
            set(displacement.departed_polyadic_relations),
        ),
    )
    for source_positions, mapped, departed in spaces:
        assert mapped.isdisjoint(departed)
        assert mapped | departed == source_positions


def test_displacement_is_total_over_every_source_position() -> None:
    """REGRESSION (parent: dependency): T26's invariant is a property."""
    random = Random(20260830)
    for _ in range(100):
        item_count = random.randint(3, 12)
        relation_count = random.randint(0, 8)
        polyadic_count = random.randint(0, 8)
        source = graph_with_spaces(item_count, relation_count, polyadic_count)
        editor = source.edit()
        insertion = random.randint(0, 2)
        editor.insert_item(TIER, insertion, Item())
        editor.remove_item(ItemRef(TIER, insertion))
        editor.move_item(ItemRef(TIER, item_count - 1), 2)
        if relation_count:
            editor.remove_relation(0)
        if polyadic_count:
            editor.remove_relation("p0")
        assert_total(source, editor.displacement())


def test_stationary_maps_every_position_to_itself() -> None:
    """REGRESSION (parent: dependency): stationary positions are not silence."""
    graph = graph_with_spaces()
    stationary = Displacement.stationary(graph)
    assert_total(graph, stationary)
    assert all(source == target for source, target in stationary.items.items())
    assert all(source == target for source, target in stationary.boundaries.items())
    assert stationary.relations == {index: index for index in range(4)}
    assert stationary.polyadic_relations == {index: index for index in range(3)}


def test_relation_removals_shift_both_graph_wide_index_spaces() -> None:
    """REGRESSION (parent: dependency): removal supplies the two missing maps."""
    editor = graph_with_spaces().edit()
    editor.remove_relation(1)
    editor.remove_relation("p1")
    displacement = editor.displacement()
    assert displacement.relations == {0: 0, 2: 1, 3: 2}
    assert displacement.departed_relations == frozenset({1})
    assert displacement.polyadic_relations == {0: 0, 2: 1}
    assert displacement.departed_polyadic_relations == frozenset({1})


def test_then_composes_in_operational_order() -> None:
    """REGRESSION (parent: dependency): the earlier image feeds the later map."""
    graph = graph_with_spaces(relation_count=0, polyadic_count=0)
    first_editor = graph.edit()
    first_editor.move_item(ItemRef(TIER, 0), 2)
    middle = first_editor.freeze()
    later_editor = middle.edit()
    later_editor.move_item(ItemRef(TIER, 1), 3)
    composed = first_editor.displacement().then(later_editor.displacement())
    assert composed.items[ItemRef(TIER, 0)] == ItemRef(TIER, 1)
    assert composed.items[ItemRef(TIER, 2)] == ItemRef(TIER, 3)


def test_composition_refuses_a_later_displacement_with_no_image() -> None:
    """REGRESSION (parent: dependency): T28 pins refusal 6.17 verbatim."""
    source = graph_with_spaces()
    earlier = Displacement.stationary(source)
    later = Displacement.stationary(graph_with_spaces(item_count=3))
    with pytest.raises(GraphValidationError) as refusal:
        earlier.then(later)
    assert str(refusal.value) == (
        "displacement composition names '{urn:displacement}tokens[3]' as an "
        "image, and the later displacement is not about a graph that has it; a "
        "composition is defined only where the first displacement's result is "
        "the second's source"
    )


def apply_script_step(editor: GraphEditor, step: int) -> None:
    """Apply one member of the fixed script to an editor."""
    kind = step % 7
    graph = editor.freeze()
    if kind == 0:
        editor.insert_item(TIER, 2, Item())
    elif kind == 1:
        editor.move_item(ItemRef(TIER, len(graph.tiers[0].items) - 1), 2)
    elif kind == 2:
        editor.remove_item(ItemRef(TIER, len(graph.tiers[0].items) - 1))
    elif kind == 3:
        editor.remove_relation(0)
    elif kind == 4:
        editor.add_relation(
            RelationInstance(
                LINK, ItemRef(TIER, 0), ItemRef(TIER, 1), f"added-r-{step}"
            )
        )
    elif kind == 5:
        editor.remove_relation(graph.polyadic_relations[0].durable_id or "")
    else:
        editor.add_relation(
            PolyadicRelationInstance(
                GROUP,
                (ItemRef(TIER, 0),),
                (ItemRef(TIER, 1),),
                f"added-p-{step}",
            )
        )


def test_composed_steps_equal_the_fixed_40_operation_script() -> None:
    """REGRESSION (parent: dependency): T27 detects positional composition."""
    source = graph_with_spaces()
    whole_script = source.edit()
    composed = Displacement.stationary(source)
    current = source
    operations: tuple[Callable[[GraphEditor, int], None], ...] = (apply_script_step,)
    for step in range(40):
        one_step = current.edit()
        operations[0](one_step, step)
        operations[0](whole_script, step)
        composed = composed.then(one_step.displacement())
        current = one_step.freeze()
    assert composed == whole_script.displacement()
    assert_total(source, composed)
    assert composed.departed_items
    assert composed.departed_boundaries
    assert composed.departed_relations
    assert composed.departed_polyadic_relations
