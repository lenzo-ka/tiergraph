"""The CLI validates, transforms, inspects, renders, and executes public values."""

from __future__ import annotations

import argparse
import ast
import io
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import tiergraph
import tiergraph.cli as cli
import tiergraph.machine_codec as machine_codec
from tests.conformance.traversal import TraversalLawSuite
from tests.test_clock import (
    BINDING as CLOCK_BINDING,
)
from tests.test_clock import (
    CLOCK as CLOCK_TIER,
)
from tests.test_clock import (
    RATE as CLOCK_RATE,
)
from tests.test_clock import (
    SEGMENT as CLOCK_SEGMENT,
)
from tests.test_clock import (
    SYNTAX as CLOCK_SYNTAX,
)
from tests.test_clock import (
    UNIT as CLOCK_UNIT,
)
from tests.test_clock import (
    clock_profile_data,
    ipakit_shape,
    with_stored_timing,
)
from tests.test_clock import (
    fixture as clock_fixture,
)
from tests.test_spanview import fixture as span_fixture
from tests.test_spanview import profile_data as span_profile_data
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
    GrammarDeclaration,
    GrammarHole,
    GrammarRule,
    GrammarTerminal,
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
from tiergraph.schema import json_schema, shape_hash


def _empty(path: Path) -> tiergraph.Graph:
    graph = tiergraph.Graph((), (), ())
    path.write_bytes(tiergraph.dump_bytes(graph))
    return graph


def _path_graph(path: Path) -> tiergraph.Graph:
    tier = QualifiedName("urn:path", "tokens")
    graph = tiergraph.Graph(
        (NamespaceDeclaration("p", "urn:path"),),
        (Tier(TierDeclaration(tier, "Tokens"), (Item("alpha"), Item("beta"))),),
        (),
    )
    path.write_bytes(tiergraph.dump_bytes(graph))
    return graph


def _walk_graph(path: Path, *, acyclic: bool = True) -> tiergraph.Graph:
    graph = TraversalLawSuite(tiergraph.Walk).graph(acyclic=acyclic)
    path.write_bytes(tiergraph.dump_bytes(graph))
    return graph


def _walk_args(path: Path, *extra: str) -> list[str]:
    return [
        "walk",
        str(path),
        "--relation-namespace",
        "urn:test:traversal",
        "--relation-local",
        "contains",
        *extra,
    ]


def _item_path(index: int) -> str:
    return f"/items/structural/urn:test:traversal/nodes/{index}"


def test_select_query_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The selection command evaluates compounds and diagnoses refused queries."""
    source = tmp_path / "graph.json"
    query = tmp_path / "query.json"
    _path_graph(source)
    query.write_text(
        json.dumps(
            {
                "op": "difference",
                "left": {
                    "op": "union",
                    "args": [
                        {
                            "select": "items",
                            "tier": {
                                "namespace": "urn:path",
                                "local_name": "tokens",
                            },
                        },
                        {"select": "item", "path": "/items/durable/alpha"},
                    ],
                },
                "right": {"select": "item", "path": "/items/durable/beta"},
            }
        ),
        encoding="utf-8",
    )
    assert main(["select", str(source), "--query", str(query)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output == {
        "nodes": [
            {
                "kind": "item",
                "reference": {
                    "tier": {"namespace": "urn:path", "local_name": "tokens"},
                    "index": 0,
                },
            }
        ]
    }

    query.write_text('{"op":"union","args":[]}', encoding="utf-8")
    assert main(["select", str(source), "--query", str(query)]) == 1
    assert "ValueError" in capsys.readouterr().err

    query.write_text(
        '{"select":"item","path":"/positions/structural/urn:path/tokens/0"}',
        encoding="utf-8",
    )
    assert main(["select", str(source), "--query", str(query)]) == 1
    assert "did not resolve to an item" in capsys.readouterr().err


def _structural_path(kind: str, namespace: str, local: str, index: int) -> str:
    """Spell the fixture's simple structural item or position path."""
    return f"/{kind}/structural/{namespace}/{local}/{index}"


def _program(path: Path, *opcodes: object, newline: bytes = b"\n") -> None:
    records = [{"machine_version": tiergraph.MACHINE_VERSION}]
    records.extend(opcode.to_data() for opcode in opcodes)  # type: ignore[attr-defined]
    path.write_bytes(newline.join(json.dumps(record).encode() for record in records))


