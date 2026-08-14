"""Immutable declarations and graph values for ordered parallel structure."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]

_INTEGER_LEXICAL = re.compile(r"[+-]?[0-9]+\Z")
_DECIMAL_LEXICAL = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)\Z")
_DOUBLE_LEXICAL = re.compile(
    r"(?:NaN|[+-]?INF|[+-]?(?:(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?))\Z"
)


class AttributeDomain(StrEnum):
    """The closed set of places where a declared attribute may occur."""

    ITEM = "item"
    TIER = "tier"
    RELATION_DECLARATION = "relation_declaration"
    RELATION_INSTANCE = "relation_instance"
    POSITION = "position"
    DOCUMENT = "document"


class XsdType(StrEnum):
    """The growable XSD datatype subset admitted for attribute values."""

    STRING = "string"
    BOOLEAN = "boolean"
    INTEGER = "integer"
    DECIMAL = "decimal"
    DOUBLE = "double"


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
            raise ValueError(
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
    """Declare an attribute's qualified name, domain, and XSD subset type."""

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
    """Declare typed links and the graph invariants they promise."""

    name: QualifiedName
    left_type: QualifiedName
    right_type: QualifiedName
    single_parent: bool = False
    acyclic: bool = False
    attributes: tuple[AttributeValue, ...] = ()

    def __post_init__(self) -> None:
        """Require structural promises to remain JSON booleans."""
        _require_boolean(self.single_parent, "single-parent promise")
        _require_boolean(self.acyclic, "acyclic promise")

    def to_data(self) -> dict[str, JsonValue]:
        """Return the declaration as JSON-serializable data."""
        return {
            "kind": "bipartite",
            "name": self.name.to_data(),
            "left_type": self.left_type.to_data(),
            "right_type": self.right_type.to_data(),
            "single_parent": self.single_parent,
            "acyclic": self.acyclic,
            "attributes": _attributes_data(self.attributes),
        }


type RelationDeclaration = SimpleRelationDeclaration | BipartiteRelationDeclaration


@dataclass(frozen=True, slots=True)
class Item:
    """Represent a tier member with attributes and a durable identifier seam."""

    durable_id: str | None = None
    attributes: tuple[AttributeValue, ...] = ()

    def __post_init__(self) -> None:
        """Refuse an empty durable identifier when one is carried."""
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
    """Address a boundary by a durable identifier without storing its coordinate."""

    durable_id: str

    def __post_init__(self) -> None:
        """Require the identifier needed for durable resolution."""
        _require_name(self.durable_id, "durable position reference")

    def to_data(self) -> dict[str, JsonValue]:
        """Return the durable reference as JSON-serializable data."""
        return {"durable_id": self.durable_id}


@dataclass(frozen=True, slots=True)
class Position:
    """Hold values for one addressable boundary while empty boundaries stay derived."""

    reference: PositionRef
    attributes: tuple[AttributeValue, ...]
    durable_id: str | None = None

    def __post_init__(self) -> None:
        """Refuse an empty durable identifier when one is carried."""
        if self.durable_id is not None:
            _require_name(self.durable_id, "position durable id")

    def to_data(self) -> dict[str, JsonValue]:
        """Return the position and its values as JSON-serializable data."""
        return {
            "reference": self.reference.to_data(),
            "attributes": _attributes_data(self.attributes),
            "durable_id": self.durable_id,
        }


