"""Exact distance engines, declared costs, admissibility, and graph bounds."""

from __future__ import annotations

import heapq
import json
from collections import deque
from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from tests.test_edit_primitives import fixture as primitive_fixture
from tiergraph import (
    PRIMITIVE_KINDS,
    UNIT_COSTS,
    AddItem,
    AdmissibleProjection,
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    BoundaryRef,
    BoundarySide,
    CostTable,
    DistanceInterval,
    DurableBoundaryRef,
    DurableItemRef,
    DurablePolyadicRef,
    DurableRelationRef,
    EquivalenceView,
    Graph,
    GraphCarrier,
    GraphValidationError,
    Item,
    ItemRef,
    Journal,
    JournalEditor,
    Layer,
    LayerFact,
    LayerName,
    NamespaceDeclaration,
    OrderedTree,
    Patch,
    PatchOperation,
    PolyadicInstanceRef,
    PolyadicRelationDeclaration,
    ProjectionWitness,
    QualifiedName,
    Refusal,
    RelationEndpointKind,
    RelationInstance,
    RelationInstanceRef,
    RelationSideDeclaration,
    Seal,
    SequenceProjection,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    XsdType,
    check_projection_admissibility,
    contiguous_segmentation_distance,
    diff,
    distance,
    dump_bytes,
    fingerprint,
    format_control_insensitive_projection,
    ordered_tree_distance,
    price_patch,
    text_projection,
    weighted_sequence_distance,
    whitespace_insensitive_projection,
)
from tiergraph.cli import main
from tiergraph.distance import (
    _delta_primitive_kind,
    _item_substitution,
    _operation_declaration,
    _rebuild_upper_bound,
    _relation_declaration,
    _undeclare_all,
)
from tiergraph.machine import DeltaOpcode, _EditCall

NS = "urn:test:distance"
TOKENS = QualifiedName(NS, "tokens")
TOKEN_MEMBERS = QualifiedName(NS, "token-members")
TOKEN_TYPE = QualifiedName(NS, "Token")
LINK = QualifiedName(NS, "link")
VALUE = QualifiedName(NS, "value")


def graph(*labels: str, relation: tuple[int, int] | None = None) -> Graph:
    """Build one ordered token tier, optionally with one non-nesting link."""
    relations = (
        SimpleRelationDeclaration(TOKEN_MEMBERS, TOKENS, TOKEN_TYPE),
        BipartiteRelationDeclaration(LINK, TOKEN_TYPE, TOKEN_TYPE),
    )
    return Graph(
        (NamespaceDeclaration("d", NS),),
        (
            Tier(
                TierDeclaration(TOKENS, "Tokens"),
                tuple(Item(label) for label in labels),
            ),
        ),
        relations,
        ()
        if relation is None
        else (
            RelationInstance(
                LINK, ItemRef(TOKENS, relation[0]), ItemRef(TOKENS, relation[1])
            ),
        ),
    )


def cyclic_relation_graph() -> Graph:
    """Build a valid schema whose relation declarations form one component."""
    namespace = "urn:test:distance:cycle"
    tier = QualifiedName(namespace, "units")
    first = QualifiedName(namespace, "first")
    second = QualifiedName(namespace, "second")
    side = RelationSideDeclaration(
        (RelationEndpointKind.ITEM,), (tier,), allow_empty=True
    )
    return Graph(
        (NamespaceDeclaration("c", namespace),),
        (Tier(TierDeclaration(tier, "Units"), ()),),
        (
            PolyadicRelationDeclaration(first, side, side, targets_subset_of=second),
            PolyadicRelationDeclaration(second, side, side, targets_subset_of=first),
        ),
    )


def move_witness(source: Graph, target: Graph) -> ProjectionWitness:
    """Build one checked move witness through the public patch route."""
    return ProjectionWitness.from_patch(
        source, diff(source, target, EquivalenceView.IDENTIFIED)
    )


def edit_witness(
    source: Graph, operation: Callable[[JournalEditor], object]
) -> ProjectionWitness:
    """Record one real editor primitive as a projection witness."""
    journal = Journal()
    editor = source.edit(journal=journal)
    operation(editor)
    editor.freeze()
    return ProjectionWitness.from_patch(source, journal.to_patch())


