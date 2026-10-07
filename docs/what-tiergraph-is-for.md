# What tiergraph is for

tiergraph is a Python library and an interchange format for parallel ordered
sequences and the declared links between them. This page says what it provides,
where it is a good fit, how it relates to tools you may already use, and what it
does not try to do.

## What it is

A tiergraph document is one immutable graph of ordered tiers. Items on a tier
carry typed attributes, and declared relations link items and boundaries within
and across tiers. The graph is validated as a whole when it is built, so code
that holds a `Graph` holds a valid one. Every other view is computed from that
one store: selections, walks, containment, timing, pattern matches, folds, and
span projections. The store serializes to one canonical JSON spelling with an
explicit format version and a published schema.

## Where it fits well

- **Checked multi-tier alignment.** Words, syllables, phones, and any other
  layer sit in one graph whose declarations say which links are allowed, and an
  alignment that breaks them cannot be built.
- **Many-to-many and ordered links.** A relation can link one item to many,
  link boundaries as well as items, or relate two ordered sequences as a whole,
  for correspondences that reorder or repeat.
- **Identity through edits.** Items and boundaries can carry durable ids, and
  every structural edit reports where each position moved.
- **Regular patterns over structure.** Sequence patterns run over a tier, over
  each container's children, over an order the graph declares across tiers and
  boundaries, and over every path of a lattice.
- **One structure, many algebras.** The same dependency relation answers least
  cost, path counts, recognition, posteriors, and witnesses, depending on the
  semiring a fold is given; synchronous grammars parse into a chart graph folded
  the same way.
- **Reproducible output.** Equal graphs produce identical bytes, and every
  computed view has a canonical order.
- **Untrusted queries.** Patterns, predicates, folds, and grammars accept a
  declared work budget.

## Related tools

Several established tools work with tiered or graph-shaped annotation. Each is
described here on its own terms, with how tiergraph relates to it.

**Praat TextGrid and its Python readers.** The TextGrid is Praat's text format
for interval and point tiers over a time axis, and it is the common exchange
format for phonetic annotation. Python packages such as `textgrid`, `tgt`, and
`praatio` read, write, and edit TextGrid files directly. tiergraph reads and
writes TextGrid as well, adding declared containment between tiers and the rest
of its query machinery; for editing the intervals of a TextGrid in place, those
packages are the more direct tool. See
[Interchange with annotation formats](guide/annotation-formats.md).

**ELAN and pympi.** ELAN is an annotation tool for audio and video whose EAF
format holds time-aligned and symbolically associated tiers, linguistic types,
and controlled vocabularies, and pympi reads and writes EAF from Python.
tiergraph has no EAF reader; a converter that builds a graph from EAF gets
tiergraph's validation and queries.

**EMU-SDMS.** The EMU Speech Database Management System manages speech corpora
with a hierarchical annotation model, a web-based labeling application, and the
EMU Query Language, used from R. Its model of levels linked by hierarchy, with
queries over sequence and dominance, is the closest of these to tiergraph's.
tiergraph is a library and a document format rather than a database or a
labeling application, and it works in Python.

**Annotation graphs, LAF, and GrAF.** Bird and Liberman's annotation graphs
give linguistic annotation a formal basis as labeled arcs between time-anchored
nodes, and the Linguistic Annotation Framework and its serialization GrAF build
standoff interchange on a graph model. tiergraph shares their view of
annotation as a graph over anchored boundaries. It makes ordered tiers primary,
types every relation by a declaration, and validates the whole graph when it is
built.

**NLTK.** The Natural Language Toolkit provides tree types, corpus readers, and
a broad set of language-processing tools. A tiergraph `OrderedContainment`
reads a tree as one relation inside a multi-tier graph, beside the other layers
aligned to the same items.

**OpenFst and Pynini.** OpenFst is a library for weighted finite-state
transducers, with composition, determinization, minimization, and shortest-path
algorithms that scale to large lexicons and rewrite cascades; Pynini brings it
to Python with a grammar-compilation toolkit. tiergraph's lattice matching and
path plans are exact computations over finite lattices stored in a graph,
aimed at checking and scoring annotated alternatives. For building and
composing large transducers, OpenFst and Pynini are the tools to use.

## Lineage

The model is informed by Sue Hertz's Delta representation, which represented
speech as synchronized streams of tokens, and by the Heterogeneous Relation
Graphs of the Festival Speech Synthesis System, which hold an utterance as items
in several relations at once. tiergraph keeps their emphasis on explicit tiered
structure and adds a typed, immutable model with a versioned interchange format.

## What it does not try to do

- **General graphs.** Structure without ordered tiers is better served by a
  graph database or a general graph library.
- **High-rate mutable stores.** A `Graph` is a value validated when it is
  built; collect many edits in a `GraphEditor` and freeze once.
- **Annotation interfaces.** tiergraph has no labeling application.
- **Signal processing.** It stores times and labels, not audio.
- **Archival storage.** A reader accepts only the format version it implements,
  so a document is interchange between tools that agree on a version.
- **Large-scale transducer operations.** Composition and minimization of large
  weighted automata belong to an FST toolkit.

## How it interoperates

- TextGrid in through `from_textgrid` and out through `to_textgrid` or
  `tiergraph span render --format textgrid`.
- Span views out as JSON, JSON Lines, text, HTML, and Graphviz DOT.
- JSON values as attributes, or as a graph of their own through
  `json_value_graph`.
- The canonical JSON document, its published schema, and the
  [format notes](format.md), for a reader or writer in another language.
