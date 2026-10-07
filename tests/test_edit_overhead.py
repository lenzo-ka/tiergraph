"""Keep plain editing free of recording state and cover its benchmark gate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from scripts import benchmark_edit_overhead as benchmark

from tiergraph import AttributeValue, Item, ItemRef, RelationInstance, XsdType

PLAIN_EDITOR_STATE = {
    "_source",
    "_displacement",
    "_namespaces",
    "_tiers",
    "_relation_declarations",
    "_relations",
    "_attribute_declarations",
    "_boundary_values",
    "_attributes",
    "_polyadic_relations",
    "_seals",
    "_layers",
}


def test_plain_edits_retain_no_recording_state() -> None:
    """REGRESSION: opt-in recording cannot add state to the plain carrier.

    The exact state shape is the deterministic overhead gate. A later recording
    feature must use an opt-in carrier or wrapper; adding a journal, records, or
    callbacks to every plain editor fails here even when those objects are empty.
    """
    fixture = benchmark.fixtures(20)[1]
    primary_count = len(fixture.graph._tiers_by_name[fixture.primary].items)
    secondary_count = len(fixture.graph._tiers_by_name[fixture.secondary].items)
    operations = (
        lambda editor: None,
        lambda editor: editor.insert_item(fixture.primary, 1, Item("new")),
        lambda editor: editor.move_item(ItemRef(fixture.primary, 0), 1),
        lambda editor: editor.remove_item(ItemRef(fixture.primary, primary_count - 1)),
        lambda editor: editor.set_attribute(
            ItemRef(fixture.primary, 0),
            AttributeValue(benchmark._name("label"), XsdType.STRING, "new"),
        ),
        lambda editor: editor.add_relation(
            RelationInstance(
                fixture.relation,
                ItemRef(fixture.primary, primary_count - 1),
                ItemRef(fixture.secondary, secondary_count - 1),
            )
        ),
        lambda editor: editor.remove_relation(0),
    )
    for operation in operations:
        editor = fixture.graph.edit()
        assert set(vars(editor)) == PLAIN_EDITOR_STATE
        operation(editor)
        editor.freeze()
        assert set(vars(editor)) == PLAIN_EDITOR_STATE


@pytest.mark.parametrize("fixture", benchmark.fixtures(20), ids=lambda item: item.name)
@pytest.mark.parametrize("operation", benchmark.OPERATIONS)
def test_every_benchmark_transaction_returns_a_valid_graph(
    fixture: benchmark.Fixture, operation: str
) -> None:
    """Both benchmark shapes support every complete measured transaction."""
    result = benchmark.execute(fixture, operation)
    assert result is not fixture.graph


def _worker(version: str, value: int = 1_000_000) -> benchmark.WorkerResult:
    """Return a small synthetic worker result for orchestration tests."""
    return {
        "python": "3.12.3",
        "version": version,
        "module": f"/{version}/tiergraph/__init__.py",
        "samples": {"flat:move": [value, value * 2]},
    }


def test_collect_rotates_operations_and_restores_gc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The worker emits one positive timing for every requested pair."""
    clock = iter(range(1_000_000))
    monkeypatch.setattr(
        "scripts.benchmark_edit_overhead.time.perf_counter_ns", lambda: next(clock)
    )
    result = benchmark.collect(20, 2, 1)
    assert set(result["samples"]) == {
        f"{fixture}:{operation}"
        for fixture in ("flat", "linked")
        for operation in benchmark.OPERATIONS
    }
    assert all(values == [1, 1] for values in result["samples"].values())


def test_collect_leaves_disabled_gc_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """A caller's disabled garbage collector remains disabled after measuring."""
    monkeypatch.setattr("scripts.benchmark_edit_overhead.gc.isenabled", lambda: False)
    enabled = False

    def reject_enable() -> None:
        nonlocal enabled
        enabled = True

    monkeypatch.setattr("scripts.benchmark_edit_overhead.gc.enable", reject_enable)
    monkeypatch.setattr(
        "scripts.benchmark_edit_overhead.time.perf_counter_ns", lambda: 1
    )
    benchmark.collect(20, 1, 0)
    assert not enabled


def test_percentile_and_summary_pair_samples() -> None:
    """Nearest-rank tails and paired ratios are computed from raw samples."""
    baseline = _worker(benchmark.RELEASE)
    candidate = _worker(benchmark.RELEASE, 2_000_000)
    rows = benchmark.summarize(baseline, candidate, 10.0)
    assert benchmark.percentile([1.0, 2.0, 3.0], 0.5) == 2.0
    assert rows == (
        benchmark.Summary("flat", "move", 1.5, 2.0, 3.0, 4.0, 2.0, 2.0, 6.0),
    )


def test_summary_refuses_unpaired_samples() -> None:
    """A ratio is never reported across sample sequences of different lengths."""
    baseline = _worker(benchmark.RELEASE)
    candidate = _worker(benchmark.RELEASE)
    candidate["samples"]["flat:move"] = [1]
    with pytest.raises(ValueError, match="unpaired sample count"):
        benchmark.summarize(baseline, candidate, benchmark.FRAME_MS)


