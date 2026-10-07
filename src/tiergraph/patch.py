"""Versioned, fingerprint-guarded graph patches and their JSONL codec."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from io import BytesIO
from typing import BinaryIO, cast

from tiergraph.core import Graph, JsonValue
from tiergraph.edit import EditAnnotations
from tiergraph.equivalence import EquivalenceView, fingerprint
from tiergraph.machine import (
    MAX_TOTAL_OPCODES,
    PrimitiveOpcode,
    Repeat,
    _decode_object,
    _decode_opcode,
)
from tiergraph.machine_codec import (
    _JSONL_LINE_BYTES,
    _jsonl_bytes,
    _load_jsonl_records,
)
from tiergraph.schema import Refusal, RefusalStage
from tiergraph.wire import (
    MAX_DOCUMENT_BYTES,
    _integer,
    _refuse_unencodable_strings,
    _string,
)

PATCH_VERSION = "1"


def _opcode_name(opcode: PrimitiveOpcode) -> str:
    value = opcode.to_data()["opcode"]
    assert isinstance(value, str)
    return value


@dataclass(frozen=True, slots=True)
class PatchOperation:
    """Carry one executable transition, its exact inverse, and fingerprints."""

    opcode: PrimitiveOpcode
    inverse: PrimitiveOpcode
    base_fingerprint: str
    target_fingerprint: str
    annotations: EditAnnotations = field(default_factory=EditAnnotations)

    def to_data(self) -> dict[str, JsonValue]:
        """Return one self-checking JSONL operation record."""
        return {
            **self.opcode.to_data(),
            "base_fingerprint": self.base_fingerprint,
            "target_fingerprint": self.target_fingerprint,
            "annotations": self.annotations.to_data(),
            "inverse": self.inverse.to_data(),
        }


@dataclass(frozen=True, slots=True)
class Patch:
    """Apply recorded graph transitions only to their identified base."""

    base_fingerprint: str
    target_fingerprint: str
    operations: tuple[PatchOperation, ...]
    annotations: EditAnnotations = field(default_factory=EditAnnotations)
    patch_version: str = PATCH_VERSION

    def __post_init__(self) -> None:
        """Validate the independently versioned patch envelope."""
        if self.patch_version != PATCH_VERSION:
            raise Refusal(
                RefusalStage.DISCRIMINATOR,
                f"patch_version must be {PATCH_VERSION!r}",
            )
        if len(self.operations) > MAX_TOTAL_OPCODES:
            raise Refusal(
                RefusalStage.ENVELOPE,
                f"patch operation count exceeds limit {MAX_TOTAL_OPCODES}",
            )

    def apply(self, base: Graph) -> Graph:
        """Apply every operation after checking each identified transition."""
        return apply_patch(self, base)

    def invert(self) -> Patch:
        """Return the exact reverse patch."""
        return invert_patch(self)


def apply_patch(patch: Patch, base: Graph) -> Graph:
    """Apply ``patch`` to its identified base, validating every transition."""
    actual = fingerprint(base, EquivalenceView.IDENTIFIED)
    if actual != patch.base_fingerprint:
        raise Refusal(
            RefusalStage.SEMANTICS,
            "patch base fingerprint mismatch: "
            f"expected {patch.base_fingerprint!r}, got {actual!r}",
        )
    graph = base
    for index, operation in enumerate(patch.operations):
        actual = fingerprint(graph, EquivalenceView.IDENTIFIED)
        if actual != operation.base_fingerprint:
            raise Refusal(
                RefusalStage.SEMANTICS,
                f"patch operation {index} {_opcode_name(operation.opcode)!r} base "
                "fingerprint mismatch",
            )
        try:
            graph = operation.opcode.apply(graph)
        except Refusal:
            raise
        except (TypeError, ValueError) as error:
            raise Refusal(
                RefusalStage.SEMANTICS,
                f"patch operation {index} {_opcode_name(operation.opcode)!r} "
                f"refused: {error}",
            ) from error
        actual_target = fingerprint(graph, EquivalenceView.IDENTIFIED)
        if actual_target != operation.target_fingerprint:
            raise Refusal(
                RefusalStage.SEMANTICS,
                f"patch operation {index} {_opcode_name(operation.opcode)!r} "
                "produced the wrong target fingerprint",
            )
    actual = fingerprint(graph, EquivalenceView.IDENTIFIED)
    if actual != patch.target_fingerprint:
        raise Refusal(
            RefusalStage.SEMANTICS,
            "patch target fingerprint mismatch: "
            f"expected {patch.target_fingerprint!r}, got {actual!r}",
        )
    return graph


def invert_patch(patch: Patch) -> Patch:
    """Reverse operation order and exchange every recorded transition."""
    return Patch(
        patch.target_fingerprint,
        patch.base_fingerprint,
        tuple(
            PatchOperation(
                operation.inverse,
                operation.opcode,
                operation.target_fingerprint,
                operation.base_fingerprint,
                operation.annotations,
            )
            for operation in reversed(patch.operations)
        ),
        patch.annotations,
    )


def compose_patches(first: Patch, second: Patch) -> Patch:
    """Compose adjacent identified patches without weakening either guard."""
    if first.target_fingerprint != second.base_fingerprint:
        raise Refusal(
            RefusalStage.SEMANTICS,
            "cannot compose patches whose target and base fingerprints differ",
        )
    return Patch(
        first.base_fingerprint,
        second.target_fingerprint,
        (*first.operations, *second.operations),
        first.annotations.merged(second.annotations),
    )


def patch_dumps(patch: Patch) -> str:
    """Return canonical JSONL with one operation per line and a final newline."""
    records: tuple[JsonValue, ...] = (
        {
            "patch_version": patch.patch_version,
            "base_fingerprint": patch.base_fingerprint,
            "target_fingerprint": patch.target_fingerprint,
            "annotations": patch.annotations.to_data(),
        },
        *(operation.to_data() for operation in patch.operations),
    )
    lines: list[str] = []
    total = 0
    for number, record in enumerate(records, 1):
        try:
            _refuse_unencodable_strings(record, "")
            line = (
                json.dumps(
                    record,
                    allow_nan=False,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
        except Refusal as error:
            raise Refusal(
                error.stage, f"JSONL line {number}: {error}", error.also
            ) from error
        except (TypeError, ValueError) as error:
            raise Refusal(
                RefusalStage.VALUE, f"JSONL line {number}: {error}"
            ) from error
        size = len(line.encode("utf-8"))
        if size > _JSONL_LINE_BYTES:
            raise Refusal(
                RefusalStage.ENVELOPE,
                f"JSONL line {number} exceeds {_JSONL_LINE_BYTES} bytes",
            )
        total += size
        if total > MAX_DOCUMENT_BYTES:
            raise Refusal(
                RefusalStage.ENVELOPE,
                f"JSONL patch exceeds {MAX_DOCUMENT_BYTES} bytes",
            )
        lines.append(line)
    return "".join(lines)


def patch_loads(source: str | bytes) -> Patch:
    """Parse a patch from bounded strict JSONL text or bytes."""
    return load_patch(BytesIO(_jsonl_bytes(source, "patch")))


def load_patch(stream: BinaryIO) -> Patch:
    """Read a patch incrementally under the machine codec's shared limits."""
    records = _load_jsonl_records(stream, "patch")
    raw_header = records[0]
    if not isinstance(raw_header, dict):
        raise Refusal(RefusalStage.CONSTRUCTION, "header must be an object")
    if "patch_version" not in raw_header:
        raise Refusal(
            RefusalStage.DISCRIMINATOR,
            "header is missing field 'patch_version'",
        )
    raw_version = raw_header["patch_version"]
    if raw_version != PATCH_VERSION:
        raise Refusal(
            RefusalStage.DISCRIMINATOR,
            f"header patch_version must be {PATCH_VERSION!r}",
        )
    header = _decode_object(
        raw_header,
        "header",
        {
            "patch_version",
            "base_fingerprint",
            "target_fingerprint",
            "annotations",
        },
    )
    version = _string(header["patch_version"], "header.patch_version")
    operations = tuple(
        _decode_patch_operation(record, f"line {number}")
        for number, record in enumerate(records[1:], 2)
    )
    return Patch(
        _string(header["base_fingerprint"], "header.base_fingerprint"),
        _string(header["target_fingerprint"], "header.target_fingerprint"),
        operations,
        _decode_annotations(header["annotations"], "header.annotations"),
        version,
    )


