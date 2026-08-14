# Schema/codec conformance

`tests/conformance/schema_codec.py` walks the live `Shape` declaration over
codec-produced documents that realize every reference variant.  At every reached
object, array, and scalar it constructs deterministic near-misses: missing and
unknown keys, wrong JSON types, empty strings, every enum spelling plus an outside
spelling, integer boundaries (`-2`, `-1`, `0`, `1`, `2`, declared minima and the
value below), zero-fraction numbers, and valid and invalid edges of each declared
lexical pattern.  A newly added field is populated from its declaration, then
mutated, so field coverage does not require a hand-written case.

The generated Draft 2020-12 schema, `schema.validation_errors`, and `wire.loads`
receive each document. Opposite acceptance is drift in any direction. Any
exception raised by `validation_errors`, or by the codec other than its public
`ValueError` refusal, is also drift. Intentional schema-accepted, codec-refused
families and the structural validator's expected decisions are data in
`tests/conformance/declared_schema_codec_divergences.py`; adding one does not
change the harness.

The harness reaches primitive syntax represented by `Shape` and realized union
variants.  It does not construct arbitrary pairs or sequences needed to explore
all graph-wide semantic failures (duplicates, cycles, parent conflicts, or every
referential-integrity topology).  Those remain codec laws, and their declared
families are listed in the same divergence policy.  It also cannot close JSON
Schema's definition of `integer` over zero-fraction JSON numbers such as `1.0`.

On the reference development machine the eight conformance tests take about 4
seconds as one pytest run.  `make check` runs pytest once with coverage and three
more times under fixed hash seeds, so their approximate incremental cost is 16
seconds.  The construction and diagnostic ordering use declaration order and
explicit sorting, never set iteration order.

The checked claim is therefore: for every declaration-derived mutation around
the accepted witnesses, schema, structural Python validation, and codec
acceptance agree except for a named machine-readable divergence. It is not a
proof that schema validation is sufficient for codec acceptance for arbitrary
graphs, nor a proof of all graph-wide semantic invariants.
