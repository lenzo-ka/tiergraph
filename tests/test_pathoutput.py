"""Output plans pool complete emitted strings without changing PathPlan."""

from __future__ import annotations

import json
import math
import os
import random
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

import pytest

from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValuation,
    AttributeValue,
    BipartiteRelationDeclaration,
    ChildCombination,
    Emissions,
    FoldDeclaration,
    FoldTransition,
    Graph,
    Item,
    ItemRef,
    NamespaceDeclaration,
    OutputPlan,
    QualifiedName,
    RelationInstance,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    XsdType,
    pathoutput,
)
from tiergraph.pathplan import PathPlan
from tiergraph.semiring import (
    ARCTIC,
    COUNTING,
    LOG_PROBABILITY,
    TROPICAL,
    CountingSemiring,
    Semiring,
)

NS = "https://example.com/plan"
NODES = QualifiedName(NS, "nodes")
NODE = QualifiedName(NS, "node")
NEXT = QualifiedName(NS, "next")
MEMBERSHIP = QualifiedName(NS, "membership")
WEIGHT = QualifiedName(NS, "weight")
COUNT = QualifiedName(NS, "count")
Edges = tuple[tuple[str, str], ...]


def lexical(value: float) -> str:
    """Spell a log weight as an XSD double."""
    return "-INF" if value == -math.inf else repr(value)


def graph(weights: dict[str, float], edges: Edges) -> Graph:
    """Build a one-tier path graph while retaining repeated edge instances."""
    labels = tuple(weights)
    refs = {label: ItemRef(NODES, index) for index, label in enumerate(labels)}
    return Graph(
        (NamespaceDeclaration("outputs", NS),),
        (
            Tier(
                TierDeclaration(NODES, "Nodes"),
                tuple(
                    Item(
                        label,
                        (
                            AttributeValue(WEIGHT, XsdType.DOUBLE, lexical(weight)),
                            AttributeValue(COUNT, XsdType.INTEGER, "1"),
                        ),
                    )
                    for label, weight in weights.items()
                ),
            ),
        ),
        (
            SimpleRelationDeclaration(MEMBERSHIP, NODES, NODE),
            BipartiteRelationDeclaration(NEXT, NODE, NODE, acyclic=True),
        ),
        tuple(
            RelationInstance(NEXT, refs[parent], refs[child]) for parent, child in edges
        ),
        (
            AttributeDeclaration(WEIGHT, AttributeDomain.ITEM, XsdType.DOUBLE),
            AttributeDeclaration(COUNT, AttributeDomain.ITEM, XsdType.INTEGER),
        ),
    )


def plan(
    weights: dict[str, float],
    edges: Edges,
    *,
    roots: tuple[str, ...],
    counting: bool = False,
    semiring: Semiring[object] | None = None,
) -> PathPlan[object]:
    """Prepare a path plan over the fixture graph."""
    carrier: Semiring[object] = cast(
        Semiring[object],
        semiring if semiring is not None else COUNTING if counting else LOG_PROBABILITY,
    )
    built = graph(weights, edges)
    refs = {
        item.durable_id: ItemRef(NODES, index)
        for index, item in enumerate(built.tiers[0].items)
    }
    count_carrier = isinstance(carrier, CountingSemiring)
    attribute = COUNT if count_carrier else WEIGHT

    def lift(value: object, _label: str) -> object:
        return 1 if count_carrier else value

    declaration = FoldDeclaration(
        "fixture",
        built,
        AttributeValuation("value", attribute, (NODES,)),
        carrier,
        lift,
        (FoldTransition(NEXT, ChildCombination.OR),),
        roots=tuple(refs[label] for label in roots),
    )
    return PathPlan.prepare(declaration)


@dataclass(frozen=True)
class Enumerated:
    """One complete base path and its emitted output."""

    items: tuple[int, ...]
    output: tuple[str, ...]
    value: float | int


