"""The reference kernel satisfies the reusable construction laws."""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests.conformance.kernel import KernelLawSuite
from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    DurableItemRef,
    DurablePositionRef,
    Graph,
    Item,
    ItemRef,
    NamespaceDeclaration,
    Position,
    PositionRef,
    QualifiedName,
    RelationInstance,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    XsdType,
)

NS = "urn:test"
NAMESPACES = (NamespaceDeclaration("t", NS),)


def name(local: str, namespace: str = NS) -> QualifiedName:
    """Return an expanded test name."""
    return QualifiedName(namespace, local)


def build_graph(
    namespaces: tuple[NamespaceDeclaration, ...],
    tiers: tuple[Tier, ...],
    declarations: tuple[SimpleRelationDeclaration | BipartiteRelationDeclaration, ...],
    relations: tuple[RelationInstance, ...] = (),
    attributes: tuple[AttributeDeclaration, ...] = (),
    positions: tuple[Position, ...] = (),
    document_values: tuple[AttributeValue, ...] = (),
) -> Graph:
    """Construct the reference graph behind the conformance boundary."""
    return Graph(
        namespaces,
        tiers,
        declarations,
        relations,
        attributes,
        positions,
        document_values,
    )


LAWS = KernelLawSuite(build_graph)


@pytest.mark.parametrize(
    "law",
    [
        LAWS.check_boundaries,
        LAWS.check_json_data,
        LAWS.check_attribute_domains,
        LAWS.check_endpoint_type_refusal,
        LAWS.check_single_parent_refusal,
        LAWS.check_cycle_refusal,
    ],
    ids=lambda law: law.__name__,
)
def test_kernel_law(law: object) -> None:
    """Run each reusable law against the reference constructor."""
    assert callable(law)
    law()


@given(st.lists(st.integers(min_value=0, max_value=8), min_size=1, max_size=6))
def test_canonical_order_is_derived(lengths: list[int]) -> None:
    """Canonical order is tier-major and follows each current tuple index."""
    tiers = tuple(
        Tier(
            TierDeclaration(name(f"t{tier_index}"), f"Tier {tier_index}"),
            tuple(Item() for _ in range(length)),
        )
        for tier_index, length in enumerate(lengths)
    )
    declarations = tuple(
        SimpleRelationDeclaration(
            name(f"r{index}"), tier.declaration.name, name(f"type{index}")
        )
        for index, tier in enumerate(tiers)
    )
    graph = Graph(NAMESPACES, tiers, declarations)
    assert graph.canonical_items() == tuple(
        ItemRef(tier.declaration.name, item_index)
        for tier in tiers
        for item_index in range(len(tier.items))
    )


@pytest.mark.parametrize(
    ("value_type", "spellings", "canonical"),
    [
        (XsdType.STRING, ("text",), "text"),
        (XsdType.BOOLEAN, ("1", "true"), "true"),
        (XsdType.INTEGER, ("+001", "1"), "1"),
        (XsdType.DECIMAL, ("01.00", "1.0"), "1.0"),
        (XsdType.DOUBLE, ("1.0", "1.00", "1e0"), "1.0E0"),
    ],
)
def test_xsd_subset_canonicalizes(
    value_type: XsdType, spellings: tuple[str, ...], canonical: str
) -> None:
    """Equivalent lexical forms become one immutable value and one JSON spelling."""
    values = tuple(
        AttributeValue(name("value"), value_type, value) for value in spellings
    )
    assert {value.lexical for value in values} == {canonical}
    assert len(set(values)) == 1


@pytest.mark.parametrize(
    ("value_type", "lexical", "canonical"),
    [
        (XsdType.INTEGER, "\t+001\n", "1"),
        (XsdType.DECIMAL, "  1.00  ", "1.0"),
        (XsdType.DOUBLE, "\t1e0\n", "1.0E0"),
    ],
)
def test_numeric_whitespace_is_collapsed_before_lexical_validation(
    value_type: XsdType, lexical: str, canonical: str
) -> None:
    """XSD numeric whitespace is collapsed before canonicalization."""
    assert AttributeValue(name("value"), value_type, lexical).lexical == canonical


