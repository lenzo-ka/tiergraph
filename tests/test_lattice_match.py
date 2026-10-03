"""Lattice path matching under declared ambiguity policies (plan rows L1-L9)."""

from __future__ import annotations

import json
from dataclasses import replace
from itertools import pairwise
from pathlib import Path
from typing import cast

import pytest

import tiergraph.pathoutput as pathoutput
from tiergraph import (
    COUNTING,
    LOG_PROBABILITY,
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    Graph,
    Item,
    ItemRef,
    JsonAttributeValue,
    JsonType,
    NamespaceDeclaration,
    Node,
    QualifiedName,
    Refusal,
    RelationInstance,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    XsdType,
    dumps,
)
from tiergraph.cli import main
from tiergraph.core import JsonValue
from tiergraph.fold import (
    AttributeValuation,
    ChildCombination,
    FoldDeclaration,
    FoldTransition,
)
from tiergraph.match import (
    AtomPattern,
    CompiledPattern,
    StartPattern,
    _match_request_loads,
    compile_pattern,
    parse_pattern,
    pattern_to_data,
)
from tiergraph.pathoutput import (
    AmbiguityPolicy,
    Determinize,
    Emissions,
    OutputPlan,
    Unambiguous,
    match_lattice,
)
from tiergraph.pathplan import PathPlan
from tiergraph.predicate import (
    And,
    Bare,
    Compare,
    Current,
    Elements,
    Equals,
    Has,
    IntervalRelation,
    Matches,
    OffsetProfile,
    Or,
    Order,
    Predicate,
    PredicateSyntax,
    Quantifier,
    Related,
    Spans,
)
from tiergraph.semiring import Semiring
from tiergraph.traversal import WalkDirection

NS = "urn:example:fixture#"
NODES = QualifiedName(NS, "nodes")
NODE = QualifiedName(NS, "node")
NEXT = QualifiedName(NS, "next")
COUNT = QualifiedName(NS, "count")
WEIGHT = QualifiedName(NS, "weight")
TOK = QualifiedName(NS, "tok")
TOKS = QualifiedName(NS, "toks")
OTHER = QualifiedName(NS, "other")
SYNTAX = PredicateSyntax((NamespaceDeclaration("ex", NS),), default_prefix="ex")
COUNTING_OBJECT = cast(Semiring[object], COUNTING)


def lattice_graph(
    labels: tuple[str, ...],
    edges: tuple[tuple[str, str], ...],
    *,
    tok: dict[str, str] | None = None,
    toks: dict[str, object] | None = None,
) -> Graph:
    """Build a neutral one-tier finite path DAG with optional emissions."""
    positions = {label: index for index, label in enumerate(labels)}
    items = []
    for label in labels:
        attributes: list[AttributeValue | JsonAttributeValue] = [
            AttributeValue(COUNT, XsdType.INTEGER, "1"),
            AttributeValue(WEIGHT, XsdType.DOUBLE, "-0.5"),
        ]
        if tok is not None and label in tok:
            attributes.append(AttributeValue(TOK, XsdType.STRING, tok[label]))
        if toks is not None and label in toks:
            attributes.append(JsonAttributeValue(TOKS, cast(JsonValue, toks[label])))
        items.append(Item(label, tuple(attributes)))
    return Graph(
        (NamespaceDeclaration("ex", NS),),
        (Tier(TierDeclaration(NODES, "Nodes"), tuple(items)),),
        (
            SimpleRelationDeclaration(QualifiedName(NS, "membership"), NODES, NODE),
            BipartiteRelationDeclaration(NEXT, NODE, NODE, acyclic=True),
        ),
        tuple(
            RelationInstance(
                NEXT,
                ItemRef(NODES, positions[parent]),
                ItemRef(NODES, positions[child]),
            )
            for parent, child in edges
        ),
        (
            AttributeDeclaration(COUNT, AttributeDomain.ITEM, XsdType.INTEGER),
            AttributeDeclaration(WEIGHT, AttributeDomain.ITEM, XsdType.DOUBLE),
            AttributeDeclaration(TOK, AttributeDomain.ITEM, XsdType.STRING),
            AttributeDeclaration(TOKS, AttributeDomain.ITEM, JsonType.JSON),
        ),
    )


