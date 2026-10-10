"""Deterministic graph differences expressed as executable patches."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import replace
from functools import partial
from typing import Literal

from tiergraph.clock import ClockRebindingPolicy
from tiergraph.core import (
    Attribute,
    DurableItemRef,
    Graph,
    GraphEditor,
    GraphValidationError,
    Item,
    ItemRef,
    JsonValue,
    PolyadicInstanceRef,
    QualifiedName,
    RelationInstanceRef,
)
from tiergraph.edit import Journal, JournalEditor
from tiergraph.equivalence import EquivalenceView, equivalent, fingerprint
from tiergraph.machine import DeltaOpcode
from tiergraph.patch import Patch, PatchOperation

type _ItemKey = str


def _item_key(item: Item, view: EquivalenceView) -> _ItemKey:
    """Return the item content visible in one equivalence view."""
    value: dict[str, JsonValue] = {
        "attributes": [attribute.to_data() for attribute in item.attributes],
    }
    if view is not EquivalenceView.FUNCTIONAL:
        value["durable_id"] = item.durable_id
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _schemas_are_compatible(source: Graph, target: Graph) -> bool:
    """Report whether item edits can retain the source declarations in place."""
    return (
        source.namespaces == target.namespaces
        and tuple(tier.declaration for tier in source.tiers)
        == tuple(tier.declaration for tier in target.tiers)
        and source.relation_declarations == target.relation_declarations
        and source.attribute_declarations == target.attribute_declarations
        and tuple(layer.name for layer in source.layers)
        == tuple(layer.name for layer in target.layers)
    )


def _delta_operation(source: Graph, target: Graph) -> PatchOperation:
    """Return one guarded exact transition for a rebuild or final residue."""
    return PatchOperation(
        DeltaOpcode.between("delta", source, target),
        DeltaOpcode.between("delta", target, source),
        fingerprint(source, EquivalenceView.IDENTIFIED),
        fingerprint(target, EquivalenceView.IDENTIFIED),
    )


def _delta_patch(source: Graph, target: Graph) -> Patch:
    """Return the dependency-independent rebuild fallback."""
    operation = _delta_operation(source, target)
    return Patch(
        operation.base_fingerprint,
        operation.target_fingerprint,
        (operation,),
    )


def _remove_constraints(editor: JournalEditor, source: Graph) -> None:
    """Remove content that can prevent ordered item edits, in dependency order."""
    for seal in source.seals:
        editor.drop_seal(seal.carrier)
    for layer in source.layers:
        for fact in layer.facts:
            editor.remove_fact(layer.name, fact.subject, fact.value.name)
    for index in reversed(range(len(source.polyadic_relations))):
        editor.remove_relation(PolyadicInstanceRef(index))
    for index in reversed(range(len(source.relations))):
        editor.remove_relation(RelationInstanceRef(index))
    for boundary in source.boundary_values:
        for attribute in boundary.attributes:
            editor.remove_attribute(boundary.reference, attribute.name)


def _pair_items(
    source: Sequence[Item], target: Sequence[Item], view: EquivalenceView
) -> list[int | None]:
    """Map target positions to reusable source positions deterministically."""
    result: list[int | None] = [None] * len(target)
    unused = set(range(len(source)))

    if view is not EquivalenceView.FUNCTIONAL:
        by_id = {
            item.durable_id: index
            for index, item in enumerate(source)
            if item.durable_id is not None
        }
        for target_index, item in enumerate(target):
            if item.durable_id is None:
                continue
            source_index = by_id.get(item.durable_id)
            if source_index in unused:
                result[target_index] = source_index
                unused.remove(source_index)

    by_key: dict[_ItemKey, list[int]] = {}
    for source_index in sorted(unused):
        by_key.setdefault(_item_key(source[source_index], view), []).append(
            source_index
        )
    for target_index, item in enumerate(target):
        if result[target_index] is not None:
            continue
        candidates = by_key.get(_item_key(item, view), [])
        if candidates:
            source_index = candidates.pop(0)
            result[target_index] = source_index
            unused.remove(source_index)

    remaining_source = iter(sorted(unused))
    for target_index, source_index in enumerate(result):
        if source_index is not None:
            continue
        replacement = next(remaining_source, None)
        if replacement is None:
            break
        result[target_index] = replacement
    return result


def _replace_item(
    editor: JournalEditor,
    tier: QualifiedName,
    index: int,
    current: Item,
    target: Item,
) -> None:
    """Replace values and identity through their separate editing primitives."""
    if current.durable_id != target.durable_id:
        if current.durable_id is not None:
            editor.demote_item(DurableItemRef(current.durable_id))
        if target.durable_id is not None:
            editor.promote_item(ItemRef(tier, index), target.durable_id)
    if current.attributes != target.attributes:
        editor.replace_item(ItemRef(tier, index), target)


def _lcs_tokens(source: Sequence[int], target: Sequence[int]) -> set[int]:
    """Return one deterministic longest ordered set of reusable item tokens."""
    lengths = [[0] * (len(target) + 1) for _ in range(len(source) + 1)]
    for source_index in reversed(range(len(source))):
        for target_index in reversed(range(len(target))):
            if source[source_index] == target[target_index]:
                lengths[source_index][target_index] = (
                    lengths[source_index + 1][target_index + 1] + 1
                )
            else:
                lengths[source_index][target_index] = max(
                    lengths[source_index + 1][target_index],
                    lengths[source_index][target_index + 1],
                )
    result: set[int] = set()
    source_index = 0
    target_index = 0
    while source_index < len(source) and target_index < len(target):
        if source[source_index] == target[target_index]:
            result.add(source[source_index])
            source_index += 1
            target_index += 1
        elif (
            lengths[source_index + 1][target_index]
            >= lengths[source_index][target_index + 1]
        ):
            source_index += 1
        else:
            target_index += 1
    return result


def _edit_tier(
    editor: JournalEditor,
    tier: QualifiedName,
    source: Sequence[Item],
    target: Sequence[Item],
    view: EquivalenceView,
) -> None:
    """Apply a unit-cost alignment with equal delete/insert pairs made moves."""
    target_sources = _pair_items(source, target, view)
    retained = {index for index in target_sources if index is not None}
    tokens = list(range(len(source)))
    items = list(source)

    for source_index in reversed(range(len(source))):
        if source_index in retained:
            continue
        position = tokens.index(source_index)
        editor.remove_item(ItemRef(tier, position))
        del tokens[position]
        del items[position]

    target_tokens = [index for index in target_sources if index is not None]
    stationary = _lcs_tokens(tokens, target_tokens)
    target_positions = {token: index for index, token in enumerate(target_tokens)}
    for target_index, desired in enumerate(target_tokens):
        if tokens[target_index] == desired:
            continue
        if desired in stationary:
            position = target_positions[tokens[target_index]]
            source_index = target_index
        else:
            position = target_index
            source_index = tokens.index(desired)
        editor.move_item(ItemRef(tier, source_index), position)
        tokens.insert(position, tokens.pop(source_index))
        items.insert(position, items.pop(source_index))

    for target_index, target_item in enumerate(target):
        if target_sources[target_index] is not None:
            continue
        editor.insert_item(tier, target_index, target_item)
        tokens.insert(target_index, -target_index - 1)
        items.insert(target_index, target_item)

    for target_index, target_item in enumerate(target):
        _replace_item(editor, tier, target_index, items[target_index], target_item)
        items[target_index] = target_item


def _values_by_name(values: Sequence[Attribute]) -> dict[QualifiedName, Attribute]:
    """Index the validated one-value-per-name attribute sequence."""
    return {value.name: value for value in values}


def _reconcile_values(
    editor: JournalEditor,
    target: QualifiedName | None,
    source: Sequence[Attribute],
    desired: Sequence[Attribute],
) -> None:
    """Reconcile one carrier's values without disturbing equal values."""
    before = _values_by_name(source)
    after = _values_by_name(desired)
    for name in sorted(before.keys() - after.keys()):
        editor.remove_attribute(target, name)
    for value in desired:
        if before.get(value.name) != value:
            editor.set_attribute(target, value)


