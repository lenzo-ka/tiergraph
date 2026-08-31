"""One operation set over two carriers: a frozen graph and a mutable editor."""

from __future__ import annotations

from dataclasses import replace

import pytest

from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    Boundary,
    BoundaryRef,
    BoundarySide,
    DurableBoundaryRef,
    DurableItemRef,
    Graph,
    GraphCarrier,
    GraphEditor,
    GraphValidationError,
    Item,
    ItemRef,
    NamespaceDeclaration,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    QualifiedName,
    RelationEndpointKind,
    RelationInstance,
    RelationSideDeclaration,
    Seal,
    SealDeclaration,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    XsdType,
    dumps,
    loads,
)
from tiergraph.core import EditDeclaration, EditTarget

NS = "urn:edit"


def name(local: str) -> QualifiedName:
    """Return an expanded test name."""
    return QualifiedName(NS, local)


WORD = name("word")
PHRASE = name("phrase")
WORDS = name("words")
PHRASES = name("phrases")
COVERS = name("covers")
ANCHORS = name("anchors")
WORD_TYPE = name("Word")
PHRASE_TYPE = name("Phrase")
SCORE = name("score")
LABEL = name("label")
TITLE = name("title")
WEIGHT = name("weight")
KIND = name("kind")
CONFIDENCE = name("confidence")
MARK = name("mark")

DECLARATIONS = (
    AttributeDeclaration(SCORE, AttributeDomain.ITEM, XsdType.INTEGER),
    AttributeDeclaration(LABEL, AttributeDomain.TIER, XsdType.STRING),
    AttributeDeclaration(TITLE, AttributeDomain.DOCUMENT, XsdType.STRING),
    AttributeDeclaration(WEIGHT, AttributeDomain.BOUNDARY, XsdType.DECIMAL),
    AttributeDeclaration(KIND, AttributeDomain.RELATION_DECLARATION, XsdType.STRING),
    AttributeDeclaration(CONFIDENCE, AttributeDomain.RELATION_INSTANCE, XsdType.DOUBLE),
    AttributeDeclaration(MARK, AttributeDomain.BOUNDARY, XsdType.STRING),
)


def score(value: int) -> AttributeValue:
    """Return one item-domain integer value."""
    return AttributeValue(SCORE, XsdType.INTEGER, str(value))


def weight(value: str) -> AttributeValue:
    """Return one boundary-domain decimal value."""
    return AttributeValue(WEIGHT, XsdType.DECIMAL, value)


def base(
    *,
    words: tuple[Item, ...] = (
        Item("w0", (score(0),)),
        Item("w1", (score(1),)),
        Item("w2", (score(2),)),
        Item("w3", (score(3),)),
    ),
    boundaries: tuple[Boundary, ...] = (
        Boundary(BoundaryRef(PHRASE, 1), (weight("0.5"),)),
        Boundary(
            DurableBoundaryRef(DurableItemRef("w2"), BoundarySide.BEFORE),
            (weight("1.5"),),
        ),
    ),
    relations: tuple[RelationInstance, ...] | None = None,
) -> Graph:
    """Return the shared editing fixture: two tiers, two link kinds, values."""
    if relations is None:
        relations = (
            RelationInstance(COVERS, ItemRef(PHRASE, 0), ItemRef(WORD, 0)),
            RelationInstance(COVERS, ItemRef(PHRASE, 0), ItemRef(WORD, 1)),
            RelationInstance(COVERS, ItemRef(PHRASE, 1), ItemRef(WORD, 2), "c2"),
            RelationInstance(
                ANCHORS,
                ItemRef(PHRASE, 1),
                DurableBoundaryRef(DurableItemRef("w1"), BoundarySide.BEFORE),
            ),
        )
    return Graph(
        (NamespaceDeclaration("e", NS),),
        (
            Tier(TierDeclaration(WORD, "words"), words),
            Tier(
                TierDeclaration(PHRASE, "phrases"),
                (Item("p0"), Item("p1")),
            ),
        ),
        (
            SimpleRelationDeclaration(WORDS, WORD, WORD_TYPE),
            SimpleRelationDeclaration(PHRASES, PHRASE, PHRASE_TYPE),
            BipartiteRelationDeclaration(COVERS, PHRASE_TYPE, WORD_TYPE),
            BipartiteRelationDeclaration(
                ANCHORS,
                PHRASE_TYPE,
                WORD_TYPE,
                RelationEndpointKind.ITEM,
                RelationEndpointKind.BOUNDARY,
            ),
        ),
        relations,
        DECLARATIONS,
        boundaries,
        (),
    )


def item_score(graph: Graph, index: int) -> str:
    """Return the score carried by one word, or the empty string."""
    values = graph.tiers[0].items[index].attributes
    return next(
        (value.lexical for value in values if value.name == SCORE),
        "",
    )


def covered(graph: Graph) -> tuple[tuple[str, str], ...]:
    """Return each covering link as a pair of endpoint spellings."""
    return tuple(
        (str(relation.left), str(relation.right))
        for relation in graph.relations
        if relation.declaration == COVERS
    )


# --- the two carriers ------------------------------------------------------


def test_a_frozen_graph_answers_an_edit_with_a_new_graph() -> None:
    """The frozen carrier rewrites: the graph it was asked still stands."""
    graph = base()
    edited = graph.set_attribute(ItemRef(WORD, 1), score(41))
    assert item_score(edited, 1) == "41"
    assert item_score(graph, 1) == "1"
    assert edited is not graph


