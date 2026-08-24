# API reference

This page is generated from the shipped objects and the documentation manifest.
It covers 138 top-level `tiergraph` exports exactly once.

## Action

### `ActionDeclaration`

```text
ActionDeclaration(name: 'str', apply: 'ActionFunction[Carrier, Result]', associative: 'bool', idempotent: 'bool', commutative: 'bool', semimodule: 'Semimodule[object, object] | None' = None) -> None
```

Declare executable behavior and trusted normalization tolerances.

``associative``, ``idempotent``, and ``commutative`` are self-attested at
declaration time. React uses them as normalization gates but does not prove
them; callers can separately execute ``ActionToleranceLawSuite``. The
An optional semimodule claim is checked over its declared finite samples
before the declaration can exist.

### `DistributionWitness`

```text
DistributionWitness(name: 'str') -> None
```

Opt in to executable one-for-one equivalence certification.

The witness supplies no operations, coordinate bridge, samples, or carrier.
On every one-for-one run, react extracts coordinates once with its declared
``yield_coordinates`` and requires its bound action to produce the same result
when applied one coordinate at a time and as one complete batch. This
certifies the concrete recognition and carrier being executed; it does not
prove equivalence for runs that have not been executed.

### `ReactDeclaration`

```text
ReactDeclaration(name: 'str', fold: 'FoldDeclaration[Value]', yield_coordinates: 'CoordinateYield', action: 'ActionDeclaration[Carrier, Result]', normalization: 'YieldNormalization' = YieldNormalization(collapse=False, unique=False, reorder=False), mode: 'ReactMode' = <ReactMode.TRANSACTIONAL: 'transactional'>, distribution: 'DistributionWitness | None' = None) -> None
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

### `Semimodule`

```text
Semimodule(scalar_zero: 'Scalar', scalar_one: 'Scalar', scalar_add: 'Callable[[Scalar, Scalar], Scalar]', scalar_multiply: 'Callable[[Scalar, Scalar], Scalar]', module_zero: 'Module', module_add: 'Callable[[Module, Module], Module]', scale: 'Callable[[Scalar, Module], Module]', scalar_samples: 'tuple[Scalar, ...]', module_samples: 'tuple[Module, ...]') -> None
```

Supply operations and samples for an explicit, opt-in semimodule claim.

Merely declaring an action does not claim or check these laws; callers that
provide this optional structure must execute a semimodule law suite.

### `WitnessCoordinate`

```text
WitnessCoordinate(position: 'tuple[int, ...]', value: 'object') -> None
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
YieldNormalization.apply(self, coordinates: 'tuple[WitnessCoordinate, ...]') -> 'tuple[WitnessCoordinate, ...]'
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
position attributes refine it to ``(coarse tick, ordered gap)``.  Thus two
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
clock boundary is the unrefined position ``(index, 0)``.  Partial document
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

#### `ClockProfile.from_position_values`

Class method.

```text
ClockProfile.from_position_values(cls, graph: 'Graph', clock_tier: 'QualifiedName', *, tick_attribute: 'QualifiedName', gap_attribute: 'QualifiedName', unit_attribute: 'QualifiedName | None' = None, collapse_shared_boundaries: 'bool' = False) -> 'ClockProfile'
```

Derive only the clock spine from the clock tier's boundary positions.

This construction path reads the ``(tick, gap)`` position attributes on
the clock tier's own boundaries -- exactly as the full constructor reads
them -- and yields the same :attr:`positions` sequence that the DOT
renderer draws as the spine. It requires neither a binding relation nor
a unit attribute, so it accepts a graph whose relations and document
attributes are empty; a unit is read only when ``unit_attribute`` is
given.

The result supports spine rendering alone. It carries no tier-to-clock
bindings, so every non-spine timing query -- :meth:`is_timed`,
:meth:`clock_position`, :meth:`refined_position`, :meth:`extent`,
:meth:`structural_span`, :meth:`timing`, and :meth:`duration` -- raises
rather than returning an answer it cannot justify. Binding other tiers
to the clock genuinely needs ``graph.relations`` and remains the full
constructor's responsibility; this path never weakens that validation.

