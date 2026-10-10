"""Exercise the lazy tgdb catalog skeleton and its refusal boundaries."""

from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import secrets
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from typing import Literal, cast

import pytest

import tiergraph.tgdb as tgdb
from tiergraph import EditAnnotations, Refusal, RefusalStage


def _created(tmp_path: Path, *, limits: tgdb.TgdbLimits | None = None) -> Path:
    """Create, close, and return one store path."""
    path = tmp_path / "store"
    tgdb.TgdbStore.create(
        path, limits=tgdb.TgdbLimits() if limits is None else limits
    ).close()
    return path


def _set_pragma(path: Path, pragma: str, value: int) -> None:
    """Set one integer catalog pragma outside the tgdb API for a refusal probe."""
    with closing(
        sqlite3.connect(path / "catalog.sqlite3", autocommit=True)
    ) as connection:
        connection.execute(f"PRAGMA {pragma} = {value}")


def _set_meta(path: Path, key: str, value: str) -> None:
    """Replace one metadata value outside the tgdb API for a corruption probe."""
    with closing(
        sqlite3.connect(path / "catalog.sqlite3", autocommit=True)
    ) as connection:
        connection.execute(
            "UPDATE store_meta SET value = ? WHERE key = ?", (value, key)
        )


def _object_path(path: Path, digest: str) -> Path:
    """Return the fixed file-resident path for one test digest."""
    return path / "objects" / "sha256" / digest[:2] / digest[2:4] / digest


def _seed_instance(
    store: tgdb.TgdbStore,
    collection_uid: bytes,
    name: str,
    position: int,
    *,
    uid: bytes | None = None,
) -> bytes:
    """Insert a versionless catalog row until the version slice owns creation."""
    instance_uid = secrets.token_bytes(16) if uid is None else uid
    collection_id = store._connection.execute(
        "SELECT id FROM collections WHERE uid = ?", (collection_uid,)
    ).fetchone()[0]
    store._connection.execute(
        "INSERT INTO instances(uid, collection_id, name, position, head_version, "
        "generation, retired_commit) VALUES (?, ?, ?, ?, NULL, 0, NULL)",
        (instance_uid, collection_id, name, position),
    )
    return instance_uid


def test_importing_tiergraph_keeps_tgdb_and_sqlite_lazy() -> None:
    """The standard package import pays for neither the store nor SQLite."""
    source = (
        "import json, sys, tiergraph; "
        "print(json.dumps({"
        "'tgdb': 'tiergraph.tgdb' in sys.modules, "
        "'sqlite': 'sqlite3' in sys.modules}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", source], check=True, capture_output=True, text=True
    )
    assert json.loads(result.stdout) == {"sqlite": False, "tgdb": False}


def test_store_creation_records_identity_metadata_and_pragmas(tmp_path: Path) -> None:
    """A new store is strict, inert, durable, relocatable metadata."""
    limits = tgdb.TgdbLimits(
        inline_threshold=1234, busy_timeout_ms=2345, batch_size=7, nesting_depth=3
    )
    store = tgdb.TgdbStore.create(tmp_path / "store", limits=limits)
    info = store.info()
    assert store.path == tmp_path / "store"
    assert store.closed is False
    assert info.to_data() == {
        "store_uid": info.store_uid,
        "schema_version": 3,
        "layout_version": 1,
        "index_version": 1,
        "inline_threshold": 1234,
        "fingerprint_domain": "tiergraph-equivalence/1",
        "mode": "rw",
    }
    assert len(bytes.fromhex(info.store_uid)) == 16
    connection = store._connection
    assert connection.execute("PRAGMA application_id").fetchone() == (0x54474442,)
    assert connection.execute("PRAGMA user_version").fetchone() == (3,)
    assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    assert connection.execute("PRAGMA synchronous").fetchone() == (2,)
    assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
    assert connection.execute("PRAGMA trusted_schema").fetchone() == (0,)
    assert connection.execute("PRAGMA busy_timeout").fetchone() == (2345,)
    assert connection.execute("PRAGMA wal_autocheckpoint").fetchone() == (1000,)
    assert connection.execute(
        "SELECT strict FROM pragma_table_list WHERE name = 'store_meta'"
    ).fetchone() == (1,)
    assert connection.execute(
        "SELECT strict FROM pragma_table_list WHERE name = 'objects'"
    ).fetchone() == (1,)
    assert (store.path / "objects" / "sha256").is_dir()
    assert (store.path / "staging").is_dir()
    assert (
        connection.execute(
            "SELECT type FROM sqlite_schema WHERE type IN ('trigger', 'view')"
        ).fetchall()
        == []
    )
    values = [row[0] for row in connection.execute("SELECT value FROM store_meta")]
    assert all(str(tmp_path) not in value for value in values)
    store.close()
    store.close()
    assert store.closed is True
    with pytest.raises(tgdb.TgdbError, match="store is closed"):
        store.info()
    with pytest.raises(tgdb.TgdbError, match="store is closed"):
        store.__enter__()


def test_context_manager_and_read_only_open(tmp_path: Path) -> None:
    """Read-only open preserves identity and enables SQLite query-only mode."""
    path = _created(tmp_path)
    with tgdb.TgdbStore.open(path) as store:
        assert store.info().mode == "ro"
        assert store._connection.execute("PRAGMA query_only").fetchone() == (1,)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            store._connection.execute(
                "INSERT INTO store_meta VALUES ('unexpected', 'value')"
            )
    assert store.closed is True
    with tgdb.TgdbStore.open(path, mode="rw") as writable:
        assert writable.info().mode == "rw"
        assert writable._connection.execute("PRAGMA journal_mode").fetchone() == (
            "wal",
        )


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"inline_threshold": -1}, "inline_threshold must be nonnegative"),
        ({"busy_timeout_ms": -1}, "busy_timeout_ms must be nonnegative"),
        ({"nesting_depth": -1}, "nesting_depth must be nonnegative"),
        ({"batch_size": 0}, "batch_size must be positive"),
    ],
)
def test_limits_refuse_unbounded_or_negative_values(
    values: dict[str, int], message: str
) -> None:
    """Every bound rejects values outside its declared domain."""
    with pytest.raises(ValueError, match=message):
        tgdb.TgdbLimits(**values)