def primitive_witnesses() -> tuple[ProjectionWitness, ...]:
    """Exercise every costed primitive on small validated graphs."""
    case = primitive_fixture("text")
    base = case.graph
    layer = LayerName(case.namespace, "distance")
    fact = LayerFact(
        ItemRef(case.unit, 0),
        AttributeValue(case.note, XsdType.STRING, "observed"),
    )
    layered = base.add_layer(layer)
    with_fact = layered.put_fact(layer, fact)
    inserted_relation = RelationInstance(
        case.link, ItemRef(case.unit, 1), ItemRef(case.unit, 2)
    )
    extra_tier = TierDeclaration(
        QualifiedName(case.namespace, "distance-extra"), "Distance extra"
    )
    declared = base.declare(extra_tier)
    promoted_item, _ = base.promote_item(ItemRef(case.spare, 0), "distance-item")
    promoted_boundary, boundary_id = base.promote_boundary(
        BoundaryRef(case.unit, 1), "u1"
    )
    promoted_relation, relation_id = base.promote_relation(
        RelationInstanceRef(0), "distance-relation"
    )
    sealed = base.seal(case.spare, 2)
    valued = base.set_attribute(
        ItemRef(case.unit, 0),
        AttributeValue(case.note, XsdType.STRING, "distance"),
    )
    replacement = Item(
        attributes=(AttributeValue(case.note, XsdType.STRING, "replacement"),)
    )
    return (
        edit_witness(base, lambda editor: editor.add_layer(layer)),
        edit_witness(layered, lambda editor: editor.remove_layer(layer)),
        edit_witness(base, lambda editor: editor.add_relation(inserted_relation)),
        edit_witness(
            base, lambda editor: editor.remove_relation(RelationInstanceRef(0))
        ),
        edit_witness(base, lambda editor: editor.declare(extra_tier)),
        edit_witness(declared, lambda editor: editor.undeclare(extra_tier)),
        edit_witness(base, lambda editor: editor.insert_item(case.spare, 1, Item())),
        edit_witness(base, lambda editor: editor.remove_item(ItemRef(case.spare, 0))),
        edit_witness(
            base,
            lambda editor: editor.promote_boundary(BoundaryRef(case.unit, 1), "u1"),
        ),
        edit_witness(
            promoted_boundary, lambda editor: editor.demote_boundary(boundary_id)
        ),
        edit_witness(
            base,
            lambda editor: editor.promote_item(ItemRef(case.spare, 0), "distance-item"),
        ),
        edit_witness(
            promoted_item,
            lambda editor: editor.demote_item(DurableItemRef("distance-item")),
        ),
        edit_witness(
            base,
            lambda editor: editor.promote_relation(
                RelationInstanceRef(0), "distance-relation"
            ),
        ),
        edit_witness(
            promoted_relation, lambda editor: editor.demote_relation(relation_id)
        ),
        edit_witness(layered, lambda editor: editor.put_fact(layer, fact)),
        edit_witness(
            with_fact,
            lambda editor: editor.remove_fact(layer, fact.subject, fact.value.name),
        ),
        edit_witness(base, lambda editor: editor.seal(case.spare, 1)),
        edit_witness(sealed, lambda editor: editor.drop_seal(case.spare)),
        edit_witness(
            base,
            lambda editor: editor.set_attribute(
                ItemRef(case.unit, 0),
                AttributeValue(case.note, XsdType.STRING, "distance"),
            ),
        ),
        edit_witness(
            valued,
            lambda editor: editor.remove_attribute(ItemRef(case.unit, 0), case.note),
        ),
        edit_witness(base, lambda editor: editor.move_item(ItemRef(case.spare, 0), 2)),
        edit_witness(
            base,
            lambda editor: editor.replace_item(ItemRef(case.spare, 0), replacement),
        ),
        edit_witness(
            base,
            lambda editor: editor.set_endpoints(
                RelationInstanceRef(0),
                ItemRef(case.unit, 1),
                ItemRef(case.unit, 2),
            ),
        ),
        edit_witness(
            base,
            lambda editor: editor.swap_items(
                ItemRef(case.spare, 0), ItemRef(case.spare, 2)
            ),
        ),
        edit_witness(sealed, lambda editor: editor.unseal(case.spare, 1)),
    )


def valued_graph(value: str) -> Graph:
    """Build one item carrying a scalar value for callback tests."""
    return replace(
        graph("a"),
        tiers=(
            Tier(
                TierDeclaration(TOKENS, "Tokens"),
                (Item("a", (AttributeValue(VALUE, XsdType.STRING, value),)),),
            ),
        ),
        attribute_declarations=(
            AttributeDeclaration(VALUE, AttributeDomain.ITEM, XsdType.STRING),
        ),
    )


def brute_sequence(source: str, target: str) -> Decimal:
    """Find unit Levenshtein distance by shortest-path search, independently."""
    alphabet = sorted(set(source + target))
    queue: list[tuple[int, str]] = [(0, source)]
    seen = {source: 0}
    while queue:
        cost, value = heapq.heappop(queue)
        if value == target:
            return Decimal(cost)
        if seen[value] != cost:
            continue
        neighbors = {value[:index] + value[index + 1 :] for index in range(len(value))}
        neighbors.update(
            value[:index] + character + value[index:]
            for index in range(len(value) + 1)
            for character in alphabet
            if len(value) < len(target) + len(source)
        )
        neighbors.update(
            value[:index] + character + value[index + 1 :]
            for index in range(len(value))
            for character in alphabet
        )
        for candidate in neighbors:
            next_cost = cost + 1
            if next_cost < seen.get(candidate, next_cost + 1):
                seen[candidate] = next_cost
                heapq.heappush(queue, (next_cost, candidate))
    raise AssertionError("finite edit graph was disconnected")


type TreeState = tuple[str, tuple[TreeState, ...]]
type ForestState = tuple[TreeState, ...]


def _tree_state(tree: OrderedTree) -> TreeState:
    """Convert the public tree to a compact brute-force state."""
    assert isinstance(tree.value, str)
    return tree.value, tuple(_tree_state(child) for child in tree.children)


def _node_count(forest: ForestState) -> int:
    """Count nodes in a brute-force ordered forest."""
    return sum(1 + _node_count(children) for _, children in forest)


def _tree_labels(forest: ForestState) -> set[str]:
    """Collect labels recursively from a brute-force forest."""
    result: set[str] = set()
    for label, children in forest:
        result.add(label)
        result.update(_tree_labels(children))
    return result


