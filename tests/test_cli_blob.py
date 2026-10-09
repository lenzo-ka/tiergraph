"""Exercise the external-resource and bundle command groups."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import BinaryIO, cast

import pytest

import tiergraph
import tiergraph.cli as cli
from tests.test_bundle import PAYLOAD_A, PAYLOAD_B, REF_A, REF_B, graph_and_rows
from tiergraph.cli import main


def _inputs(tmp_path: Path) -> tuple[Path, Path]:
    """Write the two-resource graph and populate its explicit directory store."""
    graph_path = tmp_path / "graph.json"
    graph_path.write_bytes(graph_and_rows()[0])
    store = tmp_path / "store"
    for name, payload in (("a.bin", PAYLOAD_A), ("b.bin", PAYLOAD_B)):
        source = tmp_path / name
        source.write_bytes(payload)
        assert main(["blob", "put", str(source), "--store", str(store)]) == 0
    return graph_path, store


def _assets(path: Path, capsys: pytest.CaptureFixture[str]) -> list[dict[str, object]]:
    """Return the ordered JSON asset rows reported for one bundle."""
    assert main(["bundle", "inspect", str(path), "--json"]) == 0
    return cast(list[dict[str, object]], json.loads(capsys.readouterr().out)["assets"])


def test_blob_help_profile_listing_and_put(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The new help, cheap profile check, listing, and put report are complete."""
    for command in (
        ("blob", "list"),
        ("blob", "put"),
        ("blob", "get"),
        ("blob", "verify"),
        ("bundle", "flatten"),
        ("bundle", "unflatten"),
        ("bundle", "inspect"),
    ):
        with pytest.raises(SystemExit, match="0"):
            main([*command, "--help"])
        help_text = capsys.readouterr().out
        assert f"tiergraph {' '.join(command)}" in help_text
        assert "Exit codes:" in help_text

    graph_path, store = _inputs(tmp_path)
    put_output = capsys.readouterr().out
    assert put_output.count(REF_A.sha256) == 2
    assert put_output.count(REF_B.sha256) == 2
    source = tmp_path / "typed.bin"
    source.write_bytes(PAYLOAD_A)
    assert (
        main(
            [
                "blob",
                "put",
                str(source),
                "--store",
                str(store),
                "--media-type",
                "audio/wav",
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report == {
        "href": f"sha256/{REF_A.sha256}",
        "media_type": "audio/wav",
        "sha256": REF_A.sha256,
        "size": REF_A.size,
    }
    with pytest.raises(SystemExit, match="2"):
        main(
            [
                "blob",
                "put",
                str(source),
                "--store",
                str(store),
                "--media-type",
                "Audio/WAV; codecs=pcm",
            ]
        )
    assert "lowercase type/subtype" in capsys.readouterr().err

    assert main(["validate", str(graph_path), "--profile", "blob"]) == 0
    assert capsys.readouterr().out == "ok\n"
    empty = tmp_path / "empty.json"
    empty.write_bytes(tiergraph.dump_bytes(tiergraph.Graph((), (), ())))
    assert main(["validate", str(empty), "--profile", "blob"]) == 1
    assert "blob vocabulary" in capsys.readouterr().err

    assert main(["blob", "list", str(graph_path), "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)["blobs"]
    assert [row["id"] for row in rows] == ["embedded-asset", "linked-asset"]
    assert [row["position"] for row in rows] == [0, 1]
    assert [row["mode"] for row in rows] == ["linked", "linked"]
    assert main(["blob", "list", str(graph_path)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith(f"0\tembedded-asset\t{REF_A.sha256}\t")
    assert lines[1].endswith("\tlinked\t-")


def test_blob_get_verify_and_refusals(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retrieval and verification use only explicit stores and publish verified bytes."""
    graph_path, store = _inputs(tmp_path)
    capsys.readouterr()
    assert main(["blob", "verify", str(graph_path), "--store", str(store)]) == 0
    assert capsys.readouterr().out == "ok\n"

    output = tmp_path / "payload.bin"
    assert (
        main(
            [
                "blob",
                "get",
                str(graph_path),
                REF_A.sha256,
                "--store",
                str(store),
                "-o",
                str(output),
            ]
        )
        == 0
    )
    assert output.read_bytes() == PAYLOAD_A
    assert main(["blob", "verify", str(graph_path)]) == 1
    assert "requires --store" in capsys.readouterr().err
    absent_store = tmp_path / "absent"
    assert (
        main(
            [
                "blob",
                "get",
                str(graph_path),
                REF_A.sha256,
                "--store",
                str(absent_store),
                "-o",
                str(tmp_path / "missing"),
            ]
        )
        == 1
    )
    assert "was not found" in capsys.readouterr().err
    for digest, message in (("bad", "must be 64"), ("00" * 32, "does not declare")):
        assert (
            main(
                [
                    "blob",
                    "get",
                    str(graph_path),
                    digest,
                    "--store",
                    str(store),
                    "-o",
                    str(tmp_path / digest[:3]),
                ]
            )
            == 1
        )
        assert message in capsys.readouterr().err

    monkeypatch.setattr(cli, "_copy_binary", lambda source, destination: None)
    assert (
        main(
            [
                "blob",
                "get",
                str(graph_path),
                REF_A.sha256,
                "--store",
                str(store),
                "-o",
                str(tmp_path / "partial"),
            ]
        )
        == 1
    )
    assert "not fully verified" in capsys.readouterr().err
    assert main(["blob", "verify", str(graph_path), "--store", str(store)]) == 1
    assert "not fully verified" in capsys.readouterr().err


def test_payload_outputs_refuse_stdout_before_publication(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verified payload and bundle outputs require retractable file destinations."""
    graph_path, store = _inputs(tmp_path)
    capsys.readouterr()
    for arguments in (
        [
            "blob",
            "get",
            str(graph_path),
            REF_A.sha256,
            "--store",
            str(store),
            "-o",
            "-",
        ],
        [
            "bundle",
            "flatten",
            str(graph_path),
            "--store",
            str(store),
            "-o",
            "-",
        ],
    ):
        with pytest.raises(SystemExit, match="2"):
            main(arguments)
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "must be a file path" in captured.err


def test_blob_verify_lists_every_missing_payload(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verification reports every unavailable digest in declared order."""
    graph_path = tmp_path / "graph.json"
    graph_path.write_bytes(graph_and_rows()[0])
    assert main(["blob", "verify", str(graph_path), "--store", str(tmp_path)]) == 1
    error = capsys.readouterr().err
    assert error.index(REF_A.sha256) < error.index(REF_B.sha256)
    assert error.count("was not found") == 2


def test_bundle_flatten_unflatten_and_inspect(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mixed residency moves preserve graph order, ids, links, and verified bytes."""
    graph_path, store = _inputs(tmp_path)
    capsys.readouterr()
    mixed = tmp_path / "mixed.tgb"
    assert (
        main(
            [
                "bundle",
                "flatten",
                str(graph_path),
                "--store",
                str(store),
                "--only",
                REF_A.sha256,
                "-o",
                str(mixed),
            ]
        )
        == 0
    )
    rows = _assets(mixed, capsys)
    assert [(row["id"], row["position"], row["mode"]) for row in rows] == [
        ("embedded-asset", 0, "embedded"),
        ("linked-asset", 1, "linked"),
    ]
    assert main(["bundle", "inspect", str(mixed)]) == 0
    assert capsys.readouterr().out.startswith("bundle version: 1\n")
    assert main(["blob", "list", str(mixed), "--json"]) == 0
    assert [row["mode"] for row in json.loads(capsys.readouterr().out)["blobs"]] == [
        "embedded",
        "linked",
    ]
    assert main(["blob", "verify", str(mixed), "--store", str(store)]) == 0
    assert capsys.readouterr().out == "ok\n"
    extracted = tmp_path / "embedded.bin"
    assert (
        main(
            [
                "blob",
                "get",
                str(mixed),
                REF_A.sha256,
                "-o",
                str(extracted),
            ]
        )
        == 0
    )
    assert extracted.read_bytes() == PAYLOAD_A

    mixed_flat = tmp_path / "mixed-flat.tgb"
    assert (
        main(
            [
                "bundle",
                "flatten",
                str(mixed),
                "--store",
                str(store),
                "-o",
                str(mixed_flat),
            ]
        )
        == 0
    )
    assert [row["mode"] for row in _assets(mixed_flat, capsys)] == [
        "embedded",
        "embedded",
    ]

    reversed_bundle = tmp_path / "reversed.tgb"
    assert (
        main(
            [
                "bundle",
                "flatten",
                str(mixed),
                "--store",
                str(store),
                "--only",
                REF_B.sha256,
                "-o",
                str(reversed_bundle),
            ]
        )
        == 0
    )
    assert [row["mode"] for row in _assets(reversed_bundle, capsys)] == [
        "linked",
        "embedded",
    ]
    linked = tmp_path / "linked.tgb"
    assert (
        main(
            [
                "bundle",
                "unflatten",
                str(reversed_bundle),
                "--store",
                str(store),
                "--only",
                REF_B.sha256,
                "-o",
                str(linked),
            ]
        )
        == 0
    )
    assert [row["mode"] for row in _assets(linked, capsys)] == ["linked", "linked"]
    flat = tmp_path / "flat.tgb"
    assert (
        main(
            [
                "bundle",
                "flatten",
                str(linked),
                "--store",
                str(store),
                "-o",
                str(flat),
            ]
        )
        == 0
    )
    assert [row["mode"] for row in _assets(flat, capsys)] == [
        "embedded",
        "embedded",
    ]
    all_linked = tmp_path / "all-linked.tgb"
    assert (
        main(
            [
                "bundle",
                "unflatten",
                str(flat),
                "--store",
                str(store),
                "-o",
                str(all_linked),
            ]
        )
        == 0
    )
    assert [row["mode"] for row in _assets(all_linked, capsys)] == [
        "linked",
        "linked",
    ]

    assert main(["bundle", "inspect", str(graph_path)]) == 1
    assert "requires a bundle" in capsys.readouterr().err
    assert (
        main(
            [
                "bundle",
                "unflatten",
                str(graph_path),
                "--store",
                str(store),
                "-o",
                str(tmp_path / "no.tgb"),
            ]
        )
        == 1
    )
    assert "requires a bundle" in capsys.readouterr().err
    for digest, message in (("bad", "must be 64"), ("00" * 32, "does not declare")):
        assert (
            main(
                [
                    "bundle",
                    "flatten",
                    str(graph_path),
                    "--store",
                    str(store),
                    "--only",
                    digest,
                    "-o",
                    str(tmp_path / f"{digest[:3]}.tgb"),
                ]
            )
            == 1
        )
        assert message in capsys.readouterr().err

    class NonconsumingSink:
        def put(self, ref: tiergraph.BlobRef, source: BinaryIO) -> str:
            del ref, source
            return "unused"

    sink_calls: list[tiergraph.BlobRef] = []

    def record_sink_call(
        sink: cli._DirectorySink, ref: tiergraph.BlobRef, source: BinaryIO
    ) -> str:
        del sink, source
        sink_calls.append(ref)
        return "unused"

    monkeypatch.setattr(cli._DirectorySink, "put", record_sink_call)
    assert (
        main(
            [
                "bundle",
                "flatten",
                str(mixed),
                "--store",
                str(store),
                "--only",
                REF_B.sha256,
                "-o",
                str(tmp_path),
            ]
        )
        == 1
    )
    assert "is a directory" in capsys.readouterr().err
    assert sink_calls == []

    with mixed.open("rb") as source, tiergraph.open_bundle(source) as bundle:
        calls: list[tiergraph.BlobRef] = []

        class RecordingSink:
            def put(self, ref: tiergraph.BlobRef, payload: BinaryIO) -> str:
                del payload
                calls.append(ref)
                return "unused"

        with pytest.raises(ValueError, match="seekable binary stream"):
            cli._flatten_bundle(
                bundle,
                cast(BinaryIO, NonseekableWriter()),
                str(store),
                {REF_B},
                cast(cli._DirectorySink, RecordingSink()),
            )
        assert calls == []

        with pytest.raises(ValueError, match="empty and positioned"):
            cli._flatten_bundle(
                bundle,
                io.BytesIO(b"occupied"),
                str(store),
                {REF_B},
                cast(cli._DirectorySink, RecordingSink()),
            )
        assert calls == []

        with pytest.raises(cli._MissingBlobError, match="requires --store"):
            cli._open_graph_blob(bundle, None, REF_B)
        with pytest.raises(cli._MissingBlobError, match="was not found"):
            cli._open_graph_blob(
                bundle, cli._DirectoryResolver(tmp_path / "absent"), REF_B
            )

        with pytest.raises(ValueError, match="not fully verified"):
            cli._flatten_bundle(
                bundle,
                io.BytesIO(),
                str(store),
                {REF_B},
                cast(cli._DirectorySink, NonconsumingSink()),
            )


class NonseekableWriter:
    """Collect writes without offering the rollback required by bundle output."""

    def writable(self) -> bool:
        """Report that writes would otherwise be accepted."""
        return True

    def seekable(self) -> bool:
        """Report that published bytes could not be retracted."""
        return False


def test_directory_store_refuses_unsafe_links_and_conflicts(tmp_path: Path) -> None:
    """Directory storage is bounded to its root and never replaces conflicting bytes."""
    resolver = cli._DirectoryResolver(tmp_path / "store")
    for href in (
        "custom:object",
        "//example.test/object",
        "object?query",
        "object#fragment",
        "object%2fchild",
        "object%FF",
        "object%ZZ",
        "object\\child",
        "object with space",
        "object\nrecord",
        "/absolute",
        "../escape",
        "./object",
    ):
        with pytest.raises(ValueError, match="safe relative path"):
            resolver.open(REF_A, href)
    encoded = tmp_path / "store" / "objects" / "my file"
    encoded.parent.mkdir(parents=True)
    encoded.write_bytes(PAYLOAD_A)
    source = resolver.open(REF_A, "objects/my%20file")
    assert source is not None
    with source:
        assert source.read() == PAYLOAD_A
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "store"
    root.mkdir(exist_ok=True)
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes its store"):
        resolver.open(REF_A, "link/payload")

    sink = cli._DirectorySink(root)
    wrong = tiergraph.BlobRef(REF_A.sha256, REF_A.size + 1)
    with pytest.raises(ValueError, match="identity mismatch"):
        sink.put(wrong, io.BytesIO(PAYLOAD_A))
    target = root / "sha256" / REF_A.sha256
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="contains different bytes"):
        sink.put(REF_A, io.BytesIO(PAYLOAD_A))

    class TextReader:
        def read(self, size: int) -> str:
            del size
            return "text"

    with pytest.raises(TypeError, match="must return bytes"):
        cli._copy_binary(cast(BinaryIO, TextReader()), io.BytesIO())

    class ShortWriter:
        def write(self, value: bytes) -> int:
            return len(value) - 1

    with pytest.raises(OSError, match="short write"):
        cli._copy_binary(io.BytesIO(b"x"), cast(BinaryIO, ShortWriter()))

    rendered = cli._blob_rows_text(
        [
            {
                "position": 0,
                "id": "asset",
                "sha256": REF_A.sha256,
                "size": REF_A.size,
                "media_type": "application/octet-stream",
                "schema": None,
                "mode": "linked",
                "href": "objects/payload\n1\tinjected",
            }
        ]
    )
    assert rendered.count("\n") == 1
    assert "objects/payload\\n1\\tinjected" in rendered


def test_put_reports_io_and_existing_store_conflicts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Put reuses equal content and reports a corrupt occupied digest path."""
    source = tmp_path / "payload"
    source.write_bytes(PAYLOAD_A)
    store = tmp_path / "store"
    arguments = ["blob", "put", str(source), "--store", str(store)]
    assert main(arguments) == 0
    assert json.loads(capsys.readouterr().out)["sha256"] == REF_A.sha256
    assert main(arguments) == 0
    capsys.readouterr()
    (store / "sha256" / REF_A.sha256).write_bytes(b"corrupt")
    assert main(arguments) == 1
    assert "contains different bytes" in capsys.readouterr().err
    missing = tmp_path / "missing"
    assert main(["blob", "put", str(missing), "--store", str(store)]) == 3
    assert "FileNotFoundError" in capsys.readouterr().err


def test_blob_input_detects_bundle_by_canonical_signature(tmp_path: Path) -> None:
    """A canonical bundle stays open for its borrowed archive lifetime."""
    graph = tiergraph.loads(graph_and_rows()[0])
    encoded = io.BytesIO()
    tiergraph.write_bundle(
        graph,
        encoded,
        tiergraph.MappingResolver({REF_A.sha256: PAYLOAD_A, REF_B.sha256: PAYLOAD_B}),
    )
    path = tmp_path / "bundle"
    path.write_bytes(encoded.getvalue())
    with cli._blob_input(str(path)) as (read_graph, bundle):
        assert read_graph == graph
        assert bundle is not None
        assert bundle.open_blob(REF_A).read() == PAYLOAD_A
    assert hashlib.sha256(PAYLOAD_A).hexdigest() == REF_A.sha256
