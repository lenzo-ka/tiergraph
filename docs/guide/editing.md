# Editing graphs

tiergraph separates an immutable graph value from the operation that changes
it. A single operation on `Graph` publishes a new validated graph. Promotion
also returns the durable reference it created. A
`GraphEditor` collects several operations and validates when `freeze()` builds
the result. An edit journal adds per-operation validation, reports, undo and
redo, portable patches, and optional provenance. A `ClockProfile` owns the
fourth path: edits that must keep a declared timing interpretation valid.

Choose the narrowest path that carries the guarantees the application needs:

| Need | Entry point | Validation boundary | Extra state |
|---|---|---|---|
| One checked change | `graph.OP(...)` | The returned graph (and durable reference for promotion) | None |
| Several changes as one transaction | `graph.edit()` | `editor.freeze()` | Displacement only |
| Undo, reports, annotations, or patches | `graph.edit(journal=Journal(...))` | Every operation | Opt-in journal |
| Timing-valid structural changes | `profile.edit(POLICY, journal=...)` | Every operation and the clock profile | Clock reports, and an optional journal |

The plain editor deliberately stays small. Opening it does not allocate journal
records, inverse data, callbacks, or annotations. Use a journal only when those
features are part of the job. After an abandonment-capable subtree operation,
the editor exposes the report as `last_detachment` while preserving fluent
method returns.

## One change or a transaction

The frozen and mutable carriers expose the same primitive editing vocabulary.
The frozen form is convenient for one operation. The mutable form avoids
constructing and validating an intermediate graph after every operation.

```python
from tiergraph import Item, Journal
from tiergraph.build import document

builder = document("urn:tiergraph:example:edit", prefix="edit")
tokens = builder.tier(
    "tokens",
    ("alpha", "beta", "gamma"),
    item_type="token",
    membership="token-membership",
)
base = builder.build()

one_change = base.move_item(tokens.ref(2), 0)

editor = base.edit()
editor.move_item(tokens.ref(2), 0)
editor.insert_item(tokens.name, 1, Item("delta"))
transaction = editor.freeze()

print([item.durable_id for item in one_change.tiers[0].items])
print([item.durable_id for item in transaction.tiers[0].items])
```
```text
['gamma', 'alpha', 'beta']
['gamma', 'delta', 'alpha', 'beta']
```

An operation that refuses leaves its editor unchanged. Checks local to an
operation happen before it writes. Checks that depend on several operations,
such as acyclicity or a single-parent promise, happen when a plain editor is
frozen. A journaled or clock-aware editor validates its candidate after each
operation instead.

The primitive set covers the graph's stored model:

- declarations: positional `declare()`, strict `undeclare()`, and the derived
  `undeclare_with_contents()` cascade;
- ordered items: insert one or a run, remove one or a run, move, swap, and
  replace values;
- relation instances: positional addition, removal, and endpoint replacement;
- typed attributes: set or remove a value on every supported carrier;
- durable identities: promote or demote items, boundaries, and binary or
  polyadic relation instances;
- seals: set, shorten, or remove a seal record; and
- layers: add or remove a layer, and put or remove a fact, including an
  explicitly supplied orphan fact.

`move_item()` and `swap_items()` stay within one tier. Cross-tier changes are a
remove and insert under declarations that admit the new state. `undeclare()` is
strict: it lists current dependents and refuses while any remain. The derived
cascade removes the dependent content and declaration atomically, and becomes
undoable when it runs through a journal. The plain cascade is the module-level
`undeclare_with_contents(graph, target)` function; journaled and clock-aware
editors provide the session method.

## References, displacement, and local refusal

An `ItemRef` or `BoundaryRef` is a structural coordinate. Its index can change.
A durable reference names promoted content and can be resolved again after
coordinates move. Promote content that an external edit, patch, or interface
must keep addressing by identity.

The editor rewrites graph-owned coordinate references to keep them denoting the
same surviving content. `editor.displacement()` reports the complete mapping
from the input graph's item, boundary, binary-relation, and polyadic-relation
positions to their current positions, including departures. A journal report
also carries the displacement for its one operation.