def plan(
    graph: Graph,
    *,
    roots: tuple[str, ...],
    semiring: Semiring[object] = COUNTING_OBJECT,
) -> PathPlan[object]:
    """Prepare the fixture graph as a rooted path plan."""
    positions = {
        item.durable_id: ItemRef(NODES, index)
        for index, item in enumerate(graph.tiers[0].items)
    }
    counting = semiring is cast(object, COUNTING)
    attribute = COUNT if counting else WEIGHT
    return PathPlan.prepare(
        FoldDeclaration(
            "lattice",
            graph,
            AttributeValuation("value", attribute, (NODES,)),
            semiring,
            lambda _value, _label: 1 if counting else -0.5,
            (FoldTransition(NEXT, ChildCombination.OR),),
            roots=tuple(positions[label] for label in roots),
        )
    )


def parsed(text: str) -> CompiledPattern:
    """Compile one token-only pattern text."""
    return compile_pattern(parse_pattern(text, SYNTAX))


def lat_a(*, logarithmic: bool = False) -> tuple[PathPlan[object], Emissions[object]]:
    labels = ("r", "a1", "a2", "a3", "s", "f")
    graph = lattice_graph(
        labels,
        (
            ("r", "a1"),
            ("r", "a2"),
            ("r", "a3"),
            ("a1", "s"),
            ("s", "f"),
            ("a1", "f"),
            ("a2", "f"),
            ("a3", "f"),
        ),
    )
    base = plan(
        graph,
        roots=("r",),
        semiring=cast(Semiring[object], LOG_PROBABILITY if logarithmic else COUNTING),
    )
    return base, Emissions.bind(
        base,
        {
            "r": ("ten",),
            "a1": ("o",),
            "a2": ("oh",),
            "a3": ("zero",),
            "f": ("five",),
        },
    )


def lat_8() -> tuple[PathPlan[object], Emissions[object]]:
    junctions = tuple(f"j{index}" for index in range(9))
    choices = tuple(
        f"c{index}{token}" for index in range(8) for token in ("o", "oh", "zero")
    )
    edges = tuple(
        edge
        for index in range(8)
        for token in ("o", "oh", "zero")
        for edge in (
            (f"j{index}", f"c{index}{token}"),
            (f"c{index}{token}", f"j{index + 1}"),
        )
    )
    graph = lattice_graph((*junctions, *choices), edges)
    base = plan(graph, roots=("j0",))
    return base, Emissions.bind(
        base,
        {
            f"c{index}{token}": (token,)
            for index in range(8)
            for token in ("o", "oh", "zero")
        },
    )


def labels(nodes: tuple[Node, ...], graph: Graph) -> list[str]:
    """Read durable labels from item nodes."""
    return [
        cast(
            str,
            graph.tiers[0].items[cast(ItemRef, node.reference).index].durable_id,
        )
        for node in nodes
    ]


def test_l1_boolean_counts_all_paths_and_silent_provenance() -> None:
    """L1 kills exists-as-all and dropping silent accepting-path items."""
    base, emissions = lat_a()
    complete = match_lattice(
        emissions,
        parsed("{.=ten} ({.=o} | {.=oh} | {.=zero}) {.=five}"),
    )
    restricted = match_lattice(emissions, parsed("{.=ten} ({.=o} | {.=oh}) {.=five}"))
    for policy in (Unambiguous(), Determinize(64)):
        assert complete.count(policy) == 4
        assert complete.all_paths(policy)
        assert restricted.count(policy) == 3
        assert not restricted.all_paths(policy)
    assert complete.exists()
    assert labels(complete.on_accepting_path().nodes, base.declaration.graph) == [
        "r",
        "a1",
        "a2",
        "a3",
        "s",
        "f",
    ]
    assert labels(restricted.on_accepting_path().nodes, base.declaration.graph) == [
        "r",
        "a1",
        "a2",
        "s",
        "f",
    ]


