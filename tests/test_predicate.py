"""The predicate core keeps exact value, absence, and JSON semantics."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import cast

import pytest

from tests.conformance.selection import SelectionLawSuite
from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeSelector,
    AttributeValue,
    BoundaryRef,
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
    WhereSelector,
    XsdType,
    evaluate_selection,
    selection_loads,
)
from tiergraph.core import JsonValue
from tiergraph.predicate import (
    And,
    Bare,
    Cell,
    Compare,
    Current,
    Double,
    Elements,
    Equals,
    Has,
    Matches,
    Not,
    Or,
    Order,
    Predicate,
    Quantifier,
    compile_predicate,
    predicate_loads,
    predicate_to_data,
)

NS = "urn:example:fixture#"


def q(local: str) -> QualifiedName:
    return QualifiedName(NS, local)


def xsd(local: str, value_type: XsdType, lexical: str) -> AttributeValue:
    return AttributeValue(q(local), value_type, lexical)


def native(local: str, value: JsonValue) -> JsonAttributeValue:
    return JsonAttributeValue(q(local), value)


def graph(
    tier_rows: tuple[tuple[str, tuple[AttributeValue | JsonAttributeValue, ...]], ...],
    declarations: tuple[tuple[str, XsdType | JsonType], ...],
    *,
    tier_name: str = "seg",
    other_rows: tuple[
        tuple[str, tuple[AttributeValue | JsonAttributeValue, ...]], ...
    ] = (),
) -> Graph:
    tiers = [
        Tier(
            TierDeclaration(q(tier_name), tier_name),
            tuple(Item(label, values) for label, values in tier_rows),
        )
    ]
    relations = [
        SimpleRelationDeclaration(
            q(f"{tier_name}-membership"), q(tier_name), q(f"{tier_name}-item")
        )
    ]
    if other_rows:
        tiers.append(
            Tier(
                TierDeclaration(q("other"), "other"),
                tuple(Item(label, values) for label, values in other_rows),
            )
        )
        relations.append(
            SimpleRelationDeclaration(
                q("other-membership"), q("other"), q("other-item")
            )
        )
    return Graph(
        (NamespaceDeclaration("ex", NS),),
        tuple(tiers),
        tuple(relations),
        attribute_declarations=tuple(
            AttributeDeclaration(q(name), AttributeDomain.ITEM, value_type)
            for name, value_type in declarations
        ),
    )


def stress6() -> Graph:
    return graph(
        (
            (
                "0",
                (
                    xsd("class", XsdType.STRING, "vowel"),
                    xsd("stress", XsdType.STRING, "primary"),
                ),
            ),
            (
                "1",
                (
                    xsd("class", XsdType.STRING, "vowel"),
                    xsd("stress", XsdType.STRING, "secondary"),
                ),
            ),
            ("2", (xsd("class", XsdType.STRING, "vowel"),)),
            (
                "3",
                (
                    xsd("class", XsdType.STRING, "vowel"),
                    xsd("stress", XsdType.STRING, "tertiary"),
                ),
            ),
            ("4", (xsd("class", XsdType.STRING, "consonant"),)),
            (
                "5",
                (
                    xsd("class", XsdType.STRING, "vowel"),
                    xsd("stress", XsdType.STRING, ""),
                ),
            ),
        ),
        (("class", XsdType.STRING), ("stress", XsdType.STRING)),
    )


def years() -> Graph:
    return graph(
        (
            ("y0", (xsd("year", XsdType.INTEGER, "900"),)),
            ("y1", (xsd("year", XsdType.INTEGER, "1000"),)),
            ("y2", (xsd("year", XsdType.INTEGER, "1999"),)),
            ("y3", ()),
        ),
        (("year", XsdType.INTEGER),),
        tier_name="year-items",
    )


def selected(source: Graph, predicate: Predicate, tier_name: str = "seg") -> list[str]:
    nodes = (
        compile_predicate(predicate)
        .bind(source)
        .select(evaluate_selection(source, ItemsSelector(q(tier_name))))
    )
    return labels(source, nodes.nodes)


def labels(source: Graph, nodes: tuple[Node, ...]) -> list[str]:
    result: list[str] = []
    for node in nodes:
        assert isinstance(node.reference, ItemRef)
        tier = next(
            tier
            for tier in source.tiers
            if tier.declaration.name == node.reference.tier
        )
        result.append(tier.items[node.reference.index].durable_id or "")
    return result


def f1_predicate() -> Predicate:
    return And(
        (
            Equals(Cell(q("class")), ("vowel",)),
            Or(
                (
                    Equals(Cell(q("stress")), ("primary", "secondary")),
                    Not(Has(Cell(q("stress")), alias="none")),
                )
            ),
        )
    )


def test_f1_closed_world_conjunction_and_disjunction() -> None:
    assert selected(stress6(), f1_predicate()) == ["0", "1", "2"]


def test_f2_negation_complements_the_candidate_domain() -> None:
    assert selected(stress6(), Not(Equals(Cell(q("stress")), ("primary",)))) == [
        "1",
        "2",
        "3",
        "4",
        "5",
    ]


def test_f3_presence_distinguishes_empty_from_missing() -> None:
    predicate = Has(Cell(q("stress")), alias="none")
    assert selected(stress6(), predicate) == ["0", "1", "3", "5"]
    assert selected(stress6(), Not(predicate)) == ["2", "4"]


def test_f4_membership_is_negated_as_one_slot_test() -> None:
    predicate = Not(Equals(Cell(q("stress")), ("primary", "secondary")))
    assert selected(stress6(), predicate) == ["2", "3", "4", "5"]


def test_f5_empty_string_is_a_value() -> None:
    assert selected(stress6(), Equals(Cell(q("stress")), ("",))) == ["5"]


def test_f6_integer_comparison_is_exact() -> None:
    source = years()
    year = Cell(q("year"))
    assert selected(source, Compare(year, Order.LT, 1000), "year-items") == ["y0"]
    assert selected(source, Compare(year, Order.LE, 1000), "year-items") == ["y0", "y1"]
    assert selected(source, Compare(year, Order.GE, 1000), "year-items") == ["y1", "y2"]
    assert selected(source, Not(Compare(year, Order.LT, 1000)), "year-items") == [
        "y1",
        "y2",
        "y3",
    ]


def test_f7_decimal_comparison_is_exact() -> None:
    source = graph(
        tuple(
            (label, (xsd("amount", XsdType.DECIMAL, lexical),))
            for label, lexical in (
                ("m0", "9.5"),
                ("m1", "10.0"),
                ("m2", "0.1"),
                ("m3", "-2.0"),
            )
        ),
        (("amount", XsdType.DECIMAL),),
        tier_name="amounts",
    )
    amount = Cell(q("amount"))
    assert selected(source, Compare(amount, Order.LT, Decimal("10")), "amounts") == [
        "m0",
        "m2",
        "m3",
    ]
    assert selected(source, Compare(amount, Order.GE, Decimal("0.1")), "amounts") == [
        "m0",
        "m1",
        "m2",
    ]


def test_f8_double_equality_uses_canonical_lexical_identity() -> None:
    source = graph(
        tuple(
            (label, (xsd("w", XsdType.DOUBLE, lexical),))
            for label, lexical in (
                ("w0", "0"),
                ("w1", "-0"),
                ("w2", "NaN"),
                ("w3", "1e3"),
            )
        ),
        (("w", XsdType.DOUBLE),),
        tier_name="words",
    )
    operand = Cell(q("w"))
    assert selected(source, Equals(operand, (Double("0"),)), "words") == ["w0"]
    assert selected(source, Equals(operand, (Double("-0"),)), "words") == ["w1"]
    assert selected(source, Equals(operand, (Double("NaN"),)), "words") == ["w2"]
    assert selected(source, Equals(operand, (Double("1e3"),)), "words") == ["w3"]
    assert selected(source, Equals(operand, (Double("-0"), Double("0"))), "words") == [
        "w0",
        "w1",
    ]
    with pytest.raises(Refusal) as caught:
        compile_predicate(Compare(operand, Order.LT, Double("1"))).bind(source)
    assert caught.value.stage is RefusalStage.SEMANTICS
    assert str(caught.value) == (
        "w<1.0E0: ordered comparison needs xsd:integer or xsd:decimal, "
        "and ex:w is xsd:double"
    )


def test_f9_json_ordering_accepts_only_integers() -> None:
    operand = Cell(q("value"), ("start", "y"))
    source = graph(
        (
            ("d0", (native("value", {"start": {"y": 950}}),)),
            ("d1", (native("value", {"start": {"y": 1200}}),)),
            ("d2", (native("value", {"start": {}}),)),
            ("d3", ()),
        ),
        (("value", JsonType.JSON),),
        tier_name="data",
    )
    assert selected(source, Compare(operand, Order.LT, 1000), "data") == ["d0"]
    for label, value, kind in (
        ("x-str", "0950", "string"),
        ("x-dbl", 950.0, "double"),
        ("x-bool", True, "boolean"),
    ):
        bad = graph(
            ((label, (native("value", {"start": {"y": value}}),)),),
            (("value", JsonType.JSON),),
            tier_name="data",
        )
        with pytest.raises(Refusal) as caught:
            selected(bad, Compare(operand, Order.LT, 1000), "data")
        assert caught.value.stage is RefusalStage.SEMANTICS
        assert str(caught.value) == (
            f"value/start/y<1000: item {label!r} stores a JSON {kind} at this cell; "
            "ordered comparison needs a JSON integer"
        )


def test_f10_json_equality_keeps_primitive_kinds_distinct() -> None:
    source = graph(
        tuple(
            (label, (native("value", {"start": {"y": value}}),))
            for label, value in (
                ("e0", 950),
                ("e1", 950.0),
                ("e2", True),
                ("e3", 1),
                ("e4", "0950"),
            )
        ),
        (("value", JsonType.JSON),),
        tier_name="data",
    )
    operand = Cell(q("value"), ("start", "y"))
    assert selected(source, Equals(operand, (950,)), "data") == ["e0"]
    assert selected(source, Equals(operand, (True,)), "data") == ["e2"]
    assert selected(source, Equals(operand, ("0950",)), "data") == ["e4"]


def alternatives(*, bad: tuple[str, JsonValue] | None = None) -> Graph:
    rows: list[tuple[str, tuple[AttributeValue | JsonAttributeValue, ...]]] = [
        ("a0", (native("alternatives", [{"provenance": "m"}, {"provenance": "x"}]),)),
        ("a1", (native("alternatives", [{"provenance": "x"}]),)),
        ("a2", (native("alternatives", []),)),
        ("a3", ()),
        ("a4", (native("alternatives", [{"p": 1}]),)),
    ]
    if bad is not None:
        label, value = bad
        rows.append((label, (native("alternatives", value),)))
    return graph(tuple(rows), (("alternatives", JsonType.JSON),), tier_name="alts")


def test_f11_array_quantifiers_are_defined_through_negation() -> None:
    operand = Cell(q("alternatives"))
    body_m = Equals(Current(("provenance",)), ("m",))
    body_x = Equals(Current(("provenance",)), ("x",))
    any_m = Elements(operand, Quantifier.ANY, body_m)
    none_m = Elements(operand, Quantifier.NONE, body_m)
    all_x = Elements(operand, Quantifier.ALL, body_x)
    source = alternatives()
    assert selected(source, any_m, "alts") == ["a0"]
    assert selected(source, none_m, "alts") == ["a1", "a2", "a3", "a4"]
    assert selected(source, all_x, "alts") == ["a1", "a2", "a3"]
    assert selected(source, And((Has(operand, alias="none"), none_m)), "alts") == [
        "a1",
        "a2",
        "a4",
    ]
    for bad, label, kind in (("notalist", "a5", "string"), ({}, "a6", "object")):
        with pytest.raises(Refusal) as caught:
            selected(alternatives(bad=(label, bad)), any_m, "alts")
        assert str(caught.value) == (
            f"alternatives: item {label!r} stores a JSON {kind}; "
            "any, all and none need a JSON array"
        )


def test_f12_regex_subset_uses_fullmatch_and_unicode_classes() -> None:
    source = graph(
        tuple(
            (label, (xsd("label", XsdType.STRING, value),))
            for label, value in (
                ("l0", "date"),
                ("l1", "date-interval"),
                ("l2", ""),
                ("l3", "a\nb"),
                ("l4", "٤٢"),
                ("l5", "a\u0303"),
                ("l6", "a" * 64),
            )
        ),
        (("label", XsdType.STRING),),
        tier_name="labels",
    )
    operand = Cell(q("label"))
    cases: tuple[tuple[str, list[str]], ...] = (
        ("date", ["l0"]),
        ("date.*", ["l0", "l1"]),
        ("", ["l2"]),
        (".*", ["l0", "l1", "l2", "l4", "l5", "l6"]),
        (r"\d+", ["l4"]),
        (r"\w+", ["l0", "l4", "l5", "l6"]),
        (r"(a|a)*b", []),
    )
    for regex, expected in cases:
        assert selected(source, Matches(operand, regex), "labels") == expected


@pytest.mark.parametrize(
    "regex, offset, message",
    [
        (
            "a*?",
            1,
            "'*?' is a lazy quantifier; every match is decided, so lazy and possessive quantifiers are refused",
        ),
        (
            r"(a)\1",
            3,
            r"'\1' is a backreference, which a regular language cannot express",
        ),
        ("(?=a)", 0, "'(?=' is lookaround, which the subset refuses"),
        ("^a", 0, "'^' is an anchor; '~' already matches the whole string"),
        (
            "(?i)a",
            2,
            "flag 'i' is not defined; no regex flag is defined yet, so write (?:…) for a plain group",
        ),
        ("(?<n>a)", 0, "'(?<' is a named group; write (?:…)"),
        (
            r"\p{L}",
            0,
            r"'\p' property classes are not in the subset; write a class such as \w or [a-z]",
        ),
        (r"\q", 0, r"'\q' is not an escape in the subset"),
        ("a{3,2}", 1, "'{3,2}' has minimum 3 greater than maximum 2"),
        ("a{10001}", 1, "repeat count 10001 exceeds limit 10000"),
        ("a" * 65_537, 0, "regex source exceeds 65536 bytes"),
        ("(a{100}){1001}", 0, "regex unrolls to 100100 positions; limit 100000"),
    ],
)
def test_f13_regex_refusals_are_literal(regex: str, offset: int, message: str) -> None:
    with pytest.raises(ValueError) as caught:
        Matches(Cell(q("label")), regex)
    assert str(caught.value) == f"regex at offset {offset}: {message}"


def test_regex_nesting_is_bounded_before_recursive_parsing() -> None:
    operand = Cell(q("label"))
    Matches(operand, "(" * 256 + ")" * 256)
    for depth in (257, 300):
        with pytest.raises(ValueError) as caught:
            Matches(operand, "(" * depth + ")" * depth)
        assert str(caught.value) == ("regex at offset 256: regex nests deeper than 256")


def test_character_classes_follow_perl_for_a_leading_close_bracket() -> None:
    operand = Cell(q("label"))
    for regex in ("[]", "[^]"):
        with pytest.raises(ValueError) as caught:
            Matches(operand, regex)
        assert str(caught.value) == "regex at offset 0: '[' is never closed"

    source = graph(
        (
            ("close", (xsd("label", XsdType.STRING, "]"),)),
            ("a", (xsd("label", XsdType.STRING, "a"),)),
            ("b", (xsd("label", XsdType.STRING, "b"),)),
        ),
        (("label", XsdType.STRING),),
        tier_name="labels",
    )
    assert selected(source, Matches(operand, "[]a]"), "labels") == ["close", "a"]
    assert selected(source, Matches(operand, "[^]a]"), "labels") == ["b"]


def test_f14_every_atom_is_evaluated_before_boolean_combination() -> None:
    source = graph(
        (
            (
                "r0",
                (
                    xsd("kind", XsdType.STRING, "a"),
                    native("value", {"start": {"y": 950}}),
                ),
            ),
            (
                "r1",
                (
                    xsd("kind", XsdType.STRING, "a"),
                    native("value", {"start": {"y": "0950"}}),
                ),
            ),
        ),
        (("kind", XsdType.STRING), ("value", JsonType.JSON)),
        tier_name="records",
    )
    false_atom = Equals(Cell(q("kind")), ("never",))
    refusing = Compare(Cell(q("value"), ("start", "y")), Order.LT, 1000)
    for predicate in (And((false_atom, refusing)), And((refusing, false_atom))):
        bound = compile_predicate(predicate).bind(source)
        with pytest.raises(Refusal, match="item 'r1' stores a JSON string"):
            bound.select(evaluate_selection(source, ItemsSelector(q("records"))))
        r1 = Node(NodeKind.ITEM, source.canonical_items()[0])
        assert bound.holds(r1) is False
        with pytest.raises(Refusal, match="item 'r1' stores a JSON string"):
            bound.holds(Node(NodeKind.ITEM, tuple(source.canonical_items())[1]))


def test_f15_where_complements_only_its_base_selection() -> None:
    source = graph(
        (
            ("0", (xsd("class", XsdType.STRING, "vowel"),)),
            ("1", (xsd("class", XsdType.STRING, "vowel"),)),
            ("2", (xsd("class", XsdType.STRING, "vowel"),)),
            ("3", (xsd("class", XsdType.STRING, "vowel"),)),
            ("4", (xsd("class", XsdType.STRING, "consonant"),)),
        ),
        (("class", XsdType.STRING),),
        other_rows=(
            ("o0", (xsd("class", XsdType.STRING, "consonant"),)),
            ("o1", (xsd("class", XsdType.STRING, "consonant"),)),
        ),
    )
    result = evaluate_selection(
        source,
        WhereSelector(
            ItemsSelector(q("seg")),
            Not(Equals(Cell(q("class")), ("vowel",))),
        ),
    )
    assert labels(source, result.nodes) == ["4"]


def test_f16_construction_and_bind_refusals_are_literal_and_staged() -> None:
    with pytest.raises(ValueError, match="^Equals needs at least one value$"):
        Equals(Cell(q("stress")), ())
    with pytest.raises(ValueError, match="^Equals lists 'a' twice$"):
        Equals(Cell(q("stress")), ("a", "a"))
    with pytest.raises(ValueError, match="^And needs zero or at least two arguments"):
        And((Has(Cell(q("stress")), alias="none"),))
    with pytest.raises(ValueError, match="^Or needs zero or at least two arguments"):
        Or((Has(Cell(q("stress")), alias="none"),))
    with pytest.raises(ValueError, match="^And may not directly contain an And"):
        And((And(()), Or(())))
    with pytest.raises(ValueError, match="^Or may not directly contain an Or"):
        Or((Or(()), And(())))
    with pytest.raises(ValueError, match="^Bare text '0 1' is not a bare token$"):
        Bare("0 1")
    for alias in ("∅", "0 1"):
        with pytest.raises(ValueError) as caught:
            Has(Cell(q("stress")), alias=alias)
        assert (
            str(caught.value)
            == f"Has alias {alias!r} is not a declarable missing-cell alias"
        )

    collision = graph(
        (("0", (xsd("stress", XsdType.STRING, "none"),)),),
        (("stress", XsdType.STRING),),
    )
    cases: tuple[tuple[Graph, Predicate, RefusalStage, str], ...] = (
        (
            collision,
            Has(Cell(q("stress")), alias="none"),
            RefusalStage.SEMANTICS,
            "stress=none: 'none' is both the missing-cell alias and a stored value of stress; write stress=\"none\" for the value, or declare a different missing alias",
        ),
        (
            stress6(),
            Equals(Cell(q("year")), (1999,)),
            RefusalStage.REFERENCE,
            "predicate names undeclared attribute ex:year",
        ),
        (
            stress6(),
            Compare(Cell(q("class")), Order.LT, 5),
            RefusalStage.SEMANTICS,
            "class<5: ordered comparison needs xsd:integer or xsd:decimal, and ex:class is xsd:string",
        ),
        (
            years(),
            Equals(Cell(q("year")), ("1999",)),
            RefusalStage.SEMANTICS,
            'year="1999": ex:year is xsd:integer and "1999" is a quoted string, so the test can never hold; write year=1999',
        ),
        (
            years(),
            Equals(Cell(q("year")), (Bare("abc"),)),
            RefusalStage.SEMANTICS,
            "year=abc: 'abc' is not an xsd:integer value",
        ),
        (
            stress6(),
            Equals(Cell(q("class"), ("start", "y")), ("x",)),
            RefusalStage.SEMANTICS,
            "class/start/y: ex:class is xsd:string, and only a JSON cell has keys",
        ),
        (
            years(),
            Matches(Cell(q("year")), "1.*"),
            RefusalStage.SEMANTICS,
            "year~\"1.*\": '~' needs xsd:string or a JSON string, and ex:year is xsd:integer",
        ),
        (
            stress6(),
            Equals(Current(), ("x",)),
            RefusalStage.SEMANTICS,
            "'.' names the current element or token; an item test names a cell, as in class=vowel",
        ),
    )
    for source, predicate, stage, message in cases:
        with pytest.raises(Refusal) as caught:
            compile_predicate(predicate).bind(source)
        assert caught.value.stage is stage
        assert str(caught.value) == message


def test_f17_predicate_json_is_strict_and_kind_preserving() -> None:
    predicates: tuple[Predicate, ...] = (
        f1_predicate(),
        Not(Equals(Cell(q("stress")), ("primary",))),
        Has(Cell(q("stress")), alias="none"),
        Equals(Cell(q("stress")), ("",)),
        Compare(Cell(q("year")), Order.LT, 1000),
        Compare(Cell(q("amount")), Order.GE, Decimal("0.1")),
        Equals(Cell(q("w")), (Double("-0"), Double("0"))),
        Matches(Cell(q("label")), r"\w+"),
        Elements(
            Cell(q("alternatives")),
            Quantifier.ANY,
            Equals(Current(("provenance",)), ("m",)),
        ),
    )
    for predicate in predicates:
        encoded = json.dumps(predicate_to_data(predicate), allow_nan=False)
        assert predicate_loads(encoded) == predicate
    negative = Equals(Cell(q("w")), (Double("-0"),))
    assert predicate_to_data(negative) == {
        "test": "equals",
        "operand": {"cell": q("w").to_data()},
        "values": [{"double": "-0.0E0"}],
    }
    assert predicate_loads(json.dumps(predicate_to_data(negative))) != Equals(
        Cell(q("w")), (Double("0"),)
    )
    assert Equals(Cell(q("e")), (1,)) != Equals(Cell(q("e")), (True,))
    Equals(Cell(q("e")), (1, True))

    with pytest.raises(Refusal) as caught:
        predicate_loads(
            json.dumps({"test": "has", "operand": {"cell": q("e").to_data()}})
        )
    assert caught.value.stage is RefusalStage.SHAPE
    assert str(caught.value) == "$ is missing fields ['alias']"
    with pytest.raises(Refusal) as caught:
        predicate_loads('{"test":"maybe"}')
    assert caught.value.stage is RefusalStage.DISCRIMINATOR
    assert str(caught.value) == "$.test has unknown test 'maybe'"
    for document in (
        {"test": "not", "arg": {"test": "and", "args": []}, "extra": 1},
        {"test": "not", "op": "union", "arg": {"test": "and", "args": []}},
    ):
        with pytest.raises(Refusal):
            predicate_loads(json.dumps(document))


def test_f18_where_presence_matches_attribute_selection() -> None:
    source = stress6()
    where = evaluate_selection(
        source,
        WhereSelector(ItemsSelector(q("seg")), Has(Cell(q("stress")), alias="none")),
    )
    attribute = evaluate_selection(
        source, AttributeSelector(q("stress"), AttributeDomain.ITEM)
    )
    assert where == attribute
    assert labels(source, where.nodes) == ["0", "1", "3", "5"]


def test_f19_holds_uses_the_same_missing_and_empty_semantics() -> None:
    source = stress6()
    bound = compile_predicate(f1_predicate()).bind(source)
    nodes = tuple(evaluate_selection(source, ItemsSelector(q("seg"))).nodes)
    assert bound.holds(nodes[2]) is True
    assert bound.holds(nodes[5]) is False


def test_where_selector_json_decodes_the_predicate_dialect() -> None:
    data: JsonValue = {
        "select": "where",
        "base": {"select": "items", "tier": q("seg").to_data()},
        "predicate": predicate_to_data(Has(Cell(q("stress")), alias="none")),
    }
    selector = selection_loads(json.dumps(data))
    assert selector == WhereSelector(
        ItemsSelector(q("seg")), Has(Cell(q("stress")), alias="none")
    )
    assert isinstance(selector, WhereSelector)
    assert selected(stress6(), selector.predicate) == ["0", "1", "3", "5"]


def test_predicate_constructors_refuse_values_outside_the_ast() -> None:
    with pytest.raises(ValueError, match="is not xsd:double"):
        Double("not-a-double")
    with pytest.raises(TypeError, match="Cell pointer keys must be strings"):
        Cell(q("value"), cast(tuple[str, ...], (1,)))
    with pytest.raises(TypeError, match="Current pointer keys must be strings"):
        Current(cast(tuple[str, ...], (1,)))
    with pytest.raises(TypeError, match="predicate literals are"):
        Equals(Cell(q("value")), (cast(object, 1.0),))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="predicate literals are"):
        Compare(Cell(q("value")), Order.LT, cast(object, 1.0))  # type: ignore[arg-type]


def test_regex_admitted_forms_run_on_the_internal_nfa() -> None:
    source = graph(
        tuple(
            (label, (xsd("label", XsdType.STRING, value),))
            for label, value in (
                ("empty", ""),
                ("a", "a"),
                ("aa", "aa"),
                ("b", "b"),
                ("space", "\u2003"),
                ("tab", "\t"),
                ("slash", "/"),
            )
        ),
        (("label", XsdType.STRING),),
        tier_name="labels",
    )
    operand = Cell(q("label"))
    cases: tuple[tuple[str, list[str]], ...] = (
        ("a?", ["empty", "a"]),
        ("a{1,2}", ["a", "aa"]),
        ("a{ 1 , }", ["a", "aa"]),
        ("a{,2}", ["empty", "a", "aa"]),
        ("(?:a|b)", ["a", "b"]),
        ("[a-c]", ["a", "b"]),
        ("[^a]", ["b", "space", "tab", "slash"]),
        (r"\s", ["space", "tab"]),
        (r"\S", ["a", "b", "slash"]),
        (r"\D", ["a", "b", "space", "tab", "slash"]),
        (r"\W", ["space", "tab", "slash"]),
        (r"\x{61}", ["a"]),
        (r"\t", ["tab"]),
        (r"\/", ["slash"]),
    )
    for regex, expected in cases:
        assert selected(source, Matches(operand, regex), "labels") == expected


@pytest.mark.parametrize(
    "regex, fragment",
    [
        (")", "unexpected ')' in regex"),
        ("a{", "'{' is never closed"),
        ("a{x}", "is not a counted quantifier"),
        ("(?P<n>a)", "'(?P<' is a named group"),
        ("(?", "regex group extension is incomplete"),
        ("(a", "'(' is never closed"),
        ("*", "has nothing before it to repeat"),
        ("\\", "is not an escape in the subset"),
        (r"\b", "is an anchor"),
        (r"\x{}", "needs a braced hexadecimal code point"),
        (r"\x{110000}", "names no Unicode code point"),
        (r"[a-\d]", "range needs literal endpoints"),
        ("[z-a]", "range z-a is reversed"),
        ("[abc", "'[' is never closed"),
    ],
)
def test_more_regex_refusals_are_decided_during_construction(
    regex: str, fragment: str
) -> None:
    with pytest.raises(ValueError, match=None) as caught:
        Matches(Cell(q("label")), regex)
    assert fragment in str(caught.value)


def test_json_pointers_observe_arrays_and_missing_keys() -> None:
    source = graph(
        (
            (
                "p0",
                (native("value", {"array": [{"x": 1}], "scalar": 3}),),
            ),
        ),
        (("value", JsonType.JSON),),
        tier_name="data",
    )
    assert selected(
        source, Equals(Cell(q("value"), ("array", "0", "x")), (1,)), "data"
    ) == ["p0"]
    for pointer in (
        ("array", "2", "x"),
        ("array", "01", "x"),
        ("scalar", "x"),
    ):
        assert selected(source, Equals(Cell(q("value"), pointer), (1,)), "data") == []


def test_holds_reads_every_graph_attribute_carrier() -> None:
    suite = SelectionLawSuite(evaluate_selection)
    source = suite.valued_graph()
    names = suite.valued_domain_names()
    tier = suite.name("valued")
    nodes = {
        AttributeDomain.DOCUMENT: Node(NodeKind.DOCUMENT, None),
        AttributeDomain.TIER: Node(NodeKind.TIER, tier),
        AttributeDomain.ITEM: Node(NodeKind.ITEM, ItemRef(tier, 0)),
        AttributeDomain.BOUNDARY: Node(NodeKind.BOUNDARY, BoundaryRef(tier, 0)),
        AttributeDomain.RELATION_DECLARATION: Node(
            NodeKind.RELATION_DECLARATION, suite.name("valued-members")
        ),
        AttributeDomain.RELATION_INSTANCE: Node(NodeKind.RELATION_INSTANCE, 0),
    }
    for domain, node in nodes.items():
        assert (
            compile_predicate(Has(Cell(names[domain]), alias="none"))
            .bind(source)
            .holds(node)
        )
    assert (
        compile_predicate(
            Has(Cell(names[AttributeDomain.RELATION_INSTANCE]), alias="none")
        )
        .bind(source)
        .holds(Node(NodeKind.POLYADIC_RELATION_INSTANCE, 0))
    )
    assert (
        not compile_predicate(Has(Cell(names[AttributeDomain.ITEM]), alias="none"))
        .bind(source)
        .holds(Node(NodeKind.ITEM, None))
    )


def test_bound_predicate_rejects_a_candidate_set_from_another_graph() -> None:
    source = stress6()
    other = stress6()
    bound = compile_predicate(Has(Cell(q("stress")), alias="none")).bind(source)
    with pytest.raises(Refusal, match="candidates from the bound graph"):
        bound.select(evaluate_selection(other, ItemsSelector(q("seg"))))


def test_elements_binding_requires_json_and_checks_nested_aliases() -> None:
    with pytest.raises(Refusal, match="need a JSON array"):
        compile_predicate(
            Elements(Cell(q("stress")), Quantifier.ANY, Equals(Current(), ("x",)))
        ).bind(stress6())
    collision = graph(
        (("a0", (native("alternatives", [{"provenance": "none"}]),)),),
        (("alternatives", JsonType.JSON),),
        tier_name="alts",
    )
    with pytest.raises(Refusal, match="both the missing-cell alias and a stored value"):
        compile_predicate(
            Elements(
                Cell(q("alternatives")),
                Quantifier.ANY,
                Has(Current(("provenance",)), alias="none"),
            )
        ).bind(collision)
    clean = Elements(
        Cell(q("alternatives")),
        Quantifier.ANY,
        Has(Current(("missing",)), alias="none"),
    )
    compile_predicate(clean).bind(alternatives())


def test_bare_literals_are_typed_by_xsd_and_json_cells() -> None:
    assert selected(
        years(), Equals(Cell(q("year")), (Bare("0900"),)), "year-items"
    ) == ["y0"]
    flags = graph(
        (
            ("b0", (xsd("flag", XsdType.BOOLEAN, "true"),)),
            ("b1", (xsd("flag", XsdType.BOOLEAN, "false"),)),
        ),
        (("flag", XsdType.BOOLEAN),),
        tier_name="flags",
    )
    assert selected(flags, Equals(Cell(q("flag")), (Bare("1"),)), "flags") == ["b0"]
    doubles = graph(
        (("d0", (xsd("double", XsdType.DOUBLE, "-0"),)),),
        (("double", XsdType.DOUBLE),),
        tier_name="doubles",
    )
    assert selected(doubles, Equals(Cell(q("double")), (Bare("-0"),)), "doubles") == [
        "d0"
    ]
    strings = graph(
        (("s0", (xsd("text", XsdType.STRING, "word"),)),),
        (("text", XsdType.STRING),),
        tier_name="strings",
    )
    assert selected(strings, Equals(Cell(q("text")), (Bare("word"),)), "strings") == [
        "s0"
    ]
    source = graph(
        tuple(
            (label, (native("value", value),))
            for label, value in (
                ("i", 950),
                ("d", 950.0),
                ("e", 1000.0),
                ("b", True),
                ("n", None),
                ("s", "0950"),
                ("huge", "1e999"),
                ("constant", "Infinity"),
            )
        ),
        (("value", JsonType.JSON),),
        tier_name="data",
    )
    for literal, expected in (
        (Bare("950"), ["i"]),
        (Bare("950.0"), ["d"]),
        (Bare("1e3"), ["e"]),
        (Bare("true"), ["b"]),
        (Bare("null"), ["n"]),
        (Bare("0950"), ["s"]),
        (Bare("1e999"), []),
        ("1e999", ["huge"]),
        (Bare("Infinity"), ["constant"]),
        (Decimal("950"), []),
    ):
        assert (
            selected(source, Equals(Cell(q("value")), (literal,)), "data") == expected
        )
    with pytest.raises(Refusal, match="not an exact numeric comparison value"):
        compile_predicate(Compare(Cell(q("value")), Order.LT, Bare("word"))).bind(
            source
        )
    with pytest.raises(Refusal, match="not an exact numeric comparison value"):
        compile_predicate(Compare(Cell(q("value")), Order.LT, None)).bind(source)
    with pytest.raises(Refusal, match="not an exact numeric comparison value"):
        compile_predicate(Compare(Cell(q("value")), Order.LT, True)).bind(source)

    null_graph = Graph(
        (NamespaceDeclaration("ex", NS),),
        (),
        (),
        attribute_declarations=(
            AttributeDeclaration(q("value"), AttributeDomain.DOCUMENT, JsonType.JSON),
        ),
        attributes=(native("value", None),),
    )
    bound = compile_predicate(Compare(Cell(q("value")), Order.LT, 0)).bind(null_graph)
    with pytest.raises(Refusal, match="stores a JSON null") as caught:
        bound.holds(Node(NodeKind.DOCUMENT, None))
    assert "item 'None'" in str(caught.value)


def test_compare_current_refuses_a_non_numeric_literal_at_bind() -> None:
    source = graph(
        (("a0", (native("alts", [{"y": 5}]),)),),
        (("alts", JsonType.JSON),),
        tier_name="t",
    )
    predicate = Elements(
        Cell(q("alts")),
        Quantifier.ANY,
        Compare(Current(("y",)), Order.LT, "x"),
    )
    with pytest.raises(Refusal) as caught:
        compile_predicate(predicate).bind(source)
    assert caught.value.stage is RefusalStage.SEMANTICS
    assert str(caught.value) == "'x' is not an exact numeric comparison value"


def test_predicate_json_decoder_refuses_each_wrong_shape() -> None:
    cell: JsonValue = {"cell": q("value").to_data()}
    bad_documents: tuple[JsonValue, ...] = (
        {},
        {"test": 1},
        {"test": "equals", "operand": cell, "values": "x"},
        {"test": "compare", "operand": cell, "order": "?", "value": 1},
        {
            "test": "elements",
            "operand": cell,
            "quantifier": "some",
            "body": {"test": "and", "args": []},
        },
        {"test": "and", "args": "x"},
        {"test": "has", "operand": {}, "alias": "none"},
        {
            "test": "has",
            "operand": {"cell": q("value").to_data(), "pointer": "x"},
            "alias": "none",
        },
        {"test": "equals", "operand": cell, "values": [1.5]},
        {"test": "equals", "operand": cell, "values": [{}]},
        {
            "test": "equals",
            "operand": cell,
            "values": [{"bare": "x", "double": "1"}],
        },
        {"test": "equals", "operand": cell, "values": [{"decimal": "x"}]},
    )
    for document in bad_documents:
        with pytest.raises((Refusal, ValueError)):
            predicate_loads(json.dumps(document))


def test_predicate_json_round_trips_every_literal_and_pointer_form() -> None:
    predicate = And(
        (
            Equals(Cell(q("value"), ("array", "0")), (Bare("x"), None)),
            Or(
                (
                    Equals(Current(("key",)), (Double("-0"),)),
                    Compare(Cell(q("value")), Order.GT, Decimal("0.5")),
                )
            ),
        )
    )
    assert predicate_loads(json.dumps(predicate_to_data(predicate))) == predicate
