"""Prepared regular-pattern matching over one immutable graph."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import FrozenInstanceError, replace
from itertools import permutations
from random import Random

import pytest

import tiergraph.match as match_module
from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    Graph,
    Item,
    ItemRef,
    ItemsSelector,
    NamespaceDeclaration,
    Node,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    QualifiedName,
    Refusal,
    RelationEndpointKind,
    RelationSideDeclaration,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    WhereSelector,
    XsdType,
)
from tiergraph.match import (
    AdjacentRuns,
    AltPattern,
    AtomPattern,
    BoundOrdering,
    BoundPattern,
    CompiledPattern,
    ContainerOrder,
    DeclaredOrder,
    EndPattern,
    FocusPattern,
    Ordering,
    RepeatPattern,
    SeqPattern,
    StartPattern,
    TierOrder,
    compile_pattern,
)
from tiergraph.predicate import Bare, BoundPredicate, Cell, Equals, OffsetProfile

NS = "urn:bound-pattern"


def q(local: str) -> QualifiedName:
    """Return one name in the fixture namespace."""
    return QualifiedName(NS, local)


def _side(tier: QualifiedName, maximum: int | None = None) -> RelationSideDeclaration:
    return RelationSideDeclaration(
        (RelationEndpointKind.ITEM,), tiers=(tier,), maximum=maximum
    )


def graph() -> Graph:
    """Return one graph exercising every public ordering form."""
    segments = tuple(
        Item(
            f"s{index}",
            (
                AttributeValue(q("class"), XsdType.STRING, value),
                AttributeValue(q("origin"), XsdType.INTEGER, str(origin)),
                AttributeValue(q("end"), XsdType.INTEGER, str(end)),
            ),
        )
        for index, (value, origin, end) in enumerate(
            (("a", 0, 1), ("b", 1, 2), ("a", 3, 4), ("b", 4, 5))
        )
    )
    return Graph(
        (NamespaceDeclaration("b", NS),),
        (
            Tier(TierDeclaration(q("seg"), "Segments"), segments),
            Tier(
                TierDeclaration(q("syllable"), "Syllables"),
                (Item("y0"), Item("y1")),
            ),
        ),
        (
            SimpleRelationDeclaration(q("seg-members"), q("seg"), q("seg-item")),
            SimpleRelationDeclaration(
                q("syllable-members"), q("syllable"), q("syllable-item")
            ),
            PolyadicRelationDeclaration(
                q("next"), _side(q("seg"), 1), _side(q("seg"), 1)
            ),
            PolyadicRelationDeclaration(
                q("parts"),
                _side(q("syllable"), 1),
                _side(q("seg")),
                unique_sources=True,
                acyclic=True,
            ),
        ),
        attribute_declarations=(
            AttributeDeclaration(q("class"), AttributeDomain.ITEM, XsdType.STRING),
            AttributeDeclaration(q("origin"), AttributeDomain.ITEM, XsdType.INTEGER),
            AttributeDeclaration(q("end"), AttributeDomain.ITEM, XsdType.INTEGER),
        ),
        polyadic_relations=(
            *(
                PolyadicRelationInstance(
                    q("next"),
                    (ItemRef(q("seg"), index),),
                    (ItemRef(q("seg"), index + 1),),
                )
                for index in range(3)
            ),
            PolyadicRelationInstance(
                q("parts"),
                (ItemRef(q("syllable"), 0),),
                (ItemRef(q("seg"), 0), ItemRef(q("seg"), 1)),
            ),
            PolyadicRelationInstance(
                q("parts"),
                (ItemRef(q("syllable"), 1),),
                (ItemRef(q("seg"), 2), ItemRef(q("seg"), 3)),
            ),
        ),
    )


def atom(value: str) -> AtomPattern:
    """Return one class-equality atom."""
    return AtomPattern(Equals(Cell(q("class")), (Bare(value),)))


def orderings() -> tuple[Ordering, ...]:
    """Return all four ordering variants over the shared graph."""
    return (
        TierOrder(q("seg")),
        ContainerOrder(q("parts"), ItemsSelector(q("syllable"))),
        DeclaredOrder(q("next"), ItemsSelector(q("seg"))),
        AdjacentRuns(ItemsSelector(q("seg")), OffsetProfile(q("origin"), end=q("end"))),
    )


def outcome(call: Callable[[], object]) -> tuple[object, ...]:
    """Return a value or an exception's complete public identity."""
    try:
        return ("return", call())
    except Exception as error:
        return ("raise", type(error), getattr(error, "stage", None), str(error))


