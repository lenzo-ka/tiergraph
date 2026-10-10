"""Persist tiergraph collections in a versioned local SQLite catalog.

Import this module explicitly when a process needs a store.  Importing the
top-level :mod:`tiergraph` package does not import this module or ``sqlite3``.
"""

from __future__ import annotations

import hashlib
import io
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
from tiergraph.schema import Refusal, RefusalStage

_APPLICATION_ID = 0x54474442
_SCHEMA_VERSION = 2
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
            self._connection.close()
            self._closed = True

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
]
