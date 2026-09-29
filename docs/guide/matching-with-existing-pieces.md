# Matching with existing pieces

Tiergraph can answer several matching questions by composing its existing
selection, traversal, and folding operations. A selector produces a canonical
`NodeSet`; set operations combine those results, and `Walk` relates one
selection to another. `AttributeSelector` selects carriers where an attribute
is present. It does not compare the stored value.

This graph has three segment items and two annotation items. The `host`
relation connects both annotations to segments. Two segments carry `stress`;
one stores `"primary"` and the other stores the empty string.

```python
from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeSelector,
    AttributeValue,
    BipartiteRelationDeclaration,
    Graph,
    Item,
    ItemRef,
    ItemsSelector,
    NamespaceDeclaration,
    NodeSet,
    QualifiedName,
    RelationInstance,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    Walk,
    WalkDirection,
    XsdType,
    evaluate_selection,
)

ns = "urn:example:matching"


def q(local: str) -> QualifiedName:
    return QualifiedName(ns, local)


segments = q("segments")
segment_type = q("segment")
annotations = q("annotations")
annotation_type = q("annotation")
stress = q("stress")
host = q("host")

segment_refs = tuple(ItemRef(segments, index) for index in range(3))
annotation_refs = tuple(ItemRef(annotations, index) for index in range(2))
graph = Graph(
    (NamespaceDeclaration("ex", ns),),
    (
        Tier(
            TierDeclaration(segments, "Segments"),
            (
                Item(
                    "s0",
                    (AttributeValue(stress, XsdType.STRING, "primary"),),
                ),
                Item("s1"),
                Item("s2", (AttributeValue(stress, XsdType.STRING, ""),)),
            ),
        ),
        Tier(
            TierDeclaration(annotations, "Annotations"),
            (Item("a0"), Item("a1")),
        ),
    ),
    (
        SimpleRelationDeclaration(q("segment-membership"), segments, segment_type),
        SimpleRelationDeclaration(
            q("annotation-membership"), annotations, annotation_type
        ),
        BipartiteRelationDeclaration(host, annotation_type, segment_type),
    ),
    (
        RelationInstance(host, annotation_refs[0], segment_refs[0]),
        RelationInstance(host, annotation_refs[1], segment_refs[2]),
    ),
    (AttributeDeclaration(stress, AttributeDomain.ITEM, XsdType.STRING),),
)


def item_for(reference: ItemRef) -> Item:
    tier = next(tier for tier in graph.tiers if tier.declaration.name == reference.tier)
    return tier.items[reference.index]


def labels(nodes: NodeSet) -> list[str]:
    result = []
    for node in nodes.nodes:
        reference = node.reference
        assert isinstance(reference, ItemRef)
        result.append(item_for(reference).durable_id or "")
    return result


with_stress = evaluate_selection(graph, AttributeSelector(stress, AttributeDomain.ITEM))
primary = []
for node in with_stress.nodes:
    reference = node.reference
    assert isinstance(reference, ItemRef)
    value = next(
        value
        for value in item_for(reference).attributes
        if isinstance(value, AttributeValue) and value.name == stress
    )
    if value.lexical == "primary":
        primary.append(item_for(reference).durable_id)

all_segments = evaluate_selection(graph, ItemsSelector(segments))
all_annotations = evaluate_selection(graph, ItemsSelector(annotations))
hosted = Walk(all_annotations, host, WalkDirection.FORWARD, cap=1).evaluate().nodes

print("carrying stress:", labels(with_stress))
print("stress primary:", primary)
print("unhosted segments:", labels(all_segments - hosted))
```
```text
carrying stress: ['s0', 's2']
stress primary: ['s0']
unhosted segments: ['s1']
```

The first result is presence matching: the empty string is a stored value, so
`s2` is selected. The second result compares values in ordinary Python after
selection. For a portable value comparison, use a `WhereSelector`, as below.
The last result is relation matching: it subtracts the one-step image of the
annotation tier from the segment tier. Inverse relation matching uses the same
construction with `WalkDirection.INVERSE`.

## Selecting by a stored value

A `WhereSelector` applies a frozen predicate only to the nodes produced by its
base selector. `Has` is the presence primitive; negating it selects a missing
cell, while an empty stored string remains present.

```python
from tiergraph import WhereSelector
from tiergraph.predicate import Cell, Equals, Has, Not

primary_selector = WhereSelector(
    ItemsSelector(segments),
    Equals(Cell(stress), ("primary",)),
)
missing_selector = WhereSelector(
    ItemsSelector(segments),
    Not(Has(Cell(stress), alias="none")),
)

print("stress primary:", labels(evaluate_selection(graph, primary_selector)))
print("stress missing:", labels(evaluate_selection(graph, missing_selector)))
```
```text
stress primary: ['s0']
stress missing: ['s1']
```

