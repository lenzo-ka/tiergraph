# API reference

This page is generated from the shipped objects and the documentation manifest.
It covers 303 top-level `tiergraph` exports exactly once.

## Action

### `ActionDeclaration`

```text
ActionDeclaration(name: 'str', apply: 'ActionFunction[Carrier, Result]', associative: 'bool', idempotent: 'bool', commutative: 'bool', semimodule: 'Semimodule[object, object] | None' = None) -> None
```

Declare executable behavior and trusted normalization tolerances.

``associative``, ``idempotent``, and ``commutative`` are self-attested at
declaration time. React uses them as normalization gates but does not prove
them; a caller who wants them proved runs a law suite of its own over its
own samples, which this package does from its conformance tests rather than
from an importable surface. An optional semimodule claim is checked over
its declared finite samples before the declaration can exist.

### `DistributionWitness`

```text
DistributionWitness(name: 'str') -> None
```

Opt in to executable one-for-one equivalence certification.

The witness supplies no operations, delivery bridge, samples, or carrier.
On every one-for-one run, react extracts deliveries once with its declared
``yield_deliveries`` and requires its bound action to produce the same result
when applied one delivery at a time and as one complete batch. This
certifies the concrete recognition and carrier being executed; it does not
prove equivalence for runs that have not been executed.

### `ReactDeclaration`

```text
ReactDeclaration(name: 'str', fold: 'FoldDeclaration[Value]', yield_deliveries: 'DeliveryYield', action: 'ActionDeclaration[Carrier, Result]', normalization: 'YieldNormalization' = YieldNormalization(collapse=False, unique=False, reorder=False), mode: 'ReactMode' = <ReactMode.TRANSACTIONAL: 'transactional'>, distribution: 'DistributionWitness | None' = None) -> None
```

Bind recognition, yield, normalization, action, and react mode.

One-for-one first materializes and structurally orders the complete yield,
then calls the action separately for each recognition. It therefore costs
more calls and no less memory than transactional mode. In one-for-one mode,
supplying a ``distribution`` additionally computes the transactional result
and checks equivalence for that run. Without one, the caller gives up that
executable equivalence check and avoids computing both modes. Distribution
witnesses are refused in transactional mode, where equivalence is not a live
property.

#### `ReactDeclaration.run`

Method.

```text
ReactDeclaration.run(self, carrier: 'Carrier') -> 'dict[str, object]'
```

Recognize and apply, optionally certifying equivalence for this run.

Equivalence depends on the caller's carrier, so it is checked here and
cannot in general be decided when the declaration is constructed.

### `ReactMode`

```text
ReactMode(*values)
```

Choose per-recognition or complete-batch action application.

#### `ReactMode` members

- `ONE_FOR_ONE` = `one-for-one`
- `TRANSACTIONAL` = `transactional`

### `Semimodule`

```text
Semimodule(scalar_zero: 'Scalar', scalar_one: 'Scalar', scalar_add: 'Callable[[Scalar, Scalar], Scalar]', scalar_multiply: 'Callable[[Scalar, Scalar], Scalar]', module_zero: 'Module', module_add: 'Callable[[Module, Module], Module]', scale: 'Callable[[Scalar, Module], Module]', scalar_samples: 'tuple[Scalar, ...]', module_samples: 'tuple[Module, ...]') -> None
```

Supply operations and samples for an explicit, opt-in semimodule claim.

Merely declaring an action does not claim or check these laws; callers that
provide this optional structure must execute a semimodule law suite.

### `OrderedDelivery`

```text
OrderedDelivery(order: 'tuple[int, ...]', value: 'object') -> None
```

Pair an action value with its order in the declared structure.

### `YieldNormalization`

```text
YieldNormalization(collapse: 'bool' = False, unique: 'bool' = False, reorder: 'bool' = False) -> None
```

Declare action-preserving complete-yield transformations.

``collapse`` removes adjacent equal values in structural order and requires
an associative, idempotent action. ``unique`` keeps only the structurally
first occurrence of every JSON value and requires idempotence and
commutativity. ``reorder`` sorts by canonical JSON value and requires
commutativity.

#### `YieldNormalization.requires_complete_yield`

Property.

```text
YieldNormalization.requires_complete_yield(self) -> 'bool'
```

Report whether this policy cannot be performed by a binary merge.

#### `YieldNormalization.apply`

Method.

```text
YieldNormalization.apply(self, deliveries: 'tuple[OrderedDelivery, ...]') -> 'tuple[OrderedDelivery, ...]'
```

Normalize a complete yield after first restoring structural order.

## Blobs

### `BLOB_NAMESPACE`

Namespace for the fixed external-resource vocabulary. Current value: `urn:tiergraph:blob`.

### `BlobRef`

```text
BlobRef(sha256: 'str', size: 'int') -> None
```

Identify payload bytes by their lowercase SHA-256 digest and exact size.

### `BlobSpan`

```text
BlobSpan(offset: 'int | None', length: 'int') -> None
```

Describe a linear extent in the attached resource's declared unit.

An absent ``offset`` makes the span duration-only. The resource's
``blob:unit`` supplies the meaning of both integers, so the value does not
assume time: a unit can name characters, samples, frames, or another
resource-defined linear coordinate. Structured paths remain ordinary
relation-instance attributes in the resource schema rather than being
forced into this linear value.

### `BlobProfile`

```text
BlobProfile(graph: 'Graph') -> None
```

Read and validate external-resource descriptors and attachments.

A blob is any ordinary item carrying ``blob:sha256``. Every blob also has
a durable item identifier, byte size, lowercase media type, and absolute
schema URI. The payload bytes stay outside the graph. Media-specific
metadata remains ordinary typed attributes in the media vocabulary, so
audio, video, nested graph, transcript, key-value, and other resources all
use the same profile without core interpreting their types.

Attachments are ordinary item-to-item bipartite relation instances whose
right endpoint is a blob item. They retain the graph's declared relation
order and must have durable relation identifiers. This admits any number
of roles and attachments, including several metadata resources on one
binary resource and chains from a structural item through multiple blobs.

Author-declared metadata lives on the base item. Inspected type-specific
metadata can be recorded as ordinary layer facts in the media vocabulary,
whose layer source identifies the inspector. This profile never imports or
runs an inspector and never opens payload bytes.

#### `BlobProfile.blobs`

Method.

```text
BlobProfile.blobs(self) -> 'tuple[tuple[ItemRef, BlobRef], ...]'
```

Return blob items in tier and item order without sorting by digest.

#### `BlobProfile.attachments`

Method.

```text
BlobProfile.attachments(self, blob: 'ItemRef | DurableItemRef') -> 'tuple[tuple[RelationInstanceRef, ItemRef, BlobSpan | None], ...]'
```

Return one blob item's attachments in declared relation order.

#### `BlobProfile.required`

Method.

```text
BlobProfile.required(self) -> 'tuple[BlobRef, ...]'
```

Return distinct payload requirements in first blob-item order.

### `declare_blob_vocabulary`

```text
declare_blob_vocabulary(editor: 'GraphEditor') -> 'GraphEditor'
```

Declare the fixed blob prefix and attributes on a mutable graph editor.

The helper appends declarations and returns the same editor for chaining.
Existing declarations are not silently adopted: the editor's ordinary
duplicate-declaration refusals keep one explicit declaration event.

## Clock

### `ClockProfile`

```text
ClockProfile(graph: 'Graph', clock_tier: 'QualifiedName', binding_relation: 'QualifiedName | None', rate_attribute: 'QualifiedName | None', unit_attribute: 'QualifiedName | None', tick_attribute: 'QualifiedName | None' = None, gap_attribute: 'QualifiedName | None' = None, untimed_attribute: 'QualifiedName | None' = None, start_attribute: 'QualifiedName | None' = None, duration_attribute: 'QualifiedName | None' = None) -> None
```

Interpret ordered tier boundaries against a refined structural clock.

Every non-clock tier is either completely bound or explicitly untimed.  A
binding targets an ordinary integral kernel boundary; optional integer
boundary attributes refine it to ``(coarse tick, ordered gap)``.  Thus two
repeated point occurrences can occupy distinct structural gaps at the same
coarse tick without introducing fractional indices.

Physical time has one named unit.  It may be derived from a uniform rate,
stored independently on events, or both.  When both sources exist they must
agree exactly; disagreement is refused with the offending item named.
Without a rate, independently stored event timings admit non-uniform data
and different timings for events sharing one structural span.

The unit is declared, non-empty, string-typed, and carried on returned
timings.  It is not dimensionally enforced because stored decimal values
have no independent unit annotation within this single-document profile.

The profile remains silent on physical time for bound events with neither a
rate nor stored timing, and on all events of explicitly untimed tiers.  It
does not infer refinement: without refinement attributes each integral
clock boundary is the unrefined coordinate ``(index, 0)``.  Partial document
extents remain valid, and trailing silence still needs an explicit item.

#### `ClockProfile.from_data`

Class method.

```text
ClockProfile.from_data(cls, graph: 'Graph', data: 'object') -> 'ClockProfile'
```

Decode a strict declarative clock profile for ``graph``.

Every field is required. Optional qualified-name roles are represented
by JSON null, while the clock tier, binding relation, and unit attribute
must be qualified-name objects.

#### `ClockProfile.from_boundary_values`

Class method.

```text
ClockProfile.from_boundary_values(cls, graph: 'Graph', clock_tier: 'QualifiedName', *, tick_attribute: 'QualifiedName', gap_attribute: 'QualifiedName', unit_attribute: 'QualifiedName | None' = None, collapse_shared_boundaries: 'bool' = False) -> 'ClockProfile'
```

Derive only the clock spine from the clock tier's boundary values.

This construction path reads the ``(tick, gap)`` boundary attributes on
the clock tier's own boundaries -- exactly as the full constructor reads
them -- and yields the same :attr:`coordinates` sequence that the DOT
renderer draws as the spine. It requires neither a binding relation nor
a unit attribute, so it accepts a graph whose relations and document
attributes are empty; a unit is read only when ``unit_attribute`` is
given.

The result supports spine rendering alone. It carries no tier-to-clock
bindings, so every non-spine timing query -- :meth:`is_timed`,
:meth:`clock_index`, :meth:`refined_coordinate`, :meth:`extent`,
:meth:`structural_span`, :meth:`timing`, and :meth:`duration` -- raises
rather than returning an answer it cannot justify. Binding other tiers
to the clock genuinely needs ``graph.relations`` and remains the full
constructor's responsibility; this path never weakens that validation.

With ``collapse_shared_boundaries``, each coarse tick's trailing gap --
its closing boundary, coincident with the next tick's opening boundary
-- is folded away so the spine shows one node per occupied coordinate.
The default is off, leaving the raw boundaries and keeping every other
caller's spine byte-identical.

#### `ClockProfile.edit`

Method.

```text
ClockProfile.edit(self, rebinding: 'ClockRebindingPolicy | str | None' = None, *, journal: 'Journal | None' = None) -> 'ClockEditor | ClockJournalEditor'
```

Return an editor that keeps this clock profile valid after every edit.

Structural edits to a timed tier refuse unless ``rebinding`` names a
policy. ``keep-earlier`` keeps the tier's ordered boundary times while
items move through them, resolving an anchor collision in favor of the
earlier binding. The named collapsing policy collapses the affected
span onto its earlier clock boundary and records a tier fact saying
that the result needs realignment. Untimed tiers need no rebinding
policy. The clock tier itself cannot be structurally edited in a bound
session. A named declaration cascade may explicitly remove this clock
definition and retire the session; its graph and withdrawal reports
remain available, but later profile-aware edits refuse.

#### `ClockProfile.is_structural`

Property.

```text
ClockProfile.is_structural(self) -> 'bool'
```

Report whether this profile derives only a renderable clock spine.

#### `ClockProfile.rate`

Property.

```text
ClockProfile.rate(self) -> 'Decimal | None'
```

Return ticks per declared unit, or ``None`` for an uncalibrated clock.

#### `ClockProfile.unit`

Property.

```text
ClockProfile.unit(self) -> 'str'
```

Return the declared physical timing unit.

#### `ClockProfile.coordinates`

Property.

```text
ClockProfile.coordinates(self) -> 'tuple[ClockCoordinate, ...]'
```

Return the profile's validated refined clock coordinates in order.

#### `ClockProfile.is_timed`

Method.

```text
ClockProfile.is_timed(self, tier: 'QualifiedName') -> 'bool'
```

Report whether a tier chose complete clock binding.

#### `ClockProfile.clock_index`

Method.

```text
ClockProfile.clock_index(self, boundary: 'BoundaryRef') -> 'int'
```

Return the integral clock-tier boundary bound to one tier boundary.

#### `ClockProfile.refined_coordinate`

Method.

```text
ClockProfile.refined_coordinate(self, boundary: 'BoundaryRef') -> 'ClockCoordinate'
```

Return the coarse tick and ordered gap bound to one tier boundary.

#### `ClockProfile.extent`

Method.

```text
ClockProfile.extent(self, tier: 'QualifiedName') -> 'tuple[ClockCoordinate, ClockCoordinate]'
```

Return a timed tier's possibly partial refined clock extent.

#### `ClockProfile.structural_span`

Method.

```text
ClockProfile.structural_span(self, tier: 'QualifiedName', index: 'int') -> 'tuple[ClockCoordinate, ClockCoordinate]'
```

Return an event span between refined integral coordinates.

#### `ClockProfile.timing`

Method.

```text
ClockProfile.timing(self, tier: 'QualifiedName', index: 'int') -> 'PhysicalTiming | None'
```

Return stored timing or exactly representable coarse-tick timing.

Rate-derived physical timing uses only coarse ticks.  Ordered gaps are
structural, so a real gap-only span derives zero physical duration.
When a tick/rate ratio has no finite Decimal representation, this method
refuses it; :meth:`duration` retains the exact ratio in all cases.
Explicitly untimed tiers consistently return ``None`` with or without a
document rate.

#### `ClockProfile.has_uniform_rate`

Property.

```text
ClockProfile.has_uniform_rate(self) -> 'bool'
```

Report whether legacy exact coarse-tick durations are available.

#### `ClockProfile.duration`

Method.

```text
ClockProfile.duration(self, tier: 'QualifiedName', index: 'int') -> 'tuple[int, Decimal]'
```

Return the legacy coarse-tick span and rate when a rate exists.

### `ClockEditor`

```text
ClockEditor(profile: 'ClockProfile', rebinding: 'ClockRebindingPolicy | str | None' = None) -> 'None'
```

Edit one graph while preserving a declared clock profile.

The editor validates both the graph and the clock profile after every
operation. Structural edits on timed tiers are atomic: a refusal leaves the
editor's graph, reports, and profile unchanged. Successful timed-tier edits
append a :class:`ClockEditReport`; untimed edits need no clock report. A
named declaration cascade may explicitly remove the clock definition and
end the profile-aware session; its graph and reports remain readable, while
later profile-aware operations refuse.

#### `ClockEditor.profile`

Property.

```text
ClockEditor.profile(self) -> 'ClockProfile'
```

Return the clock profile validated for the current graph.

#### `ClockEditor.reports`

Property.

```text
ClockEditor.reports(self) -> 'tuple[ClockEditReport, ...]'
```

Return every successful timed-tier policy outcome in order.

#### `ClockEditor.freeze`

Method.

```text
ClockEditor.freeze(self) -> 'Graph'
```

Return the current fully validated graph without consuming the editor.

#### `ClockEditor.insert_item`

Method.

```text
ClockEditor.insert_item(self, tier: 'QualifiedName', index: 'int', item: 'Item') -> 'ClockEditor'
```

Insert one item and atomically bind every resulting timed boundary.

#### `ClockEditor.insert_items`

Method.

```text
ClockEditor.insert_items(self, tier: 'QualifiedName', index: 'int', items: 'Iterable[Item]') -> 'ClockEditor'
```

Insert ordered items and atomically bind resulting timed boundaries.

#### `ClockEditor.remove_item`

Method.

```text
ClockEditor.remove_item(self, reference: 'ItemRef | DurableItemRef') -> 'ClockEditor'
```

Remove one item together with its departing clock anchor.

#### `ClockEditor.remove_items`

Method.

```text
ClockEditor.remove_items(self, tier: 'QualifiedName', index: 'int', count: 'int') -> 'ClockEditor'
```

Remove a run and keep one reported binding on its merged boundary.

#### `ClockEditor.move_item`

Method.

```text
ClockEditor.move_item(self, reference: 'ItemRef | DurableItemRef', index: 'int') -> 'ClockEditor'
```

Move an item through fixed boundary times under the named policy.

#### `ClockEditor.swap_items`

Method.

```text
ClockEditor.swap_items(self, first: 'ItemRef | DurableItemRef', second: 'ItemRef | DurableItemRef') -> 'ClockEditor'
```

Exchange two items through fixed boundary times under the policy.

#### `ClockEditor.reparent`

Method.

```text
ClockEditor.reparent(self, target: 'RelationTarget', sources: 'RelationEndpointRef | Iterable[RelationEndpointRef]', targets: 'RelationEndpointRef | Iterable[RelationEndpointRef]') -> 'ClockEditor'
```

Replace relation endpoints under the named policy for touched tiers.

This operation is for structural parent relations, not the clock binding
relation itself. ``keep-earlier`` leaves timing unchanged. The named
collapsing policy conservatively collapses each touched timed tier onto
its earlier extent and records that realignment is needed.

#### `ClockEditor.replace_subtree`

Method.

```text
ClockEditor.replace_subtree(self, root: 'ItemRef | DurableItemRef', containment: 'QualifiedName | Iterable[QualifiedName]', new: 'Subtree', policies: 'ReplacementPolicies | None' = None) -> 'ClockEditor'
```

Replace descendants while explicitly reconciling every timed tier.

#### `ClockEditor.undeclare_with_contents`

Method.

```text
ClockEditor.undeclare_with_contents(self, target: 'str | QualifiedName | EditDeclaration') -> 'ClockEditor'
```

Cascade one declaration under this session's rebinding policy.

The complete graph-level cascade is staged before this editor changes.
Removing or changing a clock binding, the clock tier, the binding
declaration, or items on a bound tier requires a named policy. The
report records every withdrawn binding under that policy. A withdrawal
leaves no binding behind, so it does not claim realignment.

A cascade that removes the clock tier or binding contract necessarily
retires this profile. The resulting graph and reports remain available
through :meth:`freeze` and :attr:`reports`, but :attr:`profile` and any
later profile-aware edit refuse.

### `ClockEditOperation`

```text
ClockEditOperation(*values)
```

Name the structural operation summarized by a clock edit report.

#### `ClockEditOperation` members

- `ITEM_INSERTION` = `item insertion`
- `ITEM_REMOVAL` = `item removal`
- `ITEM_MOVE` = `item move`
- `ITEM_SWAP` = `item swap`
- `REPARENT` = `reparent`
- `DECLARATION_CASCADE` = `declaration cascade`
- `SUBTREE_REPLACEMENT` = `subtree replacement`

### `ClockRebindingPolicy`

```text
ClockRebindingPolicy(*values)
```

Choose how a structural edit reconciles clock-bound boundaries.

#### `ClockRebindingPolicy` members

- `KEEP_EARLIER` = `keep-earlier`
- `DROP_TO_PROVISIONAL` = `drop-to-provisional`

### `ClockEditReport`

```text
ClockEditReport(operation: 'ClockEditOperation', policy: 'ClockRebindingPolicy', tier: 'QualifiedName', changes: 'tuple[ClockBindingChange, ...]', needs_realignment: 'bool') -> None
```

Report one policy outcome on one clock-bound tier.

``operation`` identifies the structural operation. ``policy`` is the named
rebinding policy that governed it, and ``tier`` is the affected timed tier.
``changes`` lists every inserted, changed, or withdrawn clock binding.
``needs_realignment`` says that the graph carries synthesized or collapsed
timing which is also durably marked by the tier's ``needs-realignment``
fact.

### `ClockBindingChange`

```text
ClockBindingChange(previous_boundary: 'BoundaryRef | None', boundary: 'BoundaryRef | None', previous_source: 'RelationEndpointRef | None', source: 'RelationEndpointRef | None', previous_clock_index: 'int | None', clock_index: 'int | None', provisional: 'bool') -> None
```

Report one binding that a clock-aware structural edit changed.

``previous_boundary`` and ``boundary`` are the old and new logical tier
boundaries; either is ``None`` when the binding was inserted or withdrawn.
``previous_source`` and ``source`` are their durable anchor forms.
``previous_clock_index`` and ``clock_index`` are the old and new integral
clock targets. The final boolean field says that the resulting binding now
holds a synthesized or collapsed value that needs later realignment; it is
always false for a withdrawn binding.

### `ClockCoordinate`

```text
ClockCoordinate(tick: 'int', gap: 'int' = 0) -> None
```

Name one integral gap inside an integral coarse tick.

#### `ClockCoordinate.to_data`

Method.

```text
ClockCoordinate.to_data(self) -> 'dict[str, int]'
```

Encode this refined structural clock coordinate.

### `PhysicalTiming`

```text
PhysicalTiming(start: 'Decimal', duration: 'Decimal', unit: 'str') -> None
```

Carry exact decimal values stamped with the profile's declared unit.

The unit is carried, not dimensionally enforced: this profile validates its
declaration and stamps stored values with it, but a stored decimal has no
independent unit metadata against which the declaration could be checked.

#### `PhysicalTiming.to_data`

Method.

```text
PhysicalTiming.to_data(self) -> 'dict[str, str]'
```

Encode this exact physical timing with canonical decimal lexemes.

### `anchored_boundary`

```text
anchored_boundary(graph: 'Graph', boundary: 'BoundaryRef') -> 'DurableBoundaryRef'
```

Name a boundary by either adjacent durable anchor without changing it.

## Construction

### `AddItem`

```text
AddItem(tier: 'QualifiedName', item: 'Item' = Item(durable_id=None, attributes=())) -> None
```

Append one item to a declared tier.

#### `AddItem.apply`

Method.

```text
AddItem.apply(self, graph: 'Graph') -> 'Graph'
```

Append the item, refusing an unknown tier or invalid identity.

#### `AddItem.to_data`

Method.

```text
AddItem.to_data(self) -> 'dict[str, JsonValue]'
```

Return the opcode as JSON data.

### `AsBuilt`

```text
AsBuilt(graph: 'Graph', trace: 'tuple[PrimitiveOpcode, ...]') -> None
```

Pair a checked graph with its finite primitive consume-tier trace.

#### `AsBuilt.unroll`

Method.

```text
AsBuilt.unroll(self) -> 'Self'
```

Return this already lowered outcome unchanged.

#### `AsBuilt.fingerprint`

Method.

```text
AsBuilt.fingerprint(self) -> 'str'
```

Return a SHA-256 fingerprint of canonical as-built state bytes.

Durable ids are genuine as-built content, not metadata, so promoting
an item or an interior boundary changes these bytes and therefore this
fingerprint.  A tier's leading and trailing boundaries are already
addressable by side, so promoting one returns the same graph and leaves
this fingerprint alone.

There are no bytes to hash for a graph the UTF-8 encoder cannot write,
and this is a writer of those bytes like any other.  It therefore asks
the encoding question through the same check `wire.to_data` and
`program_dumps` ask it with, imported rather than restated, so one
string meets one stage and one wording whichever writer a caller
reached it from.  Unasked, the encoder's own `UnicodeEncodeError`
escaped instead, naming a position in a rendering nobody holds rather
than a field of the graph.

#### `AsBuilt.to_data`

Method.

```text
AsBuilt.to_data(self) -> 'dict[str, JsonValue]'
```

Return the machine version and graph as JSON-serializable data.

### `AttachValue`

```text
AttachValue(domain: 'AttributeDomain', target: 'AttributeTarget', value: 'Attribute') -> None
```

Attach a typed value to an owner in its declared attribute domain.

#### `AttachValue.apply`

Method.

```text
AttachValue.apply(self, graph: 'Graph') -> 'Graph'
```

Replace the named owner and let graph construction check the value.

#### `AttachValue.to_data`

Method.

```text
AttachValue.to_data(self) -> 'dict[str, JsonValue]'
```

Return the opcode as JSON data.

### `DeclareAttribute`

```text
DeclareAttribute(declaration: 'AttributeDeclaration') -> None
```

Declare one typed attribute and its attachment domain.

#### `DeclareAttribute.apply`

Method.

```text
DeclareAttribute.apply(self, graph: 'Graph') -> 'Graph'
```

Append the declaration through graph validation.

#### `DeclareAttribute.to_data`

Method.

```text
DeclareAttribute.to_data(self) -> 'dict[str, JsonValue]'
```

Return the opcode as JSON data.

### `DeclareNamespace`

```text
DeclareNamespace(declaration: 'NamespaceDeclaration') -> None
```

Declare one namespace binding.

#### `DeclareNamespace.apply`

Method.

```text
DeclareNamespace.apply(self, graph: 'Graph') -> 'Graph'
```

Append the binding through graph validation.

#### `DeclareNamespace.to_data`

Method.

```text
DeclareNamespace.to_data(self) -> 'dict[str, JsonValue]'
```

Return the opcode as JSON data.

### `DeclareRelation`

```text
DeclareRelation(declaration: 'RelationDeclaration') -> None
```

Declare a simple membership or bipartite relation.

#### `DeclareRelation.apply`

Method.

```text
DeclareRelation.apply(self, graph: 'Graph') -> 'Graph'
```

Append the declaration through graph validation.

#### `DeclareRelation.to_data`

Method.

```text
DeclareRelation.to_data(self) -> 'dict[str, JsonValue]'
```

Return the opcode as JSON data.

### `DeclareTier`

```text
DeclareTier(declaration: 'TierDeclaration') -> None
```

Declare one empty ordered tier.

#### `DeclareTier.apply`

Method.

```text
DeclareTier.apply(self, graph: 'Graph') -> 'Graph'
```

Append the empty tier through graph validation.

#### `DeclareTier.to_data`

Method.

```text
DeclareTier.to_data(self) -> 'dict[str, JsonValue]'
```

Return the opcode as JSON data.

### `ExecutionError`

```text
ExecutionError(message: 'str') -> 'None'
```

Name the opcode that could not make its checked state transition.

Every execution refusal is a promise spanning more than one opcode, so
the class carries the last stage of the declared refusal order.

### `Program`

```text
Program(opcodes: 'tuple[Opcode, ...]') -> None
```

Carry source opcodes while defining identity on their checked outcome.

#### `Program.unroll`

Method.

```text
Program.unroll(self) -> 'AsBuilt'
```

Lower procedures and build their authoritative graph in linear time.

#### `Program.fingerprint`

Method.

```text
Program.fingerprint(self) -> 'str'
```

Hash the canonical JSON data of the as-built graph.

### `PromoteItem`

```text
PromoteItem(reference: 'ItemRef', durable_id: 'str') -> None
```

Promote one structural item reference to durable identity.

#### `PromoteItem.apply`

Method.

```text
PromoteItem.apply(self, graph: 'Graph') -> 'Graph'
```

Apply the kernel's checked promotion operation.

#### `PromoteItem.to_data`

Method.

```text
PromoteItem.to_data(self) -> 'dict[str, JsonValue]'
```

Return the opcode as JSON data.

### `PromoteBoundary`

```text
PromoteBoundary(reference: 'BoundaryRef', durable_id: 'str') -> None
```

Promote one structural boundary reference to anchored identity.

#### `PromoteBoundary.apply`

Method.

```text
PromoteBoundary.apply(self, graph: 'Graph') -> 'Graph'
```

Apply the kernel's checked boundary promotion operation.

#### `PromoteBoundary.to_data`

Method.

```text
PromoteBoundary.to_data(self) -> 'dict[str, JsonValue]'
```

Return the opcode as JSON data.

### `Relate`

```text
Relate(relation: 'RelationInstance | PolyadicRelationInstance') -> None
```

Add one instance of a declared bipartite or polyadic relation.

#### `Relate.apply`

Method.

```text
Relate.apply(self, graph: 'Graph') -> 'Graph'
```

Append the instance through endpoint and invariant validation.

#### `Relate.to_data`

Method.

```text
Relate.to_data(self) -> 'dict[str, JsonValue]'
```

Return the opcode as JSON data.

### `Repeat`

```text
Repeat(count: 'int', body: 'tuple[Opcode, ...]') -> None
```

Repeat a finite block without adding a primitive consume-tier opcode.

#### `Repeat.to_data`

Method.

```text
Repeat.to_data(self) -> 'dict[str, JsonValue]'
```

Return the procedural opcode as JSON data.

### `Step`

```text
Step(index: 'int', opcode: 'PrimitiveOpcode', graph: 'Graph') -> None
```

Record one primitive opcode and its validated resulting graph.

#### `Step.to_data`

Method.

```text
Step.to_data(self) -> 'dict[str, JsonValue]'
```

Return the step as JSON-serializable data (index, opcode, graph).

### `execute`

```text
execute(opcodes: 'Iterable[object]') -> 'Graph'
```

Execute primitives in order and name the first refused opcode.

Drives the same ``steps`` generator a debugger walks and returns its final
graph, so execution and stepping are one path: the debugger observes exactly
what runs, and the two cannot diverge.

### `graph_to_program`

```text
graph_to_program(graph: 'Graph') -> 'Program'
```

Return a construction-only program whose outcome is exactly ``graph``.

Qualified names stay expanded in machine data, so replay is independent of
document-local prefix spellings. Orphan facts and explicit zero-length seal
records are emitted rather than inferred or discarded.

### `steps`

```text
steps(source: 'Program | AsBuilt | Iterable[object]') -> 'Iterator[Step]'
```

Yield each primitive opcode with its validated resulting graph.

## Editing

### `Displacement`

```text
Displacement(items: 'Mapping[ItemRef, ItemRef]', boundaries: 'Mapping[BoundaryRef, BoundaryRef]', relations: 'Mapping[int, int]', polyadic_relations: 'Mapping[int, int]', departed_items: 'frozenset[ItemRef]', departed_boundaries: 'frozenset[BoundaryRef]', departed_relations: 'frozenset[int]', departed_polyadic_relations: 'frozenset[int]') -> None
```

Report where every position of one graph stands in another.

The four maps are total over their source index spaces: an old position is
either mapped or departed.  In particular, stationary positions map to
themselves rather than being omitted.

Construction refuses a coordinate that is both mapped and departed, which is
the half of that claim a value can decide.  The other half cannot be checked
here: a displacement does not carry the graph it is about, so the source
space is whatever the maps and departed sets name between them, and a
coordinate omitted from both is not detectable.  An accumulated displacement
is total against a real graph because the operation that built it saw one;
a hand-built one is total by definition rather than by check.

#### `Displacement.then`

Method.

```text
Displacement.then(self, later: 'Displacement') -> 'Displacement'
```

Compose two displacements into the one the pair of edits performed.

#### `Displacement.to_data`

Method.

```text
Displacement.to_data(self) -> 'dict[str, JsonValue]'
```

Return the four total position maps and their departed positions.

JSON objects cannot use structural references as keys, so every map is
represented as an ordered array of ``from``/``to`` records.  Relation
positions are integers; item and boundary positions use their public
reference encodings.

#### `Displacement.stationary`

Class method.

```text
Displacement.stationary(cls, graph: 'Graph') -> 'Displacement'
```

Return the displacement of a graph onto itself.

### `GraphEditor`

```text
GraphEditor(graph: 'Graph') -> 'None'
```

Carry graph content in mutable form and validate it once at freeze.

A frozen ``Graph`` answers this operation set by returning a new graph.
This carrier answers the same operations by changing itself, so a caller
chooses rewriting or mutation by choosing which carrier to hold.  Every
operation returns this editor so operations chain, and nothing it returns
is a graph until ``freeze()`` builds and validates one.

Structural operations keep the graph's own references denoting what they
denoted before the edit.  Item coordinates stored inside the graph are
rewritten to follow their items, and durable identifiers resolve again at
freeze.  A stored boundary value addressed by coordinate is rewritten when
the edit leaves its boundary exactly one image, and refuses the edit when
it does not: a bare coordinate has no anchor to follow, while a boundary
promoted through ``Graph.promote_boundary`` does.

An operation that refuses changes nothing, so a refused edit leaves this
editor exactly as it was.  What one operation cannot see on its own -- a
second parent, a cycle, a membership subset -- is caught by the single
validation at freeze, which is the same validation a frozen graph runs.

#### `GraphEditor.freeze`

Method.

```text
GraphEditor.freeze(self) -> 'Graph'
```

Return a fully validated graph without consuming this editor.

#### `GraphEditor.displacement`

Method.

```text
GraphEditor.displacement(self) -> 'Displacement'
```

Return where every position of this editor's input now stands.

#### `GraphEditor.declare`

Method.

```text
GraphEditor.declare(self, declaration: 'EditDeclaration', at: 'int | None' = None) -> 'GraphEditor'
```

Insert one namespace, tier, attribute, or relation declaration.

``at`` addresses the selected declaration carrier.  An omitted position
appends.  Name-keyed carriers retain their graph-defined canonical order
when frozen; tier positions remain in the supplied order.

#### `GraphEditor.undeclare`

Method.

```text
GraphEditor.undeclare(self, target: 'str | QualifiedName | EditDeclaration') -> 'GraphEditor'
```

Remove one unused declaration, listing every current dependent.

A bare ``str`` selects a namespace prefix. Passing a declaration value
disambiguates equal qualified names in different declaration carriers.
A refusal is a preflight: no editor carrier or displacement is changed
unless the complete dependency list is empty.

#### `GraphEditor.promote_item`

Method.

```text
GraphEditor.promote_item(self, reference: 'ItemRef', durable_id: 'str') -> 'GraphEditor'
```

Give one item durable identity, refusing a conflict before writing.

#### `GraphEditor.promote_boundary`

Method.

```text
GraphEditor.promote_boundary(self, reference: 'BoundaryRef', durable_id: 'str') -> 'GraphEditor'
```

Give a boundary a durable anchor and store its values by that anchor.

Demotion restores a stored boundary value to coordinate addressing. If
no value is stored, demotion is a no-op because there is no boundary
record to rewrite. It is an exact inverse when the interior anchor
already carried the id. If promotion created that item id, demote the
item separately for exact restoration.

#### `GraphEditor.promote_relation`

Method.

```text
GraphEditor.promote_relation(self, target: 'RelationTarget', durable_id: 'str') -> 'GraphEditor'
```

Give one bipartite or polyadic instance durable identity.

#### `GraphEditor.demote_item`

Method.

```text
GraphEditor.demote_item(self, reference: 'DurableItemRef') -> 'GraphEditor'
```

Remove an unreferenced item's durable identity.

