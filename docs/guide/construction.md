# Construction

This page covers the ways to author a graph, and the way to make a graph from a
graph you already hold. Most newcomers should start with the ergonomic
`tiergraph.build` builder used in [Getting started](../getting-started.md).
Direct constructors build an immutable value in one step, which suits data
already held in memory. The build machine records an ordered stream of edits as
a `Program`, which suits input where the sequence of declarations and additions
is itself part of the contract, such as a deserialized or generated document.
A graph also arrives ready-made from `tiergraph.loads`, covered in
[Serialization](serialization.md), and from `json_value_graph`, covered in
[Profiles](profiles.md).

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

## Editing an existing graph

`Graph` and `GraphEditor` answer one operation set over two carriers. A frozen
graph answers `declare()`, `set_attribute()`, `remove_attribute()`,
`insert_item()`, `remove_item()`, `move_item()`, `swap_items()`,
`add_relation()`, and `remove_relation()` by returning a new graph. The editor
`Graph.edit()` returns answers the same nine by changing itself, and
`GraphEditor.freeze()` runs the one validation at the end. Whether an operation
rewrites or mutates follows from the carrier a caller holds, never from an
argument, so nothing has to decide at run time which kind of object it has.

Setting a value replaces any value of the same name on that carrier. The value's
own declaration says which domain it belongs to, so a caller spells the place
and not the domain: `None` is the document, a qualified name is a tier or a
relation declaration, an item or durable reference is an item, a structural or
durable boundary reference is a boundary, and an index or a durable id is a
relation instance.

Structural operations keep the graph's own references denoting what they
denoted. Item coordinates stored inside the graph move with their items, and
durable identifiers resolve again at freeze. A stored boundary value addressed
by coordinate moves when the edit leaves its boundary exactly one image and
refuses the edit when it does not, because a bare coordinate has no anchor to
follow; `promote_boundary()` gives it one. A removal is refused while the graph
still references the item, and a refused operation writes nothing.

```python
from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
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

edit_ns = "https://example.com/plan"
edit_tier = QualifiedName(edit_ns, "steps")
edit_score = QualifiedName(edit_ns, "score")

edit_graph = Graph(
    (NamespaceDeclaration("plan", edit_ns),),
    (Tier(TierDeclaration(edit_tier, "Steps"), (Item("a"), Item("b"), Item("c"))),),
    (
        SimpleRelationDeclaration(
            QualifiedName(edit_ns, "membership"),
            edit_tier,
            QualifiedName(edit_ns, "Step"),
        ),
    ),
    (),
    (AttributeDeclaration(edit_score, AttributeDomain.ITEM, XsdType.INTEGER),),
)


def edit_value(number: int) -> AttributeValue:
    return AttributeValue(edit_score, XsdType.INTEGER, str(number))


edit_once = edit_graph.set_attribute(ItemRef(edit_tier, 0), edit_value(1))
print("a new graph:", edit_once is not edit_graph)
print("the graph asked still stands:", edit_graph.tiers[0].items[0].attributes == ())

edit_editor = edit_graph.edit()
for edit_round in range(3):
    for edit_index in range(3):
        edit_editor.set_attribute(
            ItemRef(edit_tier, edit_index), edit_value(edit_round * 3 + edit_index)
        )
edit_settled = edit_editor.freeze()
print(
    "scores:",
    [
        value.lexical
        for item in edit_settled.tiers[0].items
        for value in item.attributes
    ],
)

edit_grown = edit_settled.insert_item(edit_tier, 0, Item("z"))
print("items:", [item.durable_id for item in edit_grown.tiers[0].items])
```

```text
a new graph: True
the graph asked still stands: True
scores: ['6', '7', '8']
items: ['z', 'a', 'b', 'c']
```

The sweep runs nine edits and validates once. The same nine on the frozen
carrier would build and validate nine graphs, which is the cost the editor
exists to avoid; the frozen carrier is for the single edit, where building one
graph is the whole job.

## The build machine

A `Program` holds a tuple of opcodes. Each primitive opcode is one checked state
transition: `DeclareNamespace`, `DeclareTier`, `DeclareAttribute`,
`DeclareRelation`, `AddItem`, `AttachValue`, `Relate`, `PromoteItem`, and
`PromoteBoundary`. `Repeat` runs a block a fixed number of times; it carries a
declared upper bound, so a serialized program cannot request unbounded
expansion.

`Program.unroll()` returns `AsBuilt`, including promoted references; the
module-level `execute(opcodes)` is the convenience operation that returns the
`Graph` alone. `AsBuilt` pairs the graph with the flattened primitive trace.
`unroll()` builds that graph from that trace, so the pair it hands back holds by
construction and is not executed a second time; an `AsBuilt` a caller assembles
itself re-executes its trace and refuses a pair that does not agree.

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
A refused opcode raises `ExecutionError` naming the failing opcode's index,
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

The tier `missing` was never declared, so the `AddItem` at index 0 cannot
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
attributes, a boundary's values, a relation's endpoints, whatever a reference
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
`GraphValidationError`, a subclass of `Refusal` and so of `ValueError`, carrying
the stage the [refusal order](../format.md#refusal-order) declares. This
includes validation performed while decoding a serialized document into a graph.
Once a `Graph` is valid, invalid arguments to the lookup, resolution, and
promotion methods `resolve_item()`, `boundaries()`, and `promote_item()`
deliberately remain plain `ValueError` and carry no stage, because a lookup
against an already-valid graph is not a reading of a document; wrong Python
argument kinds may raise `TypeError`. The editing
operations on `Graph` and `GraphEditor` refuse with `GraphValidationError`,
because each one is a construction boundary: the frozen carrier builds a graph
on the spot, and the editor builds one at `freeze()`. The build machine reports
a refused opcode as `ExecutionError` and retains its cause.
