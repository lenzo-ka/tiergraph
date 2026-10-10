"""Exercise the tgdb command group as a thin lazy client of the store API."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tiergraph import (
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
    diff,
    dump_bytes,
    patch_dumps,
    patch_loads,
)
from tiergraph.cli import _tgdb_index_value, build_parser, main


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
    assert "schema_version: 6\n" in plain
    assert "inline_threshold: 1234\n" in plain
    assert plain.endswith("mode: ro\n")

    assert main(["tgdb", "info", str(path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report == {
        "fingerprint_domain": "tiergraph-equivalence/1",
        "index_version": 3,
        "inline_threshold": 1234,
        "layout_version": 1,
        "mode": "ro",
        "schema_version": 6,
        "store_uid": report["store_uid"],
    }
    assert len(bytes.fromhex(report["store_uid"])) == 16


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
    ),
)
def test_tgdb_catalog_syntax_refuses_as_usage(arguments: list[str]) -> None:
    """Catalog ids, positions, and commit sequences are checked by argparse."""
    with pytest.raises(SystemExit) as caught:
        build_parser().parse_args(arguments)
    assert caught.value.code == 2


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
