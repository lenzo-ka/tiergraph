"""A path plan reproduces the fold it compiles and adds the outside pass."""

from __future__ import annotations

import math
from collections.abc import Callable
from decimal import Decimal
from typing import Any, cast

import pytest

from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValuation,
    AttributeValue,
    BipartiteRelationDeclaration,
    ChildCombination,
    FoldDeclaration,
    FoldResult,
    FoldTransition,
    Graph,
    Item,
    ItemRef,
    NamespaceDeclaration,
    QualifiedName,
    RelationInstance,
    SimpleRelationDeclaration,
    TiePolicy,
    Tier,
    TierDeclaration,
    XsdType,
)
from tiergraph.pathplan import AlgebraOrder, PathMarginals, PathPlan
from tiergraph.semiring import (
    ARCTIC,
    COUNTING,
    DECIMAL_TROPICAL,
    LOG_PROBABILITY,
    PATH,
    TROPICAL,
    PathValue,
    Semiring,
)

NS = "https://example.com/plan"
NODES = QualifiedName(NS, "nodes")
NODE = QualifiedName(NS, "node")
NEXT = QualifiedName(NS, "next")
OTHER = QualifiedName(NS, "other")
WEIGHT = QualifiedName(NS, "weight")
COST = QualifiedName(NS, "cost")
Edges = tuple[tuple[str, str], ...]

# States and arcs of one lattice as items of one tier: ``s`` reaches ``f``
# directly through ``merged`` and through ``first``, ``m``, ``silent``; ``twin``
# is a parallel alternative to ``first``; ``dead`` is a sink valued at the
# zero; ``u`` is reached by no root.
LATTICE_WEIGHTS: dict[str, float] = {
    "s": 0.0,
    "m": 0.0,
    "f": 0.0,
    "dead": -math.inf,
    "u": 0.0,
    "merged": math.log(0.4),
    "first": math.log(0.3),
    "silent": math.log(0.5),
    "twin": math.log(0.3),
    "todead": math.log(0.2),
    "fromu": 0.0,
}
LATTICE_EDGES: Edges = (
    ("s", "merged"),
    ("merged", "f"),
    ("s", "first"),
    ("first", "m"),
    ("m", "silent"),
    ("silent", "f"),
    ("s", "twin"),
    ("twin", "m"),
    ("s", "todead"),
    ("todead", "dead"),
    ("u", "fromu"),
    ("fromu", "f"),
)


def lexical(value: float) -> str:
    """Spell a log weight as an XSD double."""
    return "-INF" if value == -math.inf else repr(value)


def lattice(
    weights: dict[str, float],
    edges: Edges,
    *,
    acyclic: bool = True,
    durable: bool = True,
) -> Graph:
    """Build one tier of valued items joined by the ``next`` relation."""
    labels = list(weights)
    items = tuple(
        Item(
            label if durable else None,
            (
                AttributeValue(WEIGHT, XsdType.DOUBLE, lexical(value)),
                AttributeValue(COST, XsdType.DECIMAL, "1"),
            ),
        )
        for label, value in weights.items()
    )
    refs = {label: ItemRef(NODES, index) for index, label in enumerate(labels)}
    return Graph(
        (NamespaceDeclaration("paths", NS),),
        (Tier(TierDeclaration(NODES, "Nodes"), items),),
        (
            SimpleRelationDeclaration(QualifiedName(NS, "membership"), NODES, NODE),
            BipartiteRelationDeclaration(NEXT, NODE, NODE, acyclic=acyclic),
            BipartiteRelationDeclaration(OTHER, NODE, NODE),
        ),
        (
            *(RelationInstance(NEXT, refs[left], refs[right]) for left, right in edges),
            # A relation the fold does not read, so the plan has to skip it.
            *(
                (RelationInstance(OTHER, refs[labels[0]], refs[labels[-1]]),)
                if len(labels) > 1
                else ()
            ),
        ),
        (
            AttributeDeclaration(WEIGHT, AttributeDomain.ITEM, XsdType.DOUBLE),
            AttributeDeclaration(COST, AttributeDomain.ITEM, XsdType.DECIMAL),
        ),
    )


