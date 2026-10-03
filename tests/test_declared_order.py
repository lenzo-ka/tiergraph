"""Declared linearization across tiers, boundaries, streams, and grammar input."""

from __future__ import annotations

import json
from dataclasses import replace
from itertools import pairwise
from typing import cast

import pytest

import tiergraph.match as match_module
from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BoundariesSelector,
    BoundaryPathSelector,
    BoundarySelector,
    BoundarySide,
    DifferenceSelector,
    DurableBoundaryRef,
    DurableItemRef,
    Graph,
    IntersectionSelector,
    Item,
    ItemPathSelector,
    ItemRef,
    ItemSelector,
    ItemsSelector,
    NamespaceDeclaration,
    Node,
    NodeKind,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    QualifiedName,
    Refusal,
    RefusalStage,
    RelationEndpointKind,
    RelationSideDeclaration,
    SequenceSelector,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    TierSelector,
    UnionSelector,
    WhereSelector,
    XsdType,
    evaluate_selection,
)
from tiergraph.build import BuilderError, document
from tiergraph.core import RelationEndpointRef
from tiergraph.grammar import GrammarInput
from tiergraph.match import (
    AtomPattern,
    ContainerOrder,
    DeclaredOrder,
    EndPattern,
    FocusPattern,
    RepeatPattern,
    SeqPattern,
    StartPattern,
    TierOrder,
    _decode_ordering,
    _selector_to_data,
    compile_pattern,
    ordering_to_data,
)
from tiergraph.predicate import (
    And,
    IntervalRelation,
    OffsetProfile,
    Quantifier,
    Spans,
    compile_predicate,
)
from tiergraph.traversal import OrderedPolyadicTraversal

NS = "urn:test"


def q(local: str) -> QualifiedName:
    return QualifiedName(NS, local)


def side(*, maximum: int = 1) -> RelationSideDeclaration:
    return RelationSideDeclaration(
        (RelationEndpointKind.ITEM, RelationEndpointKind.BOUNDARY),
        minimum=1,
        maximum=maximum,
    )


def declaration(*, maximum: int = 1) -> PolyadicRelationDeclaration:
    return PolyadicRelationDeclaration(
        q("next"), side(maximum=maximum), side(maximum=maximum)
    )


def item_chain(
    count: int,
    edges: tuple[tuple[int, int], ...] | None = None,
    *,
    declared: PolyadicRelationDeclaration | None = None,
) -> tuple[Graph, DeclaredOrder]:
    tier = Tier(
        TierDeclaration(q("seg"), "Segments"),
        tuple(Item(f"s{i}") for i in range(count)),
    )
    relation = declaration() if declared is None else declared
    pairs = tuple(pairwise(range(count))) if edges is None else edges
    graph = Graph(
        (NamespaceDeclaration("t", NS),),
        (tier,),
        (SimpleRelationDeclaration(q("members"), q("seg"), q("item")), relation),
        polyadic_relations=tuple(
            PolyadicRelationInstance(
                q("next"),
                (ItemRef(q("seg"), left),),
                (ItemRef(q("seg"), right),),
            )
            for left, right in pairs
        ),
    )
    return graph, DeclaredOrder(q("next"), ItemsSelector(q("seg")))


def mixed_chain() -> tuple[
    Graph,
    DeclaredOrder,
    tuple[ItemRef, DurableBoundaryRef, ItemRef, ItemRef],
]:
    """Return the declared chain a, boundary, tone, b."""
    tiers = (
        Tier(TierDeclaration(q("seg"), "Segments"), (Item("a"), Item("b"))),
        Tier(TierDeclaration(q("tone"), "Tone"), (Item("tone"),)),
    )
    boundary = DurableBoundaryRef(DurableItemRef("b"), BoundarySide.BEFORE)
    nodes: tuple[ItemRef, DurableBoundaryRef, ItemRef, ItemRef] = (
        ItemRef(q("seg"), 0),
        boundary,
        ItemRef(q("tone"), 0),
        ItemRef(q("seg"), 1),
    )
    graph = Graph(
        (NamespaceDeclaration("t", NS),),
        tiers,
        (
            SimpleRelationDeclaration(q("sm"), q("seg"), q("si")),
            SimpleRelationDeclaration(q("tm"), q("tone"), q("ti")),
            declaration(),
        ),
        polyadic_relations=tuple(
            PolyadicRelationInstance(q("next"), (left,), (right,))
            for left, right in pairwise(cast(tuple[RelationEndpointRef, ...], nodes))
        ),
    )
    members = UnionSelector(
        tuple(
            BoundarySelector(node)
            if isinstance(node, DurableBoundaryRef)
            else ItemSelector(node)
            for node in nodes
        )
    )
    return graph, DeclaredOrder(q("next"), members), nodes


