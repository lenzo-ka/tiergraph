"""Relation-instance references address the editing operation set."""

from __future__ import annotations

import re
from dataclasses import replace

import pytest

from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    DurablePolyadicRef,
    DurableRelationRef,
    Graph,
    GraphCarrier,
    GraphEditor,
    GraphValidationError,
    Item,
    ItemRef,
    Layer,
    LayerFact,
    LayerName,
    NamespaceDeclaration,
    OrphanedSubject,
    PolyadicInstanceRef,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    QualifiedName,
    RelationEndpointKind,
    RelationInstance,
    RelationInstanceRef,
    RelationSideDeclaration,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    XsdType,
    dumps,
    loads,
)
from tiergraph.core import RelationTarget
from tiergraph.wire import FORMAT_VERSION

NS = "urn:relation-targets"
TIER = QualifiedName(NS, "items")
ITEM_TYPE = QualifiedName(NS, "Item")
MEMBERS = QualifiedName(NS, "members")
BINARY = QualifiedName(NS, "binary")
P = QualifiedName(NS, "p")
Q = QualifiedName(NS, "q")
ORDER = QualifiedName(NS, "order")
NOTE = QualifiedName(NS, "note")
ITEM_NOTE = QualifiedName(NS, "item-note")
LAYER = LayerName(NS, "test")


def relation_value(name: QualifiedName, lexical: str) -> AttributeValue:
    """Return one relation-instance value of the declared scalar kind."""
    value_type = XsdType.INTEGER if name == ORDER else XsdType.STRING
    return AttributeValue(name, value_type, lexical)


def side() -> RelationSideDeclaration:
    """Return the shared one-item polyadic side."""
    return RelationSideDeclaration((RelationEndpointKind.ITEM,), (TIER,))


def fixture() -> Graph:
    """Return interleaved relations, anonymous duplicates, and layer facts."""

    def note(text: str) -> AttributeValue:
        return relation_value(NOTE, text)

    polyadic = (
        PolyadicRelationInstance(
            P, (ItemRef(TIER, 0),), (ItemRef(TIER, 1),), attributes=(note("p0"),)
        ),
        PolyadicRelationInstance(
            Q, (ItemRef(TIER, 1),), (ItemRef(TIER, 2),), attributes=(note("q0"),)
        ),
        PolyadicRelationInstance(
            P,
            (ItemRef(TIER, 2),),
            (ItemRef(TIER, 3),),
            "p1",
            (note("p1"),),
        ),
        PolyadicRelationInstance(
            Q, (ItemRef(TIER, 3),), (ItemRef(TIER, 4),), attributes=(note("q1"),)
        ),
        PolyadicRelationInstance(
            P, (ItemRef(TIER, 0),), (ItemRef(TIER, 1),), attributes=(note("p2"),)
        ),
    )
    layer = Layer(
        LAYER,
        (
            LayerFact(PolyadicInstanceRef(0), note("layer-p0")),
            LayerFact(PolyadicInstanceRef(4), note("layer-p4")),
            LayerFact(RelationInstanceRef(2), note("layer-r2")),
        ),
    )
    return Graph(
        (NamespaceDeclaration("rt", NS),),
        (Tier(TierDeclaration(TIER, "items"), tuple(Item(str(i)) for i in range(6))),),
        (
            SimpleRelationDeclaration(MEMBERS, TIER, ITEM_TYPE),
            BipartiteRelationDeclaration(BINARY, ITEM_TYPE, ITEM_TYPE),
            PolyadicRelationDeclaration(P, side(), side()),
            PolyadicRelationDeclaration(Q, side(), side()),
        ),
        (
            RelationInstance(
                BINARY,
                ItemRef(TIER, 0),
                ItemRef(TIER, 1),
                attributes=(note("r0"),),
            ),
            RelationInstance(
                BINARY,
                ItemRef(TIER, 1),
                ItemRef(TIER, 2),
                "r1",
                (note("r1"),),
            ),
            RelationInstance(
                BINARY,
                ItemRef(TIER, 2),
                ItemRef(TIER, 3),
                attributes=(note("r2"),),
            ),
        ),
        (
            AttributeDeclaration(
                ORDER, AttributeDomain.RELATION_INSTANCE, XsdType.INTEGER
            ),
            AttributeDeclaration(
                NOTE, AttributeDomain.RELATION_INSTANCE, XsdType.STRING
            ),
            AttributeDeclaration(ITEM_NOTE, AttributeDomain.ITEM, XsdType.STRING),
        ),
        polyadic_relations=polyadic,
        layers=(layer,),
    )


