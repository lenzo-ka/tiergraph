"""Graph inspection is available as structured public data."""

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


def test_graph_summary_reports_counts_and_declaration_details() -> None:
    namespace = "urn:inspect"
    tier_name = QualifiedName(namespace, "tokens")
    relation_name = QualifiedName(namespace, "next")
    graph = Graph(
        (NamespaceDeclaration("i", namespace),),
        (Tier(TierDeclaration(tier_name, "Tokens"), (Item(), Item())),),
        (
            SimpleRelationDeclaration(
                relation_name, tier_name, QualifiedName(namespace, "sequence")
            ),
        ),
    )

    assert graph_summary(graph) == {
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
                "name": tier_name,
                "long_name": "Tokens",
                "items": 2,
                "attributes": 0,
            }
        ],
        "relation_summaries": [{"name": relation_name, "kind": "simple"}],
    }