def span_mixed_chain() -> tuple[Graph, DeclaredOrder, DurableBoundaryRef]:
    """Return boundary,a with a overlapping one syllable item."""
    values = (
        AttributeValue(q("origin"), XsdType.INTEGER, "0"),
        AttributeValue(q("end"), XsdType.INTEGER, "2"),
    )
    syllable_values = (
        AttributeValue(q("origin"), XsdType.INTEGER, "1"),
        AttributeValue(q("end"), XsdType.INTEGER, "3"),
    )
    tiers = (
        Tier(TierDeclaration(q("seg"), "Segments"), (Item("a", values),)),
        Tier(
            TierDeclaration(q("syl"), "Syllables"),
            (Item("syllable", syllable_values),),
        ),
    )
    boundary = DurableBoundaryRef(DurableItemRef("a"), BoundarySide.BEFORE)
    graph = Graph(
        (NamespaceDeclaration("t", NS),),
        tiers,
        (
            SimpleRelationDeclaration(q("sm"), q("seg"), q("si")),
            SimpleRelationDeclaration(q("ym"), q("syl"), q("yi")),
            declaration(),
        ),
        attribute_declarations=(
            AttributeDeclaration(q("origin"), AttributeDomain.ITEM, XsdType.INTEGER),
            AttributeDeclaration(q("end"), AttributeDomain.ITEM, XsdType.INTEGER),
        ),
        polyadic_relations=(
            PolyadicRelationInstance(q("next"), (boundary,), (ItemRef(q("seg"), 0),)),
        ),
    )
    members = UnionSelector(
        (BoundarySelector(boundary), ItemSelector(ItemRef(q("seg"), 0)))
    )
    return graph, DeclaredOrder(q("next"), members), boundary


def any_node() -> AtomPattern:
    return AtomPattern(And(()))


def _item_label(node: Node) -> str:
    reference = node.reference
    assert isinstance(reference, ItemRef)
    return f"{reference.tier.local_name}{reference.index}"


def _item_index(node: Node) -> int:
    reference = node.reference
    assert isinstance(reference, ItemRef)
    return reference.index


def _node_name(graph: Graph, node: Node) -> str:
    assert isinstance(node.reference, ItemRef)
    tier = next(
        tier for tier in graph.tiers if tier.declaration.name == node.reference.tier
    )
    result = tier.items[node.reference.index].durable_id
    assert result is not None
    return result


def bounds(
    graph: Graph, order: DeclaredOrder, pattern: AtomPattern | SeqPattern
) -> list[tuple[int, int]]:
    return [
        (match.start, match.end)
        for match in compile_pattern(pattern).spans(graph, order).matches
    ]


def test_d1_relation_order_overrides_canonical_cross_tier_order() -> None:
    tiers = (
        Tier(TierDeclaration(q("seg"), "Segments"), (Item("s0"), Item("s1"))),
        Tier(TierDeclaration(q("stress"), "Stress"), (Item("x0"),)),
        Tier(TierDeclaration(q("tone"), "Tone"), (Item("t0"),)),
    )
    nodes: tuple[RelationEndpointRef, ...] = (
        ItemRef(q("seg"), 0),
        DurableBoundaryRef(DurableItemRef("s1"), BoundarySide.BEFORE),
        ItemRef(q("stress"), 0),
        ItemRef(q("tone"), 0),
        ItemRef(q("seg"), 1),
    )
    graph = Graph(
        (NamespaceDeclaration("t", NS),),
        tiers,
        (
            *(
                SimpleRelationDeclaration(q(f"m{i}"), tier.declaration.name, q(f"i{i}"))
                for i, tier in enumerate(tiers)
            ),
            declaration(),
        ),
        polyadic_relations=tuple(
            PolyadicRelationInstance(q("next"), (left,), (right,))
            for left, right in pairwise(nodes)
        ),
    )
    members = UnionSelector(
        tuple(
            BoundarySelector(node)
            if isinstance(node, DurableBoundaryRef)
            else ItemSelector(node)
            for node in nodes
        )
    )
    pattern = SeqPattern((any_node(), any_node(), any_node(), any_node(), any_node()))
    match = (
        compile_pattern(pattern)
        .spans(graph, DeclaredOrder(q("next"), members))
        .matches[0]
    )
    names = [
        "b0" if node.kind is NodeKind.BOUNDARY else _item_label(node)
        for node in match.items
    ]
    assert [(match.start, match.end, names)] == [
        (0, 5, ["seg0", "b0", "stress0", "tone0", "seg1"])
    ]
    assert tuple(evaluate_selection(graph, members).nodes) != match.items


