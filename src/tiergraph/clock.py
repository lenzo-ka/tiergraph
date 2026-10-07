"""A clock profile with refined structure and explicitly reconciled time."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Set
from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import StrEnum
from math import gcd
from typing import TYPE_CHECKING, cast, overload

if TYPE_CHECKING:
    from tiergraph.edit import ClockJournalEditor, Journal
    from tiergraph.replacement import DetachedDependency, ReplacementPolicies, Subtree

from tiergraph.core import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    BoundaryRef,
    BoundarySide,
    Displacement,
    DurableBoundaryRef,
    DurableItemRef,
    DurableRelationRef,
    EditDeclaration,
    Graph,
    GraphEditor,
    GraphValidationError,
    Item,
    ItemRef,
    Layer,
    LayerFact,
    LayerName,
    NamespaceDeclaration,
    PolyadicRelationInstance,
    QualifiedName,
    RefusalStage,
    RelationEndpointKind,
    RelationEndpointRef,
    RelationInstance,
    RelationInstanceRef,
    RelationTarget,
    TierRef,
    XsdType,
    _canonical_lexical,
    _scalar_attribute,
    undeclare_with_contents,
)
from tiergraph.machine import _QNameFields


@dataclass(frozen=True, slots=True, order=True)
class ClockCoordinate:
    """Name one integral gap inside an integral coarse tick."""

    tick: int
    gap: int = 0

    def __post_init__(self) -> None:
        """Keep refinement structural and integral rather than fractional."""
        if isinstance(self.tick, bool) or not isinstance(self.tick, int):
            raise ValueError(f"clock tick {self.tick!r} is not integral")
        if isinstance(self.gap, bool) or not isinstance(self.gap, int):
            raise ValueError(f"clock gap {self.gap!r} is not integral")
        if self.tick < 0 or self.gap < 0:
            raise ValueError(f"clock coordinate {(self.tick, self.gap)!r} is negative")

    def to_data(self) -> dict[str, int]:
        """Encode this refined structural clock coordinate."""
        return {"tick": self.tick, "gap": self.gap}


@dataclass(frozen=True, slots=True)
class PhysicalTiming:
    """Carry exact decimal values stamped with the profile's declared unit.

    The unit is carried, not dimensionally enforced: this profile validates its
    declaration and stamps stored values with it, but a stored decimal has no
    independent unit metadata against which the declaration could be checked.
    """

    start: Decimal
    duration: Decimal
    unit: str

    def to_data(self) -> dict[str, str]:
        """Encode this exact physical timing with canonical decimal lexemes."""
        return {
            "start": _canonical_lexical(XsdType.DECIMAL, format(self.start, "f")),
            "duration": _canonical_lexical(XsdType.DECIMAL, format(self.duration, "f")),
            "unit": self.unit,
        }


class ClockRebindingPolicy(StrEnum):
    """Choose how a structural edit reconciles clock-bound boundaries."""

    KEEP_EARLIER = "keep-earlier"
    DROP_TO_PROVISIONAL = "drop-to-provisional"


class ClockEditOperation(StrEnum):
    """Name the structural operation summarized by a clock edit report."""

    ITEM_INSERTION = "item insertion"
    ITEM_REMOVAL = "item removal"
    ITEM_MOVE = "item move"
    ITEM_SWAP = "item swap"
    REPARENT = "reparent"
    DECLARATION_CASCADE = "declaration cascade"
    SUBTREE_REPLACEMENT = "subtree replacement"


@dataclass(frozen=True, slots=True)
class ClockBindingChange:
    """Report one binding that a clock-aware structural edit changed.

    ``previous_boundary`` and ``boundary`` are the old and new logical tier
    boundaries; either is ``None`` when the binding was inserted or withdrawn.
    ``previous_source`` and ``source`` are their durable anchor forms.
    ``previous_clock_index`` and ``clock_index`` are the old and new integral
    clock targets. The final boolean field says that the resulting binding now
    holds a synthesized or collapsed value that needs later realignment; it is
    always false for a withdrawn binding.
    """

    previous_boundary: BoundaryRef | None
    boundary: BoundaryRef | None
    previous_source: RelationEndpointRef | None
    source: RelationEndpointRef | None
    previous_clock_index: int | None
    clock_index: int | None
    provisional: bool


@dataclass(frozen=True, slots=True)
class ClockEditReport:
    """Report one policy outcome on one clock-bound tier.

    ``operation`` identifies the structural operation. ``policy`` is the named
    rebinding policy that governed it, and ``tier`` is the affected timed tier.
    ``changes`` lists every inserted, changed, or withdrawn clock binding.
    ``needs_realignment`` says that the graph carries synthesized or collapsed
    timing which is also durably marked by the tier's ``needs-realignment``
    fact.
    """

    operation: ClockEditOperation
    policy: ClockRebindingPolicy
    tier: QualifiedName
    changes: tuple[ClockBindingChange, ...]
    needs_realignment: bool


@dataclass(frozen=True, slots=True)
class ClockProfile:
    """Interpret ordered tier boundaries against a refined structural clock.

    Every non-clock tier is either completely bound or explicitly untimed.  A
    binding targets an ordinary integral kernel boundary; optional integer
    boundary attributes refine it to ``(coarse tick, ordered gap)``.  Thus two
    repeated point occurrences can occupy distinct structural gaps at the same
    coarse tick without introducing fractional indices.

    Physical time has one named unit.  It may be derived from a uniform rate,
    stored independently on events, or both.  When both sources exist they must
    agree exactly; disagreement is refused with the offending item named.
    Without a rate, independently stored event timings admit non-uniform data
    and different timings for events sharing one structural span.

    The unit is declared, non-empty, string-typed, and carried on returned
    timings.  It is not dimensionally enforced because stored decimal values
    have no independent unit annotation within this single-document profile.

    The profile remains silent on physical time for bound events with neither a
    rate nor stored timing, and on all events of explicitly untimed tiers.  It
    does not infer refinement: without refinement attributes each integral
    clock boundary is the unrefined coordinate ``(index, 0)``.  Partial document
    extents remain valid, and trailing silence still needs an explicit item.
    """

    graph: Graph
    clock_tier: QualifiedName
    binding_relation: QualifiedName | None
    rate_attribute: QualifiedName | None
    unit_attribute: QualifiedName | None
    tick_attribute: QualifiedName | None = None
    gap_attribute: QualifiedName | None = None
    untimed_attribute: QualifiedName | None = None
    start_attribute: QualifiedName | None = None
    duration_attribute: QualifiedName | None = None
    _rate: Decimal | None = field(init=False, repr=False)
    _unit: str = field(init=False, repr=False)
    _bindings: dict[BoundaryRef, int] = field(init=False, repr=False)
    _clock_coordinates: tuple[ClockCoordinate, ...] = field(init=False, repr=False)
    _timings: dict[ItemRef, PhysicalTiming] = field(init=False, repr=False)
    _untimed_tiers: frozenset[QualifiedName] = field(init=False, repr=False)
    _structural: bool = field(init=False, repr=False, default=False)

    @classmethod
    def from_data(cls, graph: Graph, data: object) -> ClockProfile:
        """Decode a strict declarative clock profile for ``graph``.

        Every field is required. Optional qualified-name roles are represented
        by JSON null, while the clock tier, binding relation, and unit attribute
        must be qualified-name objects.
        """
        keys = {
            "clock_tier",
            "binding_relation",
            "rate_attribute",
            "unit_attribute",
            "tick_attribute",
            "gap_attribute",
            "untimed_attribute",
            "start_attribute",
            "duration_attribute",
        }
        fields = _QNameFields(data, "clock profile", keys)

        return cls(
            graph,
            fields.required("clock_tier"),
            fields.required("binding_relation"),
            fields.optional("rate_attribute"),
            fields.required("unit_attribute"),
            fields.optional("tick_attribute"),
            fields.optional("gap_attribute"),
            fields.optional("untimed_attribute"),
            fields.optional("start_attribute"),
            fields.optional("duration_attribute"),
        )

    def __post_init__(self) -> None:  # noqa: PLR0915 -- ordered clock contract
        """Validate declarations, totality, refinement, and timing agreement."""
        tiers = {tier.declaration.name: tier for tier in self.graph.tiers}
        clock = tiers.get(self.clock_tier)
        if clock is None:
            raise ValueError(f"clock tier {str(self.clock_tier)!r} is not declared")

        if self.binding_relation is None:
            raise ValueError("clock binding relation is required")
        if self.unit_attribute is None:
            raise ValueError("clock unit attribute is required")
        unit = self._document_value(self.unit_attribute, XsdType.STRING, "clock unit")
        if not unit.lexical:
            raise ValueError(f"clock unit {str(self.unit_attribute)!r} is empty")
        rate: Decimal | None = None
        if self.rate_attribute is not None:
            value = self._document_value(
                self.rate_attribute, XsdType.DECIMAL, "clock rate"
            )
            rate = Decimal(value.lexical)
            if rate <= 0:
                raise ValueError(f"clock rate {value.lexical!r} must be positive")

        declaration = next(
            (
                candidate
                for candidate in self.graph.relation_declarations
                if candidate.name == self.binding_relation
            ),
            None,
        )
        if declaration is None:
            raise ValueError(
                f"clock binding {str(self.binding_relation)!r} is not declared"
            )
        if not (
            isinstance(declaration, BipartiteRelationDeclaration)
            and declaration.left_endpoint is RelationEndpointKind.BOUNDARY
            and declaration.right_endpoint is RelationEndpointKind.BOUNDARY
        ):
            raise ValueError("clock binding must relate boundary to boundary")

        clock_coordinates = self._read_clock_coordinates(len(clock.items))
        bindings: dict[BoundaryRef, int] = {}
        for relation in self.graph.relations:
            if relation.declaration != self.binding_relation:
                continue
            if not isinstance(relation.left, DurableBoundaryRef):
                raise ValueError("clock binding left endpoint is not a boundary")
            if not isinstance(relation.right, DurableBoundaryRef):
                raise ValueError("clock binding right endpoint is not a boundary")
            source = self.graph.resolve_boundary(relation.left)
            target = self.graph.resolve_boundary(relation.right)
            if target.tier != self.clock_tier:
                raise ValueError("clock binding target is not on the clock tier")
            if source.tier == self.clock_tier:
                raise ValueError("clock tier boundaries do not bind to themselves")
            if source in bindings:
                raise ValueError(f"tier boundary {source.to_data()!r} has two bindings")
            bindings[source] = target.index

        untimed_tiers = self._read_untimed_tiers()
        for tier_name, tier in tiers.items():
            if tier_name == self.clock_tier:
                continue
            coordinates = [
                bindings.get(BoundaryRef(tier_name, index))
                for index in range(len(tier.items) + 1)
            ]
            present = sum(coordinate is not None for coordinate in coordinates)
            if tier_name in untimed_tiers:
                if present:
                    raise ValueError(
                        f"untimed tier {str(tier_name)!r} has {present} clock bindings"
                    )
                continue
            if present != len(coordinates):
                missing = coordinates.index(None)
                boundary = BoundaryRef(tier_name, missing)
                raise ValueError(
                    f"tier boundary {boundary.to_data()!r} has no clock binding"
                )
            integral = [
                coordinate for coordinate in coordinates if coordinate is not None
            ]
            refined = [clock_coordinates[coordinate] for coordinate in integral]
            if refined != sorted(refined):
                raise ValueError(
                    f"clock bindings for tier {str(tier_name)!r} go backward"
                )

        timings = self._read_timings(
            untimed_tiers, bindings, clock_coordinates, rate, unit.lexical
        )
        object.__setattr__(self, "_rate", rate)
        object.__setattr__(self, "_unit", unit.lexical)
        object.__setattr__(self, "_bindings", bindings)
        object.__setattr__(self, "_clock_coordinates", clock_coordinates)
        object.__setattr__(self, "_timings", timings)
        object.__setattr__(self, "_untimed_tiers", frozenset(untimed_tiers))

    @classmethod
    def from_boundary_values(
        cls,
        graph: Graph,
        clock_tier: QualifiedName,
        *,
        tick_attribute: QualifiedName,
        gap_attribute: QualifiedName,
        unit_attribute: QualifiedName | None = None,
        collapse_shared_boundaries: bool = False,
    ) -> ClockProfile:
        """Derive only the clock spine from the clock tier's boundary values.

        This construction path reads the ``(tick, gap)`` boundary attributes on
        the clock tier's own boundaries -- exactly as the full constructor reads
        them -- and yields the same :attr:`coordinates` sequence that the DOT
        renderer draws as the spine. It requires neither a binding relation nor
        a unit attribute, so it accepts a graph whose relations and document
        attributes are empty; a unit is read only when ``unit_attribute`` is
        given.

        The result supports spine rendering alone. It carries no tier-to-clock
        bindings, so every non-spine timing query -- :meth:`is_timed`,
        :meth:`clock_index`, :meth:`refined_coordinate`, :meth:`extent`,
        :meth:`structural_span`, :meth:`timing`, and :meth:`duration` -- raises
        rather than returning an answer it cannot justify. Binding other tiers
        to the clock genuinely needs ``graph.relations`` and remains the full
        constructor's responsibility; this path never weakens that validation.

        With ``collapse_shared_boundaries``, each coarse tick's trailing gap --
        its closing boundary, coincident with the next tick's opening boundary
        -- is folded away so the spine shows one node per occupied coordinate.
        The default is off, leaving the raw boundaries and keeping every other
        caller's spine byte-identical.
        """
        if not isinstance(graph, Graph):
            raise TypeError(
                f"graph must be a tiergraph.Graph, got {type(graph).__name__}"
            )
        clock = next(
            (tier for tier in graph.tiers if tier.declaration.name == clock_tier),
            None,
        )
        if clock is None:
            raise ValueError(f"clock tier {str(clock_tier)!r} is not declared")
        profile = object.__new__(cls)
        object.__setattr__(profile, "graph", graph)
        object.__setattr__(profile, "clock_tier", clock_tier)
        object.__setattr__(profile, "binding_relation", None)
        object.__setattr__(profile, "rate_attribute", None)
        object.__setattr__(profile, "unit_attribute", unit_attribute)
        object.__setattr__(profile, "tick_attribute", tick_attribute)
        object.__setattr__(profile, "gap_attribute", gap_attribute)
        object.__setattr__(profile, "untimed_attribute", None)
        object.__setattr__(profile, "start_attribute", None)
        object.__setattr__(profile, "duration_attribute", None)
        coordinates = profile._read_clock_coordinates(len(clock.items))
        if collapse_shared_boundaries:
            # Collapse raw boundaries to occupied coordinates by folding away
            # each coarse tick's trailing gap.
            coordinates = _collapse_shared_boundaries(coordinates)
        unit = ""
        if unit_attribute is not None:
            unit = profile._document_value(
                unit_attribute, XsdType.STRING, "clock unit"
            ).lexical
        object.__setattr__(profile, "_rate", None)
        object.__setattr__(profile, "_unit", unit)
        object.__setattr__(profile, "_bindings", {})
        object.__setattr__(profile, "_clock_coordinates", coordinates)
        object.__setattr__(profile, "_timings", {})
        object.__setattr__(profile, "_untimed_tiers", frozenset())
        object.__setattr__(profile, "_structural", True)
        return profile

    def _refuse_structural_timing(self, operation: str) -> None:
        """Refuse a timing query a spine-only structural profile cannot answer."""
        if self._structural:
            raise ValueError(
                f"{operation} is unsupported on a ClockProfile built by "
                "from_boundary_values: it derives only the clock spine from "
                "boundary values and carries no tier-to-clock bindings"
            )

    def _declaration(
        self,
        name: QualifiedName,
        domain: AttributeDomain,
        value_type: XsdType,
        role: str,
    ) -> None:
        declaration = next(
            (item for item in self.graph.attribute_declarations if item.name == name),
            None,
        )
        if declaration is None:
            raise ValueError(f"{role} {str(name)!r} is not declared")
        if declaration.domain is not domain or declaration.value_type is not value_type:
            raise ValueError(
                f"{role} must be a {domain.value} {value_type.value} attribute"
            )

    def _document_value(
        self, name: QualifiedName, value_type: XsdType, role: str
    ) -> AttributeValue:
        self._declaration(name, AttributeDomain.DOCUMENT, value_type, role)
        value = next(
            (item for item in self.graph.attributes if item.name == name), None
        )
        if value is None:
            raise ValueError(f"{role} {str(name)!r} has no value")
        return _scalar_attribute(value)

    def _read_clock_coordinates(self, item_count: int) -> tuple[ClockCoordinate, ...]:
        if (self.tick_attribute is None) != (self.gap_attribute is None):
            raise ValueError("clock refinement requires both tick and gap attributes")
        if self.tick_attribute is None or self.gap_attribute is None:
            return tuple(ClockCoordinate(index) for index in range(item_count + 1))
        self._declaration(
            self.tick_attribute, AttributeDomain.BOUNDARY, XsdType.INTEGER, "clock tick"
        )
        self._declaration(
            self.gap_attribute, AttributeDomain.BOUNDARY, XsdType.INTEGER, "clock gap"
        )
        coordinates = []
        for index in range(item_count + 1):
            reference = BoundaryRef(self.clock_tier, index)
            values = {
                value.name: value
                for value in self.graph.boundaries(self.clock_tier)[index].attributes
            }
            try:
                tick = int(_scalar_attribute(values[self.tick_attribute]).lexical)
                gap = int(_scalar_attribute(values[self.gap_attribute]).lexical)
            except KeyError as error:
                raise ValueError(
                    f"clock boundary {reference.to_data()!r} lacks refinement"
                ) from error
            coordinates.append(ClockCoordinate(tick, gap))
        if coordinates != sorted(coordinates) or len(set(coordinates)) != len(
            coordinates
        ):
            raise ValueError("clock refinement coordinates are not strictly ordered")
        boundary_counts = Counter(coordinate.tick for coordinate in coordinates)
        # A tick's first boundary is gap zero.
        # Every further boundary is one refinement.
        first_boundary_count = 1
        for coordinate in coordinates:
            refinement_count = boundary_counts[coordinate.tick] - first_boundary_count
            if coordinate.gap > refinement_count:
                raise ValueError(
                    f"clock gap {coordinate.gap} for tick {coordinate.tick} exceeds "
                    f"refinement count {refinement_count}"
                )
        return tuple(coordinates)

    def _read_untimed_tiers(self) -> set[QualifiedName]:
        if self.untimed_attribute is None:
            return set()
        self._declaration(
            self.untimed_attribute,
            AttributeDomain.TIER,
            XsdType.BOOLEAN,
            "untimed marker",
        )
        return {
            tier.declaration.name
            for tier in self.graph.tiers
            if any(
                value.name == self.untimed_attribute
                and _scalar_attribute(value).lexical == "true"
                for value in tier.attributes
            )
        }

    def _read_timings(
        self,
        untimed_tiers: set[QualifiedName],
        bindings: dict[BoundaryRef, int],
        clock_coordinates: tuple[ClockCoordinate, ...],
        rate: Decimal | None,
        unit: str,
    ) -> dict[ItemRef, PhysicalTiming]:
        if (self.start_attribute is None) != (self.duration_attribute is None):
            raise ValueError(
                "stored timing requires both start and duration attributes"
            )
        if self.start_attribute is None or self.duration_attribute is None:
            return {}
        self._declaration(
            self.start_attribute, AttributeDomain.ITEM, XsdType.DECIMAL, "timing start"
        )
        self._declaration(
            self.duration_attribute,
            AttributeDomain.ITEM,
            XsdType.DECIMAL,
            "timing duration",
        )
        timings: dict[ItemRef, PhysicalTiming] = {}
        for tier in self.graph.tiers:
            if tier.declaration.name == self.clock_tier:
                continue
            for index, item in enumerate(tier.items):
                values = {value.name: value for value in item.attributes}
                has_start = self.start_attribute in values
                has_duration = self.duration_attribute in values
                reference = ItemRef(tier.declaration.name, index)
                if has_start != has_duration:
                    raise ValueError(
                        f"item {reference.to_data()!r} has partial stored timing"
                    )
                if not has_start:
                    continue
                if tier.declaration.name in untimed_tiers:
                    raise ValueError(
                        f"untimed tier item {reference.to_data()!r} has stored timing"
                    )
                start = Decimal(_scalar_attribute(values[self.start_attribute]).lexical)
                duration = Decimal(
                    _scalar_attribute(values[self.duration_attribute]).lexical
                )
                if duration < 0:
                    raise ValueError(
                        f"item {reference.to_data()!r} has negative duration"
                    )
                timing = PhysicalTiming(start, duration, unit)
                if rate is not None:
                    left = bindings[BoundaryRef(reference.tier, index)]
                    right = bindings[BoundaryRef(reference.tier, index + 1)]
                    start_tick = clock_coordinates[left].tick
                    tick_span = (
                        clock_coordinates[right].tick - clock_coordinates[left].tick
                    )
                    if not (
                        _decimal_times_rate_equals(start, rate, start_tick)
                        and _decimal_times_rate_equals(duration, rate, tick_span)
                    ):
                        raise ValueError(
                            f"item {reference.to_data()!r} stored timing contradicts clock"
                        )
                timings[reference] = timing
        return timings

    @overload
    def edit(self, rebinding: ClockRebindingPolicy | str | None = None) -> ClockEditor:
        """Return a plain clock editor when no journal is attached."""
        ...

    @overload
    def edit(
        self,
        rebinding: ClockRebindingPolicy | str | None = None,
        *,
        journal: Journal,
    ) -> ClockJournalEditor:
        """Return an opt-in journaled clock editor."""
        ...

    def edit(
        self,
        rebinding: ClockRebindingPolicy | str | None = None,
        *,
        journal: Journal | None = None,
    ) -> ClockEditor | ClockJournalEditor:
        """Return an editor that keeps this clock profile valid after every edit.

        Structural edits to a timed tier refuse unless ``rebinding`` names a
        policy. ``keep-earlier`` keeps the tier's ordered boundary times while
        items move through them, resolving an anchor collision in favor of the
        earlier binding. The named collapsing policy collapses the affected
        span onto its earlier clock boundary and records a tier fact saying
        that the result needs realignment. Untimed tiers need no rebinding
        policy. The clock tier itself cannot be structurally edited in a bound
        session. A named declaration cascade may explicitly remove this clock
        definition and retire the session; its graph and withdrawal reports
        remain available, but later profile-aware edits refuse.
        """
        if journal is None:
            return ClockEditor(self, rebinding)
        return journal._attach_clock(self, rebinding)

    @property
    def is_structural(self) -> bool:
        """Report whether this profile derives only a renderable clock spine."""
        return self._structural

    @property
    def rate(self) -> Decimal | None:
        """Return ticks per declared unit, or ``None`` for an uncalibrated clock."""
        return self._rate

    @property
    def unit(self) -> str:
        """Return the declared physical timing unit."""
        return self._unit

    @property
    def coordinates(self) -> tuple[ClockCoordinate, ...]:
        """Return the profile's validated refined clock coordinates in order."""
        return self._clock_coordinates

    def is_timed(self, tier: QualifiedName) -> bool:
        """Report whether a tier chose complete clock binding."""
        self._refuse_structural_timing("is_timed")
        return tier not in self._untimed_tiers

    def clock_index(self, boundary: BoundaryRef) -> int:
        """Return the integral clock-tier boundary bound to one tier boundary."""
        self._refuse_structural_timing("clock_index")
        try:
            return self._bindings[boundary]
        except KeyError as error:
            raise ValueError(
                f"tier boundary {boundary.to_data()!r} has no clock binding"
            ) from error

    def refined_coordinate(self, boundary: BoundaryRef) -> ClockCoordinate:
        """Return the coarse tick and ordered gap bound to one tier boundary."""
        if boundary.tier in self._untimed_tiers:
            raise ValueError(f"tier {str(boundary.tier)!r} is untimed")
        return self._clock_coordinates[self.clock_index(boundary)]

    def extent(self, tier: QualifiedName) -> tuple[ClockCoordinate, ClockCoordinate]:
        """Return a timed tier's possibly partial refined clock extent."""
        self._refuse_structural_timing("extent")
        member = next(
            (
                candidate
                for candidate in self.graph.tiers
                if candidate.declaration.name == tier
            ),
            None,
        )
        if member is None:
            raise ValueError(f"tier {str(tier)!r} is not declared")
        if tier == self.clock_tier:
            raise ValueError(f"tier {str(tier)!r} is the clock tier")
        if tier in self._untimed_tiers:
            raise ValueError(f"tier {str(tier)!r} is untimed")
        return self.refined_coordinate(BoundaryRef(tier, 0)), self.refined_coordinate(
            BoundaryRef(tier, len(member.items))
        )

    def structural_span(
        self, tier: QualifiedName, index: int
    ) -> tuple[ClockCoordinate, ClockCoordinate]:
        """Return an event span between refined integral coordinates."""
        return self.refined_coordinate(
            BoundaryRef(tier, index)
        ), self.refined_coordinate(BoundaryRef(tier, index + 1))

    def timing(self, tier: QualifiedName, index: int) -> PhysicalTiming | None:
        """Return stored timing or exactly representable coarse-tick timing.

        Rate-derived physical timing uses only coarse ticks.  Ordered gaps are
        structural, so a real gap-only span derives zero physical duration.
        When a tick/rate ratio has no finite Decimal representation, this method
        refuses it; :meth:`duration` retains the exact ratio in all cases.
        Explicitly untimed tiers consistently return ``None`` with or without a
        document rate.
        """
        self._refuse_structural_timing("timing")
        if tier in self._untimed_tiers:
            return None
        reference = ItemRef(tier, index)
        stored = self._timings.get(reference)
        if stored is not None:
            return stored
        if self._rate is None:
            return None
        start, end = self.structural_span(tier, index)
        return PhysicalTiming(
            _exact_decimal_ratio(start.tick, self._rate),
            _exact_decimal_ratio(end.tick - start.tick, self._rate),
            self._unit,
        )

    @property
    def has_uniform_rate(self) -> bool:
        """Report whether legacy exact coarse-tick durations are available."""
        return self._rate is not None

    def duration(self, tier: QualifiedName, index: int) -> tuple[int, Decimal]:
        """Return the legacy coarse-tick span and rate when a rate exists."""
        self._refuse_structural_timing("duration")
        if self._rate is None:
            raise ValueError("clock has no uniform rate")
        start, end = self.structural_span(tier, index)
        return end.tick - start.tick, self._rate


