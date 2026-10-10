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

## Every link is accounted for

Pass `check_links=True` to `graph.edit()` to audit every edit against a complete
before-and-after link ledger. The check covers relation endpoints, facts,
layers, boundary values, attributes, blob attachments, and nested-graph
references. It applies to plain and journaled graph editors, clock-aware
editors, and `commit_path()`.

Use `base.edit(check_links=True)` for a plain checked session, or
`base.edit(journal=Journal(), check_links=True)` when the journal record is also
part of the account. Use `profile.edit(check_links=True)` for a checked clock
session, and pass `check_links=True` to `commit_path()` to audit its complete
derived-edit report.

Each link from the edited region must be carried unchanged, re-pointed through
the edit correspondence, or named with its content in a detachment report. A
checked operation that withdraws a link without such a report is refused, even
when the operation explicitly requests the content change. Plain edits verify
the complete partition, and journaled edits also verify their exact inverse. A
refused edit must leave the editor unchanged. A mismatch raises an internal
consistency error at the operation boundary. This is an opt-in diagnostic: the
ordinary editor allocates no ledger state and performs no ledger work.

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

## Run moves, sister shifts, and the editing algebra

The editing basis is insert, delete, substitute, shift left, shift right, and
swap. The existing graph operations spell that basis without adding a new cost
kind:

- `insert_item()` is the rewrite `""@boundary -> X` (epsilon to an item);
- `remove_item()` is `X -> ""@boundary`;
- `replace_item()` is `X -> Y`;
- `move_run()` cuts one named hole and inserts the held hole at another
  boundary;
- `shift()` is that move across an adjacent sister-container boundary; and
- `swap_runs()` is `A B -> B A`, with `swap_items()` as its `(1, 1)` case.

The empty sequence is epsilon, spelled `""`. It is not `None`, which means a
missing value, and it is not the empty language. `ItemRun(tier, start, 0)` is
the corresponding zero-width boundary in the operational API.

`cut(run)` and `insert_held(held, at)` are the two views of one move. A held run
keeps its items, identities, carried references, facts, and provenance. Freezing
an unresolved cut realizes the ordinary delete and therefore has the same live
dependency refusals as `remove_items()`. `move_run()` records the cut and insert
as one journal event with one inverse. `swap_runs()` exchanges unequal adjacent
runs as blocks; for separated runs, the middle remains in place. Either side
may be a zero-width run. A run or item swap is one atomic restructure: if its
complete result is invalid, the editor remains unchanged. Moving or swapping a
timed item tier still requires a clock rebinding policy.

A phrase-break correction uses the same operation as a syllable-boundary
correction. In this tested shape, the first phrase contains words `w0 w1 w2`
and its right sister contains `w3 w4`:

```text
shifted = utterance.shift(
    ItemRef(phrase_tier, 0),
    1,
    "right",
    phrase_words,
)
```

The result is `P0[w0 w1] P1[w2 w3 w4]`. A left shift takes the first children
of the right container and appends them to the left container. The containers
must be adjacent items on one tier and meet at one contiguous child-tier seam.
They must have the same parent in every ordered-containment relation that
contains either container, or both be root containers. The named ordered
polyadic containment must give both containers their child sequences. A shift
accepts only a nonempty edge run smaller than the source membership. It never
wraps at a tier seam.

Cross-parent resyllabification is explicit:
`utterance.shift(ItemRef(syllable_tier, 0), 1, "right",
syllable_segments, across_parent=True)`.

This form requires exactly one differing parent for each container and those
parents must belong to the same ordered-containment relation and be adjacent in
the shift direction. Parent assignments that already agree do not make the
path ambiguous. The same requirement continues up the ancestor chain until all
parent assignments agree or both sides reach roots.
`GraphEditor.last_yield_changes` and the journal report name every ancestor-tier
boundary whose descendant yield changed, with its previous and new child seam.
The default form refuses the same edit and names each container's parent.

