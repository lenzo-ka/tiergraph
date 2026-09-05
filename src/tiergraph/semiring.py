"""Semirings used to interpret finite dependency graphs."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from decimal import MAX_EMAX, MIN_EMIN, Decimal, localcontext
from enum import Enum
from typing import Any, Protocol, cast


class StarRefusal(ValueError):
    """Refuse a closure the declaring algebra does not license for this operand."""


@dataclass(frozen=True, slots=True)
class ZeroClosedStar[T]:
    """Admit 0-closed operands and close their finite ascending chain to one."""

    algebra: Semiring[T]
    name: str = "zero-closed"

    def admits(self, operand: T, /) -> bool:
        """Prove that the operand is dominated by the multiplicative identity."""
        return self.algebra.add(self.algebra.one, operand) == self.algebra.one

    def close(self, operand: T, /) -> T:
        """Return the closure after checking the warrant."""
        if not self.admits(operand):
            raise StarRefusal(
                f"{self.name} warrant refuses operand {self.algebra.encode(operand)!r}"
            )
        return self.algebra.one


type StarSelector[T] = ZeroClosedStar[T]


class LawCheck(Enum):
    """The mandatory comparison used to check a semiring law."""

    EXACT = "exact"
    APPROXIMATE = "approximate"
    NOT_HELD = "not-held"


# A shared precondition list for the composite constructions below and for
# ``inexact_laws``, not a published surface: it repeats the protocol's own
# LawCheck-valued properties, and a caller reads those from the algebra, or the
# unchecked ones from ``inexact_laws``, rather than from this tuple.
_REQUIRED_LAW_CHECKS = (
    "add_associativity",
    "multiply_associativity",
    "add_commutativity",
    "left_distributivity",
    "right_distributivity",
)


def inexact_laws(algebra: Semiring[Any], /) -> tuple[str, ...]:
    """Name the required semiring laws this algebra does not check exactly.

    The order is the fixed precondition order, so the first name is stable across
    runs and can be quoted in a refusal. An empty result is a statement about the
    algebra's *declaration* only: it says the five laws are declared exact, not
    that they hold, which is why a caller that needs the stronger fact has to
    check the laws at values rather than read this tuple.
    """
    return tuple(
        name
        for name in _REQUIRED_LAW_CHECKS
        if getattr(algebra, name) is not LawCheck.EXACT
    )


class Semiring[T](Protocol):
    """Operations, carrier boundary, encoding, and declared algebraic laws."""

    @property
    def zero(self) -> T:
        """Return the additive identity."""

    @property
    def one(self) -> T:
        """Return the multiplicative identity."""

    @property
    def add_associativity(self) -> LawCheck:
        """Return the required check for addition associativity."""

    @property
    def multiply_associativity(self) -> LawCheck:
        """Return the required check for multiplication associativity."""

    @property
    def add_commutativity(self) -> LawCheck:
        """Return the required check for addition commutativity."""

    @property
    def left_distributivity(self) -> LawCheck:
        """Return the required check for left distributivity."""

    @property
    def right_distributivity(self) -> LawCheck:
        """Return the required check for right distributivity."""

    @property
    def add_idempotent(self) -> bool:
        """Report whether addition is idempotent."""

    @property
    def star(self) -> StarSelector[T] | None:
        """Name this carrier's closure and its warrant, or declare none."""

    @property
    def multiply_commutative(self) -> bool:
        """Report whether multiplication is commutative."""

    @property
    def add_selective(self) -> bool:
        """Report whether addition always selects one operand."""

    @property
    def multiply_strictly_order_preserving(self) -> bool:
        """Report strict order preservation away from zero."""

    @property
    def multiply_preserves_witness_order(self) -> bool:
        """Report whether multiplication preserves the order induced by addition."""

    @property
    def zero_sum_free(self) -> bool:
        """Report whether a sum is zero only when both operands are zero."""

    @property
    def no_zero_divisors(self) -> bool:
        """Report whether a product is zero only with a zero operand."""

    def add(self, left: T, right: T, /) -> T:
        """Return ``left ⊕ right``."""

    def multiply(self, left: T, right: T, /) -> T:
        """Return ``left ⊗ right``."""

    def encode(self, value: T, /) -> object:
        """Return a strict-JSON representation of a carrier value."""

    def decode(self, value: object, /) -> T:
        """Decode and validate a strict-JSON representation."""


