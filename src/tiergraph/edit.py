"""Opt-in edit journals, inverse records, reports, and guarded editing."""

from __future__ import annotations

import math
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Set
from contextlib import contextmanager
from dataclasses import dataclass, field, fields, is_dataclass, replace
from inspect import Parameter, signature
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from tiergraph.blob import BlobProfile
    from tiergraph.patch import Patch

from tiergraph.clock import (
    ClockEditor,
    ClockEditReport,
    ClockProfile,
    ClockRebindingPolicy,
    _check_blob_profile,
)
from tiergraph.core import (
    Attribute,
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    BoundaryRef,
    BoundarySide,
    ContainmentYieldChange,
    Displacement,
    DocumentRef,
    DurableBoundaryRef,
    DurableItemRef,
    DurablePolyadicRef,
    DurableRelationRef,
    EditDeclaration,
    EditTarget,
    Graph,
    GraphEditor,
    GraphValidationError,
    Item,
    ItemRef,
    ItemRun,
    JsonAttributeValue,
    JsonType,
    JsonValue,
    Layer,
    LayerFact,
    LayerName,
    LayerSubject,
    NamespaceDeclaration,
    OrphanedSubject,
    PolyadicInstanceRef,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    QualifiedName,
    RelationDeclarationRef,
    RelationEndpointRef,
    RelationInstance,
    RelationInstanceRef,
    RelationTarget,
    SealedCarrier,
    ShiftDirection,
    SimpleRelationDeclaration,
    TierDeclaration,
    TierRef,
    _ImmutableMapping,
    undeclare_with_contents,
)
from tiergraph.replacement import (
    DetachedDependency,
    DetachmentReport,
    ReplacementPolicies,
    Subtree,
    SubtreeCorrespondence,
    _replace_subtree,
    _swap_subtrees,
)

_ANNOTATION_NAME = QualifiedName("urn:tiergraph:edit-journal", "annotations")
_GRAPH_FIELDS = (
    "namespaces",
    "tiers",
    "relation_declarations",
    "relations",
    "attribute_declarations",
    "boundary_values",
    "attributes",
    "polyadic_relations",
    "seals",
    "layers",
)
_POSITIONAL_PATCH_LIMIT = 4
_LEDGER_RECORD_LENGTH = 3
type _ProvenanceKey = tuple[LayerSubject, QualifiedName]
type _ProvenanceOwnershipDelta = tuple[
    frozenset[_ProvenanceKey], frozenset[_ProvenanceKey]
]


@dataclass(frozen=True, slots=True)
class EditAnnotations:
    """Carry caller-supplied edit metadata without inventing a timestamp.

    ``fields`` accepts ordinary typed JSON values.  A timestamp is an opaque
    caller-supplied string: constructing annotations never reads a clock.
    """

    author: str | None = None
    reason: str | None = None
    stage: str | None = None
    confidence: float | None = None
    iteration: int | None = None
    tool: str | None = None
    timestamp: str | None = None
    fields: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate scalar fields and detach the typed free-form mapping."""
        for name in ("author", "reason", "stage", "tool", "timestamp"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise TypeError(f"annotation {name} must be a string or None")
        if self.confidence is not None:
            if isinstance(self.confidence, bool) or not isinstance(
                self.confidence, int | float
            ):
                raise TypeError("annotation confidence must be numeric or None")
            if not math.isfinite(self.confidence):
                raise ValueError("annotation confidence must be finite")
        if self.iteration is not None and (
            isinstance(self.iteration, bool) or not isinstance(self.iteration, int)
        ):
            raise TypeError("annotation iteration must be an integer or None")
        if not isinstance(self.fields, Mapping) or any(
            not isinstance(key, str) for key in self.fields
        ):
            raise TypeError("annotation fields must be a string-keyed mapping")
        detached = JsonAttributeValue(
            _ANNOTATION_NAME, cast(dict[str, JsonValue], dict(self.fields))
        ).to_value()
        object.__setattr__(
            self,
            "fields",
            MappingProxyType(cast(dict[str, JsonValue], detached)),
        )

    def merged(self, other: EditAnnotations) -> EditAnnotations:
        """Return these defaults with non-``None`` values from ``other``."""
        return EditAnnotations(
            author=other.author if other.author is not None else self.author,
            reason=other.reason if other.reason is not None else self.reason,
            stage=other.stage if other.stage is not None else self.stage,
            confidence=(
                other.confidence if other.confidence is not None else self.confidence
            ),
            iteration=(
                other.iteration if other.iteration is not None else self.iteration
            ),
            tool=other.tool if other.tool is not None else self.tool,
            timestamp=(
                other.timestamp if other.timestamp is not None else self.timestamp
            ),
            fields={**self.fields, **other.fields},
        )

    def to_data(self) -> dict[str, JsonValue]:
        """Return only supplied metadata as detached JSON data."""
        data: dict[str, JsonValue] = {}
        for name in ("author", "reason", "stage", "tool", "timestamp"):
            value = getattr(self, name)
            if value is not None:
                data[name] = cast(str, value)
        if self.confidence is not None:
            data["confidence"] = self.confidence
        if self.iteration is not None:
            data["iteration"] = self.iteration
        if self.fields:
            data["fields"] = cast(
                dict[str, JsonValue],
                JsonAttributeValue(
                    _ANNOTATION_NAME, cast(dict[str, JsonValue], dict(self.fields))
                ).to_value(),
            )
        return data


@dataclass(frozen=True, slots=True, order=True)
class RelationTouch:
    """Name one touched binary or polyadic relation position."""

    carrier: str
    index: int

    def to_data(self) -> dict[str, JsonValue]:
        """Return the carrier and structural index."""
        return {"carrier": self.carrier, "index": self.index}


@dataclass(frozen=True, slots=True)
class _ReferenceRunMapping[Reference: (ItemRef, BoundaryRef)](
    _ImmutableMapping, Mapping[Reference, Reference]
):
    """Represent coordinate mappings as a small tuple of affine runs."""

    reference_type: type[Reference]
    runs: tuple[tuple[QualifiedName, int, int, QualifiedName, int], ...]
    size: int

    def __getitem__(self, key: Reference) -> Reference:
        if not isinstance(key, self.reference_type):
            raise KeyError(key)
        for source_tier, start, length, target_tier, target_start in self.runs:
            if source_tier == key.tier and start <= key.index < start + length:
                return self.reference_type(
                    target_tier, target_start + key.index - start
                )
        raise KeyError(key)

    def __iter__(self) -> Iterator[Reference]:
        for source_tier, start, length, _, _ in self.runs:
            for index in range(start, start + length):
                yield self.reference_type(source_tier, index)

    def __len__(self) -> int:
        return self.size

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Mapping) or len(self) != len(other):
            return False
        return dict(self.items()) == dict(other.items())


@dataclass(frozen=True, slots=True)
class _IntegerRunMapping(_ImmutableMapping, Mapping[int, int]):
    """Represent integer coordinate mappings as affine runs."""

    runs: tuple[tuple[int, int, int], ...]
    size: int

    def __getitem__(self, key: int) -> int:
        for start, length, target_start in self.runs:
            if start <= key < start + length:
                return target_start + key - start
        raise KeyError(key)

    def __iter__(self) -> Iterator[int]:
        for start, length, _ in self.runs:
            yield from range(start, start + length)

    def __len__(self) -> int:
        return self.size

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Mapping) or len(self) != len(other):
            return False
        return dict(self.items()) == dict(other.items())


def _compact_reference_mapping[Reference: (ItemRef, BoundaryRef)](
    mapping: Mapping[Reference, Reference],
    reference_type: type[Reference],
    visit: Callable[[Reference, Reference], None] | None = None,
) -> _ReferenceRunMapping[Reference]:
    """Coalesce consecutive reference pairs into affine runs."""
    entries = sorted(
        mapping.items(), key=lambda pair: (str(pair[0].tier), pair[0].index)
    )
    runs: list[tuple[QualifiedName, int, int, QualifiedName, int]] = []
    for source, target in entries:
        if visit is not None:
            visit(source, target)
        if (
            runs
            and runs[-1][0] == source.tier
            and runs[-1][1] + runs[-1][2] == source.index
            and runs[-1][3] == target.tier
            and runs[-1][4] + runs[-1][2] == target.index
        ):
            source_tier, start, length, target_tier, target_start = runs[-1]
            runs[-1] = (
                source_tier,
                start,
                length + 1,
                target_tier,
                target_start,
            )
        else:
            runs.append((source.tier, source.index, 1, target.tier, target.index))
    return _ReferenceRunMapping(reference_type, tuple(runs), len(entries))


def _compact_integer_mapping(
    mapping: Mapping[int, int],
    visit: Callable[[int, int], None] | None = None,
) -> _IntegerRunMapping:
    """Coalesce consecutive integer pairs into affine runs."""
    runs: list[tuple[int, int, int]] = []
    for source, target in sorted(mapping.items()):
        if visit is not None:
            visit(source, target)
        if (
            runs
            and runs[-1][0] + runs[-1][1] == source
            and runs[-1][2] + runs[-1][1] == target
        ):
            start, length, target_start = runs[-1]
            runs[-1] = (start, length + 1, target_start)
        else:
            runs.append((source, 1, target))
    return _IntegerRunMapping(tuple(runs), len(mapping))


@dataclass(slots=True)
class _PositionTouches:
    """Accumulate structural touches while compacting total displacement."""

    old_items: set[ItemRef] = field(default_factory=set)
    new_items: set[ItemRef] = field(default_factory=set)
    old_boundaries: set[BoundaryRef] = field(default_factory=set)
    new_boundaries: set[BoundaryRef] = field(default_factory=set)
    old_relations: set[RelationTouch] = field(default_factory=set)
    new_relations: set[RelationTouch] = field(default_factory=set)


def _missing_reference_images[Reference: (ItemRef, BoundaryRef)](
    mapping: _ReferenceRunMapping[Reference],
    counts: Mapping[QualifiedName, int],
) -> Iterator[Reference]:
    """Yield target coordinates not covered by affine mapping runs."""
    intervals: dict[QualifiedName, list[tuple[int, int]]] = {}
    for _, _, length, target_tier, target_start in mapping.runs:
        intervals.setdefault(target_tier, []).append(
            (target_start, target_start + length)
        )
    for tier, count in counts.items():
        cursor = 0
        for start, end in sorted(intervals.get(tier, ())):
            yield from (
                mapping.reference_type(tier, index)
                for index in range(cursor, min(start, count))
            )
            cursor = max(cursor, end)
        yield from (
            mapping.reference_type(tier, index) for index in range(cursor, count)
        )


def _missing_integer_images(mapping: _IntegerRunMapping, count: int) -> Iterator[int]:
    """Yield target integer coordinates not covered by affine mapping runs."""
    cursor = 0
    for start, end in sorted(
        (target_start, target_start + length)
        for _, length, target_start in mapping.runs
    ):
        yield from range(cursor, min(start, count))
        cursor = max(cursor, end)
    yield from range(cursor, count)


def _compact_displacement_with_touches(
    displacement: Displacement, before: Graph, after: Graph
) -> tuple[Displacement, _PositionTouches]:
    """Compact total maps while deriving structural report positions once."""
    touched = _PositionTouches(
        old_items=set(displacement.departed_items),
        old_boundaries=set(displacement.departed_boundaries),
        old_relations={
            RelationTouch("relations", index)
            for index in displacement.departed_relations
        }
        | {
            RelationTouch("polyadic_relations", index)
            for index in displacement.departed_polyadic_relations
        },
    )

    def _item(source: ItemRef, target: ItemRef) -> None:
        if source != target or _item_at(before, source) != _item_at(after, target):
            touched.old_items.add(source)
            touched.new_items.add(target)

    def _boundary(source: BoundaryRef, target: BoundaryRef) -> None:
        if source != target or _boundary_attributes(
            before, source
        ) != _boundary_attributes(after, target):
            touched.old_boundaries.add(source)
            touched.new_boundaries.add(target)

    def _relation(carrier: str) -> Callable[[int, int], None]:
        old_values = getattr(before, carrier)
        new_values = getattr(after, carrier)

        def _visit(source: int, target: int) -> None:
            if source != target or old_values[source] != new_values[target]:
                touched.old_relations.add(RelationTouch(carrier, source))
                touched.new_relations.add(RelationTouch(carrier, target))

        return _visit

    items = _compact_reference_mapping(displacement.items, ItemRef, _item)
    boundaries = _compact_reference_mapping(
        displacement.boundaries, BoundaryRef, _boundary
    )
    relations = _compact_integer_mapping(displacement.relations, _relation("relations"))
    polyadic = _compact_integer_mapping(
        displacement.polyadic_relations, _relation("polyadic_relations")
    )
    item_counts = {tier.declaration.name: len(tier.items) for tier in after.tiers}
    boundary_counts = {tier: count + 1 for tier, count in item_counts.items()}
    touched.new_items.update(_missing_reference_images(items, item_counts))
    touched.new_boundaries.update(
        _missing_reference_images(boundaries, boundary_counts)
    )
    touched.new_relations.update(
        RelationTouch("relations", index)
        for index in _missing_integer_images(relations, len(after.relations))
    )
    touched.new_relations.update(
        RelationTouch("polyadic_relations", index)
        for index in _missing_integer_images(polyadic, len(after.polyadic_relations))
    )
    return (
        Displacement(
            items,
            boundaries,
            relations,
            polyadic,
            displacement.departed_items,
            displacement.departed_boundaries,
            displacement.departed_relations,
            displacement.departed_polyadic_relations,
        ),
        touched,
    )


def _subject_data(subject: LayerSubject) -> dict[str, JsonValue]:
    """Use the graph's canonical layer encoding for one subject."""
    marker = JsonAttributeValue(_ANNOTATION_NAME, None)
    layer = Layer(
        LayerName(_ANNOTATION_NAME.namespace, "encoding"),
        (LayerFact(subject, marker),),
    )
    facts = cast(list[dict[str, JsonValue]], layer.to_data()["facts"])
    return cast(dict[str, JsonValue], facts[0]["subject"])


def _clock_report_data(report: ClockEditReport) -> dict[str, JsonValue]:
    """Encode the public fields shared by clock edit reports."""
    changes: list[JsonValue] = [
        {
            "previous_boundary": (
                None
                if change.previous_boundary is None
                else change.previous_boundary.to_data()
            ),
            "boundary": (
                None if change.boundary is None else change.boundary.to_data()
            ),
            "previous_source": (
                None
                if change.previous_source is None
                else _endpoint_data(change.previous_source)
            ),
            "source": (
                None if change.source is None else _endpoint_data(change.source)
            ),
            "previous_clock_index": change.previous_clock_index,
            "clock_index": change.clock_index,
            "provisional": change.provisional,
        }
        for change in report.changes
    ]
    return {
        "operation": report.operation.value,
        "policy": report.policy.value,
        "tier": report.tier.to_data(),
        "changes": changes,
        "needs_realignment": report.needs_realignment,
    }


def _endpoint_data(endpoint: RelationEndpointRef) -> dict[str, JsonValue]:
    """Encode the endpoint shapes used in clock reports."""
    if isinstance(endpoint, ItemRef):
        return {"kind": "item", "reference": endpoint.to_data()}
    if isinstance(endpoint, DurableItemRef):
        return {"kind": "durable_item", "durable_id": endpoint.durable_id}
    anchor = endpoint.anchor
    if isinstance(anchor, QualifiedName):
        anchor_data: JsonValue = {"kind": "tier", "tier": anchor.to_data()}
    else:
        anchor_data = {"kind": "item", "durable_id": anchor.durable_id}
    return {
        "kind": "durable_boundary",
        "anchor": anchor_data,
        "side": endpoint.side.value,
    }


def _single_endpoint(value: object) -> bool:
    """Report whether a value is one relation endpoint rather than an iterable."""
    return isinstance(value, ItemRef | DurableItemRef | DurableBoundaryRef)


@dataclass(frozen=True, slots=True)
class EditReport:
    """Describe the content one recorded operation touched."""

    operation: str
    touched_items: tuple[ItemRef, ...]
    touched_boundaries: tuple[BoundaryRef, ...]
    touched_relations: tuple[RelationTouch, ...]
    detached_references: tuple[LayerSubject, ...]
    displacement: Displacement
    annotations: EditAnnotations
    clock_reports: tuple[ClockEditReport, ...] = ()
    detached_dependencies: tuple[DetachedDependency, ...] = ()
    pruned_orphans: tuple[PrunedFact, ...] = ()
    detached_content: DetachmentReport | None = None
    correspondence: SubtreeCorrespondence | None = None
    yield_changes: tuple[ContainmentYieldChange, ...] = ()

    def to_data(self) -> dict[str, JsonValue]:
        """Return this report in deterministic JSON-compatible form."""
        data: dict[str, JsonValue] = {
            "operation": self.operation,
            "touched_items": [item.to_data() for item in self.touched_items],
            "touched_boundaries": [
                boundary.to_data() for boundary in self.touched_boundaries
            ],
            "touched_relations": [
                relation.to_data() for relation in self.touched_relations
            ],
            "detached_references": [
                _subject_data(subject) for subject in self.detached_references
            ],
            "displacement": self.displacement.to_data(),
            "annotations": self.annotations.to_data(),
            "clock_reports": [
                _clock_report_data(report) for report in self.clock_reports
            ],
            "detached_dependencies": [
                dependency.to_data() for dependency in self.detached_dependencies
            ],
            "pruned_orphans": [fact.to_data() for fact in self.pruned_orphans],
        }
        if self.detached_content is not None:
            data["detached_content"] = self.detached_content.to_data()
        if self.correspondence is not None:
            data["correspondence"] = self.correspondence.to_data()
        if self.yield_changes:
            data["yield_changes"] = [change.to_data() for change in self.yield_changes]
        return data


