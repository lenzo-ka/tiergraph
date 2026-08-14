"""Witness weighted mix correspondence without promoting links to items."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    Graph,
    Item,
    ItemRef,
    NamespaceDeclaration,
    QualifiedName,
    RelationInstance,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    XsdType,
)

NS = "urn:tiergraph:witness:mix-correspondence"
NAMESPACES = (NamespaceDeclaration("mix", NS),)
SOURCE_TIER = QualifiedName(NS, "source")
PLACEMENT_TIER = QualifiedName(NS, "placement")
SOURCE_TYPE = QualifiedName(NS, "source-type")
PLACEMENT_TYPE = QualifiedName(NS, "placement-type")
SOURCES = QualifiedName(NS, "sources")
PLACEMENTS = QualifiedName(NS, "placements")
CORRESPONDENCE = QualifiedName(NS, "source-placement")
WEIGHT = QualifiedName(NS, "gain")


def weight(lexical: str, value_type: XsdType = XsdType.DECIMAL) -> AttributeValue:
    """Construct one relation-instance gain in its declared lexical type."""
    return AttributeValue(WEIGHT, value_type, lexical)


def edge(left: int, right: int, lexical: str) -> RelationInstance:
    """Construct one source-to-placement correspondence instance."""
    return RelationInstance(
        CORRESPONDENCE,
        ItemRef(SOURCE_TIER, left),
        ItemRef(PLACEMENT_TIER, right),
        attributes=(weight(lexical),),
    )


def graph_with(relations: tuple[RelationInstance, ...]) -> Graph:
    """Build the page-sized mix fixture through the public kernel constructor."""
    tiers = (
        Tier(TierDeclaration(SOURCE_TIER, "Sources"), (Item(), Item())),
        Tier(
            TierDeclaration(PLACEMENT_TIER, "Placements"),
            (Item(), Item(), Item()),
        ),
    )
    declarations = (
        SimpleRelationDeclaration(SOURCES, SOURCE_TIER, SOURCE_TYPE),
        SimpleRelationDeclaration(PLACEMENTS, PLACEMENT_TIER, PLACEMENT_TYPE),
        BipartiteRelationDeclaration(CORRESPONDENCE, SOURCE_TYPE, PLACEMENT_TYPE),
    )
    attributes = (
        AttributeDeclaration(
            WEIGHT, AttributeDomain.RELATION_INSTANCE, XsdType.DECIMAL
        ),
    )
    return Graph(NAMESPACES, tiers, declarations, relations, attributes)


@dataclass
class EndpointVisits:
    """Count endpoint references examined while traversing relations."""

    count: int = 0

    def examine(self, endpoint: ItemRef) -> ItemRef:
        """Return one endpoint after recording its examination."""
        self.count += 1
        return endpoint

    def relation(self, relation: RelationInstance) -> tuple[ItemRef, ItemRef]:
        """Traverse both endpoints of one relation instance."""
        return self.examine(relation.left), self.examine(relation.right)


def recover(
    graph: Graph, visits: EndpointVisits | None = None
) -> tuple[tuple[int, int, str], ...]:
    """Recover links in the kernel's derived tier-major endpoint order."""
    visits = visits or EndpointVisits()
    rank = {reference: index for index, reference in enumerate(graph.canonical_items())}
    correspondences = (
        relation
        for relation in graph.relations
        if relation.declaration == CORRESPONDENCE
    )
    examined = (
        (
            *visits.relation(relation),
            relation.attributes[0].lexical,
        )
        for relation in correspondences
    )
    ordered = sorted(
        examined,
        key=lambda recovered: (rank[recovered[0]], rank[recovered[1]]),
    )
    return tuple((left.index, right.index, lexical) for left, right, lexical in ordered)


ORACLE = (
    (0, 0, "1.0"),
    (0, 1, "0.5"),
    (1, 0, "0.125"),
    (1, 1, "0.25"),
    (1, 2, "0.75"),
)