#### `GraphEditor.demote_boundary`

Method.

```text
GraphEditor.demote_boundary(self, reference: 'DurableBoundaryRef') -> 'GraphEditor'
```

Store one boundary value by coordinate while retaining its anchor id.

Retaining the anchor is exact when it was durable before promotion. If
promotion created that item id, this inverse is functional rather than
identified until the caller separately demotes the item. A valid durable
boundary with no stored value has no boundary record to rewrite, so
demotion is a no-op.

#### `GraphEditor.demote_relation`

Method.

```text
GraphEditor.demote_relation(self, reference: 'DurableRelationRef | DurablePolyadicRef') -> 'GraphEditor'
```

Remove an unreferenced relation instance's durable identity.

#### `GraphEditor.seal`

Method.

```text
GraphEditor.seal(self, carrier: 'SealedCarrier', sealed: 'int') -> 'GraphEditor'
```

Seal this much of one carrier, refusing a retreat.

#### `GraphEditor.unseal`

Method.

```text
GraphEditor.unseal(self, carrier: 'SealedCarrier', sealed: 'int') -> 'GraphEditor'
```

Retreat an existing seal without dropping its record.

#### `GraphEditor.drop_seal`

Method.

```text
GraphEditor.drop_seal(self, carrier: 'SealedCarrier') -> 'GraphEditor'
```

Remove one seal record, including a zero-length record.

#### `GraphEditor.add_layer`

Method.

```text
GraphEditor.add_layer(self, name: 'LayerName') -> 'GraphEditor'
```

Add one empty layer, refusing a duplicate name.

#### `GraphEditor.remove_layer`

Method.

```text
GraphEditor.remove_layer(self, name: 'LayerName') -> 'GraphEditor'
```

Remove one empty layer, refusing to discard its facts.

#### `GraphEditor.put_fact`

Method.

```text
GraphEditor.put_fact(self, layer: 'LayerName', fact: 'LayerFact') -> 'GraphEditor'
```

Add or replace one exactly addressed fact in an existing layer.

A supplied valid ``OrphanedSubject`` is retained as content. Invalid
orphan coordinates and unresolved live subjects refuse before writing.

#### `GraphEditor.remove_fact`

Method.

```text
GraphEditor.remove_fact(self, layer: 'LayerName', subject: 'LayerSubject', name: 'QualifiedName') -> 'GraphEditor'
```

Remove one fact by its exact subject spelling and attribute name.

#### `GraphEditor.prune_orphans`

Method.

```text
GraphEditor.prune_orphans(self) -> 'GraphEditor'
```

Remove every orphaned layer fact from this editor.

#### `GraphEditor.compact`

Method.

```text
GraphEditor.compact(self) -> 'GraphEditor'
```

Prune orphan facts and share equal retained immutable values.

#### `GraphEditor.set_attribute`

Method.

```text
GraphEditor.set_attribute(self, target: 'EditTarget', value: 'Attribute') -> 'GraphEditor'
```

Give one carrier this value, replacing any value of the same name.

The value's declaration decides which carrier the target names, so a
caller spells the place and not the domain.  An undeclared attribute
is refused here rather than at freeze, because without a declaration
there is no domain to read the target against.  A structural relation
reference names a position in this editor's current relation content.

#### `GraphEditor.remove_attribute`

Method.

```text
GraphEditor.remove_attribute(self, target: 'EditTarget', name: 'QualifiedName') -> 'GraphEditor'
```

Take the named value off one carrier, refusing when it is absent.

A structural relation reference names a position in this editor's
current relation content.

#### `GraphEditor.insert_item`

Method.

```text
GraphEditor.insert_item(self, tier: 'QualifiedName', index: 'int', item: 'Item') -> 'GraphEditor'
```

Insert one item at a tier index, carrying later references with it.

An index equal to the tier's item count appends.

#### `GraphEditor.insert_items`

Method.

```text
GraphEditor.insert_items(self, tier: 'QualifiedName', index: 'int', items: 'Iterable[Item]') -> 'GraphEditor'
```

Insert ordered items at a tier index in one restructure.

For ordered input containing at least one item, this equals inserting
each item at ``index + k`` in turn. An index equal to the tier's item
count appends. Unlike a zero-step fold, an empty input still validates
the tier and index. Sets and mappings are refused because they do not
provide the required item order. The input is materialized before tier
and index validation, so an exception raised while iterating it takes
precedence over either validation refusal.

#### `GraphEditor.remove_item`

Method.

```text
GraphEditor.remove_item(self, reference: 'ItemRef | DurableItemRef') -> 'GraphEditor'
```

Remove one item, refusing while the graph still references it.

#### `GraphEditor.remove_items`

Method.

```text
GraphEditor.remove_items(self, tier: 'QualifiedName', index: 'int', count: 'int') -> 'GraphEditor'
```

Remove a contiguous item run in one restructure.

This equals removing ``count`` items at ``index`` one at a time when
every removal is admitted. A zero count validates the tier and range,
then leaves both graph content and displacement untouched.

#### `GraphEditor.replace_item`

Method.

```text
GraphEditor.replace_item(self, reference: 'ItemRef | DurableItemRef', item: 'Item') -> 'GraphEditor'
```

Replace one item's values while preserving its durable identity.

Promotion and demotion are separate operations, so replacement refuses
an item whose durable id differs from the item already at the coordinate.

#### `GraphEditor.replace_subtree`

Method.

```text
GraphEditor.replace_subtree(self, root: 'ItemRef | DurableItemRef', containment: 'QualifiedName | Iterable[QualifiedName]', new: 'Subtree', policies: 'ReplacementPolicies | None' = None) -> 'GraphEditor'
```

Replace containment descendants under explicit dependency policies.

#### `GraphEditor.swap_subtrees`

Method.

```text
GraphEditor.swap_subtrees(self, first: 'ItemRef | DurableItemRef', second: 'ItemRef | DurableItemRef', containment: 'QualifiedName | Iterable[QualifiedName]', first_policies: 'ReplacementPolicies | None' = None, second_policies: 'ReplacementPolicies | None' = None) -> 'GraphEditor'
```

Exchange two non-nested containment descendant sets.

#### `GraphEditor.move_item`

Method.

```text
GraphEditor.move_item(self, reference: 'ItemRef | DurableItemRef', index: 'int') -> 'GraphEditor'
```

Move one item to another index of its own tier, carrying references.

A move across tiers is not this operation.  Membership decides an
item's type, so carrying an item into another tier retypes it, and a
caller who means that says so with a removal and an insertion.

#### `GraphEditor.swap_items`

Method.

```text
GraphEditor.swap_items(self, first: 'ItemRef | DurableItemRef', second: 'ItemRef | DurableItemRef') -> 'GraphEditor'
```

Exchange two items of one tier, carrying their references with them.

#### `GraphEditor.add_relation`

Method.

```text
GraphEditor.add_relation(self, instance: 'RelationInstance | PolyadicRelationInstance', at: 'int | None' = None) -> 'GraphEditor'
```

Insert one relation instance in the collection its arity belongs to.

#### `GraphEditor.remove_relation`

Method.

```text
GraphEditor.remove_relation(self, target: 'RelationTarget') -> 'GraphEditor'
```

Remove one relation instance by index, reference, or durable id.

A structural reference names a current position; reread
``displacement()`` after removal before reusing one.

#### `GraphEditor.set_endpoints`

Method.

```text
GraphEditor.set_endpoints(self, target: 'RelationTarget', sources: 'RelationEndpointRef | Iterable[RelationEndpointRef]', targets: 'RelationEndpointRef | Iterable[RelationEndpointRef]') -> 'GraphEditor'
```

Replace one instance's endpoints while preserving its other content.

### `Journal`

```text
Journal(annotations: 'EditAnnotations | None' = None, *, provenance: 'LayerName | None' = None, protected: 'Iterable[LayerName]' = (), horizon: 'int | JournalHorizon | None' = None, author: 'str | None' = None, reason: 'str | None' = None, stage: 'str | None' = None, confidence: 'float | None' = None, iteration: 'int | None' = None, tool: 'str | None' = None, timestamp: 'str | None' = None, fields: 'Mapping[str, JsonValue] | None' = None) -> 'None'
```

Own opt-in edit history and bind it to one editor session.

Records remain undoable until :meth:`checkpoint` or the optional history
horizon discards them.  An integer ``horizon`` limits record count;
:class:`JournalHorizon` can instead limit estimated retained bytes or apply
both limits.
``provenance`` names a layer that receives typed JSON facts only on the
durably addressable subjects an operation directly acts on.  Those facts
travel with their subjects and are retired, undoably, when a later journal
operation removes or demotes the subject.  Operations without a stable graph
subject remain attributable through their journal record. :meth:`protect`
prevents edits to facts and the content they describe.

#### `Journal.records`

Property.

```text
Journal.records(self) -> 'tuple[JournalRecord, ...]'
```

Return applied records in operation order.

#### `Journal.reports`

Property.

```text
Journal.reports(self) -> 'tuple[EditReport, ...]'
```

Return reports for all currently applied records.

#### `Journal.redo_records`

Property.

```text
Journal.redo_records(self) -> 'tuple[JournalRecord, ...]'
```

Return undone records in the order :meth:`redo` will restore them.

#### `Journal.horizon`

Property.

```text
Journal.horizon(self) -> 'JournalHorizon | None'
```

Return this journal's immutable history-retention policy.

#### `Journal.retained_bytes`

Property.

```text
Journal.retained_bytes(self) -> 'int'
```

Return estimated Python storage reachable from retained history.

A byte-bounded journal maintains a conservative per-record estimate so
enforcing the bound does not repeatedly scan all retained records.
Values shared between records or with the live graph may be counted
more than once.

#### `Journal.checkpoint`

Method.

```text
Journal.checkpoint(self) -> 'Journal'
```

Make the current graph the undo base and release earlier records.

Applied and redo history are both discarded.  Live graph content,
including provenance facts, is unchanged.  A later undo cannot cross
this boundary.

#### `Journal.protect`

Method.

```text
Journal.protect(self, layer: 'LayerName') -> 'Journal'
```

Protect one existing layer's facts and described live content.

Structural edits may move a protected fact and its unchanged subject to
new coordinates. A missing layer is an error rather than an inactive
protection rule, and no edit may create, remove, or replace protected
facts.

#### `Journal.annotate`

Method.

```text
Journal.annotate(self, annotations: 'EditAnnotations | None' = None, **fields: 'object') -> 'Iterator[Journal]'
```

Apply caller metadata to every successful operation in this context.

#### `Journal.undo`

Method.

```text
Journal.undo(self) -> 'JournalRecord'
```

Undo the latest applied operation, retaining it for redo.

#### `Journal.redo`

Method.

```text
Journal.redo(self) -> 'JournalRecord'
```

Reapply the most recently undone operation.

#### `Journal.to_patch`

Method.

```text
Journal.to_patch(self) -> 'Patch'
```

Return the applied history as a fingerprint-guarded public patch.

The import is local because the patch container depends on journal
annotations. Each emitted opcode carries only guarded document changes,
plus the exact reverse changes under the journal's inverse operation name.

### `JournalEditor`

```text
JournalEditor(graph: 'Graph', journal: 'Journal') -> 'None'
```

Apply fully validated graph edits while recording an opt-in journal.

#### `JournalEditor.freeze`

Method.

```text
JournalEditor.freeze(self) -> 'Graph'
```

Return the current fully validated graph.

#### `JournalEditor.displacement`

Method.

```text
JournalEditor.displacement(self) -> 'Displacement'
```

Return where every position of this session's input now stands.

#### `JournalEditor.undo`

Method.

```text
JournalEditor.undo(self) -> 'JournalRecord'
```

Undo the latest operation in this editor's journal.

#### `JournalEditor.redo`

Method.

```text
JournalEditor.redo(self) -> 'JournalRecord'
```

Redo the latest operation undone in this editor's journal.

#### `JournalEditor.dry_run`

Method.

```text
JournalEditor.dry_run(self, operation: 'Callable[[Any], object]') -> 'tuple[EditReport, ...]'
```

Apply, validate, report, and roll back new operations by inverses.

#### `JournalEditor.declare`

Method.

```text
JournalEditor.declare(self, declaration: 'EditDeclaration', at: 'int | None' = None) -> 'JournalEditor'
```

Declare one schema member and record its inverse.

#### `JournalEditor.undeclare`

Method.

```text
JournalEditor.undeclare(self, target: 'str | QualifiedName | EditDeclaration') -> 'JournalEditor'
```

Undeclare one unused schema member and record its inverse.

#### `JournalEditor.undeclare_with_contents`

Method.

```text
JournalEditor.undeclare_with_contents(self, target: 'str | QualifiedName | EditDeclaration') -> 'JournalEditor'
```

Cascade one declaration and record the complete inverse delta.

#### `JournalEditor.promote_item`

Method.

```text
JournalEditor.promote_item(self, reference: 'ItemRef', durable_id: 'str') -> 'JournalEditor'
```

Promote one item and record its inverse.

#### `JournalEditor.promote_boundary`

Method.

```text
JournalEditor.promote_boundary(self, reference: 'BoundaryRef', durable_id: 'str') -> 'JournalEditor'
```

Promote one boundary and record its inverse.

#### `JournalEditor.promote_relation`

Method.

```text
JournalEditor.promote_relation(self, target: 'RelationTarget', durable_id: 'str') -> 'JournalEditor'
```

Promote one relation instance and record its inverse.

#### `JournalEditor.demote_item`

Method.

```text
JournalEditor.demote_item(self, reference: 'DurableItemRef') -> 'JournalEditor'
```

Demote one item and record its inverse.

#### `JournalEditor.demote_boundary`

Method.

```text
JournalEditor.demote_boundary(self, reference: 'DurableBoundaryRef') -> 'JournalEditor'
```

Demote one boundary and record its inverse.

#### `JournalEditor.demote_relation`

Method.

```text
JournalEditor.demote_relation(self, reference: 'DurableRelationRef | DurablePolyadicRef') -> 'JournalEditor'
```

Demote one relation instance and record its inverse.

#### `JournalEditor.seal`

Method.

```text
JournalEditor.seal(self, carrier: 'SealedCarrier', sealed: 'int') -> 'JournalEditor'
```

Advance a seal and record its previous state.

#### `JournalEditor.unseal`

Method.

```text
JournalEditor.unseal(self, carrier: 'SealedCarrier', sealed: 'int') -> 'JournalEditor'
```

Retreat a seal and record its previous state.

#### `JournalEditor.drop_seal`

Method.

```text
JournalEditor.drop_seal(self, carrier: 'SealedCarrier') -> 'JournalEditor'
```

Drop a seal and record its previous state.

#### `JournalEditor.add_layer`

Method.

```text
JournalEditor.add_layer(self, name: 'LayerName') -> 'JournalEditor'
```

Add an empty layer and record its inverse.

#### `JournalEditor.remove_layer`

Method.

```text
JournalEditor.remove_layer(self, name: 'LayerName') -> 'JournalEditor'
```

Remove an empty layer and record its inverse.

#### `JournalEditor.put_fact`

Method.

```text
JournalEditor.put_fact(self, layer: 'LayerName', fact: 'LayerFact') -> 'JournalEditor'
```

Put one layer fact and record the prior fact state.

#### `JournalEditor.remove_fact`

Method.

```text
JournalEditor.remove_fact(self, layer: 'LayerName', subject: 'LayerSubject', name: 'QualifiedName') -> 'JournalEditor'
```

Remove one layer fact and record it for restoration.

#### `JournalEditor.prune_orphans`

Method.

```text
JournalEditor.prune_orphans(self) -> 'JournalEditor'
```

Remove and report orphaned layer facts as one undoable edit.

#### `JournalEditor.compact`

Method.

```text
JournalEditor.compact(self) -> 'JournalEditor'
```

Prune orphans, compact live storage, and re-intern retained history.

#### `JournalEditor.set_attribute`

Method.

```text
JournalEditor.set_attribute(self, target: 'EditTarget', value: 'Attribute') -> 'JournalEditor'
```

Set one attribute and record the prior value or absence.

#### `JournalEditor.remove_attribute`

Method.

```text
JournalEditor.remove_attribute(self, target: 'EditTarget', name: 'QualifiedName') -> 'JournalEditor'
```

Remove one attribute and record it for restoration.

#### `JournalEditor.insert_item`

Method.

```text
JournalEditor.insert_item(self, tier: 'QualifiedName', index: 'int', item: 'Item') -> 'JournalEditor'
```

Insert one item and record its structural inverse.

#### `JournalEditor.insert_items`

Method.

```text
JournalEditor.insert_items(self, tier: 'QualifiedName', index: 'int', items: 'Iterable[Item]') -> 'JournalEditor'
```

Insert ordered items and record their structural inverse.

#### `JournalEditor.remove_item`

Method.

```text
JournalEditor.remove_item(self, reference: 'ItemRef | DurableItemRef') -> 'JournalEditor'
```

Remove one item and retain it in the inverse delta.

#### `JournalEditor.remove_items`

Method.

```text
JournalEditor.remove_items(self, tier: 'QualifiedName', index: 'int', count: 'int') -> 'JournalEditor'
```

Remove an item run and retain it in the inverse delta.

#### `JournalEditor.replace_item`

Method.

```text
JournalEditor.replace_item(self, reference: 'ItemRef | DurableItemRef', item: 'Item') -> 'JournalEditor'
```

Replace one item and retain its prior value.

#### `JournalEditor.replace_subtree`

Method.

```text
JournalEditor.replace_subtree(self, root: 'ItemRef | DurableItemRef', containment: 'QualifiedName | Iterable[QualifiedName]', new: 'Subtree', policies: 'ReplacementPolicies | None' = None) -> 'JournalEditor'
```

Replace descendants atomically and retain abandoned dependencies.

#### `JournalEditor.swap_subtrees`

Method.

```text
JournalEditor.swap_subtrees(self, first: 'ItemRef | DurableItemRef', second: 'ItemRef | DurableItemRef', containment: 'QualifiedName | Iterable[QualifiedName]', first_policies: 'ReplacementPolicies | None' = None, second_policies: 'ReplacementPolicies | None' = None) -> 'JournalEditor'
```

Exchange two non-nested descendant sets as one journal event.

#### `JournalEditor.move_item`

Method.

```text
JournalEditor.move_item(self, reference: 'ItemRef | DurableItemRef', index: 'int') -> 'JournalEditor'
```

Move one item and record the reverse move.

#### `JournalEditor.swap_items`

Method.

```text
JournalEditor.swap_items(self, first: 'ItemRef | DurableItemRef', second: 'ItemRef | DurableItemRef') -> 'JournalEditor'
```

Swap two items and record the same swap as inverse.

#### `JournalEditor.add_relation`

Method.

```text
JournalEditor.add_relation(self, instance: 'RelationInstance | PolyadicRelationInstance', at: 'int | None' = None) -> 'JournalEditor'
```

Add one relation instance and record its removal.

#### `JournalEditor.remove_relation`

Method.

```text
JournalEditor.remove_relation(self, target: 'RelationTarget') -> 'JournalEditor'
```

Remove one relation instance and retain it for reinsertion.

#### `JournalEditor.set_endpoints`

Method.

```text
JournalEditor.set_endpoints(self, target: 'RelationTarget', sources: 'RelationEndpointRef | Iterable[RelationEndpointRef]', targets: 'RelationEndpointRef | Iterable[RelationEndpointRef]') -> 'JournalEditor'
```

Replace endpoints and retain their previous ordered values.

### `JournalHorizon`

```text
JournalHorizon(count: 'int | None' = None, bytes: 'int | None' = None) -> None
```

Bound history by record count, conservative estimated bytes, or both.

### `ClockJournalEditor`

```text
ClockJournalEditor(profile: 'ClockProfile', rebinding: 'ClockRebindingPolicy | str | None', journal: 'Journal') -> 'None'
```

Record atomic edits that preserve an explicit clock profile.

#### `ClockJournalEditor.profile`

Property.

```text
ClockJournalEditor.profile(self) -> 'ClockProfile'
```

Return the clock profile validated for the current graph.

#### `ClockJournalEditor.reports`

Property.

```text
ClockJournalEditor.reports(self) -> 'tuple[ClockEditReport, ...]'
```

Return clock-policy reports for currently applied records.

#### `ClockJournalEditor.freeze`

Method.

```text
ClockJournalEditor.freeze(self) -> 'Graph'
```

Return the current fully validated graph.

#### `ClockJournalEditor.displacement`

Method.

```text
ClockJournalEditor.displacement(self) -> 'Displacement'
```

Return where every position of this session's input now stands.

#### `ClockJournalEditor.undo`

Method.

```text
ClockJournalEditor.undo(self) -> 'JournalRecord'
```

Undo the latest operation in this editor's journal.

#### `ClockJournalEditor.redo`

Method.

```text
ClockJournalEditor.redo(self) -> 'JournalRecord'
```

Redo the latest operation undone in this editor's journal.

#### `ClockJournalEditor.dry_run`

Method.

```text
ClockJournalEditor.dry_run(self, operation: 'Callable[[Any], object]') -> 'tuple[EditReport, ...]'
```

Apply, validate, report, and roll back new operations by inverses.

#### `ClockJournalEditor.insert_item`

Method.

```text
ClockJournalEditor.insert_item(self, tier: 'QualifiedName', index: 'int', item: 'Item') -> 'ClockJournalEditor'
```

Insert and bind one item under the session policy.

#### `ClockJournalEditor.insert_items`

Method.

```text
ClockJournalEditor.insert_items(self, tier: 'QualifiedName', index: 'int', items: 'Iterable[Item]') -> 'ClockJournalEditor'
```

Insert and bind ordered items under the session policy.

#### `ClockJournalEditor.remove_item`

Method.

```text
ClockJournalEditor.remove_item(self, reference: 'ItemRef | DurableItemRef') -> 'ClockJournalEditor'
```

Remove one item and its binding under the session policy.

#### `ClockJournalEditor.remove_items`

Method.

```text
ClockJournalEditor.remove_items(self, tier: 'QualifiedName', index: 'int', count: 'int') -> 'ClockJournalEditor'
```

Remove an item run and reconcile its bindings.

#### `ClockJournalEditor.move_item`

Method.

```text
ClockJournalEditor.move_item(self, reference: 'ItemRef | DurableItemRef', index: 'int') -> 'ClockJournalEditor'
```

Move one item under the named rebinding policy.

#### `ClockJournalEditor.swap_items`

Method.

```text
ClockJournalEditor.swap_items(self, first: 'ItemRef | DurableItemRef', second: 'ItemRef | DurableItemRef') -> 'ClockJournalEditor'
```

Swap two items under the named rebinding policy.

#### `ClockJournalEditor.reparent`

Method.

```text
ClockJournalEditor.reparent(self, target: 'RelationTarget', sources: 'RelationEndpointRef | Iterable[RelationEndpointRef]', targets: 'RelationEndpointRef | Iterable[RelationEndpointRef]') -> 'ClockJournalEditor'
```

Reparent one relation under the named rebinding policy.

#### `ClockJournalEditor.replace_subtree`

Method.

```text
ClockJournalEditor.replace_subtree(self, root: 'ItemRef | DurableItemRef', containment: 'QualifiedName | Iterable[QualifiedName]', new: 'Subtree', policies: 'ReplacementPolicies | None' = None) -> 'ClockJournalEditor'
```

Replace descendants under replacement and clock policies.

#### `ClockJournalEditor.undeclare_with_contents`

Method.

```text
ClockJournalEditor.undeclare_with_contents(self, target: 'str | QualifiedName | EditDeclaration') -> 'ClockJournalEditor'
```

Run and record one clock-aware declaration cascade.

### `JournalRecord`

```text
JournalRecord(operation: 'str', inverse: 'EditInverse', report: 'EditReport', annotations: 'EditAnnotations') -> 'None'
```

Keep one operation, its change-sized inverse, and lazy report recipe.

#### `JournalRecord.report`

Property.

```text
JournalRecord.report(self) -> 'EditReport'
```

Compute the expanded public report only when it is requested.

#### `JournalRecord.to_data`

Method.

```text
JournalRecord.to_data(self) -> 'dict[str, JsonValue]'
```

Return the public journal record as JSON-compatible data.

### `EditInverse`

```text
EditInverse(operation: 'str', carriers: 'tuple[str, ...]') -> None
```

Name one recorded inverse and the graph carriers it restores.

#### `EditInverse.to_data`

Method.

```text
EditInverse.to_data(self) -> 'dict[str, JsonValue]'
```

Describe the inverse without exposing retained Python values.

### `EditReport`

```text
EditReport(operation: 'str', touched_items: 'tuple[ItemRef, ...]', touched_boundaries: 'tuple[BoundaryRef, ...]', touched_relations: 'tuple[RelationTouch, ...]', detached_references: 'tuple[LayerSubject, ...]', displacement: 'Displacement', annotations: 'EditAnnotations', clock_reports: 'tuple[ClockEditReport, ...]' = (), detached_dependencies: 'tuple[DetachedDependency, ...]' = (), pruned_orphans: 'tuple[PrunedFact, ...]' = ()) -> None
```

Describe the content one recorded operation touched.

#### `EditReport.to_data`

Method.

```text
EditReport.to_data(self) -> 'dict[str, JsonValue]'
```

Return this report in deterministic JSON-compatible form.

### `EditAnnotations`

```text
EditAnnotations(author: 'str | None' = None, reason: 'str | None' = None, stage: 'str | None' = None, confidence: 'float | None' = None, iteration: 'int | None' = None, tool: 'str | None' = None, timestamp: 'str | None' = None, fields: 'Mapping[str, JsonValue]' = <factory>) -> None
```

Carry caller-supplied edit metadata without inventing a timestamp.

``fields`` accepts ordinary typed JSON values.  A timestamp is an opaque
caller-supplied string: constructing annotations never reads a clock.

#### `EditAnnotations.merged`

Method.

```text
EditAnnotations.merged(self, other: 'EditAnnotations') -> 'EditAnnotations'
```

Return these defaults with non-``None`` values from ``other``.

#### `EditAnnotations.to_data`

Method.

```text
EditAnnotations.to_data(self) -> 'dict[str, JsonValue]'
```

Return only supplied metadata as detached JSON data.

### `ContainmentRule`

```text
type ContainmentRule = str | collections.abc.Callable[[tuple[int, int], tuple[tuple[tiergraph.core.ItemRef, tuple[int, int]], ...]], tiergraph.core.ItemRef | None]
```

### `DetachedDependency`

```text
DetachedDependency(carrier: 'str', index: 'int', declaration: 'QualifiedName | None' = None, layer: 'LayerName | None' = None, subject: 'LayerSubject | None' = None, tier: 'QualifiedName | None' = None) -> None
```

Name one dependency removed from the live graph by replacement.

#### `DetachedDependency.to_data`

Method.

```text
DetachedDependency.to_data(self) -> 'dict[str, JsonValue]'
```

Return a stable, JSON-compatible description.

### `Patch`

```text
Patch(base_fingerprint: 'str', target_fingerprint: 'str', operations: 'tuple[PatchOperation, ...]', annotations: 'EditAnnotations' = <factory>, patch_version: 'str' = '1') -> None
```

Apply recorded graph transitions only to their identified base.

#### `Patch.apply`

Method.

```text
Patch.apply(self, base: 'Graph') -> 'Graph'
```

Apply every operation after checking each identified transition.

#### `Patch.invert`

Method.

```text
Patch.invert(self) -> 'Patch'
```

Return the exact reverse patch.

### `PatchOperation`

```text
PatchOperation(opcode: 'PrimitiveOpcode', inverse: 'PrimitiveOpcode', base_fingerprint: 'str', target_fingerprint: 'str', annotations: 'EditAnnotations' = <factory>) -> None
```

Carry one executable transition, its exact inverse, and fingerprints.

#### `PatchOperation.to_data`

Method.

```text
PatchOperation.to_data(self) -> 'dict[str, JsonValue]'
```

Return one self-checking JSONL operation record.

### `RelationTouch`

```text
RelationTouch(carrier: 'str', index: 'int') -> None
```

Name one touched binary or polyadic relation position.

#### `RelationTouch.to_data`

Method.

```text
RelationTouch.to_data(self) -> 'dict[str, JsonValue]'
```

Return the carrier and structural index.

### `PrunedFact`

```text
PrunedFact(layer: 'LayerName', fact: 'LayerFact') -> None
```

Name one orphaned layer fact removed by explicit cleanup.

#### `PrunedFact.to_data`

Method.

```text
PrunedFact.to_data(self) -> 'dict[str, JsonValue]'
```

Return the layer identity and canonical fact data.

### `ReplacementAction`

```text
ReplacementAction(*values)
```

Choose how a dependency on replaced content is handled.

#### `ReplacementAction` members

- `ABANDON` = `abandon`
- `FOLLOW` = `follow`
- `SPLIT` = `split`

### `ReplacementPolicies`

```text
ReplacementPolicies(default: 'ReplacementAction' = <ReplacementAction.ABANDON: 'abandon'>, correspond: 'bool' = False, correspondence: 'SubtreeCorrespondence' = <factory>, relations: 'Mapping[QualifiedName, ReplacementAction]' = <factory>, layers: 'Mapping[LayerName, ReplacementAction]' = <factory>, insertion_points: 'Mapping[QualifiedName, int]' = <factory>) -> None
```

Declare replacement defaults and per-carrier dependency actions.

Abandonment is the default. ``correspond`` enables a stable local
per-tier alignment for unmatched items with equal content; an explicit
correspondence is applied first. Per-relation and per-layer actions
override ``default``.
``follow`` requires exactly one counterpart for every referenced item.
``split`` duplicates a dependency over all declared counterparts.

#### `ReplacementPolicies.corresponding`

Class method.

```text
ReplacementPolicies.corresponding(cls, correspondence: 'SubtreeCorrespondence | None' = None, *, relations: 'Mapping[QualifiedName, ReplacementAction] | None' = None, layers: 'Mapping[LayerName, ReplacementAction] | None' = None, insertion_points: 'Mapping[QualifiedName, int] | None' = None) -> 'ReplacementPolicies'
```

Return an organization default that follows local correspondence.

A speech-processing profile can use this default to retain provenance
on corresponding alternatives while plain tiergraph editing continues
to abandon dependencies unless the caller opts in.

### `Subtree`

```text
Subtree(graph: 'Graph', root: 'ItemRef | DurableItemRef') -> None
```

Name a rooted containment subtree in a validated graph.

### `SubtreeCorrespondence`

```text
SubtreeCorrespondence(items: 'Mapping[ItemRef, tuple[ItemRef, ...]]' = <factory>) -> None
```

Map old descendants to zero, one, or several new descendants.

References on the right address :attr:`Subtree.graph`. Multiple old items
may name one new item for a merge, and one old item may name several new
items for a split. Missing old items have no counterpart.

### `PathChoice`

```text
type PathChoice = collections.abc.Iterable[PathStep] | tuple[tuple[str, ...], ...]
```

### `PathStep`

```text
type PathStep = tiergraph.core.ItemRef | tiergraph.core.DurableItemRef | str
```

### `apply_patch`

```text
apply_patch(patch: 'Patch', base: 'Graph') -> 'Graph'
```

Apply ``patch`` to its identified base, validating every transition.

### `apply_selected`

```text
apply_selected(graph: 'Graph', selected: 'NodeSet | Selector | SpanMatches', operation: 'Callable[[GraphEditor, Node], object]', *, path_profile: 'PathProfile | None' = None, budget: 'WorkBudget | WorkMeter | None' = None) -> 'Graph'
```

Apply ``operation`` once to every materialized selected node.

``selected`` may be a graph-free selector, a node set belonging to
``graph``, or an exhaustive set of regular-pattern spans. Selectors are
evaluated once before editing starts. ``path_profile`` applies only to this
selector form. Span items are combined as a set, so nodes shared by
overlapping spans are edited once. Spans carry no graph provenance; their
item coordinates are validated and interpreted against ``graph``.

Nodes are visited in reverse canonical order. Before every call, the
original node is remapped through all preceding edits. This puts relation
instances before their endpoints, tiers after their contents, and positions
within one carrier in descending order while keeping arbitrary positional
shifts safe. If a preceding callback removes a later selected node, the
bulk edit refuses instead of retargeting the callback.

Every callback receives the same editor. Outside the callback,
``apply_selected`` constructs one editor and performs one final validation.
The input graph is immutable, so a callback refusal, invalid final graph, or
exhausted budget exposes no partial graph result. The callback's return
value is ignored.

One optional work budget covers selector evaluation, span materialization,
and one step per callback. Nested budget-aware work in the callback shares
the active meter. This graph-level entry point records no journal and
applies no clock rebinding policy. Callers requiring either must perform the
equivalent selected structural operations through the appropriate editor.

### `commit_path`

```text
commit_path(lattice: 'PathPlan[Any]', path: 'PathChoice', *, containment: 'QualifiedName | Iterable[QualifiedName]' = (), journal: 'Journal | None' = None) -> 'Graph'
```

Keep one complete path and its declared containment substructure.

``lattice`` supplies the finite acyclic topology and its source graph.
``path`` is either its ordered item references, its ordered labels, or
non-``None`` one-path provenance returned by :meth:`PathPlan.evaluate`.
Every unchosen lattice item and descendant outside the retained
substructure is removed. ``containment`` names ordered, item-only polyadic
relations that declare source uniqueness and acyclicity; their descendants
travel with each alternative, and descendants shared with the chosen path
remain live.

Item attributes and layer facts are ordinary graph content, so source spans
and ranked-alternative provenance on chosen units remain unchanged. Facts
scoped to withdrawn content and references with withdrawn endpoints are
removed explicitly, never orphaned silently. When ``journal`` is supplied,
the derived edit is recorded as its expanded fact, relation, value, and item
primitives. A resulting patch therefore retains no reference to the
request-scoped path plan.

### `compose_patches`

```text
compose_patches(first: 'Patch', second: 'Patch') -> 'Patch'
```

Compose adjacent identified patches without weakening either guard.

### `contain_by_time`

```text
contain_by_time(profile: 'ClockProfile', relation: 'QualifiedName', parent: 'QualifiedName', child: 'QualifiedName', *, rule: 'ContainmentRule' = 'midpoint', journal: 'Journal | None' = None) -> 'Graph'
```

Rebuild one parent-child relation from exact shared-clock spans.

The default ``"midpoint"`` rule assigns each child to the unique parent
whose half-open span contains the child's midpoint. Spans use coarse,
integral clock-boundary indices; optional within-tick refinements do not
affect assignment. A caller may instead provide a function receiving the
child's integral clock span and every parent span; it returns the chosen
parent or ``None``. Refusal is explicit when the default finds no unique
parent or a callback names another item.

