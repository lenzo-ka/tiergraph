"""The command line reports the version and exits cleanly."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from tiergraph import __version__
from tiergraph.cli import main


def test_version_is_json(capsys: pytest.CaptureFixture[str]) -> None:
    """--version emits JSON carrying the package version."""
    assert main(["--version"]) == 0
    assert json.loads(capsys.readouterr().out) == {"version": __version__}


def test_help_is_the_default(capsys: pytest.CaptureFixture[str]) -> None:
    """With no arguments the parser prints help and succeeds."""
    assert main([]) == 0
    assert "usage: tiergraph" in capsys.readouterr().out


def test_module_entry_point_runs() -> None:
    """`python -m tiergraph` executes and reports the version."""
    result = subprocess.run(
        [sys.executable, "-m", "tiergraph", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == {"version": __version__}
