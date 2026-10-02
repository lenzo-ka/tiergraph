"""Regular sequence matching and interval joins over graph selections."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from typing import cast

from tiergraph.core import (
    Graph,
    ItemRef,
    JsonValue,
    QualifiedName,
    Refusal,
    RefusalStage,
)
from tiergraph.machine import MAX_REPEAT_COUNT, _decode_qname
from tiergraph.predicate import (
    IntervalRelation,
    OffsetProfile,
    Predicate,
    _decode_offset_profile,
    _decode_predicate,
    _interval_pairs,
    _offset_profile_to_data,
    _offset_spans,
    _validate_offset_profile,
    compile_predicate,
    predicate_to_data,
)
from tiergraph.schema import _refuse_field_set
from tiergraph.selection import (
    Node,
    NodeKind,
    NodeSet,
    Selector,
    _decode_selector,
    evaluate_selection,
)
from tiergraph.traversal import OrderedContainment
from tiergraph.wire import _object, _parsed_json, _string

MAX_PATTERN_POSITIONS = 100_000
_MAX_PATTERN_NODES = 10_000
_MIN_PARTS = 2
_GUARD_ALWAYS = 0
_GUARD_START = 1
_GUARD_END = 2


@dataclass(frozen=True, slots=True)
class AtomPattern:
    """Consume one item when a value predicate holds on it."""

    predicate: Predicate


@dataclass(frozen=True, slots=True)
class SeqPattern:
    """Match every part in order."""

    parts: tuple[Pattern, ...]

    def __post_init__(self) -> None:
        if len(self.parts) < _MIN_PARTS:
            raise ValueError(
                f"SeqPattern needs at least two parts, got {len(self.parts)}"
            )
        if any(isinstance(part, SeqPattern) for part in self.parts):
            raise ValueError(
                "SeqPattern may not directly contain a SeqPattern; "
                "flatten its parts into this one"
            )


@dataclass(frozen=True, slots=True)
class AltPattern:
    """Match any one part, without preserving run multiplicity."""

    parts: tuple[Pattern, ...]

    def __post_init__(self) -> None:
        if len(self.parts) < _MIN_PARTS:
            raise ValueError(
                f"AltPattern needs at least two parts, got {len(self.parts)}"
            )
        if any(isinstance(part, AltPattern) for part in self.parts):
            raise ValueError(
                "AltPattern may not directly contain an AltPattern; "
                "flatten its parts into this one"
            )


@dataclass(frozen=True, slots=True)
class RepeatPattern:
    """Repeat one pattern between inclusive minimum and maximum counts."""

    body: Pattern
    min: int
    max: int | None

    def __post_init__(self) -> None:
        if type(self.min) is not int or self.min < 0:
            raise ValueError("RepeatPattern minimum must be a nonnegative integer")
        if self.max is not None and (type(self.max) is not int or self.max < 0):
            raise ValueError(
                "RepeatPattern maximum must be a nonnegative integer or None"
            )
        if self.max == 0:
            raise ValueError("RepeatPattern maximum 0 matches only the empty sequence")
        if self.max is not None and self.min > self.max:
            raise ValueError(
                f"RepeatPattern minimum {self.min} is greater than maximum {self.max}"
            )
        largest = self.min if self.max is None else max(self.min, self.max)
        if largest > MAX_REPEAT_COUNT:
            raise ValueError(f"repeat count {largest} exceeds limit {MAX_REPEAT_COUNT}")
        if _contains_anchor(self.body):
            raise ValueError(
                "RepeatPattern body contains an anchor; an anchor matches a position "
                "and cannot repeat"
            )


@dataclass(frozen=True, slots=True)
class FocusPattern:
    """Mark consumed items that a sequence selector returns."""

    body: Pattern

    def __post_init__(self) -> None:
        if _nullable(self.body):
            raise ValueError(
                "FocusPattern body can match no item; a focus must consume at least one item"
            )
        if _contains_anchor(self.body):
            raise ValueError(
                "FocusPattern body contains an anchor; write anchors in the context"
            )
        if _focus_count(self.body):
            raise ValueError("FocusPattern body contains a nested focus")


@dataclass(frozen=True, slots=True)
class StartPattern:
    """Match the position at the start of one ordering scope."""


@dataclass(frozen=True, slots=True)
class EndPattern:
    """Match the position at the end of one ordering scope."""


type Pattern = (
    AtomPattern
    | SeqPattern
    | AltPattern
    | RepeatPattern
    | FocusPattern
    | StartPattern
    | EndPattern
)


@dataclass(frozen=True, slots=True)
class TierOrder:
    """Read one tier as one scope in declared item order."""

    tier: QualifiedName


@dataclass(frozen=True, slots=True)
class ContainerOrder:
    """Read each selected container's direct children as a separate scope."""

    relation: QualifiedName
    containers: Selector