Bipartite declarations retain one instance per child. Polyadic declarations
retain one source per parent and ordered child targets. Existing instances
are updated in place where possible, preserving ids, values, facts, and
global order; already correct structure is an exact no-op. Journaled calls
expand into endpoint, relation, and fact primitives.

### `diff`

```text
diff(source: 'Graph', target: 'Graph', view: 'EquivalenceView | str' = <EquivalenceView.FUNCTIONAL: 'functional'>) -> 'Patch'
```

Return a deterministic executable patch from ``source`` toward ``target``.

Compatible schemas use a per-tier unit-cost alignment. Reused equal items
become moves, unmatched items become replacements, insertions, or removals,
and references are torn down and rebuilt in dependency order. Incompatible
declarations use one guarded rebuild delta. Applying the result always
produces a graph equivalent to ``target`` under ``view``; an already
equivalent pair produces an empty patch guarded to ``source``. When such a
pair differs under the identified view, that no-op patch is not an exact
transition to ``target`` and does not compose as one.

This graph-level operation validates graph structure only. It does not
preserve or report a clock profile's rebinding policy; construct edits
through :meth:`ClockProfile.edit` when that policy must govern structural
changes.

### `invert_patch`

```text
invert_patch(patch: 'Patch') -> 'Patch'
```

Reverse operation order and exchange every recorded transition.

### `replace_subtree`

```text
replace_subtree(graph: 'Graph', root: 'ItemRef | DurableItemRef', containment: 'QualifiedName | Iterable[QualifiedName]', new: 'Subtree', policies: 'ReplacementPolicies | None' = None) -> 'Graph'
```

Return ``graph`` with one root's containment descendants replaced.

The root, its incoming containment link, its attributes, and its layer facts
remain live. The default abandons dependencies on descendants and reports
them when this operation is journaled. Correspondence is explicitly opt-in.

### `retime`

```text
retime(profile: 'ClockProfile', tier: 'QualifiedName', alignment: 'Sequence[int]', *, offset: 'int' = 0, journal: 'Journal | None' = None) -> 'Graph'
```

Rebind every boundary of one timed tier to exact clock positions.

``alignment`` contains one integral clock-boundary index per tier boundary,
hence one more entry than the tier has items. ``offset`` is added to every
supplied index. The resulting positions must be in range and monotone.
Existing binding instances are updated in place, retaining their durable
identities, attributes, layer facts, and global relation positions.

The operation changes structural clock bindings only; stored item start and
duration values are not rewritten. It uses the profile's declared clock and
binding relation and validates a new profile before publishing the graph, so
stored values must still satisfy that profile. It does not apply a tolerance:
callers should store external times as integral samples or milliseconds and
put tolerance in a distance calculation. A journal records the endpoint
changes as ordinary expanded primitives.

### `swap_subtrees`

```text
swap_subtrees(graph: 'Graph', first: 'ItemRef | DurableItemRef', second: 'ItemRef | DurableItemRef', containment: 'QualifiedName | Iterable[QualifiedName]', first_policies: 'ReplacementPolicies | None' = None, second_policies: 'ReplacementPolicies | None' = None) -> 'Graph'
```

Exchange two non-nested descendant sets as two atomic replacements.

### `undeclare_with_contents`

```text
undeclare_with_contents(graph: 'Graph', target: 'str | QualifiedName | EditDeclaration') -> 'Graph'
```

Remove a declaration and all of its content as one atomic derived edit.

A bare ``str`` selects a namespace prefix; qualified names select the other
declaration carriers. Mutually dependent declarations are removed as one
strongly connected component.

The operation applies editing primitives to a private editor and publishes
only the final validated graph. A refusal therefore leaves the input graph
untouched. Like :meth:`Graph.edit`, this profile-free operation deliberately
bypasses clock rebinding; use ``ClockProfile.edit().undeclare_with_contents``
when timing must refuse or report through a named policy. Its inverse is
available when the operation runs through an edit journal, not from this
function itself.

## Distance

### `CostTable`

```text
CostTable(operations: 'Mapping[str, CostLike]' = <factory>, declarations: 'Mapping[QualifiedName, Mapping[str, CostLike]]' = <factory>, value_substitution: 'ValueSubstitution' = <factory>, boundary_displacement: 'BoundaryDisplacement' = <factory>) -> None
```

Declare graph-operation costs and domain-specific value costs.

Operation costs are global unless ``declarations`` overrides one operation
for a qualified declaration. Names must belong to :data:`PRIMITIVE_KINDS`,
and inverse pairs must have equal costs. Zero is accepted for projections,
but an exact result is a metric only when every operation visible in its
equivalence view has positive cost.

The two callbacks remain Python-only because a data file cannot safely name
executable code. :meth:`from_data` therefore reads numeric operation and
declaration costs while retaining the default callbacks.

#### `CostTable.operation`

Method.

```text
CostTable.operation(self, kind: 'str', declaration: 'QualifiedName | None' = None) -> 'Decimal'
```

Return the declared cost for one operation and optional declaration.

#### `CostTable.minimum_operation`

Method.

```text
CostTable.minimum_operation(self, kind: 'str') -> 'Decimal'
```

Return the least declared cost for an operation in any scope.

#### `CostTable.substitute_value`

Method.

```text
CostTable.substitute_value(self, before: 'Attribute', after: 'Attribute') -> 'Decimal'
```

Return the checked domain-specific cost of replacing one value.

#### `CostTable.displace_boundary`

Method.

```text
CostTable.displace_boundary(self, before: 'int', after: 'int') -> 'Decimal'
```

Return the checked domain-specific cost of moving one boundary.

#### `CostTable.metric_violations`

Method.

```text
CostTable.metric_violations(self) -> 'tuple[str, ...]'
```

Name declared graph operations whose zero cost prevents a metric.

#### `CostTable.to_data`

Method.

```text
CostTable.to_data(self) -> 'dict[str, JsonValue]'
```

Return the declarative numeric part as JSON-compatible data.

#### `CostTable.from_data`

Class method.

```text
CostTable.from_data(cls, value: 'object') -> 'CostTable'
```

Decode strict numeric cost-table data for command-line use.

### `DistanceInterval`

```text
DistanceInterval(lower: 'Decimal', upper: 'Decimal', exact: 'bool', method: 'str') -> None
```

Hold exact distance or certified lower and realized upper bounds.

#### `DistanceInterval.value`

Property.

```text
DistanceInterval.value(self) -> 'Decimal | None'
```

Return the exact value, or ``None`` while the interval is open.

#### `DistanceInterval.to_data`

Method.

```text
DistanceInterval.to_data(self) -> 'dict[str, JsonValue]'
```

Return a JSON-compatible exact value or interval report.

### `OrderedTree`

```text
OrderedTree(value: 'object', children: 'tuple[OrderedTree, ...]' = ()) -> None
```

Hold one labeled node and its ordered children for tree distance.

### `SequenceProjection`

```text
SequenceProjection(name: 'str', project: 'GraphProjection', insert: 'SequenceCost[object]' = 1, delete: 'SequenceCost[object]' = 1, substitute: 'SubstitutionCost[object]' = 1) -> None
```

Project a graph to a sequence with its own weighted edit model.

#### `SequenceProjection.distance`

Method.

```text
SequenceProjection.distance(self, source: 'Graph', target: 'Graph') -> 'Decimal'
```

Return this projection's exact sequence distance.

### `ProjectionWitness`

```text
ProjectionWitness()
```

Show one replayed graph operation for projection admissibility.

Use :meth:`from_patch`; arbitrary labeled graph pairs cannot serve as
evidence because their claimed operation would not have been checked.

#### `ProjectionWitness.from_patch`

Class method.

```text
ProjectionWitness.from_patch(cls, before: 'Graph', patch: 'Patch') -> 'ProjectionWitness'
```

Build a witness by replaying one non-residual patch operation.

### `ProjectionViolation`

```text
ProjectionViolation(operation: 'str', projected: 'Decimal', allowed: 'Decimal') -> None
```

Describe one graph operation that a projection overprices.

### `ProjectionAdmissibility`

```text
ProjectionAdmissibility(projection: 'SequenceProjection', costs: 'CostTable', checked: 'frozenset[str]', required: 'frozenset[str]', violations: 'tuple[ProjectionViolation, ...]') -> None
```

Report coverage and failures from an admissibility check.

#### `ProjectionAdmissibility.missing`

Property.

```text
ProjectionAdmissibility.missing(self) -> 'frozenset[str]'
```

Return required primitive kinds with no supplied witness.

#### `ProjectionAdmissibility.admissible`

Property.

```text
ProjectionAdmissibility.admissible(self) -> 'bool'
```

Report whether coverage is complete and every inequality holds.

#### `ProjectionAdmissibility.certify`

Method.

```text
ProjectionAdmissibility.certify(self) -> 'AdmissibleProjection'
```

Return a lower-bound certificate or refuse an incomplete check.

### `AdmissibleProjection`

```text
AdmissibleProjection(projection: 'SequenceProjection', operations: 'frozenset[str]', costs: 'CostTable') -> None
```

Certify that a projection is a lower bound for named graph operations.

### `weighted_sequence_distance`

```text
weighted_sequence_distance(source: 'Sequence[T]', target: 'Sequence[T]', *, insert: 'SequenceCost[T]' = 1, delete: 'SequenceCost[T]' = 1, substitute: 'SubstitutionCost[T]' = 1) -> 'Decimal'
```

Return exact weighted insertion, deletion, and substitution distance.

The dynamic program uses ``O(len(source) * len(target))`` time and two rows
of storage. Costs may be constants or value-sensitive callables.

### `ordered_tree_distance`

```text
ordered_tree_distance(source: 'OrderedTree', target: 'OrderedTree', *, insert: 'SequenceCost[object]' = 1, delete: 'SequenceCost[object]' = 1, substitute: 'SubstitutionCost[object]' = 1) -> 'Decimal'
```

Return exact ordered-tree edit distance with node promotion on deletion.

This is the Zhang-Shasha operation model: deleting a node promotes its
children into the ordered forest, and insertion is the inverse. The result
is exact for that model and does not claim exactness for overlapping graph
relations or cheaper graph-level move and reparent operations.

### `contiguous_segmentation_distance`

```text
contiguous_segmentation_distance(source: 'Sequence[int]', target: 'Sequence[int]', *, split: 'CostLike' = 1, merge: 'CostLike' = 1, displace: 'SubstitutionCost[int]' = 1) -> 'Decimal'
```

Return exact edit distance between two contiguous segmentations.

Each sequence gives positive segment widths over the same number of base
units. Removing an internal boundary is one merge, adding one is a split,
and matching unequal boundary positions uses the declared displacement
cost. Pass :meth:`CostTable.displace_boundary` to use a table's callback.

### `text_projection`

```text
text_projection(name: 'str', pieces: 'TextPieces', *, join: 'TextJoin', transform: 'TextTransform | None' = None) -> 'SequenceProjection'
```

Build a character projection with caller-declared joining policy.

``pieces`` extracts ordered text fragments. ``join`` decides what, if
anything, lies between them. ``transform`` can implement a presentation
view; no language- or locale-specific joining rule is built in.

### `whitespace_insensitive_projection`

```text
whitespace_insensitive_projection(pieces: 'TextPieces', *, join: 'TextJoin') -> 'SequenceProjection'
```

Build a character projection that removes ``str.isspace()`` characters.

### `format_control_insensitive_projection`

```text
format_control_insensitive_projection(pieces: 'TextPieces', *, join: 'TextJoin') -> 'SequenceProjection'
```

Build a character projection that gives Unicode Cf controls zero cost.

### `check_projection_admissibility`

```text
check_projection_admissibility(projection: 'SequenceProjection', costs: 'CostTable', witnesses: 'Iterable[ProjectionWitness]', *, required: 'Iterable[str] | None' = None) -> 'ProjectionAdmissibility'
```

Check projection cost against realized one-step graph operations.

Callers supply small-graph witnesses for every required operation kind. A
witness passes only when the projected change can be expressed at no more
than the graph operation's declared cost. Missing operation kinds prevent a
certificate; a projection never becomes a lower bound by assertion alone.

### `price_patch`

```text
price_patch(patch: 'Patch', costs: 'CostTable', source: 'Graph | None' = None) -> 'Decimal'
```

Return the cost of a realized patch containing only declared operations.

Residual document changes and multi-call native edits are not one declared
primitive and are refused. Supplying ``source`` first replays the complete
patch, establishing that the priced operations are a realized script, and
resolves declaration-specific costs for structural and durable references.

### `graph_distance`

```text
graph_distance(source: 'Graph', target: 'Graph', costs: 'CostTable | None' = None, *, view: 'EquivalenceView | str' = <EquivalenceView.FUNCTIONAL: 'functional'>, projections: 'Iterable[AdmissibleProjection]' = ()) -> 'DistanceInterval'
```

Return exact distance where proved, otherwise certified graph-edit bounds.

Independent ordered tiers use exact weighted sequence distance when move and
swap shortcuts cannot undercut insertion plus deletion. General graphs use
the maximum certified projection distance as a lower bound and the cost of
an executable diff as an upper bound. If the diff contains a residual data
delta, a conservative dependency-ordered rebuild supplies the upper bound.
Overlapping and non-nesting relations therefore receive an interval rather
than an unsupported exact graph-edit claim.

### `distance`

```text
distance(source: 'Graph', target: 'Graph', costs: 'CostTable | None' = None, *, view: 'EquivalenceView | str' = <EquivalenceView.FUNCTIONAL: 'functional'>, projections: 'Iterable[AdmissibleProjection]' = ()) -> 'DistanceInterval'
```

Return graph-edit distance as a synonym for :func:`graph_distance`.

## Equivalence

### `EquivalenceView`

```text
EquivalenceView(*values)
```

Choose which observable graph identity an equivalence query compares.

``FUNCTIONAL`` ignores namespace prefixes and carried durable ids, and
resolves durable references to structural coordinates. ``IDENTIFIED`` adds
carried durable ids while retaining those resolved references. ``EXACT``
is graph equality (``==``), including prefixes and reference spellings.

Fact order within a layer is not part of ``FUNCTIONAL`` or ``IDENTIFIED``.
Values compare after construction-time canonicalization with no tolerance;
tolerance belongs to a distance measure, not equivalence.

#### `EquivalenceView` members

- `FUNCTIONAL` = `functional`
- `IDENTIFIED` = `identified`
- `EXACT` = `exact`

### `abstract_form`

```text
abstract_form(graph: 'Graph', view: 'EquivalenceView | str' = <EquivalenceView.FUNCTIONAL: 'functional'>) -> 'tuple[tuple[str, str], ...]'
```

Return the view's canonical named elements in comparison order.

Values use deterministic strict-JSON spellings. Functional and identified
forms resolve durable references to coordinates; the identified form then
adds carried durable ids. The exact form retains reference spellings and
includes the complete graph value used by graph equality. It does not
require the graph to be encodable as wire bytes.

### `equivalent`

```text
equivalent(left: 'Graph', right: 'Graph', view: 'EquivalenceView | str' = <EquivalenceView.FUNCTIONAL: 'functional'>) -> 'bool'
```

Report whether two graphs are equal under the selected public view.

### `fingerprint`

```text
fingerprint(graph: 'Graph', view: 'EquivalenceView | str' = <EquivalenceView.FUNCTIONAL: 'functional'>) -> 'str'
```

Return a view-tagged, versioned SHA-256 fingerprint.

The payload version is ``tiergraph-equivalence/1``. A fingerprint is stable
only within that version, and two views intentionally have distinct digests
even when their canonical forms otherwise contain the same elements.

### `first_difference`

```text
first_difference(left: 'Graph', right: 'Graph', view: 'EquivalenceView | str' = <EquivalenceView.FUNCTIONAL: 'functional'>) -> 'str | None'
```

Name and render the first differing element, or return ``None``.

## Fold

### `AmbiguityPolicy`

```text
type AmbiguityPolicy = tiergraph.pathoutput.Unambiguous | tiergraph.pathoutput.Determinize
```

### `AlgebraOrder`

```text
AlgebraOrder(algebra: 'Semiring[Value]') -> None
```

Compare carrier values by the algebra's own selective addition.

A fold accepts it as a ``witness_order``: the preferred operand is the one
the algebra's addition returns, and equal values tie. ``PathPlan``
recognizes it and fuses the selection into its schedule, so a best path
under ``ARCTIC`` or ``TROPICAL`` is found in the same pass that values the
graph, with no comparator calls. The algebra must declare
``add_selective``: an aggregating addition names no winner to order by.

### `AttributeValuation`

```text
AttributeValuation(name: 'str', attribute: 'QualifiedName', tiers: 'tuple[QualifiedName, ...]') -> None
```

Read one declared item attribute over an explicit tier domain.

#### `AttributeValuation.declaration_type`

Method.

```text
AttributeValuation.declaration_type(self, graph: 'Graph') -> 'XsdType'
```

Return the declared XSD type, refusing the wrong domain or a missing name.

#### `AttributeValuation.read`

Method.

```text
AttributeValuation.read(self, graph: 'Graph', reference: 'ItemRef') -> 'object'
```

Decode the selected item's canonical lexical value by its XSD type.

### `ChildCombination`

```text
ChildCombination(*values)
```

Declare whether one relation's incident children are alternatives or requirements.

#### `ChildCombination` members

- `OR` = `or`
- `AND` = `and`

### `Determinize`

```text
Determinize(max_states: 'int') -> None
```

Count with lazy subset construction up to a declared state bound.

### `ExactnessRefusal`

Refuse an exactness claim a fold does not make good on.

### `FoldCertificate`

```text
FoldCertificate(exactness: 'FoldExactness', result: 'FoldResult[Value]', probes: 'int', derivations: 'int', compared: 'bool') -> None
```

Report what discharged one fold's exactness claim, and what it never reached.

``compared`` is the honest part. It is true only when the fold's derivations
were enumerated in full within the declared budget, which is what makes a
comparison against the published value available at all. When it is false
the claim stood on the law search alone, and a law search that finds no
refutation has found no refutation — it has not proved anything. It reports
the enumeration rather than the comparison: where the law search already
settles the claim, the enumerated combination is not read.

``derivations`` counts the structural derivations that were enumerated, which
includes any the valuation annihilates, so it is a measure of the search and
not a restatement of a counting fold's value.

#### `FoldCertificate.to_data`

Method.

```text
FoldCertificate.to_data(self, semiring: 'Semiring[Value]') -> 'dict[str, object]'
```

Return deterministic strict-JSON data.

The semiring is required for the same reason ``FoldResult.to_data``
requires it: the carrier is arbitrary and only its algebra knows how to
encode a value of it.

``compared`` and ``probes`` both survive serialization deliberately. A
certificate that reported only its exactness would let a claim that
stood on a law search alone read identically to one measured against
every derivation, which is exactly the distinction this type exists to
keep.

### `FoldCost`

```text
FoldCost(document_size: 'int', relation_incidence: 'int', index_product_size: 'int', carrier_additions: 'int', carrier_multiplications: 'int', carrier_operation_cost: 'int', witness_count: 'int', emitted_count: 'int', output_cap: 'int', witness_operations: 'int' = 0, ranked_multiplications: 'int' = 0) -> None
```

Report measured structural quantities and carrier work for one run.

#### `FoldCost.bound`

Property.

```text
FoldCost.bound(self) -> 'int'
```

Return the declared structural/carrier/output work bound.

#### `FoldCost.measured_work`

Property.

```text
FoldCost.measured_work(self) -> 'int'
```

Return measured traversal work plus actually emitted output.

#### `FoldCost.carrier_work`

Property.

```text
FoldCost.carrier_work(self) -> 'int'
```

Return measured semiring-operation work at the declared unit cost.

#### `FoldCost.to_data`

Method.

```text
FoldCost.to_data(self) -> 'dict[str, int]'
```

Return a strict-JSON cost account.

#### `FoldCost.from_data`

Class method.

```text
FoldCost.from_data(cls, data: 'object') -> 'FoldCost'
```

Decode and verify a strict serialized cost account.

### `FoldDeclaration`

```text
FoldDeclaration(name: 'str', graph: 'Graph', valuation: 'AttributeValuation', semiring: 'Semiring[Value]', lift: 'Lift[Value]', transitions: 'tuple[FoldTransition, ...]', index_axes: 'tuple[tuple[str, ...], ...]' = (), roots: 'tuple[ItemRef, ...]' = (), witness_order: 'WitnessOrder[Value] | None' = None, tie_policy: 'TiePolicy | None' = None, output_cap: 'int' = 1, carrier_operation_cost: 'int' = 1, ranked_output: 'bool' = False, exactness: 'FoldExactness' = <FoldExactness.UNDECLARED: 'undeclared'>) -> None
```

Bind one named interpretation to a graph, valuation, algebra, and relation.

The dependency relation is finite and need not be acyclic. An acyclic one has a
finite derivation set; a cyclic one is specified by the starred fixpoint the
algebra's ``star`` solves, and ``exactness`` is where that difference is stated.

A readout or final division above the algebra is taken only where it is
declared: ``PathMarginals.posteriors`` reads marginals out through a
readout the caller names and the algebra lists in its ``readouts``, and
records the readout it applied. A construct
whose soundness depends on a property it cannot verify must declare that
property rather than assume it.

``witness_order`` and ``tie_policy`` are one mechanism and are declared together:
the order names the winner and the policy says what happens where it reports a
tie, so each without the other is refused. The policy is executable and is read
at every tie the order reports.

With ``ranked_output`` the fold instead returns up to ``output_cap`` witnesses
ranked by the semiring's own order, which its multiplication must preserve
(``multiply_preserves_witness_order``); a custom ``witness_order`` is refused, and
so is a ``tie_policy``. Ranked selection breaks an equal-valued tie by the
canonical witness path, so it leaves no tie for a policy to decide and would
never read one. That order is total wherever the paths are distinct, which
holds when the document's item labels are; two witnesses whose labels
collide compare equal and are then ordered by arrival. The resulting
order is deterministic, and the paths it compares are the fold's own structural
labels, so it is canonical for a given document rather than globally so.

``exactness`` states how the published value stands to the combination over every
derivation. It defaults to ``UNDECLARED`` and ``run()`` never consults it, because
the claim is owed where it is relied on rather than where a fixture is built;
``check_exactness()`` is the gate that demands and discharges it. Only the two
refusals a declaration alone can settle are made here.

#### `FoldDeclaration.index_coordinates`

Method.

```text
FoldDeclaration.index_coordinates(self) -> 'tuple[IndexCoordinate, ...]'
```

Construct the declared finite index product in lexical axis order.

#### `FoldDeclaration.states`

Method.

```text
FoldDeclaration.states(self) -> 'tuple[State, ...]'
```

Construct the finite domain-item by index-product state space.

#### `FoldDeclaration.run`

Method.

```text
FoldDeclaration.run(self, *, budget: 'WorkBudget | WorkMeter | None' = None) -> 'FoldResult[Value]'
```

Evaluate every state within an optional declared work budget.

#### `FoldDeclaration.check_exactness`

Method.

```text
FoldDeclaration.check_exactness(self, *, derivation_budget: 'int' = 1024, budget: 'WorkBudget | WorkMeter | None' = None) -> 'FoldCertificate[Value]'
```

Check exactness within an optional declared work budget.

### `FoldExactness`

```text
FoldExactness(*values)
```

State how a fold's published value stands to the combination over every derivation.

``DISTRIBUTIVE``
    The value **is** the combination over every derivation. Gate: no
    counterexample may exist, and none may be found among the bounded set
    of probes taken from the values this fold produces. The probe set is
    capped, so a carrier that denies distributivity only at values past the
    cap is not caught here.
``APPROXIMATE``
    The value is a sound approximation of that combination, and that is a
    fact about the published result rather than a footnote about the
    algebra. Gate: the approximation must be exhibitable — an ``APPROXIMATE``
    claim over a fold measured exact by an algebra that checks every law
    exactly is a declaration that is hiding, and it is refused.
``STRUCTURAL``
    No such combination exists: the derivation set is infinite because the
    dependency graph has a cycle, so the starred fixpoint equations are the
    specification. Gate: the algebra must name the star warrant that makes
    the closure converge, and the graph must actually carry a cycle.
``UNDECLARED``
    The default. It is refused, and it does not mean ``APPROXIMATE``:
    declining to say is not the same as saying the weaker thing, and the
    refusal says so by handing back the declaration to be made.

Every branch bites, and the asymmetry is deliberate. Omitting the claim is
answered with the declaration; asserting it falsely is answered with a
semantic counterexample.

#### `FoldExactness` members

- `DISTRIBUTIVE` = `distributive`
- `APPROXIMATE` = `approximate`
- `STRUCTURAL` = `structural`
- `UNDECLARED` = `undeclared`

### `FoldHomomorphism`

```text
FoldHomomorphism(name: 'str', source: 'FoldDeclaration[Value]', target: 'FoldDeclaration[OtherValue]', mapping: 'Callable[[Value], OtherValue]') -> None
```

Declare a carrier map whose fold result must commute.

#### `FoldHomomorphism.commutes`

Method.

```text
FoldHomomorphism.commutes(self) -> 'bool'
```

Execute both folds and compare the mapped source with the target.

#### `FoldHomomorphism.check`

Method.

```text
FoldHomomorphism.check(self) -> 'None'
```

Refuse a declared homomorphism whose square does not commute.

### `FoldResult`

```text
FoldResult(values: 'tuple[tuple[State, Value], ...]', roots: 'tuple[State, ...]', value: 'Value', provenance: 'DerivationProvenance | None', truncated: 'bool', cost: 'FoldCost', ranked_witnesses: 'tuple[RankedWitness[Value], ...] | None' = None) -> None
```

Keep semiring values, witness provenance, and measured work separate.

#### `FoldResult.to_data`

Method.

```text
FoldResult.to_data(self, semiring: 'Semiring[Value]') -> 'dict[str, object]'
```

Return deterministic strict-JSON data.

### `FoldTransition`

```text
FoldTransition(relation: 'QualifiedName', combination: 'ChildCombination') -> None
```

Give one bipartite or ordered polyadic dependency its AND/OR meaning.

### `Emissions`

```text
Emissions(plan: 'PathPlan[Value]', per_item: 'tuple[tuple[str, ...], ...]') -> None
```

Per-item token tuples bound to one path plan's label inventory.

#### `Emissions.bind`

Class method.

```text
Emissions.bind(cls, plan: 'PathPlan[Value]', by_label: 'Mapping[str, Sequence[str]]') -> 'Emissions[Value]'
```

Bind emissions by item label, leaving unlisted items non-emitting.

#### `Emissions.from_attribute`

Class method.

```text
Emissions.from_attribute(cls, plan: 'PathPlan[Value]', attribute: 'QualifiedName') -> 'Emissions[Value]'
```

Read string-token tuples from one item attribute, with absence silent.

### `LatticeMatch`

```text
LatticeMatch(emissions: 'Emissions[object]', pattern: 'CompiledPattern') -> None
```

Match one compiled regular pattern against every root-to-sink path.

#### `LatticeMatch.exists`

Method.

```text
LatticeMatch.exists(self, *, budget: 'WorkBudget | WorkMeter | None' = None) -> 'bool'
```

Return whether some complete lattice path matches the whole pattern.

#### `LatticeMatch.on_accepting_path`

Method.

```text
LatticeMatch.on_accepting_path(self, *, budget: 'WorkBudget | WorkMeter | None' = None) -> 'NodeSet'
```

Return every base item lying on some accepting complete path.

#### `LatticeMatch.count`

Method.

```text
LatticeMatch.count(self, policy: 'AmbiguityPolicy', *, budget: 'WorkBudget | WorkMeter | None' = None) -> 'int'
```

Count accepting lattice paths exactly under the declared policy.

#### `LatticeMatch.all_paths`

Method.

```text
LatticeMatch.all_paths(self, policy: 'AmbiguityPolicy', *, budget: 'WorkBudget | WorkMeter | None' = None) -> 'bool'
```

Return whether every complete lattice path matches the pattern.

### `OutputItemMarginals`

```text
OutputItemMarginals(total: 'Value', zero_mass: 'bool', values: 'tuple[Value, ...] | None', cost: 'FoldCost') -> None
```

Conditioned marginals pooled into the base plan's item order.

#### `OutputItemMarginals.to_data`

Method.

```text
OutputItemMarginals.to_data(self, semiring: 'Semiring[Value]') -> 'dict[str, object]'
```

Return deterministic strict-JSON data using the carrier encoding.

### `OutputMasses`

```text
OutputMasses(total: 'Value', per_candidate: 'tuple[Value, ...]', residual: 'Value', zero_mass: 'bool', decided: 'bool | None', tied: 'tuple[int, ...] | None', cost: 'FoldCost') -> None
```

Candidate and residual masses from one product-plan marginal pass.

``decided`` and ``tied`` are available only for ``LOG_PROBABILITY`` and
``COUNTING`` because their numeric order gives the certificate its meaning;
both are ``None`` under other algebras. Counting ties use exact integer
equality. Log-probability ties use equality of the computed doubles, so an
approximate addition can split equal real masses and ``tied`` is not a tie
certificate for the underlying real values.

#### `OutputMasses.to_data`

Method.

```text
OutputMasses.to_data(self, semiring: 'Semiring[Value]') -> 'dict[str, object]'
```

Return deterministic strict-JSON data using the carrier encoding.

### `OutputPlan`

```text
OutputPlan(base: 'PathPlan[Value]', emissions: 'Emissions[Value]', candidates: 'tuple[tuple[str, ...], ...]', plan: 'PathPlan[Value]', base_index: 'Mapping[ItemRef, ItemRef]', accepted: 'tuple[bool, ...]') -> None
```

A cached product plan for complete output candidates and their residual.

#### `OutputPlan.prepare`

Class method.

```text
OutputPlan.prepare(cls, base: 'PathPlan[Value]', emissions: 'Emissions[Value]', candidates: 'Sequence[Sequence[str]]', *, budget: 'WorkBudget | WorkMeter | None' = None) -> 'OutputPlan[Value]'
```

Build the reachable product with the candidates' trie and a residual.

An optional work budget limits preparation.

#### `OutputPlan.values`

Method.

```text
OutputPlan.values(self, base_values: 'Sequence[Value] | None' = None) -> 'tuple[Value, ...]'
```

Gather base values into the product plan's canonical item order.

#### `OutputPlan.masses`

Method.

```text
OutputPlan.masses(self, base_values: 'Sequence[Value] | None' = None, *, budget: 'WorkBudget | WorkMeter | None' = None) -> 'OutputMasses[Value]'
```

Evaluate masses, with certificates only for log probability or counting.

An optional work budget limits evaluation.

#### `OutputPlan.conditioned`

Method.

```text
OutputPlan.conditioned(self, candidate: 'int', *, budget: 'WorkBudget | WorkMeter | None' = None) -> 'PathPlan[Value]'
```

Return the product restricted to paths accepting one candidate.

An optional work budget limits conditioning.

#### `OutputPlan.item_marginals`

Method.

```text
OutputPlan.item_marginals(self, candidate: 'int', base_values: 'Sequence[Value] | None' = None, *, budget: 'WorkBudget | WorkMeter | None' = None) -> 'OutputItemMarginals[Value]'
```

Pool one candidate's conditioned product copies onto base items.

An optional work budget limits evaluation.

### `PathMarginals`

```text
PathMarginals(plan: 'PathPlan[Value]', total: 'Value', inside: 'tuple[Value, ...]', outside: 'tuple[Value, ...]', marginals: 'tuple[Value, ...]', cost: 'FoldCost') -> None
```

The inside and outside passes of one evaluation, per item in plan order.

``inside[i]`` is the fold value at item ``i``, the sum over the derivations
rooted there. ``outside[i]`` is the sum over the prefixes that reach it
from a root, with the multiplicative identity contributed at every root.
``marginals[i]`` is their product: the sum over every complete derivation
through ``i``, which is the zero at a dead end and at an unreachable item.
``total`` is the fold value over the roots, and ``cost`` accounts for both
passes.

#### `PathMarginals.to_data`

Method.

```text
PathMarginals.to_data(self) -> 'dict[str, object]'
```

Return deterministic strict-JSON data using the plan's carrier codec.

#### `PathMarginals.from_data`

Class method.

```text
PathMarginals.from_data(cls, plan: 'PathPlan[Value]', data: 'object') -> 'PathMarginals[Value]'
```

Decode strict serialized marginals against their prepared path plan.

#### `PathMarginals.posteriors`

Method.

```text
PathMarginals.posteriors(self, *, readout: 'str') -> 'PathPosteriors'
```

Read every marginal as a probability of the total through a declared readout.

A readout is a division above the algebra, so the caller declares it by
name and the algebra must publish it in its ``readouts``:
``readout="normalize"`` is the one the log-probability carrier
publishes, and a name the algebra does not list there is refused rather
than divided by hand, whatever other methods the algebra happens to
have. The result records the readout it applied. A zero total reports
``zero_mass`` with no values.

### `PathPlan`

```text
PathPlan(declaration: 'FoldDeclaration[Value]', items: 'tuple[ItemRef, ...]', labels: 'tuple[str, ...]', values: 'tuple[Value, ...]', children: 'tuple[tuple[int, ...], ...]', parents: 'tuple[tuple[int, ...], ...]', roots: 'tuple[int, ...]', order: 'tuple[int, ...]') -> None
```

A fold declaration compiled to its path topology, evaluable under new values.

``items`` are the declaration's domain items in the graph's canonical
order, and that order is the plan's value order: ``evaluate`` and
``marginals`` take one carrier value per item in it, and ``values`` holds
the values the declaration itself lifts, so a caller can start from those
and replace what changed. ``labels`` are the items' durable identities or
structural labels, the names a fold's provenance spells. A vector of
another length is refused, because a vector from a different inventory has
no position that means anything here.

``children[i]`` are the indices of item ``i``'s alternatives in canonical
order, ``parents[i]`` the items it is an alternative of, ``roots`` the
declared or inferred roots, and ``order`` a children-first evaluation
order. The plan is what the declaration is: a sink accepts with its own
value, so a dead end is a sink the caller values at the zero, and an item
no root reaches contributes nothing.

``evaluate`` reproduces ``FoldDeclaration.run`` for the same values, with
the same provenance under the same ``witness_order`` and ``tie_policy``,
and the same cost account. Under the log-probability, arctic, and tropical
carriers the plan runs the algebra's operations in a fused schedule that
gathers each item's alternatives at once. The operation counts it reports
are the general schedule's, which the fused schedule performs in gathered
form, sharing one product across the children it reaches. A gathered
log-sum-exp sums its exponentials in a different order than pairwise
addition does, so under the log carrier the two schedules agree within the
algebra's declared approximation -- at the rounding scale of the operands,
which on a total near cancellation can be visible in the result -- and
under the extremum carriers they agree exactly. A selective carrier with
an ``AlgebraOrder`` and ``CHOOSE_FIRST``
fuses its selection too. Every other declaration runs the general schedule
through the algebra's own methods.