The same predicates have a graph-free text form. Bare values take their type
from the tested cell when the predicate binds; quoted values are strings.
`none` is the default missing-cell alias, while `""` is a present empty string.
`!` binds tighter than `&`, which binds tighter than `|`.

```python
from tiergraph.predicate import PredicateSyntax, parse_predicate

syntax = PredicateSyntax.for_graph(graph, default_prefix="ex")
text_selector = WhereSelector(
    ItemsSelector(segments),
    parse_predicate("stress=primary|none", syntax),
)

print("stress primary or missing:", labels(evaluate_selection(graph, text_selector)))
```
```text
stress primary or missing: ['s0', 's1']
```

The command-line equivalent uses `--where` in place of a selector JSON file:
`tiergraph select --where 'stress!=none' --prefix ex graph.json`. It evaluates
the predicate over every item and writes the selected node references as JSON.
`--selector` and `--where` are mutually exclusive.

## Quantifying related items

`Related` tests one relation step from each candidate. `ANY` needs a related
item that satisfies the target, while `NONE` and `ALL` are true when the
candidate has no related items. The direction is read from the candidate to
the target; here segments use the inverse of the stored annotation-to-segment
relation.

```python
from tiergraph.predicate import And, Quantifier, Related, compile_predicate

unhosted_predicate = Related(
    host,
    WalkDirection.INVERSE,
    Quantifier.NONE,
    And(()),
)
unhosted = compile_predicate(unhosted_predicate).bind(graph).select(all_segments)

print("segments with no annotation:", labels(unhosted))
```
```text
segments with no annotation: ['s1']
```

## Matching complete outputs and folding alternatives

`OutputPlan` matches complete emitted token sequences against a finite
candidate set supplied by the caller. It keeps a residual for paths that match
none of those candidates. A semiring fold answers a different question over the
same path topology. `COUNTING` counts alternatives, while `BOOLEAN` records
whether any alternative exists.

The next graph is a diamond with the paths `a -> b -> d` and
`a -> c -> d`.

```python
from typing import Any

from tiergraph import (
    BOOLEAN,
    COUNTING,
    AttributeValuation,
    ChildCombination,
    Emissions,
    FoldDeclaration,
    FoldTransition,
    OutputPlan,
    PathPlan,
)
from tiergraph.semiring import Semiring

nodes = q("nodes")
node_type = q("node")
next_relation = q("next")
value_attribute = q("value")
node_refs = tuple(ItemRef(nodes, index) for index in range(4))
diamond = Graph(
    (NamespaceDeclaration("ex", ns),),
    (
        Tier(
            TierDeclaration(nodes, "Nodes"),
            tuple(
                Item(
                    label,
                    (AttributeValue(value_attribute, XsdType.STRING, "present"),),
                )
                for label in ("a", "b", "c", "d")
            ),
        ),
    ),
    (
        SimpleRelationDeclaration(q("membership"), nodes, node_type),
        BipartiteRelationDeclaration(next_relation, node_type, node_type, acyclic=True),
    ),
    (
        RelationInstance(next_relation, node_refs[0], node_refs[1]),
        RelationInstance(next_relation, node_refs[0], node_refs[2]),
        RelationInstance(next_relation, node_refs[1], node_refs[3]),
        RelationInstance(next_relation, node_refs[2], node_refs[3]),
    ),
    (AttributeDeclaration(value_attribute, AttributeDomain.ITEM, XsdType.STRING),),
)


def prepare(semiring: Semiring[Any]) -> PathPlan[Any]:
    declaration = FoldDeclaration[Any](
        "diamond",
        diamond,
        AttributeValuation("value", value_attribute, (nodes,)),
        semiring,
        lambda _value, _label: semiring.one,
        (FoldTransition(next_relation, ChildCombination.OR),),
        roots=(node_refs[0],),
    )
    return PathPlan.prepare(declaration)


counting_plan = prepare(COUNTING)
count = counting_plan.evaluate().value
exists = prepare(BOOLEAN).evaluate().value
emissions = Emissions.bind(
    counting_plan,
    {label: (label,) for label in ("a", "b", "c", "d")},
)
output = OutputPlan.prepare(
    counting_plan,
    emissions,
    (("a", "b", "d"), ("a", "c", "d")),
).masses()

print("folds:", count, exists)
print("candidate masses:", output.per_candidate, "residual:", output.residual)
```
```text
folds: 2 True
candidate masses: (1, 1) residual: 0
```

`OutputPlan` compares whole outputs only with the candidates it is given. The
residual accounts for every other output, but it does not search for a sequence
pattern. Tiergraph has no sequence-pattern query language.