def _forest_neighbors(
    forest: ForestState, labels: tuple[str, ...], maximum_nodes: int
) -> set[ForestState]:
    """Enumerate unit Zhang-Shasha edits at every forest position."""
    result: set[ForestState] = set()
    for index, (label, children) in enumerate(forest):
        result.add((*forest[:index], *children, *forest[index + 1 :]))
        for replacement in labels:
            if replacement != label:
                result.add(
                    (
                        *forest[:index],
                        (replacement, children),
                        *forest[index + 1 :],
                    )
                )
        for changed_children in _forest_neighbors(children, labels, maximum_nodes):
            result.add(
                (*forest[:index], (label, changed_children), *forest[index + 1 :])
            )
    if _node_count(forest) < maximum_nodes:
        for start in range(len(forest) + 1):
            for end in range(start, len(forest) + 1):
                for label in labels:
                    result.add(
                        (
                            *forest[:start],
                            (label, forest[start:end]),
                            *forest[end:],
                        )
                    )
    return {
        candidate for candidate in result if _node_count(candidate) <= maximum_nodes
    }


def brute_tree(source: OrderedTree, target: OrderedTree) -> Decimal:
    """Find unit ordered-tree distance by exhaustive forest editing."""
    before: ForestState = (_tree_state(source),)
    after: ForestState = (_tree_state(target),)
    labels = tuple(sorted(_tree_labels(before) | _tree_labels(after)))
    maximum_nodes = max(_node_count(before), _node_count(after))
    queue: deque[tuple[ForestState, int]] = deque([(before, 0)])
    seen: set[ForestState] = {before}
    while queue:
        forest, cost = queue.popleft()
        if forest == after:
            return Decimal(cost)
        for candidate in _forest_neighbors(forest, labels, maximum_nodes):
            if candidate not in seen:
                seen.add(candidate)
                queue.append((candidate, cost + 1))
    raise AssertionError("finite tree-edit graph was disconnected")


def _segmentation_boundaries(widths: tuple[int, ...]) -> frozenset[int]:
    """Return the internal boundary set for one segmentation."""
    position = 0
    result: set[int] = set()
    for width in widths[:-1]:
        position += width
        result.add(position)
    return frozenset(result)


def brute_segmentation(source: tuple[int, ...], target: tuple[int, ...]) -> Decimal:
    """Find unit split, merge, and displacement distance by breadth-first search."""
    total = sum(source)
    before = _segmentation_boundaries(source)
    after = _segmentation_boundaries(target)
    positions = frozenset(range(1, total))
    queue = deque([(before, 0)])
    seen = {before}
    while queue:
        boundaries, cost = queue.popleft()
        if boundaries == after:
            return Decimal(cost)
        neighbors = {boundaries - {removed} for removed in boundaries} | {
            boundaries | {added} for added in positions - boundaries
        }
        neighbors.update(
            (boundaries - {removed}) | {added}
            for removed in boundaries
            for added in positions - boundaries
        )
        for candidate in neighbors:
            if candidate not in seen:
                seen.add(candidate)
                queue.append((candidate, cost + 1))
    raise AssertionError("finite segmentation-edit graph was disconnected")


@pytest.mark.parametrize(
    ("source", "target"),
    (("", ""), ("a", ""), ("", "ab"), ("ab", "ba"), ("abc", "adc")),
)
def test_weighted_sequence_agrees_with_brute_force(source: str, target: str) -> None:
    """The exact sequence engine agrees with independent shortest paths."""
    assert weighted_sequence_distance(source, target) == brute_sequence(source, target)


def test_weighted_sequence_uses_value_sensitive_costs() -> None:
    """Unary and binary callbacks participate in the dynamic program."""
    assert (
        weighted_sequence_distance(
            (1, 2),
            (1, 3, 4),
            insert=lambda value: value,
            delete=lambda value: value * 2,
            substitute=lambda before, after: abs(before - after),
        )
        == 5
    )
    with pytest.raises(ValueError, match="finite nonnegative"):
        weighted_sequence_distance((1,), (), delete=float("inf"))
    with pytest.raises(TypeError, match="boolean"):
        weighted_sequence_distance((), (1,), insert=True)


def test_ordered_tree_exact_cases_and_metric_axioms() -> None:
    """Ordered-tree distance handles promotion and satisfies finite metric axioms."""
    trees = (
        OrderedTree("a"),
        OrderedTree("a", (OrderedTree("b"),)),
        OrderedTree("a", (OrderedTree("c"),)),
    )
    assert ordered_tree_distance(trees[1], OrderedTree("b")) == 1
    for first in trees:
        assert ordered_tree_distance(first, first) == 0
        for second in trees:
            assert ordered_tree_distance(first, second) == ordered_tree_distance(
                second, first
            )
            for third in trees:
                assert ordered_tree_distance(first, third) <= (
                    ordered_tree_distance(first, second)
                    + ordered_tree_distance(second, third)
                )


def test_ordered_tree_agrees_with_brute_force() -> None:
    """The exact tree engine agrees with independently enumerated edit scripts."""
    trees = (
        OrderedTree("a"),
        OrderedTree("a", (OrderedTree("b"),)),
        OrderedTree("b", (OrderedTree("a"),)),
        OrderedTree("a", (OrderedTree("a"), OrderedTree("b"))),
    )
    for source in trees:
        for target in trees:
            assert ordered_tree_distance(source, target) == brute_tree(source, target)


def test_ordered_tree_uses_weighted_operations() -> None:
    """The tree engine accepts the same declared callable cost forms."""
    source = OrderedTree("root", (OrderedTree("old"),))
    target = OrderedTree("root", (OrderedTree("new"), OrderedTree("tail")))
    assert (
        ordered_tree_distance(
            source,
            target,
            insert=lambda value: 2 if value == "tail" else 1,
            delete=3,
            substitute=lambda before, after: 4,
        )
        == 5
    )


