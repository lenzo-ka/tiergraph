# Concepts

tiergraph stores one immutable graph made of ordered tiers, and it derives every
other view from that single store. This page describes the data model: what a
graph is built from, what it guarantees, and how the same structure supports
selection, traversal, timing, and folds.

## One store, several views

A tiergraph document is one `Graph`. A tier is a named, ordered sequence of
items. Items may carry declared, XSD-typed scalar attributes. Relations connect
items or boundaries according to their declarations. That is the whole store.

The views are computed, not stored. Selection reads tiers, items, and
boundaries as nodes. Traversal follows declared relation incidence. Profiles
interpret particular declarations as clocks, roots, choices, or recursive JSON
values. Folds evaluate a finite dependency relation with a caller-supplied
semiring. None of these views adds a new kind of stored node; each is a reading
of the same graph.

## What a graph is built from

- **Namespaces** bind a document-local prefix to a URI. Every qualified name a
  graph uses must resolve to a declared namespace.
- **Tiers** hold items in order. Item order carries meaning and is preserved;
  a tier with `n` items has `n + 1` boundaries: before, between, and after
  those items.
- **Items** are tier members. An item may hold typed attributes and may be
  promoted to a durable identifier when a reference must survive edits.
- **Attributes** are optional and have at most one value per qualified name.
  They are typed by a growable XSD subset (string, boolean, integer, decimal,
  double) and are declared for one domain: document, tier, item, boundary,
  relation declaration, or relation instance. A value's lexical form is
  canonicalized when it is stored, so equal values have one spelling. Absence
  means absent: there are deliberately no defaults that appear in a reading
  without being present in the bytes.
- **Relations** come in three shapes. A simple relation gives every member of
  one tier a single item type. A bipartite relation links two typed endpoints,
  each an item or a boundary, and can promise acyclicity or a single parent. A
  polyadic relation links two explicitly ordered endpoint sequences; it can
  promise acyclicity or a single parent as a bipartite one does, and adds source
  uniqueness, distinct targets, and subset membership.
- **References** address items and boundaries at two identity levels. A
  structural reference is a coordinate (`tier`, `index`); a durable reference
  names an item by its promoted id, or a boundary by its anchor and side.

`XsdType` types scalar values, not graph referents. An in-graph reference is a
relation: its declaration types the referents and can validate `single_parent`
and `acyclic` promises. An out-of-graph reference is a `STRING`, honestly,
because the graph cannot validate its target. A hypothetical reference-valued
`XsdType` could say only "this string is a reference," which is strictly weaker
than the referent typing and promises relations already provide.

Durable item and boundary ids are genuine as-built graph content, not metadata
about that content. Promoting an item or an interior boundary therefore changes
canonical bytes and their SHA-256 fingerprint. This is expected: durable ids
address items across graphs, and a fingerprint that ignored identity would hash
away the identifier. A tier's leading and trailing boundaries are already
addressable by side, so promoting one adds nothing and returns the same graph.

```python
from tiergraph import (
    BipartiteRelationDeclaration,
    Graph,
    Item,
    ItemRef,
    NamespaceDeclaration,
    QualifiedName,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
)

ns = "https://example.com/doc"
words = QualifiedName(ns, "words")
word_type = QualifiedName(ns, "word")
graph = Graph(
    (NamespaceDeclaration("doc", ns),),
    (Tier(TierDeclaration(words, "Words"), (Item("the"), Item("cat"))),),
    (
        SimpleRelationDeclaration(QualifiedName(ns, "membership"), words, word_type),
        BipartiteRelationDeclaration(
            QualifiedName(ns, "modifies"), word_type, word_type
        ),
    ),
)

print("item indices:", [ref.index for ref in graph.canonical_items()])
print("type of item 0:", graph.item_type(ItemRef(words, 0)))
print("boundaries on the tier:", len(graph.boundaries(words)))
```

```text
item indices: [0, 1]
type of item 0: {https://example.com/doc}word
boundaries on the tier: 3
```

