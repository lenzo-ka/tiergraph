# Getting started

This page starts from an annotation you might already have, a Praat TextGrid,
then builds a small graph from scratch and runs the command line on it. The
package requires Python 3.12 or later; install it with
`python -m pip install tiergraph`.

## Read a TextGrid, query it, write it back

`from_textgrid` turns a TextGrid into a graph and returns it with a profile that
names its tiers. Each TextGrid tier becomes a tier of the same name, each
interval an item whose `value` attribute holds the label, and each later
interval tier is related to each earlier one by a declared containment relation.
Selecting the word `cat` and walking that relation reaches its phones.

```python
from dataclasses import replace
from pathlib import Path

from tiergraph import (
    AttributeValue,
    ItemRef,
    ItemsSelector,
    NodeSet,
    QualifiedName,
    Walk,
    WalkDirection,
    WhereSelector,
    evaluate_selection,
)
from tiergraph.predicate import Cell, Equals
from tiergraph.textgrid import from_textgrid, to_textgrid

TEXTGRID = """File type = "ooTextFile"
Object class = "TextGrid"

xmin = 0
xmax = 0.9
tiers? <exists>
size = 2
item []:
    item [1]:
        class = "IntervalTier"
        name = "words"
        xmin = 0
        xmax = 0.9
        intervals: size = 2
        intervals [1]:
            xmin = 0
            xmax = 0.3
            text = "a"
        intervals [2]:
            xmin = 0.3
            xmax = 0.9
            text = "cat"
    item [2]:
        class = "IntervalTier"
        name = "phones"
        xmin = 0
        xmax = 0.9
        intervals: size = 4
        intervals [1]:
            xmin = 0
            xmax = 0.3
            text = "AH"
        intervals [2]:
            xmin = 0.3
            xmax = 0.5
            text = "K"
        intervals [3]:
            xmin = 0.5
            xmax = 0.7
            text = "AE"
        intervals [4]:
            xmin = 0.7
            xmax = 0.9
            text = "T"
"""

read = from_textgrid(TEXTGRID)
textgrid = "urn:tiergraph:textgrid"
value = QualifiedName(textgrid, "value")


def labels(nodes: NodeSet) -> list[str]:
    result: list[str] = []
    for node in nodes.nodes:
        reference = node.reference
        assert isinstance(reference, ItemRef)
        tier = next(t for t in read.graph.tiers if t.declaration.name == reference.tier)
        result.extend(
            attribute.lexical
            for attribute in tier.items[reference.index].attributes
            if isinstance(attribute, AttributeValue) and attribute.name == value
        )
    return result


cat = evaluate_selection(
    read.graph,
    WhereSelector(
        ItemsSelector(QualifiedName(textgrid, "words")), Equals(Cell(value), ("cat",))
    ),
)
phones = Walk(cat, QualifiedName(textgrid, "containment-0-1"), WalkDirection.FORWARD)
print(labels(cat), "->", labels(phones.evaluate().nodes))

written = to_textgrid(
    read.graph, replace(read.profile, clock_face="physical"), clock=read.clock
)
print(from_textgrid(written).graph == read.graph)
```

```text
['cat'] -> ['K', 'AE', 'T']
True
```

`to_textgrid` writes the tiers the profile names. Setting
`clock_face="physical"` and passing the clock from the read writes times in
seconds, so reading the output gives back an equal graph. Without them it writes
structural boundary indexes instead.
[Interchange with annotation formats](guide/annotation-formats.md) describes
the graph a TextGrid becomes and both clock faces.

## Build a graph

The `tiergraph.build` builder declares tiers, attributes, and relations with
little ceremony. This graph is a three-step pipeline. Each step has a decimal
`cost`, and a `depends` relation, declared acyclic, chains them
`fetch -> parse -> render`. The item ids double as display labels here.

```python
from decimal import Decimal
from typing import cast

from tiergraph import (
    AttributeValuation,
    ChildCombination,
    FoldDeclaration,
    FoldTransition,
    ItemSelector,
    Node,
    dumps,
    loads,
)
from tiergraph.build import document, item
from tiergraph.semiring import DECIMAL_TROPICAL

pipeline = document("https://example.com/pipeline", prefix="pl")
pipeline.attributes({"cost": "decimal"})
steps = pipeline.tier(
    "steps",
    (item("fetch", cost="2"), item("parse", cost="3"), item("render", cost="1")),
    item_type="step",
    membership="membership",
)
depends = pipeline.link("depends", steps, steps, ((0, 1), (1, 2)), acyclic=True)
graph = pipeline.build()
cost = pipeline.qname("cost")
```

