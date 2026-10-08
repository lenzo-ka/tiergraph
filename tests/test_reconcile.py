"""Path commitment and clock reconciliation preserve declared graph content."""

from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest

from tests.test_clock import (
    CLOCK as SPINE_CLOCK,
)
from tests.test_clock import (
    SEGMENT as SPINE_SEGMENT,
)
from tests.test_clock import (
    SPINE_GAP,
    SPINE_TICK,
    spine_fixture,
)
from tiergraph import (
    ARCTIC,
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    Boundary,
    BoundaryRef,
    BoundarySide,
    ChildCombination,
    ClockProfile,
    DurableBoundaryRef,
    DurableItemRef,
    DurablePolyadicRef,
    DurableRelationRef,
    FoldDeclaration,
    FoldTransition,
    Graph,
    GraphValidationError,
    Item,
    ItemRef,
    Journal,
    Layer,
    LayerFact,
    LayerName,
    NamespaceDeclaration,
    OrphanedSubject,
    PathChoice,
    PathPlan,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    QualifiedName,
    RelationEndpointKind,
    RelationInstance,
    RelationSideDeclaration,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    XsdType,
    anchored_boundary,
    commit_path,
    contain_by_time,
    retime,
)
from tiergraph.fold import AttributeValuation

NS = "urn:test:reconcile"
NODES = QualifiedName(NS, "nodes")
SYLLABLES = QualifiedName(NS, "syllables")
TOKENS = QualifiedName(NS, "tokens")
NODE = QualifiedName(NS, "node")
SYLLABLE = QualifiedName(NS, "syllable")
TOKEN = QualifiedName(NS, "token")
NODE_MEMBERS = QualifiedName(NS, "node-members")
SYLLABLE_MEMBERS = QualifiedName(NS, "syllable-members")
TOKEN_MEMBERS = QualifiedName(NS, "token-members")
NEXT = QualifiedName(NS, "next")
HAS_SYLLABLE = QualifiedName(NS, "has-syllable")
TOKEN_WORD = QualifiedName(NS, "token-word")
BOUNDARY_LINK = QualifiedName(NS, "boundary-link")
WEIGHT = QualifiedName(NS, "weight")
SPAN = QualifiedName(NS, "source-span")
ITEM_NOTE = QualifiedName(NS, "item-note")
BOUNDARY_NOTE = QualifiedName(NS, "boundary-note")
RELATION_NOTE = QualifiedName(NS, "relation-note")
POLYADIC_NOTE = QualifiedName(NS, "polyadic-note")
PROVENANCE = LayerName(NS, "imputed")