def test_the_editor_answers_the_same_edit_in_place() -> None:
    """The mutable carrier mutates, and freezing reports the change once."""
    graph = base()
    editor = graph.edit()
    assert editor.set_attribute(ItemRef(WORD, 1), score(41)) is editor
    assert item_score(editor.freeze(), 1) == "41"
    assert item_score(graph, 1) == "1"


def test_freezing_twice_does_not_consume_the_editor() -> None:
    """An editor keeps its content, so a caller may freeze checkpoints."""
    editor = base().edit()
    editor.set_attribute(ItemRef(WORD, 0), score(7))
    first = editor.freeze()
    editor.set_attribute(ItemRef(WORD, 0), score(8))
    assert item_score(first, 0) == "7"
    assert item_score(editor.freeze(), 0) == "8"


def test_an_editor_can_be_built_from_a_graph_directly() -> None:
    """``GraphEditor(graph)`` is the same carrier ``Graph.edit()`` returns."""
    graph = base()
    assert dumps(GraphEditor(graph).freeze()) == dumps(graph.edit().freeze())


def test_freezing_an_untouched_editor_reproduces_the_graph_bytes() -> None:
    """Round-tripping through the editor changes no canonical byte."""
    graph = base()
    assert dumps(graph.edit().freeze()) == dumps(graph)


# --- validation is not weakened -------------------------------------------


def test_the_editor_defers_whole_graph_checks_to_freeze() -> None:
    """A single-parent breach nothing local can see is refused at freeze."""
    graph = base().declare(
        BipartiteRelationDeclaration(
            name("owns"), PHRASE_TYPE, WORD_TYPE, single_parent=True
        )
    )
    editor = graph.edit()
    editor.add_relation(
        RelationInstance(name("owns"), ItemRef(PHRASE, 0), ItemRef(WORD, 0))
    )
    editor.add_relation(
        RelationInstance(name("owns"), ItemRef(PHRASE, 1), ItemRef(WORD, 0))
    )
    with pytest.raises(GraphValidationError, match="second parent"):
        editor.freeze()


def test_a_frozen_edit_refuses_an_endpoint_of_the_wrong_type() -> None:
    """Adding a link is validated, so a mistyped endpoint never becomes a graph."""
    with pytest.raises(GraphValidationError, match="expected"):
        base().add_relation(
            RelationInstance(COVERS, ItemRef(WORD, 0), ItemRef(WORD, 1))
        )


def test_an_inserted_item_with_an_undeclared_value_is_refused() -> None:
    """Insertion is validated, so an undeclared item value is refused."""
    stray = AttributeValue(name("stray"), XsdType.STRING, "x")
    with pytest.raises(GraphValidationError, match="undeclared"):
        base().insert_item(WORD, 0, Item("wx", (stray,)))


def test_a_duplicate_tier_declaration_is_refused() -> None:
    """Declaring is validated, so a repeated tier name never becomes a graph."""
    with pytest.raises(GraphValidationError, match="duplicate tier"):
        base().declare(TierDeclaration(WORD, "again"))


def test_a_declaration_naming_an_undeclared_namespace_is_refused() -> None:
    """Declaring is validated, so an unbound namespace is refused."""
    with pytest.raises(GraphValidationError, match="undeclared namespace"):
        base().declare(
            AttributeDeclaration(
                QualifiedName("urn:other", "x"),
                AttributeDomain.ITEM,
                XsdType.STRING,
            )
        )


def test_a_swap_that_closes_a_cycle_is_refused() -> None:
    """A reorder is validated, so an acyclic promise still holds after it."""
    chain = name("chain")
    graph = Graph(
        (NamespaceDeclaration("e", NS),),
        (Tier(TierDeclaration(WORD, "words"), (Item("a"), Item("b"))),),
        (
            SimpleRelationDeclaration(WORDS, WORD, WORD_TYPE),
            BipartiteRelationDeclaration(chain, WORD_TYPE, WORD_TYPE, acyclic=True),
        ),
        (
            RelationInstance(chain, ItemRef(WORD, 0), ItemRef(WORD, 1)),
            RelationInstance(chain, ItemRef(WORD, 0), ItemRef(WORD, 1)),
        ),
    )
    editor = graph.edit()
    editor._relations[1] = RelationInstance(chain, ItemRef(WORD, 1), ItemRef(WORD, 0))
    with pytest.raises(GraphValidationError, match="cycle"):
        editor.freeze()


# --- attribute values, one operation per domain ----------------------------


def test_a_document_value_is_set_and_removed() -> None:
    """The document carrier takes a value with no target."""
    title = AttributeValue(TITLE, XsdType.STRING, "one")
    graph = base().set_attribute(None, title)
    assert graph.attributes == (title,)
    assert graph.remove_attribute(None, TITLE).attributes == ()


def test_a_tier_value_is_set_and_removed() -> None:
    """The tier carrier is named by its qualified name."""
    label = AttributeValue(LABEL, XsdType.STRING, "surface")
    graph = base().set_attribute(WORD, label)
    assert graph.tiers[0].attributes == (label,)
    assert graph.remove_attribute(WORD, LABEL).tiers[0].attributes == ()


