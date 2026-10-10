"""Regular sequence matching and interval joins over graph selections."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, cast, overload

from tiergraph.budget import (
    _MAX_USER_STEPS,
    BudgetExhausted,
    Exhaustion,
    WorkBudget,
    WorkMeter,
    _active_meter,
    _aggregate_active,
    _ChargeMeter,
    _metered,
)
from tiergraph.core import (
    Graph,
    ItemRef,
    JsonValue,
    PolyadicRelationDeclaration,
    QualifiedName,
    Refusal,
    RefusalStage,
    XsdType,
)
from tiergraph.machine import MAX_REPEAT_COUNT, _decode_item_ref, _decode_qname
from tiergraph.predicate import (
    BoundPredicate,
    Cell,
    Equals,
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
from tiergraph.traversal import (
    OrderedContainment,
    OrderedPolyadicTraversal,
    PolyadicSide,
)
from tiergraph.wire import _object, _parsed_json, _string

MAX_PATTERN_POSITIONS = 100_000
MAX_PATTERN_STATES = 1_000_000
_MAX_PATTERN_NODES = 10_000
_MAX_PATTERN_DEPTH = 256
_MIN_PARTS = 2
_GATE_MIN_PARTS = 32
_GATE_DEPTH = 3
_GATE_FRAGMENT_STATES = 4096
# Tests turn the optimization off to compare against the original closure path.
_GATE_PRUNING = True
_GUARD_ALWAYS = 0
_GUARD_START = 1
_GUARD_END = 2
_FUTURE_ATOM = 1
_FUTURE_END = 2


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


@dataclass(frozen=True, slots=True)
class DeclaredOrder:
    """Read an explicitly declared polyadic successor chain as one scope.

    ``successor`` names the ordered polyadic relation. ``members`` selects the
    items returned to matching views. ``chain`` may select a larger complete
    chain from which those members are projected; when omitted, ``members`` is
    also the complete chain. ``open_left`` admits a chain whose predecessor lies
    outside the selected members.
    """

    successor: QualifiedName
    members: Selector
    open_left: bool = False
    chain: Selector | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.open_left, bool):
            raise ValueError("DeclaredOrder open_left must be a boolean")

    def project(self, members: Selector) -> DeclaredOrder:
        """Project this order while retaining its complete-chain selector."""
        return DeclaredOrder(
            self.successor,
            members,
            self.open_left,
            self.members if self.chain is None else self.chain,
        )


type Ordering = TierOrder | ContainerOrder | AdjacentRuns | DeclaredOrder
type _TruthRows = tuple[Mapping[Node, bool], ...]


@dataclass(frozen=True, slots=True)
class _IndexedTruth:
    """Carry ordinary atom truth plus true indexed atoms by node."""

    rows: _TruthRows
    true_atoms: Mapping[Node, tuple[int, ...]]

    def __getitem__(self, atom: int) -> Mapping[Node, bool]:
        return self.rows[atom]


type _TruthTable = _TruthRows | _IndexedTruth


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
    pending = [pattern]
    while pending:
        current = pending.pop()
        if isinstance(current, (StartPattern, EndPattern)):
            return True
        pending.extend(_children(current))
    return False


def _focus_count(pattern: Pattern) -> int:
    count = 0
    pending = [pattern]
    while pending:
        current = pending.pop()
        count += int(isinstance(current, FocusPattern))
        pending.extend(_children(current))
    return count


def _nullable(pattern: Pattern) -> bool:
    nullable: dict[int, bool] = {}
    for current in _postorder(pattern):
        if isinstance(current, AtomPattern):
            result = False
        elif isinstance(current, (StartPattern, EndPattern)):
            result = True
        elif isinstance(current, SeqPattern):
            result = all(nullable[id(part)] for part in current.parts)
        elif isinstance(current, AltPattern):
            result = any(nullable[id(part)] for part in current.parts)
        elif isinstance(current, RepeatPattern):
            result = current.min == 0 or nullable[id(current.body)]
        else:
            result = nullable[id(current.body)]
        nullable[id(current)] = result
    return nullable[id(pattern)]


def _position_count(pattern: Pattern) -> int:
    counts: dict[int, int] = {}
    for current in _postorder(pattern):
        if isinstance(current, AtomPattern):
            count = 1
        elif isinstance(current, (StartPattern, EndPattern)):
            count = 0
        elif isinstance(current, (SeqPattern, AltPattern)):
            count = sum(counts[id(part)] for part in current.parts)
        elif isinstance(current, FocusPattern):
            count = counts[id(current.body)]
        else:
            multiplier = current.min + 1 if current.max is None else current.max
            count = counts[id(current.body)] * multiplier
        counts[id(current)] = count
    return counts[id(pattern)]


def _max_width(pattern: Pattern) -> int | None:
    maximums: dict[int, int | None] = {}
    for current in _postorder(pattern):
        if isinstance(current, AtomPattern):
            width: int | None = 1
        elif isinstance(current, (StartPattern, EndPattern)):
            width = 0
        elif isinstance(current, FocusPattern):
            width = maximums[id(current.body)]
        elif isinstance(current, (SeqPattern, AltPattern)):
            widths = tuple(maximums[id(part)] for part in current.parts)
            if None in widths:
                width = None
            elif isinstance(current, SeqPattern):
                width = sum(cast(tuple[int, ...], widths))
            else:
                width = max(cast(tuple[int, ...], widths))
        else:
            body_width = maximums[id(current.body)]
            if body_width is None:
                width = None
            elif current.max is None:
                width = 0 if body_width == 0 else None
            else:
                width = body_width * current.max
        maximums[id(current)] = width
    return maximums[id(pattern)]


def _postorder(pattern: Pattern) -> Iterator[Pattern]:
    """Yield a pattern tree child-first without using the interpreter stack."""
    pending = [(pattern, False)]
    while pending:
        current, visited = pending.pop()
        if visited:
            yield current
            continue
        pending.append((current, True))
        pending.extend((child, False) for child in _children(current))


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


def _pattern_depth(pattern: Pattern) -> int:
    """Return AST depth without using the interpreter stack."""
    maximum = 0
    pending = [(pattern, 1)]
    while pending:
        current, depth = pending.pop()
        maximum = max(maximum, depth)
        pending.extend((child, depth + 1) for child in _children(current))
    return maximum


def _pattern_state_count(pattern: Pattern) -> int:
    """Return the number of states its Thompson construction allocates."""
    counts: dict[int, int] = {}
    pending = [(pattern, False)]
    while pending:
        current, visited = pending.pop()
        if not visited:
            pending.append((current, True))
            pending.extend((child, False) for child in _children(current))
            continue
        if isinstance(current, AtomPattern | StartPattern | EndPattern):
            count = 2
        elif isinstance(current, SeqPattern | AltPattern):
            count = 2 + sum(counts[id(part)] for part in current.parts)
        elif isinstance(current, FocusPattern):
            count = 2 + counts[id(current.body)]
        else:
            factor = current.min + 1 if current.max is None else current.max
            count = 2 + counts[id(current.body)] * factor
        counts[id(current)] = count
    return counts[id(pattern)]


@dataclass(frozen=True, slots=True)
class _Epsilon:
    target: int
    guard: int = _GUARD_ALWAYS


@dataclass(frozen=True, slots=True)
class _AtomEdge:
    target: int
    atom: int
    focus: bool


type _GateAtoms = int | tuple[int, ...] | None
type _GateLookahead = tuple[_GateAtoms, ...]
_GATE_PROBE_ATOMS = 0
_GATE_PROBE_EXIT = 1
_GATE_PROBE_UNKNOWN = 2


@dataclass(frozen=True, slots=True)
class _GateProbe:
    """Describe one analyzed successor at one lookahead depth."""

    atoms: _GateAtoms
    terminal: int = _GATE_PROBE_ATOMS


@dataclass(frozen=True, slots=True)
class _Gate:
    """Per-successor tests for one large alternation split."""

    targets: tuple[int, ...]
    first: tuple[_GateLookahead, ...]


type _GatePosting = int | tuple[int, ...] | None


@dataclass(frozen=True, slots=True)
class _GateDepth:
    """Index successor ordinals by atom, with conservative terminals."""

    exits: int
    unknown: int
    postings: tuple[_GatePosting, ...]


@dataclass(frozen=True, slots=True)
class _GateIndex:
    """Replace a fully indexable successor scan with depth postings."""

    depths: tuple[_GateDepth, ...]
    successor_ordinals: tuple[int, ...] | None
    passthrough_ordinals: tuple[int, ...]


type _GateMetadata = _Gate | _GateIndex


@dataclass(frozen=True, slots=True)
class _Gates(Mapping[int, _GateMetadata]):
    """Store gates as sorted immutable, copyable and picklable pairs."""

    entries: tuple[tuple[int, _GateMetadata], ...]

    def __getitem__(self, key: int) -> _GateMetadata:
        lower = 0
        upper = len(self.entries)
        while lower < upper:
            middle = (lower + upper) // 2
            state, gate = self.entries[middle]
            if state < key:
                lower = middle + 1
            elif state > key:
                upper = middle
            else:
                return gate
        raise KeyError(key)

    def __iter__(self) -> Iterator[int]:
        return (state for state, _gate in self.entries)

    def __len__(self) -> int:
        return len(self.entries)


def _gate_closure(
    epsilon: tuple[tuple[int, ...], ...], seeds: set[int]
) -> set[int] | None:
    """Close *seeds* ignoring guards, or decline an oversized fragment."""
    result = set(seeds)
    pending = list(seeds)
    while pending:
        state = pending.pop()
        for edge in epsilon[state]:
            target = _epsilon_target(edge)
            if target not in result:
                result.add(target)
                if len(result) > _GATE_FRAGMENT_STATES:
                    return None
                pending.append(target)
    return result


def _gate_successor(
    edge: int,
    accept: int,
    epsilon: tuple[tuple[int, ...], ...],
    atom_edges: tuple[tuple[int, ...], ...],
    atom_sets: dict[tuple[int, ...], tuple[int, ...]],
) -> tuple[_GateProbe, ...]:
    """Analyze one successor without using guards or predicate truth."""
    reachable = _gate_closure(epsilon, {_epsilon_target(edge)})
    if reachable is None:
        return tuple(_GateProbe(None, _GATE_PROBE_UNKNOWN) for _ in range(_GATE_DEPTH))
    accepted = accept in reachable
    first: list[_GateProbe] = []
    for depth in range(_GATE_DEPTH):
        if accepted:
            first.append(_GateProbe(None, _GATE_PROBE_EXIT))
        else:
            atoms = tuple(
                sorted(
                    {
                        _atom_index(atom_edge)
                        for state in reachable
                        for atom_edge in atom_edges[state]
                    }
                )
            )
            if len(atoms) == 1:
                first.append(_GateProbe(atoms[0]))
            else:
                first.append(_GateProbe(atom_sets.setdefault(atoms, atoms)))
        targets = {
            _atom_target(atom_edge)
            for state in reachable
            for atom_edge in atom_edges[state]
        }
        reachable = _gate_closure(epsilon, targets)
        if reachable is None:
            first.extend(
                _GateProbe(None, _GATE_PROBE_UNKNOWN)
                for _ in range(_GATE_DEPTH - depth - 1)
            )
            break
        if not accepted and accept in reachable:
            accepted = True
    return tuple(first)


def _indexed_gate(
    successors: tuple[int, ...],
    accept: int,
    epsilon: tuple[tuple[int, ...], ...],
    atom_edges: tuple[tuple[int, ...], ...],
    atom_sets: dict[tuple[int, ...], tuple[int, ...]],
    predicates: tuple[Predicate, ...],
    successor_ordinals: tuple[int, ...],
    edge_count: int,
) -> _GateIndex | None:
    """Return replacement postings for a sparse exact-equality split."""

    def indexable(predicate: Predicate) -> bool:
        """Return whether one atom has an exact cell-equality key."""
        return isinstance(predicate, Equals) and isinstance(predicate.operand, Cell)

    exits = [0] * _GATE_DEPTH
    unknown = [0] * _GATE_DEPTH
    postings: list[list[_GatePosting]] = [
        [None] * len(predicates) for _ in range(_GATE_DEPTH)
    ]
    posting_count = 0
    for ordinal, edge in enumerate(successors):
        analysis = _gate_successor(edge, accept, epsilon, atom_edges, atom_sets)
        for depth in range(_GATE_DEPTH):
            probe = analysis[depth]
            if probe.terminal == _GATE_PROBE_EXIT:
                exits[depth] |= 1 << ordinal
                continue
            if probe.terminal == _GATE_PROBE_UNKNOWN:
                unknown[depth] |= 1 << ordinal
                continue
            atoms = (
                (probe.atoms,)
                if isinstance(probe.atoms, int)
                else (() if probe.atoms is None else probe.atoms)
            )
            for atom in atoms:
                if not indexable(predicates[atom]):
                    return None
                posting_count += 1
                if posting_count > len(successors) * _GATE_DEPTH:
                    return None
                previous = postings[depth][atom]
                if previous is None:
                    postings[depth][atom] = ordinal
                elif isinstance(previous, int):
                    postings[depth][atom] = (previous, ordinal)
                else:
                    postings[depth][atom] = (*previous, ordinal)
    depths = tuple(
        _GateDepth(exits[depth], unknown[depth], tuple(postings[depth]))
        for depth in range(_GATE_DEPTH)
    )
    all_successors = len(successor_ordinals) == edge_count and all(
        ordinal == successor for ordinal, successor in enumerate(successor_ordinals)
    )
    successor_positions = None if all_successors else successor_ordinals
    passthrough: tuple[int, ...] = ()
    if not all_successors:
        successor_set = set(successor_ordinals)
        passthrough = tuple(
            index for index in range(edge_count) if index not in successor_set
        )
    return _GateIndex(
        depths,
        successor_positions,
        passthrough,
    )


def _build_gates(
    accept: int,
    epsilon: tuple[tuple[int, ...], ...],
    atom_edges: tuple[tuple[int, ...], ...],
    predicates: tuple[Predicate, ...],
    states: tuple[int, ...] | None = None,
) -> _Gates | None:
    """Build safe gates only for known or discovered large NFA splits."""
    candidates = (
        tuple(
            state
            for state, edges in enumerate(epsilon)
            if sum(_epsilon_guard(edge) == _GUARD_ALWAYS for edge in edges)
            >= _GATE_MIN_PARTS
        )
        if states is None
        else states
    )
    if not candidates:
        return None
    gates: dict[int, _GateMetadata] = {}
    atom_sets: dict[tuple[int, ...], tuple[int, ...]] = {}
    for state in candidates:
        successor_ordinals = tuple(
            ordinal
            for ordinal, edge in enumerate(epsilon[state])
            if _epsilon_guard(edge) == _GUARD_ALWAYS
        )
        successors = tuple(epsilon[state][ordinal] for ordinal in successor_ordinals)
        indexed = _indexed_gate(
            successors,
            accept,
            epsilon,
            atom_edges,
            atom_sets,
            predicates,
            successor_ordinals,
            len(epsilon[state]),
        )
        if indexed is not None:
            gates[state] = indexed
            continue
        gates[state] = _Gate(
            tuple(_epsilon_target(edge) for edge in successors),
            tuple(
                tuple(
                    None if probe.terminal else probe.atoms
                    for probe in _gate_successor(
                        edge, accept, epsilon, atom_edges, atom_sets
                    )
                )
                for edge in successors
            ),
        )
    return _Gates(tuple(sorted(gates.items())))


_EPSILON_GUARD_BITS = 2
_EPSILON_GUARD_MASK = (1 << _EPSILON_GUARD_BITS) - 1
_ATOM_INDEX_BITS = 14
_ATOM_INDEX_MASK = (1 << _ATOM_INDEX_BITS) - 1
_ATOM_TARGET_SHIFT = _ATOM_INDEX_BITS + 1


def _pack_epsilon(target: int, guard: int = _GUARD_ALWAYS) -> int:
    """Pack one epsilon edge into one nonnegative Python integer."""
    return (target << _EPSILON_GUARD_BITS) | guard


def _epsilon_target(edge: _Epsilon | int) -> int:
    return edge.target if isinstance(edge, _Epsilon) else edge >> _EPSILON_GUARD_BITS


def _epsilon_guard(edge: _Epsilon | int) -> int:
    return edge.guard if isinstance(edge, _Epsilon) else edge & _EPSILON_GUARD_MASK


def _pack_atom(target: int, atom: int, focus: bool) -> int:
    """Pack one atom edge under the pattern's 10,000-node bound."""
    if atom > _ATOM_INDEX_MASK:
        raise Refusal(
            RefusalStage.SEMANTICS,
            f"pattern has {atom + 1} distinct atoms; packed edge limit "
            f"{_ATOM_INDEX_MASK + 1}",
        )
    return (target << _ATOM_TARGET_SHIFT) | (atom << 1) | focus