def test_create_refuses_existing_paths_and_a_creation_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Initialization never adopts or replaces an existing path."""
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(tgdb.TgdbError, match="already exists"):
        tgdb.TgdbStore.create(existing)

    original = Path.mkdir

    def raced(
        path: Path,
        mode: int = 0o777,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> None:
        if path.name == "raced":
            raise FileExistsError
        original(path, mode=mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(Path, "mkdir", raced)
    with pytest.raises(tgdb.TgdbError, match="already exists"):
        tgdb.TgdbStore.create(tmp_path / "raced")


def test_create_closes_after_initialization_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both preconnection and connected failures escape without a live handle."""

    def no_connection(
        catalog: Path,
        mode: Literal["ro", "rw"],
        limits: tgdb.TgdbLimits,
        *,
        create: bool = False,
    ) -> sqlite3.Connection:
        raise RuntimeError("connect failed")

    monkeypatch.setattr(tgdb, "_connect", no_connection)
    with pytest.raises(RuntimeError, match="connect failed"):
        tgdb.TgdbStore.create(tmp_path / "first")
    assert not (tmp_path / "first").exists()

    monkeypatch.undo()
    connection: sqlite3.Connection | None = None
    original_connect = tgdb._connect

    def retained(
        catalog: Path,
        mode: Literal["ro", "rw"],
        limits: tgdb.TgdbLimits,
        *,
        create: bool = False,
    ) -> sqlite3.Connection:
        nonlocal connection
        connection = original_connect(catalog, mode, limits, create=create)
        return connection

    def failed_initialize(
        connection: sqlite3.Connection, limits: tgdb.TgdbLimits
    ) -> None:
        raise RuntimeError("initialize failed")

    monkeypatch.setattr(tgdb, "_connect", retained)
    monkeypatch.setattr(tgdb, "_initialize", failed_initialize)
    with pytest.raises(RuntimeError, match="initialize failed"):
        tgdb.TgdbStore.create(tmp_path / "second")
    assert connection is not None
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")
    assert not (tmp_path / "second").exists()


def test_open_refuses_bad_mode_and_nonstores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Open validates its mode and store layout before SQLite can create files."""
    with pytest.raises(ValueError, match="mode must be"):
        tgdb.TgdbStore.open(tmp_path, mode="invalid")  # type: ignore[arg-type]
    with pytest.raises(tgdb.TgdbError, match="not a tgdb store"):
        tgdb.TgdbStore.open(tmp_path / "absent")
    file_path = tmp_path / "file"
    file_path.write_text("not a store", encoding="utf-8")
    with pytest.raises(tgdb.TgdbError, match="not a tgdb store"):
        tgdb.TgdbStore.open(file_path)

    path = _created(tmp_path)

    def unreadable(
        catalog: Path,
        mode: Literal["ro", "rw"],
        limits: tgdb.TgdbLimits,
        *,
        create: bool = False,
    ) -> sqlite3.Connection:
        raise sqlite3.DatabaseError("unreadable")

    monkeypatch.setattr(tgdb, "_connect", unreadable)
    with pytest.raises(tgdb.StoreCorrupt, match="cannot read tgdb catalog"):
        tgdb.TgdbStore.open(path)


def test_schema_versions_refuse_new_writes_without_implicit_migration(
    tmp_path: Path,
) -> None:
    """Newer schemas refuse all opens and older schemas remain read-only."""
    path = _created(tmp_path)
    _set_pragma(path, "user_version", 4)
    with pytest.raises(tgdb.StoreSchemaTooNew) as caught:
        tgdb.TgdbStore.open(path)
    assert caught.value.found == 4
    assert caught.value.supported == 3
    assert "4" in str(caught.value) and "3" in str(caught.value)
    with closing(
        sqlite3.connect(path / "catalog.sqlite3", autocommit=True)
    ) as connection:
        assert connection.execute("PRAGMA journal_mode = DELETE").fetchone() == (
            "delete",
        )
    with pytest.raises(tgdb.StoreSchemaTooNew):
        tgdb.TgdbStore.open(path, mode="rw")
    with closing(
        sqlite3.connect(path / "catalog.sqlite3", autocommit=True)
    ) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("delete",)

    _set_pragma(path, "user_version", 2)
    with tgdb.TgdbStore.open(path) as store:
        assert store.info().schema_version == 2
        with pytest.raises(tgdb.TgdbError, match="migration to version 3"):
            store.check()
    with pytest.raises(tgdb.TgdbError, match="explicit migration"):
        tgdb.TgdbStore.open(path, mode="rw")


def test_sqlite_floor_names_found_and_required_versions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The SQLite floor is checked before a store path is touched."""
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 36, 9))
    with pytest.raises(tgdb.SqliteTooOld) as caught:
        tgdb.TgdbStore.open(tmp_path / "absent")
    assert caught.value.found == (3, 36, 9)
    assert caught.value.minimum == (3, 37, 0)
    assert str(caught.value) == (
        "SQLite version 3.36.9 is older than required version 3.37.0"
    )


def test_foreign_application_id_and_corrupt_catalog_are_refused(tmp_path: Path) -> None:
    """A foreign or malformed database never passes as a tgdb catalog."""
    path = _created(tmp_path)
    _set_pragma(path, "application_id", 0)
    with pytest.raises(tgdb.TgdbError, match="not a tgdb store"):
        tgdb.TgdbStore.open(path)

    corrupt = tmp_path / "corrupt"
    corrupt.mkdir()
    (corrupt / "catalog.sqlite3").write_bytes(b"not a sqlite database")
    with pytest.raises(tgdb.StoreCorrupt, match="cannot read tgdb catalog"):
        tgdb.TgdbStore.open(corrupt)


def test_missing_and_unreadable_metadata_are_corruption(tmp_path: Path) -> None:
    """Catalog identity alone cannot cover a missing or unreadable metadata table."""
    path = _created(tmp_path)
    with closing(
        sqlite3.connect(path / "catalog.sqlite3", autocommit=True)
    ) as connection:
        connection.execute("DROP TABLE store_meta")
    with pytest.raises(tgdb.StoreCorrupt, match="cannot read tgdb metadata"):
        tgdb.TgdbStore.open(path)

    (tmp_path / "again").mkdir()
    path = _created(tmp_path / "again")
    with closing(
        sqlite3.connect(path / "catalog.sqlite3", autocommit=True)
    ) as connection:
        connection.execute("DELETE FROM store_meta WHERE key = 'layout_version'")
    with pytest.raises(tgdb.StoreCorrupt, match="missing: layout_version"):
        tgdb.TgdbStore.open(path)


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("store_uid", "short", "is malformed"),
        ("store_uid", "z" * 32, "is malformed"),
        ("layout_version", "zero", "is malformed"),
        ("layout_version", "0", "is malformed"),
        ("layout_version", " 1", "is malformed"),
        ("layout_version", "+1", "is malformed"),
        ("layout_version", "1_0", "is malformed"),
        ("index_version", "0", "is malformed"),
        ("inline_threshold", "-1", "is malformed"),
        ("fingerprint_domain", "", "empty fingerprint domain"),
    ],
)
def test_malformed_metadata_is_refused(
    tmp_path: Path, key: str, value: str, message: str
) -> None:
    """Each metadata field is validated before it becomes public store information."""
    path = _created(tmp_path)
    _set_meta(path, key, value)
    with pytest.raises(tgdb.StoreCorrupt, match=message):
        tgdb.TgdbStore.open(path)


def test_initializer_rolls_back_a_failed_schema_transaction() -> None:
    """A schema error takes the explicit initialization transaction with it."""
    connection = sqlite3.connect(":memory:", autocommit=True)
    connection.execute("CREATE TABLE store_meta(key TEXT)")
    with pytest.raises(sqlite3.OperationalError, match="already exists"):
        tgdb._initialize(connection, tgdb.TgdbLimits())
    assert connection.in_transaction is False
    connection.close()