Removal refuses locally while another live part of the graph still needs the
subject: a relation endpoint, durable boundary anchor, stored boundary value,
seal, or live layer fact. Remove or redirect the dependency first. This rule is
why ordinary editing does not silently turn a live fact into an orphan.

Orphan facts can still arrive from an older document or be supplied explicitly
to `put_fact()`. They are content: functional equivalence compares them, and
ordinary garbage collection does not erase them. Use the explicit, undoable
`prune_orphans()` edit through a journal when removal must be reversible.

## Record, preview, undo, and share a change

A `Journal` belongs to one editor session. Each successful operation records an
inverse, annotations, a lazily expanded `EditReport`, and enough retained data
to undo the change. The graph does not carry the journal.

```python
journal = Journal(author="operator", reason="reorder")
recorded = base.edit(journal=journal)
recorded.move_item(tokens.ref(2), 0)
changed = recorded.freeze()

patch = journal.to_patch()
print(patch.apply(base) == changed)
print(patch.invert().apply(changed) == base)

preview = recorded.dry_run(
    lambda candidate: candidate.swap_items(tokens.ref(0), tokens.ref(1))
)
print(len(preview), recorded.freeze() == changed, len(journal.records))

undone = recorded.undo()
print(undone.operation, recorded.freeze() == base)
recorded.redo()
print(recorded.freeze() == changed)
```
```text
True
True
1 True 1
move_item True
True
```

`dry_run()` applies and validates the callback, returns the reports it would
have produced, and rolls back by recorded inverses. It does not copy the whole
editor as a preview mechanism. `undo()` and `redo()` retain their record until
restoration succeeds, so a failed restoration does not silently lose history.

Pass annotations to `Journal(...)`, or temporarily override them with
`journal.annotate(...)`. The fields include author, reason, stage, confidence,
iteration, a tool identifier, a caller-supplied timestamp, and typed JSON
extras. The library never invents a timestamp.

When `provenance=layer_name` is set, the journal writes typed facts only on the
durably addressable subjects the operation acted on. It does not stamp every
coordinate shifted as a side effect. A journal-owned provenance fact follows
its subject and is retired inside the same undoable edit if that subject is
removed or demoted. An operation without a stable graph subject remains
attributable through its journal record. `journal.protect(layer_name)` refuses
an operation whose final candidate would change that layer or the live content
its facts describe.

Facts within a layer are a set for functional and identified equivalence; their
storage order does not express rank. Store rank as a declared value when it is
meaningful.

## Patches, programs, and differences

`journal.to_patch()` makes the applied part of a journal portable. Each patch
operation contains its executable inverse and identified before-and-after
fingerprints. Application checks the header and every transition. It therefore
refuses a different base even when that graph is functionally equivalent, and
it cannot silently retarget a durable identifier that was removed and later
reused.

Serialize patches with `patch_dumps()` and `patch_loads()`. Use
`invert_patch()` to reverse one and `compose_patches()` only when the first
patch's identified target is the second patch's identified base.

A `Program` is different: it always constructs from an empty graph and refuses
removal opcodes. `graph_to_program()` produces an exact construction program,
including orphan facts and explicit zero-length seal records. Use a patch for a
change to a known base and a program for a from-empty construction.

`diff(source, target, view)` returns a deterministic executable patch. It
aligns compatible ordered tiers, tears references down before destructive
edits, and rebuilds them afterward. Incompatible schemas use a guarded rebuild.
Applying the result reaches the target under the requested equivalence view,
but the script is not promised to be globally shortest. An empty diff is still
guarded to the source's identified fingerprint; if the graphs agree only in a
weaker view, that no-op is not an exact transition to the other graph.

`diff()` is graph-level. It validates graph structure but has no clock profile,
rebinding policy, or clock report. Build timing-sensitive structural changes
through a clock editor instead.

## Compare the result you meant to preserve

Editing, replay, convergence, and testing do not always need the same notion of
identity. The public equivalence operations share three views:

- `FUNCTIONAL` compares namespace URIs, declarations, tier and item order,
  values, globally ordered relation instances and endpoints, layers including
  orphan facts, and seal records. It ignores namespace prefixes and durable ids
  and resolves durable references to coordinates.
