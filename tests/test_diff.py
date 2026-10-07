"""Deterministic diff construction and diff-then-apply properties."""

from __future__ import annotations

import os
import random
import subprocess
import sys
from dataclasses import replace
from importlib import import_module

import pytest

from tests.test_edit_primitives import fixture
from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BoundaryRef,
    EquivalenceView,
    Graph,
    GraphCarrier,
    GraphValidationError,
    Item,
    ItemRef,
    Layer,
    LayerFact,
    LayerName,
    NamespaceDeclaration,
    Patch,
    QualifiedName,
    Seal,
    Tier,
    TierDeclaration,
    XsdType,
    diff,
    equivalent,
    fingerprint,
    invert_patch,
    patch_dumps,
    patch_loads,
)
from tiergraph.machine import DeltaOpcode

diff_module = import_module("tiergraph.diff")


def _operation_names(patch: Patch) -> list[str]:
    return [
        operation.opcode.operation
        for operation in patch.operations
        if isinstance(operation.opcode, DeltaOpcode)
    ]


@pytest.mark.parametrize("domain", ["speech", "text", "music"])
def test_diff_then_apply_reaches_domain_targets_exactly(domain: str) -> None:
    """References follow a moved item and replay survives the patch codec."""
    case = fixture(domain)
    boundary = case.graph.boundary_values[0]
    editor = case.graph.edit()
    editor.remove_attribute(boundary.reference, boundary.attributes[0].name)
    editor.move_item(ItemRef(case.unit, 0), 2)
    editor.set_attribute(BoundaryRef(case.unit, 1), boundary.attributes[0])
    target = editor.freeze()
    patch = diff(case.graph, target, EquivalenceView.EXACT)

    cursor = case.graph
    for operation in patch.operations:
        assert fingerprint(cursor, EquivalenceView.IDENTIFIED) == (
            operation.base_fingerprint
        )
        cursor = operation.opcode.apply(cursor)
        assert fingerprint(cursor, EquivalenceView.IDENTIFIED) == (
            operation.target_fingerprint
        )

    decoded = patch_loads(patch_dumps(patch))
    assert decoded.apply(case.graph) == target
    assert invert_patch(decoded).apply(target) == case.graph
    assert "move_item" in _operation_names(decoded)


def test_diff_alignment_uses_move_and_replacement_under_unit_costs() -> None:
    """A retained shifted item is moved instead of deleted and reinserted."""
    case = fixture("text")
    spare = case.graph.tiers[1]
    changed = Item(attributes=(AttributeValue(case.note, XsdType.STRING, "changed"),))
    target = replace(
        case.graph,
        tiers=(
            case.graph.tiers[0],
            replace(spare, items=(spare.items[1], changed, spare.items[2])),
        ),
    )

    patch = diff(case.graph, target, EquivalenceView.EXACT)
    item_operations = [
        name
        for name in _operation_names(patch)
        if name in {"move_item", "replace_item", "insert_item", "remove_item"}
    ]

    assert item_operations == ["move_item", "replace_item"]
    assert patch.apply(case.graph) == target

    rotated = replace(
        case.graph,
        tiers=(
            case.graph.tiers[0],
            replace(spare, items=(spare.items[1], spare.items[2], spare.items[0])),
        ),
    )
    rotated_patch = diff(case.graph, rotated, EquivalenceView.EXACT)
    assert _operation_names(rotated_patch).count("move_item") == 1
    assert rotated_patch.apply(case.graph) == rotated


def test_diff_handles_insert_remove_and_identity_changes() -> None:
    """Unpaired items insert or depart and paired identities use promote/demote."""
    namespace = "urn:diff:identity"
    tier = QualifiedName(namespace, "items")
    source = Graph(
        (NamespaceDeclaration("d", namespace),),
        (Tier(TierDeclaration(tier, "Items"), (Item("old"), Item("drop"))),),
        (),
    )
    target = replace(
        source,
        tiers=(
            Tier(
                source.tiers[0].declaration,
                (Item("new"), Item("inserted"), Item()),
            ),
        ),
    )

    patch = diff(source, target, EquivalenceView.EXACT)
    names = _operation_names(patch)

    assert "demote_item" in names
    assert "promote_item" in names
    assert "insert_item" in names
    assert patch.apply(source) == target