@dataclass(frozen=True, slots=True)
class _BindingRecord:
    position: int
    boundary: BoundaryRef
    relation: RelationInstance


@dataclass(frozen=True, slots=True)
class _DetachedRelationFact:
    """Carry one fact across the temporary removal of its relation."""

    layer: LayerName
    fact: LayerFact
    position: int
    fact_index: int


def _unbind_relations(
    graph: Graph, records: tuple[_BindingRecord, ...]
) -> tuple[Graph, tuple[_DetachedRelationFact, ...]]:
    """Remove selected relations while remapping or temporarily holding facts."""
    positions = {record.position for record in records}
    mapping: dict[int, int] = {}
    removed = 0
    for old in range(len(graph.relations)):
        if old in positions:
            removed += 1
        else:
            mapping[old] = old - removed
    durable_positions = {
        record.relation.durable_id: record.position
        for record in records
        if record.relation.durable_id is not None
    }
    detached: list[_DetachedRelationFact] = []
    layers: list[Layer] = []
    for layer in graph.layers:
        facts: list[LayerFact] = []
        for fact_index, fact in enumerate(layer.facts):
            subject = fact.subject
            position: int | None = None
            if isinstance(subject, RelationInstanceRef):
                position = subject.index if subject.index in positions else None
                if position is None:
                    subject = RelationInstanceRef(mapping[subject.index])
            elif isinstance(subject, DurableRelationRef):
                position = durable_positions.get(subject.durable_id)
            if position is None:
                facts.append(LayerFact(subject, fact.value))
            else:
                detached.append(
                    _DetachedRelationFact(layer.name, fact, position, fact_index)
                )
        layers.append(Layer(layer.name, tuple(facts)))
    return (
        replace(
            graph,
            relations=tuple(
                relation
                for index, relation in enumerate(graph.relations)
                if index not in positions
            ),
            layers=tuple(layers),
        ),
        tuple(detached),
    )