def path_graph() -> Graph:
    """Return a two-alternative word lattice with crossing token links."""
    nodes = Tier(
        TierDeclaration(NODES, "Nodes"),
        tuple(
            Item(
                label,
                (
                    AttributeValue(WEIGHT, XsdType.DOUBLE, "0"),
                    AttributeValue(SPAN, XsdType.STRING, span),
                ),
            )
            for label, span in (
                ("s", "0:0"),
                ("a", "0:6"),
                ("b", "0:6"),
                ("f", "6:6"),
            )
        ),
    )
    syllables = Tier(
        TierDeclaration(SYLLABLES, "Syllables"),
        (Item("a-syllable"), Item("b-syllable")),
    )
    tokens = Tier(
        TierDeclaration(TOKENS, "Tokens"),
        (Item("money"), Item("date")),
    )
    item_side = RelationSideDeclaration(
        (RelationEndpointKind.ITEM,), (NODES,), maximum=1
    )
    syllable_side = RelationSideDeclaration((RelationEndpointKind.ITEM,), (SYLLABLES,))
    bare = Graph(
        (NamespaceDeclaration("r", NS),),
        (nodes, syllables, tokens),
        (
            SimpleRelationDeclaration(NODE_MEMBERS, NODES, NODE),
            SimpleRelationDeclaration(SYLLABLE_MEMBERS, SYLLABLES, SYLLABLE),
            SimpleRelationDeclaration(TOKEN_MEMBERS, TOKENS, TOKEN),
            BipartiteRelationDeclaration(NEXT, NODE, NODE, acyclic=True),
            PolyadicRelationDeclaration(
                HAS_SYLLABLE,
                item_side,
                syllable_side,
                unique_sources=True,
                single_parent=True,
                acyclic=True,
            ),
            BipartiteRelationDeclaration(TOKEN_WORD, TOKEN, NODE),
            BipartiteRelationDeclaration(
                BOUNDARY_LINK,
                NODE,
                NODE,
                RelationEndpointKind.BOUNDARY,
                RelationEndpointKind.BOUNDARY,
            ),
        ),
        attribute_declarations=(
            AttributeDeclaration(WEIGHT, AttributeDomain.ITEM, XsdType.DOUBLE),
            AttributeDeclaration(SPAN, AttributeDomain.ITEM, XsdType.STRING),
            AttributeDeclaration(ITEM_NOTE, AttributeDomain.ITEM, XsdType.STRING),
            AttributeDeclaration(
                BOUNDARY_NOTE, AttributeDomain.BOUNDARY, XsdType.STRING
            ),
            AttributeDeclaration(
                RELATION_NOTE, AttributeDomain.RELATION_INSTANCE, XsdType.STRING
            ),
            AttributeDeclaration(
                POLYADIC_NOTE,
                AttributeDomain.RELATION_INSTANCE,
                XsdType.STRING,
            ),
        ),
    )
    refs = {
        item.durable_id: ItemRef(NODES, index) for index, item in enumerate(nodes.items)
    }
    relations = (
        RelationInstance(NEXT, refs["s"], refs["a"]),
        RelationInstance(NEXT, refs["a"], refs["f"]),
        RelationInstance(NEXT, refs["s"], refs["b"]),
        RelationInstance(NEXT, refs["b"], refs["f"]),
        RelationInstance(
            TOKEN_WORD,
            ItemRef(TOKENS, 0),
            DurableItemRef("a"),
            "a-link",
        ),
        RelationInstance(
            TOKEN_WORD,
            ItemRef(TOKENS, 1),
            DurableItemRef("b"),
            "b-link",
        ),
        RelationInstance(
            BOUNDARY_LINK,
            DurableBoundaryRef(DurableItemRef("b"), BoundarySide.BEFORE),
            DurableBoundaryRef(NODES, BoundarySide.AFTER),
            "b-boundary-link",
        ),
    )
    polyadic = (
        PolyadicRelationInstance(
            HAS_SYLLABLE,
            (ItemRef(NODES, 1),),
            (ItemRef(SYLLABLES, 0),),
            "a-substructure",
        ),
        PolyadicRelationInstance(
            HAS_SYLLABLE,
            (ItemRef(NODES, 2),),
            (ItemRef(SYLLABLES, 1),),
            "b-substructure",
        ),
    )
    b_boundary = DurableBoundaryRef(DurableItemRef("b"), BoundarySide.BEFORE)
    layer = Layer(
        PROVENANCE,
        (
            LayerFact(
                DurableItemRef("a"), AttributeValue(ITEM_NOTE, XsdType.STRING, "rank:1")
            ),
            LayerFact(
                DurableItemRef("b"), AttributeValue(ITEM_NOTE, XsdType.STRING, "rank:2")
            ),
            LayerFact(
                b_boundary,
                AttributeValue(BOUNDARY_NOTE, XsdType.STRING, "measured:b"),
            ),
            LayerFact(
                DurableRelationRef("b-link"),
                AttributeValue(RELATION_NOTE, XsdType.STRING, "surface:b"),
            ),
            LayerFact(
                DurablePolyadicRef("b-substructure"),
                AttributeValue(POLYADIC_NOTE, XsdType.STRING, "lexical:b"),
            ),
            LayerFact(
                OrphanedSubject(NODES, ItemRef(NODES, 99)),
                AttributeValue(ITEM_NOTE, XsdType.STRING, "legacy"),
            ),
        ),
    )
    return replace(
        bare,
        relations=relations,
        polyadic_relations=polyadic,
        boundary_values=(
            Boundary(
                b_boundary,
                (AttributeValue(BOUNDARY_NOTE, XsdType.STRING, "boundary:b"),),
            ),
        ),
        layers=(layer,),
    )