def enumerate_paths(
    base: PathPlan[object], emissions: Emissions[object], values: Sequence[object]
) -> tuple[Enumerated, ...]:
    """Enumerate the small fixture's root-to-sink paths."""
    paths: list[Enumerated] = []
    algebra = base.declaration.semiring

    def visit(index: int, seen: tuple[int, ...], output: tuple[str, ...]) -> None:
        following = seen + (index,)
        emitted = output + emissions.per_item[index]
        if not base.children[index]:
            value = algebra.one
            for item in following:
                value = algebra.multiply(value, values[item])
            paths.append(Enumerated(following, emitted, cast(float | int, value)))
            return
        for child in base.children[index]:
            visit(child, following, emitted)

    for root in base.roots:
        visit(root, (), ())
    return tuple(paths)


def assert_log_close(actual: float, expected: float) -> None:
    """Compare log masses, including their shared zero."""
    assert actual == expected or math.isclose(actual, expected, abs_tol=1e-12)


def path_mass(base: PathPlan[object], paths: Sequence[Enumerated]) -> object:
    """Add enumerated path values with the fixture's carrier."""
    algebra = base.declaration.semiring
    total = algebra.zero
    for path in paths:
        total = algebra.add(total, path.value)
    return total


def assert_carrier_equal(
    base: PathPlan[object], actual: object, expected: object
) -> None:
    """Compare oracle values with the certified carrier's own semantics."""
    if cast(object, base.declaration.semiring) is LOG_PROBABILITY:
        assert_log_close(cast(float, actual), cast(float, expected))
    else:
        assert actual == expected


def assert_matches_brute_force(
    base: PathPlan[object],
    emissions: Emissions[object],
    candidates: tuple[tuple[str, ...], ...],
    exercised: dict[str, int],
) -> None:
    """Compare one output product with enumeration and record oracle branches."""
    all_paths = enumerate_paths(base, emissions, base.values)
    output = OutputPlan.prepare(base, emissions, candidates)
    masses = output.masses()
    expected_total = path_mass(base, all_paths)
    assert_carrier_equal(base, masses.total, expected_total)
    expected_candidates = []
    for index, candidate in enumerate(candidates):
        matching = tuple(path for path in all_paths if path.output == candidate)
        expected = path_mass(base, matching)
        expected_candidates.append(expected)
        assert_carrier_equal(base, masses.per_candidate[index], expected)
        assert output.accepted[index] is bool(matching)
        if not matching:
            exercised["not accepted"] += 1
            with pytest.raises(ValueError, match="structurally accepted"):
                output.conditioned(index)
            continue
        exercised["accepted"] += 1
        item_marginals = output.item_marginals(index)
        if expected == base.declaration.semiring.zero:
            exercised["zero mass"] += 1
            assert item_marginals.zero_mass
            assert item_marginals.values is None
            continue
        exercised["positive mass"] += 1
        assert not item_marginals.zero_mass
        assert item_marginals.values is not None
        for item in range(len(base.items)):
            through = path_mass(
                base, tuple(path for path in matching if item in path.items)
            )
            assert_carrier_equal(base, item_marginals.values[item], through)
    residual = path_mass(
        base, tuple(path for path in all_paths if path.output not in candidates)
    )
    algebra = base.declaration.semiring
    zero_mass = expected_total == algebra.zero
    exercised["residual zero" if residual == algebra.zero else "residual positive"] += 1
    assert_carrier_equal(base, masses.residual, residual)
    assert masses.zero_mass is zero_mass
    algebra_object = cast(object, algebra)
    if algebra_object is LOG_PROBABILITY or algebra_object is COUNTING:
        if zero_mass:
            expected_decided = False
            expected_tied: tuple[int, ...] = ()
        else:
            comparable = cast(list[float | int], expected_candidates)
            best = max(comparable)
            expected_decided = best >= cast(float | int, residual)
            expected_tied = tuple(
                index for index, value in enumerate(comparable) if value == best
            )
        assert masses.decided is expected_decided
        assert masses.tied == expected_tied
        exercised[f"decided {expected_decided}"] += 1
        if algebra_object is COUNTING and len(expected_tied) > 1:
            exercised["counting multi-candidate tie"] += 1
    else:
        assert masses.decided is None
        assert masses.tied is None
    assert masses.cost == output.plan.marginals(output.values()).cost


