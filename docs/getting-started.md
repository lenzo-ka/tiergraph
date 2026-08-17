# Getting started

This walkthrough goes from a first graph to a first fold. Install the package on
Python 3.12 or later, then follow the steps in order; each one builds on the
graph from the step before.

## Use the command line

The CLI reads graph documents from files or stdin. This validates a document,
canonicalizes it to compact JSON, and renders the result without intermediate
files:

    $ tiergraph validate score.json
    ok
    $ tiergraph convert score.json --to bytes | tiergraph render - -o score.dot

Machine programs use JSONL rather than the graph document format: the first
line is `{"machine_version":"1"}`, followed by one public opcode `to_data()`
object per line. Run one with `tiergraph run program.jsonl --to json`.

## A first graph

The smallest useful graph is one namespace and one tier with an item. `Graph`
validates everything when you construct it, so an ill-formed graph raises
immediately rather than failing later inside a view.

```python
from tiergraph import (
    Graph,
    Item,
    NamespaceDeclaration,
    QualifiedName,
    Tier,
    TierDeclaration,
)

ns = "https://example.com/pipeline"
steps = QualifiedName(ns, "steps")
graph = Graph(
    (NamespaceDeclaration("pl", ns),),
    (Tier(TierDeclaration(steps, "Steps"), (Item("fetch"),)),),
    (),
)
assert graph.tiers[0].items[0].durable_id == "fetch"
```

Each item here carries a durable id (`"fetch"`), a stable name that survives
edits. Items do not require one, but the ids make later references and output
readable.

## A graph with types, attributes, and a relation

A working graph usually declares more: a type for its items, a typed attribute,
and a relation between items. The next graph gives each step a decimal `cost`
and a `depends` relation, declared acyclic so it can be walked and folded
without a step cap.

```python
from decimal import Decimal
from typing import cast

from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValuation,
    AttributeValue,
    BipartiteRelationDeclaration,
    ChildCombination,
    FoldDeclaration,
    FoldTransition,
    Graph,
    Item,
    ItemRef,
    ItemSelector,
    ItemsSelector,
    NamespaceDeclaration,
    Node,
    QualifiedName,
    RelationInstance,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    Walk,
    WalkDirection,
    XsdType,
    dumps,
    loads,
    select,
)
from tiergraph.semiring import DECIMAL_TROPICAL

ns = "https://example.com/pipeline"
steps = QualifiedName(ns, "steps")
step_type = QualifiedName(ns, "step")
depends = QualifiedName(ns, "depends")
cost = QualifiedName(ns, "cost")


def step(identifier: str, weight: str) -> Item:
    return Item(identifier, (AttributeValue(cost, XsdType.DECIMAL, weight),))


refs = tuple(ItemRef(steps, index) for index in range(3))
graph = Graph(
    (NamespaceDeclaration("pl", ns),),
    (
        Tier(
            TierDeclaration(steps, "Steps"),
            (step("fetch", "2"), step("parse", "3"), step("render", "1")),
        ),
    ),
    (
        SimpleRelationDeclaration(QualifiedName(ns, "membership"), steps, step_type),
        BipartiteRelationDeclaration(depends, step_type, step_type, acyclic=True),
    ),
    (
        RelationInstance(depends, refs[0], refs[1]),
        RelationInstance(depends, refs[1], refs[2]),
    ),
    (AttributeDeclaration(cost, AttributeDomain.ITEM, XsdType.DECIMAL),),
)
```

The membership relation gives every item on the `steps` tier the type `step`,
and the two `depends` instances make a chain `fetch -> parse -> render`.

## Select the items

Selection turns parts of the graph into a canonical `NodeSet`. The helper below
turns selected nodes back into their durable ids for display.

```python
def label(node: Node) -> str:
    reference = node.reference
    assert isinstance(reference, ItemRef)
    return graph.tiers[0].items[reference.index].durable_id or ""


items = select(graph, (ItemsSelector(graph, steps),))
print([label(node) for node in items.nodes])
```

```text
['fetch', 'parse', 'render']
```

## Follow the relation

A `Walk` follows relation incidence transitively. Starting from `fetch` and
going forward reaches everything that depends on it.

```python
fetch = select(graph, (ItemSelector(graph, ItemRef(steps, 0)),))
reached = Walk(fetch, depends, WalkDirection.FORWARD).evaluate()
print([label(node) for node in reached.nodes.nodes])
```

```text
['parse', 'render']
```

## Run a first fold

A fold evaluates the dependency relation with a semiring. `DECIMAL_TROPICAL` is
min-plus arithmetic, so the fold returns the least total `cost` reachable from
the root. The `lift` reads each item's decimal value into the carrier, and the
`OR` transition treats a step's dependencies as alternatives to add up along a
path.

```python
fold = FoldDeclaration(
    "total-cost",
    graph,
    AttributeValuation("cost", cost, (steps,)),
    DECIMAL_TROPICAL,
    lambda value, _label: cast(Decimal, value),
    (FoldTransition(depends, ChildCombination.OR),),
    roots=(ItemRef(steps, 0),),
)
print(fold.run().value)
```

```text
6.0
```

The chain costs `2 + 3 + 1`, so the least total from `fetch` is `6.0`.

## Serialize and read back

`dumps` writes the one canonical JSON spelling, and `loads` validates and
rebuilds an equal graph.

```python
restored = loads(dumps(graph))
print(restored == graph)
```

```text
True
```

## Where to go next

- [Construction](guide/construction.md) records edits as a checked program.
- [Selection and traversal](guide/selection-and-traversal.md) covers selectors,
  set algebra, and ordered containment.
- [Folding](guide/folding.md) explains semirings, witnesses, and cost accounts.
- [Serialization](guide/serialization.md) describes the wire format and the DOT
  view.
