"""Capture the documents a release actually accepted, by watching it accept them.

Loaded as a pytest plugin, not as a conftest, so it costs an ordinary test run
nothing: `pytest -p scripts.capture_corpus` with `TIERGRAPH_CORPUS_OUT` set.

The point is what makes the corpus worth having. A growth corpus derived from the
current declarations would be accepted by the current decoder by construction, and
the gate over it would pass without ever being able to fail -- the same defect as
comparing two consumers of one source. What is captured here is an EXECUTION: the
suite decodes these documents, this release accepted them, and that is a fact
about this release rather than a re-derivation from its definitions.

Entries are written `unadjudicated`. Capture records that a release accepted a
document; whether the spec ever ALLOWED it is a separate claim nobody has made
yet, and the gate exists to ask for it the first time the decoder tightens.

Patching happens in `pytest_configure`, which runs before collection imports any
test module. That ordering is load-bearing: the suite binds `loads` with
`from tiergraph import loads` at module scope, so a patch applied later would be
shadowed by references already taken.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import tiergraph
import tiergraph.wire

ENV_OUT = "TIERGRAPH_CORPUS_OUT"


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
    """Write what the run accepted, if this was a capture run."""
    destination = os.environ.get(ENV_OUT)
    if not destination:
        return
    version = os.environ.get("TIERGRAPH_CORPUS_VERSION", "unreleased")
    seen = len(_RECORDER.accepted)
    count = write_corpus(Path(destination), _RECORDER.entries(version))
    dropped = seen - count
    print(
        f"\ncaptured {count} accepted documents -> {destination}"
        f" ({dropped} of {seen} dropped: accepted only under test scaffolding)"
    )
