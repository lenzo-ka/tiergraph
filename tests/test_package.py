"""The package exposes a version and ships a typing marker."""

from __future__ import annotations

from importlib import resources

import tiergraph


def test_version_is_a_string() -> None:
    """__version__ is present and non-empty."""
    assert isinstance(tiergraph.__version__, str)
    assert tiergraph.__version__


def test_typing_marker_ships() -> None:
    """py.typed travels with the package."""
    assert resources.files("tiergraph").joinpath("py.typed").is_file()
    assert resources.files("tiergraph_dot").joinpath("py.typed").is_file()


def test_module_shim_imports() -> None:
    """The `python -m` shim imports without running."""
    import importlib

    assert importlib.import_module("tiergraph.__main__") is not None
