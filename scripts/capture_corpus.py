"""Capture the documents a release actually accepted, by watching it accept them.

Loaded as a pytest plugin, not as a conftest, so it costs an ordinary test run
nothing: `pytest -p scripts.capture_corpus` with `TIERGRAPH_CORPUS_OUT` set.

The point is what makes the corpus worth having. A growth corpus derived from the
current declarations would be accepted by the current decoder by construction, and
the gate over it would pass without ever being able to fail -- the same defect as
comparing two consumers of one source. What is captured here is an EXECUTION: the
suite decodes these documents, this release accepted them, and that is a fact
about this release rather than a re-derivation from its definitions.

NEW entries are written `unadjudicated`. Capture records that a release accepted
a document; whether the spec ever ALLOWED it is a separate claim nobody has made
yet, and the gate exists to ask for it the first time the decoder tightens.

A capture MERGES into the corpus; it does not rewrite it. The dispositions and
reasons an existing row carries are adjudications, made once by hand and read by
`scripts/check_format_semantics.py` every gate run. A capture is not entitled to
any of them: it observed an acceptance, which is the one thing a disposition is
not. So an existing row is carried through byte for byte -- its `captured_at`
included, because that stamp is when the acceptance was witnessed and the span
the gate covers is measured from it -- and only documents the corpus does not
already hold are appended.

The rows that make the difference stark are the ones adjudicated `never-legal`.
A wholesale rewrite loses them and cannot get them back: the decoder has since
been tightened against exactly those documents, so no capture can witness one
again. That a release once accepted them, and that somebody afterwards ruled the
spec never allowed them, is precisely the record a frozen corpus exists to hold.

Patching happens in `pytest_configure`, which runs before collection imports any
test module. That ordering is load-bearing: the suite binds `loads` with
`from tiergraph import loads` at module scope, so a patch applied later would be
shadowed by references already taken.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tiergraph
import tiergraph.wire

ENV_OUT = "TIERGRAPH_CORPUS_OUT"

# Row identity, and the only field a corpus row means anything without. The gate
# hands `document` and nothing else to the decoder, so two rows carrying the same
# text are the same claim about the same bytes -- and the text is stable across
# captures, because the recorder stores exactly what the suite passed to `loads`
# and the suite builds its documents from fixed literals and canonical `dumps`
# output. Deliberately NOT a re-canonicalization: the corpus holds a compact and
# a pretty-printed spelling of the same graph as separate rows, and they are two
# separate acceptances. Deliberately not a digest either, which would key on the
# same bytes while making the corpus unreadable.
IDENTITY = "document"

# The one disposition whose absence from a new capture is already accounted for.
# `never-legal` says the spec never allowed the document and the decoder has been
# corrected, so a later capture CANNOT witness it -- that is what the ruling
# means. Absence is the expected consequence, not a finding.
NEVER_LEGAL = "never-legal"


class CaptureRefused(Exception):
    """A merge that would have lost a row the corpus already holds."""


class Recorder:
    """Collect every document the decoder accepts, deduplicated."""

    def __init__(self) -> None:
        """Start with nothing seen and no loader captured."""
        self.accepted: dict[str, None] = {}
        self.original: Any = None

    def wrap(self, loader: Any) -> Any:
        """Return `loader` with acceptances recorded and refusals left alone."""

        self.original = loader

        def recording(document: str | bytes) -> Any:
            result = loader(document)
            text = document.decode("utf-8") if isinstance(document, bytes) else document
            self.accepted.setdefault(text, None)
            return result

        return recording

    def surviving(self) -> list[str]:
        """Return the documents that still load once the suite is over.

        A test run accepts things the release does not. The conformance suite
        patches the declaration shapes so a near-miss is admitted, proving the
        schema and the codec agree about it, and restores them afterwards --
        so an acceptance recorded mid-test can depend on scaffolding rather than
        on the format. Measured on the first capture: seven of 193, carrying a
        literal ``__unknown__`` field or a negative relation-side maximum, both
        of which the released decoder refuses.

        Re-loading each candidate after the run, through the unpatched loader,
        drops exactly those. Without it the corpus records the harness and the
        gate over it would demand a version position for un-breaking something
        that was never accepted in the first place.
        """
        kept = []
        for document in sorted(self.accepted):
            try:
                self.original(document)
            except Exception:
                continue
            kept.append(document)
        return kept

    def entries(self, captured_at: str) -> list[dict[str, str]]:
        """Return corpus rows in a stable order, newest capture stamp on each."""
        return [
            {
                "document": document,
                "captured_at": captured_at,
                "disposition": "unadjudicated",
            }
            for document in self.surviving()
        ]


def write_corpus(path: Path, rows: list[dict[str, str]]) -> int:
    """Write the corpus as one JSON object per line; return how many."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
        ),
        encoding="utf-8",
    )
    return len(rows)