- `IDENTIFIED` adds the durable ids carried by the graph. Use it to detect
  identity churn and to guard patches.
- `EXACT` is graph equality, including prefixes and the spelling of structural
  versus durable references.

`equivalent()` answers the equality question. `first_difference()` names the
first differing canonical element, `abstract_form()` exposes the canonical
walk, and `fingerprint()` gives a deterministic, view-tagged digest. Exact
equality implies identified equality, which implies functional equality.

Values compare after construction-time canonicalization and with no tolerance.
Decimal spellings such as `1`, `1.0`, and `1.00` canonicalize together; binary
floating-point noise does not. Store timing observations as integral samples
or milliseconds when exact equivalence matters, and put any tolerance in a
distance cost.

## Keep clock-bound edits valid

A valid `ClockProfile` requires every non-clock tier to be completely bound or
explicitly untimed. It reads a tier's own boundary bindings; containment does
not synthesize timing for a parent tier.

Use `profile.edit("keep-earlier")` or
`profile.edit("drop-to-provisional")` for a structural change on a timed tier.
Without a named policy, insertion, removal, movement, swapping, reparenting,
and replacement on a timed tier refuse before writing.

`keep-earlier` keeps ordered boundary times in place and lets the earlier
relation instance win when anchors collide. `drop-to-provisional` collapses
affected boundaries onto the earlier clock position. Insertions under either
policy receive a zero-length provisional span. Provisional outcomes set a
durable needs-realignment fact as well as a report flag. Removal withdraws the
binding whose anchor departs and preserves the surviving binding on the merged
boundary. The clock tier itself cannot be structurally edited through a bound
session.

The ordinary `graph.edit()` path deliberately bypasses clock rebinding and
clock-profile validation. It can produce a graph for which an earlier profile
is no longer valid. The same caveat applies to graph-level `diff()`,
`apply_selected()`, and `undeclare_with_contents()`. Use the clock-owned forms
whenever timing must remain valid after each edit. The [Timing guide](timing.md)
defines the policies and report fields in detail.

## Replace structure and reconcile derived data

`replace_subtree()` keeps the named root and its incoming containment link, then
replaces the descendants reached through the declared containment relations.
Abandonment is the default: non-containment dependencies on departing
descendants are detached and listed in the returned `EditResult.report` even
without a journal. The report snapshots withdrawn items, relation instances,
facts, and individual boundary values in graph order with their original
references and complete typed values, including durable identifiers where
present. Its `graph` field is the edited graph. `Graph.replace_subtree()` remains
a graph-returning convenience; call the module function when the report is
needed. A journal record exposes the same detached content, and its inverse
restores it.
Binary relations in the supplied graph are copied only when both endpoints
belong to the supplied subtree. A binary relation that crosses that subtree edge
is not copied; the report identifies it with a `donor_relations` dependency
whose index addresses the supplied graph. Carrying such a relation requires an
external endpoint mapping and is not inferred.

Correspondence is opt-in through `ReplacementPolicies`. An explicit
`SubtreeCorrespondence` maps an old descendant to zero, one, or several new
descendants; stable local per-tier matching can fill equal unmatched items.
`follow` requires exactly one counterpart, while `split` duplicates a
dependency across all declared counterparts. Boundary-subject facts use the
same boundary correspondence as boundary values. Policies can differ by
relation or layer. `swap_subtrees()` also returns an `EditResult`, composes two
replacements, and refuses equal or nested roots. `Graph.swap_subtrees()` keeps
returning only the graph. Clock-aware replacement is available, but the
graph-level subtree swap has no clock policy; do not use that convenience
operation on clock-bound tiers.

Three higher-level operations support reconciliation without placing
application-specific structures in the kernel:

- `commit_path()` returns an `EditResult` after keeping one complete path from a
  finite `PathPlan` and the containment descendants requested by the caller.
  Its report lists the withdrawn alternatives, links, facts, and boundary
  values. Chosen item values and provenance facts remain ordinary graph
  content. A journal expands the operation into primitives, so its patch
  retains no path-plan object.
- `retime()` rebinds every boundary of one timed tier to exact integral clock
  positions while preserving binding instance identities and facts. It does
  not rewrite stored start or duration attributes.
