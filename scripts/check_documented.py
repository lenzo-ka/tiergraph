"""Refuse a public name that carries no docstring, or an empty one."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = (ROOT / "src" / "tiergraph", ROOT / "src" / "tiergraph_dot")


def documents_nothing(
    node: ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
) -> bool:
    """Return whether a node's docstring leaves a reader with nothing.

    Testing `ast.get_docstring(node) is None` asks whether a string literal
    opens the body, not whether it says anything. A body of `""` yields `''`,
    which is not None, so `def f(x): ""` passed as documented -- the shape of
    the requirement satisfied and none of its substance.

    Whitespace-only is refused for the same reason rather than being tolerated
    as a near miss. `ast.get_docstring` cleans indentation, so a docstring of
    spaces or of a bare newline reduces to `''` too, and is indistinguishable
    here from the empty one; a rule that accepted those would be accepting the
    empty docstring under another spelling. What a reader gets from any of them
    is a blank, and the claim this gate makes is that every public name carries
    a sentence, not that it carries a string object.
    """
    docstring = ast.get_docstring(node)
    return docstring is None or not docstring.strip()


def undocumented(path: Path) -> list[str]:
    """Return one message per public name in the file lacking a docstring."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    missing: list[str] = []
    if documents_nothing(tree):
        missing.append(f"{path.relative_to(ROOT)}: the module itself")
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        if node.name.startswith("_"):
            continue
        if documents_nothing(node):
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