def ref(graph: Graph, label: str) -> ItemRef:
    """Locate an item by its durable identity."""
    tier = graph.tiers[0]
    return ItemRef(
        NODES, next(i for i, item in enumerate(tier.items) if item.durable_id == label)
    )


def value_lift(value: object, _label: str) -> Any:
    """Embed the read value as it is."""
    return value


def negated_lift(value: object, _label: str) -> float:
    """Embed a log weight as a tropical cost: the zero maps to the zero."""
    weight = cast(float, value)
    return math.inf if weight == -math.inf else -weight


def path_lift(value: object, label: str) -> PathValue:
    """Embed a decimal cost with its label as a path witness."""
    return (cast(Decimal, value), ((label,),))


def declare(
    graph: Graph,
    semiring: Semiring[Any],
    *,
    lift: Callable[[object, str], Any] = value_lift,
    roots: tuple[str, ...] = ("s",),
    attribute: QualifiedName = WEIGHT,
    name: str = "lattice",
    **options: Any,
) -> FoldDeclaration[Any]:
    """Declare a fold over the ``next`` relation with the given options."""
    return FoldDeclaration(
        name,
        graph,
        AttributeValuation("value", attribute, (NODES,)),
        semiring,
        lift,
        (FoldTransition(NEXT, ChildCombination.OR),),
        roots=tuple(ref(graph, label) for label in roots),
        **options,
    )


def decimal_order(left: Any, right: Any) -> int:
    """Order decimal or path carriers by their scalar."""
    left_value = left[0] if isinstance(left, tuple) else left
    right_value = right[0] if isinstance(right, tuple) else right
    return int(left_value > right_value) - int(left_value < right_value)


def close(left: Any, right: Any) -> bool:
    """Compare carrier values, allowing rounding between float schedules."""
    if isinstance(left, tuple) and isinstance(right, tuple):
        return len(left) == len(right) and all(
            close(a, b) for a, b in zip(left, right, strict=True)
        )
    if isinstance(left, float) and isinstance(right, float):
        return left == right or math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-12)
    return bool(left == right)


def assert_same_result(expected: FoldResult[Any], actual: FoldResult[Any]) -> None:
    """Hold a plan's result to the fold's, value by value."""
    assert close(expected.value, actual.value)
    assert len(expected.values) == len(actual.values)
    for (state, value), (other_state, other_value) in zip(
        expected.values, actual.values, strict=True
    ):
        assert state == other_state
        assert close(value, other_value)
    assert expected.roots == actual.roots
    assert expected.provenance == actual.provenance
    assert expected.truncated == actual.truncated
    assert expected.cost == actual.cost
    assert expected.ranked_witnesses is None and actual.ranked_witnesses is None


def declarations(graph: Graph) -> dict[str, FoldDeclaration[Any]]:
    """Every carrier and witness shape the plan is held to."""
    return {
        "log": declare(graph, LOG_PROBABILITY),
        "arctic": declare(graph, ARCTIC),
        "tropical": declare(graph, TROPICAL, lift=negated_lift),
        "arctic-first": declare(
            graph,
            ARCTIC,
            witness_order=AlgebraOrder(ARCTIC),
            tie_policy=TiePolicy.CHOOSE_FIRST,
        ),
        "arctic-all": declare(
            graph,
            ARCTIC,
            witness_order=AlgebraOrder(ARCTIC),
            tie_policy=TiePolicy.ALL,
            output_cap=2,
        ),
        "tropical-first": declare(
            graph,
            TROPICAL,
            lift=negated_lift,
            witness_order=AlgebraOrder(TROPICAL),
            tie_policy=TiePolicy.CHOOSE_FIRST,
        ),
        "log-ordered": declare(
            graph,
            LOG_PROBABILITY,
            witness_order=lambda left, right: (left < right) - (left > right),
            tie_policy=TiePolicy.ALL,
            output_cap=3,
        ),
        "counting": declare(
            graph, COUNTING, attribute=COST, lift=lambda _value, _label: 1
        ),
        "decimal-all": declare(
            graph,
            DECIMAL_TROPICAL,
            attribute=COST,
            witness_order=decimal_order,
            tie_policy=TiePolicy.ALL,
            output_cap=2,
        ),
        "path-first": declare(
            graph,
            PATH,
            attribute=COST,
            lift=path_lift,
            witness_order=decimal_order,
            tie_policy=TiePolicy.CHOOSE_FIRST,
        ),
    }


