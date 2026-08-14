"""Refuse a public name that carries no docstring."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = (ROOT / "src" / "tiergraph", ROOT / "src" / "tiergraph_dot")


def undocumented(path: Path) -> list[str]:
    """Return one message per public name in the file lacking a docstring."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    missing: list[str] = []
    if ast.get_docstring(tree) is None:
        missing.append(f"{path.relative_to(ROOT)}: the module itself")
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        if node.name.startswith("_"):
            continue
        if ast.get_docstring(node) is None:
            missing.append(f"{path.relative_to(ROOT)}:{node.name}")
    return missing


def main() -> int:
    """Check the shipped package. Returns the process exit status."""
    missing = [
        name
        for package in PACKAGES
        for path in sorted(package.rglob("*.py"))
        for name in undocumented(path)
    ]
    if not missing:
        return 0
    print("public names must carry a docstring:", file=sys.stderr)
    for name in missing:
        print(f"  {name}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
