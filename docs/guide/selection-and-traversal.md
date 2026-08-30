# Selection and traversal

Selection names a set of graph nodes. Traversal follows relations from one set
to another. Both return canonical results, so the same selector on the same
graph always gives the same order.

## Selecting nodes

`evaluate_selection(graph, selector)` returns a canonical `NodeSet`. Selectors address
tiers, items, boundaries, types, and attributes. A `NodeSet` deduplicates and
sorts its nodes into the graph's canonical order and supports union (`|`),
intersection (`&`), and difference (`-`) with another set from the same graph.
Because the order is canonical, set algebra between selections is stable.

Relation instances live in two collections -- bipartite pairs and polyadic
instances -- that index separately, so index `0` names a different fact in each.
They are two node kinds accordingly, `relation_instance` and
`polyadic_relation_instance`, each over its own index. An attribute selection on
the `relation_instance` domain reads both collections, because the kernel admits
that domain's values on either, and it reports each carrier under its own kind.
A polyadic node sorts by its declaration, then by its two side arities, then by
its endpoints read in stored order, so two instances that differ only in the
order of one side stay distinct and sort apart.

The example is a small acyclic `links` relation over four hosts: `a -> b`,
`b -> c`, and `a -> d`.

```python
from tiergraph import (
    BipartiteRelationDeclaration,
    Graph,
    Item,
    ItemRef,
    ItemSelector,
    NamespaceDeclaration,
    NodeSet,
    QualifiedName,
    RelationInstance,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    Walk,
    WalkDirection,
    evaluate_selection,
)

ns = "https://example.com/net"
hosts = QualifiedName(ns, "hosts")
host_type = QualifiedName(ns, "host")
links = QualifiedName(ns, "links")

a, b, c, d = (ItemRef(hosts, index) for index in range(4))
graph = Graph(
    (NamespaceDeclaration("net", ns),),
    (
        Tier(
            TierDeclaration(hosts, "Hosts"),
            (Item("a"), Item("b"), Item("c"), Item("d")),
        ),
    ),
    (
        SimpleRelationDeclaration(QualifiedName(ns, "membership"), hosts, host_type),
        BipartiteRelationDeclaration(links, host_type, host_type, acyclic=True),
    ),
    (
        RelationInstance(links, a, b),
        RelationInstance(links, b, c),
        RelationInstance(links, a, d),
    ),
)


def names(nodes: NodeSet) -> list[str]:
    labels = []
    for node in nodes.nodes:
        reference = node.reference
        assert isinstance(reference, ItemRef)
        labels.append(graph.tiers[0].items[reference.index].durable_id or "")
    return labels
```

## Walking a relation

A `Walk` follows one bipartite relation transitively from a source `NodeSet` and
returns the reachable set, not counting the source itself. `WalkDirection.FORWARD`
reads the stored incidence; `WalkDirection.INVERSE` computes the fiber, which
gives ancestors. Because inverse reachability is set-valued, both directions
return a `NodeSet`.

```python
def reach(start: ItemRef, direction: WalkDirection) -> NodeSet:
    selection = evaluate_selection(graph, ItemSelector(start))
    return Walk(selection, links, direction).evaluate().nodes


from_a = reach(a, WalkDirection.FORWARD)
from_b = reach(b, WalkDirection.FORWARD)
print("reachable from a:", names(from_a))
print("reachable from b:", names(from_b))
print("only via a:", names(from_a - from_b))
print("ancestors of c:", names(reach(c, WalkDirection.INVERSE)))
```

```text
reachable from a: ['b', 'c', 'd']
reachable from b: ['c']
only via a: ['b', 'd']
ancestors of c: ['a', 'b']
```

The difference `from_a - from_b` is the set algebra: everything `a` reaches that
`b` does not.

An unbounded walk is admitted only when the relation was declared acyclic, so it
cannot loop forever. To walk a relation without that promise, pass a step `cap`.
The result then reports whether the cap stopped it early.

```python
bounded = Walk(
    evaluate_selection(graph, ItemSelector(a)), links, WalkDirection.FORWARD, cap=1
).evaluate()
print("one hop from a:", names(bounded.nodes), "truncated:", bounded.truncated)
```

```text
one hop from a: ['b', 'd'] truncated: True
```

One step from `a` reaches `b` and `d`; `c` is a second step away, so the walk
reports `truncated: True`.

