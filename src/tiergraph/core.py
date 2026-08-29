"""Immutable declarations and graph values for ordered parallel structure."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, cast

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]

_INTEGER_LEXICAL = re.compile(r"[+-]?[0-9]+\Z")
_DECIMAL_LEXICAL = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)\Z")
_DOUBLE_LEXICAL = re.compile(
    r"(?:NaN|[+-]?INF|[+-]?(?:(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?))\Z"
)


class GraphValidationError(ValueError):
    """Report a declaration or graph-contract validation failure."""


class AttributeDomain(StrEnum):
    """The closed set of places where a declared attribute may occur."""

    ITEM = "item"
    TIER = "tier"
    RELATION_DECLARATION = "relation_declaration"
    RELATION_INSTANCE = "relation_instance"
    POSITION = "position"
    DOCUMENT = "document"


class XsdType(StrEnum):
    """The growable XSD datatype subset admitted for scalar attribute values.

    In-graph references are relations, not attribute value types.  Relation
    declarations type their referents and may validate structural promises;
    an out-of-graph reference is honestly a string because this graph cannot
    validate what it denotes.
    """

    STRING = "string"
    BOOLEAN = "boolean"
    INTEGER = "integer"
    DECIMAL = "decimal"
    DOUBLE = "double"


class BoundarySide(StrEnum):
    """Choose the boundary immediately before or after an anchor."""

    BEFORE = "before"
    AFTER = "after"


class RelationEndpointKind(StrEnum):
    """Declare whether one relation endpoint is an item or a boundary."""

    ITEM = "item"
    BOUNDARY = "boundary"


@dataclass(frozen=True, slots=True, order=True)
class QualifiedName:
    """Identify a declaration by namespace URI and local name."""

    namespace: str
    local_name: str

    def __post_init__(self) -> None:
        """Require both parts because an unqualified name is a different contract."""
        _require_name(self.namespace, "qualified-name namespace")
        _require_name(self.local_name, "qualified-name local name")

    def to_data(self) -> dict[str, JsonValue]:
        """Return the expanded name independently of document prefix choices."""
        return {"namespace": self.namespace, "local_name": self.local_name}

    def __str__(self) -> str:
        """Return a compact expanded spelling for diagnostics."""
        return f"{{{self.namespace}}}{self.local_name}"


@dataclass(frozen=True, slots=True)
class NamespaceDeclaration:
    """Bind a document-local prefix to one namespace URI."""

    prefix: str
    namespace: str

    def __post_init__(self) -> None:
        """Require a usable prefix and namespace URI."""
        _require_name(self.prefix, "namespace prefix")
        _require_name(self.namespace, f"namespace prefix {self.prefix!r}")

    @property
    def name(self) -> str:
        """Return the prefix used as the declaration key."""
        return self.prefix

    def to_data(self) -> dict[str, JsonValue]:
        """Return the prefix binding as JSON-serializable data."""
        return {"prefix": self.prefix, "namespace": self.namespace}


@dataclass(frozen=True, slots=True)
class AttributeValue:
    """Carry one named typed value in its XSD canonical lexical form."""

    name: QualifiedName
    value_type: XsdType
    lexical: str

    def __post_init__(self) -> None:
        """Separate lexical validation from the canonical spelling stored afterward."""
        try:
            canonical = _canonical_lexical(self.value_type, self.lexical)
        except ValueError as error:
            raise GraphValidationError(
                f"attribute {str(self.name)!r} has invalid {self.value_type.value} "
                f"value {self.lexical!r}"
            ) from error
        object.__setattr__(self, "lexical", canonical)

    def to_data(self) -> dict[str, JsonValue]:
        """Use lexical strings so every XSD value remains valid JSON."""
        return {
            "name": self.name.to_data(),
            "value_type": self.value_type.value,
            "lexical": self.lexical,
        }


@dataclass(frozen=True, slots=True)
class TierDeclaration:
    """Name an ordered tier without coupling its name to item identity."""

    name: QualifiedName
    long_name: str

    def __post_init__(self) -> None:
        """Require a long display name while identity stays in the qualified name."""
        _require_name(self.long_name, f"tier {str(self.name)!r} long name")

    @property
    def short_name(self) -> str:
        """Return the local part used as the tier's short display name."""
        return self.name.local_name

    def to_data(self) -> dict[str, JsonValue]:
        """Return the declaration as JSON-serializable data."""
        return {"name": self.name.to_data(), "long_name": self.long_name}


@dataclass(frozen=True, slots=True)
class AttributeDeclaration:
    """Declare an optional, at-most-one value for one domain and XSD type.

    Absence means absent: attributes have no defaults, deliberately, because a
    default would put a value in the reading that is missing from graph bytes.
    """

    name: QualifiedName
    domain: AttributeDomain
    value_type: XsdType

    def to_data(self) -> dict[str, JsonValue]:
        """Return the declaration as JSON-serializable data."""
        return {
            "name": self.name.to_data(),
            "domain": self.domain.value,
            "value_type": self.value_type.value,
        }


@dataclass(frozen=True, slots=True)
class SimpleRelationDeclaration:
    """Give every member of one tier its type through a depth-one relation."""

    name: QualifiedName
    tier: QualifiedName
    item_type: QualifiedName
    attributes: tuple[AttributeValue, ...] = ()

    def __post_init__(self) -> None:
        """Canonicalize declaration attributes by their qualified names."""
        _canonicalize_attributes(self)

    def to_data(self) -> dict[str, JsonValue]:
        """Return the declaration as JSON-serializable data."""
        return {
            "kind": "simple",
            "name": self.name.to_data(),
            "tier": self.tier.to_data(),
            "item_type": self.item_type.to_data(),
            "attributes": _attributes_data(self.attributes),
        }


@dataclass(frozen=True, slots=True)
class BipartiteRelationDeclaration:
    """Declare typed links and the graph invariants they promise.

    Unlike scalar ``XsdType`` values, a relation types its referents through
    ``left_type`` and ``right_type`` and validates its ``single_parent`` and
    ``acyclic`` promises.
    """

    name: QualifiedName
    left_type: QualifiedName
    right_type: QualifiedName
    left_endpoint: RelationEndpointKind = RelationEndpointKind.ITEM
    right_endpoint: RelationEndpointKind = RelationEndpointKind.ITEM
    single_parent: bool = False
    acyclic: bool = False
    attributes: tuple[AttributeValue, ...] = ()

    def __post_init__(self) -> None:
        """Canonicalize attributes and require JSON-boolean promises."""
        _canonicalize_attributes(self)
        _require_boolean(self.single_parent, "single-parent promise")
        _require_boolean(self.acyclic, "acyclic promise")

    def to_data(self) -> dict[str, JsonValue]:
        """Return the declaration as JSON-serializable data."""
        return {
            "kind": "bipartite",
            "name": self.name.to_data(),
            "left_type": self.left_type.to_data(),
            "right_type": self.right_type.to_data(),
            "left_endpoint": self.left_endpoint.value,
            "right_endpoint": self.right_endpoint.value,
            "single_parent": self.single_parent,
            "acyclic": self.acyclic,
            "attributes": _attributes_data(self.attributes),
        }