@dataclass(frozen=True, slots=True)
class RelationInstance:
    """Link two structurally addressed items through a declared relation."""

    declaration: QualifiedName
    left: ItemRef
    right: ItemRef
    durable_id: str | None = None
    attributes: tuple[AttributeValue, ...] = ()

    def __post_init__(self) -> None:
        """Require a usable durable identifier when one is carried."""
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
class Graph:
    """Hold a validated immutable graph and derive order and empty boundaries."""

    namespaces: tuple[NamespaceDeclaration, ...]
    tiers: tuple[Tier, ...]
    relation_declarations: tuple[RelationDeclaration, ...]
    relations: tuple[RelationInstance, ...] = ()
    attribute_declarations: tuple[AttributeDeclaration, ...] = ()
    position_values: tuple[Position, ...] = ()
    attributes: tuple[AttributeValue, ...] = ()
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
    _positions_by_id: dict[str, PositionRef] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        """Validate the graph, requiring one prefix per URI for canonical documents."""
        namespaces = _unique_by_name(
            ((binding.prefix, binding) for binding in self.namespaces),
            "namespace prefix",
        )
        duplicate_uri = _duplicate([binding.namespace for binding in self.namespaces])
        if duplicate_uri is not None:
            raise ValueError(
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
            *(declaration.name for declaration in self.attribute_declarations),
        ]
        for name in qualified_names:
            if name.namespace not in declared_namespaces:
                raise ValueError(
                    f"qualified name {str(name)!r} uses undeclared namespace {name.namespace!r}"
                )
        simple = [
            declaration
            for declaration in self.relation_declarations
            if isinstance(declaration, SimpleRelationDeclaration)
        ]
        types_by_tier = _unique_simple_types(simple, tiers_by_name)
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
        for index, relation in enumerate(self.relations):
            _validate_attributes(
                relation.attributes, AttributeDomain.RELATION_INSTANCE, attributes
            )
            bipartite_declaration = bipartite.get(relation.declaration)
            if bipartite_declaration is None:
                raise ValueError(
                    f"relation instance {index} names {str(relation.declaration)!r}; "
                    "a bipartite relation declaration is required"
                )
            _validate_endpoint(
                index,
                "left",
                relation.left,
                bipartite_declaration.left_type,
                tiers_by_name,
                types_by_tier,
            )
            _validate_endpoint(
                index,
                "right",
                relation.right,
                bipartite_declaration.right_type,
                tiers_by_name,
                types_by_tier,
            )
        positions_by_ref = _unique_by_name(
            ((position.reference, position) for position in self.position_values),
            "position value",
        )
        for position in self.position_values:
            _validate_position(position.reference, tiers_by_name)
            if not position.attributes and position.durable_id is None:
                raise ValueError(
                    f"position {position.reference.to_data()!r} has no attribute values; "
                    "empty positions are derived"
                )
            _validate_attributes(
                position.attributes, AttributeDomain.POSITION, attributes
            )
            if position.durable_id is not None:
                durable_ids.append(
                    (position.durable_id, f"position {position.reference.to_data()!r}")
                )
        _require_unique_durable_ids(durable_ids)
        _validate_relation_invariants(self.relations, bipartite)
        object.__setattr__(self, "_tiers_by_name", tiers_by_name)
        object.__setattr__(self, "_types_by_tier", types_by_tier)
        object.__setattr__(self, "_positions_by_ref", positions_by_ref)
        object.__setattr__(
            self,
            "_items_by_id",
            {
                item.durable_id: ItemRef(tier.declaration.name, index)
                for tier in self.tiers
                for index, item in enumerate(tier.items)
                if item.durable_id is not None
            },
        )
        object.__setattr__(
            self,
            "_positions_by_id",
            {
                position.durable_id: position.reference
                for position in self.position_values
                if position.durable_id is not None
            },
        )

    def positions(self, tier: QualifiedName) -> tuple[Position, ...]:
        """Return every addressable boundary with sparse values joined on demand."""
        member_tier = self._tiers_by_name.get(tier)
        if member_tier is None:
            raise ValueError(f"position tier {str(tier)!r} is not declared")
        positions = []
        for index in range(len(member_tier.items) + 1):
            reference = PositionRef(tier, index)
            positions.append(
                self._positions_by_ref.get(reference, Position(reference, ()))
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
        _validate_reference(reference, "item reference", self._tiers_by_name)
        item_type = self._types_by_tier.get(reference.tier)
        if item_type is None:
            raise ValueError(
                f"item reference tier {str(reference.tier)!r} has no simple relation and is untyped"
            )
        return item_type

    def resolve_item(self, reference: ItemRef | DurableItemRef) -> ItemRef:
        """Resolve either identity level to the item's current coordinate."""
        if isinstance(reference, ItemRef):
            _validate_reference(reference, "item reference", self._tiers_by_name)
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
            _validate_position(reference, self._tiers_by_name)
            return reference
        if not isinstance(reference, DurablePositionRef):
            raise TypeError(
                "position resolution expected PositionRef or DurablePositionRef; "
                f"got {type(reference).__name__}"
            )
        coordinate = self._positions_by_id.get(reference.durable_id)
        if coordinate is None:
            raise ValueError(f"unknown durable position id {reference.durable_id!r}")
        return coordinate

    def promote_item(
        self, reference: ItemRef, durable_id: str
    ) -> tuple[Graph, DurableItemRef]:
        """Return a graph carrying the caller's semantic id for one item."""
        _validate_reference(reference, "item reference", self._tiers_by_name)
        tier = self._tiers_by_name[reference.tier]
        item = tier.items[reference.index]
        if item.durable_id is not None:
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
        """Return a graph sparsely carrying the caller's semantic id for a position."""
        _validate_position(reference, self._tiers_by_name)
        position = self._positions_by_ref.get(reference)
        if position is not None and position.durable_id is not None:
            return self, DurablePositionRef(position.durable_id)
        promoted = Position(
            reference, position.attributes if position is not None else (), durable_id
        )
        values = tuple(
            promoted if candidate.reference == reference else candidate
            for candidate in self.position_values
        )
        if position is None:
            values = (*values, promoted)
        return self._replace(position_values=values), DurablePositionRef(durable_id)

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
        )

    def to_data(self) -> dict[str, JsonValue]:
        """Return graph content in declaration order as JSON-serializable data."""
        return {
            "namespaces": [binding.to_data() for binding in self.namespaces],
            "tiers": [tier.to_data() for tier in self.tiers],
            "relation_declarations": [
                declaration.to_data() for declaration in self.relation_declarations
            ],
            "relations": [relation.to_data() for relation in self.relations],
            "attribute_declarations": [
                declaration.to_data() for declaration in self.attribute_declarations
            ],
            "position_values": [
                position.to_data() for position in self.position_values
            ],
            "attributes": _attributes_data(self.attributes),
        }


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