def assert_same(left: Graph, right: Graph) -> None:
    """Demand semantic and canonical-wire equality."""
    assert left == right
    assert dumps(left) == dumps(right)


def test_form_shape_matches_the_inline_replace_oracle() -> None:
    """O1: carrier routing and polyadic indexes preserve Form source order."""
    graph = fixture()
    source = (P, Q, P, P, Q)
    ordered = list(graph.polyadic_relations)
    used: set[int] = set()
    editor = graph.edit()
    for rank, declaration in enumerate(source):
        index = next(
            i
            for i, candidate in enumerate(ordered)
            if i not in used and candidate.declaration == declaration
        )
        used.add(index)
        value = relation_value(ORDER, str(rank))
        editor.set_attribute(PolyadicInstanceRef(index), value)
        ordered[index] = replace(
            ordered[index], attributes=(*ordered[index].attributes, value)
        )
    result = editor.freeze()
    oracle = replace(graph, polyadic_relations=tuple(ordered))
    assert_same(result, oracle)


def clts_fixture() -> Graph:
    """Return anonymous host, resolution, and projection relations."""
    host = QualifiedName(NS, "source-tone-host")
    resolves = QualifiedName(NS, "resolves")
    projects = QualifiedName(NS, "projects")
    polyadic = (
        PolyadicRelationInstance(host, (ItemRef(TIER, 0),), (ItemRef(TIER, 1),)),
        PolyadicRelationInstance(resolves, (ItemRef(TIER, 0),), (ItemRef(TIER, 2),)),
        PolyadicRelationInstance(projects, (ItemRef(TIER, 2),), (ItemRef(TIER, 3),)),
        PolyadicRelationInstance(host, (ItemRef(TIER, 1),), (ItemRef(TIER, 2),)),
        PolyadicRelationInstance(resolves, (ItemRef(TIER, 2),), (ItemRef(TIER, 4),)),
        PolyadicRelationInstance(projects, (ItemRef(TIER, 4),), (ItemRef(TIER, 0),)),
    )
    return Graph(
        (NamespaceDeclaration("rt", NS),),
        (Tier(TierDeclaration(TIER, "source-token"), tuple(Item() for _ in range(5))),),
        (
            SimpleRelationDeclaration(MEMBERS, TIER, ITEM_TYPE),
            *(
                PolyadicRelationDeclaration(name, side(), side())
                for name in (host, resolves, projects)
            ),
        ),
        attribute_declarations=(
            AttributeDeclaration(
                ORDER, AttributeDomain.RELATION_INSTANCE, XsdType.INTEGER
            ),
        ),
        polyadic_relations=polyadic,
    )


def test_clts_shape_matches_the_inline_replace_oracle() -> None:
    """O2: exact endpoints and resolves targets select positions, not values."""
    graph = clts_fixture()
    host = QualifiedName(NS, "source-tone-host")
    resolves_name = QualifiedName(NS, "resolves")
    projects = QualifiedName(NS, "projects")
    relations = list(graph.polyadic_relations)
    editor = graph.edit()
    used: set[int] = set()
    for rank, (source_index, target_index) in enumerate(((1, 2), (0, 1))):
        matches = [
            index
            for index, candidate in enumerate(relations)
            if index not in used
            and candidate.declaration == host
            and candidate.sources == (ItemRef(TIER, source_index),)
            and candidate.targets == (ItemRef(TIER, target_index),)
        ]
        assert len(matches) == 1
        index = matches[0]
        used.add(index)
        value = relation_value(ORDER, str(rank))
        editor.set_attribute(PolyadicInstanceRef(index), value)
        relations[index] = replace(relations[index], attributes=(value,))
    project_rank = 0
    for token_index, supported in enumerate((True, False, True)):
        if not supported:
            continue
        token = ItemRef(TIER, token_index)
        resolved = [
            relation
            for relation in relations
            if relation.declaration == resolves_name and relation.sources == (token,)
        ]
        assert len(resolved) == 1
        matches = [
            index
            for index, candidate in enumerate(relations)
            if index not in used
            and candidate.declaration == projects
            and candidate.sources == resolved[0].targets
        ]
        assert len(matches) == 1
        index = matches[0]
        used.add(index)
        value = relation_value(ORDER, str(project_rank))
        editor.set_attribute(PolyadicInstanceRef(index), value)
        relations[index] = replace(relations[index], attributes=(value,))
        project_rank += 1
    assert_same(editor.freeze(), replace(graph, polyadic_relations=tuple(relations)))


