"""Work-budget coverage for output plans, selectors, and grammars."""

from __future__ import annotations

from collections.abc import Callable

import pytest

import tiergraph.selection as selection_module
from tests.conformance.selection import SelectionLawSuite
from tests.test_grammar import oracle
from tests.test_pathoutput import plan
from tiergraph import (
    AttributeDomain,
    AttributeSelector,
    BoundariesSelector,
    BoundaryRef,
    BoundarySelector,
    BudgetExhausted,
    DifferenceSelector,
    Emissions,
    GrammarInput,
    Graph,
    IntersectionSelector,
    ItemRef,
    ItemSelector,
    ItemsSelector,
    OutputPlan,
    SequenceSelector,
    TierSelector,
    TypeSelector,
    UnionSelector,
    WhereSelector,
    WorkBudget,
    WorkMeter,
    best,
    count,
    evaluate_selection,
    generate,
    lower_grammar,
    recognize,
    target_lattice,
)
from tiergraph.fold import FoldResult
from tiergraph.grammar import GenerationResult, ParseForest
from tiergraph.match import AtomPattern, FocusPattern, TierOrder
from tiergraph.pathplan import PathPlan
from tiergraph.predicate import And
from tiergraph.semiring import BOOLEAN


def _output_fixture() -> OutputPlan[object]:
    base = plan(
        {"root": 0.0, "left": 0.0, "right": 0.0},
        (("root", "left"), ("root", "right")),
        roots=("root",),
        counting=True,
    )
    emissions = Emissions.bind(base, {"left": ("a",), "right": ("b",)})
    return OutputPlan.prepare(base, emissions, (("a",), ("b",)))


def _stable(value: object) -> object:
    if isinstance(value, OutputPlan):
        return value.candidates, value.accepted, _stable(value.plan)
    if isinstance(value, ParseForest):
        return value.graph.to_data(), value.root.to_data(), value.collapsed
    if isinstance(value, FoldResult):
        return value.to_data(BOOLEAN)
    if isinstance(value, GenerationResult):
        return value.to_data()
    if isinstance(value, PathPlan):
        return value.items, value.children, value.roots
    return value


def _assert_metered(
    call: Callable[[WorkBudget | WorkMeter | None], object],
    *,
    operation: str,
) -> None:
    unbudgeted = _stable(call(None))
    first = WorkMeter(WorkBudget(steps=10_000_000))
    second = WorkMeter(WorkBudget(steps=10_000_000))
    assert _stable(call(first)) == unbudgeted
    assert _stable(call(second)) == unbudgeted
    assert first.spent == second.spent
    assert first.spent > 0
    with pytest.raises(BudgetExhausted) as caught:
        call(WorkBudget(steps=first.spent - 1))
    assert caught.value.operation == operation


def test_output_plan_prepare_is_deterministic_and_refuses_exhaustion() -> None:
    """Meter product construction without changing its unbudgeted topology."""
    base = plan(
        {"root": 0.0, "left": 0.0, "right": 0.0},
        (("root", "left"), ("root", "right")),
        roots=("root",),
        counting=True,
    )
    emissions = Emissions.bind(base, {"left": ("a",), "right": ("b",)})

    def prepare(budget: WorkBudget | WorkMeter | None) -> OutputPlan[object]:
        return OutputPlan.prepare(base, emissions, (("a",), ("b",)), budget=budget)

    _assert_metered(prepare, operation="outputplan.prepare")


@pytest.mark.parametrize(
    ("operation", "call"),
    (
        ("outputplan.masses", lambda output, budget: output.masses(budget=budget)),
        (
            "outputplan.conditioned",
            lambda output, budget: output.conditioned(0, budget=budget),
        ),
        (
            "outputplan.item_marginals",
            lambda output, budget: output.item_marginals(0, budget=budget),
        ),
    ),
)
def test_output_plan_runs_are_deterministic_and_refuse_exhaustion(
    operation: str,
    call: Callable[[OutputPlan[object], WorkBudget | WorkMeter | None], object],
) -> None:
    """Meter every aggregate or derived-plan run and bypass topology caches."""
    output = _output_fixture()
    _assert_metered(lambda budget: call(output, budget), operation=operation)


