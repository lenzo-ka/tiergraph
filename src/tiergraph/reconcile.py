"""Commit finite paths and reconcile graph structure against a shared clock."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from itertools import pairwise
from typing import TYPE_CHECKING, Any, Protocol, cast

from tiergraph.clock import ClockProfile, anchored_boundary
from tiergraph.core import (
    BipartiteRelationDeclaration,
    BoundaryRef,
    Displacement,
    DurableBoundaryRef,
    DurableItemRef,
    Graph,
    GraphValidationError,
    ItemRef,
    LayerFact,
    LayerName,
    OrphanedSubject,
    PolyadicInstanceRef,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    QualifiedName,
    RefusalStage,
    RelationEndpointKind,
    RelationEndpointRef,
    RelationInstance,
    RelationInstanceRef,
    _boundary_images,
    _resolve_layer_subject,
)
from tiergraph.pathplan import PathPlan
from tiergraph.replacement import (
    EditResult,
    _detachment_report,
    _removed_boundary_values,
)
from tiergraph.traversal import OrderedContainment

if TYPE_CHECKING:
    from tiergraph.edit import Journal


type PathStep = ItemRef | DurableItemRef | str
type PathChoice = Iterable[PathStep] | tuple[tuple[str, ...], ...]
type ContainmentRule = (
    str
    | Callable[
        [tuple[int, int], tuple[tuple[ItemRef, tuple[int, int]], ...]], ItemRef | None
    ]
)


class _DerivedEditor(Protocol):
    """Operations shared by plain and journaled editors."""

    def freeze(self) -> Graph:
        """Return the current immutable graph."""
        ...

    def displacement(self) -> Displacement:
        """Return original-to-current coordinate mappings."""
        ...

    def remove_fact(self, layer: Any, subject: Any, name: QualifiedName) -> object:
        """Remove one layer fact."""
        ...

    def remove_attribute(self, target: Any, name: QualifiedName) -> object:
        """Remove one attribute value."""
        ...

    def remove_relation(self, target: Any) -> object:
        """Remove one relation instance."""
        ...

    def remove_item(self, reference: ItemRef | DurableItemRef) -> object:
        """Remove one item."""
        ...

    def set_endpoints(
        self,
        target: Any,
        sources: RelationEndpointRef | Iterable[RelationEndpointRef],
        targets: RelationEndpointRef | Iterable[RelationEndpointRef],
    ) -> object:
        """Replace one relation instance's endpoints."""
        ...

    def add_relation(
        self,
        instance: RelationInstance | PolyadicRelationInstance,
        at: int | None = None,
    ) -> object:
        """Add one relation instance."""
        ...