Operationally, the child run and the shared zero-width sister boundary trade
places. Declaratively, the right shift is the synchronous rewrite
`P1[x Y] P2[Z] -> P1[x] P2[Y Z]`. The journal's
`SubtreeCorrespondence` is the named hole alignment. Its optional
`identity_correspondence` is present for held moves and shifts, declaring
identified equality; delete-and-add leaves that field absent and claims only
functional correspondence. The two views produce the same graph and do not
invoke the grammar engine.

Shift does not reorder or retime the child tier, or any aligned tier. It changes
only the two containment instances. A clock-bound moved boundary therefore
needs no rebinding policy when it stores no independent boundary content. A
stored value, layer fact, or durable boundary shared with another tier at any
moved container or ancestor boundary requires `keep-earlier` or
`drop-to-provisional` on the plain editor; the other tier is never dragged. A
clock-aware editor verifies that every affected container or ancestor boundary
meets the old child seam on the common clock, then places those boundaries on
the new seam's existing child time and revalidates the complete profile. It
creates no new clock times. If any affected container or ancestor tier is timed
but the child tier is untimed, the clock-aware editor refuses because the new
child seam has no clock position. `drop-to-provisional` removes independently
stored boundary values and layer facts, reports them in the detachment report,
and leaves them for later realignment. Durable relations remain attached to the
moved boundary under either named policy; the policy acknowledges the endpoint's
new interpretation and never drags the other tier. Container-emptying merge is
intentionally not part of this operation because it needs a separate merge
policy and inverse.

Deleting two containers and inserting new containers with the shifted
memberships is the alternative construction. It is functionally equal to the
held shift but not identified equal. Use it only when the containers genuinely
are new. Shift and run swap use the existing `swap_items` cost unit; `move_run`
uses `move_item`. Consequently a shift is no more expensive than delete plus
insert under the default table, and is strictly cheaper whenever those two
costs sum to more than the swap cost. `diff()` recognizes a single retained-ID
containment shift, including explicit cross-parent and boundary-policy forms,
and emits the shift operation.

## Splitting and merging nested containers

`split_container()` and `merge_containers()` change one named ordered
containment while leaving the child tier and every other tier in place. The
module functions return an `EditResult`; the matching `Graph` methods return a
graph, and graph, journal, and clock editors expose the same operations.

A split accepts only an interior child boundary. With `side="after"`, the
original container keeps the prefix and the new sister receives the suffix;
`side="before"` gives the prefix to the new sister. The new sister is inserted
beside the original in every shared ordered-containment parent. Declared order
is content and is never sorted. Functional correspondence maps the original
container to both results. Identified correspondence maps it only to the half
that retains its durable identity.

```text
split = utterance.split_container(
    ItemRef(phrase_tier, 0),
    2,
    phrase_words,
    Item("new-phrase"),
)
```

A merge requires adjacent same-tier sisters whose child sequences meet at one
contiguous seam. Both must have the same ordered-containment parents in the
same left-to-right order, or both must be roots. Either sister may survive;
child content is always `left.children + right.children`. The survivor keeps
its durable identity and membership identity. The other membership is retired.

Every dependency must follow the single-valued functional correspondence, be
named with `ReplacementAction.DROP`, or make the operation refuse.
`RegroupPolicies` provides per-relation, per-layer, and per-attribute actions,
plus `container_values`, `seam_content`, and `clock`. Only `FOLLOW` and `DROP`
are admitted. Disjoint removed-container attributes are carried to the
survivor; equal values coalesce; unequal values require an exact drop action.
Independent content or links on the retired seam require `seam_content=DROP`
or a declaration-specific drop action. The detachment report lists every
withdrawn item, relation, fact, and boundary value.

Dependency and content actions apply only to merge. Split refuses those fields
when they are nondefault; its only applicable regroup policy is `clock`.

