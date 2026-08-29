"""The reference opcode machine satisfies the reusable machine laws."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest

import tiergraph.machine as machine
from tests.conformance.machine import MachineLawSuite
from tiergraph import (
    MACHINE_VERSION,
    MAX_REPEAT_COUNT,
    MAX_TOTAL_OPCODES,
    AddItem,
    AsBuilt,
    AttachValue,
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    BoundarySide,
    DeclareAttribute,
    DeclareNamespace,
    DeclareRelation,
    DeclareTier,
    DurableItemRef,
    DurablePositionRef,
    ExecutionError,
    Graph,
    Item,
    ItemRef,
    NamespaceDeclaration,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    PositionRef,
    Program,
    PromoteItem,
    PromotePosition,
    QualifiedName,
    Relate,
    RelationEndpointKind,
    RelationInstance,
    RelationSideDeclaration,
    Repeat,
    SimpleRelationDeclaration,
    Step,
    Tier,
    TierDeclaration,
    XsdType,
    execute,
    steps,
    wire,
)
from tiergraph.machine import AttributeTarget, Opcode, _flatten


def build_program(opcodes: tuple[Opcode, ...]) -> Program:
    """Construct the reference program behind the conformance boundary."""
    return Program(opcodes)


LAWS = MachineLawSuite(build_program)


def test_normal_unroll_does_not_run_reference_after_linear_acceptance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful production lowering pays only for the linear builder."""

    def unexpected_reference(_trace: object) -> Graph:
        raise AssertionError("reference execution ran after linear acceptance")

    monkeypatch.setattr(machine, "execute", unexpected_reference)
    outcome = Program((*LAWS.declarations(), AddItem(LAWS.name("events")))).unroll()
    assert len(outcome.graph.tiers[0].items) == 1