def assert_views_equal(
    compiled: CompiledPattern, subject: Graph, ordering: Ordering
) -> None:
    """Compare every per-call view with both bound construction spellings."""
    raw = compiled.bind(subject, ordering)
    prepared = compiled.bind(subject, BoundOrdering(subject, ordering))
    closed_calls: tuple[
        tuple[Callable[[], object], Callable[[], object], Callable[[], object]], ...
    ] = (
        (lambda: compiled.exists(subject, ordering), raw.exists, prepared.exists),
        (lambda: compiled.focus(subject, ordering), raw.focus, prepared.focus),
        (lambda: compiled.count(subject, ordering), raw.count, prepared.count),
    )
    open_calls: tuple[
        tuple[Callable[[], object], Callable[[], object], Callable[[], object]], ...
    ] = (
        (
            lambda: compiled.exists(subject, ordering, open_right=True),
            lambda: raw.exists(open_right=True),
            lambda: prepared.exists(open_right=True),
        ),
        (
            lambda: compiled.focus(subject, ordering, open_right=True),
            lambda: raw.focus(open_right=True),
            lambda: prepared.focus(open_right=True),
        ),
        (
            lambda: compiled.count(subject, ordering, open_right=True),
            lambda: raw.count(open_right=True),
            lambda: prepared.count(open_right=True),
        ),
    )
    for calls in (closed_calls, open_calls):
        for per_call, direct, shared in calls:
            expected = outcome(per_call)
            assert outcome(direct) == expected
            assert outcome(shared) == expected

    def compare_spans(limit: int | None) -> None:
        span_calls: tuple[
            tuple[Callable[[], object], Callable[[], object], Callable[[], object]], ...
        ] = (
            (
                lambda: compiled.spans(subject, ordering, limit=limit),
                lambda: raw.spans(limit=limit),
                lambda: prepared.spans(limit=limit),
            ),
            (
                lambda: compiled.spans(subject, ordering, limit=limit, open_right=True),
                lambda: raw.spans(limit=limit, open_right=True),
                lambda: prepared.spans(limit=limit, open_right=True),
            ),
        )
        for per_call, direct, shared in span_calls:
            expected = outcome(per_call)
            assert outcome(direct) == expected
            assert outcome(shared) == expected

    for limit in (None, 0, 1, 3):
        compare_spans(limit)


def test_m1_bound_views_equal_per_call_views_for_every_ordering() -> None:
    """M1: scopes, truth, limits, watermarks, focus and counts stay equivalent."""
    subject = graph()
    a = atom("a")
    b = atom("b")
    patterns = (
        a,
        SeqPattern((a, b)),
        AltPattern((a, b)),
        RepeatPattern(a, 1, 2),
        FocusPattern(a),
        SeqPattern((FocusPattern(a), b)),
        StartPattern(),
        EndPattern(),
    )
    for ordering in orderings():
        for pattern in patterns:
            assert_views_equal(compile_pattern(pattern), subject, ordering)

    generated = Random(20261005)
    predicates = (atom("a"), atom("b"), atom("c"))
    projected = DeclaredOrder(
        q("next"), ItemsSelector(q("seg")), open_left=True
    ).project(
        WhereSelector(ItemsSelector(q("seg")), Equals(Cell(q("class")), (Bare("a"),)))
    )
    for _ in range(100):
        first, second, third = generated.sample(predicates, 3)
        choices = (
            first,
            SeqPattern((first, second, third)),
            AltPattern((first, second, third)),
            RepeatPattern(first, generated.randint(0, 1), generated.randint(1, 3)),
            FocusPattern(first),
            SeqPattern((FocusPattern(first), second)),
            SeqPattern((StartPattern(), first)),
            SeqPattern((first, EndPattern())),
        )
        pattern = generated.choice(choices)
        for ordering in (*orderings(), projected):
            assert_views_equal(compile_pattern(pattern), subject, ordering)


def test_m2_one_bound_pattern_has_no_view_call_state() -> None:
    """M2: view order, repetition, limit and open-right are call-local."""
    subject = graph()
    ordering = TierOrder(q("seg"))
    compiled = compile_pattern(SeqPattern((FocusPattern(atom("a")), atom("b"))))
    expected = {
        "exists": compiled.exists(subject, ordering, open_right=True),
        "focus": compiled.focus(subject, ordering),
        "spans": compiled.spans(subject, ordering, limit=1, open_right=True),
        "count": compiled.count(subject, ordering),
    }
    bound = compiled.bind(subject, ordering)
    calls: dict[str, Callable[[], object]] = {
        "exists": lambda: bound.exists(open_right=True),
        "focus": bound.focus,
        "spans": lambda: bound.spans(limit=1, open_right=True),
        "count": bound.count,
    }
    for sequence in permutations(calls):
        for name in (*sequence, *sequence):
            assert calls[name]() == expected[name]


