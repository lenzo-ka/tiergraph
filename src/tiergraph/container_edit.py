"""Split and merge adjacent containers in ordered containment."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Literal, cast

from tiergraph.clock import ClockRebindingPolicy, anchored_boundary
from tiergraph.core import (
    Attribute,
    AttributeValue,
    Boundary,
    BoundaryRef,
    DurableBoundaryRef,
    DurableItemRef,
    DurablePolyadicRef,
    DurableRelationRef,
    Graph,
    GraphEditor,
    GraphValidationError,
    Item,
    ItemRef,
    JsonAttributeValue,
    Layer,
    LayerFact,
    LayerName,
    LayerSubject,
    PolyadicInstanceRef,
    PolyadicRelationInstance,
    QualifiedName,
    RelationEndpointRef,
    RelationInstance,
    RelationInstanceRef,
    _remap_layer,
)
from tiergraph.replacement import (
    DetachmentReport,
    EditResult,
    ReplacementAction,
    SubtreeCorrespondence,
)

_EMPTY_ITEM = Item()


def _action(value: ReplacementAction | str, subject: str) -> ReplacementAction:
    """Normalize one regroup action and reject lossy replacement-only actions."""
    try:
        action = ReplacementAction(value)
    except ValueError as error:
        raise ValueError(f"{subject} has unknown action {value!r}") from error
    if action not in {ReplacementAction.FOLLOW, ReplacementAction.DROP}:
        raise ValueError(f"{subject} action must be 'follow' or 'drop'")
    return action


@dataclass(frozen=True, slots=True)
class RegroupPolicies:
    """Name exact dependency handling for a container split or merge.

    Relations and layer facts follow the operation's functional
    correspondence unless their declaration or layer is explicitly ``drop``.
    Attribute actions address values on the container removed by a merge.
    ``seam_content=DROP`` authorizes withdrawal of independent values and facts
    at the retired container seam. Timing changes additionally require the
    named clock rebinding policy.
    """

    relations: Mapping[QualifiedName, ReplacementAction] = field(default_factory=dict)
    layers: Mapping[LayerName, ReplacementAction] = field(default_factory=dict)
    attributes: Mapping[QualifiedName, ReplacementAction] = field(default_factory=dict)
    container_values: ReplacementAction = ReplacementAction.FOLLOW
    seam_content: ReplacementAction = ReplacementAction.FOLLOW
    clock: ClockRebindingPolicy | None = None

    def __post_init__(self) -> None:
        """Detach policy mappings and keep the admitted vocabulary narrow."""
        object.__setattr__(
            self,
            "relations",
            MappingProxyType(
                {
                    name: _action(value, f"relation {str(name)!r}")
                    for name, value in self.relations.items()
                }
            ),
        )
        object.__setattr__(
            self,
            "layers",
            MappingProxyType(
                {
                    name: _action(value, f"layer {name.vocabulary!r}/{name.source!r}")
                    for name, value in self.layers.items()
                }
            ),
        )
        object.__setattr__(
            self,
            "attributes",
            MappingProxyType(
                {
                    name: _action(value, f"attribute {str(name)!r}")
                    for name, value in self.attributes.items()
                }
            ),
        )
        object.__setattr__(
            self, "container_values", _action(self.container_values, "container values")
        )
        object.__setattr__(
            self, "seam_content", _action(self.seam_content, "seam content")
        )
        if self.clock is not None:
            object.__setattr__(self, "clock", ClockRebindingPolicy(self.clock))


@dataclass(frozen=True, slots=True)
class RegroupRestoration:
    """Carry only merge-destroyed content needed by its inverse split.

    Relation and layer entries retain their original carrier positions. Removed
    relation positions distinguish insertions from in-place rewrites. Seam
    entries retain their boundary-value carrier positions. The original
    survivor item restores its pre-merge attributes.
    """

    survivor_item: Item
    membership_index: int
    relation_count: int
    polyadic_relation_count: int
    relations: tuple[tuple[int, RelationInstance], ...] = ()
    polyadic_relations: tuple[tuple[int, PolyadicRelationInstance], ...] = ()
    removed_relation_positions: tuple[int, ...] = ()
    removed_polyadic_relation_positions: tuple[int, ...] = ()
    layers: tuple[tuple[LayerName, tuple[LayerFact, ...]], ...] = ()
    seam_values: tuple[
        tuple[int, BoundaryRef | DurableBoundaryRef, tuple[Attribute, ...]], ...
    ] = ()

    def __post_init__(self) -> None:
        """Validate the change-sized positional restoration payload."""
        if not isinstance(self.survivor_item, Item):
            raise TypeError("regroup restoration survivor_item must be an Item")
        if (
            isinstance(self.membership_index, bool)
            or not isinstance(self.membership_index, int)
            or self.membership_index < 0
        ):
            raise ValueError("regroup restoration membership_index must be nonnegative")
        for count_name, count in (
            ("relation_count", self.relation_count),
            ("polyadic_relation_count", self.polyadic_relation_count),
        ):
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError(
                    f"regroup restoration {count_name} must be nonnegative"
                )
        if self.membership_index >= self.polyadic_relation_count:
            raise ValueError(
                "regroup restoration membership position is outside the original content"
            )
        for position, binary_relation in self.relations:
            if (
                position < 0
                or position >= self.relation_count
                or not isinstance(binary_relation, RelationInstance)
            ):
                raise TypeError("regroup restoration binary relation entry is invalid")
        for position, polyadic_relation in self.polyadic_relations:
            if (
                position < 0
                or position >= self.polyadic_relation_count
                or not isinstance(polyadic_relation, PolyadicRelationInstance)
            ):
                raise TypeError(
                    "regroup restoration polyadic relation entry is invalid"
                )
        relation_positions = tuple(position for position, _ in self.relations)
        polyadic_positions = tuple(position for position, _ in self.polyadic_relations)
        if len(set(relation_positions)) != len(relation_positions):
            raise ValueError("regroup restoration repeats a binary relation position")
        if len(set(polyadic_positions)) != len(polyadic_positions):
            raise ValueError("regroup restoration repeats a polyadic relation position")
        for label, removed, changed in (
            (
                "binary",
                self.removed_relation_positions,
                frozenset(relation_positions),
            ),
            (
                "polyadic",
                self.removed_polyadic_relation_positions,
                frozenset(polyadic_positions),
            ),
        ):
            if (
                any(
                    isinstance(position, bool) or not isinstance(position, int)
                    for position in removed
                )
                or len(set(removed)) != len(removed)
                or not set(removed) <= changed
            ):
                raise ValueError(
                    f"regroup restoration {label} removed positions are invalid"
                )
        if self.membership_index not in self.removed_polyadic_relation_positions:
            raise ValueError(
                "regroup restoration membership position must name a removed relation"
            )
        for name, facts in self.layers:
            if not isinstance(name, LayerName) or any(
                not isinstance(fact, LayerFact) for fact in facts
            ):
                raise TypeError("regroup restoration layer entry is invalid")
        for position, reference, attributes in self.seam_values:
            if (
                position < 0
                or not isinstance(reference, BoundaryRef | DurableBoundaryRef)
                or any(
                    not isinstance(value, AttributeValue | JsonAttributeValue)
                    for value in attributes
                )
            ):
                raise TypeError("regroup restoration seam entry is invalid")


def _restore_relation_positions[Carrier](
    current: list[Carrier],
    count: int,
    entries: tuple[tuple[int, Carrier], ...],
    removed_positions: tuple[int, ...],
    label: str,
) -> list[Carrier]:
    """Rebuild one original relation collection from its change-sized payload."""
    removed = frozenset(removed_positions)
    changed = dict(entries)
    if len(current) != count - len(removed):
        raise GraphValidationError(
            f"regroup restoration {label} relation count does not match the result"
        )
    restored: list[Carrier] = []
    cursor = 0
    for position in range(count):
        original = changed.get(position)
        if original is not None:
            restored.append(original)
            if position not in removed:
                cursor += 1
        else:
            restored.append(current[cursor])
            cursor += 1
    if cursor != len(current):  # pragma: no cover - guarded by the count equation
        raise GraphValidationError(
            f"regroup restoration {label} relation content is inconsistent"
        )
    return restored


@dataclass(frozen=True, slots=True)
class _RegroupOutcome:
    graph: Graph
    displacement: object
    report: DetachmentReport
    correspondence: SubtreeCorrespondence
    restoration: RegroupRestoration | None = None


def _policies(value: RegroupPolicies | None) -> RegroupPolicies:
    if value is None:
        return RegroupPolicies()
    if not isinstance(value, RegroupPolicies):
        raise TypeError("regroup policies must be RegroupPolicies or None")
    return value


def _item(editor: GraphEditor, reference: ItemRef) -> Item:
    return editor._member(reference.tier, "container regroup").items[reference.index]


def _stable_item(editor: GraphEditor, reference: ItemRef) -> ItemRef | DurableItemRef:
    item = _item(editor, reference)
    return reference if item.durable_id is None else DurableItemRef(item.durable_id)


def _endpoint_item(
    editor: GraphEditor, endpoint: RelationEndpointRef
) -> ItemRef | None:
    if not isinstance(endpoint, ItemRef | DurableItemRef):
        return None
    return editor._resolve_item(endpoint)


def _replace_item_endpoint(
    editor: GraphEditor,
    endpoint: RelationEndpointRef,
    source: ItemRef,
    target: ItemRef | DurableItemRef,
) -> RelationEndpointRef:
    resolved = _endpoint_item(editor, endpoint)
    return target if resolved == source else endpoint


def _ordered_memberships(
    editor: GraphEditor, item: ItemRef
) -> dict[QualifiedName, tuple[int, int]]:
    """Return relation, instance, and target positions containing one item."""
    names = {
        declaration.name
        for declaration in editor._relation_declarations
        if editor._is_ordered_containment(declaration)
    }
    result: dict[QualifiedName, tuple[int, int]] = {}
    for index, relation in enumerate(editor._polyadic_relations):
        if relation.declaration not in names:
            continue
        positions = tuple(
            position
            for position, endpoint in enumerate(relation.targets)
            if _endpoint_item(editor, endpoint) == item
        )
        if len(positions) > 1:  # pragma: no cover - Graph validates ordered arity
            raise GraphValidationError(
                f"container {str(item)!r} occurs more than once in ordered "
                f"containment {str(relation.declaration)!r} instance {index}"
            )
        if positions:
            if (
                relation.declaration in result
            ):  # pragma: no cover - single-parent invariant
                raise GraphValidationError(
                    f"container {str(item)!r} has more than one parent in ordered "
                    f"containment {str(relation.declaration)!r}"
                )
            result[relation.declaration] = (index, positions[0])
    return result


def _insert_polyadic(
    editor: GraphEditor, index: int, relation: PolyadicRelationInstance
) -> None:
    count = len(editor._polyadic_relations)
    mapping = {old: old if old < index else old + 1 for old in range(count)}
    step = editor._current_displacement(polyadic_relations=mapping)
    editor._layers = [_remap_layer(layer, step) for layer in editor._layers]
    editor._polyadic_relations.insert(index, relation)
    editor._advance_displacement(step)


def _insert_binary(editor: GraphEditor, index: int, relation: RelationInstance) -> None:
    """Insert one binary relation while displacing positional layer subjects."""
    count = len(editor._relations)
    mapping = {old: old if old < index else old + 1 for old in range(count)}
    step = editor._current_displacement(relations=mapping)
    editor._layers = [_remap_layer(layer, step) for layer in editor._layers]
    editor._relations.insert(index, relation)
    editor._advance_displacement(step)


def _complete_boundary_relations(
    editor: GraphEditor, tier: QualifiedName
) -> dict[QualifiedName, tuple[tuple[int, RelationInstance], ...]]:
    """Return declarations with exactly one left endpoint at every tier boundary."""
    expected = set(range(len(editor._member(tier, "container regroup").items) + 1))
    by_declaration: dict[
        QualifiedName, list[tuple[int, RelationInstance, BoundaryRef]]
    ] = {}
    for index, relation in enumerate(editor._relations):
        if not isinstance(relation.left, BoundaryRef | DurableBoundaryRef):
            continue
        boundary = editor._resolve_boundary(relation.left)
        if boundary.tier != tier:
            continue
        by_declaration.setdefault(relation.declaration, []).append(
            (index, relation, boundary)
        )
    return {
        declaration: tuple((index, relation) for index, relation, _ in entries)
        for declaration, entries in by_declaration.items()
        if len(entries) == len(expected)
        and {boundary.index for _, _, boundary in entries} == expected
        and all(
            isinstance(relation.right, BoundaryRef | DurableBoundaryRef)
            for _, relation, _ in entries
        )
    }


def _split_clock_bindings(
    editor: GraphEditor, tier: QualifiedName, child_seam: BoundaryRef
) -> tuple[tuple[QualifiedName, RelationInstance], ...]:
    """Find complete parent-boundary relations sharing the child seam's clock."""
    complete = _complete_boundary_relations(editor, tier)
    found: list[tuple[QualifiedName, RelationInstance]] = []
    for relation in editor._relations:
        if relation.declaration not in complete or not isinstance(
            relation.left, BoundaryRef | DurableBoundaryRef
        ):
            continue
        if editor._resolve_boundary(relation.left) == child_seam:
            found.append((relation.declaration, relation))
    return tuple(found)


