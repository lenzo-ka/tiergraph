"""REGRESSION tests for total positional displacement across graph edits."""

from collections.abc import Callable, Hashable, Mapping
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
    item_count: int = 7,
    relation_count: int = 4,
    polyadic_count: int = 3,
    *,
    durable_items: bool = False,
) -> Graph:
    """Return one graph with both instance index spaces."""
    side = RelationSideDeclaration((RelationEndpointKind.ITEM,), (TIER,))
    items = tuple(
        Item(f"item-{index}" if durable_items else None) for index in range(item_count)
    )
    return Graph(
        (NamespaceDeclaration("d", NS),),
        (Tier(TierDeclaration(TIER, "tokens"), items),),
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


def assert_total(source: Graph, result: Graph, displacement: Displacement) -> None:
    """Assert a total partition with distinct images in the result spaces."""

    def assert_space[Coordinate: Hashable](
        source_positions: set[Coordinate],
        mapping: Mapping[Coordinate, Coordinate],
        departed: frozenset[Coordinate],
        result_positions: set[Coordinate],
    ) -> None:
        mapped = set(mapping)
        images = set(mapping.values())
        assert mapped.isdisjoint(departed)
        assert mapped | departed == source_positions
        assert images <= result_positions
        assert len(images) == len(mapping)

    source_items = {
        ItemRef(tier.declaration.name, index)
        for tier in source.tiers
        for index in range(len(tier.items))
    }
    result_items = {
        ItemRef(tier.declaration.name, index)
        for tier in result.tiers
        for index in range(len(tier.items))
    }
    assert_space(
        source_items, displacement.items, displacement.departed_items, result_items
    )
    source_boundaries = {
        BoundaryRef(tier.declaration.name, index)
        for tier in source.tiers
        for index in range(len(tier.items) + 1)
    }
    result_boundaries = {
        BoundaryRef(tier.declaration.name, index)
        for tier in result.tiers
        for index in range(len(tier.items) + 1)
    }
    assert_space(
        source_boundaries,
        displacement.boundaries,
        displacement.departed_boundaries,
        result_boundaries,
    )
    assert_space(
        set(range(len(source.relations))),
        displacement.relations,
        displacement.departed_relations,
        set(range(len(result.relations))),
    )
    assert_space(
        set(range(len(source.polyadic_relations))),
        displacement.polyadic_relations,
        displacement.departed_polyadic_relations,
        set(range(len(result.polyadic_relations))),
    )


def displacement_oracle(source: Graph, result: Graph) -> Displacement:
    """Match durable members without using editor displacement or composition."""
    source_items = {
        item.durable_id: ItemRef(tier.declaration.name, index)
        for tier in source.tiers
        for index, item in enumerate(tier.items)
        if item.durable_id is not None
    }
    result_items = {
        item.durable_id: ItemRef(tier.declaration.name, index)
        for tier in result.tiers
        for index, item in enumerate(tier.items)
        if item.durable_id is not None
    }
    items = {
        coordinate: result_items[durable_id]
        for durable_id, coordinate in source_items.items()
        if durable_id in result_items
    }

    boundaries: dict[BoundaryRef, BoundaryRef] = {}
    for tier in source.tiers:
        result_tier = next(
            candidate
            for candidate in result.tiers
            if candidate.declaration.name == tier.declaration.name
        )
        result_ids = [item.durable_id for item in result_tier.items]
        for index in range(len(tier.items) + 1):
            left = None if index == 0 else tier.items[index - 1].durable_id
            right = None if index == len(tier.items) else tier.items[index].durable_id
            if left is None:
                image = 0 if result_ids and result_ids[0] == right else None
            elif right is None:
                image = (
                    len(result_ids) if result_ids and result_ids[-1] == left else None
                )
            elif left in result_ids and right in result_ids:
                right_index = result_ids.index(right)
                image = (
                    right_index
                    if right_index > 0 and result_ids[right_index - 1] == left
                    else None
                )
            else:
                image = None
            if image is not None:
                boundaries[BoundaryRef(tier.declaration.name, index)] = BoundaryRef(
                    tier.declaration.name, image
                )

    def instance_map(
        source_instances: tuple[object, ...], result_instances: tuple[object, ...]
    ) -> dict[int, int]:
        result_by_id = {
            instance.durable_id: index
            for index, instance in enumerate(result_instances)
            if isinstance(instance, RelationInstance | PolyadicRelationInstance)
            and instance.durable_id is not None
        }
        return {
            index: result_by_id[instance.durable_id]
            for index, instance in enumerate(source_instances)
            if isinstance(instance, RelationInstance | PolyadicRelationInstance)
            and instance.durable_id in result_by_id
        }

    relations = instance_map(source.relations, result.relations)
    polyadic_relations = instance_map(
        source.polyadic_relations, result.polyadic_relations
    )
    all_items = {
        ItemRef(tier.declaration.name, index)
        for tier in source.tiers
        for index in range(len(tier.items))
    }
    all_boundaries = {
        BoundaryRef(tier.declaration.name, index)
        for tier in source.tiers
        for index in range(len(tier.items) + 1)
    }
    return Displacement(
        items,
        boundaries,
        relations,
        polyadic_relations,
        frozenset(all_items - items.keys()),
        frozenset(all_boundaries - boundaries.keys()),
        frozenset(set(range(len(source.relations))) - relations.keys()),
        frozenset(
            set(range(len(source.polyadic_relations))) - polyadic_relations.keys()
        ),
    )


def compose_oracle_space[Coordinate: Hashable](
    earlier: Mapping[Coordinate, Coordinate],
    earlier_departed: frozenset[Coordinate],
    later: Mapping[Coordinate, Coordinate],
    later_departed: frozenset[Coordinate],
) -> tuple[dict[Coordinate, Coordinate], frozenset[Coordinate]]:
    """Compose oracle maps without calling either production composition path."""
    mapped = {
        source: later[intermediate]
        for source, intermediate in earlier.items()
        if intermediate in later
    }
    departed = earlier_departed | frozenset(
        source
        for source, intermediate in earlier.items()
        if intermediate in later_departed
    )
    assert len(mapped) + len(departed) == len(earlier) + len(earlier_departed)
    return mapped, departed


def compose_oracle(earlier: Displacement, later: Displacement) -> Displacement:
    """Compose every independently observed positional space."""
    items, departed_items = compose_oracle_space(
        earlier.items, earlier.departed_items, later.items, later.departed_items
    )
    boundaries, departed_boundaries = compose_oracle_space(
        earlier.boundaries,
        earlier.departed_boundaries,
        later.boundaries,
        later.departed_boundaries,
    )
    relations, departed_relations = compose_oracle_space(
        earlier.relations,
        earlier.departed_relations,
        later.relations,
        later.departed_relations,
    )
    polyadic, departed_polyadic = compose_oracle_space(
        earlier.polyadic_relations,
        earlier.departed_polyadic_relations,
        later.polyadic_relations,
        later.departed_polyadic_relations,
    )
    return Displacement(
        items,
        boundaries,
        relations,
        polyadic,
        departed_items,
        departed_boundaries,
        departed_relations,
        departed_polyadic,
    )


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
        assert_total(source, editor.freeze(), editor.displacement())


def test_stationary_maps_every_position_to_itself() -> None:
    """REGRESSION (parent: dependency): stationary positions are not silence."""
    graph = graph_with_spaces()
    stationary = Displacement.stationary(graph)
    assert_total(graph, graph, stationary)
    assert all(source == target for source, target in stationary.items.items())
    assert all(source == target for source, target in stationary.boundaries.items())
    assert stationary.relations == {index: index for index in range(4)}
    assert stationary.polyadic_relations == {index: index for index in range(3)}


def test_displacement_refuses_a_position_both_mapped_and_departed() -> None:
    """REGRESSION (F7): each source-space partition is exclusive."""
    coordinate = ItemRef(TIER, 0)
    with pytest.raises(GraphValidationError, match="both mapped and departed"):
        Displacement(
            {coordinate: coordinate},
            {},
            {},
            {},
            frozenset({coordinate}),
            frozenset(),
            frozenset(),
            frozenset(),
        )


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


def test_composition_carries_an_earlier_gap_and_refuses_a_later_one() -> None:
    """CHARACTERIZATION: totality is the producer's to supply, not the algebra's.

    The same partial displacement is accepted as the earlier argument and refused
    as the later one. Composition ranges over the earlier map's domain, so a
    coordinate missing from it is never consulted and the composite inherits the
    gap; that same coordinate missing from the later map is an image with nowhere
    to go, which is the refusal the test above pins. Construction cannot decide
    either case -- a displacement does not carry the graph it is about -- so this
    records where the asymmetry actually lives. See docs/concepts.md.
    """
    source = graph_with_spaces(item_count=3, relation_count=0, polyadic_count=0)
    total = Displacement.stationary(source)
    partial = Displacement(
        items={ItemRef(TIER, 0): ItemRef(TIER, 0)},
        boundaries={},
        relations={},
        polyadic_relations={},
        departed_items=frozenset(),
        departed_boundaries=frozenset(),
        departed_relations=frozenset(),
        departed_polyadic_relations=frozenset(),
    )

    carried = partial.then(total)
    assert carried.items[ItemRef(TIER, 0)] == ItemRef(TIER, 0)
    assert ItemRef(TIER, 1) not in carried.items
    assert ItemRef(TIER, 1) not in carried.departed_items
    assert not carried.boundaries

    with pytest.raises(GraphValidationError):
        total.then(partial)


def apply_script_step(editor: GraphEditor, step: int) -> None:
    """Apply one member of the fixed script to an editor."""
    kind = step % 7
    graph = editor.freeze()
    if kind == 0:
        editor.insert_item(TIER, 2, Item(f"added-item-{step}"))
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
    source = graph_with_spaces(durable_items=True)
    whole_script = source.edit()
    composed = Displacement.stationary(source)
    expected = Displacement.stationary(source)
    current = source
    operations: tuple[Callable[[GraphEditor, int], None], ...] = (apply_script_step,)
    for step in range(40):
        one_step = current.edit()
        operations[0](one_step, step)
        operations[0](whole_script, step)
        composed = composed.then(one_step.displacement())
        result = one_step.freeze()
        expected = compose_oracle(expected, displacement_oracle(current, result))
        current = result
    assert composed == expected
    assert whole_script.displacement() == expected
    assert_total(source, current, composed)
    assert composed.departed_items
    assert composed.departed_boundaries
    assert composed.departed_relations
    assert composed.departed_polyadic_relations
