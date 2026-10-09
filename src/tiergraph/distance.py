"""Declared graph-edit costs, exact structured engines, and honest bounds."""

from __future__ import annotations

import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import cast

from tiergraph.core import (
    Attribute,
    BoundaryRef,
    DurableBoundaryRef,
    DurableItemRef,
    DurablePolyadicRef,
    DurableRelationRef,
    EditDeclaration,
    Graph,
    GraphValidationError,
    Item,
    ItemRef,
    JsonValue,
    NamespaceDeclaration,
    PolyadicInstanceRef,
    PolyadicRelationInstance,
    QualifiedName,
    RelationDeclaration,
    RelationInstance,
    RelationInstanceRef,
    Seal,
    undeclare_with_contents,
)
from tiergraph.diff import diff
from tiergraph.equivalence import EquivalenceView, equivalent
from tiergraph.machine import DeltaOpcode
from tiergraph.patch import Patch
from tiergraph.schema import Refusal, RefusalStage

type CostLike = Decimal | int | float | str
type ValueSubstitution = Callable[[Attribute, Attribute], CostLike]
type BoundaryDisplacement = Callable[[int, int], CostLike]
type SequenceCost[T] = CostLike | Callable[[T], CostLike]
type SubstitutionCost[T] = CostLike | Callable[[T, T], CostLike]
type GraphProjection = Callable[[Graph], Sequence[object]]
type TextPieces = Callable[[Graph], Iterable[str]]
type TextJoin = Callable[[Iterable[str]], str]
type TextTransform = Callable[[str], str]

# The assignment matrix has one row and column per unmatched source or target
# atom. Keep its cubic solver bounded; larger sets use the linear count bound.
_EXACT_ATOM_ASSIGNMENT_LIMIT = 64

_INVERSE_KINDS = {
    "add_layer": "remove_layer",
    "add_relation": "remove_relation",
    "declare": "undeclare",
    "insert_item": "remove_item",
    "promote_boundary": "demote_boundary",
    "promote_item": "demote_item",
    "promote_relation": "demote_relation",
    "put_fact": "remove_fact",
    "seal": "drop_seal",
    "set_attribute": "remove_attribute",
}
_COST_KIND_ALIASES = {
    "add_item": "insert_item",
    "attach_value": "set_attribute",
    "declare_attribute": "declare",
    "declare_namespace": "declare",
    "declare_relation": "declare",
    "declare_tier": "declare",
    "move_run": "move_item",
    "promote_position": "promote_boundary",
    "relate": "add_relation",
    "seal_prefix": "seal",
    "shift": "swap_items",
    "swap_runs": "swap_items",
}

# These are the one-step graph changes that every lower-bound relaxation must
# dominate. Native shortcuts are included because one can otherwise undercut a
# relaxation assembled only from the reversible basis.
PRIMITIVE_KINDS = frozenset(
    {
        *_INVERSE_KINDS,
        *_INVERSE_KINDS.values(),
        "move_item",
        "replace_item",
        "set_endpoints",
        "swap_items",
        "unseal",
    }
)


def _cost(value: CostLike, subject: str) -> Decimal:
    """Return one finite nonnegative cost without binary-float arithmetic."""
    if isinstance(value, bool):
        raise TypeError(f"{subject} must be a number, not a boolean")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{subject} must be a finite nonnegative number") from error
    if not result.is_finite() or result < 0:
        raise ValueError(f"{subject} must be a finite nonnegative number")
    return result


def _unit_value_substitution(before: Attribute, after: Attribute) -> CostLike:
    """Price an unequal attribute value as one substitution."""
    return 0 if before == after else 1


def _unit_boundary_displacement(before: int, after: int) -> CostLike:
    """Price a changed boundary independently of displacement magnitude."""
    return 0 if before == after else 1


def _default_value_substitution() -> ValueSubstitution:
    """Return the stable default value-cost callback."""
    return _unit_value_substitution


def _default_boundary_displacement() -> BoundaryDisplacement:
    """Return the stable default boundary-cost callback."""
    return _unit_boundary_displacement


def _default_operation_costs() -> dict[str, Decimal]:
    """Return symmetric unit costs, with reorder shortcuts priced as two edits."""
    result = {kind: Decimal(1) for kind in sorted(PRIMITIVE_KINDS)}
    result["move_item"] = Decimal(2)
    result["swap_items"] = Decimal(2)
    return result


