"""Coverage of budget charging branches across all bounded engines."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import cast

import pytest

import tiergraph.budget as budget_module
from tests.test_budget import compiled_atom, graph, lattice_match
from tests.test_fold import declaration as fold_declaration
from tests.test_fold import ranked_tie
from tests.test_pathplan import COST, LATTICE_EDGES, LATTICE_WEIGHTS, path_lift
from tests.test_pathplan import declare as plan_declaration
from tests.test_pathplan import lattice as plan_lattice
from tests.test_spans import offset_graph, offsets, q
from tests.test_traversal import polyadic_graph, polyadic_selection
from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    BudgetExhausted,
    ChildCombination,
    Emissions,
    FoldDeclaration,
    FoldExactness,
    Graph,
    Item,
    ItemRef,
    JsonAttributeValue,
    JsonType,
    NamespaceDeclaration,
    Node,
    NodeKind,
    NodeSet,
    QualifiedName,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    WorkBudget,
    WorkMeter,
)
from tiergraph.match import (
    AtomPattern,
    Extent,
    FocusPattern,
    Pattern,
    RepeatPattern,
    TierOrder,
    compile_pattern,
    span_pairs,
)
from tiergraph.pathoutput import Determinize, Unambiguous, match_lattice
from tiergraph.pathplan import PathPlan
from tiergraph.predicate import (
    And,
    Cell,
    Current,
    Elements,
    IntervalRelation,
    Matches,
    Quantifier,
    Spans,
    compile_predicate,
)
from tiergraph.selection import ItemsSelector, evaluate_selection
from tiergraph.semiring import DECIMAL_TROPICAL, PATH, Semiring
from tiergraph.traversal import WalkDirection, relation_image


def test_meter_rejects_wrong_type() -> None:
    """Reject a non-budget meter declaration."""
    with pytest.raises(TypeError, match="must be a WorkBudget"):
        WorkMeter(object())  # type: ignore[arg-type]


def test_meter_rejects_bad_entry() -> None:
    """Reject an invalid meter entry object."""
    with pytest.raises(TypeError, match="WorkBudget, WorkMeter, or None"):
        with budget_module._metered(object(), "bad"):  # type: ignore[arg-type]
            pass


def test_meter_rejects_negative_charge() -> None:
    shared = WorkMeter(WorkBudget(steps=10))
    with budget_module._metered(shared, "negative") as metered:
        assert metered.meter is not None
        with pytest.raises(ValueError, match="nonnegative"):
            metered.meter.charge(-1)


def test_nested_and_reused_meters_charge_once() -> None:
    """Charge child and ancestor meters without duplicating a reused meter."""
    shared = WorkMeter(WorkBudget(steps=20_000))
    with budget_module._metered(shared, "outer") as outer:
        assert outer.owns_outermost
        with budget_module._metered(WorkBudget(steps=20_000), "child") as child:
            assert child.meter is not None
            child.meter.charge(2)
            with budget_module._metered(shared, "reused") as reused:
                assert reused.meter is child.meter
                reused.meter.charge(3)
    assert shared.spent == 5
    duplicate = budget_module._Meter(
        shared, "inner", budget_module._Meter(shared, "outer", None)
    )
    duplicate.charge(1)
    assert shared.spent == 6


def test_meter_fast_paths_and_nested_refusals() -> None:
    """Cover root charge specialization and duplicate-meter discovery."""
    root = WorkMeter(WorkBudget(steps=10))
    assert root.budget.steps == 10
    timed = WorkMeter(WorkBudget(steps=1, seconds=1.0))
    with budget_module._metered(timed, "timed") as metered:
        assert metered.meter is not None
        with pytest.raises(ValueError, match="nonnegative"):
            metered.meter.charge(-1)
        with pytest.raises(BudgetExhausted):
            metered.meter.charge(2)

    child = WorkMeter(WorkBudget(steps=10))
    with budget_module._metered(root, "root"):
        with budget_module._metered(child, "child"):
            with budget_module._metered(child, "reused-child") as reused:
                assert reused.meter is not None
                with pytest.raises(ValueError, match="nonnegative"):
                    reused.meter.charge(-1)

    with budget_module._unchecked_charging(1) as unchecked:
        assert unchecked is None
    deadline = WorkMeter(WorkBudget(seconds=1.0))
    with budget_module._metered(deadline, "deadline"):
        with budget_module._unchecked_charging(1) as unchecked:
            assert unchecked is None


def test_periodic_deadline_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check deadlines at installation and periodic crossings."""
    readings = iter((0.0, 0.0, 0.5, 2.0))
    monkeypatch.setattr(budget_module, "_clock", lambda: next(readings))
    deadline = WorkMeter(WorkBudget(seconds=1.0))
    with pytest.raises(BudgetExhausted) as caught:
        with budget_module._metered(deadline, "periodic") as metered:
            assert metered.meter is not None
            metered.meter.charge(4095)
            metered.meter.charge(1)
            metered.meter.charge(4096)
    assert caught.value.spent == 8192