def test_d2_json_encodes_admitted_leaves_and_refuses_recursive_sequence() -> None:
    members = UnionSelector(
        (ItemPathSelector("/items/durable/s0"), BoundariesSelector(q("seg")))
    )
    order = DeclaredOrder(q("next"), members, open_left=True)
    data = ordering_to_data(order)
    assert isinstance(data, dict)
    assert _decode_ordering(data, "$") == order
    assert data["open_left"] is True
    omitted = dict(data)
    omitted.pop("members")
    with pytest.raises(Refusal, match="missing fields"):
        _decode_ordering(omitted, "$")
    recursive = DeclaredOrder(
        q("next"), SequenceSelector(TierOrder(q("seg")), FocusPattern(any_node()))
    )
    with pytest.raises(Refusal, match="may not contain SequenceSelector") as caught:
        ordering_to_data(recursive)
    assert caught.value.stage is RefusalStage.SEMANTICS
    with pytest.raises(Refusal, match="open_left must be a boolean"):
        _decode_ordering({**data, "open_left": 1}, "$")
    with pytest.raises(ValueError, match="open_left must be a boolean"):
        DeclaredOrder(q("next"), members, open_left=1)  # type: ignore[arg-type]


def test_d2_json_encodes_all_new_leaves_and_algebra() -> None:
    members = DifferenceSelector(
        IntersectionSelector(
            (
                WhereSelector(ItemsSelector(q("seg")), And(())),
                ItemPathSelector("/items/durable/s0"),
            )
        ),
        BoundaryPathSelector("/boundaries/durable/s0/before"),
    )
    data = ordering_to_data(DeclaredOrder(q("next"), members))
    assert isinstance(data, dict)
    assert _decode_ordering(data, "$") == DeclaredOrder(q("next"), members)
    sequence = SequenceSelector(TierOrder(q("seg")), FocusPattern(any_node()))
    with pytest.raises(Refusal, match="nested SequenceSelector has no selector JSON"):
        _selector_to_data(sequence)
    with pytest.raises(Refusal, match="nested SequenceSelector has no selector JSON"):
        ordering_to_data(ContainerOrder(q("parts"), sequence))


def test_d3_builder_appends_bridge_and_returns_grown_members() -> None:
    doc = document(NS, prefix="t")
    seg = doc.tier(
        "seg", tuple(f"s{i}" for i in range(5)), item_type="item", membership="m"
    )
    initial = UnionSelector(tuple(ItemSelector(seg.ref(i)) for i in range(3)))
    complete = ItemsSelector(q("seg"))
    order = doc.declared_order("next", initial, tuple(seg.ref(i) for i in range(3)))
    updated = doc.append_declared(order, complete, (seg.ref(3), seg.ref(4)))
    pairs = [
        (instance.sources[0], instance.targets[0])
        for instance in doc.build().polyadic_relations
        if instance.declaration == q("next")
    ]
    assert pairs == [
        (seg.ref(0), seg.ref(1)),
        (seg.ref(1), seg.ref(2)),
        (seg.ref(2), seg.ref(3)),
        (seg.ref(3), seg.ref(4)),
    ]
    assert updated.members == complete
    assert doc.append_declared(updated, complete, ()).members == complete
    with pytest.raises(BuilderError, match="member is already present"):
        doc.append_declared(updated, complete, (seg.ref(4),))


def test_d4_empty_and_singleton_orders_keep_explicit_members() -> None:
    empty, empty_order = item_chain(0)
    assert compile_pattern(any_node()).spans(empty, empty_order).matches == ()
    anchored = SeqPattern((StartPattern(), EndPattern()))
    assert compile_pattern(anchored).exists(empty, empty_order) is True
    one, one_order = item_chain(1)
    assert bounds(one, one_order, any_node()) == [(0, 1)]


@pytest.mark.parametrize(
    ("edges", "message"),
    [
        (
            ((0, 1), (0, 2)),
            r"gives item \{urn:test\}seg\[0\] more than one successor "
            r"\(instances 0 and 1\)",
        ),
        (
            ((0, 2), (1, 2)),
            r"gives item \{urn:test\}seg\[2\] more than one predecessor "
            r"\(instances 0 and 1\)",
        ),
        (
            ((0, 1), (1, 0)),
            r"instance 1 closes a cycle at item \{urn:test\}seg\[0\]",
        ),
        (((0, 1), (2, 3)), r"has 2 heads; exactly one is required"),
    ],
)
def test_d5_d8_degree_cycle_and_single_head_refusals(
    edges: tuple[tuple[int, int], ...], message: str
) -> None:
    graph, order = item_chain(max(max(pair) for pair in edges) + 1, edges)
    with pytest.raises(Refusal, match=message) as caught:
        compile_pattern(any_node()).exists(graph, order)
    assert caught.value.stage is RefusalStage.SEMANTICS


def test_d9_relation_content_outside_members_is_not_filtered() -> None:
    graph, _ = item_chain(4, ((0, 1), (2, 3)))
    members = UnionSelector(
        (
            ItemSelector(ItemRef(q("seg"), 0)),
            ItemSelector(ItemRef(q("seg"), 1)),
        )
    )
    order = DeclaredOrder(q("next"), members)
    with pytest.raises(
        Refusal,
        match=r"instance 1 names item \{urn:test\}seg\[2\] outside",
    ):
        compile_pattern(any_node()).exists(graph, order)


