# Timing

A `ClockProfile` interprets one tier as a clock and maps the boundaries of other
tiers onto it. Time is kept structural: a clock position is an integer tick with
an optional ordered gap, so inserting a boundary does not renumber the existing
ticks, and two point events can share a tick while keeping a defined order. When
a document also declares a rate, the profile derives physical timing from the
clock; events may also carry their own stored timing, and when both exist they
must agree.

## Building a clock

A clock needs a clock tier, a boundary-to-boundary binding relation, and a
document-level unit. The example has a `beats` tier of four ticks and an
`events` tier of two events. The `binds` relation ties each event boundary to a
clock boundary, and a rate of two ticks per second lets the profile compute
physical timing. Every boundary of a timed tier must bind, so `intro` and
`verse` between them cover the whole span.

```python
from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    BoundarySide,
    ClockProfile,
    DurableItemRef,
    DurablePositionRef,
    Graph,
    Item,
    ItemRef,
    NamespaceDeclaration,
    PositionRef,
    QualifiedName,
    RelationEndpointKind,
    RelationInstance,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    XsdType,
    anchored_position,
)

ns = "https://example.com/timeline"
beats = QualifiedName(ns, "beats")
events = QualifiedName(ns, "events")
beat_type = QualifiedName(ns, "beat")
event_type = QualifiedName(ns, "event")
binds = QualifiedName(ns, "binds")
unit = QualifiedName(ns, "unit")
rate = QualifiedName(ns, "rate")

graph = Graph(
    (NamespaceDeclaration("tl", ns),),
    (
        Tier(TierDeclaration(beats, "Beats"), tuple(Item(f"t{i}") for i in range(4))),
        Tier(TierDeclaration(events, "Events"), (Item("intro"), Item("verse"))),
    ),
    (
        SimpleRelationDeclaration(
            QualifiedName(ns, "beat-membership"), beats, beat_type
        ),
        SimpleRelationDeclaration(
            QualifiedName(ns, "event-membership"), events, event_type
        ),
        BipartiteRelationDeclaration(
            binds,
            event_type,
            beat_type,
            left_endpoint=RelationEndpointKind.BOUNDARY,
            right_endpoint=RelationEndpointKind.BOUNDARY,
        ),
    ),
    (
        RelationInstance(
            binds,
            DurablePositionRef(events, BoundarySide.BEFORE),
            DurablePositionRef(beats, BoundarySide.BEFORE),
        ),
        RelationInstance(
            binds,
            DurablePositionRef(DurableItemRef("verse"), BoundarySide.BEFORE),
            DurablePositionRef(DurableItemRef("t2"), BoundarySide.BEFORE),
        ),
        RelationInstance(
            binds,
            DurablePositionRef(events, BoundarySide.AFTER),
            DurablePositionRef(beats, BoundarySide.AFTER),
        ),
    ),
    (
        AttributeDeclaration(unit, AttributeDomain.DOCUMENT, XsdType.STRING),
        AttributeDeclaration(rate, AttributeDomain.DOCUMENT, XsdType.DECIMAL),
    ),
    (),
    (
        AttributeValue(unit, XsdType.STRING, "second"),
        AttributeValue(rate, XsdType.DECIMAL, "2"),
    ),
)

clock = ClockProfile(graph, beats, binds, rate, unit)
intro_start, intro_end = clock.structural_span(events, 0)
print("clock:", clock.rate, "ticks per", clock.unit)
print("intro ticks:", intro_start.tick, "to", intro_end.tick)
for index, name in enumerate(("intro", "verse")):
    timing = clock.timing(events, index)
    assert timing is not None
    print(f"{name}: start {timing.start} {timing.unit}, duration {timing.duration}")
print(
    "anchor of events boundary 1:",
    anchored_position(graph, PositionRef(events, 1)).side.value,
)
```

```text
clock: 2.0 ticks per second
intro ticks: 0 to 2
intro: start 0 second, duration 1
verse: start 1 second, duration 1
anchor of events boundary 1: before
```

`structural_span` returns the refined clock positions bounding an event.
`intro` spans ticks 0 to 2 and `verse` spans 2 to 4. At two ticks per second
that is one second each, and `timing` returns those exact decimal values stamped
with the declared unit. Because the rate divides evenly here, the physical
durations are exact; when a tick-to-rate ratio has no finite decimal form,
`timing` refuses it and `duration` returns the exact tick span and rate instead.

`anchored_position` names an existing boundary by an anchor without changing the
graph. Boundary 1 of the `events` tier is the edge before `verse`, so it is
reported as the `before` side of that item's anchor. Anchored references are how
a boundary keeps its identity across edits that shift structural indexes.

## What the profile checks and leaves open

Constructing the profile validates the declarations, the totality of the
bindings, any refinement, and the agreement between a rate and stored timings.
It stays silent where a document is deliberately partial: a tier can be marked
untimed and carry no bindings, a bound event with neither a rate nor stored
timing has no physical time, and trailing silence needs its own explicit item
rather than being assumed.