- `contain_by_time()` rebuilds a binary or ordered-polyadic parent-child
  relation from shared integral clock spans. The default assigns a child to the
  unique parent containing its half-open-span midpoint; a callback can supply a
  different rule.

These operations are generic graph and clock operations. Export to an external
alignment format and import of an aligner's measurements belong in the caller.

### Import keyed timing in a framework

An alignment adapter should match observations to committed units before it
turns them into the positional boundary sequence accepted by `retime()`. Use a
compound key when a durable unit identifier alone does not prove which source
occurrence was aligned. The following framework-side adapter requires exact
coverage by `(unit ID, source span)`, restores graph order, checks shared
boundaries, and only then calls the generic primitive:

```python
from collections.abc import Sequence
from dataclasses import replace
from typing import NamedTuple

from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    BoundaryRef,
    BoundarySide,
    ClockProfile,
    DurableBoundaryRef,
    DurableItemRef,
    Graph,
    QualifiedName,
    RelationEndpointKind,
    RelationInstance,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    XsdType,
    retime,
)


class AlignedUnit(NamedTuple):
    """Carry one framework alignment keyed to its source occurrence."""

    unit_id: str
    source_span: str
    start: int
    end: int


def import_unit_timings(
    profile: ClockProfile,
    tier: QualifiedName,
    span_attribute: QualifiedName,
    alignment: Sequence[AlignedUnit],
    *,
    journal: Journal | None = None,
) -> Graph:
    """Validate keyed framework rows and rebind the tier in graph order."""
    member = next(
        (
            candidate
            for candidate in profile.graph.tiers
            if candidate.declaration.name == tier
        ),
        None,
    )
    if member is None:
        raise ValueError(f"unknown timed tier: {tier}")

    expected: list[tuple[str, str]] = []
    for item in member.items:
        if item.durable_id is None:
            raise ValueError("every aligned unit needs a durable ID")
        spans = tuple(
            value.lexical
            for value in item.attributes
            if isinstance(value, AttributeValue) and value.name == span_attribute
        )
        if len(spans) != 1:
            raise ValueError("every aligned unit needs one source span")
        expected.append((item.durable_id, spans[0]))

    rows = {(row.unit_id, row.source_span): row for row in alignment}
    if len(rows) != len(alignment) or set(rows) != set(expected):
        raise ValueError("alignment must cover each unit ID and source span once")
    ordered = tuple(rows[key] for key in expected)
    if not ordered:
        raise ValueError("an empty tier needs an explicit extent policy")
    if any(left.end != right.start for left, right in zip(ordered, ordered[1:])):
        raise ValueError("adjacent aligned units must share a boundary")

    boundaries = (ordered[0].start, *(row.end for row in ordered))
    return retime(profile, tier, boundaries, journal=journal)


timeline_namespace = tokens.name.namespace
samples = QualifiedName(timeline_namespace, "samples")
sample_type = QualifiedName(timeline_namespace, "sample")
sample_membership = QualifiedName(timeline_namespace, "sample-membership")
bindings = QualifiedName(timeline_namespace, "sample-bindings")
source_span = QualifiedName(timeline_namespace, "source-span")
unit = QualifiedName(timeline_namespace, "clock-unit")
assert tokens.item_type is not None

timed_tokens = replace(
    base.tiers[0],
    items=tuple(
        Item(item_id, (AttributeValue(source_span, XsdType.STRING, span),))
        for item_id, span in (("alpha", "0:5"), ("beta", "5:9"), ("gamma", "9:14"))
    ),
)
clock_tier = Tier(
    TierDeclaration(samples, "Samples"),
    tuple(Item(f"sample-{index}") for index in range(6)),
)
unbound = Graph(
    base.namespaces,
    (timed_tokens, clock_tier),
    (
        *base.relation_declarations,
        SimpleRelationDeclaration(sample_membership, samples, sample_type),
        BipartiteRelationDeclaration(
            bindings,
            tokens.item_type,
            sample_type,
            left_endpoint=RelationEndpointKind.BOUNDARY,
            right_endpoint=RelationEndpointKind.BOUNDARY,
        ),
    ),
    attribute_declarations=(
        AttributeDeclaration(source_span, AttributeDomain.ITEM, XsdType.STRING),
        AttributeDeclaration(unit, AttributeDomain.DOCUMENT, XsdType.STRING),
    ),
    attributes=(AttributeValue(unit, XsdType.STRING, "sample"),),
)
timed = replace(
    unbound,
    relations=(
        RelationInstance(
            bindings,
            DurableBoundaryRef(tokens.name, BoundarySide.BEFORE),
            DurableBoundaryRef(samples, BoundarySide.BEFORE),
        ),
        RelationInstance(
            bindings,
            DurableBoundaryRef(DurableItemRef("beta"), BoundarySide.BEFORE),
            DurableBoundaryRef(DurableItemRef("sample-2"), BoundarySide.BEFORE),
        ),
        RelationInstance(
            bindings,
            DurableBoundaryRef(DurableItemRef("gamma"), BoundarySide.BEFORE),
            DurableBoundaryRef(DurableItemRef("sample-4"), BoundarySide.BEFORE),
        ),
        RelationInstance(
            bindings,
            DurableBoundaryRef(tokens.name, BoundarySide.AFTER),
            DurableBoundaryRef(samples, BoundarySide.AFTER),
        ),
    ),
)
profile = ClockProfile(timed, samples, bindings, None, unit)
timing_journal = Journal(stage="alignment")
retimed = import_unit_timings(
    profile,
    tokens.name,
    source_span,
    (
        AlignedUnit("gamma", "9:14", 4, 6),
        AlignedUnit("alpha", "0:5", 0, 1),
        AlignedUnit("beta", "5:9", 1, 4),
    ),
    journal=timing_journal,
)
checked_profile = ClockProfile(retimed, samples, bindings, None, unit)
assert tuple(
    checked_profile.clock_index(BoundaryRef(tokens.name, index)) for index in range(4)
) == (0, 1, 4, 6)
assert [record.operation for record in timing_journal.records] == ["set_endpoints"]
```