With ``collapse_shared_boundaries``, each coarse tick's trailing gap --
its closing boundary, coincident with the next tick's opening boundary
-- is folded away so the spine shows one node per occupied position.
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

#### `ClockProfile.positions`

Property.

```text
ClockProfile.positions(self) -> 'tuple[ClockPosition, ...]'
```

Return the profile's validated refined clock positions in order.

#### `ClockProfile.is_timed`

Method.

```text
ClockProfile.is_timed(self, tier: 'QualifiedName') -> 'bool'
```

Report whether a tier chose complete clock binding.

#### `ClockProfile.clock_position`

Method.

```text
ClockProfile.clock_position(self, position: 'PositionRef') -> 'int'
```

Return the integral clock-tier boundary bound to one tier position.

#### `ClockProfile.refined_position`

Method.

```text
ClockProfile.refined_position(self, position: 'PositionRef') -> 'ClockPosition'
```

Return the coarse tick and ordered gap bound to one tier position.

#### `ClockProfile.extent`

Method.

```text
ClockProfile.extent(self, tier: 'QualifiedName') -> 'tuple[ClockPosition, ClockPosition]'
```

Return a timed tier's possibly partial refined clock extent.

#### `ClockProfile.structural_span`

Method.

```text
ClockProfile.structural_span(self, tier: 'QualifiedName', index: 'int') -> 'tuple[ClockPosition, ClockPosition]'
```

Return an event span between refined integral positions.

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

#### `ClockProfile.duration`

Method.

```text
ClockProfile.duration(self, tier: 'QualifiedName', index: 'int') -> 'tuple[int, Decimal]'
```

Return the legacy coarse-tick span and rate when a rate exists.

### `ClockPosition`

```text
ClockPosition(tick: 'int', gap: 'int' = 0) -> None
```

Name one integral gap inside an integral coarse tick.

### `PhysicalTiming`

```text
PhysicalTiming(start: 'Decimal', duration: 'Decimal', unit: 'str') -> None
```

Carry exact decimal values stamped with the profile's declared unit.

The unit is carried, not dimensionally enforced: this profile validates its
declaration and stamps stored values with it, but a stored decimal has no
independent unit metadata against which the declaration could be checked.

### `anchored_position`

```text
anchored_position(graph: 'Graph', position: 'PositionRef') -> 'DurablePositionRef'
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

Name the opcode that could not make its checked state transition.

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

### `PromotePosition`

```text
PromotePosition(reference: 'PositionRef', durable_id: 'str') -> None
```

Promote one structural boundary reference to anchored identity.

#### `PromotePosition.apply`

Method.

```text
PromotePosition.apply(self, graph: 'Graph') -> 'Graph'
```

Apply the kernel's checked boundary promotion operation.

#### `PromotePosition.to_data`

Method.

```text
PromotePosition.to_data(self) -> 'dict[str, JsonValue]'
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
FoldDeclaration(name: 'str', graph: 'Graph', valuation: 'AttributeValuation', semiring: 'Semiring[Value]', lift: 'Lift[Value]', transitions: 'tuple[FoldTransition, ...]', index_axes: 'tuple[tuple[str, ...], ...]' = (), roots: 'tuple[ItemRef, ...]' = (), witness_order: 'WitnessOrder[Value] | None' = None, tie_policy: 'TiePolicy | None' = None, output_cap: 'int' = 1, carrier_operation_cost: 'int' = 1, ranked_output: 'bool' = False) -> None
```

Bind one named interpretation to a graph, valuation, algebra, and finite DAG.

With ``ranked_output`` the fold also returns up to ``output_cap`` witnesses ranked
by the semiring's own order, which its multiplication must preserve
(``multiply_preserves_witness_order``); a custom ``witness_order`` is refused. Among
witnesses of equal carrier value the ranked selection is deterministic but not
guaranteed to be a globally canonical one.

#### `FoldDeclaration.coordinates`

Method.

```text
FoldDeclaration.coordinates(self) -> 'tuple[Coordinate, ...]'
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

