"""Refuse the small set of changelog claims the repository can disprove.

Changelog entries accumulate: prose that was true when written can become false
after a later change.  This gate intentionally does not parse claims in general.
It recognizes only three closed lexical shapes:

* ``FORMAT_VERSION`` followed in the same entry by a quoted value;
* ``byte-identical`` naming the schema artifact, its stamp, or either literal
  tracked path; and
* ``the wire is untouched`` or ``the document format is unchanged``.

The first shape is checked only under ``[Unreleased]``.  The other two compare
the current tree with the newest release tag.  More claim kinds belong here only
when their wording and their tree observable can both be closed as narrowly.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from tiergraph import __version__
from tiergraph.wire import FORMAT_VERSION

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG_PATH = Path("CHANGELOG.md")
WIRE_PATH = Path("src/tiergraph/wire.py")
SCHEMA_PATH = Path("schema/tiergraph.schema.json")
STAMP_PATH = Path("schema/tiergraph.schema.sha256")

HEADING = re.compile(r"^## \[([^]]+)](?:\s+-.*)?$")
ENTRY = re.compile(r"^- ")
VERSION_CLAIM = re.compile(
    r"`FORMAT_VERSION`[^\n.]*?(?:stays|is|remains)\s+`?[\"']([^\"']+)[\"']`?",
    re.IGNORECASE,
)
UNCHANGED_WIRE = re.compile(
    r"\b(?:the wire is untouched|the document format is unchanged)\b",
    re.IGNORECASE,
)
BYTE_IDENTICAL = re.compile(r"\bbyte-identical\b", re.IGNORECASE)
WIRE_VALUE = re.compile(r'^FORMAT_VERSION\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)


@dataclass(frozen=True, slots=True)
class Entry:
    """One changelog list entry and the release section that contains it."""

    section: str
    line: int
    text: str


def entries(text: str) -> list[Entry]:
    """Return top-level list entries under release headings."""
    found: list[Entry] = []
    section: str | None = None
    start: int | None = None
    lines: list[str] = []

    def finish() -> None:
        if section is not None and start is not None:
            found.append(Entry(section, start, "\n".join(lines)))

    for number, line in enumerate(text.splitlines(), start=1):
        heading = HEADING.match(line)
        if heading is not None:
            finish()
            start = None
            lines = []
            section = heading.group(1)
        elif section is not None and ENTRY.match(line):
            finish()
            start = number
            lines = [line]
        elif start is not None:
            lines.append(line)
    finish()
    return found


def git_output(arguments: Sequence[str], cwd: Path) -> bytes | None:
    """Return one git command's bytes, or None when git refuses."""
    try:
        result = subprocess.run(
            ["git", *arguments], cwd=cwd, check=True, capture_output=True
        )
    except subprocess.CalledProcessError:
        return None
    return result.stdout


def newest_release_tag(cwd: Path) -> str | None:
    """Return the highest version-shaped release tag known to git."""
    listing = git_output(["tag", "--list", "v[0-9]*", "--sort=-version:refname"], cwd)
    if listing is None:
        return None
    return next((line for line in listing.decode().splitlines() if line), None)


def released_bytes(tag: str, path: Path, cwd: Path) -> bytes | None:
    """Return a tracked artifact as stored by a release tag."""
    return git_output(["show", f"{tag}:{path.as_posix()}"], cwd)


def released_format(tag: str, cwd: Path) -> str | None:
    """Return the format value declared by a release tag."""
    source = released_bytes(tag, WIRE_PATH, cwd)
    if source is None:
        return None
    match = WIRE_VALUE.search(source.decode())
    return None if match is None else match.group(1)


def named_artifacts(text: str) -> tuple[Path, ...]:
    """Return artifacts named by the closed byte-identity vocabulary."""
    lowered = text.lower()
    named: list[Path] = []
    if "schema artifact" in lowered or SCHEMA_PATH.as_posix() in text:
        named.append(SCHEMA_PATH)
    if "stamp" in lowered or STAMP_PATH.as_posix() in text:
        named.append(STAMP_PATH)
    return tuple(named)


def findings(
    text: str, cwd: Path = ROOT, current_format: str = FORMAT_VERSION
) -> list[str]:
    """Return false closed-shape claims, one diagnostic per changelog entry."""
    tag = newest_release_tag(cwd)
    if tag is None:
        return ["release tags are unavailable; no changelog baseline was checked"]
    old_format = released_format(tag, cwd)
    if old_format is None:
        return [
            f"{tag} does not contain a readable {WIRE_PATH.as_posix()} format value"
        ]

    refused: list[str] = []
    for entry in entries(text):
        if entry.section not in {"Unreleased", __version__}:
            continue
        reasons: list[str] = []
        if entry.section == "Unreleased":
            claim = VERSION_CLAIM.search(entry.text)
            if claim is not None and claim.group(1) != current_format:
                reasons.append(
                    f"claims FORMAT_VERSION {claim.group(1)!r}; the tree declares {current_format!r}"
                )

        if old_format != current_format and UNCHANGED_WIRE.search(entry.text):
            reasons.append(
                f"claims the wire did not move; {tag} used {old_format!r} and the tree uses {current_format!r}"
            )

        if BYTE_IDENTICAL.search(entry.text):
            changed = [
                path.as_posix()
                for path in named_artifacts(entry.text)
                if released_bytes(tag, path, cwd) != (cwd / path).read_bytes()
            ]
            if changed:
                reasons.append(
                    f"claims byte identity with {tag}; changed: {', '.join(changed)}"
                )

        if reasons:
            refused.append(
                f"{CHANGELOG_PATH.as_posix()}:{entry.line} [{entry.section}]: "
                + "; ".join(reasons)
            )
    return refused


def main(cwd: Path = ROOT, changelog: str | None = None) -> int:
    """Check the changelog and print actionable refusals."""
    text = (
        (cwd / CHANGELOG_PATH).read_text(encoding="utf-8")
        if changelog is None
        else changelog
    )
    refused = findings(text, cwd)
    if refused:
        print("false changelog claims:", file=sys.stderr)
        for finding in refused:
            print(f"  {finding}", file=sys.stderr)
        return 1
    print("changelog claims match the released artifacts and current format")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