def test_output_plan_validates_before_installing_a_meter() -> None:
    """Keep candidate and vector refusals ahead of work exhaustion."""
    output = _output_fixture()
    with pytest.raises(ValueError, match="outside output plan candidates"):
        output.conditioned(-1, budget=WorkBudget(steps=1))
    with pytest.raises(ValueError, match="was given 1"):
        output.masses((1,), budget=WorkBudget(steps=1))

    base = output.base
    absent = OutputPlan.prepare(
        base, output.emissions, (("missing",),), budget=WorkBudget(steps=10_000)
    )
    with pytest.raises(ValueError, match="has no structurally accepted path"):
        absent.conditioned(0, budget=WorkBudget(steps=1))


def test_nested_sequence_selector_traversal_is_metered_deterministically() -> None:
    """Charge the selector tree and canonical set work around a sequence view."""
    laws = SelectionLawSuite(evaluate_selection)
    graph = laws.graph()
    sequence = SequenceSelector(
        TierOrder(laws.name("left")), FocusPattern(AtomPattern(And(())))
    )
    selector = IntersectionSelector(
        (UnionSelector((sequence, ItemsSelector(laws.name("right")))), sequence)
    )
    _assert_metered(
        lambda budget: evaluate_selection(graph, selector, budget=budget),
        operation="selection.evaluate",
    )
    wrapped = DifferenceSelector(
        WhereSelector(sequence, And(())), ItemsSelector(laws.name("right"))
    )
    assert evaluate_selection(graph, wrapped, budget=WorkBudget(steps=10_000)).nodes
    assert evaluate_selection(
        graph, ItemsSelector(laws.name("left")), budget=WorkBudget(steps=10_000)
    ).nodes


