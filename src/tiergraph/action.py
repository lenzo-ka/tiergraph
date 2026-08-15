"""Declared actions, yield normalization, and recognize-act execution."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, TypeVar, cast

from tiergraph.fold import FoldDeclaration, FoldResult, Provenance

Value = TypeVar("Value")
Carrier = TypeVar("Carrier")
Result = TypeVar("Result")
ReadCarrier = TypeVar("ReadCarrier", contravariant=True)
WriteResult = TypeVar("WriteResult", covariant=True)
Scalar = TypeVar("Scalar")
Module = TypeVar("Module")
Source = TypeVar("Source")


@dataclass(frozen=True, slots=True)
class WitnessCoordinate:
    """Pair an action value with its order in the declared structure."""

    position: tuple[int, ...]
    value: object


class CoordinateYield(Protocol):
    """Extract structural coordinates from recognized provenance."""

    def __call__(self, provenance: Provenance, /) -> tuple[WitnessCoordinate, ...]:
        """Return coordinates whose positions, rather than arrival, define order."""


class ActionFunction(Protocol[ReadCarrier, WriteResult]):
    """Apply a normalized coordinate yield to an opaque carrier."""

    def __call__(
        self, carrier: ReadCarrier, coordinates: tuple[object, ...], /
    ) -> WriteResult:
        """Return a JSON-serializable action result."""


class ReactMode(Enum):
    """Choose per-recognition or complete-batch action application."""

    ONE_FOR_ONE = "one-for-one"
    TRANSACTIONAL = "transactional"


@dataclass(frozen=True, slots=True)
class YieldNormalization:
    """Declare action-preserving complete-yield transformations.

    ``collapse`` removes adjacent equal values in structural order and requires
    an associative, idempotent action. ``unique`` keeps only the structurally
    first occurrence of every JSON value and requires idempotence and
    commutativity. ``reorder`` sorts by canonical JSON value and requires
    commutativity.
    """

    collapse: bool = False
    unique: bool = False
    reorder: bool = False

    @property
    def requires_complete_yield(self) -> bool:
        """Report whether this policy cannot be performed by a binary merge."""
        return self.collapse or self.unique or self.reorder

    def apply(
        self, coordinates: tuple[WitnessCoordinate, ...]
    ) -> tuple[WitnessCoordinate, ...]:
        """Normalize a complete yield after first restoring structural order."""
        normalized = tuple(sorted(coordinates, key=lambda item: item.position))
        if self.collapse:
            normalized = _collapse(normalized)
        if self.unique:
            normalized = _unique(normalized)
        if self.reorder:
            normalized = tuple(sorted(normalized, key=_coordinate_key))
        return normalized


@dataclass(frozen=True, slots=True)
class DistributionWitness:
    """Opt in to executable one-for-one equivalence certification.

    The witness supplies no operations, coordinate bridge, samples, or carrier.
    On every one-for-one run, react extracts coordinates once with its declared
    ``yield_coordinates`` and requires its bound action to produce the same result
    when applied one coordinate at a time and as one complete batch. This
    certifies the concrete recognition and carrier being executed; it does not
    prove equivalence for runs that have not been executed.
    """

    name: str

    def __post_init__(self) -> None:
        """Refuse a nameless certificate before it can be bound."""
        if not self.name:
            raise ValueError("distribution witness name '' must not be empty")


class ActionEquivalenceError(ValueError):
    """Refuse caller data for which certified action modes disagree."""


@dataclass(frozen=True, slots=True)
class Semimodule[Scalar, Module]:
    """Supply operations and samples for an explicit, opt-in semimodule claim.

    Merely declaring an action does not claim or check these laws; callers that
    provide this optional structure must execute a semimodule law suite.
    """

    scalar_zero: Scalar
    scalar_one: Scalar
    scalar_add: Callable[[Scalar, Scalar], Scalar]
    scalar_multiply: Callable[[Scalar, Scalar], Scalar]
    module_zero: Module
    module_add: Callable[[Module, Module], Module]
    scale: Callable[[Scalar, Module], Module]
    scalar_samples: tuple[Scalar, ...]
    module_samples: tuple[Module, ...]


@dataclass(frozen=True, slots=True)
class ActionDeclaration[Carrier, Result]:
    """Declare executable behavior and trusted normalization tolerances.

    ``associative``, ``idempotent``, and ``commutative`` are self-attested at
    declaration time. React uses them as normalization gates but does not prove
    them; callers can separately execute ``ActionToleranceLawSuite``. The
    An optional semimodule claim is checked over its declared finite samples
    before the declaration can exist.
    """

    name: str
    apply: ActionFunction[Carrier, Result]
    associative: bool
    idempotent: bool
    commutative: bool
    semimodule: Semimodule[object, object] | None = None

    def __post_init__(self) -> None:
        """Require a public name and validate any sampled algebraic claim."""
        if not self.name:
            raise ValueError("action name '' must not be empty")
        self._validate_semimodule_claim()

    def _validate_semimodule_claim(self) -> None:
        """Validate the bound semimodule laws over its declared samples."""
        law = self.semimodule
        if law is None:
            return
        apply = cast(ActionFunction[object, object], self.apply)

        def _require(actual: object, expected: object, description: str) -> None:
            if actual != expected:
                raise ValueError(
                    f"action {self.name!r} semimodule claim violates {description}: "
                    f"{actual!r} != {expected!r}"
                )

        _require(
            law.module_add(law.module_zero, law.module_zero),
            law.module_zero,
            "module zero identity",
        )
        _require(
            law.scale(law.scalar_one, law.module_zero),
            law.module_zero,
            "unit scalar on module zero",
        )
        for value in law.module_samples:
            _require(
                law.module_add(value, law.module_zero), value, "right module identity"
            )
            _require(
                law.module_add(law.module_zero, value), value, "left module identity"
            )
            _require(law.scale(law.scalar_one, value), value, "unit scalar identity")
            _require(
                law.scale(law.scalar_zero, value),
                law.module_zero,
                "zero scalar annihilation",
            )
            for left in law.module_samples:
                _require(
                    law.module_add(value, left),
                    law.module_add(left, value),
                    "module commutativity",
                )
                for right in law.module_samples:
                    _require(
                        law.module_add(law.module_add(value, left), right),
                        law.module_add(value, law.module_add(left, right)),
                        "module associativity",
                    )
        for scalar in law.scalar_samples:
            _require(
                law.scalar_add(scalar, law.scalar_zero),
                scalar,
                "right scalar additive identity",
            )
            _require(
                law.scalar_add(law.scalar_zero, scalar),
                scalar,
                "left scalar additive identity",
            )
            _require(
                law.scalar_multiply(scalar, law.scalar_one),
                scalar,
                "right scalar multiplicative identity",
            )
            _require(
                law.scalar_multiply(law.scalar_one, scalar),
                scalar,
                "left scalar multiplicative identity",
            )
            _require(
                law.scalar_multiply(scalar, law.scalar_zero),
                law.scalar_zero,
                "right scalar zero annihilation",
            )
            _require(
                law.scalar_multiply(law.scalar_zero, scalar),
                law.scalar_zero,
                "left scalar zero annihilation",
            )
            _require(
                law.scale(scalar, law.module_zero),
                law.module_zero,
                "module zero scaling",
            )
            for value in law.module_samples:
                expected = law.scale(scalar, value)
                try:
                    actual = apply(value, (scalar,))
                except Exception as error:
                    raise ValueError(
                        f"action {self.name!r} does not implement its semimodule "
                        f"scale for {scalar!r}, {value!r}"
                    ) from error
                _require(actual, expected, f"bound scale for {scalar!r}, {value!r}")
            for left_scalar in law.scalar_samples:
                _require(
                    law.scalar_add(scalar, left_scalar),
                    law.scalar_add(left_scalar, scalar),
                    "scalar additive commutativity",
                )
                for right_scalar in law.scalar_samples:
                    _require(
                        law.scalar_add(
                            law.scalar_add(scalar, left_scalar), right_scalar
                        ),
                        law.scalar_add(
                            scalar, law.scalar_add(left_scalar, right_scalar)
                        ),
                        "scalar additive associativity",
                    )
                    _require(
                        law.scalar_multiply(
                            law.scalar_multiply(scalar, left_scalar), right_scalar
                        ),
                        law.scalar_multiply(
                            scalar, law.scalar_multiply(left_scalar, right_scalar)
                        ),
                        "scalar multiplicative associativity",
                    )
                    _require(
                        law.scalar_multiply(
                            scalar, law.scalar_add(left_scalar, right_scalar)
                        ),
                        law.scalar_add(
                            law.scalar_multiply(scalar, left_scalar),
                            law.scalar_multiply(scalar, right_scalar),
                        ),
                        "left scalar distributivity",
                    )
                    _require(
                        law.scalar_multiply(
                            law.scalar_add(left_scalar, right_scalar), scalar
                        ),
                        law.scalar_add(
                            law.scalar_multiply(left_scalar, scalar),
                            law.scalar_multiply(right_scalar, scalar),
                        ),
                        "right scalar distributivity",
                    )
        for scalar in law.scalar_samples:
            for left in law.module_samples:
                for right in law.module_samples:
                    _require(
                        law.scale(scalar, law.module_add(left, right)),
                        law.module_add(
                            law.scale(scalar, left), law.scale(scalar, right)
                        ),
                        "scale distribution over module addition",
                    )
        for left_scalar in law.scalar_samples:
            for right_scalar in law.scalar_samples:
                for value in law.module_samples:
                    _require(
                        law.scale(law.scalar_add(left_scalar, right_scalar), value),
                        law.module_add(
                            law.scale(left_scalar, value),
                            law.scale(right_scalar, value),
                        ),
                        "scale distribution over scalar addition",
                    )
                    _require(
                        law.scale(
                            law.scalar_multiply(left_scalar, right_scalar), value
                        ),
                        law.scale(left_scalar, law.scale(right_scalar, value)),
                        "scale compatibility with scalar multiplication",
                    )


@dataclass(frozen=True, slots=True)
class ReactDeclaration[Value, Carrier, Result]:
    """Bind recognition, yield, normalization, action, and react mode.

    One-for-one first materializes and structurally orders the complete yield,
    then calls the action separately for each recognition. It therefore costs
    more calls and no less memory than transactional mode. In one-for-one mode,
    supplying a ``distribution`` additionally computes the transactional result
    and checks equivalence for that run. Without one, the caller gives up that
    executable equivalence check and avoids computing both modes. Distribution
    witnesses are refused in transactional mode, where equivalence is not a live
    property.
    """

    name: str
    fold: FoldDeclaration[Value]
    yield_coordinates: CoordinateYield
    action: ActionDeclaration[Carrier, Result]
    normalization: YieldNormalization = YieldNormalization()
    mode: ReactMode = ReactMode.TRANSACTIONAL
    distribution: DistributionWitness | None = None

    def __post_init__(self) -> None:
        """Refuse action-policy mismatches before recognition can run."""
        if not self.name:
            raise ValueError("react name '' must not be empty")
        if self.distribution is not None and self.mode is not ReactMode.ONE_FOR_ONE:
            raise ValueError(
                f"react {self.name!r} distribution witness requires one-for-one "
                f"mode, got {self.mode.value!r}"
            )
        if self.normalization.collapse:
            if not self.action.associative:
                raise ValueError(
                    f"react {self.name!r} collapse requires associative action "
                    f"{self.action.name!r}"
                )
            if not self.action.idempotent:
                raise ValueError(
                    f"react {self.name!r} collapse requires idempotent action "
                    f"{self.action.name!r}"
                )
        if self.normalization.unique:
            if not self.action.idempotent:
                raise ValueError(
                    f"react {self.name!r} uniquing requires idempotent action "
                    f"{self.action.name!r}"
                )
            if not self.action.commutative:
                raise ValueError(
                    f"react {self.name!r} uniquing requires commutative action "
                    f"{self.action.name!r}"
                )
        if self.normalization.reorder and not self.action.commutative:
            raise ValueError(
                f"react {self.name!r} reordering requires commutative action "
                f"{self.action.name!r}"
            )
        if self.mode is ReactMode.ONE_FOR_ONE:
            if self.normalization.requires_complete_yield:
                raise ValueError(
                    f"react {self.name!r} one-for-one mode cannot normalize a complete yield"
                )

    def run(self, carrier: Carrier) -> dict[str, object]:
        """Recognize and apply, optionally certifying equivalence for this run.

        Equivalence depends on the caller's carrier, so it is checked here and
        cannot in general be decided when the declaration is constructed.
        """
        recognition = self.fold.run()
        coordinates = self._coordinates(recognition)
        if self.mode is ReactMode.TRANSACTIONAL:
            normalized = self.normalization.apply(coordinates)
            result = self.action.apply(
                carrier, tuple(item.value for item in normalized)
            )
        else:
            ordered = tuple(sorted(coordinates, key=lambda item: item.position))
            transactional: Result | None = None
            if self.distribution is not None:
                transactional = self.action.apply(
                    carrier, tuple(item.value for item in ordered)
                )
            current = carrier
            for coordinate in ordered:
                current = cast(Carrier, self.action.apply(current, (coordinate.value,)))
            result = cast(Result, current)
            if self.distribution is not None and result != transactional:
                raise ActionEquivalenceError(
                    f"distribution witness {self.distribution.name!r} refuses "
                    f"react {self.name!r} one-for-one result differs from "
                    f"transactional result for action {self.action.name!r}: "
                    f"{result!r} != {transactional!r}"
                )
        _require_json(result, f"action {self.action.name!r} result")
        return {
            "recognition": recognition.to_data(self.fold.semiring),
            "mode": self.mode.value,
            "result": result,
        }

    def _coordinates(
        self, recognition: FoldResult[Value]
    ) -> tuple[WitnessCoordinate, ...]:
        """Extract a yield only from witness provenance."""
        if recognition.provenance is None:
            raise ValueError(f"react {self.name!r} recognition produced no witnesses")
        coordinates = self.yield_coordinates(recognition.provenance)
        for coordinate in coordinates:
            _require_json(coordinate.value, f"react {self.name!r} coordinate")
        return coordinates


def _collapse(
    coordinates: tuple[WitnessCoordinate, ...],
) -> tuple[WitnessCoordinate, ...]:
    """Collapse adjacent equal values after structural ordering."""
    result: list[WitnessCoordinate] = []
    for coordinate in coordinates:
        if not result or result[-1].value != coordinate.value:
            result.append(coordinate)
    return tuple(result)


def _unique(
    coordinates: tuple[WitnessCoordinate, ...],
) -> tuple[WitnessCoordinate, ...]:
    """Keep the structurally first occurrence of each JSON value."""
    result: list[WitnessCoordinate] = []
    seen: set[str] = set()
    for coordinate in coordinates:
        key = _json_key(coordinate.value)
        if key not in seen:
            seen.add(key)
            result.append(coordinate)
    return tuple(result)


def _coordinate_key(coordinate: WitnessCoordinate) -> tuple[str, tuple[int, ...]]:
    """Return a deterministic value order with structure as its tie breaker."""
    return _json_key(coordinate.value), coordinate.position


def _json_key(value: object) -> str:
    """Return strict canonical JSON text for comparison and uniquing."""
    return json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _require_json(value: object, offender: str) -> None:
    """Refuse a non-JSON public value while naming its producer."""
    try:
        _json_key(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{offender} is not strict-JSON serializable") from error


__all__ = [
    "ActionDeclaration",
    "ActionFunction",
    "CoordinateYield",
    "DistributionWitness",
    "ReactDeclaration",
    "ReactMode",
    "Semimodule",
    "WitnessCoordinate",
    "YieldNormalization",
]
