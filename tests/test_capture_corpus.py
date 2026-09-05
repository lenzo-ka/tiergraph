"""REGRESSION tests for the corpus merge.

The defect these exist against is a capture that REWRITES `corpus/accepted-documents.jsonl`.
The corpus holds hand-made adjudications -- a disposition and, where the ruling
is `never-legal`, the reason the spec never allowed the document. A capture
observes acceptances and can produce none of that, so a wholesale rewrite
replaced every ruling with `unadjudicated` and lost the `never-legal` rows for
good: the decoder was tightened against exactly those documents, so no later
capture can witness one again.

So the tests here are about what a merge PRESERVES and what it refuses to do
quietly, not about what it collects. Capture's own behavior is covered in
`tests/test_format_semantics.py` beside the gate that reads what it wrote.
"""

import json
from pathlib import Path

import pytest
from scripts import capture_corpus

# This mirrors the former refusal's 120-character truncation bound so the
# distinguishing suffixes begin beyond the text it used to report.
COMMON_PREFIX_PADDING = 120


class RetainConfig:
    """Supply the enabled capture option to the plugin hook."""

    def getoption(self, name: str) -> bool:
        """Return the retain flag while checking the hook asks by its public name."""
        assert name == capture_corpus.RETAIN_OPTION
        return True


def a_row(
    document: str, disposition: str = "legal", reason: str = ""
) -> dict[str, str]:
    """Return one corpus row, with a reason exactly where the gate demands one."""
    row = {
        "document": document,
        "captured_at": "0.2.0-dev",
        "disposition": disposition,
    }
    if reason:
        row["reason"] = reason
    return row


def a_capture(*documents: str) -> list[dict[str, str]]:
    """Return what a fresh capture hands the merge: unadjudicated, newly stamped."""
    return [
        {"document": document, "captured_at": "0.3.0", "disposition": "unadjudicated"}
        for document in documents
    ]


# ------------------------------------------------------------- reading


def test_a_missing_corpus_reads_as_no_rows(tmp_path: Path) -> None:
    """REGRESSION: a first capture has nothing to merge into and is not a fault."""
    assert capture_corpus.read_corpus(tmp_path / "absent.jsonl") == []


def test_reading_skips_blank_lines(tmp_path: Path) -> None:
    """REGRESSION: the gate's own reader tolerates them, so this one must too."""
    path = tmp_path / "corpus.jsonl"
    path.write_text(
        json.dumps(a_row("a")) + "\n\n" + json.dumps(a_row("b")) + "\n",
        encoding="utf-8",
    )
    assert [row["document"] for row in capture_corpus.read_corpus(path)] == ["a", "b"]


# ------------------------------------------------------------- merging


def test_an_existing_row_survives_a_capture_byte_for_byte() -> None:
    """REGRESSION: THE defect. A capture must not restate an adjudication.

    Every field is checked, not just the disposition: `reason` is the ruling's
    evidence and `captured_at` is when the acceptance was witnessed, which is
    what the gate measures its span from. A capture re-stamping either one
    rewrites a record it did not make.
    """
    adjudicated = a_row("kept", "never-legal", "the spec never allowed this")
    merged = capture_corpus.merge_corpus([adjudicated], a_capture("kept"))
    assert merged.rows == (adjudicated,)
    assert merged.kept == 1
    assert merged.added == 0


def test_a_missing_never_legal_row_refuses_by_default() -> None:
    """REGRESSION: an unreproduced adjudication needs an explicit choice."""
    ruled = a_row("gone", "never-legal", "an unpaired surrogate has no encoding")
    with pytest.raises(capture_corpus.CaptureRefused) as refusal:
        capture_corpus.merge_corpus([ruled], a_capture("other"))
    assert "gone" in str(refusal.value)


def test_the_retain_flag_keeps_a_missing_never_legal_row() -> None:
    """REGRESSION: the override retains the adjudication byte for byte."""
    # Parser has no public constructor, and Config exposes its parser only privately.
    parser = pytest.Parser(_ispytest=True)
    capture_corpus.pytest_addoption(parser)
    absent, unknown = parser.parse_known_and_unknown_args([])
    assert absent.retain_unreproduced is False
    assert unknown == []
    present, unknown = parser.parse_known_and_unknown_args(
        [capture_corpus.RETAIN_OPTION]
    )
    assert present.retain_unreproduced is True
    assert unknown == []
    ruled = a_row("gone", "never-legal", "an unpaired surrogate has no encoding")
    merged = capture_corpus.merge_corpus(
        [ruled], a_capture("other"), retain_unreproduced=True
    )
    assert ruled in merged.rows
    assert merged.unwitnessed == ("gone",)
    assert merged.kept == 1
    assert merged.added == 1


