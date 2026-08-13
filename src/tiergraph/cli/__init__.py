"""Command line entry point, a thin shell over the public API."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from tiergraph import __version__


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser."""
    parser = argparse.ArgumentParser(prog="tiergraph")
    parser.add_argument("--version", action="store_true", help="print the version")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line. Returns the process exit status."""
    args = build_parser().parse_args(argv)
    if args.version:
        # Serialized rather than printed bare: every result leaves as JSON.
        print(json.dumps({"version": __version__}))
        return 0
    build_parser().print_help()
    return 0
