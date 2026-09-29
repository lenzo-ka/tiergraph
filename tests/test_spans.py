"""Half-open interval predicates and declared-order pair joins."""

from __future__ import annotations

import json
from collections.abc import Iterable

import pytest

from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    Graph,
    Item,
    ItemRef,
    ItemsSelector,
    JsonAttributeValue,
    JsonType,
    NamespaceDeclaration,
    Node,
    NodeKind,
    QualifiedName,
    Refusal,
    RefusalStage,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    TierSelector,
    XsdType,
    evaluate_selection,
)
from tiergraph.match import Extent, _pairs_request_loads, span_pairs
from tiergraph.predicate import (
    And,
    Cell,
    Compare,
    IntervalRelation,
    Matches,
    OffsetProfile,
    Order,
    Quantifier,
    Spans,
    _interval_holds,
    _interval_pairs,
    _OffsetSpan,
    compile_predicate,
    predicate_loads,
    predicate_to_data,
)

NS = "urn:example:fixture#"


def q(local: str) -> QualifiedName:
    """Return one name in the neutral fixture namespace."""
    return QualifiedName(NS, local)


def scalar(local: str, value_type: XsdType, lexical: str) -> AttributeValue:
    """Build one scalar item attribute in the fixture namespace."""
    return AttributeValue(q(local), value_type, lexical)


def interval_item(
    label: str,
    start: int | None,
    end: int,
    *,
    extent_form: bool = False,
    values: tuple[AttributeValue | JsonAttributeValue, ...] = (),
    partition: str = "r0",
) -> Item:
    """Build one item with the OFFS profile and optional test values."""
    offsets: list[AttributeValue | JsonAttributeValue] = []
    if start is not None:
        offsets.append(scalar("start", XsdType.INTEGER, str(start)))
    offsets.append(
        scalar(
            "extent" if extent_form else "end",
            XsdType.INTEGER,
            str(end - (start or 0) if extent_form else end),
        )
    )
    offsets.append(scalar("record", XsdType.STRING, partition))
    return Item(label, tuple((*offsets, *values)))


def offset_graph(
    *,
    extent_form: bool = False,
    extra_gold: bool = False,
    missing_start: bool = False,
    inverted: bool = False,
    negative_extent: bool = False,
) -> Graph:
    """Build OFFS, OFFS-V, or one named O5 mutation fixture."""
    gold = [
        interval_item(
            "g0",
            0,
            4,
            extent_form=extent_form,
            values=(scalar("class", XsdType.STRING, "DATE"),),
        ),
        interval_item(
            "g1",
            5,
            9,
            extent_form=extent_form,
            values=(scalar("class", XsdType.STRING, "DATE"),),
        ),
        interval_item(
            "g2",
            10,
            14,
            extent_form=extent_form,
            values=(scalar("class", XsdType.STRING, "MEASURE"),),
        ),
    ]
    if extra_gold:
        gold.append(
            interval_item(
                "g3",
                0,
                9,
                extent_form=extent_form,
                values=(scalar("class", XsdType.STRING, "DATE"),),
            )
        )
    d1_start = None if missing_start else 5
    d1_end = 3 if inverted else 7
    det = (
        interval_item(
            "d0",
            0,
            4,
            extent_form=extent_form,
            values=(scalar("type", XsdType.STRING, "date:ymd"),),
        ),
        interval_item(
            "d1",
            d1_start,
            d1_end,
            extent_form=extent_form,
            values=(scalar("type", XsdType.STRING, "number:int"),),
        ),
        interval_item(
            "d2",
            10,
            14,
            extent_form=extent_form,
            values=(scalar("type", XsdType.STRING, "number:decimal"),),
        ),
        interval_item(
            "d3",
            0,
            9,
            extent_form=extent_form,
            values=(
                scalar("type", XsdType.STRING, "date-interval"),
                JsonAttributeValue(q("value"), {"start": {"y": 950}}),
            ),
        ),
    )
    if negative_extent:
        values = tuple(
            value for value in det[1].attributes if value.name != q("extent")
        )
        det = (
            det[0],
            Item("d1", (*values, scalar("extent", XsdType.INTEGER, "-1"))),
            *det[2:],
        )
    measure = "extent" if extent_form else "end"
    return Graph(
        (NamespaceDeclaration("ex", NS),),
        (
            Tier(TierDeclaration(q("gold"), "Gold"), tuple(gold)),
            Tier(TierDeclaration(q("det"), "Detected"), det),
        ),
        (
            SimpleRelationDeclaration(q("gold-members"), q("gold"), q("gold-item")),
            SimpleRelationDeclaration(q("det-members"), q("det"), q("det-item")),
        ),
        attribute_declarations=(
            AttributeDeclaration(q("start"), AttributeDomain.ITEM, XsdType.INTEGER),
            AttributeDeclaration(q(measure), AttributeDomain.ITEM, XsdType.INTEGER),
            AttributeDeclaration(q("record"), AttributeDomain.ITEM, XsdType.STRING),
            AttributeDeclaration(q("class"), AttributeDomain.ITEM, XsdType.STRING),
            AttributeDeclaration(q("type"), AttributeDomain.ITEM, XsdType.STRING),
            AttributeDeclaration(q("value"), AttributeDomain.ITEM, JsonType.JSON),
        ),
    )


