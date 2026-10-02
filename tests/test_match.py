"""Regular sequence patterns over declared graph orderings."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

import pytest

import tiergraph
from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    DifferenceSelector,
    Graph,
    Item,
    ItemRef,
    ItemsSelector,
    JsonAttributeValue,
    JsonType,
    NamespaceDeclaration,
    Node,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    QualifiedName,
    Refusal,
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
    selection_loads,
)
from tiergraph.cli import main
from tiergraph.match import (
    AdjacentRuns,
    AltPattern,
    AtomPattern,
    ContainerOrder,
    EndPattern,
    FocusPattern,
    RepeatPattern,
    SeqPattern,
    StartPattern,
    TierOrder,
    _decode_ordering,
    _evaluate_match_request,
    _match_request_loads,
    compile_pattern,
    ordering_to_data,
    pattern_loads,
    pattern_to_data,
)
from tiergraph.predicate import (
    And,
    Bare,
    Cell,
    Compare,
    Equals,
    IntervalRelation,
    Not,
    OffsetProfile,
    Order,
    Quantifier,
    Spans,
)
from tiergraph.selection import _STRUCTURAL_PATH_PROFILE

NS = "urn:example:fixture#"


def q(local: str) -> QualifiedName:
    """Return one name in the neutral fixture namespace."""
    return QualifiedName(NS, local)


def scalar(local: str, value: str, kind: XsdType = XsdType.STRING) -> AttributeValue:
    """Return one scalar item attribute."""
    return AttributeValue(q(local), kind, value)


def graph_with_tiers(
    tiers: tuple[Tier, ...],
    attributes: tuple[AttributeDeclaration, ...],
    *,
    declarations: tuple[object, ...] = (),
    polyadic: tuple[PolyadicRelationInstance, ...] = (),
) -> Graph:
    """Build a graph with simple membership plus optional structure."""
    membership = tuple(
        SimpleRelationDeclaration(
            q(f"{tier.declaration.name.local_name}-members"),
            tier.declaration.name,
            q(f"{tier.declaration.name.local_name}-item"),
        )
        for tier in tiers
    )
    return Graph(
        (NamespaceDeclaration("ex", NS),),
        tiers,
        (*membership, *declarations),  # type: ignore[arg-type]
        attribute_declarations=attributes,
        polyadic_relations=polyadic,
    )


def labels(graph: Graph, nodes: Iterable[Node]) -> list[str]:
    """Return durable labels in carried order."""
    result = []
    for node in nodes:
        assert isinstance(node.reference, ItemRef)
        tier = next(
            value
            for value in graph.tiers
            if value.declaration.name == node.reference.tier
        )
        label = tier.items[node.reference.index].durable_id
        assert label is not None
        result.append(label)
    return result


def atom(attribute: str, value: str) -> AtomPattern:
    """Return one bare string equality atom."""
    return AtomPattern(Equals(Cell(q(attribute)), (Bare(value),)))


def any_item() -> AtomPattern:
    """Return the always-true consuming atom."""
    return AtomPattern(And(()))


def ch_k() -> Graph:
    """Build CH-K exactly as the matching plan defines it."""
    segment = q("seg")
    syllable = q("syl")
    parts = q("parts")
    relation = PolyadicRelationDeclaration(
        parts,
        RelationSideDeclaration(
            (RelationEndpointKind.ITEM,), tiers=(syllable,), maximum=1
        ),
        RelationSideDeclaration((RelationEndpointKind.ITEM,), tiers=(segment,)),
        unique_sources=True,
        acyclic=True,
    )
    return graph_with_tiers(
        (
            Tier(
                TierDeclaration(segment, "Segments"),
                tuple(
                    Item(f"i{index}", (scalar("class", value),))
                    for index, value in enumerate(
                        ("obstruent", "vowel", "nasal", "vowel", "nasal", "vowel")
                    )
                ),
            ),
            Tier(
                TierDeclaration(syllable, "Syllables"),
                tuple(Item(f"s{index}") for index in range(3)),
            ),
        ),
        (AttributeDeclaration(q("class"), AttributeDomain.ITEM, XsdType.STRING),),
        declarations=(relation,),
        polyadic=(
            PolyadicRelationInstance(
                parts,
                (ItemRef(syllable, 0),),
                tuple(ItemRef(segment, index) for index in (0, 1, 2)),
            ),
            PolyadicRelationInstance(
                parts, (ItemRef(syllable, 1),), (ItemRef(segment, 3),)
            ),
            PolyadicRelationInstance(
                parts,
                (ItemRef(syllable, 2),),
                tuple(ItemRef(segment, index) for index in (4, 5)),
            ),
        ),
    )


def test_m1_focus_respects_container_scopes() -> None:
    """M1: focus is [i1] by container and [i1,i3] by whole tier."""
    graph = ch_k()
    pattern = SeqPattern((FocusPattern(atom("class", "vowel")), atom("class", "nasal")))
    compiled = compile_pattern(pattern)
    assert labels(
        graph,
        compiled.focus(
            graph, ContainerOrder(q("parts"), ItemsSelector(q("syl")))
        ).nodes,
    ) == ["i1"]
    assert labels(graph, compiled.focus(graph, TierOrder(q("seg"))).nodes) == [
        "i1",
        "i3",
    ]


def fr3() -> Graph:
    """Build FR3 exactly as the matching plan defines it."""
    rows = (
        ("May", "month", 0, 3),
        ("2024", "digits", 4, 8),
        (",", "punct", 8, 9),
        ("June", "month", 10, 14),
        ("7", "digits", 15, 16),
        ("in", "word", 17, 19),
        ("July", "month", 20, 24),
        (",", "punct", 24, 25),
        ("9", "digits", 26, 27),
    )
    word = Tier(
        TierDeclaration(q("w"), "Words"),
        tuple(
            Item(
                f"w{index}",
                (
                    scalar("surface", surface),
                    scalar("class", word_class),
                    scalar("start", str(start), XsdType.INTEGER),
                    scalar("end", str(end), XsdType.INTEGER),
                ),
            )
            for index, (surface, word_class, start, end) in enumerate(rows)
        ),
    )
    dates = Tier(
        TierDeclaration(q("date"), "Dates"),
        (
            Item(
                "D0",
                (
                    scalar("start", "0", XsdType.INTEGER),
                    scalar("end", "8", XsdType.INTEGER),
                ),
            ),
        ),
    )
    return graph_with_tiers(
        (word, dates),
        (
            AttributeDeclaration(q("surface"), AttributeDomain.ITEM, XsdType.STRING),
            AttributeDeclaration(q("class"), AttributeDomain.ITEM, XsdType.STRING),
            AttributeDeclaration(q("start"), AttributeDomain.ITEM, XsdType.INTEGER),
            AttributeDeclaration(q("end"), AttributeDomain.ITEM, XsdType.INTEGER),
        ),
    )


def test_m2_anchors_context_and_span_predicate_select_the_last_digits() -> None:
    """M2: the full structural query focuses only w8."""
    graph = fr3()
    offsets = OffsetProfile(q("start"), end=q("end"))
    digit = And(
        (
            Equals(Cell(q("class")), ("digits",)),
            Spans(
                offsets,
                IntervalRelation.WITHIN,
                Quantifier.NONE,
                q("date"),
                And(()),
            ),
        )
    )
    pattern = SeqPattern(
        (
            atom("class", "month"),
            RepeatPattern(any_item(), 0, 1),
            FocusPattern(AtomPattern(digit)),
            AltPattern(
                (
                    EndPattern(),
                    AtomPattern(Not(Equals(Cell(q("surface")), ("in",)))),
                )
            ),
        )
    )
    assert labels(
        graph, compile_pattern(pattern).focus(graph, TierOrder(q("w"))).nodes
    ) == ["w8"]


def ic3() -> tuple[Graph, OffsetProfile]:
    """Build IC3 and its declared offset profile."""
    rows = (
        ("In", "PLAIN", 0, 2),
        ("10", "CARDINAL", 3, 5),
        ("-", "PLAIN", 5, 6),
        ("12", "CARDINAL", 6, 8),
        ("and", "PLAIN", 9, 12),
        ("3", "CARDINAL", 13, 14),
        ("–", "PLAIN", 15, 16),
        ("4", "CARDINAL", 16, 17),
    )
    graph = graph_with_tiers(
        (
            Tier(
                TierDeclaration(q("tok"), "Tokens"),
                tuple(
                    Item(
                        f"t{index}",
                        (
                            scalar("surface", surface),
                            scalar("class", token_class),
                            scalar("start", str(start), XsdType.INTEGER),
                            scalar("end", str(end), XsdType.INTEGER),
                        ),
                    )
                    for index, (surface, token_class, start, end) in enumerate(rows)
                ),
            ),
        ),
        (
            AttributeDeclaration(q("surface"), AttributeDomain.ITEM, XsdType.STRING),
            AttributeDeclaration(q("class"), AttributeDomain.ITEM, XsdType.STRING),
            AttributeDeclaration(q("start"), AttributeDomain.ITEM, XsdType.INTEGER),
            AttributeDeclaration(q("end"), AttributeDomain.ITEM, XsdType.INTEGER),
        ),
    )
    return graph, OffsetProfile(q("start"), end=q("end"))


def test_m3_adjacent_runs_report_one_physical_span() -> None:
    """M3: adjacency excludes the second superficially matching triple."""
    graph, offsets = ic3()
    pattern = SeqPattern(
        (
            atom("class", "CARDINAL"),
            AtomPattern(Equals(Cell(q("surface")), ("-", "–"))),
            atom("class", "CARDINAL"),
        )
    )
    matches = compile_pattern(pattern).spans(
        graph, AdjacentRuns(ItemsSelector(q("tok")), offsets)
    )
    assert [
        (match.offsets, labels(graph, match.items)) for match in matches.matches
    ] == [((3, 8), ["t1", "t2", "t3"])]


def chain(text: str) -> Graph:
    """Build one CHAINS tier from its letters."""
    return graph_with_tiers(
        (
            Tier(
                TierDeclaration(q("seg"), "Segments"),
                tuple(
                    Item(str(index), (scalar("seg", value),))
                    for index, value in enumerate(text)
                ),
            ),
        ),
        (AttributeDeclaration(q("seg"), AttributeDomain.ITEM, XsdType.STRING),),
    )


def span_bounds(matches: object) -> list[tuple[int, int]]:
    """Return logical bounds from a SpanMatches value."""
    return [(match.start, match.end) for match in matches.matches]  # type: ignore[attr-defined]


def test_m4_alternation_deduplicates_nfa_runs() -> None:
    """M4: duplicate alternatives yield one span, count one, and existence."""
    graph = chain("a")
    compiled = compile_pattern(AltPattern((atom("seg", "a"), atom("seg", "a"))))
    assert span_bounds(compiled.spans(graph, TierOrder(q("seg")))) == [(0, 1)]
    assert compiled.count(graph, TierOrder(q("seg"))) == 1
    assert compiled.exists(graph, TierOrder(q("seg"))) is True


def test_m5_bounded_repeat_maximum_is_inclusive() -> None:
    """M5: a{2,3} over four a items yields five spans."""
    graph = chain("aaaa")
    compiled = compile_pattern(RepeatPattern(atom("seg", "a"), 2, 3))
    assert span_bounds(compiled.spans(graph, TierOrder(q("seg")))) == [
        (0, 2),
        (0, 3),
        (1, 3),
        (1, 4),
        (2, 4),
    ]
    assert compiled.count(graph, TierOrder(q("seg"))) == 5


def de_bruijn_binary(order: int) -> str:
    """Return the binary FKM de Bruijn cycle of the requested order."""
    a = [0] * (order * 2)
    sequence: list[int] = []

    def visit(t: int, period: int) -> None:
        if t > order:
            if order % period == 0:
                sequence.extend(a[1 : period + 1])
            return
        a[t] = a[t - period]
        visit(t + 1, period)
        for value in range(a[t - period] + 1, 2):
            a[t] = value
            visit(t + 1, t)

    visit(1, 1)
    return "".join("a" if value == 0 else "b" for value in sequence)


def test_m6a_large_nfa_decisions_are_exact_without_a_dfa_cap() -> None:
    """M6a: the DB13 chain exists and has 4,096 distinct length-13 spans."""
    cycle = de_bruijn_binary(13)
    graph = chain(cycle + cycle[:12])
    either = AltPattern((atom("seg", "a"), atom("seg", "b")))
    exists = SeqPattern(
        (
            StartPattern(),
            RepeatPattern(either, 0, None),
            atom("seg", "b"),
            RepeatPattern(either, 12, 12),
            EndPattern(),
        )
    )
    count = SeqPattern((atom("seg", "b"), RepeatPattern(any_item(), 12, 12)))
    assert compile_pattern(exists).exists(graph, TierOrder(q("seg"))) is True
    assert compile_pattern(count).count(graph, TierOrder(q("seg"))) == 4096


def test_m6d_expanded_positions_are_checked_before_nfa_construction() -> None:
    """M6d: nested repeat positions multiply and stop beyond 100,000."""
    one = atom("seg", "a")
    with pytest.raises(Refusal, match="100000000 item positions; limit 100000"):
        compile_pattern(
            RepeatPattern(RepeatPattern(one, 10_000, 10_000), 10_000, 10_000)
        )
    compile_pattern(RepeatPattern(RepeatPattern(any_item(), 100, 100), 1000, 1000))
    with pytest.raises(Refusal, match="100100 item positions; limit 100000"):
        compile_pattern(RepeatPattern(RepeatPattern(any_item(), 100, 100), 1001, 1001))


def test_m6e_nullable_patterns_exist_but_have_no_span_count() -> None:
    """M6e: a* exists, while spans and count refuse empty-sequence patterns."""
    graph = chain("aaa")
    compiled = compile_pattern(RepeatPattern(atom("seg", "a"), 0, None))
    assert compiled.exists(graph, TierOrder(q("seg"))) is True
    with pytest.raises(Refusal, match="pattern can match the empty sequence"):
        compiled.spans(graph, TierOrder(q("seg")))
    with pytest.raises(Refusal, match="pattern can match the empty sequence"):
        compiled.count(graph, TierOrder(q("seg")))


def jsonr() -> Graph:
    """Build JSONR exactly as the matching plan defines it."""
    return graph_with_tiers(
        (
            Tier(
                TierDeclaration(q("r"), "Records"),
                (
                    Item(
                        "r0",
                        (
                            scalar("kind", "a"),
                            JsonAttributeValue(q("value"), {"start": {"y": 950}}),
                        ),
                    ),
                    Item(
                        "r1",
                        (
                            scalar("kind", "a"),
                            JsonAttributeValue(q("value"), {"start": {"y": "0950"}}),
                        ),
                    ),
                ),
            ),
        ),
        (
            AttributeDeclaration(q("kind"), AttributeDomain.ITEM, XsdType.STRING),
            AttributeDeclaration(q("value"), AttributeDomain.ITEM, JsonType.JSON),
        ),
    )


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("operation", ["exists", "count"])
def test_m7_atoms_are_eager_for_every_item(reverse: bool, operation: str) -> None:
    """M7: a later JSON kind refusal is independent of NFA need and conjunct order."""
    graph = jsonr()
    parts = (
        Equals(Cell(q("kind")), ("never",)),
        Compare(Cell(q("value"), ("start", "y")), Order.LT, 1000),
    )
    predicate = And(tuple(reversed(parts)) if reverse else parts)
    compiled = compile_pattern(AtomPattern(predicate))
    with pytest.raises(Refusal, match="item 'r1' stores a JSON string"):
        getattr(compiled, operation)(graph, TierOrder(q("r")))


def test_m8_sequence_selector_json_and_match_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """M8: selector JSON and the request CLI retain container ordering."""
    graph = ch_k()
    pattern = SeqPattern((FocusPattern(atom("class", "vowel")), atom("class", "nasal")))
    selector = SequenceSelector(
        ContainerOrder(q("parts"), ItemsSelector(q("syl"))), pattern
    )
    loaded = selection_loads(json.dumps(selector.to_data()))
    assert loaded == selector
    assert labels(graph, evaluate_selection(graph, loaded).nodes) == ["i1"]

    source = tmp_path / "graph.json"
    request = tmp_path / "request.json"
    source.write_bytes(tiergraph.dump_bytes(graph))
    request.write_text(
        json.dumps(
            {
                "match": "focus",
                "ordering": selector.to_data()["ordering"],
                "pattern": selector.to_data()["pattern"],
            }
        ),
        encoding="utf-8",
    )
    assert main(["match", str(source), "--request", str(request)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert [node["reference"]["index"] for node in output["nodes"]] == [1]


def test_pattern_ast_constraints_have_literal_refusals() -> None:
    """The direct AST keeps every S5b constraint folded into S5."""
    value = atom("seg", "a")
    cases = (
        (lambda: SeqPattern((value,)), "SeqPattern needs at least two"),
        (
            lambda: SeqPattern((value, SeqPattern((value, value)))),
            "may not directly contain a SeqPattern",
        ),
        (lambda: AltPattern((value,)), "AltPattern needs at least two"),
        (
            lambda: AltPattern((value, AltPattern((value, value)))),
            "may not directly contain an AltPattern",
        ),
        (lambda: RepeatPattern(value, -1, 1), "minimum must be"),
        (lambda: RepeatPattern(value, 0, -1), "maximum must be"),
        (lambda: RepeatPattern(value, 0, 0), "maximum 0"),
        (lambda: RepeatPattern(value, 3, 2), "minimum 3"),
        (lambda: RepeatPattern(value, 10_001, None), "exceeds limit"),
        (lambda: RepeatPattern(StartPattern(), 1, 1), "contains an anchor"),
        (lambda: FocusPattern(StartPattern()), "can match no item"),
        (
            lambda: FocusPattern(SeqPattern((StartPattern(), value))),
            "contains an anchor",
        ),
        (lambda: FocusPattern(FocusPattern(value)), "nested focus"),
    )
    for build, message in cases:
        with pytest.raises(ValueError, match=message):
            build()


def test_compile_constraints_and_operation_refusals() -> None:
    """Compile placement limits and operation-time refusals remain distinct."""
    value = atom("seg", "a")
    with pytest.raises(Refusal, match="2 focus marks"):
        compile_pattern(SeqPattern((FocusPattern(value), FocusPattern(value))))
    with pytest.raises(Refusal, match="top-level SeqPattern"):
        compile_pattern(RepeatPattern(FocusPattern(value), 1, 1))
    with pytest.raises(Refusal, match="more than 10000 AST nodes"):
        compile_pattern(AltPattern(tuple(value for _ in range(10_001))))

    graph = chain("b")
    compiled = compile_pattern(value)
    assert compiled.exists(graph, TierOrder(q("seg"))) is False
    with pytest.raises(Refusal, match="pattern has no focus"):
        compiled.focus(graph, TierOrder(q("seg")))
    with pytest.raises(ValueError, match="nonnegative integer"):
        compiled.spans(graph, TierOrder(q("seg")), limit=-1)
    assert compiled.spans(graph, TierOrder(q("seg")), limit=0).to_data() == {
        "matches": [],
        "extent": "exhaustive",
    }


def test_span_limit_and_empty_adjacent_ordering() -> None:
    """A witness cut is declared, while an empty adjacent source stays empty."""
    graph = chain("aa")
    limited = compile_pattern(atom("seg", "a")).spans(
        graph, TierOrder(q("seg")), limit=1
    )
    assert limited.to_data()["extent"] == "cut-at-bound"
    assert limited.to_data()["matches"] == [limited.matches[0].to_data()]

    empty = graph_with_tiers(
        (Tier(TierDeclaration(q("empty"), "Empty"), ()),),
        (
            AttributeDeclaration(q("start"), AttributeDomain.ITEM, XsdType.INTEGER),
            AttributeDeclaration(q("end"), AttributeDomain.ITEM, XsdType.INTEGER),
        ),
    )
    ordering = AdjacentRuns(
        ItemsSelector(q("empty")), OffsetProfile(q("start"), end=q("end"))
    )
    assert compile_pattern(any_item()).exists(empty, ordering) is False


def test_container_order_refuses_nonitem_containers() -> None:
    """A container selector must return items, not tier nodes."""
    graph = ch_k()
    compiled = compile_pattern(any_item())
    with pytest.raises(Refusal, match="containers must select items"):
        compiled.exists(graph, ContainerOrder(q("parts"), TierSelector(q("syl"))))


def test_repeated_container_incidence_evaluates_each_atom_once_per_item() -> None:
    """Repeated stored incidence does not reevaluate one atom on the same item."""
    graph = ch_k()
    first = graph.polyadic_relations[0]
    object.__setattr__(first, "targets", (*first.targets, first.targets[1]))
    assert compile_pattern(any_item()).exists(
        graph, ContainerOrder(q("parts"), ItemsSelector(q("syl")))
    )
    assert repr(_STRUCTURAL_PATH_PROFILE) == "StructuralPathProfile()"


def test_pattern_and_ordering_json_cover_every_variant() -> None:
    """Pattern and ordering JSON preserve each graph-free variant."""
    value = atom("seg", "a")
    patterns = (
        value,
        SeqPattern((value, EndPattern())),
        AltPattern((value, any_item())),
        RepeatPattern(value, 1, 2),
        RepeatPattern(value, 0, None),
        FocusPattern(value),
        StartPattern(),
        EndPattern(),
    )
    for pattern in patterns:
        assert pattern_loads(json.dumps(pattern_to_data(pattern))) == pattern

    graph, offsets = ic3()
    del graph
    orders = (
        TierOrder(q("tok")),
        ContainerOrder(q("parts"), ItemsSelector(q("syl"))),
        AdjacentRuns(ItemsSelector(q("tok")), offsets),
    )
    for ordering in orders:
        assert _decode_ordering(ordering_to_data(ordering), "$") == ordering


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ({}, "missing fields"),
        ({"pattern": "maybe"}, "unknown pattern"),
        ({"pattern": "seq", "parts": 1}, "parts must be a list"),
        (
            {
                "pattern": "repeat",
                "body": {"pattern": "atom", "predicate": {"test": "and", "args": []}},
                "min": -1,
            },
            "nonnegative integer",
        ),
    ],
)
def test_pattern_json_refuses_invalid_shapes(source: object, message: str) -> None:
    """Strict pattern JSON refuses missing, unknown, and ill-typed members."""
    with pytest.raises((Refusal, ValueError), match=message):
        pattern_loads(json.dumps(source))


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ({}, "missing fields"),
        ({"order": "sideways"}, "unknown ordering"),
    ],
)
def test_ordering_json_refuses_invalid_discriminators(
    source: object, message: str
) -> None:
    """Strict ordering JSON requires one known order discriminator."""
    with pytest.raises(Refusal, match=message):
        _decode_ordering(source, "$")  # type: ignore[arg-type]


def test_sequence_ordering_encodes_selector_algebra() -> None:
    """Container ordering JSON retains where, union, and difference selectors."""
    selected = DifferenceSelector(
        UnionSelector(
            (
                ItemsSelector(q("syl")),
                WhereSelector(ItemsSelector(q("syl")), And(())),
            )
        ),
        ItemsSelector(q("seg")),
    )
    ordering = ContainerOrder(q("parts"), selected)
    assert _decode_ordering(ordering_to_data(ordering), "$") == ordering
    with pytest.raises(ValueError, match="no sequence-ordering JSON encoder"):
        ordering_to_data(ContainerOrder(q("parts"), TierSelector(q("syl"))))


def test_match_request_operations_and_strict_refusals() -> None:
    """The request reader dispatches decisions, witnesses, and pair joins."""
    graph = chain("aa")
    pattern = pattern_to_data(atom("seg", "a"))
    ordering = ordering_to_data(TierOrder(q("seg")))
    for operation, expected in (
        ("exists", {"exists": True}),
        ("count", {"count": 2}),
        ("spans", {"extent": "cut-at-bound"}),
    ):
        request: dict[str, object] = {
            "match": operation,
            "ordering": ordering,
            "pattern": pattern,
        }
        if operation == "spans":
            request["limit"] = 1
        result = _evaluate_match_request(graph, json.dumps(request))
        for key, value in expected.items():
            assert result[key] == value

    with pytest.raises(Refusal, match="missing fields"):
        _match_request_loads("{}")
    with pytest.raises(Refusal, match="unknown match operation"):
        _match_request_loads('{"match":"maybe"}')
    with pytest.raises(Refusal, match="unknown fields"):
        _match_request_loads(
            json.dumps(
                {
                    "match": "exists",
                    "ordering": ordering,
                    "pattern": pattern,
                    "limit": 1,
                }
            )
        )


def test_pair_request_dispatches_through_match_cli_reader() -> None:
    """The general request route retains the existing pair operation."""
    graph, offsets = ic3()
    request = {
        "match": "pairs",
        "left": {"select": "items", "tier": q("tok").to_data()},
        "right": {"select": "items", "tier": q("tok").to_data()},
        "relation": "equal",
        "offsets": {
            "origin": q("start").to_data(),
            "end": q("end").to_data(),
        },
    }
    assert _evaluate_match_request(graph, json.dumps(request)) == {
        "pairs": [],
        "extent": "exhaustive",
    }