def test_random_dag_matches_brute_force_oracle() -> None:
    """Exercise the complete oracle over fixed structures and random DAGs."""
    exercised = {
        "accepted": 0,
        "not accepted": 0,
        "zero mass": 0,
        "positive mass": 0,
        "residual zero": 0,
        "residual positive": 0,
        "decided True": 0,
        "decided False": 0,
        "counting multi-candidate tie": 0,
    }

    # Fixed certificate cases cover strict residual wins, equality at the
    # decision boundary, a counting tie, zero mass, and an unsupported carrier.
    log_base = plan(
        {"root": 0.0, "candidate": math.log(0.25), "residual": math.log(0.75)},
        (("root", "candidate"), ("root", "residual")),
        roots=("root",),
    )
    log_emissions = Emissions.bind(
        log_base, {"candidate": ("candidate",), "residual": ("residual",)}
    )
    assert_matches_brute_force(log_base, log_emissions, (("candidate",),), exercised)
    assert (
        OutputPlan.prepare(log_base, log_emissions, (("candidate",),)).masses().decided
        is False
    )

    counting_base = plan(
        {"root": 0.0, "a": 0.0, "b": 0.0, "residual": 0.0},
        (
            ("root", "a"),
            ("root", "b"),
            ("root", "residual"),
            ("root", "residual"),
        ),
        roots=("root",),
        counting=True,
    )
    counting_emissions = Emissions.bind(
        counting_base, {"a": ("a",), "b": ("b",), "residual": ("residual",)}
    )
    assert_matches_brute_force(
        counting_base, counting_emissions, (("a",), ("b",)), exercised
    )
    assert (
        OutputPlan.prepare(counting_base, counting_emissions, (("a",), ("b",)))
        .masses()
        .decided
        is False
    )

    equality_base = plan(
        {"root": 0.0, "a": 0.0, "b": 0.0, "residual": 0.0},
        (("root", "a"), ("root", "b"), ("root", "residual")),
        roots=("root",),
        counting=True,
    )
    equality_emissions = Emissions.bind(
        equality_base, {"a": ("a",), "b": ("b",), "residual": ("residual",)}
    )
    assert_matches_brute_force(
        equality_base,
        equality_emissions,
        (("a",), ("b",)),
        exercised,
    )

    zero_base = replace(counting_base, values=tuple(0 for _item in counting_base.items))
    assert_matches_brute_force(
        zero_base,
        Emissions.bind(
            zero_base, {"a": ("a",), "b": ("b",), "residual": ("residual",)}
        ),
        (("missing",),),
        exercised,
    )

    tropical_base = plan(
        {"root": 0.0, "candidate": 1.0, "residual": 2.0},
        (("root", "candidate"), ("root", "residual")),
        roots=("root",),
        semiring=cast(Semiring[object], TROPICAL),
    )
    assert_matches_brute_force(
        tropical_base,
        Emissions.bind(
            tropical_base,
            {"candidate": ("candidate",), "residual": ("residual",)},
        ),
        (("candidate",),),
        exercised,
    )

    # Retain the structures that target joins, duplicate edges, explicit roots,
    # internal candidate prefixes, and multiple product copies of one base item.
    generator = random.Random(1949)
    for _case in range(24):
        labels = tuple(f"n{index}" for index in range(11))
        weights = {
            label: (-math.inf if index == 9 else math.log(generator.uniform(0.1, 1.0)))
            for index, label in enumerate(labels)
        }
        edges = (
            (labels[0], labels[1]),
            (labels[0], labels[1]),
            (labels[0], labels[2]),
            (labels[1], labels[3]),
            (labels[2], labels[3]),
            (labels[3], labels[4]),
            (labels[3], labels[5]),
            (labels[0], labels[6]),
            (labels[0], labels[7]),
            *(
                (labels[left], labels[right])
                for left in range(7, 10)
                for right in range(left + 1, 10)
                if generator.random() < 0.35
            ),
        )
        base = plan(weights, tuple(edges), roots=(labels[0], labels[1]))
        choices = ((), ("a",), ("a", "b"), ("z",))
        emission_map = {
            label: generator.choice(choices)
            for label in labels[7:]
            if generator.random() < 0.8
        }
        emission_map.update(
            {
                labels[1]: ("a",),
                labels[4]: ("x",),
                labels[5]: ("a", "x"),
                labels[6]: ("a",),
            }
        )
        emissions = Emissions.bind(base, emission_map)
        all_paths = enumerate_paths(
            base, emissions, cast(tuple[float, ...], base.values)
        )
        candidates = (("a", "x"),)
        assert any(path.output == ("a",) for path in all_paths)
        assert_matches_brute_force(base, emissions, candidates, exercised)

    emission_choices = ((), ("a",), ("b",), ("c",), ("a", "b"), ("b", "c"))
    for seed in range(32):
        generator = random.Random(seed)
        labels = tuple(f"r{seed}n{index}" for index in range(generator.randint(6, 9)))
        zero_items = set(generator.sample(labels, generator.randint(1, 2)))
        weights = {
            label: (
                -math.inf
                if label in zero_items
                else generator.uniform(-50.0, -38.0)
                if generator.random() < 0.18
                else math.log(generator.uniform(0.1, 1.0))
            )
            for label in labels
        }
        random_edges = [
            (labels[left], labels[right])
            for left in range(len(labels))
            for right in range(left + 1, len(labels))
            if generator.random() < generator.uniform(0.16, 0.38)
        ]
        if random_edges and generator.random() < 0.6:
            random_edges.insert(
                generator.randrange(len(random_edges) + 1),
                generator.choice(random_edges),
            )
        roots = tuple(
            labels[index]
            for index in sorted(
                generator.sample(range(len(labels) - 1), generator.randint(1, 3))
            )
        )
        base = plan(weights, tuple(random_edges), roots=roots)
        emission_map = {
            label: generator.choice(emission_choices)
            for label in labels
            if generator.random() < 0.85
        }
        emissions = Emissions.bind(base, emission_map)
        all_paths = enumerate_paths(
            base, emissions, cast(tuple[float, ...], base.values)
        )
        outputs = sorted({path.output for path in all_paths})
        emitted = generator.sample(outputs, generator.randint(1, min(3, len(outputs))))
        prefixes = sorted(
            {
                output[:stop]
                for output in outputs
                for stop in range(len(output))
                if output[:stop] not in outputs
            }
        )
        internal_prefixes = sorted(
            {
                tuple(
                    token
                    for item in path.items[:stop]
                    for token in emissions.per_item[item]
                )
                for path in all_paths
                for stop in range(1, len(path.items))
            }
            - set(outputs)
        )
        random_candidates = list(emitted)
        if prefixes:
            random_candidates.append(generator.choice(prefixes))
        if internal_prefixes:
            random_candidates.append(generator.choice(internal_prefixes))
        random_candidates.append((f"never-{seed}",))

        zero_outputs = sorted(
            output
            for output in outputs
            if all(
                path.value == -math.inf for path in all_paths if path.output == output
            )
        )
        if zero_outputs:
            random_candidates.append(generator.choice(zero_outputs))
        random_candidates = list(dict.fromkeys(random_candidates))
        generator.shuffle(random_candidates)
        assert_matches_brute_force(base, emissions, tuple(random_candidates), exercised)

    assert all(exercised.values()), exercised


