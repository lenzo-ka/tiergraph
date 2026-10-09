"""Exercise strict, lazy reading of versioned blob bundles."""

from __future__ import annotations

import hashlib
import io
import json
import struct
import subprocess
import sys
import zipfile
from collections.abc import Buffer, Callable
from dataclasses import replace
from pathlib import Path
from typing import BinaryIO, cast

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from scripts import generate_schema

import tiergraph.blob as blob_module
from tests.test_blob import BLOBS, base_editor, blob_item
from tiergraph import (
    BUNDLE_VERSION,
    BlobRef,
    Bundle,
    BundleAsset,
    BundleLimits,
    Graph,
    MappingResolver,
    bundle_json_schema,
    dump_bytes,
    embed_links,
    loads,
    open_bundle,
    relink,
    write_bundle,
)

PAYLOAD_A = b"ordered embedded payload"
PAYLOAD_B = b"linked payload"
REF_A = BlobRef(hashlib.sha256(PAYLOAD_A).hexdigest(), len(PAYLOAD_A))
REF_B = BlobRef(hashlib.sha256(PAYLOAD_B).hexdigest(), len(PAYLOAD_B))
CANONICAL_ATTR = 0o100644 << 16


def graph_and_rows() -> tuple[bytes, list[dict[str, object]]]:
    """Return one graph document and matching ordered bundle rows."""
    editor = base_editor()
    editor.insert_item(
        BLOBS,
        0,
        blob_item(
            "embedded-asset",
            REF_A.sha256,
            REF_A.size,
            "application/octet-stream",
            "urn:example:binary",
        ),
    )
    editor.insert_item(
        BLOBS,
        1,
        blob_item(
            "linked-asset",
            REF_B.sha256,
            REF_B.size,
            "application/octet-stream",
            "urn:example:binary",
        ),
    )
    graph_bytes = dump_bytes(editor.freeze())
    rows: list[dict[str, object]] = [
        {
            "id": "embedded-asset",
            "position": 0,
            "sha256": REF_A.sha256,
            "size": REF_A.size,
            "mode": "embedded",
        },
        {
            "id": "linked-asset",
            "position": 1,
            "sha256": REF_B.sha256,
            "size": REF_B.size,
            "mode": "linked",
            "href": "objects/linked",
        },
    ]
    return graph_bytes, rows


def index_data(graph_bytes: bytes, rows: list[dict[str, object]]) -> dict[str, object]:
    """Return a matching bundle index."""
    return {
        "bundle_version": BUNDLE_VERSION,
        "graph": {
            "path": "graph.json",
            "sha256": hashlib.sha256(graph_bytes).hexdigest(),
            "size": len(graph_bytes),
        },
        "assets": rows,
    }


def info(name: str) -> zipfile.ZipInfo:
    """Return canonical metadata for one stored bundle entry."""
    result = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
    result.create_system = 3
    result.external_attr = CANONICAL_ATTR
    result.compress_type = zipfile.ZIP_STORED
    return result


def replace_info(metadata: zipfile.ZipInfo, **changes: object) -> zipfile.ZipInfo:
    """Mutate selected ZipInfo fields for one intentionally invalid fixture."""
    for name, value in changes.items():
        setattr(metadata, name, value)
    return metadata


def bundle_bytes(
    *,
    index: dict[str, object] | bytes | None = None,
    graph_bytes: bytes | None = None,
    rows: list[dict[str, object]] | None = None,
    entries: list[tuple[zipfile.ZipInfo, bytes]] | None = None,
    archive_comment: bytes = b"",
) -> bytes:
    """Write a hand-assembled fixture using canonical entry metadata."""
    default_graph, default_rows = graph_and_rows()
    graph_bytes = default_graph if graph_bytes is None else graph_bytes
    rows = default_rows if rows is None else rows
    if index is None:
        index = index_data(graph_bytes, rows)
    index_bytes = (
        index
        if isinstance(index, bytes)
        else json.dumps(index, separators=(",", ":"), ensure_ascii=False).encode()
    )
    if entries is None:
        entries = [
            (info("bundle.json"), index_bytes),
            (info("graph.json"), graph_bytes),
            (info("blobs/sha256/" + REF_A.sha256), PAYLOAD_A),
        ]
    destination = io.BytesIO()
    with zipfile.ZipFile(destination, "w") as archive:
        archive.comment = archive_comment
        for metadata, data in entries:
            archive.writestr(metadata, data)
    return destination.getvalue()


class TrackingSource(io.BytesIO):
    """Record every physical byte interval read from a bundle source."""

    def __init__(self, data: bytes) -> None:
        """Initialize a seekable source with an empty read log."""
        super().__init__(data)
        self.intervals: list[tuple[int, int]] = []

    def read(self, size: int | None = -1) -> bytes:
        """Read normally while retaining the physical interval."""
        start = self.tell()
        data = super().read(size)
        self.intervals.append((start, start + len(data)))
        return data


class RecordingResolver:
    """Resolve test payloads while recording ordered href-aware requests."""

    def __init__(self, payloads: dict[str, bytes]) -> None:
        """Retain payloads and initialize an empty request log."""
        self.payloads = payloads
        self.calls: list[tuple[BlobRef, str | None]] = []

    def open(self, ref: BlobRef, href: str | None) -> BinaryIO | None:
        """Return one fresh reader when the requested digest is available."""
        self.calls.append((ref, href))
        payload = self.payloads.get(ref.sha256)
        return None if payload is None else io.BytesIO(payload)


class RecordingSink:
    """Store complete test payloads and return stable content-addressed hrefs."""

    def __init__(self, hrefs: dict[BlobRef, str]) -> None:
        """Retain returned hrefs and initialize an ordered payload log."""
        self.hrefs = hrefs
        self.payloads: list[tuple[BlobRef, bytes]] = []

    def put(self, ref: BlobRef, source: BinaryIO) -> str | None:
        """Consume and retain one complete payload before returning its href."""
        self.payloads.append((ref, source.read()))
        return self.hrefs[ref]


def mutate_eocd(data: bytes, field: int, value: int) -> bytes:
    """Replace one unsigned EOCD field in a comment-free fixture."""
    changed = bytearray(data)
    offset = len(changed) - 22
    values = list(struct.unpack_from("<4s4H2LH", changed, offset))
    values[field] = value
    struct.pack_into("<4s4H2LH", changed, offset, *values)
    return bytes(changed)


def zip64_end_record(
    *,
    signature: bytes = b"PK\x06\x06",
    record_size: int = 44,
    disk: int = 0,
    central_disk: int = 0,
    disk_count: int | None = None,
    count: int = 0xFFFF,
    size: int = 0,
    classic_size: int | None = None,
) -> bytes:
    """Return minimal ZIP64 end structures without an entry directory."""
    disk_count = count if disk_count is None else disk_count
    classic_size = size if classic_size is None else classic_size
    record = struct.pack(
        "<4sQ2H2L4Q",
        signature,
        record_size,
        45,
        45,
        disk,
        central_disk,
        disk_count,
        count,
        size,
        0,
    )
    locator = struct.pack("<4sLQL", b"PK\x06\x07", 0, 0, 1)
    end = struct.pack(
        "<4s4H2LH",
        b"PK\x05\x06",
        0,
        0,
        0xFFFF,
        0xFFFF,
        classic_size,
        0,
        0,
    )
    return record + locator + end


