"""Canonical span views for large finite pattern repeats."""

from __future__ import annotations

from collections.abc import Callable
from copy import copy, deepcopy
from functools import partial
from importlib import import_module

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import tiergraph.match as match_module
from tests.test_alt_gate import TIER, _views, atom, graph
from tests.test_lattice_match import lat_a
from tiergraph import Graph, Refusal
from tiergraph.budget import BudgetExhausted, WorkBudget, WorkMeter
from tiergraph.match import (
    AltPattern,
    EndPattern,
    Extent,
    FocusPattern,
    Pattern,
    RepeatPattern,
    SeqPattern,
    StartPattern,
    TierOrder,
    compile_pattern,
)
from tiergraph.pathoutput import Determinize, Unambiguous, match_lattice
from tiergraph.predicate import Bare, Current, Equals


def _with_repeat_view[T](enabled: bool, call: Callable[[], T]) -> T:
    previous = match_module._REPEAT_VIEW
    match_module._REPEAT_VIEW = enabled
    try:
        return call()
    finally:
        match_module._REPEAT_VIEW = previous


def _body(shape: int) -> Pattern:
    if shape == 0:
        return atom("a")
    if shape == 1:
        return SeqPattern((atom("a"), atom("b")))
    if shape == 2:
        return AltPattern((atom("a"), atom("b")))
    return AltPattern((atom("a"), RepeatPattern(atom("b"), 0, 1)))


@settings(max_examples=60, deadline=None)
@given(
    shape=st.integers(min_value=0, max_value=3),
    minimum=st.integers(min_value=0, max_value=2),
    optional=st.integers(min_value=32, max_value=35),
    labels=st.lists(st.sampled_from(("a", "b", "c")), max_size=10).map(tuple),
    focus_mode=st.integers(min_value=0, max_value=2),
    anchored_left=st.booleans(),
    anchored_right=st.booleans(),
    open_left=st.booleans(),
)
def test_repeat_private_view_property(
    shape: int,
    minimum: int,
    optional: int,
    labels: tuple[str, ...],
    focus_mode: int,
    anchored_left: bool,
    anchored_right: bool,
    open_left: bool,
) -> None:
    """Every span view agrees with the public chain on varied finite repeats."""
    parts: list[Pattern] = []
    if anchored_left:
        parts.append(StartPattern())
    repeated: Pattern = RepeatPattern(_body(shape), minimum, minimum + optional)
    focus_repeat = focus_mode == 2 and minimum > 0 and shape < 3
    focused = focus_mode != 0
    head = atom("c")
    parts.append(FocusPattern(head) if focused and not focus_repeat else head)
    parts.append(FocusPattern(repeated) if focus_repeat else repeated)
    parts.append(atom("b"))
    if anchored_right:
        parts.append(EndPattern())
    compiled = compile_pattern(SeqPattern(tuple(parts)))
    subject = graph(labels)

    assert compiled._repeat_views
    public = _with_repeat_view(
        False,
        partial(
            _views,
            compiled,
            subject,
            open_left=open_left,
            focused=focused,
        ),
    )
    private = _with_repeat_view(
        True,
        partial(
            _views,
            compiled,
            subject,
            open_left=open_left,
            focused=focused,
        ),
    )
    assert private == public


def test_repeat_reverse_view_matches_forward_view() -> None:
    """The focus reverse table is the exact inverse of private forward edges."""
    pattern = SeqPattern(
        (
            FocusPattern(atom("c")),
            RepeatPattern(SeqPattern((atom("a"), atom("b"))), 0, 34),
            atom("c"),
        )
    )
    compiled = compile_pattern(pattern)
    reverse = compiled._reverse_epsilon()
    assert compiled._repeat_views
    assert {
        (source, match_module._epsilon_target(edge), match_module._epsilon_guard(edge))
        for source in range(len(compiled.epsilon))
        for edge in compiled._span_edges(source)
    } == {
        (source, target, guard)
        for target, edges in enumerate(reverse)
        for source, guard in edges
    }


