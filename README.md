# tiergraph

*Ordered tiers, declared relations, and an algebra over them*

tiergraph holds parallel ordered sequences and the declared links between them
as one immutable graph, checked when it is built. Every view — selection,
traversal, containment, timing, folds — is computed from that one graph.

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

The model is informed by Sue Hertz's Delta representation and the Heterogeneous
Relation Graphs (HRGs) of the Festival Speech Synthesis System. tiergraph keeps
their emphasis on explicit tiered structure while defining a typed, immutable
model and a versioned interchange format.

## What you can do with it

**Store aligned layers that stay valid.** Tiers, items, typed attributes, and
declared relations live in one immutable graph that refuses an invalid state
when it is built. Build one with the `tiergraph.build` builder or the direct
constructors, edit it with `GraphEditor`, or read one from a Praat TextGrid.
See [construction](https://github.com/lenzo-ka/tiergraph/blob/main/docs/guide/construction.md) and
[annotation formats](https://github.com/lenzo-ka/tiergraph/blob/main/docs/guide/annotation-formats.md).

**Query it.** Selectors with set algebra, typed value predicates, quantifiers
over related items, offset-interval joins, and walks over declared relations
return canonical node sets. See
[selection and traversal](https://github.com/lenzo-ka/tiergraph/blob/main/docs/guide/selection-and-traversal.md) and
[predicates and offset joins](https://github.com/lenzo-ka/tiergraph/blob/main/docs/guide/predicates-and-offset-joins.md).

**Match sequences.** Regular patterns of item tests run over a tier, over each
container's children, or over an order the graph declares across tiers and
boundaries, and over every path of a lattice. See
[sequence patterns](https://github.com/lenzo-ka/tiergraph/blob/main/docs/guide/patterns.md).

**Fold and parse.** A fold evaluates a dependency relation under a semiring
you choose: least cost, path counts, recognition, posteriors, or witnesses from
the same graph. Synchronous grammars parse token input into a chart graph and
fold over it. See [folding](https://github.com/lenzo-ka/tiergraph/blob/main/docs/guide/folding.md) and
[grammars](https://github.com/lenzo-ka/tiergraph/blob/main/docs/guide/grammars.md).

**Interchange.** Canonical JSON with an explicit format version and a published
schema, TextGrid in and out, and span views as JSON, JSON Lines, text, HTML, or
DOT. See [serialization](https://github.com/lenzo-ka/tiergraph/blob/main/docs/guide/serialization.md) and
[span views](https://github.com/lenzo-ka/tiergraph/blob/main/docs/guide/span-views.md).

Queries that come from someone else can carry a
[work budget](https://github.com/lenzo-ka/tiergraph/blob/main/docs/guide/work-budgets.md).

tiergraph is a library and a format rather than a database or an annotation
tool. It suits structure whose layers are declared in advance. A workload that
edits a graph many times should collect the edits in a `GraphEditor` and freeze
once, since every frozen `Graph` is validated when it is built.
[What tiergraph is for](https://github.com/lenzo-ka/tiergraph/blob/main/docs/what-tiergraph-is-for.md) compares it with
related tools and lists what it does not try to do.

## Command line

The `tiergraph` command reads graph documents from files or stdin and runs the
same machinery as the Python API. For example:

```console
$ tiergraph validate graph.json
$ tiergraph inspect graph.json
$ tiergraph select graph.json --where 'score>=0.5' --prefix ex
$ tiergraph match graph.json --pattern '{class=V} / {class=C} _' --prefix ex \
  --ordering '{"order":"tier","tier":{"namespace":"urn:ex","local_name":"phones"}}' focus
$ tiergraph span render graph.json --profile span-profile.json --format textgrid
```

See the generated [CLI reference](https://github.com/lenzo-ka/tiergraph/blob/main/docs/reference/cli.md) for every command and
its options.

## Documentation

Start with [getting started](https://github.com/lenzo-ka/tiergraph/blob/main/docs/getting-started.md), which reads a TextGrid,
queries it, and writes it back, then builds a graph and uses the command line.
[Concepts](https://github.com/lenzo-ka/tiergraph/blob/main/docs/concepts.md) describes the data model, and the
[documentation map](https://github.com/lenzo-ka/tiergraph/blob/main/docs/README.md) lists every guide by task. The
[API reference](https://github.com/lenzo-ka/tiergraph/blob/main/docs/reference/api.md) covers every top-level export and the
documented secondary surfaces; the [CLI reference](https://github.com/lenzo-ka/tiergraph/blob/main/docs/reference/cli.md) is
generated from the parser.

The companion `tiergraph_dot` package renders any `Graph` as deterministic
Graphviz DOT and ships in the same distribution. Continuing from the example
above, where `graph` was built:

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

Within a release line the format only grows; the [format notes](https://github.com/lenzo-ka/tiergraph/blob/main/docs/format.md)
describe the version policy and the canonical spelling.

## License

BSD 2-Clause. The full text is in [LICENSE](LICENSE).
