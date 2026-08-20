"""Mutation pins for the reusable semiring laws."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pytest

from tiergraph.semiring import LawCheck

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

Operation = Callable[[int, int], int]


@dataclass
class MutantSemiring:
    """An integer semiring whose operations can be replaced independently."""

    add_operation: Operation = lambda left, right: left + right
    multiply_operation: Operation = lambda left, right: left * right
    zero: int = 0
    one: int = 1
    add_idempotent: bool = False
    multiply_commutative: bool = False
    add_associativity: LawCheck = LawCheck.EXACT
    multiply_associativity: LawCheck = LawCheck.EXACT
    add_commutativity: LawCheck = LawCheck.EXACT
    left_distributivity: LawCheck = LawCheck.EXACT
    right_distributivity: LawCheck = LawCheck.EXACT
    add_selective: bool = False
    multiply_strictly_order_preserving: bool = False
    multiply_preserves_witness_order: bool = False
    zero_sum_free: bool = True
    no_zero_divisors: bool = True

    def add(self, left: int, right: int, /) -> int:
        """Apply the selected addition mutant."""
        return self.add_operation(left, right)

    def multiply(self, left: int, right: int, /) -> int:
        """Apply the selected multiplication mutant."""
        return self.multiply_operation(left, right)

    def encode(self, value: int, /) -> object:
        """Encode an integer mutant value."""
        return value

    def decode(self, value: object, /) -> int:
        """Decode an integer mutant value."""
        assert isinstance(value, int)
        return value


def test_add_associativity_pin_rejects_a_mutant() -> None:
    """The addition associativity pin rejects weighted addition."""
    mutant = MutantSemiring(add_operation=lambda left, right: left + 2 * right)
    with pytest.raises(AssertionError):
        assert_add_associative(mutant, 1, 2, 3)


def test_multiply_associativity_pin_rejects_a_mutant() -> None:
    """The multiplication associativity pin rejects a weighted product."""
    mutant = MutantSemiring(multiply_operation=lambda left, right: left + 2 * right)
    with pytest.raises(AssertionError):
        assert_multiply_associative(mutant, 1, 2, 3)


def test_add_commutativity_pin_rejects_a_mutant() -> None:
    """The addition commutativity pin rejects left projection."""
    mutant = MutantSemiring(add_operation=lambda left, _right: left)
    with pytest.raises(AssertionError):
        assert_add_commutative(mutant, 1, 2)


def test_add_identity_pin_rejects_a_mutant() -> None:
    """The addition identity pin rejects an offset sum."""
    mutant = MutantSemiring(add_operation=lambda left, right: left + right + 1)
    with pytest.raises(AssertionError):
        assert_add_identity(mutant, 2)


def test_multiply_identity_pin_rejects_a_mutant() -> None:
    """The multiplication identity pin rejects an offset product."""
    mutant = MutantSemiring(multiply_operation=lambda left, right: left * right + 1)
    with pytest.raises(AssertionError):
        assert_multiply_identity(mutant, 2)


def test_zero_annihilation_pin_rejects_a_mutant() -> None:
    """The annihilation pin rejects multiplication implemented as addition."""
    mutant = MutantSemiring(multiply_operation=lambda left, right: left + right)
    with pytest.raises(AssertionError):
        assert_zero_annihilates(mutant, 2)


def test_left_distributivity_pin_rejects_a_mutant() -> None:
    """The left distributivity pin rejects an offset product."""
    mutant = MutantSemiring(multiply_operation=lambda left, right: left * right + 1)
    with pytest.raises(AssertionError):
        assert_left_distributive(mutant, 2, 3, 4)


def test_right_distributivity_pin_rejects_a_mutant() -> None:
    """The right distributivity pin rejects an offset product."""
    mutant = MutantSemiring(multiply_operation=lambda left, right: left * right + 1)
    with pytest.raises(AssertionError):
        assert_right_distributive(mutant, 2, 3, 4)


def test_declared_idempotence_pin_rejects_a_mutant() -> None:
    """The optional idempotence pin rejects ordinary addition."""
    mutant = MutantSemiring(add_idempotent=True)
    with pytest.raises(AssertionError):
        assert_declared_optional_laws(mutant, 2, 3)


def test_declared_multiply_commutativity_pin_rejects_a_mutant() -> None:
    """The optional commutativity pin rejects left projection."""
    mutant = MutantSemiring(
        multiply_operation=lambda left, _right: left,
        multiply_commutative=True,
    )
    with pytest.raises(AssertionError):
        assert_declared_optional_laws(mutant, 2, 3)


def test_declared_selectivity_pin_rejects_a_mutant() -> None:
    """The selectivity pin rejects addition that returns a third value."""
    mutant = MutantSemiring(add_selective=True)
    with pytest.raises(AssertionError):
        assert_declared_add_selective(mutant, 2, 3)


def test_declared_strict_multiplication_order_pin_rejects_a_mutant() -> None:
    """The strict-order pin rejects multiplication that collapses an order."""
    mutant = MutantSemiring(
        add_operation=min,
        multiply_operation=lambda left, right: min(left + right, 3),
        multiply_strictly_order_preserving=True,
    )
    with pytest.raises(AssertionError):
        assert_declared_multiply_strictly_order_preserving(mutant, 1, 2, 2)


def test_declared_witness_order_preservation_pin_rejects_a_mutant() -> None:
    """The ranked-order pin rejects multiplication that reverses preference."""
    mutant = MutantSemiring(
        add_operation=min,
        multiply_operation=lambda left, right: left - right,
        multiply_preserves_witness_order=True,
    )
    with pytest.raises(AssertionError):
        assert_declared_multiply_preserves_witness_order(mutant, 1, 2, 10)


def test_declared_zero_sum_free_pin_rejects_a_mutant() -> None:
    """The zero-sum-free pin rejects addition modulo four."""
    mutant = MutantSemiring(
        add_operation=lambda left, right: (left + right) % 4,
        zero_sum_free=True,
    )
    with pytest.raises(AssertionError):
        assert_declared_zero_sum_free(mutant, 1, 3)


def test_declared_no_zero_divisors_pin_rejects_a_mutant() -> None:
    """The no-zero-divisors pin rejects multiplication modulo four."""
    mutant = MutantSemiring(
        multiply_operation=lambda left, right: (left * right) % 4,
        no_zero_divisors=True,
    )
    with pytest.raises(AssertionError):
        assert_declared_no_zero_divisors(mutant, 2, 2)


def test_approximate_associativity_pin_rejects_a_mutant() -> None:
    """Approximate associativity still rejects a material disagreement."""
    mutant = MutantSemiring(
        multiply_operation=lambda left, right: left + 2 * right,
        multiply_associativity=LawCheck.APPROXIMATE,
    )
    with pytest.raises(AssertionError):
        assert_multiply_associative(mutant, 1, 2, 3)


def test_a_required_law_cannot_declare_no_check() -> None:
    """A non-check declaration fails instead of skipping the required law."""
    mutant = MutantSemiring()
    mutant.add_associativity = None  # type: ignore[assignment]
    with pytest.raises(AssertionError, match="invalid required-law check"):
        assert_add_associative(mutant, 1, 2, 3)