def offsets(*, extent_form: bool = False) -> OffsetProfile:
    """Return the OFFS end or extent profile."""
    return OffsetProfile(
        q("start"),
        extent=q("extent") if extent_form else None,
        end=None if extent_form else q("end"),
        partition=q("record"),
    )


def labels(source: Graph, nodes: Iterable[Node]) -> list[str]:
    """Return durable item labels without changing carried order."""
    result = []
    for node in nodes:
        assert isinstance(node.reference, ItemRef)
        tier = next(
            tier
            for tier in source.tiers
            if tier.declaration.name == node.reference.tier
        )
        label = tier.items[node.reference.index].durable_id
        assert label is not None
        result.append(label)
    return result


def pair_labels(
    source: Graph, pairs: tuple[tuple[Node, Node], ...]
) -> list[tuple[str, str]]:
    """Return durable labels for a pair witness list."""
    return [
        (labels(source, (left,))[0], labels(source, (right,))[0])
        for left, right in pairs
    ]


def selected(source: Graph, tier: str, predicate: Spans | And) -> list[str]:
    """Bind a predicate and select one fixture tier."""
    candidates = evaluate_selection(source, ItemsSelector(q(tier)))
    return labels(
        source, compile_predicate(predicate).bind(source).select(candidates).nodes
    )


def o1_answers(source: Graph, profile: OffsetProfile) -> tuple[object, ...]:
    """Evaluate the four O1 calls on one offset encoding."""
    first = And(
        (
            Matches(Cell(q("class")), "DATE"),
            Spans(
                profile,
                IntervalRelation.EQUAL,
                Quantifier.NONE,
                q("det"),
                Matches(Cell(q("type")), "date.*"),
            ),
        )
    )
    fifth = And(
        (
            Matches(Cell(q("type")), "date.*"),
            Compare(Cell(q("value"), ("start", "y")), Order.LT, 1000),
            Spans(
                profile,
                IntervalRelation.EQUAL,
                Quantifier.ANY,
                q("gold"),
                And(()),
            ),
        )
    )
    equal = span_pairs(
        source,
        ItemsSelector(q("det")),
        ItemsSelector(q("gold")),
        IntervalRelation.EQUAL,
        profile,
    )
    within = span_pairs(
        source,
        ItemsSelector(q("det")),
        ItemsSelector(q("det")),
        IntervalRelation.PROPER_WITHIN,
        profile,
    )
    return (
        selected(source, "gold", first),
        pair_labels(source, equal.pairs),
        pair_labels(source, within.pairs),
        selected(source, "det", fifth),
    )


def test_o1_spans_and_pairs_answers() -> None:
    assert o1_answers(offset_graph(), offsets()) == (
        ["g1"],
        [("d0", "g0"), ("d2", "g2")],
        [("d0", "d3"), ("d1", "d3")],
        [],
    )
    assert o1_answers(offset_graph(extra_gold=True), offsets())[-1] == ["d3"]