def fixture() -> Graph:
    """Store the hand-listed oracle links in an intentionally different order."""
    return graph_with(
        (
            edge(1, 2, "0.75"),
            edge(0, 1, ".5"),
            edge(1, 1, ".25"),
            edge(0, 0, "1"),
            edge(1, 0, ".125"),
        )
    )


def test_page_sized_oracle_recovers_every_weight_in_derived_order() -> None:
    """The implementation must recover the independently enumerated relation set."""
    graph = fixture()
    assert recover(graph) == ORACLE
    assert tuple(
        (relation.left.index, relation.right.index) for relation in graph.relations
    ) != tuple((left, right) for left, right, _weight in ORACLE)
    assert len({(left, right) for left, right, _weight in recover(graph)}) == len(
        graph.relations
    )


@pytest.mark.parametrize(
    ("bad_edge", "offender"),
    [
        (edge(2, 0, "1"), r"relation instance 0 left endpoint.*outside tier"),
        (
            RelationInstance(
                CORRESPONDENCE,
                ItemRef(PLACEMENT_TIER, 0),
                ItemRef(PLACEMENT_TIER, 1),
                attributes=(weight("1"),),
            ),
            r"relation instance 0 left endpoint.*placement-type.*source-type",
        ),
    ],
)
def test_undeclared_endpoints_are_refused_by_name(
    bad_edge: RelationInstance, offender: str
) -> None:
    """Out-of-range and wrong-tier endpoints fail while the graph is constructed."""
    with pytest.raises(ValueError, match=offender):
        graph_with((bad_edge,))


def test_wrong_weight_shape_is_refused_at_graph_construction() -> None:
    """A value claiming the foreign XSD type never enters a readable graph."""
    bad = RelationInstance(
        CORRESPONDENCE,
        ItemRef(SOURCE_TIER, 0),
        ItemRef(PLACEMENT_TIER, 0),
        attributes=(weight("1.0", XsdType.DOUBLE),),
    )
    with pytest.raises(ValueError, match=r"gain.*decimal.*double"):
        graph_with((bad,))


@dataclass(frozen=True)
class DirectAccount:
    """Expose the state and incidence terms of direct correspondence recovery."""

    left_size: int
    right_size: int
    relation_incidence: int
    states: int
    endpoint_visits: int
    recovered: int


def account(graph: Graph) -> DirectAccount:
    """Measure direct recovery without an output cap or hidden expansion."""
    left_size = len(graph.tiers[0].items)
    right_size = len(graph.tiers[1].items)
    relations = tuple(
        relation
        for relation in graph.relations
        if relation.declaration == CORRESPONDENCE
    )
    visits = EndpointVisits()
    recovered = recover(graph, visits)
    return DirectAccount(
        left_size,
        right_size,
        len(relations),
        left_size + right_size,
        visits.count,
        len(recovered),
    )


def test_direct_bounds_and_dense_correspondence_have_no_truncation() -> None:
    """States are additive, incidence is linear, and dense output remains complete."""
    dense = graph_with(
        tuple(
            edge(left, right, f"{left + 1}.{right + 1}")
            for right in range(3)
            for left in range(2)
        )
    )
    measured = account(dense)
    assert measured.left_size == 2
    assert measured.right_size == 3
    assert measured.relation_incidence == 6
    assert measured.states == measured.left_size + measured.right_size == 5
    assert measured.endpoint_visits == 2 * measured.relation_incidence == 12
    assert measured.recovered == measured.relation_incidence == 6
    assert measured.relation_incidence == measured.left_size * measured.right_size


@dataclass(frozen=True)
class ReifiedAccount:
    """Measure the third-tier representation's extra states and traversal hops."""

    states: int
    relation_incidence: int
    endpoint_visits: int
    recovered: tuple[tuple[int, int], ...]


