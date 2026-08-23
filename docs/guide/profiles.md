# Profiles

A profile reads a checked role out of ordinary graph declarations. It adds no
new kind of node and changes nothing in the store; it validates that specific
declarations and instances support one interpretation, then answers questions
against them. Constructing a profile is the check: an invalid arrangement raises
instead of returning a profile that would give wrong answers later.

Three profiles read common stored roles:

- `JsonValueProfile` reads a recursive JSON value out of items joined by ordered
  relations.
- `PersistedChoiceProfile` reads a set of candidates and an optional stored
  default for each source.
- `OrderedRootsProfile` reads stored root order and reconciles it with the roots
  a dependency relation implies.

## JSON values

`json_value_graph` builds a standalone graph for one JSON value and returns the
graph, a `JsonValueProfile` over it, and the root item reference. The profile's
`value` method reads the structure back. Object keys are stored in lexical
order, so equivalent objects have one encoding.

```python
from tiergraph import json_value_graph
from tiergraph.core import JsonValue

document: JsonValue = {
    "name": "tiergraph",
    "tiers": ["events", "clock"],
    "stable": False,
}
graph, profile, root = json_value_graph(document)
print("node items:", len(graph.tiers[0].items))
print("decoded:", profile.value(root))
print("round-trips:", profile.value(root) == document)
```

```text
node items: 6
decoded: {'name': 'tiergraph', 'stable': False, 'tiers': ['events', 'clock']}
round-trips: True
```

The six value nodes are the object, its three values, and the two array
elements. The decoded object comes back with keys in sorted order, which is why
`round-trips` holds even though the input listed `stable` last.

## Persisted choices

`PersistedChoiceProfile` reads alternatives and a persisted default per source.
It requires the general relation constraints that make the role well-formed: the
alternatives relation must have unique sources and distinct targets, and the
default relation must select exactly one target that is a member of that
source's alternatives. Those constraints are what let `candidates` and `default`
return trustworthy answers.

```python
from tiergraph import (
    Graph,
    Item,
    ItemRef,
    NamespaceDeclaration,
    PersistedChoiceProfile,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    QualifiedName,
    RelationEndpointKind,
    RelationSideDeclaration,
    Tier,
    TierDeclaration,
)

ns = "https://example.com/config"
nodes = QualifiedName(ns, "nodes")
alternatives = QualifiedName(ns, "alternatives")
default = QualifiedName(ns, "default")

labels = ("theme", "light", "dark", "high-contrast")
refs = {name: ItemRef(nodes, index) for index, name in enumerate(labels)}
one = RelationSideDeclaration((RelationEndpointKind.ITEM,), (nodes,), 1, 1)
many = RelationSideDeclaration((RelationEndpointKind.ITEM,), (nodes,), 1, None)
graph = Graph(
    (NamespaceDeclaration("cfg", ns),),
    (Tier(TierDeclaration(nodes, "Nodes"), tuple(Item(name) for name in labels)),),
    (
        PolyadicRelationDeclaration(
            alternatives, one, many, unique_sources=True, distinct_targets=True
        ),
        PolyadicRelationDeclaration(
            default,
            one,
            one,
            unique_sources=True,
            distinct_targets=True,
            targets_subset_of=alternatives,
        ),
    ),
    polyadic_relations=(
        PolyadicRelationInstance(
            alternatives,
            (refs["theme"],),
            (refs["light"], refs["dark"], refs["high-contrast"]),
        ),
        PolyadicRelationInstance(default, (refs["theme"],), (refs["dark"],)),
    ),
)

choice = PersistedChoiceProfile(graph, alternatives, default)


def name_of(reference: ItemRef | None) -> str | None:
    return None if reference is None else labels[reference.index]


print("candidates:", [name_of(ref) for ref in choice.candidates(refs["theme"])])
print("default:", name_of(choice.default(refs["theme"])))
```

```text
candidates: ['light', 'dark', 'high-contrast']
default: dark
```

The `default` relation declares `targets_subset_of=alternatives`, so the profile
refuses a stored default that is not one of the source's candidates. A source
with no stored default returns `None` rather than an arbitrary choice.

## Ordered roots

`OrderedRootsProfile` reads a stored root order from one polyadic relation whose
source side is explicitly empty, and reconciles it against the roots inferred
from the dependency relations you name. Stored order adds information; stored
membership may not contradict the inferred set. The profile reconciles over
exactly the dependency relations you pass, and is silent about any you omit,
because enumerating today's dependencies does not enforce anything about a
dependency added later. A curated ordered subset of parentless items is valid;
consumers that require every inferred root can call `is_exhaustive()`.