@dataclass(frozen=True, slots=True)
class RelationSideDeclaration:
    """Constrain one explicitly ordered side of a polyadic relation."""

    endpoint_kinds: tuple[RelationEndpointKind, ...]
    tiers: tuple[QualifiedName, ...] | None = None
    minimum: int = 1
    maximum: int | None = None
    allow_empty: bool = False

    def __post_init__(self) -> None:
        """Canonicalize allowed sets and refuse incoherent arity bounds."""
        object.__setattr__(self, "endpoint_kinds", tuple(sorted(self.endpoint_kinds)))
        if self.tiers is not None:
            object.__setattr__(self, "tiers", tuple(sorted(self.tiers)))
        if not self.endpoint_kinds:
            raise GraphValidationError("relation side endpoint kinds must not be empty")
        if len(set(self.endpoint_kinds)) != len(self.endpoint_kinds):
            raise GraphValidationError("relation side endpoint kinds must be unique")
        if self.tiers is not None and len(set(self.tiers)) != len(self.tiers):
            raise GraphValidationError("relation side tiers must be unique")
        _require_integral_bound(self.minimum, "relation side minimum")
        if self.maximum is not None:
            _require_integral_bound(self.maximum, "relation side maximum")
        if self.maximum is not None and self.maximum < self.minimum:
            raise GraphValidationError(
                "relation side maximum must not be less than minimum"
            )
        _require_boolean(self.allow_empty, "relation side allow-empty promise")

    def to_data(self) -> dict[str, JsonValue]:
        """Return the side contract without inventing order for its allowed sets."""
        return {
            "endpoint_kinds": [kind.value for kind in self.endpoint_kinds],
            "tiers": (
                None if self.tiers is None else [tier.to_data() for tier in self.tiers]
            ),
            "minimum": self.minimum,
            "maximum": -1 if self.maximum is None else self.maximum,
            "allow_empty": self.allow_empty,
        }


@dataclass(frozen=True, slots=True)
class PolyadicRelationDeclaration:
    """Declare ordered endpoint sequences and general incidence constraints.

    ``unique_sources`` makes each source occur in at most one instance.
    ``distinct_targets`` forbids repeated candidates within an instance.
    ``targets_subset_of`` requires each instance's targets to be members of the
    named relation's targets for the same source.  These are the structural
    contracts commonly called containment, choice, and selection membership;
    their domain names do not belong in the kernel.

    Empty sources or targets are admitted only by that side's ``allow_empty``.
    An empty side contributes no edges to acyclicity, no parent assignments to
    ``single_parent``, and no source keys to source uniqueness or subset checks.
    Its arity bounds are deliberately bypassed: emptiness is an explicit case,
    not an accidental consequence of a zero minimum.
    """

    name: QualifiedName
    sources: RelationSideDeclaration
    targets: RelationSideDeclaration
    unique_sources: bool = False
    distinct_targets: bool = False
    single_parent: bool = False
    acyclic: bool = False
    targets_subset_of: QualifiedName | None = None
    attributes: tuple[AttributeValue, ...] = ()

    def __post_init__(self) -> None:
        """Canonicalize attributes and require actual JSON-boolean promises."""
        _canonicalize_attributes(self)
        for value, subject in (
            (self.unique_sources, "unique-sources promise"),
            (self.distinct_targets, "distinct-targets promise"),
            (self.single_parent, "single-parent promise"),
            (self.acyclic, "acyclic promise"),
        ):
            _require_boolean(value, subject)

    def to_data(self) -> dict[str, JsonValue]:
        """Return the declaration as JSON-serializable data."""
        return {
            "kind": "polyadic",
            "name": self.name.to_data(),
            "sources": self.sources.to_data(),
            "targets": self.targets.to_data(),
            "unique_sources": self.unique_sources,
            "distinct_targets": self.distinct_targets,
            "single_parent": self.single_parent,
            "acyclic": self.acyclic,
            "targets_subset_of": []
            if self.targets_subset_of is None
            else [self.targets_subset_of.to_data()],
            "attributes": _attributes_data(self.attributes),
        }


type RelationDeclaration = (
    SimpleRelationDeclaration
    | BipartiteRelationDeclaration
    | PolyadicRelationDeclaration
)


@dataclass(frozen=True, slots=True)
class Item:
    """Represent a tier member with attributes and a durable identifier seam."""

    durable_id: str | None = None
    attributes: tuple[AttributeValue, ...] = ()

    def __post_init__(self) -> None:
        """Canonicalize attributes and refuse a carried empty durable id."""
        _canonicalize_attributes(self)
        if self.durable_id is not None:
            _require_name(self.durable_id, "item durable id")

    def to_data(self) -> dict[str, JsonValue]:
        """Return the item as JSON-serializable data."""
        return {
            "durable_id": self.durable_id,
            "attributes": _attributes_data(self.attributes),
        }


@dataclass(frozen=True, slots=True)
class Tier:
    """Pair a declaration with immutable ordered members and tier attributes."""

    declaration: TierDeclaration
    items: tuple[Item, ...] = ()
    attributes: tuple[AttributeValue, ...] = ()

    def __post_init__(self) -> None:
        """Canonicalize tier attributes while retaining item order."""
        _canonicalize_attributes(self)

    def to_data(self) -> dict[str, JsonValue]:
        """Return the tier as JSON-serializable data."""
        return {
            "declaration": self.declaration.to_data(),
            "items": [item.to_data() for item in self.items],
            "attributes": _attributes_data(self.attributes),
        }


@dataclass(frozen=True, slots=True)
class ItemRef:
    """Address an item by its current structural position."""

    tier: QualifiedName
    index: int

    def __post_init__(self) -> None:
        """Require context-free integral identity before use or serialization."""
        _require_integral_index(self.index, "item reference", self.to_data())

    def to_data(self) -> dict[str, JsonValue]:
        """Return the reference as JSON-serializable data."""
        return {"tier": self.tier.to_data(), "index": self.index}

    def __str__(self) -> str:
        """Return a compact coordinate spelling for diagnostics."""
        return f"{self.tier}[{self.index}]"


@dataclass(frozen=True, slots=True)
class PositionRef:
    """Address a boundary owned by a tier, including both outer boundaries."""

    tier: QualifiedName
    index: int

    def __post_init__(self) -> None:
        """Require context-free integral identity before use or serialization."""
        _require_integral_index(self.index, "position", self.to_data())

    def to_data(self) -> dict[str, JsonValue]:
        """Return the position reference as JSON-serializable data."""
        return {"tier": self.tier.to_data(), "index": self.index}

    def __str__(self) -> str:
        """Return a compact coordinate spelling for diagnostics."""
        return f"{self.tier}[{self.index}]"


@dataclass(frozen=True, slots=True)
class DurableItemRef:
    """Address an item by a durable identifier without a coordinate fallback."""

    durable_id: str

    def __post_init__(self) -> None:
        """Require the identifier needed for durable resolution."""
        _require_name(self.durable_id, "durable item reference")

    def to_data(self) -> dict[str, JsonValue]:
        """Return the durable reference as JSON-serializable data."""
        return {"durable_id": self.durable_id}


