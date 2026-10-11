"""Persist tiergraph collections in a versioned local SQLite catalog.

Import this module explicitly when a process needs a store.  Importing the
top-level :mod:`tiergraph` package does not import this module or ``sqlite3``.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import suppress
from dataclasses import dataclass, field, replace
from decimal import Decimal
from os import PathLike
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, Literal, Protocol, Self, cast

from tiergraph import predicate as _predicate
from tiergraph.blob import (
    BLOB_NAMESPACE,
    BlobProfile,
    BlobRef,
    BlobResolver,
    BlobSink,
    Bundle,
    VerifiedReader,
    open_bundle,
    write_bundle,
)
from tiergraph.core import (
    Attribute,
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    DurableItemRef,
    Graph,
    ItemRef,
    JsonAttributeValue,
    JsonType,
    JsonValue,
    LayerFact,
    LayerName,
    NamespaceDeclaration,
    QualifiedName,
    XsdType,
)
from tiergraph.diff import diff as graph_diff
from tiergraph.edit import EditAnnotations
from tiergraph.equivalence import EquivalenceView, equivalent, fingerprint
from tiergraph.patch import Patch, invert_patch, patch_dumps, patch_loads
from tiergraph.predicate import Predicate
from tiergraph.schema import Refusal, RefusalStage
from tiergraph.selection import Node, NodeKind, NodeSet
from tiergraph.wire import FORMAT_VERSION, MAX_DOCUMENT_BYTES, dumps, loads

_APPLICATION_ID = 0x54474442
_SCHEMA_VERSION = 9
_LAYOUT_VERSION = 1
_INDEX_VERSION = 6
_FINGERPRINT_DOMAIN = "tiergraph-equivalence/1"
_MINIMUM_SQLITE = (3, 37, 0)
_WAL_AUTOCHECKPOINT_PAGES = 1_000
_CATALOG_NAME = "catalog.sqlite3"
_OBJECTS_NAME = "objects"
_HASH_NAME = "sha256"
_STAGING_NAME = "staging"
_OBJECT_CHUNK_SIZE = 1 << 20
_DIGEST_HEX_LENGTH = 64
_SHARD_NAME = re.compile(r"[0-9a-f]{2}")
_MEDIA_TYPE = re.compile(
    r"[a-z0-9][a-z0-9!#$&^_.+\-]{0,126}/"
    r"[a-z0-9][a-z0-9!#$&^_.+\-]{0,126}"
)
_STORE_UID_HEX_LENGTH = 32
_UID_BYTES = 16
_MAX_NAME_BYTES = 4_096
_TGDB_EXPORT_VERSION = "1"
_COLLECTION_INDEX_NAME = "collection.json"
_COLLECTION_BUNDLE_SUFFIX = ".tgb"
_MAX_COLLECTION_INDEX_BYTES = 16 << 20
_NUMERIC_OFFSET = MAX_DOCUMENT_BYTES + 1
_NUMERIC_EXPONENT_WIDTH = len(str(2 * _NUMERIC_OFFSET))
_BLOB_MEDIA_TYPE = QualifiedName(BLOB_NAMESPACE, "media-type")
_BLOB_SCHEMA = QualifiedName(BLOB_NAMESPACE, "schema")
_BLOB_UNIT = QualifiedName(BLOB_NAMESPACE, "unit")
_TIERGRAPH_MEDIA_TYPE = "application/vnd.tiergraph+json"
_TIERGRAPH_INSPECTOR_NAME = "tiergraph-graph"
_TIERGRAPH_INSPECTOR_VERSION = "1"
_GRAPH_FACT_NAMESPACE = "urn:tiergraph:tgdb:graph-facts"
_MAX_INSPECTION_FACTS = 4_096
_INSPECTION_PAIR_SIZE = 2
_META_KEYS = frozenset(
    {
        "store_uid",
        "layout_version",
        "index_version",
        "inline_threshold",
        "fingerprint_domain",
    }
)


class TgdbError(Refusal):
    """Refuse a tgdb store operation through the shared refusal channel."""

    def __init__(self, message: str) -> None:
        """Record a store-level semantic refusal."""
        super().__init__(RefusalStage.SEMANTICS, message)


class StoreSchemaTooNew(TgdbError):
    """Refuse a catalog whose schema is newer than this build supports."""

    found: int
    supported: int

    def __init__(self, found: int, supported: int = _SCHEMA_VERSION) -> None:
        """Record the schema versions found and supported."""
        self.found = found
        self.supported = supported
        super().__init__(
            f"store schema version {found} is newer than supported version {supported}"
        )


class SqliteTooOld(TgdbError):
    """Refuse a SQLite runtime below the catalog's required version."""

    found: tuple[int, int, int]
    minimum: tuple[int, int, int]

    def __init__(
        self,
        found: tuple[int, int, int],
        minimum: tuple[int, int, int] = _MINIMUM_SQLITE,
    ) -> None:
        """Record the SQLite versions found and required."""
        self.found = found
        self.minimum = minimum
        super().__init__(
            "SQLite version "
            f"{_version_text(found)} is older than required version "
            f"{_version_text(minimum)}"
        )


class StoreBusy(TgdbError):
    """Refuse a write when the catalog remains busy past its declared timeout."""


class StaleVersion(TgdbError):
    """Refuse a write whose instance version or catalog base is no longer current."""


class StaleJournalBase(TgdbError):
    """Refuse a journal patch whose base is not the stored instance head."""


class StoreCorrupt(TgdbError):
    """Refuse a catalog whose identity, schema, or metadata is inconsistent."""


@dataclass(frozen=True, slots=True)
class CollectionInfo:
    """Describe one named collection in its declared catalog order."""

    uid: bytes
    name: str
    position: int
    retired_commit: int | None

    @property
    def retired(self) -> bool:
        """Return whether the collection is retired."""
        return self.retired_commit is not None

    def to_data(self) -> dict[str, str | int | bool | None]:
        """Return a JSON-compatible description in stable field order."""
        return {
            "uid": self.uid.hex(),
            "name": self.name,
            "position": self.position,
            "retired": self.retired,
            "retired_commit": self.retired_commit,
        }


@dataclass(frozen=True, slots=True)
class InstanceInfo:
    """Describe one stable instance identity without loading graph content."""

    uid: bytes
    collection_uid: bytes
    collection: str
    name: str
    position: int
    generation: int
    retired_commit: int | None

    @property
    def retired(self) -> bool:
        """Return whether the instance is retired."""
        return self.retired_commit is not None

    def to_data(self) -> dict[str, str | int | bool | None]:
        """Return a JSON-compatible description in stable field order."""
        return {
            "uid": self.uid.hex(),
            "collection_uid": self.collection_uid.hex(),
            "collection": self.collection,
            "name": self.name,
            "position": self.position,
            "generation": self.generation,
            "retired": self.retired,
            "retired_commit": self.retired_commit,
        }


@dataclass(frozen=True, slots=True)
class IndexInfo:
    """Describe one declared item index in its declared catalog order."""

    name: str
    position: int
    tier: QualifiedName
    attribute: QualifiedName
    history: bool

    def to_data(self) -> dict[str, str | int | bool]:
        """Return a JSON-compatible description in stable field order."""
        return {
            "name": self.name,
            "position": self.position,
            "tier": _qname_spelling(self.tier),
            "attribute": _qname_spelling(self.attribute),
            "history": self.history,
        }


@dataclass(frozen=True, slots=True)
class ColumnInfo:
    """Describe one declared blob metadata column in catalog order."""

    name: str
    position: int
    media_type: str
    attribute: QualifiedName
    value_type: str

    def to_data(self) -> dict[str, str | int]:
        """Return a JSON-compatible description in stable field order."""
        return {
            "name": self.name,
            "position": self.position,
            "media_type": self.media_type,
            "attribute": _qname_spelling(self.attribute),
            "value_type": self.value_type,
        }


class Inspector(Protocol):
    """Read typed facts from payload bytes for declared media types.

    An inspector returns at most 4,096 ``(key, value)`` tuples in the order
    they should be stored. Keys are nonempty strings and values are
    :class:`AttributeValue` instances. The source is a forward-only verified
    stream; inspectors may read it incrementally but must not seek or mutate
    it. The store drains the remaining bytes after iteration to verify the
    payload's size and digest.
    """

    name: str
    version: str
    media_types: tuple[str, ...]

    def inspect(
        self, ref: BlobRef, source: BinaryIO
    ) -> Iterable[tuple[str, AttributeValue]]:
        """Yield bounded typed facts in order while reading forward only."""
        ...


@dataclass(frozen=True, slots=True)
class InspectionFact:
    """Hold one ordered typed fact emitted by an inspector."""

    key: str
    value: AttributeValue

    def to_data(self) -> dict[str, object]:
        """Return a JSON-compatible fact description in stable field order."""
        return {"key": self.key, "value": self.value.to_data()}


@dataclass(frozen=True, slots=True)
class InspectionResult:
    """Describe one inspector run over one content-addressed payload."""

    digest: str
    media_type: str
    inspector: str
    inspector_version: str
    facts: tuple[InspectionFact, ...]

    def to_data(self) -> dict[str, object]:
        """Return a JSON-compatible result in emitted fact order."""
        return {
            "digest": self.digest,
            "media_type": self.media_type,
            "inspector": self.inspector,
            "inspector_version": self.inspector_version,
            "facts": [fact.to_data() for fact in self.facts],
        }


@dataclass(frozen=True, slots=True)
class InspectReport:
    """Summarize explicit payload inspection and optional graph publication."""

    results: tuple[InspectionResult, ...]
    changed: int
    published: CommitReceipt | None = None

    def to_data(self) -> dict[str, object]:
        """Return a JSON-compatible report in inspection order."""
        return {
            "inspected": len(self.results),
            "changed": self.changed,
            "results": [result.to_data() for result in self.results],
            "published": (None if self.published is None else self.published.to_data()),
        }


@dataclass(frozen=True, slots=True)
class VersionChange:
    """Describe one version written or skipped by a write transaction."""

    instance_uid: bytes
    name: str
    seq: int
    graph_digest: str
    status: Literal["created", "published", "unchanged", "skipped"]
    transition: Literal["initial", "patch", "snapshot"] | None = None
    reason: str | None = None
    difference_view: Literal["identified", "exact"] | None = None

    def to_data(self) -> dict[str, str | int | None]:
        """Return a JSON-compatible description in stable field order."""
        return {
            "instance_uid": self.instance_uid.hex(),
            "name": self.name,
            "seq": self.seq,
            "graph_digest": self.graph_digest,
            "status": self.status,
            "transition": self.transition,
            "reason": self.reason,
            "difference_view": self.difference_view,
        }


@dataclass(frozen=True, slots=True)
class CommitReceipt:
    """Report a durable commit or version attempts that were all unchanged."""

    commit_seq: int | None
    kind: str
    operations: tuple[str, ...]
    versions: tuple[VersionChange, ...] = ()

    def to_data(
        self,
    ) -> dict[str, int | str | None | list[str] | list[dict[str, str | int | None]]]:
        """Return a JSON-compatible receipt in stable field order."""
        return {
            "commit_seq": self.commit_seq,
            "kind": self.kind,
            "operations": list(self.operations),
            "versions": [version.to_data() for version in self.versions],
        }


@dataclass(frozen=True, slots=True)
class VersionHandle:
    """Name one immutable stored graph version without loading its document.

    Handles are returned by :meth:`TgdbStore.get` and :meth:`TgdbStore.history`;
    callers do not construct them directly.
    """

    collection: str
    instance_uid: bytes
    name: str
    position: int
    seq: int
    commit_seq: int
    graph_digest: str
    patch_digest: str | None
    transition: Literal["initial", "patch", "snapshot"]
    reason: str | None
    functional: str
    identified: str
    stage: str | None
    iteration: int | None
    _store: TgdbStore = field(repr=False, compare=False)

    def load(self) -> Graph:
        """Load and validate this version through one verified object read."""
        with self._store._open_object(self.graph_digest) as source:
            return loads(source.read())

    def load_patch(self) -> Patch | None:
        """Load this version's verified transition patch when it has one."""
        if self.patch_digest is None:
            return None
        return self._store._load_patch_digest(self.patch_digest)

    def to_data(self) -> dict[str, str | int | None]:
        """Return a JSON-compatible description without loading the graph."""
        return {
            "collection": self.collection,
            "instance_uid": self.instance_uid.hex(),
            "name": self.name,
            "position": self.position,
            "seq": self.seq,
            "commit_seq": self.commit_seq,
            "graph_digest": self.graph_digest,
            "patch_digest": self.patch_digest,
            "transition": self.transition,
            "reason": self.reason,
            "functional": self.functional,
            "identified": self.identified,
            "stage": self.stage,
            "iteration": self.iteration,
        }


@dataclass(frozen=True, slots=True)
class TgdbLimits:
    """Bound store residency, waiting, query batches, and nested resources."""

    inline_threshold: int = 64 * 1024
    busy_timeout_ms: int = 5_000
    batch_size: int = 256
    nesting_depth: int = 8

    def __post_init__(self) -> None:
        """Refuse limits that cannot define a bounded store operation."""
        for name in ("inline_threshold", "busy_timeout_ms", "nesting_depth"):
            value = getattr(self, name)
            if value < 0:
                raise ValueError(f"{name} must be nonnegative")
        if self.batch_size <= 0:
            raise ValueError("batch_size must be positive")


def _validate_inspector(inspector: object) -> None:
    """Require the public inspector identity and media declaration contract."""
    name = getattr(inspector, "name", None)
    version = getattr(inspector, "version", None)
    media_types = getattr(inspector, "media_types", None)
    inspect_method = getattr(inspector, "inspect", None)
    if not isinstance(name, str) or not name:
        raise TypeError("inspector name must be a nonempty string")
    if not isinstance(version, str) or not version:
        raise TypeError("inspector version must be a nonempty string")
    if not isinstance(media_types, tuple) or not media_types:
        raise TypeError("inspector media_types must be a nonempty tuple")
    for media_type in media_types:
        if not isinstance(media_type, str) or _MEDIA_TYPE.fullmatch(media_type) is None:
            raise ValueError(
                "inspector media_types must contain canonical lowercase types"
            )
    if len(set(media_types)) != len(media_types):
        raise ValueError("inspector media_types must not contain duplicates")
    if not callable(inspect_method):
        raise TypeError("inspector inspect must be callable")


_INSPECTORS: list[Inspector] = []


def register_inspector(inspector: Inspector) -> None:
    """Register one inspector in explicit media-type selection order.

    Domain packages opt in by importing their inspector and calling this
    function. Re-registering the same object has no effect; a different
    inspector with the same public name is refused.
    """
    _validate_inspector(inspector)
    for current in _INSPECTORS:
        if current is inspector:
            return
        if current.name == inspector.name:
            raise ValueError(f"inspector name {inspector.name!r} is already registered")
    _INSPECTORS.append(inspector)


class _TiergraphInspector:
    """Inspect canonical tiergraph documents without another decoder."""

    name: str = _TIERGRAPH_INSPECTOR_NAME
    version: str = _TIERGRAPH_INSPECTOR_VERSION
    media_types: tuple[str, ...] = (_TIERGRAPH_MEDIA_TYPE,)

    def inspect(
        self, ref: BlobRef, source: BinaryIO
    ) -> Iterable[tuple[str, AttributeValue]]:
        """Return graph identity and shape facts in a stable order."""
        del ref
        graph = loads(source.read())
        blob_rows, attachment_rows = _blob_index_rows(graph, "0" * _DIGEST_HEX_LENGTH)
        return (
            (
                "format-version",
                AttributeValue(
                    QualifiedName(_GRAPH_FACT_NAMESPACE, "format-version"),
                    XsdType.STRING,
                    FORMAT_VERSION,
                ),
            ),
            (
                "functional",
                AttributeValue(
                    QualifiedName(_GRAPH_FACT_NAMESPACE, "functional"),
                    XsdType.STRING,
                    fingerprint(graph, EquivalenceView.FUNCTIONAL),
                ),
            ),
            (
                "identified",
                AttributeValue(
                    QualifiedName(_GRAPH_FACT_NAMESPACE, "identified"),
                    XsdType.STRING,
                    fingerprint(graph, EquivalenceView.IDENTIFIED),
                ),
            ),
            (
                "tier-count",
                AttributeValue(
                    QualifiedName(_GRAPH_FACT_NAMESPACE, "tier-count"),
                    XsdType.INTEGER,
                    str(len(graph.tiers)),
                ),
            ),
            (
                "blob-count",
                AttributeValue(
                    QualifiedName(_GRAPH_FACT_NAMESPACE, "blob-count"),
                    XsdType.INTEGER,
                    str(len(blob_rows)),
                ),
            ),
            (
                "attachment-count",
                AttributeValue(
                    QualifiedName(_GRAPH_FACT_NAMESPACE, "attachment-count"),
                    XsdType.INTEGER,
                    str(len(attachment_rows)),
                ),
            ),
        )


_TIERGRAPH_INSPECTOR: Inspector = _TiergraphInspector()
register_inspector(_TIERGRAPH_INSPECTOR)


@dataclass(frozen=True, slots=True)
class StoreInfo:
    """Describe one catalog without loading any stored graph content."""

    store_uid: str
    schema_version: int
    layout_version: int
    index_version: int
    inline_threshold: int
    fingerprint_domain: str
    mode: Literal["ro", "rw"]

    def to_data(self) -> dict[str, str | int]:
        """Return a JSON-compatible description in stable field order."""
        return {
            "store_uid": self.store_uid,
            "schema_version": self.schema_version,
            "layout_version": self.layout_version,
            "index_version": self.index_version,
            "inline_threshold": self.inline_threshold,
            "fingerprint_domain": self.fingerprint_domain,
            "mode": self.mode,
        }


@dataclass(frozen=True, slots=True)
class CheckReport:
    """Summarize a successful catalog and object-pool integrity check."""

    full: bool
    objects: int
    inline_objects: int
    file_objects: int
    object_bytes: int

    def to_data(self) -> dict[str, bool | int]:
        """Return a JSON-compatible report in stable field order."""
        return {
            "full": self.full,
            "objects": self.objects,
            "inline_objects": self.inline_objects,
            "file_objects": self.file_objects,
            "object_bytes": self.object_bytes,
        }


@dataclass(frozen=True, slots=True)
class ReindexReport:
    """Summarize a completed check or rebuild of all derived indexes."""

    rebuilt: bool
    graphs: int
    tiers: int

    def to_data(self) -> dict[str, bool | int]:
        """Return a JSON-compatible report in stable field order."""
        return {
            "rebuilt": self.rebuilt,
            "graphs": self.graphs,
            "tiers": self.tiers,
        }


@dataclass(frozen=True, slots=True)
class RepackReport:
    """Summarize one explicit object-residency threshold change."""

    commit_seq: int | None
    previous_threshold: int
    threshold: int
    inline_objects: int
    file_objects: int
    moved_bytes: int

    def to_data(self) -> dict[str, int | None]:
        """Return a JSON-compatible report in stable field order."""
        return {
            "commit_seq": self.commit_seq,
            "previous_threshold": self.previous_threshold,
            "threshold": self.threshold,
            "inline_objects": self.inline_objects,
            "file_objects": self.file_objects,
            "moved_bytes": self.moved_bytes,
        }


@dataclass(frozen=True, slots=True)
class _IdsClause:
    values: tuple[bytes, ...]


@dataclass(frozen=True, slots=True)
class _NamesClause:
    values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _FingerprintClause:
    value: str
    view: EquivalenceView


@dataclass(frozen=True, slots=True)
class _TierClause:
    tier: QualifiedName
    minimum: int
    maximum: int | None
    present: bool


@dataclass(frozen=True, slots=True)
class _IndexClause:
    name: str
    value: AttributeValue | None
    minimum: Decimal | int | None
    maximum: Decimal | int | None
    present: bool


@dataclass(frozen=True, slots=True)
class _StageClause:
    stage: str | None
    iteration: int | None


@dataclass(frozen=True, slots=True)
class _ChangedSinceClause:
    commit_seq: int
    view: EquivalenceView


@dataclass(frozen=True, slots=True)
class _UsesBlobClause:
    sha256: str


@dataclass(frozen=True, slots=True)
class _BlobMediaTypeClause:
    media_type: str


@dataclass(frozen=True, slots=True)
class _BlobColumnClause:
    name: str
    equals: object | None
    minimum: Decimal | int | None
    maximum: Decimal | int | None
    source: Literal["declared", "inspected"] | None


@dataclass(frozen=True, slots=True)
class _MissingBlobClause:
    pass


@dataclass(frozen=True, slots=True)
class _InspectionCandidate:
    digest: str
    size: int
    media_type: str
    depth: int = 0
    chain: tuple[str, ...] = ()


type _QueryClause = (
    _IdsClause
    | _NamesClause
    | _FingerprintClause
    | _TierClause
    | _IndexClause
    | _StageClause
    | _ChangedSinceClause
    | _UsesBlobClause
    | _BlobMediaTypeClause
    | _BlobColumnClause
    | _MissingBlobClause
)


@dataclass(frozen=True, slots=True)
class Query:
    """Build an immutable, typed query over current instance versions.

    Every builder adds an intersecting condition. Each ``where`` predicate is
    satisfied independently by at least one item, and a graph that lacks a
    declaration named by a predicate does not match it. Results use declared
    collection and instance order unless a caller requests another order. A
    predicate is the only filter that loads documents, and it evaluates against
    one graph's items before the next document is opened.
    """

    _collection: str | None = None
    _clauses: tuple[_QueryClause, ...] = ()
    _predicates: tuple[Predicate, ...] = ()
    _include_retired: bool = False

    @classmethod
    def all(cls, collection: str | None = None) -> Query:
        """Return a query for every active head, optionally in one collection."""
        if collection is not None:
            _validate_name(collection)
        return cls(_collection=collection)

    def ids(self, *uids: bytes) -> Query:
        """Restrict results to any of the supplied stable instance ids."""
        if not uids:
            raise ValueError("ids needs at least one instance id")
        for uid in uids:
            _validate_uid(uid, "instance uid")
        return replace(self, _clauses=(*self._clauses, _IdsClause(tuple(uids))))

    def names(self, *names: str) -> Query:
        """Restrict results to any of the supplied instance names."""
        if not names:
            raise ValueError("names needs at least one instance name")
        for name in names:
            _validate_name(name)
        return replace(self, _clauses=(*self._clauses, _NamesClause(tuple(names))))

    def fingerprint(
        self,
        digest: str,
        view: EquivalenceView = EquivalenceView.FUNCTIONAL,
    ) -> Query:
        """Restrict results to one fingerprint under the requested view."""
        _validate_query_digest(digest, "fingerprint")
        _validate_equivalence_view(view)
        return replace(
            self,
            _clauses=(*self._clauses, _FingerprintClause(digest, view)),
        )

    def has_tier(
        self,
        tier: QualifiedName,
        *,
        minimum: int = 1,
        maximum: int | None = None,
    ) -> Query:
        """Restrict results to graphs with a tier in the requested size range."""
        _validate_tier_filter(tier, minimum, maximum)
        return replace(
            self,
            _clauses=(
                *self._clauses,
                _TierClause(tier, minimum, maximum, True),
            ),
        )

    def lacks_tier(self, tier: QualifiedName) -> Query:
        """Restrict results to graphs that do not declare the requested tier."""
        _validate_tier_filter(tier, 0, None)
        return replace(
            self,
            _clauses=(*self._clauses, _TierClause(tier, 0, None, False)),
        )

    def indexed(
        self,
        index: str,
        value: AttributeValue | None = None,
        *,
        minimum: Decimal | int | None = None,
        maximum: Decimal | int | None = None,
        present: bool = True,
    ) -> Query:
        """Restrict results by one declared per-item index.

        With no value or range, ``present=True`` requires at least one indexed
        item and ``present=False`` requires none. Equality keeps the scalar
        value's type distinct and verifies its canonical text after the digest
        lookup. Inclusive numeric bounds match only integer- and decimal-typed
        indexed values.
        """
        _validate_name(index)
        if value is not None and not isinstance(value, AttributeValue):
            raise TypeError("value must be an AttributeValue or None")
        _validate_numeric_bounds(minimum, maximum)
        if value is not None and (minimum is not None or maximum is not None):
            raise ValueError("indexed equality and numeric ranges are exclusive")
        if not present and (
            value is not None or minimum is not None or maximum is not None
        ):
            raise ValueError("an absent index filter cannot take a value or range")
        return replace(
            self,
            _clauses=(
                *self._clauses,
                _IndexClause(index, value, minimum, maximum, present),
            ),
        )

    def stage(self, stage: str | None, *, iteration: int | None = None) -> Query:
        """Restrict results by indexed version stage and optional iteration."""
        if stage is not None:
            _validate_name(stage)
        if iteration is not None and (
            isinstance(iteration, bool) or not isinstance(iteration, int)
        ):
            raise TypeError("iteration must be an integer or None")
        return replace(
            self,
            _clauses=(*self._clauses, _StageClause(stage, iteration)),
        )

    def changed_since(
        self,
        commit_seq: int,
        view: EquivalenceView = EquivalenceView.FUNCTIONAL,
    ) -> Query:
        """Keep heads that differ from the version current at a commit."""
        if isinstance(commit_seq, bool) or not isinstance(commit_seq, int):
            raise TypeError("commit_seq must be an integer")
        if commit_seq < 0:
            raise ValueError("commit_seq must be nonnegative")
        _validate_equivalence_view(view)
        return replace(
            self,
            _clauses=(
                *self._clauses,
                _ChangedSinceClause(commit_seq, view),
            ),
        )

    def uses_blob(self, sha256: str) -> Query:
        """Restrict results to graphs that declare one payload digest."""
        _validate_query_digest(sha256, "blob SHA-256")
        return replace(
            self,
            _clauses=(*self._clauses, _UsesBlobClause(sha256)),
        )

    def blob_media_type(self, media_type: str) -> Query:
        """Restrict results to graphs declaring one exact lowercase media type."""
        if not isinstance(media_type, str):
            raise TypeError("media_type must be a string")
        if _MEDIA_TYPE.fullmatch(media_type) is None:
            raise ValueError("media_type must be a canonical lowercase type/subtype")
        return replace(
            self,
            _clauses=(*self._clauses, _BlobMediaTypeClause(media_type)),
        )

    def blob_column(
        self,
        column: str,
        *,
        equals: object | None = None,
        minimum: Decimal | int | None = None,
        maximum: Decimal | int | None = None,
        source: Literal["declared", "inspected"] | None = None,
    ) -> Query:
        """Restrict results by one declared blob metadata column.

        Omitting equality and bounds tests for presence. Equality is converted
        through the column's declared type when the query runs. Inclusive
        numeric bounds apply only to integer and decimal columns. ``source``
        distinguishes base-item declarations from values carried by layers.
        """
        _validate_name(column)
        _validate_numeric_bounds(minimum, maximum)
        if equals is not None and (minimum is not None or maximum is not None):
            raise ValueError("blob-column equality and numeric ranges are exclusive")
        if source not in {None, "declared", "inspected"}:
            raise ValueError("source must be 'declared', 'inspected', or None")
        return replace(
            self,
            _clauses=(
                *self._clauses,
                _BlobColumnClause(column, equals, minimum, maximum, source),
            ),
        )

    def missing_blob(self) -> Query:
        """Restrict active head graphs to those with an unheld payload."""
        return replace(
            self,
            _clauses=(*self._clauses, _MissingBlobClause()),
        )

    def where(self, predicate: Predicate) -> Query:
        """Keep graphs where this predicate independently holds for an item.

        A graph that lacks a declaration named by the predicate does not match.
        Other binding and evaluation refusals remain errors.
        """
        if not isinstance(
            predicate,
            (
                _predicate.Has,
                _predicate.Equals,
                _predicate.Compare,
                _predicate.Matches,
                _predicate.Elements,
                _predicate.Related,
                _predicate.Spans,
                _predicate.And,
                _predicate.Or,
                _predicate.Not,
            ),
        ):
            raise TypeError("predicate must be a tiergraph Predicate")
        return replace(self, _predicates=(*self._predicates, predicate))

    def include_retired(self) -> Query:
        """Include retired instances and collections in the result."""
        return replace(self, _include_retired=True)


class _VerifiedObjectReader(VerifiedReader):
    """Translate a stored object's EOF mismatch into a store corruption."""

    def __init__(self, source: BinaryIO, ref: BlobRef) -> None:
        """Retain the content address for a precise corruption refusal."""
        self._object_digest = ref.sha256
        super().__init__(source, ref)

    def _finish(self) -> None:
        """Verify once and keep blob-reader details behind the tgdb taxonomy."""
        try:
            super()._finish()
        except ValueError as error:
            raise StoreCorrupt(
                f"object {self._object_digest} failed verification: {error}"
            ) from error


class _StoreResolver:
    """Resolve held content by digest without interpreting an href."""

    def __init__(self, store: TgdbStore) -> None:
        """Retain the open store that owns the content pool."""
        self._store = store

    def open(self, ref: BlobRef, href: str | None) -> BinaryIO | None:
        """Return a verified reader, or ``None`` when the digest is not held."""
        del href
        if not isinstance(ref, BlobRef):
            raise TypeError("ref must be a BlobRef")
        self._store._require_open()
        if self._store._object_record(ref.sha256) is None:
            return None
        source = cast(BinaryIO, self._store._open_object(ref.sha256))
        return cast(BinaryIO, VerifiedReader(source, ref))


class _StoreSink:
    """Publish verified payloads through one store transaction per call."""

    def __init__(self, store: TgdbStore) -> None:
        """Retain the writable store that owns the content pool."""
        self._store = store

    def put(self, ref: BlobRef, source: BinaryIO) -> None:
        """Store one exactly matching payload without assigning an href."""
        if not isinstance(ref, BlobRef):
            raise TypeError("ref must be a BlobRef")
        with self._store.write() as transaction:
            transaction.put_blob(source, expected=ref)
            transaction.commit()