@pytest.mark.parametrize(
    ("reference", "legacy"),
    [
        (RelationInstanceRef(1), 1),
        (DurableRelationRef("r1"), "r1"),
        (PolyadicInstanceRef(2), "p1"),
        (DurablePolyadicRef("p1"), "p1"),
    ],
)
def test_reference_routes_equal_existing_routes(
    reference: RelationTarget, legacy: int | str
) -> None:
    """O3: all three operations use the right carrier and equal displacement."""
    graph = fixture()
    value = relation_value(ORDER, "7")
    assert_same(
        graph.set_attribute(reference, value), graph.set_attribute(legacy, value)
    )
    assert_same(
        graph.remove_attribute(reference, NOTE), graph.remove_attribute(legacy, NOTE)
    )
    by_reference = graph.edit().remove_relation(reference)
    by_legacy = graph.edit().remove_relation(legacy)
    assert_same(by_reference.freeze(), by_legacy.freeze())
    assert by_reference.displacement() == by_legacy.displacement()


@pytest.mark.parametrize(
    "reference",
    [
        RelationInstanceRef(1),
        DurableRelationRef("r1"),
        PolyadicInstanceRef(2),
        DurablePolyadicRef("p1"),
    ],
)
def test_frozen_twins_equal_editor_routes(reference: RelationTarget) -> None:
    """O4: frozen twins delegate; editor methods mutate and return themselves."""
    graph = fixture()
    original = dumps(graph)
    value = relation_value(ORDER, "8")
    editor = graph.edit()
    assert editor.set_attribute(reference, value) is editor
    assert_same(graph.set_attribute(reference, value), editor.freeze())
    editor = graph.edit()
    assert editor.remove_attribute(reference, NOTE) is editor
    assert_same(graph.remove_attribute(reference, NOTE), editor.freeze())
    editor = graph.edit()
    assert editor.remove_relation(reference) is editor
    assert_same(graph.remove_relation(reference), editor.freeze())
    assert dumps(graph) == original


def test_anonymous_polyadic_removal_remaps_layers_and_displacement() -> None:
    """O5: removal remaps later facts and orphans the departed coordinate."""
    editor = fixture().edit()
    editor.remove_relation(PolyadicInstanceRef(0))
    displacement = editor.displacement()
    assert displacement.polyadic_relations == {1: 0, 2: 1, 3: 2, 4: 3}
    assert displacement.departed_polyadic_relations == frozenset({0})
    facts = editor.freeze().layers[0].facts
    subjects = tuple(fact.subject for fact in facts)
    assert OrphanedSubject(GraphCarrier.POLYADIC_RELATIONS, 0) in subjects
    assert PolyadicInstanceRef(3) in subjects
    assert RelationInstanceRef(2) in subjects


def test_setting_twice_replaces_by_name() -> None:
    """O6: set_attribute retains one value under a name, with the last value."""
    editor = fixture().edit()
    editor.set_attribute(PolyadicInstanceRef(0), relation_value(ORDER, "1"))
    editor.set_attribute(PolyadicInstanceRef(0), relation_value(ORDER, "2"))
    values = editor.freeze().polyadic_relations[0].attributes
    order = tuple(value for value in values if value.name == ORDER)
    assert order == (relation_value(ORDER, "2"),)


