"""The CLI validates, transforms, inspects, renders, and executes public values."""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import tiergraph
import tiergraph.cli as cli
import tiergraph.machine_codec as machine_codec
from tiergraph import (
    AddItem,
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
    Tier,
    TierDeclaration,
    XsdType,
)
from tiergraph.cli import build_parser, main


def _empty(path: Path) -> tiergraph.Graph:
    graph = tiergraph.Graph((), (), ())
    path.write_bytes(tiergraph.dump_bytes(graph))
    return graph


def _program(path: Path, *opcodes: object, newline: bytes = b"\n") -> None:
    records = [{"machine_version": tiergraph.MACHINE_VERSION}]
    records.extend(opcode.to_data() for opcode in opcodes)  # type: ignore[attr-defined]
    path.write_bytes(newline.join(json.dumps(record).encode() for record in records))


def test_version_default_help_and_every_command_help(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--version"]) == 0
    assert json.loads(capsys.readouterr().out) == {"version": tiergraph.__version__}
    assert main([]) == 0
    assert "{validate,render,inspect,convert,run,step}" in capsys.readouterr().out
    for command in ("validate", "render", "inspect", "convert", "run", "step"):
        with pytest.raises(SystemExit) as raised:
            main([command, "--help"])
        assert raised.value.code == 0
        assert f"tiergraph {command}" in capsys.readouterr().out
    action = next(
        action
        for action in build_parser()._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    assert list(action.choices) == [
        "validate",
        "render",
        "inspect",
        "convert",
        "run",
        "step",
    ]


def test_argparse_usage_error() -> None:
    with pytest.raises(SystemExit) as raised:
        main(["convert", "-"])
    assert raised.value.code == 2


def test_validate_and_graph_output_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "graph.json"
    graph = _empty(source)
    assert main(["validate", str(source)]) == 0
    assert capsys.readouterr().out == "ok\n"

    report = tmp_path / "report.txt"
    assert main(["inspect", str(source), "-o", str(report)]) == 0
    assert report.read_text() == (
        f"format version: {tiergraph.FORMAT_VERSION}\n"
        "namespaces: 0\n"
        "tiers: 0\n"
        "items: 0\n"
        "relation declarations: 0\n"
        "binary relation instances: 0\n"
        "polyadic relation instances: 0\n"
        "attribute declarations: 0\n"
        "populated position values: 0\n"
        "document attributes: 0\n"
    )
    dot = tmp_path / "graph.dot"
    assert main(["render", str(source), "--include-empty-tiers", "-o", str(dot)]) == 0
    assert dot.read_text().startswith("digraph tiergraph {")

    for target in ("json", "json-compact", "bytes"):
        output = tmp_path / target
        assert main(["convert", str(source), "--to", target, "-o", str(output)]) == 0
        assert output.read_bytes().endswith(b"\n")
        assert tiergraph.loads(output.read_bytes()) == graph
    assert (tmp_path / "bytes").read_bytes() == tiergraph.dump_bytes(graph)
    assert (tmp_path / "json-compact").read_bytes() == tiergraph.dump_compact(
        graph
    ).encode("utf-8")
    assert b"\n  " in (tmp_path / "json").read_bytes()
    assert b"\n  " not in (tmp_path / "json-compact").read_bytes()


def test_inspect_uses_canonical_relation_order(tmp_path: Path) -> None:
    ns = "urn:cli"
    tier_name = QualifiedName(ns, "tier")
    other_tier = QualifiedName(ns, "other")
    graph = tiergraph.Graph(
        (NamespaceDeclaration("c", ns),),
        (
            Tier(TierDeclaration(tier_name, "Tier"), (Item(),)),
            Tier(TierDeclaration(other_tier, "Other"), (Item(),)),
        ),
        (
            tiergraph.SimpleRelationDeclaration(
                QualifiedName(ns, "z"), other_tier, QualifiedName(ns, "type-z")
            ),
            tiergraph.SimpleRelationDeclaration(
                QualifiedName(ns, "a"), tier_name, QualifiedName(ns, "type-a")
            ),
        ),
    )
    source = tmp_path / "graph.json"
    source.write_bytes(tiergraph.dump_bytes(graph))
    output = tmp_path / "report"
    assert main(["inspect", str(source), "-o", str(output)]) == 0
    text = output.read_text()
    assert text.index("{urn:cli}a") < text.index("{urn:cli}z")
    assert "tier: {urn:cli}tier | Tier | items=1 | attributes=0" in text


def test_run_header_only_edges_and_all_outputs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for suffix, content in (
        ("nonewline", b'{"machine_version":"1"}'),
        ("crlf", b'{"machine_version":"1"}\r\n'),
    ):
        source = tmp_path / suffix
        source.write_bytes(content)
        for target in ("json", "json-compact", "bytes", "dot"):
            output = tmp_path / f"{suffix}-{target}"
            args = ["run", str(source), "--to", target, "-o", str(output)]
            if target == "dot":
                args.append("--include-empty-tiers")
            assert main(args) == 0
            assert output.read_bytes()
    bad = tmp_path / "blank"
    bad.write_bytes(b'{"machine_version":"1"}\n \t\n')
    assert main(["run", str(bad), "--to", "json"]) == 1
    assert "whitespace-only" in capsys.readouterr().err


def test_run_nested_repeat_and_kernel_expansion_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    ns = "urn:run"
    tier = QualifiedName(ns, "events")
    source = tmp_path / "program.jsonl"
    _program(
        source,
        DeclareNamespace(NamespaceDeclaration("r", ns)),
        DeclareTier(TierDeclaration(tier, "Events")),
        Repeat(2, (AddItem(tier),)),
    )
    output = tmp_path / "graph.json"
    assert main(["run", str(source), "--to", "json", "-o", str(output)]) == 0
    assert len(tiergraph.loads(output.read_bytes()).tiers[0].items) == 2

    monkeypatch.setattr(tiergraph.machine, "MAX_TOTAL_OPCODES", 1)
    assert main(["run", str(source), "--to", "json"]) == 1
    assert "total primitive opcode count exceeds limit" in capsys.readouterr().err


@pytest.mark.parametrize(
    "repeat_depth", [tiergraph.MAX_JSON_DEPTH // 2 + 1, 258, 300, 440]
)
def test_run_repeat_depth_boundary(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    repeat_depth: int,
) -> None:
    prefix = b'{"opcode":"repeat","count":1,"body":['
    source = tmp_path / "deep.jsonl"
    header = b'{"machine_version":"1"}\n'
    source.write_bytes(header + prefix * repeat_depth + b"]}" * repeat_depth)

    assert main(["run", str(source), "--to", "json"]) == 1
    error = capsys.readouterr().err
    assert (
        f"JSONL line 2: JSON nesting depth exceeds limit {tiergraph.MAX_JSON_DEPTH}"
        in error
    )
    assert "maximum recursion depth exceeded" not in error
    assert "Traceback" not in error

    if repeat_depth == tiergraph.MAX_JSON_DEPTH // 2 + 1:
        source.write_bytes(
            header + prefix * (repeat_depth - 1) + b"]}" * (repeat_depth - 1)
        )
        assert main(["run", str(source), "--to", "json"]) == 0


def test_run_execution_error_and_option_contract(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "program.jsonl"
    _program(source, AddItem(QualifiedName("urn:none", "missing")))
    assert main(["run", str(source), "--to", "json"]) == 1
    error = capsys.readouterr().err
    assert "ExecutionError: opcode 0" in error
    assert "Traceback" not in error
    _program(source)
    assert main(["run", str(source), "--to", "json", "--include-empty-tiers"]) == 1
    assert "requires --to dot" in capsys.readouterr().err


def test_step_dump_repeat_is_exact_and_deterministic(tmp_path: Path) -> None:
    ns = "urn:step"
    tier = QualifiedName(ns, "events")
    source = tmp_path / "program.jsonl"
    opcodes = (
        DeclareNamespace(NamespaceDeclaration("s", ns)),
        DeclareTier(TierDeclaration(tier, "Events")),
        Repeat(2, (AddItem(tier),)),
    )
    _program(source, *opcodes)
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    assert main(["step", str(source), "-o", str(first)]) == 0
    assert main(["step", str(source), "-o", str(second)]) == 0
    expected = b"".join(
        cli._step_bytes(step) for step in tiergraph.steps(Program(opcodes))
    )
    assert first.read_bytes() == expected == second.read_bytes()
    records = [json.loads(line) for line in expected.splitlines()]
    assert [record["index"] for record in records] == [0, 1, 2, 3]
    assert [record["opcode"]["opcode"] for record in records] == [
        "declare_namespace",
        "declare_tier",
        "add_item",
        "add_item",
    ]
    assert len(records[-1]["graph"]["tiers"][0]["items"]) == 2


def test_step_dump_writes_each_record_before_requesting_the_next(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ns = "urn:stream"
    program = Program(
        (
            DeclareNamespace(NamespaceDeclaration("s", ns)),
            DeclareTier(TierDeclaration(QualifiedName(ns, "events"), "Events")),
        )
    )
    steps = tuple(tiergraph.steps(program))
    writes: list[bytes] = []

    class Output:
        def write(self, value: bytes) -> int:
            writes.append(value)
            return len(value)

    class Stdout:
        buffer = Output()

    def streaming_steps(value: Program) -> Any:
        assert value is program
        yield steps[0]
        assert writes == [cli._step_bytes(steps[0])]
        yield steps[1]

    monkeypatch.setattr(tiergraph, "steps", streaming_steps)
    monkeypatch.setattr(sys, "stdout", Stdout())
    assert cli._step_dump(program, "-", "-") == 0
    assert writes == [cli._step_bytes(step) for step in steps]


def test_step_transactional_refusal_reports_last_good_graph(
    tmp_path: Path, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    ns = "urn:step"
    source = tmp_path / "refused.jsonl"
    _program(
        source,
        DeclareNamespace(NamespaceDeclaration("s", ns)),
        AddItem(QualifiedName(ns, "missing")),
    )
    assert main(["step", str(source)]) == 1
    captured = capsysbinary.readouterr()
    records = [json.loads(line) for line in captured.out.splitlines()]
    assert len(records) == 1
    assert records[0]["index"] == 0
    assert records[0]["graph"]["namespaces"] == [{"namespace": ns, "prefix": "s"}]
    assert b"failing opcode index: 1" in captured.err
    assert b"last good graph:" in captured.err
    assert ns.encode() in captured.err
    assert b"Traceback" not in captured.err

    output = tmp_path / "partial.jsonl"
    assert main(["step", str(source), "-o", str(output)]) == 1
    assert len(output.read_bytes().splitlines()) == 1


def test_step_repl_commands_and_public_iterator_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsysbinary: pytest.CaptureFixture[bytes],
) -> None:
    ns = "urn:repl"
    source = tmp_path / "program.jsonl"
    opcodes = (
        DeclareNamespace(NamespaceDeclaration("r", ns)),
        DeclareTier(TierDeclaration(QualifiedName(ns, "tier"), "Tier")),
        Repeat(2, (AddItem(QualifiedName(ns, "tier")),)),
    )
    _program(source, *opcodes)
    calls: list[Program] = []
    public_steps = tiergraph.steps

    def recording_steps(program: Program) -> Any:
        calls.append(program)
        return public_steps(program)

    monkeypatch.setattr(tiergraph, "steps", recording_steps)
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO("print\nstep\nlist\nrun-to 2\nbreak 3\ncontinue\ninspect\nquit\n"),
    )
    assert main(["step", str(source), "--interactive"]) == 0
    captured = capsysbinary.readouterr()
    assert len(calls) == 1
    assert b'"index":0' in captured.out
    assert b'"index":3' in captured.out
    assert b'"format_version"' in captured.out
    assert b"end of program" in captured.out
    assert captured.err == b""


def test_step_mode_contracts_and_repl_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "program.jsonl"
    _program(source)
    assert main(["step", "-", "--interactive"]) == 1
    assert "requires a program file" in capsys.readouterr().err
    assert main(["step", str(source), "--interactive", "-o", str(tmp_path / "x")]) == 1
    assert "requires stdout" in capsys.readouterr().err
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO("\nrun-to nope\nrun-to -1\nunknown\nnext\nnext\nrun-to 0\nquit\n"),
    )
    assert main(["step", str(source), "--interactive"]) == 0
    output = capsys.readouterr().out
    assert output.count("expected a non-negative opcode index") == 2
    assert "commands:" in output
    assert output.count("end of program") == 3
    assert "already at opcode" not in output

    ns = "urn:refuse"
    _program(
        source,
        DeclareNamespace(NamespaceDeclaration("r", ns)),
        AddItem(QualifiedName(ns, "missing")),
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO("step\nrun-to 0\ncontinue\n"))
    assert main(["step", str(source), "--interactive"]) == 1
    captured = capsys.readouterr()
    assert "already at opcode 0" in captured.out
    assert "failing opcode index: 1" in captured.err

    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert main(["step", str(source), "--interactive"]) == 0

    _program(source, AddItem(QualifiedName(ns, "missing")))
    assert main(["step", str(source)]) == 1
    assert "failing opcode index: 0" in capsys.readouterr().err

    class TtyInput(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(sys, "stdin", TtyInput("q\n"))
    assert main(["step", str(source)]) == 0


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"not-json",
        b"[]",
        b'{"machine_version":"bad"}',
        b'{"machine_version":"1","extra":true}',
        b'{"machine_version":"1"}\n{}',
        b'{"machine_version":"1"}\n{"opcode":"unknown"}',
    ],
)
def test_run_bad_envelopes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], content: bytes
) -> None:
    source = tmp_path / "bad.jsonl"
    source.write_bytes(content)
    assert main(["run", str(source), "--to", "json"]) == 1
    assert "tiergraph: run: ValueError:" in capsys.readouterr().err


