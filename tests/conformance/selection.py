"""Reusable laws for validated selectors and ordered node sets."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

import pytest

from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeSelector,
    AttributeValue,
    BipartiteRelationDeclaration,
    BoundariesSelector,
    BoundarySelector,
    BoundarySide,
    DurableItemRef,
    DurablePositionRef,
    Graph,
    Item,
    ItemRef,
    ItemSelector,
    ItemsSelector,
    NamespaceDeclaration,
    Node,
    NodeKind,
    NodeSet,
    Position,
    PositionRef,
    QualifiedName,
    RelationEndpointKind,
    RelationInstance,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    TierSelector,
    TypeSelector,
    XsdType,
)
from tiergraph.selection import Selector

SelectionFactory = Callable[[Graph, tuple[Selector, ...]], NodeSet]


@dataclass(frozen=True)
class SelectionLawSuite:
    """Apply selection laws through a replaceable evaluation boundary."""

    select: SelectionFactory
    namespace: str = "urn:test:selection"

    def name(self, local: str) -> QualifiedName:
        """Return a name in the fixture namespace."""
        return QualifiedName(self.namespace, local)

    def graph(self) -> Graph:
        """Return reversed edge storage and shared item types across two tiers."""
        mark = self.name("mark")
        left = self.name("left")
        right = self.name("right")
        shared = self.name("shared")
        members_left = SimpleRelationDeclaration(
            self.name("left-members"), left, shared
        )
        members_right = SimpleRelationDeclaration(
            self.name("right-members"), right, shared
        )
        link = BipartiteRelationDeclaration(self.name("link"), shared, shared)
        value = AttributeValue(mark, XsdType.STRING, "yes")
        tiers = (
            Tier(TierDeclaration(left, "Left"), (Item("left-0"), Item("left-1"))),
            Tier(TierDeclaration(right, "Right"), (Item("right-0"),)),
        )
        # Storage is the reverse of endpoint-derived canonical order.
        relations = (
            RelationInstance(
                link.name,
                ItemRef(left, 1),
                ItemRef(right, 0),
                attributes=(value,),
            ),
            RelationInstance(
                link.name,
                ItemRef(left, 0),
                ItemRef(right, 0),
                attributes=(value,),
            ),
        )
        return Graph(
            (NamespaceDeclaration("s", self.namespace),),
            tiers,
            (members_left, members_right, link),
            relations,
            (
                AttributeDeclaration(
                    mark, AttributeDomain.RELATION_INSTANCE, XsdType.STRING
                ),
            ),
        )

    def check_axes_and_canonical_order(self) -> None:
        """Declared axes normalize route and relation storage order."""
        graph = self.graph()
        selected = self.select(
            graph,
            (
                ItemsSelector(graph, self.name("right")),
                TypeSelector(graph, self.name("shared")),
                TierSelector(graph, self.name("left")),
                AttributeSelector(
                    graph, self.name("mark"), AttributeDomain.RELATION_INSTANCE
                ),
            ),
        )
        assert selected.nodes == (
            Node(NodeKind.TIER, self.name("left")),
            Node(NodeKind.ITEM, ItemRef(self.name("left"), 0)),
            Node(NodeKind.ITEM, ItemRef(self.name("left"), 1)),
            Node(NodeKind.ITEM, ItemRef(self.name("right"), 0)),
            Node(NodeKind.RELATION_INSTANCE, 1),
            Node(NodeKind.RELATION_INSTANCE, 0),
        )

    def check_duplicate_routes_and_set_operations(self) -> None:
        """Overlapping routes deduplicate and union, intersection, and difference compose."""
        graph = self.graph()
        left = ItemsSelector(graph, self.name("left")).evaluate()
        shared = TypeSelector(graph, self.name("shared")).evaluate()
        combined = self.select(
            graph,
            (
                ItemsSelector(graph, self.name("left")),
                TypeSelector(graph, self.name("shared")),
            ),
        )
        assert combined == left | shared
        assert left & shared == left
        assert combined - left == ItemsSelector(graph, self.name("right")).evaluate()
        assert len(combined.nodes) == len(set(combined.nodes))

    def check_refusals_name_offenders(self) -> None:
        """Each unsatisfied selector class refuses while its near-valid peer constructs."""
        graph = self.graph()
        ItemSelector(graph, ItemRef(self.name("left"), 1))
        with pytest.raises(ValueError, match=r"index.*2"):
            ItemSelector(graph, ItemRef(self.name("left"), 2))
        TierSelector(graph, self.name("left"))
        with pytest.raises(ValueError, match="missing"):
            TierSelector(graph, self.name("missing"))
        AttributeSelector(graph, self.name("mark"), AttributeDomain.RELATION_INSTANCE)
        with pytest.raises(ValueError, match=r"mark.*'item'"):
            AttributeSelector(graph, self.name("mark"), AttributeDomain.ITEM)

    def check_boundaries_and_anchors(self) -> None:
        """Nonempty and empty tiers expose outer boundaries and anchored resolution."""
        graph = self.graph()
        boundaries = BoundariesSelector(graph, self.name("left")).evaluate()
        assert boundaries.nodes == tuple(
            Node(NodeKind.POSITION, PositionRef(self.name("left"), index))
            for index in range(3)
        )
        anchored = BoundarySelector(
            graph,
            DurablePositionRef(DurableItemRef("left-1"), BoundarySide.BEFORE),
        ).evaluate()
        assert anchored.nodes == (
            Node(NodeKind.POSITION, PositionRef(self.name("left"), 1)),
        )
        empty_name = self.name("empty")
        empty = Graph(
            graph.namespaces,
            (*graph.tiers, Tier(TierDeclaration(empty_name, "Empty"))),
            graph.relation_declarations,
            graph.relations,
            graph.attribute_declarations,
        )
        assert BoundariesSelector(empty, empty_name).evaluate().nodes == (
            Node(NodeKind.POSITION, PositionRef(empty_name, 0)),
        )
        assert (
            BoundarySelector(
                empty,
                DurablePositionRef(empty_name, BoundarySide.BEFORE),
            ).evaluate()
            == BoundarySelector(
                empty,
                DurablePositionRef(empty_name, BoundarySide.AFTER),
            ).evaluate()
        )

    def check_json_data(self) -> None:
        """Selection results encode as strict JSON in canonical order."""
        graph = self.graph()
        result = TypeSelector(graph, self.name("shared")).evaluate().to_data()
        encoded = json.dumps(
            result, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        assert json.loads(encoded) == result

    def check_every_attribute_domain(self) -> None:
        """Each attribute axis returns its value owners and no unvalued peers."""
        names = {domain: self.name(domain.value) for domain in AttributeDomain}

        def value(domain: AttributeDomain) -> AttributeValue:
            return AttributeValue(names[domain], XsdType.STRING, "yes")

        tier_name = self.name("valued")
        item_type = self.name("valued-type")
        members = SimpleRelationDeclaration(
            self.name("valued-members"),
            tier_name,
            item_type,
            (value(AttributeDomain.RELATION_DECLARATION),),
        )
        link = BipartiteRelationDeclaration(
            self.name("valued-link"), item_type, item_type
        )
        tier = Tier(
            TierDeclaration(tier_name, "Valued"),
            (Item("valued-item", (value(AttributeDomain.ITEM),)),),
            (value(AttributeDomain.TIER),),
        )
        relation = RelationInstance(
            link.name,
            ItemRef(tier_name, 0),
            ItemRef(tier_name, 0),
            attributes=(value(AttributeDomain.RELATION_INSTANCE),),
        )
        position = Position(
            PositionRef(tier_name, 0), (value(AttributeDomain.POSITION),)
        )
        declarations = tuple(
            AttributeDeclaration(names[domain], domain, XsdType.STRING)
            for domain in AttributeDomain
        ) + (
            AttributeDeclaration(
                self.name("unvalued-document"),
                AttributeDomain.DOCUMENT,
                XsdType.STRING,
            ),
        )
        graph = Graph(
            (NamespaceDeclaration("s", self.namespace),),
            (tier,),
            (members, link),
            (relation,),
            declarations,
            (position,),
            (value(AttributeDomain.DOCUMENT),),
        )
        expected = {
            AttributeDomain.DOCUMENT: Node(NodeKind.DOCUMENT, None),
            AttributeDomain.TIER: Node(NodeKind.TIER, tier_name),
            AttributeDomain.ITEM: Node(NodeKind.ITEM, ItemRef(tier_name, 0)),
            AttributeDomain.POSITION: Node(
                NodeKind.POSITION, PositionRef(tier_name, 0)
            ),
            AttributeDomain.RELATION_DECLARATION: Node(
                NodeKind.RELATION_DECLARATION, members.name
            ),
            AttributeDomain.RELATION_INSTANCE: Node(NodeKind.RELATION_INSTANCE, 0),
        }
        for domain, node in expected.items():
            result = AttributeSelector(graph, names[domain], domain).evaluate()
            assert result.nodes == (node,)
            json.dumps(result.to_data(), allow_nan=False)
        assert (
            not AttributeSelector(
                graph, self.name("unvalued-document"), AttributeDomain.DOCUMENT
            )
            .evaluate()
            .nodes
        )

    def check_remaining_construction_guards(self) -> None:
        """Undeclared axes and cross-graph composition refuse their offenders."""
        graph = self.graph()
        with pytest.raises(ValueError, match="absent-type"):
            TypeSelector(graph, self.name("absent-type"))
        with pytest.raises(ValueError, match="absent-attribute"):
            AttributeSelector(
                graph, self.name("absent-attribute"), AttributeDomain.ITEM
            )
        durable = ItemSelector(graph, DurableItemRef("left-0")).evaluate()
        assert durable.nodes == (Node(NodeKind.ITEM, ItemRef(self.name("left"), 0)),)
        other = self.graph()
        with pytest.raises(ValueError, match="same graph"):
            durable | ItemsSelector(other, self.name("left")).evaluate()
        with pytest.raises(ValueError, match="different graph"):
            self.select(graph, (ItemsSelector(other, self.name("left")),))

    def check_boundary_relation_order(self) -> None:
        """Canonical relation ordering resolves anchored boundary endpoints."""
        graph = self.graph()
        boundary_link = BipartiteRelationDeclaration(
            self.name("boundary-link"),
            self.name("shared"),
            self.name("shared"),
            RelationEndpointKind.BOUNDARY,
            RelationEndpointKind.BOUNDARY,
        )
        boundary_relation = RelationInstance(
            boundary_link.name,
            DurablePositionRef(self.name("left"), BoundarySide.BEFORE),
            DurablePositionRef(self.name("right"), BoundarySide.AFTER),
        )
        extended = Graph(
            graph.namespaces,
            graph.tiers,
            (*graph.relation_declarations, boundary_link),
            (*graph.relations, boundary_relation),
            graph.attribute_declarations,
        )
        assert NodeSet(
            extended,
            (Node(NodeKind.RELATION_INSTANCE, 2), Node(NodeKind.DOCUMENT, None)),
        ).nodes == (
            Node(NodeKind.DOCUMENT, None),
            Node(NodeKind.RELATION_INSTANCE, 2),
        )
