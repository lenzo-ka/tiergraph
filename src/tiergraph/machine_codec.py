"""Canonical JSON Lines serialization for checked machine programs."""

from __future__ import annotations

import json
from io import BytesIO
from typing import BinaryIO

from tiergraph.machine import (
    MACHINE_VERSION,
    Program,
    _decode_object,
    _decode_opcode,
)
from tiergraph.schema import Refusal, RefusalStage
from tiergraph.wire import (
    MAX_DOCUMENT_BYTES,
    MAX_JSON_DEPTH,
    _object_without_duplicate_keys,
    _refuse_unencodable_strings,
)

# Owner-tunable policy: keep an individual JSONL record bounded independently
# of the complete program stream.
_JSONL_LINE_BYTES = 1024 * 1024


def program_loads(source: str | bytes) -> Program:
    """Parse a versioned JSONL machine program under the public wire limits."""
    return load_program(BytesIO(_program_bytes(source)))


def _program_bytes(source: str | bytes) -> bytes:
    """Encode a text program, ranking its size before its encodability.

    Text this reader cannot encode is the encoding condition met before any
    line is read, and it used to leave `program_loads` as the encoder's own
    `UnicodeEncodeError` while the three whole-document readers answered the
    same input at `ENCODING`.  The size is measured first, in code points,
    because every code point is at least one encoded byte: that keeps the
    envelope ahead of the encoding condition on an input that meets both,
    which is the order `_checked_document` reads a text document in.  What is
    left unmeasured is the same residue that reader leaves -- text too large
    and also unencodable has no byte length to compare -- rather than a new
    gap this one opens.

    The line the encoder stopped on scopes the refusal, counted by the `\\n`
    the reader itself splits on, so the diagnostic says where to look exactly
    as every other one this reader raises does.
    """
    if isinstance(source, bytes):
        return source
    if len(source) > MAX_DOCUMENT_BYTES:
        raise Refusal(
            RefusalStage.ENVELOPE,
            f"JSONL program exceeds {MAX_DOCUMENT_BYTES} bytes",
        )
    try:
        return source.encode("utf-8")
    except UnicodeEncodeError as error:
        number = source.count("\n", 0, error.start) + 1
        raise Refusal(
            RefusalStage.ENCODING,
            f"JSONL line {number}: encode UTF-8 failed: {error.reason}",
        ) from error