def _merge_clock_bindings(
    editor: GraphEditor, tier: QualifiedName, seam: BoundaryRef
) -> frozenset[int]:
    """Find complete parent-boundary relations attached to a retired seam."""
    complete = _complete_boundary_relations(editor, tier)
    return frozenset(
        index
        for entries in complete.values()
        for index, relation in entries
        if isinstance(relation.left, BoundaryRef | DurableBoundaryRef)
        and editor._resolve_boundary(relation.left) == seam
    )


def _remove_relations(
    editor: GraphEditor,
    binary: frozenset[int],
    polyadic: frozenset[int],
) -> None:
    binary_mapping = {
        old: old - sum(index < old for index in binary)
        for old in range(len(editor._relations))
        if old not in binary
    }
    polyadic_mapping = {
        old: old - sum(index < old for index in polyadic)
        for old in range(len(editor._polyadic_relations))
        if old not in polyadic
    }
    step = editor._current_displacement(
        relations=binary_mapping,
        polyadic_relations=polyadic_mapping,
        departed_relations=binary,
        departed_polyadic_relations=polyadic,
    )
    editor._layers = [_remap_layer(layer, step) for layer in editor._layers]
    editor._relations = [
        relation
        for index, relation in enumerate(editor._relations)
        if index not in binary
    ]
    editor._polyadic_relations = [
        relation
        for index, relation in enumerate(editor._polyadic_relations)
        if index not in polyadic
    ]
    editor._advance_displacement(step)


