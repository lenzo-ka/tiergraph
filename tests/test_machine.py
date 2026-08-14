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
    MAX_REPEAT_COUNT,
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
    ExecutionError,
    Graph,
    Item,
    ItemRef,
    NamespaceDeclaration,
    PositionRef,
    Program,
    PromoteItem,
    PromotePosition,
    QualifiedName,
    Relate,
    RelationInstance,
    Repeat,
    SimpleRelationDeclaration,
    Tier,
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
        LAWS.check_fingerprint_ignores_source_procedure,
        LAWS.check_attach_value,
    ],
    ids=lambda law: law.__name__,
)
def test_machine_law(law: object) -> None:
    """Run each reusable law against the reference machine."""
    assert callable(law)
    law()


@pytest.mark.parametrize(
    ("label", "program", "reason"),
    LAWS.invalidity_cases(),
    ids=[case[0] for case in LAWS.invalidity_cases()],
)
def test_kernel_invalidity_class_is_independently_pinned(
    label: str, program: Program, reason: str
) -> None:
    """Each near-valid program names only its independently selected kernel guard."""
    Program(program.opcodes[:-1]).unroll()
    with pytest.raises(ExecutionError, match=reason) as caught:
        program.unroll()
    assert "opcode " in str(caught.value), label


def test_repeat_refuses_non_finite_expansion() -> None:
    """The procedure constructor rejects counts that cannot prove termination."""
    with pytest.raises(ValueError, match="repeat count -1"):
        Repeat(-1, ())
    with pytest.raises(ValueError, match="repeat count True"):
        Repeat(True, ())
    with pytest.raises(
        ValueError,
        match=f"repeat count {MAX_REPEAT_COUNT + 1} exceeds limit {MAX_REPEAT_COUNT}",
    ):
        Repeat(MAX_REPEAT_COUNT + 1, ())


def test_unrecognized_opcode_is_an_indexed_execution_refusal() -> None:
    """Lowering refuses values outside the closed opcode classes at their trace index."""
    with pytest.raises(
        ExecutionError,
        match="opcode 0 .*unrecognized.*object.*unrecognized opcode type",
    ):
        Program((object(),)).unroll()  # type: ignore[arg-type]


def test_opcode_subclass_is_not_a_member_of_the_closed_set() -> None:
    """Runtime membership is exact and cannot be acquired by overriding a primitive."""

    class Forged(AddItem):
        def apply(self, graph: Graph) -> Graph:
            return graph

    with pytest.raises(ExecutionError, match="opcode 0 .*unrecognized.*Forged"):
        Program((Forged(LAWS.name("events")),)).unroll()


def test_execution_revalidates_each_returned_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hostile implementation cannot advance execution with corrupted graph state."""
    tier = Tier(TierDeclaration(LAWS.name("events"), "Events"), (Item(),))
    bad = Graph((NamespaceDeclaration("m", "urn:machine-test"),), (tier,), ())
    object.__setattr__(bad, "tiers", (tier, tier))

    def forged_apply(_opcode: DeclareTier, _graph: Graph) -> Graph:
        return bad

    monkeypatch.setattr(DeclareTier, "apply", forged_apply)
    with pytest.raises(ExecutionError, match="opcode 0 .*duplicate tier"):
        Program((DeclareTier(tier.declaration),)).unroll()


def test_execution_refuses_a_non_graph_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """A recognized primitive must return graph state before execution can advance."""

    def forged_apply(_opcode: AddItem, _graph: Graph) -> object:
        return object()

    monkeypatch.setattr(AddItem, "apply", forged_apply)
    with pytest.raises(
        ExecutionError, match="opcode 0 .*returned 'object', expected Graph"
    ):
        Program((AddItem(LAWS.name("events")),)).unroll()


def test_diagnostic_serialization_failure_stays_indexed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken opcode description cannot escape the indexed refusal boundary."""

    def broken_data(_opcode: AddItem) -> dict[str, object]:
        raise RuntimeError("broken description")

    monkeypatch.setattr(AddItem, "to_data", broken_data)
    with pytest.raises(
        ExecutionError,
        match="opcode 0 .*unserializable.*broken description.*not declared",
    ):
        Program((AddItem(LAWS.name("missing")),)).unroll()


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