def path_plan(graph: Graph) -> PathPlan[float]:
    """Prepare the generic path topology over the word alternatives."""
    declaration = FoldDeclaration(
        "words",
        graph,
        AttributeValuation("weight", WEIGHT, (NODES,)),
        ARCTIC,
        lambda value, _label: cast(float, value),
        (FoldTransition(NEXT, ChildCombination.OR),),
        roots=(ItemRef(NODES, 0),),
    )
    return PathPlan.prepare(declaration)


def test_commit_path_keeps_chosen_substructure_offsets_and_provenance() -> None:
    """A chosen crossing-link path keeps only its own durable graph content."""
    graph = path_graph()
    plan = path_plan(graph)
    journal = Journal(stage="aligned")
    result = commit_path(
        plan,
        (("s", "a", "f"),),
        containment=HAS_SYLLABLE,
        journal=journal,
    )

    ids = {item.durable_id for tier in result.tiers for item in tier.items}
    assert ids == {"s", "a", "f", "a-syllable", "money", "date"}
    assert result.resolve_item(DurableItemRef("a")) == ItemRef(NODES, 1)
    chosen = result.tiers[0].items[1]
    assert AttributeValue(SPAN, XsdType.STRING, "0:6") in chosen.attributes
    assert (
        LayerFact(
            DurableItemRef("a"), AttributeValue(ITEM_NOTE, XsdType.STRING, "rank:1")
        )
        in result.layers[0].facts
    )
    assert any(
        isinstance(fact.subject, OrphanedSubject) for fact in result.layers[0].facts
    )
    assert (
        len(
            [
                relation
                for relation in result.relations
                if relation.declaration == TOKEN_WORD
            ]
        )
        == 1
    )
    patch = journal.to_patch()
    assert patch.apply(graph) == result
    assert patch.invert().apply(result) == graph
    assert all(
        operation.opcode.to_data()["opcode"] != "commit_path"
        for operation in patch.operations
    )


def test_commit_path_keeps_boundaries_anchored_to_surviving_items() -> None:
    """A surviving durable anchor retains its values, facts, and links."""
    graph = path_graph()
    surviving = anchored_boundary(graph, BoundaryRef(NODES, 3))
    graph = replace(
        graph,
        relations=(
            *graph.relations,
            RelationInstance(
                BOUNDARY_LINK,
                surviving,
                DurableBoundaryRef(NODES, BoundarySide.AFTER),
                "surviving-boundary-link",
            ),
        ),
        boundary_values=(
            *graph.boundary_values,
            Boundary(
                surviving,
                (AttributeValue(BOUNDARY_NOTE, XsdType.STRING, "before-f"),),
            ),
        ),
        layers=(
            replace(
                graph.layers[0],
                facts=(
                    *graph.layers[0].facts,
                    LayerFact(
                        surviving,
                        AttributeValue(BOUNDARY_NOTE, XsdType.STRING, "live:f"),
                    ),
                ),
            ),
        ),
    )

    result = commit_path(path_plan(graph), ("s", "a", "f"))

    assert result.resolve_boundary(surviving) == BoundaryRef(NODES, 2)
    assert any(boundary.reference == surviving for boundary in result.boundary_values)
    assert (
        LayerFact(
            surviving,
            AttributeValue(BOUNDARY_NOTE, XsdType.STRING, "live:f"),
        )
        in result.layers[0].facts
    )
    assert any(
        relation.durable_id == "surviving-boundary-link"
        for relation in result.relations
    )


def test_commit_path_removes_departing_positional_boundary_content() -> None:
    """A positional boundary with no image is withdrawn before its item."""
    graph = path_graph()
    departing = BoundaryRef(NODES, 2)
    graph = replace(
        graph,
        boundary_values=(
            Boundary(
                departing,
                (AttributeValue(BOUNDARY_NOTE, XsdType.STRING, "before-b"),),
            ),
        ),
        layers=(
            replace(
                graph.layers[0],
                facts=(
                    *(
                        fact
                        for fact in graph.layers[0].facts
                        if fact.value.name != BOUNDARY_NOTE
                    ),
                    LayerFact(
                        departing,
                        AttributeValue(BOUNDARY_NOTE, XsdType.STRING, "measured:b"),
                    ),
                ),
            ),
        ),
    )

    result = commit_path(path_plan(graph), ("s", "a", "f"))

    assert all(boundary.reference != departing for boundary in result.boundary_values)
    assert all(fact.subject != departing for fact in result.layers[0].facts)