def commit_path(
    lattice: PathPlan[Any],
    path: PathChoice,
    *,
    containment: QualifiedName | Iterable[QualifiedName] = (),
    journal: Journal | None = None,
    check_links: bool = False,
) -> EditResult:
    """Keep one complete path and its declared containment substructure.

    ``lattice`` supplies the finite acyclic topology and its source graph.
    ``path`` is either its ordered item references, its ordered labels, or
    non-``None`` one-path provenance returned by :meth:`PathPlan.evaluate`.
    Every unchosen lattice item and descendant outside the retained
    substructure is removed. ``containment`` names ordered, item-only polyadic
    relations that declare source uniqueness and acyclicity; their descendants
    travel with each alternative, and descendants shared with the chosen path
    remain live.

    Item attributes and layer facts are ordinary graph content, so source spans
    and ranked-alternative provenance on chosen units remain unchanged. Facts
    scoped to withdrawn content and references with withdrawn endpoints are
    removed explicitly, never orphaned silently. The detachment report includes
    every removed boundary value in source order. When ``journal`` is supplied,
    the derived edit is recorded as its expanded fact, relation, value, and item
    primitives. A resulting patch therefore retains no reference to the
    request-scoped path plan. Set ``check_links=True`` to audit the complete
    path commitment against its detachment report before returning it.
    """
    if not isinstance(lattice, PathPlan):
        raise TypeError("commit_path lattice must be a PathPlan")
    if not isinstance(check_links, bool):
        raise TypeError("check_links must be a boolean")
    chosen = _chosen_path(lattice, path)
    graph = lattice.declaration.graph
    containments = _containment_traversals(graph, _containment_names(containment))
    selected = _closure(chosen, containments)
    alternatives = set(lattice.items) - set(chosen)
    departing = _closure(tuple(alternatives), containments) - selected

    def apply(editor: _DerivedEditor) -> None:
        """Remove every alternative-exclusive item and its dependencies."""
        _remove_items(editor, departing)

    def checked_result(result: Graph, displacement: Displacement) -> EditResult:
        """Build and optionally audit the complete derived-edit account."""
        report = _detachment_report(
            graph,
            items=displacement.departed_items,
            binary=displacement.departed_relations,
            polyadic=displacement.departed_polyadic_relations,
            facts=_detached_fact_sites(graph, result, displacement),
            boundary_values=_removed_boundary_values(graph, result, displacement),
        )
        account = EditResult(result, report)
        if check_links:
            from tiergraph.edit import link_ledger  # noqa: PLC0415

            link_ledger(
                graph,
                result,
                (report, displacement, None),
                operation="commit_path",
            )
        return account

    if check_links and journal is not None:
        staged, staged_displacement = _apply_derived(graph, None, apply)
        checked_result(staged, staged_displacement)
    result, displacement = _apply_derived(graph, journal, apply)
    return checked_result(result, displacement)


def retime(
    profile: ClockProfile,
    tier: QualifiedName,
    alignment: Sequence[int],
    *,
    offset: int = 0,
    journal: Journal | None = None,
) -> Graph:
    """Rebind every boundary of one timed tier to exact clock positions.

    ``alignment`` contains one integral clock-boundary index per tier boundary,
    hence one more entry than the tier has items. ``offset`` is added to every
    supplied index. The resulting positions must be in range and monotone.
    Existing binding instances are updated in place, retaining their durable
    identities, attributes, layer facts, and global relation positions.

    The operation changes structural clock bindings only; stored item start and
    duration values are not rewritten. It uses the profile's declared clock and
    binding relation and validates a new profile before publishing the graph, so
    stored values must still satisfy that profile. It does not apply a tolerance:
    callers should store external times as integral samples or milliseconds and
    put tolerance in a distance calculation. A journal records the endpoint
    changes as ordinary expanded primitives.
    """
    if not isinstance(profile, ClockProfile):
        raise TypeError("retime profile must be a ClockProfile")
    if not isinstance(tier, QualifiedName):
        raise TypeError("retime tier must be a QualifiedName")
    if isinstance(offset, bool) or not isinstance(offset, int):
        raise TypeError("retime offset must be an integer")
    positions = tuple(alignment)
    if any(
        isinstance(value, bool) or not isinstance(value, int) for value in positions
    ):
        raise TypeError("retime alignment must contain only integers")
    graph = profile.graph
    member = graph._tiers_by_name.get(tier)
    if member is None:
        raise GraphValidationError(
            f"retime tier {str(tier)!r} is not declared", RefusalStage.REFERENCE
        )
    if tier == profile.clock_tier:
        raise GraphValidationError("retime cannot bind the clock tier to itself")
    binding = profile.binding_relation
    if binding is None:
        raise GraphValidationError("retime requires a full clock profile")
    if not profile.is_timed(tier):
        raise GraphValidationError(f"retime tier {str(tier)!r} is untimed")
    expected = len(member.items) + 1
    if len(positions) != expected:
        raise GraphValidationError(
            f"retime tier {str(tier)!r} needs {expected} boundary positions; "
            f"got {len(positions)}"
        )
    shifted = tuple(value + offset for value in positions)
    clock_size = len(graph._tiers_by_name[profile.clock_tier].items)
    if any(value < 0 or value > clock_size for value in shifted):
        raise GraphValidationError(
            f"retime alignment must stay between clock boundaries 0 and {clock_size}"
        )
    if shifted != tuple(sorted(shifted)):
        raise GraphValidationError("retime alignment must be monotone")

    def apply(editor: _DerivedEditor) -> None:
        """Apply the resolved boundary rebindings."""
        current = editor.freeze()
        sites: dict[BoundaryRef, int] = {}
        for index, relation in enumerate(current.relations):
            if relation.declaration != binding:
                continue
            source = current.resolve_boundary(cast(DurableBoundaryRef, relation.left))
            if source.tier == tier:
                sites[source] = index
        for boundary_index, clock_index in enumerate(shifted):
            source = BoundaryRef(tier, boundary_index)
            site = sites[source]
            relation = current.relations[site]
            target = anchored_boundary(
                current, BoundaryRef(profile.clock_tier, clock_index)
            )
            if current.resolve_boundary(cast(DurableBoundaryRef, relation.right)) != (
                BoundaryRef(profile.clock_tier, clock_index)
            ):
                editor.set_endpoints(RelationInstanceRef(site), relation.left, target)
        _profile_with_graph(profile, editor.freeze())

    return _apply_derived(graph, journal, apply)[0]