def test_legacy_targets_keep_routing_and_exact_messages() -> None:
    """O7: int and str routing plus every legacy refusal remain unchanged."""
    graph = fixture()
    value = relation_value(ORDER, "3")
    by_index = graph.set_attribute(0, value)
    assert value in by_index.relations[0].attributes
    assert value not in by_index.polyadic_relations[0].attributes
    by_id = graph.set_attribute("r1", value)
    assert value in by_id.relations[1].attributes
    assert all(
        value not in relation.attributes for relation in by_id.polyadic_relations
    )
    messages: tuple[tuple[object, str], ...] = (
        (
            3,
            "relation instance index 3 is outside the graph's 3 bipartite relation instances",
        ),
        ("missing", "no relation instance carries durable id 'missing'"),
        (
            ItemRef(TIER, 0),
            "relation instance target must be a bipartite instance index or a durable id",
        ),
    )
    for target, message in messages:
        with pytest.raises(GraphValidationError, match=re.escape(message) + "$"):
            graph.set_attribute(target, value)  # type: ignore[arg-type]
    with pytest.raises(
        GraphValidationError,
        match=re.escape(f"relation instance 2 carries no attribute '{ORDER}' to remove")
        + "$",
    ):
        graph.remove_attribute("p1", ORDER)


def test_wire_round_trip_and_format_version_are_unchanged() -> None:
    """O8: editing changes neither the wire contract nor tuple ordering."""
    result = fixture().set_attribute(PolyadicInstanceRef(4), relation_value(ORDER, "9"))
    assert loads(dumps(result)) == result
    assert FORMAT_VERSION == "0.3.0"


def assert_atomic_refusal(target: object, fragment: str, *, polyadic: bool) -> None:
    """Demand a reference refusal before any editor write."""
    editor = fixture().edit()
    before = editor.freeze()
    with pytest.raises(GraphValidationError, match=re.escape(fragment)):
        editor.set_attribute(target, relation_value(ORDER, "1"))  # type: ignore[arg-type]
    assert editor.freeze() == before
    if polyadic:
        assert len(before.polyadic_relations) == 5
    else:
        assert len(before.relations) == 3


@pytest.mark.parametrize(
    ("target", "fragment", "polyadic"),
    [
        (
            PolyadicInstanceRef(5),
            "polyadic relation instance reference index 5 is outside",
            True,
        ),
        (
            PolyadicInstanceRef(-1),
            "polyadic relation instance reference index -1 is outside",
            True,
        ),
        (PolyadicInstanceRef(True), "has non-integral index True", True),
        (
            PolyadicInstanceRef(1.0),  # type: ignore[arg-type]
            "has non-integral index 1.0",
            True,
        ),
        (
            RelationInstanceRef(3),
            "relation instance reference index 3 is outside",
            False,
        ),
        (
            RelationInstanceRef(-1),
            "relation instance reference index -1 is outside",
            False,
        ),
        (RelationInstanceRef(True), "has non-integral index True", False),
        (
            RelationInstanceRef(1.0),  # type: ignore[arg-type]
            "has non-integral index 1.0",
            False,
        ),
    ],
)
def test_structural_reference_refusals_are_atomic(
    target: object, fragment: str, polyadic: bool
) -> None:
    """R1-R4: bounds, negatives, bool, and float never reach list indexing."""
    assert_atomic_refusal(target, fragment, polyadic=polyadic)


@pytest.mark.parametrize(
    ("target", "fragment"),
    [
        (
            DurableRelationRef("p1"),
            "names a polyadic relation instance; use DurablePolyadicRef",
        ),
        (
            DurablePolyadicRef("r1"),
            "names a bipartite relation instance; use DurableRelationRef",
        ),
        (
            DurableRelationRef("missing"),
            "no relation instance carries durable id 'missing'",
        ),
        (
            DurablePolyadicRef("missing"),
            "no relation instance carries durable id 'missing'",
        ),
    ],
)
def test_durable_reference_kind_and_unknown_refusals_are_atomic(
    target: object, fragment: str
) -> None:
    """R5-R7: durable references search only the carrier they name."""
    assert_atomic_refusal(
        target, fragment, polyadic=isinstance(target, DurablePolyadicRef)
    )