@dataclass(frozen=True, slots=True)
class PrunedFact:
    """Name one orphaned layer fact removed by explicit cleanup."""

    layer: LayerName
    fact: LayerFact

    def to_data(self) -> dict[str, JsonValue]:
        """Return the layer identity and canonical fact data."""
        encoded = Layer(self.layer, (self.fact,)).to_data()
        facts = cast(list[dict[str, JsonValue]], encoded["facts"])
        return {"layer": self.layer.to_data(), "fact": facts[0]}


@dataclass(frozen=True, slots=True)
class _ReferenceTouchRuns[Reference: (ItemRef, BoundaryRef)]:
    """Keep a touched coordinate set as contiguous tier-local runs."""

    reference_type: type[Reference]
    runs: tuple[tuple[QualifiedName, int, int], ...]

    @classmethod
    def from_references(
        cls, references: Iterable[Reference], reference_type: type[Reference]
    ) -> _ReferenceTouchRuns[Reference]:
        """Compact one reference set into contiguous tier-local runs."""
        runs: list[tuple[QualifiedName, int, int]] = []
        for reference in sorted(
            set(references), key=lambda item: (str(item.tier), item.index)
        ):
            if (
                runs
                and runs[-1][0] == reference.tier
                and runs[-1][1] + runs[-1][2] == reference.index
            ):
                tier, start, length = runs[-1]
                runs[-1] = (tier, start, length + 1)
            else:
                runs.append((reference.tier, reference.index, 1))
        return cls(reference_type, tuple(runs))

    def expand(self) -> tuple[Reference, ...]:
        """Materialize the public tuple only when a report is requested."""
        return tuple(
            self.reference_type(tier, index)
            for tier, start, length in self.runs
            for index in range(start, start + length)
        )


@dataclass(frozen=True, slots=True)
class _RelationTouchRuns:
    """Keep touched relation positions as contiguous carrier-local runs."""

    runs: tuple[tuple[str, int, int], ...]

    @classmethod
    def from_touches(cls, touches: Iterable[RelationTouch]) -> _RelationTouchRuns:
        """Compact one relation-touch set into carrier-local runs."""
        runs: list[tuple[str, int, int]] = []
        for touch in sorted(set(touches)):
            if (
                runs
                and runs[-1][0] == touch.carrier
                and runs[-1][1] + runs[-1][2] == touch.index
            ):
                carrier, start, length = runs[-1]
                runs[-1] = (carrier, start, length + 1)
            else:
                runs.append((touch.carrier, touch.index, 1))
        return cls(tuple(runs))

    def expand(self) -> tuple[RelationTouch, ...]:
        """Materialize the public tuple only when a report is requested."""
        return tuple(
            RelationTouch(carrier, index)
            for carrier, start, length in self.runs
            for index in range(start, start + length)
        )


@dataclass(frozen=True, slots=True)
class _ReportRecipe:
    """Retain an edit report in space proportional to its changed runs."""

    operation: str
    items: _ReferenceTouchRuns[ItemRef]
    boundaries: _ReferenceTouchRuns[BoundaryRef]
    relations: _RelationTouchRuns
    detached: tuple[LayerSubject, ...]
    displacement: Displacement
    annotations: EditAnnotations
    clock_reports: tuple[ClockEditReport, ...]
    detached_dependencies: tuple[DetachedDependency, ...]
    detached_content: DetachmentReport | None
    pruned_orphans: tuple[PrunedFact, ...]
    correspondence: SubtreeCorrespondence | None
    yield_changes: tuple[ContainmentYieldChange, ...]

    @classmethod
    def create(
        cls,
        operation: str,
        touches: _Touches,
        displacement: Displacement,
        annotations: EditAnnotations,
        clock_reports: tuple[ClockEditReport, ...],
        detached_dependencies: tuple[DetachedDependency, ...] = (),
        detached_content: DetachmentReport | None = None,
        pruned_orphans: tuple[PrunedFact, ...] = (),
        correspondence: SubtreeCorrespondence | None = None,
        yield_changes: tuple[ContainmentYieldChange, ...] = (),
    ) -> _ReportRecipe:
        """Capture eagerly derived touches without retaining expanded tuples."""
        return cls(
            operation,
            _ReferenceTouchRuns.from_references(
                touches.old_items | touches.new_items, ItemRef
            ),
            _ReferenceTouchRuns.from_references(
                touches.old_boundaries | touches.new_boundaries, BoundaryRef
            ),
            _RelationTouchRuns.from_touches(
                touches.old_relations | touches.new_relations
            ),
            touches.detached,
            displacement,
            annotations,
            clock_reports,
            detached_dependencies,
            detached_content,
            pruned_orphans,
            correspondence,
            yield_changes,
        )

    def build(self) -> EditReport:
        """Build a fresh public report without retaining its expanded tuples."""
        return EditReport(
            self.operation,
            self.items.expand(),
            self.boundaries.expand(),
            self.relations.expand(),
            self.detached,
            self.displacement,
            self.annotations,
            self.clock_reports,
            self.detached_dependencies,
            pruned_orphans=self.pruned_orphans,
            detached_content=self.detached_content,
            correspondence=self.correspondence,
            yield_changes=self.yield_changes,
        )

    @classmethod
    def from_report(cls, report: EditReport) -> _ReportRecipe:
        """Compact a report supplied to the public record constructor."""
        return cls(
            report.operation,
            _ReferenceTouchRuns.from_references(report.touched_items, ItemRef),
            _ReferenceTouchRuns.from_references(report.touched_boundaries, BoundaryRef),
            _RelationTouchRuns.from_touches(report.touched_relations),
            report.detached_references,
            report.displacement,
            report.annotations,
            report.clock_reports,
            report.detached_dependencies,
            report.detached_content,
            report.pruned_orphans,
            report.correspondence,
            report.yield_changes,
        )


class _ValueDelta:
    """Restore one immutable value without retaining its unchanged contents."""

    def apply(self, value: object, *, forward: bool) -> object:
        """Apply this patch in one direction after checking its changed content."""
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class _AtomDelta(_ValueDelta):
    before: object
    after: object

    def apply(self, value: object, *, forward: bool) -> object:
        """Exchange one indivisible changed value."""
        expected = self.before if forward else self.after
        if value != expected:
            raise GraphValidationError("changed value no longer matches the edit")
        return self.after if forward else self.before


@dataclass(frozen=True, slots=True)
class _TupleSpliceDelta(_ValueDelta):
    start: int
    before: tuple[object, ...]
    after: tuple[object, ...]
    before_length: int
    after_length: int

    def apply(self, value: object, *, forward: bool) -> object:
        """Exchange one changed tuple span while sharing the unchanged spans."""
        if not isinstance(value, tuple):
            raise GraphValidationError("changed tuple is no longer a tuple")
        expected_length = self.before_length if forward else self.after_length
        expected = self.before if forward else self.after
        replacement = self.after if forward else self.before
        if (
            len(value) != expected_length
            or value[self.start : self.start + len(expected)] != expected
        ):
            raise GraphValidationError("changed tuple span no longer matches the edit")
        return (
            *value[: self.start],
            *replacement,
            *value[self.start + len(expected) :],
        )


@dataclass(frozen=True, slots=True)
class _TuplePositionsDelta(_ValueDelta):
    length: int
    changes: tuple[tuple[int, _ValueDelta], ...]

    def apply(self, value: object, *, forward: bool) -> object:
        """Patch a small set of changed positions in an otherwise shared tuple."""
        if not isinstance(value, tuple) or len(value) != self.length:
            raise GraphValidationError("changed tuple no longer matches the edit")
        result = list(value)
        for position, change in self.changes:
            result[position] = change.apply(result[position], forward=forward)
        return tuple(result)


@dataclass(frozen=True, slots=True)
class _DataclassDelta(_ValueDelta):
    value_type: type[object]
    changes: tuple[tuple[str, _ValueDelta], ...]

    def apply(self, value: object, *, forward: bool) -> object:
        """Patch changed constructor fields of one immutable dataclass value."""
        if type(value) is not self.value_type:
            raise GraphValidationError("changed value no longer has its recorded type")
        replacements = {
            name: change.apply(getattr(value, name), forward=forward)
            for name, change in self.changes
        }
        return cast(Any, replace)(value, **replacements)


def _value_delta(before: object, after: object) -> _ValueDelta:
    """Capture changed tuple spans and dataclass fields, never whole carriers."""
    if isinstance(before, tuple) and isinstance(after, tuple):
        prefix = 0
        limit = min(len(before), len(after))
        while prefix < limit and before[prefix] == after[prefix]:
            prefix += 1
        suffix = 0
        while (
            suffix < limit - prefix
            and before[len(before) - suffix - 1] == after[len(after) - suffix - 1]
        ):
            suffix += 1
        before_end = len(before) - suffix
        after_end = len(after) - suffix
        old = before[prefix:before_end]
        new = after[prefix:after_end]
        if len(old) == len(new) and len(old) <= _POSITIONAL_PATCH_LIMIT:
            return _TuplePositionsDelta(
                len(before),
                tuple(
                    (prefix + offset, _value_delta(left, right))
                    for offset, (left, right) in enumerate(zip(old, new, strict=True))
                    if left != right
                ),
            )
        return _TupleSpliceDelta(prefix, old, new, len(before), len(after))
    if (
        type(before) is type(after)
        and is_dataclass(before)
        and not isinstance(before, type)
    ):
        changed = tuple(
            member
            for member in fields(before)
            if getattr(before, member.name) != getattr(after, member.name)
        )
        parameters = signature(type(before)).parameters.values()
        constructor_names = {
            parameter.name
            for parameter in parameters
            if parameter.kind
            in (Parameter.POSITIONAL_OR_KEYWORD, Parameter.KEYWORD_ONLY)
        }
        if changed and all(
            member.init and member.name in constructor_names for member in changed
        ):
            return _DataclassDelta(
                type(before),
                tuple(
                    (
                        member.name,
                        _value_delta(
                            getattr(before, member.name), getattr(after, member.name)
                        ),
                    )
                    for member in changed
                ),
            )
    return _AtomDelta(before, after)


@dataclass(frozen=True, slots=True)
class _FieldChange:
    name: str
    delta: _ValueDelta


@dataclass(frozen=True, slots=True)
class _GraphDelta:
    changes: tuple[_FieldChange, ...]

    @classmethod
    def between(cls, before: Graph, after: Graph) -> _GraphDelta:
        """Capture positional changes inside semantic carriers."""
        changes: list[_FieldChange] = []
        for name in _GRAPH_FIELDS:
            old = getattr(before, name)
            new = getattr(after, name)
            if old != new:
                changes.append(_FieldChange(name, _value_delta(old, new)))
        return cls(tuple(changes))

    def reverse(self, graph: Graph) -> Graph:
        """Apply captured old values to only the changed carrier positions."""
        try:
            replacements = {
                change.name: change.delta.apply(
                    getattr(graph, change.name), forward=False
                )
                for change in self.changes
            }
        except GraphValidationError as error:
            raise GraphValidationError(
                "cannot undo: changed graph content no longer matches the recorded edit"
            ) from error
        return cast(Graph, cast(Any, replace)(graph, **replacements))

    def forward(self, graph: Graph) -> Graph:
        """Reapply captured new values to only the changed carrier positions."""
        try:
            replacements = {
                change.name: change.delta.apply(
                    getattr(graph, change.name), forward=True
                )
                for change in self.changes
            }
        except GraphValidationError as error:
            raise GraphValidationError(
                "cannot redo: changed graph content no longer matches the recorded inverse"
            ) from error
        return cast(Graph, cast(Any, replace)(graph, **replacements))


@dataclass(frozen=True, slots=True)
class _OperationCall:
    """Apply one public graph operation from its change-sized arguments."""

    method: str
    arguments: tuple[object, ...] = ()


@dataclass(frozen=True, slots=True)
class _OperationSequence:
    """Apply the few primitive calls needed by one exact inverse."""

    calls: tuple[_OperationCall, ...] = ()

    def apply(self, graph: Graph) -> Graph:
        """Apply all calls through one editor and one validation boundary."""
        if not self.calls:
            return graph
        editor = GraphEditor(graph)
        for call in self.calls:
            operation = cast(Callable[..., GraphEditor], getattr(editor, call.method))
            operation(*call.arguments)
        return editor.freeze()


@dataclass(frozen=True, slots=True)
class _OperationPair:
    """Name the forward operation and its exact structural inverse."""

    forward: _OperationSequence
    inverse: _OperationSequence


@dataclass(frozen=True, slots=True)
class _OperationalDelta:
    """Restore structure by operations and only exceptional content by deltas."""

    operations: _OperationPair
    before_residual: _GraphDelta
    after_residual: _GraphDelta

    @property
    def changes(self) -> tuple[_FieldChange, ...]:
        """Expose only retained residual carrier changes for diagnostics."""
        return (*self.before_residual.changes, *self.after_residual.changes)

    def reverse(self, graph: Graph) -> Graph:
        """Remove after-only content, run the inverse, and restore dependents."""
        try:
            raw_after = self.after_residual.reverse(graph)
            raw_before = self.operations.inverse.apply(raw_after)
            return self.before_residual.forward(raw_before)
        except GraphValidationError as error:
            raise GraphValidationError(
                "cannot undo: changed graph content no longer matches the recorded edit"
            ) from error

    def forward(self, graph: Graph) -> Graph:
        """Remove before-only content, rerun the edit, and restore after extras."""
        try:
            raw_before = self.before_residual.reverse(graph)
            raw_after = self.operations.forward.apply(raw_before)
            return self.after_residual.forward(raw_after)
        except GraphValidationError as error:
            raise GraphValidationError(
                "cannot redo: changed graph content no longer matches the recorded inverse"
            ) from error


type _Restoration = _GraphDelta | _OperationalDelta


@dataclass(frozen=True, slots=True)
class EditInverse:
    """Name one recorded inverse and the graph carriers it restores."""

    operation: str
    carriers: tuple[str, ...]
    _delta: _Restoration = field(init=False, repr=False, compare=False)

    @classmethod
    def _create(
        cls, operation: str, carriers: tuple[str, ...], delta: _Restoration
    ) -> EditInverse:
        inverse = cls(operation, carriers)
        object.__setattr__(inverse, "_delta", delta)
        return inverse

    def to_data(self) -> dict[str, JsonValue]:
        """Describe the inverse without exposing retained Python values."""
        return {"operation": self.operation, "carriers": list(self.carriers)}


@dataclass(frozen=True, slots=True, init=False)
class JournalRecord:
    """Keep one operation, its change-sized inverse, and lazy report recipe."""

    operation: str
    inverse: EditInverse
    annotations: EditAnnotations
    _report_recipe: _ReportRecipe = field(init=False, repr=False, compare=False)
    _before_clock_active: bool = field(init=False, repr=False, compare=False)
    _after_clock_active: bool = field(init=False, repr=False, compare=False)
    _provenance_ownership: _ProvenanceOwnershipDelta | None = field(
        init=False, repr=False, compare=False
    )
    _patch_operations: _OperationPair | None = field(
        init=False, repr=False, compare=False
    )

    def __init__(
        self,
        operation: str,
        inverse: EditInverse,
        report: EditReport,
        annotations: EditAnnotations,
    ) -> None:
        """Build a public record while compacting its supplied report."""
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "inverse", inverse)
        object.__setattr__(self, "annotations", annotations)
        object.__setattr__(self, "_report_recipe", _ReportRecipe.from_report(report))
        object.__setattr__(self, "_before_clock_active", True)
        object.__setattr__(self, "_after_clock_active", True)
        object.__setattr__(self, "_provenance_ownership", None)
        object.__setattr__(self, "_patch_operations", None)

    @classmethod
    def _create(
        cls,
        operation: str,
        inverse: EditInverse,
        report: _ReportRecipe,
        annotations: EditAnnotations,
        before_clock_active: bool,
        after_clock_active: bool,
        provenance_ownership: _ProvenanceOwnershipDelta | None,
        patch_operations: _OperationPair | None,
    ) -> JournalRecord:
        record = object.__new__(cls)
        object.__setattr__(record, "operation", operation)
        object.__setattr__(record, "inverse", inverse)
        object.__setattr__(record, "annotations", annotations)
        object.__setattr__(record, "_report_recipe", report)
        object.__setattr__(record, "_before_clock_active", before_clock_active)
        object.__setattr__(record, "_after_clock_active", after_clock_active)
        object.__setattr__(record, "_provenance_ownership", provenance_ownership)
        object.__setattr__(record, "_patch_operations", patch_operations)
        return record

    @property
    def report(self) -> EditReport:
        """Compute the expanded public report only when it is requested."""
        return self._report_recipe.build()

    def to_data(self) -> dict[str, JsonValue]:
        """Return the public journal record as JSON-compatible data."""
        return {
            "operation": self.operation,
            "inverse": self.inverse.to_data(),
            "report": self.report.to_data(),
            "annotations": self.annotations.to_data(),
        }


