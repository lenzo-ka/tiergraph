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
    semimodule=GAIN_MODULE,
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


def path_coordinates(value: object) -> tuple[object, ...]:
    """Map a sampled path-semiring value to its witness labels as integers."""
    return tuple(
        POSITIONS[label]
        for path in cast(tuple[Decimal, tuple[tuple[str, ...], ...]], value)[1]
        for label in path
    )


PATH_SAMPLES: tuple[object, ...] = (
    (Decimal(1), (("start",),)),
    (Decimal(1), (("bed",),)),
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
    ActionLawSuite(transactional, ({}, {99: 7})).check_carrier_substitution()


def test_semimodule_laws_are_conditional_on_the_claim() -> None:
    """Mixing is checked as a semimodule while an ordered chain makes no claim."""
    assert MIX.semimodule is not None
    SemimoduleLawSuite(MIX.semimodule).check_laws()
    assert CHAIN.semimodule is None


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


def test_distribution_witness_rejects_the_bound_nonlinear_action() -> None:
    """An unrelated identity claim cannot admit a non-distributing action."""
    nonlinear = replace(
        ADD,
        name="square-sum",
        apply=lambda carrier, values: (
            cast(int, carrier) + sum(cast(int, value) for value in values) ** 2
        ),
    )
    witness = DistributionWitness("bound-paths", PATH_SAMPLES, 0, path_coordinates)
    with pytest.raises(ValueError, match=r"bound-paths.*bound action"):
        ReactDeclaration(
            "bad-stream",
            declaration(),
            coordinates,
            nonlinear,
            mode=ReactMode.ONE_FOR_ONE,
            distribution=witness,
        )


def test_one_for_one_requires_distribution_and_forbids_normalization() -> None:
    """Streaming declarations require the executable condition, not weaker flags."""
    fold = declaration()
    with pytest.raises(ValueError, match=r"one-for-one.*no distribution witness"):
        ReactDeclaration("stream", fold, coordinates, MIX, mode=ReactMode.ONE_FOR_ONE)
    witness = DistributionWitness("bound-paths", PATH_SAMPLES, 0, path_coordinates)
    with pytest.raises(ValueError, match=r"one-for-one.*cannot normalize"):
        ReactDeclaration(
            "stream-normalized",
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


def test_declaration_names_and_distribution_samples_are_required() -> None:
    """Empty diagnostics and vacuous homomorphism witnesses are refused."""
    with pytest.raises(ValueError, match="action name"):
        replace(MIX, name="")
    with pytest.raises(ValueError, match="react name"):
        replace(transactional({}), name="")
    with pytest.raises(ValueError, match="witness name"):
        DistributionWitness("", (1,), 0, lambda value: (value,))
    with pytest.raises(ValueError, match="has no samples"):
        DistributionWitness("empty", (), 0, lambda value: (value,))


def test_one_for_one_executes_each_structurally_ordered_coordinate() -> None:
    """A distributing action is admitted and streams each ordered coordinate."""
    witness: DistributionWitness[object, object] = DistributionWitness(
        "bound-paths", PATH_SAMPLES, {}, path_coordinates
    )
    result = ReactDeclaration(
        "stream",
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
