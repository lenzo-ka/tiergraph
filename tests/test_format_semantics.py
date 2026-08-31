"""REGRESSION tests for the semantic growth gate and its capture.

The gate's whole value is that it fails where the schema gate cannot, so the
load-bearing test here is the one that breaks the decoder in place and confirms
this gate reports it. A gate proved only on a corpus its own release accepted
proves nothing: current code accepts what current code produced.
"""

import json
from pathlib import Path

import pytest
from scripts import capture_corpus, check_format_semantics, check_tracked_clean

import tiergraph
import tiergraph.wire
from tiergraph import Graph, GraphValidationError, NamespaceDeclaration
from tiergraph.schema import Refusal, RefusalStage
from tiergraph.wire import dumps, loads

NS = "urn:corpus"


def a_document() -> str:
    """Return the canonical spelling of a minimal valid graph."""
    return dumps(Graph((NamespaceDeclaration("c", NS),), (), ()))


def corpus_line(document: str, disposition: str = "unadjudicated", **extra: str) -> str:
    """Return one corpus row as it is written to disk."""
    row = {"document": document, "captured_at": "0.2.0", "disposition": disposition}
    row.update(extra)
    return json.dumps(row, sort_keys=True)


# --------------------------------------------------------------- entries


def test_never_legal_entry_requires_its_reason() -> None:
    """REGRESSION: an unexplained never-legal reads as a convenient one."""
    with pytest.raises(ValueError) as refusal:
        check_format_semantics.Entry(
            document="{}",
            captured_at="0.2.0",
            disposition=check_format_semantics.Disposition.NEVER_LEGAL,
            reason="",
        )
    assert "never allowed it" in str(refusal.value)


def test_other_dispositions_refuse_a_reason() -> None:
    """REGRESSION: a reason on an unadjudicated entry asserts a judgment."""
    with pytest.raises(ValueError) as refusal:
        check_format_semantics.Entry(
            document="{}",
            captured_at="0.2.0",
            disposition=check_format_semantics.Disposition.UNADJUDICATED,
            reason="because",
        )
    assert "carries no reason" in str(refusal.value)


def test_parse_entry_refuses_an_unknown_disposition() -> None:
    """REGRESSION: a misspelled disposition is refused, not silently ignored."""
    with pytest.raises(ValueError) as refusal:
        check_format_semantics.parse_entry(corpus_line("{}", disposition="probably"))
    assert "unknown disposition" in str(refusal.value)


def test_parse_entry_accepts_a_never_legal_row_with_its_reason() -> None:
    """REGRESSION: the full row shape round-trips."""
    entry = check_format_semantics.parse_entry(
        corpus_line("{}", disposition="never-legal", reason="the spec forbade it")
    )
    assert entry.disposition is check_format_semantics.Disposition.NEVER_LEGAL
    assert entry.reason == "the spec forbade it"


# --------------------------------------------------------------- corpus


def test_a_missing_corpus_is_refused_rather_than_read_as_empty(tmp_path: Path) -> None:
    """REGRESSION: absent and empty mean opposite things; only one is silence."""
    with pytest.raises(ValueError) as refusal:
        check_format_semantics.read_corpus(tmp_path / "nothing.jsonl")
    assert "is missing" in str(refusal.value)


def test_read_corpus_skips_blank_lines(tmp_path: Path) -> None:
    """REGRESSION: a trailing newline is not an entry."""
    path = tmp_path / "corpus.jsonl"
    path.write_text(corpus_line(a_document()) + "\n\n", encoding="utf-8")
    assert len(check_format_semantics.read_corpus(path)) == 1


# --------------------------------------------------------------- the gate


def test_the_gate_passes_when_every_document_still_loads(tmp_path: Path) -> None:
    """CHARACTERIZATION: green against the release that captured it."""
    path = tmp_path / "corpus.jsonl"
    path.write_text(corpus_line(a_document()) + "\n", encoding="utf-8")
    assert check_format_semantics.main(["--corpus", str(path)]) == 0