def contain_by_time(
    profile: ClockProfile,
    relation: QualifiedName,
    parent: QualifiedName,
    child: QualifiedName,
    *,
    rule: ContainmentRule = "midpoint",
    journal: Journal | None = None,
) -> Graph:
    """Rebuild one parent-child relation from exact shared-clock spans.

    The default ``"midpoint"`` rule assigns each child to the unique parent
    whose half-open span contains the child's midpoint. Spans use coarse,
    integral clock-boundary indices; optional within-tick refinements do not
    affect assignment. A caller may instead provide a function receiving the
    child's integral clock span and every parent span; it returns the chosen
    parent or ``None``. Refusal is explicit when the default finds no unique
    parent or a callback names another item.

    Bipartite declarations retain one instance per child. Polyadic declarations
    retain one source per parent and ordered child targets. Existing instances
    are updated in place where possible, preserving ids, values, facts, and
    global order; already correct structure is an exact no-op. Journaled calls
    expand into endpoint, relation, and fact primitives.
    """
    if not isinstance(profile, ClockProfile):
        raise TypeError("contain_by_time profile must be a ClockProfile")
    if not all(isinstance(value, QualifiedName) for value in (relation, parent, child)):
        raise TypeError("contain_by_time names must be QualifiedName values")
    if not (callable(rule) or rule == "midpoint"):
        raise ValueError("contain_by_time rule must be 'midpoint' or callable")
    graph = profile.graph
    parents = _tier_items(graph, parent, "parent")
    children = _tier_items(graph, child, "child")
    if not profile.is_timed(parent) or not profile.is_timed(child):
        raise GraphValidationError("contain_by_time requires two timed tiers")
    parent_spans = tuple(
        (reference, _clock_span(profile, reference)) for reference in parents
    )
    assignments = {
        reference: _choose_parent(
            reference,
            _clock_span(profile, reference),
            parent_spans,
            rule,
        )
        for reference in children
    }
    declaration = next(
        (
            candidate
            for candidate in graph.relation_declarations
            if candidate.name == relation
        ),
        None,
    )
    if not isinstance(
        declaration, BipartiteRelationDeclaration | PolyadicRelationDeclaration
    ):
        raise GraphValidationError(
            f"contain_by_time relation {str(relation)!r} is not declared containment"
        )
    _validate_containment_shape(graph, declaration, parent, child)

    def apply(editor: _DerivedEditor) -> None:
        """Apply the resolved containment assignments."""
        if isinstance(declaration, BipartiteRelationDeclaration):
            _apply_binary_containment(editor, relation, assignments)
        else:
            _apply_polyadic_containment(editor, relation, parents, assignments)
        _profile_with_graph(profile, editor.freeze())

    return _apply_derived(graph, journal, apply)[0]


def _apply_derived(
    graph: Graph,
    journal: Journal | None,
    operation: Callable[[_DerivedEditor], None],
) -> tuple[Graph, Displacement]:
    """Apply a derived edit atomically, preflighting journal protections."""
    if journal is None:
        editor = cast(_DerivedEditor, graph.edit())
    else:
        editor = cast(_DerivedEditor, graph.edit(journal=journal))
        cast(Any, editor).dry_run(operation)
    operation(editor)
    return editor.freeze(), editor.displacement()


