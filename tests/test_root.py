"""Ordered-root and persisted-choice profile tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import pytest

from tiergraph.core import (
    BipartiteRelationDeclaration,
    Graph,
    Item,
    ItemRef,
    NamespaceDeclaration,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    QualifiedName,
    RelationEndpointKind,
    RelationSideDeclaration,
    Tier,
    TierDeclaration,
)
from tiergraph.root import OrderedRootsProfile, PersistedChoiceProfile
from tiergraph.schema import validation_errors
from tiergraph.wire import FORMAT_VERSION, dump_bytes, loads, to_data

NS = "urn:tiergraph:test:roots"


def name(local_name: str) -> QualifiedName:
    """Return one test-qualified name."""
    return QualifiedName(NS, local_name)


NODE = name("node")
ROOTS = name("roots")
DEPENDS = name("depends")
ALTERNATIVES = name("alternatives")
DEFAULT = name("default")


def side(
    minimum: int = 1,
    maximum: int | None = None,
    *,
    allow_empty: bool = False,
) -> RelationSideDeclaration:
    """Declare an item-only side on the fixture tier."""
    return RelationSideDeclaration(
        (RelationEndpointKind.ITEM,), (NODE,), minimum, maximum, allow_empty
    )


def declarations() -> tuple[PolyadicRelationDeclaration, ...]:
    """Return the consumer-shaped root, dependency, and choice contracts."""
    return (
        PolyadicRelationDeclaration(
            ROOTS,
            side(0, 0, allow_empty=True),
            side(0, allow_empty=True),
            distinct_targets=True,
        ),
        PolyadicRelationDeclaration(DEPENDS, side(), side(), acyclic=True),
        PolyadicRelationDeclaration(ALTERNATIVES, side(1, 1), side(), True, True),
        PolyadicRelationDeclaration(
            DEFAULT,
            side(1, 1),
            side(1, 1),
            True,
            True,
            targets_subset_of=ALTERNATIVES,
        ),
    )


def consumer_graph() -> Graph:
    """Build ordered roots and one persisted delivery choice."""
    references = tuple(ItemRef(NODE, index) for index in range(4))
    parent, child, first, second = references
    return Graph(
        (NamespaceDeclaration("r", NS),),
        (
            Tier(
                TierDeclaration(NODE, "Nodes"),
                tuple(Item(durable_id) for durable_id in ("p", "c", "a", "b")),
            ),
        ),
        declarations(),
        polyadic_relations=(
            PolyadicRelationInstance(ROOTS, (), (second, parent, first)),
            PolyadicRelationInstance(DEPENDS, (parent,), (child,)),
            PolyadicRelationInstance(ALTERNATIVES, (parent,), (first, second)),
            PolyadicRelationInstance(DEFAULT, (parent,), (second,)),
        ),
    )


def profiles(graph: Graph) -> tuple[OrderedRootsProfile, PersistedChoiceProfile]:
    """Bind both public roles to the fixture graph."""
    return (
        OrderedRootsProfile(graph, ROOTS, (DEPENDS,)),
        PersistedChoiceProfile(graph, ALTERNATIVES, DEFAULT),
    )


def test_real_consumer_case_round_trips_byte_identically() -> None:
    """Roots, candidate order, and persisted default survive canonical wire."""
    graph = consumer_graph()
    before = dump_bytes(graph)
    decoded = loads(before)
    roots, choices = profiles(decoded)
    parent, _child, first, second = tuple(ItemRef(NODE, i) for i in range(4))
    assert roots.roots() == (second, parent, first)
    assert roots.inferred() == (parent, first, second)
    assert choices.candidates(parent) == (first, second)
    assert choices.default(parent) == second
    assert dump_bytes(decoded) == before
    assert validation_errors(to_data(graph), FORMAT_VERSION) == []
    assert validation_errors(to_data(decoded), FORMAT_VERSION) == []


def test_stored_roots_may_be_an_ordered_subset_of_inferred_roots() -> None:
    """Stored incidence may curate and reorder parentless inferred items."""
    graph = consumer_graph()
    roots = graph.polyadic_relations[0]
    neighbour = replace(
        graph,
        polyadic_relations=(
            replace(roots, targets=tuple(reversed(roots.targets))),
            *graph.polyadic_relations[1:],
        ),
    )
    assert OrderedRootsProfile(neighbour, ROOTS, (DEPENDS,)).roots() == tuple(
        reversed(roots.targets)
    )
    curated = replace(
        graph,
        polyadic_relations=(
            replace(roots, targets=roots.targets[:-1]),
            *graph.polyadic_relations[1:],
        ),
    )
    profile = OrderedRootsProfile(curated, ROOTS, (DEPENDS,))
    assert profile.roots() == roots.targets[:-1]
    assert profile.inferred() == (ItemRef(NODE, 0), ItemRef(NODE, 2), ItemRef(NODE, 3))


def test_stored_roots_refuse_a_declared_non_parentless_item() -> None:
    """A declared item with incoming dependency incidence remains unsound."""
    graph = consumer_graph()
    roots = graph.polyadic_relations[0]
    child = ItemRef(NODE, 1)
    contradictory = replace(
        graph,
        polyadic_relations=(
            replace(roots, targets=(*roots.targets, child)),
            *graph.polyadic_relations[1:],
        ),
    )
    with pytest.raises(
        ValueError,
        match=r"non-parentless roots.*'index': 1.*must be a subset.*parentless",
    ):
        OrderedRootsProfile(contradictory, ROOTS, (DEPENDS,))


def test_is_exhaustive_distinguishes_complete_and_curated_roots() -> None:
    """Consumers can opt into equality with the complete inferred set."""
    graph = consumer_graph()
    complete = OrderedRootsProfile(graph, ROOTS, (DEPENDS,))
    assert complete.is_exhaustive()
    roots = graph.polyadic_relations[0]
    curated_graph = replace(
        graph,
        polyadic_relations=(
            replace(roots, targets=roots.targets[:-1]),
            *graph.polyadic_relations[1:],
        ),
    )
    assert not OrderedRootsProfile(curated_graph, ROOTS, (DEPENDS,)).is_exhaustive()


# Both roles refuse through an if-chain rather than a table, so nothing at
# runtime can be asked what the population of refusals is. The two case lists
# below are that population, transcribed once and read against the source: every
# ``raise`` reachable from ``OrderedRootsProfile.__post_init__`` and from
# ``PersistedChoiceProfile.__post_init__`` that a declaration can provoke,
# including the ones inside the shared ``_polyadic_declaration`` and
# ``_item_only`` helpers, which each role reaches under its own role name. The
# two root refusals a declaration cannot provoke -- the stored-instance count
# and the parentless-subset reconciliation -- read the instances rather than the
# declarations and are covered by their own tests above and below.
#
# A case returns the whole call, not just a mutated declaration, because two of
# the refusals are provoked by the name a caller asks for rather than by
# anything stored in the graph, and a fixture that could only mutate
# declarations could not reach them.
RootCase = Callable[[Graph], tuple[Graph, QualifiedName, tuple[QualifiedName, ...]]]
ChoiceCase = Callable[[Graph], tuple[Graph, QualifiedName, QualifiedName]]

BOUNDARY_KINDS = (RelationEndpointKind.ITEM, RelationEndpointKind.BOUNDARY)
BIPARTITE = name("bipartite")


def declared(graph: Graph, relation: QualifiedName) -> PolyadicRelationDeclaration:
    """Return one polyadic declaration from the fixture by name."""
    match = next(item for item in graph.relation_declarations if item.name == relation)
    assert isinstance(match, PolyadicRelationDeclaration)
    return match


def with_declaration(graph: Graph, declaration: PolyadicRelationDeclaration) -> Graph:
    """Return the graph with one same-named declaration swapped in."""
    return replace(
        graph,
        relation_declarations=tuple(
            declaration if item.name == declaration.name else item
            for item in graph.relation_declarations
        ),
    )


def with_bipartite(graph: Graph) -> Graph:
    """Return the graph carrying one declaration that is not polyadic."""
    return replace(
        graph,
        relation_declarations=(
            *graph.relation_declarations,
            BipartiteRelationDeclaration(BIPARTITE, name("kind"), name("kind")),
        ),
    )


def boundary_side(side_declaration: RelationSideDeclaration) -> RelationSideDeclaration:
    """Return the same side widened to admit boundary endpoints."""
    return replace(side_declaration, endpoint_kinds=BOUNDARY_KINDS)


def spoil_side(graph: Graph, relation: QualifiedName, *, sources: bool) -> Graph:
    """Return the graph with one side of one relation admitting boundaries."""
    declaration = declared(graph, relation)
    widened = (
        replace(declaration, sources=boundary_side(declaration.sources))
        if sources
        else replace(declaration, targets=boundary_side(declaration.targets))
    )
    return with_declaration(graph, widened)


ROOT_CASES: tuple[tuple[str, RootCase, str], ...] = (
    (
        "root-undeclared",
        lambda graph: (graph, name("missing"), (DEPENDS,)),
        r"ordered-root relation.*missing.*requires a polyadic",
    ),
    (
        "root-not-polyadic",
        lambda graph: (with_bipartite(graph), BIPARTITE, (DEPENDS,)),
        r"ordered-root relation.*bipartite.*requires a polyadic",
    ),
    (
        "root-boundary-sources",
        lambda graph: (spoil_side(graph, ROOTS, sources=True), ROOTS, (DEPENDS,)),
        r"ordered-root relation.*roots.*item-only",
    ),
    (
        "root-boundary-targets",
        lambda graph: (
            replace(
                spoil_side(graph, ROOTS, sources=False),
                polyadic_relations=tuple(
                    item
                    for item in graph.polyadic_relations
                    if item.declaration != ROOTS
                ),
            ),
            ROOTS,
            (DEPENDS,),
        ),
        r"ordered-root relation.*roots.*item-only",
    ),
    (
        "root-sources-not-empty",
        lambda graph: (
            with_declaration(
                graph,
                replace(
                    declared(graph, ROOTS), sources=side(0, None, allow_empty=True)
                ),
            ),
            ROOTS,
            (DEPENDS,),
        ),
        r"ordered-root relation.*explicitly empty source",
    ),
    (
        "root-repeatable-targets",
        lambda graph: (
            with_declaration(
                graph, replace(declared(graph, ROOTS), distinct_targets=False)
            ),
            ROOTS,
            (DEPENDS,),
        ),
        r"ordered-root relation.*distinct targets",
    ),
    (
        "dependency-undeclared",
        lambda graph: (graph, ROOTS, (name("missing"),)),
        r"root dependency relation.*missing.*requires a polyadic",
    ),
    (
        "dependency-not-polyadic",
        lambda graph: (with_bipartite(graph), ROOTS, (BIPARTITE,)),
        r"root dependency relation.*bipartite.*requires a polyadic",
    ),
    (
        "dependency-boundary-sources",
        lambda graph: (spoil_side(graph, DEPENDS, sources=True), ROOTS, (DEPENDS,)),
        r"root dependency relation.*depends.*item-only",
    ),
    (
        "dependency-boundary-targets",
        lambda graph: (spoil_side(graph, DEPENDS, sources=False), ROOTS, (DEPENDS,)),
        r"root dependency relation.*depends.*item-only",
    ),
)


@pytest.mark.parametrize(
    ("build", "message"),
    [(build, message) for _, build, message in ROOT_CASES],
    ids=[case_id for case_id, _, _ in ROOT_CASES],
)
def test_root_role_refuses_each_bad_declaration(build: RootCase, message: str) -> None:
    """Each root-role promise names the offending declared relation."""
    graph = consumer_graph()
    assert OrderedRootsProfile(graph, ROOTS, (DEPENDS,)).roots()
    malformed, root_relation, dependencies = build(graph)
    with pytest.raises(ValueError, match=message):
        OrderedRootsProfile(malformed, root_relation, dependencies)


@pytest.mark.parametrize("instances", (0, 2))
def test_root_role_requires_exactly_one_instance(instances: int) -> None:
    """Neither an absent nor a doubled stored root order is silently resolved."""
    graph = consumer_graph()
    neighbour = OrderedRootsProfile(graph, ROOTS, (DEPENDS,))
    assert neighbour.roots()
    stored = graph.polyadic_relations[0]
    assert stored.declaration == ROOTS
    # Both halves of the count matter and they fail differently. With none
    # stored, ``roots()`` would raise StopIteration rather than return an order;
    # with two, it would return the first and silently discard the second, which
    # is the reading a caller cannot see going wrong.
    replacement = (
        () if instances == 0 else (stored, replace(stored, targets=(ItemRef(NODE, 2),)))
    )
    malformed = replace(
        graph, polyadic_relations=(*replacement, *graph.polyadic_relations[1:])
    )
    with pytest.raises(
        ValueError, match=f"ordered-root relation.*{instances} instances"
    ):
        OrderedRootsProfile(malformed, ROOTS, (DEPENDS,))


def test_unrestricted_root_domain_ignores_cross_domain_dependency_endpoints() -> None:
    """An unrestricted role admits all items; a restricted role ignores outer edges."""
    graph = consumer_graph()
    root_declaration = next(
        item for item in graph.relation_declarations if item.name == ROOTS
    )
    dependency = next(
        item for item in graph.relation_declarations if item.name == DEPENDS
    )
    assert isinstance(root_declaration, PolyadicRelationDeclaration)
    assert isinstance(dependency, PolyadicRelationDeclaration)
    outer = name("outer")
    outer_ref = ItemRef(outer, 0)
    broad_dependency = replace(
        dependency,
        sources=replace(dependency.sources, tiers=None),
        targets=replace(dependency.targets, tiers=None),
    )
    mixed = replace(
        graph,
        tiers=(*graph.tiers, Tier(TierDeclaration(outer, "Outer"), (Item("o"),))),
        relation_declarations=tuple(
            broad_dependency if item.name == DEPENDS else item
            for item in graph.relation_declarations
        ),
        polyadic_relations=(
            *graph.polyadic_relations,
            PolyadicRelationInstance(DEPENDS, (outer_ref,), (ItemRef(NODE, 0),)),
            PolyadicRelationInstance(DEPENDS, (ItemRef(NODE, 2),), (outer_ref,)),
        ),
    )
    # The two mixed edges each have one endpoint outside the root relation's tier.
    assert OrderedRootsProfile(mixed, ROOTS, (DEPENDS,)).inferred() == (
        ItemRef(NODE, 0),
        ItemRef(NODE, 2),
        ItemRef(NODE, 3),
    )
    unrestricted = replace(
        root_declaration,
        targets=replace(side(0, allow_empty=True), tiers=None),
    )
    unrestricted_graph = replace(
        mixed,
        relation_declarations=tuple(
            unrestricted if item.name == ROOTS else item
            for item in mixed.relation_declarations
        ),
    )
    with pytest.raises(ValueError, match="non-parentless roots"):
        OrderedRootsProfile(unrestricted_graph, ROOTS, (DEPENDS,))


Spoil = Callable[[PolyadicRelationDeclaration], PolyadicRelationDeclaration]


def spoiled_choice(
    graph: Graph, relation: QualifiedName, change: Spoil
) -> tuple[Graph, QualifiedName, QualifiedName]:
    """Return the choice call with one declaration changed in place."""
    changed = with_declaration(graph, change(declared(graph, relation)))
    return changed, ALTERNATIVES, DEFAULT


CHOICE_CASES: tuple[tuple[str, ChoiceCase, str], ...] = (
    (
        "alternatives-undeclared",
        lambda graph: (graph, name("missing"), DEFAULT),
        r"alternatives relation.*missing.*requires a polyadic",
    ),
    (
        "alternatives-not-polyadic",
        lambda graph: (with_bipartite(graph), BIPARTITE, DEFAULT),
        r"alternatives relation.*bipartite.*requires a polyadic",
    ),
    (
        "default-undeclared",
        lambda graph: (graph, ALTERNATIVES, name("missing")),
        r"persisted-default relation.*missing.*requires a polyadic",
    ),
    (
        "default-not-polyadic",
        lambda graph: (with_bipartite(graph), ALTERNATIVES, BIPARTITE),
        r"persisted-default relation.*bipartite.*requires a polyadic",
    ),
    (
        "alternatives-boundary-sources",
        lambda graph: (
            spoil_side(graph, ALTERNATIVES, sources=True),
            ALTERNATIVES,
            DEFAULT,
        ),
        r"alternatives relation.*alternatives.*item-only",
    ),
    (
        "alternatives-boundary-targets",
        lambda graph: (
            spoil_side(graph, ALTERNATIVES, sources=False),
            ALTERNATIVES,
            DEFAULT,
        ),
        r"alternatives relation.*alternatives.*item-only",
    ),
    (
        "default-boundary-sources",
        lambda graph: (spoil_side(graph, DEFAULT, sources=True), ALTERNATIVES, DEFAULT),
        r"persisted-default relation.*default.*item-only",
    ),
    (
        "default-boundary-targets",
        lambda graph: (
            spoil_side(graph, DEFAULT, sources=False),
            ALTERNATIVES,
            DEFAULT,
        ),
        r"persisted-default relation.*default.*item-only",
    ),
    (
        "alternatives-repeatable-sources",
        lambda graph: spoiled_choice(
            graph,
            ALTERNATIVES,
            lambda declaration: replace(declaration, unique_sources=False),
        ),
        r"alternatives relation.*source uniqueness",
    ),
    (
        "alternatives-repeatable-targets",
        lambda graph: spoiled_choice(
            graph,
            ALTERNATIVES,
            lambda declaration: replace(declaration, distinct_targets=False),
        ),
        r"alternatives relation.*distinct targets",
    ),
    (
        "default-repeatable-sources",
        lambda graph: spoiled_choice(
            graph,
            DEFAULT,
            lambda declaration: replace(declaration, unique_sources=False),
        ),
        r"persisted-default relation.*source uniqueness",
    ),
    (
        "default-repeatable-targets",
        lambda graph: spoiled_choice(
            graph,
            DEFAULT,
            lambda declaration: replace(declaration, distinct_targets=False),
        ),
        r"persisted-default relation.*distinct targets",
    ),
    (
        "default-optional-target",
        lambda graph: spoiled_choice(
            graph,
            DEFAULT,
            lambda declaration: replace(
                declaration, targets=side(1, 1, allow_empty=True)
            ),
        ),
        r"persisted-default relation.*must not allow empty",
    ),
    (
        "default-target-floor-below-one",
        lambda graph: spoiled_choice(
            graph,
            DEFAULT,
            lambda declaration: replace(declaration, targets=side(0, 1)),
        ),
        r"persisted-default relation.*exactly one target",
    ),
    (
        "default-unbounded-targets",
        lambda graph: spoiled_choice(
            graph,
            DEFAULT,
            lambda declaration: replace(declaration, targets=side(1, None)),
        ),
        r"persisted-default relation.*exactly one target",
    ),
    (
        "default-unconstrained-membership",
        lambda graph: spoiled_choice(
            graph,
            DEFAULT,
            lambda declaration: replace(declaration, targets_subset_of=None),
        ),
        r"persisted-default relation.*must declare targets_subset_of",
    ),
)


@pytest.mark.parametrize(
    ("build", "message"),
    [(build, message) for _, build, message in CHOICE_CASES],
    ids=[case_id for case_id, _, _ in CHOICE_CASES],
)
def test_choice_role_refuses_each_bad_declaration(
    build: ChoiceCase, message: str
) -> None:
    """Every choice-role constraint accepts its neighbour and names its offender."""
    graph = consumer_graph()
    choices = PersistedChoiceProfile(graph, ALTERNATIVES, DEFAULT)
    assert choices.default(ItemRef(NODE, 0)) == ItemRef(NODE, 3)
    malformed, alternatives, default = build(graph)
    with pytest.raises(ValueError, match=message):
        PersistedChoiceProfile(malformed, alternatives, default)


def test_choice_default_membership_is_refused_by_the_graph_not_the_role() -> None:
    """A stored default outside the candidates never reaches the profile.

    The singleton cardinality this default declares is a role promise, and the
    enumeration above holds all three of its branches. Membership is a
    different kind of claim: it constrains the stored instance rather than the
    declaration, and the kernel refuses it while the graph is being built, so
    no ``PersistedChoiceProfile`` is ever constructed over a graph that could
    return a default the candidates do not contain.
    """
    graph = consumer_graph()
    default = graph.polyadic_relations[-1]
    with pytest.raises(ValueError, match="target outside.*alternatives.*membership"):
        replace(
            graph,
            polyadic_relations=(
                *graph.polyadic_relations[:-1],
                replace(default, targets=(ItemRef(NODE, 1),)),
            ),
        )


def test_absent_default_is_distinct_from_contradictory_default() -> None:
    """No stored default is optional; an invalid stored default is never ignored."""
    graph = consumer_graph()
    without_default = replace(graph, polyadic_relations=graph.polyadic_relations[:-1])
    choices = PersistedChoiceProfile(without_default, ALTERNATIVES, DEFAULT)
    assert choices.default(ItemRef(NODE, 0)) is None
    assert choices.default(ItemRef(NODE, 1)) is None
    assert choices.candidates(ItemRef(NODE, 1)) == ()