@dataclass(frozen=True, slots=True)
class _Touches:
    old_items: frozenset[ItemRef]
    new_items: frozenset[ItemRef]
    old_boundaries: frozenset[BoundaryRef]
    new_boundaries: frozenset[BoundaryRef]
    old_relations: frozenset[RelationTouch]
    new_relations: frozenset[RelationTouch]
    old_subjects: frozenset[LayerSubject]
    new_subjects: frozenset[LayerSubject]
    detached: tuple[LayerSubject, ...]


def _item_at(graph: Graph, reference: ItemRef) -> Item:
    return graph._tiers_by_name[reference.tier].items[reference.index]


def _boundary_attributes(graph: Graph, reference: BoundaryRef) -> tuple[Attribute, ...]:
    """Return stored values at one structural boundary, or the empty tuple."""
    boundary = graph._boundaries_by_ref.get(reference)
    return () if boundary is None else boundary.attributes


def _layer_by_name(graph: Graph, name: LayerName) -> Layer | None:
    return next((layer for layer in graph.layers if layer.name == name), None)


def _orphan_facts(graph: Graph) -> tuple[PrunedFact, ...]:
    """Return every orphan fact with its layer in deterministic graph order."""
    return tuple(
        PrunedFact(layer.name, fact)
        for layer in graph.layers
        for fact in layer.facts
        if isinstance(fact.subject, OrphanedSubject)
    )


def _mapped_subject(subject: LayerSubject, step: Displacement) -> LayerSubject | None:
    """Map one structural subject through an edit, or report its departure."""
    if isinstance(subject, ItemRef):
        return None if subject in step.departed_items else step.items[subject]
    if isinstance(subject, BoundaryRef):
        return None if subject in step.departed_boundaries else step.boundaries[subject]
    if isinstance(subject, RelationInstanceRef):
        return (
            None
            if subject.index in step.departed_relations
            else RelationInstanceRef(step.relations[subject.index])
        )
    if isinstance(subject, PolyadicInstanceRef):
        return (
            None
            if subject.index in step.departed_polyadic_relations
            else PolyadicInstanceRef(step.polyadic_relations[subject.index])
        )
    return subject


def _fact_touches(
    before: Graph, after: Graph, step: Displacement
) -> tuple[set[LayerSubject], set[LayerSubject], tuple[LayerSubject, ...]]:
    """Return changed fact subjects and references whose subjects departed."""
    before_facts = {
        (layer.name, fact) for layer in before.layers for fact in layer.facts
    }
    after_facts = {(layer.name, fact) for layer in after.layers for fact in layer.facts}
    old_subjects = {fact.subject for _, fact in before_facts - after_facts}
    new_subjects = {fact.subject for _, fact in after_facts - before_facts}

    def _detached(layer_name: LayerName, fact: LayerFact) -> bool:
        if isinstance(fact.subject, OrphanedSubject):
            return False
        mapped = _mapped_subject(fact.subject, step)
        if mapped is not None and _subject_is_live(after, mapped):
            return False
        return (
            mapped is None
            or (
                layer_name,
                LayerFact(mapped, fact.value),
            )
            not in after_facts
        )

    detached = {
        fact.subject for layer_name, fact in before_facts if _detached(layer_name, fact)
    }
    return old_subjects, new_subjects, tuple(sorted(detached, key=repr))


def _touches(
    before: Graph,
    after: Graph,
    step: Displacement,
    positions: _PositionTouches,
) -> _Touches:
    old_items = positions.old_items
    new_items = positions.new_items
    old_boundaries = positions.old_boundaries
    new_boundaries = positions.new_boundaries
    old_relations = positions.old_relations
    new_relations = positions.new_relations
    old_subjects: set[LayerSubject] = {
        *old_items,
        *old_boundaries,
        *(
            RelationInstanceRef(touch.index)
            if touch.carrier == "relations"
            else PolyadicInstanceRef(touch.index)
            for touch in old_relations
        ),
    }
    new_subjects: set[LayerSubject] = {
        *new_items,
        *new_boundaries,
        *(
            RelationInstanceRef(touch.index)
            if touch.carrier == "relations"
            else PolyadicInstanceRef(touch.index)
            for touch in new_relations
        ),
    }
    if before.attributes != after.attributes:
        old_subjects.add(DocumentRef())
        new_subjects.add(DocumentRef())
    before_tiers = {tier.declaration.name: tier for tier in before.tiers}
    after_tiers = {tier.declaration.name: tier for tier in after.tiers}
    for name in before_tiers.keys() | after_tiers.keys():
        old = before_tiers.get(name)
        new = after_tiers.get(name)
        if old is None:
            new_subjects.add(TierRef(name))
        elif new is None:
            old_subjects.add(TierRef(name))
        elif old.declaration != new.declaration or old.attributes != new.attributes:
            old_subjects.add(TierRef(name))
            new_subjects.add(TierRef(name))
    before_declarations = {
        declaration.name: declaration for declaration in before.relation_declarations
    }
    after_declarations = {
        declaration.name: declaration for declaration in after.relation_declarations
    }
    for name in before_declarations.keys() | after_declarations.keys():
        if before_declarations.get(name) != after_declarations.get(name):
            if name in before_declarations:
                old_subjects.add(RelationDeclarationRef(name))
            if name in after_declarations:
                new_subjects.add(RelationDeclarationRef(name))

    old_fact_subjects, new_fact_subjects, detached = _fact_touches(before, after, step)
    old_subjects.update(old_fact_subjects)
    new_subjects.update(new_fact_subjects)
    return _Touches(
        frozenset(old_items),
        frozenset(new_items),
        frozenset(old_boundaries),
        frozenset(new_boundaries),
        frozenset(old_relations),
        frozenset(new_relations),
        frozenset(old_subjects),
        frozenset(new_subjects),
        detached,
    )


def _inverse_name(operation: str) -> str:
    pairs = {
        "declare": "undeclare",
        "undeclare": "declare",
        "promote_item": "demote_item",
        "promote_boundary": "demote_boundary",
        "promote_relation": "demote_relation",
        "demote_item": "promote_item",
        "demote_boundary": "promote_boundary",
        "demote_relation": "promote_relation",
        "seal": "restore_seal",
        "unseal": "seal",
        "drop_seal": "seal",
        "add_layer": "remove_layer",
        "remove_layer": "add_layer",
        "put_fact": "restore_fact",
        "remove_fact": "put_fact",
        "prune_orphans": "restore_orphans",
        "compact": "restore_orphans",
        "set_attribute": "restore_attribute",
        "remove_attribute": "set_attribute",
        "insert_item": "remove_item",
        "insert_items": "remove_items",
        "remove_item": "insert_item",
        "remove_items": "insert_items",
        "replace_item": "replace_item",
        "move_item": "move_item",
        "move_run": "move_run",
        "shift": "shift",
        "swap_items": "swap_items",
        "swap_runs": "swap_runs",
        "add_relation": "remove_relation",
        "remove_relation": "add_relation",
        "set_endpoints": "set_endpoints",
        "reparent": "reparent",
        "undeclare_with_contents": "restore_declaration_contents",
    }
    return pairs.get(operation, f"undo_{operation}")


def _resolved_subject(graph: Graph, subject: LayerSubject) -> LayerSubject:
    if isinstance(subject, DurableItemRef):
        return graph.resolve_item(subject)
    if isinstance(subject, DurableBoundaryRef):
        return graph.resolve_boundary(subject)
    if isinstance(subject, DurableRelationRef):
        return RelationInstanceRef(
            next(
                index
                for index, relation in enumerate(graph.relations)
                if relation.durable_id == subject.durable_id
            )
        )
    if isinstance(subject, DurablePolyadicRef):
        return PolyadicInstanceRef(
            next(
                index
                for index, relation in enumerate(graph.polyadic_relations)
                if relation.durable_id == subject.durable_id
            )
        )
    return subject


def _subject_is_live(graph: Graph, subject: LayerSubject) -> bool:
    """Say whether a non-orphan layer subject still resolves in this graph."""
    try:
        _subject_content(graph, _resolved_subject(graph, subject))
    except (GraphValidationError, IndexError, KeyError, StopIteration, ValueError):
        return False
    return True


def _check_protected(
    before: Graph,
    candidate: Graph,
    touches: _Touches,
    protected: frozenset[LayerName],
    step: Displacement,
) -> None:
    """Refuse changes to protected facts or their underlying live content."""
    for name in sorted(protected):
        old_layer = _layer_by_name(before, name)
        new_layer = _layer_by_name(candidate, name)
        if old_layer is None:
            raise GraphValidationError(
                f"protected layer {name.vocabulary!r}/{name.source!r} is absent"
            )
        if new_layer is None:
            raise GraphValidationError(
                f"edit would change protected layer {name.vocabulary!r}/{name.source!r}"
            )
        mapped_facts = {
            LayerFact(mapped, fact.value)
            for fact in old_layer.facts
            if (mapped := _mapped_subject(fact.subject, step)) is not None
        }
        if mapped_facts != set(new_layer.facts):
            raise GraphValidationError(
                f"edit would change protected layer {name.vocabulary!r}/{name.source!r}"
            )
        for fact in old_layer.facts:
            if isinstance(fact.subject, OrphanedSubject):
                continue
            try:
                subject = _resolved_subject(before, fact.subject)
            except (StopIteration, ValueError):
                continue
            mapped = _mapped_subject(subject, step)
            if (
                subject in touches.old_subjects
                and mapped is not None
                and _subject_content(before, subject)
                != _subject_content(candidate, mapped)
            ):
                raise GraphValidationError(
                    f"edit would change subject {str(fact.subject)!r} protected by "
                    f"layer {name.vocabulary!r}/{name.source!r}"
                )


def _subject_content(graph: Graph, subject: LayerSubject) -> object:
    """Return the content protected by one resolved live subject."""
    if isinstance(subject, ItemRef):
        return _item_at(graph, subject)
    if isinstance(subject, BoundaryRef):
        return _boundary_attributes(graph, subject)
    if isinstance(subject, RelationInstanceRef):
        return graph.relations[subject.index]
    if isinstance(subject, PolyadicInstanceRef):
        return graph.polyadic_relations[subject.index]
    if isinstance(subject, DocumentRef):
        return graph.attributes
    if isinstance(subject, TierRef):
        tier = graph._tiers_by_name[subject.tier]
        return tier.declaration, tier.attributes
    if isinstance(subject, RelationDeclarationRef):
        return next(
            declaration
            for declaration in graph.relation_declarations
            if declaration.name == subject.relation
        )
    return subject


def _provenance_name(layer: LayerName, domain: AttributeDomain) -> QualifiedName:
    return QualifiedName(
        layer.vocabulary, f"journal-provenance-{domain.value.replace('_', '-')}"
    )


def _subject_domain(subject: LayerSubject) -> AttributeDomain | None:
    if isinstance(subject, ItemRef | DurableItemRef):
        return AttributeDomain.ITEM
    if isinstance(subject, BoundaryRef | DurableBoundaryRef):
        return AttributeDomain.BOUNDARY
    if isinstance(subject, TierRef):
        return AttributeDomain.TIER
    if isinstance(subject, RelationDeclarationRef):
        return AttributeDomain.RELATION_DECLARATION
    if isinstance(
        subject,
        RelationInstanceRef
        | DurableRelationRef
        | PolyadicInstanceRef
        | DurablePolyadicRef,
    ):
        return AttributeDomain.RELATION_INSTANCE
    if isinstance(subject, DocumentRef):
        return AttributeDomain.DOCUMENT
    return None


def _stable_subject(graph: Graph, subject: LayerSubject) -> LayerSubject | None:
    """Return a durable provenance subject, never a bare structural coordinate."""
    if isinstance(subject, ItemRef):
        durable_id = _item_at(graph, subject).durable_id
        return None if durable_id is None else DurableItemRef(durable_id)
    if isinstance(subject, BoundaryRef):
        return next(
            (
                boundary.reference
                for boundary in graph.boundary_values
                if isinstance(boundary.reference, DurableBoundaryRef)
                and graph.resolve_boundary(boundary.reference) == subject
            ),
            None,
        )
    if isinstance(subject, RelationInstanceRef):
        relation = graph.relations[subject.index]
        return (
            None
            if relation.durable_id is None
            else DurableRelationRef(relation.durable_id)
        )
    if isinstance(subject, PolyadicInstanceRef):
        polyadic_relation = graph.polyadic_relations[subject.index]
        return (
            None
            if polyadic_relation.durable_id is None
            else DurablePolyadicRef(polyadic_relation.durable_id)
        )
    if isinstance(subject, OrphanedSubject):
        return None
    return subject


def _is_journal_provenance_fact(layer: LayerName, fact: LayerFact) -> bool:
    """Recognize the typed facts emitted by this journal facility."""
    domain = _subject_domain(fact.subject)
    if domain is None or not isinstance(fact.value, JsonAttributeValue):
        return False
    if fact.value.name != _provenance_name(layer, domain):
        return False
    payload = fact.value.to_value()
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("operation"), str)
        and isinstance(payload.get("annotations"), dict)
    )


def _provenance_subject_is_retired(
    subject: LayerSubject, retired: frozenset[LayerSubject]
) -> bool:
    """Include durable boundaries whose item anchor is being retired."""
    if subject in retired:
        return True
    return isinstance(subject, DurableBoundaryRef) and subject.anchor in retired


def _provenance_key(fact: LayerFact) -> _ProvenanceKey:
    return fact.subject, fact.value.name


def _retire_provenance(
    graph: Graph,
    layer: LayerName,
    subjects: Iterable[LayerSubject] | None,
    owned: frozenset[_ProvenanceKey],
) -> Graph:
    """Remove this facility's facts for retiring subjects before kernel checks."""
    current = _layer_by_name(graph, layer)
    if current is None:
        return graph
    retired = (
        None
        if subjects is None
        else frozenset(
            stable
            for subject in subjects
            if (stable := _stable_subject(graph, subject)) is not None
        )
    )
    facts = tuple(
        fact
        for fact in current.facts
        if not (
            _provenance_key(fact) in owned
            and (
                retired is None or _provenance_subject_is_retired(fact.subject, retired)
            )
        )
    )
    if facts == current.facts:
        return graph
    replacement = replace(current, facts=facts)
    return replace(
        graph,
        layers=tuple(
            replacement if candidate.name == layer else candidate
            for candidate in graph.layers
        ),
    )


def _owned_provenance_facts(
    graph: Graph, layer: LayerName, owned: frozenset[_ProvenanceKey]
) -> tuple[LayerFact, ...]:
    """Return the journal-shaped facts currently carried by its output layer."""
    current = _layer_by_name(graph, layer)
    if current is None:
        return ()
    return tuple(fact for fact in current.facts if _provenance_key(fact) in owned)


def _provenance_keys(
    graph: Graph,
    layer: LayerName,
    acted_subjects: Iterable[LayerSubject] | None = None,
) -> frozenset[_ProvenanceKey]:
    """Return journal-shaped fact keys, optionally limited to acted subjects."""
    current = _layer_by_name(graph, layer)
    if current is None:
        return frozenset()
    subjects = (
        None
        if acted_subjects is None
        else frozenset(
            stable
            for subject in acted_subjects
            if (stable := _stable_subject(graph, subject)) is not None
        )
    )
    return frozenset(
        _provenance_key(fact)
        for fact in current.facts
        if _is_journal_provenance_fact(layer, fact)
        and (subjects is None or fact.subject in subjects)
    )


def _restore_live_provenance_facts(
    graph: Graph, layer: LayerName, facts: Iterable[LayerFact]
) -> Graph:
    """Put back retired facts whose declarations and subjects survived a cascade."""
    if _layer_by_name(graph, layer) is None:
        return graph
    declared = {item.name for item in graph.attribute_declarations}
    result = graph
    for fact in facts:
        if fact.value.name not in declared:
            continue
        try:
            result = GraphEditor(result).put_fact(layer, fact).freeze()
        except GraphValidationError:
            continue
    return result


def _stamp_provenance(
    graph: Graph,
    layer: LayerName,
    operation: str,
    annotations: EditAnnotations,
    acted_subjects: Iterable[LayerSubject],
) -> Graph:
    """Stamp only the durable subjects directly acted on by the operation."""
    subjects: set[LayerSubject] = {
        stable
        for subject in acted_subjects
        if (stable := _stable_subject(graph, subject)) is not None
        and _subject_domain(stable) is not None
    }
    if not subjects:
        return graph
    editor = GraphEditor(graph)
    if layer.vocabulary not in {item.namespace for item in graph.namespaces}:
        prefixes = {item.prefix for item in graph.namespaces}
        prefix = "journal"
        suffix = 2
        while prefix in prefixes:
            prefix = f"journal{suffix}"
            suffix += 1
        editor.declare(NamespaceDeclaration(prefix, layer.vocabulary))
    existing = {item.name: item for item in graph.attribute_declarations}
    domains = {
        domain
        for subject in subjects
        if (domain := _subject_domain(subject)) is not None
    }
    for domain in sorted(domains, key=lambda item: item.value):
        name = _provenance_name(layer, domain)
        declaration = AttributeDeclaration(name, domain, JsonType.JSON)
        current = existing.get(name)
        if current is None:
            editor.declare(declaration)
        elif current != declaration:
            raise GraphValidationError(
                f"provenance attribute {str(name)!r} conflicts with an existing "
                "declaration"
            )
    if _layer_by_name(graph, layer) is None:
        editor.add_layer(layer)
    payload: dict[str, JsonValue] = {
        "operation": operation,
        "annotations": annotations.to_data(),
    }
    for subject in sorted(subjects, key=repr):
        domain = cast(AttributeDomain, _subject_domain(subject))
        editor.put_fact(
            layer,
            LayerFact(
                subject,
                JsonAttributeValue(_provenance_name(layer, domain), payload),
            ),
        )
    return editor.freeze()


