# JSON format

The wire representation is strict JSON: object keys are strings,
arrays retain order, and scalar attribute values retain their declared XSD type
and canonical lexical form. The top-level document carries
`"format_version": "0.2.0"`.

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

The version names **the release at which the format last changed**, not the
release that wrote the document. This release stamps `"0.2.0"`, and `0.2.1` and
`0.3.0` will keep stamping `"0.2.0"` until the format itself moves again.

It is worth being clear about why it is not simply the writing package's version,
which is the obvious reading. Versions are compared by string equality, so a
reader built at `0.2.0` would refuse a document written by `0.2.1` even though
the two formats are identical -- every patch release would break document
reading. Repairing that needs compatibility ranges, which is more machinery than
the plain counter it replaced rather than less. The rare thing must not inherit
the frequent thing's cadence.

The form also says something a bare counter could not. A reader that refuses now
names the release to go and look at, instead of sending someone to a table to
find out what format `7` was.

Within a release line the format may only grow: a change that shrinks what an
existing document may say is legal, but it costs the version position that
carries breaking changes -- the minor while the major is zero, the major after
that. This release spent one, moving to `0.2.0` to drop `position` from the
attribute-domain vocabulary in favor of `boundary`.

Documents are versioned interchange: they move data between tools that agree on
a version. They are not an archival format, and reading a document written by a
later release is not supported.

## Refusal order

An input routinely breaks several rules at once. Every document reader this
package exposes — `loads`, `grammar_loads`, `selection_loads`, and
`load_program` — ranks the conditions it can meet by one numbered order,
`RefusalStage`, so the condition reported first is the one that explains the
rest rather than whichever check happened to run first:

1. `ENVELOPE` — a byte or line limit the reader enforces before interpreting the
   input at all.
2. `ENCODING` — the bytes are text, and the text is one the encoder can write.
3. `SYNTAX` — the text is JSON, nested no deeper than the limit, with no
   repeated object key.
4. `CONSTRUCTION` — this node is the JSON construction its declaration names.
5. `DISCRIMINATOR` — the member that selects which declaration applies names one
   this release implements. `format_version` and a program's `machine_version`
   are discriminators, and so are a relation's `kind`, an opcode's name, and a
   selector's `op` or `select`. A discriminator that selects one node's
   declaration — a relation's `kind`, an opcode's name — reaches this rank only
   once it spells something: absent or not a string, it is a construction
   condition reported at rank 4, because there is no spelling yet to judge
   against the implemented set. A discriminator that selects the whole read —
   `format_version`, `machine_version`, a selector's `op` or `select` — is
   reported here whether it is absent, unreadable, or unimplemented, because
   nothing else about the input can be ranked until it is settled.
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

The command line decodes some of its own inputs outside those readers. `clock
--profile` and `span render --profile` read a profile file with the JSON module
directly, and `grammar --tokens-json` decodes an inline argument the same way. A
malformed value at any of the three is refused with the underlying decoder's
message and no stage, so this order governs the document readers rather than
every JSON this package parses.

A further condition is reported beside the primary one only while it stays
applicable once the primary is known. A document announcing a format this
release does not implement is refused for its version alone, because the field
set of a declaration the document never selected cannot honestly be judged. The
one condition currently carried beside another is the field set of a single
node: a node both missing required fields and carrying unknown ones has the two
named in one message and the unknown-field half repeated on `also` as a refusal
in its own right, so a consumer learns the whole difference at that node from
one attempt. Conditions at two different nodes are two reads, and a caller
repairs them one at a time.

The stage is the stable part of a refusal and the wording is diagnostic.
`tiergraph.schema.Refusal` carries the stage as `stage` and any further
applicable conditions as `also`, each a refusal in its own right. A `Refusal` is
a `ValueError`, so callers that already catch one still do.
