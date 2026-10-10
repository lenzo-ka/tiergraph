"""Indexed lookup for fully keyable large pattern alternations."""

from __future__ import annotations

from collections.abc import Callable
from copy import copy, deepcopy
from functools import partial
from importlib import import_module

from hypothesis import given, settings
from hypothesis import strategies as st

import tiergraph.match as match_module
from tests.test_alt_gate import LABEL, TIER, atom, graph
from tiergraph.budget import BudgetExhausted, WorkBudget, WorkMeter
from tiergraph.match import (
    AltPattern,
    AtomPattern,
    EndPattern,
    Extent,
    FocusPattern,
    Pattern,
    RepeatPattern,
    SeqPattern,
    StartPattern,
    TierOrder,
    _PatternOperation,
    _Scope,
    compile_pattern,
)
from tiergraph.predicate import Bare, Cell, Equals, Has


def _compile_scan(pattern: Pattern) -> match_module.CompiledPattern:
    """Compile with the shipped successor scan as a comparison oracle."""
    original = match_module._indexed_gate
    match_module._indexed_gate = lambda *args, **kwargs: None
    try:
        return compile_pattern(pattern)
    finally:
        match_module._indexed_gate = original


def _observables(
    compiled: match_module.CompiledPattern,
    labels: tuple[str, ...],
    *,
    focused: bool = False,
) -> dict[str, object]:
    subject = graph(labels)
    order = TierOrder(TIER)
    result: dict[str, object] = {
        "public-exists": _outcome(lambda: compiled.exists(subject, order)),
        "public-exists-open": _outcome(
            lambda: compiled.exists(subject, order, open_right=True)
        ),
        "public-count": _outcome(lambda: compiled.count(subject, order)),
        "public-count-open": _outcome(
            lambda: compiled.count(subject, order, open_right=True)
        ),
        "public-spans": _outcome(lambda: compiled.spans(subject, order)),
        "public-spans-limit": _outcome(lambda: compiled.spans(subject, order, limit=2)),
        "public-spans-open": _outcome(
            lambda: compiled.spans(subject, order, open_right=True)
        ),
    }
    if focused:
        result["public-focus"] = _outcome(lambda: compiled.focus(subject, order))
        result["public-focus-open"] = _outcome(
            lambda: compiled.focus(subject, order, open_right=True)
        )

    raw_scopes, truth = compiled._prepare(
        subject,
        order,
        _PatternOperation.FOCUS if focused else _PatternOperation.EXISTS,
    )
    nodes = raw_scopes[0].nodes
    midpoint = len(nodes) // 2
    layouts = {
        "closed": raw_scopes,
        "open-left": (_Scope(nodes, open_left=True),),
        "reversed": (_Scope(tuple(reversed(nodes))),),
        "multiple": (
            _Scope(nodes[:midpoint], open_right=True),
            _Scope(nodes[midpoint:], open_left=True),
        ),
    }
    for layout, scopes in layouts.items():
        result[f"{layout}-exists"] = _outcome(
            partial(compiled._exists_over, scopes, truth)
        )
        result[f"{layout}-exists-open"] = _outcome(
            partial(compiled._exists_over, scopes, truth, open_right=True)
        )
        result[f"{layout}-count"] = _outcome(
            partial(compiled._count_over, scopes, truth)
        )
        result[f"{layout}-count-open"] = _outcome(
            partial(compiled._count_over, scopes, truth, open_right=True)
        )
        result[f"{layout}-spans"] = _outcome(
            partial(compiled._spans_over, scopes, truth)
        )
        result[f"{layout}-spans-limit"] = _outcome(
            partial(compiled._spans_over, scopes, truth, limit=2)
        )
        result[f"{layout}-spans-open"] = _outcome(
            partial(compiled._spans_over, scopes, truth, open_right=True)
        )
        if focused:
            result[f"{layout}-focus"] = _outcome(
                partial(compiled._focus_over, subject, scopes, truth)
            )
            result[f"{layout}-focus-open"] = _outcome(
                partial(
                    compiled._focus_over,
                    subject,
                    scopes,
                    truth,
                    open_right=True,
                )
            )
    return result


def _outcome(call: Callable[[], object]) -> tuple[object, ...]:
    try:
        return ("return", call())
    except Exception as error:
        return ("raise", type(error), getattr(error, "stage", None), str(error))


def _gate(
    compiled: match_module.CompiledPattern, state: int
) -> match_module._GateMetadata:
    assert compiled._gates is not None
    return compiled._gates[state]


