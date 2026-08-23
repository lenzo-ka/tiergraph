# tiergraph

tiergraph is a library for data that is ordered, layered, and typed: sequences of
typed items arranged in parallel tiers, with declared relations between them, held
as one immutable graph and read through views derived from that single structure.

Many domains have this shape — aligned annotations over a signal, layered
timelines, structured documents whose parts reference each other. Representing it
by hand tends to produce invalid states, views that drift out of sync, and
serialization that breaks between versions. tiergraph gives one checked kernel for
it:

- **Construction is checked.** A graph validates its names, declarations,
  endpoints, attributes, and structural constraints when it is built, so an
  invalid graph cannot be constructed and nothing downstream has to re-check it.
- **One structure, many views.** Selection, traversal, containment, and folds all
  read the same immutable graph, so a view cannot disagree with the store.
- **Folds measure and recognize.** A fold evaluates a dependency graph with a
  semiring — least-cost paths, path counts, reachability, recognition — over the
  declared structure.
- **The format is versioned and deterministic.** The JSON wire codec and the
  construction machine carry explicit version stamps, and serialization is
  canonical, so documents round-trip and interchange without ambiguity.

tiergraph is domain-general. One application is the phonetics toolkit ipakit,
which models transcriptions as tiered structure; tiergraph itself carries no
phonetics.

The package requires Python 3.12 or later. A PyPI release is not yet available;
install it from a source checkout:

```console
git clone https://github.com/lenzo-ka/tiergraph.git
cd tiergraph
python -m pip install .
tiergraph --version
```

## A first graph

A graph declares its namespaces, tiers, and items, and validates them on
construction:

```python
from tiergraph import (
    Graph,
    Item,
    NamespaceDeclaration,
    QualifiedName,
    Tier,
    TierDeclaration,
)

namespace = "https://example.com/score"
events = QualifiedName(namespace, "events")
graph = Graph(
    (NamespaceDeclaration("score", namespace),),
    (Tier(TierDeclaration(events, "Events"), (Item("opening"),)),),
    (),
)
assert graph.tiers[0].items[0].durable_id == "opening"
```

`Graph` checks names, declarations, endpoints, attributes, and graph-wide
constraints as it is constructed. An invalid graph fails here, before it can
reach a selection, a fold, or a serializer.

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