def test_l2_path_counts_are_not_nfa_run_counts() -> None:
    """L2 kills run counting on the 3-to-the-8 lattice."""
    _base, emissions = lat_8()
    all_tokens = match_lattice(emissions, parsed("({.=o} | {.=oh} | {.=zero}){8}"))
    two_tokens = match_lattice(emissions, parsed("({.=o} | {.=oh}){8}"))
    ambiguous = match_lattice(emissions, parsed(".* {.=zero} .*"))
    rewritten = match_lattice(emissions, parsed("{.!=zero}* {.=zero} .*"))
    for policy in (Unambiguous(), Determinize(64)):
        assert all_tokens.count(policy) == 6561
        assert all_tokens.all_paths(policy)
        assert two_tokens.count(policy) == 256
        assert not two_tokens.all_paths(policy)
    assert ambiguous.count(Determinize(64)) == 6305
    assert not ambiguous.all_paths(Determinize(64))
    assert rewritten.count(Unambiguous()) == 6305


def test_l3_count_ignores_path_plan_values() -> None:
    """L3 kills folding the source plan's log weights instead of units."""
    _base, emissions = lat_a(logarithmic=True)
    lattice = match_lattice(
        emissions,
        parsed("{.=ten} ({.=o} | {.=oh} | {.=zero}) {.=five}"),
    )
    assert lattice.count(Unambiguous()) == 4


def test_l4_determinization_counts_paths_and_unambiguous_names_witness() -> None:
    """L4 kills NFA-run counting and pins the shortest ambiguity witness."""
    base, emissions = lat_a()
    restricted = match_lattice(emissions, parsed("{.=ten} ({.=o} | {.=oh}) {.=five}"))
    duplicate = match_lattice(emissions, parsed("{.=ten} ({.=o} | {.=o}) {.=five}"))
    assert restricted.count(Determinize(64)) == 3
    assert OutputPlan.prepare(
        base, emissions, (("ten", "o", "five"), ("ten", "oh", "five"))
    ).masses().per_candidate == (2, 1)
    assert duplicate.count(Determinize(64)) == 2
    assert OutputPlan.prepare(
        base, emissions, (("ten", "o", "five"),)
    ).masses().per_candidate == (2,)
    with pytest.raises(
        Refusal,
        match=(
            r'pattern is ambiguous: the tokens "ten" "o" "five" have two '
            r"accepting runs; count under Determinize"
        ),
    ):
        duplicate.count(Unambiguous())


