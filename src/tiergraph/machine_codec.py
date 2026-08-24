"""Canonical JSON Lines serialization for checked machine programs."""

from __future__ import annotations

import json
from io import BytesIO
from typing import BinaryIO

from tiergraph.machine import MACHINE_VERSION, Program, _decode_object, _decode_opcode
from tiergraph.wire import MAX_DOCUMENT_BYTES, MAX_JSON_DEPTH

# Owner-tunable policy: keep an individual JSONL record bounded independently
# of the complete program stream.
_JSONL_LINE_BYTES = 1024 * 1024


def program_loads(source: str | bytes) -> Program:
    """Parse a versioned JSONL machine program under the public wire limits."""
    encoded = source.encode("utf-8") if isinstance(source, str) else source
    return load_program(BytesIO(encoded))


def load_program(stream: BinaryIO) -> Program:
    """Read a versioned JSONL machine program incrementally from a binary stream."""
    records: list[object] = []
    total = 0
    for number, line in enumerate(stream, 1):
        total += len(line)
        if total > MAX_DOCUMENT_BYTES:
            raise ValueError(f"JSONL program exceeds {MAX_DOCUMENT_BYTES} bytes")
        if len(line) > _JSONL_LINE_BYTES:
            raise ValueError(f"JSONL line {number} exceeds {_JSONL_LINE_BYTES} bytes")
        if not line.strip():
            raise ValueError(f"JSONL line {number} is whitespace-only")
        try:
            _check_jsonl_depth(line, number)
            records.append(json.loads(line))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValueError(f"JSONL line {number}: {error}") from error
        except RecursionError as error:
            raise ValueError(
                f"JSONL line {number}: JSON nesting depth exceeds limit "
                f"{MAX_JSON_DEPTH}"
            ) from error
    if not records:
        raise ValueError("JSONL program is missing its header line")
    header = _decode_object(records[0], "header", {"machine_version"})
    if header["machine_version"] != MACHINE_VERSION:
        raise ValueError(f"header machine_version must be {MACHINE_VERSION!r}")
    try:
        return Program(
            tuple(
                _decode_opcode(record, f"line {number}")
                for number, record in enumerate(records[1:], 2)
            )
        )
    except TypeError as error:
        raise ValueError(str(error)) from error


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
                raise ValueError(
                    f"JSONL line {number}: JSON nesting depth exceeds limit "
                    f"{MAX_JSON_DEPTH}"
                )
        elif byte in (ord("]"), ord("}")):
            depth -= 1


__all__ = ["load_program", "program_dumps", "program_loads"]
