"""Exercise the tgdb command group as a thin lazy client of the store API."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tiergraph import RefusalStage
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
    assert "schema_version: 1\n" in plain
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
        "schema_version": 1,
        "store_uid": report["store_uid"],
    }
    assert len(bytes.fromhex(report["store_uid"])) == 16


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