def test_l5_emissions_from_string_json_and_absence() -> None:
    """L5 kills empty-string absence and whitespace splitting."""
    labels_ = ("r", "a1", "a2", "s", "f")
    graph = lattice_graph(
        labels_,
        (("r", "a1"), ("a1", "a2"), ("a2", "s"), ("s", "f")),
        tok={"r": "ten", "s": ""},
        toks={"a1": ["o", "five"], "a2": []},
    )
    base = plan(graph, roots=("r",))
    assert Emissions.from_attribute(base, TOK).per_item == (
        ("ten",),
        (),
        (),
        ("",),
        (),
    )
    assert Emissions.from_attribute(base, TOKS).per_item == (
        (),
        ("o", "five"),
        (),
        (),
        (),
    )
    bad_item = replace(
        graph.tiers[0].items[1],
        attributes=tuple(
            JsonAttributeValue(TOKS, 5) if value.name == TOKS else value
            for value in graph.tiers[0].items[1].attributes
        ),
    )
    bad_tier = replace(
        graph.tiers[0],
        items=(graph.tiers[0].items[0], bad_item, *graph.tiers[0].items[2:]),
    )
    bad_graph = replace(graph, tiers=(bad_tier,))
    with pytest.raises(
        Refusal,
        match=(
            "from_attribute needs a JSON array of strings at ex:toks; "
            "item 'a1' stores a JSON integer"
        ),
    ):
        Emissions.from_attribute(plan(bad_graph, roots=("r",)), TOKS)
    with pytest.raises(
        Refusal,
        match=(
            "from_attribute needs an xsd:string or JSON attribute; "
            "ex:count is xsd:integer"
        ),
    ):
        Emissions.from_attribute(base, COUNT)

    with pytest.raises(Refusal, match="names undeclared attribute ex:other"):
        Emissions.from_attribute(base, OTHER)
    wrong_domain = replace(
        graph,
        attribute_declarations=(
            *graph.attribute_declarations,
            AttributeDeclaration(OTHER, AttributeDomain.DOCUMENT, XsdType.STRING),
        ),
    )
    with pytest.raises(Refusal, match="needs an item attribute"):
        Emissions.from_attribute(plan(wrong_domain, roots=("r",)), OTHER)

    array_item = replace(
        graph.tiers[0].items[1],
        attributes=tuple(
            JsonAttributeValue(TOKS, ["o", 5]) if value.name == TOKS else value
            for value in graph.tiers[0].items[1].attributes
        ),
    )
    array_graph = replace(
        graph,
        tiers=(
            replace(
                graph.tiers[0],
                items=(
                    graph.tiers[0].items[0],
                    array_item,
                    *graph.tiers[0].items[2:],
                ),
            ),
        ),
    )
    with pytest.raises(Refusal, match="array with an integer at index 1"):
        Emissions.from_attribute(plan(array_graph, roots=("r",)), TOKS)

    null_item = replace(
        graph.tiers[0].items[1],
        attributes=tuple(
            JsonAttributeValue(TOKS, None) if value.name == TOKS else value
            for value in graph.tiers[0].items[1].attributes
        ),
    )
    null_graph = replace(
        graph,
        tiers=(
            replace(
                graph.tiers[0],
                items=(
                    graph.tiers[0].items[0],
                    null_item,
                    *graph.tiers[0].items[2:],
                ),
            ),
        ),
    )
    with pytest.raises(Refusal, match="stores a JSON null"):
        Emissions.from_attribute(plan(null_graph, roots=("r",)), TOKS)


def _de_bruijn_binary(order: int) -> list[int]:
    sequence: list[int] = []
    workspace = [0] * (2 * order)

    def visit(index: int, period: int) -> None:
        if index > order:
            if order % period == 0:
                sequence.extend(workspace[1 : period + 1])
            return
        workspace[index] = workspace[index - period]
        visit(index + 1, period)
        for value in range(workspace[index - period] + 1, 2):
            workspace[index] = value
            visit(index + 1, index)

    visit(1, 1)
    return sequence


def test_l6_determinize_bound_refuses_without_partial_count() -> None:
    """L6 reaches 4097 subsets and kills partial or cut-at-bound counts."""
    sequence = _de_bruijn_binary(13)
    sequence.extend(sequence[:12])
    labels_ = tuple(f"i{index}" for index in range(len(sequence)))
    graph = lattice_graph(labels_, tuple(pairwise(labels_)))
    base = plan(graph, roots=("i0",))
    emissions = Emissions.bind(
        base,
        {
            label: (("a" if value == 0 else "b"),)
            for label, value in zip(labels_, sequence, strict=True)
        },
    )
    lattice = match_lattice(emissions, parsed(".* {.=b} .{12}"))
    assert lattice.exists()
    assert lattice.count(Unambiguous()) == 1
    assert lattice.all_paths(Unambiguous())
    with pytest.raises(
        Refusal,
        match=(
            "counting needs more than 4096 determinized states on this lattice "
            r"\(reached 4097\); no count is reported"
        ),
    ):
        lattice.count(Determinize(4096))