def _detached_fact_sites(
    graph: Graph, result: Graph, displacement: Displacement
) -> tuple[tuple[LayerName, int], ...]:
    """Locate facts whose subjects departed during a derived edit."""
    sites: list[tuple[LayerName, int]] = []
    result_facts_by_layer = {
        layer.name: frozenset(layer.facts) for layer in result.layers
    }
    for layer in graph.layers:
        result_facts = result_facts_by_layer[layer.name]
        for index, fact in enumerate(layer.facts):
            if isinstance(fact.subject, OrphanedSubject):
                continue
            positional = isinstance(
                fact.subject,
                ItemRef | BoundaryRef | RelationInstanceRef | PolyadicInstanceRef,
            )
            if not positional and fact in result_facts:
                continue
            subject = _resolve_layer_subject(graph, fact.subject)
            image: object | None
            if isinstance(subject, ItemRef):
                image = displacement.items.get(subject)
            elif isinstance(subject, BoundaryRef):
                image = displacement.boundaries.get(subject)
            elif isinstance(subject, RelationInstanceRef):
                relation = displacement.relations.get(subject.index)
                image = None if relation is None else RelationInstanceRef(relation)
            elif isinstance(subject, PolyadicInstanceRef):
                relation = displacement.polyadic_relations.get(subject.index)
                image = None if relation is None else PolyadicInstanceRef(relation)
            else:  # pragma: no cover - live facts resolve to graph coordinates
                image = None
            if image is None or LayerFact(image, fact.value) not in result_facts:
                sites.append((layer.name, index))
    return tuple(sites)


def _chosen_path(lattice: PathPlan[Any], raw: PathChoice) -> tuple[ItemRef, ...]:
    """Resolve and validate one complete root-to-sink path."""
    if raw is None:
        raise TypeError(
            "commit_path path must be iterable; PathPlan provenance may be "
            "unavailable without a declared witness order and tie policy"
        )
    if isinstance(raw, str):
        raise TypeError("commit_path path must be an iterable of path steps")
    materialized = tuple(raw)
    if materialized and all(isinstance(value, tuple) for value in materialized):
        if len(materialized) != 1:
            raise GraphValidationError(
                "commit_path provenance must contain exactly one chosen path"
            )
        materialized = tuple(cast(tuple[str, ...], materialized[0]))
    if not materialized:
        raise GraphValidationError("commit_path path must not be empty")
    labels = dict(zip(lattice.labels, lattice.items, strict=True))
    resolved: list[ItemRef] = []
    for step in materialized:
        if isinstance(step, ItemRef | DurableItemRef):
            reference = lattice.declaration.graph.resolve_item(step)
        elif isinstance(step, str):
            found = labels.get(step)
            if found is None:
                raise GraphValidationError(
                    f"commit_path label {step!r} is not in the lattice"
                )
            reference = found
        else:
            raise TypeError(
                "commit_path steps must be ItemRef, DurableItemRef, or str values"
            )
        if reference not in lattice.items:
            raise GraphValidationError(
                f"commit_path item {reference!s} is not in the lattice"
            )
        resolved.append(reference)
    indexes = tuple(lattice.index(reference) for reference in resolved)
    if len(set(indexes)) != len(indexes):
        raise GraphValidationError("commit_path path repeats a lattice item")
    if indexes[0] not in lattice.roots:
        raise GraphValidationError("commit_path path does not start at a lattice root")
    for source, target in pairwise(indexes):
        if target not in lattice.children[source]:
            raise GraphValidationError(
                "commit_path path contains a step that is not a lattice edge"
            )
    if lattice.children[indexes[-1]]:
        raise GraphValidationError("commit_path path does not end at a lattice sink")
    return tuple(resolved)


