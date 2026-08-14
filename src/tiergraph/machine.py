"""Checked opcodes and deterministic lowering for tiergraph graphs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol, Self

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


class ExecutionError(ValueError):
    """Name the opcode that could not make its checked state transition."""


class PrimitiveOpcode(Protocol):
    """A checked state transition in the consume-tier language fragment."""

    def apply(self, graph: Graph) -> Graph:
        """Return the next validated graph or refuse the transition."""

    def to_data(self) -> dict[str, JsonValue]:
        """Return a JSON-serializable description for diagnostics."""


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

    def to_data(self) -> dict[str, JsonValue]:
        """Return the procedural opcode as JSON data."""
        return {
            "opcode": "repeat",
            "count": self.count,
            "body": [opcode.to_data() for opcode in self.body],
        }


type Opcode = PrimitiveOpcode | Repeat


@dataclass(frozen=True, slots=True, eq=False)
class Program:
    """Carry source opcodes while defining identity on their checked outcome."""

    opcodes: tuple[Opcode, ...]

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


def execute(opcodes: Iterable[PrimitiveOpcode]) -> Graph:
    """Execute primitives in order and name the first refused opcode."""
    graph = Graph((), (), ())
    for index, opcode in enumerate(opcodes):
        try:
            graph = opcode.apply(graph)
        except (TypeError, ValueError) as error:
            raise ExecutionError(
                f"opcode {index} {opcode.to_data()!r} refused: {error}"
            ) from error
    return graph


def _flatten(opcodes: tuple[Opcode, ...]) -> tuple[PrimitiveOpcode, ...]:
    flattened: list[PrimitiveOpcode] = []
    stack: list[tuple[tuple[Opcode, ...], int]] = [(opcodes, 0)]
    while stack:
        block, index = stack.pop()
        if index == len(block):
            continue
        opcode = block[index]
        stack.append((block, index + 1))
        if isinstance(opcode, Repeat):
            for _ in range(opcode.count):
                stack.append((opcode.body, 0))
        else:
            flattened.append(opcode)
    return tuple(flattened)


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
            else:
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
        declarations.append(declaration)
    if not found:
        raise ValueError(
            f"relation declaration attribute target {str(target)!r} is not declared"
        )
    return tuple(declarations)