def _rebuilt_layers(
    layers: tuple[Layer, ...],
    unbound_to_final: Mapping[int, int],
    detached: tuple[_DetachedRelationFact, ...],
    original_to_final: Mapping[int, int],
) -> tuple[Layer, ...]:
    """Remap surviving facts and restore facts whose relations survived rebuild."""
    by_name: dict[LayerName, list[LayerFact]] = {}
    for layer in layers:
        by_name[layer.name] = [
            LayerFact(
                RelationInstanceRef(unbound_to_final[fact.subject.index])
                if isinstance(fact.subject, RelationInstanceRef)
                else fact.subject,
                fact.value,
            )
            for fact in layer.facts
        ]
    for held in detached:
        position = original_to_final.get(held.position)
        if position is None:
            raise GraphValidationError(
                "relation removal would invalidate a live fact in layer "
                f"{held.layer.vocabulary!r}/{held.layer.source!r}"
            )
        subject = held.fact.subject
        if isinstance(subject, RelationInstanceRef):
            subject = RelationInstanceRef(position)
        by_name[held.layer].append(LayerFact(subject, held.fact.value))
    return tuple(Layer(layer.name, tuple(by_name[layer.name])) for layer in layers)


_CLOCK_EDIT_NAMESPACE = "urn:tiergraph:clock-edit"
_NEEDS_REALIGNMENT = QualifiedName(_CLOCK_EDIT_NAMESPACE, "needs-realignment")
_REALIGNMENT_LAYER = LayerName(_CLOCK_EDIT_NAMESPACE, "rebinding")


