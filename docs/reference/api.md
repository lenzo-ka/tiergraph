# API reference

This page is generated from the shipped objects and the documentation manifest.
It covers 190 top-level `tiergraph` exports exactly once.

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

Name an existing boundary by its anchor without changing the graph.

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
AttachValue(domain: 'AttributeDomain', target: 'AttributeTarget', value: 'AttributeValue') -> None
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
GraphEditor.declare(self, declaration: 'EditDeclaration') -> 'GraphEditor'
```

Add one namespace, tier, attribute, or relation declaration.

Declarations are added, never changed or withdrawn.  Retyping or
withdrawing one retroactively decides the meaning of every value and
reference that already depends on it, which is a migration of the
whole graph rather than an edit to a place in it.

#### `GraphEditor.set_attribute`

Method.

```text
GraphEditor.set_attribute(self, target: 'EditTarget', value: 'AttributeValue') -> 'GraphEditor'
```

Give one carrier this value, replacing any value of the same name.

The value's declaration decides which carrier the target names, so a
caller spells the place and not the domain.  An undeclared attribute
is refused here rather than at freeze, because without a declaration
there is no domain to read the target against.

#### `GraphEditor.remove_attribute`

Method.

```text
GraphEditor.remove_attribute(self, target: 'EditTarget', name: 'QualifiedName') -> 'GraphEditor'
```

Take the named value off one carrier, refusing when it is absent.

#### `GraphEditor.insert_item`

Method.

```text
GraphEditor.insert_item(self, tier: 'QualifiedName', index: 'int', item: 'Item') -> 'GraphEditor'
```

Insert one item at a tier index, carrying later references with it.

An index equal to the tier's item count appends.

#### `GraphEditor.remove_item`

Method.

```text
GraphEditor.remove_item(self, reference: 'ItemRef | DurableItemRef') -> 'GraphEditor'
```

Remove one item, refusing while the graph still references it.

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
GraphEditor.add_relation(self, instance: 'RelationInstance | PolyadicRelationInstance') -> 'GraphEditor'
```

Add one relation instance to the collection its arity belongs to.

#### `GraphEditor.remove_relation`

Method.

```text
GraphEditor.remove_relation(self, target: 'int | str') -> 'GraphEditor'
```

Remove one relation instance by bipartite index or by durable id.

## Fold

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

### `FoldDeclaration`

```text
FoldDeclaration(name: 'str', graph: 'Graph', valuation: 'AttributeValuation', semiring: 'Semiring[Value]', lift: 'Lift[Value]', transitions: 'tuple[FoldTransition, ...]', index_axes: 'tuple[tuple[str, ...], ...]' = (), roots: 'tuple[ItemRef, ...]' = (), witness_order: 'WitnessOrder[Value] | None' = None, tie_policy: 'TiePolicy | None' = None, output_cap: 'int' = 1, carrier_operation_cost: 'int' = 1, ranked_output: 'bool' = False, exactness: 'FoldExactness' = <FoldExactness.UNDECLARED: 'undeclared'>) -> None
```

Bind one named interpretation to a graph, valuation, algebra, and relation.

The dependency relation is finite and need not be acyclic. An acyclic one has a
finite derivation set; a cyclic one is specified by the starred fixpoint the
algebra's ``star`` solves, and ``exactness`` is where that difference is stated.

