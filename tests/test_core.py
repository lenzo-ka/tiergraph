"""The reference kernel satisfies the reusable construction laws."""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests.conformance.kernel import KernelLawSuite
from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    BoundarySide,
    DurableItemRef,
    DurablePositionRef,
    Graph,
    GraphValidationError,
    Item,
    ItemRef,
    NamespaceDeclaration,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    Position,
    PositionRef,
    QualifiedName,
    RelationEndpointKind,
    RelationInstance,
    RelationSideDeclaration,
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


def test_every_attribute_carrier_canonicalizes_by_qualified_name() -> None:
    """Nested constructors remove presentation order from all attribute tuples."""
    first = AttributeValue(name("a"), XsdType.STRING, "first")
    second = AttributeValue(name("z"), XsdType.STRING, "second")
    supplied = (second, first)
    expected = (first, second)
    tier_name = name("tier")
    item_ref = ItemRef(tier_name, 0)
    side = RelationSideDeclaration((RelationEndpointKind.ITEM,), (tier_name,))

    assert Item(attributes=supplied).attributes == expected
    assert (
        Tier(TierDeclaration(tier_name, "Tier"), attributes=supplied).attributes
        == expected
    )
    assert Position(PositionRef(tier_name, 0), supplied).attributes == expected
    assert (
        SimpleRelationDeclaration(
            name("simple"), tier_name, name("type"), supplied
        ).attributes
        == expected
    )
    assert (
        BipartiteRelationDeclaration(
            name("binary"), name("left"), name("right"), attributes=supplied
        ).attributes
        == expected
    )
    assert (
        PolyadicRelationDeclaration(
            name("polyadic"), side, side, attributes=supplied
        ).attributes
        == expected
    )
    assert (
        RelationInstance(
            name("binary"), item_ref, item_ref, attributes=supplied
        ).attributes
        == expected
    )
    assert (
        PolyadicRelationInstance(
            name("polyadic"), (item_ref,), (item_ref,), attributes=supplied
        ).attributes
        == expected
    )


def test_relation_side_allowed_sets_have_canonical_order() -> None:
    """Allowed endpoint kinds and tiers compare independently of supply order."""
    left = RelationSideDeclaration(
        (RelationEndpointKind.ITEM, RelationEndpointKind.BOUNDARY),
        (name("z"), name("a")),
    )
    right = RelationSideDeclaration(
        (RelationEndpointKind.BOUNDARY, RelationEndpointKind.ITEM),
        (name("a"), name("z")),
    )
    assert left == right
    assert left.to_data() == right.to_data()


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


def test_namespace_declaration_supply_order_is_canonicalized() -> None:
    """Namespace declaration order is absent from equality and public data."""
    first = NamespaceDeclaration("z", "urn:z")
    second = NamespaceDeclaration("a", "urn:a")
    forward = Graph((first, second), (), ())
    reverse = Graph((second, first), (), ())
    assert forward == reverse
    assert forward.to_data() == reverse.to_data()
    assert forward.namespaces == (second, first)


def test_relation_declaration_supply_order_is_canonicalized() -> None:
    """Relation declaration order is absent from equality and public data."""
    first = BipartiteRelationDeclaration(name("z"), name("left"), name("right"))
    second = BipartiteRelationDeclaration(name("a"), name("left"), name("right"))
    forward = Graph(NAMESPACES, (), (first, second))
    reverse = Graph(NAMESPACES, (), (second, first))
    assert forward == reverse
    assert forward.to_data() == reverse.to_data()
    assert forward.relation_declarations == (second, first)


def test_attribute_declaration_supply_order_is_canonicalized() -> None:
    """Attribute declaration order is absent from equality and public data."""
    first = AttributeDeclaration(name("z"), AttributeDomain.DOCUMENT, XsdType.STRING)
    second = AttributeDeclaration(name("a"), AttributeDomain.DOCUMENT, XsdType.STRING)
    forward = Graph(NAMESPACES, (), (), attribute_declarations=(first, second))
    reverse = Graph(NAMESPACES, (), (), attribute_declarations=(second, first))
    assert forward == reverse
    assert forward.to_data() == reverse.to_data()
    assert forward.attribute_declarations == (second, first)


def test_tier_order_remains_observable() -> None:
    """Canonicalizing declarations does not erase tier declaration order."""
    first_tier = Tier(TierDeclaration(name("a"), "A"))
    second_tier = Tier(TierDeclaration(name("b"), "B"))
    tier_reversed = Graph(NAMESPACES, (second_tier, first_tier), ())
    original = Graph(NAMESPACES, (first_tier, second_tier), ())
    assert original != tier_reversed
    assert original.to_data() != tier_reversed.to_data()


