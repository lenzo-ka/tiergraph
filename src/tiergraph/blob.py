"""Describe typed external resources with ordinary graph vocabulary."""

from __future__ import annotations

import hashlib
import io
import re
from collections.abc import Buffer, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, BinaryIO, Literal, Protocol, cast

if TYPE_CHECKING:
    import zipfile

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
from tiergraph.wire import MAX_DOCUMENT_BYTES, dump_bytes, loads

BLOB_NAMESPACE = "urn:tiergraph:blob"
"""Namespace for the fixed external-resource vocabulary."""

BUNDLE_VERSION = "1"
"""Version of the strict ZIP bundle container."""

_BUNDLE_INDEX = "bundle.json"
_BUNDLE_GRAPH = "graph.json"
_BLOB_PATH = "blobs/sha256/"
_CANONICAL_DATE = (1980, 1, 1, 0, 0, 0)
_CANONICAL_DOS_TIME = 0
_CANONICAL_DOS_DATE = 33
_CANONICAL_EXTERNAL_ATTR = 0o100644 << 16
_EOCD_SIGNATURE = b"PK\x05\x06"
_ZIP64_EOCD_SIGNATURE = b"PK\x06\x06"
_ZIP64_LOCATOR_SIGNATURE = b"PK\x06\x07"
_LOCAL_SIGNATURE = b"PK\x03\x04"
_EOCD_SIZE = 22
_ZIP64_LOCATOR_SIZE = 20
_ZIP64_EOCD_MINIMUM_BODY_SIZE = 44
_REQUIRED_METADATA_ENTRIES = 2
_UNIX_SYSTEM = 3
_ZIP_VERSION = 20
_ZIP64_VERSION = 45
_EXTRA_HEADER_SIZE = 4
_LOCAL_HEADER_SIZE = 30
_CENTRAL_HEADER_SIZE = 46
_ZIP64_VALUE_SIZE = 8
_UINT16_MAX = (1 << 16) - 1
_UINT32_MAX = (1 << 32) - 1
_ZIP64_EXTRA = 0x0001

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


def hash_blob(source: BinaryIO, *, chunk_size: int = 1 << 20) -> BlobRef:
    """Hash bytes from the source's current position through end of stream."""
    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int):
        raise ValueError(f"blob hash chunk size {chunk_size!r} is not integral")
    if chunk_size <= 0:
        raise ValueError(f"blob hash chunk size {chunk_size} is not positive")
    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = source.read(chunk_size)
        if not isinstance(chunk, bytes):
            raise TypeError("binary blob source read() must return bytes")
        if not chunk:
            return BlobRef(digest.hexdigest(), size)
        digest.update(chunk)
        size += len(chunk)


class BlobResolver(Protocol):
    """Open payload bytes from one explicit storage source.

    Returned readers must follow ``BinaryIO`` semantics, including reading
    through end of stream when given a negative size.
    """

    def open(self, ref: BlobRef, href: str | None) -> BinaryIO | None:
        """Return a fresh binary reader, or ``None`` when this source has no match."""
        ...


class MappingResolver:
    """Resolve caller-supplied immutable bytes by digest alone.

    Declared size and href do not participate in lookup. Wrap returned readers
    in ``VerifiedReader`` when size and content must be checked.
    """

    def __init__(self, payloads: Mapping[str, bytes]) -> None:
        """Copy a digest-to-bytes lookup without copying its payload bytes."""
        copied: dict[str, bytes] = {}
        for digest, payload in payloads.items():
            if _DIGEST.fullmatch(digest) is None:
                raise ValueError(
                    "mapping resolver digest must be exactly 64 lowercase "
                    "hexadecimal characters"
                )
            if not isinstance(payload, bytes):
                raise TypeError("mapping resolver payloads must be bytes")
            copied[digest] = payload
        self._payloads = copied

    def open(self, ref: BlobRef, href: str | None) -> BinaryIO | None:
        """Return a new in-memory reader for the digest, independent of its href."""
        del href
        payload = self._payloads.get(ref.sha256)
        return None if payload is None else io.BytesIO(payload)


class ChainResolver:
    """Try an explicitly ordered collection of resolvers until one answers."""

    def __init__(self, resolvers: Iterable[BlobResolver]) -> None:
        """Materialize resolver order once so every lookup follows the same order."""
        self._resolvers = tuple(resolvers)

    def open(self, ref: BlobRef, href: str | None) -> BinaryIO | None:
        """Return the first available reader without consulting later resolvers."""
        for resolver in self._resolvers:
            source = resolver.open(ref, href)
            if source is not None:
                return source
        return None


class BlobSink(Protocol):
    """Store one payload in caller-selected external storage.

    A sink may close ``source`` after consuming and verifying it completely.
    External writes remain the sink's responsibility if a later sink call or
    bundle publication fails, so implementations should be transactional or
    idempotent when partial publication is not acceptable.
    """

    def put(self, ref: BlobRef, source: BinaryIO) -> str | None:
        """Store the bytes and return an optional href for a bundle index."""
        ...