@dataclass(frozen=True, slots=True)
class AdjacentRuns:
    """Split selected offset items where consecutive half-open spans do not meet."""

    source: Selector
    offsets: OffsetProfile


type Ordering = TierOrder | ContainerOrder | AdjacentRuns


class _PatternOperation(StrEnum):
    EXISTS = "exists"
    FOCUS = "focus"
    SPANS = "spans"
    COUNT = "count"


def _children(pattern: Pattern) -> tuple[Pattern, ...]:
    if isinstance(pattern, (SeqPattern, AltPattern)):
        return pattern.parts
    if isinstance(pattern, (RepeatPattern, FocusPattern)):
        return (pattern.body,)
    return ()


def _contains_anchor(pattern: Pattern) -> bool:
    return isinstance(pattern, (StartPattern, EndPattern)) or any(
        _contains_anchor(child) for child in _children(pattern)
    )


def _focus_count(pattern: Pattern) -> int:
    return int(isinstance(pattern, FocusPattern)) + sum(
        _focus_count(child) for child in _children(pattern)
    )


def _nullable(pattern: Pattern) -> bool:
    if isinstance(pattern, AtomPattern):
        return False
    if isinstance(pattern, (StartPattern, EndPattern)):
        return True
    if isinstance(pattern, SeqPattern):
        return all(_nullable(part) for part in pattern.parts)
    if isinstance(pattern, AltPattern):
        return any(_nullable(part) for part in pattern.parts)
    if isinstance(pattern, RepeatPattern):
        return pattern.min == 0 or _nullable(pattern.body)
    return _nullable(pattern.body)


def _position_count(pattern: Pattern) -> int:
    if isinstance(pattern, AtomPattern):
        return 1
    if isinstance(pattern, (StartPattern, EndPattern)):
        return 0
    if isinstance(pattern, (SeqPattern, AltPattern)):
        return sum(_position_count(part) for part in pattern.parts)
    if isinstance(pattern, FocusPattern):
        return _position_count(pattern.body)
    multiplier = pattern.min + 1 if pattern.max is None else pattern.max
    return _position_count(pattern.body) * multiplier


def _pattern_node_count(pattern: Pattern) -> int:
    count = 0
    pending = [pattern]
    while pending:
        current = pending.pop()
        count += 1
        if count > _MAX_PATTERN_NODES:
            break
        pending.extend(_children(current))
    return count


@dataclass(frozen=True, slots=True)
class _Epsilon:
    target: int
    guard: int = _GUARD_ALWAYS


@dataclass(frozen=True, slots=True)
class _AtomEdge:
    target: int
    atom: int
    focus: bool


class _NfaBuilder:
    def __init__(self) -> None:
        self.epsilon: list[list[_Epsilon]] = []
        self.atoms: list[list[_AtomEdge]] = []
        self.predicates: list[Predicate] = []
        self._predicate_indices: dict[Predicate, int] = {}

    def state(self) -> int:
        """Allocate and return one empty NFA state."""
        result = len(self.epsilon)
        self.epsilon.append([])
        self.atoms.append([])
        return result

    def atom_index(self, predicate: Predicate) -> int:
        """Return the deduplicated atom-table index for *predicate*."""
        found = self._predicate_indices.get(predicate)
        if found is not None:
            return found
        result = len(self.predicates)
        self.predicates.append(predicate)
        self._predicate_indices[predicate] = result
        return result

    def build(self, pattern: Pattern, *, focused: bool = False) -> tuple[int, int]:
        """Build *pattern* and return its entry and exit states."""
        start = self.state()
        end = self.state()
        if isinstance(pattern, AtomPattern):
            self.atoms[start].append(
                _AtomEdge(end, self.atom_index(pattern.predicate), focused)
            )
        elif isinstance(pattern, StartPattern):
            self.epsilon[start].append(_Epsilon(end, _GUARD_START))
        elif isinstance(pattern, EndPattern):
            self.epsilon[start].append(_Epsilon(end, _GUARD_END))
        elif isinstance(pattern, FocusPattern):
            body_start, body_end = self.build(pattern.body, focused=True)
            self.epsilon[start].append(_Epsilon(body_start))
            self.epsilon[body_end].append(_Epsilon(end))
        elif isinstance(pattern, SeqPattern):
            cursor = start
            for part in pattern.parts:
                part_start, part_end = self.build(part, focused=focused)
                self.epsilon[cursor].append(_Epsilon(part_start))
                cursor = part_end
            self.epsilon[cursor].append(_Epsilon(end))
        elif isinstance(pattern, AltPattern):
            for part in pattern.parts:
                part_start, part_end = self.build(part, focused=focused)
                self.epsilon[start].append(_Epsilon(part_start))
                self.epsilon[part_end].append(_Epsilon(end))
        else:
            cursor = start
            for _ in range(pattern.min):
                body_start, body_end = self.build(pattern.body, focused=focused)
                self.epsilon[cursor].append(_Epsilon(body_start))
                cursor = body_end
            if pattern.max is None:
                self.epsilon[cursor].append(_Epsilon(end))
                body_start, body_end = self.build(pattern.body, focused=focused)
                self.epsilon[cursor].append(_Epsilon(body_start))
                self.epsilon[body_end].append(_Epsilon(cursor))
            else:
                for _ in range(pattern.max - pattern.min):
                    body_start, body_end = self.build(pattern.body, focused=focused)
                    self.epsilon[cursor].append(_Epsilon(body_start))
                    self.epsilon[cursor].append(_Epsilon(body_end))
                    cursor = body_end
                self.epsilon[cursor].append(_Epsilon(end))
        return start, end


