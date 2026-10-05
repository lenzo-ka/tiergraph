"""Declared work-budget contracts shared by matching and aggregation engines."""

from __future__ import annotations

import sys
from dataclasses import FrozenInstanceError

import pytest

import tiergraph.budget as budget_module
from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValuation,
    AttributeValue,
    BipartiteRelationDeclaration,
    BudgetExhausted,
    ChildCombination,
    Emissions,
    Exhaustion,
    FoldDeclaration,
    FoldTransition,
    Graph,
    Item,
    NamespaceDeclaration,
    QualifiedName,
    Refusal,
    RefusalStage,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    WorkBudget,
    WorkMeter,
    XsdType,
)
from tiergraph.match import (
    MAX_PATTERN_STATES,
    AtomPattern,
    CompiledPattern,
    Extent,
    FocusPattern,
    Pattern,
    RepeatPattern,
    SeqPattern,
    TierOrder,
    compile_pattern,
    parse_pattern,
)
from tiergraph.pathoutput import LatticeMatch, Unambiguous, match_lattice
from tiergraph.pathplan import PathPlan
from tiergraph.predicate import (
    And,
    Current,
    Matches,
    PredicateSyntax,
)
from tiergraph.semiring import COUNTING, CountingSemiring

NS = "urn:budget"
TIER = QualifiedName(NS, "items")
TYPE = QualifiedName(NS, "item")
MEMBERSHIP = QualifiedName(NS, "membership")
NEXT = QualifiedName(NS, "next")
WEIGHT = QualifiedName(NS, "weight")
SYNTAX = PredicateSyntax((NamespaceDeclaration("b", NS),), "b")


def graph(size: int = 4) -> Graph:
    """Return one short ordered tier for deterministic matching probes."""
    return Graph(
        (NamespaceDeclaration("b", NS),),
        (
            Tier(
                TierDeclaration(TIER, "Items"), tuple(Item(str(i)) for i in range(size))
            ),
        ),
        (SimpleRelationDeclaration(MEMBERSHIP, TIER, TYPE),),
    )


def compiled_atom() -> CompiledPattern:
    """Return the two-state wildcard pattern used by hand-count tests."""
    return compile_pattern(AtomPattern(And(())))


def fold_graph(size: int = 4) -> Graph:
    """Return a foldable tier whose dependency relation has no incidences."""
    return Graph(
        (NamespaceDeclaration("b", NS),),
        (
            Tier(
                TierDeclaration(TIER, "Items"),
                tuple(
                    Item(str(index), (AttributeValue(WEIGHT, XsdType.INTEGER, "1"),))
                    for index in range(size)
                ),
            ),
        ),
        (
            SimpleRelationDeclaration(MEMBERSHIP, TIER, TYPE),
            BipartiteRelationDeclaration(NEXT, TYPE, TYPE, acyclic=True),
        ),
        attribute_declarations=(
            AttributeDeclaration(WEIGHT, AttributeDomain.ITEM, XsdType.INTEGER),
        ),
    )


def lattice_match() -> LatticeMatch:
    """Return a four-root emitted lattice and one unambiguous optional atom."""
    document = fold_graph()
    declared = FoldDeclaration(
        "lattice-budget",
        document,
        AttributeValuation("weight", WEIGHT, (TIER,)),
        COUNTING,
        lambda _value, _label: 1,
        (FoldTransition(NEXT, ChildCombination.OR),),
    )
    plan = PathPlan.prepare(declared)
    emissions = Emissions.bind(plan, {str(index): ("x",) for index in range(4)})
    optional = compile_pattern(RepeatPattern(AtomPattern(And(())), 0, 1))
    return match_lattice(emissions, optional)


@pytest.mark.parametrize(
    "arguments",
    (
        {},
        {"steps": True},
        {"steps": 0},
        {"steps": -1},
        {"seconds": True},
        {"seconds": 0},
        {"seconds": float("nan")},
        {"seconds": float("inf")},
    ),
)
def test_work_budget_validation(arguments: dict[str, object]) -> None:
    """Reject empty, Boolean, nonpositive and nonfinite declarations."""
    with pytest.raises(ValueError):
        WorkBudget(**arguments)  # type: ignore[arg-type]


def test_budget_exhausted_is_typed_refusal() -> None:
    """Expose stable refusal fields and the standard empty ``also`` tuple."""
    declared = WorkBudget(steps=3)
    error = BudgetExhausted("pattern.exists", Exhaustion.STEPS, 4, declared)
    assert isinstance(error, (Refusal, ValueError))
    assert error.stage is RefusalStage.SEMANTICS
    assert error.also == ()
    assert (error.operation, error.exhaustion, error.spent, error.budget) == (
        "pattern.exists",
        Exhaustion.STEPS,
        4,
        declared,
    )