def test_m3_one_ordering_serves_distinct_patterns() -> None:
    """M3: truth belongs to a pattern, not to its reusable ordering."""
    subject = graph()
    ordering = BoundOrdering(subject, TierOrder(q("seg")))
    first = compile_pattern(atom("a"))
    second = compile_pattern(atom("b"))
    assert first.bind(subject, ordering).spans() == first.spans(
        subject, ordering.ordering
    )
    assert second.bind(subject, ordering).spans() == second.spans(
        subject, ordering.ordering
    )


def test_m4_characterization_results_survive_binding() -> None:
    """M4: the four ordering forms retain their characterized spans and focus."""
    subject = graph()
    compiled = compile_pattern(SeqPattern((FocusPattern(atom("a")), atom("b"))))
    expected = {
        TierOrder: ((0, 0, 2), (0, 2, 4)),
        ContainerOrder: ((0, 0, 2), (1, 0, 2)),
        DeclaredOrder: ((0, 0, 2), (0, 2, 4)),
        AdjacentRuns: ((0, 0, 2), (1, 0, 2)),
    }
    for ordering in orderings():
        bound = compiled.bind(subject, ordering)
        assert (
            tuple(
                (match.scope, match.start, match.end) for match in bound.spans().matches
            )
            == expected[type(ordering)]
        )
        focused = bound.focus().nodes
        assert all(isinstance(node.reference, ItemRef) for node in focused)
        assert tuple(
            node.reference.index
            for node in focused
            if isinstance(node.reference, ItemRef)
        ) == (0, 2)


def test_m5_m6_bound_checks_are_per_view_and_repeatable() -> None:
    """M5/M6: every operation keeps its exact single-defect refusal."""
    subject = graph()
    ordering = TierOrder(q("seg"))
    focusless = compile_pattern(atom("a")).bind(subject, ordering)
    assert focusless.spans().matches
    for _ in range(2):
        with pytest.raises(Refusal, match="pattern has no focus"):
            focusless.focus()

    nullable = compile_pattern(RepeatPattern(atom("a"), 0, 1)).bind(subject, ordering)
    assert nullable.exists()
    for operation in (nullable.spans, nullable.count):
        with pytest.raises(Refusal, match="pattern can match the empty sequence"):
            operation()

    valid = compile_pattern(atom("a")).bind(subject, ordering)
    assert valid.spans().matches
    for limit in (-1, True, 1.0):
        with pytest.raises(ValueError, match="nonnegative integer"):
            valid.spans(limit=limit)  # type: ignore[arg-type]


def test_m7_raw_and_prebuilt_refusal_precedence_is_explicit() -> None:
    """M7: raw bind matches predicate order; prebuilt validation happens first."""
    subject = graph()
    missing = TierOrder(q("missing"))
    compiled = compile_pattern(atom("a"))
    with pytest.raises(Refusal, match="is undeclared"):
        compiled.bind(subject, missing)
    with pytest.raises(Refusal, match="pattern has no focus"):
        compiled.focus(subject, missing)
    with pytest.raises(Refusal, match="is undeclared"):
        compiled.bind(subject, BoundOrdering(subject, missing)).focus()

    unbound = compile_pattern(
        AtomPattern(Equals(Cell(q("missing-attribute")), (Bare("x"),)))
    )
    raw_calls: tuple[Callable[[], object], ...] = (
        lambda: unbound.bind(subject, missing),
        lambda: unbound.exists(subject, missing),
    )
    for call in raw_calls:
        with pytest.raises(Refusal, match="undeclared attribute"):
            call()
    with pytest.raises(Refusal, match="is undeclared"):
        unbound.bind(subject, BoundOrdering(subject, missing))


def test_m8_foreign_equal_graph_is_refused_by_both_construction_paths() -> None:
    """M8: graph object identity is the prepared ordering's validity witness."""
    first = graph()
    equal = replace(first)
    assert equal == first and equal is not first
    ordering = BoundOrdering(first, TierOrder(q("seg")))
    compiled = compile_pattern(atom("a"))
    calls: tuple[Callable[[], object], ...] = (
        lambda: compiled.bind(equal, ordering),
        lambda: BoundPattern(compiled, equal, ordering),
    )
    for call in calls:
        with pytest.raises(Refusal) as caught:
            call()
        assert str(caught.value) == (
            "pattern binding requires an ordering bound to the same graph"
        )


