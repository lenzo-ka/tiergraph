# Serialization

tiergraph writes one canonical JSON spelling for a graph, so two equal graphs
produce identical bytes and a document has a single normal form. The codec is
strict: it validates structure and every reference on read, and it refuses a
document whose `format_version` it does not implement rather than migrating it
silently.

Reading refuses with a `Refusal` carrying its stage in the
[refusal order](../format.md#refusal-order), and a document that decodes but
breaks a graph rule refuses with `GraphValidationError`, the last rank of that
order. [Construction](construction.md#error-boundary) describes the whole error
boundary.

## JSON round-trip

`dumps` returns the canonical JSON text, `dump_bytes` returns its UTF-8 bytes,
and `loads` validates and rebuilds a graph. `to_data` returns the same content
as a strict-JSON data structure without serializing it to text. The document is
tagged with `FORMAT_VERSION`; see [Format](../format.md) for the interchange
contract.

```python
from tiergraph import (
    FORMAT_VERSION,
    Graph,
    Item,
    NamespaceDeclaration,
    QualifiedName,
    Tier,
    TierDeclaration,
    dump_bytes,
    dumps,
    loads,
)

ns = "https://example.com/score"
events = QualifiedName(ns, "events")
graph = Graph(
    (NamespaceDeclaration("score", ns),),
    (Tier(TierDeclaration(events, "Events"), (Item("opening"),)),),
    (),
)

text = dumps(graph)
print(text, end="")
print("format version:", FORMAT_VERSION)
print("byte length:", len(dump_bytes(graph)))
print("round-trip equal:", loads(text) == graph)
```

```text
{
  "format_version": "0.3.0",
  "graph": {
    "namespaces": [
      {
        "namespace": "https://example.com/score",
        "prefix": "score"
      }
    ],
    "tiers": [
      {
        "declaration": {
          "long_name": "Events",
          "name": "score:events"
        },
        "items": [
          {
            "durable_id": "opening"
          }
        ]
      }
    ]
  }
}
format version: 0.3.0
byte length: 397
round-trip equal: True
```

The keys are sorted; empty collections and null fields are omitted, except that
an explicit empty relation-side `tiers` restriction is preserved. Qualified
names use the document's namespace prefixes. The same graph always produces
these exact bytes. A graph carrying a string that UTF-8 cannot encode is
refused by the writer instead of being emitted as an unreadable document. That
is the one condition the writer answers, so `dumps` returning is not on its own
a promise that `loads` accepts what it wrote: the reader ranks conditions the
writer never asks, and refuses a document that meets one of them. The size
budget is one such condition, and it is reachable from a graph the constructor
accepts: canonical text running past `MAX_DOCUMENT_BYTES` is written in full
and refused at `ENVELOPE` on the way back, because the offending size belongs
to the text rather than to any member a constructor could have bounded.

Collections whose order carries no graph meaning, such as namespaces and
declarations, are sorted before writing, so two graphs that differ only in the
order those were supplied compare equal and serialize to the same bytes. Tier
order, item order, relation instance order, and polyadic endpoint order are
data and are written as supplied. The [format notes](../format.md) list which
collections are which.

## The DOT view

The companion `tiergraph_dot` package renders a read-only Graphviz DOT view
through the public API. It ships in the same distribution as the kernel.
`dumps(graph, *, clock=None, presentation=None, binding=None,
include_empty_tiers=False)` follows graph and relation order, so its output is
byte-stable. A clock profile passed here must be the one built for this exact
graph instance. `presentation` customizes labels and relation styling; `binding`
places non-clock items when rendering a structural clock built with
`ClockProfile.from_boundary_values`.

```python
import tiergraph_dot
from tiergraph import (
    Graph,
    Item,
    NamespaceDeclaration,
    QualifiedName,
    Tier,
    TierDeclaration,
)

ns = "https://example.com/score"
events = QualifiedName(ns, "events")
graph = Graph(
    (NamespaceDeclaration("score", ns),),
    (Tier(TierDeclaration(events, "Events"), (Item("opening"),)),),
    (),
)
print(tiergraph_dot.dumps(graph), end="")
```

```text
digraph tiergraph {
  graph [rankdir=TB, newrank=true, ranksep="0.62 equally", nodesep=0.28, splines=line];
  node [fontname="Helvetica"];
  edge [fontname="Helvetica", fontsize=9];

  subgraph tier_0 {
    rank=same;
    tier_label_0 [shape=plaintext, label="events"];
    guide_0_0 [shape=point, width=0.01, label="", group="tier_0_0", style=invis];
    item_0_0 [shape=box, group="tier_0_0", label="opening"];
    guide_0_1 [shape=point, width=0.01, label="", group="tier_0_1", style=invis];
    guide_0_0 -> guide_0_1 [style=invis, weight=100];
    item_0_0 -> guide_0_1 [xlabel="extent", color="#777777", style=dashed, arrowhead=tee, arrowsize=0.6, fontsize=8, constraint=false];
  }

  // The score brace joins rows in tier order.
}
```

Passing a `ClockProfile` as `clock` lays timed tiers out against the refined
clock spine; see [Timing](timing.md). The renderer treats attribute names and
values as data and assigns them no domain meaning.
