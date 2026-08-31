"""A clock profile with refined structure and explicitly reconciled time."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from math import gcd

from tiergraph.core import (
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    BoundaryRef,
    BoundarySide,
    DurableBoundaryRef,
    DurableItemRef,
    Graph,
    ItemRef,
    QualifiedName,
    RelationEndpointKind,
    XsdType,
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

    def __post_init__(self) -> None:
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
        return value

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
                tick = int(values[self.tick_attribute].lexical)
                gap = int(values[self.gap_attribute].lexical)
            except KeyError as error:
                raise ValueError(
                    f"clock boundary {reference.to_data()!r} lacks refinement"
                ) from error
            coordinates.append(ClockCoordinate(tick, gap))
        if coordinates != sorted(coordinates) or len(set(coordinates)) != len(
            coordinates
        ):
            raise ValueError("clock refinement coordinates are not strictly ordered")
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
                value.name == self.untimed_attribute and value.lexical == "true"
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
                start = Decimal(values[self.start_attribute].lexical)
                duration = Decimal(values[self.duration_attribute].lexical)
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
    """Name an existing boundary by its anchor without changing the graph."""
    tier = next(
        (
            candidate
            for candidate in graph.tiers
            if candidate.declaration.name == boundary.tier
        ),
        None,
    )
    if tier is None or boundary.index < 0 or boundary.index > len(tier.items):
        raise ValueError(f"boundary {boundary.to_data()!r} is outside its tier")
    if boundary.index == 0:
        return DurableBoundaryRef(boundary.tier, BoundarySide.BEFORE)
    if boundary.index == len(tier.items):
        return DurableBoundaryRef(boundary.tier, BoundarySide.AFTER)
    anchor = tier.items[boundary.index].durable_id
    if anchor is None:
        raise ValueError(
            f"boundary {boundary.to_data()!r} needs a durable right-hand anchor"
        )
    return DurableBoundaryRef(DurableItemRef(anchor), BoundarySide.BEFORE)