def _validate_split_policies(policies: RegroupPolicies) -> None:
    """Refuse merge-only dependency actions on a split."""
    if (
        policies.relations
        or policies.layers
        or policies.attributes
        or policies.container_values is not ReplacementAction.FOLLOW
        or policies.seam_content is not ReplacementAction.FOLLOW
    ):
        raise GraphValidationError(
            "container split accepts only the clock regroup policy"
        )


def _split_temporary(  # noqa: PLR0915 -- one atomic ordered split
    temporary: GraphEditor,
    container: ItemRef | DurableItemRef,
    at: int,
    containment: QualifiedName,
    new_container: Item,
    side: Literal["before", "after"],
    policies: RegroupPolicies,
    restoration: RegroupRestoration | None,
) -> tuple[DetachmentReport, SubtreeCorrespondence]:
    if side not in {"before", "after"}:
        raise GraphValidationError("split side must be 'before' or 'after'")
    if isinstance(at, bool) or not isinstance(at, int):
        raise GraphValidationError("split position must be an integer")
    if not isinstance(new_container, Item):
        raise TypeError("new_container must be an Item")
    original = temporary._resolve_item(container)
    _validate_split_policies(policies)
    _, instances = temporary._containment_instances(containment)
    membership_index = instances.get(original)
    if membership_index is None:
        raise GraphValidationError(
            f"container {str(original)!r} has no {str(containment)!r} membership"
        )
    membership = temporary._polyadic_relations[membership_index]
    children = temporary._resolved_containment_targets(membership_index)
    if at < 1 or at >= len(children):
        raise GraphValidationError(
            f"split position {at} must be inside the container's {len(children)} children"
        )
    if new_container.durable_id is not None:
        temporary._require_unused_durable_id(new_container.durable_id)
    child_seam = BoundaryRef(children[at].tier, children[at].index)
    clock_bindings = (
        _split_clock_bindings(temporary, original.tier, child_seam)
        if policies.clock is not None
        else ()
    )

    incoming = _ordered_memberships(temporary, original)
    insert_index = original.index + (1 if side == "after" else 0)
    member = temporary._member(original.tier, "container split")
    old_count = len(member.items)
    item_mapping = {
        old: old if old < insert_index else old + 1 for old in range(old_count)
    }
    boundary_mapping = {
        old: old if old <= insert_index else old + 1 for old in range(old_count + 1)
    }
    temporary._restructure(
        member,
        [*member.items[:insert_index], new_container, *member.items[insert_index:]],
        item_mapping,
        "container split",
        boundary_mapping=boundary_mapping,
    )
    original_after = ItemRef(
        original.tier, original.index + (1 if side == "before" else 0)
    )
    new_after = ItemRef(original.tier, insert_index)

    # Tier insertion already displaced every structural endpoint. Add the new
    # sister immediately beside the original in every incoming containment.
    for _, (index, position) in incoming.items():
        relation = temporary._polyadic_relations[index]
        displaced_position = position
        targets = list(relation.targets)
        insertion = displaced_position + (1 if side == "after" else 0)
        targets.insert(insertion, _stable_item(temporary, new_after))
        temporary._polyadic_relations[index] = replace(relation, targets=tuple(targets))

    membership = temporary._polyadic_relations[membership_index]
    left_targets = membership.targets[:at]
    right_targets = membership.targets[at:]
    original_targets, new_targets = (
        (left_targets, right_targets)
        if side == "after"
        else (right_targets, left_targets)
    )
    temporary._polyadic_relations[membership_index] = replace(
        membership, targets=original_targets
    )
    new_membership = PolyadicRelationInstance(
        containment,
        (_stable_item(temporary, new_after),),
        new_targets,
    )
    if restoration is None:
        _insert_polyadic(temporary, membership_index + 1, new_membership)

    seam = BoundaryRef(original.tier, max(original_after.index, new_after.index))
    for declaration, template in () if restoration is not None else clock_bindings:
        insertion = len(temporary._relations)
        last = -1
        for index, binding_relation in enumerate(temporary._relations):
            if binding_relation.declaration != declaration or not isinstance(
                binding_relation.left, BoundaryRef | DurableBoundaryRef
            ):
                continue
            boundary = temporary._resolve_boundary(binding_relation.left)
            if boundary.tier != original.tier:
                continue
            last = index
            if boundary.index > seam.index:
                insertion = index
                break
        else:  # pragma: no cover - a complete relation has a later outer boundary
            if last >= 0:
                insertion = last + 1
        stable_seam = anchored_boundary(temporary.freeze(), seam)
        _insert_binary(
            temporary,
            insertion,
            RelationInstance(declaration, stable_seam, template.right),
        )

    if restoration is not None:
        restored_member = temporary._member(original.tier, "container restoration")
        restored_member.items[original_after.index] = restoration.survivor_item
        temporary._relations = _restore_relation_positions(
            temporary._relations,
            restoration.relation_count,
            restoration.relations,
            restoration.removed_relation_positions,
            "binary",
        )
        temporary._polyadic_relations = _restore_relation_positions(
            temporary._polyadic_relations,
            restoration.polyadic_relation_count,
            restoration.polyadic_relations,
            restoration.removed_polyadic_relation_positions,
            "polyadic",
        )
        layers = {layer.name: layer for layer in temporary._layers}
        for name, facts in restoration.layers:
            current = layers.get(name)
            if current is None:
                raise GraphValidationError("regroup restoration names a missing layer")
            layers[name] = replace(current, facts=facts)
        temporary._layers = [layers[layer.name] for layer in temporary._layers]
        for position, reference, attributes in sorted(restoration.seam_values):
            if position > len(temporary._boundary_values):
                raise GraphValidationError(
                    "regroup restoration boundary position is outside the result"
                )
            temporary._boundary_values.insert(position, Boundary(reference, attributes))

    functional = (
        (new_after, original_after) if side == "before" else (original_after, new_after)
    )
    correspondence = SubtreeCorrespondence(
        {original: functional},
        {original: (original_after,)},
    )
    temporary.freeze()
    return DetachmentReport(), correspondence


