"""Keep opt-in surfaces off the default package import path."""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from types import ModuleType

import pytest

import tiergraph

DECLARED_LAZY_MODULES = (
    "tiergraph.blob",
    "tiergraph.container_edit",
    "tiergraph.diff",
    "tiergraph.distance",
    "tiergraph.edit",
    "tiergraph.equivalence",
    "tiergraph.patch",
    "tiergraph.pathoutput",
    "tiergraph.profile",
    "tiergraph.reconcile",
    "tiergraph.replacement",
    "tiergraph.selected",
    "tiergraph.textgrid",
)


def test_default_import_does_not_load_opt_in_surfaces() -> None:
    """A fresh package import and directory listing leave lazy modules unloaded."""
    script = f"""
import json
import sys

import tiergraph

lazy_modules = {DECLARED_LAZY_MODULES!r}
loaded_before_dir = sorted(set(lazy_modules) & set(sys.modules))
public_names = dir(tiergraph)
parsed_index = tiergraph.parse_path_index_segment("12")
loaded_after_dir = sorted(set(lazy_modules) & set(sys.modules))
print(json.dumps({{
    "loaded_before_dir": loaded_before_dir,
    "loaded_after_dir": loaded_after_dir,
    "missing_from_dir": sorted(set(tiergraph.__all__) - set(public_names)),
    "parsed_index": parsed_index,
}}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)
    assert result == {
        "loaded_before_dir": [],
        "loaded_after_dir": [],
        "missing_from_dir": [],
        "parsed_index": 12,
    }


def test_lazy_module_gate_matches_the_declaration() -> None:
    """The fresh-process gate covers every module in the lazy export table."""
    assert set(DECLARED_LAZY_MODULES) == set(tiergraph._LAZY_EXPORTS)


def test_every_public_name_resolves() -> None:
    """Lazy loading preserves the complete top-level runtime surface."""
    assert set(tiergraph.__all__) <= set(dir(tiergraph))
    assert {name for name in tiergraph.__all__ if not hasattr(tiergraph, name)} == set()


def test_loading_one_surface_publishes_colliding_function_names() -> None:
    """Lazy dependency imports cannot leave a public function as its module."""
    script = """
import importlib

import tiergraph

diff_module = importlib.import_module("tiergraph.diff")
assert tiergraph.diff is diff_module.diff
_ = tiergraph.CostTable
distance_module = importlib.import_module("tiergraph.distance")
assert tiergraph.distance is distance_module.distance
"""
    subprocess.run([sys.executable, "-c", script], check=True)


def test_partially_initialized_module_does_not_block_lazy_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Republishing skips exports absent from a partially initialized module."""
    published_diff = tiergraph.diff
    colliding_name = "diff"
    setattr(tiergraph, colliding_name, ModuleType("tiergraph.diff"))
    assert tiergraph.diff is published_diff

    partial_textgrid = ModuleType("tiergraph.textgrid")
    monkeypatch.setitem(sys.modules, "tiergraph.textgrid", partial_textgrid)

    patch_module = importlib.import_module("tiergraph.patch")
    assert tiergraph.__getattr__("Patch") is patch_module.Patch


def test_unknown_public_name_is_absent() -> None:
    """Lazy lookup retains the ordinary module attribute refusal."""
    with pytest.raises(AttributeError, match="has no attribute 'not_public'"):
        _ = tiergraph.not_public