def test_item_order_within_a_tier_remains_observable() -> None:
    """Canonicalizing declarations does not erase a tier's item sequence."""
    tier = Tier(TierDeclaration(name("tier"), "Tier"), (Item("one"), Item("two")))
    original = Graph(NAMESPACES, (tier,), ())
    item_reversed = Graph(
        NAMESPACES, (Tier(tier.declaration, tuple(reversed(tier.items))),), ()
    )
    assert original != item_reversed
    assert original.to_data() != item_reversed.to_data()


def test_relation_instance_order_remains_observable() -> None:
    """Relation instances retain a potentially meaningful child or link sequence."""
    tier_name = name("tier")
    item_type = name("item")
    tier = Tier(TierDeclaration(tier_name, "Tier"), (Item(), Item()))
    members = SimpleRelationDeclaration(name("members"), tier_name, item_type)
    links = BipartiteRelationDeclaration(name("links"), item_type, item_type)
    forward_link = RelationInstance(
        links.name, ItemRef(tier_name, 0), ItemRef(tier_name, 1)
    )
    reverse_link = RelationInstance(
        links.name, ItemRef(tier_name, 1), ItemRef(tier_name, 0)
    )
    forward = Graph(NAMESPACES, (tier,), (members, links), (forward_link, reverse_link))
    reverse = Graph(NAMESPACES, (tier,), (members, links), (reverse_link, forward_link))
    assert forward != reverse
    assert forward.to_data() != reverse.to_data()


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
    promoted, durable = graph.promote_position(
        PositionRef(tier_name, 1), "stored-position"
    )
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


def test_acyclic_boundary_relation_resolves_mixed_anchor_spellings() -> None:
    """A cycle through coincident boundary anchors is refused as a real cycle."""
    tier_name = name("x")
    item_type = name("node")
    tier = Tier(
        TierDeclaration(tier_name, "X"),
        (Item("a"), Item("b"), Item("c")),
    )
    members = SimpleRelationDeclaration(name("members"), tier_name, item_type)
    links = BipartiteRelationDeclaration(
        name("links"),
        item_type,
        item_type,
        left_endpoint=RelationEndpointKind.BOUNDARY,
        right_endpoint=RelationEndpointKind.BOUNDARY,
        acyclic=True,
    )
    before_a = DurablePositionRef(DurableItemRef("a"), BoundarySide.BEFORE)
    before_b = DurablePositionRef(DurableItemRef("b"), BoundarySide.BEFORE)
    after_a = DurablePositionRef(DurableItemRef("a"), BoundarySide.AFTER)
    before_tier = DurablePositionRef(tier_name, BoundarySide.BEFORE)
    relations = (
        RelationInstance(links.name, before_a, before_b),
        RelationInstance(links.name, after_a, before_tier),
    )
    with pytest.raises(ValueError, match="closes a cycle"):
        Graph(NAMESPACES, (tier,), (members, links), relations)


def test_single_parent_boundary_relation_resolves_mixed_anchor_spellings() -> None:
    """Coincident target anchors cannot conceal a boundary's second parent."""
    tier_name = name("x")
    item_type = name("node")
    tier = Tier(
        TierDeclaration(tier_name, "X"),
        (Item("a"), Item("b"), Item("c")),
    )
    members = SimpleRelationDeclaration(name("members"), tier_name, item_type)
    links = BipartiteRelationDeclaration(
        name("links"),
        item_type,
        item_type,
        left_endpoint=RelationEndpointKind.BOUNDARY,
        right_endpoint=RelationEndpointKind.BOUNDARY,
        single_parent=True,
    )
    before_a = DurablePositionRef(DurableItemRef("a"), BoundarySide.BEFORE)
    after_b = DurablePositionRef(DurableItemRef("b"), BoundarySide.AFTER)
    before_b = DurablePositionRef(DurableItemRef("b"), BoundarySide.BEFORE)
    after_a = DurablePositionRef(DurableItemRef("a"), BoundarySide.AFTER)
    relations = (
        RelationInstance(links.name, before_a, before_b),
        RelationInstance(links.name, after_b, after_a),
    )
    with pytest.raises(ValueError, match="a second parent"):
        Graph(NAMESPACES, (tier,), (members, links), relations)


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


