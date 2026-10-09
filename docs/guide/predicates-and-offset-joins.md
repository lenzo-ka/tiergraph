# Predicates and offset joins

A value predicate decides something about one node: whether an attribute is
present, what it holds, whether a related item satisfies another predicate, or
how its offset interval relates to other items'. Predicates live in
`tiergraph.predicate`, have a graph-free text form, and plug into selection
through `WhereSelector`, into [sequence patterns](patterns.md) as item tests,
and into the command line through `tiergraph select --where`. An offset join
pairs the items of two selections whose intervals stand in a declared relation.

Some of these questions can also be answered by composing selection and
traversal. A selector produces a canonical `NodeSet`; set operations combine
those results, and `Walk` relates one selection to another.
`AttributeSelector` selects carriers where an attribute is present. It does not
compare the stored value.

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

## The text form and JSON

The text form covers comparisons (`=`, `!=`, `<`, `<=`, `>`, `>=`), regular
expression matches written `cell~"regex"`, negation with `!`, conjunction with
`&`, disjunction with `|`, and parentheses. `format_predicate` writes the
canonical text of a predicate, and `predicate_to_data` and `predicate_loads`
write and read its strict JSON form, which selector JSON embeds.

```python
import json

from tiergraph.predicate import format_predicate, predicate_loads, predicate_to_data

for text in ("start>=4", 'stress~"pri.*"', "!(start=0 & end=8)"):
    predicate = parse_predicate(text, syntax)
    selector = WhereSelector(ItemsSelector(segments), predicate)
    selected = evaluate_selection(graph, selector)
    same = predicate_loads(json.dumps(predicate_to_data(predicate))) == predicate
    print(f"{format_predicate(predicate, syntax)}: {labels(selected)} {same}")
```
```text
start>=4: ['s1'] True
stress~"pri.*": ['s0'] True
!(start=0 & end=8): ['s0', 's1'] True
```

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
filters those items first. Like `span_pairs`, `Spans` never relates an item to
itself, including when `other` names the candidate's own tier. `ANY` requires
one matching item; `NONE` and `ALL` use the same vacuous truth as `Related` when
no interval-related item remains.

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
equal_to_another_segment = (
    compile_predicate(
        Spans(
            OffsetProfile(start, end=end),
            IntervalRelation.EQUAL,
            Quantifier.ANY,
            segments,
            And(()),
        )
    )
    .bind(graph)
    .select(all_segments)
)
print("segments aligned with an annotation:", labels(aligned_segments))
print("segments equal to another segment:", labels(equal_to_another_segment))
```
```text
segments aligned with an annotation: ['s0', 's1']
segments equal to another segment: []
```

An origin, end, extent, or partition missing from an item that the operation
reads is a semantic refusal. An extent must be nonnegative, and an end cannot
precede its origin. Supplying `extent=` in place of `end=` gives the same
intervals when the stored extent is `end - origin`. When `partition` is present,
only items with equal partition values can match.

For regular patterns over item sequences, see [Sequence patterns](patterns.md).
To bound the work a predicate or join may do, see [Work budgets](work-budgets.md).