@dataclass(frozen=True, slots=True)
class CostTable:
    """Declare graph-operation costs and domain-specific value costs.

    Operation costs are global unless ``declarations`` overrides one operation
    for a qualified declaration. Names must belong to :data:`PRIMITIVE_KINDS`,
    and inverse pairs must have equal costs. Zero is accepted for projections,
    but an exact result is a metric only when every operation visible in its
    equivalence view has positive cost.

    The two callbacks remain Python-only because a data file cannot safely name
    executable code. :meth:`from_data` therefore reads numeric operation and
    declaration costs while retaining the default callbacks.
    """

    operations: Mapping[str, CostLike] = field(default_factory=_default_operation_costs)
    declarations: Mapping[QualifiedName, Mapping[str, CostLike]] = field(
        default_factory=dict
    )
    value_substitution: ValueSubstitution = field(
        default_factory=_default_value_substitution, repr=False
    )
    boundary_displacement: BoundaryDisplacement = field(
        default_factory=_default_boundary_displacement, repr=False
    )

    def __post_init__(self) -> None:
        """Detach caller mappings and validate symmetry and numeric bounds."""
        operations = {
            kind: _cost(value, f"operation cost {kind!r}")
            for kind, value in self.operations.items()
        }
        if not all(isinstance(kind, str) and kind for kind in operations):
            raise TypeError("operation cost names must be nonempty strings")
        unknown = set(operations) - PRIMITIVE_KINDS
        if unknown:
            raise ValueError(f"unknown graph operation costs: {sorted(unknown)!r}")
        declarations: dict[QualifiedName, Mapping[str, Decimal]] = {}
        for declaration, values in self.declarations.items():
            if not isinstance(declaration, QualifiedName):
                raise TypeError("declaration cost keys must be qualified names")
            if not all(isinstance(kind, str) and kind for kind in values):
                raise TypeError("declaration operation names must be nonempty strings")
            unknown = set(values) - PRIMITIVE_KINDS
            if unknown:
                raise ValueError(
                    f"unknown graph operation costs for {str(declaration)!r}: "
                    f"{sorted(unknown)!r}"
                )
            declarations[declaration] = MappingProxyType(
                {
                    kind: _cost(
                        value,
                        f"declaration {str(declaration)!r} operation cost {kind!r}",
                    )
                    for kind, value in values.items()
                }
            )
        object.__setattr__(self, "operations", MappingProxyType(operations))
        object.__setattr__(self, "declarations", MappingProxyType(declarations))
        self._check_inverse_symmetry(None, operations)
        for declaration, values in declarations.items():
            merged = {**operations, **values}
            self._check_inverse_symmetry(declaration, merged)

    @staticmethod
    def _check_inverse_symmetry(
        declaration: QualifiedName | None, values: Mapping[str, Decimal]
    ) -> None:
        """Require each present operation/inverse pair to have one cost."""
        for operation, inverse in _INVERSE_KINDS.items():
            if operation not in values or inverse not in values:
                continue
            if values[operation] != values[inverse]:
                scope = "global" if declaration is None else str(declaration)
                raise ValueError(
                    f"{scope} costs for {operation!r} and {inverse!r} must match"
                )

    def operation(self, kind: str, declaration: QualifiedName | None = None) -> Decimal:
        """Return the declared cost for one operation and optional declaration."""
        if declaration is not None:
            override = self.declarations.get(declaration, {}).get(kind)
            if override is not None:
                return cast(Decimal, override)
        try:
            return cast(Decimal, self.operations[kind])
        except KeyError as error:
            raise ValueError(f"no cost is declared for operation {kind!r}") from error

    def minimum_operation(self, kind: str) -> Decimal:
        """Return the least declared cost for an operation in any scope."""
        candidates = [self.operation(kind)]
        candidates.extend(
            cast(Decimal, values[kind])
            for values in self.declarations.values()
            if kind in values
        )
        return min(candidates)

    def substitute_value(self, before: Attribute, after: Attribute) -> Decimal:
        """Return the checked domain-specific cost of replacing one value."""
        if before.name != after.name:
            raise ValueError("value substitution requires values with the same name")
        return _cost(
            self.value_substitution(before, after),
            f"value substitution for {str(before.name)!r}",
        )

    def displace_boundary(self, before: int, after: int) -> Decimal:
        """Return the checked domain-specific cost of moving one boundary."""
        return _cost(
            self.boundary_displacement(before, after),
            f"boundary displacement from {before} to {after}",
        )

    def metric_violations(self) -> tuple[str, ...]:
        """Name declared graph operations whose zero cost prevents a metric."""
        violations = {kind for kind, value in self.operations.items() if not value}
        violations.update(
            f"{declaration}:{kind}"
            for declaration, values in self.declarations.items()
            for kind, value in values.items()
            if not value
        )
        return tuple(sorted(violations))

    def to_data(self) -> dict[str, JsonValue]:
        """Return the declarative numeric part as JSON-compatible data."""
        return {
            "operations": {
                kind: _json_cost(cast(Decimal, value))
                for kind, value in sorted(self.operations.items())
            },
            "declarations": [
                {
                    "declaration": declaration.to_data(),
                    "operations": {
                        kind: _json_cost(cast(Decimal, value))
                        for kind, value in sorted(values.items())
                    },
                }
                for declaration, values in sorted(self.declarations.items())
            ],
        }

    @classmethod
    def from_data(cls, value: object) -> CostTable:
        """Decode strict numeric cost-table data for command-line use."""
        if not isinstance(value, dict) or set(value) - {"operations", "declarations"}:
            raise Refusal(
                RefusalStage.SHAPE,
                "cost table must be an object containing operations and declarations",
            )
        raw_operations = value.get("operations", {})
        if not isinstance(raw_operations, dict) or any(
            not isinstance(kind, str) for kind in raw_operations
        ):
            raise Refusal(
                RefusalStage.CONSTRUCTION, "cost operations must be an object"
            )
        raw_declarations = value.get("declarations", [])
        if not isinstance(raw_declarations, list):
            raise Refusal(
                RefusalStage.CONSTRUCTION, "cost declarations must be an array"
            )
        declarations: dict[QualifiedName, Mapping[str, CostLike]] = {}
        for index, entry in enumerate(raw_declarations):
            if not isinstance(entry, dict) or set(entry) != {
                "declaration",
                "operations",
            }:
                raise Refusal(
                    RefusalStage.SHAPE,
                    f"cost declarations[{index}] needs declaration and operations",
                )
            raw_name = entry["declaration"]
            raw_costs = entry["operations"]
            if (
                not isinstance(raw_name, dict)
                or set(raw_name) != {"namespace", "local_name"}
                or not isinstance(raw_name["namespace"], str)
                or not isinstance(raw_name["local_name"], str)
            ):
                raise Refusal(
                    RefusalStage.CONSTRUCTION,
                    f"cost declarations[{index}].declaration must be a qualified name",
                )
            if not isinstance(raw_costs, dict) or any(
                not isinstance(kind, str) for kind in raw_costs
            ):
                raise Refusal(
                    RefusalStage.CONSTRUCTION,
                    f"cost declarations[{index}].operations must be an object",
                )
            name = QualifiedName(raw_name["namespace"], raw_name["local_name"])
            if name in declarations:
                raise Refusal(
                    RefusalStage.SEMANTICS,
                    f"cost declaration {str(name)!r} occurs more than once",
                )
            declarations[name] = cast(Mapping[str, CostLike], raw_costs)
        try:
            return cls(cast(Mapping[str, CostLike], raw_operations), declarations)
        except (TypeError, ValueError) as error:
            raise Refusal(RefusalStage.VALUE, f"invalid cost table: {error}") from error


UNIT_COSTS = CostTable()


def _json_cost(value: Decimal) -> int | float:
    """Encode a finite decimal as an ordinary JSON number."""
    integral = value.to_integral_value()
    return int(integral) if value == integral else float(value)


def _element_cost[T](value: T, cost: SequenceCost[T], subject: str) -> Decimal:
    """Evaluate a scalar or unary sequence cost."""
    raw = cost(value) if callable(cost) else cost
    return _cost(raw, subject)


def _substitution_cost[T](
    before: T, after: T, cost: SubstitutionCost[T], subject: str
) -> Decimal:
    """Evaluate a scalar or binary sequence substitution cost."""
    if before == after:
        return Decimal(0)
    raw = cost(before, after) if callable(cost) else cost
    return _cost(raw, subject)


def weighted_sequence_distance[T](
    source: Sequence[T],
    target: Sequence[T],
    *,
    insert: SequenceCost[T] = 1,
    delete: SequenceCost[T] = 1,
    substitute: SubstitutionCost[T] = 1,
) -> Decimal:
    """Return exact weighted insertion, deletion, and substitution distance.

    The dynamic program uses ``O(len(source) * len(target))`` time and two rows
    of storage. Costs may be constants or value-sensitive callables.
    """
    previous = [Decimal(0)]
    for item in target:
        previous.append(
            previous[-1] + _element_cost(item, insert, "sequence insertion")
        )
    for before in source:
        deletion = _element_cost(before, delete, "sequence deletion")
        current = [previous[0] + deletion]
        for index, after in enumerate(target, 1):
            insertion = _element_cost(after, insert, "sequence insertion")
            substitution = _substitution_cost(
                before, after, substitute, "sequence substitution"
            )
            current.append(
                min(
                    previous[index] + deletion,
                    current[index - 1] + insertion,
                    previous[index - 1] + substitution,
                )
            )
        previous = current
    return previous[-1]


@dataclass(frozen=True, slots=True)
class OrderedTree:
    """Hold one labeled node and its ordered children for tree distance."""

    value: object
    children: tuple[OrderedTree, ...] = ()

    def __post_init__(self) -> None:
        """Detach the ordered child collection from mutable caller sequences."""
        object.__setattr__(self, "children", tuple(self.children))


@dataclass(frozen=True, slots=True)
class _PostorderTree:
    """Store the postorder arrays used by the ordered-tree dynamic program."""

    nodes: tuple[OrderedTree, ...]
    leftmost: tuple[int, ...]
    keyroots: tuple[int, ...]