def test_o2_all_literal_interval_rows() -> None:
    rows = (
        ((0, 4), (4, 8), (IntervalRelation.MEETS,)),
        (
            (4, 4),
            (0, 4),
            (
                IntervalRelation.WITHIN,
                IntervalRelation.PROPER_WITHIN,
                IntervalRelation.MET_BY,
            ),
        ),
        (
            (4, 4),
            (4, 8),
            (
                IntervalRelation.WITHIN,
                IntervalRelation.PROPER_WITHIN,
                IntervalRelation.MEETS,
            ),
        ),
        (
            (0, 8),
            (4, 4),
            (IntervalRelation.CONTAINS, IntervalRelation.PROPER_CONTAINS),
        ),
        (
            (4, 4),
            (4, 4),
            (
                IntervalRelation.EQUAL,
                IntervalRelation.CONTAINS,
                IntervalRelation.WITHIN,
                IntervalRelation.MEETS,
                IntervalRelation.MET_BY,
            ),
        ),
        (
            (0, 4),
            (0, 4),
            (
                IntervalRelation.EQUAL,
                IntervalRelation.CONTAINS,
                IntervalRelation.WITHIN,
                IntervalRelation.OVERLAPS,
            ),
        ),
        ((2, 6), (0, 4), (IntervalRelation.OVERLAPS,)),
    )
    for index, (left_bounds, right_bounds, expected) in enumerate(rows):
        left = _OffsetSpan(
            Node(NodeKind.ITEM, ItemRef(q("left"), index)), *left_bounds, None
        )
        right = _OffsetSpan(
            Node(NodeKind.ITEM, ItemRef(q("right"), index)), *right_bounds, None
        )
        observed = tuple(
            relation
            for relation in IntervalRelation
            if tuple(_interval_pairs((left,), (right,), relation))
        )
        assert observed == expected
    same = _OffsetSpan(Node(NodeKind.ITEM, ItemRef(q("left"), 9)), 0, 1, None)
    other_partition = _OffsetSpan(
        Node(NodeKind.ITEM, ItemRef(q("right"), 9)), 0, 1, "other"
    )
    assert not _interval_holds(IntervalRelation.WITHIN, same, other_partition)
    assert _interval_holds(IntervalRelation.EQUAL, same, same)
    assert not _interval_holds(IntervalRelation.MEETS, same, same)
    assert not _interval_holds(IntervalRelation.MET_BY, same, same)


def test_o3_partitions_and_self_pairs_are_excluded() -> None:
    source = Graph(
        (NamespaceDeclaration("ex", NS),),
        (
            Tier(
                TierDeclaration(q("p"), "Partitioned"),
                (
                    interval_item("p0", 0, 4, partition="r1"),
                    interval_item("p1", 0, 4, partition="r2"),
                ),
            ),
        ),
        (SimpleRelationDeclaration(q("p-members"), q("p"), q("p-item")),),
        attribute_declarations=(
            AttributeDeclaration(q("start"), AttributeDomain.ITEM, XsdType.INTEGER),
            AttributeDeclaration(q("end"), AttributeDomain.ITEM, XsdType.INTEGER),
            AttributeDeclaration(q("record"), AttributeDomain.ITEM, XsdType.STRING),
        ),
    )
    result = span_pairs(
        source,
        ItemsSelector(q("p")),
        ItemsSelector(q("p")),
        IntervalRelation.EQUAL,
        offsets(),
    )
    assert pair_labels(source, result.pairs) == []


def test_o4_pairs_retain_left_major_declared_order() -> None:
    source = Graph(
        (NamespaceDeclaration("ex", NS),),
        (
            Tier(
                TierDeclaration(q("e"), "Events"),
                (
                    interval_item("e0", 5, 7),
                    interval_item("e1", 0, 4),
                    interval_item("e2", 0, 9),
                ),
            ),
        ),
        (SimpleRelationDeclaration(q("e-members"), q("e"), q("e-item")),),
        attribute_declarations=(
            AttributeDeclaration(q("start"), AttributeDomain.ITEM, XsdType.INTEGER),
            AttributeDeclaration(q("end"), AttributeDomain.ITEM, XsdType.INTEGER),
            AttributeDeclaration(q("record"), AttributeDomain.ITEM, XsdType.STRING),
        ),
    )
    result = span_pairs(
        source,
        ItemsSelector(q("e")),
        ItemsSelector(q("e")),
        IntervalRelation.PROPER_WITHIN,
        offsets(),
    )
    assert pair_labels(source, result.pairs) == [("e0", "e2"), ("e1", "e2")]


