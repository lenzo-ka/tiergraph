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
Target = TypeVar("Target")


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
    """Choose interleaved or complete-yield recognize-act execution."""

    ONE_FOR_ONE = "one-for-one"
    TRANSACTIONAL = "transactional"


@dataclass(frozen=True, slots=True)
class YieldNormalization:
    """Declare complete-yield transformations requested before action."""

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
class DistributionWitness[Source, Target]:
    """Execute the homomorphism condition required by one-for-one react."""

    name: str
    samples: tuple[Source, ...]
    source_add: Callable[[Source, Source], Source]
    mapping: Callable[[Source], Target]
    target_add: Callable[[Target, Target], Target]

    def __post_init__(self) -> None:
        """Refuse an empty or non-distributing witness and name it."""
        if not self.name:
            raise ValueError("distribution witness name '' must not be empty")
        if not self.samples:
            raise ValueError(f"distribution witness {self.name!r} has no samples")
        for left in self.samples:
            for right in self.samples:
                mapped_sum = self.mapping(self.source_add(left, right))
                sum_mapped = self.target_add(self.mapping(left), self.mapping(right))
                if mapped_sum != sum_mapped:
                    raise ValueError(
                        f"distribution witness {self.name!r} fails for "
                        f"{left!r} and {right!r}"
                    )


@dataclass(frozen=True, slots=True)
class Semimodule[Scalar, Module]:
    """Supply the operations and samples for an optional semimodule claim."""

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
    """Declare an action's executable behavior and normalization tolerances."""

    name: str
    apply: ActionFunction[Carrier, Result]
    associative: bool
    idempotent: bool
    commutative: bool
    semimodule: Semimodule[object, object] | None = None

    def __post_init__(self) -> None:
        """Require a public name for declaration-time diagnostics."""
        if not self.name:
            raise ValueError("action name '' must not be empty")


@dataclass(frozen=True, slots=True)
class ReactDeclaration[Value, Carrier, Result]:
    """Bind recognition, yield, normalization, action, and react mode."""

    name: str
    fold: FoldDeclaration[Value]
    yield_coordinates: CoordinateYield
    action: ActionDeclaration[Carrier, Result]
    normalization: YieldNormalization = YieldNormalization()
    mode: ReactMode = ReactMode.TRANSACTIONAL
    distribution: DistributionWitness[object, object] | None = None

    def __post_init__(self) -> None:
        """Refuse action-policy mismatches before recognition can run."""
        if not self.name:
            raise ValueError("react name '' must not be empty")
        if self.normalization.collapse and not self.action.associative:
            raise ValueError(
                f"react {self.name!r} collapse requires associative action "
                f"{self.action.name!r}"
            )
        if self.normalization.unique and not self.action.idempotent:
            raise ValueError(
                f"react {self.name!r} uniquing requires idempotent action "
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
            if self.distribution is None:
                raise ValueError(
                    f"react {self.name!r} one-for-one action "
                    f"{self.action.name!r} has no distribution witness"
                )

    def run(self, carrier: Carrier) -> dict[str, object]:
        """Recognize and apply the declared action without inspecting its carrier."""
        recognition = self.fold.run()
        coordinates = self._coordinates(recognition)
        if self.mode is ReactMode.TRANSACTIONAL:
            normalized = self.normalization.apply(coordinates)
            result = self.action.apply(
                carrier, tuple(item.value for item in normalized)
            )
        else:
            current = carrier
            for coordinate in sorted(coordinates, key=lambda item: item.position):
                current = cast(Carrier, self.action.apply(current, (coordinate.value,)))
            result = cast(Result, current)
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