def _restore_constraints(editor: JournalEditor, target: Graph) -> None:
    """Restore values and references in forward dependency order."""
    for boundary in target.boundary_values:
        for attribute in boundary.attributes:
            editor.set_attribute(boundary.reference, attribute)
    for binary_relation in target.relations:
        editor.add_relation(binary_relation)
    for polyadic_relation in target.polyadic_relations:
        editor.add_relation(polyadic_relation)
    for layer in target.layers:
        for fact in layer.facts:
            editor.put_fact(layer.name, fact)
    for seal in target.seals:
        editor.seal(seal.carrier, seal.sealed)


def _compatible_diff(
    source: Graph, target: Graph, view: EquivalenceView
) -> tuple[Patch, Graph]:
    """Build a dependency-ordered patch over compatible declarations."""
    journal = Journal()
    editor = source.edit(journal=journal)
    _remove_constraints(editor, source)
    current_tiers = [tier.items for tier in source.tiers]
    if view is EquivalenceView.FUNCTIONAL:
        for tier_index, tier in enumerate(source.tiers):
            current: list[Item] = []
            for item in tier.items:
                if item.durable_id is not None:
                    editor.demote_item(DurableItemRef(item.durable_id))
                current.append(Item(None, item.attributes))
            current_tiers[tier_index] = tuple(current)
    for tier_index, (source_tier, target_tier) in enumerate(
        zip(source.tiers, target.tiers, strict=True)
    ):
        _edit_tier(
            editor,
            source_tier.declaration.name,
            current_tiers[tier_index],
            target_tier.items,
            view,
        )
    for source_tier, target_tier in zip(source.tiers, target.tiers, strict=True):
        _reconcile_values(
            editor,
            source_tier.declaration.name,
            source_tier.attributes,
            target_tier.attributes,
        )
    _reconcile_values(editor, None, source.attributes, target.attributes)
    _restore_constraints(editor, target)
    result = editor.freeze()
    patch = journal.to_patch()
    return patch, result


