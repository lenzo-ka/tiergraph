"""Exact distance engines, declared costs, and graph bounds."""

from __future__ import annotations

import heapq
import json
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.strategies import DataObject, DrawFn

from tests.generated_graphs import GENERATED_HIERARCHIES, GeneratedHierarchy
from tests.test_edit_primitives import fixture as primitive_fixture
from tiergraph import (
    PRIMITIVE_KINDS,
    UNIT_COSTS,
    AddItem,
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    Boundary,
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
    ItemRun,
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
    contiguous_segmentation_distance,
    diff,
    distance,
    dump_bytes,
    format_control_insensitive_projection,
    ordered_tree_distance,
    price_patch,
    text_projection,
    weighted_sequence_distance,
    whitespace_insensitive_projection,
)
from tiergraph.cli import main
from tiergraph.distance import (
    _INVERSE_KINDS,
    _Atom,
    _atom_deletion,
    _atom_insertion,
    _atom_kind_distance,
    _atom_multiset_lower_bound,
    _atom_substitution,
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


@dataclass(frozen=True)
class PrimitiveCase:
    """Hold one realized primitive and the scope that prices it."""

    kind: str
    before: Graph
    after: Graph
    declaration: QualifiedName | None


def edit_case(
    source: Graph, operation: Callable[[JournalEditor], object]
) -> PrimitiveCase:
    """Record one real editor primitive and its resulting graph."""
    journal = Journal()
    editor = source.edit(journal=journal)
    operation(editor)
    after = editor.freeze()
    patch = journal.to_patch()
    assert len(patch.operations) == 1
    opcode = patch.operations[0].opcode
    assert isinstance(opcode, DeltaOpcode)
    return PrimitiveCase(
        _delta_primitive_kind(opcode),
        source,
        after,
        _operation_declaration(opcode, source),
    )


def primitive_cases() -> tuple[PrimitiveCase, ...]:
    """Exercise every costed primitive on validated graphs."""
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
        edit_case(base, lambda editor: editor.add_layer(layer)),
        edit_case(layered, lambda editor: editor.remove_layer(layer)),
        edit_case(base, lambda editor: editor.add_relation(inserted_relation)),
        edit_case(base, lambda editor: editor.remove_relation(RelationInstanceRef(0))),
        edit_case(base, lambda editor: editor.declare(extra_tier)),
        edit_case(declared, lambda editor: editor.undeclare(extra_tier)),
        edit_case(base, lambda editor: editor.insert_item(case.spare, 1, Item())),
        edit_case(base, lambda editor: editor.remove_item(ItemRef(case.spare, 0))),
        edit_case(
            base,
            lambda editor: editor.promote_boundary(BoundaryRef(case.unit, 1), "u1"),
        ),
        edit_case(
            promoted_boundary, lambda editor: editor.demote_boundary(boundary_id)
        ),
        edit_case(
            base,
            lambda editor: editor.promote_item(ItemRef(case.spare, 0), "distance-item"),
        ),
        edit_case(
            promoted_item,
            lambda editor: editor.demote_item(DurableItemRef("distance-item")),
        ),
        edit_case(
            base,
            lambda editor: editor.promote_relation(
                RelationInstanceRef(0), "distance-relation"
            ),
        ),
        edit_case(
            promoted_relation, lambda editor: editor.demote_relation(relation_id)
        ),
        edit_case(layered, lambda editor: editor.put_fact(layer, fact)),
        edit_case(
            with_fact,
            lambda editor: editor.remove_fact(layer, fact.subject, fact.value.name),
        ),
        edit_case(base, lambda editor: editor.seal(case.spare, 1)),
        edit_case(sealed, lambda editor: editor.drop_seal(case.spare)),
        edit_case(
            base,
            lambda editor: editor.set_attribute(
                ItemRef(case.unit, 0),
                AttributeValue(case.note, XsdType.STRING, "distance"),
            ),
        ),
        edit_case(
            valued,
            lambda editor: editor.remove_attribute(ItemRef(case.unit, 0), case.note),
        ),
        edit_case(base, lambda editor: editor.move_item(ItemRef(case.spare, 0), 2)),
        edit_case(
            base,
            lambda editor: editor.replace_item(ItemRef(case.spare, 0), replacement),
        ),
        edit_case(
            base,
            lambda editor: editor.set_endpoints(
                RelationInstanceRef(0),
                ItemRef(case.unit, 1),
                ItemRef(case.unit, 2),
            ),
        ),
        edit_case(
            base,
            lambda editor: editor.swap_items(
                ItemRef(case.spare, 0), ItemRef(case.spare, 2)
            ),
        ),
        edit_case(sealed, lambda editor: editor.unseal(case.spare, 1)),
    )