def _containment_names(
    containment: QualifiedName | Iterable[QualifiedName],
) -> tuple[QualifiedName, ...]:
    """Normalize and validate containment declaration names."""
    if isinstance(containment, QualifiedName):
        return (containment,)
    names = tuple(containment)
    if any(not isinstance(name, QualifiedName) for name in names):
        raise TypeError("commit_path containment names must be QualifiedName values")
    return names


def _containment_traversals(
    graph: Graph,
    containments: tuple[QualifiedName, ...],
) -> tuple[OrderedContainment, ...]:
    """Build the declared containment traversals with a public refusal type."""
    traversals: list[OrderedContainment] = []
    for name in containments:
        try:
            traversals.append(OrderedContainment(graph, name))
        except ValueError as error:
            raise GraphValidationError(
                "commit_path containment must name an ordered, item-only "
                "polyadic relation with source uniqueness and acyclicity: "
                f"{error}"
            ) from error
    return tuple(traversals)


def _closure(
    roots: tuple[ItemRef, ...],
    traversals: tuple[OrderedContainment, ...],
) -> set[ItemRef]:
    """Return roots and their transitive descendants across declarations."""
    reached = set(roots)
    frontier = list(roots)
    while frontier:
        source = frontier.pop()
        for traversal in traversals:
            tiers = traversal._declaration.sources.tiers
            if tiers is not None and source.tier not in tiers:
                continue
            for node in traversal.direct_children(source).nodes:
                target = cast(ItemRef, node.reference)
                if target not in reached:
                    reached.add(target)
                    frontier.append(target)
    return reached


def _remove_items(editor: _DerivedEditor, departing: set[ItemRef]) -> None:
    """Cascade explicit dependency removals before removing selected items."""
    order = sorted(departing, key=lambda ref: (str(ref.tier), ref.index), reverse=True)
    for original in order:
        current = editor.displacement().items[original]
        _remove_item(editor, current)


def _remove_item(editor: _DerivedEditor, reference: ItemRef) -> None:
    """Remove one item after explicitly removing every owned dependency."""
    graph = editor.freeze()
    member = graph._tiers_by_name[reference.tier]
    mapping = {
        old: old if old < reference.index else old - 1
        for old in range(len(member.items))
        if old != reference.index
    }
    images = _boundary_images(len(member.items), len(member.items) - 1, mapping)
    boundaries = {
        BoundaryRef(reference.tier, index)
        for index in range(len(member.items) + 1)
        if index not in images
    }
    binary = {
        index
        for index, relation in enumerate(graph.relations)
        if _endpoint_depends(graph, relation.left, reference)
        or _endpoint_depends(graph, relation.right, reference)
    }
    polyadic = {
        index
        for index, relation in enumerate(graph.polyadic_relations)
        if any(
            _endpoint_depends(graph, endpoint, reference)
            for endpoint in (*relation.sources, *relation.targets)
        )
    }
    _remove_facts_where(
        editor,
        graph,
        lambda subject: (
            (isinstance(subject, RelationInstanceRef) and subject.index in binary)
            or (isinstance(subject, PolyadicInstanceRef) and subject.index in polyadic)
        ),
    )
    for index in sorted(polyadic, reverse=True):
        editor.remove_relation(PolyadicInstanceRef(index))
    for index in sorted(binary, reverse=True):
        editor.remove_relation(RelationInstanceRef(index))
    graph = editor.freeze()
    for layer in graph.layers:
        for fact in layer.facts:
            if isinstance(fact.subject, OrphanedSubject):
                continue
            subject = _resolve_layer_subject(graph, fact.subject)
            if subject == reference or (
                isinstance(fact.subject, BoundaryRef | DurableBoundaryRef)
                and _boundary_depends(graph, fact.subject, reference, boundaries)
            ):
                editor.remove_fact(layer.name, fact.subject, fact.value.name)
    for boundary in tuple(graph.boundary_values):
        if _boundary_depends(graph, boundary.reference, reference, boundaries):
            for value in boundary.attributes:
                editor.remove_attribute(boundary.reference, value.name)
    editor.remove_item(reference)