The row order is deliberately unrelated to tier order. Unknown, duplicate,
stale-span, missing, and noncontiguous rows refuse before `retime()` changes the
graph. If the framework also derives parent-child structure from the new
timings, construct a `ClockProfile` over the returned graph and pass it to
`contain_by_time()`. Keep source decoding, time-unit conversion, tolerance, and
the choice of compound key in the framework; they are not graph semantics.

## Edit a materialized selection

`apply_selected()` accepts a selector, a `NodeSet`, or exhaustive pattern
spans. It materializes the targets once, visits them in reverse canonical order,
and remaps every original target through the displacement caused by earlier
callbacks. Shared items from overlapping spans are edited once. A callback
receives the same editor each time, and the result is published only after one
final validation.

One optional work budget covers selection, span materialization, and one step
per callback. The operation is atomic from the caller's point of view, but it
is deliberately graph-level: it records no journal and applies no clock
rebinding policy. If either is required, materialize the selection and perform
the equivalent operations through the appropriate journaled or clock editor.

## Measure edit distance

`graph_distance()` and its `distance()` synonym use a declared `CostTable`.
Costs cover primitive kinds, per-declaration overrides, value substitution, and
boundary displacement. Inverse pairs have equal costs. With positive costs for
every operation visible in the selected view, an exact result is a metric on
that view's equivalence classes.

Every call returns a `DistanceInterval`. Exact results set `exact=True`, with
equal lower and upper bounds and the exact distance available as `value`.
Independent ordered tiers can receive an exact weighted sequence result.
Ordered trees and contiguous segmentations also have exact engines for their
declared operation models. For general graphs with crossing or non-nesting
relations, the result remains an interval even if its endpoints happen to meet.
Its `lower_bound_method` names the incidence-free `atom-multiset` relaxation;
`method` names the realized diff or rebuild used for the upper bound. The upper
bound is valid, not promised to be close to optimal.

