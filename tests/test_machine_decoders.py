"""Encoding-derived conformance for the machine program decoders.

The graph document has a declaration to walk, and `tests/conformance/schema_codec`
walks it: every field it names is mutated in turn and every acceptance path is
made to agree.  A machine program has no such declaration.  What it has is a
canonical writer, `program_dumps`, whose output *is* the wire shape -- one JSON
type per field, chosen by the opcode being written.  So the population of fields
is derived here from the writer rather than listed by hand: seed programs that
realize every opcode are encoded, every node of that encoding is located, and
the JSON type each node carries is read off as the type the format declares
there.  Substituting a type the writer never emits at that position must be
refused, and the refusal must be staged.

The consequence is the one a hand-written list cannot give: a field added to an
opcode is probed the moment the writer emits it, and a guard removed from a
decoder fails here rather than waiting to be noticed.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pytest

from tiergraph import (
    AddItem,
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
    Item,
    ItemRef,
    NamespaceDeclaration,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    Program,
    PromoteBoundary,
    PromoteItem,
    QualifiedName,
    Refusal,
    RefusalStage,
    Relate,
    RelationEndpointKind,
    RelationInstance,
    RelationSideDeclaration,
    Repeat,
    SimpleRelationDeclaration,
    TierDeclaration,
    XsdType,
    machine,
    program_dumps,
    program_loads,
    wire,
)
from tiergraph.cli import main
from tiergraph.machine import _PRIMITIVE_OPCODE_TYPES, Opcode

NAMESPACE = "urn:machine-decoder-test"


def _name(local: str) -> QualifiedName:
    """Return a fixture name in the declared test namespace."""
    return QualifiedName(NAMESPACE, local)


TIER = _name("events")
OTHER = _name("words")
# Three side shapes: one naming tiers, one leaving them unconstrained (which the
# writer spells as an explicit null), and one over boundaries.
TIER_SIDE = RelationSideDeclaration((RelationEndpointKind.ITEM,), (TIER,), 1, 2)
OPEN_SIDE = RelationSideDeclaration((RelationEndpointKind.ITEM,))
BOUNDARY_SIDE = RelationSideDeclaration(
    (RelationEndpointKind.BOUNDARY,), None, 0, None, True
)


def _declarations() -> tuple[Opcode, ...]:
    """Return a prefix declaring one attribute in every attachment domain."""
    return (
        DeclareNamespace(NamespaceDeclaration("m", NAMESPACE)),
        DeclareTier(TierDeclaration(TIER, "Events")),
        DeclareTier(TierDeclaration(OTHER, "Words")),
        *(
            DeclareAttribute(
                AttributeDeclaration(_name(domain.value), domain, XsdType.STRING)
            )
            for domain in AttributeDomain
        ),
    )


def _binary_program() -> Program:
    """Return a program realizing the binary relation carrier and every domain."""
    return Program(
        (
            *_declarations(),
            DeclareRelation(
                SimpleRelationDeclaration(_name("members"), TIER, _name("event"))
            ),
            DeclareRelation(
                SimpleRelationDeclaration(_name("tokens"), OTHER, _name("word"))
            ),
            DeclareRelation(
                BipartiteRelationDeclaration(
                    _name("dominates"),
                    _name("event"),
                    _name("word"),
                    RelationEndpointKind.ITEM,
                    RelationEndpointKind.ITEM,
                    False,
                    True,
                )
            ),
            AddItem(
                TIER,
                Item(
                    "first",
                    (AttributeValue(_name("item"), XsdType.STRING, "a"),),
                ),
            ),
            AddItem(TIER),
            AddItem(OTHER),
            Repeat(2, (AddItem(OTHER),)),
            PromoteItem(ItemRef(TIER, 1), "second"),
            PromoteItem(ItemRef(OTHER, 0), "already"),
            PromoteBoundary(BoundaryRef(OTHER, 3), "middle"),
            Relate(
                RelationInstance(
                    _name("dominates"),
                    ItemRef(TIER, 0),
                    ItemRef(OTHER, 1),
                    "rel-a",
                    (AttributeValue(_name("relation_instance"), XsdType.STRING, "v"),),
                )
            ),
            Relate(
                RelationInstance(
                    _name("dominates"),
                    DurableItemRef("second"),
                    DurableItemRef("already"),
                )
            ),
            AttachValue(
                AttributeDomain.DOCUMENT,
                None,
                AttributeValue(_name("document"), XsdType.STRING, "d"),
            ),
            AttachValue(
                AttributeDomain.TIER,
                TIER,
                AttributeValue(_name("tier"), XsdType.STRING, "t"),
            ),
            AttachValue(
                AttributeDomain.ITEM,
                ItemRef(TIER, 1),
                AttributeValue(_name("item"), XsdType.STRING, "i"),
            ),
            AttachValue(
                AttributeDomain.BOUNDARY,
                BoundaryRef(TIER, 1),
                AttributeValue(_name("boundary"), XsdType.STRING, "b"),
            ),
            AttachValue(
                AttributeDomain.RELATION_DECLARATION,
                _name("dominates"),
                AttributeValue(_name("relation_declaration"), XsdType.STRING, "rd"),
            ),
            AttachValue(
                AttributeDomain.RELATION_INSTANCE,
                1,
                AttributeValue(_name("relation_instance"), XsdType.STRING, "ri"),
            ),
        )
    )


def _polyadic_program() -> Program:
    """Return a program realizing the polyadic carrier and every side shape."""
    return Program(
        (
            *_declarations(),
            DeclareRelation(
                PolyadicRelationDeclaration(_name("ordered"), TIER_SIDE, TIER_SIDE)
            ),
            DeclareRelation(
                PolyadicRelationDeclaration(
                    _name("subset"),
                    TIER_SIDE,
                    TIER_SIDE,
                    targets_subset_of=_name("ordered"),
                )
            ),
            DeclareRelation(
                PolyadicRelationDeclaration(_name("open"), OPEN_SIDE, OPEN_SIDE)
            ),
            DeclareRelation(
                PolyadicRelationDeclaration(
                    _name("anchored"), BOUNDARY_SIDE, BOUNDARY_SIDE
                )
            ),
            AddItem(TIER, Item("first")),
            AddItem(TIER),
            PromoteBoundary(BoundaryRef(TIER, 1), "middle"),
            Relate(
                PolyadicRelationInstance(
                    _name("ordered"),
                    (ItemRef(TIER, 0),),
                    (ItemRef(TIER, 1),),
                    "poly-a",
                )
            ),
            Relate(
                PolyadicRelationInstance(
                    _name("anchored"),
                    (DurableBoundaryRef(DurableItemRef("first"), BoundarySide.AFTER),),
                    (DurableBoundaryRef(TIER, BoundarySide.BEFORE),),
                )
            ),
        )
    )


def seeds() -> tuple[tuple[str, Program], ...]:
    """Return the named witness programs the probe population is derived from."""
    return (("binary", _binary_program()), ("polyadic", _polyadic_program()))


# One value per JSON type, so a substitution says "a value of this type here"
# rather than smuggling in a second condition alongside the type.
REPRESENTATIVE: dict[type, object] = {
    type(None): None,
    bool: True,
    int: 7,
    float: 1.5,
    str: "__probe__",
    list: [],
    dict: {},
}
# A spelling no enumeration in this format admits, and no pattern rejects: at a
# free-text field it is simply another string, and at an enumerated one it is
# the condition `wire` stages at VALUE.
OUTSIDE_ENUM = "__outside_enum__"

type NodePath = tuple[str | int, ...]
type ShapeKey = tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Probe:
    """Describe one substitution of a JSON type into one encoded position."""

    seed: str
    line: int
    path: NodePath
    replacement: object
    records: tuple[dict[str, object], ...]

    @property
    def id(self) -> str:
        """Return a deterministic diagnostic identity."""
        rendered = "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}" for part in self.path
        )
        return f"{self.seed}:line {self.line}{rendered}:={self.replacement!r}"

    @property
    def source(self) -> str:
        """Return the mutated program as the public reader receives it."""
        return "".join(json.dumps(record) + "\n" for record in self.records)


def records_of(program: Program) -> list[dict[str, object]]:
    """Return the canonical encoding of a program as parsed JSON records."""
    return [json.loads(line) for line in program_dumps(program).splitlines()]


def nodes(
    value: object, path: NodePath = (), key: ShapeKey = ("header",)
) -> list[tuple[NodePath, object, ShapeKey]]:
    """Locate every node of an encoded record beneath its own shape key.

    The key is what makes two positions the same declared field.  It restarts at
    every nested opcode object, so an `add_item` inside a `repeat` body is keyed
    as an `add_item` and not as a region of `repeat` -- otherwise a field that a
    body happens never to exercise would look like a field of a different shape.
    """
    found: list[tuple[NodePath, object, ShapeKey]] = []
    if isinstance(value, dict) and isinstance(value.get("opcode"), str):
        key = (value["opcode"],)
    if path:
        found.append((path, value, key))
    if isinstance(value, dict):
        for member, item in value.items():
            found.extend(nodes(item, (*path, member), (*key, member)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(nodes(item, (*path, index), (*key, "[]")))
    return found


def declared_types() -> dict[ShapeKey, frozenset[type]]:
    """Read each position's admitted JSON types off the canonical writer.

    A field is nullable exactly when some witness writes a null there, so the
    types are unioned across every seed rather than taken from one encounter.
    Under-reading them costs a probe; over-reading one would demand a refusal
    the format does not owe.
    """
    found: dict[ShapeKey, set[type]] = {}
    for _, program in seeds():
        for record in records_of(program):
            for path, value, key in nodes(record):
                if path == ("opcode",):
                    continue
                found.setdefault(key, set()).add(type(value))
    return {key: frozenset(types) for key, types in found.items()}


def probes() -> tuple[Probe, ...]:
    """Construct every wrong-type and outside-enumeration probe from the seeds."""
    types = declared_types()
    found: list[Probe] = []
    for seed_name, program in seeds():
        records = records_of(program)
        for line, record in enumerate(records, 1):
            for path, _, key in nodes(record):
                if path == ("opcode",):
                    continue
                declared = types[key]
                replacements = [
                    REPRESENTATIVE[kind]
                    for kind in REPRESENTATIVE
                    if kind not in declared
                ]
                if str in declared:
                    replacements.append(OUTSIDE_ENUM)
                for replacement in replacements:
                    mutated = copy.deepcopy(records)
                    _place(mutated[line - 1], path, replacement)
                    found.append(
                        Probe(seed_name, line, path, replacement, tuple(mutated))
                    )
    return tuple(found)


def _place(record: object, path: NodePath, replacement: object) -> None:
    """Substitute one value at a located position of one record."""
    node: object = record
    for part in path[:-1]:
        node = node[part]  # type: ignore[index]
    node[path[-1]] = replacement  # type: ignore[index]


@dataclass(frozen=True, slots=True)
class Outcome:
    """Record what the public program reader did with one probe."""

    probe: Probe
    accepted: bool
    error: BaseException | None


def outcomes(probe_set: tuple[Probe, ...]) -> tuple[Outcome, ...]:
    """Run every probe through the public reader without judging the result."""
    results: list[Outcome] = []
    for probe in probe_set:
        try:
            program_loads(probe.source)
        except BaseException as error:  # noqa: B036 - the sweep judges, not filters
            results.append(Outcome(probe, False, error))
        else:
            results.append(Outcome(probe, True, None))
    return tuple(results)


@lru_cache(maxsize=1)
def _swept() -> tuple[Outcome, ...]:
    """Run the derived sweep once for every test that reads it."""
    return outcomes(probes())


def test_seeds_round_trip_through_the_public_program_codec() -> None:
    """What the writer emits, the reader reads back to the same program."""
    for _, program in seeds():
        encoded = program_dumps(program)
        assert program_dumps(program_loads(encoded)) == encoded


def test_seeds_realize_every_opcode_and_every_declared_spelling() -> None:
    """The witnesses cover the population the probes are derived over.

    Both sides are read from the code rather than listed here, so an opcode or
    an enumeration member added later leaves this failing rather than silently
    outside the sweep.
    """
    flat: list[object] = []
    pending = [opcode for _, program in seeds() for opcode in program.opcodes]
    while pending:
        opcode = pending.pop()
        flat.append(opcode)
        if isinstance(opcode, Repeat):
            pending.extend(opcode.body)
    assert {type(opcode) for opcode in flat} == {*_PRIMITIVE_OPCODE_TYPES, Repeat}

    attaches = [opcode for opcode in flat if isinstance(opcode, AttachValue)]
    assert {attach.domain for attach in attaches} == set(AttributeDomain)

    relations = [opcode for opcode in flat if isinstance(opcode, DeclareRelation)]
    assert {type(relation.declaration) for relation in relations} == {
        SimpleRelationDeclaration,
        BipartiteRelationDeclaration,
        PolyadicRelationDeclaration,
    }

    instances = [opcode for opcode in flat if isinstance(opcode, Relate)]
    endpoints = [
        endpoint
        for instance in instances
        for endpoint in (
            (instance.relation.left, instance.relation.right)
            if isinstance(instance.relation, RelationInstance)
            else (*instance.relation.sources, *instance.relation.targets)
        )
    ]
    assert {type(endpoint) for endpoint in endpoints} == {
        ItemRef,
        DurableItemRef,
        DurableBoundaryRef,
    }
    assert {
        endpoint.side
        for endpoint in endpoints
        if isinstance(endpoint, DurableBoundaryRef)
    } == set(BoundarySide)
    kinds = {
        kind
        for relation in relations
        if isinstance(relation.declaration, PolyadicRelationDeclaration)
        for side in (relation.declaration.sources, relation.declaration.targets)
        for kind in side.endpoint_kinds
    }
    assert kinds == set(RelationEndpointKind)


def test_derived_sweep_probes_every_position_of_the_encoded_shape() -> None:
    """The population is the writer's own output, not a list kept by hand."""
    covered = {(outcome.probe.seed, outcome.probe.path) for outcome in _swept()}
    for seed_name, program in seeds():
        for record in records_of(program):
            for path, _, _ in nodes(record):
                if path == ("opcode",):
                    continue
                assert (seed_name, path) in covered, (seed_name, path)