def _endpoint_depends(
    graph: Graph,
    endpoint: RelationEndpointRef,
    item: ItemRef,
) -> bool:
    """Report whether an endpoint belongs to an item being withdrawn."""
    if isinstance(endpoint, ItemRef | DurableItemRef):
        return graph.resolve_item(endpoint) == item
    return _boundary_depends(graph, endpoint, item, set())


def _boundary_depends(
    graph: Graph,
    boundary: BoundaryRef | DurableBoundaryRef,
    item: ItemRef,
    departed: set[BoundaryRef],
) -> bool:
    """Report whether a boundary loses its coordinate or durable anchor."""
    if isinstance(boundary, DurableBoundaryRef):
        return (
            isinstance(boundary.anchor, DurableItemRef)
            and graph.resolve_item(boundary.anchor) == item
        )
    return graph.resolve_boundary(boundary) in departed


def _remove_facts_where(
    editor: _DerivedEditor,
    graph: Graph,
    predicate: Callable[[object], bool],
) -> None:
    """Remove live facts whose resolved subjects satisfy ``predicate``."""
    for layer in graph.layers:
        for fact in layer.facts:
            if isinstance(fact.subject, OrphanedSubject):
                continue
            subject = _resolve_layer_subject(graph, fact.subject)
            if predicate(subject):
                editor.remove_fact(layer.name, fact.subject, fact.value.name)


def _profile_with_graph(profile: ClockProfile, graph: Graph) -> ClockProfile:
    """Rebuild one full clock profile over an edited graph."""
    return ClockProfile(
        graph,
        profile.clock_tier,
        profile.binding_relation,
        profile.rate_attribute,
        profile.unit_attribute,
        profile.tick_attribute,
        profile.gap_attribute,
        profile.untimed_attribute,
        profile.start_attribute,
        profile.duration_attribute,
    )


def _tier_items(graph: Graph, tier: QualifiedName, role: str) -> tuple[ItemRef, ...]:
    """Return every item coordinate from a required tier."""
    member = graph._tiers_by_name.get(tier)
    if member is None:
        raise GraphValidationError(
            f"contain_by_time {role} tier {str(tier)!r} is not declared",
            RefusalStage.REFERENCE,
        )
    return tuple(ItemRef(tier, index) for index in range(len(member.items)))


def _clock_span(profile: ClockProfile, reference: ItemRef) -> tuple[int, int]:
    """Return one event's exact integral clock-boundary span."""
    return (
        profile.clock_index(BoundaryRef(reference.tier, reference.index)),
        profile.clock_index(BoundaryRef(reference.tier, reference.index + 1)),
    )


def _choose_parent(
    child: ItemRef,
    span: tuple[int, int],
    parents: tuple[tuple[ItemRef, tuple[int, int]], ...],
    rule: ContainmentRule,
) -> ItemRef:
    """Apply a declared exact assignment rule to one child."""
    if callable(rule):
        chosen = rule(span, parents)
        if chosen is None:
            raise GraphValidationError(
                f"contain_by_time rule assigned no parent to {child!s}"
            )
        if chosen not in {reference for reference, _ in parents}:
            raise GraphValidationError(
                f"contain_by_time rule returned {chosen!s}, which is not a parent item"
            )
        return chosen
    midpoint = span[0] + span[1]
    matches = tuple(
        reference
        for reference, (start, end) in parents
        if 2 * start <= midpoint < 2 * end
    )
    if len(matches) != 1:
        raise GraphValidationError(
            f"contain_by_time midpoint assigned {child!s} to {len(matches)} parents"
        )
    return matches[0]