PRIMITIVE_CASES = primitive_cases()


@st.composite
def cost_tables(draw: DrawFn) -> CostTable:
    """Generate complete nonnegative tables while preserving inverse symmetry."""
    operations: dict[str, int] = {}
    for operation, inverse in _INVERSE_KINDS.items():
        value = draw(st.integers(min_value=0, max_value=20))
        operations[operation] = value
        operations[inverse] = value
    for operation in PRIMITIVE_KINDS - operations.keys():
        operations[operation] = draw(st.integers(min_value=0, max_value=20))
    return CostTable(operations)


@st.composite
def scoped_cost_tables(
    draw: DrawFn, declarations: tuple[QualifiedName, ...]
) -> CostTable:
    """Generate complete global and declaration-specific symmetric costs."""
    global_costs = draw(cost_tables())
    chosen = draw(
        st.lists(
            st.sampled_from(declarations),
            min_size=1,
            max_size=len(declarations),
            unique=True,
        )
    )
    overrides: dict[QualifiedName, dict[str, int]] = {}
    for declaration in chosen:
        values: dict[str, int] = {}
        for operation, inverse in _INVERSE_KINDS.items():
            value = draw(st.integers(min_value=0, max_value=20))
            values[operation] = value
            values[inverse] = value
        for operation in PRIMITIVE_KINDS - values.keys():
            values[operation] = draw(st.integers(min_value=0, max_value=20))
        overrides[declaration] = values
    return CostTable(global_costs.operations, overrides)


def generated_reorder_cases(case: GeneratedHierarchy) -> tuple[PrimitiveCase, ...]:
    """Realize the generalized reorder primitives on one generated hierarchy."""
    graph = case.graph
    return (
        edit_case(
            graph,
            lambda editor: editor.move_run(ItemRun(case.word, 0, 2), 2),
        ),
        edit_case(
            graph,
            lambda editor: editor.shift(
                ItemRef(case.phrase, 0), 1, "right", case.phrase_words
            ),
        ),
        edit_case(
            graph,
            lambda editor: editor.swap_runs(
                ItemRun(case.word, 0, 1), ItemRun(case.word, 3, 1)
            ),
        ),
        edit_case(
            graph,
            lambda editor: editor.swap_items(
                ItemRef(case.word, 0), ItemRef(case.word, 3)
            ),
        ),
        edit_case(
            graph,
            lambda editor: editor.set_endpoints(
                PolyadicInstanceRef(len(graph.polyadic_relations) - 1),
                (ItemRef(case.segment, 1),),
                (ItemRef(case.segment, 6),),
            ),
        ),
    )