def _append_residue(patch: Patch, source: Graph, target: Graph) -> Patch:
    """Append the exact remainder after semantic operations, if any."""
    if source == target:
        return patch
    residue = _delta_operation(source, target)
    return Patch(
        patch.base_fingerprint,
        residue.target_fingerprint,
        (*patch.operations, residue),
        patch.annotations,
    )


def _shift_patch(source: Graph, target: Graph) -> Patch | None:
    """Recognize one identity-preserving adjacent containment-boundary shift."""
    if source.tiers != target.tiers:
        return None
    if (
        replace(
            source,
            polyadic_relations=target.polyadic_relations,
            boundary_values=target.boundary_values,
        )
        != target
    ):
        return None
    if len(source.polyadic_relations) != len(target.polyadic_relations):
        return None
    changed = tuple(
        index
        for index, (before, after) in enumerate(
            zip(source.polyadic_relations, target.polyadic_relations, strict=True)
        )
        if before != after
    )
    shifted_instance_count = 2
    if len(changed) != shifted_instance_count:
        return None
    for source_index in changed:
        instance = source.polyadic_relations[source_index]
        candidate = target.polyadic_relations[source_index]
        count = len(instance.targets) - len(candidate.targets)
        if count < 1 or len(instance.sources) != 1:
            continue
        endpoint = instance.sources[0]
        if not isinstance(endpoint, ItemRef | DurableItemRef):
            continue
        container = source.resolve_item(endpoint)
        for direction in ("left", "right"):
            for policy in (None, "keep-earlier", "drop-to-provisional"):
                for across_parent in (False, True):
                    journal = Journal()
                    editor = source.edit(journal=journal)
                    try:
                        editor.shift(
                            container,
                            count,
                            direction,
                            instance.declaration,
                            policy,
                            across_parent,
                        )
                    except GraphValidationError:
                        continue
                    if editor.freeze() == target:
                        return journal.to_patch()
    return None


def _containment_names(graph: Graph) -> tuple[QualifiedName, ...]:
    """Return ordered-containment declarations in declared order."""
    return tuple(
        declaration.name
        for declaration in graph.relation_declarations
        if GraphEditor._is_ordered_containment(declaration)
    )


def _insertion_positions(before: Sequence[Item], after: Sequence[Item]) -> range:
    """Return every position where one item insertion explains ``after``."""
    if len(after) != len(before) + 1:
        return range(0)
    prefix = 0
    while prefix < len(before) and before[prefix] == after[prefix]:
        prefix += 1
    suffix = 0
    while suffix < len(before) and before[-suffix - 1] == after[-suffix - 1]:
        suffix += 1
    return range(len(before) - suffix, prefix + 1)


def _before_insertion(
    item: ItemRef, tier: QualifiedName, insertion: int
) -> ItemRef | None:
    """Map a post-insertion item coordinate to its earlier coordinate."""
    if item.tier != tier:
        return item
    if item.index == insertion:
        return None
    return ItemRef(tier, item.index - (item.index > insertion))