def _decode_patch_operation(value: object, path: str) -> PatchOperation:
    if not isinstance(value, dict):
        raise Refusal(RefusalStage.CONSTRUCTION, f"{path} must be an object")
    required = {
        "opcode",
        "base_fingerprint",
        "target_fingerprint",
        "annotations",
        "inverse",
    }
    missing = required - value.keys()
    if missing:
        raise Refusal(
            RefusalStage.SHAPE,
            f"{path} is missing fields {sorted(missing)!r}",
        )
    forward_data = {
        key: item
        for key, item in value.items()
        if key
        not in {"base_fingerprint", "target_fingerprint", "annotations", "inverse"}
    }
    opcode = _decode_opcode(forward_data, f"{path}.opcode")
    inverse = _decode_opcode(value["inverse"], f"{path}.inverse")
    if isinstance(opcode, Repeat) or isinstance(inverse, Repeat):
        raise Refusal(
            RefusalStage.CONSTRUCTION,
            f"{path} forward and inverse must be primitive opcodes",
        )
    return PatchOperation(
        opcode,
        inverse,
        _string(value["base_fingerprint"], f"{path}.base_fingerprint"),
        _string(value["target_fingerprint"], f"{path}.target_fingerprint"),
        _decode_annotations(value["annotations"], f"{path}.annotations"),
    )