class TgdbStore:
    """Own one connection to a local tgdb catalog."""

    def __init__(
        self,
        path: Path,
        connection: sqlite3.Connection,
        info: StoreInfo,
        limits: TgdbLimits,
    ) -> None:
        """Retain a validated connection created by :meth:`create` or :meth:`open`."""
        self._path = path
        self._connection = connection
        self._info = info
        self._limits = limits
        self._closed = False
        self._writer: WriteTransaction | None = None

    @classmethod
    def create(
        cls,
        path: str | PathLike[str],
        *,
        limits: TgdbLimits = TgdbLimits(),  # noqa: B008 -- immutable public default
    ) -> Self:
        """Create and open a new store directory without replacing any path."""
        _require_sqlite()
        root = Path(path)
        if root.exists() or root.is_symlink():
            raise TgdbError(f"store path {str(root)!r} already exists")
        try:
            root.mkdir()
        except FileExistsError as error:
            raise TgdbError(f"store path {str(root)!r} already exists") from error
        connection: sqlite3.Connection | None = None
        try:
            _initialize_layout(root)
            connection = _connect(root / _CATALOG_NAME, "rw", limits, create=True)
            _configure_writable(connection)
            _initialize(connection, limits)
            info = _read_info(connection, "rw", root)
        except Exception:
            if connection is not None:
                connection.close()
            _remove_failed_store(root)
            raise
        return cls(root, connection, info, limits)

    @classmethod
    def open(
        cls,
        path: str | PathLike[str],
        *,
        mode: Literal["ro", "rw"] = "ro",
        limits: TgdbLimits = TgdbLimits(),  # noqa: B008 -- immutable public default
    ) -> Self:
        """Open a store without migrating it or loading graph content.

        The catalog's recorded inline threshold takes precedence over the
        corresponding value in ``limits``.
        """
        _require_sqlite()
        if mode not in ("ro", "rw"):
            raise ValueError("mode must be 'ro' or 'rw'")
        root = Path(path)
        catalog = root / _CATALOG_NAME
        if not root.is_dir() or not catalog.is_file():
            raise TgdbError(f"path {str(root)!r} is not a tgdb store")
        try:
            connection = _connect(catalog, mode, limits)
        except sqlite3.DatabaseError as error:
            raise StoreCorrupt(
                f"cannot read tgdb catalog {str(root)!r}: {error}"
            ) from error
        try:
            info = _read_info(connection, mode, root)
            if mode == "rw":
                _configure_writable(connection)
        except Exception:
            connection.close()
            raise
        return cls(root, connection, info, limits)

    @property
    def path(self) -> Path:
        """Return the caller-supplied store directory path."""
        return self._path

    @property
    def closed(self) -> bool:
        """Return whether this store connection has been closed."""
        return self._closed

    def info(self) -> StoreInfo:
        """Return catalog metadata read and validated when the store opened."""
        if self._closed:
            raise TgdbError("store is closed")
        return self._info

    def close(self) -> None:
        """Close this store connection; repeated calls have no effect."""
        if not self._closed:
            if self._writer is not None:
                self._writer.discard()
            self._connection.close()
            self._closed = True

    def collections(
        self, *, include_retired: bool = False
    ) -> tuple[CollectionInfo, ...]:
        """Return collections in declared order, excluding retired rows by default."""
        self._require_open()
        self._require_current_schema()
        clause = "" if include_retired else "WHERE retired_commit IS NULL"
        try:
            rows = self._connection.execute(
                "SELECT uid, name, position, retired_commit FROM collections "
                f"{clause} ORDER BY position"
            )
            return tuple(CollectionInfo(*row) for row in rows)
        except sqlite3.DatabaseError as error:
            raise StoreCorrupt(f"cannot list collections: {error}") from error

    def instances(
        self,
        collection: bytes | str | None = None,
        *,
        include_retired: bool = False,
    ) -> tuple[InstanceInfo, ...]:
        """Return instances in collection and instance declared order."""
        self._require_open()
        self._require_current_schema()
        parameters: list[object] = []
        conditions: list[str] = []
        if collection is not None:
            collection_id = _resolve_collection(
                self._connection, collection, include_retired=True
            )[0]
            conditions.append("i.collection_id = ?")
            parameters.append(collection_id)
        if not include_retired:
            conditions.extend(("i.retired_commit IS NULL", "c.retired_commit IS NULL"))
        where = "" if not conditions else "WHERE " + " AND ".join(conditions)
        try:
            rows = self._connection.execute(
                "SELECT i.uid, c.uid, c.name, i.name, i.position, i.generation, "
                "i.retired_commit FROM instances AS i "
                "JOIN collections AS c ON c.id = i.collection_id "
                f"{where} ORDER BY c.position, i.position",
                parameters,
            )
            return tuple(InstanceInfo(*row) for row in rows)
        except sqlite3.DatabaseError as error:
            raise StoreCorrupt(f"cannot list instances: {error}") from error

    def indexes(self) -> tuple[IndexInfo, ...]:
        """Return declared item indexes in their declared order."""
        self._require_open()
        self._require_current_schema()
        try:
            rows = self._connection.execute(
                "SELECT name, position, tier, attribute, history "
                "FROM declared_indexes ORDER BY position"
            )
            return tuple(
                IndexInfo(
                    name,
                    position,
                    _qname_from_text(tier),
                    _qname_from_text(attribute),
                    bool(history),
                )
                for name, position, tier, attribute, history in rows
            )
        except (sqlite3.DatabaseError, TypeError, ValueError) as error:
            raise StoreCorrupt(f"cannot list declared indexes: {error}") from error

    def columns(self) -> tuple[ColumnInfo, ...]:
        """Return declared blob metadata columns in their declared order."""
        self._require_open()
        self._require_current_schema()
        try:
            rows = self._connection.execute(
                "SELECT name, position, media_type, attribute, value_type "
                "FROM declared_columns ORDER BY position"
            )
            return tuple(
                ColumnInfo(
                    name,
                    position,
                    media_type,
                    _qname_from_text(attribute),
                    value_type,
                )
                for name, position, media_type, attribute, value_type in rows
            )
        except (sqlite3.DatabaseError, TypeError, ValueError) as error:
            raise StoreCorrupt(f"cannot list declared columns: {error}") from error

    def get(
        self,
        instance: bytes | str,
        *,
        collection: bytes | str | None = None,
        seq: int | Literal["head"] = "head",
    ) -> VersionHandle:
        """Return one immutable version handle without loading graph content."""
        self._require_open()
        self._require_current_schema()
        row = _resolve_instance(self._connection, instance, collection=collection)
        if seq != "head":
            _validate_version_seq(seq)
        try:
            version = _version_row(
                self._connection,
                row[0],
                head_version=row[7] if seq == "head" else None,
                seq=None if seq == "head" else seq,
            )
            if version is not None:
                return _version_handle(self, row, version)
        except sqlite3.DatabaseError as error:
            raise StoreCorrupt(f"cannot read instance version: {error}") from error
        selected = "head" if seq == "head" else f"version {seq}"
        raise TgdbError(f"instance {row[3]!r} has no {selected}")

    def history(
        self,
        instance: bytes | str,
        *,
        collection: bytes | str | None = None,
    ) -> tuple[VersionHandle, ...]:
        """Return an instance's retained versions in append order without loading."""
        self._require_open()
        self._require_current_schema()
        row = _resolve_instance(self._connection, instance, collection=collection)
        try:
            versions = self._connection.execute(
                _VERSION_SELECT + " WHERE v.instance_id = ? ORDER BY v.seq",
                (row[0],),
            ).fetchall()
            return tuple(_version_handle(self, row, version) for version in versions)
        except sqlite3.DatabaseError as error:
            raise StoreCorrupt(f"cannot read instance history: {error}") from error

    def diff(
        self,
        instance: bytes | str,
        source: int,
        target: int | Literal["head"] = "head",
        *,
        collection: bytes | str | None = None,
    ) -> Patch:
        """Return an exact patch between two retained versions of one instance."""
        source_handle = self.get(instance, collection=collection, seq=source)
        target_handle = self.get(instance, collection=collection, seq=target)
        return graph_diff(
            source_handle.load(), target_handle.load(), EquivalenceView.EXACT
        )

    def write(
        self,
        *,
        annotations: EditAnnotations | None = None,
    ) -> WriteTransaction:
        """Open an explicit store transaction that commits only on request."""
        self._require_writable()
        if self._writer is not None:
            raise TgdbError("a write transaction is already active")
        writer = WriteTransaction(self, annotations)
        self._writer = writer
        return writer

    def snapshot(self) -> Snapshot:
        """Open an independent, stable read transaction over the current catalog.

        The snapshot keeps its own SQLite connection and must be closed promptly;
        a long-lived snapshot can delay WAL checkpointing. Writers can continue
        while the snapshot is open, but every snapshot query sees the same heads.
        """
        self._require_open()
        self._require_current_schema()
        return Snapshot(self._path, self._limits)

    def query(
        self,
        query: Query,
        *,
        order_by: str | None = None,
        batch_size: int | None = None,
        limit: int | None = None,
    ) -> list[VersionHandle]:
        """Run one short query and return handles bound to this open store."""
        self._require_open()
        self._require_current_schema()
        self._require_current_indexes()
        size = self._limits.batch_size if batch_size is None else batch_size
        _validate_query_run(query, order_by, size, limit)
        try:
            self._connection.execute("BEGIN")
            return list(
                _run_query(
                    self,
                    query,
                    order_by=order_by,
                    batch_size=size,
                    limit=limit,
                )
            )
        except sqlite3.DatabaseError as error:
            raise StoreCorrupt(f"cannot query tgdb catalog: {error}") from error
        finally:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")

    def resolver(self) -> BlobResolver:
        """Return a digest-only resolver backed by this store's object pool."""
        self._require_open()
        return _StoreResolver(self)

    def sink(self) -> BlobSink:
        """Return a sink that verifies and stores one payload per call."""
        self._require_writable()
        return _StoreSink(self)

    def inspections(
        self,
        digest: str,
        *,
        inspector: str | None = None,
    ) -> tuple[InspectionResult, ...]:
        """Return stored inspection results for one digest in inspector order."""
        self._require_open()
        self._require_current_schema()
        _validate_query_digest(digest, "inspection digest")
        if inspector is not None:
            _validate_name(inspector)
        conditions = "digest = ?"
        parameters: list[object] = [digest]
        if inspector is not None:
            conditions += " AND inspector = ?"
            parameters.append(inspector)
        try:
            rows = self._connection.execute(
                "SELECT media_type, inspector, inspector_version, ordinal, key, "
                "value_type, value FROM inspections WHERE "
                + conditions
                + " ORDER BY rowid, ordinal",
                parameters,
            ).fetchall()
        except sqlite3.DatabaseError as error:
            raise StoreCorrupt(
                f"cannot read inspections for {digest}: {error}"
            ) from error
        grouped: list[InspectionResult] = []
        for (
            media_type,
            inspector_name,
            version,
            ordinal,
            key,
            value_type,
            value,
        ) in rows:
            fact = InspectionFact(
                cast(str, key),
                _decode_inspection_value(
                    cast(str, value_type), cast(str, value), digest
                ),
            )
            if (
                grouped
                and grouped[-1].media_type == media_type
                and grouped[-1].inspector == inspector_name
                and grouped[-1].inspector_version == version
            ):
                previous = grouped[-1]
                if ordinal != len(previous.facts):
                    raise StoreCorrupt(
                        f"inspection rows for {digest} and {inspector_name!r} "
                        "have noncontiguous ordinals"
                    )
                grouped[-1] = replace(previous, facts=(*previous.facts, fact))
            else:
                if ordinal != 0:
                    raise StoreCorrupt(
                        f"inspection rows for {digest} and {inspector_name!r} "
                        "do not begin at ordinal zero"
                    )
                grouped.append(
                    InspectionResult(
                        digest,
                        cast(str, media_type),
                        cast(str, inspector_name),
                        cast(str, version),
                        (fact,),
                    )
                )
        return tuple(grouped)

    def inspect(
        self,
        *,
        media_type: str | None = None,
        inspector: str | None = None,
        digests: Iterable[str] = (),
    ) -> InspectReport:
        """Inspect held payloads explicitly and retain their ordered facts.

        Without ``digests``, current active heads supply payloads in declared
        collection, instance, tier, and item order. Explicit digests retain the
        caller's order. Attached tiergraph documents are indexed recursively,
        within :class:`TgdbLimits` ``nesting_depth``. Inspectors never run from
        any other store operation.
        """
        self._require_writable()
        if self._writer is not None:
            raise TgdbError("cannot inspect during a write transaction")
        if media_type is not None:
            if not isinstance(media_type, str):
                raise TypeError("media_type must be a string or None")
            if _MEDIA_TYPE.fullmatch(media_type) is None:
                raise ValueError(
                    "media_type must be a canonical lowercase type/subtype"
                )
        if inspector is not None:
            _validate_name(inspector)
        requested = tuple(digests)
        for digest in requested:
            _validate_query_digest(digest, "inspection digest")
        selected_inspectors = tuple(
            candidate
            for candidate in _INSPECTORS
            if inspector is None or candidate.name == inspector
        )
        if inspector is not None and not selected_inspectors:
            raise TgdbError(f"inspector {inspector!r} is not registered")
        candidates = list(_inspection_candidates(self, requested))
        if media_type is not None:
            candidates = [
                candidate
                for candidate in candidates
                if candidate.media_type == media_type
            ]
        if requested:
            found = {candidate.digest for candidate in candidates}
            missing = next(
                (digest for digest in requested if digest not in found), None
            )
            if missing is not None:
                raise TgdbError(
                    f"object {missing} is not a held, referenced payload matching "
                    "the inspection selection"
                )
        results, graphs = _run_inspections(
            self,
            candidates,
            selected_inspectors,
            media_type=media_type,
            strict=bool(requested) or media_type is not None,
        )
        changed = _store_inspections(self, results, graphs)
        return InspectReport(results, changed)

    def export_bundle(
        self,
        instance: bytes | str,
        destination: BinaryIO,
        *,
        collection: bytes | str | None = None,
        seq: int | Literal["head"] = "head",
        embed: Callable[[BlobRef], bool] | bool = True,
        hrefs: Mapping[BlobRef, str] | None = None,
    ) -> None:
        """Write one stored version as an external-resource bundle.

        The stored graph document is required to have the canonical public
        spelling before export. Payload choices affect only bundle residency;
        graph bytes, graph identity, asset ids, and declared order stay fixed.
        """
        handle = self.get(instance, collection=collection, seq=seq)
        with self._open_object(handle.graph_digest) as source:
            document = source.read()
        try:
            graph = loads(document)
        except Refusal as error:
            raise StoreCorrupt(
                f"stored graph {handle.graph_digest} cannot be exported: {error}"
            ) from error
        if _graph_document(graph) != document:
            raise StoreCorrupt(
                f"stored graph {handle.graph_digest} is not canonically encoded"
            )
        try:
            write_bundle(
                graph,
                destination,
                self.resolver(),
                embed=embed,
                hrefs=hrefs,
            )
        except ValueError as error:
            raise TgdbError(f"cannot export bundle: {error}") from error

    def export_collection(
        self,
        collection: bytes | str,
        directory: str | PathLike[str],
        *,
        embed: Callable[[BlobRef], bool] | bool = True,
    ) -> None:
        """Export active instance heads and their declared order to a directory.

        The target is created without replacing an existing path. Every bundle
        is made durable before the closed ``collection.json`` index is linked
        last, so an incomplete export cannot look complete. Full history is
        intentionally outside this interchange format.
        """
        self._require_open()
        self._require_current_schema()
        collection_row = _resolve_collection(
            self._connection, collection, include_retired=False
        )
        target = Path(directory)
        if target.exists() or target.is_symlink():
            raise TgdbError(f"collection export path {str(target)!r} already exists")
        parent = target.parent
        if parent.is_symlink() or not parent.is_dir():
            raise TgdbError(
                f"collection export parent {str(parent)!r} is not a plain directory"
            )
        instances = self.instances(collection_row[1])
        staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=parent))
        try:
            rows: list[dict[str, object]] = []
            bundle_names: list[str] = []
            for position, instance in enumerate(instances):
                handle = self.get(instance.uid)
                bundle_name = instance.uid.hex() + _COLLECTION_BUNDLE_SUFFIX
                bundle_names.append(bundle_name)
                bundle_path = staging / bundle_name
                with bundle_path.open("xb") as destination:
                    self.export_bundle(instance.uid, destination, embed=embed)
                    destination.flush()
                    os.fsync(destination.fileno())
                rows.append(
                    {
                        "uid": instance.uid.hex(),
                        "name": instance.name,
                        "position": position,
                        "head_digest": handle.graph_digest,
                        "functional": handle.functional,
                    }
                )
            manifest = {
                "tgdb_export": _TGDB_EXPORT_VERSION,
                "collection": {
                    "uid": collection_row[1].hex(),
                    "name": collection_row[2],
                    "position": 0,
                },
                "instances": rows,
            }
            encoded = _collection_index_bytes(manifest)
            with (staging / _COLLECTION_INDEX_NAME).open("xb") as destination:
                destination.write(encoded)
                destination.flush()
                os.fsync(destination.fileno())
            _fsync_directory(staging)
            _publish_collection_directory(
                staging, target, (*bundle_names, _COLLECTION_INDEX_NAME)
            )
            _fsync_directory(parent)
        except OSError as error:
            raise TgdbError(f"cannot export collection: {error}") from error
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def undo(
        self,
        commit_seq: int,
        *,
        annotations: EditAnnotations | None = None,
    ) -> CommitReceipt:
        """Apply a commit's recorded inverses unless a later commit touched its rows."""
        if isinstance(commit_seq, bool) or not isinstance(commit_seq, int):
            raise TypeError("commit_seq must be an integer")
        if commit_seq <= 0:
            raise ValueError("commit_seq must be positive")
        with self.write(annotations=annotations) as transaction:
            transaction._undo(commit_seq)
            return transaction.commit()

    def check(self, *, full: bool = False) -> CheckReport:
        """Check the catalog and every recorded object without changing the store.

        A normal check validates SQLite, object records, file placement, residency,
        and byte counts.  A full check additionally hashes every inline and file
        object and compares it with its content address.
        """
        self._require_open()
        self._require_current_schema()
        _require_pool_layout(self._path, writable=False)
        _check_object_tree(self._path)
        try:
            result = self._connection.execute("PRAGMA quick_check").fetchone()
            if result != ("ok",):
                detail = "no result" if result is None else str(result[0])
                raise StoreCorrupt(f"tgdb catalog check failed: {detail}")
            foreign_key_error = self._connection.execute(
                "PRAGMA foreign_key_check"
            ).fetchone()
            if foreign_key_error is not None:
                raise StoreCorrupt(
                    "tgdb catalog has a foreign-key violation in "
                    f"{foreign_key_error[0]!r}"
                )
            for forward, inverse in self._connection.execute(
                "SELECT forward, inverse FROM commit_ops ORDER BY commit_seq, ordinal"
            ):
                _action_touches(_decode_action(forward))
                _action_touches(_decode_action(inverse))
            invalid_head = self._connection.execute(
                "SELECT i.uid FROM instances AS i "
                "LEFT JOIN versions AS v ON v.id = i.head_version "
                "WHERE (i.head_version IS NOT NULL AND v.id IS NULL) "
                "OR (v.id IS NOT NULL AND v.instance_id != i.id) LIMIT 1"
            ).fetchone()
            if invalid_head is not None:
                raise StoreCorrupt(
                    f"instance {cast(bytes, invalid_head[0]).hex()} has an invalid head"
                )
            missing_fact = self._connection.execute(
                "SELECT v.graph_digest FROM versions AS v "
                "LEFT JOIN graph_facts AS f ON f.digest = v.graph_digest "
                "WHERE f.digest IS NULL LIMIT 1"
            ).fetchone()
            if missing_fact is not None:
                raise StoreCorrupt(
                    f"version object {missing_fact[0]} has no graph facts"
                )
            incomplete_tiers = self._connection.execute(
                "SELECT f.digest FROM graph_facts AS f LEFT JOIN ("
                "SELECT digest, count(*) AS row_count "
                "FROM graph_tiers GROUP BY digest"
                ") AS gt ON gt.digest = f.digest "
                "WHERE coalesce(gt.row_count, 0) != f.tier_count LIMIT 1"
            ).fetchone()
            if incomplete_tiers is not None:
                raise StoreCorrupt(
                    f"graph_tiers rows for object {incomplete_tiers[0]} are incomplete"
                )
            _require_complete_derived_indexes(self._connection)
            _check_inspection_rows(self._connection)
            for fact in self._connection.execute(
                "SELECT f.digest, f.functional, f.identified, f.size, o.size "
                "FROM graph_facts AS f JOIN objects AS o ON o.digest = f.digest"
            ):
                digest, functional, identified, fact_size, object_size = fact
                _validate_digest(digest)
                _validate_fingerprint(functional, "functional", digest)
                _validate_fingerprint(identified, "identified", digest)
                if fact_size != object_size:
                    raise StoreCorrupt(
                        f"graph facts for object {digest} record size {fact_size} "
                        f"instead of {object_size}"
                    )
            rows = self._connection.execute(
                "SELECT digest, size, residency, data FROM objects"
            )
            objects = inline_objects = file_objects = object_bytes = 0
            for digest, size, residency, data in rows:
                _validate_object_record(digest, size, residency, data)
                if residency == "inline":
                    inline_data = cast(bytes, data)
                    inline_objects += 1
                    if len(inline_data) != size:
                        raise StoreCorrupt(
                            f"object {digest} records {size} bytes but stores "
                            f"{len(inline_data)} inline bytes"
                        )
                    if full:
                        _require_digest(digest, inline_data)
                else:
                    file_objects += 1
                    path = _object_path(self._path, digest)
                    _check_object_file(path, digest, size, full=full)
                objects += 1
                object_bytes += size
        except sqlite3.DatabaseError as error:
            raise StoreCorrupt(
                f"cannot check tgdb catalog {str(self._path)!r}: {error}"
            ) from error
        return CheckReport(
            full=full,
            objects=objects,
            inline_objects=inline_objects,
            file_objects=file_objects,
            object_bytes=object_bytes,
        )

    def reindex(self, *, rebuild: bool = False) -> ReindexReport:
        """Check or rebuild deterministic graph, tier, and declared-item rows.

        The check form works through a read-only handle and changes nothing.
        Rebuilding requires a writable handle, replaces only derived rows, and
        records the build's index and fingerprint-domain versions.
        """
        self._require_open()
        self._require_current_schema()
        if not isinstance(rebuild, bool):
            raise TypeError("rebuild must be a boolean")
        if rebuild:
            self._require_writable()
            if self._writer is not None:
                raise TgdbError("cannot rebuild indexes during a write transaction")
        (
            expected_facts,
            expected_tiers,
            expected_blobs,
            expected_attachments,
            expected_metadata,
            expected_metadata_counts,
            expected_item_counts,
            expected_items,
        ) = _derive_indexes(self)
        if rebuild:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                current = tuple(
                    row[0]
                    for row in self._connection.execute(
                        "SELECT graph_digest FROM versions UNION SELECT digest "
                        "FROM inspections WHERE inspector = ? AND inspector_version = ? "
                        "ORDER BY 1",
                        (_TIERGRAPH_INSPECTOR_NAME, _TIERGRAPH_INSPECTOR_VERSION),
                    )
                )
                if current != tuple(expected_facts):
                    raise StaleVersion(
                        "stored graph set changed while derived indexes were rebuilt"
                    )
                self._connection.execute("DELETE FROM attachments")
                self._connection.execute("DELETE FROM blob_metadata")
                self._connection.execute("DELETE FROM blob_metadata_counts")
                self._connection.execute("DELETE FROM blob_items")
                self._connection.execute("DELETE FROM graph_tiers")
                self._connection.execute("DELETE FROM item_index")
                self._connection.execute("DELETE FROM item_index_counts")
                self._connection.execute("DELETE FROM graph_facts")
                self._connection.executemany(
                    "INSERT INTO graph_facts("
                    "digest, format_version, fp_domain, functional, identified, size, "
                    "tier_count, blob_count, attachment_count) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    expected_facts.values(),
                )
                self._connection.executemany(
                    "INSERT INTO graph_tiers(digest, position, tier, item_count) "
                    "VALUES (?, ?, ?, ?)",
                    expected_tiers,
                )
                self._connection.executemany(
                    "INSERT INTO blob_items("
                    "graph_digest, tier, position, durable_id, sha256, size, "
                    "media_type, schema) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    expected_blobs,
                )
                self._connection.executemany(
                    "INSERT INTO attachments("
                    "graph_digest, ordinal, declaration, durable_id, subject, "
                    "blob_tier, blob_position, span_unit, span_offset, "
                    "span_length, span_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    expected_attachments,
                )
                self._connection.executemany(
                    "INSERT INTO blob_metadata("
                    "graph_digest, tier, position, column, value_text, "
                    "value_num, source) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    expected_metadata,
                )
                self._connection.executemany(
                    "INSERT INTO blob_metadata_counts("
                    "graph_digest, column, row_count, row_digest) "
                    "VALUES (?, ?, ?, ?)",
                    expected_metadata_counts,
                )
                self._connection.executemany(
                    "INSERT INTO item_index_counts(graph_digest, index_name, row_count) "
                    "VALUES (?, ?, ?)",
                    expected_item_counts,
                )
                self._connection.executemany(
                    "INSERT INTO item_index("
                    "graph_digest, index_name, tier_position, item_position, "
                    "value_type, value_text, value_num, value_digest) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    expected_items,
                )
                self._connection.execute(
                    "UPDATE store_meta SET value = ? WHERE key = 'index_version'",
                    (str(_INDEX_VERSION),),
                )
                self._connection.execute(
                    "UPDATE store_meta SET value = ? WHERE key = 'fingerprint_domain'",
                    (_FINGERPRINT_DOMAIN,),
                )
                self._connection.execute("COMMIT")
            except sqlite3.OperationalError as error:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                if _sqlite_busy(error):
                    raise StoreBusy(
                        "tgdb catalog remained busy while rebuilding indexes"
                    ) from error
                raise StoreCorrupt(f"cannot rebuild tgdb indexes: {error}") from error
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise
            self._info = replace(
                self._info,
                index_version=_INDEX_VERSION,
                fingerprint_domain=_FINGERPRINT_DOMAIN,
            )
        else:
            _compare_derived_indexes(
                self._connection,
                expected_facts,
                expected_tiers,
                expected_blobs,
                expected_attachments,
                expected_metadata,
                expected_metadata_counts,
                expected_item_counts,
                expected_items,
            )
        return ReindexReport(
            rebuilt=rebuild,
            graphs=len(expected_facts),
            tiers=len(expected_tiers),
        )

    def repack(  # noqa: PLR0915 -- residency publication is one atomic contract
        self, threshold: int
    ) -> RepackReport:
        """Move objects across the inline threshold in one catalog commit.

        Content bytes, digests, graph versions, and graph fingerprints do not
        change. File bytes are durably published before the SQLite commit;
        superseded files are removed only after the catalog points at inline
        bytes, so interruption cannot leave a row naming an absent file.
        """
        if isinstance(threshold, bool) or not isinstance(threshold, int):
            raise TypeError("threshold must be an integer")
        if threshold < 0:
            raise ValueError("threshold must be nonnegative")
        self._require_writable()
        if self._writer is not None:
            raise TgdbError("cannot repack during a write transaction")
        previous = self._info.inline_threshold
        if threshold == previous:
            return RepackReport(None, previous, threshold, 0, 0, 0)
        _require_pool_layout(self._path, writable=True)
        inline_objects = 0
        file_objects = 0
        moved_bytes = 0
        obsolete_files: list[Path] = []
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            cursor = self._connection.execute(
                "INSERT INTO commits(kind, annotations) VALUES ('repack', NULL)"
            )
            rows = self._connection.execute(
                "SELECT digest, size, residency, data FROM objects ORDER BY digest"
            ).fetchall()
            for digest, size, residency, data in rows:
                _validate_object_record(digest, size, residency, data)
                if residency == "inline" and size > threshold:
                    staging = self._path / _STAGING_NAME / secrets.token_hex(16)
                    try:
                        with staging.open("xb") as destination:
                            destination.write(cast(bytes, data))
                            destination.flush()
                            os.fsync(destination.fileno())
                        _publish_object_file(self._path, staging, digest, size)
                    finally:
                        with suppress(OSError):
                            staging.unlink()
                    self._connection.execute(
                        "UPDATE objects SET residency = 'file', data = NULL "
                        "WHERE digest = ?",
                        (digest,),
                    )
                    file_objects += 1
                    moved_bytes += size
                elif residency == "file" and size <= threshold:
                    with self._open_object(digest) as source:
                        payload = source.read()
                    self._connection.execute(
                        "UPDATE objects SET residency = 'inline', data = ? "
                        "WHERE digest = ?",
                        (payload, digest),
                    )
                    obsolete_files.append(_object_path(self._path, digest))
                    inline_objects += 1
                    moved_bytes += size
            self._connection.execute(
                "UPDATE store_meta SET value = ? WHERE key = 'inline_threshold'",
                (str(threshold),),
            )
            self._connection.execute("COMMIT")
        except sqlite3.OperationalError as error:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if _sqlite_busy(error):
                raise StoreBusy("tgdb catalog remained busy while repacking") from error
            raise StoreCorrupt(f"cannot repack tgdb objects: {error}") from error
        except Exception:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        commit_seq = cast(int, cursor.lastrowid)
        self._info = replace(self._info, inline_threshold=threshold)
        for path in obsolete_files:
            try:
                path.unlink()
                _fsync_directory(path.parent)
            except OSError as error:
                raise TgdbError(
                    f"repack commit {commit_seq} could not remove obsolete object "
                    f"file {path.name}: {error}"
                ) from error
        return RepackReport(
            commit_seq,
            previous,
            threshold,
            inline_objects,
            file_objects,
            moved_bytes,
        )

    def _put_object(
        self,
        source: BinaryIO,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> str:
        """Store one byte stream once and return its SHA-256 content address."""
        self._require_writable()
        catalog = self._connection if connection is None else connection
        _require_pool_layout(self._path, writable=True)
        prefix, finished = _read_prefix(source, self._info.inline_threshold + 1)
        if finished and len(prefix) <= self._info.inline_threshold:
            digest = hashlib.sha256(prefix).hexdigest()
            self._record_object(
                digest, len(prefix), "inline", prefix, connection=catalog
            )
            return digest

        staging = self._path / _STAGING_NAME / secrets.token_hex(16)
        digest_hash = hashlib.sha256()
        size = 0
        staging_created = False
        try:
            with staging.open("xb") as destination:
                staging_created = True
                destination.write(prefix)
                digest_hash.update(prefix)
                size += len(prefix)
                while True:
                    chunk = _read_binary(source, _OBJECT_CHUNK_SIZE)
                    if not chunk:
                        break
                    destination.write(chunk)
                    digest_hash.update(chunk)
                    size += len(chunk)
                destination.flush()
                os.fsync(destination.fileno())
            digest = digest_hash.hexdigest()
            existing = self._object_record(digest, connection=catalog)
            if existing is not None:
                _check_recorded_object(self._path, digest, existing, full=True)
                staging.unlink()
                return digest
            _publish_object_file(self._path, staging, digest, size)
            self._record_object(digest, size, "file", None, connection=catalog)
            return digest
        except Exception:
            if staging_created:
                with suppress(OSError):
                    staging.unlink()
            raise

    def _load_patch_digest(
        self,
        digest: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> Patch:
        """Load one patch object and translate malformed bytes into corruption."""
        try:
            with self._open_object(digest, connection=connection) as source:
                return patch_loads(source.read())
        except Refusal as error:
            raise StoreCorrupt(
                f"stored patch {digest} is malformed: {error}"
            ) from error

    def _open_object(
        self,
        digest: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> VerifiedReader:
        """Open one stored object through an EOF-verifying binary reader."""
        self._require_open()
        self._require_current_schema()
        _validate_digest(digest)
        record = self._object_record(digest, connection=connection)
        if record is None:
            raise TgdbError(f"object {digest} is not stored")
        size, residency, data = record
        _validate_object_record(digest, size, residency, data)
        if residency == "inline":
            source: BinaryIO = io.BytesIO(cast(bytes, data))
        else:
            path = _object_path(self._path, digest)
            _check_object_file(path, digest, size, full=False)
            try:
                descriptor = os.open(
                    path,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                )
            except OSError as error:
                raise StoreCorrupt(f"cannot open object {digest}: {error}") from error
            source = io.FileIO(descriptor, mode="rb", closefd=True)
        return _VerifiedObjectReader(source, BlobRef(digest, size))

    def _record_object(
        self,
        digest: str,
        size: int,
        residency: Literal["inline", "file"],
        data: bytes | None,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        """Publish one object row, joining a caller's transaction when present."""
        catalog = self._connection if connection is None else connection
        owns_transaction = not catalog.in_transaction
        if owns_transaction:
            catalog.execute("BEGIN IMMEDIATE")
        try:
            existing = self._object_record(digest, connection=catalog)
            if existing is None:
                catalog.execute(
                    "INSERT INTO objects(digest, size, residency, data) "
                    "VALUES (?, ?, ?, ?)",
                    (digest, size, residency, data),
                )
            else:
                _check_recorded_object(self._path, digest, existing, full=True)
                if existing[0] != size:
                    raise StoreCorrupt(
                        f"object {digest} has conflicting recorded sizes "
                        f"{existing[0]} and {size}"
                    )
            if owns_transaction:
                catalog.execute("COMMIT")
        except Exception:
            if owns_transaction:
                catalog.execute("ROLLBACK")
            raise

    def _object_record(
        self,
        digest: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> tuple[int, str, bytes | None] | None:
        """Return one raw object record, or ``None`` when it is absent."""
        catalog = self._connection if connection is None else connection
        try:
            row = catalog.execute(
                "SELECT size, residency, data FROM objects WHERE digest = ?",
                (digest,),
            ).fetchone()
        except sqlite3.DatabaseError as error:
            raise StoreCorrupt(f"cannot read object {digest}: {error}") from error
        return cast(tuple[int, str, bytes | None] | None, row)

    def _require_open(self) -> None:
        """Refuse operations after the owning connection has closed."""
        if self._closed:
            raise TgdbError("store is closed")

    def _require_current_schema(self) -> None:
        """Refuse object operations against a read-only older schema."""
        if self._info.schema_version != _SCHEMA_VERSION:
            raise TgdbError(
                f"store schema version {self._info.schema_version} requires an "
                f"explicit migration to version {_SCHEMA_VERSION}"
            )

    def _require_current_indexes(self) -> None:
        """Refuse queries whose derived rows use another derivation contract."""
        if (
            self._info.index_version != _INDEX_VERSION
            or self._info.fingerprint_domain != _FINGERPRINT_DOMAIN
        ):
            raise TgdbError("store derived indexes are stale; run reindex --rebuild")

    def _require_writable(self) -> None:
        """Refuse writes through a read-only, closed, or older store handle."""
        self._require_open()
        self._require_current_schema()
        if self._info.mode != "rw":
            raise TgdbError("store is read-only")

    def __enter__(self) -> Self:
        """Return this open store for use as a context manager."""
        if self._closed:
            raise TgdbError("store is closed")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the store when its context exits."""
        self.close()


class Snapshot:
    """Hold one stable, explicitly bounded read view of a tgdb catalog.

    A snapshot owns a separate read-only SQLite connection. Version handles
    obtained from it remain bound to that connection and therefore load only
    while the snapshot is open.
    """

    def __init__(self, path: Path, limits: TgdbLimits) -> None:
        """Open and pin a read transaction at the catalog's current state."""
        reader = TgdbStore.open(path, mode="ro", limits=limits)
        try:
            reader._connection.execute("BEGIN")
            row = reader._connection.execute(
                "SELECT coalesce(max(seq), 0) FROM commits"
            ).fetchone()
        except Exception:
            reader.close()
            raise
        self._reader = reader
        self._commit_seq = cast(int, row[0])

    @property
    def commit_seq(self) -> int:
        """Return the newest commit visible in this pinned catalog view."""
        return self._commit_seq

    @property
    def closed(self) -> bool:
        """Return whether this snapshot's read transaction has closed."""
        return self._reader.closed

    def info(self) -> StoreInfo:
        """Return metadata for the store viewed by this snapshot."""
        return self._reader.info()

    def collections(
        self, *, include_retired: bool = False
    ) -> tuple[CollectionInfo, ...]:
        """Return collections from the pinned catalog view in declared order."""
        return self._reader.collections(include_retired=include_retired)

    def instances(
        self,
        collection: bytes | str | None = None,
        *,
        include_retired: bool = False,
    ) -> tuple[InstanceInfo, ...]:
        """Return instances from the pinned catalog view in declared order."""
        return self._reader.instances(collection, include_retired=include_retired)

    def get(
        self,
        instance: bytes | str,
        *,
        collection: bytes | str | None = None,
        seq: int | Literal["head"] = "head",
    ) -> VersionHandle:
        """Return one version handle from the pinned catalog view."""
        return self._reader.get(instance, collection=collection, seq=seq)

    def history(
        self,
        instance: bytes | str,
        *,
        collection: bytes | str | None = None,
    ) -> tuple[VersionHandle, ...]:
        """Return retained versions from the pinned catalog view in append order."""
        return self._reader.history(instance, collection=collection)

    def diff(
        self,
        instance: bytes | str,
        source: int,
        target: int | Literal["head"] = "head",
        *,
        collection: bytes | str | None = None,
    ) -> Patch:
        """Return an exact patch between versions in the pinned catalog view."""
        return self._reader.diff(instance, source, target, collection=collection)

    def run(
        self,
        query: Query,
        *,
        order_by: str | None = None,
        batch_size: int = 256,
        limit: int | None = None,
    ) -> Iterator[VersionHandle]:
        """Stream matching handles in bounded batches from the pinned view."""
        if self.closed:
            raise TgdbError("snapshot is closed")
        self._reader._require_current_indexes()
        _validate_query_run(query, order_by, batch_size, limit)
        return _run_query(
            self._reader,
            query,
            order_by=order_by,
            batch_size=batch_size,
            limit=limit,
        )

    def count(self, query: Query) -> int:
        """Count matching heads without loading documents unless ``where`` is set."""
        return sum(1 for _handle in self.run(query))

    def close(self) -> None:
        """End the read transaction; repeated calls have no effect."""
        if not self._reader.closed:
            self._reader._connection.execute("ROLLBACK")
            self._reader.close()

    def __enter__(self) -> Self:
        """Return this open snapshot for a bounded context."""
        if self.closed:
            raise TgdbError("snapshot is closed")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the snapshot when its context exits."""
        self.close()


class WriteTransaction:
    """Stage ordered changes and publish them in one explicit SQLite commit."""

    def __init__(
        self,
        store: TgdbStore,
        annotations: EditAnnotations | None,
    ) -> None:
        """Stage a metadata view and assign a tentative commit sequence."""
        if annotations is not None and not isinstance(annotations, EditAnnotations):
            raise TypeError("annotations must be EditAnnotations or None")
        self._store = store
        self._stage_path = (
            store.path / _STAGING_NAME / f"{secrets.token_hex(16)}.sqlite3"
        )
        self._active = True
        self._operations: list[str] = []
        self._versions: list[VersionChange] = []
        self._staged_objects: set[str] = set()
        self._staged_graphs: set[str] = set()
        self._indexes_changed = False
        self._kind = "catalog"
        encoded = _encode_annotations(annotations)
        connection: sqlite3.Connection | None = None
        try:
            _require_pool_layout(store.path, writable=True)
            connection = sqlite3.connect(self._stage_path, autocommit=True)
            connection.execute("PRAGMA trusted_schema = OFF")
            self._base_commit_seq = _copy_catalog_metadata(
                store._connection, connection, batch_size=store._limits.batch_size
            )
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "INSERT INTO commits(kind, annotations) VALUES (?, ?)",
                (self._kind, encoded),
            )
        except sqlite3.OperationalError as error:
            if connection is not None:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                connection.close()
            _remove_staged_catalog(self._stage_path)
            if _sqlite_busy(error):
                raise StoreBusy(
                    "tgdb catalog remained busy past its timeout"
                ) from error
            raise StoreCorrupt(f"cannot begin catalog transaction: {error}") from error
        except sqlite3.DatabaseError as error:
            if connection is not None:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                connection.close()
            _remove_staged_catalog(self._stage_path)
            raise StoreCorrupt(f"cannot begin catalog transaction: {error}") from error
        except Exception:
            if connection is not None:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                connection.close()
            _remove_staged_catalog(self._stage_path)
            raise
        self._connection = connection
        self._commit_seq = cast(int, cursor.lastrowid)

    @property
    def commit_seq(self) -> int:
        """Return the sequence assigned to this transaction."""
        self._require_active()
        return self._commit_seq

    def create_collection(
        self,
        name: str,
        *,
        uid: bytes | None = None,
        position: int | None = None,
    ) -> bytes:
        """Create a collection with stable identity and declared position."""
        self._require_active()
        _validate_name(name)
        collection_uid = secrets.token_bytes(_UID_BYTES) if uid is None else uid
        _validate_uid(collection_uid, "collection uid")
        if (
            self._connection.execute(
                "SELECT 1 FROM collections WHERE uid = ?", (collection_uid,)
            ).fetchone()
            is not None
        ):
            raise TgdbError(f"collection uid {collection_uid.hex()} already exists")
        if (
            self._connection.execute(
                "SELECT 1 FROM collections WHERE name = ?", (name,)
            ).fetchone()
            is not None
        ):
            raise TgdbError(f"collection name {name!r} already exists")
        count = cast(
            int,
            self._connection.execute("SELECT count(*) FROM collections").fetchone()[0],
        )
        target = count if position is None else position
        _validate_position(target, count, allow_end=True)
        if target != count:
            rows = self._connection.execute(
                "SELECT uid, position FROM collections WHERE position >= ? "
                "ORDER BY position",
                (target,),
            ).fetchall()
            _set_positions(
                self._connection,
                "collections",
                {uid_value: old_position + 1 for uid_value, old_position in rows},
            )
        self._connection.execute(
            "INSERT INTO collections(uid, name, position, retired_commit) "
            "VALUES (?, ?, ?, NULL)",
            (collection_uid, name, target),
        )
        touch = _touch("collection", collection_uid)
        self._record(
            {
                "action": "create_collection",
                "name": name,
                "position": target,
                "touches": [touch],
                "uid": collection_uid.hex(),
            },
            {
                "action": "retire_collection",
                "touches": [touch],
                "uid": collection_uid.hex(),
            },
        )
        return collection_uid

    def create_instance(
        self,
        collection: bytes | str,
        name: str,
        graph: Graph,
        *,
        uid: bytes | None = None,
        position: int | None = None,
        annotations: EditAnnotations | None = None,
    ) -> bytes:
        """Create an ordered instance whose first version is a complete graph."""
        return self._create_instance(
            collection,
            name,
            graph,
            uid=uid,
            position=position,
            annotations=annotations,
            document=None,
        )

    def _create_instance(
        self,
        collection: bytes | str,
        name: str,
        graph: Graph,
        *,
        uid: bytes | None,
        position: int | None,
        annotations: EditAnnotations | None,
        document: bytes | None,
    ) -> bytes:
        """Implement creation with optional already verified canonical bytes."""
        self._require_active()
        _validate_name(name)
        _validate_graph(graph)
        encoded_annotations = _encode_annotations(annotations)
        collection_row = _resolve_collection(
            self._connection, collection, include_retired=False
        )
        instance_uid = secrets.token_bytes(_UID_BYTES) if uid is None else uid
        _validate_uid(instance_uid, "instance uid")
        if (
            self._connection.execute(
                "SELECT 1 FROM instances WHERE uid = ?", (instance_uid,)
            ).fetchone()
            is not None
        ):
            raise TgdbError(f"instance uid {instance_uid.hex()} already exists")
        if (
            self._connection.execute(
                "SELECT 1 FROM instances WHERE collection_id = ? AND name = ?",
                (collection_row[0], name),
            ).fetchone()
            is not None
        ):
            raise TgdbError(f"instance name {name!r} already exists in its collection")
        count = cast(
            int,
            self._connection.execute(
                "SELECT count(*) FROM instances WHERE collection_id = ?",
                (collection_row[0],),
            ).fetchone()[0],
        )
        target = count if position is None else position
        _validate_position(target, count, allow_end=True)
        digest = (
            self._store_graph(graph)
            if document is None
            else self._store_graph_document(graph, document)
        )
        if target != count:
            rows = self._connection.execute(
                "SELECT uid, position FROM instances WHERE collection_id = ? "
                "AND position >= ? ORDER BY position",
                (collection_row[0], target),
            ).fetchall()
            _set_positions(
                self._connection,
                "instances",
                {uid_value: old_position + 1 for uid_value, old_position in rows},
            )
        cursor = self._connection.execute(
            "INSERT INTO instances(uid, collection_id, name, position, head_version, "
            "generation, retired_commit) VALUES (?, ?, ?, ?, NULL, 0, NULL)",
            (instance_uid, collection_row[0], name, target),
        )
        instance_id = cast(int, cursor.lastrowid)
        version_id = self._insert_version(
            instance_id,
            seq=1,
            parent_id=None,
            graph_digest=digest,
            patch_digest=None,
            transition="initial",
            reason=None,
            annotations=annotations,
            encoded_annotations=encoded_annotations,
        )
        self._connection.execute(
            "UPDATE instances SET head_version = ? WHERE id = ?",
            (version_id, instance_id),
        )
        touch = _touch("instance", instance_uid)
        self._record(
            {
                "action": "create_instance",
                "collection_uid": collection_row[1].hex(),
                "name": name,
                "position": target,
                "touches": [touch],
                "uid": instance_uid.hex(),
            },
            {
                "action": "retire_instance",
                "touches": [touch],
                "uid": instance_uid.hex(),
            },
        )
        self._kind = "version"
        self._versions.append(
            VersionChange(instance_uid, name, 1, digest, "created", "initial")
        )
        return instance_uid

    def publish(
        self,
        instance: bytes | str,
        graph: Graph,
        *,
        expected: int,
        collection: bytes | str | None = None,
        patch: Patch | None = None,
        skip_if_equivalent: EquivalenceView | None = None,
        annotations: EditAnnotations | None = None,
    ) -> None:
        """Append a replay-verified graph version when its expected head is current.

        A supplied journal patch must name the stored head as its identified base.
        Without one, an exact diff is recorded.  A patch that exceeds the public
        JSONL limits falls back to a typed snapshot rather than being truncated.
        """
        self._publish(
            "publish",
            instance,
            graph,
            expected=expected,
            collection=collection,
            patch=patch,
            skip_if_equivalent=skip_if_equivalent,
            annotations=annotations,
        )

    def apply(
        self,
        instance: bytes | str,
        patch: Patch,
        *,
        expected: int,
        collection: bytes | str | None = None,
        annotations: EditAnnotations | None = None,
    ) -> None:
        """Apply and publish a patch against the checked current instance head."""
        self._require_active()
        if not isinstance(patch, Patch):
            raise TypeError("patch must be a Patch")
        row = _resolve_instance(self._connection, instance, collection=collection)
        _require_active_instance(self._connection, row)
        head = self._checked_head(row, expected)
        base = self._load_graph_digest(head[2])
        if patch.base_fingerprint != head[4]:
            raise StaleJournalBase(
                f"patch base for instance {row[3]!r} differs from stored head "
                f"version {head[0]}"
            )
        try:
            target = patch.apply(base)
        except Refusal as error:
            raise TgdbError(
                f"patch for instance {row[3]!r} did not replay: {error}"
            ) from error
        self._publish(
            "apply",
            row[1],
            target,
            expected=expected,
            patch=patch,
            annotations=annotations,
        )

    def revert(
        self,
        instance: bytes | str,
        *,
        to_seq: int,
        expected: int,
        collection: bytes | str | None = None,
        annotations: EditAnnotations | None = None,
    ) -> None:
        """Publish a new version containing one retained version's exact document."""
        self._require_active()
        _validate_version_seq(to_seq)
        row = _resolve_instance(self._connection, instance, collection=collection)
        _require_active_instance(self._connection, row)
        self._checked_head(row, expected)
        target = _version_row(self._connection, row[0], head_version=None, seq=to_seq)
        if target is None:
            raise TgdbError(f"instance {row[3]!r} has no version {to_seq}")
        self._publish(
            "revert",
            row[1],
            self._load_graph_digest(target[2]),
            expected=expected,
            annotations=annotations,
        )

    def _publish(  # noqa: PLR0915 -- refusal order is one atomic contract
        self,
        operation: Literal["publish", "apply", "revert"],
        instance: bytes | str,
        graph: Graph,
        *,
        expected: int,
        collection: bytes | str | None = None,
        patch: Patch | None = None,
        skip_if_equivalent: EquivalenceView | None = None,
        annotations: EditAnnotations | None = None,
        document: bytes | None = None,
    ) -> None:
        """Implement all public version-producing operations in one order."""
        self._require_active()
        _validate_graph(graph)
        _validate_version_seq(expected)
        if patch is not None and not isinstance(patch, Patch):
            raise TypeError("patch must be a Patch or None")
        if skip_if_equivalent is not None and not isinstance(
            skip_if_equivalent, EquivalenceView
        ):
            raise TypeError("skip_if_equivalent must be an EquivalenceView or None")
        encoded_annotations = _encode_annotations(annotations)
        row = _resolve_instance(self._connection, instance, collection=collection)
        _require_active_instance(self._connection, row)
        head = self._checked_head(row, expected)
        head_seq = head[0]
        document = _graph_document(graph) if document is None else document
        digest = hashlib.sha256(document).hexdigest()
        if digest == head[2]:
            self._versions.append(
                VersionChange(row[1], row[3], head_seq, digest, "unchanged")
            )
            return
        head_graph = self._load_graph_digest(head[2])
        if skip_if_equivalent is not None and equivalent(
            head_graph, graph, skip_if_equivalent
        ):
            difference_view: Literal["identified", "exact"] = (
                "exact"
                if equivalent(head_graph, graph, EquivalenceView.IDENTIFIED)
                else "identified"
            )
            self._versions.append(
                VersionChange(
                    row[1],
                    row[3],
                    head_seq,
                    digest,
                    "skipped",
                    difference_view=difference_view,
                )
            )
            return
        transition_patch = (
            graph_diff(head_graph, graph, EquivalenceView.EXACT)
            if patch is None
            else patch
        )
        if patch is not None and patch.base_fingerprint != head[4]:
            raise StaleJournalBase(
                f"patch base for instance {row[3]!r} differs from stored head "
                f"version {head_seq}; publish without the patch"
            )
        try:
            replayed = transition_patch.apply(head_graph)
        except Refusal as error:
            raise TgdbError(
                f"patch for instance {row[3]!r} did not replay: {error}"
            ) from error
        if replayed != graph:
            raise TgdbError(
                f"patch for instance {row[3]!r} did not reproduce the published graph"
            )
        transition: Literal["patch", "snapshot"] = "patch"
        reason: str | None = None
        patch_digest: str | None
        try:
            patch_document = patch_dumps(transition_patch).encode("utf-8")
        except Refusal as error:
            if error.stage is not RefusalStage.ENVELOPE:
                raise TgdbError(
                    f"patch for instance {row[3]!r} cannot be stored: {error}"
                ) from error
            transition = "snapshot"
            reason = "patch-over-limit"
            patch_digest = None
        else:
            patch_digest = self._put_object(io.BytesIO(patch_document))
        digest = self._store_graph_document(graph, document)
        next_seq = head_seq + 1
        version_id = self._insert_version(
            row[0],
            seq=next_seq,
            parent_id=head[6],
            graph_digest=digest,
            patch_digest=patch_digest,
            transition=transition,
            reason=reason,
            annotations=annotations,
            encoded_annotations=encoded_annotations,
        )
        self._connection.execute(
            "UPDATE instances SET head_version = ?, generation = generation + 1 "
            "WHERE id = ?",
            (version_id, row[0]),
        )
        touch = _touch("instance", row[1])
        self._record(
            {
                "action": operation,
                "touches": [touch],
                "uid": row[1].hex(),
                "version_id": version_id,
            },
            {
                "action": "republish",
                "patch_digest": patch_digest,
                "touches": [touch],
                "uid": row[1].hex(),
                "version_id": head[6],
            },
        )
        self._kind = "version"
        self._versions.append(
            VersionChange(
                row[1],
                row[3],
                next_seq,
                digest,
                "published",
                transition,
                reason,
            )
        )

    def _checked_head(
        self,
        row: tuple[int, bytes, int, str, int, int, int | None, int | None],
        expected: int,
    ) -> tuple[
        int,
        int,
        str,
        str,
        str,
        str | None,
        int,
        int | None,
        str | None,
        Literal["initial", "patch", "snapshot"],
        str | None,
    ]:
        """Return the current head after checking its public sequence."""
        head = _version_row(self._connection, row[0], head_version=row[7], seq=None)
        if head is None:
            raise StoreCorrupt(f"instance {row[3]!r} has no stored head version")
        if head[0] != expected:
            raise StaleVersion(
                f"instance {row[3]!r} expected version {expected} but head is "
                f"version {head[0]}"
            )
        return head

    def _load_graph_digest(self, digest: str) -> Graph:
        """Load one graph object by its already validated catalog digest."""
        connection = (
            self._connection
            if self._store._object_record(digest, connection=self._connection)
            is not None
            else self._store._connection
        )
        with self._store._open_object(digest, connection=connection) as source:
            return loads(source.read())

    def _load_patch_digest(self, digest: str) -> Patch:
        """Load one patch from staged content or the pinned catalog base."""
        connection = (
            self._connection
            if self._store._object_record(digest, connection=self._connection)
            is not None
            else self._store._connection
        )
        return self._store._load_patch_digest(digest, connection=connection)

    def _put_object(self, source: BinaryIO) -> str:
        """Stage one object and remember the only row commit may need to copy."""
        digest = self._store._put_object(source, connection=self._connection)
        self._staged_objects.add(digest)
        return digest

    def put_blob(
        self,
        source: BinaryIO,
        *,
        expected: BlobRef | None = None,
    ) -> BlobRef:
        """Stream one payload into the content pool and verify its identity.

        Memory use is bounded by the store's inline threshold plus one read
        chunk. A mismatching ``expected`` identity refuses the transaction.
        Payload interpretation remains in graph blob items, never in the
        shared object row.
        """
        self._require_active()
        if expected is not None and not isinstance(expected, BlobRef):
            raise TypeError("expected must be a BlobRef or None")
        digest = self._put_object(source)
        record = self._store._object_record(digest, connection=self._connection)
        if record is None:
            raise StoreCorrupt(f"staged object {digest} is missing")
        ref = BlobRef(digest, record[0])
        if expected is not None and ref != expected:
            raise TgdbError(
                "blob identity mismatch: expected "
                f"{expected.sha256} ({expected.size} bytes), read "
                f"{ref.sha256} ({ref.size} bytes)"
            )
        self._operations.append("put_blob")
        if self._kind == "catalog":
            self._kind = "blob"
        return ref

    def import_bundle(
        self,
        source: BinaryIO,
        *,
        collection: bytes | str,
        name: str,
        resolver: BlobResolver | None = None,
        allow_missing: bool = False,
        on_conflict: Literal["refuse", "new-version"] = "refuse",
    ) -> bytes:
        """Stage one validated bundle and return its stable instance id.

        Embedded payloads and explicitly resolved links enter the shared object
        pool. Unresolved links refuse by default; ``allow_missing=True`` keeps
        them discoverable through :meth:`Query.missing_blob`. A conflicting
        name is never changed unless ``on_conflict="new-version"`` is explicit.
        """
        return self._import_bundle(
            source,
            collection=collection,
            name=name,
            resolver=resolver,
            allow_missing=allow_missing,
            on_conflict=on_conflict,
            uid=None,
            position=None,
            expected_digest=None,
            expected_functional=None,
        )

    def import_collection(
        self,
        directory: str | PathLike[str],
        *,
        resolver: BlobResolver | None = None,
        allow_missing: bool = False,
    ) -> tuple[bytes, ...]:
        """Stage every head in a collection export in one transaction.

        The closed collection index supplies stable collection and instance
        ids. Its instance array is the declared order, and each bundle must
        match the recorded head digest and functional fingerprint.
        """
        self._require_active()
        if not isinstance(allow_missing, bool):
            raise TypeError("allow_missing must be a boolean")
        root = Path(directory)
        if root.is_symlink() or not root.is_dir():
            raise TgdbError(
                f"collection import path {str(root)!r} is not a plain directory"
            )
        collection_data, instance_data = _read_collection_index(
            root / _COLLECTION_INDEX_NAME
        )
        expected_names = {
            _COLLECTION_INDEX_NAME,
            *(uid.hex() + _COLLECTION_BUNDLE_SUFFIX for uid, *_rest in instance_data),
        }
        try:
            actual_names = {entry.name for entry in root.iterdir()}
        except OSError as error:
            raise TgdbError(
                f"cannot list collection import directory: {error}"
            ) from error
        if actual_names != expected_names:
            unexpected = sorted(actual_names - expected_names)
            missing = sorted(expected_names - actual_names)
            detail = (
                f"unexpected entries {unexpected!r}"
                if unexpected
                else f"missing entries {missing!r}"
            )
            raise TgdbError(f"collection import directory has {detail}")
        collection_uid, collection_name, collection_position = collection_data
        created_collection = self.create_collection(
            collection_name,
            uid=collection_uid,
            position=collection_position,
        )
        imported: list[bytes] = []
        for uid, name, position, head_digest, functional in instance_data:
            bundle_path = root / (uid.hex() + _COLLECTION_BUNDLE_SUFFIX)
            if bundle_path.is_symlink() or not bundle_path.is_file():
                raise TgdbError(
                    f"collection bundle {bundle_path.name!r} is not a plain file"
                )
            try:
                with bundle_path.open("rb") as source:
                    imported.append(
                        self._import_bundle(
                            source,
                            collection=created_collection,
                            name=name,
                            resolver=resolver,
                            allow_missing=allow_missing,
                            on_conflict="refuse",
                            uid=uid,
                            position=position,
                            expected_digest=head_digest,
                            expected_functional=functional,
                        )
                    )
            except OSError as error:
                raise TgdbError(
                    f"cannot read collection bundle {bundle_path.name!r}: {error}"
                ) from error
        return tuple(imported)

    def _import_bundle(
        self,
        source: BinaryIO,
        *,
        collection: bytes | str,
        name: str,
        resolver: BlobResolver | None,
        allow_missing: bool,
        on_conflict: Literal["refuse", "new-version"],
        uid: bytes | None,
        position: int | None,
        expected_digest: str | None,
        expected_functional: str | None,
    ) -> bytes:
        """Implement single and collection imports through one staged path."""
        self._require_active()
        _validate_name(name)
        if not isinstance(allow_missing, bool):
            raise TypeError("allow_missing must be a boolean")
        if on_conflict not in ("refuse", "new-version"):
            raise ValueError("on_conflict must be 'refuse' or 'new-version'")
        collection_row = _resolve_collection(
            self._connection, collection, include_retired=False
        )
        existing = self._connection.execute(
            "SELECT uid FROM instances WHERE collection_id = ? AND name = ?",
            (collection_row[0], name),
        ).fetchone()
        if existing is not None and on_conflict == "refuse":
            raise TgdbError(f"instance name {name!r} already exists in its collection")
        try:
            with open_bundle(source) as bundle:
                document = _bundle_graph_document(bundle)
                canonical = _graph_document(bundle.graph)
                if document != canonical:
                    label = _bundle_source_label(source)
                    raise TgdbError(
                        f"bundle {label} graph.json is not canonically encoded"
                    )
                digest = hashlib.sha256(document).hexdigest()
                functional = fingerprint(bundle.graph, EquivalenceView.FUNCTIONAL)
                if expected_digest is not None and digest != expected_digest:
                    raise TgdbError(
                        f"collection bundle for {name!r} has graph digest {digest}, "
                        f"not recorded digest {expected_digest}"
                    )
                if (
                    expected_functional is not None
                    and functional != expected_functional
                ):
                    raise TgdbError(
                        f"collection bundle for {name!r} has functional fingerprint "
                        f"{functional}, not recorded fingerprint {expected_functional}"
                    )
                if existing is None:
                    self._import_bundle_payloads(
                        bundle,
                        resolver=resolver,
                        allow_missing=allow_missing,
                    )
                    return self._create_instance(
                        collection_row[1],
                        name,
                        bundle.graph,
                        uid=uid,
                        position=position,
                        annotations=None,
                        document=document,
                    )
                instance_uid = cast(bytes, existing[0])
                row = _resolve_instance(
                    self._connection,
                    instance_uid,
                    collection=collection_row[1],
                )
                _require_active_instance(self._connection, row)
                head = _version_row(
                    self._connection,
                    row[0],
                    head_version=row[7],
                    seq=None,
                )
                if head is None:
                    raise StoreCorrupt(f"instance {name!r} has no stored head version")
                if digest == head[2]:
                    self._import_bundle_payloads(
                        bundle,
                        resolver=resolver,
                        allow_missing=allow_missing,
                        stage=False,
                    )
                    self._publish(
                        "publish",
                        instance_uid,
                        bundle.graph,
                        expected=head[0],
                        collection=collection_row[1],
                        document=document,
                    )
                    return instance_uid
                self._import_bundle_payloads(
                    bundle,
                    resolver=resolver,
                    allow_missing=allow_missing,
                )
                self._publish(
                    "publish",
                    instance_uid,
                    bundle.graph,
                    expected=head[0],
                    collection=collection_row[1],
                    document=document,
                )
                return instance_uid
        except TgdbError:
            raise
        except (OSError, ValueError) as error:
            raise TgdbError(f"cannot import bundle: {error}") from error

    def _import_bundle_payloads(
        self,
        bundle: Bundle,
        *,
        resolver: BlobResolver | None,
        allow_missing: bool,
        stage: bool = True,
    ) -> None:
        """Validate and optionally stage payloads in first declared asset order."""
        seen: set[BlobRef] = set()
        for asset in bundle.assets():
            if asset.ref in seen:
                continue
            seen.add(asset.ref)
            if asset.mode == "embedded":
                with bundle.open_blob(asset.ref) as payload:
                    if stage:
                        self.put_blob(cast(BinaryIO, payload), expected=asset.ref)
                    else:
                        _verify_payload(payload)
                continue
            staged = self._store._object_record(
                asset.ref.sha256, connection=self._connection
            )
            held = (
                staged is not None
                or self._store._object_record(asset.ref.sha256) is not None
            )
            if held:
                continue
            linked_source = (
                None if resolver is None else resolver.open(asset.ref, asset.href)
            )
            if linked_source is None:
                if allow_missing:
                    continue
                raise TgdbError(
                    f"linked blob {asset.ref.sha256} was not found by the resolver"
                )
            with linked_source:
                if stage:
                    self.put_blob(linked_source, expected=asset.ref)
                else:
                    with VerifiedReader(linked_source, asset.ref) as payload:
                        _verify_payload(payload)

    def rename_collection(self, collection: bytes | str, name: str) -> None:
        """Change a collection name without changing its identity or position."""
        self._require_active()
        _validate_name(name)
        row = _resolve_collection(self._connection, collection, include_retired=True)
        if row[2] == name:
            return
        if (
            self._connection.execute(
                "SELECT 1 FROM collections WHERE name = ? AND id != ?", (name, row[0])
            ).fetchone()
            is not None
        ):
            raise TgdbError(f"collection name {name!r} already exists")
        forward = _name_action("collection", row[1], name)
        inverse = _name_action("collection", row[1], row[2])
        self._apply_action(forward)
        self._record(forward, inverse)

    def move_collection(self, collection: bytes | str, position: int) -> None:
        """Move a collection explicitly while preserving every collection id."""
        self._require_active()
        row = _resolve_collection(self._connection, collection, include_retired=True)
        rows = self._connection.execute(
            "SELECT uid, position FROM collections ORDER BY position"
        ).fetchall()
        _validate_position(position, len(rows), allow_end=False)
        if row[3] == position:
            return
        before = {uid: old_position for uid, old_position in rows}
        ordered = [uid for uid, _old_position in rows]
        ordered.remove(row[1])
        ordered.insert(position, row[1])
        after = {uid: index for index, uid in enumerate(ordered)}
        forward = _positions_action("collection", after, before)
        inverse = _positions_action("collection", before, after)
        self._apply_action(forward)
        self._record(forward, inverse)

    def retire_collection(self, collection: bytes | str) -> None:
        """Retire a collection without deleting it or changing its position."""
        self._change_collection_retirement(collection, retire=True)

    def restore_collection(self, collection: bytes | str) -> None:
        """Restore a retired collection at its existing declared position."""
        self._change_collection_retirement(collection, retire=False)

    def rename(
        self,
        instance: bytes | str,
        name: str,
        *,
        collection: bytes | str | None = None,
    ) -> None:
        """Change an instance name without changing identity, position, or head."""
        self._require_active()
        _validate_name(name)
        row = _resolve_instance(self._connection, instance, collection=collection)
        if row[3] == name:
            return
        if (
            self._connection.execute(
                "SELECT 1 FROM instances WHERE collection_id = ? AND name = ? "
                "AND id != ?",
                (row[2], name, row[0]),
            ).fetchone()
            is not None
        ):
            raise TgdbError(f"instance name {name!r} already exists in its collection")
        forward = _name_action("instance", row[1], name)
        inverse = _name_action("instance", row[1], row[3])
        self._apply_action(forward)
        self._record(forward, inverse)

    def move(
        self,
        instance: bytes | str,
        position: int,
        *,
        collection: bytes | str | None = None,
    ) -> None:
        """Move an instance explicitly within its collection."""
        self._require_active()
        row = _resolve_instance(self._connection, instance, collection=collection)
        rows = self._connection.execute(
            "SELECT uid, position FROM instances WHERE collection_id = ? "
            "ORDER BY position",
            (row[2],),
        ).fetchall()
        _validate_position(position, len(rows), allow_end=False)
        if row[4] == position:
            return
        before = {uid: old_position for uid, old_position in rows}
        ordered = [uid for uid, _old_position in rows]
        ordered.remove(row[1])
        ordered.insert(position, row[1])
        after = {uid: index for index, uid in enumerate(ordered)}
        forward = _positions_action("instance", after, before)
        inverse = _positions_action("instance", before, after)
        self._apply_action(forward)
        self._record(forward, inverse)

    def retire(
        self,
        instance: bytes | str,
        *,
        collection: bytes | str | None = None,
    ) -> None:
        """Retire an instance without deleting its identity or position."""
        self._change_instance_retirement(instance, collection=collection, retire=True)

    def restore(
        self,
        instance: bytes | str,
        *,
        collection: bytes | str | None = None,
    ) -> None:
        """Restore a retired instance at its existing declared position."""
        self._change_instance_retirement(instance, collection=collection, retire=False)

    def declare_index(
        self,
        name: str,
        tier: QualifiedName,
        attribute: QualifiedName,
        *,
        history: bool = False,
    ) -> None:
        """Declare and build one ordered per-item attribute index.

        By default only documents that are current instance heads retain rows.
        ``history=True`` indexes every retained version instead. The declaration
        may name a tier or attribute that no stored graph currently uses.
        """
        self._require_active()
        _validate_name(name)
        if not isinstance(tier, QualifiedName):
            raise TypeError("tier must be a QualifiedName")
        if not isinstance(attribute, QualifiedName):
            raise TypeError("attribute must be a QualifiedName")
        if not isinstance(history, bool):
            raise TypeError("history must be a boolean")
        if (
            self._connection.execute(
                "SELECT 1 FROM declared_indexes WHERE name = ?", (name,)
            ).fetchone()
            is not None
        ):
            raise TgdbError(f"item index {name!r} is already declared")
        position = cast(
            int,
            self._connection.execute(
                "SELECT coalesce(max(position) + 1, 0) FROM declared_indexes"
            ).fetchone()[0],
        )
        self._declare_index(name, position, tier, attribute, history)
        action = _index_action(
            "declare_index", name, position, tier, attribute, history
        )
        self._record(action, _drop_index_action(name))

    def drop_index(self, name: str) -> None:
        """Drop one declared item index and its rebuildable value rows."""
        self._require_active()
        _validate_name(name)
        row = self._connection.execute(
            "SELECT position, tier, attribute, history FROM declared_indexes "
            "WHERE name = ?",
            (name,),
        ).fetchone()
        if row is None:
            raise TgdbError(f"item index {name!r} is not declared")
        position, tier_text, attribute_text, history = row
        self._connection.execute("DELETE FROM item_index WHERE index_name = ?", (name,))
        self._connection.execute(
            "DELETE FROM item_index_counts WHERE index_name = ?", (name,)
        )
        self._connection.execute("DELETE FROM declared_indexes WHERE name = ?", (name,))
        self._indexes_changed = True
        self._record(
            _drop_index_action(name),
            _index_action(
                "declare_index",
                name,
                position,
                _qname_from_text(tier_text),
                _qname_from_text(attribute_text),
                bool(history),
            ),
        )

    def _declare_index(
        self,
        name: str,
        position: int,
        tier: QualifiedName,
        attribute: QualifiedName,
        history: bool,
    ) -> None:
        """Insert and fully populate one already validated declaration."""
        if (
            self._connection.execute(
                "SELECT 1 FROM declared_indexes WHERE name = ?", (name,)
            ).fetchone()
            is not None
        ):
            raise StaleVersion(f"item index {name!r} is already declared")
        if (
            self._connection.execute(
                "SELECT 1 FROM declared_indexes WHERE position = ?", (position,)
            ).fetchone()
            is not None
        ):
            raise StaleVersion(
                f"declared item index position {position} is no longer available"
            )
        self._connection.execute(
            "INSERT INTO declared_indexes(name, position, tier, attribute, history) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                name,
                position,
                _qname_text(tier),
                _qname_text(attribute),
                int(history),
            ),
        )
        if history:
            digest_rows = self._connection.execute(
                "SELECT DISTINCT graph_digest FROM versions ORDER BY graph_digest"
            )
        else:
            digest_rows = self._connection.execute(
                "SELECT DISTINCT v.graph_digest FROM instances AS i "
                "JOIN versions AS v ON v.id = i.head_version ORDER BY v.graph_digest"
            )
        declaration = ((name, _qname_text(tier), _qname_text(attribute)),)
        for (digest,) in digest_rows:
            graph = self._load_graph_digest(digest)
            rows = _item_index_rows(graph, digest, declaration)
            self._connection.execute(
                "INSERT INTO item_index_counts(graph_digest, index_name, row_count) "
                "VALUES (?, ?, ?)",
                (digest, name, len(rows)),
            )
            self._connection.executemany(
                "INSERT INTO item_index("
                "graph_digest, index_name, tier_position, item_position, value_type, "
                "value_text, value_num, value_digest) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        self._indexes_changed = True

    def declare_column(
        self,
        name: str,
        media_type: str,
        attribute: QualifiedName,
        value_type: str,
    ) -> None:
        """Declare and build one ordered blob-item metadata column.

        ``media_type`` is an exact lowercase media type or a ``type/*`` family.
        Values come only from stored graph content; declaring a column never
        opens a payload or runs an inspector.
        """
        self._require_active()
        _validate_name(name)
        _validate_column_media_type(media_type)
        if not isinstance(attribute, QualifiedName):
            raise TypeError("attribute must be a QualifiedName")
        _validate_column_value_type(value_type)
        if (
            self._connection.execute(
                "SELECT 1 FROM declared_columns WHERE name = ?", (name,)
            ).fetchone()
            is not None
        ):
            raise TgdbError(f"blob column {name!r} is already declared")
        position = cast(
            int,
            self._connection.execute(
                "SELECT coalesce(max(position) + 1, 0) FROM declared_columns"
            ).fetchone()[0],
        )
        self._declare_column(name, position, media_type, attribute, value_type)
        action = _column_action(
            "declare_column", name, position, media_type, attribute, value_type
        )
        self._record(action, _drop_column_action(name))

    def drop_column(self, name: str) -> None:
        """Drop one declared blob column and its rebuildable metadata rows."""
        self._require_active()
        _validate_name(name)
        row = self._connection.execute(
            "SELECT position, media_type, attribute, value_type "
            "FROM declared_columns WHERE name = ?",
            (name,),
        ).fetchone()
        if row is None:
            raise TgdbError(f"blob column {name!r} is not declared")
        position, media_type, attribute_text, value_type = row
        self._connection.execute("DELETE FROM blob_metadata WHERE column = ?", (name,))
        self._connection.execute(
            "DELETE FROM blob_metadata_counts WHERE column = ?", (name,)
        )
        self._connection.execute("DELETE FROM declared_columns WHERE name = ?", (name,))
        self._indexes_changed = True
        self._record(
            _drop_column_action(name),
            _column_action(
                "declare_column",
                name,
                position,
                media_type,
                _qname_from_text(attribute_text),
                value_type,
            ),
        )

    def _declare_column(
        self,
        name: str,
        position: int,
        media_type: str,
        attribute: QualifiedName,
        value_type: str,
    ) -> None:
        """Insert and fully populate one validated blob-column declaration."""
        if (
            self._connection.execute(
                "SELECT 1 FROM declared_columns WHERE name = ?", (name,)
            ).fetchone()
            is not None
        ):
            raise StaleVersion(f"blob column {name!r} is already declared")
        if (
            self._connection.execute(
                "SELECT 1 FROM declared_columns WHERE position = ?", (position,)
            ).fetchone()
            is not None
        ):
            raise StaleVersion(
                f"declared blob column position {position} is no longer available"
            )
        declaration = ((name, media_type, _qname_text(attribute), value_type),)
        derived: list[tuple[str, tuple[_BlobMetadataRow, ...]]] = []
        for (digest,) in self._connection.execute(
            "SELECT graph_digest FROM versions UNION SELECT digest FROM inspections "
            "WHERE inspector = ? AND inspector_version = ? ORDER BY 1",
            (_TIERGRAPH_INSPECTOR_NAME, _TIERGRAPH_INSPECTOR_VERSION),
        ):
            graph = self._load_graph_digest(digest)
            try:
                blob_rows, _attachments = _blob_index_rows(graph, digest)
                rows = _blob_metadata_rows(graph, digest, blob_rows, declaration)
            except ValueError as error:
                raise TgdbError(
                    f"cannot index blob column {name!r} for graph {digest}: {error}"
                ) from error
            derived.append((digest, rows))
        self._connection.execute(
            "INSERT INTO declared_columns("
            "name, position, media_type, attribute, value_type) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, position, media_type, _qname_text(attribute), value_type),
        )
        for digest, rows in derived:
            self._connection.executemany(
                "INSERT INTO blob_metadata_counts("
                "graph_digest, column, row_count, row_digest) VALUES (?, ?, ?, ?)",
                _blob_metadata_count_rows(digest, declaration, rows),
            )
            self._connection.executemany(
                "INSERT INTO blob_metadata("
                "graph_digest, tier, position, column, value_text, value_num, "
                "source) VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        self._indexes_changed = True

    def _store_graph(self, graph: Graph) -> str:
        """Store one canonical graph document and its small derived facts."""
        return self._store_graph_document(graph, _graph_document(graph))

    def _store_graph_document(self, graph: Graph, document: bytes) -> str:
        """Store already serialized graph bytes and their verified fingerprints."""
        digest = self._put_object(io.BytesIO(document))
        functional = fingerprint(graph, EquivalenceView.FUNCTIONAL)
        identified = fingerprint(graph, EquivalenceView.IDENTIFIED)
        try:
            blob_rows, attachment_rows = _blob_index_rows(graph, digest)
        except ValueError as error:
            raise TgdbError(f"graph blob profile is invalid: {error}") from error
        self._connection.execute(
            "INSERT OR IGNORE INTO graph_facts("
            "digest, format_version, fp_domain, functional, identified, size, "
            "tier_count, blob_count, attachment_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                digest,
                FORMAT_VERSION,
                self._store._info.fingerprint_domain,
                functional,
                identified,
                len(document),
                len(graph.tiers),
                len(blob_rows),
                len(attachment_rows),
            ),
        )
        recorded = self._connection.execute(
            "SELECT format_version, fp_domain, functional, identified, size, "
            "tier_count, blob_count, attachment_count FROM graph_facts "
            "WHERE digest = ?",
            (digest,),
        ).fetchone()
        expected = (
            FORMAT_VERSION,
            self._store._info.fingerprint_domain,
            functional,
            identified,
            len(document),
            len(graph.tiers),
            len(blob_rows),
            len(attachment_rows),
        )
        if recorded != expected:
            raise StoreCorrupt(f"graph facts for object {digest} are inconsistent")
        tier_rows = tuple(
            (digest, position, _qname_text(tier.declaration.name), len(tier.items))
            for position, tier in enumerate(graph.tiers)
        )
        self._connection.executemany(
            "INSERT OR IGNORE INTO graph_tiers("
            "digest, position, tier, item_count) VALUES (?, ?, ?, ?)",
            tier_rows,
        )
        recorded_tiers = tuple(
            self._connection.execute(
                "SELECT digest, position, tier, item_count FROM graph_tiers "
                "WHERE digest = ? ORDER BY position",
                (digest,),
            )
        )
        if recorded_tiers != tier_rows:
            raise StoreCorrupt(f"graph tiers for object {digest} are inconsistent")
        self._index_blobs(graph, digest, blob_rows, attachment_rows)
        self._index_graph(graph, digest)
        self._staged_graphs.add(digest)
        return digest

    def _index_blobs(
        self,
        graph: Graph,
        digest: str,
        blob_rows: tuple[_BlobItemRow, ...],
        attachment_rows: tuple[_AttachmentRow, ...],
    ) -> None:
        """Replace one graph's prevalidated blob-item and attachment facts."""
        self._connection.execute(
            "DELETE FROM attachments WHERE graph_digest = ?", (digest,)
        )
        self._connection.execute(
            "DELETE FROM blob_items WHERE graph_digest = ?", (digest,)
        )
        self._connection.execute(
            "DELETE FROM blob_metadata WHERE graph_digest = ?", (digest,)
        )
        self._connection.execute(
            "DELETE FROM blob_metadata_counts WHERE graph_digest = ?", (digest,)
        )
        self._connection.executemany(
            "INSERT INTO blob_items("
            "graph_digest, tier, position, durable_id, sha256, size, media_type, "
            "schema) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            blob_rows,
        )
        self._connection.executemany(
            "INSERT INTO attachments("
            "graph_digest, ordinal, declaration, durable_id, subject, blob_tier, "
            "blob_position, span_unit, span_offset, span_length, span_path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            attachment_rows,
        )
        declarations = tuple(
            cast(tuple[str, str, str, str], row)
            for row in self._connection.execute(
                "SELECT name, media_type, attribute, value_type "
                "FROM declared_columns ORDER BY position"
            )
        )
        try:
            metadata_rows = _blob_metadata_rows(graph, digest, blob_rows, declarations)
        except ValueError as error:
            raise TgdbError(
                f"cannot index blob metadata for graph {digest}: {error}"
            ) from error
        self._connection.executemany(
            "INSERT INTO blob_metadata("
            "graph_digest, tier, position, column, value_text, value_num, "
            "source) VALUES (?, ?, ?, ?, ?, ?, ?)",
            metadata_rows,
        )
        self._connection.executemany(
            "INSERT INTO blob_metadata_counts("
            "graph_digest, column, row_count, row_digest) VALUES (?, ?, ?, ?)",
            _blob_metadata_count_rows(digest, declarations, metadata_rows),
        )

    def _index_graph(self, graph: Graph, digest: str) -> None:
        """Replace this graph's rows for every current declaration."""
        declarations = tuple(
            cast(tuple[str, str, str], row)
            for row in self._connection.execute(
                "SELECT name, tier, attribute FROM declared_indexes ORDER BY position"
            )
        )
        self._connection.execute(
            "DELETE FROM item_index WHERE graph_digest = ?", (digest,)
        )
        self._connection.execute(
            "DELETE FROM item_index_counts WHERE graph_digest = ?", (digest,)
        )
        rows = _item_index_rows(graph, digest, declarations)
        self._connection.executemany(
            "INSERT INTO item_index_counts(graph_digest, index_name, row_count) "
            "VALUES (?, ?, ?)",
            _item_index_count_rows(digest, declarations, rows),
        )
        self._connection.executemany(
            "INSERT INTO item_index("
            "graph_digest, index_name, tier_position, item_position, value_type, "
            "value_text, value_num, value_digest) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )

    def _changed_head_digests(self) -> set[str]:
        """Return prior head digests displaced by this commit's new versions."""
        return {
            cast(str, row[0])
            for row in self._connection.execute(
                "SELECT DISTINCT parent.graph_digest FROM versions AS current "
                "JOIN versions AS parent ON parent.id = current.parent_id "
                "WHERE current.commit_seq = ?",
                (self._commit_seq,),
            )
        }

    def _insert_version(
        self,
        instance_id: int,
        *,
        seq: int,
        parent_id: int | None,
        graph_digest: str,
        patch_digest: str | None,
        transition: Literal["initial", "patch", "snapshot"],
        reason: str | None,
        annotations: EditAnnotations | None,
        encoded_annotations: str | None,
    ) -> int:
        """Insert one complete document version and return its internal row id."""
        cursor = self._connection.execute(
            "INSERT INTO versions("
            "instance_id, seq, parent_id, commit_seq, graph_digest, patch_digest, "
            "transition, reason, stage, iteration, annotations"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                instance_id,
                seq,
                parent_id,
                self._commit_seq,
                graph_digest,
                patch_digest,
                transition,
                reason,
                None if annotations is None else annotations.stage,
                None if annotations is None else annotations.iteration,
                encoded_annotations,
            ),
        )
        return cast(int, cursor.lastrowid)

    def commit(self) -> CommitReceipt:
        """Publish every staged change atomically after checking the base view."""
        self._require_active()
        if not self._operations:
            versions = tuple(self._versions)
            self.discard()
            if versions:
                return CommitReceipt(None, "unchanged", (), versions)
            raise TgdbError("write transaction has no changes")
        try:
            changed_head_digests = self._changed_head_digests()
            if not self._indexes_changed:
                _prune_head_indexes(self._connection, changed_head_digests)
            self._connection.execute(
                "UPDATE commits SET kind = ? WHERE seq = ?",
                (self._kind, self._commit_seq),
            )
            self._connection.execute("COMMIT")
            catalog = self._store._connection
            catalog.execute("BEGIN IMMEDIATE")
            _publication_step("locked")
            current = catalog.execute("SELECT max(seq) FROM commits").fetchone()
            current_seq = 0 if current is None or current[0] is None else current[0]
            if current_seq != self._base_commit_seq:
                raise StaleVersion(
                    "write transaction is stale because the catalog advanced from "
                    f"commit {self._base_commit_seq} to commit {current_seq}"
                )
            _publish_staged_catalog(
                self._connection,
                catalog,
                self._commit_seq,
                self._staged_objects,
                self._staged_graphs,
                indexes_changed=self._indexes_changed,
                changed_head_digests=changed_head_digests,
            )
            catalog.execute("COMMIT")
        except sqlite3.OperationalError as error:
            if self._store._connection.in_transaction:
                self._store._connection.execute("ROLLBACK")
            self._finish()
            if _sqlite_busy(error):
                raise StoreBusy(
                    "tgdb catalog remained busy past its timeout"
                ) from error
            raise StoreCorrupt(f"cannot commit catalog transaction: {error}") from error
        except sqlite3.DatabaseError as error:
            if self._store._connection.in_transaction:
                self._store._connection.execute("ROLLBACK")
            self._finish()
            raise StoreCorrupt(f"cannot commit catalog transaction: {error}") from error
        except Exception:
            if self._store._connection.in_transaction:
                self._store._connection.execute("ROLLBACK")
            self._finish()
            raise
        receipt = CommitReceipt(
            self._commit_seq,
            self._kind,
            tuple(self._operations),
            tuple(self._versions),
        )
        self._finish()
        return receipt

    def discard(self) -> None:
        """Roll back every change; repeated calls have no effect."""
        if self._active:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            self._finish()

    def _change_collection_retirement(
        self, collection: bytes | str, *, retire: bool
    ) -> None:
        """Apply one checked collection lifecycle transition."""
        self._require_active()
        row = _resolve_collection(self._connection, collection, include_retired=True)
        if retire == (row[4] is not None):
            state = "retired" if retire else "active"
            raise TgdbError(f"collection {row[2]!r} is already {state}")
        action = "retire_collection" if retire else "restore_collection"
        opposite = "restore_collection" if retire else "retire_collection"
        touch = _touch("collection", row[1])
        forward: dict[str, object] = {
            "action": action,
            "touches": [touch],
            "uid": row[1].hex(),
        }
        inverse: dict[str, object] = {
            "action": opposite,
            "touches": [touch],
            "uid": row[1].hex(),
        }
        self._apply_action(forward)
        self._record(forward, inverse)

    def _change_instance_retirement(
        self,
        instance: bytes | str,
        *,
        collection: bytes | str | None,
        retire: bool,
    ) -> None:
        """Apply one checked instance lifecycle transition."""
        self._require_active()
        row = _resolve_instance(self._connection, instance, collection=collection)
        if retire == (row[6] is not None):
            state = "retired" if retire else "active"
            raise TgdbError(f"instance {row[3]!r} is already {state}")
        action = "retire_instance" if retire else "restore_instance"
        opposite = "restore_instance" if retire else "retire_instance"
        touch = _touch("instance", row[1])
        forward: dict[str, object] = {
            "action": action,
            "touches": [touch],
            "uid": row[1].hex(),
        }
        inverse: dict[str, object] = {
            "action": opposite,
            "touches": [touch],
            "uid": row[1].hex(),
        }
        self._apply_action(forward)
        self._record(forward, inverse)

    def _undo(self, commit_seq: int) -> None:
        """Stage the checked inverse of one earlier commit."""
        self._require_active()
        commit = self._connection.execute(
            "SELECT kind FROM commits WHERE seq = ?", (commit_seq,)
        ).fetchone()
        if commit is None or commit_seq == self._commit_seq:
            raise TgdbError(f"commit {commit_seq} does not exist")
        rows = self._connection.execute(
            "SELECT forward, inverse FROM commit_ops WHERE commit_seq = ? "
            "ORDER BY ordinal",
            (commit_seq,),
        ).fetchall()
        if not rows:
            raise TgdbError(f"commit {commit_seq} has no undoable operations")
        original: list[tuple[dict[str, object], dict[str, object]]] = []
        touches: set[str] = set()
        for forward_text, inverse_text in rows:
            forward = _decode_action(forward_text)
            inverse = _decode_action(inverse_text)
            original.append((forward, inverse))
            touches.update(_action_touches(forward))
        later = self._connection.execute(
            "SELECT commit_seq, forward FROM commit_ops "
            "WHERE commit_seq > ? AND commit_seq < ? ORDER BY commit_seq, ordinal",
            (commit_seq, self._commit_seq),
        )
        for later_seq, forward_text in later:
            if touches.intersection(_action_touches(_decode_action(forward_text))):
                raise StaleVersion(
                    f"commit {commit_seq} is stale because commit {later_seq} "
                    "touched the same catalog identity"
                )
        self._kind = "undo"
        for _forward, inverse in reversed(original):
            opposite = self._apply_action(inverse)
            self._record(inverse, opposite)

    def _apply_action(  # noqa: PLR0915
        self, action: dict[str, object]
    ) -> dict[str, object]:
        """Apply one bounded internal action and return its current inverse."""
        kind = action.get("action")
        touches = list(_action_touches(action))
        if kind == "declare_index":
            name, position, tier, attribute, history = _index_action_data(action)
            self._declare_index(name, position, tier, attribute, history)
            return _drop_index_action(name)
        if kind == "drop_index":
            index_name = action.get("name")
            _validate_name(index_name)
            index_name = cast(str, index_name)
            row = self._connection.execute(
                "SELECT position, tier, attribute, history FROM declared_indexes "
                "WHERE name = ?",
                (index_name,),
            ).fetchone()
            if row is None:
                raise StaleVersion(f"item index {index_name!r} is no longer declared")
            position, tier_text, attribute_text, history = row
            self._connection.execute(
                "DELETE FROM item_index WHERE index_name = ?", (index_name,)
            )
            self._connection.execute(
                "DELETE FROM item_index_counts WHERE index_name = ?", (index_name,)
            )
            self._connection.execute(
                "DELETE FROM declared_indexes WHERE name = ?", (index_name,)
            )
            self._indexes_changed = True
            return _index_action(
                "declare_index",
                index_name,
                position,
                _qname_from_text(tier_text),
                _qname_from_text(attribute_text),
                bool(history),
            )
        if kind == "declare_column":
            name, position, media_type, attribute, value_type = _column_action_data(
                action
            )
            self._declare_column(name, position, media_type, attribute, value_type)
            return _drop_column_action(name)
        if kind == "drop_column":
            column_name = action.get("name")
            _validate_name(column_name)
            column_name = cast(str, column_name)
            row = self._connection.execute(
                "SELECT position, media_type, attribute, value_type "
                "FROM declared_columns WHERE name = ?",
                (column_name,),
            ).fetchone()
            if row is None:
                raise StaleVersion(f"blob column {column_name!r} is no longer declared")
            position, media_type, attribute_text, value_type = row
            self._connection.execute(
                "DELETE FROM blob_metadata WHERE column = ?", (column_name,)
            )
            self._connection.execute(
                "DELETE FROM blob_metadata_counts WHERE column = ?", (column_name,)
            )
            self._connection.execute(
                "DELETE FROM declared_columns WHERE name = ?", (column_name,)
            )
            self._indexes_changed = True
            return _column_action(
                "declare_column",
                column_name,
                position,
                media_type,
                _qname_from_text(attribute_text),
                value_type,
            )
        if kind == "republish":
            uid = _action_uid(action)
            version_id = cast(int, action["version_id"])
            patch_digest = cast(str | None, action["patch_digest"])
            row = self._connection.execute(
                "SELECT id, uid, collection_id, name, position, generation, "
                "retired_commit, head_version FROM instances WHERE uid = ?",
                (uid,),
            ).fetchone()
            if row is None:
                raise StoreCorrupt("recorded instance identity is missing")
            instance = cast(
                tuple[int, bytes, int, str, int, int, int | None, int | None], row
            )
            _require_active_instance(self._connection, instance)
            current = _version_row(
                self._connection,
                instance[0],
                head_version=instance[7],
                seq=None,
            )
            target = self._connection.execute(
                "SELECT graph_digest FROM versions WHERE id = ? AND instance_id = ?",
                (version_id, instance[0]),
            ).fetchone()
            if current is None or target is None:
                raise StoreCorrupt("recorded version identity is missing")
            stored_patch: Patch | None = None
            stored_patch_digest: str | None = None
            transition: Literal["patch", "snapshot"] = "snapshot"
            reason: str | None = "undo-snapshot"
            if patch_digest is not None:
                stored_patch = invert_patch(self._load_patch_digest(patch_digest))
                current_graph = self._load_graph_digest(current[2])
                target_graph = self._load_graph_digest(target[0])
                try:
                    replayed = stored_patch.apply(current_graph)
                except Refusal as error:
                    raise StoreCorrupt(
                        f"recorded inverse patch for instance {instance[3]!r} "
                        f"did not replay: {error}"
                    ) from error
                if replayed != target_graph:
                    raise StoreCorrupt(
                        f"recorded inverse patch for instance {instance[3]!r} "
                        "did not reproduce its target"
                    )
                stored_patch_digest = self._put_object(
                    io.BytesIO(patch_dumps(stored_patch).encode("utf-8")),
                )
                transition = "patch"
                reason = None
            next_seq = current[0] + 1
            self._index_graph(self._load_graph_digest(target[0]), target[0])
            self._staged_graphs.add(target[0])
            new_id = self._insert_version(
                instance[0],
                seq=next_seq,
                parent_id=current[6],
                graph_digest=target[0],
                patch_digest=stored_patch_digest,
                transition=transition,
                reason=reason,
                annotations=None,
                encoded_annotations=None,
            )
            self._connection.execute(
                "UPDATE instances SET head_version = ?, generation = generation + 1 "
                "WHERE id = ?",
                (new_id, instance[0]),
            )
            self._versions.append(
                VersionChange(
                    uid,
                    instance[3],
                    next_seq,
                    target[0],
                    "published",
                    transition,
                    reason,
                )
            )
            return {
                "action": "republish",
                "patch_digest": stored_patch_digest,
                "touches": touches,
                "uid": uid.hex(),
                "version_id": current[6],
            }
        if kind in {"rename_collection", "rename_instance"}:
            uid = _action_uid(action)
            renamed = action.get("name")
            _validate_name(renamed)
            renamed = cast(str, renamed)
            rename_table = "collections" if kind == "rename_collection" else "instances"
            columns = (
                "id, name"
                if rename_table == "collections"
                else "id, name, collection_id"
            )
            row = self._connection.execute(
                f"SELECT {columns} FROM {rename_table} WHERE uid = ?", (uid,)
            ).fetchone()
            if row is None:
                raise StoreCorrupt(f"recorded {rename_table[:-1]} identity is missing")
            if rename_table == "collections":
                conflict = self._connection.execute(
                    "SELECT 1 FROM collections WHERE name = ? AND id != ?",
                    (renamed, row[0]),
                ).fetchone()
            else:
                conflict = self._connection.execute(
                    "SELECT 1 FROM instances WHERE collection_id = ? AND name = ? "
                    "AND id != ?",
                    (row[2], renamed, row[0]),
                ).fetchone()
            if conflict is not None:
                raise StaleVersion(
                    f"recorded {rename_table[:-1]} name {renamed!r} is no longer available"
                )
            self._connection.execute(
                f"UPDATE {rename_table} SET name = ? WHERE uid = ?", (renamed, uid)
            )
            if rename_table == "instances":
                self._connection.execute(
                    "UPDATE instances SET generation = generation + 1 WHERE uid = ?",
                    (uid,),
                )
            return {
                "action": kind,
                "name": row[1],
                "touches": touches,
                "uid": uid.hex(),
            }
        if kind in {"retire_collection", "restore_collection"}:
            uid = _action_uid(action)
            retiring = kind == "retire_collection"
            row = self._connection.execute(
                "SELECT retired_commit FROM collections WHERE uid = ?", (uid,)
            ).fetchone()
            if row is None:
                raise StoreCorrupt("recorded collection identity is missing")
            expected_retired = not retiring
            if (row[0] is not None) != expected_retired:
                raise StaleVersion("recorded collection lifecycle state is stale")
            self._connection.execute(
                "UPDATE collections SET retired_commit = ? WHERE uid = ?",
                (self._commit_seq if retiring else None, uid),
            )
            opposite = "restore_collection" if retiring else "retire_collection"
            return {"action": opposite, "touches": touches, "uid": uid.hex()}
        if kind in {"retire_instance", "restore_instance"}:
            uid = _action_uid(action)
            retiring = kind == "retire_instance"
            row = self._connection.execute(
                "SELECT retired_commit FROM instances WHERE uid = ?", (uid,)
            ).fetchone()
            if row is None:
                raise StoreCorrupt("recorded instance identity is missing")
            expected_retired = not retiring
            if (row[0] is not None) != expected_retired:
                raise StaleVersion("recorded instance lifecycle state is stale")
            self._connection.execute(
                "UPDATE instances SET retired_commit = ?, generation = generation + 1 "
                "WHERE uid = ?",
                (self._commit_seq if retiring else None, uid),
            )
            opposite = "restore_instance" if retiring else "retire_instance"
            return {"action": opposite, "touches": touches, "uid": uid.hex()}
        if kind in {"set_collection_positions", "set_instance_positions"}:
            positions_table: Literal["collections", "instances"] = (
                "collections" if kind == "set_collection_positions" else "instances"
            )
            raw_positions = cast(dict[str, object], action["positions"])
            positions: dict[bytes, int] = {}
            previous: dict[bytes, int] = {}
            for uid_text, moved_position in raw_positions.items():
                item_uid = _uid_from_hex(uid_text)
                if (
                    isinstance(moved_position, bool)
                    or not isinstance(moved_position, int)
                    or moved_position < 0
                ):
                    raise StoreCorrupt("recorded catalog position is malformed")
                row = self._connection.execute(
                    f"SELECT position FROM {positions_table} WHERE uid = ?", (item_uid,)
                ).fetchone()
                if row is None:
                    raise StoreCorrupt(
                        f"recorded {positions_table[:-1]} identity is missing"
                    )
                positions[item_uid] = moved_position
                previous[item_uid] = row[0]
            _set_positions(self._connection, positions_table, positions)
            if positions_table == "instances":
                self._connection.executemany(
                    "UPDATE instances SET generation = generation + 1 WHERE uid = ?",
                    ((item_uid,) for item_uid in positions),
                )
            item_kind: Literal["collection", "instance"] = (
                "collection" if positions_table == "collections" else "instance"
            )
            return _positions_action(item_kind, previous, positions)
        raise StoreCorrupt(f"unknown catalog operation {kind!r}")

    def _record(self, forward: dict[str, object], inverse: dict[str, object]) -> None:
        """Append one forward/inverse pair in its declared operation order."""
        ordinal = len(self._operations)
        operation = forward.get("action")
        if not isinstance(operation, str):
            raise StoreCorrupt("catalog operation has no action")
        self._connection.execute(
            "INSERT INTO commit_ops(commit_seq, ordinal, op, forward, inverse) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                self._commit_seq,
                ordinal,
                operation,
                _encode_action(forward),
                _encode_action(inverse),
            ),
        )
        self._operations.append(operation)

    def _require_active(self) -> None:
        """Refuse use after commit, discard, or owning-store close."""
        if not self._active or self._store.closed:
            raise TgdbError("write transaction is closed")

    def _finish(self) -> None:
        """Release this transaction from its owning store."""
        self._active = False
        self._connection.close()
        _remove_staged_catalog(self._stage_path)
        if self._store._writer is self:
            self._store._writer = None

    def __enter__(self) -> Self:
        """Return this active transaction without making commit implicit."""
        self._require_active()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Discard uncommitted work whenever the context exits."""
        self.discard()


def _publication_step(step: str) -> None:
    """Mark one precommit publication boundary for deterministic fault tests."""
    _ = step


def _copy_catalog_metadata(
    catalog: sqlite3.Connection,
    staged: sqlite3.Connection,
    *,
    batch_size: int,
) -> int:
    """Copy a stable catalog view without copying stored object payloads."""
    tables = tuple(
        cast(tuple[str, str], row)
        for row in catalog.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY rowid"
        )
    )
    indexes = tuple(
        cast(str, row[0])
        for row in catalog.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'index' AND sql IS NOT NULL ORDER BY rowid"
        )
    )
    catalog.execute("SAVEPOINT tgdb_stage_view")
    try:
        base = catalog.execute("SELECT coalesce(max(seq), 0) FROM commits").fetchone()
        for _name, sql in tables:
            staged.execute(sql)
        staged.execute("BEGIN")
        try:
            for name, _sql in tables:
                if name == "objects":
                    continue
                columns = tuple(
                    cast(str, row[1])
                    for row in catalog.execute(f"PRAGMA table_info({name})")
                )
                names = ", ".join(columns)
                placeholders = ", ".join("?" for _column in columns)
                rows = catalog.execute(f"SELECT {names} FROM {name}")
                while batch := rows.fetchmany(batch_size):
                    staged.executemany(
                        f"INSERT INTO {name}({names}) VALUES ({placeholders})", batch
                    )
            staged.execute("COMMIT")
        except Exception:
            staged.execute("ROLLBACK")
            raise
        for sql in indexes:
            staged.execute(sql)
    finally:
        catalog.execute("ROLLBACK TO tgdb_stage_view")
        catalog.execute("RELEASE tgdb_stage_view")
    return cast(int, base[0])


def _publish_staged_catalog(
    staged: sqlite3.Connection,
    catalog: sqlite3.Connection,
    commit_seq: int,
    object_digests: set[str],
    graph_digests: set[str],
    *,
    indexes_changed: bool,
    changed_head_digests: set[str],
) -> None:
    """Copy one staged catalog delta into a locked live catalog in dependency order."""
    commit = staged.execute(
        "SELECT seq, kind, annotations FROM commits WHERE seq = ?", (commit_seq,)
    ).fetchone()
    if commit is None:
        raise StoreCorrupt("staged commit row is missing")
    catalog.execute(
        "INSERT INTO commits(seq, kind, annotations) VALUES (?, ?, ?)", commit
    )
    _publication_step("commit-row")

    _copy_selected_rows(
        staged,
        catalog,
        "objects",
        ("digest", "size", "residency", "data"),
        "digest",
        object_digests,
    )
    _publication_step("objects")

    _merge_collections(staged, catalog)
    _merge_instance_shells(staged, catalog)
    _publication_step("catalog")

    _copy_selected_rows(
        staged,
        catalog,
        "graph_facts",
        (
            "digest",
            "format_version",
            "fp_domain",
            "functional",
            "identified",
            "size",
            "tier_count",
            "blob_count",
            "attachment_count",
        ),
        "digest",
        graph_digests,
    )
    _copy_graph_tiers(staged, catalog, graph_digests)
    _copy_blob_indexes(staged, catalog, graph_digests)
    if indexes_changed:
        _replace_declared_indexes(staged, catalog)
    else:
        _copy_item_indexes(staged, catalog, graph_digests)
    _publication_step("derived-rows")

    version_columns = (
        "id",
        "instance_id",
        "seq",
        "parent_id",
        "commit_seq",
        "graph_digest",
        "patch_digest",
        "transition",
        "reason",
        "stage",
        "iteration",
        "annotations",
    )
    version_names = ", ".join(version_columns)
    version_rows = staged.execute(
        f"SELECT {version_names} FROM versions WHERE commit_seq = ? ORDER BY id",
        (commit_seq,),
    ).fetchall()
    catalog.executemany(
        f"INSERT INTO versions({version_names}) VALUES ("
        + ", ".join("?" for _column in version_columns)
        + ")",
        version_rows,
    )
    _publication_step("versions")

    _merge_instance_heads(staged, catalog)
    if not indexes_changed:
        _prune_head_indexes(catalog, changed_head_digests)
    _publication_step("heads")

    rows = staged.execute(
        "SELECT commit_seq, ordinal, op, forward, inverse FROM commit_ops "
        "WHERE commit_seq = ? ORDER BY ordinal",
        (commit_seq,),
    ).fetchall()
    catalog.executemany(
        "INSERT INTO commit_ops(commit_seq, ordinal, op, forward, inverse) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    _publication_step("operations")


def _copy_selected_rows(
    staged: sqlite3.Connection,
    catalog: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
    key: str,
    keys: set[str],
) -> None:
    """Copy selected rows absent from the catalog, verifying existing rows."""
    names = ", ".join(columns)
    placeholders = ", ".join("?" for _column in columns)
    key_index = columns.index(key)
    for selected in sorted(keys):
        row = staged.execute(
            f"SELECT {names} FROM {table} WHERE {key} = ?", (selected,)
        ).fetchone()
        if row is None:
            raise StoreCorrupt(f"staged {table} row {selected!r} is missing")
        existing = catalog.execute(
            f"SELECT {names} FROM {table} WHERE {key} = ?",
            (row[key_index],),
        ).fetchone()
        if existing is None:
            catalog.execute(
                f"INSERT INTO {table}({names}) VALUES ({placeholders})",
                row,
            )
        elif existing != row:
            raise StoreCorrupt(
                f"staged {table} row {row[key_index]!r} conflicts with the catalog"
            )


def _copy_graph_tiers(
    staged: sqlite3.Connection,
    catalog: sqlite3.Connection,
    digests: set[str],
) -> None:
    """Copy ordered tier facts for selected graph objects without drift."""
    for digest in sorted(digests):
        rows = staged.execute(
            "SELECT digest, position, tier, item_count FROM graph_tiers "
            "WHERE digest = ? ORDER BY position",
            (digest,),
        ).fetchall()
        existing = catalog.execute(
            "SELECT digest, position, tier, item_count FROM graph_tiers "
            "WHERE digest = ? ORDER BY position",
            (digest,),
        ).fetchall()
        if existing and existing != rows:
            raise StoreCorrupt(
                f"staged graph_tiers rows for {digest!r} conflict with the catalog"
            )
        if not existing:
            catalog.executemany(
                "INSERT INTO graph_tiers(digest, position, tier, item_count) "
                "VALUES (?, ?, ?, ?)",
                rows,
            )


def _copy_blob_indexes(
    staged: sqlite3.Connection,
    catalog: sqlite3.Connection,
    digests: set[str],
) -> None:
    """Copy ordered blob and attachment facts for selected graph objects."""
    tables = (
        (
            "blob_items",
            (
                "graph_digest",
                "tier",
                "position",
                "durable_id",
                "sha256",
                "size",
                "media_type",
                "schema",
            ),
            "tier, position",
        ),
        (
            "attachments",
            (
                "graph_digest",
                "ordinal",
                "declaration",
                "durable_id",
                "subject",
                "blob_tier",
                "blob_position",
                "span_unit",
                "span_offset",
                "span_length",
                "span_path",
            ),
            "ordinal",
        ),
        (
            "blob_metadata",
            (
                "graph_digest",
                "tier",
                "position",
                "column",
                "value_text",
                "value_num",
                "source",
            ),
            "tier, position, column",
        ),
        (
            "blob_metadata_counts",
            ("graph_digest", "column", "row_count", "row_digest"),
            "column",
        ),
    )
    for digest in sorted(digests):
        for table, columns, order in tables:
            names = ", ".join(columns)
            rows = staged.execute(
                f"SELECT {names} FROM {table} WHERE graph_digest = ? ORDER BY {order}",
                (digest,),
            ).fetchall()
            existing = catalog.execute(
                f"SELECT {names} FROM {table} WHERE graph_digest = ? ORDER BY {order}",
                (digest,),
            ).fetchall()
            if existing and existing != rows:
                raise StoreCorrupt(
                    f"staged {table} rows for {digest!r} conflict with the catalog"
                )
            if not existing:
                catalog.executemany(
                    f"INSERT INTO {table}({names}) VALUES ("
                    + ", ".join("?" for _column in columns)
                    + ")",
                    rows,
                )


def _copy_item_indexes(
    staged: sqlite3.Connection,
    catalog: sqlite3.Connection,
    digests: set[str],
) -> None:
    """Copy deterministic declared-index rows for selected graph objects."""
    count_columns = "graph_digest, index_name, row_count"
    columns = (
        "graph_digest, index_name, tier_position, item_position, value_type, "
        "value_text, value_num, value_digest"
    )
    for digest in sorted(digests):
        counts = staged.execute(
            f"SELECT {count_columns} FROM item_index_counts "
            "WHERE graph_digest = ? ORDER BY index_name",
            (digest,),
        ).fetchall()
        for count in counts:
            existing_count = catalog.execute(
                f"SELECT {count_columns} FROM item_index_counts "
                "WHERE graph_digest = ? AND index_name = ?",
                (count[0], count[1]),
            ).fetchone()
            if existing_count is None:
                catalog.execute(
                    f"INSERT INTO item_index_counts({count_columns}) VALUES (?, ?, ?)",
                    count,
                )
            elif existing_count != count:
                raise StoreCorrupt(
                    f"staged item_index_counts row {(count[0], count[1])!r} "
                    "conflicts with the catalog"
                )
        rows = staged.execute(
            f"SELECT {columns} FROM item_index WHERE graph_digest = ? "
            "ORDER BY index_name, item_position",
            (digest,),
        ).fetchall()
        for row in rows:
            existing = catalog.execute(
                f"SELECT {columns} FROM item_index WHERE graph_digest = ? "
                "AND index_name = ? AND item_position = ?",
                (row[0], row[1], row[3]),
            ).fetchone()
            if existing is None:
                catalog.execute(
                    f"INSERT INTO item_index({columns}) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    row,
                )
            elif existing != row:
                raise StoreCorrupt(
                    f"staged item_index row {(row[0], row[1], row[3])!r} "
                    "conflicts with the catalog"
                )


def _replace_declared_indexes(
    staged: sqlite3.Connection, catalog: sqlite3.Connection
) -> None:
    """Publish one declaration change and its complete derived rows atomically."""
    declarations = staged.execute(
        "SELECT name, position, tier, attribute, history FROM declared_indexes "
        "ORDER BY position"
    ).fetchall()
    items = staged.execute(
        "SELECT graph_digest, index_name, tier_position, item_position, value_type, "
        "value_text, value_num, value_digest FROM item_index "
        "ORDER BY graph_digest, index_name, item_position"
    ).fetchall()
    columns = staged.execute(
        "SELECT name, position, media_type, attribute, value_type "
        "FROM declared_columns ORDER BY position"
    ).fetchall()
    metadata = staged.execute(
        "SELECT graph_digest, tier, position, column, value_text, value_num, "
        "source FROM blob_metadata "
        "ORDER BY graph_digest, tier, position, column"
    ).fetchall()
    metadata_counts = staged.execute(
        "SELECT graph_digest, column, row_count, row_digest "
        "FROM blob_metadata_counts ORDER BY graph_digest, column"
    ).fetchall()
    catalog.execute("DELETE FROM blob_metadata")
    catalog.execute("DELETE FROM blob_metadata_counts")
    catalog.execute("DELETE FROM declared_columns")
    catalog.execute("DELETE FROM item_index")
    catalog.execute("DELETE FROM item_index_counts")
    catalog.execute("DELETE FROM declared_indexes")
    catalog.executemany(
        "INSERT INTO declared_indexes(name, position, tier, attribute, history) "
        "VALUES (?, ?, ?, ?, ?)",
        declarations,
    )
    counts = staged.execute(
        "SELECT graph_digest, index_name, row_count FROM item_index_counts "
        "ORDER BY graph_digest, index_name"
    ).fetchall()
    catalog.executemany(
        "INSERT INTO item_index_counts(graph_digest, index_name, row_count) "
        "VALUES (?, ?, ?)",
        counts,
    )
    catalog.executemany(
        "INSERT INTO item_index("
        "graph_digest, index_name, tier_position, item_position, value_type, "
        "value_text, value_num, value_digest) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        items,
    )
    catalog.executemany(
        "INSERT INTO declared_columns("
        "name, position, media_type, attribute, value_type) VALUES (?, ?, ?, ?, ?)",
        columns,
    )
    catalog.executemany(
        "INSERT INTO blob_metadata_counts("
        "graph_digest, column, row_count, row_digest) VALUES (?, ?, ?, ?)",
        metadata_counts,
    )
    catalog.executemany(
        "INSERT INTO blob_metadata("
        "graph_digest, tier, position, column, value_text, value_num, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        metadata,
    )


def _prune_head_indexes(
    connection: sqlite3.Connection, changed_digests: set[str]
) -> None:
    """Remove head-only rows for changed digests that no head still names."""
    if not changed_digests:
        return
    head_digests = {
        cast(str, row[0])
        for row in connection.execute(
            "SELECT DISTINCT v.graph_digest FROM instances AS i "
            "JOIN versions AS v ON v.id = i.head_version"
        )
    }
    stale_digests = changed_digests - head_digests
    for digest in sorted(stale_digests):
        connection.execute(
            "DELETE FROM item_index WHERE graph_digest = ? AND index_name IN ("
            "SELECT name FROM declared_indexes WHERE history = 0)",
            (digest,),
        )
        connection.execute(
            "DELETE FROM item_index_counts WHERE graph_digest = ? "
            "AND index_name IN ("
            "SELECT name FROM declared_indexes WHERE history = 0)",
            (digest,),
        )


def _remove_staged_catalog(path: Path) -> None:
    """Remove one closed private staging catalog and its SQLite sidecars."""
    candidates = (
        path,
        Path(f"{path}-journal"),
        Path(f"{path}-wal"),
        Path(f"{path}-shm"),
    )
    for candidate in candidates:
        with suppress(OSError):
            candidate.unlink()


def _merge_collections(staged: sqlite3.Connection, catalog: sqlite3.Connection) -> None:
    """Merge collection rows while preserving dense declared positions."""
    rows = staged.execute(
        "SELECT id, uid, name, position, retired_commit FROM collections "
        "ORDER BY position"
    ).fetchall()
    existing = {
        row[0] for row in catalog.execute("SELECT id FROM collections").fetchall()
    }
    maximum = catalog.execute(
        "SELECT coalesce(max(position), -1) FROM collections"
    ).fetchone()[0]
    for row_id, uid, _name, _position, _retired_commit in rows:
        if row_id in existing:
            catalog.execute(
                "UPDATE collections SET name = ? WHERE id = ?",
                (_temporary_name(catalog, "collections", uid), row_id),
            )
    for row_id, uid, name, _position, retired_commit in rows:
        if row_id not in existing:
            catalog.execute(
                "INSERT INTO collections(id, uid, name, position, retired_commit) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    row_id,
                    uid,
                    _temporary_name(catalog, "collections", uid),
                    maximum + row_id + 1,
                    retired_commit,
                ),
            )
        catalog.execute(
            "UPDATE collections SET name = ?, retired_commit = ? WHERE id = ?",
            (name, retired_commit, row_id),
        )
    _set_positions(catalog, "collections", {row[1]: row[3] for row in rows})


def _merge_instance_shells(
    staged: sqlite3.Connection, catalog: sqlite3.Connection
) -> None:
    """Insert instance identities without publishing their staged heads yet."""
    rows = staged.execute(
        "SELECT id, uid, collection_id, name, position, retired_commit "
        "FROM instances ORDER BY collection_id, position"
    ).fetchall()
    existing = {row[0] for row in catalog.execute("SELECT id FROM instances")}
    maxima = {
        collection_id: maximum
        for collection_id, maximum in catalog.execute(
            "SELECT collection_id, max(position) FROM instances GROUP BY collection_id"
        )
    }
    for row_id, uid, _collection_id, _name, _position, _retired_commit in rows:
        if row_id in existing:
            catalog.execute(
                "UPDATE instances SET name = ? WHERE id = ?",
                (_temporary_name(catalog, "instances", uid), row_id),
            )
    for row_id, uid, collection_id, name, _position, retired_commit in rows:
        if row_id not in existing:
            catalog.execute(
                "INSERT INTO instances(id, uid, collection_id, name, position, "
                "head_version, generation, retired_commit) "
                "VALUES (?, ?, ?, ?, ?, NULL, 0, ?)",
                (
                    row_id,
                    uid,
                    collection_id,
                    _temporary_name(catalog, "instances", uid),
                    maxima.get(collection_id, -1) + row_id + 1,
                    retired_commit,
                ),
            )
        catalog.execute(
            "UPDATE instances SET collection_id = ?, name = ?, "
            "retired_commit = ? WHERE id = ?",
            (collection_id, name, retired_commit, row_id),
        )
    _set_positions(catalog, "instances", {row[1]: row[4] for row in rows})


def _merge_instance_heads(
    staged: sqlite3.Connection, catalog: sqlite3.Connection
) -> None:
    """Publish all staged instance heads and generations together."""
    rows = staged.execute(
        "SELECT id, head_version, generation FROM instances ORDER BY id"
    ).fetchall()
    catalog.executemany(
        "UPDATE instances SET head_version = ?, generation = ? WHERE id = ?",
        ((head, generation, row_id) for row_id, head, generation in rows),
    )


def _temporary_name(
    catalog: sqlite3.Connection,
    table: Literal["collections", "instances"],
    uid: bytes,
) -> str:
    """Return a publication-only name absent from the selected catalog table."""
    prefix = f"staged-{uid.hex()}-"
    while True:
        candidate = prefix + secrets.token_hex(16)
        if (
            catalog.execute(
                f"SELECT 1 FROM {table} WHERE name = ?", (candidate,)
            ).fetchone()
            is None
        ):
            return candidate


def _sqlite_busy(error: sqlite3.Error) -> bool:
    """Return whether SQLite classified an error as lock contention."""
    return getattr(error, "sqlite_errorcode", None) in {
        sqlite3.SQLITE_BUSY,
        sqlite3.SQLITE_LOCKED,
    }


def _validate_uid(uid: object, description: str) -> None:
    """Require one 128-bit public catalog identity."""
    if not isinstance(uid, bytes) or len(uid) != _UID_BYTES:
        raise ValueError(f"{description} must be exactly {_UID_BYTES} bytes")


def _uid_from_hex(value: str) -> bytes:
    """Decode one canonical catalog identity from an internal operation."""
    if len(value) != _UID_BYTES * 2 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise StoreCorrupt("recorded catalog identity is malformed")
    return bytes.fromhex(value)


def _validate_name(name: object) -> None:
    """Require a nonempty catalog name with a bounded UTF-8 spelling."""
    if not isinstance(name, str):
        raise TypeError("catalog name must be a string")
    size = len(name.encode("utf-8"))
    if size == 0:
        raise ValueError("catalog name must not be empty")
    if size > _MAX_NAME_BYTES:
        raise ValueError(f"catalog name exceeds {_MAX_NAME_BYTES} UTF-8 bytes")


def _validate_position(position: object, count: int, *, allow_end: bool) -> None:
    """Require a position within an existing order or its append boundary."""
    if isinstance(position, bool) or not isinstance(position, int):
        raise TypeError("position must be an integer")
    maximum = count if allow_end else count - 1
    if position < 0 or position > maximum:
        raise ValueError(f"position must be between 0 and {maximum}")


def _validate_version_seq(seq: object) -> None:
    """Require one positive, non-Boolean instance version sequence."""
    if isinstance(seq, bool) or not isinstance(seq, int):
        raise TypeError("version sequence must be an integer")
    if seq <= 0:
        raise ValueError("version sequence must be positive")


def _validate_graph(graph: object) -> None:
    """Require the immutable graph value accepted by canonical serialization."""
    if not isinstance(graph, Graph):
        raise TypeError("graph must be a Graph")


def _graph_document(graph: Graph) -> bytes:
    """Serialize a graph once and refuse bytes its public reader cannot load."""
    document = dumps(graph).encode("utf-8")
    if len(document) > MAX_DOCUMENT_BYTES:
        raise TgdbError(
            f"graph document size {len(document)} bytes exceeds limit "
            f"{MAX_DOCUMENT_BYTES}"
        )
    return document


def _bundle_graph_document(bundle: Bundle) -> bytes:
    """Return the already validated graph entry bytes from an open bundle."""
    try:
        document = bundle._archive.read("graph.json")
    except (KeyError, OSError, RuntimeError, ValueError) as error:
        raise ValueError(f"cannot reread validated graph.json: {error}") from error
    if not isinstance(document, bytes):
        raise TypeError("bundle graph reader did not return bytes")
    return document


def _bundle_source_label(source: BinaryIO) -> str:
    """Return a factual input label without inventing a filesystem location."""
    name = getattr(source, "name", None)
    return repr(name) if isinstance(name, str) else "input"


def _collection_index_bytes(manifest: Mapping[str, object]) -> bytes:
    """Encode one deterministic, bounded collection interchange index."""
    encoded = json.dumps(
        manifest,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > _MAX_COLLECTION_INDEX_BYTES:
        raise TgdbError(
            f"collection index size {len(encoded)} bytes exceeds limit "
            f"{_MAX_COLLECTION_INDEX_BYTES}"
        )
    return encoded


def _publish_collection_directory(
    staging: Path, target: Path, names: tuple[str, ...]
) -> None:
    """Publish staged files without replacing an existing path or entry."""
    try:
        target.mkdir()
    except FileExistsError as error:
        raise TgdbError(
            f"collection export path {str(target)!r} already exists"
        ) from error
    published: list[Path] = []
    try:
        for name in names:
            destination = target / name
            os.link(staging / name, destination)
            published.append(destination)
        _fsync_directory(target)
    except OSError:
        for path in reversed(published):
            with suppress(OSError):
                path.unlink()
        with suppress(OSError):
            target.rmdir()
        raise


def _read_collection_index(
    path: Path,
) -> tuple[
    tuple[bytes, str, int],
    tuple[tuple[bytes, str, int, str, str], ...],
]:
    """Read and validate the closed collection interchange index."""
    if path.is_symlink() or not path.is_file():
        raise TgdbError(f"collection index {path.name!r} is not a plain file")
    try:
        size = path.stat().st_size
        if size > _MAX_COLLECTION_INDEX_BYTES:
            raise TgdbError(
                f"collection index size {size} bytes exceeds limit "
                f"{_MAX_COLLECTION_INDEX_BYTES}"
            )
        encoded = path.read_bytes()
        text = encoded.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except TgdbError:
        raise
    except (OSError, UnicodeError, ValueError) as error:
        raise TgdbError(f"collection index is invalid: {error}") from error
    if not isinstance(value, dict) or set(value) != {
        "tgdb_export",
        "collection",
        "instances",
    }:
        raise TgdbError("collection index must be a closed export object")
    if value["tgdb_export"] != _TGDB_EXPORT_VERSION:
        raise TgdbError(
            f"collection export version {value['tgdb_export']!r} is not supported"
        )
    raw_collection = value["collection"]
    if not isinstance(raw_collection, dict) or set(raw_collection) != {
        "uid",
        "name",
        "position",
    }:
        raise TgdbError("collection index collection must be a closed object")
    collection_uid = _import_uid(raw_collection["uid"], "collection")
    collection_name = _import_name(raw_collection["name"], "collection")
    collection_position = _import_position(raw_collection["position"], "collection")
    raw_instances = value["instances"]
    if not isinstance(raw_instances, list):
        raise TgdbError("collection index instances must be an array")
    instances: list[tuple[bytes, str, int, str, str]] = []
    seen_uids: set[bytes] = set()
    seen_names: set[str] = set()
    for ordinal, raw in enumerate(raw_instances):
        if not isinstance(raw, dict) or set(raw) != {
            "uid",
            "name",
            "position",
            "head_digest",
            "functional",
        }:
            raise TgdbError(f"collection instance {ordinal} must be a closed object")
        uid = _import_uid(raw["uid"], f"instance {ordinal}")
        name = _import_name(raw["name"], f"instance {ordinal}")
        position = _import_position(raw["position"], f"instance {ordinal}")
        if position != ordinal:
            raise TgdbError(
                f"collection instance {ordinal} has position {position}; "
                "positions must be dense and declared"
            )
        digest = _import_sha256(raw["head_digest"], f"instance {ordinal} digest")
        functional = _import_sha256(
            raw["functional"], f"instance {ordinal} functional fingerprint"
        )
        if uid in seen_uids:
            raise TgdbError(f"collection instance {ordinal} repeats uid {uid.hex()}")
        if name in seen_names:
            raise TgdbError(f"collection instance {ordinal} repeats name {name!r}")
        seen_uids.add(uid)
        seen_names.add(name)
        instances.append((uid, name, position, digest, functional))
    return (
        (collection_uid, collection_name, collection_position),
        tuple(instances),
    )


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Build one JSON object while refusing duplicate member names."""
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError(f"duplicate member {name!r}")
        result[name] = value
    return result


def _reject_json_constant(value: str) -> object:
    """Refuse non-JSON numeric constants accepted by the standard parser."""
    raise ValueError(f"invalid JSON constant {value!r}")


def _import_uid(value: object, subject: str) -> bytes:
    """Decode one canonical exported 128-bit identity."""
    if (
        not isinstance(value, str)
        or len(value) != _UID_BYTES * 2
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise TgdbError(f"{subject} uid must be 32 lowercase hexadecimal characters")
    return bytes.fromhex(value)


def _import_name(value: object, subject: str) -> str:
    """Validate one exported catalog name with contextual wording."""
    try:
        _validate_name(value)
    except (TypeError, ValueError) as error:
        raise TgdbError(f"{subject} name is invalid: {error}") from error
    return cast(str, value)


def _import_position(value: object, subject: str) -> int:
    """Validate one exported nonnegative declared position."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TgdbError(f"{subject} position must be a nonnegative integer")
    return value


def _import_sha256(value: object, subject: str) -> str:
    """Validate one exported lowercase SHA-256 spelling."""
    if (
        not isinstance(value, str)
        or len(value) != _DIGEST_HEX_LENGTH
        or not value.isascii()
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise TgdbError(f"{subject} must be 64 lowercase hexadecimal characters")
    return value


def _encode_annotations(annotations: EditAnnotations | None) -> str | None:
    """Encode optional edit metadata in one deterministic catalog spelling."""
    if annotations is not None and not isinstance(annotations, EditAnnotations):
        raise TypeError("annotations must be EditAnnotations or None")
    if annotations is None:
        return None
    return json.dumps(
        annotations.to_data(),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _resolve_collection(
    connection: sqlite3.Connection,
    collection: bytes | str,
    *,
    include_retired: bool,
) -> tuple[int, bytes, str, int, int | None]:
    """Resolve a collection by stable id or unique name."""
    if isinstance(collection, bytes):
        _validate_uid(collection, "collection uid")
        condition = "uid = ?"
    elif isinstance(collection, str):
        _validate_name(collection)
        condition = "name = ?"
    else:
        raise TypeError("collection must be a uid or name")
    row = connection.execute(
        "SELECT id, uid, name, position, retired_commit FROM collections "
        f"WHERE {condition}",
        (collection,),
    ).fetchone()
    if row is None:
        raise TgdbError(f"collection {collection!r} does not exist")
    result = cast(tuple[int, bytes, str, int, int | None], row)
    if not include_retired and result[4] is not None:
        raise TgdbError(f"collection {result[2]!r} is retired")
    return result


def _resolve_instance(
    connection: sqlite3.Connection,
    instance: bytes | str,
    *,
    collection: bytes | str | None,
) -> tuple[int, bytes, int, str, int, int, int | None, int | None]:
    """Resolve an instance by global stable id or a possibly scoped name."""
    parameters: list[object] = []
    conditions: list[str] = []
    if isinstance(instance, bytes):
        _validate_uid(instance, "instance uid")
        conditions.append("uid = ?")
        parameters.append(instance)
    elif isinstance(instance, str):
        _validate_name(instance)
        conditions.append("name = ?")
        parameters.append(instance)
    else:
        raise TypeError("instance must be a uid or name")
    if collection is not None:
        collection_id = _resolve_collection(
            connection, collection, include_retired=True
        )[0]
        conditions.append("collection_id = ?")
        parameters.append(collection_id)
    rows = connection.execute(
        "SELECT id, uid, collection_id, name, position, generation, retired_commit, "
        "head_version FROM instances WHERE " + " AND ".join(conditions),
        parameters,
    ).fetchall()
    if not rows:
        description = (
            f"id {instance.hex()}"
            if isinstance(instance, bytes)
            else f"name {instance!r}"
        )
        raise TgdbError(f"instance {description} does not exist")
    if len(rows) > 1:
        raise TgdbError(
            f"instance name {instance!r} is ambiguous; specify its collection"
        )
    return cast(tuple[int, bytes, int, str, int, int, int | None, int | None], rows[0])


def _require_active_instance(
    connection: sqlite3.Connection,
    row: tuple[int, bytes, int, str, int, int, int | None, int | None],
) -> None:
    """Require an instance and its containing collection to be active."""
    if row[6] is not None:
        raise TgdbError(f"instance {row[3]!r} is retired")
    collection = connection.execute(
        "SELECT name, retired_commit FROM collections WHERE id = ?", (row[2],)
    ).fetchone()
    if collection is None:
        raise StoreCorrupt(f"instance {row[3]!r} has no collection")
    if collection[1] is not None:
        raise TgdbError(f"collection {collection[0]!r} is retired")


_QUERY_ORDERS = {
    "collection": "c.name",
    "name": "i.name",
    "position": "i.position",
    "version": "v.seq",
    "seq": "v.seq",
    "commit": "v.commit_seq",
    "commit_seq": "v.commit_seq",
    "graph_digest": "v.graph_digest",
    "functional": "f.functional",
    "identified": "f.identified",
    "stage": "v.stage",
    "iteration": "v.iteration",
}


def _validate_query_digest(value: object, description: str) -> None:
    """Require one canonical lowercase SHA-256 query value."""
    if (
        not isinstance(value, str)
        or len(value) != _DIGEST_HEX_LENGTH
        or not value.isascii()
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{description} must be a lowercase SHA-256 digest")


def _validate_equivalence_view(view: object) -> None:
    """Require one public equivalence view without accepting a raw string."""
    if not isinstance(view, EquivalenceView):
        raise TypeError("view must be an EquivalenceView")


def _validate_tier_filter(tier: object, minimum: object, maximum: object) -> None:
    """Require one typed tier and a closed or open nonnegative count range."""
    if not isinstance(tier, QualifiedName):
        raise TypeError("tier must be a QualifiedName")
    if isinstance(minimum, bool) or not isinstance(minimum, int):
        raise TypeError("minimum must be an integer")
    if minimum < 0:
        raise ValueError("minimum must be nonnegative")
    if maximum is not None:
        if isinstance(maximum, bool) or not isinstance(maximum, int):
            raise TypeError("maximum must be an integer or None")
        if maximum < minimum:
            raise ValueError("maximum must be at least minimum")


def _validate_numeric_bounds(minimum: object, maximum: object) -> None:
    """Require optional finite integer or decimal bounds in ascending order."""
    for label, value in (("minimum", minimum), ("maximum", maximum)):
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int | Decimal):
            raise TypeError(f"{label} must be an integer, Decimal, or None")
        if isinstance(value, Decimal) and not value.is_finite():
            raise ValueError(f"{label} must be finite")
        try:
            _numeric_sort_key(Decimal(value))
        except ValueError as error:
            raise ValueError(f"{label} exceeds the supported numeric range") from error
    minimum_number = cast(int | Decimal | None, minimum)
    maximum_number = cast(int | Decimal | None, maximum)
    if (
        minimum_number is not None
        and maximum_number is not None
        and Decimal(minimum_number) > Decimal(maximum_number)
    ):
        raise ValueError("maximum must be at least minimum")


def _validate_column_media_type(value: object) -> None:
    """Require an exact lowercase media type or a lowercase type family."""
    if not isinstance(value, str):
        raise TypeError("media_type must be a string")
    if _MEDIA_TYPE.fullmatch(value) is not None:
        return
    major, separator, subtype = value.partition("/")
    if separator and subtype == "*" and _MEDIA_TYPE.fullmatch(f"{major}/x") is not None:
        return
    raise ValueError("media_type must be a canonical lowercase type/subtype or type/*")


def _validate_column_value_type(value: object) -> None:
    """Require one public scalar or structured attribute value type spelling."""
    allowed = {member.value for member in XsdType} | {
        member.value for member in JsonType
    }
    if not isinstance(value, str):
        raise TypeError("value_type must be a string")
    if value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ValueError(f"value_type must be one of: {choices}")


def _media_type_matches(declaration: str, actual: str) -> bool:
    """Return whether an exact or family declaration selects a media type."""
    return declaration == actual or (
        declaration.endswith("/*") and actual.startswith(declaration.removesuffix("*"))
    )


_COLUMN_QUERY_NAME = QualifiedName("urn:tiergraph:tgdb:query", "value")


def _column_query_value(value_type: str, value: object) -> tuple[str, str | None]:
    """Encode one native equality value through a column's declared type."""
    try:
        if value_type == JsonType.JSON.value:
            attribute: Attribute = JsonAttributeValue(
                _COLUMN_QUERY_NAME, cast(JsonValue, value)
            )
        else:
            scalar_type = XsdType(value_type)
            if scalar_type is XsdType.STRING:
                if not isinstance(value, str):
                    raise TypeError("string column equality requires a string")
                lexical = value
            elif scalar_type is XsdType.BOOLEAN:
                if not isinstance(value, bool):
                    raise TypeError("boolean column equality requires a boolean")
                lexical = str(value).lower()
            elif scalar_type is XsdType.INTEGER:
                if isinstance(value, bool) or not isinstance(value, int):
                    raise TypeError("integer column equality requires an integer")
                lexical = str(value)
            elif scalar_type is XsdType.DECIMAL:
                if isinstance(value, bool) or not isinstance(value, int | Decimal):
                    raise TypeError(
                        "decimal column equality requires an integer or Decimal"
                    )
                lexical = str(value)
            else:
                if isinstance(value, bool) or not isinstance(
                    value, int | float | Decimal
                ):
                    raise TypeError(
                        "double column equality requires an integer, float, or Decimal"
                    )
                lexical = str(value)
            attribute = AttributeValue(_COLUMN_QUERY_NAME, scalar_type, lexical)
    except ValueError as error:
        raise ValueError(
            f"column equality is not a valid {value_type} value: {error}"
        ) from error
    _actual_type, value_text, value_num, _digest = _index_value(attribute)
    return value_text, value_num


def _validate_query_run(
    query: object,
    order_by: object,
    batch_size: object,
    limit: object,
) -> None:
    """Validate public execution controls before opening a result cursor."""
    if not isinstance(query, Query):
        raise TypeError("query must be a Query")
    if order_by is not None and order_by not in _QUERY_ORDERS:
        choices = ", ".join(_QUERY_ORDERS)
        raise ValueError(f"order_by must be one of: {choices}")
    if isinstance(batch_size, bool) or not isinstance(batch_size, int):
        raise TypeError("batch_size must be an integer")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if limit is not None:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError("limit must be an integer or None")
        if limit < 0:
            raise ValueError("limit must be nonnegative")


def _qname_text(name: QualifiedName) -> str:
    """Encode an expanded name without depending on document-local prefixes."""
    return json.dumps(name.to_data(), separators=(",", ":"), sort_keys=True)


def _qname_from_text(value: str) -> QualifiedName:
    """Decode one internally stored expanded name."""
    data = json.loads(value)
    if not isinstance(data, dict) or set(data) != {"namespace", "local_name"}:
        raise ValueError("stored qualified name is malformed")
    return QualifiedName(data["namespace"], data["local_name"])


def _qname_spelling(name: QualifiedName) -> str:
    """Return the CLI spelling for one expanded name."""
    return f"{name.namespace}|{name.local_name}"


def _index_value(value: Attribute) -> tuple[str, str, str | None, str]:
    """Return canonical typed text, an exact numeric key, and its digest."""
    if isinstance(value, AttributeValue):
        value_type = value.value_type.value
        value_text = value.lexical
        value_num = (
            _numeric_sort_key(Decimal(value.lexical))
            if value.value_type in {XsdType.INTEGER, XsdType.DECIMAL}
            else None
        )
    else:
        value_type = value.value_type.value
        value_text = json.dumps(
            value.to_value(),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        value_num = None
    digest_source = json.dumps(
        [value_type, value_text],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return value_type, value_text, value_num, hashlib.sha256(digest_source).hexdigest()


def _numeric_sort_key(value: Decimal) -> str:
    """Encode a finite decimal so ordinary text order is exact numeric order."""
    if not value.is_finite():
        raise ValueError("numeric index values must be finite")
    if value.is_zero():
        return "1"
    absolute = value.copy_abs()
    adjusted = absolute.adjusted()
    if not -_NUMERIC_OFFSET < adjusted < _NUMERIC_OFFSET:
        raise ValueError("numeric index value exceeds the graph document bound")
    digits = "".join(str(digit) for digit in absolute.as_tuple().digits).rstrip("0")
    if value > 0:
        exponent = adjusted + _NUMERIC_OFFSET
        return f"2{exponent:0{_NUMERIC_EXPONENT_WIDTH}d}:{digits}/"
    exponent = _NUMERIC_OFFSET - adjusted
    complement = "".join(str(9 - int(digit)) for digit in digits)
    return f"0{exponent:0{_NUMERIC_EXPONENT_WIDTH}d}:{complement}:"


def _query_sql(  # noqa: PLR0915 -- each typed clause stays visibly parameterized
    query: Query,
    order_by: str | None,
    column_types: Mapping[str, str] | None = None,
) -> tuple[str, list[object]]:
    """Compile one typed query into internal SQL and bound parameters."""
    conditions: list[str] = []
    parameters: list[object] = []
    if query._collection is not None:
        conditions.append("c.name = ?")
        parameters.append(query._collection)
    if not query._include_retired:
        conditions.extend(("i.retired_commit IS NULL", "c.retired_commit IS NULL"))
    for clause in query._clauses:
        if isinstance(clause, _IdsClause):
            marks = ", ".join("?" for _value in clause.values)
            conditions.append(f"i.uid IN ({marks})")
            parameters.extend(clause.values)
        elif isinstance(clause, _NamesClause):
            marks = ", ".join("?" for _value in clause.values)
            conditions.append(f"i.name IN ({marks})")
            parameters.extend(clause.values)
        elif isinstance(clause, _FingerprintClause):
            column = {
                EquivalenceView.FUNCTIONAL: "f.functional",
                EquivalenceView.IDENTIFIED: "f.identified",
                EquivalenceView.EXACT: "v.graph_digest",
            }[clause.view]
            conditions.append(f"{column} = ?")
            parameters.append(clause.value)
        elif isinstance(clause, _TierClause):
            tier_conditions = ["gt.digest = v.graph_digest", "gt.tier = ?"]
            tier_parameters: list[object] = [_qname_text(clause.tier)]
            if clause.present:
                tier_conditions.append("gt.item_count >= ?")
                tier_parameters.append(clause.minimum)
                if clause.maximum is not None:
                    tier_conditions.append("gt.item_count <= ?")
                    tier_parameters.append(clause.maximum)
            exists = (
                ("EXISTS" if clause.present else "NOT EXISTS")
                + " (SELECT 1 FROM graph_tiers AS gt WHERE "
                + " AND ".join(tier_conditions)
                + ")"
            )
            conditions.append(exists)
            parameters.extend(tier_parameters)
        elif isinstance(clause, _IndexClause):
            index_conditions = [
                "ix.graph_digest = v.graph_digest",
                "ix.index_name = ?",
            ]
            index_parameters: list[object] = [clause.name]
            if clause.value is not None:
                value_type, value_text, _value_num, value_digest = _index_value(
                    clause.value
                )
                index_conditions.extend(
                    (
                        "ix.value_digest = ?",
                        "ix.value_type = ?",
                        "ix.value_text = ?",
                    )
                )
                index_parameters.extend((value_digest, value_type, value_text))
            if clause.minimum is not None:
                index_conditions.append("ix.value_num >= ?")
                index_parameters.append(_numeric_sort_key(Decimal(clause.minimum)))
            if clause.maximum is not None:
                index_conditions.append("ix.value_num <= ?")
                index_parameters.append(_numeric_sort_key(Decimal(clause.maximum)))
            exists = (
                ("EXISTS" if clause.present else "NOT EXISTS")
                + " (SELECT 1 FROM item_index AS ix WHERE "
                + " AND ".join(index_conditions)
                + ")"
            )
            conditions.append(exists)
            parameters.extend(index_parameters)
        elif isinstance(clause, _StageClause):
            conditions.append("v.stage IS ?")
            parameters.append(clause.stage)
            if clause.iteration is not None:
                conditions.append("v.iteration = ?")
                parameters.append(clause.iteration)
        elif isinstance(clause, _UsesBlobClause):
            conditions.append(
                "EXISTS (SELECT 1 FROM blob_items AS bi "
                "WHERE bi.graph_digest = v.graph_digest AND bi.sha256 = ?)"
            )
            parameters.append(clause.sha256)
        elif isinstance(clause, _BlobMediaTypeClause):
            conditions.append(
                "EXISTS (SELECT 1 FROM blob_items AS bi "
                "WHERE bi.graph_digest = v.graph_digest AND bi.media_type = ?)"
            )
            parameters.append(clause.media_type)
        elif isinstance(clause, _BlobColumnClause):
            metadata_conditions = [
                "bm.graph_digest = v.graph_digest",
                "bm.column = ?",
            ]
            metadata_parameters: list[object] = [clause.name]
            if clause.equals is not None:
                assert column_types is not None
                value_text, _value_num = _column_query_value(
                    column_types[clause.name], clause.equals
                )
                metadata_conditions.append("bm.value_text = ?")
                metadata_parameters.append(value_text)
            if clause.minimum is not None:
                metadata_conditions.append("bm.value_num >= ?")
                metadata_parameters.append(_numeric_sort_key(Decimal(clause.minimum)))
            if clause.maximum is not None:
                metadata_conditions.append("bm.value_num <= ?")
                metadata_parameters.append(_numeric_sort_key(Decimal(clause.maximum)))
            if clause.source is not None:
                metadata_conditions.append("bm.source = ?")
                metadata_parameters.append(clause.source)
            conditions.append(
                "EXISTS (SELECT 1 FROM blob_metadata AS bm WHERE "
                + " AND ".join(metadata_conditions)
                + ")"
            )
            parameters.extend(metadata_parameters)
        elif isinstance(clause, _MissingBlobClause):
            conditions.append(
                "EXISTS (SELECT 1 FROM blob_items AS bi "
                "LEFT JOIN objects AS held ON held.digest = bi.sha256 "
                "WHERE bi.graph_digest = v.graph_digest AND held.digest IS NULL)"
            )
        else:
            assert isinstance(clause, _ChangedSinceClause)
            current, historical = {
                EquivalenceView.FUNCTIONAL: ("f.functional", "oldf.functional"),
                EquivalenceView.IDENTIFIED: ("f.identified", "oldf.identified"),
                EquivalenceView.EXACT: ("v.graph_digest", "oldv.graph_digest"),
            }[clause.view]
            conditions.append(
                "(NOT EXISTS (SELECT 1 FROM versions AS beforev "
                "WHERE beforev.instance_id = i.id AND beforev.commit_seq <= ?) "
                f"OR {current} != (SELECT {historical} FROM versions AS oldv "
                "LEFT JOIN graph_facts AS oldf ON oldf.digest = oldv.graph_digest "
                "WHERE oldv.instance_id = i.id AND oldv.commit_seq <= ? "
                "ORDER BY oldv.seq DESC LIMIT 1))"
            )
            parameters.extend((clause.commit_seq, clause.commit_seq))
    where = "" if not conditions else " WHERE " + " AND ".join(conditions)
    order = "c.position, i.position"
    if order_by is not None:
        order = f"{_QUERY_ORDERS[order_by]}, c.position, i.position"
    statement = (
        "SELECT i.id, i.uid, i.collection_id, i.name, i.position, i.generation, "
        "i.retired_commit, i.head_version, c.name, "
        "v.seq, v.commit_seq, v.graph_digest, f.functional, f.identified, "
        "v.stage, v.id, v.iteration, v.patch_digest, v.transition, v.reason "
        "FROM instances AS i JOIN collections AS c ON c.id = i.collection_id "
        "JOIN versions AS v ON v.id = i.head_version "
        "LEFT JOIN graph_facts AS f ON f.digest = v.graph_digest"
        f"{where} ORDER BY {order}"
    )
    return statement, parameters


def _run_query(
    store: TgdbStore,
    query: Query,
    *,
    order_by: str | None,
    batch_size: int,
    limit: int | None,
) -> Iterator[VersionHandle]:
    """Yield one compiled query while retaining only a bounded row batch."""
    if limit == 0:
        return
    try:
        yield from _query_rows(
            store,
            query,
            order_by=order_by,
            batch_size=batch_size,
            limit=limit,
        )
    except sqlite3.DatabaseError as error:
        raise StoreCorrupt(f"cannot query tgdb catalog: {error}") from error


def _require_complete_item_indexes(
    connection: sqlite3.Connection,
    *,
    index_name: str | None = None,
    heads_only: bool = False,
) -> None:
    """Refuse missing, extra, or incomplete declared-item derived rows."""
    if heads_only:
        if index_name is None:
            raise ValueError("heads-only item-index checks require an index name")
        expected_rows = connection.execute(
            "SELECT DISTINCT v.graph_digest, d.name FROM instances AS i "
            "JOIN versions AS v ON v.id = i.head_version "
            "JOIN declared_indexes AS d ON d.name = ? "
            "ORDER BY v.graph_digest, d.name",
            (index_name,),
        )
        counts_rows = connection.execute(
            "SELECT c.graph_digest, c.index_name, c.row_count "
            "FROM item_index_counts AS c WHERE c.index_name = ? "
            "AND EXISTS (SELECT 1 FROM instances AS i "
            "JOIN versions AS v ON v.id = i.head_version "
            "WHERE v.graph_digest = c.graph_digest) "
            "ORDER BY c.graph_digest, c.index_name",
            (index_name,),
        )
        actual_rows = connection.execute(
            "SELECT ix.graph_digest, ix.index_name, count(*) FROM item_index AS ix "
            "WHERE ix.index_name = ? AND EXISTS (SELECT 1 FROM instances AS i "
            "JOIN versions AS v ON v.id = i.head_version "
            "WHERE v.graph_digest = ix.graph_digest) "
            "GROUP BY ix.graph_digest, ix.index_name "
            "ORDER BY ix.graph_digest, ix.index_name",
            (index_name,),
        )
    else:
        expected_rows = connection.execute(
            "SELECT v.graph_digest, d.name FROM ("
            "SELECT DISTINCT graph_digest FROM versions"
            ") AS v JOIN declared_indexes AS d ON d.history = 1 "
            "UNION SELECT v.graph_digest, d.name FROM ("
            "SELECT DISTINCT head.graph_digest FROM instances AS i "
            "JOIN versions AS head ON head.id = i.head_version"
            ") AS v JOIN declared_indexes AS d ON d.history = 0 "
            "ORDER BY 1, 2"
        )
        counts_rows = connection.execute(
            "SELECT graph_digest, index_name, row_count FROM item_index_counts "
            "ORDER BY graph_digest, index_name"
        )
        actual_rows = connection.execute(
            "SELECT graph_digest, index_name, count(*) FROM item_index "
            "GROUP BY graph_digest, index_name ORDER BY graph_digest, index_name"
        )
    expected = {
        (cast(str, row[0]), cast(str, row[1])) for row in expected_rows.fetchall()
    }
    counts = {
        (cast(str, row[0]), cast(str, row[1])): cast(int, row[2])
        for row in counts_rows.fetchall()
    }
    actual = {
        (cast(str, row[0]), cast(str, row[1])): cast(int, row[2])
        for row in actual_rows.fetchall()
    }
    for key in sorted(expected):
        recorded = counts.pop(key, None)
        if recorded is None:
            raise StoreCorrupt(f"item_index_counts row {key!r} is missing")
        if actual.pop(key, 0) != recorded:
            raise StoreCorrupt(f"item_index rows for {key!r} are incomplete")
    if counts:
        key = next(iter(counts))
        raise StoreCorrupt(f"item_index_counts row {key!r} is unexpected")
    if actual:
        key = next(iter(actual))
        raise StoreCorrupt(f"item_index rows for {key!r} are unexpected")


def _require_complete_blob_indexes(connection: sqlite3.Connection) -> None:
    """Refuse incomplete blob-item or attachment derived rows."""
    missing_fact = connection.execute(
        "SELECT g.digest FROM (SELECT graph_digest AS digest FROM versions "
        "UNION SELECT digest FROM inspections WHERE inspector = ? "
        "AND inspector_version = ?) AS g "
        "LEFT JOIN graph_facts AS f ON f.digest = g.digest "
        "WHERE f.digest IS NULL ORDER BY g.digest LIMIT 1",
        (_TIERGRAPH_INSPECTOR_NAME, _TIERGRAPH_INSPECTOR_VERSION),
    ).fetchone()
    if missing_fact is not None:
        raise StoreCorrupt(f"graph object {missing_fact[0]} has no graph facts")
    for table, count_column in (
        ("blob_items", "blob_count"),
        ("attachments", "attachment_count"),
    ):
        incomplete = connection.execute(
            "SELECT f.digest FROM graph_facts AS f LEFT JOIN ("
            f"SELECT graph_digest, count(*) AS row_count FROM {table} "
            "GROUP BY graph_digest"
            ") AS derived ON derived.graph_digest = f.digest "
            f"WHERE coalesce(derived.row_count, 0) != f.{count_column} "
            "ORDER BY f.digest LIMIT 1"
        ).fetchone()
        if incomplete is not None:
            raise StoreCorrupt(
                f"{table} rows for object {incomplete[0]} are incomplete"
            )


def _require_complete_blob_metadata(
    connection: sqlite3.Connection,
    *,
    column_name: str | None = None,
    heads_only: bool = False,
) -> None:
    """Refuse missing, unexpected, or drifted blob-metadata rows."""
    if heads_only:
        expected_query = (
            "SELECT DISTINCT v.graph_digest, d.name FROM instances AS i "
            "JOIN versions AS v ON v.id = i.head_version "
            "JOIN declared_columns AS d ON (? IS NULL OR d.name = ?) "
            "ORDER BY v.graph_digest, d.name"
        )
        selected = (
            "EXISTS (SELECT 1 FROM instances AS i "
            "JOIN versions AS v ON v.id = i.head_version "
            "WHERE v.graph_digest = {alias}.graph_digest)"
        )
    else:
        expected_query = (
            "SELECT f.digest, d.name FROM graph_facts AS f "
            "JOIN declared_columns AS d ON (? IS NULL OR d.name = ?) "
            "ORDER BY f.digest, d.name"
        )
        selected = "1"
    parameters = (column_name, column_name)
    expected = {
        (cast(str, row[0]), cast(str, row[1]))
        for row in connection.execute(expected_query, parameters)
    }
    counts = {
        (cast(str, row[0]), cast(str, row[1])): (cast(int, row[2]), cast(str, row[3]))
        for row in connection.execute(
            "SELECT c.graph_digest, c.column, c.row_count, c.row_digest "
            "FROM blob_metadata_counts AS c WHERE (? IS NULL OR c.column = ?) "
            f"AND {selected.format(alias='c')} "
            "ORDER BY c.graph_digest, c.column",
            parameters,
        )
    }
    grouped: dict[tuple[str, str], list[_BlobMetadataRow]] = {}
    metadata_rows = connection.execute(
        "SELECT m.graph_digest, m.tier, m.position, m.column, m.value_text, "
        "m.value_num, m.source FROM blob_metadata AS m "
        "WHERE (? IS NULL OR m.column = ?) "
        f"AND {selected.format(alias='m')} "
        "ORDER BY m.graph_digest, m.column, m.tier, m.position",
        parameters,
    )
    for row in metadata_rows:
        metadata = cast(_BlobMetadataRow, row)
        grouped.setdefault((metadata[0], metadata[3]), []).append(metadata)
    for key in sorted(expected):
        recorded = counts.pop(key, None)
        if recorded is None:
            raise StoreCorrupt(f"blob_metadata_counts row {key!r} is missing")
        rows = tuple(grouped.pop(key, ()))
        actual = (len(rows), _blob_metadata_row_digest(rows))
        if recorded != actual:
            raise StoreCorrupt(
                f"blob_metadata rows for {key!r} are incomplete or drifted"
            )
    if counts:
        key = next(iter(counts))
        raise StoreCorrupt(f"blob_metadata_counts row {key!r} is unexpected")
    if grouped:
        key = next(iter(grouped))
        raise StoreCorrupt(f"blob_metadata rows for {key!r} are unexpected")


def _require_complete_derived_indexes(connection: sqlite3.Connection) -> None:
    """Refuse incomplete queryable derived rows without opening documents."""
    _require_complete_blob_indexes(connection)
    _require_complete_blob_metadata(connection)
    _require_complete_item_indexes(connection)


def _query_rows(  # noqa: PLR0915 -- typed query guards stay beside execution
    store: TgdbStore,
    query: Query,
    *,
    order_by: str | None,
    batch_size: int,
    limit: int | None,
) -> Iterator[VersionHandle]:
    """Execute validated query SQL and post-filter its bounded row stream."""
    checked_indexes: set[str] = set()
    column_types: dict[str, str] = {}
    checked_columns: set[str] = set()
    for clause in query._clauses:
        if not isinstance(clause, _IndexClause):
            continue
        declaration = store._connection.execute(
            "SELECT attribute FROM declared_indexes WHERE name = ?", (clause.name,)
        ).fetchone()
        if declaration is None:
            raise TgdbError(f"item index {clause.name!r} is not declared")
        if clause.value is not None and declaration[0] != _qname_text(
            clause.value.name
        ):
            raise TgdbError(
                f"item index {clause.name!r} is declared for another attribute"
            )
        if clause.name not in checked_indexes:
            _require_complete_item_indexes(
                store._connection, index_name=clause.name, heads_only=True
            )
            checked_indexes.add(clause.name)
    for clause in query._clauses:
        if not isinstance(clause, _BlobColumnClause):
            continue
        declaration = store._connection.execute(
            "SELECT value_type FROM declared_columns WHERE name = ?", (clause.name,)
        ).fetchone()
        if declaration is None:
            raise TgdbError(f"blob column {clause.name!r} is not declared")
        value_type = cast(str, declaration[0])
        if (
            clause.minimum is not None or clause.maximum is not None
        ) and value_type not in {
            XsdType.INTEGER.value,
            XsdType.DECIMAL.value,
        }:
            raise TgdbError(f"blob column {clause.name!r} is not integer or decimal")
        if clause.equals is not None:
            _column_query_value(value_type, clause.equals)
        column_types[clause.name] = value_type
        if clause.name not in checked_columns:
            _require_complete_blob_metadata(
                store._connection, column_name=clause.name, heads_only=True
            )
            checked_columns.add(clause.name)
    needs_facts = any(
        isinstance(clause, _ChangedSinceClause)
        or (
            isinstance(clause, _FingerprintClause)
            and clause.view is not EquivalenceView.EXACT
        )
        for clause in query._clauses
    )
    if needs_facts:
        missing = store._connection.execute(
            "SELECT v.graph_digest FROM versions AS v "
            "LEFT JOIN graph_facts AS f ON f.digest = v.graph_digest "
            "WHERE f.digest IS NULL LIMIT 1"
        ).fetchone()
        if missing is not None:
            raise StoreCorrupt(f"version object {missing[0]} has no graph facts")
    if any(isinstance(clause, _TierClause) for clause in query._clauses):
        incomplete = store._connection.execute(
            "SELECT f.digest FROM graph_facts AS f LEFT JOIN ("
            "SELECT digest, count(*) AS row_count FROM graph_tiers GROUP BY digest"
            ") AS gt ON gt.digest = f.digest "
            "WHERE coalesce(gt.row_count, 0) != f.tier_count LIMIT 1"
        ).fetchone()
        if incomplete is not None:
            raise StoreCorrupt(
                f"graph_tiers rows for object {incomplete[0]} are incomplete"
            )
    if any(
        isinstance(
            clause,
            (
                _UsesBlobClause,
                _BlobMediaTypeClause,
                _BlobColumnClause,
                _MissingBlobClause,
            ),
        )
        for clause in query._clauses
    ):
        _require_complete_blob_indexes(store._connection)
    statement, parameters = _query_sql(query, order_by, column_types)
    cursor = store._connection.execute(statement, parameters)
    compiled = tuple(
        _predicate.compile_predicate(predicate) for predicate in query._predicates
    )
    emitted = 0
    while rows := cursor.fetchmany(batch_size):
        for row in rows:
            instance = cast(
                tuple[int, bytes, int, str, int, int, int | None, int | None],
                row[:8],
            )
            version = cast(
                tuple[
                    int,
                    int,
                    str,
                    str,
                    str,
                    str | None,
                    int,
                    int | None,
                    str | None,
                    Literal["initial", "patch", "snapshot"],
                    str | None,
                ],
                row[9:],
            )
            handle = _version_handle(
                store,
                instance,
                version,
                collection_name=cast(str, row[8]),
            )
            if compiled:
                graph = handle.load()
                candidates = NodeSet(
                    graph,
                    tuple(
                        Node(NodeKind.ITEM, reference)
                        for reference in graph.canonical_items()
                    ),
                )
                matches = True
                for bound in compiled:
                    try:
                        selected = bound.bind(graph).select(candidates)
                    except Refusal as error:
                        if error.stage is not RefusalStage.REFERENCE:
                            raise
                        matches = False
                        break
                    if not selected.nodes:
                        matches = False
                        break
                if not matches:
                    continue
            yield handle
            emitted += 1
            if limit is not None and emitted >= limit:
                return


type _ItemIndexRow = tuple[str, str, int, int, str, str, str | None, str]
type _ItemIndexCountRow = tuple[str, str, int]
type _BlobItemRow = tuple[str, str, int, str, str, int, str, str | None]
type _BlobMetadataRow = tuple[
    str,
    str,
    int,
    str,
    str,
    str | None,
    Literal["declared", "inspected"],
]
type _BlobMetadataCountRow = tuple[str, str, int, str]
type _AttachmentRow = tuple[
    str,
    int,
    str,
    str,
    str,
    str,
    int,
    str | None,
    int | None,
    int | None,
    str | None,
]


def _inspection_candidates(
    store: TgdbStore,
    digests: tuple[str, ...],
) -> tuple[_InspectionCandidate, ...]:
    """Select held blob interpretations without sorting presented content."""
    if digests:
        selected: list[_InspectionCandidate] = []
        seen: set[tuple[str, str]] = set()
        for digest in digests:
            rows = store._connection.execute(
                "SELECT bi.size, bi.media_type FROM blob_items AS bi "
                "JOIN graph_tiers AS gt ON gt.digest = bi.graph_digest "
                "AND gt.tier = bi.tier JOIN objects AS o ON o.digest = bi.sha256 "
                "WHERE bi.sha256 = ? ORDER BY gt.position, bi.position",
                (digest,),
            )
            for size, actual_media_type in rows:
                key = (digest, cast(str, actual_media_type))
                if key not in seen:
                    seen.add(key)
                    selected.append(
                        _InspectionCandidate(
                            digest, cast(int, size), cast(str, actual_media_type)
                        )
                    )
        return tuple(selected)
    rows = store._connection.execute(
        "SELECT bi.sha256, bi.size, bi.media_type FROM collections AS c "
        "JOIN instances AS i ON i.collection_id = c.id "
        "JOIN versions AS v ON v.id = i.head_version "
        "JOIN blob_items AS bi ON bi.graph_digest = v.graph_digest "
        "JOIN graph_tiers AS gt ON gt.digest = bi.graph_digest "
        "AND gt.tier = bi.tier JOIN objects AS o ON o.digest = bi.sha256 "
        "WHERE c.retired_commit IS NULL AND i.retired_commit IS NULL "
        "ORDER BY c.position, i.position, gt.position, bi.position"
    )
    selected = []
    seen = set()
    for digest, size, actual_media_type in rows:
        key = (cast(str, digest), cast(str, actual_media_type))
        if key not in seen:
            seen.add(key)
            selected.append(
                _InspectionCandidate(
                    cast(str, digest), cast(int, size), cast(str, actual_media_type)
                )
            )
    return tuple(selected)


def _inspection_facts(
    inspector: Inspector,
    ref: BlobRef,
    source: BinaryIO,
) -> tuple[InspectionFact, ...]:
    """Materialize and validate one inspector's bounded ordered output."""
    facts: list[InspectionFact] = []
    try:
        for ordinal, emitted in enumerate(inspector.inspect(ref, source)):
            if ordinal >= _MAX_INSPECTION_FACTS:
                raise TgdbError(
                    f"inspector {inspector.name!r} exceeded "
                    f"{_MAX_INSPECTION_FACTS} facts for {ref.sha256}"
                )
            if not isinstance(emitted, tuple) or len(emitted) != _INSPECTION_PAIR_SIZE:
                raise TypeError("inspector output must contain (key, value) tuples")
            key, value = emitted
            if not isinstance(key, str) or not key:
                raise TypeError("inspection fact key must be a nonempty string")
            if not isinstance(value, AttributeValue):
                raise TypeError("inspection fact value must be an AttributeValue")
            facts.append(InspectionFact(key, value))
        source.read()
        if not isinstance(source, VerifiedReader) or not source.verified:
            raise ValueError("inspector did not leave a fully verified payload read")
    except TgdbError:
        raise
    except Exception as error:
        raise TgdbError(
            f"inspector {inspector.name!r} failed for {ref.sha256}: {error}"
        ) from error
    return tuple(facts)


def _tiergraph_inspection_facts(graph: Graph) -> tuple[InspectionFact, ...]:
    """Return the built-in inspector's facts from one already loaded graph."""
    encoded = dumps(graph).encode()
    ref = BlobRef(hashlib.sha256(encoded).hexdigest(), len(encoded))
    return tuple(
        InspectionFact(key, value)
        for key, value in _TIERGRAPH_INSPECTOR.inspect(ref, io.BytesIO(encoded))
    )


def _run_inspections(  # noqa: PLR0915 -- selection and nested walk are one contract
    store: TgdbStore,
    candidates: list[_InspectionCandidate],
    inspectors: tuple[Inspector, ...],
    *,
    media_type: str | None,
    strict: bool = False,
) -> tuple[tuple[InspectionResult, ...], dict[str, tuple[Graph, bytes]]]:
    """Run selected inspectors and discover nested graph resources in order."""
    results: list[InspectionResult] = []
    graphs: dict[str, tuple[Graph, bytes]] = {}
    visited: set[tuple[str, str, str]] = set()
    cursor = 0
    while cursor < len(candidates):
        candidate = candidates[cursor]
        cursor += 1
        matching = tuple(
            item for item in inspectors if candidate.media_type in item.media_types
        )
        if not matching:
            if strict and not candidate.chain:
                raise TgdbError(
                    f"no registered inspector accepts {candidate.media_type!r} "
                    f"for {candidate.digest}"
                )
            continue
        for selected in matching:
            visit = (candidate.digest, selected.name, selected.version)
            if visit in visited:
                continue
            visited.add(visit)
            ref = BlobRef(candidate.digest, candidate.size)
            graph: Graph | None = None
            document: bytes | None = None
            if selected is _TIERGRAPH_INSPECTOR:
                if candidate.depth > store._limits.nesting_depth:
                    chain = " -> ".join((*candidate.chain, candidate.digest))
                    raise TgdbError(
                        "attached graph nesting exceeds limit "
                        f"{store._limits.nesting_depth}: {chain}"
                    )
                try:
                    with store._open_object(candidate.digest) as source:
                        document = source.read()
                    graph = loads(document)
                except (Refusal, ValueError) as error:
                    raise TgdbError(
                        f"inspector {selected.name!r} failed for "
                        f"{candidate.digest}: {error}"
                    ) from error
                if dumps(graph).encode() != document:
                    raise TgdbError(
                        f"inspector {selected.name!r} refused noncanonical "
                        f"tiergraph document {candidate.digest}"
                    )
                facts = _tiergraph_inspection_facts(graph)
                graphs[candidate.digest] = (graph, document)
            else:
                with store._open_object(candidate.digest) as source:
                    facts = _inspection_facts(selected, ref, cast(BinaryIO, source))
            results.append(
                InspectionResult(
                    candidate.digest,
                    candidate.media_type,
                    selected.name,
                    selected.version,
                    facts,
                )
            )
            if graph is None:
                continue
            blob_rows, _attachment_rows = _blob_index_rows(graph, candidate.digest)
            for row in blob_rows:
                nested_digest, nested_size, nested_media = row[4], row[5], row[6]
                if media_type is not None and nested_media != media_type:
                    continue
                if store._object_record(nested_digest) is None:
                    if any(nested_media in item.media_types for item in inspectors):
                        chain = " -> ".join(
                            (*candidate.chain, candidate.digest, nested_digest)
                        )
                        raise TgdbError(f"nested payload is not stored: {chain}")
                    continue
                nested_depth = candidate.depth + (
                    1 if nested_media == _TIERGRAPH_MEDIA_TYPE else 0
                )
                candidates.append(
                    _InspectionCandidate(
                        nested_digest,
                        nested_size,
                        nested_media,
                        nested_depth,
                        (*candidate.chain, candidate.digest),
                    )
                )
    return tuple(results), graphs


def _inspection_value_text(value: AttributeValue) -> str:
    """Encode one scalar inspection value without losing its qualified name."""
    return json.dumps(
        value.to_data(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def _decode_inspection_value(
    value_type: str,
    encoded: str,
    digest: str,
) -> AttributeValue:
    """Decode and validate one stored scalar inspection value."""
    try:
        data = json.loads(encoded)
        if not isinstance(data, dict) or set(data) != {"name", "value_type", "lexical"}:
            raise ValueError("value envelope is malformed")
        name_data = data["name"]
        if not isinstance(name_data, dict):
            raise ValueError("qualified name is malformed")
        value = AttributeValue(
            QualifiedName(name_data["namespace"], name_data["local_name"]),
            XsdType(data["value_type"]),
            data["lexical"],
        )
        if value.value_type.value != value_type:
            raise ValueError("value type column disagrees with the value")
        return value
    except (KeyError, TypeError, ValueError) as error:
        raise StoreCorrupt(
            f"inspection value for {digest} is malformed: {error}"
        ) from error


def _inspection_rows(
    result: InspectionResult,
) -> tuple[tuple[str, str, str, str, int, str, str, str], ...]:
    """Encode one result as ordered catalog rows."""
    return tuple(
        (
            result.digest,
            result.media_type,
            result.inspector,
            result.inspector_version,
            ordinal,
            fact.key,
            fact.value.value_type.value,
            _inspection_value_text(fact.value),
        )
        for ordinal, fact in enumerate(result.facts)
    )


def _check_inspection_rows(connection: sqlite3.Connection) -> None:
    """Validate inspection values and contiguous per-inspector ordinals."""
    previous: tuple[str, str, str] | None = None
    expected_ordinal = 0
    rows = connection.execute(
        "SELECT digest, media_type, inspector, inspector_version, ordinal, key, "
        "value_type, value FROM inspections "
        "ORDER BY digest, inspector, inspector_version, ordinal"
    )
    for digest, media_type, inspector, version, ordinal, key, value_type, value in rows:
        group = (cast(str, digest), cast(str, inspector), cast(str, version))
        if group != previous:
            previous = group
            expected_ordinal = 0
        if ordinal != expected_ordinal:
            raise StoreCorrupt(
                f"inspection rows for {digest} and {inspector!r} "
                "have noncontiguous ordinals"
            )
        if not isinstance(media_type, str) or _MEDIA_TYPE.fullmatch(media_type) is None:
            raise StoreCorrupt(f"inspection row for {digest} has an invalid media type")
        if not isinstance(key, str) or not key:
            raise StoreCorrupt(f"inspection row for {digest} has an invalid key")
        _decode_inspection_value(cast(str, value_type), cast(str, value), digest)
        expected_ordinal += 1


def _index_inspected_graph(
    store: TgdbStore,
    graph: Graph,
    digest: str,
    document: bytes,
) -> None:
    """Write rebuildable small facts for one explicitly inspected graph."""
    blob_rows, attachment_rows = _blob_index_rows(graph, digest)
    expected_fact = (
        digest,
        FORMAT_VERSION,
        _FINGERPRINT_DOMAIN,
        fingerprint(graph, EquivalenceView.FUNCTIONAL),
        fingerprint(graph, EquivalenceView.IDENTIFIED),
        len(document),
        len(graph.tiers),
        len(blob_rows),
        len(attachment_rows),
    )
    existing = store._connection.execute(
        "SELECT digest, format_version, fp_domain, functional, identified, size, "
        "tier_count, blob_count, attachment_count FROM graph_facts WHERE digest = ?",
        (digest,),
    ).fetchone()
    if existing is None:
        store._connection.execute(
            "INSERT INTO graph_facts(digest, format_version, fp_domain, functional, "
            "identified, size, tier_count, blob_count, attachment_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            expected_fact,
        )
    elif existing != expected_fact:
        raise StoreCorrupt(f"graph facts for inspected object {digest} conflict")
    store._connection.execute(
        "DELETE FROM attachments WHERE graph_digest = ?", (digest,)
    )
    store._connection.execute(
        "DELETE FROM blob_metadata WHERE graph_digest = ?", (digest,)
    )
    store._connection.execute(
        "DELETE FROM blob_metadata_counts WHERE graph_digest = ?", (digest,)
    )
    store._connection.execute(
        "DELETE FROM blob_items WHERE graph_digest = ?", (digest,)
    )
    store._connection.execute("DELETE FROM graph_tiers WHERE digest = ?", (digest,))
    store._connection.executemany(
        "INSERT INTO graph_tiers(digest, position, tier, item_count) VALUES (?, ?, ?, ?)",
        (
            (digest, position, _qname_text(tier.declaration.name), len(tier.items))
            for position, tier in enumerate(graph.tiers)
        ),
    )
    store._connection.executemany(
        "INSERT INTO blob_items(graph_digest, tier, position, durable_id, sha256, "
        "size, media_type, schema) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        blob_rows,
    )
    store._connection.executemany(
        "INSERT INTO attachments(graph_digest, ordinal, declaration, durable_id, "
        "subject, blob_tier, blob_position, span_unit, span_offset, span_length, "
        "span_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        attachment_rows,
    )
    declarations = tuple(
        cast(tuple[str, str, str, str], row)
        for row in store._connection.execute(
            "SELECT name, media_type, attribute, value_type "
            "FROM declared_columns ORDER BY position"
        )
    )
    try:
        metadata_rows = _blob_metadata_rows(graph, digest, blob_rows, declarations)
    except ValueError as error:
        raise TgdbError(
            f"cannot index blob metadata for graph {digest}: {error}"
        ) from error
    store._connection.executemany(
        "INSERT INTO blob_metadata(graph_digest, tier, position, column, value_text, "
        "value_num, source) VALUES (?, ?, ?, ?, ?, ?, ?)",
        metadata_rows,
    )
    store._connection.executemany(
        "INSERT INTO blob_metadata_counts(graph_digest, column, row_count, row_digest) "
        "VALUES (?, ?, ?, ?)",
        _blob_metadata_count_rows(digest, declarations, metadata_rows),
    )


def _store_inspections(
    store: TgdbStore,
    results: tuple[InspectionResult, ...],
    graphs: dict[str, tuple[Graph, bytes]],
) -> int:
    """Atomically replace changed inspection records and nested graph facts."""
    changed = 0
    try:
        store._connection.execute("BEGIN IMMEDIATE")
        for result in results:
            rows = _inspection_rows(result)
            existing = tuple(
                store._connection.execute(
                    "SELECT digest, media_type, inspector, inspector_version, ordinal, "
                    "key, value_type, value FROM inspections WHERE digest = ? "
                    "AND inspector = ? AND inspector_version = ? ORDER BY ordinal",
                    (result.digest, result.inspector, result.inspector_version),
                )
            )
            if existing != rows:
                store._connection.execute(
                    "DELETE FROM inspections WHERE digest = ? AND inspector = ? "
                    "AND inspector_version = ?",
                    (result.digest, result.inspector, result.inspector_version),
                )
                store._connection.executemany(
                    "INSERT INTO inspections(digest, media_type, inspector, "
                    "inspector_version, ordinal, key, value_type, value) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
                changed += 1
        for digest, (graph, document) in graphs.items():
            _index_inspected_graph(store, graph, digest, document)
        store._connection.execute("COMMIT")
    except sqlite3.OperationalError as error:
        if store._connection.in_transaction:
            store._connection.execute("ROLLBACK")
        if _sqlite_busy(error):
            raise StoreBusy("tgdb catalog remained busy while inspecting") from error
        raise StoreCorrupt(f"cannot store inspections: {error}") from error
    except Exception:
        store._connection.execute("ROLLBACK")
        raise
    return changed


def _graph_with_inspections(
    graph: Graph,
    results: tuple[InspectionResult, ...],
) -> Graph:
    """Return a graph carrying matching inspector values as layer facts."""
    by_payload: dict[tuple[str, str], list[InspectionResult]] = {}
    for result in results:
        by_payload.setdefault((result.digest, result.media_type), []).append(result)
    editor = graph.edit()
    declarations = {item.name: item for item in graph.attribute_declarations}
    namespace_uris = {item.namespace for item in graph.namespaces}
    namespace_prefixes = {item.prefix for item in graph.namespaces}
    layers = {layer.name for layer in graph.layers}
    changed = False
    profile = BlobProfile(graph)
    tiers = {tier.declaration.name: tier for tier in graph.tiers}
    for reference, ref in profile.blobs():
        item = tiers[reference.tier].items[reference.index]
        values = {attribute.name: attribute for attribute in item.attributes}
        actual_media = cast(AttributeValue, values[_BLOB_MEDIA_TYPE]).lexical
        for result in by_payload.get((ref.sha256, actual_media), ()):
            for fact in result.facts:
                declaration = declarations.get(fact.value.name)
                if declaration is None:
                    if fact.value.name.namespace not in namespace_uris:
                        prefix = "inspection"
                        suffix = 2
                        while prefix in namespace_prefixes:
                            prefix = f"inspection{suffix}"
                            suffix += 1
                        editor.declare(
                            NamespaceDeclaration(prefix, fact.value.name.namespace)
                        )
                        namespace_uris.add(fact.value.name.namespace)
                        namespace_prefixes.add(prefix)
                    declaration = AttributeDeclaration(
                        fact.value.name, AttributeDomain.ITEM, fact.value.value_type
                    )
                    editor.declare(declaration)
                    declarations[fact.value.name] = declaration
                elif (
                    declaration.domain is not AttributeDomain.ITEM
                    or declaration.value_type is not fact.value.value_type
                ):
                    raise TgdbError(
                        f"inspector {result.inspector!r} value "
                        f"{fact.value.name} conflicts with its graph declaration"
                    )
                layer = LayerName(
                    fact.value.name.namespace,
                    f"{result.inspector}@{result.inspector_version}",
                )
                if layer not in layers:
                    editor.add_layer(layer)
                    layers.add(layer)
                editor.put_fact(
                    layer,
                    LayerFact(DurableItemRef(cast(str, item.durable_id)), fact.value),
                )
                changed = True
    return editor.freeze() if changed else graph


def _publish_inspections(
    store: TgdbStore,
    results: tuple[InspectionResult, ...],
) -> CommitReceipt | None:
    """Publish inspector facts into every matching active head in one commit."""
    handles = store.query(Query.all())
    if not handles or not results:
        return None
    transaction = store.write(annotations=EditAnnotations(stage="inspect"))
    try:
        for handle in handles:
            graph = handle.load()
            inspected = _graph_with_inspections(graph, results)
            if inspected != graph:
                transaction.publish(
                    handle.instance_uid,
                    inspected,
                    expected=handle.seq,
                    annotations=EditAnnotations(stage="inspect"),
                )
        if not transaction._versions:
            transaction.discard()
            return None
        return transaction.commit()
    except Exception:
        transaction.discard()
        raise


def _blob_index_rows(
    graph: Graph,
    digest: str,
) -> tuple[tuple[_BlobItemRow, ...], tuple[_AttachmentRow, ...]]:
    """Derive blob facts in graph order without opening any payload bytes."""
    if not any(
        declaration.namespace == BLOB_NAMESPACE for declaration in graph.namespaces
    ):
        return (), ()
    profile = BlobProfile(graph)
    tiers = {tier.declaration.name: tier for tier in graph.tiers}
    blob_rows: list[_BlobItemRow] = []
    attachment_rows: list[_AttachmentRow] = []
    for blob_item_ref, blob_ref in profile.blobs():
        item = tiers[blob_item_ref.tier].items[blob_item_ref.index]
        values = {
            attribute.name: attribute
            for attribute in item.attributes
            if isinstance(attribute, AttributeValue)
        }
        media_type = values[_BLOB_MEDIA_TYPE].lexical
        schema_value = values.get(_BLOB_SCHEMA)
        schema = None if schema_value is None else schema_value.lexical
        unit_value = values.get(_BLOB_UNIT)
        unit = None if unit_value is None else unit_value.lexical
        blob_rows.append(
            (
                digest,
                _qname_text(blob_item_ref.tier),
                blob_item_ref.index,
                cast(str, item.durable_id),
                blob_ref.sha256,
                blob_ref.size,
                media_type,
                schema,
            )
        )
        for relation_ref, subject_ref, span in profile.attachments(blob_item_ref):
            relation = graph.relations[relation_ref.index]
            attachment_rows.append(
                (
                    digest,
                    relation_ref.index,
                    _qname_text(relation.declaration),
                    cast(str, relation.durable_id),
                    _subject_text(graph, subject_ref),
                    _qname_text(blob_item_ref.tier),
                    blob_item_ref.index,
                    unit if span is not None else None,
                    None if span is None else span.offset,
                    None if span is None else span.length,
                    _structured_span_path(relation.attributes)
                    if span is None
                    else None,
                )
            )
    attachment_rows.sort(key=lambda row: row[1])
    return tuple(blob_rows), tuple(attachment_rows)


def _blob_metadata_rows(
    graph: Graph,
    digest: str,
    blob_rows: tuple[_BlobItemRow, ...],
    declarations: tuple[tuple[str, str, str, str], ...],
) -> tuple[_BlobMetadataRow, ...]:
    """Derive declared blob columns from graph values without payload reads."""
    if not declarations or not blob_rows:
        return ()
    tiers = {tier.declaration.name: tier for tier in graph.tiers}
    rows: list[_BlobMetadataRow] = []
    for blob_row in blob_rows:
        tier_name = _qname_from_text(blob_row[1])
        item_ref = ItemRef(tier_name, blob_row[2])
        item = tiers[tier_name].items[item_ref.index]
        base_values = {attribute.name: attribute for attribute in item.attributes}
        inspected: dict[QualifiedName, list[Attribute]] = {}
        for layer in graph.layers:
            for fact in layer.facts:
                if not isinstance(fact.subject, ItemRef | DurableItemRef):
                    continue
                if graph.resolve_item(fact.subject) == item_ref:
                    inspected.setdefault(fact.value.name, []).append(fact.value)
        for column, media_type, attribute_text, value_type in declarations:
            if not _media_type_matches(media_type, blob_row[6]):
                continue
            attribute_name = _qname_from_text(attribute_text)
            value = base_values.get(attribute_name)
            source: Literal["declared", "inspected"]
            if value is not None:
                source = "declared"
            else:
                readings = inspected.get(attribute_name, [])
                if not readings:
                    continue
                indexed = {_index_value(reading)[:3] for reading in readings}
                if len(indexed) != 1:
                    raise ValueError(
                        f"blob item {item_ref} has conflicting inspected readings "
                        f"for column {column!r}"
                    )
                value = readings[0]
                source = "inspected"
            actual_type, value_text, value_num, _value_digest = _index_value(value)
            if actual_type != value_type:
                raise ValueError(
                    f"blob item {item_ref} column {column!r} requires "
                    f"{value_type}, not {actual_type}"
                )
            rows.append(
                (
                    digest,
                    blob_row[1],
                    blob_row[2],
                    column,
                    value_text,
                    value_num,
                    source,
                )
            )
    return tuple(rows)


def _blob_metadata_row_digest(rows: tuple[_BlobMetadataRow, ...]) -> str:
    """Hash one column's ordered derived rows for drift detection."""
    data = [
        [tier, position, value_text, value_num, source]
        for _digest, tier, position, _column, value_text, value_num, source in sorted(
            rows, key=lambda row: (row[1], row[2])
        )
    ]
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _blob_metadata_count_rows(
    digest: str,
    declarations: tuple[tuple[str, str, str, str], ...],
    rows: tuple[_BlobMetadataRow, ...],
) -> tuple[_BlobMetadataCountRow, ...]:
    """Record each declared column's row count and content digest."""
    grouped: dict[str, list[_BlobMetadataRow]] = {
        name: [] for name, _media_type, _attribute, _value_type in declarations
    }
    for row in rows:
        grouped[row[3]].append(row)
    return tuple(
        (
            digest,
            name,
            len(grouped[name]),
            _blob_metadata_row_digest(tuple(grouped[name])),
        )
        for name, _media_type, _attribute, _value_type in declarations
    )


def _subject_text(graph: Graph, reference: ItemRef) -> str:
    """Encode one attachment subject without losing a stable item id."""
    tier = next(tier for tier in graph.tiers if tier.declaration.name == reference.tier)
    durable_id = tier.items[reference.index].durable_id
    value: dict[str, object]
    if durable_id is None:
        value = {"item": reference.to_data()}
    else:
        value = {"durable_id": durable_id}
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _structured_span_path(attributes: tuple[Attribute, ...]) -> str | None:
    """Preserve schema-defined JSON coordinates without interpreting them."""
    values = tuple(
        attribute
        for attribute in attributes
        if isinstance(attribute, JsonAttributeValue)
    )
    if not values:
        return None
    data: object
    if len(values) == 1:
        data = values[0].to_value()
    else:
        data = [value.to_data() for value in values]
    return json.dumps(
        data,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _item_index_rows(
    graph: Graph,
    digest: str,
    declarations: tuple[tuple[str, str, str], ...],
) -> tuple[_ItemIndexRow, ...]:
    """Derive ordered item-value rows for declarations that match this graph."""
    selected = tuple(
        (name, tier_text, _qname_from_text(attribute_text))
        for name, tier_text, attribute_text in declarations
    )
    rows: list[_ItemIndexRow] = []
    for tier_position, tier in enumerate(graph.tiers):
        tier_text = _qname_text(tier.declaration.name)
        matching = tuple(
            (name, attribute)
            for name, declared_tier, attribute in selected
            if declared_tier == tier_text
        )
        if not matching:
            continue
        for item_position, item in enumerate(tier.items):
            attributes = {attribute.name: attribute for attribute in item.attributes}
            for index_name, attribute_name in matching:
                value = attributes.get(attribute_name)
                if value is None:
                    continue
                value_type, value_text, value_num, value_digest = _index_value(value)
                rows.append(
                    (
                        digest,
                        index_name,
                        tier_position,
                        item_position,
                        value_type,
                        value_text,
                        value_num,
                        value_digest,
                    )
                )
    return tuple(rows)


def _item_index_count_rows(
    digest: str,
    declarations: tuple[tuple[str, str, str], ...],
    rows: tuple[_ItemIndexRow, ...],
) -> tuple[_ItemIndexCountRow, ...]:
    """Record the expected row count, including zero, for each declaration."""
    counts = {name: 0 for name, _tier, _attribute in declarations}
    for row in rows:
        counts[row[1]] += 1
    return tuple((digest, name, count) for name, count in counts.items())


def _derive_indexes(
    store: TgdbStore,
) -> tuple[
    dict[str, tuple[str, str, str, str, str, int, int, int, int]],
    tuple[tuple[str, int, str, int], ...],
    tuple[_BlobItemRow, ...],
    tuple[_AttachmentRow, ...],
    tuple[_BlobMetadataRow, ...],
    tuple[_BlobMetadataCountRow, ...],
    tuple[_ItemIndexCountRow, ...],
    tuple[_ItemIndexRow, ...],
]:
    """Recompute graph, blob, tier, and declared-item derived rows."""
    try:
        version_digests = {
            cast(str, row[0])
            for row in store._connection.execute(
                "SELECT DISTINCT graph_digest FROM versions ORDER BY graph_digest"
            )
        }
        inspected_digests = {
            cast(str, row[0])
            for row in store._connection.execute(
                "SELECT DISTINCT digest FROM inspections WHERE inspector = ? "
                "AND inspector_version = ?",
                (_TIERGRAPH_INSPECTOR_NAME, _TIERGRAPH_INSPECTOR_VERSION),
            )
        }
        digests = tuple(sorted(version_digests | inspected_digests))
    except sqlite3.DatabaseError as error:
        raise StoreCorrupt(f"cannot enumerate stored graphs: {error}") from error
    facts: dict[str, tuple[str, str, str, str, str, int, int, int, int]] = {}
    tiers: list[tuple[str, int, str, int]] = []
    blobs: list[_BlobItemRow] = []
    attachments: list[_AttachmentRow] = []
    metadata: list[_BlobMetadataRow] = []
    metadata_counts: list[_BlobMetadataCountRow] = []
    item_counts: list[_ItemIndexCountRow] = []
    items: list[_ItemIndexRow] = []
    declarations = tuple(
        cast(tuple[str, str, str, int], row)
        for row in store._connection.execute(
            "SELECT name, tier, attribute, history FROM declared_indexes "
            "ORDER BY position"
        )
    )
    column_declarations = tuple(
        cast(tuple[str, str, str, str], row)
        for row in store._connection.execute(
            "SELECT name, media_type, attribute, value_type "
            "FROM declared_columns ORDER BY position"
        )
    )
    head_digests = {
        cast(str, row[0])
        for row in store._connection.execute(
            "SELECT DISTINCT v.graph_digest FROM instances AS i "
            "JOIN versions AS v ON v.id = i.head_version"
        )
    }
    for digest in digests:
        with store._open_object(digest) as source:
            document = source.read()
        try:
            graph = loads(document)
        except Refusal as error:
            raise StoreCorrupt(
                f"cannot reindex graph object {digest}: {error}"
            ) from error
        if dumps(graph).encode() != document:
            raise StoreCorrupt(f"graph object {digest} is not in canonical form")
        tiers.extend(
            (digest, position, _qname_text(tier.declaration.name), len(tier.items))
            for position, tier in enumerate(graph.tiers)
        )
        try:
            blob_rows, attachment_rows = _blob_index_rows(graph, digest)
        except ValueError as error:
            raise StoreCorrupt(
                f"cannot reindex blob facts for graph object {digest}: {error}"
            ) from error
        blobs.extend(blob_rows)
        attachments.extend(attachment_rows)
        try:
            metadata_rows = _blob_metadata_rows(
                graph, digest, blob_rows, column_declarations
            )
        except ValueError as error:
            raise StoreCorrupt(
                f"cannot reindex blob metadata for graph object {digest}: {error}"
            ) from error
        metadata.extend(metadata_rows)
        metadata_counts.extend(
            _blob_metadata_count_rows(digest, column_declarations, metadata_rows)
        )
        facts[digest] = (
            digest,
            FORMAT_VERSION,
            _FINGERPRINT_DOMAIN,
            fingerprint(graph, EquivalenceView.FUNCTIONAL),
            fingerprint(graph, EquivalenceView.IDENTIFIED),
            len(document),
            len(graph.tiers),
            len(blob_rows),
            len(attachment_rows),
        )
        applicable = tuple(
            (name, tier, attribute)
            for name, tier, attribute, history in declarations
            if digest in version_digests and (history or digest in head_digests)
        )
        item_rows = _item_index_rows(graph, digest, applicable)
        item_counts.extend(_item_index_count_rows(digest, applicable, item_rows))
        items.extend(item_rows)
    return (
        facts,
        tuple(tiers),
        tuple(blobs),
        tuple(attachments),
        tuple(metadata),
        tuple(metadata_counts),
        tuple(item_counts),
        tuple(items),
    )


def _compare_derived_indexes(  # noqa: PLR0915 -- each derived table stays explicit
    connection: sqlite3.Connection,
    expected_facts: dict[str, tuple[str, str, str, str, str, int, int, int, int]],
    expected_tiers: tuple[tuple[str, int, str, int], ...],
    expected_blobs: tuple[_BlobItemRow, ...],
    expected_attachments: tuple[_AttachmentRow, ...],
    expected_metadata: tuple[_BlobMetadataRow, ...],
    expected_metadata_counts: tuple[_BlobMetadataCountRow, ...],
    expected_item_counts: tuple[_ItemIndexCountRow, ...],
    expected_items: tuple[_ItemIndexRow, ...],
) -> None:
    """Refuse the first missing, unexpected, or drifted derived row."""
    metadata = dict(
        connection.execute(
            "SELECT key, value FROM store_meta "
            "WHERE key IN ('index_version', 'fingerprint_domain')"
        )
    )
    if metadata.get("index_version") != str(_INDEX_VERSION):
        raise StoreCorrupt("derived index version is stale; run reindex --rebuild")
    if metadata.get("fingerprint_domain") != _FINGERPRINT_DOMAIN:
        raise StoreCorrupt("derived fingerprint domain is stale; run reindex --rebuild")
    actual_facts = {
        cast(str, row[0]): cast(tuple[str, str, str, str, str, int, int, int, int], row)
        for row in connection.execute(
            "SELECT digest, format_version, fp_domain, functional, identified, size, "
            "tier_count, blob_count, attachment_count "
            "FROM graph_facts ORDER BY digest"
        )
    }
    for digest, expected in expected_facts.items():
        actual = actual_facts.pop(digest, None)
        if actual is None:
            raise StoreCorrupt(f"graph_facts row {digest!r} is missing")
        if actual != expected:
            raise StoreCorrupt(f"graph_facts row {digest!r} differs from its document")
    if actual_facts:
        digest = next(iter(actual_facts))
        raise StoreCorrupt(f"graph_facts row {digest!r} is unexpected")
    actual_tiers = {
        (cast(str, row[0]), cast(int, row[1])): cast(tuple[str, int, str, int], row)
        for row in connection.execute(
            "SELECT digest, position, tier, item_count FROM graph_tiers "
            "ORDER BY digest, position"
        )
    }
    for tier_expected in expected_tiers:
        tier_key = (tier_expected[0], tier_expected[1])
        tier_actual = actual_tiers.pop(tier_key, None)
        if tier_actual is None:
            raise StoreCorrupt(f"graph_tiers row {tier_key!r} is missing")
        if tier_actual != tier_expected:
            raise StoreCorrupt(
                f"graph_tiers row {tier_key!r} differs from its document"
            )
    if actual_tiers:
        tier_key = next(iter(actual_tiers))
        raise StoreCorrupt(f"graph_tiers row {tier_key!r} is unexpected")
    actual_blobs = {
        (cast(str, row[0]), cast(str, row[1]), cast(int, row[2])): cast(
            _BlobItemRow, row
        )
        for row in connection.execute(
            "SELECT graph_digest, tier, position, durable_id, sha256, size, "
            "media_type, schema FROM blob_items "
            "ORDER BY graph_digest, tier, position"
        )
    }
    for blob_expected in expected_blobs:
        blob_key = (blob_expected[0], blob_expected[1], blob_expected[2])
        blob_actual = actual_blobs.pop(blob_key, None)
        if blob_actual is None:
            raise StoreCorrupt(f"blob_items row {blob_key!r} is missing")
        if blob_actual != blob_expected:
            raise StoreCorrupt(f"blob_items row {blob_key!r} differs from its document")
    if actual_blobs:
        blob_key = next(iter(actual_blobs))
        raise StoreCorrupt(f"blob_items row {blob_key!r} is unexpected")
    actual_attachments = {
        (cast(str, row[0]), cast(int, row[1])): cast(_AttachmentRow, row)
        for row in connection.execute(
            "SELECT graph_digest, ordinal, declaration, durable_id, subject, "
            "blob_tier, blob_position, span_unit, span_offset, span_length, "
            "span_path FROM attachments ORDER BY graph_digest, ordinal"
        )
    }
    for attachment_expected in expected_attachments:
        attachment_key = (attachment_expected[0], attachment_expected[1])
        attachment_actual = actual_attachments.pop(attachment_key, None)
        if attachment_actual is None:
            raise StoreCorrupt(f"attachments row {attachment_key!r} is missing")
        if attachment_actual != attachment_expected:
            raise StoreCorrupt(
                f"attachments row {attachment_key!r} differs from its document"
            )
    if actual_attachments:
        attachment_key = next(iter(actual_attachments))
        raise StoreCorrupt(f"attachments row {attachment_key!r} is unexpected")
    actual_metadata_counts = {
        (cast(str, row[0]), cast(str, row[1])): cast(_BlobMetadataCountRow, row)
        for row in connection.execute(
            "SELECT graph_digest, column, row_count, row_digest "
            "FROM blob_metadata_counts ORDER BY graph_digest, column"
        )
    }
    for metadata_count_expected in expected_metadata_counts:
        metadata_count_key = (
            metadata_count_expected[0],
            metadata_count_expected[1],
        )
        metadata_count_actual = actual_metadata_counts.pop(metadata_count_key, None)
        if metadata_count_actual is None:
            raise StoreCorrupt(
                f"blob_metadata_counts row {metadata_count_key!r} is missing"
            )
        if metadata_count_actual != metadata_count_expected:
            raise StoreCorrupt(
                f"blob_metadata_counts row {metadata_count_key!r} "
                "differs from its document"
            )
    if actual_metadata_counts:
        metadata_count_key = next(iter(actual_metadata_counts))
        raise StoreCorrupt(
            f"blob_metadata_counts row {metadata_count_key!r} is unexpected"
        )
    actual_metadata = {
        (
            cast(str, row[0]),
            cast(str, row[1]),
            cast(int, row[2]),
            cast(str, row[3]),
        ): cast(_BlobMetadataRow, row)
        for row in connection.execute(
            "SELECT graph_digest, tier, position, column, value_text, "
            "value_num, source FROM blob_metadata "
            "ORDER BY graph_digest, tier, position, column"
        )
    }
    for metadata_expected in expected_metadata:
        metadata_key = (
            metadata_expected[0],
            metadata_expected[1],
            metadata_expected[2],
            metadata_expected[3],
        )
        metadata_actual = actual_metadata.pop(metadata_key, None)
        if metadata_actual is None:
            raise StoreCorrupt(f"blob_metadata row {metadata_key!r} is missing")
        if metadata_actual != metadata_expected:
            raise StoreCorrupt(
                f"blob_metadata row {metadata_key!r} differs from its document"
            )
    if actual_metadata:
        metadata_key = next(iter(actual_metadata))
        raise StoreCorrupt(f"blob_metadata row {metadata_key!r} is unexpected")
    actual_item_counts = {
        (cast(str, row[0]), cast(str, row[1])): cast(_ItemIndexCountRow, row)
        for row in connection.execute(
            "SELECT graph_digest, index_name, row_count FROM item_index_counts "
            "ORDER BY graph_digest, index_name"
        )
    }
    for count_expected in expected_item_counts:
        count_key = (count_expected[0], count_expected[1])
        count_actual = actual_item_counts.pop(count_key, None)
        if count_actual is None:
            raise StoreCorrupt(f"item_index_counts row {count_key!r} is missing")
        if count_actual != count_expected:
            raise StoreCorrupt(
                f"item_index_counts row {count_key!r} differs from its document"
            )
    if actual_item_counts:
        count_key = next(iter(actual_item_counts))
        raise StoreCorrupt(f"item_index_counts row {count_key!r} is unexpected")
    actual_items = {
        (cast(str, row[0]), cast(str, row[1]), cast(int, row[3])): cast(
            _ItemIndexRow, row
        )
        for row in connection.execute(
            "SELECT graph_digest, index_name, tier_position, item_position, "
            "value_type, value_text, value_num, value_digest FROM item_index "
            "ORDER BY graph_digest, index_name, item_position"
        )
    }
    for item_expected in expected_items:
        item_key = (item_expected[0], item_expected[1], item_expected[3])
        item_actual = actual_items.pop(item_key, None)
        if item_actual is None:
            raise StoreCorrupt(f"item_index row {item_key!r} is missing")
        if item_actual != item_expected:
            raise StoreCorrupt(f"item_index row {item_key!r} differs from its document")
    if actual_items:
        item_key = next(iter(actual_items))
        raise StoreCorrupt(f"item_index row {item_key!r} is unexpected")


_VERSION_SELECT = (
    "SELECT v.seq, v.commit_seq, v.graph_digest, f.functional, f.identified, "
    "v.stage, v.id, v.iteration, v.patch_digest, v.transition, v.reason "
    "FROM versions AS v "
    "LEFT JOIN graph_facts AS f ON f.digest = v.graph_digest"
)


def _version_row(
    connection: sqlite3.Connection,
    instance_id: int,
    *,
    head_version: int | None,
    seq: int | None,
) -> (
    tuple[
        int,
        int,
        str,
        str,
        str,
        str | None,
        int,
        int | None,
        str | None,
        Literal["initial", "patch", "snapshot"],
        str | None,
    ]
    | None
):
    """Return one version and its graph facts by head id or declared sequence."""
    if head_version is not None:
        condition = "v.id = ? AND v.instance_id = ?"
        parameters = (head_version, instance_id)
    elif seq is not None:
        condition = "v.instance_id = ? AND v.seq = ?"
        parameters = (instance_id, seq)
    else:
        return None
    row = connection.execute(
        _VERSION_SELECT + " WHERE " + condition, parameters
    ).fetchone()
    return cast(
        tuple[
            int,
            int,
            str,
            str,
            str,
            str | None,
            int,
            int | None,
            str | None,
            Literal["initial", "patch", "snapshot"],
            str | None,
        ]
        | None,
        row,
    )


def _version_handle(
    store: TgdbStore,
    instance: tuple[int, bytes, int, str, int, int, int | None, int | None],
    version: tuple[
        int,
        int,
        str,
        str,
        str,
        str | None,
        int,
        int | None,
        str | None,
        Literal["initial", "patch", "snapshot"],
        str | None,
    ],
    *,
    collection_name: str | None = None,
) -> VersionHandle:
    """Bind one immutable version row to its verified object loader."""
    if collection_name is None:
        collection = store._connection.execute(
            "SELECT name FROM collections WHERE id = ?", (instance[2],)
        ).fetchone()
        if collection is None:
            raise StoreCorrupt(f"instance {instance[3]!r} has no collection")
        collection_name = cast(str, collection[0])
    _validate_digest(version[2])
    _validate_fingerprint(version[3], "functional", version[2])
    _validate_fingerprint(version[4], "identified", version[2])
    if version[8] is not None:
        _validate_digest(version[8])
    return VersionHandle(
        collection=collection_name,
        instance_uid=instance[1],
        name=instance[3],
        position=instance[4],
        seq=version[0],
        commit_seq=version[1],
        graph_digest=version[2],
        patch_digest=version[8],
        transition=version[9],
        reason=version[10],
        functional=version[3],
        identified=version[4],
        stage=version[5],
        iteration=version[7],
        _store=store,
    )


def _set_positions(
    connection: sqlite3.Connection,
    table: Literal["collections", "instances"],
    positions: dict[bytes, int],
) -> None:
    """Set a declared-order mapping without transient uniqueness collisions."""
    if not positions:
        return
    maximum = cast(
        int,
        connection.execute(
            f"SELECT coalesce(max(position), -1) FROM {table}"
        ).fetchone()[0],
    )
    offset = maximum + len(positions) + 1
    connection.executemany(
        f"UPDATE {table} SET position = position + ? WHERE uid = ?",
        ((offset, uid) for uid in positions),
    )
    connection.executemany(
        f"UPDATE {table} SET position = ? WHERE uid = ?",
        ((position, uid) for uid, position in positions.items()),
    )


def _touch(kind: Literal["collection", "instance"], uid: bytes) -> str:
    """Return one stable internal staleness key."""
    return f"{kind}:{uid.hex()}"


def _index_touch(name: str) -> str:
    """Return one stable staleness key for a declared item index."""
    return f"index:{name}"


def _index_action(
    action: Literal["declare_index"],
    name: str,
    position: int,
    tier: QualifiedName,
    attribute: QualifiedName,
    history: bool,
) -> dict[str, object]:
    """Build one complete declaration action for replay and undo."""
    return {
        "action": action,
        "attribute": _qname_text(attribute),
        "history": history,
        "name": name,
        "position": position,
        "tier": _qname_text(tier),
        "touches": [_index_touch(name)],
    }


def _drop_index_action(name: str) -> dict[str, object]:
    """Build one stable index-removal action."""
    return {
        "action": "drop_index",
        "name": name,
        "touches": [_index_touch(name)],
    }


def _index_action_data(
    action: dict[str, object],
) -> tuple[str, int, QualifiedName, QualifiedName, bool]:
    """Validate and decode one recorded index declaration."""
    name = action.get("name")
    position = action.get("position")
    tier = action.get("tier")
    attribute = action.get("attribute")
    history = action.get("history")
    _validate_name(name)
    name = cast(str, name)
    if isinstance(position, bool) or not isinstance(position, int) or position < 0:
        raise StoreCorrupt("recorded item index position is malformed")
    if not isinstance(tier, str) or not isinstance(attribute, str):
        raise StoreCorrupt("recorded item index names are malformed")
    if not isinstance(history, bool):
        raise StoreCorrupt("recorded item index history flag is malformed")
    try:
        return (
            name,
            position,
            _qname_from_text(tier),
            _qname_from_text(attribute),
            history,
        )
    except (TypeError, ValueError) as error:
        raise StoreCorrupt("recorded item index names are malformed") from error


def _column_touch(name: str) -> str:
    """Return one stable staleness key for a declared blob column."""
    return f"column:{name}"


def _column_action(
    action: Literal["declare_column"],
    name: str,
    position: int,
    media_type: str,
    attribute: QualifiedName,
    value_type: str,
) -> dict[str, object]:
    """Build one complete blob-column action for replay and undo."""
    return {
        "action": action,
        "attribute": _qname_text(attribute),
        "media_type": media_type,
        "name": name,
        "position": position,
        "touches": [_column_touch(name)],
        "value_type": value_type,
    }


def _drop_column_action(name: str) -> dict[str, object]:
    """Build one stable blob-column removal action."""
    return {
        "action": "drop_column",
        "name": name,
        "touches": [_column_touch(name)],
    }


def _column_action_data(
    action: dict[str, object],
) -> tuple[str, int, str, QualifiedName, str]:
    """Validate and decode one recorded blob-column declaration."""
    name = action.get("name")
    position = action.get("position")
    media_type = action.get("media_type")
    attribute = action.get("attribute")
    value_type = action.get("value_type")
    _validate_name(name)
    if isinstance(position, bool) or not isinstance(position, int) or position < 0:
        raise StoreCorrupt("recorded blob column position is malformed")
    if not isinstance(media_type, str) or not isinstance(attribute, str):
        raise StoreCorrupt("recorded blob column names are malformed")
    if not isinstance(value_type, str):
        raise StoreCorrupt("recorded blob column value type is malformed")
    try:
        _validate_column_media_type(media_type)
        _validate_column_value_type(value_type)
        qualified = _qname_from_text(attribute)
    except (TypeError, ValueError) as error:
        raise StoreCorrupt("recorded blob column declaration is malformed") from error
    return cast(str, name), position, media_type, qualified, value_type


def _name_action(
    kind: Literal["collection", "instance"], uid: bytes, name: str
) -> dict[str, object]:
    """Build one bounded rename operation."""
    return {
        "action": f"rename_{kind}",
        "name": name,
        "touches": [_touch(kind, uid)],
        "uid": uid.hex(),
    }


def _positions_action(
    kind: Literal["collection", "instance"],
    positions: dict[bytes, int],
    prior: dict[bytes, int],
) -> dict[str, object]:
    """Build one ordered-position operation for identities that actually move."""
    changed = {
        uid: position
        for uid, position in positions.items()
        if prior.get(uid) != position
    }
    return {
        "action": f"set_{kind}_positions",
        "positions": {uid.hex(): position for uid, position in changed.items()},
        "touches": [_touch(kind, uid) for uid in changed],
    }


def _action_uid(action: dict[str, object]) -> bytes:
    """Read the stable identity from one decoded operation."""
    value = action.get("uid")
    if not isinstance(value, str):
        raise StoreCorrupt("recorded catalog identity is malformed")
    return _uid_from_hex(value)


def _action_touches(  # noqa: PLR0915 -- action validation is one ledger contract
    action: dict[str, object],
) -> tuple[str, ...]:
    """Read and validate one operation's complete ordered staleness keys."""
    value = action.get("touches")
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise StoreCorrupt("recorded catalog touches are malformed")
    touches = tuple(cast(list[str], value))
    identity_kinds: dict[str, Literal["collection", "instance"]] = {
        "create_collection": "collection",
        "rename_collection": "collection",
        "retire_collection": "collection",
        "restore_collection": "collection",
        "create_instance": "instance",
        "rename_instance": "instance",
        "retire_instance": "instance",
        "restore_instance": "instance",
        "publish": "instance",
        "apply": "instance",
        "revert": "instance",
        "republish": "instance",
    }
    action_name = cast(str, action.get("action"))
    if action_name in {"declare_index", "drop_index"}:
        name = action.get("name")
        try:
            _validate_name(name)
        except (TypeError, ValueError) as error:
            raise StoreCorrupt("recorded item index name is malformed") from error
        index_expected = (_index_touch(cast(str, name)),)
        if action_name == "declare_index":
            _index_action_data(action)
        if touches != index_expected:
            raise StoreCorrupt("recorded catalog touches do not match its operation")
        return touches
    if action_name in {"declare_column", "drop_column"}:
        name = action.get("name")
        try:
            _validate_name(name)
        except (TypeError, ValueError) as error:
            raise StoreCorrupt("recorded blob column name is malformed") from error
        column_expected = (_column_touch(cast(str, name)),)
        if action_name == "declare_column":
            _column_action_data(action)
        if touches != column_expected:
            raise StoreCorrupt("recorded catalog touches do not match its operation")
        return touches
    identity_kind = identity_kinds.get(action_name)
    expected: tuple[str, ...]
    if identity_kind is not None:
        expected = (_touch(identity_kind, _action_uid(action)),)
        if action_name in {"publish", "apply", "revert", "republish"}:
            version_id = action.get("version_id")
            if (
                isinstance(version_id, bool)
                or not isinstance(version_id, int)
                or version_id <= 0
            ):
                raise StoreCorrupt("recorded version operation is malformed")
            if action_name == "republish":
                patch_digest = action.get("patch_digest")
                if "patch_digest" not in action or (
                    patch_digest is not None and not isinstance(patch_digest, str)
                ):
                    raise StoreCorrupt("recorded version operation is malformed")
                if isinstance(patch_digest, str):
                    _validate_digest(patch_digest)
    elif action.get("action") in {
        "set_collection_positions",
        "set_instance_positions",
    }:
        positions = action.get("positions")
        if not isinstance(positions, dict) or not positions:
            raise StoreCorrupt("recorded catalog positions are malformed")
        item_kind: Literal["collection", "instance"] = (
            "collection"
            if action.get("action") == "set_collection_positions"
            else "instance"
        )
        expected = tuple(
            _touch(item_kind, _uid_from_hex(uid_text))
            for uid_text in positions
            if isinstance(uid_text, str)
        )
        if len(expected) != len(positions):
            raise StoreCorrupt("recorded catalog identity is malformed")
    else:
        return touches
    if len(touches) != len(expected) or set(touches) != set(expected):
        raise StoreCorrupt("recorded catalog touches do not match its operation")
    return touches


def _encode_action(action: dict[str, object]) -> str:
    """Encode an internal operation in its sole strict JSON spelling."""
    return json.dumps(
        action,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _decode_action(value: object) -> dict[str, object]:
    """Decode one internal operation while requiring a JSON object."""
    if not isinstance(value, str):
        raise StoreCorrupt("catalog commit operation is not text")
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as error:
        raise StoreCorrupt("catalog commit operation is malformed") from error
    if not isinstance(decoded, dict) or any(
        not isinstance(key, str) for key in decoded
    ):
        raise StoreCorrupt("catalog commit operation is malformed")
    return cast(dict[str, object], decoded)


def _version_text(version: tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in version)


def _remove_failed_store(root: Path) -> None:
    """Remove only catalog files created by a failed initialization."""
    for suffix in ("-shm", "-wal", ""):
        with suppress(OSError):
            (root / f"{_CATALOG_NAME}{suffix}").unlink()
    for path in (
        root / _STAGING_NAME,
        root / _OBJECTS_NAME / _HASH_NAME,
        root / _OBJECTS_NAME,
    ):
        with suppress(OSError):
            path.rmdir()
    with suppress(OSError):
        root.rmdir()


def _initialize_layout(root: Path) -> None:
    """Create the fixed empty object-pool directories for a new store."""
    objects = root / _OBJECTS_NAME
    hashes = objects / _HASH_NAME
    staging = root / _STAGING_NAME
    objects.mkdir()
    hashes.mkdir()
    staging.mkdir()
    _fsync_directory(objects)
    _fsync_directory(root)


def _require_sqlite() -> None:
    found = sqlite3.sqlite_version_info
    if found < _MINIMUM_SQLITE:
        raise SqliteTooOld(found)


def _connect(
    catalog: Path,
    mode: Literal["ro", "rw"],
    limits: TgdbLimits,
    *,
    create: bool = False,
) -> sqlite3.Connection:
    sqlite_mode = "rwc" if create else mode
    uri = f"{catalog.resolve().as_uri()}?mode={sqlite_mode}"
    connection = sqlite3.connect(
        uri,
        uri=True,
        timeout=limits.busy_timeout_ms / 1_000,
        autocommit=True,
    )
    connection.execute("PRAGMA trusted_schema = OFF")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {limits.busy_timeout_ms}")
    if mode == "ro":
        connection.execute("PRAGMA query_only = ON")
    return connection


def _configure_writable(connection: sqlite3.Connection) -> None:
    journal_mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
    if journal_mode != "wal":
        connection.close()
        raise TgdbError(f"SQLite refused WAL journal mode: {journal_mode}")
    connection.execute("PRAGMA synchronous = FULL")
    connection.execute(f"PRAGMA wal_autocheckpoint = {_WAL_AUTOCHECKPOINT_PAGES}")


def _initialize(connection: sqlite3.Connection, limits: TgdbLimits) -> None:
    metadata = (
        ("store_uid", secrets.token_hex(16)),
        ("layout_version", str(_LAYOUT_VERSION)),
        ("index_version", str(_INDEX_VERSION)),
        ("inline_threshold", str(limits.inline_threshold)),
        ("fingerprint_domain", _FINGERPRINT_DOMAIN),
    )
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            "CREATE TABLE store_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL) STRICT"
        )
        connection.execute(
            "CREATE TABLE objects("
            "digest TEXT PRIMARY KEY "
            "CHECK(length(digest) = 64 AND digest NOT GLOB '*[^0-9a-f]*'), "
            "size INTEGER NOT NULL CHECK(size >= 0), "
            "residency TEXT NOT NULL CHECK(residency IN ('inline', 'file')), "
            "data BLOB, "
            "CHECK((residency = 'inline' AND data IS NOT NULL) OR "
            "(residency = 'file' AND data IS NULL))"
            ") STRICT"
        )
        connection.execute(
            "CREATE TABLE commits("
            "seq INTEGER PRIMARY KEY, "
            "kind TEXT NOT NULL CHECK(length(kind) > 0), "
            "annotations TEXT"
            ") STRICT"
        )
        connection.execute(
            "CREATE TABLE commit_ops("
            "commit_seq INTEGER NOT NULL REFERENCES commits(seq), "
            "ordinal INTEGER NOT NULL CHECK(ordinal >= 0), "
            "op TEXT NOT NULL CHECK(length(op) > 0), "
            "forward TEXT NOT NULL, "
            "inverse TEXT NOT NULL, "
            "PRIMARY KEY(commit_seq, ordinal)"
            ") STRICT"
        )
        connection.execute(
            "CREATE TABLE collections("
            "id INTEGER PRIMARY KEY, "
            "uid BLOB NOT NULL UNIQUE CHECK(length(uid) = 16), "
            "name TEXT NOT NULL UNIQUE CHECK(length(name) > 0), "
            "position INTEGER NOT NULL UNIQUE CHECK(position >= 0), "
            "retired_commit INTEGER REFERENCES commits(seq)"
            ") STRICT"
        )
        connection.execute(
            "CREATE TABLE instances("
            "id INTEGER PRIMARY KEY, "
            "uid BLOB NOT NULL UNIQUE CHECK(length(uid) = 16), "
            "collection_id INTEGER NOT NULL REFERENCES collections(id), "
            "name TEXT NOT NULL CHECK(length(name) > 0), "
            "position INTEGER NOT NULL CHECK(position >= 0), "
            "head_version INTEGER REFERENCES versions(id), "
            "generation INTEGER NOT NULL CHECK(generation >= 0), "
            "retired_commit INTEGER REFERENCES commits(seq), "
            "UNIQUE(collection_id, name), "
            "UNIQUE(collection_id, position)"
            ") STRICT"
        )
        connection.execute(
            "CREATE TABLE declared_indexes("
            "name TEXT PRIMARY KEY CHECK(length(name) > 0), "
            "position INTEGER NOT NULL UNIQUE CHECK(position >= 0), "
            "tier TEXT NOT NULL CHECK(length(tier) > 0), "
            "attribute TEXT NOT NULL CHECK(length(attribute) > 0), "
            "history INTEGER NOT NULL CHECK(history IN (0, 1))"
            ") STRICT"
        )
        connection.execute(
            "CREATE TABLE declared_columns("
            "name TEXT PRIMARY KEY CHECK(length(name) > 0), "
            "position INTEGER NOT NULL UNIQUE CHECK(position >= 0), "
            "media_type TEXT NOT NULL CHECK(length(media_type) > 0), "
            "attribute TEXT NOT NULL CHECK(length(attribute) > 0), "
            "value_type TEXT NOT NULL CHECK(length(value_type) > 0)"
            ") STRICT"
        )
        connection.execute(
            "CREATE TABLE inspections("
            "digest TEXT NOT NULL REFERENCES objects(digest) ON DELETE CASCADE, "
            "media_type TEXT NOT NULL CHECK(length(media_type) > 0), "
            "inspector TEXT NOT NULL CHECK(length(inspector) > 0), "
            "inspector_version TEXT NOT NULL CHECK(length(inspector_version) > 0), "
            "ordinal INTEGER NOT NULL CHECK(ordinal >= 0), "
            "key TEXT NOT NULL CHECK(length(key) > 0), "
            "value_type TEXT NOT NULL CHECK(length(value_type) > 0), "
            "value TEXT NOT NULL, "
            "PRIMARY KEY(digest, inspector, inspector_version, ordinal)"
            ") STRICT"
        )
        connection.execute(
            "CREATE TABLE graph_facts("
            "digest TEXT PRIMARY KEY REFERENCES objects(digest), "
            "format_version TEXT NOT NULL, "
            "fp_domain TEXT NOT NULL, "
            "functional TEXT NOT NULL "
            "CHECK(length(functional) = 64 AND functional NOT GLOB '*[^0-9a-f]*'), "
            "identified TEXT NOT NULL "
            "CHECK(length(identified) = 64 AND identified NOT GLOB '*[^0-9a-f]*'), "
            "size INTEGER NOT NULL CHECK(size >= 0), "
            "tier_count INTEGER NOT NULL CHECK(tier_count >= 0), "
            "blob_count INTEGER NOT NULL CHECK(blob_count >= 0), "
            "attachment_count INTEGER NOT NULL CHECK(attachment_count >= 0)"
            ") STRICT"
        )
        connection.execute(
            "CREATE TABLE graph_tiers("
            "digest TEXT NOT NULL REFERENCES graph_facts(digest) ON DELETE CASCADE, "
            "position INTEGER NOT NULL CHECK(position >= 0), "
            "tier TEXT NOT NULL CHECK(length(tier) > 0), "
            "item_count INTEGER NOT NULL CHECK(item_count >= 0), "
            "PRIMARY KEY(digest, position)"
            ") STRICT"
        )
        connection.execute(
            "CREATE TABLE blob_items("
            "graph_digest TEXT NOT NULL REFERENCES graph_facts(digest) "
            "ON DELETE CASCADE, "
            "tier TEXT NOT NULL CHECK(length(tier) > 0), "
            "position INTEGER NOT NULL CHECK(position >= 0), "
            "durable_id TEXT NOT NULL CHECK(length(durable_id) > 0), "
            "sha256 TEXT NOT NULL "
            "CHECK(length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'), "
            "size INTEGER NOT NULL CHECK(size >= 0), "
            "media_type TEXT NOT NULL CHECK(length(media_type) > 0), "
            "schema TEXT, "
            "PRIMARY KEY(graph_digest, tier, position)"
            ") STRICT"
        )
        connection.execute(
            "CREATE TABLE attachments("
            "graph_digest TEXT NOT NULL, "
            "ordinal INTEGER NOT NULL CHECK(ordinal >= 0), "
            "declaration TEXT NOT NULL CHECK(length(declaration) > 0), "
            "durable_id TEXT NOT NULL CHECK(length(durable_id) > 0), "
            "subject TEXT NOT NULL CHECK(length(subject) > 0), "
            "blob_tier TEXT NOT NULL CHECK(length(blob_tier) > 0), "
            "blob_position INTEGER NOT NULL CHECK(blob_position >= 0), "
            "span_unit TEXT, "
            "span_offset INTEGER CHECK(span_offset >= 0), "
            "span_length INTEGER CHECK(span_length >= 0), "
            "span_path TEXT, "
            "PRIMARY KEY(graph_digest, ordinal), "
            "FOREIGN KEY(graph_digest, blob_tier, blob_position) "
            "REFERENCES blob_items(graph_digest, tier, position) ON DELETE CASCADE, "
            "CHECK((span_length IS NULL AND span_offset IS NULL "
            "AND span_unit IS NULL) OR (span_length IS NOT NULL "
            "AND span_unit IS NOT NULL AND span_path IS NULL)), "
            "CHECK(span_path IS NULL OR (span_unit IS NULL "
            "AND span_offset IS NULL AND span_length IS NULL))"
            ") STRICT"
        )
        connection.execute(
            "CREATE TABLE blob_metadata("
            "graph_digest TEXT NOT NULL, "
            "tier TEXT NOT NULL, "
            "position INTEGER NOT NULL CHECK(position >= 0), "
            "column TEXT NOT NULL REFERENCES declared_columns(name) "
            "ON DELETE CASCADE, "
            "value_text TEXT NOT NULL, "
            "value_num TEXT, "
            "source TEXT NOT NULL CHECK(source IN ('declared', 'inspected')), "
            "PRIMARY KEY(graph_digest, tier, position, column), "
            "FOREIGN KEY(graph_digest, tier, position) "
            "REFERENCES blob_items(graph_digest, tier, position) ON DELETE CASCADE"
            ") STRICT"
        )
        connection.execute(
            "CREATE TABLE blob_metadata_counts("
            "graph_digest TEXT NOT NULL REFERENCES graph_facts(digest) "
            "ON DELETE CASCADE, "
            "column TEXT NOT NULL REFERENCES declared_columns(name) "
            "ON DELETE CASCADE, "
            "row_count INTEGER NOT NULL CHECK(row_count >= 0), "
            "row_digest TEXT NOT NULL "
            "CHECK(length(row_digest) = 64 AND "
            "row_digest NOT GLOB '*[^0-9a-f]*'), "
            "PRIMARY KEY(graph_digest, column)"
            ") STRICT"
        )
        connection.execute(
            "CREATE TABLE item_index_counts("
            "graph_digest TEXT NOT NULL REFERENCES graph_facts(digest) "
            "ON DELETE CASCADE, "
            "index_name TEXT NOT NULL REFERENCES declared_indexes(name) "
            "ON DELETE CASCADE, "
            "row_count INTEGER NOT NULL CHECK(row_count >= 0), "
            "PRIMARY KEY(graph_digest, index_name)"
            ") STRICT"
        )
        connection.execute(
            "CREATE TABLE item_index("
            "graph_digest TEXT NOT NULL REFERENCES graph_facts(digest) "
            "ON DELETE CASCADE, "
            "index_name TEXT NOT NULL REFERENCES declared_indexes(name) "
            "ON DELETE CASCADE, "
            "tier_position INTEGER NOT NULL CHECK(tier_position >= 0), "
            "item_position INTEGER NOT NULL CHECK(item_position >= 0), "
            "value_type TEXT NOT NULL CHECK(length(value_type) > 0), "
            "value_text TEXT NOT NULL, "
            "value_num TEXT, "
            "value_digest TEXT NOT NULL "
            "CHECK(length(value_digest) = 64 AND "
            "value_digest NOT GLOB '*[^0-9a-f]*'), "
            "PRIMARY KEY(graph_digest, index_name, item_position)"
            ") STRICT"
        )
        connection.execute(
            "CREATE TABLE versions("
            "id INTEGER PRIMARY KEY, "
            "instance_id INTEGER NOT NULL REFERENCES instances(id), "
            "seq INTEGER NOT NULL CHECK(seq > 0), "
            "parent_id INTEGER REFERENCES versions(id), "
            "commit_seq INTEGER NOT NULL REFERENCES commits(seq), "
            "graph_digest TEXT NOT NULL REFERENCES objects(digest), "
            "patch_digest TEXT REFERENCES objects(digest), "
            "transition TEXT NOT NULL "
            "CHECK(transition IN ('initial', 'patch', 'snapshot')), "
            "reason TEXT, "
            "stage TEXT, "
            "iteration INTEGER, "
            "annotations TEXT, "
            "UNIQUE(instance_id, seq), "
            "CHECK((transition = 'initial' AND seq = 1 AND parent_id IS NULL "
            "AND patch_digest IS NULL) OR transition != 'initial'), "
            "CHECK((transition = 'initial' AND patch_digest IS NULL "
            "AND reason IS NULL) OR (transition = 'patch' "
            "AND patch_digest IS NOT NULL AND reason IS NULL) OR "
            "(transition = 'snapshot' AND patch_digest IS NULL "
            "AND reason IS NOT NULL))"
            ") STRICT"
        )
        connection.execute(
            "CREATE INDEX graph_facts_functional ON graph_facts(functional)"
        )
        connection.execute(
            "CREATE INDEX graph_facts_identified ON graph_facts(identified)"
        )
        connection.execute(
            "CREATE INDEX graph_tiers_tier_count "
            "ON graph_tiers(tier, item_count, digest)"
        )
        connection.execute("CREATE INDEX blob_items_sha256 ON blob_items(sha256)")
        connection.execute(
            "CREATE INDEX blob_items_durable_id ON blob_items(durable_id)"
        )
        connection.execute(
            "CREATE INDEX blob_metadata_value_text ON blob_metadata(column, value_text)"
        )
        connection.execute(
            "CREATE INDEX blob_metadata_value_num ON blob_metadata(column, value_num)"
        )
        connection.execute(
            "CREATE INDEX item_index_value_digest "
            "ON item_index(index_name, value_digest)"
        )
        connection.execute(
            "CREATE INDEX item_index_value_num ON item_index(index_name, value_num)"
        )
        connection.execute(
            "CREATE INDEX versions_instance_commit ON versions(instance_id, commit_seq)"
        )
        connection.execute(
            "CREATE INDEX versions_stage_iteration ON versions(stage, iteration)"
        )
        connection.executemany(
            "INSERT INTO store_meta(key, value) VALUES (?, ?)", metadata
        )
        connection.execute(f"PRAGMA application_id = {_APPLICATION_ID}")
        connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise


def _read_info(
    connection: sqlite3.Connection,
    mode: Literal["ro", "rw"],
    root: Path,
) -> StoreInfo:
    try:
        application_id = connection.execute("PRAGMA application_id").fetchone()[0]
        schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.DatabaseError as error:
        raise StoreCorrupt(
            f"cannot read tgdb catalog {str(root)!r}: {error}"
        ) from error
    if application_id != _APPLICATION_ID:
        raise TgdbError(f"path {str(root)!r} is not a tgdb store")
    if schema_version > _SCHEMA_VERSION:
        raise StoreSchemaTooNew(schema_version)
    if schema_version < _SCHEMA_VERSION and mode == "rw":
        raise TgdbError(
            f"store schema version {schema_version} requires an explicit migration "
            f"to version {_SCHEMA_VERSION} before writing"
        )
    try:
        metadata = dict(connection.execute("SELECT key, value FROM store_meta"))
    except sqlite3.DatabaseError as error:
        raise StoreCorrupt(
            f"cannot read tgdb metadata in {str(root)!r}: {error}"
        ) from error
    missing = _META_KEYS - metadata.keys()
    if missing:
        names = ", ".join(sorted(missing))
        raise StoreCorrupt(f"tgdb metadata in {str(root)!r} is missing: {names}")
    try:
        store_uid = metadata["store_uid"]
        if len(store_uid) != _STORE_UID_HEX_LENGTH:
            raise ValueError
        bytes.fromhex(store_uid)
        integer_values = (
            metadata["layout_version"],
            metadata["index_version"],
            metadata["inline_threshold"],
        )
        if any(
            not value.isascii() or not value.isdecimal() for value in integer_values
        ):
            raise ValueError
        layout_version, index_version, inline_threshold = map(int, integer_values)
        if layout_version <= 0 or index_version <= 0 or inline_threshold < 0:
            raise ValueError
    except ValueError as error:
        raise StoreCorrupt(f"tgdb metadata in {str(root)!r} is malformed") from error
    fingerprint_domain = metadata["fingerprint_domain"]
    if not fingerprint_domain:
        raise StoreCorrupt(
            f"tgdb metadata in {str(root)!r} has an empty fingerprint domain"
        )
    return StoreInfo(
        store_uid=store_uid,
        schema_version=schema_version,
        layout_version=layout_version,
        index_version=index_version,
        inline_threshold=inline_threshold,
        fingerprint_domain=fingerprint_domain,
        mode=mode,
    )


def _read_binary(source: BinaryIO, size: int) -> bytes:
    """Read one binary chunk and enforce the stream protocol."""
    chunk = source.read(size)
    if not isinstance(chunk, bytes):
        raise TypeError("object source read() must return bytes")
    if len(chunk) > size:
        raise TypeError("object source read() returned more bytes than requested")
    return chunk


def _verify_payload(source: VerifiedReader) -> None:
    """Consume one payload with bounded memory and require its full identity."""
    while _read_binary(cast(BinaryIO, source), _OBJECT_CHUNK_SIZE):
        pass
    if not source.verified:
        raise ValueError("payload reader did not verify a complete read")


def _read_prefix(source: BinaryIO, limit: int) -> tuple[bytes, bool]:
    """Read through the inline boundary and report whether EOF was reached."""
    parts: list[bytes] = []
    remaining = limit
    while remaining:
        chunk = _read_binary(source, remaining)
        if not chunk:
            return b"".join(parts), True
        parts.append(chunk)
        remaining -= len(chunk)
    return b"".join(parts), False


def _validate_digest(digest: object) -> None:
    """Require one canonical lowercase SHA-256 spelling."""
    if (
        not isinstance(digest, str)
        or len(digest) != _DIGEST_HEX_LENGTH
        or not digest.isascii()
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise StoreCorrupt(f"object digest {digest!r} is malformed")


def _validate_fingerprint(value: object, view: str, digest: str) -> None:
    """Require one canonical SHA-256 graph fingerprint spelling."""
    if (
        not isinstance(value, str)
        or len(value) != _DIGEST_HEX_LENGTH
        or not value.isascii()
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise StoreCorrupt(
            f"graph facts for object {digest} have a malformed {view} fingerprint"
        )


def _validate_object_record(
    digest: object, size: object, residency: object, data: object
) -> None:
    """Require one catalog row to match the authoritative object schema."""
    _validate_digest(digest)
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise StoreCorrupt(f"object {digest} has malformed size {size!r}")
    if residency == "inline":
        if not isinstance(data, bytes):
            raise StoreCorrupt(f"inline object {digest} has malformed data")
    elif residency == "file":
        if data is not None:
            raise StoreCorrupt(f"file object {digest} has unexpected inline data")
    else:
        raise StoreCorrupt(f"object {digest} has unknown residency {residency!r}")


def _object_path(root: Path, digest: str) -> Path:
    """Derive the sole file location for one canonical digest."""
    _validate_digest(digest)
    return root / _OBJECTS_NAME / _HASH_NAME / digest[:2] / digest[2:4] / digest


def _require_plain_directory(path: Path, description: str) -> None:
    """Require an existing real directory without following a symlink."""
    if path.is_symlink() or not path.is_dir():
        raise StoreCorrupt(f"{description} is not a plain directory")


def _require_pool_layout(root: Path, *, writable: bool) -> None:
    """Validate fixed pool directories before any object path is followed."""
    _require_plain_directory(root / _OBJECTS_NAME, "object directory")
    _require_plain_directory(
        root / _OBJECTS_NAME / _HASH_NAME, "SHA-256 object directory"
    )
    if writable:
        _require_plain_directory(root / _STAGING_NAME, "staging directory")


def _check_object_tree(root: Path) -> None:
    """Reject every symlink or path not derived from a canonical digest."""
    objects = root / _OBJECTS_NAME
    hashes = objects / _HASH_NAME
    for path in objects.iterdir():
        if path != hashes:
            raise StoreCorrupt(f"non-derived path in object directory: {path.name!r}")
    for first in hashes.iterdir():
        if _SHARD_NAME.fullmatch(first.name) is None:
            raise StoreCorrupt(f"non-derived object shard: {first.name!r}")
        _require_plain_directory(first, f"object shard {first.name!r}")
        for second in first.iterdir():
            if _SHARD_NAME.fullmatch(second.name) is None:
                raise StoreCorrupt(f"non-derived object shard: {second.name!r}")
            _require_plain_directory(second, f"object shard {second.name!r}")
            prefix = first.name + second.name
            object_name = re.compile(rf"{prefix}[0-9a-f]{{60}}")
            for path in second.iterdir():
                if object_name.fullmatch(path.name) is None:
                    raise StoreCorrupt(f"non-derived object path: {path.name!r}")
                if path.is_symlink() or not path.is_file():
                    raise StoreCorrupt(f"object path {path.name!r} is not a plain file")


def _ensure_object_parent(root: Path, digest: str) -> Path:
    """Create the two digest shards without accepting substituted symlinks."""
    parent = root / _OBJECTS_NAME / _HASH_NAME
    for name in (digest[:2], digest[2:4]):
        child = parent / name
        try:
            child.mkdir()
        except FileExistsError:
            _require_plain_directory(child, f"object shard {name!r}")
        else:
            _fsync_directory(parent)
        parent = child
    return parent


def _fsync_directory(path: Path) -> None:
    """Persist directory-entry changes before publication continues."""
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_object_file(root: Path, staging: Path, digest: str, size: int) -> None:
    """Publish a staged file once at its digest-derived location."""
    parent = _ensure_object_parent(root, digest)
    target = parent / digest
    try:
        os.link(staging, target)
    except FileExistsError:
        _check_object_file(target, digest, size, full=True)
    else:
        _fsync_directory(parent)
    staging.unlink()


def _check_object_file(path: Path, digest: str, size: int, *, full: bool) -> None:
    """Validate one file object's kind, size, and optionally its content hash."""
    for parent, description in (
        (path.parent.parent, f"first shard for object {digest}"),
        (path.parent, f"second shard for object {digest}"),
    ):
        _require_plain_directory(parent, description)
    try:
        metadata = path.lstat()
    except OSError as error:
        raise StoreCorrupt(f"cannot read object {digest}: {error}") from error
    if path.is_symlink() or not path.is_file():
        raise StoreCorrupt(f"object {digest} is not a plain file")
    if metadata.st_size != size:
        raise StoreCorrupt(
            f"object {digest} records {size} bytes but its file has "
            f"{metadata.st_size} bytes"
        )
    if full:
        try:
            descriptor = os.open(
                path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            with io.FileIO(descriptor, mode="rb", closefd=True) as source:
                actual = hashlib.file_digest(source, "sha256").hexdigest()
        except OSError as error:
            raise StoreCorrupt(f"cannot read object {digest}: {error}") from error
        if actual != digest:
            raise StoreCorrupt(
                f"object {digest} has SHA-256 {actual} instead of its content address"
            )


def _require_digest(digest: str, data: bytes) -> None:
    """Require inline bytes to match their content address."""
    actual = hashlib.sha256(data).hexdigest()
    if actual != digest:
        raise StoreCorrupt(
            f"object {digest} has SHA-256 {actual} instead of its content address"
        )


def _check_recorded_object(
    root: Path,
    digest: str,
    record: tuple[int, str, bytes | None],
    *,
    full: bool,
) -> None:
    """Validate one raw record and the bytes selected by its residency."""
    size, residency, data = record
    _validate_object_record(digest, size, residency, data)
    if residency == "inline":
        inline_data = cast(bytes, data)
        if len(inline_data) != size:
            raise StoreCorrupt(
                f"object {digest} records {size} bytes but stores "
                f"{len(inline_data)} inline bytes"
            )
        if full:
            _require_digest(digest, inline_data)
    else:
        _check_object_file(_object_path(root, digest), digest, size, full=full)


__all__ = [
    "CheckReport",
    "CollectionInfo",
    "ColumnInfo",
    "CommitReceipt",
    "IndexInfo",
    "InspectReport",
    "InspectionFact",
    "InspectionResult",
    "Inspector",
    "InstanceInfo",
    "Query",
    "ReindexReport",
    "RepackReport",
    "Snapshot",
    "SqliteTooOld",
    "StaleJournalBase",
    "StaleVersion",
    "StoreBusy",
    "StoreCorrupt",
    "StoreInfo",
    "StoreSchemaTooNew",
    "TgdbError",
    "TgdbLimits",
    "TgdbStore",
    "VersionChange",
    "VersionHandle",
    "WriteTransaction",
    "register_inspector",
]
