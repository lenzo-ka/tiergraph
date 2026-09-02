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
    Refusal,
    RefusalStage,
    Repeat,
    TierDeclaration,
    load_program,
    machine,
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


@pytest.mark.parametrize(
    "value,path,message",
    [
        ({"namespace": [], "local_name": "tier"}, "name", "name.namespace"),
        ({"namespace": "urn:test", "local_name": 3}, "name", "name.local_name"),
    ],
)
def test_qname_decoder_rejects_non_string_members(
    value: object, path: str, message: str
) -> None:
    """QName members are strings before a core value is constructed."""
    with pytest.raises(ValueError, match=rf"^{re.escape(message)} must be a string$"):
        machine._decode_qname(value, path)


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("name", {"namespace": [], "local_name": "value"}, "attribute.name.namespace"),
        ("value_type", 4, "attribute.value_type"),
        ("lexical", ["x"], "attribute.lexical"),
    ],
)
def test_attribute_value_decoder_rejects_non_string_members(
    field: str, value: object, message: str
) -> None:
    """Attribute values reject every non-string member with its precise path."""
    data: dict[str, object] = {
        "name": {"namespace": "urn:test", "local_name": "value"},
        "value_type": "string",
        "lexical": "x",
    }
    data[field] = value
    with pytest.raises(ValueError, match=rf"^{re.escape(message)} must be a string$"):
        machine._decode_attribute_value(data, "attribute")


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