def _boundary_subject_at(
    editor: GraphEditor, subject: object, boundary: BoundaryRef
) -> bool:
    return (
        isinstance(subject, BoundaryRef | DurableBoundaryRef)
        and editor._resolve_boundary(subject) == boundary
    )


def _layer_action(policies: RegroupPolicies, name: LayerName) -> ReplacementAction:
    return policies.layers.get(name, ReplacementAction.FOLLOW)


def _relation_action(
    policies: RegroupPolicies, name: QualifiedName
) -> ReplacementAction:
    return policies.relations.get(name, ReplacementAction.FOLLOW)


def _merge_temporary(  # noqa: PLR0915 -- one atomic dependency-routed merge
    temporary: GraphEditor,
    first: ItemRef | DurableItemRef,
    second: ItemRef | DurableItemRef,
    survivor: ItemRef | DurableItemRef,
    containment: QualifiedName,
    policies: RegroupPolicies,
) -> tuple[DetachmentReport, SubtreeCorrespondence, RegroupRestoration]:
    one = temporary._resolve_item(first)
    two = temporary._resolve_item(second)
    kept = temporary._resolve_item(survivor)
    if one == two:
        raise GraphValidationError("container merge requires two distinct containers")
    if kept not in {one, two}:
        raise GraphValidationError("merge survivor must be one of the two containers")
    if one.tier != two.tier or abs(one.index - two.index) != 1:
        raise GraphValidationError(
            "container merge requires adjacent same-tier containers"
        )
    left, right = sorted((one, two), key=lambda item: item.index)
    removed = right if kept == left else left
    _, instances = temporary._containment_instances(containment)
    left_membership = instances.get(left)
    right_membership = instances.get(right)
    if left_membership is None or right_membership is None:
        raise GraphValidationError(
            "both merged containers need named containment memberships"
        )
    left_children = temporary._resolved_containment_targets(left_membership)
    right_children = temporary._resolved_containment_targets(right_membership)
    left_target_endpoints = temporary._polyadic_relations[left_membership].targets
    right_target_endpoints = temporary._polyadic_relations[right_membership].targets
    if (
        not left_children
        or not right_children
        or len({item.tier for item in (*left_children, *right_children)}) != 1
        or any(
            following.index != preceding.index + 1
            for preceding, following in zip(
                (*left_children, *right_children),
                (*left_children, *right_children)[1:],
                strict=False,
            )
        )
    ):
        raise GraphValidationError(
            "merged containers do not meet at a contiguous child-tier seam"
        )

    left_parents = _ordered_memberships(temporary, left)
    right_parents = _ordered_memberships(temporary, right)
    if set(left_parents) != set(right_parents):
        raise GraphValidationError(
            "merged containers do not share every containment parent"
        )
    for name in left_parents:
        left_site = left_parents[name]
        right_site = right_parents[name]
        if left_site[0] != right_site[0] or right_site[1] != left_site[1] + 1:
            raise GraphValidationError(
                "merged containers must occur in left-right order under the same parent"
            )

    seam = BoundaryRef(left.tier, right.index)
    seam_values = tuple(
        (index, boundary)
        for index, boundary in enumerate(temporary._boundary_values)
        if temporary._resolve_boundary(boundary.reference) == seam
    )
    seam_facts = tuple(
        (layer.name, fact)
        for layer in temporary._layers
        for fact in layer.facts
        if _boundary_subject_at(temporary, fact.subject, seam)
    )
    seam_relation_indexes = frozenset(
        index
        for index, relation in enumerate(temporary._relations)
        if any(
            _boundary_subject_at(temporary, endpoint, seam)
            for endpoint in (relation.left, relation.right)
        )
    )
    seam_polyadic_indexes = frozenset(
        index
        for index, relation in enumerate(temporary._polyadic_relations)
        if any(
            _boundary_subject_at(temporary, endpoint, seam)
            for endpoint in (*relation.sources, *relation.targets)
        )
    )
    clock_drops = (
        _merge_clock_bindings(temporary, left.tier, seam)
        if policies.clock is ClockRebindingPolicy.DROP_TO_PROVISIONAL
        else frozenset()
    )
    nonclock_seam_relations = seam_relation_indexes - clock_drops
    if (
        seam_values or seam_facts
    ) and policies.seam_content is not ReplacementAction.DROP:
        raise GraphValidationError(
            "container merge retires independent seam content; name seam_content='drop'"
        )
    for index in nonclock_seam_relations:
        binary_relation = temporary._relations[index]
        if (
            _relation_action(policies, binary_relation.declaration)
            is not ReplacementAction.DROP
        ):
            raise GraphValidationError(
                "container merge retires a seam relation; name its action 'drop'"
            )
    for index in seam_polyadic_indexes:
        polyadic_relation = temporary._polyadic_relations[index]
        if (
            _relation_action(policies, polyadic_relation.declaration)
            is not ReplacementAction.DROP
        ):
            raise GraphValidationError(
                "container merge retires a seam relation; name its action 'drop'"
            )

    before_relations = tuple(enumerate(temporary._relations))
    before_polyadic = tuple(enumerate(temporary._polyadic_relations))
    before_layers = {layer.name: layer.facts for layer in temporary._layers}
    survivor_item = _item(temporary, kept)
    removed_item = _item(temporary, removed)
    merged_attributes = list(survivor_item.attributes)
    by_name = {value.name: value for value in survivor_item.attributes}
    for value in removed_item.attributes:
        action = policies.attributes.get(value.name, policies.container_values)
        prior_value = by_name.get(value.name)
        if action is ReplacementAction.DROP:
            continue
        if prior_value is None:
            merged_attributes.append(value)
            by_name[value.name] = value
        elif prior_value != value:
            raise GraphValidationError(
                f"container attribute {str(value.name)!r} conflicts across merge; "
                "name its action 'drop'"
            )
    temporary._member(kept.tier, "container merge").items[kept.index] = replace(
        survivor_item, attributes=tuple(merged_attributes)
    )

    stable_kept = _stable_item(temporary, kept)
    dropped_binary: set[int] = set(seam_relation_indexes)
    dropped_polyadic: set[int] = set(seam_polyadic_indexes)
    dropped_binary.update(
        index
        for index, relation in before_relations
        if _relation_action(policies, relation.declaration) is ReplacementAction.DROP
        and any(
            _endpoint_item(temporary, endpoint) == removed
            for endpoint in (relation.left, relation.right)
        )
    )
    dropped_polyadic.update(
        index
        for index, relation in before_polyadic
        if _relation_action(policies, relation.declaration) is ReplacementAction.DROP
        and any(
            _endpoint_item(temporary, endpoint) == removed
            for endpoint in (*relation.sources, *relation.targets)
        )
    )
    nonsurvivor_membership = right_membership if kept == left else left_membership
    dropped_polyadic.add(nonsurvivor_membership)

    for index, binary_relation in enumerate(temporary._relations):
        if index in dropped_binary:
            continue
        temporary._relations[index] = replace(
            binary_relation,
            left=_replace_item_endpoint(
                temporary, binary_relation.left, removed, stable_kept
            ),
            right=_replace_item_endpoint(
                temporary, binary_relation.right, removed, stable_kept
            ),
        )
    survivor_membership = left_membership if kept == left else right_membership
    for index, polyadic_relation in enumerate(temporary._polyadic_relations):
        if index in dropped_polyadic:
            continue
        if index == survivor_membership:
            temporary._polyadic_relations[index] = replace(
                polyadic_relation,
                targets=(*left_target_endpoints, *right_target_endpoints),
            )
            continue
        sources = tuple(
            _replace_item_endpoint(temporary, endpoint, removed, stable_kept)
            for endpoint in polyadic_relation.sources
        )
        targets = tuple(
            endpoint
            for endpoint in polyadic_relation.targets
            if _endpoint_item(temporary, endpoint) != removed
        )
        targets = tuple(
            _replace_item_endpoint(temporary, endpoint, removed, stable_kept)
            for endpoint in targets
        )
        temporary._polyadic_relations[index] = replace(
            polyadic_relation, sources=sources, targets=targets
        )

    dropped_relation_subjects: set[object] = {
        *(RelationInstanceRef(index) for index in dropped_binary),
        *(PolyadicInstanceRef(index) for index in dropped_polyadic),
        *(
            DurableRelationRef(cast(str, temporary._relations[index].durable_id))
            for index in dropped_binary
            if temporary._relations[index].durable_id is not None
        ),
        *(
            DurablePolyadicRef(
                cast(str, temporary._polyadic_relations[index].durable_id)
            )
            for index in dropped_polyadic
            if temporary._polyadic_relations[index].durable_id is not None
        ),
    }
    dropped_facts: list[tuple[LayerName, LayerFact]] = []
    rewritten_layers: list[Layer] = []
    for layer in temporary._layers:
        facts: list[LayerFact] = []
        keys: dict[tuple[object, QualifiedName], LayerFact] = {}
        for fact in layer.facts:
            drop = fact.subject in dropped_relation_subjects or _boundary_subject_at(
                temporary, fact.subject, seam
            )
            subject: LayerSubject = fact.subject
            if (
                isinstance(subject, ItemRef | DurableItemRef)
                and temporary._resolve_item(subject) == removed
            ):
                subject = stable_kept
            fact_candidate = LayerFact(subject, fact.value)
            key = (fact_candidate.subject, fact_candidate.value.name)
            existing_fact = keys.get(key)
            if drop:
                dropped_facts.append((layer.name, fact))
                continue
            if existing_fact is not None:
                if existing_fact.value == fact_candidate.value:
                    continue
                if _layer_action(policies, layer.name) is ReplacementAction.DROP:
                    dropped_facts.append((layer.name, fact))
                    continue
                raise GraphValidationError(
                    "container merge creates conflicting facts; name the layer action 'drop'"
                )
            keys[key] = fact_candidate
            facts.append(fact_candidate)
        rewritten_layers.append(replace(layer, facts=tuple(facts)))
    temporary._layers = rewritten_layers
    seam_positions = {index for index, _ in seam_values}
    temporary._boundary_values = [
        boundary
        for index, boundary in enumerate(temporary._boundary_values)
        if index not in seam_positions
    ]

    dropped_relations = tuple(
        (RelationInstanceRef(index), relation)
        for index, relation in before_relations
        if index in dropped_binary
    ) + tuple(
        (PolyadicInstanceRef(index), relation)
        for index, relation in before_polyadic
        if index in dropped_polyadic
    )
    _remove_relations(temporary, frozenset(dropped_binary), frozenset(dropped_polyadic))

    member = temporary._member(removed.tier, "container merge")
    old_count = len(member.items)
    mapping = {
        old: old if old < removed.index else old - 1
        for old in range(old_count)
        if old != removed.index
    }
    boundary_mapping = {
        boundary: boundary if boundary <= left.index else boundary - 1
        for boundary in range(old_count + 1)
        if boundary != right.index
    }
    temporary._restructure(
        member,
        [*member.items[: removed.index], *member.items[removed.index + 1 :]],
        mapping,
        "container merge",
        boundary_mapping=boundary_mapping,
    )
    after_kept = ItemRef(
        kept.tier, kept.index - (1 if removed.index < kept.index else 0)
    )
    correspondence = SubtreeCorrespondence(
        {left: (after_kept,), right: (after_kept,)},
        {kept: (after_kept,)},
    )
    final_graph = temporary.freeze()

    changed_relations = tuple(
        entry
        for entry in before_relations
        if entry[0] in dropped_binary
        or entry[0] >= len(final_graph.relations)
        or final_graph.relations[entry[0]] != entry[1]
    )
    changed_polyadic = tuple(
        entry
        for entry in before_polyadic
        if entry[0] in dropped_polyadic
        or entry[0] >= len(final_graph.polyadic_relations)
        or final_graph.polyadic_relations[entry[0]] != entry[1]
    )
    changed_layers = tuple(
        (name, facts)
        for name, facts in before_layers.items()
        if next(layer.facts for layer in final_graph.layers if layer.name == name)
        != facts
    )
    restoration = RegroupRestoration(
        survivor_item,
        nonsurvivor_membership,
        len(before_relations),
        len(before_polyadic),
        changed_relations,
        changed_polyadic,
        tuple(sorted(dropped_binary)),
        tuple(sorted(dropped_polyadic)),
        changed_layers,
        tuple(
            (index, boundary.reference, boundary.attributes)
            for index, boundary in seam_values
        ),
    )
    report = DetachmentReport(
        items=((removed, removed_item),),
        relations=dropped_relations,
        facts=tuple(dropped_facts),
        boundary_values=tuple(
            (boundary.reference, value)
            for _, boundary in seam_values
            for value in boundary.attributes
        ),
    )
    return report, correspondence, restoration