Journaled regrouping records one semantic operation and its semantic inverse.
`RegroupRestoration` is the validated, change-sized inverse payload for a
merge. It retains the removed membership position and payload, routed link and
layer state, seam content, original relation counts, and removed relation
positions. The original survivor item restores its pre-merge attributes.
Patches therefore replay split and merge with no residual document delta,
including when membership instances are interleaved in document order.
`check_links=True` also audits child endpoints that cross from
one membership instance to another during a split or whose membership is
retired during a merge.

Timing changes occur only in a clock-aware editor under the operation-specific
policy. Split requires `keep-earlier`, binds the new container seam to the
selected child seam, and reports the insertion as `container split`. Merge
requires `drop-to-provisional`, withdraws only the internal seam binding, and
reports `container merge`. A missing child time, a common-clock disagreement,
clock-tier regrouping, or an invalid final clock profile refuses the edit.

The default cost table prices split and merge at 3. A partial cost table may
price a realized patch without naming both operations, but the general atom
lower bound is then zero because the omitted operation could undercut an atom
transition. Machine format version 2 is unchanged. A version 2 reader that
does not know these opcode names refuses them rather than interpreting them as
another operation; there is no compatibility alias.

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

For exact before-and-after audits, that one journal-owned provenance fact on a
moved durable child is metadata introduced by recording the edit. Exclude only
that fact when asserting that all content outside the changed containment tier
is exactly equal; plain edits add no such exception.

Facts within each layer are canonicalized at construction, so their supplied
order is not observed by any equivalence view and does not express rank. Store
rank as a declared value when it is meaningful.

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
identity. The public equivalence operations share three fixed, graph-wide
observational projections. A projection says what a comparison reads; it does
not declare a domain symmetry.

- `FUNCTIONAL` omits durable IDs, namespace prefixes, and reference spellings.
- `IDENTIFIED` also reads durable IDs. Use it to detect identity churn and to
  guard patches.
- `EXACT` reads all three and is graph equality.

Facts within each layer are canonicalized at construction, so their supplied
order is not observed by any view. Declared order is content under every view.
Per-tier declared order-insensitivity is a separate, future concept.

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
Facts and boundary values still use their declared replacement policy. A binary
or polyadic relation crossing the replaced edge instead carries automatically:
each inside endpoint follows the old-to-new correspondence, while outside
endpoints remain attached to their displaced items. Polyadic one-to-many holes
expand in place, so declared endpoint order is preserved. If an inside endpoint
has no counterpart, or a binary endpoint has several, the edit refuses and
names the relation and endpoint. `drop` explicitly removes the whole relation;
`trim` is polyadic-only and removes only the unaligned endpoints. Reports contain
only the relation or endpoints actually removed. A carried polyadic relation
also refuses by relation and endpoint if flattening or trimming would violate
its local declaration invariants; `drop` is the fallback for that case. A fact
whose subject is a carried relation follows it; facts on a dropped relation are
reported.

The returned `EditResult.report` snapshots withdrawn items, relation instances,
facts, and individual boundary values in graph order with their original
references and complete typed values, including durable identifiers where
present. Its `graph` field is the edited graph. `Graph.replace_subtree()` remains
a graph-returning convenience; call the module function when the report is
needed. A journal record exposes the same detached content, and its inverse
restores it. Binary and polyadic relations in the supplied donor graph are
copied only when all endpoints belong to the supplied subtree. A crossing donor
relation and any facts on it are not copied, but their complete content is
included in the report's separate `donor_relations` and `donor_facts` fields.
The corresponding `donor_relations` and `donor_layer` dependencies retain the
donor coordinates that explain why the content was omitted.

