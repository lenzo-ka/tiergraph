"""Pool path mass by the complete string emitted along each path.

An emission is a tuple of strings attached to an item. A path emits their
concatenation in path order; an empty tuple is the identity. ``OutputPlan``
builds the reachable product of a ``PathPlan`` with a caller-supplied candidate
trie and an absorbing state for every other output. It can therefore compute a
finite candidate set and the residual in one pass, or condition the product on
one candidate and pool item marginals back onto the base plan.

The mass of a given string, a finite candidate set with its residual, and
conditioned item marginals are exact and tractable. Finding the pooled argmax
over all strings is the consensus-string problem and is NP-hard even for
acyclic hidden Markov models. Full determinization under the log-probability
semiring can grow exponentially. Caller-supplied candidates and the residual
certificate provide a bounded alternative.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from itertools import product
from typing import cast

from tiergraph.budget import (
    WorkBudget,
    WorkMeter,
    _active_meter,
    _aggregating,
    _Meter,
    _metered,
)
from tiergraph.core import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    Graph,
    Item,
    ItemRef,
    JsonType,
    NamespaceDeclaration,
    QualifiedName,
    Refusal,
    RefusalStage,
    RelationInstance,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    XsdType,
)
from tiergraph.fold import (
    AttributeValuation,
    ChildCombination,
    FoldCost,
    FoldDeclaration,
    FoldTransition,
)
from tiergraph.match import (
    CompiledPattern,
    EndPattern,
    FocusPattern,
    Pattern,
    StartPattern,
    _children,
)
from tiergraph.pathplan import PathPlan
from tiergraph.predicate import (
    And,
    Bare,
    Cell,
    Compare,
    Elements,
    Equals,
    Has,
    Matches,
    Not,
    Or,
    Predicate,
    Related,
    Spans,
    _compile_regex,
    _literal_text,
)
from tiergraph.selection import Node, NodeKind, NodeSet
from tiergraph.semiring import BOOLEAN, COUNTING, LOG_PROBABILITY, Semiring

_NAMESPACE = "urn:tiergraph:path-output"
_TIER = QualifiedName(_NAMESPACE, "items")
_TYPE = QualifiedName(_NAMESPACE, "item")
_MEMBERSHIP = QualifiedName(_NAMESPACE, "membership")
_NEXT = QualifiedName(_NAMESPACE, "next")
_SOURCE = QualifiedName(_NAMESPACE, "source")
_OFF = -1
_START_POSITION = -1
_MAX_AMBIGUITY_PAIRS = 100_000
_OTHER = object()


@dataclass(frozen=True, slots=True)
class Emissions[Value]:
    """Per-item token tuples bound to one path plan's label inventory."""

    plan: PathPlan[Value]
    per_item: tuple[tuple[str, ...], ...]

    def __post_init__(self) -> None:
        """Validate the positional emission inventory at construction."""
        if len(self.per_item) != len(self.plan.items):
            raise ValueError(
                f"emissions for path plan {self.plan.declaration.name!r} require "
                f"{len(self.plan.items)} item entries and were given "
                f"{len(self.per_item)}"
            )
        for index, tokens in enumerate(self.per_item):
            label = self.plan.labels[index]
            if type(tokens) is not tuple:
                raise TypeError(
                    f"emission for item {index} label {label!r} must be a tuple "
                    "of strings"
                )
            for token in tokens:
                if type(token) is not str:
                    raise TypeError(
                        f"emission for item {index} label {label!r} contains symbol "
                        f"{token!r}; every symbol must be a string"
                    )

    @classmethod
    def bind(
        cls, plan: PathPlan[Value], by_label: Mapping[str, Sequence[str]]
    ) -> Emissions[Value]:
        """Bind emissions by item label, leaving unlisted items non-emitting."""
        for label in by_label:
            if type(label) is not str:
                raise TypeError(
                    f"emission label {label!r} must be a string naming a plan item"
                )
        known = set(plan.labels)
        unknown = sorted(set(by_label) - known)
        if unknown:
            raise ValueError(
                f"emission label {unknown[0]!r} is not an item label in path plan "
                f"{plan.declaration.name!r}"
            )
        bound: list[tuple[str, ...]] = []
        for label in plan.labels:
            raw = by_label.get(label, ())
            if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
                raise TypeError(
                    f"emission for item label {label!r} must be a sequence of strings"
                )
            tokens = tuple(raw)
            for token in tokens:
                if type(token) is not str:
                    raise TypeError(
                        f"emission for item label {label!r} contains symbol "
                        f"{token!r}; every symbol must be a string"
                    )
            bound.append(tokens)
        return cls(plan, tuple(bound))

    @classmethod
    def from_attribute(
        cls, plan: PathPlan[Value], attribute: QualifiedName
    ) -> Emissions[Value]:
        """Read string-token tuples from one item attribute, with absence silent."""
        graph = plan.declaration.graph
        declaration = next(
            (
                candidate
                for candidate in graph.attribute_declarations
                if candidate.name == attribute
            ),
            None,
        )
        display = _display_name(graph, attribute)
        if declaration is None:
            raise Refusal(
                RefusalStage.REFERENCE,
                f"from_attribute names undeclared attribute {display}",
            )
        if declaration.domain is not AttributeDomain.ITEM:
            raise Refusal(
                RefusalStage.SEMANTICS,
                f"from_attribute needs an item attribute; {display} has domain "
                f"{declaration.domain.value!r}",
            )
        if declaration.value_type not in {XsdType.STRING, JsonType.JSON}:
            kind = (
                f"xsd:{declaration.value_type.value}"
                if isinstance(declaration.value_type, XsdType)
                else declaration.value_type.value
            )
            raise Refusal(
                RefusalStage.SEMANTICS,
                f"from_attribute needs an xsd:string or JSON attribute; "
                f"{display} is {kind}",
            )
        per_item: list[tuple[str, ...]] = []
        for index, reference in enumerate(plan.items):
            item = _graph_item(graph, reference)
            stored = next(
                (
                    candidate
                    for candidate in item.attributes
                    if candidate.name == attribute
                ),
                None,
            )
            if stored is None:
                per_item.append(())
                continue
            if isinstance(stored, AttributeValue):
                per_item.append((stored.lexical,))
                continue
            value = stored.to_value()
            if type(value) is not list:
                raise Refusal(
                    RefusalStage.SEMANTICS,
                    f"from_attribute needs a JSON array of strings at {display}; "
                    f"item {plan.labels[index]!r} stores a JSON {_json_kind(value)}",
                )
            tokens = cast(list[object], value)
            bad = next(
                (
                    position
                    for position, token in enumerate(tokens)
                    if type(token) is not str
                ),
                None,
            )
            if bad is not None:
                raise Refusal(
                    RefusalStage.SEMANTICS,
                    f"from_attribute needs a JSON array of strings at {display}; "
                    f"item {plan.labels[index]!r} stores a JSON array with "
                    f"{_indefinite(_json_kind(tokens[bad]))} at index {bad}",
                )
            per_item.append(tuple(cast(list[str], tokens)))
        return cls(plan, tuple(per_item))


