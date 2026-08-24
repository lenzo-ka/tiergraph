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

## Error boundary

Invalid declarations or graph content at a graph-construction boundary raise
`GraphValidationError`, a subclass of `ValueError`. This includes validation
performed while decoding a serialized document into a graph. Once a `Graph` is
valid, invalid arguments to lookup and mutation-style methods such as
`resolve_item()`, `positions()`, and `promote_item()` deliberately remain plain
`ValueError`; wrong Python argument kinds may raise `TypeError`. The build
machine reports a refused opcode as `ExecutionError` and retains its cause.
