"""Replace containment descendants while making dependent-reference policy explicit."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType
from typing import cast

from tiergraph.core import (
    Attribute,
    BipartiteRelationDeclaration,
    BoundaryRef,
    Displacement,
    DurableBoundaryRef,
    DurableItemRef,
    DurablePolyadicRef,
    DurableRelationRef,
    Graph,
    GraphEditor,
    GraphValidationError,
    Item,
    ItemRef,
    JsonValue,
    Layer,
    LayerFact,
    LayerName,
    LayerSubject,
    PolyadicInstanceRef,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    QualifiedName,
    RelationEndpointKind,
    RelationEndpointRef,
    RelationInstance,
    RelationInstanceRef,
    RelationSideDeclaration,
    SealDeclaration,
)


class ReplacementAction(StrEnum):
    """Choose how a dependency on replaced content is handled."""

    ABANDON = "abandon"
    FOLLOW = "follow"
    SPLIT = "split"
    DROP = "drop"
    TRIM = "trim"


@dataclass(frozen=True, slots=True)
class Subtree:
    """Name a rooted containment subtree in a validated graph."""

    graph: Graph
    root: ItemRef | DurableItemRef

    def __post_init__(self) -> None:
        """Require the public graph and item-reference shapes."""
        if not isinstance(self.graph, Graph):
            raise TypeError("subtree graph must be a Graph")
        if not isinstance(self.root, ItemRef | DurableItemRef):
            raise TypeError("subtree root must be an item reference")


@dataclass(frozen=True, slots=True)
class SubtreeCorrespondence:
    """Align old descendant holes with zero, one, or several new positions.

    References on the right address :attr:`Subtree.graph`. Multiple old items
    may name one new item for a merge, and one old item may name several new
    items for a split. Missing old items have no counterpart. Each source
    reference names one alignment hole. ``identity_correspondence`` optionally
    marks holes whose aligned items retain identity; an absent entry claims
    functional correspondence only.
    """

    items: Mapping[ItemRef, tuple[ItemRef, ...]] = field(default_factory=dict)
    identity_correspondence: Mapping[ItemRef, tuple[ItemRef, ...]] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        """Detach the mapping and require ordered target tuples."""
        detached: dict[ItemRef, tuple[ItemRef, ...]] = {}
        for source, targets in self.items.items():
            if not isinstance(source, ItemRef):
                raise TypeError("correspondence sources must be item references")
            values = tuple(targets)
            if any(not isinstance(target, ItemRef) for target in values):
                raise TypeError("correspondence targets must be item references")
            detached[source] = values
        identities: dict[ItemRef, tuple[ItemRef, ...]] = {}
        for source, targets in self.identity_correspondence.items():
            if not isinstance(source, ItemRef):
                raise TypeError(
                    "identity correspondence sources must be item references"
                )
            values = tuple(targets)
            if any(not isinstance(target, ItemRef) for target in values):
                raise TypeError(
                    "identity correspondence targets must be item references"
                )
            if detached.get(source) != values:
                raise ValueError(
                    "identity correspondence must match its alignment hole"
                )
            identities[source] = values
        object.__setattr__(self, "items", MappingProxyType(detached))
        object.__setattr__(
            self, "identity_correspondence", MappingProxyType(identities)
        )

    def to_data(self) -> dict[str, JsonValue]:
        """Return named alignment holes with optional identity claims."""
        holes: list[JsonValue] = []
        for source, targets in sorted(
            self.items.items(), key=lambda pair: (str(pair[0].tier), pair[0].index)
        ):
            hole: dict[str, JsonValue] = {
                "name": str(source),
                "old": source.to_data(),
                "new": [target.to_data() for target in targets],
            }
            identities = self.identity_correspondence.get(source)
            if identities is not None:
                hole["identity_correspondence"] = [
                    target.to_data() for target in identities
                ]
            holes.append(hole)
        return {"holes": holes}


@dataclass(frozen=True, slots=True)
class ReplacementPolicies:
    """Declare replacement defaults and per-carrier dependency actions.

    Abandonment is the default for facts and boundary values. Crossing
    relations instead carry through correspondence; ``drop`` and polyadic
    ``trim`` are explicit fallbacks for a missing correspondence. ``correspond``
    enables a stable local per-tier alignment for unmatched items with equal
    content; an explicit correspondence is applied first. Per-relation and
    per-layer actions override ``default``.
    ``follow`` requires exactly one counterpart for every referenced item.
    ``split`` duplicates a dependency over all declared counterparts.
    """

    default: ReplacementAction = ReplacementAction.ABANDON
    correspond: bool = False
    correspondence: SubtreeCorrespondence = field(default_factory=SubtreeCorrespondence)
    relations: Mapping[QualifiedName, ReplacementAction] = field(default_factory=dict)
    layers: Mapping[LayerName, ReplacementAction] = field(default_factory=dict)
    insertion_points: Mapping[QualifiedName, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalize enum spellings and detach policy mappings."""
        object.__setattr__(self, "default", ReplacementAction(self.default))
        object.__setattr__(
            self,
            "relations",
            MappingProxyType(
                {
                    name: ReplacementAction(value)
                    for name, value in self.relations.items()
                }
            ),
        )
        object.__setattr__(
            self,
            "layers",
            MappingProxyType(
                {name: ReplacementAction(value) for name, value in self.layers.items()}
            ),
        )
        points: dict[QualifiedName, int] = {}
        for tier, index in self.insertion_points.items():
            if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                raise ValueError(
                    "replacement insertion points must be nonnegative integers"
                )
            points[tier] = index
        object.__setattr__(self, "insertion_points", MappingProxyType(points))

    @classmethod
    def corresponding(
        cls,
        correspondence: SubtreeCorrespondence | None = None,
        *,
        relations: Mapping[QualifiedName, ReplacementAction] | None = None,
        layers: Mapping[LayerName, ReplacementAction] | None = None,
        insertion_points: Mapping[QualifiedName, int] | None = None,
    ) -> ReplacementPolicies:
        """Return an organization default that follows local correspondence.

        A speech-processing profile can use this default to retain provenance
        on corresponding alternatives while plain tiergraph editing continues
        to abandon dependencies unless the caller opts in.
        """
        return cls(
            ReplacementAction.FOLLOW,
            True,
            SubtreeCorrespondence() if correspondence is None else correspondence,
            {} if relations is None else relations,
            {} if layers is None else layers,
            {} if insertion_points is None else insertion_points,
        )


def _abandons(action: ReplacementAction) -> bool:
    """Return whether a general dependency policy withdraws its content."""
    return action in {ReplacementAction.ABANDON, ReplacementAction.DROP}


@dataclass(frozen=True, slots=True)
class DetachedDependency:
    """Name one dependency omitted or removed by replacement.

    Ordinary carrier coordinates address the graph passed to the replacement
    function before editing. A
    ``donor_relations`` coordinate addresses :attr:`Subtree.graph` and names a
    binary or polyadic relation that crossed the supplied subtree edge, so it
    could not be copied. ``polyadic_endpoints`` names one endpoint removed by
    an explicit trim.
    """

    carrier: str
    index: int
    declaration: QualifiedName | None = None
    layer: LayerName | None = None
    subject: LayerSubject | None = None
    tier: QualifiedName | None = None
    endpoint: RelationEndpointRef | None = None
    endpoint_side: str | None = None
    endpoint_index: int | None = None

    def to_data(self) -> dict[str, JsonValue]:
        """Return a stable, JSON-compatible description."""
        data: dict[str, JsonValue] = {
            "carrier": self.carrier,
            "index": self.index,
        }
        if self.declaration is not None:
            data["declaration"] = self.declaration.to_data()
        if self.layer is not None:
            data["layer"] = self.layer.to_data()
        if self.subject is not None:
            data["subject"] = _subject_data(self.subject)
        if self.tier is not None:
            data["tier"] = self.tier.to_data()
        if self.endpoint is not None:
            data["endpoint"] = _subject_data(self.endpoint)
        if self.endpoint_side is not None:
            data["endpoint_side"] = self.endpoint_side
        if self.endpoint_index is not None:
            data["endpoint_index"] = self.endpoint_index
        return data


