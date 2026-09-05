"""Refuse a reservation that the tree has already overtaken.

A reservation is a documented promise that something is deliberately absent: a
name kept for a meaning nothing produces yet, a helper withheld until a decision
lands, a rule left unratified. The two ways a reservation can go wrong are not
symmetric. One that is merely undischarged is visible -- a reader meets the
docstring and sees the promise standing. One that quietly became false is
invisible: the thing it waited on now exists, the prose still says it does not,
and nothing in the tree disagrees. This gate exists for that second case.

Every reservation on a covered surface is registered here with the docstring
that carries it, the exact prose pinned, and the condition that would discharge
it. ``RESERVATIONS`` holds those a machine can decide. Each carries a predicate
that returns evidence when the tree has overtaken the promise, and this gate
fails naming that evidence, so the condition is checked rather than merely
declared. ``UNENFORCEABLE`` holds the ones no observable here decides, each with
the reason; for those this gate pins the prose and claims nothing further.

The register is read in both directions. An entry whose prose is gone or
reworded fails, so a reservation cannot be edited away while its entry survives.
A docstring that announces a reservation in this project's vocabulary and
carries no entry fails, so a new one cannot be written without recording what
would discharge it.

Scope, stated so a green run is not read for more than it earns:

* Covered: docstrings under ``src/tiergraph``, ``src/tiergraph_dot``, and
  ``examples/``. ``docs/reference/api.md`` is generated from those docstrings,
  so a reservation printed there is covered by the docstring it came from.
* Not covered: hand-written Markdown, including the guide pages, ``README.md``,
  and ``CHANGELOG.md``. The vocabulary below is ordinary English on those pages
  and no lexical rule separates the senses. The changelog's "``loads`` also
  defers materializing omitted members" describes shipped behavior, and
  ``docs/concepts.md``'s "For now, the sanctioned pattern is" introduces a
  recommendation; neither is a reservation of the kind registered here, and a
  scan of that prose would flag both.
* Not covered: ``tests/`` and ``scripts/``. A test or a gate describes behavior
  rather than promising it, and this file necessarily writes down the whole
  vocabulary it searches for, exactly as the tracked-file gate must write down
  the patterns it forbids.
* Not covered: a reservation whose prose uses none of ``VOCABULARY``. The
  completeness half is lexical. It finds a reservation that announces itself in
  the words this project uses, and it finds no others.

Each predicate states its own reach in its docstring as well, because a
predicate that watches one spelling of an arrival cannot see the arrival under
another, and a reader is entitled to know which spelling is watched.
"""

from __future__ import annotations

import ast
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SURFACES: tuple[Path, ...] = (
    ROOT / "src" / "tiergraph",
    ROOT / "src" / "tiergraph_dot",
    ROOT / "examples",
)
MODULE_SYMBOL = "<module>"

# The words this project uses when it declines to provide something. A docstring
# on a covered surface that uses one of them is announcing a reservation and has
# to be registered below. "deliberately" is deliberately absent: it is this
# project's word for a settled design refusal as well -- no set algebra on a
# node sequence, no migration of an older format -- and those are closed
# decisions, not promises awaiting a condition.
VOCABULARY: tuple[str, ...] = (
    "reserved",
    "reserves",
    "deferred",
    "defers",
    "unsettled",
    "not yet",
    "not currently",
    "no current",
    "not produced",
    "out of scope",
    "for now",
    "at present",
    "unimplemented",
    "not implemented",
    "placeholder",
    "provisional",
    "pending",
    "todo",
    "fixme",
)
ANNOUNCEMENT = re.compile(
    "(?i)(?<![a-z])(?:"
    + "|".join(re.escape(term) for term in VOCABULARY)
    + ")(?![a-z])"
)


@dataclass(frozen=True, slots=True)
class Registered:
    """Locate one registered reservation and pin the prose that carries it."""

    name: str
    site: str
    symbol: str
    text: str
    condition: str


@dataclass(frozen=True, slots=True)
class Reservation(Registered):
    """A reservation whose condition an observable in this tree can decide."""

    overtaken: Callable[[], str | None]


@dataclass(frozen=True, slots=True)
class Unenforceable(Registered):
    """A reservation no observable in this tree can decide, and the reason."""

    why: str


def shipped_python(surfaces: Sequence[Path] = SURFACES) -> list[Path]:
    """Return every Python file on the covered surfaces, in a stable order."""
    return [path for surface in surfaces for path in sorted(surface.rglob("*.py"))]


