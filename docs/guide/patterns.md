# Sequence patterns

A sequence pattern is a regular expression whose symbols are item tests. It is
matched over an *ordering*: a declared reading of graph nodes in sequence, split
into one or more scopes. Matching has three steps. Write the pattern, as Python
values or as text; compile it once; then ask one of four questions of an
ordering on a graph: whether it matches (`exists`), which items its marked part
consumed (`focus`), where it matched (`spans`), or how many distinct spans
matched (`count`).

Value tests inside a pattern are the predicates of `tiergraph.predicate`, which
[Predicates and offset joins](predicates-and-offset-joins.md) introduces.

## An example graph

Two words, `cat` and `sat`, are spelled by six phone items. Each phone carries
its segment and a consonant or vowel `class`. An ordered polyadic `spells`
relation gives each word its phones in order, which is the shape
`ContainerOrder` reads.

```python
from tiergraph import (
    AttributeValue,
    BoundarySelector,
    ItemRef,
    ItemsSelector,
    Node,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    RelationEndpointKind,
    RelationSideDeclaration,
    UnionSelector,
    WhereSelector,
    evaluate_selection,
)
from tiergraph.build import document, item
from tiergraph.match import (
    AtomPattern,
    BoundOrdering,
    ContainerOrder,
    SeqPattern,
    TierOrder,
    compile_pattern,
    format_pattern,
    parse_pattern,
)
from tiergraph.predicate import Cell, Equals, PredicateSyntax

builder = document("urn:example:patterns", prefix="ex")
builder.attributes({"seg": "string", "class": "string"})
segments = (("k", "C"), ("ae", "V"), ("t", "C"), ("s", "C"), ("ae", "V"), ("t", "C"))
phones = builder.tier(
    "phones",
    tuple(
        item(f"p{index}", attrs={"seg": seg, "class": kind})
        for index, (seg, kind) in enumerate(segments)
    ),
    item_type="phone",
    membership="phone-membership",
)
words = builder.tier(
    "words", ("cat", "sat"), item_type="word", membership="word-membership"
)
spells = builder.qname("spells")
builder.declare(
    PolyadicRelationDeclaration(
        spells,
        RelationSideDeclaration((RelationEndpointKind.ITEM,), (words.name,), 1, 1),
        RelationSideDeclaration((RelationEndpointKind.ITEM,), (phones.name,), 1, None),
        unique_sources=True,
        acyclic=True,
    )
)
for word, spelled in ((0, (0, 1, 2)), (1, (3, 4, 5))):
    builder.relate(
        PolyadicRelationInstance(
            spells, (words.ref(word),), tuple(phones.ref(index) for index in spelled)
        )
    )
graph = builder.build()
seg = builder.qname("seg")
kind = builder.qname("class")
syntax = PredicateSyntax.for_graph(graph, default_prefix="ex")


def segs(nodes: tuple[Node, ...]) -> list[str]:
    result: list[str] = []
    for node in nodes:
        reference = node.reference
        assert isinstance(reference, ItemRef)
        phone = graph.tiers[0].items[reference.index]
        result.extend(
            value.lexical
            for value in phone.attributes
            if isinstance(value, AttributeValue) and value.name == seg
        )
    return result
```

## Patterns as values and as text

A pattern is an immutable value. `AtomPattern` consumes one item when its
predicate holds; `SeqPattern` matches its parts in order. The same pattern has a
text form that `parse_pattern` reads and `format_pattern` writes.

```python
consonant_vowel = SeqPattern(
    (
        AtomPattern(Equals(Cell(kind), ("C",))),
        AtomPattern(Equals(Cell(kind), ("V",))),
    )
)
print(format_pattern(consonant_vowel, syntax))
parsed = parse_pattern("{class=C} {class=V}", syntax)
print(format_pattern(parsed, syntax))
```

```text
{class="C"} {class="V"}
{class=C} {class=V}
```

A quoted value in text is a string. A bare value such as `C` takes its type from
the cell it is compared with when the predicate binds to a graph, so the two
patterns above match the same items although they are spelled differently.

| Text | Value | Matches |
| --- | --- | --- |
| `{pred}` | `AtomPattern(pred)` | one item on which the predicate holds |
| `.` | `AtomPattern(And(()))` | any one item |
| `a b` | `SeqPattern((a, b))` | `a` then `b` |
| `a \| b` | `AltPattern((a, b))` | either |
| `( ... )` | grouping | |
| `a?` `a*` `a+` | `RepeatPattern(a, min, max)` | zero or one, zero or more, one or more |
| `a{n}` `a{n,}` `a{,m}` `a{n,m}` | `RepeatPattern(a, min, max)` | counted repetition |
| `^` `$` | `StartPattern()`, `EndPattern()` | the start or end of a scope |
| `target / left _ right` | `FocusPattern(target)` in a sequence | `left target right`, marking `target` |

