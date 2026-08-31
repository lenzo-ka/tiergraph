"""Checked opcodes and deterministic lowering for tiergraph graphs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, replace
from typing import Self, cast

from tiergraph.core import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    Boundary,
    BoundaryRef,
    BoundarySide,
    DurableBoundaryRef,
    DurableItemRef,
    Graph,
    Item,
    ItemRef,
    JsonValue,
    NamespaceDeclaration,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    QualifiedName,
    RelationDeclaration,
    RelationEndpointKind,
    RelationInstance,
    RelationSideDeclaration,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    XsdType,
    _GraphBuilder,
    _MutableTier,
    _resolve_relation_endpoint,
    _unique_simple_types,
    _validate_attributes,
    _validate_endpoint,
    _validate_polyadic_instance,
)
from tiergraph.schema import Refusal, RefusalStage
from tiergraph.wire import MAX_JSON_DEPTH

MACHINE_VERSION = "1"
MAX_REPEAT_COUNT = 10_000
# Owner-tunable policy: bound eager traces while leaving ample room for real builds.
MAX_TOTAL_OPCODES = 2_000_000


class ExecutionError(Refusal):
    """Name the opcode that could not make its checked state transition.

    Every execution refusal is a promise spanning more than one opcode, so
    the class carries the last stage of the declared refusal order.
    """

    def __init__(self, message: str) -> None:
        """Stage the refusal as a whole-graph promise."""
        super().__init__(RefusalStage.SEMANTICS, message)


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
            raise Refusal(
                RefusalStage.REFERENCE,
                f"item tier {str(self.tier)!r} is not declared",
            )
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
class PromoteBoundary:
    """Promote one structural boundary reference to anchored identity."""

    reference: BoundaryRef
    durable_id: str

    def apply(self, graph: Graph) -> Graph:
        """Apply the kernel's checked boundary promotion operation."""
        promoted, _ = graph.promote_boundary(self.reference, self.durable_id)
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
    """Add one instance of a declared bipartite or polyadic relation."""

    relation: RelationInstance | PolyadicRelationInstance

    def apply(self, graph: Graph) -> Graph:
        """Append the instance through endpoint and invariant validation."""
        if isinstance(self.relation, PolyadicRelationInstance):
            return _replace(
                graph,
                polyadic_relations=(*graph.polyadic_relations, self.relation),
            )
        return _replace(graph, relations=(*graph.relations, self.relation))

    def to_data(self) -> dict[str, JsonValue]:
        """Return the opcode as JSON data."""
        return {"opcode": "relate", "relation": self.relation.to_data()}