A readout or final division above the algebra is not currently provided. If one
is introduced, it must be declared as part of what the fold profile records. A
construct whose soundness depends on a property it cannot verify must declare
that property rather than assume it.

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
FoldDeclaration.run(self) -> 'FoldResult[Value]'
```

Evaluate every state with the semiring's own declared operations.

Addition and multiplication carry an acyclic relation. A cyclic
component reaches the algebra's ``star`` as well, which is what
specifies the fixpoint there.

#### `FoldDeclaration.check_exactness`

Method.

```text
FoldDeclaration.check_exactness(self, *, derivation_budget: 'int' = 1024) -> 'FoldCertificate[Value]'
```

Demand this fold's exactness claim and discharge it, or refuse.

Every branch bites, and the asymmetry is deliberate. An ``UNDECLARED``
exactness is refused with **the declaration to be made**, and no fold is
run for it; a false claim is refused with **a semantic counterexample**.
Declining to say is not the same as saying the weaker thing.

The claim is checked two ways, and neither is a carrier swap: re-running
the fold under another algebra reads the same ``transitions``, so it
confirms whatever the declaration says rather than testing it.

The first way is a law search over **probes the fold produces itself**,
rather than a probe set a caller supplies. Distributivity is what
regroups a sum over derivations into a fold over shared structure, so a
carrier that fails it at one of those probes cannot be folded exactly,
whatever its declared ``LawCheck`` says. The probes are capped, so this
is a search over some of the values the fold reaches and not over all of
them: finding no refutation here is not a proof. A carrier that
cannot evaluate its own laws at its own values raises; that is a defect
in the carrier boundary, not something to be swallowed here.

The second way enumerates the derivations and combines them with no
sharing at all, then compares. It runs only when the whole enumeration
fits in ``derivation_budget``, and the returned certificate reports
whether it did. A search that finds no counterexample has found no
counterexample; it has not proved the claim, and the certificate says
which of the two happened rather than implying the stronger one.

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

Give one dependency relation its local AND/OR incidence meaning.

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

### `GrammarChartProfile`

```text
GrammarChartProfile(forest: 'ParseForest') -> None
```

Address chart alternatives in a stable order within one forest snapshot.

The profile vocabulary is
``/chart/NONTERMINAL/START/END/alternatives/INDEX``. Alternative indices are
independent of rule weights, but intentionally are not stable across forest
snapshots whose sets of alternatives differ.

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

### `GrammarRule`

```text
GrammarRule(left: 'QualifiedName', source: 'GrammarPattern', target: 'GrammarPattern', boundary: 'AttributeValue' = AttributeValue(name=QualifiedName(namespace='urn:tiergraph:grammar', local_name='boundary'), value_type=<XsdType.STRING: 'string'>, lexical='complete'), awaited_variables: 'tuple[AttributeValue, ...]' = (), weight: 'AttributeValue | None' = None) -> None
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
ParseForest(graph: 'Graph', program: 'Program', root: 'ItemRef', fold: 'FoldDeclaration[bool]', declaration: 'GrammarDeclaration', collapsed: 'bool' = True) -> None
```

Carry a machine-built parse forest and its Boolean interpretation.

#### `ParseForest.recognized`

Method.

```text
ParseForest.recognized(self) -> 'bool'
```

Return whether the designated start span has a derivation.

#### `ParseForest.result`

Method.

```text
ParseForest.result(self) -> 'FoldResult[bool]'
```

Evaluate and return the complete Boolean fold result.

#### `ParseForest.count`

Method.

```text
ParseForest.count(self) -> 'int'
```

Count derivations when the grammar lies in the finite-fold domain.

#### `ParseForest.best`

Method.

```text
ParseForest.best(self, count: 'int' = 1) -> 'tuple[BestDerivation, ...]'
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

### `best`

```text
best(grammar: 'LoweredGrammar | ParseForest', input_tokens: 'Sequence[str] | None' = None, count: 'int' = 1) -> 'tuple[BestDerivation, ...]'
```

Return folded derivations by exact cost, choosing canonical paths on ties.

### `count`

```text
count(grammar: 'LoweredGrammar | ParseForest', input_tokens: 'Sequence[str] | None' = None) -> 'int'
```