def _descendant_indexes(
    descendants: frozenset[ItemRef],
) -> dict[QualifiedName, tuple[int, ...]]:
    """Group subtree coordinates into their ordered tier-local runs."""
    return {
        tier: tuple(sorted(item.index for item in descendants if item.tier == tier))
        for tier in {item.tier for item in descendants}
    }


def _corresponding_boundary_origins(
    correspondence: Mapping[ItemRef, tuple[ItemRef, ...]],
) -> dict[BoundaryRef, int]:
    """Find old boundaries preserved by an item correspondence.

    A split preserves only its outside boundaries. A merge preserves the first
    outside boundary and the last outside boundary. Adjacent one-to-one matches
    agree on their shared boundary; an ambiguous interior boundary is left for
    the named rebinding policy to synthesize.
    """
    before: dict[BoundaryRef, set[int]] = {}
    after: dict[BoundaryRef, set[int]] = {}
    for source, raw_targets in correspondence.items():
        targets = tuple(
            sorted(
                (target for target in raw_targets if target.tier == source.tier),
                key=lambda target: target.index,
            )
        )
        if not targets:
            continue
        before.setdefault(BoundaryRef(source.tier, targets[0].index), set()).add(
            source.index
        )
        after.setdefault(BoundaryRef(source.tier, targets[-1].index + 1), set()).add(
            source.index + 1
        )
    result: dict[BoundaryRef, int] = {}
    for boundary in set(before) | set(after):
        left = before.get(boundary)
        right = after.get(boundary)
        if left is not None and right is not None:
            shared = left & right
            if len(shared) == 1:  # pragma: no branch - ordered runs share at most one
                result[boundary] = next(iter(shared))
        elif left is not None:
            result[boundary] = min(left)
        elif right is not None:  # pragma: no branch - boundary came from this map
            result[boundary] = max(right)
    return result