@pytest.mark.parametrize(
    "operation",
    [
        lambda graph: graph.resolve_item(ItemRef(name("x"), 9)),
        lambda graph: graph.resolve_position(PositionRef(name("x"), 9)),
        lambda graph: graph.promote_item(ItemRef(name("x"), 9), "promoted"),
        lambda graph: graph.promote_position(PositionRef(name("x"), 9), "promoted"),
        lambda graph: graph.item_type(ItemRef(name("x"), 9)),
    ],
    ids=(
        "resolve-item",
        "resolve-position",
        "promote-item",
        "promote-position",
        "item-type",
    ),
)
def test_public_reference_errors_are_not_graph_validation_errors(
    operation: Callable[[Graph], object],
) -> None:
    """Bad arguments do not imply that an already-valid graph broke its contract."""
    tier_name = name("x")
    tier = Tier(TierDeclaration(tier_name, "X"), (Item(),))
    membership = SimpleRelationDeclaration(name("members"), tier_name, name("item"))
    graph = Graph(NAMESPACES, (tier,), (membership,))

    with pytest.raises(ValueError, match=r"x\[9\].*outside tier") as caught:
        operation(graph)

    assert type(caught.value) is ValueError
    assert not isinstance(caught.value, GraphValidationError)


def test_graph_construction_contract_raises_graph_validation_error() -> None:
    """Invalid graph content retains its construction-specific exception type."""
    tier = Tier(TierDeclaration(name("x"), "X"), (Item(),))

    with pytest.raises(GraphValidationError, match="duplicate tier"):
        Graph(NAMESPACES, (tier, tier), ())


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
    assert durable == DurablePositionRef(
        DurableItemRef("middle-boundary"), BoundarySide.BEFORE
    )
    assert promoted.resolve_position(durable) == coordinate
    inserted = Graph(
        NAMESPACES,
        (
            Tier(
                TierDeclaration(tier_name, "Placements"),
                (Item(), *promoted.tiers[0].items),
            ),
        ),
        (),
    )
    assert inserted.resolve_position(durable) == PositionRef(tier_name, 2)
    assert inserted.resolve_position(coordinate) == PositionRef(tier_name, 1)
    assert inserted.resolve_position(coordinate) != inserted.resolve_position(durable)


def test_coincident_position_anchors_intentionally_diverge_after_insertion() -> None:
    """Coincident item and empty-tier edges retain distinct edit intentions."""
    tier_name = name("placements")
    empty_name = name("empty")
    graph = Graph(
        NAMESPACES,
        (
            Tier(
                TierDeclaration(tier_name, "Placements"),
                (Item("a"), Item("b")),
            ),
            Tier(TierDeclaration(empty_name, "Empty")),
        ),
        (),
    )
    after_a = DurablePositionRef(DurableItemRef("a"), BoundarySide.AFTER)
    before_b = DurablePositionRef(DurableItemRef("b"), BoundarySide.BEFORE)
    before_empty = DurablePositionRef(empty_name, BoundarySide.BEFORE)
    after_empty = DurablePositionRef(empty_name, BoundarySide.AFTER)
    assert after_a != before_b
    assert before_empty != after_empty
    assert graph.resolve_position(after_a) == graph.resolve_position(before_b)
    assert graph.resolve_position(before_empty) == graph.resolve_position(after_empty)

    inserted = Graph(
        NAMESPACES,
        (
            Tier(
                graph.tiers[0].declaration,
                (graph.tiers[0].items[0], Item("x"), graph.tiers[0].items[1]),
            ),
            Tier(graph.tiers[1].declaration, (Item("first"),)),
        ),
        (),
    )
    assert inserted.resolve_position(after_a) == PositionRef(tier_name, 1)
    assert inserted.resolve_position(before_b) == PositionRef(tier_name, 2)
    assert inserted.resolve_position(before_empty) == PositionRef(empty_name, 0)
    assert inserted.resolve_position(after_empty) == PositionRef(empty_name, 1)


def test_removed_boundary_anchor_refuses_on_both_sides_by_name() -> None:
    """Neither side of a missing item anchor can silently retarget a neighbour."""
    tier_name = name("placements")
    anchor_id = "lead-vocal"
    graph = Graph(
        NAMESPACES,
        (Tier(TierDeclaration(tier_name, "Placements"), (Item(anchor_id),)),),
        (),
    )
    before = DurablePositionRef(DurableItemRef(anchor_id), BoundarySide.BEFORE)
    after = DurablePositionRef(DurableItemRef(anchor_id), BoundarySide.AFTER)
    assert graph.resolve_position(before) == PositionRef(tier_name, 0)
    assert graph.resolve_position(after) == PositionRef(tier_name, 1)
    removed = Graph(NAMESPACES, (Tier(TierDeclaration(tier_name, "Placements")),), ())
    for reference in (before, after):
        with pytest.raises(ValueError, match="lead-vocal.*not found"):
            removed.resolve_position(reference)


