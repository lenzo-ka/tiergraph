"""A shared integral clock profile over kernel tiers and boundary relations."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from tiergraph.core import (
    AttributeDomain,
    BipartiteRelationDeclaration,
    BoundarySide,
    DurableItemRef,
    DurablePositionRef,
    Graph,
    PositionRef,
    QualifiedName,
    RelationEndpointKind,
    XsdType,
)


@dataclass(frozen=True, slots=True)
class ClockProfile:
    """Bind tier positions to positions on one integral clock tier.

    A tier item occupies the half-open clock span between the bindings of its
    adjacent positions.  Bindings must be total, single-valued, and monotone;
    equality expresses a zero-duration item.  The first and last bindings give
    the tier extent, so a tier may start after the clock origin or stop before
    the clock end.

    There is no unoccupied extent after a tier's last item: its last position
    is both the item's end and the tier sink.  Trailing silence therefore needs
    an explicit gap item.  Likewise, events on the same clock span necessarily
    have the same derived duration; different durations for that span are not
    expressible.

    Tick duration is ``1 / rate`` in the rate's declared continuous unit.  No
    duration, offset, or event time is stored by this profile.
    """

    graph: Graph
    clock_tier: QualifiedName
    binding_relation: QualifiedName
    rate_attribute: QualifiedName
    _rate: Decimal = field(init=False, repr=False)
    _bindings: dict[PositionRef, int] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Validate the profile declaration and derive its coordinate map."""
        tiers = {tier.declaration.name: tier for tier in self.graph.tiers}
        clock = tiers.get(self.clock_tier)
        if clock is None:
            raise ValueError(f"clock tier {str(self.clock_tier)!r} is not declared")

        rate_declaration = next(
            (
                declaration
                for declaration in self.graph.attribute_declarations
                if declaration.name == self.rate_attribute
            ),
            None,
        )
        if rate_declaration is None:
            raise ValueError(f"clock rate {str(self.rate_attribute)!r} is not declared")
        if (
            rate_declaration.domain is not AttributeDomain.DOCUMENT
            or rate_declaration.value_type is not XsdType.DECIMAL
        ):
            raise ValueError("clock rate must be a document decimal attribute")
        rate_value = next(
            (
                value
                for value in self.graph.attributes
                if value.name == self.rate_attribute
            ),
            None,
        )
        if rate_value is None:
            raise ValueError(f"clock rate {str(self.rate_attribute)!r} has no value")
        rate = Decimal(rate_value.lexical)
        # Positivity uses the XSD decimal value order, never lexical order.
        if rate <= 0:
            raise ValueError(f"clock rate {rate_value.lexical!r} must be positive")

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

        bindings: dict[PositionRef, int] = {}
        for relation in self.graph.relations:
            if relation.declaration != self.binding_relation:
                continue
            # The kernel enforces anchored references for boundary endpoints.
            assert isinstance(relation.left, DurablePositionRef)
            assert isinstance(relation.right, DurablePositionRef)
            source = self.graph.resolve_position(relation.left)
            target = self.graph.resolve_position(relation.right)
            if target.tier != self.clock_tier:
                raise ValueError("clock binding target is not on the clock tier")
            if source.tier == self.clock_tier:
                raise ValueError("clock tier positions do not bind to themselves")
            if source in bindings:
                raise ValueError(f"tier position {source.to_data()!r} has two bindings")
            bindings[source] = target.index

        for tier_name, tier in tiers.items():
            if tier_name == self.clock_tier:
                continue
            coordinates = []
            for index in range(len(tier.items) + 1):
                position = PositionRef(tier_name, index)
                if position not in bindings:
                    raise ValueError(
                        f"tier position {position.to_data()!r} has no clock binding"
                    )
                coordinates.append(bindings[position])
            # Clock-position order is the clock tier's declared structural order.
            if coordinates != sorted(coordinates):
                raise ValueError(
                    f"clock bindings for tier {str(tier_name)!r} go backward"
                )

        object.__setattr__(self, "_rate", rate)
        object.__setattr__(self, "_bindings", bindings)

    @property
    def rate(self) -> Decimal:
        """Return the declared ticks per continuous unit."""
        return self._rate

    def clock_position(self, position: PositionRef) -> int:
        """Return the integral clock position bound to one tier position."""
        try:
            return self._bindings[position]
        except KeyError as error:
            raise ValueError(
                f"tier position {position.to_data()!r} has no clock binding"
            ) from error

    def extent(self, tier: QualifiedName) -> tuple[int, int]:
        """Return a tier's clock-position extent, which may be partial-document."""
        member = next(
            (
                candidate
                for candidate in self.graph.tiers
                if candidate.declaration.name == tier
            ),
            None,
        )
        if member is None or tier == self.clock_tier:
            raise ValueError(f"timed tier {str(tier)!r} is not declared")
        return (
            self.clock_position(PositionRef(tier, 0)),
            self.clock_position(PositionRef(tier, len(member.items))),
        )

    def duration(self, tier: QualifiedName, index: int) -> Decimal:
        """Derive one item's continuous duration from its bound clock span."""
        start = self.clock_position(PositionRef(tier, index))
        end = self.clock_position(PositionRef(tier, index + 1))
        return Decimal(end - start) / self._rate


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
