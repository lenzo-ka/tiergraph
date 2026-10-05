"""Opt-in deterministic work budgets for tiergraph operations."""

from __future__ import annotations

import math
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum

from tiergraph.core import Refusal, RefusalStage

_DEADLINE_INTERVAL = 4096
_clock = time.monotonic


@dataclass(frozen=True, slots=True)
class WorkBudget:
    """Declare how much work one operation may do before it stops."""

    steps: int | None = None
    seconds: float | None = None

    def __post_init__(self) -> None:
        if self.steps is None and self.seconds is None:
            raise ValueError("a work budget needs steps, seconds, or both")
        if self.steps is not None and (type(self.steps) is not int or self.steps <= 0):
            raise ValueError("work budget steps must be a positive integer or None")
        if self.seconds is not None and (
            isinstance(self.seconds, bool)
            or not isinstance(self.seconds, int | float)
            or not math.isfinite(self.seconds)
            or self.seconds <= 0
        ):
            raise ValueError(
                "work budget seconds must be a finite positive number or None"
            )


class Exhaustion(StrEnum):
    """Name which declared work bound stopped an operation."""

    STEPS = "steps"
    DEADLINE = "deadline"


class BudgetExhausted(Refusal):
    """Refuse an operation whose declared work budget ran out."""

    operation: str
    exhaustion: Exhaustion
    spent: int
    budget: WorkBudget

    def __init__(
        self,
        operation: str,
        exhaustion: Exhaustion,
        spent: int,
        budget: WorkBudget,
    ) -> None:
        self.operation = operation
        self.exhaustion = exhaustion
        self.spent = spent
        self.budget = budget
        if exhaustion is Exhaustion.STEPS:
            message = (
                f"{operation} exhausted its work budget after {spent} steps "
                f"(limit {budget.steps})"
            )
        else:
            message = (
                f"{operation} reached its {budget.seconds}s deadline after "
                f"{spent} steps"
            )
        super().__init__(RefusalStage.SEMANTICS, message)


class WorkMeter:
    """Charge one budget across calls and report the work they spent.

    A meter is intended for one thread or task at a time.
    """

    __slots__ = ("_budget", "_deadline", "_next_deadline_check", "_spent")

    def __init__(self, budget: WorkBudget) -> None:
        if not isinstance(budget, WorkBudget):
            raise TypeError("WorkMeter budget must be a WorkBudget")
        self._budget = budget
        self._spent = 0
        self._next_deadline_check = _DEADLINE_INTERVAL
        self._deadline = None if budget.seconds is None else _clock() + budget.seconds

    @property
    def budget(self) -> WorkBudget:
        """Return the declaration this meter enforces."""
        return self._budget

    @property
    def spent(self) -> int:
        """Return logical steps charged so far, including a crossing charge."""
        return self._spent


@dataclass(frozen=True, slots=True)
class _Meter:
    meter: WorkMeter
    operation: str
    parent: _Meter | None

    def charge(self, steps: int) -> None:
        """Charge this meter and every distinct ancestor meter."""
        if steps < 0:
            raise ValueError("work charge must be nonnegative")
        if self.parent is None:
            public = self.meter
            spent = public._spent + steps
            public._spent = spent
            limit = public._budget.steps
            if limit is not None and spent > limit:
                raise BudgetExhausted(
                    self.operation,
                    Exhaustion.STEPS,
                    spent,
                    public._budget,
                )
            if public._deadline is not None:
                self._check_deadline(self)
            return
        seen: set[int] = set()
        current: _Meter | None = self
        while current is not None:
            public = current.meter
            identity = id(public)
            if identity not in seen:
                seen.add(identity)
                public._spent += steps
                limit = public.budget.steps
                if limit is not None and public.spent > limit:
                    raise BudgetExhausted(
                        current.operation,
                        Exhaustion.STEPS,
                        public.spent,
                        public.budget,
                    )
                self._check_deadline(current)
            current = current.parent

    @staticmethod
    def _check_deadline(current: _Meter, *, installation: bool = False) -> None:
        public = current.meter
        deadline = public._deadline
        if deadline is None:
            return
        if not installation and public.spent < public._next_deadline_check:
            return
        public._next_deadline_check = (
            public.spent - public.spent % _DEADLINE_INTERVAL + _DEADLINE_INTERVAL
        )
        if _clock() >= deadline:
            raise BudgetExhausted(
                current.operation,
                Exhaustion.DEADLINE,
                public.spent,
                public.budget,
            )


@dataclass(frozen=True, slots=True)
class _Metered:
    meter: _Meter | None
    owns_outermost: bool


_ACTIVE: ContextVar[_Meter | None] = ContextVar("tiergraph_work_meter", default=None)
_AGGREGATE_ACTIVE: ContextVar[bool] = ContextVar(
    "tiergraph_semiring_aggregate", default=False
)


def _active_meter() -> _Meter | None:
    """Return the ambient meter, if this dynamic extent has one."""
    return _ACTIVE.get()


def _aggregate_active() -> bool:
    """Return whether a semiring aggregate owns this dynamic extent."""
    return _AGGREGATE_ACTIVE.get()


@contextmanager
def _aggregating() -> Iterator[None]:
    """Prevent partial witness lists from entering a semiring aggregate."""
    token = _AGGREGATE_ACTIVE.set(True)
    try:
        yield
    finally:
        _AGGREGATE_ACTIVE.reset(token)


def _contains_meter(active: _Meter | None, meter: WorkMeter) -> bool:
    while active is not None:
        if active.meter is meter:
            return True
        active = active.parent
    return False


@contextmanager
def _metered(
    budget: WorkBudget | WorkMeter | None, operation: str
) -> Iterator[_Metered]:
    """Install one declared meter while preserving an ambient ancestor chain."""
    active = _ACTIVE.get()
    if budget is None:
        yield _Metered(active, False)
        return
    public = WorkMeter(budget) if isinstance(budget, WorkBudget) else budget
    if not isinstance(public, WorkMeter):
        raise TypeError("budget must be a WorkBudget, WorkMeter, or None")
    if _contains_meter(active, public):
        assert active is not None
        active._check_deadline(active, installation=True)
        yield _Metered(active, False)
        return
    node = _Meter(public, operation, active)
    node._check_deadline(node, installation=True)
    token = _ACTIVE.set(node)
    try:
        yield _Metered(node, active is None)
    finally:
        _ACTIVE.reset(token)
