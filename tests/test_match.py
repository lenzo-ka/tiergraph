"""Regular sequence patterns over declared graph orderings."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
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


def _assert_atom_bind_refusal(call: Callable[[], object]) -> None:
    with pytest.raises(
        Refusal, match="predicate names undeclared attribute ex:missing-attr"
    ) as caught:
        call()
    assert caught.value.stage is RefusalStage.REFERENCE


def _unbound_atom() -> AtomPattern:
    return AtomPattern(Equals(Cell(q("missing-attr")), (Bare("x"),)))


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


def test_exists_binds_atoms_before_reading_scopes() -> None:
    """Exists reports an atom bind refusal before an ordering refusal."""
    graph = chain("a")
    compiled = compile_pattern(_unbound_atom())
    _assert_atom_bind_refusal(
        lambda: compiled.exists(graph, TierOrder(q("missing-tier")))
    )


def test_focus_binds_atoms_before_reading_scopes_and_checking_focus() -> None:
    """Focus binds atoms before ordering and missing-focus checks."""
    graph = chain("a")
    focused = compile_pattern(FocusPattern(_unbound_atom()))
    _assert_atom_bind_refusal(
        lambda: focused.focus(graph, TierOrder(q("missing-tier")))
    )
    unfocused = compile_pattern(_unbound_atom())
    _assert_atom_bind_refusal(lambda: unfocused.focus(graph, TierOrder(q("seg"))))


def test_spans_binds_atoms_before_reading_scopes_and_checking_nullable() -> None:
    """Spans binds atoms before ordering and nullable-pattern checks."""
    graph = chain("a")
    compiled = compile_pattern(_unbound_atom())
    _assert_atom_bind_refusal(
        lambda: compiled.spans(graph, TierOrder(q("missing-tier")))
    )
    nullable = compile_pattern(RepeatPattern(_unbound_atom(), 0, None))
    _assert_atom_bind_refusal(lambda: nullable.spans(graph, TierOrder(q("seg"))))


def test_count_binds_atoms_before_reading_scopes_and_checking_nullable() -> None:
    """Count binds atoms before ordering and nullable-pattern checks."""
    graph = chain("a")
    compiled = compile_pattern(_unbound_atom())
    _assert_atom_bind_refusal(
        lambda: compiled.count(graph, TierOrder(q("missing-tier")))
    )
    nullable = compile_pattern(RepeatPattern(_unbound_atom(), 0, None))
    _assert_atom_bind_refusal(lambda: nullable.count(graph, TierOrder(q("seg"))))


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


def test_compiled_pattern_max_width_is_exact() -> None:
    a = atom("seg", "a")
    cases = (
        (StartPattern(), 0),
        (a, 1),
        (FocusPattern(a), 1),
        (AltPattern((a, SeqPattern((a, a)))), 2),
        (RepeatPattern(a, 2, 3), 3),
        (SeqPattern((StartPattern(), RepeatPattern(a, 0, 2), EndPattern())), 2),
        (RepeatPattern(a, 0, None), None),
        (SeqPattern((a, RepeatPattern(a, 1, None))), None),
        (RepeatPattern(RepeatPattern(a, 1, None), 1, 2), None),
    )
    for pattern, expected in cases:
        assert compile_pattern(pattern).max_width == expected

    for pattern, expected in (cases[1], cases[3], cases[4]):
        spans = compile_pattern(pattern).spans(chain("aaaa"), TierOrder(q("seg")))
        observed = max(end - start for start, end in span_bounds(spans))
        assert observed == expected


def test_open_right_reports_settled_results_and_watermarks() -> None:
    ordering = TierOrder(q("seg"))
    a, b = atom("seg", "a"), atom("seg", "b")
    partial = compile_pattern(SeqPattern((a, b)))
    graph = chain("a")
    spans = partial.spans(graph, ordering, open_right=True)
    assert spans.pending_from == (0,)
    assert spans.result.matches == ()
    assert partial.count(graph, ordering, open_right=True).result == 0
    assert partial.exists(graph, ordering, open_right=True).result is False

    settled = compile_pattern(a)
    open_spans = settled.spans(graph, ordering, open_right=True)
    assert open_spans.pending_from == (1,)
    assert span_bounds(open_spans.result) == [(0, 1)]
    assert settled.count(graph, ordering, open_right=True).result == 1
    focused = compile_pattern(FocusPattern(a)).focus(graph, ordering, open_right=True)
    assert focused.pending_from == (1,)
    assert labels(graph, focused.result.nodes) == ["0"]

    end_dependent = compile_pattern(SeqPattern((a, EndPattern())))
    assert end_dependent.exists(graph, ordering) is True
    open_end = end_dependent.exists(graph, ordering, open_right=True)
    assert open_end.result is False
    assert open_end.pending_from == (0,)

    dead = compile_pattern(SeqPattern((StartPattern(), a)))
    dead_open = dead.exists(chain("b"), ordering, open_right=True)
    assert dead_open.result is False
    assert dead_open.pending_from == (None,)

    unbounded = compile_pattern(RepeatPattern(a, 1, None))
    live = unbounded.spans(chain("aaa"), ordering, open_right=True)
    assert live.pending_from == (0,)
    assert live.result.matches == ()

    accepting = settled.exists(graph, ordering, open_right=True)
    assert accepting.result is True
    assert accepting.pending_from == (1,)
    empty_accepting = compile_pattern(StartPattern()).exists(
        graph, ordering, open_right=True
    )
    assert empty_accepting.result is True
    assert empty_accepting.pending_from == (None,)
    intermediate = partial.exists(chain("ac"), ordering, open_right=True)
    assert intermediate.result is False
    assert intermediate.pending_from == (2,)


def test_open_right_settled_span_prefix_is_extension_invariant() -> None:
    ordering = TierOrder(q("seg"))
    a, b = atom("seg", "a"), atom("seg", "b")
    patterns = (
        a,
        SeqPattern((a, b)),
        RepeatPattern(a, 1, 2),
        SeqPattern((a, EndPattern())),
        SeqPattern((StartPattern(), a)),
    )
    for pattern in patterns:
        compiled = compile_pattern(pattern)
        for prefix in ("", "a", "b", "aa", "ab"):
            opened = compiled.spans(chain(prefix), ordering, open_right=True)
            cutoff = (
                len(prefix) + 1
                if opened.pending_from[0] is None
                else opened.pending_from[0]
            )
            settled = span_bounds(opened.result)
            for continuation in ("", "a", "b", "ab", "ba"):
                closed = compiled.spans(chain(prefix + continuation), ordering)
                before = [
                    bounds for bounds in span_bounds(closed) if bounds[0] < cutoff
                ]
                assert before == settled


def test_match_cli_pattern_text_equals_json_request(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    graph = ch_k()
    source = tmp_path / "graph.json"
    source.write_bytes(tiergraph.dump_bytes(graph))
    ordering = ContainerOrder(q("parts"), ItemsSelector(q("syl")))
    ordering_text = json.dumps(ordering_to_data(ordering))
    assert (
        main(
            [
                "match",
                str(source),
                "--pattern",
                "{class=vowel} / _ {class=nasal}",
                "--ordering",
                ordering_text,
                "--prefix",
                "ex",
                "focus",
            ]
        )
        == 0
    )
    text_result = json.loads(capsys.readouterr().out)
    pattern = SeqPattern((FocusPattern(atom("class", "vowel")), atom("class", "nasal")))
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "match": "focus",
                "ordering": ordering_to_data(ordering),
                "pattern": pattern_to_data(pattern),
            }
        ),
        encoding="utf-8",
    )
    assert main(["match", str(source), "--request", str(request)]) == 0
    assert json.loads(capsys.readouterr().out) == text_result


@pytest.mark.parametrize(
    "argument_order",
    ("options-graph-op", "graph-options-op", "graph-op-options"),
)
def test_match_cli_accepts_each_documented_argument_order(
    argument_order: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "graph.json"
    source.write_bytes(tiergraph.dump_bytes(chain("a")))
    options = [
        "--pattern",
        "{seg=a}",
        "--ordering",
        json.dumps(ordering_to_data(TierOrder(q("seg")))),
        "--prefix",
        "ex",
    ]
    parts = {
        "options-graph-op": [*options, str(source), "count"],
        "graph-options-op": [str(source), *options, "count"],
        "graph-op-options": [str(source), "count", *options],
    }

    assert main(["match", *parts[argument_order]]) == 0
    assert json.loads(capsys.readouterr().out) == {"count": 1}


def test_match_cli_refuses_incompatible_text_options(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "graph.json"
    request = tmp_path / "request.json"
    source.write_bytes(tiergraph.dump_bytes(chain("a")))
    request.write_text("{}", encoding="utf-8")

    assert (
        main(
            [
                "match",
                str(source),
                "--request",
                str(request),
                "--ordering",
                "{}",
            ]
        )
        == 1
    )
    assert "--request does not take text-pattern options" in capsys.readouterr().err

    assert main(["match", str(source), "--pattern", "."]) == 1
    assert "--pattern requires --ordering and an operation" in capsys.readouterr().err

    assert (
        main(
            [
                "match",
                str(source),
                "--pattern",
                ".",
                "--ordering",
                "{}",
                "--limit",
                "1",
                "count",
            ]
        )
        == 1
    )
    assert "--limit is available only for spans" in capsys.readouterr().err