def test_large_deadline_crossing_advances_checkpoint_directly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seat the next deadline check after one large charge without catch-up work."""
    readings = iter((0.0, 0.0, 0.5))
    monkeypatch.setattr(budget_module, "_clock", lambda: next(readings))
    deadline = WorkMeter(WorkBudget(seconds=1.0))
    with budget_module._metered(deadline, "large-crossing") as metered:
        assert metered.meter is not None
        metered.meter.charge(100_000_000)
    assert deadline._next_deadline_check == 100_003_840


def test_budgeted_focus_and_depth_guard() -> None:
    """Meter focus and refuse excessive AST depth."""
    document = graph(3)
    focused = compile_pattern(FocusPattern(AtomPattern(And(()))))
    selected = focused.focus(
        document,
        TierOrder(document.tiers[0].declaration.name),
        budget=WorkBudget(steps=10_000),
    )
    assert len(selected.nodes) == 3
    assert (
        compiled_atom()
        .exists(
            document,
            TierOrder(document.tiers[0].declaration.name),
            open_right=True,
            budget=WorkBudget(steps=100_000),
        )
        .result
    )
    nested: Pattern = AtomPattern(And(()))
    for _ in range(256):
        nested = RepeatPattern(nested, 1, 1)
    with pytest.raises(ValueError, match="deeper than 256"):
        compile_pattern(nested)


def test_budgeted_regex_and_elements_atoms() -> None:
    """Charge regex states and array elements."""
    source = offset_graph()
    candidates = evaluate_selection(source, ItemsSelector(q("det")))
    regex = compile_predicate(Matches(Cell(q("type")), "date.*")).bind(source)
    assert regex.select(candidates, budget=WorkBudget(steps=10_000)).nodes

    namespace = "urn:array"
    tier_name = QualifiedName(namespace, "items")
    item_type = QualifiedName(namespace, "item")
    membership = QualifiedName(namespace, "membership")
    array = QualifiedName(namespace, "array")
    array_graph = Graph(
        (NamespaceDeclaration("a", namespace),),
        (
            Tier(
                TierDeclaration(tier_name, "Items"),
                (Item("array", (JsonAttributeValue(array, ["x", "y"]),)),),
            ),
        ),
        (SimpleRelationDeclaration(membership, tier_name, item_type),),
        attribute_declarations=(
            AttributeDeclaration(array, AttributeDomain.ITEM, JsonType.JSON),
        ),
    )
    elements = compile_predicate(
        Elements(Cell(array), Quantifier.ANY, Matches(Current(), "x"))
    ).bind(array_graph)
    node = Node(NodeKind.ITEM, ItemRef(tier_name, 0))
    meter = WorkMeter(WorkBudget(steps=100))
    assert elements.holds(node, budget=meter)
    assert meter.spent >= 2


def test_budgeted_spans_atom_and_pair_indexes() -> None:
    """Charge offset records and indexed and product interval candidates."""
    source = offset_graph(extra_gold=True)
    candidates = evaluate_selection(source, ItemsSelector(q("det")))
    predicate = Spans(
        offsets(), IntervalRelation.EQUAL, Quantifier.ANY, q("gold"), And(())
    )
    bound = compile_predicate(predicate).bind(source)
    assert bound.select(candidates, budget=WorkBudget(steps=100_000)).nodes
    left = ItemsSelector(q("det"))
    right = ItemsSelector(q("gold"))
    profile = offsets()
    equal = span_pairs(
        source,
        left,
        right,
        IntervalRelation.EQUAL,
        profile,
        budget=WorkBudget(steps=100_000),
    )
    within = span_pairs(
        source,
        left,
        right,
        IntervalRelation.PROPER_WITHIN,
        profile,
        budget=WorkBudget(steps=100_000),
    )
    assert equal.pairs
    assert within.extent is Extent.EXHAUSTIVE
    meter = WorkMeter(WorkBudget(steps=100_000))
    span_pairs(source, left, right, IntervalRelation.EQUAL, profile, budget=meter)
    cut = span_pairs(
        source,
        left,
        right,
        IntervalRelation.EQUAL,
        profile,
        budget=WorkBudget(steps=21),
    )
    assert cut.extent is Extent.CUT_AT_BUDGET
    with pytest.raises(BudgetExhausted):
        span_pairs(
            source,
            left,
            right,
            IntervalRelation.EQUAL,
            profile,
            budget=WorkBudget(steps=20),
        )


def test_budgeted_relation_images_cover_both_shapes() -> None:
    """Charge full bipartite and polyadic incidence scans."""
    declared = fold_declaration()
    document = declared.graph
    source = NodeSet(document, (Node(NodeKind.ITEM, document.canonical_items()[0]),))
    relation = declared.transitions[0].relation
    with budget_module._metered(WorkBudget(steps=10_000), "bipartite"):
        relation_image(source, relation, WalkDirection.FORWARD)

    polyadic = polyadic_graph()
    with budget_module._metered(WorkBudget(steps=10_000), "polyadic"):
        result = relation_image(
            polyadic_selection(polyadic, 0),
            polyadic.relation_declarations[0].name,
            WalkDirection.FORWARD,
        )
    assert result.nodes


def test_budgeted_lattice_views_cover_reverse_subset_and_path_count() -> None:
    """Meter lattice projection, both count policies and universal views."""
    matched = lattice_match()
    ample = WorkBudget(steps=100_000)
    assert matched.on_accepting_path(budget=ample).nodes
    assert matched.count(Determinize(64), budget=ample) == 4
    assert matched.all_paths(Unambiguous(), budget=ample)
    assert matched.all_paths(Determinize(64), budget=ample)
    assert not matched._subset_cache


def test_proven_budget_headroom_uses_exact_fast_charging() -> None:
    """Exercise fast charges only where a conservative bound proves headroom."""
    ample = WorkBudget(steps=1_000_000_000)
    pattern = compiled_atom()
    document = graph(3)
    assert pattern.spans(
        document, TierOrder(document.tiers[0].declaration.name), budget=ample
    )
    assert (
        pattern.count(
            document, TierOrder(document.tiers[0].declaration.name), budget=ample
        )
        == 3
    )

    matched = lattice_match()
    assert matched.count(Unambiguous(), budget=ample) == 4
    assert matched.count(Determinize(64), budget=ample) == 4
    assert matched.count(Determinize(64), budget=WorkBudget(steps=100)) == 4


def test_budgeted_fold_exactness_and_ranked_products(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reach fold checkpoints, exactness and both ranked products."""
    declared = replace(fold_declaration(), exactness=FoldExactness.DISTRIBUTIVE)
    ample = WorkBudget(steps=10_000_000)
    assert declared.run(budget=ample).value
    certificate = declared.check_exactness(derivation_budget=100_000, budget=ample)
    assert certificate.compared
    plain = fold_declaration(semiring=cast(Semiring[object], DECIMAL_TROPICAL))
    assert plain.run(budget=ample).value is not None
    transition = replace(declared.transitions[0], combination=ChildCombination.AND)
    conjunctive = replace(declared, transitions=(transition,))
    conjunctive.check_exactness(derivation_budget=100_000, budget=ample)
    ranked = ranked_tie()
    assert ranked.run(budget=ample).ranked_witnesses
    with monkeypatch.context() as eager:
        eager.setattr(
            FoldDeclaration,
            "_lazy_ranked_product",
            lambda self, left, right, witness_operations, ranked_additions: None,
        )
        assert ranked.run(budget=ample).ranked_witnesses


def test_budgeted_pathplan_charges_operations_and_path_values() -> None:
    """Meter compiled schedules and retained built-in path values."""
    lattice = plan_lattice(LATTICE_WEIGHTS, LATTICE_EDGES)
    declared = plan_declaration(lattice, PATH, attribute=COST, lift=path_lift)
    plan = PathPlan.prepare(declared)
    ample = WorkBudget(steps=10_000_000)
    evaluated = plan.evaluate(budget=ample)
    marginals = plan.marginals(budget=ample)
    assert evaluated.value == marginals.total
    assert isinstance(evaluated.value[0], Decimal)
    emissions = Emissions.bind(plan, dict.fromkeys(plan.labels, ("x",)))
    pattern = compile_pattern(RepeatPattern(AtomPattern(And(())), 1, None))
    matched = match_lattice(emissions, pattern)
    assert matched.exists(budget=ample)
    assert matched.on_accepting_path(budget=ample).nodes
    assert matched.count(Determinize(64), budget=ample) > 0
