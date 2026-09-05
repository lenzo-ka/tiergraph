"""Property tests for sanctioned and composed semirings."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, cast

import pytest
from hypothesis import given
from hypothesis import strategies as st
from hypothesis.strategies import DataObject, SearchStrategy

from tiergraph import semiring as semiring_module
from tiergraph.semiring import (
    ARCTIC,
    BOOLEAN,
    COUNTING,
    DECIMAL_ARCTIC,
    DECIMAL_TROPICAL,
    PATH,
    PATH_WITNESSES,
    TROPICAL,
    ArcticSemiring,
    BooleanSemiring,
    CountingSemiring,
    DecimalExtremumSemiring,
    DoubleExtremumSemiring,
    ExpectationSemiring,
    LawCheck,
    LexicographicSemiring,
    PathSemiring,
    PathValue,
    PathWitnessSemiring,
    ProductSemiring,
    SelectionSemiring,
    Semiring,
    TropicalSemiring,
    inexact_laws,
)

from .semiring_laws import (
    assert_add_associative,
    assert_add_commutative,
    assert_add_identity,
    assert_declared_add_selective,
    assert_declared_multiply_preserves_witness_order,
    assert_declared_multiply_strictly_order_preserving,
    assert_declared_no_zero_divisors,
    assert_declared_optional_laws,
    assert_declared_zero_sum_free,
    assert_left_distributive,
    assert_multiply_associative,
    assert_multiply_identity,
    assert_right_distributive,
    assert_zero_annihilates,
)

# This exponent range exercises fractional IEEE values while keeping every
# three-factor law evaluation inside the finite carrier.
FINITE_DOUBLES = st.floats(
    min_value=-1e100, max_value=1e100, allow_nan=False, allow_infinity=False
)
TROPICAL_VALUES = st.one_of(FINITE_DOUBLES, st.just(float("inf")))
ARCTIC_VALUES = st.one_of(FINITE_DOUBLES, st.just(float("-inf")))
DECIMALS = st.decimals(
    min_value=Decimal("-1e100"),
    max_value=Decimal("1e100"),
    allow_nan=False,
    allow_infinity=False,
    places=8,
)
COUNTING_VALUES = st.integers(min_value=0, max_value=1_000_000)
PATHS = st.lists(st.text(min_size=1, max_size=4), max_size=4).map(tuple)
WITNESS_VALUES: SearchStrategy[tuple[tuple[str, ...], ...]] = st.sets(
    PATHS, max_size=4
).map(lambda paths: tuple(sorted(paths)))
PATH_VALUES: SearchStrategy[PathValue] = st.one_of(
    st.just(PATH.zero),
    st.tuples(
        DECIMALS,
        st.sets(PATHS, min_size=1, max_size=4).map(lambda paths: tuple(sorted(paths))),
    ),
)


def test_declared_star_dispositions_and_warrants() -> None:
    """REGRESSION: shipped carriers state exact, per-operand star dispositions."""
    assert PATH.star is not None
    assert PATH.star.name == "zero-closed"
    positive = (Decimal(1), (("a",),))
    tied = (Decimal(0), (("a",),))
    assert PATH.star.admits(positive)
    assert PATH.star.close(positive) == PATH.one
    assert PATH.star.admits(PATH.one)
    assert not PATH.star.admits(tied)
    partial = PATH.one
    power = PATH.one
    sums = []
    for _ in range(4):
        power = PATH.multiply(power, tied)
        partial = PATH.add(partial, power)
        sums.append(partial)
    assert len(set(sums)) == 4
    with pytest.raises(ValueError, match=r"zero-closed warrant refuses operand.*0"):
        PATH.star.close(tied)

    assert PATH_WITNESSES.add_idempotent
    assert PATH_WITNESSES.star is None
    witness_partial = PATH_WITNESSES.one
    witness_power = PATH_WITNESSES.one
    witness_sums = []
    operand = (("a",),)
    for _ in range(4):
        witness_power = PATH_WITNESSES.multiply(witness_power, operand)
        witness_partial = PATH_WITNESSES.add(witness_partial, witness_power)
        witness_sums.append(witness_partial)
    assert len(set(witness_sums)) == 4

    assert COUNTING.star is None
    assert BOOLEAN.star.admits(False)
    assert BOOLEAN.star.close(False) is True
    assert not DECIMAL_TROPICAL.star.admits(Decimal(-1))
    assert DECIMAL_TROPICAL.star.admits(Decimal(0))
    assert DECIMAL_TROPICAL.star.admits(DECIMAL_TROPICAL.zero)
    assert DECIMAL_ARCTIC.star.admits(Decimal(0))
    assert TROPICAL.star.admits(0.0)
    assert ARCTIC.star.admits(0.0)


def test_star_is_explicit_and_not_a_component_conjunction() -> None:
    """REGRESSION: every implementation owns star and products do not derive it."""
    product = ProductSemiring(DECIMAL_TROPICAL, PATH_WITNESSES)
    assert PATH.star is not None
    assert product.star is None
    assert ProductSemiring(BOOLEAN, BOOLEAN).star is None
    assert DoubleExtremumSemiring(minimum=True).star is not None
    assert LexicographicSemiring(DECIMAL_TROPICAL, PATH_WITNESSES).star is None
    assert ExpectationSemiring(COUNTING).star is None
    implementations = (
        BooleanSemiring,
        CountingSemiring,
        DecimalExtremumSemiring,
        DoubleExtremumSemiring,
        TropicalSemiring,
        ArcticSemiring,
        PathWitnessSemiring,
        ProductSemiring,
        LexicographicSemiring,
        ExpectationSemiring,
        PathSemiring,
    )
    assert all("star" in implementation.__dict__ for implementation in implementations)
    assert "star" not in {
        "add_idempotent",
        "multiply_commutative",
        "zero_sum_free",
    }


@dataclass(frozen=True)
class SemiringCase:
    """A semiring paired with a strategy for its complete carrier."""

    name: str
    # Parametrization deliberately erases the carrier shared by these fields.
    semiring: Semiring[Any]
    values: SearchStrategy[Any]


PRODUCT = ProductSemiring(DECIMAL_TROPICAL, COUNTING)
SELECTIVE_PRODUCT = ProductSemiring(DECIMAL_TROPICAL, DECIMAL_ARCTIC)
EXPECTATION = ExpectationSemiring(COUNTING)
SELECTION = SelectionSemiring(DECIMAL_TROPICAL, 0, tie_invariant_payload=True)
CASES = (
    SemiringCase("boolean", BOOLEAN, st.booleans()),
    SemiringCase("double-tropical", TROPICAL, TROPICAL_VALUES),
    SemiringCase("double-arctic", ARCTIC, ARCTIC_VALUES),
    SemiringCase(
        "decimal-tropical",
        DECIMAL_TROPICAL,
        st.one_of(DECIMALS, st.just(DECIMAL_TROPICAL.zero)),
    ),
    SemiringCase(
        "decimal-arctic",
        DECIMAL_ARCTIC,
        st.one_of(DECIMALS, st.just(DECIMAL_ARCTIC.zero)),
    ),
    SemiringCase("counting", COUNTING, COUNTING_VALUES),
    SemiringCase("path", PATH, PATH_VALUES),
    SemiringCase("path-witnesses", PATH_WITNESSES, WITNESS_VALUES),
    SemiringCase(
        "product",
        PRODUCT,
        st.tuples(st.one_of(DECIMALS, st.just(DECIMAL_TROPICAL.zero)), COUNTING_VALUES),
    ),
    SemiringCase(
        "selective-components-product",
        SELECTIVE_PRODUCT,
        st.tuples(
            st.one_of(DECIMALS, st.just(DECIMAL_TROPICAL.zero)),
            st.one_of(DECIMALS, st.just(DECIMAL_ARCTIC.zero)),
        ),
    ),
    SemiringCase(
        "expectation", EXPECTATION, st.tuples(COUNTING_VALUES, COUNTING_VALUES)
    ),
    SemiringCase(
        "selection",
        SELECTION,
        st.one_of(st.tuples(DECIMALS, COUNTING_VALUES), st.just(SELECTION.zero)),
    ),
)


class BrokenAddIdentity:
    """A transparent semiring mutant with a broken left additive identity."""

    def __init__(self, base: Semiring[Any]) -> None:
        self.base = base
        self.zero = base.zero
        self.one = base.one

    def __getattr__(self, name: str) -> Any:
        return getattr(self.base, name)

    def add(self, left: Any, right: Any, /) -> Any:
        """Return zero instead of the right operand at the left identity."""
        if left == self.zero:
            return self.zero
        return self.base.add(left, right)

    def multiply(self, left: Any, right: Any, /) -> Any:
        """Delegate multiplication unchanged."""
        return self.base.multiply(left, right)

    def encode(self, value: Any, /) -> object:
        """Delegate encoding unchanged."""
        return self.base.encode(value)

    def decode(self, value: object, /) -> Any:
        """Delegate decoding unchanged."""
        return self.base.decode(value)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
@given(data=st.data())
def test_semiring_laws(case: SemiringCase, data: DataObject) -> None:
    """Every instance obeys exactly its declared laws."""
    a = data.draw(case.values, label="a")
    b = data.draw(case.values, label="b")
    c = data.draw(case.values, label="c")
    semiring = case.semiring
    assert_add_associative(semiring, a, b, c)
    assert_multiply_associative(semiring, a, b, c)
    assert_add_commutative(semiring, a, b)
    assert_add_identity(semiring, a)
    assert_multiply_identity(semiring, a)
    assert_zero_annihilates(semiring, a)
    assert_left_distributive(semiring, a, b, c)
    assert_right_distributive(semiring, a, b, c)
    assert_declared_optional_laws(semiring, a, b)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
@given(data=st.data())
def test_declared_selectivity_holds_over_each_carrier(
    case: SemiringCase, data: DataObject
) -> None:
    """Every selective declaration returns one of the sampled operands."""
    a = data.draw(case.values, label="a")
    b = data.draw(case.values, label="b")
    assert_declared_add_selective(case.semiring, a, b)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
@given(data=st.data())
def test_declared_strict_multiplication_order_holds_over_each_carrier(
    case: SemiringCase, data: DataObject
) -> None:
    """Every strict-order declaration holds over sampled nonzero operands."""
    a = data.draw(case.values, label="a")
    b = data.draw(case.values, label="b")
    c = data.draw(case.values, label="c")
    assert_declared_multiply_strictly_order_preserving(case.semiring, a, b, c)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
@given(data=st.data())
def test_declared_witness_order_is_preserved_by_multiplication(
    case: SemiringCase, data: DataObject
) -> None:
    """Every ranked-order declaration holds over sampled carrier operands."""
    a = data.draw(case.values, label="a")
    b = data.draw(case.values, label="b")
    c = data.draw(case.values, label="c")
    assert_declared_multiply_preserves_witness_order(case.semiring, a, b, c)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
@given(data=st.data())
def test_declared_zero_sum_freedom_holds_over_each_carrier(
    case: SemiringCase, data: DataObject
) -> None:
    """Every zero-sum-free declaration holds over sampled operands."""
    a = data.draw(case.values, label="a")
    b = data.draw(case.values, label="b")
    assert_declared_zero_sum_free(case.semiring, a, b)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
@given(data=st.data())
def test_declared_no_zero_divisors_holds_over_each_carrier(
    case: SemiringCase, data: DataObject
) -> None:
    """Every no-zero-divisors declaration holds over sampled operands."""
    a = data.draw(case.values, label="a")
    b = data.draw(case.values, label="b")
    assert_declared_no_zero_divisors(case.semiring, a, b)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_every_sanctioned_instance_catches_a_required_law_breakage(
    case: SemiringCase,
) -> None:
    """No sanctioned instance can opt out of its additive identity law."""
    with pytest.raises(AssertionError):
        assert_add_identity(BrokenAddIdentity(case.semiring), case.semiring.one)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
@given(data=st.data())
def test_carrier_encoding_round_trips_strict_json(
    case: SemiringCase, data: DataObject
) -> None:
    """Identities and operation results round-trip through strict JSON."""
    a = data.draw(case.values)
    b = data.draw(case.values)
    values = (
        case.semiring.zero,
        case.semiring.one,
        case.semiring.add(a, b),
        case.semiring.multiply(a, b),
    )
    for value in values:
        encoded = case.semiring.encode(value)
        strict_json = json.dumps(encoded, allow_nan=False)
        assert case.semiring.decode(json.loads(strict_json)) == value


def test_double_overflow_and_excluded_bounds_are_refused() -> None:
    """IEEE operations cannot silently leave their declared carrier."""
    with pytest.raises(OverflowError, match="result"):
        TROPICAL.multiply(-1e308, -1e308)
    with pytest.raises(ValueError, match="right"):
        TROPICAL.add(1.0, float("-inf"))
    assert TROPICAL.multiply(TROPICAL.zero, -1e308) == TROPICAL.zero


def test_double_associativity_is_approximate_but_bounded() -> None:
    """The known IEEE reassociation disagreement fits the declared ulp bound."""
    assert_multiply_associative(TROPICAL, 1e16, -1e16, 1.0)


@pytest.mark.parametrize("semiring", (TROPICAL, ARCTIC))
@given(a=FINITE_DOUBLES, b=FINITE_DOUBLES, c=FINITE_DOUBLES)
def test_ieee_distributivity_is_checked_exactly(
    semiring: Semiring[float], a: float, b: float, c: float
) -> None:
    """Monotone IEEE rounding preserves both extremum distributive laws."""
    assert_left_distributive(semiring, a, b, c)
    assert_right_distributive(semiring, a, b, c)


def test_path_rejects_a_finite_cost_without_witnesses() -> None:
    """A finite path value cannot inhabit the carrier without a witness."""
    with pytest.raises(ValueError, match="right"):
        PATH.multiply(PATH.one, (Decimal(2), ()))


def test_path_multiplication_preserves_order() -> None:
    """Derived path multiplication concatenates rather than commuting."""
    left: PathValue = (Decimal(1), (("left",),))
    right: PathValue = (Decimal(2), (("right",),))
    assert PATH.multiply(left, right) == (Decimal(3), (("left", "right"),))
    assert PATH.multiply(left, right) != PATH.multiply(right, left)


def test_lexicographic_side_conditions_are_refused() -> None:
    """Construction names each missing first-component side condition."""
    with pytest.raises(ValueError, match="exact multiply_associativity"):
        LexicographicSemiring(TROPICAL, PathWitnessSemiring())
    with pytest.raises(ValueError, match="add_selective"):
        LexicographicSemiring(COUNTING, BOOLEAN)
    with pytest.raises(ValueError, match="multiply_strictly_order_preserving"):
        LexicographicSemiring(BOOLEAN, COUNTING)


def test_lexicographic_refuses_ieee_tropical_strict_order() -> None:
    """IEEE tropical also lacks the strict order required for composition."""
    tropical = TropicalSemiring()
    tropical.multiply_associativity = LawCheck.EXACT
    with pytest.raises(ValueError, match="multiply_strictly_order_preserving"):
        LexicographicSemiring(tropical, PathWitnessSemiring())


def test_lexicographic_refuses_a_product_first_component() -> None:
    """Independent product choices do not satisfy additive selectivity."""
    first = ProductSemiring(DECIMAL_TROPICAL, DECIMAL_ARCTIC)
    assert first.add_selective is False
    with pytest.raises(ValueError, match="add_selective"):
        LexicographicSemiring(first, COUNTING)


def test_expectation_requires_a_commutative_base() -> None:
    """The mixed product refuses a base that cannot support its laws."""
    with pytest.raises(ValueError, match="exact multiply_associativity"):
        ExpectationSemiring(TROPICAL)
    with pytest.raises(ValueError, match="multiply_commutative"):
        ExpectationSemiring(PATH)


def test_composed_properties_are_derived() -> None:
    """A composition cannot strengthen a component's declarations."""

    class InvalidLawCheck(CountingSemiring):
        add_associativity = None  # type: ignore[assignment]

    mixed = ProductSemiring(DECIMAL_TROPICAL, TROPICAL)
    assert mixed.multiply_associativity is LawCheck.APPROXIMATE
    assert PRODUCT.add_idempotent is False
    assert ProductSemiring(DECIMAL_TROPICAL, DECIMAL_ARCTIC).add_selective is False
    assert ProductSemiring(BOOLEAN, BOOLEAN).no_zero_divisors is False
    assert mixed.multiply_strictly_order_preserving is False
    assert PRODUCT.multiply_preserves_witness_order is False
    assert PATH.multiply_commutative is False
    assert PATH.add_selective is False
    assert PATH.multiply_strictly_order_preserving is False
    assert PATH.multiply_preserves_witness_order
    assert PATH.no_zero_divisors
    assert PATH.multiply_associativity is LawCheck.EXACT
    assert PATH.left_distributivity is LawCheck.EXACT
    assert PATH.right_distributivity is LawCheck.EXACT
    assert EXPECTATION.add_selective is False
    assert EXPECTATION.multiply_strictly_order_preserving is False
    assert EXPECTATION.multiply_preserves_witness_order is False
    assert EXPECTATION.zero_sum_free
    assert EXPECTATION.no_zero_divisors is False
    assert PATH.left is DECIMAL_TROPICAL
    assert EXPECTATION.multiply_associativity is LawCheck.EXACT
    missing_property = "not_a_property"
    with pytest.raises(AttributeError):
        getattr(PRODUCT, missing_property)
    with pytest.raises(ValueError, match="invalid add_associativity"):
        _ = ProductSemiring(
            COUNTING,
            InvalidLawCheck(),  # type: ignore[arg-type]
        ).add_associativity