Return the derivation count from a new or previously built forest.

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
recognize(grammar: 'LoweredGrammar', input_tokens: 'Sequence[str]', namespace: 'str' = 'urn:tiergraph:grammar:chart', *, collapse_units: 'bool' = True) -> 'ParseForest'
```

Build a chart forest for token input using polynomial span deduction.

For a fixed grammar whose longest source pattern has length ``m``, the
exhaustive boundary discipline takes ``O(n^(m+1))`` time and polynomial
space in input length ``n``.

## Kernel

### `AttributeDeclaration`

```text
AttributeDeclaration(name: 'QualifiedName', domain: 'AttributeDomain', value_type: 'XsdType') -> None
```

Declare an optional, at-most-one value for one domain and XSD type.

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

### `BipartiteRelationDeclaration`

```text
BipartiteRelationDeclaration(name: 'QualifiedName', left_type: 'QualifiedName', right_type: 'QualifiedName', left_endpoint: 'RelationEndpointKind' = <RelationEndpointKind.ITEM: 'item'>, right_endpoint: 'RelationEndpointKind' = <RelationEndpointKind.ITEM: 'item'>, single_parent: 'bool' = False, acyclic: 'bool' = False, attributes: 'tuple[AttributeValue, ...]' = ()) -> None
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
Boundary(reference: 'BoundaryRef | DurableBoundaryRef', attributes: 'tuple[AttributeValue, ...]') -> None
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
Graph(namespaces: 'tuple[NamespaceDeclaration, ...]', tiers: 'tuple[Tier, ...]', relation_declarations: 'tuple[RelationDeclaration, ...]', relations: 'tuple[RelationInstance, ...]' = (), attribute_declarations: 'tuple[AttributeDeclaration, ...]' = (), boundary_values: 'tuple[Boundary, ...]' = (), attributes: 'tuple[AttributeValue, ...]' = (), polyadic_relations: 'tuple[PolyadicRelationInstance, ...]' = (), seals: 'tuple[Seal, ...]' = (), layers: 'tuple[Layer, ...]' = ()) -> None
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
Graph.layer_values(self, subject: 'LayerSubject', name: 'QualifiedName', delivery: 'Delivery') -> 'tuple[AttributeValue, ...]'
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

#### `Graph.is_sealed`

Method.

```text
Graph.is_sealed(self, coordinate: 'ItemRef | BoundaryRef') -> 'bool'
```

Report whether this coordinate stands inside its carrier's seal.

#### `Graph.edit`

Method.

```text
Graph.edit(self) -> 'GraphEditor'
```

Return a mutable editor holding a copy of this graph's content.

The editor answers the same operations this graph answers, and answers
them in place: one validation runs at ``freeze()`` instead of one per
operation.  Whether an operation rewrites or mutates follows from the
carrier the caller holds, never from an argument passed to it.

#### `Graph.declare`

Method.

```text
Graph.declare(self, declaration: 'EditDeclaration') -> 'Graph'
```

Return a new graph carrying one more declaration.

#### `Graph.set_attribute`

Method.

```text
Graph.set_attribute(self, target: 'EditTarget', value: 'AttributeValue') -> 'Graph'
```

Return a new graph whose target carries this value under its name.

#### `Graph.remove_attribute`

Method.

```text
Graph.remove_attribute(self, target: 'EditTarget', name: 'QualifiedName') -> 'Graph'
```

Return a new graph whose target no longer carries this name.

#### `Graph.insert_item`

Method.

```text
Graph.insert_item(self, tier: 'QualifiedName', index: 'int', item: 'Item') -> 'Graph'
```

Return a new graph with one more item at this tier index.

#### `Graph.remove_item`

Method.

```text
Graph.remove_item(self, reference: 'ItemRef | DurableItemRef') -> 'Graph'
```

