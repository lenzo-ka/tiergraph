# JSON format

The wire representation is strict JSON: object keys are strings,
arrays retain order, and scalar attribute values retain their declared XSD type
and canonical lexical form. The top-level document carries
`"format_version": "0.3.0"`.

Structured literal concerns use a declared `"value_type": "json"` attribute.
Its record has `name`, `value_type`, and a required `value`, including null.
For example, `{"name":"form:profile","value_type":"json","value":{"empty":[],"start":null}}`.
The JSON literal retains empty containers, nulls, array order and primitive
kinds; object keys are sorted. Finite doubles retain their sign, including
negative zero. Namespace-shaped objects inside literals remain ordinary user
data. Graph references remain native relation endpoints, with their existing
declaration and reference validation. Literal strings carry no automatic
reference semantics. Scalar XSD records continue to use `lexical`.

Outside structured JSON attribute literals, empty arrays and null-valued fields
are not emitted, with one presence-sensitive
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

Order is significant in some collections and discarded in others, so an
implementation that preserves supply order throughout will not write canonical
bytes. Namespaces are sorted by namespace URI, relation and attribute
declarations by qualified name, attribute values by name, and sparse boundary
values by tier and index; seals, layers, the facts within each layer, and a
relation side's endpoint kinds and allowed tiers are sorted as well. Tier order,
tier item order, relation instance order, and polyadic endpoint order are
retained as supplied. Implementers should treat those retained orders, along
with qualified names and boundary indexes, as data. Validate all references
after decoding. Do not infer relation meaning from a name or compact ordered
relations into unordered sets.

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
release that wrote the document. `FORMAT_VERSION` holds it, and every release
writes that stamp until the format itself moves again.

It is not the writing package's version because versions are compared by string
equality: a reader built at one release would otherwise refuse a document
written by the next even when the two formats are identical, so every patch
release would break document reading. Repairing that would need compatibility
ranges, which is more machinery than a stamp that moves only with the format.

The form also says something a bare counter could not. A reader that refuses now
names the release to go and look at, instead of sending someone to a table to
find out what format `7` was.

Within a release line the format may only grow. A change that shrinks what an
existing document may say is legal, but it costs a step in the version position
that carries breaking changes: the minor while the major is zero, the major
after that. Because the reader matches the version exactly, any move of the
stamp is itself a break, so a reader refuses every document written under an
earlier stamp. The [changelog](../CHANGELOG.md) records each move and its
reason. The repository's gates compare the committed schema against the last
released one and replay a corpus of accepted documents through the current
decoder; [Contributing](../CONTRIBUTING.md) describes them.

Documents are versioned interchange: they move data between tools that agree on
a version. They are not an archival format, and reading a document written by a
later release is not supported.

## Program and patch JSON Lines

Construction programs use canonical JSON Lines. Version 2 adds seal, layer,
and fact construction records while retaining every version 1 spelling; the
reader continues to accept version 1 with exactly its original opcode set.
`Program` always starts from the empty graph and refuses removal operations.
`graph_to_program()` emits a construction-only version 2 program that replays
to exact graph equality, including orphan facts and explicit zero-length seal
records. This does not change the graph document `FORMAT_VERSION`.

Patches are a separate JSON Lines container with `"patch_version": "1"`.
Their header carries identified base and target fingerprints plus default edit
annotations. Each following line is one named operation with per-operation
annotations, identified base and target fingerprints, executable edit calls,
guarded residual document changes, and its executable recorded inverse. The
residual changes contain only changed members or array slices, not complete
before and after graph documents. Patch application checks the header base
before any operation, checks each operation's base, executes its calls and
residual changes, validates the resulting graph, checks the operation target,
and checks the final target. Current patches use identified fingerprints, so a
patch refuses a functionally equal base with different or reused durable ids.