@pytest.mark.parametrize(
    "kind", list(declarations(lattice(LATTICE_WEIGHTS, LATTICE_EDGES)))
)
@pytest.mark.parametrize("roots", [("s",), ("s", "u"), ("m", "s")])
def test_the_plan_reproduces_the_fold(kind: str, roots: tuple[str, ...]) -> None:
    """``evaluate`` returns what ``run`` returns, for every carrier and witness shape."""
    graph = lattice(LATTICE_WEIGHTS, LATTICE_EDGES)
    declaration = declarations(graph)[kind]
    declaration = FoldDeclaration(
        declaration.name,
        graph,
        declaration.valuation,
        declaration.semiring,
        declaration.lift,
        declaration.transitions,
        roots=tuple(ref(graph, label) for label in roots),
        witness_order=declaration.witness_order,
        tie_policy=declaration.tie_policy,
        output_cap=declaration.output_cap,
    )
    plan = PathPlan.prepare(declaration)
    assert_same_result(declaration.run(), plan.evaluate())
    assert plan.roots == tuple(plan.index(ref(graph, label)) for label in roots)


def test_the_plan_exposes_its_binding() -> None:
    """Items, labels, values, incidence, and order are the plan's contract."""
    graph = lattice(LATTICE_WEIGHTS, LATTICE_EDGES)
    plan = PathPlan.prepare(declare(graph, LOG_PROBABILITY))
    assert plan.labels == tuple(LATTICE_WEIGHTS)
    assert plan.items == tuple(ItemRef(NODES, i) for i in range(len(LATTICE_WEIGHTS)))
    assert plan.values == tuple(LATTICE_WEIGHTS.values())
    assert plan.index(ref(graph, "silent")) == plan.labels.index("silent")
    at = plan.labels.index
    assert plan.children[at("s")] == (
        at("merged"),
        at("first"),
        at("twin"),
        at("todead"),
    )
    assert plan.parents[at("m")] == (at("first"), at("twin"))
    assert plan.children[at("f")] == ()
    assert plan.roots == (at("s"),)
    for parent, links in enumerate(plan.children):
        for child in links:
            assert plan.order.index(child) < plan.order.index(parent)
    with pytest.raises(ValueError, match="has no item"):
        plan.index(ItemRef(NODES, 99))


def test_new_values_reuse_the_topology() -> None:
    """A plan under new values equals a fresh fold over the re-valued graph."""
    graph = lattice(LATTICE_WEIGHTS, LATTICE_EDGES)
    plan = PathPlan.prepare(declare(graph, LOG_PROBABILITY))
    changed = dict(LATTICE_WEIGHTS, merged=math.log(0.1), twin=-math.inf)
    fresh = declare(lattice(changed, LATTICE_EDGES), LOG_PROBABILITY)
    assert_same_result(fresh.run(), plan.evaluate(tuple(changed.values())))
    assert plan.evaluate().value == pytest.approx(math.log(0.4 + 0.15 + 0.15))
    assert plan.evaluate(list(changed.values())).value == pytest.approx(math.log(0.25))
    zero_mass = dict.fromkeys(LATTICE_WEIGHTS, -math.inf)
    assert plan.evaluate(tuple(zero_mass.values())).value == -math.inf


