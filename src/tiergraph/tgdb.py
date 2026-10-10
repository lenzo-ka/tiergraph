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
import sqlite3
from contextlib import suppress
from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, Literal, Self, cast

from tiergraph.blob import BlobRef, VerifiedReader
from tiergraph.core import Graph
from tiergraph.diff import diff as graph_diff
from tiergraph.edit import EditAnnotations
from tiergraph.equivalence import EquivalenceView, equivalent, fingerprint
from tiergraph.patch import Patch, invert_patch, patch_dumps, patch_loads
from tiergraph.schema import Refusal, RefusalStage
from tiergraph.wire import FORMAT_VERSION, MAX_DOCUMENT_BYTES, dumps, loads

_APPLICATION_ID = 0x54474442
_SCHEMA_VERSION = 4
_LAYOUT_VERSION = 1
_INDEX_VERSION = 1
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
_STORE_UID_HEX_LENGTH = 32
_UID_BYTES = 16
_MAX_NAME_BYTES = 4_096
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
        digest = self._store_graph(graph)
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
        document = _graph_document(graph)
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

    def _store_graph(self, graph: Graph) -> str:
        """Store one canonical graph document and its small derived facts."""
        return self._store_graph_document(graph, _graph_document(graph))

    def _store_graph_document(self, graph: Graph, document: bytes) -> str:
        """Store already serialized graph bytes and their verified fingerprints."""
        digest = self._put_object(io.BytesIO(document))
        functional = fingerprint(graph, EquivalenceView.FUNCTIONAL)
        identified = fingerprint(graph, EquivalenceView.IDENTIFIED)
        self._connection.execute(
            "INSERT OR IGNORE INTO graph_facts("
            "digest, format_version, fp_domain, functional, identified, size"
            ") VALUES (?, ?, ?, ?, ?, ?)",
            (
                digest,
                FORMAT_VERSION,
                self._store._info.fingerprint_domain,
                functional,
                identified,
                len(document),
            ),
        )
        recorded = self._connection.execute(
            "SELECT format_version, fp_domain, functional, identified, size "
            "FROM graph_facts WHERE digest = ?",
            (digest,),
        ).fetchone()
        expected = (
            FORMAT_VERSION,
            self._store._info.fingerprint_domain,
            functional,
            identified,
            len(document),
        )
        if recorded != expected:
            raise StoreCorrupt(f"graph facts for object {digest} are inconsistent")
        self._staged_graphs.add(digest)
        return digest

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
            name = action.get("name")
            _validate_name(name)
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
                    (name, row[0]),
                ).fetchone()
            else:
                conflict = self._connection.execute(
                    "SELECT 1 FROM instances WHERE collection_id = ? AND name = ? "
                    "AND id != ?",
                    (row[2], name, row[0]),
                ).fetchone()
            if conflict is not None:
                raise StaleVersion(
                    f"recorded {rename_table[:-1]} name {name!r} is no longer available"
                )
            self._connection.execute(
                f"UPDATE {rename_table} SET name = ? WHERE uid = ?", (name, uid)
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
            for uid_text, position in raw_positions.items():
                item_uid = _uid_from_hex(uid_text)
                if (
                    isinstance(position, bool)
                    or not isinstance(position, int)
                    or position < 0
                ):
                    raise StoreCorrupt("recorded catalog position is malformed")
                row = self._connection.execute(
                    f"SELECT position FROM {positions_table} WHERE uid = ?", (item_uid,)
                ).fetchone()
                if row is None:
                    raise StoreCorrupt(
                        f"recorded {positions_table[:-1]} identity is missing"
                    )
                positions[item_uid] = position
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
        ),
        "digest",
        graph_digests,
    )
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
) -> VersionHandle:
    """Bind one immutable version row to its verified object loader."""
    collection = store._connection.execute(
        "SELECT name FROM collections WHERE id = ?", (instance[2],)
    ).fetchone()
    if collection is None:
        raise StoreCorrupt(f"instance {instance[3]!r} has no collection")
    _validate_digest(version[2])
    _validate_fingerprint(version[3], "functional", version[2])
    _validate_fingerprint(version[4], "identified", version[2])
    if version[8] is not None:
        _validate_digest(version[8])
    return VersionHandle(
        collection=cast(str, collection[0]),
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


def _action_touches(action: dict[str, object]) -> tuple[str, ...]:
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
            "CREATE TABLE graph_facts("
            "digest TEXT PRIMARY KEY REFERENCES objects(digest), "
            "format_version TEXT NOT NULL, "
            "fp_domain TEXT NOT NULL, "
            "functional TEXT NOT NULL "
            "CHECK(length(functional) = 64 AND functional NOT GLOB '*[^0-9a-f]*'), "
            "identified TEXT NOT NULL "
            "CHECK(length(identified) = 64 AND identified NOT GLOB '*[^0-9a-f]*'), "
            "size INTEGER NOT NULL CHECK(size >= 0)"
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
    "CommitReceipt",
    "InstanceInfo",
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
]