@pytest.mark.parametrize(
    ("source_item", "target_item", "operation"),
    [
        (Item("old"), Item(), "demote_item"),
        (Item(), Item("new"), "promote_item"),
    ],
)
def test_diff_changes_one_side_of_item_identity(
    source_item: Item, target_item: Item, operation: str
) -> None:
    """Promotion and demotion remain separate from item-value replacement."""
    namespace = "urn:diff:one-sided-identity"
    tier = QualifiedName(namespace, "items")
    source = Graph(
        (NamespaceDeclaration("d", namespace),),
        (Tier(TierDeclaration(tier, "Items"), (source_item,)),),
        (),
    )
    target = replace(source, tiers=(replace(source.tiers[0], items=(target_item,)),))

    patch = diff(source, target, EquivalenceView.EXACT)

    assert operation in _operation_names(patch)
    assert patch.apply(source) == target


def test_diff_empty_patch_respects_the_selected_view() -> None:
    """Ignored ids and prefix spellings do not create functional churn."""
    case = fixture("music")
    identified, _ = case.graph.promote_item(ItemRef(case.spare, 0), "voice-0")
    rebound = replace(
        case.graph,
        namespaces=(NamespaceDeclaration("other", case.namespace),),
    )

    identity_patch = diff(case.graph, identified)
    prefix_patch = diff(case.graph, rebound)

    assert identity_patch.operations == ()
    assert prefix_patch.operations == ()
    assert identity_patch.apply(case.graph) is case.graph
    assert equivalent(identity_patch.apply(case.graph), identified)
    assert equivalent(prefix_patch.apply(case.graph), rebound)


def test_diff_exact_incompatible_schema_uses_guarded_rebuild() -> None:
    """A schema mismatch takes the deterministic rebuild fallback."""
    case = fixture("text")
    target = replace(
        case.graph,
        namespaces=(NamespaceDeclaration("other", case.namespace),),
    )

    first = diff(case.graph, target, EquivalenceView.EXACT)
    second = diff(case.graph, target, "exact")

    assert _operation_names(first) == ["delta"]
    assert patch_dumps(first) == patch_dumps(second)
    assert first.apply(case.graph) == target


def test_guarded_rebuild_handles_reordering_before_later_insertion() -> None:
    """A guarded array reorder remains valid before a later insertion."""
    namespace = "urn:diff:delta-order"
    tier = QualifiedName(namespace, "items")
    declaration = TierDeclaration(tier, "Items")
    source = Graph(
        (NamespaceDeclaration("d", namespace),),
        (Tier(declaration, tuple(Item(f"i-{index}") for index in range(5))),),
        (),
    )
    target = replace(
        source,
        tiers=(
            Tier(
                declaration,
                tuple(
                    Item(value) for value in ("i-2", "i-0", "new", "i-4", "i-3", "i-1")
                ),
            ),
        ),
    )

    opcode = DeltaOpcode.between("delta", source, target)

    assert opcode.apply(source) == target


def test_guarded_rebuild_progresses_past_equal_duplicate_items() -> None:
    """A repeated current value cannot turn a guarded array move into a loop."""
    namespace = "urn:diff:delta-duplicates"
    tier = QualifiedName(namespace, "items")
    declaration = TierDeclaration(tier, "Items")
    source = Graph(
        (NamespaceDeclaration("source", namespace),),
        (Tier(declaration, (Item(), Item(), Item("moved"))),),
        (),
    )
    target = replace(
        source,
        namespaces=(NamespaceDeclaration("target", namespace),),
        tiers=(Tier(declaration, (Item("moved"), Item(), Item())),),
    )

    patch = diff(source, target, EquivalenceView.EXACT)

    assert _operation_names(patch) == ["delta"]
    assert patch.apply(source) == target


