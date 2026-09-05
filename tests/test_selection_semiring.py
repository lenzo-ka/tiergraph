"""Selection-semiring behavior and fold integration."""

import json
from dataclasses import replace
from decimal import Decimal
from functools import reduce
from typing import cast

import pytest

from tests.semiring_laws import _assert_required_equal
from tiergraph.semiring import (
    COUNTING,
    DECIMAL_TROPICAL,
    LawCheck,
    ProductSemiring,
    SelectionSemiring,
    Semiring,
)


def selection() -> SelectionSemiring[Decimal, int]:
    """Build the scalar payload selection used by the examples."""
    return SelectionSemiring(DECIMAL_TROPICAL, 0, tie_invariant_payload=True)


def test_selection_keeps_the_winning_payload() -> None:
    """Selection carries the payload from the minimum-cost operand."""
    values = tuple(
        (Decimal(cost), payload) for cost, payload in ((3, 5), (2, 9), (4, 1))
    )
    selected = reduce(selection().add, values)
    product = reduce(ProductSemiring(DECIMAL_TROPICAL, COUNTING).add, values)
    assert selected == (Decimal(2), 9)
    assert product != selected


def test_selection_ties_keep_the_first_operand() -> None:
    """Reversing tied operands reverses the selected payload."""
    algebra = selection()
    left = (Decimal(2), 5)
    right = (Decimal(2), 9)
    assert algebra.add(left, right) == left
    assert algebra.add(right, left) == right


def test_selection_requires_tie_invariance_declaration() -> None:
    """The required declaration is named when it is omitted or false."""
    with pytest.raises(TypeError, match="tie_invariant_payload"):
        SelectionSemiring(DECIMAL_TROPICAL, 0)  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="tie_invariant_payload"):
        SelectionSemiring(DECIMAL_TROPICAL, 0, tie_invariant_payload=False)


def test_selection_law_declarations_follow_the_cost_except_commutativity() -> None:
    """Cost laws are inherited while ordered ties deny commutativity."""
    algebra = selection()
    assert algebra.add_commutativity is LawCheck.NOT_HELD
    assert algebra.add_idempotent is DECIMAL_TROPICAL.add_idempotent
    assert algebra.add_associativity is DECIMAL_TROPICAL.add_associativity
    assert algebra.multiply_associativity is DECIMAL_TROPICAL.multiply_associativity
    assert algebra.left_distributivity is DECIMAL_TROPICAL.left_distributivity
    assert algebra.right_distributivity is DECIMAL_TROPICAL.right_distributivity


def test_selection_carrier_boundary_and_codecs() -> None:
    """Selection validates zero payloads, law checks, and encoded pairs."""
    algebra = SelectionSemiring(
        DECIMAL_TROPICAL,
        0,
        tie_invariant_payload=True,
        payload_encode=lambda value: str(value),
        payload_decode=lambda value: int(str(value)),
    )
    assert algebra.star is None
    assert algebra.decode(algebra.encode((Decimal(2), 9))) == (Decimal(2), 9)
    with pytest.raises(ValueError, match="payload_identity"):
        algebra.add((DECIMAL_TROPICAL.zero, 9), algebra.zero)
    with pytest.raises(ValueError, match="two-element array"):
        algebra.decode([])

    class InvalidLawCost:
        add_associativity = "invalid"

    invalid_cost = cast(Semiring[Decimal], cast(object, InvalidLawCost()))
    with pytest.raises(ValueError, match="selection cost.*add_associativity"):
        _ = replace(algebra, cost=invalid_cost).add_associativity


def test_selection_refuses_a_payload_identity_the_operation_does_not_preserve() -> None:
    """A near-valid payload identity is checked against its operation."""
    with pytest.raises(ValueError, match="payload_identity.*payload_multiply"):
        SelectionSemiring(DECIMAL_TROPICAL, 1, tie_invariant_payload=True)

    def refusing_operation(left: int, right: int, /) -> int:
        del left, right
        raise TypeError("unsupported payload")

    with pytest.raises(ValueError, match="payload_identity.*payload_multiply"):
        SelectionSemiring(
            DECIMAL_TROPICAL,
            0,
            tie_invariant_payload=True,
            payload_multiply=refusing_operation,
        )


def test_not_held_is_only_valid_for_add_commutativity() -> None:
    """No other mandatory law can opt out with NOT_HELD."""
    with pytest.raises(AssertionError, match="multiply_associativity"):
        _assert_required_equal(
            LawCheck.NOT_HELD,
            1,
            2,
            (1, 2),
            law="multiply_associativity",
        )


def test_selection_not_held_commutativity_has_a_tie_witness() -> None:
    """The declared failure of commutativity has concrete carrier operands."""
    algebra = selection()
    left = (Decimal(2), 5)
    right = (Decimal(2), 9)
    assert algebra.add_commutativity is LawCheck.NOT_HELD
    assert algebra.add(left, right) != algebra.add(right, left)


def test_selection_normalizes_a_zero_divisor_product() -> None:
    """A cost-zero product carries the selection payload identity."""

    class ZeroDivisorCost:
        zero = 0

        def multiply(self, left: int, right: int, /) -> int:
            del left, right
            return self.zero

    cost = cast(Semiring[int], cast(object, ZeroDivisorCost()))
    algebra: SelectionSemiring[int, tuple[str, ...]] = SelectionSemiring(
        cost, (), tie_invariant_payload=True
    )
    assert algebra.multiply((2, ("left",)), (3, ("right",))) == algebra.zero


def test_selection_default_codec_round_trips_tuple_witnesses() -> None:
    """The default tuple-witness codec is strict JSON and round-trips."""
    algebra: SelectionSemiring[Decimal, tuple[str, ...]] = SelectionSemiring(
        DECIMAL_TROPICAL, (), tie_invariant_payload=True
    )
    value = (Decimal(2), ("a", "b"))
    encoded = algebra.encode(value)
    serialized = json.dumps(encoded)
    assert algebra.decode(json.loads(serialized)) == value