def _postorder(root: OrderedTree) -> _PostorderTree:
    """Return one-based Zhang-Shasha postorder indexes for a tree."""
    nodes: list[OrderedTree] = []
    leftmost: list[int] = [0]

    def _visit(node: OrderedTree) -> int:
        first_leaf: int | None = None
        for child in node.children:
            child_leaf = _visit(child)
            if first_leaf is None:
                first_leaf = child_leaf
        nodes.append(node)
        index = len(nodes)
        leftmost.append(index if first_leaf is None else first_leaf)
        return leftmost[index]

    _visit(root)
    last_for_leaf: dict[int, int] = {}
    for index in range(1, len(nodes) + 1):
        last_for_leaf[leftmost[index]] = index
    return _PostorderTree(
        tuple(nodes), tuple(leftmost), tuple(sorted(last_for_leaf.values()))
    )


def ordered_tree_distance(
    source: OrderedTree,
    target: OrderedTree,
    *,
    insert: SequenceCost[object] = 1,
    delete: SequenceCost[object] = 1,
    substitute: SubstitutionCost[object] = 1,
) -> Decimal:
    """Return exact ordered-tree edit distance with node promotion on deletion.

    This is the Zhang-Shasha operation model: deleting a node promotes its
    children into the ordered forest, and insertion is the inverse. The result
    is exact for that model and does not claim exactness for overlapping graph
    relations or cheaper graph-level move and reparent operations.
    """
    left = _postorder(source)
    right = _postorder(target)
    tree = [[Decimal(0)] * (len(right.nodes) + 1) for _ in range(len(left.nodes) + 1)]
    for left_root in left.keyroots:
        for right_root in right.keyroots:
            left_start = left.leftmost[left_root]
            right_start = right.leftmost[right_root]
            rows = left_root - left_start + 2
            columns = right_root - right_start + 2
            forest = [[Decimal(0)] * columns for _ in range(rows)]
            for row in range(1, rows):
                left_index = left_start + row - 1
                forest[row][0] = forest[row - 1][0] + _element_cost(
                    left.nodes[left_index - 1].value,
                    delete,
                    "tree deletion",
                )
            for column in range(1, columns):
                right_index = right_start + column - 1
                forest[0][column] = forest[0][column - 1] + _element_cost(
                    right.nodes[right_index - 1].value,
                    insert,
                    "tree insertion",
                )
            for row in range(1, rows):
                left_index = left_start + row - 1
                deletion = _element_cost(
                    left.nodes[left_index - 1].value,
                    delete,
                    "tree deletion",
                )
                for column in range(1, columns):
                    right_index = right_start + column - 1
                    insertion = _element_cost(
                        right.nodes[right_index - 1].value,
                        insert,
                        "tree insertion",
                    )
                    candidates = (
                        forest[row - 1][column] + deletion,
                        forest[row][column - 1] + insertion,
                    )
                    if (
                        left.leftmost[left_index] == left_start
                        and right.leftmost[right_index] == right_start
                    ):
                        substitution = _substitution_cost(
                            left.nodes[left_index - 1].value,
                            right.nodes[right_index - 1].value,
                            substitute,
                            "tree substitution",
                        )
                        value = min(
                            *candidates,
                            forest[row - 1][column - 1] + substitution,
                        )
                        forest[row][column] = value
                        tree[left_index][right_index] = value
                    else:
                        prefix_row = left.leftmost[left_index] - left_start
                        prefix_column = right.leftmost[right_index] - right_start
                        forest[row][column] = min(
                            *candidates,
                            forest[prefix_row][prefix_column]
                            + tree[left_index][right_index],
                        )
    return tree[len(left.nodes)][len(right.nodes)]


def contiguous_segmentation_distance(
    source: Sequence[int],
    target: Sequence[int],
    *,
    split: CostLike = 1,
    merge: CostLike = 1,
    displace: SubstitutionCost[int] = 1,
) -> Decimal:
    """Return exact edit distance between two contiguous segmentations.

    Each sequence gives positive segment widths over the same number of base
    units. Removing an internal boundary is one merge, adding one is a split,
    and matching unequal boundary positions uses the declared displacement
    cost. Pass :meth:`CostTable.displace_boundary` to use a table's callback.
    """
    if any(
        isinstance(width, bool) or not isinstance(width, int) or width <= 0
        for width in (*source, *target)
    ):
        raise ValueError("segmentation widths must be positive integers")
    if sum(source) != sum(target):
        raise ValueError("segmentations must cover the same number of base units")

    def _boundaries(widths: Sequence[int]) -> set[int]:
        result: set[int] = set()
        position = 0
        for width in widths[:-1]:
            position += width
            result.add(position)
        return result

    before = tuple(sorted(_boundaries(source)))
    after = tuple(sorted(_boundaries(target)))
    split_cost = _cost(split, "segmentation split")
    merge_cost = _cost(merge, "segmentation merge")
    size = len(before) + len(after)
    matrix = [
        [
            *(
                _substitution_cost(
                    left,
                    right,
                    displace,
                    "boundary displacement",
                )
                for right in after
            ),
            *(merge_cost for _ in before),
        ]
        for left in before
    ]
    matrix.extend(
        [*(split_cost for _ in after), *(Decimal(0) for _ in before)] for _ in after
    )
    if not size:
        return Decimal(0)
    return _minimum_assignment_cost(matrix)


def _minimum_assignment_cost(costs: Sequence[Sequence[Decimal]]) -> Decimal:
    """Return the minimum cost of a square assignment with exact arithmetic."""
    size = len(costs)
    potential_rows = [Decimal(0)] * (size + 1)
    potential_columns = [Decimal(0)] * (size + 1)
    matching = [0] * (size + 1)
    predecessor = [0] * (size + 1)
    infinity = Decimal("Infinity")
    for row in range(1, size + 1):
        matching[0] = row
        minimum = [infinity] * (size + 1)
        used = [False] * (size + 1)
        column = 0
        while True:
            used[column] = True
            matched_row = matching[column]
            delta = infinity
            next_column = 0
            for candidate in range(1, size + 1):
                if used[candidate]:
                    continue
                reduced = (
                    costs[matched_row - 1][candidate - 1]
                    - potential_rows[matched_row]
                    - potential_columns[candidate]
                )
                if reduced < minimum[candidate]:
                    minimum[candidate] = reduced
                    predecessor[candidate] = column
                if minimum[candidate] < delta:
                    delta = minimum[candidate]
                    next_column = candidate
            for candidate in range(size + 1):
                if used[candidate]:
                    potential_rows[matching[candidate]] += delta
                    potential_columns[candidate] -= delta
            for candidate in range(1, size + 1):
                if not used[candidate]:
                    minimum[candidate] -= delta
            column = next_column
            if not matching[column]:
                break
        while True:
            previous = predecessor[column]
            matching[column] = matching[previous]
            column = previous
            if not column:
                break
    return sum(
        (costs[matching[column] - 1][column - 1] for column in range(1, size + 1)),
        start=Decimal(0),
    )


@dataclass(frozen=True, slots=True)
class SequenceProjection:
    """Project a graph to a sequence with its own weighted edit model."""

    name: str
    project: GraphProjection
    insert: SequenceCost[object] = 1
    delete: SequenceCost[object] = 1
    substitute: SubstitutionCost[object] = 1

    def __post_init__(self) -> None:
        """Require a stable nonempty diagnostic name."""
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("projection name must be a nonempty string")

    def distance(self, source: Graph, target: Graph) -> Decimal:
        """Return this projection's exact sequence distance."""
        return weighted_sequence_distance(
            tuple(self.project(source)),
            tuple(self.project(target)),
            insert=self.insert,
            delete=self.delete,
            substitute=self.substitute,
        )