def _atom_target(edge: _AtomEdge | int) -> int:
    return edge.target if isinstance(edge, _AtomEdge) else edge >> _ATOM_TARGET_SHIFT


def _atom_index(edge: _AtomEdge | int) -> int:
    return edge.atom if isinstance(edge, _AtomEdge) else (edge >> 1) & _ATOM_INDEX_MASK


def _atom_focus(edge: _AtomEdge | int) -> bool:
    return edge.focus if isinstance(edge, _AtomEdge) else bool(edge & 1)


@dataclass(slots=True)
class _BuildFrame:
    """One suspended Thompson-construction call."""

    pattern: Pattern
    focused: bool
    start: int
    end: int
    cursor: int
    index: int = 0


class _NfaBuilder:
    def __init__(self) -> None:
        self.epsilon: list[list[int]] = []
        self.atoms: list[list[int]] = []
        self.predicates: list[Predicate] = []
        self._predicate_indices: dict[Predicate, int] = {}
        self.gate_splits: list[int] | None = None

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

    def build(  # noqa: PLR0915 -- explicit stack mirrors each AST case
        self, pattern: Pattern, *, focused: bool = False
    ) -> tuple[int, int]:
        """Build *pattern* iteratively and return its entry and exit states."""

        def _frame(current: Pattern, is_focused: bool) -> _BuildFrame:
            start, end = self.state(), self.state()
            if (
                isinstance(current, AltPattern)
                and len(current.parts) >= _GATE_MIN_PARTS
            ):
                if self.gate_splits is None:
                    self.gate_splits = []
                self.gate_splits.append(start)
            return _BuildFrame(current, is_focused, start, end, start)

        frames = [_frame(pattern, focused)]
        returned: tuple[int, int] | None = None
        while frames:
            current = frames[-1]
            node = current.pattern
            if returned is not None:
                child_start, child_end = returned
                returned = None
                if isinstance(node, FocusPattern):
                    self.epsilon[current.start].append(_pack_epsilon(child_start))
                    self.epsilon[child_end].append(_pack_epsilon(current.end))
                    returned = current.start, current.end
                    frames.pop()
                elif isinstance(node, SeqPattern):
                    self.epsilon[current.cursor].append(_pack_epsilon(child_start))
                    current.cursor = child_end
                    current.index += 1
                elif isinstance(node, AltPattern):
                    self.epsilon[current.start].append(_pack_epsilon(child_start))
                    self.epsilon[child_end].append(_pack_epsilon(current.end))
                    current.index += 1
                else:
                    assert isinstance(node, RepeatPattern)
                    required = current.index < node.min
                    self.epsilon[current.cursor].append(_pack_epsilon(child_start))
                    if required:
                        current.cursor = child_end
                    elif node.max is None:
                        self.epsilon[child_end].append(_pack_epsilon(current.cursor))
                    else:
                        self.epsilon[current.cursor].append(_pack_epsilon(child_end))
                        current.cursor = child_end
                    current.index += 1
                continue
            if isinstance(node, AtomPattern):
                self.atoms[current.start].append(
                    _pack_atom(
                        current.end,
                        self.atom_index(node.predicate),
                        current.focused,
                    )
                )
                returned = current.start, current.end
                frames.pop()
            elif isinstance(node, StartPattern):
                self.epsilon[current.start].append(
                    _pack_epsilon(current.end, _GUARD_START)
                )
                returned = current.start, current.end
                frames.pop()
            elif isinstance(node, EndPattern):
                self.epsilon[current.start].append(
                    _pack_epsilon(current.end, _GUARD_END)
                )
                returned = current.start, current.end
                frames.pop()
            elif isinstance(node, FocusPattern):
                frames.append(_frame(node.body, True))
            elif isinstance(node, (SeqPattern, AltPattern)):
                if current.index < len(node.parts):
                    frames.append(_frame(node.parts[current.index], current.focused))
                else:
                    if isinstance(node, SeqPattern):
                        self.epsilon[current.cursor].append(_pack_epsilon(current.end))
                    returned = current.start, current.end
                    frames.pop()
            else:
                assert isinstance(node, RepeatPattern)
                total = node.min + 1 if node.max is None else node.max
                if current.index < total:
                    if current.index == node.min and node.max is None:
                        self.epsilon[current.cursor].append(_pack_epsilon(current.end))
                    frames.append(_frame(node.body, current.focused))
                else:
                    if node.max is not None:
                        self.epsilon[current.cursor].append(_pack_epsilon(current.end))
                    returned = current.start, current.end
                    frames.pop()
        assert returned is not None
        return returned