@dataclass(frozen=True, slots=True)
class _Scope:
    nodes: tuple[Node, ...]
    offsets: tuple[tuple[int, int], ...] | None = None


@dataclass(frozen=True, slots=True)
class SpanMatch:
    """Carry one distinct matching scope span and optional physical offsets."""

    scope: int
    start: int
    end: int
    items: tuple[Node, ...]
    offsets: tuple[int, int] | None

    def to_data(self) -> dict[str, JsonValue]:
        """Return one match as strict JSON data."""
        return {
            "scope": self.scope,
            "start": self.start,
            "end": self.end,
            "items": [item.to_data() for item in self.items],
            "offsets": None if self.offsets is None else list(self.offsets),
        }


@dataclass(frozen=True, slots=True)
class SpanMatches:
    """Carry distinct matching spans and whether their witness list was cut."""

    matches: tuple[SpanMatch, ...]
    extent: Extent

    def to_data(self) -> dict[str, JsonValue]:
        """Return matches and extent as strict JSON data."""
        return {
            "matches": [match.to_data() for match in self.matches],
            "extent": self.extent.value,
        }


@dataclass(frozen=True, slots=True)
class CompiledPattern:
    """Hold one Thompson epsilon-NFA and its deduplicated atom table."""

    pattern: Pattern
    start: int
    accept: int
    epsilon: tuple[tuple[_Epsilon, ...], ...]
    atom_edges: tuple[tuple[_AtomEdge, ...], ...]
    predicates: tuple[Predicate, ...]

    def _closure(self, states: set[int], position: int, length: int) -> set[int]:
        result = set(states)
        pending = list(states)
        while pending:
            state = pending.pop()
            for edge in self.epsilon[state]:
                if edge.guard == _GUARD_START and position != 0:
                    continue
                if edge.guard == _GUARD_END and position != length:
                    continue
                if edge.target not in result:
                    result.add(edge.target)
                    pending.append(edge.target)
        return result

    def _reverse_epsilon(self) -> tuple[tuple[tuple[int, int], ...], ...]:
        reverse: list[list[tuple[int, int]]] = [[] for _ in self.epsilon]
        for source, edges in enumerate(self.epsilon):
            for edge in edges:
                reverse[edge.target].append((source, edge.guard))
        return tuple(tuple(edges) for edges in reverse)

    @staticmethod
    def _guarded(guard: int, position: int, length: int) -> bool:
        return (
            guard == _GUARD_ALWAYS
            or (guard == _GUARD_START and position == 0)
            or (guard == _GUARD_END and position == length)
        )

    def _reverse_closure(
        self,
        states: set[int],
        position: int,
        length: int,
        reverse: tuple[tuple[tuple[int, int], ...], ...],
    ) -> set[int]:
        result = set(states)
        pending = list(states)
        while pending:
            state = pending.pop()
            for source, guard in reverse[state]:
                if self._guarded(guard, position, length) and source not in result:
                    result.add(source)
                    pending.append(source)
        return result

    def _scopes(self, graph: Graph, ordering: Ordering) -> tuple[_Scope, ...]:
        if isinstance(ordering, TierOrder):
            selected = evaluate_selection(graph, _items_selector(ordering.tier))
            return (_Scope(selected.nodes),)
        if isinstance(ordering, ContainerOrder):
            containment = OrderedContainment(graph, ordering.relation)
            containers = evaluate_selection(graph, ordering.containers)
            result: list[_Scope] = []
            for container in containers.nodes:
                if container.kind is not NodeKind.ITEM or not isinstance(
                    container.reference, ItemRef
                ):
                    raise Refusal(
                        RefusalStage.SEMANTICS,
                        "ContainerOrder containers must select items",
                    )
                result.append(
                    _Scope(containment.direct_children(container.reference).nodes)
                )
            return tuple(result)
        _validate_offset_profile(graph, ordering.offsets)
        selected = evaluate_selection(graph, ordering.source)
        spans = _offset_spans(graph, selected.nodes, ordering.offsets)
        if not spans:
            return ()
        runs: list[_Scope] = []
        nodes: list[Node] = []
        offsets: list[tuple[int, int]] = []
        previous = None
        for span in spans:
            if previous is None or (
                span.partition == previous.partition and span.origin == previous.end
            ):
                nodes.append(cast(Node, span.node))
                offsets.append((span.origin, span.end))
            else:
                runs.append(_Scope(tuple(nodes), tuple(offsets)))
                nodes = [cast(Node, span.node)]
                offsets = [(span.origin, span.end)]
            previous = span
        runs.append(_Scope(tuple(nodes), tuple(offsets)))
        return tuple(runs)

    def _prepare(
        self,
        graph: Graph,
        ordering: Ordering,
        operation: _PatternOperation,
        *,
        limit: int | None = None,
    ) -> tuple[tuple[_Scope, ...], tuple[dict[Node, bool], ...]]:
        """Bind atoms, validate the operation, then read and evaluate scopes."""
        bound = tuple(
            compile_predicate(predicate).bind(graph) for predicate in self.predicates
        )
        if operation is _PatternOperation.FOCUS and _focus_count(self.pattern) == 0:
            raise Refusal(
                RefusalStage.SEMANTICS,
                "pattern has no focus; mark one with FocusPattern or write T / L _ R",
            )
        if operation in (
            _PatternOperation.SPANS,
            _PatternOperation.COUNT,
        ) and _nullable(self.pattern):
            raise Refusal(
                RefusalStage.SEMANTICS,
                "pattern can match the empty sequence; spans and count need a pattern "
                "that consumes at least one item",
            )
        if operation is _PatternOperation.SPANS and (
            limit is not None and (type(limit) is not int or limit < 0)
        ):
            raise ValueError("pattern span limit must be a nonnegative integer or None")
        scopes = self._scopes(graph, ordering)
        nodes: list[Node] = []
        seen: set[Node] = set()
        for scope in scopes:
            for node in scope.nodes:
                if node not in seen:
                    seen.add(node)
                    nodes.append(node)
        return scopes, tuple(
            {node: predicate.holds(node) for node in nodes} for predicate in bound
        )

    def _step(
        self,
        active: set[int],
        node: Node,
        truth: tuple[dict[Node, bool], ...],
        position: int,
        length: int,
    ) -> set[int]:
        targets = {
            edge.target
            for state in active
            for edge in self.atom_edges[state]
            if truth[edge.atom][node]
        }
        return self._closure(targets, position + 1, length)

    def exists(self, graph: Graph, ordering: Ordering) -> bool:
        """Return whether any scope contains an accepting span."""
        scopes, truth = self._prepare(graph, ordering, _PatternOperation.EXISTS)
        for scope in scopes:
            length = len(scope.nodes)
            active: set[int] = set()
            for position in range(length + 1):
                active = self._closure(active | {self.start}, position, length)
                if self.accept in active:
                    return True
                if position < length:
                    active = self._step(
                        active, scope.nodes[position], truth, position, length
                    )
        return False

    def focus(self, graph: Graph, ordering: Ordering) -> NodeSet:
        """Return every item consumed by a focus edge on an accepting run."""
        scopes, truth = self._prepare(graph, ordering, _PatternOperation.FOCUS)
        selected: list[Node] = []
        reverse = self._reverse_epsilon()
        for scope in scopes:
            length = len(scope.nodes)
            forward: list[set[int]] = []
            active: set[int] = set()
            for position in range(length + 1):
                active = self._closure(active | {self.start}, position, length)
                forward.append(active)
                if position < length:
                    active = self._step(
                        active, scope.nodes[position], truth, position, length
                    )
            backward: list[set[int]] = [set() for _ in range(length + 1)]
            backward[length] = self._reverse_closure(
                {self.accept}, length, length, reverse
            )
            for position in range(length - 1, -1, -1):
                node = scope.nodes[position]
                predecessors = {self.accept}
                for state, edges in enumerate(self.atom_edges):
                    if any(
                        edge.target in backward[position + 1] and truth[edge.atom][node]
                        for edge in edges
                    ):
                        predecessors.add(state)
                backward[position] = self._reverse_closure(
                    predecessors, position, length, reverse
                )
            for position, node in enumerate(scope.nodes):
                if any(
                    edge.focus
                    and truth[edge.atom][node]
                    and edge.target in backward[position + 1]
                    for state in forward[position]
                    for edge in self.atom_edges[state]
                ):
                    selected.append(node)
        return NodeSet(graph, tuple(selected))

    def _span_matches(
        self,
        scope_index: int,
        scope: _Scope,
        truth: tuple[dict[Node, bool], ...],
    ) -> tuple[SpanMatch, ...]:
        length = len(scope.nodes)
        result: list[SpanMatch] = []
        for start in range(length + 1):
            active = self._closure({self.start}, start, length)
            for end in range(start, length):
                active = self._step(active, scope.nodes[end], truth, end, length)
                if not active:
                    break
                if self.accept in active:
                    result.append(self._span(scope_index, scope, start, end + 1))
        return tuple(result)

    @staticmethod
    def _span(scope_index: int, scope: _Scope, start: int, end: int) -> SpanMatch:
        offsets = (
            None
            if scope.offsets is None
            else (scope.offsets[start][0], scope.offsets[end - 1][1])
        )
        return SpanMatch(scope_index, start, end, scope.nodes[start:end], offsets)

    def spans(
        self,
        graph: Graph,
        ordering: Ordering,
        *,
        limit: int | None = None,
    ) -> SpanMatches:
        """Return each distinct accepting span once in scope-major order."""
        scopes, truth = self._prepare(
            graph, ordering, _PatternOperation.SPANS, limit=limit
        )
        result: list[SpanMatch] = []
        for scope_index, scope in enumerate(scopes):
            for match in self._span_matches(scope_index, scope, truth):
                if limit is not None and len(result) == limit:
                    return SpanMatches(tuple(result), Extent.CUT_AT_BOUND)
                result.append(match)
        return SpanMatches(tuple(result), Extent.EXHAUSTIVE)

    def count(self, graph: Graph, ordering: Ordering) -> int:
        """Count distinct accepting scope spans, never NFA runs."""
        scopes, truth = self._prepare(graph, ordering, _PatternOperation.COUNT)
        return sum(
            len(self._span_matches(index, scope, truth))
            for index, scope in enumerate(scopes)
        )


