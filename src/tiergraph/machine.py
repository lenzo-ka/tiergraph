"""Checked opcodes and deterministic lowering for tiergraph graphs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Self, cast

from tiergraph.core import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    DurableItemRef,
    DurablePositionRef,
    Graph,
    Item,
    ItemRef,
    JsonValue,
    NamespaceDeclaration,
    PolyadicRelationDeclaration,
    Position,
    PositionRef,
    QualifiedName,
    RelationDeclaration,
    RelationInstance,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
)

MACHINE_VERSION = "1"
MAX_REPEAT_COUNT = 10_000
# Owner-tunable policy: bound eager traces while leaving ample room for real builds.
MAX_TOTAL_OPCODES = 2_000_000


class ExecutionError(ValueError):
    """Name the opcode that could not make its checked state transition."""


@dataclass(frozen=True, slots=True)
class DeclareNamespace:
    """Declare one namespace binding."""

    declaration: NamespaceDeclaration

    def apply(self, graph: Graph) -> Graph:
        """Append the binding through graph validation."""
        return _replace(graph, namespaces=(*graph.namespaces, self.declaration))

    def to_data(self) -> dict[str, JsonValue]:
        """Return the opcode as JSON data."""
        return {
            "opcode": "declare_namespace",
            "declaration": self.declaration.to_data(),
        }


@dataclass(frozen=True, slots=True)
class DeclareTier:
    """Declare one empty ordered tier."""

    declaration: TierDeclaration

    def apply(self, graph: Graph) -> Graph:
        """Append the empty tier through graph validation."""
        return _replace(graph, tiers=(*graph.tiers, Tier(self.declaration)))

    def to_data(self) -> dict[str, JsonValue]:
        """Return the opcode as JSON data."""
        return {"opcode": "declare_tier", "declaration": self.declaration.to_data()}


@dataclass(frozen=True, slots=True)
class DeclareRelation:
    """Declare a simple membership or bipartite relation."""

    declaration: RelationDeclaration

    def apply(self, graph: Graph) -> Graph:
        """Append the declaration through graph validation."""
        return _replace(
            graph,
            relation_declarations=(*graph.relation_declarations, self.declaration),
        )

    def to_data(self) -> dict[str, JsonValue]:
        """Return the opcode as JSON data."""
        return {"opcode": "declare_relation", "declaration": self.declaration.to_data()}


@dataclass(frozen=True, slots=True)
class DeclareAttribute:
    """Declare one typed attribute and its attachment domain."""

    declaration: AttributeDeclaration

    def apply(self, graph: Graph) -> Graph:
        """Append the declaration through graph validation."""
        return _replace(
            graph,
            attribute_declarations=(*graph.attribute_declarations, self.declaration),
        )

    def to_data(self) -> dict[str, JsonValue]:
        """Return the opcode as JSON data."""
        return {
            "opcode": "declare_attribute",
            "declaration": self.declaration.to_data(),
        }


@dataclass(frozen=True, slots=True)
class AddItem:
    """Append one item to a declared tier."""

    tier: QualifiedName
    item: Item = Item()

    def apply(self, graph: Graph) -> Graph:
        """Append the item, refusing an unknown tier or invalid identity."""
        found = False
        tiers: list[Tier] = []
        for candidate in graph.tiers:
            if candidate.declaration.name == self.tier:
                found = True
                candidate = Tier(
                    candidate.declaration,
                    (*candidate.items, self.item),
                    candidate.attributes,
                )
            tiers.append(candidate)
        if not found:
            raise ValueError(f"item tier {str(self.tier)!r} is not declared")
        return _replace(graph, tiers=tuple(tiers))

    def to_data(self) -> dict[str, JsonValue]:
        """Return the opcode as JSON data."""
        return {
            "opcode": "add_item",
            "tier": self.tier.to_data(),
            "item": self.item.to_data(),
        }


@dataclass(frozen=True, slots=True)
class PromoteItem:
    """Promote one structural item reference to durable identity."""

    reference: ItemRef
    durable_id: str

    def apply(self, graph: Graph) -> Graph:
        """Apply the kernel's checked promotion operation."""
        promoted, _ = graph.promote_item(self.reference, self.durable_id)
        return promoted

    def to_data(self) -> dict[str, JsonValue]:
        """Return the opcode as JSON data."""
        return {
            "opcode": "promote_item",
            "reference": self.reference.to_data(),
            "durable_id": self.durable_id,
        }


