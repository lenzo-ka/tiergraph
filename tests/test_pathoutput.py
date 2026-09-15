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


def logsum(values: Sequence[float]) -> float:
    """Add log weights with the shipped carrier."""
    total = -math.inf
    for value in values:
        total = LOG_PROBABILITY.add(total, value)
    return total


@dataclass(frozen=True)
class Enumerated:
    """One complete base path and its emitted output."""

    items: tuple[int, ...]
    output: tuple[str, ...]
    value: float


def enumerate_paths(
    base: PathPlan[object], emissions: Emissions[object], values: Sequence[float]
) -> tuple[Enumerated, ...]:
    """Enumerate the small fixture's root-to-sink paths."""
    paths: list[Enumerated] = []

    def visit(index: int, seen: tuple[int, ...], output: tuple[str, ...]) -> None:
        following = seen + (index,)
        emitted = output + emissions.per_item[index]
        if not base.children[index]:
            value = sum(values[item] for item in following)
            paths.append(Enumerated(following, emitted, value))
            return
        for child in base.children[index]:
            visit(child, following, emitted)

    for root in base.roots:
        visit(root, (), ())
    return tuple(paths)


def assert_log_close(actual: float, expected: float) -> None:
    """Compare log masses, including their shared zero."""
    assert actual == expected or math.isclose(actual, expected, abs_tol=1e-12)


def test_random_dag_matches_brute_force_oracle() -> None:
    """Kill prefix, epsilon-closure, offset, root, sink, and pooling mutations."""
    generator = random.Random(1949)
    for _case in range(24):
        labels = tuple(f"n{index}" for index in range(8))
        weights = {
            label: (-math.inf if index == 6 else math.log(generator.uniform(0.1, 1.0)))
            for index, label in enumerate(labels)
        }
        edges = tuple(
            (labels[left], labels[right])
            for left in range(len(labels))
            for right in range(left + 1, len(labels))
            if generator.random() < 0.22
        )
        base = plan(weights, edges, roots=(labels[0], labels[2]))
        choices = ((), ("a",), ("a", "b"), ("z",))
        emission_map = {
            label: generator.choice(choices)
            for label in labels
            if generator.random() < 0.8
        }
        emissions = Emissions.bind(base, emission_map)
        all_paths = enumerate_paths(
            base, emissions, cast(tuple[float, ...], base.values)
        )
        outputs = sorted({path.output for path in all_paths})
        candidates = tuple(outputs[:2] or [()])
        output = OutputPlan.prepare(base, emissions, candidates)
        masses = output.masses()
        expected_total = logsum(tuple(path.value for path in all_paths))
        assert_log_close(cast(float, masses.total), expected_total)
        for index, candidate in enumerate(candidates):
            matching = tuple(path for path in all_paths if path.output == candidate)
            expected = logsum(tuple(path.value for path in matching))
            assert_log_close(cast(float, masses.per_candidate[index]), expected)
            assert output.accepted[index] is bool(matching)
            if not matching:
                with pytest.raises(ValueError, match="structurally accepted"):
                    output.conditioned(index)
                continue
            item_marginals = output.item_marginals(index)
            if expected == -math.inf:
                assert item_marginals.zero_mass
                assert item_marginals.values is None
                continue
            assert not item_marginals.zero_mass
            assert item_marginals.values is not None
            for item in range(len(base.items)):
                through = logsum(
                    tuple(path.value for path in matching if item in path.items)
                )
                assert_log_close(cast(float, item_marginals.values[item]), through)
        residual = logsum(
            tuple(path.value for path in all_paths if path.output not in candidates)
        )
        assert_log_close(cast(float, masses.residual), residual)
        assert masses.cost == output.plan.marginals(output.values()).cost


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
    """Kill falling back to parentless-root inference in the product."""
    base = plan(
        {"first": 0.0, "second": 0.0, "sink": 0.0},
        (("first", "second"), ("second", "sink")),
        roots=("first", "second"),
    )
    emissions = Emissions.bind(base, {"sink": ("right",)})
    output = OutputPlan.prepare(base, emissions, (("right",),))
    assert output.plan.roots
    assert cast(float, output.masses().per_candidate[0]) == pytest.approx(math.log(2))


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