class BooleanSemiring:
    """The exact Boolean semiring, with disjunction and conjunction."""

    zero = False
    one = True
    add_associativity = multiply_associativity = LawCheck.EXACT
    add_commutativity = left_distributivity = right_distributivity = LawCheck.EXACT
    add_idempotent = multiply_commutative = True
    add_selective = True
    multiply_strictly_order_preserving = False
    multiply_preserves_witness_order = False
    zero_sum_free = no_zero_divisors = True

    @property
    def star(self) -> StarSelector[bool]:
        """Return the Boolean carrier's 0-closed closure."""
        return ZeroClosedStar(self)

    def _value(self, value: bool, name: str) -> bool:
        if type(value) is not bool:
            raise TypeError(f"{name} must be a Boolean carrier value")
        return value

    def add(self, left: bool, right: bool, /) -> bool:
        """Return the disjunction of two values."""
        return self._value(left, "left") or self._value(right, "right")

    def multiply(self, left: bool, right: bool, /) -> bool:
        """Return the conjunction of two values."""
        return self._value(left, "left") and self._value(right, "right")

    def encode(self, value: bool, /) -> object:
        """Encode a Boolean as a JSON Boolean."""
        return self._value(value, "value")

    def decode(self, value: object, /) -> bool:
        """Decode a JSON Boolean."""
        return self._value(cast(bool, value), "encoded value")


class CountingSemiring:
    """The exact natural-number semiring."""

    zero = 0
    one = 1
    add_associativity = multiply_associativity = LawCheck.EXACT
    add_commutativity = left_distributivity = right_distributivity = LawCheck.EXACT
    add_idempotent = False
    multiply_commutative = True
    add_selective = False
    multiply_strictly_order_preserving = False
    multiply_preserves_witness_order = False
    zero_sum_free = no_zero_divisors = True
    star = None

    def _value(self, value: int, name: str) -> int:
        if type(value) is not int or value < 0:
            raise ValueError(f"{name} must be a nonnegative integer carrier value")
        return value

    def add(self, left: int, right: int, /) -> int:
        """Return the sum of two counts."""
        return self._value(left, "left") + self._value(right, "right")

    def multiply(self, left: int, right: int, /) -> int:
        """Return the product of independent counts."""
        return self._value(left, "left") * self._value(right, "right")

    def encode(self, value: int, /) -> object:
        """Encode a count as a JSON integer."""
        return self._value(value, "value")

    def decode(self, value: object, /) -> int:
        """Decode a JSON natural number."""
        return self._value(cast(int, value), "encoded value")