def test_bundle_schema_is_published_and_closed() -> None:
    """The public schema validates both modes and rejects unknown fields."""
    graph_bytes, rows = graph_and_rows()
    schema = bundle_json_schema()
    validator = Draft202012Validator(schema)
    document = index_data(graph_bytes, rows)
    assert BUNDLE_VERSION == "1"
    assert validator.is_valid(document)
    cast(dict[str, object], document["graph"])["unknown"] = None
    assert not validator.is_valid(document)
    assert generate_schema.generated_bundle_bytes() == (
        generate_schema.BUNDLE_SCHEMA_PATH.read_bytes()
    )


def test_schema_check_names_a_stale_bundle_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shared schema generator checks the committed bundle artifact too."""
    path = tmp_path / "tiergraph.bundle.schema.json"
    path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(generate_schema, "BUNDLE_SCHEMA_PATH", path)
    baseline = json.loads(
        generate_schema.stamp_bytes(generate_schema.generated_bytes())
    )
    with pytest.raises(SystemExit, match="is stale; regenerate it"):
        generate_schema.main(["--check"], baseline)


def test_open_bundle_is_lazy_and_preserves_order_and_identity() -> None:
    """Opening reads metadata only and returns asset rows in graph order."""
    encoded = bundle_bytes()
    with zipfile.ZipFile(io.BytesIO(encoded)) as archive:
        payload_info = archive.getinfo("blobs/sha256/" + REF_A.sha256)
        payload_start = (
            payload_info.header_offset
            + 30
            + len(payload_info.filename.encode())
            + len(payload_info.extra)
        )
    source = TrackingSource(encoded)
    bundle = open_bundle(source)
    assert bundle.assets() == (
        BundleAsset("embedded-asset", 0, REF_A, "embedded", None),
        BundleAsset("linked-asset", 1, REF_B, "linked", "objects/linked"),
    )
    assert all(
        end <= payload_start or start >= payload_start + REF_A.size
        for start, end in source.intervals
    )
    reader = bundle.open_blob(REF_A)
    assert reader.read(5) == PAYLOAD_A[:5]
    assert reader.verified is False
    reader.seek(0)
    assert reader.read() == PAYLOAD_A
    assert reader.verified is False
    reader.close()
    with bundle.open_blob(REF_A) as complete:
        assert complete.read() == PAYLOAD_A
        assert complete.verified is True
    bundle.close()
    bundle.close()
    with pytest.raises(ValueError, match="closed"):
        bundle.open_blob(REF_A)
    with pytest.raises(ValueError, match="closed"):
        bundle.__enter__()
    with pytest.raises(TypeError, match="created by open_bundle"):
        Bundle()


def test_same_payload_rows_preserve_distinct_ids_and_positions() -> None:
    """Equal payloads remain distinct bundle rows with durable graph identity."""
    editor = base_editor()
    for position, durable_id in enumerate(("one", "two")):
        editor.insert_item(
            BLOBS,
            position,
            blob_item(
                durable_id,
                REF_A.sha256,
                REF_A.size,
                "application/octet-stream",
                "urn:example:binary",
            ),
        )
    graph_bytes = dump_bytes(editor.freeze())
    rows = [
        {
            "id": durable_id,
            "position": position,
            "sha256": REF_A.sha256,
            "size": REF_A.size,
            "mode": "embedded",
        }
        for position, durable_id in enumerate(("one", "two"))
    ]
    with open_bundle(
        io.BytesIO(bundle_bytes(graph_bytes=graph_bytes, rows=rows))
    ) as bundle:
        assert bundle.assets() == (
            BundleAsset("one", 0, REF_A, "embedded", None),
            BundleAsset("two", 1, REF_A, "embedded", None),
        )


def test_write_bundle_is_deterministic_and_selects_each_payload_once() -> None:
    """Writing preserves graph bytes, row order, choices, hrefs, and entry order."""
    graph_bytes, _ = graph_and_rows()
    graph = loads(graph_bytes)
    calls: list[BlobRef] = []

    def choose(ref: BlobRef) -> bool:
        calls.append(ref)
        return ref == REF_A

    hrefs = {REF_A: "objects/embedded-source", REF_B: "objects/linked"}
    resolver = RecordingResolver({REF_A.sha256: PAYLOAD_A, REF_B.sha256: PAYLOAD_B})
    first = io.BytesIO()
    write_bundle(graph, first, resolver, embed=choose, hrefs=hrefs)
    assert calls == [REF_A, REF_B]
    assert resolver.calls == [(REF_A, "objects/embedded-source")]

    second = io.BytesIO()
    write_bundle(
        graph,
        second,
        MappingResolver({REF_A.sha256: PAYLOAD_A}),
        embed=lambda ref: ref == REF_A,
        hrefs=hrefs,
    )
    assert first.getvalue() == second.getvalue()
    with zipfile.ZipFile(io.BytesIO(first.getvalue())) as archive:
        assert archive.namelist() == [
            "bundle.json",
            "graph.json",
            "blobs/sha256/" + REF_A.sha256,
        ]
        assert archive.read("graph.json") == graph_bytes
    with open_bundle(io.BytesIO(first.getvalue())) as bundle:
        assert bundle.assets() == (
            BundleAsset("embedded-asset", 0, REF_A, "embedded", None),
            BundleAsset("linked-asset", 1, REF_B, "linked", "objects/linked"),
        )
        with bundle.open_blob(REF_A) as reader:
            assert reader.read() == PAYLOAD_A
            assert reader.verified


def test_write_bundle_supports_all_linked_and_blobless_graphs() -> None:
    """Reference bundles need no payload reads, including an empty inventory."""
    graph_bytes, _ = graph_and_rows()
    resolver = RecordingResolver({})
    linked = io.BytesIO()
    write_bundle(
        loads(graph_bytes),
        linked,
        resolver,
        embed=False,
        hrefs={REF_A: "a", REF_B: "b"},
    )
    assert resolver.calls == []
    with open_bundle(io.BytesIO(linked.getvalue())) as bundle:
        assert [asset.mode for asset in bundle.assets()] == ["linked", "linked"]
        assert [asset.href for asset in bundle.assets()] == ["a", "b"]

    empty = io.BytesIO()
    write_bundle(Graph((), (), ()), empty, resolver)
    with open_bundle(io.BytesIO(empty.getvalue())) as bundle:
        assert bundle.assets() == ()


def test_embed_links_and_relink_are_byte_exact_inverse_moves() -> None:
    """Moving every payload out and back preserves the deterministic container."""
    graph_bytes, _ = graph_and_rows()
    graph = loads(graph_bytes)
    payloads = {REF_A.sha256: PAYLOAD_A, REF_B.sha256: PAYLOAD_B}
    hrefs = {REF_A: "objects/a", REF_B: "objects/b"}
    linked = io.BytesIO()
    write_bundle(graph, linked, MappingResolver(payloads), embed=False, hrefs=hrefs)

    with open_bundle(io.BytesIO(linked.getvalue())) as source:
        flattened = io.BytesIO()
        resolver = RecordingResolver(payloads)
        embed_links(source, flattened, resolver)
        assert resolver.calls == [(REF_A, "objects/a"), (REF_B, "objects/b")]
    with open_bundle(io.BytesIO(flattened.getvalue())) as source:
        assert [asset.mode for asset in source.assets()] == ["embedded", "embedded"]
        sink = RecordingSink(hrefs)
        restored = io.BytesIO()
        relink(source, restored, sink)
        assert sink.payloads == [(REF_A, PAYLOAD_A), (REF_B, PAYLOAD_B)]
    assert restored.getvalue() == linked.getvalue()


def test_partial_moves_preserve_unselected_rows_and_embedded_bytes() -> None:
    """Predicates change only selected modes and keep all rows in declared order."""
    graph_bytes, _ = graph_and_rows()
    graph = loads(graph_bytes)
    payloads = {REF_A.sha256: PAYLOAD_A, REF_B.sha256: PAYLOAD_B}
    mixed = io.BytesIO()
    write_bundle(
        graph,
        mixed,
        MappingResolver(payloads),
        embed=lambda ref: ref == REF_A,
        hrefs={REF_B: "objects/b"},
    )
    with open_bundle(io.BytesIO(mixed.getvalue())) as source:
        unchanged = io.BytesIO()
        embed_links(source, unchanged, MappingResolver(payloads), which=False)
        assert unchanged.getvalue() == mixed.getvalue()
    with open_bundle(io.BytesIO(mixed.getvalue())) as source:
        flattened = io.BytesIO()
        embed_links(
            source,
            flattened,
            MappingResolver(payloads),
            which=lambda ref: ref == REF_B,
        )
    with open_bundle(io.BytesIO(flattened.getvalue())) as source:
        partly_linked = io.BytesIO()
        sink = RecordingSink({REF_A: "objects/a", REF_B: "objects/b"})
        relink(source, partly_linked, sink, which=lambda ref: ref == REF_A)
        assert sink.payloads == [(REF_A, PAYLOAD_A)]
    with open_bundle(io.BytesIO(partly_linked.getvalue())) as result:
        assert result.assets() == (
            BundleAsset("embedded-asset", 0, REF_A, "linked", "objects/a"),
            BundleAsset("linked-asset", 1, REF_B, "embedded", None),
        )


def test_write_failures_roll_back_the_destination() -> None:
    """Missing, truncated, changed, and extra payload bytes publish no archive."""
    graph_bytes, _ = graph_and_rows()
    graph = loads(graph_bytes)
    failures = [
        ({}, "was not found"),
        ({REF_A.sha256: PAYLOAD_A[:-1]}, "size mismatch"),
        ({REF_A.sha256: b"x" * len(PAYLOAD_A)}, "SHA-256 mismatch"),
        ({REF_A.sha256: PAYLOAD_A + b"x"}, "exceeds its declared size"),
    ]
    for payloads, message in failures:
        destination = io.BytesIO()
        with pytest.raises(ValueError, match=message):
            write_bundle(
                graph,
                destination,
                MappingResolver(payloads),
                embed=lambda ref: ref == REF_A,
            )
        assert destination.getvalue() == b""
        assert destination.tell() == 0


def test_move_failures_refuse_incomplete_sinks_and_missing_links() -> None:
    """Moves verify both sink consumption and resolved bytes before publishing."""
    graph_bytes, _ = graph_and_rows()
    graph = loads(graph_bytes)
    flat = io.BytesIO()
    write_bundle(
        graph,
        flat,
        MappingResolver({REF_A.sha256: PAYLOAD_A, REF_B.sha256: PAYLOAD_B}),
    )

    class PartialSink:
        def put(self, ref: BlobRef, source: BinaryIO) -> str | None:
            del ref
            source.read(1)
            return None

    with open_bundle(io.BytesIO(flat.getvalue())) as source:
        destination = io.BytesIO()
        with pytest.raises(ValueError, match="did not consume"):
            relink(source, destination, PartialSink())
        assert destination.getvalue() == b""

    linked = io.BytesIO()
    write_bundle(graph, linked, MappingResolver({}), embed=False)
    with open_bundle(io.BytesIO(linked.getvalue())) as source:
        destination = io.BytesIO()
        with pytest.raises(ValueError, match="was not found"):
            embed_links(source, destination, MappingResolver({}))
        assert destination.getvalue() == b""


def test_relink_preflights_destination_and_selection_before_sink_writes() -> None:
    """Locally detectable move refusals occur before external sink writes."""
    graph_bytes, _ = graph_and_rows()
    flat = io.BytesIO()
    write_bundle(
        loads(graph_bytes),
        flat,
        MappingResolver({REF_A.sha256: PAYLOAD_A, REF_B.sha256: PAYLOAD_B}),
    )
    sink = RecordingSink({REF_A: "objects/a", REF_B: "objects/b"})
    with open_bundle(io.BytesIO(flat.getvalue())) as source:
        with pytest.raises(ValueError, match="empty and positioned"):
            relink(source, io.BytesIO(b"occupied"), sink)
    assert sink.payloads == []

    def refuse_second(ref: BlobRef) -> bool:
        if ref == REF_B:
            raise ValueError("selection refusal")
        return True

    with open_bundle(io.BytesIO(flat.getvalue())) as source:
        with pytest.raises(ValueError, match="selection refusal"):
            relink(source, io.BytesIO(), sink, which=refuse_second)
    assert sink.payloads == []


def test_relink_accepts_a_sink_that_closes_a_verified_stream() -> None:
    """A sink may close a source after one complete verified read."""
    graph_bytes, _ = graph_and_rows()
    flat = io.BytesIO()
    write_bundle(
        loads(graph_bytes),
        flat,
        MappingResolver({REF_A.sha256: PAYLOAD_A, REF_B.sha256: PAYLOAD_B}),
    )

    class ClosingSink:
        def put(self, ref: BlobRef, source: BinaryIO) -> str | None:
            payload = source.read()
            source.close()
            assert payload in {PAYLOAD_A, PAYLOAD_B}
            return "objects/" + ref.sha256

    with open_bundle(io.BytesIO(flat.getvalue())) as source:
        linked = io.BytesIO()
        relink(source, linked, ClosingSink())
    with open_bundle(io.BytesIO(linked.getvalue())) as result:
        assert [asset.mode for asset in result.assets()] == ["linked", "linked"]


def test_bundle_write_public_argument_refusals() -> None:
    """Writers reject ill-typed choices, hrefs, sinks, and destinations."""
    graph_bytes, _ = graph_and_rows()
    graph = loads(graph_bytes)
    resolver = MappingResolver({REF_A.sha256: PAYLOAD_A, REF_B.sha256: PAYLOAD_B})
    with pytest.raises(TypeError, match="boolean or callable"):
        write_bundle(graph, io.BytesIO(), resolver, embed=cast(bool, 1))
    with pytest.raises(TypeError, match="return a boolean"):
        write_bundle(graph, io.BytesIO(), resolver, embed=lambda ref: cast(bool, 1))
    with pytest.raises(TypeError, match="href.*string"):
        write_bundle(
            graph,
            io.BytesIO(),
            resolver,
            embed=False,
            hrefs=cast(dict[BlobRef, str], {REF_A: 1}),
        )
    for destination in (io.BytesIO(b"occupied"), io.BytesIO(b"x")):
        if destination.getvalue() == b"x":
            destination.seek(1)
        with pytest.raises(ValueError, match="empty and positioned"):
            write_bundle(graph, destination, resolver, embed=False)

    class NotSeekable(io.BytesIO):
        def seekable(self) -> bool:
            return False

    with pytest.raises(ValueError, match="seekable binary stream"):
        write_bundle(graph, cast(BinaryIO, NotSeekable()), resolver, embed=False)

    class InvalidSink:
        def put(self, ref: BlobRef, source: BinaryIO) -> str | None:
            del ref
            source.read()
            return cast(str, 1)

    flat = io.BytesIO()
    write_bundle(graph, flat, resolver)
    with open_bundle(io.BytesIO(flat.getvalue())) as source:
        with pytest.raises(TypeError, match="sink href.*string"):
            relink(source, io.BytesIO(), InvalidSink())


def test_write_selection_is_memoized_for_shared_payloads() -> None:
    """Equal payload rows share one decision, source read, and ZIP entry."""
    editor = base_editor()
    for position, durable_id in enumerate(("one", "two")):
        editor.insert_item(
            BLOBS,
            position,
            blob_item(
                durable_id,
                REF_A.sha256,
                REF_A.size,
                "application/octet-stream",
                "urn:example:binary",
            ),
        )
    calls: list[BlobRef] = []
    resolver = RecordingResolver({REF_A.sha256: PAYLOAD_A})
    destination = io.BytesIO()

    def choose(ref: BlobRef) -> bool:
        calls.append(ref)
        return True

    write_bundle(
        editor.freeze(),
        destination,
        resolver,
        embed=choose,
    )
    assert calls == [REF_A]
    assert resolver.calls == [(REF_A, None)]
    with open_bundle(io.BytesIO(destination.getvalue())) as bundle:
        assert [asset.id for asset in bundle.assets()] == ["one", "two"]


def test_sink_must_leave_a_verifiable_stream_at_eof() -> None:
    """A sink that seeks cannot make its complete read count as verification."""
    graph_bytes, _ = graph_and_rows()
    flat = io.BytesIO()
    write_bundle(
        loads(graph_bytes),
        flat,
        MappingResolver({REF_A.sha256: PAYLOAD_A, REF_B.sha256: PAYLOAD_B}),
    )

    class SeekingSink:
        def put(self, ref: BlobRef, source: BinaryIO) -> str | None:
            del ref
            source.seek(0)
            source.read()
            return None

    with open_bundle(io.BytesIO(flat.getvalue())) as source:
        with pytest.raises(ValueError, match="did not consume"):
            relink(source, io.BytesIO(), SeekingSink())


def test_write_limits_refuse_before_publishing() -> None:
    """Count, metadata, entry, and total limits are enforced before output."""
    graph_bytes, _ = graph_and_rows()
    graph = loads(graph_bytes)
    resolver = MappingResolver({REF_A.sha256: PAYLOAD_A, REF_B.sha256: PAYLOAD_B})
    limits = [
        BundleLimits(max_entries=2),
        BundleLimits(max_index_bytes=1),
        BundleLimits(max_graph_bytes=1),
        BundleLimits(max_entry_bytes=1),
        BundleLimits(max_total_bytes=1),
        BundleLimits(max_central_directory_bytes=1),
    ]
    for limit in limits:
        destination = io.BytesIO()
        with pytest.raises(ValueError, match="bundle limit"):
            write_bundle(graph, destination, resolver, limits=limit)
        assert destination.getvalue() == b""


def test_central_directory_limit_precedes_payload_reads_and_writes() -> None:
    """Predictable directory limits refuse before payload and output I/O."""
    graph_bytes, _ = graph_and_rows()
    resolver = RecordingResolver({REF_A.sha256: PAYLOAD_A, REF_B.sha256: PAYLOAD_B})

    class RecordingDestination(io.BytesIO):
        def __init__(self) -> None:
            super().__init__()
            self.write_calls: list[int] = []

        def write(self, data: Buffer, /) -> int:
            self.write_calls.append(len(memoryview(data)))
            return super().write(data)

    destination = RecordingDestination()
    with pytest.raises(ValueError, match="central directory size"):
        write_bundle(
            loads(graph_bytes),
            destination,
            resolver,
            limits=BundleLimits(max_central_directory_bytes=1),
        )
    assert resolver.calls == []
    assert destination.write_calls == []
    assert destination.getvalue() == b""


def test_low_level_bundle_writer_defenses(monkeypatch: pytest.MonkeyPatch) -> None:
    """Binary writer helpers reject malformed streams and cover ZIP64 records."""

    class TextSource(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            del size
            return cast(bytes, "text")

    with pytest.raises(TypeError, match="did not return bytes"):
        blob_module._write_entry(io.BytesIO(), "entry", 0, TextSource())
    with pytest.raises(ValueError, match="size mismatch"):
        blob_module._write_entry(io.BytesIO(), "entry", 1, io.BytesIO())

    monkeypatch.setattr(blob_module, "_UINT32_MAX", 2)
    encoded = io.BytesIO()
    large = blob_module._write_entry(encoded, "large", 3, io.BytesIO(b"abc"))
    assert large.version == 45
    monkeypatch.setattr(blob_module, "_UINT16_MAX", 2)
    directory = io.BytesIO()
    blob_module._write_directory(
        directory,
        (
            large,
            blob_module._WrittenEntry("offset", 1, 0, 3, 45),
        ),
        BundleLimits(),
    )
    assert b"PK\x06\x06" in directory.getvalue()

    central_directory_size = blob_module._central_directory_size
    monkeypatch.setattr(
        blob_module,
        "_central_directory_size",
        lambda entries: central_directory_size(entries) + 1,
    )
    with pytest.raises(AssertionError, match="planned central directory"):
        blob_module._write_directory(
            io.BytesIO(),
            (blob_module._WrittenEntry("tiny", 1, 0, 0, 20),),
            BundleLimits(),
        )

    class BadTell(io.BytesIO):
        def tell(self) -> int:
            return cast(int, True)

    with pytest.raises(ValueError, match="nonnegative integer"):
        blob_module._stream_position(BadTell(), "position")

    class PartialWriter(io.BytesIO):
        def write(self, data: Buffer, /) -> int:
            return super().write(memoryview(data)[:1])

    partial = PartialWriter()
    blob_module._write_all(partial, b"abc", "partial")
    assert partial.getvalue() == b"abc"

    class StalledWriter(io.BytesIO):
        def write(self, data: Buffer, /) -> int:
            del data
            return 0

    with pytest.raises(ValueError, match="made no progress"):
        blob_module._write_all(StalledWriter(), b"x", "stalled")

    class LyingWriter(io.BytesIO):
        def write(self, data: Buffer, /) -> int:
            return len(memoryview(data)) + 1

    with pytest.raises(ValueError, match="too many bytes"):
        blob_module._write_all(LyingWriter(), b"x", "lying")


def test_destination_seek_and_rollback_failures_are_named() -> None:
    """Unmeasurable outputs and failed rollback expose stable diagnostics."""

    class BadSeek(io.BytesIO):
        def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
            del offset, whence
            raise OSError("no seek")

    with pytest.raises(ValueError, match="seekable binary stream"):
        blob_module._prepare_destination(BadSeek())
    with pytest.raises(ValueError, match="could not be rolled back"):
        blob_module._rollback_destination(BadSeek())


def test_plain_package_import_does_not_load_zipfile() -> None:
    """Bundle-only ZIP support has no import cost for ordinary graph users."""
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import tiergraph; assert 'zipfile' not in sys.modules",
        ],
        check=True,
    )


def test_linked_open_is_explicit_and_verified() -> None:
    """A linked row consults only its caller-supplied resolver and href."""
    with open_bundle(io.BytesIO(bundle_bytes())) as bundle:
        with pytest.raises(ValueError, match="requires a resolver"):
            bundle.open_blob(REF_B)
        with pytest.raises(ValueError, match="was not found"):
            bundle.open_blob(REF_B, MappingResolver({}))
        with bundle.open_blob(
            REF_B, MappingResolver({REF_B.sha256: PAYLOAD_B})
        ) as reader:
            assert reader.read() == PAYLOAD_B
            assert reader.verified
        with pytest.raises(ValueError, match="does not declare"):
            bundle.open_blob(BlobRef("00" * 32, 0))


def test_embedded_corruption_is_refused_at_eof() -> None:
    """Payload identity is checked only when its complete body is consumed."""
    graph_bytes, rows = graph_and_rows()
    index_bytes = json.dumps(
        index_data(graph_bytes, rows), separators=(",", ":")
    ).encode()
    wrong_payload = bytes([PAYLOAD_A[0] ^ 1]) + PAYLOAD_A[1:]
    encoded = bundle_bytes(
        rows=rows,
        graph_bytes=graph_bytes,
        entries=[
            (info("bundle.json"), index_bytes),
            (info("graph.json"), graph_bytes),
            (info("blobs/sha256/" + REF_A.sha256), wrong_payload),
        ],
    )
    with open_bundle(io.BytesIO(encoded)) as bundle:
        with bundle.open_blob(REF_A) as reader:
            with pytest.raises(ValueError, match="(size|SHA-256) mismatch"):
                reader.read()


def test_embedded_entry_size_must_match_its_asset_row() -> None:
    """Opening rejects central entry sizes that disagree with asset metadata."""
    graph_bytes, rows = graph_and_rows()
    index_bytes = json.dumps(
        index_data(graph_bytes, rows), separators=(",", ":")
    ).encode()
    encoded = bundle_bytes(
        entries=[
            (info("bundle.json"), index_bytes),
            (info("graph.json"), graph_bytes),
            (info("blobs/sha256/" + REF_A.sha256), b"short"),
        ]
    )
    with pytest.raises(ValueError, match="entry size does not match"):
        open_bundle(io.BytesIO(encoded))


def test_embedded_reader_wraps_a_closed_archive_diagnostic() -> None:
    """Failure to create an embedded entry reader is named as a bundle error."""
    bundle = open_bundle(io.BytesIO(bundle_bytes()))
    bundle._archive.close()
    with pytest.raises(ValueError, match="cannot open embedded"):
        bundle.open_blob(REF_A)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (1, 1, "single-disk"),
        (3, 99, "single-disk"),
        (4, 99, "single-disk"),
        (5, 1 << 30, "central directory size"),
        (6, 1 << 30, "outside the archive"),
    ],
)
def test_end_record_and_preallocation_limits_refuse(
    field: int, value: int, message: str
) -> None:
    """Hostile end-record declarations fail before ZIP parses its directory."""
    limits = BundleLimits(max_entries=10)
    with pytest.raises(ValueError, match=message):
        open_bundle(
            io.BytesIO(mutate_eocd(bundle_bytes(), field, value)), limits=limits
        )


@pytest.mark.parametrize(
    "limits",
    [
        BundleLimits(max_entries=2),
        BundleLimits(max_entry_bytes=1),
        BundleLimits(max_total_bytes=1),
        BundleLimits(max_index_bytes=1),
        BundleLimits(max_graph_bytes=1),
    ],
)
def test_declared_entry_limits_refuse_before_payload_allocation(
    limits: BundleLimits,
) -> None:
    """Per-entry, total, index, and graph bounds reject declared sizes."""
    with pytest.raises(ValueError, match="bundle limit"):
        open_bundle(io.BytesIO(bundle_bytes()), limits=limits)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("max_entries", 0),
        ("max_entry_bytes", -1),
        ("max_total_bytes", True),
        ("max_central_directory_bytes", 1.5),
        ("max_index_bytes", "large"),
        ("max_graph_bytes", None),
    ],
)
def test_bundle_limits_require_positive_integers(name: str, value: object) -> None:
    """Every public limit rejects booleans, nonintegers, and nonpositive values."""
    with pytest.raises(ValueError, match="bundle limit"):
        BundleLimits(**{name: value})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda index: index.update(bundle_version="2"), "version"),
        (lambda index: index.update(unknown=None), "fields differ"),
        (lambda index: index.update(assets={}), "assets must be an array"),
        (
            lambda index: cast(list[dict[str, object]], index["assets"])[0].update(
                position=1
            ),
            "out-of-order",
        ),
        (
            lambda index: cast(list[dict[str, object]], index["assets"])[1].update(
                id="embedded-asset"
            ),
            "repeat durable id",
        ),
        (
            lambda index: cast(list[dict[str, object]], index["assets"])[0].update(
                id="different-asset"
            ),
            "does not match graph blob item",
        ),
        (
            lambda index: cast(list[dict[str, object]], index["assets"])[0].update(
                mode="foreign"
            ),
            "unsupported mode",
        ),
        (
            lambda index: cast(dict[str, object], index["graph"]).update(
                path="other.json"
            ),
            "graph path",
        ),
        (
            lambda index: cast(dict[str, object], index["graph"]).update(size=1),
            "graph size",
        ),
        (
            lambda index: cast(dict[str, object], index["graph"]).update(
                sha256="00" * 32
            ),
            "graph SHA-256 mismatch",
        ),
        (
            lambda index: cast(list[dict[str, object]], index["assets"])[0].update(
                size=1
            ),
            "entry size does not match its asset row",
        ),
    ],
)
def test_closed_index_and_inventory_refusals(
    change: Callable[[dict[str, object]], None], message: str
) -> None:
    """Near-valid index mutations fail with a bounded, named diagnostic."""
    graph_bytes, rows = graph_and_rows()
    index = index_data(graph_bytes, rows)
    change(index)
    with pytest.raises((ValueError, TypeError), match=message):
        open_bundle(io.BytesIO(bundle_bytes(index=index)))


@pytest.mark.parametrize(
    ("document", "message"),
    [
        (b"{", "strict UTF-8 JSON"),
        (b"\xff", "strict UTF-8 JSON"),
        (b'{"bundle_version":"1","bundle_version":"1"}', "repeats field"),
        (b'{"bundle_version":"1","graph":NaN,"assets":[]}', "non-JSON"),
        (b"[]", "must be an object"),
    ],
)
def test_strict_json_refusals(document: bytes, message: str) -> None:
    """Syntax, encoding, constants, duplicates, and root type are strict."""
    graph_bytes, rows = graph_and_rows()
    entries = [
        (info("bundle.json"), document),
        (info("graph.json"), graph_bytes),
        (info("blobs/sha256/" + REF_A.sha256), PAYLOAD_A),
    ]
    with pytest.raises(ValueError, match=message):
        open_bundle(io.BytesIO(bundle_bytes(rows=rows, entries=entries)))


def test_blobless_graph_has_an_empty_inventory() -> None:
    """A graph without the opt-in vocabulary opens only with no asset rows."""
    graph_bytes = dump_bytes(Graph((), (), ()))
    with open_bundle(
        io.BytesIO(
            bundle_bytes(
                graph_bytes=graph_bytes,
                rows=[],
                entries=[
                    (
                        info("bundle.json"),
                        json.dumps(
                            index_data(graph_bytes, []), separators=(",", ":")
                        ).encode(),
                    ),
                    (info("graph.json"), graph_bytes),
                ],
            )
        )
    ) as bundle:
        assert bundle.assets() == ()


@pytest.mark.parametrize(
    ("entries", "comment", "message"),
    [
        ([(info("bundle.json"), b"{}")], b"", "must contain"),
        (
            [(info("graph.json"), b"{}"), (info("bundle.json"), b"{}")],
            b"",
            "entry order",
        ),
        (
            [
                (info("bundle.json"), b"{}"),
                (info("graph.json"), b"{}"),
                (info("../escape"), b"x"),
            ],
            b"",
            "safe name",
        ),
        (
            [
                (info("bundle.json"), b"{}"),
                (info("graph.json"), b"{}"),
                (replace_info(info("extra"), compress_type=zipfile.ZIP_DEFLATED), b"x"),
            ],
            b"",
            "compressed",
        ),
        (None, b"comment", "comments are not allowed"),
    ],
)
def test_zip_profile_refusals(
    entries: list[tuple[zipfile.ZipInfo, bytes]] | None,
    comment: bytes,
    message: str,
) -> None:
    """The reader refuses noncanonical archive structure before payload use."""
    with pytest.raises(ValueError, match=message):
        open_bundle(io.BytesIO(bundle_bytes(entries=entries, archive_comment=comment)))


def test_source_and_archive_shape_refusals() -> None:
    """Nonseekable, tiny, missing-end-record, and trailing forms fail closed."""

    class NotSeekable(io.BytesIO):
        def seekable(self) -> bool:
            return False

    with pytest.raises(ValueError, match="seekable"):
        open_bundle(cast(BinaryIO, NotSeekable()))
    with pytest.raises(ValueError, match="complete end record"):
        open_bundle(io.BytesIO(b"tiny"))
    with pytest.raises(ValueError, match="end record is missing"):
        open_bundle(io.BytesIO(b"x" * 22))
    with pytest.raises(ValueError, match="end record is missing"):
        open_bundle(io.BytesIO(bundle_bytes() + b"trailing"))


def test_end_record_zip64_paths_and_source_failures() -> None:
    """ZIP64 is accepted only when required and each end record is coherent."""
    limits = BundleLimits()
    directory = blob_module._read_central_directory(
        io.BytesIO(zip64_end_record()), limits
    )
    assert directory.zip64 is True
    missing_locator = bytearray(zip64_end_record())
    missing_locator[-42:-38] = b"none"
    with pytest.raises(ValueError, match="ZIP64 end records are missing"):
        blob_module._read_central_directory(io.BytesIO(missing_locator), limits)
    malformed = zip64_end_record(signature=b"none")
    with pytest.raises(ValueError, match="end record is malformed"):
        blob_module._read_central_directory(io.BytesIO(malformed), limits)
    short = zip64_end_record(record_size=1)
    with pytest.raises(ValueError, match="end record is malformed"):
        blob_module._read_central_directory(io.BytesIO(short), limits)
    extended = zip64_end_record(record_size=45)
    with pytest.raises(ValueError, match="end record is malformed"):
        blob_module._read_central_directory(io.BytesIO(extended), limits)
    unnecessary = zip64_end_record(count=3, disk_count=3)
    with pytest.raises(ValueError, match="only when a field requires"):
        blob_module._read_central_directory(io.BytesIO(unnecessary), limits)
    disagreement = zip64_end_record(size=1, classic_size=0)
    with pytest.raises(ValueError, match="end records disagree"):
        blob_module._read_central_directory(io.BytesIO(disagreement), limits)
    bad_directory_layout = zip64_end_record(size=1)
    with pytest.raises(ValueError, match="central-directory layout is inconsistent"):
        blob_module._read_central_directory(io.BytesIO(bad_directory_layout), limits)
    displaced = bytearray(zip64_end_record())
    struct.pack_into("<Q", displaced, 64, 1)
    with pytest.raises(ValueError, match="layout is inconsistent"):
        blob_module._read_central_directory(io.BytesIO(displaced), limits)
    multidisk = zip64_end_record(disk=1)
    with pytest.raises(ValueError, match="single-disk"):
        blob_module._read_central_directory(io.BytesIO(multidisk), limits)
    disagreeing = zip64_end_record(disk_count=1, count=2)
    with pytest.raises(ValueError, match="single-disk"):
        blob_module._read_central_directory(io.BytesIO(disagreeing), limits)

    ordinary = bundle_bytes()
    locator = struct.pack("<4sLQL", b"PK\x06\x07", 0, 0, 1)
    with_locator = ordinary[:-22] + locator + ordinary[-22:]
    with pytest.raises(ValueError, match="only when a size requires"):
        blob_module._read_central_directory(io.BytesIO(with_locator), limits)

    class BadSeek(io.BytesIO):
        def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
            if whence == io.SEEK_END:
                raise OSError("no size")
            return super().seek(offset, whence)

    with pytest.raises(ValueError, match="cannot be measured"):
        blob_module._read_central_directory(BadSeek(), limits)

    comment = mutate_eocd(ordinary, 7, 1)
    with pytest.raises(ValueError, match="comments are not allowed"):
        blob_module._read_central_directory(io.BytesIO(comment), limits)
    locator_multidisk = bytearray(zip64_end_record())
    struct.pack_into("<L", locator_multidisk, 60, 1)
    with pytest.raises(ValueError, match="single-disk"):
        blob_module._read_central_directory(io.BytesIO(locator_multidisk), limits)


def test_zipfile_constructor_failure_is_wrapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A parser failure after bounded end checks has one public diagnostic."""
    encoded = bundle_bytes()

    def refuse(*arguments: object, **keywords: object) -> zipfile.ZipFile:
        del arguments, keywords
        raise zipfile.BadZipFile("broken directory")

    monkeypatch.setattr("zipfile.ZipFile", refuse)
    with pytest.raises(ValueError, match="invalid bundle ZIP"):
        open_bundle(io.BytesIO(encoded))


