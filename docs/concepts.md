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
values. Folds evaluate an acyclic dependency relation with a caller-supplied
semiring. None of these views adds a new kind of stored node; each is a reading
of the same graph.

## What a graph is built from

- **Namespaces** bind a document-local prefix to a URI. Every qualified name a
  graph uses must resolve to a declared namespace.
- **Tiers** hold items in order. Item order carries meaning and is preserved;
  a tier's boundaries are the `n + 1` positions before, between, and after its
  `n` items.
- **Items** are tier members. An item may hold typed attributes and may be
  promoted to a durable identifier when a reference must survive edits.
- **Attributes** are typed by a growable XSD subset (string, boolean, integer,
  decimal, double) and are declared for one domain: document, tier, item,
  position, relation declaration, or relation instance. A value's lexical form
  is canonicalized when it is stored, so equal values have one spelling.
- **Relations** come in three shapes. A simple relation gives every member of
  one tier a single item type. A bipartite relation links two typed endpoints,
  each an item or a boundary, and can promise acyclicity or a single parent. A
  polyadic relation links two explicitly ordered endpoint sequences and can
  promise source uniqueness, distinct targets, and subset membership.
- **References** address items and boundaries at two identity levels. A
  structural reference is a coordinate (`tier`, `index`); a durable reference
  names an item by its promoted id, or a boundary by its anchor and side.

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
print("boundaries on the tier:", len(graph.positions(words)))
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

## Immutability and refusal

`Graph` is a frozen value that validates its whole boundary in
`__post_init__`. An invalid graph cannot be constructed, so no later view has to
re-check it. Construction refuses undeclared namespaces, duplicate names or
durable ids, attribute values whose type or domain does not match a declaration,
references outside their tier, relation endpoints of the wrong kind, and
violations of declared constraints such as acyclicity or single-parent
incidence.

Collections whose supply order carries no meaning are canonicalized:
namespaces, relation and attribute declarations, and every attribute-value set
are sorted, so two graphs that differ only in input order compare equal and
serialize identically. Tiers, tier items, relation instances, and polyadic
endpoint sequences keep their order, because for those the sequence is data.

Profiles add their own checks without adding kernel node kinds. Constructing a
`ClockProfile`, an `OrderedRootsProfile`, or a `JsonValueProfile` validates the
declarations that interpretation needs and refuses a graph that cannot carry it.

## Lineage

The model learned from Paul Hertz's Delta representation and the heterogeneous
relation graphs of the Festival Speech Synthesis System. tiergraph keeps their
emphasis on explicit tiered structure and defines its own typed, immutable
model with a versioned interchange format. Phonetics is one application; the
graph and format assign no phonetic meaning to any tier or attribute.