@dataclass(frozen=True, slots=True)
class PromotePosition:
    """Promote one structural boundary reference to anchored identity."""

    reference: PositionRef
    durable_id: str

    def apply(self, graph: Graph) -> Graph:
        """Apply the kernel's checked boundary promotion operation."""
        promoted, _ = graph.promote_position(self.reference, self.durable_id)
        return promoted

    def to_data(self) -> dict[str, JsonValue]:
        """Return the opcode as JSON data."""
        return {
            "opcode": "promote_position",
            "reference": self.reference.to_data(),
            "durable_id": self.durable_id,
        }


@dataclass(frozen=True, slots=True)
class Relate:
    """Add one instance of a declared bipartite relation."""

    relation: RelationInstance

    def apply(self, graph: Graph) -> Graph:
        """Append the instance through endpoint and invariant validation."""
        return _replace(graph, relations=(*graph.relations, self.relation))

    def to_data(self) -> dict[str, JsonValue]:
        """Return the opcode as JSON data."""
        return {"opcode": "relate", "relation": self.relation.to_data()}


type AttributeTarget = (
    None
    | QualifiedName
    | ItemRef
    | DurableItemRef
    | PositionRef
    | DurablePositionRef
    | int
)


@dataclass(frozen=True, slots=True)
class AttachValue:
    """Attach a typed value to an owner in its declared attribute domain."""

    domain: AttributeDomain
    target: AttributeTarget
    value: AttributeValue

    def apply(self, graph: Graph) -> Graph:
        """Replace the named owner and let graph construction check the value."""
        if self.domain is AttributeDomain.DOCUMENT:
            _require_target(self.target, None, self.domain)
            return _replace(graph, attributes=(*graph.attributes, self.value))
        if self.domain is AttributeDomain.TIER:
            target = _qualified_target(self.target, self.domain)
            tiers = _map_tier(
                graph,
                target,
                lambda tier: Tier(
                    tier.declaration, tier.items, (*tier.attributes, self.value)
                ),
            )
            return _replace(graph, tiers=tiers)
        if self.domain is AttributeDomain.ITEM:
            reference = _item_target(self.target, self.domain)
            return _replace(graph, tiers=_attach_item(graph, reference, self.value))
        if self.domain is AttributeDomain.RELATION_DECLARATION:
            target = _qualified_target(self.target, self.domain)
            return _replace(
                graph,
                relation_declarations=_attach_relation_declaration(
                    graph, target, self.value
                ),
            )
        if self.domain is AttributeDomain.RELATION_INSTANCE:
            index = _index_target(self.target, self.domain, len(graph.relations))
            relations = list(graph.relations)
            relation = relations[index]
            relations[index] = RelationInstance(
                relation.declaration,
                relation.left,
                relation.right,
                relation.durable_id,
                (*relation.attributes, self.value),
            )
            return _replace(graph, relations=tuple(relations))
        position_reference = _position_target(self.target, self.domain)
        position_coordinate = graph.resolve_position(position_reference)
        positions = list(graph.position_values)
        for index, position in enumerate(positions):
            if graph.resolve_position(position.reference) == position_coordinate:
                positions[index] = Position(
                    position.reference, (*position.attributes, self.value)
                )
                break
        else:
            positions.append(Position(position_reference, (self.value,)))
        return _replace(graph, position_values=tuple(positions))

    def to_data(self) -> dict[str, JsonValue]:
        """Return the opcode as JSON data."""
        target: JsonValue
        if self.target is None or isinstance(self.target, int):
            target = self.target
        else:
            target = self.target.to_data()
        return {
            "opcode": "attach_value",
            "domain": self.domain.value,
            "target": target,
            "value": self.value.to_data(),
        }