@dataclass(frozen=True, slots=True)
class Unambiguous:
    """Require the pattern to have at most one accepting run per token string."""


@dataclass(frozen=True, slots=True)
class Determinize:
    """Count with lazy subset construction up to a declared state bound."""

    max_states: int

    def __post_init__(self) -> None:
        if type(self.max_states) is not int or self.max_states <= 0:
            raise ValueError("Determinize max_states must be a positive integer")


type AmbiguityPolicy = Unambiguous | Determinize


@dataclass(frozen=True, slots=True)
class _Position:
    """Name one atom edge in the epsilon-eliminated position automaton."""

    source: int
    edge_index: int


@dataclass(frozen=True, slots=True)
class _Product[State, Value]:
    """Carry one reachable path-plan and automaton product."""

    algebra: Semiring[Value]
    pairs: tuple[tuple[int, State], ...]
    edges: tuple[tuple[int, ...], ...]
    roots: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _BooleanProduct:
    """Carry the reachable state-level NFA by lattice product."""

    pairs: tuple[tuple[int, int], ...]
    edges: tuple[tuple[int, ...], ...]
    accepting: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _SubsetProduct:
    """Carry one reachable deterministic-subset by lattice product."""

    pairs: tuple[tuple[int, frozenset[int]], ...]
    edges: tuple[tuple[int, ...], ...]
    roots: tuple[int, ...]


def _product[State: Hashable, Value](
    plan: PathPlan[object],
    emissions: Emissions[object],
    algebra: Semiring[Value],
    start: State,
    advance: Callable[[State, tuple[str, ...]], tuple[State, ...]],
    *,
    observe: Callable[[State], None] | None = None,
) -> _Product[State, Value]:
    """Build the reachable product under the caller's explicit algebra."""
    meter = _active_meter()
    pairs: list[tuple[int, State]] = []
    positions: dict[tuple[int, State], int] = {}
    edges: list[list[int]] = []
    pending: deque[int] = deque()

    def seat(pair: tuple[int, State]) -> int:
        """Assign a stable product position and queue a newly seen pair."""
        found = positions.get(pair)
        if found is not None:
            return found
        if meter is not None:
            meter.charge(1)
        found = len(pairs)
        positions[pair] = found
        pairs.append(pair)
        edges.append([])
        pending.append(found)
        return found

    if observe is not None:
        observe(start)
    roots: list[int] = []
    for root in plan.roots:
        for state in advance(start, emissions.per_item[root]):
            if observe is not None:
                observe(state)
            roots.append(seat((root, state)))
    while pending:
        parent_index = pending.popleft()
        parent, state = pairs[parent_index]
        for child in plan.children[parent]:
            if meter is not None:
                meter.charge(1)
            for target in advance(state, emissions.per_item[child]):
                if observe is not None:
                    observe(target)
                edges[parent_index].append(seat((child, target)))
    return _Product(
        algebra,
        tuple(pairs),
        tuple(tuple(children) for children in edges),
        tuple(roots),
    )


