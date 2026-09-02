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


def another_document() -> str:
    """Return a second valid graph, distinct from the minimal one."""
    return dumps(
        Graph(
            (NamespaceDeclaration("c", NS), NamespaceDeclaration("d", NS + ":other")),
            (),
            (),
        )
    )


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


def test_the_gate_passes_when_every_document_still_loads(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CHARACTERIZATION: green against the release that captured it.

    A one-row corpus cannot tell "every document loads" from "the first one
    does": a gate reading only its first entry passes that fixture exactly as a
    correct one would.  Two distinct documents are written and the entry count
    is asserted, so the quantifier has something to range over.

    The universal wording is claimed only here, where every entry did load.
    """
    path = tmp_path / "corpus.jsonl"
    documents = (a_document(), another_document())
    assert len(set(documents)) == len(documents)
    path.write_text(
        "".join(corpus_line(document) + "\n" for document in documents),
        encoding="utf-8",
    )
    assert len(check_format_semantics.read_corpus(path)) == len(documents)
    assert check_format_semantics.main(["--corpus", str(path)]) == 0
    assert capsys.readouterr().out == (
        "every one of the 2 captured documents still loads\n"
    )


def test_a_pass_over_a_refused_entry_does_not_claim_every_document_loads(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """REGRESSION: the success line said something the committed corpus disproves.

    Seven of the 186 committed entries carry an unpaired surrogate, the reader
    refuses them, and their never-legal dispositions make that a pass. The gate
    printed "every one of the 186 captured documents still loads" through all of
    it -- a true verdict under a false sentence, which is the worse of the two
    failures because the verdict is what gets trusted and the sentence is what
    gets quoted. What a pass establishes is narrower: everything loaded except
    where a disposition already said it should not, and this gate never rechecks
    the disposition.
    """
    path = tmp_path / "corpus.jsonl"
    path.write_text(
        corpus_line(a_document())
        + "\n"
        + corpus_line(
            '{"never": "loadable"}',
            disposition="never-legal",
            reason="the spec forbade it",
        )
        + "\n",
        encoding="utf-8",
    )
    assert check_format_semantics.main(["--corpus", str(path)]) == 0
    printed = capsys.readouterr().out
    assert "every one of" not in printed
    assert "1 of 2 captured documents still load; the remaining 1 do not" in printed
    assert "not that those adjudications are right" in printed


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

    outcome = check_format_semantics.review(entries, narrowed)
    assert outcome.loaded == 0
    assert len(outcome.findings) == 1
    assert "no longer loads" in outcome.findings[0]
    assert "narrowed after capture" in outcome.findings[0]


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

    outcome = check_format_semantics.review(entries, narrowed)
    assert len(outcome.findings) == 1
    assert "GraphValidationError" in outcome.findings[0]


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

    outcome = check_format_semantics.review(entries, narrowed)
    assert outcome.findings == ()
    assert (outcome.loaded, outcome.adjudicated) == (0, 1)


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


def test_the_committed_corpus_splits_exactly_where_its_dispositions_say() -> None:
    """The reader refuses every never-legal entry and still reads the rest.

    The seven never-legal entries were dispositioned ahead of the refusal that
    now meets them: each names the unpaired surrogate in a namespace URI as
    something the format never had a canonical byte form for. This pins both
    halves of that claim against the committed file, so a reader that stopped
    refusing them and a reader that started refusing anything else each fail
    here, rather than only in the gate's aggregate count.
    """
    entries = check_format_semantics.read_corpus(check_format_semantics.CORPUS_PATH)
    assert len(entries) == 186
    never_legal = check_format_semantics.Disposition.NEVER_LEGAL

    refused = [entry for entry in entries if entry.disposition is never_legal]
    assert len(refused) == 7
    for entry in refused:
        with pytest.raises(ValueError) as refusal:
            loads(entry.document)
        assert type(refusal.value) is Refusal
        assert refusal.value.stage is RefusalStage.ENCODING
        assert "namespace value " in str(refusal.value)
        assert "has unsupported character U+D800" in str(refusal.value)

    accepted = [entry for entry in entries if entry.disposition is not never_legal]
    assert len(accepted) == 179
    for entry in accepted:
        loads(entry.document)


def test_the_gate_FAILS_when_a_never_legal_entry_is_read_again(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """REGRESSION: a re-acceptance is a finding, not a success.

    The gate counted any accepted document as loaded before it consulted the
    disposition, so an entry adjudicated never-legal that a later reader ACCEPTS
    fell into the success count and produced nothing. The suite caught that
    direction and the gate did not, which meant a consumer running only
    `make format-semantics` got a different answer from one running pytest.

    The construction is a valid document carrying a never-legal disposition:
    nothing refuses it, and the corpus says nothing may accept it. Both halves
    are needed -- an invalid document would be refused and take the adjudicated
    path instead, which is the case that already passed.
    """
    path = tmp_path / "corpus.jsonl"
    path.write_text(
        corpus_line(a_document())
        + "\n"
        + corpus_line(
            a_document(),
            disposition="never-legal",
            reason="ruled to have no canonical byte form",
        )
        + "\n",
        encoding="utf-8",
    )
    assert check_format_semantics.main(["--corpus", str(path)]) == 1
    errors = capsys.readouterr().err
    assert "adjudicated never-legal and now loads" in errors
    assert "widened past what was ruled" in errors
    # The reverse condition is reported apart from the forward one: the sentence
    # about documents that STOPPED loading must not be printed for a document
    # that STARTED loading.
    assert "stopped loading" not in errors