def _items_selector(tier: QualifiedName) -> Selector:
    from tiergraph.selection import ItemsSelector  # noqa: PLC0415 -- cycle breaker

    return ItemsSelector(tier)


def compile_pattern(pattern: Pattern) -> CompiledPattern:
    """Validate and compile one pattern to a Thompson epsilon-NFA."""
    nodes = _pattern_node_count(pattern)
    if nodes > _MAX_PATTERN_NODES:
        raise Refusal(
            RefusalStage.SEMANTICS,
            f"pattern has more than {_MAX_PATTERN_NODES} AST nodes",
        )
    focuses = _focus_count(pattern)
    if focuses > 1:
        raise Refusal(
            RefusalStage.SEMANTICS,
            f"pattern has {focuses} focus marks; at most one is allowed",
        )
    if focuses == 1 and not (
        isinstance(pattern, FocusPattern)
        or (
            isinstance(pattern, SeqPattern)
            and any(isinstance(part, FocusPattern) for part in pattern.parts)
        )
    ):
        raise Refusal(
            RefusalStage.SEMANTICS,
            "a FocusPattern must be the whole pattern or a part of its top-level "
            "SeqPattern",
        )
    positions = _position_count(pattern)
    if positions > MAX_PATTERN_POSITIONS:
        raise Refusal(
            RefusalStage.SEMANTICS,
            f"pattern unrolls to {positions} item positions; limit "
            f"{MAX_PATTERN_POSITIONS}",
        )
    builder = _NfaBuilder()
    start, accept = builder.build(pattern)
    return CompiledPattern(
        pattern,
        start,
        accept,
        tuple(tuple(edges) for edges in builder.epsilon),
        tuple(tuple(edges) for edges in builder.atoms),
        tuple(builder.predicates),
    )