def test_a_type_the_writer_never_emits_is_refused_at_every_position() -> None:
    """No wrong JSON type reaches a graph through the program reader."""
    wrongly_accepted = [
        outcome.probe.id
        for outcome in _swept()
        if outcome.accepted and outcome.probe.replacement != OUTSIDE_ENUM
    ]
    assert not wrongly_accepted


def test_every_program_refusal_carries_its_declared_stage() -> None:
    """`except Refusal` catches all of it, enumerated spellings included."""
    unstaged = [
        (outcome.probe.id, f"{type(outcome.error).__name__}: {outcome.error}")
        for outcome in _swept()
        if outcome.error is not None and not isinstance(outcome.error, Refusal)
    ]
    assert not unstaged


NAME = {"namespace": "urn:x", "local_name": "t"}
VALUE_MEMBER = {"name": NAME, "value_type": "string", "lexical": "x"}


def one_opcode(record: dict[str, object]) -> str:
    """Return a program of exactly one opcode, so one condition is under test.

    Each case below is decoded on its own rather than substituted into a seed:
    a whole program meets the first condition it contains, and against an
    unguarded decoder that is somebody else's condition several lines later.
    """
    return f'{{"machine_version":"1"}}\n{json.dumps(record)}\n'


@pytest.mark.parametrize(
    "record,message",
    [
        (
            {
                "opcode": "declare_namespace",
                "declaration": {"prefix": "x", "namespace": 7},
            },
            "line 2.declaration.namespace",
        ),
        (
            {
                "opcode": "declare_namespace",
                "declaration": {"prefix": 7, "namespace": "urn:x"},
            },
            "line 2.declaration.prefix",
        ),
        (
            {"opcode": "declare_tier", "declaration": {"name": NAME, "long_name": 7}},
            "line 2.declaration.long_name",
        ),
        (
            {
                "opcode": "add_item",
                "tier": NAME,
                "item": {"durable_id": 7, "attributes": []},
            },
            "line 2.item.durable_id",
        ),
        (
            {
                "opcode": "promote_item",
                "reference": {"tier": NAME, "index": 0},
                "durable_id": 7,
            },
            "line 2.durable_id",
        ),
        (
            {
                "opcode": "promote_position",
                "reference": {"tier": NAME, "index": 0},
                "durable_id": 7,
            },
            "line 2.durable_id",
        ),
        (
            {
                "opcode": "relate",
                "relation": {
                    "declaration": NAME,
                    "left": {"tier": NAME, "index": 0},
                    "right": {"tier": NAME, "index": 1},
                    "durable_id": 7,
                    "attributes": [],
                },
            },
            "line 2.relation.durable_id",
        ),
        (
            {
                "opcode": "relate",
                "relation": {
                    "declaration": NAME,
                    "left": {"kind": "durable-item", "durable_id": 7},
                    "right": {"durable_id": "b"},
                    "durable_id": None,
                    "attributes": [],
                },
            },
            "line 2.relation.left.durable_id",
        ),
    ],
)
def test_a_non_string_member_is_refused_with_its_own_path(
    record: dict[str, object], message: str
) -> None:
    """Each string-declared member names itself, at the construction stage."""
    with pytest.raises(Refusal) as refusal:
        program_loads(one_opcode(record))
    assert refusal.value.stage is RefusalStage.CONSTRUCTION
    assert str(refusal.value) == f"{message} must be a string"


