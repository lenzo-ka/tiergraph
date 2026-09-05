# Folding

A fold evaluates a finite dependency relation with a semiring. The graph and the
relation stay the same; the semiring decides what the fold computes. The same
dependency structure yields a least-cost path under min-plus arithmetic and a
count of paths under natural-number arithmetic, because addition and
multiplication mean different things in each. An acyclic relation has a finite
derivation set. A cyclic one does not, and is specified instead by the starred
fixpoint the algebra's `star` solves, which
[exactness](#exactness-is-a-declared-claim) returns to.

## Declaring a fold

A `FoldDeclaration` binds five things: the graph, an `AttributeValuation` that
reads one typed item attribute over an explicit set of tiers, the `Semiring`,
a `lift` that embeds each read value into the carrier, and the `FoldTransition`
tuple that gives each dependency relation an `AND` or `OR` meaning. `AND`
combines a node's children as joint requirements; `OR` combines them as
alternatives. Import concrete semirings from `tiergraph.semiring`.

Use selection when the payload is invariant across optimal paths: `SelectionSemiring` keeps the payload attached to the winning cost and resolves a cost tie by operand order. A payload that is the path itself can differ between tied optima and therefore wants accumulation with `PATH`. Selection requires the caller to declare tie invariance, and `tiergraph semirings` does not list it because its payload identity, payload combination, codecs, and declaration are supplied by Python callers rather than nameable by `tiergraph fold`.

Each transition must name a bipartite declaration, because a fold reads one
parent and one child per incidence. A declared relation of another kind is
refused and named for the kind it is, rather than being reported as undeclared
or quietly contributing no incidence.

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
which path won; `output_cap` limits how many witnesses it emits. The two are one
mechanism and are declared together: the order names the winner and the policy
answers the ties the order reports, so neither is accepted alone.

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

## Exactness is a declared claim

A fold evaluates shared structure once and reuses it. Whether the value that
comes out is the same as combining every derivation separately is a real
question with a real answer, and `exactness` is where a declaration answers it.
`FoldExactness` has four values. `DISTRIBUTIVE` says the value *is* that
combination. `APPROXIMATE` says it is a sound approximation of it, which is a
fact about the published result rather than a footnote about the algebra.
`STRUCTURAL` says no such combination exists, because a cycle makes the
derivation set infinite and the starred fixpoint equations are the
specification. `UNDECLARED` is the default.

`check_exactness()` demands the claim and returns a `FoldCertificate`. The two
ways of getting it wrong are answered differently on purpose: **omitting** the
claim is answered with the declaration to be made, and **asserting it falsely**
is answered with a semantic counterexample.

```python
from tiergraph import FoldExactness

undeclared = FoldDeclaration(
    "undeclared",
    graph,
    valuation,
    COUNTING,
    lambda value, _label: 1,
    transitions,
    roots=(refs[0],),
)
try:
    undeclared.check_exactness()
except ValueError as error:
    head, _, rest = str(error).partition(": ")
    print(head)
    print(rest.rsplit(". ", 1)[-1])

certificate = FoldDeclaration(
    "declared",
    graph,
    valuation,
    COUNTING,
    lambda value, _label: 1,
    transitions,
    roots=(refs[0],),
    exactness=FoldExactness.DISTRIBUTIVE,
).check_exactness()
print("value:", certificate.result.value)
print("derivations:", certificate.derivations)
print("compared:", certificate.compared)
```

```text
fold 'undeclared' exactness is UNDECLARED
Not declaring is not the same as declaring APPROXIMATE.
value: 2
derivations: 2
compared: True
```

Not declaring is not the same as declaring `APPROXIMATE`, and the refusal says
so rather than assuming the weaker claim on the caller's behalf.

The declared laws of a semiring are necessary and not sufficient here.
`COUNTING` checks distributivity exactly, and that is a statement about the
algebra; whether *this* fold equals the combination over its own derivations is
a statement about the fold. So the check is made two ways. It searches for a
counterexample to distributivity among a bounded set of probes taken from the
values this fold itself produces — a search, not a proof, since the probe set is
capped — and, when the whole enumeration fits in `derivation_budget`, it
enumerates the derivations with no sharing at all and compares. Re-running the
fold under a second algebra would not do: both algebras read the same
`FoldTransition` tuple, so a swap confirms whatever the declaration says
instead of testing it.

`FoldCertificate.compared` reports which of the two happened. When it is false
the enumeration did not fit and the claim stood on the law search alone — and a
law search that finds no counterexample has found no counterexample, not a
proof.

```python
print("budgeted:", undeclared.run().value)
capped = FoldDeclaration(
    "capped",
    graph,
    valuation,
    COUNTING,
    lambda value, _label: 1,
    transitions,
    roots=(refs[0],),
    exactness=FoldExactness.DISTRIBUTIVE,
).check_exactness(derivation_budget=1)
print("compared:", capped.compared)
```

```text
budgeted: 2
compared: False
```

Two refusals are settled by the declaration alone and never run the fold: a
`DISTRIBUTIVE` claim over an algebra that does not check every required law
exactly, which `tiergraph.semiring.inexact_laws` names, and a `STRUCTURAL`
claim over an algebra that declares no star warrant.

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

## From the command line

`tiergraph semirings` lists the algebras the `tiergraph fold` shell can name,
with their carrier boundaries and declared laws, and `tiergraph fold` runs the
declaration the flags describe over a stored document. The two folds above read
as:

```console
tiergraph fold plan.json \
  --name least-cost \
  --attribute-namespace https://example.com/plan --attribute-local cost \
  --tier https://example.com/plan tasks \
  --semiring decimal-tropical --lift value \
  --transition https://example.com/plan depends or \
  --root /items/durable/a --ranked --output-cap 4

tiergraph fold plan.json \
  --name path-count \
  --attribute-namespace https://example.com/plan --attribute-local cost \
  --tier https://example.com/plan tasks \
  --semiring counting --lift one \
  --transition https://example.com/plan depends or
```

The shell names one of two lifts: `value` embeds the read value, and `one`
embeds the semiring's multiplicative identity whatever the value is. A general
`lift`, a `witness_order`, and an index product are caller code and stay here in
the Python API; `--ranked` is the shell's route to witnesses, and it needs an
algebra that declares `multiply_preserves_witness_order`. `tiergraph.semiring`
also publishes `PATH_WITNESSES`, which the shell has no spelling for and a
Python fold names directly.

Ranked output is the other mechanism, and it takes neither a `witness_order` nor
a `TiePolicy`. It ranks by the semiring's own order and settles an equal-valued
tie by the canonical witness path, which is total wherever the document's item
labels are distinct; every tied witness is kept, in that order, up to
`output_cap`. A `TiePolicy` alongside `ranked_output` is refused rather than
accepted and ignored, because nothing would ever read it.