def test_exact_and_ieee_extrema_declare_different_strict_order() -> None:
    """Only exact extremum multiplication preserves every strict comparison."""
    assert DECIMAL_TROPICAL.multiply_strictly_order_preserving
    assert DECIMAL_ARCTIC.multiply_strictly_order_preserving
    assert TROPICAL.multiply_strictly_order_preserving is False
    assert ARCTIC.multiply_strictly_order_preserving is False


def test_carrier_refusals_name_invalid_values() -> None:
    """Each public carrier boundary refuses near-valid outsiders."""
    with pytest.raises(TypeError, match="Boolean"):
        BOOLEAN.add(1, False)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="nonnegative"):
        COUNTING.decode(-1)
    with pytest.raises(ValueError, match="XSD-decimal"):
        DECIMAL_TROPICAL.add(Decimal("NaN"), Decimal(0))
    with pytest.raises(ValueError, match="excluded"):
        DECIMAL_TROPICAL.add(Decimal("-Infinity"), Decimal(0))
    with pytest.raises(TypeError, match="string"):
        DECIMAL_TROPICAL.decode(1)
    with pytest.raises(ValueError, match="IEEE-double"):
        TROPICAL.add(float("nan"), 0.0)
    with pytest.raises(TypeError, match="string"):
        TROPICAL.decode(1)
    with pytest.raises(ValueError, match="two-element"):
        PRODUCT.decode([])