def test_pooled_mass_beats_best_path() -> None:
    """Kill replacing log-sum-exp by a per-output maximum."""
    weights = {
        "root": 0.0,
        "a1": math.log(0.3),
        "a2": math.log(0.3),
        "b": math.log(0.4),
        "sink": 0.0,
    }
    edges = (
        ("root", "a1"),
        ("root", "a2"),
        ("root", "b"),
        *((name, "sink") for name in ("a1", "a2", "b")),
    )
    base = plan(weights, tuple(edges), roots=("root",))
    emissions = Emissions.bind(base, {"a1": ("A",), "a2": ("A",), "b": ("B",)})
    paths = enumerate_paths(base, emissions, cast(tuple[float, ...], base.values))
    arctic_best = ARCTIC.zero
    for path in paths:
        arctic_best = ARCTIC.add(arctic_best, path.value)
    assert tuple(path.output for path in paths if path.value == arctic_best) == (
        ("B",),
    )
    costs = enumerate_paths(
        base,
        emissions,
        tuple(-value for value in cast(tuple[float, ...], base.values)),
    )
    tropical_best = TROPICAL.zero
    for path in costs:
        tropical_best = TROPICAL.add(tropical_best, path.value)
    assert tuple(path.output for path in costs if path.value == tropical_best) == (
        ("B",),
    )
    masses = OutputPlan.prepare(base, emissions, (("A",), ("B",))).masses()
    assert math.exp(cast(float, masses.per_candidate[0])) == pytest.approx(0.6)
    assert math.exp(cast(float, masses.per_candidate[1])) == pytest.approx(0.4)
    assert masses.tied == (0,)
    assert masses.decided