Return a new graph without this item.

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
Graph.add_relation(self, instance: 'RelationInstance | PolyadicRelationInstance') -> 'Graph'
```

Return a new graph carrying one more relation instance.

#### `Graph.remove_relation`

Method.

```text
Graph.remove_relation(self, target: 'int | str') -> 'Graph'
```

Return a new graph without the relation instance this names.

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
Item(durable_id: 'str | None' = None, attributes: 'tuple[AttributeValue, ...]' = ()) -> None
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
PolyadicRelationDeclaration(name: 'QualifiedName', sources: 'RelationSideDeclaration', targets: 'RelationSideDeclaration', unique_sources: 'bool' = False, distinct_targets: 'bool' = False, single_parent: 'bool' = False, acyclic: 'bool' = False, targets_subset_of: 'QualifiedName | None' = None, attributes: 'tuple[AttributeValue, ...]' = ()) -> None
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
PolyadicRelationInstance(declaration: 'QualifiedName', sources: 'tuple[RelationEndpointRef, ...]', targets: 'tuple[RelationEndpointRef, ...]', durable_id: 'str | None' = None, attributes: 'tuple[AttributeValue, ...]' = ()) -> None
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

### `Refusal`

```text
Refusal(stage: 'RefusalStage', message: 'str', also: 'Iterable[Refusal]' = ()) -> 'None'
```

Refuse one read, naming its stage and every further applicable condition.

``stage`` places the refusal in the declared total order, and ``also``
carries the conditions that remain applicable once this one is known, each a
refusal in its own right.  Both are data rather than prose, so a caller acts
on the order without matching message text.  Both are declared on the class
as well as assigned, so a caller reads them as fields of what it caught
rather than recovering them with ``getattr``.

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
RelationInstance(declaration: 'QualifiedName', left: 'RelationEndpointRef', right: 'RelationEndpointRef', durable_id: 'str | None' = None, attributes: 'tuple[AttributeValue, ...]' = ()) -> None
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

Type alias.

Type aliases are created through the type statement::

    type Alias = int

In this example, Alias and int will be treated equivalently by static
type checkers.

At runtime, Alias is an instance of TypeAliasType. The __name__
attribute holds the name of the type alias. The value of the type alias
is stored in the __value__ attribute. It is evaluated lazily, so the
value is computed only if the attribute is accessed.

Type aliases can also be generic::

    type ListOrSet[T] = list[T] | set[T]

In this case, the type parameters of the alias are stored in the
__type_params__ attribute.

See PEP 695 for more information.

### `SimpleRelationDeclaration`

```text
SimpleRelationDeclaration(name: 'QualifiedName', tier: 'QualifiedName', item_type: 'QualifiedName', attributes: 'tuple[AttributeValue, ...]' = ()) -> None
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
Tier(declaration: 'TierDeclaration', items: 'tuple[Item, ...]' = (), attributes: 'tuple[AttributeValue, ...]' = ()) -> None
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
Consensus(subject: 'LayerSubject', name: 'QualifiedName', readings: 'tuple[tuple[LayerName, AttributeValue], ...]', agreed: 'bool') -> None
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
LayerFact(subject: 'LayerSubject', value: 'AttributeValue') -> None
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

Type alias.

Type aliases are created through the type statement::

    type Alias = int

In this example, Alias and int will be treated equivalently by static
type checkers.

At runtime, Alias is an instance of TypeAliasType. The __name__
attribute holds the name of the type alias. The value of the type alias
is stored in the __value__ attribute. It is evaluated lazily, so the
value is computed only if the attribute is accessed.

Type aliases can also be generic::

    type ListOrSet[T] = list[T] | set[T]

In this case, the type parameters of the alias are stored in the
__type_params__ attribute.

See PEP 695 for more information.

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

Version tag written by the JSON wire codec. Current value: `0.2.0`.

### `MACHINE_VERSION`

Version tag for serialized construction programs. Current value: `1`.

### `MAX_DOCUMENT_BYTES`

Largest UTF-8 JSON document accepted by the wire codec. Current value: `16777216`.

### `MAX_JSON_DEPTH`

Deepest JSON container nesting accepted by the wire codec. Current value: `256`.

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

Installed distribution version. Current value: `0.2.0`.

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

Type alias.

Type aliases are created through the type statement::

    type Alias = int

In this example, Alias and int will be treated equivalently by static
type checkers.

At runtime, Alias is an instance of TypeAliasType. The __name__
attribute holds the name of the type alias. The value of the type alias
is stored in the __value__ attribute. It is evaluated lazily, so the
value is computed only if the attribute is accessed.

Type aliases can also be generic::

    type ListOrSet[T] = list[T] | set[T]

In this case, the type parameters of the alias are stored in the
__type_params__ attribute.

See PEP 695 for more information.

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

Type alias.

Type aliases are created through the type statement::

    type Alias = int

In this example, Alias and int will be treated equivalently by static
type checkers.

At runtime, Alias is an instance of TypeAliasType. The __name__
attribute holds the name of the type alias. The value of the type alias
is stored in the __value__ attribute. It is evaluated lazily, so the
value is computed only if the attribute is accessed.