def test_prepare_refuses_what_a_path_cannot_carry() -> None:
    """Ranked output, index axes, several relations, AND, and cycles are refused."""
    graph = lattice(LATTICE_WEIGHTS, LATTICE_EDGES)
    ranked = declare(
        graph, PATH, attribute=COST, lift=path_lift, ranked_output=True, output_cap=2
    )
    with pytest.raises(ValueError, match="does not carry ranked output"):
        PathPlan.prepare(ranked)
    with pytest.raises(ValueError, match="does not carry an index product"):
        PathPlan.prepare(declare(graph, LOG_PROBABILITY, index_axes=(("a", "b"),)))
    two = FoldDeclaration(
        "two",
        graph,
        AttributeValuation("value", WEIGHT, (NODES,)),
        LOG_PROBABILITY,
        value_lift,
        (
            FoldTransition(NEXT, ChildCombination.OR),
            FoldTransition(OTHER, ChildCombination.OR),
        ),
        roots=(ref(graph, "s"),),
    )
    with pytest.raises(
        ValueError, match="exactly one dependency relation, and the declaration names 2"
    ):
        PathPlan.prepare(two)
    joint = FoldDeclaration(
        "joint",
        graph,
        AttributeValuation("value", WEIGHT, (NODES,)),
        LOG_PROBABILITY,
        value_lift,
        (FoldTransition(NEXT, ChildCombination.AND),),
        roots=(ref(graph, "s"),),
    )
    with pytest.raises(ValueError, match="declared AND"):
        PathPlan.prepare(joint)
    cyclic = lattice(
        {"a": 0.0, "b": 0.0, "c": 0.0},
        (("a", "b"), ("b", "c"), ("c", "a")),
        acyclic=False,
    )
    with pytest.raises(
        ValueError, match=r"closes a cycle from .*'index': 2.* to .*'index': 0"
    ):
        PathPlan.prepare(declare(cyclic, LOG_PROBABILITY, roots=("a",)))


def test_values_are_bound_to_the_plan_and_its_carrier() -> None:
    """A vector of another length, or outside the double carrier, is refused."""
    graph = lattice(LATTICE_WEIGHTS, LATTICE_EDGES)
    plan = PathPlan.prepare(declare(graph, LOG_PROBABILITY))
    good = list(LATTICE_WEIGHTS.values())
    with pytest.raises(
        ValueError, match="takes 11 values in plan item order and was given 10"
    ):
        plan.evaluate(good[:-1])
    for bad in (math.nan, True, 1, "0.0"):
        with pytest.raises(ValueError, match="must be IEEE-double carrier values"):
            plan.marginals([*good[:-1], bad])
    with pytest.raises(ValueError, match="excluded infinite bound"):
        plan.evaluate([*good[:-1], math.inf])
    tropical = PathPlan.prepare(declare(graph, TROPICAL, lift=negated_lift))
    with pytest.raises(ValueError, match="excluded infinite bound"):
        tropical.evaluate([*tropical.values[:-1], -math.inf])
    with pytest.raises(ValueError, match="must be IEEE-double carrier values"):
        PathPlan.prepare(declare(graph, LOG_PROBABILITY, lift=lambda _value, _label: 1))
    general = PathPlan.prepare(
        declare(graph, COUNTING, attribute=COST, lift=lambda _value, _label: 1)
    )
    with pytest.raises(ValueError, match="nonnegative integer"):
        general.evaluate([*([1] * 10), -1])


def enumerate_paths(plan: PathPlan[float]) -> list[tuple[tuple[int, ...], float]]:
    """List every root-to-sink path with its log product, the fold's own definition."""
    paths: list[tuple[tuple[int, ...], float]] = []

    def visit(item: int, prefix: tuple[int, ...], value: float) -> None:
        prefix = (*prefix, item)
        value = value + plan.values[item]
        if not plan.children[item]:
            paths.append((prefix, value))
        for child in plan.children[item]:
            visit(child, prefix, value)

    for root in plan.roots:
        visit(root, (), 0.0)
    return paths


def log_sum(values: list[float]) -> float:
    """Sum log weights the slow way."""
    finite = [value for value in values if value != -math.inf]
    if not finite:
        return -math.inf
    high = max(finite)
    return high + math.log(math.fsum(math.exp(value - high) for value in finite))