def test_d10_open_right_append_crosses_the_old_chunk_edge() -> None:
    graph, _ = item_chain(2)
    first_graph = replace(graph, polyadic_relations=())
    first = DeclaredOrder(q("next"), ItemSelector(ItemRef(q("seg"), 0)))
    pattern = compile_pattern(SeqPattern((any_node(), any_node())))
    before = pattern.spans(first_graph, first, open_right=True)
    assert before.result.matches == ()
    assert before.pending_from == (0,)
    complete = DeclaredOrder(q("next"), ItemsSelector(q("seg")))
    after = pattern.spans(graph, complete, open_right=True)
    assert [(match.start, match.end) for match in after.result.matches] == [(0, 2)]
    assert after.pending_from == (1,)


def test_d11_boundary_is_one_materialized_position() -> None:
    tier = Tier(TierDeclaration(q("seg"), "Segments"), (Item("left"), Item("right")))
    boundary = DurableBoundaryRef(DurableItemRef("right"), BoundarySide.BEFORE)
    sequence: tuple[RelationEndpointRef, ...] = (
        ItemRef(q("seg"), 0),
        boundary,
        ItemRef(q("seg"), 1),
    )
    graph = Graph(
        (NamespaceDeclaration("t", NS),),
        (tier,),
        (SimpleRelationDeclaration(q("members"), q("seg"), q("item")), declaration()),
        polyadic_relations=tuple(
            PolyadicRelationInstance(q("next"), (left,), (right,))
            for left, right in pairwise(sequence)
        ),
    )
    members = UnionSelector(
        (
            ItemSelector(ItemRef(q("seg"), 0)),
            BoundarySelector(boundary),
            ItemSelector(ItemRef(q("seg"), 1)),
        )
    )
    pattern = SeqPattern((any_node(), any_node(), any_node()))
    match = (
        compile_pattern(pattern)
        .spans(graph, DeclaredOrder(q("next"), members))
        .matches[0]
    )
    names = [
        "boundary"
        if node.kind is NodeKind.BOUNDARY
        else tier.items[_item_index(node)].durable_id
        for node in match.items
    ]
    assert names == ["left", "boundary", "right"]
    assert (match.start, match.end) == (0, 3)


def grammar_graph() -> tuple[Graph, UnionSelector, DeclaredOrder, OffsetProfile]:
    def values(symbol: str) -> tuple[AttributeValue, ...]:
        return (
            AttributeValue(q("symbol"), XsdType.STRING, symbol),
            AttributeValue(q("realization"), XsdType.STRING, symbol),
            AttributeValue(q("origin"), XsdType.INTEGER, "0"),
            AttributeValue(q("end"), XsdType.INTEGER, "1"),
        )

    seg = Tier(
        TierDeclaration(q("seg"), "Segments"),
        (Item("s0", values("s0")), Item("s1", values("s1"))),
    )
    tone = Tier(TierDeclaration(q("tone"), "Tone"), (Item("t0", values("t0")),))
    graph = Graph(
        (NamespaceDeclaration("t", NS),),
        (seg, tone),
        (
            SimpleRelationDeclaration(q("sm"), q("seg"), q("si")),
            SimpleRelationDeclaration(q("tm"), q("tone"), q("ti")),
            declaration(),
        ),
        attribute_declarations=tuple(
            AttributeDeclaration(q(name), AttributeDomain.ITEM, kind)
            for name, kind in (
                ("symbol", XsdType.STRING),
                ("realization", XsdType.STRING),
                ("origin", XsdType.INTEGER),
                ("end", XsdType.INTEGER),
            )
        ),
        polyadic_relations=(
            PolyadicRelationInstance(
                q("next"),
                (ItemRef(q("seg"), 0),),
                (ItemRef(q("tone"), 0),),
            ),
            PolyadicRelationInstance(
                q("next"),
                (ItemRef(q("tone"), 0),),
                (ItemRef(q("seg"), 1),),
            ),
        ),
    )
    selected = UnionSelector((ItemsSelector(q("seg")), ItemsSelector(q("tone"))))
    return (
        graph,
        selected,
        DeclaredOrder(q("next"), selected),
        OffsetProfile(q("origin"), q("end")),
    )


def test_d12_grammar_input_uses_declared_order_and_accepts_equal_origins() -> None:
    graph, selected, order, offsets = grammar_graph()
    declared = GrammarInput.from_graph(
        graph,
        selected,
        q("symbol"),
        q("realization"),
        offsets,
        ordering=order,
    )
    canonical = GrammarInput.from_graph(
        graph, selected, q("symbol"), q("realization"), offsets
    )
    assert [token.symbol for token in declared.tokens] == ["s0", "t0", "s1"]
    assert [token.symbol for token in canonical.tokens] == ["s0", "s1", "t0"]