def test_load_program_rejects_without_consuming_the_remaining_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused line ends the read; nothing after it is asked for.

    The reader asks for a bounded read rather than iterating whole lines, so
    the sentinel answers `readline` and the claim is unchanged: the delivery
    that trips the envelope is the last thing taken from the stream.  The
    document bound is lowered so one bounded delivery crosses it, which is what
    a whole-line read used to reach only by handing over sixteen megabytes.
    """
    monkeypatch.setattr(machine_codec, "MAX_DOCUMENT_BYTES", 4)

    class SentinelStream:
        def __init__(self) -> None:
            self.reads = 0

        def readline(self, limit: int = -1) -> bytes:
            self.reads += 1
            if self.reads == 1:
                assert limit == 5
                return b"x" * limit
            raise AssertionError("load_program consumed beyond the rejecting line")

    stream = SentinelStream()
    with pytest.raises(ValueError, match=r"^JSONL program exceeds 4 bytes$"):
        load_program(stream)  # type: ignore[arg-type]
    assert stream.reads == 1


def test_load_program_never_holds_a_line_longer_than_its_own_bound() -> None:
    """REGRESSION: the envelopes bound the read, not just the refusal.

    Both bounds used to be measured after a whole line had been materialized,
    which left the input carrying no newline at all bounded by neither: the
    reader held it entire in order to say it was too big, sixteen times the
    document envelope and two hundred and fifty-six times the line one, and
    that is exactly what reading incrementally is written to prevent.  The
    refusal was never the defect and does not move; what moves is how much had
    to be delivered to reach it.

    The stream below answers whichever way it is read and records the largest
    single delivery, so the discrimination is that number rather than the
    exception: iterating whole lines hands over the entire four-megabyte line,
    and asking for a bounded read hands over one byte past the line envelope.
    """
    hostile = b"x" * (4 * machine_codec._JSONL_LINE_BYTES)

    class CountingStream:
        def __init__(self, data: bytes) -> None:
            self.data = data
            self.position = 0
            self.largest = 0

        def __iter__(self) -> CountingStream:
            return self

        def __next__(self) -> bytes:
            chunk = self.readline()
            if not chunk:
                raise StopIteration
            return chunk

        def readline(self, limit: int = -1) -> bytes:
            end = len(self.data) if limit < 0 else min(len(self.data), limit)
            newline = self.data.find(b"\n", self.position, self.position + end)
            stop = (
                min(self.position + end, len(self.data)) if newline < 0 else newline + 1
            )
            chunk = self.data[self.position : stop]
            self.position = stop
            self.largest = max(self.largest, len(chunk))
            return chunk

    stream = CountingStream(hostile)
    with pytest.raises(Refusal) as refusal:
        load_program(stream)  # type: ignore[arg-type]
    assert refusal.value.stage is RefusalStage.ENVELOPE
    assert str(refusal.value) == (
        f"JSONL line 1 exceeds {machine_codec._JSONL_LINE_BYTES} bytes"
    )
    assert stream.largest <= machine_codec._JSONL_LINE_BYTES + 1
    assert len(hostile) > machine_codec._JSONL_LINE_BYTES + 1


SURROGATE_PROGRAM = (
    '{"machine_version":"1"}\n'
    '{"opcode":"declare_namespace",'
    '"declaration":{"namespace":"urn:\\ud800","prefix":"p"}}\n'
)


def test_the_program_reader_refuses_the_line_no_writer_can_encode() -> None:
    """REGRESSION: the escaped surrogate is refused where the character is.

    Every byte of this program is ASCII, so the unpaired surrogate survives the
    envelope, the decode, and the depth scan; it becomes a character only when
    the parser builds the record, and the reader used to hand back a `Program`
    for it.  The stage is the one `docs/format.md` gives the condition rather
    than the one the check happens to run at: the canonical text of this
    program -- what `program_dumps` writes, without ASCII escaping -- carries
    the character itself and is a text the encoder cannot write.

    Line orientation shows up only in the scope.  The field path is the one
    inside the record, as the whole-document readers name the one inside the
    document, and the line number says which record to look at.
    """
    assert len(SURROGATE_PROGRAM.encode("ascii")) == len(SURROGATE_PROGRAM)
    with pytest.raises(ValueError) as refusal:
        program_loads(SURROGATE_PROGRAM)
    assert type(refusal.value) is Refusal
    assert refusal.value.stage is RefusalStage.ENCODING
    assert refusal.value.also == ()
    assert str(refusal.value) == (
        "JSONL line 2: declaration.namespace value 'urn:\\ud800' "
        "has unsupported character U+D800"
    )


def test_a_text_program_is_measured_before_it_is_encoded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REGRESSION: a text program's size outranks its encodability.

    The other spelling of the same condition is the character standing in the
    text, and it used to meet no staged condition at all: the encoder's own
    `UnicodeEncodeError` escaped `program_loads` while the three whole-document
    readers answered that very input at `ENCODING`.

    Staging it puts a rank-2 condition where a rank-1 one can also hold, so the
    size is measured first, in code points, each of which is at least one
    encoded byte.  A reader that encoded before measuring would report the
    encoding condition on an input that meets both.
    """
    monkeypatch.setattr(machine_codec, "MAX_DOCUMENT_BYTES", 1)
    with pytest.raises(Refusal) as sized:
        program_loads('{"machine_version":"1"}\n"\ud800"\n')
    assert sized.value.stage is RefusalStage.ENVELOPE
    assert str(sized.value) == "JSONL program exceeds 1 bytes"

    monkeypatch.setattr(machine_codec, "MAX_DOCUMENT_BYTES", 1024)
    with pytest.raises(Refusal) as encoded:
        program_loads('{"machine_version":"1"}\n"\ud800"\n')
    assert encoded.value.stage is RefusalStage.ENCODING
    assert str(encoded.value) == (
        "JSONL line 2: encode UTF-8 failed: surrogates not allowed"
    )


def test_program_dumps_has_one_canonical_object_per_line() -> None:
    lines = program_dumps(_representative_program()).splitlines()
    assert json.loads(lines[0]) == {"machine_version": tiergraph.MACHINE_VERSION}
    assert [json.loads(line)["opcode"] for line in lines[1:]] == [
        "declare_namespace",
        "declare_tier",
        "repeat",
    ]