class DecimalExtremumSemiring:
    """An exact min-plus or max-plus semiring with XSD-decimal finite values."""

    add_associativity = multiply_associativity = LawCheck.EXACT
    add_commutativity = left_distributivity = right_distributivity = LawCheck.EXACT
    add_idempotent = multiply_commutative = True
    add_selective = True
    multiply_strictly_order_preserving = True
    multiply_preserves_witness_order = True
    zero_sum_free = no_zero_divisors = True

    @property
    def star(self) -> StarSelector[Decimal]:
        """Return this extremum carrier's 0-closed closure."""
        return ZeroClosedStar(self)

    def __init__(self, *, minimum: bool) -> None:
        self._minimum = minimum
        self.zero = Decimal("Infinity" if minimum else "-Infinity")
        self.one = Decimal(0)

    def _value(self, value: Decimal, name: str) -> Decimal:
        if not isinstance(value, Decimal) or value.is_nan():
            raise ValueError(
                f"{name} must be an XSD-decimal value or this semiring's zero"
            )
        if value.is_infinite() and value != self.zero:
            raise ValueError(f"{name} contains the excluded infinite bound")
        return value

    def add(self, left: Decimal, right: Decimal, /) -> Decimal:
        """Return the preferred extremum."""
        left = self._value(left, "left")
        right = self._value(right, "right")
        return min(left, right) if self._minimum else max(left, right)

    def multiply(self, left: Decimal, right: Decimal, /) -> Decimal:
        """Return the exact sum, preserving the annihilator."""
        left = self._value(left, "left")
        right = self._value(right, "right")
        if left == self.zero or right == self.zero:
            return self.zero
        # Decimal's ambient context is finite; choose enough precision for this
        # addition so the XSD-decimal value-space operation stays exact.
        precision = max(len(left.as_tuple().digits), len(right.as_tuple().digits))
        precision += abs(left.adjusted() - right.adjusted()) + 2
        with localcontext() as context:
            context.prec = precision
            context.Emax = MAX_EMAX
            context.Emin = MIN_EMIN
            return self._value(left + right, "result")

    def encode(self, value: Decimal, /) -> object:
        """Encode a value with XSD-style infinity and exact decimal text."""
        value = self._value(value, "value")
        return (
            "INF"
            if value == Decimal("Infinity")
            else "-INF"
            if value == Decimal("-Infinity")
            else str(value)
        )

    def decode(self, value: object, /) -> Decimal:
        """Decode exact decimal text."""
        if not isinstance(value, str):
            raise TypeError("encoded value must be a string")
        return self._value(
            Decimal(
                "Infinity"
                if value == "INF"
                else "-Infinity"
                if value == "-INF"
                else value
            ),
            "encoded value",
        )


class DoubleExtremumSemiring:
    """An inexact min-plus or max-plus semiring over finite IEEE doubles."""

    add_associativity = LawCheck.EXACT
    multiply_associativity = LawCheck.APPROXIMATE
    add_commutativity = LawCheck.EXACT
    left_distributivity = right_distributivity = LawCheck.EXACT
    add_idempotent = multiply_commutative = True
    add_selective = True
    multiply_strictly_order_preserving = False
    multiply_preserves_witness_order = False
    zero_sum_free = no_zero_divisors = True

    @property
    def star(self) -> StarSelector[float]:
        """Return this extremum carrier's 0-closed closure."""
        return ZeroClosedStar(self)

    def __init__(self, *, minimum: bool) -> None:
        self._minimum = minimum
        self.zero = math.inf if minimum else -math.inf
        self.one = 0.0

    def _value(self, value: float, name: str) -> float:
        if type(value) is not float or math.isnan(value):
            raise ValueError(f"{name} must be an IEEE-double carrier value")
        if math.isinf(value) and value != self.zero:
            raise ValueError(f"{name} contains the excluded infinite bound")
        return value

    def add(self, left: float, right: float, /) -> float:
        """Return the preferred extremum."""
        left = self._value(left, "left")
        right = self._value(right, "right")
        return min(left, right) if self._minimum else max(left, right)

    def multiply(self, left: float, right: float, /) -> float:
        """Add finite doubles, refusing overflow and preserving the annihilator."""
        left = self._value(left, "left")
        right = self._value(right, "right")
        if left == self.zero or right == self.zero:
            return self.zero
        result = left + right
        if not math.isfinite(result):
            raise OverflowError("result leaves the finite IEEE-double carrier")
        return result

    def encode(self, value: float, /) -> object:
        """Encode a double losslessly without non-JSON numeric tokens."""
        value = self._value(value, "value")
        return (
            "INF"
            if value == math.inf
            else "-INF"
            if value == -math.inf
            else value.hex()
        )

    def decode(self, value: object, /) -> float:
        """Decode lossless hexadecimal double text."""
        if not isinstance(value, str):
            raise TypeError("encoded value must be a string")
        decoded = (
            math.inf
            if value == "INF"
            else -math.inf
            if value == "-INF"
            else float.fromhex(value)
        )
        return self._value(decoded, "encoded value")