@dataclass(frozen=True, slots=True)
class _Scope:
    nodes: tuple[Node, ...]
    offsets: tuple[tuple[int, int], ...] | None = None
    open_left: bool = False
    open_right: bool = False


def _read_scopes(graph: Graph, ordering: Ordering) -> tuple[_Scope, ...]:
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
    if isinstance(ordering, DeclaredOrder):
        return (_declared_scope(graph, ordering),)
    if not isinstance(ordering, AdjacentRuns):
        raise Refusal(
            RefusalStage.SEMANTICS,
            "pattern ordering must be TierOrder, ContainerOrder, AdjacentRuns, "
            "or DeclaredOrder",
        )
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


def _truth_table(
    bound: tuple[BoundPredicate, ...],
    scopes: tuple[_Scope, ...],
    indexed_atoms: tuple[int, ...] = (),
) -> _TruthTable:
    nodes: list[Node] = []
    seen: set[Node] = set()
    for scope in scopes:
        for node in scope.nodes:
            if node not in seen:
                seen.add(node)
                nodes.append(node)
    meter = _active_meter()
    tables: list[Mapping[Node, bool]] = []
    indexed = set(indexed_atoms)
    true_atoms: dict[Node, list[int]] | None = (
        {node: [] for node in nodes} if indexed else None
    )
    candidates = NodeSet(bound[0].graph, tuple(nodes)) if indexed and bound else None
    for atom, predicate in enumerate(bound):
        decisions: dict[Node, bool] = {}
        selected = (
            set(predicate.select(candidates).nodes)
            if atom in indexed and candidates is not None
            else None
        )
        for node in nodes:
            if meter is not None:
                meter.charge(1)
            decision = (
                node in selected if selected is not None else predicate.holds(node)
            )
            decisions[node] = decision
            if decision and atom in indexed:
                assert true_atoms is not None
                true_atoms[node].append(atom)
        tables.append(MappingProxyType(decisions))
    rows = tuple(tables)
    if true_atoms is None:
        return rows
    return _IndexedTruth(
        rows,
        MappingProxyType({node: tuple(atoms) for node, atoms in true_atoms.items()}),
    )


def _indexed_atoms(gates: _Gates | None) -> tuple[int, ...]:
    """Return the atom IDs used by any replacement index."""
    if gates is None:
        return ()
    return tuple(
        atom
        for atom in range(
            max(
                (
                    len(depth.postings)
                    for _state, gate in gates.entries
                    if isinstance(gate, _GateIndex)
                    for depth in gate.depths
                ),
                default=0,
            )
        )
        if any(
            depth.postings[atom] is not None
            for _state, gate in gates.entries
            if isinstance(gate, _GateIndex)
            for depth in gate.depths
        )
    )


