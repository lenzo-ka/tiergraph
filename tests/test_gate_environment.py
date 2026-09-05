"""Prove the gate imports the checkout its makefile lives in.

This project's worktrees live under `.claude/worktrees/<name>/`, and a worktree
has no virtualenv of its own, so the gate is run there with `VENV` pointing at
another checkout's. That virtualenv carries an editable install pinned to the
checkout it was built from, so every gate step that does `import tiergraph`
read the other checkout's library while reporting on this one's diff. Measured
before the fix: with `FORMAT_VERSION` altered under `src/` in a worktree,
`make schema-check` exited 0. The change was never read, and the gate said so
in green.

The child environment is set here rather than inherited. A relative
`PYTHONPATH=src` exported by whoever launched pytest resolves against the
child's own working directory and lands on the synthetic checkout by accident,
which makes the fixed and stripped makefiles indistinguishable -- observed
while writing this, and the reason the workaround it stands for is not one.

A defect that can only be seen by running make is checked by running make.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from scripts import check_tracked_clean

MAKEFILE = check_tracked_clean.ROOT / "Makefile"
# `include` rather than an edit: MAKEFILE_LIST's last word while the included
# file is parsed is that file, so the makefile under test derives its own
# directory exactly as it does when invoked directly.
PROBE = """include Makefile

probe:
\t@$(VENV_PYTHON) -c 'import os, tiergraph; print(tiergraph.__file__); print(os.environ["PYTHONPATH"])'
"""


def _venv() -> Path:
    """Return the virtualenv directory holding the running interpreter."""
    return Path(sys.executable).resolve().parent.parent


def _probe(root: Path, makefile: str, inherited: str | None = None) -> list[str]:
    """Run the probe target against one makefile text in a synthetic checkout."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "Makefile").write_text(makefile, encoding="utf-8")
    (root / "probe.mk").write_text(PROBE, encoding="utf-8")
    package = root / "src" / "tiergraph"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text('"""Stand-in package."""\n', encoding="utf-8")
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    if inherited is not None:
        environment["PYTHONPATH"] = inherited
    result = subprocess.run(
        [
            "make",
            "--directory",
            str(root),
            "--file",
            "probe.mk",
            "--no-print-directory",
            "probe",
            f"VENV={_venv()}",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    return result.stdout.splitlines()


needs_make = pytest.mark.skipif(
    shutil.which("make") is None or not (_venv() / "bin" / "python").is_file(),
    reason="needs make and a virtualenv interpreter",
)


@needs_make
def test_the_makefile_puts_its_own_checkout_ahead_of_the_installed_library(
    tmp_path: Path,
) -> None:
    """REGRESSION: a gate step imports the makefile's own src, not the installed one.

    The stripped run is the discrimination rather than a separate test: without
    the two lines that derive the path, the same probe in the same directory
    reaches whatever library the virtualenv has installed. That is the failure
    the fix is for, kept where it can be read beside the fix.
    """
    text = MAKEFILE.read_text(encoding="utf-8")
    root = tmp_path / "fixed"
    assert _probe(root, text)[0].startswith(str(root.resolve()))

    stripped = "".join(
        line
        for line in text.splitlines(keepends=True)
        if not line.startswith(("MAKEFILE_DIR", "export PYTHONPATH"))
    )
    assert stripped != text
    bare = tmp_path / "without"
    # The stripped makefile exports nothing, so the probe's own report of
    # PYTHONPATH raises KeyError and the run produces no line at all. Either
    # that or an import from elsewhere is the defect; reaching this checkout is
    # what cannot happen.
    reported = _probe(bare, stripped)
    assert not any(line.startswith(str(bare.resolve())) for line in reported)


@needs_make
def test_an_inherited_python_path_is_kept_behind_the_checkout(tmp_path: Path) -> None:
    """An ambient PYTHONPATH still contributes; it just cannot displace src.

    Dropping it would be its own defect: a caller who added a directory for a
    reason of their own would find it silently gone. Winning the front of the
    path is the whole requirement.
    """
    extra = tmp_path / "extra"
    extra.mkdir()
    root = tmp_path / "inherited"
    imported, path = _probe(root, MAKEFILE.read_text(encoding="utf-8"), str(extra))
    assert imported.startswith(str(root.resolve()))
    assert path == f"{root.resolve()}/src:{extra}"


def test_every_non_file_target_is_phony() -> None:
    """A command target cannot become inert when a same-named file appears."""
    text = MAKEFILE.read_text(encoding="utf-8")
    phony_line = next(line for line in text.splitlines() if line.startswith(".PHONY:"))
    phony = set(phony_line.partition(":")[2].split())
    targets = {
        target
        for line in text.splitlines()
        if (match := re.match(r"^([^#\s][^:]*)\s*:(?!=)", line)) is not None
        for target in match.group(1).split()
    }
    # The lane defines file rules by a slash or dot; deriving them from .PHONY
    # would make the completeness assertion true even when an entry was missing.
    non_file_targets = {
        target for target in targets if "/" not in target and "." not in target
    }
    assert non_file_targets <= phony, sorted(non_file_targets - phony)
