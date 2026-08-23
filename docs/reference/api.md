# API reference

This page is generated from the shipped objects and the documentation manifest.
It covers 121 top-level `tiergraph` exports exactly once.

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

### `AsBuilt`

```text
AsBuilt(graph: 'Graph', trace: 'tuple[PrimitiveOpcode, ...]') -> None
```

Pair a checked graph with its finite primitive consume-tier trace.

### `AttachValue`

```text
AttachValue(domain: 'AttributeDomain', target: 'AttributeTarget', value: 'AttributeValue') -> None
```

Attach a typed value to an owner in its declared attribute domain.

### `DeclareAttribute`

```text
DeclareAttribute(declaration: 'AttributeDeclaration') -> None
```

Declare one typed attribute and its attachment domain.

### `DeclareNamespace`

```text
DeclareNamespace(declaration: 'NamespaceDeclaration') -> None
```

Declare one namespace binding.

### `DeclareRelation`

```text
DeclareRelation(declaration: 'RelationDeclaration') -> None
```

Declare a simple membership or bipartite relation.

### `DeclareTier`

```text
DeclareTier(declaration: 'TierDeclaration') -> None
```

Declare one empty ordered tier.

### `ExecutionError`

Name the opcode that could not make its checked state transition.

### `Program`

```text
Program(opcodes: 'tuple[Opcode, ...]') -> None
```

Carry source opcodes while defining identity on their checked outcome.

### `PromoteItem`

```text
PromoteItem(reference: 'ItemRef', durable_id: 'str') -> None
```

Promote one structural item reference to durable identity.

### `PromotePosition`

```text
PromotePosition(reference: 'PositionRef', durable_id: 'str') -> None
```

Promote one structural boundary reference to anchored identity.

### `Relate`

```text
Relate(relation: 'RelationInstance | PolyadicRelationInstance') -> None
```

Add one instance of a declared bipartite or polyadic relation.

### `Repeat`

```text
Repeat(count: 'int', body: 'tuple[Opcode, ...]') -> None
```

Repeat a finite block without adding a primitive consume-tier opcode.

### `Step`

```text
Step(index: 'int', opcode: 'PrimitiveOpcode', graph: 'Graph') -> None
```

Record one primitive opcode and its validated resulting graph.

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

### `FoldHomomorphism`

```text
FoldHomomorphism(name: 'str', source: 'FoldDeclaration[Value]', target: 'FoldDeclaration[OtherValue]', mapping: 'Callable[[Value], OtherValue]') -> None
```

Declare a carrier map whose fold result must commute.

### `FoldResult`

```text
FoldResult(values: 'tuple[tuple[State, Value], ...]', roots: 'tuple[State, ...]', value: 'Value', provenance: 'Provenance | None', truncated: 'bool', cost: 'FoldCost', ranked_witnesses: 'tuple[RankedWitness[Value], ...] | None' = None) -> None
```

Keep semiring values, witness provenance, and measured work separate.

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

### `GrammarChartProfile`

```text
GrammarChartProfile(forest: 'ParseForest') -> None
```

Address chart alternatives in a stable order within one forest snapshot.

The profile vocabulary is
``/chart/NONTERMINAL/START/END/alternatives/INDEX``. Alternative indices are
independent of rule weights, but intentionally are not stable across forest
snapshots whose sets of alternatives differ.

### `GrammarDeclaration`

```text
GrammarDeclaration(nonterminals: 'tuple[QualifiedName, ...]', start: 'QualifiedName', rules: 'tuple[GrammarRule, ...]') -> None
```

Hold a validated synchronous grammar with fixed source and target roles.

### `GrammarHole`

```text
GrammarHole(variable: 'AttributeValue', nonterminal: 'QualifiedName') -> None
```

Bind one named pattern variable to a declared nonterminal.

### `GrammarRule`

```text
GrammarRule(left: 'QualifiedName', source: 'GrammarPattern', target: 'GrammarPattern', boundary: 'AttributeValue' = AttributeValue(name=QualifiedName(namespace='urn:tiergraph:grammar', local_name='boundary'), value_type=<XsdType.STRING: 'string'>, lexical='complete'), awaited_variables: 'tuple[AttributeValue, ...]' = (), weight: 'AttributeValue | None' = None) -> None
```

Declare one directional pairing of source and target patterns.

### `GrammarTerminal`

```text
GrammarTerminal(text: 'AttributeValue') -> None
```