def test_residual_is_a_product_sink_not_subtraction() -> None:
    """Kill reconstructing the residual by subtracting candidate masses."""
    tiny = 1e-18
    base = plan(
        {"root": 0.0, "candidate": math.log1p(-tiny), "residual": math.log(tiny)},
        (("root", "candidate"), ("root", "residual")),
        roots=("root",),
    )
    emissions = Emissions.bind(base, {"candidate": ("a",), "residual": ("b",)})
    output = OutputPlan.prepare(base, emissions, (("a",),))
    assert cast(float, output.masses().residual) == math.log(tiny)
    covered = OutputPlan.prepare(base, emissions, (("a",), ("b",))).masses()
    assert covered.residual == -math.inf


def test_prefix_empty_and_multi_token_emissions() -> None:
    """Kill internal-trie acceptance and one-token-per-item assumptions."""
    base = plan(
        {"root": 0.0, "short": math.log(0.2), "long": math.log(0.8), "empty": 0.0},
        (("root", "short"), ("root", "empty"), ("empty", "long")),
        roots=("root",),
    )
    emissions = Emissions.bind(
        base, {"root": (), "short": ("a",), "empty": (), "long": ("a", "b")}
    )
    masses = OutputPlan.prepare(base, emissions, (("a",), ("a", "b"))).masses()
    assert tuple(
        math.exp(cast(float, value)) for value in masses.per_candidate
    ) == pytest.approx((0.2, 0.8))
    assert masses.residual == -math.inf


def test_emitted_proper_prefix_of_sole_candidate_is_residual() -> None:
    """Kill marking every candidate-prefix trie state as terminal."""
    base = plan(
        {"root": 0.0, "short": math.log(0.25), "long": math.log(0.75)},
        (("root", "short"), ("root", "long")),
        roots=("root",),
    )
    emissions = Emissions.bind(base, {"short": ("a",), "long": ("a", "b")})
    masses = OutputPlan.prepare(base, emissions, (("a", "b"),)).masses()
    assert math.exp(cast(float, masses.per_candidate[0])) == pytest.approx(0.75)
    assert math.exp(cast(float, masses.residual)) == pytest.approx(0.25)