@dataclass(frozen=True, slots=True)
class DurablePositionRef:
    """Address a boundary whose identity is its anchor and chosen side.

    Boundary identity is anchor-relative: an interior boundary's identity is,
    for example, "before item X", not an identity attached to an adjacency.
    A boundary therefore follows its anchor when it moves; moving a block
    carries its internal boundaries.  Under reordering identities follow their
    anchors and no new adjacency inherits an identity.  Inserting exactly at
    ``before(x)`` leaves that boundary before ``x``.

    Distinct anchors may resolve to the same boundary in the current graph and
    diverge after an edit.  In particular, ``after(a)`` and ``before(b)`` keep
    different intentions even when ``a`` and ``b`` are adjacent.  Likewise,
    ``before(tier)`` and ``after(tier)`` are distinct first-edge and last-edge
    anchors that coincide only while the tier is empty.

    Removing an anchor is deliberately unsettled: removal destroys the anchor,
    and the kernel has no ratified rule for that case.
    """

    anchor: DurableItemRef | QualifiedName
    side: BoundarySide

    def to_data(self) -> dict[str, JsonValue]:
        """Return the tagged anchor and side as JSON-serializable data."""
        if isinstance(self.anchor, DurableItemRef):
            anchor: dict[str, JsonValue] = {
                "kind": "item",
                "durable_id": self.anchor.durable_id,
            }
        else:
            anchor = {"kind": "tier", "tier": self.anchor.to_data()}
        return {"anchor": anchor, "side": self.side.value}

    def __str__(self) -> str:
        """Return a compact anchored-boundary spelling for diagnostics."""
        anchor = (
            str(self.anchor)
            if isinstance(self.anchor, QualifiedName)
            else f"item {self.anchor.durable_id!r}"
        )
        return f"{self.side.value} {anchor}"


type RelationEndpointRef = ItemRef | DurablePositionRef


@dataclass(frozen=True, slots=True)
class Position:
    """Hold values for one addressable boundary while empty boundaries stay derived."""

    reference: PositionRef | DurablePositionRef
    attributes: tuple[AttributeValue, ...]

    def __post_init__(self) -> None:
        """Canonicalize the values attached to this boundary."""
        _canonicalize_attributes(self)

    def to_data(self) -> dict[str, JsonValue]:
        """Return the position and its values as JSON-serializable data."""
        return {
            "reference": self.reference.to_data(),
            "attributes": _attributes_data(self.attributes),
        }


@dataclass(frozen=True, slots=True)
class RelationInstance:
    """Link item or anchored-boundary endpoints through a declared relation."""

    declaration: QualifiedName
    left: RelationEndpointRef
    right: RelationEndpointRef
    durable_id: str | None = None
    attributes: tuple[AttributeValue, ...] = ()

    def __post_init__(self) -> None:
        """Canonicalize attributes and require a usable carried durable id."""
        _canonicalize_attributes(self)
        if self.durable_id is not None:
            _require_name(self.durable_id, "relation instance durable id")

    def to_data(self) -> dict[str, JsonValue]:
        """Return the instance as JSON-serializable data."""
        return {
            "declaration": self.declaration.to_data(),
            "left": self.left.to_data(),
            "right": self.right.to_data(),
            "durable_id": self.durable_id,
            "attributes": _attributes_data(self.attributes),
        }


@dataclass(frozen=True, slots=True)
class PolyadicRelationInstance:
    """Link two declared, ordered endpoint sequences."""

    declaration: QualifiedName
    sources: tuple[RelationEndpointRef, ...]
    targets: tuple[RelationEndpointRef, ...]
    durable_id: str | None = None
    attributes: tuple[AttributeValue, ...] = ()

    def __post_init__(self) -> None:
        """Canonicalize attributes and require a usable carried durable id."""
        _canonicalize_attributes(self)
        if self.durable_id is not None:
            _require_name(self.durable_id, "relation instance durable id")

    def to_data(self) -> dict[str, JsonValue]:
        """Return the ordered sides as JSON-serializable arrays."""
        return {
            "declaration": self.declaration.to_data(),
            "sources": [endpoint.to_data() for endpoint in self.sources],
            "targets": [endpoint.to_data() for endpoint in self.targets],
            "durable_id": self.durable_id,
            "attributes": _attributes_data(self.attributes),
        }


