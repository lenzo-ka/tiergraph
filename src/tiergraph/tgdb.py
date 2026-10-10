"""Persist tiergraph collections in a versioned local SQLite catalog.

Import this module explicitly when a process needs a store.  Importing the
top-level :mod:`tiergraph` package does not import this module or ``sqlite3``.
"""

from __future__ import annotations

import secrets
import sqlite3
from contextlib import suppress
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from types import TracebackType
from typing import Literal, Self

from tiergraph.schema import Refusal, RefusalStage

_APPLICATION_ID = 0x54474442
_SCHEMA_VERSION = 1
_LAYOUT_VERSION = 1
_INDEX_VERSION = 1
_FINGERPRINT_DOMAIN = "tiergraph-equivalence/1"
_MINIMUM_SQLITE = (3, 37, 0)
_WAL_AUTOCHECKPOINT_PAGES = 1_000
_CATALOG_NAME = "catalog.sqlite3"
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
    with suppress(OSError):
        root.rmdir()


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


__all__ = [
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