def generated_primitive_cases(
    case: GeneratedHierarchy,
) -> tuple[PrimitiveCase, ...]:
    """Realize every costed primitive on variants of a generated hierarchy."""
    base = case.graph
    layer = LayerName(case.utterance.namespace, "generated-distance")
    fact = LayerFact(
        DurableItemRef("word-0"),
        AttributeValue(case.label, XsdType.STRING, "observed"),
    )
    layered = base.add_layer(layer)
    with_fact = layered.put_fact(layer, fact)
    relation = RelationInstance(
        case.attachment,
        ItemRef(case.segment, 3),
        ItemRef(case.blob, 0),
    )
    related = base.add_relation(relation)
    extra_tier = TierDeclaration(
        QualifiedName(case.utterance.namespace, "generated-extra"),
        "Generated extra",
    )
    declared = base.declare(extra_tier)
    inserted = base.insert_item(
        case.segment,
        len(base._tiers_by_name[case.segment].items),
        Item(),
    )
    inserted_ref = ItemRef(case.segment, len(base._tiers_by_name[case.segment].items))
    promoted_item, _ = inserted.promote_item(inserted_ref, "generated-distance-item")
    promoted_boundary, boundary_id = base.promote_boundary(
        BoundaryRef(case.segment, 4),
        "segment-4",
    )
    promoted_relation, relation_id = related.promote_relation(
        RelationInstanceRef(len(base.relations)), "generated-distance-relation"
    )
    sealed = base.seal(case.word, 4)
    valued = base.set_attribute(
        ItemRef(case.word, 0),
        AttributeValue(case.label, XsdType.STRING, "distance"),
    )
    replacement = Item(
        "word-0",
        (AttributeValue(case.label, XsdType.STRING, "replacement"),),
    )
    return (
        edit_case(base, lambda editor: editor.add_layer(layer)),
        edit_case(layered, lambda editor: editor.remove_layer(layer)),
        edit_case(base, lambda editor: editor.add_relation(relation)),
        edit_case(
            related,
            lambda editor: editor.remove_relation(
                RelationInstanceRef(len(base.relations))
            ),
        ),
        edit_case(base, lambda editor: editor.declare(extra_tier)),
        edit_case(declared, lambda editor: editor.undeclare(extra_tier)),
        edit_case(
            base,
            lambda editor: editor.insert_item(
                case.segment,
                len(base._tiers_by_name[case.segment].items),
                Item(),
            ),
        ),
        edit_case(inserted, lambda editor: editor.remove_item(inserted_ref)),
        edit_case(
            base,
            lambda editor: editor.promote_boundary(
                BoundaryRef(case.segment, 4),
                "segment-4",
            ),
        ),
        edit_case(
            promoted_boundary, lambda editor: editor.demote_boundary(boundary_id)
        ),
        edit_case(
            inserted,
            lambda editor: editor.promote_item(inserted_ref, "generated-distance-item"),
        ),
        edit_case(
            promoted_item,
            lambda editor: editor.demote_item(
                DurableItemRef("generated-distance-item")
            ),
        ),
        edit_case(
            related,
            lambda editor: editor.promote_relation(
                RelationInstanceRef(len(base.relations)),
                "generated-distance-relation",
            ),
        ),
        edit_case(
            promoted_relation, lambda editor: editor.demote_relation(relation_id)
        ),
        edit_case(layered, lambda editor: editor.put_fact(layer, fact)),
        edit_case(
            with_fact,
            lambda editor: editor.remove_fact(layer, fact.subject, fact.value.name),
        ),
        edit_case(base, lambda editor: editor.seal(case.word, 4)),
        edit_case(sealed, lambda editor: editor.drop_seal(case.word)),
        edit_case(
            base,
            lambda editor: editor.set_attribute(
                ItemRef(case.word, 0),
                AttributeValue(case.label, XsdType.STRING, "distance"),
            ),
        ),
        edit_case(
            valued,
            lambda editor: editor.remove_attribute(ItemRef(case.word, 0), case.label),
        ),
        edit_case(base, lambda editor: editor.move_item(ItemRef(case.word, 0), 3)),
        edit_case(
            base,
            lambda editor: editor.replace_item(ItemRef(case.word, 0), replacement),
        ),
        edit_case(
            base,
            lambda editor: editor.set_endpoints(
                RelationInstanceRef(2),
                ItemRef(case.segment, 2),
                ItemRef(case.blob, 0),
            ),
        ),
        edit_case(
            base,
            lambda editor: editor.swap_items(
                ItemRef(case.word, 0), ItemRef(case.word, 3)
            ),
        ),
        edit_case(sealed, lambda editor: editor.unseal(case.word, 2)),
    )


def test_atom_bound_has_a_realized_case_for_every_primitive() -> None:
    """The local proof surface covers the complete public primitive vocabulary."""
    assert {case.kind for case in PRIMITIVE_CASES} == PRIMITIVE_KINDS


@pytest.mark.parametrize("case", PRIMITIVE_CASES, ids=lambda case: case.kind)
@settings(max_examples=20)
@given(costs=cost_tables())
def test_atom_bound_is_dominated_by_every_primitive(
    case: PrimitiveCase, costs: CostTable
) -> None:
    """Every realized primitive changes the atom relaxation by at most its cost."""
    bound = _atom_multiset_lower_bound(
        case.before, case.after, costs, EquivalenceView.EXACT
    )
    assert bound <= costs.operation(case.kind, case.declaration)


