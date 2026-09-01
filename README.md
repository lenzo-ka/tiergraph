# tiergraph

*Ordered tiers, declared relations, and an algebra over them*

tiergraph holds parallel ordered sequences and the declared links between them
as one immutable graph, checked when it is built. Every view — selection,
traversal, containment, timing, folds — is computed from that one graph, so no
view can disagree with the store.

The shape is the track view of an audio or video editor: rows of items, ordered
within a row, aligned across rows, with links between rows. Aligned annotations
over a signal have it; so do layered timelines and structured documents whose
parts reference each other.

You have this problem already if:

- you can construct a state your own code treats as invalid;
- you keep a derived index beside the store and must remember to update both; or
- your serialized format breaks when you add a field.

The package requires Python 3.12 or later. Install it from PyPI:

```console
python -m pip install tiergraph
```

For development, install an editable checkout with the development tools:

```console
git clone https://github.com/lenzo-ka/tiergraph.git
cd tiergraph
python -m pip install -e ".[dev]"
```

## See an alignment

This caption graph links each word to its phones. Select `cat`, walk the declared
alignment, and the answer is visible in the input:

```python
from tiergraph import ItemSelector, Walk, WalkDirection, evaluate_selection
from tiergraph.build import document

builder = document("https://example.com/captions", prefix="caption")
words = builder.tier(
    "words",
    ("a", "cat", "sat"),
    item_type="word",
    membership="word-membership",
)
phones = builder.tier(
    "phones",
    ("AH", "K", "AE", "T", "S", "AE-2", "T-2"),
    item_type="phone",
    membership="phone-membership",
)
aligns = builder.link(
    "aligns",
    words,
    phones,
    ((0, 0), (1, 1), (1, 2), (1, 3), (2, 4), (2, 5), (2, 6)),
    acyclic=True,
)
graph = builder.build()

cat = evaluate_selection(graph, ItemSelector(words.ref(1)))
reached = Walk(cat, aligns.name, WalkDirection.FORWARD).evaluate().nodes
assert [node.reference for node in reached.nodes] == [
    phones.ref(1),
    phones.ref(2),
    phones.ref(3),
]
```

The complete runnable example keeps the displayed phone labels separate from
their durable ids and prints `['K', 'AE', 'T']`; see
[`examples/caption_alignment.py`](examples/caption_alignment.py).

The model learned from Paul Hertz's Delta representation and the heterogeneous
relation graphs (HRGs) of the Festival Speech Synthesis System. tiergraph keeps
their emphasis on explicit tiered structure while defining a typed, immutable
model and a versioned interchange format.

## What you can do with it

**Hold aligned layers without drift.** One store, computed views. This is the base
case and covers most use. Build a graph directly, or record an ordered edit stream
as a `Program` and run it — see [construction](docs/guide/construction.md).

**Answer structural questions by traversal.** Selection with set algebra, `Walk`
over declared relation incidence, ordered containment. Replaces hand-written index
arithmetic — see [selection and traversal](docs/guide/selection-and-traversal.md).

**Measure and recognize by fold.** A fold evaluates an acyclic dependency relation
with a semiring you supply: min-plus for least cost, counting for path counts,
boolean for recognition, path semirings for witnesses. This is the capability with
no common substitute — most alternatives make you write the traversal and the
accumulation by hand, separately, for each question. See
[folding](docs/guide/folding.md) and
[recognize and act](docs/guide/recognize-and-act.md).

**Interchange that does not rot.** Canonical JSON, explicit format and machine
version stamps, a SHA-pinned schema. Documents round-trip, and two graphs differing
only in input order serialize identically — see
[serialization](docs/guide/serialization.md).

Timing and projection build on those: attach a clock profile to resolve physical
timing ([timing](docs/guide/timing.md)), or project segmentation graphs into
deterministic span views for JSON, JSON Lines, text, HTML, or DOT
([span views](docs/guide/span-views.md)).

It is not good for unordered graphs, for mutable working stores with high edit
rates, or for anything whose layer structure is not known in advance. `Graph` is a
frozen value validated when it is built; an edit-heavy workload should record a
`Program` and execute it once.

## Command line

The `tiergraph` command validates graph documents, renders them, and exposes the
same span-view and folding machinery as the Python API. For example:

```console
tiergraph validate graph.json
tiergraph render graph.json -o graph.dot
tiergraph span render graph.json --profile span-profile.json --format text
tiergraph semirings
```

See the generated [CLI reference](docs/reference/cli.md) for all fifteen
commands and their options.

## Documentation

Start with the [documentation map](docs/README.md), then
[concepts](docs/concepts.md) for the data model and [getting
started](docs/getting-started.md) for a worked walkthrough. The [API
reference](docs/reference/api.md) covers every top-level export; the [CLI
reference](docs/reference/cli.md) is generated from the parser.

The companion `tiergraph_dot` package renders a graph as deterministic Graphviz
DOT and ships in the same distribution:

```python
import tiergraph_dot

dot = tiergraph_dot.dumps(graph)
```

## Stability

The current development version and every published pre-1.0 release are alpha
software. Before 1.0, a 0.X.0 release is in effect a major release and carries
no compatibility guarantee for the public Python API: names may be removed or
renamed in one, with no migration path. A consumer is expected to track the
current version rather than pin an older one and wait.
The JSON wire format, construction machine format, and span-view JSON format
carry explicit version stamps so a reader can identify the format it receives.
A format stamp identifies a contract; it does not imply that every version can
read or migrate every older format.

After 1.0, the intended policy is to announce a deprecated public Python API in
a minor release, retain it with a warning for at least one subsequent minor
release, and remove it only in a later release. Security, correctness, or
otherwise impractical compatibility constraints may require a faster change,
which will be documented in the release. This is an intended post-1.0 policy,
not a compatibility promise for the current alpha series.

## Format versions

A tiergraph document declares the format version it was written in. A reader
accepts documents of the version it implements and refuses any other, naming
the version it found and the one it expected.

Documents are versioned interchange: they move data between tools that agree on
a version. They are not an archival format, and reading a document written by a
later release is not supported.

Within a release line the format only grows. Fields are added; none is removed,
narrowed, or redefined, so a document written earlier in the line stays valid
and no field a reader already understands changes meaning underneath it. A
change that would break that is permitted, and it costs a step in the version
position that carries breaking changes — before 1.0 the minor position, after
1.0 the major one. A gate in this repository compares the committed schema
against the last released one and refuses a break that takes no such step, so
for the structural shape that schema describes, the version alone tells a reader
whether the format grew or moved.

The gate reads the schema, and the schema is not the whole format. The decoder is
the authority for semantic constraints such as declaration compatibility,
acyclicity, and reference validity, and a release that tightened one of those
would refuse a document the previous release accepted without changing a schema
byte, drawing a finding, or costing a version position. Within a release line the
structural half of *only grows* is enforced; the semantic half is intended and
rests on review. A reader deciding whether an existing document still loads should
read the changelog, not the version alone.