def test_conditioning_pools_base_item_copies_only_for_candidate() -> None:
    """Kill taking one trie-state copy or pooling copies across candidates."""
    base = plan(
        {
            "root": 0.0,
            "a": math.log(0.2),
            "empty": math.log(0.3),
            "join": 0.0,
            "x": 0.0,
            "ax": 0.0,
            "b": math.log(0.5),
        },
        (
            ("root", "a"),
            ("root", "empty"),
            ("a", "join"),
            ("empty", "join"),
            ("join", "x"),
            ("join", "ax"),
            ("root", "b"),
        ),
        roots=("root",),
    )
    emissions = Emissions.bind(
        base, {"a": ("a",), "x": ("x",), "ax": ("a", "x"), "b": ("b",)}
    )
    output = OutputPlan.prepare(base, emissions, (("a", "x"), ("x",), ("b",)))
    first = output.item_marginals(0)
    second = output.item_marginals(1)
    join = base.labels.index("join")
    assert first.values is not None and second.values is not None
    assert math.exp(cast(float, first.values[join])) == pytest.approx(0.5)
    assert math.exp(cast(float, second.values[join])) == pytest.approx(0.3)


def test_duplicate_relation_instances_preserve_mass() -> None:
    """Kill deduplication of product and conditioned relation instances."""
    base = plan(
        {"root": 0.0, "sink": 0.0},
        (("root", "sink"), ("root", "sink")),
        roots=("root",),
    )
    emissions = Emissions.bind(base, {"sink": ("a",)})
    output = OutputPlan.prepare(base, emissions, (("a",),))
    assert cast(float, output.masses().per_candidate[0]) == pytest.approx(math.log(2))
    assert cast(float, output.item_marginals(0).total) == pytest.approx(math.log(2))


def test_product_uses_declared_roots() -> None:
    """Kill falling back to parentless-root inference in either derived plan."""
    base = plan(
        {"first": 0.0, "second": 0.0, "sink": 0.0},
        (("first", "second"), ("second", "sink")),
        roots=("first", "second"),
    )
    emissions = Emissions.bind(base, {"sink": ("right",)})
    output = OutputPlan.prepare(base, emissions, (("right",),))
    assert output.plan.roots
    assert cast(float, output.masses().per_candidate[0]) == pytest.approx(math.log(2))
    assert cast(float, output.item_marginals(0).total) == pytest.approx(math.log(2))


def test_conditioning_survives_zero_positive_zero_revaluation() -> None:
    """Kill conditioning based on a candidate's current numerical mass."""
    base = plan(
        {"root": -math.inf, "sink": 0.0},
        (("root", "sink"),),
        roots=("root",),
    )
    output = OutputPlan.prepare(base, Emissions.bind(base, {"sink": ("a",)}), (("a",),))
    conditioned = output.conditioned(0)
    zero = output.item_marginals(0)
    positive = output.item_marginals(0, (0.0, 0.0))
    zero_again = output.item_marginals(0, (-math.inf, 0.0))
    assert conditioned is output.conditioned(0)
    assert zero.zero_mass and zero.values is None
    assert not positive.zero_mass and positive.values is not None
    assert zero_again.zero_mass and zero_again.values is None
    masses = output.masses((-math.inf, 0.0))
    assert masses.zero_mass and not masses.decided and masses.tied == ()