The atom relaxation keeps complete declaration and item payloads, relation
payloads without endpoints, boundary values without addresses, facts without
subjects, and layer, seal, and other carrier-value atoms. It ignores item order,
endpoint incidence, and fact attachment. Small unmatched atom sets use
minimum-cost assignment over direct one-atom transitions and
delete-then-insert paths. Large unmatched sets avoid cubic assignment and use
their unavoidable atom count at the least declared transition cost. This makes
the result sound but deliberately weak for rewiring and reordering:
`set_endpoints`, moves, and swaps can project to zero. A partial cost table also
uses zero for the general lower bound rather than requiring costs unrelated to
the realized upper-bound script.
An opaque `value_substitution` callback does not expose the global triangle
information needed for a sound comparison, so general-graph value substitutions
relax to zero when a custom callback is active. Exact ordered-tier distance
still uses the callback directly.

Text projections can define strict, presentation, whitespace-insensitive, or
format-control-insensitive readings while a caller supplies its own joining
policy. They are standalone sequence distances and do not contribute a general
graph lower bound. Zero-cost projected characters are compatible with a
pseudometric for that projection; they do not make the exact positive-cost
graph distance a metric on a finer view.

### Build costs for text views

Keep the graph equivalence view separate from application presentation views.
A small application record can pair each sequence projection with a graph cost
table that applies the same normalization to attribute substitutions. This
example also raises item insertion and removal costs on the token declaration,
and makes insertion and removal free only on a dedicated format-control tier:

```python
import unicodedata
from collections.abc import Callable, Iterable
from typing import NamedTuple

from tiergraph import (
    UNIT_COSTS,
    Attribute,
    CostTable,
    SequenceProjection,
    format_control_insensitive_projection,
    text_projection,
    whitespace_insensitive_projection,
)


class TextViewCosts(NamedTuple):
    """Pair one projected reading with compatible graph operation costs."""

    projection: SequenceProjection
    costs: CostTable


def item_text(graph: Graph) -> tuple[str, ...]:
    """Read this application's ordered text pieces."""
    return tuple(item.durable_id or "" for item in graph.tiers[0].items)


def identity(text: str) -> str:
    """Preserve strict text."""
    return text


def presentation(text: str) -> str:
    """Apply this application's presentation normalization."""
    return unicodedata.normalize("NFC", text).casefold()


def without_whitespace(text: str) -> str:
    """Remove the application's zero-cost whitespace class."""
    return "".join(character for character in text if not character.isspace())


def without_format_controls(text: str) -> str:
    """Remove the Unicode Cf zero-cost class."""
    return "".join(
        character for character in text if unicodedata.category(character) != "Cf"
    )


def value_cost(
    normalize: Callable[[str], str],
) -> Callable[[Attribute, Attribute], int]:
    """Price scalar string substitutions under one text normalization."""

    def substitute(before: Attribute, after: Attribute) -> int:
        """Return zero exactly when this view identifies the values."""
        if isinstance(before, AttributeValue) and isinstance(after, AttributeValue):
            return int(normalize(before.lexical) != normalize(after.lexical))
        return int(before != after)

    return substitute


def join_pieces(pieces: Iterable[str]) -> str:
    """Apply this application's no-separator joining policy."""
    return "".join(pieces)


format_controls = QualifiedName(tokens.name.namespace, "format-controls")


def costs_for(
    normalize: Callable[[str], str], *, controls_are_free: bool = False
) -> CostTable:
    """Build symmetric global costs with declaration-specific overrides."""
    declarations: dict[QualifiedName, dict[str, int]] = {
        tokens.name: {"insert_item": 2, "remove_item": 2}
    }
    if controls_are_free:
        declarations[format_controls] = {"insert_item": 0, "remove_item": 0}
    return CostTable(
        UNIT_COSTS.operations,
        declarations,
        value_substitution=value_cost(normalize),
    )


views = {
    "strict": TextViewCosts(
        text_projection("strict", item_text, join=join_pieces), costs_for(identity)
    ),
    "presentation": TextViewCosts(
        text_projection(
            "presentation", item_text, join=join_pieces, transform=presentation
        ),
        costs_for(presentation),
    ),
    "whitespace-insensitive": TextViewCosts(
        whitespace_insensitive_projection(item_text, join=join_pieces),
        costs_for(without_whitespace),
    ),
    "format-control-insensitive": TextViewCosts(
        format_control_insensitive_projection(item_text, join=join_pieces),
        costs_for(without_format_controls, controls_are_free=True),
    ),
}

upper = replace(
    base,
    tiers=(replace(base.tiers[0], items=(Item("ALPHA"), *base.tiers[0].items[1:])),),
)
spaced = replace(
    base,
    tiers=(replace(base.tiers[0], items=(Item("al pha"), *base.tiers[0].items[1:])),),
)
controlled = replace(
    base,
    tiers=(
        replace(base.tiers[0], items=(Item("al\u200cpha"), *base.tiers[0].items[1:])),
    ),
)
assert views["strict"].projection.distance(base, upper) == 5
assert views["presentation"].projection.distance(base, upper) == 0
assert views["whitespace-insensitive"].projection.distance(base, spaced) == 0
assert views["format-control-insensitive"].projection.distance(base, controlled) == 0
assert views["strict"].costs.operation("insert_item", tokens.name) == 2
assert (
    views["format-control-insensitive"].costs.operation("insert_item", format_controls)
    == 0
)

surface = QualifiedName(tokens.name.namespace, "surface")
plain_value = AttributeValue(surface, XsdType.STRING, "alpha")
controlled_value = AttributeValue(surface, XsdType.STRING, "al\u200cpha")
assert views["strict"].costs.substitute_value(plain_value, controlled_value) == 1
assert (
    views["format-control-insensitive"].costs.substitute_value(
        plain_value, controlled_value
    )
    == 0
)
```