@dataclass(frozen=True, slots=True)
class JournalHorizon:
    """Bound history by record count, conservative estimated bytes, or both."""

    count: int | None = None
    bytes: int | None = None

    def __post_init__(self) -> None:
        """Require at least one nonnegative integer bound."""
        if self.count is None and self.bytes is None:
            raise ValueError("a journal horizon needs a count or byte bound")
        for name, value in (("count", self.count), ("bytes", self.bytes)):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"journal horizon {name} must be nonnegative")


def _retained_size(values: object) -> int:
    """Estimate unique Python storage reachable from retained history values."""
    seen: set[int] = set()

    def visit(value: object) -> int:
        """Count one object and its supported immutable children once."""
        identity = id(value)
        if identity in seen:
            return 0
        seen.add(identity)
        size = sys.getsizeof(value)
        if isinstance(value, Mapping):
            return size + sum(visit(key) + visit(item) for key, item in value.items())
        if isinstance(value, tuple | list | set | frozenset):
            return size + sum(visit(item) for item in value)
        if is_dataclass(value) and not isinstance(value, type):
            return size + sum(
                visit(getattr(value, member.name)) for member in fields(value)
            )
        return size

    return visit(values)


def _share_history_value(
    value: object,
    attributes: dict[Attribute, Attribute],
    references: dict[ItemRef, ItemRef],
    memo: dict[int, object],
) -> object:
    """Rebuild retained immutable values through shared representative tables."""
    identity = id(value)
    if identity in memo:
        return memo[identity]
    if isinstance(value, AttributeValue | JsonAttributeValue):
        shared: object = attributes.setdefault(value, value)
    elif isinstance(value, ItemRef):
        shared = references.setdefault(value, value)
    elif isinstance(value, tuple):
        items = tuple(
            _share_history_value(item, attributes, references, memo) for item in value
        )
        shared = (
            value if all(a is b for a, b in zip(value, items, strict=True)) else items
        )
    elif isinstance(value, frozenset):
        frozen_items = frozenset(
            _share_history_value(item, attributes, references, memo) for item in value
        )
        shared = frozen_items
    elif is_dataclass(value) and not isinstance(value, type):
        replacements = {
            member.name: transformed
            for member in fields(value)
            if member.init
            and (
                transformed := _share_history_value(
                    getattr(value, member.name), attributes, references, memo
                )
            )
            is not getattr(value, member.name)
        }
        shared = (
            value if not replacements else cast(Any, replace)(value, **replacements)
        )
    else:
        shared = value
    memo[identity] = shared
    return shared


def _share_journal_history(journal: Journal, graph: Graph) -> None:
    """Intern retained record payloads with the compacted current graph."""
    attributes: dict[Attribute, Attribute] = {
        attribute: attribute
        for tier in graph.tiers
        for item in tier.items
        for attribute in item.attributes
    }
    references: dict[ItemRef, ItemRef] = {
        reference: reference for reference in graph._items_by_id.values()
    }
    for relation in graph.relations:
        for endpoint in (relation.left, relation.right):
            if isinstance(endpoint, ItemRef):
                references.setdefault(endpoint, endpoint)
    for polyadic in graph.polyadic_relations:
        for endpoint in (*polyadic.sources, *polyadic.targets):
            if isinstance(endpoint, ItemRef):
                references.setdefault(endpoint, endpoint)
    memo: dict[int, object] = {}

    def shared_record(record: JournalRecord) -> JournalRecord:
        """Rebuild one record with interned restoration and report values."""
        delta = cast(
            _Restoration,
            _share_history_value(record.inverse._delta, attributes, references, memo),
        )
        inverse = EditInverse._create(
            record.inverse.operation, record.inverse.carriers, delta
        )
        return JournalRecord._create(
            record.operation,
            inverse,
            cast(
                _ReportRecipe,
                _share_history_value(
                    record._report_recipe, attributes, references, memo
                ),
            ),
            record.annotations,
            record._before_clock_active,
            record._after_clock_active,
            record._provenance_ownership,
            cast(
                _OperationPair | None,
                _share_history_value(
                    record._patch_operations, attributes, references, memo
                ),
            ),
        )

    journal._done = [shared_record(record) for record in journal._done]
    journal._undone = [shared_record(record) for record in journal._undone]
    journal._refresh_retained_sizes()


class Journal:
    """Own opt-in edit history and bind it to one editor session.

    Records remain undoable until :meth:`checkpoint` or the optional history
    horizon discards them.  An integer ``horizon`` limits record count;
    :class:`JournalHorizon` can instead limit estimated retained bytes or apply
    both limits.
    ``provenance`` names a layer that receives typed JSON facts only on the
    durably addressable subjects an operation directly acts on.  Those facts
    travel with their subjects and are retired, undoably, when a later journal
    operation removes or demotes the subject.  Operations without a stable graph
    subject remain attributable through their journal record. :meth:`protect`
    prevents edits to facts and the content they describe.
    """

    def __init__(
        self,
        annotations: EditAnnotations | None = None,
        *,
        provenance: LayerName | None = None,
        protected: Iterable[LayerName] = (),
        horizon: int | JournalHorizon | None = None,
        author: str | None = None,
        reason: str | None = None,
        stage: str | None = None,
        confidence: float | None = None,
        iteration: int | None = None,
        tool: str | None = None,
        timestamp: str | None = None,
        fields: Mapping[str, JsonValue] | None = None,
    ) -> None:
        supplied = EditAnnotations(
            author,
            reason,
            stage,
            confidence,
            iteration,
            tool,
            timestamp,
            {} if fields is None else fields,
        )
        self._defaults = (
            supplied if annotations is None else annotations.merged(supplied)
        )
        self._annotation_stack: list[EditAnnotations] = []
        self._provenance = provenance
        self._protected = frozenset(protected)
        if isinstance(horizon, bool) or not isinstance(
            horizon, int | JournalHorizon | None
        ):
            raise TypeError(
                "journal horizon must be an integer, JournalHorizon, or None"
            )
        self._horizon = (
            JournalHorizon(count=horizon) if isinstance(horizon, int) else horizon
        )
        self._owned_provenance: set[_ProvenanceKey] = set()
        self._done: list[JournalRecord] = []
        self._undone: list[JournalRecord] = []
        self._done_sizes: list[int] = []
        self._undone_sizes: list[int] = []
        self._done_bytes = 0
        self._undone_bytes = 0
        self._editor: JournalEditor | ClockJournalEditor | None = None
        self._history_truncated = False
        self._horizon_suspended = 0

    @property
    def records(self) -> tuple[JournalRecord, ...]:
        """Return applied records in operation order."""
        return tuple(self._done)

    @property
    def reports(self) -> tuple[EditReport, ...]:
        """Return reports for all currently applied records."""
        return tuple(record.report for record in self._done)

    @property
    def redo_records(self) -> tuple[JournalRecord, ...]:
        """Return undone records in the order :meth:`redo` will restore them."""
        return tuple(reversed(self._undone))

    @property
    def horizon(self) -> JournalHorizon | None:
        """Return this journal's immutable history-retention policy."""
        return self._horizon

    @property
    def retained_bytes(self) -> int:
        """Return estimated Python storage reachable from retained history.

        A byte-bounded journal maintains a conservative per-record estimate so
        enforcing the bound does not repeatedly scan all retained records.
        Values shared between records or with the live graph may be counted
        more than once.
        """
        if not self._done and not self._undone:
            return 0
        if self._tracks_retained_sizes():
            return (
                self._done_bytes
                + self._undone_bytes
                + sys.getsizeof(self._done)
                + sys.getsizeof(self._undone)
            )
        return _retained_size((self._done, self._undone))

    def checkpoint(self) -> Journal:
        """Make the current graph the undo base and release earlier records.

        Applied and redo history are both discarded.  Live graph content,
        including provenance facts, is unchanged.  A later undo cannot cross
        this boundary.
        """
        if self._editor is None:
            raise GraphValidationError("journal is not attached to an editor")
        if self._horizon_suspended:
            raise GraphValidationError("journal cannot checkpoint during a dry run")
        self._editor._source = self._editor._graph
        self._done.clear()
        self._undone.clear()
        self._done_sizes.clear()
        self._undone_sizes.clear()
        self._done_bytes = 0
        self._undone_bytes = 0
        self._history_truncated = True
        return self

    def protect(self, layer: LayerName) -> Journal:
        """Protect one existing layer's facts and described live content.

        Structural edits may move a protected fact and its unchanged subject to
        new coordinates. A missing layer is an error rather than an inactive
        protection rule, and no edit may create, remove, or replace protected
        facts.
        """
        self._protected = self._protected | {layer}
        return self

    @contextmanager
    def annotate(
        self,
        annotations: EditAnnotations | None = None,
        **fields: object,
    ) -> Iterator[Journal]:
        """Apply caller metadata to every successful operation in this context."""
        known = {
            name: fields.pop(name, None)
            for name in (
                "author",
                "reason",
                "stage",
                "confidence",
                "iteration",
                "tool",
                "timestamp",
            )
        }
        explicit_fields = fields.pop("fields", {})
        if not isinstance(explicit_fields, Mapping):
            raise TypeError("annotation fields must be a mapping")
        overlay = EditAnnotations(
            author=cast(str | None, known["author"]),
            reason=cast(str | None, known["reason"]),
            stage=cast(str | None, known["stage"]),
            confidence=cast(float | None, known["confidence"]),
            iteration=cast(int | None, known["iteration"]),
            tool=cast(str | None, known["tool"]),
            timestamp=cast(str | None, known["timestamp"]),
            fields=cast(Mapping[str, JsonValue], {**explicit_fields, **fields}),
        )
        if annotations is not None:
            overlay = annotations.merged(overlay)
        self._annotation_stack.append(overlay)
        try:
            yield self
        finally:
            self._annotation_stack.pop()

    def undo(self) -> JournalRecord:
        """Undo the latest applied operation, retaining it for redo."""
        if self._editor is None:
            raise GraphValidationError("journal is not attached to an editor")
        if not self._done:
            message = (
                "journal cannot undo past its retained history boundary"
                if self._history_truncated
                else "journal has no operation to undo"
            )
            raise GraphValidationError(message)
        record = self._done[-1]
        self._editor._restore(record, forward=False)
        if record._provenance_ownership is not None:
            added, removed = record._provenance_ownership
            self._owned_provenance.difference_update(added)
            self._owned_provenance.update(removed)
        size = self._done_sizes.pop()
        self._done.pop()
        self._done_bytes -= size
        self._undone.append(record)
        self._undone_sizes.append(size)
        self._undone_bytes += size
        return record

    def redo(self) -> JournalRecord:
        """Reapply the most recently undone operation."""
        if self._editor is None:
            raise GraphValidationError("journal is not attached to an editor")
        if not self._undone:
            raise GraphValidationError("journal has no operation to redo")
        record = self._undone[-1]
        self._editor._restore(record, forward=True)
        if record._provenance_ownership is not None:
            added, removed = record._provenance_ownership
            self._owned_provenance.difference_update(removed)
            self._owned_provenance.update(added)
        size = self._undone_sizes.pop()
        self._undone.pop()
        self._undone_bytes -= size
        self._done.append(record)
        self._done_sizes.append(size)
        self._done_bytes += size
        return record

    def to_patch(self) -> Patch:
        """Return the applied history as a fingerprint-guarded public patch.

        The import is local because the patch container depends on journal
        annotations. Each emitted opcode carries only guarded document changes,
        plus the exact reverse changes under the journal's inverse operation name.
        """
        from tiergraph.equivalence import (  # noqa: PLC0415
            EquivalenceView,
            fingerprint,
        )
        from tiergraph.machine import DeltaOpcode  # noqa: PLC0415
        from tiergraph.patch import Patch, PatchOperation  # noqa: PLC0415

        if self._editor is None:
            raise GraphValidationError("journal is not attached to an editor")
        base = self._editor._source
        cursor = base
        operations: list[PatchOperation] = []
        for record in self._done:
            target = record.inverse._delta.forward(cursor)
            patch_operations = record._patch_operations
            if patch_operations is not None:
                forward_calls = tuple(
                    (call.method, call.arguments)
                    for call in patch_operations.forward.calls
                )
                inverse_calls = tuple(
                    (call.method, call.arguments)
                    for call in patch_operations.inverse.calls
                )
            else:
                forward_calls = ()
                inverse_calls = ()
            try:
                opcode = DeltaOpcode.between(
                    record.operation, cursor, target, forward_calls
                )
            except (GraphValidationError, TypeError, ValueError):
                opcode = DeltaOpcode.between("delta", cursor, target)
            try:
                inverse = DeltaOpcode.between(
                    record.inverse.operation, target, cursor, inverse_calls
                )
            except (GraphValidationError, TypeError, ValueError):
                inverse = DeltaOpcode.between("delta", target, cursor)
            operations.append(
                PatchOperation(
                    opcode,
                    inverse,
                    fingerprint(cursor, EquivalenceView.IDENTIFIED),
                    fingerprint(target, EquivalenceView.IDENTIFIED),
                    record.annotations,
                )
            )
            cursor = target
        if cursor != self._editor._graph:  # pragma: no cover - journal invariant
            raise GraphValidationError(
                "journal records do not replay to the attached editor state"
            )
        return Patch(
            fingerprint(base, EquivalenceView.IDENTIFIED),
            fingerprint(cursor, EquivalenceView.IDENTIFIED),
            tuple(operations),
            self._defaults,
        )

    def _annotations(self) -> EditAnnotations:
        result = self._defaults
        for overlay in self._annotation_stack:
            result = result.merged(overlay)
        return result

    def _record(self, record: JournalRecord) -> None:
        self._done.append(record)
        size = _retained_size(record) if self._tracks_retained_sizes() else 0
        self._done_sizes.append(size)
        self._done_bytes += size
        self._undone.clear()
        self._undone_sizes.clear()
        self._undone_bytes = 0
        self._enforce_horizon()

    def _tracks_retained_sizes(self) -> bool:
        """Report whether byte-bound enforcement needs incremental estimates."""
        return self._horizon is not None and self._horizon.bytes is not None

    def _refresh_retained_sizes(self) -> None:
        """Recompute estimates after retained records have been rebuilt."""
        if self._tracks_retained_sizes():
            self._done_sizes = [_retained_size(record) for record in self._done]
            self._undone_sizes = [_retained_size(record) for record in self._undone]
        else:
            self._done_sizes = [0] * len(self._done)
            self._undone_sizes = [0] * len(self._undone)
        self._done_bytes = sum(self._done_sizes)
        self._undone_bytes = sum(self._undone_sizes)

    def _enforce_horizon(self) -> None:
        """Advance the journal base until every configured bound is met."""
        if self._horizon is None or self._horizon_suspended:
            return
        while self._done and (
            (self._horizon.count is not None and len(self._done) > self._horizon.count)
            or (
                self._horizon.bytes is not None
                and self.retained_bytes > self._horizon.bytes
            )
        ):
            assert self._editor is not None
            dropped = self._done.pop(0)
            self._done_bytes -= self._done_sizes.pop(0)
            self._editor._source = dropped.inverse._delta.forward(self._editor._source)
            self._history_truncated = True

    def _attach_graph(
        self, graph: Graph, *, check_links: bool = False
    ) -> JournalEditor:
        if self._editor is not None:
            raise GraphValidationError("journal is already attached to an editor")
        editor = JournalEditor(graph, self, check_links=check_links)
        self._editor = editor
        return editor

    def _attach_clock(
        self,
        profile: ClockProfile,
        rebinding: ClockRebindingPolicy | str | None,
        blob: BlobProfile | None,
    ) -> ClockJournalEditor:
        if self._editor is not None:
            raise GraphValidationError("journal is already attached to an editor")
        editor = ClockJournalEditor(profile, rebinding, self, blob=blob)
        self._editor = editor
        return editor