## Ordered polyadic traversal

`OrderedPolyadicTraversal` is the role-neutral engine for any declared polyadic
relation. It binds the stored source and target sides explicitly. `direct()`
returns a `NodeSequence` in relation-instance and endpoint-incidence order,
including repeated endpoints. `transitive()` returns depth-first pre-order in
that same incidence order and is admitted only for a declaration whose
`acyclic` promise was validated by the graph.

`inverse()` computes a `NodeSet` fiber, so repeated endpoints and parents are
deduplicated into canonical graph order. When stored order is the question,
`stored_opposite(instance_index)` returns the selected opposite-side sequence
for that particular relation instance instead. Either side may be selected as
the traversal source, and item and boundary endpoints are resolved without
assigning them domain roles.

Those operations all key on an origin endpoint. A correspondence read as a
whole -- one ordered side against another, with no positional pairing between
them -- has no origin to key on, so `instances()` enumerates it: each
`PolyadicIncidence` carries the instance's graph-local index and both sides as
`NodeSequence` values in stored order. The sides are named for the declaration,
not for the traversal direction, and their arities need not match. That is what
makes the shape usable for a correspondence that reorders one side: the order is
graph content, and reading either side as a bag of endpoints, or pairing the two
sides off position by position, would lose it.

`OrderedContainment` below is the item-only, source-unique, acyclic profile over
this shared engine. Its existing result types and ordering remain unchanged.

## Ordered containment

`OrderedContainment` is a narrower reading for one polyadic relation that models
a tree: item-only sides, source-unique, and acyclic. It preserves child order
and repetition. Descending queries (`direct_children`, `descendants`, `leaves`)
return a `NodeSequence`, which keeps order and duplicates; ascending queries
(`parents`, `ancestors`) return a `NodeSet`, because an inverse fiber is a set.

```python
from tiergraph import (
    Graph,
    Item,
    ItemRef,
    NamespaceDeclaration,
    NodeSequence,
    NodeSet,
    OrderedContainment,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    QualifiedName,
    RelationEndpointKind,
    RelationSideDeclaration,
    Tier,
    TierDeclaration,
)

ns = "https://example.com/tree"
nodes = QualifiedName(ns, "nodes")
contains = QualifiedName(ns, "contains")

labels = ("root", "branch", "leaf-1", "leaf-2", "twig")
refs = {name: ItemRef(nodes, index) for index, name in enumerate(labels)}
one = RelationSideDeclaration((RelationEndpointKind.ITEM,), (nodes,), 1, 1)
many = RelationSideDeclaration((RelationEndpointKind.ITEM,), (nodes,), 1, None)
tree_graph = Graph(
    (NamespaceDeclaration("tree", ns),),
    (Tier(TierDeclaration(nodes, "Nodes"), tuple(Item(name) for name in labels)),),
    (
        PolyadicRelationDeclaration(
            contains, one, many, unique_sources=True, acyclic=True
        ),
    ),
    polyadic_relations=(
        PolyadicRelationInstance(
            contains, (refs["root"],), (refs["branch"], refs["twig"])
        ),
        PolyadicRelationInstance(
            contains, (refs["branch"],), (refs["leaf-1"], refs["leaf-2"])
        ),
    ),
)


def show(sequence: NodeSequence | NodeSet) -> list[str]:
    result = []
    for node in sequence.nodes:
        reference = node.reference
        assert isinstance(reference, ItemRef)
        result.append(tree_graph.tiers[0].items[reference.index].durable_id or "")
    return result


tree = OrderedContainment(tree_graph, contains)
print("children of root:", show(tree.direct_children(refs["root"])))
print("descendants of root:", show(tree.descendants(refs["root"])))
print("leaves of root:", show(tree.leaves(refs["root"])))
print("ancestors of leaf-1:", show(tree.ancestors(refs["leaf-1"])))
```

```text
children of root: ['branch', 'twig']
descendants of root: ['branch', 'leaf-1', 'leaf-2', 'twig']
leaves of root: ['leaf-1', 'leaf-2', 'twig']
ancestors of leaf-1: ['root', 'branch']
```

Descendants come back in depth-first pre-order; leaves are the descendants with
no children of their own. The ascending `ancestors` result is a set, so `root`
and `branch` appear once regardless of how many paths reach `leaf-1`.