class ClockEditor:
    """Edit one graph while preserving a declared clock profile.

    The editor validates both the graph and the clock profile after every
    operation. Structural edits on timed tiers are atomic: a refusal leaves the
    editor's graph, reports, and profile unchanged. Successful timed-tier edits
    append a :class:`ClockEditReport`; untimed edits need no clock report. A
    named declaration cascade may explicitly remove the clock definition and
    end the profile-aware session; its graph and reports remain readable, while
    later profile-aware operations refuse.
    """

    _capture_journal_displacement: bool
    _journal_displacement: Displacement

    def __init__(
        self,
        profile: ClockProfile,
        rebinding: ClockRebindingPolicy | str | None = None,
    ) -> None:
        """Start a profile-aware session with an optional named policy."""
        if profile.is_structural:
            raise ValueError(
                "a structural clock-spine profile has no tier bindings to edit"
            )
        if rebinding is None:
            policy = None
        else:
            try:
                policy = ClockRebindingPolicy(rebinding)
            except ValueError as error:
                names = ", ".join(policy.value for policy in ClockRebindingPolicy)
                raise ValueError(
                    f"unknown clock rebinding policy {rebinding!r}; choose {names}"
                ) from error
        self._profile = profile
        self._profile_active = True
        self._graph = profile.graph
        self._policy = policy
        self._reports: list[ClockEditReport] = []
        self._detached_dependencies: tuple[DetachedDependency, ...] = ()

    @property
    def profile(self) -> ClockProfile:
        """Return the clock profile validated for the current graph."""
        self._require_active_profile()
        return self._profile

    @property
    def reports(self) -> tuple[ClockEditReport, ...]:
        """Return every successful timed-tier policy outcome in order."""
        return tuple(self._reports)

    def freeze(self) -> Graph:
        """Return the current fully validated graph without consuming the editor."""
        return self._graph

    def insert_item(self, tier: QualifiedName, index: int, item: Item) -> ClockEditor:
        """Insert one item and atomically bind every resulting timed boundary."""
        return self.insert_items(tier, index, (item,))

    def insert_items(
        self, tier: QualifiedName, index: int, items: Iterable[Item]
    ) -> ClockEditor:
        """Insert ordered items and atomically bind resulting timed boundaries."""
        self._require_active_profile()
        if isinstance(items, Set | Mapping):
            raise GraphValidationError(
                "item insertion items must be an ordered iterable"
            )
        new_items = tuple(items)
        operation = ClockEditOperation.ITEM_INSERTION
        old_count = self._item_count(tier, operation)
        if index < 0 or index > old_count:
            raise GraphValidationError(
                f"item insertion index {index} is outside tier {str(tier)!r}"
            )
        if not new_items:
            return self._no_op(operation, tier)

        def _edit(editor: GraphEditor) -> GraphEditor:
            return editor.insert_items(tier, index, new_items)

        if self._route_plain_tier_edit(operation, tier, _edit):
            return self
        amount = len(new_items)
        targets = tuple(
            boundary
            if boundary <= index
            else index
            if boundary <= index + amount
            else boundary - amount
            for boundary in range(old_count + amount + 1)
        )
        templates: list[int | None] = [None] * (old_count + amount + 1)
        for boundary in range(old_count + 1):
            if boundary == 0:
                image = 0
            elif boundary == old_count:
                image = old_count + amount
            else:
                image = boundary + amount if boundary >= index else boundary
            templates[image] = boundary
        return self._tier_edit(
            operation,
            tier,
            _edit,
            tuple(templates),
            targets,
            targets,
        )

    def remove_item(self, reference: ItemRef | DurableItemRef) -> ClockEditor:
        """Remove one item together with its departing clock anchor."""
        self._require_active_profile()
        coordinate = self._graph.resolve_item(reference)
        return self.remove_items(coordinate.tier, coordinate.index, 1)

    def remove_items(self, tier: QualifiedName, index: int, count: int) -> ClockEditor:
        """Remove a run and keep one reported binding on its merged boundary."""
        self._require_active_profile()
        operation = ClockEditOperation.ITEM_REMOVAL
        old_count = self._item_count(tier, operation)
        if count < 0:
            raise GraphValidationError("item removal count must not be negative")
        if index < 0 or index > old_count or index + count > old_count:
            raise GraphValidationError(
                f"item removal range {index}:{index + count} is outside tier "
                f"{str(tier)!r} with {old_count} items"
            )
        if count == 0:
            return self._no_op(operation, tier)

        def _edit(editor: GraphEditor) -> GraphEditor:
            return editor.remove_items(tier, index, count)

        if self._route_plain_tier_edit(operation, tier, _edit):
            return self
        records = self._binding_records(tier)
        candidates: dict[int, _BindingRecord] = {}
        for record in records:
            boundary = record.boundary.index
            if boundary < index:
                image = boundary
            elif boundary <= index + count:
                image = index
            else:
                image = boundary - count
            previous = candidates.get(image)
            if previous is None or record.position < previous.position:
                candidates[image] = record
        templates = tuple(
            candidates[boundary].boundary.index
            for boundary in range(old_count - count + 1)
        )
        keep_targets = templates
        drop_targets = list(keep_targets)
        drop_targets[index] = index
        return self._tier_edit(
            operation,
            tier,
            _edit,
            templates,
            keep_targets,
            tuple(drop_targets),
        )

    def move_item(self, reference: ItemRef | DurableItemRef, index: int) -> ClockEditor:
        """Move an item through fixed boundary times under the named policy."""
        self._require_active_profile()
        operation = ClockEditOperation.ITEM_MOVE
        coordinate = self._graph.resolve_item(reference)
        count = self._item_count(coordinate.tier, operation)
        if index < 0 or index >= count:
            raise GraphValidationError(
                f"item move index {index} is outside tier {str(coordinate.tier)!r}"
            )
        if coordinate.index == index:
            return self._no_op(operation, coordinate.tier)

        def _edit(editor: GraphEditor) -> GraphEditor:
            return editor.move_item(reference, index)

        if self._route_plain_tier_edit(operation, coordinate.tier, _edit):
            return self
        lower = min(coordinate.index, index)
        upper = max(coordinate.index, index) + 1
        templates = tuple(range(count + 1))
        drop_targets = list(templates)
        drop_targets[lower : upper + 1] = [lower] * (upper - lower + 1)
        return self._tier_edit(
            operation,
            coordinate.tier,
            _edit,
            templates,
            templates,
            tuple(drop_targets),
        )

    def swap_items(
        self,
        first: ItemRef | DurableItemRef,
        second: ItemRef | DurableItemRef,
    ) -> ClockEditor:
        """Exchange two items through fixed boundary times under the policy."""
        self._require_active_profile()
        left = self._graph.resolve_item(first)
        right = self._graph.resolve_item(second)
        if left.tier != right.tier:
            raise GraphValidationError(
                f"item swap names {str(left)!r} and {str(right)!r} in different "
                "tiers; an item's tier decides its type"
            )
        if left.index == right.index:
            return self._no_op(ClockEditOperation.ITEM_SWAP, left.tier)
        operation = ClockEditOperation.ITEM_SWAP

        def _edit(editor: GraphEditor) -> GraphEditor:
            return editor.swap_items(first, second)

        if self._route_plain_tier_edit(operation, left.tier, _edit):
            return self
        count = self._item_count(left.tier, operation)
        lower = min(left.index, right.index)
        upper = max(left.index, right.index) + 1
        templates = tuple(range(count + 1))
        drop_targets = list(templates)
        drop_targets[lower : upper + 1] = [lower] * (upper - lower + 1)
        return self._tier_edit(
            operation,
            left.tier,
            _edit,
            templates,
            templates,
            tuple(drop_targets),
        )

    def reparent(
        self,
        target: RelationTarget,
        sources: RelationEndpointRef | Iterable[RelationEndpointRef],
        targets: RelationEndpointRef | Iterable[RelationEndpointRef],
    ) -> ClockEditor:
        """Replace relation endpoints under the named policy for touched tiers.

        This operation is for structural parent relations, not the clock binding
        relation itself. ``keep-earlier`` leaves timing unchanged. The named
        collapsing policy conservatively collapses each touched timed tier onto
        its earlier extent and records that realignment is needed.
        """
        self._require_active_profile()
        source_values = _endpoint_tuple(sources)
        target_values = _endpoint_tuple(targets)
        current = _target_relation(self._graph, target)
        if current.declaration == self._profile.binding_relation:
            raise GraphValidationError(
                "reparent cannot edit the clock binding relation; structural "
                "edits reconcile that relation through a named policy"
            )
        endpoints = (
            *(
                (current.left, current.right)
                if isinstance(current, RelationInstance)
                else (*current.sources, *current.targets)
            ),
            *source_values,
            *target_values,
        )
        timed_tiers = tuple(
            sorted(
                {
                    tier
                    for endpoint in endpoints
                    if (tier := _endpoint_tier(self._graph, endpoint))
                    != self._profile.clock_tier
                    and self._profile.is_timed(tier)
                }
            )
        )
        if timed_tiers and self._policy is None:
            self._missing_policy(ClockEditOperation.REPARENT, timed_tiers[0])
        editor = self._graph.edit()
        if isinstance(current, RelationInstance):
            if len(source_values) != 1 or len(target_values) != 1:
                raise GraphValidationError(
                    "bipartite endpoints must each be one endpoint reference"
                )
            editor.set_endpoints(target, source_values[0], target_values[0])
        else:
            editor.set_endpoints(target, source_values, target_values)
        candidate = editor.freeze()
        next_profile = self._profile_for(candidate)
        reports: list[ClockEditReport] = []
        if self._policy is ClockRebindingPolicy.DROP_TO_PROVISIONAL:
            for tier in timed_tiers:
                count = self._item_count(tier, ClockEditOperation.REPARENT)
                templates = tuple(range(count + 1))
                records = self._binding_records(tier)
                candidate, detached = _unbind_relations(candidate, records)
                candidate, changes = self._rebuild_bindings(
                    candidate,
                    tier,
                    templates,
                    tuple(0 for _ in templates),
                    records=records,
                    detached=detached,
                )
                candidate = _record_needs_realignment(candidate, tier)
                next_profile = self._profile_for(candidate)
                reports.append(
                    ClockEditReport(
                        ClockEditOperation.REPARENT,
                        self._policy,
                        tier,
                        changes,
                        True,
                    )
                )
        else:
            reports.extend(
                ClockEditReport(
                    ClockEditOperation.REPARENT,
                    ClockRebindingPolicy.KEEP_EARLIER,
                    tier,
                    (),
                    False,
                )
                for tier in timed_tiers
            )
        self._graph = candidate
        self._profile = next_profile
        self._reports.extend(reports)
        return self

    def replace_subtree(
        self,
        root: ItemRef | DurableItemRef,
        containment: QualifiedName | Iterable[QualifiedName],
        new: Subtree,
        policies: ReplacementPolicies | None = None,
    ) -> ClockEditor:
        """Replace descendants while explicitly reconciling every timed tier."""
        from tiergraph.replacement import (  # noqa: PLC0415
            _containment_names,
            _replace_subtree,
            _shape,
        )

        self._require_active_profile()
        names = _containment_names(containment)
        old_shape = _shape(self._graph, root, set(names))
        new_shape = _shape(new.graph, new.root, set(names))
        old_by_tier = _descendant_indexes(old_shape.descendants)
        source_new_by_tier = _descendant_indexes(new_shape.descendants)
        affected = tuple(sorted(set(old_by_tier) | set(source_new_by_tier)))
        if self._profile.clock_tier in affected:
            raise GraphValidationError(
                "subtree replacement cannot restructure the clock tier in a bound session"
            )
        timed = tuple(tier for tier in affected if self._profile.is_timed(tier))
        if timed and self._policy is None:
            self._missing_policy(ClockEditOperation.SUBTREE_REPLACEMENT, timed[0])

        records = tuple(
            sorted(
                (record for tier in timed for record in self._binding_records(tier)),
                key=lambda record: record.position,
            )
        )
        unbound, detached = _unbind_relations(self._graph, records)
        outcome = _replace_subtree(unbound, root, names, new, policies)
        candidate = outcome.graph
        new_by_tier = _descendant_indexes(frozenset(outcome.new_items.values()))
        candidate, reports, detached_dependencies = self._replacement_bindings(
            candidate,
            timed,
            old_by_tier,
            new_by_tier,
            outcome.correspondence,
            records,
            detached,
            outcome.detached,
        )
        next_profile = self._profile_for(candidate)
        self._graph = candidate
        self._profile = next_profile
        self._detached_dependencies = detached_dependencies
        self._reports.extend(reports)
        return self

    def _replacement_bindings(  # noqa: PLR0915 -- rebuilds one atomic clock edit
        self,
        graph: Graph,
        tiers: tuple[QualifiedName, ...],
        old_by_tier: Mapping[QualifiedName, tuple[int, ...]],
        new_by_tier: Mapping[QualifiedName, tuple[int, ...]],
        correspondence: Mapping[ItemRef, tuple[ItemRef, ...]],
        records: tuple[_BindingRecord, ...],
        detached: tuple[_DetachedRelationFact, ...],
        detached_dependencies: tuple[DetachedDependency, ...],
    ) -> tuple[Graph, tuple[ClockEditReport, ...], tuple[DetachedDependency, ...]]:
        """Rebuild all affected binding sets after one multi-tier replacement."""
        by_tier: dict[QualifiedName, dict[int, _BindingRecord]] = {
            tier: {
                record.boundary.index: record
                for record in records
                if record.boundary.tier == tier
            }
            for tier in tiers
        }
        rebuilt: list[RelationInstance] = []
        original_to_final: dict[int, int] = {}
        reports: list[ClockEditReport] = []
        relation_offset = len(graph.relations)
        corresponding_boundaries = _corresponding_boundary_origins(correspondence)
        for tier in tiers:
            old_indexes = old_by_tier.get(tier, ())
            new_indexes = new_by_tier.get(tier, ())
            old_count = len(old_indexes)
            new_count = len(new_indexes)
            start = old_indexes[0] if old_indexes else new_indexes[0]
            old_records = by_tier[tier]
            changes: list[ClockBindingChange] = []
            used: set[int] = set()
            new_tier_count = len(graph._tiers_by_name[tier].items)
            for boundary_index in range(new_tier_count + 1):
                template_origin: int | None
                target_origin: int
                if boundary_index < start:
                    template_origin = boundary_index
                    target_origin = template_origin
                elif boundary_index == start:
                    template_origin = start
                    target_origin = template_origin
                elif boundary_index < start + new_count:
                    corresponding_origin = (
                        corresponding_boundaries.get(BoundaryRef(tier, boundary_index))
                        if self._policy is ClockRebindingPolicy.KEEP_EARLIER
                        else None
                    )
                    target_origin = (
                        start if corresponding_origin is None else corresponding_origin
                    )
                    template_origin = (
                        corresponding_origin
                        if corresponding_origin not in {start, start + old_count}
                        else None
                    )
                elif boundary_index == start + new_count:
                    template_origin = start + old_count
                    target_origin = template_origin
                else:
                    template_origin = boundary_index - new_count + old_count
                    target_origin = template_origin
                target = old_records[target_origin]
                template = (
                    None if template_origin is None else old_records[template_origin]
                )
                if template_origin is not None:
                    used.add(template_origin)
                boundary = BoundaryRef(tier, boundary_index)
                source = anchored_boundary(graph, boundary)
                relation = RelationInstance(
                    target.relation.declaration,
                    source,
                    target.relation.right,
                    None if template is None else template.relation.durable_id,
                    () if template is None else template.relation.attributes,
                )
                final_index = relation_offset + len(rebuilt)
                rebuilt.append(relation)
                if template is not None:
                    original_to_final[template.position] = final_index
                previous_clock = (
                    None
                    if template is None
                    else self._profile.clock_index(template.boundary)
                )
                clock_index = self._profile.clock_index(target.boundary)
                if template is None or template.relation.left != source:
                    changes.append(
                        ClockBindingChange(
                            None if template is None else template.boundary,
                            boundary,
                            None if template is None else template.relation.left,
                            source,
                            previous_clock,
                            clock_index,
                            template is None,
                        )
                    )
            changes.extend(
                ClockBindingChange(
                    record.boundary,
                    None,
                    record.relation.left,
                    None,
                    self._profile.clock_index(record.boundary),
                    None,
                    False,
                )
                for origin, record in old_records.items()
                if origin not in used
            )
            needs_realignment = any(change.provisional for change in changes)
            reports.append(
                ClockEditReport(
                    ClockEditOperation.SUBTREE_REPLACEMENT,
                    cast(ClockRebindingPolicy, self._policy),
                    tier,
                    tuple(changes),
                    needs_realignment,
                )
            )
        retained_detached = tuple(
            held for held in detached if held.position in original_to_final
        )
        dropped_detached = tuple(
            held for held in detached if held.position not in original_to_final
        )
        if dropped_detached:
            from tiergraph.replacement import DetachedDependency  # noqa: PLC0415

            detached_dependencies = (
                *detached_dependencies,
                *(
                    DetachedDependency(
                        "layer",
                        held.fact_index,
                        layer=held.layer,
                        subject=held.fact.subject,
                    )
                    for held in dropped_detached
                ),
            )
        layers = _rebuilt_layers(
            graph.layers,
            {index: index for index in range(len(graph.relations))},
            retained_detached,
            original_to_final,
        )
        result = replace(graph, relations=(*graph.relations, *rebuilt), layers=layers)
        for report in reports:
            if report.needs_realignment:
                result = _record_needs_realignment(result, report.tier)
        return result, tuple(reports), detached_dependencies

    def undeclare_with_contents(
        self, target: str | QualifiedName | EditDeclaration
    ) -> ClockEditor:
        """Cascade one declaration under this session's rebinding policy.

        The complete graph-level cascade is staged before this editor changes.
        Removing or changing a clock binding, the clock tier, the binding
        declaration, or items on a bound tier requires a named policy. The
        report records every withdrawn binding under that policy. A withdrawal
        leaves no binding behind, so it does not claim realignment.

        A cascade that removes the clock tier or binding contract necessarily
        retires this profile. The resulting graph and reports remain available
        through :meth:`freeze` and :attr:`reports`, but :attr:`profile` and any
        later profile-aware edit refuse.
        """
        self._require_active_profile()
        candidate = undeclare_with_contents(self._graph, target)
        affected, changes, impacts_timing = self._cascade_clock_impact(candidate)
        if impacts_timing and self._policy is None:
            self._missing_policy(
                ClockEditOperation.DECLARATION_CASCADE,
                affected[0] if affected else self._profile.clock_tier,
            )

        next_profile: ClockProfile | None
        try:
            next_profile = self._profile_for(candidate)
        except ValueError as error:
            if not impacts_timing or self._policy is None:
                raise GraphValidationError(
                    "declaration cascade would invalidate the active clock profile: "
                    f"{error}"
                ) from error
            next_profile = None

        reports: tuple[ClockEditReport, ...] = ()
        if impacts_timing:
            policy = cast(ClockRebindingPolicy, self._policy)
            reports = tuple(
                ClockEditReport(
                    ClockEditOperation.DECLARATION_CASCADE,
                    policy,
                    tier,
                    changes.get(tier, ()),
                    False,
                )
                for tier in affected
            )
        self._graph = candidate
        self._reports.extend(reports)
        if next_profile is None:
            self._profile_active = False
        else:
            self._profile = next_profile
        return self

    def _cascade_clock_impact(
        self, candidate: Graph
    ) -> tuple[
        tuple[QualifiedName, ...],
        dict[QualifiedName, tuple[ClockBindingChange, ...]],
        bool,
    ]:
        """Compare a staged cascade with the active profile's timing structure."""
        remaining_relations = list(candidate.relations)
        changes_by_tier: dict[QualifiedName, list[ClockBindingChange]] = {}
        for relation in self._graph.relations:
            if relation.declaration != self._profile.binding_relation:
                continue
            try:
                remaining_relations.remove(relation)
            except ValueError:
                boundary = self._graph.resolve_boundary(
                    cast(DurableBoundaryRef, relation.left)
                )
                changes_by_tier.setdefault(boundary.tier, []).append(
                    ClockBindingChange(
                        boundary,
                        None,
                        relation.left,
                        None,
                        self._profile.clock_index(boundary),
                        None,
                        False,
                    )
                )

        before_tiers = {tier.declaration.name: tier for tier in self._graph.tiers}
        after_tiers = {tier.declaration.name: tier for tier in candidate.tiers}
        affected = set(changes_by_tier)
        for name, tier in before_tiers.items():
            if name == self._profile.clock_tier or not self._profile.is_timed(name):
                continue
            after = after_tiers.get(name)
            if after is None or after.items != tier.items:
                affected.add(name)

        old_clock = before_tiers[self._profile.clock_tier]
        clock_changed = after_tiers.get(self._profile.clock_tier) != old_clock
        before_binding = next(
            declaration
            for declaration in self._graph.relation_declarations
            if declaration.name == self._profile.binding_relation
        )
        after_binding = next(
            (
                declaration
                for declaration in candidate.relation_declarations
                if declaration.name == self._profile.binding_relation
            ),
            None,
        )
        binding_changed = after_binding != before_binding
        impacts_timing = bool(affected) or clock_changed or binding_changed
        if impacts_timing and not affected:
            affected.add(self._profile.clock_tier)
        return (
            tuple(sorted(affected)),
            {
                tier: tuple(tier_changes)
                for tier, tier_changes in changes_by_tier.items()
            },
            impacts_timing,
        )

    def _require_active_profile(self) -> None:
        """Refuse another profile edit after its declarations were removed."""
        if not self._profile_active:
            raise GraphValidationError(
                "clock profile was retired by a declaration cascade; start a new "
                "clock session for the resulting graph"
            )

    def _no_op(self, operation: ClockEditOperation, tier: QualifiedName) -> ClockEditor:
        """Report a timed no-op without inventing a timing change."""
        if tier == self._profile.clock_tier:
            raise GraphValidationError(
                f"{operation} cannot restructure the clock tier in a bound session"
            )
        if not self._profile.is_timed(tier):
            return self
        if self._policy is None:
            self._missing_policy(operation, tier)
        if getattr(self, "_capture_journal_displacement", False):
            self._journal_displacement = Displacement.stationary(self._graph)
        self._reports.append(
            ClockEditReport(
                operation,
                cast(ClockRebindingPolicy, self._policy),
                tier,
                (),
                False,
            )
        )
        return self

    def _tier_edit(
        self,
        operation: ClockEditOperation,
        tier: QualifiedName,
        edit: Callable[[GraphEditor], GraphEditor],
        template_origins: tuple[int | None, ...],
        keep_targets: tuple[int, ...],
        provisional_targets: tuple[int, ...],
    ) -> ClockEditor:
        """Apply one tier restructure on temporary state, then adopt it."""
        if self._policy is None:
            self._missing_policy(operation, tier)
        policy = cast(ClockRebindingPolicy, self._policy)
        records = self._binding_records(tier)
        unbound, detached = _unbind_relations(self._graph, records)
        editor = unbound.edit()
        edit(editor)
        if getattr(self, "_capture_journal_displacement", False):
            self._journal_displacement = editor.displacement()
        candidate = editor.freeze()
        targets = (
            keep_targets
            if policy is ClockRebindingPolicy.KEEP_EARLIER
            else provisional_targets
        )
        candidate, changes = self._rebuild_bindings(
            candidate,
            tier,
            template_origins,
            targets,
            records=records,
            detached=detached,
        )
        needs_realignment = policy is ClockRebindingPolicy.DROP_TO_PROVISIONAL or any(
            change.provisional for change in changes
        )
        if needs_realignment:
            candidate = _record_needs_realignment(candidate, tier)
        next_profile = self._profile_for(candidate)
        report = ClockEditReport(
            operation,
            policy,
            tier,
            changes,
            needs_realignment,
        )
        self._graph = candidate
        self._profile = next_profile
        self._reports.append(report)
        return self

    def _rebuild_bindings(
        self,
        graph: Graph,
        tier: QualifiedName,
        template_origins: tuple[int | None, ...],
        target_origins: tuple[int, ...],
        *,
        records: tuple[_BindingRecord, ...] | None = None,
        detached: tuple[_DetachedRelationFact, ...] = (),
    ) -> tuple[Graph, tuple[ClockBindingChange, ...]]:
        """Rebuild one tier's binding relations and report every changed one."""
        old_records = self._binding_records(tier) if records is None else records
        by_boundary = {record.boundary.index: record for record in old_records}
        rebuilt: list[tuple[int | None, RelationInstance]] = []
        changes: list[ClockBindingChange] = []
        used_templates: set[int] = set()
        for boundary_index, (template_origin, target_origin) in enumerate(
            zip(template_origins, target_origins, strict=True)
        ):
            target_record = by_boundary[target_origin]
            template = None if template_origin is None else by_boundary[template_origin]
            if template_origin is not None:
                used_templates.add(template_origin)
            boundary = BoundaryRef(tier, boundary_index)
            source = anchored_boundary(graph, boundary)
            relation = RelationInstance(
                target_record.relation.declaration,
                source,
                target_record.relation.right,
                None if template is None else template.relation.durable_id,
                () if template is None else template.relation.attributes,
            )
            rebuilt.append((template_origin, relation))
            previous_source = None if template is None else template.relation.left
            previous_clock = (
                None
                if template is None
                else self._profile.clock_index(template.boundary)
            )
            clock_index = self._profile.clock_index(target_record.boundary)
            if (
                template is None
                or previous_source != source
                or previous_clock != clock_index
            ):
                changes.append(
                    ClockBindingChange(
                        None if template is None else template.boundary,
                        boundary,
                        previous_source,
                        source,
                        previous_clock,
                        clock_index,
                        template_origin is None or template_origin != target_origin,
                    )
                )
        changes.extend(
            ClockBindingChange(
                record.boundary,
                None,
                record.relation.left,
                None,
                self._profile.clock_index(record.boundary),
                None,
                False,
            )
            for record in old_records
            if record.boundary.index not in used_templates
        )

        replacements = {
            origin: relation for origin, relation in rebuilt if origin is not None
        }
        extras = [relation for origin, relation in rebuilt if origin is None]
        positions = {record.position: record for record in old_records}
        other_relations = iter(graph.relations)
        relations: list[RelationInstance] = []
        unbound_to_final: dict[int, int] = {}
        original_to_final: dict[int, int] = {}
        unbound_position = 0
        last_position = max(positions)
        for position in range(len(self._graph.relations)):
            record = positions.get(position)
            if record is None:
                unbound_to_final[unbound_position] = len(relations)
                relations.append(next(other_relations))
                unbound_position += 1
            else:
                replacement = replacements.get(record.boundary.index)
                if replacement is not None:
                    original_to_final[position] = len(relations)
                    relations.append(replacement)
            if position == last_position:
                relations.extend(extras)
        relations.extend(other_relations)
        result = replace(
            graph,
            relations=tuple(relations),
            layers=_rebuilt_layers(
                graph.layers,
                unbound_to_final,
                detached,
                original_to_final,
            ),
        )
        return result, tuple(changes)

    def _binding_records(self, tier: QualifiedName) -> tuple[_BindingRecord, ...]:
        """Return this profile's binding relations for one tier."""
        return tuple(
            _BindingRecord(
                index,
                self._graph.resolve_boundary(cast(DurableBoundaryRef, relation.left)),
                relation,
            )
            for index, relation in enumerate(self._graph.relations)
            if relation.declaration == self._profile.binding_relation
            and self._graph.resolve_boundary(
                cast(DurableBoundaryRef, relation.left)
            ).tier
            == tier
        )

    def _route_plain_tier_edit(
        self,
        operation: ClockEditOperation,
        tier: QualifiedName,
        edit: Callable[[GraphEditor], GraphEditor],
    ) -> bool:
        """Refuse the clock tier or apply an untimed edit without profile rebuild."""
        if tier == self._profile.clock_tier:
            raise GraphValidationError(
                f"{operation} cannot restructure the clock tier in a bound session"
            )
        if self._profile.is_timed(tier):
            return False
        editor = self._graph.edit()
        edit(editor)
        if getattr(self, "_capture_journal_displacement", False):
            self._journal_displacement = editor.displacement()
        candidate = editor.freeze()
        self._graph = candidate
        self._profile = self._profile_with_graph(candidate)
        return True

    def _profile_with_graph(self, graph: Graph) -> ClockProfile:
        """Retarget validated profile caches after an untimed-tier restructure."""
        profile = object.__new__(ClockProfile)
        for name in (
            "clock_tier",
            "binding_relation",
            "rate_attribute",
            "unit_attribute",
            "tick_attribute",
            "gap_attribute",
            "untimed_attribute",
            "start_attribute",
            "duration_attribute",
            "_rate",
            "_unit",
            "_bindings",
            "_clock_coordinates",
            "_timings",
            "_untimed_tiers",
            "_structural",
        ):
            object.__setattr__(profile, name, getattr(self._profile, name))
        object.__setattr__(profile, "graph", graph)
        return profile

    def _item_count(self, tier: QualifiedName, operation: ClockEditOperation) -> int:
        """Return a declared tier's item count or refuse with editor wording."""
        member = next(
            (item for item in self._graph.tiers if item.declaration.name == tier), None
        )
        if member is None:
            raise GraphValidationError(
                f"{operation} names undeclared tier {str(tier)!r}"
            )
        return len(member.items)

    def _profile_for(self, graph: Graph) -> ClockProfile:
        """Rebuild this session's profile declaration against another graph."""
        return ClockProfile(
            graph,
            self._profile.clock_tier,
            self._profile.binding_relation,
            self._profile.rate_attribute,
            self._profile.unit_attribute,
            self._profile.tick_attribute,
            self._profile.gap_attribute,
            self._profile.untimed_attribute,
            self._profile.start_attribute,
            self._profile.duration_attribute,
        )

    @staticmethod
    def _missing_policy(operation: ClockEditOperation, tier: QualifiedName) -> None:
        """Refuse a timed restructure before any editor state is created."""
        raise GraphValidationError(
            f"{operation} on clock-bound tier {str(tier)!r} requires a named "
            "rebinding policy"
        )