type AttributeTarget = (
    None
    | QualifiedName
    | ItemRef
    | DurableItemRef
    | BoundaryRef
    | DurableBoundaryRef
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
        boundary_reference = _boundary_target(self.target, self.domain)
        boundary_coordinate = graph.resolve_boundary(boundary_reference)
        boundaries = list(graph.boundary_values)
        for index, boundary in enumerate(boundaries):
            if graph.resolve_boundary(boundary.reference) == boundary_coordinate:
                boundaries[index] = Boundary(
                    boundary.reference, (*boundary.attributes, self.value)
                )
                break
        else:
            boundaries.append(Boundary(boundary_reference, (self.value,)))
        return _replace(graph, boundary_values=tuple(boundaries))

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
            raise Refusal(
                RefusalStage.VALUE,
                f"repeat count {self.count!r} must be a nonnegative integer",
            )
        if self.count > MAX_REPEAT_COUNT:
            raise Refusal(
                RefusalStage.VALUE,
                f"repeat count {self.count!r} exceeds limit {MAX_REPEAT_COUNT}",
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
    | PromoteBoundary
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
    PromoteBoundary,
    Relate,
    AttachValue,
)


def _decode_opcode(value: object, path: str, depth: int = 1) -> Opcode:
    """Decode one JSON-value opcode with path-aware shape diagnostics."""
    if depth > MAX_JSON_DEPTH:
        raise Refusal(
            RefusalStage.SYNTAX,
            f"{path}: JSON nesting depth exceeds limit {MAX_JSON_DEPTH}",
        )
    if not isinstance(value, dict) or not isinstance(value.get("opcode"), str):
        raise Refusal(RefusalStage.CONSTRUCTION, f"{path} must be an opcode object")
    name = value["opcode"]
    decoders: dict[str, tuple[set[str], Callable[[dict[str, object]], Opcode]]] = {
        "declare_namespace": (
            {"opcode", "declaration"},
            lambda v: DeclareNamespace(
                _decode_namespace(v["declaration"], f"{path}.declaration")
            ),
        ),
        "declare_tier": (
            {"opcode", "declaration"},
            lambda v: DeclareTier(
                _decode_tier(v["declaration"], f"{path}.declaration")
            ),
        ),
        "declare_relation": (
            {"opcode", "declaration"},
            lambda v: DeclareRelation(
                _decode_relation_declaration(v["declaration"], f"{path}.declaration")
            ),
        ),
        "declare_attribute": (
            {"opcode", "declaration"},
            lambda v: DeclareAttribute(
                _decode_attribute_declaration(v["declaration"], f"{path}.declaration")
            ),
        ),
        "add_item": (
            {"opcode", "tier", "item"},
            lambda v: AddItem(
                _decode_qname(v["tier"], f"{path}.tier"),
                _decode_item(v["item"], f"{path}.item"),
            ),
        ),
        "promote_item": (
            {"opcode", "reference", "durable_id"},
            lambda v: PromoteItem(
                _decode_item_ref(v["reference"], f"{path}.reference"),
                cast(str, v["durable_id"]),
            ),
        ),
        "promote_position": (
            {"opcode", "reference", "durable_id"},
            lambda v: PromoteBoundary(
                _decode_boundary_ref(v["reference"], f"{path}.reference"),
                cast(str, v["durable_id"]),
            ),
        ),
        "relate": (
            {"opcode", "relation"},
            lambda v: Relate(
                _decode_relation_instance(v["relation"], f"{path}.relation")
            ),
        ),
        "attach_value": (
            {"opcode", "domain", "target", "value"},
            lambda v: _decode_attach(v, path),
        ),
        "repeat": (
            {"opcode", "count", "body"},
            lambda v: _decode_repeat(v, path, depth),
        ),
    }
    if name not in decoders:
        raise Refusal(RefusalStage.DISCRIMINATOR, f"{path}.opcode {name!r} is unknown")
    keys, decoder = decoders[name]
    return decoder(_decode_object(value, path, keys))


def _decode_object(value: object, path: str, keys: set[str]) -> dict[str, object]:
    if not isinstance(value, dict):
        raise Refusal(RefusalStage.CONSTRUCTION, f"{path} must be an object")
    actual = set(value)
    if actual != keys:
        raise Refusal(
            RefusalStage.SHAPE,
            f"{path} fields must be {sorted(keys)!r}; got {sorted(actual)!r}",
        )
    return cast(dict[str, object], value)


def _decode_qname(value: object, path: str) -> QualifiedName:
    obj = _decode_object(value, path, {"namespace", "local_name"})
    namespace = obj["namespace"]
    local_name = obj["local_name"]
    if not isinstance(namespace, str):
        raise Refusal(RefusalStage.CONSTRUCTION, f"{path}.namespace must be a string")
    if not isinstance(local_name, str):
        raise Refusal(RefusalStage.CONSTRUCTION, f"{path}.local_name must be a string")
    return QualifiedName(namespace, local_name)


def _decode_namespace(value: object, path: str) -> NamespaceDeclaration:
    obj = _decode_object(value, path, {"prefix", "namespace"})
    return NamespaceDeclaration(cast(str, obj["prefix"]), cast(str, obj["namespace"]))


def _decode_tier(value: object, path: str) -> TierDeclaration:
    obj = _decode_object(value, path, {"name", "long_name"})
    return TierDeclaration(
        _decode_qname(obj["name"], f"{path}.name"), cast(str, obj["long_name"])
    )


def _decode_attribute_declaration(value: object, path: str) -> AttributeDeclaration:
    obj = _decode_object(value, path, {"name", "domain", "value_type"})
    return AttributeDeclaration(
        _decode_qname(obj["name"], f"{path}.name"),
        AttributeDomain(cast(str, obj["domain"])),
        XsdType(cast(str, obj["value_type"])),
    )


def _decode_attribute_value(value: object, path: str) -> AttributeValue:
    obj = _decode_object(value, path, {"name", "value_type", "lexical"})
    name = _decode_qname(obj["name"], f"{path}.name")
    value_type = obj["value_type"]
    lexical = obj["lexical"]
    if not isinstance(value_type, str):
        raise Refusal(RefusalStage.CONSTRUCTION, f"{path}.value_type must be a string")
    if not isinstance(lexical, str):
        raise Refusal(RefusalStage.CONSTRUCTION, f"{path}.lexical must be a string")
    return AttributeValue(
        name,
        XsdType(value_type),
        lexical,
    )


def _decode_attributes(value: object, path: str) -> tuple[AttributeValue, ...]:
    if not isinstance(value, list):
        raise Refusal(RefusalStage.CONSTRUCTION, f"{path} must be an array")
    return tuple(
        _decode_attribute_value(item, f"{path}[{index}]")
        for index, item in enumerate(value)
    )


def _decode_item(value: object, path: str) -> Item:
    obj = _decode_object(value, path, {"durable_id", "attributes"})
    return Item(
        cast(str | None, obj["durable_id"]),
        _decode_attributes(obj["attributes"], f"{path}.attributes"),
    )


def _decode_item_ref(value: object, path: str) -> ItemRef:
    obj = _decode_object(value, path, {"tier", "index"})
    return ItemRef(_decode_qname(obj["tier"], f"{path}.tier"), cast(int, obj["index"]))


def _decode_boundary_ref(value: object, path: str) -> BoundaryRef:
    obj = _decode_object(value, path, {"tier", "index"})
    return BoundaryRef(
        _decode_qname(obj["tier"], f"{path}.tier"), cast(int, obj["index"])
    )


def _decode_endpoint(value: object, path: str) -> object:
    if not isinstance(value, dict):
        raise Refusal(RefusalStage.CONSTRUCTION, f"{path} must be an endpoint object")
    if set(value) == {"tier", "index"}:
        return _decode_item_ref(value, path)
    if set(value) == {"durable_id"}:
        return DurableItemRef(value["durable_id"])
    if set(value) == {"anchor", "side"}:
        anchor_value = value["anchor"]
        anchor = _decode_object(
            anchor_value,
            f"{path}.anchor",
            set(cast(dict[str, object], anchor_value)),
        )
        if set(anchor) == {"kind", "durable_id"} and anchor["kind"] == "item":
            decoded_anchor: DurableItemRef | QualifiedName = DurableItemRef(
                cast(str, anchor["durable_id"])
            )
        elif set(anchor) == {"kind", "tier"} and anchor["kind"] == "tier":
            decoded_anchor = _decode_qname(anchor["tier"], f"{path}.anchor.tier")
        else:
            raise Refusal(
                RefusalStage.DISCRIMINATOR,
                f"{path}.anchor has an unknown shape",
            )
        return DurableBoundaryRef(
            decoded_anchor, BoundarySide(cast(str, value["side"]))
        )
    raise Refusal(RefusalStage.DISCRIMINATOR, f"{path} has an unknown reference shape")


def _decode_relation_instance(
    value: object, path: str
) -> RelationInstance | PolyadicRelationInstance:
    if isinstance(value, dict) and "sources" in value:
        obj = _decode_object(
            value,
            path,
            {"declaration", "sources", "targets", "durable_id", "attributes"},
        )
        sources, targets = obj["sources"], obj["targets"]
        if not isinstance(sources, list) or not isinstance(targets, list):
            raise Refusal(
                RefusalStage.CONSTRUCTION,
                f"{path} sources and targets must be arrays",
            )
        return PolyadicRelationInstance(
            _decode_qname(obj["declaration"], f"{path}.declaration"),
            tuple(
                cast(
                    ItemRef | DurableBoundaryRef,
                    _decode_endpoint(endpoint, f"{path}.sources[{index}]"),
                )
                for index, endpoint in enumerate(sources)
            ),
            tuple(
                cast(
                    ItemRef | DurableBoundaryRef,
                    _decode_endpoint(endpoint, f"{path}.targets[{index}]"),
                )
                for index, endpoint in enumerate(targets)
            ),
            cast(str | None, obj["durable_id"]),
            _decode_attributes(obj["attributes"], f"{path}.attributes"),
        )
    obj = _decode_object(
        value, path, {"declaration", "left", "right", "durable_id", "attributes"}
    )
    return RelationInstance(
        _decode_qname(obj["declaration"], f"{path}.declaration"),
        cast(
            ItemRef | DurableBoundaryRef,
            _decode_endpoint(obj["left"], f"{path}.left"),
        ),
        cast(
            ItemRef | DurableBoundaryRef,
            _decode_endpoint(obj["right"], f"{path}.right"),
        ),
        cast(str | None, obj["durable_id"]),
        _decode_attributes(obj["attributes"], f"{path}.attributes"),
    )


def _decode_side(value: object, path: str) -> RelationSideDeclaration:
    obj = _decode_object(
        value, path, {"endpoint_kinds", "tiers", "minimum", "maximum", "allow_empty"}
    )
    kinds, tiers = obj["endpoint_kinds"], obj["tiers"]
    if not isinstance(kinds, list) or not isinstance(tiers, list):
        raise Refusal(
            RefusalStage.CONSTRUCTION,
            f"{path} endpoint_kinds and tiers must be arrays",
        )
    maximum = obj["maximum"]
    return RelationSideDeclaration(
        tuple(RelationEndpointKind(cast(str, item)) for item in kinds),
        None
        if not tiers
        else tuple(_decode_qname(item, f"{path}.tiers") for item in tiers),
        cast(int, obj["minimum"]),
        None if maximum == -1 else cast(int, maximum),
        cast(bool, obj["allow_empty"]),
    )


def _decode_relation_declaration(value: object, path: str) -> RelationDeclaration:
    if not isinstance(value, dict):
        raise Refusal(RefusalStage.CONSTRUCTION, f"{path} must be an object")
    kind = value.get("kind")
    if kind == "simple":
        obj = _decode_object(
            value, path, {"kind", "name", "tier", "item_type", "attributes"}
        )
        return SimpleRelationDeclaration(
            _decode_qname(obj["name"], f"{path}.name"),
            _decode_qname(obj["tier"], f"{path}.tier"),
            _decode_qname(obj["item_type"], f"{path}.item_type"),
            _decode_attributes(obj["attributes"], f"{path}.attributes"),
        )
    if kind == "bipartite":
        obj = _decode_object(
            value,
            path,
            {
                "kind",
                "name",
                "left_type",
                "right_type",
                "left_endpoint",
                "right_endpoint",
                "single_parent",
                "acyclic",
                "attributes",
            },
        )
        return BipartiteRelationDeclaration(
            _decode_qname(obj["name"], f"{path}.name"),
            _decode_qname(obj["left_type"], f"{path}.left_type"),
            _decode_qname(obj["right_type"], f"{path}.right_type"),
            RelationEndpointKind(cast(str, obj["left_endpoint"])),
            RelationEndpointKind(cast(str, obj["right_endpoint"])),
            cast(bool, obj["single_parent"]),
            cast(bool, obj["acyclic"]),
            _decode_attributes(obj["attributes"], f"{path}.attributes"),
        )
    if kind == "polyadic":
        obj = _decode_object(
            value,
            path,
            {
                "kind",
                "name",
                "sources",
                "targets",
                "unique_sources",
                "distinct_targets",
                "single_parent",
                "acyclic",
                "targets_subset_of",
                "attributes",
            },
        )
        subset = obj["targets_subset_of"]
        if not isinstance(subset, list) or len(subset) > 1:
            raise Refusal(
                RefusalStage.VALUE,
                f"{path}.targets_subset_of must contain at most one name",
            )
        return PolyadicRelationDeclaration(
            _decode_qname(obj["name"], f"{path}.name"),
            _decode_side(obj["sources"], f"{path}.sources"),
            _decode_side(obj["targets"], f"{path}.targets"),
            cast(bool, obj["unique_sources"]),
            cast(bool, obj["distinct_targets"]),
            cast(bool, obj["single_parent"]),
            cast(bool, obj["acyclic"]),
            None
            if not subset
            else _decode_qname(subset[0], f"{path}.targets_subset_of[0]"),
            _decode_attributes(obj["attributes"], f"{path}.attributes"),
        )
    raise Refusal(RefusalStage.DISCRIMINATOR, f"{path}.kind {kind!r} is unknown")


def _decode_attach(value: dict[str, object], path: str) -> AttachValue:
    domain = AttributeDomain(cast(str, value["domain"]))
    target: object = value["target"]
    if isinstance(target, dict):
        if domain in {AttributeDomain.TIER, AttributeDomain.RELATION_DECLARATION}:
            target = _decode_qname(target, f"{path}.target")
        elif domain is AttributeDomain.BOUNDARY and set(target) == {"tier", "index"}:
            target = _decode_boundary_ref(target, f"{path}.target")
        else:
            target = _decode_endpoint(target, f"{path}.target")
    return AttachValue(
        domain,
        cast(AttributeTarget, target),
        _decode_attribute_value(value["value"], f"{path}.value"),
    )


def _decode_repeat(value: dict[str, object], path: str, depth: int) -> Repeat:
    body = value["body"]
    if not isinstance(body, list):
        raise Refusal(RefusalStage.CONSTRUCTION, f"{path}.body must be an array")
    return Repeat(
        cast(int, value["count"]),
        tuple(
            _decode_opcode(item, f"{path}.body[{index}]", depth + 2)
            for index, item in enumerate(body)
        ),
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
        """Lower procedures and build their authoritative graph in linear time."""
        trace = _flatten(self.opcodes)
        return AsBuilt._trusted(_build_checked(trace), trace)

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
        rebuilt = _build_checked(self.trace)
        if rebuilt != self.graph:
            raise Refusal(
                RefusalStage.SEMANTICS,
                "as-built trace does not execute to its graph",
            )

    @classmethod
    def _trusted(cls, graph: Graph, trace: tuple[PrimitiveOpcode, ...]) -> AsBuilt:
        outcome = object.__new__(cls)
        object.__setattr__(outcome, "graph", graph)
        object.__setattr__(outcome, "trace", trace)
        return outcome

    def unroll(self) -> Self:
        """Return this already lowered outcome unchanged."""
        return self

    def fingerprint(self) -> str:
        """Return a SHA-256 fingerprint of canonical as-built state bytes.

        Durable ids are genuine as-built content, not metadata, so promotion
        changes these bytes and therefore this fingerprint.
        """
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


def _build_checked(trace: tuple[PrimitiveOpcode, ...]) -> Graph:
    """Build quickly, using reference execution only to localize refusals."""
    if _has_shift_sensitive_relation_endpoint(trace):
        return execute(trace)
    try:
        return _build(trace)
    except Exception as builder_error:
        try:
            execute(trace)
        except Exception:
            raise
        raise RuntimeError(  # pragma: no cover - defensive builder/reference mismatch
            "linear graph builder refused a trace accepted by reference execution"
        ) from builder_error


def _has_shift_sensitive_relation_endpoint(
    trace: tuple[PrimitiveOpcode, ...],
) -> bool:
    """Detect tier-anchored boundaries, whose coordinates move as tiers grow."""
    for opcode in trace:
        if type(opcode) is not Relate:
            continue
        relation = opcode.relation
        endpoints = (
            (*relation.sources, *relation.targets)
            if isinstance(relation, PolyadicRelationInstance)
            else (relation.left, relation.right)
        )
        if any(
            isinstance(endpoint, DurableBoundaryRef)
            and isinstance(endpoint.anchor, QualifiedName)
            for endpoint in endpoints
        ):
            return True
    return False


def _build(trace: tuple[PrimitiveOpcode, ...]) -> Graph:
    builder = _GraphBuilder()
    for opcode in trace:
        _build_opcode(builder, opcode)
    return builder._finish()


def _build_opcode(builder: _GraphBuilder, opcode: PrimitiveOpcode) -> None:
    if type(opcode) is DeclareNamespace:
        builder.namespaces.append(opcode.declaration)
        builder.declared_namespaces.add(opcode.declaration.namespace)
        return
    if type(opcode) is DeclareTier:
        builder._require_namespaces((opcode.declaration.name,))
        added_tier = _MutableTier(opcode.declaration, [], [])
        builder.tiers.append(added_tier)
        builder.tiers_by_name[opcode.declaration.name] = added_tier
        return
    if type(opcode) is DeclareRelation:
        _build_declare_relation(builder, opcode.declaration)
        return
    if type(opcode) is DeclareAttribute:
        builder._require_namespaces((opcode.declaration.name,))
        builder.attribute_declarations.append(opcode.declaration)
        builder.attributes_by_name[opcode.declaration.name] = opcode.declaration
        return
    if type(opcode) is AddItem:
        current_tier = builder.tiers_by_name.get(opcode.tier)
        if current_tier is None:
            raise Refusal(
                RefusalStage.REFERENCE,
                f"item tier {str(opcode.tier)!r} is not declared",
            )
        _validate_attributes(
            opcode.item.attributes, AttributeDomain.ITEM, builder.attributes_by_name
        )
        index = len(current_tier.items)
        after_index = builder.after_boundary_by_tier.get(opcode.tier)
        if after_index is not None:
            del builder.boundaries_by_coordinate[BoundaryRef(opcode.tier, index)]
            builder.boundaries_by_coordinate[BoundaryRef(opcode.tier, index + 1)] = (
                after_index
            )
        current_tier.items.append(opcode.item)
        if opcode.item.durable_id is not None:
            builder.items_by_id[opcode.item.durable_id] = ItemRef(opcode.tier, index)
        return
    if type(opcode) is PromoteItem:
        _build_promote_item(builder, opcode.reference, opcode.durable_id)
        return
    if type(opcode) is PromoteBoundary:
        _build_promote_boundary(builder, opcode.reference, opcode.durable_id)
        return
    if type(opcode) is Relate:
        _build_relate(builder, opcode.relation)
        return
    if type(opcode) is AttachValue:
        _build_attach_value(builder, opcode)
        return
    raise TypeError(f"unrecognized opcode type {type(opcode).__name__!r}")


def _build_declare_relation(
    builder: _GraphBuilder, declaration: RelationDeclaration
) -> None:
    names = [declaration.name]
    if isinstance(declaration, SimpleRelationDeclaration):
        names.append(declaration.item_type)
        _unique_simple_types([declaration], builder._tier_views())
        if declaration.tier in builder.types_by_tier:
            raise Refusal(
                RefusalStage.SEMANTICS,
                f"tier {str(declaration.tier)!r} has multiple simple relations; "
                f"at most one is allowed",
            )
        builder.types_by_tier[declaration.tier] = declaration.item_type
    elif isinstance(declaration, BipartiteRelationDeclaration):
        names.extend((declaration.left_type, declaration.right_type))
    else:
        names.extend(
            tier
            for side in (declaration.sources, declaration.targets)
            for tier in (() if side.tiers is None else side.tiers)
        )
        if declaration.targets_subset_of is not None:
            names.append(declaration.targets_subset_of)
            subset = builder.declarations_by_name.get(declaration.targets_subset_of)
            if declaration.targets_subset_of == declaration.name:
                subset = declaration
            if not isinstance(subset, PolyadicRelationDeclaration):
                raise Refusal(
                    RefusalStage.REFERENCE,
                    f"polyadic relation {str(declaration.name)!r} "
                    f"targets-subset-of names undeclared polyadic relation "
                    f"{str(declaration.targets_subset_of)!r}",
                )
    builder._require_namespaces(names)
    _validate_attributes(
        declaration.attributes,
        AttributeDomain.RELATION_DECLARATION,
        builder.attributes_by_name,
    )
    builder.declaration_indexes[declaration.name] = len(builder.relation_declarations)
    builder.relation_declarations.append(declaration)
    builder.declarations_by_name[declaration.name] = declaration


def _build_promote_item(
    builder: _GraphBuilder, reference: ItemRef, durable_id: str
) -> DurableItemRef:
    coordinate = builder._resolve_item(reference)
    tier = builder.tiers_by_name[coordinate.tier]
    item = tier.items[coordinate.index]
    if item.durable_id is not None:
        if item.durable_id != durable_id:
            raise Refusal(
                RefusalStage.SEMANTICS,
                f"item {str(reference)!r} already carries durable id "
                f"{item.durable_id!r}; refused conflicting durable id "
                f"{durable_id!r}",
            )
        return DurableItemRef(item.durable_id)
    durable = DurableItemRef(durable_id)
    tier.items[coordinate.index] = Item(durable_id, item.attributes)
    builder.items_by_id[durable_id] = coordinate
    return durable


def _build_promote_boundary(
    builder: _GraphBuilder, reference: BoundaryRef, durable_id: str
) -> DurableBoundaryRef:
    coordinate = builder._resolve_boundary(reference)
    tier = builder.tiers_by_name[coordinate.tier]
    if coordinate.index == 0:
        durable = DurableBoundaryRef(coordinate.tier, BoundarySide.BEFORE)
    elif coordinate.index == len(tier.items):
        durable = DurableBoundaryRef(coordinate.tier, BoundarySide.AFTER)
    else:
        anchor_item = tier.items[coordinate.index]
        # Refusing here keeps this engine's own diagnostic in boundary terms rather
        # than the item terms the delegation below would raise. No test gates that
        # wording: _build_checked re-runs the reference machine on any builder
        # failure and re-raises its error, so nothing a builder raises reaches a
        # caller, and deleting this branch leaves the suite green. The parity the
        # refusal exists for is between the engines themselves, not between their
        # observable messages, which the substitution would supply either way.
        if anchor_item.durable_id is not None and anchor_item.durable_id != durable_id:
            raise Refusal(
                RefusalStage.SEMANTICS,
                f"boundary {str(reference)!r} is before an anchor carrying "
                f"durable id {anchor_item.durable_id!r}; refused conflicting "
                f"boundary durable id {durable_id!r}",
            )
        anchor = _build_promote_item(
            builder, ItemRef(coordinate.tier, coordinate.index), durable_id
        )
        durable = DurableBoundaryRef(anchor, BoundarySide.BEFORE)
    boundary_index = builder.boundaries_by_coordinate.get(coordinate)
    if boundary_index is not None:
        boundary = builder.boundary_values[boundary_index]
        if isinstance(boundary.reference, BoundaryRef):
            builder.boundary_values[boundary_index] = Boundary(
                durable, boundary.attributes
            )
            if (
                isinstance(durable.anchor, QualifiedName)
                and durable.side is BoundarySide.AFTER
            ):
                builder.after_boundary_by_tier[durable.anchor] = boundary_index
        else:
            durable = boundary.reference
    return durable


def _build_relate(
    builder: _GraphBuilder,
    relation: RelationInstance | PolyadicRelationInstance,
) -> None:
    _validate_attributes(
        relation.attributes,
        AttributeDomain.RELATION_INSTANCE,
        builder.attributes_by_name,
    )
    if isinstance(relation, RelationInstance):
        declaration = builder.declarations_by_name.get(relation.declaration)
        if not isinstance(declaration, BipartiteRelationDeclaration):
            raise Refusal(
                RefusalStage.REFERENCE, "a bipartite relation declaration is required"
            )
        index = len(builder.relations)
        _validate_endpoint(
            index,
            "left",
            relation.left,
            declaration.left_type,
            declaration.left_endpoint,
            builder._tier_views(),
            builder.types_by_tier,
            builder.items_by_id,
        )
        _validate_endpoint(
            index,
            "right",
            relation.right,
            declaration.right_type,
            declaration.right_endpoint,
            builder._tier_views(),
            builder.types_by_tier,
            builder.items_by_id,
        )
        builder.relations.append(relation)
        return
    declaration = builder.declarations_by_name.get(relation.declaration)
    if not isinstance(declaration, PolyadicRelationDeclaration):
        raise Refusal(
            RefusalStage.REFERENCE, "a polyadic relation declaration is required"
        )
    index = len(builder.polyadic_relations)
    _validate_polyadic_instance(
        index,
        relation,
        declaration,
        builder._tier_views(),
        builder.items_by_id,
    )
    sources = tuple(
        _resolve_relation_endpoint(
            endpoint, builder._tier_views(), builder.items_by_id, ValueError
        )
        for endpoint in relation.sources
    )
    targets = {
        _resolve_relation_endpoint(
            endpoint, builder._tier_views(), builder.items_by_id, ValueError
        )
        for endpoint in relation.targets
    }
    for source in sources:
        builder.polyadic_targets_by_source.setdefault(
            (relation.declaration, source), set()
        ).update(targets)
    if declaration.targets_subset_of is not None:
        for source in sources:
            allowed = builder.polyadic_targets_by_source.get(
                (declaration.targets_subset_of, source)
            )
            if allowed is None or not targets <= allowed:
                raise Refusal(
                    RefusalStage.SEMANTICS,
                    "polyadic targets-subset-of membership is not satisfied",
                )
    builder.polyadic_relations.append(relation)


def _build_attach_value(builder: _GraphBuilder, opcode: AttachValue) -> None:
    _validate_attributes((opcode.value,), opcode.domain, builder.attributes_by_name)
    if opcode.domain is AttributeDomain.DOCUMENT:
        _require_target(opcode.target, None, opcode.domain)
        builder.attributes.append(opcode.value)
    elif opcode.domain is AttributeDomain.TIER:
        target = _qualified_target(opcode.target, opcode.domain)
        tier = builder.tiers_by_name.get(target)
        if tier is None:
            raise Refusal(
                RefusalStage.REFERENCE,
                f"tier attribute target {str(target)!r} is not declared",
            )
        tier.attributes.append(opcode.value)
    elif opcode.domain is AttributeDomain.ITEM:
        coordinate = builder._resolve_item(_item_target(opcode.target, opcode.domain))
        tier = builder.tiers_by_name[coordinate.tier]
        item = tier.items[coordinate.index]
        tier.items[coordinate.index] = Item(
            item.durable_id, (*item.attributes, opcode.value)
        )
    elif opcode.domain is AttributeDomain.RELATION_DECLARATION:
        target = _qualified_target(opcode.target, opcode.domain)
        index = builder.declaration_indexes.get(target)
        if index is None:
            raise Refusal(
                RefusalStage.REFERENCE,
                f"relation declaration attribute target {str(target)!r} "
                f"is not declared",
            )
        declaration = _relation_with_value(
            builder.relation_declarations[index], opcode.value
        )
        builder.relation_declarations[index] = declaration
        builder.declarations_by_name[target] = declaration
    elif opcode.domain is AttributeDomain.RELATION_INSTANCE:
        index = _index_target(opcode.target, opcode.domain, len(builder.relations))
        relation = builder.relations[index]
        builder.relations[index] = RelationInstance(
            relation.declaration,
            relation.left,
            relation.right,
            relation.durable_id,
            (*relation.attributes, opcode.value),
        )
    else:
        reference = _boundary_target(opcode.target, opcode.domain)
        boundary_coordinate = builder._resolve_boundary(reference)
        index = builder.boundaries_by_coordinate.get(boundary_coordinate)
        if index is None:
            builder.boundaries_by_coordinate[boundary_coordinate] = len(
                builder.boundary_values
            )
            builder.boundary_values.append(Boundary(reference, (opcode.value,)))
            if (
                isinstance(reference, DurableBoundaryRef)
                and isinstance(reference.anchor, QualifiedName)
                and reference.side is BoundarySide.AFTER
            ):
                builder.after_boundary_by_tier[reference.anchor] = (
                    len(builder.boundary_values) - 1
                )
        else:
            boundary = builder.boundary_values[index]
            builder.boundary_values[index] = Boundary(
                boundary.reference, (*boundary.attributes, opcode.value)
            )


def _relation_with_value(
    declaration: RelationDeclaration, value: AttributeValue
) -> RelationDeclaration:
    attributes = (*declaration.attributes, value)
    if isinstance(declaration, SimpleRelationDeclaration):
        return SimpleRelationDeclaration(
            declaration.name, declaration.tier, declaration.item_type, attributes
        )
    if isinstance(declaration, BipartiteRelationDeclaration):
        return BipartiteRelationDeclaration(
            declaration.name,
            declaration.left_type,
            declaration.right_type,
            declaration.left_endpoint,
            declaration.right_endpoint,
            declaration.single_parent,
            declaration.acyclic,
            attributes,
        )
    return PolyadicRelationDeclaration(
        declaration.name,
        declaration.sources,
        declaration.targets,
        declaration.unique_sources,
        declaration.distinct_targets,
        declaration.single_parent,
        declaration.acyclic,
        declaration.targets_subset_of,
        attributes,
    )


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
                raise Refusal(
                    RefusalStage.SEMANTICS,
                    f"total primitive opcode count exceeds limit {MAX_TOTAL_OPCODES}",
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
    return replace(graph)


def _replace(
    graph: Graph,
    *,
    namespaces: tuple[NamespaceDeclaration, ...] | None = None,
    tiers: tuple[Tier, ...] | None = None,
    relation_declarations: tuple[RelationDeclaration, ...] | None = None,
    relations: tuple[RelationInstance, ...] | None = None,
    attribute_declarations: tuple[AttributeDeclaration, ...] | None = None,
    boundary_values: tuple[Boundary, ...] | None = None,
    attributes: tuple[AttributeValue, ...] | None = None,
    polyadic_relations: tuple[PolyadicRelationInstance, ...] | None = None,
) -> Graph:
    return replace(
        graph,
        namespaces=graph.namespaces if namespaces is None else namespaces,
        tiers=graph.tiers if tiers is None else tiers,
        relation_declarations=(
            graph.relation_declarations
            if relation_declarations is None
            else relation_declarations
        ),
        relations=graph.relations if relations is None else relations,
        attribute_declarations=(
            graph.attribute_declarations
            if attribute_declarations is None
            else attribute_declarations
        ),
        boundary_values=(
            graph.boundary_values if boundary_values is None else boundary_values
        ),
        attributes=graph.attributes if attributes is None else attributes,
        polyadic_relations=(
            graph.polyadic_relations
            if polyadic_relations is None
            else polyadic_relations
        ),
    )


def _require_target(
    actual: AttributeTarget, expected: None, domain: AttributeDomain
) -> None:
    if actual is not expected:
        raise Refusal(
            RefusalStage.CONSTRUCTION,
            f"{domain.value} attribute target {actual!r} must be None",
        )


def _qualified_target(
    target: AttributeTarget, domain: AttributeDomain
) -> QualifiedName:
    if not isinstance(target, QualifiedName):
        raise Refusal(
            RefusalStage.CONSTRUCTION,
            f"{domain.value} attribute target {target!r} must be a qualified name",
        )
    return target


def _item_target(
    target: AttributeTarget, domain: AttributeDomain
) -> ItemRef | DurableItemRef:
    if not isinstance(target, ItemRef | DurableItemRef):
        raise Refusal(
            RefusalStage.CONSTRUCTION,
            f"{domain.value} attribute target {target!r} must be an item reference",
        )
    return target


def _boundary_target(
    target: AttributeTarget, domain: AttributeDomain
) -> BoundaryRef | DurableBoundaryRef:
    if not isinstance(target, BoundaryRef | DurableBoundaryRef):
        raise Refusal(
            RefusalStage.CONSTRUCTION,
            f"{domain.value} attribute target {target!r} must be a boundary reference",
        )
    return target


def _index_target(target: AttributeTarget, domain: AttributeDomain, length: int) -> int:
    if type(target) is not int or target < 0 or target >= length:
        raise Refusal(
            RefusalStage.REFERENCE,
            f"{domain.value} attribute target {target!r} is not an existing "
            f"relation index",
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
        raise Refusal(
            RefusalStage.REFERENCE,
            f"tier attribute target {str(target)!r} is not declared",
        )
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
            declaration = _relation_with_value(declaration, value)
        declarations.append(declaration)
    if not found:
        raise Refusal(
            RefusalStage.REFERENCE,
            f"relation declaration attribute target {str(target)!r} is not declared",
        )
    return tuple(declarations)
