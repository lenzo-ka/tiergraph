"""Exercise the lazy tgdb catalog skeleton and its refusal boundaries."""

from __future__ import annotations

import ast
import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from typing import Literal

import pytest

import tiergraph.tgdb as tgdb
from tiergraph import Refusal, RefusalStage


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
        "schema_version": 1,
        "layout_version": 1,
        "index_version": 1,
        "inline_threshold": 1234,
        "fingerprint_domain": "tiergraph-equivalence/1",
        "mode": "rw",
    }
    assert len(bytes.fromhex(info.store_uid)) == 16
    connection = store._connection
    assert connection.execute("PRAGMA application_id").fetchone() == (0x54474442,)
    assert connection.execute("PRAGMA user_version").fetchone() == (1,)
    assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    assert connection.execute("PRAGMA synchronous").fetchone() == (2,)
    assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
    assert connection.execute("PRAGMA trusted_schema").fetchone() == (0,)
    assert connection.execute("PRAGMA busy_timeout").fetchone() == (2345,)
    assert connection.execute("PRAGMA wal_autocheckpoint").fetchone() == (1000,)
    assert connection.execute(
        "SELECT strict FROM pragma_table_list WHERE name = 'store_meta'"
    ).fetchone() == (1,)
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
    _set_pragma(path, "user_version", 2)
    with pytest.raises(tgdb.StoreSchemaTooNew) as caught:
        tgdb.TgdbStore.open(path)
    assert caught.value.found == 2
    assert caught.value.supported == 1
    assert "2" in str(caught.value) and "1" in str(caught.value)
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

    _set_pragma(path, "user_version", 0)
    with tgdb.TgdbStore.open(path) as store:
        assert store.info().schema_version == 0
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