@pytest.mark.parametrize(
    ("source", "profile", "message"),
    (
        (
            offset_graph(missing_start=True),
            offsets(),
            "item 'd1' has no ex:start; Spans and span_pairs need offsets on every item they read",
        ),
        (
            offset_graph(inverted=True),
            offsets(),
            "item 'd1' has end 3 before origin 5",
        ),
        (
            offset_graph(extent_form=True, negative_extent=True),
            offsets(extent_form=True),
            "item 'd1' has negative extent -1",
        ),
    ),
)
def test_o5_offset_refusals(
    source: Graph, profile: OffsetProfile, message: str
) -> None:
    with pytest.raises(Refusal) as caught:
        span_pairs(
            source,
            ItemsSelector(q("det")),
            ItemsSelector(q("det")),
            IntervalRelation.EQUAL,
            profile,
        )
    assert caught.value.stage is RefusalStage.SEMANTICS
    assert str(caught.value) == message


def test_o5_offset_profile_requires_one_measure() -> None:
    with pytest.raises(
        ValueError, match="^OffsetProfile needs exactly one of extent or end$"
    ):
        OffsetProfile(q("start"))
    with pytest.raises(
        ValueError, match="^OffsetProfile needs exactly one of extent or end$"
    ):
        OffsetProfile(q("start"), extent=q("extent"), end=q("end"))


def test_o6_limit_reports_cut_only_when_a_pair_is_omitted() -> None:
    source = offset_graph()
    arguments = (
        source,
        ItemsSelector(q("det")),
        ItemsSelector(q("det")),
        IntervalRelation.PROPER_WITHIN,
        offsets(),
    )
    cut = span_pairs(*arguments, limit=1)
    assert (pair_labels(source, cut.pairs), cut.extent) == (
        [("d0", "d3")],
        Extent.CUT_AT_BOUND,
    )
    complete = span_pairs(*arguments)
    assert (pair_labels(source, complete.pairs), complete.extent) == (
        [("d0", "d3"), ("d1", "d3")],
        Extent.EXHAUSTIVE,
    )
    assert span_pairs(*arguments, limit=2).extent is Extent.EXHAUSTIVE


def test_o7_extent_and_end_forms_have_identical_answers() -> None:
    assert o1_answers(offset_graph(), offsets()) == o1_answers(
        offset_graph(extent_form=True), offsets(extent_form=True)
    )


def test_o8_spans_and_pairs_request_json_round_trip() -> None:
    predicate = Spans(
        offsets(),
        IntervalRelation.PROPER_WITHIN,
        Quantifier.ALL,
        q("gold"),
        And(()),
    )
    assert predicate_loads(json.dumps(predicate_to_data(predicate))) == predicate
    data = {
        "match": "pairs",
        "left": {"select": "items", "tier": q("det").to_data()},
        "right": {"select": "items", "tier": q("gold").to_data()},
        "relation": "equal",
        "offsets": {
            "origin": q("start").to_data(),
            "end": q("end").to_data(),
            "partition": q("record").to_data(),
        },
        "limit": 3,
    }
    request = _pairs_request_loads(json.dumps(data))
    assert request.to_data() == data
    bad = {**data, "relation": "same"}
    with pytest.raises(Refusal) as caught:
        _pairs_request_loads(json.dumps(bad))
    assert caught.value.stage is RefusalStage.VALUE
    assert str(caught.value) == "$.relation has invalid interval relation 'same'"
    predicate_data = predicate_to_data(predicate)
    assert isinstance(predicate_data, dict)
    bad_predicate = {**predicate_data, "relation": "same"}
    with pytest.raises(Refusal, match="invalid interval relation"):
        predicate_loads(json.dumps(bad_predicate))
    bad_predicate = {**predicate_data, "quantifier": "several"}
    with pytest.raises(Refusal, match="invalid quantifier"):
        predicate_loads(json.dumps(bad_predicate))
    extent_predicate = Spans(
        OffsetProfile(q("start"), extent=q("extent")),
        IntervalRelation.EQUAL,
        Quantifier.ANY,
        q("gold"),
        And(()),
    )
    extent_data = predicate_to_data(extent_predicate)
    assert predicate_loads(json.dumps(extent_data)) == extent_predicate
    assert isinstance(extent_data, dict)
    extent_offsets = extent_data["offsets"]
    assert isinstance(extent_offsets, dict)
    assert "partition" not in extent_offsets
    no_limit = {key: value for key, value in data.items() if key != "limit"}
    assert _pairs_request_loads(json.dumps(no_limit)).to_data() == no_limit