class VerifiedReader(io.BufferedIOBase):
    """Hash a binary stream as it is read and verify its declared identity at EOF.

    ``verified`` becomes true only after an unseeked negative-size read or an
    empty bounded read establishes end of stream with both the expected byte
    count and SHA-256 digest. A bounded read that returns the final payload byte
    therefore needs one subsequent read to establish EOF. The wrapped source
    must follow ``BinaryIO`` semantics, including reading through EOF for a
    negative size. Seeking permanently makes the wrapper unverified, which
    permits random-access span reads without presenting a partial read as an
    integrity check. Closing the wrapper closes its source.
    """

    def __init__(self, source: BinaryIO, ref: BlobRef) -> None:
        """Wrap a reader positioned at the start of the declared payload."""
        super().__init__()
        self._source = source
        self._ref = ref
        self._digest = hashlib.sha256()
        self._size = 0
        self._verification_possible = True
        self._finished = False
        self._verified = False

    @property
    def verified(self) -> bool:
        """Return whether one complete, unseeked read matched size and digest."""
        return self._verified

    def readable(self) -> bool:
        """Return whether the wrapped stream supports reads."""
        return self._source.readable()

    def writable(self) -> bool:
        """Return false because integrity readers never expose writes."""
        return False

    def seekable(self) -> bool:
        """Return whether the wrapped stream supports random access."""
        return self._source.seekable()

    def tell(self) -> int:
        """Return the wrapped stream's current byte position."""
        return self._source.tell()

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        """Move the wrapped stream and permanently invalidate full-read verification."""
        position = self._source.seek(offset, whence)
        self._verification_possible = False
        self._verified = False
        return position

    def read(self, size: int | None = -1) -> bytes:
        """Read bytes and verify after a negative-size or empty bounded read."""
        requested = -1 if size is None else size
        data = self._binary(self._source.read(requested))
        self._accept(data, eof=requested < 0 or (requested != 0 and not data))
        return data

    def read1(self, size: int = -1) -> bytes:
        """Read bytes through the same verification path as ``read``."""
        return self.read(size)

    def readinto(self, buffer: Buffer, /) -> int:
        """Read verified bytes into a writable contiguous buffer."""
        view = memoryview(buffer)
        if view.readonly:
            raise TypeError("readinto() argument must be a writable buffer")
        byte_view = view.cast("B")
        data = self.read(len(byte_view))
        byte_view[: len(data)] = data
        return len(data)

    def readinto1(self, buffer: Buffer, /) -> int:
        """Read one verified chunk into a writable contiguous buffer."""
        return self.readinto(buffer)

    def readline(self, size: int | None = -1) -> bytes:
        """Read one binary line and verify when its result establishes EOF."""
        requested = -1 if size is None else size
        data = self._binary(self._source.readline(requested))
        eof = (requested != 0 and not data) or (
            requested < 0 and not data.endswith(b"\n")
        )
        self._accept(data, eof=eof)
        return data

    def close(self) -> None:
        """Close both the wrapped source and this reader without forcing a read."""
        if not self.closed:
            try:
                self._source.close()
            finally:
                super().close()

    @staticmethod
    def _binary(data: object) -> bytes:
        """Require the binary-reader result promised by the public protocol."""
        if not isinstance(data, bytes):
            raise TypeError("binary blob source read must return bytes")
        return data

    def _accept(self, data: bytes, *, eof: bool) -> None:
        """Accumulate sequential bytes and finish verification at known EOF."""
        if not self._verification_possible or self._finished:
            return
        self._digest.update(data)
        self._size += len(data)
        if eof:
            self._finish()

    def _finish(self) -> None:
        """Compare the accumulated stream identity exactly once."""
        self._finished = True
        if self._size != self._ref.size:
            raise ValueError(
                f"blob size mismatch: expected {self._ref.size}, read {self._size}"
            )
        actual = self._digest.hexdigest()
        if actual != self._ref.sha256:
            raise ValueError(
                f"blob SHA-256 mismatch: expected {self._ref.sha256}, read {actual}"
            )
        self._verified = True


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


@dataclass(frozen=True, slots=True)
class BundleLimits:
    """Bound bundle metadata and declared uncompressed entry sizes."""

    max_entries: int = 100_000
    max_entry_bytes: int = 1 << 40
    max_total_bytes: int = 1 << 44
    max_central_directory_bytes: int = 64 << 20
    max_index_bytes: int = 16 << 20
    max_graph_bytes: int = MAX_DOCUMENT_BYTES

    def __post_init__(self) -> None:
        """Require every configured resource limit to be a positive integer."""
        for name in (
            "max_entries",
            "max_entry_bytes",
            "max_total_bytes",
            "max_central_directory_bytes",
            "max_index_bytes",
            "max_graph_bytes",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"bundle limit {name}={value!r} is not integral")
            if value <= 0:
                raise ValueError(f"bundle limit {name}={value} is not positive")


@dataclass(frozen=True, slots=True)
class BundleAsset:
    """Describe one durably identified asset row in declared graph order."""

    id: str
    position: int
    ref: BlobRef
    mode: Literal["embedded", "linked"]
    href: str | None


@dataclass(frozen=True, slots=True)
class _CentralDirectory:
    count: int
    size: int
    offset: int
    eocd_offset: int
    zip64: bool


@dataclass(frozen=True, slots=True)
class _WrittenEntry:
    name: str
    size: int
    crc: int
    offset: int
    version: int


class Bundle:
    """Expose one validated bundle graph and its lazily opened payloads.

    Opening validates the complete index, graph bytes, inventory, ZIP metadata,
    and local entry headers. Payload bodies are not read until ``open_blob`` is
    called. The bundle borrows the caller's binary source; closing the bundle
    releases ZIP state but does not close that source.
    """

    graph: Graph
    _archive: zipfile.ZipFile
    _assets: tuple[BundleAsset, ...]
    _closed: bool

    def __init__(self) -> None:
        """Refuse direct construction because bundle state must be validated."""
        raise TypeError("Bundle objects are created by open_bundle()")

    @classmethod
    def _from_validated(
        cls,
        graph: Graph,
        archive: zipfile.ZipFile,
        assets: tuple[BundleAsset, ...],
    ) -> Bundle:
        """Construct a bundle from state already validated by ``open_bundle``."""
        bundle = object.__new__(cls)
        bundle.graph = graph
        bundle._archive = archive
        bundle._assets = assets
        bundle._closed = False
        return bundle

    def assets(self) -> tuple[BundleAsset, ...]:
        """Return durably identified asset rows in declared blob-item order."""
        return self._assets

    def open_blob(
        self, ref: BlobRef, resolver: BlobResolver | None = None
    ) -> VerifiedReader:
        """Open one declared payload and verify a complete sequential read."""
        import zipfile  # noqa: PLC0415 -- loaded only for opted-in bundle reads

        if self._closed:
            raise ValueError("bundle is closed")
        asset = next((row for row in self._assets if row.ref == ref), None)
        if asset is None:
            raise ValueError(
                f"bundle does not declare blob {ref.sha256} with size {ref.size}"
            )
        if asset.mode == "embedded":
            try:
                source = self._archive.open(_BLOB_PATH + ref.sha256, "r")
            except (KeyError, RuntimeError, ValueError, zipfile.BadZipFile) as error:
                raise ValueError(
                    f"bundle cannot open embedded blob {ref.sha256}: {error}"
                ) from error
            return VerifiedReader(cast(BinaryIO, source), ref)
        if resolver is None:
            raise ValueError(f"linked blob {ref.sha256} requires a resolver")
        linked_source = resolver.open(ref, asset.href)
        if linked_source is None:
            raise ValueError(f"linked blob {ref.sha256} was not found by the resolver")
        return VerifiedReader(linked_source, ref)

    def close(self) -> None:
        """Release bundle ZIP state without closing the caller-owned source."""
        if not self._closed:
            self._archive.close()
            self._closed = True

    def __enter__(self) -> Bundle:
        """Return this open bundle for a context-managed read session."""
        if self._closed:
            raise ValueError("bundle is closed")
        return self

    def __exit__(self, *exception: object) -> None:
        """Close ZIP state when leaving a context-managed read session."""
        del exception
        self.close()