def test_d13_grammar_input_refuses_member_mismatch_and_boundaries() -> None:
    graph, selected, _, offsets = grammar_graph()
    short_members = UnionSelector(
        (
            ItemSelector(ItemRef(q("seg"), 0)),
            ItemSelector(ItemRef(q("tone"), 0)),
        )
    )
    short_graph = replace(graph, polyadic_relations=graph.polyadic_relations[:1])
    with pytest.raises(
        ValueError, match="ordering members must equal the selected input items"
    ):
        GrammarInput.from_graph(
            short_graph,
            selected,
            q("symbol"),
            q("realization"),
            offsets,
            ordering=DeclaredOrder(q("next"), short_members),
        )
    boundary = DurableBoundaryRef(DurableItemRef("s1"), BoundarySide.BEFORE)
    boundary_members = UnionSelector(
        (
            ItemSelector(ItemRef(q("seg"), 0)),
            BoundarySelector(boundary),
        )
    )
    boundary_graph = replace(
        graph,
        polyadic_relations=(
            PolyadicRelationInstance(
                q("next"),
                (ItemRef(q("seg"), 0),),
                (boundary,),
            ),
        ),
    )
    with pytest.raises(ValueError, match="ordering must contain only items"):
        GrammarInput.from_graph(
            boundary_graph,
            boundary_members,
            q("symbol"),
            q("realization"),
            offsets,
            ordering=DeclaredOrder(q("next"), boundary_members),
        )


def test_d14_two_target_instance_is_not_truncated() -> None:
    graph, order = item_chain(3, (), declared=declaration(maximum=2))
    graph = replace(
        graph,
        polyadic_relations=(
            PolyadicRelationInstance(
                q("next"),
                (ItemRef(q("seg"), 0),),
                (ItemRef(q("seg"), 1), ItemRef(q("seg"), 2)),
            ),
        ),
    )
    with pytest.raises(
        Refusal, match="instance 0 must have exactly one source and one target"
    ):
        compile_pattern(any_node()).exists(graph, order)


def test_d15_open_left_prevents_retirement_from_rebasing_start() -> None:
    patterns = (
        SeqPattern((StartPattern(), any_node())),
        SeqPattern((StartPattern(), RepeatPattern(any_node(), 1, 2))),
    )
    for length in range(1, 5):
        full_graph, full_order = item_chain(length)
        for pattern in patterns:
            full = bounds(full_graph, full_order, pattern)
            for split in range(1, length + 1):
                suffix_graph, suffix_order = item_chain(length - split)
                open_suffix = replace(suffix_order, open_left=True)
                shifted = [
                    (start + split, end + split)
                    for start, end in bounds(suffix_graph, open_suffix, pattern)
                ]
                assert shifted == [
                    (start, end) for start, end in full if start >= split
                ]
                if length > split:
                    rebased = replace(open_suffix, open_left=False)
                    assert bounds(suffix_graph, rebased, pattern) != bounds(
                        suffix_graph, open_suffix, pattern
                    )


def test_declared_order_refuses_wrong_declaration_and_member_kind() -> None:
    graph, order = item_chain(1)
    with pytest.raises(Refusal, match="is not a declared polyadic relation"):
        compile_pattern(any_node()).exists(
            graph, replace(order, successor=q("missing"))
        )
    with pytest.raises(Refusal, match="members must select only items and boundaries"):
        compile_pattern(any_node()).exists(
            graph, replace(order, members=TierSelector(q("seg")))
        )


def test_declared_order_normalizes_traversal_resolution_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, order = item_chain(2)

    def malformed_instances(
        self: OrderedPolyadicTraversal, *, check_cycle: bool
    ) -> object:
        del self, check_cycle
        raise ValueError("malformed declared traversal")

    monkeypatch.setattr(OrderedPolyadicTraversal, "_instances", malformed_instances)
    with pytest.raises(Refusal, match="malformed declared traversal") as caught:
        compile_pattern(any_node()).exists(graph, order)
    assert caught.value.stage is RefusalStage.SEMANTICS


def test_declared_order_accepts_reverse_canonical_chain() -> None:
    graph, order = item_chain(2, ((1, 0),))
    match = compile_pattern(SeqPattern((any_node(), any_node()))).spans(graph, order)
    assert [
        tuple(_item_index(node) for node in span.items) for span in match.matches
    ] == [(1, 0)]


@pytest.mark.parametrize(
    "members",
    [
        WhereSelector(
            SequenceSelector(TierOrder(q("seg")), FocusPattern(any_node())), And(())
        ),
        DifferenceSelector(
            ItemsSelector(q("seg")),
            SequenceSelector(TierOrder(q("seg")), FocusPattern(any_node())),
        ),
    ],
)
def test_declared_order_refuses_nested_sequence_members(members: object) -> None:
    graph, order = item_chain(1)
    with pytest.raises(Refusal, match="may not contain SequenceSelector"):
        compile_pattern(any_node()).exists(
            graph,
            replace(order, members=members),  # type: ignore[arg-type]
        )


