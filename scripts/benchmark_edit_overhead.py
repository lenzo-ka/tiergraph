"""Compare complete plain-editor transactions with the released wheel."""

from __future__ import annotations

import argparse
import gc
import json
import math
import os
import statistics
import subprocess
import sys
import tempfile
import time
import venv
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TypedDict, cast

import tiergraph
from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    Graph,
    Item,
    ItemRef,
    NamespaceDeclaration,
    QualifiedName,
    RelationInstance,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    XsdType,
)

ROOT = Path(__file__).resolve().parent.parent
RELEASE = "0.8.0"
FRAME_MS = 1000.0 / 60.0
NS = "urn:tiergraph:benchmark:plain-edit"
PREFIX = "bench"
OPERATIONS = (
    "edit+freeze",
    "insert",
    "move",
    "remove",
    "set_attribute",
    "relate",
    "unrelate",
)


class WorkerResult(TypedDict):
    """Describe one interpreter and its raw nanosecond samples."""

    python: str
    version: str
    module: str
    samples: dict[str, list[int]]


@dataclass(frozen=True, slots=True)
class Fixture:
    """Hold one graph shape and the coordinates needed by every operation."""

    name: str
    graph: Graph
    primary: QualifiedName
    secondary: QualifiedName
    relation: QualifiedName


@dataclass(frozen=True, slots=True)
class Summary:
    """Hold paired timing statistics for one fixture and operation."""

    fixture: str
    operation: str
    baseline_p50_ms: float
    baseline_p95_ms: float
    candidate_p50_ms: float
    candidate_p95_ms: float
    ratio_p50: float
    ratio_p95: float
    headroom_ms: float


def _name(local: str) -> QualifiedName:
    """Return one expanded benchmark name."""
    return QualifiedName(NS, local)


def fixtures(items: int) -> tuple[Fixture, Fixture]:
    """Build flat and linked graphs carrying approximately ``items`` items."""
    flat_tier = _name("flat")
    flat_type = _name("Flat")
    flat_members = _name("flat-members")
    flat_link = _name("flat-link")
    label = _name("label")
    flat = Graph(
        (NamespaceDeclaration(PREFIX, NS),),
        (
            Tier(
                TierDeclaration(flat_tier, "Flat items"),
                tuple(Item(f"flat-{index}") for index in range(items)),
            ),
        ),
        (
            SimpleRelationDeclaration(flat_members, flat_tier, flat_type),
            BipartiteRelationDeclaration(flat_link, flat_type, flat_type),
        ),
        (RelationInstance(flat_link, ItemRef(flat_tier, 0), ItemRef(flat_tier, 1)),),
        (AttributeDeclaration(label, AttributeDomain.ITEM, XsdType.STRING),),
    )

    left_count = items // 2
    right_count = items - left_count
    left = _name("left")
    right = _name("right")
    left_type = _name("Left")
    right_type = _name("Right")
    linked_relation = _name("linked")
    linked = Graph(
        (NamespaceDeclaration(PREFIX, NS),),
        (
            Tier(
                TierDeclaration(left, "Left items"),
                tuple(Item(f"left-{index}") for index in range(left_count)),
            ),
            Tier(
                TierDeclaration(right, "Right items"),
                tuple(Item(f"right-{index}") for index in range(right_count)),
            ),
        ),
        (
            SimpleRelationDeclaration(_name("left-members"), left, left_type),
            SimpleRelationDeclaration(_name("right-members"), right, right_type),
            BipartiteRelationDeclaration(linked_relation, left_type, right_type),
        ),
        tuple(
            RelationInstance(
                linked_relation, ItemRef(left, index), ItemRef(right, index)
            )
            for index in range(min(left_count, right_count) - 1)
        ),
        (AttributeDeclaration(label, AttributeDomain.ITEM, XsdType.STRING),),
    )
    return (
        Fixture("flat", flat, flat_tier, flat_tier, flat_link),
        Fixture("linked", linked, left, right, linked_relation),
    )