@settings(max_examples=20, deadline=None)
@given(case=GENERATED_HIERARCHIES, data=st.data())
def test_generated_graphs_exercise_primitive_and_path_atom_bounds(
    case: GeneratedHierarchy, data: DataObject
) -> None:
    """Generated shapes, scoped costs, and paths exercise both FR4 bounds."""
    declarations = tuple(
        {
            *(tier.declaration.name for tier in case.graph.tiers),
            *(item.name for item in case.graph.relation_declarations),
            *(item.name for item in case.graph.attribute_declarations),
        }
    )
    costs = data.draw(scoped_cost_tables(declarations), label="costs")
    primitives = generated_primitive_cases(case)
    assert {primitive.kind for primitive in primitives} == PRIMITIVE_KINDS
    for primitive in (*primitives, *generated_reorder_cases(case)):
        bound = _atom_multiset_lower_bound(
            primitive.before, primitive.after, costs, EquivalenceView.EXACT
        )
        assert bound <= costs.operation(primitive.kind, primitive.declaration)

        witness = primitive.after.set_attribute(
            DurableItemRef("segment-2"),
            AttributeValue(case.label, XsdType.STRING, f"witness-{primitive.kind}"),
        )
        witness_bound = _atom_multiset_lower_bound(
            primitive.before, witness, UNIT_COSTS, EquivalenceView.EXACT
        )
        assert witness_bound > 0
        assert witness_bound <= UNIT_COSTS.operation(
            primitive.kind, primitive.declaration
        ) + UNIT_COSTS.operation("set_attribute", case.label)

    operations: tuple[Callable[[JournalEditor], object], ...] = (
        lambda editor: editor.move_run(ItemRun(case.word, 0, 1), 3),
        lambda editor: editor.shift(
            ItemRef(case.phrase, 0), 1, "right", case.phrase_words
        ),
        lambda editor: editor.swap_runs(
            ItemRun(case.word, 0, 1), ItemRun(case.word, 3, 1)
        ),
        lambda editor: editor.set_attribute(
            ItemRef(case.segment, 2),
            AttributeValue(case.label, XsdType.STRING, "path"),
        ),
    )
    selected = data.draw(
        st.lists(
            st.sampled_from((0, 2, 3)),
            min_size=2,
            max_size=3,
            unique=True,
        ).map(lambda tail: (1, *tail)),
        label="path",
    )
    journal = Journal()
    editor = case.graph.edit(journal=journal)
    applied_shift = False
    for index in selected:
        if index == 1:
            operations[index](editor)
            applied_shift = True
        else:
            operations[index](editor)
    assert applied_shift
    target = editor.freeze()
    patch = journal.to_patch()
    assert len(patch.operations) >= 2
    assert _atom_multiset_lower_bound(
        case.graph, target, costs, EquivalenceView.EXACT
    ) <= price_patch(patch, costs, case.graph)

    word_tier = case.graph._tiers_by_name[case.word]
    membership = next(
        declaration
        for declaration in case.graph.relation_declarations
        if isinstance(declaration, SimpleRelationDeclaration)
        and declaration.tier == case.word
    )
    ordered = Graph(
        case.graph.namespaces,
        (word_tier,),
        (membership,),
        attribute_declarations=(
            next(
                declaration
                for declaration in case.graph.attribute_declarations
                if declaration.name == case.label
            ),
        ),
    )
    ordered_target = (
        ordered.edit()
        .replace_item(
            ItemRef(case.word, 0),
            Item(
                "word-0",
                (AttributeValue(case.label, XsdType.STRING, "ordered"),),
            ),
        )
        .insert_item(case.word, 1, Item("ordered-extra"))
        .freeze()
    )
    operations_costs = dict(costs.operations)
    insertion = costs.operation("insert_item", case.word)
    deletion = costs.operation("remove_item", case.word)
    declaration_costs = {
        name: dict(values) for name, values in costs.declarations.items()
    }
    word_costs = declaration_costs.setdefault(case.word, {})
    word_costs["move_item"] = insertion + deletion
    word_costs["swap_items"] = insertion + deletion
    exact_costs = CostTable(operations_costs, declaration_costs)
    exact = distance(ordered, ordered_target, exact_costs, view=EquivalenceView.EXACT)
    assert exact.exact
    assert (
        _atom_multiset_lower_bound(
            ordered, ordered_target, exact_costs, EquivalenceView.EXACT
        )
        <= exact.lower
    )


