"""One-step relation images and quantified related-item predicates."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import cast

import pytest
from hypothesis import example, given, seed, settings
from hypothesis import strategies as st

from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    Graph,
    Item,
    ItemRef,
    ItemsSelector,
    JsonAttributeValue,
    JsonType,
    NamespaceDeclaration,
    Node,
    NodeKind,
    NodeSet,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    QualifiedName,
    Refusal,
    RefusalStage,
    RelationEndpointKind,
    RelationInstance,
    RelationSideDeclaration,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    Walk,
    WalkDirection,
    XsdType,
    evaluate_selection,
    from_textgrid,
)
from tiergraph.predicate import (
    And,
    Cell,
    Current,
    Elements,
    Equals,
    Has,
    Matches,
    Not,
    Or,
    Predicate,
    PredicateSyntax,
    Quantifier,
    Related,
    compile_predicate,
    format_predicate,
    predicate_loads,
    predicate_to_data,
)
from tiergraph.traversal import relation_image

NS = "urn:example:fixture#"


def q(local: str) -> QualifiedName:
    """Return one name in the neutral fixture namespace."""
    return QualifiedName(NS, local)


def item_nodes(source: Graph, tier: QualifiedName, indices: Iterable[int]) -> NodeSet:
    """Return selected item nodes by graph-local coordinate."""
    return NodeSet(
        source,
        tuple(Node(NodeKind.ITEM, ItemRef(tier, index)) for index in indices),
    )


def labels(nodes: NodeSet) -> list[str]:
    """Return durable labels in canonical node order."""
    tiers = {tier.declaration.name: tier for tier in nodes.graph.tiers}
    result = []
    for node in nodes.nodes:
        assert isinstance(node.reference, ItemRef)
        label = tiers[node.reference.tier].items[node.reference.index].durable_id
        assert label is not None
        result.append(label)
    return result


def selected(source: Graph, tier: QualifiedName, predicate: Predicate) -> list[str]:
    """Bind one predicate and return the labels it retains from a tier."""
    candidates = evaluate_selection(source, ItemsSelector(tier))
    return labels(compile_predicate(predicate).bind(source).select(candidates))


def tone_graph() -> Graph:
    """Build the TONE fixture exactly as the matching plan declares it."""
    segment = q("seg")
    segment_type = q("seg-item")
    tone = q("tone")
    tone_type = q("tone-item")
    host = q("host")
    tv = q("tv")
    tones = tuple(
        Item(f"t{index}", (AttributeValue(tv, XsdType.STRING, value),))
        for index, value in enumerate(("H", "L", "H", "H"))
    )
    return Graph(
        (NamespaceDeclaration("ex", NS),),
        (
            Tier(
                TierDeclaration(segment, "Segments"),
                tuple(Item(f"s{index}") for index in range(4)),
            ),
            Tier(TierDeclaration(tone, "Tones"), tones),
        ),
        (
            SimpleRelationDeclaration(q("seg-members"), segment, segment_type),
            SimpleRelationDeclaration(q("tone-members"), tone, tone_type),
            BipartiteRelationDeclaration(host, tone_type, segment_type),
        ),
        tuple(
            RelationInstance(
                host,
                ItemRef(tone, tone_index),
                ItemRef(segment, segment_index),
            )
            for tone_index, segment_index in ((0, 0), (1, 2), (2, 2), (3, 3))
        ),
        (AttributeDeclaration(tv, AttributeDomain.ITEM, XsdType.STRING),),
    )


def chain_graph() -> Graph:
    """Build the three-item directed-chain fixture."""
    tier = q("i")
    item_type = q("i-item")
    relation = q("next")
    key = q("k")
    return Graph(
        (NamespaceDeclaration("ex", NS),),
        (
            Tier(
                TierDeclaration(tier, "Items"),
                tuple(
                    Item(f"i{index}", (AttributeValue(key, XsdType.STRING, value),))
                    for index, value in enumerate(("a", "b", "b"))
                ),
            ),
        ),
        (
            SimpleRelationDeclaration(q("i-members"), tier, item_type),
            BipartiteRelationDeclaration(relation, item_type, item_type, acyclic=True),
        ),
        (
            RelationInstance(relation, ItemRef(tier, 0), ItemRef(tier, 1)),
            RelationInstance(relation, ItemRef(tier, 1), ItemRef(tier, 2)),
        ),
        (AttributeDeclaration(key, AttributeDomain.ITEM, XsdType.STRING),),
    )


def layered_graph() -> Graph:
    """Build the three-tier constituent and membership fixture."""
    syllable = q("syl")
    constituent = q("con")
    segment = q("seg")
    syllable_type = q("syl-item")
    constituent_type = q("con-item")
    segment_type = q("seg-item")
    constituent_relation = q("constituent")
    member = q("member")
    role = q("role")
    stress = q("stress")
    rows = (
        ("n0", "nucleus", "primary"),
        ("k0", "coda", None),
        ("n1", "nucleus", "primary"),
        ("k1", "coda", None),
        ("n2", "nucleus", None),
    )
    constituents = []
    for label, item_role, item_stress in rows:
        attributes = [AttributeValue(role, XsdType.STRING, item_role)]
        if item_stress is not None:
            attributes.append(AttributeValue(stress, XsdType.STRING, item_stress))
        constituents.append(Item(label, tuple(attributes)))
    relations = [
        RelationInstance(
            constituent_relation,
            ItemRef(syllable, parent),
            ItemRef(constituent, child),
        )
        for parent, child in ((0, 0), (0, 1), (1, 2), (1, 3), (2, 4))
    ]
    relations.extend(
        RelationInstance(
            member,
            ItemRef(constituent, parent),
            ItemRef(segment, child),
        )
        for parent, child in ((0, 0), (2, 1), (3, 2), (4, 3))
    )
    tiers = (
        Tier(
            TierDeclaration(syllable, "Syllables"),
            tuple(Item(f"σ{index}") for index in range(3)),
        ),
        Tier(TierDeclaration(constituent, "Constituents"), tuple(constituents)),
        Tier(
            TierDeclaration(segment, "Segments"),
            tuple(Item(f"g{index}") for index in range(4)),
        ),
    )
    declarations = (
        SimpleRelationDeclaration(q("syl-members"), syllable, syllable_type),
        SimpleRelationDeclaration(q("con-members"), constituent, constituent_type),
        SimpleRelationDeclaration(q("seg-members"), segment, segment_type),
        BipartiteRelationDeclaration(
            constituent_relation, syllable_type, constituent_type
        ),
        BipartiteRelationDeclaration(member, constituent_type, segment_type),
    )
    return Graph(
        (NamespaceDeclaration("ex", NS),),
        tiers,
        declarations,
        tuple(relations),
        (
            AttributeDeclaration(role, AttributeDomain.ITEM, XsdType.STRING),
            AttributeDeclaration(stress, AttributeDomain.ITEM, XsdType.STRING),
        ),
    )


def incidence_graph(
    edges: tuple[tuple[int, int], ...], *, polyadic: bool = False
) -> Graph:
    """Build a four-item graph from one drawn incidence set."""
    tier = q("node")
    item_type = q("node-item")
    relation = q("edge")
    membership = SimpleRelationDeclaration(q("node-members"), tier, item_type)
    declaration: BipartiteRelationDeclaration | PolyadicRelationDeclaration
    if polyadic:
        side = RelationSideDeclaration(
            (RelationEndpointKind.ITEM,), (tier,), minimum=1, maximum=1
        )
        declaration = PolyadicRelationDeclaration(relation, side, side)
        relations: tuple[RelationInstance, ...] = ()
        polyadic_relations = tuple(
            PolyadicRelationInstance(
                relation,
                (ItemRef(tier, left),),
                (ItemRef(tier, right),),
            )
            for left, right in edges
        )
    else:
        declaration = BipartiteRelationDeclaration(relation, item_type, item_type)
        relations = tuple(
            RelationInstance(
                relation,
                ItemRef(tier, left),
                ItemRef(tier, right),
            )
            for left, right in edges
        )
        polyadic_relations = ()
    nodes = Tier(
        TierDeclaration(tier, "Nodes"),
        tuple(Item(f"n{index}") for index in range(4)),
    )
    return Graph(
        (NamespaceDeclaration("ex", NS),),
        (nodes,),
        (membership, declaration),
        relations,
        polyadic_relations=polyadic_relations,
    )


def quantified_graph(
    edges: tuple[tuple[int, int], ...], targets: frozenset[int]
) -> Graph:
    """Add integer target marks to one four-item incidence graph."""
    tier = q("node")
    item_type = q("node-item")
    relation = q("edge")
    key = q("k")
    items = []
    for index in range(4):
        attributes: tuple[AttributeValue, ...] = ()
        if index in targets:
            attributes = (AttributeValue(key, XsdType.INTEGER, "1"),)
        elif index % 2 == 0:
            attributes = (AttributeValue(key, XsdType.INTEGER, "0"),)
        items.append(Item(f"n{index}", attributes))
    declarations = (
        SimpleRelationDeclaration(q("node-members"), tier, item_type),
        BipartiteRelationDeclaration(relation, item_type, item_type),
    )
    relations = tuple(
        RelationInstance(
            relation,
            ItemRef(tier, left),
            ItemRef(tier, right),
        )
        for left, right in edges
    )
    return Graph(
        (NamespaceDeclaration("ex", NS),),
        (Tier(TierDeclaration(tier, "Nodes"), tuple(items)),),
        declarations,
        relations,
        (AttributeDeclaration(key, AttributeDomain.ITEM, XsdType.INTEGER),),
    )


def test_related_quantifiers_use_the_complete_relation_image() -> None:
    """R1: NONE, ANY, and ALL distinguish absence and mixed targets."""
    source = tone_graph()
    high = Equals(Cell(q("tv")), ("H",))
    assert selected(
        source,
        q("seg"),
        Related(q("host"), WalkDirection.INVERSE, Quantifier.NONE, And(())),
    ) == ["s1"]
    assert selected(
        source,
        q("seg"),
        Related(q("host"), WalkDirection.INVERSE, Quantifier.ANY, high),
    ) == ["s0", "s2", "s3"]
    assert selected(
        source,
        q("seg"),
        Related(q("host"), WalkDirection.INVERSE, Quantifier.ALL, high),
    ) == ["s0", "s1", "s3"]


def test_related_keeps_a_source_reached_in_one_step() -> None:
    """R2: one-step relation matching does not inherit Walk source exclusion."""
    predicate = Related(
        q("next"),
        WalkDirection.FORWARD,
        Quantifier.ANY,
        Equals(Cell(q("k")), ("b",)),
    )
    assert selected(chain_graph(), q("i"), predicate) == ["i0", "i1"]


def test_nested_related_targets_distinguish_empty_constituents() -> None:
    """R3: an existing coda with no member is empty for the nested test."""
    stressed_nucleus = And(
        (
            Equals(Cell(q("role")), ("nucleus",)),
            Has(Cell(q("stress")), alias="none"),
        )
    )
    filled_coda = And(
        (
            Equals(Cell(q("role")), ("coda",)),
            Related(q("member"), WalkDirection.FORWARD, Quantifier.ANY, And(())),
        )
    )
    predicate = And(
        (
            Related(
                q("constituent"),
                WalkDirection.FORWARD,
                Quantifier.ANY,
                stressed_nucleus,
            ),
            Related(
                q("constituent"),
                WalkDirection.FORWARD,
                Quantifier.NONE,
                filled_coda,
            ),
        )
    )
    assert selected(layered_graph(), q("syl"), predicate) == ["σ0"]


EDGE_SETS = st.sets(st.tuples(st.integers(0, 3), st.integers(0, 3)), max_size=16).map(
    lambda value: tuple(sorted(value))
)
SOURCE_SETS = st.sets(st.integers(0, 3), max_size=4).map(frozenset)


@seed(3104)
@settings(max_examples=64, deadline=None)
@example(edges=((0, 0),), source_indices=frozenset({0}))
@given(edges=EDGE_SETS, source_indices=SOURCE_SETS)
def test_relation_image_agrees_with_one_bounded_walk_step(
    edges: tuple[tuple[int, int], ...], source_indices: frozenset[int]
) -> None:
    """R4: both relation shapes and directions differ only by source exclusion."""
    for polyadic in (False, True):
        source = incidence_graph(edges, polyadic=polyadic)
        selected_nodes = item_nodes(source, q("node"), source_indices)
        for direction in WalkDirection:
            image = relation_image(selected_nodes, q("edge"), direction)
            expected = {
                right if direction is WalkDirection.FORWARD else left
                for left, right in edges
                if (
                    left in source_indices
                    if direction is WalkDirection.FORWARD
                    else right in source_indices
                )
            }
            assert image == item_nodes(source, q("node"), expected)
            walked = Walk(selected_nodes, q("edge"), direction, cap=1).evaluate().nodes
            assert image - selected_nodes == walked


def bit_indices(bits: int) -> frozenset[int]:
    """Decode one four-bit subset."""
    return frozenset(index for index in range(4) if bits & (1 << index))


@seed(3105)
@settings(max_examples=64, deadline=None)
@example(edges=((0, 1), (0, 2), (1, 2), (2, 3)))
@given(edges=EDGE_SETS)
def test_related_quantifiers_agree_with_finite_brute_force(
    edges: tuple[tuple[int, int], ...],
) -> None:
    """R5: every candidate and target subset agrees in both directions."""
    tier = q("node")
    relation = q("edge")
    target = Equals(Cell(q("k")), (1,))
    for target_bits in range(16):
        target_indices = bit_indices(target_bits)
        source = quantified_graph(edges, target_indices)
        for direction in WalkDirection:
            any_predicate = Related(relation, direction, Quantifier.ANY, target)
            none = compile_predicate(
                Related(relation, direction, Quantifier.NONE, target)
            ).bind(source)
            all_related = compile_predicate(
                Related(relation, direction, Quantifier.ALL, target)
            ).bind(source)
            not_any = compile_predicate(Not(any_predicate)).bind(source)
            not_any_not = compile_predicate(
                Not(Related(relation, direction, Quantifier.ANY, Not(target)))
            ).bind(source)
            for candidate_bits in range(16):
                candidate_indices = bit_indices(candidate_bits)
                candidates = item_nodes(source, tier, candidate_indices)
                neighbors = {
                    candidate: {
                        right if direction is WalkDirection.FORWARD else left
                        for left, right in edges
                        if (
                            left == candidate
                            if direction is WalkDirection.FORWARD
                            else right == candidate
                        )
                    }
                    for candidate in candidate_indices
                }
                expected_none = {
                    candidate
                    for candidate, related in neighbors.items()
                    if not (related & target_indices)
                }
                expected_all = {
                    candidate
                    for candidate, related in neighbors.items()
                    if related <= target_indices
                }
                none_nodes = none.select(candidates)
                all_nodes = all_related.select(candidates)
                assert none_nodes == item_nodes(source, tier, expected_none)
                assert all_nodes == item_nodes(source, tier, expected_all)
                assert none_nodes == not_any.select(candidates)
                assert all_nodes == not_any_not.select(candidates)


def textgrid_graph() -> Graph:
    """Build the two-tier TG fixture from its exact TextGrid intervals."""
    document = """File type = "ooTextFile short"