def test_zip_metadata_profile_rejects_ambiguities() -> None:
    """Duplicate, unsafe, special, and noncanonical ZIP metadata is refused."""
    with pytest.warns(UserWarning, match="Duplicate name"):
        duplicate = bundle_bytes(
            entries=[
                (info("bundle.json"), b"{}"),
                (info("graph.json"), b"{}"),
                (info("graph.json"), b"{}"),
            ]
        )
    with pytest.raises(ValueError, match="duplicate entry"):
        open_bundle(io.BytesIO(duplicate))

    non_ascii = bytearray(bundle_bytes())
    non_ascii[30] = 0xE9
    central_name = non_ascii.rfind(b"bundle.json")
    assert central_name > 30
    non_ascii[central_name] = 0xE9
    with pytest.raises(ValueError, match="name is not ASCII"):
        open_bundle(io.BytesIO(non_ascii))

    invalid_entries: list[tuple[zipfile.ZipInfo, str]] = [
        (info("/absolute"), "safe name"),
        (info("bad\\name"), "safe name"),
        (info("folder/"), "directory"),
        (
            zipfile.ZipInfo("dated", (1981, 1, 1, 0, 0, 0)),
            "noncanonical timestamp",
        ),
        (replace_info(info("mode"), external_attr=0), "noncanonical permissions"),
        (replace_info(info("commented"), comment=b"x"), "has a comment"),
        (replace_info(info("extra"), extra=b"\x02\x00\x00\x00"), "extra field"),
        (replace_info(info("version"), extract_version=10), "ZIP version"),
    ]
    for metadata, message in invalid_entries:
        if metadata.create_system != 3:
            metadata.create_system = 3
            metadata.external_attr = CANONICAL_ATTR
        encoded = bundle_bytes(
            entries=[
                (info("bundle.json"), b"{}"),
                (info("graph.json"), b"{}"),
                (metadata, b"x"),
            ]
        )
        with pytest.raises(ValueError, match=message):
            open_bundle(io.BytesIO(encoded))

    source = io.BytesIO(bundle_bytes())
    directory = blob_module._read_central_directory(source, BundleLimits())
    archive = zipfile.ZipFile(source)
    infos = list(archive.infolist())
    try:
        for flags, message in [
            (0x1, "encrypted"),
            (0x8, "data descriptor"),
            (0x2, "unsupported flags"),
        ]:
            infos[0].flag_bits = flags
            with pytest.raises(ValueError, match=message):
                blob_module._check_archive_profile(
                    source, archive, tuple(infos), directory, BundleLimits()
                )
            infos[0].flag_bits = 0
    finally:
        archive.close()


