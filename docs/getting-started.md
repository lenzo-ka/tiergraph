# Getting started

Install the package on Python 3.12 or later, then construct a graph directly or
run a checked build program.

```python
from tiergraph import (
    Graph,
    Item,
    NamespaceDeclaration,
    QualifiedName,
    Tier,
    TierDeclaration,
)

ns = "https://example.com/document"
tokens = QualifiedName(ns, "tokens")
graph = Graph(
    (NamespaceDeclaration("doc", ns),),
    (Tier(TierDeclaration(tokens, "Tokens"), (Item("first"), Item("second"))),),
    (),
)
```

Direct construction suits data already held in memory. The construction machine
records edits as a `Program` and returns an `AsBuilt` result; use it when the
sequence of declarations and additions is part of the input contract.

Next, use [selection and traversal](guide/selection-and-traversal.md), or write
the graph with [serialization](guide/serialization.md).