def test_an_item_value_replaces_the_value_of_the_same_name() -> None:
    """Setting is replacement, so a carrier never holds one name twice."""
    graph = base().set_attribute(ItemRef(WORD, 0), score(9))
    assert graph.tiers[0].items[0].attributes == (score(9),)


def test_an_item_value_is_reached_through_a_durable_reference() -> None:
    """Either identity level addresses the same item."""
    graph = base().set_attribute(DurableItemRef("w2"), score(9))
    assert item_score(graph, 2) == "9"
    assert item_score(graph.remove_attribute(DurableItemRef("w2"), SCORE), 2) == ""


def test_a_boundary_without_a_stored_value_gains_one() -> None:
    """A boundary value is created where the graph stored none."""
    graph = base().set_attribute(BoundaryRef(WORD, 0), weight("2.5"))
    assert graph.boundaries(WORD)[0].attributes == (weight("2.5"),)


def test_a_boundary_is_reached_through_either_spelling() -> None:
    """A coordinate and its durable anchor address one stored boundary."""
    graph = base().set_attribute(BoundaryRef(WORD, 2), weight("3.5"))
    assert len(graph.boundary_values) == 2
    assert graph.boundaries(WORD)[2].attributes == (weight("3.5"),)


def test_removing_a_boundary_last_value_drops_the_stored_boundary() -> None:
    """An empty boundary is derived, so its stored entry goes with its value."""
    graph = base().remove_attribute(BoundaryRef(WORD, 2), WEIGHT)
    assert len(graph.boundary_values) == 1
    assert graph.boundaries(WORD)[2].attributes == ()


def test_removing_one_of_two_boundary_values_keeps_the_boundary() -> None:
    """A boundary carrying more than one value keeps its stored entry."""
    mark = AttributeValue(MARK, XsdType.STRING, "x")
    graph = base().set_attribute(BoundaryRef(PHRASE, 1), mark)
    assert graph.boundaries(PHRASE)[1].attributes == (mark, weight("0.5"))
    kept = graph.remove_attribute(BoundaryRef(PHRASE, 1), WEIGHT)
    assert kept.boundaries(PHRASE)[1].attributes == (mark,)
    assert len(kept.boundary_values) == 2


def test_a_relation_declaration_value_is_set_and_removed() -> None:
    """Every relation-declaration shape carries values the same way."""
    kind = AttributeValue(KIND, XsdType.STRING, "coverage")
    for target in (WORDS, COVERS):
        graph = base().set_attribute(target, kind)
        declaration = next(
            item for item in graph.relation_declarations if item.name == target
        )
        assert declaration.attributes == (kind,)
        removed = graph.remove_attribute(target, KIND)
        assert all(
            item.attributes == ()
            for item in removed.relation_declarations
            if item.name == target
        )


def test_a_polyadic_declaration_value_is_set() -> None:
    """A polyadic declaration takes values through the same operation."""
    kind = AttributeValue(KIND, XsdType.STRING, "grouping")
    graph = polyadic_graph().set_attribute(name("groups"), kind)
    assert graph.relation_declarations[0].attributes == (kind,)


def test_a_relation_instance_value_is_set_by_index_and_by_id() -> None:
    """An instance is addressed by its bipartite index or its durable id."""
    confidence = AttributeValue(CONFIDENCE, XsdType.DOUBLE, "0.5")
    by_index = base().set_attribute(0, confidence)
    assert by_index.relations[0].attributes == (confidence,)
    by_id = base().set_attribute("c2", confidence)
    assert by_id.relations[2].attributes == (confidence,)
    assert by_id.remove_attribute("c2", CONFIDENCE).relations[2].attributes == ()


def test_a_polyadic_instance_value_is_set_by_durable_id() -> None:
    """A polyadic instance is reachable by the id it carries."""
    confidence = AttributeValue(CONFIDENCE, XsdType.DOUBLE, "0.5")
    graph = polyadic_graph().set_attribute("g0", confidence)
    assert graph.polyadic_relations[0].attributes == (confidence,)
    cleared = graph.remove_attribute("g0", CONFIDENCE)
    assert cleared.polyadic_relations[0].attributes == ()


# --- attribute refusals ----------------------------------------------------


def test_an_undeclared_attribute_is_refused_before_the_target_is_read() -> None:
    """Without a declaration there is no domain to read the target against."""
    stray = AttributeValue(name("stray"), XsdType.STRING, "x")
    with pytest.raises(GraphValidationError, match="is undeclared"):
        base().set_attribute(None, stray)
    with pytest.raises(GraphValidationError, match="is undeclared"):
        base().remove_attribute(None, name("stray"))


@pytest.mark.parametrize(
    ("target", "value", "message"),
    [
        (WORD, AttributeValue(TITLE, XsdType.STRING, "x"), "must be None"),
        (None, AttributeValue(LABEL, XsdType.STRING, "x"), "qualified name"),
        (None, AttributeValue(SCORE, XsdType.INTEGER, "1"), "item reference"),
        (None, AttributeValue(WEIGHT, XsdType.DECIMAL, "1.0"), "boundary reference"),
        (
            None,
            AttributeValue(CONFIDENCE, XsdType.DOUBLE, "1.0"),
            "instance index or a durable id",
        ),
    ],
)
def test_a_target_of_the_wrong_form_is_refused(
    target: EditTarget, value: AttributeValue, message: str
) -> None:
    """Each domain states the target form it accepts."""
    with pytest.raises(GraphValidationError, match=message):
        base().set_attribute(target, value)