def test_l7_unambiguous_refuses_with_least_witness() -> None:
    """L7 kills skipping the squared-automaton ambiguity check."""
    _base, emissions = lat_8()
    lattice = match_lattice(emissions, parsed(".* {.=zero} .*"))
    with pytest.raises(
        Refusal,
        match=r'pattern is ambiguous: the tokens "zero" "zero"',
    ):
        lattice.count(Unambiguous())


def test_ambiguity_check_has_a_finite_declared_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The robustness bound refuses instead of exploring a squared product forever."""
    _base, emissions = lat_a()
    monkeypatch.setattr(pathoutput, "_MAX_AMBIGUITY_PAIRS", 1)
    lattice = match_lattice(emissions, parsed("{.=ten} {.=o} {.=five}"))
    with pytest.raises(
        Refusal,
        match="ambiguity checking needs more than 100000 state pairs",
    ):
        lattice.count(Unambiguous())


def test_l8_lattice_atoms_focus_and_anchors_are_refused() -> None:
    """L8 kills lowering aliases or impossible token tests to silent failure."""
    _base, emissions = lat_a()
    with pytest.raises(Refusal, match="a lattice token is always present"):
        match_lattice(emissions, parsed("{.=none}"))
    with pytest.raises(
        Refusal,
        match=(
            r"a lattice token is a string, so the literal 1 \(int\) can never "
            r"equal it; write .=\"1\""
        ),
    ):
        match_lattice(
            emissions,
            compile_pattern(AtomPattern(Equals(Current(), (1,)))),
        )
    with pytest.raises(Refusal, match=r"tests the token '\.', not a cell"):
        match_lattice(emissions, parsed("{class=vowel}"))
    with pytest.raises(Refusal, match="reports paths, not a focus"):
        match_lattice(emissions, parsed("{.=ten} / _ ."))
    with pytest.raises(
        Refusal,
        match=r"matched against the whole path; '\^' and '\$' add nothing",
    ):
        match_lattice(emissions, compile_pattern(StartPattern()))


def test_lattice_token_predicate_subset_and_validation_branches() -> None:
    """Every admitted token atom evaluates; every graph-only atom refuses."""
    _base, emissions = lat_a()
    current = Current()
    assert (
        match_lattice(
            emissions,
            compile_pattern(AtomPattern(Matches(current, "ten"))),
        ).exists()
        is False
    )
    assert (
        match_lattice(
            emissions,
            parsed('{.=ten} {.!="never"}* ({.=o} | {.=oh} | {.=zero}) {.=five}'),
        ).count(Determinize(64))
        == 4
    )
    combined = AtomPattern(Or((Equals(current, ("ten",)), Matches(current, "t.*"))))
    assert not match_lattice(emissions, compile_pattern(combined)).all_paths(
        Unambiguous()
    )
    conjunction = AtomPattern(And((Equals(current, ("ten",)), Matches(current, "t.*"))))
    assert (
        match_lattice(emissions, compile_pattern(conjunction)).count(Unambiguous()) == 0
    )
    invalid: tuple[Predicate, ...] = (
        Equals(Current(("x",)), ("x",)),
        Compare(current, Order.LT, Bare("2")),
        Elements(current, Quantifier.ANY, And(())),
        Related(OTHER, WalkDirection.FORWARD, Quantifier.ANY, And(())),
        Spans(
            OffsetProfile(COUNT, end=COUNT),
            IntervalRelation.EQUAL,
            Quantifier.ANY,
            NODES,
            And(()),
        ),
        Has(current, "none"),
    )
    messages = (
        "not a pointer beneath it",
        "ordered comparison has no meaning",
        "Elements has no meaning",
        "Related has no meaning",
        "Spans has no meaning",
        "missing-cell alias",
    )
    for predicate, message in zip(invalid, messages, strict=True):
        with pytest.raises(Refusal, match=message):
            match_lattice(emissions, compile_pattern(AtomPattern(predicate)))


def test_lattice_api_rejects_invalid_policy_and_arguments() -> None:
    """Public constructors reject values outside the two declared policies."""
    _base, emissions = lat_a()
    with pytest.raises(ValueError, match="positive integer"):
        Determinize(0)
    lattice = match_lattice(emissions, parsed("{.=ten} .*"))
    invalid = cast(AmbiguityPolicy, object())
    with pytest.raises(TypeError, match="count needs"):
        lattice.count(invalid)
    with pytest.raises(TypeError, match="all_paths needs"):
        lattice.all_paths(invalid)
    with pytest.raises(TypeError, match="emissions must"):
        match_lattice(cast(Emissions[object], object()), parsed("."))
    with pytest.raises(TypeError, match="pattern must"):
        match_lattice(emissions, cast(CompiledPattern, object()))


def test_l9_cli_lattice_request_requires_policy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """L9 pins the CLI result and kills a silent default ambiguity policy."""
    base, _emissions = lat_a()
    graph = base.declaration.graph
    graph_file = tmp_path / "lat-a.json"
    graph_file.write_text(dumps(graph), encoding="utf-8")
    request: dict[str, object] = {
        "match": "lattice",
        "transitions": [NEXT.to_data()],
        "roots": [ItemRef(NODES, 0).to_data()],
        "emission": TOK.to_data(),
        "pattern": pattern_to_data(
            parse_pattern("{.=ten} ({.=o} | {.=oh} | {.=zero}) {.=five}", SYNTAX)
        ),
        "policy": {"policy": "unambiguous"},
    }
    items = []
    emissions = {"r": "ten", "a1": "o", "a2": "oh", "a3": "zero", "f": "five"}
    for item in graph.tiers[0].items:
        attributes = item.attributes
        if item.durable_id in emissions:
            attributes = (
                *attributes,
                AttributeValue(TOK, XsdType.STRING, emissions[item.durable_id]),
            )
        items.append(replace(item, attributes=attributes))
    graph = replace(graph, tiers=(replace(graph.tiers[0], items=tuple(items)),))
    graph_file.write_text(dumps(graph), encoding="utf-8")
    request_file = tmp_path / "request.json"
    request_file.write_text(json.dumps(request), encoding="utf-8")
    assert main(["match", str(graph_file), "--request", str(request_file)]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "exists": True,
        "count": 4,
        "all_paths": True,
    }
    request["policy"] = {"policy": "determinize", "max_states": 64}
    request_file.write_text(json.dumps(request), encoding="utf-8")
    assert main(["match", str(graph_file), "--request", str(request_file)]) == 0
    assert json.loads(capsys.readouterr().out)["count"] == 4
    request.pop("policy")
    request_file.write_text(json.dumps(request), encoding="utf-8")
    assert main(["match", str(graph_file), "--request", str(request_file)]) == 1
    assert "is missing fields ['policy']" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("change", "message"),
    (
        ({"transitions": {}}, "transitions must be an array"),
        ({"transitions": []}, "must name exactly one path relation"),
        ({"roots": {}}, "roots must be an array"),
        ({"policy": {}}, "is missing fields"),
        (
            {"policy": {"policy": "determinize"}},
            "is missing fields",
        ),
        (
            {"policy": {"policy": "determinize", "max_states": 0}},
            "max_states must be a positive integer",
        ),
        ({"policy": {"policy": "other"}}, "unknown ambiguity policy"),
    ),
)
def test_lattice_request_shape_refusals(
    change: dict[str, object], message: str
) -> None:
    """The lattice JSON envelope is strict and never invents a policy."""
    request: dict[str, object] = {
        "match": "lattice",
        "transitions": [NEXT.to_data()],
        "roots": [ItemRef(NODES, 0).to_data()],
        "emission": TOK.to_data(),
        "pattern": pattern_to_data(parse_pattern(".", SYNTAX)),
        "policy": {"policy": "unambiguous"},
    }
    request.update(change)
    with pytest.raises(Refusal, match=message):
        _match_request_loads(json.dumps(request))