Evaluate every state using only the semiring's addition and multiplication.

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
FoldResult(values: 'tuple[tuple[State, Value], ...]', roots: 'tuple[State, ...]', value: 'Value', provenance: 'Provenance | None', truncated: 'bool', cost: 'FoldCost', ranked_witnesses: 'tuple[RankedWitness[Value], ...] | None' = None) -> None
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

Order applications by rule ordinal and ordered child spans.

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

Pair a grammar with its replayable coordinate-hedge construction.

#### `LoweredGrammar.to_data`

Method.

```text
LoweredGrammar.to_data(self) -> 'dict[str, JsonValue]'
```

Return the declaration, graph, and construction fingerprint.

### `ParseForest`

```text
ParseForest(graph: 'Graph', program: 'Program', root: 'ItemRef', fold: 'FoldDeclaration[bool]', declaration: 'GrammarDeclaration') -> None
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

### `lower_grammar`

```text
lower_grammar(declaration: 'GrammarDeclaration', namespace: 'str' = 'urn:tiergraph:grammar') -> 'LoweredGrammar'
```

Lower a grammar through machine opcodes to an ordered coordinate hedge.

### `recognize`

```text
recognize(grammar: 'LoweredGrammar', input_tokens: 'Sequence[str]', namespace: 'str' = 'urn:tiergraph:grammar:chart') -> 'ParseForest'
```

Build a chart forest for token input using polynomial span deduction.

For a fixed grammar whose longest source pattern has length ``m``, the
exhaustive boundary discipline takes ``O(n^(m+1))`` time and polynomial
space in input length ``n``. Nullable expansion and unit closure remove
same-span dependencies before candidate construction, so every remaining
child span is shorter than its parent span.

## Kernel

### `AttributeDeclaration`

```text
AttributeDeclaration(name: 'QualifiedName', domain: 'AttributeDomain', value_type: 'XsdType') -> None
```

Declare an attribute's qualified name, domain, and XSD subset type.

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

#### `BipartiteRelationDeclaration.to_data`

Method.

```text
BipartiteRelationDeclaration.to_data(self) -> 'dict[str, JsonValue]'
```

Return the declaration as JSON-serializable data.

### `BoundarySide`

```text
BoundarySide(*values)
```

Choose the boundary immediately before or after an anchor.

### `Graph`

```text
Graph(namespaces: 'tuple[NamespaceDeclaration, ...]', tiers: 'tuple[Tier, ...]', relation_declarations: 'tuple[RelationDeclaration, ...]', relations: 'tuple[RelationInstance, ...]' = (), attribute_declarations: 'tuple[AttributeDeclaration, ...]' = (), position_values: 'tuple[Position, ...]' = (), attributes: 'tuple[AttributeValue, ...]' = (), polyadic_relations: 'tuple[PolyadicRelationInstance, ...]' = ()) -> None
```

Hold a validated immutable graph and derive order and empty boundaries.

Collections keyed by names or references are canonicalized because supply
order has no graph meaning: namespaces, relation and attribute declarations,
every attribute-value collection, sparse position values, and relation-side
allowed kinds and tiers.  Tiers, tier items, relation instances, and polyadic
endpoint sequences remain ordered because their sequence carries graph meaning.

#### `Graph.positions`

Method.

```text
Graph.positions(self, tier: 'QualifiedName') -> 'tuple[Position, ...]'
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

#### `Graph.resolve_position`

Method.

```text
Graph.resolve_position(self, reference: 'PositionRef | DurablePositionRef') -> 'PositionRef'
```

Resolve either identity level to the position's current coordinate.

#### `Graph.promote_item`

Method.

```text
Graph.promote_item(self, reference: 'ItemRef', durable_id: 'str') -> 'tuple[Graph, DurableItemRef]'
```

Return a graph carrying the caller's semantic id for one item.

#### `Graph.promote_position`