@pytest.mark.parametrize(
    "law",
    [
        LAWS.check_primitive_trace_executes,
        LAWS.check_linear_builder_matches_reference_on_acceptance,
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


def test_steps_follow_flattened_trace_with_correct_intermediate_graphs() -> None:
    """Each step corresponds exactly to executing its primitive trace prefix."""
    declarations = LAWS.declarations()
    add = AddItem(LAWS.name("events"))
    program = Program((*declarations, Repeat(2, (add,))))
    trace = program.unroll().trace

    observed = tuple(steps(program))

    assert len(observed) == len(trace)
    assert all(isinstance(step, Step) for step in observed)
    assert tuple(step.index for step in observed) == tuple(range(len(trace)))
    assert tuple(step.opcode for step in observed) == trace
    assert tuple(step.graph for step in observed) == tuple(
        execute(trace[: index + 1]) for index in range(len(trace))
    )
    assert observed[-1].graph == execute(trace)


def test_step_to_data_is_public_json_serializable_state() -> None:
    """A repeated program's step state is composed from public JSON data."""
    declarations = LAWS.declarations()
    add = AddItem(LAWS.name("events"))
    observed = tuple(steps(Program((*declarations, Repeat(2, (add,))))))
    step = observed[-1]

    data = step.to_data()

    assert data == {
        "index": step.index,
        "opcode": step.opcode.to_data(),
        "graph": step.graph.to_data(),
    }
    assert json.loads(json.dumps(data)) == data


def test_steps_accept_as_built_and_primitive_iterables() -> None:
    """Every documented source shape produces the same primitive transitions."""
    outcome = Program(LAWS.declarations()).unroll()
    assert tuple(steps(outcome)) == tuple(steps(outcome.trace))


def test_steps_yield_prefix_before_indexed_refusal() -> None:
    """A refused transition preserves prior steps and names its trace index."""
    trace = (*LAWS.declarations(), AddItem(LAWS.name("missing")))
    iterator = steps(trace)
    prefix = [next(iterator) for _ in LAWS.declarations()]
    assert tuple(step.graph for step in prefix) == tuple(
        execute(trace[: index + 1]) for index in range(len(prefix))
    )
    with pytest.raises(
        ExecutionError,
        match=rf"opcode {len(prefix)} .*missing.*not declared",
    ):
        next(iterator)


def test_step_budget_refusal_precedes_iteration() -> None:
    """A nested repeat bomb cannot produce any primitive step."""
    yielded: list[Step] = []
    opcode = AddItem(LAWS.name("events"))
    hostile = Repeat(MAX_REPEAT_COUNT, (Repeat(MAX_REPEAT_COUNT, (opcode,)),))
    with pytest.raises(ValueError, match="total primitive opcode count exceeds limit"):
        program = Program((hostile,))
        yielded.extend(steps(program))
    assert yielded == []


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


def test_nested_repeat_refuses_total_expansion_before_materializing() -> None:
    """A multiplicative procedure bomb is counted, not eagerly expanded."""
    opcode = AddItem(LAWS.name("events"))
    hostile = Repeat(MAX_REPEAT_COUNT, (Repeat(MAX_REPEAT_COUNT, (opcode,)),))
    started = time.monotonic()
    with pytest.raises(
        ValueError,
        match=f"total primitive opcode count exceeds limit {MAX_TOTAL_OPCODES}",
    ):
        Program((hostile,))
    assert time.monotonic() - started < 1.0


def test_total_expansion_budget_discriminates_at_boundary() -> None:
    """The exact trace budget is admitted and one additional primitive is refused."""
    opcode = AddItem(LAWS.name("events"))
    Program(
        (
            Repeat(
                MAX_TOTAL_OPCODES // MAX_REPEAT_COUNT,
                (Repeat(MAX_REPEAT_COUNT, (opcode,)),),
            ),
        )
    )
    with pytest.raises(ValueError, match="total primitive opcode count exceeds limit"):
        Program(
            (
                Repeat(
                    MAX_TOTAL_OPCODES // MAX_REPEAT_COUNT,
                    (Repeat(MAX_REPEAT_COUNT, (opcode,)),),
                ),
                opcode,
            )
        )


def test_large_admitted_repeat_unrolls_correctly() -> None:
    """A useful large procedure beneath policy still produces its complete trace."""
    opcode = AddItem(LAWS.name("events"))
    # The authoritative linear path must admit the full policy-sized build.
    outcome = Program(
        (*LAWS.declarations(), Repeat(MAX_REPEAT_COUNT, (opcode,)))
    ).unroll()
    assert len(outcome.trace) == len(LAWS.declarations()) + MAX_REPEAT_COUNT
    assert outcome.trace[len(LAWS.declarations())] is opcode
    assert outcome.trace[-1] is opcode
    assert len(outcome.graph.tiers[0].items) == MAX_REPEAT_COUNT


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
    # Per-opcode apply/revalidation is the reference execute/steps mechanism.
    with pytest.raises(ExecutionError, match="opcode 0 .*duplicate tier"):
        execute((DeclareTier(tier.declaration),))


def test_execution_refuses_a_non_graph_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """A recognized primitive must return graph state before execution can advance."""

    def forged_apply(_opcode: AddItem, _graph: Graph) -> object:
        return object()

    monkeypatch.setattr(AddItem, "apply", forged_apply)
    # Per-opcode result-type checking is the reference execute/steps mechanism.
    with pytest.raises(
        ExecutionError, match="opcode 0 .*returned 'object', expected Graph"
    ):
        execute((AddItem(LAWS.name("events")),))


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


def test_steps_are_stable_across_hash_seeds() -> None:
    """Separate interpreters emit the same ordered intermediate states."""
    script = """import json
from tiergraph import *
n=QualifiedName('urn:s','t')
p=Program((DeclareNamespace(NamespaceDeclaration('s','urn:s')),DeclareTier(TierDeclaration(n,'Tier')),Repeat(2,(AddItem(n),))))
print(json.dumps([{'index': s.index, 'opcode': s.opcode.to_data(), 'graph': s.graph.to_data()} for s in steps(p)], sort_keys=True))
"""
    sequences = []
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
        sequences.append(completed.stdout)
    assert len(set(sequences)) == 1


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
    assert execute(outcome.trace) == outcome.graph
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


def test_relate_opcode_builds_ordered_polyadic_instances() -> None:
    """The machine retains a polyadic instance and its ordered target sequence."""
    tier = LAWS.name("events")
    relation_name = LAWS.name("ordered")
    side = RelationSideDeclaration((RelationEndpointKind.ITEM,), (tier,), 1, 2)
    relation = PolyadicRelationInstance(
        relation_name,
        (ItemRef(tier, 0),),
        (ItemRef(tier, 1), ItemRef(tier, 0)),
    )
    outcome = build_program(
        (
            *LAWS.declarations(),
            AddItem(tier),
            AddItem(tier),
            DeclareRelation(PolyadicRelationDeclaration(relation_name, side, side)),
            Relate(relation),
        )
    ).unroll()
    assert outcome.graph.polyadic_relations == (relation,)
    assert outcome.trace[-1].to_data()["relation"] == relation.to_data()


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


def test_linear_unroll_matches_reference_execution_for_rich_graph() -> None:
    """The fast builder preserves every graph field and canonical wire byte."""
    tier = LAWS.name("events")
    relation_name = LAWS.name("ordered")
    position_name = LAWS.name("position-label")
    side = RelationSideDeclaration((RelationEndpointKind.ITEM,), (tier,), 1, 2)
    trace: tuple[Opcode, ...] = (
        *LAWS.declarations(),
        DeclareAttribute(
            AttributeDeclaration(
                position_name, AttributeDomain.POSITION, XsdType.STRING
            )
        ),
        AddItem(tier),
        PromoteItem(ItemRef(tier, 0), "durable"),
        PromotePosition(PositionRef(tier, 1), "outer-unused"),
        AddItem(tier),
        AttachValue(
            AttributeDomain.POSITION,
            DurablePositionRef(tier, BoundarySide.AFTER),
            AttributeValue(position_name, XsdType.STRING, "tick"),
        ),
        AddItem(tier),
        DeclareRelation(PolyadicRelationDeclaration(relation_name, side, side)),
        Relate(
            PolyadicRelationInstance(
                relation_name,
                (ItemRef(tier, 0),),
                (ItemRef(tier, 1), ItemRef(tier, 0)),
            )
        ),
    )
    outcome = Program(trace).unroll()
    reference = execute(outcome.trace)

    assert outcome.graph == reference
    assert wire.dumps(outcome.graph) == wire.dumps(reference)
    assert outcome.graph.to_data() == reference.to_data()


def test_linear_builder_covers_promotions_subsets_and_attachment_shapes() -> None:
    """Less common mutable transitions retain the reference machine's result."""
    name = LAWS.name
    tier = name("events")
    position_attribute = name("position-extra")
    relation_attribute = name("relation-extra")
    membership = name("membership-poly")
    selection = name("selection-poly")
    side = RelationSideDeclaration((RelationEndpointKind.ITEM,), (tier,), 1, 2)
    trace: tuple[Opcode, ...] = (
        *LAWS.declarations(),
        DeclareAttribute(
            AttributeDeclaration(
                position_attribute, AttributeDomain.POSITION, XsdType.STRING
            )
        ),
        DeclareAttribute(
            AttributeDeclaration(
                relation_attribute,
                AttributeDomain.RELATION_DECLARATION,
                XsdType.STRING,
            )
        ),
        AddItem(tier, Item("already")),
        PromoteItem(ItemRef(tier, 0), "already"),
        AddItem(tier),
        AttachValue(
            AttributeDomain.POSITION,
            PositionRef(tier, 1),
            AttributeValue(position_attribute, XsdType.STRING, "middle"),
        ),
        PromotePosition(PositionRef(tier, 1), "middle-anchor"),
        PromotePosition(PositionRef(tier, 1), "middle-anchor"),
        AttachValue(
            AttributeDomain.POSITION,
            PositionRef(tier, 2),
            AttributeValue(position_attribute, XsdType.STRING, "after"),
        ),
        PromotePosition(PositionRef(tier, 2), "outer-unused"),
        DeclareRelation(PolyadicRelationDeclaration(membership, side, side)),
        DeclareRelation(
            PolyadicRelationDeclaration(
                selection, side, side, targets_subset_of=membership
            )
        ),
        AttachValue(
            AttributeDomain.RELATION_DECLARATION,
            selection,
            AttributeValue(relation_attribute, XsdType.STRING, "decl"),
        ),
        Relate(
            PolyadicRelationInstance(
                membership, (ItemRef(tier, 0),), (ItemRef(tier, 1),)
            )
        ),
        Relate(
            PolyadicRelationInstance(
                selection, (ItemRef(tier, 0),), (ItemRef(tier, 1),)
            )
        ),
        DeclareRelation(
            PolyadicRelationDeclaration(
                name("self-subset"),
                side,
                side,
                targets_subset_of=name("self-subset"),
            )
        ),
        Relate(
            PolyadicRelationInstance(
                name("self-subset"),
                (ItemRef(tier, 0),),
                (ItemRef(tier, 1),),
            )
        ),
    )
    outcome = Program(trace).unroll()
    reference = execute(outcome.trace)
    assert wire.dumps(outcome.graph) == wire.dumps(reference)

    conflicting_promotions = (
        (
            *LAWS.declarations(),
            AddItem(tier, Item("already")),
            PromoteItem(ItemRef(tier, 0), "ignored"),
        ),
        (
            *LAWS.declarations(),
            AddItem(tier),
            AddItem(tier),
            PromotePosition(PositionRef(tier, 1), "middle-anchor"),
            PromotePosition(PositionRef(tier, 1), "ignored-again"),
        ),
    )
    for conflicting_trace in conflicting_promotions:
        with pytest.raises(ExecutionError) as linear_refusal:
            Program(conflicting_trace).unroll()
        with pytest.raises(ExecutionError) as reference_refusal:
            execute(conflicting_trace)
        assert str(linear_refusal.value) == str(reference_refusal.value)

    bad_subset = PolyadicRelationDeclaration(
        name("bad-subset"), side, side, targets_subset_of=name("later")
    )
    with pytest.raises(ExecutionError, match="opcode "):
        Program((*LAWS.declarations(), DeclareRelation(bad_subset))).unroll()
    with pytest.raises(ExecutionError, match="opcode "):
        Program(
            (
                *LAWS.declarations(),
                AddItem(tier),
                DeclareRelation(PolyadicRelationDeclaration(membership, side, side)),
                DeclareRelation(
                    PolyadicRelationDeclaration(
                        selection, side, side, targets_subset_of=membership
                    )
                ),
                Relate(
                    PolyadicRelationInstance(
                        selection, (ItemRef(tier, 0),), (ItemRef(tier, 0),)
                    )
                ),
            )
        ).unroll()


def test_linear_builder_localizes_rare_missing_targets() -> None:
    """Fast-path target lookups refuse with reference execution diagnostics."""
    name = LAWS.name
    declarations = LAWS.declarations()
    tier_attribute = DeclareAttribute(
        AttributeDeclaration(name("tier-extra"), AttributeDomain.TIER, XsdType.STRING)
    )
    relation_attribute = DeclareAttribute(
        AttributeDeclaration(
            name("relation-extra"),
            AttributeDomain.RELATION_DECLARATION,
            XsdType.STRING,
        )
    )
    cases = (
        (
            *declarations,
            tier_attribute,
            AttachValue(
                AttributeDomain.TIER,
                name("missing"),
                AttributeValue(name("tier-extra"), XsdType.STRING, "v"),
            ),
        ),
        (
            *declarations,
            relation_attribute,
            AttachValue(
                AttributeDomain.RELATION_DECLARATION,
                name("missing"),
                AttributeValue(name("relation-extra"), XsdType.STRING, "v"),
            ),
        ),
        (
            *declarations,
            AttachValue(
                AttributeDomain.ITEM,
                DurableItemRef("missing"),
                AttributeValue(name("label"), XsdType.STRING, "v"),
            ),
        ),
        (
            *declarations,
            DeclareRelation(
                PolyadicRelationDeclaration(
                    name("poly"),
                    RelationSideDeclaration((RelationEndpointKind.ITEM,), None),
                    RelationSideDeclaration((RelationEndpointKind.ITEM,), None),
                )
            ),
            Relate(
                RelationInstance(
                    name("poly"), ItemRef(name("events"), 0), ItemRef(name("events"), 0)
                )
            ),
        ),
        (
            *declarations,
            AddItem(name("events")),
            Relate(
                PolyadicRelationInstance(
                    name("members"),
                    (ItemRef(name("events"), 0),),
                    (ItemRef(name("events"), 0),),
                )
            ),
        ),
    )
    for trace in cases:
        with pytest.raises(ExecutionError, match="opcode "):
            Program(trace).unroll()


def test_temporal_dependencies_are_not_healed_by_later_opcodes() -> None:
    """Each use must be valid against its own prefix, not only the final graph."""
    name = LAWS.name
    namespace = DeclareNamespace(NamespaceDeclaration("m", "urn:machine-test"))
    tier = DeclareTier(TierDeclaration(name("events"), "Events"))
    simple = DeclareRelation(
        SimpleRelationDeclaration(name("members"), name("events"), name("event"))
    )
    add = AddItem(name("events"))
    attribute = DeclareAttribute(
        AttributeDeclaration(name("label"), AttributeDomain.ITEM, XsdType.STRING)
    )
    attach = AttachValue(
        AttributeDomain.ITEM,
        ItemRef(name("events"), 0),
        AttributeValue(name("label"), XsdType.STRING, "v"),
    )
    bipartite = DeclareRelation(
        BipartiteRelationDeclaration(name("link"), name("event"), name("event"))
    )
    relate = Relate(
        RelationInstance(
            name("link"), ItemRef(name("events"), 0), ItemRef(name("events"), 0)
        )
    )
    valid_traces: tuple[tuple[Opcode, ...], ...] = (
        (namespace, tier, simple, add, attribute, attach),
        (namespace, tier, simple, add, bipartite, relate),
        (namespace, tier),
        (namespace, tier, simple),
        (namespace, tier, simple, bipartite, add, relate),
    )
    invalid_traces: tuple[tuple[Opcode, ...], ...] = (
        (namespace, tier, simple, add, attach, attribute),
        (namespace, tier, simple, add, relate, bipartite),
        (tier, namespace),
        (namespace, simple, tier),
        (namespace, tier, bipartite, add, relate, simple),
    )

    for valid, invalid in zip(valid_traces, invalid_traces, strict=True):
        reference = execute(valid)
        assert wire.dumps(Program(valid).unroll().graph) == wire.dumps(reference)
        with pytest.raises(ExecutionError, match="opcode "):
            execute(invalid)
        with pytest.raises(ExecutionError, match="opcode "):
            Program(invalid).unroll()


def test_tier_anchored_boundary_cycle_cannot_be_healed_by_later_item() -> None:
    """A cycle at an empty tier boundary is refused before that boundary shifts."""
    namespace = "urn:shift-sensitive"

    def name(local: str) -> QualifiedName:
        return QualifiedName(namespace, local)

    tier = name("tier")
    item_type = name("type")
    relation = name("relation")
    program = Program(
        (
            DeclareNamespace(NamespaceDeclaration("s", namespace)),
            DeclareTier(TierDeclaration(tier, "Tier")),
            DeclareRelation(
                SimpleRelationDeclaration(name("members"), tier, item_type)
            ),
            DeclareRelation(
                BipartiteRelationDeclaration(
                    relation,
                    item_type,
                    item_type,
                    RelationEndpointKind.BOUNDARY,
                    RelationEndpointKind.BOUNDARY,
                    acyclic=True,
                )
            ),
            Relate(
                RelationInstance(
                    relation,
                    DurablePositionRef(tier, BoundarySide.BEFORE),
                    DurablePositionRef(tier, BoundarySide.AFTER),
                )
            ),
            AddItem(tier),
        )
    )
    trace = _flatten(program.opcodes)
    with pytest.raises(ExecutionError, match="closes a cycle"):
        execute(trace)
    with pytest.raises(ExecutionError, match="closes a cycle"):
        program.unroll()


def test_tier_anchored_boundary_second_parent_cannot_be_healed() -> None:
    """A shared boundary child is refused before tier-after moves apart."""
    namespace = "urn:shift-parent"

    def name(local: str) -> QualifiedName:
        return QualifiedName(namespace, local)

    tier = name("tier")
    item_type = name("type")
    relation = name("relation")
    anchor_after = DurablePositionRef(DurableItemRef("anchor"), BoundarySide.AFTER)
    tier_after = DurablePositionRef(tier, BoundarySide.AFTER)
    program = Program(
        (
            DeclareNamespace(NamespaceDeclaration("s", namespace)),
            DeclareTier(TierDeclaration(tier, "Tier")),
            DeclareRelation(
                SimpleRelationDeclaration(name("members"), tier, item_type)
            ),
            DeclareRelation(
                BipartiteRelationDeclaration(
                    relation,
                    item_type,
                    item_type,
                    RelationEndpointKind.BOUNDARY,
                    RelationEndpointKind.BOUNDARY,
                    single_parent=True,
                )
            ),
            AddItem(tier, Item("anchor")),
            Relate(
                RelationInstance(
                    relation,
                    DurablePositionRef(tier, BoundarySide.BEFORE),
                    anchor_after,
                )
            ),
            Relate(RelationInstance(relation, tier_after, tier_after)),
            AddItem(tier),
        )
    )
    trace = _flatten(program.opcodes)
    with pytest.raises(ExecutionError, match="a second parent"):
        execute(trace)
    with pytest.raises(ExecutionError, match="a second parent"):
        program.unroll()


def test_tier_anchored_polyadic_duplicate_targets_cannot_be_healed() -> None:
    """Coincident empty-tier targets are refused before an append separates them."""
    namespace = "urn:shift-polyadic"

    def name(local: str) -> QualifiedName:
        return QualifiedName(namespace, local)

    tier = name("tier")
    relation = name("relation")
    side = RelationSideDeclaration((RelationEndpointKind.BOUNDARY,), (tier,), 1, 2)
    program = Program(
        (
            DeclareNamespace(NamespaceDeclaration("s", namespace)),
            DeclareTier(TierDeclaration(tier, "Tier")),
            DeclareRelation(
                PolyadicRelationDeclaration(relation, side, side, distinct_targets=True)
            ),
            Relate(
                PolyadicRelationInstance(
                    relation,
                    (DurablePositionRef(tier, BoundarySide.BEFORE),),
                    (
                        DurablePositionRef(tier, BoundarySide.BEFORE),
                        DurablePositionRef(tier, BoundarySide.AFTER),
                    ),
                )
            ),
            AddItem(tier),
        )
    )
    trace = _flatten(program.opcodes)
    with pytest.raises(ExecutionError, match="duplicate declared-distinct targets"):
        execute(trace)
    with pytest.raises(ExecutionError, match="duplicate declared-distinct targets"):
        program.unroll()


def test_valid_tier_anchored_relation_uses_identical_reference_graph() -> None:
    """The guarded reference path remains byte-identical for valid programs."""
    namespace = "urn:shift-valid"

    def name(local: str) -> QualifiedName:
        return QualifiedName(namespace, local)

    tier = name("tier")
    item_type = name("type")
    relation = name("relation")
    program = Program(
        (
            DeclareNamespace(NamespaceDeclaration("s", namespace)),
            DeclareTier(TierDeclaration(tier, "Tier")),
            DeclareRelation(
                SimpleRelationDeclaration(name("members"), tier, item_type)
            ),
            DeclareRelation(
                BipartiteRelationDeclaration(
                    relation,
                    item_type,
                    item_type,
                    RelationEndpointKind.BOUNDARY,
                    RelationEndpointKind.BOUNDARY,
                )
            ),
            AddItem(tier),
            Relate(
                RelationInstance(
                    relation,
                    DurablePositionRef(tier, BoundarySide.BEFORE),
                    DurablePositionRef(tier, BoundarySide.AFTER),
                )
            ),
        )
    )
    trace = _flatten(program.opcodes)
    assert wire.dumps(program.unroll().graph) == wire.dumps(execute(trace))


def test_unroll_constructs_one_full_graph_independent_of_trace_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Authoritative construction performs one full validation, not one per opcode."""
    original = Graph.__post_init__
    constructions = 0

    def counted(graph: Graph) -> None:
        nonlocal constructions
        constructions += 1
        original(graph)

    monkeypatch.setattr(Graph, "__post_init__", counted)
    prefix = LAWS.declarations()
    for count in (50, 100, 200, 400):
        constructions = 0
        Program((*prefix, Repeat(count, (AddItem(LAWS.name("events")),)))).unroll()
        assert constructions == 1

    constructions = 0
    execute((*prefix, *(AddItem(LAWS.name("events")) for _ in range(10))))
    assert constructions > 10