@pytest.mark.parametrize(
    ("target", "attribute"),
    [
        (None, TITLE),
        (WORD, LABEL),
        (ItemRef(PHRASE, 0), SCORE),
        (WORDS, KIND),
        (0, CONFIDENCE),
    ],
)
def test_removing_an_absent_value_is_refused_on_every_carrier(
    target: EditTarget, attribute: QualifiedName
) -> None:
    """Absence is a distinct case, so removal names what it could not find."""
    with pytest.raises(GraphValidationError, match="carries no attribute"):
        base().remove_attribute(target, attribute)


def test_removing_an_absent_value_from_a_stored_boundary_is_refused() -> None:
    """A boundary that stores another value still carries no missing one."""
    graph = base().set_attribute(
        BoundaryRef(WORD, 0), AttributeValue(MARK, XsdType.STRING, "x")
    )
    with pytest.raises(GraphValidationError, match="carries no attribute"):
        graph.remove_attribute(BoundaryRef(WORD, 0), WEIGHT)


def test_removing_a_value_from_an_empty_boundary_is_refused() -> None:
    """A boundary the graph never stored carries nothing to remove."""
    with pytest.raises(GraphValidationError, match="carries no attribute"):
        base().remove_attribute(BoundaryRef(WORD, 0), WEIGHT)


def test_a_tier_attribute_names_a_declared_tier() -> None:
    """A tier target that names no tier is refused by name."""
    with pytest.raises(GraphValidationError, match="undeclared tier"):
        base().set_attribute(name("absent"), AttributeValue(LABEL, XsdType.STRING, "x"))


def test_a_relation_declaration_attribute_names_a_declared_relation() -> None:
    """A relation-declaration target that names no relation is refused."""
    with pytest.raises(GraphValidationError, match="is undeclared"):
        base().set_attribute(name("absent"), AttributeValue(KIND, XsdType.STRING, "x"))


def test_an_instance_index_outside_the_collection_is_refused() -> None:
    """An index past the bipartite instances names nothing."""
    with pytest.raises(GraphValidationError, match="outside the graph"):
        base().set_attribute(9, AttributeValue(CONFIDENCE, XsdType.DOUBLE, "1.0"))


def test_an_unknown_instance_durable_id_is_refused() -> None:
    """A durable id no instance carries names nothing."""
    with pytest.raises(GraphValidationError, match="no relation instance carries"):
        base().set_attribute("nope", AttributeValue(CONFIDENCE, XsdType.DOUBLE, "1"))


def test_an_unknown_durable_item_id_is_refused() -> None:
    """A durable item id nothing carries names no item."""
    with pytest.raises(GraphValidationError, match="unknown durable item id"):
        base().set_attribute(DurableItemRef("nope"), score(1))


def test_an_item_coordinate_outside_its_tier_is_refused() -> None:
    """A coordinate past the tier's items names no item."""
    with pytest.raises(GraphValidationError, match="outside tier"):
        base().set_attribute(ItemRef(WORD, 9), score(1))


# --- declaring -------------------------------------------------------------


def test_each_declaration_kind_is_added() -> None:
    """One operation covers namespaces, tiers, attributes, and relations."""
    graph = base()
    other = "urn:other"
    graph = graph.declare(NamespaceDeclaration("o", other))
    graph = graph.declare(TierDeclaration(QualifiedName(other, "gloss"), "glosses"))
    graph = graph.declare(
        AttributeDeclaration(
            QualifiedName(other, "note"), AttributeDomain.ITEM, XsdType.STRING
        )
    )
    graph = graph.declare(
        SimpleRelationDeclaration(
            QualifiedName(other, "glosses"),
            QualifiedName(other, "gloss"),
            QualifiedName(other, "Gloss"),
        )
    )
    assert len(graph.namespaces) == 2
    assert len(graph.tiers) == 3
    assert len(graph.attribute_declarations) == len(DECLARATIONS) + 1
    assert len(graph.relation_declarations) == 5


def test_declaring_something_that_is_not_a_declaration_is_refused() -> None:
    """The operation names the four kinds it accepts."""
    with pytest.raises(GraphValidationError, match="declare expected"):
        base().declare(cast_declaration(object()))


def cast_declaration(value: object) -> EditDeclaration:
    """Present an arbitrary object where a declaration is expected."""
    assert isinstance(value, object)
    return value  # type: ignore[return-value]


# --- structure: insertion --------------------------------------------------


def test_insertion_carries_later_references_with_their_items() -> None:
    """A link keeps denoting its item after an item is inserted before it."""
    graph = base().insert_item(WORD, 1, Item("wx"))
    assert graph.tiers[0].items[1].durable_id == "wx"
    assert covered(graph) == (
        ("{urn:edit}phrase[0]", "{urn:edit}word[0]"),
        ("{urn:edit}phrase[0]", "{urn:edit}word[2]"),
        ("{urn:edit}phrase[1]", "{urn:edit}word[3]"),
    )


def test_insertion_at_the_item_count_appends() -> None:
    """The index one past the last item is the append index."""
    graph = base().insert_item(WORD, 4, Item("wx"))
    assert graph.tiers[0].items[4].durable_id == "wx"
    assert covered(graph) == (
        ("{urn:edit}phrase[0]", "{urn:edit}word[0]"),
        ("{urn:edit}phrase[0]", "{urn:edit}word[1]"),
        ("{urn:edit}phrase[1]", "{urn:edit}word[2]"),
    )