The dedicated zero-cost declaration makes that table a pseudometric at the
raw-graph level, which `metric_violations()` reports. It is appropriate only
when the application intentionally quotients away that class. If format
controls share ordinary items with visible text, their whole-item insertion and
removal remain ordinary costs; the projection and `value_substitution` callback
still make control-only replacements free. For a multi-tier graph result,
normalize a copy under the selected view and call `graph_distance()` with the
matching table. The sequence projection itself is not a lower-bound certificate
for a general graph.

## Bound history and clean up explicitly

By default, a journal retains every applied inverse. `journal.checkpoint()`
makes the current graph the new undo base, clears applied and redo history, and
allows abandoned structures reachable only from those records to be released.
Undo beyond that boundary refuses. Pass an integer `horizon` to retain at most
that many records, or a `JournalHorizon` to bound count, conservatively
estimated retained bytes, or both. Automatic trimming advances the patch base,
so `journal.to_patch()` continues to replay from the retained base.

`graph.prune_orphans()` explicitly removes orphan facts.
`graph.compact()` prunes them and shares equal immutable values. Their
journaled forms report every removed fact and are undoable; compaction also
re-interns values retained by the journal. Neither operation runs silently in
the background. Clock journal editors do not expose cleanup operations: finish
the clock-aware session, clean the resulting graph explicitly, and construct a
new profile if more timed editing follows.

## Use the command line for scripted edits

The command line exposes the same primitive, replacement, bulk, cleanup,
patch, diff, distance, and program operations. Item and boundary targets use
TG-PATH. Other carriers use the target spellings documented by `tiergraph edit
--help` and the generated [CLI reference](../reference/cli.md).

```console
$ tiergraph edit graph.json move /items/durable/alpha --to 1 \
    -o edited.json --record change.jsonl --inverse-out undo.jsonl \
    --report report.json
$ tiergraph patch apply change.jsonl graph.json -o replayed.json
$ tiergraph diff before.json after.json --check -o change.jsonl
$ tiergraph distance before.json after.json --costs costs.json
$ tiergraph program from-graph graph.json -o construction.jsonl
```

Mutating commands write the result to standard output or an explicit output
file. `--in-place` is never implicit: it writes and validates a temporary file,
then atomically replaces the input while preserving its permissions.
`--dry-run` validates and reports without publishing the graph. `--record`,
`--inverse-out`, `--report`, caller annotations, and `--max-steps` are opt-in.
On a direct edit or patch application, caller annotations require `--record`,
`--inverse-out`, `--report`, or `--dry-run` to expose the metadata; otherwise the
command refuses them. Commands that can affect a clock-bound tier accept a
profile and require an explicit rebinding policy.