@dataclass(frozen=True, slots=True)
class DetachmentReport:
    """Snapshot graph content withdrawn by one derived edit.

    Source items follow tier and item order, source relation instances follow
    their carrier order, source facts follow canonical layer and fact order,
    and boundary values follow boundary and attribute order. Donor relations
    and facts have separate fields because their coordinates address the
    supplied subtree graph rather than the edited graph. Entries retain their
    original references and complete typed values, including durable
    identifiers where present.
    """

    items: tuple[tuple[ItemRef, Item], ...] = ()
    relations: tuple[
        tuple[
            RelationInstanceRef | PolyadicInstanceRef,
            RelationInstance | PolyadicRelationInstance,
        ],
        ...,
    ] = ()
    facts: tuple[tuple[LayerName, LayerFact], ...] = ()
    dependencies: tuple[DetachedDependency, ...] = ()
    boundary_values: tuple[tuple[BoundaryRef | DurableBoundaryRef, Attribute], ...] = ()
    donor_relations: tuple[
        tuple[
            RelationInstanceRef | PolyadicInstanceRef,
            RelationInstance | PolyadicRelationInstance,
        ],
        ...,
    ] = ()
    donor_facts: tuple[tuple[LayerName, LayerFact], ...] = ()

    def to_data(self) -> dict[str, JsonValue]:
        """Return the ordered detached content as JSON-compatible data."""

        def relation_data(
            values: tuple[
                tuple[
                    RelationInstanceRef | PolyadicInstanceRef,
                    RelationInstance | PolyadicRelationInstance,
                ],
                ...,
            ],
        ) -> list[JsonValue]:
            """Encode one graph's binary and polyadic relation snapshots."""
            return [
                {
                    "carrier": (
                        "polyadic_relations"
                        if isinstance(reference, PolyadicInstanceRef)
                        else "relations"
                    ),
                    "index": reference.index,
                    "instance": relation.to_data(),
                }
                for reference, relation in values
            ]

        def fact_data(
            values: tuple[tuple[LayerName, LayerFact], ...],
        ) -> list[JsonValue]:
            """Encode one graph's ordered layer-fact snapshots."""
            result: list[JsonValue] = []
            for layer_name, fact in values:
                encoded = Layer(layer_name, (fact,)).to_data()
                facts = cast(list[dict[str, JsonValue]], encoded["facts"])
                result.append({"layer": layer_name.to_data(), "fact": facts[0]})
            return result

        return {
            "items": [
                {"reference": reference.to_data(), "item": item.to_data()}
                for reference, item in self.items
            ],
            "relations": relation_data(self.relations),
            "facts": fact_data(self.facts),
            "dependencies": [item.to_data() for item in self.dependencies],
            "boundary_values": [
                {"reference": reference.to_data(), "value": value.to_data()}
                for reference, value in self.boundary_values
            ],
            "donor_relations": relation_data(self.donor_relations),
            "donor_facts": fact_data(self.donor_facts),
        }


@dataclass(frozen=True, slots=True)
class EditResult:
    """Return an edited graph together with content withdrawn by the edit."""

    graph: Graph
    report: DetachmentReport


@dataclass(frozen=True, slots=True)
class _ReplacementOutcome:
    graph: Graph
    displacement: Displacement
    detached: tuple[DetachedDependency, ...]
    report: DetachmentReport | None
    correspondence: SubtreeCorrespondence
    new_items: Mapping[ItemRef, ItemRef]


@dataclass(frozen=True, slots=True)
class _Shape:
    root: ItemRef
    descendants: frozenset[ItemRef]
    binary: frozenset[int]
    polyadic: frozenset[int]


def replace_subtree(
    graph: Graph,
    root: ItemRef | DurableItemRef,
    containment: QualifiedName | Iterable[QualifiedName],
    new: Subtree,
    policies: ReplacementPolicies | None = None,
) -> EditResult:
    """Replace one root's descendants and report withdrawn graph content.

    The root, its incoming containment link, its attributes, and its layer facts
    remain live. The default abandons dependencies on descendants. The result
    reports the abandoned items, relation instances, facts, and boundary values
    whether or not a journal is used. Binary and polyadic relations crossing
    the replaced edge carry through the old-to-new correspondence in declared
    endpoint order. A missing or ambiguous endpoint refuses unless ``drop`` or,
    for a polyadic relation, ``trim`` is named. Donor relations crossing the
    supplied subtree edge and their facts are reported as complete content but
    not copied. Correspondence is explicitly opt-in, and boundary-subject facts
    use the same correspondence as boundary values.
    """
    outcome = _replace_subtree(
        graph, root, containment, new, policies, capture_report=True
    )
    return EditResult(outcome.graph, cast(DetachmentReport, outcome.report))


def swap_subtrees(
    graph: Graph,
    first: ItemRef | DurableItemRef,
    second: ItemRef | DurableItemRef,
    containment: QualifiedName | Iterable[QualifiedName],
    first_policies: ReplacementPolicies | None = None,
    second_policies: ReplacementPolicies | None = None,
) -> EditResult:
    """Exchange descendant sets and report every dependency or value lost."""
    outcome = _swap_subtrees(
        graph,
        first,
        second,
        containment,
        first_policies,
        second_policies,
        capture_report=True,
    )
    return EditResult(outcome.graph, cast(DetachmentReport, outcome.report))


def _swap_subtrees(
    graph: Graph,
    first: ItemRef | DurableItemRef,
    second: ItemRef | DurableItemRef,
    containment: QualifiedName | Iterable[QualifiedName],
    first_policies: ReplacementPolicies | None,
    second_policies: ReplacementPolicies | None,
    *,
    capture_report: bool = False,
) -> _ReplacementOutcome:
    names = _containment_names(containment)
    selected = set(names)
    left = _shape(graph, first, selected)
    right = _shape(graph, second, selected)
    if left.root == right.root:
        raise GraphValidationError("subtree swap roots must be distinct")
    if left.root in right.descendants or right.root in left.descendants:
        raise GraphValidationError("subtree swap roots must not contain one another")
    temporary_graph, temporary_ids = _temporary_subtree(graph, right)
    first_outcome = _replace_subtree(
        graph,
        first,
        names,
        Subtree(temporary_graph, right.root),
        first_policies,
        report_donor_edges=False,
    )
    if isinstance(second, DurableItemRef):
        current_second: ItemRef | DurableItemRef = second
    else:
        current_second = first_outcome.displacement.items[right.root]
    current_right = _shape(first_outcome.graph, current_second, selected)
    translated_second_policies = _translated_swap_policies(
        second_policies, first_outcome.displacement
    )
    second_outcome = _replace_subtree(
        first_outcome.graph,
        current_second,
        names,
        Subtree(graph, first),
        translated_second_policies,
        report_donor_edges=False,
    )
    current_temporary = {
        source: second_outcome.graph.resolve_item(DurableItemRef(durable_id))
        for source, durable_id in temporary_ids.items()
    }
    final_correspondence = SubtreeCorrespondence(
        {
            current_temporary[source]: (source,)
            for source in sorted(
                right.descendants, key=lambda item: (str(item.tier), item.index)
            )
        }
    )
    current_left = second_outcome.displacement.items[
        first_outcome.displacement.items[left.root]
    ]
    final_outcome = _replace_subtree(
        second_outcome.graph,
        current_left,
        names,
        Subtree(graph, right.root),
        ReplacementPolicies.corresponding(final_correspondence),
        report_donor_edges=False,
    )
    detached = _ordered_detached(
        (
            *_external_detached(graph, left, first_outcome.detached),
            *_source_dependencies(
                graph,
                first_outcome.graph,
                first_outcome.displacement,
                _external_detached(
                    first_outcome.graph, current_right, second_outcome.detached
                ),
            ),
        )
    )
    displacement = first_outcome.displacement.then(second_outcome.displacement).then(
        final_outcome.displacement
    )
    swap_correspondence = {
        **{
            source: (final_outcome.displacement.items[current],)
            for source, current in second_outcome.new_items.items()
            if source in left.descendants
        },
        **{
            source: (target,)
            for source, target in final_outcome.new_items.items()
            if source in right.descendants
        },
    }
    return _ReplacementOutcome(
        final_outcome.graph,
        displacement,
        detached,
        (
            _detachment_report(
                graph,
                dependencies=detached,
                boundary_values=_removed_boundary_values(
                    graph, final_outcome.graph, displacement
                ),
            )
            if capture_report
            else None
        ),
        SubtreeCorrespondence(swap_correspondence, swap_correspondence),
        final_outcome.new_items,
    )


def _translated_swap_policies(
    policies: ReplacementPolicies | None, displacement: Displacement
) -> ReplacementPolicies | None:
    """Move second-side correspondence sources into the intermediate graph."""
    if policies is None:
        return None
    items = {
        displacement.items.get(source, source): targets
        for source, targets in policies.correspondence.items.items()
    }
    identities = {
        displacement.items.get(source, source): targets
        for source, targets in policies.correspondence.identity_correspondence.items()
    }
    return replace(
        policies,
        correspondence=SubtreeCorrespondence(items, identities),
    )


