# Construction

There are three ways to make a graph. Most newcomers should start with the
ergonomic `tiergraph.build` builder used in [Getting started](../getting-started.md).
Direct constructors build an immutable value in one step, which suits data
already held in memory. The build machine records an ordered stream of edits as
a `Program`, which suits input where the sequence of declarations and additions
is itself part of the contract, such as a deserialized or generated document.

## The ergonomic builder

Create a mutable `Document` with `tiergraph.build.document()`, add ordered tiers
and relations, then call `build()` to cross the immutable graph-validation
boundary. `Document.attributes()` declares several typed attributes at once;
`Document.tier()` returns a `TierHandle` whose `ref()`, `start()`, `end()`,
`before()`, and `after()` methods provide checked item and boundary anchors.
`Document.relation()` accepts those anchors using concise builder notation.
The `item()` helper describes an item before declared attribute types are
lowered. Invalid builder notation raises `tiergraph.build.BuilderError`, while
`build()` may expose graph-wide validation errors. See the
[generated API reference](../reference/api.md#tiergraphbuild) for the supported
secondary surface.

## Direct construction

Pass namespaces, tiers, relation declarations, and the rest to `Graph`. Declare
a name before referring to it, because `Graph` performs the cross-object
validation when it is constructed. Use this lower-level path when the complete
immutable content is already available to the caller.

## The build machine

A `Program` holds a tuple of opcodes. Each primitive opcode is one checked state
transition: `DeclareNamespace`, `DeclareTier`, `DeclareAttribute`,
`DeclareRelation`, `AddItem`, `AttachValue`, `Relate`, `PromoteItem`, and
`PromotePosition`. `Repeat` runs a block a fixed number of times; it carries a
declared upper bound, so a serialized program cannot request unbounded
expansion.

`Program.unroll()` returns `AsBuilt`, including promoted references; `execute()`
is the convenience operation that returns its `Graph`. `AsBuilt` pairs the graph
with the flattened primitive trace, and re-executes that trace to confirm it
builds the graph it claims.

```python
from tiergraph import (
    AddItem,
    AttachValue,
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    DeclareAttribute,
    DeclareNamespace,
    DeclareRelation,
    DeclareTier,
    ExecutionError,
    ItemRef,
    NamespaceDeclaration,
    Program,
    QualifiedName,
    Repeat,
    SimpleRelationDeclaration,
    TierDeclaration,
    XsdType,
    execute,
)

ns = "https://example.com/doc"
words = QualifiedName(ns, "words")
word_type = QualifiedName(ns, "word")
weight = QualifiedName(ns, "weight")

program = Program(
    (
        DeclareNamespace(NamespaceDeclaration("doc", ns)),
        DeclareTier(TierDeclaration(words, "Words")),
        DeclareAttribute(
            AttributeDeclaration(weight, AttributeDomain.ITEM, XsdType.INTEGER)
        ),
        DeclareRelation(
            SimpleRelationDeclaration(QualifiedName(ns, "membership"), words, word_type)
        ),
        Repeat(3, (AddItem(words),)),
        AttachValue(
            AttributeDomain.ITEM,
            ItemRef(words, 0),
            AttributeValue(weight, XsdType.INTEGER, "5"),
        ),
    )
)

built = program.unroll()
print("as-built type:", type(built).__name__)
print("primitive opcodes:", len(built.trace))
print("items:", len(built.graph.tiers[0].items))

graph = execute(built.trace)
print("execute equals unroll graph:", graph == built.graph)
```

```text
as-built type: AsBuilt
primitive opcodes: 8
items: 3
execute equals unroll graph: True
```

The program has six opcodes, but the trace has eight: `unroll()` expands the
`Repeat(3, ...)` into three `AddItem` transitions, then executes them. The
six-opcode source and the eight-opcode trace describe the same graph.

## Refusal names its location

`execute` applies opcodes in order and validates the whole graph after each one.
A refused opcode raises `ExecutionError` naming the failing opcode's position,
so a bad edit points at itself rather than surfacing later.

```python
bad = Program((AddItem(QualifiedName(ns, "missing")),))
try:
    bad.unroll()
except ExecutionError as error:
    print("refused at opcode", str(error).split(" ", 2)[1])
```

```text
refused at opcode 0
```

The tier `missing` was never declared, so the `AddItem` at position 0 cannot
make its transition. Because each step is validated in full, a program either
produces a valid graph or stops at the exact opcode that broke.

## What a rewrite did to what it rewrote

Every edit here outputs a new graph, so any two graphs can be read as one
rewrite's before and after. What that rewrite did to the graph it read is a
real question with a real answer, and `RewriteEffect` is where a declaration
answers it. `DECORATE` says the result added and took nothing back: every
structure the source asserts stands in the result at the same coordinate,
carrying everything it carried. `REVISE` says every structure still stands but
some value stands in place of another. `COLLAPSE` says some structure has no
counterpart at all. `UNDECLARED` is the default.

"Tiers can only decorate" is not a law of this kernel, and the API does not
state it as one. Nothing stops a new graph from standing in any relation to the
old one. What can be said, and held to account, is that a *particular* rewrite
decorated. `check_effect()` demands that claim and returns a
`RewriteCertificate`. The two ways of getting it wrong are answered differently
on purpose: **omitting** the claim is answered with the declaration to be made,
and **asserting it falsely** is answered with a semantic counterexample naming
the structure and what happened to it.

```python
from tiergraph import Graph, RewriteDeclaration, RewriteEffect, Tier

grown = execute((*built.trace, AddItem(words)))
certificate = RewriteDeclaration(
    "grow", graph, grown, RewriteEffect.DECORATE
).check_effect()
print("subjects examined:", certificate.subjects)
print("disturbances:", certificate.disturbances)

stripped = Graph(
    graph.namespaces,
    (Tier(graph.tiers[0].declaration, graph.tiers[0].items[:2]),),
    graph.relation_declarations,
    graph.relations,
    graph.attribute_declarations,
)
try:
    RewriteDeclaration("drop", graph, stripped, RewriteEffect.DECORATE).check_effect()
except ValueError as error:
    print(str(error).split(". ")[0] + ".")
```

```text
subjects examined: 8
disturbances: 0
rewrite 'drop' declares DECORATE, but item '{https://example.com/doc}words'[2] has no counterpart in the result.
```

`subjects` is the honest part of the certificate. It counts the structures the
source asserts, every one of which was examined, so a claim over a graph that
asserts almost nothing cannot be read as a strong one.

A discharged `DECORATE` licenses one thing. Every reading taken over the source
is still a correct reading of the result without re-reading it: an item's
attributes, a position's values, a relation's endpoints, whatever a reference
resolved to. It does not license any reading that counts, quantifies over
everything, or turns on absence, such as a tier's extent, the canonical bytes,
or the construction fingerprint. Decoration adds, so those must be taken again.
Put shortly, a positive property proved of the source transfers to the result
and a negative or counting one does not.

The effect belongs to the pair of graphs rather than to the operation, and
`AddItem` shows why. It decorates against the graph above. Against a graph
carrying a value at the boundary anchored to its tier's last edge it does not,
because growing the tier moves that edge and leaves the value's old coordinate
empty. Neither the opcode nor the anchor is at fault; the claim is declared per
rewrite because a pair of graphs is the only place it can be true.

## Error boundary

Invalid declarations or graph content at a graph-construction boundary raise
`GraphValidationError`, a subclass of `ValueError`. This includes validation
performed while decoding a serialized document into a graph. Once a `Graph` is
valid, invalid arguments to lookup and mutation-style methods such as
`resolve_item()`, `positions()`, and `promote_item()` deliberately remain plain
`ValueError`; wrong Python argument kinds may raise `TypeError`. The build
machine reports a refused opcode as `ExecutionError` and retains its cause.