class TropicalSemiring(DoubleExtremumSemiring):
    """The inexact IEEE-double min-plus semiring."""

    @property
    def star(self) -> StarSelector[float]:
        """Return this carrier's explicitly declared 0-closed closure."""
        return ZeroClosedStar(self)

    def __init__(self) -> None:
        super().__init__(minimum=True)


class ArcticSemiring(DoubleExtremumSemiring):
    """The inexact IEEE-double max-plus semiring."""

    @property
    def star(self) -> StarSelector[float]:
        """Return this carrier's explicitly declared 0-closed closure."""
        return ZeroClosedStar(self)

    def __init__(self) -> None:
        super().__init__(minimum=False)


@dataclass(frozen=True)
class ProductSemiring[T, U]:
    """The componentwise product of two semirings."""

    left: Semiring[T]
    right: Semiring[U]

    @property
    def star(self) -> StarSelector[tuple[T, U]] | None:
        """Declare no closure for arbitrary component products."""
        return None

    @property
    def zero(self) -> tuple[T, U]:
        """Return the pair of additive identities."""
        return (self.left.zero, self.right.zero)

    @property
    def one(self) -> tuple[T, U]:
        """Return the pair of multiplicative identities."""
        return (self.left.one, self.right.one)

    def __getattr__(self, name: str) -> bool:
        if name in {
            "add_idempotent",
            "multiply_commutative",
            "zero_sum_free",
        }:
            return bool(getattr(self.left, name) and getattr(self.right, name))
        raise AttributeError(name)

    @property
    def add_selective(self) -> bool:
        """Report false because components may select opposite operands."""
        return False

    @property
    def multiply_strictly_order_preserving(self) -> bool:
        """Report false because a nonzero pair may have a zero component."""
        return False

    @property
    def multiply_preserves_witness_order(self) -> bool:
        """Report false because an external order need not be componentwise."""
        return False

    @property
    def no_zero_divisors(self) -> bool:
        """Report false because complementary zero components multiply to zero."""
        return False

    def _law_check(self, name: str) -> LawCheck:
        """Use an approximate check when either component requires one."""
        checks = (getattr(self.left, name), getattr(self.right, name))
        if any(not isinstance(check, LawCheck) for check in checks):
            raise ValueError(f"product component has invalid {name} check")
        return (
            LawCheck.APPROXIMATE if LawCheck.APPROXIMATE in checks else LawCheck.EXACT
        )

    @property
    def add_associativity(self) -> LawCheck:
        """Derive the mandatory addition-associativity check."""
        return self._law_check("add_associativity")

    @property
    def multiply_associativity(self) -> LawCheck:
        """Derive the mandatory multiplication-associativity check."""
        return self._law_check("multiply_associativity")

    @property
    def add_commutativity(self) -> LawCheck:
        """Derive the mandatory addition-commutativity check."""
        return self._law_check("add_commutativity")

    @property
    def left_distributivity(self) -> LawCheck:
        """Derive the mandatory left-distributivity check."""
        return self._law_check("left_distributivity")

    @property
    def right_distributivity(self) -> LawCheck:
        """Derive the mandatory right-distributivity check."""
        return self._law_check("right_distributivity")

    def add(self, left: tuple[T, U], right: tuple[T, U], /) -> tuple[T, U]:
        """Add each component."""
        return (self.left.add(left[0], right[0]), self.right.add(left[1], right[1]))

    def multiply(self, left: tuple[T, U], right: tuple[T, U], /) -> tuple[T, U]:
        """Multiply each component."""
        return (
            self.left.multiply(left[0], right[0]),
            self.right.multiply(left[1], right[1]),
        )

    def encode(self, value: tuple[T, U], /) -> object:
        """Encode both components as a JSON array."""
        return [self.left.encode(value[0]), self.right.encode(value[1])]

    def decode(self, value: object, /) -> tuple[T, U]:
        """Decode a two-component JSON array."""
        product_component_count = 2
        if not isinstance(value, list) or len(value) != product_component_count:
            raise ValueError("encoded product must be a two-element array")
        return (self.left.decode(value[0]), self.right.decode(value[1]))