def many_incidence_cases() -> tuple[PrimitiveCase, ...]:
    """Build the four primitives whose one cost can cover a large payload."""
    case = primitive_fixture("atom-many")
    base = case.graph
    item_names = tuple(
        QualifiedName(case.namespace, f"item-value-{index}") for index in range(16)
    )
    boundary_names = tuple(
        QualifiedName(case.namespace, f"boundary-value-{index}") for index in range(16)
    )
    declaration_names = tuple(
        QualifiedName(case.namespace, f"declaration-value-{index}")
        for index in range(16)
    )
    declarations = (
        *base.attribute_declarations,
        *(
            AttributeDeclaration(name, AttributeDomain.ITEM, XsdType.STRING)
            for name in item_names
        ),
        *(
            AttributeDeclaration(name, AttributeDomain.BOUNDARY, XsdType.STRING)
            for name in boundary_names
        ),
        *(
            AttributeDeclaration(
                name, AttributeDomain.RELATION_DECLARATION, XsdType.STRING
            )
            for name in declaration_names
        ),
    )
    item_values = tuple(
        AttributeValue(name, XsdType.STRING, "before") for name in item_names
    )
    boundary_values = tuple(
        AttributeValue(name, XsdType.STRING, "edge") for name in boundary_names
    )
    rich = replace(
        base,
        tiers=(
            base.tiers[0],
            replace(
                base.tiers[1],
                items=(replace(base.tiers[1].items[0], attributes=item_values),),
            ),
        ),
        attribute_declarations=declarations,
        boundary_values=(Boundary(BoundaryRef(case.unit, 1), boundary_values),),
    )
    replacement = replace(
        rich.tiers[1].items[0],
        attributes=tuple(
            AttributeValue(name, XsdType.STRING, "after") for name in item_names
        ),
    )
    relation_template = cast(
        BipartiteRelationDeclaration,
        next(
            declaration
            for declaration in rich.relation_declarations
            if declaration.name == case.link
        ),
    )
    relation_declaration = BipartiteRelationDeclaration(
        QualifiedName(case.namespace, "wide-relation"),
        relation_template.left_type,
        relation_template.right_type,
        attributes=tuple(
            AttributeValue(name, XsdType.STRING, "declared")
            for name in declaration_names
        ),
    )
    endpoints = tuple(ItemRef(case.unit, index % 3) for index in range(64))
    return (
        edit_case(
            rich,
            lambda editor: editor.set_endpoints(
                PolyadicInstanceRef(0), endpoints, tuple(reversed(endpoints))
            ),
        ),
        edit_case(
            rich,
            lambda editor: editor.replace_item(ItemRef(case.spare, 0), replacement),
        ),
        edit_case(
            rich,
            lambda editor: editor.promote_boundary(BoundaryRef(case.unit, 1), "u1"),
        ),
        edit_case(rich, lambda editor: editor.declare(relation_declaration)),
    )


MANY_INCIDENCE_CASES = many_incidence_cases()


# Per-example cost varies with machine load; correctness is in the assertions.
@settings(max_examples=20, deadline=None)
@given(costs=cost_tables())
def test_atom_bound_dominates_many_incidence_primitives(costs: CostTable) -> None:
    """One primitive cost dominates each fixed wide-payload example."""
    for case in MANY_INCIDENCE_CASES:
        bound = _atom_multiset_lower_bound(
            case.before, case.after, costs, EquivalenceView.EXACT
        )
        assert bound <= costs.operation(case.kind, case.declaration)