def test_object_pool_uses_threshold_deduplicates_and_verifies_reads(
    tmp_path: Path,
) -> None:
    """Objects are stored once, split by size, and verified only at EOF."""
    path = tmp_path / "store"
    with tgdb.TgdbStore.create(
        path, limits=tgdb.TgdbLimits(inline_threshold=4)
    ) as store:
        inline = b"tiny"
        file_data = b"large"
        inline_digest = store._put_object(io.BytesIO(inline))
        file_digest = store._put_object(io.BytesIO(file_data))
        assert inline_digest == hashlib.sha256(inline).hexdigest()
        assert file_digest == hashlib.sha256(file_data).hexdigest()
        assert store._put_object(io.BytesIO(inline)) == inline_digest
        assert store._put_object(io.BytesIO(file_data)) == file_digest

        rows = store._connection.execute(
            "SELECT digest, size, residency, data FROM objects ORDER BY size"
        ).fetchall()
        assert rows == [
            (inline_digest, 4, "inline", inline),
            (file_digest, 5, "file", None),
        ]
        file_path = _object_path(path, file_digest)
        assert file_path.read_bytes() == file_data
        assert list((path / "staging").iterdir()) == []

        with store._open_object(inline_digest) as reader:
            assert reader.read(4) == inline
            assert reader.verified is False
            assert reader.read(1) == b""
            assert reader.verified is True
        with store._open_object(file_digest) as reader:
            assert reader.read() == file_data
            assert reader.verified is True

        assert store.check().to_data() == {
            "full": False,
            "objects": 2,
            "inline_objects": 1,
            "file_objects": 1,
            "object_bytes": 9,
        }
        assert store.check(full=True).to_data() == {
            "full": True,
            "objects": 2,
            "inline_objects": 1,
            "file_objects": 1,
            "object_bytes": 9,
        }


def test_zero_threshold_and_short_reads_keep_ingest_bounded(tmp_path: Path) -> None:
    """The threshold is inclusive and short binary reads do not imply EOF."""

    class ShortReader(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            requested = -1 if size is None else size
            return super().read(1 if requested < 0 else min(requested, 1))

    with tgdb.TgdbStore.create(
        tmp_path / "store", limits=tgdb.TgdbLimits(inline_threshold=0)
    ) as store:
        empty = store._put_object(ShortReader(b""))
        payload = b"streamed"
        digest = store._put_object(ShortReader(payload))
        assert store._object_record(empty) == (0, "inline", b"")
        assert store._object_record(digest) == (len(payload), "file", None)
        assert _object_path(store.path, digest).read_bytes() == payload


def test_large_put_bounded_memory(tmp_path: Path) -> None:
    """A large streamed object uses chunk-bounded memory rather than its full size."""
    import tracemalloc  # noqa: PLC0415 -- measure only this focused allocation window

    class RepeatingReader:
        def __init__(self, size: int) -> None:
            self.remaining = size

        def read(self, size: int = -1) -> bytes:
            amount = self.remaining if size < 0 else min(size, self.remaining)
            self.remaining -= amount
            return b"x" * amount

    object_size = 16 << 20
    with tgdb.TgdbStore.create(
        tmp_path / "store", limits=tgdb.TgdbLimits(inline_threshold=4)
    ) as store:
        tracemalloc.start()
        try:
            digest = store._put_object(RepeatingReader(object_size))  # type: ignore[arg-type]
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert peak < 4 << 20
        assert store._object_record(digest) == (object_size, "file", None)
        assert _object_path(store.path, digest).stat().st_size == object_size


def test_object_ingest_refuses_nonbinary_sources_and_cleans_staging(
    tmp_path: Path,
) -> None:
    """A broken binary stream cannot leave a staged or cataloged object."""

    class TextReader:
        def read(self, _size: int = -1) -> str:
            return "text"

    class BrokenLater:
        calls = 0

        def read(self, _size: int = -1) -> bytes | bytearray:
            self.calls += 1
            return b"abcde" if self.calls == 1 else bytearray(b"bad")

    class OversizedRead:
        def read(self, size: int = -1) -> bytes:
            return b"x" * (size + 1)

    with tgdb.TgdbStore.create(
        tmp_path / "store", limits=tgdb.TgdbLimits(inline_threshold=4)
    ) as store:
        with pytest.raises(TypeError, match="must return bytes"):
            store._put_object(TextReader())  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="must return bytes"):
            store._put_object(BrokenLater())  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="more bytes than requested"):
            store._put_object(OversizedRead())  # type: ignore[arg-type]
        assert list((store.path / "staging").iterdir()) == []
        assert store._connection.execute("SELECT count(*) FROM objects").fetchone() == (
            0,
        )


def test_object_operations_refuse_wrong_store_state_and_identity(
    tmp_path: Path,
) -> None:
    """Object access requires an open current store and a canonical known digest."""
    path = _created(tmp_path)
    with tgdb.TgdbStore.open(path) as store:
        with pytest.raises(tgdb.TgdbError, match="read-only"):
            store._put_object(io.BytesIO(b"payload"))
        with pytest.raises(tgdb.StoreCorrupt, match="malformed"):
            store._open_object("ABC")
        with pytest.raises(tgdb.TgdbError, match="is not stored"):
            store._open_object("0" * 64)
    with pytest.raises(tgdb.TgdbError, match="store is closed"):
        store.check()
    with pytest.raises(tgdb.TgdbError, match="store is closed"):
        store._put_object(io.BytesIO(b"payload"))


def test_existing_orphan_file_is_reused_only_when_its_bytes_match(
    tmp_path: Path,
) -> None:
    """A prepublication orphan can be adopted, but conflicting bytes refuse."""
    payload = b"file-resident"
    digest = hashlib.sha256(payload).hexdigest()
    with tgdb.TgdbStore.create(
        tmp_path / "good", limits=tgdb.TgdbLimits(inline_threshold=1)
    ) as store:
        target = _object_path(store.path, digest)
        target.parent.mkdir(parents=True)
        target.write_bytes(payload)
        assert store._put_object(io.BytesIO(payload)) == digest
        assert store._object_record(digest) == (len(payload), "file", None)

    with tgdb.TgdbStore.create(
        tmp_path / "bad", limits=tgdb.TgdbLimits(inline_threshold=1)
    ) as store:
        target = _object_path(store.path, digest)
        target.parent.mkdir(parents=True)
        target.write_bytes(b"x" * len(payload))
        with pytest.raises(tgdb.StoreCorrupt, match="instead of its content address"):
            store._put_object(io.BytesIO(payload))
        assert list((store.path / "staging").iterdir()) == []
        assert store._object_record(digest) is None


def test_check_distinguishes_structural_and_full_content_validation(
    tmp_path: Path,
) -> None:
    """Structural checks count bytes while full checks detect equal-size damage."""
    path = tmp_path / "store"
    with tgdb.TgdbStore.create(
        path, limits=tgdb.TgdbLimits(inline_threshold=4)
    ) as store:
        inline_digest = store._put_object(io.BytesIO(b"four"))
        file_digest = store._put_object(io.BytesIO(b"five!"))
        store._connection.execute(
            "UPDATE objects SET data = ? WHERE digest = ?",
            (b"ruin", inline_digest),
        )
        assert store.check().objects == 2
        with pytest.raises(tgdb.StoreCorrupt, match=inline_digest):
            store.check(full=True)
        with store._open_object(inline_digest) as reader:
            with pytest.raises(tgdb.StoreCorrupt, match="failed verification"):
                reader.read()

        store._connection.execute(
            "UPDATE objects SET data = ? WHERE digest = ?",
            (b"four", inline_digest),
        )
        _object_path(path, file_digest).write_bytes(b"ruin!")
        assert store.check().file_objects == 1
        with pytest.raises(tgdb.StoreCorrupt, match=file_digest):
            store.check(full=True)
        with store._open_object(file_digest) as reader:
            with pytest.raises(tgdb.StoreCorrupt, match="failed verification"):
                reader.read()


