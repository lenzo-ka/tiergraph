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
    Boundary,
    BoundaryRef,
    BoundarySelector,
    BoundarySide,
    DurableBoundaryRef,
    DurableItemRef,
    Graph,
    Item,
    ItemRef,
    ItemSelector,
    ItemsSelector,
    NamespaceDeclaration,
    Node,
    NodeKind,
    NodeSet,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    QualifiedName,
    RelationEndpointKind,
    RelationInstance,
    RelationSideDeclaration,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    TierSelector,
    TypeSelector,
    UnionSelector,
    XsdType,
    evaluate_selection,
)
from tiergraph.selection import Selector

SelectionFactory = Callable[[Graph, Selector], NodeSet]


@dataclass(frozen=True)
class SelectionLawSuite:
    """Apply selection laws through a replaceable evaluation boundary."""

    evaluate: SelectionFactory
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
        selected = self.evaluate(
            graph,
            UnionSelector(
                (
                    ItemsSelector(self.name("right")),
                    TypeSelector(self.name("shared")),
                    TierSelector(self.name("left")),
                    AttributeSelector(
                        self.name("mark"), AttributeDomain.RELATION_INSTANCE
                    ),
                )
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
        left = evaluate_selection(graph, ItemsSelector(self.name("left")))
        shared = evaluate_selection(graph, TypeSelector(self.name("shared")))
        combined = self.evaluate(
            graph,
            UnionSelector(
                (
                    ItemsSelector(self.name("left")),
                    TypeSelector(self.name("shared")),
                )
            ),
        )
        assert combined == left | shared
        assert left & shared == left
        assert combined - left == evaluate_selection(
            graph, ItemsSelector(self.name("right"))
        )
        assert len(combined.nodes) == len(set(combined.nodes))

    def check_refusals_name_offenders(self) -> None:
        """Each unsatisfied selector class refuses while its near-valid peer constructs."""
        graph = self.graph()
        evaluate_selection(graph, ItemSelector(ItemRef(self.name("left"), 1)))
        with pytest.raises(ValueError, match=r"left\[2\]"):
            evaluate_selection(graph, ItemSelector(ItemRef(self.name("left"), 2)))
        evaluate_selection(graph, TierSelector(self.name("left")))
        with pytest.raises(ValueError, match="missing"):
            evaluate_selection(graph, TierSelector(self.name("missing")))
        evaluate_selection(
            graph,
            AttributeSelector(self.name("mark"), AttributeDomain.RELATION_INSTANCE),
        )
        with pytest.raises(ValueError, match=r"mark.*'item'"):
            evaluate_selection(
                graph, AttributeSelector(self.name("mark"), AttributeDomain.ITEM)
            )

    def check_boundaries_and_anchors(self) -> None:
        """Nonempty and empty tiers expose outer boundaries and anchored resolution."""
        graph = self.graph()
        boundaries = evaluate_selection(graph, BoundariesSelector(self.name("left")))
        assert boundaries.nodes == tuple(
            Node(NodeKind.BOUNDARY, BoundaryRef(self.name("left"), index))
            for index in range(3)
        )
        anchored_selector = BoundarySelector(
            DurableBoundaryRef(DurableItemRef("left-1"), BoundarySide.BEFORE),
        )
        anchored = evaluate_selection(graph, anchored_selector)
        assert anchored.nodes == (
            Node(NodeKind.BOUNDARY, BoundaryRef(self.name("left"), 1)),
        )
        empty_name = self.name("empty")
        empty = Graph(
            graph.namespaces,
            (*graph.tiers, Tier(TierDeclaration(empty_name, "Empty"))),
            graph.relation_declarations,
            graph.relations,
            graph.attribute_declarations,
        )
        assert evaluate_selection(empty, BoundariesSelector(empty_name)).nodes == (
            Node(NodeKind.BOUNDARY, BoundaryRef(empty_name, 0)),
        )
        assert evaluate_selection(
            empty,
            BoundarySelector(
                DurableBoundaryRef(empty_name, BoundarySide.BEFORE),
            ),
        ) == evaluate_selection(
            empty,
            BoundarySelector(
                DurableBoundaryRef(empty_name, BoundarySide.AFTER),
            ),
        )

    def check_json_data(self) -> None:
        """Selection results encode as strict JSON in canonical order."""
        graph = self.graph()
        result = evaluate_selection(graph, TypeSelector(self.name("shared"))).to_data()
        encoded = json.dumps(
            result, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        assert json.loads(encoded) == result

    def valued_domain_names(self) -> dict[AttributeDomain, QualifiedName]:
        """Return the attribute name this suite values on each declared domain."""
        return {domain: self.name(domain.value) for domain in AttributeDomain}

    def valued_graph(self) -> Graph:
        """Return one graph carrying a value on every declared attribute domain.

        The suite's main fixture declares one attribute on one domain, which is
        enough for the ordering and set-algebra laws but leaves any claim
        quantified over attribute domains resting on a single member. This is
        the fixture that can tell such a claim from the weaker one: it names an
        attribute per domain and values each, with one unvalued document
        attribute besides, so an axis that answered with everything would show.
        """
        names = self.valued_domain_names()

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
        side = RelationSideDeclaration(
            (RelationEndpointKind.ITEM,), tiers=(tier_name,), maximum=None
        )
        correspondence = PolyadicRelationDeclaration(
            self.name("valued-correspondence"), side, side
        )
        polyadic = PolyadicRelationInstance(
            correspondence.name,
            (ItemRef(tier_name, 0),),
            (ItemRef(tier_name, 0),),
            attributes=(value(AttributeDomain.RELATION_INSTANCE),),
        )
        boundary = Boundary(
            BoundaryRef(tier_name, 0), (value(AttributeDomain.BOUNDARY),)
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
        return Graph(
            (NamespaceDeclaration("s", self.namespace),),
            (tier,),
            (members, link, correspondence),
            (relation,),
            declarations,
            (boundary,),
            (value(AttributeDomain.DOCUMENT),),
            (polyadic,),
        )

    def check_every_attribute_domain(self) -> None:
        """Each attribute axis returns its value owners and no unvalued peers."""
        names = self.valued_domain_names()
        graph = self.valued_graph()
        tier_name = self.name("valued")
        expected = {
            AttributeDomain.DOCUMENT: Node(NodeKind.DOCUMENT, None),
            AttributeDomain.TIER: Node(NodeKind.TIER, tier_name),
            AttributeDomain.ITEM: Node(NodeKind.ITEM, ItemRef(tier_name, 0)),
            AttributeDomain.BOUNDARY: Node(
                NodeKind.BOUNDARY, BoundaryRef(tier_name, 0)
            ),
            AttributeDomain.RELATION_DECLARATION: Node(
                NodeKind.RELATION_DECLARATION, self.name("valued-members")
            ),
        }
        for domain, node in expected.items():
            result = evaluate_selection(graph, AttributeSelector(names[domain], domain))
            assert result.nodes == (node,)
            json.dumps(result.to_data(), allow_nan=False)
        # The kernel admits relation-instance values on both instance
        # collections, so the axis must report both carriers.  Written as data
        # so the law states the observable rather than a symbol.
        instance_domain = AttributeDomain.RELATION_INSTANCE
        carriers = evaluate_selection(
            graph, AttributeSelector(names[instance_domain], instance_domain)
        )
        assert carriers.to_data() == [
            {"kind": "relation_instance", "reference": 0},
            {"kind": "polyadic_relation_instance", "reference": 0},
        ]
        json.dumps(carriers.to_data(), allow_nan=False)
        assert not evaluate_selection(
            graph,
            AttributeSelector(self.name("unvalued-document"), AttributeDomain.DOCUMENT),
        ).nodes

    def check_remaining_construction_guards(self) -> None:
        """Undeclared axes and cross-graph composition refuse their offenders."""
        graph = self.graph()
        with pytest.raises(ValueError, match="absent-type"):
            evaluate_selection(graph, TypeSelector(self.name("absent-type")))
        with pytest.raises(ValueError, match="absent-attribute"):
            evaluate_selection(
                graph,
                AttributeSelector(self.name("absent-attribute"), AttributeDomain.ITEM),
            )
        durable = evaluate_selection(graph, ItemSelector(DurableItemRef("left-0")))
        assert durable.nodes == (Node(NodeKind.ITEM, ItemRef(self.name("left"), 0)),)
        other = self.graph()
        with pytest.raises(ValueError, match="same graph"):
            durable | evaluate_selection(other, ItemsSelector(self.name("left")))

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
            DurableBoundaryRef(self.name("left"), BoundarySide.BEFORE),
            DurableBoundaryRef(self.name("right"), BoundarySide.AFTER),
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
