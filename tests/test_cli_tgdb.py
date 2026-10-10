"""Exercise the tgdb command group as a thin lazy client of the store API."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tiergraph import (
    EquivalenceView,
    Graph,
    NamespaceDeclaration,
    RefusalStage,
    diff,
    dump_bytes,
    patch_dumps,
    patch_loads,
)
from tiergraph.cli import build_parser, main


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
    assert "schema_version: 4\n" in plain
    assert "inline_threshold: 1234\n" in plain
    assert plain.endswith("mode: ro\n")

    assert main(["tgdb", "info", str(path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report == {
        "fingerprint_domain": "tiergraph-equivalence/1",
        "index_version": 1,
        "inline_threshold": 1234,
        "layout_version": 1,
        "mode": "ro",
        "schema_version": 4,
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
    for command in ("apply", "diff", "revert"):
        with pytest.raises(SystemExit) as caught:
            parser.parse_args(["tgdb", command, "--help"])
        assert caught.value.code == 0
        assert "Exit codes:" in capsys.readouterr().out
