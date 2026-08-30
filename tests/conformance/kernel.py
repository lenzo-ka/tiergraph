"""Construction and refusal laws shared by kernel implementations."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

import pytest

from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    Boundary,
    BoundaryRef,
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

GraphFactory = Callable[..., Graph]


@dataclass(frozen=True)
class KernelLawSuite:
    """Apply kernel laws through a replaceable graph construction boundary."""

    build: GraphFactory

    def name(self, local: str, namespace: str = "urn:test") -> QualifiedName:
        """Return an expanded name used by conformance fixtures."""
        return QualifiedName(namespace, local)

    def typed_graph(self) -> Graph:
        """Return a small graph containing two item types."""
        phon = self.name("phon")
        syllable = self.name("syl")
        tiers = (
            Tier(TierDeclaration(phon, "Phonemic segment"), (Item(), Item())),
            Tier(TierDeclaration(syllable, "Syllable"), (Item(),)),
        )
        declarations = (
            SimpleRelationDeclaration(
                self.name("segments"), phon, self.name("segment")
            ),
            SimpleRelationDeclaration(
                self.name("syllables"), syllable, self.name("syllable")
            ),
        )
        return self.build((NamespaceDeclaration("t", "urn:test"),), tiers, declarations)

    def assert_refuses(self, offender: str, operation: Callable[[], object]) -> None:
        """Require a construction refusal to include its offending value."""
        with pytest.raises(ValueError, match=offender):
            operation()

    def check_boundaries(self) -> None:
        """Every tier owns one more boundary than it has items."""
        graph = self.typed_graph()
        references = tuple(
            boundary.reference for boundary in graph.boundaries(self.name("phon"))
        )
        assert all(isinstance(reference, BoundaryRef) for reference in references)
        assert [cast(BoundaryRef, reference).index for reference in references] == [
            0,
            1,
            2,
        ]

    def check_json_data(self) -> None:
        """The rich graph has a strict JSON-serializable public representation."""
        json.dumps(self.typed_graph().to_data(), sort_keys=True, allow_nan=False)

    def check_endpoint_type_refusal(self) -> None:
        """A near-valid edge with reversed endpoint types is refused."""
        graph = self.typed_graph()
        relation = BipartiteRelationDeclaration(
            self.name("contains"), self.name("syllable"), self.name("segment")
        )
        self.build(
            graph.namespaces,
            graph.tiers,
            (*graph.relation_declarations, relation),
            (
                RelationInstance(
                    relation.name,
                    ItemRef(self.name("syl"), 0),
                    ItemRef(self.name("phon"), 0),
                ),
            ),
        )
        self.assert_refuses(
            "relation instance 0 left endpoint",
            lambda: self.build(
                graph.namespaces,
                graph.tiers,
                (*graph.relation_declarations, relation),
                (
                    RelationInstance(
                        relation.name,
                        ItemRef(self.name("phon"), 0),
                        ItemRef(self.name("syl"), 0),
                    ),
                ),
            ),
        )

    def check_attribute_domains(self) -> None:
        """Each declared attribute domain accepts a value on its matching owner."""
        names = {domain: self.name(domain.value) for domain in AttributeDomain}
        declarations = tuple(
            AttributeDeclaration(name, domain, XsdType.STRING)
            for domain, name in names.items()
        )

        def value(domain: AttributeDomain) -> AttributeValue:
            return AttributeValue(names[domain], XsdType.STRING, "v")

        tier_name = self.name("tier")
        type_name = self.name("type")
        simple = SimpleRelationDeclaration(
            self.name("members"),
            tier_name,
            type_name,
            (value(AttributeDomain.RELATION_DECLARATION),),
        )
        link = BipartiteRelationDeclaration(self.name("link"), type_name, type_name)
        item = Item(attributes=(value(AttributeDomain.ITEM),))
        tier = Tier(
            TierDeclaration(tier_name, "Tier"),
            (item,),
            (value(AttributeDomain.TIER),),
        )
        instance = RelationInstance(
            link.name,
            ItemRef(tier_name, 0),
            ItemRef(tier_name, 0),
            attributes=(value(AttributeDomain.RELATION_INSTANCE),),
        )
        boundary = Boundary(
            BoundaryRef(tier_name, 1), (value(AttributeDomain.BOUNDARY),)
        )
        graph = self.build(
            (NamespaceDeclaration("t", "urn:test"),),
            (tier,),
            (simple, link),
            (instance,),
            declarations,
            (boundary,),
            (value(AttributeDomain.DOCUMENT),),
        )
        assert graph.boundaries(tier_name)[1] == boundary
        json.dumps(graph.to_data(), allow_nan=False)

    def check_single_parent_refusal(self) -> None:
        """A declared local tree refuses a second distinct parent."""
        graph = self.typed_graph()
        relation = BipartiteRelationDeclaration(
            self.name("dominates"),
            self.name("segment"),
            self.name("segment"),
            single_parent=True,
        )
        edges = (
            RelationInstance(
                relation.name,
                ItemRef(self.name("phon"), 0),
                ItemRef(self.name("phon"), 1),
            ),
            RelationInstance(
                relation.name,
                ItemRef(self.name("phon"), 1),
                ItemRef(self.name("phon"), 1),
            ),
        )
        self.build(
            graph.namespaces,
            graph.tiers,
            (*graph.relation_declarations, relation),
            edges[:1],
        )
        self.assert_refuses(
            "relation instance 1",
            lambda: self.build(
                graph.namespaces,
                graph.tiers,
                (*graph.relation_declarations, relation),
                edges,
            ),
        )

    def check_cycle_refusal(self) -> None:
        """A relation promising termination refuses its closing edge."""
        graph = self.typed_graph()
        relation = BipartiteRelationDeclaration(
            self.name("precedes"),
            self.name("segment"),
            self.name("segment"),
            acyclic=True,
        )
        edges = (
            RelationInstance(
                relation.name,
                ItemRef(self.name("phon"), 0),
                ItemRef(self.name("phon"), 1),
            ),
            RelationInstance(
                relation.name,
                ItemRef(self.name("phon"), 1),
                ItemRef(self.name("phon"), 0),
            ),
        )
        self.build(
            graph.namespaces,
            graph.tiers,
            (*graph.relation_declarations, relation),
            edges[:1],
        )
        self.assert_refuses(
            "relation instance 1",
            lambda: self.build(
                graph.namespaces,
                graph.tiers,
                (*graph.relation_declarations, relation),
                edges,
            ),
        )