def reified_account(graph: Graph) -> ReifiedAccount:
    """Encode every direct link as an item joined by two declared relations."""
    direct = tuple(
        relation
        for relation in graph.relations
        if relation.declaration == CORRESPONDENCE
    )
    link_tier = QualifiedName(NS, "correspondence-item")
    link_type = QualifiedName(NS, "correspondence-item-type")
    link_members = QualifiedName(NS, "correspondence-items")
    source_to_link = QualifiedName(NS, "source-to-correspondence")
    link_to_placement = QualifiedName(NS, "correspondence-to-placement")
    tiers = (
        *graph.tiers,
        Tier(
            TierDeclaration(link_tier, "Correspondence items"),
            tuple(Item() for _ in direct),
        ),
    )
    declarations = (
        graph.relation_declarations[0],
        graph.relation_declarations[1],
        SimpleRelationDeclaration(link_members, link_tier, link_type),
        BipartiteRelationDeclaration(source_to_link, SOURCE_TYPE, link_type),
        BipartiteRelationDeclaration(link_to_placement, link_type, PLACEMENT_TYPE),
    )
    first_hop = tuple(
        RelationInstance(source_to_link, relation.left, ItemRef(link_tier, index))
        for index, relation in enumerate(direct)
    )
    second_hop = tuple(
        RelationInstance(link_to_placement, ItemRef(link_tier, index), relation.right)
        for index, relation in enumerate(direct)
    )
    reified = Graph(NAMESPACES, tiers, declarations, (*first_hop, *second_hop))
    visits = EndpointVisits()
    source_links: dict[ItemRef, list[ItemRef]] = {}
    link_placements: dict[ItemRef, ItemRef] = {}
    for relation in reified.relations:
        left, right = visits.relation(relation)
        if relation.declaration == source_to_link:
            source_links.setdefault(left, []).append(right)
        elif relation.declaration == link_to_placement:
            link_placements[left] = right
    recovered = tuple(
        sorted(
            (source.index, link_placements[link].index)
            for source, links in source_links.items()
            for link in links
        )
    )
    incidence = len(reified.relations)
    return ReifiedAccount(
        sum(len(tier.items) for tier in reified.tiers),
        incidence,
        visits.count,
        recovered,
    )


def test_reified_links_cost_a_tier_and_two_hops_per_correspondence() -> None:
    """The working alternative adds one state and doubles traversal per link."""
    direct = account(fixture())
    reified = reified_account(fixture())
    assert reified.states == direct.states + direct.relation_incidence == 10
    assert reified.relation_incidence == 2 * direct.relation_incidence == 10
    assert reified.endpoint_visits == 2 * direct.endpoint_visits == 20
    assert reified.recovered == tuple(
        (left, right) for left, right, _ in recover(fixture())
    )


def endpoint_weights(relations: tuple[RelationInstance, ...]) -> dict[ItemRef, str]:
    """Attempt the endpoint-held representation and refuse its first collision."""
    assigned: dict[ItemRef, str] = {}
    for relation in relations:
        lexical = relation.attributes[0].lexical
        previous = assigned.get(relation.left)
        if previous is not None and previous != lexical:
            raise ValueError(
                f"source endpoint {relation.left.to_data()!r} needs both "
                f"weights {previous!r} and {lexical!r}"
            )
        assigned[relation.left] = lexical
    return assigned


def test_endpoint_held_weight_cannot_represent_distinct_link_weights() -> None:
    """One source with two gains forces a collision unless the source is duplicated."""
    graph = fixture()
    source_zero_links = tuple(
        relation for relation in graph.relations if relation.left.index == 0
    )
    with pytest.raises(
        ValueError, match=r"source endpoint.*needs both weights '0.5' and '1.0'"
    ):
        endpoint_weights(source_zero_links)
    assert len(source_zero_links) == 2
    assert len({relation.attributes[0].lexical for relation in source_zero_links}) == 2
