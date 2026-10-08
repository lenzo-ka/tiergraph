"""Describe typed external resources with ordinary graph vocabulary."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from tiergraph.core import (
    Attribute,
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    DurableItemRef,
    Graph,
    GraphEditor,
    ItemRef,
    NamespaceDeclaration,
    QualifiedName,
    RelationInstanceRef,
    XsdType,
)

BLOB_NAMESPACE = "urn:tiergraph:blob"
"""Namespace for the fixed external-resource vocabulary."""

_SHA256 = QualifiedName(BLOB_NAMESPACE, "sha256")
_SIZE = QualifiedName(BLOB_NAMESPACE, "size")
_MEDIA_TYPE = QualifiedName(BLOB_NAMESPACE, "media-type")
_SCHEMA = QualifiedName(BLOB_NAMESPACE, "schema")
_RATE = QualifiedName(BLOB_NAMESPACE, "rate")
_UNIT = QualifiedName(BLOB_NAMESPACE, "unit")
_ORIGIN = QualifiedName(BLOB_NAMESPACE, "origin")
_EXTENT = QualifiedName(BLOB_NAMESPACE, "extent")
_OFFSET = QualifiedName(BLOB_NAMESPACE, "offset")
_LENGTH = QualifiedName(BLOB_NAMESPACE, "length")

_VOCABULARY = (
    AttributeDeclaration(_SHA256, AttributeDomain.ITEM, XsdType.STRING),
    AttributeDeclaration(_SIZE, AttributeDomain.ITEM, XsdType.INTEGER),
    AttributeDeclaration(_MEDIA_TYPE, AttributeDomain.ITEM, XsdType.STRING),
    AttributeDeclaration(_SCHEMA, AttributeDomain.ITEM, XsdType.STRING),
    AttributeDeclaration(_RATE, AttributeDomain.ITEM, XsdType.DECIMAL),
    AttributeDeclaration(_UNIT, AttributeDomain.ITEM, XsdType.STRING),
    AttributeDeclaration(_ORIGIN, AttributeDomain.ITEM, XsdType.DECIMAL),
    AttributeDeclaration(_EXTENT, AttributeDomain.ITEM, XsdType.INTEGER),
    AttributeDeclaration(_OFFSET, AttributeDomain.RELATION_INSTANCE, XsdType.INTEGER),
    AttributeDeclaration(_LENGTH, AttributeDomain.RELATION_INSTANCE, XsdType.INTEGER),
)
_VOCABULARY_BY_NAME = {declaration.name: declaration for declaration in _VOCABULARY}
_DIGEST = re.compile(r"[0-9a-f]{64}")
_MEDIA = re.compile(
    r"[a-z0-9][a-z0-9!#$&^_.+\-]{0,126}/"
    r"[a-z0-9][a-z0-9!#$&^_.+\-]{0,126}"
)
_URI_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*")
_SPACE_CODE_POINT = 0x20


@dataclass(frozen=True, slots=True, order=True)
class BlobRef:
    """Identify payload bytes by their lowercase SHA-256 digest and exact size."""

    sha256: str
    size: int

    def __post_init__(self) -> None:
        """Require a canonical digest and a nonnegative integral byte count."""
        if _DIGEST.fullmatch(self.sha256) is None:
            raise ValueError(
                "blob SHA-256 must be exactly 64 lowercase hexadecimal characters"
            )
        if isinstance(self.size, bool) or not isinstance(self.size, int):
            raise ValueError(f"blob size {self.size!r} is not integral")
        if self.size < 0:
            raise ValueError(f"blob size {self.size} is negative")


@dataclass(frozen=True, slots=True)
class BlobSpan:
    """Describe a linear extent in the attached resource's declared unit.

    An absent ``offset`` makes the span duration-only. The resource's
    ``blob:unit`` supplies the meaning of both integers, so the value does not
    assume time: a unit can name characters, samples, frames, or another
    resource-defined linear coordinate. Structured paths remain ordinary
    relation-instance attributes in the resource schema rather than being
    forced into this linear value.
    """

    offset: int | None
    length: int

    def __post_init__(self) -> None:
        """Require nonnegative integral coordinates without treating booleans as integers."""
        if self.offset is not None and (
            isinstance(self.offset, bool) or not isinstance(self.offset, int)
        ):
            raise ValueError(f"blob span offset {self.offset!r} is not integral")
        if isinstance(self.length, bool) or not isinstance(self.length, int):
            raise ValueError(f"blob span length {self.length!r} is not integral")
        if self.offset is not None and self.offset < 0:
            raise ValueError(f"blob span offset {self.offset} is negative")
        if self.length < 0:
            raise ValueError(f"blob span length {self.length} is negative")


def _values(attributes: tuple[Attribute, ...]) -> dict[QualifiedName, AttributeValue]:
    """Return scalar attributes by name; graph validation has checked their kinds."""
    return {
        attribute.name: attribute
        for attribute in attributes
        if isinstance(attribute, AttributeValue)
    }


def _lexical(
    values: dict[QualifiedName, AttributeValue],
    name: QualifiedName,
    subject: str,
) -> str:
    """Return one required vocabulary value or name the item that lacks it."""
    value = values.get(name)
    if value is None:
        raise ValueError(f"blob item {subject} lacks required {str(name)!r}")
    return value.lexical


def _absolute_uri(value: str) -> bool:
    """Return whether a string has the lexical shape of an absolute URI."""
    from urllib.parse import urlsplit  # noqa: PLC0415 -- defer optional profile cost

    if any(
        character.isspace() or ord(character) < _SPACE_CODE_POINT for character in value
    ):
        return False
    try:
        scheme = urlsplit(value).scheme
    except ValueError:
        return False
    return _URI_SCHEME.fullmatch(scheme) is not None


@dataclass(frozen=True, slots=True)
class BlobProfile:
    """Read and validate external-resource descriptors and attachments.

    A blob is any ordinary item carrying ``blob:sha256``. Every blob also has
    a durable item identifier, byte size, lowercase media type, and absolute
    schema URI. The payload bytes stay outside the graph. Media-specific
    metadata remains ordinary typed attributes in the media vocabulary, so
    audio, video, nested graph, transcript, key-value, and other resources all
    use the same profile without core interpreting their types.

    Attachments are ordinary item-to-item bipartite relation instances whose
    right endpoint is a blob item. They retain the graph's declared relation
    order and must have durable relation identifiers. This admits any number
    of roles and attachments, including several metadata resources on one
    binary resource and chains from a structural item through multiple blobs.

    Author-declared metadata lives on the base item. Inspected type-specific
    metadata can be recorded as ordinary layer facts in the media vocabulary,
    whose layer source identifies the inspector. This profile never imports or
    runs an inspector and never opens payload bytes.
    """

    graph: Graph
    _blob_entries: tuple[tuple[ItemRef, BlobRef], ...] = field(
        init=False, repr=False, compare=False
    )
    _attachment_entries: dict[
        ItemRef,
        tuple[tuple[RelationInstanceRef, ItemRef, BlobSpan | None], ...],
    ] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:  # noqa: PLR0915 -- one profile contract
        """Validate the complete fixed vocabulary before exposing any reading."""
        namespace = next(
            (
                declaration
                for declaration in self.graph.namespaces
                if declaration.namespace == BLOB_NAMESPACE
            ),
            None,
        )
        if namespace != NamespaceDeclaration("blob", BLOB_NAMESPACE):
            raise ValueError(
                f"blob vocabulary requires prefix 'blob' bound to {BLOB_NAMESPACE!r}"
            )
        declarations = {
            declaration.name: declaration
            for declaration in self.graph.attribute_declarations
            if declaration.name.namespace == BLOB_NAMESPACE
        }
        for name, expected in _VOCABULARY_BY_NAME.items():
            actual = declarations.get(name)
            if actual != expected:
                raise ValueError(
                    f"blob vocabulary declaration {str(name)!r} must be a "
                    f"{expected.domain.value} {expected.value_type.value} attribute"
                )
        unknown = tuple(
            name for name in declarations if name not in _VOCABULARY_BY_NAME
        )
        if unknown:
            raise ValueError(
                f"blob vocabulary contains unknown attribute {str(unknown[0])!r}"
            )
        for layer in self.graph.layers:
            for index, fact in enumerate(layer.facts):
                if fact.value.name.namespace == BLOB_NAMESPACE:
                    raise ValueError(
                        f"layer {layer.name} fact {index} carries reserved blob "
                        f"attribute {str(fact.value.name)!r}; blob descriptors and "
                        "spans must be base graph values"
                    )

        entries: list[tuple[ItemRef, BlobRef]] = []
        refs_by_item: dict[ItemRef, BlobRef] = {}
        units_by_item: dict[ItemRef, str | None] = {}
        extents_by_item: dict[ItemRef, int | None] = {}
        sizes_by_digest: dict[str, int] = {}
        for tier in self.graph.tiers:
            for index, item in enumerate(tier.items):
                reference = ItemRef(tier.declaration.name, index)
                values = _values(item.attributes)
                reserved = {
                    name: value
                    for name, value in values.items()
                    if name.namespace == BLOB_NAMESPACE
                }
                if _SHA256 not in reserved:
                    if reserved:
                        raise ValueError(
                            f"non-blob item {reference} carries reserved attribute "
                            f"{str(next(iter(reserved)))!r}"
                        )
                    continue
                if item.durable_id is None:
                    raise ValueError(f"blob item {reference} lacks a durable id")
                blob_ref = BlobRef(
                    _lexical(reserved, _SHA256, str(reference)),
                    int(_lexical(reserved, _SIZE, str(reference))),
                )
                previous_size = sizes_by_digest.setdefault(
                    blob_ref.sha256, blob_ref.size
                )
                if previous_size != blob_ref.size:
                    raise ValueError(
                        f"blob digest {blob_ref.sha256!r} is declared with sizes "
                        f"{previous_size} and {blob_ref.size}"
                    )
                media_type = _lexical(reserved, _MEDIA_TYPE, str(reference))
                if _MEDIA.fullmatch(media_type) is None:
                    raise ValueError(
                        f"blob item {reference} has invalid lowercase media type "
                        f"{media_type!r}"
                    )
                schema = _lexical(reserved, _SCHEMA, str(reference))
                if not _absolute_uri(schema):
                    raise ValueError(
                        f"blob item {reference} schema {schema!r} is not an absolute URI"
                    )
                rate_value = reserved.get(_RATE)
                if rate_value is not None and Decimal(rate_value.lexical) <= 0:
                    raise ValueError(f"blob item {reference} rate must be positive")
                unit_value = reserved.get(_UNIT)
                unit = None if unit_value is None else unit_value.lexical
                if rate_value is not None and not unit:
                    raise ValueError(
                        f"blob item {reference} with a rate requires a nonempty unit"
                    )
                extent_value = reserved.get(_EXTENT)
                extent = None if extent_value is None else int(extent_value.lexical)
                if extent is not None and extent < 0:
                    raise ValueError(f"blob item {reference} extent is negative")
                entries.append((reference, blob_ref))
                refs_by_item[reference] = blob_ref
                units_by_item[reference] = unit
                extents_by_item[reference] = extent

        attachments: dict[
            ItemRef,
            list[tuple[RelationInstanceRef, ItemRef, BlobSpan | None]],
        ] = {reference: [] for reference in refs_by_item}
        for index, polyadic_relation in enumerate(self.graph.polyadic_relations):
            span_names = tuple(
                attribute.name
                for attribute in polyadic_relation.attributes
                if attribute.name in {_OFFSET, _LENGTH}
            )
            if span_names:
                raise ValueError(
                    f"polyadic relation instance {index} carries reserved blob span "
                    f"attribute {str(span_names[0])!r}; blob attachments must be "
                    "bipartite item-to-item relations"
                )
        for index, relation in enumerate(self.graph.relations):
            values = _values(relation.attributes)
            span_values = {
                name: value
                for name, value in values.items()
                if name in {_OFFSET, _LENGTH}
            }
            right = (
                self.graph.resolve_item(relation.right)
                if isinstance(relation.right, ItemRef | DurableItemRef)
                else None
            )
            if right not in refs_by_item:
                if span_values:
                    raise ValueError(
                        f"relation instance {index} carries a blob span but its "
                        "right endpoint is not a blob item"
                    )
                continue
            if not isinstance(relation.left, ItemRef | DurableItemRef):
                raise ValueError(
                    f"blob attachment {index} has a non-item left endpoint"
                )
            if relation.durable_id is None:
                raise ValueError(f"blob attachment {index} lacks a durable id")
            subject = self.graph.resolve_item(relation.left)
            offset_value = span_values.get(_OFFSET)
            length_value = span_values.get(_LENGTH)
            if offset_value is not None and length_value is None:
                raise ValueError(
                    f"blob attachment {index} carries an offset without a length"
                )
            span = (
                None
                if length_value is None
                else BlobSpan(
                    None if offset_value is None else int(offset_value.lexical),
                    int(length_value.lexical),
                )
            )
            if span is not None:
                if not units_by_item[right]:
                    raise ValueError(
                        f"blob attachment {index} carries a linear span but blob "
                        f"item {right} has no nonempty unit"
                    )
                extent = extents_by_item[right]
                start = 0 if span.offset is None else span.offset
                if extent is not None and start + span.length > extent:
                    raise ValueError(
                        f"blob attachment {index} span exceeds blob item {right} "
                        f"extent {extent}"
                    )
            attachments[right].append((RelationInstanceRef(index), subject, span))

        object.__setattr__(self, "_blob_entries", tuple(entries))
        object.__setattr__(
            self,
            "_attachment_entries",
            {reference: tuple(found) for reference, found in attachments.items()},
        )

    def blobs(self) -> tuple[tuple[ItemRef, BlobRef], ...]:
        """Return blob items in tier and item order without sorting by digest."""
        return self._blob_entries

    def attachments(
        self, blob: ItemRef | DurableItemRef
    ) -> tuple[tuple[RelationInstanceRef, ItemRef, BlobSpan | None], ...]:
        """Return one blob item's attachments in declared relation order."""
        reference = self.graph.resolve_item(blob)
        if reference not in self._attachment_entries:
            raise ValueError(f"item {reference} is not a blob item")
        return self._attachment_entries[reference]

    def required(self) -> tuple[BlobRef, ...]:
        """Return distinct payload requirements in first blob-item order."""
        seen: set[str] = set()
        required: list[BlobRef] = []
        for _, reference in self._blob_entries:
            if reference.sha256 not in seen:
                seen.add(reference.sha256)
                required.append(reference)
        return tuple(required)


def declare_blob_vocabulary(editor: GraphEditor) -> GraphEditor:
    """Declare the fixed blob prefix and attributes on a mutable graph editor.

    The helper appends declarations and returns the same editor for chaining.
    Existing declarations are not silently adopted: the editor's ordinary
    duplicate-declaration refusals keep one explicit declaration event.
    """
    editor.declare(NamespaceDeclaration("blob", BLOB_NAMESPACE))
    for declaration in _VOCABULARY:
        editor.declare(declaration)
    return editor


__all__ = [
    "BLOB_NAMESPACE",
    "BlobProfile",
    "BlobRef",
    "BlobSpan",
    "declare_blob_vocabulary",
]