@dataclass(frozen=True, slots=True)
class Graph:
    """Hold a validated immutable graph and derive order and empty boundaries.

    Collections keyed by names or references are canonicalized because supply
    order has no graph meaning: namespaces, relation and attribute declarations,
    every attribute-value collection, sparse position values, and relation-side
    allowed kinds and tiers.  Tiers, tier items, relation instances, and polyadic
    endpoint sequences remain ordered because their sequence carries graph meaning.
    """

    namespaces: tuple[NamespaceDeclaration, ...]
    tiers: tuple[Tier, ...]
    relation_declarations: tuple[RelationDeclaration, ...]
    relations: tuple[RelationInstance, ...] = ()
    attribute_declarations: tuple[AttributeDeclaration, ...] = ()
    position_values: tuple[Position, ...] = ()
    attributes: tuple[AttributeValue, ...] = ()
    polyadic_relations: tuple[PolyadicRelationInstance, ...] = ()
    _tiers_by_name: dict[QualifiedName, Tier] = field(
        init=False, repr=False, compare=False
    )
    _types_by_tier: dict[QualifiedName, QualifiedName] = field(
        init=False, repr=False, compare=False
    )
    _positions_by_ref: dict[PositionRef, Position] = field(
        init=False, repr=False, compare=False
    )
    _items_by_id: dict[str, ItemRef] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Canonicalize keyed collections and validate the complete graph."""
        _canonicalize_attributes(self)
        object.__setattr__(
            self,
            "namespaces",
            tuple(sorted(self.namespaces, key=lambda item: item.namespace)),
        )
        object.__setattr__(
            self,
            "relation_declarations",
            tuple(sorted(self.relation_declarations, key=lambda item: item.name)),
        )
        object.__setattr__(
            self,
            "attribute_declarations",
            tuple(sorted(self.attribute_declarations, key=lambda item: item.name)),
        )
        namespaces = _unique_by_name(
            ((binding.prefix, binding) for binding in self.namespaces),
            "namespace prefix",
        )
        duplicate_uri = _duplicate([binding.namespace for binding in self.namespaces])
        if duplicate_uri is not None:
            raise GraphValidationError(
                f"duplicate namespace URI {duplicate_uri!r}; each URI needs one prefix"
            )
        declared_namespaces = {binding.namespace for binding in namespaces.values()}
        tiers_by_name = _unique_by_name(
            ((tier.declaration.name, tier) for tier in self.tiers), "tier"
        )
        declarations = _unique_by_name(
            (
                (declaration.name, declaration)
                for declaration in self.relation_declarations
            ),
            "relation declaration",
        )
        attributes = _unique_by_name(
            (
                (declaration.name, declaration)
                for declaration in self.attribute_declarations
            ),
            "attribute declaration",
        )
        qualified_names = [
            *(tier.declaration.name for tier in self.tiers),
            *(declaration.name for declaration in self.relation_declarations),
            *(
                declaration.item_type
                for declaration in self.relation_declarations
                if isinstance(declaration, SimpleRelationDeclaration)
            ),
            *(
                endpoint_type
                for declaration in self.relation_declarations
                if isinstance(declaration, BipartiteRelationDeclaration)
                for endpoint_type in (declaration.left_type, declaration.right_type)
            ),
            *(
                tier
                for declaration in self.relation_declarations
                if isinstance(declaration, PolyadicRelationDeclaration)
                for side in (declaration.sources, declaration.targets)
                for tier in (() if side.tiers is None else side.tiers)
            ),
            *(
                declaration.targets_subset_of
                for declaration in self.relation_declarations
                if isinstance(declaration, PolyadicRelationDeclaration)
                and declaration.targets_subset_of is not None
            ),
            *(declaration.name for declaration in self.attribute_declarations),
        ]
        for name in qualified_names:
            if name.namespace not in declared_namespaces:
                raise GraphValidationError(
                    f"qualified name {str(name)!r} uses undeclared namespace {name.namespace!r}"
                )
        simple = [
            declaration
            for declaration in self.relation_declarations
            if isinstance(declaration, SimpleRelationDeclaration)
        ]
        types_by_tier = _unique_simple_types(simple, tiers_by_name)
        items_by_id = {
            item.durable_id: ItemRef(tier.declaration.name, index)
            for tier in self.tiers
            for index, item in enumerate(tier.items)
            if item.durable_id is not None
        }
        durable_ids = [
            (item.durable_id, f"item at tier {tier_index}, index {item_index}")
            for tier_index, tier in enumerate(self.tiers)
            for item_index, item in enumerate(tier.items)
            if item.durable_id is not None
        ]
        durable_ids.extend(
            (relation.durable_id, f"relation instance {index}")
            for index, relation in enumerate(self.relations)
            if relation.durable_id is not None
        )
        durable_ids.extend(
            (relation.durable_id, f"polyadic relation instance {index}")
            for index, relation in enumerate(self.polyadic_relations)
            if relation.durable_id is not None
        )
        _validate_attributes(self.attributes, AttributeDomain.DOCUMENT, attributes)
        for tier in self.tiers:
            _validate_attributes(tier.attributes, AttributeDomain.TIER, attributes)
            for item in tier.items:
                _validate_attributes(item.attributes, AttributeDomain.ITEM, attributes)
        for declaration in self.relation_declarations:
            _validate_attributes(
                declaration.attributes,
                AttributeDomain.RELATION_DECLARATION,
                attributes,
            )
        bipartite = {
            name: declaration
            for name, declaration in declarations.items()
            if isinstance(declaration, BipartiteRelationDeclaration)
        }
        polyadic = {
            name: declaration
            for name, declaration in declarations.items()
            if isinstance(declaration, PolyadicRelationDeclaration)
        }
        for index, relation in enumerate(self.relations):
            _validate_attributes(
                relation.attributes, AttributeDomain.RELATION_INSTANCE, attributes
            )
            bipartite_declaration = bipartite.get(relation.declaration)
            if bipartite_declaration is None:
                raise GraphValidationError(
                    f"relation instance {index} names {str(relation.declaration)!r}; "
                    "a bipartite relation declaration is required"
                )
            _validate_endpoint(
                index,
                "left",
                relation.left,
                bipartite_declaration.left_type,
                bipartite_declaration.left_endpoint,
                tiers_by_name,
                types_by_tier,
                items_by_id,
            )
            _validate_endpoint(
                index,
                "right",
                relation.right,
                bipartite_declaration.right_type,
                bipartite_declaration.right_endpoint,
                tiers_by_name,
                types_by_tier,
                items_by_id,
            )
        for index, polyadic_relation in enumerate(self.polyadic_relations):
            _validate_attributes(
                polyadic_relation.attributes,
                AttributeDomain.RELATION_INSTANCE,
                attributes,
            )
            polyadic_declaration = polyadic.get(polyadic_relation.declaration)
            if polyadic_declaration is None:
                raise GraphValidationError(
                    f"polyadic relation instance {index} names {str(polyadic_relation.declaration)!r}; "
                    "a polyadic relation declaration is required"
                )
            _validate_polyadic_instance(
                index,
                polyadic_relation,
                polyadic_declaration,
                tiers_by_name,
                items_by_id,
            )
        positioned_values: list[tuple[PositionRef, Position]] = []
        for position in self.position_values:
            coordinate = _resolve_position_reference(
                position.reference,
                tiers_by_name,
                items_by_id,
                GraphValidationError,
            )
            positioned_values.append((coordinate, position))
            if not position.attributes:
                raise GraphValidationError(
                    f"position {str(position.reference)!r} has no attribute values; "
                    "empty positions are derived"
                )
            _validate_attributes(
                position.attributes, AttributeDomain.POSITION, attributes
            )
        positions_by_ref = _unique_by_name(positioned_values, "position value")
        object.__setattr__(
            self,
            "position_values",
            tuple(
                position
                for _, position in sorted(
                    positioned_values,
                    key=lambda entry: (entry[0].tier, entry[0].index),
                )
            ),
        )
        _require_unique_durable_ids(durable_ids)
        _validate_relation_invariants(
            self.relations, bipartite, tiers_by_name, items_by_id
        )
        _validate_polyadic_invariants(
            self.polyadic_relations, polyadic, tiers_by_name, items_by_id
        )
        object.__setattr__(self, "_tiers_by_name", tiers_by_name)
        object.__setattr__(self, "_types_by_tier", types_by_tier)
        object.__setattr__(self, "_positions_by_ref", positions_by_ref)
        object.__setattr__(
            self,
            "_items_by_id",
            items_by_id,
        )

    def positions(self, tier: QualifiedName) -> tuple[Position, ...]:
        """Return every addressable boundary with sparse values joined on demand."""
        member_tier = self._tiers_by_name.get(tier)
        if member_tier is None:
            raise ValueError(f"position tier {str(tier)!r} is not declared")
        positions = []
        for index in range(len(member_tier.items) + 1):
            reference = PositionRef(tier, index)
            stored = self._positions_by_ref.get(reference)
            positions.append(
                Position(reference, stored.attributes)
                if stored is not None
                else Position(reference, ())
            )
        return tuple(positions)

    def canonical_items(self) -> tuple[ItemRef, ...]:
        """Compute tier-major canonical order without storing it."""
        return tuple(
            ItemRef(tier.declaration.name, index)
            for tier in self.tiers
            for index in range(len(tier.items))
        )

    def item_type(self, reference: ItemRef) -> QualifiedName:
        """Return the type supplied by simple membership or refuse an untyped tier."""
        _validate_reference(
            reference, "item reference", self._tiers_by_name, ValueError
        )
        item_type = self._types_by_tier.get(reference.tier)
        if item_type is None:
            raise ValueError(
                f"item reference tier {str(reference.tier)!r} has no simple relation and is untyped"
            )
        return item_type

    def resolve_item(self, reference: ItemRef | DurableItemRef) -> ItemRef:
        """Resolve either identity level to the item's current coordinate."""
        if isinstance(reference, ItemRef):
            _validate_reference(
                reference, "item reference", self._tiers_by_name, ValueError
            )
            return reference
        if not isinstance(reference, DurableItemRef):
            raise TypeError(
                "item resolution expected ItemRef or DurableItemRef; "
                f"got {type(reference).__name__}"
            )
        coordinate = self._items_by_id.get(reference.durable_id)
        if coordinate is None:
            raise ValueError(f"unknown durable item id {reference.durable_id!r}")
        return coordinate

    def resolve_position(
        self, reference: PositionRef | DurablePositionRef
    ) -> PositionRef:
        """Resolve either identity level to the position's current coordinate."""
        if isinstance(reference, PositionRef):
            _validate_position(reference, self._tiers_by_name, ValueError)
            return reference
        if not isinstance(reference, DurablePositionRef):
            raise TypeError(
                "position resolution expected PositionRef or DurablePositionRef; "
                f"got {type(reference).__name__}"
            )
        return _resolve_position_reference(
            reference, self._tiers_by_name, self._items_by_id, ValueError
        )

    def promote_item(
        self, reference: ItemRef, durable_id: str
    ) -> tuple[Graph, DurableItemRef]:
        """Return a graph carrying the caller's semantic id for one item.

        The durable id is as-built content, so adding it changes canonical bytes
        and the construction fingerprint.  Repeating the same id is idempotent;
        a different id is refused and never replaces the established identity.
        """
        _validate_reference(
            reference, "item reference", self._tiers_by_name, ValueError
        )
        tier = self._tiers_by_name[reference.tier]
        item = tier.items[reference.index]
        if item.durable_id is not None:
            if item.durable_id != durable_id:
                raise ValueError(
                    f"item {str(reference)!r} already carries durable id "
                    f"{item.durable_id!r}; refused conflicting durable id "
                    f"{durable_id!r}"
                )
            return self, DurableItemRef(item.durable_id)
        items = list(tier.items)
        items[reference.index] = Item(durable_id, item.attributes)
        replacement = Tier(tier.declaration, tuple(items), tier.attributes)
        tiers = tuple(
            replacement if candidate is tier else candidate for candidate in self.tiers
        )
        return self._replace(tiers=tiers), DurableItemRef(durable_id)

    def promote_position(
        self, reference: PositionRef, durable_id: str
    ) -> tuple[Graph, DurablePositionRef]:
        """Return a graph whose boundary anchor has durable identity.

        Promoting an interior boundary promotes its anchor item.  That durable
        id is as-built content, so adding it changes canonical bytes and the
        construction fingerprint.  An anchor carrying a different id refuses
        the requested boundary identity rather than replacing its own.
        """
        _validate_position(reference, self._tiers_by_name, ValueError)
        tier = self._tiers_by_name[reference.tier]
        if reference.index == 0:
            promoted = self
            durable = DurablePositionRef(reference.tier, BoundarySide.BEFORE)
        elif reference.index == len(tier.items):
            promoted = self
            durable = DurablePositionRef(reference.tier, BoundarySide.AFTER)
        else:
            anchor_reference = ItemRef(reference.tier, reference.index)
            anchor_item = tier.items[reference.index]
            if (
                anchor_item.durable_id is not None
                and anchor_item.durable_id != durable_id
            ):
                raise ValueError(
                    f"position {str(reference)!r} is before an anchor carrying "
                    f"durable id {anchor_item.durable_id!r}; refused conflicting "
                    f"boundary durable id {durable_id!r}"
                )
            promoted, anchor = self.promote_item(anchor_reference, durable_id)
            durable = DurablePositionRef(anchor, BoundarySide.BEFORE)
        position = promoted._positions_by_ref.get(reference)
        if position is None:
            return promoted, durable
        if isinstance(position.reference, DurablePositionRef):
            return promoted, position.reference
        anchored = Position(durable, position.attributes)
        values = tuple(
            anchored if candidate is position else candidate
            for candidate in promoted.position_values
        )
        return promoted._replace(position_values=values), durable

    def _replace(
        self,
        *,
        tiers: tuple[Tier, ...] | None = None,
        position_values: tuple[Position, ...] | None = None,
    ) -> Graph:
        """Rebuild immutable graph content for a promotion operation."""
        return Graph(
            self.namespaces,
            self.tiers if tiers is None else tiers,
            self.relation_declarations,
            self.relations,
            self.attribute_declarations,
            self.position_values if position_values is None else position_values,
            self.attributes,
            self.polyadic_relations,
        )

    def to_data(self) -> dict[str, JsonValue]:
        """Return graph content in canonical declaration order as JSON data."""
        return {
            "namespaces": [binding.to_data() for binding in self.namespaces],
            "tiers": [tier.to_data() for tier in self.tiers],
            "relation_declarations": [
                declaration.to_data() for declaration in self.relation_declarations
            ],
            "relations": [
                relation.to_data()
                for relation in (*self.relations, *self.polyadic_relations)
            ],
            "attribute_declarations": [
                declaration.to_data() for declaration in self.attribute_declarations
            ],
            "position_values": [
                position.to_data() for position in self.position_values
            ],
            "attributes": _attributes_data(self.attributes),
        }


