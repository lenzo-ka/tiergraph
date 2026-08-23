"""Encode and decode a checked, standalone JSON document graph.

This constructs a STANDALONE graph for a JSON document; attaching such a value to an item in another graph needs composition machinery tiergraph does not yet provide.
"""

from __future__ import annotations

from dataclasses import replace

from tiergraph import dump_bytes, json_value_graph, loads
from tiergraph.core import JsonValue


def main() -> int:
    """Round-trip a JSON document and show refusal of a non-finite double."""
    document: JsonValue = {
        "active": True,
        "alternatives": [
            {"label": "primary", "score": 0.875},
            None,
            2,
        ],
        "name": "tiergraph",
    }
    graph, profile, root = json_value_graph(document)
    wire_bytes = dump_bytes(graph)
    decoded_graph = loads(wire_bytes)
    decoded_profile = replace(profile, graph=decoded_graph)
    print("Document round-trips:", decoded_profile.value(root) == document)

    try:
        json_value_graph(float("inf"))
    except ValueError as error:
        print("Refused non-finite double:", error)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