@dataclass(frozen=True, slots=True)
class LatticeMatch:
    """Match one compiled regular pattern against every root-to-sink path."""

    emissions: Emissions[object]
    pattern: CompiledPattern
    _boolean_cache: list[_BooleanProduct] = field(
        default_factory=list, repr=False, compare=False
    )
    _ambiguity_cache: list[bool] = field(
        default_factory=list, repr=False, compare=False
    )
    _subset_cache: dict[int, _SubsetProduct] = field(
        default_factory=dict, repr=False, compare=False
    )

    def exists(self, *, budget: WorkBudget | WorkMeter | None = None) -> bool:
        """Return whether some complete lattice path matches the whole pattern."""
        if budget is None and _active_meter() is None:
            return bool(self._boolean().accepting)
        with _metered(budget, "lattice.exists"):
            return bool(self._boolean().accepting)

    def on_accepting_path(
        self, *, budget: WorkBudget | WorkMeter | None = None
    ) -> NodeSet:
        """Return every base item lying on some accepting complete path."""
        if budget is None and _active_meter() is None:
            return self._on_accepting_path()
        with _metered(budget, "lattice.on_accepting_path"):
            return self._on_accepting_path()

    def _on_accepting_path(self) -> NodeSet:
        """Implement accepting-path projection under an ambient meter."""
        meter = _active_meter()
        product_graph = self._boolean()
        reverse: list[list[int]] = [[] for _ in product_graph.pairs]
        for parent, children in enumerate(product_graph.edges):
            for child in children:
                if meter is not None:
                    meter.charge(1)
                reverse[child].append(parent)
        accepted = set(product_graph.accepting)
        pending = list(product_graph.accepting)
        while pending:
            child = pending.pop()
            for parent in reverse[child]:
                if meter is not None:
                    meter.charge(1)
                if parent not in accepted:
                    accepted.add(parent)
                    pending.append(parent)
        plan = self.emissions.plan
        nodes = tuple(
            Node(NodeKind.ITEM, plan.items[item])
            for product_index, (item, _state) in enumerate(product_graph.pairs)
            if product_index in accepted
        )
        return NodeSet(plan.declaration.graph, nodes)

    def count(
        self, policy: AmbiguityPolicy, *, budget: WorkBudget | WorkMeter | None = None
    ) -> int:
        """Count accepting lattice paths exactly under the declared policy."""
        if not isinstance(policy, (Unambiguous, Determinize)):
            raise TypeError(
                "lattice count needs Unambiguous() or Determinize(max_states)"
            )
        if budget is None and _active_meter() is None:
            return self._count(policy)
        with _metered(budget, "lattice.count"):
            return self._count(policy)

    def _count(self, policy: AmbiguityPolicy) -> int:
        """Count under one validated policy."""
        if isinstance(policy, Unambiguous):
            self._require_unambiguous()
            return self._run_count()
        return self._subset_count(self._subset(policy.max_states))

    def all_paths(
        self, policy: AmbiguityPolicy, *, budget: WorkBudget | WorkMeter | None = None
    ) -> bool:
        """Return whether every complete lattice path matches the pattern."""
        if not isinstance(policy, (Unambiguous, Determinize)):
            raise TypeError(
                "lattice all_paths needs Unambiguous() or Determinize(max_states)"
            )
        if budget is None and _active_meter() is None:
            return self._all_paths(policy)
        with _metered(budget, "lattice.all_paths"):
            return self._all_paths(policy)

    def _all_paths(self, policy: AmbiguityPolicy) -> bool:
        """Apply one validated universal-path policy."""
        if isinstance(policy, Unambiguous):
            return self.count(policy) == _path_count(self.emissions.plan)
        product_graph = self._subset(policy.max_states)
        accept = self.pattern.accept
        return all(
            accept in subset
            for item, subset in product_graph.pairs
            if not self.emissions.plan.children[item]
        )

    def _boolean(self) -> _BooleanProduct:
        meter = _active_meter()
        budgeted = meter is not None
        if not budgeted and self._boolean_cache:
            return self._boolean_cache[0]
        plan = self.emissions.plan
        product_graph = _product(
            plan,
            self.emissions,
            BOOLEAN,
            self.pattern.start,
            lambda state, tokens: tuple(
                _advance_states(self.pattern, frozenset((state,)), tokens, meter=meter)
            ),
        )
        accepting = tuple(
            index
            for index, (item, state) in enumerate(product_graph.pairs)
            if not plan.children[item] and state == self.pattern.accept
        )
        result = _BooleanProduct(product_graph.pairs, product_graph.edges, accepting)
        if not budgeted:
            self._boolean_cache.append(result)
        return result

    def _require_unambiguous(self) -> None:
        meter = _active_meter()
        budgeted = meter is not None
        if not budgeted and self._ambiguity_cache:
            return
        witness = _ambiguity_witness(self.pattern, meter=meter)
        if witness is not None:
            rendered = " ".join(
                json.dumps(token, ensure_ascii=False) for token in witness
            )
            raise Refusal(
                RefusalStage.SEMANTICS,
                f"pattern is ambiguous: the tokens {rendered} have two accepting "
                "runs; count under Determinize, or rewrite the pattern so each path "
                "has one run, as in {.!=zero}* {.=zero} .*",
            )
        if not budgeted:
            self._ambiguity_cache.append(True)

    def _run_count(self) -> int:
        meter = _active_meter()
        plan = self.emissions.plan
        reached: list[dict[int, int]] = [dict() for _ in plan.items]
        for root in plan.roots:
            advanced = _advance_counts(
                self.pattern,
                {self.pattern.start: 1},
                self.emissions.per_item[root],
                meter=meter,
            )
            _merge_counts(reached[root], advanced, meter=meter)
        total = 0
        for item in reversed(plan.order):
            current = reached[item]
            if not current:
                continue
            if not plan.children[item]:
                total += sum(
                    count
                    for state, count in current.items()
                    if self.pattern.accept
                    in _epsilon_closure(self.pattern, frozenset((state,)), meter=meter)
                )
                continue
            for child in plan.children[item]:
                _merge_counts(
                    reached[child],
                    _advance_counts(
                        self.pattern,
                        current,
                        self.emissions.per_item[child],
                        meter=meter,
                    ),
                    meter=meter,
                )
        return total

    def _subset(self, max_states: int) -> _SubsetProduct:
        meter = _active_meter()
        budgeted = meter is not None
        cached = None if budgeted else self._subset_cache.get(max_states)
        if cached is not None:
            return cached
        plan = self.emissions.plan
        start = _epsilon_closure(
            self.pattern, frozenset((self.pattern.start,)), meter=meter
        )
        subsets: set[frozenset[int]] = set()

        def register(subset: frozenset[int]) -> None:
            """Count a newly reached subset or refuse beyond the bound."""
            if subset in subsets:
                return
            if meter is not None:
                meter.charge(1)
            subsets.add(subset)
            if len(subsets) > max_states:
                raise Refusal(
                    RefusalStage.SEMANTICS,
                    f"counting needs more than {max_states} determinized states "
                    f"on this lattice (reached {len(subsets)}); no count is reported",
                )

        product_graph = _product(
            plan,
            self.emissions,
            COUNTING,
            start,
            lambda subset, tokens: (
                _advance_states(self.pattern, subset, tokens, meter=meter),
            ),
            observe=register,
        )
        result = _SubsetProduct(
            product_graph.pairs, product_graph.edges, product_graph.roots
        )
        if not budgeted:
            self._subset_cache[max_states] = result
        return result

    def _subset_count(self, product_graph: _SubsetProduct) -> int:
        plan = self.emissions.plan
        meter = _active_meter()
        position_order = {item: order for order, item in enumerate(plan.order)}
        totals = [0] * len(product_graph.pairs)
        for product_index in sorted(
            range(len(product_graph.pairs)),
            key=lambda index: position_order[product_graph.pairs[index][0]],
        ):
            item, subset = product_graph.pairs[product_index]
            children = product_graph.edges[product_index]
            if meter is not None:
                meter.charge(len(children))
            totals[product_index] = (
                sum(totals[child] for child in children)
                if children
                else int(self.pattern.accept in subset and not plan.children[item])
            )
        if meter is not None:
            meter.charge(len(product_graph.roots))
        return sum(totals[root] for root in product_graph.roots)


