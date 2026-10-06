"""Safe per-successor pruning for large pattern alternations."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from copy import copy, deepcopy
from functools import partial
from importlib import import_module
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import tiergraph.match as match_module
from tests.test_lattice_match import lat_a
from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    Graph,
    Item,
    NamespaceDeclaration,
    QualifiedName,
    Refusal,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    XsdType,
)
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
from tiergraph.pathoutput import Determinize, Unambiguous, match_lattice
from tiergraph.predicate import Bare, Cell, Current, Equals, Has

NS = "urn:alternation-gate"
TIER = QualifiedName(NS, "tokens")
LABEL = QualifiedName(NS, "label")


def q(local: str) -> QualifiedName:
    """Return one fixture name."""
    return QualifiedName(NS, local)


def graph(labels: tuple[str, ...]) -> Graph:
    """Build one labeled tier."""
    return Graph(
        (NamespaceDeclaration("g", NS),),
        (
            Tier(
                TierDeclaration(TIER, "Tokens"),
                tuple(
                    Item(
                        f"i{index}",
                        (AttributeValue(LABEL, XsdType.STRING, label),),
                    )
                    for index, label in enumerate(labels)
                ),
            ),
        ),
        (SimpleRelationDeclaration(q("members"), TIER, q("token")),),
        attribute_declarations=(
            AttributeDeclaration(LABEL, AttributeDomain.ITEM, XsdType.STRING),
        ),
    )


def atom(label: str) -> AtomPattern:
    """Match one fixture label."""
    return AtomPattern(Equals(Cell(LABEL), (Bare(label),)))


def present() -> AtomPattern:
    """Match every fixture item through a predicate overlapping label atoms."""
    return AtomPattern(Has(Cell(LABEL), alias="label"))


def _part(specification: tuple[int, int, int]) -> Pattern:
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
        return SeqPattern((StartPattern(), left))
    if shape == 4:
        return SeqPattern((left, EndPattern()))
    if shape == 5:
        return SeqPattern((RepeatPattern(left, 0, 1), right))
    if shape == 6:
        return SeqPattern((present(), right))
    nested = AltPattern((left, right))
    return SeqPattern((nested, atom("abc"[(second + 1) % 3])))


def _outcome(call: Callable[[], object]) -> tuple[object, ...]:
    try:
        return ("return", call())
    except Exception as error:
        return (
            "raise",
            type(error),
            getattr(error, "stage", None),
            str(error),
        )


def _views(
    compiled: match_module.CompiledPattern,
    subject: Graph,
    *,
    open_left: bool,
    focused: bool,
) -> dict[str, tuple[object, ...]]:
    raw_scopes, truth = compiled._prepare(
        subject, TierOrder(TIER), _PatternOperation.EXISTS
    )
    scopes = (_Scope(raw_scopes[0].nodes, open_left=open_left),)
    result = {
        "exists": _outcome(lambda: compiled._exists_over(scopes, truth)),
        "exists-open": _outcome(
            lambda: compiled._exists_over(scopes, truth, open_right=True)
        ),
        "spans": _outcome(lambda: compiled._spans_over(scopes, truth)),
        "spans-limit": _outcome(lambda: compiled._spans_over(scopes, truth, limit=2)),
        "spans-open": _outcome(
            lambda: compiled._spans_over(scopes, truth, open_right=True)
        ),
        "count": _outcome(lambda: compiled._count_over(scopes, truth)),
        "count-open": _outcome(
            lambda: compiled._count_over(scopes, truth, open_right=True)
        ),
    }
    if focused:
        result["focus"] = _outcome(lambda: compiled._focus_over(subject, scopes, truth))
        result["focus-open"] = _outcome(
            lambda: compiled._focus_over(subject, scopes, truth, open_right=True)
        )
    return result


def _with_pruning(enabled: bool, call: Callable[[], object]) -> object:
    previous = match_module._GATE_PRUNING
    match_module._GATE_PRUNING = enabled
    try:
        return call()
    finally:
        match_module._GATE_PRUNING = previous


def _compile_with_threshold(
    pattern: Pattern, threshold: int
) -> match_module.CompiledPattern:
    previous = match_module._GATE_MIN_PARTS
    match_module._GATE_MIN_PARTS = threshold
    try:
        return compile_pattern(pattern)
    finally:
        match_module._GATE_MIN_PARTS = previous


PARTS = st.lists(
    st.tuples(
        st.integers(min_value=0, max_value=7),
        st.integers(min_value=0, max_value=2),
        st.integers(min_value=0, max_value=2),
    ),
    min_size=2,
    max_size=7,
)


@settings(max_examples=60, deadline=None)
@given(
    specifications=PARTS,
    labels=st.lists(st.sampled_from(("a", "b", "c")), min_size=0, max_size=8).map(
        tuple
    ),
    open_left=st.booleans(),
)
def test_gate_property_preserves_every_span_view(
    specifications: list[tuple[int, int, int]],
    labels: tuple[str, ...],
    open_left: bool,
) -> None:
    """Random nested, nullable, anchored and repeated parts stay exact."""
    previous = match_module._GATE_MIN_PARTS
    match_module._GATE_MIN_PARTS = 2
    try:
        choice = AltPattern(tuple(_part(spec) for spec in specifications))
        patterns = (
            (SeqPattern((atom("a"), choice)), False),
            (SeqPattern((FocusPattern(atom("a")), choice)), True),
        )
        subject = graph(labels)
        for pattern, focused in patterns:
            compiled = compile_pattern(pattern)
            full = _with_pruning(
                False,
                partial(
                    _views,
                    compiled,
                    subject,
                    open_left=open_left,
                    focused=focused,
                ),
            )
            gated = _with_pruning(
                True,
                partial(
                    _views,
                    compiled,
                    subject,
                    open_left=open_left,
                    focused=focused,
                ),
            )
            assert gated == full
    finally:
        match_module._GATE_MIN_PARTS = previous


def test_a_deeper_first_mismatch_keeps_a_branch_that_already_exits() -> None:
    """Deeper FIRST mismatches cannot discard a branch that already exits."""
    ab = SeqPattern((atom("a"), atom("b")))
    short = SeqPattern((atom("a"), RepeatPattern(atom("b"), 0, 1)))
    compiled = compile_pattern(AltPattern((*((ab,) * 64), short)))
    assert compiled.count(graph(("a", "c")), TierOrder(TIER)) == 1


def test_first_accept_and_duplicate_collapse_shortcuts_are_killed() -> None:
    """Kill first-accept and duplicate-collapse shortcuts."""
    previous = match_module._GATE_MIN_PARTS
    match_module._GATE_MIN_PARTS = 2
    try:
        distinct_ends = compile_pattern(
            AltPattern((atom("a"), SeqPattern((present(), atom("b")))))
        )
        assert distinct_ends.count(graph(("a", "b")), TierOrder(TIER)) == 2

        duplicates = compile_pattern(AltPattern(tuple(atom("a") for _ in range(32))))
        assert duplicates.count(graph(("a",)), TierOrder(TIER)) == 1

        def token(value: str) -> AtomPattern:
            return AtomPattern(Equals(Current(), (Bare(value),)))

        lattice_pattern = compile_pattern(
            SeqPattern(
                (
                    token("ten"),
                    AltPattern(tuple(token("o") for _ in range(32))),
                    token("five"),
                )
            )
        )
        _base, emissions = lat_a()
        matched = match_lattice(emissions, lattice_pattern)
        assert matched.count(Determinize(64)) == 2
        with pytest.raises(Refusal, match="two accepting runs"):
            matched.count(Unambiguous())
    finally:
        match_module._GATE_MIN_PARTS = previous


def test_gate_is_private_and_lattice_observables_are_unchanged() -> None:
    """Only private span-engine metadata differs between the two builds."""

    def token(value: str) -> AtomPattern:
        return AtomPattern(Equals(Current(), (Bare(value),)))

    pattern = SeqPattern(
        (
            token("ten"),
            AltPattern(tuple(token("o") for _ in range(32))),
            token("five"),
        )
    )
    gated = _compile_with_threshold(pattern, 32)
    ungated = _compile_with_threshold(pattern, 33)

    assert gated._gates
    assert not ungated._gates
    assert (
        gated.epsilon,
        gated.atom_edges,
        gated.predicates,
        gated.start,
        gated.accept,
    ) == (
        ungated.epsilon,
        ungated.atom_edges,
        ungated.predicates,
        ungated.start,
        ungated.accept,
    )
    assert repr(gated) == repr(ungated)
    assert gated == ungated
    assert hash(gated) == hash(ungated)
    discovered = match_module.CompiledPattern(
        pattern,
        gated.start,
        gated.accept,
        gated.epsilon,
        gated.atom_edges,
        gated.predicates,
    )
    assert discovered._gates
    assert discovered == gated

    def lattice_observables(
        compiled: match_module.CompiledPattern,
    ) -> tuple[int, int]:
        _base, emissions = lat_a()
        meter = WorkMeter(WorkBudget(steps=10**9))
        runs = match_lattice(emissions, compiled).count(Determinize(64), budget=meter)
        return runs, meter.spent

    assert lattice_observables(gated) == lattice_observables(ungated)


def test_gate_threshold_is_exact_and_small_patterns_retain_no_table() -> None:
    """Thirty-one successors stay ordinary; the thirty-second builds a gate."""
    below = compile_pattern(AltPattern(tuple(atom(f"v{index}") for index in range(31))))
    boundary = compile_pattern(
        AltPattern(tuple(atom(f"v{index}") for index in range(32)))
    )
    assert below._gates is None
    assert isinstance(boundary._gates, Mapping)
    assert len(boundary._gates) == 1
    assert tuple(boundary._gates) == (boundary.start,)
    with pytest.raises(KeyError):
        boundary._gates[-1]


def test_gated_compiled_patterns_are_copyable_and_picklable() -> None:
    """Private gate metadata survives ordinary object round trips."""
    pickle_module = import_module("pickle")
    compiled = compile_pattern(
        AltPattern(tuple(atom(f"v{index}") for index in range(32)))
    )
    assert compiled._gates
    for cloned in (
        copy(compiled),
        deepcopy(compiled),
        pickle_module.loads(pickle_module.dumps(compiled)),
    ):
        assert cloned == compiled
        assert cloned._gates == compiled._gates
        assert repr(cloned) == repr(compiled)
        assert hash(cloned) == hash(compiled)


def test_gate_preserves_multiple_scopes_with_distinct_open_edges() -> None:
    """Gate decisions remain local to each scope and its open-edge flags."""
    compiled = compile_pattern(
        AltPattern(
            tuple(
                SeqPattern((atom(f"v{index}"), atom(f"w{index}")))
                for index in range(32)
            )
        )
    )
    subject = graph(("v0", "w0", "miss", "v31", "w31", "tail"))
    raw_scopes, truth = compiled._prepare(
        subject, TierOrder(TIER), _PatternOperation.EXISTS
    )
    nodes = raw_scopes[0].nodes
    scopes = (
        _Scope(nodes[:3]),
        _Scope(nodes[3:], open_left=True, open_right=True),
    )

    def observables() -> tuple[object, object, object]:
        return (
            compiled._exists_over(scopes, truth, open_right=True),
            compiled._count_over(scopes, truth, open_right=True),
            compiled._spans_over(scopes, truth, open_right=True),
        )

    assert _with_pruning(True, observables) == _with_pruning(False, observables)


def _spent(
    compiled: match_module.CompiledPattern, subject: Graph, enabled: bool
) -> tuple[int, int]:
    meter = WorkMeter(WorkBudget(steps=10**9))
    count = _with_pruning(
        enabled,
        lambda: compiled.count(subject, TierOrder(TIER), budget=meter),
    )
    assert isinstance(count, int)
    return count, meter.spent


def test_gate_fires_and_small_patterns_keep_the_same_steps() -> None:
    """A selective large split saves work; a sub-threshold pattern is inert."""
    labels = tuple(f"v{index}" for index in range(64))
    large = compile_pattern(AltPattern(tuple(atom(label) for label in labels)))
    subject = graph(("v0", "miss", "v63"))
    full = _spent(large, subject, False)
    gated = _spent(large, subject, True)
    assert gated[0] == full[0] == 2
    assert gated[1] < full[1]

    small = compile_pattern(AltPattern((atom("v0"), atom("v1"))))
    assert _spent(small, subject, True) == _spent(small, subject, False)


def _budget_result(
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

    return _with_pruning(enabled, run)  # type: ignore[return-value]


def test_budget_sweep_is_monotone_and_cut_prefixes_only_extend() -> None:
    """Every gated threshold is no worse than its full-run counterpart."""
    previous = match_module._GATE_MIN_PARTS
    match_module._GATE_MIN_PARTS = 2
    try:
        pattern = AltPattern(
            (
                atom("a"),
                SeqPattern((atom("a"), atom("b"))),
                SeqPattern((present(), atom("c"))),
                SeqPattern((atom("b"), atom("a"))),
            )
        )
        compiled = compile_pattern(pattern)
        subject = graph(("a", "b", "a", "c"))
        full_count, full_spent = _spent(compiled, subject, False)
        gated_count, gated_spent = _spent(compiled, subject, True)
        assert gated_count == full_count
        assert gated_spent <= full_spent

        span_meter = WorkMeter(WorkBudget(steps=10**9))
        full_spans = _with_pruning(
            False,
            lambda: compiled.spans(subject, TierOrder(TIER), budget=span_meter),
        )
        assert full_spans is not None
        improved_boundary = False
        for steps in range(1, span_meter.spent + 1):
            full = _budget_result(compiled, subject, False, steps)
            gated = _budget_result(compiled, subject, True, steps)
            if full is None:
                if gated is not None and gated[1] is Extent.CUT_AT_BUDGET:
                    improved_boundary = True
                continue
            full_matches, full_extent = full
            assert gated is not None
            gated_matches, gated_extent = gated
            assert gated_matches[: len(full_matches)] == full_matches
            if full_extent is Extent.EXHAUSTIVE:
                assert gated == full
            else:
                assert full_extent is Extent.CUT_AT_BUDGET
                assert gated_extent in (Extent.CUT_AT_BUDGET, Extent.EXHAUSTIVE)
        assert improved_boundary
    finally:
        match_module._GATE_MIN_PARTS = previous


def test_fragment_limit_falls_back_to_always_candidate() -> None:
    """Oversized zero-width and later closures weaken pruning safely."""
    previous_parts = match_module._GATE_MIN_PARTS
    previous_fragment = match_module._GATE_FRAGMENT_STATES
    match_module._GATE_MIN_PARTS = 2
    try:
        match_module._GATE_FRAGMENT_STATES = 1
        anchored = compile_pattern(
            AltPattern(
                (
                    SeqPattern((StartPattern(), atom("a"))),
                    SeqPattern((StartPattern(), atom("b"))),
                )
            )
        )
        assert anchored.exists(graph(("a",)), TierOrder(TIER))

        match_module._GATE_FRAGMENT_STATES = 2
        later = compile_pattern(
            AltPattern(
                (
                    SeqPattern((atom("a"), atom("b"))),
                    SeqPattern((atom("a"), atom("c"))),
                )
            )
        )
        subject = graph(("a", "b", "x"))
        assert _with_pruning(
            True, lambda: later.spans(subject, TierOrder(TIER))
        ) == _with_pruning(False, lambda: later.spans(subject, TierOrder(TIER)))

        assert {
            later.start,
            *(
                match_module._epsilon_target(edge)
                for edge in later.epsilon[later.start]
            ),
        } <= later._closure({later.start}, 0, 0)
    finally:
        match_module._GATE_MIN_PARTS = previous_parts
        match_module._GATE_FRAGMENT_STATES = previous_fragment


def _determinism_probe() -> tuple[int, int, list[tuple[int, int]]]:
    labels = tuple(f"d{index}" for index in range(40))
    compiled = compile_pattern(AltPattern(tuple(atom(label) for label in labels)))
    subject = graph(("d39", "x", "d0", "d20"))
    meter = WorkMeter(WorkBudget(steps=10**9))
    spans = compiled.spans(subject, TierOrder(TIER), budget=meter)
    return (
        len(spans.matches),
        meter.spent,
        [(span.start, span.end) for span in spans.matches],
    )


def test_gate_is_deterministic_across_hash_seeds() -> None:
    """Results and logical work do not inherit set hash iteration order."""
    script = (
        "import json; "
        "from tests.test_alt_gate import _determinism_probe; "
        "print(json.dumps(_determinism_probe()))"
    )
    outputs = []
    for seed in (0, 1, 8675309):
        environment = dict(os.environ, PYTHONHASHSEED=str(seed))
        outputs.append(
            subprocess.run(
                [sys.executable, "-c", script],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
                cwd=Path(__file__).resolve().parents[1],
            ).stdout.strip()
        )
    assert len(set(outputs)) == 1
    assert json.loads(outputs[0])[0] == 3


def test_a_gated_end_anchor_is_skipped_before_the_scope_end() -> None:
    """An end-anchored gated successor matches only at the end of the scope."""
    parts = tuple(SeqPattern((atom(f"w{index}"), EndPattern())) for index in range(40))
    compiled = compile_pattern(AltPattern(parts))
    assert compiled.count(graph(("w0", "w1")), TierOrder(TIER)) == 1
    assert compiled.count(graph(("w0", "zz")), TierOrder(TIER)) == 0
