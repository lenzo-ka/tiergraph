# Schema/codec conformance

`tests/conformance/schema_codec.py` walks the live `Shape` declaration over
codec-produced documents that realize every reference variant.  At every reached
object, array, and scalar it constructs deterministic near-misses: missing and
unknown keys, wrong JSON types, empty strings, every enum spelling plus an outside
spelling, integer boundaries (`-2`, `-1`, `0`, `1`, `2`, declared minima and the
value below), zero-fraction numbers, and valid and invalid edges of each declared
lexical pattern.  A newly added field is populated from its declaration, then
mutated, so field coverage does not require a hand-written case.

The generated Draft 2020-12 schema and `wire.loads` are two acceptance oracles.
They are independent on value, type, and enum semantics, where the codec
hand-writes its checks, but share the `Shape` declarations that determine field
structure. Consequently, agreement on missing and unknown fields is by
construction rather than an independent cross-check. `schema.validation_errors`
receives the same document as a
third surface, but it shares the schema declaration and validation machinery; it
agrees by construction rather than independently confirming the other two. Its
value here is that a change cannot make it silently diverge unnoticed. Opposite
acceptance is drift in any direction. Any
exception raised by `validation_errors`, or by the codec other than its public
`ValueError` refusal, is also drift. Intentional schema-accepted, codec-refused
families reached by the generated probes, and the structural validator's expected
decisions, are live subtraction rules in
`tests/conformance/declared_schema_codec_divergences.py`; adding one does not
change the harness.

The harness reaches primitive syntax represented by `Shape` and realized union
variants.  It does not construct arbitrary pairs or sequences needed to explore
all graph-wide semantic failures. Codec-only laws include referential integrity;
unique declaration, tier, item, durable-id, position, and relation names; endpoint
typing; `single_parent` and `acyclic`; coherent position cardinality; one prefix
per namespace URI; one simple relation per tier; relation-instance declaration
kind; non-empty positioned values; and the remaining polyadic cross-instance
promises. These are design constraints, not blanket runtime suppressions.

The generated probes reach local examples of referential integrity, endpoint
typing, and position-cardinality disagreement, so those causes have live rules as
well as being codec-only laws. They do not generate the paired or sequenced
documents needed to exercise name and identity uniqueness, duplicate namespace
URIs, relation-graph promises, or empty positioned-value attribute collections.
Those four families are explicit coverage gaps, not policy entries. The harness
also cannot close JSON Schema's definition of `integer` over zero-fraction JSON
numbers such as `1.0`.

On the reference development machine the conformance tests take about 4
seconds as one pytest run.  `make check` runs pytest once with coverage and three
more times under fixed hash seeds, so their approximate incremental cost is 16
seconds.  The construction and diagnostic ordering use declaration order and
explicit sorting, never set iteration order.

The checked claim is therefore: for every declaration-derived mutation around
the accepted witnesses, the schema and codec oracles agree except for a live
divergence. Each policy entry matches at least one observed drift, and removing
it exposes at least one drift that it matched; the audit does not prove that its
pattern is maximally tight. The oracles independently cross-check value, type,
and enum semantics, share field structure, and the shared structural Python
validation surface cannot differ from them unnoticed. This is not three-way
independent triangulation. It is not a proof that schema validation is
sufficient for codec acceptance for arbitrary graphs, nor a proof of all
graph-wide semantic invariants.
