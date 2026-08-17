# Folding

A fold evaluates a finite acyclic dependency relation with a semiring. The graph
and the relation stay the same; the semiring decides what the fold computes. The
same dependency structure yields a least-cost path under min-plus arithmetic and
a count of paths under natural-number arithmetic, because addition and
multiplication mean different things in each.

## Declaring a fold

A `FoldDeclaration` binds five things: the graph, an `AttributeValuation` that
reads one typed item attribute over an explicit set of tiers, the `Semiring`,
a `lift` that embeds each read value into the carrier, and the `FoldTransition`
tuple that gives each dependency relation an `AND` or `OR` meaning. `AND`
combines a node's children as joint requirements; `OR` combines them as
alternatives. Import concrete semirings from `tiergraph.semiring`.

The example graph is a diamond: `a` depends on `b` and `c`, both of which depend
on `d`. Each task has a decimal `cost`.

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
    NamespaceDeclaration,
    QualifiedName,
    RelationInstance,
    SimpleRelationDeclaration,
    TiePolicy,
    Tier,
    TierDeclaration,
    XsdType,
)
from tiergraph.semiring import COUNTING, DECIMAL_TROPICAL

ns = "https://example.com/plan"
tasks = QualifiedName(ns, "tasks")
task_type = QualifiedName(ns, "task")
depends = QualifiedName(ns, "depends")
cost = QualifiedName(ns, "cost")


def task(identifier: str, weight: str) -> Item:
    return Item(identifier, (AttributeValue(cost, XsdType.DECIMAL, weight),))


refs = tuple(ItemRef(tasks, index) for index in range(4))
graph = Graph(
    (NamespaceDeclaration("plan", ns),),
    (
        Tier(
            TierDeclaration(tasks, "Tasks"),
            (task("a", "1"), task("b", "2"), task("c", "5"), task("d", "1")),
        ),
    ),
    (
        SimpleRelationDeclaration(QualifiedName(ns, "membership"), tasks, task_type),
        BipartiteRelationDeclaration(depends, task_type, task_type, acyclic=True),
    ),
    (
        RelationInstance(depends, refs[0], refs[1]),
        RelationInstance(depends, refs[0], refs[2]),
        RelationInstance(depends, refs[1], refs[3]),
        RelationInstance(depends, refs[2], refs[3]),
    ),
    (AttributeDeclaration(cost, AttributeDomain.ITEM, XsdType.DECIMAL),),
)

valuation = AttributeValuation("cost", cost, (tasks,))
transitions = (FoldTransition(depends, ChildCombination.OR),)
```

## The semiring decides the answer

`DECIMAL_TROPICAL` is exact min-plus arithmetic. Its addition takes the smaller
of two carriers and its multiplication adds them, so an `OR` fold returns the
least-cost path. A witness order and a `TiePolicy` make the fold also report
which path won; `output_cap` limits how many witnesses it emits.

```python
least_cost = FoldDeclaration(
    "least-cost",
    graph,
    valuation,
    DECIMAL_TROPICAL,
    lambda value, _label: cast(Decimal, value),
    transitions,
    roots=(refs[0],),
    witness_order=lambda left, right: (left > right) - (left < right),
    tie_policy=TiePolicy.CHOOSE_FIRST,
    output_cap=4,
)
result = least_cost.run()
print("least cost:", result.value)
print("witness:", result.provenance)
print("truncated:", result.truncated)
print("carrier ops:", result.cost.carrier_work)
```

```text
least cost: 4.0
witness: (('a', 'b', 'd'),)
truncated: False
carrier ops: 6
```

The path `a -> b -> d` costs `1 + 2 + 1 = 4`, less than `a -> c -> d` at `7`.
`FoldResult` keeps these concerns separate: `value` is the carrier answer,
`provenance` is the witness, `truncated` reports whether more witnesses existed
than `output_cap` allowed, and `cost` is a measured account of the work done.

Swapping in `COUNTING`, whose addition is ordinary `+` and whose lift returns
`1` for every node, counts how many paths reach the root instead.

```python
path_count = FoldDeclaration(
    "path-count",
    graph,
    valuation,
    COUNTING,
    lambda value, _label: 1,
    transitions,
    roots=(refs[0],),
)
print("paths:", path_count.run().value)
```

```text
paths: 2
```

Nothing about the graph changed; only the algebra did.

## Refusals

A fold validates its declaration before it can run. The type of the valued
attribute and the declared laws of the semiring must be compatible: an exact
semiring may not read an `xsd:double` attribute, because IEEE addition is not
associative and the exactness claim would be false.

```python
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
    NamespaceDeclaration,
    QualifiedName,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    XsdType,
)
from tiergraph.semiring import DECIMAL_TROPICAL

ns = "https://example.com/plan"
signals = QualifiedName(ns, "signals")
signal_type = QualifiedName(ns, "signal")
level = QualifiedName(ns, "level")
follows = QualifiedName(ns, "follows")
approx = Graph(
    (NamespaceDeclaration("plan", ns),),
    (
        Tier(
            TierDeclaration(signals, "Signals"),
            (Item("a", (AttributeValue(level, XsdType.DOUBLE, "1.5"),)),),
        ),
    ),
    (
        SimpleRelationDeclaration(
            QualifiedName(ns, "signal-membership"), signals, signal_type
        ),
        BipartiteRelationDeclaration(follows, signal_type, signal_type, acyclic=True),
    ),
    (),
    (AttributeDeclaration(level, AttributeDomain.ITEM, XsdType.DOUBLE),),
)

try:
    FoldDeclaration(
        "exact-over-double",
        approx,
        AttributeValuation("level", level, (signals,)),
        DECIMAL_TROPICAL,
        lambda value, _label: value,
        (FoldTransition(follows, ChildCombination.OR),),
        roots=(ItemRef(signals, 0),),
    )
except ValueError as error:
    print(str(error).split(",")[0])
```

```text
fold 'exact-over-double' valuation 'level' reads xsd:double attribute '{https://example.com/plan}level'
```

Use `TROPICAL` or `ARCTIC`, whose associativity check is approximate, for
`xsd:double` values. The refusal is a declaration-time guard, so a fold that
runs has already been checked for this mismatch.
