"""Reusable execution and lowering laws for opcode machines."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

import pytest

from tiergraph import (
    AddItem,
    AsBuilt,
    AttachValue,
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    BoundaryRef,
    BoundarySide,
    DeclareAttribute,
    DeclareNamespace,
    DeclareRelation,
    DeclareTier,
    DurableBoundaryRef,
    DurableItemRef,
    ExecutionError,
    ItemRef,
    NamespaceDeclaration,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    Program,
    PromoteBoundary,
    PromoteItem,
    QualifiedName,
    Relate,
    RelationEndpointKind,
    RelationInstance,
    RelationSideDeclaration,
    Repeat,
    SimpleRelationDeclaration,
    TierDeclaration,
    XsdType,
    execute,
)
from tiergraph.machine import Opcode

ProgramFactory = Callable[[tuple[Opcode, ...]], Program]


@dataclass(frozen=True)
class MachineLawSuite:
    """Apply machine laws through a replaceable program construction boundary."""

    build: ProgramFactory

    def name(self, local: str) -> QualifiedName:
        """Return a fixture name in the declared test namespace."""
        return QualifiedName("urn:machine-test", local)

    def declarations(self) -> tuple[Opcode, ...]:
        """Return the primitive prefix for a typed tier and typed value."""
        tier = self.name("events")
        return (
            DeclareNamespace(NamespaceDeclaration("m", "urn:machine-test")),
            DeclareTier(TierDeclaration(tier, "Events")),
            DeclareRelation(
                SimpleRelationDeclaration(
                    self.name("members"), tier, self.name("event")
                )
            ),
            DeclareAttribute(
                AttributeDeclaration(
                    self.name("label"), AttributeDomain.ITEM, XsdType.STRING
                )
            ),
        )

    def check_primitive_trace_executes(self) -> None:
        """Every lowered fixture exposes a finite trace that reconstructs its graph."""
        program = self.build((*self.declarations(), AddItem(self.name("events"))))
        outcome = program.unroll()
        assert outcome.trace
        assert AsBuilt(outcome.graph, outcome.trace) == outcome

    def acceptance_cases(self) -> tuple[tuple[Opcode, ...], ...]:
        """Return varied valid traces for differential builder conformance."""
        tier = self.name("events")
        link = self.name("link")
        group = self.name("group")
        side = RelationSideDeclaration(
            (RelationEndpointKind.ITEM,), (tier,), minimum=1, maximum=2
        )
        return (
            self.declarations(),
            (
                *self.declarations(),
                AddItem(tier),
                PromoteItem(ItemRef(tier, 0), "event-0"),
                PromoteBoundary(BoundaryRef(tier, 1), "after-event-0"),
                AttachValue(
                    AttributeDomain.ITEM,
                    DurableItemRef("event-0"),
                    AttributeValue(self.name("label"), XsdType.STRING, "accepted"),
                ),
            ),
            (
                *self.declarations(),
                AddItem(tier),
                AddItem(tier),
                DeclareRelation(
                    BipartiteRelationDeclaration(
                        link, self.name("event"), self.name("event")
                    )
                ),
                Relate(RelationInstance(link, ItemRef(tier, 0), ItemRef(tier, 1))),
            ),
            (
                *self.declarations(),
                AddItem(tier),
                AddItem(tier),
                DeclareRelation(PolyadicRelationDeclaration(group, side, side)),
                Relate(
                    PolyadicRelationInstance(
                        group,
                        (ItemRef(tier, 0),),
                        (ItemRef(tier, 1), ItemRef(tier, 0)),
                    )
                ),
            ),
        )

    def check_linear_builder_matches_reference_on_acceptance(self) -> None:
        """Every representative accepted trace has the reference graph result."""
        for index, trace in enumerate(self.acceptance_cases()):
            outcome = self.build(trace).unroll()
            reference = execute(outcome.trace)
            assert outcome.graph == reference, (
                "linear and reference graph builds diverged "
                f"for acceptance case {index}"
            )

    def check_as_built_is_fixed_point(self) -> None:
        """Lowering an as-built returns the same object."""
        outcome = self.build(
            (*self.declarations(), AddItem(self.name("events")))
        ).unroll()
        assert outcome.unroll() is outcome

    def check_procedures_lower_identically(self) -> None:
        """A visible procedure and its explicit expansion have one outcome."""
        add = AddItem(self.name("events"))
        repeated = self.build((*self.declarations(), Repeat(3, (add,))))
        explicit = self.build((*self.declarations(), add, add, add))
        assert repeated.opcodes != explicit.opcodes
        assert repeated == explicit
        assert repeated.fingerprint() == explicit.fingerprint()

    def check_deep_procedure_terminates(self) -> None:
        """Iterative lowering handles nesting deeper than Python's call stack."""
        opcode: Opcode = AddItem(self.name("events"))
        for _ in range(1500):
            opcode = Repeat(1, (opcode,))
        outcome = self.build((*self.declarations(), opcode)).unroll()
        assert len(outcome.graph.tiers[0].items) == 1

    def check_refusal_names_opcode(self) -> None:
        """A state-dependent refusal reports the first offending operation."""
        program = self.build((*self.declarations(), AddItem(self.name("missing"))))
        with pytest.raises(ExecutionError, match=r"opcode 4 .*missing.*not declared"):
            program.unroll()

    def check_fingerprint_ignores_source_procedure(self) -> None:
        """Fingerprinting observes as-built state rather than source opcodes."""
        add = AddItem(self.name("events"))
        left = self.build((*self.declarations(), Repeat(2, (add,))))
        right = self.build((*self.declarations(), add, add))
        assert left.fingerprint() == right.fingerprint()

    def invalidity_cases(self) -> tuple[tuple[str, Program, str], ...]:
        """Return near-valid programs for each checked kernel transition class."""
        tier = self.name("events")
        other = self.name("other")
        link = BipartiteRelationDeclaration(
            self.name("link"), self.name("event"), self.name("event")
        )
        base = (*self.declarations(), AddItem(tier))
        item_value = AttributeValue(self.name("label"), XsdType.STRING, "x")
        tier_value = AttributeValue(self.name("tier-label"), XsdType.STRING, "x")
        integer_value = AttributeValue(self.name("label"), XsdType.INTEGER, "1")
        guarded = BipartiteRelationDeclaration(
            self.name("guarded"),
            self.name("event"),
            self.name("event"),
            single_parent=True,
            acyclic=True,
        )
        return (
            (
                "duplicate namespace prefix",
                self.build(
                    (
                        *self.declarations(),
                        DeclareNamespace(
                            NamespaceDeclaration("m", "urn:machine-other")
                        ),
                    )
                ),
                "duplicate namespace prefix",
            ),
            (
                "duplicate namespace URI",
                self.build(
                    (
                        *self.declarations(),
                        DeclareNamespace(
                            NamespaceDeclaration("other", "urn:machine-test")
                        ),
                    )
                ),
                "duplicate namespace URI",
            ),
            (
                "duplicate tier declaration",
                self.build((*base, DeclareTier(TierDeclaration(tier, "Again")))),
                "duplicate tier",
            ),
            (
                "unknown item tier",
                self.build((*self.declarations(), AddItem(other))),
                "not declared",
            ),
            (
                "duplicate durable identity",
                self.build(
                    (
                        *base,
                        PromoteItem(ItemRef(tier, 0), "same"),
                        AddItem(tier),
                        PromoteItem(ItemRef(tier, 1), "same"),
                    )
                ),
                "duplicate durable id",
            ),
            (
                "durable id reused across kinds",
                self.build(
                    (
                        *base,
                        PromoteItem(ItemRef(tier, 0), "same"),
                        DeclareRelation(link),
                        Relate(
                            RelationInstance(
                                link.name,
                                ItemRef(tier, 0),
                                ItemRef(tier, 0),
                                "same",
                            )
                        ),
                    )
                ),
                "duplicate durable id",
            ),
            (
                "relation declaration undeclared namespace",
                self.build(
                    (
                        *base,
                        DeclareRelation(
                            BipartiteRelationDeclaration(
                                QualifiedName("urn:missing", "link"),
                                self.name("event"),
                                self.name("event"),
                            )
                        ),
                    )
                ),
                "uses undeclared namespace",
            ),
            (
                "simple relation undeclared tier",
                self.build(
                    (
                        *base,
                        DeclareRelation(
                            SimpleRelationDeclaration(
                                self.name("missing-members"),
                                other,
                                self.name("event"),
                            )
                        ),
                    )
                ),
                "names undeclared tier",
            ),
            (
                "multiple simple relations",
                self.build(
                    (
                        *base,
                        DeclareRelation(
                            SimpleRelationDeclaration(
                                self.name("other-members"),
                                tier,
                                self.name("event"),
                            )
                        ),
                    )
                ),
                "multiple simple relations",
            ),
            (
                "duplicate relation declaration",
                self.build(
                    (
                        *base,
                        DeclareRelation(
                            SimpleRelationDeclaration(
                                self.name("members"), tier, self.name("event")
                            )
                        ),
                    )
                ),
                "duplicate relation declaration",
            ),
            (
                "undeclared relation",
                self.build(
                    (
                        *base,
                        Relate(
                            RelationInstance(
                                self.name("missing"), ItemRef(tier, 0), ItemRef(tier, 0)
                            )
                        ),
                    )
                ),
                "bipartite relation declaration is required",
            ),
            (
                "wrong relation endpoint",
                self.build(
                    (
                        *base,
                        DeclareTier(TierDeclaration(other, "Other")),
                        DeclareRelation(
                            SimpleRelationDeclaration(
                                self.name("others"), other, self.name("other-type")
                            )
                        ),
                        AddItem(other),
                        DeclareRelation(link),
                        Relate(
                            RelationInstance(
                                link.name, ItemRef(other, 0), ItemRef(tier, 0)
                            )
                        ),
                    )
                ),
                "left endpoint",
            ),
            (
                "wrong endpoint kind",
                self.build(
                    (
                        *base,
                        DeclareRelation(
                            BipartiteRelationDeclaration(
                                self.name("boundary-link"),
                                self.name("event"),
                                self.name("event"),
                                right_endpoint=RelationEndpointKind.BOUNDARY,
                            )
                        ),
                        Relate(
                            RelationInstance(
                                self.name("boundary-link"),
                                ItemRef(tier, 0),
                                ItemRef(tier, 0),
                            )
                        ),
                    )
                ),
                "declaration requires a boundary",
            ),
            (
                "missing boundary anchor",
                self.build(
                    (
                        *base,
                        DeclareRelation(
                            BipartiteRelationDeclaration(
                                self.name("boundary-link"),
                                self.name("event"),
                                self.name("event"),
                                right_endpoint=RelationEndpointKind.BOUNDARY,
                            )
                        ),
                        Relate(
                            RelationInstance(
                                self.name("boundary-link"),
                                ItemRef(tier, 0),
                                DurableBoundaryRef(
                                    DurableItemRef("missing"), BoundarySide.BEFORE
                                ),
                            )
                        ),
                    )
                ),
                "names missing anchor item",
            ),
            (
                "single parent",
                self.build(
                    (
                        *self.declarations(),
                        AddItem(tier),
                        AddItem(tier),
                        AddItem(tier),
                        DeclareRelation(guarded),
                        Relate(
                            RelationInstance(
                                guarded.name, ItemRef(tier, 0), ItemRef(tier, 2)
                            )
                        ),
                        Relate(
                            RelationInstance(
                                guarded.name, ItemRef(tier, 1), ItemRef(tier, 2)
                            )
                        ),
                    )
                ),
                "a second parent",
            ),
            (
                "acyclic",
                self.build(
                    (
                        *self.declarations(),
                        AddItem(tier),
                        AddItem(tier),
                        DeclareRelation(guarded),
                        Relate(
                            RelationInstance(
                                guarded.name, ItemRef(tier, 0), ItemRef(tier, 1)
                            )
                        ),
                        Relate(
                            RelationInstance(
                                guarded.name, ItemRef(tier, 1), ItemRef(tier, 0)
                            )
                        ),
                    )
                ),
                "closes a cycle",
            ),
            (
                "duplicate attribute declaration",
                self.build(
                    (
                        *base,
                        DeclareAttribute(
                            AttributeDeclaration(
                                self.name("label"),
                                AttributeDomain.ITEM,
                                XsdType.STRING,
                            )
                        ),
                    )
                ),
                "duplicate attribute declaration",
            ),
            (
                "attribute declaration undeclared namespace",
                self.build(
                    (
                        *base,
                        DeclareAttribute(
                            AttributeDeclaration(
                                QualifiedName("urn:missing", "label"),
                                AttributeDomain.ITEM,
                                XsdType.STRING,
                            )
                        ),
                    )
                ),
                "uses undeclared namespace",
            ),
            (
                "undeclared attribute",
                self.build(
                    (
                        *base,
                        AttachValue(
                            AttributeDomain.ITEM,
                            ItemRef(tier, 0),
                            AttributeValue(self.name("missing"), XsdType.STRING, "x"),
                        ),
                    )
                ),
                "is undeclared",
            ),
            (
                "attribute domain",
                self.build(
                    (
                        *base,
                        DeclareAttribute(
                            AttributeDeclaration(
                                self.name("tier-label"),
                                AttributeDomain.TIER,
                                XsdType.STRING,
                            )
                        ),
                        AttachValue(AttributeDomain.ITEM, ItemRef(tier, 0), tier_value),
                    )
                ),
                "cannot occur on 'item'",
            ),
            (
                "attribute type",
                self.build(
                    (
                        *base,
                        AttachValue(
                            AttributeDomain.ITEM, ItemRef(tier, 0), integer_value
                        ),
                    )
                ),
                "value has type 'integer'",
            ),
            (
                "duplicate attribute value",
                self.build(
                    (
                        *base,
                        AttachValue(AttributeDomain.ITEM, ItemRef(tier, 0), item_value),
                        AttachValue(AttributeDomain.ITEM, ItemRef(tier, 0), item_value),
                    )
                ),
                "duplicate attribute value",
            ),
            (
                "boundary promotion",
                self.build((*base, PromoteBoundary(BoundaryRef(tier, 2), "position"))),
                "is outside tier",
            ),
        )

    def check_invalidity_classes(self) -> None:
        """Each invalid transition class stops at and names its own opcode."""
        for label, program, reason in self.invalidity_cases():
            with pytest.raises(ExecutionError, match=re.escape(reason)) as caught:
                program.unroll()
            assert "opcode " in str(caught.value), label

    def check_attach_value(self) -> None:
        """A declared typed value attaches through a checked item transition."""
        tier = self.name("events")
        value = AttributeValue(self.name("label"), XsdType.STRING, "hit")
        outcome = self.build(
            (
                *self.declarations(),
                AddItem(tier),
                AttachValue(AttributeDomain.ITEM, ItemRef(tier, 0), value),
            )
        ).unroll()
        assert outcome.graph.tiers[0].items[0].attributes == (value,)
