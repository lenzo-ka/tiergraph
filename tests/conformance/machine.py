"""Reusable execution and lowering laws for opcode machines."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

import pytest

from tiergraph import (
    AddItem,
    AsBuilt,
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    DeclareAttribute,
    DeclareNamespace,
    DeclareRelation,
    DeclareTier,
    ExecutionError,
    ItemRef,
    NamespaceDeclaration,
    Program,
    PromoteItem,
    QualifiedName,
    Relate,
    RelationInstance,
    Repeat,
    SimpleRelationDeclaration,
    TierDeclaration,
    XsdType,
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
        return (
            (
                "duplicate declaration",
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
        from tiergraph import AttachValue

        outcome = self.build(
            (
                *self.declarations(),
                AddItem(tier),
                AttachValue(AttributeDomain.ITEM, ItemRef(tier, 0), value),
            )
        ).unroll()
        assert outcome.graph.tiers[0].items[0].attributes == (value,)
