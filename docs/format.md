# JSON format

The version 6 wire representation is strict JSON: object keys are strings,
arrays retain order, and scalar attribute values retain their declared XSD type
and canonical lexical form. The top-level document carries
`"format_version": "6"`.

Empty arrays and null-valued fields are not emitted, with one presence-sensitive
exception: a relation side's explicit `"tiers": []` means that no tier is
allowed, while an absent `tiers` field means that any tier is allowed. Other
missing array fields decode as empty collections, and missing nullable strings
decode as absent values. Non-empty collections and non-null values retain their
existing object fields and ordering; relation instances remain objects in the
single `relations` collection and are not grouped under declarations.

Qualified names are strings in the form `prefix:local`. The prefix is expanded
through the document's `namespaces` bindings, which are mandatory whenever a
qualified name uses them (the empty table itself is omitted). A namespace prefix
must not contain `:`. A local name may contain any number of colons: decoders
split on the first colon only, so `score:section:voice` uses prefix `score` and
local name `section:voice`. Empty prefixes, empty local names, unknown prefixes,
and colon-bearing namespace prefixes are refused.

This spelling means an isolated fragment containing `score:event` is not
self-describing: a streaming consumer cannot recover its namespace URI without
the document's namespace table. That trade-off is accepted because namespace
bindings are mandatory for qualified names and the complete graph is validated
after decode.

Implementers should treat qualified names, declaration order, tier order, item
order, relation endpoint order, and boundary indexes as data. Validate all
references after decoding. Do not infer relation meaning from a name or compact
ordered relations into unordered sets.

The generated schema in `schema/tiergraph.schema.json` describes structural
shape. The Python decoder remains the authority for semantic constraints such
as declaration compatibility, acyclicity, and reference validity. A format
version decision accompanies changes to the generated schema or declaration
shape.

The CLI's `convert --to bytes` target uses the same canonical JSON byte API. It
is not a distinct compact wire spelling; `json-compact` is only a presentation
variant accepted and normalized by the decoder.

Durable ids are canonical as-built content, not metadata. Promoting an item or
an interior boundary therefore changes the canonical bytes and their SHA-256
fingerprint. Ignoring durable identity would make fingerprints erase the very
identifier consumers use to address an item across graphs.

## Format versions

A tiergraph document declares the format version it was written in. A reader
accepts documents of the version it implements and refuses any other, naming
the version it found and the one it expected.

Documents are versioned interchange: they move data between tools that agree on
a version. They are not an archival format, and reading a document written by a
later release is not supported.

## Refusal order

An input routinely breaks several rules at once. Every reader in this package
ranks the conditions it can meet by one numbered order, `RefusalStage`, so the
condition reported first is the one that explains the rest rather than whichever
check happened to run first:

1. `ENVELOPE` — a byte or line limit the reader enforces before interpreting the
   input at all.
2. `ENCODING` — the bytes are text, and the text is one the encoder can write.
3. `SYNTAX` — the text is JSON, nested no deeper than the limit, with no
   repeated object key.
4. `CONSTRUCTION` — this node is the JSON construction its declaration names.
5. `DISCRIMINATOR` — the member that selects which declaration applies is
   present, readable, and names one this release implements. `format_version`
   and a program's `machine_version` are discriminators, and so are a relation's
   `kind`, an opcode's name, and a selector's `op` or `select`.
6. `SHAPE` — this node's field set is the selected declaration's, naming every
   missing and every unknown member at once.
7. `VALUE` — this node's own value lies in the declared language: an enumerated
   spelling, a lexical pattern, a bound.
8. `REFERENCE` — a name this node carries resolves inside the document.
9. `SEMANTICS` — a promise spanning more than one node holds.

The stages rank the conditions of one node. Nodes are read from the outside in
and members in their declared order, so an enclosing node's condition precedes
its members' whatever their stages; the pair of a node and a stage totally
orders every condition a read can meet.

A further condition is reported beside the primary one only while it stays
applicable once the primary is known. A document announcing a format this
release does not implement is refused for its version alone, because the field
set of a declaration the document never selected cannot honestly be judged. A
document whose version this release does implement reports every structural
condition it meets, so a caller repairs it in one pass rather than once per
problem.

The stage is the stable part of a refusal and the wording is diagnostic.
`tiergraph.schema.Refusal` carries the stage as `stage` and any further
applicable conditions as `also`, each a refusal in its own right. A `Refusal` is
a `ValueError`, so callers that already catch one still do.
