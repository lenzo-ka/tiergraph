"""Graph inspection is available as structured public data."""

import json

from tiergraph import (
    FORMAT_VERSION,
    Graph,
    Item,
    NamespaceDeclaration,
    QualifiedName,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    graph_summary,
)


def _summary_graph() -> Graph:
    """Build the smallest graph that carries both summarized name positions."""
    namespace = "urn:inspect"
    tier_name = QualifiedName(namespace, "tokens")
    return Graph(
        (NamespaceDeclaration("i", namespace),),
        (Tier(TierDeclaration(tier_name, "Tokens"), (Item(), Item())),),
        (
            SimpleRelationDeclaration(
                QualifiedName(namespace, "next"),
                tier_name,
                QualifiedName(namespace, "sequence"),
            ),
        ),
    )


def test_graph_summary_reports_counts_and_declaration_details() -> None:
    """Names are reported in the declared expanded spelling, not name objects."""
    assert graph_summary(_summary_graph()) == {
        "format_version": FORMAT_VERSION,
        "namespaces": 1,
        "tiers": 1,
        "items": 2,
        "relation_declarations": 1,
        "binary_relation_instances": 0,
        "polyadic_relation_instances": 0,
        "attribute_declarations": 0,
        "populated_position_values": 0,
        "document_attributes": 0,
        "tier_summaries": [
            {
                "name": {"namespace": "urn:inspect", "local_name": "tokens"},
                "long_name": "Tokens",
                "items": 2,
                "attributes": 0,
            }
        ],
        "relation_summaries": [
            {
                "name": {"namespace": "urn:inspect", "local_name": "next"},
                "kind": "simple",
            }
        ],
    }


def test_graph_summary_is_json_serializable() -> None:
    """A public return that JSON refuses is a broken return, however it reads.

    The expected spellings are written out rather than derived from the graph,
    so this fails on a summary that serializes to some other shape as surely as
    on one that does not serialize at all.
    """
    summary = graph_summary(_summary_graph())

    round_tripped = json.loads(json.dumps(summary))

    tier_summaries = round_tripped["tier_summaries"]
    relation_summaries = round_tripped["relation_summaries"]
    assert tier_summaries[0]["name"] == {
        "namespace": "urn:inspect",
        "local_name": "tokens",
    }
    assert relation_summaries[0]["name"] == {
        "namespace": "urn:inspect",
        "local_name": "next",
    }