def _endpoint_tuple(
    endpoints: RelationEndpointRef | Iterable[RelationEndpointRef],
) -> tuple[RelationEndpointRef, ...]:
    """Materialize one endpoint side without iterating a scalar reference."""
    if isinstance(
        endpoints, ItemRef | DurableItemRef | BoundaryRef | DurableBoundaryRef
    ):
        return (endpoints,)
    return tuple(endpoints)


def _endpoint_tier(graph: Graph, endpoint: RelationEndpointRef) -> QualifiedName:
    """Return the tier that owns one item or boundary endpoint."""
    if isinstance(endpoint, ItemRef | DurableItemRef):
        return graph.resolve_item(endpoint).tier
    return graph.resolve_boundary(endpoint).tier


def _target_relation(
    graph: Graph, target: RelationTarget
) -> RelationInstance | PolyadicRelationInstance:
    """Resolve an editor relation target without changing the graph."""
    polyadic, index = graph.edit()._relation_site(target)
    return graph.polyadic_relations[index] if polyadic else graph.relations[index]


def _record_needs_realignment(graph: Graph, tier: QualifiedName) -> Graph:
    """Record the standard tier fact required by collapsed rebinding."""
    editor = graph.edit()
    if not any(
        namespace.namespace == _CLOCK_EDIT_NAMESPACE for namespace in graph.namespaces
    ):
        prefixes = {namespace.prefix for namespace in graph.namespaces}
        prefix = "clock-edit"
        suffix = 2
        while prefix in prefixes:
            prefix = f"clock-edit-{suffix}"
            suffix += 1
        editor.declare(NamespaceDeclaration(prefix, _CLOCK_EDIT_NAMESPACE))
    if not any(
        declaration.name == _NEEDS_REALIGNMENT
        for declaration in graph.attribute_declarations
    ):
        editor.declare(
            AttributeDeclaration(
                _NEEDS_REALIGNMENT, AttributeDomain.TIER, XsdType.BOOLEAN
            )
        )
    if not any(layer.name == _REALIGNMENT_LAYER for layer in graph.layers):
        editor.add_layer(_REALIGNMENT_LAYER)
    editor.put_fact(
        _REALIGNMENT_LAYER,
        LayerFact(
            TierRef(tier),
            AttributeValue(_NEEDS_REALIGNMENT, XsdType.BOOLEAN, "true"),
        ),
    )
    return editor.freeze()