#### `PathPlan.prepare`

Class method.

```text
PathPlan.prepare(cls, declaration: 'FoldDeclaration[Value]') -> 'PathPlan[Value]'
```

Compile the declaration's topology, refusing what a path cannot carry.

#### `PathPlan.index`

Method.

```text
PathPlan.index(self, reference: 'ItemRef') -> 'int'
```

Return an item's position in the plan's value order.

#### `PathPlan.evaluate`

Method.

```text
PathPlan.evaluate(self, values: 'Sequence[Value] | None' = None, *, budget: 'WorkBudget | WorkMeter | None' = None) -> 'FoldResult[Value]'
```

Fold the compiled topology within an optional work budget.

#### `PathPlan.marginals`

Method.

```text
PathPlan.marginals(self, values: 'Sequence[Value] | None' = None, *, budget: 'WorkBudget | WorkMeter | None' = None) -> 'PathMarginals[Value]'
```

Run inside and outside passes within an optional work budget.

### `PathPosteriors`

```text
PathPosteriors(readout: 'str', zero_mass: 'bool', values: 'tuple[float, ...] | None') -> None
```

Probabilities read out of a plan's marginals, with the readout named.

``readout`` is the name the caller declared and the algebra method that
produced ``values``, so the result says which post-pass above the algebra
it applied. ``zero_mass`` is
true when the total was the algebra's zero; ``values`` is then ``None``,
because there is no distribution to report and none is fabricated.

#### `PathPosteriors.to_data`

Method.

```text
PathPosteriors.to_data(self) -> 'dict[str, object]'
```

Return deterministic strict-JSON data with lossless double values.

#### `PathPosteriors.from_data`

Class method.

```text
PathPosteriors.from_data(cls, data: 'object') -> 'PathPosteriors'
```

Decode deterministic strict-JSON posterior data.

### `TiePolicy`

```text
TiePolicy(*values)
```

Supported, executable policies for equal-valued alternatives.

A policy answers a tie that a declared ``witness_order`` reports, so it is
declared with that order and with nothing else. Ranked output totalizes its
own comparison and takes no policy.

#### `TiePolicy` members

- `ALL` = `all`
- `CHOOSE_FIRST` = `choose-first`

### `Unambiguous`

```text
Unambiguous() -> None
```

Require the pattern to have at most one accepting run per token string.

### `match_lattice`

```text
match_lattice(emissions: 'Emissions[Value]', pattern: 'CompiledPattern') -> 'LatticeMatch'
```

Bind a compiled pattern to one emitted finite path DAG.

## Grammar

### `BestDerivation`

```text
BestDerivation(weight: 'str', witness: 'tuple[str, ...]') -> None
```

Carry an exact total cost and one deterministic derivation witness.

#### `BestDerivation.to_data`

Method.

```text
BestDerivation.to_data(self) -> 'dict[str, JsonValue]'
```

Return the result as JSON-serializable data.

### `GeneratedDerivation`

```text
GeneratedDerivation(weight: 'str', pieces: 'tuple[TargetPiece, ...]', applications: 'tuple[RuleApplication, ...]', witness: 'tuple[str, ...]') -> None
```

Carry one experimental ranked target materialization and exact derivation cost.

#### `GeneratedDerivation.tokens`

Property.

```text
GeneratedDerivation.tokens(self) -> 'tuple[str, ...]'
```

Return emitted tokens in declared target order.

#### `GeneratedDerivation.text`

Property.

```text
GeneratedDerivation.text(self) -> 'str'
```

Join emitted tokens with the experimental one-ASCII-space profile.

#### `GeneratedDerivation.to_data`

Method.

```text
GeneratedDerivation.to_data(self) -> 'dict[str, JsonValue]'
```

Return this experimental generated derivation as strict JSON data.

#### `GeneratedDerivation.from_data`

Class method.

```text
GeneratedDerivation.from_data(cls, data: 'object') -> 'GeneratedDerivation'
```

Decode one strict experimental generated derivation.

### `GenerationResult`

```text
GenerationResult(derivations: 'tuple[GeneratedDerivation, ...]', truncated: 'bool', cost: 'FoldCost') -> None
```

Report experimental bounded target projections and their fold account.

#### `GenerationResult.to_data`

Method.

```text
GenerationResult.to_data(self) -> 'dict[str, JsonValue]'
```

Return the versioned experimental generation-result envelope.

#### `GenerationResult.from_data`

Class method.

```text
GenerationResult.from_data(cls, data: 'object') -> 'GenerationResult'
```

Decode one strict versioned experimental generation-result envelope.

### `GrammarChartProfile`

```text
GrammarChartProfile(forest: 'ParseForest') -> None
```

Address chart alternatives in a stable order within one forest snapshot.

The profile vocabulary is
``/chart/NONTERMINAL/START/END/alternatives/INDEX``. Alternative indices are
independent of rule weights, but intentionally are not stable across forest
snapshots whose sets of alternatives differ.

#### `GrammarChartProfile.to_data`

Method.

```text
GrammarChartProfile.to_data(self) -> 'dict[str, JsonValue]'
```

Return the declarative chart-profile input used by the CLI.

#### `GrammarChartProfile.from_data`

Class method.

```text
GrammarChartProfile.from_data(cls, graph: 'Graph', data: 'object') -> 'GrammarChartProfile'
```

Bind a declarative chart profile to one graph snapshot.

#### `GrammarChartProfile.bind`

Method.

```text
GrammarChartProfile.bind(self, path: 'CanonicalPath', graph: 'Graph') -> 'PathBinding'
```

Bind a chart coordinate and profile-owned alternatives literal.

#### `GrammarChartProfile.spell`

Method.

```text
GrammarChartProfile.spell(self, binding: 'PathBinding', graph: 'Graph') -> 'CanonicalPath'
```

Spell an alternative binding in this chart vocabulary.

#### `GrammarChartProfile.alternatives`

Method.

```text
GrammarChartProfile.alternatives(self, owner: 'ItemRef', relation: 'QualifiedName', graph: 'Graph') -> 'tuple[object, ...]'
```

Order by application start, ordered child spans, then application index.

### `GrammarDeclaration`

```text
GrammarDeclaration(nonterminals: 'tuple[QualifiedName, ...]', start: 'QualifiedName', rules: 'tuple[GrammarRule, ...]') -> None
```

Hold a validated synchronous grammar with fixed source and target roles.

#### `GrammarDeclaration.to_data`

Method.

```text
GrammarDeclaration.to_data(self) -> 'dict[str, JsonValue]'
```

Return the grammar declaration as JSON-serializable data.

#### `GrammarDeclaration.from_data`

Class method.

```text
GrammarDeclaration.from_data(cls, data: 'object') -> 'GrammarDeclaration'
```

Decode one strict grammar declaration from JSON-compatible data.

### `GrammarHole`

```text
GrammarHole(variable: 'AttributeValue', nonterminal: 'QualifiedName') -> None
```

Bind one named pattern variable to a declared nonterminal.

#### `GrammarHole.to_data`

Method.

```text
GrammarHole.to_data(self) -> 'dict[str, JsonValue]'
```

Return the hole declaration as JSON-serializable data.

#### `GrammarHole.from_data`

Class method.

```text
GrammarHole.from_data(cls, data: 'object') -> 'GrammarHole'
```

Decode one strict hole declaration from JSON-compatible data.

### `GrammarInput`

```text
GrammarInput(tokens: 'tuple[GrammarInputToken, ...]') -> None
```

Hold the experimental typed token sequence retained by a parse forest.

#### `GrammarInput.to_data`

Method.

```text
GrammarInput.to_data(self) -> 'dict[str, JsonValue]'
```

Return this experimental typed grammar input as strict JSON data.

#### `GrammarInput.from_data`

Class method.

```text
GrammarInput.from_data(cls, data: 'object') -> 'GrammarInput'
```

Decode one strict experimental typed grammar input.

#### `GrammarInput.from_symbols`

Class method.

```text
GrammarInput.from_symbols(cls, symbols: 'Sequence[str]') -> 'GrammarInput'
```

Wrap raw source symbols with one identity realization apiece.

#### `GrammarInput.from_graph`

Class method.

```text
GrammarInput.from_graph(cls, graph: 'Graph', selector: 'Selector', symbol_attribute: 'QualifiedName', realization_attribute: 'QualifiedName', offsets: 'OffsetProfile', *, ordering: 'DeclaredOrder | None' = None) -> 'GrammarInput'
```

Bind declared graph items to typed grammar tokens with raw offsets.

### `GrammarInputToken`

```text
GrammarInputToken(symbol: 'str', realization: 'tuple[Realization, ...]', provenance: 'tuple[str, ...]' = (), source: 'ItemRef | None' = None, span: 'SourceSpan | None' = None) -> None
```

Carry one experimental typed source symbol and its target alternatives.

#### `GrammarInputToken.to_data`

Method.

```text
GrammarInputToken.to_data(self) -> 'dict[str, JsonValue]'
```

Return this experimental typed input token as strict JSON data.

#### `GrammarInputToken.from_data`

Class method.

```text
GrammarInputToken.from_data(cls, data: 'object') -> 'GrammarInputToken'
```

Decode one strict experimental typed input token.

### `GrammarRule`

```text
GrammarRule(left: 'QualifiedName', source: 'GrammarPattern', target: 'GrammarPattern', boundary: 'AttributeValue' = AttributeValue(name=QualifiedName(namespace='urn:tiergraph:grammar', local_name='boundary'), value_type=<XsdType.STRING: 'string'>, lexical='complete'), awaited_variables: 'tuple[AttributeValue, ...]' = (), weight: 'AttributeValue | None' = None, provenance: 'tuple[AttributeValue, ...]' = ()) -> None
```

Declare one directional pairing of source and target patterns.

#### `GrammarRule.to_data`

Method.

```text
GrammarRule.to_data(self) -> 'dict[str, JsonValue]'
```

Return the directional rule as JSON-serializable data.

#### `GrammarRule.from_data`

Class method.

```text
GrammarRule.from_data(cls, data: 'object') -> 'GrammarRule'
```

Decode one strict directional rule from JSON-compatible data.

#### `GrammarRule.effective_weight`

Property.

```text
GrammarRule.effective_weight(self) -> 'AttributeValue'
```

Return the declared weight or the unit rule cost.

### `GrammarTerminal`

```text
GrammarTerminal(text: 'AttributeValue') -> None
```

Carry one source or target terminal as a canonical XSD string value.

#### `GrammarTerminal.to_data`

Method.

```text
GrammarTerminal.to_data(self) -> 'dict[str, JsonValue]'
```

Return the terminal declaration as JSON-serializable data.

#### `GrammarTerminal.from_data`

Class method.

```text
GrammarTerminal.from_data(cls, data: 'object') -> 'GrammarTerminal'
```

Decode one strict terminal declaration from JSON-compatible data.

### `LoweredGrammar`

```text
LoweredGrammar(declaration: 'GrammarDeclaration', program: 'Program', as_built: 'AsBuilt') -> None
```

Pair a grammar with its replayable ordered-hedge construction.

#### `LoweredGrammar.to_data`

Method.

```text
LoweredGrammar.to_data(self) -> 'dict[str, JsonValue]'
```

Return the declaration, graph, and construction fingerprint.

### `ParseForest`

```text
ParseForest(graph: 'Graph', program: 'Program', root: 'ItemRef', fold: 'FoldDeclaration[bool]', declaration: 'GrammarDeclaration', collapsed: 'bool' = True, input: 'GrammarInput | None' = None) -> None
```

Carry a machine-built parse forest and its Boolean interpretation.

#### `ParseForest.recognized`

Method.

```text
ParseForest.recognized(self, *, budget: 'WorkBudget | WorkMeter | None' = None) -> 'bool'
```

Return whether the designated start span has a derivation.

An optional work budget limits evaluation.

#### `ParseForest.result`

Method.

```text
ParseForest.result(self, *, budget: 'WorkBudget | WorkMeter | None' = None) -> 'FoldResult[bool]'
```

Return the Boolean fold result within an optional work budget.

#### `ParseForest.count`

Method.

```text
ParseForest.count(self, *, budget: 'WorkBudget | WorkMeter | None' = None) -> 'int'
```

Count derivations when the grammar lies in the finite-fold domain.

An optional work budget limits evaluation.

#### `ParseForest.best`

Method.

```text
ParseForest.best(self, count: 'int' = 1, *, budget: 'WorkBudget | WorkMeter | None' = None) -> 'tuple[BestDerivation, ...]'
```

Return up to ``count`` cheapest derivations, by exact total cost.

The grammar must lie in the finite-fold domain. Costs are exact and the
returned order is nondecreasing by cost. Among derivations of equal cost a
deterministic subset is returned; that tie selection is not guaranteed to be a
globally canonical one, because ranking keeps the cheapest by cost rather than
by witness identity.

#### `ParseForest.to_data`

Method.

```text
ParseForest.to_data(self) -> 'dict[str, JsonValue]'
```

Return the forest, root, fingerprint, and Boolean answer as JSON data.

### `Realization`

```text
Realization(tokens: 'tuple[str, ...]', provenance: 'tuple[str, ...]' = (), weight: 'Decimal | None' = None) -> None
```

Carry one experimental target-token alternative for a typed input token.

#### `Realization.to_data`

Method.

```text
Realization.to_data(self) -> 'dict[str, JsonValue]'
```

Return this experimental realization as strict JSON data.

#### `Realization.from_data`

Class method.

```text
Realization.from_data(cls, data: 'object') -> 'Realization'
```

Decode one strict experimental target realization.

### `RuleApplication`

```text
RuleApplication(rule_index: 'int', provenance: 'tuple[AttributeValue, ...]', source_span: 'SourceSpan', witness: 'str') -> None
```

Carry one experimental generated rule application in witness order.

#### `RuleApplication.to_data`

Method.

```text
RuleApplication.to_data(self) -> 'dict[str, JsonValue]'
```

Return this experimental application record as strict JSON data.

#### `RuleApplication.from_data`

Class method.

```text
RuleApplication.from_data(cls, data: 'object') -> 'RuleApplication'
```

Decode one strict experimental application record.

### `SourceSpan`

```text
SourceSpan(partition: 'str | None', origin: 'int', end: 'int') -> None
```

Carry one experimental half-open source span.

#### `SourceSpan.to_data`

Method.

```text
SourceSpan.to_data(self) -> 'dict[str, JsonValue]'
```

Return this experimental half-open source span as strict JSON data.

#### `SourceSpan.from_data`

Class method.

```text
SourceSpan.from_data(cls, data: 'object', path: 'str' = 'source span') -> 'SourceSpan'
```

Decode one strict experimental half-open source span.

### `TargetLattice`

```text
TargetLattice(graph: 'Graph', root: 'ItemRef', fold: 'FoldDeclaration[PathValue]', input: 'GrammarInput', declaration: 'GrammarDeclaration', cyclic: 'bool') -> None
```

View an experimental keep-all target graph without enumerating paths.

#### `TargetLattice.best`

Method.

```text
TargetLattice.best(self, count: 'int' = 1, *, budget: 'WorkBudget | WorkMeter | None' = None) -> 'GenerationResult'
```

Project up to ``count`` ranked targets from the retained graph.

An optional work budget limits projection.

#### `TargetLattice.to_data`

Method.

```text
TargetLattice.to_data(self) -> 'dict[str, JsonValue]'
```

Return the versioned experimental keep-all lattice envelope.

### `TargetPiece`

```text
TargetPiece(token: 'str', witness: 'str', application: 'RuleApplication', input_provenance: 'tuple[str, ...]' = (), source: 'ItemRef | None' = None, spans: 'tuple[SourceSpan, ...]' = ()) -> None
```

Carry one experimental emitted token and its introducing provenance.

#### `TargetPiece.span`

Property.

```text
TargetPiece.span(self) -> 'SourceSpan | None'
```

Return the single source interval when the coverage lies in one partition.

#### `TargetPiece.to_data`

Method.

```text
TargetPiece.to_data(self) -> 'dict[str, JsonValue]'
```

Return this experimental target piece as strict JSON data.

#### `TargetPiece.from_data`

Class method.

```text
TargetPiece.from_data(cls, data: 'object') -> 'TargetPiece'
```

Decode one strict experimental emitted target piece.

### `best`

```text
best(grammar: 'LoweredGrammar | ParseForest', input_tokens: 'Sequence[str] | GrammarInput | None' = None, count: 'int' = 1, *, budget: 'WorkBudget | WorkMeter | None' = None) -> 'tuple[BestDerivation, ...]'
```

Return folded derivations within an optional work budget.

### `count`

```text
count(grammar: 'LoweredGrammar | ParseForest', input_tokens: 'Sequence[str] | None' = None, *, budget: 'WorkBudget | WorkMeter | None' = None) -> 'int'
```

Return the derivation count within an optional work budget.

### `generate`

```text
generate(grammar: 'LoweredGrammar | ParseForest', input_tokens: 'Sequence[str] | GrammarInput | None' = None, *, count: 'int' = 1, budget: 'WorkBudget | WorkMeter | None' = None) -> 'GenerationResult'
```

Return target materializations within an optional work budget.

### `grammar_loads`

```text
grammar_loads(source: 'str | bytes') -> 'GrammarDeclaration'
```

Decode a strict grammar declaration from UTF-8 JSON text or bytes.

The text is read under the same envelope, encoding, and syntax stages the
graph document reader applies, so a caller routes on a declared stage here
as well as there.

### `lower_grammar`

```text
lower_grammar(declaration: 'GrammarDeclaration', namespace: 'str' = 'urn:tiergraph:grammar') -> 'LoweredGrammar'
```

Lower a grammar through machine opcodes to an ordered hedge.

### `recognize`

```text
recognize(grammar: 'LoweredGrammar', input_tokens: 'Sequence[str] | GrammarInput', namespace: 'str' = 'urn:tiergraph:grammar:chart', *, collapse_units: 'bool' = True, budget: 'WorkBudget | WorkMeter | None' = None) -> 'ParseForest'
```

Build a chart forest for token input using polynomial span deduction.

For a fixed grammar whose longest source pattern has length ``m``, the
exhaustive boundary discipline takes ``O(n^(m+1))`` time and polynomial
space in input length ``n``.

### `target_lattice`

```text
target_lattice(forest: 'ParseForest', input: 'GrammarInput | None' = None, *, root: '_ChartKey | None' = None) -> 'TargetLattice'
```

Return an experimental keep-all target view over one retained forest.

## Kernel

### `AttributeDeclaration`

```text
AttributeDeclaration(name: 'QualifiedName', domain: 'AttributeDomain', value_type: 'AttributeType') -> None
```

Declare an optional, at-most-one value for one domain and value type.

Absence means absent: attributes have no defaults, deliberately, because a
default would put a value in the reading that is missing from graph bytes.

#### `AttributeDeclaration.to_data`

Method.

```text
AttributeDeclaration.to_data(self) -> 'dict[str, JsonValue]'
```

Return the declaration as JSON-serializable data.

### `AttributeDomain`

```text
AttributeDomain(*values)
```

The closed set of places where a declared attribute may occur.

#### `AttributeDomain` members

- `ITEM` = `item`
- `TIER` = `tier`
- `RELATION_DECLARATION` = `relation_declaration`
- `RELATION_INSTANCE` = `relation_instance`
- `BOUNDARY` = `boundary`
- `DOCUMENT` = `document`

### `AttributeValue`

```text
AttributeValue(name: 'QualifiedName', value_type: 'XsdType', lexical: 'str') -> None
```

Carry one named typed value in its XSD canonical lexical form.

#### `AttributeValue.to_data`

Method.

```text
AttributeValue.to_data(self) -> 'dict[str, JsonValue]'
```

Use lexical strings so every XSD value remains valid JSON.

### `Attribute`

```text
type Attribute = tiergraph.core.AttributeValue | tiergraph.core.JsonAttributeValue
```

### `AttributeType`

```text
type AttributeType = tiergraph.core.XsdType | tiergraph.core.JsonType
```

### `JsonAttributeValue`

```text
JsonAttributeValue(name: 'QualifiedName', value: 'JsonValue') -> 'None'
```

Carry an owned immutable JSON literal, retaining exact primitive kinds.

#### `JsonAttributeValue.to_value`

Method.

```text
JsonAttributeValue.to_value(self) -> 'JsonValue'
```

Return caller-owned plain JSON data without exposing graph storage.

#### `JsonAttributeValue.to_data`

Method.

```text
JsonAttributeValue.to_data(self) -> 'dict[str, JsonValue]'
```

Return the structured native attribute variant, including null.

### `JsonType`

```text
JsonType(*values)
```

Name structured literal data independently of XSD lexical values.

#### `JsonType` members

- `JSON` = `json`

### `BipartiteRelationDeclaration`

```text
BipartiteRelationDeclaration(name: 'QualifiedName', left_type: 'QualifiedName', right_type: 'QualifiedName', left_endpoint: 'RelationEndpointKind' = <RelationEndpointKind.ITEM: 'item'>, right_endpoint: 'RelationEndpointKind' = <RelationEndpointKind.ITEM: 'item'>, single_parent: 'bool' = False, acyclic: 'bool' = False, attributes: 'tuple[Attribute, ...]' = ()) -> None
```

Declare typed links and the graph invariants they promise.

Unlike scalar ``XsdType`` values, a relation types its referents through
``left_type`` and ``right_type`` and validates its ``single_parent`` and
``acyclic`` promises.

#### `BipartiteRelationDeclaration.to_data`

Method.

```text
BipartiteRelationDeclaration.to_data(self) -> 'dict[str, JsonValue]'
```

Return the declaration as JSON-serializable data.

### `Boundary`

```text
Boundary(reference: 'BoundaryRef | DurableBoundaryRef', attributes: 'tuple[Attribute, ...]') -> None
```

Hold values for one addressable boundary while empty boundaries stay derived.

#### `Boundary.to_data`

Method.

```text
Boundary.to_data(self) -> 'dict[str, JsonValue]'
```

Return the boundary and its values as JSON-serializable data.

### `BoundarySide`

```text
BoundarySide(*values)
```

Choose the boundary immediately before or after an anchor.

#### `BoundarySide` members

- `BEFORE` = `before`
- `AFTER` = `after`

### `Graph`

```text
Graph(namespaces: 'tuple[NamespaceDeclaration, ...]', tiers: 'tuple[Tier, ...]', relation_declarations: 'tuple[RelationDeclaration, ...]', relations: 'tuple[RelationInstance, ...]' = (), attribute_declarations: 'tuple[AttributeDeclaration, ...]' = (), boundary_values: 'tuple[Boundary, ...]' = (), attributes: 'tuple[Attribute, ...]' = (), polyadic_relations: 'tuple[PolyadicRelationInstance, ...]' = (), seals: 'tuple[Seal, ...]' = (), layers: 'tuple[Layer, ...]' = ()) -> None
```

Hold a validated immutable graph and derive order and empty boundaries.

Collections keyed by names or references are canonicalized because supply
order has no graph meaning: namespaces, relation and attribute declarations,
every attribute-value collection, seals, layers and the facts within each
layer, sparse boundary values, and relation-side allowed kinds and tiers.
Tiers, tier items, relation instances, and polyadic endpoint sequences
remain ordered because their sequence carries graph meaning.

#### `Graph.layer_values`

Method.

```text
Graph.layer_values(self, subject: 'LayerSubject', name: 'QualifiedName', delivery: 'Delivery') -> 'tuple[Attribute, ...]'
```

Return what the explicit delivery reads at this live subject and name.

#### `Graph.consensus`

Method.

```text
Graph.consensus(self, subject: 'LayerSubject', name: 'QualifiedName', delivery: 'Delivery') -> 'Consensus'
```

Report every delivered statement and whether canonical values agree.

#### `Graph.disagreements`

Method.

```text
Graph.disagreements(self, delivery: 'Delivery') -> 'tuple[Consensus, ...]'
```

Return only delivered subject/name rows carrying unequal readings.

#### `Graph.flatten`

Method.

```text
Graph.flatten(self, delivery: 'Delivery') -> 'Graph'
```

Write selected readings into a layerless base, refusing ambiguity/orphans.

#### `Graph.share_values`

Method.

```text
Graph.share_values(self) -> 'Graph'
```

Return an equal graph sharing repeated immutable retained values.

Value sharing applies to equal item attributes and structural item
coordinates used by relation instances.  It never mutates this graph
or any value reachable from it; changed carriers are rebuilt and the
returned graph owns fresh derived indexes.  During the call, peak
memory is therefore about the original graph plus the result.  The
retained-memory saving applies after the caller drops the original
graph.

#### `Graph.prune_orphans`

Method.

```text
Graph.prune_orphans(self) -> 'Graph'
```

Return a graph without orphaned layer facts.

Orphans are ordinary graph content, so this operation is explicit and
changes functional equivalence.  Use an edit journal when the removed
facts must remain undoable or reportable.

#### `Graph.compact`

Method.

```text
Graph.compact(self) -> 'Graph'
```

Return a graph with orphan facts removed and retained values shared.

Live durable identifiers and relation positions are already compact:
removal deletes their records and rewrites structural indexes.  This
operation performs the remaining content cleanup and applies the same
opt-in value interning as :meth:`share_values`.

#### `Graph.promotion`

Method.

```text
Graph.promotion(self, tier: 'QualifiedName') -> 'bool'
```

Report whether every item on a tier carries durable identity.

#### `Graph.boundaries`

Method.

```text
Graph.boundaries(self, tier: 'QualifiedName') -> 'tuple[Boundary, ...]'
```

Return every addressable boundary with sparse values joined on demand.

#### `Graph.canonical_items`

Method.

```text
Graph.canonical_items(self) -> 'tuple[ItemRef, ...]'
```

Compute tier-major canonical order without storing it.

Durable items are returned as this graph's own references; their values
are unchanged.

#### `Graph.item_type`

Method.

```text
Graph.item_type(self, reference: 'ItemRef') -> 'QualifiedName'
```

Return the type supplied by simple membership or refuse an untyped tier.

#### `Graph.resolve_item`

Method.

```text
Graph.resolve_item(self, reference: 'ItemRef | DurableItemRef') -> 'ItemRef'
```

Resolve either identity level to the item's current coordinate.

#### `Graph.resolve_boundary`

Method.

```text
Graph.resolve_boundary(self, reference: 'BoundaryRef | DurableBoundaryRef') -> 'BoundaryRef'
```

Resolve either identity level to the boundary's current coordinate.

#### `Graph.promote_item`

Method.

```text
Graph.promote_item(self, reference: 'ItemRef', durable_id: 'str') -> 'tuple[Graph, DurableItemRef]'
```

Return a graph carrying the caller's semantic id for one item.

The durable id is as-built content, so adding it changes canonical bytes
and the construction fingerprint.  Repeating the same id is idempotent;
a different id is refused and never replaces the established identity.

#### `Graph.promote_boundary`

Method.

```text
Graph.promote_boundary(self, reference: 'BoundaryRef', durable_id: 'str') -> 'tuple[Graph, DurableBoundaryRef]'
```

Return a graph whose boundary anchor has durable identity.

Promoting an interior boundary promotes its anchor item.  That durable
id is as-built content, so adding it changes canonical bytes and the
construction fingerprint.  An anchor carrying a different id refuses
the requested boundary identity rather than replacing its own.

Demotion restores a stored boundary value to coordinate addressing. If
no value is stored, demotion is a no-op because there is no boundary
record to rewrite. It is an exact inverse when the interior anchor
already carried the id. If promotion created that item id, demote the
item separately for exact restoration.

#### `Graph.promote_relation`

Method.

```text
Graph.promote_relation(self, target: 'RelationTarget', durable_id: 'str') -> 'tuple[Graph, DurableRelationRef | DurablePolyadicRef]'
```

Return a graph carrying the caller's semantic id for one instance.

#### `Graph.demote_item`

Method.

```text
Graph.demote_item(self, reference: 'DurableItemRef') -> 'Graph'
```

Return a graph without this item's durable identity.

#### `Graph.demote_boundary`

Method.

```text
Graph.demote_boundary(self, reference: 'DurableBoundaryRef') -> 'Graph'
```

Return a graph storing this boundary value by coordinate.

A valid durable boundary with no stored value has no boundary record to
rewrite, so demotion is a no-op.

#### `Graph.demote_relation`

Method.

```text
Graph.demote_relation(self, reference: 'DurableRelationRef | DurablePolyadicRef') -> 'Graph'
```

Return a graph without this relation instance's durable identity.

#### `Graph.to_data`

Method.

```text
Graph.to_data(self) -> 'dict[str, JsonValue]'
```

Return graph content in canonical declaration order as JSON data.

#### `Graph.seal`

Method.

```text
Graph.seal(self, carrier: 'SealedCarrier', sealed: 'int') -> 'Graph'
```

Return a graph sealing this much of one carrier, refusing a retreat.

#### `Graph.unseal`

Method.

```text
Graph.unseal(self, carrier: 'SealedCarrier', sealed: 'int') -> 'Graph'
```

Return a graph whose seal on one carrier stands lower than it did.

#### `Graph.drop_seal`

Method.

```text
Graph.drop_seal(self, carrier: 'SealedCarrier') -> 'Graph'
```

Return a graph with no seal record for this carrier.

#### `Graph.is_sealed`

Method.

```text
Graph.is_sealed(self, coordinate: 'ItemRef | BoundaryRef') -> 'bool'
```

Report whether this coordinate stands inside its carrier's seal.

#### `Graph.edit`

Method.

```text
Graph.edit(self, journal: 'Journal | None' = None) -> 'GraphEditor | JournalEditor'
```

Return a mutable editor holding a copy of this graph's content.

The editor answers the same operations this graph answers, and answers
them in place: one validation runs at ``freeze()`` instead of one per
operation.  Whether an operation rewrites or mutates follows from the
carrier the caller holds, never from an argument passed to it.

This editor has no clock profile. Structural edits can therefore leave
an existing :class:`tiergraph.clock.ClockProfile` invalid without a
rebinding refusal or report. Use ``ClockProfile.edit()`` when clock
validity and the explicit rebinding policy must be preserved.

#### `Graph.declare`

Method.

```text
Graph.declare(self, declaration: 'EditDeclaration', at: 'int | None' = None) -> 'Graph'
```

Return a new graph carrying one declaration at its carrier position.

#### `Graph.undeclare`

Method.

```text
Graph.undeclare(self, target: 'str | QualifiedName | EditDeclaration') -> 'Graph'
```

Return a graph without one unused declaration.

A bare ``str`` selects a namespace prefix. A qualified name shared by
declaration kinds is ambiguous; passing the declaration value itself
selects its kind. The operation refuses, before changing an editor,
while any graph content or another declaration depends on the selected
declaration.

#### `Graph.set_attribute`

Method.

```text
Graph.set_attribute(self, target: 'EditTarget', value: 'Attribute') -> 'Graph'
```

Return a new graph whose target carries this value under its name.

A structural relation reference names a position in this graph's
current relation content.

#### `Graph.remove_attribute`

Method.

```text
Graph.remove_attribute(self, target: 'EditTarget', name: 'QualifiedName') -> 'Graph'
```

Return a new graph whose target no longer carries this name.

A structural relation reference names a position in this graph's
current relation content.

#### `Graph.insert_item`

Method.

```text
Graph.insert_item(self, tier: 'QualifiedName', index: 'int', item: 'Item') -> 'Graph'
```

Return a new graph with one more item at this tier index.

#### `Graph.insert_items`

Method.

```text
Graph.insert_items(self, tier: 'QualifiedName', index: 'int', items: 'Iterable[Item]') -> 'Graph'
```

Return a new graph with these items at this tier index.

The input is materialized before the tier and index are validated, so
an exception raised while iterating it takes precedence over either
validation refusal.

#### `Graph.remove_item`

Method.

```text
Graph.remove_item(self, reference: 'ItemRef | DurableItemRef') -> 'Graph'
```

Return a new graph without this item.

#### `Graph.remove_items`

Method.

```text
Graph.remove_items(self, tier: 'QualifiedName', index: 'int', count: 'int') -> 'Graph'
```

Return a new graph without one contiguous run of items.

#### `Graph.replace_item`

Method.

```text
Graph.replace_item(self, reference: 'ItemRef | DurableItemRef', item: 'Item') -> 'Graph'
```

Return a new graph with this item's values replaced.

#### `Graph.replace_subtree`

Method.

```text
Graph.replace_subtree(self, root: 'ItemRef | DurableItemRef', containment: 'QualifiedName | Iterable[QualifiedName]', new: 'Subtree', policies: 'ReplacementPolicies | None' = None) -> 'Graph'
```

Return a graph with one root's containment descendants replaced.

#### `Graph.swap_subtrees`

Method.

```text
Graph.swap_subtrees(self, first: 'ItemRef | DurableItemRef', second: 'ItemRef | DurableItemRef', containment: 'QualifiedName | Iterable[QualifiedName]', first_policies: 'ReplacementPolicies | None' = None, second_policies: 'ReplacementPolicies | None' = None) -> 'Graph'
```

Return a graph with two non-nested descendant sets exchanged.

#### `Graph.move_item`

Method.

```text
Graph.move_item(self, reference: 'ItemRef | DurableItemRef', index: 'int') -> 'Graph'
```

Return a new graph with this item at another index of its own tier.

#### `Graph.swap_items`

Method.

```text
Graph.swap_items(self, first: 'ItemRef | DurableItemRef', second: 'ItemRef | DurableItemRef') -> 'Graph'
```

Return a new graph with two items of one tier exchanged.

#### `Graph.add_relation`

Method.

```text
Graph.add_relation(self, instance: 'RelationInstance | PolyadicRelationInstance', at: 'int | None' = None) -> 'Graph'
```

Return a new graph carrying one relation instance at a position.

#### `Graph.remove_relation`

Method.

```text
Graph.remove_relation(self, target: 'RelationTarget') -> 'Graph'
```

Return a new graph without the relation instance this names.

A structural reference names a position in this graph's current
relation content.

#### `Graph.set_endpoints`

Method.

```text
Graph.set_endpoints(self, target: 'RelationTarget', sources: 'RelationEndpointRef | Iterable[RelationEndpointRef]', targets: 'RelationEndpointRef | Iterable[RelationEndpointRef]') -> 'Graph'
```