def execute(fixture: Fixture, operation: str) -> Graph:
    """Run one complete editor transaction for a named operation."""
    graph = fixture.graph
    primary_count = len(graph._tiers_by_name[fixture.primary].items)
    secondary_count = len(graph._tiers_by_name[fixture.secondary].items)
    editor = graph.edit()
    if operation == "insert":
        editor.insert_item(fixture.primary, primary_count // 2, Item("inserted"))
    elif operation == "move":
        editor.move_item(ItemRef(fixture.primary, 0), primary_count - 1)
    elif operation == "remove":
        editor.remove_item(ItemRef(fixture.primary, primary_count - 1))
    elif operation == "set_attribute":
        editor.set_attribute(
            ItemRef(fixture.primary, primary_count // 2),
            AttributeValue(_name("label"), XsdType.STRING, "changed"),
        )
    elif operation == "relate":
        editor.add_relation(
            RelationInstance(
                fixture.relation,
                ItemRef(fixture.primary, primary_count - 1),
                ItemRef(fixture.secondary, secondary_count - 1),
            )
        )
    elif operation == "unrelate":
        editor.remove_relation(0)
    elif operation != "edit+freeze":
        raise ValueError(f"unknown operation {operation!r}")
    return editor.freeze()


def collect(items: int, samples: int, warmups: int) -> WorkerResult:
    """Measure every transaction after untimed warmups in deterministic order."""
    cases = fixtures(items)
    for fixture in cases:
        for operation in OPERATIONS:
            for _ in range(warmups):
                execute(fixture, operation)

    measured: dict[str, list[int]] = {
        f"{fixture.name}:{operation}": []
        for fixture in cases
        for operation in OPERATIONS
    }
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        pairs = tuple(
            (fixture, operation) for fixture in cases for operation in OPERATIONS
        )
        for sample in range(samples):
            offset = sample % len(pairs)
            for fixture, operation in (*pairs[offset:], *pairs[:offset]):
                start = time.perf_counter_ns()
                result = execute(fixture, operation)
                elapsed = time.perf_counter_ns() - start
                measured[f"{fixture.name}:{operation}"].append(elapsed)
                del result
    finally:
        if was_enabled:
            gc.enable()
    return {
        "python": sys.version.split()[0],
        "version": tiergraph.__version__,
        "module": str(Path(tiergraph.__file__).resolve()),
        "samples": measured,
    }


def percentile(values: Sequence[float], fraction: float) -> float:
    """Return the nearest-rank percentile of a nonempty sample."""
    ordered = sorted(values)
    return ordered[math.ceil(fraction * len(ordered)) - 1]


def summarize(
    baseline: WorkerResult, candidate: WorkerResult, frame_ms: float
) -> tuple[Summary, ...]:
    """Pair raw samples and compute p50/p95 timings and candidate ratios."""
    rows: list[Summary] = []
    for key in sorted(baseline["samples"]):
        baseline_ns = baseline["samples"][key]
        candidate_ns = candidate["samples"][key]
        if len(baseline_ns) != len(candidate_ns):
            raise ValueError(f"unpaired sample count for {key}")
        ratios = [
            candidate_value / baseline_value
            for baseline_value, candidate_value in zip(
                baseline_ns, candidate_ns, strict=True
            )
        ]
        fixture, operation = key.split(":", 1)
        baseline_ms = [value / 1_000_000 for value in baseline_ns]
        candidate_ms = [value / 1_000_000 for value in candidate_ns]
        candidate_p95 = percentile(candidate_ms, 0.95)
        rows.append(
            Summary(
                fixture,
                operation,
                statistics.median(baseline_ms),
                percentile(baseline_ms, 0.95),
                statistics.median(candidate_ms),
                candidate_p95,
                statistics.median(ratios),
                percentile(ratios, 0.95),
                frame_ms - candidate_p95,
            )
        )
    return tuple(rows)


def render(
    baseline: WorkerResult,
    candidate: WorkerResult,
    rows: Sequence[Summary],
    *,
    items: int,
    samples: int,
    frame_ms: float,
    max_ratio: float,
) -> str:
    """Render a reviewable table and the two timing-gate decisions."""
    lines = [
        f"baseline: tiergraph {baseline['version']} wheel at {baseline['module']}",
        f"candidate: tiergraph {candidate['version']} checkout at {candidate['module']}",
        f"{items} items; {samples} paired samples; full edit + operation + freeze",
        "fixture operation       base p50  base p95  cand p50  cand p95  "
        "ratio p50  ratio p95  frame headroom",
    ]
    lines.extend(
        (
            f"{row.fixture:7} {row.operation:15} "
            f"{row.baseline_p50_ms:8.3f}  {row.baseline_p95_ms:8.3f}  "
            f"{row.candidate_p50_ms:8.3f}  {row.candidate_p95_ms:8.3f}  "
            f"{row.ratio_p50:9.3f}  {row.ratio_p95:9.3f}  "
            f"{row.headroom_ms:8.3f} ms"
        )
        for row in rows
    )
    ratio_ok = all(
        row.ratio_p50 <= max_ratio and row.ratio_p95 <= max_ratio for row in rows
    )
    frame_ok = all(row.headroom_ms > 0 for row in rows)
    lines.append(
        f"ratio gate ({max_ratio:.3f}x): {'PASS' if ratio_ok else 'FAIL'}; "
        f"{1000 / frame_ms:.0f} Hz frame gate ({frame_ms:.3f} ms): "
        f"{'PASS' if frame_ok else 'FAIL'}"
    )
    return "\n".join(lines)


def _environment(*, checkout: bool) -> dict[str, str]:
    """Return an isolated import environment for one measured interpreter."""
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    if checkout:
        environment["PYTHONPATH"] = str(ROOT / "src")
    return environment


def run_worker(
    python: Path, *, items: int, samples: int, warmups: int, checkout: bool
) -> WorkerResult:
    """Run the measurement worker under one interpreter and decode its result."""
    completed = subprocess.run(
        [
            str(python),
            str(Path(__file__).resolve()),
            "--worker",
            "--items",
            str(items),
            "--samples",
            str(samples),
            "--warmups",
            str(warmups),
        ],
        cwd=ROOT,
        env=_environment(checkout=checkout),
        check=True,
        capture_output=True,
        text=True,
    )
    return cast(WorkerResult, json.loads(completed.stdout))


def install_release(directory: Path) -> Path:
    """Install the released binary wheel into a new disposable virtualenv."""
    # A copied macOS framework executable looks for libpython relative to the
    # disposable directory; the base interpreter owns that library. A symlink
    # keeps the ordinary venv prefix while resolving the executable correctly.
    venv.EnvBuilder(with_pip=False, symlinks=os.name != "nt").create(directory)
    python = directory / "bin" / "python"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "--python",
            str(python),
            "install",
            "--quiet",
            "--only-binary=:all:",
            f"tiergraph=={RELEASE}",
        ],
        check=True,
        env=_environment(checkout=False),
    )
    return python