def docstrings(path: Path) -> list[tuple[str, str]]:
    """Return each documented symbol in one file with its docstring.

    Symbols are qualified, so a method that shares a name with another class's
    method keeps its own entry instead of shadowing it.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[str, str]] = []
    module = ast.get_docstring(tree)
    if module is not None:
        found.append((MODULE_SYMBOL, module))

    def descend(body: Sequence[ast.stmt], prefix: str) -> None:
        """Record documented definitions in one body, then their own bodies."""
        for node in body:
            if not isinstance(
                node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
            ):
                continue
            qualified = f"{prefix}{node.name}"
            text = ast.get_docstring(node)
            if text is not None:
                found.append((qualified, text))
            descend(node.body, f"{qualified}.")

    descend(tree.body, "")
    return found


def label(path: Path) -> str:
    """Return a path relative to the repository root, or its bare name outside."""
    if path.is_relative_to(ROOT):
        return path.relative_to(ROOT).as_posix()
    return path.name


def attribute_references(member: str, paths: Sequence[Path]) -> list[str]:
    """Return ``path:line`` for every attribute access spelling this member."""
    return [
        f"{label(path)}:{node.lineno}"
        for path in paths
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Attribute) and node.attr == member
    ]


def public_arrivals(words: Sequence[str], paths: Sequence[Path]) -> list[str]:
    """Return ``path:name`` for public definitions whose name spells one word."""
    return [
        f"{label(path)}:{node.name}"
        for path in paths
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        and not node.name.startswith("_")
        and any(word in node.name.lower() for word in words)
    ]


def _reserved_refusal_is_produced(paths: Sequence[Path] | None = None) -> str | None:
    """Report shipped code that names the reserved path-refusal member.

    Reach: an attribute access spelling ``BOUNDARY_NOT_IN_PARENT``, which is how
    every other member of that enum is used. A resolver reaching the member
    through ``PathRefusalCode(value)`` or ``PathRefusalCode[name]`` would not be
    seen; nothing in the tree constructs a refusal code that way today.
    """
    sites = attribute_references(
        "BOUNDARY_NOT_IN_PARENT", shipped_python() if paths is None else paths
    )
    if not sites:
        return None
    return "shipped code names the reserved member at " + ", ".join(sites)


# The three concepts convenience construction withholds, and the roots a helper
# for any of them would have to be named after.
DEFERRED_ERGONOMICS: tuple[str, ...] = ("choice", "alternative", "select")


def _build_ergonomics_have_landed(paths: Sequence[Path] | None = None) -> str | None:
    """Report a public convenience-construction name spelling a withheld helper.

    Reach: a public class, function, or method defined in ``build.py`` whose name
    contains ``choice``, ``alternative``, or ``select``. It is scoped to that one
    module on purpose -- those roots are ordinary names elsewhere in the package,
    on selectors, span alternatives, and grammar alternatives. A helper arriving
    under a name sharing none of the three roots would not be seen, but it would
    also be undiscoverable to the reader the reservation is written for.
    """
    landed = public_arrivals(
        DEFERRED_ERGONOMICS,
        [ROOT / "src" / "tiergraph" / "build.py"] if paths is None else paths,
    )
    if not landed:
        return None
    return "convenience construction now publishes " + ", ".join(landed)


RESERVATIONS: tuple[Reservation, ...] = (
    Reservation(
        name="boundary-not-in-parent",
        site="src/tiergraph/path.py",
        symbol="PathRefusalCode",
        text=(
            "``BOUNDARY_NOT_IN_PARENT`` is reserved and is not produced by a "
            "current path\nresolver or profile."
        ),
        condition=(
            "a shipped module names PathRefusalCode.BOUNDARY_NOT_IN_PARENT, "
            "which is what producing it from a resolver or profile amounts to"
        ),
        overtaken=_reserved_refusal_is_produced,
    ),
    Reservation(
        name="build-ergonomics",
        site="src/tiergraph/build.py",
        symbol=MODULE_SYMBOL,
        text=(
            "Ergonomic choice, alternatives, and selection helpers are deferred "
            "pending the\ndownstream gesture subsystem's canonical-byte goldens."
        ),
        condition=(
            "convenience construction publishes a public name spelling choice, "
            "alternatives, or selection"
        ),
        overtaken=_build_ergonomics_have_landed,
    ),
)

UNENFORCEABLE: tuple[Unenforceable, ...] = (
    Unenforceable(
        name="declared-readout",
        site="src/tiergraph/fold.py",
        symbol="FoldDeclaration",
        text=(
            "A readout or final division above the algebra is not currently "
            "provided. If one\nis introduced, it must be declared as part of what "
            "the fold profile records. A\nconstruct whose soundness depends on a "
            "property it cannot verify must declare\nthat property rather than "
            "assume it."
        ),
        condition=(
            "a shipped module computes a final answer in a post-pass above the "
            "algebra without recording that readout in the fold profile's "
            "declared signature"
        ),
        why=(
            "an undeclared post-pass has no reserved name, decorator, return "
            "type, protocol, or declaration field that distinguishes it from "
            "ordinary computation; Python syntax can reveal a chosen spelling "
            "but cannot decide whether that computation is the final answer or "
            "whether its soundness depends on an unrecorded property, so a "
            "predicate could miss the arrival it claims to enforce"
        ),
    ),
    Unenforceable(
        name="graph-composition",
        site="examples/json_document.py",
        symbol=MODULE_SYMBOL,
        text=(
            "attaching such a value to an item in another graph needs "
            "composition machinery tiergraph does not yet provide."
        ),
        condition=(
            "tiergraph publishes machinery that attaches one graph's value to an "
            "item of another"
        ),
        why=(
            "the machinery has no reserved name, no placeholder export, and no "
            "refusal code standing in for it, so nothing in this tree changes "
            "shape when it arrives; a predicate guessing at its eventual "
            "spelling would be a check that can never fire, which is worse than "
            "no check because a reader would count it as coverage"
        ),
    ),
    Unenforceable(
        name="cone-model",
        site="src/tiergraph/core.py",
        symbol="SealDeclaration",
        text=(
            "The cone model is reserved until a whole-graph seal exists as one "
            "frozen base,\na mergeable delta type exists, coordinate removal is "
            "expressible within a\nfootprint or excluded from the mergeable set, "
            "and observed-read validation is\ndecided."
        ),
        condition=(
            "one whole-graph seal can freeze the base, a mergeable delta type "
            "exists, coordinate removal is either representable in a footprint "
            "or excluded, and observed-read validation has a decision"
        ),
        why=(
            "SealedCarrier exposes only one tier or relation carrier at a time, "
            "and their conjunction is not one base for a footprint declaration; "
            "the other arrivals have no reserved names or declaration fields, so "
            "syntax cannot distinguish them from unrelated deltas, removals, or "
            "read validation without guessing their eventual forms"
        ),
    ),
)


def registered() -> tuple[Registered, ...]:
    """Return every register entry, enforceable or not."""
    return (*RESERVATIONS, *UNENFORCEABLE)


def unpinned(entries: Sequence[Registered]) -> list[str]:
    """Return one message per entry whose pinned prose is gone or reworded."""
    messages: list[str] = []
    for entry in entries:
        path = ROOT / entry.site
        if not path.is_file():
            messages.append(f"{entry.name}: {entry.site} no longer exists")
            continue
        documented = dict(docstrings(path))
        if entry.symbol not in documented:
            messages.append(
                f"{entry.name}: {entry.site} has no documented {entry.symbol}"
            )
        elif documented[entry.symbol].count(entry.text) != 1:
            messages.append(
                f"{entry.name}: the reserving prose in {entry.site}:{entry.symbol} "
                "is missing or changed"
            )
    return messages


def undeclared(entries: Sequence[Registered], paths: Sequence[Path]) -> list[str]:
    """Return one message per announced reservation carrying no register entry."""
    declared = {(entry.site, entry.symbol) for entry in entries}
    return [
        f"{label(path)}:{symbol} announces a reservation "
        f"({found.group(0)!r}) that the register does not carry"
        for path in paths
        for symbol, text in docstrings(path)
        if (found := ANNOUNCEMENT.search(text)) is not None
        and (label(path), symbol) not in declared
    ]


def stale(reservations: Sequence[Reservation]) -> list[str]:
    """Return one message per reservation the tree has already overtaken."""
    messages: list[str] = []
    for reservation in reservations:
        evidence = reservation.overtaken()
        if evidence is not None:
            messages.append(
                f"{reservation.name}: {reservation.site}:{reservation.symbol} "
                f"still reserves this, but {evidence}; the condition it waited "
                f"on ({reservation.condition}) now holds"
            )
    return messages


def main() -> int:
    """Check the register against the tree. Returns the process exit status."""
    entries = registered()
    found = unpinned(entries)
    found.extend(undeclared(entries, shipped_python()))
    found.extend(stale(RESERVATIONS))
    if not found:
        return 0
    print(
        "every reservation must still be true and still be declared:", file=sys.stderr
    )
    for message in found:
        print(f"  {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
