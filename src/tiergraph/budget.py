"""Opt-in deterministic work budgets for tiergraph operations."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum

from tiergraph.core import Refusal, RefusalStage

_DEADLINE_INTERVAL = 4096
# Text-facing interfaces accept a bounded decimal integer even though the Python
# API deliberately accepts any positive ``int``.  This keeps hostile JSON and
# command lines from smuggling arbitrarily large integer spellings into policy
# or logs while leaving ordinary budgets far above practical CLI work.
_MAX_USER_STEPS = 1_000_000_000
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

    __slots__ = (
        "_budget",
        "_deadline",
        "_next_deadline_check",
        "_operation",
        "_spent",
        "_step_limit",
        "charge",
    )

    charge: Callable[[int], None]

    def __init__(self, budget: WorkBudget) -> None:
        if not isinstance(budget, WorkBudget):
            raise TypeError("WorkMeter budget must be a WorkBudget")
        self._budget = budget
        self._spent = 0
        # Charging is the hot path. Keep the validated integer beside the
        # mutable count instead of walking through the declaration each time.
        self._step_limit = budget.steps
        self._next_deadline_check = _DEADLINE_INTERVAL
        self._deadline = None if budget.seconds is None else _clock() + budget.seconds
        self._operation: str | None = None
        if self._step_limit is not None and self._deadline is None:
            self.charge = self._charge_steps
        else:
            self.charge = self._charge_timed

    @property
    def budget(self) -> WorkBudget:
        """Return the declaration this meter enforces."""
        return self._budget

    @property
    def spent(self) -> int:
        """Return logical steps charged so far, including a crossing charge."""
        return self._spent

    def _charge_steps(self, steps: int) -> None:
        """Charge the common step-only declaration."""
        if steps < 0:
            raise ValueError("work charge must be nonnegative")
        spent = self._spent + steps
        self._spent = spent
        limit = self._step_limit
        assert limit is not None
        if spent > limit:
            operation = self._operation
            assert operation is not None
            raise BudgetExhausted(operation, Exhaustion.STEPS, spent, self._budget)

    def _charge_timed(self, steps: int) -> None:
        """Charge a declaration that includes a deadline."""
        if steps < 0:
            raise ValueError("work charge must be nonnegative")
        spent = self._spent + steps
        self._spent = spent
        limit = self._step_limit
        if limit is not None and spent > limit:
            operation = self._operation
            assert operation is not None
            raise BudgetExhausted(operation, Exhaustion.STEPS, spent, self._budget)
        assert self._deadline is not None
        operation = self._operation
        assert operation is not None
        _check_deadline(self, operation)


@dataclass(frozen=True, slots=True)
class _Meter:
    meter: WorkMeter
    operation: str
    parent: WorkMeter | _Meter | None

    def charge(self, steps: int) -> None:
        """Charge this meter and every distinct ancestor meter."""
        if steps < 0:
            raise ValueError("work charge must be nonnegative")
        seen: set[int] = set()
        current: WorkMeter | _Meter | None = self
        while current is not None:
            if isinstance(current, WorkMeter):
                public = current
                operation = current._operation
                assert operation is not None
                parent = None
            else:
                public = current.meter
                operation = current.operation
                parent = current.parent
            identity = id(public)
            if identity not in seen:
                seen.add(identity)
                public._spent += steps
                limit = public._step_limit
                spent = public._spent
                if limit is not None and spent > limit:
                    raise BudgetExhausted(
                        operation,
                        Exhaustion.STEPS,
                        spent,
                        public._budget,
                    )
                _check_deadline(public, operation)
            current = parent


type _ChargeMeter = WorkMeter | _Meter


def _check_deadline(
    public: WorkMeter, operation: str, *, installation: bool = False
) -> None:
    deadline = public._deadline
    if deadline is None:
        return
    spent = public._spent
    if not installation and spent < public._next_deadline_check:
        return
    public._next_deadline_check = (
        spent - spent % _DEADLINE_INTERVAL + _DEADLINE_INTERVAL
    )
    if _clock() >= deadline:
        raise BudgetExhausted(operation, Exhaustion.DEADLINE, spent, public._budget)


@dataclass(frozen=True, slots=True)
class _Metered:
    meter: _ChargeMeter | None
    owns_outermost: bool


_ACTIVE: ContextVar[_ChargeMeter | None] = ContextVar(
    "tiergraph_work_meter", default=None
)
_AGGREGATE_ACTIVE: ContextVar[bool] = ContextVar(
    "tiergraph_semiring_aggregate", default=False
)


def _active_meter() -> _ChargeMeter | None:
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


def _contains_meter(active: _ChargeMeter | None, meter: WorkMeter) -> bool:
    while active is not None:
        if active is meter:
            return True
        if isinstance(active, WorkMeter):
            return False
        if active.meter is meter:
            return True
        active = active.parent
    return False


@contextmanager
def _unchecked_charging(upper_bound: int) -> Iterator[WorkMeter | None]:
    """Use the cheap root charge when a finite region fits its headroom."""
    active = _ACTIVE.get()
    if not isinstance(active, WorkMeter) or active._deadline is not None:
        yield None
        return
    limit = active._step_limit
    if limit is None or active._spent + upper_bound > limit:
        yield None
        return
    yield active


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
        operation_name = (
            active._operation if isinstance(active, WorkMeter) else active.operation
        )
        assert operation_name is not None
        active_public = active if isinstance(active, WorkMeter) else active.meter
        _check_deadline(active_public, operation_name, installation=True)
        yield _Metered(active, False)
        return
    if active is None:
        previous_operation = public._operation
        public._operation = operation
        try:
            _check_deadline(public, operation, installation=True)
        except BaseException:
            public._operation = previous_operation
            raise
        installed: _ChargeMeter = public
    else:
        installed = _Meter(public, operation, active)
        _check_deadline(public, operation, installation=True)
        previous_operation = None
    token = _ACTIVE.set(installed)
    try:
        yield _Metered(installed, active is None)
    finally:
        _ACTIVE.reset(token)
        if active is None:
            public._operation = previous_operation