class Extent(StrEnum):
    """State whether an output witness list was truncated."""

    EXHAUSTIVE = "exhaustive"
    CUT_AT_BOUND = "cut-at-bound"


@dataclass(frozen=True, slots=True)
class SpanPairs:
    """Carry interval-related node pairs in declared order and their extent."""

    pairs: tuple[tuple[Node, Node], ...]
    extent: Extent

    def to_data(self) -> dict[str, JsonValue]:
        """Return pairs and extent as strict JSON data."""
        return {
            "pairs": [[left.to_data(), right.to_data()] for left, right in self.pairs],
            "extent": self.extent.value,
        }


def span_pairs(
    graph: Graph,
    left: Selector,
    right: Selector,
    relation: IntervalRelation,
    offsets: OffsetProfile,
    *,
    limit: int | None = None,
) -> SpanPairs:
    """Return related item pairs in left-major declared order."""
    if limit is not None and (type(limit) is not int or limit < 0):
        raise ValueError("span_pairs limit must be a nonnegative integer or None")
    _validate_offset_profile(graph, offsets)
    left_nodes = evaluate_selection(graph, left)
    right_nodes = evaluate_selection(graph, right)
    left_spans = _offset_spans(graph, left_nodes.nodes, offsets)
    right_spans = _offset_spans(graph, right_nodes.nodes, offsets)
    pairs: list[tuple[Node, Node]] = []
    for left_span, right_span in _interval_pairs(left_spans, right_spans, relation):
        if limit is not None and len(pairs) == limit:
            return SpanPairs(tuple(pairs), Extent.CUT_AT_BOUND)
        pairs.append((cast(Node, left_span.node), cast(Node, right_span.node)))
    return SpanPairs(tuple(pairs), Extent.EXHAUSTIVE)