def text_projection(
    name: str,
    pieces: TextPieces,
    *,
    join: TextJoin,
    transform: TextTransform | None = None,
) -> SequenceProjection:
    """Build a character projection with caller-declared joining policy.

    ``pieces`` extracts ordered text fragments. ``join`` decides what, if
    anything, lies between them. ``transform`` can implement a presentation
    view; no language- or locale-specific joining rule is built in.
    """

    def _project(graph: Graph) -> Sequence[object]:
        text = join(pieces(graph))
        return tuple(text if transform is None else transform(text))

    return SequenceProjection(name, _project)


def whitespace_insensitive_projection(
    pieces: TextPieces, *, join: TextJoin
) -> SequenceProjection:
    """Build a character projection that removes ``str.isspace()`` characters."""
    return text_projection(
        "whitespace-insensitive",
        pieces,
        join=join,
        transform=lambda text: "".join(
            character for character in text if not character.isspace()
        ),
    )


def format_control_insensitive_projection(
    pieces: TextPieces, *, join: TextJoin
) -> SequenceProjection:
    """Build a character projection that gives Unicode Cf controls zero cost."""
    return text_projection(
        "format-control-insensitive",
        pieces,
        join=join,
        transform=lambda text: "".join(
            character for character in text if unicodedata.category(character) != "Cf"
        ),
    )


@dataclass(frozen=True, slots=True)
class DistanceInterval:
    """Hold an exact distance or named lower and realized upper bounds."""

    lower: Decimal
    upper: Decimal
    exact: bool
    method: str
    lower_bound_method: str | None = None

    def __post_init__(self) -> None:
        """Require finite ordered bounds and an honest exactness flag."""
        lower = _cost(self.lower, "distance lower bound")
        upper = _cost(self.upper, "distance upper bound")
        if lower > upper:
            raise ValueError("distance lower bound must not exceed upper bound")
        if self.exact and lower != upper:
            raise ValueError("an exact distance must have equal bounds")
        if not isinstance(self.method, str) or not self.method:
            raise ValueError("distance method must be a nonempty string")
        if self.exact:
            if self.lower_bound_method is not None:
                raise ValueError("an exact distance has no lower-bound method")
        elif (
            not isinstance(self.lower_bound_method, str) or not self.lower_bound_method
        ):
            raise ValueError("an interval needs a nonempty lower-bound method")
        object.__setattr__(self, "lower", lower)
        object.__setattr__(self, "upper", upper)

    @property
    def value(self) -> Decimal | None:
        """Return the exact value, or ``None`` while the interval is open."""
        return self.lower if self.exact else None

    def to_data(self) -> dict[str, JsonValue]:
        """Return a JSON-compatible exact value or interval report."""
        if self.exact:
            return {
                "exact": True,
                "value": _json_cost(self.lower),
                "method": self.method,
            }
        return {
            "exact": False,
            "lower": _json_cost(self.lower),
            "upper": _json_cost(self.upper),
            "method": self.method,
            "lower_bound_method": self.lower_bound_method,
        }


@dataclass(frozen=True, slots=True)
class _Atom:
    """Hold one location-free unit retained by the general-graph relaxation."""

    kind: str
    value: object


type _RelationAtomValue = tuple[str, QualifiedName, str | None, tuple[Attribute, ...]]
type _CarrierValueAtom = tuple[str, QualifiedName | None, Attribute]


def _relation_atom(
    relation: RelationInstance | PolyadicRelationInstance,
    view: EquivalenceView,
) -> _Atom:
    """Erase endpoints and relation order while retaining the atomic payload."""
    kind = "polyadic" if isinstance(relation, PolyadicRelationInstance) else "binary"
    durable_id = None if view is EquivalenceView.FUNCTIONAL else relation.durable_id
    return _Atom(
        "relation",
        (kind, relation.declaration, durable_id, relation.attributes),
    )


def _graph_atoms(graph: Graph, view: EquivalenceView) -> Counter[_Atom]:
    """Project a graph to the proved location- and incidence-free atom multiset."""
    atoms: list[_Atom] = []
    atoms.extend(
        _Atom(
            "declaration",
            declaration
            if view is EquivalenceView.EXACT
            else ("namespace", declaration.namespace),
        )
        for declaration in graph.namespaces
    )
    atoms.extend(_Atom("declaration", tier.declaration) for tier in graph.tiers)
    atoms.extend(
        _Atom("declaration", declaration) for declaration in graph.relation_declarations
    )
    atoms.extend(
        _Atom("declaration", declaration)
        for declaration in graph.attribute_declarations
    )
    for tier in graph.tiers:
        atoms.extend(
            _Atom(
                "item",
                (
                    None if view is EquivalenceView.FUNCTIONAL else item.durable_id,
                    item.attributes,
                ),
            )
            for item in tier.items
        )
        atoms.extend(
            _Atom("carrier-value", ("tier", tier.declaration.name, value))
            for value in tier.attributes
        )
    atoms.extend(_relation_atom(relation, view) for relation in graph.relations)
    atoms.extend(
        _relation_atom(relation, view) for relation in graph.polyadic_relations
    )
    atoms.extend(
        _Atom("boundary-value", value)
        for boundary in graph.boundary_values
        for value in boundary.attributes
    )
    atoms.extend(_Atom("seal", seal) for seal in graph.seals)
    for layer in graph.layers:
        atoms.append(_Atom("layer", layer.name))
        atoms.extend(_Atom("fact", (layer.name, fact.value)) for fact in layer.facts)
    atoms.extend(
        _Atom("carrier-value", ("document", None, value)) for value in graph.attributes
    )
    return Counter(atoms)


def _attribute_transition(
    before: Attribute, after: Attribute, costs: CostTable
) -> Decimal:
    """Return the closed one-value transition cost for one declared name."""
    if before == after:
        return Decimal(0)
    if before.name != after.name:
        return costs.operation("remove_attribute", before.name) + costs.operation(
            "set_attribute", after.name
        )
    if costs.value_substitution is not _unit_value_substitution:
        # An opaque callback gives no finite alphabet on which to prove its
        # triangle closure. General-graph value substitution therefore relaxes
        # to zero; ordered-tier exact distance still evaluates the callback.
        return Decimal(0)
    return min(
        costs.operation("set_attribute", after.name),
        costs.substitute_value(before, after),
    )


def _attributes_transition(
    before: tuple[Attribute, ...],
    after: tuple[Attribute, ...],
    costs: CostTable,
) -> Decimal:
    """Close independent one-value transitions over a carrier payload."""
    left = {value.name: value for value in before}
    right = {value.name: value for value in after}
    total = sum(
        (costs.operation("remove_attribute", name) for name in left.keys() - right),
        start=Decimal(0),
    )
    total += sum(
        (costs.operation("set_attribute", name) for name in right.keys() - left),
        start=Decimal(0),
    )
    total += sum(
        (
            _attribute_transition(left[name], right[name], costs)
            for name in left.keys() & right
        ),
        start=Decimal(0),
    )
    return total