def test_contiguous_segmentation_prices_boundaries() -> None:
    """Segmentation distance chooses displacement or a merge-plus-split."""
    assert contiguous_segmentation_distance((2, 2), (1, 3)) == 1
    assert contiguous_segmentation_distance((2, 1, 1), (1, 1, 2)) == 1
    assert contiguous_segmentation_distance((2, 2), (1, 3), displace=3) == 2
    table = CostTable(
        boundary_displacement=lambda before, after: abs(before - after) / 2
    )
    assert contiguous_segmentation_distance(
        (2, 2), (1, 3), displace=table.displace_boundary
    ) == Decimal("0.5")
    assert contiguous_segmentation_distance((1, 1, 2), (2, 2), merge=2) == 2
    with pytest.raises(ValueError, match="positive integers"):
        contiguous_segmentation_distance((0,), (0,))
    with pytest.raises(ValueError, match="same number"):
        contiguous_segmentation_distance((2,), (3,))


def test_contiguous_segmentation_agrees_with_brute_force() -> None:
    """The exact segmentation engine agrees with independent boundary search."""
    segmentations = ((4,), (1, 3), (2, 2), (1, 1, 2), (1, 1, 1, 1))
    for source in segmentations:
        for target in segmentations:
            assert contiguous_segmentation_distance(
                source, target
            ) == brute_segmentation(source, target)


def test_cost_table_validates_symmetry_and_round_trips_data() -> None:
    """Numeric costs detach from callers and retain declaration overrides."""
    operations = dict(UNIT_COSTS.operations)
    operations["move_item"] = 3
    table = CostTable(operations, {TOKENS: {"insert_item": 4, "remove_item": 4}})
    encoded = table.to_data()
    decoded = CostTable.from_data(json.loads(json.dumps(encoded)))
    assert decoded.operation("move_item") == 3
    assert decoded.operation("insert_item", TOKENS) == 4
    assert decoded.metric_violations() == ()
    with pytest.raises(ValueError, match="must match"):
        CostTable({"insert_item": 1, "remove_item": 2})
    with pytest.raises(TypeError, match="qualified names"):
        CostTable(UNIT_COSTS.operations, {"tokens": {}})  # type: ignore[dict-item]
    with pytest.raises(TypeError, match="nonempty strings"):
        CostTable({"": 1})
    with pytest.raises(ValueError, match="unknown graph operation"):
        CostTable({**UNIT_COSTS.operations, "insert_items": 0})
    with pytest.raises(ValueError, match="unknown graph operation"):
        CostTable(UNIT_COSTS.operations, {TOKENS: {"insert_items": 0}})


def test_cost_table_callbacks_and_zero_metric_notice() -> None:
    """Value and boundary callbacks are checked at their public boundary."""
    before = AttributeValue(VALUE, XsdType.STRING, "before")
    after = AttributeValue(VALUE, XsdType.STRING, "after")
    table = CostTable(
        {**UNIT_COSTS.operations, "replace_item": 0},
        value_substitution=lambda left, right: 0,
        boundary_displacement=lambda left, right: abs(right - left) / 2,
    )
    assert table.substitute_value(before, after) == 0
    assert UNIT_COSTS.substitute_value(before, before) == 0
    assert UNIT_COSTS.substitute_value(before, after) == 1
    assert table.displace_boundary(2, 5) == Decimal("1.5")
    assert UNIT_COSTS.displace_boundary(2, 2) == 0
    assert UNIT_COSTS.displace_boundary(2, 3) == 1
    assert table.metric_violations() == ("replace_item",)
    zero_override = CostTable(
        UNIT_COSTS.operations,
        {TOKENS: {"insert_item": 0, "remove_item": 0}},
    )
    assert zero_override.metric_violations() == (
        f"{TOKENS}:insert_item",
        f"{TOKENS}:remove_item",
    )
    with pytest.raises(ValueError, match="same name"):
        table.substitute_value(
            before, AttributeValue(QualifiedName(NS, "other"), XsdType.STRING, "x")
        )
    with pytest.raises(ValueError, match="finite nonnegative"):
        CostTable(value_substitution=lambda left, right: -1).substitute_value(
            before, after
        )
    with pytest.raises(ValueError, match="finite nonnegative"):
        CostTable({"probe": "not-a-number"})
    with pytest.raises(ValueError, match="no cost"):
        UNIT_COSTS.operation("not-an-operation")
    with pytest.raises(TypeError, match="operation names"):
        CostTable(UNIT_COSTS.operations, {TOKENS: {1: 2}})  # type: ignore[dict-item]


@pytest.mark.parametrize(
    "value",
    (
        [],
        {"unknown": 1},
        {"operations": []},
        {"declarations": {}},
        {"declarations": [{}]},
        {"declarations": [{"declaration": [], "operations": {}}]},
        {
            "declarations": [
                {
                    "declaration": {"namespace": NS, "local_name": "x"},
                    "operations": [],
                }
            ]
        },
        {"operations": {"move_item": True}},
    ),
)
def test_cost_table_data_refuses_bad_shapes(value: object) -> None:
    """Cost-table data failures remain staged refusals."""
    with pytest.raises(Refusal):
        CostTable.from_data(value)