Method.

```text
Graph.promote_position(self, reference: 'PositionRef', durable_id: 'str') -> 'tuple[Graph, DurablePositionRef]'
```

Return a graph whose boundary anchor has durable identity.

#### `Graph.to_data`

Method.

```text
Graph.to_data(self) -> 'dict[str, JsonValue]'
```

Return graph content in canonical declaration order as JSON data.

### `GraphValidationError`

Report a declaration or graph-contract validation failure.

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

### `Position`

```text
Position(reference: 'PositionRef | DurablePositionRef', attributes: 'tuple[AttributeValue, ...]') -> None
```

Hold values for one addressable boundary while empty boundaries stay derived.

#### `Position.to_data`

Method.

```text
Position.to_data(self) -> 'dict[str, JsonValue]'
```

Return the position and its values as JSON-serializable data.

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

### `RelationEndpointKind`

```text
RelationEndpointKind(*values)
```

Declare whether one relation endpoint is an item or a boundary.

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

The growable XSD datatype subset admitted for attribute values.

## Metadata

### `FORMAT_VERSION`

Version tag written by the JSON wire codec. Current value: `6`.

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

Installed distribution version. Current value: `0.1.0`.

## Paths

### `AlternativeRef`

```text
AlternativeRef(owner: 'ItemRef | DurableItemRef', relation: 'QualifiedName', index: 'int') -> None
```

Select one profile-ordered alternative of an owning graph item.

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

Parse a strict JSON Pointer, refusing empty and malformed spellings.

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

### `PositionBinding`

```text
PositionBinding(reference: 'PositionRef | DurablePositionRef') -> None
```

Request resolution of one structural or durable position reference.

### `ResolvedAlternative`

```text
ResolvedAlternative(path: 'CanonicalPath', owner: 'ItemRef', relation: 'QualifiedName', index: 'int', value: 'object') -> None
```

Pair a path with one selection from a profile-ordered alternative set.

### `ResolvedItem`

```text
ResolvedItem(path: 'CanonicalPath', current: 'ItemRef') -> None
```

Pair the parsed path with its current structural item coordinate.

### `ResolvedPosition`

```text
ResolvedPosition(path: 'CanonicalPath', current: 'PositionRef') -> None
```