def _span_files(
    tmp_path: Path,
) -> tuple[Path, Path, tiergraph.Graph, tiergraph.SpanViewProfile]:
    """Write the shared span-view graph and declarative profile fixture."""
    graph, profile = span_fixture()
    graph_path = tmp_path / "graph.json"
    profile_path = tmp_path / "profile.json"
    graph_path.write_bytes(tiergraph.dump_bytes(graph))
    profile_path.write_text(json.dumps(span_profile_data(profile)), encoding="utf-8")
    return graph_path, profile_path, graph, profile


@pytest.mark.parametrize("format_name", ("text", "json", "jsonl", "html", "dot"))
def test_span_render_formats(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    format_name: str,
) -> None:
    """Every span renderer writes its already-formatted public string."""
    graph_path, profile_path, graph, profile = _span_files(tmp_path)
    assert (
        main(
            [
                "span",
                "render",
                str(graph_path),
                "--profile",
                str(profile_path),
                "--format",
                format_name,
            ]
        )
        == 0
    )
    view = tiergraph.span_view(graph, profile)
    expected = {
        "text": tiergraph.to_text(view),
        "json": tiergraph.to_json(view),
        "jsonl": tiergraph.to_jsonl(view),
        "html": tiergraph.to_html(view),
        "dot": __import__("tiergraph_dot").dumps_spans(graph, profile),
    }[format_name]
    assert capsys.readouterr().out == expected


def test_span_render_options_and_misuse_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Span-only flags are forwarded and refused outside their formats."""
    graph_path, profile_path, graph, profile = _span_files(tmp_path)
    common = [
        "span",
        "render",
        str(graph_path),
        "--profile",
        str(profile_path),
    ]
    assert main([*common, "--format", "jsonl", "--jsonl-record", "span"]) == 0
    assert capsys.readouterr().out == tiergraph.to_jsonl(
        tiergraph.span_view(graph, profile), record="span"
    )
    assert main([*common, "--format", "text", "--alternatives"]) == 0
    assert capsys.readouterr().out == tiergraph.to_text(
        tiergraph.span_view(graph, profile, alternatives=True), alternatives=True
    )
    assert main([*common, "--format", "dot", "--include-empty-tiers"]) == 0
    assert capsys.readouterr().out.startswith("digraph tiergraph")

    assert main([*common, "--format", "text", "--jsonl-record", "input"]) == 1
    assert "--jsonl-record requires --format jsonl" in capsys.readouterr().err
    assert main([*common, "--format", "html", "--include-empty-tiers"]) == 1
    assert "--include-empty-tiers requires --format dot" in capsys.readouterr().err


def _grammar(path: Path, *, unit: bool = False) -> GrammarDeclaration:
    namespace = "urn:test:grammar-cli"
    sentence = QualifiedName(namespace, "S")
    choice = QualifiedName(namespace, "A")
    text_name = QualifiedName(namespace, "text")
    variable_name = QualifiedName(namespace, "variable")
    weight_name = QualifiedName(namespace, "weight")

    def terminal(value: str) -> GrammarTerminal:
        return GrammarTerminal(AttributeValue(text_name, XsdType.STRING, value))

    def hole(value: str) -> GrammarHole:
        return GrammarHole(AttributeValue(variable_name, XsdType.STRING, value), choice)

    def weight(value: str) -> AttributeValue:
        return AttributeValue(weight_name, XsdType.DECIMAL, value)

    rules = (
        (GrammarRule(sentence, (hole("a"),), (hole("a"),)),)
        if unit
        else (
            GrammarRule(
                sentence, (terminal("x"),), (terminal("x"),), weight=weight("1.5")
            ),
            GrammarRule(
                sentence, (terminal("x"),), (terminal("x"),), weight=weight("2.5")
            ),
        )
    )
    declaration = GrammarDeclaration((sentence, choice), sentence, rules)
    path.write_text(json.dumps(declaration.to_data()), encoding="utf-8")
    return declaration


def test_grammar_commands_recognize_count_and_best(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Nested grammar commands emit their exact deterministic JSON objects."""
    source = tmp_path / "grammar.json"
    _grammar(source)
    assert main(["grammar", "recognize", str(source), "--tokens-json", '["x"]']) == 0
    assert json.loads(capsys.readouterr().out) == {"recognized": True}
    assert main(["grammar", "count", str(source), "--tokens-json", '["x"]']) == 0
    assert json.loads(capsys.readouterr().out) == {"count": 2}
    assert main(["grammar", "best", str(source), "--tokens-json", '["x"]']) == 0
    assert json.loads(capsys.readouterr().out) == {
        "derivations": [
            {
                "weight": "1.5",
                "witness": [
                    "urn:tiergraph:grammar:chart:chart-items:4",
                    "urn:tiergraph:grammar:chart:applications:4",
                ],
            }
        ]
    }
    assert (
        main(["grammar", "best", str(source), "--tokens-json", '["x"]', "--count", "2"])
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {
        "derivations": [
            {
                "weight": "1.5",
                "witness": [
                    "urn:tiergraph:grammar:chart:chart-items:4",
                    "urn:tiergraph:grammar:chart:applications:4",
                ],
            },
            {
                "weight": "2.5",
                "witness": [
                    "urn:tiergraph:grammar:chart:chart-items:4",
                    "urn:tiergraph:grammar:chart:applications:5",
                ],
            },
        ]
    }


@pytest.mark.parametrize("tokens", ("wat", "{}", '["x", 1]'))
def test_grammar_command_refuses_bad_tokens_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], tokens: str
) -> None:
    """Invalid JSON and non-string arrays use the normal exit-one diagnostic."""
    source = tmp_path / "grammar.json"
    _grammar(source)
    assert main(["grammar", "recognize", str(source), "--tokens-json", tokens]) == 1
    assert "tiergraph: grammar: ValueError:" in capsys.readouterr().err