@pytest.mark.parametrize(
    ("reference_type", "subject"),
    [
        (DurableRelationRef, "durable relation reference"),
        (DurablePolyadicRef, "durable polyadic reference"),
    ],
)
@pytest.mark.parametrize(
    ("durable_id", "problem"),
    [
        (None, "must not be empty"),
        ("", "must not be empty"),
        (7, "must be a string"),
    ],
)
@pytest.mark.parametrize(
    "operation", ["set_attribute", "remove_attribute", "remove_relation"]
)
@pytest.mark.parametrize("frozen", [False, True], ids=["editor", "graph"])
def test_invalid_durable_reference_ids_refuse_every_edit_route(
    reference_type: type[DurableRelationRef] | type[DurablePolyadicRef],
    subject: str,
    durable_id: object,
    problem: str,
    operation: str,
    frozen: bool,
) -> None:
    """R8: malformed durable ids never select an anonymous relation."""
    graph = fixture()
    target = reference_type(durable_id)  # type: ignore[arg-type]
    receiver: Graph | GraphEditor = graph if frozen else graph.edit()
    with pytest.raises(
        GraphValidationError,
        match=rf"^{re.escape(subject)} .* {re.escape(problem)}$",
    ):
        if operation == "set_attribute":
            receiver.set_attribute(target, relation_value(ORDER, "1"))
        elif operation == "remove_attribute":
            receiver.remove_attribute(target, NOTE)
        else:
            receiver.remove_relation(target)
    if isinstance(receiver, GraphEditor):
        assert receiver.freeze() == graph


@pytest.mark.parametrize(
    ("carrier", "inside", "outside"),
    [
        (
            GraphCarrier.POLYADIC_RELATIONS,
            PolyadicInstanceRef(0),
            PolyadicInstanceRef(2),
        ),
        (GraphCarrier.RELATIONS, RelationInstanceRef(0), RelationInstanceRef(2)),
    ],
)
def test_relation_references_honor_geometric_seals(
    carrier: GraphCarrier, inside: object, outside: object
) -> None:
    """R9: removal honors seals while attribute values remain mutable."""
    sealed = fixture().seal(carrier, 2)
    editor = sealed.edit()
    before = editor.freeze()
    with pytest.raises(GraphValidationError, match="relation removal would move"):
        editor.remove_relation(inside)  # type: ignore[arg-type]
    assert editor.freeze() == before
    assert sealed.remove_relation(outside)  # type: ignore[arg-type]
    changed = sealed.set_attribute(inside, relation_value(ORDER, "4"))  # type: ignore[arg-type]
    assert changed.seals == sealed.seals


def test_int_and_relation_reference_are_equivalent_under_a_seal() -> None:
    """R10: the legacy and explicit bipartite positions honor the same seal."""
    sealed = fixture().seal(GraphCarrier.RELATIONS, 2)
    for target in (0, RelationInstanceRef(0)):
        editor = sealed.edit()
        before = editor.freeze()
        with pytest.raises(GraphValidationError, match="relation removal would move"):
            editor.remove_relation(target)
        assert editor.freeze() == before
    value = relation_value(ORDER, "4")
    assert_same(
        sealed.set_attribute(0, value),
        sealed.set_attribute(RelationInstanceRef(0), value),
    )
    assert_same(
        sealed.remove_attribute(0, NOTE),
        sealed.remove_attribute(RelationInstanceRef(0), NOTE),
    )


def test_relation_reference_does_not_bypass_attribute_domain() -> None:
    """R11: an item value still demands an item reference target."""
    value = AttributeValue(ITEM_NOTE, XsdType.STRING, "wrong domain")
    with pytest.raises(
        GraphValidationError, match="item attribute target must be an item reference"
    ):
        fixture().set_attribute(PolyadicInstanceRef(0), value)


def test_new_absent_attribute_message_names_the_polyadic_carrier() -> None:
    """R12: new target forms receive a carrier-specific absence subject."""
    with pytest.raises(
        GraphValidationError,
        match=re.escape(
            f"polyadic relation instance 0 carries no attribute '{ORDER}' to remove"
        )
        + "$",
    ):
        fixture().remove_attribute(PolyadicInstanceRef(0), ORDER)