def test_the_gate_FAILS_on_a_narrowing_the_schema_cannot_see(tmp_path: Path) -> None:
    """REGRESSION: the load-bearing one -- break the decoder, gate reports it.

    Measured against the real gates when this was built: a decode-time refusal
    added to `wire.loads` left `generate_schema.py --check` and
    `check_format_growth.py` both at exit 0, while this gate reported 76 of 186
    captured documents. That asymmetry is the reason this file exists.
    """
    path = tmp_path / "corpus.jsonl"
    path.write_text(corpus_line(a_document()) + "\n", encoding="utf-8")
    entries = check_format_semantics.read_corpus(path)

    def narrowed(document: str) -> Graph:
        raise Refusal(RefusalStage.SEMANTICS, "narrowed after capture")

    reported = check_format_semantics.findings(entries, narrowed)
    assert len(reported) == 1
    assert "no longer loads" in reported[0]
    assert "narrowed after capture" in reported[0]


def test_the_gate_catches_the_graph_validation_channel_too(tmp_path: Path) -> None:
    """REGRESSION: `loads` never converts GraphValidationError into a Refusal.

    Acyclicity and reference validity -- the constraints `format.md` names as
    the decoder's authority -- surface through that second channel. A gate
    catching only `Refusal` would be blind to exactly what it exists to watch.
    """
    path = tmp_path / "corpus.jsonl"
    path.write_text(corpus_line(a_document()) + "\n", encoding="utf-8")
    entries = check_format_semantics.read_corpus(path)

    def narrowed(document: str) -> Graph:
        raise GraphValidationError("a semantic constraint tightened")

    reported = check_format_semantics.findings(entries, narrowed)
    assert len(reported) == 1
    assert "GraphValidationError" in reported[0]


def test_a_never_legal_entry_may_stop_loading_without_failing(tmp_path: Path) -> None:
    """REGRESSION: refusing what the spec never allowed is a fix, not a break."""
    path = tmp_path / "corpus.jsonl"
    path.write_text(
        corpus_line(
            a_document(), disposition="never-legal", reason="the spec forbade it"
        )
        + "\n",
        encoding="utf-8",
    )
    entries = check_format_semantics.read_corpus(path)

    def narrowed(document: str) -> Graph:
        raise Refusal(RefusalStage.SEMANTICS, "now refused, correctly")

    assert check_format_semantics.findings(entries, narrowed) == []


def test_main_refuses_an_empty_corpus_rather_than_reporting_success(
    tmp_path: Path,
) -> None:
    """REGRESSION: a gate given nothing must not read as a gate that passed."""
    path = tmp_path / "corpus.jsonl"
    path.write_text("\n", encoding="utf-8")
    assert check_format_semantics.main(["--corpus", str(path)]) == 2


def test_main_reports_a_missing_corpus_as_a_setup_error(tmp_path: Path) -> None:
    """REGRESSION: exit 2 distinguishes 'cannot run' from 'found a break'."""
    assert check_format_semantics.main(["--corpus", str(tmp_path / "gone.jsonl")]) == 2


