"""Convenience construction that lowers directly to the tiergraph kernel."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Set
from dataclasses import dataclass, field, replace
from decimal import Decimal

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
    NamespaceDeclaration,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    Position,
    PositionRef,
    QualifiedName,
    RelationDeclaration,
    RelationEndpointKind,
    RelationInstance,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    XsdType,
)

type Name = str | QualifiedName
type AttributeTarget = (
    None
    | QualifiedName
    | ItemRef
    | DurableItemRef
    | PositionRef
    | DurablePositionRef
    | int
)
type AttributeInput = Mapping[Name, object] | None


class BuilderError(ValueError):
    """Report invalid builder notation before graph-wide kernel validation."""


@dataclass(frozen=True, slots=True)
class ItemSpec:
    """Hold an item spelling until its declared attribute types are available."""

    durable_id: str | None
    attributes: tuple[tuple[Name, object], ...]


@dataclass(frozen=True, slots=True)
class TierHandle:
    """Refer to one tier owned by a particular mutable document builder."""

    name: QualifiedName
    item_type: QualifiedName | None
    membership: QualifiedName | None
    _document: Document = field(repr=False, compare=False)

    def ref(self, index: int) -> ItemRef:
        """Return a checked structural reference into this tier."""
        return self._document._tier_ref(self, index)


@dataclass(frozen=True, slots=True)
class LinkHandle:
    """Refer to one relation declaration owned by a document builder."""

    name: QualifiedName
    _document: Document = field(repr=False, compare=False)


def item(
    durable_id: str | None = None,
    /,
    *,
    attrs: Mapping[Name, object] | None = None,
    **attributes: object,
) -> ItemSpec:
    """Describe an item with values to lower through declared attribute types."""
    values: list[tuple[Name, object]] = []
    if attrs is not None:
        if not isinstance(attrs, Mapping):
            raise BuilderError("item attrs: expected a mapping")
        values.extend(attrs.items())
    values.extend(attributes.items())
    return ItemSpec(durable_id, tuple(values))


def document(namespace: str, *, prefix: str) -> Document:
    """Create a mutable document builder with its required default namespace."""
    return Document(namespace, prefix=prefix)


class Document:
    """Accumulate convenient notation and repeatedly build fresh immutable graphs."""

    def __init__(self, namespace: str, *, prefix: str) -> None:
        self._default_namespace = namespace
        self._namespaces: list[NamespaceDeclaration] = [
            NamespaceDeclaration(prefix, namespace)
        ]
        self._tiers: list[Tier] = []
        self._tier_handles: dict[QualifiedName, TierHandle] = {}
        self._relations: list[RelationDeclaration] = []
        self._instances: list[RelationInstance] = []
        self._polyadic_instances: list[PolyadicRelationInstance] = []
        self._attribute_declarations: list[AttributeDeclaration] = []
        self._positions: list[Position] = []
        self._attributes: list[AttributeValue] = []

    def namespace(self, namespace: str, *, prefix: str) -> None:
        """Register an additional namespace binding."""
        self._namespaces.append(NamespaceDeclaration(prefix, namespace))

    def qname(self, local: str, *, namespace: str | None = None) -> QualifiedName:
        """Expand a local spelling in the default or explicitly selected namespace."""
        return QualifiedName(
            self._default_namespace if namespace is None else namespace, local
        )

    def attribute(
        self,
        name: Name,
        value_type: XsdType | str,
        *,
        domain: AttributeDomain | str = AttributeDomain.ITEM,
    ) -> None:
        """Declare an attribute without inferring its type from Python values."""
        operation = f"attribute {self._local(name)}"
        try:
            declared_type = XsdType(value_type)
            declared_domain = AttributeDomain(domain)
        except ValueError as error:
            raise BuilderError(f"{operation}: {error}") from error
        self._attribute_declarations.append(
            AttributeDeclaration(self._name(name), declared_domain, declared_type)
        )

    def tier(
        self,
        name: Name,
        items: Iterable[Item | ItemSpec | str | None] = (),
        *,
        item_type: Name | None = None,
        membership: Name | None = None,
        long_name: str | None = None,
        attributes: AttributeInput = None,
    ) -> TierHandle:
        """Add an ordered tier, optionally with one explicit membership declaration."""
        tier_name = self._name(name)
        local = tier_name.local_name
        if (item_type is None) != (membership is None):
            raise BuilderError(
                f"tier {local} needs both item_type and membership, or neither"
            )
        lowered_items: list[Item] = []
        if isinstance(items, Set | Mapping):
            raise BuilderError(f"tier {local}: items must be an ordered iterable")
        try:
            source_items = tuple(items)
        except TypeError as error:
            raise BuilderError(f"tier {local}: items must be iterable") from error
        for index, value in enumerate(source_items):
            if isinstance(value, Item):
                lowered_items.append(value)
            elif isinstance(value, ItemSpec):
                lowered_items.append(
                    Item(
                        value.durable_id,
                        self._values(value.attributes, f"tier {local} item {index}"),
                    )
                )
            elif isinstance(value, str):
                lowered_items.append(Item(value))
            elif value is None:
                lowered_items.append(Item())
            else:
                raise BuilderError(
                    f"tier {local} item {index}: expected Item, ItemSpec, str, or None"
                )
        qualified_type = None if item_type is None else self._name(item_type)
        qualified_membership = None if membership is None else self._name(membership)
        self._tiers.append(
            Tier(
                TierDeclaration(tier_name, local if long_name is None else long_name),
                tuple(lowered_items),
                self._mapping_values(attributes, f"tier {local}"),
            )
        )
        handle = TierHandle(tier_name, qualified_type, qualified_membership, self)
        self._tier_handles[tier_name] = handle
        if qualified_type is not None and qualified_membership is not None:
            self._relations.append(
                SimpleRelationDeclaration(
                    qualified_membership, tier_name, qualified_type
                )
            )
        return handle

    def link(
        self,
        name: Name,
        source: TierHandle | Name,
        target: TierHandle | Name,
        pairs: Iterable[tuple[object, object]] = (),
        *,
        source_type: Name | None = None,
        target_type: Name | None = None,
        left_endpoint: RelationEndpointKind | str = RelationEndpointKind.ITEM,
        right_endpoint: RelationEndpointKind | str = RelationEndpointKind.ITEM,
        single_parent: bool = False,
        acyclic: bool = False,
        attributes: AttributeInput = None,
    ) -> LinkHandle:
        """Declare a bipartite relation and add its ordered endpoint pairs."""
        relation_name = self._name(name)
        local = relation_name.local_name
        try:
            normalized_left_endpoint = RelationEndpointKind(left_endpoint)
            normalized_right_endpoint = RelationEndpointKind(right_endpoint)
        except (TypeError, ValueError) as error:
            raise BuilderError(
                f"link {local}: invalid endpoint kind: {error}"
            ) from error
        source_handle = self._tier(source, f"link {local} source")
        target_handle = self._tier(target, f"link {local} target")
        left_type = self._endpoint_type(
            local, "source", source_handle, source_type, normalized_left_endpoint
        )
        right_type = self._endpoint_type(
            local, "target", target_handle, target_type, normalized_right_endpoint
        )
        declaration = BipartiteRelationDeclaration(
            relation_name,
            left_type,
            right_type,
            normalized_left_endpoint,
            normalized_right_endpoint,
            single_parent,
            acyclic,
            self._mapping_values(attributes, f"link {local}"),
        )
        if isinstance(pairs, Set | Mapping):
            raise BuilderError(f"link {local}: pairs must be an ordered iterable")
        try:
            pair_values = tuple(pairs)
        except TypeError as error:
            raise BuilderError(f"link {local}: pairs must be iterable") from error
        instances: list[RelationInstance] = []
        for index, pair in enumerate(pair_values):
            try:
                left, right = pair
            except (TypeError, ValueError) as error:
                raise BuilderError(
                    f"link {local} pair {index}: expected two endpoints"
                ) from error
            instances.append(
                RelationInstance(
                    relation_name,
                    self._endpoint(
                        local,
                        "source",
                        source_handle,
                        left,
                        normalized_left_endpoint,
                    ),
                    self._endpoint(
                        local,
                        "target",
                        target_handle,
                        right,
                        normalized_right_endpoint,
                    ),
                )
            )
        self._relations.append(declaration)
        self._instances.extend(instances)
        return LinkHandle(relation_name, self)

    def declare(self, declaration: RelationDeclaration) -> None:
        """Add an already-constructed kernel relation declaration as-is."""
        if not isinstance(
            declaration,
            SimpleRelationDeclaration
            | BipartiteRelationDeclaration
            | PolyadicRelationDeclaration,
        ):
            raise BuilderError(
                "declare: expected Simple, Bipartite, or Polyadic RelationDeclaration"
            )
        self._relations.append(declaration)

    def relate(self, instance: RelationInstance | PolyadicRelationInstance) -> None:
        """Add an already-constructed kernel relation instance as-is."""
        if isinstance(instance, RelationInstance):
            self._instances.append(instance)
        elif isinstance(instance, PolyadicRelationInstance):
            self._polyadic_instances.append(instance)
        else:
            raise BuilderError(
                "relate: expected RelationInstance or PolyadicRelationInstance"
            )

    def add(
        self, value: RelationInstance | PolyadicRelationInstance | Position
    ) -> None:
        """Add an already-constructed relation instance or sparse position value."""
        if isinstance(value, Position):
            self._positions.append(value)
        else:
            self.relate(value)

    def attach(
        self,
        domain: AttributeDomain | str,
        target: AttributeTarget,
        values: Mapping[Name, object],
    ) -> None:
        """Attach declared values using the kernel's attribute-domain target forms."""
        try:
            declared_domain = AttributeDomain(domain)
        except ValueError as error:
            raise BuilderError(f"attach: {error}") from error
        lowered = self._mapping_values(values, f"attach {declared_domain.value}")
        for value in lowered:
            self._attach_value(declared_domain, target, value)

    def build(self) -> Graph:
        """Return a fresh immutable graph without consuming this builder."""
        return Graph(
            tuple(self._namespaces),
            tuple(self._tiers),
            tuple(self._relations),
            tuple(self._instances),
            tuple(self._attribute_declarations),
            tuple(self._positions),
            tuple(self._attributes),
            tuple(self._polyadic_instances),
        )

    def _name(self, name: Name) -> QualifiedName:
        if isinstance(name, QualifiedName):
            return name
        if isinstance(name, str):
            return self.qname(name)
        raise BuilderError(
            f"name: expected str or QualifiedName, got {type(name).__name__}"
        )

    @staticmethod
    def _local(name: object) -> str:
        return name.local_name if isinstance(name, QualifiedName) else str(name)

    def _tier(self, value: TierHandle | Name, operation: str) -> TierHandle:
        if isinstance(value, TierHandle):
            if value._document is not self:
                raise BuilderError(
                    f"{operation}: foreign tier handle {value.name.local_name}"
                )
            return value
        name = self._name(value)
        handle = self._tier_handles.get(name)
        if handle is None:
            raise BuilderError(f"{operation}: tier {name.local_name} is not declared")
        return handle

    def _tier_ref(self, handle: TierHandle, index: int) -> ItemRef:
        if type(index) is not int:
            raise BuilderError(
                f"tier ref {handle.name.local_name}: invalid index {index!r}"
            )
        tier = next(
            tier for tier in self._tiers if tier.declaration.name == handle.name
        )
        self._checked_item_index(tier, index, f"tier ref {handle.name.local_name}")
        return ItemRef(handle.name, index)

    def _memberships(self, tier: QualifiedName) -> tuple[QualifiedName, ...]:
        return tuple(
            declaration.item_type
            for declaration in self._relations
            if isinstance(declaration, SimpleRelationDeclaration)
            and declaration.tier == tier
        )

    def _endpoint_type(
        self,
        link: str,
        side: str,
        tier: TierHandle,
        explicit: Name | None,
        kind: RelationEndpointKind,
    ) -> QualifiedName:
        supplied = None if explicit is None else self._name(explicit)
        if kind is RelationEndpointKind.BOUNDARY:
            if supplied is None:
                raise BuilderError(
                    f"link {link} {side}: boundary endpoint needs explicit {side}_type"
                )
            return supplied
        memberships = self._memberships(tier.name)
        inferred = memberships[0] if len(memberships) == 1 else None
        if supplied is None:
            if inferred is None:
                state = "ambiguous" if len(memberships) > 1 else "untyped"
                raise BuilderError(
                    f"link {link} {side}: tier {tier.name.local_name} is {state}; "
                    f"explicit {side}_type is required"
                )
            return inferred
        if inferred is not None and supplied != inferred:
            raise BuilderError(
                f"link {link} {side}: explicit type {supplied} contradicts "
                f"tier {tier.name.local_name} type {inferred}"
            )
        return supplied

    def _endpoint(
        self,
        link: str,
        side: str,
        tier: TierHandle,
        value: object,
        kind: RelationEndpointKind,
    ) -> ItemRef | DurablePositionRef:
        operation = f"link {link} {side}"
        if kind is RelationEndpointKind.BOUNDARY:
            if isinstance(value, int):
                raise BuilderError(
                    f"{operation}: boundary endpoint refuses integer index"
                )
            if isinstance(value, DurablePositionRef):
                return value
            raise BuilderError(
                f"{operation}: boundary endpoint needs DurablePositionRef"
            )
        if type(value) is int:
            return self._tier_ref(tier, value)
        if isinstance(value, ItemRef):
            if value.tier != tier.name:
                raise BuilderError(
                    f"{operation}: item reference belongs to tier {value.tier.local_name}"
                )
            self._tier_ref(tier, value.index)
            return value
        if isinstance(value, DurableItemRef):
            matches = [
                index
                for candidate in self._tiers
                if candidate.declaration.name == tier.name
                for index, member in enumerate(candidate.items)
                if member.durable_id == value.durable_id
            ]
            if len(matches) != 1:
                raise BuilderError(
                    f"{operation}: durable item {value.durable_id!r} is not unique "
                    f"in tier {tier.name.local_name}"
                )
            return ItemRef(tier.name, matches[0])
        raise BuilderError(
            f"{operation}: item endpoint needs int, ItemRef, or DurableItemRef"
        )

    def _mapping_values(
        self, values: AttributeInput, operation: str
    ) -> tuple[AttributeValue, ...]:
        if values is None:
            return ()
        if not isinstance(values, Mapping):
            raise BuilderError(f"{operation}: expected a mapping")
        return self._values(tuple(values.items()), operation)

    def _values(
        self, values: tuple[tuple[Name, object], ...], operation: str
    ) -> tuple[AttributeValue, ...]:
        return tuple(self._value(name, value, operation) for name, value in values)

    def _value(self, name: Name, value: object, operation: str) -> AttributeValue:
        qualified = self._name(name)
        declarations = [
            declaration
            for declaration in self._attribute_declarations
            if declaration.name == qualified
        ]
        if len(declarations) != 1:
            raise BuilderError(
                f"{operation}: attribute {qualified.local_name} needs exactly one declaration"
            )
        value_type = declarations[0].value_type
        lexical: str
        if value_type is XsdType.STRING and isinstance(value, str):
            lexical = value
        elif value_type is XsdType.BOOLEAN and isinstance(value, bool | str):
            lexical = str(value).lower() if isinstance(value, bool) else value
        elif value_type is XsdType.INTEGER and (
            type(value) is int or isinstance(value, str)
        ):
            lexical = str(value)
        elif value_type is XsdType.DECIMAL and (
            isinstance(value, Decimal | str) or type(value) is int
        ):
            lexical = str(value)
        elif value_type is XsdType.DOUBLE and (
            isinstance(value, float | str) or type(value) is int
        ):
            lexical = str(value)
        else:
            raise BuilderError(
                f"{operation}: attribute {qualified.local_name} rejects "
                f"{type(value).__name__} for {value_type.value}"
            )
        return AttributeValue(qualified, value_type, lexical)

    def _attach_value(
        self, domain: AttributeDomain, target: AttributeTarget, value: AttributeValue
    ) -> None:
        operation = f"attach {domain.value}"
        if domain is AttributeDomain.DOCUMENT:
            if target is not None:
                raise BuilderError(f"{operation}: target must be None")
            self._attributes.append(value)
        elif domain is AttributeDomain.TIER:
            name = self._qualified_target(target, operation)
            index = self._tier_index(name, operation)
            self._tiers[index] = replace(
                self._tiers[index], attributes=(*self._tiers[index].attributes, value)
            )
        elif domain is AttributeDomain.ITEM:
            reference = self._item_target(target, operation)
            tier_index = self._tier_index(reference.tier, operation)
            tier = self._tiers[tier_index]
            self._checked_item_index(tier, reference.index, operation)
            members = list(tier.items)
            members[reference.index] = replace(
                members[reference.index],
                attributes=(*members[reference.index].attributes, value),
            )
            self._tiers[tier_index] = replace(tier, items=tuple(members))
        elif domain is AttributeDomain.RELATION_DECLARATION:
            name = self._qualified_target(target, operation)
            indexes = [
                i
                for i, declaration in enumerate(self._relations)
                if declaration.name == name
            ]
            if len(indexes) != 1:
                raise BuilderError(
                    f"{operation}: relation {name.local_name} is not uniquely declared"
                )
            index = indexes[0]
            declaration = self._relations[index]
            self._relations[index] = replace(
                declaration, attributes=(*declaration.attributes, value)
            )
        elif domain is AttributeDomain.RELATION_INSTANCE:
            index = self._index_target(target, operation, len(self._instances))
            relation = self._instances[index]
            self._instances[index] = replace(
                relation, attributes=(*relation.attributes, value)
            )
        else:
            if not isinstance(target, PositionRef | DurablePositionRef):
                raise BuilderError(f"{operation}: target must be a position reference")
            for index, position in enumerate(self._positions):
                if position.reference == target:
                    self._positions[index] = replace(
                        position, attributes=(*position.attributes, value)
                    )
                    break
            else:
                self._positions.append(Position(target, (value,)))

    @staticmethod
    def _qualified_target(target: AttributeTarget, operation: str) -> QualifiedName:
        if not isinstance(target, QualifiedName):
            raise BuilderError(f"{operation}: target must be QualifiedName")
        return target

    def _item_target(self, target: AttributeTarget, operation: str) -> ItemRef:
        if isinstance(target, ItemRef):
            return target
        if isinstance(target, DurableItemRef):
            matches = [
                ItemRef(tier.declaration.name, index)
                for tier in self._tiers
                for index, member in enumerate(tier.items)
                if member.durable_id == target.durable_id
            ]
            if len(matches) == 1:
                return matches[0]
            raise BuilderError(
                f"{operation}: durable item {target.durable_id!r} is not unique"
            )
        raise BuilderError(f"{operation}: target must be ItemRef or DurableItemRef")

    @staticmethod
    def _index_target(target: AttributeTarget, operation: str, size: int) -> int:
        if type(target) is not int or target < 0 or target >= size:
            raise BuilderError(f"{operation}: index {target!r} out of range")
        return target

    def _tier_index(self, name: QualifiedName, operation: str) -> int:
        for index, tier in enumerate(self._tiers):
            if tier.declaration.name == name:
                return index
        raise BuilderError(f"{operation}: tier {name.local_name} is not declared")

    @staticmethod
    def _checked_item_index(tier: Tier, index: int, operation: str) -> None:
        if index < 0 or index >= len(tier.items):
            raise BuilderError(f"{operation}: index {index} out of range")


__all__ = ["Document", "document", "item"]
