"""The CLI reads a declarative profile under the document envelope stages."""

from __future__ import annotations

import io
import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

import tiergraph
from tests.test_clock import clock_profile_data, reference_shape
from tests.test_spanview import fixture as span_fixture
from tests.test_spanview import profile_data as span_profile_data
from tiergraph.cli import build_parser, main
from tiergraph.schema import Refusal, RefusalStage

ProfileArgv = Callable[[dict[str, Path], Path], list[str]]


@pytest.fixture(name="documents")
def _documents(tmp_path: Path) -> dict[str, Path]:
    """Write the clock and span graphs whose profiles these cases replace."""
    clock_graph = tmp_path / "clock_graph.json"
    clock_graph.write_bytes(tiergraph.dump_bytes(reference_shape()))
    span_graph_value, span_profile_value = span_fixture()
    span_graph = tmp_path / "span_graph.json"
    span_graph.write_bytes(tiergraph.dump_bytes(span_graph_value))
    clock_profile = tmp_path / "clock_profile.json"
    clock_profile.write_text(json.dumps(clock_profile_data()), encoding="utf-8")
    span_profile = tmp_path / "span_profile.json"
    span_profile.write_text(
        json.dumps(span_profile_data(span_profile_value)), encoding="utf-8"
    )
    return {
        "clock_graph": clock_graph,
        "span_graph": span_graph,
        "clock_profile": clock_profile,
        "span_profile": span_profile,
        "output": tmp_path / "output.txt",
    }


def _clock_argv(documents: dict[str, Path], profile: Path) -> list[str]:
    """Spell the clock query that reads a profile beside its graph."""
    return [
        "clock",
        "coordinates",
        str(documents["clock_graph"]),
        "--profile",
        str(profile),
        "-o",
        str(documents["output"]),
    ]


def _span_argv(documents: dict[str, Path], profile: Path) -> list[str]:
    """Spell the span render that reads a profile beside its graph."""
    return [
        "span",
        "render",
        str(documents["span_graph"]),
        "--profile",
        str(profile),
        "--format",
        "text",
        "-o",
        str(documents["output"]),
    ]


def _graph_argv(documents: dict[str, Path], document: Path) -> list[str]:
    """Spell the same query with the candidate text supplied as the graph."""
    return [
        "clock",
        "coordinates",
        str(document),
        "--profile",
        str(documents["clock_profile"]),
        "-o",
        str(documents["output"]),
    ]


def _refusal(argv: list[str]) -> Refusal:
    """Run one command far enough to catch the staged refusal it raises."""
    args = build_parser().parse_args(argv)
    with pytest.raises(Refusal) as caught:
        args.handler(args)
    return caught.value


VERBS = (
    pytest.param(_clock_argv, id="clock"),
    pytest.param(_span_argv, id="span"),
)


@pytest.mark.parametrize("argv", VERBS)
def test_profile_refuses_utf16_at_the_encoding_stage(
    documents: dict[str, Path],
    tmp_path: Path,
    argv: ProfileArgv,
) -> None:
    """A byte-order-marked profile refuses where the same bytes refuse as a graph."""
    source = documents["clock_profile" if argv is _clock_argv else "span_profile"]
    encoded = tmp_path / "utf16_profile.json"
    encoded.write_bytes(source.read_text(encoding="utf-8").encode("utf-16"))
    refusal = _refusal(argv(documents, encoded))
    assert refusal.stage is RefusalStage.ENCODING
    assert str(refusal) == "parse UTF-8 failed: invalid start byte"


@pytest.mark.parametrize("argv", VERBS)
def test_profile_refuses_an_oversized_document_at_the_envelope_stage(
    documents: dict[str, Path],
    tmp_path: Path,
    argv: ProfileArgv,
) -> None:
    """The byte bound applies to a profile, not only to a graph document."""
    oversized = tmp_path / "oversized_profile.json"
    padding = "x" * (tiergraph.MAX_DOCUMENT_BYTES + 16)
    oversized.write_text(f'{{"pad":"{padding}"}}', encoding="utf-8")
    refusal = _refusal(argv(documents, oversized))
    assert refusal.stage is RefusalStage.ENVELOPE
    assert str(refusal).startswith("document size ")
    assert str(refusal).endswith(f"exceeds limit {tiergraph.MAX_DOCUMENT_BYTES}")