@pytest.mark.parametrize(
    "record,message",
    [
        (
            {
                "opcode": "declare_attribute",
                "declaration": {
                    "name": NAME,
                    "domain": OUTSIDE_ENUM,
                    "value_type": "string",
                },
            },
            "line 2.declaration.domain",
        ),
        (
            {
                "opcode": "declare_attribute",
                "declaration": {
                    "name": NAME,
                    "domain": "item",
                    "value_type": OUTSIDE_ENUM,
                },
            },
            "line 2.declaration.value_type",
        ),
        (
            {
                "opcode": "declare_relation",
                "declaration": {
                    "kind": "bipartite",
                    "name": NAME,
                    "left_type": NAME,
                    "right_type": NAME,
                    "left_endpoint": OUTSIDE_ENUM,
                    "right_endpoint": "item",
                    "single_parent": False,
                    "acyclic": False,
                    "attributes": [],
                },
            },
            "line 2.declaration.left_endpoint",
        ),
        (
            {
                "opcode": "attach_value",
                "domain": OUTSIDE_ENUM,
                "target": None,
                "value": VALUE_MEMBER,
            },
            "line 2.domain",
        ),
        (
            {
                "opcode": "attach_value",
                "domain": "document",
                "target": None,
                "value": {**VALUE_MEMBER, "value_type": OUTSIDE_ENUM},
            },
            "line 2.value.value_type",
        ),
        (
            {
                "opcode": "relate",
                "relation": {
                    "declaration": NAME,
                    "left": {"durable_id": "a"},
                    "right": {
                        "anchor": {"kind": "tier", "tier": NAME},
                        "side": OUTSIDE_ENUM,
                    },
                    "durable_id": None,
                    "attributes": [],
                },
            },
            "line 2.relation.right.side",
        ),
    ],
)
def test_an_unsupported_spelling_is_staged_where_the_document_reader_stages_it(
    record: dict[str, object], message: str
) -> None:
    """The enumerated-spelling condition is VALUE for the program reader too."""
    with pytest.raises(Refusal) as refusal:
        program_loads(one_opcode(record))
    assert refusal.value.stage is RefusalStage.VALUE
    assert str(refusal.value) == f"{message} has unsupported value {OUTSIDE_ENUM!r}"