Pair the parsed path with its current structural boundary coordinate.

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
resolve_path(graph: 'Graph', profile: 'PathProfile', text: 'str', *, require: 'PathKind | None' = None) -> 'ResolvedItem | ResolvedPosition | ResolvedAlternative'
```

Parse, bind, kind-check, and resolve a profile-owned graph path.

## Profiles

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

Provenance is deliberately not interpreted or constrained by this profile.

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
stored roots must be a subset of the inferred set, so every declared root
is parentless. A curated ordered subset is allowed; use
:meth:`is_exhaustive` to require stored roots to equal the inferred set.

Reconciliation considers exactly the caller-supplied
``dependency_relations``. It checks stored roots against the roots inferred
over that enumerated set, but is silent about dependencies omitted from it;
enumeration is not enforcement.

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

A subset is sound because every declared root is parentless; exhaustive
consumers can use this check to require the complete inferred set.

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

### `SpanViewProfile`

```text
SpanViewProfile(base_tier: 'QualifiedName', span_tiers: 'tuple[QualifiedName, ...]', coverage_relation: 'QualifiedName', score_attribute: 'QualifiedName', value_attribute: 'QualifiedName', base_surface_attribute: 'QualifiedName', char_offset_attribute: 'QualifiedName | None' = None, alternative_relation: 'QualifiedName | None' = None) -> None
```

Name every graph declaration used to interpret a segmentation.

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

### `to_html`

```text
to_html(view: 'SpanView', *, alternatives: 'bool' = False) -> 'str'
```

Return a self-contained, injection-safe HTML segmentation report.

## References

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

### `DurablePositionRef`

```text
DurablePositionRef(anchor: 'DurableItemRef | QualifiedName', side: 'BoundarySide') -> None
```

Address a boundary whose identity is its anchor and chosen side.

Distinct anchors may resolve to the same boundary in the current graph and
diverge after an edit.  In particular, ``after(a)`` and ``before(b)`` keep
different intentions even when ``a`` and ``b`` are adjacent.  Likewise,
``before(tier)`` and ``after(tier)`` are distinct first-edge and last-edge
anchors that coincide only while the tier is empty.

#### `DurablePositionRef.to_data`

Method.

```text
DurablePositionRef.to_data(self) -> 'dict[str, JsonValue]'
```

Return the tagged anchor and side as JSON-serializable data.

### `ItemRef`

```text
ItemRef(tier: 'QualifiedName', index: 'int') -> None
```

Address an item by its current structural position.

#### `ItemRef.to_data`

Method.

```text
ItemRef.to_data(self) -> 'dict[str, JsonValue]'
```

Return the reference as JSON-serializable data.

### `PositionRef`

```text
PositionRef(tier: 'QualifiedName', index: 'int') -> None
```

Address a boundary owned by a tier, including both outer boundaries.

#### `PositionRef.to_data`

Method.

```text
PositionRef.to_data(self) -> 'dict[str, JsonValue]'
```

Return the position reference as JSON-serializable data.

## Selection

### `AttributeSelector`

```text
AttributeSelector(graph: 'Graph', attribute: 'QualifiedName', domain: 'AttributeDomain') -> None
```

Select nodes carrying one attribute on its declared domain.

#### `AttributeSelector.evaluate`

Method.

```text
AttributeSelector.evaluate(self) -> 'NodeSet'
```

Return owners that carry the named value without following relations.

### `BoundariesSelector`

```text
BoundariesSelector(graph: 'Graph', tier: 'QualifiedName') -> None
```

Select every boundary owned by one declared tier.

#### `BoundariesSelector.evaluate`

Method.

```text
BoundariesSelector.evaluate(self) -> 'NodeSet'
```

Return both outer boundaries and every boundary between items.

### `BoundarySelector`

```text
BoundarySelector(graph: 'Graph', reference: 'PositionRef | DurablePositionRef') -> None
```

Select one structural or anchored durable boundary reference.

#### `BoundarySelector.evaluate`

Method.

```text
BoundarySelector.evaluate(self) -> 'NodeSet'
```

Return the resolved boundary identity.

### `ItemSelector`

```text
ItemSelector(graph: 'Graph', reference: 'ItemRef | DurableItemRef') -> None
```

Select one structural or durable item reference.

#### `ItemSelector.evaluate`

Method.

```text
ItemSelector.evaluate(self) -> 'NodeSet'
```

Return the resolved item identity.

### `ItemsSelector`

```text
ItemsSelector(graph: 'Graph', tier: 'QualifiedName') -> None
```

Select all items owned by one declared tier.

#### `ItemsSelector.evaluate`

Method.

```text
ItemsSelector.evaluate(self) -> 'NodeSet'
```

Return the tier's items in coordinate order.

### `Node`

```text
Node(kind: 'NodeKind', reference: 'QualifiedName | ItemRef | PositionRef | int | None') -> None
```

Identify a node by its kind and its graph-local coordinate.

Item and boundary coordinates include their tier, declaration nodes use their
qualified name, and relation instances use their graph-local index.  The kind
is part of identity, so coordinates from unlike node classes never alias.

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

### `NodeSet`

```text
NodeSet(graph: 'Graph', nodes: 'tuple[Node, ...]') -> None
```

Hold unique nodes in the graph's canonical mixed-node order.

#### `NodeSet.to_data`

Method.

```text
NodeSet.to_data(self) -> 'list[JsonValue]'
```

Return the ordered set as strict-JSON data.

### `TierSelector`

```text
TierSelector(graph: 'Graph', tier: 'QualifiedName') -> None
```

Select one declared tier node.

#### `TierSelector.evaluate`

Method.

```text
TierSelector.evaluate(self) -> 'NodeSet'
```

Return the selected tier.

### `TypeSelector`

```text
TypeSelector(graph: 'Graph', item_type: 'QualifiedName') -> None
```

Select every item assigned one declared type by simple membership.

#### `TypeSelector.evaluate`

Method.

```text
TypeSelector.evaluate(self) -> 'NodeSet'
```

Return all items of the declared type.

### `select`

```text
select(graph: 'Graph', selectors: 'tuple[Selector, ...]') -> 'NodeSet'
```

Union validated selector routes into one canonical node set.

## Semirings

### `BOOLEAN`

The exact Boolean semiring, with disjunction and conjunction.

### `COUNTING`

The exact natural-number semiring.

### `DECIMAL_TROPICAL`

An exact min-plus or max-plus semiring with XSD-decimal finite values.

### `PATH`

The exact decimal tropical semiring enriched with tied best paths.

## Serialization

### `dump_bytes`

```text
dump_bytes(graph: 'Graph') -> 'bytes'
```

Encode the canonical document as UTF-8 bytes.

### `dump_compact`

```text
dump_compact(graph: 'Graph') -> 'str'
```

Return compact canonical JSON, including its final newline.

### `dumps`

```text
dumps(graph: 'Graph') -> 'str'
```

Return the sole canonical JSON spelling, including its final newline.

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

## Inspection

### `graph_summary`

```text
graph_summary(graph: 'Graph') -> 'dict[str, object]'
```

Return stable document counts and per-declaration graph summaries.

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

#### `OrderedPolyadicTraversal.stored_opposite`

Method.

```text
OrderedPolyadicTraversal.stored_opposite(self, instance_index: 'int') -> 'NodeSequence'
```

Return one instance's stored target-side sequence without inversion.

### `PolyadicSide`

```text
PolyadicSide(*values)
```

Choose one stored side of a polyadic relation declaration.

### `Walk`

```text
Walk(source: 'NodeSet', relation: 'QualifiedName', direction: 'WalkDirection', cap: 'int | None' = None) -> None
```

Declare a transitive walk along one bipartite relation.

A bounded walk stops after ``cap`` relation steps.  An unbounded walk is
admitted only when graph construction has validated the declaration's
acyclicity promise.  Forward access reads the stored relation and inverse
access computes its fiber over each selected item.  That fiber is a set:
deduplication is a consequence of relational inversion, not an accommodation
for any particular domain whose morphs happen to cross-cut.

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

### `WalkResult`

```text
WalkResult(nodes: 'NodeSet', truncated: 'bool', cap: 'int | None') -> None
```

Return reached nodes and disclose whether a step cap stopped the walk.

#### `WalkResult.to_data`

Method.

```text
WalkResult.to_data(self) -> 'dict[str, JsonValue]'
```

Return strict-JSON traversal data in canonical node order.

## Supported secondary surface

### `tiergraph.build`

This module is importable and usable, but carries no API-stability promise at version 0.1.0.

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
Document.add(self, value: 'RelationInstance | PolyadicRelationInstance | Position') -> 'None'
```