@pytest.mark.parametrize(
    "path, message",
    (
        ((), "must not be empty"),
        (("a", "f"), "does not start"),
        (("s", "f"), "not a lattice edge"),
        (("s", "a"), "does not end"),
        (("s", "a", "a", "f"), "repeats"),
        (("s", "missing", "f"), "not in the lattice"),
        (("s", ItemRef(SYLLABLES, 0), "f"), "not in the lattice"),
    ),
)
def test_commit_path_refuses_nonpaths(path: object, message: str) -> None:
    """Only a complete root-to-sink path in the prepared inventory commits."""
    with pytest.raises(GraphValidationError, match=message):
        commit_path(path_plan(path_graph()), cast(PathChoice, path))


def test_commit_path_argument_shapes_are_explicit() -> None:
    """Wrong lattice, path-step, provenance, and containment shapes refuse."""
    plan = path_plan(path_graph())
    with pytest.raises(TypeError, match="PathPlan"):
        commit_path(cast(PathPlan[float], object()), ("s", "a", "f"))
    with pytest.raises(TypeError, match="iterable"):
        commit_path(plan, cast(tuple[str, ...], "s"))
    with pytest.raises(TypeError, match="steps"):
        commit_path(plan, ("s", cast(str, 1), "f"))
    with pytest.raises(GraphValidationError, match="exactly one"):
        commit_path(plan, (("s", "a", "f"), ("s", "b", "f")))
    with pytest.raises(TypeError, match="provenance may be unavailable"):
        commit_path(plan, cast(PathChoice, None))
    with pytest.raises(TypeError, match="QualifiedName"):
        commit_path(plan, ("s", "a", "f"), containment=(cast(QualifiedName, 1),))


@pytest.mark.parametrize(
    "containment",
    (NEXT, QualifiedName(NS, "missing-containment")),
)
def test_commit_path_refuses_invalid_containment_declarations(
    containment: QualifiedName,
) -> None:
    """Containment names must select the ordered polyadic traversal contract."""
    with pytest.raises(GraphValidationError, match="ordered, item-only polyadic"):
        commit_path(
            path_plan(path_graph()),
            ("s", "a", "f"),
            containment=containment,
        )


def test_commit_path_without_substructure_names_is_valid() -> None:
    """An empty containment list removes lattice alternatives only."""
    result = commit_path(path_plan(path_graph()), ("s", "a", "f"))
    assert {item.durable_id for item in result.tiers[0].items} == {"s", "a", "f"}
    assert {item.durable_id for item in result.tiers[1].items} == {
        "a-syllable",
        "b-syllable",
    }


def test_commit_path_deduplicates_substructure_reached_by_two_declarations() -> None:
    """A shared descendant reached twice is retained and visited once."""
    graph = path_graph()
    other = QualifiedName(NS, "other-substructure")
    declaration = cast(
        PolyadicRelationDeclaration,
        next(
            candidate
            for candidate in graph.relation_declarations
            if candidate.name == HAS_SYLLABLE
        ),
    )
    graph = replace(
        graph,
        relation_declarations=(
            *graph.relation_declarations,
            replace(declaration, name=other),
        ),
        polyadic_relations=(
            *graph.polyadic_relations,
            PolyadicRelationInstance(
                other,
                (ItemRef(NODES, 1),),
                (ItemRef(SYLLABLES, 0),),
            ),
        ),
    )
    result = commit_path(
        path_plan(graph),
        ("s", "a", "f"),
        containment=(HAS_SYLLABLE, other),
    )
    assert result.resolve_item(DurableItemRef("a-syllable")) == ItemRef(SYLLABLES, 0)