def _identity_transition(
    before: str | None,
    after: str | None,
    promote: Decimal,
    demote: Decimal,
) -> Decimal:
    """Return the shortest promotion/demotion path between durable identities."""
    if before == after:
        return Decimal(0)
    if before is None:
        return promote
    if after is None:
        return demote
    return demote + promote


def _declaration_scope_from_atom(value: object) -> QualifiedName | None:
    """Return the cost scope retained by one declaration atom."""
    if isinstance(value, tuple):
        return None
    return _declaration_scope(cast(EditDeclaration, value))


def _atom_insertion(atom: _Atom, costs: CostTable) -> Decimal:
    """Return the one-atom insertion edge induced by graph primitives."""
    if atom.kind == "declaration":
        return costs.operation("declare", _declaration_scope_from_atom(atom.value))
    if atom.kind == "item":
        return costs.minimum_operation("insert_item")
    if atom.kind == "relation":
        declaration = cast(tuple[object, QualifiedName, object, object], atom.value)[1]
        return costs.operation("add_relation", declaration)
    if atom.kind in {"boundary-value", "carrier-value"}:
        value = (
            atom.value
            if atom.kind == "boundary-value"
            else cast(_CarrierValueAtom, atom.value)[2]
        )
        return costs.operation("set_attribute", cast(Attribute, value).name)
    if atom.kind == "fact":
        value = cast(tuple[object, Attribute], atom.value)[1]
        return costs.operation("put_fact", value.name)
    if atom.kind == "layer":
        return costs.operation("add_layer")
    if atom.kind == "seal":
        seal = cast(Seal, atom.value)
        scope = seal.carrier if isinstance(seal.carrier, QualifiedName) else None
        return costs.operation("seal", scope)
    raise ValueError(f"unknown graph-distance atom kind {atom.kind!r}")


def _atom_deletion(atom: _Atom, costs: CostTable) -> Decimal:
    """Return the one-atom deletion edge induced by graph primitives."""
    if atom.kind == "declaration":
        return costs.operation("undeclare", _declaration_scope_from_atom(atom.value))
    if atom.kind == "item":
        return costs.minimum_operation("remove_item")
    if atom.kind == "relation":
        declaration = cast(tuple[object, QualifiedName, object, object], atom.value)[1]
        return costs.operation("remove_relation", declaration)
    if atom.kind in {"boundary-value", "carrier-value"}:
        value = (
            atom.value
            if atom.kind == "boundary-value"
            else cast(_CarrierValueAtom, atom.value)[2]
        )
        return costs.operation("remove_attribute", cast(Attribute, value).name)
    if atom.kind == "fact":
        value = cast(tuple[object, Attribute], atom.value)[1]
        return costs.operation("remove_fact", value.name)
    if atom.kind == "layer":
        return costs.operation("remove_layer")
    if atom.kind == "seal":
        seal = cast(Seal, atom.value)
        scope = seal.carrier if isinstance(seal.carrier, QualifiedName) else None
        return costs.operation("drop_seal", scope)
    raise ValueError(f"unknown graph-distance atom kind {atom.kind!r}")


def _declaration_substitution(
    before: object, after: object, costs: CostTable
) -> Decimal | None:
    """Return a mutable declaration's one-atom closure, when names agree."""
    if not hasattr(before, "attributes") or not hasattr(after, "attributes"):
        return None
    left = cast(RelationDeclaration, before)
    right = cast(RelationDeclaration, after)
    if replace(left, attributes=()) != replace(right, attributes=()):
        return None
    return _attributes_transition(left.attributes, right.attributes, costs)


def _atom_substitution(before: _Atom, after: _Atom, costs: CostTable) -> Decimal:
    """Choose the cheapest direct transition or delete-then-insert path."""
    if before.kind != after.kind:
        raise ValueError("atom substitution requires atoms of the same kind")
    delete_insert = _atom_deletion(before, costs) + _atom_insertion(after, costs)
    if before.kind == "declaration":
        direct = _declaration_substitution(before.value, after.value, costs)
        return delete_insert if direct is None else min(delete_insert, direct)
    if before.kind == "item":
        left_id, left_values = cast(
            tuple[str | None, tuple[Attribute, ...]], before.value
        )
        right_id, right_values = cast(
            tuple[str | None, tuple[Attribute, ...]], after.value
        )
        promote = min(
            costs.minimum_operation("promote_item"),
            costs.minimum_operation("promote_boundary"),
        )
        identity = _identity_transition(
            left_id,
            right_id,
            promote,
            costs.minimum_operation("demote_item"),
        )
        values = min(
            costs.minimum_operation("replace_item"),
            _attributes_transition(left_values, right_values, costs),
        )
        return min(delete_insert, identity + values)
    if before.kind == "relation":
        left_kind, left_declaration, left_id, left_values = cast(
            _RelationAtomValue, before.value
        )
        right_kind, right_declaration, right_id, right_values = cast(
            _RelationAtomValue, after.value
        )
        if (left_kind, left_declaration) != (right_kind, right_declaration):
            return delete_insert
        identity = _identity_transition(
            left_id,
            right_id,
            costs.operation("promote_relation", left_declaration),
            costs.operation("demote_relation", left_declaration),
        )
        values = _attributes_transition(left_values, right_values, costs)
        return min(delete_insert, identity + values)
    if before.kind == "boundary-value":
        return min(
            delete_insert,
            _attribute_transition(
                cast(Attribute, before.value), cast(Attribute, after.value), costs
            ),
        )
    if before.kind == "fact":
        left_layer, left_value = cast(tuple[object, Attribute], before.value)
        right_layer, right_value = cast(tuple[object, Attribute], after.value)
        if left_layer != right_layer or left_value.name != right_value.name:
            return delete_insert
        value_cost = (
            Decimal(0)
            if costs.value_substitution is not _unit_value_substitution
            else costs.substitute_value(left_value, right_value)
        )
        return min(
            delete_insert,
            costs.operation("put_fact", right_value.name),
            value_cost,
        )
    if before.kind == "carrier-value":
        left_kind, left_carrier, left_value = cast(_CarrierValueAtom, before.value)
        right_kind, right_carrier, right_value = cast(_CarrierValueAtom, after.value)
        if (left_kind, left_carrier) != (right_kind, right_carrier):
            return delete_insert
        return min(
            delete_insert,
            _attribute_transition(left_value, right_value, costs),
        )
    if before.kind == "seal":
        left = cast(Seal, before.value)
        right = cast(Seal, after.value)
        if left.carrier != right.carrier:
            return delete_insert
        declaration = left.carrier if isinstance(left.carrier, QualifiedName) else None
        operation = "seal" if left.sealed < right.sealed else "unseal"
        return min(delete_insert, costs.operation(operation, declaration))
    if before.kind == "layer":
        return delete_insert
    raise AssertionError("known atom kinds are handled above")  # pragma: no cover


def _uncancelled_atoms(
    source: Counter[_Atom], target: Counter[_Atom], kind: str
) -> tuple[list[_Atom], list[_Atom]]:
    """Expand only unequal occurrences of one atom kind after exact cancellation."""
    left: list[_Atom] = []
    right: list[_Atom] = []
    for atom in source.keys() | target.keys():
        if atom.kind != kind:
            continue
        difference = source[atom] - target[atom]
        if difference > 0:
            left.extend([atom] * difference)
        elif difference < 0:
            right.extend([atom] * -difference)
    return left, right