Correspondence is opt-in through `ReplacementPolicies`. An explicit
`SubtreeCorrespondence` records named old-to-new alignment holes. Each hole can
optionally carry `identity_correspondence`: its absence claims functional
correspondence only, while its presence declares that exactly one target in the
hole retains identity on the graph by carrying the source item's durable ID.
The target may be anonymous or already carry that ID; a different donor ID, or
the source ID on another donor item, refuses. An anonymous source requires an
anonymous target. For a split, the claim names the identified target and the
other targets are fresh. Carrying a crossing relation does not promote a
functional alignment to identity. Journal reports retain the effective
correspondence so this distinction is explicit. Stable local per-tier matching
can fill equal unmatched items, but remains functional unless the caller
explicitly declares identity. `follow` requires exactly one counterpart for
facts and boundary values, while `split` duplicates them across all declared
counterparts.
Boundary-subject facts use the same boundary correspondence as boundary values.
Policies can differ by relation or layer. `swap_subtrees()` also returns an
`EditResult`, composes two replacements, reports boundary values absent from the
final graph, and refuses equal or nested roots. A swap preserves each moved
item's identity, so its per-side replacement policies cannot declare identity
correspondence. `Graph.swap_subtrees()` keeps returning only the graph.
Clock-aware replacement is available, but the graph-level subtree swap has no
clock policy; do not use that convenience operation on clock-bound tiers.

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
from typing import NamedTuple, Protocol

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
    Journal,
    Layer,
    LayerFact,
    LayerName,
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


class RetimeOperation(Protocol):
    """Describe the primitive invoked only after row validation succeeds."""

    def __call__(
        self,
        profile: ClockProfile,
        tier: QualifiedName,
        alignment: Sequence[int],
        *,
        journal: Journal | None = None,
    ) -> Graph: ...


def import_unit_timings(
    profile: ClockProfile,
    tier: QualifiedName,
    span_attribute: QualifiedName,
    alignment: Sequence[AlignedUnit],
    *,
    journal: Journal | None = None,
    retime_operation: RetimeOperation = retime,
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
    return retime_operation(profile, tier, boundaries, journal=journal)


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
            "binding-start",
        ),
        RelationInstance(
            bindings,
            DurableBoundaryRef(DurableItemRef("beta"), BoundarySide.BEFORE),
            DurableBoundaryRef(DurableItemRef("sample-2"), BoundarySide.BEFORE),
            "binding-beta",
        ),
        RelationInstance(
            bindings,
            DurableBoundaryRef(DurableItemRef("gamma"), BoundarySide.BEFORE),
            DurableBoundaryRef(DurableItemRef("sample-4"), BoundarySide.BEFORE),
            "binding-gamma",
        ),
        RelationInstance(
            bindings,
            DurableBoundaryRef(tokens.name, BoundarySide.AFTER),
            DurableBoundaryRef(samples, BoundarySide.AFTER),
            "binding-end",
        ),
    ),
    layers=(
        Layer(
            LayerName(timeline_namespace, "alignment"),
            (
                LayerFact(
                    DurableItemRef("beta"),
                    AttributeValue(source_span, XsdType.STRING, "5:9"),
                ),
            ),
        ),
    ),
)
profile = ClockProfile(timed, samples, bindings, None, unit)
timing_journal = Journal(stage="alignment")
shuffled_alignment = (
    AlignedUnit("gamma", "9:14", 4, 6),
    AlignedUnit("alpha", "0:5", 0, 1),
    AlignedUnit("beta", "5:9", 1, 4),
)
retimed = import_unit_timings(
    profile,
    tokens.name,
    source_span,
    shuffled_alignment,
    journal=timing_journal,
)
checked_profile = ClockProfile(retimed, samples, bindings, None, unit)
assert tuple(
    checked_profile.clock_index(BoundaryRef(tokens.name, index)) for index in range(4)
) == (0, 1, 4, 6)
assert [record.operation for record in timing_journal.records] == ["set_endpoints"]
assert tuple(item.attributes for item in retimed.tiers[0].items) == tuple(
    item.attributes for item in timed.tiers[0].items
)
assert tuple(
    (relation.declaration, relation.durable_id, relation.attributes)
    for relation in retimed.relations
) == tuple(
    (relation.declaration, relation.durable_id, relation.attributes)
    for relation in timed.relations
)
assert retimed.layers == timed.layers
assert timing_journal.to_patch().invert().apply(retimed) == timed


