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
import tiergraph.machine as machine
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
    reference_shape,
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
    BoundaryRef,
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


def test_select_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The selection command evaluates compounds and diagnoses refused selectors."""
    source = tmp_path / "graph.json"
    selector = tmp_path / "selector.json"
    _path_graph(source)
    selector.write_text(
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
    assert main(["select", str(source), "--selector", str(selector)]) == 0
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

    selector.write_text('{"op":"union","args":[]}', encoding="utf-8")
    assert main(["select", str(source), "--selector", str(selector)]) == 1
    assert "ValueError" in capsys.readouterr().err

    selector.write_text(
        '{"select":"item","path":"/positions/structural/urn:path/tokens/0"}',
        encoding="utf-8",
    )
    assert main(["select", str(source), "--selector", str(selector)]) == 1
    assert "did not resolve to an item" in capsys.readouterr().err


def test_select_spells_the_library_concept(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`select` reads a `--selector`; the retired `--query` spelling is refused."""
    source = tmp_path / "graph.json"
    _path_graph(source)
    selector = tmp_path / "selector.json"
    selector.write_text(
        '{"select":"item","path":"/items/durable/alpha"}', encoding="utf-8"
    )

    assert main(["select", str(source), "--selector", str(selector)]) == 0
    assert json.loads(capsys.readouterr().out) == {
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

    with pytest.raises(SystemExit) as raised:
        main(["select", str(source), "--query", str(selector)])
    assert raised.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "required: --selector" in captured.err


def test_select_help_retires_the_query_spelling(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Neither the subcommand listing nor `select --help` says `query`."""
    with pytest.raises(SystemExit) as raised:
        main(["--help"])
    assert raised.value.code == 0
    listing = capsys.readouterr().out
    assert "evaluate a selector" in listing
    assert "selection query" not in listing

    with pytest.raises(SystemExit) as raised:
        main(["select", "--help"])
    assert raised.value.code == 0
    select_help = capsys.readouterr().out
    assert "--selector FILE" in select_help
    assert "query" not in select_help


SEAL_TIER = QualifiedName("urn:seal", "word")


def _sealed_source(tmp_path: Path) -> tuple[Path, tiergraph.Graph]:
    """Write a source graph whose first two word coordinates are under seal."""
    graph = tiergraph.Graph(
        (NamespaceDeclaration("s", "urn:seal"),),
        (
            Tier(
                TierDeclaration(SEAL_TIER, "Words"),
                (Item("w0"), Item("w1"), Item("w2")),
            ),
        ),
        (),
    ).seal(SEAL_TIER, 2)
    source = tmp_path / "source.json"
    source.write_bytes(tiergraph.dump_bytes(graph))
    return source, graph


def _refusal_report(captured: str) -> Any:
    """Read the JSON refusal object that follows the stderr diagnostic line."""
    line, report = captured.split("\n", 1)
    assert line.startswith("tiergraph: discharge: ")
    return json.loads(report)


def test_discharge_seals_certifies_a_result_that_honors_the_seal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An edit beyond the seal is discharged, and the counts stay the seal's own."""
    source, graph = _sealed_source(tmp_path)
    result = tmp_path / "result.json"
    result.write_bytes(
        tiergraph.dump_bytes(graph.insert_item(SEAL_TIER, 3, Item("w3")))
    )

    assert main(["discharge", "seals", str(source), "--result", str(result)]) == 0
    assert json.loads(capsys.readouterr().out) == {"carriers": 1, "sealed_members": 2}


def test_discharge_seals_reports_a_breach_stage_as_data(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A moved sealed member refuses at SEMANTICS and writes no output file."""
    source, graph = _sealed_source(tmp_path)
    tier = graph.tiers[0]
    breached = tmp_path / "breached.json"
    breached.write_bytes(
        tiergraph.dump_bytes(
            replace(
                graph,
                tiers=(
                    replace(
                        tier,
                        items=(tier.items[0], Item("replacement"), tier.items[2]),
                    ),
                ),
            )
        )
    )
    output = tmp_path / "certificate.json"

    assert (
        main(
            [
                "discharge",
                "seals",
                str(source),
                "--result",
                str(breached),
                "--name",
                "replace-word",
                "-o",
                str(output),
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    report = _refusal_report(captured.err)["refusal"]
    assert (report["stage"], report["rank"], report["also"]) == ("semantics", 9, [])
    assert "carried durable id 'w1'" in report["message"]
    assert "replace-word" in report["message"]
    assert not output.exists()


def test_discharge_seals_carries_every_condition_of_a_refused_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A result document meeting two conditions reports both, each with its stage."""
    source, _ = _sealed_source(tmp_path)
    document = json.loads(source.read_bytes())
    del document["graph"]
    document["aa"] = 1
    document["zz"] = 2
    malformed = tmp_path / "malformed.json"
    malformed.write_text(json.dumps(document), encoding="utf-8")

    assert main(["discharge", "seals", str(source), "--result", str(malformed)]) == 1
    report = _refusal_report(capsys.readouterr().err)["refusal"]
    assert (report["stage"], report["rank"]) == ("shape", 6)
    assert [(entry["stage"], entry["message"]) for entry in report["also"]] == [
        ("shape", "document has unknown fields ['aa', 'zz']")
    ]


def test_discharge_reports_a_stage_only_where_the_refusal_declares_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unnamed declaration and an unreadable input keep their own exit codes."""
    source, graph = _sealed_source(tmp_path)
    result = tmp_path / "result.json"
    result.write_bytes(tiergraph.dump_bytes(graph))

    assert (
        main(["discharge", "seals", str(source), "--result", str(result), "--name", ""])
        == 1
    )
    unnamed = capsys.readouterr().err
    assert "must not be empty" in unnamed
    assert '"stage"' not in unnamed

    assert (
        main(
            [
                "discharge",
                "seals",
                str(source),
                "--result",
                str(tmp_path / "absent.json"),
            ]
        )
        == 3
    )
    assert '"stage"' not in capsys.readouterr().err


REWRITE_TIER = QualifiedName("urn:rewrite", "word")


def _rewrite_source(tmp_path: Path) -> tuple[Path, tiergraph.Graph]:
    """Write a source graph asserting six structures, three of them items."""
    graph = tiergraph.Graph(
        (NamespaceDeclaration("r", "urn:rewrite"),),
        (
            Tier(
                TierDeclaration(REWRITE_TIER, "Words"),
                (Item("w0"), Item("w1"), Item("w2")),
            ),
        ),
        (),
    )
    source = tmp_path / "source.json"
    source.write_bytes(tiergraph.dump_bytes(graph))
    return source, graph


def test_discharge_rewrite_certifies_an_effect_the_pair_bears_out(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Both a decoration and a collapse are discharged over the same source.

    The pair is the one `seals` reads, so the flags are the same; what the
    certificate adds is the claim it discharged and how much it was held to.
    The effect is on the wire because the counts do not imply it: a revision and
    a collapse can leave the same disturbance count over the same subjects.
    """
    source, graph = _rewrite_source(tmp_path)
    decorated = tmp_path / "decorated.json"
    decorated.write_bytes(
        tiergraph.dump_bytes(graph.insert_item(REWRITE_TIER, 3, Item("w3")))
    )
    collapsed = tmp_path / "collapsed.json"
    collapsed.write_bytes(
        tiergraph.dump_bytes(graph.remove_item(ItemRef(REWRITE_TIER, 2)))
    )

    assert (
        main(
            [
                "discharge",
                "rewrite",
                str(source),
                "--result",
                str(decorated),
                "--effect",
                "decorate",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {
        "effect": "decorate",
        "subjects": 6,
        "disturbances": 0,
    }

    assert (
        main(
            [
                "discharge",
                "rewrite",
                str(source),
                "--result",
                str(collapsed),
                "--effect",
                "collapse",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {
        "effect": "collapse",
        "subjects": 6,
        "disturbances": 1,
    }


@pytest.mark.parametrize(
    ("claim", "shrink", "fragment"),
    (
        ((), False, "effect is UNDECLARED: say what this rewrite did"),
        (
            ("--effect", "collapse"),
            False,
            "A collapse you cannot exhibit is a declaration that is hiding",
        ),
        (
            ("--effect", "decorate"),
            True,
            "item '{urn:rewrite}word'[2] has no counterpart in the result",
        ),
    ),
)
def test_discharge_rewrite_refuses_a_claim_the_pair_does_not_make_good(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    claim: tuple[str, ...],
    shrink: bool,
    fragment: str,
) -> None:
    """Each unmet claim is one exit-one refusal that writes no certificate.

    Omitting `--effect` is not a usage error: it reaches the library's own
    refusal, which hands back the declaration to be made rather than standing in
    COLLAPSE, the weaker claim. A claim that is false the other way is answered
    with a counterexample instead. An effect refusal declares no stage, so the
    diagnostic line stands alone with no staged object after it, which is the
    rule `seals` and `fold` already follow.
    """
    source, graph = _rewrite_source(tmp_path)
    result = tmp_path / "result.json"
    edited = graph.remove_item(ItemRef(REWRITE_TIER, 2)) if shrink else graph
    result.write_bytes(tiergraph.dump_bytes(edited))
    output = tmp_path / "certificate.json"

    assert (
        main(
            [
                "discharge",
                "rewrite",
                str(source),
                "--result",
                str(result),
                "--name",
                "trim",
                *claim,
                "-o",
                str(output),
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("tiergraph: discharge: ValueError: rewrite 'trim' ")
    assert fragment in captured.err
    assert '"stage"' not in captured.err
    assert not output.exists()


def test_discharge_rewrite_reports_an_input_stage_before_it_weighs_a_claim(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A malformed result reaches the staged reporter the other discharges use."""
    source, _ = _rewrite_source(tmp_path)
    document = json.loads(source.read_bytes())
    document["zz"] = 1
    malformed = tmp_path / "malformed.json"
    malformed.write_text(json.dumps(document), encoding="utf-8")

    assert (
        main(
            [
                "discharge",
                "rewrite",
                str(source),
                "--result",
                str(malformed),
                "--effect",
                "decorate",
            ]
        )
        == 1
    )
    report = _refusal_report(capsys.readouterr().err)["refusal"]
    assert (report["stage"], report["rank"]) == ("shape", 6)
    assert "unknown fields ['zz']" in report["message"]


def test_discharge_fold_certifies_a_claim_the_derivations_bear_out(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A distributive count is discharged, and the certificate says how far it reached.

    The flags are `fold`'s own, prefixed with the verb, because both commands
    assemble the one declaration from the one vocabulary. What the certificate
    adds over a fold's result is the reach of the check: that the derivations
    were enumerated in full and how many there were, which is what separates a
    claim measured against every derivation from one left standing on a law
    search alone.
    """
    source = tmp_path / "plan.json"
    _fold_graph(source, *DIAMOND)

    assert (
        main(
            [
                "discharge",
                *_fold_args(source, "counting", "one"),
                "--exactness",
                "distributive",
            ]
        )
        == 0
    )
    certificate = json.loads(capsys.readouterr().out)
    assert certificate["exactness"] == "distributive"
    assert (certificate["compared"], certificate["derivations"]) == (True, 2)
    assert certificate["probes"] == 3
    assert certificate["result"]["value"] == 2


@pytest.mark.parametrize(
    ("claim", "fragment"),
    (
        ((), "exactness is UNDECLARED: say whether this fold's value is"),
        (("--exactness", "approximate"), "nothing here approximates anything"),
        (("--exactness", "structural"), "declares no star"),
    ),
)
def test_discharge_fold_refuses_a_claim_the_fold_does_not_make_good(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    claim: tuple[str, ...],
    fragment: str,
) -> None:
    """Each unmet claim is one exit-one refusal that writes no certificate.

    Omitting `--exactness` is not a usage error: it reaches the library's own
    refusal, which hands back the declaration to be made. An exactness refusal
    declares no stage, so the diagnostic line stands alone with no staged object
    after it, which is the rule the seal path already follows -- the object
    appears where the refusal carries a stage and nowhere else.
    """
    source = tmp_path / "plan.json"
    _fold_graph(source, *DIAMOND)
    output = tmp_path / "certificate.json"

    assert (
        main(
            [
                "discharge",
                *_fold_args(source, "counting", "one", *claim),
                "-o",
                str(output),
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("tiergraph: discharge: ValueError: fold 'fold' ")
    assert fragment in captured.err
    assert '"stage"' not in captured.err
    assert not output.exists()


def test_discharge_fold_reports_an_input_stage_before_it_weighs_a_claim(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A malformed graph reaches the staged reporter the seal path already uses.

    Both discharges read their inputs through the one document reader, so a
    condition the graph fails is reported by stage and rank here exactly as it is
    under `seals`, and the claim that would have been refused afterwards is never
    weighed.
    """
    source = tmp_path / "plan.json"
    _fold_graph(source, *DIAMOND)
    document = json.loads(source.read_bytes())
    document["zz"] = 1
    malformed = tmp_path / "malformed.json"
    malformed.write_text(json.dumps(document), encoding="utf-8")

    assert (
        main(
            [
                "discharge",
                *_fold_args(malformed, "counting", "one"),
                "--exactness",
                "approximate",
            ]
        )
        == 1
    )
    report = _refusal_report(capsys.readouterr().err)["refusal"]
    assert (report["stage"], report["rank"]) == ("shape", 6)
    assert "unknown fields ['zz']" in report["message"]


def test_fold_offers_no_exactness_flag_to_the_command_that_never_reads_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`fold` runs a declaration, so the claim is spelled only where it is demanded.

    The declaration `fold` builds stays UNDECLARED rather than standing in a
    claim the caller never made, and the flag that would state one is absent
    from the running verb rather than accepted and ignored.
    """
    source = tmp_path / "plan.json"
    _fold_graph(source, *DIAMOND)

    with pytest.raises(SystemExit) as raised:
        main(_fold_args(source, "counting", "one", "--exactness", "distributive"))
    assert raised.value.code == 2
    assert "unrecognized arguments: --exactness" in capsys.readouterr().err

    parsed = cli.build_parser().parse_args(_fold_args(source, "counting", "one"))
    declaration = cli._fold_declaration(tiergraph.loads(source.read_bytes()), parsed)
    assert declaration.exactness is tiergraph.FoldExactness.UNDECLARED


def _structural_path(kind: str, namespace: str, local: str, index: int) -> str:
    """Spell the fixture's simple structural item or boundary path."""
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


FOLD_NAMESPACE = "urn:test:fold"
FOLD_TASKS = QualifiedName(FOLD_NAMESPACE, "tasks")
FOLD_COST = QualifiedName(FOLD_NAMESPACE, "cost")
FOLD_DEPENDS = QualifiedName(FOLD_NAMESPACE, "depends")
FOLD_TIER_DATA = {"local_name": "tasks", "namespace": FOLD_NAMESPACE}
DIAMOND = (("a", "1"), ("b", "2"), ("c", "5"), ("d", "1"))


def _fold_graph(path: Path, *costs: tuple[str, str]) -> tiergraph.Graph:
    """Write the folding guide's diamond, or a prefix of it, as a document."""
    task_type = QualifiedName(FOLD_NAMESPACE, "task")
    items = tuple(
        Item(identifier, (AttributeValue(FOLD_COST, XsdType.DECIMAL, weight),))
        for identifier, weight in costs
    )
    references = tuple(ItemRef(FOLD_TASKS, index) for index in range(len(items)))
    graph = tiergraph.Graph(
        (NamespaceDeclaration("t", FOLD_NAMESPACE),),
        (Tier(TierDeclaration(FOLD_TASKS, "Tasks"), items),),
        (
            SimpleRelationDeclaration(
                QualifiedName(FOLD_NAMESPACE, "membership"), FOLD_TASKS, task_type
            ),
            BipartiteRelationDeclaration(
                FOLD_DEPENDS, task_type, task_type, acyclic=True
            ),
        ),
        tuple(
            RelationInstance(FOLD_DEPENDS, references[left], references[right])
            for left, right in ((0, 1), (0, 2), (1, 3), (2, 3))
            if left < len(items) and right < len(items)
        ),
        (AttributeDeclaration(FOLD_COST, AttributeDomain.ITEM, XsdType.DECIMAL),),
    )
    path.write_bytes(tiergraph.dump_bytes(graph))
    return graph


def _fold_args(path: Path, semiring: str, lift: str, *extra: str) -> list[str]:
    """Spell the shared fold flags the way the CLI reference documents them."""
    return [
        "fold",
        str(path),
        "--attribute-namespace",
        FOLD_NAMESPACE,
        "--attribute-local",
        "cost",
        "--tier",
        FOLD_NAMESPACE,
        "tasks",
        "--semiring",
        semiring,
        "--lift",
        lift,
        "--transition",
        FOLD_NAMESPACE,
        "depends",
        "or",
        *extra,
    ]


def test_fold_reproduces_the_guide_least_cost_and_path_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The two documented folds reach the shell with their guide answers.

    One graph and one dependency relation yield a least-cost path under exact
    min-plus arithmetic and a count of paths under natural-number arithmetic.
    Only the named algebra and the named lift differ.
    """
    source = tmp_path / "plan.json"
    _fold_graph(source, *DIAMOND)

    assert (
        main(
            _fold_args(
                source,
                "decimal-tropical",
                "value",
                "--name",
                "least-cost",
                "--root",
                "/items/durable/a",
                "--ranked",
                "--output-cap",
                "4",
            )
        )
        == 0
    )
    least_cost = json.loads(capsys.readouterr().out)
    assert least_cost["value"] == "4.0"
    assert least_cost["truncated"] is False
    assert least_cost["ranked_witnesses"] == [
        {"path": ["a", "b", "d"], "value": "4.0"},
        {"path": ["a", "c", "d"], "value": "7.0"},
    ]
    assert least_cost["cost"]["carrier_work"] == 19

    assert main(_fold_args(source, "counting", "one", "--name", "path-count")) == 0
    path_count = json.loads(capsys.readouterr().out)
    assert path_count["value"] == 2
    assert path_count["provenance"] is None
    assert "ranked_witnesses" not in path_count


def test_fold_infers_roots_and_keeps_the_shared_file_contract(tmp_path: Path) -> None:
    """An omitted root is inferred, and output refuses to overwrite its input."""
    source = tmp_path / "plan.json"
    original = _fold_graph(source, *DIAMOND)
    target = tmp_path / "fold.json"
    assert main([*_fold_args(source, "counting", "one"), "-o", str(target)]) == 0
    inferred = json.loads(target.read_text(encoding="utf-8"))
    assert inferred["roots"] == [
        {"coordinate": [], "item": {"index": 0, "tier": FOLD_TIER_DATA}}
    ]
    assert main([*_fold_args(source, "counting", "one"), "-o", str(source)]) == 1
    assert tiergraph.loads(source.read_bytes()) == original


@pytest.mark.parametrize(
    ("semiring", "lift", "extra", "fragment"),
    (
        ("counting", "value", (), "nonnegative integer carrier value"),
        ("boolean", "value", (), "must be a Boolean carrier value"),
        ("path", "value", (), "not subscriptable"),
        ("counting", "one", ("--output-cap", "3"), "--output-cap requires --ranked"),
        ("counting", "one", ("--ranked",), "multiply_preserves_witness_order"),
        (
            "decimal-tropical",
            "value",
            ("--ranked", "--output-cap", "0"),
            "output cap 0 must be positive",
        ),
    ),
)
def test_fold_refuses_a_mismatched_carrier_or_flag_combination(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    semiring: str,
    lift: str,
    extra: tuple[str, ...],
    fragment: str,
) -> None:
    """Each carrier and flag mismatch is one exit-one diagnostic, not a traceback."""
    source = tmp_path / "plan.json"
    _fold_graph(source, *DIAMOND)
    assert main(_fold_args(source, semiring, lift, *extra)) == 1
    error = capsys.readouterr().err
    assert error.startswith("tiergraph: fold: ")
    assert fragment in error
    assert "Traceback" not in error


def test_fold_refuses_a_bad_combination_and_a_position_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A transition needs an and/or meaning, and a declared root needs an item."""
    source = tmp_path / "plan.json"
    _fold_graph(source, *DIAMOND)
    arguments = _fold_args(source, "counting", "one")
    arguments[arguments.index("or")] = "xor"
    assert main(arguments) == 1
    assert (
        "transition combination 'xor' must be 'and' or 'or'" in capsys.readouterr().err
    )

    assert (
        main(
            _fold_args(
                source,
                "counting",
                "one",
                "--root",
                _structural_path("positions", FOLD_NAMESPACE, "tasks", 0),
            )
        )
        == 1
    )
    assert "fold root item path" in capsys.readouterr().err


def test_fold_json_shape_does_not_vary_with_cardinality(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every fold array stays an array at one member and at many.

    A zero-length ``states`` or ``roots`` is unreachable: a domain with no
    parentless item is refused before the fold can run, and the last case here
    witnesses that refusal. A zero-length ``ranked_witnesses`` is unreachable
    too, because only the decimal extrema admit ranked output and their zero is
    an infinity that no ``xsd:decimal`` lexical can carry into the graph.
    """
    shapes = []
    for name, costs in (("one", DIAMOND[:1]), ("many", DIAMOND)):
        source = tmp_path / f"{name}.json"
        _fold_graph(source, *costs)
        assert (
            main(
                _fold_args(
                    source, "decimal-tropical", "value", "--ranked", "--output-cap", "4"
                )
            )
            == 0
        )
        shapes.append(json.loads(capsys.readouterr().out))
    single, many = shapes
    for key in ("roots", "states", "ranked_witnesses"):
        assert isinstance(single[key], list), key
        assert isinstance(many[key], list), key
    assert set(single) == set(many)
    assert [len(single[key]) for key in ("roots", "states", "ranked_witnesses")] == [
        1,
        1,
        1,
    ]
    assert [len(many[key]) for key in ("roots", "states", "ranked_witnesses")] == [
        1,
        4,
        2,
    ]

    empty = tmp_path / "empty.json"
    _fold_graph(empty)
    assert main(_fold_args(empty, "counting", "one")) == 1
    assert "dependency graph has no root" in capsys.readouterr().err


def test_semirings_lists_exactly_the_algebras_this_shell_can_name(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The listing is one array whose names are the `--semiring` vocabulary."""
    assert main(["semirings"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert isinstance(report["semirings"], list)
    listed = [entry["name"] for entry in report["semirings"]]
    assert listed == sorted(listed)

    action = next(
        candidate
        for candidate in build_parser()._actions
        if isinstance(candidate, argparse._SubParsersAction)
    )
    semiring_choice = next(
        candidate
        for candidate in action.choices["fold"]._actions
        if candidate.dest == "semiring"
    )
    assert list(semiring_choice.choices or ()) == listed

    counting = next(
        entry for entry in report["semirings"] if entry["name"] == "counting"
    )
    assert counting == {
        "laws": {
            "add_associativity": "exact",
            "add_commutativity": "exact",
            "left_distributivity": "exact",
            "multiply_associativity": "exact",
            "right_distributivity": "exact",
        },
        "name": "counting",
        "one": 1,
        "properties": {
            "add_idempotent": False,
            "add_selective": False,
            "multiply_commutative": True,
            "multiply_preserves_witness_order": False,
            "multiply_strictly_order_preserving": False,
            "no_zero_divisors": True,
            "zero_sum_free": True,
        },
        "star": None,
        "type": "CountingSemiring",
        "zero": 0,
    }
    tropical = next(
        entry for entry in report["semirings"] if entry["name"] == "decimal-tropical"
    )
    assert tropical["star"] == "zero-closed"
    assert tropical["zero"] == "INF"
    assert tropical["properties"]["multiply_preserves_witness_order"] is True


def test_semirings_writes_to_a_file_without_reading_a_document(
    tmp_path: Path,
) -> None:
    """The listing takes no document, so it emits under the shared file contract."""
    target = tmp_path / "semirings.json"
    assert main(["semirings", "-o", str(target)]) == 0
    assert json.loads(target.read_text(encoding="utf-8"))["semirings"]


def test_clock_commands_query_full_declarative_profile(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """All four clock queries emit exact structural and physical JSON data."""
    graph_path = tmp_path / "graph.json"
    profile_path = tmp_path / "clock.json"
    graph_path.write_bytes(tiergraph.dump_bytes(reference_shape()))
    profile_path.write_text(json.dumps(clock_profile_data()), encoding="utf-8")
    common = [str(graph_path), "--profile", str(profile_path)]

    assert main(["clock", "coordinates", *common]) == 0
    expected_coordinates = {
        "clock_tier": {
            "namespace": CLOCK_SEGMENT.namespace,
            "local_name": "clock",
        },
        "coordinates": [
            {"index": 0, "tick": 0, "gap": 0},
            {"index": 1, "tick": 1, "gap": 0},
            {"index": 2, "tick": 1, "gap": 1},
            {"index": 3, "tick": 2, "gap": 0},
        ],
    }
    assert capsys.readouterr().out.encode() == cli._json_bytes(expected_coordinates)

    boundary_path = _structural_path(
        "positions", CLOCK_SEGMENT.namespace, CLOCK_SEGMENT.local_name, 1
    )
    assert main(["clock", "boundary", *common, "--boundary", boundary_path]) == 0
    expected_boundary = {
        "boundary": {"tier": CLOCK_SEGMENT.to_data(), "index": 1},
        "clock_index": 2,
        "refined": {"tick": 1, "gap": 1},
    }
    assert capsys.readouterr().out.encode() == cli._json_bytes(expected_boundary)

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
    expected_extent = {
        "tier": CLOCK_SEGMENT.to_data(),
        "start": {"tick": 1, "gap": 0},
        "end": {"tick": 2, "gap": 0},
    }
    assert capsys.readouterr().out.encode() == cli._json_bytes(expected_extent)

    item_path = _structural_path(
        "items", CLOCK_SEGMENT.namespace, CLOCK_SEGMENT.local_name, 1
    )
    assert main(["clock", "item", *common, "--item", item_path]) == 0
    expected_item = {
        "item": {"tier": CLOCK_SEGMENT.to_data(), "index": 1},
        "structural": {
            "start": {"tick": 1, "gap": 1},
            "end": {"tick": 2, "gap": 0},
        },
        "physical": {"start": "0.1", "duration": "0.04", "unit": "s"},
        "exact_duration": None,
    }
    assert capsys.readouterr().out.encode() == cli._json_bytes(expected_item)


def test_clock_item_without_uniform_rate_does_not_inspect_refusal_message(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing uniform rate is detected without calling legacy duration()."""
    graph_path = tmp_path / "graph.json"
    profile_path = tmp_path / "clock.json"
    graph_path.write_bytes(tiergraph.dump_bytes(reference_shape()))
    profile_path.write_text(json.dumps(clock_profile_data()), encoding="utf-8")
    item = _structural_path(
        "items", CLOCK_SEGMENT.namespace, CLOCK_SEGMENT.local_name, 1
    )

    def reworded_refusal(
        self: tiergraph.ClockProfile, tier: QualifiedName, index: int
    ) -> tuple[int, Any]:
        del self, tier, index
        raise ValueError("a uniform clock rate is unavailable")

    monkeypatch.setattr(tiergraph.ClockProfile, "duration", reworded_refusal)
    assert (
        main(
            [
                "clock",
                "item",
                str(graph_path),
                "--profile",
                str(profile_path),
                "--item",
                item,
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["exact_duration"] is None


def test_clock_commands_report_profile_path_kind_and_untimed_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Clock decoding and query refusals use the ordinary exit-one path."""
    graph_path = tmp_path / "graph.json"
    profile_path = tmp_path / "clock.json"
    graph_path.write_bytes(tiergraph.dump_bytes(reference_shape()))
    profile_path.write_text("{}", encoding="utf-8")
    common = [str(graph_path), "--profile", str(profile_path)]
    assert main(["clock", "coordinates", *common]) == 1
    assert "clock profile fields" in capsys.readouterr().err

    malformed = clock_profile_data()
    malformed["clock_tier"] = {
        "namespace": [CLOCK_TIER.namespace],
        "local_name": CLOCK_TIER.local_name,
    }
    profile_path.write_text(json.dumps(malformed), encoding="utf-8")
    assert main(["clock", "coordinates", *common]) == 1
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
    assert main(["clock", "boundary", *common, "--boundary", item]) == 1
    assert "did not resolve to a boundary" in capsys.readouterr().err

    boundary = _structural_path(
        "positions", CLOCK_SEGMENT.namespace, CLOCK_SEGMENT.local_name, 0
    )
    assert main(["clock", "item", *common, "--item", boundary]) == 1
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

    graph = reference_shape()
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
        reference_shape(), CLOCK_SEGMENT, 0, "0.0000001", "0.0000001"
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
        "{validate,discharge,render,inspect,convert,schema,run,step,walk,path,"
        "grammar,clock,span,select,fold,semirings}" in capsys.readouterr().out
    )
    for command in (
        "validate",
        "discharge",
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
        "fold",
        "semirings",
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
        "discharge",
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
        "fold",
        "semirings",
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
    """Path and relation refusals exit one cleanly, while cap zero is valid."""
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
    )
    for arguments in cases:
        assert main(arguments) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.startswith("tiergraph: walk:")

    assert main(_walk_args(source, "--source", _item_path(0), "--cap", "0")) == 0
    assert json.loads(capsys.readouterr().out) == {
        "nodes": [],
        "truncated": True,
        "cap": 0,
    }

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

    expected_boundary = {
        "kind": "boundary",
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
    assert capsys.readouterr().out.encode() == cli._json_bytes(expected_boundary)

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
                "boundary",
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
                "boundary",
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
                "boundary",
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
    ("flags", "code"),
    [
        (
            ["--kind", "item", "--durable-id", "alpha", "--side", "after"],
            "unknown_form",
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
            "unknown_form",
        ),
        (
            ["--kind", "item"],
            "unknown_form",
        ),
        (
            ["--kind", "boundary", "--durable-id", "alpha"],
            "unknown_form",
        ),
        (
            [
                "--kind",
                "boundary",
                "--tier-namespace",
                "urn:path",
                "--tier-local",
                "tokens",
                "--index",
                "0",
                "--anchor-item-id",
                "alpha",
            ],
            "unknown_form",
        ),
        (
            [
                "--kind",
                "boundary",
                "--tier-namespace",
                "urn:path",
                "--tier-local",
                "tokens",
                "--index",
                "0",
                "--side",
                "after",
            ],
            "unknown_form",
        ),
        (
            ["--kind", "boundary", "--tier-namespace", "urn:path"],
            "invalid_segment",
        ),
        (
            ["--kind", "boundary", "--anchor-item-id", "alpha"],
            "invalid_segment",
        ),
        (
            ["--kind", "boundary", "--side", "after"],
            "unknown_form",
        ),
    ],
)
def test_path_spell_rejects_incoherent_flags(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    flags: list[str],
    code: str,
) -> None:
    source = tmp_path / "graph.json"
    _path_graph(source)
    assert main(["path", "spell", str(source), *flags]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith(f"tiergraph: path: PathRefusal: {code}:")


def test_path_spell_and_resolve_share_typed_malformed_reference_refusal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "graph.json"
    _path_graph(source)
    flags = [
        "--kind",
        "item",
        "--tier-namespace",
        "urn:path",
        "--tier-local",
        "tokens",
        "--index",
        "-1",
    ]
    assert main(["path", "spell", str(source), *flags]) == 1
    spell_error = capsys.readouterr().err
    assert (
        main(["path", "resolve", str(source), "/items/structural/urn:path/tokens/-1"])
        == 1
    )
    resolve_error = capsys.readouterr().err
    for error in (spell_error, resolve_error):
        assert error.startswith("tiergraph: path: PathRefusal: invalid_segment:")


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
    assert main(["schema", "--format-version", "0.2.0", "-o", str(output)]) == 0
    selected = json.loads(output.read_text())
    assert selected["properties"]["format_version"] == {"const": "0.2.0"}

    assert main(["schema", "--hash"]) == 0
    assert capsys.readouterr().out == f"{shape_hash()}\n"


def test_schema_hash_rejects_discarded_format_version(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["schema", "--hash", "--format-version", "not-a-version"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--format-version cannot be used with --hash" in captured.err


def test_schema_help_labels_format_version_as_a_version(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as stopped:
        main(["schema", "--help"])
    assert stopped.value.code == 0
    output = capsys.readouterr().out
    assert "--format-version VERSION" in output
    assert "--format-version N" not in output


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
    # "All outputs" is the parser's own list of them, so a target added to
    # `run --to` is exercised here on the day it is added.
    subcommands = next(
        candidate
        for candidate in build_parser()._actions
        if isinstance(candidate, argparse._SubParsersAction)
    )
    target_choice = next(
        candidate
        for candidate in subcommands.choices["run"]._actions
        if candidate.dest == "to"
    )
    targets = tuple(target_choice.choices or ())
    assert targets == ("json", "json-compact", "bytes", "dot")
    for suffix, content in (
        ("nonewline", b'{"machine_version":"1"}'),
        ("crlf", b'{"machine_version":"1"}\r\n'),
    ):
        source = tmp_path / suffix
        source.write_bytes(content)
        for target in targets:
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


def _spells_a_codec(node: ast.Call) -> bool:
    """Report whether one `.encode` call names a text codec inline."""
    if not node.args and not node.keywords:
        return True
    return (
        len(node.args) == 1
        and not node.keywords
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    )


def test_cli_output_emitters_are_audited() -> None:
    """Every dynamic CLI byte emitter remains behind the shared refusal.

    This enumerates the emitters instead of trusting a hand list: any new
    ``json.dumps`` or text-to-bytes ``.encode`` in the module fails here until
    its author routes it through the refusal and records it below.  The check is
    static, so it discriminates by listing an emitter set no other revision has,
    not by observing a leak; the behavioral discrimination lives in the matrix.

    A byte emitter spells its codec inline, as ``.encode()`` or
    ``.encode("utf-8")``, so only those two shapes count.  A semiring's
    ``encode`` returns strict-JSON data rather than bytes and takes the carrier
    value as its argument, so naming a carrier value is what tells the two
    apart. An emitter that reached its codec through a variable would evade
    this, but no shipped emitter spells one that way.
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
                elif node.func.attr == "encode" and _spells_a_codec(node):
                    found.add((functions[-1], "encode"))
            self.generic_visit(node)

    Calls().visit(tree)
    approved = {
        ("main", "json.dumps"),
        ("_handle_schema", "encode"),
        ("_graph_bytes", "encode"),
        ("_graph_report_bytes", "encode"),
        ("_json_text", "json.dumps"),
        ("_json_bytes", "encode"),
        ("_step_bytes", "json.dumps"),
        ("_step_bytes", "encode"),
    }
    assert found == approved, (
        "route any new CLI emitter through the shared wire refusal; do not casually "
        f"widen the approved emitter set (found {sorted(found)!r})"
    )


def test_cli_binds_no_private_name_from_another_module() -> None:
    """The CLI keeps no module-level handle on another module's private name.

    A module-level alias to a private decoder executes on import, so linting,
    typing, and branch coverage all report it as live even when nothing reads
    it.  This reads the module and names every such binding, so a test seam
    reintroduced through the CLI fails here instead of surviving every gate.
    """
    tree = ast.parse(Path(cli.__file__).read_text(encoding="utf-8"))
    aliases: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if not isinstance(value, ast.Attribute) or not value.attr.startswith("_"):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                aliases.add(f"{target.id} = {ast.unparse(value)}")
    assert aliases == set(), (
        "the CLI is a thin API client and never reaches past it; point a test at "
        "the module that owns the private name instead of aliasing it through "
        f"the CLI (found {sorted(aliases)!r})"
    )


def test_surrogate_reports_are_refused_with_field_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every CLI entry refuses surrogate data by field path, never by traceback.

    Every path here is the reader's, because every one of these inputs is a
    document some reader in this package staged. A graph file names the path in
    the document it read, so every subcommand taking one reports the same
    ``graph.namespaces[...]`` binding however far past decoding its own report
    would have gone. ``step`` and ``run`` build their graph from a JSONL
    program rather than a document, and the program reader names the path
    inside the record it read, scoped by the line that record was on; they used
    to reach a writer instead, because that reader accepted the escape its own
    canonical text cannot spell.

    What every case shares is the part worth pinning: exit 1, this package's
    field-path wording, and no ``UnicodeEncodeError`` reaching a user.
    """
    source = tmp_path / "path-graph.json"
    source.write_text(
        '{"format_version":"0.2.0","graph":{"namespaces":'
        '[{"namespace":"\\ud800","prefix":"p"}],"tiers":[{"declaration":'
        '{"long_name":"T","name":"p:t"},"items":[{"durable_id":"a"}]}]}}'
    )
    surrogate = "\ud800"
    profile = tmp_path / "profile.json"
    profile.write_text("{}")
    selector = tmp_path / "selector.json"
    selector.write_text(json.dumps({"select": "item", "path": "/items/durable/a"}))
    program = tmp_path / "program.jsonl"
    _program(program, DeclareNamespace(NamespaceDeclaration("p", surrogate)))

    walk_graph = tmp_path / "walk-graph.json"
    _walk_graph(walk_graph)
    walk_graph.write_bytes(
        walk_graph.read_bytes().replace(b"urn:test:traversal", b"\\ud800")
    )

    fold_graph = tmp_path / "fold-graph.json"
    _fold_graph(fold_graph, *DIAMOND)
    fold_graph.write_bytes(
        fold_graph.read_bytes().replace(FOLD_NAMESPACE.encode(), b"\\ud800")
    )

    graph = reference_shape()
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

    # The reader names the path in the document it read, so every graph file
    # reports the binding that carries the surrogate rather than wherever that
    # namespace would have surfaced in the subcommand's own report.
    graph_zero = "graph.namespaces[0].namespace"
    graph_one = "graph.namespaces[1].namespace"
    cases = [
        (["inspect", str(source)], graph_zero),
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
            graph_zero,
        ),
        (
            ["path", "resolve", str(source), "/items/durable/a"],
            graph_zero,
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
            graph_zero,
        ),
        (
            ["select", str(source), "--selector", str(selector)],
            graph_zero,
        ),
        *[
            (
                [
                    "fold",
                    str(fold_graph),
                    "--attribute-namespace",
                    surrogate,
                    "--attribute-local",
                    "cost",
                    "--tier",
                    surrogate,
                    "tasks",
                    "--semiring",
                    semiring_name,
                    "--lift",
                    lift_name,
                    "--transition",
                    surrogate,
                    "depends",
                    "or",
                    *extra_flags,
                ],
                graph_zero,
            )
            for semiring_name, lift_name, extra_flags in (
                ("counting", "one", ()),
                ("decimal-tropical", "value", ("--ranked", "--output-cap", "4")),
            )
        ],
        (
            [
                "clock",
                "coordinates",
                str(clock_graph),
                "--profile",
                str(clock_profile),
            ],
            graph_zero,
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
            graph_zero,
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
                graph_one,
            )
            for name in ("text", "json", "jsonl", "html")
        ],
        (["step", str(program)], "JSONL line 2: declaration.namespace"),
        (["render", str(span_graph)], graph_one),
        (["run", str(program), "--to", "dot"], "JSONL line 2: declaration.namespace"),
    ]
    for arguments, field_path in cases:
        assert main(arguments) == 1, arguments
        error = capsys.readouterr().err
        assert f"{field_path} value " in error, (arguments, error)
        assert "unsupported character U+D800" in error, (arguments, error)
        assert "UnicodeEncodeError" not in error, (arguments, error)


def test_invalid_utf8_profile_is_a_staged_encoding_refusal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A side-car document decodes under the stages the graph beside it does.

    This once pinned the ``UnicodeError`` arm of ``main``, because the profile
    reader handed raw bytes to the JSON library and so raised the decoder's own
    exception. Reading the profile as a document answers the condition at
    ``ENCODING`` instead, exactly as the graph does; the remaining live path to
    that arm is an output stream that cannot encode, pinned in
    ``tests/test_cli_envelope.py``.
    """
    graph = tmp_path / "graph.json"
    graph.write_bytes(tiergraph.dump_bytes(reference_shape()))
    profile = tmp_path / "profile.json"
    profile.write_bytes(b'{"clock_tier":"\xff\xfe"}')
    assert main(["clock", "coordinates", str(graph), "--profile", str(profile)]) == 1
    assert "ValueError: parse UTF-8 failed:" in capsys.readouterr().err


def test_validate_and_convert_agree_about_the_escaped_surrogate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`validate` no longer passes a document `convert` cannot canonicalize.

    ``json.dumps`` writes the lone surrogate as the ASCII escape ``\\ud800``, so
    this file is byte-for-byte writable and reaches the reader intact. It used
    to validate at exit 0 and then fail conversion at exit 1, which said the
    document was one this format admits while no writer here could produce it.
    Both commands now refuse it, at the same stage and by the same field path.
    """
    source = tmp_path / "surrogate.json"
    data = tiergraph.to_data(tiergraph.Graph((), (), ()))
    graph_data = data["graph"]
    assert isinstance(graph_data, dict)
    graph_data["namespaces"] = [{"prefix": "p", "namespace": chr(0xD800)}]
    source.write_text(json.dumps(data))
    assert source.read_text().count("\\ud800") == 1

    for arguments in (
        ["validate", str(source)],
        ["convert", str(source), "--to", "json-compact"],
    ):
        assert main(arguments) == 1, arguments
        error = capsys.readouterr().err
        assert "ValueError" in error, arguments
        assert "graph.namespaces[0].namespace value " in error, arguments
        assert "unsupported character U+D800" in error, arguments


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
    """The primitive shapes no neighboring test drives round-trip through the CLI.

    "Remaining" is the complement of a named set within the machine's own
    primitive opcode tuple rather than a list someone kept current: an opcode
    added to the machine is either driven here or declared covered elsewhere,
    and otherwise fails this assertion instead of shipping undriven.
    """
    covered_elsewhere = {DeclareRelation, PromoteItem, PromoteBoundary, Relate}
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
    assert {type(opcode) for opcode in opcodes} == (
        set(machine._PRIMITIVE_OPCODE_TYPES) - covered_elsewhere
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
    boundary = BoundaryRef(tier, 0)
    durable_item = tiergraph.DurableItemRef("item-id")
    durable_boundary = tiergraph.DurableBoundaryRef(durable_item, BoundarySide.AFTER)
    tier_boundary = tiergraph.DurableBoundaryRef(tier, BoundarySide.BEFORE)
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
        PromoteBoundary(boundary, "new-position"),
        Relate(RelationInstance(relation, item, durable_boundary, "link", (value,))),
        AttachValue(AttributeDomain.TIER, tier, value),
        AttachValue(AttributeDomain.ITEM, item, value),
        AttachValue(AttributeDomain.ITEM, durable_item, value),
        AttachValue(AttributeDomain.BOUNDARY, boundary, value),
        AttachValue(AttributeDomain.BOUNDARY, durable_boundary, value),
        AttachValue(AttributeDomain.BOUNDARY, tier_boundary, value),
        AttachValue(AttributeDomain.RELATION_INSTANCE, 0, value),
        Repeat(1, (AddItem(tier),)),
    ]
    for opcode in opcodes:
        assert machine._decode_opcode(opcode.to_data(), "record") == opcode


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
        machine._decode_opcode(value, "record")


def test_reference_and_collection_shape_errors() -> None:
    with pytest.raises(ValueError, match="must be an array"):
        machine._decode_attributes({}, "attributes")
    with pytest.raises(ValueError, match="endpoint object"):
        machine._decode_endpoint(None, "endpoint")
    with pytest.raises(ValueError, match="unknown reference shape"):
        machine._decode_endpoint({}, "endpoint")
    with pytest.raises(ValueError, match="unknown shape"):
        machine._decode_endpoint({"anchor": {}, "side": "before"}, "endpoint")
    with pytest.raises(ValueError, match="endpoint_kinds and tiers"):
        machine._decode_side(
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
        machine._decode_relation_instance(polyadic, "relation")
    with pytest.raises(ValueError, match="must contain at most one"):
        data = PolyadicRelationDeclaration(
            QualifiedName("urn:x", "r"),
            RelationSideDeclaration((RelationEndpointKind.ITEM,)),
            RelationSideDeclaration((RelationEndpointKind.ITEM,)),
        ).to_data()
        data["targets_subset_of"] = [{}, {}]
        machine._decode_relation_declaration(data, "declaration")
    with pytest.raises(ValueError, match="kind .* is unknown"):
        machine._decode_relation_declaration({"kind": "bad"}, "declaration")
    with pytest.raises(ValueError, match="must be an object"):
        machine._decode_relation_declaration(None, "declaration")


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
    # The stub names `object_pairs_hook` rather than absorbing it into
    # `**kwargs` so that it stops accepting the call if the reader ever stops
    # passing the duplicate-key hook.  A stub permissive about its keywords
    # would hold this test green while the hook silently disappeared, which is
    # the failure a substitute for a real function is most able to hide.
    monkeypatch.setattr(
        "tiergraph.machine_codec.json.loads",
        lambda line, object_pairs_hook: (_ for _ in ()).throw(
            RecursionError("parser recursion")
        ),
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
        machine._decode_opcode({}, "line 2", tiergraph.MAX_JSON_DEPTH + 1)
