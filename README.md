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

- Build a graph directly, or record an ordered edit stream as a `Program` and run
  it — see [construction](docs/guide/construction.md).
- Select and traverse the structure, including ordered containment — see
  [selection and traversal](docs/guide/selection-and-traversal.md).
- Fold a dependency graph with a semiring to measure or recognize it — see
  [folding](docs/guide/folding.md) and [recognize and act](docs/guide/recognize-and-act.md).
- Attach a clock profile and resolve physical timing — see [timing](docs/guide/timing.md).
- Serialize to canonical JSON or render Graphviz DOT — see
  [serialization](docs/guide/serialization.md).
- Project segmentation graphs into deterministic span views for JSON, JSON Lines,
  text, HTML, or DOT — see [span views](docs/guide/span-views.md).

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

tiergraph 0.1.x is alpha software. The public Python API may change before 1.0;
where possible, changes will be additive, but compatibility is not yet promised.
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