class _JournalEditorBase:
    """Share history, dry-run, protection, and provenance mechanics."""

    def __init__(
        self, graph: Graph, journal: Journal, *, check_links: bool = False
    ) -> None:
        if not isinstance(check_links, bool):
            raise TypeError("check_links must be a boolean")
        self._source = graph
        self._graph = graph
        self._journal = journal
        if check_links:
            self._check_links = True

    def freeze(self) -> Graph:
        """Return the current fully validated graph."""
        return self._graph

    def displacement(self) -> Displacement:
        """Return where every position of this session's input now stands."""
        result = Displacement.stationary(self._source)
        for record in self._journal._done:
            result = result.then(record._report_recipe.displacement)
        return result

    def undo(self) -> JournalRecord:
        """Undo the latest operation in this editor's journal."""
        return self._journal.undo()

    def redo(self) -> JournalRecord:
        """Redo the latest operation undone in this editor's journal."""
        return self._journal.redo()

    def dry_run(self, operation: Callable[[Any], object]) -> tuple[EditReport, ...]:
        """Apply, validate, report, and roll back new operations by inverses."""
        self._journal._horizon_suspended += 1
        try:
            return self._dry_run(operation)
        finally:
            self._journal._horizon_suspended -= 1

    def _dry_run(self, operation: Callable[[Any], object]) -> tuple[EditReport, ...]:
        """Run rollback mechanics while automatic history trimming is paused."""
        prior_done = list(self._journal._done)
        prior_done_sizes = list(self._journal._done_sizes)
        prior_done_bytes = self._journal._done_bytes
        done = len(prior_done)
        prior_undone = list(self._journal._undone)
        prior_undone_sizes = list(self._journal._undone_sizes)
        prior_undone_bytes = self._journal._undone_bytes
        prior_owned = set(self._journal._owned_provenance)
        try:
            operation(self)
            reports = tuple(record.report for record in self._journal._done[done:])
        except BaseException as original:
            try:
                while len(self._journal._done) > done:
                    self._journal.undo()
            except BaseException as rollback:
                self._journal._done = prior_done
                self._journal._undone = prior_undone
                self._journal._done_sizes = prior_done_sizes
                self._journal._undone_sizes = prior_undone_sizes
                self._journal._done_bytes = prior_done_bytes
                self._journal._undone_bytes = prior_undone_bytes
                self._journal._owned_provenance = prior_owned
                original.add_note(
                    f"dry-run rollback also failed: {type(rollback).__name__}: "
                    f"{rollback}"
                )
                raise original from rollback
            self._journal._undone = prior_undone
            self._journal._undone_sizes = prior_undone_sizes
            self._journal._undone_bytes = prior_undone_bytes
            self._journal._owned_provenance = prior_owned
            raise
        try:
            while len(self._journal._done) > done:
                self._journal.undo()
        except BaseException as rollback:
            self._journal._done = prior_done
            self._journal._undone = prior_undone
            self._journal._done_sizes = prior_done_sizes
            self._journal._undone_sizes = prior_undone_sizes
            self._journal._done_bytes = prior_done_bytes
            self._journal._undone_bytes = prior_undone_bytes
            self._journal._owned_provenance = prior_owned
            failure = GraphValidationError(
                "dry-run operation succeeded but rollback failed"
            )
            failure.add_note(f"rollback error: {type(rollback).__name__}: {rollback}")
            raise failure from rollback
        self._journal._undone = prior_undone
        self._journal._undone_sizes = prior_undone_sizes
        self._journal._undone_bytes = prior_undone_bytes
        self._journal._owned_provenance = prior_owned
        return reports

    def _finish(
        self,
        operation: str,
        candidate: Graph,
        step: Displacement,
        *,
        operations: _OperationPair | None = None,
        patch_operations: _OperationPair | None = None,
        operation_before: Graph | None = None,
        provenance_subjects: Iterable[LayerSubject] = (),
        clock_reports: tuple[ClockEditReport, ...] = (),
        before_clock_active: bool = True,
        after_clock_active: bool = True,
        detached_dependencies: tuple[DetachedDependency, ...] = (),
        detached_content: DetachmentReport | None = None,
        pruned_orphans: tuple[PrunedFact, ...] = (),
        correspondence: SubtreeCorrespondence | None = None,
        yield_changes: tuple[ContainmentYieldChange, ...] = (),
    ) -> None:
        before = self._graph
        acted_subjects = tuple(provenance_subjects)
        before_owned = frozenset(self._journal._owned_provenance)
        raw_before = before if operation_before is None else operation_before
        semantic_candidate = candidate
        annotations = self._journal._annotations()
        compact_step, positions = _compact_displacement_with_touches(
            step, before, candidate
        )
        touches = _touches(before, candidate, compact_step, positions)
        if self._journal._provenance is not None:
            candidate = _stamp_provenance(
                candidate,
                self._journal._provenance,
                operation,
                annotations,
                acted_subjects,
            )
            current_keys = _provenance_keys(candidate, self._journal._provenance)
            acted_keys = _provenance_keys(
                candidate, self._journal._provenance, acted_subjects
            )
            after_owned = (before_owned & current_keys) | acted_keys
        else:
            after_owned = before_owned
        _check_protected(
            before,
            candidate,
            touches,
            self._journal._protected,
            compact_step,
        )
        complete_delta = _GraphDelta.between(before, candidate)
        if operations is None:
            restoration: _Restoration = complete_delta
        else:
            restoration = _OperationalDelta(
                operations,
                _GraphDelta.between(raw_before, before),
                _GraphDelta.between(semantic_candidate, candidate),
            )
        report = _ReportRecipe.create(
            operation,
            touches,
            compact_step,
            annotations,
            clock_reports,
            detached_dependencies,
            detached_content,
            pruned_orphans,
            correspondence,
            yield_changes,
        )
        inverse = EditInverse._create(
            _inverse_name(operation),
            tuple(change.name for change in complete_delta.changes),
            restoration,
        )
        record = JournalRecord._create(
            operation,
            inverse,
            report,
            annotations,
            before_clock_active,
            after_clock_active,
            (
                (after_owned - before_owned, before_owned - after_owned)
                if before_owned != after_owned
                else None
            ),
            operations if patch_operations is None else patch_operations,
        )
        if getattr(self, "_check_links", False):
            link_ledger(before, candidate, record)
        self._graph = candidate
        self._journal._owned_provenance = set(after_owned)
        self._journal._record(record)

    def _restore(self, record: JournalRecord, *, forward: bool) -> None:
        delta = record.inverse._delta
        self._graph = (
            delta.forward(self._graph) if forward else delta.reverse(self._graph)
        )


def _operation(method: str, *arguments: object) -> _OperationSequence:
    """Build a one-call structural operation sequence."""
    return _OperationSequence((_OperationCall(method, arguments),))


def _operation_pair(
    forward_method: str,
    forward_arguments: tuple[object, ...],
    inverse_method: str,
    inverse_arguments: tuple[object, ...],
) -> _OperationPair:
    """Build the common one-call forward/inverse record."""
    return _OperationPair(
        _operation(forward_method, *forward_arguments),
        _operation(inverse_method, *inverse_arguments),
    )


def _relation_coordinate(
    graph: Graph, target: RelationTarget
) -> RelationInstanceRef | PolyadicInstanceRef:
    """Resolve a relation target to its stable carrier-local current position."""
    polyadic, index = GraphEditor(graph)._relation_site(target)
    return PolyadicInstanceRef(index) if polyadic else RelationInstanceRef(index)


def _declaration_subject(
    graph: Graph, target: str | QualifiedName | EditDeclaration
) -> LayerSubject | None:
    """Return the layer-addressable subject for one declaration, if any."""
    site = GraphEditor(graph)._declaration_site(target)
    if site.kind == "tier":
        return TierRef(cast(Any, site.declaration).name)
    if site.kind == "relation":
        return RelationDeclarationRef(cast(Any, site.declaration).name)
    return None


def _new_declaration_subject(declaration: EditDeclaration) -> LayerSubject | None:
    """Return the subject introduced by a layer-addressable declaration."""
    if isinstance(declaration, TierDeclaration):
        return TierRef(declaration.name)
    if isinstance(
        declaration,
        SimpleRelationDeclaration
        | BipartiteRelationDeclaration
        | PolyadicRelationDeclaration,
    ):
        return RelationDeclarationRef(declaration.name)
    return None


def _attribute_subject(
    graph: Graph, target: EditTarget, name: QualifiedName
) -> LayerSubject | None:
    """Resolve the subject directly addressed by one attribute operation."""
    declaration = next(
        (item for item in graph.attribute_declarations if item.name == name), None
    )
    if declaration is None:
        return None
    domain = declaration.domain
    subject: LayerSubject
    if domain is AttributeDomain.DOCUMENT:
        subject = DocumentRef()
    elif domain is AttributeDomain.TIER:
        if not isinstance(target, QualifiedName):
            return None
        subject = TierRef(target)
    elif domain is AttributeDomain.ITEM:
        if not isinstance(target, ItemRef | DurableItemRef):
            return None
        subject = target
    elif domain is AttributeDomain.BOUNDARY:
        if not isinstance(target, BoundaryRef | DurableBoundaryRef):
            return None
        subject = target
    elif domain is AttributeDomain.RELATION_DECLARATION:
        if not isinstance(target, QualifiedName):
            return None
        subject = RelationDeclarationRef(target)
    else:
        try:
            subject = _relation_coordinate(graph, cast(RelationTarget, target))
        except (GraphValidationError, TypeError):
            return None
    return _stable_subject(graph, subject)


def _attribute_at(
    graph: Graph, target: EditTarget, name: QualifiedName
) -> Attribute | None:
    """Return the named value at an edit target, if it is present."""
    declaration = next(
        (item for item in graph.attribute_declarations if item.name == name), None
    )
    if declaration is None:
        return None
    if declaration.domain is AttributeDomain.DOCUMENT:
        attributes = graph.attributes
    elif declaration.domain is AttributeDomain.TIER and isinstance(
        target, QualifiedName
    ):
        attributes = graph._tiers_by_name[target].attributes
    elif declaration.domain is AttributeDomain.ITEM and isinstance(
        target, ItemRef | DurableItemRef
    ):
        attributes = _item_at(graph, graph.resolve_item(target)).attributes
    elif declaration.domain is AttributeDomain.BOUNDARY and isinstance(
        target, BoundaryRef | DurableBoundaryRef
    ):
        attributes = _boundary_attributes(graph, graph.resolve_boundary(target))
    elif declaration.domain is AttributeDomain.RELATION_DECLARATION and isinstance(
        target, QualifiedName
    ):
        attributes = next(
            item.attributes
            for item in graph.relation_declarations
            if item.name == target
        )
    elif declaration.domain is AttributeDomain.RELATION_INSTANCE:
        coordinate = _relation_coordinate(graph, cast(RelationTarget, target))
        if isinstance(coordinate, PolyadicInstanceRef):
            attributes = graph.polyadic_relations[coordinate.index].attributes
        else:
            attributes = graph.relations[coordinate.index].attributes
    else:
        return None
    return next((value for value in attributes if value.name == name), None)


def _present_subject(subject: LayerSubject | None) -> tuple[LayerSubject, ...]:
    """Turn one optional resolved subject into an iterable for provenance."""
    return () if subject is None else (subject,)