def match_lattice[Value](
    emissions: Emissions[Value], pattern: CompiledPattern
) -> LatticeMatch:
    """Bind a compiled pattern to one emitted finite path DAG."""
    if not isinstance(emissions, Emissions):
        raise TypeError("match_lattice emissions must be an Emissions value")
    if not isinstance(pattern, CompiledPattern):
        raise TypeError("match_lattice pattern must be a CompiledPattern")
    _validate_lattice_pattern(pattern.pattern)
    for predicate in pattern.predicates:
        _validate_token_predicate(predicate)
    return LatticeMatch(cast(Emissions[object], emissions), pattern)


def _validate_lattice_pattern(pattern: Pattern) -> None:
    if isinstance(pattern, FocusPattern):
        raise Refusal(
            RefusalStage.SEMANTICS,
            "a lattice match reports paths, not a focus",
        )
    if isinstance(pattern, StartPattern | EndPattern):
        symbol = "^" if isinstance(pattern, StartPattern) else "$"
        raise Refusal(
            RefusalStage.SEMANTICS,
            f"a lattice pattern is matched against the whole path; {symbol!r} and "
            f"{'$' if symbol == '^' else '^'!r} add nothing",
        )
    for child in _children(pattern):
        _validate_lattice_pattern(child)


def _validate_token_predicate(predicate: Predicate) -> None:
    if isinstance(predicate, And | Or):
        for argument in predicate.args:
            _validate_token_predicate(argument)
        return
    if isinstance(predicate, Not):
        _validate_token_predicate(predicate.arg)
        return
    if isinstance(predicate, Has):
        raise Refusal(
            RefusalStage.SEMANTICS,
            "a lattice token is always present, so "
            f"{predicate.alias!r} (the missing-cell alias) has no meaning on it; "
            f"write .={json.dumps(predicate.alias)} for the token {predicate.alias}",
        )
    if isinstance(predicate, Related | Spans | Elements):
        raise Refusal(
            RefusalStage.SEMANTICS,
            "a lattice atom tests the token '.'; "
            f"{type(predicate).__name__} has no meaning on a token",
        )
    operand = predicate.operand
    if isinstance(operand, Cell):
        raise Refusal(
            RefusalStage.SEMANTICS,
            "a lattice atom tests the token '.', not a cell",
        )
    if operand.pointer:
        raise Refusal(
            RefusalStage.SEMANTICS,
            "a lattice atom tests the token '.', not a pointer beneath it",
        )
    if isinstance(predicate, Compare):
        raise Refusal(
            RefusalStage.SEMANTICS,
            "a lattice token is a string, so ordered comparison has no meaning on it",
        )
    if isinstance(predicate, Equals):
        for literal in predicate.values:
            if type(literal) is str or isinstance(literal, Bare):
                continue
            raise Refusal(
                RefusalStage.SEMANTICS,
                f"a lattice token is a string, so the literal "
                f"{_literal_text(literal)} ({type(literal).__name__}) can never "
                f"equal it; write .={json.dumps(_literal_text(literal))}",
            )