Return a graph with one instance's endpoint side or sides replaced.

#### `Graph.add_layer`

Method.

```text
Graph.add_layer(self, name: 'LayerName') -> 'Graph'
```

Return a graph carrying a new empty layer.

#### `Graph.remove_layer`

Method.

```text
Graph.remove_layer(self, name: 'LayerName') -> 'Graph'
```

Return a graph without this empty layer.

#### `Graph.put_fact`

Method.

```text
Graph.put_fact(self, layer: 'LayerName', fact: 'LayerFact') -> 'Graph'
```

Return a graph with this layer fact added or replaced.

#### `Graph.remove_fact`

Method.

```text
Graph.remove_fact(self, layer: 'LayerName', subject: 'LayerSubject', name: 'QualifiedName') -> 'Graph'
```

Return a graph without this exactly addressed layer fact.

### `GraphCarrier`

```text
GraphCarrier(*values)
```

Name the graph's ordered carriers that are not a tier's items.

#### `GraphCarrier` members

- `RELATIONS` = `relations`
- `POLYADIC_RELATIONS` = `polyadic_relations`

### `GraphValidationError`

```text
GraphValidationError(message: 'str', stage: 'RefusalStage' = <RefusalStage.SEMANTICS: 9>) -> 'None'
```

Report a declaration or graph-contract validation failure.

A caller meets refusals from two channels and should have to learn one
vocabulary, so this failure ranks in the same order under the same base, and
carries its ``stage`` as data rather than prose.  The stage defaults to
``SEMANTICS`` because a violated declaration or graph contract is semantic
by nature: the document parsed, its shapes held, and what it says is still
not sayable.  Every raise site in this package takes that default; a site
whose condition is sharper may name one, and the argument is kept for that
reason and for a caller constructing one of these itself.  The message stays
first so an existing raise reads unchanged.

A graph contract is one condition about the whole graph rather than a node
whose siblings are still judged, so ``also`` is empty here.

This is still a ``ValueError``, so every caller that already catches one
still does.

### `Item`

```text
Item(durable_id: 'str | None' = None, attributes: 'tuple[Attribute, ...]' = ()) -> None
```

Represent a tier member with attributes and a durable identifier seam.

#### `Item.to_data`

Method.

```text
Item.to_data(self) -> 'dict[str, JsonValue]'
```

Return the item as JSON-serializable data.

### `NamespaceDeclaration`

```text
NamespaceDeclaration(prefix: 'str', namespace: 'str') -> None
```

Bind a document-local prefix to one namespace URI.

#### `NamespaceDeclaration.name`

Property.

```text
NamespaceDeclaration.name(self) -> 'str'
```

Return the prefix used as the declaration key.

#### `NamespaceDeclaration.to_data`

Method.

```text
NamespaceDeclaration.to_data(self) -> 'dict[str, JsonValue]'
```

Return the prefix binding as JSON-serializable data.

### `PolyadicRelationDeclaration`

```text
PolyadicRelationDeclaration(name: 'QualifiedName', sources: 'RelationSideDeclaration', targets: 'RelationSideDeclaration', unique_sources: 'bool' = False, distinct_targets: 'bool' = False, single_parent: 'bool' = False, acyclic: 'bool' = False, targets_subset_of: 'QualifiedName | None' = None, attributes: 'tuple[Attribute, ...]' = ()) -> None
```

Declare ordered endpoint sequences and general incidence constraints.

``unique_sources`` makes each source occur in at most one instance.
``distinct_targets`` forbids repeated candidates within an instance.
``targets_subset_of`` requires each instance's targets to be members of the
named relation's targets for the same source.  These are the structural
contracts commonly called containment, choice, and selection membership;
their domain names do not belong in the kernel.

Empty sources or targets are admitted only by that side's ``allow_empty``.
An empty side contributes no edges to acyclicity, no parent assignments to
``single_parent``, and no source keys to source uniqueness or subset checks.
Its arity bounds are deliberately bypassed: emptiness is an explicit case,
not an accidental consequence of a zero minimum.

#### `PolyadicRelationDeclaration.to_data`

Method.

```text
PolyadicRelationDeclaration.to_data(self) -> 'dict[str, JsonValue]'
```

Return the declaration as JSON-serializable data.

### `PolyadicRelationInstance`

```text
PolyadicRelationInstance(declaration: 'QualifiedName', sources: 'tuple[RelationEndpointRef, ...]', targets: 'tuple[RelationEndpointRef, ...]', durable_id: 'str | None' = None, attributes: 'tuple[Attribute, ...]' = ()) -> None
```

Link two declared, ordered endpoint sequences.

#### `PolyadicRelationInstance.to_data`

Method.

```text
PolyadicRelationInstance.to_data(self) -> 'dict[str, JsonValue]'
```

Return the ordered sides as JSON-serializable arrays.

### `QualifiedName`

```text
QualifiedName(namespace: 'str', local_name: 'str') -> None
```

Identify a declaration by namespace URI and local name.

#### `QualifiedName.to_data`

Method.

```text
QualifiedName.to_data(self) -> 'dict[str, JsonValue]'
```

Return the expanded name independently of document prefix choices.

### `BudgetExhausted`

```text
BudgetExhausted(operation: 'str', exhaustion: 'Exhaustion', spent: 'int', budget: 'WorkBudget') -> 'None'
```

Refuse an operation whose declared work budget ran out.

### `Exhaustion`

```text
Exhaustion(*values)
```

Name which declared work bound stopped an operation.

#### `Exhaustion` members

- `STEPS` = `steps`
- `DEADLINE` = `deadline`

### `Refusal`

```text
Refusal(stage: 'RefusalStage', message: 'str', also: 'Iterable[Refusal]' = ()) -> 'None'
```

Refuse one read, naming its stage and applicable conditions at its site.

``stage`` places the refusal in the declared total order, and ``also``
carries the further conditions applicable at the refusing site, each a
refusal in its own right.  It is not a census of the document.  Both are
data rather than prose, so a caller acts on the order without matching
message text.  Both are declared on the class as well as assigned, so a
caller reads them as fields of what it caught rather than recovering them
with ``getattr``.

This is the one base every staged refusal has.  Wherever the order is
observed it is observed whole, so ``except Refusal`` has to catch all of it:
a base that covered a prefix of the order would send a caller who read the
declaration past the ranks it left out.  Which readers observe the order,
and where one of them answers unstaged instead, is stated in the format
document rather than here -- this base is about the ranks a caller must be
able to catch, not about which readers produce them.  Subclasses say which
channel refused, never which ranks a caller has to expect.

A ``Refusal`` is a ``ValueError``, so every caller that already catches one
still does.

Not every refusal this package raises is staged, and the boundary is worth
stating because ``except Refusal`` is silent on the other side of it.  A
*declaration* refuses its own construction with a plain ``ValueError`` --
``SealDeclaration``, ``FoldDeclaration``, ``AttributeValuation``,
``ActionDeclaration`` and ``ReactDeclaration`` all refuse an empty name that
way, and ``DistributionWitness`` refuses its own the same way.
Those are refusals about the description a caller wrote, not about a
document or a graph, so there is no read for a stage to rank them within.
What carries a stage is the refusal of *content*: a document a reader
refuses, and a graph ``GraphValidationError`` refuses at construction or
validation.  A caller that wants both catches ``ValueError``.

``tiergraph.Refusal`` is the staged document-reader refusal.  The other
exported classes ending in ``Refusal`` -- ``StarRefusal``,
``EffectRefusal``, ``ExactnessRefusal``, ``PathRefusal``, and
``ProfileRegistrationRefusal`` -- are ``ValueError`` subclasses carrying
their own subsystem's data.  They have no document-reader stage.

It is declared here, beside ``RefusalStage`` and for the same reason: this
module is the base every other imports, so the channel that refuses from
here can share the base without the cycle that reaching upward would create.

### `RefusalStage`

```text
RefusalStage(*values)
```

Number the classes a refusal can belong to, lowest reported first.

A reader routinely meets several conditions at once.  The stage numbers put
them in one order, so a caller is told the condition that explains the rest
rather than whichever check happened to run first: a refusal at one stage
explains what a later stage would have reported, and the converse never
holds.  Bytes that are not text have no JSON to nest; a document announcing
a format this release does not implement has a field set this release cannot
judge; a member of the wrong construction has no value to place in a
declared language; a name that does not resolve cannot keep a promise.

The stages rank the conditions that apply to one node.  Nodes are read from
the outside in and members in their declared order, so an enclosing node's
condition precedes its members' whatever their stages, and the pair of a
node and a stage totally orders every condition one read can meet.

A condition is carried beside the primary one only while it stays
applicable once the primary is known.  A field set is not judged against a
declaration the document never selected, so a foreign version is reported
alone rather than with the fields that being foreign introduces.

The stage is the stable part of a refusal; the wording is diagnostic.

The vocabulary lives here, beside the other declared enumerations, because
both refusal channels have to name it: this module is the base every other
imports, so a refusal raised from here can carry a stage without the cycle
that reaching upward for it would create.

#### `RefusalStage` members

- `ENVELOPE` = `1`
- `ENCODING` = `2`
- `SYNTAX` = `3`
- `CONSTRUCTION` = `4`
- `DISCRIMINATOR` = `5`
- `SHAPE` = `6`
- `VALUE` = `7`
- `REFERENCE` = `8`
- `SEMANTICS` = `9`

### `RelationEndpointKind`

```text
RelationEndpointKind(*values)
```

Declare whether one relation endpoint is an item or a boundary.

#### `RelationEndpointKind` members

- `ITEM` = `item`
- `BOUNDARY` = `boundary`

### `RelationInstance`

```text
RelationInstance(declaration: 'QualifiedName', left: 'RelationEndpointRef', right: 'RelationEndpointRef', durable_id: 'str | None' = None, attributes: 'tuple[Attribute, ...]' = ()) -> None
```

Link item or anchored-boundary endpoints through a declared relation.

#### `RelationInstance.to_data`

Method.

```text
RelationInstance.to_data(self) -> 'dict[str, JsonValue]'
```

Return the instance as JSON-serializable data.

### `RelationSideDeclaration`

```text
RelationSideDeclaration(endpoint_kinds: 'tuple[RelationEndpointKind, ...]', tiers: 'tuple[QualifiedName, ...] | None' = None, minimum: 'int' = 1, maximum: 'int | None' = None, allow_empty: 'bool' = False) -> None
```

Constrain one explicitly ordered side of a polyadic relation.

#### `RelationSideDeclaration.to_data`

Method.

```text
RelationSideDeclaration.to_data(self) -> 'dict[str, JsonValue]'
```

Return the side contract without inventing order for its allowed sets.

### `Seal`

```text
Seal(carrier: 'SealedCarrier', sealed: 'int') -> None
```

State how much of one ordered carrier may not be disturbed.

#### `Seal.to_data`

Method.

```text
Seal.to_data(self) -> 'dict[str, JsonValue]'
```

Return the tagged carrier and sealed prefix for wire encoding.

### `SealBreach`

```text
SealBreach(carrier: 'SealedCarrier', index: 'int', detail: 'str') -> None
```

Name one sealed member that the result did not leave where it stood.

### `SealCertificate`

```text
SealCertificate(carriers: 'int', sealed_members: 'int') -> None
```

Report what a seal check could discriminate, and over how much.

``sealed_members`` counts only members whose durable identity made a
value-only comparison capable of detecting movement. Anonymous members do
not contribute: two graph values cannot reveal whether one anonymous member
moved or an indistinguishable one took its coordinate. A zero count is
therefore an explicit vacuous pass, not evidence that anonymous geometry was
preserved.

#### `SealCertificate.to_data`

Method.

```text
SealCertificate.to_data(self) -> 'dict[str, int]'
```

Return deterministic strict-JSON data.

Both counts are carried because either alone misleads. ``carriers``
without ``sealed_members`` hides a vacuous pass; ``sealed_members``
without ``carriers`` hides how much was under seal to begin with. A
reader deciding what this certificate is worth needs the ratio, not
either half.

### `SealDeclaration`

```text
SealDeclaration(name: 'str', source: 'Graph', result: 'Graph') -> None
```

Bind the seals one graph carries to the graph that claims to honor them.

The cone model is reserved until a whole-graph seal exists as one frozen base,
a mergeable delta type exists, coordinate removal is expressible within a
footprint or excluded from the mergeable set, and observed-read validation is
decided.

#### `SealDeclaration.breaches`

Method.

```text
SealDeclaration.breaches(self) -> 'tuple[SealBreach, ...]'
```

Return every sealed member the result disturbed, in carrier order.

#### `SealDeclaration.check_seals`

Method.

```text
SealDeclaration.check_seals(self) -> 'SealCertificate'
```

Demand that the result honor the source's seals, or refuse.

### `SealedCarrier`

```text
type SealedCarrier = tiergraph.core.QualifiedName | tiergraph.core.GraphCarrier
```

### `SimpleRelationDeclaration`

```text
SimpleRelationDeclaration(name: 'QualifiedName', tier: 'QualifiedName', item_type: 'QualifiedName', attributes: 'tuple[Attribute, ...]' = ()) -> None
```

Give every member of one tier its type through a depth-one relation.

#### `SimpleRelationDeclaration.to_data`

Method.

```text
SimpleRelationDeclaration.to_data(self) -> 'dict[str, JsonValue]'
```

Return the declaration as JSON-serializable data.

### `Tier`

```text
Tier(declaration: 'TierDeclaration', items: 'tuple[Item, ...]' = (), attributes: 'tuple[Attribute, ...]' = ()) -> None
```

Pair a declaration with immutable ordered members and tier attributes.

#### `Tier.to_data`

Method.

```text
Tier.to_data(self) -> 'dict[str, JsonValue]'
```

Return the tier as JSON-serializable data.

### `TierDeclaration`

```text
TierDeclaration(name: 'QualifiedName', long_name: 'str') -> None
```

Name an ordered tier without coupling its name to item identity.

#### `TierDeclaration.short_name`

Property.

```text
TierDeclaration.short_name(self) -> 'str'
```

Return the local part used as the tier's short display name.

#### `TierDeclaration.to_data`

Method.

```text
TierDeclaration.to_data(self) -> 'dict[str, JsonValue]'
```

Return the declaration as JSON-serializable data.

### `WorkBudget`

```text
WorkBudget(steps: 'int | None' = None, seconds: 'float | None' = None) -> None
```

Declare how much work one operation may do before it stops.

### `WorkMeter`

```text
WorkMeter(budget: 'WorkBudget') -> 'None'
```

Charge one budget across calls and report the work they spent.

A meter is intended for one thread or task at a time.

#### `WorkMeter.budget`

Property.

```text
WorkMeter.budget(self) -> 'WorkBudget'
```

Return the declaration this meter enforces.

#### `WorkMeter.spent`

Property.

```text
WorkMeter.spent(self) -> 'int'
```

Return logical steps charged so far, including a crossing charge.

### `XsdType`

```text
XsdType(*values)
```

The growable XSD datatype subset admitted for scalar attribute values.

In-graph references are relations, not attribute value types.  Relation
declarations type their referents and may validate structural promises;
an out-of-graph reference is honestly a string because this graph cannot
validate what it denotes.

#### `XsdType` members

- `STRING` = `string`
- `BOOLEAN` = `boolean`
- `INTEGER` = `integer`
- `DECIMAL` = `decimal`
- `DOUBLE` = `double`

## Layers

### `Consensus`

```text
Consensus(subject: 'LayerSubject', name: 'QualifiedName', readings: 'tuple[tuple[LayerName, Attribute], ...]', agreed: 'bool') -> None
```

Report every delivered reading and whether their canonical values agree.

### `Delivery`

```text
Delivery(layers: 'tuple[LayerName, ...]', read: 'LayerRead') -> None
```

Select layers in lowest-to-highest precedence order; read is explicit.

### `Layer`

```text
Layer(name: 'LayerName', facts: 'tuple[LayerFact, ...]') -> None
```

Hold one source's attribute facts and nothing structural.

#### `Layer.to_data`

Method.

```text
Layer.to_data(self) -> 'dict[str, JsonValue]'
```

Return the layer and its tagged facts as JSON-serializable data.

### `LayerFact`

```text
LayerFact(subject: 'LayerSubject', value: 'Attribute') -> None
```

State one named typed value at one subject of the base.

### `LayerName`

```text
LayerName(vocabulary: 'str', source: 'str') -> None
```

Identify a layer by its vocabulary and its producing source.

#### `LayerName.to_data`

Method.

```text
LayerName.to_data(self) -> 'dict[str, JsonValue]'
```

Return the layer identity axes as JSON-serializable data.

### `LayerRead`

```text
LayerRead(*values)
```

Choose how a delivery answers a subject several layers describe.

#### `LayerRead` members

- `FIRST` = `first`
- `LAST` = `last`
- `ALL` = `all`

### `LayerSubject`

```text
type LayerSubject = tiergraph.core.ItemRef | tiergraph.core.DurableItemRef | tiergraph.core.BoundaryRef | tiergraph.core.DurableBoundaryRef | tiergraph.core.TierRef | tiergraph.core.RelationDeclarationRef | tiergraph.core.RelationInstanceRef | tiergraph.core.DurableRelationRef | tiergraph.core.PolyadicInstanceRef | tiergraph.core.DurablePolyadicRef | tiergraph.core.DocumentRef | tiergraph.core.OrphanedSubject
```

### `OrphanedSubject`

```text
OrphanedSubject(carrier: 'SealedCarrier', was: 'ItemRef | BoundaryRef | int') -> None
```

Name where a fact stood when an edit left its subject no image.

The old coordinate and its carrier are retained, never re-anchored. Orphans
are unreachable from reads and accumulate until a caller constructs a layer
without them; ``flatten`` refuses rather than hiding that cost in the base.

## Metadata

### `FORMAT_VERSION`

Version tag written by the JSON wire codec. Current value: `0.3.0`.

### `MACHINE_VERSION`

Version tag for serialized construction programs. Current value: `2`.

### `PATCH_VERSION`

Version tag for serialized graph patches. Current value: `1`.

### `PRIMITIVE_KINDS`

Graph operation names accepted by declared edit-cost tables. Current value: `frozenset({'add_layer', 'add_relation', 'declare', 'demote_boundary', 'demote_item', 'demote_relation', 'drop_seal', 'insert_item', 'move_item', 'promote_boundary', 'promote_item', 'promote_relation', 'put_fact', 'remove_attribute', 'remove_fact', 'remove_item', 'remove_layer', 'remove_relation', 'replace_item', 'seal', 'set_attribute', 'set_endpoints', 'swap_items', 'undeclare', 'unseal'})`.

### `MAX_DOCUMENT_BYTES`

Largest UTF-8 JSON document accepted by the wire codec. Current value: `16777216`.

### `MAX_JSON_DEPTH`

Deepest JSON container nesting accepted by the wire codec; construction bounds each attribute carrier by this limit less its document envelope. Current value: `256`.

### `MAX_REPEAT_COUNT`

Largest repeat count accepted by the build machine. Current value: `10000`.

### `MAX_TOTAL_OPCODES`

Largest flattened primitive trace accepted by the build machine. Current value: `2000000`.

### `GRAMMAR_NAMESPACE`

Default namespace for lowered grammar coordinates. Current value: `urn:tiergraph:grammar`.

### `CHART_NAMESPACE`

Default namespace for grammar chart forests. Current value: `urn:tiergraph:grammar:chart`.

### `COMPLETE_BOUNDARY`

Canonical complete-boundary grammar value. Current value: `AttributeValue(name=QualifiedName(namespace='urn:tiergraph:grammar', local_name='boundary'), value_type=<XsdType.STRING: 'string'>, lexical='complete')`.

### `__version__`

Installed distribution version. Current value: `0.8.0`.

## Paths

### `AlternativeRef`

```text
AlternativeRef(owner: 'ItemRef | DurableItemRef', relation: 'QualifiedName', index: 'int') -> None
```

Select one profile-ordered alternative of an owning graph item.

### `BoundaryBinding`

```text
BoundaryBinding(reference: 'BoundaryRef | DurableBoundaryRef') -> None
```

Request resolution of one structural or durable boundary reference.

### `CanonicalPath`

```text
CanonicalPath(segments: 'tuple[str, ...]') -> None
```

Hold decoded segments of a strict, non-fragment RFC 6901 pointer.

#### `CanonicalPath.parse`

Class method.

```text
CanonicalPath.parse(cls, text: 'str') -> 'CanonicalPath'
```

Parse a strict JSON Pointer, accepting empty and refusing malformed spellings.

### `ItemBinding`

```text
ItemBinding(reference: 'ItemRef | DurableItemRef') -> None
```

Request resolution of one structural or durable item reference.

### `PathBinding`

```text
type PathBinding = tiergraph.path.ItemBinding | tiergraph.path.BoundaryBinding | tiergraph.path.AlternativeRef
```

### `PathKind`

```text
PathKind(*values)
```

Classify the graph reference produced by a path profile.

#### `PathKind` members

- `ITEM` = `item`
- `BOUNDARY` = `boundary`
- `ALTERNATIVE` = `alternative`

### `PathOffender`

```text
PathOffender(text: 'str', path: 'CanonicalPath | None' = None, segment_index: 'int | None' = None, segment: 'str | None' = None, expected_kind: 'PathKind | None' = None, actual_kind: 'PathKind | None' = None, tier: 'QualifiedName | None' = None, index: 'int | None' = None, durable_id: 'str | None' = None, profile_reason: 'str | None' = None, relation: 'QualifiedName | None' = None, available_count: 'int | None' = None) -> None
```

Carry stable structured context for a refused path operation.

### `PathProfile`

```text
PathProfile(*args, **kwargs)
```

Interpret and spell canonical paths for one explicit vocabulary.

#### `PathProfile.bind`

Method.

```text
PathProfile.bind(self, path: 'CanonicalPath', graph: 'Graph') -> 'PathBinding'
```

Convert a canonical path to a graph resolution request.

#### `PathProfile.spell`

Method.

```text
PathProfile.spell(self, binding: 'PathBinding', graph: 'Graph') -> 'CanonicalPath'
```

Project a supported graph resolution request back to a path.

#### `PathProfile.alternatives`

Method.

```text
PathProfile.alternatives(self, owner: 'ItemRef', relation: 'QualifiedName', graph: 'Graph') -> 'tuple[object, ...]'
```

Return alternatives in the profile's stable, snapshot-local order.

### `PathRefusal`

```text
PathRefusal(code: 'PathRefusalCode', offender: 'PathOffender', cause: 'Exception | None' = None) -> 'None'
```

Report a typed path failure with offender data and its original cause.

### `PathRefusalCode`

```text
PathRefusalCode(*values)
```

Identify stable classes of path refusal independently of diagnostics.

``BOUNDARY_NOT_IN_PARENT`` is reserved and is not produced by a current path
resolver or profile.

#### `PathRefusalCode` members

- `MALFORMED_POINTER` = `malformed_pointer`
- `NONCANONICAL_SEGMENT` = `noncanonical_segment`
- `UNKNOWN_FORM` = `unknown_form`
- `INVALID_SEGMENT` = `invalid_segment`
- `WRONG_KIND` = `wrong_kind`
- `UNKNOWN_TIER` = `unknown_tier`
- `OUT_OF_RANGE` = `out_of_range`
- `UNKNOWN_DURABLE_ITEM` = `unknown_durable_item`
- `UNKNOWN_DURABLE_ANCHOR` = `unknown_durable_anchor`
- `BOUNDARY_NOT_IN_PARENT` = `boundary_not_in_parent`
- `UNSPELLABLE` = `unspellable`
- `PROFILE_REFUSED` = `profile_refused`
- `ALTERNATIVE_OUT_OF_RANGE` = `alternative_out_of_range`

### `ResolvedAlternative`

```text
ResolvedAlternative(path: 'CanonicalPath', owner: 'ItemRef', relation: 'QualifiedName', index: 'int', value: 'object') -> None
```

Pair a path with one selection from a profile-ordered alternative set.

### `ResolvedBoundary`

```text
ResolvedBoundary(path: 'CanonicalPath', current: 'BoundaryRef') -> None
```

Pair the parsed path with its current structural boundary coordinate.

### `ResolvedItem`

```text
ResolvedItem(path: 'CanonicalPath', current: 'ItemRef') -> None
```

Pair the parsed path with its current structural item coordinate.

### `StructuralPathProfile`

```text
StructuralPathProfile()
```

Address items and boundaries with a domain-neutral explicit vocabulary.

Structural forms are ``/items/structural/NS/LOCAL/INDEX`` and
``/positions/structural/NS/LOCAL/INDEX``. Durable forms are
``/items/durable/ID`` and ``/positions/durable/item/ID/SIDE`` or
``/positions/durable/tier/NS/LOCAL/SIDE``.

#### `StructuralPathProfile.bind`

Method.

```text
StructuralPathProfile.bind(self, path: 'CanonicalPath', graph: 'Graph') -> 'PathBinding'
```

Interpret one of the generic structural or durable forms.

#### `StructuralPathProfile.spell`

Method.

```text
StructuralPathProfile.spell(self, binding: 'PathBinding', graph: 'Graph') -> 'CanonicalPath'
```

Spell each reference shape supported by the generic vocabulary.

#### `StructuralPathProfile.alternatives`

Method.

```text
StructuralPathProfile.alternatives(self, owner: 'ItemRef', relation: 'QualifiedName', graph: 'Graph') -> 'tuple[object, ...]'
```

Return no alternatives because this vocabulary declares none.

### `resolve_path`

```text
resolve_path(graph: 'Graph', profile: 'PathProfile', text: 'str', *, require: 'PathKind | None' = None) -> 'ResolvedItem | ResolvedBoundary | ResolvedAlternative'
```

Parse, bind, kind-check, and resolve a profile-owned graph path.

## Profiles

### `GraphProfile`

```text
GraphProfile()
```

Declare a graph role whose satisfaction one check decides.

A subclass names the profile, names the roles it reads, states in prose the
conditions its check decides and any it leaves undecided, and implements
:meth:`check`. Those are claims, and :meth:`ProfileRegistry.register` tests
them before admitting the profile.

``decides`` must name at least one condition. ``leaves_undecided`` names
conditions the profile declares in its own documentation but whose truth
this check does not establish; naming one costs a weaker outcome rather than
a refusal, which is the point -- an honest partial check outranks a silent
one.

#### `GraphProfile.check`

Class method.

```text
GraphProfile.check(cls, graph: 'Graph', roles: 'RoleBinding') -> 'None'
```

Return when ``graph`` satisfies this role, raise ``ValueError`` when not.

Every required role is bound when this runs. Any other exception is a
fault in the check rather than a verdict about the graph, and travels
out to the caller unchanged.

#### `GraphProfile.satisfaction_witness`

Class method.

```text
GraphProfile.satisfaction_witness(cls) -> 'tuple[Graph, RoleBinding]'
```

Return an arrangement this profile's check must accept.

#### `GraphProfile.refusal_witness`

Class method.

```text
GraphProfile.refusal_witness(cls) -> 'tuple[Graph, RoleBinding]'
```

Return an arrangement this profile's check must refuse.

### `JsonValueProfile`

```text
JsonValueProfile(graph: 'Graph', node_tier: 'QualifiedName', occurrence_tier: 'QualifiedName', member_relation: 'QualifiedName', value_relation: 'QualifiedName', kind_attribute: 'QualifiedName', key_attribute: 'QualifiedName', string_attribute: 'QualifiedName', boolean_attribute: 'QualifiedName', integer_attribute: 'QualifiedName', double_attribute: 'QualifiedName') -> None
```

Interpret a recursive JSON value as items joined by ordered relations.

Each value node is an ordinary item.  Container membership is an ordered
polyadic relation whose one source is the container and whose targets are
membership items.  Each membership item has exactly one value target, and
object keys are attributes of those membership items.  Keys are required in
lexical order so equivalent objects have one encoding.
Scalar leaves retain the kernel's canonical XSD lexical spelling.

Derivation provenance is deliberately not interpreted or constrained by this
profile.

#### `JsonValueProfile.value`

Method.

```text
JsonValueProfile.value(self, root: 'ItemRef') -> 'JsonValue'
```

Return the JSON value rooted at ``root``, refusing malformed neighbours.

### `OrderedRootsProfile`

```text
OrderedRootsProfile(graph: 'Graph', root_relation: 'QualifiedName', dependency_relations: 'tuple[QualifiedName, ...]') -> None
```

Read ordered stored roots and reconcile them with dependency incidence.

The root relation is one polyadic instance with an explicitly empty source
side. Its target incidence order is the declared root order. Dependency
relations determine root membership: every item on an admitted root tier
with no incoming dependency incidence is a root. Stored order adds
information, but stored membership may not contradict that derived set:
stored roots must be a subset of the inferred set. A curated ordered subset
is allowed; use :meth:`is_exhaustive` to require stored roots to equal the
inferred set.

Two narrowings bound what "parentless" means here, and neither is enforced.
Reconciliation considers exactly the caller-supplied
``dependency_relations``, and is silent about dependencies omitted from it.
Within those, it counts an incidence only when both endpoints lie on the
root relation's admitted target tiers, so an incoming dependency whose
source sits on another tier is not counted and its target is inferred a
root. A declared root is therefore parentless over the enumerated
dependencies restricted to the admitted domain, which is weaker than
parentless in the graph; enumeration is not enforcement.

#### `OrderedRootsProfile.inferred`

Method.

```text
OrderedRootsProfile.inferred(self) -> 'tuple[ItemRef, ...]'
```

Return dependency roots in canonical item order.

#### `OrderedRootsProfile.roots`

Method.

```text
OrderedRootsProfile.roots(self) -> 'tuple[ItemRef, ...]'
```

Return roots in the stored semantic incidence order.

#### `OrderedRootsProfile.is_exhaustive`

Method.

```text
OrderedRootsProfile.is_exhaustive(self) -> 'bool'
```

Return whether declared roots include every inferred parentless item.

A subset is sound because every declared root is parentless in the
sense this profile infers, which the class docstring bounds; exhaustive
consumers can use this check to require the complete inferred set.

### `PROFILES`

Hold explicitly registered profiles and enumerate the ones a graph satisfies.

Population is explicit. Nothing here scans modules or subclasses for
profiles to adopt, because a discovered profile is one nobody decided to
trust: import order would determine what a caller is told a graph satisfies,
and an accidental subclass would answer for a role its author never
published. A caller registers what it means to offer.

Enumeration is ordered by profile name, so the answer does not depend on
registration order or on interpreter hash state.

### `UNIT_COSTS`

Default symmetric graph-edit costs with reorder shortcuts priced as two edits. Current value: `CostTable(operations=mappingproxy({'add_layer': Decimal('1'), 'add_relation': Decimal('1'), 'declare': Decimal('1'), 'demote_boundary': Decimal('1'), 'demote_item': Decimal('1'), 'demote_relation': Decimal('1'), 'drop_seal': Decimal('1'), 'insert_item': Decimal('1'), 'move_item': Decimal('2'), 'promote_boundary': Decimal('1'), 'promote_item': Decimal('1'), 'promote_relation': Decimal('1'), 'put_fact': Decimal('1'), 'remove_attribute': Decimal('1'), 'remove_fact': Decimal('1'), 'remove_item': Decimal('1'), 'remove_layer': Decimal('1'), 'remove_relation': Decimal('1'), 'replace_item': Decimal('1'), 'seal': Decimal('1'), 'set_attribute': Decimal('1'), 'set_endpoints': Decimal('1'), 'swap_items': Decimal('2'), 'undeclare': Decimal('1'), 'unseal': Decimal('1')}), declarations=mappingproxy({}))`.

### `PersistedChoiceProfile`

```text
PersistedChoiceProfile(graph: 'Graph', alternatives_relation: 'QualifiedName', default_relation: 'QualifiedName') -> None
```

Read alternatives and optional persisted singleton defaults by source.

#### `PersistedChoiceProfile.candidates`

Method.

```text
PersistedChoiceProfile.candidates(self, source: 'ItemRef') -> 'tuple[ItemRef, ...]'
```

Return the source's candidates in stored incidence order.

#### `PersistedChoiceProfile.default`

Method.

```text
PersistedChoiceProfile.default(self, source: 'ItemRef') -> 'ItemRef | None'
```

Return the persisted default for a source when one is stored.

### `ProfileOutcome`

```text
ProfileOutcome(*values)
```

Say what one profile's check established about one graph.

#### `ProfileOutcome` members

- `SATISFIED` = `satisfied`
- `SATISFIED_AS_CHECKED` = `satisfied_as_checked`
- `REFUSED` = `refused`
- `NOT_APPLICABLE` = `not_applicable`

### `ProfileRegistrationRefusal`

Refuse a profile whose registration claims do not hold.

### `ProfileRegistry`

```text
ProfileRegistry() -> 'None'
```

Hold explicitly registered profiles and enumerate the ones a graph satisfies.

Population is explicit. Nothing here scans modules or subclasses for
profiles to adopt, because a discovered profile is one nobody decided to
trust: import order would determine what a caller is told a graph satisfies,
and an accidental subclass would answer for a role its author never
published. A caller registers what it means to offer.

Enumeration is ordered by profile name, so the answer does not depend on
registration order or on interpreter hash state.

#### `ProfileRegistry.register`

Method.

```text
ProfileRegistry.register(self, profile: 'type[P]') -> 'type[P]'
```

Admit one profile after testing the claims it registers under.

Refuses a profile that leaves :meth:`GraphProfile.check` or either
witness abstract, that names no condition its check decides, that
names one role or condition twice -- a role both required and optional,
or a condition both decided and left open, is named twice -- that
repeats a registered name, or whose check does not tell its own two
witnesses apart. The profile is returned so a definition can register
itself in place.

#### `ProfileRegistry.names`

Method.

```text
ProfileRegistry.names(self) -> 'tuple[str, ...]'
```

Return every registered profile name in sorted order.

#### `ProfileRegistry.profile`

Method.

```text
ProfileRegistry.profile(self, name: 'str') -> 'type[GraphProfile]'
```

Return one registered profile by name.

#### `ProfileRegistry.report`

Method.

```text
ProfileRegistry.report(self, name: 'str', graph: 'Graph', roles: 'RoleBinding') -> 'ProfileReport'
```

Report what one named profile's check establishes about a graph.

#### `ProfileRegistry.reports`

Method.

```text
ProfileRegistry.reports(self, graph: 'Graph', roles: 'RoleBinding') -> 'tuple[ProfileReport, ...]'
```

Report every registered profile against a graph, in profile-name order.

#### `ProfileRegistry.satisfied`

Method.

```text
ProfileRegistry.satisfied(self, graph: 'Graph', roles: 'RoleBinding') -> 'tuple[ProfileReport, ...]'
```