The two items produce a canonical order and three boundaries. The simple
membership relation is what gives item 0 a type; a tier with no membership
relation is untyped, and asking for its item type is refused rather than
guessed.

## Structured payloads in scalar attributes

Attribute values are typed scalars (`XsdType`). When an application needs to
attach non-scalar evidence, such as nested feature structures, scored
alternatives, or provenance, tiergraph does not currently provide a first-class
in-graph attachment mechanism. For now, the sanctioned pattern is a declared
string attribute containing a JSON object with this canonical envelope:

`{"schema": "<stable versioned schema id>", "value": <the JSON value>}`

The producing application serializes the whole envelope with
`json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True,
separators=(",", ":"))`.

tiergraph checks only that the attribute is declared as a string in the correct
domain. It does not check whether the string is valid JSON; whether the envelope
or schema is present or recognized; required fields or types inside `value`;
numeric ranges; or canonical serialization. The producing and consuming
applications must perform those validations.

Separately, `json_value_graph` and `JsonValueProfile` already represent a JSON
value as a checked, standalone graph. They are the foundation for a future
first-class payload-attachment API that could attach a JSON value to an item in
another graph. That API does not yet exist. If it is added, stringified-JSON
attributes will be candidates for migration.

## Coordinates, and what an edit does to them

A position is the thing; a coordinate is its identity. A `(tier, index)` pair is
a coordinate, and the families that spell one are `ItemRef` and `BoundaryRef`,
with `DurableItemRef` and `DurableBoundaryRef` naming the same places by an
identity the graph carries rather than by where they sit.

This document said so before the code did. The sentence above about a structural
reference being a coordinate was already published while the type was called
`PositionRef`, and the prose was the half that was right: the reading has not
changed, only the spelling caught up to it.

Coordinates are cheap to key on and they move. An edit that inserts an item
shifts every later index in that tier, and removing a relation shifts every later
relation index in the graph. A `Displacement` reports where every position of one
graph stands in another, over all four of the ordered index spaces this graph
has: items, boundaries, relation instances, and polyadic relation instances.

Its maps are total. Every coordinate the source individuates is a key of its map
or a member of the matching departed set, never both and never neither, and a
position that did not move appears at its own coordinate rather than being left
out. Silence would otherwise have to mean two things — *did not move* and *is not
described* — and a caller would have to guess which.

Only one half of that totality is decided when the value is built, and the
difference matters to anyone constructing a `Displacement` directly. A
displacement does not carry the graph it is about, so *never both* is refused at
construction while *never neither* is not detectable there: the source space is
whatever the maps and departed sets name between them, and a coordinate omitted
from both is indistinguishable from one the source never had. A displacement this
library produces is total because the operation that built it saw a graph; a
hand-built one is total by definition rather than by check.

Composition inherits that asymmetry rather than repairing it. Composing refuses a
later displacement that does not describe the earlier one's result — an image the
later map neither carries nor departs is an error, because a composition is
defined only where the first result is the second's source. A gap in the *earlier*
displacement raises nothing and is carried into the composite, since composition
ranges over the earlier map's domain and a coordinate missing from it is never
consulted. Totality is a property the producing operation supplies, not one the
value or its algebra enforces.

A displacement is accumulated by the operation performing the edit and is never
recovered by comparing two graph values. Two values cannot carry it: an anonymous
item that moved is indistinguishable from one that did not.

## Seals

A seal states how much of one ordered carrier may not be disturbed. It is data
the graph carries, not an inference about how the graph was built.

That distinction is the whole reason it exists. A graph value carries no history,
so *"this will only ever be appended to"* is a promise about future edits that
nothing in the value can refute — identical content built by appending, by
inserting in the middle, and from scratch produces equal graphs with identical
canonical bytes. A seal makes no claim about the past. It constrains successors,
and a successor is always in hand to be checked, so it is refutable at every step
where a claim about the past never is. Append-only is the degenerate case, where
the seal equals the carrier's length.