class _Addable[U](Protocol):
    def __add__(self, other: U, /) -> U: ...


@dataclass(frozen=True, slots=True)
class _PayloadAddition:
    """Give the default payload operation a stable documented representation."""

    def __call__[U](self, left: U, right: U, /) -> U:
        return cast(_Addable[U], left).__add__(right)


_PAYLOAD_ADDITION = _PayloadAddition()


@dataclass(frozen=True)
class SelectionSemiring[T, U]:
    """Select a winning cost together with its payload.

    ``tie_invariant_payload`` must be declared true because this construction
    cannot check it. Addition keeps the first operand on a cost tie, so a false
    declaration makes results depend on operand order when tied optimal paths
    carry different payloads. Such payloads require accumulation instead.

    ``payload_identity`` supplies the payload at both cost identities. Payload
    multiplication defaults to addition, which concatenates tuple witnesses;
    callers can supply another operation for another payload. The caller
    declares that ``payload_identity`` is its two-sided identity and that
    ``payload_multiply`` is associative; the law checks derived from ``cost``
    assume both conditions. Construction can test the identity against itself
    but cannot prove either condition over every payload. Float costs can create
    ties by rounding, so tie invariance covers arithmetic ties as well as exact
    ones. The default codec writes a tuple witness as a JSON array and restores
    that tuple; callers provide codecs for other compound payload shapes.
    """

    cost: Semiring[T]
    payload_identity: U
    tie_invariant_payload: bool
    payload_multiply: Callable[[U, U], U] = _PAYLOAD_ADDITION
    payload_encode: Callable[[U], object] | None = None
    payload_decode: Callable[[object], U] | None = None

    def __post_init__(self) -> None:
        """Require the caller's uncheckable soundness declaration."""
        if self.tie_invariant_payload is not True:
            raise ValueError(
                "tie_invariant_payload must be declared true for selection"
            )
        try:
            identity_product = self.payload_multiply(
                self.payload_identity, self.payload_identity
            )
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"payload_identity {self.payload_identity!r} is not accepted by "
                f"payload_multiply {self.payload_multiply!r}"
            ) from error
        if identity_product != self.payload_identity:
            raise ValueError(
                f"payload_identity {self.payload_identity!r} must be preserved by "
                f"payload_multiply {self.payload_multiply!r}"
            )

    @property
    def star(self) -> StarSelector[tuple[T, U]] | None:
        """Declare no closure for an arbitrary payload combination."""
        return None

    @property
    def zero(self) -> tuple[T, U]:
        """Pair the cost zero with the payload identity."""
        return (self.cost.zero, self.payload_identity)

    @property
    def one(self) -> tuple[T, U]:
        """Pair the cost one with the payload identity."""
        return (self.cost.one, self.payload_identity)

    def _law_check(self, name: str) -> LawCheck:
        check = getattr(self.cost, name)
        if not isinstance(check, LawCheck):
            raise ValueError(f"selection cost has invalid {name} check")
        return check

    @property
    def add_associativity(self) -> LawCheck:
        """Derive the mandatory addition-associativity check from the cost."""
        return self._law_check("add_associativity")

    @property
    def multiply_associativity(self) -> LawCheck:
        """Derive the mandatory multiplication-associativity check from the cost."""
        return self._law_check("multiply_associativity")

    @property
    def add_commutativity(self) -> LawCheck:
        """Declare that operand-ordered tie selection is not commutative."""
        return LawCheck.NOT_HELD

    @property
    def left_distributivity(self) -> LawCheck:
        """Derive the mandatory left-distributivity check from the cost."""
        return self._law_check("left_distributivity")

    @property
    def right_distributivity(self) -> LawCheck:
        """Derive the mandatory right-distributivity check from the cost."""
        return self._law_check("right_distributivity")

    @property
    def add_idempotent(self) -> bool:
        """Derive addition idempotence from the cost semiring."""
        return self.cost.add_idempotent

    add_selective = True
    multiply_commutative = False
    multiply_strictly_order_preserving = False
    multiply_preserves_witness_order = False
    zero_sum_free = False
    no_zero_divisors = False

    def _value(self, value: tuple[T, U], name: str) -> tuple[T, U]:
        if value[0] == self.cost.zero and value[1] != self.payload_identity:
            raise ValueError(
                f"{name} with selection cost zero must carry payload_identity"
            )
        return value

    def add(self, left: tuple[T, U], right: tuple[T, U], /) -> tuple[T, U]:
        """Select the first cost winner and carry its payload."""
        left = self._value(left, "left")
        right = self._value(right, "right")
        preferred = self.cost.add(left[0], right[0])
        return left if preferred == left[0] else right

    def multiply(self, left: tuple[T, U], right: tuple[T, U], /) -> tuple[T, U]:
        """Multiply costs and combine payloads."""
        left = self._value(left, "left")
        right = self._value(right, "right")
        if left[0] == self.cost.zero or right[0] == self.cost.zero:
            return self.zero
        cost = self.cost.multiply(left[0], right[0])
        if cost == self.cost.zero:
            return self.zero
        return (cost, self.payload_multiply(left[1], right[1]))

    def encode(self, value: tuple[T, U], /) -> object:
        """Encode the cost and payload as a JSON array."""
        value = self._value(value, "value")
        payload = (
            list(value[1])
            if self.payload_encode is None and isinstance(value[1], tuple)
            else value[1]
            if self.payload_encode is None
            else self.payload_encode(value[1])
        )
        return [self.cost.encode(value[0]), payload]

    def decode(self, value: object, /) -> tuple[T, U]:
        """Decode a cost and payload JSON array."""
        selection_component_count = 2
        if not isinstance(value, list) or len(value) != selection_component_count:
            raise ValueError("encoded selection must be a two-element array")
        payload = (
            cast(U, tuple(value[1]))
            if self.payload_decode is None
            and isinstance(self.payload_identity, tuple)
            and isinstance(value[1], list)
            else cast(U, value[1])
            if self.payload_decode is None
            else self.payload_decode(value[1])
        )
        return self._value((self.cost.decode(value[0]), payload), "encoded value")