def _atom_kind_distance(
    source: list[_Atom],
    target: list[_Atom],
    costs: CostTable,
) -> Decimal:
    """Price one canceled atom kind without unbounded cubic assignment work."""
    if not source:
        return sum((_atom_insertion(atom, costs) for atom in target), Decimal(0))
    if not target:
        return sum((_atom_deletion(atom, costs) for atom in source), Decimal(0))
    if len(source) + len(target) > _EXACT_ATOM_ASSIGNMENT_LIMIT:
        operation_costs = tuple(
            cast(Decimal, value) for value in costs.operations.values()
        )
        scoped_costs = tuple(
            cast(Decimal, value)
            for values in costs.declarations.values()
            for value in values.values()
        )
        value_floor = (
            Decimal(1)
            if costs.value_substitution is _unit_value_substitution
            else Decimal(0)
        )
        floor = min(*operation_costs, *scoped_costs, value_floor)
        return Decimal(max(len(source), len(target))) * floor
    removed = len(source)
    inserted = len(target)
    matrix = [
        [
            *(_atom_substitution(before, after, costs) for after in target),
            *(_atom_deletion(before, costs) for _ in range(removed)),
        ]
        for before in source
    ]
    matrix.extend(
        [
            *(_atom_insertion(after, costs) for after in target),
            *(Decimal(0) for _ in range(removed)),
        ]
        for _ in range(inserted)
    )
    return _minimum_assignment_cost(matrix)


def _atom_multiset_lower_bound(
    source: Graph,
    target: Graph,
    costs: CostTable,
    view: EquivalenceView,
) -> Decimal:
    """Return the incidence-free atom-multiset relaxation of graph distance.

    The projection retains complete declaration and item payloads, relation
    payloads without endpoints, boundary values without addresses, facts
    without subjects, and layer, seal, and other carrier-value atoms. It omits
    item order, relation incidence, and fact attachment. Every primitive changes
    at most one retained atom. Small unmatched sets use the minimum of direct
    one-atom transitions and delete-then-insert paths. Large sets use their
    unavoidable atom count at the least declared transition cost.
    """
    if PRIMITIVE_KINDS - costs.operations.keys():
        # A partial table can price a realized script without defining every
        # transition used by the general relaxation. Zero remains sound and
        # does not make unrelated operation costs mandatory.
        return Decimal(0)
    left = _graph_atoms(source, view)
    right = _graph_atoms(target, view)
    kinds = {atom.kind for atom in left} | {atom.kind for atom in right}
    return sum(
        (
            _atom_kind_distance(
                *_uncancelled_atoms(left, right, kind),
                costs,
            )
            for kind in kinds
        ),
        start=Decimal(0),
    )


def _general_graph_lower_bound(
    source: Graph,
    target: Graph,
    costs: CostTable,
    view: EquivalenceView,
) -> tuple[Decimal, str]:
    """Return the strongest built-in general-graph relaxation proved here.

    A later relaxation can join this seam by taking the maximum only after it
    has its own primitive-wise domination proof.
    """
    return _atom_multiset_lower_bound(source, target, costs, view), "atom-multiset"


def _relation_declaration(graph: Graph, target: object) -> QualifiedName | None:
    """Resolve one relation target to the declaration that prices it."""
    if isinstance(target, int) and not isinstance(target, bool):
        return graph.relations[target].declaration
    if isinstance(target, RelationInstanceRef):
        return graph.relations[target.index].declaration
    if isinstance(target, PolyadicInstanceRef):
        return graph.polyadic_relations[target.index].declaration
    durable_id = (
        target
        if isinstance(target, str)
        else (
            target.durable_id
            if isinstance(target, DurableRelationRef | DurablePolyadicRef)
            else None
        )
    )
    if durable_id is not None:
        for relation in graph.relations:
            if relation.durable_id == durable_id:
                return relation.declaration
        for polyadic_relation in graph.polyadic_relations:
            if polyadic_relation.durable_id == durable_id:
                return polyadic_relation.declaration
    return None


def _operation_declaration(
    opcode: DeltaOpcode, graph: Graph | None = None
) -> QualifiedName | None:
    """Read the declaration whose override prices one checked edit call."""
    if not opcode.calls or not opcode.calls[0].arguments:
        return None
    call = opcode.calls[0]
    arguments = call.arguments
    argument = arguments[0]
    if call.method == "set_attribute":
        return cast(Attribute, arguments[1]).name
    if call.method == "remove_attribute":
        return cast(QualifiedName, arguments[1])
    if call.method == "put_fact":
        value = getattr(arguments[1], "value", None)
        name = getattr(value, "name", None)
        return name if isinstance(name, QualifiedName) else None
    if call.method == "remove_fact":
        return cast(QualifiedName, arguments[2])
    if call.method == "add_relation":
        declaration = getattr(argument, "declaration", None)
        return declaration if isinstance(declaration, QualifiedName) else None
    if call.method in {
        "remove_relation",
        "set_endpoints",
        "promote_relation",
        "demote_relation",
    }:
        return None if graph is None else _relation_declaration(graph, argument)
    if call.method in {
        "insert_item",
        "insert_items",
        "remove_items",
    }:
        return argument if isinstance(argument, QualifiedName) else None
    if call.method in {
        "remove_item",
        "replace_item",
        "move_item",
        "shift",
        "swap_items",
        "promote_item",
        "demote_item",
    }:
        if isinstance(argument, ItemRef):
            return argument.tier
        if graph is not None and isinstance(argument, DurableItemRef):
            return graph.resolve_item(argument).tier
        return None
    if call.method in {"move_run", "swap_runs"}:
        tier = getattr(argument, "tier", None)
        return tier if isinstance(tier, QualifiedName) else None
    if call.method in {"promote_boundary", "demote_boundary"}:
        if isinstance(argument, BoundaryRef):
            return argument.tier
        if graph is not None and isinstance(argument, DurableBoundaryRef):
            return graph.resolve_boundary(argument).tier
        return None
    if isinstance(argument, QualifiedName):
        return argument
    name = getattr(argument, "name", None)
    return name if isinstance(name, QualifiedName) else None


def _delta_primitive_kind(opcode: DeltaOpcode) -> str:
    """Return the one primitive performed by a residue-free delta opcode."""
    if len(opcode.calls) != 1 or opcode.changes:
        raise ValueError("a priced edit delta must contain one call and no residue")
    method = opcode.calls[0].method
    if method in {"insert_items", "remove_items"}:
        raise ValueError(
            f"{method!r} is a multi-item native operation, not one priced primitive"
        )
    kind = _COST_KIND_ALIASES.get(method, method)
    if kind not in PRIMITIVE_KINDS:  # pragma: no cover - closed call vocabulary
        raise ValueError(f"{method!r} is not a declared graph editing primitive")
    return kind