def test_meter_is_shared_and_spent_is_read_only() -> None:
    """Accumulate successful calls in one caller-owned meter."""
    pattern = compiled_atom()
    document = graph(2)
    meter = WorkMeter(WorkBudget(steps=100))
    assert pattern.exists(document, TierOrder(TIER), budget=meter)
    first = meter.spent
    assert first == 7  # truth 2; closure/step/closure 1+1+1; next closure 2
    assert pattern.exists(document, TierOrder(TIER), budget=meter)
    assert meter.spent == 2 * first
    with pytest.raises((AttributeError, FrozenInstanceError)):
        meter.spent = 0  # type: ignore[misc]


def test_exact_threshold_and_refusal_fields() -> None:
    """A limit equal to total work succeeds; one less crosses it."""
    pattern = compiled_atom()
    document = graph(2)
    assert pattern.exists(document, TierOrder(TIER), budget=WorkBudget(steps=7))
    with pytest.raises(BudgetExhausted) as caught:
        pattern.exists(document, TierOrder(TIER), budget=WorkBudget(steps=6))
    assert caught.value.operation == "pattern.exists"
    assert caught.value.exhaustion is Exhaustion.STEPS
    assert caught.value.spent == 7


def test_no_budget_never_reads_clock_or_charges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the unbudgeted hot path independent of meter machinery."""
    monkeypatch.setattr(
        budget_module, "_clock", lambda: (_ for _ in ()).throw(AssertionError())
    )
    monkeypatch.setattr(
        budget_module._Meter,
        "charge",
        lambda *_args: (_ for _ in ()).throw(AssertionError()),
    )
    assert compiled_atom().exists(graph(2), TierOrder(TIER))


def test_spans_return_only_a_nonempty_outermost_prefix() -> None:
    """Turn outermost step exhaustion into a marked ordered witness prefix."""
    pattern = compiled_atom()
    document = graph(4)
    exhaustive = pattern.spans(document, TierOrder(TIER))
    cut = pattern.spans(document, TierOrder(TIER), budget=WorkBudget(steps=15))
    assert cut.extent is Extent.CUT_AT_BUDGET
    assert cut.matches
    assert cut.matches == exhaustive.matches[: len(cut.matches)]
    with pytest.raises(BudgetExhausted):
        pattern.spans(document, TierOrder(TIER), budget=WorkBudget(steps=1))


def test_count_does_not_turn_exhaustion_into_a_lower_bound() -> None:
    """Refuse aggregate exhaustion even after accepting spans were seen."""
    with pytest.raises(BudgetExhausted):
        compiled_atom().count(graph(4), TierOrder(TIER), budget=WorkBudget(steps=15))


def test_deadline_always_refuses_at_installation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh relative deadline is fixed at construction and checked on install."""
    readings = iter((0.0, 2.0))
    monkeypatch.setattr(budget_module, "_clock", lambda: next(readings))
    with pytest.raises(BudgetExhausted) as caught:
        compiled_atom().exists(
            graph(1), TierOrder(TIER), budget=WorkBudget(seconds=1.0)
        )
    assert caught.value.exhaustion is Exhaustion.DEADLINE
    assert caught.value.spent == 0


@pytest.mark.parametrize("child_budget", (None, WorkBudget(steps=1000)))
def test_spans_never_cut_inside_a_budgeted_fold_lift(
    child_budget: WorkBudget | None,
) -> None:
    """Refuse ambient and explicit-child exhaustion before semiring aggregation."""
    document = fold_graph()
    pattern = compiled_atom()

    def lift(_value: object, _label: str) -> int:
        matches = pattern.spans(document, TierOrder(TIER), budget=child_budget)
        assert matches.extent is not Extent.CUT_AT_BUDGET
        return len(matches.matches)

    declared = FoldDeclaration(
        "nested-spans",
        document,
        AttributeValuation("weight", WEIGHT, (TIER,)),
        COUNTING,
        lift,
        (FoldTransition(NEXT, ChildCombination.OR),),
    )
    with pytest.raises(BudgetExhausted) as caught:
        declared.run(budget=WorkBudget(steps=5))
    assert caught.value.operation == "fold.run"
    assert caught.value.exhaustion is Exhaustion.STEPS


def test_spans_never_cut_inside_an_unbudgeted_fold_lift() -> None:
    """Refuse a child budget before its prefix can reach an unmetered aggregate."""
    document = fold_graph(40)
    pattern = compile_pattern(RepeatPattern(AtomPattern(And(())), 1, None))

    def lift(_value: object, _label: str) -> int:
        matches = pattern.spans(document, TierOrder(TIER), budget=WorkBudget(steps=60))
        return len(matches.matches)

    declared = FoldDeclaration(
        "unbudgeted-nested-spans",
        document,
        AttributeValuation("weight", WEIGHT, (TIER,)),
        COUNTING,
        lift,
        (FoldTransition(NEXT, ChildCombination.OR),),
    )
    with pytest.raises(BudgetExhausted) as caught:
        declared.run()
    assert caught.value.operation == "pattern.spans"
    assert caught.value.exhaustion is Exhaustion.STEPS