@dataclass(frozen=True, slots=True)
class Repeat:
    """Repeat a finite block without adding a primitive consume-tier opcode."""

    count: int
    body: tuple[Opcode, ...]

    def __post_init__(self) -> None:
        """Refuse values that do not prove a finite nonnegative expansion."""
        if type(self.count) is not int or self.count < 0:
            raise ValueError(
                f"repeat count {self.count!r} must be a nonnegative integer"
            )
        if self.count > MAX_REPEAT_COUNT:
            raise ValueError(
                f"repeat count {self.count!r} exceeds limit {MAX_REPEAT_COUNT}"
            )

    def to_data(self) -> dict[str, JsonValue]:
        """Return the procedural opcode as JSON data."""
        return {
            "opcode": "repeat",
            "count": self.count,
            "body": [opcode.to_data() for opcode in self.body],
        }


type PrimitiveOpcode = (
    DeclareNamespace
    | DeclareTier
    | DeclareRelation
    | DeclareAttribute
    | AddItem
    | PromoteItem
    | PromotePosition
    | Relate
    | AttachValue
)
type Opcode = PrimitiveOpcode | Repeat

_PRIMITIVE_OPCODE_TYPES = (
    DeclareNamespace,
    DeclareTier,
    DeclareRelation,
    DeclareAttribute,
    AddItem,
    PromoteItem,
    PromotePosition,
    Relate,
    AttachValue,
)


@dataclass(frozen=True, slots=True)
class Step:
    """Record one primitive opcode and its validated resulting graph."""

    index: int
    opcode: PrimitiveOpcode
    graph: Graph

    def to_data(self) -> dict[str, JsonValue]:
        """Return the step as JSON-serializable data (index, opcode, graph)."""
        return {
            "index": self.index,
            "opcode": self.opcode.to_data(),
            "graph": self.graph.to_data(),
        }


@dataclass(frozen=True, slots=True, eq=False)
class Program:
    """Carry source opcodes while defining identity on their checked outcome."""

    opcodes: tuple[Opcode, ...]

    def __post_init__(self) -> None:
        """Refuse source procedures whose flattened trace exceeds policy."""
        _primitive_count(self.opcodes)

    def unroll(self) -> AsBuilt:
        """Lower procedures iteratively and execute every primitive transition."""
        trace = _flatten(self.opcodes)
        return AsBuilt(execute(trace), trace)

    def fingerprint(self) -> str:
        """Hash the canonical JSON data of the as-built graph."""
        return self.unroll().fingerprint()

    def __eq__(self, other: object) -> bool:
        """Compare programs only by their as-built graph values."""
        if isinstance(other, Program | AsBuilt):
            return self.unroll().graph == other.unroll().graph
        return NotImplemented

    def __hash__(self) -> int:
        """Hash the same as-built identity used by equality."""
        return hash(self.fingerprint())