def _source_dependencies(
    source: Graph,
    current: Graph,
    displacement: Displacement,
    dependencies: Iterable[DetachedDependency],
) -> tuple[DetachedDependency, ...]:
    """Translate dependency coordinates from an intermediate graph to source."""
    inverse_items = {target: origin for origin, target in displacement.items.items()}
    inverse_boundaries = {
        target: origin for origin, target in displacement.boundaries.items()
    }
    inverse_relations = {
        target: origin for origin, target in displacement.relations.items()
    }
    inverse_polyadic = {
        target: origin for origin, target in displacement.polyadic_relations.items()
    }

    def source_subject(subject: LayerSubject) -> LayerSubject:
        """Recover a source coordinate from one surviving current subject."""
        if isinstance(subject, ItemRef):
            return inverse_items[subject]
        if isinstance(subject, BoundaryRef):
            return inverse_boundaries[subject]
        if isinstance(subject, RelationInstanceRef):
            return RelationInstanceRef(inverse_relations[subject.index])
        if isinstance(subject, PolyadicInstanceRef):
            return PolyadicInstanceRef(inverse_polyadic[subject.index])
        return subject

    result: list[DetachedDependency] = []
    source_layers = {layer.name: layer for layer in source.layers}
    current_layers = {layer.name: layer for layer in current.layers}
    for dependency in dependencies:
        if dependency.carrier == "relations":
            result.append(
                replace(dependency, index=inverse_relations[dependency.index])
            )
        elif dependency.carrier == "polyadic_relations":
            result.append(replace(dependency, index=inverse_polyadic[dependency.index]))
        elif dependency.carrier == "boundary_values" and dependency.tier is not None:
            source_boundary = inverse_boundaries[
                BoundaryRef(dependency.tier, dependency.index)
            ]
            result.append(
                replace(
                    dependency,
                    index=source_boundary.index,
                    tier=source_boundary.tier,
                )
            )
        elif dependency.carrier == "layer" and dependency.layer is not None:
            current_fact = current_layers[dependency.layer].facts[dependency.index]
            original_subject = source_subject(current_fact.subject)
            original_fact = LayerFact(original_subject, current_fact.value)
            fact_index = source_layers[dependency.layer].facts.index(original_fact)
            result.append(
                replace(
                    dependency,
                    index=fact_index,
                    subject=original_subject,
                )
            )
        else:
            result.append(dependency)
    return tuple(result)


def _temporary_subtree(graph: Graph, shape: _Shape) -> tuple[Graph, dict[ItemRef, str]]:
    """Build a valid source graph whose copied descendant ids cannot collide."""
    used = {
        item.durable_id
        for tier in graph.tiers
        for item in tier.items
        if item.durable_id is not None
    }
    temporary_ids: dict[ItemRef, str] = {}
    serial = 1
    for reference in sorted(
        shape.descendants, key=lambda item: (str(item.tier), item.index)
    ):
        while (candidate := f"subtree-swap-{serial}") in used:
            serial += 1
        temporary_ids[reference] = candidate
        used.add(candidate)
        serial += 1
    tiers = tuple(
        replace(
            tier,
            items=tuple(
                Item(
                    temporary_ids.get(
                        ItemRef(tier.declaration.name, index), item.durable_id
                    ),
                    item.attributes,
                )
                for index, item in enumerate(tier.items)
            ),
        )
        for tier in graph.tiers
    )
    relations = tuple(
        replace(graph.relations[index], durable_id=None)
        for index in sorted(shape.binary)
    )
    polyadic = tuple(
        replace(graph.polyadic_relations[index], durable_id=None)
        for index in sorted(shape.polyadic)
    )
    return (
        replace(
            graph,
            tiers=tiers,
            relations=relations,
            polyadic_relations=polyadic,
            boundary_values=(),
            seals=(),
            layers=(),
        ),
        temporary_ids,
    )


def _external_detached(
    graph: Graph,
    shape: _Shape,
    dependencies: Iterable[DetachedDependency],
) -> tuple[DetachedDependency, ...]:
    """Exclude subtree-owned content that a swap copies to its new location."""
    result: list[DetachedDependency] = []
    old_runs = _tier_runs(graph, shape.descendants)
    mapping = {item: item for item in shape.descendants}
    members = frozenset({shape.root, *shape.descendants})
    binary = {
        index: (index,)
        for index, relation in enumerate(graph.relations)
        if all(
            _source_endpoint_inside(graph, endpoint, members)
            for endpoint in (relation.left, relation.right)
        )
    }
    polyadic = {
        index: (index,)
        for index, relation in enumerate(graph.polyadic_relations)
        if all(
            _source_endpoint_inside(graph, endpoint, members)
            for endpoint in (*relation.sources, *relation.targets)
        )
    }
    layers = {layer.name: layer for layer in graph.layers}
    for dependency in dependencies:
        if dependency.carrier == "boundary_values" and dependency.tier is not None:
            if _boundary_touches(
                BoundaryRef(dependency.tier, dependency.index), old_runs
            ):
                continue
        elif dependency.carrier == "layer" and dependency.layer is not None:
            layer = layers[dependency.layer]
            fact = layer.facts[dependency.index]
            if (
                _copy_source_fact_subject(
                    graph,
                    graph,
                    fact.subject,
                    shape,
                    shape.root,
                    mapping,
                    binary,
                    polyadic,
                )
                is not None
            ):
                continue
        result.append(dependency)
    return tuple(result)


def _containment_names(
    containment: QualifiedName | Iterable[QualifiedName],
) -> tuple[QualifiedName, ...]:
    if isinstance(containment, QualifiedName):
        return (containment,)
    if isinstance(containment, (str, bytes, Mapping)):
        raise TypeError("containment must be a qualified name or ordered iterable")
    names = tuple(containment)
    if not names or any(not isinstance(name, QualifiedName) for name in names):
        raise TypeError("containment must contain qualified names")
    if len(set(names)) != len(names):
        raise ValueError("containment declarations must be unique")
    return names


def _validated_containment(graph: Graph, names: tuple[QualifiedName, ...]) -> None:
    declarations = {item.name: item for item in graph.relation_declarations}
    for name in names:
        declaration = declarations.get(name)
        if isinstance(declaration, BipartiteRelationDeclaration):
            valid = (
                declaration.single_parent
                and declaration.acyclic
                and declaration.left_endpoint is RelationEndpointKind.ITEM
                and declaration.right_endpoint is RelationEndpointKind.ITEM
            )
        elif isinstance(declaration, PolyadicRelationDeclaration):
            valid = (
                declaration.single_parent
                and declaration.acyclic
                and declaration.sources.endpoint_kinds == (RelationEndpointKind.ITEM,)
                and declaration.targets.endpoint_kinds == (RelationEndpointKind.ITEM,)
                and declaration.sources.maximum == 1
            )
        else:
            valid = False
        if not valid:
            raise GraphValidationError(
                f"replacement containment {str(name)!r} must be an acyclic, "
                "single-parent item relation with one source"
            )


def _item_endpoint(graph: Graph, endpoint: RelationEndpointRef) -> ItemRef:
    if isinstance(endpoint, DurableBoundaryRef):
        raise GraphValidationError("replacement containment endpoints must be items")
    return graph.resolve_item(endpoint)


def _shape(
    graph: Graph, root: ItemRef | DurableItemRef, names: set[QualifiedName]
) -> _Shape:
    resolved = graph.resolve_item(root)
    descendants: set[ItemRef] = set()
    binary: set[int] = set()
    polyadic: set[int] = set()
    pending = [resolved]
    while pending:
        parent = pending.pop()
        for index, binary_relation in enumerate(graph.relations):
            if binary_relation.declaration not in names:
                continue
            source = _item_endpoint(graph, binary_relation.left)
            if source != parent:
                continue
            child = _item_endpoint(graph, binary_relation.right)
            binary.add(index)
            if child not in descendants:  # pragma: no branch - validated acyclic
                descendants.add(child)
                pending.append(child)
        for index, polyadic_relation in enumerate(graph.polyadic_relations):
            if polyadic_relation.declaration not in names:
                continue
            sources = tuple(
                _item_endpoint(graph, item) for item in polyadic_relation.sources
            )
            if sources != (parent,):
                continue
            polyadic.add(index)
            for endpoint in polyadic_relation.targets:
                child = _item_endpoint(graph, endpoint)
                if child not in descendants:  # pragma: no branch - validated acyclic
                    descendants.add(child)
                    pending.append(child)
    return _Shape(
        resolved, frozenset(descendants), frozenset(binary), frozenset(polyadic)
    )


def _tier_runs(
    graph: Graph, references: frozenset[ItemRef]
) -> dict[QualifiedName, tuple[int, ...]]:
    result: dict[QualifiedName, tuple[int, ...]] = {}
    for tier in graph.tiers:
        indexes = tuple(
            index
            for index in range(len(tier.items))
            if ItemRef(tier.declaration.name, index) in references
        )
        if indexes:
            if indexes != tuple(range(indexes[0], indexes[-1] + 1)):
                raise GraphValidationError(
                    f"replacement descendants on tier {str(tier.declaration.name)!r} "
                    "must form one contiguous run"
                )
            result[tier.declaration.name] = indexes
    return result