def _token_holds(predicate: Predicate, token: object) -> bool:
    if isinstance(predicate, And):
        return all(_token_holds(argument, token) for argument in predicate.args)
    if isinstance(predicate, Or):
        return any(_token_holds(argument, token) for argument in predicate.args)
    if isinstance(predicate, Not):
        return not _token_holds(predicate.arg, token)
    if isinstance(predicate, Equals):
        return any(
            token == (literal.text if isinstance(literal, Bare) else literal)
            for literal in predicate.values
        )
    assert isinstance(predicate, Matches)
    return token is not _OTHER and _compile_regex(predicate.regex).fullmatch(
        cast(str, token)
    )


def _epsilon_closure(
    pattern: CompiledPattern,
    states: frozenset[int],
    *,
    meter: _Meter | None = None,
) -> frozenset[int]:
    result = set(states)
    pending = list(states)
    while pending:
        state = pending.pop()
        for edge in pattern.epsilon[state]:
            if edge.target not in result:
                result.add(edge.target)
                pending.append(edge.target)
    if meter is not None:
        meter.charge(len(result))
    return frozenset(result)


def _advance_states(
    pattern: CompiledPattern,
    states: frozenset[int],
    tokens: tuple[str, ...],
    *,
    meter: _Meter | None = None,
) -> frozenset[int]:
    active = _epsilon_closure(pattern, states, meter=meter)
    for token in tokens:
        if meter is not None:
            meter.charge(len(active))
        active = _epsilon_closure(
            pattern,
            frozenset(
                edge.target
                for state in active
                for edge in pattern.atom_edges[state]
                if _token_holds(pattern.predicates[edge.atom], token)
            ),
            meter=meter,
        )
    return active


def _advance_counts(
    pattern: CompiledPattern,
    counts: Mapping[int, int],
    tokens: tuple[str, ...],
    *,
    meter: _Meter | None = None,
) -> dict[int, int]:
    active = dict(counts)
    for token in tokens:
        following: dict[int, int] = {}
        for state, count in active.items():
            for source in _epsilon_closure(pattern, frozenset((state,)), meter=meter):
                for edge in pattern.atom_edges[source]:
                    if _token_holds(pattern.predicates[edge.atom], token):
                        following[edge.target] = following.get(edge.target, 0) + count
        active = following
    return active


def _merge_counts(
    target: dict[int, int], source: Mapping[int, int], *, meter: _Meter | None = None
) -> None:
    if meter is not None:
        meter.charge(len(source))
    for state, count in source.items():
        target[state] = target.get(state, 0) + count


def _literal_alphabet(pattern: CompiledPattern) -> tuple[object, ...]:
    literals: set[str] = set()
    pending = list(pattern.predicates)
    while pending:
        predicate = pending.pop()
        if isinstance(predicate, And | Or):
            pending.extend(predicate.args)
        elif isinstance(predicate, Not):
            pending.append(predicate.arg)
        elif isinstance(predicate, Equals):
            literals.update(
                literal.text if isinstance(literal, Bare) else cast(str, literal)
                for literal in predicate.values
            )
    return (*sorted(literals), _OTHER)


def _contains_matches(predicate: Predicate) -> bool:
    if isinstance(predicate, Matches):
        return True
    if isinstance(predicate, And | Or):
        return any(_contains_matches(argument) for argument in predicate.args)
    if isinstance(predicate, Not):
        return _contains_matches(predicate.arg)
    return False


def _minterm_holds(predicate: Predicate, symbol: object) -> bool:
    return True if _contains_matches(predicate) else _token_holds(predicate, symbol)


def _ambiguity_witness(
    pattern: CompiledPattern, *, meter: _Meter | None = None
) -> tuple[str, ...] | None:
    atom_edge_count = sum(len(edges) for edges in pattern.atom_edges)
    if meter is not None:
        meter.charge(atom_edge_count)
    positions = tuple(
        _Position(source, edge_index)
        for source, edges in enumerate(pattern.atom_edges)
        for edge_index, _edge in enumerate(edges)
    )
    if meter is not None:
        meter.charge(len(positions))
    position_index = {position: index for index, position in enumerate(positions)}

    def choices(position: int, symbol: object) -> tuple[int, ...]:
        """Return the atom positions reachable by consuming one minterm."""
        state = (
            pattern.start
            if position == _START_POSITION
            else pattern.atom_edges[positions[position].source][
                positions[position].edge_index
            ].target
        )
        return tuple(
            position_index[_Position(source, edge_index)]
            for source in _epsilon_closure(pattern, frozenset((state,)), meter=meter)
            for edge_index, edge in enumerate(pattern.atom_edges[source])
            if _minterm_holds(pattern.predicates[edge.atom], symbol)
        )

    terminal = tuple(
        pattern.accept
        in _epsilon_closure(
            pattern,
            frozenset(
                (pattern.atom_edges[position.source][position.edge_index].target,)
            ),
            meter=meter,
        )
        for position in positions
    )
    start = (_START_POSITION, _START_POSITION, False)
    queue = deque((start,))
    parent: dict[
        tuple[int, int, bool],
        tuple[tuple[int, int, bool], object] | None,
    ] = {start: None}
    alphabet = _literal_alphabet(pattern)
    if meter is not None:
        meter.charge(len(pattern.predicates) + len(alphabet))
    while queue:
        current = queue.popleft()
        left, right, diverged = current
        for symbol in alphabet:
            if meter is not None:
                meter.charge(1)
            left_choices = choices(left, symbol)
            right_choices = choices(right, symbol)
            if meter is not None:
                meter.charge(len(left_choices) * len(right_choices))
            for left_next, right_next in product(left_choices, right_choices):
                pair = (
                    min(left_next, right_next),
                    max(left_next, right_next),
                    diverged or left_next != right_next,
                )
                if pair in parent:
                    continue
                if len(parent) == _MAX_AMBIGUITY_PAIRS:
                    raise Refusal(
                        RefusalStage.SEMANTICS,
                        "ambiguity checking needs more than 100000 state pairs; "
                        "no count is reported",
                    )
                parent[pair] = (current, symbol)
                if pair[2] and terminal[pair[0]] and terminal[pair[1]]:
                    witness: list[str] = []
                    cursor = pair
                    while parent[cursor] is not None:
                        previous, token = cast(
                            tuple[tuple[int, int, bool], object], parent[cursor]
                        )
                        witness.append(
                            "<other>" if token is _OTHER else cast(str, token)
                        )
                        cursor = previous
                    return tuple(reversed(witness))
                parent_pair = pair
                queue.append(parent_pair)
    return None