def _keyed_part(specification: tuple[int, int, int]) -> Pattern:
    shape, first, second = specification
    left = atom("abc"[first])
    right = atom("abc"[second])
    if shape == 0:
        return left
    if shape == 1:
        return SeqPattern((left, right))
    if shape == 2:
        return RepeatPattern(left, 0, 2)
    if shape == 3:
        return SeqPattern((RepeatPattern(left, 0, 1), right))
    if shape == 4:
        return SeqPattern((StartPattern(), left))
    return SeqPattern((left, EndPattern()))


@settings(max_examples=40, deadline=None)
@given(
    specifications=st.lists(
        st.tuples(
            st.integers(min_value=0, max_value=5),
            st.integers(min_value=0, max_value=2),
            st.integers(min_value=0, max_value=2),
        ),
        min_size=2,
        max_size=7,
    ),
    labels=st.lists(st.sampled_from(("a", "b", "c")), max_size=8).map(tuple),
)
def test_index_matches_scan_property(
    specifications: list[tuple[int, int, int]], labels: tuple[str, ...]
) -> None:
    """Indexed and scan metadata answer every tested view identically."""
    original = match_module._GATE_MIN_PARTS
    match_module._GATE_MIN_PARTS = 2
    try:
        choice = AltPattern(tuple(_keyed_part(spec) for spec in specifications))
        patterns = (
            (choice, False),
            (SeqPattern((FocusPattern(atom("a")), choice)), True),
        )
        for pattern, focused in patterns:
            indexed = compile_pattern(pattern)
            scanned = _compile_scan(pattern)
            assert indexed._gates is not None
            assert any(
                isinstance(gate, match_module._GateIndex)
                for _state, gate in indexed._gates.entries
            )
            assert _observables(indexed, labels, focused=focused) == _observables(
                scanned, labels, focused=focused
            )
    finally:
        match_module._GATE_MIN_PARTS = original


def test_index_keeps_optional_tail_exit() -> None:
    """A later mismatch cannot discard a branch that already accepts."""
    ab = SeqPattern((atom("a"), atom("b")))
    optional = SeqPattern((atom("a"), RepeatPattern(atom("b"), 0, 1)))
    compiled = compile_pattern(AltPattern((*((ab,) * 64), optional)))
    gate = _gate(compiled, compiled.start)
    assert isinstance(gate, match_module._GateIndex)
    assert gate.depths[1].exits
    assert compiled.count(graph(("a", "c")), TierOrder(TIER)) == 1


def test_index_unions_overlapping_atoms() -> None:
    """All true atom postings contribute once in original successor order."""
    overlap = AtomPattern(
        Equals(
            Cell(LABEL),
            (Bare("a"), Bare("b")),
        )
    )
    pattern = AltPattern(tuple((atom("a"), overlap) * 16))
    indexed = compile_pattern(pattern)
    scanned = _compile_scan(pattern)
    assert isinstance(_gate(indexed, indexed.start), match_module._GateIndex)
    assert _observables(indexed, ("a", "b")) == _observables(scanned, ("a", "b"))
    indexed_count, indexed_spent = _spent(indexed, ("a", "b"))
    scanned_count, scanned_spent = _spent(scanned, ("a", "b"))
    assert indexed_count == scanned_count
    assert indexed_spent <= scanned_spent


def test_index_falls_back_for_opaque_or_dense_split() -> None:
    """Unsupported predicates and wide postings retain scan metadata."""
    opaque = AtomPattern(Has(Cell(LABEL), "label"))
    opaque_pattern = AltPattern(tuple(opaque for _ in range(32)))
    opaque_compiled = compile_pattern(opaque_pattern)
    assert isinstance(
        _gate(opaque_compiled, opaque_compiled.start),
        match_module._Gate,
    )

    choices = AltPattern((atom("a"), atom("b"), atom("c"), atom("d")))
    dense_pattern = AltPattern(
        tuple(SeqPattern((choices, atom(str(index)))) for index in range(32))
    )
    dense = compile_pattern(dense_pattern)
    assert isinstance(_gate(dense, dense.start), match_module._Gate)


def test_index_does_not_read_past_scope_end_and_keeps_unknowns() -> None:
    """Unavailable lookahead and bounded-analysis failure only admit branches."""
    original_parts = match_module._GATE_MIN_PARTS
    original_fragment = match_module._GATE_FRAGMENT_STATES
    match_module._GATE_MIN_PARTS = 2
    match_module._GATE_FRAGMENT_STATES = 2
    try:
        pattern = AltPattern(
            (
                SeqPattern((atom("a"), atom("b"), atom("c"))),
                SeqPattern((atom("a"), RepeatPattern(atom("b"), 0, 2))),
            )
        )
        indexed = compile_pattern(pattern)
        scanned = _compile_scan(pattern)
        gate = _gate(indexed, indexed.start)
        assert isinstance(gate, match_module._GateIndex)
        assert any(depth.unknown for depth in gate.depths)
        for labels in (("a",), ("a", "x"), ("a", "b")):
            assert _observables(indexed, labels) == _observables(scanned, labels)
    finally:
        match_module._GATE_MIN_PARTS = original_parts
        match_module._GATE_FRAGMENT_STATES = original_fragment