@pytest.mark.parametrize("argv", VERBS)
@pytest.mark.parametrize("depth", (tiergraph.MAX_JSON_DEPTH + 1, 20_000))
def test_profile_refuses_deep_nesting_at_the_syntax_stage(
    documents: dict[str, Path],
    tmp_path: Path,
    argv: ProfileArgv,
    depth: int,
) -> None:
    """Nesting past the bound is a staged refusal, whether or not it would recurse."""
    nested = tmp_path / "nested_profile.json"
    nested.write_text("[" * depth + "]" * depth, encoding="utf-8")
    refusal = _refusal(argv(documents, nested))
    assert refusal.stage is RefusalStage.SYNTAX
    assert (
        str(refusal) == f"JSON nesting depth exceeds limit {tiergraph.MAX_JSON_DEPTH}"
    )


@pytest.mark.parametrize("argv", VERBS)
def test_profile_refuses_a_duplicate_object_key_at_the_syntax_stage(
    documents: dict[str, Path],
    tmp_path: Path,
    argv: ProfileArgv,
) -> None:
    """The duplicate-key condition the graph reader answers governs a profile too."""
    repeated = tmp_path / "repeated_profile.json"
    repeated.write_text('{"clock_tier": 1, "clock_tier": 2}', encoding="utf-8")
    refusal = _refusal(argv(documents, repeated))
    assert refusal.stage is RefusalStage.SYNTAX
    assert str(refusal) == "parse JSON failed: duplicate object key 'clock_tier'"


@pytest.mark.parametrize(
    "name",
    ("utf16", "oversized", "nested", "repeated"),
)
def test_a_profile_and_a_graph_refuse_the_same_text_at_the_same_stage(
    documents: dict[str, Path], tmp_path: Path, name: str
) -> None:
    """Every condition a profile now answers is the one the graph reader answers."""
    candidate = tmp_path / f"{name}.json"
    if name == "utf16":
        candidate.write_bytes(
            documents["clock_profile"].read_text(encoding="utf-8").encode("utf-16")
        )
    elif name == "oversized":
        padding = "x" * (tiergraph.MAX_DOCUMENT_BYTES + 16)
        candidate.write_text(f'{{"pad":"{padding}"}}', encoding="utf-8")
    elif name == "nested":
        depth = tiergraph.MAX_JSON_DEPTH + 1
        candidate.write_text("[" * depth + "]" * depth, encoding="utf-8")
    else:
        candidate.write_text('{"graph": 1, "graph": 2}', encoding="utf-8")
    as_profile = _refusal(_clock_argv(documents, candidate))
    as_graph = _refusal(_graph_argv(documents, candidate))
    assert as_profile.stage is as_graph.stage
    assert str(as_profile) == str(as_graph)


@pytest.mark.parametrize("argv", VERBS)
def test_a_refused_profile_reports_a_staged_diagnostic_and_writes_nothing(
    documents: dict[str, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    argv: ProfileArgv,
) -> None:
    """The command line surfaces the refusal instead of the JSON library's error."""
    nested = tmp_path / "nested_profile.json"
    nested.write_text("[" * 20_000 + "]" * 20_000, encoding="utf-8")
    assert main(argv(documents, nested)) == 1
    message = capsys.readouterr().err.strip()
    assert message.endswith(
        f"ValueError: JSON nesting depth exceeds limit {tiergraph.MAX_JSON_DEPTH}"
    )
    assert "recursion" not in message
    assert not documents["output"].exists()


@pytest.mark.parametrize("argv", VERBS)
def test_a_utf8_profile_still_reads(
    documents: dict[str, Path],
    argv: ProfileArgv,
) -> None:
    """The ordinary encoding is unchanged by the added conditions."""
    profile = documents["clock_profile" if argv is _clock_argv else "span_profile"]
    assert main(argv(documents, profile)) == 0
    assert documents["output"].read_text(encoding="utf-8")


def test_an_unencodable_stdout_remains_an_exit_three_encode_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reading a profile as a document leaves the encode arm one live witness.

    Every reader this shell calls now answers a decoding failure as a staged
    refusal, so no input reaches the ``UnicodeError`` arm of ``main`` any more.
    What still reaches it is an output the terminal cannot represent: the
    interactive stepper writes a graph to ``sys.stdout`` as text, and a stream
    that cannot encode the graph's own strings raises there. This pins that
    remaining path so the arm is not held open by a substitute.
    """
    source = tmp_path / "program.jsonl"
    records = [
        {"machine_version": tiergraph.MACHINE_VERSION},
        tiergraph.DeclareNamespace(
            tiergraph.NamespaceDeclaration("r", "urn:é")
        ).to_data(),
    ]
    source.write_bytes(b"\n".join(json.dumps(record).encode() for record in records))
    stream = io.TextIOWrapper(io.BytesIO(), encoding="ascii", newline="")
    diagnostics = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO("next\nprint\nquit\n"))
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", diagnostics)
    assert main(["step", str(source), "--interactive"]) == 3
    assert "UnicodeEncodeError" in diagnostics.getvalue()
