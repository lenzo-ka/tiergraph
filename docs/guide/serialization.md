# Serialization

tiergraph writes one canonical JSON spelling for a graph, so two equal graphs
produce identical bytes and a document has a single normal form. The codec is
strict: it validates structure and every reference on read, and it refuses a
document whose `format_version` it does not implement rather than migrating it
silently.

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
  "format_version": "5",
  "graph": {
    "attribute_declarations": [],
    "attributes": [],
    "namespaces": [
      {
        "namespace": "https://example.com/score",
        "prefix": "score"
      }
    ],
    "position_values": [],
    "relation_declarations": [],
    "relations": [],
    "tiers": [
      {
        "attributes": [],
        "declaration": {
          "long_name": "Events",
          "name": {
            "local_name": "events",
            "namespace": "https://example.com/score"
          }
        },
        "items": [
          {
            "attributes": [],
            "durable_id": "opening"
          }
        ]
      }
    ]
  }
}
format version: 5
byte length: 674
round-trip equal: True
```

The keys are sorted and every collection is present, including the empty ones.
That is what makes the form canonical: the same graph always produces these
exact bytes. `loads(dumps(graph))` reconstructs a graph equal to the original.

Qualified names, declaration order, tier order, item order, relation endpoint
order, and boundary indexes are all data. A reader must not infer a relation's
meaning from its name or collapse an ordered relation into an unordered set.

## The DOT view

The companion `tiergraph_dot` package renders a read-only Graphviz DOT view
through the public API. It ships in the same distribution as the kernel.
`dumps(graph, clock=None, include_empty_tiers=False)` follows graph and relation
order, so its output is byte-stable. A clock profile passed here must be the one
built for this exact graph instance.

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