Return the reports of the profiles whose check ran and accepted.

These are the ``satisfied`` and ``satisfied_as_checked`` ones. A
profile reported ``not_applicable`` refused nothing and is still
absent, because an unanswered question is not an accepted one. Reports
are returned rather than bare names because a name alone would read as
a whole guarantee. A report carries its outcome and its unconfirmed
conditions, so a caller holding one can see how far the answer reaches.

### `ProfileReport`

```text
ProfileReport(profile: 'str', outcome: 'ProfileOutcome', confirmed: 'tuple[str, ...]', unconfirmed: 'tuple[str, ...]', reason: 'str | None' = None) -> None
```

Carry what one check established about one graph, and what it did not.

``confirmed`` holds the conditions this run decided in the graph's favor and
``unconfirmed`` the ones it did not, so the two together always name every
condition the profile declares. A refused or inapplicable run confirms
nothing, so all of them are unconfirmed: the check stopped, and which
conditions it had already passed over is not evidence a caller can use.

#### `ProfileReport.to_data`

Method.

```text
ProfileReport.to_data(self) -> 'dict[str, object]'
```

Return deterministic strict-JSON data.

Both condition lists are emitted even when one is empty, because
together they name every condition the profile declares and a reader
cannot reconstruct the second from the first. An accepting outcome with
a non-empty ``unconfirmed`` is the case that matters: the check passed
and still left something undecided.

### `RoleBinding`

```text
type RoleBinding = collections.abc.Mapping[str, RoleValue]
```

### `RoleValue`

```text
type RoleValue = tiergraph.core.QualifiedName | tuple[tiergraph.core.QualifiedName, ...] | ValueAttributeBindings | str
```

### `SpanViewProfile`

```text
SpanViewProfile(base_tier: 'QualifiedName', span_tiers: 'tuple[QualifiedName, ...]', coverage_relation: 'QualifiedName', score_attribute: 'QualifiedName', value_attribute: 'QualifiedName', base_surface_attribute: 'QualifiedName | None' = None, char_offset_attribute: 'QualifiedName | None' = None, alternative_relation: 'QualifiedName | None' = None, point_tiers: 'tuple[QualifiedName, ...]' = (), point_coverage_relation: 'QualifiedName | None' = None, value_attributes: 'tuple[tuple[QualifiedName, QualifiedName], ...]' = (), clock_face: 'str' = 'tick') -> None
```

Name the graph declarations a segmentation has to be selected among.

``coverage_relation`` and ``alternative_relation`` must name bipartite
declarations.  A span is an interval over the base tier, so each fact this
view reads is one base endpoint paired with one span item; there is no
reading of a polyadic instance's ordered sides that keeps that meaning.
Naming a non-bipartite declaration is refused rather than skipped, because
silently reading only the bipartite collection would report a partial
segmentation as a complete one.

One declaration the projection reads is deliberately absent: a span's
``label`` is the item type its tier's simple membership supplies, read
through :meth:`Graph.item_type` and falling back to the tier's short name
when the tier is untyped.  A profile names what a reading has to be
selected among, and a tier carries at most one simple membership, so there
is nothing there to select.

#### `SpanViewProfile.to_data`

Method.

```text
SpanViewProfile.to_data(self) -> 'dict[str, JsonValue]'
```

Return the declarative span-view profile document used by the CLI.

#### `SpanViewProfile.from_data`

Class method.

```text
SpanViewProfile.from_data(cls, data: 'object') -> 'SpanViewProfile'
```

Decode a strict declarative span-view profile document.

### `embed_json_value`

```text
embed_json_value(graph: 'Graph', value: 'JsonValue', *, namespace: 'NamespaceDeclaration') -> 'tuple[Graph, JsonValueProfile, ItemRef]'
```

Embed a native JSON value using an explicitly fresh namespace and prefix.

Return the extended graph, its validated value profile, and the value root.
Existing graph content and the input object remain unchanged. Both the URI
and prefix must be unused, even for null or an empty container; collisions
refuse rather than rename, alias, or overwrite existing declarations.

No owner relation is inferred: callers can link the returned root through
their own declared relation. This embeds one JSON value, not an arbitrary
graph fragment or a migration of existing profile tiers. Failure never
exposes a partially changed graph.

### `json_value_graph`

```text
json_value_graph(value: 'JsonValue', namespace: 'str' = 'urn:tiergraph:json-value') -> 'tuple[Graph, JsonValueProfile, ItemRef]'
```

Construct a standalone canonical graph for one recursively nested JSON value.

### `span_view`

```text
span_view(graph: 'Graph', profile: 'SpanViewProfile', *, alternatives: 'bool' = False) -> 'SpanView'
```

Read a segmentation and its coverage entirely through the public graph API.

### `to_html`

```text
to_html(view: 'SpanView', *, alternatives: 'bool' = False) -> 'str'
```

Return a self-contained, injection-safe HTML segmentation report.

### `to_json`

```text
to_json(view: 'SpanView', *, alternatives: 'bool' = False) -> 'str'
```

Return one stable, indented JSON span-view document.

### `to_jsonl`

```text
to_jsonl(views: 'SpanView | Iterable[SpanView]', *, record: 'str' = 'input', alternatives: 'bool' = False) -> 'str'
```

Return compact JSON Lines records grouped by input or flattened by span.

### `to_text`

```text
to_text(view: 'SpanView', *, alternatives: 'bool' = False) -> 'str'
```

Return a deterministic ruler and aligned plain-text span table.

## References

### `BoundaryRef`

```text
BoundaryRef(tier: 'QualifiedName', index: 'int') -> None
```

Address a boundary owned by a tier, including both outer boundaries.

#### `BoundaryRef.to_data`

Method.

```text
BoundaryRef.to_data(self) -> 'dict[str, JsonValue]'
```

Return the boundary reference as JSON-serializable data.

### `DocumentRef`

```text
DocumentRef() -> None
```

Identify the document itself as an attribute subject.

### `DurableBoundaryRef`

```text
DurableBoundaryRef(anchor: 'DurableItemRef | QualifiedName', side: 'BoundarySide') -> None
```

Address a boundary whose identity is its anchor and chosen side.

Boundary identity is anchor-relative: an interior boundary's identity is,
for example, "before item X", not an identity attached to an adjacency.
A boundary therefore follows its anchor when it moves; moving a block
carries its internal boundaries.  Under reordering identities follow their
anchors and no new adjacency inherits an identity.  Inserting exactly at
``before(x)`` leaves that boundary before ``x``.

Distinct anchors may resolve to the same boundary in the current graph and
diverge after an edit.  In particular, ``after(a)`` and ``before(b)`` keep
different intentions even when ``a`` and ``b`` are adjacent.  Likewise,
``before(tier)`` and ``after(tier)`` are distinct first-edge and last-edge
anchors that coincide only while the tier is empty.

Removing an anchor is refused rather than reinterpreted.  Removal destroys
the anchor, a boundary whose anchor is gone has no identity left to keep,
and the kernel will not choose a replacement anchor on a caller's behalf.
An edit that would remove such an item is therefore refused, immediately by
either a frozen graph's operation or the mutable editor's removal call, and
a caller who means to keep the boundary anchors it elsewhere first.

#### `DurableBoundaryRef.to_data`

Method.

```text
DurableBoundaryRef.to_data(self) -> 'dict[str, JsonValue]'
```

Return the tagged anchor and side as JSON-serializable data.

### `DurableItemRef`

```text
DurableItemRef(durable_id: 'str') -> None
```

Address an item by a durable identifier without a coordinate fallback.

#### `DurableItemRef.to_data`

Method.

```text
DurableItemRef.to_data(self) -> 'dict[str, JsonValue]'
```

Return the durable reference as JSON-serializable data.

### `DurablePolyadicRef`

```text
DurablePolyadicRef(durable_id: 'str') -> None
```

Identify one polyadic relation instance by durable identity.

### `DurableRelationRef`

```text
DurableRelationRef(durable_id: 'str') -> None
```

Identify one relation instance by durable identity.

### `ItemRef`

```text
ItemRef(tier: 'QualifiedName', index: 'int') -> None
```

Address an item by its current structural coordinate.

#### `ItemRef.to_data`

Method.

```text
ItemRef.to_data(self) -> 'dict[str, JsonValue]'
```

Return the reference as JSON-serializable data.

### `PolyadicInstanceRef`

```text
PolyadicInstanceRef(index: 'int') -> None
```

Identify one polyadic relation instance by structural index.

### `RelationDeclarationRef`

```text
RelationDeclarationRef(relation: 'QualifiedName') -> None
```

Identify one relation declaration as an attribute subject.

### `RelationInstanceRef`

```text
RelationInstanceRef(index: 'int') -> None
```

Identify one binary relation instance by structural index.

### `TierRef`

```text
TierRef(tier: 'QualifiedName') -> None
```

Identify one tier as an attribute subject.

## Rewrite

### `EffectRefusal`

Refuse an effect claim a rewrite does not make good on.

### `RewriteCertificate`

```text
RewriteCertificate(effect: 'RewriteEffect', subjects: 'int', disturbances: 'int') -> None
```

Report what discharged one rewrite's effect claim, and over how much.

``subjects`` is the honest part. It counts the structures the source
asserts, every one of which was examined. A ``DECORATE`` claim over a
source that asserts three things has been held to three things; the count
is there so a nearly vacuous claim cannot be read as a strong one.

``disturbances`` counts the ways the result failed to leave the source's
structures standing, which is zero exactly when the rewrite decorated. One
structure contributes one entry per way, so this is not a count of
structures and does not sit on the same scale as ``subjects``.

#### `RewriteCertificate.to_data`

Method.

```text
RewriteCertificate.to_data(self) -> 'dict[str, JsonValue]'
```

Return deterministic strict-JSON data.

All three fields are carried because no two of them recover the third.
``effect`` is what was discharged, and it is the only one that separates
a ``REVISE`` from a ``COLLAPSE``: both leave disturbances behind, so a
certificate reporting counts alone would read identically for either.
``subjects`` is how much the claim was held to, without which a nearly
vacuous discharge reads as a strong one. ``disturbances`` is what the
check found, which is zero exactly when the rewrite decorated.

``disturbances`` is written as the count this type holds, under the name
it holds it by, because a count of ways is what was measured: one
structure that lost two attributes contributes two. It is therefore not
a count of structures, it does not sit on the same scale as
``subjects``, and the two together are not a proportion of anything. A
reader wanting the structures themselves calls
``RewriteDeclaration.disturbances()``, whose entries serialize through
``RewriteDisturbance.to_data``; this certificate says how far the check
reached rather than what it saw.

### `RewriteDeclaration`

```text
RewriteDeclaration(name: 'str', source: 'Graph', result: 'Graph', effect: 'RewriteEffect' = <RewriteEffect.UNDECLARED: 'undeclared'>) -> None
```

Bind one named claim to the pair of graphs a rewrite read and wrote.

``effect`` states what the rewrite did to ``source``. It defaults to
``UNDECLARED`` and nothing consults it until ``check_effect()`` is called,
because the claim is owed where it is relied on rather than where a pair of
graphs is built.

This is a claim about two graph *values*. It does not know, and does not
ask, whether one was produced from the other: two graphs built
independently that happen to stand in this relation are measured exactly as
a rewrite and its input would be.

#### `RewriteDeclaration.disturbances`

Method.

```text
RewriteDeclaration.disturbances(self) -> 'tuple[RewriteDisturbance, ...]'
```

Return every way the rewrite disturbed a structure, in source order.

The order is the source graph's own reading order -- namespaces, then
each tier and its items, then relation declarations, attribute
declarations, relation instances, polyadic relation instances, boundary
values, each layer and the facts it holds, and the document. It is
total and reproducible, so the first disturbance is the first in a
fixed order rather than a minimized or a most-severe one, and the
refusals report it as such.

#### `RewriteDeclaration.check_effect`

Method.

```text
RewriteDeclaration.check_effect(self) -> 'RewriteCertificate'
```

Demand this rewrite's effect claim and discharge it, or refuse.

Every branch bites, and the asymmetry is deliberate. An ``UNDECLARED``
effect is refused with **the declaration to be made**; a false claim is
refused with **a semantic counterexample** naming the structure, the
tier it belongs to, and what happened to it. Declining to say is not
the same as saying the weaker thing.

What a discharged ``DECORATE`` licenses is one thing and not more:
every reading taken over the source is still a correct reading of the
result, without re-reading it. An item's attributes, a boundary's
values, a relation's endpoints, whatever a reference resolved to --
all of it still holds. What it does not license is any reading that
counts, quantifies over everything, or turns on absence: a tier's
extent, a root set's exhaustiveness, the canonical bytes, the
construction fingerprint. Decoration adds, so those must be taken
again. Put shortly, a positive property proved of the source transfers
to the result and a negative or counting one does not.

As this tree stands that license discharges a proof obligation and buys
no optimization: nothing here caches a reading across a rewrite, so
there is no revalidation for the claim to skip. It is stated as a
license rather than a speedup on purpose.

### `RewriteDisturbance`

```text
RewriteDisturbance(effect: 'RewriteEffect', subject: 'str', tier: 'QualifiedName | None', detail: 'str') -> None
```

Name one structure the rewrite did not leave standing as it found it.

``effect`` is ``REVISE`` when the structure still stands and a value in it
was replaced, and ``COLLAPSE`` when the structure or the value is gone.
``subject`` names it in the source's own coordinates, ``tier`` is the tier
it belongs to when it belongs to one, and ``detail`` says what happened.

#### `RewriteDisturbance.to_data`

Method.

```text
RewriteDisturbance.to_data(self) -> 'dict[str, JsonValue]'
```

Return the disturbance as JSON-serializable data.

### `RewriteEffect`

```text
RewriteEffect(*values)
```

State what a rewrite did to the graph it rewrote.

``DECORATE``
    The rewrite added to the source and took nothing back. Gate: no
    structure the source asserts may be missing from the result, and no
    value it carries may have been replaced.
``REVISE``
    Every structure the source asserts still stands, but some value stands
    in place of another. Gate: the replacement must be exhibitable -- a
    ``REVISE`` claim over a rewrite that replaced nothing is a declaration
    that is hiding, and it is refused.
``COLLAPSE``
    Some structure the source asserts is gone. Gate: the loss must be
    exhibitable, for the same reason.
``UNDECLARED``
    The default. It is refused, and it does not mean ``COLLAPSE``:
    declining to say is not the same as saying the weaker thing, and the
    refusal says so by handing back the declaration to be made.

Every branch bites, and the asymmetry is deliberate. Omitting the claim is
answered with the declaration; asserting it falsely is answered with a
semantic counterexample naming the structure and what happened to it.

#### `RewriteEffect` members

- `DECORATE` = `decorate`
- `REVISE` = `revise`
- `COLLAPSE` = `collapse`
- `UNDECLARED` = `undeclared`

## Selection

### `AttributeSelector`

```text
AttributeSelector(attribute: 'QualifiedName', domain: 'AttributeDomain') -> None
```

Select nodes carrying one attribute on its declared domain.

The kernel admits ``relation_instance`` values on bipartite and polyadic
instances alike, so this selector reads both collections and reports each
carrier under its own node kind.  Reading only one would answer a question
about the whole domain from part of it.

#### `AttributeSelector.evaluate`

Method.

```text
AttributeSelector.evaluate(self, graph: 'Graph', *, path_profile: 'PathProfile') -> 'NodeSet'
```

Validate and return owners carrying the named value.

### `BoundariesSelector`

```text
BoundariesSelector(tier: 'QualifiedName') -> None
```

Select every boundary owned by one declared tier.

#### `BoundariesSelector.evaluate`

Method.

```text
BoundariesSelector.evaluate(self, graph: 'Graph', *, path_profile: 'PathProfile') -> 'NodeSet'
```

Validate and return outer and inter-item boundaries.

### `BoundaryPathSelector`

```text
BoundaryPathSelector(path: 'str') -> None
```

Select the boundary resolved by one path.

#### `BoundaryPathSelector.evaluate`

Method.

```text
BoundaryPathSelector.evaluate(self, graph: 'Graph', *, path_profile: 'PathProfile') -> 'NodeSet'
```

Resolve the path and require a boundary result.

### `BoundarySelector`

```text
BoundarySelector(reference: 'BoundaryRef | DurableBoundaryRef') -> None
```

Select one structural or anchored durable boundary reference.

#### `BoundarySelector.evaluate`

Method.

```text
BoundarySelector.evaluate(self, graph: 'Graph', *, path_profile: 'PathProfile') -> 'NodeSet'
```

Resolve and return the boundary identity.

### `DifferenceSelector`

```text
DifferenceSelector(left: 'Selector', right: 'Selector') -> None
```

Remove the right selection from the left selection.

#### `DifferenceSelector.evaluate`

Method.

```text
DifferenceSelector.evaluate(self, graph: 'Graph', *, path_profile: 'PathProfile') -> 'NodeSet'
```

Evaluate both operands and remove right from left.

### `IntersectionSelector`

```text
IntersectionSelector(args: 'tuple[Selector, ...]') -> None
```

Intersect one or more selectors.

#### `IntersectionSelector.evaluate`

Method.

```text
IntersectionSelector.evaluate(self, graph: 'Graph', *, path_profile: 'PathProfile') -> 'NodeSet'
```

Evaluate and intersect the operands from left to right.

### `ItemPathSelector`

```text
ItemPathSelector(path: 'str') -> None
```

Select the item resolved by one path.

#### `ItemPathSelector.evaluate`

Method.

```text
ItemPathSelector.evaluate(self, graph: 'Graph', *, path_profile: 'PathProfile') -> 'NodeSet'
```

Resolve the path and require an item result.

### `ItemSelector`

```text
ItemSelector(reference: 'ItemRef | DurableItemRef') -> None
```

Select one structural or durable item reference.

#### `ItemSelector.evaluate`

Method.

```text
ItemSelector.evaluate(self, graph: 'Graph', *, path_profile: 'PathProfile') -> 'NodeSet'
```

Resolve and return the item identity.

### `ItemsSelector`

```text
ItemsSelector(tier: 'QualifiedName') -> None
```

Select all items owned by one declared tier.

#### `ItemsSelector.evaluate`

Method.

```text
ItemsSelector.evaluate(self, graph: 'Graph', *, path_profile: 'PathProfile') -> 'NodeSet'
```

Validate and return the tier's items in coordinate order.

### `Node`

```text
Node(kind: 'NodeKind', reference: 'QualifiedName | ItemRef | BoundaryRef | int | None') -> None
```

Identify a node by its kind and its graph-local coordinate.

Item and boundary coordinates include their tier, declaration nodes use their
qualified name, and relation instances use their graph-local index.  The kind
is part of identity, so coordinates from unlike node classes never alias.

Bipartite and polyadic instances live in separate graph collections, so they
index separate spaces and index 0 names a different fact in each.  They are
two node kinds over their own indices rather than one kind over a merged
index, so a selection can neither confuse them nor answer for only one.

#### `Node.to_data`

Method.

```text
Node.to_data(self) -> 'dict[str, JsonValue]'
```

Return a tagged strict-JSON representation of this identity.

### `NodeKind`

```text
NodeKind(*values)
```

Distinguish identities belonging to different graph node classes.

#### `NodeKind` members

- `DOCUMENT` = `document`
- `TIER` = `tier`
- `ITEM` = `item`
- `BOUNDARY` = `boundary`
- `RELATION_DECLARATION` = `relation_declaration`
- `RELATION_INSTANCE` = `relation_instance`
- `POLYADIC_RELATION_INSTANCE` = `polyadic_relation_instance`

### `NodeSet`

```text
NodeSet(graph: 'Graph', nodes: 'tuple[Node, ...]') -> None
```

Hold unique nodes in the graph's canonical mixed-node order.

Nodes sort first by kind rank. Within tier-addressed kinds they sort by tier
declaration index, then item or boundary index, so reproducible selection
output depends on the graph's tier declaration order.

A polyadic instance sorts by its declaration, then its two side arities,
then its endpoints read in stored order.  Side order is part of the key, so
two instances over the same endpoints in different orders remain distinct.

#### `NodeSet.to_data`

Method.

```text
NodeSet.to_data(self) -> 'list[JsonValue]'
```

Return the ordered set as strict-JSON data.

### `Selector`

```text
type Selector = tiergraph.selection.TierSelector | tiergraph.selection.TypeSelector | tiergraph.selection.ItemsSelector | tiergraph.selection.BoundariesSelector | tiergraph.selection.ItemSelector | tiergraph.selection.BoundarySelector | tiergraph.selection.ItemPathSelector | tiergraph.selection.BoundaryPathSelector | tiergraph.selection.AttributeSelector | tiergraph.selection.WhereSelector | tiergraph.selection.SequenceSelector | tiergraph.selection.UnionSelector | tiergraph.selection.IntersectionSelector | tiergraph.selection.DifferenceSelector
```

### `SequenceSelector`

```text
SequenceSelector(ordering: 'Ordering', pattern: 'Pattern') -> None
```

Select the focus of one regular pattern over a declared ordering.

#### `SequenceSelector.evaluate`

Method.

```text
SequenceSelector.evaluate(self, graph: 'Graph', *, path_profile: 'PathProfile') -> 'NodeSet'
```

Compile the pattern and return its focus over the ordering scopes.

#### `SequenceSelector.to_data`

Method.

```text
SequenceSelector.to_data(self) -> 'dict[str, JsonValue]'
```

Return the strict selector JSON form.

### `TierSelector`

```text
TierSelector(tier: 'QualifiedName') -> None
```

Select one declared tier node.

#### `TierSelector.evaluate`

Method.

```text
TierSelector.evaluate(self, graph: 'Graph', *, path_profile: 'PathProfile') -> 'NodeSet'
```

Validate and return the selected tier.

### `TypeSelector`

```text
TypeSelector(item_type: 'QualifiedName') -> None
```

Select every item assigned one declared type by simple membership.

#### `TypeSelector.evaluate`

Method.

```text
TypeSelector.evaluate(self, graph: 'Graph', *, path_profile: 'PathProfile') -> 'NodeSet'
```

Validate and return all items of the declared type.

### `UnionSelector`

```text
UnionSelector(args: 'tuple[Selector, ...]') -> None
```

Union one or more selectors.

#### `UnionSelector.evaluate`

Method.

```text
UnionSelector.evaluate(self, graph: 'Graph', *, path_profile: 'PathProfile') -> 'NodeSet'
```

Evaluate and union the operands from left to right.

### `WhereSelector`

```text
WhereSelector(base: 'Selector', predicate: 'Predicate') -> None
```

Retain base-selection nodes on which one bound predicate holds.

#### `WhereSelector.evaluate`

Method.

```text
WhereSelector.evaluate(self, graph: 'Graph', *, path_profile: 'PathProfile') -> 'NodeSet'
```

Evaluate the base first, then mask only that finite candidate domain.

### `evaluate_selection`

```text
evaluate_selection(graph: 'Graph', selector: 'Selector', *, path_profile: 'PathProfile' = StructuralPathProfile(), budget: 'WorkBudget | WorkMeter | None' = None) -> 'NodeSet'
```

Evaluate a graph-free selector into one canonical node set.

An optional declared work budget limits evaluation.

### `selection_loads`

```text
selection_loads(source: 'str | bytes') -> 'Selector'
```

Decode one strict declarative selector from JSON.

The text is read under the same envelope, encoding, and syntax stages the
graph document reader applies, so a caller routes on a declared stage here
as well as there.

## Semirings

### `ARCTIC`

The inexact IEEE-double max-plus semiring.

### `BOOLEAN`

The exact Boolean semiring, with disjunction and conjunction.

### `COUNTING`

The exact natural-number semiring.

### `DECIMAL_ARCTIC`

An exact min-plus or max-plus semiring with XSD-decimal finite values.

### `DECIMAL_TROPICAL`

An exact min-plus or max-plus semiring with XSD-decimal finite values.

### `LOG_PROBABILITY`

The inexact log-sum-exp semiring over finite IEEE-double log weights.

Values are log weights: finite doubles, or ``-inf`` as the zero. Addition is
the numerically stable log-sum-exp, multiplication is ordinary addition of
logs, and ``0.0`` is the one. Positive values are admitted because a weight
need not be a normalized probability. A path of ``-1000`` log weights folds
without the underflow that raw exponentials suffer, which is the reason to
fold in this carrier rather than in probabilities.

Every required law is checked approximately except addition commutativity,
which the symmetric log-sum-exp keeps exactly. That is the honest claim for
floating-point accumulation, and an acyclic dependency graph does not
change it: a finite derivation set makes the *search* exhaustive and says
nothing about the arithmetic. ``ExpectationSemiring`` refuses this base
for the same reason, and that refusal stands. A result that leaves the
finite carrier is refused rather than read as mass created or destroyed.
The carrier declares no star.

``normalize`` is the readout above the algebra: it reads log weights out
as probabilities of a total, and a construct applying it says so where it
reports the result. ``readouts`` names it, so a caller can declare it and
nothing else is mistaken for one.

### `PATH`

The exact decimal tropical semiring enriched with tied best paths.

### `TROPICAL`

The inexact IEEE-double min-plus semiring.

### `StarRefusal`

Refuse a closure the declaring algebra does not license for this operand.

### `StarSelector`

```text
type StarSelector[T] = tiergraph.semiring.ZeroClosedStar[T]
```

### `ZeroClosedStar`

```text
ZeroClosedStar(algebra: 'Semiring[T]', name: 'str' = 'zero-closed') -> None
```

Admit 0-closed operands and close their finite ascending chain to one.

#### `ZeroClosedStar.admits`

Method.

```text
ZeroClosedStar.admits(self, operand: 'T', /) -> 'bool'
```

Prove that the operand is dominated by the multiplicative identity.

#### `ZeroClosedStar.close`

Method.

```text
ZeroClosedStar.close(self, operand: 'T', /) -> 'T'
```

Return the closure after checking the warrant.

## Serialization

### `dump_bytes`

```text
dump_bytes(graph: 'Graph') -> 'bytes'
```

Encode the canonical document as UTF-8 bytes.

A graph carrying a string UTF-8 cannot encode is refused, not written.

### `dump_compact`

```text
dump_compact(graph: 'Graph') -> 'str'
```

Return compact canonical JSON, including its final newline.

A graph carrying a string UTF-8 cannot encode is refused, not written.

### `dumps`

```text
dumps(graph: 'Graph') -> 'str'
```

Return the sole canonical JSON spelling, including its final newline.

A graph carrying a string UTF-8 cannot encode is refused, not written.

### `load_patch`

```text
load_patch(stream: 'BinaryIO') -> 'Patch'
```

Read a patch incrementally under the machine codec's shared limits.

### `load_program`

```text
load_program(stream: 'BinaryIO') -> 'Program'
```

Read a versioned JSONL machine program incrementally from a binary stream.

### `loads`

```text
loads(document: 'str | bytes') -> 'Graph'
```

Parse the current format without implicitly migrating older documents.

Migration is refused because choosing a loss-aware conversion belongs in an
explicit version-to-version tool, not in the primitive codec.

### `patch_dumps`

```text
patch_dumps(patch: 'Patch') -> 'str'
```

Return canonical JSONL with one operation per line and a final newline.

### `patch_loads`

```text
patch_loads(source: 'str | bytes') -> 'Patch'
```

Parse a patch from bounded strict JSONL text or bytes.

### `program_dumps`

```text
program_dumps(program: 'Program') -> 'str'
```

Return canonical JSONL for a machine program, including a final newline.

A record carrying text the UTF-8 encoder refuses is refused here, named by
its path inside that record and by the line it would have stood on, because
what this used to return for such a program was not a program: written with
`ensure_ascii=False`, the character stood in the text itself, so the `str`
had no UTF-8 encoding at all and `load_program` refused it at `ENCODING` on
the way back.  `wire.to_data` has answered that condition for the graph
writers through the same check, imported rather than restated; this writer
answered nothing, and the asymmetry was reachable from any `Program` built
in memory rather than read.  What is refused is what the reader already
refuses, so no program that round-trips today stops doing so.

### `program_loads`

```text
program_loads(source: 'str | bytes') -> 'Program'
```

Parse a versioned JSONL machine program under the public wire limits.

### `to_data`

```text
to_data(graph: 'Graph') -> 'dict[str, JsonValue]'
```

Return the versioned primitive document as strict JSON data.

A string the UTF-8 encoder refuses is refused here, named by its field path,
so no writer built on this function emits text `loads` would refuse for its
encoding.  That is the one condition this function answers, and it is not a
round trip over the whole refusal order: the reader ranks conditions this
writer never asks, so `dumps` returning is not on its own a promise that
`loads` accepts what it wrote.

## Inspection

### `graph_summary`

```text
graph_summary(graph: 'Graph') -> 'dict[str, object]'
```

Return stable document counts and per-declaration graph summaries.

Qualified names carry their declared expanded spelling, the same
``{"namespace", "local_name"}`` data every declaration's ``to_data`` emits,
so the whole summary is JSON-serializable. The wire's compact
``prefix:local`` spelling is deliberately not used: it depends on the
document's prefix bindings, which are a wire choice rather than graph
content, and a summary of graph content should not vary with them.

## Textgrid

### `TextGridReadResult`

```text
TextGridReadResult(graph: ForwardRef('Graph'), profile: ForwardRef('SpanViewProfile'))
```

Return the decoded graph beside the profile that selects its TextGrid tiers.

#### `TextGridReadResult.clock`

Property.

```text
TextGridReadResult.clock(self) -> 'ClockProfile'
```

Construct the physical clock declared by the decoded graph.

### `from_textgrid`

```text
from_textgrid(document: 'str | bytes', *, unit: 'str' = 's', containment_rule: 'str' = 'enclosure') -> 'TextGridReadResult'
```

Decode a TextGrid using enclosure or endpoint-coincidence containment.

### `to_textgrid`

```text
to_textgrid(graph: 'Graph', profile: 'SpanViewProfile', *, clock: 'ClockProfile | None' = None, scale: 'int | None' = None) -> 'str'
```

Render declared span and point tiers as a long-form TextGrid document.

## Traversal

### `NodeSequence`

```text
NodeSequence(graph: 'Graph', nodes: 'tuple[Node, ...]') -> None
```

Hold graph nodes without sorting or deduplicating them.

Unlike :class:`NodeSet`, this value carries semantic sequence order and may
contain the same node more than once. It deliberately provides no set
algebra: callers must explicitly construct a ``NodeSet`` for set-valued
reachability.

#### `NodeSequence.to_data`

Method.

```text
NodeSequence.to_data(self) -> 'list[JsonValue]'
```

Return nodes as strict-JSON data in their carried order.

### `OrderedContainment`

```text
OrderedContainment(graph: 'Graph', relation: 'QualifiedName') -> None
```

Traverse one ordered, item-only polyadic containment relation.

Descending order is exactly stored target incidence order. Descendants are
depth-first pre-order and leaves are depth-first leaf order; repeated
incidence remains repeated. Parents and ancestors are computed inverse
fibers, so their result is intentionally a :class:`NodeSet`.

#### `OrderedContainment.direct_children`

Method.

```text
OrderedContainment.direct_children(self, parent: 'ItemRef') -> 'NodeSequence'
```

Return direct children in declared target incidence order.

#### `OrderedContainment.descendants`

Method.

```text
OrderedContainment.descendants(self, parent: 'ItemRef') -> 'NodeSequence'
```

Return descendants in depth-first pre-order, preserving repetition.

#### `OrderedContainment.leaves`

Method.

```text
OrderedContainment.leaves(self, parent: 'ItemRef') -> 'NodeSequence'
```

Return descendant leaves, or the source itself when it has no children.

#### `OrderedContainment.parents`

Method.

```text
OrderedContainment.parents(self, child: 'ItemRef') -> 'NodeSet'
```

Return the canonical set-valued inverse fiber over one child.

#### `OrderedContainment.ancestors`

Method.

```text
OrderedContainment.ancestors(self, child: 'ItemRef') -> 'NodeSet'
```

Return the transitive inverse fiber as a canonical reachable set.

### `OrderedPolyadicTraversal`

```text
OrderedPolyadicTraversal(graph: 'Graph', relation: 'QualifiedName', source_side: 'PolyadicSide', target_side: 'PolyadicSide') -> None
```

Traverse between either pair of sides of one ordered polyadic relation.

Direct and transitive results retain instance order, opposite-side endpoint
order, and repetition.  Relational inversion is set-valued; callers that
need stored order can instead request the opposite sequence of one instance.

#### `OrderedPolyadicTraversal.direct`

Method.

```text
OrderedPolyadicTraversal.direct(self, origin: 'TraversalEndpointRef') -> 'NodeSequence'
```

Return one ordered step from ``origin``, retaining all incidence.

#### `OrderedPolyadicTraversal.transitive`

Method.

```text
OrderedPolyadicTraversal.transitive(self, origin: 'TraversalEndpointRef') -> 'NodeSequence'
```

Return depth-first pre-order reachability in stored incidence order.

#### `OrderedPolyadicTraversal.inverse`

Method.

```text
OrderedPolyadicTraversal.inverse(self, endpoint: 'TraversalEndpointRef') -> 'NodeSet'
```

Return the deduplicated computed fiber over the target endpoint.

#### `OrderedPolyadicTraversal.instances`

Method.

```text
OrderedPolyadicTraversal.instances(self) -> 'tuple[PolyadicIncidence, ...]'
```

Return every validated instance of this relation in stored order.

Origin-keyed steps answer "what does this endpoint correspond to"; a
correspondence read as a whole, one ordered side against another with
no positional pairing between them, has no origin to key on, so it is
reachable only by enumeration.  Both sides keep their stored order.

#### `OrderedPolyadicTraversal.stored_opposite`

Method.

```text
OrderedPolyadicTraversal.stored_opposite(self, instance_index: 'int') -> 'NodeSequence'
```

Return one instance's stored target-side sequence without inversion.

### `PolyadicIncidence`

```text
PolyadicIncidence(index: 'int', sources: 'NodeSequence', targets: 'NodeSequence') -> None
```

Hold one instance's graph-local index and both sides in stored order.

Sides are named for the declaration, not for a traversal direction, so
``sources`` and ``targets`` mean the same thing whichever way a caller
walks.  Each side is a :class:`NodeSequence` because its order is graph
content: a correspondence that reorders its two sides is a different fact
from one that does not, and a pair-per-endpoint reading would lose that.

#### `PolyadicIncidence.to_data`

Method.

```text
PolyadicIncidence.to_data(self) -> 'dict[str, JsonValue]'
```

Return the index and both ordered sides as strict-JSON data.