def test_diff_reconciles_carrier_values_and_exact_order_residue() -> None:
    """Tier and document values edit normally; exact ordering remains guarded."""
    namespace = "urn:diff:values"
    tier = QualifiedName(namespace, "items")
    tier_a = QualifiedName(namespace, "tier-a")
    tier_b = QualifiedName(namespace, "tier-b")
    document = QualifiedName(namespace, "document")
    document_drop = QualifiedName(namespace, "document-drop")
    declarations = (
        AttributeDeclaration(tier_a, AttributeDomain.TIER, XsdType.STRING),
        AttributeDeclaration(tier_b, AttributeDomain.TIER, XsdType.STRING),
        AttributeDeclaration(document, AttributeDomain.DOCUMENT, XsdType.STRING),
        AttributeDeclaration(document_drop, AttributeDomain.DOCUMENT, XsdType.STRING),
    )
    value_a = AttributeValue(tier_a, XsdType.STRING, "a")
    value_b = AttributeValue(tier_b, XsdType.STRING, "b")
    source = Graph(
        (NamespaceDeclaration("d", namespace),),
        (Tier(TierDeclaration(tier, "Items"), (Item(),), (value_a, value_b)),),
        (),
        attribute_declarations=declarations,
        attributes=(
            AttributeValue(document, XsdType.STRING, "before"),
            AttributeValue(document_drop, XsdType.STRING, "drop"),
        ),
    )
    target = replace(
        source,
        tiers=(replace(source.tiers[0], attributes=(value_b, value_a)),),
        attributes=(AttributeValue(document, XsdType.STRING, "after"),),
    )

    patch = diff(source, target, EquivalenceView.EXACT)

    assert "set_attribute" in _operation_names(patch)
    assert "remove_attribute" in _operation_names(patch)
    assert patch.apply(source) == target

    empty = diff(source, source, EquivalenceView.EXACT)
    with_residue = diff_module._append_residue(empty, source, target)
    assert _operation_names(with_residue) == ["delta"]
    assert with_residue.apply(source) == target


def test_diff_tears_down_and_restores_facts_and_seals() -> None:
    """Facts, relations, boundaries, and seals bracket affected item edits."""
    case = fixture("music")
    layer_name = LayerName(case.namespace, "hand")
    fact = LayerFact(
        ItemRef(case.spare, 0),
        AttributeValue(case.note, XsdType.STRING, "observed"),
    )
    source = replace(
        case.graph,
        layers=(Layer(layer_name, (fact,)),),
        seals=(
            Seal(case.spare, 1),
            Seal(GraphCarrier.RELATIONS, 1),
            Seal(GraphCarrier.POLYADIC_RELATIONS, 1),
        ),
    )
    changed = Item(attributes=(AttributeValue(case.note, XsdType.STRING, "changed"),))
    target = replace(
        source,
        tiers=(
            source.tiers[0],
            replace(source.tiers[1], items=(changed, *source.tiers[1].items[1:])),
        ),
    )

    patch = diff(source, target, EquivalenceView.EXACT)
    names = _operation_names(patch)

    assert "drop_seal" in names
    assert "remove_fact" in names
    assert "put_fact" in names
    assert "seal" in names
    assert patch.apply(source) == target


def test_diff_functional_alignment_compares_item_values() -> None:
    """Functional differences align item values without consulting identities."""
    case = fixture("text")
    changed = Item(attributes=(AttributeValue(case.note, XsdType.STRING, "changed"),))
    target = replace(
        case.graph,
        tiers=(
            case.graph.tiers[0],
            replace(
                case.graph.tiers[1], items=(changed, *case.graph.tiers[1].items[1:])
            ),
        ),
    )

    patch = diff(case.graph, target, EquivalenceView.FUNCTIONAL)

    assert patch.apply(case.graph) == target


