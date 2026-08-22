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