### `PolyadicSide`

```text
PolyadicSide(*values)
```

Choose one stored side of a polyadic relation declaration.

#### `PolyadicSide` members

- `SOURCES` = `sources`
- `TARGETS` = `targets`

### `Walk`

```text
Walk(source: 'NodeSet', relation: 'QualifiedName', direction: 'WalkDirection', cap: 'int | None' = None) -> None
```

Declare a transitive walk along one bipartite or polyadic relation.

A bounded walk stops after ``cap`` relation steps.  An unbounded walk is
admitted only when graph construction has validated the declaration's
acyclicity promise.  Forward access reads the stored relation and inverse
access computes its fiber over each selected item.  That fiber is a set:
deduplication is a consequence of relational inversion, not an accommodation
for any particular domain whose morphs happen to cross-cut.

**What one polyadic step is.** A bipartite incidence names one endpoint on
each side, so a step from one of them is the other. A polyadic incidence
names an ordered sequence on each side, and the two sides declare their
arities independently, so a step has to say how much of the far side it
reaches. It reaches all of it: a step from any endpoint of the near side
reaches every endpoint of the far side of that incidence. A ``k``-source,
``m``-target incidence therefore contributes exactly the ``k * m`` edges the
graph itself ranged over when it validated the declaration's ``acyclic``
promise, which is what lets the unbounded branch keep resting on that
promise here: the promise is about the edges this walk follows, not about
some smaller relation it is merely phrased near. It is also the step
:class:`OrderedPolyadicTraversal` already takes, so a walk is the
set-valued image of that traversal rather than a second reading of one
graph's bytes.

Pairing the two sides off index by index was the alternative, and it is not
available: each side declares its own arity bounds and its own emptiness, so
``k`` and ``m`` are unrelated and a positional reading is undefined wherever
they differ. It would also walk a strictly smaller relation than the one the
acyclicity promise was validated over -- still terminating, since a subgraph
of an acyclic graph is acyclic, but reaching less than the graph's own
reading of its own incidence.

``WalkDirection`` keeps its meaning across both shapes. ``FORWARD`` reads
the declared descending direction, which for a polyadic declaration is its
``sources`` side to its ``targets`` side, the direction its
``single_parent`` and ``targets_subset_of`` promises are phrased over.
``INVERSE`` computes the fiber, targets back to sources. Both directions
deduplicate, because a reachable set is a set; that is not new in the
polyadic case, only more visible, since one wide incidence can offer the
same node along many of its edges. Where stored order and repetition are the
question, :class:`OrderedPolyadicTraversal` answers with a
:class:`NodeSequence`, and this class deliberately does not.

#### `Walk.evaluate`

Method.

```text
Walk.evaluate(self) -> 'WalkResult'
```

Return the transitive reachable set, excluding the source selection.

### `WalkDirection`

```text
WalkDirection(*values)
```

Choose the declared descending direction or its computed inverse view.

#### `WalkDirection` members

- `FORWARD` = `forward`
- `INVERSE` = `inverse`

### `WalkResult`

```text
WalkResult(nodes: 'NodeSet', truncated: 'bool', cap: 'int | None') -> None
```

Return reached nodes and report whether the step cap dropped a result.

``truncated`` is true only when one lookahead step finds a node that is not
already in the deduplicated result or source selection. Reaching the cap is
not itself truncation. A false report therefore guarantees that ``nodes``
is the whole reachable set less the source selection, which
:meth:`Walk.evaluate` excludes from what it returns.

#### `WalkResult.to_data`

Method.

```text
WalkResult.to_data(self) -> 'dict[str, JsonValue]'
```

Return strict-JSON traversal data in canonical node order.

## Supported secondary surface

### `tiergraph.blob`

This module is importable and usable, but carries no API-stability promise at version 0.8.0.

### `BLOB_NAMESPACE`

Namespace for the fixed external-resource vocabulary. Current value: `urn:tiergraph:blob`.

### `BlobProfile`

```text
BlobProfile(graph: 'Graph') -> None
```

Read and validate external-resource descriptors and attachments.

A blob is any ordinary item carrying ``blob:sha256``. Every blob also has
a durable item identifier, byte size, lowercase media type, and absolute
schema URI. The payload bytes stay outside the graph. Media-specific
metadata remains ordinary typed attributes in the media vocabulary, so
audio, video, nested graph, transcript, key-value, and other resources all
use the same profile without core interpreting their types.

Attachments are ordinary item-to-item bipartite relation instances whose
right endpoint is a blob item. They retain the graph's declared relation
order and must have durable relation identifiers. This admits any number
of roles and attachments, including several metadata resources on one
binary resource and chains from a structural item through multiple blobs.

Author-declared metadata lives on the base item. Inspected type-specific
metadata can be recorded as ordinary layer facts in the media vocabulary,
whose layer source identifies the inspector. This profile never imports or
runs an inspector and never opens payload bytes.

#### `BlobProfile.blobs`

Method.

```text
BlobProfile.blobs(self) -> 'tuple[tuple[ItemRef, BlobRef], ...]'
```

Return blob items in tier and item order without sorting by digest.

#### `BlobProfile.attachments`

Method.

```text
BlobProfile.attachments(self, blob: 'ItemRef | DurableItemRef') -> 'tuple[tuple[RelationInstanceRef, ItemRef, BlobSpan | None], ...]'
```

Return one blob item's attachments in declared relation order.

#### `BlobProfile.required`

Method.

```text
BlobProfile.required(self) -> 'tuple[BlobRef, ...]'
```

Return distinct payload requirements in first blob-item order.

### `BlobRef`

```text
BlobRef(sha256: 'str', size: 'int') -> None
```

Identify payload bytes by their lowercase SHA-256 digest and exact size.

### `BlobSpan`

```text
BlobSpan(offset: 'int | None', length: 'int') -> None
```

Describe a linear extent in the attached resource's declared unit.

An absent ``offset`` makes the span duration-only. The resource's
``blob:unit`` supplies the meaning of both integers, so the value does not
assume time: a unit can name characters, samples, frames, or another
resource-defined linear coordinate. Structured paths remain ordinary
relation-instance attributes in the resource schema rather than being
forced into this linear value.

### `declare_blob_vocabulary`

```text
declare_blob_vocabulary(editor: 'GraphEditor') -> 'GraphEditor'
```

Declare the fixed blob prefix and attributes on a mutable graph editor.

The helper appends declarations and returns the same editor for chaining.
Existing declarations are not silently adopted: the editor's ordinary
duplicate-declaration refusals keep one explicit declaration event.

### `tiergraph.build`

This module is importable and usable, but carries no API-stability promise at version 0.8.0.

Builder notation errors raise the directly importable `tiergraph.build.BuilderError`, a `ValueError` subclass. It is not part of the module's star-exported surface.

### `Document`

```text
Document(namespace: 'str', *, prefix: 'str') -> 'None'
```

Accumulate convenient notation and repeatedly build fresh immutable graphs.

#### `Document.namespace`

Method.

```text
Document.namespace(self, namespace: 'str', *, prefix: 'str') -> 'None'
```

Register an additional namespace binding.

#### `Document.qname`

Method.

```text
Document.qname(self, local: 'str', *, namespace: 'str | None' = None) -> 'QualifiedName'
```

Expand a local spelling in the default or explicitly selected namespace.

#### `Document.attribute`

Method.

```text
Document.attribute(self, name: 'Name', value_type: 'AttributeType | str', *, domain: 'AttributeDomain | str' = <AttributeDomain.ITEM: 'item'>) -> 'None'
```

Declare an attribute without inferring its type from Python values.

#### `Document.attributes`

Method.

```text
Document.attributes(self, declarations: 'Mapping[Name, AttributeDeclarationInput]', *, domain: 'AttributeDomain | str' = <AttributeDomain.ITEM: 'item'>) -> 'None'
```

Declare mapped attributes, with an optional domain on each entry.

#### `Document.tier`

Method.

```text
Document.tier(self, name: 'Name', items: 'Iterable[Item | ItemSpec | str | None]' = (), *, item_type: 'Name | None' = None, membership: 'Name | None' = None, long_name: 'str | None' = None, attributes: 'AttributeInput' = None) -> 'TierHandle'
```

Add an ordered tier, optionally with one explicit membership declaration.

#### `Document.link`

Method.

```text
Document.link(self, name: 'Name', source: 'TierHandle | Name', target: 'TierHandle | Name', pairs: 'Iterable[tuple[object, object]]' = (), *, source_type: 'Name | None' = None, target_type: 'Name | None' = None, left_endpoint: 'RelationEndpointKind | str' = <RelationEndpointKind.ITEM: 'item'>, right_endpoint: 'RelationEndpointKind | str' = <RelationEndpointKind.ITEM: 'item'>, single_parent: 'bool' = False, acyclic: 'bool' = False, attributes: 'AttributeInput' = None) -> 'LinkHandle'
```

Declare a bipartite relation and add its ordered endpoint pairs.

#### `Document.relation`

Method.

```text
Document.relation(self, name: 'Name', source: 'TierHandle | Name', target: 'TierHandle | Name', pairs: 'Iterable[tuple[object, object]]', *, left_endpoint: 'RelationEndpointKind | str', right_endpoint: 'RelationEndpointKind | str', source_type: 'Name | None' = None, target_type: 'Name | None' = None, single_parent: 'bool' = False, acyclic: 'bool' = False, attributes: 'AttributeInput' = None) -> 'LinkHandle'
```

Declare a relation whose ordered pairs may contain boundary anchors.

#### `Document.declare`

Method.

```text
Document.declare(self, declaration: 'RelationDeclaration') -> 'None'
```

Add an already-constructed kernel relation declaration as-is.

#### `Document.declared_order`

Method.

```text
Document.declared_order(self, name: 'Name', members: 'Selector', sequence: 'Iterable[RelationEndpointRef]' = (), *, open_left: 'bool' = False) -> 'DeclaredOrder'
```

Declare and populate one mixed-kind successor chain.

#### `Document.append_declared`

Method.

```text
Document.append_declared(self, order: 'DeclaredOrder', members: 'Selector', chunk: 'Iterable[RelationEndpointRef]') -> 'DeclaredOrder'
```

Append one chunk and return the order with its complete member selector.

#### `Document.relate`

Method.

```text
Document.relate(self, instance: 'RelationInstance | PolyadicRelationInstance') -> 'None'
```

Add an already-constructed kernel relation instance as-is.

#### `Document.add`

Method.

```text
Document.add(self, value: 'RelationInstance | PolyadicRelationInstance | Boundary') -> 'None'
```

Add an already-constructed relation instance or sparse boundary value.

#### `Document.attach`

Method.

```text
Document.attach(self, domain: 'AttributeDomain | str', target: 'AttributeTarget', values: 'Mapping[Name, object]') -> 'None'
```

Attach declared values using the kernel's attribute-domain target forms.

#### `Document.build`

Method.

```text
Document.build(self) -> 'Graph'
```

Return a fresh immutable graph without consuming this builder.

### `document`

```text
document(namespace: 'str', *, prefix: 'str') -> 'Document'
```

Create a mutable document builder with its required default namespace.

### `item`

```text
item(durable_id: 'str | None' = None, /, *, attrs: 'Mapping[Name, object] | None' = None, **attributes: 'object') -> 'ItemSpec'
```

Describe an item with values to lower through declared attribute types.

### `tiergraph.semiring`

This module is a supported secondary API.

### `ARCTIC`

The inexact IEEE-double max-plus semiring.

### `BOOLEAN`

The exact Boolean semiring, with disjunction and conjunction.

### `COUNTING`

The exact natural-number semiring.

### `DECIMAL_ARCTIC`

An exact min-plus or max-plus semiring with XSD-decimal finite values.

### `DECIMAL_TROPICAL`

An exact min-plus or max-plus semiring with XSD-decimal finite values.

### `LOG_PROBABILITY`

The inexact log-sum-exp semiring over finite IEEE-double log weights.

Values are log weights: finite doubles, or ``-inf`` as the zero. Addition is
the numerically stable log-sum-exp, multiplication is ordinary addition of
logs, and ``0.0`` is the one. Positive values are admitted because a weight
need not be a normalized probability. A path of ``-1000`` log weights folds
without the underflow that raw exponentials suffer, which is the reason to
fold in this carrier rather than in probabilities.

Every required law is checked approximately except addition commutativity,
which the symmetric log-sum-exp keeps exactly. That is the honest claim for
floating-point accumulation, and an acyclic dependency graph does not
change it: a finite derivation set makes the *search* exhaustive and says
nothing about the arithmetic. ``ExpectationSemiring`` refuses this base
for the same reason, and that refusal stands. A result that leaves the
finite carrier is refused rather than read as mass created or destroyed.
The carrier declares no star.

``normalize`` is the readout above the algebra: it reads log weights out
as probabilities of a total, and a construct applying it says so where it
reports the result. ``readouts`` names it, so a caller can declare it and
nothing else is mistaken for one.

### `PATH`

The exact decimal tropical semiring enriched with tied best paths.

### `PATH_WITNESSES`

The exact semiring of finite path sets under union and concatenation.

### `TROPICAL`

The inexact IEEE-double min-plus semiring.

### `ArcticSemiring`

```text
ArcticSemiring() -> 'None'
```

The inexact IEEE-double max-plus semiring.

#### `ArcticSemiring.star`

Property.

```text
ArcticSemiring.star(self) -> 'StarSelector[float]'
```

Return this carrier's explicitly declared 0-closed closure.

### `BooleanSemiring`

```text
BooleanSemiring()
```

The exact Boolean semiring, with disjunction and conjunction.

#### `BooleanSemiring.star`

Property.

```text
BooleanSemiring.star(self) -> 'StarSelector[bool]'
```

Return the Boolean carrier's 0-closed closure.

#### `BooleanSemiring.add`

Method.

```text
BooleanSemiring.add(self, left: 'bool', right: 'bool', /) -> 'bool'
```

Return the disjunction of two values.

#### `BooleanSemiring.multiply`

Method.

```text
BooleanSemiring.multiply(self, left: 'bool', right: 'bool', /) -> 'bool'
```

Return the conjunction of two values.

#### `BooleanSemiring.encode`

Method.

```text
BooleanSemiring.encode(self, value: 'bool', /) -> 'object'
```

Encode a Boolean as a JSON Boolean.

#### `BooleanSemiring.decode`

Method.

```text
BooleanSemiring.decode(self, value: 'object', /) -> 'bool'
```

Decode a JSON Boolean.

### `CountingSemiring`

```text
CountingSemiring()
```

The exact natural-number semiring.

#### `CountingSemiring.add`

Method.

```text
CountingSemiring.add(self, left: 'int', right: 'int', /) -> 'int'
```

Return the sum of two counts.

#### `CountingSemiring.multiply`

Method.

```text
CountingSemiring.multiply(self, left: 'int', right: 'int', /) -> 'int'
```

Return the product of independent counts.

#### `CountingSemiring.encode`

Method.

```text
CountingSemiring.encode(self, value: 'int', /) -> 'object'
```

Encode a count as a JSON integer.

#### `CountingSemiring.decode`

Method.

```text
CountingSemiring.decode(self, value: 'object', /) -> 'int'
```

Decode a JSON natural number.

### `DecimalExtremumSemiring`

```text
DecimalExtremumSemiring(*, minimum: 'bool') -> 'None'
```

An exact min-plus or max-plus semiring with XSD-decimal finite values.

#### `DecimalExtremumSemiring.star`

Property.

```text
DecimalExtremumSemiring.star(self) -> 'StarSelector[Decimal]'
```

Return this extremum carrier's 0-closed closure.

#### `DecimalExtremumSemiring.add`

Method.

```text
DecimalExtremumSemiring.add(self, left: 'Decimal', right: 'Decimal', /) -> 'Decimal'
```

Return the preferred extremum.

#### `DecimalExtremumSemiring.multiply`

Method.

```text
DecimalExtremumSemiring.multiply(self, left: 'Decimal', right: 'Decimal', /) -> 'Decimal'
```

Return the exact sum, preserving the annihilator.

#### `DecimalExtremumSemiring.encode`

Method.

```text
DecimalExtremumSemiring.encode(self, value: 'Decimal', /) -> 'object'
```

Encode a value with XSD-style infinity and exact decimal text.

#### `DecimalExtremumSemiring.decode`

Method.

```text
DecimalExtremumSemiring.decode(self, value: 'object', /) -> 'Decimal'
```

Decode exact decimal text.

### `DoubleExtremumSemiring`

```text
DoubleExtremumSemiring(*, minimum: 'bool') -> 'None'
```

An inexact min-plus or max-plus semiring over finite IEEE doubles.

#### `DoubleExtremumSemiring.star`

Property.

```text
DoubleExtremumSemiring.star(self) -> 'StarSelector[float]'
```

Return this extremum carrier's 0-closed closure.

#### `DoubleExtremumSemiring.add`

Method.

```text
DoubleExtremumSemiring.add(self, left: 'float', right: 'float', /) -> 'float'
```

Return the preferred extremum.

#### `DoubleExtremumSemiring.multiply`

Method.

```text
DoubleExtremumSemiring.multiply(self, left: 'float', right: 'float', /) -> 'float'
```

Add finite doubles, refusing overflow and preserving the annihilator.

#### `DoubleExtremumSemiring.encode`

Method.

```text
DoubleExtremumSemiring.encode(self, value: 'float', /) -> 'object'
```

Encode a double losslessly without non-JSON numeric tokens.

#### `DoubleExtremumSemiring.decode`

Method.

```text
DoubleExtremumSemiring.decode(self, value: 'object', /) -> 'float'
```

Decode lossless hexadecimal double text.

### `ExpectationSemiring`

```text
ExpectationSemiring(base: 'Semiring[T]') -> 'None'
```

The expectation construction ``(weight, weighted statistic)``.

#### `ExpectationSemiring.star`

Property.

```text
ExpectationSemiring.star(self) -> 'StarSelector[tuple[T, T]] | None'
```

Declare no closure for an arbitrary expectation base.

#### `ExpectationSemiring.multiply`

Method.

```text
ExpectationSemiring.multiply(self, left: 'tuple[T, T]', right: 'tuple[T, T]', /) -> 'tuple[T, T]'
```

Multiply weights and apply the product rule to statistics.

#### `ExpectationSemiring.one`

Property.

```text
ExpectationSemiring.one(self) -> 'tuple[T, T]'
```

Return the expectation multiplicative identity.

#### `ExpectationSemiring.add_idempotent`

Property.

```text
ExpectationSemiring.add_idempotent(self) -> 'bool'
```

Expectation addition is componentwise.

#### `ExpectationSemiring.add_selective`

Property.

```text
ExpectationSemiring.add_selective(self) -> 'bool'
```

Expectation addition is not selective in general.

#### `ExpectationSemiring.multiply_strictly_order_preserving`

Property.

```text
ExpectationSemiring.multiply_strictly_order_preserving(self) -> 'bool'
```

The mixed product has no inherited strict order.

#### `ExpectationSemiring.multiply_preserves_witness_order`

Property.

```text
ExpectationSemiring.multiply_preserves_witness_order(self) -> 'bool'
```

Report false because expectation multiplication mixes components.

#### `ExpectationSemiring.zero_sum_free`

Property.

```text
ExpectationSemiring.zero_sum_free(self) -> 'bool'
```

Derive zero-sum freedom from the base.

#### `ExpectationSemiring.no_zero_divisors`

Property.

```text
ExpectationSemiring.no_zero_divisors(self) -> 'bool'
```

The mixed component can vanish independently.

### `LawCheck`

```text
LawCheck(*values)
```

The mandatory comparison used to check a semiring law.

#### `LawCheck` members

- `EXACT` = `exact`
- `APPROXIMATE` = `approximate`
- `NOT_HELD` = `not-held`

### `LexicographicSemiring`

```text
LexicographicSemiring(first: 'Semiring[T]', second: 'Semiring[U]', exact_workload: 'bool' = False, order_preserving_workload: 'bool' = False) -> 'None'
```

A selective first semiring with second-component aggregation on ties.

``exact_workload`` declares that the caller's values make approximate
component laws exact. Without it, an approximate component is refused. A
false declaration can make a fold non-associative on fractional float values
and silently change its result with the evaluation order.

``order_preserving_workload`` declares that multiplication preserves strict
first-component order on the caller's values. Without it, a first component
that cannot promise this property is refused. A false declaration lets a
strictly worse cost tie or overtake after rounded multiplication, making the
lexicographic choice wrong. Exact components need neither declaration.

#### `LexicographicSemiring.star`

Property.

```text
LexicographicSemiring.star(self) -> 'StarSelector[tuple[T, U]] | None'
```

Declare no closure for arbitrary lexicographic components.

#### `LexicographicSemiring.add`

Method.

```text
LexicographicSemiring.add(self, left: 'tuple[T, U]', right: 'tuple[T, U]', /) -> 'tuple[T, U]'
```

Choose by the first component and aggregate the second on a tie.

#### `LexicographicSemiring.multiply`

Method.

```text
LexicographicSemiring.multiply(self, left: 'tuple[T, U]', right: 'tuple[T, U]', /) -> 'tuple[T, U]'
```

Multiply componentwise within the restricted carrier.

#### `LexicographicSemiring.encode`

Method.

```text
LexicographicSemiring.encode(self, value: 'tuple[T, U]', /) -> 'object'
```

Encode a validated lexicographic value.

#### `LexicographicSemiring.decode`

Method.

```text
LexicographicSemiring.decode(self, value: 'object', /) -> 'tuple[T, U]'
```

Decode and validate a lexicographic value.

#### `LexicographicSemiring.add_idempotent`

Property.

```text
LexicographicSemiring.add_idempotent(self) -> 'bool'
```

Derive idempotence from both components.

#### `LexicographicSemiring.add_selective`

Property.

```text
LexicographicSemiring.add_selective(self) -> 'bool'
```

A tie may aggregate to a new second value.

#### `LexicographicSemiring.multiply_strictly_order_preserving`

Property.

```text
LexicographicSemiring.multiply_strictly_order_preserving(self) -> 'bool'
```

The restricted carrier excludes nonzero pairs with a zero component.

#### `LexicographicSemiring.no_zero_divisors`

Property.

```text
LexicographicSemiring.no_zero_divisors(self) -> 'bool'
```

The restricted carrier makes componentwise zero operands whole zeros.

### `LogProbabilitySemiring`

```text
LogProbabilitySemiring()
```

The inexact log-sum-exp semiring over finite IEEE-double log weights.

Values are log weights: finite doubles, or ``-inf`` as the zero. Addition is
the numerically stable log-sum-exp, multiplication is ordinary addition of
logs, and ``0.0`` is the one. Positive values are admitted because a weight
need not be a normalized probability. A path of ``-1000`` log weights folds
without the underflow that raw exponentials suffer, which is the reason to
fold in this carrier rather than in probabilities.

Every required law is checked approximately except addition commutativity,
which the symmetric log-sum-exp keeps exactly. That is the honest claim for
floating-point accumulation, and an acyclic dependency graph does not
change it: a finite derivation set makes the *search* exhaustive and says
nothing about the arithmetic. ``ExpectationSemiring`` refuses this base
for the same reason, and that refusal stands. A result that leaves the
finite carrier is refused rather than read as mass created or destroyed.
The carrier declares no star.

``normalize`` is the readout above the algebra: it reads log weights out
as probabilities of a total, and a construct applying it says so where it
reports the result. ``readouts`` names it, so a caller can declare it and
nothing else is mistaken for one.

#### `LogProbabilitySemiring.add`

Method.

```text
LogProbabilitySemiring.add(self, left: 'float', right: 'float', /) -> 'float'
```

Return the log of the summed weights, computed stably.

#### `LogProbabilitySemiring.multiply`

Method.

```text
LogProbabilitySemiring.multiply(self, left: 'float', right: 'float', /) -> 'float'
```

Add log weights, refusing overflow and preserving the annihilator.

#### `LogProbabilitySemiring.normalize`

Method.

```text
LogProbabilitySemiring.normalize(self, values: 'Iterable[float]', total: 'float', /) -> 'tuple[float, ...]'
```

Read log weights out as probabilities of a total, in one pass.

Each result is ``exp(value - total)``. A total equal to the zero has no
mass to normalize against and is refused rather than answered with
fabricated probabilities. A value that exceeds the total by rounding
reads as a probability slightly above one, and that is reported as
read: the readout normalizes, it does not clip. Every value is held to
the carrier, and a difference or an exponential that leaves the finite
carrier is refused as overflow rather than read as infinite mass.

#### `LogProbabilitySemiring.encode`

Method.

```text
LogProbabilitySemiring.encode(self, value: 'float', /) -> 'object'
```

Encode a log weight losslessly without non-JSON numeric tokens.

#### `LogProbabilitySemiring.decode`

Method.

```text
LogProbabilitySemiring.decode(self, value: 'object', /) -> 'float'
```

Decode lossless hexadecimal log-weight text.

### `Path`

```text
type Path = tuple[str, ...]
```

### `PathSemiring`

```text
PathSemiring() -> 'None'
```

The exact decimal tropical semiring enriched with tied best paths.

#### `PathSemiring.star`

Property.

```text
PathSemiring.star(self) -> 'StarSelector[tuple[Decimal, tuple[tuple[str, ...], ...]]]'
```

Return the proved 0-closed closure for path values.

#### `PathSemiring.multiply_preserves_witness_order`

Property.

```text
PathSemiring.multiply_preserves_witness_order(self) -> 'bool'
```

Report preservation of the exact decimal cost ordering.

### `PathValue`

```text
type PathValue = tuple[decimal.Decimal, tuple[Path, ...]]
```

### `PathWitnessSemiring`

```text
PathWitnessSemiring()
```

The exact semiring of finite path sets under union and concatenation.

#### `PathWitnessSemiring.add`

Method.

```text
PathWitnessSemiring.add(self, left: 'tuple[tuple[str, ...], ...]', right: 'tuple[tuple[str, ...], ...]', /) -> 'tuple[tuple[str, ...], ...]'
```

Union two path sets.

#### `PathWitnessSemiring.multiply`

Method.

```text
PathWitnessSemiring.multiply(self, left: 'tuple[tuple[str, ...], ...]', right: 'tuple[tuple[str, ...], ...]', /) -> 'tuple[tuple[str, ...], ...]'
```

Concatenate every pair of paths.

#### `PathWitnessSemiring.encode`

Method.

```text
PathWitnessSemiring.encode(self, value: 'tuple[tuple[str, ...], ...]', /) -> 'object'
```

Encode paths as nested JSON arrays.

#### `PathWitnessSemiring.decode`

Method.

```text
PathWitnessSemiring.decode(self, value: 'object', /) -> 'tuple[tuple[str, ...], ...]'
```

Decode nested JSON arrays of path labels.

### `ProductSemiring`

```text
ProductSemiring(left: 'Semiring[T]', right: 'Semiring[U]') -> None
```

The componentwise product of two semirings.

#### `ProductSemiring.star`

Property.

```text
ProductSemiring.star(self) -> 'StarSelector[tuple[T, U]] | None'
```

Declare no closure for arbitrary component products.

#### `ProductSemiring.zero`

Property.

```text
ProductSemiring.zero(self) -> 'tuple[T, U]'
```

Return the pair of additive identities.

#### `ProductSemiring.one`

Property.

```text
ProductSemiring.one(self) -> 'tuple[T, U]'
```

Return the pair of multiplicative identities.

#### `ProductSemiring.add_selective`

Property.

```text
ProductSemiring.add_selective(self) -> 'bool'
```

Report false because components may select opposite operands.

#### `ProductSemiring.multiply_strictly_order_preserving`

Property.

```text
ProductSemiring.multiply_strictly_order_preserving(self) -> 'bool'
```

Report false because a nonzero pair may have a zero component.

#### `ProductSemiring.multiply_preserves_witness_order`

Property.

```text
ProductSemiring.multiply_preserves_witness_order(self) -> 'bool'
```

Report false because an external order need not be componentwise.

#### `ProductSemiring.no_zero_divisors`

Property.

```text
ProductSemiring.no_zero_divisors(self) -> 'bool'
```

Report false because complementary zero components multiply to zero.

#### `ProductSemiring.add_associativity`

Property.

```text
ProductSemiring.add_associativity(self) -> 'LawCheck'
```

Derive the mandatory addition-associativity check.

#### `ProductSemiring.multiply_associativity`

Property.

```text
ProductSemiring.multiply_associativity(self) -> 'LawCheck'
```

Derive the mandatory multiplication-associativity check.

#### `ProductSemiring.add_commutativity`

Property.

```text
ProductSemiring.add_commutativity(self) -> 'LawCheck'
```

Derive the mandatory addition-commutativity check.

#### `ProductSemiring.left_distributivity`

Property.

```text
ProductSemiring.left_distributivity(self) -> 'LawCheck'
```

Derive the mandatory left-distributivity check.

#### `ProductSemiring.right_distributivity`

Property.

```text
ProductSemiring.right_distributivity(self) -> 'LawCheck'
```

Derive the mandatory right-distributivity check.

#### `ProductSemiring.add`

Method.

```text
ProductSemiring.add(self, left: 'tuple[T, U]', right: 'tuple[T, U]', /) -> 'tuple[T, U]'
```

Add each component.

#### `ProductSemiring.multiply`

Method.

```text
ProductSemiring.multiply(self, left: 'tuple[T, U]', right: 'tuple[T, U]', /) -> 'tuple[T, U]'
```

Multiply each component.

#### `ProductSemiring.encode`

Method.

```text
ProductSemiring.encode(self, value: 'tuple[T, U]', /) -> 'object'
```

Encode both components as a JSON array.

#### `ProductSemiring.decode`

Method.

```text
ProductSemiring.decode(self, value: 'object', /) -> 'tuple[T, U]'
```

Decode a two-component JSON array.

### `SelectionSemiring`

```text
SelectionSemiring(cost: 'Semiring[T]', payload_identity: 'U', tie_invariant_payload: 'bool', payload_multiply: 'Callable[[U, U], U]' = _PayloadAddition(), payload_encode: 'Callable[[U], object] | None' = None, payload_decode: 'Callable[[object], U] | None' = None) -> None
```

Select a winning cost together with its payload.

``tie_invariant_payload`` must be declared true because this construction
cannot check it. Addition keeps the first operand on a cost tie, so a false
declaration makes results depend on operand order when tied optimal paths
carry different payloads. Such payloads require accumulation instead.

``payload_identity`` supplies the payload at both cost identities. Payload
multiplication defaults to addition, which concatenates tuple witnesses;
callers can supply another operation for another payload. The caller
declares that ``payload_identity`` is its two-sided identity and that
``payload_multiply`` is associative; the law checks derived from ``cost``
assume both conditions. Construction can test the identity against itself
but cannot prove either condition over every payload. Float costs can create
ties by rounding, so tie invariance covers arithmetic ties as well as exact
ones. The default codec writes a tuple witness as a JSON array and restores
that tuple; callers provide codecs for other compound payload shapes.

#### `SelectionSemiring.star`

Property.

```text
SelectionSemiring.star(self) -> 'StarSelector[tuple[T, U]] | None'
```

Declare no closure for an arbitrary payload combination.

#### `SelectionSemiring.zero`

Property.

```text
SelectionSemiring.zero(self) -> 'tuple[T, U]'
```

Pair the cost zero with the payload identity.

#### `SelectionSemiring.one`

Property.

```text
SelectionSemiring.one(self) -> 'tuple[T, U]'
```

Pair the cost one with the payload identity.

#### `SelectionSemiring.add_associativity`

Property.

```text
SelectionSemiring.add_associativity(self) -> 'LawCheck'
```

Derive the mandatory addition-associativity check from the cost.

#### `SelectionSemiring.multiply_associativity`

Property.

```text
SelectionSemiring.multiply_associativity(self) -> 'LawCheck'
```

Derive the mandatory multiplication-associativity check from the cost.

#### `SelectionSemiring.add_commutativity`

Property.

```text
SelectionSemiring.add_commutativity(self) -> 'LawCheck'
```

Declare that operand-ordered tie selection is not commutative.

#### `SelectionSemiring.left_distributivity`

Property.

```text
SelectionSemiring.left_distributivity(self) -> 'LawCheck'
```

Derive the mandatory left-distributivity check from the cost.

#### `SelectionSemiring.right_distributivity`

Property.

```text
SelectionSemiring.right_distributivity(self) -> 'LawCheck'
```

Derive the mandatory right-distributivity check from the cost.

#### `SelectionSemiring.add_idempotent`

Property.

```text
SelectionSemiring.add_idempotent(self) -> 'bool'
```

Derive addition idempotence from the cost semiring.

#### `SelectionSemiring.add`

Method.

```text
SelectionSemiring.add(self, left: 'tuple[T, U]', right: 'tuple[T, U]', /) -> 'tuple[T, U]'
```

Select the first cost winner and carry its payload.

#### `SelectionSemiring.multiply`

Method.

```text
SelectionSemiring.multiply(self, left: 'tuple[T, U]', right: 'tuple[T, U]', /) -> 'tuple[T, U]'
```

Multiply costs and combine payloads.

#### `SelectionSemiring.encode`

Method.

```text
SelectionSemiring.encode(self, value: 'tuple[T, U]', /) -> 'object'
```

Encode the cost and payload as a JSON array.

#### `SelectionSemiring.decode`

Method.

```text
SelectionSemiring.decode(self, value: 'object', /) -> 'tuple[T, U]'
```

Decode a cost and payload JSON array.

### `Semiring`

```text
Semiring(*args, **kwargs)
```

Operations, carrier boundary, encoding, and declared algebraic laws.

#### `Semiring.zero`

Property.

```text
Semiring.zero(self) -> 'T'
```

Return the additive identity.

#### `Semiring.one`

Property.

```text
Semiring.one(self) -> 'T'
```

Return the multiplicative identity.

#### `Semiring.add_associativity`

Property.

```text
Semiring.add_associativity(self) -> 'LawCheck'
```

Return the required check for addition associativity.

#### `Semiring.multiply_associativity`

Property.

```text
Semiring.multiply_associativity(self) -> 'LawCheck'
```

Return the required check for multiplication associativity.

#### `Semiring.add_commutativity`

Property.

```text
Semiring.add_commutativity(self) -> 'LawCheck'
```

Return the required check for addition commutativity.

#### `Semiring.left_distributivity`

Property.

```text
Semiring.left_distributivity(self) -> 'LawCheck'
```

Return the required check for left distributivity.

#### `Semiring.right_distributivity`

Property.

```text
Semiring.right_distributivity(self) -> 'LawCheck'
```

Return the required check for right distributivity.

#### `Semiring.add_idempotent`

Property.