CLOCK = QualifiedName(NS, "clock")
PARENTS = QualifiedName(NS, "parents")
CHILDREN = QualifiedName(NS, "children")
CLOCK_TYPE = QualifiedName(NS, "tick")
PARENT_TYPE = QualifiedName(NS, "event")
CHILD_TYPE = PARENT_TYPE
CLOCK_MEMBERS = QualifiedName(NS, "clock-members")
PARENT_MEMBERS = QualifiedName(NS, "parent-members")
CHILD_MEMBERS = QualifiedName(NS, "child-members")
BINDING = QualifiedName(NS, "binding")
CONTAINS = QualifiedName(NS, "contains")
BINARY_CONTAINS = QualifiedName(NS, "binary-contains")
UNIT = QualifiedName(NS, "unit")
UNTIMED = QualifiedName(NS, "untimed")
OTHER_CONTAINS = QualifiedName(NS, "other-contains")


def timing_graph(*, binary: bool = False) -> Graph:
    """Return timed parent and child tiers with deliberately wrong containment."""
    tiers = (
        Tier(
            TierDeclaration(CLOCK, "Clock"),
            tuple(Item(f"tick-{index}") for index in range(6)),
        ),
        Tier(
            TierDeclaration(PARENTS, "Parents"),
            (Item("parent-0"), Item("parent-1")),
        ),
        Tier(
            TierDeclaration(CHILDREN, "Children"),
            tuple(Item(f"child-{index}") for index in range(4)),
        ),
    )
    parent_side = RelationSideDeclaration(
        (RelationEndpointKind.ITEM,), (PARENTS,), maximum=1
    )
    child_side = RelationSideDeclaration((RelationEndpointKind.ITEM,), (CHILDREN,))
    declarations = (
        SimpleRelationDeclaration(CLOCK_MEMBERS, CLOCK, CLOCK_TYPE),
        SimpleRelationDeclaration(PARENT_MEMBERS, PARENTS, PARENT_TYPE),
        SimpleRelationDeclaration(CHILD_MEMBERS, CHILDREN, CHILD_TYPE),
        BipartiteRelationDeclaration(
            BINDING,
            PARENT_TYPE,
            CLOCK_TYPE,
            RelationEndpointKind.BOUNDARY,
            RelationEndpointKind.BOUNDARY,
        ),
        (
            BipartiteRelationDeclaration(
                BINARY_CONTAINS,
                PARENT_TYPE,
                CHILD_TYPE,
                single_parent=True,
                acyclic=True,
            )
            if binary
            else PolyadicRelationDeclaration(
                CONTAINS,
                parent_side,
                child_side,
                unique_sources=True,
                single_parent=True,
                acyclic=True,
            )
        ),
    )
    bare = Graph(
        (NamespaceDeclaration("r", NS),),
        tiers,
        declarations,
        attribute_declarations=(
            AttributeDeclaration(UNIT, AttributeDomain.DOCUMENT, XsdType.STRING),
        ),
        attributes=(AttributeValue(UNIT, XsdType.STRING, "samples"),),
    )
    bindings = tuple(
        RelationInstance(
            BINDING,
            anchored_boundary(bare, BoundaryRef(tier, source)),
            anchored_boundary(bare, BoundaryRef(CLOCK, target)),
        )
        for tier, positions in (
            (PARENTS, (0, 3, 6)),
            (CHILDREN, (0, 1, 3, 4, 6)),
        )
        for source, target in enumerate(positions)
    )
    if binary:
        relations = (
            *bindings,
            RelationInstance(
                BINARY_CONTAINS, ItemRef(PARENTS, 0), ItemRef(CHILDREN, 0)
            ),
            RelationInstance(
                BINARY_CONTAINS, ItemRef(PARENTS, 1), ItemRef(CHILDREN, 1)
            ),
            RelationInstance(
                BINARY_CONTAINS, ItemRef(PARENTS, 0), ItemRef(CHILDREN, 2)
            ),
        )
        polyadic: tuple[PolyadicRelationInstance, ...] = ()
    else:
        relations = bindings
        polyadic = (
            PolyadicRelationInstance(
                CONTAINS,
                (ItemRef(PARENTS, 0),),
                (ItemRef(CHILDREN, 0), ItemRef(CHILDREN, 1), ItemRef(CHILDREN, 2)),
                "contain-0",
            ),
            PolyadicRelationInstance(
                CONTAINS,
                (ItemRef(PARENTS, 1),),
                (ItemRef(CHILDREN, 3),),
                "contain-1",
            ),
        )
    return replace(bare, relations=relations, polyadic_relations=polyadic)