def test_unbudgeted_items_selection_does_not_add_traversal_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the unbudgeted path free of charging work and repeated item scans."""
    laws = SelectionLawSuite(evaluate_selection)
    graph = laws.graph()
    original = type(graph).canonical_items
    calls = 0

    def counted_items(self: Graph) -> tuple[ItemRef, ...]:
        nonlocal calls
        calls += 1
        return original(self)

    def unexpected_charge(steps: int) -> None:
        pytest.fail(f"unbudgeted selection tried to charge {steps} steps")

    monkeypatch.setattr(type(graph), "canonical_items", counted_items)
    monkeypatch.setattr(selection_module, "_charge_traversal", unexpected_charge)
    selected = evaluate_selection(graph, ItemsSelector(laws.name("left")))
    assert selected.nodes
    assert calls == 1


def test_traversal_charge_requires_an_active_meter() -> None:
    """Give inconsistent internal charging state a clear diagnostic."""
    token = selection_module._METER_TRAVERSAL.set(True)
    try:
        with pytest.raises(RuntimeError, match="requires an active work meter"):
            selection_module._charge_traversal(1)
    finally:
        selection_module._METER_TRAVERSAL.reset(token)


def test_budgeted_items_selection_has_exact_step_count() -> None:
    """Keep the item scan and canonicalization charge exactly accounted."""
    laws = SelectionLawSuite(evaluate_selection)
    graph = laws.graph()
    meter = WorkMeter(WorkBudget(steps=10_000))

    selected = evaluate_selection(graph, ItemsSelector(laws.name("left")), budget=meter)

    assert len(selected.nodes) == 2
    assert meter.spent == 10


def test_budgeted_selection_charges_every_leaf_scan() -> None:
    """Exercise each size-based leaf charge under an active selection meter."""
    laws = SelectionLawSuite(evaluate_selection)
    graph = laws.valued_graph()
    names = laws.valued_domain_names()
    tier = laws.name("valued")
    selectors: tuple[selection_module.Selector, ...] = (
        TierSelector(tier),
        TypeSelector(laws.name("valued-type")),
        ItemsSelector(tier),
        BoundariesSelector(tier),
        ItemSelector(ItemRef(tier, 0)),
        BoundarySelector(BoundaryRef(tier, 0)),
        *(AttributeSelector(names[domain], domain) for domain in AttributeDomain),
    )
    for selector in selectors:
        evaluate_selection(graph, selector, budget=WorkBudget(steps=10_000))


def test_nested_sequence_detection_covers_every_selector_shape() -> None:
    """Recognize a sequence through every compound selector shape."""
    laws = SelectionLawSuite(evaluate_selection)
    items = ItemsSelector(laws.name("left"))
    sequence = SequenceSelector(
        TierOrder(laws.name("left")), FocusPattern(AtomPattern(And(())))
    )
    assert selection_module._contains_sequence_selector(sequence)
    assert selection_module._contains_sequence_selector(
        WhereSelector(sequence, And(()))
    )
    assert selection_module._contains_sequence_selector(
        UnionSelector((items, sequence))
    )
    assert selection_module._contains_sequence_selector(
        IntersectionSelector((sequence, items))
    )
    assert selection_module._contains_sequence_selector(
        DifferenceSelector(items, sequence)
    )
    assert not selection_module._contains_sequence_selector(
        DifferenceSelector(items, items)
    )


def test_grammar_recognition_entries_are_metered_deterministically() -> None:
    """Meter chart construction and both Boolean recognition views."""
    lowered = lower_grammar(oracle())
    _assert_metered(
        lambda budget: recognize(lowered, ("written",), budget=budget),
        operation="grammar.recognize",
    )
    _assert_metered(
        lambda budget: recognize(
            lowered, ("written",), collapse_units=False, budget=budget
        ),
        operation="grammar.recognize",
    )
    forest = recognize(lowered, ("written",), collapse_units=False)
    _assert_metered(
        lambda budget: forest.recognized(budget=budget),
        operation="grammar.recognize",
    )
    _assert_metered(
        lambda budget: forest.result(budget=budget),
        operation="grammar.recognize",
    )


def test_grammar_count_entries_are_metered_deterministically() -> None:
    """Meter count on both the forest and convenience function surfaces."""
    forest = recognize(lower_grammar(oracle()), ("written",), collapse_units=False)
    _assert_metered(
        lambda budget: forest.count(budget=budget), operation="grammar.count"
    )
    _assert_metered(
        lambda budget: count(forest, budget=budget), operation="grammar.count"
    )


def test_grammar_best_entries_are_metered_deterministically() -> None:
    """Meter ranked derivation folds on both supported Python surfaces."""
    forest = recognize(lower_grammar(oracle()), ("written",), collapse_units=False)
    _assert_metered(
        lambda budget: forest.best(2, budget=budget), operation="grammar.best"
    )
    _assert_metered(
        lambda budget: best(forest, count=2, budget=budget),
        operation="grammar.best",
    )


def test_grammar_generate_entries_are_metered_deterministically() -> None:
    """Meter target generation including witness materialization."""
    lowered = lower_grammar(oracle())
    grammar_input = GrammarInput.from_symbols(("written",))
    forest = recognize(lowered, grammar_input, collapse_units=False)
    lattice = target_lattice(forest)
    _assert_metered(
        lambda budget: lattice.best(2, budget=budget),
        operation="grammar.generate",
    )
    _assert_metered(
        lambda budget: generate(lowered, grammar_input, count=2, budget=budget),
        operation="grammar.generate",
    )
    with pytest.raises(ValueError, match="generation count 0"):
        lattice.best(0, budget=WorkBudget(steps=1))
    with pytest.raises(ValueError, match="derivation count 0"):
        forest.best(0, budget=WorkBudget(steps=1))
