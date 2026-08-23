"""Find the critical path through a small build dependency graph."""

from __future__ import annotations

from decimal import Decimal

from tiergraph import (
    DECIMAL_TROPICAL,
    PATH,
    AttributeValuation,
    ChildCombination,
    FoldDeclaration,
    FoldTransition,
    TiePolicy,
    XsdType,
)
from tiergraph.build import document, item
from tiergraph.semiring import PathValue

NAMESPACE = "https://tiergraph.dev/examples/critical-path"


def _decimal(value: object) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError("duration must decode as Decimal")
    return value


def _duration(value: object, label: str) -> Decimal:
    """Negate duration so tropical minimum selects the longest path."""
    del label
    return -_decimal(value)


def _path_duration(value: object, label: str) -> PathValue:
    return (-_decimal(value), ((label,),))


def critical_path() -> tuple[Decimal, tuple[str, ...]]:
    """Return the build's critical-path duration and witnessing task sequence."""
    builder = document(NAMESPACE, prefix="build")
    builder.attribute("duration", XsdType.DECIMAL)
    tasks = builder.tier(
        "tasks",
        (
            item("compile", duration=4),
            item("lint", duration=2),
            item("link", duration=3),
            item("package", duration=1),
        ),
        item_type="task",
        membership="task-membership",
    )
    depends = builder.link(
        "depends",
        tasks,
        tasks,
        ((0, 2), (1, 2), (2, 3)),
        acyclic=True,
    )
    graph = builder.build()
    valuation = AttributeValuation("duration", builder.qname("duration"), (tasks.name,))
    transition = (FoldTransition(depends.name, ChildCombination.OR),)
    roots = (tasks.ref(0), tasks.ref(1))

    tropical = FoldDeclaration(
        "critical-path-length",
        graph,
        valuation,
        DECIMAL_TROPICAL,
        _duration,
        transition,
        roots=roots,
    ).run()
    witnessed = FoldDeclaration(
        "critical-path-witness",
        graph,
        valuation,
        PATH,
        _path_duration,
        transition,
        roots=roots,
        witness_order=lambda left, right: (left > right) - (left < right),
        tie_policy=TiePolicy.CHOOSE_FIRST,
        output_cap=1,
    ).run()
    if witnessed.provenance is None:
        raise RuntimeError("critical path did not produce a witness")
    return -tropical.value, witnessed.provenance[0]


def main() -> int:
    """Print the critical-path length and witnessing task sequence."""
    length, path = critical_path()
    print(f"Critical path length: {length}")
    print(f"Critical path: {' -> '.join(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
