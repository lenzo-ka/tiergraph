# Selection and traversal

`select(graph, (selector,))` returns a canonical `NodeSet`. Selectors address tiers,
items, boundaries, types, and attributes. `NodeSet` supplies set operations while
preserving the graph's canonical order.

`Walk` follows relation incidence in a declared direction and returns a
`WalkResult`. `OrderedContainment` is narrower: it reads an acyclic,
source-unique, item-only polyadic relation as ordered containment. Its child and
descendant results use `NodeSequence`, which preserves order and repetition;
parent and ancestor results use `NodeSet`.