@pytest.mark.parametrize("view", ("evaluate", "marginals"))
def test_spans_never_cut_inside_an_unbudgeted_pathplan_view(view: str) -> None:
    """Refuse a child prefix before a plan can use it in carrier arithmetic."""
    document = fold_graph(40)
    pattern = compile_pattern(RepeatPattern(AtomPattern(And(())), 1, None))

    class NestedSpansCounting(CountingSemiring):
        def multiply(self, left: int, right: int, /) -> int:
            pattern.spans(document, TierOrder(TIER), budget=WorkBudget(steps=60))
            return super().multiply(left, right)

    declared = FoldDeclaration(
        "plan-nested-spans",
        document,
        AttributeValuation("weight", WEIGHT, (TIER,)),
        NestedSpansCounting(),
        lambda _value, _label: 1,
        (FoldTransition(NEXT, ChildCombination.OR),),
    )
    plan = PathPlan.prepare(declared)
    with pytest.raises(BudgetExhausted, match="pattern.spans"):
        getattr(plan, view)()


def test_spans_never_cut_while_pathplan_prepares_lifted_values() -> None:
    """Refuse a child prefix before a plan can cache it as a carrier value."""
    document = fold_graph(40)
    pattern = compile_pattern(RepeatPattern(AtomPattern(And(())), 1, None))

    def lift(_value: object, _label: str) -> int:
        matches = pattern.spans(document, TierOrder(TIER), budget=WorkBudget(steps=60))
        return len(matches.matches)

    declared = FoldDeclaration(
        "plan-lift-nested-spans",
        document,
        AttributeValuation("weight", WEIGHT, (TIER,)),
        COUNTING,
        lift,
        (FoldTransition(NEXT, ChildCombination.OR),),
    )
    with pytest.raises(BudgetExhausted, match="pattern.spans"):
        PathPlan.prepare(declared)


def test_lattice_count_charges_epsilon_closures_and_bypasses_caches() -> None:
    """Pin closure-sensitive counting work and history-independent cache bypass."""
    matched = lattice_match()
    meter = WorkMeter(WorkBudget(steps=49))
    assert matched.count(Unambiguous(), budget=meter) == 4
    # Ambiguity setup is 9 steps; its terminal and transition closures are 24;
    # four propagated root entries cost 4; token closures cost 12: total 49.
    assert meter.spent == 49
    assert not matched._ambiguity_cache
    with pytest.raises(BudgetExhausted):
        matched.count(Unambiguous(), budget=WorkBudget(steps=48))

    assert matched.exists()
    assert matched._boolean_cache
    warmed = WorkMeter(WorkBudget(steps=48))
    assert matched.exists(budget=warmed)
    assert warmed.spent == 48
    assert len(matched._boolean_cache) == 1


def test_pattern_text_nesting_has_typed_syntax_refusal(
    request: pytest.FixtureRequest,
) -> None:
    """Parse 64 groups and refuse the 65th without leaking RecursionError."""
    old_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(1000)
    request.addfinalizer(lambda: sys.setrecursionlimit(old_limit))
    parse_pattern("(" * 64 + "." + ")?" * 64, SYNTAX)
    with pytest.raises(Refusal) as caught:
        parse_pattern("(" * 65 + "." + ")?" * 65, SYNTAX)
    assert caught.value.stage is RefusalStage.SYNTAX
    assert "offset 64" in str(caught.value)


def test_ast_depth_refusal_wins_from_a_deep_caller(
    request: pytest.FixtureRequest,
) -> None:
    """Refuse a 350-deep AST without recursing through its focus walk."""
    old_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(1000)
    request.addfinalizer(lambda: sys.setrecursionlimit(old_limit))
    pattern: Pattern = AtomPattern(And(()))
    for _ in range(349):
        pattern = RepeatPattern(pattern, 1, 1)

    def nested_call(frames: int) -> CompiledPattern:
        return nested_call(frames - 1) if frames else compile_pattern(pattern)

    with pytest.raises(Refusal, match="pattern nests deeper than 256 levels"):
        nested_call(300)


def test_pattern_state_ceiling_precedes_nfa_construction() -> None:
    """Refuse arithmetic state growth while positions remain below their cap."""
    body: Pattern = AtomPattern(And(()))
    for _ in range(50):
        body = RepeatPattern(body, 1, 1)
    pattern = RepeatPattern(body, 10_000, 10_000)
    with pytest.raises(Refusal, match="1020002 NFA states"):
        compile_pattern(pattern)
    assert MAX_PATTERN_STATES == 1_000_000


def test_two_focus_refusal_precedes_the_ast_depth_guard() -> None:
    """Preserve the existing two-focus refusal on a 261-deep shape."""
    atom = AtomPattern(And(()))
    pattern: Pattern = SeqPattern((FocusPattern(atom), FocusPattern(atom)))
    for _ in range(258):
        pattern = RepeatPattern(pattern, 1, 1)
    with pytest.raises(Refusal, match="2 focus marks"):
        compile_pattern(pattern)


def test_predicate_regex_state_ceiling() -> None:
    """Refuse the analogous 1,020,002-state predicate NFA before allocation."""
    regex = "(" * 51 + "a" + "){1}" * 50 + "){10000}"
    with pytest.raises(ValueError, match="1020002 NFA states"):
        Matches(Current(), regex)