def _split_outcome(
    graph: Graph,
    container: ItemRef | DurableItemRef,
    at: int,
    containment: QualifiedName,
    new_container: Item = _EMPTY_ITEM,
    side: Literal["before", "after"] = "after",
    policies: RegroupPolicies | None = None,
    restoration: RegroupRestoration | None = None,
) -> _RegroupOutcome:
    selected = _policies(policies)
    temporary = GraphEditor(graph)
    report, correspondence = _split_temporary(
        temporary,
        container,
        at,
        containment,
        new_container,
        side,
        selected,
        restoration,
    )
    return _RegroupOutcome(
        temporary.freeze(), temporary.displacement(), report, correspondence
    )


def _merge_outcome(
    graph: Graph,
    first: ItemRef | DurableItemRef,
    second: ItemRef | DurableItemRef,
    survivor: ItemRef | DurableItemRef,
    containment: QualifiedName,
    policies: RegroupPolicies | None = None,
) -> _RegroupOutcome:
    selected = _policies(policies)
    temporary = GraphEditor(graph)
    report, correspondence, restoration = _merge_temporary(
        temporary, first, second, survivor, containment, selected
    )
    return _RegroupOutcome(
        temporary.freeze(),
        temporary.displacement(),
        report,
        correspondence,
        restoration,
    )


def split_container(
    graph: Graph,
    container: ItemRef | DurableItemRef,
    at: int,
    containment: QualifiedName,
    new_container: Item = _EMPTY_ITEM,
    side: Literal["before", "after"] = "after",
    policies: RegroupPolicies | None = None,
    restoration: RegroupRestoration | None = None,
) -> EditResult:
    """Split one container at an interior child seam and report withdrawals."""
    outcome = _split_outcome(
        graph,
        container,
        at,
        containment,
        new_container,
        side,
        policies,
        restoration,
    )
    return EditResult(outcome.graph, outcome.report)


def merge_containers(
    graph: Graph,
    first: ItemRef | DurableItemRef,
    second: ItemRef | DurableItemRef,
    survivor: ItemRef | DurableItemRef,
    containment: QualifiedName,
    policies: RegroupPolicies | None = None,
) -> EditResult:
    """Merge adjacent sister containers and report every withdrawn link."""
    outcome = _merge_outcome(graph, first, second, survivor, containment, policies)
    return EditResult(outcome.graph, outcome.report)


__all__ = [
    "RegroupPolicies",
    "RegroupRestoration",
    "merge_containers",
    "split_container",
]
