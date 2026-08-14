"""The reference opcode machine satisfies the reusable machine laws."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from tests.conformance.machine import MachineLawSuite
from tiergraph import (
    MACHINE_VERSION,
    AddItem,
    AsBuilt,
    AttachValue,
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    BoundarySide,
    DeclareAttribute,
    DeclareRelation,
    DeclareTier,
    DurableItemRef,
    DurablePositionRef,
    Graph,
    ItemRef,
    PositionRef,
    Program,
    PromoteItem,
    PromotePosition,
    QualifiedName,
    Relate,
    RelationInstance,
    Repeat,
    SimpleRelationDeclaration,
    TierDeclaration,
    XsdType,
)
from tiergraph.machine import AttributeTarget, Opcode


def build_program(opcodes: tuple[Opcode, ...]) -> Program:
    """Construct the reference program behind the conformance boundary."""
    return Program(opcodes)


LAWS = MachineLawSuite(build_program)


@pytest.mark.parametrize(
    "law",
    [
        LAWS.check_primitive_trace_executes,
        LAWS.check_as_built_is_fixed_point,
        LAWS.check_procedures_lower_identically,
        LAWS.check_deep_procedure_terminates,
        LAWS.check_refusal_names_opcode,
        LAWS.check_invalidity_classes,
        LAWS.check_fingerprint_ignores_source_procedure,
        LAWS.check_attach_value,
    ],
    ids=lambda law: law.__name__,
)
def test_machine_law(law: object) -> None:
    """Run each reusable law against the reference machine."""
    assert callable(law)
    law()


def test_repeat_refuses_non_finite_expansion() -> None:
    """The procedure constructor rejects counts that cannot prove termination."""
    with pytest.raises(ValueError, match="repeat count -1"):
        Repeat(-1, ())
    with pytest.raises(ValueError, match="repeat count True"):
        Repeat(True, ())


def test_machine_version_is_independent_public_data() -> None:
    """Machine behavior carries its own version instead of the package stamp."""
    outcome = build_program(LAWS.declarations()).unroll()
    assert outcome.to_data()["machine_version"] == MACHINE_VERSION
    json.dumps(outcome.to_data(), allow_nan=False)


def test_fingerprint_is_stable_across_hash_seeds() -> None:
    """Separate interpreters with different hash state emit identical fingerprints."""
    script = """from tiergraph import *