def assert_timing_refusal(
    tier: QualifiedName,
    rows: Sequence[AlignedUnit],
    message: str,
) -> None:
    """Require a row refusal before the adapter invokes ``retime()``."""

    def unexpected_retime(
        profile: ClockProfile,
        tier: QualifiedName,
        alignment: Sequence[int],
        *,
        journal: Journal | None = None,
    ) -> Graph:
        raise AssertionError("retime was called before row validation finished")

    refused_journal = Journal()
    try:
        import_unit_timings(
            profile,
            tier,
            source_span,
            rows,
            journal=refused_journal,
            retime_operation=unexpected_retime,
        )
    except ValueError as error:
        assert message in str(error)
    else:
        raise AssertionError("invalid alignment rows were accepted")
    assert not refused_journal.records


assert_timing_refusal(
    QualifiedName(timeline_namespace, "unknown-tier"),
    shuffled_alignment,
    "unknown timed tier",
)
assert_timing_refusal(
    tokens.name,
    (
        AlignedUnit("alpha", "0:5", 0, 1),
        AlignedUnit("alpha", "0:5", 1, 4),
        AlignedUnit("gamma", "9:14", 4, 6),
    ),
    "cover each unit ID and source span once",
)
assert_timing_refusal(
    tokens.name,
    (
        AlignedUnit("gamma", "9:14", 4, 6),
        AlignedUnit("alpha", "0:5", 0, 1),
        AlignedUnit("beta", "4:9", 1, 4),
    ),
    "cover each unit ID and source span once",
)
assert_timing_refusal(
    tokens.name,
    (
        AlignedUnit("alpha", "0:5", 0, 1),
        AlignedUnit("beta", "5:9", 1, 4),
    ),
    "cover each unit ID and source span once",
)
assert_timing_refusal(
    tokens.name,
    (
        AlignedUnit("alpha", "0:5", 0, 1),
        AlignedUnit("beta", "5:9", 2, 4),
        AlignedUnit("gamma", "9:14", 4, 6),
    ),
    "share a boundary",
)
assert_timing_refusal(
    tokens.name,
    (
        AlignedUnit("unknown", "0:5", 0, 1),
        AlignedUnit("beta", "5:9", 1, 4),
        AlignedUnit("gamma", "9:14", 4, 6),
    ),
    "cover each unit ID and source span once",
)
assert_timing_refusal(
    tokens.name,
    (
        AlignedUnit("alpha", "stale", 0, 1),
        AlignedUnit("beta", "5:9", 1, 4),
        AlignedUnit("gamma", "9:14", 4, 6),
    ),
    "cover each unit ID and source span once",
)
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
their unavoidable atom count at the least declared transition cost. Realized
primitive edits and generated graph cases exercise the resulting lower bound,
which is deliberately weak for rewiring and reordering:
`set_endpoints`, moves, and swaps can project to zero. A partial cost table also
uses zero for the general lower bound rather than requiring costs unrelated to
the realized upper-bound script. Because one container split or merge can
change several retained atoms, a complete table caps the general atom bound at
the cheaper regroup operation. This global cap is conservative even for graph
pairs not produced directly by regrouping.
An opaque `value_substitution` callback does not expose the global triangle
information needed for this comparison, so general-graph value substitutions
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
    normalize: Callable[[Graph], Graph]


surface = QualifiedName(tokens.name.namespace, "surface")


def item_text(graph: Graph) -> tuple[str, ...]:
    """Read this application's ordered text pieces."""
    return tuple(
        value.lexical
        for item in graph.tiers[0].items
        for value in item.attributes
        if isinstance(value, AttributeValue) and value.name == surface
    )


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