@dataclass(slots=True)
class _MutableTier:
    declaration: TierDeclaration
    items: list[Item]
    attributes: list[AttributeValue]


class _GraphBuilder:
    """Accumulate trusted machine state before one complete graph validation."""

    def __init__(self) -> None:
        self.namespaces: list[NamespaceDeclaration] = []
        self.tiers: list[_MutableTier] = []
        self.relation_declarations: list[RelationDeclaration] = []
        self.relations: list[RelationInstance] = []
        self.attribute_declarations: list[AttributeDeclaration] = []
        self.position_values: list[Position] = []
        self.attributes: list[AttributeValue] = []
        self.polyadic_relations: list[PolyadicRelationInstance] = []
        self.declared_namespaces: set[str] = set()
        self.tiers_by_name: dict[QualifiedName, _MutableTier] = {}
        self.items_by_id: dict[str, ItemRef] = {}
        self.types_by_tier: dict[QualifiedName, QualifiedName] = {}
        self.declarations_by_name: dict[QualifiedName, RelationDeclaration] = {}
        self.declaration_indexes: dict[QualifiedName, int] = {}
        self.attributes_by_name: dict[QualifiedName, AttributeDeclaration] = {}
        self.positions_by_coordinate: dict[PositionRef, int] = {}
        self.after_position_by_tier: dict[QualifiedName, int] = {}
        self.polyadic_targets_by_source: dict[
            tuple[QualifiedName, ItemRef | PositionRef],
            set[ItemRef | PositionRef],
        ] = {}

    def _tier_views(self) -> dict[QualifiedName, Tier]:
        # The validators inspect only declaration identity and item count/indexing.
        return cast(dict[QualifiedName, Tier], self.tiers_by_name)

    def _require_namespaces(self, names: Iterable[QualifiedName]) -> None:
        for name in names:
            if name.namespace not in self.declared_namespaces:
                raise ValueError(
                    f"qualified name {str(name)!r} uses undeclared namespace {name.namespace!r}"
                )

    def _resolve_item(self, reference: ItemRef | DurableItemRef) -> ItemRef:
        if isinstance(reference, ItemRef):
            _validate_reference(
                reference, "item reference", self._tier_views(), ValueError
            )
            return reference
        coordinate = self.items_by_id.get(reference.durable_id)
        if coordinate is None:
            raise ValueError(f"unknown durable item id {reference.durable_id!r}")
        return coordinate

    def _resolve_position(
        self, reference: PositionRef | DurablePositionRef
    ) -> PositionRef:
        return _resolve_position_reference(
            reference, self._tier_views(), self.items_by_id, ValueError
        )

    def _finish(self) -> Graph:
        return Graph(
            tuple(self.namespaces),
            tuple(
                Tier(tier.declaration, tuple(tier.items), tuple(tier.attributes))
                for tier in self.tiers
            ),
            tuple(self.relation_declarations),
            tuple(self.relations),
            tuple(self.attribute_declarations),
            tuple(self.position_values),
            tuple(self.attributes),
            tuple(self.polyadic_relations),
        )