def clock_profile(graph: Graph) -> ClockProfile:
    """Build the full exact structural clock used by reconciliation tests."""
    return ClockProfile(graph, CLOCK, BINDING, None, UNIT)


def untimed_child_profile() -> ClockProfile:
    """Return the timing fixture with its child tier explicitly untimed."""
    graph = timing_graph()
    child = replace(
        graph.tiers[2],
        attributes=(AttributeValue(UNTIMED, XsdType.BOOLEAN, "true"),),
    )
    graph = replace(
        graph,
        tiers=(graph.tiers[0], graph.tiers[1], child),
        relations=graph.relations[:3],
        attribute_declarations=(
            *graph.attribute_declarations,
            AttributeDeclaration(UNTIMED, AttributeDomain.TIER, XsdType.BOOLEAN),
        ),
    )
    return ClockProfile(graph, CLOCK, BINDING, None, UNIT, untimed_attribute=UNTIMED)


def test_retime_rebinds_exact_positions_and_patch_round_trips() -> None:
    """Retime updates binding endpoints in place and expands its journal."""
    graph = timing_graph()
    journal = Journal(stage="audio")
    result = retime(
        clock_profile(graph), CHILDREN, (0, 1, 2, 4, 5), offset=1, journal=journal
    )
    checked = clock_profile(result)
    assert tuple(
        checked.clock_index(BoundaryRef(CHILDREN, index)) for index in range(5)
    ) == (1, 2, 3, 5, 6)
    assert [record.operation for record in journal.records] == [
        "set_endpoints",
        "set_endpoints",
        "set_endpoints",
    ]
    patch = journal.to_patch()
    assert patch.apply(graph) == result
    assert patch.invert().apply(result) == graph
    assert retime(checked, CHILDREN, (1, 2, 3, 5, 6)) == result


@pytest.mark.parametrize(
    "tier, alignment, offset, error, message",
    (
        (CLOCK, (0,), 0, GraphValidationError, "clock tier"),
        (QualifiedName(NS, "missing"), (0,), 0, GraphValidationError, "not declared"),
        (CHILDREN, (0, 1), 0, GraphValidationError, "needs 5"),
        (CHILDREN, (0, 2, 1, 4, 5), 0, GraphValidationError, "monotone"),
        (CHILDREN, (0, 1, 3, 4, 6), 1, GraphValidationError, "between"),
        (CHILDREN, (0, 1, 3, 4, cast(int, True)), 0, TypeError, "integers"),
        (CHILDREN, (0, 1, 3, 4, 6), cast(int, True), TypeError, "offset"),
    ),
)
def test_retime_refusals(
    tier: QualifiedName,
    alignment: tuple[int, ...],
    offset: int,
    error: type[Exception],
    message: str,
) -> None:
    """Retime refuses malformed, incomplete, reversed, and out-of-range input."""
    with pytest.raises(error, match=message):
        retime(clock_profile(timing_graph()), tier, alignment, offset=offset)


def test_retime_argument_types_and_untimed_tier_refuse() -> None:
    """The profile and tier are typed and an explicitly untimed tier is not rebound."""
    profile = clock_profile(timing_graph())
    with pytest.raises(TypeError, match="ClockProfile"):
        retime(cast(ClockProfile, object()), CHILDREN, (0, 1, 3, 4, 6))
    with pytest.raises(TypeError, match="QualifiedName"):
        retime(profile, cast(QualifiedName, "children"), (0, 1, 3, 4, 6))
    with pytest.raises(GraphValidationError, match="is untimed"):
        retime(untimed_child_profile(), CHILDREN, (0, 1, 3, 4, 6))