def pattern_to_data(pattern: Pattern) -> JsonValue:
    """Return one pattern as strict tagged JSON data."""
    if isinstance(pattern, AtomPattern):
        return {"pattern": "atom", "predicate": predicate_to_data(pattern.predicate)}
    if isinstance(pattern, SeqPattern):
        return {
            "pattern": "seq",
            "parts": [pattern_to_data(part) for part in pattern.parts],
        }
    if isinstance(pattern, AltPattern):
        return {
            "pattern": "alt",
            "parts": [pattern_to_data(part) for part in pattern.parts],
        }
    if isinstance(pattern, RepeatPattern):
        result: dict[str, JsonValue] = {
            "pattern": "repeat",
            "body": pattern_to_data(pattern.body),
            "min": pattern.min,
        }
        if pattern.max is not None:
            result["max"] = pattern.max
        return result
    if isinstance(pattern, FocusPattern):
        return {"pattern": "focus", "body": pattern_to_data(pattern.body)}
    return {"pattern": "start" if isinstance(pattern, StartPattern) else "end"}


def pattern_loads(source: str | bytes) -> Pattern:
    """Decode one strict declarative pattern from JSON."""
    return _decode_pattern(cast(JsonValue, _parsed_json(source)), "$")


def _decode_pattern(value: JsonValue, path: str) -> Pattern:
    node = cast(dict[str, JsonValue], _object(value, path))
    if "pattern" not in node:
        _refuse_field_set(node.keys(), {"pattern"}, {"pattern"}, path)
    kind = _string(node["pattern"], f"{path}.pattern")
    if kind == "atom":
        _refuse_field_set(
            node.keys(), {"pattern", "predicate"}, {"pattern", "predicate"}, path
        )
        return AtomPattern(_decode_predicate(node["predicate"], f"{path}.predicate"))
    if kind in {"seq", "alt"}:
        _refuse_field_set(node.keys(), {"pattern", "parts"}, {"pattern", "parts"}, path)
        raw_parts = node["parts"]
        if not isinstance(raw_parts, list):
            raise Refusal(RefusalStage.SHAPE, f"{path}.parts must be a list")
        parts = tuple(
            _decode_pattern(part, f"{path}.parts[{index}]")
            for index, part in enumerate(raw_parts)
        )
        return SeqPattern(parts) if kind == "seq" else AltPattern(parts)
    if kind == "repeat":
        _refuse_field_set(
            node.keys(),
            {"pattern", "body", "min", "max"},
            {"pattern", "body", "min"},
            path,
        )
        minimum = _nonnegative_integer(node["min"], f"{path}.min")
        maximum = (
            None
            if "max" not in node
            else _nonnegative_integer(node["max"], f"{path}.max")
        )
        return RepeatPattern(
            _decode_pattern(node["body"], f"{path}.body"), minimum, maximum
        )
    if kind == "focus":
        _refuse_field_set(node.keys(), {"pattern", "body"}, {"pattern", "body"}, path)
        return FocusPattern(_decode_pattern(node["body"], f"{path}.body"))
    if kind in {"start", "end"}:
        _refuse_field_set(node.keys(), {"pattern"}, {"pattern"}, path)
        return StartPattern() if kind == "start" else EndPattern()
    raise Refusal(
        RefusalStage.DISCRIMINATOR,
        f"{path}.pattern has unknown pattern {kind!r}",
    )