`diff(source, target, view)` returns one of these executable patches. It is
deterministic, returns an empty patch when the inputs already agree in the
selected view, and otherwise aligns compatible ordered tiers under unit edit
costs. Reusable paired items outside the retained longest common subsequence
become moves. References are removed before affected items and restored
afterward, so every patch operation yields a valid graph. When declarations
are incompatible, the patch uses a guarded rebuild; a compatible edit may end
with a guarded whole-graph residue for exact state not expressed by the
semantic operations. Applying the patch produces a graph equivalent to
`target` in the selected view; per-tier unit-cost alignment is the only
minimality guarantee.

An empty diff is a no-op guarded to the source's identified fingerprint. When
the graphs agree only in the selected view, it is not an exact transition to
the target and cannot be composed as though its target fingerprint named that
graph.

This graph-level operation validates graph structure only. It has no clock
profile and does not apply or report a clock rebinding policy. Construct edits
through `ClockProfile.edit()` when such a policy must govern structural
changes.

Both containers share the graph codec's whole-document byte limit, UTF-8 and
strict-JSON rules, nesting limit, and the independent 1 MiB JSONL line limit.
Annotations contain optional `author`, `reason`, `stage`, `confidence`,
`iteration`, `tool`, and caller-supplied `timestamp` fields plus typed JSON
`fields`; no timestamp is invented. Qualified names in program opcodes remain
expanded rather than depending on a document prefix table.

## Refusal order

An input routinely breaks several rules at once. A document reader ranks the
conditions it can meet by one numbered order, `RefusalStage`, so the condition
reported first is the one that explains the rest rather than whichever check
happened to run first:

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

The order ranks applicable conditions. A condition that is inapplicable until a lower-ranked condition has passed is reported when it becomes applicable.

Which readers observe this order. `loads` observes it in full. `selection_loads`
and `load_program` observe it for every condition their inputs can raise: neither
is handed a graph, so `REFERENCE` — and for `selection_loads` `SEMANTICS` as well
— is settled when the selector is evaluated or the program is executed rather
than when the document is read, and no rank the read could have reported is
skipped. `grammar_loads` observes it only for the conditions it shares with the
other readers: its envelope, encoding and syntax conditions come from the common
parse, and its construction and shape conditions are staged. Its four
grammar-specific conditions are not — an unimplemented discriminator, a value
outside the declared language, an undeclared nonterminal, and the declaration
contract, ranks 5, 7, 8 and 9, are answered with a bare `ValueError` carrying no
stage. A caller routing on `RefusalStage` should not expect one from
`grammar_loads` for those four.

The stages rank the conditions of one node. Nodes are read from the outside in
and members in their declared order, so an enclosing node's condition precedes
its members' whatever their stages; the pair of a node and a stage totally
orders every condition a read can meet.

The command line reads its own declarative profiles through this order too:
`clock --profile` and `span render --profile` take the envelope, encoding, and
syntax conditions from the same reader a graph takes them from, so the same
bytes are answered at the same rank and in the same wording whichever of the
two they arrive as. One input is left outside: `grammar --tokens-json` decodes
an inline argument with the JSON module directly, and a malformed value there
is refused with the underlying decoder's message and no stage. This order
therefore governs the documents this package reads rather than every JSON it
parses.

A further condition is reported beside the primary one only while it stays
applicable once the primary is known. A document announcing a format this
release does not implement is refused for its version alone, because the field
set of a declaration the document never selected cannot be judged. The
one condition currently carried beside another is the field set of a single
node: a node both missing required fields and carrying unknown ones has the two
named in one message and the unknown-field half repeated on `also` as a refusal
in its own right, so a consumer learns the whole difference at that node from
one attempt. Conditions at two different nodes are two reads, and a caller
repairs them one at a time.

The stage is the stable part of a refusal and the wording is diagnostic.
`tiergraph.Refusal` carries the stage as `stage` and any further applicable
conditions as `also`, each a refusal in its own right.

`Refusal` is the one base every stage of this order arrives under, so `except
Refusal` catches the whole order rather than a prefix of it. The last rank is
raised by the graph constructor as `GraphValidationError`, which is a `Refusal`
and reports its stage through the same two fields; a subclass says which channel
refused, never which ranks a caller has to expect. A `Refusal` is a
`ValueError`, so callers that already catch one still do.