class LexicographicSemiring[T, U](ProductSemiring[T, U]):
    """A selective first semiring with second-component aggregation on ties.

    ``exact_workload`` declares that the caller's values make approximate
    component laws exact. Without it, an approximate component is refused. A
    false declaration can make a fold non-associative on fractional float values
    and silently change its result with the evaluation order.

    ``order_preserving_workload`` declares that multiplication preserves strict
    first-component order on the caller's values. Without it, a first component
    that cannot promise this property is refused. A false declaration lets a
    strictly worse cost tie or overtake after rounded multiplication, making the
    lexicographic choice wrong. Exact components need neither declaration.
    """

    exact_workload: bool
    order_preserving_workload: bool

    @property
    def star(self) -> StarSelector[tuple[T, U]] | None:
        """Declare no closure for arbitrary lexicographic components."""
        return None

    def __init__(
        self,
        first: Semiring[T],
        second: Semiring[U],
        exact_workload: bool = False,
        order_preserving_workload: bool = False,
    ) -> None:
        for component in (first, second):
            unchecked = inexact_laws(component)
            if unchecked and exact_workload is not True:
                raise ValueError(
                    f"lexicographic component lacks exact {unchecked[0]}; "
                    "exact_workload must be declared true"
                )
        if not first.add_selective:
            raise ValueError("lexicographic first component lacks add_selective")
        if (
            not first.multiply_strictly_order_preserving
            and order_preserving_workload is not True
        ):
            raise ValueError(
                "lexicographic first component lacks "
                "multiply_strictly_order_preserving; "
                "order_preserving_workload must be declared true"
            )
        for name in ("zero_sum_free", "no_zero_divisors"):
            if not getattr(second, name):
                raise ValueError(f"lexicographic second component lacks {name}")
        super().__init__(first, second)
        self.exact_workload = exact_workload
        self.order_preserving_workload = order_preserving_workload

    def _value(self, value: tuple[T, U], name: str) -> tuple[T, U]:
        if (value[0] == self.left.zero) != (value[1] == self.right.zero):
            raise ValueError(f"{name} must pair both zeros or neither zero")
        return value

    def add(self, left: tuple[T, U], right: tuple[T, U], /) -> tuple[T, U]:
        """Choose by the first component and aggregate the second on a tie."""
        left = self._value(left, "left")
        right = self._value(right, "right")
        preferred = self.left.add(left[0], right[0])
        if left[0] == right[0]:
            return (preferred, self.right.add(left[1], right[1]))
        return left if preferred == left[0] else right

    def multiply(self, left: tuple[T, U], right: tuple[T, U], /) -> tuple[T, U]:
        """Multiply componentwise within the restricted carrier."""
        return self._value(
            super().multiply(self._value(left, "left"), self._value(right, "right")),
            "result",
        )

    def encode(self, value: tuple[T, U], /) -> object:
        """Encode a validated lexicographic value."""
        return super().encode(self._value(value, "value"))

    def decode(self, value: object, /) -> tuple[T, U]:
        """Decode and validate a lexicographic value."""
        return self._value(super().decode(value), "encoded value")

    @property
    def add_idempotent(self) -> bool:
        """Derive idempotence from both components."""
        return self.left.add_idempotent and self.right.add_idempotent

    @property
    def add_selective(self) -> bool:
        """A tie may aggregate to a new second value."""
        return self.right.add_selective

    @property
    def multiply_strictly_order_preserving(self) -> bool:
        """The restricted carrier excludes nonzero pairs with a zero component."""
        return (
            self.left.multiply_strictly_order_preserving
            and self.right.multiply_strictly_order_preserving
        )

    @property
    def no_zero_divisors(self) -> bool:
        """The restricted carrier makes componentwise zero operands whole zeros."""
        return self.left.no_zero_divisors and self.right.no_zero_divisors