def test_render_names_passing_and_failing_gates() -> None:
    """The human report makes both independent decisions explicit."""
    baseline = _worker(benchmark.RELEASE)
    candidate = _worker(benchmark.RELEASE)
    passing = benchmark.Summary("flat", "move", 1, 1, 1, 1, 1, 1, 15)
    failing = benchmark.Summary("flat", "move", 1, 1, 2, 20, 2, 2, -3)
    assert "ratio gate (1.100x): PASS" in benchmark.render(
        baseline,
        candidate,
        (passing,),
        items=1000,
        samples=2,
        frame_ms=benchmark.FRAME_MS,
        max_ratio=1.1,
    )
    report = benchmark.render(
        baseline,
        candidate,
        (failing,),
        items=1000,
        samples=2,
        frame_ms=benchmark.FRAME_MS,
        max_ratio=1.1,
    )
    assert "ratio gate (1.100x): FAIL" in report
    assert "frame gate (16.667 ms): FAIL" in report


def test_environment_selects_exactly_one_import_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wheel cannot inherit the checkout path and vice versa."""
    monkeypatch.setenv("PYTHONPATH", "/wrong")
    assert "PYTHONPATH" not in benchmark._environment(checkout=False)
    assert benchmark._environment(checkout=True)["PYTHONPATH"] == str(
        benchmark.ROOT / "src"
    )


def test_run_worker_decodes_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """The parent asks for worker mode and decodes its raw result."""
    expected = _worker(benchmark.RELEASE)
    seen: dict[str, Any] = {}

    def run(command: list[str], **options: object) -> SimpleNamespace:
        seen["command"] = command
        seen.update(options)
        return SimpleNamespace(stdout=json.dumps(expected))

    monkeypatch.setattr("scripts.benchmark_edit_overhead.subprocess.run", run)
    assert (
        benchmark.run_worker(
            Path("python"), items=1000, samples=3, warmups=1, checkout=False
        )
        == expected
    )
    assert "--worker" in seen["command"]
    assert seen["check"] is True


def test_install_release_requires_the_binary_wheel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Baseline setup pins the release and refuses a source distribution."""
    created: list[Path] = []
    commands: list[list[str]] = []
    monkeypatch.setattr(
        "scripts.benchmark_edit_overhead.venv.EnvBuilder",
        lambda **_options: SimpleNamespace(create=lambda path: created.append(path)),
    )
    monkeypatch.setattr(
        "scripts.benchmark_edit_overhead.subprocess.run",
        lambda command, **_options: commands.append(command),
    )
    python = benchmark.install_release(tmp_path / "baseline")
    assert created == [tmp_path / "baseline"]
    assert python == tmp_path / "baseline" / "bin" / "python"
    assert commands[0][:4] == [
        sys.executable,
        "-m",
        "pip",
        "--python",
    ]
    assert str(python) in commands[0]
    assert "--only-binary=:all:" in commands[0]
    assert f"tiergraph=={benchmark.RELEASE}" in commands[0]


def test_positive_argument_type() -> None:
    """Sample and item counts accept positive integers only."""
    assert benchmark._positive("2") == 2
    with pytest.raises(argparse.ArgumentTypeError, match="positive"):
        benchmark._positive("0")


def test_execute_refuses_an_unknown_operation() -> None:
    """A misspelled operation cannot turn into an edit-and-freeze measurement."""
    with pytest.raises(ValueError, match="unknown operation"):
        benchmark.execute(benchmark.fixtures(20)[0], "unknown")


def test_compare_checks_release_and_combines_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The parent refuses the wrong baseline and reports one combined result."""
    results = iter((_worker("0.7.0"), _worker(benchmark.RELEASE)))
    monkeypatch.setattr(
        benchmark, "run_worker", lambda *_args, **_kwargs: next(results)
    )
    with pytest.raises(ValueError, match="not 0.8.0"):
        benchmark.compare(
            Path("python"),
            items=1000,
            samples=2,
            warmups=0,
            frame_ms=10,
            max_ratio=1.1,
        )

    results = iter((_worker(benchmark.RELEASE), _worker(benchmark.RELEASE)))
    monkeypatch.setattr(
        benchmark, "run_worker", lambda *_args, **_kwargs: next(results)
    )
    _, _, rows, passed = benchmark.compare(
        Path("python"),
        items=1000,
        samples=2,
        warmups=0,
        frame_ms=10,
        max_ratio=1.1,
    )
    assert rows[0].ratio_p95 == 1.0
    assert passed


def test_main_runs_worker_and_both_parent_output_modes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Worker JSON, human comparison, and machine comparison are reachable."""
    worker = _worker(benchmark.RELEASE)
    monkeypatch.setattr(benchmark, "collect", lambda *_args: worker)
    assert benchmark.main(["--worker", "--items", "2", "--samples", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["version"] == benchmark.RELEASE

    row = benchmark.Summary("flat", "move", 1, 1, 1, 1, 1, 1, 15)
    comparison = (_worker(benchmark.RELEASE), _worker(benchmark.RELEASE), (row,), True)
    monkeypatch.setattr(benchmark, "compare", lambda *_args, **_kwargs: comparison)
    assert benchmark.main(["--baseline-python", "python"]) == 0
    assert "ratio gate" in capsys.readouterr().out

    assert benchmark.main(["--baseline-python", "python", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["passed"] is True


def test_main_installs_default_baseline_and_returns_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without an override the temporary wheel environment is used and cleaned."""
    installed: list[Path] = []

    def install(path: Path) -> Path:
        installed.append(path)
        return path / "bin" / "python"

    monkeypatch.setattr(benchmark, "install_release", install)
    baseline = _worker(benchmark.RELEASE)
    candidate = _worker(benchmark.RELEASE)
    row = benchmark.Summary("flat", "move", 1, 1, 2, 20, 2, 2, -3)
    monkeypatch.setattr(
        benchmark,
        "compare",
        lambda *_args, **_kwargs: (baseline, candidate, (row,), False),
    )
    assert benchmark.main([]) == 1
    assert len(installed) == 1
