"""The public machine-program codec preserves the CLI JSONL contract."""

from __future__ import annotations

import json
import re

import pytest

import tiergraph
from tiergraph import (
    AddItem,
    DeclareNamespace,
    DeclareTier,
    NamespaceDeclaration,
    Program,
    QualifiedName,
    Repeat,
    TierDeclaration,
    load_program,
    machine_codec,
    program_dumps,
    program_loads,
)


def _representative_program() -> Program:
    namespace = "urn:codec"
    tier = QualifiedName(namespace, "events")
    return Program(
        (
            DeclareNamespace(NamespaceDeclaration("c", namespace)),
            DeclareTier(TierDeclaration(tier, "Events")),
            Repeat(2, (AddItem(tier),)),
        )
    )


def test_program_codec_round_trip_is_canonical() -> None:
    program = _representative_program()
    encoded = program_dumps(program)
    reparsed = program_loads(encoded)

    assert encoded.endswith("\n")
    assert encoded == program_dumps(reparsed)
    assert tuple(opcode.to_data() for opcode in reparsed.opcodes) == tuple(
        opcode.to_data() for opcode in program.opcodes
    )
    assert reparsed.fingerprint() == program.fingerprint()
    assert program_loads(encoded.encode("utf-8")) == program


@pytest.mark.parametrize(
    "source,message",
    [
        (b"", "JSONL program is missing its header line"),
        (b"[]", "header must be an object"),
        (
            b'{"machine_version":"old"}',
            f"header machine_version must be {tiergraph.MACHINE_VERSION!r}",
        ),
        (
            b'{"machine_version":"1"}\n{"opcode":"unknown"}',
            "line 2.opcode 'unknown' is unknown",
        ),
        (
            b'{"machine_version":"1"}\n{',
            "JSONL line 2:",
        ),
    ],
)
def test_program_loads_normalizes_malformed_records(
    source: bytes, message: str
) -> None:
    with pytest.raises(ValueError, match=re.escape(message)):
        program_loads(source)


def test_program_loads_preserves_all_limit_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    header = b'{"machine_version":"1"}\n'

    monkeypatch.setattr(machine_codec, "MAX_DOCUMENT_BYTES", len(header))
    with pytest.raises(
        ValueError,
        match=rf"^JSONL program exceeds {len(header)} bytes$",
    ):
        program_loads(header + b"{}")

    monkeypatch.setattr(machine_codec, "MAX_DOCUMENT_BYTES", 1024)
    monkeypatch.setattr(machine_codec, "_JSONL_LINE_BYTES", 2)
    with pytest.raises(ValueError, match=r"^JSONL line 1 exceeds 2 bytes$"):
        program_loads(header)

    monkeypatch.setattr(machine_codec, "_JSONL_LINE_BYTES", 1024)
    monkeypatch.setattr(machine_codec, "MAX_JSON_DEPTH", 2)
    with pytest.raises(
        ValueError,
        match=r"^JSONL line 1: JSON nesting depth exceeds limit 2$",
    ):
        program_loads(b"[[[]]]")


def test_load_program_rejects_without_consuming_the_remaining_stream() -> None:
    class SentinelStream:
        def __init__(self) -> None:
            self.reads = 0

        def __iter__(self) -> SentinelStream:
            return self

        def __next__(self) -> bytes:
            self.reads += 1
            if self.reads == 1:
                return b"x" * (tiergraph.MAX_DOCUMENT_BYTES + 1)
            raise AssertionError("load_program consumed beyond the rejecting line")

    stream = SentinelStream()
    with pytest.raises(
        ValueError,
        match=rf"^JSONL program exceeds {tiergraph.MAX_DOCUMENT_BYTES} bytes$",
    ):
        load_program(stream)  # type: ignore[arg-type]
    assert stream.reads == 1


def test_program_dumps_has_one_canonical_object_per_line() -> None:
    lines = program_dumps(_representative_program()).splitlines()
    assert json.loads(lines[0]) == {"machine_version": tiergraph.MACHINE_VERSION}
    assert [json.loads(line)["opcode"] for line in lines[1:]] == [
        "declare_namespace",
        "declare_tier",
        "repeat",
    ]