def _after_removal(item: ItemRef, tier: QualifiedName, removal: int) -> ItemRef | None:
    """Map a pre-removal item coordinate to its later coordinate."""
    if item.tier != tier:
        return item
    if item.index == removal:
        return None
    return ItemRef(tier, item.index - (item.index > removal))


def _mapped_items(
    items: Sequence[ItemRef],
    mapping: Callable[[ItemRef], ItemRef | None],
) -> tuple[ItemRef, ...] | None:
    """Apply one coordinate map, refusing a sequence containing the changed item."""
    mapped: list[ItemRef] = []
    for item in items:
        candidate = mapping(item)
        if candidate is None:
            return None
        mapped.append(candidate)
    return tuple(mapped)


def _regroup_patch(  # noqa: PLR0915 -- derives and verifies both regroup directions
    source: Graph, target: Graph
) -> Patch | None:
    """Recognize one exact container split or merge from changed memberships."""
    from tiergraph.container_edit import RegroupPolicies  # noqa: PLC0415

    if not _schemas_are_compatible(source, target):
        return None
    deltas = tuple(
        len(after.items) - len(before.items)
        for before, after in zip(source.tiers, target.tiers, strict=True)
    )
    if deltas.count(1) + deltas.count(-1) != 1 or any(
        delta not in {-1, 0, 1} for delta in deltas
    ):
        return None
    changed_tier = next(index for index, delta in enumerate(deltas) if delta in {-1, 1})
    if any(
        before.items != after.items
        for index, (before, after) in enumerate(
            zip(source.tiers, target.tiers, strict=True)
        )
        if index != changed_tier
    ):
        return None
    tier = source.tiers[changed_tier].declaration.name
    names = _containment_names(source)
    if not names:
        return None
    source_probe = GraphEditor(source)
    target_probe = GraphEditor(target)
    memberships = tuple(
        (
            containment,
            source_probe._containment_instances(containment)[1],
            target_probe._containment_instances(containment)[1],
        )
        for containment in names
    )
    if deltas[changed_tier] == 1:
        before_items = source.tiers[changed_tier].items
        after_items = target.tiers[changed_tier].items
        candidates: list[
            tuple[ItemRef, int, QualifiedName, Literal["after", "before"], int]
        ] = []
        for insertion in _insertion_positions(before_items, after_items):
            split_placements: tuple[tuple[Literal["after", "before"], int], ...] = (
                ("after", insertion - 1),
                ("before", insertion),
            )
            for side, container_index in split_placements:
                if container_index < 0 or container_index >= len(before_items):
                    continue
                container = ItemRef(tier, container_index)
                original_after = ItemRef(tier, container_index + (side == "before"))
                new_after = ItemRef(tier, insertion)
                for containment, source_instances, target_instances in memberships:
                    source_membership = source_instances.get(container)
                    original_membership = target_instances.get(original_after)
                    new_membership = target_instances.get(new_after)
                    if (
                        source_membership is None
                        or original_membership is None
                        or new_membership is None
                        or original_membership == new_membership
                    ):
                        continue
                    source_children = source_probe._resolved_containment_targets(
                        source_membership
                    )
                    original_children = _mapped_items(
                        target_probe._resolved_containment_targets(original_membership),
                        partial(_before_insertion, tier=tier, insertion=insertion),
                    )
                    new_children = _mapped_items(
                        target_probe._resolved_containment_targets(new_membership),
                        partial(_before_insertion, tier=tier, insertion=insertion),
                    )
                    if original_children is None or new_children is None:
                        continue
                    child_prefix, child_suffix = (
                        (original_children, new_children)
                        if side == "after"
                        else (new_children, original_children)
                    )
                    at = len(child_prefix)
                    if (
                        not child_prefix
                        or not child_suffix
                        or (*child_prefix, *child_suffix) != source_children
                    ):
                        continue
                    split_candidate = (container, at, containment, side, insertion)
                    candidates.append(split_candidate)
        for container, at, containment, side, insertion in candidates:
            for policies in (
                None,
                RegroupPolicies(clock=ClockRebindingPolicy.KEEP_EARLIER),
            ):
                journal = Journal()
                editor = source.edit(journal=journal)
                try:
                    editor.split_container(
                        container,
                        at,
                        containment,
                        after_items[insertion],
                        side,
                        policies,
                    )
                except GraphValidationError:
                    continue
                if editor.freeze() == target:
                    return journal.to_patch()
        reverse = _regroup_patch(target, source)
        return None if reverse is None else reverse.invert()

    before_items = source.tiers[changed_tier].items
    after_items = target.tiers[changed_tier].items
    merge_candidates: list[tuple[ItemRef, ItemRef, ItemRef, QualifiedName]] = []
    for removal in _insertion_positions(after_items, before_items):
        merge_placements: list[tuple[ItemRef, ItemRef, ItemRef]] = []
        if removal > 0:
            merge_placements.append(
                (
                    ItemRef(tier, removal - 1),
                    ItemRef(tier, removal),
                    ItemRef(tier, removal - 1),
                )
            )
        if removal + 1 < len(before_items):
            merge_placements.append(
                (
                    ItemRef(tier, removal),
                    ItemRef(tier, removal + 1),
                    ItemRef(tier, removal + 1),
                )
            )
        for first, second, survivor in merge_placements:
            survivor_after = ItemRef(tier, survivor.index - (survivor.index > removal))
            for containment, source_instances, target_instances in memberships:
                left_membership = source_instances.get(first)
                right_membership = source_instances.get(second)
                survivor_membership = target_instances.get(survivor_after)
                if (
                    left_membership is None
                    or right_membership is None
                    or survivor_membership is None
                ):
                    continue
                left_children = _mapped_items(
                    source_probe._resolved_containment_targets(left_membership),
                    partial(_after_removal, tier=tier, removal=removal),
                )
                right_children = _mapped_items(
                    source_probe._resolved_containment_targets(right_membership),
                    partial(_after_removal, tier=tier, removal=removal),
                )
                target_children = target_probe._resolved_containment_targets(
                    survivor_membership
                )
                if (
                    left_children is None
                    or right_children is None
                    or (*left_children, *right_children) != target_children
                ):
                    continue
                merge_candidate = (first, second, survivor, containment)
                merge_candidates.append(merge_candidate)
    for first, second, survivor, containment in merge_candidates:
        for policies in (
            None,
            RegroupPolicies(clock=ClockRebindingPolicy.DROP_TO_PROVISIONAL),
        ):
            journal = Journal()
            editor = source.edit(journal=journal)
            try:
                editor.merge_containers(first, second, survivor, containment, policies)
            except GraphValidationError:
                continue
            if editor.freeze() == target:
                return journal.to_patch()
    return None