@settings(max_examples=50)
@given(
    source=st.lists(st.sampled_from(tuple("abcdef")), max_size=6, unique=True),
    target=st.lists(st.sampled_from(tuple("abcdef")), max_size=6, unique=True),
)
def test_atom_bound_never_exceeds_ordered_tier_exact_distance(
    source: list[str], target: list[str]
) -> None:
    """The relaxed multiset cost stays below the exact ordered-tier result."""
    left = graph(*source)
    right = graph(*target)
    exact = distance(left, right, view=EquivalenceView.IDENTIFIED)
    assert exact.exact
    assert (
        _atom_multiset_lower_bound(left, right, UNIT_COSTS, EquivalenceView.IDENTIFIED)
        <= exact.lower
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


def test_sequence_projection_requires_a_diagnostic_name() -> None:
    """Standalone sequence projections retain a stable diagnostic name."""
    with pytest.raises(ValueError, match="nonempty"):
        SequenceProjection("", lambda value: ())
    labels = SequenceProjection(
        "labels", lambda value: tuple(item.durable_id for item in value.tiers[0].items)
    )
    assert labels.distance(graph("a"), graph("b")) == 1


def test_atom_transition_closure_covers_every_retained_atom_kind() -> None:
    """Insertion, deletion, and substitution cover each atom payload shape."""
    case = primitive_fixture("atom-transition")
    first = AttributeValue(case.note, XsdType.STRING, "first")
    second = AttributeValue(case.note, XsdType.STRING, "second")
    other = AttributeValue(case.boundary_note, XsdType.STRING, "other")
    boundary = _Atom("boundary-value", first)
    carrier = _Atom("carrier-value", ("document", None, first))
    for atom in (boundary, carrier):
        assert _atom_insertion(atom, UNIT_COSTS) == 1
        assert _atom_deletion(atom, UNIT_COSTS) == 1

    relation_declaration = cast(
        BipartiteRelationDeclaration,
        next(
            declaration
            for declaration in case.graph.relation_declarations
            if declaration.name == case.link
        ),
    )
    changed_declaration = replace(relation_declaration, attributes=(first,))
    assert (
        _atom_substitution(
            _Atom("declaration", relation_declaration),
            _Atom("declaration", changed_declaration),
            UNIT_COSTS,
        )
        == 1
    )
    assert (
        _atom_substitution(
            boundary,
            _Atom("boundary-value", second),
            UNIT_COSTS,
        )
        == 1
    )
    assert (
        _atom_substitution(
            boundary,
            _Atom("boundary-value", other),
            UNIT_COSTS,
        )
        == 2
    )

    relation = _Atom("relation", ("binary", case.link, None, ()))
    other_relation_shape = _Atom("relation", ("polyadic", case.link, None, ()))
    assert _atom_substitution(relation, other_relation_shape, UNIT_COSTS) == 2

    layer = LayerName(case.namespace, "first")
    other_layer = LayerName(case.namespace, "second")
    fact = _Atom("fact", (layer, first))
    assert _atom_substitution(fact, _Atom("fact", (layer, second)), UNIT_COSTS) == 1
    opaque_costs = CostTable(
        UNIT_COSTS.operations, value_substitution=lambda before, after: 1
    )
    assert _atom_substitution(fact, _Atom("fact", (layer, second)), opaque_costs) == 0
    assert (
        _atom_substitution(fact, _Atom("fact", (other_layer, second)), UNIT_COSTS) == 2
    )

    changed_carrier = _Atom("carrier-value", ("document", None, second))
    other_carrier = _Atom("carrier-value", ("tier", case.unit, second))
    assert _atom_substitution(carrier, changed_carrier, UNIT_COSTS) == 1
    assert _atom_substitution(carrier, other_carrier, UNIT_COSTS) == 2

    seal = _Atom("seal", Seal(case.unit, 1))
    advanced = _Atom("seal", Seal(case.unit, 2))
    other_seal = _Atom("seal", Seal(GraphCarrier.RELATIONS, 1))
    assert _atom_substitution(seal, advanced, UNIT_COSTS) == 1
    assert _atom_substitution(advanced, seal, UNIT_COSTS) == 1
    assert _atom_substitution(seal, other_seal, UNIT_COSTS) == 2
    assert (
        _atom_substitution(
            _Atom("layer", layer), _Atom("layer", other_layer), UNIT_COSTS
        )
        == 2
    )


def test_atom_transition_refuses_an_unknown_kind() -> None:
    """New atom kinds cannot silently inherit seal transition costs."""
    unknown = _Atom("unknown", object())
    with pytest.raises(ValueError, match="unknown graph-distance atom kind"):
        _atom_insertion(unknown, UNIT_COSTS)
    with pytest.raises(ValueError, match="unknown graph-distance atom kind"):
        _atom_deletion(unknown, UNIT_COSTS)
    with pytest.raises(ValueError, match="same kind"):
        _atom_substitution(unknown, _Atom("item", (None, ())), UNIT_COSTS)


def test_large_atom_assignment_uses_a_linear_count_bound() -> None:
    """Large unmatched multisets avoid materializing a cubic assignment."""
    source = [_Atom("item", (f"source-{index}", ())) for index in range(1_000)]
    target = [_Atom("item", (f"target-{index}", ())) for index in range(1_000)]
    assert _atom_kind_distance(source, target, UNIT_COSTS) == 1_000

    zero_costs = CostTable({**UNIT_COSTS.operations, "replace_item": 0})
    assert _atom_kind_distance(source, target, zero_costs) == 0


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
    source = valued_graph("old")
    target = valued_graph("new")
    exact = distance(source, target, costs)
    assert exact.value == Decimal("0.25")
    assert (
        _atom_multiset_lower_bound(source, target, costs, EquivalenceView.FUNCTIONAL)
        == 0
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
    assert result.method in {"realized-diff", "realized-rebuild"}


def test_general_atom_interval_can_have_a_positive_bound() -> None:
    """A retained payload change gives a positive general-graph lower bound."""
    case = primitive_fixture("distance-positive")
    source = case.graph
    item = source.tiers[1].items[0]
    target = source.replace_item(
        ItemRef(case.spare, 0),
        replace(
            item,
            attributes=(AttributeValue(case.note, XsdType.STRING, "changed"),),
        ),
    )
    result = distance(source, target, view=EquivalenceView.IDENTIFIED)
    assert result.lower == Decimal(1)
    assert result.upper >= result.lower
    assert not result.exact
    assert result.value is None
    assert result.lower_bound_method == "atom-multiset"


def test_general_atom_bound_accepts_a_partial_cost_table() -> None:
    """The relaxation does not require costs unrelated to a realized edit."""
    source = replace(
        graph("a"),
        attribute_declarations=(
            AttributeDeclaration(VALUE, AttributeDomain.DOCUMENT, XsdType.STRING),
        ),
        layers=(Layer(LayerName(NS, "diagnostic"), ()),),
    )
    target = source.set_attribute(
        None, AttributeValue(VALUE, XsdType.STRING, "changed")
    )
    costs = CostTable({"set_attribute": 1, "remove_attribute": 1})
    result = distance(source, target, costs, view=EquivalenceView.EXACT)
    assert result.lower == 0
    assert result.upper == 1
    assert result.method == "realized-diff"
    assert result.lower_bound_method == "atom-multiset"


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
    assert result.method == "realized-rebuild"
    assert 0 <= result.lower <= result.upper


def test_rebuild_realizes_mutually_dependent_relation_declarations() -> None:
    """An atomic declaration component still has a replayed primitive price."""
    cyclic = cyclic_relation_graph()
    forward = distance(graph("a"), cyclic, view="identified")
    backward = distance(cyclic, graph("a"), view="identified")
    assert forward.method == backward.method == "realized-rebuild"
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
    assert distance(source, extra, view="identified").method.startswith("realized-")
    costs = CostTable({**UNIT_COSTS.operations, "swap_items": 1})
    swapped = distance(source, graph("b", "a"), costs, view="identified")
    assert not swapped.exact


def test_general_interval_names_both_bound_constructions() -> None:
    """A general result names the atom lower bound and realized upper bound."""
    source = graph("a", "b", "c")
    target = source.move_item(ItemRef(TOKENS, 0), 2)
    costs = CostTable({**UNIT_COSTS.operations, "move_item": 1})
    result = distance(source, target, costs, view="identified")
    assert result.to_data() == {
        "exact": False,
        "lower": 0,
        "upper": 1,
        "method": "realized-diff",
        "lower_bound_method": "atom-multiset",
    }


def test_distance_interval_checks_its_claim() -> None:
    """Interval construction checks bound order, labels, and exactness."""
    with pytest.raises(ValueError, match="must not exceed"):
        DistanceInterval(Decimal(2), Decimal(1), False, "probe", "atom")
    assert not DistanceInterval(Decimal(1), Decimal(1), False, "probe", "atom").exact
    with pytest.raises(ValueError, match="equal bounds"):
        DistanceInterval(Decimal(1), Decimal(2), True, "probe")
    with pytest.raises(ValueError, match="lower-bound method"):
        DistanceInterval(Decimal(1), Decimal(2), False, "probe")
    with pytest.raises(ValueError, match="no lower-bound method"):
        DistanceInterval(Decimal(1), Decimal(1), True, "probe", "atom")
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