def test_diff_functional_alignment_ignores_crossed_item_ids() -> None:
    """Functional value correspondence wins over contrary durable identities."""
    namespace = "urn:diff:functional-ids"
    tier = QualifiedName(namespace, "items")
    value = QualifiedName(namespace, "value")
    declaration = AttributeDeclaration(value, AttributeDomain.ITEM, XsdType.STRING)
    first = Item("first", (AttributeValue(value, XsdType.STRING, "a"),))
    second = Item("second", (AttributeValue(value, XsdType.STRING, "b"),))
    source = Graph(
        (NamespaceDeclaration("d", namespace),),
        (Tier(TierDeclaration(tier, "Items"), (first, second)),),
        (),
        attribute_declarations=(declaration,),
    )
    target = replace(
        source,
        tiers=(
            replace(
                source.tiers[0],
                items=(
                    replace(first, attributes=second.attributes),
                    replace(second, attributes=first.attributes),
                ),
            ),
        ),
    )

    patch = diff(source, target, EquivalenceView.FUNCTIONAL)

    assert _operation_names(patch).count("move_item") == 1
    assert patch.apply(source) == target


def test_diff_falls_back_if_a_compatible_edit_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A local edit refusal selects the guarded rebuild instead of leaking state."""
    case = fixture("text")
    target = case.graph.move_item(ItemRef(case.spare, 0), 1)

    def refuse(
        source: Graph, target: Graph, view: EquivalenceView
    ) -> tuple[object, Graph]:
        del source, target, view
        raise GraphValidationError("probe refusal")

    monkeypatch.setattr(diff_module, "_compatible_diff", refuse)
    patch = diff_module.diff(case.graph, target, EquivalenceView.EXACT)

    assert _operation_names(patch) == ["delta"]
    assert patch.apply(case.graph) == target


def test_diff_seeded_item_sequences_are_deterministic_and_exact() -> None:
    """Duplicate-free permutations, insertions, and removals replay exactly."""
    randomizer = random.Random(7)
    namespace = "urn:diff:random"
    tier = QualifiedName(namespace, "items")
    declaration = TierDeclaration(tier, "Items")
    for _ in range(20):
        source_ids = [f"i-{index}" for index in range(randomizer.randrange(8))]
        target_ids = source_ids[:]
        randomizer.shuffle(target_ids)
        if target_ids and randomizer.randrange(2):
            target_ids.pop()
        if randomizer.randrange(2):
            target_ids.insert(randomizer.randrange(len(target_ids) + 1), "new")
        source = Graph(
            (NamespaceDeclaration("d", namespace),),
            (Tier(declaration, tuple(Item(value) for value in source_ids)),),
            (),
        )
        target = replace(
            source,
            tiers=(Tier(declaration, tuple(Item(value) for value in target_ids)),),
        )

        first = diff(source, target, EquivalenceView.EXACT)
        second = diff(source, target, EquivalenceView.EXACT)

        assert patch_dumps(first) == patch_dumps(second)
        assert first.apply(source) == target


def test_diff_attribute_removals_are_stable_across_hash_seeds() -> None:
    """Separate interpreters order removed attribute names identically."""
    script = """from tiergraph import *
namespace = 'urn:diff:attribute-order'
tier = QualifiedName(namespace, 'items')
names = tuple(QualifiedName(namespace, name) for name in ('charlie', 'alpha', 'bravo'))
declarations = tuple(AttributeDeclaration(name, AttributeDomain.DOCUMENT, XsdType.STRING) for name in names)
source = Graph((NamespaceDeclaration('d', namespace),), (Tier(TierDeclaration(tier, 'Items'), (Item(),)),), (), attribute_declarations=declarations, attributes=tuple(AttributeValue(name, XsdType.STRING, name.local_name) for name in names))
target = Graph(source.namespaces, source.tiers, (), attribute_declarations=declarations)
print(patch_dumps(diff(source, target, EquivalenceView.EXACT)))
"""
    patches = []
    for seed in ("0", "12345", "999"):
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = seed
        completed = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        patches.append(completed.stdout)

    assert len(set(patches)) == 1


def test_diff_rejects_an_unknown_view() -> None:
    """View parsing follows the public equivalence API."""
    graph = fixture("text").graph
    with pytest.raises(ValueError, match="not a valid EquivalenceView"):
        diff(graph, graph, "unknown")
