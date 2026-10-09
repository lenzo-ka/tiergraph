# External resources

A graph can identify and attach a large or non-JSON resource without putting
its bytes in the graph document. The resource may be audio, video, a structured
transcript, a key-value document, another tiergraph graph, or a domain-specific
format. Its descriptor is an ordinary item, and its attachment is an ordinary
bipartite relation. This keeps editing, differences, patches, layers, and graph
fingerprints on the existing graph model.

The payload itself is identified by a lowercase SHA-256 digest and an exact
byte count. The descriptor also gives a lowercase media type and an absolute
schema URI. Media type and schema describe how a domain may interpret the
payload; the core validates their syntax but does not interpret either one.

## Declare descriptors and attachments

Call `declare_blob_vocabulary(editor)` once on the graph editor. It declares
the `blob` prefix and the fixed item and relation-instance attributes. A blob
item carries `blob:sha256`, `blob:size`, `blob:media-type`, and `blob:schema`.
Optional `blob:rate`, `blob:origin`, and `blob:extent` values describe linear
intrinsic coordinates. A nonempty `blob:unit` is required when an attachment
carries a linear span.

Every blob item needs a durable item ID. Define any blob tier and item type the
domain needs, then define bipartite attachment relations whose right endpoint
has that item type. Every attachment needs a durable relation-instance ID. The
relation declaration is the role: separate relations can mean recording,
transcript, metadata, or any other domain concept. Subjects are unrestricted,
so an attachment can run from an ordinary item to a blob item or from one blob
item to another.

Order and identity are separate. Blob items keep tier and item order;
attachments keep relation-instance order. Reordering changes graph content but
does not change durable IDs. Replacing or re-encoding a payload changes its
digest and size but need not change the blob item's durable ID. Two blob items
may name the same payload while carrying different media types, schemas, roles,
or metadata.

`BlobProfile(graph)` validates the complete fixed vocabulary without opening a
payload. `blobs()` returns blob items in declared tier and item order,
`attachments(blob)` returns attachments in declared relation order, and
`required()` returns distinct payload identities in first blob-item order. The
profile refuses malformed descriptors, conflicting sizes for one digest,
non-durable blob items or attachments, and spans attached to a non-blob target.
Run the same check from the command line with `tiergraph validate --profile
blob graph.json`.

## Attachments and spans

An attachment may carry `blob:length` and an optional `blob:offset`. These are
intrinsic linear coordinates, not necessarily time. They may count characters,
samples, frames, or another resource-defined unit. The blob item's nonempty
`blob:unit` names that unit, while the media type and schema supply its domain
meaning. Omitting the offset makes a duration-only claim. When `blob:extent` is
present, the offset (or zero when omitted) plus the length must not exceed it.

Structured coordinates do not fit `BlobSpan`. A structured transcript can use
character offsets where they are linear, while its schema can declare an
ordinary relation-instance attribute for a structured path. A key-value
resource can likewise use a schema-defined key path. This keeps linear clock
agreement precise without treating every resource as time-based.

`BlobProfile.check_clock(clock)` checks linear spans only when requested. The
blob item supplies the rate and unit. Units must match the clock exactly, and
the check compares exact integer products rather than using floating-point
tolerance or unit conversion. A blob tier in a clocked graph must be explicitly
untimed. A clock edit can pass `blob=BlobProfile(graph)` to its edit session so
`freeze()` refuses a result whose updated timing disagrees with a staged span.

## Hash and resolve payloads explicitly

`hash_blob(source)` streams from the source's current position and returns a
validated `BlobRef`. `MappingResolver` serves caller-held bytes, and
`ChainResolver` tries resolvers in the exact order supplied. A custom store
implements `BlobResolver.open(ref, href)` and either returns a fresh binary
reader or returns `None` when it has no match.

Resolution is always explicit. The library does not search the working
directory, a user directory, or the network. Once a resolver answers, a digest
or size mismatch does not make `ChainResolver` fall through to a later
resolver. A consumer that wraps the answer in `VerifiedReader` reports the
mismatch as an error.