def diff(
    source: Graph,
    target: Graph,
    view: EquivalenceView | str = EquivalenceView.FUNCTIONAL,
) -> Patch:
    """Return a deterministic executable patch from ``source`` toward ``target``.

    One exact containment shift, split, or merge remains its semantic
    operation. Other compatible schemas use a per-tier unit-cost alignment.
    Reused equal items become moves, unmatched items become replacements,
    insertions, or removals, and references are torn down and rebuilt in
    dependency order. Incompatible declarations use one guarded rebuild delta.
    Applying the result always produces a graph equivalent to ``target`` under
    ``view``; an already equivalent pair produces an empty patch guarded to
    ``source``. When such a pair differs under the identified view, that no-op
    patch is not an exact transition to ``target`` and does not compose as one.

    This graph-level operation validates graph structure only. It does not
    preserve or report a clock profile's rebinding policy; construct edits
    through :meth:`ClockProfile.edit` when that policy must govern structural
    changes.
    """
    selected = EquivalenceView(view)
    base_fingerprint = fingerprint(source, EquivalenceView.IDENTIFIED)
    if equivalent(source, target, selected):
        return Patch(base_fingerprint, base_fingerprint, ())
    recognized_shift = _shift_patch(source, target)
    if recognized_shift is not None:
        return recognized_shift
    recognized_regroup = _regroup_patch(source, target)
    if recognized_regroup is not None:
        return recognized_regroup
    if not _schemas_are_compatible(source, target):
        return _delta_patch(source, target)
    try:
        patch, result = _compatible_diff(source, target, selected)
    except GraphValidationError:
        return _delta_patch(source, target)
    patch = _append_residue(patch, result, target)
    replayed = patch.apply(source)
    if not equivalent(replayed, target, selected):  # pragma: no cover
        raise GraphValidationError("graph diff did not replay to its target view")
    return patch


__all__ = ["diff"]