def test_the_document_reader_stages_the_same_spelling_condition_at_value() -> None:
    """The stage asserted above is read off `wire`, not chosen for `machine`."""
    document = json.loads(wire.dumps(_binary_program().unroll().graph))
    declarations = document["graph"]["attribute_declarations"]
    declarations[0]["domain"] = OUTSIDE_ENUM
    with pytest.raises(Refusal) as refusal:
        wire.loads(json.dumps(document))
    assert refusal.value.stage is RefusalStage.VALUE
    assert str(refusal.value).endswith(f"has unsupported value {OUTSIDE_ENUM!r}")


def test_an_attachment_target_is_held_to_the_shapes_the_format_names() -> None:
    """A boolean target would silently attach the value to relation one."""
    with pytest.raises(Refusal) as refusal:
        program_loads(
            one_opcode(
                {
                    "opcode": "attach_value",
                    "domain": "relation_instance",
                    "target": True,
                    "value": VALUE_MEMBER,
                }
            )
        )
    assert refusal.value.stage is RefusalStage.CONSTRUCTION
    assert str(refusal.value) == "line 2.target must be an integer"


def test_a_side_leaving_its_tiers_unconstrained_is_read_back() -> None:
    """The writer spells an unconstrained side as null; the reader took only arrays."""
    declaration = PolyadicRelationDeclaration(_name("open"), OPEN_SIDE, OPEN_SIDE)
    encoded = program_dumps(Program((DeclareRelation(declaration),)))
    assert program_loads(encoded).opcodes == (DeclareRelation(declaration),)