class PathWitnessSemiring:
    """The exact semiring of finite path sets under union and concatenation."""

    zero: tuple[tuple[str, ...], ...] = ()
    one: tuple[tuple[str, ...], ...] = ((),)
    add_associativity = multiply_associativity = LawCheck.EXACT
    add_commutativity = left_distributivity = right_distributivity = LawCheck.EXACT
    add_idempotent = True
    multiply_commutative = False
    add_selective = False
    multiply_strictly_order_preserving = False
    multiply_preserves_witness_order = False
    zero_sum_free = no_zero_divisors = True
    star = None

    def _value(
        self, value: tuple[tuple[str, ...], ...], name: str
    ) -> tuple[tuple[str, ...], ...]:
        if (
            not isinstance(value, tuple)
            or any(
                not isinstance(path, tuple)
                or any(not isinstance(item, str) for item in path)
                for path in value
            )
            or tuple(sorted(set(value))) != value
        ):
            raise ValueError(f"{name} must be a sorted duplicate-free tuple of paths")
        return value

    def add(
        self, left: tuple[tuple[str, ...], ...], right: tuple[tuple[str, ...], ...], /
    ) -> tuple[tuple[str, ...], ...]:
        """Union two path sets."""
        return tuple(
            sorted(set(self._value(left, "left")) | set(self._value(right, "right")))
        )

    def multiply(
        self, left: tuple[tuple[str, ...], ...], right: tuple[tuple[str, ...], ...], /
    ) -> tuple[tuple[str, ...], ...]:
        """Concatenate every pair of paths."""
        left = self._value(left, "left")
        right = self._value(right, "right")
        return tuple(sorted({a + b for a in left for b in right}))

    def encode(self, value: tuple[tuple[str, ...], ...], /) -> object:
        """Encode paths as nested JSON arrays."""
        return [list(path) for path in self._value(value, "value")]

    def decode(self, value: object, /) -> tuple[tuple[str, ...], ...]:
        """Decode nested JSON arrays of path labels."""
        if not isinstance(value, list) or any(
            not isinstance(path, list) for path in value
        ):
            raise ValueError("encoded paths must be an array of arrays")
        return self._value(
            tuple(tuple(cast(list[str], path)) for path in value), "encoded value"
        )