def test_grammar_command_reports_bad_grammar_and_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Malformed grammar and nonpositive caps exit one; unit folds run."""
    malformed = tmp_path / "bad.json"
    malformed.write_text("{}", encoding="utf-8")
    assert main(["grammar", "recognize", str(malformed), "--tokens-json", "[]"]) == 1
    assert "grammar fields" in capsys.readouterr().err

    unit = tmp_path / "unit.json"
    _grammar(unit, unit=True)
    for command in ("count", "best"):
        assert main(["grammar", command, str(unit), "--tokens-json", '["x"]']) == 0
        capsys.readouterr()

    source = tmp_path / "grammar.json"
    _grammar(source)
    assert (
        main(
            [
                "grammar",
                "best",
                str(source),
                "--tokens-json",
                '["x"]',
                "--count",
                "0",
            ]
        )
        == 1
    )
    assert "must be positive" in capsys.readouterr().err


def test_clock_commands_query_full_declarative_profile(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """All four clock queries emit exact structural and physical JSON data."""
    graph_path = tmp_path / "graph.json"
    profile_path = tmp_path / "clock.json"
    graph_path.write_bytes(tiergraph.dump_bytes(ipakit_shape()))
    profile_path.write_text(json.dumps(clock_profile_data()), encoding="utf-8")
    common = [str(graph_path), "--profile", str(profile_path)]

    assert main(["clock", "positions", *common]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "clock_tier": {
            "namespace": CLOCK_SEGMENT.namespace,
            "local_name": "clock",
        },
        "positions": [
            {"index": 0, "tick": 0, "gap": 0},
            {"index": 1, "tick": 1, "gap": 0},
            {"index": 2, "tick": 1, "gap": 1},
            {"index": 3, "tick": 2, "gap": 0},
        ],
    }

    position_path = _structural_path(
        "positions", CLOCK_SEGMENT.namespace, CLOCK_SEGMENT.local_name, 1
    )
    assert main(["clock", "position", *common, "--position", position_path]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "position": {"tier": CLOCK_SEGMENT.to_data(), "index": 1},
        "clock_index": 2,
        "refined": {"tick": 1, "gap": 1},
    }

    assert (
        main(
            [
                "clock",
                "extent",
                *common,
                "--tier-namespace",
                CLOCK_SEGMENT.namespace,
                "--tier-local",
                CLOCK_SEGMENT.local_name,
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {
        "tier": CLOCK_SEGMENT.to_data(),
        "start": {"tick": 1, "gap": 0},
        "end": {"tick": 2, "gap": 0},
    }

    item_path = _structural_path(
        "items", CLOCK_SEGMENT.namespace, CLOCK_SEGMENT.local_name, 1
    )
    assert main(["clock", "item", *common, "--item", item_path]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "item": {"tier": CLOCK_SEGMENT.to_data(), "index": 1},
        "structural": {
            "start": {"tick": 1, "gap": 1},
            "end": {"tick": 2, "gap": 0},
        },
        "physical": {"start": "0.1", "duration": "0.04", "unit": "s"},
        "exact_duration": None,
    }


def test_clock_commands_report_profile_path_kind_and_untimed_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Clock decoding and query refusals use the ordinary exit-one path."""
    graph_path = tmp_path / "graph.json"
    profile_path = tmp_path / "clock.json"
    graph_path.write_bytes(tiergraph.dump_bytes(ipakit_shape()))
    profile_path.write_text("{}", encoding="utf-8")
    common = [str(graph_path), "--profile", str(profile_path)]
    assert main(["clock", "positions", *common]) == 1
    assert "clock profile fields" in capsys.readouterr().err

    malformed = clock_profile_data()
    malformed["clock_tier"] = {
        "namespace": [CLOCK_TIER.namespace],
        "local_name": CLOCK_TIER.local_name,
    }
    profile_path.write_text(json.dumps(malformed), encoding="utf-8")
    assert main(["clock", "positions", *common]) == 1
    assert (
        "clock profile.clock_tier.namespace must be a string" in capsys.readouterr().err
    )

    profile_path.write_text(json.dumps(clock_profile_data()), encoding="utf-8")
    missing = _structural_path(
        "items", CLOCK_SEGMENT.namespace, CLOCK_SEGMENT.local_name, 99
    )
    assert main(["clock", "item", *common, "--item", missing]) == 1
    assert "tiergraph: clock:" in capsys.readouterr().err

    item = _structural_path(
        "items", CLOCK_SEGMENT.namespace, CLOCK_SEGMENT.local_name, 0
    )
    assert main(["clock", "position", *common, "--position", item]) == 1
    assert "did not resolve to a position" in capsys.readouterr().err

    position = _structural_path(
        "positions", CLOCK_SEGMENT.namespace, CLOCK_SEGMENT.local_name, 0
    )
    assert main(["clock", "item", *common, "--item", position]) == 1
    assert "did not resolve to an item" in capsys.readouterr().err

    assert (
        main(
            [
                "clock",
                "extent",
                *common,
                "--tier-namespace",
                CLOCK_SYNTAX.namespace,
                "--tier-local",
                CLOCK_SYNTAX.local_name,
            ]
        )
        == 1
    )
    assert "is untimed" in capsys.readouterr().err