def test_a_durable_item_endpoint_of_a_relation_is_read_back() -> None:
    """A relation instance tags its durable endpoints; the reader took only bare ones."""
    instance = RelationInstance(
        _name("dominates"), DurableItemRef("a"), DurableItemRef("b")
    )
    encoded = program_dumps(Program((Relate(instance),)))
    assert program_loads(encoded).opcodes == (Relate(instance),)


def test_run_refuses_the_program_it_used_to_execute_into_an_unreadable_graph(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`run` settles the question `validate` settles, before it writes."""
    program = tmp_path / "program.jsonl"
    program.write_text(
        '{"machine_version":"1"}\n'
        '{"opcode":"declare_namespace",'
        '"declaration":{"prefix":"x","namespace":"urn:x"}}\n'
        '{"opcode":"declare_tier","declaration":'
        '{"name":{"namespace":"urn:x","local_name":"t"},"long_name":7}}\n',
        encoding="utf-8",
    )
    output = tmp_path / "out.json"
    assert main(["run", str(program), "--to", "json", "-o", str(output)]) == 1
    assert not output.exists()
    assert capsys.readouterr().err.strip() == (
        "tiergraph: run: ValueError: line 3.declaration.long_name must be a string"
    )


def test_what_the_program_reader_accepts_the_document_reader_reads_back() -> None:
    """A program that loads cannot fingerprint a graph `loads` would refuse.

    `Program.fingerprint` hashes the as-built graph, so a decoder that admits a
    member the graph format does not carry would publish a digest of a document
    no reader can take back.  Every probe the reader accepts is built and its
    graph is re-read here, which is the same question `validate` settles.
    """
    checked = 0
    for outcome in _swept():
        if not outcome.accepted:
            continue
        program = program_loads(outcome.probe.source)
        try:
            graph = program.unroll().graph
        except Refusal:
            continue
        assert wire.loads(wire.dumps(graph)) == graph
        assert program.fingerprint()
        checked += 1
    assert checked


def test_reverting_the_checked_string_guard_is_reported_by_the_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A decoder that casts instead of checking fails here, not at `validate`.

    `typing.cast` is erased at runtime, so reverting the guards to what they
    were is exactly replacing the checked helper with the identity.
    """
    monkeypatch.setattr(machine, "_string", lambda value, path: value)
    reverted = outcomes(probes())
    assert [
        outcome.probe.id
        for outcome in reverted
        if outcome.accepted and outcome.probe.replacement != OUTSIDE_ENUM
    ]


def test_reverting_the_checked_enum_guard_is_reported_by_the_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Constructing an enumeration directly escapes the declared order."""
    monkeypatch.setattr(
        machine, "_enum", lambda enum_type, value, path: enum_type(value)
    )
    reverted = outcomes(probes())
    assert [
        outcome.probe.id
        for outcome in reverted
        if outcome.error is not None and not isinstance(outcome.error, Refusal)
    ]