def test_missing_wrong_size_and_symlinked_object_files_refuse(
    tmp_path: Path,
) -> None:
    """Checks never follow substituted content paths or accept missing bytes."""
    for case in ("missing", "size", "symlink"):
        directory = tmp_path / case
        with tgdb.TgdbStore.create(
            directory, limits=tgdb.TgdbLimits(inline_threshold=1)
        ) as store:
            digest = store._put_object(io.BytesIO(b"payload"))
            target = _object_path(directory, digest)
            if case == "missing":
                target.unlink()
                message = "cannot read object"
            elif case == "size":
                target.write_bytes(b"short")
                message = "file has"
            else:
                payload = target.read_bytes()
                target.unlink()
                elsewhere = directory / "elsewhere"
                elsewhere.write_bytes(payload)
                target.symlink_to(elsewhere)
                message = "not a plain file"
            with pytest.raises(tgdb.StoreCorrupt, match=message):
                store.check()
            if case == "symlink":
                with pytest.raises(tgdb.StoreCorrupt, match=message):
                    store._open_object(digest)


def test_pool_directories_must_be_plain_and_present(tmp_path: Path) -> None:
    """Pool roots and digest shards cannot be files, links, or absent paths."""
    for case in ("absent", "file", "symlink"):
        directory = tmp_path / case
        with tgdb.TgdbStore.create(directory) as store:
            hashes = directory / "objects" / "sha256"
            hashes.rmdir()
            if case == "file":
                hashes.write_bytes(b"not a directory")
            elif case == "symlink":
                hashes.symlink_to(directory / "staging", target_is_directory=True)
            with pytest.raises(tgdb.StoreCorrupt, match="SHA-256 object directory"):
                store.check()


@pytest.mark.parametrize("case", ("root", "first", "second", "object"))
def test_check_refuses_non_derived_object_paths(tmp_path: Path, case: str) -> None:
    """An unrecorded object-pool entry must still have a digest-derived path."""
    with tgdb.TgdbStore.create(tmp_path / case) as store:
        hashes = store.path / "objects" / "sha256"
        if case == "root":
            (store.path / "objects" / "extra").write_bytes(b"")
        elif case == "first":
            (hashes / "zz").write_bytes(b"")
        elif case == "second":
            (hashes / "aa").mkdir()
            (hashes / "aa" / "zz").write_bytes(b"")
        else:
            (hashes / "aa" / "bb").mkdir(parents=True)
            (hashes / "aa" / "bb" / ("0" * 64)).write_bytes(b"")
        with pytest.raises(tgdb.StoreCorrupt, match="non-derived"):
            store.check()


def test_check_refuses_unreferenced_symlink_and_accepts_derived_orphan(
    tmp_path: Path,
) -> None:
    """Pool scans reject unused links while permitting a canonical orphan file."""
    with tgdb.TgdbStore.create(tmp_path / "store") as store:
        hashes = store.path / "objects" / "sha256"
        orphan_digest = "aabb" + ("0" * 60)
        orphan = hashes / "aa" / "bb" / orphan_digest
        orphan.parent.mkdir(parents=True)
        orphan.write_bytes(b"orphan")
        assert store.check().objects == 0

        (hashes / "cc").symlink_to(store.path / "staging", target_is_directory=True)
        with pytest.raises(tgdb.StoreCorrupt, match="object shard 'cc'"):
            store.check()


@pytest.mark.parametrize(
    ("record", "message"),
    [
        (("g" * 64, 0, "inline", b""), "digest"),
        ((("0" * 64), True, "inline", b""), "malformed size"),
        ((("0" * 64), -1, "inline", b""), "malformed size"),
        ((("0" * 64), 0, "inline", None), "malformed data"),
        ((("0" * 64), 0, "file", b""), "unexpected inline data"),
        ((("0" * 64), 0, "remote", None), "unknown residency"),
    ],
)
def test_object_record_validation_names_each_malformed_field(
    record: tuple[object, object, object, object], message: str
) -> None:
    """Corrupt object rows are diagnosed by the field that broke the schema."""
    with pytest.raises(tgdb.StoreCorrupt, match=message):
        tgdb._validate_object_record(*record)


def test_object_record_conflicts_roll_back_without_changing_bytes(
    tmp_path: Path,
) -> None:
    """A conflicting publication leaves the existing row and transaction intact."""
    with tgdb.TgdbStore.create(tmp_path / "store") as store:
        payload = b"stable"
        digest = store._put_object(io.BytesIO(payload))
        with pytest.raises(tgdb.StoreCorrupt, match="conflicting recorded sizes"):
            store._record_object(digest, len(payload) + 1, "inline", payload)
        assert store._connection.in_transaction is False
        assert store._object_record(digest) == (len(payload), "inline", payload)


def test_object_record_joins_and_does_not_commit_an_outer_transaction(
    tmp_path: Path,
) -> None:
    """A future batch can publish an object row in its one outer transaction."""
    with tgdb.TgdbStore.create(tmp_path / "store") as store:
        payload = b"pending"
        digest = hashlib.sha256(payload).hexdigest()
        store._connection.execute("BEGIN IMMEDIATE")
        store._record_object(digest, len(payload), "inline", payload)
        assert store._connection.in_transaction is True
        assert store._object_record(digest) == (len(payload), "inline", payload)
        with pytest.raises(tgdb.StoreCorrupt, match="conflicting recorded sizes"):
            store._record_object(digest, len(payload) + 1, "inline", payload)
        assert store._connection.in_transaction is True
        store._connection.execute("ROLLBACK")
        assert store._object_record(digest) is None


def test_staging_name_collision_never_removes_an_unowned_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exclusive staging collision leaves the existing entry untouched."""
    with tgdb.TgdbStore.create(
        tmp_path / "store", limits=tgdb.TgdbLimits(inline_threshold=1)
    ) as store:
        collision = store.path / "staging" / "fixed"
        collision.write_bytes(b"keep")
        monkeypatch.setattr(secrets, "token_hex", lambda _size: "fixed")
        with pytest.raises(FileExistsError):
            store._put_object(io.BytesIO(b"payload"))
        assert collision.read_bytes() == b"keep"


@pytest.mark.parametrize("result", (None, ("damaged",)))
def test_check_refuses_failed_sqlite_quick_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, result: tuple[str] | None
) -> None:
    """A missing or failed SQLite quick-check result is a store corruption."""

    class Cursor:
        def fetchone(self) -> tuple[str] | None:
            return result

    class Connection:
        def execute(self, _statement: str) -> Cursor:
            return Cursor()

    with tgdb.TgdbStore.create(tmp_path / "store") as store:
        with monkeypatch.context() as scoped:
            scoped.setattr(store, "_connection", Connection())
            message = "no result" if result is None else "damaged"
            with pytest.raises(tgdb.StoreCorrupt, match=message):
                store.check()


def test_check_and_lookup_wrap_catalog_read_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SQLite read failures leave the module through the corruption taxonomy."""

    class Connection:
        def execute(self, _statement: str, _values: object = ()) -> None:
            raise sqlite3.DatabaseError("broken page")

    with tgdb.TgdbStore.create(tmp_path / "store") as store:
        with monkeypatch.context() as scoped:
            scoped.setattr(store, "_connection", Connection())
            with pytest.raises(tgdb.StoreCorrupt, match="cannot check tgdb catalog"):
                store.check()
            with pytest.raises(tgdb.StoreCorrupt, match="cannot read object"):
                store._object_record("0" * 64)