def test_marginals_agree_with_enumerated_derivations() -> None:
    """Every marginal is the sum over the complete derivations through its item."""
    graph = lattice(LATTICE_WEIGHTS, LATTICE_EDGES)
    plan = PathPlan.prepare(declare(graph, LOG_PROBABILITY))
    marginals = plan.marginals()
    paths = enumerate_paths(plan)
    assert marginals.total == pytest.approx(log_sum([value for _, value in paths]))
    for item in range(len(plan.items)):
        through = log_sum([value for path, value in paths if item in path])
        assert close(marginals.marginals[item], through), plan.labels[item]
    assert marginals.inside == tuple(value for _, value in plan.evaluate().values)
    posteriors = marginals.posteriors(readout="normalize")
    assert posteriors.readout == "normalize"
    assert not posteriors.zero_mass
    assert posteriors.values is not None
    by_label = dict(zip(plan.labels, posteriors.values, strict=True))
    assert by_label["s"] == pytest.approx(1.0)
    assert by_label["f"] == pytest.approx(1.0)
    assert by_label["dead"] == 0.0 and by_label["todead"] == 0.0
    assert by_label["u"] == 0.0 and by_label["fromu"] == 0.0
    assert by_label["merged"] + by_label["first"] + by_label["twin"] == pytest.approx(
        1.0
    )
    assert by_label["m"] == pytest.approx(by_label["silent"])
    assert marginals.plan is plan
    assert isinstance(marginals, PathMarginals)


def test_fused_and_general_schedules_agree() -> None:
    """The general schedule and the fused kernels compute the same passes."""
    graph = lattice(LATTICE_WEIGHTS, LATTICE_EDGES)
    for roots in (("s",), ("m", "s"), ("f",)):
        fused = PathPlan.prepare(declare(graph, LOG_PROBABILITY, roots=roots))
        general = PathPlan.prepare(
            declare(
                graph,
                LOG_PROBABILITY,
                roots=roots,
                witness_order=lambda left, right: (left < right) - (left > right),
                tie_policy=TiePolicy.CHOOSE_FIRST,
            )
        )
        left, right = fused.marginals(), general.marginals()
        assert close(left.total, right.total)
        assert close(left.inside, right.inside)
        assert close(left.outside, right.outside)
        assert close(left.marginals, right.marginals)
        assert left.cost == right.cost
        exact_fused = PathPlan.prepare(declare(graph, ARCTIC, roots=roots))
        exact_general = PathPlan.prepare(
            declare(
                graph,
                ARCTIC,
                roots=roots,
                witness_order=AlgebraOrder(ARCTIC),
                tie_policy=TiePolicy.ALL,
            )
        )
        assert exact_fused.marginals().marginals == exact_general.marginals().marginals
        assert exact_fused.marginals().outside == exact_general.marginals().outside
        minimum = PathPlan.prepare(
            declare(graph, TROPICAL, lift=negated_lift, roots=roots)
        )
        assert minimum.marginals().marginals == tuple(
            math.inf if value == -math.inf else -value
            for value in exact_general.marginals().marginals
        )


def test_zero_mass_reports_no_posteriors() -> None:
    """A zero total is reported as such, with nothing fabricated."""
    graph = lattice(LATTICE_WEIGHTS, LATTICE_EDGES)
    plan = PathPlan.prepare(declare(graph, LOG_PROBABILITY))
    zero = plan.marginals([-math.inf] * len(plan.items))
    assert zero.total == -math.inf
    assert set(zero.marginals) == {-math.inf}
    posteriors = zero.posteriors(readout="normalize")
    assert posteriors.zero_mass and posteriors.values is None
    assert posteriors.readout == "normalize"
    unreached = PathPlan.prepare(declare(graph, LOG_PROBABILITY, roots=("dead",)))
    assert unreached.marginals().posteriors(readout="normalize").zero_mass


def test_posteriors_are_read_only_through_a_declared_readout() -> None:
    """An algebra that publishes no ``normalize`` is refused by name."""
    graph = lattice(LATTICE_WEIGHTS, LATTICE_EDGES)
    plan = PathPlan.prepare(
        declare(graph, COUNTING, attribute=COST, lift=lambda _value, _label: 1)
    )
    with pytest.raises(
        ValueError, match="'CountingSemiring' publishes no 'normalize' readout"
    ):
        plan.marginals().posteriors(readout="normalize")
    log_plan = PathPlan.prepare(declare(graph, LOG_PROBABILITY))
    for undeclared in ("_value", "missing"):
        with pytest.raises(ValueError, match=f"publishes no {undeclared!r} readout"):
            log_plan.marginals().posteriors(readout=undeclared)