def normalize_graph(graph: Graph, normalize: Callable[[str], str]) -> Graph:
    """Normalize surface values without changing tiers, IDs, or references."""
    return replace(
        graph,
        tiers=tuple(
            replace(
                tier,
                items=tuple(
                    replace(
                        item,
                        attributes=tuple(
                            replace(value, lexical=normalize(value.lexical))
                            if isinstance(value, AttributeValue)
                            and value.name == surface
                            else value
                            for value in item.attributes
                        ),
                    )
                    for item in tier.items
                ),
            )
            for tier in graph.tiers
        ),
    )


def value_cost(
    normalize: Callable[[str], str],
) -> Callable[[Attribute, Attribute], int]:
    """Price scalar string substitutions under one text normalization."""

    def substitute(before: Attribute, after: Attribute) -> int:
        """Return zero exactly when this view identifies the values."""
        if (
            isinstance(before, AttributeValue)
            and isinstance(after, AttributeValue)
            and before.name == after.name == surface
        ):
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
        text_projection("strict", item_text, join=join_pieces),
        costs_for(identity),
        lambda graph: normalize_graph(graph, identity),
    ),
    "presentation": TextViewCosts(
        text_projection(
            "presentation", item_text, join=join_pieces, transform=presentation
        ),
        costs_for(presentation),
        lambda graph: normalize_graph(graph, presentation),
    ),
    "whitespace-insensitive": TextViewCosts(
        whitespace_insensitive_projection(item_text, join=join_pieces),
        costs_for(without_whitespace),
        lambda graph: normalize_graph(graph, without_whitespace),
    ),
    "format-control-insensitive": TextViewCosts(
        format_control_insensitive_projection(item_text, join=join_pieces),
        costs_for(without_format_controls, controls_are_free=True),
        lambda graph: normalize_graph(graph, without_format_controls),
    ),
}

text_base = replace(
    base,
    tiers=(
        replace(
            base.tiers[0],
            items=tuple(
                replace(
                    item,
                    attributes=(
                        AttributeValue(surface, XsdType.STRING, item.durable_id or ""),
                    ),
                )
                for item in base.tiers[0].items
            ),
        ),
    ),
    attribute_declarations=(
        AttributeDeclaration(surface, AttributeDomain.ITEM, XsdType.STRING),
    ),
)


def replace_first_text(graph: Graph, text: str) -> Graph:
    """Replace the first surface reading without changing its identity."""
    first = graph.tiers[0].items[0]
    return replace(
        graph,
        tiers=(
            replace(
                graph.tiers[0],
                items=(
                    replace(
                        first,
                        attributes=(AttributeValue(surface, XsdType.STRING, text),),
                    ),
                    *graph.tiers[0].items[1:],
                ),
            ),
            *graph.tiers[1:],
        ),
    )


upper = replace_first_text(text_base, "ALPHA")
spaced = replace_first_text(text_base, "al pha")
controlled = replace_first_text(text_base, "al\u200cpha")
assert views["strict"].projection.distance(text_base, upper) == 5
assert views["presentation"].projection.distance(text_base, upper) == 0
assert views["whitespace-insensitive"].projection.distance(text_base, spaced) == 0
assert (
    views["format-control-insensitive"].projection.distance(text_base, controlled) == 0
)
assert views["strict"].costs.operation("insert_item", tokens.name) == 2
assert (
    views["format-control-insensitive"].costs.operation("insert_item", format_controls)
    == 0
)

plain_value = AttributeValue(surface, XsdType.STRING, "alpha")
upper_value = AttributeValue(surface, XsdType.STRING, "ALPHA")
different_value = AttributeValue(surface, XsdType.STRING, "omega")
controlled_value = AttributeValue(surface, XsdType.STRING, "al\u200cpha")
assert views["strict"].costs.substitute_value(plain_value, controlled_value) == 1
assert views["presentation"].costs.substitute_value(plain_value, upper_value) == 0
assert views["presentation"].costs.substitute_value(plain_value, different_value) == 1
assert (
    views["format-control-insensitive"].costs.substitute_value(
        plain_value, controlled_value
    )
    == 0
)
```

The graph comparison uses those same readers and costs. Normalization changes
only the declared surface value, so the durable item IDs and the cross-tier
relation remain intact:

```python
from tiergraph import graph_distance