def _nonnegative_integer(value: JsonValue, path: str) -> int:
    if type(value) is not int or value < 0:
        raise Refusal(RefusalStage.VALUE, f"{path} must be a nonnegative integer")
    return value


def ordering_to_data(ordering: Ordering) -> JsonValue:
    """Return one declared chain ordering as strict JSON data."""
    if isinstance(ordering, TierOrder):
        return {"order": "tier", "tier": ordering.tier.to_data()}
    if isinstance(ordering, ContainerOrder):
        return {
            "order": "containers",
            "relation": ordering.relation.to_data(),
            "containers": _selector_to_data(ordering.containers),
        }
    return {
        "order": "adjacent-runs",
        "source": _selector_to_data(ordering.source),
        "offsets": _offset_profile_to_data(ordering.offsets),
    }


def _decode_ordering(value: JsonValue, path: str) -> Ordering:
    node = cast(dict[str, JsonValue], _object(value, path))
    if "order" not in node:
        _refuse_field_set(node.keys(), {"order"}, {"order"}, path)
    kind = _string(node["order"], f"{path}.order")
    if kind == "tier":
        _refuse_field_set(node.keys(), {"order", "tier"}, {"order", "tier"}, path)
        return TierOrder(_decode_qname(node["tier"], f"{path}.tier"))
    if kind == "containers":
        _refuse_field_set(
            node.keys(),
            {"order", "relation", "containers"},
            {"order", "relation", "containers"},
            path,
        )
        return ContainerOrder(
            _decode_qname(node["relation"], f"{path}.relation"),
            _decode_selector(node["containers"], f"{path}.containers"),
        )
    if kind == "adjacent-runs":
        _refuse_field_set(
            node.keys(),
            {"order", "source", "offsets"},
            {"order", "source", "offsets"},
            path,
        )
        return AdjacentRuns(
            _decode_selector(node["source"], f"{path}.source"),
            _decode_offset_profile(node["offsets"], f"{path}.offsets"),
        )
    raise Refusal(
        RefusalStage.DISCRIMINATOR,
        f"{path}.order has unknown ordering {kind!r}",
    )


def _selector_to_data(selector: Selector) -> JsonValue:
    """Encode the selector leaves and algebra admitted inside an ordering."""
    from tiergraph.selection import (  # noqa: PLC0415 -- cycle breaker
        DifferenceSelector,
        IntersectionSelector,
        ItemsSelector,
        UnionSelector,
        WhereSelector,
    )

    if isinstance(selector, ItemsSelector):
        return {"select": "items", "tier": selector.tier.to_data()}
    if isinstance(selector, WhereSelector):
        return {
            "select": "where",
            "base": _selector_to_data(selector.base),
            "predicate": predicate_to_data(selector.predicate),
        }
    if isinstance(selector, (UnionSelector, IntersectionSelector)):
        return {
            "op": "union" if isinstance(selector, UnionSelector) else "intersection",
            "args": [_selector_to_data(argument) for argument in selector.args],
        }
    if isinstance(selector, DifferenceSelector):
        return {
            "op": "difference",
            "left": _selector_to_data(selector.left),
            "right": _selector_to_data(selector.right),
        }
    raise ValueError(f"{type(selector).__name__} has no sequence-ordering JSON encoder")


@dataclass(frozen=True, slots=True)
class _MatchRequest:
    operation: str
    ordering: Ordering
    pattern: Pattern
    limit: int | None = None

    def evaluate(self, graph: Graph) -> dict[str, JsonValue]:
        """Evaluate this decoded request against *graph*."""
        compiled = compile_pattern(self.pattern)
        if self.operation == "exists":
            return {"exists": compiled.exists(graph, self.ordering)}
        if self.operation == "focus":
            return {"nodes": compiled.focus(graph, self.ordering).to_data()}
        if self.operation == "count":
            return {"count": compiled.count(graph, self.ordering)}
        return compiled.spans(graph, self.ordering, limit=self.limit).to_data()


