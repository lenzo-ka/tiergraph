"""The reference action surface satisfies the reusable action laws."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import cast

import pytest

from tests.conformance.action import (
    ActionLawSuite,
    ActionToleranceLawSuite,
    SemimoduleLawSuite,
)
from tests.test_fold import declaration
from tiergraph.action import (
    ActionDeclaration,
    ActionEquivalenceError,
    DistributionWitness,
    ReactDeclaration,
    ReactMode,
    Semimodule,
    WitnessCoordinate,
    YieldNormalization,
)
from tiergraph.fold import Provenance
from tiergraph.semiring import DECIMAL_TROPICAL, Semiring

POSITIONS = {"start": 0, "bed": 4, "sting": 8, "out": 12}


def coordinates(provenance: Provenance) -> tuple[WitnessCoordinate, ...]:
    """Extract coordinates in hostile arrival order to expose order dependence."""
    return tuple(
        WitnessCoordinate((POSITIONS[label],), POSITIONS[label])
        for path in reversed(provenance)
        for label in reversed(path)
    )


def mix(carrier: object, values: tuple[object, ...]) -> object:
    """Add unit gain at each integer coordinate to a mapping carrier."""
    levels = dict(cast(dict[int, int], carrier))
    for value in values:
        coordinate = cast(int, value)
        levels[coordinate] = levels.get(coordinate, 0) + 1
    return {key: levels[key] for key in sorted(levels)}


def append(carrier: object, values: tuple[object, ...]) -> object:
    """Append coordinates to an ordered effect carrier."""
    return [*cast(list[object], carrier), *values]


def mark(carrier: object, values: tuple[object, ...]) -> object:
    """Mark coordinate presence without accumulating repeated applications."""
    marked = dict(cast(dict[str, int], carrier))
    for value in values:
        marked[str(cast(int, value))] = 1
    return {key: marked[key] for key in sorted(marked)}


def add_values(carrier: object, values: tuple[object, ...]) -> object:
    """Add integer action values to an integer carrier."""
    return cast(int, carrier) + sum(cast(int, value) for value in values)


def _product(values: tuple[int, ...]) -> int:
    """Multiply action values, with the empty action as identity."""
    result = 1
    for value in values:
        result *= value
    return result


GAIN_MODULE = Semimodule[object, object](
    0,
    1,
    lambda left, right: cast(int, left) + cast(int, right),
    lambda left, right: cast(int, left) * cast(int, right),
    0,
    lambda left, right: cast(int, left) + cast(int, right),
    lambda scalar, value: cast(int, scalar) * cast(int, value),
    (0, 1, 3),
    (0, 2, 5),
)

MIX = ActionDeclaration[object, object](
    "gain-mix",
    mix,
    associative=True,
    idempotent=False,
    commutative=True,
)
CHAIN = ActionDeclaration[object, object](
    "effect-chain", append, associative=True, idempotent=False, commutative=False
)
MARK = ActionDeclaration[object, object](
    "coordinate-mark",
    mark,
    associative=True,
    idempotent=True,
    commutative=True,
)
ADD = ActionDeclaration[object, object](
    "coordinate-sum", add_values, associative=True, idempotent=False, commutative=True
)
SCALE = ActionDeclaration[object, object](
    "gain-scale",
    lambda carrier, values: (
        cast(int, carrier) * _product(tuple(cast(int, value) for value in values))
    ),
    associative=True,
    idempotent=False,
    commutative=True,
    semimodule=GAIN_MODULE,
)


def transactional(carrier: object) -> ReactDeclaration[object, object, object]:
    """Build the action-law declaration independently of its carrier value."""
    del carrier
    return ReactDeclaration(
        "mix-react",
        declaration(),
        coordinates,
        MIX,
    )


def test_action_laws() -> None:
    """Recognition stays carrier-independent across unrelated action carriers."""
    suite = ActionLawSuite(transactional, ({}, {99: 7}))
    suite.check_carrier_substitution()
    suite.check_one_for_one_equivalence()


def test_semimodule_claim_satisfies_bound_laws() -> None:
    """The reusable suite rechecks an already validated declaration."""
    SemimoduleLawSuite(SCALE).check_laws()
    assert CHAIN.semimodule is None


def test_gain_mix_does_not_claim_integer_semimodule_scaling() -> None:
    """Coordinate mixing is not the claimed integer scaling operation."""
    assert MIX.semimodule is None
    with pytest.raises(ValueError, match=r"gain-mix.*does not implement.*scale"):
        replace(MIX, semimodule=GAIN_MODULE)


def test_semimodule_claim_is_bound_to_its_action() -> None:
    """A valid detached module cannot launder an unrelated ordered action."""
    with pytest.raises(ValueError, match=r"effect-chain.*does not implement.*scale"):
        replace(CHAIN, semimodule=GAIN_MODULE)


def test_malformed_semimodule_claim_is_refused_where_it_is_constructed() -> None:
    """A runtime-local claim cannot bypass sampled algebra validation."""
    malformed = replace(GAIN_MODULE, scalar_add=lambda left, right: 99)
    with pytest.raises(ValueError, match=r"runtime-scale.*scalar additive identity"):
        replace(SCALE, name="runtime-scale", semimodule=malformed)


@pytest.mark.parametrize(
    "suite",
    [
        ActionToleranceLawSuite(MIX, {}, (0, 8), (12,)),
        ActionToleranceLawSuite(MARK, {}, (0, 8), (12,)),
        ActionToleranceLawSuite(CHAIN, [], (0, 8), (12,)),
    ],
    ids=lambda suite: suite.action.name,
)
def test_action_tolerance_claims(suite: ActionToleranceLawSuite) -> None:
    """Run the reusable laws each action selected in its declaration."""
    suite.check_claims()


@pytest.mark.parametrize(
    ("normalization", "action", "message"),
    [
        (
            YieldNormalization(collapse=True),
            replace(MARK, associative=False),
            "collapse",
        ),
        (
            YieldNormalization(collapse=True),
            MIX,
            "collapse",
        ),
        (YieldNormalization(unique=True), MIX, "uniquing"),
        (YieldNormalization(reorder=True), CHAIN, "reordering"),
    ],
)
def test_normalization_mismatch_is_refused_at_declaration(
    normalization: YieldNormalization,
    action: ActionDeclaration[object, object],
    message: str,
) -> None:
    """Each complete-yield transformation requires its matching tolerance."""
    with pytest.raises(ValueError, match=rf"{message}.*{action.name}"):
        ReactDeclaration(
            "bad-normalization",
            declaration(),
            coordinates,
            action,
            normalization,
        )


def test_unique_requires_commutativity_and_names_the_action() -> None:
    """Global duplicate removal refuses an order-sensitive idempotent action."""
    with pytest.raises(ValueError, match=r"uniquing.*commutative.*effect-chain"):
        ReactDeclaration(
            "bad-unique-order",
            declaration("tie"),
            coordinates,
            replace(CHAIN, idempotent=True),
            YieldNormalization(unique=True),
        )


def test_distribution_witness_rejects_the_bound_nonlinear_action() -> None:
    """The actual yield exposes a non-distributing action on its executed run."""
    nonlinear = replace(
        ADD,
        name="square-sum",
        apply=lambda carrier, values: (
            cast(int, carrier) + sum(cast(int, value) for value in values) ** 2
        ),
    )
    witness = DistributionWitness("bound-paths")
    with pytest.raises(
        ActionEquivalenceError,
        match=r"bound-paths.*one-for-one.*transactional.*square-sum.*!=",
    ):
        ReactDeclaration(
            "bad-per-item",
            declaration(),
            coordinates,
            nonlinear,
            mode=ReactMode.ONE_FOR_ONE,
            distribution=witness,
        ).run(0)


def test_distribution_witness_refuses_transactional_mode_at_construction() -> None:
    """A supplied witness cannot be silently inert in the default mode."""
    with pytest.raises(
        ValueError, match=r"react 'default-mode'.*one-for-one.*'transactional'"
    ):
        ReactDeclaration(
            "default-mode",
            declaration(),
            coordinates,
            ADD,
            distribution=DistributionWitness("bound-paths"),
        )


def test_equivalence_refusal_is_carrier_dependent_and_names_results() -> None:
    """Certification accepts and refuses concrete carriers at execution time."""

    def carrier_sensitive(carrier: object, values: tuple[object, ...]) -> object:
        total = sum(cast(int, value) for value in values)
        multiplier = 2 if len(values) == 1 and cast(int, carrier) >= 100 else 1
        return cast(int, carrier) + multiplier * total

    action = replace(ADD, name="carrier-sensitive", apply=carrier_sensitive)
    react = ReactDeclaration(
        "carrier-bound",
        declaration(),
        coordinates,
        action,
        mode=ReactMode.ONE_FOR_ONE,
        distribution=DistributionWitness("per-run"),
    )
    assert react.run(50)["result"] == 70
    with pytest.raises(
        ActionEquivalenceError,
        match=r"carrier-bound.*carrier-sensitive.*240 != 220",
    ):
        react.run(200)


def test_equivalence_certification_controls_batch_computation() -> None:
    """Only an opted-in one-for-one run also invokes the complete batch."""
    lengths: list[int] = []

    def measured(carrier: object, values: tuple[object, ...]) -> object:
        lengths.append(len(values))
        return add_values(carrier, values)

    action = replace(ADD, name="measured-sum", apply=measured)
    base = ReactDeclaration(
        "measured", declaration(), coordinates, action, mode=ReactMode.ONE_FOR_ONE
    )
    assert base.run(0)["result"] == 20
    assert lengths == [1, 1, 1]

    lengths.clear()
    certified = replace(base, distribution=DistributionWitness("measured-equivalence"))
    assert certified.run(0)["result"] == 20
    assert lengths == [3, 1, 1, 1]


def test_action_law_suite_exposes_broken_mode_equivalence() -> None:
    """The reusable equivalence law fails for a nonlinear action."""
    nonlinear = replace(
        ADD,
        name="square-sum-law",
        apply=lambda carrier, values: (
            cast(int, carrier) + sum(cast(int, value) for value in values) ** 2
        ),
    )

    def broken(carrier: object) -> ReactDeclaration[object, object, object]:
        del carrier
        return ReactDeclaration("broken-law", declaration(), coordinates, nonlinear)

    with pytest.raises(AssertionError, match=r"one-for-one.*transactional"):
        ActionLawSuite(broken, (0, 10)).check_one_for_one_equivalence()


def test_one_for_one_certification_is_optional_and_normalization_is_forbidden() -> None:
    """Per-recognition execution need not double-compute, but cannot normalize."""
    fold = declaration()
    accepted = ReactDeclaration(
        "per-item", fold, coordinates, MIX, mode=ReactMode.ONE_FOR_ONE
    ).run({})
    assert accepted["result"] == {0: 1, 8: 1, 12: 1}
    witness = DistributionWitness("bound-paths")
    with pytest.raises(ValueError, match=r"one-for-one.*cannot normalize"):
        ReactDeclaration(
            "per-item-normalized",
            fold,
            coordinates,
            replace(MIX, idempotent=True),
            YieldNormalization(unique=True),
            ReactMode.ONE_FOR_ONE,
            witness,
        )


def test_structural_order_overrides_yield_arrival_for_ordered_action() -> None:
    """The chain follows positions even though extraction returns reverse arrival."""
    result = ReactDeclaration("ordered", declaration(), coordinates, CHAIN).run([])
    assert result["result"] == [0, 8, 12]


def test_page_sized_tie_exposes_double_counting_before_action() -> None:
    """The tied diamond names the shared endpoints twice and uniquing removes them."""
    tied = declaration("tie")
    raw = ReactDeclaration("raw-tie", tied, coordinates, MIX).run({})
    assert raw["result"] == {0: 2, 4: 1, 8: 1, 12: 2}
    unique = ReactDeclaration(
        "unique-tie",
        tied,
        coordinates,
        MARK,
        YieldNormalization(unique=True),
    ).run({})
    assert unique["result"] == {"0": 1, "4": 1, "8": 1, "12": 1}


def test_collapse_refuses_counting_and_accepts_idempotent_action() -> None:
    """Adjacent duplicate removal is gated by its result-preserving property."""
    with pytest.raises(ValueError, match=r"collapse.*idempotent.*gain-mix"):
        ReactDeclaration(
            "bad-collapse",
            declaration("tie"),
            coordinates,
            MIX,
            YieldNormalization(collapse=True),
        )
    accepted = ReactDeclaration(
        "mark-collapse",
        declaration("tie"),
        coordinates,
        MARK,
        YieldNormalization(collapse=True),
    ).run({})
    assert accepted["result"] == {"0": 1, "4": 1, "8": 1, "12": 1}


def test_collapse_accepts_noncommutative_idempotent_action() -> None:
    """Collapse requires associativity and idempotence, not commutativity."""
    action = replace(CHAIN, idempotent=True)
    accepted = ReactDeclaration(
        "chain-collapse",
        declaration("tie"),
        coordinates,
        action,
        YieldNormalization(collapse=True),
    ).run([])
    assert accepted["result"] == [0, 4, 8, 12]


def test_action_result_must_be_strict_json() -> None:
    """A public carrier result names its action when it cannot be serialized."""
    bad = replace(MIX, name="opaque", apply=lambda carrier, values: {Decimal("1")})
    declaration_value = ReactDeclaration("bad-result", declaration(), coordinates, bad)
    with pytest.raises(ValueError, match=r"action 'opaque' result.*strict-JSON"):
        declaration_value.run({})


def test_normalization_operations_execute_on_hostile_yield() -> None:
    """Collapse, unique, and reorder each change a measured hostile sequence."""
    values = (
        WitnessCoordinate((3,), 2),
        WitnessCoordinate((1,), 1),
        WitnessCoordinate((2,), 1),
        WitnessCoordinate((0,), 2),
    )
    assert tuple(
        item.value for item in YieldNormalization(collapse=True).apply(values)
    ) == (2, 1, 2)
    assert tuple(
        item.value for item in YieldNormalization(unique=True).apply(values)
    ) == (2, 1)
    assert tuple(
        item.value for item in YieldNormalization(reorder=True).apply(values)
    ) == (1, 1, 2, 2)


def test_declaration_and_distribution_names_are_required() -> None:
    """Empty declaration and certificate diagnostics are refused."""
    with pytest.raises(ValueError, match="action name"):
        replace(MIX, name="")
    with pytest.raises(ValueError, match="react name"):
        replace(transactional({}), name="")
    with pytest.raises(ValueError, match="witness name"):
        DistributionWitness("")


@pytest.mark.parametrize("bridge", [lambda value: (), lambda value: (0,)])
def test_distribution_witness_has_no_caller_supplied_coordinate_bridge(
    bridge: object,
) -> None:
    """Vacuous and constant legacy bridges cannot enter a certificate."""
    with pytest.raises(TypeError, match="positional"):
        DistributionWitness("launder", (1, 2), 0, bridge)  # type: ignore[call-arg]


def test_one_for_one_executes_each_structurally_ordered_coordinate() -> None:
    """A distributing action receives each structurally ordered coordinate."""
    witness = DistributionWitness("bound-paths")
    result = ReactDeclaration(
        "per-item",
        declaration(),
        coordinates,
        MARK,
        mode=ReactMode.ONE_FOR_ONE,
        distribution=witness,
    ).run({})
    assert result["result"] == {"0": 1, "8": 1, "12": 1}


def test_missing_witness_and_opaque_coordinate_are_named() -> None:
    """Yield failures identify the react declaration before action begins."""
    scalar = declaration("cost", cast(Semiring[object], DECIMAL_TROPICAL))
    with pytest.raises(ValueError, match=r"no-witness.*no witnesses"):
        ReactDeclaration("no-witness", scalar, coordinates, MIX).run({})

    def opaque(provenance: Provenance) -> tuple[WitnessCoordinate, ...]:
        """Return one coordinate whose value cannot cross the public boundary."""
        del provenance
        return (WitnessCoordinate((0,), {Decimal("1")}),)

    with pytest.raises(ValueError, match=r"opaque-coordinate.*coordinate.*strict-JSON"):
        ReactDeclaration("opaque-coordinate", declaration(), opaque, MIX).run({})