control_type = QualifiedName(tokens.name.namespace, "format-control")
control_membership = QualifiedName(tokens.name.namespace, "format-control-membership")
control_link = QualifiedName(tokens.name.namespace, "token-format-control")
control_item = Item("control-1", (AttributeValue(surface, XsdType.STRING, "\u200c"),))
control_tier = Tier(
    TierDeclaration(format_controls, "Format controls"), (control_item,)
)
two_tier = Graph(
    text_base.namespaces,
    (*text_base.tiers, control_tier),
    (
        *text_base.relation_declarations,
        SimpleRelationDeclaration(control_membership, format_controls, control_type),
        BipartiteRelationDeclaration(control_link, tokens.item_type, control_type),
    ),
    relations=(
        RelationInstance(
            control_link,
            DurableItemRef("alpha"),
            DurableItemRef("control-1"),
        ),
    ),
    attribute_declarations=text_base.attribute_declarations,
)
upper_two_tier = replace_first_text(two_tier, "ALPHA")

strict_source = views["strict"].normalize(two_tier)
strict_target = views["strict"].normalize(upper_two_tier)
presentation_source = views["presentation"].normalize(two_tier)
presentation_target = views["presentation"].normalize(upper_two_tier)
assert presentation_source.relations == two_tier.relations
assert tuple(
    item.durable_id for tier in presentation_source.tiers for item in tier.items
) == (
    "alpha",
    "beta",
    "gamma",
    "control-1",
)
strict_distance = graph_distance(strict_source, strict_target, views["strict"].costs)
presentation_distance = graph_distance(
    presentation_source,
    presentation_target,
    views["presentation"].costs,
)
assert strict_distance.upper > 0
assert presentation_distance.value == 0

control_present = Graph(
    text_base.namespaces,
    (control_tier,),
    (SimpleRelationDeclaration(control_membership, format_controls, control_type),),
    attribute_declarations=text_base.attribute_declarations,
)
control_absent = replace(
    control_present,
    tiers=(replace(control_tier, items=()),),
)
strict_control_distance = graph_distance(
    views["strict"].normalize(control_present),
    views["strict"].normalize(control_absent),
    views["strict"].costs,
)
free_control_distance = graph_distance(
    views["format-control-insensitive"].normalize(control_present),
    views["format-control-insensitive"].normalize(control_absent),
    views["format-control-insensitive"].costs,
)
assert strict_control_distance.value is not None
assert strict_control_distance.value > 0
assert free_control_distance.value == 0

linked_control_absent = replace(
    two_tier,
    tiers=(*two_tier.tiers[:-1], replace(control_tier, items=())),
    relations=(),
)
linked_control_distance = graph_distance(
    views["format-control-insensitive"].normalize(two_tier),
    views["format-control-insensitive"].normalize(linked_control_absent),
    views["format-control-insensitive"].costs,
)
assert linked_control_distance.value is None
assert linked_control_distance.lower == 1
assert linked_control_distance.upper == 8
```

The dedicated zero-cost declaration makes that table a pseudometric at the
raw-graph level, which `metric_violations()` reports. It is appropriate only
when the application intentionally quotients away that class. If format
controls share ordinary items with visible text, their whole-item insertion and
removal remain ordinary costs; the projection and `value_substitution` callback
still make control-only replacements free. Removing the linked control above
has a lower cost bound of one for its relation even though removal of its
control-tier item is free; the realized general-graph edit gives an upper bound
of eight. The multi-tier comparison above
normalizes copies under the selected view and retains cross-tier references
before calling `graph_distance()` with the matching table. The sequence
projection itself is not a lower-bound certificate for a general graph.

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