def test_an_insertion_index_outside_the_tier_is_refused() -> None:
    """An index past the append index addresses no place."""
    with pytest.raises(GraphValidationError, match="insertion index 5 is outside"):
        base().insert_item(WORD, 5, Item("wx"))
    with pytest.raises(GraphValidationError, match="insertion index -1 is outside"):
        base().insert_item(WORD, -1, Item("wx"))


def test_inserting_into_an_undeclared_tier_is_refused() -> None:
    """A tier that was never declared holds no place to insert into."""
    with pytest.raises(GraphValidationError, match="undeclared tier"):
        base().insert_item(name("absent"), 0, Item("wx"))


def test_a_boundary_value_before_the_insertion_keeps_its_coordinate() -> None:
    """A boundary whose neighbors both keep their places keeps its index."""
    graph = with_word_boundary(0).insert_item(WORD, 2, Item("wx"))
    assert graph.boundaries(WORD)[0].attributes == (weight("7.5"),)


def test_a_boundary_after_the_insertion_moves_with_its_neighbors() -> None:
    """A boundary between two items follows both of them."""
    graph = with_word_boundary(3).insert_item(WORD, 1, Item("wx"))
    assert graph.boundaries(WORD)[4].attributes == (weight("7.5"),)


def test_the_last_boundary_follows_an_insertion_before_it() -> None:
    """The boundary after the last item stays after the last item."""
    graph = with_word_boundary(4).insert_item(WORD, 1, Item("wx"))
    assert graph.boundaries(WORD)[5].attributes == (weight("7.5"),)


@pytest.mark.parametrize(("boundary", "index"), [(0, 0), (2, 2), (4, 4)])
def test_an_insertion_at_a_boundary_value_is_refused(boundary: int, index: int) -> None:
    """A boundary the insertion splits has no one image, so the edit refuses."""
    with pytest.raises(GraphValidationError, match="without one boundary"):
        with_word_boundary(boundary).insert_item(WORD, index, Item("wx"))


def test_the_first_insertion_into_a_tier_with_a_boundary_value_is_refused() -> None:
    """An empty tier's only boundary cannot say which side of the item it is."""
    graph = Graph(
        (NamespaceDeclaration("e", NS),),
        (Tier(TierDeclaration(WORD, "words"), ()),),
        (SimpleRelationDeclaration(WORDS, WORD, WORD_TYPE),),
        (),
        DECLARATIONS,
        (Boundary(BoundaryRef(WORD, 0), (weight("7.5"),)),),
    )
    with pytest.raises(GraphValidationError, match="without one boundary"):
        graph.insert_item(WORD, 0, Item("wx"))


def with_word_boundary(index: int) -> Graph:
    """Return the fixture with one coordinate boundary value on the word tier."""
    return base(
        boundaries=(
            Boundary(BoundaryRef(PHRASE, 1), (weight("0.5"),)),
            Boundary(BoundaryRef(WORD, index), (weight("7.5"),)),
        )
    )


# --- structure: removal ----------------------------------------------------


def test_removal_refuses_while_the_graph_still_references_the_item() -> None:
    """A link to a removed item would be a wrong answer, so removal refuses."""
    with pytest.raises(GraphValidationError, match="still references"):
        base().remove_item(ItemRef(WORD, 0))


def test_removal_carries_later_references_with_their_items() -> None:
    """Links past the removed item keep denoting the items they denoted."""
    graph = base(
        relations=(
            RelationInstance(COVERS, ItemRef(PHRASE, 0), ItemRef(WORD, 2)),
            RelationInstance(COVERS, ItemRef(PHRASE, 1), ItemRef(WORD, 3)),
        )
    ).remove_item(ItemRef(WORD, 0))
    assert tuple(item.durable_id for item in graph.tiers[0].items) == (
        "w1",
        "w2",
        "w3",
    )
    assert covered(graph) == (
        ("{urn:edit}phrase[0]", "{urn:edit}word[1]"),
        ("{urn:edit}phrase[1]", "{urn:edit}word[2]"),
    )


def test_removal_reaches_an_item_by_durable_id() -> None:
    """Either identity level names the item to remove."""
    graph = base(relations=()).remove_item(DurableItemRef("w3"))
    assert tuple(item.durable_id for item in graph.tiers[0].items) == (
        "w0",
        "w1",
        "w2",
    )


def test_removing_a_boundary_anchor_is_refused() -> None:
    """A boundary whose anchor is gone has no identity, so removal refuses."""
    with pytest.raises(GraphValidationError, match="anchor item 'w2' was not found"):
        base(relations=()).remove_item(ItemRef(WORD, 2))


@pytest.mark.parametrize("boundary", [1, 2])
def test_removal_refuses_a_boundary_value_beside_the_removed_item(
    boundary: int,
) -> None:
    """The two boundaries a removal merges have no one image between them."""
    graph = base(
        relations=(),
        boundaries=(Boundary(BoundaryRef(WORD, boundary), (weight("7.5"),)),),
    )
    with pytest.raises(GraphValidationError, match="without one boundary"):
        graph.remove_item(ItemRef(WORD, 1))


def test_removal_moves_a_boundary_value_past_the_removed_item() -> None:
    """A boundary clear of the removal keeps its meaning at a new index."""
    graph = base(
        relations=(),
        boundaries=(Boundary(BoundaryRef(WORD, 3), (weight("7.5"),)),),
    ).remove_item(ItemRef(WORD, 0))
    assert graph.boundaries(WORD)[2].attributes == (weight("7.5"),)