def load_program(stream: BinaryIO) -> Program:
    """Read a versioned JSONL machine program incrementally from a binary stream."""
    # The envelope, encoding, and syntax conditions below are the ones
    # `wire._parsed_json` answers for a whole-document reader, and they are
    # answered here at the same stages, in the same order, and in the same
    # wording: bytes that are not UTF-8 are an encoding condition rather than a
    # parse failure, and a repeated object key is refused through the very hook
    # that reader passes, imported rather than restated, so the two cannot
    # drift into two accounts of one rule.
    #
    # The encoding condition has two spellings and both are answered. A
    # character standing in a line is met when that line is decoded. One
    # written as an escape is not in the line at all -- `\ud800` is six ASCII
    # bytes -- so it survives every check that reads bytes and becomes a
    # character only when the parser builds the record; the same
    # `_refuse_unencodable_strings` the writers use therefore runs on each
    # parsed record, so one condition keeps one wording and one stage whichever
    # direction a caller met it from.
    #
    # Running that check after parsing does not make it a later condition. The
    # order in `docs/format.md` ranks conditions, not the checks that find
    # them, and rank 2 is the text being one the encoder can write. The
    # canonical text of such a program -- what `program_dumps` writes, with
    # `ensure_ascii=False` -- holds the character rather than the escape, and
    # is a text the encoder cannot write. Line orientation changes the scope
    # the condition is reported in, not its rank: the escape changes when the
    # condition becomes visible, and the line says where to look.
    #
    # What cannot be shared is the frame those conditions are measured
    # against. `_parsed_json` measures one text: it takes the complete input,
    # decodes it once, and names the document in what it reports. A program is
    # a stream of lines read incrementally, so its envelope is a running total
    # plus a per-line bound, its decoding happens a line at a time, and every
    # diagnostic is scoped by the line number that says where to look. Calling
    # `_parsed_json` here would mean holding the whole stream in memory to
    # decode it at once, which is what reading incrementally exists to avoid.
    records: list[object] = []
    total = 0
    for number, line in enumerate(stream, 1):
        total += len(line)
        if total > MAX_DOCUMENT_BYTES:
            raise Refusal(
                RefusalStage.ENVELOPE,
                f"JSONL program exceeds {MAX_DOCUMENT_BYTES} bytes",
            )
        if len(line) > _JSONL_LINE_BYTES:
            raise Refusal(
                RefusalStage.ENVELOPE,
                f"JSONL line {number} exceeds {_JSONL_LINE_BYTES} bytes",
            )
        try:
            text = line.decode("utf-8")
        except UnicodeDecodeError as error:
            raise Refusal(
                RefusalStage.ENCODING,
                f"JSONL line {number}: parse UTF-8 failed: {error.reason}",
            ) from error
        if not text.strip():
            raise Refusal(
                RefusalStage.SYNTAX, f"JSONL line {number} is whitespace-only"
            )
        _check_jsonl_depth(line, number)
        try:
            record = json.loads(text, object_pairs_hook=_object_without_duplicate_keys)
            _refuse_unencodable_strings(record, "")
        except json.JSONDecodeError as error:
            raise Refusal(
                RefusalStage.SYNTAX,
                f"JSONL line {number}: parse JSON failed: {error.msg}",
            ) from error
        except Refusal as error:
            raise Refusal(
                error.stage, f"JSONL line {number}: {error}", error.also
            ) from error
        except RecursionError as error:
            raise Refusal(
                RefusalStage.SYNTAX,
                f"JSONL line {number}: JSON nesting depth exceeds limit "
                f"{MAX_JSON_DEPTH}",
            ) from error
        records.append(record)
    if not records:
        raise Refusal(
            RefusalStage.DISCRIMINATOR, "JSONL program is missing its header line"
        )
    header = records[0]
    if not isinstance(header, dict):
        raise Refusal(RefusalStage.CONSTRUCTION, "header must be an object")
    if "machine_version" not in header:
        raise Refusal(
            RefusalStage.DISCRIMINATOR, "header is missing field 'machine_version'"
        )
    if header["machine_version"] != MACHINE_VERSION:
        raise Refusal(
            RefusalStage.DISCRIMINATOR,
            f"header machine_version must be {MACHINE_VERSION!r}",
        )
    _decode_object(header, "header", {"machine_version"})
    try:
        return Program(
            tuple(
                _decode_opcode(record, f"line {number}")
                for number, record in enumerate(records[1:], 2)
            )
        )
    except TypeError as error:
        raise Refusal(RefusalStage.CONSTRUCTION, str(error)) from error


def program_dumps(program: Program) -> str:
    """Return canonical JSONL for a machine program, including a final newline."""
    records: tuple[object, ...] = (
        {"machine_version": MACHINE_VERSION},
        *(opcode.to_data() for opcode in program.opcodes),
    )
    return "".join(
        json.dumps(
            record,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
        for record in records
    )


def _check_jsonl_depth(line: bytes, number: int) -> None:
    """Refuse excessive JSON container nesting before invoking the parser."""
    depth = 0
    in_string = False
    escaped = False
    for byte in line:
        if in_string:
            if escaped:
                escaped = False
            elif byte == ord("\\"):
                escaped = True
            elif byte == ord('"'):
                in_string = False
        elif byte == ord('"'):
            in_string = True
        elif byte in (ord("["), ord("{")):
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise Refusal(
                    RefusalStage.SYNTAX,
                    f"JSONL line {number}: JSON nesting depth exceeds limit "
                    f"{MAX_JSON_DEPTH}",
                )
        elif byte in (ord("]"), ord("}")):
            depth -= 1


__all__ = ["load_program", "program_dumps", "program_loads"]