def _spent(
    compiled: match_module.CompiledPattern, labels: tuple[str, ...]
) -> tuple[int, int]:
    meter = WorkMeter(WorkBudget(steps=10**9))
    result = compiled.count(graph(labels), TierOrder(TIER), budget=meter)
    assert isinstance(result, int)
    return result, meter.spent


def _budget_spans(
    compiled: match_module.CompiledPattern,
    labels: tuple[str, ...],
    steps: int,
) -> tuple[tuple[tuple[int, int], ...], Extent] | None:
    try:
        result = compiled.spans(
            graph(labels), TierOrder(TIER), budget=WorkBudget(steps=steps)
        )
    except BudgetExhausted:
        return None
    return tuple((span.start, span.end) for span in result.matches), result.extent


def test_index_budget_sweep_is_monotone() -> None:
    """Distinct-candidate charging never exceeds the shipped scan."""
    pattern = AltPattern(
        tuple(
            SeqPattern((atom(f"a{index % 4}"), atom(f"b{index}")))
            for index in range(32)
        )
    )
    indexed = compile_pattern(pattern)
    scanned = _compile_scan(pattern)
    labels = ("a0", "b0", "a3", "miss")
    assert _spent(indexed, labels)[0] == _spent(scanned, labels)[0]
    assert _spent(indexed, labels)[1] <= _spent(scanned, labels)[1]
    full = _budget_spans(indexed, labels, 10**9)
    assert full is not None
    for steps in range(1, _spent(scanned, labels)[1] + 1):
        parent = _budget_spans(scanned, labels, steps)
        candidate = _budget_spans(indexed, labels, steps)
        if parent is None:
            continue
        parent_matches, parent_extent = parent
        assert candidate is not None
        candidate_matches, candidate_extent = candidate
        assert candidate_matches[: len(parent_matches)] == parent_matches
        if parent_extent is Extent.EXHAUSTIVE:
            assert candidate == full
        else:
            assert parent_extent is Extent.CUT_AT_BUDGET
            assert candidate_extent in (Extent.CUT_AT_BUDGET, Extent.EXHAUSTIVE)


def test_index_is_private_stable_and_inert_when_small() -> None:
    """Only private metadata changes, and sub-threshold patterns stay inert."""
    small = compile_pattern(AltPattern(tuple(atom(str(index)) for index in range(31))))
    assert small._gates is None
    pattern = AltPattern(tuple(atom(str(index)) for index in range(32)))
    indexed = compile_pattern(pattern)
    scanned = _compile_scan(pattern)
    assert isinstance(_gate(indexed, indexed.start), match_module._GateIndex)
    assert indexed == scanned
    assert repr(indexed) == repr(scanned)
    assert hash(indexed) == hash(scanned)
    pickle_module = import_module("pickle")
    for clone in (
        copy(indexed),
        deepcopy(indexed),
        pickle_module.loads(pickle_module.dumps(indexed)),
    ):
        assert clone == indexed
        assert clone._gates == indexed._gates

    epsilon = list(indexed.epsilon)
    epsilon[indexed.start] = (
        match_module._pack_epsilon(indexed.accept, match_module._GUARD_END),
        *epsilon[indexed.start],
    )
    mixed = match_module.CompiledPattern(
        pattern,
        indexed.start,
        indexed.accept,
        tuple(epsilon),
        indexed.atom_edges,
        indexed.predicates,
    )
    mixed_gate = _gate(mixed, mixed.start)
    assert isinstance(mixed_gate, match_module._GateIndex)
    assert mixed_gate.successor_ordinals is not None
    assert mixed_gate.passthrough_ordinals == (0,)
    assert mixed.exists(graph(("0",)), TierOrder(TIER))
    scopes, truth = mixed._prepare(
        graph(("0",)), TierOrder(TIER), match_module._PatternOperation.EXISTS
    )
    assert isinstance(truth, match_module._IndexedTruth)
    full_edges, tested = mixed._gated_edges(
        mixed.start, scopes[0].nodes, truth.rows, 0, 1
    )
    assert full_edges == mixed.epsilon[mixed.start]
    assert len(tested) == 32