@pytest.mark.parametrize(
    ("lexical", "canonical"), [("\t true\r\n", "true"), ("  false  ", "false")]
)
def test_boolean_whitespace_is_collapsed_before_lexical_validation(
    lexical: str, canonical: str
) -> None:
    """XSD boolean whitespace is collapsed before canonicalization."""
    assert AttributeValue(name("value"), XsdType.BOOLEAN, lexical).lexical == canonical


def test_string_whitespace_is_preserved() -> None:
    """XSD string retains leading and trailing whitespace under its preserve facet."""
    lexical = "  text\twith whitespace\r\n"
    assert AttributeValue(name("value"), XsdType.STRING, lexical).lexical == lexical


@pytest.mark.parametrize(
    ("lexical", "canonical"),
    [("NaN", "NaN"), ("INF", "INF"), ("-INF", "-INF"), ("-0", "-0.0E0")],
)
def test_double_edges_are_json_strings(lexical: str, canonical: str) -> None:
    """IEEE special values and signed zero never become non-JSON number tokens."""
    value = AttributeValue(name("weight"), XsdType.DOUBLE, lexical)
    assert value.lexical == canonical
    encoded = json.dumps(value.to_data(), allow_nan=False)
    assert canonical in encoded


@pytest.mark.parametrize(
    ("lexical", "canonical"),
    [
        ("-12.5", "-1.25E1"),
        ("0.025", "2.5E-2"),
        ("1e20", "1.0E20"),
        ("1e999", "INF"),
        ("-1e999", "-INF"),
    ],
)
def test_finite_double_scientific_canonical_form(lexical: str, canonical: str) -> None:
    """Finite lexical forms normalize across signs, scales, and IEEE overflow."""
    assert AttributeValue(name("value"), XsdType.DOUBLE, lexical).lexical == canonical


@pytest.mark.parametrize(
    ("lexical", "canonical"),
    [("false", "false"), ("0", "false"), ("0", "0.0"), ("12", "12.0")],
)
def test_boolean_and_decimal_remaining_canonical_forms(
    lexical: str, canonical: str
) -> None:
    """False and integral decimal spellings use their XSD canonical forms."""
    value_type = XsdType.BOOLEAN if canonical == "false" else XsdType.DECIMAL
    assert AttributeValue(name("value"), value_type, lexical).lexical == canonical


@pytest.mark.parametrize(
    ("value_type", "lexical"),
    [
        (XsdType.BOOLEAN, "yes"),
        (XsdType.INTEGER, "1.0"),
        (XsdType.DECIMAL, "1e0"),
        (XsdType.DOUBLE, "Infinity"),
    ],
)
def test_invalid_lexical_value_names_attribute(
    value_type: XsdType, lexical: str
) -> None:
    """Each subset type refuses a near-valid foreign lexical spelling."""
    with pytest.raises(ValueError, match=r"attribute.*bad.*" + lexical):
        AttributeValue(name("bad"), value_type, lexical)


def test_attribute_declaration_and_domain_are_enforced() -> None:
    """Values must name a declaration of matching domain and type."""
    item = Tier(
        TierDeclaration(name("x"), "X"),
        (Item(attributes=(AttributeValue(name("weight"), XsdType.DOUBLE, "1"),)),),
    )
    with pytest.raises(ValueError, match="weight.*undeclared"):
        Graph(NAMESPACES, (item,), ())
    declaration = AttributeDeclaration(
        name("weight"), AttributeDomain.TIER, XsdType.DOUBLE
    )
    with pytest.raises(ValueError, match="weight.*cannot occur on 'item'"):
        Graph(NAMESPACES, (item,), (), attribute_declarations=(declaration,))
    wrong_type = AttributeDeclaration(
        name("weight"), AttributeDomain.ITEM, XsdType.DECIMAL
    )
    with pytest.raises(ValueError, match="weight.*decimal.*double"):
        Graph(NAMESPACES, (item,), (), attribute_declarations=(wrong_type,))