def _decimal_times_rate_equals(value: Decimal, rate: Decimal, tick: int) -> bool:
    """Compare ``value * rate`` with an integer using exact integer products."""
    value_numerator, value_denominator = value.as_integer_ratio()
    rate_numerator, rate_denominator = rate.as_integer_ratio()
    return (
        value_numerator * rate_numerator == tick * value_denominator * rate_denominator
    )


def _exact_decimal_ratio(tick: int, rate: Decimal) -> Decimal:
    """Return ``tick / rate`` only when its decimal expansion terminates."""
    rate_numerator, rate_denominator = rate.as_integer_ratio()
    numerator = tick * rate_denominator
    denominator = rate_numerator
    common = gcd(abs(numerator), denominator)
    numerator //= common
    denominator //= common

    twos = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    fives = 0
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    if denominator != 1:
        raise ValueError(
            f"coarse-tick ratio ({tick}, {rate!r}) cannot be represented exactly "
            "as Decimal; use duration()"
        )

    scale = max(twos, fives)
    coefficient = numerator * 2 ** (scale - twos) * 5 ** (scale - fives)
    sign = int(coefficient < 0)
    digits = tuple(int(digit) for digit in str(abs(coefficient)))
    return Decimal((sign, digits, -scale))


def _collapse_shared_boundaries(
    coordinates: tuple[ClockCoordinate, ...],
) -> tuple[ClockCoordinate, ...]:
    """Fold each coarse tick's trailing gap, keeping one node per occupied gap.

    A tick's closing boundary and the next tick's opening boundary lie on the
    same instant. Collapsing drops each tick's final raw gap so the
    spine shows exactly the occupied coordinates: a tick with ``R`` raw
    boundaries keeps gaps ``0`` through ``R - 2``. The terminal tick's closing
    boundary is dropped the same way. The input is strictly ordered, so equal
    ticks are consecutive and the drop is always the last member of each run.

    A tick with a single raw boundary (``R == 1``) has no shared closing
    boundary to fold; collapsing it would delete the tick entirely, so it is
    refused rather than silently dropped. Collapse therefore only ever folds
    the trailing boundary of a tick that has at least two.
    """
    collapsed: list[ClockCoordinate] = []
    index = 0
    count = len(coordinates)
    while index < count:
        tail = index
        while (
            tail + 1 < count and coordinates[tail + 1].tick == coordinates[index].tick
        ):
            tail += 1
        if tail == index:
            raise ValueError(
                f"clock tick {coordinates[index].tick} has a single raw boundary; "
                "collapse_shared_boundaries needs at least two raw boundaries per "
                "tick so the shared closing boundary can be folded without "
                "deleting the tick"
            )
        collapsed.extend(coordinates[index:tail])
        index = tail + 1
    return tuple(collapsed)