@dataclass(frozen=True, slots=True, eq=False)
class AsBuilt:
    """Pair a checked graph with its finite primitive consume-tier trace."""

    graph: Graph
    trace: tuple[PrimitiveOpcode, ...]

    def __post_init__(self) -> None:
        """Require the trace to execute to the graph it claims to construct."""
        rebuilt = execute(self.trace)
        if rebuilt != self.graph:
            raise ValueError("as-built trace does not execute to its graph")

    def unroll(self) -> Self:
        """Return this already lowered outcome unchanged."""
        return self

    def fingerprint(self) -> str:
        """Return a SHA-256 fingerprint of canonical as-built state bytes."""
        encoded = json.dumps(
            self.graph.to_data(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def to_data(self) -> dict[str, JsonValue]:
        """Return the machine version and graph as JSON-serializable data."""
        return {"machine_version": MACHINE_VERSION, "graph": self.graph.to_data()}

    def __eq__(self, other: object) -> bool:
        """Compare outcomes by graph state rather than construction trace."""
        if isinstance(other, Program | AsBuilt):
            return self.graph == other.unroll().graph
        return NotImplemented

    def __hash__(self) -> int:
        """Hash the same graph identity used by equality."""
        return hash(self.fingerprint())


def execute(opcodes: Iterable[object]) -> Graph:
    """Execute primitives in order and name the first refused opcode.

    Drives the same ``steps`` generator a debugger walks and returns its final
    graph, so execution and stepping are one path: the debugger observes exactly
    what runs, and the two cannot diverge.
    """
    graph = Graph((), (), ())
    for step in steps(opcodes):
        graph = step.graph
    return graph


def steps(source: Program | AsBuilt | Iterable[object]) -> Iterator[Step]:
    """Yield each primitive opcode with its validated resulting graph."""
    if isinstance(source, Program):
        opcodes: Iterable[object] = _flatten(source.opcodes)
    elif isinstance(source, AsBuilt):
        opcodes = source.trace
    else:
        opcodes = source

    graph = Graph((), (), ())
    for index, opcode in enumerate(opcodes):
        graph = _apply_opcode(graph, index, opcode)
        yield Step(index, cast(PrimitiveOpcode, opcode), graph)


def _apply_opcode(graph: Graph, index: int, opcode: object) -> Graph:
    try:
        if type(opcode) not in _PRIMITIVE_OPCODE_TYPES:
            raise TypeError(f"unrecognized opcode type {type(opcode).__name__!r}")
        result = cast(PrimitiveOpcode, opcode).apply(graph)
        if not isinstance(result, Graph):
            raise TypeError(
                f"opcode returned {type(result).__name__!r}, expected Graph"
            )
        return _validate_graph(result)
    except Exception as error:
        raise ExecutionError(
            f"opcode {index} {_opcode_data(opcode)!r} refused: {error}"
        ) from error


def _flatten(opcodes: tuple[Opcode, ...]) -> tuple[PrimitiveOpcode, ...]:
    flattened: list[PrimitiveOpcode] = []
    stack: list[tuple[tuple[object, ...], int]] = [(opcodes, 0)]
    while stack:
        block, index = stack.pop()
        if index == len(block):
            continue
        opcode = block[index]
        stack.append((block, index + 1))
        if type(opcode) is Repeat:
            for _ in range(opcode.count):
                stack.append((opcode.body, 0))
        else:
            flattened.append(cast(PrimitiveOpcode, opcode))
    return tuple(flattened)


def _primitive_count(opcodes: tuple[Opcode, ...]) -> int:
    """Count a procedure tree with capped arithmetic and no recursive calls."""
    totals: dict[int, int] = {}
    stack: list[tuple[tuple[Opcode, ...], bool]] = [(opcodes, False)]
    while stack:
        block, visited = stack.pop()
        if not visited:
            stack.append((block, True))
            for opcode in block:
                if type(opcode) is Repeat:
                    stack.append((opcode.body, False))
            continue

        total = 0
        for opcode in block:
            contribution = 1
            if type(opcode) is Repeat:
                body_total = totals[id(opcode.body)]
                if body_total and opcode.count > MAX_TOTAL_OPCODES // body_total:
                    contribution = MAX_TOTAL_OPCODES + 1
                else:
                    contribution = opcode.count * body_total
            if contribution > MAX_TOTAL_OPCODES - total:
                raise ValueError(
                    f"total primitive opcode count exceeds limit {MAX_TOTAL_OPCODES}"
                )
            total += contribution
        totals[id(block)] = total
    return totals[id(opcodes)]


def _opcode_data(opcode: object) -> object:
    if type(opcode) in _PRIMITIVE_OPCODE_TYPES:
        try:
            return cast(PrimitiveOpcode, opcode).to_data()
        except Exception as error:
            return {
                "opcode": "unserializable",
                "type": type(opcode).__name__,
                "reason": str(error),
            }
    return {"opcode": "unrecognized", "type": type(opcode).__name__}


def _validate_graph(graph: Graph) -> Graph:
    return Graph(
        graph.namespaces,
        graph.tiers,
        graph.relation_declarations,
        graph.relations,
        graph.attribute_declarations,
        graph.position_values,
        graph.attributes,
    )


def _replace(
    graph: Graph,
    *,
    namespaces: tuple[NamespaceDeclaration, ...] | None = None,
    tiers: tuple[Tier, ...] | None = None,
    relation_declarations: tuple[RelationDeclaration, ...] | None = None,
    relations: tuple[RelationInstance, ...] | None = None,
    attribute_declarations: tuple[AttributeDeclaration, ...] | None = None,
    position_values: tuple[Position, ...] | None = None,
    attributes: tuple[AttributeValue, ...] | None = None,
) -> Graph:
    return Graph(
        graph.namespaces if namespaces is None else namespaces,
        graph.tiers if tiers is None else tiers,
        graph.relation_declarations
        if relation_declarations is None
        else relation_declarations,
        graph.relations if relations is None else relations,
        graph.attribute_declarations
        if attribute_declarations is None
        else attribute_declarations,
        graph.position_values if position_values is None else position_values,
        graph.attributes if attributes is None else attributes,
    )


def _require_target(
    actual: AttributeTarget, expected: None, domain: AttributeDomain
) -> None:
    if actual is not expected:
        raise ValueError(f"{domain.value} attribute target {actual!r} must be None")


def _qualified_target(
    target: AttributeTarget, domain: AttributeDomain
) -> QualifiedName:
    if not isinstance(target, QualifiedName):
        raise ValueError(
            f"{domain.value} attribute target {target!r} must be a qualified name"
        )
    return target


def _item_target(
    target: AttributeTarget, domain: AttributeDomain
) -> ItemRef | DurableItemRef:
    if not isinstance(target, ItemRef | DurableItemRef):
        raise ValueError(
            f"{domain.value} attribute target {target!r} must be an item reference"
        )
    return target


def _position_target(
    target: AttributeTarget, domain: AttributeDomain
) -> PositionRef | DurablePositionRef:
    if not isinstance(target, PositionRef | DurablePositionRef):
        raise ValueError(
            f"{domain.value} attribute target {target!r} must be a position reference"
        )
    return target


def _index_target(target: AttributeTarget, domain: AttributeDomain, length: int) -> int:
    if type(target) is not int or target < 0 or target >= length:
        raise ValueError(
            f"{domain.value} attribute target {target!r} is not an existing relation index"
        )
    return target


def _map_tier(
    graph: Graph, target: QualifiedName, replace: Callable[[Tier], Tier]
) -> tuple[Tier, ...]:
    found = False
    tiers: list[Tier] = []
    for tier in graph.tiers:
        if tier.declaration.name == target:
            found = True
            tier = replace(tier)
        tiers.append(tier)
    if not found:
        raise ValueError(f"tier attribute target {str(target)!r} is not declared")
    return tuple(tiers)


def _attach_item(
    graph: Graph, reference: ItemRef | DurableItemRef, value: AttributeValue
) -> tuple[Tier, ...]:
    coordinate = graph.resolve_item(reference)
    return _map_tier(
        graph,
        coordinate.tier,
        lambda tier: Tier(
            tier.declaration,
            tuple(
                Item(item.durable_id, (*item.attributes, value))
                if index == coordinate.index
                else item
                for index, item in enumerate(tier.items)
            ),
            tier.attributes,
        ),
    )


def _attach_relation_declaration(
    graph: Graph, target: QualifiedName, value: AttributeValue
) -> tuple[RelationDeclaration, ...]:
    found = False
    declarations: list[RelationDeclaration] = []
    for declaration in graph.relation_declarations:
        if declaration.name == target:
            found = True
            if isinstance(declaration, SimpleRelationDeclaration):
                declaration = SimpleRelationDeclaration(
                    declaration.name,
                    declaration.tier,
                    declaration.item_type,
                    (*declaration.attributes, value),
                )
            elif isinstance(declaration, BipartiteRelationDeclaration):
                declaration = BipartiteRelationDeclaration(
                    declaration.name,
                    declaration.left_type,
                    declaration.right_type,
                    declaration.left_endpoint,
                    declaration.right_endpoint,
                    declaration.single_parent,
                    declaration.acyclic,
                    (*declaration.attributes, value),
                )
            else:
                declaration = PolyadicRelationDeclaration(
                    declaration.name,
                    declaration.sources,
                    declaration.targets,
                    declaration.unique_sources,
                    declaration.distinct_targets,
                    declaration.single_parent,
                    declaration.acyclic,
                    declaration.targets_subset_of,
                    (*declaration.attributes, value),
                )
        declarations.append(declaration)
    if not found:
        raise ValueError(
            f"relation declaration attribute target {str(target)!r} is not declared"
        )
    return tuple(declarations)