def _match_request_loads(source: str | bytes) -> _MatchRequest | _PairsRequest:
    value = cast(JsonValue, _parsed_json(source))
    node = cast(dict[str, JsonValue], _object(value, "$"))
    if "match" not in node:
        _refuse_field_set(node.keys(), {"match"}, {"match"}, "$")
    operation = _string(node["match"], "$.match")
    if operation == "pairs":
        return _pairs_request_from_node(node)
    if operation not in {"exists", "focus", "spans", "count"}:
        raise Refusal(
            RefusalStage.DISCRIMINATOR,
            f"$.match has unknown match operation {operation!r}",
        )
    allowed = {"match", "ordering", "pattern"}
    if operation == "spans":
        allowed.add("limit")
    _refuse_field_set(node.keys(), allowed, {"match", "ordering", "pattern"}, "$")
    limit = None
    if "limit" in node:
        limit = _nonnegative_integer(node["limit"], "$.limit")
    return _MatchRequest(
        operation,
        _decode_ordering(node["ordering"], "$.ordering"),
        _decode_pattern(node["pattern"], "$.pattern"),
        limit,
    )


def _evaluate_match_request(graph: Graph, source: str | bytes) -> dict[str, JsonValue]:
    """Decode and evaluate one CLI match request."""
    request = _match_request_loads(source)
    if isinstance(request, _PairsRequest):
        return span_pairs(
            graph,
            request.left,
            request.right,
            request.relation,
            request.offsets,
            limit=request.limit,
        ).to_data()
    return request.evaluate(graph)


@dataclass(frozen=True, slots=True)
class _PairsRequest:
    left: Selector
    right: Selector
    relation: IntervalRelation
    offsets: OffsetProfile
    limit: int | None = None
    _left_data: JsonValue = field(default=None, repr=False, compare=False)
    _right_data: JsonValue = field(default=None, repr=False, compare=False)

    def to_data(self) -> dict[str, JsonValue]:
        """Return the pairs request as strict JSON data."""
        result: dict[str, JsonValue] = {
            "match": "pairs",
            "left": deepcopy(self._left_data),
            "right": deepcopy(self._right_data),
            "relation": self.relation.value,
            "offsets": _offset_profile_to_data(self.offsets),
        }
        if self.limit is not None:
            result["limit"] = self.limit
        return result


def _pairs_request_loads(source: str | bytes) -> _PairsRequest:
    value = cast(JsonValue, _parsed_json(source))
    node = cast(dict[str, JsonValue], _object(value, "$"))
    return _pairs_request_from_node(node)


def _pairs_request_from_node(node: dict[str, JsonValue]) -> _PairsRequest:
    allowed = {"match", "left", "right", "relation", "offsets", "limit"}
    required = {"match", "left", "right", "relation", "offsets"}
    _refuse_field_set(node.keys(), allowed, required, "$")
    if _string(node["match"], "$.match") != "pairs":
        raise Refusal(RefusalStage.VALUE, "$.match must be 'pairs'")
    left = _decode_selector(node["left"], "$.left")
    right = _decode_selector(node["right"], "$.right")
    relation_text = _string(node["relation"], "$.relation")
    try:
        relation = IntervalRelation(relation_text)
    except ValueError as error:
        raise Refusal(
            RefusalStage.VALUE,
            f"$.relation has invalid interval relation {relation_text!r}",
        ) from error
    limit = node.get("limit")
    if limit is not None and (type(limit) is not int or limit < 0):
        raise Refusal(
            RefusalStage.VALUE, "$.limit must be a nonnegative integer or null"
        )
    return _PairsRequest(
        left,
        right,
        relation,
        _decode_offset_profile(node["offsets"], "$.offsets"),
        limit,
        deepcopy(node["left"]),
        deepcopy(node["right"]),
    )


__all__ = [
    "MAX_PATTERN_POSITIONS",
    "AdjacentRuns",
    "AltPattern",
    "AtomPattern",
    "CompiledPattern",
    "ContainerOrder",
    "EndPattern",
    "Extent",
    "FocusPattern",
    "Ordering",
    "Pattern",
    "RepeatPattern",
    "SeqPattern",
    "SpanMatch",
    "SpanMatches",
    "SpanPairs",
    "StartPattern",
    "TierOrder",
    "compile_pattern",
    "ordering_to_data",
    "pattern_loads",
    "pattern_to_data",
    "span_pairs",
]