def test_inline_size_corruption_is_found_before_hashing(tmp_path: Path) -> None:
    """A malformed inline byte count refuses even in a structural check."""
    with tgdb.TgdbStore.create(tmp_path / "store") as store:
        digest = store._put_object(io.BytesIO(b"payload"))
        store._connection.execute("PRAGMA ignore_check_constraints = ON")
        store._connection.execute(
            "UPDATE objects SET size = size + 1 WHERE digest = ?", (digest,)
        )
        with pytest.raises(tgdb.StoreCorrupt, match="stores 7 inline bytes"):
            store.check()
        with pytest.raises(tgdb.StoreCorrupt, match="stores 7 inline bytes"):
            tgdb._check_recorded_object(
                store.path, digest, (8, "inline", b"payload"), full=False
            )
        tgdb._check_recorded_object(
            store.path, digest, (7, "inline", b"payload"), full=False
        )


def test_open_and_full_check_wrap_file_open_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file that becomes unreadable is named as corrupt by both read paths."""
    with tgdb.TgdbStore.create(
        tmp_path / "store", limits=tgdb.TgdbLimits(inline_threshold=1)
    ) as store:
        digest = store._put_object(io.BytesIO(b"payload"))
        target = _object_path(store.path, digest)
        original_os_open = os.open

        def cannot_open(path: str | os.PathLike[str], flags: int) -> int:
            if Path(path) == target:
                raise PermissionError("denied")
            return original_os_open(path, flags)

        monkeypatch.setattr(os, "open", cannot_open)
        with pytest.raises(tgdb.StoreCorrupt, match="cannot open object"):
            store._open_object(digest)
        monkeypatch.undo()

        def cannot_hash(_source: object, _digest: object) -> object:
            raise PermissionError("denied")

        monkeypatch.setattr(hashlib, "file_digest", cannot_hash)
        with pytest.raises(tgdb.StoreCorrupt, match="cannot read object"):
            store.check(full=True)


def test_wal_refusal_closes_the_connection() -> None:
    """A runtime that declines WAL cannot leave an apparently writable store."""

    class Cursor:
        def fetchone(self) -> tuple[str]:
            return ("delete",)

    class Connection:
        closed = False

        def execute(self, _statement: str) -> Cursor:
            return Cursor()

        def close(self) -> None:
            self.closed = True

    connection = Connection()
    with pytest.raises(tgdb.TgdbError, match="refused WAL"):
        tgdb._configure_writable(connection)  # type: ignore[arg-type]
    assert connection.closed is True


def test_catalog_collections_keep_id_name_and_declared_order_separate(
    tmp_path: Path,
) -> None:
    """Collection insertion, movement, naming, and metadata remain independent."""
    path = tmp_path / "store"
    fixed_uid = bytes.fromhex("00" * 15 + "01")
    annotations = EditAnnotations(stage="catalog", fields={"pass": 1})
    with tgdb.TgdbStore.create(path) as store:
        with store.write(annotations=annotations) as transaction:
            assert transaction.commit_seq == 1
            last = transaction.create_collection("last", uid=fixed_uid)
            first = transaction.create_collection("first", position=0)
            transaction.create_collection("middle", position=1)
            receipt = transaction.commit()
        assert last == fixed_uid
        assert receipt.to_data() == {
            "commit_seq": 1,
            "kind": "catalog",
            "operations": [
                "create_collection",
                "create_collection",
                "create_collection",
            ],
        }
        assert [entry.name for entry in store.collections()] == [
            "first",
            "middle",
            "last",
        ]
        assert [entry.position for entry in store.collections()] == [0, 1, 2]
        assert store.collections()[2].uid == fixed_uid
        encoded = store._connection.execute(
            "SELECT annotations FROM commits WHERE seq = 1"
        ).fetchone()[0]
        assert json.loads(encoded) == annotations.to_data()

        with store.write() as transaction:
            transaction.rename_collection(first, "first")
            transaction.move_collection(first, 0)
            with pytest.raises(tgdb.TgdbError, match="already exists"):
                transaction.rename_collection(first, "middle")
            transaction.rename_collection(first, "renamed")
            transaction.move_collection(last, 0)
            receipt = transaction.commit()
        assert receipt.operations == (
            "rename_collection",
            "set_collection_positions",
        )
        assert [entry.name for entry in store.collections()] == [
            "last",
            "renamed",
            "middle",
        ]
        assert store.collections()[0].to_data() == {
            "uid": fixed_uid.hex(),
            "name": "last",
            "position": 0,
            "retired": False,
            "retired_commit": None,
        }

        with store.write() as transaction:
            transaction.retire_collection("middle")
            with pytest.raises(tgdb.TgdbError, match="already retired"):
                transaction.retire_collection("middle")
            retire = transaction.commit()
        assert [entry.name for entry in store.collections()] == ["last", "renamed"]
        retired = store.collections(include_retired=True)[2]
        assert retired.retired is True
        assert retired.retired_commit == retire.commit_seq
        with store.write() as transaction:
            transaction.restore_collection("middle")
            with pytest.raises(tgdb.TgdbError, match="already active"):
                transaction.restore_collection("middle")
            transaction.commit()
        assert [entry.name for entry in store.collections()] == [
            "last",
            "renamed",
            "middle",
        ]


def test_catalog_transactions_discard_explicitly_and_on_every_exit(
    tmp_path: Path,
) -> None:
    """Leaving a writer never publishes implicitly, including store close."""
    store = tgdb.TgdbStore.create(tmp_path / "store")
    transaction = store.write()
    transaction.create_collection("discarded")
    with pytest.raises(tgdb.TgdbError, match="already active"):
        store.write()
    transaction.discard()
    transaction.discard()
    assert store.collections() == ()
    with pytest.raises(tgdb.TgdbError, match="closed"):
        transaction.commit()

    with store.write() as empty:
        with pytest.raises(tgdb.TgdbError, match="no changes"):
            empty.commit()
    with store.write() as implicit:
        implicit.create_collection("implicit")
    assert store.collections() == ()

    active = store.write()
    active.create_collection("close-discard")
    store.close()
    assert active._active is False
    with pytest.raises(tgdb.TgdbError, match="closed"):
        active.__enter__()
    with tgdb.TgdbStore.open(store.path) as reopened:
        assert reopened.collections() == ()


def test_collection_undo_is_additive_reversible_and_stale_safe(tmp_path: Path) -> None:
    """Undo records another commit and refuses identities changed afterward."""
    with tgdb.TgdbStore.create(tmp_path / "store") as store:
        with store.write() as transaction:
            first = transaction.create_collection("first")
            create_receipt = transaction.commit()
        undo_create = store.undo(create_receipt.commit_seq)
        assert undo_create.kind == "undo"
        assert store.collections() == ()
        assert store.collections(include_retired=True)[0].uid == first
        redo_create = store.undo(undo_create.commit_seq)
        assert redo_create.operations == ("restore_collection",)
        assert store.collections()[0].uid == first

        with store.write() as transaction:
            transaction.rename_collection(first, "second")
            rename_receipt = transaction.commit()
        with store.write() as transaction:
            other = transaction.create_collection("other")
            transaction.commit()
        undo_rename = store.undo(rename_receipt.commit_seq)
        assert undo_rename.operations == ("rename_collection",)
        assert store.collections()[0].name == "first"
        with store.write() as transaction:
            transaction.rename_collection(first, "latest")
            transaction.commit()
        with pytest.raises(tgdb.StaleVersion, match="touched the same"):
            store.undo(undo_rename.commit_seq)
        assert any(entry.uid == other for entry in store.collections())

        with pytest.raises(TypeError, match="integer"):
            store.undo(True)
        with pytest.raises(ValueError, match="positive"):
            store.undo(0)
        with pytest.raises(tgdb.TgdbError, match="does not exist"):
            store.undo(999)
        store._connection.execute(
            "INSERT INTO commits(kind, annotations) VALUES ('empty', NULL)"
        )
        empty_seq = store._connection.execute(
            "SELECT max(seq) FROM commits"
        ).fetchone()[0]
        with pytest.raises(tgdb.TgdbError, match="no undoable"):
            store.undo(empty_seq)


def test_undo_rename_refuses_when_the_original_name_was_reassigned(
    tmp_path: Path,
) -> None:
    """Rename inverses refuse cleanly when another identity now owns the name."""
    with tgdb.TgdbStore.create(tmp_path / "collections") as store:
        with store.write() as transaction:
            first = transaction.create_collection("first")
            second = transaction.create_collection("second")
            transaction.commit()
        with store.write() as transaction:
            transaction.rename_collection(first, "renamed")
            renamed = transaction.commit()
        with store.write() as transaction:
            transaction.rename_collection(second, "first")
            transaction.commit()
        with pytest.raises(tgdb.StaleVersion, match="no longer available"):
            store.undo(renamed.commit_seq)
        assert [entry.name for entry in store.collections()] == ["renamed", "first"]
        assert store._connection.execute("SELECT count(*) FROM commits").fetchone() == (
            3,
        )

    with tgdb.TgdbStore.create(tmp_path / "instances") as store:
        with store.write() as transaction:
            collection = transaction.create_collection("collection")
            transaction.commit()
        first = _seed_instance(store, collection, "first", 0)
        second = _seed_instance(store, collection, "second", 1)
        with store.write() as transaction:
            transaction.rename(first, "renamed")
            renamed = transaction.commit()
        with store.write() as transaction:
            transaction.rename(second, "first")
            transaction.commit()
        with pytest.raises(tgdb.StaleVersion, match="no longer available"):
            store.undo(renamed.commit_seq)
        assert [entry.name for entry in store.instances()] == ["renamed", "first"]
        assert store._connection.execute("SELECT count(*) FROM commits").fetchone() == (
            3,
        )


def test_instance_catalog_operations_are_ordered_scoped_and_undoable(
    tmp_path: Path,
) -> None:
    """Instance lifecycle operations preserve ids and use collection-scoped names."""
    with tgdb.TgdbStore.create(tmp_path / "store") as store:
        with store.write() as transaction:
            first_collection = transaction.create_collection("first")
            second_collection = transaction.create_collection("second")
            transaction.commit()
        first = _seed_instance(store, first_collection, "shared", 0)
        second = _seed_instance(store, first_collection, "second", 1)
        other = _seed_instance(store, second_collection, "shared", 0)

        assert [entry.uid for entry in store.instances()] == [first, second, other]
        scoped = store.instances(first_collection)
        assert [entry.name for entry in scoped] == ["shared", "second"]
        assert scoped[0].to_data() == {
            "uid": first.hex(),
            "collection_uid": first_collection.hex(),
            "collection": "first",
            "name": "shared",
            "position": 0,
            "generation": 0,
            "retired": False,
            "retired_commit": None,
        }
        with store.write() as transaction:
            with pytest.raises(tgdb.TgdbError, match="ambiguous"):
                transaction.rename("shared", "ambiguous")
            transaction.rename(first, "shared")
            transaction.move(first, 0)
            with pytest.raises(tgdb.TgdbError, match="already exists"):
                transaction.rename(first, "second")
            transaction.rename(first, "renamed")
            transaction.move(first, 1)
            receipt = transaction.commit()
        assert receipt.operations == ("rename_instance", "set_instance_positions")
        assert [entry.uid for entry in store.instances("first")] == [second, first]
        assert [entry.generation for entry in store.instances("first")] == [1, 2]

        with store.write() as transaction:
            transaction.retire(first)
            with pytest.raises(tgdb.TgdbError, match="already retired"):
                transaction.retire(first)
            retired_receipt = transaction.commit()
        assert [entry.uid for entry in store.instances("first")] == [second]
        retired = store.instances("first", include_retired=True)[1]
        assert retired.retired is True
        assert retired.retired_commit == retired_receipt.commit_seq
        undo_retire = store.undo(retired_receipt.commit_seq)
        assert undo_retire.operations == ("restore_instance",)
        assert store.instances("first", include_retired=True)[1].retired is False
        with store.write() as transaction:
            with pytest.raises(tgdb.TgdbError, match="already active"):
                transaction.restore(first)
            transaction.retire(first)
            transaction.commit()
        with store.write() as transaction:
            transaction.restore(first, collection="first")
            transaction.commit()

        with store.write() as transaction:
            transaction.retire_collection(second_collection)
            transaction.commit()
        assert other not in {entry.uid for entry in store.instances()}
        assert other in {entry.uid for entry in store.instances(include_retired=True)}


@pytest.mark.parametrize(
    ("value", "error", "message"),
    [
        (b"short", ValueError, "exactly 16 bytes"),
        (123, TypeError, "must be a string"),
        ("", ValueError, "must not be empty"),
        ("x" * 4097, ValueError, "exceeds 4096"),
    ],
)
def test_catalog_identity_and_name_validation(
    tmp_path: Path, value: object, error: type[Exception], message: str
) -> None:
    """Catalog operands have bounded, typed public spellings."""
    with tgdb.TgdbStore.create(tmp_path / "store") as store:
        with store.write() as transaction:
            if isinstance(value, bytes):
                with pytest.raises(error, match=message):
                    transaction.create_collection("valid", uid=value)
            else:
                with pytest.raises(error, match=message):
                    transaction.create_collection(value)  # type: ignore[arg-type]


def test_catalog_positions_and_resolvers_refuse_invalid_operands(
    tmp_path: Path,
) -> None:
    """Positions, selectors, missing rows, and retired scopes refuse explicitly."""
    with tgdb.TgdbStore.create(tmp_path / "store") as store:
        with store.write() as transaction:
            collection = transaction.create_collection("collection")
            transaction.commit()
        instance = _seed_instance(store, collection, "instance", 0)
        with store.write() as transaction:
            for position in (True, "0"):
                with pytest.raises(TypeError, match="integer"):
                    transaction.move_collection(collection, position)  # type: ignore[arg-type]
            with pytest.raises(ValueError, match="between"):
                transaction.move_collection(collection, 1)
            with pytest.raises(ValueError, match="between"):
                transaction.move(instance, -1)
            with pytest.raises(TypeError, match="collection must"):
                transaction.rename_collection(3, "new")  # type: ignore[arg-type]
            with pytest.raises(tgdb.TgdbError, match="does not exist"):
                transaction.rename_collection("missing", "new")
            with pytest.raises(ValueError, match="exactly 16 bytes"):
                transaction.rename_collection(b"bad", "new")
            with pytest.raises(TypeError, match="instance must"):
                transaction.rename(3, "new")  # type: ignore[arg-type]
            with pytest.raises(tgdb.TgdbError, match="does not exist"):
                transaction.rename("missing", "new")
            with pytest.raises(ValueError, match="exactly 16 bytes"):
                transaction.rename(b"bad", "new")
        with store.write() as transaction:
            transaction.retire_collection(collection)
            transaction.commit()
        with pytest.raises(tgdb.TgdbError, match="retired"):
            tgdb._resolve_collection(
                store._connection, collection, include_retired=False
            )


def test_catalog_duplicate_creation_and_annotation_types_are_refused(
    tmp_path: Path,
) -> None:
    """Collection constraints and commit metadata refuse before publication."""
    with tgdb.TgdbStore.create(tmp_path / "store") as store:
        with pytest.raises(TypeError, match="EditAnnotations"):
            store.write(annotations="metadata")  # type: ignore[arg-type]
        uid = bytes.fromhex("11" * 16)
        with store.write() as transaction:
            transaction.create_collection("one", uid=uid)
            with pytest.raises(tgdb.TgdbError, match="uid .* already exists"):
                transaction.create_collection("two", uid=uid)
            with pytest.raises(tgdb.TgdbError, match="name 'one' already exists"):
                transaction.create_collection("one")
            transaction.commit()
        store.check()


def test_catalog_action_decoding_and_replay_corruption_guards(tmp_path: Path) -> None:
    """Malformed recorded operands never become executable SQL."""
    with tgdb.TgdbStore.create(tmp_path / "store") as store:
        with store.write() as transaction:
            collection = transaction.create_collection("collection")
            transaction.commit()
        instance = _seed_instance(store, collection, "instance", 0)
        missing = bytes.fromhex("ff" * 16)
        touch = [f"collection:{missing.hex()}"]
        with store.write() as transaction:
            malformed_actions: tuple[dict[str, object], ...] = (
                {"action": "set_collection_positions", "positions": {}, "touches": []},
                {
                    "action": "set_collection_positions",
                    "positions": {1: 0},
                    "touches": [],
                },
                {
                    "action": "set_collection_positions",
                    "positions": {missing.hex(): True},
                    "touches": touch,
                },
                {
                    "action": "set_collection_positions",
                    "positions": {missing.hex(): 0},
                    "touches": touch,
                },
                {"action": "unknown", "touches": []},
            )
            for action in malformed_actions:
                with pytest.raises(tgdb.StoreCorrupt):
                    transaction._apply_action(action)
            missing_actions: tuple[dict[str, object], ...] = (
                tgdb._name_action("collection", missing, "name"),
                {
                    "action": "retire_collection",
                    "touches": touch,
                    "uid": missing.hex(),
                },
                {
                    "action": "retire_instance",
                    "touches": [f"instance:{missing.hex()}"],
                    "uid": missing.hex(),
                },
            )
            for missing_action in missing_actions:
                with pytest.raises(tgdb.StoreCorrupt, match="identity is missing"):
                    transaction._apply_action(missing_action)
            with pytest.raises(tgdb.StaleVersion, match="lifecycle"):
                transaction._apply_action(
                    {
                        "action": "restore_collection",
                        "touches": [f"collection:{collection.hex()}"],
                        "uid": collection.hex(),
                    }
                )
            with pytest.raises(tgdb.StaleVersion, match="lifecycle"):
                transaction._apply_action(
                    {
                        "action": "restore_instance",
                        "touches": [f"instance:{instance.hex()}"],
                        "uid": instance.hex(),
                    }
                )
            with pytest.raises(tgdb.StoreCorrupt, match="no action"):
                transaction._record({}, {})
            store._writer = None
            transaction.discard()

        assert tgdb._sqlite_busy(sqlite3.OperationalError("ordinary")) is False
        tgdb._set_positions(store._connection, "collections", {})
        for value in ("bad", "G" * 32):
            with pytest.raises(tgdb.StoreCorrupt, match="identity"):
                tgdb._uid_from_hex(value)
        with pytest.raises(tgdb.StoreCorrupt, match="identity"):
            tgdb._action_uid({})
        touch_actions: tuple[dict[str, object], ...] = ({}, {"touches": [1]})
        for touch_action in touch_actions:
            with pytest.raises(tgdb.StoreCorrupt, match="touches"):
                tgdb._action_touches(touch_action)
        for decode_value in (None, "{", "[]"):
            with pytest.raises(tgdb.StoreCorrupt, match="operation"):
                tgdb._decode_action(cast(object, decode_value))


def test_catalog_check_finds_foreign_keys_and_malformed_operations(
    tmp_path: Path,
) -> None:
    """Structural checks include catalog references and recorded JSON operands."""
    path = _created(tmp_path)
    with closing(sqlite3.connect(path / "catalog.sqlite3", autocommit=True)) as raw:
        raw.execute("PRAGMA foreign_keys = OFF")
        raw.execute(
            "INSERT INTO collections(uid, name, position, retired_commit) "
            "VALUES (?, 'broken', 0, 999)",
            (bytes.fromhex("22" * 16),),
        )
    with tgdb.TgdbStore.open(path) as store:
        with pytest.raises(tgdb.StoreCorrupt, match="foreign-key"):
            store.check()

    path = tmp_path / "operations"
    with tgdb.TgdbStore.create(path) as store:
        with store.write() as transaction:
            transaction.create_collection("collection")
            transaction.commit()
        store._connection.execute(
            "UPDATE commit_ops SET inverse = '{' WHERE commit_seq = 1"
        )
        with pytest.raises(tgdb.StoreCorrupt, match="operation is malformed"):
            store.check()

    path = tmp_path / "touches"
    with tgdb.TgdbStore.create(path) as store:
        with store.write() as transaction:
            transaction.create_collection("collection")
            transaction.commit()
        forward = json.loads(
            store._connection.execute(
                "SELECT forward FROM commit_ops WHERE commit_seq = 1"
            ).fetchone()[0]
        )
        forward["touches"] = ["bogus"]
        store._connection.execute(
            "UPDATE commit_ops SET forward = ? WHERE commit_seq = 1",
            (json.dumps(forward),),
        )
        with pytest.raises(tgdb.StoreCorrupt, match="touches do not match"):
            store.check()


class _FailingConnection:
    """Delegate SQLite except for one selected statement used by failure tests."""

    def __init__(
        self,
        wrapped: sqlite3.Connection,
        statement: str,
        error: sqlite3.DatabaseError,
        *,
        transaction_state: bool | None = None,
    ) -> None:
        self.wrapped = wrapped
        self.statement = statement
        self.error = error
        self.transaction_state = transaction_state

    @property
    def in_transaction(self) -> bool:
        """Expose the delegated transaction state."""
        return (
            self.wrapped.in_transaction
            if self.transaction_state is None
            else self.transaction_state
        )

    def execute(self, sql: str, parameters: object = ()) -> sqlite3.Cursor:
        """Raise for the selected statement and delegate every other statement."""
        if sql.startswith(self.statement):
            raise self.error
        return self.wrapped.execute(sql, parameters)  # type: ignore[arg-type]

    def __getattr__(self, name: str) -> object:
        """Delegate methods not involved in the selected failure."""
        return getattr(self.wrapped, name)


@pytest.mark.parametrize(
    ("statement", "error", "expected"),
    [
        ("BEGIN", sqlite3.OperationalError("begin failed"), tgdb.StoreCorrupt),
        (
            "INSERT INTO commits",
            sqlite3.OperationalError("insert failed"),
            tgdb.StoreCorrupt,
        ),
        ("BEGIN", sqlite3.DatabaseError("begin failed"), tgdb.StoreCorrupt),
        (
            "INSERT INTO commits",
            sqlite3.DatabaseError("insert failed"),
            tgdb.StoreCorrupt,
        ),
    ],
)
def test_catalog_begin_failures_leave_no_transaction(
    tmp_path: Path,
    statement: str,
    error: sqlite3.DatabaseError,
    expected: type[Exception],
) -> None:
    """Writer setup failures roll back and remain typed store refusals."""
    store = tgdb.TgdbStore.create(tmp_path / "store")
    wrapped = store._connection
    store._connection = _FailingConnection(wrapped, statement, error)  # type: ignore[assignment]
    with pytest.raises(expected, match="cannot begin"):
        store.write()
    assert wrapped.in_transaction is False
    store.close()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (sqlite3.OperationalError("commit failed"), tgdb.StoreCorrupt),
        (sqlite3.DatabaseError("commit failed"), tgdb.StoreCorrupt),
    ],
)
def test_catalog_commit_failures_roll_back_and_close_writer(
    tmp_path: Path,
    error: sqlite3.DatabaseError,
    expected: type[Exception],
) -> None:
    """Commit failures publish nothing and release the store's writer slot."""
    store = tgdb.TgdbStore.create(tmp_path / "store")
    wrapped = store._connection
    proxy = _FailingConnection(wrapped, "COMMIT", error)
    store._connection = proxy  # type: ignore[assignment]
    transaction = store.write()
    transaction.create_collection("collection")
    with pytest.raises(expected, match="cannot commit"):
        transaction.commit()
    assert wrapped.in_transaction is False
    assert store._writer is None
    store.close()