def test_cost_table_data_refuses_duplicate_declarations() -> None:
    """A declaration has only one override entry."""
    entry = {
        "declaration": TOKENS.to_data(),
        "operations": {"insert_item": 2},
    }
    with pytest.raises(Refusal, match="more than once"):
        CostTable.from_data({"declarations": [entry, entry]})


def _labels(value: Graph) -> tuple[object, ...]:
    """Project durable labels from the first tier."""
    return tuple(item.durable_id for item in value.tiers[0].items)


def test_projection_admissibility_catches_a_cheap_move() -> None:
    """A Levenshtein projection cannot lower-bound a cheaper arbitrary move."""
    source = graph("a", "b", "c")
    target = source.move_item(ItemRef(TOKENS, 0), 2)
    costs = CostTable({**UNIT_COSTS.operations, "move_item": 1})
    projection = SequenceProjection("labels", _labels)
    check = check_projection_admissibility(
        projection,
        costs,
        (move_witness(source, target),),
        required=("move_item", "replace_item"),
    )
    assert not check.admissible
    assert check.missing == frozenset({"replace_item"})
    assert check.violations[0].projected == 2
    with pytest.raises(ValueError, match="missing witnesses"):
        check.certify()

    complete = check_projection_admissibility(
        projection,
        CostTable({**UNIT_COSTS.operations, "move_item": 2}),
        (move_witness(source, target),),
        required=("move_item",),
    )
    assert complete.admissible
    assert complete.certify().operations == frozenset({"move_item"})

    other = QualifiedName(NS, "other-tier")
    scoped = CostTable(
        {**UNIT_COSTS.operations, "move_item": 2},
        {other: {"move_item": 1}},
    )
    scoped_check = check_projection_admissibility(
        projection,
        scoped,
        (move_witness(source, target),),
        required=("move_item",),
    )
    assert scoped_check.violations[0].allowed == 1


def test_projection_violation_refuses_certification() -> None:
    """Complete witness coverage still refuses when the cost inequality fails."""
    source = graph("a", "b", "c")
    target = source.move_item(ItemRef(TOKENS, 0), 2)
    check = check_projection_admissibility(
        SequenceProjection("labels", _labels),
        CostTable({**UNIT_COSTS.operations, "move_item": 1}),
        (move_witness(source, target),),
        required=("move_item",),
    )
    with pytest.raises(ValueError, match="overprices"):
        check.certify()
    with pytest.raises(ValueError, match="nonempty"):
        SequenceProjection("", _labels)
    with pytest.raises(TypeError, match="base must be"):
        ProjectionWitness.from_patch(object(), diff(source, target, "identified"))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="must be a Patch"):
        ProjectionWitness.from_patch(source, object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="one operation"):
        ProjectionWitness.from_patch(source, diff(source, source, "identified"))
    other_name = QualifiedName("urn:test:distance:other", "items")
    incompatible = Graph(
        (NamespaceDeclaration("o", other_name.namespace),),
        (Tier(TierDeclaration(other_name, "Items"), (Item("x"),)),),
        (),
    )
    with pytest.raises(ValueError, match="editing primitive"):
        ProjectionWitness.from_patch(source, diff(source, incompatible, "identified"))

    appended = AddItem(TOKENS, Item("tail")).apply(source)
    base_fingerprint = fingerprint(source, EquivalenceView.IDENTIFIED)
    target_fingerprint = fingerprint(appended, EquivalenceView.IDENTIFIED)
    patch = Patch(
        base_fingerprint,
        target_fingerprint,
        (
            PatchOperation(
                AddItem(TOKENS, Item("tail")),
                AddItem(TOKENS, Item("tail")),
                base_fingerprint,
                target_fingerprint,
            ),
        ),
    )
    assert ProjectionWitness.from_patch(source, patch).operation == "insert_item"
    with pytest.raises(TypeError, match="come from"):
        AdmissibleProjection(
            SequenceProjection("labels", _labels), frozenset(), UNIT_COSTS
        )


def _pieces(value: Graph) -> tuple[str, ...]:
    """Return item labels as text pieces."""
    return tuple(item.durable_id or "" for item in value.tiers[0].items)


def test_text_projections_leave_joining_policy_to_the_caller() -> None:
    """Strict, presentation, whitespace, and Cf views have explicit transforms."""
    source = graph("a", " b", "\u200cc")
    strict = text_projection("strict", _pieces, join=lambda pieces: "|".join(pieces))
    presentation = text_projection(
        "presentation",
        _pieces,
        join=lambda pieces: "".join(pieces),
        transform=str.upper,
    )
    whitespace = whitespace_insensitive_projection(
        _pieces, join=lambda pieces: "".join(pieces)
    )
    controls = format_control_insensitive_projection(
        _pieces, join=lambda pieces: "".join(pieces)
    )
    assert strict.project(source) == tuple("a| b|\u200cc")
    assert presentation.project(source) == tuple("A B\u200cC")
    assert whitespace.project(source) == tuple("ab\u200cc")
    assert controls.project(source) == tuple("a bc")


def test_independent_graph_distance_is_exact_and_metric() -> None:
    """Independent ordered tiers get exact values and satisfy metric axioms."""
    graphs = (graph("a"), graph("a", "b"), graph("b", "a"))
    for first in graphs:
        assert distance(first, first, view="identified").value == 0
        for second in graphs:
            forward = distance(first, second, view="identified")
            backward = distance(second, first, view="identified")
            assert forward.exact
            assert forward.value == backward.value
            for third in graphs:
                assert distance(first, third, view="identified").lower <= (
                    forward.lower + distance(second, third, view="identified").lower
                )
    assert distance(graph("a", "b"), graph("b", "a"), view="identified").value == 2


