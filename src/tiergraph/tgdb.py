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
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, Literal, Self, cast

from tiergraph.blob import BlobRef, VerifiedReader
from tiergraph.edit import EditAnnotations
from tiergraph.schema import Refusal, RefusalStage

_APPLICATION_ID = 0x54474442
_SCHEMA_VERSION = 3
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
    """Refuse a write based on an instance version that is no longer current."""


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
class CommitReceipt:
    """Identify one durable catalog commit and its ordered operation kinds."""

    commit_seq: int
    kind: str
    operations: tuple[str, ...]

    def to_data(self) -> dict[str, int | str | list[str]]:
        """Return a JSON-compatible receipt in stable field order."""
        return {
            "commit_seq": self.commit_seq,
            "kind": self.kind,
            "operations": list(self.operations),
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
    ) -> None:
        """Retain a validated connection created by :meth:`create` or :meth:`open`."""
        self._path = path
        self._connection = connection
        self._info = info
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
        return cls(root, connection, info)

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
        return cls(root, connection, info)

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

    def write(
        self,
        *,
        annotations: EditAnnotations | None = None,
    ) -> WriteTransaction:
        """Open an explicit catalog transaction that commits only on request."""
        self._require_writable()
        if self._writer is not None:
            raise TgdbError("a write transaction is already active")
        writer = WriteTransaction(self, annotations)
        self._writer = writer
        return writer

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

    def _put_object(self, source: BinaryIO) -> str:
        """Store one byte stream once and return its SHA-256 content address."""
        self._require_writable()
        _require_pool_layout(self._path, writable=True)
        prefix, finished = _read_prefix(source, self._info.inline_threshold + 1)
        if finished and len(prefix) <= self._info.inline_threshold:
            digest = hashlib.sha256(prefix).hexdigest()
            self._record_object(digest, len(prefix), "inline", prefix)
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
            existing = self._object_record(digest)
            if existing is not None:
                _check_recorded_object(self._path, digest, existing, full=True)
                staging.unlink()
                return digest
            _publish_object_file(self._path, staging, digest, size)
            self._record_object(digest, size, "file", None)
            return digest
        except Exception:
            if staging_created:
                with suppress(OSError):
                    staging.unlink()
            raise

    def _open_object(self, digest: str) -> VerifiedReader:
        """Open one stored object through an EOF-verifying binary reader."""
        self._require_open()
        self._require_current_schema()
        _validate_digest(digest)
        record = self._object_record(digest)
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
    ) -> None:
        """Publish one object row, joining a caller's transaction when present."""
        owns_transaction = not self._connection.in_transaction
        if owns_transaction:
            self._connection.execute("BEGIN IMMEDIATE")
        try:
            existing = self._object_record(digest)
            if existing is None:
                self._connection.execute(
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
                self._connection.execute("COMMIT")
        except Exception:
            if owns_transaction:
                self._connection.execute("ROLLBACK")
            raise

    def _object_record(self, digest: str) -> tuple[int, str, bytes | None] | None:
        """Return one raw object record, or ``None`` when it is absent."""
        try:
            row = self._connection.execute(
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


class WriteTransaction:
    """Apply ordered catalog changes in one explicit SQLite commit."""

    def __init__(
        self,
        store: TgdbStore,
        annotations: EditAnnotations | None,
    ) -> None:
        """Acquire the writer lock and assign one commit sequence."""
        if annotations is not None and not isinstance(annotations, EditAnnotations):
            raise TypeError("annotations must be EditAnnotations or None")
        self._store = store
        self._connection = store._connection
        self._active = True
        self._operations: list[str] = []
        self._kind = "catalog"
        encoded = (
            None
            if annotations is None
            else json.dumps(
                annotations.to_data(),
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            cursor = self._connection.execute(
                "INSERT INTO commits(kind, annotations) VALUES (?, ?)",
                (self._kind, encoded),
            )
        except sqlite3.OperationalError as error:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if _sqlite_busy(error):
                raise StoreBusy(
                    "tgdb catalog remained busy past its timeout"
                ) from error
            raise StoreCorrupt(f"cannot begin catalog transaction: {error}") from error
        except sqlite3.DatabaseError as error:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise StoreCorrupt(f"cannot begin catalog transaction: {error}") from error
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

    def commit(self) -> CommitReceipt:
        """Commit all staged catalog changes and return their durable sequence."""
        self._require_active()
        if not self._operations:
            self.discard()
            raise TgdbError("write transaction has no changes")
        try:
            self._connection.execute(
                "UPDATE commits SET kind = ? WHERE seq = ?",
                (self._kind, self._commit_seq),
            )
            self._connection.execute("COMMIT")
        except sqlite3.OperationalError as error:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            self._finish()
            if _sqlite_busy(error):
                raise StoreBusy(
                    "tgdb catalog remained busy past its timeout"
                ) from error
            raise StoreCorrupt(f"cannot commit catalog transaction: {error}") from error
        except sqlite3.DatabaseError as error:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            self._finish()
            raise StoreCorrupt(f"cannot commit catalog transaction: {error}") from error
        receipt = CommitReceipt(self._commit_seq, self._kind, tuple(self._operations))
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
) -> tuple[int, bytes, int, str, int, int, int | None]:
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
        "SELECT id, uid, collection_id, name, position, generation, retired_commit "
        "FROM instances WHERE " + " AND ".join(conditions),
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
    return cast(tuple[int, bytes, int, str, int, int, int | None], rows[0])


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
        "rename_instance": "instance",
        "retire_instance": "instance",
        "restore_instance": "instance",
    }
    identity_kind = identity_kinds.get(cast(str, action.get("action")))
    expected: tuple[str, ...]
    if identity_kind is not None:
        expected = (_touch(identity_kind, _action_uid(action)),)
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
            "head_version INTEGER, "
            "generation INTEGER NOT NULL CHECK(generation >= 0), "
            "retired_commit INTEGER REFERENCES commits(seq), "
            "UNIQUE(collection_id, name), "
            "UNIQUE(collection_id, position)"
            ") STRICT"
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
    "WriteTransaction",
]