def _canonical_lexical(value_type: XsdType, lexical: str) -> str:
    # xsd:string has whiteSpace=preserve; the remaining admitted types use collapse.
    if value_type is XsdType.STRING:
        return lexical
    lexical = re.sub(r"[ \t\r\n]+", " ", lexical).strip(" ")
    if value_type is XsdType.BOOLEAN:
        if lexical in {"true", "1"}:
            return "true"
        if lexical in {"false", "0"}:
            return "false"
        raise ValueError(lexical)
    if value_type is XsdType.INTEGER:
        if _INTEGER_LEXICAL.fullmatch(lexical) is None:
            raise ValueError(lexical)
        return str(int(lexical))
    if value_type is XsdType.DECIMAL:
        if _DECIMAL_LEXICAL.fullmatch(lexical) is None:
            raise ValueError(lexical)
        value = Decimal(lexical)
        if value.is_zero():
            return "0.0"
        fixed = format(value, "f")
        whole, separator, fraction = fixed.partition(".")
        fraction = fraction.rstrip("0") or "0"
        if not separator:
            fraction = "0"
        return f"{whole}.{fraction}"
    if _DOUBLE_LEXICAL.fullmatch(lexical) is None:
        raise ValueError(lexical)
    if lexical == "NaN":
        return "NaN"
    if lexical in {"INF", "+INF"}:
        return "INF"
    if lexical == "-INF":
        return "-INF"
    double_value = float(lexical)
    if math.isinf(double_value):
        return "-INF" if double_value < 0 else "INF"
    if double_value == 0.0:
        return "-0.0E0" if math.copysign(1.0, double_value) < 0 else "0.0E0"
    shortest = repr(double_value).lower()
    coefficient, _, exponent_text = shortest.partition("e")
    exponent = int(exponent_text) if exponent_text else 0
    sign = ""
    if coefficient.startswith("-"):
        sign, coefficient = "-", coefficient[1:]
    whole, _, fraction = coefficient.partition(".")
    digits = (whole + fraction).lstrip("0")
    first_nonzero = (
        next(index for index, digit in enumerate(whole) if digit != "0")
        if int(whole)
        else None
    )
    if first_nonzero is not None:
        scientific_exponent = exponent + len(whole) - first_nonzero - 1
    else:
        leading_fraction_zeros = len(fraction) - len(fraction.lstrip("0"))
        scientific_exponent = exponent - leading_fraction_zeros - 1
    mantissa_tail = digits[1:].rstrip("0") or "0"
    return f"{sign}{digits[0]}.{mantissa_tail}E{scientific_exponent}"


def _attributes_data(attributes: tuple[AttributeValue, ...]) -> list[JsonValue]:
    return [attribute.to_data() for attribute in attributes]


class _AttributeCarrier(Protocol):
    @property
    def attributes(self) -> tuple[AttributeValue, ...]:
        """Return the carrier's attribute values, in canonical name order."""
        ...


def _canonicalize_attributes(value: _AttributeCarrier) -> None:
    attributes = value.attributes
    object.__setattr__(
        value, "attributes", tuple(sorted(attributes, key=lambda item: item.name))
    )


def _require_name(value: str, subject: str) -> None:
    if not value:
        raise GraphValidationError(f"{subject} {value!r} must not be empty")


def _unique_by_name[NameKey, NamedValue](
    pairs: Iterable[tuple[NameKey, NamedValue]], subject: str
) -> dict[NameKey, NamedValue]:
    result: dict[NameKey, NamedValue] = {}
    for name, value in pairs:
        if name in result:
            raise GraphValidationError(
                f"duplicate {subject} {str(name)!r}; names must be unique"
            )
        result[name] = value
    return result


def _duplicate(values: Iterable[str]) -> str | None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            return value
        seen.add(value)
    return None


def _require_unique_durable_ids(values: Iterable[tuple[str, str]]) -> None:
    sources: dict[str, str] = {}
    for durable_id, source in values:
        previous = sources.get(durable_id)
        if previous is not None:
            raise GraphValidationError(
                f"duplicate durable id {durable_id!r}; {previous} collides with {source}"
            )
        sources[durable_id] = source


def _validate_attributes(
    values: tuple[AttributeValue, ...],
    domain: AttributeDomain,
    declarations: Mapping[QualifiedName, AttributeDeclaration],
) -> None:
    _unique_by_name(((value.name, value) for value in values), "attribute value")
    for value in values:
        candidate = declarations.get(value.name)
        if not isinstance(candidate, AttributeDeclaration):
            raise GraphValidationError(f"attribute {str(value.name)!r} is undeclared")
        if candidate.domain is not domain:
            raise GraphValidationError(
                f"attribute {str(value.name)!r} has domain {candidate.domain.value!r}; "
                f"it cannot occur on {domain.value!r}"
            )
        if candidate.value_type is not value.value_type:
            raise GraphValidationError(
                f"attribute {str(value.name)!r} has type {candidate.value_type.value!r}; "
                f"value has type {value.value_type.value!r}"
            )


def _unique_simple_types(
    declarations: list[SimpleRelationDeclaration],
    tiers: Mapping[QualifiedName, Tier],
) -> dict[QualifiedName, QualifiedName]:
    result: dict[QualifiedName, QualifiedName] = {}
    for declaration in declarations:
        if declaration.tier not in tiers:
            raise GraphValidationError(
                f"simple relation {str(declaration.name)!r} names undeclared tier "
                f"{str(declaration.tier)!r}"
            )
        if declaration.tier in result:
            raise GraphValidationError(
                f"tier {str(declaration.tier)!r} has multiple simple relations; at most one is allowed"
            )
        result[declaration.tier] = declaration.item_type
    return result