def test_archive_profile_cross_checks_directory_layout() -> None:
    """Directory counts, comments, offsets, and physical extent must agree."""
    source = io.BytesIO(bundle_bytes())
    directory = blob_module._read_central_directory(source, BundleLimits())
    archive = zipfile.ZipFile(source)
    infos = list(archive.infolist())
    try:
        with pytest.raises(ValueError, match="entry count disagrees"):
            blob_module._check_archive_profile(
                source,
                archive,
                tuple(infos),
                replace(directory, count=directory.count + 1),
                BundleLimits(),
            )
        archive.comment = b"x"
        with pytest.raises(ValueError, match="comments are not allowed"):
            blob_module._check_archive_profile(
                source, archive, tuple(infos), directory, BundleLimits()
            )
        archive.comment = b""
        original = infos[0].header_offset
        infos[0].header_offset = 1
        with pytest.raises(ValueError, match="prefix bytes"):
            blob_module._check_archive_profile(
                source, archive, tuple(infos), directory, BundleLimits()
            )
        infos[0].header_offset = original
        original = infos[1].header_offset
        infos[1].header_offset += 1
        with pytest.raises(ValueError, match="gaps or overlapping"):
            blob_module._check_archive_profile(
                source, archive, tuple(infos), directory, BundleLimits()
            )
        infos[1].header_offset = original
        with pytest.raises(ValueError, match="outside its declared entries"):
            blob_module._check_archive_profile(
                source,
                archive,
                tuple(infos),
                replace(directory, offset=directory.offset + 1),
                BundleLimits(),
            )
    finally:
        archive.close()