# --- structure: move and swap ---------------------------------------------


def test_a_move_carries_every_reference_with_its_item() -> None:
    """Reordering renames places; the links keep their items."""
    graph = base().move_item(ItemRef(WORD, 0), 2)
    assert tuple(item.durable_id for item in graph.tiers[0].items) == (
        "w1",
        "w2",
        "w0",
        "w3",
    )
    assert covered(graph) == (
        ("{urn:edit}phrase[0]", "{urn:edit}word[2]"),
        ("{urn:edit}phrase[0]", "{urn:edit}word[0]"),
        ("{urn:edit}phrase[1]", "{urn:edit}word[1]"),
    )


def test_a_move_backward_carries_its_references_too() -> None:
    """The rule does not depend on the direction of the move."""
    graph = base().move_item(DurableItemRef("w2"), 0)
    assert tuple(item.durable_id for item in graph.tiers[0].items) == (
        "w2",
        "w0",
        "w1",
        "w3",
    )
    assert covered(graph) == (
        ("{urn:edit}phrase[0]", "{urn:edit}word[1]"),
        ("{urn:edit}phrase[0]", "{urn:edit}word[2]"),
        ("{urn:edit}phrase[1]", "{urn:edit}word[0]"),
    )


def test_a_move_index_outside_the_tier_is_refused() -> None:
    """A move names a place that exists after the move."""
    with pytest.raises(GraphValidationError, match="move index 4 is outside"):
        base().move_item(ItemRef(WORD, 0), 4)
    with pytest.raises(GraphValidationError, match="move index -1 is outside"):
        base().move_item(ItemRef(WORD, 0), -1)


def test_a_swap_exchanges_two_items_and_their_references() -> None:
    """Two items trade places and each link follows its own item."""
    graph = base().swap_items(ItemRef(WORD, 0), ItemRef(WORD, 2))
    assert tuple(item.durable_id for item in graph.tiers[0].items) == (
        "w2",
        "w1",
        "w0",
        "w3",
    )
    assert covered(graph) == (
        ("{urn:edit}phrase[0]", "{urn:edit}word[2]"),
        ("{urn:edit}phrase[0]", "{urn:edit}word[1]"),
        ("{urn:edit}phrase[1]", "{urn:edit}word[0]"),
    )


def test_a_swap_across_tiers_is_refused() -> None:
    """A tier decides an item's type, so items do not trade tiers."""
    with pytest.raises(GraphValidationError, match="different"):
        base().swap_items(ItemRef(WORD, 0), ItemRef(PHRASE, 0))


def test_a_swap_refuses_a_boundary_value_between_the_swapped_items() -> None:
    """A boundary inside the swapped span has no one image."""
    with pytest.raises(GraphValidationError, match="without one boundary"):
        with_word_boundary(1).swap_items(ItemRef(WORD, 0), ItemRef(WORD, 2))


# --- relation instances ----------------------------------------------------


def test_a_relation_instance_is_added_and_removed_by_index() -> None:
    """The bipartite collection is addressed by index."""
    added = base().add_relation(
        RelationInstance(COVERS, ItemRef(PHRASE, 1), ItemRef(WORD, 3))
    )
    assert len(added.relations) == 5
    assert len(added.remove_relation(4).relations) == 4


def test_a_relation_instance_is_removed_by_durable_id() -> None:
    """An instance carrying a durable id is named by it."""
    graph = base().remove_relation("c2")
    assert all(relation.durable_id is None for relation in graph.relations)


def test_a_polyadic_instance_is_added_and_removed() -> None:
    """The polyadic collection takes the same two operations."""
    graph = polyadic_graph()
    added = graph.add_relation(
        PolyadicRelationInstance(
            name("groups"), (ItemRef(WORD, 1),), (ItemRef(WORD, 2),), "g1"
        )
    )
    assert len(added.polyadic_relations) == 2
    assert len(added.remove_relation("g1").polyadic_relations) == 1


def test_removing_a_relation_instance_that_is_not_there_is_refused() -> None:
    """Neither an index past the collection nor an unknown id names one."""
    with pytest.raises(GraphValidationError, match="outside the graph"):
        base().remove_relation(9)
    with pytest.raises(GraphValidationError, match="no relation instance carries"):
        base().remove_relation("nope")


def polyadic_graph() -> Graph:
    """Return a small graph carrying one polyadic declaration and instance."""
    side = RelationSideDeclaration((RelationEndpointKind.ITEM,), (WORD,))
    return Graph(
        (NamespaceDeclaration("e", NS),),
        (Tier(TierDeclaration(WORD, "words"), (Item("a"), Item("b"), Item("c"))),),
        (
            PolyadicRelationDeclaration(name("groups"), side, side),
            SimpleRelationDeclaration(WORDS, WORD, WORD_TYPE),
        ),
        (),
        DECLARATIONS,
        (),
        (),
        (
            PolyadicRelationInstance(
                name("groups"), (ItemRef(WORD, 0),), (ItemRef(WORD, 1),), "g0"
            ),
        ),
    )