**A seal is geometric, not a content freeze.** Sealed members cannot move; their
attribute values may still be rewritten. This is deliberate and load-bearing in
both directions. It is what makes coordinate-keyed layers safe, since such a fact
breaks when a coordinate moves and not when a value changes. And it is what the
motivating work requires: taking an automatic reading, freezing it as *what I was
given*, and iterating the values to a fixpoint is exactly twiddling content over
fixed structure. A total seal would have forbidden the work the seal exists to
protect.

Sealing advances freely. It retreats only through `unseal`, which is named,
public, and deliberately loud — an escape spelled as a flag on the sealing
operation would make unsealing indistinguishable from sealing at the call site.

`SealCertificate` reports how many members a check could actually discriminate.
A durable identifier lets two graph values expose movement; an anonymous member
does not, because an anonymous member that moved is indistinguishable from one
that did not. Anonymous sealed members therefore do not contribute to
`sealed_members`. A zero count says plainly that the value-only check was
vacuous rather than letting that pass read as a strong one. This does not weaken
an operation's own displacement accounting: the operation sees the movement as
it happens, while a declaration comparing two values has no such history.

One limit is worth stating plainly rather than papering over: the constructor can
validate that a seal is internally consistent, but it cannot know whether a
hand-built prefix matches anyone else's, so a constructed graph may claim any
seal. That is the same limit durable identifiers already live with. What a seal
does guarantee is that it is refutable across every rewrite, checkable between
any two values, and a bare declaration on a value with no predecessor — and
append-only cannot say even the last of those.

## Attribute layers

`Graph.layers` keeps separately sourced attribute facts without weakening the
base graph's at-most-one value rule. A layer is identified by the vocabulary it
writes in and by its source. Facts may describe items, boundaries, tiers,
relation declarations, binary or polyadic relation instances, and the document;
they cannot add tiers, items, boundaries, or relations.

A `Delivery` names the layers to read and their precedence. Its `read` policy is
always explicit: `FIRST` selects the earliest statement, `LAST` selects the
latest, and `ALL` returns every statement in delivery order. `consensus` reports
agreement without merging values, while `disagreements` supplies the rows that
need review. `flatten` writes a chosen delivery into a new layerless base.

Structural edits remap coordinate-keyed facts. When a subject is removed, its
fact becomes an `OrphanedSubject` carrying the old coordinate and its former
carrier. Orphans are invisible to reads and accumulate until a caller replaces
the layer; flattening a delivery containing one is refused. A layer never vetoes
a base edit. Inside a seal the seal does, which is the one place the base's own
constraint outranks anything a layer has to say about it.

## Immutability and refusal

`Graph` is a frozen value that validates its whole boundary in
`__post_init__`. An invalid graph cannot be constructed, so no later view has to
re-check it. Construction refuses undeclared namespaces, duplicate names or
durable ids, attribute values whose type or domain does not match a declaration,
references outside their tier, relation endpoints of the wrong kind, and
violations of declared constraints such as acyclicity or single-parent
incidence.

Collections whose supply order carries no meaning are canonicalized: namespaces,
relation and attribute declarations, every attribute-value set, seals, layers and
the facts within each layer, the sparse boundary values, and a relation side's
endpoint kinds and tiers are all sorted, so two graphs that differ only in input
order compare equal and serialize identically. Tiers, tier items, relation
instances, and polyadic endpoint sequences keep their order, because for those
the sequence is data.

Profiles add their own checks without adding kernel node kinds. Constructing a
`ClockProfile`, an `OrderedRootsProfile`, or a `JsonValueProfile` validates the
declarations that interpretation needs and refuses a graph that cannot carry it.

## Lineage

The model learned from Paul Hertz's Delta representation and the heterogeneous
relation graphs of the Festival Speech Synthesis System. tiergraph keeps their
emphasis on explicit tiered structure and defines its own typed, immutable
model with a versioned interchange format. Phonetics is one application; the
graph and format assign no phonetic meaning to any tier or attribute.
