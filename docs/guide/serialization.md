# Serialization

`to_data()` returns the strict-JSON data model. `dumps()` returns canonical JSON
text, `dump_bytes()` returns its UTF-8 bytes, and `loads()` validates and rebuilds
a graph. The codec records `FORMAT_VERSION`; see [Format](../format.md) for the
interchange contract.

The separate `tiergraph_dot` package exposes `dumps(graph, clock=None,
include_empty_tiers=False)`. It renders a read-only Graphviz DOT view through the
public tiergraph API. Output order follows graph and relation order. A clock
profile must belong to the exact graph instance being rendered.