def _indexed_gate_edges(
    edges: tuple[int, ...],
    gate: _GateIndex,
    nodes: tuple[Node, ...],
    truth: _IndexedTruth,
    position: int,
    length: int,
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Return indexed successors and the distinct candidates consulted."""
    candidates: set[int] | None = None
    touched: set[int] = set()
    stop = min(_GATE_DEPTH, length - position)
    for depth in range(stop):
        indexed = gate.depths[depth]
        terminals = indexed.exits | indexed.unknown
        if candidates is None:
            admitted: set[int] = set()
            while terminals:
                bit = terminals & -terminals
                admitted.add(bit.bit_length() - 1)
                terminals ^= bit
        else:
            admitted = {
                candidate for candidate in candidates if terminals & (1 << candidate)
            }
        for atom in truth.true_atoms[nodes[position + depth]]:
            posting = indexed.postings[atom]
            if isinstance(posting, int):
                if candidates is None or posting in candidates:
                    admitted.add(posting)
            elif posting is not None:
                admitted.update(
                    posting
                    if candidates is None
                    else (value for value in posting if value in candidates)
                )
        touched.update(admitted)
        if candidates is None:
            candidates = admitted
        else:
            candidates.intersection_update(admitted)
        if not candidates:
            break
    assert candidates is not None

    def edge_ordinal(successor: int) -> int:
        """Map one indexed successor back to its original edge ordinal."""
        ordinals = gate.successor_ordinals
        return successor if ordinals is None else ordinals[successor]

    selected = sorted(
        (*gate.passthrough_ordinals, *(edge_ordinal(i) for i in candidates))
    )
    tested = tuple(
        _epsilon_target(edges[edge_ordinal(successor)]) for successor in sorted(touched)
    )
    return tuple(edges[ordinal] for ordinal in selected), tested


def _unchecked_step_meter(
    meter: _ChargeMeter | None, upper_bound: int
) -> WorkMeter | None:
    """Return a root step meter when this whole region cannot exhaust it."""
    if not isinstance(meter, WorkMeter) or meter._deadline is not None:
        return None
    limit = meter._step_limit
    if limit is None or meter._spent + upper_bound > limit:
        return None
    return meter


def _contains_sequence_selector(selector: Selector) -> bool:
    from tiergraph.selection import (  # noqa: PLC0415 -- cycle breaker
        DifferenceSelector,
        IntersectionSelector,
        SequenceSelector,
        UnionSelector,
        WhereSelector,
    )

    if isinstance(selector, SequenceSelector):
        return True
    if isinstance(selector, WhereSelector):
        return _contains_sequence_selector(selector.base)
    if isinstance(selector, (UnionSelector, IntersectionSelector)):
        return any(_contains_sequence_selector(argument) for argument in selector.args)
    if isinstance(selector, DifferenceSelector):
        return _contains_sequence_selector(
            selector.left
        ) or _contains_sequence_selector(selector.right)
    return False


def _node_label(node: Node) -> str:
    return f"{node.kind.value} {node.reference}"


def _declared_scope(  # noqa: PLR0915 -- validation order fixes diagnostics
    graph: Graph, ordering: DeclaredOrder
) -> _Scope:
    declaration = next(
        (
            candidate
            for candidate in graph.relation_declarations
            if candidate.name == ordering.successor
        ),
        None,
    )
    if not isinstance(declaration, PolyadicRelationDeclaration):
        raise Refusal(
            RefusalStage.SEMANTICS,
            f"declared order successor '{ordering.successor}' is not a declared "
            "polyadic relation",
        )
    complete_selector = ordering.members if ordering.chain is None else ordering.chain
    if _contains_sequence_selector(complete_selector):
        raise Refusal(
            RefusalStage.SEMANTICS,
            f"DeclaredOrder {'members' if ordering.chain is None else 'chain'} "
            "may not contain SequenceSelector",
        )
    chain = evaluate_selection(graph, complete_selector)
    if any(
        member.kind not in (NodeKind.ITEM, NodeKind.BOUNDARY) for member in chain.nodes
    ):
        raise Refusal(
            RefusalStage.SEMANTICS,
            f"DeclaredOrder {'members' if ordering.chain is None else 'chain'} "
            "must select only items and boundaries",
        )
    if ordering.chain is None:
        members = chain
    else:
        if _contains_sequence_selector(ordering.members):
            raise Refusal(
                RefusalStage.SEMANTICS,
                "DeclaredOrder members may not contain SequenceSelector",
            )
        members = evaluate_selection(graph, ordering.members)
        if any(
            member.kind not in (NodeKind.ITEM, NodeKind.BOUNDARY)
            for member in members.nodes
        ):
            raise Refusal(
                RefusalStage.SEMANTICS,
                "DeclaredOrder members must select only items and boundaries",
            )
    complete = set(chain.nodes)
    projected = set(members.nodes)
    if not projected <= complete:
        offender = next(node for node in members.nodes if node not in complete)
        raise Refusal(
            RefusalStage.SEMANTICS,
            f"DeclaredOrder members must be a subset of chain; "
            f"{_node_label(offender)} is outside",
        )

    named = tuple(
        (index, instance)
        for index, instance in enumerate(graph.polyadic_relations)
        if instance.declaration == ordering.successor
    )
    for instance_index, instance in named:
        if len(instance.sources) != 1 or len(instance.targets) != 1:
            raise Refusal(
                RefusalStage.SEMANTICS,
                f"declared order successor '{ordering.successor}' instance "
                f"{instance_index} must have exactly one source and one target",
            )

    traversal = OrderedPolyadicTraversal(
        graph, ordering.successor, PolyadicSide.SOURCES, PolyadicSide.TARGETS
    )
    try:
        incidences = traversal._instances(check_cycle=False)
    except ValueError as error:
        raise Refusal(RefusalStage.SEMANTICS, str(error)) from error

    admitted = complete
    outgoing: dict[Node, tuple[int, Node]] = {}
    incoming: dict[Node, tuple[int, Node]] = {}
    for instance_index, sources, targets in incidences:
        source = sources[0]
        target = targets[0]
        for endpoint in (source, target):
            if endpoint not in admitted:
                raise Refusal(
                    RefusalStage.SEMANTICS,
                    f"declared order successor '{ordering.successor}' instance "
                    f"{instance_index} names {_node_label(endpoint)} outside its "
                    f"{'member' if ordering.chain is None else 'chain'} selection",
                )
        previous_out = outgoing.get(source)
        if previous_out is not None:
            raise Refusal(
                RefusalStage.SEMANTICS,
                f"declared order successor '{ordering.successor}' gives "
                f"{_node_label(source)} more than one successor "
                f"(instances {previous_out[0]} and {instance_index})",
            )
        previous_in = incoming.get(target)
        if previous_in is not None:
            raise Refusal(
                RefusalStage.SEMANTICS,
                f"declared order successor '{ordering.successor}' gives "
                f"{_node_label(target)} more than one predecessor "
                f"(instances {previous_in[0]} and {instance_index})",
            )
        outgoing[source] = (instance_index, target)
        incoming[target] = (instance_index, source)

    finished: set[Node] = set()
    for root in chain.nodes:
        if root in finished:
            continue
        visiting: set[Node] = set()
        cursor = root
        while cursor not in finished:
            visiting.add(cursor)
            edge = outgoing.get(cursor)
            if edge is None:
                finished.update(visiting)
                break
            instance_index, target = edge
            if target in visiting:
                raise Refusal(
                    RefusalStage.SEMANTICS,
                    f"declared order successor '{ordering.successor}' instance "
                    f"{instance_index} closes a cycle at {_node_label(target)}",
                )
            cursor = target

    if not chain.nodes:
        return _Scope((), open_left=ordering.open_left)
    heads = tuple(member for member in chain.nodes if member not in incoming)
    if len(heads) != 1:
        raise Refusal(
            RefusalStage.SEMANTICS,
            f"declared order successor '{ordering.successor}' has {len(heads)} heads; "
            "exactly one is required",
        )
    head = heads[0]
    ordered: list[Node] = []
    cursor = head
    while True:
        ordered.append(cursor)
        edge = outgoing.get(cursor)
        if edge is None:
            break
        cursor = edge[1]
    visited = set(ordered)
    if len(visited) != len(
        admitted
    ):  # pragma: no cover - prior invariants imply reachability
        unreachable = next(member for member in chain.nodes if member not in visited)
        raise Refusal(
            RefusalStage.SEMANTICS,
            f"declared order successor '{ordering.successor}' leaves "
            f"{_node_label(unreachable)} unreachable from head {_node_label(head)}",
        )
    visible = tuple(node for node in ordered if node in projected)
    dropped_head = not visible or visible[0] != ordered[0]
    dropped_tail = not visible or visible[-1] != ordered[-1]
    return _Scope(
        visible,
        open_left=ordering.open_left or dropped_head,
        open_right=dropped_tail,
    )


@dataclass(frozen=True, slots=True)
class OpenPatternResult[Result]:
    """Carry a settled open-edge result and one watermark per ordering scope."""

    result: Result
    pending_from: tuple[int | None, ...]


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


@dataclass(frozen=True, slots=True, eq=False)
class BoundOrdering:
    """Read one ordering's scopes once for reuse by any number of patterns.

    Pass this prepared value to :meth:`CompiledPattern.bind` to share ordering
    validation and scope construction across patterns. Pass a raw ordering when
    each pattern should prepare the ordering independently.
    """

    graph: Graph
    ordering: Ordering
    _scopes: tuple[_Scope, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_scopes", _read_scopes(self.graph, self.ordering))

    @classmethod
    def _from_scopes(
        cls,
        graph: Graph,
        ordering: Ordering,
        scopes: tuple[_Scope, ...],
    ) -> BoundOrdering:
        prepared = object.__new__(cls)
        object.__setattr__(prepared, "graph", graph)
        object.__setattr__(prepared, "ordering", ordering)
        object.__setattr__(prepared, "_scopes", scopes)
        return prepared


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class CompiledPattern:
    """Hold one Thompson epsilon-NFA and its deduplicated atom table.

    ``epsilon`` and ``atom_edges`` store compact integer edges. Compilation
    validates the pattern size and refuses inputs that exceed the documented
    AST-node or NFA-state ceilings before returning this value.
    """

    pattern: Pattern
    start: int
    accept: int
    epsilon: tuple[tuple[int, ...], ...]
    atom_edges: tuple[tuple[int, ...], ...]
    predicates: tuple[Predicate, ...]
    _gates: _Gates | None = field(init=False, repr=False, compare=False)

    if TYPE_CHECKING:

        def __init__(
            self,
            pattern: Pattern,
            start: int,
            accept: int,
            epsilon: tuple[tuple[int | _Epsilon, ...], ...],
            atom_edges: tuple[tuple[int | _AtomEdge, ...], ...],
            predicates: tuple[Predicate, ...],
        ) -> None:
            """Accept packed edges and the private legacy construction values."""
            ...

    def __post_init__(self) -> None:
        epsilon = tuple(
            tuple(
                _pack_epsilon(_epsilon_target(edge), _epsilon_guard(edge))
                for edge in edges
            )
            for edges in self.epsilon
        )
        atom_edges = tuple(
            tuple(
                _pack_atom(_atom_target(edge), _atom_index(edge), _atom_focus(edge))
                for edge in edges
            )
            for edges in self.atom_edges
        )
        object.__setattr__(self, "epsilon", epsilon)
        object.__setattr__(self, "atom_edges", atom_edges)
        object.__setattr__(
            self,
            "_gates",
            _build_gates(self.accept, epsilon, atom_edges, self.predicates),
        )

    @classmethod
    def _from_builder(
        cls,
        pattern: Pattern,
        start: int,
        accept: int,
        epsilon: tuple[tuple[int, ...], ...],
        atom_edges: tuple[tuple[int, ...], ...],
        predicates: tuple[Predicate, ...],
        gates: _Gates | None,
    ) -> CompiledPattern:
        """Construct from an NFA builder that already identified large splits."""
        result = object.__new__(cls)
        object.__setattr__(result, "pattern", pattern)
        object.__setattr__(result, "start", start)
        object.__setattr__(result, "accept", accept)
        object.__setattr__(result, "epsilon", epsilon)
        object.__setattr__(result, "atom_edges", atom_edges)
        object.__setattr__(result, "predicates", predicates)
        object.__setattr__(result, "_gates", gates)
        return result

    def __eq__(self, other: object) -> bool:
        """Compare the public edge values independently of packed storage."""
        if type(other) is not type(self):
            return NotImplemented
        assert isinstance(other, CompiledPattern)
        return (
            self.pattern == other.pattern
            and self.start == other.start
            and self.accept == other.accept
            and tuple(
                tuple((_epsilon_target(edge), _epsilon_guard(edge)) for edge in edges)
                for edges in self.epsilon
            )
            == tuple(
                tuple((_epsilon_target(edge), _epsilon_guard(edge)) for edge in edges)
                for edges in other.epsilon
            )
            and tuple(
                tuple(
                    (_atom_target(edge), _atom_index(edge), _atom_focus(edge))
                    for edge in edges
                )
                for edges in self.atom_edges
            )
            == tuple(
                tuple(
                    (_atom_target(edge), _atom_index(edge), _atom_focus(edge))
                    for edge in edges
                )
                for edges in other.atom_edges
            )
            and self.predicates == other.predicates
        )

    def __repr__(self) -> str:
        """Retain the public structural repr while storing edges compactly."""
        epsilon = tuple(
            tuple(
                _Epsilon(_epsilon_target(edge), _epsilon_guard(edge)) for edge in edges
            )
            for edges in self.epsilon
        )
        atom_edges = tuple(
            tuple(
                _AtomEdge(_atom_target(edge), _atom_index(edge), _atom_focus(edge))
                for edge in edges
            )
            for edges in self.atom_edges
        )
        return (
            f"CompiledPattern(pattern={self.pattern!r}, start={self.start!r}, "
            f"accept={self.accept!r}, epsilon={epsilon!r}, "
            f"atom_edges={atom_edges!r}, predicates={self.predicates!r})"
        )

    def __hash__(self) -> int:
        """Hash the same public edge values as the unpacked representation."""
        epsilon = tuple(
            tuple(hash((_epsilon_target(edge), _epsilon_guard(edge))) for edge in edges)
            for edges in self.epsilon
        )
        atom_edges = tuple(
            tuple(
                hash((_atom_target(edge), _atom_index(edge), _atom_focus(edge)))
                for edge in edges
            )
            for edges in self.atom_edges
        )
        return hash(
            (
                self.pattern,
                self.start,
                self.accept,
                epsilon,
                atom_edges,
                self.predicates,
            )
        )

    @property
    def max_width(self) -> int | None:
        """Return the exact maximum consumed item count, or None if unbounded."""
        return _max_width(self.pattern)

    def _gated_edges(
        self,
        state: int,
        nodes: tuple[Node, ...],
        truth: _TruthTable,
        position: int,
        length: int,
    ) -> tuple[tuple[int, ...], tuple[int, ...]]:
        """Return safe successors and targets whose gate tests did work."""
        edges = self.epsilon[state]
        gate = None if self._gates is None else self._gates.get(state)
        if gate is None or position >= length:
            return edges, ()
        if isinstance(gate, _GateIndex):
            if not isinstance(truth, _IndexedTruth):
                return edges, tuple(
                    _epsilon_target(edge)
                    for edge in edges
                    if _epsilon_guard(edge) == _GUARD_ALWAYS
                )
            return _indexed_gate_edges(edges, gate, nodes, truth, position, length)
        skipped: set[int] = set()
        stop = min(_GATE_DEPTH, length - position)
        for target, first_by_depth in zip(gate.targets, gate.first, strict=True):
            keep = True
            for depth in range(stop):
                first = first_by_depth[depth]
                if first is None:
                    continue
                node = nodes[position + depth]
                matched = (
                    truth[first][node]
                    if isinstance(first, int)
                    else any(truth[atom][node] for atom in first)
                )
                if not matched:
                    keep = False
                    break
            if not keep:
                skipped.add(target)
        if not skipped:
            return edges, gate.targets
        return (
            tuple(edge for edge in edges if _epsilon_target(edge) not in skipped),
            gate.targets,
        )

    def _closure(
        self,
        states: set[int],
        position: int,
        length: int,
        *,
        nodes: tuple[Node, ...] | None = None,
        truth: _TruthTable | None = None,
        open_right: bool = False,
        open_left: bool = False,
        meter: _ChargeMeter | None = None,
        unchecked: WorkMeter | None = None,
    ) -> set[int]:
        result = set(states)
        pending = list(states)
        if nodes is None or truth is None or not self._gates or not _GATE_PRUNING:
            while pending:
                state = pending.pop()
                for edge in self.epsilon[state]:
                    guard = _epsilon_guard(edge)
                    target = _epsilon_target(edge)
                    if guard == _GUARD_END and open_right:
                        continue
                    if guard == _GUARD_START and (position != 0 or open_left):
                        continue
                    if guard == _GUARD_END and position != length:
                        continue
                    if target not in result:
                        result.add(target)
                        pending.append(target)
            if unchecked is not None:
                unchecked._spent += len(result)
            elif meter is not None:
                meter.charge(len(result))
            return result
        gate_targets: list[tuple[int, ...]] = []
        while pending:
            state = pending.pop()
            edges, tested = self._gated_edges(state, nodes, truth, position, length)
            if tested:
                gate_targets.append(tested)
            for edge in edges:
                guard = _epsilon_guard(edge)
                target = _epsilon_target(edge)
                if guard == _GUARD_END and open_right:
                    continue
                if guard == _GUARD_START and (position != 0 or open_left):
                    continue
                if guard == _GUARD_END and position != length:
                    continue
                if target not in result:
                    result.add(target)
                    pending.append(target)
        # One existing closure unit pays for either visiting a successor's
        # entry state or consulting the gate that replaces that visit.  This
        # charges every successor test without exceeding the full closure.
        charge = (
            len(result)
            + sum(len(targets) for targets in gate_targets)
            - sum(target in result for targets in gate_targets for target in targets)
        )
        if unchecked is not None:
            unchecked._spent += charge
        elif meter is not None:
            meter.charge(charge)
        return result

    def _reverse_epsilon(self) -> tuple[tuple[tuple[int, int], ...], ...]:
        reverse: list[list[tuple[int, int]]] = [[] for _ in self.epsilon]
        for source, edges in enumerate(self.epsilon):
            for edge in edges:
                reverse[_epsilon_target(edge)].append((source, _epsilon_guard(edge)))
        return tuple(tuple(edges) for edges in reverse)

    @staticmethod
    def _guarded(
        guard: int, position: int, length: int, *, open_left: bool = False
    ) -> bool:
        return (
            guard == _GUARD_ALWAYS
            or (guard == _GUARD_START and position == 0 and not open_left)
            or (guard == _GUARD_END and position == length)
        )

    def _reverse_closure(
        self,
        states: set[int],
        position: int,
        length: int,
        reverse: tuple[tuple[tuple[int, int], ...], ...],
        *,
        open_left: bool = False,
        meter: _ChargeMeter | None = None,
    ) -> set[int]:
        result = set(states)
        pending = list(states)
        while pending:
            state = pending.pop()
            for source, guard in reverse[state]:
                if (
                    self._guarded(guard, position, length, open_left=open_left)
                    and source not in result
                ):
                    result.add(source)
                    pending.append(source)
        if meter is not None:
            meter.charge(len(result))
        return result

    def _scopes(self, graph: Graph, ordering: Ordering) -> tuple[_Scope, ...]:
        return _read_scopes(graph, ordering)

    def _check(self, operation: _PatternOperation, limit: int | None = None) -> None:
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

    def _prepare(
        self,
        graph: Graph,
        ordering: Ordering,
        operation: _PatternOperation,
        *,
        limit: int | None = None,
    ) -> tuple[tuple[_Scope, ...], _TruthTable]:
        """Bind atoms, validate the operation, then read and evaluate scopes."""
        bound = tuple(
            compile_predicate(predicate).bind(graph) for predicate in self.predicates
        )
        self._check(operation, limit)
        scopes = self._scopes(graph, ordering)
        return scopes, _truth_table(bound, scopes, _indexed_atoms(self._gates))

    def bind(
        self,
        graph: Graph,
        ordering: Ordering | BoundOrdering,
        *,
        budget: WorkBudget | WorkMeter | None = None,
    ) -> BoundPattern:
        """Bind predicates, read scopes and evaluate atoms once on one graph.

        With a raw ordering, predicates bind before the ordering scopes are
        prepared. View checks happen only when that view is called, so a combined
        ordering and view defect reports the ordering first. A prebuilt
        :class:`BoundOrdering` has already validated and prepared its scopes, so
        an ordering defect is reported when that value is constructed.
        """
        with _metered(budget, "pattern.bind"):
            return BoundPattern(self, graph, ordering)

    def _step(
        self,
        active: set[int],
        node: Node,
        truth: _TruthTable,
        position: int,
        length: int,
        *,
        nodes: tuple[Node, ...],
        open_right: bool = False,
        open_left: bool = False,
        meter: _ChargeMeter | None = None,
        unchecked: WorkMeter | None = None,
    ) -> set[int]:
        if unchecked is not None:
            unchecked._spent += len(active)
        elif meter is not None:
            meter.charge(len(active))
        targets = {
            _atom_target(edge)
            for state in active
            for edge in self.atom_edges[state]
            if truth[_atom_index(edge)][node]
        }
        return self._closure(
            targets,
            position + 1,
            length,
            nodes=nodes,
            truth=truth,
            open_right=open_right,
            open_left=open_left,
            meter=meter,
            unchecked=unchecked,
        )

    def _can_change_after_end(
        self,
        states: set[int],
        length: int,
        *,
        open_left: bool = False,
        meter: _ChargeMeter | None = None,
    ) -> bool:
        if meter is not None:
            meter.charge(len(self.epsilon))
        pending = [(state, 0) for state in states]
        seen = set(pending)
        while pending:
            state, phase = pending.pop()
            if state == self.accept and phase:
                return True
            for edge in self.epsilon[state]:
                next_phase = phase
                guard = _epsilon_guard(edge)
                if guard == _GUARD_START and (phase or length or open_left):
                    continue
                if guard == _GUARD_END:
                    next_phase = _FUTURE_END
                candidate = (_epsilon_target(edge), next_phase)
                if candidate not in seen:
                    seen.add(candidate)
                    pending.append(candidate)
            if phase != _FUTURE_END:
                for atom_edge in self.atom_edges[state]:
                    candidate = (_atom_target(atom_edge), _FUTURE_ATOM)
                    if candidate not in seen:
                        seen.add(candidate)
                        pending.append(candidate)
        return False

    def _pending_start(
        self, scope: _Scope, truth: _TruthTable, *, meter: _ChargeMeter | None = None
    ) -> int | None:
        length = len(scope.nodes)
        for start in range(length + 1):
            active = self._closure(
                {self.start},
                start,
                length,
                nodes=scope.nodes,
                truth=truth,
                open_right=True,
                open_left=scope.open_left,
                meter=meter,
            )
            for position in range(start, length):
                active = self._step(
                    active,
                    scope.nodes[position],
                    truth,
                    position,
                    length,
                    nodes=scope.nodes,
                    open_right=True,
                    open_left=scope.open_left,
                    meter=meter,
                )
                if not active:
                    break
            if active and self._can_change_after_end(
                active, length, open_left=scope.open_left, meter=meter
            ):
                return start
        return None

    def _scope_accepts(
        self,
        scope: _Scope,
        truth: _TruthTable,
        before: int,
        *,
        meter: _ChargeMeter | None = None,
    ) -> bool:
        length = len(scope.nodes)
        for start in range(before):
            active = self._closure(
                {self.start},
                start,
                length,
                nodes=scope.nodes,
                truth=truth,
                open_right=True,
                open_left=scope.open_left,
                meter=meter,
            )
            if self.accept in active:
                return True
            for position in range(start, length):
                active = self._step(
                    active,
                    scope.nodes[position],
                    truth,
                    position,
                    length,
                    nodes=scope.nodes,
                    open_right=True,
                    open_left=scope.open_left,
                    meter=meter,
                )
                if self.accept in active:
                    return True
                if not active:
                    break
        return False

    def _exists_over(
        self,
        scopes: tuple[_Scope, ...],
        truth: _TruthTable,
        *,
        open_right: bool = False,
        meter: _ChargeMeter | None = None,
    ) -> bool | OpenPatternResult[bool]:
        effective_open_right = open_right or any(scope.open_right for scope in scopes)
        if effective_open_right:
            pending = tuple(
                self._pending_start(scope, truth, meter=meter) for scope in scopes
            )
            settled = any(
                self._scope_accepts(
                    scope,
                    truth,
                    len(scope.nodes) + 1 if mark is None else mark,
                    meter=meter,
                )
                for scope, mark in zip(scopes, pending, strict=True)
            )
            return OpenPatternResult(settled, pending) if open_right else settled
        for scope in scopes:
            length = len(scope.nodes)
            active: set[int] = set()
            for position in range(length + 1):
                active = self._closure(
                    active | {self.start},
                    position,
                    length,
                    nodes=scope.nodes,
                    truth=truth,
                    open_left=scope.open_left,
                    meter=meter,
                )
                if self.accept in active:
                    return True
                if position < length:
                    active = self._step(
                        active,
                        scope.nodes[position],
                        truth,
                        position,
                        length,
                        nodes=scope.nodes,
                        open_left=scope.open_left,
                        meter=meter,
                    )
        return False

    @overload
    def exists(
        self,
        graph: Graph,
        ordering: Ordering,
        *,
        open_right: Literal[False] = False,
        budget: WorkBudget | WorkMeter | None = None,
    ) -> bool:
        """Return whether a closed scope contains an accepting span."""
        ...

    @overload
    def exists(
        self,
        graph: Graph,
        ordering: Ordering,
        *,
        open_right: Literal[True],
        budget: WorkBudget | WorkMeter | None = None,
    ) -> OpenPatternResult[bool]:
        """Return settled existence and open-right watermarks."""
        ...

    def exists(
        self,
        graph: Graph,
        ordering: Ordering,
        *,
        open_right: bool = False,
        budget: WorkBudget | WorkMeter | None = None,
    ) -> bool | OpenPatternResult[bool]:
        """Return whether any scope contains an accepting span."""
        with _metered(budget, "pattern.exists") as metered:
            scopes, truth = self._prepare(graph, ordering, _PatternOperation.EXISTS)
            return self._exists_over(
                scopes, truth, open_right=open_right, meter=metered.meter
            )

    def _focus_over(
        self,
        graph: Graph,
        scopes: tuple[_Scope, ...],
        truth: _TruthTable,
        *,
        open_right: bool = False,
        meter: _ChargeMeter | None = None,
    ) -> NodeSet | OpenPatternResult[NodeSet]:
        selected: list[Node] = []
        effective_open_right = open_right or any(scope.open_right for scope in scopes)
        pending = (
            tuple(self._pending_start(scope, truth, meter=meter) for scope in scopes)
            if effective_open_right
            else ()
        )
        if meter is not None:
            meter.charge(len(self.epsilon))
        reverse = self._reverse_epsilon()
        for scope_index, scope in enumerate(scopes):
            length = len(scope.nodes)
            forward: list[set[int]] = []
            active: set[int] = set()
            for position in range(length + 1):
                active = self._closure(
                    active | {self.start},
                    position,
                    length,
                    nodes=scope.nodes,
                    truth=truth,
                    open_left=scope.open_left,
                    meter=meter,
                )
                forward.append(active)
                if position < length:
                    active = self._step(
                        active,
                        scope.nodes[position],
                        truth,
                        position,
                        length,
                        nodes=scope.nodes,
                        open_left=scope.open_left,
                        meter=meter,
                    )
            backward: list[set[int]] = [set() for _ in range(length + 1)]
            backward[length] = self._reverse_closure(
                {self.accept},
                length,
                length,
                reverse,
                open_left=scope.open_left,
                meter=meter,
            )
            for position in range(length - 1, -1, -1):
                node = scope.nodes[position]
                if meter is not None:
                    meter.charge(len(self.atom_edges))
                predecessors = {self.accept}
                for state, edges in enumerate(self.atom_edges):
                    if any(
                        _atom_target(edge) in backward[position + 1]
                        and truth[_atom_index(edge)][node]
                        for edge in edges
                    ):
                        predecessors.add(state)
                backward[position] = self._reverse_closure(
                    predecessors,
                    position,
                    length,
                    reverse,
                    open_left=scope.open_left,
                    meter=meter,
                )
            for position, node in enumerate(scope.nodes):
                if any(
                    _atom_focus(edge)
                    and truth[_atom_index(edge)][node]
                    and _atom_target(edge) in backward[position + 1]
                    for state in forward[position]
                    for edge in self.atom_edges[state]
                ) and (
                    not effective_open_right
                    or pending[scope_index] is None
                    or position < cast(int, pending[scope_index])
                ):
                    selected.append(node)
        result = NodeSet(graph, tuple(selected))
        return OpenPatternResult(result, pending) if open_right else result

    @overload
    def focus(
        self,
        graph: Graph,
        ordering: Ordering,
        *,
        open_right: Literal[False] = False,
        budget: WorkBudget | WorkMeter | None = None,
    ) -> NodeSet:
        """Return focused items for closed scopes."""
        ...

    @overload
    def focus(
        self,
        graph: Graph,
        ordering: Ordering,
        *,
        open_right: Literal[True],
        budget: WorkBudget | WorkMeter | None = None,
    ) -> OpenPatternResult[NodeSet]:
        """Return settled focused items and open-right watermarks."""
        ...

    def focus(
        self,
        graph: Graph,
        ordering: Ordering,
        *,
        open_right: bool = False,
        budget: WorkBudget | WorkMeter | None = None,
    ) -> NodeSet | OpenPatternResult[NodeSet]:
        """Return every item consumed by a focus edge on an accepting run."""
        with _metered(budget, "pattern.focus") as metered:
            scopes, truth = self._prepare(graph, ordering, _PatternOperation.FOCUS)
            return self._focus_over(
                graph, scopes, truth, open_right=open_right, meter=metered.meter
            )

    def _span_matches(
        self,
        scope_index: int,
        scope: _Scope,
        truth: _TruthTable,
        *,
        open_right: bool = False,
        before: int | None = None,
        meter: _ChargeMeter | None = None,
        unchecked: WorkMeter | None = None,
        materialize: bool = True,
    ) -> Iterator[SpanMatch | None]:
        length = len(scope.nodes)
        stop = length + 1 if before is None else before
        for start in range(stop):
            active = self._closure(
                {self.start},
                start,
                length,
                nodes=scope.nodes,
                truth=truth,
                open_right=open_right,
                open_left=scope.open_left,
                meter=meter,
                unchecked=unchecked,
            )
            for end in range(start, length):
                active = self._step(
                    active,
                    scope.nodes[end],
                    truth,
                    end,
                    length,
                    nodes=scope.nodes,
                    open_right=open_right,
                    open_left=scope.open_left,
                    meter=meter,
                    unchecked=unchecked,
                )
                if not active:
                    break
                if self.accept in active:
                    if materialize:
                        # The proven-headroom path is count-only: materialized
                        # spans retain crossing-charge/CUT prefix semantics.
                        assert unchecked is None
                        if meter is not None:
                            meter.charge(end + 1 - start)
                        yield self._span(scope_index, scope, start, end + 1)
                    else:
                        if unchecked is not None:
                            unchecked._spent += 1
                        elif meter is not None:
                            meter.charge(1)
                        yield None

    @staticmethod
    def _span(scope_index: int, scope: _Scope, start: int, end: int) -> SpanMatch:
        offsets = (
            None
            if scope.offsets is None
            else (scope.offsets[start][0], scope.offsets[end - 1][1])
        )
        return SpanMatch(scope_index, start, end, scope.nodes[start:end], offsets)

    def _spans_over(
        self,
        scopes: tuple[_Scope, ...],
        truth: _TruthTable,
        *,
        limit: int | None = None,
        open_right: bool = False,
        meter: _ChargeMeter | None = None,
        allow_cut: bool = False,
    ) -> SpanMatches | OpenPatternResult[SpanMatches]:
        result: list[SpanMatch] = []
        effective_open_right = open_right or any(scope.open_right for scope in scopes)
        pending = (
            tuple(self._pending_start(scope, truth, meter=meter) for scope in scopes)
            if effective_open_right
            else ()
        )
        try:
            for scope_index, scope in enumerate(scopes):
                before = pending[scope_index] if effective_open_right else None
                for match in self._span_matches(
                    scope_index,
                    scope,
                    truth,
                    open_right=effective_open_right,
                    before=before,
                    meter=meter,
                ):
                    assert match is not None
                    if limit is not None and len(result) == limit:
                        spans = SpanMatches(tuple(result), Extent.CUT_AT_BOUND)
                        return (
                            OpenPatternResult(spans, pending) if open_right else spans
                        )
                    result.append(match)
        except BudgetExhausted as error:
            if not (allow_cut and error.exhaustion is Exhaustion.STEPS and result):
                raise
            spans = SpanMatches(tuple(result), Extent.CUT_AT_BUDGET)
            return OpenPatternResult(spans, pending) if open_right else spans
        spans = SpanMatches(tuple(result), Extent.EXHAUSTIVE)
        return OpenPatternResult(spans, pending) if open_right else spans

    @overload
    def spans(
        self,
        graph: Graph,
        ordering: Ordering,
        *,
        limit: int | None = None,
        open_right: Literal[False] = False,
        budget: WorkBudget | WorkMeter | None = None,
    ) -> SpanMatches:
        """Return accepting spans for closed scopes."""
        ...

    @overload
    def spans(
        self,
        graph: Graph,
        ordering: Ordering,
        *,
        limit: int | None = None,
        open_right: Literal[True],
        budget: WorkBudget | WorkMeter | None = None,
    ) -> OpenPatternResult[SpanMatches]:
        """Return settled spans and open-right watermarks."""
        ...

    def spans(
        self,
        graph: Graph,
        ordering: Ordering,
        *,
        limit: int | None = None,
        open_right: bool = False,
        budget: WorkBudget | WorkMeter | None = None,
    ) -> SpanMatches | OpenPatternResult[SpanMatches]:
        """Return each distinct accepting span once in scope-major order."""
        with _metered(budget, "pattern.spans") as metered:
            scopes, truth = self._prepare(
                graph, ordering, _PatternOperation.SPANS, limit=limit
            )
            return self._spans_over(
                scopes,
                truth,
                limit=limit,
                open_right=open_right,
                meter=metered.meter,
                allow_cut=(
                    metered.owns_outermost
                    and budget is not None
                    and not _aggregate_active()
                ),
            )

    def _count_over(
        self,
        scopes: tuple[_Scope, ...],
        truth: _TruthTable,
        *,
        open_right: bool = False,
        meter: _ChargeMeter | None = None,
    ) -> int | OpenPatternResult[int]:
        effective_open_right = open_right or any(scope.open_right for scope in scopes)
        pending = (
            tuple(self._pending_start(scope, truth, meter=meter) for scope in scopes)
            if effective_open_right
            else ()
        )
        states = len(self.epsilon)
        unchecked = (
            None
            if effective_open_right
            else _unchecked_step_meter(
                meter,
                sum(
                    (len(scope.nodes) + 1) * states
                    + len(scope.nodes) * (len(scope.nodes) + 1) // 2 * (2 * states + 1)
                    for scope in scopes
                ),
            )
        )
        count = sum(
            1
            for index, scope in enumerate(scopes)
            for _match in self._span_matches(
                index,
                scope,
                truth,
                open_right=effective_open_right,
                before=pending[index] if effective_open_right else None,
                meter=meter,
                unchecked=unchecked,
                materialize=False,
            )
        )
        return OpenPatternResult(count, pending) if open_right else count

    @overload
    def count(
        self,
        graph: Graph,
        ordering: Ordering,
        *,
        open_right: Literal[False] = False,
        budget: WorkBudget | WorkMeter | None = None,
    ) -> int:
        """Count accepting spans in closed scopes."""
        ...

    @overload
    def count(
        self,
        graph: Graph,
        ordering: Ordering,
        *,
        open_right: Literal[True],
        budget: WorkBudget | WorkMeter | None = None,
    ) -> OpenPatternResult[int]:
        """Return settled counts and open-right watermarks."""
        ...

    def count(
        self,
        graph: Graph,
        ordering: Ordering,
        *,
        open_right: bool = False,
        budget: WorkBudget | WorkMeter | None = None,
    ) -> int | OpenPatternResult[int]:
        """Count distinct accepting scope spans, never NFA runs."""
        with _metered(budget, "pattern.count") as metered:
            scopes, truth = self._prepare(graph, ordering, _PatternOperation.COUNT)
            return self._count_over(
                scopes, truth, open_right=open_right, meter=metered.meter
            )


@dataclass(frozen=True, slots=True, eq=False, init=False)
class BoundPattern:
    """Answer every match view from one eager preparation on one graph.

    With a valid raw ordering, each view equals the corresponding per-call
    :class:`CompiledPattern` method. Predicates bind before scopes are read, but
    each view is checked only when called. A prebuilt :class:`BoundOrdering`
    instead supplies scopes shared across patterns; its construction validates
    ordering before this handle binds predicates or a view validates its
    operation. This handle holds its deeply immutable graph for its own lifetime.
    """

    compiled: CompiledPattern
    graph: Graph
    ordering: BoundOrdering
    _truth: _TruthTable = field(repr=False, compare=False)

    def __init__(
        self,
        compiled: CompiledPattern,
        graph: Graph,
        ordering: Ordering | BoundOrdering,
    ) -> None:
        bound = tuple(
            compile_predicate(predicate).bind(graph)
            for predicate in compiled.predicates
        )
        if isinstance(ordering, BoundOrdering):
            if ordering.graph is not graph:
                raise Refusal(
                    RefusalStage.SEMANTICS,
                    "pattern binding requires an ordering bound to the same graph",
                )
            prepared = ordering
        else:
            prepared = BoundOrdering._from_scopes(
                graph, ordering, compiled._scopes(graph, ordering)
            )
        object.__setattr__(self, "compiled", compiled)
        object.__setattr__(self, "graph", graph)
        object.__setattr__(self, "ordering", prepared)
        object.__setattr__(
            self,
            "_truth",
            _truth_table(
                bound,
                prepared._scopes,
                _indexed_atoms(compiled._gates),
            ),
        )

    @overload
    def exists(
        self,
        *,
        open_right: Literal[False] = False,
        budget: WorkBudget | WorkMeter | None = None,
    ) -> bool:
        """Return whether a closed scope contains an accepting span."""
        ...

    @overload
    def exists(
        self,
        *,
        open_right: Literal[True],
        budget: WorkBudget | WorkMeter | None = None,
    ) -> OpenPatternResult[bool]:
        """Return settled existence and open-right watermarks."""
        ...

    def exists(
        self, *, open_right: bool = False, budget: WorkBudget | WorkMeter | None = None
    ) -> bool | OpenPatternResult[bool]:
        """Return whether any scope contains an accepting span."""
        self.compiled._check(_PatternOperation.EXISTS)
        with _metered(budget, "pattern.exists") as metered:
            return self.compiled._exists_over(
                self.ordering._scopes,
                self._truth,
                open_right=open_right,
                meter=metered.meter,
            )

    @overload
    def focus(
        self,
        *,
        open_right: Literal[False] = False,
        budget: WorkBudget | WorkMeter | None = None,
    ) -> NodeSet:
        """Return focused items for closed scopes."""
        ...

    @overload
    def focus(
        self, *, open_right: Literal[True], budget: WorkBudget | WorkMeter | None = None
    ) -> OpenPatternResult[NodeSet]:
        """Return settled focused items and open-right watermarks."""
        ...

    def focus(
        self, *, open_right: bool = False, budget: WorkBudget | WorkMeter | None = None
    ) -> NodeSet | OpenPatternResult[NodeSet]:
        """Return every item consumed by a focus edge on an accepting run."""
        self.compiled._check(_PatternOperation.FOCUS)
        with _metered(budget, "pattern.focus") as metered:
            return self.compiled._focus_over(
                self.graph,
                self.ordering._scopes,
                self._truth,
                open_right=open_right,
                meter=metered.meter,
            )

    @overload
    def spans(
        self,
        *,
        limit: int | None = None,
        open_right: Literal[False] = False,
        budget: WorkBudget | WorkMeter | None = None,
    ) -> SpanMatches:
        """Return accepting spans for closed scopes."""
        ...

    @overload
    def spans(
        self,
        *,
        limit: int | None = None,
        open_right: Literal[True],
        budget: WorkBudget | WorkMeter | None = None,
    ) -> OpenPatternResult[SpanMatches]:
        """Return settled spans and open-right watermarks."""
        ...

    def spans(
        self,
        *,
        limit: int | None = None,
        open_right: bool = False,
        budget: WorkBudget | WorkMeter | None = None,
    ) -> SpanMatches | OpenPatternResult[SpanMatches]:
        """Return each distinct accepting span once in scope-major order."""
        self.compiled._check(_PatternOperation.SPANS, limit)
        with _metered(budget, "pattern.spans") as metered:
            return self.compiled._spans_over(
                self.ordering._scopes,
                self._truth,
                limit=limit,
                open_right=open_right,
                meter=metered.meter,
                allow_cut=(
                    metered.owns_outermost
                    and budget is not None
                    and not _aggregate_active()
                ),
            )

    @overload
    def count(
        self,
        *,
        open_right: Literal[False] = False,
        budget: WorkBudget | WorkMeter | None = None,
    ) -> int:
        """Count accepting spans in closed scopes."""
        ...

    @overload
    def count(
        self, *, open_right: Literal[True], budget: WorkBudget | WorkMeter | None = None
    ) -> OpenPatternResult[int]:
        """Return settled counts and open-right watermarks."""
        ...

    def count(
        self, *, open_right: bool = False, budget: WorkBudget | WorkMeter | None = None
    ) -> int | OpenPatternResult[int]:
        """Count distinct accepting scope spans, never NFA runs."""
        self.compiled._check(_PatternOperation.COUNT)
        with _metered(budget, "pattern.count") as metered:
            return self.compiled._count_over(
                self.ordering._scopes,
                self._truth,
                open_right=open_right,
                meter=metered.meter,
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
    depth = _pattern_depth(pattern)
    if depth > _MAX_PATTERN_DEPTH:
        raise Refusal(
            RefusalStage.SEMANTICS,
            f"pattern nests deeper than {_MAX_PATTERN_DEPTH} levels",
        )
    positions = _position_count(pattern)
    if positions > MAX_PATTERN_POSITIONS:
        raise Refusal(
            RefusalStage.SEMANTICS,
            f"pattern unrolls to {positions} item positions; limit "
            f"{MAX_PATTERN_POSITIONS}",
        )
    states = _pattern_state_count(pattern)
    if states > MAX_PATTERN_STATES:
        raise Refusal(
            RefusalStage.SEMANTICS,
            f"pattern unrolls to {states} NFA states; limit {MAX_PATTERN_STATES}",
        )
    builder = _NfaBuilder()
    start, accept = builder.build(pattern)
    epsilon = tuple(tuple(edges) for edges in builder.epsilon)
    atom_edges = tuple(tuple(edges) for edges in builder.atoms)
    return CompiledPattern._from_builder(
        pattern,
        start,
        accept,
        epsilon,
        atom_edges,
        tuple(builder.predicates),
        _build_gates(
            accept,
            epsilon,
            atom_edges,
            tuple(builder.predicates),
            () if builder.gate_splits is None else tuple(builder.gate_splits),
        ),
    )


class Extent(StrEnum):
    """State whether an output witness list was truncated."""

    EXHAUSTIVE = "exhaustive"
    CUT_AT_BOUND = "cut-at-bound"
    CUT_AT_BUDGET = "cut-at-budget"


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


def _span_pairs_impl(
    graph: Graph,
    left: Selector,
    right: Selector,
    relation: IntervalRelation,
    offsets: OffsetProfile,
    *,
    limit: int | None = None,
    allow_cut: bool,
) -> SpanPairs:
    """Return related item pairs in left-major declared order."""
    if limit is not None and (type(limit) is not int or limit < 0):
        raise ValueError("span_pairs limit must be a nonnegative integer or None")
    _validate_offset_profile(graph, offsets)
    left_nodes = evaluate_selection(graph, left)
    right_nodes = evaluate_selection(graph, right)
    meter = _active_meter()
    if meter is not None:
        meter.charge(len(left_nodes.nodes) + len(right_nodes.nodes))
    left_spans = _offset_spans(graph, left_nodes.nodes, offsets)
    right_spans = _offset_spans(graph, right_nodes.nodes, offsets)
    pairs: list[tuple[Node, Node]] = []
    try:
        for left_span, right_span in _interval_pairs(left_spans, right_spans, relation):
            if limit is not None and len(pairs) == limit:
                return SpanPairs(tuple(pairs), Extent.CUT_AT_BOUND)
            pairs.append((cast(Node, left_span.node), cast(Node, right_span.node)))
    except BudgetExhausted as error:
        if not (allow_cut and error.exhaustion is Exhaustion.STEPS and pairs):
            raise
        return SpanPairs(tuple(pairs), Extent.CUT_AT_BUDGET)
    return SpanPairs(tuple(pairs), Extent.EXHAUSTIVE)


def span_pairs(
    graph: Graph,
    left: Selector,
    right: Selector,
    relation: IntervalRelation,
    offsets: OffsetProfile,
    *,
    limit: int | None = None,
    budget: WorkBudget | WorkMeter | None = None,
) -> SpanPairs:
    """Return related item pairs in left-major declared order."""
    with _metered(budget, "span_pairs") as metered:
        return _span_pairs_impl(
            graph,
            left,
            right,
            relation,
            offsets,
            limit=limit,
            allow_cut=(
                metered.owns_outermost
                and budget is not None
                and not _aggregate_active()
            ),
        )


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
    """Return any supported sequence ordering as strict JSON data."""
    if isinstance(ordering, TierOrder):
        return {"order": "tier", "tier": ordering.tier.to_data()}
    if isinstance(ordering, ContainerOrder):
        return {
            "order": "containers",
            "relation": ordering.relation.to_data(),
            "containers": _selector_to_data(ordering.containers),
        }
    if isinstance(ordering, DeclaredOrder):
        for role, selector in (
            ("members", ordering.members),
            ("chain", ordering.chain),
        ):
            if selector is not None and _contains_sequence_selector(selector):
                raise Refusal(
                    RefusalStage.SEMANTICS,
                    f"DeclaredOrder {role} may not contain SequenceSelector",
                )
        result: dict[str, JsonValue] = {
            "order": "declared",
            "successor": ordering.successor.to_data(),
            "members": _selector_to_data(ordering.members),
        }
        if ordering.chain is not None:
            result["chain"] = _selector_to_data(ordering.chain)
        if ordering.open_left:
            result["open_left"] = True
        return result
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
    if kind == "declared":
        optional = {name for name in ("open_left", "chain") if name in node}
        _refuse_field_set(
            node.keys(),
            {"order", "successor", "members"} | optional,
            {"order", "successor", "members"},
            path,
        )
        open_left = node.get("open_left", False)
        if not isinstance(open_left, bool):
            raise Refusal(RefusalStage.VALUE, f"{path}.open_left must be a boolean")
        members = _decode_selector(node["members"], f"{path}.members")
        chain = (
            _decode_selector(node["chain"], f"{path}.chain")
            if "chain" in node
            else None
        )
        for role, selector in (("members", members), ("chain", chain)):
            if selector is not None and _contains_sequence_selector(selector):
                raise Refusal(
                    RefusalStage.SEMANTICS,
                    f"DeclaredOrder {role} may not contain SequenceSelector",
                )
        return DeclaredOrder(
            _decode_qname(node["successor"], f"{path}.successor"),
            members,
            open_left,
            chain,
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
        BoundariesSelector,
        BoundaryPathSelector,
        DifferenceSelector,
        IntersectionSelector,
        ItemPathSelector,
        ItemsSelector,
        SequenceSelector,
        UnionSelector,
        WhereSelector,
    )

    if isinstance(selector, ItemsSelector):
        return {"select": "items", "tier": selector.tier.to_data()}
    if isinstance(selector, BoundariesSelector):
        return {"select": "boundaries", "tier": selector.tier.to_data()}
    if isinstance(selector, ItemPathSelector):
        return {"select": "item", "path": selector.path}
    if isinstance(selector, BoundaryPathSelector):
        return {"select": "boundary", "path": selector.path}
    if isinstance(selector, SequenceSelector):
        raise Refusal(
            RefusalStage.SEMANTICS,
            "a nested SequenceSelector has no selector JSON encoding",
        )
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
    max_steps: int | None = None

    def evaluate(
        self, graph: Graph, *, budget: WorkBudget | WorkMeter | None = None
    ) -> dict[str, JsonValue]:
        """Evaluate this decoded request against *graph*."""
        compiled = compile_pattern(self.pattern)
        if self.operation == "exists":
            return {"exists": compiled.exists(graph, self.ordering, budget=budget)}
        if self.operation == "focus":
            focused = compiled.focus(graph, self.ordering, budget=budget)
            return {"nodes": focused.to_data()}
        if self.operation == "count":
            return {"count": compiled.count(graph, self.ordering, budget=budget)}
        spans = compiled.spans(graph, self.ordering, limit=self.limit, budget=budget)
        return spans.to_data()


@dataclass(frozen=True, slots=True)
class _UnitValuation:
    """Supply unit values while a CLI lattice request compiles only topology."""

    tiers: tuple[QualifiedName, ...]
    name: str = "lattice-units"
    attribute: QualifiedName = QualifiedName("urn:tiergraph:lattice-match", "unit")

    def declaration_type(self, graph: Graph) -> XsdType:
        """Return the integer carrier used for unit path counting."""
        del graph
        return XsdType.INTEGER

    def read(self, graph: Graph, reference: ItemRef) -> object:
        """Return one for every item in the requested lattice topology."""
        del graph, reference
        return 1


@dataclass(frozen=True, slots=True)
class _LatticeRequest:
    transition: QualifiedName
    roots: tuple[ItemRef, ...]
    emission: QualifiedName
    pattern: Pattern
    policy: str
    max_states: int | None
    max_steps: int | None = None

    def evaluate(
        self, graph: Graph, *, budget: WorkBudget | WorkMeter | None = None
    ) -> dict[str, JsonValue]:
        """Build the requested unit topology and report its three decisions."""
        from tiergraph.fold import (  # noqa: PLC0415 -- optional lattice surface
            AttributeValuation,
            ChildCombination,
            FoldDeclaration,
            FoldTransition,
        )
        from tiergraph.pathoutput import (  # noqa: PLC0415 -- match imports this AST
            Determinize,
            Emissions,
            Unambiguous,
            match_lattice,
        )
        from tiergraph.pathplan import PathPlan  # noqa: PLC0415 -- optional surface
        from tiergraph.semiring import COUNTING  # noqa: PLC0415 -- optional surface

        valuation = cast(
            AttributeValuation,
            _UnitValuation(tuple(tier.declaration.name for tier in graph.tiers)),
        )
        declaration = FoldDeclaration(
            "lattice-match",
            graph,
            valuation,
            COUNTING,
            lambda _value, _label: 1,
            (FoldTransition(self.transition, ChildCombination.OR),),
            roots=self.roots,
        )
        plan = PathPlan.prepare(declaration)
        lattice = match_lattice(
            Emissions.from_attribute(plan, self.emission),
            compile_pattern(self.pattern),
        )
        policy = (
            Unambiguous()
            if self.policy == "unambiguous"
            else Determinize(cast(int, self.max_states))
        )
        shared = WorkMeter(budget) if isinstance(budget, WorkBudget) else budget
        return {
            "exists": lattice.exists(budget=shared),
            "count": lattice.count(policy, budget=shared),
            "all_paths": lattice.all_paths(policy, budget=shared),
        }


def _match_request_loads(
    source: str | bytes,
) -> _MatchRequest | _PairsRequest | _LatticeRequest:
    value = cast(JsonValue, _parsed_json(source))
    node = cast(dict[str, JsonValue], _object(value, "$"))
    if "match" not in node:
        _refuse_field_set(node.keys(), {"match"}, {"match"}, "$")
    operation = _string(node["match"], "$.match")
    if operation == "pairs":
        return _pairs_request_from_node(node)
    if operation == "lattice":
        return _lattice_request_from_node(node)
    if operation not in {"exists", "focus", "spans", "count"}:
        raise Refusal(
            RefusalStage.DISCRIMINATOR,
            f"$.match has unknown match operation {operation!r}",
        )
    allowed = {"match", "ordering", "pattern", "max_steps"}
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
        _request_max_steps(node),
    )


def _evaluate_match_request(
    graph: Graph, source: str | bytes, *, max_steps: int | None = None
) -> dict[str, JsonValue]:
    """Decode and evaluate one CLI match request."""
    request = _match_request_loads(source)
    embedded = request.max_steps
    if max_steps is not None and embedded is not None:
        raise Refusal(
            RefusalStage.VALUE,
            "--max-steps and $.max_steps may not both be set",
        )
    steps = embedded if max_steps is None else max_steps
    budget = None if steps is None else WorkBudget(steps=steps)
    if isinstance(request, _PairsRequest):
        return span_pairs(
            graph,
            request.left,
            request.right,
            request.relation,
            request.offsets,
            limit=request.limit,
            budget=budget,
        ).to_data()
    return request.evaluate(graph, budget=budget)


def _lattice_request_from_node(node: dict[str, JsonValue]) -> _LatticeRequest:
    allowed = {
        "match",
        "transitions",
        "roots",
        "emission",
        "pattern",
        "policy",
        "max_steps",
    }
    _refuse_field_set(node.keys(), allowed, allowed - {"max_steps"}, "$")
    transitions = node["transitions"]
    if not isinstance(transitions, list):
        raise Refusal(RefusalStage.SHAPE, "$.transitions must be an array")
    if len(transitions) != 1:
        raise Refusal(
            RefusalStage.VALUE,
            "$.transitions must name exactly one path relation",
        )
    roots = node["roots"]
    if not isinstance(roots, list):
        raise Refusal(RefusalStage.SHAPE, "$.roots must be an array")
    policy_node = cast(dict[str, JsonValue], _object(node["policy"], "$.policy"))
    if "policy" not in policy_node:
        _refuse_field_set(policy_node.keys(), {"policy"}, {"policy"}, "$.policy")
    policy = _string(policy_node["policy"], "$.policy.policy")
    if policy == "unambiguous":
        _refuse_field_set(policy_node.keys(), {"policy"}, {"policy"}, "$.policy")
        max_states = None
    elif policy == "determinize":
        _refuse_field_set(
            policy_node.keys(),
            {"policy", "max_states"},
            {"policy", "max_states"},
            "$.policy",
        )
        max_states = _nonnegative_integer(
            policy_node["max_states"], "$.policy.max_states"
        )
        if max_states == 0:
            raise Refusal(
                RefusalStage.VALUE,
                "$.policy.max_states must be a positive integer",
            )
    else:
        raise Refusal(
            RefusalStage.DISCRIMINATOR,
            f"$.policy.policy has unknown ambiguity policy {policy!r}",
        )
    return _LatticeRequest(
        _decode_qname(transitions[0], "$.transitions[0]"),
        tuple(
            _decode_item_ref(root, f"$.roots[{index}]")
            for index, root in enumerate(roots)
        ),
        _decode_qname(node["emission"], "$.emission"),
        _decode_pattern(node["pattern"], "$.pattern"),
        policy,
        max_states,
        _request_max_steps(node),
    )


@dataclass(frozen=True, slots=True)
class _PairsRequest:
    left: Selector
    right: Selector
    relation: IntervalRelation
    offsets: OffsetProfile
    limit: int | None = None
    max_steps: int | None = None
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
        if self.max_steps is not None:
            result["max_steps"] = self.max_steps
        return result


def _pairs_request_loads(source: str | bytes) -> _PairsRequest:
    value = cast(JsonValue, _parsed_json(source))
    node = cast(dict[str, JsonValue], _object(value, "$"))
    return _pairs_request_from_node(node)


def _pairs_request_from_node(node: dict[str, JsonValue]) -> _PairsRequest:
    allowed = {
        "match",
        "left",
        "right",
        "relation",
        "offsets",
        "limit",
        "max_steps",
    }
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
        _request_max_steps(node),
        deepcopy(node["left"]),
        deepcopy(node["right"]),
    )


def _request_max_steps(node: dict[str, JsonValue]) -> int | None:
    """Decode the optional bounded positive step count shared by requests."""
    if "max_steps" not in node:
        return None
    value = node["max_steps"]
    if type(value) is not int or value <= 0:
        raise Refusal(
            RefusalStage.VALUE,
            "$.max_steps must be a positive integer",
        )
    if value > _MAX_USER_STEPS:
        raise Refusal(
            RefusalStage.VALUE,
            f"$.max_steps must be no greater than {_MAX_USER_STEPS}",
        )
    return value


from tiergraph.match_text import (  # noqa: E402 -- text layer uses this AST
    format_pattern,
    parse_pattern,
    parse_pattern_at,
)

__all__ = [
    "MAX_PATTERN_POSITIONS",
    "MAX_PATTERN_STATES",
    "AdjacentRuns",
    "AltPattern",
    "AtomPattern",
    "BoundOrdering",
    "BoundPattern",
    "CompiledPattern",
    "ContainerOrder",
    "DeclaredOrder",
    "EndPattern",
    "Extent",
    "FocusPattern",
    "OpenPatternResult",
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
    "format_pattern",
    "ordering_to_data",
    "parse_pattern",
    "parse_pattern_at",
    "pattern_loads",
    "pattern_to_data",
    "span_pairs",
]
