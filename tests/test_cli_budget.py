"""Opt-in deterministic work budgets on command and request surfaces."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

import tiergraph
from tests.test_budget import NS, TIER, graph
from tiergraph.budget import _MAX_USER_STEPS
from tiergraph.cli import build_parser, main
from tiergraph.match import (
    AtomPattern,
    TierOrder,
    _evaluate_match_request,
    _match_request_loads,
    _PairsRequest,
    ordering_to_data,
    pattern_to_data,
)
from tiergraph.predicate import And


def _request(operation: str = "exists") -> dict[str, object]:
    return {
        "match": operation,
        "ordering": ordering_to_data(TierOrder(TIER)),
        "pattern": pattern_to_data(AtomPattern(And(()))),
    }


def _subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    action = next(
        item for item in parser._actions if isinstance(item, argparse._SubParsersAction)
    )
    return action.choices


def _has_max_steps(parser: argparse.ArgumentParser) -> bool:
    return any(action.dest == "max_steps" for action in parser._actions)


def test_flag_is_only_on_commands_that_reach_budgeted_evaluation() -> None:
    """Parser coverage pins every command and the topology-only exception."""
    commands = _subcommands(build_parser())
    assert {name for name, parser in commands.items() if _has_max_steps(parser)} == {
        "select",
        "match",
        "fold",
    }
    discharge = _subcommands(commands["discharge"])
    assert {name for name, parser in discharge.items() if _has_max_steps(parser)} == {
        "fold"
    }
    grammar = _subcommands(commands["grammar"])
    assert {name for name, parser in grammar.items() if _has_max_steps(parser)} == {
        "recognize",
        "count",
        "best",
        "generate",
    }
    assert not _has_max_steps(grammar["lattice"])


@pytest.mark.parametrize("value", ("0", "-1", "1.5", "true", str(10**30)))
def test_cli_max_steps_is_a_typed_usage_refusal(
    value: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Zero, negatives, non-integers, bool spellings, and huge values exit 2."""
    with pytest.raises(SystemExit) as stopped:
        main(["match", "missing.json", "exists", "--max-steps", value])
    assert stopped.value.code == 2
    diagnostic = capsys.readouterr().err
    assert "argument --max-steps:" in diagnostic
    assert (
        "must be a positive integer" in diagnostic
        or f"must be no greater than {_MAX_USER_STEPS}" in diagnostic
    )


@pytest.mark.parametrize("value", (0, -1, 1.5, True, 10**30))
def test_request_max_steps_is_a_staged_value_refusal(value: object) -> None:
    """The strict JSON reader rejects every non-positive/bounded integer shape."""
    request = _request()
    request["max_steps"] = value
    with pytest.raises(tiergraph.Refusal) as stopped:
        _match_request_loads(json.dumps(request))
    assert stopped.value.stage is tiergraph.RefusalStage.VALUE
    assert "$.max_steps must be" in str(stopped.value)


@pytest.mark.parametrize("operation", ("exists", "focus", "spans", "count"))
def test_every_pattern_request_kind_accepts_max_steps(operation: str) -> None:
    """The optional request field is common to every regular-pattern decision."""
    request = _request(operation)
    request["max_steps"] = _MAX_USER_STEPS
    decoded = _match_request_loads(json.dumps(request))
    assert decoded.max_steps == _MAX_USER_STEPS