def price_patch(patch: Patch, costs: CostTable, source: Graph | None = None) -> Decimal:
    """Return the cost of a realized patch containing only declared operations.

    Residual document changes and multi-call native edits are not one declared
    primitive and are refused. Supplying ``source`` first replays the complete
    patch, establishing that the priced operations are a realized script, and
    resolves declaration-specific costs for structural and durable references.
    """
    if source is not None:
        patch.apply(source)
    total = Decimal(0)
    cursor = source
    for index, operation in enumerate(patch.operations):
        opcode = operation.opcode
        if isinstance(opcode, DeltaOpcode):
            try:
                kind = _delta_primitive_kind(opcode)
            except ValueError as error:
                raise ValueError(f"patch operation {index}: {error}") from error
            total += costs.operation(kind, _operation_declaration(opcode, cursor))
        else:
            name = opcode.to_data().get("opcode")
            if not isinstance(name, str):  # pragma: no cover - opcode invariant
                raise TypeError("patch opcode has no string operation name")
            declaration = getattr(opcode, "tier", None)
            total += costs.operation(
                _COST_KIND_ALIASES.get(name, name),
                declaration if isinstance(declaration, QualifiedName) else None,
            )
        if cursor is not None:
            cursor = opcode.apply(cursor)
    return total


def _item_key(item: Item, view: EquivalenceView) -> object:
    """Return item content visible in one equivalence view."""
    if view is EquivalenceView.FUNCTIONAL:
        return item.attributes
    return item


def _item_substitution(
    before: Item,
    after: Item,
    view: EquivalenceView,
    costs: CostTable,
    declaration: QualifiedName | None = None,
) -> Decimal:
    """Return the cheapest declared in-place item-value update."""
    if _item_key(before, view) == _item_key(after, view):
        return Decimal(0)
    replace_cost = costs.operation("replace_item", declaration)
    if before.durable_id != after.durable_id and view is not EquivalenceView.FUNCTIONAL:
        identity = Decimal(0)
        if before.durable_id is not None:
            identity += costs.operation("demote_item", declaration)
        if after.durable_id is not None:
            identity += costs.operation("promote_item", declaration)
    else:
        identity = Decimal(0)
    before_values = {value.name: value for value in before.attributes}
    after_values = {value.name: value for value in after.attributes}
    attributes = Decimal(0)
    for name in before_values.keys() - after_values.keys():
        attributes += costs.operation("remove_attribute", name)
    for name in after_values.keys() - before_values.keys():
        attributes += costs.operation("set_attribute", name)
    for name in before_values.keys() & after_values.keys():
        if before_values[name] != after_values[name]:
            attributes += min(
                costs.operation("set_attribute", name),
                costs.substitute_value(before_values[name], after_values[name]),
            )
    return identity + min(replace_cost, attributes)


def _item_substitution_for(
    view: EquivalenceView, costs: CostTable, declaration: QualifiedName
) -> SubstitutionCost[Item]:
    """Bind one tier's declaration to its item-substitution cost."""

    def substitute(before: Item, after: Item) -> Decimal:
        """Price one item pair against the bound tier declaration."""
        return _item_substitution(before, after, view, costs, declaration)

    return substitute


def _independent_sequence_distance(
    source: Graph,
    target: Graph,
    view: EquivalenceView,
    costs: CostTable,
) -> Decimal | None:
    """Return an exact sum when graphs differ only within independent tiers."""
    if any(
        (
            source.relations,
            target.relations,
            source.polyadic_relations,
            target.polyadic_relations,
            source.boundary_values,
            target.boundary_values,
            source.layers,
            target.layers,
            source.seals,
            target.seals,
        )
    ):
        return None
    if len(source.tiers) != len(target.tiers):
        return None
    blank_source = replace(
        source, tiers=tuple(replace(tier, items=()) for tier in source.tiers)
    )
    blank_target = replace(
        target, tiers=tuple(replace(tier, items=()) for tier in target.tiers)
    )
    if not equivalent(blank_source, blank_target, view):
        return None
    total = Decimal(0)
    for source_tier, target_tier in zip(source.tiers, target.tiers, strict=True):
        declaration = source_tier.declaration.name
        insertion = costs.operation("insert_item", declaration)
        deletion = costs.operation("remove_item", declaration)
        if costs.operation("move_item", declaration) < insertion + deletion:
            return None
        if costs.operation("swap_items", declaration) < insertion + deletion:
            return None
        total += weighted_sequence_distance(
            source_tier.items,
            target_tier.items,
            insert=insertion,
            delete=deletion,
            substitute=_item_substitution_for(view, costs, declaration),
        )
    return total


def _declaration_scope(declaration: EditDeclaration) -> QualifiedName | None:
    """Return the cost-table scope for one declaration edit."""
    return None if isinstance(declaration, NamespaceDeclaration) else declaration.name


def _without_relation_attributes(
    declaration: RelationDeclaration,
) -> RelationDeclaration:
    """Return a relation declaration whose values can be restored later."""
    return replace(declaration, attributes=())


def _undeclare_all(
    graph: Graph,
    declarations: Sequence[EditDeclaration],
    costs: CostTable,
    *,
    atomic_components: bool = False,
) -> tuple[Graph, Decimal]:
    """Undeclare an acyclic dependency set in an order accepted by the editor."""
    cursor = graph
    total = Decimal(0)
    pending = list(declarations)
    while pending:
        for declaration in tuple(pending):
            try:
                candidate = cursor.undeclare(declaration)
            except GraphValidationError:
                continue
            cursor = candidate
            total += costs.operation("undeclare", _declaration_scope(declaration))
            pending.remove(declaration)
            break
        else:
            if atomic_components:
                candidate = undeclare_with_contents(cursor, pending[0])
                live_relations = {
                    declaration.name for declaration in candidate.relation_declarations
                }
                removed = [
                    declaration
                    for declaration in pending
                    if not isinstance(declaration, NamespaceDeclaration)
                    and declaration.name not in live_relations
                ]
                if not removed:  # pragma: no cover - cascade removes its target
                    raise GraphValidationError(
                        "atomic undeclare left its selected declaration in place"
                    )
                cursor = candidate
                total += sum(
                    (
                        costs.operation("undeclare", _declaration_scope(declaration))
                        for declaration in removed
                    ),
                    start=Decimal(0),
                )
                pending = [
                    declaration for declaration in pending if declaration not in removed
                ]
                continue
            raise GraphValidationError(
                "dependency-ordered rebuild could not undeclare the remaining schema"
            )
    return cursor, total


def _declare_relations(
    graph: Graph,
    declarations: Sequence[RelationDeclaration],
    costs: CostTable,
) -> tuple[Graph, Decimal]:
    """Declare relation schemas in an order accepted by their dependencies."""
    cursor = graph
    total = Decimal(0)
    pending = list(declarations)
    while pending:
        for declaration in tuple(pending):
            try:
                candidate = cursor.declare(declaration)
            except GraphValidationError:
                continue
            cursor = candidate
            total += costs.operation("declare", declaration.name)
            pending.remove(declaration)
            break
        else:
            editor = cursor.edit()
            for declaration in pending:
                editor.declare(declaration)
            cursor = editor.freeze()
            total += sum(
                (
                    costs.operation("declare", declaration.name)
                    for declaration in pending
                ),
                start=Decimal(0),
            )
            pending.clear()
    return cursor, total