@pytest.mark.parametrize(
    "error",
    (sqlite3.OperationalError("commit failed"), sqlite3.DatabaseError("commit failed")),
)
def test_catalog_commit_failure_handles_an_already_ended_transaction(
    tmp_path: Path, error: sqlite3.DatabaseError
) -> None:
    """A commit error remains typed when SQLite already ended the transaction."""
    store = tgdb.TgdbStore.create(tmp_path / "store")
    wrapped = store._connection
    store._connection = _FailingConnection(  # type: ignore[assignment]
        wrapped, "COMMIT", error, transaction_state=False
    )
    transaction = store.write()
    transaction.create_collection("collection")
    with pytest.raises(tgdb.StoreCorrupt, match="cannot commit"):
        transaction.commit()
    wrapped.rollback()
    store.close()


def test_catalog_discard_handles_an_already_ended_transaction(tmp_path: Path) -> None:
    """Discard closes its handle even when SQLite has no transaction to roll back."""
    store = tgdb.TgdbStore.create(tmp_path / "store")
    transaction = store.write()
    store._connection.execute("ROLLBACK")
    transaction.discard()
    assert transaction._active is False
    store.close()


def test_catalog_listing_wraps_database_errors(tmp_path: Path) -> None:
    """Collection and instance listing keep SQLite details in the store taxonomy."""
    store = tgdb.TgdbStore.create(tmp_path / "store")
    wrapped = store._connection
    store._connection = _FailingConnection(  # type: ignore[assignment]
        wrapped, "SELECT uid, name", sqlite3.DatabaseError("list failed")
    )
    with pytest.raises(tgdb.StoreCorrupt, match="cannot list collections"):
        store.collections()
    store._connection = _FailingConnection(  # type: ignore[assignment]
        wrapped, "SELECT i.uid", sqlite3.DatabaseError("list failed")
    )
    with pytest.raises(tgdb.StoreCorrupt, match="cannot list instances"):
        store.instances()
    store.close()