def _path_count(plan: PathPlan[object]) -> int:
    meter = _active_meter()
    totals = [0] * len(plan.items)
    for item in plan.order:
        if meter is not None:
            meter.charge(len(plan.children[item]))
        totals[item] = (
            sum(totals[child] for child in plan.children[item])
            if plan.children[item]
            else 1
        )
    if meter is not None:
        meter.charge(len(plan.roots))
    return sum(totals[root] for root in plan.roots)


def _graph_item(graph: Graph, reference: ItemRef) -> Item:
    tier = next(
        candidate
        for candidate in graph.tiers
        if candidate.declaration.name == reference.tier
    )
    return tier.items[reference.index]


def _display_name(graph: Graph, name: QualifiedName) -> str:
    prefix = next(
        (
            binding.prefix
            for binding in graph.namespaces
            if binding.namespace == name.namespace
        ),
        None,
    )
    return f"{prefix}:{name.local_name}" if prefix is not None else str(name)


def _indefinite(noun: str) -> str:
    return f"{'an' if noun[0] in 'aeiou' else 'a'} {noun}"


def _json_kind(value: object) -> str:
    if value is None:
        return "null"
    return {
        bool: "boolean",
        int: "integer",
        float: "double",
        str: "string",
        list: "array",
        dict: "object",
    }[type(value)]


@dataclass(frozen=True, slots=True)
class OutputMasses[Value]:
    """Candidate and residual masses from one product-plan marginal pass.

    ``decided`` and ``tied`` are available only for ``LOG_PROBABILITY`` and
    ``COUNTING`` because their numeric order gives the certificate its meaning;
    both are ``None`` under other algebras. Counting ties use exact integer
    equality. Log-probability ties use equality of the computed doubles, so an
    approximate addition can split equal real masses and ``tied`` is not a tie
    certificate for the underlying real values.
    """

    total: Value
    per_candidate: tuple[Value, ...]
    residual: Value
    zero_mass: bool
    decided: bool | None
    tied: tuple[int, ...] | None
    cost: FoldCost

    def to_data(self, semiring: Semiring[Value]) -> dict[str, object]:
        """Return deterministic strict-JSON data using the carrier encoding."""
        return {
            "total": semiring.encode(self.total),
            "per_candidate": [semiring.encode(value) for value in self.per_candidate],
            "residual": semiring.encode(self.residual),
            "zero_mass": self.zero_mass,
            "decided": self.decided,
            "tied": None if self.tied is None else list(self.tied),
            "cost": self.cost.to_data(),
        }


@dataclass(frozen=True, slots=True)
class OutputItemMarginals[Value]:
    """Conditioned marginals pooled into the base plan's item order."""

    total: Value
    zero_mass: bool
    values: tuple[Value, ...] | None
    cost: FoldCost

    def to_data(self, semiring: Semiring[Value]) -> dict[str, object]:
        """Return deterministic strict-JSON data using the carrier encoding."""
        return {
            "total": semiring.encode(self.total),
            "zero_mass": self.zero_mass,
            "values": (
                None
                if self.values is None
                else [semiring.encode(value) for value in self.values]
            ),
            "cost": self.cost.to_data(),
        }