def test_retime_refuses_a_spine_only_profile_and_skips_other_relations() -> None:
    """Retime requires bindings and ignores nonbinding instances in a full graph."""
    structural_graph = spine_fixture(((0, 0), (0, 1), (1, 0)))
    structural_graph = replace(
        structural_graph,
        tiers=(
            *structural_graph.tiers,
            Tier(TierDeclaration(SPINE_SEGMENT, "segment"), ()),
        ),
    )
    structural = ClockProfile.from_boundary_values(
        structural_graph,
        SPINE_CLOCK,
        tick_attribute=SPINE_TICK,
        gap_attribute=SPINE_GAP,
    )
    with pytest.raises(GraphValidationError, match="full clock profile"):
        retime(structural, SPINE_SEGMENT, ())
    graph = timing_graph(binary=True)
    assert retime(clock_profile(graph), CHILDREN, (0, 1, 3, 4, 6)) == graph


def test_contain_by_time_rebuilds_polyadic_groups_and_is_a_fixpoint() -> None:
    """Midpoint grouping preserves relation identities and becomes an exact no-op."""
    graph = timing_graph()
    journal = Journal(stage="audio")
    result = contain_by_time(
        clock_profile(graph), CONTAINS, PARENTS, CHILDREN, journal=journal
    )
    assert result.polyadic_relations == (
        PolyadicRelationInstance(
            CONTAINS,
            (ItemRef(PARENTS, 0),),
            (ItemRef(CHILDREN, 0), ItemRef(CHILDREN, 1)),
            "contain-0",
        ),
        PolyadicRelationInstance(
            CONTAINS,
            (ItemRef(PARENTS, 1),),
            (ItemRef(CHILDREN, 2), ItemRef(CHILDREN, 3)),
            "contain-1",
        ),
    )
    assert [record.operation for record in journal.records] == [
        "set_endpoints",
        "set_endpoints",
    ]
    assert contain_by_time(clock_profile(result), CONTAINS, PARENTS, CHILDREN) == result


def test_contain_by_time_rebuilds_binary_links_and_adds_missing_child() -> None:
    """Binary containment retains existing instances and adds absent links."""
    result = contain_by_time(
        clock_profile(timing_graph(binary=True)),
        BINARY_CONTAINS,
        PARENTS,
        CHILDREN,
    )
    links = tuple(
        (
            result.resolve_item(cast(ItemRef | DurableItemRef, relation.left)),
            result.resolve_item(cast(ItemRef | DurableItemRef, relation.right)),
        )
        for relation in result.relations
        if relation.declaration == BINARY_CONTAINS
    )
    assert links == (
        (ItemRef(PARENTS, 0), ItemRef(CHILDREN, 0)),
        (ItemRef(PARENTS, 0), ItemRef(CHILDREN, 1)),
        (ItemRef(PARENTS, 1), ItemRef(CHILDREN, 2)),
        (ItemRef(PARENTS, 1), ItemRef(CHILDREN, 3)),
    )


def test_contain_by_time_accepts_a_declared_callable_rule() -> None:
    """A domain caller may choose exact parents without changing the substrate."""
    graph = timing_graph(binary=True)

    def later_parent(
        _span: tuple[int, int],
        parents: tuple[tuple[ItemRef, tuple[int, int]], ...],
    ) -> ItemRef:
        return parents[-1][0]

    result = contain_by_time(
        clock_profile(graph),
        BINARY_CONTAINS,
        PARENTS,
        CHILDREN,
        rule=later_parent,
    )
    assert all(
        result.resolve_item(cast(ItemRef | DurableItemRef, relation.left))
        == ItemRef(PARENTS, 1)
        for relation in result.relations
        if relation.declaration == BINARY_CONTAINS
    )


def test_contain_by_time_refuses_untimed_and_unassigned_children() -> None:
    """Both timing participation and default-rule coverage are mandatory."""
    with pytest.raises(GraphValidationError, match="two timed tiers"):
        contain_by_time(untimed_child_profile(), CONTAINS, PARENTS, CHILDREN)
    graph = timing_graph()
    shifted = retime(clock_profile(graph), CHILDREN, (6, 6, 6, 6, 6))
    with pytest.raises(GraphValidationError, match="to 0 parents"):
        contain_by_time(clock_profile(shifted), CONTAINS, PARENTS, CHILDREN)