def _positive(value: str) -> int:
    """Parse a positive command-line integer."""
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def parser() -> argparse.ArgumentParser:
    """Build the command-line parser for the benchmark and its worker."""
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--items", type=_positive, default=1000)
    result.add_argument("--samples", type=_positive, default=31)
    result.add_argument("--warmups", type=int, default=2)
    result.add_argument("--max-ratio", type=float, default=1.10)
    result.add_argument("--frame-ms", type=float, default=FRAME_MS)
    result.add_argument("--baseline-python", type=Path)
    result.add_argument("--json", action="store_true")
    result.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return result


def compare(
    baseline_python: Path,
    *,
    items: int,
    samples: int,
    warmups: int,
    frame_ms: float,
    max_ratio: float,
) -> tuple[WorkerResult, WorkerResult, tuple[Summary, ...], bool]:
    """Run both interpreters and return results plus the combined gate decision."""
    baseline = run_worker(
        baseline_python,
        items=items,
        samples=samples,
        warmups=warmups,
        checkout=False,
    )
    if baseline["version"] != RELEASE:
        raise ValueError(
            f"baseline interpreter has tiergraph {baseline['version']}, not {RELEASE}"
        )
    candidate = run_worker(
        Path(sys.executable),
        items=items,
        samples=samples,
        warmups=warmups,
        checkout=True,
    )
    rows = summarize(baseline, candidate, frame_ms)
    passed = all(
        row.ratio_p50 <= max_ratio
        and row.ratio_p95 <= max_ratio
        and row.headroom_ms > 0
        for row in rows
    )
    return baseline, candidate, rows, passed


def main(argv: Sequence[str] | None = None) -> int:
    """Run a worker or compare the checkout with the released wheel."""
    arguments = parser().parse_args(argv)
    if arguments.worker:
        print(
            json.dumps(collect(arguments.items, arguments.samples, arguments.warmups))
        )
        return 0

    def finish(baseline_python: Path) -> int:
        baseline, candidate, rows, passed = compare(
            baseline_python,
            items=arguments.items,
            samples=arguments.samples,
            warmups=arguments.warmups,
            frame_ms=arguments.frame_ms,
            max_ratio=arguments.max_ratio,
        )
        if arguments.json:
            print(
                json.dumps(
                    {
                        "baseline": baseline,
                        "candidate": candidate,
                        "rows": [asdict(row) for row in rows],
                        "passed": passed,
                    },
                    sort_keys=True,
                )
            )
        else:
            print(
                render(
                    baseline,
                    candidate,
                    rows,
                    items=arguments.items,
                    samples=arguments.samples,
                    frame_ms=arguments.frame_ms,
                    max_ratio=arguments.max_ratio,
                )
            )
        return 0 if passed else 1

    if arguments.baseline_python is not None:
        return finish(arguments.baseline_python)
    with tempfile.TemporaryDirectory(prefix="tiergraph-0.8.0-") as temporary:
        return finish(install_release(Path(temporary)))


if __name__ == "__main__":
    raise SystemExit(main())