def anchored_boundary(graph: Graph, boundary: BoundaryRef) -> DurableBoundaryRef:
    """Name a boundary by either adjacent durable anchor without changing it."""
    tier = next(
        (
            candidate
            for candidate in graph.tiers
            if candidate.declaration.name == boundary.tier
        ),
        None,
    )
    if tier is None or boundary.index < 0 or boundary.index > len(tier.items):
        raise GraphValidationError(
            f"boundary {boundary.to_data()!r} is outside its tier",
            RefusalStage.REFERENCE,
        )
    if boundary.index == 0:
        return DurableBoundaryRef(boundary.tier, BoundarySide.BEFORE)
    if boundary.index == len(tier.items):
        return DurableBoundaryRef(boundary.tier, BoundarySide.AFTER)
    anchor = tier.items[boundary.index].durable_id
    if anchor is not None:
        return DurableBoundaryRef(DurableItemRef(anchor), BoundarySide.BEFORE)
    previous_anchor = tier.items[boundary.index - 1].durable_id
    if previous_anchor is not None:
        return DurableBoundaryRef(DurableItemRef(previous_anchor), BoundarySide.AFTER)
    raise GraphValidationError(
        f"boundary {boundary.to_data()!r} has no adjacent durable anchor",
        RefusalStage.REFERENCE,
    )