def test_contain_by_time_removes_extra_binary_and_polyadic_groups() -> None:
    """Instances outside the requested tiers and empty parent groups are explicit edits."""
    binary = timing_graph(binary=True)
    binary = replace(
        binary,
        relations=(
            *binary.relations,
            RelationInstance(
                BINARY_CONTAINS, ItemRef(PARENTS, 0), ItemRef(CHILDREN, 0)
            ),
            RelationInstance(BINARY_CONTAINS, ItemRef(PARENTS, 1), ItemRef(PARENTS, 0)),
        ),
    )
    rebuilt = contain_by_time(clock_profile(binary), BINARY_CONTAINS, PARENTS, CHILDREN)
    assert all(
        rebuilt.resolve_item(cast(ItemRef | DurableItemRef, relation.right)).tier
        == CHILDREN
        for relation in rebuilt.relations
        if relation.declaration == BINARY_CONTAINS
    )
    assert (
        sum(
            rebuilt.resolve_item(cast(ItemRef | DurableItemRef, relation.right))
            == ItemRef(CHILDREN, 0)
            for relation in rebuilt.relations
            if relation.declaration == BINARY_CONTAINS
        )
        == 1
    )

    graph = timing_graph()
    collapsed = retime(clock_profile(graph), CHILDREN, (0, 1, 1, 2, 2))
    rebuilt = contain_by_time(clock_profile(collapsed), CONTAINS, PARENTS, CHILDREN)
    assert len(rebuilt.polyadic_relations) == 1
    assert rebuilt.polyadic_relations[0].sources == (ItemRef(PARENTS, 0),)


def test_contain_by_time_adds_missing_polyadic_group_and_skips_other_kind() -> None:
    """A missing parent group is added while an unrelated declaration is untouched."""
    graph = timing_graph()
    base = cast(
        PolyadicRelationDeclaration,
        next(
            candidate
            for candidate in graph.relation_declarations
            if candidate.name == CONTAINS
        ),
    )
    graph = replace(
        graph,
        relation_declarations=(
            *graph.relation_declarations,
            replace(base, name=OTHER_CONTAINS),
        ),
        polyadic_relations=(
            graph.polyadic_relations[0],
            PolyadicRelationInstance(
                OTHER_CONTAINS,
                (ItemRef(PARENTS, 0),),
                (ItemRef(CHILDREN, 0),),
            ),
        ),
    )
    result = contain_by_time(clock_profile(graph), CONTAINS, PARENTS, CHILDREN)
    assert any(
        relation.declaration == CONTAINS and relation.sources == (ItemRef(PARENTS, 1),)
        for relation in result.polyadic_relations
    )
    assert any(
        relation.declaration == OTHER_CONTAINS for relation in result.polyadic_relations
    )


@pytest.mark.parametrize(
    "rule, message",
    (
        ("overlap", "midpoint.*callable"),
        (lambda _span, _parents: None, "assigned no parent"),
        (lambda _span, _parents: ItemRef(CHILDREN, 0), "not a parent item"),
    ),
)
def test_contain_by_time_refuses_undeclared_rules(rule: object, message: str) -> None:
    """Rules must be named or return one of the supplied parent coordinates."""
    with pytest.raises((ValueError, GraphValidationError), match=message):
        contain_by_time(
            clock_profile(timing_graph()),
            CONTAINS,
            PARENTS,
            CHILDREN,
            rule=cast(str, rule),
        )


def test_contain_by_time_argument_and_declaration_refusals() -> None:
    """Wrong profiles, names, tiers, and declaration shapes refuse explicitly."""
    graph = timing_graph()
    profile = clock_profile(graph)
    with pytest.raises(TypeError, match="ClockProfile"):
        contain_by_time(cast(ClockProfile, object()), CONTAINS, PARENTS, CHILDREN)
    with pytest.raises(TypeError, match="QualifiedName"):
        contain_by_time(profile, cast(QualifiedName, "contains"), PARENTS, CHILDREN)
    with pytest.raises(GraphValidationError, match="parent tier.*not declared"):
        contain_by_time(profile, CONTAINS, QualifiedName(NS, "missing"), CHILDREN)
    with pytest.raises(GraphValidationError, match="not declared containment"):
        contain_by_time(profile, NEXT, PARENTS, CHILDREN)
    with pytest.raises(GraphValidationError, match="parent-to-child"):
        contain_by_time(profile, CONTAINS, CHILDREN, PARENTS)