def test_position_values_are_sparse_and_checked() -> None:
    """Only valued positions are stored while all boundaries remain addressable."""
    tier_name = name("x")
    declaration = AttributeDeclaration(
        name("mark"), AttributeDomain.POSITION, XsdType.BOOLEAN
    )
    stored = Position(
        PositionRef(tier_name, 1),
        (AttributeValue(declaration.name, XsdType.BOOLEAN, "1"),),
    )
    graph = Graph(
        NAMESPACES,
        (Tier(TierDeclaration(tier_name, "X"), (Item(), Item())),),
        (),
        attribute_declarations=(declaration,),
        position_values=(stored,),
    )
    assert graph.position_values == (stored,)
    assert graph.positions(tier_name) == (
        Position(PositionRef(tier_name, 0), ()),
        stored,
        Position(PositionRef(tier_name, 2), ()),
    )
    promoted, durable = graph.promote_position(stored.reference, "stored-position")
    assert promoted.position_values[0].attributes == stored.attributes
    assert promoted.resolve_position(durable) == stored.reference
    with pytest.raises(ValueError, match="outside tier"):
        Graph(
            NAMESPACES,
            graph.tiers,
            (),
            attribute_declarations=(declaration,),
            position_values=(Position(PositionRef(tier_name, 3), stored.attributes),),
        )
    with pytest.raises(ValueError, match="empty positions are derived"):
        Graph(
            NAMESPACES,
            graph.tiers,
            (),
            attribute_declarations=(declaration,),
            position_values=(Position(PositionRef(tier_name, 0), ()),),
        )


def test_qualified_name_uniqueness_and_prefix_declarations() -> None:
    """Local names may repeat across declared namespaces but expanded names may not."""
    namespaces = (
        NamespaceDeclaration("p", "urn:phonemic"),
        NamespaceDeclaration("a", "urn:allophonic"),
    )
    tiers = (
        Tier(TierDeclaration(name("segment", "urn:phonemic"), "Phonemic")),
        Tier(TierDeclaration(name("segment", "urn:allophonic"), "Allophonic")),
    )
    assert len(Graph(namespaces, tiers, ()).tiers) == 2
    with pytest.raises(ValueError, match="duplicate tier"):
        Graph(namespaces, (tiers[0], tiers[0]), ())
    with pytest.raises(ValueError, match="undeclared namespace 'urn:missing'"):
        Graph(namespaces, (Tier(TierDeclaration(name("x", "urn:missing"), "X")),), ())
    with pytest.raises(ValueError, match="duplicate namespace prefix 'p'"):
        Graph((namespaces[0], NamespaceDeclaration("p", "urn:other")), (), ())
    with pytest.raises(ValueError, match="duplicate namespace URI 'urn:phonemic'"):
        Graph((namespaces[0], NamespaceDeclaration("other", "urn:phonemic")), (), ())
    assert namespaces[0].name == "p"
    assert tiers[0].declaration.short_name == "segment"


@pytest.mark.parametrize(
    "declaration",
    [
        SimpleRelationDeclaration(
            name("members"), name("x"), name("phone", "urn:missing")
        ),
        BipartiteRelationDeclaration(
            name("links"), name("phone", "urn:missing"), name("phone")
        ),
        BipartiteRelationDeclaration(
            name("links"), name("phone"), name("phone", "urn:missing")
        ),
    ],
)
def test_relation_type_names_require_declared_namespaces(
    declaration: SimpleRelationDeclaration | BipartiteRelationDeclaration,
) -> None:
    """Every expanded type name must use a document-declared namespace."""
    tier = Tier(TierDeclaration(name("x"), "X"), (Item(), Item()))
    with pytest.raises(ValueError, match="phone.*undeclared namespace 'urn:missing'"):
        Graph(NAMESPACES, (tier,), (declaration,))


def test_declaration_and_reference_refusals() -> None:
    """Near-valid declaration and reference mistakes name their expanded offender."""
    tier_name = name("x")
    tier = Tier(TierDeclaration(tier_name, "X"), (Item(),))
    simple = SimpleRelationDeclaration(name("members"), tier_name, name("node"))
    with pytest.raises(ValueError, match="undeclared tier.*missing"):
        Graph(
            NAMESPACES,
            (tier,),
            (SimpleRelationDeclaration(name("bad"), name("missing"), name("node")),),
        )
    with pytest.raises(ValueError, match="multiple simple relations"):
        Graph(
            NAMESPACES,
            (tier,),
            (simple, simple.__class__(name("other"), tier_name, name("node"))),
        )
    graph = Graph(NAMESPACES, (tier,), (simple,))
    assert graph.item_type(ItemRef(tier_name, 0)) == name("node")
    with pytest.raises(ValueError, match="position tier.*missing"):
        graph.positions(name("missing"))
    with pytest.raises(ValueError, match="names undeclared tier.*missing"):
        graph.item_type(ItemRef(name("missing"), 0))