def test_clean_domain_io_and_same_path_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing"
    assert main(["validate", str(missing)]) == 3
    assert "FileNotFoundError" in capsys.readouterr().err
    invalid = tmp_path / "invalid"
    invalid.write_text("{}")
    assert main(["validate", str(invalid)]) == 1
    assert "ValueError:" in capsys.readouterr().err
    source = tmp_path / "graph"
    original = tiergraph.dump_bytes(tiergraph.Graph((), (), ()))
    source.write_bytes(original)
    assert main(["convert", str(source), "--to", "json", "-o", str(source)]) == 1
    assert source.read_bytes() == original


def test_validate_accepts_surrogate_but_convert_refuses_emission(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "surrogate.json"
    data = tiergraph.to_data(tiergraph.Graph((), (), ()))
    graph_data = data["graph"]
    assert isinstance(graph_data, dict)
    graph_data["namespaces"] = [{"prefix": "p", "namespace": chr(0xD800)}]
    source.write_text(json.dumps(data))
    assert main(["validate", str(source)]) == 0
    capsys.readouterr()
    assert main(["convert", str(source), "--to", "json-compact"]) == 3
    assert "UnicodeEncodeError" in capsys.readouterr().err


def test_module_entry_point_and_pipelines(tmp_path: Path) -> None:
    version = subprocess.run(
        [sys.executable, "-m", "tiergraph", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(version.stdout) == {"version": tiergraph.__version__}
    program = tmp_path / "program.jsonl"
    _program(program)
    run = subprocess.Popen(
        [sys.executable, "-m", "tiergraph", "run", str(program), "--to", "bytes"],
        stdout=subprocess.PIPE,
    )
    validate = subprocess.run(
        [sys.executable, "-m", "tiergraph", "validate", "-"],
        stdin=run.stdout,
        capture_output=True,
        check=True,
    )
    assert validate.stdout == b"ok\n"
    assert run.wait() == 0
    stepped = subprocess.run(
        [sys.executable, "-m", "tiergraph", "step", str(program)],
        capture_output=True,
        check=True,
    )
    assert stepped.stdout == b""
    assert stepped.stderr == b""


def test_every_remaining_opcode_shape_round_trips_through_decoder(
    tmp_path: Path,
) -> None:
    ns = "urn:all"
    name = QualifiedName(ns, "name")
    attribute = QualifiedName(ns, "attribute")
    opcodes = (
        DeclareNamespace(NamespaceDeclaration("a", ns)),
        DeclareTier(TierDeclaration(name, "Name")),
        DeclareAttribute(
            AttributeDeclaration(attribute, AttributeDomain.DOCUMENT, XsdType.STRING)
        ),
        AddItem(name, Item("durable")),
        AttachValue(
            AttributeDomain.DOCUMENT,
            None,
            AttributeValue(attribute, XsdType.STRING, "value"),
        ),
    )
    source = tmp_path / "all.jsonl"
    _program(source, *opcodes)
    output = tmp_path / "all.json"
    assert main(["run", str(source), "--to", "json", "-o", str(output)]) == 0
    graph = tiergraph.loads(output.read_bytes())
    assert graph.attributes[0].lexical == "value"


def test_polyadic_relate_round_trips_through_run_and_step(tmp_path: Path) -> None:
    """JSONL decoding preserves ordered polyadic endpoints on both CLI paths."""
    namespace = "urn:polyadic-program"
    tier = QualifiedName(namespace, "items")
    relation_name = QualifiedName(namespace, "ordered")
    side = RelationSideDeclaration((RelationEndpointKind.ITEM,), (tier,), 1, 2)
    relation = PolyadicRelationInstance(
        relation_name,
        (ItemRef(tier, 0),),
        (ItemRef(tier, 1), ItemRef(tier, 0)),
    )
    program = Program(
        (
            DeclareNamespace(NamespaceDeclaration("p", namespace)),
            DeclareTier(TierDeclaration(tier, "Items")),
            AddItem(tier),
            AddItem(tier),
            DeclareRelation(PolyadicRelationDeclaration(relation_name, side, side)),
            Relate(relation),
        )
    )
    source = tmp_path / "polyadic.jsonl"
    _program(source, *program.opcodes)

    decoded = cli._read_program(str(source))
    assert decoded.unroll().graph == program.unroll().graph
    assert decoded.opcodes[-1] == Relate(relation)

    run_output = tmp_path / "run.json"
    assert main(["run", str(source), "--to", "json", "-o", str(run_output)]) == 0
    assert tiergraph.loads(run_output.read_bytes()) == program.unroll().graph

    step_output = tmp_path / "steps.jsonl"
    assert main(["step", str(source), "-o", str(step_output)]) == 0
    final_step = json.loads(step_output.read_text().splitlines()[-1])
    assert final_step["graph"] == program.unroll().graph.to_data()


def test_public_opcode_data_shapes_decode_exactly() -> None:
    ns = "urn:decode"
    tier = QualifiedName(ns, "tier")
    other = QualifiedName(ns, "other")
    relation = QualifiedName(ns, "relation")
    attribute = QualifiedName(ns, "attribute")
    item = tiergraph.ItemRef(tier, 0)
    position = PositionRef(tier, 0)
    durable_item = tiergraph.DurableItemRef("item-id")
    durable_position = tiergraph.DurablePositionRef(durable_item, BoundarySide.AFTER)
    tier_position = tiergraph.DurablePositionRef(tier, BoundarySide.BEFORE)
    value = AttributeValue(attribute, XsdType.STRING, "value")
    side = RelationSideDeclaration((RelationEndpointKind.ITEM,), (tier,), maximum=None)
    declarations = (
        SimpleRelationDeclaration(relation, tier, other, (value,)),
        BipartiteRelationDeclaration(
            relation,
            other,
            other,
            RelationEndpointKind.ITEM,
            RelationEndpointKind.BOUNDARY,
            True,
            True,
            (value,),
        ),
        PolyadicRelationDeclaration(
            relation, side, side, True, True, True, True, relation, (value,)
        ),
        PolyadicRelationDeclaration(relation, side, side),
    )
    opcodes: list[Any] = [
        *(DeclareRelation(declaration) for declaration in declarations),
        PromoteItem(item, "new-item"),
        PromotePosition(position, "new-position"),
        Relate(RelationInstance(relation, item, durable_position, "link", (value,))),
        AttachValue(AttributeDomain.TIER, tier, value),
        AttachValue(AttributeDomain.ITEM, item, value),
        AttachValue(AttributeDomain.ITEM, durable_item, value),
        AttachValue(AttributeDomain.POSITION, position, value),
        AttachValue(AttributeDomain.POSITION, durable_position, value),
        AttachValue(AttributeDomain.POSITION, tier_position, value),
        AttachValue(AttributeDomain.RELATION_INSTANCE, 0, value),
        Repeat(1, (AddItem(tier),)),
    ]
    for opcode in opcodes:
        assert cli._opcode(opcode.to_data(), "record") == opcode


@pytest.mark.parametrize(
    "value,message",
    [
        (None, "opcode object"),
        ({"opcode": 1}, "opcode object"),
        ({"opcode": "add_item", "extra": 1}, "fields must be"),
        ({"opcode": "repeat", "count": 1, "body": {}}, "must be an array"),
    ],
)
def test_opcode_shape_errors(value: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        cli._opcode(value, "record")


def test_reference_and_collection_shape_errors() -> None:
    with pytest.raises(ValueError, match="must be an array"):
        cli._attributes({}, "attributes")
    with pytest.raises(ValueError, match="endpoint object"):
        cli._endpoint(None, "endpoint")
    with pytest.raises(ValueError, match="unknown reference shape"):
        cli._endpoint({}, "endpoint")
    with pytest.raises(ValueError, match="unknown shape"):
        cli._endpoint({"anchor": {}, "side": "before"}, "endpoint")
    with pytest.raises(ValueError, match="endpoint_kinds and tiers"):
        cli._side(
            {
                "endpoint_kinds": {},
                "tiers": [],
                "minimum": 1,
                "maximum": 1,
                "allow_empty": False,
            },
            "side",
        )
    polyadic = PolyadicRelationInstance(QualifiedName("urn:x", "r"), (), ()).to_data()
    polyadic["sources"] = {}
    with pytest.raises(ValueError, match="sources and targets must be arrays"):
        cli._relation_instance(polyadic, "relation")
    with pytest.raises(ValueError, match="must contain at most one"):
        data = PolyadicRelationDeclaration(
            QualifiedName("urn:x", "r"),
            RelationSideDeclaration((RelationEndpointKind.ITEM,)),
            RelationSideDeclaration((RelationEndpointKind.ITEM,)),
        ).to_data()
        data["targets_subset_of"] = [{}, {}]
        cli._relation_declaration(data, "declaration")
    with pytest.raises(ValueError, match="kind .* is unknown"):
        cli._relation_declaration({"kind": "bad"}, "declaration")
    with pytest.raises(ValueError, match="must be an object"):
        cli._relation_declaration(None, "declaration")


def test_stdin_stdout_limits_and_type_normalization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsysbinary: pytest.CaptureFixture[bytes],
) -> None:
    graph_bytes = tiergraph.dump_bytes(tiergraph.Graph((), (), ()))
    stdin = io.TextIOWrapper(io.BytesIO(graph_bytes))
    monkeypatch.setattr(sys, "stdin", stdin)
    assert main(["convert", "-", "--to", "bytes"]) == 0
    assert capsysbinary.readouterr().out == graph_bytes

    program = io.TextIOWrapper(io.BytesIO(b'{"machine_version":"1"}'))
    monkeypatch.setattr(sys, "stdin", program)
    assert main(["run", "-", "--to", "bytes", "-o", str(tmp_path / "out")]) == 0

    source = tmp_path / "large"
    source.write_bytes(b'{"machine_version":"1"}\n{}')
    monkeypatch.setattr(machine_codec, "_JSONL_LINE_BYTES", 2)
    assert main(["run", str(source), "--to", "json"]) == 1
    monkeypatch.setattr(machine_codec, "_JSONL_LINE_BYTES", 1024)
    monkeypatch.setattr(machine_codec, "MAX_DOCUMENT_BYTES", 2)
    assert main(["run", str(source), "--to", "json"]) == 1

    typed = tmp_path / "typed"
    typed.write_bytes(
        b'{"machine_version":"1"}\n'
        b'{"opcode":"add_item","tier":{"namespace":1,"local_name":"x"},'
        b'"item":{"durable_id":null,"attributes":[]}}'
    )
    monkeypatch.setattr(machine_codec, "MAX_DOCUMENT_BYTES", 1024)
    assert main(["run", str(typed), "--to", "json"]) == 1


def test_atomic_output_cleanup_and_renderer_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"source")
    output = tmp_path / "output"

    def refuse_replace(source_name: str, output_name: Path) -> None:
        del source_name, output_name
        raise OSError("replace refused")

    monkeypatch.setattr("tiergraph.cli.os.replace", refuse_replace)
    with pytest.raises(OSError, match="replace refused"):
        cli._write_output(str(source), str(output), b"new")
    assert list(tmp_path.glob(".output.*")) == []
    with pytest.raises(ValueError, match="graph must be"):
        cli._render(None, False)  # type: ignore[arg-type]


def test_secondary_cleanup_and_decoder_type_normalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "output"

    def remove_then_refuse(temporary: str, output_name: Path) -> None:
        del output_name
        os.unlink(temporary)
        raise OSError("replace refused")

    monkeypatch.setattr("tiergraph.cli.os.replace", remove_then_refuse)
    with pytest.raises(OSError, match="replace refused"):
        cli._write_output("-", str(output), b"new")

    program = tmp_path / "program"
    program.write_bytes(b'{"machine_version":"1"}\n{"opcode":"anything"}')
    monkeypatch.setattr(
        machine_codec,
        "_decode_opcode",
        lambda value, path: (_ for _ in ()).throw(TypeError("typed")),
    )
    with pytest.raises(ValueError, match="typed"):
        cli._read_program(str(program))


def test_jsonl_recursion_error_is_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    program = tmp_path / "program"
    program.write_bytes(b'{"machine_version":"1"}')
    monkeypatch.setattr(
        cli,
        "_read_program",
        lambda filename: (_ for _ in ()).throw(RecursionError("deep")),
    )
    assert main(["run", str(program), "--to", "json"]) == 1
    assert "ValueError: deep" in capsys.readouterr().err


def test_jsonl_depth_fallback_and_scanner_escape_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    program = tmp_path / "program"
    program.write_bytes(b'{"machine_version":"1"}')
    monkeypatch.setattr(
        "tiergraph.machine_codec.json.loads",
        lambda line: (_ for _ in ()).throw(RecursionError("parser recursion")),
    )
    with pytest.raises(
        ValueError,
        match=f"JSONL line 1: JSON nesting depth exceeds limit {tiergraph.MAX_JSON_DEPTH}",
    ):
        cli._read_program(str(program))

    # An escaped quote exercises both escape-state transitions in the byte scanner.
    machine_codec._check_jsonl_depth(
        bytes((ord('"'), ord("\\"), ord('"'), ord('"'))), 1
    )
    with pytest.raises(ValueError, match="JSON nesting depth exceeds limit"):
        cli._opcode({}, "line 2", tiergraph.MAX_JSON_DEPTH + 1)