def _local_correspondence(
    old: Graph,
    old_shape: _Shape,
    new: Graph,
    new_shape: _Shape,
    explicit: SubtreeCorrespondence,
    enabled: bool,
) -> dict[ItemRef, tuple[ItemRef, ...]]:
    result = dict(explicit.items)
    for source, targets in result.items():
        if source not in old_shape.descendants:
            raise GraphValidationError(
                "correspondence source is outside the old subtree"
            )
        if any(target not in new_shape.descendants for target in targets):
            raise GraphValidationError(
                "correspondence target is outside the new subtree"
            )
        if any(target.tier != source.tier for target in targets):
            raise GraphValidationError("correspondence must stay within one tier")
    if not enabled:
        return result
    used = {target for targets in result.values() for target in targets}
    for tier in {item.tier for item in old_shape.descendants}:
        old_items = sorted(
            (item for item in old_shape.descendants if item.tier == tier),
            key=lambda item: item.index,
        )
        new_items = sorted(
            (item for item in new_shape.descendants if item.tier == tier),
            key=lambda item: item.index,
        )
        cursor = 0
        for source in old_items:
            if source in result:
                continue
            value = old._tiers_by_name[tier].items[source.index]
            match = next(
                (
                    target
                    for target in new_items[cursor:]
                    if target not in used
                    and new._tiers_by_name[tier].items[target.index].attributes
                    == value.attributes
                ),
                None,
            )
            if match is not None:
                result[source] = (match,)
                used.add(match)
                cursor = new_items.index(match) + 1
    return result


