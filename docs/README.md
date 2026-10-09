# Documentation

Start with [Getting started](getting-started.md): it reads a TextGrid, queries
it, and writes it back, then builds a graph and uses the command line.
[Concepts](concepts.md) describes the data model behind it, and
[What tiergraph is for](what-tiergraph-is-for.md) places tiergraph among related
tools.

## How do I…

- **Read or write a Praat TextGrid?** See
  [Interchange with annotation formats](guide/annotation-formats.md).
- **Build a checked graph?** Start with [Construction](guide/construction.md).
- **Edit a graph, one change or many?** See
  [Editing graphs](guide/editing.md).
- **Find items or follow their links?** See
  [Selection and traversal](guide/selection-and-traversal.md).
- **Select by a stored value, a related item, or an offset interval?** See
  [Predicates and offset joins](guide/predicates-and-offset-joins.md).
- **Find a sequence of items, within a tier, a container, or across tiers?** See
  [Sequence patterns](guide/patterns.md).
- **Match or count paths through a lattice?** See
  [Sequence patterns](guide/patterns.md#matching-paths-through-a-lattice).
- **Compute a least cost, a count, a posterior, or a recognition result?** Read
  [Folding](guide/folding.md).
- **Parse or translate token input with a grammar?** See
  [Grammars](guide/grammars.md).
- **Limit the work an untrusted query may do?** See
  [Work budgets](guide/work-budgets.md).
- **Attach physical times to boundaries?** See [Timing](guide/timing.md).
- **Attach typed external resources without putting bytes in the graph?** See
  [External resources](guide/external-resources.md).
- **Turn segmentation into text, HTML, JSON, or JSON Lines?** See
  [Span views](guide/span-views.md).
- **Write JSON or render DOT?** Go to [Serialization](guide/serialization.md).
- **Check that a graph carries an interpretation?** See
  [Profiles](guide/profiles.md).
- **Chain a fold into actions?** See
  [Advanced: recognize and act](guide/recognize-and-act.md).

## Guides

- [Construction](guide/construction.md)
- [Editing graphs](guide/editing.md)
- [Selection and traversal](guide/selection-and-traversal.md)
- [Predicates and offset joins](guide/predicates-and-offset-joins.md)
- [Sequence patterns](guide/patterns.md)
- [Folding](guide/folding.md)
- [Grammars](guide/grammars.md)
- [Work budgets](guide/work-budgets.md)
- [Timing](guide/timing.md)
- [External resources](guide/external-resources.md)
- [Interchange with annotation formats](guide/annotation-formats.md)
- [Span views](guide/span-views.md)
- [Profiles](guide/profiles.md)
- [Serialization](guide/serialization.md)
- [Advanced: recognize and act](guide/recognize-and-act.md)

## Examples

Each example runs from the repository root with `python -m examples.NAME`.

- [caption_alignment](../examples/caption_alignment.py) builds a word and phone
  alignment and walks from a word to its phones.
- [text_segmentation](../examples/text_segmentation.py) projects word and
  sentence segmentations through span views.
- [critical_path](../examples/critical_path.py) finds the critical path through
  a build dependency graph with a fold.
- [json_document](../examples/json_document.py) encodes and decodes a JSON
  document as a checked graph.
- [closed_media_profile](../examples/closed_media_profile.py) restricts
  external resources to a domain-owned set of media types.
- [mix_paths](../examples/mix_paths.py) addresses a multi-ring mix graph through
  TG-PATH.
- [mixing](../examples/mixing.py) recognizes a path and applies two actions; it
  is the subject of [Advanced: recognize and act](guide/recognize-and-act.md).

## Reference

Use the [format notes](format.md) when implementing an interchange reader or
writer. The generated [API](reference/api.md) and [CLI](reference/cli.md)
references describe the current source. Maintainers should also read
[Contributing documentation](contributing-docs.md).