def test_declared_json_defaults_closed_left_and_rejects_unknown_fields() -> None:
    data = ordering_to_data(DeclaredOrder(q("next"), ItemsSelector(q("seg"))))
    assert isinstance(data, dict)
    assert "open_left" not in data
    decoded = _decode_ordering(data, "$")
    assert isinstance(decoded, DeclaredOrder)
    assert decoded.open_left is False
    with pytest.raises(Refusal, match="unknown fields"):
        _decode_ordering({**data, "extra": True}, "$")


def test_builder_refuses_unordered_bad_and_inexact_sequences() -> None:
    doc = document(NS, prefix="t")
    seg = doc.tier("seg", ("a", "b"), item_type="item", membership="m")
    with pytest.raises(BuilderError, match="ordered iterable"):
        doc.declared_order(
            "set-order", ItemsSelector(q("seg")), {seg.ref(0), seg.ref(1)}
        )
    with pytest.raises(BuilderError, match="endpoints must be item or boundary"):
        doc.declared_order(
            "bad-order",
            ItemsSelector(q("seg")),
            (object(),),  # type: ignore[arg-type]
        )
    with pytest.raises(BuilderError, match="members must select exactly"):
        doc.declared_order("short-order", ItemsSelector(q("seg")), (seg.ref(0),))
    with pytest.raises(BuilderError, match="expected an iterable"):
        doc.declared_order(
            "non-iterable",
            ItemsSelector(q("seg")),
            1,  # type: ignore[arg-type]
        )


def test_builder_refuses_duplicate_names_wrong_types_and_missing_append_refs() -> None:
    doc = document(NS, prefix="t")
    seg = doc.tier("seg", ("a", "b"), item_type="item", membership="m")
    first = ItemSelector(seg.ref(0))
    order = doc.declared_order("next", first, (seg.ref(0),))
    with pytest.raises(BuilderError, match="name is already used"):
        doc.declared_order("next", first, (seg.ref(0),))
    with pytest.raises(BuilderError, match="order must be a DeclaredOrder"):
        doc.append_declared(object(), first, ())  # type: ignore[arg-type]
    complete = ItemsSelector(q("seg"))
    with pytest.raises(BuilderError, match="unknown durable item id 'missing'"):
        doc.append_declared(order, complete, (DurableItemRef("missing"),))


def test_builder_declared_order_resolves_boundary_endpoints() -> None:
    doc = document(NS, prefix="t")
    seg = doc.tier("seg", ("left", "right"), item_type="item", membership="m")
    boundary = seg.before(1)
    members = UnionSelector((ItemSelector(seg.ref(0)), BoundarySelector(boundary)))
    order = doc.declared_order("next", members, (seg.ref(0), boundary))
    result = compile_pattern(SeqPattern((any_node(), any_node()))).spans(
        doc.build(), order
    )
    assert [tuple(node.kind for node in span.items) for span in result.matches] == [
        (NodeKind.ITEM, NodeKind.BOUNDARY)
    ]


def test_builder_defensive_scope_mismatch_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc = document(NS, prefix="t")
    seg = doc.tier("seg", ("a", "b"), item_type="item", membership="m")
    original = match_module._declared_scope

    def reversed_scope(graph: Graph, order: DeclaredOrder) -> object:
        return replace(
            original(graph, order), nodes=tuple(reversed(original(graph, order).nodes))
        )

    monkeypatch.setattr(match_module, "_declared_scope", reversed_scope)
    with pytest.raises(BuilderError, match="members must select exactly"):
        doc.declared_order("next", ItemsSelector(q("seg")), (seg.ref(0), seg.ref(1)))

    monkeypatch.setattr(match_module, "_declared_scope", original)
    order = doc.declared_order("kept", ItemSelector(seg.ref(0)), (seg.ref(0),))
    monkeypatch.setattr(match_module, "_declared_scope", reversed_scope)
    with pytest.raises(BuilderError, match="members must select exactly"):
        doc.append_declared(order, ItemsSelector(q("seg")), (seg.ref(1),))


def test_sequence_selector_decoding_is_refused_before_graph_evaluation() -> None:
    recursive = {
        "order": "declared",
        "successor": q("next").to_data(),
        "members": {
            "select": "sequence",
            "ordering": {"order": "tier", "tier": q("seg").to_data()},
            "pattern": {
                "pattern": "focus",
                "body": {
                    "pattern": "atom",
                    "predicate": {"test": "and", "args": []},
                },
            },
        },
    }
    with pytest.raises(Refusal, match="may not contain SequenceSelector") as caught:
        _decode_ordering(json.loads(json.dumps(recursive)), "$")
    assert caught.value.stage is RefusalStage.SEMANTICS


def test_append_declared_requires_owned_order_and_exact_updated_members() -> None:
    doc = document(NS, prefix="t")
    seg = doc.tier("seg", ("a", "b"), item_type="item", membership="m")
    first = ItemSelector(seg.ref(0))
    order = doc.declared_order("next", first, (seg.ref(0),))
    foreign = DeclaredOrder(q("foreign"), first)
    with pytest.raises(BuilderError, match="not owned"):
        doc.append_declared(foreign, first, ())
    with pytest.raises(BuilderError, match="must select exactly"):
        doc.append_declared(order, first, (seg.ref(1),))