The predicate inside braces uses the text form of
[value predicates](predicates-and-offset-joins.md#selecting-by-a-stored-value).
The focus notation reads like a rewrite-rule environment: the part before `/` is
the focus, `_` marks where it sits, and either side of `_` may be empty.

## Ask a question of an ordering

`TierOrder` reads one tier as one scope in item order. A compiled pattern
answers every view against any graph and ordering.

```python
in_order = TierOrder(phones.name)
compiled = compile_pattern(parsed)
found = compiled.spans(graph, in_order)
print("spans:", [(match.start, match.end) for match in found.matches])
print("extent:", found.extent.value)
print("count:", compiled.count(graph, in_order))
print("exists:", compiled.exists(graph, in_order))

after_consonant = compile_pattern(parse_pattern("{class=V} / {class=C} _", syntax))
print("focus:", segs(after_consonant.focus(graph, in_order).nodes))
```

```text
spans: [(0, 2), (3, 5)]
extent: exhaustive
count: 2
exists: True
focus: ['ae', 'ae']
```

A span is a half-open range of positions in its scope, and `SpanMatch` also
carries the scope index and the matched nodes. `spans` returns each distinct
accepting span once, in scope order. `count` counts those distinct spans, never
the number of ways the pattern could match one span. `spans(limit=n)` stops
after `n` spans and reports `CUT_AT_BOUND` in `extent` if it found a further
one. `exists` and `focus` accept a pattern that can match zero items; `spans`
and `count` refuse it, because an empty match has no span to report.

## Orderings and scopes

A match never crosses a scope boundary, and `^` and `$` match at the edges of a
scope. Four orderings decide what the scopes are:

- `TierOrder(tier)` reads one tier as one scope.
- `ContainerOrder(relation, containers)` makes one scope of each selected
  container's direct children, read through an ordered containment relation.
- `AdjacentRuns(source, offsets)` splits the selected items wherever their
  half-open offset intervals do not meet, using the `OffsetProfile` described
  in [Predicates and offset joins](predicates-and-offset-joins.md#joining-items-by-offsets).
- `DeclaredOrder(successor, members)` reads a chain the graph itself declares,
  which may mix tiers, items, and boundaries.

Reading the phones by word makes each word a scope, so a word-initial consonant
is found in both words and a vowel followed by two consonants is not found at
all, because in tier order the only such run crosses from `cat` into `sat`.

```python
by_word = ContainerOrder(spells, ItemsSelector(words.name))
initial = compile_pattern(parse_pattern("^ {class=C}", syntax))
print("initial, tier order:", segs(initial.spans(graph, in_order).matches[0].items))
print(
    "initial, by word:",
    [segs(match.items) for match in initial.spans(graph, by_word).matches],
)
vowel_cluster = compile_pattern(parse_pattern("{class=V} {class=C}{2}", syntax))
print("VCC, tier order:", vowel_cluster.count(graph, in_order))
print("VCC, by word:", vowel_cluster.count(graph, by_word))
```

```text
initial, tier order: ['k']
initial, by word: [['k'], ['s']]
VCC, tier order: 1
VCC, by word: 0
```

### Declared orders

A declared order is a chain of successor links stored in the graph as a
polyadic relation with one endpoint on each side. Because the endpoints may be
items or boundaries on any tier, it can order material across tiers or put a
boundary in the sequence. The builder's `declared_order` declares the relation,
adds the links in the order given, and returns the `DeclaredOrder` that reads
it. Here the chain puts the boundary between the two words into the phone
sequence, so a match that runs from one word into the next has to consume it.

```python
chained = document("urn:example:patterns", prefix="ex")
chained.attributes({"seg": "string", "class": "string"})
chain_phones = chained.tier(
    "phones",
    tuple(
        item(f"p{index}", attrs={"seg": seg, "class": kind})
        for index, (seg, kind) in enumerate(segments)
    ),
    item_type="phone",
    membership="phone-membership",
)
word_break = chain_phones.after(2)
reading = chained.declared_order(
    "reading",
    UnionSelector((ItemsSelector(chain_phones.name), BoundarySelector(word_break))),
    (
        *(chain_phones.ref(index) for index in range(3)),
        word_break,
        *(chain_phones.ref(index) for index in range(3, 6)),
    ),
)
chained_graph = chained.build()

chain_in_tier_order = TierOrder(chain_phones.name)
print("VCC, tier order:", vowel_cluster.count(chained_graph, chain_in_tier_order))
print("VCC, declared:", vowel_cluster.count(chained_graph, reading))
```

```text
VCC, tier order: 1
VCC, declared: 0
```

`DeclaredOrder.project(members)` reads only some of the chain's members while
keeping the chain's order; the full chain is retained as `chain`. A projection
that drops the first or last member of the chain has an open edge there, so `^`
or `$` does not match at that edge: the projected scope does not start where the
chain starts.

```python
phones_only = reading.project(ItemsSelector(chain_phones.name))
vowels_only = reading.project(
    WhereSelector(ItemsSelector(chain_phones.name), Equals(Cell(kind), ("V",)))
)
two_vowels = compile_pattern(parse_pattern("{class=V} {class=V}", syntax))
first_vowel = compile_pattern(parse_pattern("^ {class=V}", syntax))
print("VCC, phones only:", vowel_cluster.count(chained_graph, phones_only))
print("adjacent vowels:", two_vowels.count(chained_graph, vowels_only))
print("vowel at start:", first_vowel.count(chained_graph, vowels_only))
```

```text
VCC, phones only: 1
adjacent vowels: 1
vowel at start: 0
```

## Bind once, ask many times

Each call on a `CompiledPattern` binds its predicates to the graph and reads the
ordering's scopes. `bind` does both once and returns a `BoundPattern` whose
views take no graph or ordering. A `BoundOrdering` reads one ordering's scopes
once and can be shared by any number of patterns bound to the same graph
object.

```python
shared = BoundOrdering(graph, in_order)
for text in ("{class=V}", "{seg=t} $", "{class=C}+"):
    bound = compile_pattern(parse_pattern(text, syntax)).bind(graph, shared)
    print(f"{text}:", bound.count(), [(m.start, m.end) for m in bound.spans().matches])
```

```text
{class=V}: 2 [(1, 2), (4, 5)]
{seg=t} $: 1 [(5, 6)]
{class=C}+: 5 [(0, 1), (2, 3), (2, 4), (3, 4), (5, 6)]
```

A bound pattern holds its graph for as long as the handle lives.

## Open edges

Every view accepts `open_right=True` for a scope that may still grow at its end,
as a stream being annotated does. The result is an `OpenPatternResult`: the
answer computed only from matches that later items cannot change, and one
`pending_from` watermark per scope, the earliest start position whose match is
still undecided, or `None` when nothing is pending.

```python
consonant_vowel_open = compile_pattern(parsed).count(graph, in_order, open_right=True)
print(consonant_vowel_open.result, consonant_vowel_open.pending_from)
```

```text
2 (5,)
```

The last phone is a consonant, so a vowel arriving next would start a match at
position 5. `DeclaredOrder(..., open_left=True)` declares the other edge open,
as a projection that drops the chain's head does.

## Patterns inside selections

`SequenceSelector(ordering, pattern)` is a selector whose result is the
pattern's focus, so a pattern can feed set algebra, `WhereSelector`, or a walk
like any other selection.

```python
from tiergraph import SequenceSelector

after_consonant_selector = SequenceSelector(
    in_order, parse_pattern("{class=V} / {class=C} _", syntax)
)
print(segs(evaluate_selection(graph, after_consonant_selector).nodes))
```

```text
['ae', 'ae']
```

## Matching paths through a lattice

A lattice is a dependency graph whose root-to-sink paths are the alternatives,
such as the readings a recognizer proposes. `match_lattice` matches a compiled
pattern against the token string of every complete path. The lattice is a
`PathPlan` (see [Folding](folding.md#preparing-a-path-plan)), and an `Emissions`
value says which tokens each item emits; `Emissions.from_attribute` reads them
from an item attribute, and `Emissions.bind` takes them from a mapping. Inside
the pattern, `.` in a predicate names the token itself.

```python
from tiergraph import (
    COUNTING,
    AttributeValuation,
    ChildCombination,
    Determinize,
    Emissions,
    FoldDeclaration,
    FoldTransition,
    PathPlan,
    Unambiguous,
    match_lattice,
)

spoken = document("urn:example:lattice", prefix="ex")
spoken.attributes({"token": "string"})
states = spoken.tier(
    "states",
    (
        item("start", token="ten"),
        item("o", token="o"),
        item("oh", token="oh"),
        item("zero", token="zero"),
        item("end", token="five"),
    ),
    item_type="state",
    membership="state-membership",
)
follows = spoken.link(
    "next",
    states,
    states,
    ((0, 1), (0, 2), (0, 3), (1, 4), (2, 4), (3, 4)),
    acyclic=True,
)
lattice = spoken.build()
token = spoken.qname("token")
paths = PathPlan.prepare(
    FoldDeclaration(
        "paths",
        lattice,
        AttributeValuation("token", token, (states.name,)),
        COUNTING,
        lambda _value, _label: 1,
        (FoldTransition(follows.name, ChildCombination.OR),),
        roots=(states.ref(0),),
    )
)
lattice_syntax = PredicateSyntax.for_graph(lattice, default_prefix="ex")
emissions = Emissions.from_attribute(paths, token)
spoken_o = match_lattice(
    emissions,
    compile_pattern(parse_pattern("{.=ten} ({.=o} | {.=oh}) {.=five}", lattice_syntax)),
)
print("paths:", paths.evaluate().value)
print("some path matches:", spoken_o.exists())
print("matching paths:", spoken_o.count(Unambiguous()))
print("every path matches:", spoken_o.all_paths(Unambiguous()))
print(
    "on a matching path:",
    [
        lattice.tiers[0].items[node.reference.index].durable_id
        for node in spoken_o.on_accepting_path().nodes
        if isinstance(node.reference, ItemRef)
    ],
)
```

```text
paths: 3
some path matches: True
matching paths: 2
every path matches: False
on a matching path: ['start', 'o', 'oh', 'end']
```

`count` and `all_paths` take an `AmbiguityPolicy`. `Unambiguous()` requires
that no token string has two accepting runs of the pattern, and refuses with a
witness when one does. `Determinize(max_states)` counts any pattern by building
the deterministic automaton lazily, up to the declared number of states.

```python
from tiergraph import Refusal

either_way = match_lattice(
    emissions,
    compile_pattern(parse_pattern("(. | {.=ten} {.=o}) .*", lattice_syntax)),
)
try:
    either_way.count(Unambiguous())
except Refusal as error:
    print(str(error).split(":")[0])
print("determinized:", either_way.count(Determinize(16)))
```

```text
pattern is ambiguous
determinized: 3
```

### Scoring whole outputs

A pattern asks which paths match. `OutputPlan` asks how much of the lattice's
mass each of a few complete outputs carries, under the plan's own semiring. It
compares whole emitted token strings with a finite set of candidates the caller
supplies, and reports a residual for every path whose output is none of them.

```python
from tiergraph import OutputPlan

masses = OutputPlan.prepare(
    paths, emissions, (("ten", "o", "five"), ("ten", "oh", "five"))
).masses()
print("candidates:", masses.per_candidate, "residual:", masses.residual)
```

```text
candidates: (1, 1) residual: 1
```

Under `COUNTING` the masses are path counts: one path says each candidate, and
the `zero` path is the residual. Under `LOG_PROBABILITY` they are probability
masses, and `conditioned` and `item_marginals` restrict the plan to the paths
that produce one candidate.

## JSON forms

`pattern_to_data` and `pattern_loads` write and read a pattern as strict tagged
JSON, and `ordering_to_data` writes an ordering. These are the forms the command
line reads, and the forms a `SequenceSelector` takes inside selector JSON.

```python
from tiergraph.match import ordering_to_data, pattern_loads, pattern_to_data
import json

print(json.dumps(ordering_to_data(in_order)))
print(pattern_loads(json.dumps(pattern_to_data(parsed))) == parsed)
```

```text
{"order": "tier", "tier": {"namespace": "urn:example:patterns", "local_name": "phones"}}
True
```

## From the command line

`tiergraph match GRAPH --pattern TEXT --ordering JSON VIEW` runs one view over
a stored graph, where `VIEW` is `exists`, `focus`, `spans`, or `count`.
`--prefix` sets the default prefix for names in the pattern text, the ordering
is the JSON above, and `--limit` bounds `spans`. With the graph above saved as
`patterns.json`:

```console
$ tiergraph match patterns.json --prefix ex \
    --pattern '{class=C} {class=V}' \
    --ordering '{"order":"tier","tier":{"namespace":"urn:example:patterns","local_name":"phones"}}' \
    count
{
  "count": 2
}
```

`--request FILE` reads the same choices from one JSON request instead, and
`--max-steps` sets a work budget; see [Work budgets](work-budgets.md) and the
[CLI reference](../reference/cli.md).
