# Profiles

A profile reads a checked role out of ordinary graph declarations. It adds no
new kind of node and changes nothing in the store; it validates that specific
declarations and instances support one interpretation, then answers questions
against them. For a profile that holds the graph it reads, constructing it is
the check: an invalid arrangement raises instead of returning a profile that
would give wrong answers later. A profile that holds no graph settles nothing at
construction, and its check runs where the graph is finally read;
[`SpanViewProfile`](#registering-a-profile-of-your-own) is that case.

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

The `default` relation declares `targets_subset_of=alternatives`, and a stored
default that is not one of the source's candidates violates it, so the graph
carrying it cannot be constructed at all. The profile's own check is over the
declarations, and one of the things it insists on is that `targets_subset_of` be
declared; the instance-level guard is `Graph` validation, one layer down. A
source with no stored default returns `None` rather than an arbitrary choice.

## Ordered roots

`OrderedRootsProfile` reads a stored root order from one polyadic relation whose
source side is explicitly empty, and reconciles it against the roots inferred
from the dependency relations you name. Stored order adds information; stored
membership may not contradict the inferred set. The profile reconciles over
exactly the dependency relations you pass, and is silent about any you omit,
because enumerating today's dependencies does not enforce anything about a
dependency added later. A curated ordered subset of parentless items is valid;
consumers that require every inferred root can call `is_exhaustive()`.

## Asking which profiles a graph satisfies

Constructing a profile checks one role you already had in mind. `PROFILES` is
the registry that turns that into a question you can ask without naming a
profile first: bind the roles you have, and it reports every registered profile
against your graph.

```python
from tiergraph import (
    PROFILES,
    Graph,
    Item,
    ItemRef,
    NamespaceDeclaration,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    QualifiedName,
    RelationEndpointKind,
    RelationSideDeclaration,
    RoleBinding,
    Tier,
    TierDeclaration,
)

ns = "https://example.com/tree"
nodes = QualifiedName(ns, "nodes")
members = QualifiedName(ns, "members")
roots = QualifiedName(ns, "roots")
one = RelationSideDeclaration((RelationEndpointKind.ITEM,), (nodes,), 1, 1)
many = RelationSideDeclaration((RelationEndpointKind.ITEM,), (nodes,), 1, None)
none = RelationSideDeclaration((RelationEndpointKind.ITEM,), (nodes,), 0, 0, True)
graph = Graph(
    (NamespaceDeclaration("t", ns),),
    (
        Tier(
            TierDeclaration(nodes, "Nodes"),
            (Item("root"), Item("left"), Item("right")),
        ),
    ),
    (
        PolyadicRelationDeclaration(
            members, one, many, unique_sources=True, distinct_targets=True, acyclic=True
        ),
        PolyadicRelationDeclaration(roots, none, many, distinct_targets=True),
    ),
    polyadic_relations=(
        PolyadicRelationInstance(
            members, (ItemRef(nodes, 0),), (ItemRef(nodes, 1), ItemRef(nodes, 2))
        ),
        PolyadicRelationInstance(roots, (), (ItemRef(nodes, 0),)),
    ),
)

bindings: RoleBinding = {
    "relation": members,
    "root_relation": roots,
    "dependency_relations": (members,),
}
for report in PROFILES.reports(graph, bindings):
    print(report.profile, report.outcome.value)
print()
roots_report = PROFILES.report("tiergraph.ordered-roots", graph, bindings)
for condition in roots_report.unconfirmed:
    print("undecided:", condition)
```

```text
tiergraph.json-value not_applicable
tiergraph.ordered-containment satisfied
tiergraph.ordered-roots satisfied_as_checked
tiergraph.persisted-choice not_applicable
tiergraph.span-view not_applicable

undecided: roots implied by a dependency relation the caller did not enumerate
undecided: whether the stored roots are the whole inferred set, which OrderedRootsProfile.is_exhaustive answers separately
```

Four outcomes keep that answer honest. `satisfied` means the check decided every
condition the profile declares. `satisfied_as_checked` means it decided the ones
it can and the report names the rest, so a partial answer cannot be read as a
whole guarantee: ordered roots reconciles over the dependency relations you
enumerate, and says so. `refused` carries the reason. `not_applicable` means a
required role was left unbound and no check ran, so a profile you bound no roles
for is reported as unanswered rather than quietly counted as a pass.

`PROFILES.satisfied(graph, bindings)` returns the reports of the profiles whose
check ran and accepted -- the `satisfied` and `satisfied_as_checked` ones. A
profile reported `not_applicable` refused nothing and is still absent, because
an unanswered question is not an accepted one. It returns reports rather than
names because a bare name would read as a whole guarantee.

## Registering a profile of your own

A profile is a `GraphProfile` subclass. It names itself, names the roles it
reads, states in prose the conditions its `check` decides and any it leaves
undecided, and supplies two witnesses: one arrangement its check must accept and
one it must refuse.

Registration tests those claims rather than taking them. A profile that names no
condition its check decides is refused outright, because a check that states
nothing cannot be told from one that passes always, and a caller counts a
registered profile as coverage. That alone would still leave room for a profile
to name conditions and check none of them, so `register` runs both witnesses and
admits the profile only when its check tells them apart.

Population is explicit. Nothing is discovered by scanning, because a discovered
profile is one nobody decided to trust, and import order would then decide what
a caller is told a graph satisfies. Register what you mean to offer, into
`PROFILES` or into a `ProfileRegistry` of your own.

`SpanViewProfile` shows why the check and the naming are separate. It holds no
graph, so constructing one settles nothing about any particular graph; the
registered `tiergraph.span-view` profile projects the view, which is where the
names it carries are reconciled with what the graph stores.

The clock profile and the path profiles are absent from the registry. A path
profile interprets a path vocabulary rather than asserting anything about a
graph, so satisfaction is not a question it answers.