def open_bundle(
    source: BinaryIO,
    *,
    limits: BundleLimits = BundleLimits(),  # noqa: B008 -- frozen public default
) -> Bundle:
    """Validate and open a strict store-only bundle without reading payloads."""
    import zipfile  # noqa: PLC0415 -- loaded only for opted-in bundle reads

    directory = _read_central_directory(source, limits)
    try:
        archive = zipfile.ZipFile(source, "r")
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        raise ValueError(f"invalid bundle ZIP: {error}") from error
    try:
        infos = tuple(archive.infolist())
        _check_archive_profile(source, archive, infos, directory, limits)
        index_bytes = _read_metadata_entry(
            archive, infos[0], limits.max_index_bytes, "bundle index"
        )
        index = _parse_bundle_index(index_bytes)
        assets = _parse_assets(index["assets"])
        _check_index_entry_order(infos, assets)
        graph_entry = _graph_row(index["graph"])
        graph_info = infos[1]
        if graph_entry[0] != _BUNDLE_GRAPH:
            raise ValueError("bundle graph path must be 'graph.json'")
        if graph_entry[2] != graph_info.file_size:
            raise ValueError(
                "bundle graph size does not match the graph.json entry size"
            )
        graph_bytes = _read_metadata_entry(
            archive, graph_info, limits.max_graph_bytes, "bundle graph"
        )
        actual_digest = hashlib.sha256(graph_bytes).hexdigest()
        if actual_digest != graph_entry[1]:
            raise ValueError(
                f"bundle graph SHA-256 mismatch: expected {graph_entry[1]}, "
                f"read {actual_digest}"
            )
        graph = loads(graph_bytes)
        _check_inventory(graph, assets)
        return Bundle._from_validated(graph, archive, assets)
    except Exception:
        archive.close()
        raise


def write_bundle(
    graph: Graph,
    destination: BinaryIO,
    resolver: BlobResolver,
    *,
    embed: Callable[[BlobRef], bool] | bool = True,
    hrefs: Mapping[BlobRef, str] | None = None,
    limits: BundleLimits = BundleLimits(),  # noqa: B008 -- frozen public default
) -> None:
    """Write a deterministic bundle with a per-payload storage choice.

    ``embed`` is either one choice for every payload or a predicate evaluated
    once for each distinct payload in first blob-item order. Linked rows take
    their optional locator from ``hrefs``. The resolver is consulted only for
    embedded payloads and receives the matching locator when one was supplied.

    The destination must be an empty, seekable binary stream. Any refusal while
    resolving, reading, or verifying a payload rolls it back to empty, so a
    failed write never leaves a usable-looking bundle.
    """
    choices = _selection(embed)
    source_hrefs = {} if hrefs is None else hrefs
    assets: list[BundleAsset] = []
    for position, (durable_id, ref) in enumerate(_graph_assets(graph)):
        embedded = choices(ref)
        href = source_hrefs.get(ref)
        if href is not None and not isinstance(href, str):
            raise TypeError(f"bundle href for blob {ref.sha256} must be a string")
        assets.append(
            BundleAsset(
                durable_id,
                position,
                ref,
                "embedded" if embedded else "linked",
                None if embedded else href,
            )
        )

    def _open_payload(ref: BlobRef) -> BinaryIO | None:
        return resolver.open(ref, source_hrefs.get(ref))

    _write_bundle_archive(
        graph, destination, tuple(assets), _open_payload, limits=limits
    )


def relink(
    bundle: Bundle,
    destination: BinaryIO,
    sink: BlobSink,
    *,
    which: Callable[[BlobRef], bool] | bool = True,
) -> None:
    """Move selected embedded payloads to a sink and write linked rows.

    Selection is evaluated once per distinct payload in declared asset order.
    Existing linked rows and unselected embedded rows retain their modes and
    locators. A sink must consume the complete verified stream before its href
    is accepted. The destination and every selection are validated before the
    first sink call. External writes remain the sink's responsibility if a
    later sink call or bundle publication fails.
    """
    selected = _selection(which)
    selected_assets = tuple(
        asset
        for asset in _distinct_assets(bundle.assets())
        if asset.mode == "embedded" and selected(asset.ref)
    )
    _prepare_destination(destination)
    hrefs: dict[BlobRef, str | None] = {}
    for asset in selected_assets:
        with bundle.open_blob(asset.ref) as reader:
            href = sink.put(asset.ref, cast(BinaryIO, reader))
            if href is not None and not isinstance(href, str):
                raise TypeError(
                    f"blob sink href for {asset.ref.sha256} must be a string"
                )
            if not reader.verified and not reader.closed:
                reader.read(1)
            if not reader.verified:
                raise ValueError(
                    f"blob sink did not consume payload {asset.ref.sha256} completely"
                )
        hrefs[asset.ref] = href
    assets = tuple(
        BundleAsset(
            asset.id,
            asset.position,
            asset.ref,
            "linked" if asset.ref in hrefs else asset.mode,
            hrefs[asset.ref] if asset.ref in hrefs else asset.href,
        )
        for asset in bundle.assets()
    )
    _write_bundle_archive(
        bundle.graph,
        destination,
        assets,
        lambda ref: cast(BinaryIO, bundle.open_blob(ref)),
        limits=BundleLimits(),
    )


def embed_links(
    bundle: Bundle,
    destination: BinaryIO,
    resolver: BlobResolver,
    *,
    which: Callable[[BlobRef], bool] | bool = True,
) -> None:
    """Embed selected linked payloads and preserve every other asset row.

    The resolver is consulted only for selected linked payloads, with each
    row's href. Existing embedded payloads are streamed from the source bundle.
    Selection is evaluated once per distinct payload in declared asset order.
    """
    selected = _selection(which)
    changed: set[BlobRef] = set()
    original = {asset.ref: asset for asset in _distinct_assets(bundle.assets())}
    for asset in original.values():
        if asset.mode == "linked" and selected(asset.ref):
            changed.add(asset.ref)
    assets = tuple(
        BundleAsset(
            asset.id,
            asset.position,
            asset.ref,
            "embedded" if asset.ref in changed else asset.mode,
            None if asset.ref in changed else asset.href,
        )
        for asset in bundle.assets()
    )

    def _open_payload(ref: BlobRef) -> BinaryIO | None:
        asset = original[ref]
        if asset.mode == "embedded":
            return cast(BinaryIO, bundle.open_blob(ref))
        return resolver.open(ref, asset.href)

    _write_bundle_archive(
        bundle.graph,
        destination,
        assets,
        _open_payload,
        limits=BundleLimits(),
    )


def _graph_assets(graph: Graph) -> tuple[tuple[str, BlobRef], ...]:
    """Return durable ids and payload identities in declared blob-item order."""
    if not any(namespace.namespace == BLOB_NAMESPACE for namespace in graph.namespaces):
        return ()
    profile = BlobProfile(graph)
    tiers = {tier.declaration.name: tier for tier in graph.tiers}
    result: list[tuple[str, BlobRef]] = []
    for reference, blob_ref in profile.blobs():
        durable_id = tiers[reference.tier].items[reference.index].durable_id
        result.append((cast(str, durable_id), blob_ref))
    return tuple(result)


def _selection(
    choice: Callable[[BlobRef], bool] | bool,
) -> Callable[[BlobRef], bool]:
    """Return a memoized, strictly boolean per-payload choice."""
    if isinstance(choice, bool):

        def _fixed(ref: BlobRef) -> bool:
            del ref
            return choice

        return _fixed
    if not callable(choice):
        raise TypeError("bundle payload selection must be a boolean or callable")
    decisions: dict[BlobRef, bool] = {}

    def _selected(ref: BlobRef) -> bool:
        if ref not in decisions:
            result = choice(ref)
            if not isinstance(result, bool):
                raise TypeError("bundle payload selection must return a boolean")
            decisions[ref] = result
        return decisions[ref]

    return _selected