def _validate_containment_shape(
    graph: Graph,
    declaration: BipartiteRelationDeclaration | PolyadicRelationDeclaration,
    parent: QualifiedName,
    child: QualifiedName,
) -> None:
    """Require one item-only declaration oriented from parent to child."""
    parent_type = graph._types_by_tier.get(parent)
    child_type = graph._types_by_tier.get(child)
    if isinstance(declaration, BipartiteRelationDeclaration):
        valid = (
            parent_type is not None
            and child_type is not None
            and declaration.left_endpoint is RelationEndpointKind.ITEM
            and declaration.right_endpoint is RelationEndpointKind.ITEM
            and declaration.left_type == parent_type
            and declaration.right_type == child_type
            and declaration.single_parent
        )
    else:
        valid = (
            parent_type is not None
            and child_type is not None
            and declaration.sources.endpoint_kinds == (RelationEndpointKind.ITEM,)
            and declaration.targets.endpoint_kinds == (RelationEndpointKind.ITEM,)
            and (declaration.sources.tiers in (None, (parent,)))
            and (declaration.targets.tiers in (None, (child,)))
            and declaration.sources.minimum == 1
            and declaration.sources.maximum == 1
            and not declaration.sources.allow_empty
            and declaration.unique_sources
        )
    if not valid:
        raise GraphValidationError(
            "contain_by_time relation must be item-only, parent-to-child, and "
            "source-unique when polyadic"
        )


def _apply_binary_containment(
    editor: _DerivedEditor,
    relation: QualifiedName,
    assignments: dict[ItemRef, ItemRef],
) -> None:
    """Update one binary parent-child relation while retaining instance content."""
    graph = editor.freeze()
    existing: dict[ItemRef, int] = {}
    extras: list[int] = []
    for index, instance in enumerate(graph.relations):
        if instance.declaration != relation:
            continue
        child = graph.resolve_item(cast(ItemRef | DurableItemRef, instance.right))
        if child in existing:
            extras.append(index)
        else:
            existing[child] = index
    for child, parent in assignments.items():
        site = existing.get(child)
        if site is None:
            editor.add_relation(RelationInstance(relation, parent, child))
            continue
        instance = graph.relations[site]
        if graph.resolve_item(cast(ItemRef | DurableItemRef, instance.left)) != parent:
            editor.set_endpoints(RelationInstanceRef(site), parent, instance.right)
    extras.extend(existing[child] for child in set(existing) - set(assignments))
    for site in sorted(extras, reverse=True):
        _remove_relation_facts(editor, graph, RelationInstanceRef(site))
        editor.remove_relation(RelationInstanceRef(site))


def _apply_polyadic_containment(
    editor: _DerivedEditor,
    relation: QualifiedName,
    parents: tuple[ItemRef, ...],
    assignments: dict[ItemRef, ItemRef],
) -> None:
    """Update ordered polyadic containment while retaining instance content."""
    graph = editor.freeze()
    existing: dict[ItemRef, int] = {}
    for index, instance in enumerate(graph.polyadic_relations):
        if instance.declaration != relation:
            continue
        parent = graph.resolve_item(cast(ItemRef | DurableItemRef, instance.sources[0]))
        existing[parent] = index
    grouped = {
        parent: tuple(
            child for child, assigned in assignments.items() if assigned == parent
        )
        for parent in parents
    }
    desired = {parent: children for parent, children in grouped.items() if children}
    for parent, children in desired.items():
        site = existing.get(parent)
        if site is None:
            editor.add_relation(PolyadicRelationInstance(relation, (parent,), children))
            continue
        instance = graph.polyadic_relations[site]
        resolved = tuple(
            graph.resolve_item(cast(ItemRef | DurableItemRef, target))
            for target in instance.targets
        )
        if resolved != children:
            editor.set_endpoints(PolyadicInstanceRef(site), instance.sources, children)
    extras = tuple(existing[parent] for parent in set(existing) - set(desired))
    for site in sorted(extras, reverse=True):
        _remove_relation_facts(editor, graph, PolyadicInstanceRef(site))
        editor.remove_relation(PolyadicInstanceRef(site))


def _remove_relation_facts(
    editor: _DerivedEditor,
    graph: Graph,
    reference: RelationInstanceRef | PolyadicInstanceRef,
) -> None:
    """Remove every fact owned by one relation instance before its removal."""
    _remove_facts_where(editor, graph, lambda subject: subject == reference)


__all__ = [
    "ContainmentRule",
    "PathChoice",
    "PathStep",
    "commit_path",
    "contain_by_time",
    "retime",
]