Carry one source or target terminal as a canonical XSD string value.

### `LoweredGrammar`

```text
LoweredGrammar(declaration: 'GrammarDeclaration', program: 'Program', as_built: 'AsBuilt') -> None
```

Pair a grammar with its replayable coordinate-hedge construction.

### `ParseForest`

```text
ParseForest(graph: 'Graph', program: 'Program', root: 'ItemRef', fold: 'FoldDeclaration[bool]', declaration: 'GrammarDeclaration') -> None
```

Carry a machine-built parse forest and its Boolean interpretation.

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

### `BipartiteRelationDeclaration`

```text
BipartiteRelationDeclaration(name: 'QualifiedName', left_type: 'QualifiedName', right_type: 'QualifiedName', left_endpoint: 'RelationEndpointKind' = <RelationEndpointKind.ITEM: 'item'>, right_endpoint: 'RelationEndpointKind' = <RelationEndpointKind.ITEM: 'item'>, single_parent: 'bool' = False, acyclic: 'bool' = False, attributes: 'tuple[AttributeValue, ...]' = ()) -> None
```

Declare typed links and the graph invariants they promise.

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

### `Item`

```text
Item(durable_id: 'str | None' = None, attributes: 'tuple[AttributeValue, ...]' = ()) -> None
```

Represent a tier member with attributes and a durable identifier seam.

### `NamespaceDeclaration`

```text
NamespaceDeclaration(prefix: 'str', namespace: 'str') -> None
```

Bind a document-local prefix to one namespace URI.

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

### `PolyadicRelationInstance`

```text
PolyadicRelationInstance(declaration: 'QualifiedName', sources: 'tuple[RelationEndpointRef, ...]', targets: 'tuple[RelationEndpointRef, ...]', durable_id: 'str | None' = None, attributes: 'tuple[AttributeValue, ...]' = ()) -> None
```

Link two declared, ordered endpoint sequences.

### `Position`

```text
Position(reference: 'PositionRef | DurablePositionRef', attributes: 'tuple[AttributeValue, ...]') -> None
```

Hold values for one addressable boundary while empty boundaries stay derived.

### `QualifiedName`

```text
QualifiedName(namespace: 'str', local_name: 'str') -> None
```

Identify a declaration by namespace URI and local name.

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

### `RelationSideDeclaration`

```text
RelationSideDeclaration(endpoint_kinds: 'tuple[RelationEndpointKind, ...]', tiers: 'tuple[QualifiedName, ...] | None' = None, minimum: 'int' = 1, maximum: 'int | None' = None, allow_empty: 'bool' = False) -> None
```

Constrain one explicitly ordered side of a polyadic relation.

### `SimpleRelationDeclaration`

```text
SimpleRelationDeclaration(name: 'QualifiedName', tier: 'QualifiedName', item_type: 'QualifiedName', attributes: 'tuple[AttributeValue, ...]' = ()) -> None
```

Give every member of one tier its type through a depth-one relation.

### `Tier`

```text
Tier(declaration: 'TierDeclaration', items: 'tuple[Item, ...]' = (), attributes: 'tuple[AttributeValue, ...]' = ()) -> None
```

Pair a declaration with immutable ordered members and tier attributes.

### `TierDeclaration`

```text
TierDeclaration(name: 'QualifiedName', long_name: 'str') -> None
```

Name an ordered tier without coupling its name to item identity.

### `XsdType`

```text
XsdType(*values)
```

The growable XSD datatype subset admitted for attribute values.

## Metadata

### `FORMAT_VERSION`

Version tag written by the JSON wire codec. Current value: `5`.

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

### `PersistedChoiceProfile`

```text
PersistedChoiceProfile(graph: 'Graph', alternatives_relation: 'QualifiedName', default_relation: 'QualifiedName') -> None
```

Read alternatives and optional persisted singleton defaults by source.

### `json_value_graph`

```text
json_value_graph(value: 'JsonValue', namespace: 'str' = 'urn:tiergraph:json-value') -> 'tuple[Graph, JsonValueProfile, ItemRef]'
```

Construct a standalone canonical graph for one recursively nested JSON value.

## References

### `DurableItemRef`

```text
DurableItemRef(durable_id: 'str') -> None
```

Address an item by a durable identifier without a coordinate fallback.

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

### `ItemRef`

```text
ItemRef(tier: 'QualifiedName', index: 'int') -> None
```

Address an item by its current structural position.

### `PositionRef`