"TextGrid"

0
2
<exists>
2
"IntervalTier"
"words"
0
2
2
0
1
"ab"
1
2
"cd"
"IntervalTier"
"phones"
0
2
4
0
0.5
"p"
0.5
1
"a"
1
1.5
"t"
1.5
2
"i"
"""
    return from_textgrid(document).graph


def test_related_reads_imported_containment_in_inverse_direction() -> None:
    """R6: phone candidates match the label on their containing word."""
    source = textgrid_graph()
    textgrid = "urn:tiergraph:textgrid"
    phones = QualifiedName(textgrid, "phones")
    predicate = Related(
        QualifiedName(textgrid, "containment-0-1"),
        WalkDirection.INVERSE,
        Quantifier.ANY,
        Matches(Cell(QualifiedName(textgrid, "value")), "a.*"),
    )
    assert selected(source, phones, predicate) == ["textgrid-3-0", "textgrid-3-1"]


def test_related_json_round_trip_and_direction_refusal() -> None:
    """R7: Related data preserves direction and refuses unknown values."""
    high = Equals(Cell(q("tv")), ("H",))
    predicates = (
        Related(q("host"), WalkDirection.INVERSE, Quantifier.NONE, And(())),
        Related(q("host"), WalkDirection.INVERSE, Quantifier.ANY, high),
        Related(q("host"), WalkDirection.INVERSE, Quantifier.ALL, high),
    )
    for predicate in predicates:
        assert predicate_loads(json.dumps(predicate_to_data(predicate))) == predicate
    syntax = PredicateSyntax((NamespaceDeclaration("ex", NS),), "ex")
    with pytest.raises(ValueError, match="Related has no value-test text; use JSON"):
        format_predicate(predicates[0], syntax)
    invalid = predicate_to_data(predicates[1])
    assert isinstance(invalid, dict)
    invalid["direction"] = "sideways"
    with pytest.raises(Refusal) as caught:
        predicate_loads(json.dumps(invalid))
    assert caught.value.stage is RefusalStage.VALUE
    invalid["direction"] = "inverse"
    invalid["quantifier"] = "several"
    with pytest.raises(Refusal) as caught:
        predicate_loads(json.dumps(invalid))
    assert caught.value.stage is RefusalStage.VALUE


def test_related_target_refuses_current_element_operands() -> None:
    """A Related target is an item test, not a current-element test."""
    predicate = Related(
        q("host"),
        WalkDirection.INVERSE,
        Quantifier.ANY,
        Equals(Current(), ("H",)),
    )
    with pytest.raises(
        Refusal,
        match=r"'\.' names the current element or token; an item test names a cell",
    ):
        compile_predicate(predicate).bind(tone_graph())


def test_relation_image_refuses_invalid_direction_and_relation() -> None:
    """The public image helper validates its direction and relation shape."""
    source = chain_graph()
    nodes = item_nodes(source, q("i"), (0,))
    with pytest.raises(ValueError, match="invalid direction"):
        relation_image(nodes, q("next"), cast(WalkDirection, "sideways"))
    with pytest.raises(ValueError, match="not a declared bipartite or polyadic"):
        relation_image(nodes, q("i-members"), WalkDirection.FORWARD)


def test_related_can_be_combined_inside_an_element_body() -> None:
    """Related composes with Boolean nodes while an array element is current."""
    tier = q("node")
    item_type = q("node-item")
    edge = q("edge")
    key = q("k")
    values = q("values")
    source = Graph(
        (NamespaceDeclaration("ex", NS),),
        (
            Tier(
                TierDeclaration(tier, "Nodes"),
                (
                    Item("n0", (JsonAttributeValue(values, ["x"]),)),
                    Item("n1", (AttributeValue(key, XsdType.INTEGER, "1"),)),
                ),
            ),
        ),
        (
            SimpleRelationDeclaration(q("node-members"), tier, item_type),
            BipartiteRelationDeclaration(edge, item_type, item_type),
        ),
        (RelationInstance(edge, ItemRef(tier, 0), ItemRef(tier, 1)),),
        (
            AttributeDeclaration(key, AttributeDomain.ITEM, XsdType.INTEGER),
            AttributeDeclaration(values, AttributeDomain.ITEM, JsonType.JSON),
        ),
    )
    body = And(
        (
            Related(
                edge,
                WalkDirection.FORWARD,
                Quantifier.ANY,
                Equals(Cell(key), (1,)),
            ),
            Or(
                (
                    Equals(Current(), ("never",)),
                    Not(Equals(Current(), ("bad",))),
                )
            ),
        )
    )
    predicate = Elements(Cell(values), Quantifier.ANY, body)
    assert selected(source, tier, predicate) == ["n0"]
