"""Refuse the small set of changelog claims the repository can disprove.

Changelog entries accumulate: prose that was true when written can become false
after a later change.  This gate intentionally does not parse claims in general.
It recognizes only three closed lexical shapes:

* ``FORMAT_VERSION`` followed in the same entry by a quoted value;
* ``byte-identical`` naming the schema artifact, its stamp, or either literal
  tracked path -- and refused outright when it names none of them, because a
  phrase that matches and then resolves to nothing is checked by no one while
  reading as checked; and
* ``the`` (``wire`` | ``wire format`` | ``document format``) ``is``
  (``unchanged`` | ``untouched``) -- one closed cross product, because all six
  spellings name the same tree observable, the released ``FORMAT_VERSION``
  against the current one.

The first shape is checked only under ``[Unreleased]``.  The other two compare
the current tree with the newest release tag.  More claim kinds belong here only
when their wording and their tree observable can both be closed as narrowly.

Matching runs over an entry with its whitespace flattened.  A changelog entry is
wrapped prose, so a claim spelled across a line break is the same claim, and a
matcher that reads a raw line decides its own scope by where an editor happened
to wrap -- which nothing in this repository controls or reviews.

Two bounds stand, and both are the closed vocabulary rather than defects in it.
A stability claim spelled any other way passes unread, because nothing looked at
it; only a spelling that names the released-versus-current format comparison
belongs in the list, and never a spelling added so that some sentence already
written goes quiet.  And a recognized phrase is matched wherever it appears, so
an entry quoting one reads as an entry making one -- use and mention are a
distinction no lexical matcher draws, and an entry about this gate has to
describe the vocabulary instead of listing it.
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
    r"\bthe (?:wire|wire format|document format) is (?:unchanged|untouched)\b",
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


def flattened(text: str) -> str:
    """Return one entry's prose with every run of whitespace as a single space.

    The entry keeps its raw text so diagnostics can name the line it starts on;
    only the matching sees this. Without it a claim reads as absent whenever the
    wrap falls inside the phrase, so the check's reach is decided by an editor's
    column width rather than by the vocabulary this file writes down.
    """
    return " ".join(text.split())


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
        prose = flattened(entry.text)
        reasons: list[str] = []
        if entry.section == "Unreleased":
            claim = VERSION_CLAIM.search(prose)
            if claim is not None and claim.group(1) != current_format:
                reasons.append(
                    f"claims FORMAT_VERSION {claim.group(1)!r}; the tree declares {current_format!r}"
                )

        if old_format != current_format and UNCHANGED_WIRE.search(prose):
            reasons.append(
                f"claims the wire did not move; {tag} used {old_format!r} and the tree uses {current_format!r}"
            )

        if BYTE_IDENTICAL.search(prose):
            named = named_artifacts(prose)
            if not named:
                # A claim that matches the phrase and then resolves to no
                # artifact compared nothing, and the old code reported that as
                # agreement: the loop below ran zero times and the entry passed.
                # `- This release is byte-identical to the last one.` is the
                # whole counter-example. The vocabulary this gate can check is
                # closed on purpose, so a byte-identity claim outside it is not
                # a claim this gate verified -- and passing it is worse than
                # having no check, because the green says it was.
                reasons.append(
                    "claims byte identity but names no artifact this gate can "
                    "resolve; name the schema artifact, its stamp, "
                    f"{SCHEMA_PATH.as_posix()}, or {STAMP_PATH.as_posix()}"
                )
            changed = [
                path.as_posix()
                for path in named
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