def _validate_reference(
    reference: ItemRef,
    subject: str,
    tiers: dict[QualifiedName, Tier],
    error_type: type[ValueError],
) -> None:
    tier = tiers.get(reference.tier)
    if tier is None:
        raise error_type(f"{subject} names undeclared tier {str(reference.tier)!r}")
    if reference.index < 0 or reference.index >= len(tier.items):
        raise error_type(
            f"{subject} {str(reference)!r} is outside tier {str(reference.tier)!r}"
        )


def _validate_position(
    reference: PositionRef,
    tiers: dict[QualifiedName, Tier],
    error_type: type[ValueError],
) -> None:
    tier = tiers.get(reference.tier)
    if tier is None:
        raise error_type(
            f"position {str(reference)!r} names undeclared tier {str(reference.tier)!r}"
        )
    if reference.index < 0 or reference.index > len(tier.items):
        raise error_type(
            f"position {str(reference)!r} is outside tier {str(reference.tier)!r}"
        )


def _resolve_position_reference(
    reference: PositionRef | DurablePositionRef,
    tiers: dict[QualifiedName, Tier],
    items_by_id: dict[str, ItemRef],
    error_type: type[ValueError],
) -> PositionRef:
    if isinstance(reference, PositionRef):
        _validate_position(reference, tiers, error_type)
        return reference
    if isinstance(reference.anchor, QualifiedName):
        tier = tiers.get(reference.anchor)
        if tier is None:
            raise error_type(
                f"durable position tier anchor {str(reference.anchor)!r} is not declared"
            )
        return PositionRef(
            reference.anchor,
            0 if reference.side is BoundarySide.BEFORE else len(tier.items),
        )
    coordinate = items_by_id.get(reference.anchor.durable_id)
    if coordinate is None:
        raise error_type(
            f"durable position anchor item {reference.anchor.durable_id!r} was not found"
        )
    return PositionRef(
        coordinate.tier,
        coordinate.index
        if reference.side is BoundarySide.BEFORE
        else coordinate.index + 1,
    )


def _require_integral_index(index: object, subject: str, offender: object) -> None:
    if isinstance(index, bool) or not isinstance(index, int):
        raise GraphValidationError(
            f"{subject} {offender!r} has non-integral index {index!r}"
        )


