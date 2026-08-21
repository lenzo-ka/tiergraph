"""Ordered-root and persisted-choice profile tests."""

from __future__ import annotations

from dataclasses import replace

import pytest

from tiergraph.core import (
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


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            lambda declaration: replace(
                declaration, sources=side(0, None, allow_empty=True)
            ),
            "explicitly empty source",
        ),
        (lambda declaration: replace(declaration, distinct_targets=False), "distinct"),
    ],
)
def test_root_role_refuses_each_bad_declaration(change: object, message: str) -> None:
    """Each root-role promise names the offending declared relation."""
    assert callable(change)
    graph = consumer_graph()
    changed = change(graph.relation_declarations[3])
    declarations_by_name = tuple(
        changed if item.name == ROOTS else item for item in graph.relation_declarations
    )
    malformed = replace(graph, relation_declarations=declarations_by_name)
    with pytest.raises(ValueError, match=f"ordered-root relation.*{message}"):
        OrderedRootsProfile(malformed, ROOTS, (DEPENDS,))


def test_root_role_requires_exactly_one_instance() -> None:
    """An absent stored root order is named instead of silently inferred."""
    graph = consumer_graph()
    neighbour = OrderedRootsProfile(graph, ROOTS, (DEPENDS,))
    assert neighbour.roots()
    malformed = replace(graph, polyadic_relations=graph.polyadic_relations[1:])
    with pytest.raises(ValueError, match="ordered-root relation.*0 instances"):
        OrderedRootsProfile(malformed, ROOTS, (DEPENDS,))


def test_roles_require_declared_polyadic_item_relations() -> None:
    """Missing declarations and boundary-sided roles name the requested role."""
    graph = consumer_graph()
    with pytest.raises(ValueError, match="ordered-root relation.*requires a polyadic"):
        OrderedRootsProfile(graph, name("missing"), (DEPENDS,))
    root_declaration = next(
        item for item in graph.relation_declarations if item.name == ROOTS
    )
    assert isinstance(root_declaration, PolyadicRelationDeclaration)
    boundary_side = RelationSideDeclaration(
        (RelationEndpointKind.BOUNDARY,), (NODE,), 0, None, True
    )
    boundary_roots = replace(root_declaration, targets=boundary_side)
    malformed = replace(
        graph,
        relation_declarations=tuple(
            boundary_roots if item.name == ROOTS else item
            for item in graph.relation_declarations
        ),
        polyadic_relations=graph.polyadic_relations[1:],
    )
    with pytest.raises(ValueError, match="ordered-root relation.*item-only"):
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


@pytest.mark.parametrize(
    ("relation_name", "defect", "message"),
    [
        (ALTERNATIVES, "unique", "source uniqueness"),
        (ALTERNATIVES, "distinct", "distinct targets"),
        (DEFAULT, "unique", "source uniqueness"),
        (DEFAULT, "distinct", "distinct targets"),
        (DEFAULT, "subset", "must declare targets_subset_of"),
    ],
)
def test_choice_role_refuses_each_bad_declaration(
    relation_name: QualifiedName, defect: str, message: str
) -> None:
    """Every choice-role constraint accepts its neighbour and names its offender."""
    graph = consumer_graph()
    choices = PersistedChoiceProfile(graph, ALTERNATIVES, DEFAULT)
    assert choices.default(ItemRef(NODE, 0)) == ItemRef(NODE, 3)
    declaration = next(
        item for item in graph.relation_declarations if item.name == relation_name
    )
    assert isinstance(declaration, PolyadicRelationDeclaration)
    if defect == "unique":
        changed = replace(declaration, unique_sources=False)
    elif defect == "distinct":
        changed = replace(declaration, distinct_targets=False)
    else:
        changed = replace(declaration, targets_subset_of=None)
    declarations_by_name = tuple(
        changed if item.name == relation_name else item
        for item in graph.relation_declarations
    )
    malformed = replace(graph, relation_declarations=declarations_by_name)
    with pytest.raises(ValueError, match=message):
        PersistedChoiceProfile(malformed, ALTERNATIVES, DEFAULT)


def test_choice_default_must_be_singleton_and_member_of_candidates() -> None:
    """Cardinality is a role promise and membership is a graph constraint."""
    graph = consumer_graph()
    default_declaration = next(
        item for item in graph.relation_declarations if item.name == DEFAULT
    )
    assert isinstance(default_declaration, PolyadicRelationDeclaration)
    widened = replace(default_declaration, targets=side(1, None))
    malformed = replace(
        graph,
        relation_declarations=tuple(
            widened if item.name == DEFAULT else item
            for item in graph.relation_declarations
        ),
    )
    with pytest.raises(ValueError, match="persisted-default relation.*exactly one"):
        PersistedChoiceProfile(malformed, ALTERNATIVES, DEFAULT)

    contradictory = replace(
        default_declaration,
        targets=replace(default_declaration.targets, allow_empty=True),
    )
    malformed = replace(
        graph,
        relation_declarations=tuple(
            contradictory if item.name == DEFAULT else item
            for item in graph.relation_declarations
        ),
    )
    with pytest.raises(
        ValueError,
        match=r"persisted-default relation '.*default'.*must not allow empty",
    ):
        PersistedChoiceProfile(malformed, ALTERNATIVES, DEFAULT)

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