class ExpectationSemiring[T](ProductSemiring[T, T]):
    """The expectation construction ``(weight, weighted statistic)``."""

    @property
    def star(self) -> StarSelector[tuple[T, T]] | None:
        """Declare no closure for an arbitrary expectation base."""
        return None

    def __init__(self, base: Semiring[T]) -> None:
        unchecked = inexact_laws(base)
        if unchecked:
            raise ValueError(f"expectation base lacks exact {unchecked[0]}")
        if not base.multiply_commutative:
            raise ValueError("expectation base lacks multiply_commutative")
        super().__init__(base, base)

    def multiply(self, left: tuple[T, T], right: tuple[T, T], /) -> tuple[T, T]:
        """Multiply weights and apply the product rule to statistics."""
        base = self.left
        return (
            base.multiply(left[0], right[0]),
            base.add(
                base.multiply(left[0], right[1]), base.multiply(left[1], right[0])
            ),
        )

    @property
    def one(self) -> tuple[T, T]:
        """Return the expectation multiplicative identity."""
        return (self.left.one, self.left.zero)

    @property
    def add_idempotent(self) -> bool:
        """Expectation addition is componentwise."""
        return self.left.add_idempotent

    @property
    def add_selective(self) -> bool:
        """Expectation addition is not selective in general."""
        return False

    @property
    def multiply_strictly_order_preserving(self) -> bool:
        """The mixed product has no inherited strict order."""
        return False

    @property
    def multiply_preserves_witness_order(self) -> bool:
        """Report false because expectation multiplication mixes components."""
        return False

    @property
    def zero_sum_free(self) -> bool:
        """Derive zero-sum freedom from the base."""
        return self.left.zero_sum_free

    @property
    def no_zero_divisors(self) -> bool:
        """The mixed component can vanish independently."""
        return False


class PathSemiring(LexicographicSemiring[Decimal, tuple[tuple[str, ...], ...]]):
    """The exact decimal tropical semiring enriched with tied best paths."""

    @property
    def star(self) -> StarSelector[tuple[Decimal, tuple[tuple[str, ...], ...]]]:
        """Return the proved 0-closed closure for path values."""
        return ZeroClosedStar(self)

    def __init__(self) -> None:
        super().__init__(DECIMAL_TROPICAL, PATH_WITNESSES)

    @property
    def multiply_preserves_witness_order(self) -> bool:
        """Report preservation of the exact decimal cost ordering."""
        return True


type Path = tuple[str, ...]
type PathValue = tuple[Decimal, tuple[Path, ...]]

BOOLEAN = BooleanSemiring()
COUNTING = CountingSemiring()
DECIMAL_TROPICAL = DecimalExtremumSemiring(minimum=True)
DECIMAL_ARCTIC = DecimalExtremumSemiring(minimum=False)
TROPICAL = TropicalSemiring()
ARCTIC = ArcticSemiring()
PATH_WITNESSES = PathWitnessSemiring()
PATH = PathSemiring()

__all__ = [
    "ARCTIC",
    "BOOLEAN",
    "COUNTING",
    "DECIMAL_ARCTIC",
    "DECIMAL_TROPICAL",
    "PATH",
    "PATH_WITNESSES",
    "TROPICAL",
    "ArcticSemiring",
    "BooleanSemiring",
    "CountingSemiring",
    "DecimalExtremumSemiring",
    "DoubleExtremumSemiring",
    "ExpectationSemiring",
    "LawCheck",
    "LexicographicSemiring",
    "Path",
    "PathSemiring",
    "PathValue",
    "PathWitnessSemiring",
    "ProductSemiring",
    "SelectionSemiring",
    "Semiring",
    "StarRefusal",
    "StarSelector",
    "TropicalSemiring",
    "ZeroClosedStar",
    "inexact_laws",
]