def test_relation_and_position_value_refusals() -> None:
    """Relation instances and sparse position entries require valid unique targets."""
    tier_name = name("x")
    tier = Tier(TierDeclaration(tier_name, "X"), (Item(),))
    simple = SimpleRelationDeclaration(name("members"), tier_name, name("node"))
    edge = RelationInstance(simple.name, ItemRef(tier_name, 0), ItemRef(tier_name, 0))
    with pytest.raises(ValueError, match="bipartite relation declaration"):
        Graph(NAMESPACES, (tier,), (simple,), (edge,))
    link = BipartiteRelationDeclaration(name("link"), name("node"), name("node"))
    untyped_tier = Tier(TierDeclaration(name("raw"), "Raw"), (Item(),))
    untyped_edge = RelationInstance(
        link.name, ItemRef(untyped_tier.declaration.name, 0), ItemRef(tier_name, 0)
    )
    with pytest.raises(ValueError, match="belongs to untyped tier"):
        Graph(NAMESPACES, (tier, untyped_tier), (simple, link), (untyped_edge,))
    mark = AttributeDeclaration(name("mark"), AttributeDomain.POSITION, XsdType.STRING)
    valued = Position(
        PositionRef(tier_name, 0),
        (AttributeValue(mark.name, XsdType.STRING, "x"),),
    )
    with pytest.raises(ValueError, match="duplicate position value"):
        Graph(
            NAMESPACES,
            (tier,),
            (simple,),
            attribute_declarations=(mark,),
            position_values=(valued, valued),
        )
    with pytest.raises(ValueError, match="names undeclared tier.*missing"):
        Graph(
            NAMESPACES,
            (tier,),
            (simple,),
            attribute_declarations=(mark,),
            position_values=(
                Position(PositionRef(name("missing"), 0), valued.attributes),
            ),
        )


def test_invariant_checks_accept_repeats_and_diamonds() -> None:
    """Repeated parents and converging acyclic paths satisfy declared invariants."""
    tier_name = name("x")
    item_type = name("node")
    tier = Tier(TierDeclaration(tier_name, "X"), tuple(Item() for _ in range(4)))
    simple = SimpleRelationDeclaration(name("members"), tier_name, item_type)
    link = BipartiteRelationDeclaration(
        name("link"), item_type, item_type, single_parent=True
    )
    dag = BipartiteRelationDeclaration(name("dag"), item_type, item_type, acyclic=True)
    relations = (
        RelationInstance(link.name, ItemRef(tier_name, 0), ItemRef(tier_name, 1)),
        RelationInstance(link.name, ItemRef(tier_name, 0), ItemRef(tier_name, 1)),
        RelationInstance(dag.name, ItemRef(tier_name, 0), ItemRef(tier_name, 1)),
        RelationInstance(dag.name, ItemRef(tier_name, 0), ItemRef(tier_name, 2)),
        RelationInstance(dag.name, ItemRef(tier_name, 1), ItemRef(tier_name, 3)),
        RelationInstance(dag.name, ItemRef(tier_name, 2), ItemRef(tier_name, 3)),
    )
    assert (
        Graph(NAMESPACES, (tier,), (simple, link, dag), relations).relations
        == relations
    )


def test_untyped_tier_is_allowed_but_has_no_item_type() -> None:
    """Structure may contain items whose type has not been supplied by membership."""
    tier_name = name("raw")
    graph = Graph(NAMESPACES, (Tier(TierDeclaration(tier_name, "Raw"), (Item(),)),), ())
    assert graph.canonical_items() == (ItemRef(tier_name, 0),)
    with pytest.raises(ValueError, match="untyped"):
        graph.item_type(ItemRef(tier_name, 0))