def test_only_documents_the_corpus_lacks_are_added() -> None:
    """REGRESSION: a capture adds; it does not restate what is already held."""
    merged = capture_corpus.merge_corpus([a_row("held")], a_capture("held", "fresh"))
    assert [row["document"] for row in merged.rows] == ["fresh", "held"]
    assert merged.added == 1
    assert [row for row in merged.rows if row["document"] == "fresh"] == [
        {"document": "fresh", "captured_at": "0.3.0", "disposition": "unadjudicated"}
    ]


def test_the_merged_corpus_stays_sorted_by_document() -> None:
    """REGRESSION: the corpus is already in this order, so a merge is an insert.

    A merge that appended would rewrite every line's neighborhood and make the
    diff unreviewable, which is how an unnoticed drop gets committed.
    """
    merged = capture_corpus.merge_corpus(
        [a_row("b"), a_row("d")], a_capture("a", "b", "c", "d", "e")
    )
    documents = [row["document"] for row in merged.rows]
    assert documents == sorted(documents)
    assert documents == ["a", "b", "c", "d", "e"]


def test_the_merge_refuses_rather_than_drop_an_unaccounted_row() -> None:
    """REGRESSION: silence here is the whole failure mode.

    A `legal` row the capture did not reproduce means the frozen record and the
    running suite have drifted apart. Dropping it destroys the record; keeping it
    without saying so hides the drift. The merge does neither and refuses.
    """
    with pytest.raises(capture_corpus.CaptureRefused) as refusal:
        capture_corpus.merge_corpus([a_row("vanished")], a_capture("something else"))
    message = str(refusal.value)
    assert "1 of 1" in message
    assert "[legal]" in message
    assert "vanished" in message


def test_an_unadjudicated_row_is_refused_over_too() -> None:
    """REGRESSION: only `never-legal` accounts for an absence, not "nobody ruled"."""
    with pytest.raises(capture_corpus.CaptureRefused):
        capture_corpus.merge_corpus([a_row("gone", "unadjudicated")], a_capture("kept"))


def test_a_refusal_names_every_lost_row_not_just_the_first() -> None:
    """REGRESSION: a partial report sends the operator back for a second run."""
    existing = [a_row("one"), a_row("two"), a_row("three")]
    with pytest.raises(capture_corpus.CaptureRefused) as refusal:
        capture_corpus.merge_corpus(existing, a_capture("two"))
    message = str(refusal.value)
    assert "2 of 3" in message
    assert "one" in message
    assert "three" in message


def test_a_refusal_distinguishes_documents_with_a_long_common_prefix() -> None:
    """REGRESSION: exact document text identifies each unreproduced corpus row."""
    common = (
        '{"format_version":"0.2.0","namespaces":[],"tiers":['
        + " " * COMMON_PREFIX_PADDING
    )
    first = common + '"first"]}'
    second = common + '"second"]}'
    with pytest.raises(capture_corpus.CaptureRefused) as refusal:
        capture_corpus.merge_corpus([a_row(first), a_row(second)], [])
    message = str(refusal.value)
    assert first in message
    assert second in message


# ------------------------------------------------------------- the plugin


def test_the_plugin_writes_nothing_when_the_merge_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REGRESSION: a refused capture leaves the corpus exactly as it was.

    And it raises. This hook runs after pytest has fixed the session's exit
    status, so a message alone would leave a green run behind an unwritten
    corpus -- and the operator committing a file nothing changed.
    """
    path = tmp_path / "corpus.jsonl"
    before = json.dumps(a_row("never captured"), sort_keys=True) + "\n"
    path.write_text(before, encoding="utf-8")
    monkeypatch.setenv(capture_corpus.ENV_OUT, str(path))
    monkeypatch.setattr(capture_corpus, "_RECORDER", capture_corpus.Recorder())
    with pytest.raises(capture_corpus.CaptureRefused):
        capture_corpus.pytest_unconfigure(None)
    assert path.read_text(encoding="utf-8") == before


def test_the_plugin_reports_what_the_merge_did(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """REGRESSION: the operator reads counts, so the counts must be the merge's."""
    path = tmp_path / "corpus.jsonl"
    path.write_text(
        json.dumps(a_row("ruled", "never-legal", "not a scalar value"), sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(capture_corpus.ENV_OUT, str(path))
    monkeypatch.setenv("TIERGRAPH_CORPUS_VERSION", "9.9.9")
    recorder = capture_corpus.Recorder()
    recorder.accepted = {"fresh": None}
    recorder.original = lambda document: None
    monkeypatch.setattr(capture_corpus, "_RECORDER", recorder)
    capture_corpus.pytest_unconfigure(RetainConfig())
    reported = capsys.readouterr().out
    assert "1 rows kept, 1 added, 2 in the corpus" in reported
    assert "1 kept rows this capture did not witness" in reported
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["disposition"] for row in rows] == ["unadjudicated", "never-legal"]
    assert rows[1]["reason"] == "not a scalar value"