```text
PositionRef(tier: 'QualifiedName', index: 'int') -> None
```

Address a boundary owned by a tier, including both outer boundaries.

## Selection

### `AttributeSelector`

```text
AttributeSelector(graph: 'Graph', attribute: 'QualifiedName', domain: 'AttributeDomain') -> None
```

Select nodes carrying one attribute on its declared domain.

### `BoundariesSelector`

```text
BoundariesSelector(graph: 'Graph', tier: 'QualifiedName') -> None
```

Select every boundary owned by one declared tier.

### `BoundarySelector`

```text
BoundarySelector(graph: 'Graph', reference: 'PositionRef | DurablePositionRef') -> None
```

Select one structural or anchored durable boundary reference.

### `ItemSelector`

```text
ItemSelector(graph: 'Graph', reference: 'ItemRef | DurableItemRef') -> None
```

Select one structural or durable item reference.

### `ItemsSelector`

```text
ItemsSelector(graph: 'Graph', tier: 'QualifiedName') -> None
```

Select all items owned by one declared tier.

### `Node`

```text
Node(kind: 'NodeKind', reference: 'QualifiedName | ItemRef | PositionRef | int | None') -> None
```

Identify a node by its kind and its graph-local coordinate.

Item and boundary coordinates include their tier, declaration nodes use their
qualified name, and relation instances use their graph-local index.  The kind
is part of identity, so coordinates from unlike node classes never alias.

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

### `TierSelector`

```text
TierSelector(graph: 'Graph', tier: 'QualifiedName') -> None
```

Select one declared tier node.

### `TypeSelector`

```text
TypeSelector(graph: 'Graph', item_type: 'QualifiedName') -> None
```

Select every item assigned one declared type by simple membership.

### `select`

```text
select(graph: 'Graph', selectors: 'tuple[Selector, ...]') -> 'NodeSet'
```

Union validated selector routes into one canonical node set.

## Serialization

### `dump_bytes`

```text
dump_bytes(graph: 'Graph') -> 'bytes'
```

Encode the canonical document as UTF-8 bytes.

### `dumps`

```text
dumps(graph: 'Graph') -> 'str'
```

Return the sole canonical JSON spelling, including its final newline.

### `loads`

```text
loads(document: 'str | bytes') -> 'Graph'
```

Parse the current format without implicitly migrating older documents.

Migration is refused because choosing a loss-aware conversion belongs in an
explicit version-to-version tool, not in the primitive codec.

### `to_data`

```text
to_data(graph: 'Graph') -> 'dict[str, JsonValue]'
```

Return the versioned primitive document as strict JSON data.

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

### `OrderedContainment`

```text
OrderedContainment(graph: 'Graph', relation: 'QualifiedName') -> None
```

Traverse one ordered, item-only polyadic containment relation.

Descending order is exactly stored target incidence order. Descendants are
depth-first pre-order and leaves are depth-first leaf order; repeated
incidence remains repeated. Parents and ancestors are computed inverse
fibers, so their result is intentionally a :class:`NodeSet`.

### `OrderedPolyadicTraversal`

```text
OrderedPolyadicTraversal(graph: 'Graph', relation: 'QualifiedName', source_side: 'PolyadicSide', target_side: 'PolyadicSide') -> None
```

Traverse between either pair of sides of one ordered polyadic relation.

Direct and transitive results retain instance order, opposite-side endpoint
order, and repetition.  Relational inversion is set-valued; callers that
need stored order can instead request the opposite sequence of one instance.

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

## Supported secondary surface

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

### `DecimalExtremumSemiring`

```text
DecimalExtremumSemiring(*, minimum: 'bool') -> 'None'
```

An exact min-plus or max-plus semiring with XSD-decimal finite values.

### `DoubleExtremumSemiring`

```text
DoubleExtremumSemiring(*, minimum: 'bool') -> 'None'
```

An inexact min-plus or max-plus semiring over finite IEEE doubles.

### `ExpectationSemiring`

```text
ExpectationSemiring(base: 'Semiring[T]') -> 'None'
```

The expectation construction ``(weight, weighted statistic)``.

### `LexicographicSemiring`

```text
LexicographicSemiring(first: 'Semiring[T]', second: 'Semiring[U]') -> 'None'
```

A selective first semiring with second-component aggregation on ties.

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

### `Semiring`

```text
Semiring(*args, **kwargs)
```

Operations, carrier boundary, encoding, and declared algebraic laws.

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