def _replace_subtree(  # noqa: PLR0915 -- one atomic dependency-ordered edit
    graph: Graph,
    root: ItemRef | DurableItemRef,
    containment: QualifiedName | Iterable[QualifiedName],
    new: Subtree,
    policies: ReplacementPolicies | None,
    *,
    capture_report: bool = False,
    report_donor_edges: bool = True,
) -> _ReplacementOutcome:
    names = _containment_names(containment)
    _validated_containment(graph, names)
    _validated_containment(new.graph, names)
    selected = set(names)
    old_shape = _shape(graph, root, selected)
    new_shape = _shape(new.graph, new.root, selected)
    if old_shape.root.tier != new_shape.root.tier:
        raise GraphValidationError("old and new subtree roots must have the same tier")
    old_declarations = {
        item.name: item for item in graph.relation_declarations if item.name in selected
    }
    new_declarations = {
        item.name: item
        for item in new.graph.relation_declarations
        if item.name in selected
    }
    if old_declarations != new_declarations:
        raise GraphValidationError("old and new containment declarations differ")
    chosen = ReplacementPolicies() if policies is None else policies
    old_runs = _tier_runs(graph, old_shape.descendants)
    new_runs = _tier_runs(new.graph, new_shape.descendants)
    insertions: dict[QualifiedName, int] = {}
    for tier in set(old_runs) | set(new_runs):
        member = graph._tiers_by_name.get(tier)
        if member is None:  # pragma: no cover - checked for a clearer diagnostic
            raise GraphValidationError(
                f"replacement tier {str(tier)!r} is not declared"
            )
        if tier in old_runs:
            insertions[tier] = old_runs[tier][0]
        else:
            insertion = chosen.insertion_points.get(tier, len(member.items))
            if insertion > len(member.items):
                raise GraphValidationError(
                    f"replacement insertion point {insertion} is outside tier "
                    f"{str(tier)!r}"
                )
            insertions[tier] = insertion
    source_to_target: dict[ItemRef, ItemRef] = {}
    for tier, indexes in new_runs.items():
        start = insertions[tier]
        for offset, source_index in enumerate(indexes):
            source_to_target[ItemRef(tier, source_index)] = ItemRef(
                tier, start + offset
            )
    raw_correspondence = _local_correspondence(
        graph,
        old_shape,
        new.graph,
        new_shape,
        chosen.correspondence,
        chosen.correspond,
    )
    for descendant in old_shape.descendants:
        raw_correspondence.setdefault(descendant, ())
    correspondence = {
        source: tuple(source_to_target[target] for target in targets)
        for source, targets in raw_correspondence.items()
    }
    identity_correspondence = {
        source: tuple(source_to_target[target] for target in targets)
        for source, targets in chosen.correspondence.identity_correspondence.items()
    }

    editor = GraphEditor(graph)
    detached: list[DetachedDependency] = []

    old_binary = [
        (index, relation)
        for index, relation in enumerate(graph.relations)
        if index in old_shape.binary
        or _relation_touches(graph, relation, old_shape.descendants)
    ]
    old_polyadic = [
        (index, relation)
        for index, relation in enumerate(graph.polyadic_relations)
        if index in old_shape.polyadic
        or _polyadic_touches(graph, relation, old_shape.descendants)
    ]
    removed_binary = {index for index, _ in old_binary}
    removed_polyadic = {index for index, _ in old_polyadic}

    held_facts: list[tuple[LayerName, int, LayerFact, ReplacementAction]] = []
    for layer in graph.layers:
        for fact_index, fact in enumerate(layer.facts):
            affected = _subject_touches(
                graph,
                fact.subject,
                old_shape.descendants,
                removed_binary,
                removed_polyadic,
            )
            if not affected:
                continue
            action = chosen.layers.get(layer.name, chosen.default)
            editor.remove_fact(layer.name, fact.subject, fact.value.name)
            held_facts.append((layer.name, fact_index, fact, action))
            is_relation_subject = isinstance(
                fact.subject,
                RelationInstanceRef
                | PolyadicInstanceRef
                | DurableRelationRef
                | DurablePolyadicRef,
            )
            if _abandons(action) and not is_relation_subject:
                detached.append(
                    DetachedDependency(
                        "layer", fact_index, layer=layer.name, subject=fact.subject
                    )
                )

    held_boundaries: list[
        tuple[BoundaryRef | DurableBoundaryRef, BoundaryRef, tuple[Attribute, ...]]
    ] = []
    for stored_boundary in graph.boundary_values:
        coordinate = graph.resolve_boundary(stored_boundary.reference)
        if _boundary_touches(coordinate, old_runs):
            held_boundaries.append(
                (stored_boundary.reference, coordinate, stored_boundary.attributes)
            )
            for value in stored_boundary.attributes:
                editor.remove_attribute(stored_boundary.reference, value.name)
            if _abandons(chosen.default):
                detached.append(
                    DetachedDependency(
                        "boundary_values", coordinate.index, tier=coordinate.tier
                    )
                )

    for index, _ in reversed(old_binary):
        editor.remove_relation(RelationInstanceRef(index))
    for index, _ in reversed(old_polyadic):
        editor.remove_relation(PolyadicInstanceRef(index))

    for tier, indexes in sorted(old_runs.items(), key=lambda pair: str(pair[0])):
        editor.remove_items(tier, indexes[0], len(indexes))
    for tier, indexes in sorted(new_runs.items(), key=lambda pair: str(pair[0])):
        items = tuple(new.graph._tiers_by_name[tier].items[index] for index in indexes)
        editor.insert_items(tier, insertions[tier], items)
    item_images = editor.displacement().items
    inserted_graph = editor.freeze()

    additions_binary: list[tuple[int, RelationInstance, int]] = []
    additions_polyadic: list[tuple[int, PolyadicRelationInstance, int]] = []
    for index, binary_relation in old_binary:
        if index in old_shape.binary:
            continue
        crossing_policy = _crossing_policy(chosen, binary_relation.declaration)
        binary_carried = _carry_binary_relation(
            graph,
            inserted_graph,
            index,
            binary_relation,
            old_shape.descendants,
            correspondence,
            item_images,
            crossing_policy,
        )
        if binary_carried is None:
            detached.append(
                DetachedDependency(
                    "relations", index, declaration=binary_relation.declaration
                )
            )
        else:
            additions_binary.append((index, binary_carried, index))
    for index, polyadic_relation in old_polyadic:
        if index in old_shape.polyadic:
            continue
        crossing_policy = _crossing_policy(chosen, polyadic_relation.declaration)
        polyadic_carried, removed_endpoints = _carry_polyadic_relation(
            graph,
            inserted_graph,
            index,
            polyadic_relation,
            old_shape.descendants,
            correspondence,
            item_images,
            crossing_policy,
        )
        if polyadic_carried is None:
            detached.append(
                DetachedDependency(
                    "polyadic_relations",
                    index,
                    declaration=polyadic_relation.declaration,
                )
            )
        else:
            additions_polyadic.append((index, polyadic_carried, index))
            detached.extend(removed_endpoints)

    source_members = frozenset({new_shape.root, *new_shape.descendants})
    source_binary: list[int] = []
    donor_binary: list[int] = []
    for index, source_binary_relation in enumerate(new.graph.relations):
        inside = tuple(
            _source_endpoint_inside(new.graph, endpoint, source_members)
            for endpoint in (
                source_binary_relation.left,
                source_binary_relation.right,
            )
        )
        if all(inside):
            source_binary.append(index)
        elif report_donor_edges and any(inside):
            donor_binary.append(index)
            detached.append(
                DetachedDependency(
                    "donor_relations",
                    index,
                    declaration=source_binary_relation.declaration,
                )
            )
    source_polyadic: list[int] = []
    donor_polyadic: list[int] = []
    for index, source_polyadic_relation in enumerate(new.graph.polyadic_relations):
        inside = tuple(
            _source_endpoint_inside(new.graph, endpoint, source_members)
            for endpoint in (
                *source_polyadic_relation.sources,
                *source_polyadic_relation.targets,
            )
        )
        if all(inside):
            source_polyadic.append(index)
        elif report_donor_edges and any(inside):
            donor_polyadic.append(index)
            detached.append(
                DetachedDependency(
                    "donor_relations",
                    index,
                    declaration=source_polyadic_relation.declaration,
                )
            )
    binary_anchor = min(old_shape.binary, default=len(graph.relations))
    for index in source_binary:
        binary_relation = new.graph.relations[index]
        additions_binary.append(
            (
                binary_anchor,
                _copy_binary_source_relation(
                    new.graph,
                    inserted_graph,
                    binary_relation,
                    new_shape.root,
                    item_images[old_shape.root],
                    source_to_target,
                ),
                -index - 1,
            )
        )
    polyadic_anchor = min(old_shape.polyadic, default=len(graph.polyadic_relations))
    for index in source_polyadic:
        polyadic_relation = new.graph.polyadic_relations[index]
        additions_polyadic.append(
            (
                polyadic_anchor,
                _copy_polyadic_source_relation(
                    new.graph,
                    inserted_graph,
                    polyadic_relation,
                    new_shape.root,
                    item_images[old_shape.root],
                    source_to_target,
                ),
                -index - 1,
            )
        )

    all_binary_images = _insert_relations(editor, additions_binary, removed_binary)
    all_polyadic_images = _insert_relations(
        editor, additions_polyadic, removed_polyadic
    )
    binary_images = {
        index: images for index, images in all_binary_images.items() if index >= 0
    }
    polyadic_images = {
        index: images for index, images in all_polyadic_images.items() if index >= 0
    }
    source_binary_images = {
        -index - 1: images for index, images in all_binary_images.items() if index < 0
    }
    source_polyadic_images = {
        -index - 1: images for index, images in all_polyadic_images.items() if index < 0
    }

    for _, boundary_coordinate, attributes in held_boundaries:
        targets = _boundary_correspondence(
            boundary_coordinate, correspondence, old_runs, insertions, new_runs
        )
        boundary_carried = False
        if chosen.default is ReplacementAction.FOLLOW and len(targets) == 1:
            for value in attributes:
                editor.set_attribute(targets[0], value)
            boundary_carried = True
        elif chosen.default is ReplacementAction.SPLIT and targets:
            for target in targets:
                for value in attributes:
                    editor.set_attribute(target, value)
            boundary_carried = True
        if not boundary_carried and not _abandons(chosen.default):
            detached.append(
                DetachedDependency(
                    "boundary_values",
                    boundary_coordinate.index,
                    tier=boundary_coordinate.tier,
                )
            )

    for layer_name, fact_index, fact, action in held_facts:
        subjects = _fact_subjects(
            graph,
            fact.subject,
            correspondence,
            binary_images,
            polyadic_images,
            action,
            old_runs,
            insertions,
            new_runs,
        )
        is_relation_subject = isinstance(
            fact.subject,
            RelationInstanceRef
            | PolyadicInstanceRef
            | DurableRelationRef
            | DurablePolyadicRef,
        )
        if not subjects and (not _abandons(action) or is_relation_subject):
            detached.append(
                DetachedDependency(
                    "layer",
                    fact_index,
                    layer=layer_name,
                    subject=fact.subject,
                )
            )
        for subject in subjects:
            editor.put_fact(layer_name, LayerFact(subject, fact.value))

    for stored_boundary in new.graph.boundary_values:
        coordinate = new.graph.resolve_boundary(stored_boundary.reference)
        boundary_run = new_runs.get(coordinate.tier)
        if (
            boundary_run is None
            or not boundary_run[0] <= coordinate.index <= boundary_run[-1] + 1
        ):
            continue
        target = BoundaryRef(
            coordinate.tier,
            insertions[coordinate.tier] + coordinate.index - boundary_run[0],
        )
        for value in stored_boundary.attributes:
            editor.set_attribute(target, value)

    target_layer_names = {layer.name for layer in graph.layers}
    donor_facts: list[tuple[LayerName, LayerFact]] = []
    for layer in new.graph.layers:
        copied: list[LayerFact] = []
        for fact_index, fact in enumerate(layer.facts):
            copied_subject = _copy_source_fact_subject(
                new.graph,
                inserted_graph,
                fact.subject,
                new_shape,
                old_shape.root,
                source_to_target,
                source_binary_images,
                source_polyadic_images,
            )
            if copied_subject is not None:
                copied.append(LayerFact(copied_subject, fact.value))
            elif _donor_relation_fact(
                new.graph, fact.subject, set(donor_binary), set(donor_polyadic)
            ):
                donor_facts.append((layer.name, fact))
                detached.append(
                    DetachedDependency(
                        "donor_layer",
                        fact_index,
                        layer=layer.name,
                        subject=fact.subject,
                    )
                )
        if copied and layer.name not in target_layer_names:
            editor.add_layer(layer.name)
            target_layer_names.add(layer.name)
        for fact in copied:
            editor.put_fact(layer.name, fact)

    candidate = editor.freeze()
    SealDeclaration("replace subtree", graph, candidate).check_seals()
    candidate_facts = {layer.name: frozenset(layer.facts) for layer in candidate.layers}
    for layer_name, fact_index, fact, action in held_facts:
        subjects = _fact_subjects(
            graph,
            fact.subject,
            correspondence,
            binary_images,
            polyadic_images,
            action,
            old_runs,
            insertions,
            new_runs,
        )
        if subjects and any(
            LayerFact(subject, fact.value) not in candidate_facts[layer_name]
            for subject in subjects
        ):
            detached.append(
                DetachedDependency(
                    "layer", fact_index, layer=layer_name, subject=fact.subject
                )
            )
    candidate_boundary_values = {
        candidate.resolve_boundary(boundary.reference): boundary.attributes
        for boundary in candidate.boundary_values
    }
    overwritten_boundary_values: list[
        tuple[BoundaryRef | DurableBoundaryRef, Attribute]
    ] = []
    for source_reference, boundary_coordinate, attributes in held_boundaries:
        targets = _boundary_correspondence(
            boundary_coordinate, correspondence, old_runs, insertions, new_runs
        )
        if chosen.default is ReplacementAction.FOLLOW:
            targets = targets if len(targets) == 1 else ()
        elif _abandons(chosen.default):
            targets = ()
        if not targets:
            continue
        overwritten_boundary_values.extend(
            (source_reference, value)
            for value in attributes
            if any(
                value not in candidate_boundary_values.get(target, ())
                for target in targets
            )
        )
    ordered_detached = _ordered_detached(detached)
    detached_binary = {
        dependency.index
        for dependency in ordered_detached
        if dependency.carrier == "relations"
    }
    detached_polyadic = {
        dependency.index
        for dependency in ordered_detached
        if dependency.carrier == "polyadic_relations"
    }
    detached_facts = {
        (dependency.layer, dependency.index)
        for dependency in ordered_detached
        if dependency.carrier == "layer" and dependency.layer is not None
    }
    report = (
        _detachment_report(
            graph,
            items=old_shape.descendants,
            binary=old_shape.binary | detached_binary,
            polyadic=old_shape.polyadic | detached_polyadic,
            facts=detached_facts,
            dependencies=ordered_detached,
            boundary_values=overwritten_boundary_values,
            donor_relations=(
                *(
                    (RelationInstanceRef(index), new.graph.relations[index])
                    for index in donor_binary
                ),
                *(
                    (PolyadicInstanceRef(index), new.graph.polyadic_relations[index])
                    for index in donor_polyadic
                ),
            ),
            donor_facts=donor_facts,
        )
        if capture_report
        else None
    )
    return _ReplacementOutcome(
        candidate,
        editor.displacement(),
        ordered_detached,
        report,
        SubtreeCorrespondence(correspondence, identity_correspondence),
        MappingProxyType(source_to_target),
    )