class JournalEditor(_JournalEditorBase):
    """Apply fully validated graph edits while recording an opt-in journal."""

    def freeze(self) -> Graph:
        """Return the current fully validated graph."""
        return super().freeze()

    def displacement(self) -> Displacement:
        """Return where every position of this session's input now stands."""
        return super().displacement()

    def undo(self) -> JournalRecord:
        """Undo the latest operation in this editor's journal."""
        return super().undo()

    def redo(self) -> JournalRecord:
        """Redo the latest operation undone in this editor's journal."""
        return super().redo()

    def dry_run(self, operation: Callable[[Any], object]) -> tuple[EditReport, ...]:
        """Apply, validate, report, and roll back new operations by inverses."""
        return super().dry_run(operation)

    def _apply(
        self,
        operation: str,
        edit: Callable[[GraphEditor], GraphEditor],
        operations: _OperationPair | None = None,
        *,
        patch_operations: _OperationPair | None = None,
        provenance_subjects: Iterable[LayerSubject] = (),
        retire_subjects: Iterable[LayerSubject] = (),
        retire_all_provenance: bool = False,
        correspondence: SubtreeCorrespondence | None = None,
    ) -> JournalEditor:
        acted = tuple(provenance_subjects)
        retiring = tuple(retire_subjects)
        operation_before = self._graph
        if self._journal._provenance is not None and (
            retiring or retire_all_provenance
        ):
            operation_before = _retire_provenance(
                operation_before,
                self._journal._provenance,
                None if retire_all_provenance else retiring,
                frozenset(self._journal._owned_provenance),
            )
        editor = GraphEditor(operation_before)
        edit(editor)
        candidate = editor.freeze()
        if operations is not None and candidate == operation_before:
            operations = _OperationPair(_OperationSequence(), _OperationSequence())
        if patch_operations is not None and candidate == operation_before:
            patch_operations = _OperationPair(
                _OperationSequence(), _OperationSequence()
            )
        self._finish(
            operation,
            candidate,
            editor.displacement(),
            operations=operations,
            patch_operations=patch_operations,
            operation_before=operation_before,
            provenance_subjects=acted,
            detached_content=editor.last_detachment,
            correspondence=correspondence,
            yield_changes=editor.last_yield_changes,
        )
        return self

    def declare(
        self, declaration: EditDeclaration, at: int | None = None
    ) -> JournalEditor:
        """Declare one schema member and record its inverse."""
        operations = _operation_pair(
            "declare", (declaration, at), "undeclare", (declaration,)
        )
        return self._apply(
            "declare",
            lambda editor: editor.declare(declaration, at),
            operations,
            provenance_subjects=_present_subject(_new_declaration_subject(declaration)),
        )

    def undeclare(self, target: str | QualifiedName | EditDeclaration) -> JournalEditor:
        """Undeclare one unused schema member and record its inverse."""
        site = GraphEditor(self._graph)._declaration_site(target)
        subject = _declaration_subject(self._graph, target)
        operations = _operation_pair(
            "undeclare",
            (site.declaration,),
            "declare",
            (site.declaration, site.index),
        )
        return self._apply(
            "undeclare",
            lambda editor: editor.undeclare(target),
            operations,
            retire_subjects=_present_subject(subject),
        )

    def undeclare_with_contents(
        self, target: str | QualifiedName | EditDeclaration
    ) -> JournalEditor:
        """Cascade one declaration and record the complete inverse delta."""
        operation_before = self._graph
        owned: tuple[LayerFact, ...] = ()
        if self._journal._provenance is not None:
            keys = frozenset(self._journal._owned_provenance)
            owned = _owned_provenance_facts(
                operation_before, self._journal._provenance, keys
            )
            operation_before = _retire_provenance(
                operation_before, self._journal._provenance, None, keys
            )
        candidate = undeclare_with_contents(operation_before, target)
        if self._journal._provenance is not None:
            candidate = _restore_live_provenance_facts(
                candidate, self._journal._provenance, owned
            )
        self._finish(
            "undeclare_with_contents",
            candidate,
            _displacement_between(self._graph, candidate),
            operation_before=operation_before,
        )
        return self

    def promote_item(self, reference: ItemRef, durable_id: str) -> JournalEditor:
        """Promote one item and record its inverse."""
        coordinate = self._graph.resolve_item(reference)
        operations = _operation_pair(
            "promote_item",
            (coordinate, durable_id),
            "demote_item",
            (DurableItemRef(durable_id),),
        )
        return self._apply(
            "promote_item",
            lambda editor: editor.promote_item(reference, durable_id),
            operations,
            provenance_subjects=(DurableItemRef(durable_id),),
        )

    def promote_boundary(
        self, reference: BoundaryRef, durable_id: str
    ) -> JournalEditor:
        """Promote one boundary and record its inverse."""
        tier = self._graph._tiers_by_name.get(reference.tier)
        created_item_id = (
            tier is not None
            and 0 < reference.index < len(tier.items)
            and tier.items[reference.index].durable_id is None
        )
        if reference.index == 0:
            durable = DurableBoundaryRef(reference.tier, BoundarySide.BEFORE)
        elif tier is not None and reference.index == len(tier.items):
            durable = DurableBoundaryRef(reference.tier, BoundarySide.AFTER)
        else:
            durable = DurableBoundaryRef(
                DurableItemRef(durable_id), BoundarySide.BEFORE
            )
        inverse = [_OperationCall("demote_boundary", (durable,))]
        if created_item_id:
            inverse.append(_OperationCall("demote_item", (DurableItemRef(durable_id),)))
        operations = _OperationPair(
            _operation("promote_boundary", reference, durable_id),
            _OperationSequence(tuple(inverse)),
        )
        return self._apply(
            "promote_boundary",
            lambda editor: editor.promote_boundary(reference, durable_id),
            operations,
            provenance_subjects=(durable,),
        )

    def promote_relation(
        self, target: RelationTarget, durable_id: str
    ) -> JournalEditor:
        """Promote one relation instance and record its inverse."""
        coordinate = _relation_coordinate(self._graph, target)
        durable: DurableRelationRef | DurablePolyadicRef = (
            DurablePolyadicRef(durable_id)
            if isinstance(coordinate, PolyadicInstanceRef)
            else DurableRelationRef(durable_id)
        )
        operations = _operation_pair(
            "promote_relation",
            (coordinate, durable_id),
            "demote_relation",
            (durable,),
        )
        return self._apply(
            "promote_relation",
            lambda editor: editor.promote_relation(target, durable_id),
            operations,
            provenance_subjects=(durable,),
        )

    def demote_item(self, reference: DurableItemRef) -> JournalEditor:
        """Demote one item and record its inverse."""
        coordinate = self._graph.resolve_item(reference)
        operations = _operation_pair(
            "demote_item",
            (reference,),
            "promote_item",
            (coordinate, reference.durable_id),
        )
        return self._apply(
            "demote_item",
            lambda editor: editor.demote_item(reference),
            operations,
            retire_subjects=(reference,),
        )

    def demote_boundary(self, reference: DurableBoundaryRef) -> JournalEditor:
        """Demote one boundary and record its inverse."""
        coordinate = self._graph.resolve_boundary(reference)
        durable_id = (
            reference.anchor.durable_id
            if isinstance(reference.anchor, DurableItemRef)
            else "journal-inverse"
        )
        operations = _operation_pair(
            "demote_boundary",
            (reference,),
            "promote_boundary",
            (coordinate, durable_id),
        )
        return self._apply(
            "demote_boundary",
            lambda editor: editor.demote_boundary(reference),
            operations,
            retire_subjects=(reference,),
        )

    def demote_relation(
        self, reference: DurableRelationRef | DurablePolyadicRef
    ) -> JournalEditor:
        """Demote one relation instance and record its inverse."""
        coordinate = _relation_coordinate(self._graph, reference)
        operations = _operation_pair(
            "demote_relation",
            (reference,),
            "promote_relation",
            (coordinate, reference.durable_id),
        )
        return self._apply(
            "demote_relation",
            lambda editor: editor.demote_relation(reference),
            operations,
            retire_subjects=(reference,),
        )

    def seal(self, carrier: SealedCarrier, sealed: int) -> JournalEditor:
        """Advance a seal and record its previous state."""
        current = GraphEditor(self._graph)._seal_for(carrier)
        inverse = (
            _operation("drop_seal", carrier)
            if current is None
            else _operation("unseal", carrier, current.sealed)
        )
        operations = _OperationPair(_operation("seal", carrier, sealed), inverse)
        return self._apply(
            "seal", lambda editor: editor.seal(carrier, sealed), operations
        )

    def unseal(self, carrier: SealedCarrier, sealed: int) -> JournalEditor:
        """Retreat a seal and record its previous state."""
        current = GraphEditor(self._graph)._seal_for(carrier)
        old = sealed if current is None else current.sealed
        operations = _operation_pair(
            "unseal", (carrier, sealed), "seal", (carrier, old)
        )
        return self._apply(
            "unseal", lambda editor: editor.unseal(carrier, sealed), operations
        )

    def drop_seal(self, carrier: SealedCarrier) -> JournalEditor:
        """Drop a seal and record its previous state."""
        current = GraphEditor(self._graph)._seal_for(carrier)
        old = 0 if current is None else current.sealed
        operations = _operation_pair("drop_seal", (carrier,), "seal", (carrier, old))
        return self._apply(
            "drop_seal", lambda editor: editor.drop_seal(carrier), operations
        )

    def add_layer(self, name: LayerName) -> JournalEditor:
        """Add an empty layer and record its inverse."""
        operations = _operation_pair("add_layer", (name,), "remove_layer", (name,))
        return self._apply(
            "add_layer",
            lambda editor: editor.add_layer(name),
            patch_operations=operations,
        )

    def remove_layer(self, name: LayerName) -> JournalEditor:
        """Remove an empty layer and record its inverse."""
        operations = _operation_pair("remove_layer", (name,), "add_layer", (name,))
        return self._apply(
            "remove_layer",
            lambda editor: editor.remove_layer(name),
            patch_operations=operations,
            retire_all_provenance=name == self._journal._provenance,
        )

    def put_fact(self, layer: LayerName, fact: LayerFact) -> JournalEditor:
        """Put one layer fact and record the prior fact state."""
        current = _layer_by_name(self._graph, layer)
        prior = (
            None
            if current is None
            else next(
                (
                    candidate
                    for candidate in current.facts
                    if (candidate.subject, candidate.value.name)
                    == (fact.subject, fact.value.name)
                ),
                None,
            )
        )
        inverse = (
            _operation("remove_fact", layer, fact.subject, fact.value.name)
            if prior is None
            else _operation("put_fact", layer, prior)
        )
        operations = _OperationPair(_operation("put_fact", layer, fact), inverse)
        return self._apply(
            "put_fact",
            lambda editor: editor.put_fact(layer, fact),
            patch_operations=operations,
            provenance_subjects=(fact.subject,),
        )

    def remove_fact(
        self, layer: LayerName, subject: LayerSubject, name: QualifiedName
    ) -> JournalEditor:
        """Remove one layer fact and record it for restoration."""
        current = _layer_by_name(self._graph, layer)
        prior = (
            None
            if current is None
            else next(
                (
                    fact
                    for fact in current.facts
                    if (fact.subject, fact.value.name) == (subject, name)
                ),
                None,
            )
        )
        operations = (
            None
            if prior is None
            else _operation_pair(
                "remove_fact", (layer, subject, name), "put_fact", (layer, prior)
            )
        )
        return self._apply(
            "remove_fact",
            lambda editor: editor.remove_fact(layer, subject, name),
            patch_operations=operations,
            provenance_subjects=(subject,),
        )

    def prune_orphans(self) -> JournalEditor:
        """Remove and report orphaned layer facts as one undoable edit."""
        return self._cleanup_orphans("prune_orphans", compact=False)

    def compact(self) -> JournalEditor:
        """Prune orphans, compact live storage, and re-intern retained history."""
        result = self._cleanup_orphans("compact", compact=True)
        _share_journal_history(self._journal, self._graph)
        return result

    def _cleanup_orphans(self, operation: str, *, compact: bool) -> JournalEditor:
        """Apply one explicit cleanup operation and retain every removed fact."""
        removed = _orphan_facts(self._graph)
        forward = _operation(operation)
        inverse = _OperationSequence(
            tuple(
                _OperationCall("put_fact", (value.layer, value.fact))
                for value in removed
            )
        )
        operations = _OperationPair(forward, inverse)
        editor = GraphEditor(self._graph)
        if compact:
            editor.compact()
        else:
            editor.prune_orphans()
        candidate = editor.freeze()
        if compact:
            candidate = candidate.share_values()
        self._finish(
            operation,
            candidate,
            editor.displacement(),
            operations=operations,
            patch_operations=operations,
            pruned_orphans=removed,
        )
        return self

    def set_attribute(self, target: EditTarget, value: Attribute) -> JournalEditor:
        """Set one attribute and record the prior value or absence."""
        subject = _attribute_subject(self._graph, target, value.name)
        prior = _attribute_at(self._graph, target, value.name)
        inverse = (
            _operation("remove_attribute", target, value.name)
            if prior is None
            else _operation("set_attribute", target, prior)
        )
        operations = _OperationPair(_operation("set_attribute", target, value), inverse)
        return self._apply(
            "set_attribute",
            lambda editor: editor.set_attribute(target, value),
            patch_operations=operations,
            provenance_subjects=_present_subject(subject),
        )

    def remove_attribute(
        self, target: EditTarget, name: QualifiedName
    ) -> JournalEditor:
        """Remove one attribute and record it for restoration."""
        subject = _attribute_subject(self._graph, target, name)
        prior = _attribute_at(self._graph, target, name)
        operations = (
            None
            if prior is None
            else _operation_pair(
                "remove_attribute",
                (target, name),
                "set_attribute",
                (target, prior),
            )
        )
        return self._apply(
            "remove_attribute",
            lambda editor: editor.remove_attribute(target, name),
            patch_operations=operations,
            provenance_subjects=_present_subject(subject),
        )

    def insert_item(self, tier: QualifiedName, index: int, item: Item) -> JournalEditor:
        """Insert one item and record its structural inverse."""
        operations = _operation_pair(
            "insert_item",
            (tier, index, item),
            "remove_items",
            (tier, index, 1),
        )
        return self._apply(
            "insert_item",
            lambda editor: editor.insert_item(tier, index, item),
            operations,
            provenance_subjects=(ItemRef(tier, index),),
        )

    def insert_items(
        self, tier: QualifiedName, index: int, items: Iterable[Item]
    ) -> JournalEditor:
        """Insert ordered items and record their structural inverse."""
        if isinstance(items, Set | Mapping):
            return self._apply(
                "insert_items", lambda editor: editor.insert_items(tier, index, items)
            )
        values = tuple(items)
        operations = _operation_pair(
            "insert_items",
            (tier, index, values),
            "remove_items",
            (tier, index, len(values)),
        )
        return self._apply(
            "insert_items",
            lambda editor: editor.insert_items(tier, index, values),
            operations,
            provenance_subjects=tuple(
                ItemRef(tier, index + offset) for offset in range(len(values))
            ),
        )

    def remove_item(self, reference: ItemRef | DurableItemRef) -> JournalEditor:
        """Remove one item and retain it in the inverse delta."""
        coordinate = self._graph.resolve_item(reference)
        item = _item_at(self._graph, coordinate)
        operations = _operation_pair(
            "remove_item",
            (coordinate,),
            "insert_item",
            (coordinate.tier, coordinate.index, item),
        )
        return self._apply(
            "remove_item",
            lambda editor: editor.remove_item(reference),
            operations,
            retire_subjects=_present_subject(_stable_subject(self._graph, coordinate)),
        )

    def remove_items(
        self, tier: QualifiedName, index: int, count: int
    ) -> JournalEditor:
        """Remove an item run and retain it in the inverse delta."""
        member = self._graph._tiers_by_name.get(tier)
        removed = (
            () if member is None or count < 0 else member.items[index : index + count]
        )
        operations = _operation_pair(
            "remove_items",
            (tier, index, count),
            "insert_items",
            (tier, index, removed),
        )
        return self._apply(
            "remove_items",
            lambda editor: editor.remove_items(tier, index, count),
            operations,
            retire_subjects=tuple(
                stable
                for offset in range(len(removed) if index >= 0 else 0)
                if (
                    stable := _stable_subject(
                        self._graph, ItemRef(tier, index + offset)
                    )
                )
                is not None
            ),
        )

    def replace_item(
        self, reference: ItemRef | DurableItemRef, item: Item
    ) -> JournalEditor:
        """Replace one item and retain its prior value."""
        coordinate = self._graph.resolve_item(reference)
        prior = _item_at(self._graph, coordinate)
        operations = _operation_pair(
            "replace_item", (coordinate, item), "replace_item", (coordinate, prior)
        )
        return self._apply(
            "replace_item",
            lambda editor: editor.replace_item(reference, item),
            patch_operations=operations,
            provenance_subjects=_present_subject(
                _stable_subject(self._graph, coordinate)
            ),
        )

    def replace_subtree(
        self,
        root: ItemRef | DurableItemRef,
        containment: QualifiedName | Iterable[QualifiedName],
        new: Subtree,
        policies: ReplacementPolicies | None = None,
    ) -> JournalEditor:
        """Replace descendants atomically and retain abandoned dependencies."""
        outcome = _replace_subtree(
            self._graph, root, containment, new, policies, capture_report=True
        )
        coordinate = self._graph.resolve_item(root)
        self._finish(
            "replace_subtree",
            outcome.graph,
            outcome.displacement,
            provenance_subjects=_present_subject(
                _stable_subject(self._graph, coordinate)
            ),
            detached_dependencies=outcome.detached,
            detached_content=outcome.report,
            correspondence=outcome.correspondence,
        )
        return self

    def swap_subtrees(
        self,
        first: ItemRef | DurableItemRef,
        second: ItemRef | DurableItemRef,
        containment: QualifiedName | Iterable[QualifiedName],
        first_policies: ReplacementPolicies | None = None,
        second_policies: ReplacementPolicies | None = None,
    ) -> JournalEditor:
        """Exchange two non-nested descendant sets as one journal event."""
        outcome = _swap_subtrees(
            self._graph,
            first,
            second,
            containment,
            first_policies,
            second_policies,
            capture_report=True,
        )
        coordinates = (
            self._graph.resolve_item(first),
            self._graph.resolve_item(second),
        )
        self._finish(
            "swap_subtrees",
            outcome.graph,
            outcome.displacement,
            provenance_subjects=tuple(
                stable
                for coordinate in coordinates
                if (stable := _stable_subject(self._graph, coordinate)) is not None
            ),
            detached_dependencies=outcome.detached,
            detached_content=outcome.report,
            correspondence=outcome.correspondence,
        )
        return self

    def move_item(
        self, reference: ItemRef | DurableItemRef, index: int
    ) -> JournalEditor:
        """Move one item and record the reverse move."""
        coordinate = self._graph.resolve_item(reference)
        operations = _operation_pair(
            "move_item",
            (coordinate, index),
            "move_item",
            (ItemRef(coordinate.tier, index), coordinate.index),
        )
        return self._apply(
            "move_item",
            lambda editor: editor.move_item(reference, index),
            operations,
            provenance_subjects=_present_subject(
                _stable_subject(self._graph, coordinate)
            ),
        )

    def move_run(self, run: ItemRun, at: int | BoundaryRef) -> JournalEditor:
        """Move one held run and record one inverse move and hole alignment."""
        member = self._graph._tiers_by_name.get(run.tier)
        size = -1 if member is None else len(member.items)
        destination = at.index if isinstance(at, BoundaryRef) else at
        # The native editor owns the public refusal wording. These guarded
        # values only make a valid operation's inverse and correspondence.
        valid = (
            member is not None
            and run.count > 0
            and run.start >= 0
            and run.stop <= size
            and isinstance(destination, int)
            and not isinstance(destination, bool)
            and 0 <= destination <= size - run.count
        )
        operations = None
        correspondence = None
        subjects: tuple[LayerSubject, ...] = ()
        if valid:
            assert isinstance(destination, int)
            inverse_run = ItemRun(run.tier, destination, run.count)
            operations = _operation_pair(
                "move_run",
                (run, destination),
                "move_run",
                (inverse_run, run.start),
            )
            alignment = {
                ItemRef(run.tier, run.start + offset): (
                    ItemRef(run.tier, destination + offset),
                )
                for offset in range(run.count)
            }
            correspondence = SubtreeCorrespondence(alignment, alignment)
            subjects = tuple(
                stable
                for offset in range(run.count)
                if (
                    stable := _stable_subject(
                        self._graph, ItemRef(run.tier, run.start + offset)
                    )
                )
                is not None
            )
        return self._apply(
            "move_run",
            lambda editor: editor.move_run(run, at),
            operations,
            provenance_subjects=subjects,
            correspondence=correspondence,
        )

    def swap_runs(self, first: ItemRun, second: ItemRun) -> JournalEditor:
        """Swap two runs as one self-inverse journal event."""
        operations = None
        correspondence = None
        subjects: tuple[LayerSubject, ...] = ()
        member = self._graph._tiers_by_name.get(first.tier)
        if member is not None and first.tier == second.tier:
            left, right = (
                (first, second) if first.start <= second.start else (second, first)
            )
            valid = (
                left.stop <= right.start
                and right.stop <= len(member.items)
                and left.start >= 0
            )
            if valid:
                left_after = ItemRun(left.tier, left.start, right.count)
                right_after = ItemRun(
                    left.tier, right.start + right.count - left.count, left.count
                )
                operations = _operation_pair(
                    "swap_runs",
                    (first, second),
                    "swap_runs",
                    (left_after, right_after),
                )
                alignment = {
                    **{
                        ItemRef(left.tier, left.start + offset): (
                            ItemRef(left.tier, right_after.start + offset),
                        )
                        for offset in range(left.count)
                    },
                    **{
                        ItemRef(right.tier, right.start + offset): (
                            ItemRef(right.tier, left_after.start + offset),
                        )
                        for offset in range(right.count)
                    },
                }
                correspondence = SubtreeCorrespondence(alignment, alignment)
                subjects = tuple(
                    stable
                    for reference in alignment
                    if (stable := _stable_subject(self._graph, reference)) is not None
                )
        return self._apply(
            "swap_runs",
            lambda editor: editor.swap_runs(first, second),
            operations,
            provenance_subjects=subjects,
            correspondence=correspondence,
        )

    def swap_items(
        self,
        first: ItemRef | DurableItemRef,
        second: ItemRef | DurableItemRef,
    ) -> JournalEditor:
        """Swap two items and record the same swap as inverse."""
        left = self._graph.resolve_item(first)
        right = self._graph.resolve_item(second)
        operations = _operation_pair(
            "swap_items", (left, right), "swap_items", (left, right)
        )
        return self._apply(
            "swap_items",
            lambda editor: editor.swap_items(first, second),
            operations,
            provenance_subjects=tuple(
                stable
                for coordinate in (left, right)
                if (stable := _stable_subject(self._graph, coordinate)) is not None
            ),
        )

    def shift(
        self,
        container: ItemRef | DurableItemRef,
        k: int,
        direction: ShiftDirection | str,
        containment: QualifiedName,
        policy: str | None = None,
        across_parent: bool = False,
    ) -> JournalEditor:
        """Shift children and record the opposite sister shift as inverse."""
        probe = GraphEditor(self._graph)
        coordinate = self._graph.resolve_item(container)
        try:
            selected = ShiftDirection(direction)
        except ValueError:
            selected = None
        operations = None
        correspondence = None
        subjects: tuple[LayerSubject, ...] = ()
        if selected is not None:
            sister = ItemRef(
                coordinate.tier,
                coordinate.index + (1 if selected is ShiftDirection.RIGHT else -1),
            )
            _, instances = probe._containment_instances(containment)
            source_index = instances.get(coordinate)
            if (
                source_index is not None
                and isinstance(k, int)
                and not isinstance(k, bool)
            ):
                targets = self._graph.polyadic_relations[source_index].targets
                held = targets[-k:] if selected is ShiftDirection.RIGHT else targets[:k]
                moved = (
                    tuple(
                        self._graph.resolve_item(
                            cast(ItemRef | DurableItemRef, endpoint)
                        )
                        for endpoint in held
                    )
                    if k > 0
                    else ()
                )
                alignment = {reference: (reference,) for reference in moved}
                correspondence = SubtreeCorrespondence(alignment, alignment)
                inverse_direction = (
                    ShiftDirection.LEFT
                    if selected is ShiftDirection.RIGHT
                    else ShiftDirection.RIGHT
                )
                operations = _operation_pair(
                    "shift",
                    (
                        coordinate,
                        k,
                        selected.value,
                        containment,
                        policy,
                        across_parent,
                    ),
                    "shift",
                    (
                        sister,
                        k,
                        inverse_direction.value,
                        containment,
                        policy,
                        across_parent,
                    ),
                )
                subjects = tuple(
                    stable
                    for reference in moved
                    if (stable := _stable_subject(self._graph, reference)) is not None
                )
        exact_delta = policy == "drop-to-provisional"
        return self._apply(
            "shift",
            lambda editor: editor.shift(
                container,
                k,
                direction,
                containment,
                policy,
                across_parent,
            ),
            None if exact_delta else operations,
            patch_operations=operations if exact_delta else None,
            provenance_subjects=subjects,
            correspondence=correspondence,
        )

    def add_relation(
        self,
        instance: RelationInstance | PolyadicRelationInstance,
        at: int | None = None,
    ) -> JournalEditor:
        """Add one relation instance and record its removal."""
        count = (
            len(self._graph.polyadic_relations)
            if isinstance(instance, PolyadicRelationInstance)
            else len(self._graph.relations)
        )
        index = count if at is None else at
        coordinate: RelationInstanceRef | PolyadicInstanceRef = (
            PolyadicInstanceRef(index)
            if isinstance(instance, PolyadicRelationInstance)
            else RelationInstanceRef(index)
        )
        operations = _operation_pair(
            "add_relation",
            (instance, index),
            "remove_relation",
            (coordinate,),
        )
        return self._apply(
            "add_relation",
            lambda editor: editor.add_relation(instance, at),
            operations,
            provenance_subjects=(coordinate,),
        )

    def remove_relation(self, target: RelationTarget) -> JournalEditor:
        """Remove one relation instance and retain it for reinsertion."""
        coordinate = _relation_coordinate(self._graph, target)
        instance: RelationInstance | PolyadicRelationInstance = (
            self._graph.polyadic_relations[coordinate.index]
            if isinstance(coordinate, PolyadicInstanceRef)
            else self._graph.relations[coordinate.index]
        )
        operations = _operation_pair(
            "remove_relation",
            (coordinate,),
            "add_relation",
            (instance, coordinate.index),
        )
        return self._apply(
            "remove_relation",
            lambda editor: editor.remove_relation(target),
            operations,
            retire_subjects=_present_subject(_stable_subject(self._graph, coordinate)),
        )

    def set_endpoints(
        self,
        target: RelationTarget,
        sources: RelationEndpointRef | Iterable[RelationEndpointRef],
        targets: RelationEndpointRef | Iterable[RelationEndpointRef],
    ) -> JournalEditor:
        """Replace endpoints and retain their previous ordered values."""
        source_values = (
            cast(RelationEndpointRef, sources)
            if _single_endpoint(sources)
            else tuple(cast(Iterable[RelationEndpointRef], sources))
        )
        target_values = (
            cast(RelationEndpointRef, targets)
            if _single_endpoint(targets)
            else tuple(cast(Iterable[RelationEndpointRef], targets))
        )
        coordinate = _relation_coordinate(self._graph, target)
        relation = (
            self._graph.polyadic_relations[coordinate.index]
            if isinstance(coordinate, PolyadicInstanceRef)
            else self._graph.relations[coordinate.index]
        )
        old_sources: object
        old_targets: object
        if isinstance(relation, PolyadicRelationInstance):
            old_sources = relation.sources
            old_targets = relation.targets
        else:
            old_sources = relation.left
            old_targets = relation.right
        operations = _operation_pair(
            "set_endpoints",
            (coordinate, source_values, target_values),
            "set_endpoints",
            (coordinate, old_sources, old_targets),
        )
        return self._apply(
            "set_endpoints",
            lambda editor: editor.set_endpoints(target, source_values, target_values),
            operations,
            provenance_subjects=_present_subject(
                _stable_subject(self._graph, coordinate)
            ),
        )