def test_refusals_name_the_offender() -> None:
    """Kill permissive candidate, emission, algebra, and vector handling."""
    base = plan({"root": 0.0}, (), roots=("root",))
    emissions = Emissions.bind(base, {})
    with pytest.raises(ValueError, match="must not be empty"):
        OutputPlan.prepare(base, emissions, ())
    with pytest.raises(ValueError, match="duplicated"):
        OutputPlan.prepare(base, emissions, ((), ()))
    absent = OutputPlan.prepare(base, emissions, (("missing",),))
    with pytest.raises(ValueError, match="structurally accepted"):
        absent.conditioned(0)
    with pytest.raises(TypeError, match="candidate 0"):
        OutputPlan.prepare(base, emissions, cast(Sequence[Sequence[str]], ("a",)))
    with pytest.raises(TypeError, match="symbol 1"):
        OutputPlan.prepare(base, emissions, cast(Sequence[Sequence[str]], ((1,),)))
    with pytest.raises(TypeError, match="emission label 3"):
        Emissions.bind(base, cast(dict[str, Sequence[str]], {3: ()}))
    with pytest.raises(ValueError, match="missing"):
        Emissions.bind(base, {"missing": ()})
    with pytest.raises(TypeError, match="sequence of strings"):
        Emissions.bind(base, {"root": cast(Sequence[str], "a")})
    with pytest.raises(TypeError, match="symbol 1"):
        Emissions.bind(base, {"root": cast(Sequence[str], (1,))})
    with pytest.raises(ValueError, match="require 1 item entries.*given 0"):
        Emissions(base, ())
    with pytest.raises(ValueError, match="require 1 item entries.*given 2"):
        Emissions(base, ((), ()))
    with pytest.raises(TypeError, match="item 0 label 'root'.*tuple of strings"):
        Emissions(base, cast(tuple[tuple[str, ...], ...], ([],)))
    with pytest.raises(TypeError, match="item 0 label 'root'.*symbol 1"):
        Emissions(base, cast(tuple[tuple[str, ...], ...], ((1,),)))

    other = plan({"root": 0.0}, (), roots=("root",))
    with pytest.raises(ValueError, match="different path plan"):
        OutputPlan.prepare(other, emissions, ((),))
    rootless = replace(base, roots=())
    with pytest.raises(ValueError, match="explicit product root"):
        OutputPlan.prepare(rootless, Emissions.bind(rootless, {}), ((),))
    output = OutputPlan.prepare(base, emissions, ((),))
    with pytest.raises(ValueError, match="takes 1 values"):
        output.values(())
    with pytest.raises(ValueError, match="outside"):
        output.conditioned(-1)
    with pytest.raises(ValueError, match="outside"):
        output.conditioned(cast(int, True))

    with pytest.raises(ValueError, match="explicit nonempty roots"):
        pathoutput._prepare_derived(
            "rootless",
            cast(Semiring[object], LOG_PROBABILITY),
            (),
            (),
            (),
            carrier_operation_cost=1,
        )


class NoncommutativeCounting(CountingSemiring):
    """A fixture carrier whose multiplication declaration refuses products."""

    multiply_commutative = False


def test_noncommutative_algebra_is_refused() -> None:
    """Kill admitting an algebra whose multiplication cannot reorder marginals."""
    base = plan(
        {"root": 0.0},
        (),
        roots=("root",),
        semiring=cast(Semiring[object], NoncommutativeCounting()),
    )
    with pytest.raises(ValueError, match="NoncommutativeCounting"):
        OutputPlan.prepare(base, Emissions.bind(base, {}), ((),))


def test_counting_masses_count_paths_and_reuse() -> None:
    """Kill treating COUNTING as log mass or caching a prior value vector."""
    base = plan(
        {"root": 0.0, "left": 0.0, "right": 0.0},
        (("root", "left"), ("root", "right")),
        roots=("root",),
        counting=True,
    )
    emissions = Emissions.bind(base, {"left": ("a",), "right": ("a",)})
    reused = OutputPlan.prepare(base, emissions, (("a",),))
    assert reused.masses().per_candidate == (2,)
    assert reused.masses((0, 0, 0)).zero_mass
    fresh = OutputPlan.prepare(base, emissions, (("a",),))
    assert reused.masses((2, 3, 5)) == fresh.masses((2, 3, 5))