def test_local_header_and_zip64_extra_defenses() -> None:
    """Local headers and minimal ZIP64 metadata are checked independently."""
    encoded = bundle_bytes()
    for offset, replacement, message in [
        (0, b"none", "bad local header"),
        (6, b"\x01\x00", "local header disagrees"),
        (12, b"\x21\x50", "local timestamp disagrees"),
        (30, b"x", "noncanonical local name"),
    ]:
        changed = bytearray(encoded)
        changed[offset : offset + len(replacement)] = replacement
        with pytest.raises(ValueError, match=message):
            open_bundle(io.BytesIO(changed))

    metadata = info("large")
    metadata.file_size = 0xFFFFFFFF
    metadata.compress_size = 0xFFFFFFFF
    metadata.header_offset = 0
    metadata.extract_version = 45
    metadata.extra = struct.pack(
        "<HHQQ", 1, 16, metadata.file_size, metadata.compress_size
    )
    blob_module._check_zip64_extra(metadata)
    metadata.extra = struct.pack("<HHQQ", 1, 16, 1, 1)
    with pytest.raises(ValueError, match="ZIP64 metadata disagrees"):
        blob_module._check_zip64_extra(metadata)
    metadata.extra = struct.pack(
        "<HHQQ", 1, 16, metadata.file_size, metadata.compress_size
    )
    metadata.extract_version = 20
    with pytest.raises(ValueError, match="version 45"):
        blob_module._check_zip64_extra(metadata)
    with pytest.raises(ValueError, match="malformed ZIP64"):
        blob_module._check_extra_bytes(b"", required_values=1, subject="x")
    with pytest.raises(ValueError, match="nonminimal ZIP64"):
        blob_module._check_extra_bytes(
            struct.pack("<HHQ", 2, 8, 1), required_values=1, subject="x"
        )


