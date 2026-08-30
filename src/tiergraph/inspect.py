"""Structured summaries of graph contents."""

from __future__ import annotations

from tiergraph.core import Graph
from tiergraph.wire import FORMAT_VERSION


def graph_summary(graph: Graph) -> dict[str, object]:
    """Return stable document counts and per-declaration graph summaries.

    Qualified names carry their declared expanded spelling, the same
    ``{"namespace", "local_name"}`` data every declaration's ``to_data`` emits,
    so the whole summary is JSON-serializable. The wire's compact
    ``prefix:local`` spelling is deliberately not used: it depends on the
    document's prefix bindings, which are a wire choice rather than graph
    content, and a summary of graph content should not vary with them.
    """
    return {
        "format_version": FORMAT_VERSION,
        "namespaces": len(graph.namespaces),
        "tiers": len(graph.tiers),
        "items": sum(len(tier.items) for tier in graph.tiers),
        "relation_declarations": len(graph.relation_declarations),
        "binary_relation_instances": len(graph.relations),
        "polyadic_relation_instances": len(graph.polyadic_relations),
        "attribute_declarations": len(graph.attribute_declarations),
        "populated_position_values": len(graph.boundary_values),
        "document_attributes": len(graph.attributes),
        "tier_summaries": [
            {
                "name": tier.declaration.name.to_data(),
                "long_name": tier.declaration.long_name,
                "items": len(tier.items),
                "attributes": len(tier.attributes),
            }
            for tier in graph.tiers
        ],
        "relation_summaries": [
            {
                "name": declaration.name.to_data(),
                "kind": declaration.to_data()["kind"],
            }
            for declaration in graph.relation_declarations
        ],
    }


__all__ = ["graph_summary"]