def test_s5d_projection_preserves_declared_order_across_hidden_boundary() -> None:
    graph, order, nodes = mixed_chain()
    members = UnionSelector(
        (ItemSelector(nodes[0]), ItemSelector(nodes[2]), ItemSelector(nodes[3]))
    )
    projected = order.project(members)
    match = compile_pattern(SeqPattern((any_node(), any_node(), any_node()))).spans(
        graph, projected
    )
    assert [
        tuple(_node_name(graph, node) for node in span.items) for span in match.matches
    ] == [("a", "tone", "b")]


def test_s5d_projection_validates_hidden_chain_before_filtering() -> None:
    graph, order = item_chain(3, ((0, 1), (0, 2)))
    projected = order.project(ItemSelector(ItemRef(q("seg"), 0)))
    with pytest.raises(Refusal, match="more than one successor"):
        compile_pattern(any_node()).spans(graph, projected)


def test_s5d_projected_anchor_openness_is_derived_from_complete_chain() -> None:
    graph, order = item_chain(2)
    first = ItemSelector(ItemRef(q("seg"), 0))
    second = ItemSelector(ItemRef(q("seg"), 1))
    start = SeqPattern((StartPattern(), any_node()))
    end = SeqPattern((any_node(), EndPattern()))
    assert bounds(graph, order.project(second), start) == []
    assert bounds(graph, order.project(first), end) == []
    retained = SeqPattern((StartPattern(), any_node(), any_node(), EndPattern()))
    assert bounds(graph, order.project(order.members), retained) == [(0, 2)]


def test_s5d_append_full_chain_then_reproject_preserves_watermark() -> None:
    doc = document(NS, prefix="t")
    seg = doc.tier("seg", ("a", "x", "b"), item_type="item", membership="m")
    initial = UnionSelector((ItemSelector(seg.ref(0)), ItemSelector(seg.ref(1))))
    full = doc.declared_order("next", initial, (seg.ref(0), seg.ref(1)))
    only_a = ItemSelector(seg.ref(0))
    pattern = compile_pattern(SeqPattern((any_node(), any_node())))
    before = pattern.spans(doc.build(), full.project(only_a), open_right=True)
    assert before.pending_from == (0,)
    assert before.result.matches == ()
    complete = ItemsSelector(q("seg"))
    appended = doc.append_declared(full, complete, (seg.ref(2),))
    a_and_b = UnionSelector((ItemSelector(seg.ref(0)), ItemSelector(seg.ref(2))))
    after = pattern.spans(doc.build(), appended.project(a_and_b))
    assert [
        tuple(_node_name(doc.build(), node) for node in span.items)
        for span in after.matches
    ] == [("a", "b")]


def test_s5d_append_declared_refuses_projected_order_exactly() -> None:
    doc = document(NS, prefix="t")
    seg = doc.tier("seg", ("a", "b"), item_type="item", membership="m")
    first = ItemSelector(seg.ref(0))
    order = doc.declared_order("next", first, (seg.ref(0),))
    with pytest.raises(BuilderError) as caught:
        doc.append_declared(
            order.project(first), ItemsSelector(q("seg")), (seg.ref(1),)
        )
    assert str(caught.value) == "append declared: project the result after appending"


def test_s5d_old_declared_order_json_is_byte_identical() -> None:
    old = (
        '{"order":"declared","successor":{"namespace":"urn:test",'
        '"local_name":"next"},"members":{"select":"items","tier":{'
        '"namespace":"urn:test","local_name":"seg"}}}'
    )
    decoded = _decode_ordering(json.loads(old), "$")
    encoded = json.dumps(ordering_to_data(decoded), separators=(",", ":"))
    assert encoded == old
    assert '"chain"' not in encoded


def test_s5d_nested_projection_retains_original_complete_selector() -> None:
    _, order = item_chain(3)
    middle = ItemPathSelector("/items/durable/s1")
    tail = ItemPathSelector("/items/durable/s2")
    nested = order.project(UnionSelector((middle, tail))).project(tail)
    assert nested.chain == order.members
    data = ordering_to_data(nested)
    assert isinstance(data, dict)
    assert _decode_ordering(data, "$") == nested


def test_s5d_empty_and_singleton_projections_derive_both_edges() -> None:
    empty, empty_order = item_chain(0)
    anchored = SeqPattern((StartPattern(), EndPattern()))
    assert (
        compile_pattern(anchored).exists(
            empty, empty_order.project(empty_order.members)
        )
        is True
    )
    one, one_order = item_chain(1)
    no_members = DifferenceSelector(one_order.members, one_order.members)
    projected_empty = one_order.project(no_members)
    assert compile_pattern(anchored).exists(one, projected_empty) is False
    closed = SeqPattern((StartPattern(), any_node(), EndPattern()))
    assert bounds(one, one_order.project(one_order.members), closed) == [(0, 1)]