def test_the_log_carrier_survives_weights_raw_exponentials_cannot() -> None:
    """Path weights of ``-1000`` fold and normalize without underflow."""
    graph = lattice(
        {"s": 0.0, "f": 0.0, "a": -1000.0, "b": -1001.0},
        (("s", "a"), ("a", "f"), ("s", "b"), ("b", "f")),
    )
    plan = PathPlan.prepare(declare(graph, LOG_PROBABILITY))
    marginals = plan.marginals()
    assert marginals.total == pytest.approx(-1000 + math.log1p(math.exp(-1)))
    posteriors = marginals.posteriors(readout="normalize")
    assert posteriors.values is not None
    by_label = dict(zip(plan.labels, posteriors.values, strict=True))
    assert by_label["a"] == pytest.approx(1 / (1 + math.exp(-1)))
    assert by_label["b"] == pytest.approx(math.exp(-1) / (1 + math.exp(-1)))


BIG = 1e308
OVERFLOWS: dict[
    str, tuple[Semiring[Any], Callable[[object, str], Any], dict[str, float], Edges]
] = {
    "log-chain-underflows": (
        LOG_PROBABILITY,
        value_lift,
        {"r": -BIG, "a": -BIG, "f": 0.0},
        (("r", "a"), ("a", "f")),
    ),
    "log-gather-underflows": (
        LOG_PROBABILITY,
        value_lift,
        {"r": -BIG, "a": -BIG, "b": -BIG, "f": 0.0},
        (("r", "a"), ("r", "b"), ("a", "f"), ("b", "f")),
    ),
    "log-chain-overflows": (
        LOG_PROBABILITY,
        value_lift,
        {"r": BIG, "a": BIG, "f": 0.0},
        (("r", "a"), ("a", "f")),
    ),
    "arctic-chain-underflows": (
        ARCTIC,
        value_lift,
        {"r": -BIG, "a": -BIG, "f": 0.0},
        (("r", "a"), ("a", "f")),
    ),
    "arctic-chain-overflows": (
        ARCTIC,
        value_lift,
        {"r": BIG, "a": BIG, "f": 0.0},
        (("r", "a"), ("a", "f")),
    ),
    "tropical-chain-overflows": (
        TROPICAL,
        negated_lift,
        {"r": -BIG, "a": -BIG, "f": 0.0},
        (("r", "a"), ("a", "f")),
    ),
    "tropical-chain-underflows": (
        TROPICAL,
        negated_lift,
        {"r": BIG, "a": BIG, "f": 0.0},
        (("r", "a"), ("a", "f")),
    ),
    "log-outside-product-underflows": (
        LOG_PROBABILITY,
        value_lift,
        {"r": -BIG, "a": -BIG, "f": BIG},
        (("r", "a"), ("a", "f")),
    ),
    "log-outside-overflows": (
        LOG_PROBABILITY,
        value_lift,
        {"r": BIG, "a": BIG, "f": -BIG},
        (("r", "a"), ("a", "f")),
    ),
    # A prefix and a suffix that are each finite but overflow when multiplied
    # through ``a``, while every product along the way stays finite because a
    # second root ``d`` reaches ``c`` cheaply and ``b`` reaches the sink cheaply.
    "log-through-underflows": (
        LOG_PROBABILITY,
        value_lift,
        {"r": -BIG, "a": 0.0, "c": -BIG, "s": 0.0, "b": 0.0, "d": 0.0},
        (("r", "a"), ("a", "c"), ("c", "s"), ("r", "b"), ("b", "s"), ("d", "c")),
    ),
    "arctic-outside-product-underflows": (
        ARCTIC,
        value_lift,
        {"r": -BIG, "a": -BIG, "f": BIG},
        (("r", "a"), ("a", "f")),
    ),
    "arctic-outside-overflows": (
        ARCTIC,
        value_lift,
        {"r": BIG, "a": BIG, "f": -BIG},
        (("r", "a"), ("a", "f")),
    ),
    "tropical-through-overflows": (
        TROPICAL,
        negated_lift,
        {"r": -BIG, "a": 0.0, "c": -BIG, "s": 0.0, "b": 0.0, "d": 0.0},
        (("r", "a"), ("a", "c"), ("c", "s"), ("r", "b"), ("b", "s"), ("d", "c")),
    ),
}