def test_outer_and_empty_tier_boundaries_are_promotable() -> None:
    """Tier anchors cover both outer edges and the empty tier's sole boundary."""
    full_name = name("full")
    empty_name = name("empty")
    graph = Graph(
        NAMESPACES,
        (
            Tier(TierDeclaration(full_name, "Full"), (Item(),)),
            Tier(TierDeclaration(empty_name, "Empty")),
        ),
        (),
    )
    graph, leading = graph.promote_position(PositionRef(full_name, 0), "unused")
    graph, trailing = graph.promote_position(PositionRef(full_name, 1), "unused")
    graph, empty = graph.promote_position(PositionRef(empty_name, 0), "unused")
    assert leading == DurablePositionRef(full_name, BoundarySide.BEFORE)
    assert trailing == DurablePositionRef(full_name, BoundarySide.AFTER)
    assert empty == DurablePositionRef(empty_name, BoundarySide.BEFORE)
    assert graph.resolve_position(leading) == PositionRef(full_name, 0)
    assert graph.resolve_position(trailing) == PositionRef(full_name, 1)
    assert graph.resolve_position(empty) == PositionRef(empty_name, 0)
    with pytest.raises(ValueError, match="missing-tier.*not declared"):
        graph.resolve_position(
            DurablePositionRef(name("missing-tier"), BoundarySide.BEFORE)
        )


def test_boundary_relation_endpoint_uses_anchor_type_and_names_wrong_type() -> None:
    """Boundary endpoint declarations type-check the anchor and name a mismatch."""
    cue_tier = name("cues")
    placement_tier = name("placements")
    other_tier = name("other")
    cue_type = name("cue")
    placement_type = name("placement")
    other_type = name("other-type")
    boundary = DurablePositionRef(DurableItemRef("lead-vocal"), BoundarySide.BEFORE)
    tiers = (
        Tier(TierDeclaration(cue_tier, "Cues"), (Item(),)),
        Tier(TierDeclaration(placement_tier, "Placements"), (Item("lead-vocal"),)),
        Tier(TierDeclaration(other_tier, "Other"), (Item("wrong-anchor"),)),
    )
    simple = (
        SimpleRelationDeclaration(name("cue-members"), cue_tier, cue_type),
        SimpleRelationDeclaration(
            name("placement-members"), placement_tier, placement_type
        ),
        SimpleRelationDeclaration(name("other-members"), other_tier, other_type),
    )
    declaration = BipartiteRelationDeclaration(
        name("points-to"),
        cue_type,
        placement_type,
        right_endpoint=RelationEndpointKind.BOUNDARY,
    )
    relation = RelationInstance(declaration.name, ItemRef(cue_tier, 0), boundary)
    outer_relation = RelationInstance(
        declaration.name,
        ItemRef(cue_tier, 0),
        DurablePositionRef(placement_tier, BoundarySide.AFTER),
    )
    relations = (relation, outer_relation)
    assert (
        Graph(NAMESPACES, tiers, (*simple, declaration), relations).relations
        == relations
    )
    wrong = RelationInstance(
        declaration.name,
        ItemRef(cue_tier, 0),
        DurablePositionRef(DurableItemRef("wrong-anchor"), BoundarySide.AFTER),
    )
    with pytest.raises(
        ValueError, match="right endpoint.*wrong-anchor.*other-type.*placement"
    ):
        Graph(NAMESPACES, tiers, (*simple, declaration), (wrong,))


def test_boundary_endpoint_refusals_name_kind_and_missing_anchors() -> None:
    """Boundary declarations refuse item endpoints and both absent anchor kinds."""
    tier_name = name("nodes")
    node_type = name("node")
    tier = Tier(TierDeclaration(tier_name, "Nodes"), (Item("present"),))
    members = SimpleRelationDeclaration(name("members"), tier_name, node_type)
    boundary_link = BipartiteRelationDeclaration(
        name("boundary-link"),
        node_type,
        node_type,
        right_endpoint=RelationEndpointKind.BOUNDARY,
    )
    item_right = RelationInstance(
        boundary_link.name, ItemRef(tier_name, 0), ItemRef(tier_name, 0)
    )
    with pytest.raises(ValueError, match="right endpoint.*is an item.*boundary"):
        Graph(NAMESPACES, (tier,), (members, boundary_link), (item_right,))
    missing_item = RelationInstance(
        boundary_link.name,
        ItemRef(tier_name, 0),
        DurablePositionRef(DurableItemRef("absent"), BoundarySide.BEFORE),
    )
    with pytest.raises(ValueError, match="right endpoint.*absent.*missing anchor"):
        Graph(NAMESPACES, (tier,), (members, boundary_link), (missing_item,))
    missing_tier = RelationInstance(
        boundary_link.name,
        ItemRef(tier_name, 0),
        DurablePositionRef(name("absent-tier"), BoundarySide.AFTER),
    )
    with pytest.raises(
        ValueError, match="right endpoint.*undeclared tier.*absent-tier"
    ):
        Graph(NAMESPACES, (tier,), (members, boundary_link), (missing_tier,))


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
            DurablePositionRef(DurableItemRef("missing-position"), BoundarySide.BEFORE),
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
        (
            Graph.resolve_item,
            DurablePositionRef(DurableItemRef("position-id"), BoundarySide.BEFORE),
            "DurableItemRef",
        ),
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