def local_header(
    metadata: zipfile.ZipInfo,
    *,
    size: int,
    compressed: int,
    crc: int = 0,
    version: int | None = None,
    extra: bytes = b"",
    stamp_time: int = 0,
    stamp_date: int = 33,
) -> bytes:
    """Build one local header for direct consistency tests."""
    name = metadata.filename.encode()
    header = struct.pack(
        "<4s5H3L2H",
        b"PK\x03\x04",
        metadata.extract_version if version is None else version,
        metadata.flag_bits,
        metadata.compress_type,
        stamp_time,
        stamp_date,
        crc,
        compressed,
        size,
        len(name),
        len(extra),
    )
    return header + name + extra


def test_local_header_size_crc_and_extra_checks() -> None:
    """Central and local size, CRC, version, and ZIP64 fields must agree."""
    metadata = info("entry")
    metadata.header_offset = 0
    metadata.file_size = 1
    metadata.compress_size = 1
    metadata.CRC = 0
    with pytest.raises(ValueError, match="unsupported local extra"):
        blob_module._check_local_header(
            io.BytesIO(local_header(metadata, size=1, compressed=1, extra=b"xxxx")),
            metadata,
        )
    with pytest.raises(ValueError, match="local sizes disagree"):
        blob_module._check_local_header(
            io.BytesIO(local_header(metadata, size=2, compressed=2)), metadata
        )
    with pytest.raises(ValueError, match="local header disagrees"):
        blob_module._check_local_header(
            io.BytesIO(local_header(metadata, size=1, compressed=1, crc=1)), metadata
        )
    with pytest.raises(ValueError, match="local header disagrees"):
        blob_module._check_local_header(
            io.BytesIO(local_header(metadata, size=1, compressed=1, version=10)),
            metadata,
        )
    with pytest.raises(ValueError, match="local timestamp disagrees"):
        blob_module._check_local_header(
            io.BytesIO(local_header(metadata, size=1, compressed=1, stamp_date=0x5021)),
            metadata,
        )
    non_ascii = info("\N{GREEK CAPITAL LETTER THETA}")
    non_ascii.header_offset = 0
    with pytest.raises(ValueError, match="name is not ASCII"):
        blob_module._check_local_header(
            io.BytesIO(local_header(non_ascii, size=0, compressed=0)), non_ascii
        )

    metadata.file_size = 0xFFFFFFFF
    metadata.compress_size = 0xFFFFFFFF
    metadata.extract_version = 45
    extra = struct.pack("<HHQQ", 1, 16, metadata.file_size, metadata.compress_size)
    assert (
        blob_module._check_local_header(
            io.BytesIO(
                local_header(
                    metadata,
                    size=0xFFFFFFFF,
                    compressed=0xFFFFFFFF,
                    extra=extra,
                )
            ),
            metadata,
        )
        == 30 + len(metadata.filename) + len(extra) + 0xFFFFFFFF
    )
    with pytest.raises(ValueError, match="omits required ZIP64"):
        blob_module._check_local_header(
            io.BytesIO(local_header(metadata, size=1, compressed=1)), metadata
        )
    mismatched = struct.pack("<HHQQ", 1, 16, metadata.file_size + 1, 1)
    with pytest.raises(ValueError, match="local ZIP64 sizes disagree"):
        blob_module._check_local_header(
            io.BytesIO(
                local_header(
                    metadata,
                    size=0xFFFFFFFF,
                    compressed=0xFFFFFFFF,
                    extra=mismatched,
                )
            ),
            metadata,
        )