def test_value_callback_participates_in_exact_graph_distance() -> None:
    """An exact tier substitution uses the declared value-distance callback."""
    costs = CostTable(
        UNIT_COSTS.operations,
        value_substitution=lambda before, after: Decimal("0.25"),
    )
    assert distance(valued_graph("old"), valued_graph("new"), costs).value == Decimal(
        "0.25"
    )


def test_declaration_costs_participate_in_exact_graph_distance() -> None:
    """Each independent tier uses its own declared operation overrides."""
    costs = CostTable(
        UNIT_COSTS.operations,
        {
            TOKENS: {
                "insert_item": 4,
                "remove_item": 4,
                "move_item": 8,
                "swap_items": 8,
            }
        },
    )
    assert distance(graph("a"), graph("a", "b"), costs).value == 4


def test_exact_item_updates_cover_identity_and_attribute_shapes() -> None:
    """Identity promotion and each attribute-set difference receive a cost."""
    plain = valued_graph("same")
    no_value = replace(
        plain,
        tiers=(replace(plain.tiers[0], items=(Item("a"),)),),
    )
    no_id = replace(
        plain,
        tiers=(
            replace(
                plain.tiers[0],
                items=(replace(plain.tiers[0].items[0], durable_id=None),),
            ),
        ),
    )
    assert distance(plain, no_value, view="identified").value == 1
    assert distance(no_value, plain, view="identified").value == 1
    assert distance(plain, no_id, view="identified").value == 1
    assert distance(no_id, plain, view="identified").value == 1
    assert (
        _item_substitution(
            Item("left"), Item("right"), EquivalenceView.FUNCTIONAL, UNIT_COSTS
        )
        == 0
    )


def test_patch_pricing_handles_primitive_and_delta_call_shapes() -> None:
    """Realized scripts price construction opcodes and named edit deltas."""
    primitive = Patch(
        "base",
        "target",
        (
            PatchOperation(
                AddItem(TOKENS, Item("x")),
                AddItem(TOKENS, Item("x")),
                "base",
                "target",
            ),
        ),
    )
    assert price_patch(primitive, UNIT_COSTS) == 1
    named = Patch(
        "base",
        "target",
        (
            PatchOperation(
                DeltaOpcode("move_item", (), ()),
                DeltaOpcode("move_item", (), ()),
                "base",
                "target",
            ),
        ),
    )
    with pytest.raises(ValueError, match="one call and no residue"):
        price_patch(named, UNIT_COSTS)
    moved_source = graph("a", "b", "c")
    moved_target = moved_source.move_item(ItemRef(TOKENS, 0), 2)
    assert (
        price_patch(
            diff(moved_source, moved_target, "identified"), UNIT_COSTS, moved_source
        )
        == 2
    )
    changed = diff(graph("a"), graph("a", "b"), "identified")
    assert price_patch(changed, UNIT_COSTS) == 1

    linked = graph("a", "b", "c", relation=(0, 1))
    relinked = graph("a", "b", "c", relation=(0, 2))
    relation_costs = CostTable(
        UNIT_COSTS.operations,
        {LINK: {"add_relation": 4, "remove_relation": 4}},
    )
    relation_patch = diff(linked, relinked, "identified")
    assert price_patch(relation_patch, relation_costs, linked) == 8
    assert distance(linked, relinked, relation_costs, view="identified").upper == 8


def test_patch_pricing_refuses_multi_item_delta_calls() -> None:
    """A multi-item native call cannot contribute one primitive cost."""
    with pytest.raises(ValueError, match="multi-item native operation"):
        _delta_primitive_kind(
            DeltaOpcode("insert_items", (_EditCall("insert_items", ()),), ())
        )