def read_corpus(path: Path) -> list[dict[str, str]]:
    """Return the rows the corpus already holds, or none if there is no corpus.

    A missing file is a first capture, not a fault; an unreadable one is a fault
    and `json.loads` says so rather than this returning an empty corpus that a
    merge would then treat as nothing to preserve.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    return [json.loads(line) for line in text.splitlines() if line.strip()]


@dataclass(frozen=True, slots=True)
class Merge:
    """What one capture did to the corpus it merged into.

    `kept` and `added` partition `rows`, and `unwitnessed` names the existing
    rows this capture did not reproduce and did not need to -- every one of them
    still in `rows`. There is no fourth number, because a merge that would have
    lost a row does not produce a `Merge` at all.
    """

    rows: tuple[dict[str, str], ...]
    kept: int
    added: int
    unwitnessed: tuple[str, ...]


def merge_corpus(
    existing: list[dict[str, str]], captured: list[dict[str, str]]
) -> Merge:
    """Fold a fresh capture into the corpus, preserving every row already there.

    Additive by construction: an existing row is carried through unchanged, a
    captured document the corpus does not hold is appended, and nothing is
    edited. Rows come back sorted by document, which is the order the corpus is
    already in, so a merge shows up as an insert-only diff.

    The refusal is the point. An existing row the capture did not reproduce is
    never silently dropped -- but neither is it silently kept, unless its own
    disposition already accounts for the absence. `never-legal` does: the decoder
    was corrected against that document, so no capture can witness it again. Any
    other row going unreproduced means the suite stopped exercising a document
    the corpus still asserts, and carrying it forward without saying so hides
    that the frozen record and the running suite have drifted apart.
    """
    witnessed = {row[IDENTITY] for row in captured}
    unwitnessed = tuple(
        row[IDENTITY] for row in existing if row[IDENTITY] not in witnessed
    )
    lost = [
        row
        for row in existing
        if row[IDENTITY] not in witnessed and row.get("disposition") != NEVER_LEGAL
    ]
    if lost:
        raise CaptureRefused(
            f"{len(lost)} of {len(existing)} corpus rows were not reproduced by "
            "this capture, and no disposition accounts for that. A capture "
            "merges into the corpus and never drops a row, so nothing was "
            "written. Each is a question: the suite stopped exercising a "
            "document it still asserts, or the row was never reproducible and "
            "belongs adjudicated. Rows: "
            + "; ".join(
                f"[{row.get('disposition', 'unknown')}] {row[IDENTITY][:120]!r}"
                for row in lost
            )
        )
    held = {row[IDENTITY] for row in existing}
    added = [row for row in captured if row[IDENTITY] not in held]
    rows = sorted(existing + added, key=lambda row: row[IDENTITY])
    return Merge(
        rows=tuple(rows),
        kept=len(existing),
        added=len(added),
        unwitnessed=unwitnessed,
    )


_RECORDER = Recorder()


def install(recorder: Recorder) -> None:
    """Route both spellings of the decoder through the recorder.

    Two, because `tiergraph.loads` and `tiergraph.wire.loads` are separate
    bindings and the suite uses the package one. Patching a single spelling would
    capture a fraction of the run and report the fraction as the corpus.
    """
    wrapped = recorder.wrap(tiergraph.wire.loads)
    tiergraph.wire.loads = wrapped
    tiergraph.loads = wrapped


def pytest_configure(config: object) -> None:  # noqa: ARG001 -- pytest matches this hook by parameter name
    """Install the recorder before collection imports anything."""
    if os.environ.get(ENV_OUT):
        install(_RECORDER)


def pytest_unconfigure(config: object) -> None:  # noqa: ARG001 -- pytest matches this hook by parameter name
    """Merge what the run accepted into the corpus, if this was a capture run.

    A refusal is raised, not printed and swallowed. This hook runs after pytest
    has fixed the session's exit status, so a message alone would leave a green
    run behind a corpus that was not written -- the operator's next act being to
    commit a file nothing changed. Raising ends the run non-zero with the reason
    on the way out.
    """
    destination = os.environ.get(ENV_OUT)
    if not destination:
        return
    version = os.environ.get("TIERGRAPH_CORPUS_VERSION", "unreleased")
    path = Path(destination)
    seen = len(_RECORDER.accepted)
    captured = _RECORDER.entries(version)
    scaffolded = seen - len(captured)
    try:
        merged = merge_corpus(read_corpus(path), captured)
    except CaptureRefused as refusal:
        print(f"\ncapture refused: {refusal}", file=sys.stderr)
        raise
    count = write_corpus(path, list(merged.rows))
    print(
        f"\ncaptured {len(captured)} accepted documents"
        f" ({scaffolded} of {seen} dropped: accepted only under test scaffolding);"
        f" merged into {destination}: {merged.kept} rows kept, {merged.added} added,"
        f" {count} in the corpus."
        f" {len(merged.unwitnessed)} kept rows this capture did not witness,"
        " each already adjudicated never-legal."
    )