@dataclass(frozen=True, slots=True)
class OutputPlan[Value]:
    """A cached product plan for complete output candidates and their residual."""

    base: PathPlan[Value]
    emissions: Emissions[Value]
    candidates: tuple[tuple[str, ...], ...]
    plan: PathPlan[Value]
    base_index: Mapping[ItemRef, ItemRef]
    accepted: tuple[bool, ...]
    _product_base: tuple[int | None, ...] = field(repr=False, compare=False)
    _accept_indices: tuple[int, ...] = field(repr=False, compare=False)
    _residual_index: int = field(repr=False, compare=False)
    _conditioned_cache: dict[int, tuple[PathPlan[Value], tuple[int, ...]]] = field(
        default_factory=dict, repr=False, compare=False
    )

    @classmethod
    def prepare(
        cls,
        base: PathPlan[Value],
        emissions: Emissions[Value],
        candidates: Sequence[Sequence[str]],
    ) -> OutputPlan[Value]:
        """Build the reachable product with the candidates' trie and a residual."""
        if emissions.plan is not base:
            raise ValueError("emissions are bound to a different path plan")
        algebra = base.declaration.semiring
        if not algebra.multiply_commutative:
            raise ValueError(
                f"algebra {type(algebra).__name__!r} does not declare commutative "
                "multiplication required by output marginals"
            )
        parsed = tuple(
            _candidate(candidate, index) for index, candidate in enumerate(candidates)
        )
        if not parsed:
            raise ValueError("output plan candidates must not be empty")
        if len(set(parsed)) != len(parsed):
            repeated = next(
                candidate for candidate in parsed if parsed.count(candidate) > 1
            )
            raise ValueError(f"output plan candidate {repeated!r} is duplicated")
        if not base.roots:
            raise ValueError(
                f"path plan {base.declaration.name!r} has no root for an explicit "
                "product root"
            )

        transitions, terminals = _trie(parsed)

        def advance(state: int, tokens: tuple[str, ...]) -> int:
            """Advance a trie state through one item's complete emission."""
            if state == _OFF:
                return _OFF
            for token in tokens:
                state = transitions.get((state, token), _OFF)
                if state == _OFF:
                    break
            return state

        product_graph = _product(
            cast(PathPlan[object], base),
            cast(Emissions[object], emissions),
            algebra,
            0,
            lambda state, tokens: (advance(state, tokens),),
        )
        pairs = product_graph.pairs
        roots = product_graph.roots
        edges = [
            (parent, child)
            for parent, children in enumerate(product_graph.edges)
            for child in children
        ]

        accepted = [False] * len(parsed)
        sink_targets: list[int] = []
        for base_item, state in pairs:
            if base.children[base_item]:
                sink_targets.append(-1)
                continue
            candidate_index = terminals.get(state)
            if candidate_index is not None:
                accepted[candidate_index] = True
                sink_targets.append(candidate_index)
            else:
                sink_targets.append(len(parsed))

        accept_local = tuple(len(pairs) + index for index in range(len(parsed)))
        residual_local = len(pairs) + len(parsed)
        for product_sink, target in enumerate(sink_targets):
            if target >= 0:
                child = (
                    residual_local if target == len(parsed) else accept_local[target]
                )
                edges.append((product_sink, child))
        product_base = tuple(base_item for base_item, _state in pairs) + (None,) * (
            len(parsed) + 1
        )
        product_plan = _prepare_derived(
            f"{base.declaration.name}:outputs",
            product_graph.algebra,
            tuple(base.values[index] for index, _state in pairs)
            + (algebra.one,) * (len(parsed) + 1),
            edges,
            roots,
            carrier_operation_cost=base.declaration.carrier_operation_cost,
        )
        accept_indices = tuple(
            product_plan.index(ItemRef(_TIER, local)) for local in accept_local
        )
        residual_index = product_plan.index(ItemRef(_TIER, residual_local))
        mapping = {
            product_plan.items[product_index]: base.items[base_item]
            for product_index, base_item in enumerate(product_base)
            if base_item is not None
        }
        return cls(
            base,
            emissions,
            parsed,
            product_plan,
            mapping,
            tuple(accepted),
            product_base,
            accept_indices,
            residual_index,
        )

    def values(self, base_values: Sequence[Value] | None = None) -> tuple[Value, ...]:
        """Gather base values into the product plan's canonical item order."""
        vector = self.base.values if base_values is None else tuple(base_values)
        if len(vector) != len(self.base.items):
            raise ValueError(
                f"path plan {self.base.declaration.name!r} takes "
                f"{len(self.base.items)} values in plan item order and was given "
                f"{len(vector)}"
            )
        algebra = self.base.declaration.semiring
        return tuple(
            algebra.one if base_index is None else vector[base_index]
            for base_index in self._product_base
        )

    def masses(self, base_values: Sequence[Value] | None = None) -> OutputMasses[Value]:
        """Evaluate masses, with certificates only for log probability or counting."""
        with _aggregating():
            return self._masses(base_values)

    def _masses(
        self, base_values: Sequence[Value] | None = None
    ) -> OutputMasses[Value]:
        """Implement mass aggregation within the caller's aggregate scope."""
        algebra = self.base.declaration.semiring
        result = self.plan.marginals(self.values(base_values))
        per_candidate = tuple(result.marginals[index] for index in self._accept_indices)
        residual = result.marginals[self._residual_index]
        zero_mass = result.total == algebra.zero
        algebra_object = cast(object, algebra)
        tied: tuple[int, ...] | None
        if algebra_object is not LOG_PROBABILITY and algebra_object is not COUNTING:
            decided = None
            tied = None
        elif zero_mass:
            decided = False
            tied = ()
        else:
            comparable = cast(tuple[float | int, ...], per_candidate)
            best = max(comparable)
            tied = tuple(
                index for index, value in enumerate(comparable) if value == best
            )
            decided = best >= cast(float | int, residual)
        return OutputMasses(
            result.total,
            per_candidate,
            residual,
            zero_mass,
            decided,
            tied,
            result.cost,
        )

    def conditioned(self, candidate: int) -> PathPlan[Value]:
        """Return the product restricted to paths accepting one candidate."""
        return self._conditioned(candidate)[0]

    def item_marginals(
        self, candidate: int, base_values: Sequence[Value] | None = None
    ) -> OutputItemMarginals[Value]:
        """Pool one candidate's conditioned product copies onto base items."""
        with _aggregating():
            return self._item_marginals(candidate, base_values)

    def _item_marginals(
        self, candidate: int, base_values: Sequence[Value] | None = None
    ) -> OutputItemMarginals[Value]:
        """Implement conditioned pooling within the caller's aggregate scope."""
        conditioned, product_indices = self._conditioned(candidate)
        product_values = self.values(base_values)
        result = conditioned.marginals(
            tuple(product_values[index] for index in product_indices)
        )
        algebra = self.base.declaration.semiring
        if result.total == algebra.zero:
            return OutputItemMarginals(result.total, True, None, result.cost)
        pooled = [algebra.zero for _item in self.base.items]
        for local_index, product_index in enumerate(product_indices):
            base_index = self._product_base[product_index]
            if base_index is not None:
                pooled[base_index] = algebra.add(
                    pooled[base_index], result.marginals[local_index]
                )
        return OutputItemMarginals(result.total, False, tuple(pooled), result.cost)

    def _conditioned(self, candidate: int) -> tuple[PathPlan[Value], tuple[int, ...]]:
        if type(candidate) is not int or not 0 <= candidate < len(self.candidates):
            raise ValueError(
                f"candidate index {candidate!r} is outside output plan candidates"
            )
        if not self.accepted[candidate]:
            raise ValueError(
                f"output candidate {candidate} {self.candidates[candidate]!r} has no "
                "structurally accepted path"
            )
        cached = self._conditioned_cache.get(candidate)
        if cached is not None:
            return cached
        target = self._accept_indices[candidate]
        keep = {target}
        queue = deque((target,))
        while queue:
            child = queue.popleft()
            for parent in self.plan.parents[child]:
                if parent not in keep:
                    keep.add(parent)
                    queue.append(parent)
        product_indices = tuple(
            index for index in range(len(self.plan.items)) if index in keep
        )
        local = {product: index for index, product in enumerate(product_indices)}
        edges = [
            (local[parent], local[child])
            for parent in product_indices
            for child in self.plan.children[parent]
            if child in keep
        ]
        roots = tuple(local[root] for root in self.plan.roots if root in keep)
        conditioned = _prepare_derived(
            f"{self.base.declaration.name}:output:{candidate}",
            self.base.declaration.semiring,
            tuple(self.plan.values[index] for index in product_indices),
            edges,
            roots,
            carrier_operation_cost=self.base.declaration.carrier_operation_cost,
        )
        cached = (conditioned, product_indices)
        self._conditioned_cache[candidate] = cached
        return cached