Add an already-constructed relation instance or sparse position value.

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

### `TROPICAL`

The inexact IEEE-double min-plus semiring.

### `BooleanSemiring`

```text
BooleanSemiring()
```

The exact Boolean semiring, with disjunction and conjunction.

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

### `ArcticSemiring`

```text
ArcticSemiring() -> 'None'
```

The inexact IEEE-double max-plus semiring.

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

### `LexicographicSemiring`

```text
LexicographicSemiring(first: 'Semiring[T]', second: 'Semiring[U]') -> 'None'
```

A selective first semiring with second-component aggregation on ties.

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

### `LawCheck`

```text
LawCheck(*values)
```

The mandatory comparison used to check a semiring law.

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

### `ProductSemiring`

```text
ProductSemiring(left: 'Semiring[T]', right: 'Semiring[U]') -> None
```

The componentwise product of two semirings.

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

### `TropicalSemiring`

```text
TropicalSemiring() -> 'None'
```

The inexact IEEE-double min-plus semiring.
### `tiergraph.schema`

This module is importable and usable, but carries no API-stability promise at version 0.1.0.

### `json_schema`

```text
json_schema(format_version: 'str') -> 'dict[str, JsonValue]'
```

Generate the JSON Schema document for one codec format version.

### `shape_hash`