def test_s5d_projection_refuses_members_outside_complete_chain() -> None:
    graph, order = item_chain(2)
    projected = DeclaredOrder(
        order.successor,
        ItemSelector(ItemRef(q("seg"), 1)),
        chain=ItemSelector(ItemRef(q("seg"), 0)),
    )
    with pytest.raises(Refusal, match="members must be a subset of chain"):
        compile_pattern(any_node()).exists(graph, projected)


def test_s5d_projection_refuses_edges_outside_complete_chain() -> None:
    graph, order = item_chain(3)
    projected = DeclaredOrder(
        order.successor,
        ItemSelector(ItemRef(q("seg"), 0)),
        chain=UnionSelector(
            (
                ItemSelector(ItemRef(q("seg"), 0)),
                ItemSelector(ItemRef(q("seg"), 1)),
            )
        ),
    )
    with pytest.raises(Refusal, match="outside its chain selection"):
        compile_pattern(any_node()).exists(graph, projected)


@pytest.mark.parametrize(
    ("edges", "message"),
    [
        (((0, 1), (0, 2)), "more than one successor"),
        (((0, 2), (1, 2)), "more than one predecessor"),
        (((0, 1), (1, 0)), "closes a cycle"),
        (((0, 1), (2, 3)), "has 2 heads"),
    ],
)
def test_s5d_projection_refuses_hidden_degree_cycle_and_disconnect(
    edges: tuple[tuple[int, int], ...], message: str
) -> None:
    graph, order = item_chain(max(max(pair) for pair in edges) + 1, edges)
    projected = order.project(ItemSelector(ItemRef(q("seg"), 0)))
    with pytest.raises(Refusal, match=message):
        compile_pattern(any_node()).exists(graph, projected)


def test_s5d_reverse_chain_projection_keeps_declared_direction() -> None:
    graph, order = item_chain(3, ((2, 1), (1, 0)))
    edges = UnionSelector(
        (
            ItemSelector(ItemRef(q("seg"), 2)),
            ItemSelector(ItemRef(q("seg"), 0)),
        )
    )
    pattern = SeqPattern((StartPattern(), any_node(), any_node(), EndPattern()))
    matches = compile_pattern(pattern).spans(graph, order.project(edges)).matches
    assert [tuple(_item_index(node) for node in match.items) for match in matches] == [
        (2, 0)
    ]


def test_s5d_projection_accepts_item_boundary_mixtures() -> None:
    graph, order, nodes = mixed_chain()
    visible = UnionSelector((ItemSelector(nodes[0]), BoundarySelector(nodes[1])))
    result = compile_pattern(SeqPattern((any_node(), any_node()))).spans(
        graph, order.project(visible)
    )
    assert [tuple(node.kind for node in match.items) for match in result.matches] == [
        (NodeKind.ITEM, NodeKind.BOUNDARY)
    ]


def test_s5d_projection_refuses_nested_sequence_in_complete_chain() -> None:
    graph, order = item_chain(1)
    recursive = WhereSelector(
        SequenceSelector(TierOrder(q("seg")), FocusPattern(any_node())), And(())
    )
    projected = DeclaredOrder(order.successor, order.members, chain=recursive)
    with pytest.raises(Refusal, match="chain may not contain SequenceSelector"):
        compile_pattern(any_node()).exists(graph, projected)
    with pytest.raises(Refusal, match="chain may not contain SequenceSelector"):
        ordering_to_data(projected)
    recursive_members = DeclaredOrder(order.successor, recursive, chain=order.members)
    with pytest.raises(Refusal, match="members may not contain SequenceSelector"):
        compile_pattern(any_node()).exists(graph, recursive_members)
    wrong_kind = DeclaredOrder(
        order.successor, TierSelector(q("seg")), chain=order.members
    )
    with pytest.raises(Refusal, match="members must select only items and boundaries"):
        compile_pattern(any_node()).exists(graph, wrong_kind)


def test_s5d_spans_is_false_on_non_item_candidates_in_mixed_scope() -> None:
    graph, order, boundary = span_mixed_chain()
    profile = OffsetProfile(q("origin"), end=q("end"))
    predicate = Spans(
        profile,
        IntervalRelation.OVERLAPS,
        Quantifier.ANY,
        q("syl"),
        And(()),
    )
    matches = compile_pattern(AtomPattern(predicate)).spans(graph, order).matches
    assert [
        tuple(_node_name(graph, node) for node in match.items) for match in matches
    ] == [("a",)]
    boundary_node = evaluate_selection(graph, BoundarySelector(boundary)).nodes[0]
    for quantifier in Quantifier:
        total = replace(predicate, quantifier=quantifier)
        assert compile_predicate(total).bind(graph).holds(boundary_node) is False