`item_type` and `membership` give every item on the tier the type `step`, and
`link` declares a relation between items of those types and adds its pairs by
item index. `build()` returns an immutable `Graph`, validated as a whole.

## Select the items

Selection turns parts of the graph into a canonical `NodeSet`. The helper below
turns selected nodes back into their ids for display.

```python
def label(node: Node) -> str:
    reference = node.reference
    assert isinstance(reference, ItemRef)
    return graph.tiers[0].items[reference.index].durable_id or ""


items = evaluate_selection(graph, ItemsSelector(steps.name))
print([label(node) for node in items.nodes])
```

```text
['fetch', 'parse', 'render']
```

## Follow the relation

A `Walk` follows relation incidence transitively. Starting from `fetch` and
going forward reaches everything that depends on it.

```python
fetch = evaluate_selection(graph, ItemSelector(steps.ref(0)))
reached = Walk(fetch, depends.name, WalkDirection.FORWARD).evaluate()
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
    AttributeValuation("cost", cost, (steps.name,)),
    DECIMAL_TROPICAL,
    lambda value, _label: cast(Decimal, value),
    (FoldTransition(depends.name, ChildCombination.OR),),
    roots=(steps.ref(0),),
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

## Use the command line

Save the document with
`Path("pipeline.json").write_text(dumps(graph), encoding="utf-8")`, and the
`tiergraph` command can work with it. It reads a file, or `-` for stdin:

```console
$ tiergraph validate pipeline.json
ok
$ tiergraph inspect pipeline.json
format version: 0.3.0
namespaces: 1
tiers: 1
items: 3
relation declarations: 2
binary relation instances: 2
polyadic relation instances: 0
attribute declarations: 1
populated position values: 0
document attributes: 0
tier: {https://example.com/pipeline}steps | steps | items=3 | attributes=0
relation: {https://example.com/pipeline}depends | kind=bipartite
relation: {https://example.com/pipeline}membership | kind=simple
$ tiergraph match pipeline.json --prefix pl --pattern '{cost>=2} {cost>=2}' \
    --ordering '{"order":"tier","tier":{"namespace":"https://example.com/pipeline","local_name":"steps"}}' \
    count
{
  "count": 1
}
```

`tiergraph select pipeline.json --where 'cost>=2' --prefix pl` prints the
selected nodes as JSON, and `tiergraph fold` runs a fold declared by its flags;
[Folding](guide/folding.md#from-the-command-line) shows one.
`tiergraph convert pipeline.json --to bytes | tiergraph render - -o pipeline.dot`
canonicalizes the document and renders it as Graphviz DOT.

Construction programs use JSON Lines rather than the graph document format: the
first line is `{"machine_version":"1"}`, followed by one public opcode
`to_data()` object per line. Run one with `tiergraph run program.jsonl --to json`.

## Where to go next

- [What tiergraph is for](what-tiergraph-is-for.md) places it among related
  tools.
- [Concepts](concepts.md) describes the data model.
- [Construction](guide/construction.md) covers the builder, direct constructors,
  and construction programs.
- [Editing graphs](guide/editing.md) covers transactions, journals, patches,
  clock-aware edits, bulk operations, distance, and cleanup.
- [Selection and traversal](guide/selection-and-traversal.md) covers selectors,
  set algebra, walks, and ordered containment.
- [Predicates and offset joins](guide/predicates-and-offset-joins.md) adds
  value predicates, related-item quantifiers, and interval joins.
- [Sequence patterns](guide/patterns.md) matches regular patterns over tiers,
  declared orders, and lattices.
- [Folding](guide/folding.md) explains semirings, witnesses, path plans, and
  cost accounts; [Grammars](guide/grammars.md) parses token input with a
  synchronous grammar.
- [Work budgets](guide/work-budgets.md) bounds the work a query may do.
- [Timing](guide/timing.md) attaches physical times to boundaries.
- [Interchange with annotation formats](guide/annotation-formats.md),
  [Span views](guide/span-views.md), and
  [Serialization](guide/serialization.md) cover reading and writing.
- [Profiles](guide/profiles.md) checks interpretations a graph can carry.
- [Advanced: recognize and act](guide/recognize-and-act.md) chains a fold into
  actions.
