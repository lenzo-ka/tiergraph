"""Structured summaries of graph contents."""

from __future__ import annotations

from tiergraph.core import Graph
from tiergraph.wire import FORMAT_VERSION


def graph_summary(graph: Graph) -> dict[str, object]:
    """Return stable document counts and per-declaration graph summaries."""
    return {
        "format_version": FORMAT_VERSION,
        "namespaces": len(graph.namespaces),
        "tiers": len(graph.tiers),
        "items": sum(len(tier.items) for tier in graph.tiers),
        "relation_declarations": len(graph.relation_declarations),
        "binary_relation_instances": len(graph.relations),
        "polyadic_relation_instances": len(graph.polyadic_relations),
        "attribute_declarations": len(graph.attribute_declarations),
        "populated_position_values": len(graph.position_values),
        "document_attributes": len(graph.attributes),
        "tier_summaries": [
            {
                "name": tier.declaration.name,
                "long_name": tier.declaration.long_name,
                "items": len(tier.items),
                "attributes": len(tier.attributes),
            }
            for tier in graph.tiers
        ],
        "relation_summaries": [
            {
                "name": declaration.name,
                "kind": declaration.to_data()["kind"],
            }
            for declaration in graph.relation_declarations
        ],
    }


__all__ = ["graph_summary"]