def test_a_polyadic_endpoint_follows_its_item() -> None:
    """Polyadic sides are remapped exactly as bipartite endpoints are."""
    graph = polyadic_graph().insert_item(WORD, 0, Item("z"))
    instance = graph.polyadic_relations[0]
    assert instance.sources == (ItemRef(WORD, 1),)
    assert instance.targets == (ItemRef(WORD, 2),)


def test_a_polyadic_endpoint_untouched_by_an_edit_is_left_alone() -> None:
    """An edit to another tier rewrites nothing on this one."""
    graph = polyadic_graph().declare(TierDeclaration(PHRASE, "phrases"))
    edited = graph.insert_item(PHRASE, 0, Item("p"))
    assert edited.polyadic_relations[0] == graph.polyadic_relations[0]


def test_a_polyadic_removal_still_refuses_a_referenced_item() -> None:
    """A polyadic side references an item exactly as a bipartite side does."""
    with pytest.raises(GraphValidationError, match="still references"):
        polyadic_graph().remove_item(ItemRef(WORD, 1))


# --- atomicity and the stale-reference finding -----------------------------


def test_a_refused_structural_edit_leaves_the_editor_untouched() -> None:
    """Every refusal a structural edit can raise is raised before any write."""
    editor = base().edit()
    before = dumps(editor.freeze())
    with pytest.raises(GraphValidationError, match="still references"):
        editor.remove_item(ItemRef(WORD, 0))
    assert dumps(editor.freeze()) == before


def test_a_reference_held_across_an_edit_silently_resolves_elsewhere() -> None:
    """A coordinate held outside the graph is not refused; it points elsewhere.

    This records what a caller gets today, not what the design wants.  There is
    no identity on a graph a held reference could be checked against, so the
    reference resolves against whichever item now sits at its index.
    """
    graph = base(relations=())
    held = ItemRef(WORD, 1)
    assert item_score(graph, held.index) == "1"
    edited = graph.remove_item(ItemRef(WORD, 0))
    assert edited.resolve_item(held) == held
    assert item_score(edited, held.index) == "2"
    assert graph.canonical_items() != edited.canonical_items()


def test_sealed_member_cannot_move_but_its_value_can_change() -> None:
    """REGRESSION (parent: dependency failure): the seal is geometric."""
    sealed = base().seal(WORD, 3)
    with pytest.raises(GraphValidationError) as refusal:
        sealed.insert_item(WORD, 1, Item("new"))
    carrier = f"'{WORD}'"
    assert str(refusal.value) == (
        f"item insertion at {carrier}[1] would move {carrier}[1], which stands "
        "inside this graph's seal on that tier at 3. A seal says the coordinates "
        "up to it do not move, so an edit that moves one is not an edit this graph "
        "admits. Unseal that carrier first if the base itself is what needs "
        "correcting."
    )
    changed = sealed.set_attribute(ItemRef(WORD, 1), score(20))
    assert item_score(changed, 1) == "20"
    assert changed.seals == sealed.seals


def test_edits_beyond_a_seal_succeed_and_retreat_is_explicit() -> None:
    """REGRESSION (parent: dependency failure): only the prefix is fixed."""
    sealed = base().seal(WORD, 2)
    assert (
        sealed.insert_item(WORD, 4, Item("new")).tiers[0].items[-1].durable_id == "new"
    )
    with pytest.raises(GraphValidationError, match="sealing advances"):
        sealed.seal(WORD, 1)
    assert sealed.unseal(WORD, 1).seals == (Seal(WORD, 1),)
    assert sealed.is_sealed(ItemRef(WORD, 1))
    assert not sealed.is_sealed(ItemRef(WORD, 2))


def test_unseal_refuses_to_raise_or_create_a_seal() -> None:
    """REGRESSION (F6): unsealing only retreats from an existing seal."""
    graph = base(relations=())
    sealed = graph.seal(WORD, 1)
    raised_message = None
    try:
        sealed.unseal(WORD, 3)
    except GraphValidationError as error:
        raised_message = str(error)
    absent_message = None
    try:
        graph.unseal(WORD, 2)
    except GraphValidationError as error:
        absent_message = str(error)
    assert (raised_message, absent_message) == (
        f"cannot unseal '{WORD}' from 1 to 3: the requested seal is not lower",
        f"cannot unseal '{WORD}' to 2: this graph carries no seal on that carrier",
    )


def test_seal_bounds_and_graph_carrier_edits_are_checked() -> None:
    """REGRESSION (parent: dependency failure): every ordered carrier bites."""
    graph = base()
    with pytest.raises(GraphValidationError, match="must not be negative"):
        graph.seal(WORD, -1)
    with pytest.raises(GraphValidationError, match="members that exist"):
        graph.seal(WORD, 5)
    with pytest.raises(GraphValidationError, match="undeclared tier"):
        graph.seal(name("missing"), 0)
    with pytest.raises(GraphValidationError, match="duplicate seal carrier"):
        replace(graph, seals=(Seal(WORD, 0), Seal(WORD, 1)))

    relations = graph.seal(GraphCarrier.RELATIONS, 2)
    with pytest.raises(GraphValidationError, match="relation removal would move"):
        relations.remove_relation(1)
    assert len(relations.remove_relation(3).relations) == 3
    assert graph.seal(GraphCarrier.POLYADIC_RELATIONS, 0).seals