def _distinct_assets(assets: tuple[BundleAsset, ...]) -> tuple[BundleAsset, ...]:
    """Return the first row for each payload without changing declared order."""
    seen: set[BlobRef] = set()
    result: list[BundleAsset] = []
    for asset in assets:
        if asset.ref not in seen:
            seen.add(asset.ref)
            result.append(asset)
    return tuple(result)


def _write_bundle_archive(
    graph: Graph,
    destination: BinaryIO,
    assets: tuple[BundleAsset, ...],
    open_payload: Callable[[BlobRef], BinaryIO | None],
    *,
    limits: BundleLimits,
) -> None:
    """Write the strict archive and roll back the destination on every failure."""
    import json  # noqa: PLC0415 -- loaded only for opted-in bundle writes

    graph_bytes = dump_bytes(graph)
    index = {
        "bundle_version": BUNDLE_VERSION,
        "graph": {
            "path": _BUNDLE_GRAPH,
            "sha256": hashlib.sha256(graph_bytes).hexdigest(),
            "size": len(graph_bytes),
        },
        "assets": [_asset_data(asset) for asset in assets],
    }
    index_bytes = json.dumps(
        index,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    embedded = tuple(
        asset for asset in _distinct_assets(assets) if asset.mode == "embedded"
    )
    _check_write_limits(index_bytes, graph_bytes, embedded, limits)
    _prepare_destination(destination)
    try:
        entries = [
            _write_entry(
                destination, _BUNDLE_INDEX, len(index_bytes), io.BytesIO(index_bytes)
            ),
            _write_entry(
                destination, _BUNDLE_GRAPH, len(graph_bytes), io.BytesIO(graph_bytes)
            ),
        ]
        for asset in embedded:
            source = open_payload(asset.ref)
            if source is None:
                raise ValueError(
                    f"embedded blob {asset.ref.sha256} was not found by the resolver"
                )
            reader = VerifiedReader(source, asset.ref)
            try:
                entries.append(
                    _write_entry(
                        destination,
                        _BLOB_PATH + asset.ref.sha256,
                        asset.ref.size,
                        cast(BinaryIO, reader),
                    )
                )
            finally:
                reader.close()
        _write_directory(destination, tuple(entries), limits)
    except BaseException:
        _rollback_destination(destination)
        raise


def _asset_data(asset: BundleAsset) -> dict[str, object]:
    """Return one closed index row with fields in canonical presentation order."""
    row: dict[str, object] = {
        "id": asset.id,
        "position": asset.position,
        "sha256": asset.ref.sha256,
        "size": asset.ref.size,
        "mode": asset.mode,
    }
    if asset.mode == "linked" and asset.href is not None:
        row["href"] = asset.href
    return row


def _check_write_limits(
    index: bytes,
    graph: bytes,
    embedded: tuple[BundleAsset, ...],
    limits: BundleLimits,
) -> None:
    """Check all declared entry limits before the destination is touched."""
    specifications = (
        (_BUNDLE_INDEX, len(index)),
        (_BUNDLE_GRAPH, len(graph)),
        *((_BLOB_PATH + asset.ref.sha256, asset.ref.size) for asset in embedded),
    )
    sizes = [size for _, size in specifications]
    _check_limit(len(sizes), limits.max_entries, "entry count")
    _check_limit(len(index), limits.max_index_bytes, "bundle index size")
    _check_limit(len(graph), limits.max_graph_bytes, "bundle graph size")
    total = 0
    for size in sizes:
        _check_limit(size, limits.max_entry_bytes, "entry size")
        total += size
        _check_limit(total, limits.max_total_bytes, "total entry size")
    directory_size = _central_directory_size(_plan_entries(specifications))
    _check_limit(
        directory_size,
        limits.max_central_directory_bytes,
        "central directory size",
    )


def _plan_entries(
    specifications: tuple[tuple[str, int], ...],
) -> tuple[_WrittenEntry, ...]:
    """Plan exact stored-entry offsets without opening payload streams."""
    entries: list[_WrittenEntry] = []
    offset = 0
    for name, size in specifications:
        version = (
            _ZIP64_VERSION
            if size >= _UINT32_MAX or offset >= _UINT32_MAX
            else _ZIP_VERSION
        )
        entries.append(_WrittenEntry(name, size, 0, offset, version))
        local_extra_size = (
            _EXTRA_HEADER_SIZE + (2 * _ZIP64_VALUE_SIZE) if size >= _UINT32_MAX else 0
        )
        offset += _LOCAL_HEADER_SIZE + len(name.encode("ascii")) + local_extra_size
        offset += size
    return tuple(entries)


def _central_directory_size(entries: tuple[_WrittenEntry, ...]) -> int:
    """Return the exact byte size of canonical central-directory records."""
    total = 0
    for entry in entries:
        value_count = 2 if entry.size >= _UINT32_MAX else 0
        if entry.offset >= _UINT32_MAX:
            value_count += 1
        extra_size = (
            _EXTRA_HEADER_SIZE + (value_count * _ZIP64_VALUE_SIZE) if value_count else 0
        )
        total += _CENTRAL_HEADER_SIZE + len(entry.name.encode("ascii")) + extra_size
    return total


def _prepare_destination(destination: BinaryIO) -> None:
    """Require an empty seekable output that can be rolled back exactly."""
    try:
        if not destination.writable() or not destination.seekable():
            raise ValueError("bundle destination must be seekable and writable")
        position = destination.tell()
        end = destination.seek(0, io.SEEK_END)
        destination.seek(position)
    except (AttributeError, OSError, ValueError) as error:
        raise ValueError(
            "bundle destination must be a seekable binary stream"
        ) from error
    if position != 0 or end != 0:
        raise ValueError("bundle destination must be empty and positioned at byte 0")


def _rollback_destination(destination: BinaryIO) -> None:
    """Erase every byte written during a refused bundle operation."""
    try:
        destination.seek(0)
        destination.truncate(0)
        destination.seek(0)
    except (OSError, ValueError) as error:
        raise ValueError(
            "failed bundle destination could not be rolled back"
        ) from error


def _write_entry(
    destination: BinaryIO, name: str, expected_size: int, source: BinaryIO
) -> _WrittenEntry:
    """Stream one stored entry and patch its CRC into the local header."""
    import struct  # noqa: PLC0415 -- loaded only for opted-in bundle writes
    import zlib  # noqa: PLC0415 -- loaded only for opted-in bundle writes

    encoded_name = name.encode("ascii")
    offset = _stream_position(destination, "bundle entry offset")
    version = (
        _ZIP64_VERSION
        if expected_size >= _UINT32_MAX or offset >= _UINT32_MAX
        else _ZIP_VERSION
    )
    extra = (
        _zip64_extra((expected_size, expected_size))
        if expected_size >= _UINT32_MAX
        else b""
    )
    stored_size = _UINT32_MAX if extra else expected_size

    def _header(crc: int) -> bytes:
        return (
            struct.pack(
                "<4s5H3L2H",
                _LOCAL_SIGNATURE,
                version,
                0,
                0,
                _CANONICAL_DOS_TIME,
                _CANONICAL_DOS_DATE,
                crc,
                stored_size,
                stored_size,
                len(encoded_name),
                len(extra),
            )
            + encoded_name
            + extra
        )

    _write_all(destination, _header(0), f"entry {name!r} header")
    crc = 0
    size = 0
    while True:
        chunk = source.read(1 << 20)
        if not isinstance(chunk, bytes):
            raise TypeError(f"bundle entry {name!r} source did not return bytes")
        if not chunk:
            break
        size += len(chunk)
        if size > expected_size:
            raise ValueError(
                f"bundle entry {name!r} exceeds its declared size {expected_size}"
            )
        crc = zlib.crc32(chunk, crc)
        _write_all(destination, chunk, f"entry {name!r} payload")
    if size != expected_size:
        raise ValueError(
            f"bundle entry {name!r} size mismatch: expected {expected_size}, read {size}"
        )
    end = _stream_position(destination, f"entry {name!r} end")
    destination.seek(offset)
    _write_all(destination, _header(crc), f"entry {name!r} header")
    destination.seek(end)
    return _WrittenEntry(name, size, crc, offset, version)


def _write_directory(
    destination: BinaryIO,
    entries: tuple[_WrittenEntry, ...],
    limits: BundleLimits,
) -> None:
    """Write canonical central and end records for completed stored entries."""
    import struct  # noqa: PLC0415 -- loaded only for opted-in bundle writes

    directory_offset = _stream_position(destination, "central directory offset")
    planned_size = _central_directory_size(entries)
    _check_limit(
        planned_size,
        limits.max_central_directory_bytes,
        "central directory size",
    )
    directory_size = 0
    for entry in entries:
        encoded_name = entry.name.encode("ascii")
        values: list[int] = []
        if entry.size >= _UINT32_MAX:
            values.extend((entry.size, entry.size))
        if entry.offset >= _UINT32_MAX:
            values.append(entry.offset)
        extra = _zip64_extra(tuple(values)) if values else b""
        size = _UINT32_MAX if entry.size >= _UINT32_MAX else entry.size
        offset = _UINT32_MAX if entry.offset >= _UINT32_MAX else entry.offset
        record = (
            struct.pack(
                "<4s6H3L5H2L",
                b"PK\x01\x02",
                (_UNIX_SYSTEM << 8) | entry.version,
                entry.version,
                0,
                0,
                _CANONICAL_DOS_TIME,
                _CANONICAL_DOS_DATE,
                entry.crc,
                size,
                size,
                len(encoded_name),
                len(extra),
                0,
                0,
                0,
                _CANONICAL_EXTERNAL_ATTR,
                offset,
            )
            + encoded_name
            + extra
        )
        directory_size += len(record)
        _write_all(destination, record, "central directory")
    if directory_size != planned_size:
        raise AssertionError("planned central directory size disagrees with encoding")
    count = len(entries)
    needs_zip64 = (
        count >= _UINT16_MAX
        or directory_size >= _UINT32_MAX
        or directory_offset >= _UINT32_MAX
    )
    if needs_zip64:
        record_offset = _stream_position(destination, "ZIP64 end record offset")
        _write_all(
            destination,
            struct.pack(
                "<4sQ2H2L4Q",
                _ZIP64_EOCD_SIGNATURE,
                _ZIP64_EOCD_MINIMUM_BODY_SIZE,
                _ZIP64_VERSION,
                _ZIP64_VERSION,
                0,
                0,
                count,
                count,
                directory_size,
                directory_offset,
            ),
            "ZIP64 end record",
        )
        _write_all(
            destination,
            struct.pack("<4sLQL", _ZIP64_LOCATOR_SIGNATURE, 0, record_offset, 1),
            "ZIP64 locator",
        )
    classic_count = _UINT16_MAX if count >= _UINT16_MAX else count
    classic_size = _UINT32_MAX if directory_size >= _UINT32_MAX else directory_size
    classic_offset = (
        _UINT32_MAX if directory_offset >= _UINT32_MAX else directory_offset
    )
    _write_all(
        destination,
        struct.pack(
            "<4s4H2LH",
            _EOCD_SIGNATURE,
            0,
            0,
            classic_count,
            classic_count,
            classic_size,
            classic_offset,
            0,
        ),
        "bundle ZIP end record",
    )


def _zip64_extra(values: tuple[int, ...]) -> bytes:
    """Encode one minimal ZIP64 extra record for its required values."""
    import struct  # noqa: PLC0415 -- loaded only for opted-in bundle writes

    body = struct.pack(f"<{len(values)}Q", *values)
    return struct.pack("<HH", _ZIP64_EXTRA, len(body)) + body


def _stream_position(stream: BinaryIO, subject: str) -> int:
    """Return a nonnegative integral stream position."""
    position = stream.tell()
    if isinstance(position, bool) or not isinstance(position, int) or position < 0:
        raise ValueError(f"{subject} is not a nonnegative integer")
    return position


def _write_all(destination: BinaryIO, data: bytes, subject: str) -> None:
    """Write every byte while refusing nonblocking and invalid writer results."""
    offset = 0
    while offset < len(data):
        written = destination.write(data[offset:])
        if isinstance(written, bool) or not isinstance(written, int) or written <= 0:
            raise ValueError(f"{subject} writer made no progress")
        if written > len(data) - offset:
            raise ValueError(f"{subject} writer reported too many bytes")
        offset += written


def bundle_json_schema() -> dict[str, object]:
    """Return the strict JSON Schema for bundle version 1 indexes."""
    digest = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    size = {"type": "integer", "minimum": 0}
    identity = {
        "id": {"type": "string", "minLength": 1},
        "position": {"type": "integer", "minimum": 0},
        "sha256": digest,
        "size": size,
    }
    embedded = {
        "type": "object",
        "additionalProperties": False,
        "required": ["id", "position", "sha256", "size", "mode"],
        "properties": {**identity, "mode": {"const": "embedded"}},
    }
    linked = {
        "type": "object",
        "additionalProperties": False,
        "required": ["id", "position", "sha256", "size", "mode"],
        "properties": {
            **identity,
            "mode": {"const": "linked"},
            "href": {"type": "string"},
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://tiergraph.org/schema/bundle-1.json",
        "type": "object",
        "additionalProperties": False,
        "required": ["bundle_version", "graph", "assets"],
        "properties": {
            "bundle_version": {"const": BUNDLE_VERSION},
            "graph": {
                "type": "object",
                "additionalProperties": False,
                "required": ["path", "sha256", "size"],
                "properties": {
                    "path": {"const": _BUNDLE_GRAPH},
                    "sha256": digest,
                    "size": size,
                },
            },
            "assets": {"type": "array", "items": {"oneOf": [embedded, linked]}},
        },
    }


def _read_central_directory(  # noqa: PLR0915 -- fixed binary record sequence
    source: BinaryIO, limits: BundleLimits
) -> _CentralDirectory:
    """Read bounded end records and reject limits before ZIP allocates entries."""
    import struct  # noqa: PLC0415 -- loaded only for opted-in bundle reads

    if not source.seekable() or not source.readable():
        raise ValueError("bundle source must be a seekable, readable binary stream")
    try:
        file_size = source.seek(0, io.SEEK_END)
    except (OSError, ValueError) as error:
        raise ValueError("bundle source cannot be measured") from error
    if (
        isinstance(file_size, bool)
        or not isinstance(file_size, int)
        or file_size < _EOCD_SIZE
    ):
        raise ValueError("bundle ZIP has no complete end record")
    eocd_offset = file_size - _EOCD_SIZE
    tail = _read_at(source, eocd_offset, _EOCD_SIZE, "bundle ZIP end record")
    if not tail.startswith(_EOCD_SIGNATURE):
        raise ValueError(
            "bundle ZIP end record is missing or archive comments are not allowed"
        )
    fields = struct.unpack("<4s4H2LH", tail)
    _, disk, central_disk, disk_count, count, size, offset, comment_size = fields
    if comment_size:
        raise ValueError("bundle ZIP archive comments are not allowed")
    if disk != 0 or central_disk != 0 or disk_count != count:
        raise ValueError("bundle ZIP must be a single-disk archive")
    sentinels = count == _UINT16_MAX or size == _UINT32_MAX or offset == _UINT32_MAX
    zip64_offset = eocd_offset - _ZIP64_LOCATOR_SIZE
    has_locator = (
        zip64_offset >= 0
        and _read_at(source, zip64_offset, 4, "ZIP64 locator")
        == _ZIP64_LOCATOR_SIGNATURE
    )
    zip64 = False
    if sentinels:
        if not has_locator:
            raise ValueError("bundle ZIP64 end records are missing")
        locator = struct.unpack(
            "<4sLQL",
            _read_at(source, zip64_offset, _ZIP64_LOCATOR_SIZE, "ZIP64 locator"),
        )
        _, zip64_disk, record_offset, total_disks = locator
        if zip64_disk != 0 or total_disks != 1:
            raise ValueError("bundle ZIP64 must be a single-disk archive")
        if record_offset + 56 != zip64_offset:
            raise ValueError("bundle ZIP64 end-record layout is inconsistent")
        record = _read_at(source, record_offset, 56, "ZIP64 end record")
        values = struct.unpack("<4sQ2H2L4Q", record)
        (
            signature,
            record_size,
            _,
            _,
            record_disk,
            record_cd_disk,
            disk_count64,
            count64,
            size64,
            offset64,
        ) = values
        if (
            signature != _ZIP64_EOCD_SIGNATURE
            or record_size != _ZIP64_EOCD_MINIMUM_BODY_SIZE
        ):
            raise ValueError("bundle ZIP64 end record is malformed")
        if record_disk != 0 or record_cd_disk != 0 or disk_count64 != count64:
            raise ValueError("bundle ZIP64 must be a single-disk archive")
        for classic, expanded, sentinel in (
            (count, count64, _UINT16_MAX),
            (size, size64, _UINT32_MAX),
            (offset, offset64, _UINT32_MAX),
        ):
            if classic == sentinel:
                if expanded < sentinel:
                    raise ValueError(
                        "bundle ZIP64 is allowed only when a field requires it"
                    )
            elif expanded != classic:
                raise ValueError("bundle ZIP64 end records disagree")
        if offset64 + size64 != record_offset:
            raise ValueError("bundle ZIP64 central-directory layout is inconsistent")
        count, size, offset = count64, size64, offset64
        zip64 = True
    elif has_locator:
        raise ValueError("bundle ZIP64 is allowed only when a size requires it")
    _check_limit(count, limits.max_entries, "entry count")
    _check_limit(size, limits.max_central_directory_bytes, "central directory size")
    if offset + size != (record_offset if zip64 else eocd_offset):
        raise ValueError(
            "bundle central directory lies outside the archive or leaves "
            "undeclared bytes"
        )
    return _CentralDirectory(count, size, offset, eocd_offset, zip64)


def _check_archive_profile(  # noqa: PLR0915 -- complete closed ZIP profile
    source: BinaryIO,
    archive: zipfile.ZipFile,
    infos: tuple[zipfile.ZipInfo, ...],
    directory: _CentralDirectory,
    limits: BundleLimits,
) -> None:
    """Enforce the closed, canonical store-only ZIP profile."""
    import zipfile  # noqa: PLC0415 -- loaded only for opted-in bundle reads

    if len(infos) != directory.count:
        raise ValueError("bundle ZIP entry count disagrees with its end record")
    if archive.comment:
        raise ValueError("bundle ZIP archive comments are not allowed")
    if len(infos) < _REQUIRED_METADATA_ENTRIES:
        raise ValueError("bundle ZIP must contain bundle.json and graph.json")
    for info in infos:
        try:
            info.filename.encode("ascii", "strict")
        except UnicodeEncodeError as error:
            raise ValueError(
                f"bundle ZIP entry {info.filename!r} name is not ASCII"
            ) from error
    names = [info.filename for info in infos]
    if len(names) != len(set(names)):
        raise ValueError("bundle ZIP contains a duplicate entry name")
    if names[:2] != [_BUNDLE_INDEX, _BUNDLE_GRAPH]:
        raise ValueError(
            "bundle ZIP entry order must begin with bundle.json, graph.json"
        )
    total = 0
    previous_end = 0
    for index, info in enumerate(infos):
        if info.filename.startswith("/") or "\\" in info.filename:
            raise ValueError(f"bundle ZIP entry {info.filename!r} is not a safe name")
        if info.is_dir():
            raise ValueError(f"bundle ZIP entry {info.filename!r} is a directory")
        parts = info.filename.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError(f"bundle ZIP entry {info.filename!r} is not a safe name")
        if info.compress_type != zipfile.ZIP_STORED:
            raise ValueError(f"bundle ZIP entry {info.filename!r} is compressed")
        if info.flag_bits & 0x1:
            raise ValueError(f"bundle ZIP entry {info.filename!r} is encrypted")
        if info.flag_bits & 0x8:
            raise ValueError(
                f"bundle ZIP entry {info.filename!r} uses a data descriptor"
            )
        if info.flag_bits:
            raise ValueError(
                f"bundle ZIP entry {info.filename!r} has unsupported flags"
            )
        if info.date_time != _CANONICAL_DATE:
            raise ValueError(
                f"bundle ZIP entry {info.filename!r} has a noncanonical timestamp"
            )
        if (
            info.create_system != _UNIX_SYSTEM
            or info.external_attr != _CANONICAL_EXTERNAL_ATTR
        ):
            raise ValueError(
                f"bundle ZIP entry {info.filename!r} has noncanonical permissions"
            )
        if info.comment:
            raise ValueError(f"bundle ZIP entry {info.filename!r} has a comment")
        _check_zip64_extra(info)
        _check_limit(
            info.file_size, limits.max_entry_bytes, f"entry {info.filename!r} size"
        )
        total += info.file_size
        _check_limit(total, limits.max_total_bytes, "total entry size")
        if info.header_offset != previous_end:
            detail = "prefix bytes" if index == 0 else "gaps or overlapping entries"
            raise ValueError(f"bundle ZIP contains {detail}")
        previous_end = _check_local_header(source, info)
    if previous_end != directory.offset:
        raise ValueError("bundle ZIP contains bytes outside its declared entries")
    if infos[0].file_size > limits.max_index_bytes:
        _raise_limit("bundle index size", infos[0].file_size, limits.max_index_bytes)
    if infos[1].file_size > limits.max_graph_bytes:
        _raise_limit("bundle graph size", infos[1].file_size, limits.max_graph_bytes)


def _check_local_header(source: BinaryIO, info: zipfile.ZipInfo) -> int:
    """Validate one local header and return the byte after its stored payload."""
    import struct  # noqa: PLC0415 -- loaded only for opted-in bundle reads

    header = _read_at(source, info.header_offset, 30, f"entry {info.filename!r} header")
    values = struct.unpack("<4s5H3L2H", header)
    (
        signature,
        version,
        flags,
        compression,
        stamp_time,
        stamp_date,
        crc,
        compressed,
        size,
        name_size,
        extra_size,
    ) = values
    if signature != _LOCAL_SIGNATURE:
        raise ValueError(f"bundle ZIP entry {info.filename!r} has a bad local header")
    if flags != info.flag_bits or compression != info.compress_type:
        raise ValueError(f"bundle ZIP entry {info.filename!r} local header disagrees")
    if stamp_time != _CANONICAL_DOS_TIME or stamp_date != _CANONICAL_DOS_DATE:
        raise ValueError(
            f"bundle ZIP entry {info.filename!r} local timestamp disagrees"
        )
    name = _read_at(source, info.header_offset + 30, name_size, "local entry name")
    try:
        canonical_name = info.filename.encode("ascii", "strict")
    except UnicodeEncodeError as error:
        raise ValueError(
            f"bundle ZIP entry {info.filename!r} name is not ASCII"
        ) from error
    if name != canonical_name:
        raise ValueError(
            f"bundle ZIP entry {info.filename!r} has a noncanonical local name"
        )
    extra = _read_at(
        source, info.header_offset + 30 + name_size, extra_size, "local entry extra"
    )
    needs_zip64 = info.file_size >= _UINT32_MAX or info.compress_size >= _UINT32_MAX
    if needs_zip64:
        if size != _UINT32_MAX or compressed != _UINT32_MAX:
            raise ValueError(
                f"bundle ZIP entry {info.filename!r} omits required ZIP64 sizes"
            )
        values = _check_extra_bytes(extra, required_values=2, subject=info.filename)
        if values != (info.file_size, info.compress_size):
            raise ValueError(
                f"bundle ZIP entry {info.filename!r} local ZIP64 sizes disagree"
            )
    elif extra:
        raise ValueError(
            f"bundle ZIP entry {info.filename!r} has an unsupported local extra field"
        )
    elif size != info.file_size or compressed != info.compress_size:
        raise ValueError(f"bundle ZIP entry {info.filename!r} local sizes disagree")
    if crc != info.CRC or version != info.extract_version:
        raise ValueError(f"bundle ZIP entry {info.filename!r} local header disagrees")
    return int(info.header_offset + 30 + name_size + extra_size + info.compress_size)


def _check_zip64_extra(info: zipfile.ZipInfo) -> None:
    """Allow exactly the ZIP64 metadata required by an oversized field."""
    expected = tuple(
        value
        for value, maximum in (
            (info.file_size, _UINT32_MAX),
            (info.compress_size, _UINT32_MAX),
            (info.header_offset, _UINT32_MAX),
        )
        if value >= maximum
    )
    if expected:
        values = _check_extra_bytes(
            info.extra, required_values=len(expected), subject=info.filename
        )
        if values != expected:
            raise ValueError(
                f"bundle ZIP entry {info.filename!r} ZIP64 metadata disagrees"
            )
        if info.extract_version != _ZIP64_VERSION:
            raise ValueError(
                f"bundle ZIP entry {info.filename!r} requires ZIP64 version 45"
            )
    elif info.extra:
        raise ValueError(
            f"bundle ZIP entry {info.filename!r} has an unsupported extra field"
        )
    elif info.extract_version != _ZIP_VERSION or info.create_version != _ZIP_VERSION:
        raise ValueError(
            f"bundle ZIP entry {info.filename!r} has a noncanonical ZIP version"
        )


def _check_extra_bytes(
    extra: bytes, *, required_values: int, subject: str
) -> tuple[int, ...]:
    """Require one minimally sized ZIP64 extra record and no other records."""
    import struct  # noqa: PLC0415 -- loaded only for opted-in bundle reads

    if len(extra) < _EXTRA_HEADER_SIZE:
        raise ValueError(f"bundle ZIP entry {subject!r} has malformed ZIP64 metadata")
    identifier, length = struct.unpack_from("<HH", extra)
    if (
        identifier != _ZIP64_EXTRA
        or length != required_values * 8
        or len(extra) != length + _EXTRA_HEADER_SIZE
    ):
        raise ValueError(f"bundle ZIP entry {subject!r} has nonminimal ZIP64 metadata")
    return struct.unpack_from(f"<{required_values}Q", extra, _EXTRA_HEADER_SIZE)


def _read_metadata_entry(
    archive: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int, subject: str
) -> bytes:
    """Read one already bounded metadata entry and reject short or extra bytes."""
    _check_limit(info.file_size, limit, f"{subject} size")
    with archive.open(info, "r") as reader:
        data = reader.read(info.file_size + 1)
        if not isinstance(data, bytes):
            raise TypeError(f"{subject} reader did not return bytes")
    if len(data) != info.file_size:
        raise ValueError(f"{subject} bytes disagree with the declared entry size")
    return data


def _parse_bundle_index(document: bytes) -> dict[str, object]:
    """Parse a closed strict-JSON bundle index before the graph is read."""
    import json  # noqa: PLC0415 -- loaded only for opted-in bundle reads

    def _reject_constant(value: str) -> object:
        raise ValueError(f"bundle index contains non-JSON number {value!r}")

    def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for name, value in pairs:
            if name in result:
                raise ValueError(f"bundle index repeats field {name!r}")
            result[name] = value
        return result

    try:
        parsed = json.loads(
            document.decode("utf-8"),
            object_pairs_hook=_unique,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError(f"bundle index is not strict UTF-8 JSON: {error}") from error
    root = _closed_object(parsed, {"bundle_version", "graph", "assets"}, "bundle index")
    if root["bundle_version"] != BUNDLE_VERSION:
        raise ValueError(
            f"bundle version must be {BUNDLE_VERSION!r}, got {root['bundle_version']!r}"
        )
    return root


def _graph_row(value: object) -> tuple[str, str, int]:
    """Validate and return the graph index row."""
    row = _closed_object(value, {"path", "sha256", "size"}, "bundle graph row")
    path = _string_field(row["path"], "bundle graph path")
    digest = _string_field(row["sha256"], "bundle graph SHA-256")
    size = _integer_field(row["size"], "bundle graph size")
    return path, BlobRef(digest, size).sha256, size


def _parse_assets(value: object) -> tuple[BundleAsset, ...]:
    """Validate closed asset rows in their explicit total order."""
    if not isinstance(value, list):
        raise ValueError("bundle assets must be an array")
    rows: list[BundleAsset] = []
    seen_ids: set[str] = set()
    by_ref: dict[BlobRef, tuple[str, str | None]] = {}
    for position, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise ValueError(f"bundle asset {position} must be an object")
        mode = raw.get("mode")
        fields = {"id", "position", "sha256", "size", "mode"}
        if mode == "linked" and "href" in raw:
            fields.add("href")
        row = _closed_object(raw, fields, f"bundle asset {position}")
        durable_id = _string_field(row["id"], f"bundle asset {position} id")
        DurableItemRef(durable_id)
        if durable_id in seen_ids:
            raise ValueError(f"bundle assets repeat durable id {durable_id!r}")
        seen_ids.add(durable_id)
        declared_position = _integer_field(
            row["position"], f"bundle asset {position} position"
        )
        if declared_position != position:
            raise ValueError(
                f"bundle asset {position} declares out-of-order position "
                f"{declared_position}"
            )
        ref = BlobRef(
            _string_field(row["sha256"], f"bundle asset {position} SHA-256"),
            _integer_field(row["size"], f"bundle asset {position} size"),
        )
        if mode not in {"embedded", "linked"}:
            raise ValueError(f"bundle asset {position} has unsupported mode {mode!r}")
        href = (
            _string_field(row["href"], f"bundle asset {position} href")
            if "href" in row
            else None
        )
        location = (cast(str, mode), href)
        previous = by_ref.setdefault(ref, location)
        if previous != location:
            raise ValueError(
                f"bundle assets give blob {ref.sha256} conflicting locations"
            )
        rows.append(
            BundleAsset(
                durable_id,
                position,
                ref,
                cast(Literal["embedded", "linked"], mode),
                href,
            )
        )
    return tuple(rows)


def _check_index_entry_order(
    infos: tuple[zipfile.ZipInfo, ...], assets: tuple[BundleAsset, ...]
) -> None:
    """Require exactly one payload entry per first embedded digest, in row order."""
    digests: list[str] = []
    seen: set[str] = set()
    for asset in assets:
        if asset.mode == "embedded" and asset.ref.sha256 not in seen:
            seen.add(asset.ref.sha256)
            digests.append(asset.ref.sha256)
    expected = [
        _BUNDLE_INDEX,
        _BUNDLE_GRAPH,
        *(_BLOB_PATH + value for value in digests),
    ]
    actual = [info.filename for info in infos]
    if actual != expected:
        raise ValueError(
            "bundle ZIP entries must be exactly bundle.json, graph.json, and "
            "embedded blobs in declared asset order"
        )
    by_name = {info.filename: info for info in infos}
    for asset in assets:
        if asset.mode != "embedded":
            continue
        info = by_name[_BLOB_PATH + asset.ref.sha256]
        if info.file_size != asset.ref.size:
            raise ValueError(
                f"bundle embedded blob {asset.ref.sha256} entry size "
                "does not match its asset row"
            )


def _check_inventory(graph: Graph, assets: tuple[BundleAsset, ...]) -> None:
    """Match each ordered index row to one durably identified graph blob item."""
    has_vocabulary = any(
        namespace.namespace == BLOB_NAMESPACE for namespace in graph.namespaces
    )
    entries = BlobProfile(graph).blobs() if has_vocabulary else ()
    if len(entries) != len(assets):
        raise ValueError(
            f"bundle inventory has {len(assets)} rows for {len(entries)} blob items"
        )
    tiers = {tier.declaration.name: tier for tier in graph.tiers}
    for position, ((reference, expected_ref), asset) in enumerate(
        zip(entries, assets, strict=True)
    ):
        item = tiers[reference.tier].items[reference.index]
        expected_id = item.durable_id
        if (
            asset.position != position
            or asset.id != expected_id
            or asset.ref != expected_ref
        ):
            raise ValueError(
                f"bundle asset {position} does not match graph blob item "
                f"{reference} with durable id {expected_id!r}"
            )


def _closed_object(value: object, fields: set[str], subject: str) -> dict[str, object]:
    """Require one object to have exactly its closed field set."""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{subject} must be an object")
    actual = set(value)
    if actual != fields:
        missing = sorted(fields - actual)
        unknown = sorted(actual - fields)
        raise ValueError(
            f"{subject} fields differ; missing={missing}, unknown={unknown}"
        )
    return cast(dict[str, object], value)


def _string_field(value: object, subject: str) -> str:
    """Require one JSON value to be a string."""
    if not isinstance(value, str):
        raise ValueError(f"{subject} must be a string")
    return value


def _integer_field(value: object, subject: str) -> int:
    """Require one JSON value to be a nonnegative integer other than bool."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{subject} must be a nonnegative integer")
    return value


def _check_limit(value: int, limit: int, subject: str) -> None:
    """Refuse a declared resource amount above its configured limit."""
    if value > limit:
        _raise_limit(subject, value, limit)


def _raise_limit(subject: str, value: int, limit: int) -> None:
    """Raise the common limit-class refusal used by bundle parsing."""
    raise ValueError(f"bundle limit: {subject} {value} exceeds {limit}")


def _read_at(source: BinaryIO, offset: int, size: int, subject: str) -> bytes:
    """Read exactly one bounded byte range from a seekable binary source."""
    try:
        source.seek(offset)
    except (OSError, ValueError) as error:
        raise ValueError(f"cannot seek to {subject}") from error
    return _read_exact(source, size, subject)


def _read_exact(source: BinaryIO, size: int, subject: str) -> bytes:
    """Read an exact small structure without accepting oversized read results."""
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = source.read(remaining)
        if not isinstance(chunk, bytes):
            raise TypeError(f"{subject} reader did not return bytes")
        if len(chunk) > remaining:
            raise ValueError(f"{subject} reader returned more bytes than requested")
        if not chunk:
            raise ValueError(f"{subject} is truncated")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


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
    "BUNDLE_VERSION",
    "BlobProfile",
    "BlobRef",
    "BlobResolver",
    "BlobSink",
    "BlobSpan",
    "Bundle",
    "BundleAsset",
    "BundleLimits",
    "ChainResolver",
    "MappingResolver",
    "VerifiedReader",
    "bundle_json_schema",
    "declare_blob_vocabulary",
    "embed_links",
    "hash_blob",
    "open_bundle",
    "relink",
    "write_bundle",
]
