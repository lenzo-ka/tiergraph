"""Generate and verify the committed schema and its versioned stamp."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from tiergraph.schema import json_schema, shape_hash
from tiergraph.wire import FORMAT_VERSION

SCHEMA_PATH = Path("schema/tiergraph.schema.json")
STAMP_PATH = Path("schema/tiergraph.schema.sha256")


def generated_bytes() -> bytes:
    """Return canonical generated schema bytes."""
    return (
        json.dumps(
            json_schema(FORMAT_VERSION), ensure_ascii=False, indent=2, sort_keys=True
        )
        + "\n"
    ).encode()


def stamp_bytes(schema_bytes: bytes) -> bytes:
    """Bind the artifact digest and declaration shape to its codec version."""
    stamp = {
        "format_version": FORMAT_VERSION,
        "schema_sha256": hashlib.sha256(schema_bytes).hexdigest(),
        "shape_sha256": shape_hash(),
    }
    return (json.dumps(stamp, indent=2, sort_keys=True) + "\n").encode()


def refuse_unversioned_shape_change(prior: object) -> None:
    """Refuse a changed declaration when the codec stamp did not move."""
    if not isinstance(prior, dict):
        raise ValueError("schema stamp must be an object")
    if (
        prior.get("shape_sha256") != shape_hash()
        and prior.get("format_version") == FORMAT_VERSION
    ):
        raise ValueError("wire shape changed without moving FORMAT_VERSION")


def main() -> int:
    """Write artifacts or check that the committed copies match generation."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    schema_bytes = generated_bytes()
    expected_stamp = stamp_bytes(schema_bytes)
    if arguments.check:
        if SCHEMA_PATH.read_bytes() != schema_bytes:
            raise SystemExit(f"{SCHEMA_PATH} is stale; regenerate it")
        if STAMP_PATH.read_bytes() != expected_stamp:
            raise SystemExit(
                f"{STAMP_PATH} does not match the declaration and artifact"
            )
        return 0
    if STAMP_PATH.exists():
        prior = json.loads(STAMP_PATH.read_text())
        try:
            refuse_unversioned_shape_change(prior)
        except ValueError as error:
            raise SystemExit(str(error)) from error
    SCHEMA_PATH.parent.mkdir(exist_ok=True)
    SCHEMA_PATH.write_bytes(schema_bytes)
    STAMP_PATH.write_bytes(expected_stamp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
