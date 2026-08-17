# tiergraph

tiergraph represents ordered tiers, typed items, declared relations, and views
derived from that structure. It provides an immutable graph kernel, a checked
construction machine, selection and traversal operations, folds over dependency
graphs, coordinate actions, timing profiles, and a versioned JSON format.

The package requires Python 3.12 or later.

```console
python -m pip install tiergraph
```

## First graph

```python
from tiergraph import (
    Graph,
    Item,
    NamespaceDeclaration,
    QualifiedName,
    Tier,
    TierDeclaration,
)

namespace = "https://example.com/score"
events = QualifiedName(namespace, "events")
graph = Graph(
    (NamespaceDeclaration("score", namespace),),
    (Tier(TierDeclaration(events, "Events"), (Item("opening"),)),),
    (),
)
assert graph.tiers[0].items[0].durable_id == "opening"
```

`Graph` validates names, declarations, endpoints, attributes, and graph-wide
constraints when it is constructed. Invalid graphs fail before they can enter a
selection, fold, or serializer.

## Documentation

Start with the [documentation map](docs/README.md), then read
[concepts](docs/concepts.md) and [getting started](docs/getting-started.md).
The [API reference](docs/reference/api.md) covers every top-level export. The
[CLI reference](docs/reference/cli.md) is generated from the parser.

The companion `tiergraph_dot` import package renders a graph as deterministic
Graphviz DOT. It ships in the same distribution:

```python
import tiergraph_dot

dot = tiergraph_dot.dumps(graph)
```

The project is alpha software. Wire and machine format versions are explicit;
the Python API may still change before a stable release.
