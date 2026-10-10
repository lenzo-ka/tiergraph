# Timing

A `ClockProfile` interprets one tier as a clock and maps the boundaries of other
tiers onto it. Time is kept structural: a clock coordinate is an integer tick with
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
    DurableBoundaryRef,
    Graph,
    Item,
    ItemRef,
    NamespaceDeclaration,
    BoundaryRef,
    QualifiedName,
    RelationEndpointKind,
    RelationInstance,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    XsdType,
    anchored_boundary,
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
            DurableBoundaryRef(events, BoundarySide.BEFORE),
            DurableBoundaryRef(beats, BoundarySide.BEFORE),
        ),
        RelationInstance(
            binds,
            DurableBoundaryRef(DurableItemRef("verse"), BoundarySide.BEFORE),
            DurableBoundaryRef(DurableItemRef("t2"), BoundarySide.BEFORE),
        ),
        RelationInstance(
            binds,
            DurableBoundaryRef(events, BoundarySide.AFTER),
            DurableBoundaryRef(beats, BoundarySide.AFTER),
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
    anchored_boundary(graph, BoundaryRef(events, 1)).side.value,
)
```

```text
clock: 2.0 ticks per second
intro ticks: 0 to 2
intro: start 0 second, duration 1
verse: start 1 second, duration 1
anchor of events boundary 1: before
```

`structural_span` returns the refined clock coordinates bounding an event.
`intro` spans ticks 0 to 2 and `verse` spans 2 to 4. At two ticks per second
that is one second each, and `timing` returns those exact decimal values stamped
with the declared unit. Because the rate divides evenly here, the physical
durations are exact; when a tick-to-rate ratio has no finite decimal form,
`timing` refuses it and `duration` returns the exact tick span and rate instead.

`anchored_boundary` names an existing boundary by an anchor without changing the
graph. Boundary 1 of the `events` tier is the edge before `verse`, so it is
reported as the `before` side of that item's anchor. Anchored references are how
a boundary keeps its identity across edits that shift structural indexes.

## Editing a clock-bound tier

Use `ClockProfile.edit()` when a session must keep the clock profile valid after
every operation. Structural edits on a timed tier refuse unless the session
names a rebinding policy:

```python
editor = clock.edit("keep-earlier")
editor.move_item(ItemRef(events, 0), 1)
edited = editor.freeze()
updated_clock = editor.profile
report = editor.reports[-1]

assert updated_clock.graph is edited
assert report.policy.value == "keep-earlier"
```

`keep-earlier` keeps the ordered boundary times in place while items move
through them. When two anchors resolve to the same boundary, the binding that
occurred earlier in the relation order wins; every changed, inserted, or
dropped binding appears in the operation report.

A containment shift binds each moved timed yield to its new child seam. It does
not require a session-wide rebinding policy when the affected boundaries already
meet on the common clock. Its `SHIFT` report uses `keep-earlier` to name that
seam behavior and lists the exact old and new binding endpoints.

`drop-to-provisional` collapses the affected boundaries onto their earlier
clock position. Its report sets `needs_realignment`, and the graph carries a
tier fact with the same meaning so the requirement survives serialization.
Insertion under either policy synthesizes a zero-length provisional span at the
insertion boundary, so it also sets `needs_realignment` and records the durable
tier fact. Removal first withdraws a binding whose anchor departs, then retains
the surviving binding on the merged boundary. Reparenting is also policy-gated
even when `keep-earlier` leaves every clock binding unchanged.

`ClockEditReport.operation` is a `ClockEditOperation`; `policy` is the selected
`ClockRebindingPolicy`; `tier` is the affected timed tier; `changes` contains
every inserted, changed, or withdrawn binding; and `needs_realignment` says the
result contains durably marked provisional timing. In each
`ClockBindingChange`, the `previous_boundary`/`boundary`,
`previous_source`/`source`, `previous_target`/`target`, and
`previous_clock_index`/`clock_index` pairs name the old and new logical
boundary, durable anchor, exact durable clock target, and resolved integral
clock position. Either old or new side is `None` for insertion or withdrawal.
`provisional` means that this resulting binding now holds a synthesized or
collapsed value; it is not merely a marker that its boundary lay inside the
edited span.

Calling `clock.edit()` without a policy is useful for sessions that edit only
untimed tiers. A move, swap, insertion, removal, or reparenting operation that
touches a timed tier refuses before changing the editor. The clock tier itself
cannot be structurally edited through a bound session, even when a rebinding
policy is present.

Declaration cascades use the same explicit-policy boundary. Call
`editor.undeclare_with_contents(...)` to stage the complete cascade before any
session state changes. Removing bindings, the binding declaration, the clock
tier, or bound-tier items refuses without a named policy; with one, every
withdrawn binding is reported. A cascade that removes the clock definition ends
that profile-aware session, so its graph and reports remain available but later
clock edits refuse.

The ordinary `graph.edit()` API remains the profile-free editor. It bypasses
clock rebinding policy and validation. The module-level
`undeclare_with_contents()` cascade does too, so either can silently leave a
previously valid `ClockProfile` invalid. Use `ClockProfile.edit()` whenever clock
validity must be maintained across edits.

## What the profile checks and leaves open

Constructing the profile validates the declarations, the totality of the
bindings, any refinement, and the agreement between a rate and stored timings.
It stays silent where a document is deliberately partial: a tier can be marked
untimed and carry no bindings, a bound event with neither a rate nor stored
timing has no physical time, and trailing silence needs its own explicit item
rather than being assumed.