def _relation_touches(
    graph: Graph, relation: RelationInstance, descendants: frozenset[ItemRef]
) -> bool:
    return any(
        _endpoint_touches(graph, endpoint, descendants)
        for endpoint in (relation.left, relation.right)
    )


def _polyadic_touches(
    graph: Graph, relation: PolyadicRelationInstance, descendants: frozenset[ItemRef]
) -> bool:
    return any(
        _endpoint_touches(graph, endpoint, descendants)
        for endpoint in (*relation.sources, *relation.targets)
    )


def _endpoint_touches(
    graph: Graph,
    endpoint: RelationEndpointRef,
    descendants: frozenset[ItemRef],
) -> bool:
    """Recognize item endpoints and boundary endpoints anchored to descendants."""
    if isinstance(endpoint, DurableBoundaryRef):
        return (
            isinstance(endpoint.anchor, DurableItemRef)
            and graph.resolve_item(endpoint.anchor) in descendants
        )
    return graph.resolve_item(endpoint) in descendants


def _endpoint_item(graph: Graph, endpoint: RelationEndpointRef) -> ItemRef | None:
    if isinstance(endpoint, DurableBoundaryRef):
        return None
    return graph.resolve_item(endpoint)


def _boundary_owner(graph: Graph, endpoint: DurableBoundaryRef) -> ItemRef | None:
    """Return the item anchoring a durable boundary, if it has one."""
    if isinstance(endpoint.anchor, DurableItemRef):
        return graph.resolve_item(endpoint.anchor)
    return None


def _source_endpoint_inside(
    graph: Graph,
    endpoint: RelationEndpointRef,
    members: frozenset[ItemRef],
) -> bool:
    """Say whether an endpoint is wholly owned by a copied subtree."""
    if isinstance(endpoint, DurableBoundaryRef):
        return _boundary_owner(graph, endpoint) in members
    return graph.resolve_item(endpoint) in members


def _copy_source_endpoint(
    source: Graph,
    target: Graph,
    endpoint: RelationEndpointRef,
    source_root: ItemRef,
    target_root: ItemRef,
    mapping: Mapping[ItemRef, ItemRef],
) -> RelationEndpointRef:
    """Map one endpoint from the supplied subtree into the edited graph."""
    if not isinstance(endpoint, DurableBoundaryRef):
        return _mapped_new_endpoint(source, endpoint, source_root, target_root, mapping)
    owner = _boundary_owner(source, endpoint)
    if owner is None:
        raise GraphValidationError(
            "a copied subtree relation cannot use a tier-anchored boundary"
        )
    coordinate = target_root if owner == source_root else mapping[owner]
    item = target._tiers_by_name[coordinate.tier].items[coordinate.index]
    if item.durable_id is None:
        raise GraphValidationError(
            "a copied subtree boundary endpoint requires a durable target item"
        )
    return DurableBoundaryRef(DurableItemRef(item.durable_id), endpoint.side)


def _boundary_touches(
    boundary: BoundaryRef, runs: Mapping[QualifiedName, tuple[int, ...]]
) -> bool:
    indexes = runs.get(boundary.tier)
    return indexes is not None and indexes[0] <= boundary.index <= indexes[-1] + 1


def _subject_touches(
    graph: Graph,
    subject: LayerSubject,
    descendants: frozenset[ItemRef],
    binary: set[int],
    polyadic: set[int],
) -> bool:
    if isinstance(subject, ItemRef | DurableItemRef):
        return graph.resolve_item(subject) in descendants
    if isinstance(subject, BoundaryRef | DurableBoundaryRef):
        coordinate = graph.resolve_boundary(subject)
        indexes = tuple(
            item.index for item in descendants if item.tier == coordinate.tier
        )
        return bool(indexes) and min(indexes) <= coordinate.index <= max(indexes) + 1
    if isinstance(subject, RelationInstanceRef):
        return subject.index in binary
    if isinstance(subject, PolyadicInstanceRef):
        return subject.index in polyadic
    if isinstance(subject, DurableRelationRef):
        return any(
            index in binary and relation.durable_id == subject.durable_id
            for index, relation in enumerate(graph.relations)
        )
    if isinstance(subject, DurablePolyadicRef):
        return any(
            index in polyadic and relation.durable_id == subject.durable_id
            for index, relation in enumerate(graph.polyadic_relations)
        )
    return False


def _donor_relation_fact(
    graph: Graph,
    subject: LayerSubject,
    binary: set[int],
    polyadic: set[int],
) -> bool:
    """Recognize a fact whose donor relation was rejected at the subtree edge."""
    if isinstance(subject, RelationInstanceRef):
        return subject.index in binary
    if isinstance(subject, PolyadicInstanceRef):
        return subject.index in polyadic
    if isinstance(subject, DurableRelationRef):
        return any(
            index in binary and relation.durable_id == subject.durable_id
            for index, relation in enumerate(graph.relations)
        )
    if isinstance(subject, DurablePolyadicRef):
        return any(
            index in polyadic and relation.durable_id == subject.durable_id
            for index, relation in enumerate(graph.polyadic_relations)
        )
    return False


def _crossing_policy(
    policies: ReplacementPolicies, declaration: QualifiedName
) -> ReplacementAction | None:
    """Return the explicitly named fallback for an uncarryable crossing."""
    action = policies.relations.get(declaration, policies.default)
    return (
        action if action in {ReplacementAction.DROP, ReplacementAction.TRIM} else None
    )


def _crossing_targets(
    source: Graph,
    target: Graph,
    endpoint: RelationEndpointRef,
    correspondence: Mapping[ItemRef, tuple[ItemRef, ...]],
) -> tuple[RelationEndpointRef, ...]:
    """Resolve one inside endpoint through an old-to-new hole alignment."""
    if isinstance(endpoint, DurableBoundaryRef):
        owner = _boundary_owner(source, endpoint)
        if owner is None:
            return ()
        result: list[RelationEndpointRef] = []
        for coordinate in correspondence.get(owner, ()):
            item = target._tiers_by_name[coordinate.tier].items[coordinate.index]
            if item.durable_id is None:
                return ()
            result.append(
                DurableBoundaryRef(DurableItemRef(item.durable_id), endpoint.side)
            )
        return tuple(result)
    return correspondence.get(source.resolve_item(endpoint), ())


def _crossing_refusal(
    carrier: str,
    index: int,
    declaration: QualifiedName,
    endpoint: RelationEndpointRef,
    reason: str,
    *,
    allow_trim: bool = True,
) -> GraphValidationError:
    """Build the K3 refusal naming both relation and crossing endpoint."""
    return GraphValidationError(
        f"crossing {carrier} relation {str(declaration)!r} at index {index} "
        f"endpoint {endpoint!s} has {reason}; name a DROP policy"
        + (" or TRIM" if carrier == "polyadic" and allow_trim else "")
    )


def _polyadic_crossing_issue(
    target: Graph,
    declaration: PolyadicRelationDeclaration,
    sources: tuple[RelationEndpointRef, ...],
    targets: tuple[RelationEndpointRef, ...],
) -> str | None:
    """Name a local declaration invariant broken by a carried crossing."""

    def side_issue(
        name: str,
        values: tuple[RelationEndpointRef, ...],
        declared: RelationSideDeclaration,
    ) -> str | None:
        """Name an empty or out-of-bounds carried side."""
        if not values:
            return None if declared.allow_empty else f"an empty {name} side"
        if len(values) < declared.minimum or (
            declared.maximum is not None and len(values) > declared.maximum
        ):
            return (
                f"{name} arity {len(values)} outside declared bounds "
                f"{declared.minimum}..{declared.maximum}"
            )
        return None

    issue = side_issue("source", sources, declaration.sources)
    if issue is None:
        issue = side_issue("target", targets, declaration.targets)
    if issue is not None:
        return issue

    def resolved(
        endpoint: RelationEndpointRef,
    ) -> ItemRef | BoundaryRef:
        """Resolve an endpoint for duplicate-invariant checks."""
        if isinstance(endpoint, DurableBoundaryRef):
            return target.resolve_boundary(endpoint)
        return target.resolve_item(endpoint)

    resolved_sources = tuple(resolved(endpoint) for endpoint in sources)
    if declaration.unique_sources and len(set(resolved_sources)) != len(
        resolved_sources
    ):
        return "duplicate sources in a declared unique-source relation"
    resolved_targets = tuple(resolved(endpoint) for endpoint in targets)
    if declaration.distinct_targets and len(set(resolved_targets)) != len(
        resolved_targets
    ):
        return "duplicate declared-distinct targets"
    return None


