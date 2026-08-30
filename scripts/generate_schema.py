"""Generate and verify the committed schema and its versioned stamp."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from tiergraph import __version__
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


def committed_stamp() -> object:
    """Read the comparison baseline from the current commit, not regenerated files."""
    try:
        result = subprocess.run(
            ["git", "show", f"HEAD:{STAMP_PATH.as_posix()}"],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as error:
        raise ValueError("committed schema stamp is unavailable") from error
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ValueError("committed schema stamp is not valid JSON") from error


def refuse_unversioned_shape_change(
    baseline: object, current: object, declared: str = __version__
) -> None:
    """Require the release named by a changed format stamp to have been taken."""
    if not isinstance(baseline, dict) or not isinstance(current, dict):
        raise ValueError("schema stamp must be an object")
    changed = any(
        baseline.get(field) != current.get(field)
        for field in ("shape_sha256", "schema_sha256")
    )
    if (
        changed
        and baseline.get("format_version") == current.get("format_version")
        and declared != current.get("format_version")
    ):
        raise ValueError(
            "schema declaration or generated artifact changed without moving "
            "the package to the release named by FORMAT_VERSION; codec semantics "
            "are not checked and require a manual version decision"
        )


def main(
    argv: list[str] | None = None,
    baseline: object | None = None,
    declared: str = __version__,
) -> int:
    """Write artifacts or check that the committed copies match generation."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args(argv)
    schema_bytes = generated_bytes()
    expected_stamp = stamp_bytes(schema_bytes)
    current = json.loads(expected_stamp)
    try:
        refuse_unversioned_shape_change(
            committed_stamp() if baseline is None else baseline, current, declared
        )
    except ValueError as error:
        raise SystemExit(str(error)) from error
    if arguments.check:
        if SCHEMA_PATH.read_bytes() != schema_bytes:
            raise SystemExit(f"{SCHEMA_PATH} is stale; regenerate it")
        if STAMP_PATH.read_bytes() != expected_stamp:
            raise SystemExit(
                f"{STAMP_PATH} does not match the declaration and artifact"
            )
        return 0
    SCHEMA_PATH.parent.mkdir(exist_ok=True)
    SCHEMA_PATH.write_bytes(schema_bytes)
    STAMP_PATH.write_bytes(expected_stamp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