def test_clock_item_emits_uniform_duration_and_reraises_other_refusals(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Item queries encode a uniform rate and suppress no unrelated error."""
    graph_path = tmp_path / "graph.json"
    profile_path = tmp_path / "clock.json"
    graph_path.write_bytes(tiergraph.dump_bytes(clock_fixture()))
    profile_path.write_text(
        json.dumps(
            {
                "clock_tier": CLOCK_TIER.to_data(),
                "binding_relation": CLOCK_BINDING.to_data(),
                "rate_attribute": CLOCK_RATE.to_data(),
                "unit_attribute": CLOCK_UNIT.to_data(),
                "tick_attribute": None,
                "gap_attribute": None,
                "untimed_attribute": None,
                "start_attribute": None,
                "duration_attribute": None,
            }
        ),
        encoding="utf-8",
    )
    item = _structural_path(
        "items", CLOCK_SEGMENT.namespace, CLOCK_SEGMENT.local_name, 0
    )
    args = [
        "clock",
        "item",
        str(graph_path),
        "--profile",
        str(profile_path),
        "--item",
        item,
    ]
    assert main(args) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["physical"] == {"start": "0.1", "duration": "0.1", "unit": "s"}
    assert value["exact_duration"] == {"ticks": 1, "rate": "10.0"}

    def refuse(
        self: tiergraph.ClockProfile, tier: QualifiedName, index: int
    ) -> tuple[int, Any]:
        del self, tier, index
        raise ValueError("different duration refusal")

    monkeypatch.setattr(tiergraph.ClockProfile, "duration", refuse)
    assert main(args) == 1
    assert "different duration refusal" in capsys.readouterr().err


def test_clock_item_encodes_null_and_canonical_small_physical_timing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Absent timing stays null and small decimals use fixed canonical notation."""
    graph_path = tmp_path / "graph.json"
    profile_path = tmp_path / "clock.json"
    profile_path.write_text(json.dumps(clock_profile_data()), encoding="utf-8")
    item = _structural_path(
        "items", CLOCK_SEGMENT.namespace, CLOCK_SEGMENT.local_name, 0
    )
    args = [
        "clock",
        "item",
        str(graph_path),
        "--profile",
        str(profile_path),
        "--item",
        item,
    ]

    graph = ipakit_shape()
    tiers = tuple(
        replace(
            tier,
            items=tuple(replace(member, attributes=()) for member in tier.items),
        )
        for tier in graph.tiers
    )
    graph_path.write_bytes(tiergraph.dump_bytes(replace(graph, tiers=tiers)))
    assert main(args) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["physical"] is None
    assert value["exact_duration"] is None

    graph = with_stored_timing(
        ipakit_shape(), CLOCK_SEGMENT, 0, "0.0000001", "0.0000001"
    )
    graph_path.write_bytes(tiergraph.dump_bytes(graph))
    assert main(args) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["physical"] == {
        "start": "0.0000001",
        "duration": "0.0000001",
        "unit": "s",
    }


def test_version_default_help_and_every_command_help(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--version"]) == 0
    assert json.loads(capsys.readouterr().out) == {"version": tiergraph.__version__}
    assert main([]) == 0
    assert (
        "{validate,render,inspect,convert,schema,run,step,walk,path,grammar,clock,span,select}"
        in capsys.readouterr().out
    )
    for command in (
        "validate",
        "render",
        "inspect",
        "convert",
        "schema",
        "run",
        "step",
        "walk",
        "path",
        "grammar",
        "clock",
        "span",
        "select",
    ):
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
        "schema",
        "run",
        "step",
        "walk",
        "path",
        "grammar",
        "clock",
        "span",
        "select",
    ]


def test_walk_forward_inverse_capped_and_multiple_sources(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Walk emits exact public result data for both directions and source unions."""
    source = tmp_path / "diamond.json"
    _walk_graph(source)

    assert main(_walk_args(source, "--source", _item_path(0))) == 0
    assert json.loads(capsys.readouterr().out) == {
        "nodes": [
            {
                "kind": "item",
                "reference": {
                    "tier": {
                        "namespace": "urn:test:traversal",
                        "local_name": "nodes",
                    },
                    "index": index,
                },
            }
            for index in (1, 2, 3)
        ],
        "truncated": False,
        "cap": None,
    }

    assert (
        main(
            _walk_args(
                source,
                "--source",
                _item_path(3),
                "--direction",
                "inverse",
                "--cap",
                "1",
            )
        )
        == 0
    )
    inverse = json.loads(capsys.readouterr().out)
    assert [node["reference"]["index"] for node in inverse["nodes"]] == [1, 2]
    assert inverse["truncated"] is True
    assert inverse["cap"] == 1

    assert main(_walk_args(source, "--source", _item_path(0), "--cap", "1")) == 0
    capped = json.loads(capsys.readouterr().out)
    assert [node["reference"]["index"] for node in capped["nodes"]] == [1, 2]
    assert capped["truncated"] is True
    assert capped["cap"] == 1

    assert (
        main(
            _walk_args(
                source,
                "--source",
                _item_path(1),
                "--source",
                _item_path(2),
            )
        )
        == 0
    )
    union = json.loads(capsys.readouterr().out)
    assert [node["reference"]["index"] for node in union["nodes"]] == [3]
    assert union["truncated"] is False
    assert union["cap"] is None


def test_walk_failures_use_the_normal_diagnostic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Path, relation, cyclic, and nonpositive-cap refusals exit one cleanly."""
    source = tmp_path / "diamond.json"
    _walk_graph(source)
    assert (
        main(
            _walk_args(
                source,
                "--source",
                "/positions/structural/urn:test:traversal/nodes/0",
            )
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {
        "nodes": [],
        "truncated": False,
        "cap": None,
    }
    cases = (
        _walk_args(source, "--source", "/items/durable/missing"),
        [
            *_walk_args(source, "--source", _item_path(0)),
            "--relation-local",
            "missing",
        ],
        _walk_args(source, "--source", _item_path(0), "--cap", "0"),
    )
    for arguments in cases:
        assert main(arguments) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.startswith("tiergraph: walk:")

    _walk_graph(source, acyclic=False)
    assert main(_walk_args(source, "--source", _item_path(0))) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("tiergraph: walk: ValueError:")
    assert "not declared acyclic" in captured.err


def test_argparse_usage_error() -> None:
    with pytest.raises(SystemExit) as raised:
        main(["convert", "-"])
    assert raised.value.code == 2
    with pytest.raises(SystemExit) as raised:
        main(
            _walk_args(
                Path("graph.json"),
                "--source",
                _item_path(0),
                "--cap",
                "not-an-integer",
            )
        )
    assert raised.value.code == 2


def test_path_resolve_and_spell(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "graph.json"
    _path_graph(source)

    expected_item = {
        "kind": "item",
        "path": "/items/structural/urn:path/tokens/1",
        "current": {
            "tier": {"namespace": "urn:path", "local_name": "tokens"},
            "index": 1,
        },
    }
    assert (
        main(
            [
                "path",
                "resolve",
                str(source),
                "/items/structural/urn:path/tokens/1",
            ]
        )
        == 0
    )
    assert capsys.readouterr().out.encode() == cli._json_bytes(expected_item)

    expected_durable = {
        **expected_item,
        "path": "/items/durable/beta",
    }
    assert main(["path", "resolve", str(source), "/items/durable/beta"]) == 0
    assert capsys.readouterr().out.encode() == cli._json_bytes(expected_durable)

    expected_position = {
        "kind": "position",
        "path": "/positions/durable/item/beta/after",
        "current": {
            "tier": {"namespace": "urn:path", "local_name": "tokens"},
            "index": 2,
        },
    }
    assert (
        main(
            [
                "path",
                "resolve",
                str(source),
                "/positions/durable/item/beta/after",
            ]
        )
        == 0
    )
    assert capsys.readouterr().out.encode() == cli._json_bytes(expected_position)

    spell_cases = (
        (
            [
                "--kind",
                "item",
                "--tier-namespace",
                "urn:path",
                "--tier-local",
                "tokens",
                "--index",
                "1",
            ],
            "/items/structural/urn:path/tokens/1",
        ),
        (["--kind", "item", "--durable-id", "beta"], "/items/durable/beta"),
        (
            [
                "--kind",
                "position",
                "--tier-namespace",
                "urn:path",
                "--tier-local",
                "tokens",
                "--index",
                "0",
            ],
            "/positions/structural/urn:path/tokens/0",
        ),
        (
            [
                "--kind",
                "position",
                "--anchor-item-id",
                "beta",
                "--side",
                "after",
            ],
            "/positions/durable/item/beta/after",
        ),
        (
            [
                "--kind",
                "position",
                "--anchor-tier-namespace",
                "urn:path",
                "--anchor-tier-local",
                "tokens",
                "--side",
                "before",
            ],
            "/positions/durable/tier/urn:path/tokens/before",
        ),
    )
    for flags, path in spell_cases:
        assert main(["path", "spell", str(source), *flags]) == 0
        assert capsys.readouterr().out.encode() == cli._json_bytes({"path": path})
        assert main(["path", "resolve", str(source), path]) == 0
        assert json.loads(capsys.readouterr().out)["path"] == path


@pytest.mark.parametrize(
    ("flags", "message"),
    [
        (
            ["--kind", "item", "--durable-id", "alpha", "--side", "after"],
            "item flags cannot include position anchor flags",
        ),
        (
            [
                "--kind",
                "item",
                "--tier-namespace",
                "urn:path",
                "--tier-local",
                "tokens",
                "--index",
                "0",
                "--anchor-item-id",
                "alpha",
            ],
            "item flags cannot include position anchor flags",
        ),
        (
            ["--kind", "item"],
            "item requires either --durable-id or --tier-namespace, --tier-local, and --index",
        ),
        (
            ["--kind", "position", "--durable-id", "alpha"],
            "position flags cannot include --durable-id",
        ),
        (
            [
                "--kind",
                "position",
                "--tier-namespace",
                "urn:path",
                "--tier-local",
                "tokens",
                "--index",
                "0",
                "--anchor-item-id",
                "alpha",
            ],
            "structural position flags cannot include durable anchors",
        ),
        (
            [
                "--kind",
                "position",
                "--tier-namespace",
                "urn:path",
                "--tier-local",
                "tokens",
                "--index",
                "0",
                "--side",
                "after",
            ],
            "structural position flags cannot include --side",
        ),
        (
            ["--kind", "position", "--tier-namespace", "urn:path"],
            "structural position requires --tier-namespace, --tier-local, and --index",
        ),
        (
            ["--kind", "position", "--anchor-item-id", "alpha"],
            "durable position requires --side",
        ),
        (
            ["--kind", "position", "--side", "after"],
            "durable position requires exactly one of --anchor-item-id or --anchor-tier-namespace with --anchor-tier-local",
        ),
    ],
)
def test_path_spell_rejects_incoherent_flags(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    flags: list[str],
    message: str,
) -> None:
    source = tmp_path / "graph.json"
    _path_graph(source)
    assert main(["path", "spell", str(source), *flags]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "tiergraph: path: ValueError:" in captured.err
    assert message in captured.err


def test_path_failure_is_a_clean_diagnostic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "graph.json"
    _empty(source)
    assert main(["path", "resolve", str(source), "not-a-pointer"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("tiergraph: path: PathRefusal: malformed_pointer:")


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


def test_schema_outputs_current_selected_version_and_hash(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["schema"]) == 0
    current_output = capsys.readouterr().out
    current = json.loads(current_output)
    assert current["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert current["$id"].endswith(f"format-{tiergraph.FORMAT_VERSION}.json")
    assert current["properties"]["format_version"] == {
        "const": tiergraph.FORMAT_VERSION
    }
    assert current_output.encode("utf-8") == cli._json_bytes(
        json_schema(tiergraph.FORMAT_VERSION)
    )

    output = tmp_path / "schema.json"
    assert main(["schema", "--format-version", "6", "-o", str(output)]) == 0
    selected = json.loads(output.read_text())
    assert selected["properties"]["format_version"] == {"const": "6"}

    assert main(["schema", "--hash"]) == 0
    assert capsys.readouterr().out == f"{shape_hash()}\n"


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


@pytest.mark.parametrize(
    ("command", "member", "value"),
    [("run", "namespace", ["urn:bad"]), ("step", "local_name", 7)],
)
def test_machine_commands_cleanly_reject_non_string_qname_members(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    command: str,
    member: str,
    value: object,
) -> None:
    """Malformed machine QNames produce the normal exit-one diagnostic."""
    source = tmp_path / "bad-qname.jsonl"
    qname: dict[str, object] = {"namespace": "urn:bad", "local_name": "tier"}
    qname[member] = value
    records = [
        {"machine_version": tiergraph.MACHINE_VERSION},
        {
            "opcode": "declare_tier",
            "declaration": {"name": qname, "long_name": "Tier"},
        },
    ]
    source.write_text("".join(f"{json.dumps(record)}\n" for record in records))

    arguments = [command, str(source)]
    if command == "run":
        arguments.extend(("--to", "json"))
    assert main(arguments) == 1
    error = capsys.readouterr().err
    assert f"tiergraph: {command}: ValueError:" in error
    assert f"line 2.declaration.name.{member} must be a string" in error
    assert "TypeError" not in error
    assert "Traceback" not in error


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


def test_cli_output_emitters_are_audited() -> None:
    """Every dynamic CLI byte emitter remains behind the shared refusal.

    This enumerates the emitters instead of trusting a hand list: any new
    ``json.dumps`` or ``.encode`` in the module fails here until its author
    routes it through the refusal and records it below.  The check is static,
    so it discriminates by listing an emitter set no other revision has, not
    by observing a leak; the behavioral discrimination lives in the matrix.
    """
    source = Path(cli.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    found: set[tuple[str, str]] = set()
    # A module-level emitter has no enclosing function; name it so it is
    # reported as unapproved rather than raising out of the visitor.
    functions: list[str] = ["<module>"]

    class Calls(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            functions.append(node.name)
            self.generic_visit(node)
            functions.pop()

        def visit_Call(self, node: ast.Call) -> None:
            if isinstance(node.func, ast.Attribute):
                if (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "json"
                    and node.func.attr == "dumps"
                ):
                    found.add((functions[-1], "json.dumps"))
                elif node.func.attr == "encode":
                    found.add((functions[-1], "encode"))
            self.generic_visit(node)

    Calls().visit(tree)
    approved = {
        ("main", "json.dumps"),
        ("main", "encode"),
        ("_graph_bytes", "encode"),
        ("_graph_report_bytes", "encode"),
        ("_json_bytes", "json.dumps"),
        ("_json_bytes", "encode"),
        ("_step_bytes", "json.dumps"),
        ("_step_bytes", "encode"),
    }
    assert found == approved, (
        "route any new CLI emitter through the shared wire refusal; do not casually "
        f"widen the approved emitter set (found {sorted(found)!r})"
    )


def test_surrogate_reports_are_refused_with_field_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every CLI report refuses surrogate data with its report or graph path."""
    source = tmp_path / "path-graph.json"
    source.write_text(
        '{"format_version":"6","graph":{"namespaces":'
        '[{"namespace":"\\ud800","prefix":"p"}],"tiers":[{"declaration":'
        '{"long_name":"T","name":"p:t"},"items":[{"durable_id":"a"}]}]}}'
    )
    surrogate = "\ud800"
    profile = tmp_path / "profile.json"
    profile.write_text("{}")
    query = tmp_path / "query.json"
    query.write_text(json.dumps({"select": "item", "path": "/items/durable/a"}))
    program = tmp_path / "program.jsonl"
    _program(program, DeclareNamespace(NamespaceDeclaration("p", surrogate)))

    walk_graph = tmp_path / "walk-graph.json"
    _walk_graph(walk_graph)
    walk_graph.write_bytes(
        walk_graph.read_bytes().replace(b"urn:test:traversal", b"\\ud800")
    )

    graph = ipakit_shape()
    clock_graph = tmp_path / "clock-graph.json"
    clock_graph.write_bytes(
        tiergraph.dump_bytes(graph).replace(
            CLOCK_SEGMENT.namespace.encode(), b"\\ud800"
        )
    )
    clock_profile = tmp_path / "clock-profile.json"
    clock_profile.write_text(
        json.dumps(clock_profile_data()).replace(CLOCK_SEGMENT.namespace, "\\ud800")
    )

    (tmp_path / "span").mkdir()
    span_graph, span_profile, span_value, profile_value = _span_files(tmp_path / "span")
    del span_value, profile_value
    span_data = tiergraph.to_data(tiergraph.loads(span_graph.read_bytes()))
    graph_data = span_data["graph"]
    assert isinstance(graph_data, dict)
    namespaces = graph_data.setdefault("namespaces", [])
    assert isinstance(namespaces, list)
    namespaces.append({"namespace": surrogate, "prefix": "bad"})
    span_graph.write_text(json.dumps(span_data))

    cases = [
        (["inspect", str(source)], "namespaces[0].namespace"),
        (
            [
                "walk",
                str(walk_graph),
                "--source",
                _item_path(0).replace("urn:test:traversal", surrogate),
                "--relation-namespace",
                surrogate,
                "--relation-local",
                "contains",
            ],
            "nodes[0].reference.tier.namespace",
        ),
        (
            ["path", "resolve", str(source), "/items/durable/a"],
            "current.tier.namespace",
        ),
        (
            [
                "path",
                "spell",
                str(source),
                "--kind",
                "item",
                "--tier-namespace",
                surrogate,
                "--tier-local",
                "t",
                "--index",
                "0",
            ],
            "path",
        ),
        (
            ["select", str(source), "--query", str(query)],
            "nodes[0].reference.tier.namespace",
        ),
        (
            ["clock", "positions", str(clock_graph), "--profile", str(clock_profile)],
            "clock_tier.namespace",
        ),
        (
            [
                "clock",
                "item",
                str(clock_graph),
                "--profile",
                str(clock_profile),
                "--item",
                _structural_path("items", surrogate, CLOCK_SEGMENT.local_name, 0),
            ],
            "item.tier.namespace",
        ),
        *[
            (
                [
                    "span",
                    "render",
                    str(span_graph),
                    "--profile",
                    str(span_profile),
                    "--format",
                    name,
                ],
                "namespaces[1].namespace",
            )
            for name in ("text", "json", "jsonl", "html")
        ],
        (["step", str(program)], "declaration.namespace"),
        (["render", str(span_graph)], "namespaces[1].namespace"),
        (["run", str(program), "--to", "dot"], "namespaces[0].namespace"),
    ]
    for arguments, field_path in cases:
        assert main(arguments) == 1, arguments
        error = capsys.readouterr().err
        assert f"{field_path} value " in error, (arguments, error)
        assert "unsupported character U+D800" in error, (arguments, error)
        assert "UnicodeEncodeError" not in error, (arguments, error)


def test_invalid_utf8_profile_remains_an_exit_three_decode_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A real input decoding failure still exercises the UnicodeError arm.

    Output encoding no longer reaches that arm, so this pins the witness that
    keeps it honest: a side-car document whose bytes are not UTF-8 at all.
    """
    graph = tmp_path / "graph.json"
    graph.write_bytes(tiergraph.dump_bytes(ipakit_shape()))
    profile = tmp_path / "profile.json"
    profile.write_bytes(b'{"clock_tier":"\xff\xfe"}')
    assert main(["clock", "positions", str(graph), "--profile", str(profile)]) == 3
    assert "UnicodeDecodeError" in capsys.readouterr().err


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
    # Deliberately exercise the writer's refusal after reader-only validation.
    assert main(["convert", str(source), "--to", "json-compact"]) == 1
    error = capsys.readouterr().err
    assert "ValueError" in error
    assert "namespaces[0].namespace" in error


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
