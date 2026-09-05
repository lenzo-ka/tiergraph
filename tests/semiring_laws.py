"""Reusable assertions for the laws promised by a semiring."""

from __future__ import annotations

import math
from typing import Any

from tiergraph.semiring import LawCheck, Semiring

# A heterogeneous parametrized suite erases each carrier type at this boundary.
LawSemiring = Semiring[Any]


def _float_magnitudes(value: Any) -> list[float]:
    """Collect finite float magnitudes from a possibly composed carrier."""
    if isinstance(value, tuple):
        return [item for part in value for item in _float_magnitudes(part)]
    if isinstance(value, float) and math.isfinite(value):
        return [abs(value)]
    return []


def _approximately_equal(left: Any, right: Any, scale: float) -> bool:
    """Compare nested float carriers against a rounding-error bound."""
    if isinstance(left, tuple) and isinstance(right, tuple):
        return len(left) == len(right) and all(
            _approximately_equal(a, b, scale) for a, b in zip(left, right, strict=True)
        )
    if isinstance(left, float) and isinstance(right, float):
        # Three-term addition performs two roundings on either side. Eight ulps
        # leaves margin for subnormal boundaries but rejects algebraic mutants.
        tolerance = 8 * math.ulp(scale) if scale else 8 * math.ulp(0.0)
        return math.isclose(left, right, rel_tol=1e-15, abs_tol=tolerance)
    return bool(left == right)


def _assert_required_equal(
    check: LawCheck,
    left: Any,
    right: Any,
    operands: tuple[Any, ...],
    *,
    law: str,
    allow_not_held: bool = False,
) -> None:
    """Assert a required equality exactly or within its declared bound."""
    if check is LawCheck.EXACT:
        assert left == right
        return
    if check is LawCheck.APPROXIMATE:
        magnitudes = [
            magnitude for value in operands for magnitude in _float_magnitudes(value)
        ]
        assert _approximately_equal(left, right, max(magnitudes, default=0.0))
        return
    if check is LawCheck.NOT_HELD:
        assert allow_not_held, f"{law} cannot be declared NOT_HELD"
        return
    raise AssertionError(f"invalid required-law check: {check!r}")


def assert_add_associative(semiring: LawSemiring, a: Any, b: Any, c: Any) -> None:
    """Assert addition associativity with its mandatory check."""
    _assert_required_equal(
        semiring.add_associativity,
        semiring.add(semiring.add(a, b), c),
        semiring.add(a, semiring.add(b, c)),
        (a, b, c),
        law="add_associativity",
    )


def assert_multiply_associative(semiring: LawSemiring, a: Any, b: Any, c: Any) -> None:
    """Assert exact or explicitly approximate multiplication associativity."""
    left = semiring.multiply(semiring.multiply(a, b), c)
    right = semiring.multiply(a, semiring.multiply(b, c))
    _assert_required_equal(
        semiring.multiply_associativity,
        left,
        right,
        (a, b, c),
        law="multiply_associativity",
    )


def assert_add_commutative(semiring: LawSemiring, a: Any, b: Any) -> None:
    """Assert addition commutativity with its mandatory check."""
    _assert_required_equal(
        semiring.add_commutativity,
        semiring.add(a, b),
        semiring.add(b, a),
        (a, b),
        law="add_commutativity",
        allow_not_held=True,
    )


def assert_add_identity(semiring: LawSemiring, value: Any) -> None:
    """Assert that zero is the two-sided ⊕ identity."""
    assert semiring.add(semiring.zero, value) == value
    assert semiring.add(value, semiring.zero) == value


def assert_multiply_identity(semiring: LawSemiring, value: Any) -> None:
    """Assert that one is the two-sided ⊗ identity."""
    assert semiring.multiply(semiring.one, value) == value
    assert semiring.multiply(value, semiring.one) == value


def assert_zero_annihilates(semiring: LawSemiring, value: Any) -> None:
    """Assert that zero annihilates multiplication on both sides."""
    assert semiring.multiply(semiring.zero, value) == semiring.zero
    assert semiring.multiply(value, semiring.zero) == semiring.zero


def assert_left_distributive(semiring: LawSemiring, a: Any, b: Any, c: Any) -> None:
    """Assert left distributivity with its mandatory check."""
    _assert_required_equal(
        semiring.left_distributivity,
        semiring.multiply(a, semiring.add(b, c)),
        semiring.add(semiring.multiply(a, b), semiring.multiply(a, c)),
        (a, b, c),
        law="left_distributivity",
    )


def assert_right_distributive(semiring: LawSemiring, a: Any, b: Any, c: Any) -> None:
    """Assert right distributivity with its mandatory check."""
    _assert_required_equal(
        semiring.right_distributivity,
        semiring.multiply(semiring.add(a, b), c),
        semiring.add(semiring.multiply(a, c), semiring.multiply(b, c)),
        (a, b, c),
        law="right_distributivity",
    )


def assert_declared_optional_laws(semiring: LawSemiring, a: Any, b: Any) -> None:
    """Assert only the optional laws declared by the instance."""
    if semiring.add_idempotent:
        assert semiring.add(a, a) == a
    if semiring.multiply_commutative:
        assert semiring.multiply(a, b) == semiring.multiply(b, a)


def assert_declared_add_selective(semiring: LawSemiring, a: Any, b: Any) -> None:
    """Assert that declared selective addition returns one of its operands."""
    if semiring.add_selective:
        assert semiring.add(a, b) in (a, b)


def assert_declared_multiply_strictly_order_preserving(
    semiring: LawSemiring, a: Any, b: Any, c: Any
) -> None:
    """Assert strict multiplication monotonicity for the order induced by ⊕.

    Here ``a < b`` means ``a != b`` and ``a ⊕ b = a``: ``a`` is strictly
    preferred by the semiring's declared selective addition.
    """
    if (
        semiring.multiply_strictly_order_preserving
        and a != semiring.zero
        and b != semiring.zero
        and c != semiring.zero
        and a != b
        and semiring.add(a, b) == a
    ):
        left_a = semiring.multiply(c, a)
        left_b = semiring.multiply(c, b)
        right_a = semiring.multiply(a, c)
        right_b = semiring.multiply(b, c)
        assert left_a != left_b and semiring.add(left_a, left_b) == left_a
        assert right_a != right_b and semiring.add(right_a, right_b) == right_a


def assert_declared_multiply_preserves_witness_order(
    semiring: LawSemiring, a: Any, b: Any, c: Any
) -> None:
    """Assert weak multiplication monotonicity for the order induced by addition."""
    if semiring.multiply_preserves_witness_order and a != b and semiring.add(a, b) == a:
        left_a = semiring.multiply(c, a)
        left_b = semiring.multiply(c, b)
        right_a = semiring.multiply(a, c)
        right_b = semiring.multiply(b, c)
        assert semiring.add(left_a, left_b) == left_a
        assert semiring.add(right_a, right_b) == right_a


def assert_declared_zero_sum_free(semiring: LawSemiring, a: Any, b: Any) -> None:
    """Assert that a declared zero sum has only zero operands."""
    if semiring.zero_sum_free and semiring.add(a, b) == semiring.zero:
        assert a == semiring.zero and b == semiring.zero


def assert_declared_no_zero_divisors(semiring: LawSemiring, a: Any, b: Any) -> None:
    """Assert that a declared zero product has at least one zero operand."""
    if semiring.no_zero_divisors and semiring.multiply(a, b) == semiring.zero:
        assert a == semiring.zero or b == semiring.zero