`VerifiedReader` hashes bytes as they are read. Its `verified` property becomes
true only after one complete, sequential read reaches end of file and matches
both the declared size and digest. A partial or seeked read remains unverified,
which permits random-access inspection without presenting that read as a
content check.

## Bundle embedded and linked payloads

A bundle is a strict, store-only ZIP container with `bundle.json`, the ordinary
`graph.json` document, and zero or more embedded payload entries. Each asset
row records a durable ID, a declared position, its content identity, and either
`embedded` or `linked` residency. A linked row may carry a relative href. The
asset rows and ZIP entries preserve declared order; they are never presented in
digest order.

Residency is not graph identity. `graph.json` is byte-identical whether a
payload is embedded or linked, and moving a payload does not change any graph
fingerprint. `write_bundle` accepts a Boolean or predicate for per-payload
embedding. `relink` streams selected embedded payloads to a `BlobSink`, and
`embed_links` performs the inverse through an explicit resolver. Bundle output
is deterministic for equal graph bytes and equal per-asset choices.

`open_bundle` validates the index, graph, inventory, entry names, entry order,
ZIP metadata, and configured limits before exposing the bundle. It does not
read payload bodies. `Bundle.open_blob` opens one payload lazily and returns a
`VerifiedReader`. Close the bundle after its readers are finished; it borrows
the caller's bundle stream rather than taking ownership of it.

The command line provides an explicit content-addressed directory store:

```console
$ tiergraph blob put recording.wav --store media --media-type audio/wav
$ tiergraph blob list graph.json
$ tiergraph blob verify graph.json --store media
```

`blob put` reports the digest, size, and relative store href; the graph remains
an explicit editing step. A plain graph document is the all-linked form with no
hrefs, so the directory resolver uses `sha256/DIGEST` below the selected store.
`blob get` verifies a selected payload before publishing its output.

Use bundles when graph bytes and residency choices should travel together:

```console
$ tiergraph bundle flatten graph.json --store media -o graph.tgb
$ tiergraph bundle inspect graph.tgb --json
$ tiergraph bundle unflatten graph.tgb --store media -o linked.tgb
```

`bundle flatten` embeds every payload by default. Repeated `--only SHA256`
options make exactly those payloads embedded and leave the others linked.
`bundle unflatten` moves every embedded payload to the store by default, or
only the payloads selected with `--only`. The commands publish outputs only
after verified streaming succeeds.

## Keep type-specific policy in the domain

The core accepts any syntactically valid media type and absolute schema URI. A
domain that supports a closed set should register its own `GraphProfile`. The
runnable
[`closed_media_profile`](../../examples/closed_media_profile.py) example wraps
`BlobProfile`, accepts two media types, refuses an unlisted type, and supplies
the witnesses required for explicit profile registration. Run it with
`python -m examples.closed_media_profile`.

Type-specific metadata remains ordinary typed attributes in the media type's
own namespace. Audio duration, sample rate, and channels; video frame rate; a
transcript's text organization; selected key-value entries; or an attached
graph's fingerprint can all be declared there. The base item's values are
author-declared. An inspector can instead record inspected values as layer
facts, with the layer source identifying where they came from.

Inspectors are optional domain components. Register them per media type, import
them lazily, and run them only on an explicit request. An inspector reads
through a resolver, never mutates a payload, and can compare declared metadata
with content. The content digest then makes a verified result stable. No
inspector ships in the core, and no inspector runs while loading a graph or
bundle.

A structured transcript remains its own resource even when another graph
describes the same speech. An attached graph is likewise an ordinary resource:
its inspector may load it and resolve its nested attachments through the same
explicit resolver. A key-value blob is appropriate when nested data is large or
needs content identity; small JSON that should participate directly in graph
structure can remain an inline `JsonAttributeValue`. Any item can carry several
key-value blobs, and a binary blob can carry metadata blobs while also being
attached to many structural items.
