"""A clock profile with refined structure and explicitly reconciled time."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from math import gcd

from tiergraph.core import (
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    BoundarySide,
    DurableItemRef,
    DurablePositionRef,
    Graph,
    ItemRef,
    PositionRef,
    QualifiedName,
    RelationEndpointKind,
    XsdType,
)


@dataclass(frozen=True, slots=True, order=True)
class ClockPosition:
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
            raise ValueError(f"clock position {(self.tick, self.gap)!r} is negative")


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
    position attributes refine it to ``(coarse tick, ordered gap)``.  Thus two
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
    clock boundary is the unrefined position ``(index, 0)``.  Partial document
    extents remain valid, and trailing silence still needs an explicit item.
    """

    graph: Graph
    clock_tier: QualifiedName
    binding_relation: QualifiedName
    rate_attribute: QualifiedName | None
    unit_attribute: QualifiedName
    tick_attribute: QualifiedName | None = None
    gap_attribute: QualifiedName | None = None
    untimed_attribute: QualifiedName | None = None
    start_attribute: QualifiedName | None = None
    duration_attribute: QualifiedName | None = None
    _rate: Decimal | None = field(init=False, repr=False)
    _unit: str = field(init=False, repr=False)
    _bindings: dict[PositionRef, int] = field(init=False, repr=False)
    _clock_positions: tuple[ClockPosition, ...] = field(init=False, repr=False)
    _timings: dict[ItemRef, PhysicalTiming] = field(init=False, repr=False)
    _untimed_tiers: frozenset[QualifiedName] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Validate declarations, totality, refinement, and timing agreement."""
        tiers = {tier.declaration.name: tier for tier in self.graph.tiers}
        clock = tiers.get(self.clock_tier)
        if clock is None:
            raise ValueError(f"clock tier {str(self.clock_tier)!r} is not declared")

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

        clock_positions = self._read_clock_positions(len(clock.items))
        bindings: dict[PositionRef, int] = {}
        for relation in self.graph.relations:
            if relation.declaration != self.binding_relation:
                continue
            if not isinstance(relation.left, DurablePositionRef):
                raise ValueError("clock binding left endpoint is not a boundary")
            if not isinstance(relation.right, DurablePositionRef):
                raise ValueError("clock binding right endpoint is not a boundary")
            source = self.graph.resolve_position(relation.left)
            target = self.graph.resolve_position(relation.right)
            if target.tier != self.clock_tier:
                raise ValueError("clock binding target is not on the clock tier")
            if source.tier == self.clock_tier:
                raise ValueError("clock tier positions do not bind to themselves")
            if source in bindings:
                raise ValueError(f"tier position {source.to_data()!r} has two bindings")
            bindings[source] = target.index

        untimed_tiers = self._read_untimed_tiers()
        for tier_name, tier in tiers.items():
            if tier_name == self.clock_tier:
                continue
            coordinates = [
                bindings.get(PositionRef(tier_name, index))
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
                position = PositionRef(tier_name, missing)
                raise ValueError(
                    f"tier position {position.to_data()!r} has no clock binding"
                )
            integral = [
                coordinate for coordinate in coordinates if coordinate is not None
            ]
            refined = [clock_positions[coordinate] for coordinate in integral]
            if refined != sorted(refined):
                raise ValueError(
                    f"clock bindings for tier {str(tier_name)!r} go backward"
                )

        timings = self._read_timings(
            untimed_tiers, bindings, clock_positions, rate, unit.lexical
        )
        object.__setattr__(self, "_rate", rate)
        object.__setattr__(self, "_unit", unit.lexical)
        object.__setattr__(self, "_bindings", bindings)
        object.__setattr__(self, "_clock_positions", clock_positions)
        object.__setattr__(self, "_timings", timings)
        object.__setattr__(self, "_untimed_tiers", frozenset(untimed_tiers))

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

    def _read_clock_positions(self, item_count: int) -> tuple[ClockPosition, ...]:
        if (self.tick_attribute is None) != (self.gap_attribute is None):
            raise ValueError("clock refinement requires both tick and gap attributes")
        if self.tick_attribute is None or self.gap_attribute is None:
            return tuple(ClockPosition(index) for index in range(item_count + 1))
        self._declaration(
            self.tick_attribute, AttributeDomain.POSITION, XsdType.INTEGER, "clock tick"
        )
        self._declaration(
            self.gap_attribute, AttributeDomain.POSITION, XsdType.INTEGER, "clock gap"
        )
        positions = []
        for index in range(item_count + 1):
            reference = PositionRef(self.clock_tier, index)
            values = {
                value.name: value
                for value in self.graph.positions(self.clock_tier)[index].attributes
            }
            try:
                tick = int(values[self.tick_attribute].lexical)
                gap = int(values[self.gap_attribute].lexical)
            except KeyError as error:
                raise ValueError(
                    f"clock position {reference.to_data()!r} lacks refinement"
                ) from error
            positions.append(ClockPosition(tick, gap))
        if positions != sorted(positions) or len(set(positions)) != len(positions):
            raise ValueError("clock refinement positions are not strictly ordered")
        return tuple(positions)

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
        bindings: dict[PositionRef, int],
        clock_positions: tuple[ClockPosition, ...],
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
                    left = bindings[PositionRef(reference.tier, index)]
                    right = bindings[PositionRef(reference.tier, index + 1)]
                    start_tick = clock_positions[left].tick
                    tick_span = clock_positions[right].tick - clock_positions[left].tick
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
    def rate(self) -> Decimal | None:
        """Return ticks per declared unit, or ``None`` for an uncalibrated clock."""
        return self._rate

    @property
    def unit(self) -> str:
        """Return the declared physical timing unit."""
        return self._unit

    @property
    def positions(self) -> tuple[ClockPosition, ...]:
        """Return the profile's validated refined clock positions in order."""
        return self._clock_positions

    def is_timed(self, tier: QualifiedName) -> bool:
        """Report whether a tier chose complete clock binding."""
        return tier not in self._untimed_tiers

    def clock_position(self, position: PositionRef) -> int:
        """Return the integral clock-tier boundary bound to one tier position."""
        try:
            return self._bindings[position]
        except KeyError as error:
            raise ValueError(
                f"tier position {position.to_data()!r} has no clock binding"
            ) from error

    def refined_position(self, position: PositionRef) -> ClockPosition:
        """Return the coarse tick and ordered gap bound to one tier position."""
        if position.tier in self._untimed_tiers:
            raise ValueError(f"tier {str(position.tier)!r} is untimed")
        return self._clock_positions[self.clock_position(position)]

    def extent(self, tier: QualifiedName) -> tuple[ClockPosition, ClockPosition]:
        """Return a timed tier's possibly partial refined clock extent."""
        member = next(
            (
                candidate
                for candidate in self.graph.tiers
                if candidate.declaration.name == tier
            ),
            None,
        )
        if member is None or tier == self.clock_tier or tier in self._untimed_tiers:
            raise ValueError(f"timed tier {str(tier)!r} is not declared")
        return self.refined_position(PositionRef(tier, 0)), self.refined_position(
            PositionRef(tier, len(member.items))
        )

    def structural_span(
        self, tier: QualifiedName, index: int
    ) -> tuple[ClockPosition, ClockPosition]:
        """Return an event span between refined integral positions."""
        return self.refined_position(PositionRef(tier, index)), self.refined_position(
            PositionRef(tier, index + 1)
        )

    def timing(self, tier: QualifiedName, index: int) -> PhysicalTiming | None:
        """Return stored timing or exactly representable coarse-tick timing.

        Rate-derived physical timing uses only coarse ticks.  Ordered gaps are
        structural, so a real gap-only span derives zero physical duration.
        When a tick/rate ratio has no finite Decimal representation, this method
        refuses it; :meth:`duration` retains the exact ratio in all cases.
        Explicitly untimed tiers consistently return ``None`` with or without a
        document rate.
        """
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

    def duration(self, tier: QualifiedName, index: int) -> tuple[int, Decimal]:
        """Return the legacy coarse-tick span and rate when a rate exists."""
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


def anchored_position(graph: Graph, position: PositionRef) -> DurablePositionRef:
    """Name an existing boundary by its anchor without changing the graph."""
    tier = next(
        (
            candidate
            for candidate in graph.tiers
            if candidate.declaration.name == position.tier
        ),
        None,
    )
    if tier is None or position.index < 0 or position.index > len(tier.items):
        raise ValueError(f"position {position.to_data()!r} is outside its tier")
    if position.index == 0:
        return DurablePositionRef(position.tier, BoundarySide.BEFORE)
    if position.index == len(tier.items):
        return DurablePositionRef(position.tier, BoundarySide.AFTER)
    anchor = tier.items[position.index].durable_id
    if anchor is None:
        raise ValueError(
            f"position {position.to_data()!r} needs a durable right-hand anchor"
        )
    return DurablePositionRef(DurableItemRef(anchor), BoundarySide.BEFORE)
