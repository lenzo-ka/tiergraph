# Span views

A span view projects a resolved segmentation without changing the graph wire
format. The graph contains one ordered base tier whose item surfaces reconstruct
the input text, one or more span tiers, and a declared bipartite coverage
relation. Each coverage instance points from a base-tier `ItemRef` on the left
to a span-tier `ItemRef` on the right. Span items may carry value and score
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

A span is a live rule over coverage membership, not an origin-plus-extent
snapshot. The kernel graph stores coverage and no extent at all; the view
derives `start` and `end` for projection and rendering. (Origin plus extent
governs intervals, not kernel spans.) Coverage must be contiguous. If an item
appears inside a span's range without a corresponding coverage edge, projection
refuses the hole, forcing the caller to decide how membership changes instead
of silently absorbing the item or splitting the span.
