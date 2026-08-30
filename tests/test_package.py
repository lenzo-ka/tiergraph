"""The package exposes a version and ships a typing marker."""

from __future__ import annotations

import importlib
import subprocess
from importlib import resources
from pathlib import Path

import pytest

import tiergraph


def test_version_is_a_string() -> None:
    """__version__ is present and non-empty."""
    assert isinstance(tiergraph.__version__, str)
    assert tiergraph.__version__


def test_graph_validation_error_is_a_value_error() -> None:
    """Callers can identify graph-contract failures without breaking old handlers."""
    assert issubclass(tiergraph.GraphValidationError, ValueError)
    with pytest.raises(tiergraph.GraphValidationError, match="must not be empty"):
        tiergraph.QualifiedName("", "item")


def test_typing_marker_ships() -> None:
    """py.typed travels with the package."""
    assert resources.files("tiergraph").joinpath("py.typed").is_file()
    assert resources.files("tiergraph_dot").joinpath("py.typed").is_file()


def test_module_shim_imports() -> None:
    """The `python -m` shim imports without running."""
    import importlib

    assert importlib.import_module("tiergraph.__main__") is not None


def test_denylist_reaches_neither_distribution(tmp_path: Path) -> None:
    """Neither real distribution archive contains the repository-only denylist."""
    pytest.importorskip("hatchling")

    root = Path(__file__).resolve().parent.parent
    sdist_module = importlib.import_module("hatchling.builders.sdist")
    wheel_module = importlib.import_module("hatchling.builders.wheel")
    sdist = Path(
        next(sdist_module.SdistBuilder(str(root)).build(directory=str(tmp_path)))
    )
    wheel = Path(
        next(wheel_module.WheelBuilder(str(root)).build(directory=str(tmp_path)))
    )
    sdist_members = subprocess.run(
        ["tar", "-tf", sdist], check=True, capture_output=True, text=True
    ).stdout.splitlines()
    wheel_members = subprocess.run(
        ["unzip", "-Z1", wheel], check=True, capture_output=True, text=True
    ).stdout.splitlines()
    assert not any(Path(name).name == "denied-names.txt" for name in sdist_members)
    assert "denied-names.txt" not in wheel_members
    assert any(name.startswith("tiergraph/") for name in wheel_members)
    assert any(name.startswith("tiergraph_dot/") for name in wheel_members)