@pytest.mark.parametrize("statement", ("BEGIN", "COMMIT"))
def test_catalog_busy_errors_use_the_specific_refusal(
    tmp_path: Path, statement: str
) -> None:
    """SQLite lock exhaustion is distinguishable from catalog corruption."""
    store = tgdb.TgdbStore.create(tmp_path / "store")
    wrapped = store._connection
    error = sqlite3.OperationalError("busy")
    error.sqlite_errorcode = sqlite3.SQLITE_BUSY
    store._connection = _FailingConnection(  # type: ignore[assignment]
        wrapped, statement, error
    )
    if statement == "BEGIN":
        with pytest.raises(tgdb.StoreBusy, match="remained busy"):
            store.write()
    else:
        transaction = store.write()
        transaction.create_collection("collection")
        with pytest.raises(tgdb.StoreBusy, match="remained busy"):
            transaction.commit()
    store.close()


def test_refusal_taxonomy_is_public_staged_and_specific() -> None:
    """Every planned store refusal is catchable through one staged base."""
    for error_type in (
        tgdb.StoreBusy,
        tgdb.StaleVersion,
        tgdb.StaleJournalBase,
        tgdb.StoreCorrupt,
    ):
        error = error_type("condition")
        assert isinstance(error, Refusal)
        assert error.stage is RefusalStage.SEMANTICS
    assert tgdb.__all__ == [
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


def test_only_tgdb_and_the_cli_import_filesystem_or_sqlite_modules() -> None:
    """The store is the library's sole filesystem and SQLite boundary."""
    root = Path(__file__).parents[1] / "src" / "tiergraph"
    permitted = {Path("tgdb.py"), Path("cli/__init__.py")}
    imports: list[tuple[Path, str]] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(
                    (path.relative_to(root), alias.name)
                    for alias in node.names
                    if alias.name in {"os", "pathlib", "sqlite3"}
                )
            elif isinstance(node, ast.ImportFrom) and node.module in {
                "os",
                "pathlib",
                "sqlite3",
            }:
                imports.append((path.relative_to(root), node.module))
    assert imports
    assert all(path in permitted for path, _module in imports)
