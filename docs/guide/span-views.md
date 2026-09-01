# Span views

A span view projects a resolved segmentation without changing the graph wire
format. The graph contains one ordered base tier whose item surfaces reconstruct
the input text, one or more span tiers, and a declared bipartite coverage
relation. Each coverage instance points to a span-tier `ItemRef` on the right; on
the left it carries either a base-tier `ItemRef` or a base-tier boundary. A span
covered only by a boundary projects as a zero-width span at that position, which
is how an anchor with no extent is stored. Span items may carry value and score
attributes as canonical lexical strings.

Construct a `tiergraph.spanview.SpanViewProfile` with the qualified names of
those tiers, relations, and attributes, then call `span_view`. The projection
derives half-open base-item ranges solely from coverage edges and uses
`StructuralPathProfile` paths as stable references. An optional base-item
character-offset attribute exposes external offsets; intrinsic display offsets
always come from cumulative surface lengths.

An optional bipartite alternatives relation points from a selected span to
candidate items carrying the configured value and score attributes. Emitters
include those candidates only when requested and rank them by descending
numeric score, then ascending path. JSON, JSON Lines, text, HTML, and the
companion `tiergraph_dot.dumps_spans` renderer all consume the same projection.

Both roles must name bipartite declarations, and naming anything else is
refused when the profile is validated. A span is an interval over the base
tier, so each fact this view reads is one left endpoint against one span item;
a polyadic instance carries two ordered sides with no such pairing. Refusing is
the point: reading only the bipartite collection would report a partial
segmentation as a complete one.

A span is a live rule over coverage membership, not an origin-plus-extent
snapshot. The kernel graph stores coverage and no extent at all; the view
derives `start` and `end` for projection and rendering. (Origin plus extent
governs intervals, not kernel spans.) Coverage must be contiguous. If an item
appears inside a span's range without a corresponding coverage edge, projection
refuses the hole, forcing the caller to decide how membership changes instead
of silently absorbing the item or splitting the span.
