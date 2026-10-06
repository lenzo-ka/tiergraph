# Work budgets

Queries over a graph can be supplied by someone else: a pattern typed into a
form, a grammar from a file, a selector in a request. A work budget lets the
caller say how much work one such call may do. Budgets are opt-in; with no
budget, a call runs to completion.

## Declaring a budget

`WorkBudget(steps=..., seconds=...)` declares a limit in logical steps, a
wall-clock deadline, or both. Pass it as `budget=` to any entry point in the
table below. A `WorkMeter` wraps one budget so that several calls share it, and
reports the steps they spent in total; it is intended for one thread or task at
a time.

A step budget is deterministic within a release: the same call on the same
graph spends the same steps on every machine. Deadlines use a monotonic clock,
are checked periodically, and can only refuse.

```python
from tiergraph import BudgetExhausted, WorkBudget, WorkMeter
from tiergraph.build import document, item
from tiergraph.match import Extent, TierOrder, compile_pattern, parse_pattern
from tiergraph.predicate import PredicateSyntax

builder = document("urn:example:budgets", prefix="ex")
builder.attributes({"class": "string"})
phones = builder.tier(
    "phones", tuple(item(None, attrs={"class": c}) for c in "CVCCVC" * 50)
)
graph = builder.build()
syntax = PredicateSyntax.for_graph(graph, default_prefix="ex")
pattern = compile_pattern(parse_pattern("{class=C} {class=V}", syntax))
ordering = TierOrder(phones.name)

meter = WorkMeter(WorkBudget(steps=1_000_000))
full = pattern.spans(graph, ordering, budget=meter)
pattern.count(graph, ordering, budget=meter)
print("complete:", full.extent.value, "spent some steps:", meter.spent > 0)

try:
    pattern.count(graph, ordering, budget=WorkBudget(steps=200))
except BudgetExhausted as error:
    print(error.operation, error.exhaustion.value, error.stage.name)
```

```text
complete: exhaustive spent some steps: True
pattern.count steps SEMANTICS
```

## What exhaustion does

Exhaustion raises `BudgetExhausted`, a `Refusal` at the `SEMANTICS` stage that
names the operation, which bound ran out (`Exhaustion.STEPS` or
`Exhaustion.DEADLINE`), the steps spent, and the budget. Nothing partial is
returned, with one exception: an outermost `spans` or `span_pairs` call that has
its own step budget returns the completed prefix of its witness list when that
prefix is nonempty, marked `Extent.CUT_AT_BUDGET`.

```python
cut = pattern.spans(graph, ordering, budget=WorkBudget(steps=2000))
print(cut.extent is Extent.CUT_AT_BUDGET, len(cut.matches) < len(full.matches))
```

```text
True True
```

An empty prefix, a deadline, or exhaustion inside an enclosing metered
operation refuses instead, so a cut witness list never enters a fold or another
aggregate. A fold, a path plan, a grammar entry, and a selection never return a
partial value.

A budget is separate from the output bounds each operation already has:

- `limit` on `spans` and `span_pairs` bounds the witnesses returned. Finding a
  further witness reports `CUT_AT_BOUND`; scanning to the end with exactly
  `limit` witnesses reports `EXHAUSTIVE`.
- A fold's `output_cap` bounds reported witnesses, and `FoldResult.truncated`
  says whether more existed once the complete value was computed.
- `derivation_budget` on `check_exactness` makes a certificate report
  `compared=False` when the enumeration would not fit; it never raises.
- `FoldCost` accounts for the work a fold did after the fact; it does not stop
  anything.
- A `Walk` `cap` bounds the number of relation steps.

## What each entry point charges

Steps count logical work, not time.

| Entry point | Charges |
| --- | --- |
| `evaluate_selection` | one visit per scanned carrier, plus size-based work for canonical node sets and set operations, shared with any nested `SequenceSelector` |
| `WhereSelector` predicates, `BoundPredicate.holds` and `select` | predicate decisions and relation-instance scans |
| `CompiledPattern` views, `CompiledPattern.bind`, `BoundPattern` views | NFA active sets and epsilon closures, and copied span items; a bound pattern charges its truth table at `bind`, and its views charge only the simulation |
| `span_pairs` | offset records and interval candidates |
| `LatticeMatch` methods | lattice product pairs, incidences, count entries, reverse edges, ambiguity pairs, and per-state epsilon closures |
| `FoldDeclaration.run`, `check_exactness` | carrier additions, multiplications, and witness operations times the declaration's `carrier_operation_cost`, plus provenance products, ranked candidates, copied path labels, and cycle enumeration |
| `PathPlan.evaluate`, `marginals` | each gather or scatter operation, and the size of a built-in `PATH` result |
| `OutputPlan.prepare`, `masses`, `conditioned`, `item_marginals` | candidate tokens, reachable product topology, conditioned reverse traversal, compiled path-plan work, and pooled carrier additions |
| `recognize`, `count`, `best`, `generate`, and the `ParseForest` and `TargetLattice` methods | each chart key, rule probe, and candidate state, then the folds and target materialization over the forest |

Some details follow from the table. A pattern alternation large enough to use
bounded lookahead charges one step per successor it looks up, so pruning never
charges more than the full automaton would. Budgeted lattice calls and budgeted
`OutputPlan.conditioned` bypass their caches, so the steps a call spends do not
depend on what ran before it. A carrier operation is indivisible: a budget
cannot interrupt arbitrary code in a user-supplied semiring, and the charge
lands at the surrounding checkpoint. Passing one `WorkMeter` to several entry
points records their combined work.

## What is bounded without a budget

Compiling a pattern is not metered. Its size is bounded instead: the pattern
text parser and the pattern compiler refuse deep nesting, and a compiled
pattern may hold at most `MAX_PATTERN_POSITIONS` item positions and
`MAX_PATTERN_STATES` automaton states. Regular expressions inside predicates
have the same state ceiling. These refusals are typed and do not depend on the
caller's Python stack depth. Because one position can expand into many states,
the state ceiling can refuse a pattern well below the position ceiling.

```python
from tiergraph.match import MAX_PATTERN_POSITIONS, MAX_PATTERN_STATES

print(f"{MAX_PATTERN_POSITIONS:,} positions, {MAX_PATTERN_STATES:,} states")
```

```text
100,000 positions, 1,000,000 states
```

Reading the scopes of an ordering, outside a metered selection, is an unmetered
pass over a graph whose size the reader already bounds.

## From the command line

`select`, `match`, `fold`, `discharge fold`, and the `grammar` commands
`recognize`, `count`, `best`, and `generate` take `--max-steps N`, a step
budget for the whole command. A match request file takes the same budget as
`max_steps`. The [CLI reference](../reference/cli.md) states the largest value
accepted.
