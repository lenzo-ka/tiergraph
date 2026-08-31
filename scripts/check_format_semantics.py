"""Refuse a semantic narrowing that no version position has paid for.

`check_format_growth.py` compares the committed schema against the last released
one, which covers the format's structural shape. The decoder is the authority for
everything else -- declaration compatibility, acyclicity, reference validity --
and a release that tightens one of those refuses a document the previous release
accepted while changing no schema byte. This gate is the other half.

It reads a corpus of documents a previous release ACCEPTED, and runs the current
decoder over them. The corpus is captured at a release and frozen; it is not
regenerated, because a corpus derived from current code is trivially accepted by
current code and the check would pass by construction. What is continuous is the
checking, not the capture.

A document that no longer loads is not automatically a break. Owner ruling
2026-08-31: a narrowing prices a version position only if the document was LEGAL.
Refusing what the spec never allowed is a fix, and the corpus entry was itself
wrong. So each entry carries a disposition, and the gate fails on any refusal the
disposition does not already account for -- it reports that a document stopped
loading and that nobody has said which case it is. Deciding legality is the
spec's business, not this script's.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from tiergraph import GraphValidationError
from tiergraph.schema import Refusal
from tiergraph.wire import loads

ROOT = Path(__file__).resolve().parent.parent
CORPUS_PATH: Path = ROOT / "corpus" / "accepted-documents.jsonl"


class Disposition(StrEnum):
    """What has been established about one corpus entry's legality."""

    UNADJUDICATED = "unadjudicated"
    LEGAL = "legal"
    NEVER_LEGAL = "never-legal"


@dataclass(frozen=True, slots=True)
class Entry:
    """One document a release accepted, and what is known about it."""

    document: str
    captured_at: str
    disposition: Disposition
    reason: str

    def __post_init__(self) -> None:
        """Require a reason exactly where the disposition claims one."""
        if self.disposition is Disposition.NEVER_LEGAL and not self.reason:
            raise ValueError(
                "a never-legal entry states why the spec never allowed it; "
                "an unexplained never-legal is indistinguishable from a "
                "convenient one"
            )
        if self.disposition is not Disposition.NEVER_LEGAL and self.reason:
            raise ValueError(
                f"a {self.disposition} entry carries no reason; a reason here "
                "would read as a judgment nobody made"
            )


def parse_entry(line: str) -> Entry:
    """Build one entry from its corpus line, refusing an unknown disposition."""
    raw = json.loads(line)
    spelling = raw["disposition"]
    if spelling not in tuple(Disposition):
        raise ValueError(
            f"unknown disposition {spelling!r}; expected one of "
            f"{', '.join(sorted(str(value) for value in Disposition))}"
        )
    return Entry(
        document=raw["document"],
        captured_at=raw["captured_at"],
        disposition=Disposition(spelling),
        reason=raw.get("reason", ""),
    )


def read_corpus(path: Path) -> tuple[Entry, ...]:
    """Read every entry, or refuse a corpus that is absent rather than empty."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise ValueError(
            f"corpus {path} is missing; capture one with "
            "scripts/capture_corpus.py before this gate can say anything"
        ) from error
    return tuple(parse_entry(line) for line in text.splitlines() if line.strip())


def refusal_of(entry: Entry, loader: object) -> str:
    """Return why the current decoder refuses this entry, or an empty string.

    Both refusal channels are caught because the decoder has two. Its own
    `Refusal` carries a stage; a `GraphValidationError` raised while constructing
    the graph does not, and `wire.loads` does not convert one into the other.
    A gate that caught only the first would miss exactly the acyclicity and
    reference-validity constraints this file exists to watch.
    """
    try:
        loader(entry.document)  # type: ignore[operator]
    except (Refusal, GraphValidationError) as error:
        return f"{type(error).__name__}: {error}"
    return ""


def findings(entries: tuple[Entry, ...], loader: object) -> list[str]:
    """Return one message per refusal the entry's disposition does not cover."""
    reported = []
    for index, entry in enumerate(entries):
        why = refusal_of(entry, loader)
        if not why:
            continue
        if entry.disposition is Disposition.NEVER_LEGAL:
            continue
        reported.append(
            f"corpus entry {index} (captured at {entry.captured_at}, "
            f"{entry.disposition}) no longer loads: {why}"
        )
    return reported


def main(argv: list[str] | None = None) -> int:
    """Report every uncovered refusal, or say the corpus still loads."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=CORPUS_PATH)
    arguments = parser.parse_args(argv)

    try:
        entries = read_corpus(arguments.corpus)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    if not entries:
        print(
            f"corpus {arguments.corpus} holds no entries; this gate is "
            "reporting nothing because it was given nothing",
            file=sys.stderr,
        )
        return 2

    reported = findings(entries, loads)
    for message in reported:
        print(message, file=sys.stderr)
    if reported:
        print(
            f"{len(reported)} of {len(entries)} captured documents stopped "
            "loading. Each is a question with two answers: the spec allowed it, "
            "so this is a break and owes a version position; or the spec never "
            "did, so this is a fix and the entry should be marked never-legal "
            "with the reason. This gate does not decide which.",
            file=sys.stderr,
        )
        return 1
    print(f"every one of the {len(entries)} captured documents still loads")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