def test_seal_declarations_report_vacuity_breaches_and_omission() -> None:
    """REGRESSION (parent: dependency failure): certificates remain honest."""
    graph = base(relations=())
    certificate = SealDeclaration("identity", graph, graph).check_seals()
    assert (certificate.carriers, certificate.sealed_members) == (0, 0)
    with pytest.raises(ValueError, match="must not be empty"):
        SealDeclaration("", graph, graph)

    source = graph.seal(WORD, 3)
    tier = source.tiers[0]
    moved = replace(
        source,
        tiers=(
            replace(
                tier,
                items=(tier.items[0], Item("replacement"), *tier.items[2:]),
            ),
            source.tiers[1],
        ),
    )
    with pytest.raises(GraphValidationError) as semantic:
        SealDeclaration("replace-word", source, moved).check_seals()
    assert (
        "carried durable id 'w1' in the source and carries 'replacement' here"
        in str(semantic.value)
    )
    with pytest.raises(GraphValidationError, match="not withdrawn by omission"):
        SealDeclaration("strip", source, replace(source, seals=())).check_seals()

    relation_graph = base()
    relation_source = replace(
        relation_graph,
        relations=(
            replace(relation_graph.relations[0], durable_id="c0"),
            *relation_graph.relations[1:],
        ),
    ).seal(GraphCarrier.RELATIONS, 1)
    relation_result = replace(
        relation_source,
        relations=(relation_source.relations[1], *relation_source.relations[1:]),
    )
    assert (
        SealDeclaration("relations", relation_source, relation_result)
        .breaches()[0]
        .detail.endswith("did not keep its member")
    )

    poly_name = name("poly")
    side = RelationSideDeclaration((RelationEndpointKind.ITEM,), (WORD,), 1, 1, False)
    poly_declaration = PolyadicRelationDeclaration(poly_name, side, side)
    poly = PolyadicRelationInstance(
        poly_name, (ItemRef(WORD, 0),), (ItemRef(WORD, 1),), "poly-0"
    )
    poly_source = replace(
        base(relations=()),
        relation_declarations=(poly_declaration,),
        polyadic_relations=(poly,),
    ).seal(GraphCarrier.POLYADIC_RELATIONS, 1)
    poly_result = replace(
        poly_source,
        polyadic_relations=(replace(poly, durable_id="poly-replacement"),),
    )
    assert SealDeclaration("poly", poly_source, poly_result).breaches()[0].index == 0


def test_anonymous_sealed_members_make_the_certificate_explicitly_vacuous() -> None:
    """REGRESSION (F2): anonymous values do not pose as compared identities."""
    anonymous = base(
        words=(Item(), Item(), Item(), Item()), boundaries=(), relations=()
    ).seal(WORD, 3)
    tier = anonymous.tiers[0]
    reversed_prefix = replace(
        anonymous,
        tiers=(
            replace(tier, items=(*reversed(tier.items[:3]), tier.items[3])),
            anonymous.tiers[1],
        ),
    )
    certificate = SealDeclaration(
        "anonymous-prefix", anonymous, reversed_prefix
    ).check_seals()
    assert (certificate.carriers, certificate.sealed_members) == (1, 0)


def test_relation_identity_excludes_shifted_endpoint_coordinates() -> None:
    """REGRESSION (F3): a stationary relation survives endpoint remapping."""
    relation = RelationInstance(
        COVERS, ItemRef(PHRASE, 0), ItemRef(WORD, 1), "relation-0"
    )
    source = base(boundaries=(), relations=(relation,)).seal(GraphCarrier.RELATIONS, 1)
    result = source.insert_item(WORD, 0, Item("inserted"))
    assert result.relations[0].left == ItemRef(PHRASE, 0)
    assert result.relations[0].right == ItemRef(WORD, 2)
    declaration = SealDeclaration("endpoint-shift", source, result)
    assert declaration.breaches() == ()
    certificate = declaration.check_seals()
    assert (certificate.carriers, certificate.sealed_members) == (1, 1)


def test_seal_refusal_describes_geometry_without_claiming_content_stability() -> None:
    """REGRESSION (F4): refusal text states the geometric comparison."""
    source = base(relations=()).seal(WORD, 2)
    tier = source.tiers[0]
    moved = replace(
        source,
        tiers=(
            replace(tier, items=(tier.items[1], tier.items[0], *tier.items[2:])),
            source.tiers[1],
        ),
    )
    with pytest.raises(GraphValidationError) as refusal:
        SealDeclaration("swap", source, moved).check_seals()
    assert "stands at the same coordinate" in str(refusal.value)
    assert "carrying what it carried" not in str(refusal.value)


def test_seals_are_canonical_optional_and_round_trip() -> None:
    """REGRESSION (parent: dependency failure): wire seals preserve guarantees."""
    graph = base(relations=())
    sealed = replace(
        graph,
        seals=(Seal(GraphCarrier.RELATIONS, 0), Seal(WORD, 2)),
    )
    assert sealed.seals == (Seal(WORD, 2), Seal(GraphCarrier.RELATIONS, 0))
    assert loads(dumps(sealed)) == sealed
    with pytest.raises(ValueError, match="carrier.kind 'bad' is unsupported"):
        loads(dumps(sealed).replace('"kind": "tier"', '"kind": "bad"', 1))

    current = dumps(Graph((), (), ()))
    format_six = '{\n  "format_version": "6",\n  "graph": {}\n}\n'
    assert current == '{\n  "format_version": "0.2.0",\n  "graph": {}\n}\n'
    assert current.replace('"0.2.0"', '"6"', 1) == format_six