def test_repeat_preserves_lattice_observables() -> None:
    """Private repeat metadata never changes public NFA or lattice behavior."""

    def token(value: str) -> match_module.AtomPattern:
        return match_module.AtomPattern(Equals(Current(), (Bare(value),)))

    pattern = SeqPattern(
        (
            token("ten"),
            RepeatPattern(token("o"), 0, 32),
            token("five"),
        )
    )
    previous = match_module._REPEAT_VIEW_MIN_OPTIONAL
    try:
        match_module._REPEAT_VIEW_MIN_OPTIONAL = 32
        optimized = compile_pattern(pattern)
        match_module._REPEAT_VIEW_MIN_OPTIONAL = 33
        public = compile_pattern(pattern)
    finally:
        match_module._REPEAT_VIEW_MIN_OPTIONAL = previous

    assert optimized._repeat_views
    assert not public._repeat_views
    assert (
        optimized.epsilon,
        optimized.atom_edges,
        optimized.predicates,
        optimized.start,
        optimized.accept,
    ) == (
        public.epsilon,
        public.atom_edges,
        public.predicates,
        public.start,
        public.accept,
    )
    assert optimized == public
    assert repr(optimized) == repr(public)
    assert hash(optimized) == hash(public)

    def observables(compiled: match_module.CompiledPattern) -> tuple[object, ...]:
        _base, emissions = lat_a()
        matched = match_lattice(emissions, compiled)
        meter = WorkMeter(WorkBudget(steps=10**9))
        count = matched.count(Determinize(256), budget=meter)
        with pytest.raises(Refusal) as caught:
            matched.count(Unambiguous())
        return count, meter.spent, caught.value.stage, str(caught.value)

    assert observables(optimized) == observables(public)


def test_repeat_descriptor_threshold_and_object_round_trips() -> None:
    """Small repeats retain nothing; private descriptors survive cloning."""
    below = compile_pattern(RepeatPattern(atom("a"), 1, 32))
    boundary = compile_pattern(RepeatPattern(atom("a"), 1, 33))
    assert not below._repeat_views
    assert len(boundary._repeat_views) == 1

    pickle_module = import_module("pickle")
    for cloned in (
        copy(boundary),
        deepcopy(boundary),
        pickle_module.loads(pickle_module.dumps(boundary)),
    ):
        assert cloned == boundary
        assert cloned._repeat_views == boundary._repeat_views
        assert repr(cloned) == repr(boundary)
        assert hash(cloned) == hash(boundary)


def test_repeat_private_view_reduces_charged_work() -> None:
    """The declared repeat wall shape exercises and benefits from the view."""
    compiled = compile_pattern(
        SeqPattern(
            (
                atom("p0"),
                RepeatPattern(atom("p1"), 1, 10_000),
                atom("p2"),
            )
        )
    )
    subject = graph(("p0", "p1", "p2", *("x" for _ in range(997))))

    def measured() -> tuple[int, int]:
        meter = WorkMeter(WorkBudget(steps=10**8))
        result = compiled.count(subject, TierOrder(TIER), budget=meter)
        return result, meter.spent

    public = _with_repeat_view(False, measured)
    private = _with_repeat_view(True, measured)
    assert public[0] == private[0] == 1
    assert private[1] < public[1]


def _budget_spans(
    compiled: match_module.CompiledPattern,
    subject: Graph,
    enabled: bool,
    steps: int,
) -> tuple[tuple[tuple[int, int], ...], Extent] | None:
    def run() -> tuple[tuple[tuple[int, int], ...], Extent] | None:
        try:
            result = compiled.spans(
                subject, TierOrder(TIER), budget=WorkBudget(steps=steps)
            )
        except BudgetExhausted:
            return None
        return tuple((span.start, span.end) for span in result.matches), result.extent

    return _with_repeat_view(enabled, run)


def test_repeat_budget_sweep_only_extends_cut_prefixes() -> None:
    """The private skip chain never shortens a budgeted span prefix."""
    compiled = compile_pattern(
        SeqPattern(
            (
                atom("p0"),
                RepeatPattern(atom("p1"), 1, 33),
                atom("p2"),
            )
        )
    )
    subject = graph(("p0", "p1", "p2", "x"))

    meter = WorkMeter(WorkBudget(steps=10**9))
    complete = _with_repeat_view(
        False,
        lambda: compiled.spans(subject, TierOrder(TIER), budget=meter),
    )
    expected = (
        tuple((span.start, span.end) for span in complete.matches),
        complete.extent,
    )
    improved_boundary = False
    for steps in range(1, meter.spent + 1):
        public = _budget_spans(compiled, subject, False, steps)
        private = _budget_spans(compiled, subject, True, steps)
        if public is None:
            if private is not None:
                improved_boundary = True
            continue
        public_matches, public_extent = public
        assert private is not None
        private_matches, private_extent = private
        assert private_matches[: len(public_matches)] == public_matches
        if public_extent is Extent.EXHAUSTIVE:
            assert private == expected
        else:
            assert public_extent is Extent.CUT_AT_BUDGET
            assert private_extent in (Extent.CUT_AT_BUDGET, Extent.EXHAUSTIVE)
    assert improved_boundary