def _carry_binary_relation(
    source: Graph,
    target: Graph,
    index: int,
    relation: RelationInstance,
    descendants: frozenset[ItemRef],
    correspondence: Mapping[ItemRef, tuple[ItemRef, ...]],
    item_images: Mapping[ItemRef, ItemRef],
    failure_policy: ReplacementAction | None,
) -> RelationInstance | None:
    """Carry one binary crossing or apply its explicit DROP fallback."""
    sides: list[RelationEndpointRef] = []
    for endpoint in (relation.left, relation.right):
        if not _endpoint_touches(source, endpoint, descendants):
            sides.append(_remap_unaffected_endpoint(endpoint, item_images))
            continue
        targets = _crossing_targets(source, target, endpoint, correspondence)
        if len(targets) != 1:
            if failure_policy is ReplacementAction.DROP:
                return None
            reason = "no correspondence" if not targets else "ambiguous correspondence"
            raise _crossing_refusal(
                "binary", index, relation.declaration, endpoint, reason
            )
        sides.append(targets[0])
    return RelationInstance(
        relation.declaration,
        sides[0],
        sides[1],
        relation.durable_id,
        relation.attributes,
    )


def _carry_polyadic_relation(
    source: Graph,
    target: Graph,
    index: int,
    relation: PolyadicRelationInstance,
    descendants: frozenset[ItemRef],
    correspondence: Mapping[ItemRef, tuple[ItemRef, ...]],
    item_images: Mapping[ItemRef, ItemRef],
    failure_policy: ReplacementAction | None,
) -> tuple[
    PolyadicRelationInstance | None,
    tuple[DetachedDependency, ...],
]:
    """Carry a polyadic crossing, flattening each aligned hole in place."""
    removed: list[DetachedDependency] = []

    def side(
        name: str, values: tuple[RelationEndpointRef, ...]
    ) -> tuple[RelationEndpointRef, ...] | None:
        """Carry one declared endpoint sequence or apply its fallback."""
        result: list[RelationEndpointRef] = []
        for endpoint_index, endpoint in enumerate(values):
            if not _endpoint_touches(source, endpoint, descendants):
                result.append(_remap_unaffected_endpoint(endpoint, item_images))
                continue
            targets = _crossing_targets(source, target, endpoint, correspondence)
            if not targets:
                if failure_policy is ReplacementAction.DROP:
                    return None
                if failure_policy is ReplacementAction.TRIM:
                    removed.append(
                        DetachedDependency(
                            "polyadic_endpoints",
                            index,
                            declaration=relation.declaration,
                            endpoint=endpoint,
                            endpoint_side=name,
                            endpoint_index=endpoint_index,
                        )
                    )
                    continue
                raise _crossing_refusal(
                    "polyadic",
                    index,
                    relation.declaration,
                    endpoint,
                    "no correspondence",
                )
            result.extend(targets)
        return tuple(result)

    sources = side("sources", relation.sources)
    if sources is None:
        return None, ()
    targets = side("targets", relation.targets)
    if targets is None:
        return None, ()
    declaration = next(
        item
        for item in target.relation_declarations
        if item.name == relation.declaration
        and isinstance(item, PolyadicRelationDeclaration)
    )
    issue = _polyadic_crossing_issue(target, declaration, sources, targets)
    if issue is not None:
        if failure_policy is ReplacementAction.DROP:
            return None, ()
        endpoint = next(
            endpoint
            for endpoint in (*relation.sources, *relation.targets)
            if _endpoint_touches(source, endpoint, descendants)
        )
        raise _crossing_refusal(
            "polyadic",
            index,
            relation.declaration,
            endpoint,
            issue,
            allow_trim=False,
        )
    return (
        PolyadicRelationInstance(
            relation.declaration,
            sources,
            targets,
            relation.durable_id,
            relation.attributes,
        ),
        tuple(removed),
    )


def _remap_unaffected_endpoint(
    endpoint: RelationEndpointRef,
    item_images: Mapping[ItemRef, ItemRef],
) -> RelationEndpointRef:
    """Carry an unaffected coordinate endpoint through the tier restructure."""
    if isinstance(endpoint, ItemRef):
        return item_images[endpoint]
    if isinstance(endpoint, DurableItemRef | DurableBoundaryRef):
        return endpoint
    raise GraphValidationError(f"unsupported relation endpoint {endpoint!r}")


def _mapped_new_endpoint(
    graph: Graph,
    endpoint: RelationEndpointRef,
    source_root: ItemRef,
    target_root: ItemRef,
    mapping: Mapping[ItemRef, ItemRef],
) -> RelationEndpointRef:
    if isinstance(endpoint, DurableBoundaryRef):
        raise GraphValidationError("containment endpoints must be items")
    coordinate = graph.resolve_item(endpoint)
    if coordinate == source_root:
        return target_root
    try:
        return mapping[coordinate]
    except KeyError as error:
        raise GraphValidationError(
            "new containment relation leaves the subtree"
        ) from error


def _copy_binary_source_relation(
    graph: Graph,
    target: Graph,
    relation: RelationInstance,
    source_root: ItemRef,
    target_root: ItemRef,
    mapping: Mapping[ItemRef, ItemRef],
) -> RelationInstance:
    return RelationInstance(
        relation.declaration,
        _copy_source_endpoint(
            graph, target, relation.left, source_root, target_root, mapping
        ),
        _copy_source_endpoint(
            graph, target, relation.right, source_root, target_root, mapping
        ),
        relation.durable_id,
        relation.attributes,
    )


def _copy_polyadic_source_relation(
    graph: Graph,
    target: Graph,
    relation: PolyadicRelationInstance,
    source_root: ItemRef,
    target_root: ItemRef,
    mapping: Mapping[ItemRef, ItemRef],
) -> PolyadicRelationInstance:
    return PolyadicRelationInstance(
        relation.declaration,
        tuple(
            _copy_source_endpoint(
                graph, target, item, source_root, target_root, mapping
            )
            for item in relation.sources
        ),
        tuple(
            _copy_source_endpoint(
                graph, target, item, source_root, target_root, mapping
            )
            for item in relation.targets
        ),
        relation.durable_id,
        relation.attributes,
    )


def _insert_relations(
    editor: GraphEditor,
    additions: Sequence[tuple[int, RelationInstance | PolyadicRelationInstance, int]],
    removed: set[int],
) -> dict[int, tuple[int, ...]]:
    images: dict[int, list[int]] = {}
    added = 0
    for anchor, instance, origin in sorted(additions, key=lambda value: value[0]):
        before = sum(1 for index in range(anchor) if index not in removed)
        position = before + added
        editor.add_relation(instance, position)
        images.setdefault(origin, []).append(position)
        added += 1
    return {key: tuple(values) for key, values in images.items()}


def _boundary_correspondence(
    boundary: BoundaryRef,
    correspondence: Mapping[ItemRef, tuple[ItemRef, ...]],
    old_runs: Mapping[QualifiedName, tuple[int, ...]],
    insertions: Mapping[QualifiedName, int],
    new_runs: Mapping[QualifiedName, tuple[int, ...]],
) -> tuple[BoundaryRef, ...]:
    indexes = old_runs.get(boundary.tier)
    if indexes is None:
        return ()
    start = indexes[0]
    end = indexes[-1] + 1
    new_count = len(new_runs.get(boundary.tier, ()))
    if boundary.index == start:
        return (BoundaryRef(boundary.tier, insertions[boundary.tier]),)
    if boundary.index == end:
        return (BoundaryRef(boundary.tier, insertions[boundary.tier] + new_count),)
    left = correspondence.get(ItemRef(boundary.tier, boundary.index - 1), ())
    right = correspondence.get(ItemRef(boundary.tier, boundary.index), ())
    candidates = {
        *(BoundaryRef(item.tier, item.index + 1) for item in left),
        *(BoundaryRef(item.tier, item.index) for item in right),
    }
    return tuple(sorted(candidates, key=lambda item: item.index))