def test_lexicographic_and_path_witness_refusals() -> None:
    """Restricted lexicographic and witness carriers reject malformed values."""

    class NotZeroSumFree(CountingSemiring):
        zero_sum_free = False

    class HasZeroDivisors(CountingSemiring):
        no_zero_divisors = False

    with pytest.raises(ValueError, match="zero_sum_free"):
        LexicographicSemiring(DECIMAL_TROPICAL, NotZeroSumFree())
    with pytest.raises(ValueError, match="no_zero_divisors"):
        LexicographicSemiring(DECIMAL_TROPICAL, HasZeroDivisors())
    with pytest.raises(ValueError, match="both zeros"):
        PATH.encode((PATH.left.zero, (("orphan",),)))
    witnesses = PathWitnessSemiring()
    invalid_values: tuple[object, ...] = (
        [["duplicate"], ["duplicate"]],
        [[1]],
    )
    for invalid in invalid_values:
        with pytest.raises(ValueError):
            witnesses.decode(invalid)
    with pytest.raises(ValueError, match="array of arrays"):
        witnesses.decode(["not-a-path"])


def test_inexact_laws_names_the_unchecked_laws_in_precondition_order() -> None:
    """REGRESSION: the first name is stable, so a refusal can quote it."""
    assert inexact_laws(DECIMAL_TROPICAL) == ()
    assert inexact_laws(COUNTING) == ()
    assert inexact_laws(TROPICAL) == ("multiply_associativity",)
    assert inexact_laws(ARCTIC) == ("multiply_associativity",)