def test_m9_binding_reads_and_evaluates_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M9: bound views do not silently repeat scope reads or predicate holds."""
    subject = graph()
    ordering = TierOrder(q("seg"))
    reads = 0
    holds = 0
    original_read = match_module._read_scopes
    original_holds = BoundPredicate.holds

    def counted_read(
        graph: Graph, ordering: Ordering
    ) -> tuple[match_module._Scope, ...]:
        nonlocal reads
        reads += 1
        return original_read(graph, ordering)

    def counted_holds(predicate: BoundPredicate, node: Node) -> bool:
        nonlocal holds
        holds += 1
        return original_holds(predicate, node)

    monkeypatch.setattr(match_module, "_read_scopes", counted_read)
    monkeypatch.setattr(BoundPredicate, "holds", counted_holds)
    prepared = BoundOrdering(subject, ordering)
    assert reads == 1

    compiled = compile_pattern(SeqPattern((FocusPattern(atom("a")), atom("b"))))
    bound = compiled.bind(subject, prepared)
    assert (reads, holds) == (1, 8)
    bound.exists()
    bound.focus()
    bound.spans()
    bound.count()
    assert (reads, holds) == (1, 8)

    for value in (atom("a"), atom("b")):
        compile_pattern(value).bind(subject, prepared)
    assert (reads, holds) == (1, 16)

    compiled.exists(subject, ordering)
    compiled.focus(subject, ordering)
    compiled.spans(subject, ordering)
    compiled.count(subject, ordering)
    assert reads == 5


def test_m10_public_shapes_and_frozen_handle_semantics() -> None:
    """M10: public shapes stay narrow and prepared state is deeply immutable."""
    assert tuple(inspect.signature(BoundOrdering).parameters) == ("graph", "ordering")
    assert tuple(inspect.signature(BoundPattern).parameters) == (
        "compiled",
        "graph",
        "ordering",
    )
    assert "BoundOrdering" in match_module.__all__
    assert "BoundPattern" in match_module.__all__

    subject = graph()
    order = BoundOrdering(subject, TierOrder(q("seg")))
    bound = compile_pattern(atom("a")).bind(subject, order)
    assert bound.graph is subject and bound.ordering is order
    assert bound != compile_pattern(atom("a")).bind(subject, order)
    with pytest.raises(FrozenInstanceError):
        bound.graph = replace(subject)  # type: ignore[misc]
    row = bound._truth[0]
    node = next(iter(row))
    with pytest.raises(TypeError):
        row[node] = not row[node]  # type: ignore[index]


def test_subclass_scopes_override_remains_on_the_per_call_path() -> None:
    """Raw bind honors an override; a shared ordering keeps default scopes."""

    class EmptyScopesPattern(CompiledPattern):
        def _scopes(
            self, _graph: Graph, _ordering: match_module.Ordering
        ) -> tuple[match_module._Scope, ...]:
            return ()

    subject = graph()
    ordinary = compile_pattern(atom("a"))
    overridden = EmptyScopesPattern(
        ordinary.pattern,
        ordinary.start,
        ordinary.accept,
        ordinary.epsilon,
        ordinary.atom_edges,
        ordinary.predicates,
    )
    ordering = TierOrder(q("seg"))
    assert overridden.exists(subject, ordering) is False
    assert overridden.bind(subject, ordering).exists() is False
    prepared = BoundOrdering(subject, ordering)
    assert overridden.bind(subject, prepared).exists() is True


def test_wrong_typed_ordering_has_a_typed_refusal() -> None:
    """Both ordering preparation spellings refuse instead of leaking an attribute."""
    subject = graph()
    compiled = compile_pattern(atom("a"))
    calls: tuple[Callable[[], object], ...] = (
        lambda: BoundOrdering(subject, "not-an-ordering"),  # type: ignore[arg-type]
        lambda: compiled.bind(subject, "not-an-ordering"),  # type: ignore[arg-type]
    )
    for call in calls:
        with pytest.raises(Refusal) as caught:
            call()
        assert str(caught.value) == (
            "pattern ordering must be TierOrder, ContainerOrder, AdjacentRuns, "
            "or DeclaredOrder"
        )