n=QualifiedName('urn:s','t')
p=Program((DeclareNamespace(NamespaceDeclaration('s','urn:s')),DeclareTier(TierDeclaration(n,'Tier')),AddItem(n)))
print(p.fingerprint())
"""
    fingerprints = []
    for seed in ("0", "12345", "999"):
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = seed
        completed = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        fingerprints.append(completed.stdout.strip())
    assert len(set(fingerprints)) == 1


def test_all_attribute_domains_are_checked_transitions() -> None:
    """Every kernel attribute owner is reachable only through validated replacement."""
    name = LAWS.name
    tier = name("events")
    link = BipartiteRelationDeclaration(name("link"), name("event"), name("event"))
    domains = tuple(AttributeDomain)
    declarations: tuple[Opcode, ...] = tuple(
        DeclareAttribute(
            AttributeDeclaration(name(domain.value), domain, XsdType.STRING)
        )
        for domain in domains
    )

    def value(domain: AttributeDomain, lexical: str = "v") -> AttributeValue:
        return AttributeValue(name(domain.value), XsdType.STRING, lexical)

    program = build_program(
        (
            *LAWS.declarations(),
            *declarations,
            DeclareAttribute(
                AttributeDeclaration(
                    name("position-second"), AttributeDomain.POSITION, XsdType.STRING
                )
            ),
            AddItem(tier),
            DeclareRelation(link),
            Relate(RelationInstance(link.name, ItemRef(tier, 0), ItemRef(tier, 0))),
            AttachValue(
                AttributeDomain.DOCUMENT, None, value(AttributeDomain.DOCUMENT)
            ),
            AttachValue(AttributeDomain.TIER, tier, value(AttributeDomain.TIER)),
            AttachValue(
                AttributeDomain.ITEM, ItemRef(tier, 0), value(AttributeDomain.ITEM)
            ),
            AttachValue(
                AttributeDomain.RELATION_DECLARATION,
                name("members"),
                value(AttributeDomain.RELATION_DECLARATION),
            ),
            AttachValue(
                AttributeDomain.RELATION_DECLARATION,
                link.name,
                value(AttributeDomain.RELATION_DECLARATION, "b"),
            ),
            AttachValue(
                AttributeDomain.RELATION_INSTANCE,
                0,
                value(AttributeDomain.RELATION_INSTANCE),
            ),
            AttachValue(
                AttributeDomain.POSITION,
                PositionRef(tier, 0),
                value(AttributeDomain.POSITION),
            ),
            AttachValue(
                AttributeDomain.POSITION,
                PositionRef(tier, 1),
                value(AttributeDomain.POSITION, "other"),
            ),
            AttachValue(
                AttributeDomain.POSITION,
                PositionRef(tier, 1),
                AttributeValue(name("position-second"), XsdType.STRING, "second"),
            ),
        )
    )
    outcome = program.unroll()
    assert outcome.graph.attributes == (value(AttributeDomain.DOCUMENT),)
    assert len(outcome.graph.position_values[1].attributes) == 2
    for opcode in program.opcodes:
        json.dumps(opcode.to_data(), allow_nan=False)


@pytest.mark.parametrize(
    ("domain", "target", "reason"),
    [
        (AttributeDomain.DOCUMENT, 0, "must be None"),
        (AttributeDomain.TIER, 0, "must be a qualified name"),
        (
            AttributeDomain.TIER,
            QualifiedName("urn:machine-test", "missing"),
            "is not declared",
        ),
        (AttributeDomain.ITEM, 0, "must be an item reference"),
        (AttributeDomain.RELATION_DECLARATION, 0, "must be a qualified name"),
        (
            AttributeDomain.RELATION_DECLARATION,
            QualifiedName("urn:machine-test", "missing"),
            "is not declared",
        ),
        (AttributeDomain.RELATION_INSTANCE, 0, "not an existing relation index"),
        (AttributeDomain.POSITION, 0, "must be a position reference"),
    ],
)
def test_attribute_target_refusals_name_the_target(
    domain: AttributeDomain,
    target: AttributeTarget,
    reason: str,
) -> None:
    """Near-valid attachment targets discriminate each domain guard."""
    opcode = AttachValue(
        domain, target, AttributeValue(LAWS.name("label"), XsdType.STRING, "v")
    )
    with pytest.raises(ValueError, match=reason):
        build_program((*LAWS.declarations(), opcode)).unroll()


def test_as_built_identity_rejects_false_trace_and_foreign_comparands() -> None:
    """Outcome identity checks denotation and shares equality with its fingerprint."""
    outcome = build_program(LAWS.declarations()).unroll()
    assert hash(outcome) == hash(outcome.fingerprint())
    assert hash(build_program(LAWS.declarations())) == hash(outcome)
    assert outcome != object()
    assert build_program(()) != object()
    with pytest.raises(ValueError, match="does not execute to its graph"):
        AsBuilt(Graph((), (), ()), outcome.trace)


def test_relation_declaration_opcode_serializes_both_kernel_kinds() -> None:
    """The primitive declaration opcode admits both relation shapes."""
    name = LAWS.name
    simple = DeclareRelation(
        SimpleRelationDeclaration(name("s"), name("t"), name("type"))
    )
    bipartite = DeclareRelation(
        BipartiteRelationDeclaration(name("b"), name("type"), name("type"))
    )
    tier = DeclareTier(TierDeclaration(name("t"), "Tier"))
    for opcode in (simple, bipartite, tier, Repeat(0, (AddItem(name("t")),))):
        json.dumps(opcode.to_data(), allow_nan=False)


def test_promoted_references_drive_later_checked_operations() -> None:
    """Durable item and boundary identity remain usable by later opcodes."""
    tier = LAWS.name("events")
    item_value = AttributeValue(LAWS.name("label"), XsdType.STRING, "named")
    position_name = LAWS.name("position-label")
    position_value = AttributeValue(position_name, XsdType.STRING, "edge")
    program = build_program(
        (
            *LAWS.declarations(),
            DeclareAttribute(
                AttributeDeclaration(
                    position_name, AttributeDomain.POSITION, XsdType.STRING
                )
            ),
            AddItem(tier),
            PromoteItem(ItemRef(tier, 0), "item-id"),
            PromotePosition(PositionRef(tier, 0), "unused-at-outer-edge"),
            AttachValue(
                AttributeDomain.ITEM,
                DurableItemRef("item-id"),
                item_value,
            ),
            AttachValue(
                AttributeDomain.POSITION,
                DurablePositionRef(tier, BoundarySide.BEFORE),
                position_value,
            ),
        )
    )
    outcome = program.unroll()
    assert outcome.graph.tiers[0].items[0].attributes == (item_value,)
    assert outcome.graph.position_values[0].attributes == (position_value,)
    json.dumps(program.opcodes[7].to_data(), allow_nan=False)