def test_deep_acyclic_chain_is_iterative() -> None:
    """Validation accepts a chain deeper than Python's recursion limit."""
    tier_name = name("nodes")
    item_type = name("node")
    item_count = 1500
    tier = Tier(
        TierDeclaration(tier_name, "Nodes"), tuple(Item() for _ in range(item_count))
    )
    simple = SimpleRelationDeclaration(name("members"), tier_name, item_type)
    links = BipartiteRelationDeclaration(
        name("links"), item_type, item_type, acyclic=True
    )
    relations = tuple(
        RelationInstance(
            links.name, ItemRef(tier_name, index), ItemRef(tier_name, index + 1)
        )
        for index in range(item_count - 1)
    )
    assert len(Graph(NAMESPACES, (tier,), (simple, links), relations).relations) == len(
        relations
    )


def test_durable_ids_and_reference_refusals() -> None:
    """Durable ids stay flat and malformed structural references name the offender."""
    tier_name = name("x")
    tier = Tier(TierDeclaration(tier_name, "X"), (Item("same"), Item("same")))
    with pytest.raises(ValueError, match="duplicate durable id 'same'"):
        Graph(NAMESPACES, (tier,), ())
    graph = Graph(NAMESPACES, (Tier(tier.declaration, (Item("id"),)),), ())
    assert graph.tiers[0].items[0].durable_id == "id"
    with pytest.raises(ValueError, match="outside tier"):
        graph.item_type(ItemRef(tier_name, 3))


def test_durable_position_reference_survives_insertion_but_coordinate_does_not() -> (
    None
):
    """A promoted boundary follows its identity while a coordinate remains structural."""
    tier_name = name("placements")
    original = Graph(
        NAMESPACES,
        (Tier(TierDeclaration(tier_name, "Placements"), (Item(), Item())),),
        (),
    )
    coordinate = PositionRef(tier_name, 1)
    promoted, durable = original.promote_position(coordinate, "middle-boundary")
    assert promoted.resolve_position(durable) == coordinate
    carried = promoted.position_values[0]
    inserted = Graph(
        NAMESPACES,
        (
            Tier(
                TierDeclaration(tier_name, "Placements"),
                (Item(), *promoted.tiers[0].items),
            ),
        ),
        (),
        position_values=(Position(PositionRef(tier_name, 2), (), carried.durable_id),),
    )
    assert inserted.resolve_position(durable) == PositionRef(tier_name, 2)
    assert inserted.resolve_position(coordinate) == PositionRef(tier_name, 1)
    assert inserted.resolve_position(coordinate) != inserted.resolve_position(durable)


def test_durable_item_reference_resolves_after_insertion() -> None:
    """Item durable references resolve by carried identity rather than old coordinate."""
    tier_name = name("items")
    graph = Graph(
        NAMESPACES,
        (Tier(TierDeclaration(tier_name, "Items"), (Item(),)),),
        (),
    )
    promoted, durable = graph.promote_item(ItemRef(tier_name, 0), "original-item")
    inserted = Graph(
        NAMESPACES,
        (
            Tier(
                promoted.tiers[0].declaration,
                (Item(), *promoted.tiers[0].items),
            ),
        ),
        (),
    )
    assert inserted.resolve_item(durable) == ItemRef(tier_name, 1)
    assert inserted.resolve_item(ItemRef(tier_name, 0)) == ItemRef(tier_name, 0)


@pytest.mark.parametrize(
    ("reference", "resolver", "message"),
    [
        (DurableItemRef("missing-item"), Graph.resolve_item, "missing-item"),
        (
            DurablePositionRef("missing-position"),
            Graph.resolve_position,
            "missing-position",
        ),
    ],
)
def test_unknown_durable_reference_is_refused_by_name(
    reference: DurableItemRef | DurablePositionRef,
    resolver: object,
    message: str,
) -> None:
    """Durable resolution never falls back when its exact id is absent."""
    graph = Graph(NAMESPACES, (), ())
    assert callable(resolver)
    with pytest.raises(ValueError, match=message):
        resolver(graph, reference)


@pytest.mark.parametrize(
    ("resolver", "reference", "expected"),
    [
        (Graph.resolve_item, DurablePositionRef("position-id"), "DurableItemRef"),
        (Graph.resolve_position, DurableItemRef("item-id"), "DurablePositionRef"),
    ],
)
def test_resolvers_refuse_the_wrong_reference_class(
    resolver: object,
    reference: DurableItemRef | DurablePositionRef,
    expected: str,
) -> None:
    """Runtime resolution enforces the identity level expressed by its annotation."""
    graph = Graph(NAMESPACES, (), ())
    assert callable(resolver)
    with pytest.raises(
        TypeError, match=rf"expected .*{expected}; got {type(reference).__name__}"
    ):
        resolver(graph, reference)


