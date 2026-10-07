# Interchange with annotation formats

tiergraph reads and writes Praat TextGrid files, so an existing time-aligned
annotation can be loaded, queried with the rest of the library, and written
back. This page walks through that round trip and describes the graph a
TextGrid becomes. Other formats are listed at the end.

## Read a TextGrid

`tiergraph.textgrid.from_textgrid` accepts the text of a TextGrid in Praat's
long or short form, as `str` or `bytes`. It returns a `TextGridReadResult`, a
named pair of the decoded `graph` and the span-view `profile` that selects the
imported tiers. Its `clock` property builds the `ClockProfile` that maps the
graph's boundaries back to the times in the file.

```python
from dataclasses import replace

from tiergraph import (
    AttributeValue,
    ItemRef,
    ItemsSelector,
    NodeSet,
    QualifiedName,
    Walk,
    WalkDirection,
    WhereSelector,
    evaluate_selection,
)
from tiergraph.predicate import Cell, Equals
from tiergraph.textgrid import from_textgrid, to_textgrid

TEXTGRID = """File type = "ooTextFile"
Object class = "TextGrid"

xmin = 0
xmax = 0.9
tiers? <exists>
size = 2
item []:
    item [1]:
        class = "IntervalTier"
        name = "words"
        xmin = 0
        xmax = 0.9
        intervals: size = 2
        intervals [1]:
            xmin = 0
            xmax = 0.3
            text = "a"
        intervals [2]:
            xmin = 0.3
            xmax = 0.9
            text = "cat"
    item [2]:
        class = "IntervalTier"
        name = "phones"
        xmin = 0
        xmax = 0.9
        intervals: size = 4
        intervals [1]:
            xmin = 0
            xmax = 0.3
            text = "AH"
        intervals [2]:
            xmin = 0.3
            xmax = 0.5
            text = "K"
        intervals [3]:
            xmin = 0.5
            xmax = 0.7
            text = "AE"
        intervals [4]:
            xmin = 0.7
            xmax = 0.9
            text = "T"
"""

read = from_textgrid(TEXTGRID)
graph = read.graph
print([tier.declaration.long_name for tier in graph.tiers])
```

```text
['TextGrid base', 'TextGrid clock', 'words', 'phones']
```

## What the graph holds

Every name the reader declares is in the namespace `urn:tiergraph:textgrid`.
Each TextGrid tier becomes a tier with its TextGrid name as its local name, and
each interval or point becomes one item whose `value` attribute holds its label.
Two tiers come first:

- `base` has one item for each stretch between consecutive distinct times
  anywhere in the file, carrying decimal `start` and `duration` attributes.
  Every interval covers a contiguous run of base items through the `coverage`
  relation, and every point sits on a base boundary through `point-coverage`.
- `clock` binds the base boundaries to the clock the `clock` property builds,
  with the document attribute `unit` naming the unit (`"s"` unless the `unit=`
  argument says otherwise).

For every pair of interval tiers, the reader declares a containment relation
from the earlier tier in the file to the later one, named
`containment-I-J` after the two tiers' zero-based positions among the file's
tiers. With the default `containment_rule="enclosure"`, a later-tier interval is
contained by the earlier-tier interval that encloses it.
`containment_rule="endpoint_coincidence"` additionally requires both of the
child's endpoints to be boundaries of the parent. Each containment relation is
declared acyclic with a single parent, so a `Walk` over it needs no step cap.

## Query it

Select the word whose label is `cat` with a value predicate, then walk the
containment relation from the words tier to the phones tier.

```python
textgrid = "urn:tiergraph:textgrid"
words = QualifiedName(textgrid, "words")
value = QualifiedName(textgrid, "value")


def labels(nodes: NodeSet) -> list[str]:
    result: list[str] = []
    for node in nodes.nodes:
        reference = node.reference
        assert isinstance(reference, ItemRef)
        tier = next(t for t in graph.tiers if t.declaration.name == reference.tier)
        result.extend(
            attribute.lexical
            for attribute in tier.items[reference.index].attributes
            if isinstance(attribute, AttributeValue) and attribute.name == value
        )
    return result


cat = evaluate_selection(
    graph, WhereSelector(ItemsSelector(words), Equals(Cell(value), ("cat",)))
)
phones_of_cat = Walk(
    cat, QualifiedName(textgrid, "containment-0-1"), WalkDirection.FORWARD
).evaluate()
print(labels(cat), "->", labels(phones_of_cat.nodes))
```

```text
['cat'] -> ['K', 'AE', 'T']
```

## Write it back in seconds

`to_textgrid(graph, profile)` writes the long form, one TextGrid tier for each
tier the profile names, and fills any uncovered stretch of an interval tier with
an empty label. The profile's `clock_face` decides which coordinates are
written. The default, `"tick"`, writes structural boundary indexes, so the file
above comes back with `xmax = 4` (its four base stretches) instead of `0.9`. To
write the times the file was read with, set `clock_face="physical"` on the
profile and pass the clock:

```python
seconds = to_textgrid(
    graph, replace(read.profile, clock_face="physical"), clock=read.clock
)
ticks = to_textgrid(graph, read.profile)
print([line.strip() for line in seconds.splitlines()[3:5]])
print([line.strip() for line in ticks.splitlines()[3:5]])
print(from_textgrid(seconds).graph == graph)
```

```text
['xmin = 0', 'xmax = 0.9']
['xmin = 0', 'xmax = 4']
True
```

Reading the written file gives back an equal graph. The tick face is useful when
the graph has no physical clock, or when a downstream tool wants integer
positions. With a clock whose coordinates carry ordered gaps between ticks, the
tick face needs a `scale=` that maps those refined coordinates onto integers;
the physical face refuses `scale=`.

## From the command line

`tiergraph span render GRAPH --profile PROFILE --format textgrid` writes a
TextGrid from a stored graph and a span-view profile document. Reading a
TextGrid is a Python operation. Save both values returned by `from_textgrid` so
the CLI can use them. This executed example uses a temporary directory:

```python
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from tiergraph import dumps

with TemporaryDirectory() as directory:
    graph_path = Path(directory) / "textgrid.json"
    profile_path = Path(directory) / "span-profile.json"
    graph_path.write_text(dumps(graph), encoding="utf-8")
    profile_path.write_text(
        json.dumps(read.profile.to_data(), indent=2) + "\n", encoding="utf-8"
    )
    rendered = subprocess.run(
        (
            Path(sys.executable).with_name("tiergraph"),
            "span",
            "render",
            graph_path,
            "--profile",
            profile_path,
            "--format",
            "textgrid",
        ),
        check=True,
        capture_output=True,
        text=True,
    ).stdout
print(rendered.splitlines()[0])
```

```text
File type = "ooTextFile"
```

With the two files in the current directory, the shell command is:

```console
$ tiergraph span render textgrid.json --profile span-profile.json \
    --format textgrid > rendered.TextGrid
```

## Other formats

tiergraph has no reader for ELAN EAF or other annotation formats. A converter
that builds a graph with `tiergraph.build` or the direct constructors (see
[Construction](construction.md)) gets the same validation as any other graph.
On the output side, [span views](span-views.md) project a segmentation to JSON,
JSON Lines, text, HTML, and DOT as well as TextGrid, and
[Serialization](serialization.md) covers the canonical JSON document that any
other tool can read with the published schema and [format notes](../format.md).