def test_inexact_laws_reports_the_declaration_and_not_the_behavior() -> None:
    """REGRESSION: an empty result is about the declaration, and can be a lie."""

    class Liar:
        add_associativity = multiply_associativity = LawCheck.EXACT
        add_commutativity = LawCheck.EXACT
        left_distributivity = right_distributivity = LawCheck.EXACT

    assert inexact_laws(cast(Semiring[object], Liar())) == ()


def test_composite_constructions_quote_the_first_unchecked_law() -> None:
    """REGRESSION: both composites refuse through the same shared law reader."""
    with pytest.raises(ValueError, match="lexicographic component lacks exact "):
        LexicographicSemiring(TROPICAL, PATH_WITNESSES)
    with pytest.raises(ValueError, match="expectation base lacks exact "):
        ExpectationSemiring(TROPICAL)


def module_singletons() -> set[str]:
    """Return the names the module binds to a semiring instance it defines."""
    return {
        name
        for name, value in vars(semiring_module).items()
        if not name.startswith("_")
        and not isinstance(value, type)
        and type(value).__module__ == semiring_module.__name__
    }


def test_every_semiring_the_module_defines_is_declared_public() -> None:
    """REGRESSION: an implementation or singleton absent from __all__ is unreachable."""
    declared = set(semiring_module.__all__)
    implementations = {
        name: value
        for name, value in vars(semiring_module).items()
        if not name.startswith("_")
        and isinstance(value, type)
        and value.__module__ == semiring_module.__name__
        and name.endswith("Semiring")
    }
    singletons = module_singletons()
    assert implementations and singletons
    assert sorted((set(implementations) | singletons) - declared) == []


def test_every_sanctioned_singleton_is_a_law_case() -> None:
    """REGRESSION: a singleton absent from CASES is a shipped carrier nothing checks.

    `CASES` is the denominator of every law suite in this module, so a
    singleton left out of it is exempt from the whole file while still being
    public.  `PATH_WITNESSES` was exactly that, and the omission is invisible
    unless the population is measured against the module rather than against a
    list someone remembered to extend.
    """
    covered = [case.semiring for case in CASES]
    assert (
        sorted(
            name
            for name in module_singletons()
            if not any(
                getattr(semiring_module, name) is instance for instance in covered
            )
        )
        == []
    )