def test_promotion_ids_are_independent_of_promotion_order() -> None:
    """Caller-owned semantic ids do not depend on prior item or position promotion."""
    tier_name = name("x")
    graph = Graph(
        NAMESPACES,
        (Tier(TierDeclaration(tier_name, "X"), (Item(), Item())),),
        (),
    )
    first, first_zero = graph.promote_item(ItemRef(tier_name, 0), "zero")
    first, first_one = first.promote_item(ItemRef(tier_name, 1), "one")
    second, second_one = graph.promote_item(ItemRef(tier_name, 1), "one")
    second, second_zero = second.promote_item(ItemRef(tier_name, 0), "zero")
    assert (first_zero, first_one) == (second_zero, second_one)
    assert first == second

    item_first, item_ref = graph.promote_item(ItemRef(tier_name, 0), "zero")
    item_first, position_ref = item_first.promote_position(
        PositionRef(tier_name, 1), "middle"
    )
    position_first, reverse_position_ref = graph.promote_position(
        PositionRef(tier_name, 1), "middle"
    )
    position_first, reverse_item_ref = position_first.promote_item(
        ItemRef(tier_name, 0), "zero"
    )
    assert (item_ref, position_ref) == (reverse_item_ref, reverse_position_ref)
    assert item_first == position_first


def test_promotion_refuses_a_supplied_id_collision() -> None:
    """A first promotion must pass its semantic id through the flat namespace."""
    tier_name = name("x")
    graph = Graph(
        NAMESPACES,
        (Tier(TierDeclaration(tier_name, "X"), (Item("taken"), Item())),),
        (),
    )
    with pytest.raises(
        ValueError,
        match=(
            "duplicate durable id 'taken'; item at tier 0, index 0 "
            "collides with position"
        ),
    ):
        graph.promote_position(PositionRef(tier_name, 1), "taken")


def test_promotion_is_idempotent_and_positions_remain_sparse() -> None:
    """Repeated promotion preserves ids and stores only the promoted boundary."""
    tier_name = name("x")
    graph = Graph(
        NAMESPACES,
        (Tier(TierDeclaration(tier_name, "X"), (Item(),)),),
        (),
    )
    independently_promoted, independent_item_ref = graph.promote_item(
        ItemRef(tier_name, 0), "the-item"
    )
    equivalent = Graph(NAMESPACES, graph.tiers, ())
    _, equivalent_item_ref = equivalent.promote_item(ItemRef(tier_name, 0), "the-item")
    item_graph, item_ref = graph.promote_item(ItemRef(tier_name, 0), "the-item")
    assert independently_promoted == item_graph
    assert independent_item_ref == item_ref
    assert equivalent_item_ref == item_ref
    same_item_graph, same_item_ref = item_graph.promote_item(
        ItemRef(tier_name, 0), "ignored-replacement"
    )
    assert same_item_graph is item_graph
    assert same_item_ref == item_ref
    position_graph, position_ref = item_graph.promote_position(
        PositionRef(tier_name, 1), "end-boundary"
    )
    same_position_graph, same_position_ref = position_graph.promote_position(
        PositionRef(tier_name, 1), "ignored-replacement"
    )
    assert same_position_graph is position_graph
    assert same_position_ref == position_ref
    assert len(position_graph.position_values) == 1
    assert position_graph.position_values[0].attributes == ()


def test_durable_ids_survive_json_data_round_trip() -> None:
    """Both promoted identity forms remain present through public serialization."""
    tier_name = name("x")
    graph = Graph(
        NAMESPACES,
        (Tier(TierDeclaration(tier_name, "X"), (Item(),)),),
        (),
    )
    graph, item_ref = graph.promote_item(ItemRef(tier_name, 0), "the-item")
    graph, position_ref = graph.promote_position(
        PositionRef(tier_name, 1), "end-boundary"
    )
    data = json.loads(json.dumps(graph.to_data(), allow_nan=False))
    assert data["tiers"][0]["items"][0]["durable_id"] == item_ref.durable_id
    assert data["position_values"][0]["durable_id"] == position_ref.durable_id
    assert item_ref.to_data() == {"durable_id": item_ref.durable_id}
    assert position_ref.to_data() == {"durable_id": position_ref.durable_id}