def test_main_returns_one_when_a_document_stops_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REGRESSION: an uncovered refusal is a red build, per the owner ruling."""
    path = tmp_path / "corpus.jsonl"
    path.write_text(corpus_line('{"not": "a document"}') + "\n", encoding="utf-8")
    assert check_format_semantics.main(["--corpus", str(path)]) == 1


# --------------------------------------------------------------- capture


def test_capture_records_what_the_decoder_accepts() -> None:
    """REGRESSION: an acceptance is recorded; a refusal is not."""
    recorder = capture_corpus.Recorder()
    wrapped = recorder.wrap(loads)
    document = a_document()
    wrapped(document)
    with pytest.raises((Refusal, GraphValidationError)):
        wrapped('{"format_version": "0.2.0"}')
    assert list(recorder.accepted) == [document]


def test_capture_records_a_bytes_document_as_its_text() -> None:
    """REGRESSION: one spelling in the corpus, whatever the caller passed."""
    recorder = capture_corpus.Recorder()
    wrapped = recorder.wrap(loads)
    document = a_document()
    wrapped(document.encode("utf-8"))
    assert list(recorder.accepted) == [document]


def test_capture_drops_what_only_loaded_under_test_scaffolding() -> None:
    """REGRESSION: the defect the first real capture actually had.

    Seven of 193 documents were recorded as accepted because the conformance
    suite had patched the declaration shapes for the duration of a test. Re-loading
    each candidate after the run, through the unpatched loader, is what removes
    them -- without it the corpus records the harness.
    """
    recorder = capture_corpus.Recorder()
    recorder.accepted = {"kept": None, "scaffolded": None}

    def only_one_survives(document: str) -> None:
        if document == "scaffolded":
            raise Refusal(RefusalStage.SYNTAX, "not really acceptable")

    recorder.original = only_one_survives
    assert recorder.surviving() == ["kept"]
    assert [row["document"] for row in recorder.entries("0.2.0")] == ["kept"]
    assert recorder.entries("0.2.0")[0]["disposition"] == "unadjudicated"


def test_write_corpus_is_one_json_object_per_line(tmp_path: Path) -> None:
    """REGRESSION: the format the gate reads back."""
    path = tmp_path / "nested" / "corpus.jsonl"
    written = capture_corpus.write_corpus(
        path,
        [{"document": "a", "captured_at": "0.2.0", "disposition": "unadjudicated"}],
    )
    assert written == 1
    assert json.loads(path.read_text(encoding="utf-8").strip())["document"] == "a"


def test_install_patches_both_spellings_of_the_decoder() -> None:
    """REGRESSION: the suite binds `from tiergraph import loads`.

    Patching only `tiergraph.wire.loads` would capture a fraction of a run and
    report the fraction as the corpus.
    """
    original_package, original_module = tiergraph.loads, tiergraph.wire.loads
    recorder = capture_corpus.Recorder()
    try:
        capture_corpus.install(recorder)
        assert tiergraph.loads is not original_package
        assert tiergraph.wire.loads is not original_module
        tiergraph.loads(a_document())
        assert len(recorder.accepted) == 1
    finally:
        tiergraph.loads = original_package
        tiergraph.wire.loads = original_module


def test_the_plugin_does_nothing_without_the_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REGRESSION: an ordinary test run pays nothing for this plugin."""
    monkeypatch.delenv(capture_corpus.ENV_OUT, raising=False)
    before = tiergraph.loads
    capture_corpus.pytest_configure(None)
    capture_corpus.pytest_unconfigure(None)
    assert tiergraph.loads is before


def test_the_plugin_writes_when_the_environment_names_a_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REGRESSION: configure installs, unconfigure writes."""
    destination = tmp_path / "out.jsonl"
    monkeypatch.setenv(capture_corpus.ENV_OUT, str(destination))
    monkeypatch.setenv("TIERGRAPH_CORPUS_VERSION", "9.9.9")
    original_package, original_module = tiergraph.loads, tiergraph.wire.loads
    monkeypatch.setattr(capture_corpus, "_RECORDER", capture_corpus.Recorder())
    try:
        capture_corpus.pytest_configure(None)
        tiergraph.loads(a_document())
        capture_corpus.pytest_unconfigure(None)
    finally:
        tiergraph.loads = original_package
        tiergraph.wire.loads = original_module
    rows = [json.loads(line) for line in destination.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["captured_at"] == "9.9.9"


def test_a_json_escaped_url_is_read_by_its_real_prefix() -> None:
    """REGRESSION: the corpus is JSON, so its URLs arrive carrying an escape.

    A document stored as a JSON string turns a trailing quote into a backslash
    escape, and the URL pattern takes the backslash with it. Before this was
    stripped, an allowlisted prefix read as unallowlisted purely because of the
    quoting, and the publishability gate reported a leak that was an artifact of
    the file format rather than anything in the content.
    """
    assert (
        check_tracked_clean._url_prefix("https://example.com/score\\")
        == "example.com/score"
    )
    assert (
        check_tracked_clean._url_prefix("https://example.com/score")
        == "example.com/score"
    )