@pytest.mark.parametrize("case", list(OVERFLOWS))
def test_a_result_that_leaves_the_finite_carrier_is_refused(case: str) -> None:
    """The fused kernels refuse overflow the way the algebra's methods do."""
    semiring, lift, weights, edges = OVERFLOWS[case]
    roots = ("r", "d") if "d" in weights else ("r",)
    plan = PathPlan.prepare(
        declare(lattice(weights, edges), semiring, lift=lift, roots=roots)
    )
    with pytest.raises(OverflowError, match="leaves the finite IEEE-double carrier"):
        plan.marginals()
    if "outside" in case or "through" in case:
        # The inside pass is finite here; only the outside pass overflows.
        assert math.isfinite(plan.evaluate().value)
    else:
        with pytest.raises(
            OverflowError, match="leaves the finite IEEE-double carrier"
        ):
            plan.evaluate()


def test_the_fused_log_schedule_associates_as_the_fold_does() -> None:
    """Local times the summed alternatives, so cancellation rounds identically."""
    graph = lattice({"s": -1e16, "a": 1e16, "b": 1e16}, (("s", "a"), ("s", "b")))
    declaration = declare(graph, LOG_PROBABILITY)
    assert PathPlan.prepare(declaration).evaluate().value == declaration.run().value


def test_selection_is_fused_and_ties_choose_the_canonical_first() -> None:
    """A fused best path equals the general selection, and ties are deterministic."""
    weights = dict(LATTICE_WEIGHTS, merged=math.log(0.15))
    graph = lattice(weights, LATTICE_EDGES)
    first = PathPlan.prepare(
        declare(
            graph,
            ARCTIC,
            witness_order=AlgebraOrder(ARCTIC),
            tie_policy=TiePolicy.CHOOSE_FIRST,
        )
    )
    every = PathPlan.prepare(
        declare(
            graph,
            ARCTIC,
            witness_order=AlgebraOrder(ARCTIC),
            tie_policy=TiePolicy.ALL,
            output_cap=4,
        )
    )
    chosen = first.evaluate()
    assert chosen.value == pytest.approx(math.log(0.15))
    assert chosen.provenance == (("s", "merged", "f"),)
    assert not chosen.truncated and chosen.cost.witness_count == 1
    listed = every.evaluate()
    assert listed.provenance == (
        ("s", "merged", "f"),
        ("s", "first", "m", "silent", "f"),
        ("s", "twin", "m", "silent", "f"),
    )
    assert listed.cost.witness_count == 3
    assert first.evaluate().provenance == chosen.provenance
    capped = PathPlan.prepare(
        declare(
            graph,
            ARCTIC,
            witness_order=AlgebraOrder(ARCTIC),
            tie_policy=TiePolicy.ALL,
            output_cap=2,
        )
    ).evaluate()
    assert capped.truncated and len(capped.provenance or ()) == 2
    later = declare(
        graph,
        ARCTIC,
        roots=("f", "s"),
        witness_order=AlgebraOrder(ARCTIC),
        tie_policy=TiePolicy.CHOOSE_FIRST,
    )
    assert PathPlan.prepare(later).evaluate().provenance == later.run().provenance


def test_algebra_order_compares_by_the_algebra_and_refuses_aggregation() -> None:
    """The order prefers what the algebra's addition returns and ties on equality."""
    order = AlgebraOrder(ARCTIC)
    assert order(2.0, 1.0) == -1
    assert order(1.0, 2.0) == 1
    assert order(1.0, 1.0) == 0
    assert AlgebraOrder(TROPICAL)(1.0, 2.0) == -1
    with pytest.raises(
        ValueError, match="'CountingSemiring' does not declare add_selective"
    ):
        AlgebraOrder(COUNTING)
    with pytest.raises(
        ValueError, match="'LogProbabilitySemiring' does not declare add_selective"
    ):
        AlgebraOrder(LOG_PROBABILITY)