def test_index_scalar_and_collection_defenses() -> None:
    """Closed rows reject wrong scalar types, conflicts, and entry mismatch."""
    graph_bytes, rows = graph_and_rows()
    mutations: list[tuple[Callable[[dict[str, object]], None], str]] = [
        (
            lambda index: cast(list[object], index["assets"]).__setitem__(0, []),
            "must be an object",
        ),
        (
            lambda index: cast(list[dict[str, object]], index["assets"])[0].update(
                id=1
            ),
            "must be a string",
        ),
        (
            lambda index: cast(list[dict[str, object]], index["assets"])[0].update(
                position=True
            ),
            "nonnegative integer",
        ),
        (
            lambda index: cast(list[dict[str, object]], index["assets"])[0].update(
                href="not-allowed"
            ),
            "fields differ",
        ),
    ]
    for mutation, message in mutations:
        index = index_data(graph_bytes, [dict(row) for row in rows])
        mutation(index)
        with pytest.raises(ValueError, match=message):
            open_bundle(io.BytesIO(bundle_bytes(index=index)))

    duplicate = [dict(row) for row in rows]
    duplicate[1].update(
        sha256=REF_A.sha256,
        size=REF_A.size,
        mode="linked",
        href="different",
    )
    with pytest.raises(ValueError, match="conflicting locations"):
        open_bundle(io.BytesIO(bundle_bytes(rows=duplicate)))

    valid_index = json.dumps(
        index_data(graph_bytes, rows), separators=(",", ":")
    ).encode()
    with pytest.raises(ValueError, match="entries must be exactly"):
        open_bundle(
            io.BytesIO(
                bundle_bytes(
                    entries=[
                        (info("bundle.json"), valid_index),
                        (info("graph.json"), graph_bytes),
                        (info("undeclared"), b"x"),
                    ]
                )
            )
        )

    no_assets = index_data(graph_bytes, [])
    with pytest.raises(ValueError, match="inventory has 0 rows"):
        open_bundle(
            io.BytesIO(
                bundle_bytes(
                    index=no_assets,
                    entries=[
                        (
                            info("bundle.json"),
                            json.dumps(no_assets, separators=(",", ":")).encode(),
                        ),
                        (info("graph.json"), graph_bytes),
                    ],
                )
            )
        )