def _require_name(value: str, subject: str) -> None:
    if not value:
        raise ValueError(f"{subject} {value!r} must not be empty")


def _unique_by_name[NameKey, NamedValue](
    pairs: Iterable[tuple[NameKey, NamedValue]], subject: str
) -> dict[NameKey, NamedValue]:
    result: dict[NameKey, NamedValue] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError(f"duplicate {subject} {str(name)!r}; names must be unique")
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
            raise ValueError(
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
            raise ValueError(f"attribute {str(value.name)!r} is undeclared")
        if candidate.domain is not domain:
            raise ValueError(
                f"attribute {str(value.name)!r} has domain {candidate.domain.value!r}; "
                f"it cannot occur on {domain.value!r}"
            )
        if candidate.value_type is not value.value_type:
            raise ValueError(
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
            raise ValueError(
                f"simple relation {str(declaration.name)!r} names undeclared tier "
                f"{str(declaration.tier)!r}"
            )
        if declaration.tier in result:
            raise ValueError(
                f"tier {str(declaration.tier)!r} has multiple simple relations; at most one is allowed"
            )
        result[declaration.tier] = declaration.item_type
    return result


def _validate_reference(
    reference: ItemRef, subject: str, tiers: dict[QualifiedName, Tier]
) -> None:
    tier = tiers.get(reference.tier)
    if tier is None:
        raise ValueError(f"{subject} names undeclared tier {str(reference.tier)!r}")
    if reference.index < 0 or reference.index >= len(tier.items):
        raise ValueError(
            f"{subject} {reference.to_data()!r} is outside tier {str(reference.tier)!r}"
        )


def _validate_position(
    reference: PositionRef, tiers: dict[QualifiedName, Tier]
) -> None:
    tier = tiers.get(reference.tier)
    if tier is None:
        raise ValueError(
            f"position {reference.to_data()!r} names undeclared tier {str(reference.tier)!r}"
        )
    if reference.index < 0 or reference.index > len(tier.items):
        raise ValueError(
            f"position {reference.to_data()!r} is outside tier {str(reference.tier)!r}"
        )


def _require_integral_index(index: object, subject: str, offender: object) -> None:
    if isinstance(index, bool) or not isinstance(index, int):
        raise ValueError(f"{subject} {offender!r} has non-integral index {index!r}")


def _require_boolean(value: object, subject: str) -> None:
    if not isinstance(value, bool):
        raise ValueError(f"{subject} {value!r} must be boolean")


def _validate_endpoint(
    relation_index: int,
    side: str,
    reference: ItemRef,
    expected_type: QualifiedName,
    tiers: dict[QualifiedName, Tier],
    types: dict[QualifiedName, QualifiedName],
) -> None:
    subject = f"relation instance {relation_index} {side} endpoint"
    _validate_reference(reference, subject, tiers)
    actual_type = types.get(reference.tier)
    if actual_type is None:
        raise ValueError(
            f"{subject} {reference.to_data()!r} belongs to untyped tier {str(reference.tier)!r}"
        )
    if actual_type != expected_type:
        raise ValueError(
            f"{subject} {reference.to_data()!r} has type {str(actual_type)!r}; "
            f"expected {str(expected_type)!r}"
        )


def _validate_relation_invariants(
    relations: tuple[RelationInstance, ...],
    declarations: dict[QualifiedName, BipartiteRelationDeclaration],
) -> None:
    for name, declaration in declarations.items():
        indexed = [
            (index, edge)
            for index, edge in enumerate(relations)
            if edge.declaration == name
        ]
        if declaration.single_parent:
            parents: dict[ItemRef, tuple[int, ItemRef]] = {}
            for index, edge in indexed:
                previous = parents.get(edge.right)
                if previous is not None and previous[1] != edge.left:
                    raise ValueError(
                        f"relation instance {index} gives {edge.right.to_data()!r} a second "
                        f"parent in {str(name)!r}; first parent is relation instance {previous[0]}"
                    )
                parents[edge.right] = (index, edge.left)
        if declaration.acyclic:
            _require_acyclic(name, indexed)


def _require_acyclic(
    name: QualifiedName, indexed: list[tuple[int, RelationInstance]]
) -> None:
    outgoing: dict[ItemRef, list[tuple[int, ItemRef]]] = {}
    for index, edge in indexed:
        outgoing.setdefault(edge.left, []).append((index, edge.right))
    visited: set[ItemRef] = set()
    for root in tuple(outgoing):
        if root in visited:
            continue
        visiting: set[ItemRef] = {root}
        stack: list[tuple[ItemRef, int]] = [(root, 0)]
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
                raise ValueError(
                    f"relation instance {edge_index} closes a cycle in acyclic relation "
                    f"{str(name)!r} at {child.to_data()!r}"
                )
            if child not in visited:
                visiting.add(child)
                stack.append((child, 0))