def _require_integral_bound(value: object, subject: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GraphValidationError(f"{subject} {value!r} must be a nonnegative integer")


def _require_boolean(value: object, subject: str) -> None:
    if not isinstance(value, bool):
        raise GraphValidationError(f"{subject} {value!r} must be boolean")


def _validate_endpoint(
    relation_index: int,
    side: str,
    reference: ItemRef | DurablePositionRef,
    expected_type: QualifiedName,
    expected_kind: RelationEndpointKind,
    tiers: dict[QualifiedName, Tier],
    types: dict[QualifiedName, QualifiedName],
    items_by_id: dict[str, ItemRef],
) -> None:
    subject = f"relation instance {relation_index} {side} endpoint"
    actual_kind = (
        RelationEndpointKind.ITEM
        if isinstance(reference, ItemRef)
        else RelationEndpointKind.BOUNDARY
    )
    if actual_kind is not expected_kind:
        article = "an" if actual_kind is RelationEndpointKind.ITEM else "a"
        expected_article = "an" if expected_kind is RelationEndpointKind.ITEM else "a"
        raise GraphValidationError(
            f"{subject} {str(reference)!r} is {article} {actual_kind.value}; "
            f"declaration requires {expected_article} {expected_kind.value}"
        )
    if isinstance(reference, ItemRef):
        _validate_reference(reference, subject, tiers, GraphValidationError)
        tier_name = reference.tier
    else:
        tier_name = _boundary_anchor_tier(reference, subject, tiers, items_by_id)
    actual_type = types.get(tier_name)
    if actual_type is None:
        raise GraphValidationError(
            f"{subject} {str(reference)!r} belongs to untyped tier {str(tier_name)!r}"
        )
    if actual_type != expected_type:
        raise GraphValidationError(
            f"{subject} {str(reference)!r} has type {str(actual_type)!r}; "
            f"expected {str(expected_type)!r}"
        )


def _boundary_anchor_tier(
    reference: DurablePositionRef,
    subject: str,
    tiers: dict[QualifiedName, Tier],
    items_by_id: dict[str, ItemRef],
) -> QualifiedName:
    if isinstance(reference.anchor, QualifiedName):
        if reference.anchor not in tiers:
            raise GraphValidationError(
                f"{subject} {str(reference)!r} names undeclared tier "
                f"{str(reference.anchor)!r}"
            )
        return reference.anchor
    coordinate = items_by_id.get(reference.anchor.durable_id)
    if coordinate is None:
        raise GraphValidationError(
            f"{subject} {str(reference)!r} names missing anchor item "
            f"{reference.anchor.durable_id!r}"
        )
    return coordinate.tier


def _validate_relation_invariants(
    relations: tuple[RelationInstance, ...],
    declarations: dict[QualifiedName, BipartiteRelationDeclaration],
    tiers: dict[QualifiedName, Tier],
    items_by_id: dict[str, ItemRef],
) -> None:
    for name, declaration in declarations.items():
        indexed = [
            (index, edge)
            for index, edge in enumerate(relations)
            if edge.declaration == name
        ]
        resolved = [
            (
                index,
                edge,
                _resolve_relation_endpoint(
                    edge.left, tiers, items_by_id, GraphValidationError
                ),
                _resolve_relation_endpoint(
                    edge.right, tiers, items_by_id, GraphValidationError
                ),
            )
            for index, edge in indexed
        ]
        if declaration.single_parent:
            parents: dict[ItemRef | PositionRef, tuple[int, ItemRef | PositionRef]] = {}
            for index, edge, left, right in resolved:
                previous = parents.get(right)
                if previous is not None and previous[1] != left:
                    raise GraphValidationError(
                        f"relation instance {index} gives {str(edge.right)!r} a second "
                        f"parent in {str(name)!r}; first parent is relation instance {previous[0]}"
                    )
                parents[right] = (index, left)
        if declaration.acyclic:
            _require_acyclic(name, resolved)


def _validate_polyadic_instance(
    index: int,
    relation: PolyadicRelationInstance,
    declaration: PolyadicRelationDeclaration,
    tiers: dict[QualifiedName, Tier],
    items_by_id: dict[str, ItemRef],
) -> None:
    for label, endpoints, side in (
        ("source", relation.sources, declaration.sources),
        ("target", relation.targets, declaration.targets),
    ):
        if not endpoints:
            if not side.allow_empty:
                raise GraphValidationError(
                    f"relation instance {index} has an empty {label} side"
                )
            continue
        if len(endpoints) < side.minimum or (
            side.maximum is not None and len(endpoints) > side.maximum
        ):
            raise GraphValidationError(
                f"relation instance {index} {label} arity {len(endpoints)} is outside "
                f"declared bounds {side.minimum}..{side.maximum}"
            )
        for endpoint_index, endpoint in enumerate(endpoints):
            subject = f"relation instance {index} {label} endpoint {endpoint_index}"
            kind = (
                RelationEndpointKind.ITEM
                if isinstance(endpoint, ItemRef)
                else RelationEndpointKind.BOUNDARY
            )
            if kind not in side.endpoint_kinds:
                raise GraphValidationError(
                    f"{subject} {str(endpoint)!r} has kind {kind.value!r}; "
                    "kind is not allowed by the declaration"
                )
            if isinstance(endpoint, ItemRef):
                _validate_reference(endpoint, subject, tiers, GraphValidationError)
                tier = endpoint.tier
            else:
                tier = _boundary_anchor_tier(endpoint, subject, tiers, items_by_id)
            if side.tiers is not None and tier not in side.tiers:
                raise GraphValidationError(
                    f"{subject} {str(endpoint)!r} belongs to tier {str(tier)!r}; "
                    "tier is not allowed by the declaration"
                )


def _validate_polyadic_invariants(
    relations: tuple[PolyadicRelationInstance, ...],
    declarations: dict[QualifiedName, PolyadicRelationDeclaration],
    tiers: dict[QualifiedName, Tier],
    items_by_id: dict[str, ItemRef],
) -> None:
    """Check promises on hyperedges without reducing composites to one factor.

    A polyadic instance is one parent even when its source composite has several
    endpoints. Membership, conversely, is the union of every base instance for
    a source: no source-uniqueness promise is required of a membership base.
    """
    grouped: dict[QualifiedName, list[tuple[int, PolyadicRelationInstance]]] = {
        name: [] for name in declarations
    }
    for index, relation in enumerate(relations):
        grouped[relation.declaration].append((index, relation))
    first_source_instance: dict[tuple[QualifiedName, ItemRef | PositionRef], int] = {}
    targets_by_source: dict[
        tuple[QualifiedName, ItemRef | PositionRef],
        set[ItemRef | PositionRef],
    ] = {}
    for name, declaration in declarations.items():
        resolved_edges: list[
            tuple[int, RelationInstance, ItemRef | PositionRef, ItemRef | PositionRef]
        ] = []
        resolved_instances: list[
            tuple[
                int,
                tuple[ItemRef | PositionRef, ...],
                tuple[ItemRef | PositionRef, ...],
            ]
        ] = []
        for index, relation in grouped[name]:
            resolved_sources = tuple(
                _resolve_relation_endpoint(
                    endpoint, tiers, items_by_id, GraphValidationError
                )
                for endpoint in relation.sources
            )
            resolved_targets = tuple(
                _resolve_relation_endpoint(
                    endpoint, tiers, items_by_id, GraphValidationError
                )
                for endpoint in relation.targets
            )
            if declaration.distinct_targets and len(set(resolved_targets)) != len(
                resolved_targets
            ):
                raise GraphValidationError(
                    f"relation instance {index} has duplicate declared-distinct targets"
                )
            resolved_instances.append((index, resolved_sources, resolved_targets))
            for source in resolved_sources:
                key = (name, source)
                previous = first_source_instance.get(key)
                if declaration.unique_sources and previous is not None:
                    raise GraphValidationError(
                        f"relation instance {index} repeats source {str(source)!r} in "
                        f"unique-source relation {str(name)!r}; first used by relation instance {previous}"
                    )
                first_source_instance.setdefault(key, index)
                targets_by_source.setdefault(key, set()).update(resolved_targets)
            for source in resolved_sources:
                for target in resolved_targets:
                    resolved_edges.append(
                        (
                            index,
                            RelationInstance(
                                name, relation.sources[0], relation.targets[0]
                            ),
                            source,
                            target,
                        )
                    )
        if declaration.single_parent:
            parents: dict[
                ItemRef | PositionRef,
                tuple[int, tuple[ItemRef | PositionRef, ...]],
            ] = {}
            for index, sources, targets in resolved_instances:
                for target in targets:
                    poly_previous = parents.get(target)
                    if poly_previous is not None and poly_previous[1] != sources:
                        raise GraphValidationError(
                            f"relation instance {index} gives {str(target)!r} a second parent "
                            f"in {str(name)!r}; first parent is relation instance {poly_previous[0]}"
                        )
                    parents[target] = (index, sources)
        if declaration.acyclic:
            _require_acyclic(name, resolved_edges)
    for name, declaration in declarations.items():
        if declaration.targets_subset_of is None:
            continue
        if declaration.targets_subset_of not in declarations:
            raise GraphValidationError(
                f"polyadic relation {str(name)!r} targets-subset-of names undeclared "
                f"polyadic relation {str(declaration.targets_subset_of)!r}"
            )
        for index, relation in grouped[name]:
            for source_ref in relation.sources:
                source = _resolve_relation_endpoint(
                    source_ref, tiers, items_by_id, GraphValidationError
                )
                allowed = targets_by_source.get((declaration.targets_subset_of, source))
                if allowed is None:
                    raise GraphValidationError(
                        f"relation instance {index} source {str(source)!r} has no "
                        f"{str(declaration.targets_subset_of)!r} membership relation"
                    )
                if any(
                    _resolve_relation_endpoint(
                        endpoint, tiers, items_by_id, GraphValidationError
                    )
                    not in allowed
                    for endpoint in relation.targets
                ):
                    raise GraphValidationError(
                        f"relation instance {index} has a target outside "
                        f"{str(declaration.targets_subset_of)!r} membership"
                    )


def _resolve_relation_endpoint(
    reference: RelationEndpointRef,
    tiers: dict[QualifiedName, Tier],
    items_by_id: dict[str, ItemRef],
    error_type: type[ValueError],
) -> ItemRef | PositionRef:
    """Resolve boundary spellings to graph places before invariant comparison."""
    if isinstance(reference, ItemRef):
        return reference
    return _resolve_position_reference(reference, tiers, items_by_id, error_type)


def _require_acyclic(
    name: QualifiedName,
    indexed: list[
        tuple[int, RelationInstance, ItemRef | PositionRef, ItemRef | PositionRef]
    ],
) -> None:
    outgoing: dict[ItemRef | PositionRef, list[tuple[int, ItemRef | PositionRef]]] = {}
    for index, _edge, left, right in indexed:
        outgoing.setdefault(left, []).append((index, right))
    visited: set[ItemRef | PositionRef] = set()
    for root in tuple(outgoing):
        if root in visited:
            continue
        visiting: set[ItemRef | PositionRef] = {root}
        stack: list[tuple[ItemRef | PositionRef, int]] = [(root, 0)]
        while stack:
            node, child_index = stack[-1]
            children = outgoing.get(node, [])
            if child_index == len(children):
                stack.pop()
                visiting.remove(node)
                visited.add(node)
                continue
            edge_index, child = children[child_index]
            stack[-1] = (node, child_index + 1)
            if child in visiting:
                raise GraphValidationError(
                    f"relation instance {edge_index} closes a cycle in acyclic relation "
                    f"{str(name)!r} at {str(child)!r}"
                )
            if child not in visited:
                visiting.add(child)
                stack.append((child, 0))