def _decode_annotations(value: object, path: str) -> EditAnnotations:
    if not isinstance(value, dict):
        raise Refusal(RefusalStage.CONSTRUCTION, f"{path} must be an object")
    allowed = {
        "author",
        "reason",
        "stage",
        "confidence",
        "iteration",
        "tool",
        "timestamp",
        "fields",
    }
    unknown = set(value) - allowed
    if unknown:
        raise Refusal(
            RefusalStage.SHAPE,
            f"{path} has unknown fields {sorted(unknown)!r}",
        )
    strings = {
        name: _string(value[name], f"{path}.{name}") if name in value else None
        for name in ("author", "reason", "stage", "tool", "timestamp")
    }
    confidence = value.get("confidence")
    if confidence is not None and (
        isinstance(confidence, bool) or not isinstance(confidence, int | float)
    ):
        raise Refusal(
            RefusalStage.CONSTRUCTION,
            f"{path}.confidence must be numeric",
        )
    iteration = (
        _integer(value["iteration"], f"{path}.iteration")
        if "iteration" in value
        else None
    )
    fields = value.get("fields", {})
    if not isinstance(fields, dict):
        raise Refusal(
            RefusalStage.CONSTRUCTION,
            f"{path}.fields must be an object",
        )
    try:
        return EditAnnotations(
            author=strings["author"],
            reason=strings["reason"],
            stage=strings["stage"],
            confidence=cast(float | None, confidence),
            iteration=iteration,
            tool=strings["tool"],
            timestamp=strings["timestamp"],
            fields=cast(dict[str, JsonValue], fields),
        )
    except (TypeError, ValueError) as error:
        raise Refusal(RefusalStage.VALUE, f"{path}: {error}") from error


__all__ = [
    "PATCH_VERSION",
    "Patch",
    "PatchOperation",
    "apply_patch",
    "compose_patches",
    "invert_patch",
    "load_patch",
    "patch_dumps",
    "patch_loads",
]