def test_interval_surface_validation_and_json_output() -> None:
    source = offset_graph()
    result = span_pairs(
        source,
        ItemsSelector(q("det")),
        ItemsSelector(q("gold")),
        IntervalRelation.EQUAL,
        offsets(),
        limit=1,
    )
    assert result.to_data()["extent"] == "cut-at-bound"
    with pytest.raises(ValueError, match="span_pairs limit"):
        span_pairs(
            source,
            ItemsSelector(q("det")),
            ItemsSelector(q("gold")),
            IntervalRelation.EQUAL,
            offsets(),
            limit=-1,
        )
    with pytest.raises(Refusal, match="offset profile names undeclared"):
        span_pairs(
            source,
            ItemsSelector(q("det")),
            ItemsSelector(q("gold")),
            IntervalRelation.EQUAL,
            OffsetProfile(q("missing"), end=q("end")),
        )
    with pytest.raises(Refusal, match="must be an xsd:integer item attribute"):
        span_pairs(
            source,
            ItemsSelector(q("det")),
            ItemsSelector(q("gold")),
            IntervalRelation.EQUAL,
            OffsetProfile(q("record"), end=q("end")),
        )
    with pytest.raises(Refusal, match="offset profile names undeclared"):
        span_pairs(
            source,
            ItemsSelector(q("det")),
            ItemsSelector(q("gold")),
            IntervalRelation.EQUAL,
            OffsetProfile(q("start"), end=q("end"), partition=q("missing")),
        )
    with pytest.raises(Refusal, match="must be a scalar item attribute"):
        span_pairs(
            source,
            ItemsSelector(q("det")),
            ItemsSelector(q("gold")),
            IntervalRelation.EQUAL,
            OffsetProfile(q("start"), end=q("end"), partition=q("value")),
        )
    with pytest.raises(Refusal, match="read offsets only from items"):
        span_pairs(
            source,
            TierSelector(q("det")),
            ItemsSelector(q("gold")),
            IntervalRelation.EQUAL,
            offsets(),
        )
    without_partition = OffsetProfile(q("start"), end=q("end"))
    assert span_pairs(
        source,
        ItemsSelector(q("det")),
        ItemsSelector(q("gold")),
        IntervalRelation.EQUAL,
        without_partition,
    ).pairs


def test_spans_all_uses_vacuous_truth_and_target_complement() -> None:
    source = offset_graph()
    predicate = Spans(
        offsets(),
        IntervalRelation.EQUAL,
        Quantifier.ALL,
        q("det"),
        Matches(Cell(q("type")), "date.*"),
    )
    assert selected(source, "gold", predicate) == ["g0", "g1"]


def test_pairs_request_refuses_wrong_kind_and_limit() -> None:
    base = {
        "match": "pairs",
        "left": {"select": "items", "tier": q("det").to_data()},
        "right": {"select": "items", "tier": q("gold").to_data()},
        "relation": "equal",
        "offsets": {"origin": q("start").to_data(), "end": q("end").to_data()},
    }
    with pytest.raises(Refusal, match="must be 'pairs'"):
        _pairs_request_loads(json.dumps({**base, "match": "focus"}))
    with pytest.raises(Refusal, match="nonnegative integer"):
        _pairs_request_loads(json.dumps({**base, "limit": -1}))