def _candidate(candidate: Sequence[str], index: int) -> tuple[str, ...]:
    if isinstance(candidate, (str, bytes)) or not isinstance(candidate, Sequence):
        raise TypeError(f"output candidate {index} must be a sequence of strings")
    parsed = tuple(candidate)
    for token in parsed:
        if type(token) is not str:
            raise TypeError(
                f"output candidate {index} contains symbol {token!r}; every symbol "
                "must be a string"
            )
    return parsed


def _trie(
    candidates: tuple[tuple[str, ...], ...],
) -> tuple[dict[tuple[int, str], int], dict[int, int]]:
    transitions: dict[tuple[int, str], int] = {}
    terminals: dict[int, int] = {}
    next_state = 1
    for candidate_index, candidate in enumerate(candidates):
        state = 0
        for token in candidate:
            key = (state, token)
            following = transitions.get(key)
            if following is None:
                following = next_state
                next_state += 1
                transitions[key] = following
            state = following
        terminals[state] = candidate_index
    return transitions, terminals


def _prepare_derived[Value](
    name: str,
    algebra: Semiring[Value],
    values: tuple[Value, ...],
    edges: Sequence[tuple[int, int]],
    roots: tuple[int, ...],
    *,
    carrier_operation_cost: int,
) -> PathPlan[Value]:
    if not roots:
        raise ValueError(f"derived path plan {name!r} requires explicit nonempty roots")
    items = tuple(
        Item(
            f"item-{index}",
            (AttributeValue(_SOURCE, XsdType.INTEGER, str(index)),),
        )
        for index in range(len(values))
    )
    graph = Graph(
        (NamespaceDeclaration("output", _NAMESPACE),),
        (Tier(TierDeclaration(_TIER, "Output product items"), items),),
        (
            SimpleRelationDeclaration(_MEMBERSHIP, _TIER, _TYPE),
            BipartiteRelationDeclaration(_NEXT, _TYPE, _TYPE, acyclic=True),
        ),
        tuple(
            RelationInstance(_NEXT, ItemRef(_TIER, parent), ItemRef(_TIER, child))
            for parent, child in edges
        ),
        (AttributeDeclaration(_SOURCE, AttributeDomain.ITEM, XsdType.INTEGER),),
    )

    def lift(source: object, _label: str) -> Value:
        """Read the carrier value stored outside the derived graph."""
        return values[int(cast(Decimal, source))]

    declaration = FoldDeclaration(
        name,
        graph,
        AttributeValuation("source", _SOURCE, (_TIER,)),
        algebra,
        lift,
        (FoldTransition(_NEXT, ChildCombination.OR),),
        roots=tuple(ItemRef(_TIER, root) for root in roots),
        carrier_operation_cost=carrier_operation_cost,
    )
    return PathPlan.prepare(declaration)


__all__ = [
    "AmbiguityPolicy",
    "Determinize",
    "Emissions",
    "LatticeMatch",
    "OutputItemMarginals",
    "OutputMasses",
    "OutputPlan",
    "Unambiguous",
    "match_lattice",
]