Type aliases can also be generic::

    type ListOrSet[T] = list[T] | set[T]

In this case, the type parameters of the alias are stored in the
__type_params__ attribute.

See PEP 695 for more information.

### `RoleValue`

Type alias.

Type aliases are created through the type statement::

    type Alias = int

In this example, Alias and int will be treated equivalently by static
type checkers.

At runtime, Alias is an instance of TypeAliasType. The __name__
attribute holds the name of the type alias. The value of the type alias
is stored in the __value__ attribute. It is evaluated lazily, so the
value is computed only if the attribute is accessed.

Type aliases can also be generic::

    type ListOrSet[T] = list[T] | set[T]

In this case, the type parameters of the alias are stored in the
__type_params__ attribute.

See PEP 695 for more information.

### `SpanViewProfile`

```text
SpanViewProfile(base_tier: 'QualifiedName', span_tiers: 'tuple[QualifiedName, ...]', coverage_relation: 'QualifiedName', score_attribute: 'QualifiedName', value_attribute: 'QualifiedName', base_surface_attribute: 'QualifiedName', char_offset_attribute: 'QualifiedName | None' = None, alternative_relation: 'QualifiedName | None' = None) -> None
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

#### `SpanViewProfile.from_data`

Class method.

```text
SpanViewProfile.from_data(cls, data: 'object') -> 'SpanViewProfile'
```

Decode a strict declarative span-view profile document.

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
a frozen graph's operation and at ``GraphEditor.freeze()`` by the editor's,
and a caller who means to keep the boundary anchors it elsewhere first.

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

Type alias.

Type aliases are created through the type statement::

    type Alias = int

In this example, Alias and int will be treated equivalently by static
type checkers.

At runtime, Alias is an instance of TypeAliasType. The __name__
attribute holds the name of the type alias. The value of the type alias
is stored in the __value__ attribute. It is evaluated lazily, so the
value is computed only if the attribute is accessed.

Type aliases can also be generic::

    type ListOrSet[T] = list[T] | set[T]

In this case, the type parameters of the alias are stored in the
__type_params__ attribute.

See PEP 695 for more information.

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

### `evaluate_selection`

```text
evaluate_selection(graph: 'Graph', selector: 'Selector', *, path_profile: 'PathProfile' = StructuralPathProfile()) -> 'NodeSet'
```

Evaluate a graph-free selector into one canonical node set.

### `selection_loads`

```text
selection_loads(source: 'str | bytes') -> 'Selector'
```

Decode one strict declarative selector from JSON.

The text is read under the same envelope, encoding, and syntax stages the
graph document reader applies, so a caller routes on a declared stage here
as well as there.

## Semirings

### `BOOLEAN`

The exact Boolean semiring, with disjunction and conjunction.

### `COUNTING`

The exact natural-number semiring.

### `DECIMAL_TROPICAL`

An exact min-plus or max-plus semiring with XSD-decimal finite values.

### `PATH`

The exact decimal tropical semiring enriched with tied best paths.

### `StarRefusal`

Refuse a closure the declaring algebra does not license for this operand.

### `StarSelector`

Type alias.

Type aliases are created through the type statement::

    type Alias = int

In this example, Alias and int will be treated equivalently by static
type checkers.

At runtime, Alias is an instance of TypeAliasType. The __name__
attribute holds the name of the type alias. The value of the type alias
is stored in the __value__ attribute. It is evaluated lazily, so the
value is computed only if the attribute is accessed.

Type aliases can also be generic::

    type ListOrSet[T] = list[T] | set[T]

In this case, the type parameters of the alias are stored in the
__type_params__ attribute.

See PEP 695 for more information.

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

Return reached nodes and a one-sided report of the step cap.

``truncated`` is ``False`` when the last step found nothing the walk had
not already reached, which is also what a step that exhausts the frontier
and the cap at once reports: the cap being reached is not what this field
says. A ``False`` report is a guarantee that ``nodes`` is the whole
reachable set less the source selection, which :meth:`Walk.evaluate`
excludes from what it returns. ``True`` says only that the cap ended a step
that was still finding nodes, which a walk that had already reached
everything also reports; separating the two costs another step.

#### `WalkResult.to_data`

Method.

```text
WalkResult.to_data(self) -> 'dict[str, JsonValue]'
```

Return strict-JSON traversal data in canonical node order.

## Supported secondary surface

### `tiergraph.build`

This module is importable and usable, but carries no API-stability promise at version 0.2.0.

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
Document.attribute(self, name: 'Name', value_type: 'XsdType | str', *, domain: 'AttributeDomain | str' = <AttributeDomain.ITEM: 'item'>) -> 'None'
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

### `LexicographicSemiring`

```text
LexicographicSemiring(first: 'Semiring[T]', second: 'Semiring[U]') -> 'None'
```

A selective first semiring with second-component aggregation on ties.

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

### `Path`

Type alias.

Type aliases are created through the type statement::

    type Alias = int

In this example, Alias and int will be treated equivalently by static
type checkers.

At runtime, Alias is an instance of TypeAliasType. The __name__
attribute holds the name of the type alias. The value of the type alias
is stored in the __value__ attribute. It is evaluated lazily, so the
value is computed only if the attribute is accessed.

Type aliases can also be generic::

    type ListOrSet[T] = list[T] | set[T]

In this case, the type parameters of the alias are stored in the
__type_params__ attribute.

See PEP 695 for more information.

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

Type alias.

Type aliases are created through the type statement::

    type Alias = int

In this example, Alias and int will be treated equivalently by static
type checkers.

At runtime, Alias is an instance of TypeAliasType. The __name__
attribute holds the name of the type alias. The value of the type alias
is stored in the __value__ attribute. It is evaluated lazily, so the
value is computed only if the attribute is accessed.

Type aliases can also be generic::

    type ListOrSet[T] = list[T] | set[T]

In this case, the type parameters of the alias are stored in the
__type_params__ attribute.

See PEP 695 for more information.

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

Type alias.

Type aliases are created through the type statement::

    type Alias = int

In this example, Alias and int will be treated equivalently by static
type checkers.

At runtime, Alias is an instance of TypeAliasType. The __name__
attribute holds the name of the type alias. The value of the type alias
is stored in the __value__ attribute. It is evaluated lazily, so the
value is computed only if the attribute is accessed.

Type aliases can also be generic::

    type ListOrSet[T] = list[T] | set[T]

In this case, the type parameters of the alias are stored in the
__type_params__ attribute.

See PEP 695 for more information.

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

This module is importable and usable, but carries no API-stability promise at version 0.2.0.

### `Refusal`

```text
Refusal(stage: 'RefusalStage', message: 'str', also: 'Iterable[Refusal]' = ()) -> 'None'
```

Refuse one read, naming its stage and every further applicable condition.

``stage`` places the refusal in the declared total order, and ``also``
carries the conditions that remain applicable once this one is known, each a
refusal in its own right.  Both are data rather than prose, so a caller acts
on the order without matching message text.  Both are declared on the class
as well as assigned, so a caller reads them as fields of what it caught
rather than recovering them with ``getattr``.

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
### `tiergraph.cli`

This module is importable and usable, but carries no API-stability promise at version 0.2.0.

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

This module is importable and usable, but carries no API-stability promise at version 0.2.0.

### `SPANVIEW_FORMAT_VERSION`

str(object='') -> str
str(bytes_or_buffer[, encoding[, errors]]) -> str

Create a new string object from the given object. If encoding or
errors is specified, then the object must expose a data buffer
that will be decoded using the given encoding and error handler.
Otherwise, returns the result of object.__str__() (if defined)
or repr(object).
encoding defaults to sys.getdefaultencoding().
errors defaults to 'strict'.

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
SpanViewProfile(base_tier: 'QualifiedName', span_tiers: 'tuple[QualifiedName, ...]', coverage_relation: 'QualifiedName', score_attribute: 'QualifiedName', value_attribute: 'QualifiedName', base_surface_attribute: 'QualifiedName', char_offset_attribute: 'QualifiedName | None' = None, alternative_relation: 'QualifiedName | None' = None) -> None
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
