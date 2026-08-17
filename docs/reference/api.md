# API reference

This page is generated from the shipped objects and the documentation manifest.
It covers 84 top-level `tiergraph` exports exactly once.

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
ClockProfile(graph: 'Graph', clock_tier: 'QualifiedName', binding_relation: 'QualifiedName', rate_attribute: 'QualifiedName | None', unit_attribute: 'QualifiedName', tick_attribute: 'QualifiedName | None' = None, gap_attribute: 'QualifiedName | None' = None, untimed_attribute: 'QualifiedName | None' = None, start_attribute: 'QualifiedName | None' = None, duration_attribute: 'QualifiedName | None' = None) -> None
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
Relate(relation: 'RelationInstance') -> None
```

Add one instance of a declared bipartite relation.

### `Repeat`

```text
Repeat(count: 'int', body: 'tuple[Opcode, ...]') -> None
```

Repeat a finite block without adding a primitive consume-tier opcode.

### `execute`

```text
execute(opcodes: 'Iterable[object]') -> 'Graph'
```

Execute primitives in order and name the first refused opcode.

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
FoldCost(document_size: 'int', relation_incidence: 'int', index_product_size: 'int', carrier_additions: 'int', carrier_multiplications: 'int', carrier_operation_cost: 'int', witness_count: 'int', emitted_count: 'int', output_cap: 'int') -> None
```

Report measured structural quantities and carrier work for one run.

### `FoldDeclaration`

```text
FoldDeclaration(name: 'str', graph: 'Graph', valuation: 'AttributeValuation', semiring: 'Semiring[Value]', lift: 'Lift[Value]', transitions: 'tuple[FoldTransition, ...]', index_axes: 'tuple[tuple[str, ...], ...]' = (), roots: 'tuple[ItemRef, ...]' = (), witness_order: 'WitnessOrder[Value] | None' = None, tie_policy: 'TiePolicy | None' = None, output_cap: 'int' = 1, carrier_operation_cost: 'int' = 1) -> None
```

Bind one named interpretation to a graph, valuation, algebra, and finite DAG.

### `FoldHomomorphism`

```text
FoldHomomorphism(name: 'str', source: 'FoldDeclaration[Value]', target: 'FoldDeclaration[OtherValue]', mapping: 'Callable[[Value], OtherValue]') -> None
```

Declare a carrier map whose fold result must commute.

### `FoldResult`

```text
FoldResult(values: 'tuple[tuple[State, Value], ...]', roots: 'tuple[State, ...]', value: 'Value', provenance: 'Provenance | None', truncated: 'bool', cost: 'FoldCost') -> None
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

### `MAX_REPEAT_COUNT`

Largest repeat count accepted by the build machine. Current value: `10000`.

### `__version__`

Installed distribution version. Current value: `0.0.0`.

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
information, but stored membership may not contradict that derived set.

Reconciliation considers exactly the caller-supplied
``dependency_relations``. It checks that stored roots equal the roots
inferred over that enumerated set, but is silent about dependencies omitted
from it; enumeration is not enforcement. If the set is empty, every item in
the admitted domain is inferred as a root, so a curated ordered subset is
refused.

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

This module is importable and usable, but carries no API-stability promise at version 0.0.0.

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

This module is importable and usable, but carries no API-stability promise at version 0.0.0.

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

## Companion package

### `dumps`

```text
dumps(graph: 'Graph', *, clock: 'ClockProfile | None' = None, include_empty_tiers: 'bool' = False) -> 'str'
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
