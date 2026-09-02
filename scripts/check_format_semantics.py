"""Refuse a semantic narrowing that no version position has paid for.

`check_format_growth.py` compares the committed schema against the last released
one, which covers the format's structural shape. The decoder is the authority for
everything else -- declaration compatibility, acyclicity, reference validity --
and a release that tightens one of those refuses a document the previous release
accepted while changing no schema byte. This gate is the other half.

It reads a corpus of documents ACCEPTED when the corpus was captured, and runs
the current decoder over them. The corpus is frozen and not regenerated, because
a corpus derived from current code is trivially accepted by current code and the
check would pass by construction. What is continuous is the checking, not the
capture.

Capture belongs at a release, so that the frozen entries are ones a release
accepted and this gate then covers the span since that release. Every entry the
corpus holds today was captured from a development tree instead: each records the
version its capture ran under, and no published release has accepted any of them.
So what this gate currently catches is a decoder that has tightened since that
capture, and it enforces nothing about cross-release compatibility yet.

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
    """One document accepted at capture time, and what is known about it."""

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


@dataclass(frozen=True, slots=True)
class Outcome:
    """What one pass of the current decoder over the corpus established.

    The four fields partition the corpus along two axes at once -- what the
    decoder did, and what the disposition says it should have done -- because
    either can disagree with the other and they fail in opposite directions.
    A document loaded and was permitted to; or it was refused where a
    disposition accounts for the refusal; or it was refused where nothing does;
    or it loaded where a disposition says it never may.

    The last is the reverse of the third and was once invisible here: counting
    an accepted document as ``loaded`` before consulting its disposition passes
    a re-acceptance green, so a change that stops refusing what the corpus says
    it must refuse produced no finding. The suite caught it
    (``tests/test_format_semantics.py``) and this gate did not, which meant the
    two disagreed about what the gate established.

    They are reported apart rather than summed. "Stopped loading" and "started
    loading" are different questions with different answers, and one sentence
    covering both would be false of half its subjects.
    """

    loaded: int
    adjudicated: int
    findings: tuple[str, ...]
    readmitted: tuple[str, ...] = ()


def review(entries: tuple[Entry, ...], loader: object) -> Outcome:
    """Run the decoder once over every entry and partition what it did.

    The disposition is consulted on BOTH outcomes, not only on a refusal: an
    accepted document that the corpus adjudicated never-legal is a finding in
    its own right, and reading it as a plain success is how a re-acceptance
    used to pass this gate.
    """
    loaded = 0
    adjudicated = 0
    found: list[str] = []
    readmitted: list[str] = []
    for index, entry in enumerate(entries):
        why = refusal_of(entry, loader)
        if not why:
            if entry.disposition is Disposition.NEVER_LEGAL:
                readmitted.append(
                    f"corpus entry {index} (captured at {entry.captured_at}) is "
                    "adjudicated never-legal and now loads"
                )
                continue
            loaded += 1
            continue
        if entry.disposition is Disposition.NEVER_LEGAL:
            adjudicated += 1
            continue
        found.append(
            f"corpus entry {index} (captured at {entry.captured_at}, "
            f"{entry.disposition}) no longer loads: {why}"
        )
    return Outcome(
        loaded=loaded,
        adjudicated=adjudicated,
        findings=tuple(found),
        readmitted=tuple(readmitted),
    )


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

    outcome = review(entries, loads)
    for message in outcome.readmitted:
        print(message, file=sys.stderr)
    if outcome.readmitted:
        print(
            f"{len(outcome.readmitted)} of {len(entries)} captured documents are "
            "adjudicated never-legal and the reader accepted them. This is the "
            "reverse of the condition below and it is not a version question: "
            "the corpus says the format never had a canonical byte form for "
            "these, so a reader that takes them has widened past what was ruled "
            "rather than past what was released. Either the reader regressed, or "
            "the adjudication was wrong and owes a rewritten reason. This gate "
            "does not decide which.",
            file=sys.stderr,
        )
        return 1
    for message in outcome.findings:
        print(message, file=sys.stderr)
    if outcome.findings:
        print(
            f"{len(outcome.findings)} of {len(entries)} captured documents "
            "stopped loading with nothing accounting for it. Each is a question "
            "with two answers: the spec allowed it, so this is a break and owes "
            "a version position; or the spec never did, so this is a fix and "
            "the entry should be marked never-legal with the reason. This gate "
            "does not decide which.",
            file=sys.stderr,
        )
        return 1
    if outcome.adjudicated:
        print(
            f"{outcome.loaded} of {len(entries)} captured documents still load; "
            f"the remaining {outcome.adjudicated} do not, each already "
            "adjudicated never-legal in the corpus. This gate establishes that "
            "no refusal is unaccounted for, not that those adjudications are "
            "right."
        )
        return 0
    print(f"every one of the {len(entries)} captured documents still loads")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
