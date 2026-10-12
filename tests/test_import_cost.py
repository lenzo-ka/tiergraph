"""Keep opt-in surfaces off the default package import path."""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

import tiergraph

DECLARED_LAZY_MODULES = (
    "tiergraph.blob",
    "tiergraph.build",
    "tiergraph.cli",
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
    "tiergraph.tgdb",
)
DOCUMENTED_SECONDARY_MODULES = tuple(
    json.loads(
        (Path(__file__).resolve().parent.parent / "docs" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )["secondary"]
)
SECONDARY_MODULES_TO_CHECK = tuple(
    sorted(set(DECLARED_LAZY_MODULES) | set(DOCUMENTED_SECONDARY_MODULES))
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
    assert set(DECLARED_LAZY_MODULES) == set(tiergraph._LAZY_EXPORTS) | {
        "tiergraph.build",
        "tiergraph.cli",
        "tiergraph.tgdb",
    }
    assert set(tiergraph._SUBMODULES) == (
        set(DECLARED_LAZY_MODULES) | set(DOCUMENTED_SECONDARY_MODULES)
    )


def test_declared_submodule_resolves_in_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A declared noncolliding submodule is cached on first attribute access."""
    build_module = tiergraph.build
    monkeypatch.delitem(vars(tiergraph), "build", raising=False)

    assert tiergraph.build is build_module
    assert vars(tiergraph)["build"] is build_module


def test_every_documented_and_lazy_submodule_resolves_in_a_fresh_process() -> None:
    """Package attributes resolve modules except where top-level functions collide."""
    script = f"""
import importlib
from types import ModuleType

import tiergraph

module_names = {SECONDARY_MODULES_TO_CHECK!r}
collisions = {{"tiergraph.diff", "tiergraph.distance"}}
for module_name in module_names:
    short_name = module_name.rpartition(".")[2]
    package_value = getattr(tiergraph, short_name)
    module = importlib.import_module(module_name)
    if module_name in collisions:
        assert package_value is getattr(module, short_name)
        assert not isinstance(package_value, ModuleType)
    else:
        assert package_value is module
"""
    subprocess.run([sys.executable, "-c", script], check=True)


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