def test_absent_flag_and_request_field_preserve_exact_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Omission is the old unbudgeted path, byte-equal to an ample opt-in guard."""
    document = graph(2)
    source = tmp_path / "graph.json"
    source.write_bytes(tiergraph.dump_bytes(document))
    ordering = json.dumps(ordering_to_data(TierOrder(TIER)))
    base = [
        "match",
        str(source),
        "exists",
        "--pattern",
        ".",
        "--ordering",
        ordering,
    ]
    assert main(base) == 0
    absent = capsys.readouterr()
    assert absent.err == ""
    assert absent.out == '{\n  "exists": true\n}\n'

    assert main([*base, "--max-steps", "1000"]) == 0
    guarded = capsys.readouterr()
    assert guarded == absent

    request = _request()
    assert _evaluate_match_request(document, json.dumps(request)) == {"exists": True}
    request["max_steps"] = 1000
    assert _evaluate_match_request(document, json.dumps(request)) == {"exists": True}


def test_budget_exhaustion_is_a_structured_semantics_refusal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Aggregate exhaustion exits 1 with both the readable and typed report."""
    source = tmp_path / "graph.json"
    source.write_bytes(tiergraph.dump_bytes(graph(2)))
    assert (
        main(
            [
                "match",
                str(source),
                "exists",
                "--pattern",
                ".",
                "--ordering",
                json.dumps(ordering_to_data(TierOrder(TIER))),
                "--max-steps",
                "1",
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    line, report_text = captured.err.split("\n", 1)
    assert line.startswith("tiergraph: match: BudgetExhausted: pattern.exists")
    report = json.loads(report_text)
    assert report["refusal"]["stage"] == "semantics"
    assert report["refusal"]["rank"] == int(tiergraph.RefusalStage.SEMANTICS)
    assert "exhausted its work budget" in report["refusal"]["message"]


def test_spans_reports_a_nonempty_budget_cut(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A completed spans prefix is visibly partial rather than reported complete."""
    source = tmp_path / "graph.json"
    source.write_bytes(tiergraph.dump_bytes(graph(4)))
    assert (
        main(
            [
                "match",
                str(source),
                "spans",
                "--pattern",
                ".",
                "--ordering",
                json.dumps(ordering_to_data(TierOrder(TIER))),
                "--max-steps",
                "15",
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["extent"] == "cut-at-budget"
    assert report["matches"]


def test_request_field_and_flag_have_parity_and_cannot_compete() -> None:
    """Either spelling installs the same guard; two declarations are refused."""
    document = graph(2)
    request = _request()
    with pytest.raises(tiergraph.BudgetExhausted) as from_flag:
        _evaluate_match_request(document, json.dumps(request), max_steps=1)
    request["max_steps"] = 1
    with pytest.raises(tiergraph.BudgetExhausted) as from_json:
        _evaluate_match_request(document, json.dumps(request))
    assert str(from_json.value) == str(from_flag.value)

    with pytest.raises(tiergraph.Refusal, match="may not both be set") as stopped:
        _evaluate_match_request(document, json.dumps(request), max_steps=1)
    assert stopped.value.stage is tiergraph.RefusalStage.VALUE


def test_pairs_request_round_trips_max_steps() -> None:
    """The interval-join request retains its optional guard in strict JSON data."""
    request = {
        "match": "pairs",
        "left": {"select": "items", "tier": TIER.to_data()},
        "right": {"select": "items", "tier": TIER.to_data()},
        "relation": "equal",
        "offsets": {
            "origin": {"namespace": NS, "local_name": "origin"},
            "end": {"namespace": NS, "local_name": "end"},
        },
        "max_steps": 7,
    }
    decoded = _match_request_loads(json.dumps(request))
    assert isinstance(decoded, _PairsRequest)
    assert decoded.max_steps == 7
    assert decoded.to_data() == request


def test_lattice_request_accepts_max_steps() -> None:
    """The lattice request shares the same optional field as pattern and pairs."""
    request = {
        "match": "lattice",
        "transitions": [
            {"namespace": NS, "local_name": "next"},
        ],
        "roots": [tiergraph.ItemRef(TIER, 0).to_data()],
        "emission": {"namespace": NS, "local_name": "token"},
        "pattern": pattern_to_data(AtomPattern(And(()))),
        "policy": {"policy": "unambiguous"},
        "max_steps": 7,
    }
    decoded = _match_request_loads(json.dumps(request))
    assert decoded.max_steps == 7