def test_exact_reader_defenses() -> None:
    """Bounded structure reads reject bad seek and binary-reader behavior."""

    class BadRead(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            del size
            return b"too much"

    class TextRead(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            del size
            return cast(bytes, "text")

    class EmptyRead(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            del size
            return b""

    class BadAbsoluteSeek(io.BytesIO):
        def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
            del offset, whence
            raise OSError("no seek")

    with pytest.raises(ValueError, match="more bytes"):
        blob_module._read_exact(BadRead(), 1, "test")
    with pytest.raises(TypeError, match="did not return bytes"):
        blob_module._read_exact(TextRead(), 1, "test")
    with pytest.raises(ValueError, match="truncated"):
        blob_module._read_exact(EmptyRead(), 1, "test")
    with pytest.raises(ValueError, match="cannot seek"):
        blob_module._read_at(BadAbsoluteSeek(), 0, 1, "test")


def test_metadata_entry_reader_defenses() -> None:
    """Metadata readers must return exactly their central-directory byte count."""

    class Reader(io.BytesIO):
        def __init__(self, value: object) -> None:
            super().__init__()
            self.value = value

        def read(self, size: int | None = -1) -> bytes:
            del size
            return cast(bytes, self.value)

    class Archive:
        def __init__(self, value: object) -> None:
            self.value = value

        def open(self, metadata: zipfile.ZipInfo, mode: str) -> Reader:
            del metadata, mode
            return Reader(self.value)

    metadata = info("metadata")
    metadata.file_size = 1
    with pytest.raises(TypeError, match="did not return bytes"):
        blob_module._read_metadata_entry(
            cast(zipfile.ZipFile, Archive("x")), metadata, 10, "metadata"
        )
    with pytest.raises(ValueError, match="bytes disagree"):
        blob_module._read_metadata_entry(
            cast(zipfile.ZipFile, Archive(b"")), metadata, 10, "metadata"
        )