def _fact_subjects(
    graph: Graph,
    subject: LayerSubject,
    correspondence: Mapping[ItemRef, tuple[ItemRef, ...]],
    binary: Mapping[int, tuple[int, ...]],
    polyadic: Mapping[int, tuple[int, ...]],
    action: ReplacementAction,
    old_runs: Mapping[QualifiedName, tuple[int, ...]],
    insertions: Mapping[QualifiedName, int],
    new_runs: Mapping[QualifiedName, tuple[int, ...]],
) -> tuple[LayerSubject, ...]:
    relation_subject = isinstance(
        subject,
        RelationInstanceRef
        | PolyadicInstanceRef
        | DurableRelationRef
        | DurablePolyadicRef,
    )
    if _abandons(action) and not relation_subject:
        return ()
    targets: tuple[LayerSubject, ...]
    if isinstance(subject, ItemRef | DurableItemRef):
        targets = correspondence.get(graph.resolve_item(subject), ())
    elif isinstance(subject, BoundaryRef | DurableBoundaryRef):
        targets = _boundary_correspondence(
            graph.resolve_boundary(subject),
            correspondence,
            old_runs,
            insertions,
            new_runs,
        )
    elif isinstance(subject, RelationInstanceRef):
        targets = tuple(
            RelationInstanceRef(index) for index in binary.get(subject.index, ())
        )
    elif isinstance(subject, PolyadicInstanceRef):
        targets = tuple(
            PolyadicInstanceRef(index) for index in polyadic.get(subject.index, ())
        )
    elif isinstance(subject, DurableRelationRef):
        old = next(
            index
            for index, relation in enumerate(graph.relations)
            if relation.durable_id == subject.durable_id
        )
        images = binary.get(old, ())
        targets = (
            (subject,)
            if len(images) == 1
            else tuple(RelationInstanceRef(index) for index in images)
        )
    elif isinstance(subject, DurablePolyadicRef):
        old = next(
            index
            for index, relation in enumerate(graph.polyadic_relations)
            if relation.durable_id == subject.durable_id
        )
        images = polyadic.get(old, ())
        targets = (
            (subject,)
            if len(images) == 1
            else tuple(PolyadicInstanceRef(index) for index in images)
        )
    else:
        targets = ()
    if relation_subject:
        return targets
    if action is ReplacementAction.FOLLOW:
        return targets if len(targets) == 1 else ()
    return targets


def _copy_source_fact_subject(
    source: Graph,
    target: Graph,
    subject: LayerSubject,
    shape: _Shape,
    target_root: ItemRef,
    mapping: Mapping[ItemRef, ItemRef],
    binary: Mapping[int, tuple[int, ...]],
    polyadic: Mapping[int, tuple[int, ...]],
) -> LayerSubject | None:
    """Map a fact owned by copied subtree content into the edited graph."""
    if isinstance(subject, ItemRef | DurableItemRef):
        coordinate = source.resolve_item(subject)
        if coordinate not in shape.descendants:
            return None
        if isinstance(subject, DurableItemRef):
            return subject
        return mapping[coordinate]
    if isinstance(subject, BoundaryRef):
        source_indexes = tuple(
            sorted(
                item.index for item in shape.descendants if item.tier == subject.tier
            )
        )
        if (
            not source_indexes
            or subject.index < source_indexes[0]
            or subject.index > source_indexes[-1] + 1
        ):
            return None
        target_start = min(
            mapping[ItemRef(subject.tier, index)].index for index in source_indexes
        )
        return BoundaryRef(
            subject.tier, target_start + subject.index - source_indexes[0]
        )
    if isinstance(subject, DurableBoundaryRef):
        owner = _boundary_owner(source, subject)
        if owner not in shape.descendants:
            return None
        return _copy_source_endpoint(
            source, target, subject, shape.root, target_root, mapping
        )
    if isinstance(subject, RelationInstanceRef):
        images = binary.get(subject.index, ())
        return RelationInstanceRef(images[0]) if len(images) == 1 else None
    if isinstance(subject, PolyadicInstanceRef):
        images = polyadic.get(subject.index, ())
        return PolyadicInstanceRef(images[0]) if len(images) == 1 else None
    if isinstance(subject, DurableRelationRef):
        index = next(
            (
                index
                for index, relation in enumerate(source.relations)
                if relation.durable_id == subject.durable_id
            ),
            None,
        )
        return subject if index is not None and index in binary else None
    if isinstance(subject, DurablePolyadicRef):
        index = next(
            (
                index
                for index, relation in enumerate(source.polyadic_relations)
                if relation.durable_id == subject.durable_id
            ),
            None,
        )
        return subject if index is not None and index in polyadic else None
    return None


def _ordered_detached(
    dependencies: Iterable[DetachedDependency],
) -> tuple[DetachedDependency, ...]:
    """Deduplicate and order reports without ordering heterogeneous subjects."""
    unique = set(dependencies)
    return tuple(
        sorted(
            unique,
            key=lambda dependency: (
                dependency.carrier,
                dependency.index,
                repr(dependency.declaration),
                repr(dependency.layer),
                repr(dependency.subject),
                repr(dependency.tier),
                dependency.endpoint_side,
                dependency.endpoint_index,
                repr(dependency.endpoint),
            ),
        )
    )


def _detachment_report(
    graph: Graph,
    *,
    items: Iterable[ItemRef] = (),
    binary: Iterable[int] = (),
    polyadic: Iterable[int] = (),
    facts: Iterable[tuple[LayerName, int]] = (),
    dependencies: Iterable[DetachedDependency] = (),
    boundary_values: Iterable[tuple[BoundaryRef | DurableBoundaryRef, Attribute]] = (),
    donor_relations: Iterable[
        tuple[
            RelationInstanceRef | PolyadicInstanceRef,
            RelationInstance | PolyadicRelationInstance,
        ]
    ] = (),
    donor_facts: Iterable[tuple[LayerName, LayerFact]] = (),
) -> DetachmentReport:
    """Snapshot selected source content in the graph's declared order."""
    ordered_dependencies = tuple(dependencies)
    item_sites = set(items)
    binary_sites = set(binary) | {
        dependency.index
        for dependency in ordered_dependencies
        if dependency.carrier == "relations"
    }
    polyadic_sites = set(polyadic) | {
        dependency.index
        for dependency in ordered_dependencies
        if dependency.carrier == "polyadic_relations"
    }
    fact_sites = set(facts) | {
        (dependency.layer, dependency.index)
        for dependency in ordered_dependencies
        if dependency.carrier == "layer" and dependency.layer is not None
    }
    detached_items = tuple(
        (reference, item)
        for tier in graph.tiers
        for index, item in enumerate(tier.items)
        if (reference := ItemRef(tier.declaration.name, index)) in item_sites
    )
    detached_relations: tuple[
        tuple[
            RelationInstanceRef | PolyadicInstanceRef,
            RelationInstance | PolyadicRelationInstance,
        ],
        ...,
    ] = (
        *(
            (RelationInstanceRef(index), relation)
            for index, relation in enumerate(graph.relations)
            if index in binary_sites
        ),
        *(
            (PolyadicInstanceRef(index), relation)
            for index, relation in enumerate(graph.polyadic_relations)
            if index in polyadic_sites
        ),
    )
    detached_facts = tuple(
        (layer.name, fact)
        for layer in graph.layers
        for index, fact in enumerate(layer.facts)
        if (layer.name, index) in fact_sites
    )
    boundary_sites = {
        BoundaryRef(dependency.tier, dependency.index)
        for dependency in ordered_dependencies
        if dependency.carrier == "boundary_values" and dependency.tier is not None
    }
    explicit_boundary_values = set(boundary_values)
    detached_boundary_values = tuple(
        (boundary.reference, value)
        for boundary in graph.boundary_values
        for value in boundary.attributes
        if graph.resolve_boundary(boundary.reference) in boundary_sites
        or (boundary.reference, value) in explicit_boundary_values
    )
    return DetachmentReport(
        detached_items,
        detached_relations,
        detached_facts,
        ordered_dependencies,
        detached_boundary_values,
        tuple(donor_relations),
        tuple(donor_facts),
    )


def _removed_boundary_values(
    source: Graph, target: Graph, displacement: Displacement
) -> tuple[tuple[BoundaryRef | DurableBoundaryRef, Attribute], ...]:
    """Return source boundary values absent at their displaced coordinates."""
    target_by_reference = {
        boundary.reference: boundary.attributes for boundary in target.boundary_values
    }
    target_values = {
        target.resolve_boundary(boundary.reference): boundary.attributes
        for boundary in target.boundary_values
    }
    removed: list[tuple[BoundaryRef | DurableBoundaryRef, Attribute]] = []
    for boundary in source.boundary_values:
        coordinate = source.resolve_boundary(boundary.reference)
        image = displacement.boundaries.get(coordinate)
        if isinstance(boundary.reference, DurableBoundaryRef):
            carried = target_by_reference.get(boundary.reference, ())
        else:
            carried = () if image is None else target_values.get(image, ())
        removed.extend(
            (boundary.reference, value)
            for value in boundary.attributes
            if value not in carried
        )
    return tuple(removed)


def _subject_data(subject: LayerSubject) -> dict[str, JsonValue]:
    """Use the graph's canonical tagged encoding for a layer subject."""
    from tiergraph.core import _layer_subject_data  # noqa: PLC0415

    return _layer_subject_data(subject)


__all__ = [
    "DetachedDependency",
    "DetachmentReport",
    "EditResult",
    "ReplacementAction",
    "ReplacementPolicies",
    "Subtree",
    "SubtreeCorrespondence",
    "replace_subtree",
    "swap_subtrees",
]