def test_declared_roots_with_parents_take_the_identity_too() -> None:
    """A root reached from another root sums its own identity with the prefixes."""
    graph = lattice(LATTICE_WEIGHTS, LATTICE_EDGES)
    for semiring, lift in (
        (LOG_PROBABILITY, value_lift),
        (ARCTIC, value_lift),
        (TROPICAL, negated_lift),
    ):
        plan = PathPlan.prepare(
            declare(graph, semiring, lift=lift, roots=("s", "m", "silent", "f"))
        )
        marginals = plan.marginals()
        at = plan.labels.index
        assert marginals.outside[at("s")] == semiring.one
        assert marginals.outside[at("silent")] == semiring.add(
            semiring.one,
            semiring.multiply(marginals.outside[at("m")], plan.values[at("m")]),
        )
        assert marginals.total == plan.evaluate().value
    unreached = dict(LATTICE_WEIGHTS, first=-math.inf, twin=-math.inf)
    plan = PathPlan.prepare(
        declare(
            lattice(unreached, LATTICE_EDGES), LOG_PROBABILITY, roots=("s", "silent")
        )
    )
    at = plan.labels.index
    assert plan.marginals().outside[at("silent")] == 0.0


def test_structural_labels_name_items_without_durable_identities() -> None:
    """Provenance and labels fall back to the canonical structural label."""
    graph = lattice(
        {"s": 0.0, "a": -1.0, "f": 0.0}, (("s", "a"), ("a", "f")), durable=False
    )
    declaration = FoldDeclaration(
        "structural",
        graph,
        AttributeValuation("value", WEIGHT, (NODES,)),
        ARCTIC,
        value_lift,
        (FoldTransition(NEXT, ChildCombination.OR),),
        roots=(ItemRef(NODES, 0),),
        witness_order=AlgebraOrder(ARCTIC),
        tie_policy=TiePolicy.CHOOSE_FIRST,
    )
    plan = PathPlan.prepare(declaration)
    assert plan.labels == tuple(f"{NS}:nodes:{i}" for i in range(3))
    assert plan.evaluate().provenance == declaration.run().provenance == (plan.labels,)


def test_an_empty_path_has_mass_one() -> None:
    """A root that is its own sink accepts with its value."""
    plan = PathPlan.prepare(declare(lattice({"s": 0.0}, ()), LOG_PROBABILITY))
    marginals = plan.marginals()
    assert marginals.total == 0.0
    assert marginals.posteriors(readout="normalize").values == (1.0,)
    best = PathPlan.prepare(
        declare(
            lattice({"s": 0.0}, ()),
            ARCTIC,
            witness_order=AlgebraOrder(ARCTIC),
            tie_policy=TiePolicy.CHOOSE_FIRST,
        )
    )
    assert best.evaluate().provenance == (("s",),)


def test_the_cost_accounts_for_both_passes() -> None:
    """Additions and multiplications are the general schedule's, for each pass."""
    weights = {
        label: LATTICE_WEIGHTS[label]
        for label in ("s", "m", "f", "merged", "first", "silent")
    }
    edges: Edges = (
        ("s", "merged"),
        ("merged", "f"),
        ("s", "first"),
        ("first", "m"),
        ("m", "silent"),
        ("silent", "f"),
    )
    plan = PathPlan.prepare(declare(lattice(weights, edges), LOG_PROBABILITY))
    inside = plan.evaluate().cost
    assert (inside.carrier_additions, inside.carrier_multiplications) == (2, 6)
    both = plan.marginals().cost
    assert (both.carrier_additions, both.carrier_multiplications) == (3, 18)
    assert both.document_size == 6 and both.relation_incidence == 6
    assert both.index_product_size == 1 and both.witness_count == 0