def test_position_promotion_uses_an_existing_anchor_id() -> None:
    """A boundary shares its item anchor instead of minting another identity."""
    tier_name = name("x")
    graph = Graph(
        NAMESPACES,
        (Tier(TierDeclaration(tier_name, "X"), (Item(), Item("taken"))),),
        (),
    )
    promoted, durable = graph.promote_position(PositionRef(tier_name, 1), "taken")
    assert promoted is graph
    assert durable == DurablePositionRef(DurableItemRef("taken"), BoundarySide.BEFORE)


def test_position_promotion_returns_the_anchor_that_already_stores_values() -> None:
    """A promoted handle continues to identify its stored values after insertion."""
    tier_name = name("x")
    mark = AttributeDeclaration(name("mark"), AttributeDomain.POSITION, XsdType.STRING)
    after_a = DurablePositionRef(DurableItemRef("a"), BoundarySide.AFTER)
    stored = Position(
        after_a,
        (AttributeValue(mark.name, XsdType.STRING, "kept"),),
    )
    graph = Graph(
        NAMESPACES,
        (Tier(TierDeclaration(tier_name, "X"), (Item("a"), Item("b"))),),
        (),
        attribute_declarations=(mark,),
        position_values=(stored,),
    )
    promoted, durable = graph.promote_position(PositionRef(tier_name, 1), "b")
    assert promoted is graph
    assert durable == after_a
    assert promoted.position_values[0].reference == durable

    inserted = Graph(
        NAMESPACES,
        (
            Tier(
                graph.tiers[0].declaration,
                (graph.tiers[0].items[0], Item("x"), graph.tiers[0].items[1]),
            ),
        ),
        (),
        attribute_declarations=(mark,),
        position_values=promoted.position_values,
    )
    assert inserted.resolve_position(durable) == PositionRef(tier_name, 1)
    assert inserted.resolve_position(inserted.position_values[0].reference) == (
        inserted.resolve_position(durable)
    )


def test_promotion_is_idempotent_and_unvalued_positions_remain_derived() -> None:
    """Repeated promotion preserves the anchor and stores no empty position."""
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
        ItemRef(tier_name, 0), "the-item"
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
    assert position_graph.position_values == ()


def test_item_promotion_refuses_a_conflicting_durable_id() -> None:
    """An established identity wins and the refused replacement is named."""
    tier_name = name("x")
    graph = Graph(
        NAMESPACES,
        (Tier(TierDeclaration(tier_name, "X"), (Item("first"),)),),
        (),
    )
    with pytest.raises(
        ValueError,
        match=r"already carries durable id 'first'.*refused.*'second'",
    ):
        graph.promote_item(ItemRef(tier_name, 0), "second")


def test_position_promotion_refuses_a_conflicting_anchor_id() -> None:
    """An interior-boundary conflict is reported in boundary terms."""
    tier_name = name("x")
    graph = Graph(
        NAMESPACES,
        (Tier(TierDeclaration(tier_name, "X"), (Item(), Item("anchor"))),),
        (),
    )
    with pytest.raises(
        ValueError,
        match=r"position .* is before an anchor.*'anchor'.*boundary.*'refused'",
    ):
        graph.promote_position(PositionRef(tier_name, 1), "refused")


def test_anchored_position_reference_has_tagged_json_shape() -> None:
    """The item-or-tier anchor and side remain explicit in public data."""
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
    assert item_ref.to_data() == {"durable_id": item_ref.durable_id}
    assert position_ref.to_data() == {
        "anchor": {"kind": "tier", "tier": tier_name.to_data()},
        "side": "after",
    }
    assert "position_values" in data


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
        lambda: DurablePositionRef(DurableItemRef(""), BoundarySide.BEFORE),
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
