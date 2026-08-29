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