def _match_positions(
    old: tuple[object, ...], new: tuple[object, ...]
) -> dict[int, int]:
    """Match durable identities, then equal unmatched values in stable order."""
    result: dict[int, int] = {}
    used: set[int] = set()
    by_id = {
        getattr(value, "durable_id", None): index
        for index, value in enumerate(new)
        if getattr(value, "durable_id", None) is not None
    }
    for index, value in enumerate(old):
        durable_id = getattr(value, "durable_id", None)
        if durable_id is not None and durable_id in by_id:
            target = by_id[durable_id]
            result[index] = target
            used.add(target)
    for index, value in enumerate(old):
        if index in result:
            continue
        unmatched = next(
            (
                candidate
                for candidate, other in enumerate(new)
                if candidate not in used and other == value
            ),
            None,
        )
        if unmatched is not None:
            result[index] = unmatched
            used.add(unmatched)
    return result


def _displacement_between(
    before: Graph,
    after: Graph,
    positional: Displacement | None = None,
) -> Displacement:
    """Build a total best-evidence displacement for a clock-session edit."""
    after_tiers = {tier.declaration.name: tier for tier in after.tiers}
    items: dict[ItemRef, ItemRef] = {}
    boundaries: dict[BoundaryRef, BoundaryRef] = {}
    departed_items: set[ItemRef] = set()
    departed_boundaries: set[BoundaryRef] = set()
    for old_tier in before.tiers:
        name = old_tier.declaration.name
        new_tier = after_tiers.get(name)
        if new_tier is None:
            departed_items.update(
                ItemRef(name, index) for index in range(len(old_tier.items))
            )
            departed_boundaries.update(
                BoundaryRef(name, index) for index in range(len(old_tier.items) + 1)
            )
            continue
        mapping = (
            {
                source.index: target.index
                for source, target in positional.items.items()
                if source.tier == name
            }
            if positional is not None
            else _match_positions(old_tier.items, new_tier.items)
        )
        items.update(
            {
                ItemRef(name, source): ItemRef(name, target)
                for source, target in mapping.items()
            }
        )
        departed_items.update(
            ItemRef(name, index)
            for index in set(range(len(old_tier.items))) - set(mapping)
        )
        for index in range(len(old_tier.items) + 1):
            old_boundary = BoundaryRef(name, index)
            if positional is not None:
                target_boundary = positional.boundaries.get(old_boundary)
                image = None if target_boundary is None else target_boundary.index
            elif index == 0:
                image = 0
            elif index == len(old_tier.items):
                image = len(new_tier.items)
            else:
                left = mapping.get(index - 1)
                right = mapping.get(index)
                image = right if left is not None and right == left + 1 else None
            reference = BoundaryRef(name, index)
            if image is None:
                departed_boundaries.add(reference)
            else:
                boundaries[reference] = BoundaryRef(name, image)
    relations = _match_positions(before.relations, after.relations)
    polyadic = _match_positions(before.polyadic_relations, after.polyadic_relations)
    return Displacement(
        items,
        boundaries,
        relations,
        polyadic,
        frozenset(departed_items),
        frozenset(departed_boundaries),
        frozenset(set(range(len(before.relations))) - set(relations)),
        frozenset(set(range(len(before.polyadic_relations))) - set(polyadic)),
    )


@dataclass(frozen=True, slots=True)
class _LinkSnapshot:
    """Keep one complete link value and the carrier position that owns it."""

    kind: str
    carrier: str
    owner: object
    value: object
    side: str | None = None
    position: int | None = None


@dataclass(frozen=True, slots=True)
class _LinkLedger:
    """Partition every source link and name links introduced by the edit."""

    carried: tuple[_LinkSnapshot, ...]
    repointed: tuple[_LinkSnapshot, ...]
    dropped: tuple[_LinkSnapshot, ...]
    introduced: tuple[_LinkSnapshot, ...]


def _relation_at(
    graph: Graph, carrier: str, index: int
) -> RelationInstance | PolyadicRelationInstance:
    """Return one relation from its arity-specific carrier."""
    values = (
        graph.polyadic_relations if carrier == "polyadic_relations" else graph.relations
    )
    return values[index]


def _graph_links(graph: Graph) -> tuple[_LinkSnapshot, ...]:
    """Flatten every graph link while retaining its complete typed content."""
    links: list[_LinkSnapshot] = []

    def _attributes(carrier: str, owner: object, values: Iterable[Attribute]) -> None:
        kind = "boundary-value" if carrier == "boundary_values" else "attribute"
        links.extend(_LinkSnapshot(kind, carrier, owner, value) for value in values)

    _attributes("document", None, graph.attributes)
    for tier in graph.tiers:
        name = tier.declaration.name
        _attributes("tiers", name, tier.attributes)
        for index, item in enumerate(tier.items):
            _attributes("items", ItemRef(name, index), item.attributes)
    for declaration in graph.relation_declarations:
        _attributes("relation_declarations", declaration.name, declaration.attributes)
    for boundary in graph.boundary_values:
        _attributes("boundary_values", boundary.reference, boundary.attributes)
    for carrier, relations in (
        ("relations", graph.relations),
        ("polyadic_relations", graph.polyadic_relations),
    ):
        for index, raw_relation in enumerate(relations):
            relation = cast(RelationInstance | PolyadicRelationInstance, raw_relation)
            owner = (carrier, index)
            links.append(
                _LinkSnapshot(
                    "relation",
                    carrier,
                    owner,
                    (relation.declaration, relation.durable_id),
                )
            )
            _attributes(carrier, owner, relation.attributes)
            sides = (
                (("left", (relation.left,)), ("right", (relation.right,)))
                if isinstance(relation, RelationInstance)
                else (("sources", relation.sources), ("targets", relation.targets))
            )
            for side, endpoints in sides:
                links.extend(
                    _LinkSnapshot("endpoint", carrier, owner, endpoint, side, position)
                    for position, endpoint in enumerate(endpoints)
                )
    for layer in graph.layers:
        links.extend(
            _LinkSnapshot("fact", "layers", (layer.name, fact.subject), fact.value)
            for fact in layer.facts
        )
    return tuple(links)


def _relation_image(
    before: Graph,
    after: Graph,
    carrier: str,
    index: int,
    displacement: Displacement,
) -> int | None:
    """Locate a surviving relation by identity, then by structural displacement."""
    relation = _relation_at(before, carrier, index)
    values = cast(
        tuple[RelationInstance | PolyadicRelationInstance, ...],
        (
            after.polyadic_relations
            if carrier == "polyadic_relations"
            else after.relations
        ),
    )
    if relation.durable_id is not None:
        found = next(
            (
                position
                for position, candidate in enumerate(values)
                if candidate.durable_id == relation.durable_id
            ),
            None,
        )
        if found is not None:
            return found
    mapping = (
        displacement.polyadic_relations
        if carrier == "polyadic_relations"
        else displacement.relations
    )
    return mapping.get(index)


def _reference_images(
    reference: LayerSubject | RelationEndpointRef,
    displacement: Displacement,
    correspondence: SubtreeCorrespondence | None,
) -> tuple[LayerSubject | RelationEndpointRef, ...]:
    """Return every declared image of one link endpoint or fact subject."""
    if isinstance(reference, ItemRef):
        item_image = displacement.items.get(reference)
        if item_image is not None:
            return (item_image,)
        if correspondence is not None:
            return correspondence.items.get(reference, ())
        return ()
    if isinstance(reference, BoundaryRef):
        boundary_image = displacement.boundaries.get(reference)
        return () if boundary_image is None else (boundary_image,)
    if isinstance(reference, RelationInstanceRef):
        relation_image = displacement.relations.get(reference.index)
        return () if relation_image is None else (RelationInstanceRef(relation_image),)
    if isinstance(reference, PolyadicInstanceRef):
        polyadic_image = displacement.polyadic_relations.get(reference.index)
        return () if polyadic_image is None else (PolyadicInstanceRef(polyadic_image),)
    return (reference,)


def _owner_images(
    link: _LinkSnapshot,
    before: Graph,
    after: Graph,
    displacement: Displacement,
    correspondence: SubtreeCorrespondence | None,
) -> tuple[object, ...]:
    """Return candidate owners for one source link in the result graph."""
    if link.carrier == "items":
        return cast(
            tuple[object, ...],
            _reference_images(cast(ItemRef, link.owner), displacement, correspondence),
        )
    if link.carrier == "boundary_values":
        owner = cast(BoundaryRef | DurableBoundaryRef, link.owner)
        if isinstance(owner, DurableBoundaryRef):
            try:
                after.resolve_boundary(owner)
            except ValueError:
                return ()
            return (owner,)
        return cast(
            tuple[object, ...],
            _reference_images(owner, displacement, correspondence),
        )
    if link.carrier in {"relations", "polyadic_relations"}:
        _, index = cast(tuple[str, int], link.owner)
        image = _relation_image(before, after, link.carrier, index, displacement)
        return () if image is None else ((link.carrier, image),)
    if link.carrier == "layers":
        layer, subject = cast(tuple[LayerName, LayerSubject], link.owner)
        return tuple(
            (layer, image)
            for image in _reference_images(subject, displacement, correspondence)
        )
    return (link.owner,)


def _value_images(
    link: _LinkSnapshot,
    displacement: Displacement,
    correspondence: SubtreeCorrespondence | None,
) -> tuple[object, ...]:
    """Return the possible carried values for a source link."""
    if link.kind != "endpoint":
        return (link.value,)
    return cast(
        tuple[object, ...],
        _reference_images(
            cast(RelationEndpointRef, link.value), displacement, correspondence
        ),
    )


def _reported_detachment(record: object) -> DetachmentReport | None:
    """Return the public withdrawal snapshot carried by a result report."""
    if isinstance(record, DetachmentReport):
        return record
    if (
        isinstance(record, tuple)
        and len(record) == _LEDGER_RECORD_LENGTH
        and isinstance(record[0], DetachmentReport)
    ):
        return record[0]
    return None


def _detachment_has_link(
    link: _LinkSnapshot,
    before: Graph,
    after: Graph,
    report: DetachmentReport,
    displacement: Displacement | None = None,
    correspondence: SubtreeCorrespondence | None = None,
) -> bool:
    """Check that one dropped link has complete content in a result report."""
    if link.carrier in {"relations", "polyadic_relations"}:
        carrier, index = cast(tuple[str, int], link.owner)
        relation = _relation_at(before, carrier, index)
        snapshotted = any(
            reference.index == index and value == relation
            for reference, value in report.relations
            if (carrier == "polyadic_relations")
            == isinstance(reference, PolyadicInstanceRef)
        )
        if not snapshotted:
            return False
        if link.kind != "endpoint":
            return True
        endpoint_reported = any(
            dependency.carrier == "polyadic_endpoints"
            and dependency.index == index
            and dependency.endpoint == link.value
            and dependency.endpoint_side == link.side
            and dependency.endpoint_index == link.position
            for dependency in report.dependencies
        )
        relation_reported = any(
            dependency.carrier == carrier and dependency.index == index
            for dependency in report.dependencies
        )
        if endpoint_reported or relation_reported:
            return True
        if any(
            dependency.carrier == "polyadic_endpoints" and dependency.index == index
            for dependency in report.dependencies
        ):
            return False
        if displacement is None:
            displacement = _displacement_between(before, after)
        mapping = (
            displacement.polyadic_relations
            if carrier == "polyadic_relations"
            else displacement.relations
        )
        if index in mapping:
            return False
        surviving = (
            after.polyadic_relations
            if carrier == "polyadic_relations"
            else after.relations
        )
        if relation.durable_id is not None:
            return all(
                candidate.durable_id != relation.durable_id for candidate in surviving
            )
        if not isinstance(relation, PolyadicRelationInstance):
            return True
        used = set(mapping.values())

        def endpoint_counts(
            endpoints: tuple[RelationEndpointRef, ...],
        ) -> Counter[RelationEndpointRef]:
            """Count every surviving image of an endpoint sequence."""
            return Counter(
                image
                for endpoint in endpoints
                for image in cast(
                    tuple[RelationEndpointRef, ...],
                    _reference_images(endpoint, displacement, correspondence),
                )
            )

        source_images = endpoint_counts(relation.sources)
        target_images = endpoint_counts(relation.targets)
        return not any(
            position not in used
            and isinstance(candidate, PolyadicRelationInstance)
            and candidate.declaration == relation.declaration
            and candidate.durable_id is None
            and candidate.attributes == relation.attributes
            and Counter(candidate.sources) <= source_images
            and Counter(candidate.targets) <= target_images
            for position, candidate in enumerate(surviving)
        )
    if link.kind == "fact":
        layer, subject = cast(tuple[LayerName, LayerSubject], link.owner)
        return (layer, LayerFact(subject, cast(Attribute, link.value))) in report.facts
    if link.carrier == "boundary_values":
        return (link.owner, link.value) in report.boundary_values
    if link.carrier == "items":
        owner = cast(ItemRef, link.owner)
        item = before._tiers_by_name[owner.tier].items[owner.index]
        return (owner, item) in report.items
    return False  # pragma: no cover - _graph_links exhausts link carriers