def test_tropical_masses_are_best_path_costs_without_certificates() -> None:
    """Keep algebra-valued masses available when numeric argmax is meaningless."""
    base = plan(
        {"root": 0.0, "a1": 3.0, "a2": 4.0, "b": 2.5, "other": 6.0},
        (
            ("root", "a1"),
            ("root", "a2"),
            ("root", "b"),
            ("root", "other"),
        ),
        roots=("root",),
        semiring=cast(Semiring[object], TROPICAL),
    )
    emissions = Emissions.bind(
        base, {"a1": ("A",), "a2": ("A",), "b": ("B",), "other": ("other",)}
    )
    candidates = (("A",), ("B",))
    paths = enumerate_paths(base, emissions, cast(tuple[float, ...], base.values))
    masses = OutputPlan.prepare(base, emissions, candidates).masses()
    expected = tuple(
        min(path.value for path in paths if path.output == candidate)
        for candidate in candidates
    )
    assert masses.per_candidate == expected
    assert masses.total == min(path.value for path in paths)
    assert masses.residual == min(
        path.value for path in paths if path.output not in candidates
    )
    assert not masses.zero_mass
    assert masses.decided is None
    assert masses.tied is None
    data = masses.to_data(cast(Semiring[object], TROPICAL))
    assert json.loads(json.dumps(data, allow_nan=False)) == data


def test_log_probability_ties_compare_computed_doubles_exactly() -> None:
    """Pin that an approximate sum may split equal real candidate masses."""
    base = plan(
        {
            "root": 0.0,
            "a1": math.log(0.1),
            "a2": math.log(0.2),
            "b": math.log(0.3),
        },
        (("root", "a1"), ("root", "a2"), ("root", "b")),
        roots=("root",),
    )
    emissions = Emissions.bind(base, {"a1": ("A",), "a2": ("A",), "b": ("B",)})
    masses = OutputPlan.prepare(base, emissions, (("A",), ("B",))).masses()
    assert masses.per_candidate[0] != masses.per_candidate[1]
    assert masses.tied == (0,)


def test_output_results_have_strict_json_serialization() -> None:
    """Keep both public result records serializable through carrier encodings."""
    base = plan(
        {"root": 0.0, "sink": 0.0},
        (("root", "sink"),),
        roots=("root",),
    )
    output = OutputPlan.prepare(base, Emissions.bind(base, {"sink": ("a",)}), (("a",),))
    results = (
        output.masses(),
        output.item_marginals(0),
        output.item_marginals(0, (-math.inf, 0.0)),
    )
    carrier = cast(Semiring[object], LOG_PROBABILITY)
    for result in results:
        data = result.to_data(carrier)
        assert json.loads(json.dumps(data, allow_nan=False)) == data


def test_hash_seed_preserves_candidate_and_tie_order() -> None:
    """Kill output order derived from set or dictionary iteration."""
    script = """
import json
from tests.test_pathoutput import plan
from tiergraph import Emissions, OutputPlan
b = plan({'root': 0.0, 'a': 0.0, 'b': 0.0}, (('root', 'a'), ('root', 'b')), roots=('root',))
o = OutputPlan.prepare(b, Emissions.bind(b, {'a': ('a',), 'b': ('b',)}), (('b',), ('a',)))
m = o.masses()
print(json.dumps([o.candidates, m.per_candidate, m.tied]))
"""
    root = Path(__file__).resolve().parents[1]
    outputs = []
    for seed in ("0", "12345", "999"):
        environment = dict(
            os.environ, PYTHONHASHSEED=seed, PYTHONPATH=str(root / "src")
        )
        outputs.append(
            subprocess.check_output(
                [sys.executable, "-c", script], cwd=root, env=environment, text=True
            )
        )
    assert len(set(outputs)) == 1
    assert json.loads(outputs[0])[-1] == [0, 1]


def test_pathplan_import_does_not_load_pathoutput() -> None:
    """Keep the new composition out of PathPlan's import and preparation path."""
    script = "import sys; import tiergraph.pathplan; print('tiergraph.pathoutput' in sys.modules)"
    root = Path(__file__).resolve().parents[1]
    output = subprocess.check_output(
        [sys.executable, "-c", script],
        cwd=root,
        env=dict(os.environ, PYTHONPATH=str(root / "src")),
        text=True,
    )
    assert output.strip() == "False"