```text
shape_hash() -> 'str'
```

Hash the declaration independently of JSON Schema presentation.
### `tiergraph.cli`

This module is importable and usable, but carries no API-stability promise at version 0.1.0.

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

This module is importable and usable, but carries no API-stability promise at version 0.1.0.

### `SpanViewProfile`

```text
SpanViewProfile(base_tier: 'QualifiedName', span_tiers: 'tuple[QualifiedName, ...]', coverage_relation: 'QualifiedName', score_attribute: 'QualifiedName', value_attribute: 'QualifiedName', base_surface_attribute: 'QualifiedName', char_offset_attribute: 'QualifiedName | None' = None, alternative_relation: 'QualifiedName | None' = None) -> None
```

Name every graph declaration used to interpret a segmentation.

### `SpanView`

```text
SpanView(text: 'str', spans: 'tuple[Span, ...]', base_surfaces: 'tuple[str, ...]') -> None
```

Hold reconstructed input text and its ordered, non-overlapping spans.

### `Span`

```text
Span(label: 'str', start: 'int', end: 'int', char_start: 'int | None', char_end: 'int | None', value: 'str | None', score: 'str | None', path: 'str', alternatives: 'tuple[SpanAlternative, ...]' = ()) -> None
```

Describe one selected span and its graph-derived extent.

### `SpanAlternative`

```text
SpanAlternative(value: 'str | None', score: 'str | None', path: 'str') -> None
```

Describe one ranked candidate associated with a selected span.

### `span_view`

```text
span_view(graph: 'Graph', profile: 'SpanViewProfile', *, alternatives: 'bool' = False) -> 'SpanView'
```

Read a segmentation and its coverage entirely through the public graph API.

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

### `to_html`

```text
to_html(view: 'SpanView', *, alternatives: 'bool' = False) -> 'str'
```

Return a self-contained, injection-safe HTML segmentation report.

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
is built from the item's durable id and attributes without querying clock
timing, so the default holds under a structural clock as well.

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
dumps(graph: 'Graph', *, clock: 'ClockProfile | None' = None, presentation: 'DotPresentation | None' = None, binding: 'Callable[..., tuple[ClockPosition, ClockPosition]] | None' = None, include_empty_tiers: 'bool' = False) -> 'str'
```

Return byte-stable DOT for ``graph``.

With ``clock``, the complete refined clock is the horizontal spine. Timed
tier boundaries align with that spine, event extents end at their bound
refined positions, and physical timing is included when the profile exposes
it. Explicitly untimed tiers are still drawn on their own structural axes.
Without ``clock``, every tier uses its own ordered structural boundaries.

Empty tiers are omitted by default and included when
``include_empty_tiers`` is true. Attribute names and values are rendered as
data; the renderer assigns no domain-specific meaning to them. A clock
profile must belong to this exact graph instance, not merely an equal graph,
because its cached derived state was computed from that instance.

A structural clock (built by :meth:`ClockProfile.from_position_values`)
selects the occupied-spine rendering: the clock tier is drawn only as the
spine, an occupied clock column is anchored on its item node, and empty
columns keep a guide point. ``binding`` places the non-clock items: when it
is supplied it MUST return, for every visible non-clock item, the
``(start, end)`` :class:`tiergraph.ClockPosition` pair naming the collapsed
columns the item occupies. There is no untimed lane, so returning ``None``
is refused with the offending item named. The kernel never parses domain
identifiers; the caller supplies the placement.

### `dumps_spans`

```text
dumps_spans(graph: 'Graph', profile: 'SpanViewProfile', *, alternatives: 'bool' = False, include_empty_tiers: 'bool' = False) -> 'str'
```

Return deterministic DOT focused on a segmentation and its span extents.