def _ledger_context(
    before: Graph, after: Graph, record: object
) -> tuple[Displacement, SubtreeCorrespondence | None, DetachmentReport | None]:
    """Resolve one journal or result account into its ledger inputs."""
    if isinstance(record, JournalRecord):
        try:
            restored = record.inverse._delta.reverse(after)
        except GraphValidationError as error:
            raise GraphValidationError(
                "link ledger found a journal inverse that cannot restore the edit"
            ) from error
        if restored != before:
            raise GraphValidationError(
                "link ledger found a journal inverse without the complete source snapshot"
            )
        journal_report = record.report
        return (
            journal_report.displacement,
            journal_report.correspondence,
            journal_report.detached_content,
        )
    report = record if isinstance(record, EditReport) else None
    if (
        isinstance(record, tuple)
        and len(record) == _LEDGER_RECORD_LENGTH
        and isinstance(record[1], Displacement)
        and isinstance(record[2], SubtreeCorrespondence | None)
    ):
        return record[1], record[2], _reported_detachment(record)
    if report is not None:
        return report.displacement, report.correspondence, report.detached_content
    return _displacement_between(before, after), None, _reported_detachment(record)


def _link_positions_match(
    link: _LinkSnapshot,
    candidate: _LinkSnapshot,
    endpoint_positions: Mapping[tuple[object, object, str | None], int],
) -> bool:
    """Preserve polyadic endpoint order while allowing reported trims."""
    if link.kind != "endpoint" or link.carrier != "polyadic_relations":
        return candidate.position == link.position
    key = (link.owner, candidate.owner, link.side)
    previous = endpoint_positions.get(key, -1)
    return candidate.position is not None and candidate.position > previous


def link_ledger(before: Graph, after: Graph, record: object = None) -> _LinkLedger:
    """Balance the complete before/after link diff against one edit account.

    A journal record verifies its exact inverse and accounts withdrawals through
    its detached-content snapshot. A derived result report accounts through the
    same snapshot. An unmatched source link without such an account is refused.
    """
    if not isinstance(before, Graph) or not isinstance(after, Graph):
        raise TypeError("link ledger requires before and after Graph values")
    displacement, correspondence, detachment = _ledger_context(before, after, record)

    source = _graph_links(before)
    target = _graph_links(after)
    used: set[int] = set()
    endpoint_positions: dict[tuple[object, object, str | None], int] = {}
    carried: list[_LinkSnapshot] = []
    repointed: list[_LinkSnapshot] = []
    dropped: list[_LinkSnapshot] = []
    for link in source:
        owners = _owner_images(link, before, after, displacement, correspondence)
        structural_fallback = False
        if (
            not owners
            and link.carrier in {"relations", "polyadic_relations"}
            and (
                detachment is None
                or not _detachment_has_link(
                    link,
                    before,
                    after,
                    detachment,
                    displacement,
                    correspondence,
                )
            )
        ):
            owners = (link.owner,)
            structural_fallback = True
        values = _value_images(link, displacement, correspondence)
        if structural_fallback and link.value not in values:
            values = (*values, link.value)
        reported_drop = detachment is not None and _detachment_has_link(
            link,
            before,
            after,
            detachment,
            displacement,
            correspondence,
        )

        match = (
            None
            if reported_drop
            else next(
                (
                    (index, candidate)
                    for index, candidate in enumerate(target)
                    if index not in used
                    and candidate.kind == link.kind
                    and candidate.carrier == link.carrier
                    and candidate.owner in owners
                    and candidate.value in values
                    and candidate.side == link.side
                    and _link_positions_match(link, candidate, endpoint_positions)
                ),
                None,
            )
        )
        if match is None:
            dropped.append(link)
            if not reported_drop:
                raise GraphValidationError(
                    f"link ledger found an unreported dropped {link.kind} link"
                )
            continue
        index, candidate = match
        used.add(index)
        if link.kind == "endpoint" and link.carrier == "polyadic_relations":
            assert candidate.position is not None
            endpoint_positions[(link.owner, candidate.owner, link.side)] = (
                candidate.position
            )
        if candidate == link:
            carried.append(link)
        else:
            repointed.append(link)

    if len(carried) + len(repointed) + len(dropped) != len(
        source
    ):  # pragma: no cover - loop appends exactly once per source link
        raise GraphValidationError("link ledger did not partition every source link")
    return _LinkLedger(
        tuple(carried),
        tuple(repointed),
        tuple(dropped),
        tuple(link for index, link in enumerate(target) if index not in used),
    )


class ClockJournalEditor(_JournalEditorBase):
    """Record atomic edits that preserve a clock and optional blob-span guard."""

    def __init__(
        self,
        profile: ClockProfile,
        rebinding: ClockRebindingPolicy | str | None,
        journal: Journal,
        *,
        blob: BlobProfile | None = None,
    ) -> None:
        probe = ClockEditor(profile, rebinding, blob=blob)
        super().__init__(profile.graph, journal)
        self._profile_template = profile
        self._blob_profile = blob
        self._profile = profile
        self._policy = probe._policy
        self._profile_active = True

    @property
    def profile(self) -> ClockProfile:
        """Return the clock profile validated for the current graph."""
        if not self._profile_active:
            raise GraphValidationError(
                "clock profile was retired by a declaration cascade; start a new "
                "clock session for the resulting graph"
            )
        return self._profile

    @property
    def reports(self) -> tuple[ClockEditReport, ...]:
        """Return clock-policy reports for currently applied records."""
        return tuple(
            report
            for record in self._journal.records
            for report in record.report.clock_reports
        )

    def freeze(self) -> Graph:
        """Return the graph after checking any opted-in blob span agreement."""
        if self._blob_profile is not None and self._profile_active:
            _check_blob_profile(self._blob_profile, self._profile)
        return super().freeze()

    def displacement(self) -> Displacement:
        """Return where every position of this session's input now stands."""
        return super().displacement()

    def undo(self) -> JournalRecord:
        """Undo the latest operation in this editor's journal."""
        return super().undo()

    def redo(self) -> JournalRecord:
        """Redo the latest operation undone in this editor's journal."""
        return super().redo()

    def dry_run(self, operation: Callable[[Any], object]) -> tuple[EditReport, ...]:
        """Apply, validate, report, and roll back new operations by inverses."""
        return super().dry_run(operation)

    def _apply_clock(
        self,
        operation: str,
        edit: Callable[[ClockEditor], ClockEditor],
        *,
        patch_operations: _OperationPair | None = None,
        provenance_subjects: Iterable[LayerSubject] = (),
        retire_subjects: Iterable[LayerSubject] = (),
        retire_all_provenance: bool = False,
        correspondence: SubtreeCorrespondence | None = None,
    ) -> ClockJournalEditor:
        if not self._profile_active:
            raise GraphValidationError(
                "clock profile was retired by a declaration cascade; start a new "
                "clock session for the resulting graph"
            )
        acted = tuple(provenance_subjects)
        retiring = tuple(retire_subjects)
        operation_before = self._graph
        owned: tuple[LayerFact, ...] = ()
        if self._journal._provenance is not None and (
            retiring or retire_all_provenance
        ):
            if retire_all_provenance:
                owned = _owned_provenance_facts(
                    operation_before,
                    self._journal._provenance,
                    frozenset(self._journal._owned_provenance),
                )
            operation_before = _retire_provenance(
                operation_before,
                self._journal._provenance,
                None if retire_all_provenance else retiring,
                frozenset(self._journal._owned_provenance),
            )
        native = ClockEditor(
            replace(self._profile, graph=operation_before), self._policy
        )
        native._capture_journal_displacement = True
        edit(native)
        candidate = native.freeze()
        if retire_all_provenance and self._journal._provenance is not None and owned:
            candidate = _restore_live_provenance_facts(
                candidate, self._journal._provenance, owned
            )
        step = _displacement_between(
            self._graph,
            candidate,
            getattr(native, "_journal_displacement", None),
        )
        active = native._profile_active
        self._finish(
            operation,
            candidate,
            step,
            patch_operations=patch_operations,
            operation_before=operation_before,
            provenance_subjects=acted,
            clock_reports=native.reports,
            before_clock_active=self._profile_active,
            after_clock_active=active,
            detached_dependencies=native._detached_dependencies,
            detached_content=native._detached_content,
            correspondence=correspondence,
            yield_changes=native._yield_changes,
        )
        self._profile_active = active
        if active:
            self._profile = replace(native.profile, graph=self._graph)
        return self

    def set_attribute(self, target: EditTarget, value: Attribute) -> ClockJournalEditor:
        """Set one attribute within the guarded clock session."""
        subject = _attribute_subject(self._graph, target, value.name)
        prior = _attribute_at(self._graph, target, value.name)
        inverse = (
            _operation("remove_attribute", target, value.name)
            if prior is None
            else _operation("set_attribute", target, prior)
        )
        operations = _OperationPair(_operation("set_attribute", target, value), inverse)
        return self._apply_clock(
            "set_attribute",
            lambda editor: editor.set_attribute(target, value),
            patch_operations=operations,
            provenance_subjects=_present_subject(subject),
        )

    def remove_attribute(
        self, target: EditTarget, name: QualifiedName
    ) -> ClockJournalEditor:
        """Remove one attribute within the guarded clock session."""
        subject = _attribute_subject(self._graph, target, name)
        prior = _attribute_at(self._graph, target, name)
        operations = (
            None
            if prior is None
            else _operation_pair(
                "remove_attribute",
                (target, name),
                "set_attribute",
                (target, prior),
            )
        )
        return self._apply_clock(
            "remove_attribute",
            lambda editor: editor.remove_attribute(target, name),
            patch_operations=operations,
            provenance_subjects=_present_subject(subject),
        )

    def insert_item(
        self, tier: QualifiedName, index: int, item: Item
    ) -> ClockJournalEditor:
        """Insert and bind one item under the session policy."""
        return self._apply_clock(
            "insert_item",
            lambda editor: editor.insert_item(tier, index, item),
            provenance_subjects=(ItemRef(tier, index),),
        )

    def insert_items(
        self, tier: QualifiedName, index: int, items: Iterable[Item]
    ) -> ClockJournalEditor:
        """Insert and bind ordered items under the session policy."""
        if isinstance(items, Set | Mapping):
            return self._apply_clock(
                "insert_items", lambda editor: editor.insert_items(tier, index, items)
            )
        values = tuple(items)
        return self._apply_clock(
            "insert_items",
            lambda editor: editor.insert_items(tier, index, values),
            provenance_subjects=tuple(
                ItemRef(tier, index + offset) for offset in range(len(values))
            ),
        )

    def remove_item(self, reference: ItemRef | DurableItemRef) -> ClockJournalEditor:
        """Remove one item and its binding under the session policy."""
        coordinate = self._graph.resolve_item(reference)
        return self._apply_clock(
            "remove_item",
            lambda editor: editor.remove_item(reference),
            retire_subjects=_present_subject(_stable_subject(self._graph, coordinate)),
        )

    def remove_items(
        self, tier: QualifiedName, index: int, count: int
    ) -> ClockJournalEditor:
        """Remove an item run and reconcile its bindings."""
        member = self._graph._tiers_by_name.get(tier)
        removed = (
            () if member is None or count < 0 else member.items[index : index + count]
        )
        return self._apply_clock(
            "remove_items",
            lambda editor: editor.remove_items(tier, index, count),
            retire_subjects=tuple(
                stable
                for offset in range(len(removed) if index >= 0 else 0)
                if (
                    stable := _stable_subject(
                        self._graph, ItemRef(tier, index + offset)
                    )
                )
                is not None
            ),
        )

    def move_item(
        self, reference: ItemRef | DurableItemRef, index: int
    ) -> ClockJournalEditor:
        """Move one item under the named rebinding policy."""
        coordinate = self._graph.resolve_item(reference)
        return self._apply_clock(
            "move_item",
            lambda editor: editor.move_item(reference, index),
            provenance_subjects=_present_subject(
                _stable_subject(self._graph, coordinate)
            ),
        )

    def move_run(self, run: ItemRun, at: int | BoundaryRef) -> ClockJournalEditor:
        """Move one run under the named clock policy."""
        destination = at.index if isinstance(at, BoundaryRef) else at
        alignment = (
            {
                ItemRef(run.tier, run.start + offset): (
                    ItemRef(run.tier, destination + offset),
                )
                for offset in range(run.count)
            }
            if isinstance(destination, int) and not isinstance(destination, bool)
            else {}
        )
        return self._apply_clock(
            "move_run",
            lambda editor: editor.move_run(run, at),
            provenance_subjects=tuple(
                stable
                for reference in alignment
                if (stable := _stable_subject(self._graph, reference)) is not None
            ),
            correspondence=SubtreeCorrespondence(alignment, alignment),
        )

    def swap_runs(self, first: ItemRun, second: ItemRun) -> ClockJournalEditor:
        """Swap two runs under the named clock policy."""
        references = tuple(
            ItemRef(run.tier, run.start + offset)
            for run in (first, second)
            for offset in range(run.count)
        )
        return self._apply_clock(
            "swap_runs",
            lambda editor: editor.swap_runs(first, second),
            provenance_subjects=tuple(
                stable
                for reference in references
                if (stable := _stable_subject(self._graph, reference)) is not None
            ),
        )

    def swap_items(
        self,
        first: ItemRef | DurableItemRef,
        second: ItemRef | DurableItemRef,
    ) -> ClockJournalEditor:
        """Swap two items under the named rebinding policy."""
        left = self._graph.resolve_item(first)
        right = self._graph.resolve_item(second)
        return self._apply_clock(
            "swap_items",
            lambda editor: editor.swap_items(first, second),
            provenance_subjects=tuple(
                stable
                for coordinate in (left, right)
                if (stable := _stable_subject(self._graph, coordinate)) is not None
            ),
        )

    def shift(
        self,
        container: ItemRef | DurableItemRef,
        k: int,
        direction: ShiftDirection | str,
        containment: QualifiedName,
        policy: ClockRebindingPolicy | str | None = None,
        across_parent: bool = False,
    ) -> ClockJournalEditor:
        """Shift containment and rebind moved yields to existing child times."""
        coordinate = self._graph.resolve_item(container)
        try:
            selected = ShiftDirection(direction)
        except ValueError:
            selected = None
        moved: tuple[ItemRef, ...] = ()
        if selected is not None and isinstance(k, int) and not isinstance(k, bool):
            probe = GraphEditor(self._graph)
            _, instances = probe._containment_instances(containment)
            source_index = instances.get(coordinate)
            if source_index is not None and k > 0:
                relation = self._graph.polyadic_relations[source_index]
                held = (
                    relation.targets[-k:]
                    if selected is ShiftDirection.RIGHT
                    else relation.targets[:k]
                )
                moved = tuple(
                    self._graph.resolve_item(cast(ItemRef | DurableItemRef, endpoint))
                    for endpoint in held
                )
        alignment = {reference: (reference,) for reference in moved}
        return self._apply_clock(
            "shift",
            lambda editor: editor.shift(
                container,
                k,
                direction,
                containment,
                policy,
                across_parent,
            ),
            provenance_subjects=tuple(
                stable
                for reference in moved
                if (stable := _stable_subject(self._graph, reference)) is not None
            ),
            correspondence=SubtreeCorrespondence(alignment, alignment),
        )

    def reparent(
        self,
        target: RelationTarget,
        sources: RelationEndpointRef | Iterable[RelationEndpointRef],
        targets: RelationEndpointRef | Iterable[RelationEndpointRef],
    ) -> ClockJournalEditor:
        """Reparent one relation under the named rebinding policy."""
        source_values = (
            cast(RelationEndpointRef, sources)
            if _single_endpoint(sources)
            else tuple(cast(Iterable[RelationEndpointRef], sources))
        )
        target_values = (
            cast(RelationEndpointRef, targets)
            if _single_endpoint(targets)
            else tuple(cast(Iterable[RelationEndpointRef], targets))
        )
        coordinate = _relation_coordinate(self._graph, target)
        return self._apply_clock(
            "reparent",
            lambda editor: editor.reparent(target, source_values, target_values),
            provenance_subjects=_present_subject(
                _stable_subject(self._graph, coordinate)
            ),
        )

    def replace_subtree(
        self,
        root: ItemRef | DurableItemRef,
        containment: QualifiedName | Iterable[QualifiedName],
        new: Subtree,
        policies: ReplacementPolicies | None = None,
    ) -> ClockJournalEditor:
        """Replace descendants under replacement and clock policies."""
        coordinate = self._graph.resolve_item(root)
        return self._apply_clock(
            "replace_subtree",
            lambda editor: editor.replace_subtree(root, containment, new, policies),
            provenance_subjects=_present_subject(
                _stable_subject(self._graph, coordinate)
            ),
        )

    def undeclare_with_contents(
        self, target: str | QualifiedName | EditDeclaration
    ) -> ClockJournalEditor:
        """Run and record one clock-aware declaration cascade."""
        return self._apply_clock(
            "undeclare_with_contents",
            lambda editor: editor.undeclare_with_contents(target),
            retire_all_provenance=True,
        )

    def _restore(self, record: JournalRecord, *, forward: bool) -> None:
        super()._restore(record, forward=forward)
        self._profile_active = (
            record._after_clock_active if forward else record._before_clock_active
        )
        if self._profile_active:
            self._profile = replace(self._profile_template, graph=self._graph)


__all__ = [
    "ClockJournalEditor",
    "EditAnnotations",
    "EditInverse",
    "EditReport",
    "Journal",
    "JournalEditor",
    "JournalRecord",
    "RelationTouch",
]
