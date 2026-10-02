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
start = q("start")
end = q("end")

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
                    (
                        AttributeValue(stress, XsdType.STRING, "primary"),
                        AttributeValue(start, XsdType.INTEGER, "0"),
                        AttributeValue(end, XsdType.INTEGER, "4"),
                    ),
                ),
                Item(
                    "s1",
                    (
                        AttributeValue(start, XsdType.INTEGER, "4"),
                        AttributeValue(end, XsdType.INTEGER, "8"),
                    ),
                ),
                Item(
                    "s2",
                    (
                        AttributeValue(stress, XsdType.STRING, ""),
                        AttributeValue(start, XsdType.INTEGER, "0"),
                        AttributeValue(end, XsdType.INTEGER, "8"),
                    ),
                ),
            ),
        ),
        Tier(
            TierDeclaration(annotations, "Annotations"),
            (
                Item(
                    "a0",
                    (
                        AttributeValue(start, XsdType.INTEGER, "0"),
                        AttributeValue(end, XsdType.INTEGER, "4"),
                    ),
                ),
                Item(
                    "a1",
                    (
                        AttributeValue(start, XsdType.INTEGER, "4"),
                        AttributeValue(end, XsdType.INTEGER, "8"),
                    ),
                ),
            ),
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
    (
        AttributeDeclaration(stress, AttributeDomain.ITEM, XsdType.STRING),
        AttributeDeclaration(start, AttributeDomain.ITEM, XsdType.INTEGER),
        AttributeDeclaration(end, AttributeDomain.ITEM, XsdType.INTEGER),
    ),
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

## Joining items by offsets

`OffsetProfile` names item attributes rather than tiers. `span_pairs` applies
one directed half-open interval relation to the items selected on its left and
right. It excludes a node paired with itself and preserves left-major declared
order. A limit truncates only the returned witnesses and reports that cut in
the result's `extent`.

```python
from tiergraph.match import span_pairs
from tiergraph.predicate import IntervalRelation, OffsetProfile

equal_offsets = span_pairs(
    graph,
    ItemsSelector(segments),
    ItemsSelector(annotations),
    IntervalRelation.EQUAL,
    OffsetProfile(start, end=end),
)
print(
    "equal offsets:",
    [
        (labels(NodeSet(graph, (left,)))[0], labels(NodeSet(graph, (right,)))[0])
        for left, right in equal_offsets.pairs
    ],
)
print("extent:", equal_offsets.extent.value)
```
```text
equal offsets: [('s0', 'a0'), ('s1', 'a1')]
extent: exhaustive
```

`Spans` uses the same interval relations inside a predicate. Its `other` tier
supplies the items tested against each candidate, and its target predicate
filters those items first. `ANY` requires one matching item; `NONE` and `ALL`
use the same vacuous truth as `Related` when no interval-related item remains.

```python
from tiergraph.predicate import Spans

aligned_with_an_annotation = Spans(
    OffsetProfile(start, end=end),
    IntervalRelation.EQUAL,
    Quantifier.ANY,
    annotations,
    And(()),
)
aligned_segments = (
    compile_predicate(aligned_with_an_annotation).bind(graph).select(all_segments)
)
print("segments aligned with an annotation:", labels(aligned_segments))
```
```text
segments aligned with an annotation: ['s0', 's1']
```

An origin, end, extent, or partition missing from an item that the operation
reads is a semantic refusal. An extent must be nonnegative, and an end cannot
precede its origin. Supplying `extent=` in place of `end=` gives the same
intervals when the stored extent is `end - origin`. When `partition` is present,
only items with equal partition values can match.

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
residual accounts for every other output. Regular sequence patterns answer a
different question over graph items in a declared order. An `AtomPattern`
consumes one item when its predicate holds, and a `FocusPattern` marks the items
returned by `focus` or a `SequenceSelector`.

```python
from tiergraph.match import (
    AtomPattern,
    FocusPattern,
    SeqPattern,
    TierOrder,
    compile_pattern,
)
from tiergraph.predicate import Cell, Equals, Has, Not

primary_then_missing = SeqPattern(
    (
        FocusPattern(AtomPattern(Equals(Cell(stress), ("primary",)))),
        AtomPattern(Not(Has(Cell(stress), alias="none"))),
    )
)
focused = compile_pattern(primary_then_missing).focus(graph, TierOrder(segments))

print("primary before a missing stress:", labels(focused))
```
```text
primary before a missing stress: ['s0']
```

`TierOrder` treats a tier as one scope. `ContainerOrder` makes one scope from
each selected container's direct children, and `AdjacentRuns` splits selected
items where their declared offsets are not adjacent. Matches never cross a
scope. `exists` and `focus` admit nullable patterns; `spans` and `count` refuse
them because an empty match has no item span.
