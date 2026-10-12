"""Exercise the tgdb command group as a thin lazy client of the store API."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import tiergraph.cli as cli
import tiergraph.tgdb as tgdb
from tiergraph import (
    BLOB_NAMESPACE,
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    EquivalenceView,
    Graph,
    Item,
    NamespaceDeclaration,
    QualifiedName,
    RefusalStage,
    Tier,
    TierDeclaration,
    XsdType,
    declare_blob_vocabulary,
    diff,
    dump_bytes,
    patch_dumps,
    patch_loads,
)
from tiergraph.cli import _tgdb_index_value, build_parser, main


def _cli_blob_graph(
    payload: bytes, media_type: str = "application/octet-stream"
) -> Graph:
    """Return one graph declaring a single external payload."""
    namespace = "urn:tgdb:cli-blob"
    blobs = QualifiedName(namespace, "resources")
    duration = QualifiedName(namespace, "duration")

    def blob_value(local: str, value_type: XsdType, lexical: str) -> AttributeValue:
        return AttributeValue(QualifiedName(BLOB_NAMESPACE, local), value_type, lexical)

    editor = Graph((), (), ()).edit()
    declare_blob_vocabulary(editor)
    editor.declare(NamespaceDeclaration("test", namespace))
    editor.declare(TierDeclaration(blobs, "Resources"))
    editor.declare(
        AttributeDeclaration(duration, AttributeDomain.ITEM, XsdType.DECIMAL)
    )
    editor.insert_item(
        blobs,
        0,
        Item(
            "payload",
            (
                blob_value(
                    "sha256", XsdType.STRING, hashlib.sha256(payload).hexdigest()
                ),
                blob_value("size", XsdType.INTEGER, str(len(payload))),
                blob_value("media-type", XsdType.STRING, media_type),
                blob_value("schema", XsdType.STRING, "urn:example:binary:v1"),
                AttributeValue(duration, XsdType.DECIMAL, "2.5"),
            ),
        ),
    )
    return editor.freeze()


def test_building_tgdb_help_does_not_import_tgdb_or_sqlite() -> None:
    """Command registration stays lazy until a tgdb handler actually runs."""
    source = (
        "import json, sys; from tiergraph.cli import build_parser; build_parser(); "
        "print(json.dumps({"
        "'tgdb': 'tiergraph.tgdb' in sys.modules, "
        "'sqlite': 'sqlite3' in sys.modules}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", source], check=True, capture_output=True, text=True
    )
    assert json.loads(result.stdout) == {"sqlite": False, "tgdb": False}


def test_tgdb_init_and_info_plain_and_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The command creates a configured store and reports stable public metadata."""
    path = tmp_path / "corpus.tgdb"
    assert main(["tgdb", "init", str(path), "--inline-threshold", "1234"]) == 0
    assert capsys.readouterr().out == f"initialized {path}\n"

    assert main(["tgdb", "info", str(path)]) == 0
    plain = capsys.readouterr().out
    assert "schema_version: 10\n" in plain
    assert "inline_threshold: 1234\n" in plain
    assert plain.endswith("mode: ro\n")

    assert main(["tgdb", "info", str(path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report == {
        "fingerprint_domain": "tiergraph-equivalence/1",
        "index_version": 6,
        "inline_threshold": 1234,
        "layout_version": 1,
        "mode": "ro",
        "schema_version": 10,
        "store_uid": report["store_uid"],
    }
    assert len(bytes.fromhex(report["store_uid"])) == 16


def test_tgdb_backup_and_migrate_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Maintenance commands copy stores and report explicit schema movement."""
    path = tmp_path / "corpus.tgdb"
    destination = tmp_path / "backup.tgdb"
    assert main(["tgdb", "init", str(path)]) == 0
    capsys.readouterr()
    assert main(["tgdb", "backup", str(path), str(destination)]) == 0
    assert capsys.readouterr().out == f"backed up {path} to {destination}\n"
    with tgdb.TgdbStore.open(destination) as backup:
        assert backup.info().schema_version == 10

    assert main(["tgdb", "migrate", str(path)]) == 0
    assert capsys.readouterr().out == "already at schema version 10\n"
    with sqlite3.connect(path / "catalog.sqlite3", autocommit=True) as connection:
        connection.execute(
            "UPDATE store_meta SET value = '5' WHERE key = 'index_version'"
        )
    assert main(["tgdb", "migrate", str(path)]) == 0
    assert capsys.readouterr().out == (
        "already at schema version 10; rebuild derived indexes\n"
    )
    with sqlite3.connect(path / "catalog.sqlite3", autocommit=True) as connection:
        connection.execute("PRAGMA user_version = 9")
        connection.execute(
            "UPDATE store_meta SET value = '6' WHERE key = 'index_version'"
        )
    assert main(["tgdb", "migrate", str(path)]) == 0
    assert capsys.readouterr().out == (
        "migrated schema 9 to 10; backup catalog.pre-9.sqlite3\n"
    )

    (path / "catalog.pre-9.sqlite3").unlink()
    with sqlite3.connect(path / "catalog.sqlite3", autocommit=True) as connection:
        connection.execute("PRAGMA user_version = 9")
        connection.execute(
            "UPDATE store_meta SET value = '5' WHERE key = 'index_version'"
        )
    assert main(["tgdb", "migrate", str(path)]) == 0
    assert capsys.readouterr().out == (
        "migrated schema 9 to 10; backup catalog.pre-9.sqlite3; "
        "rebuild derived indexes\n"
    )

    (path / "catalog.pre-9.sqlite3").unlink()
    with sqlite3.connect(path / "catalog.sqlite3", autocommit=True) as connection:
        connection.execute("PRAGMA user_version = 9")
        connection.execute(
            "UPDATE store_meta SET value = '6' WHERE key = 'index_version'"
        )
    assert main(["tgdb", "migrate", str(path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report == {
        "backup": str(path / "catalog.pre-9.sqlite3"),
        "migrated_versions": [10],
        "previous_version": 9,
        "reindex_required": False,
        "schema_version": 10,
    }


def test_tgdb_measure_corpus_plain_and_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The corpus measurement command reports every required resource measure."""
    json_path = tmp_path / "json.tgdb"
    assert (
        main(
            [
                "tgdb",
                "measure-corpus",
                str(json_path),
                "--instances",
                "1",
                "--items-per-instance",
                "2",
                "--change-passes",
                "1",
                "--inline-threshold",
                "128",
                "--json",
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["instance_count"] == 1
    assert report["items_per_instance"] == 2
    assert report["change_passes"] == 1
    assert report["inline_threshold"] == 128
    assert [measurement["name"] for measurement in report["passes"]] == [
        "seed",
        "pass-1",
        "fixpoint",
    ]
    assert report["passes"][1]["replay_verification_seconds"] > 0
    assert (
        report["passes"][1]["compact_document_bytes"]
        < report["passes"][1]["document_bytes"]
    )
    assert report["fixpoint_changed_instances"] == 0

    plain_path = tmp_path / "plain.tgdb"
    assert (
        main(
            [
                "tgdb",
                "measure-corpus",
                str(plain_path),
                "--instances",
                "1",
                "--items-per-instance",
                "1",
                "--change-passes",
                "1",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert output.startswith(
        "pass\tseconds\treplay_seconds\tpeak_rss_bytes\twal_growth_bytes\t"
        "object_growth_bytes\tobject_bytes\tdocument_bytes\t"
        "compact_document_bytes\tpatch_bytes\tinline_object_bytes\t"
        "file_object_bytes\t"
        "history_versions\tversions_written\tpatch_versions\t"
        "snapshot_versions\n"
    )
    assert "seed\t" in output
    assert "pass-1\t" in output
    assert "fixpoint\t" in output
    assert output.splitlines()[-1].startswith("fixpoint changed: 0; query seconds: ")


def test_tgdb_check_reports_structural_and_full_success(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The check command exposes both verification levels without mutation."""
    path = tmp_path / "corpus.tgdb"
    assert main(["tgdb", "init", str(path)]) == 0
    capsys.readouterr()
    assert main(["tgdb", "check", str(path)]) == 0
    assert capsys.readouterr().out == "ok: structural check, 0 objects, 0 bytes\n"
    assert main(["tgdb", "check", str(path), "--full"]) == 0
    assert capsys.readouterr().out == "ok: full check, 0 objects, 0 bytes\n"


def test_tgdb_blob_commands_queries_and_repack(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """TG9 CLI paths ingest, query, verify, retrieve, and repack payloads."""
    store = tmp_path / "corpus.tgdb"
    payload = b"payload bytes"
    payload_file = tmp_path / "payload.bin"
    graph_file = tmp_path / "graph.json"
    output = tmp_path / "copy.bin"
    digest = hashlib.sha256(payload).hexdigest()
    payload_file.write_bytes(payload)
    graph_file.write_bytes(dump_bytes(_cli_blob_graph(payload)))
    assert main(["tgdb", "init", str(store)]) == 0
    capsys.readouterr()
    assert main(["tgdb", "collection", "create", str(store), "corpus"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "tgdb",
                "add",
                str(store),
                "corpus",
                "sample",
                str(graph_file),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        main(
            [
                "tgdb",
                "find",
                str(store),
                "--uses-blob",
                digest,
                "--media-type",
                "application/octet-stream",
                "--missing-blob",
                "--count",
            ]
        )
        == 0
    )
    assert capsys.readouterr().out == "1\n"
    assert main(["tgdb", "blob", "put", str(store), str(payload_file)]) == 0
    assert capsys.readouterr().out == f"{digest}\t{len(payload)}\n"
    assert main(["tgdb", "find", str(store), "--missing-blob", "--count"]) == 0
    assert capsys.readouterr().out == "0\n"
    assert main(["tgdb", "blob", "get", str(store), digest, "-o", str(output)]) == 0
    assert capsys.readouterr().out == ""
    assert output.read_bytes() == payload
    assert main(["tgdb", "blob", "verify", str(store), digest]) == 0
    assert capsys.readouterr().out == "ok\n"
    assert main(["tgdb", "blob", "verify", str(store)]) == 0
    assert capsys.readouterr().out == "ok\n"
    assert main(["tgdb", "repack", str(store), "--threshold", "0"]) == 0
    repacked = capsys.readouterr().out
    assert "threshold 0 bytes" in repacked
    assert "file" in repacked
    assert main(["tgdb", "repack", str(store), "--threshold", "0"]) == 0
    assert capsys.readouterr().out == "unchanged: threshold 0 bytes\n"

    missing = "f" * 64
    assert main(["tgdb", "blob", "verify", str(store), missing]) == 1
    assert f"object {missing} is not stored" in capsys.readouterr().err
    assert (
        main(
            [
                "tgdb",
                "blob",
                "get",
                str(store),
                missing,
                "-o",
                str(tmp_path / "missing.bin"),
            ]
        )
        == 1
    )
    assert f"object {missing} is not stored" in capsys.readouterr().err


def test_tgdb_inspect_reports_and_publishes_attached_graph_facts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The inspect command selects payloads and can publish one graph version."""
    store = tmp_path / "corpus.tgdb"
    payload_file = tmp_path / "nested.json"
    graph_file = tmp_path / "graph.json"
    binary_file = tmp_path / "audio.bin"
    binary_graph_file = tmp_path / "audio-graph.json"
    payload = dump_bytes(Graph((), (), ()))
    binary = b"binary payload"
    digest = hashlib.sha256(payload).hexdigest()
    payload_file.write_bytes(payload)
    binary_file.write_bytes(binary)
    graph_file.write_bytes(
        dump_bytes(_cli_blob_graph(payload, "application/vnd.tiergraph+json"))
    )
    binary_graph_file.write_bytes(dump_bytes(_cli_blob_graph(binary)))
    assert main(["tgdb", "init", str(store)]) == 0
    capsys.readouterr()
    assert main(["tgdb", "collection", "create", str(store), "corpus"]) == 0
    capsys.readouterr()
    assert main(["tgdb", "blob", "put", str(store), str(payload_file)]) == 0
    capsys.readouterr()
    assert main(["tgdb", "blob", "put", str(store), str(binary_file)]) == 0
    capsys.readouterr()
    assert main(["tgdb", "add", str(store), "corpus", "sample", str(graph_file)]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "tgdb",
                "add",
                str(store),
                "corpus",
                "binary",
                str(binary_graph_file),
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert (
        main(
            [
                "tgdb",
                "inspect",
                str(store),
                digest,
                "--media-type",
                "application/vnd.tiergraph+json",
                "--json",
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["inspected"] == 1
    assert report["changed"] == 1
    assert report["results"][0]["inspector"] == "tiergraph-graph"
    assert report["published"] is None

    assert (
        main(
            [
                "tgdb",
                "inspect",
                str(store),
                "--inspector",
                "tiergraph-graph",
                "--publish",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "inspected 1 payloads, 6 facts, 0 changed, commit " in output
    with tgdb.TgdbStore.open(store) as opened:
        assert opened.get("sample").stage == "inspect"


def test_tgdb_column_commands_and_find_filter(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The TG11 shell declares, lists, queries, and drops blob columns."""
    store = tmp_path / "corpus.tgdb"
    graph_path = tmp_path / "graph.json"
    graph_path.write_bytes(dump_bytes(_cli_blob_graph(b"payload")))
    assert main(["tgdb", "init", str(store)]) == 0
    capsys.readouterr()
    assert main(["tgdb", "collection", "create", str(store), "corpus"]) == 0
    capsys.readouterr()
    assert main(["tgdb", "add", str(store), "corpus", "sample", str(graph_path)]) == 0
    capsys.readouterr()

    assert main(["tgdb", "column", "list", str(store), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []
    assert (
        main(
            [
                "tgdb",
                "column",
                "declare",
                str(store),
                "duration",
                "application/*",
                "urn:tgdb:cli-blob|duration",
                "decimal",
            ]
        )
        == 0
    )
    assert "declare column 'duration'" in capsys.readouterr().out
    assert main(["tgdb", "column", "list", str(store)]) == 0
    assert capsys.readouterr().out.endswith(
        "0\tduration\tapplication/*\turn:tgdb:cli-blob|duration\tdecimal\n"
    )
    assert main(["tgdb", "column", "list", str(store), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == [
        {
            "name": "duration",
            "position": 0,
            "media_type": "application/*",
            "attribute": "urn:tgdb:cli-blob|duration",
            "value_type": "decimal",
        }
    ]
    for column_filter in ("duration", "duration=2.5", "duration=:2:3"):
        assert (
            main(
                [
                    "tgdb",
                    "find",
                    str(store),
                    "--column",
                    column_filter,
                    "--count",
                ]
            )
            == 0
        )
        assert capsys.readouterr().out == "1\n"
    assert (
        main(
            [
                "tgdb",
                "find",
                str(store),
                "--column",
                "duration",
                "--column",
                "duration=2.5",
                "--count",
            ]
        )
        == 0
    )
    assert capsys.readouterr().out == "1\n"
    for column_filter in ('duration="wrong"', "duration=true"):
        assert (
            main(
                [
                    "tgdb",
                    "find",
                    str(store),
                    "--column",
                    column_filter,
                    "--count",
                ]
            )
            == 1
        )
        error = capsys.readouterr().err
        assert "blob column 'duration'" in error
        assert "Traceback" not in error
    assert main(["tgdb", "column", "drop", str(store), "duration"]) == 0
    assert "drop column 'duration'" in capsys.readouterr().out
    assert main(["tgdb", "find", str(store), "--column", "duration"]) == 1
    assert "not declared" in capsys.readouterr().err
    assert cli._column_media_type_argument("application/octet-stream") == (
        "application/octet-stream"
    )
    with pytest.raises(argparse.ArgumentTypeError, match="column"):
        cli._tgdb_column_filter("=1")


@pytest.mark.parametrize(
    ("command", "resolver_result", "message"),
    (
        ("get", "missing", "is not stored"),
        ("get", "unverified", "was not fully verified"),
        ("verify", "missing", "disappeared during verification"),
        ("verify", "unverified", "was not fully verified"),
    ),
)
def test_tgdb_blob_commands_refuse_resolver_races_and_failed_verification(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    resolver_result: str,
    message: str,
) -> None:
    """Payload reads fail closed if resolution races or verification fails."""
    payload = b"payload bytes"
    digest = hashlib.sha256(payload).hexdigest()
    store = tmp_path / "corpus.tgdb"
    source = tmp_path / "payload.bin"
    source.write_bytes(payload)
    assert main(["tgdb", "init", str(store)]) == 0
    capsys.readouterr()
    assert main(["tgdb", "blob", "put", str(store), str(source)]) == 0
    capsys.readouterr()

    class UnverifiedReader(io.BytesIO):
        @property
        def verified(self) -> bool:
            return False

    class BrokenResolver:
        def open(self, ref: object, href: object) -> io.BytesIO | None:
            del ref, href
            if resolver_result == "missing":
                return None
            return UnverifiedReader(payload)

    monkeypatch.setattr(tgdb.TgdbStore, "resolver", lambda _store: BrokenResolver())
    arguments = ["tgdb", "blob", command, str(store), digest]
    if command == "get":
        arguments.extend(("-o", str(tmp_path / "copy.bin")))
    assert main(arguments) == 1
    assert message in capsys.readouterr().err


def test_tgdb_find_and_reindex_expose_tg7_queries(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The TG7 commands query tier facts and check or rebuild derived rows."""
    path = tmp_path / "corpus.tgdb"
    graph_path = tmp_path / "graph.json"
    tier = QualifiedName("urn:tgdb:test", "words")
    graph = Graph(
        (NamespaceDeclaration("test", "urn:tgdb:test"),),
        (Tier(TierDeclaration(tier, "Words"), (Item("one"), Item("two"))),),
        (),
    )
    graph_path.write_bytes(dump_bytes(graph))
    assert main(["tgdb", "init", str(path)]) == 0
    capsys.readouterr()
    assert main(["tgdb", "collection", "create", str(path), "corpus"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "tgdb",
                "add",
                str(path),
                "corpus",
                "sample",
                str(graph_path),
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert (
        main(
            [
                "tgdb",
                "find",
                str(path),
                "--tier",
                "urn:tgdb:test|words:2:2",
                "--json",
            ]
        )
        == 0
    )
    found = json.loads(capsys.readouterr().out)
    assert [(entry["collection"], entry["name"], entry["seq"]) for entry in found] == [
        ("corpus", "sample", 1)
    ]
    assert (
        main(["tgdb", "find", str(path), "--lacks-tier", "urn:x|missing", "--count"])
        == 0
    )
    assert capsys.readouterr().out == "1\n"

    assert main(["tgdb", "reindex", str(path)]) == 0
    assert capsys.readouterr().out == "checked 1 graphs and 1 tier\n"
    with sqlite3.connect(path / "catalog.sqlite3", autocommit=True) as connection:
        connection.execute("UPDATE graph_tiers SET item_count = 9")
    assert main(["tgdb", "reindex", str(path)]) == 1
    assert "graph_tiers row" in capsys.readouterr().err
    assert main(["tgdb", "reindex", str(path), "--rebuild"]) == 0
    assert capsys.readouterr().out == "rebuilt 1 graphs and 1 tier\n"

    revised = Graph(
        (NamespaceDeclaration("test", "urn:tgdb:test"),),
        (
            Tier(
                TierDeclaration(tier, "Words"),
                (Item("one"), Item("two"), Item("three")),
            ),
        ),
        (),
    )
    graph_path.write_bytes(dump_bytes(revised))
    assert (
        main(
            [
                "tgdb",
                "publish",
                str(path),
                "sample",
                str(graph_path),
                "--expected",
                "1",
                "--stage",
                "pass",
                "--iteration",
                "2",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["tgdb", "find", str(path), "--order-by", "name"]) == 0
    fields = capsys.readouterr().out.rstrip("\n").split("\t")
    assert fields[:5] == ["corpus", "0", fields[2], "sample", "2"]
    assert fields[6:] == ["pass", "2"]
    exact = fields[5]
    assert main(["tgdb", "retire", str(path), "sample"]) == 0
    capsys.readouterr()

    assert (
        main(
            [
                "tgdb",
                "find",
                str(path),
                "--collection",
                "corpus",
                "--name",
                "sample",
                "--fingerprint",
                f"exact:{exact}",
                "--tier",
                "urn:tgdb:test|words:3",
                "--lacks-tier",
                "urn:tgdb:test|missing",
                "--stage",
                "pass",
                "--iteration",
                "2",
                "--changed-since",
                "2",
                "--changed-view",
                "exact",
                "--where",
                '{"test":"not","arg":{"test":"or","args":[]}}',
                "--retired",
                "--count",
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {"count": 1}


def test_tgdb_index_commands_and_find_filters(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The TG8 shell declares, lists, queries, and drops typed item indexes."""
    store = tmp_path / "corpus.tgdb"
    graph_path = tmp_path / "graph.json"
    namespace = "urn:tgdb:index"
    tier = QualifiedName(namespace, "tokens")
    attribute = QualifiedName(namespace, "score")
    graph = Graph(
        (NamespaceDeclaration("index", namespace),),
        (
            Tier(
                TierDeclaration(tier, "Tokens"),
                (
                    Item(
                        "low",
                        (AttributeValue(attribute, XsdType.DECIMAL, "-2.0"),),
                    ),
                    Item(
                        "high",
                        (AttributeValue(attribute, XsdType.DECIMAL, "2.5"),),
                    ),
                ),
            ),
        ),
        (),
        attribute_declarations=(
            AttributeDeclaration(attribute, AttributeDomain.ITEM, XsdType.DECIMAL),
        ),
    )
    graph_path.write_bytes(dump_bytes(graph))
    assert main(["tgdb", "init", str(store)]) == 0
    capsys.readouterr()
    assert main(["tgdb", "collection", "create", str(store), "corpus"]) == 0
    capsys.readouterr()
    assert main(["tgdb", "add", str(store), "corpus", "sample", str(graph_path)]) == 0
    capsys.readouterr()

    assert main(["tgdb", "index", "list", str(store), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []
    assert (
        main(
            [
                "tgdb",
                "index",
                "declare",
                str(store),
                "score",
                "urn:tgdb:index|tokens",
                "urn:tgdb:index|score",
            ]
        )
        == 0
    )
    assert "declare index 'score'" in capsys.readouterr().out
    assert main(["tgdb", "index", "list", str(store)]) == 0
    assert capsys.readouterr().out.endswith(
        "0\tscore\turn:tgdb:index|tokens\turn:tgdb:index|score\theads\n"
    )
    assert main(["tgdb", "index", "list", str(store), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == [
        {
            "name": "score",
            "position": 0,
            "tier": "urn:tgdb:index|tokens",
            "attribute": "urn:tgdb:index|score",
            "history": False,
        }
    ]

    for filter_value in ("score=2.5", "score=:-2:3", "score"):
        assert (
            main(
                [
                    "tgdb",
                    "find",
                    str(store),
                    "--index",
                    filter_value,
                    "--count",
                ]
            )
            == 0
        )
        assert capsys.readouterr().out == "1\n"
    assert (
        main(
            [
                "tgdb",
                "find",
                str(store),
                "--lacks-index",
                "score",
                "--count",
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {"count": 0}

    assert main(["tgdb", "index", "drop", str(store), "score"]) == 0
    assert "drop index 'score'" in capsys.readouterr().out
    assert main(["tgdb", "find", str(store), "--index", "score"]) == 1
    assert "not declared" in capsys.readouterr().err
    assert main(["tgdb", "find", str(store), "--index", "score=2.5"]) == 1
    assert "not declared" in capsys.readouterr().err

    assert _tgdb_index_value(attribute, "text").value_type is XsdType.STRING
    assert _tgdb_index_value(attribute, True).value_type is XsdType.BOOLEAN
    assert _tgdb_index_value(attribute, 2).value_type is XsdType.INTEGER


@pytest.mark.parametrize(
    "option,value",
    (
        ("--fingerprint", "exact"),
        ("--fingerprint", f"near:{'a' * 64}"),
        ("--fingerprint", "exact:ABC"),
        ("--tier", "words"),
        ("--tier", "urn:test|words:2:1"),
        ("--tier", "|words"),
        ("--index", "=1"),
        ("--index", "score=:"),
        ("--index", "score=:2:1"),
        ("--index", "score=:not-a-number:"),
        ("--index", "score=:NaN:"),
        ("--index", "score=:1e99999999:"),
        ("--index", "score=NaN"),
        ("--index", "score=null"),
        ("--where", '{"test":"maybe"}'),
    ),
)
def test_tgdb_find_refuses_invalid_filter_syntax(option: str, value: str) -> None:
    """Typed find filters reject malformed command-line spellings as usage."""
    with pytest.raises(SystemExit) as caught:
        build_parser().parse_args(["tgdb", "find", "store", option, value])
    assert caught.value.code == 2


@pytest.mark.parametrize(
    "arguments,message",
    (
        (("--iteration", "2"), "--iteration requires --stage"),
        (("--changed-view", "exact"), "--changed-view requires --changed-since"),
    ),
)
def test_tgdb_find_dependent_options_require_their_filter(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    arguments: tuple[str, str],
    message: str,
) -> None:
    """A dependent option alone is refused before a query reaches the store."""
    path = tmp_path / "corpus.tgdb"
    assert main(["tgdb", "init", str(path)]) == 0
    capsys.readouterr()
    assert main(["tgdb", "find", str(path), *arguments]) == 1
    assert message in capsys.readouterr().err


def test_tgdb_refusal_is_a_status_one_staged_diagnostic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Store refusals preserve their stage through the CLI error channel."""
    missing = tmp_path / "missing"
    assert main(["tgdb", "info", str(missing)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    line, report_text = captured.err.split("\n", 1)
    assert line.startswith("tiergraph: tgdb: TgdbError: path ")
    assert line.endswith(" is not a tgdb store")
    report = json.loads(report_text)["refusal"]
    assert report == {
        "stage": "semantics",
        "rank": int(RefusalStage.SEMANTICS),
        "message": f"path {str(missing)!r} is not a tgdb store",
        "also": [],
    }


@pytest.mark.parametrize("value", ("-1", "many"))
def test_tgdb_init_refuses_invalid_threshold_syntax(value: str) -> None:
    """The parser rejects a negative or nonnumeric inline threshold as usage."""
    parser = build_parser()
    with pytest.raises(SystemExit) as caught:
        parser.parse_args(["tgdb", "init", "store", "--inline-threshold", value])
    assert caught.value.code == 2


def test_tgdb_collection_commands_preserve_declared_order_and_ids(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The collection CLI exposes every TG3 lifecycle operation and its commit."""
    path = tmp_path / "corpus.tgdb"
    uid = "11" * 16
    assert main(["tgdb", "init", str(path)]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "tgdb",
                "collection",
                "create",
                str(path),
                "last",
                "--uid",
                uid,
            ]
        )
        == 0
    )
    assert capsys.readouterr().out == (f"created collection 'last' {uid} in commit 1\n")
    assert (
        main(
            [
                "tgdb",
                "collection",
                "create",
                str(path),
                "first",
                "--position",
                "0",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert main(["tgdb", "collection", "list", str(path)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("0\t") and lines[0].endswith("\tfirst\tactive")
    assert lines[1] == f"1\t{uid}\tlast\tactive"
    assert main(["tgdb", "collection", "rename", str(path), "last", "renamed"]) == 0
    assert capsys.readouterr().out == "committed 3: rename\n"
    assert main(["tgdb", "collection", "move", str(path), "renamed", "0"]) == 0
    assert capsys.readouterr().out == "committed 4: move\n"
    assert main(["tgdb", "collection", "retire", str(path), "renamed"]) == 0
    assert capsys.readouterr().out == "committed 5: retire\n"

    assert main(["tgdb", "collection", "list", str(path), "--retired", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [entry["name"] for entry in listed] == ["renamed", "first"]
    assert listed[0]["retired"] is True
    assert main(["tgdb", "undo", str(path), "5"]) == 0
    assert capsys.readouterr().out == "committed 6: undo 5\n"
    assert main(["tgdb", "collection", "restore", str(path), "renamed"]) == 1
    captured = capsys.readouterr()
    assert "already active" in captured.err


def test_tgdb_instance_commands_are_scoped_and_json_serializable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The instance CLI lists and changes seeded TG3 catalog rows by stable id."""
    path = tmp_path / "corpus.tgdb"
    assert main(["tgdb", "init", str(path)]) == 0
    capsys.readouterr()
    assert main(["tgdb", "collection", "create", str(path), "collection"]) == 0
    capsys.readouterr()
    first = bytes.fromhex("22" * 16)
    second = bytes.fromhex("33" * 16)
    with sqlite3.connect(path / "catalog.sqlite3", autocommit=True) as connection:
        collection_id = connection.execute(
            "SELECT id FROM collections WHERE name = 'collection'"
        ).fetchone()[0]
        connection.executemany(
            "INSERT INTO instances(uid, collection_id, name, position, head_version, "
            "generation, retired_commit) VALUES (?, ?, ?, ?, NULL, 0, NULL)",
            (
                (first, collection_id, "first", 0),
                (second, collection_id, "second", 1),
            ),
        )

    assert main(["tgdb", "list", str(path), "--collection", "collection"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"collection\t0\t{first.hex()}\tfirst\tactive",
        f"collection\t1\t{second.hex()}\tsecond\tactive",
    ]
    assert main(["tgdb", "rename", str(path), first.hex(), "renamed"]) == 0
    assert capsys.readouterr().out == "committed 2: rename\n"
    assert main(["tgdb", "move", str(path), first.hex(), "1"]) == 0
    assert capsys.readouterr().out == "committed 3: move\n"
    assert main(["tgdb", "retire", str(path), first.hex()]) == 0
    assert capsys.readouterr().out == "committed 4: retire\n"
    assert main(["tgdb", "list", str(path), "--retired", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [entry["uid"] for entry in listed] == [second.hex(), first.hex()]
    assert listed[1]["retired"] is True
    assert main(["tgdb", "restore", str(path), first.hex()]) == 0
    assert capsys.readouterr().out == "committed 5: restore\n"


def test_tgdb_plain_lists_escape_names_and_hexadecimal_names_remain_addressable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Plain rows stay singular and an exact name takes precedence over id syntax."""
    path = tmp_path / "corpus.tgdb"
    assert main(["tgdb", "init", str(path)]) == 0
    capsys.readouterr()
    collection_name = "collection\tline\nnext"
    assert main(["tgdb", "collection", "create", str(path), collection_name]) == 0
    capsys.readouterr()
    hexadecimal_name = "abcdef0123456789abcdef0123456789"
    instance_uid = bytes.fromhex("44" * 16)
    with sqlite3.connect(path / "catalog.sqlite3", autocommit=True) as connection:
        collection_id = connection.execute(
            "SELECT id FROM collections WHERE name = ?", (collection_name,)
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO instances(uid, collection_id, name, position, head_version, "
            "generation, retired_commit) VALUES (?, ?, ?, 0, NULL, 0, NULL)",
            (instance_uid, collection_id, hexadecimal_name),
        )

    assert main(["tgdb", "collection", "list", str(path)]) == 0
    collection_row = capsys.readouterr().out
    assert collection_row.count("\n") == 1
    assert "collection\\tline\\nnext" in collection_row
    assert main(["tgdb", "list", str(path)]) == 0
    instance_row = capsys.readouterr().out
    assert instance_row.count("\n") == 1
    assert instance_row.startswith("collection\\tline\\nnext\t0\t")
    assert (
        main(
            [
                "tgdb",
                "rename",
                str(path),
                hexadecimal_name,
                "plain",
                "--collection",
                collection_name,
            ]
        )
        == 0
    )
    assert capsys.readouterr().out == "committed 2: rename\n"


def test_tgdb_add_publish_get_and_history(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The version CLI stores, retrieves, and lists complete graph documents."""
    path = tmp_path / "corpus.tgdb"
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    output = tmp_path / "output.json"
    first.write_bytes(dump_bytes(Graph((), (), ())))
    second.write_bytes(
        dump_bytes(Graph((NamespaceDeclaration("n", "urn:second"),), (), ()))
    )
    uid = "55" * 16
    assert main(["tgdb", "init", str(path)]) == 0
    capsys.readouterr()
    assert main(["tgdb", "collection", "create", str(path), "collection"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "tgdb",
                "add",
                str(path),
                "collection",
                "sample",
                str(first),
                "--uid",
                uid,
            ]
        )
        == 0
    )
    assert capsys.readouterr().out == (
        f"created instance 'sample' {uid} version 1 in commit 2\n"
    )
    assert main(["tgdb", "history", str(path), "sample"]) == 0
    columns = capsys.readouterr().out.rstrip("\n").split("\t")
    assert columns[0:2] == ["1", "2"]
    assert len(columns[2]) == len(columns[6]) == len(columns[7]) == 64
    assert columns[3:6] == ["-", "initial", "-"]
    assert columns[8:] == ["-", "-"]
    assert (
        main(
            [
                "tgdb",
                "get",
                str(path),
                uid,
                "--seq",
                "1",
                "-o",
                str(output),
            ]
        )
        == 0
    )
    assert capsys.readouterr().out == ""
    assert output.read_bytes() == first.read_bytes()

    catalog = path / "catalog.sqlite3"
    catalog_bytes = catalog.read_bytes()
    assert (
        main(
            [
                "tgdb",
                "get",
                str(path),
                uid,
                "-o",
                str(catalog),
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "output path must be outside the tgdb store" in captured.err
    assert catalog.read_bytes() == catalog_bytes

    assert (
        main(
            [
                "tgdb",
                "publish",
                str(path),
                "sample",
                str(second),
                "--expected",
                "1",
                "--annotations",
                '{"stage":"draft","iteration":1,"fields":{"pass":2}}',
                "--stage",
                "final",
                "--iteration",
                "3",
            ]
        )
        == 0
    )
    assert capsys.readouterr().out == "published 'sample' version 2 in commit 3\n"
    assert main(["tgdb", "history", str(path), "sample", "--json"]) == 0
    history = json.loads(capsys.readouterr().out)
    assert [version["seq"] for version in history] == [1, 2]
    assert history[1]["stage"] == "final"
    assert history[1]["iteration"] == 3

    assert (
        main(
            [
                "tgdb",
                "publish",
                str(path),
                "sample",
                str(second),
                "--expected",
                "2",
            ]
        )
        == 0
    )
    assert capsys.readouterr().out == "unchanged 'sample' at version 2\n"
    assert (
        main(
            [
                "tgdb",
                "publish",
                str(path),
                "sample",
                str(second),
                "--expected",
                "1",
            ]
        )
        == 1
    )
    assert "expected version 1 but head is version 2" in capsys.readouterr().err


def test_tgdb_patch_diff_apply_and_revert_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The commands exchange exact patches and append reverted documents."""
    path = tmp_path / "corpus.tgdb"
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    supplied_path = tmp_path / "supplied.jsonl"
    emitted_path = tmp_path / "emitted.jsonl"
    first = Graph((), (), ())
    second = Graph((NamespaceDeclaration("n", "urn:second"),), (), ())
    first_path.write_bytes(dump_bytes(first))
    second_path.write_bytes(dump_bytes(second))
    supplied_path.write_text(
        patch_dumps(diff(first, second, EquivalenceView.EXACT)), encoding="utf-8"
    )
    assert main(["tgdb", "init", str(path)]) == 0
    capsys.readouterr()
    assert main(["tgdb", "collection", "create", str(path), "collection"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "tgdb",
                "add",
                str(path),
                "collection",
                "sample",
                str(first_path),
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert main(["tgdb", "diff", str(path), "sample"]) == 1
    assert "head has no parent version" in capsys.readouterr().err

    assert (
        main(
            [
                "tgdb",
                "publish",
                str(path),
                "sample",
                str(second_path),
                "--expected",
                "1",
                "--patch",
                str(supplied_path),
            ]
        )
        == 0
    )
    assert capsys.readouterr().out == "published 'sample' version 2 in commit 3\n"
    assert (
        main(
            [
                "tgdb",
                "diff",
                str(path),
                "sample",
                "-o",
                str(emitted_path),
            ]
        )
        == 0
    )
    assert capsys.readouterr().out == ""
    assert patch_loads(emitted_path.read_bytes()).apply(first) == second

    catalog = path / "catalog.sqlite3"
    catalog_bytes = catalog.read_bytes()
    assert (
        main(
            [
                "tgdb",
                "diff",
                str(path),
                "sample",
                "-o",
                str(catalog),
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "output path must be outside the tgdb store" in captured.err
    assert catalog.read_bytes() == catalog_bytes

    assert (
        main(
            [
                "tgdb",
                "revert",
                str(path),
                "sample",
                "--to",
                "1",
                "--expected",
                "2",
            ]
        )
        == 0
    )
    assert capsys.readouterr().out == (
        "reverted 'sample' to stored version 1 as version 3 in commit 4\n"
    )
    assert (
        main(
            [
                "tgdb",
                "apply",
                str(path),
                "sample",
                str(supplied_path),
                "--expected",
                "3",
            ]
        )
        == 0
    )
    assert capsys.readouterr().out == "published 'sample' version 4 in commit 5\n"
    assert (
        main(
            [
                "tgdb",
                "diff",
                str(path),
                "sample",
                "4",
                "4",
                "-o",
                str(emitted_path),
            ]
        )
        == 0
    )
    assert capsys.readouterr().out == ""
    assert (
        main(
            [
                "tgdb",
                "apply",
                str(path),
                "sample",
                str(emitted_path),
                "--expected",
                "4",
            ]
        )
        == 0
    )
    assert capsys.readouterr().out == "unchanged 'sample' at version 4\n"
    assert (
        main(
            [
                "tgdb",
                "revert",
                str(path),
                "sample",
                "--to",
                "2",
                "--expected",
                "4",
            ]
        )
        == 0
    )
    assert capsys.readouterr().out == "unchanged 'sample' at version 4\n"
    assert main(["tgdb", "history", str(path), "sample", "--json"]) == 0
    history = json.loads(capsys.readouterr().out)
    assert [version["transition"] for version in history] == [
        "initial",
        "patch",
        "patch",
        "patch",
    ]


def test_tgdb_batch_publishes_all_heads_in_one_commit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The batch manifest publishes its declared entries atomically and in order."""
    store = tmp_path / "corpus.tgdb"
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_bytes(dump_bytes(Graph((), (), ())))
    second.write_bytes(
        dump_bytes(Graph((NamespaceDeclaration("n", "urn:second"),), (), ()))
    )
    (tmp_path / "change.jsonl").write_text(
        patch_dumps(
            diff(
                Graph((), (), ()),
                Graph((NamespaceDeclaration("n", "urn:second"),), (), ()),
                EquivalenceView.EXACT,
            )
        ),
        encoding="utf-8",
    )
    assert main(["tgdb", "init", str(store)]) == 0
    capsys.readouterr()
    assert main(["tgdb", "collection", "create", str(store), "collection"]) == 0
    capsys.readouterr()
    for name in ("left", "right"):
        assert (
            main(
                [
                    "tgdb",
                    "add",
                    str(store),
                    "collection",
                    name,
                    str(first),
                ]
            )
            == 0
        )
        capsys.readouterr()
    manifest = tmp_path / "pass.json"
    manifest.write_text(
        json.dumps(
            {
                "annotations": {"stage": "reconcile", "iteration": 2},
                "publishes": [
                    {
                        "instance": "left",
                        "graph": "second.json",
                        "expected": 1,
                        "annotations": {"stage": "left"},
                    },
                    {
                        "instance": "right",
                        "collection": "collection",
                        "graph": "second.json",
                        "expected": 1,
                        "patch": "change.jsonl",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    assert main(["tgdb", "batch", str(store), str(manifest)]) == 0
    assert capsys.readouterr().out == "committed 4: 2 published, 0 unchanged\n"
    assert main(["tgdb", "history", str(store), "left", "--json"]) == 0
    left = json.loads(capsys.readouterr().out)
    assert left[-1]["commit_seq"] == 4
    assert left[-1]["stage"] == "left"
    assert main(["tgdb", "history", str(store), "right", "--json"]) == 0
    right = json.loads(capsys.readouterr().out)
    assert right[-1]["commit_seq"] == 4

    assert main(["tgdb", "batch", str(store), str(manifest)]) == 1
    captured = capsys.readouterr()
    assert "expected version 1 but head is version 2" in captured.err
    assert [entry["seq"] for entry in right] == [1, 2]

    unchanged = tmp_path / "unchanged.json"
    unchanged.write_text(
        json.dumps(
            {
                "publishes": [
                    {"instance": name, "graph": "second.json", "expected": 2}
                    for name in ("left", "right")
                ]
            }
        ),
        encoding="utf-8",
    )
    assert main(["tgdb", "batch", str(store), str(unchanged)]) == 0
    assert capsys.readouterr().out == "unchanged: 2 instances\n"

    absolute = tmp_path / "absolute.json"
    absolute.write_text(
        json.dumps(
            {
                "publishes": [
                    {
                        "instance": "left",
                        "graph": str(second.resolve()),
                        "expected": 2,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert main(["tgdb", "batch", str(store), str(absolute)]) == 1
    assert "graph path must be relative" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("document", "message"),
    (
        ("[]", "must be a JSON object"),
        ('{"unknown": 1}', "unknown batch manifest fields"),
        ('{"annotations": []}', "batch annotations must be"),
        ('{"publishes": []}', "must be a nonempty JSON array"),
        ('{"publishes": [1]}', "publish 0 must be a JSON object"),
        (
            '{"publishes": [{"unknown": 1}]}',
            "unknown batch publish 0 fields",
        ),
        (
            '{"publishes": [{"instance": "", "graph": "g", "expected": 1}]}',
            "instance must be a nonempty string",
        ),
        (
            '{"publishes": [{"instance": "x", "graph": 1, "expected": 1}]}',
            "graph must be a nonempty string",
        ),
        (
            '{"publishes": [{"instance": "x", "graph": "g", "expected": true}]}',
            "expected must be a positive integer",
        ),
        (
            '{"publishes": [{"instance": "x", "graph": "g", "expected": 1, '
            '"collection": ""}]}',
            "collection must be a nonempty string",
        ),
        (
            '{"publishes": [{"instance": "x", "graph": "g", "expected": 1, '
            '"annotations": []}]}',
            "annotations must be a JSON object",
        ),
    ),
)
def test_tgdb_batch_manifest_refusals_are_status_one(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    document: str,
    message: str,
) -> None:
    """Malformed batch data is a checked input refusal, not a partial write."""
    store = tmp_path / "store"
    assert main(["tgdb", "init", str(store)]) == 0
    capsys.readouterr()
    manifest = tmp_path / "pass.json"
    manifest.write_text(document, encoding="utf-8")
    assert main(["tgdb", "batch", str(store), str(manifest)]) == 1
    assert message in capsys.readouterr().err


def test_tgdb_batch_manifest_refuses_invalid_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Invalid JSON in a batch manifest names the manifest syntax."""
    store = tmp_path / "store"
    assert main(["tgdb", "init", str(store)]) == 0
    capsys.readouterr()
    manifest = tmp_path / "pass.json"
    manifest.write_text("{", encoding="utf-8")
    assert main(["tgdb", "batch", str(store), str(manifest)]) == 1
    assert "batch manifest is not valid JSON" in capsys.readouterr().err


@pytest.mark.parametrize(
    "arguments",
    (
        ["tgdb", "publish", "store", "item", "graph.json", "--expected", "0"],
        ["tgdb", "get", "store", "item", "--seq", "0"],
        [
            "tgdb",
            "publish",
            "store",
            "item",
            "graph.json",
            "--expected",
            "1",
            "--annotations",
            "[]",
        ],
        [
            "tgdb",
            "publish",
            "store",
            "item",
            "graph.json",
            "--expected",
            "1",
            "--annotations",
            '{"unknown":1}',
        ],
        [
            "tgdb",
            "publish",
            "store",
            "item",
            "graph.json",
            "--expected",
            "1",
            "--annotations",
            '{"iteration":true}',
        ],
        [
            "tgdb",
            "publish",
            "store",
            "item",
            "graph.json",
            "--expected",
            "1",
            "--annotations",
            "{",
        ],
    ),
)
def test_tgdb_version_syntax_refuses_as_usage(arguments: list[str]) -> None:
    """Version sequences and annotations are checked by argparse."""
    with pytest.raises(SystemExit) as caught:
        build_parser().parse_args(arguments)
    assert caught.value.code == 2


@pytest.mark.parametrize(
    "arguments",
    (
        ["tgdb", "collection", "create", "store", "name", "--uid", "ABC"],
        ["tgdb", "collection", "move", "store", "name", "-1"],
        ["tgdb", "collection", "move", "store", "name", "many"],
        ["tgdb", "undo", "store", "0"],
        ["tgdb", "undo", "store", "latest"],
        ["tgdb", "revert", "store", "item", "--to", "0", "--expected", "1"],
        ["tgdb", "apply", "store", "item", "patch", "--expected", "0"],
        ["tgdb", "blob", "get", "store", "ABC", "-o", "payload.bin"],
    ),
)
def test_tgdb_catalog_syntax_refuses_as_usage(arguments: list[str]) -> None:
    """Catalog ids, positions, and commit sequences are checked by argparse."""
    with pytest.raises(SystemExit) as caught:
        build_parser().parse_args(arguments)
    assert caught.value.code == 2


def test_tgdb_bundle_and_collection_commands_round_trip(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """TG10 CLI commands move one graph or an ordered collection."""
    source = tmp_path / "source.tgdb"
    target = tmp_path / "target.tgdb"
    collection_target = tmp_path / "collection-target.tgdb"
    graph_path = tmp_path / "graph.json"
    payload_path = tmp_path / "payload.bin"
    bundle_path = tmp_path / "sample.tgb"
    linked_bundle_path = tmp_path / "sample-linked.tgb"
    export_path = tmp_path / "collection-export"
    payload = b"bundle payload"
    graph_path.write_bytes(dump_bytes(_cli_blob_graph(payload)))
    payload_path.write_bytes(payload)

    assert main(["tgdb", "init", str(source)]) == 0
    assert main(["tgdb", "collection", "create", str(source), "corpus"]) == 0
    assert main(["tgdb", "add", str(source), "corpus", "sample", str(graph_path)]) == 0
    assert main(["tgdb", "blob", "put", str(source), str(payload_path)]) == 0
    capsys.readouterr()
    assert main(["tgdb", "export", str(source), "sample", "-o", str(bundle_path)]) == 0
    assert "exported 'sample'" in capsys.readouterr().out

    assert main(["tgdb", "init", str(target)]) == 0
    assert main(["tgdb", "collection", "create", str(target), "corpus"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "tgdb",
                "import",
                str(target),
                str(bundle_path),
                "--collection",
                "corpus",
                "--name",
                "sample",
            ]
        )
        == 0
    )
    assert "imported 'sample'" in capsys.readouterr().out
    assert (
        main(
            [
                "tgdb",
                "export",
                str(source),
                "sample",
                "--link",
                hashlib.sha256(payload).hexdigest(),
                "-o",
                str(linked_bundle_path),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        main(
            [
                "tgdb",
                "import",
                str(target),
                str(linked_bundle_path),
                "--collection",
                "corpus",
                "--name",
                "sample",
                "--on-conflict",
                "new-version",
            ]
        )
        == 0
    )
    assert "unchanged 'sample'" in capsys.readouterr().out

    assert (
        main(
            [
                "tgdb",
                "collection",
                "export",
                str(source),
                "corpus",
                "-o",
                str(export_path),
            ]
        )
        == 0
    )
    assert "exported collection 'corpus'" in capsys.readouterr().out
    assert main(["tgdb", "init", str(collection_target)]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "tgdb",
                "collection",
                "import",
                str(collection_target),
                str(export_path),
            ]
        )
        == 0
    )
    assert "imported 1 instances" in capsys.readouterr().out

    with tgdb.TgdbStore.open(target) as imported_store:
        assert imported_store.get("sample", collection="corpus").load() == (
            _cli_blob_graph(payload)
        )
    with tgdb.TgdbStore.open(collection_target) as imported_store:
        assert [item.name for item in imported_store.instances("corpus")] == ["sample"]


def test_tgdb_retention_pin_and_gc_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """TG13 CLI maintenance is dry-run by default and explicit when destructive."""
    store = tmp_path / "corpus.tgdb"
    payload = tmp_path / "payload.bin"
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    payload.write_bytes(b"pending")
    digest = hashlib.sha256(b"pending").hexdigest()
    first.write_bytes(dump_bytes(Graph((), (), ())))
    second.write_bytes(
        dump_bytes(Graph((NamespaceDeclaration("n", "urn:second"),), (), ()))
    )
    assert main(["tgdb", "init", str(store), "--inline-threshold", "0"]) == 0
    capsys.readouterr()
    assert main(["tgdb", "blob", "put", str(store), str(payload)]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "tgdb",
                "blob",
                "pin",
                str(store),
                digest,
                "--reason",
                "pending-import",
            ]
        )
        == 0
    )
    assert f"pin {digest}" in capsys.readouterr().out
    with tgdb.TgdbStore.open(store) as opened:
        assert opened.pins()[0].reason == "pending-import"
    assert main(["tgdb", "blob", "unpin", str(store), digest]) == 0
    assert f"unpin {digest}" in capsys.readouterr().out

    assert main(["tgdb", "collection", "create", str(store), "corpus"]) == 0
    capsys.readouterr()
    assert main(["tgdb", "add", str(store), "corpus", "sample", str(first)]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "tgdb",
                "publish",
                str(store),
                "sample",
                str(second),
                "--expected",
                "1",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["tgdb", "prune", str(store), "sample", "--keep", "1"]) == 0
    assert "dry run: 1 versions selected" in capsys.readouterr().out
    assert (
        main(
            [
                "tgdb",
                "prune",
                str(store),
                "sample",
                "--keep",
                "1",
                "--delete",
            ]
        )
        == 0
    )
    assert "deleted 1 versions in commit" in capsys.readouterr().out
    assert (
        main(
            [
                "tgdb",
                "publish",
                str(store),
                "sample",
                str(first),
                "--expected",
                "2",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        main(
            [
                "tgdb",
                "prune",
                str(store),
                "sample",
                "--before-commit",
                "999",
                "--delete",
                "--json",
            ]
        )
        == 0
    )
    pruned = json.loads(capsys.readouterr().out)
    assert pruned["deleted"] is True
    assert pruned["version_count"] == 1

    assert main(["tgdb", "retire", str(store), "sample"]) == 0
    capsys.readouterr()
    assert main(["tgdb", "purge-retired", str(store)]) == 0
    assert "dry run: 1 retired instances selected" in capsys.readouterr().out
    assert main(["tgdb", "purge-retired", str(store), "--delete"]) == 0
    assert "deleted 1 retired instances in commit" in capsys.readouterr().out
    assert main(["tgdb", "add", str(store), "corpus", "other", str(first)]) == 0
    capsys.readouterr()
    assert main(["tgdb", "retire", str(store), "other"]) == 0
    capsys.readouterr()
    assert main(["tgdb", "purge-retired", str(store), "--delete", "--json"]) == 0
    purged = json.loads(capsys.readouterr().out)
    assert purged["deleted"] is True
    assert purged["instance_count"] == 1

    assert main(["tgdb", "gc", str(store), "--json"]) == 0
    garbage = json.loads(capsys.readouterr().out)
    assert garbage["deleted"] is False
    assert garbage["unreachable_objects"] >= 3
    assert garbage["unreachable_file_bytes"] > 0
    assert main(["tgdb", "gc", str(store), "--delete"]) == 0
    collected = capsys.readouterr().out
    assert collected.startswith("deleted:")
    assert "unreachable inline objects" in collected
    assert "unreachable file objects" in collected


def test_tgdb_nested_help_is_complete(capsys: pytest.CaptureFixture[str]) -> None:
    """Every collection command supplies descriptions and option help."""
    parser = build_parser()
    with pytest.raises(SystemExit) as caught:
        parser.parse_args(["tgdb", "collection", "create", "--help"])
    assert caught.value.code == 0
    help_text = capsys.readouterr().out
    assert "Create a named collection" in help_text
    assert "--position N" in help_text
    assert "--uid HEX" in help_text
    with pytest.raises(SystemExit) as caught:
        parser.parse_args(["tgdb", "publish", "--help"])
    assert caught.value.code == 0
    help_text = capsys.readouterr().out
    assert "Byte-identical documents" in help_text
    assert "--expected SEQ" in help_text
    assert "--annotations JSON" in help_text
    assert "--patch PATCH" in help_text
    with pytest.raises(SystemExit) as caught:
        parser.parse_args(["tgdb", "batch", "--help"])
    assert caught.value.code == 0
    help_text = capsys.readouterr().out
    assert "one atomic commit" in help_text
    assert "Manifest shape:" in help_text
    for command in ("apply", "diff", "revert"):
        with pytest.raises(SystemExit) as caught:
            parser.parse_args(["tgdb", command, "--help"])
        assert caught.value.code == 0
        assert "Exit codes:" in capsys.readouterr().out
    with pytest.raises(SystemExit) as caught:
        parser.parse_args(["tgdb", "index", "declare", "--help"])
    assert caught.value.code == 0
    help_text = capsys.readouterr().out
    assert "Build an item attribute index" in help_text
    assert "--history" in help_text
    assert "Exit codes:" in help_text
    with pytest.raises(SystemExit) as caught:
        parser.parse_args(["tgdb", "blob", "put", "--help"])
    assert caught.value.code == 0
    help_text = capsys.readouterr().out
    assert "Stream one file into the object pool" in help_text
    assert "Exit codes:" in help_text
    with pytest.raises(SystemExit) as caught:
        parser.parse_args(["tgdb", "blob", "pin", "--help"])
    assert caught.value.code == 0
    help_text = capsys.readouterr().out
    assert "--reason TEXT" in help_text
    assert "Exit codes:" in help_text
    for command, option in (
        ("prune", "--before-commit COMMIT"),
        ("purge-retired", "--delete"),
        ("gc", "--delete"),
        ("backup", "DEST"),
        ("migrate", "--json"),
        ("measure-corpus", "--items-per-instance N"),
    ):
        with pytest.raises(SystemExit) as caught:
            parser.parse_args(["tgdb", command, "--help"])
        assert caught.value.code == 0
        help_text = capsys.readouterr().out
        assert option in help_text
        assert "Exit codes:" in help_text
    with pytest.raises(SystemExit) as caught:
        parser.parse_args(["tgdb", "repack", "--help"])
    assert caught.value.code == 0
    help_text = capsys.readouterr().out
    assert "--threshold SIZE" in help_text
    assert "Exit codes:" in help_text
    with pytest.raises(SystemExit) as caught:
        parser.parse_args(["tgdb", "inspect", "--help"])
    assert caught.value.code == 0
    help_text = capsys.readouterr().out
    assert "No inspector runs during any other operation" in help_text
    assert "--publish" in help_text
    assert "Exit codes:" in help_text
    with pytest.raises(SystemExit) as caught:
        parser.parse_args(["tgdb", "import", "--help"])
    assert caught.value.code == 0
    help_text = capsys.readouterr().out
    assert "--allow-missing" in help_text
    assert "--on-conflict {refuse,new-version}" in help_text
    assert "Exit codes:" in help_text
    with pytest.raises(SystemExit) as caught:
        parser.parse_args(["tgdb", "export", "--help"])
    assert caught.value.code == 0
    help_text = capsys.readouterr().out
    assert "--link SHA256" in help_text
    assert "Exit codes:" in help_text
    for command in ("export", "import"):
        with pytest.raises(SystemExit) as caught:
            parser.parse_args(["tgdb", "collection", command, "--help"])
        assert caught.value.code == 0
        assert "Exit codes:" in capsys.readouterr().out