def test_operation_declarations_follow_each_edit_call_shape() -> None:
    """Declaration overrides follow values, items, boundaries, and relations."""

    def opcode(
        method: str, arguments: tuple[object, ...], operation: str | None = None
    ) -> DeltaOpcode:
        return DeltaOpcode(
            method if operation is None else operation,
            (_EditCall(method, arguments),),
            (),
        )

    case = primitive_fixture("text")
    rich = replace(
        case.graph,
        relations=(replace(case.graph.relations[0], durable_id="binary-id"),),
        polyadic_relations=(
            replace(case.graph.polyadic_relations[0], durable_id="other-polyadic-id"),
            replace(case.graph.polyadic_relations[0], durable_id="polyadic-id"),
        ),
    )
    assert _relation_declaration(rich, 0) == case.link
    assert _relation_declaration(rich, RelationInstanceRef(0)) == case.link
    assert _relation_declaration(rich, PolyadicInstanceRef(0)) == case.group
    assert _relation_declaration(rich, "binary-id") == case.link
    assert _relation_declaration(rich, DurableRelationRef("binary-id")) == case.link
    assert _relation_declaration(rich, DurablePolyadicRef("polyadic-id")) == case.group
    assert _relation_declaration(rich, DurablePolyadicRef("unknown")) is None
    assert _relation_declaration(rich, object()) is None

    value = AttributeValue(case.note, XsdType.STRING, "new")
    fact = LayerFact(ItemRef(case.unit, 0), value)
    relation = rich.relations[0]
    assert _operation_declaration(DeltaOpcode("move_item", (), ())) is None
    assert _operation_declaration(opcode("move_item", ())) is None
    assert (
        _operation_declaration(
            opcode("set_attribute", (ItemRef(case.unit, 0), value)), rich
        )
        == case.note
    )
    assert (
        _operation_declaration(
            opcode("remove_attribute", (ItemRef(case.unit, 0), case.note)), rich
        )
        == case.note
    )
    assert (
        _operation_declaration(opcode("put_fact", (LayerName(NS, "x"), fact)), rich)
        == case.note
    )
    assert (
        _operation_declaration(
            opcode("remove_fact", (LayerName(NS, "x"), fact.subject, case.note)),
            rich,
        )
        == case.note
    )
    assert _operation_declaration(opcode("put_fact", (None, object())), rich) is None
    assert (
        _operation_declaration(opcode("add_relation", (relation,)), rich) == case.link
    )
    assert _operation_declaration(opcode("add_relation", (object(),)), rich) is None
    assert _operation_declaration(opcode("remove_relation", (0,))) is None
    assert _operation_declaration(opcode("remove_relation", (0,)), rich) == case.link
    assert (
        _operation_declaration(opcode("insert_item", (case.unit, 0, Item())), rich)
        == case.unit
    )
    assert (
        _operation_declaration(opcode("insert_item", (object(), 0, Item())), rich)
        is None
    )
    assert (
        _operation_declaration(opcode("move_item", (ItemRef(case.unit, 0), 1)), rich)
        == case.unit
    )
    assert (
        _operation_declaration(opcode("move_item", (DurableItemRef("u0"), 1)), rich)
        == case.unit
    )
    assert (
        _operation_declaration(opcode("move_item", (DurableItemRef("u0"), 1))) is None
    )
    assert _operation_declaration(opcode("move_item", (object(), 1)), rich) is None
    assert (
        _operation_declaration(
            opcode("promote_boundary", (BoundaryRef(case.unit, 1), "edge")), rich
        )
        == case.unit
    )
    leading = DurableBoundaryRef(case.unit, BoundarySide.BEFORE)
    assert (
        _operation_declaration(opcode("demote_boundary", (leading,)), rich) == case.unit
    )
    assert _operation_declaration(opcode("demote_boundary", (leading,))) is None
    assert (
        _operation_declaration(opcode("promote_boundary", (object(), "edge")), rich)
        is None
    )
    assert _operation_declaration(opcode("seal", (case.unit, 1)), rich) == case.unit
    assert (
        _operation_declaration(opcode("declare", (TierDeclaration(case.unit, "x"),)))
        == case.unit
    )
    assert (
        _operation_declaration(opcode("seal", (GraphCarrier.RELATIONS, 1)), rich)
        is None
    )


def test_general_graph_distance_returns_realized_interval() -> None:
    """A changed non-nesting relation gets bounds rather than false exactness."""
    source = graph("a", "b", "c", relation=(0, 1))
    target = graph("a", "b", "c", relation=(0, 2))
    result = distance(source, target)
    assert result.lower == 0
    assert result.lower <= result.upper
    assert not result.exact
    assert result.method in {"projected-diff", "projected-rebuild"}


def test_incompatible_graph_distance_prices_a_rebuild() -> None:
    """A residual diff delta selects the dependency-ordered rebuild bound."""
    source = graph("a")
    other_name = QualifiedName("urn:test:distance:other", "units")
    target = Graph(
        (NamespaceDeclaration("o", other_name.namespace),),
        (Tier(TierDeclaration(other_name, "Units"), (Item("x"),)),),
        (),
    )
    result = distance(source, target, view="identified")
    assert result.method == "projected-rebuild"
    assert 0 <= result.lower <= result.upper


def test_rebuild_realizes_mutually_dependent_relation_declarations() -> None:
    """An atomic declaration component still has a replayed primitive price."""
    cyclic = cyclic_relation_graph()
    forward = distance(graph("a"), cyclic, view="identified")
    backward = distance(cyclic, graph("a"), view="identified")
    assert forward.method == backward.method == "projected-rebuild"
    assert forward.upper == backward.upper
    with pytest.raises(GraphValidationError, match="could not undeclare"):
        _undeclare_all(cyclic, cyclic.relation_declarations, UNIT_COSTS)


def test_rebuild_bound_prices_each_carrier_kind() -> None:
    """The conservative rebuild includes facts, links, values, seals, and declarations."""
    case = primitive_fixture("text")
    document_value = QualifiedName(case.namespace, "document-value")
    tier_value = QualifiedName(case.namespace, "tier-value")
    declaration_value = QualifiedName(case.namespace, "declaration-value")
    rich = replace(
        case.graph,
        tiers=(
            replace(
                case.graph.tiers[0],
                attributes=(AttributeValue(tier_value, XsdType.STRING, "tier"),),
            ),
            *case.graph.tiers[1:],
        ),
        relation_declarations=(
            replace(
                case.graph.relation_declarations[0],
                attributes=(
                    AttributeValue(declaration_value, XsdType.STRING, "declaration"),
                ),
            ),
            *case.graph.relation_declarations[1:],
        ),
        attribute_declarations=(
            *case.graph.attribute_declarations,
            AttributeDeclaration(
                document_value, AttributeDomain.DOCUMENT, XsdType.STRING
            ),
            AttributeDeclaration(tier_value, AttributeDomain.TIER, XsdType.STRING),
            AttributeDeclaration(
                declaration_value,
                AttributeDomain.RELATION_DECLARATION,
                XsdType.STRING,
            ),
        ),
        attributes=(AttributeValue(document_value, XsdType.STRING, "document"),),
        layers=(
            Layer(
                LayerName(case.namespace, "hand"),
                (
                    LayerFact(
                        ItemRef(case.spare, 0),
                        AttributeValue(case.note, XsdType.STRING, "observed"),
                    ),
                ),
            ),
        ),
        seals=(Seal(case.unit, 1), Seal(GraphCarrier.RELATIONS, 1)),
    )
    assert _rebuild_upper_bound(rich, rich, UNIT_COSTS) > 0


