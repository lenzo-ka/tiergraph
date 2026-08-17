"""Refuse any tracked file carrying something local to a contributor.

Runs over the git index rather than the working tree: untracked scratch is
exactly where local material is allowed to live.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

FORBIDDEN: tuple[tuple[str, str], ...] = (
    (r"/Users/[A-Za-z0-9._-]+", "a macOS home-directory path"),
    (r"/home/[A-Za-z0-9._-]+", "a Linux home-directory path"),
    (r"\.ssh/", "a path into an SSH configuration directory"),
    (r"\bfile:///", "an absolute local file URL"),
    (
        r"(?i)generated (?:by|with) (?:an? )?(?:AI|LLM|ChatGPT|Codex)",
        "AI/tool attribution",
    ),
    (r"(?i)as an AI", "AI/tool attribution"),
    (r"(?i)generated (?:at|on) \d{4}-\d{2}-\d{2}", "a generated timestamp"),
)

# This file lists the patterns it forbids, so it necessarily contains them.
SELF = Path(__file__).name
ROOT = Path(__file__).resolve().parent.parent


def tracked_files() -> list[Path]:
    """Return every file in the git index."""
    listing = subprocess.run(
        ["git", "ls-files", "-z"], check=True, capture_output=True, text=True
    )
    return [Path(name) for name in listing.stdout.split("\0") if name]


def leaks(path: Path) -> list[str]:
    """Return one message per forbidden match in the file."""
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    return [
        f"{path}: {reason} ({match.group(0)!r})"
        for pattern, reason in FORBIDDEN
        for match in re.finditer(pattern, text)
    ]


def main() -> int:
    """Check every tracked file. Returns the process exit status."""
    found = [
        message
        for path in tracked_files()
        if path.name != SELF and path.is_file()
        for message in leaks(path)
    ]
    if not found:
        return 0
    print("tracked files must be publishable as they stand:", file=sys.stderr)
    for message in found:
        print(f"  {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