```text
Semiring.add_idempotent(self) -> 'bool'
```

Report whether addition is idempotent.

#### `Semiring.star`

Property.

```text
Semiring.star(self) -> 'StarSelector[T] | None'
```

Name this carrier's closure and its warrant, or declare none.

#### `Semiring.multiply_commutative`

Property.

```text
Semiring.multiply_commutative(self) -> 'bool'
```

Report whether multiplication is commutative.

#### `Semiring.add_selective`

Property.

```text
Semiring.add_selective(self) -> 'bool'
```

Report whether addition always selects one operand.

#### `Semiring.multiply_strictly_order_preserving`

Property.

```text
Semiring.multiply_strictly_order_preserving(self) -> 'bool'
```

Report strict order preservation away from zero.

#### `Semiring.multiply_preserves_witness_order`

Property.

```text
Semiring.multiply_preserves_witness_order(self) -> 'bool'
```

Report whether multiplication preserves the order induced by addition.

#### `Semiring.zero_sum_free`

Property.

```text
Semiring.zero_sum_free(self) -> 'bool'
```

Report whether a sum is zero only when both operands are zero.

#### `Semiring.no_zero_divisors`

Property.

```text
Semiring.no_zero_divisors(self) -> 'bool'
```

Report whether a product is zero only with a zero operand.

#### `Semiring.add`

Method.

```text
Semiring.add(self, left: 'T', right: 'T', /) -> 'T'
```

Return ``left ⊕ right``.

#### `Semiring.multiply`

Method.

```text
Semiring.multiply(self, left: 'T', right: 'T', /) -> 'T'
```

Return ``left ⊗ right``.

#### `Semiring.encode`

Method.

```text
Semiring.encode(self, value: 'T', /) -> 'object'
```

Return a strict-JSON representation of a carrier value.

#### `Semiring.decode`

Method.

```text
Semiring.decode(self, value: 'object', /) -> 'T'
```

Decode and validate a strict-JSON representation.

### `StarRefusal`

Refuse a closure the declaring algebra does not license for this operand.

### `StarSelector`

```text
type StarSelector[T] = tiergraph.semiring.ZeroClosedStar[T]
```

### `TropicalSemiring`

```text
TropicalSemiring() -> 'None'
```

The inexact IEEE-double min-plus semiring.

#### `TropicalSemiring.star`

Property.

```text
TropicalSemiring.star(self) -> 'StarSelector[float]'
```

Return this carrier's explicitly declared 0-closed closure.

### `ZeroClosedStar`

```text
ZeroClosedStar(algebra: 'Semiring[T]', name: 'str' = 'zero-closed') -> None
```

Admit 0-closed operands and close their finite ascending chain to one.

#### `ZeroClosedStar.admits`

Method.

```text
ZeroClosedStar.admits(self, operand: 'T', /) -> 'bool'
```

Prove that the operand is dominated by the multiplicative identity.

#### `ZeroClosedStar.close`

Method.

```text
ZeroClosedStar.close(self, operand: 'T', /) -> 'T'
```

Return the closure after checking the warrant.

### `inexact_laws`

```text
inexact_laws(algebra: 'Semiring[Any]', /) -> 'tuple[str, ...]'
```

Name the required semiring laws this algebra does not check exactly.

The order is the fixed precondition order, so the first name is stable across
runs and can be quoted in a refusal. An empty result is a statement about the
algebra's *declaration* only: it says the five laws are declared exact, not
that they hold, which is why a caller that needs the stronger fact has to
check the laws at values rather than read this tuple.

### `tiergraph.schema`

This module is importable and usable, but carries no API-stability promise at version 0.8.0.

### `Refusal`

```text
Refusal(stage: 'RefusalStage', message: 'str', also: 'Iterable[Refusal]' = ()) -> 'None'
```

Refuse one read, naming its stage and applicable conditions at its site.

``stage`` places the refusal in the declared total order, and ``also``
carries the further conditions applicable at the refusing site, each a
refusal in its own right.  It is not a census of the document.  Both are
data rather than prose, so a caller acts on the order without matching
message text.  Both are declared on the class as well as assigned, so a
caller reads them as fields of what it caught rather than recovering them
with ``getattr``.

This is the one base every staged refusal has.  Wherever the order is
observed it is observed whole, so ``except Refusal`` has to catch all of it:
a base that covered a prefix of the order would send a caller who read the
declaration past the ranks it left out.  Which readers observe the order,
and where one of them answers unstaged instead, is stated in the format
document rather than here -- this base is about the ranks a caller must be
able to catch, not about which readers produce them.  Subclasses say which
channel refused, never which ranks a caller has to expect.

A ``Refusal`` is a ``ValueError``, so every caller that already catches one
still does.

Not every refusal this package raises is staged, and the boundary is worth
stating because ``except Refusal`` is silent on the other side of it.  A
*declaration* refuses its own construction with a plain ``ValueError`` --
``SealDeclaration``, ``FoldDeclaration``, ``AttributeValuation``,
``ActionDeclaration`` and ``ReactDeclaration`` all refuse an empty name that
way, and ``DistributionWitness`` refuses its own the same way.
Those are refusals about the description a caller wrote, not about a
document or a graph, so there is no read for a stage to rank them within.
What carries a stage is the refusal of *content*: a document a reader
refuses, and a graph ``GraphValidationError`` refuses at construction or
validation.  A caller that wants both catches ``ValueError``.

``tiergraph.Refusal`` is the staged document-reader refusal.  The other
exported classes ending in ``Refusal`` -- ``StarRefusal``,
``EffectRefusal``, ``ExactnessRefusal``, ``PathRefusal``, and
``ProfileRegistrationRefusal`` -- are ``ValueError`` subclasses carrying
their own subsystem's data.  They have no document-reader stage.

It is declared here, beside ``RefusalStage`` and for the same reason: this
module is the base every other imports, so the channel that refuses from
here can share the base without the cycle that reaching upward would create.

### `RefusalStage`

```text
RefusalStage(*values)
```

Number the classes a refusal can belong to, lowest reported first.

A reader routinely meets several conditions at once.  The stage numbers put
them in one order, so a caller is told the condition that explains the rest
rather than whichever check happened to run first: a refusal at one stage
explains what a later stage would have reported, and the converse never
holds.  Bytes that are not text have no JSON to nest; a document announcing
a format this release does not implement has a field set this release cannot
judge; a member of the wrong construction has no value to place in a
declared language; a name that does not resolve cannot keep a promise.

The stages rank the conditions that apply to one node.  Nodes are read from
the outside in and members in their declared order, so an enclosing node's
condition precedes its members' whatever their stages, and the pair of a
node and a stage totally orders every condition one read can meet.

A condition is carried beside the primary one only while it stays
applicable once the primary is known.  A field set is not judged against a
declaration the document never selected, so a foreign version is reported
alone rather than with the fields that being foreign introduces.

The stage is the stable part of a refusal; the wording is diagnostic.

The vocabulary lives here, beside the other declared enumerations, because
both refusal channels have to name it: this module is the base every other
imports, so a refusal raised from here can carry a stage without the cycle
that reaching upward for it would create.

#### `RefusalStage` members

- `ENVELOPE` = `1`
- `ENCODING` = `2`
- `SYNTAX` = `3`
- `CONSTRUCTION` = `4`
- `DISCRIMINATOR` = `5`
- `SHAPE` = `6`
- `VALUE` = `7`
- `REFERENCE` = `8`
- `SEMANTICS` = `9`

### `json_schema`

```text
json_schema(format_version: 'str') -> 'dict[str, JsonValue]'
```

Generate the JSON Schema document for the format this release implements.

### `shape_hash`

```text
shape_hash() -> 'str'
```

Hash the declaration independently of JSON Schema presentation.

### `tiergraph.match`

This module is a supported secondary API.

### `MAX_PATTERN_POSITIONS`

int([x]) -> integer
int(x, base=10) -> integer

Convert a number or string to an integer, or return 0 if no arguments
are given.  If x is a number, return x.__int__().  For floating point
numbers, this truncates towards zero.

If x is not a number or if base is given, then x must be a string,
bytes, or bytearray instance representing an integer literal in the
given base.  The literal can be preceded by '+' or '-' and be surrounded
by whitespace.  The base defaults to 10.  Valid bases are 0 and 2-36.
Base 0 means to interpret the base from the string as an integer literal.
>>> int('0b100', base=0)
4

### `MAX_PATTERN_STATES`

Largest Thompson NFA accepted by the regular-pattern compiler. Current value: `1000000`.

### `AdjacentRuns`

```text
AdjacentRuns(source: 'Selector', offsets: 'OffsetProfile') -> None
```

Split selected offset items where consecutive half-open spans do not meet.

### `AltPattern`

```text
AltPattern(parts: 'tuple[Pattern, ...]') -> None
```

Match any one part, without preserving run multiplicity.

### `AtomPattern`

```text
AtomPattern(predicate: 'Predicate') -> None
```

Consume one item when a value predicate holds on it.

### `BoundOrdering`

```text
BoundOrdering(graph: 'Graph', ordering: 'Ordering') -> None
```

Read one ordering's scopes once for reuse by any number of patterns.

Pass this prepared value to :meth:`CompiledPattern.bind` to share ordering
validation and scope construction across patterns. Pass a raw ordering when
each pattern should prepare the ordering independently.

### `BoundPattern`

```text
BoundPattern(compiled: 'CompiledPattern', graph: 'Graph', ordering: 'Ordering | BoundOrdering') -> 'None'
```

Answer every match view from one eager preparation on one graph.

With a valid raw ordering, each view equals the corresponding per-call
:class:`CompiledPattern` method. Predicates bind before scopes are read, but
each view is checked only when called. A prebuilt :class:`BoundOrdering`
instead supplies scopes shared across patterns; its construction validates
ordering before this handle binds predicates or a view validates its
operation. This handle holds its deeply immutable graph for its own lifetime.

#### `BoundPattern.exists`

Method.

```text
BoundPattern.exists(self, *, open_right: 'bool' = False, budget: 'WorkBudget | WorkMeter | None' = None) -> 'bool | OpenPatternResult[bool]'
```

Return whether any scope contains an accepting span.

#### `BoundPattern.focus`

Method.

```text
BoundPattern.focus(self, *, open_right: 'bool' = False, budget: 'WorkBudget | WorkMeter | None' = None) -> 'NodeSet | OpenPatternResult[NodeSet]'
```

Return every item consumed by a focus edge on an accepting run.

#### `BoundPattern.spans`

Method.

```text
BoundPattern.spans(self, *, limit: 'int | None' = None, open_right: 'bool' = False, budget: 'WorkBudget | WorkMeter | None' = None) -> 'SpanMatches | OpenPatternResult[SpanMatches]'
```

Return each distinct accepting span once in scope-major order.

#### `BoundPattern.count`

Method.

```text
BoundPattern.count(self, *, open_right: 'bool' = False, budget: 'WorkBudget | WorkMeter | None' = None) -> 'int | OpenPatternResult[int]'
```

Count distinct accepting scope spans, never NFA runs.

### `CompiledPattern`

```text
CompiledPattern(pattern: 'Pattern', start: 'int', accept: 'int', epsilon: 'tuple[tuple[int, ...], ...]', atom_edges: 'tuple[tuple[int, ...], ...]', predicates: 'tuple[Predicate, ...]') -> None
```

Hold one Thompson epsilon-NFA and its deduplicated atom table.

``epsilon`` and ``atom_edges`` store compact integer edges. Compilation
validates the pattern size and refuses inputs that exceed the documented
AST-node or NFA-state ceilings before returning this value.

#### `CompiledPattern.max_width`

Property.

```text
CompiledPattern.max_width(self) -> 'int | None'
```

Return the exact maximum consumed item count, or None if unbounded.

#### `CompiledPattern.bind`

Method.

```text
CompiledPattern.bind(self, graph: 'Graph', ordering: 'Ordering | BoundOrdering', *, budget: 'WorkBudget | WorkMeter | None' = None) -> 'BoundPattern'
```

Bind predicates, read scopes and evaluate atoms once on one graph.

With a raw ordering, predicates bind before the ordering scopes are
prepared. View checks happen only when that view is called, so a combined
ordering and view defect reports the ordering first. A prebuilt
:class:`BoundOrdering` has already validated and prepared its scopes, so
an ordering defect is reported when that value is constructed.

#### `CompiledPattern.exists`

Method.

```text
CompiledPattern.exists(self, graph: 'Graph', ordering: 'Ordering', *, open_right: 'bool' = False, budget: 'WorkBudget | WorkMeter | None' = None) -> 'bool | OpenPatternResult[bool]'
```

Return whether any scope contains an accepting span.

#### `CompiledPattern.focus`

Method.

```text
CompiledPattern.focus(self, graph: 'Graph', ordering: 'Ordering', *, open_right: 'bool' = False, budget: 'WorkBudget | WorkMeter | None' = None) -> 'NodeSet | OpenPatternResult[NodeSet]'
```

Return every item consumed by a focus edge on an accepting run.

#### `CompiledPattern.spans`

Method.

```text
CompiledPattern.spans(self, graph: 'Graph', ordering: 'Ordering', *, limit: 'int | None' = None, open_right: 'bool' = False, budget: 'WorkBudget | WorkMeter | None' = None) -> 'SpanMatches | OpenPatternResult[SpanMatches]'
```

Return each distinct accepting span once in scope-major order.

#### `CompiledPattern.count`

Method.

```text
CompiledPattern.count(self, graph: 'Graph', ordering: 'Ordering', *, open_right: 'bool' = False, budget: 'WorkBudget | WorkMeter | None' = None) -> 'int | OpenPatternResult[int]'
```

Count distinct accepting scope spans, never NFA runs.

### `ContainerOrder`

```text
ContainerOrder(relation: 'QualifiedName', containers: 'Selector') -> None
```

Read each selected container's direct children as a separate scope.

### `DeclaredOrder`

```text
DeclaredOrder(successor: 'QualifiedName', members: 'Selector', open_left: 'bool' = False, chain: 'Selector | None' = None) -> None
```

Read an explicitly declared polyadic successor chain as one scope.

``successor`` names the ordered polyadic relation. ``members`` selects the
items returned to matching views. ``chain`` may select a larger complete
chain from which those members are projected; when omitted, ``members`` is
also the complete chain. ``open_left`` admits a chain whose predecessor lies
outside the selected members.

#### `DeclaredOrder.project`

Method.

```text
DeclaredOrder.project(self, members: 'Selector') -> 'DeclaredOrder'
```

Project this order while retaining its complete-chain selector.

### `EndPattern`

```text
EndPattern() -> None
```

Match the position at the end of one ordering scope.

### `Extent`

```text
Extent(*values)
```

State whether an output witness list was truncated.

#### `Extent` members

- `EXHAUSTIVE` = `exhaustive`
- `CUT_AT_BOUND` = `cut-at-bound`
- `CUT_AT_BUDGET` = `cut-at-budget`

### `FocusPattern`

```text
FocusPattern(body: 'Pattern') -> None
```

Mark consumed items that a sequence selector returns.

### `OpenPatternResult`

```text
OpenPatternResult(result: 'Result', pending_from: 'tuple[int | None, ...]') -> None
```

Carry a settled open-edge result and one watermark per ordering scope.

### `Ordering`

```text
type Ordering = tiergraph.match.TierOrder | tiergraph.match.ContainerOrder | tiergraph.match.AdjacentRuns | tiergraph.match.DeclaredOrder
```

### `Pattern`

```text
type Pattern = tiergraph.match.AtomPattern | tiergraph.match.SeqPattern | tiergraph.match.AltPattern | tiergraph.match.RepeatPattern | tiergraph.match.FocusPattern | tiergraph.match.StartPattern | tiergraph.match.EndPattern
```

### `RepeatPattern`

```text
RepeatPattern(body: 'Pattern', min: 'int', max: 'int | None') -> None
```

Repeat one pattern between inclusive minimum and maximum counts.

### `SeqPattern`

```text
SeqPattern(parts: 'tuple[Pattern, ...]') -> None
```

Match every part in order.

### `SpanMatch`

```text
SpanMatch(scope: 'int', start: 'int', end: 'int', items: 'tuple[Node, ...]', offsets: 'tuple[int, int] | None') -> None
```

Carry one distinct matching scope span and optional physical offsets.

#### `SpanMatch.to_data`

Method.

```text
SpanMatch.to_data(self) -> 'dict[str, JsonValue]'
```

Return one match as strict JSON data.

### `SpanMatches`

```text
SpanMatches(matches: 'tuple[SpanMatch, ...]', extent: 'Extent') -> None
```

Carry distinct matching spans and whether their witness list was cut.

#### `SpanMatches.to_data`

Method.

```text
SpanMatches.to_data(self) -> 'dict[str, JsonValue]'
```

Return matches and extent as strict JSON data.

### `SpanPairs`

```text
SpanPairs(pairs: 'tuple[tuple[Node, Node], ...]', extent: 'Extent') -> None
```

Carry interval-related node pairs in declared order and their extent.

#### `SpanPairs.to_data`

Method.

```text
SpanPairs.to_data(self) -> 'dict[str, JsonValue]'
```

Return pairs and extent as strict JSON data.

### `StartPattern`

```text
StartPattern() -> None
```

Match the position at the start of one ordering scope.

### `TierOrder`

```text
TierOrder(tier: 'QualifiedName') -> None
```

Read one tier as one scope in declared item order.

### `compile_pattern`

```text
compile_pattern(pattern: 'Pattern') -> 'CompiledPattern'
```

Validate and compile one pattern to a Thompson epsilon-NFA.

### `format_pattern`

```text
format_pattern(pattern: 'Pattern', syntax: 'PredicateSyntax') -> 'str'
```

Return canonical text for one representable sequence pattern.

### `ordering_to_data`

```text
ordering_to_data(ordering: 'Ordering') -> 'JsonValue'
```

Return any supported sequence ordering as strict JSON data.

### `parse_pattern`

```text
parse_pattern(text: 'str', syntax: 'PredicateSyntax') -> 'Pattern'
```

Parse one complete regular sequence pattern.

### `parse_pattern_at`

```text
parse_pattern_at(text: 'str', start: 'int', syntax: 'PredicateSyntax', terminator: 'str | None' = None) -> 'tuple[Pattern, int]'
```

Parse until a requested terminator outside item tests and groups.

### `pattern_loads`

```text
pattern_loads(source: 'str | bytes') -> 'Pattern'
```

Decode one strict declarative pattern from JSON.

### `pattern_to_data`

```text
pattern_to_data(pattern: 'Pattern') -> 'JsonValue'
```

Return one pattern as strict tagged JSON data.

### `span_pairs`

```text
span_pairs(graph: 'Graph', left: 'Selector', right: 'Selector', relation: 'IntervalRelation', offsets: 'OffsetProfile', *, limit: 'int | None' = None, budget: 'WorkBudget | WorkMeter | None' = None) -> 'SpanPairs'
```

Return related item pairs in left-major declared order.

### `tiergraph.predicate`

This module is a supported secondary API.

### `And`

```text
And(args: 'tuple[Predicate, ...]') -> None
```

Intersect zero or at least two predicate decisions.

### `Bare`

```text
Bare(text: 'str') -> None
```

Carry an unquoted token until its cell supplies a type at bind.

### `BoundPredicate`

```text
BoundPredicate(predicate: 'Predicate', graph: 'Graph') -> None
```

Evaluate one predicate against nodes of its bound graph.

#### `BoundPredicate.holds`

Method.

```text
BoundPredicate.holds(self, node: 'Node', *, budget: 'WorkBudget | WorkMeter | None' = None) -> 'bool'
```

Decide one graph node after evaluating every atom it can reach.

#### `BoundPredicate.select`

Method.

```text
BoundPredicate.select(self, candidates: 'NodeSet', *, budget: 'WorkBudget | WorkMeter | None' = None) -> 'NodeSet'
```

Return candidates that hold, retaining the candidate set's domain.

### `Cell`

```text
Cell(attribute: 'QualifiedName', pointer: 'tuple[str, ...]' = ()) -> None
```

Name one declared attribute and an optional JSON pointer beneath it.

### `Compare`

```text
Compare(operand: 'Operand', order: 'Order', value: 'Literal') -> None
```

Apply one exact numeric ordering relation.

### `CompiledPredicate`

```text
CompiledPredicate(predicate: 'Predicate') -> None
```

Hold a validated graph-free predicate ready to bind.

#### `CompiledPredicate.bind`

Method.

```text
CompiledPredicate.bind(self, graph: 'Graph') -> 'BoundPredicate'
```

Validate names and type-impossible operations against one graph.

### `Current`

```text
Current(pointer: 'tuple[str, ...]' = ()) -> None
```

Name the current JSON element and an optional pointer beneath it.

### `Double`

```text
Double(lexical: 'str') -> None
```

Carry one xsd:double by canonical lexical identity.

### `Elements`

```text
Elements(operand: 'Operand', quantifier: 'Quantifier', body: 'Predicate') -> None
```

Quantify a predicate over one JSON array operand.

### `Equals`

```text
Equals(operand: 'Operand', values: 'tuple[Literal, ...]') -> None
```

Test exact typed membership in a nonempty literal tuple.

### `Has`

```text
Has(operand: 'Operand', alias: 'str') -> None
```

Test whether an operand is present, carrying its missing-cell spelling.

### `IntervalRelation`

```text
IntervalRelation(*values)
```

Name one directed relation between half-open integer intervals.

#### `IntervalRelation` members

- `EQUAL` = `equal`
- `CONTAINS` = `contains`
- `WITHIN` = `within`
- `PROPER_CONTAINS` = `proper-contains`
- `PROPER_WITHIN` = `proper-within`
- `OVERLAPS` = `overlaps`
- `MEETS` = `meets`
- `MET_BY` = `met-by`

### `Literal`

```text
type Literal = str | int | decimal.Decimal | bool | None | tiergraph.predicate.Double | tiergraph.predicate.Bare
```

### `Matches`

```text
Matches(operand: 'Operand', regex: 'str') -> None
```

Fullmatch a string with the facility's regular-language subset.

### `Not`

```text
Not(arg: 'Predicate') -> None
```

Complement one completed two-valued predicate decision.

### `OffsetProfile`

```text
OffsetProfile(origin: 'QualifiedName', extent: 'QualifiedName | None' = None, end: 'QualifiedName | None' = None, partition: 'QualifiedName | None' = None) -> None
```

Name an origin and exactly one integer measure, with a partition.

### `Operand`

```text
type Operand = tiergraph.predicate.Cell | tiergraph.predicate.Current
```

### `Or`

```text
Or(args: 'tuple[Predicate, ...]') -> None
```

Union zero or at least two predicate decisions.

### `Order`

```text
Order(*values)
```

Name an exact ordered comparison.

#### `Order` members

- `LT` = `<`
- `LE` = `<=`
- `GT` = `>`
- `GE` = `>=`

### `Predicate`

```text
type Predicate = tiergraph.predicate.Has | tiergraph.predicate.Equals | tiergraph.predicate.Compare | tiergraph.predicate.Matches | tiergraph.predicate.Elements | tiergraph.predicate.Related | tiergraph.predicate.Spans | tiergraph.predicate.And | tiergraph.predicate.Or | tiergraph.predicate.Not
```

### `PredicateSyntax`

```text
PredicateSyntax(namespaces: 'tuple[NamespaceDeclaration, ...]', default_prefix: 'str | None' = None, missing_aliases: 'tuple[str, ...]' = ('none',), vocabularies: 'tuple[tuple[QualifiedName, tuple[str, ...]], ...]' = ()) -> None
```

Declare names, a default prefix, and missing-cell text spellings.

#### `PredicateSyntax.for_graph`

Class method.

```text
PredicateSyntax.for_graph(cls, graph: 'Graph', *, default_prefix: 'str | None' = None, missing_aliases: 'tuple[str, ...]' = ('none',), vocabularies: 'tuple[tuple[QualifiedName, tuple[str, ...]], ...]' = ()) -> 'PredicateSyntax'
```

Build syntax from a graph's namespace declarations.

### `Quantifier`

```text
Quantifier(*values)
```

Quantify a predicate over the elements of a JSON array.

#### `Quantifier` members

- `ANY` = `any`
- `ALL` = `all`
- `NONE` = `none`

### `Related`

```text
Related(relation: 'QualifiedName', direction: 'WalkDirection', quantifier: 'Quantifier', target: 'Predicate') -> None
```

Quantify a target predicate over one relation step.

### `Spans`

```text
Spans(offsets: 'OffsetProfile', relation: 'IntervalRelation', quantifier: 'Quantifier', other: 'QualifiedName', target: 'Predicate') -> None
```

Quantify a target predicate over items in an interval relation.

### `compile_predicate`

```text
compile_predicate(predicate: 'Predicate') -> 'CompiledPredicate'
```

Compile one frozen predicate without consulting a graph.

### `format_predicate`

```text
format_predicate(predicate: 'Predicate', syntax: 'PredicateSyntax') -> 'str'
```

Return the canonical value-test text for a representable predicate.

### `parse_predicate`

```text
parse_predicate(text: 'str', syntax: 'PredicateSyntax') -> 'Predicate'
```

Parse one complete value-test predicate.

### `parse_predicate_at`

```text
parse_predicate_at(text: 'str', start: 'int', syntax: 'PredicateSyntax', terminator: 'str' = '}') -> 'tuple[Predicate, int]'
```

Parse until one requested terminator outside quotes and parentheses.

### `predicate_loads`

```text
predicate_loads(source: 'str | bytes') -> 'Predicate'
```

Decode one strict predicate JSON document.

### `predicate_to_data`

```text
predicate_to_data(predicate: 'Predicate') -> 'JsonValue'
```

Return strict JSON data for one predicate AST.

### `tiergraph.cli`

This module is importable and usable, but carries no API-stability promise at version 0.8.0.

### `build_parser`

```text
build_parser() -> 'argparse.ArgumentParser'
```

Return the argument parser.

### `main`

```text
main(argv: 'Sequence[str] | None' = None) -> 'int'
```

Run the command line. Returns the process exit status.

### `tiergraph.spanview`

This module is importable and usable, but carries no API-stability promise at version 0.8.0.

### `SPANVIEW_FORMAT_VERSION`

Version tag written by span-view JSON. Current value: `1`.

### `Span`

```text
Span(label: 'str', start: 'int', end: 'int', char_start: 'int | None', char_end: 'int | None', value: 'str | None', score: 'str | None', path: 'str', alternatives: 'tuple[SpanAlternative, ...]' = ()) -> None
```

Describe one selected span whose extent is derived from live coverage.

The kernel graph stores membership, not an origin-plus-extent snapshot;
this projection carries the resulting bounds for renderers.  Coverage must
remain contiguous, so a new base item inside its range requires the caller
to update membership rather than being absorbed or splitting it.

### `SpanAlternative`

```text
SpanAlternative(value: 'str | None', score: 'str | None', path: 'str') -> None
```

Describe one ranked candidate associated with a selected span.

### `SpanView`

```text
SpanView(text: 'str', spans: 'tuple[Span, ...]', base_surfaces: 'tuple[str, ...]') -> None
```

Hold reconstructed input text and its ordered, non-overlapping spans.

### `SpanViewProfile`

```text
SpanViewProfile(base_tier: 'QualifiedName', span_tiers: 'tuple[QualifiedName, ...]', coverage_relation: 'QualifiedName', score_attribute: 'QualifiedName', value_attribute: 'QualifiedName', base_surface_attribute: 'QualifiedName | None' = None, char_offset_attribute: 'QualifiedName | None' = None, alternative_relation: 'QualifiedName | None' = None, point_tiers: 'tuple[QualifiedName, ...]' = (), point_coverage_relation: 'QualifiedName | None' = None, value_attributes: 'tuple[tuple[QualifiedName, QualifiedName], ...]' = (), clock_face: 'str' = 'tick') -> None
```

Name the graph declarations a segmentation has to be selected among.

``coverage_relation`` and ``alternative_relation`` must name bipartite
declarations.  A span is an interval over the base tier, so each fact this
view reads is one base endpoint paired with one span item; there is no
reading of a polyadic instance's ordered sides that keeps that meaning.
Naming a non-bipartite declaration is refused rather than skipped, because
silently reading only the bipartite collection would report a partial
segmentation as a complete one.

One declaration the projection reads is deliberately absent: a span's
``label`` is the item type its tier's simple membership supplies, read
through :meth:`Graph.item_type` and falling back to the tier's short name
when the tier is untyped.  A profile names what a reading has to be
selected among, and a tier carries at most one simple membership, so there
is nothing there to select.

#### `SpanViewProfile.to_data`

Method.

```text
SpanViewProfile.to_data(self) -> 'dict[str, JsonValue]'
```

Return the declarative span-view profile document used by the CLI.

#### `SpanViewProfile.from_data`

Class method.

```text
SpanViewProfile.from_data(cls, data: 'object') -> 'SpanViewProfile'
```

Decode a strict declarative span-view profile document.

### `span_view`

```text
span_view(graph: 'Graph', profile: 'SpanViewProfile', *, alternatives: 'bool' = False) -> 'SpanView'
```

Read a segmentation and its coverage entirely through the public graph API.

### `to_html`

```text
to_html(view: 'SpanView', *, alternatives: 'bool' = False) -> 'str'
```

Return a self-contained, injection-safe HTML segmentation report.

### `to_json`

```text
to_json(view: 'SpanView', *, alternatives: 'bool' = False) -> 'str'
```

Return one stable, indented JSON span-view document.

### `to_jsonl`

```text
to_jsonl(views: 'SpanView | Iterable[SpanView]', *, record: 'str' = 'input', alternatives: 'bool' = False) -> 'str'
```

Return compact JSON Lines records grouped by input or flattened by span.

### `to_text`

```text
to_text(view: 'SpanView', *, alternatives: 'bool' = False) -> 'str'
```

Return a deterministic ruler and aligned plain-text span table.

### `tiergraph.textgrid`

This module is importable and usable, but carries no API-stability promise at version 0.8.0.

### `TextGridReadResult`

```text
TextGridReadResult(graph: ForwardRef('Graph'), profile: ForwardRef('SpanViewProfile'))
```

Return the decoded graph beside the profile that selects its TextGrid tiers.

#### `TextGridReadResult.clock`

Property.

```text
TextGridReadResult.clock(self) -> 'ClockProfile'
```

Construct the physical clock declared by the decoded graph.

### `from_textgrid`

```text
from_textgrid(document: 'str | bytes', *, unit: 'str' = 's', containment_rule: 'str' = 'enclosure') -> 'TextGridReadResult'
```

Decode a TextGrid using enclosure or endpoint-coincidence containment.

### `to_textgrid`

```text
to_textgrid(graph: 'Graph', profile: 'SpanViewProfile', *, clock: 'ClockProfile | None' = None, scale: 'int | None' = None) -> 'str'
```

Render declared span and point tiers as a long-form TextGrid document.


## Companion package

### `DotPresentation`

```text
DotPresentation(tier_name: 'Callable[..., str | None] | None' = None, node_id: 'Callable[..., str | None] | None' = None, item_label: 'Callable[..., str | None] | None' = None, relation_name: 'Callable[..., str | None] | None' = None, relation_style: 'Callable[..., str | None] | None' = None) -> None
```

Optional overrides for tier labels, node ids, and item labels in DOT.

Each hook is optional and may return ``None`` for any element to fall back
to the renderer's default. When the whole profile is ``None`` -- or a hook
is absent or returns ``None`` -- the emitted DOT is byte-identical to the
default rendering; the hooks are the only surface through which output can
differ. Overridden tier names and item labels are quoted through the same
``_quote`` path as the defaults. An overridden node id is emitted verbatim
as a DOT identifier and is applied consistently at the node definition and
at every edge endpoint that references it, so an override never leaves a
dangling reference.

``tier_name`` is called as ``tier_name(tier)`` with the
:class:`tiergraph.Tier`; ``node_id`` as ``node_id(reference)`` with the
item's :class:`tiergraph.ItemRef`; and ``item_label`` as
``item_label(item, tier)`` with the :class:`tiergraph.Item` and its owning
:class:`tiergraph.Tier`, so a consumer can fall back to a tier-derived
label. When ``item_label`` is absent or returns ``None`` the default label
is built from the item's durable id and attributes, and appends the item's
physical timing when a clock is rendering one. On the occupied-spine path
no clock reaches that default, so it holds under a structural clock as
well.

Two further hooks shape relation rendering on the occupied-spine path.
``relation_style`` is called as ``relation_style(relation)`` with the
relation instance; when it returns ``"bipartite"`` for a polyadic relation
that relation is drawn as individual parent-to-child edges (one per
source-target pair) under a ``// Declared relations.`` header rather than as
the default polyadic fan-out. ``relation_name`` is called as
``relation_name(relation)`` and supplies each such edge's label, defaulting
to the relation's local name. Both are per-relation: absent hooks, a ``None``
return, or any non-``"bipartite"`` style leave relations rendered exactly as
before.

### `dumps`

```text
dumps(graph: 'Graph', *, clock: 'ClockProfile | None' = None, presentation: 'DotPresentation | None' = None, binding: 'Callable[..., tuple[ClockCoordinate, ClockCoordinate]] | None' = None, include_empty_tiers: 'bool' = False) -> 'str'
```

Return byte-stable DOT for ``graph``.

With ``clock``, the complete refined clock is the horizontal spine. Timed
tier boundaries align with that spine, event extents end at their bound
refined coordinates, and physical timing is included when the profile exposes
it. Explicitly untimed tiers are still drawn on their own structural axes.
Without ``clock``, every tier uses its own ordered structural boundaries.

Empty tiers are omitted by default and included when
``include_empty_tiers`` is true. Attribute names and values are rendered as
data; the renderer assigns no domain-specific meaning to them. A clock
profile must belong to this exact graph instance, not merely an equal graph,
because its cached derived state was computed from that instance.

A structural clock (built by :meth:`ClockProfile.from_boundary_values`)
selects the occupied-spine rendering: the clock tier is drawn only as the
spine, an occupied clock column is anchored on its item node, and empty
columns keep a guide point. ``binding`` places the non-clock items: when it
is supplied it MUST return, for every visible non-clock item, the
``(start, end)`` :class:`tiergraph.ClockCoordinate` pair naming the collapsed
columns the item occupies. There is no untimed lane, so returning ``None``
is refused with the offending item named. The kernel never parses domain
identifiers; the caller supplies the placement.

### `dumps_spans`

```text
dumps_spans(graph: 'Graph', profile: 'SpanViewProfile', *, alternatives: 'bool' = False, include_empty_tiers: 'bool' = False) -> 'str'
```

Return deterministic DOT focused on a segmentation and its span extents.