def test_nonsequence_shapes_and_cheap_swap_select_bounds() -> None:
    """Tier-count changes and an underpriced swap do not receive sequence exactness."""
    source = graph("a", "b")
    extra_name = QualifiedName(NS, "extra")
    extra = replace(
        source,
        tiers=(
            *source.tiers,
            Tier(TierDeclaration(extra_name, "Extra"), (Item("x"),)),
        ),
    )
    assert distance(source, extra, view="identified").method.startswith("projected-")
    costs = CostTable({**UNIT_COSTS.operations, "swap_items": 1})
    swapped = distance(source, graph("b", "a"), costs, view="identified")
    assert not swapped.exact


def test_an_invalid_projection_bound_is_refused() -> None:
    """The final interval guard catches inconsistent certified input."""
    costs = CostTable({**UNIT_COSTS.operations, "move_item": 1})

    def project(value: Graph) -> tuple[object, ...]:
        if not value.relations or value.tiers[0].declaration.name.namespace != NS:
            return ()
        endpoint = value.relations[0].right
        assert isinstance(endpoint, ItemRef)
        return tuple(range(endpoint.index * 10))

    certificate = check_projection_admissibility(
        SequenceProjection("conditional", project),
        costs,
        primitive_witnesses(),
    ).certify()
    with pytest.raises(ValueError, match="exceeds"):
        distance(
            graph("a", "b", "c", relation=(0, 1)),
            graph("a", "b", "c", relation=(0, 2)),
            costs,
            projections=(certificate,),
        )


def test_move_interval_accepts_an_admissible_projection() -> None:
    """Certified projection bounds remain below a cheap realized move."""
    source = graph("a", "b", "c")
    target = source.move_item(ItemRef(TOKENS, 0), 2)
    costs = CostTable({**UNIT_COSTS.operations, "move_item": 1})
    projection = SequenceProjection("constant", lambda value: ())
    certificate = check_projection_admissibility(
        projection,
        costs,
        (move_witness(source, target),),
        required=("move_item",),
    ).certify()
    with pytest.raises(ValueError, match="lacks primitive coverage"):
        distance(
            source,
            target,
            costs,
            view="identified",
            projections=(certificate,),
        )
    with pytest.raises(TypeError, match="come from"):
        replace(certificate, operations=PRIMITIVE_KINDS)
    certificate = check_projection_admissibility(
        projection,
        costs,
        primitive_witnesses(),
    ).certify()
    result = distance(
        source,
        target,
        costs,
        view="identified",
        projections=(certificate,),
    )
    assert result.to_data() == {
        "exact": False,
        "lower": 0,
        "upper": 1,
        "method": "projected-diff",
    }
    with pytest.raises(ValueError, match="different cost table"):
        distance(
            source,
            target,
            CostTable({**UNIT_COSTS.operations, "move_item": 1}),
            view="identified",
            projections=(certificate,),
        )


def test_distance_interval_checks_its_claim() -> None:
    """Interval construction refuses reversed bounds and false exactness."""
    with pytest.raises(ValueError, match="must not exceed"):
        DistanceInterval(Decimal(2), Decimal(1), False, "probe")
    with pytest.raises(ValueError, match="exact exactly"):
        DistanceInterval(Decimal(1), Decimal(1), False, "probe")
    with pytest.raises(ValueError, match="method"):
        DistanceInterval(Decimal(1), Decimal(1), True, "")


def test_distance_checks_endpoint_types() -> None:
    """The graph entry point refuses values outside its endpoint domain."""
    with pytest.raises(TypeError, match="Graph values"):
        distance(graph("a"), object())  # type: ignore[arg-type]


def test_distance_command_writes_exact_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The command reads two graphs and emits the public result shape."""
    source = tmp_path / "source.json"
    target = tmp_path / "target.json"
    source.write_bytes(dump_bytes(graph("a")))
    target.write_bytes(dump_bytes(graph("a", "b")))
    assert main(["distance", str(source), str(target), "--view", "identified"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "exact": True,
        "method": "ordered-tiers",
        "value": 1,
    }


def test_distance_command_reads_a_cost_table(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A numeric cost file controls command-line distance without callbacks."""
    source = tmp_path / "source.json"
    target = tmp_path / "target.json"
    costs = tmp_path / "costs.json"
    source.write_bytes(dump_bytes(graph("a")))
    target.write_bytes(dump_bytes(graph("a", "b")))
    data = UNIT_COSTS.to_data()
    assert isinstance(data["operations"], dict)
    data["operations"]["insert_item"] = 3
    data["operations"]["remove_item"] = 3
    data["operations"]["move_item"] = 6
    data["operations"]["swap_items"] = 6
    costs.write_text(json.dumps(data), encoding="utf-8")
    assert (
        main(
            [
                "distance",
                str(source),
                str(target),
                "--view",
                "identified",
                "--costs",
                str(costs),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["value"] == 3