def test_position_durable_ids_share_the_flat_namespace() -> None:
    """Position identity cannot collide with item or relation identity."""
    tier_name = name("x")
    tier = Tier(TierDeclaration(tier_name, "X"), (Item("same"),))
    with pytest.raises(ValueError, match="duplicate durable id 'same'.*position"):
        Graph(
            NAMESPACES,
            (tier,),
            (),
            position_values=(Position(PositionRef(tier_name, 0), (), "same"),),
        )


def test_relation_durable_ids_are_unique_between_relations() -> None:
    """Stable relation identities cannot collide with other relations."""
    tier_name = name("x")
    item_type = name("node")
    tier = Tier(TierDeclaration(tier_name, "X"), (Item(), Item()))
    simple = SimpleRelationDeclaration(name("members"), tier_name, item_type)
    link = BipartiteRelationDeclaration(name("link"), item_type, item_type)
    edge = RelationInstance(
        link.name, ItemRef(tier_name, 0), ItemRef(tier_name, 1), "dup"
    )
    with pytest.raises(ValueError, match="duplicate durable id 'dup'"):
        Graph(NAMESPACES, (tier,), (simple, link), (edge, edge))


def test_relation_and_item_durable_ids_share_one_unique_namespace() -> None:
    """Stable relation identities cannot collide with item identities."""
    tier_name = name("x")
    item_type = name("node")
    tier = Tier(TierDeclaration(tier_name, "X"), (Item("item-id"), Item()))
    simple = SimpleRelationDeclaration(name("members"), tier_name, item_type)
    link = BipartiteRelationDeclaration(name("link"), item_type, item_type)
    colliding = RelationInstance(
        link.name, ItemRef(tier_name, 0), ItemRef(tier_name, 1), "item-id"
    )
    with pytest.raises(
        ValueError,
        match=(
            "duplicate durable id 'item-id'; item at tier 0, index 0 "
            "collides with relation instance 0"
        ),
    ):
        Graph(NAMESPACES, (tier,), (simple, link), (colliding,))


@pytest.mark.parametrize("reference_type", [ItemRef, PositionRef])
@pytest.mark.parametrize(("index", "spelling"), [(1.5, "1.5"), (True, "True")])
def test_reference_indices_must_be_integral_at_construction(
    reference_type: type[ItemRef] | type[PositionRef], index: object, spelling: str
) -> None:
    """References refuse fractional indices and bool's integer disguise immediately."""
    tier_name = name("x")
    with pytest.raises(ValueError, match=f"non-integral index {spelling}"):
        reference_type(tier_name, index)  # type: ignore[arg-type]


def test_valued_position_index_must_be_integral() -> None:
    """A fractional sparse boundary is refused before it can be embedded."""
    tier_name = name("x")
    with pytest.raises(ValueError, match=r"position.*non-integral index 1\.5"):
        PositionRef(tier_name, 1.5)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "kwargs"),
    [("single-parent", {"single_parent": 1}), ("acyclic", {"acyclic": "yes"})],
)
def test_relation_structural_promises_must_be_boolean(
    field: str, kwargs: dict[str, object]
) -> None:
    """Relation promises refuse values that would serialize as non-booleans."""
    with pytest.raises(ValueError, match=rf"{field} promise .* must be boolean"):
        BipartiteRelationDeclaration(
            name("link"),
            name("left"),
            name("right"),
            **kwargs,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "operation",
    [
        lambda: QualifiedName("", "x"),
        lambda: QualifiedName(NS, ""),
        lambda: NamespaceDeclaration("", NS),
        lambda: NamespaceDeclaration("t", ""),
        lambda: TierDeclaration(name("x"), ""),
        lambda: Item(""),
        lambda: DurableItemRef(""),
        lambda: DurablePositionRef(""),
        lambda: Position(PositionRef(name("x"), 0), (), ""),
        lambda: RelationInstance(
            name("links"), ItemRef(name("x"), 0), ItemRef(name("x"), 0), ""
        ),
    ],
)
def test_empty_names_are_refused(operation: object) -> None:
    """Every declared or carried empty name is refused at its constructor."""
    assert callable(operation)
    with pytest.raises(ValueError, match="must not be empty"):
        operation()