def _rebuild_upper_bound(source: Graph, target: Graph, costs: CostTable) -> Decimal:
    """Execute and price a dependency-ordered dismantle and rebuild script."""
    cursor = source
    total = Decimal(0)

    def advance(
        candidate: Graph, kind: str, declaration: QualifiedName | None = None
    ) -> None:
        """Accept one validated primitive and add its declared cost."""
        nonlocal cursor, total
        cursor = candidate
        total += costs.operation(kind, declaration)

    for seal in source.seals:
        advance(
            cursor.drop_seal(seal.carrier),
            "drop_seal",
            seal.carrier if isinstance(seal.carrier, QualifiedName) else None,
        )
    for layer in source.layers:
        for fact in layer.facts:
            advance(
                cursor.remove_fact(layer.name, fact.subject, fact.value.name),
                "remove_fact",
                fact.value.name,
            )
    for layer in source.layers:
        advance(cursor.remove_layer(layer.name), "remove_layer")
    for index in reversed(range(len(source.polyadic_relations))):
        polyadic_relation = source.polyadic_relations[index]
        advance(
            cursor.remove_relation(PolyadicInstanceRef(index)),
            "remove_relation",
            polyadic_relation.declaration,
        )
    for index in reversed(range(len(source.relations))):
        binary_relation = source.relations[index]
        advance(
            cursor.remove_relation(RelationInstanceRef(index)),
            "remove_relation",
            binary_relation.declaration,
        )
    for boundary in source.boundary_values:
        for value in boundary.attributes:
            advance(
                cursor.remove_attribute(boundary.reference, value.name),
                "remove_attribute",
                value.name,
            )
    for value in source.attributes:
        advance(
            cursor.remove_attribute(None, value.name), "remove_attribute", value.name
        )
    for tier in source.tiers:
        for value in tier.attributes:
            advance(
                cursor.remove_attribute(tier.declaration.name, value.name),
                "remove_attribute",
                value.name,
            )
    bare_source_relations: list[RelationDeclaration] = []
    for declaration in source.relation_declarations:
        for value in declaration.attributes:
            advance(
                cursor.remove_attribute(declaration.name, value.name),
                "remove_attribute",
                value.name,
            )
        bare_source_relations.append(_without_relation_attributes(declaration))
    for tier in source.tiers:
        for index in reversed(range(len(tier.items))):
            advance(
                cursor.remove_item(ItemRef(tier.declaration.name, index)),
                "remove_item",
                tier.declaration.name,
            )
    cursor, cost = _undeclare_all(cursor, source.attribute_declarations, costs)
    total += cost
    cursor, cost = _undeclare_all(
        cursor, bare_source_relations, costs, atomic_components=True
    )
    total += cost
    cursor, cost = _undeclare_all(
        cursor, tuple(tier.declaration for tier in source.tiers), costs
    )
    total += cost
    cursor, cost = _undeclare_all(cursor, source.namespaces, costs)
    total += cost

    for namespace in target.namespaces:
        advance(cursor.declare(namespace), "declare")
    for index, tier in enumerate(target.tiers):
        advance(
            cursor.declare(tier.declaration, at=index), "declare", tier.declaration.name
        )
    bare_target_relations = tuple(
        _without_relation_attributes(declaration)
        for declaration in target.relation_declarations
    )
    cursor, cost = _declare_relations(cursor, bare_target_relations, costs)
    total += cost
    for attribute_declaration in target.attribute_declarations:
        advance(
            cursor.declare(attribute_declaration),
            "declare",
            attribute_declaration.name,
        )
    for tier in target.tiers:
        for index, item in enumerate(tier.items):
            advance(
                cursor.insert_item(tier.declaration.name, index, item),
                "insert_item",
                tier.declaration.name,
            )
        for value in tier.attributes:
            advance(
                cursor.set_attribute(tier.declaration.name, value),
                "set_attribute",
                value.name,
            )
    for declaration in target.relation_declarations:
        for value in declaration.attributes:
            advance(
                cursor.set_attribute(declaration.name, value),
                "set_attribute",
                value.name,
            )
    for value in target.attributes:
        advance(cursor.set_attribute(None, value), "set_attribute", value.name)
    for boundary in target.boundary_values:
        for value in boundary.attributes:
            advance(
                cursor.set_attribute(boundary.reference, value),
                "set_attribute",
                value.name,
            )
    for binary_relation in target.relations:
        advance(
            cursor.add_relation(binary_relation),
            "add_relation",
            binary_relation.declaration,
        )
    for polyadic_relation in target.polyadic_relations:
        advance(
            cursor.add_relation(polyadic_relation),
            "add_relation",
            polyadic_relation.declaration,
        )
    for layer in target.layers:
        advance(cursor.add_layer(layer.name), "add_layer")
        for fact in layer.facts:
            advance(cursor.put_fact(layer.name, fact), "put_fact", fact.value.name)
    for seal in target.seals:
        advance(
            cursor.seal(seal.carrier, seal.sealed),
            "seal",
            seal.carrier if isinstance(seal.carrier, QualifiedName) else None,
        )
    if cursor != target:  # pragma: no cover - guarded by construction tests
        raise GraphValidationError(
            "dependency-ordered rebuild did not reproduce the target graph"
        )
    return total


def graph_distance(
    source: Graph,
    target: Graph,
    costs: CostTable | None = None,
    *,
    view: EquivalenceView | str = EquivalenceView.FUNCTIONAL,
) -> DistanceInterval:
    """Return exact distance where proved, otherwise named graph-edit bounds.

    Independent ordered tiers use exact weighted sequence distance when move and
    swap shortcuts cannot undercut insertion plus deletion. General graphs use
    the incidence-free atom-multiset relaxation as a lower bound and the cost
    of an executable diff as an upper bound. If the diff contains a residual
    data delta, a conservative dependency-ordered rebuild supplies the upper
    bound. Overlapping and non-nesting relations therefore receive an interval
    rather than an unsupported exact graph-edit claim.
    """
    if not isinstance(source, Graph) or not isinstance(target, Graph):
        raise TypeError("graph distance endpoints must be Graph values")
    active_costs = UNIT_COSTS if costs is None else costs
    selected_view = EquivalenceView(view)
    if equivalent(source, target, selected_view):
        return DistanceInterval(Decimal(0), Decimal(0), True, "equivalent")
    exact = _independent_sequence_distance(source, target, selected_view, active_costs)
    if exact is not None:
        return DistanceInterval(exact, exact, True, "ordered-tiers")
    lower, lower_method = _general_graph_lower_bound(
        source, target, active_costs, selected_view
    )
    patch = diff(source, target, selected_view)
    try:
        upper = price_patch(patch, active_costs, source)
        method = "realized-diff"
    except ValueError:
        upper = _rebuild_upper_bound(source, target, active_costs)
        method = "realized-rebuild"
    if lower > upper:  # pragma: no cover - primitive-wise proof makes this defensive
        raise ValueError(
            "the atom-multiset lower bound exceeds the realized graph-edit upper bound"
        )
    return DistanceInterval(lower, upper, False, method, lower_method)


def distance(
    source: Graph,
    target: Graph,
    costs: CostTable | None = None,
    *,
    view: EquivalenceView | str = EquivalenceView.FUNCTIONAL,
) -> DistanceInterval:
    """Return graph-edit distance as a synonym for :func:`graph_distance`."""
    return graph_distance(source, target, costs, view=view)


__all__ = [
    "PRIMITIVE_KINDS",
    "UNIT_COSTS",
    "CostTable",
    "DistanceInterval",
    "OrderedTree",
    "SequenceProjection",
    "contiguous_segmentation_distance",
    "distance",
    "format_control_insensitive_projection",
    "graph_distance",
    "ordered_tree_distance",
    "price_patch",
    "text_projection",
    "weighted_sequence_distance",
    "whitespace_insensitive_projection",
]
