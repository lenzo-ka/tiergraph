"""Command line entry point, a thin shell over the public API."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager, suppress
from decimal import Decimal
from pathlib import Path
from typing import Any, BinaryIO, cast

import tiergraph
import tiergraph_dot
from tiergraph import ExecutionError, Program, Step, load_program, semiring
from tiergraph import core as _core
from tiergraph import wire as _wire
from tiergraph.schema import Refusal, RefusalStage, json_schema, shape_hash

# The published semiring constants this shell has a spelling for, which is not
# every algebra a Python fold can name. The listing command and the fold command
# share this one mapping, so the vocabulary a user can read is exactly the
# vocabulary `fold --semiring` accepts.
_SEMIRINGS: dict[str, semiring.Semiring[Any]] = {
    "arctic": semiring.ARCTIC,
    "boolean": semiring.BOOLEAN,
    "counting": semiring.COUNTING,
    "decimal-arctic": semiring.DECIMAL_ARCTIC,
    "decimal-tropical": semiring.DECIMAL_TROPICAL,
    "path": semiring.PATH,
    "tropical": semiring.TROPICAL,
}


def build_parser() -> argparse.ArgumentParser:  # noqa: PLR0915 -- parser vocabulary
    """Return the argument parser."""
    parser = argparse.ArgumentParser(prog="tiergraph")
    parser.add_argument("--version", action="store_true", help="print the version")
    subparsers = parser.add_subparsers(dest="command")

    validate = subparsers.add_parser("validate", help="validate a graph document")
    validate.set_defaults(handler=_handle_validate)
    validate.add_argument("file", metavar="FILE", help="graph file, or - for stdin")

    discharge = subparsers.add_parser(
        "discharge", help="discharge a declaration against its inputs"
    )
    discharge_subparsers = discharge.add_subparsers(
        dest="discharge_command", required=True
    )
    seals = discharge_subparsers.add_parser(
        "seals", help="discharge a source graph's seals against a result graph"
    )
    seals.set_defaults(handler=_handle_discharge)
    seals.add_argument(
        "file", metavar="SOURCE", help="source graph file, or - for stdin"
    )
    seals.add_argument(
        "--result", required=True, metavar="FILE", help="result graph file"
    )
    seals.add_argument(
        "--name", default="rewrite", metavar="NAME", help="name used in refusals"
    )
    _output_argument(seals)

    discharge_fold = discharge_subparsers.add_parser(
        "fold", help="discharge a fold's exactness claim against its graph"
    )
    discharge_fold.set_defaults(handler=_handle_discharge)
    _fold_arguments(discharge_fold)
    discharge_fold.add_argument(
        "--exactness",
        choices=tuple(
            member.value
            for member in tiergraph.FoldExactness
            if member is not tiergraph.FoldExactness.UNDECLARED
        ),
        help="the claim to discharge; omitted, the library refuses UNDECLARED",
    )
    _output_argument(discharge_fold)

    render = subparsers.add_parser("render", help="render a graph as DOT")
    render.set_defaults(handler=_handle_render)
    _document_arguments(render)
    render.add_argument(
        "--include-empty-tiers", action="store_true", help="include empty tiers"
    )

    inspect = subparsers.add_parser("inspect", help="inspect a graph document")
    inspect.set_defaults(handler=_handle_inspect)
    _document_arguments(inspect)

    convert = subparsers.add_parser("convert", help="canonicalize a graph document")
    convert.set_defaults(handler=_handle_convert)
    _document_arguments(convert)
    convert.add_argument(
        "--to", choices=("json", "json-compact", "bytes"), required=True
    )

    schema = subparsers.add_parser("schema", help="print the graph document schema")
    schema.add_argument("--format-version", metavar="VERSION")
    schema.set_defaults(handler=_handle_schema)
    schema.add_argument("--hash", action="store_true", help="print the shape hash")
    _output_argument(schema)

    run = subparsers.add_parser("run", help="execute a JSONL machine program")
    run.set_defaults(handler=_handle_run)
    _document_arguments(run, input_help="JSONL program file, or - for stdin")
    run.add_argument(
        "--to", choices=("json", "json-compact", "bytes", "dot"), required=True
    )
    run.add_argument(
        "--include-empty-tiers",
        action="store_true",
        help="include empty tiers in DOT output",
    )

    step = subparsers.add_parser("step", help="step through a JSONL machine program")
    step.set_defaults(handler=_handle_step)
    _document_arguments(step, input_help="JSONL program file, or - for stdin")
    step.add_argument(
        "--interactive",
        action="store_true",
        help="use the interactive debugger (also enabled when stdin is a TTY)",
    )

    walk = subparsers.add_parser("walk", help="traverse a transitive relation")
    walk.set_defaults(handler=_handle_walk)
    walk.add_argument("file", metavar="GRAPH", help="graph file, or - for stdin")
    walk.add_argument("--source", action="append", required=True, metavar="PATH")
    walk.add_argument("--relation-namespace", required=True, metavar="NS")
    walk.add_argument("--relation-local", required=True, metavar="LOCAL")
    walk.add_argument("--direction", choices=("forward", "inverse"), default="forward")
    walk.add_argument("--cap", type=int, metavar="N")
    _output_argument(walk)

    path = subparsers.add_parser("path", help="resolve and spell tiergraph paths")
    path_subparsers = path.add_subparsers(dest="path_command", required=True)
    resolve = path_subparsers.add_parser("resolve", help="resolve a tiergraph path")
    resolve.set_defaults(handler=_handle_path)
    resolve.add_argument("file", metavar="GRAPH", help="graph file, or - for stdin")
    resolve.add_argument("tgpath", metavar="TGPATH", help="tiergraph path to resolve")
    _output_argument(resolve)

    spell = path_subparsers.add_parser("spell", help="spell a tiergraph path")
    spell.set_defaults(handler=_handle_path)
    spell.add_argument("file", metavar="GRAPH", help="graph file, or - for stdin")
    spell.add_argument("--kind", choices=("item", "boundary"), required=True)
    spell.add_argument("--tier-namespace", metavar="NS")
    spell.add_argument("--tier-local", metavar="LOCAL")
    spell.add_argument("--index", type=int, metavar="N")
    spell.add_argument("--durable-id", metavar="ID")
    spell.add_argument("--anchor-item-id", metavar="ID")
    spell.add_argument("--anchor-tier-namespace", metavar="NS")
    spell.add_argument("--anchor-tier-local", metavar="LOCAL")
    spell.add_argument("--side", choices=("before", "after"))
    _output_argument(spell)

    grammar = subparsers.add_parser("grammar", help="recognize with tiergraph grammars")
    grammar_subparsers = grammar.add_subparsers(dest="grammar_command", required=True)
    for grammar_command, help_text in (
        ("recognize", "recognize a token sequence"),
        ("count", "count token-sequence derivations"),
        ("best", "find best token-sequence derivations"),
    ):
        grammar_parser = grammar_subparsers.add_parser(grammar_command, help=help_text)
        grammar_parser.set_defaults(handler=_handle_grammar)
        grammar_parser.add_argument(
            "file", metavar="GRAMMAR", help="grammar JSON file, or - for stdin"
        )
        grammar_parser.add_argument("--tokens-json", required=True, metavar="JSON")
        if grammar_command == "best":
            grammar_parser.add_argument("--count", type=int, default=1, metavar="N")
        _output_argument(grammar_parser)

    clock = subparsers.add_parser("clock", help="query declarative clock timing")
    clock_subparsers = clock.add_subparsers(dest="clock_command", required=True)
    for clock_command, help_text in (
        ("coordinates", "list refined clock coordinates"),
        ("boundary", "query one tier boundary"),
        ("extent", "query a timed tier extent"),
        ("item", "query one timed item"),
    ):
        clock_parser = clock_subparsers.add_parser(clock_command, help=help_text)
        clock_parser.set_defaults(handler=_handle_clock)
        clock_parser.add_argument(
            "file", metavar="GRAPH", help="graph file, or - for stdin"
        )
        clock_parser.add_argument("--profile", required=True, metavar="FILE")
        if clock_command == "boundary":
            clock_parser.add_argument("--boundary", required=True, metavar="PATH")
        elif clock_command == "extent":
            clock_parser.add_argument("--tier-namespace", required=True, metavar="NS")
            clock_parser.add_argument("--tier-local", required=True, metavar="LOCAL")
        elif clock_command == "item":
            clock_parser.add_argument("--item", required=True, metavar="PATH")
        _output_argument(clock_parser)

    span = subparsers.add_parser("span", help="render declarative span views")
    span_subparsers = span.add_subparsers(dest="span_command", required=True)
    span_render = span_subparsers.add_parser("render", help="render a span view")
    span_render.set_defaults(handler=_handle_span)
    span_render.add_argument("file", metavar="GRAPH", help="graph file, or - for stdin")
    span_render.add_argument("--profile", required=True, metavar="FILE")
    span_render.add_argument(
        "--format", choices=("text", "json", "jsonl", "html", "dot"), required=True
    )
    span_render.add_argument("--alternatives", action="store_true")
    span_render.add_argument("--jsonl-record", choices=("input", "span"), default=None)
    span_render.add_argument("--include-empty-tiers", action="store_true")
    _output_argument(span_render)

    selection = subparsers.add_parser("select", help="evaluate a selector")
    selection.set_defaults(handler=_handle_select)
    selection.add_argument("file", metavar="GRAPH", help="graph file, or - for stdin")
    selection.add_argument("--selector", required=True, metavar="FILE")
    _output_argument(selection)

    fold = subparsers.add_parser("fold", help="fold a dependency relation")
    fold.set_defaults(handler=_handle_fold, exactness=None)
    _fold_arguments(fold)
    _output_argument(fold)

    semirings = subparsers.add_parser(
        "semirings", help="list the semirings this shell can name"
    )
    semirings.set_defaults(handler=_handle_semirings)
    _output_argument(semirings)
    return parser


def _document_arguments(
    parser: argparse.ArgumentParser, *, input_help: str = "graph file, or - for stdin"
) -> None:
    parser.add_argument("file", metavar="FILE", help=input_help)
    _output_argument(parser)


def _output_argument(parser: argparse.ArgumentParser) -> None:
    """Add the canonical output destination shared by every emitting command."""
    parser.add_argument(
        "-o", "--output", default="-", metavar="FILE", help="output file (default: -)"
    )


def _fold_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the flags one fold declaration is assembled from.

    ``fold`` and ``discharge fold`` build the same declaration from the same
    graph, valuation, algebra, and dependency relation, so they name those
    inputs once here. What separates them is the claim: ``fold`` runs the
    declaration and never consults its exactness, so it offers no flag for one,
    and ``discharge fold`` adds the flag whose claim is the whole of what it
    discharges.
    """
    parser.add_argument("file", metavar="GRAPH", help="graph file, or - for stdin")
    parser.add_argument(
        "--name", default="fold", metavar="NAME", help="name used in refusals"
    )
    parser.add_argument("--attribute-namespace", required=True, metavar="NS")
    parser.add_argument("--attribute-local", required=True, metavar="LOCAL")
    parser.add_argument(
        "--tier",
        action="append",
        nargs=2,
        required=True,
        metavar=("NS", "LOCAL"),
        help="one valuation domain tier; repeatable",
    )
    parser.add_argument("--semiring", choices=tuple(_SEMIRINGS), required=True)
    parser.add_argument(
        "--lift",
        choices=("one", "value"),
        required=True,
        help="embed the read value, or the semiring's multiplicative identity",
    )
    parser.add_argument(
        "--transition",
        action="append",
        nargs=3,
        required=True,
        metavar=("NS", "LOCAL", "COMBINATION"),
        help="one dependency relation and its and/or meaning; repeatable",
    )
    parser.add_argument(
        "--root",
        action="append",
        metavar="TGPATH",
        help="one declared root item; repeatable, inferred when omitted",
    )
    parser.add_argument(
        "--ranked",
        action="store_true",
        help="also report witnesses ranked by the semiring's own order",
    )
    parser.add_argument(
        "--output-cap", type=int, metavar="N", help="witness cap; requires --ranked"
    )


def _handle_validate(args: argparse.Namespace) -> None:
    graph = tiergraph.loads(_read_bytes(args.file))
    del graph
    _stdout_text("ok\n")


def _discharge_seals(args: argparse.Namespace) -> object:
    """Bind a source graph's seals to the result that claims to honor them.

    The declaration is assembled from flags rather than read from a document of
    its own, the way ``fold`` assembles its own declaration: both inputs are
    ordinary graph documents that ``loads`` already validates, and the only part
    left is a name, which exists so a refusal can say whose claim failed.
    """
    _check_distinct(args.result, args.output)
    source = tiergraph.loads(_read_bytes(args.file))
    result = tiergraph.loads(_read_bytes(args.result))
    return tiergraph.SealDeclaration(args.name, source, result).check_seals().to_data()


def _discharge_fold(args: argparse.Namespace) -> object:
    """Demand a fold's exactness claim against the graph and valuation it reads.

    The declaration is the one ``fold`` already assembles from the same flags,
    with the claim added, so the two commands cannot drift into describing
    different folds. ``--exactness`` is optional on purpose: leaving it off is
    not a usage error but reaches the library's own refusal, which hands back
    the declaration to be made rather than quietly standing in the weaker claim.
    """
    declaration = _fold_declaration(tiergraph.loads(_read_bytes(args.file)), args)
    return declaration.check_exactness().to_data(declaration.semiring)


# One entry per capability this verb carries. The four declaration kinds this
# package publishes take genuinely different inputs -- a pair of graphs for
# seals, a whole valuation for a fold, a graph and a role binding for a profile
# -- so the dispatch is a table of handlers rather than one shared shape they
# would all have to be bent into. Another capability is one subparser naming its
# inputs and one entry here returning its certificate's ``to_data()``.
#
# Two kinds are absent because that last clause is what they cannot supply, and
# both absences are the library's shape rather than an omission here. A rewrite
# effect takes the same pair of graphs seals does and refuses the same way, but
# ``RewriteCertificate`` publishes no ``to_data()``, and encoding it in this
# shell would put a public type's serialization somewhere no reader of that type
# would look for it. A profile is further off: its check returns nothing to
# certify, and ``ProfileRegistry.report`` answers a failing check with an
# accepting report, which is precisely the artifact ``discharge`` promises never
# to write under the name of a certificate.
_DISCHARGES: dict[str, Callable[[argparse.Namespace], object]] = {
    "fold": _discharge_fold,
    "seals": _discharge_seals,
}


def _handle_discharge(args: argparse.Namespace) -> int:
    """Emit the certificate a discharged declaration yields, or its refusal.

    This sits beside ``validate`` rather than inside it. ``validate`` answers
    whether one document is well formed; this answers whether a declaration
    holds against its inputs, which is a question about several documents at
    once and has an answer even when every one of them is well formed.

    A refusal leaves stdout and any ``--output`` file untouched, so an artifact
    this verb wrote is always a discharged certificate and never a report of
    failure wearing the same name.
    """
    try:
        certificate = _DISCHARGES[args.discharge_command](args)
    except Refusal as error:
        _refusal_diagnostic(args.command, error)
        return 1
    _write_output(args.file, args.output, _json_bytes(certificate))
    return 0


def _refusal_diagnostic(command: str, error: Refusal) -> None:
    """Report one staged refusal as a diagnostic line and as data beside it.

    The line keeps this command's stderr readable the way every other command's
    is; the object after it carries the stage, which a caller acts on and must
    not have to recover by matching the wording. ``step`` already writes its
    extra refusal detail to stderr after the same diagnostic line, so this is
    the shape a reader of this CLI's failures already meets.
    """
    _diagnostic(command, type(error).__name__, error)
    sys.stderr.write(_json_text({"refusal": _refusal_data(error)}))


def _refusal_data(error: Refusal) -> dict[str, object]:
    """Encode one refusal's declared stage, its rank, and any further condition.

    The stage is carried twice because either half alone forces the reader to
    supply the other: ``stage`` names the class of condition, and ``rank`` is its
    place in the declared total order, which is what says that this refusal
    explains the ones a later stage would have reported. ``also`` carries the
    conditions that stay applicable once this one is known, each with its own
    stage, so a document that meets two conditions is read as two rather than as
    one sentence mentioning both.

    Both channels are read through the one base, so this reaches ``stage`` and
    ``also`` on whatever it caught rather than asking which channel raised it.
    """
    return {
        "stage": _stage_name(error.stage),
        "rank": int(error.stage),
        "message": str(error),
        "also": [_refusal_data(entry) for entry in error.also],
    }


def _stage_name(stage: RefusalStage) -> str:
    """Spell one refusal stage the way this CLI spells every other enum member."""
    return stage.name.lower()


def _handle_render(args: argparse.Namespace) -> None:
    graph = tiergraph.loads(_read_bytes(args.file))
    rendered = _render(graph, args.include_empty_tiers)
    _write_output(args.file, args.output, _graph_report_bytes(graph, rendered))


def _handle_inspect(args: argparse.Namespace) -> None:
    graph = tiergraph.loads(_read_bytes(args.file))
    _write_output(args.file, args.output, _graph_report_bytes(graph, _inspect(graph)))


def _handle_convert(args: argparse.Namespace) -> None:
    graph = tiergraph.loads(_read_bytes(args.file))
    _write_output(args.file, args.output, _graph_bytes(graph, args.to))


def _handle_schema(args: argparse.Namespace) -> None:
    if args.hash and args.format_version is not None:
        raise ValueError("--format-version cannot be used with --hash")
    encoded = (
        (shape_hash() + "\n").encode("utf-8")
        if args.hash
        else _json_bytes(json_schema(args.format_version or tiergraph.FORMAT_VERSION))
    )
    _write_output("-", args.output, encoded)


def _handle_walk(args: argparse.Namespace) -> None:
    graph = tiergraph.loads(_read_bytes(args.file))
    profile = tiergraph.StructuralPathProfile()
    sources = [_walk_source(graph, profile, text) for text in args.source]
    source = sources[0]
    for selection in sources[1:]:
        source = source | selection
    result = tiergraph.Walk(
        source,
        tiergraph.QualifiedName(args.relation_namespace, args.relation_local),
        tiergraph.WalkDirection(args.direction),
        args.cap,
    ).evaluate()
    _write_output(args.file, args.output, _json_bytes(result.to_data()))


def _handle_path(args: argparse.Namespace) -> None:
    graph = tiergraph.loads(_read_bytes(args.file))
    profile = tiergraph.StructuralPathProfile()
    if args.path_command == "resolve":
        resolved = tiergraph.resolve_path(graph, profile, args.tgpath)
        if isinstance(resolved, tiergraph.ResolvedItem):
            value = {
                "kind": "item",
                "path": str(resolved.path),
                "current": resolved.current.to_data(),
            }
        elif isinstance(resolved, tiergraph.ResolvedBoundary):
            value = {
                "kind": "boundary",
                "path": str(resolved.path),
                "current": resolved.current.to_data(),
            }
        else:
            raise ValueError(  # pragma: no cover - StructuralPathProfile never yields an alternative
                "structural path profile returned an alternative"
            )
    else:
        value = {"path": str(profile.spell(_path_binding(args, profile, graph), graph))}
    _write_output(args.file, args.output, _json_bytes(value))


def _handle_grammar(args: argparse.Namespace) -> None:
    declaration = tiergraph.grammar_loads(_read_bytes(args.file))
    lowered = tiergraph.lower_grammar(declaration)
    tokens = _tokens_json(args.tokens_json)
    if args.grammar_command == "recognize":
        value: object = {
            "recognized": tiergraph.recognize(lowered, tokens).recognized()
        }
    elif args.grammar_command == "count":
        value = {"count": tiergraph.count(lowered, tokens)}
    else:
        if args.count < 1:
            raise ValueError(f"best derivation count {args.count!r} must be positive")
        value = {
            "derivations": [
                item.to_data() for item in tiergraph.best(lowered, tokens, args.count)
            ]
        }
    _write_output(args.file, args.output, _json_bytes(value))


def _handle_clock(args: argparse.Namespace) -> None:
    graph = tiergraph.loads(_read_bytes(args.file))
    profile = tiergraph.ClockProfile.from_data(graph, _profile_json(args.profile))
    _check_distinct(args.profile, args.output)
    _write_output(
        args.file, args.output, _json_bytes(_clock_query(graph, profile, args))
    )


def _handle_span(args: argparse.Namespace) -> None:
    if args.jsonl_record is not None and args.format != "jsonl":
        raise ValueError("--jsonl-record requires --format jsonl")
    if args.include_empty_tiers and args.format != "dot":
        raise ValueError("--include-empty-tiers requires --format dot")
    graph = tiergraph.loads(_read_bytes(args.file))
    profile = tiergraph.SpanViewProfile.from_data(_profile_json(args.profile))
    _check_distinct(args.profile, args.output)
    rendered = _span_render(graph, profile, args)
    _write_output(args.file, args.output, _graph_report_bytes(graph, rendered))


def _handle_select(args: argparse.Namespace) -> None:
    graph = tiergraph.loads(_read_bytes(args.file))
    selector = tiergraph.selection_loads(_read_bytes(args.selector))
    _check_distinct(args.selector, args.output)
    result = tiergraph.evaluate_selection(graph, selector)
    _write_output(args.file, args.output, _json_bytes({"nodes": result.to_data()}))


def _handle_fold(args: argparse.Namespace) -> None:
    graph = tiergraph.loads(_read_bytes(args.file))
    fold = _fold_declaration(graph, args)
    _write_output(
        args.file, args.output, _json_bytes(fold.run().to_data(fold.semiring))
    )


def _handle_semirings(args: argparse.Namespace) -> None:
    _write_output("-", args.output, _json_bytes(_semiring_report()))


def _handle_run(args: argparse.Namespace) -> None:
    if args.include_empty_tiers and args.to != "dot":
        raise ValueError("--include-empty-tiers requires --to dot")
    program = _read_program(args.file)
    graph = program.unroll().graph
    encoded = (
        _graph_report_bytes(graph, _render(graph, args.include_empty_tiers))
        if args.to == "dot"
        else _graph_bytes(graph, args.to)
    )
    _write_output(args.file, args.output, encoded)


def _handle_step(args: argparse.Namespace) -> int:
    if args.interactive and args.file == "-":
        raise ValueError("--interactive requires a program file, not stdin")
    program = _read_program(args.file)
    interactive = args.interactive or (args.file != "-" and sys.stdin.isatty())
    if interactive:
        if args.output != "-":
            raise ValueError("interactive mode requires stdout output")
        return _step_interactive(program)
    return _step_dump(program, args.file, args.output)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line. Returns the process exit status."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print(json.dumps({"version": tiergraph.__version__}))
        return 0
    if args.command is None:
        parser.print_help()
        return 0
    try:
        if hasattr(args, "output"):
            _check_distinct(getattr(args, "file", "-"), args.output)
        status = args.handler(args)
        if status is not None:
            return cast(int, status)
    except ExecutionError as error:
        _diagnostic(args.command, "ExecutionError", error)
        return 1
    except UnicodeError as error:
        _diagnostic(args.command, type(error).__name__, error)
        return 3
    except RecursionError as error:
        _diagnostic(args.command, "ValueError", error)
        return 1
    except tiergraph.PathRefusal as error:
        _diagnostic(args.command, "PathRefusal", error)
        return 1
    except ValueError as error:
        _diagnostic(args.command, "ValueError", error)
        return 1
    except OSError as error:
        _diagnostic(args.command, type(error).__name__, error)
        return 3
    return 0


def _clock_decimal(value: Decimal) -> str:
    """Encode a Decimal with the graph codec's canonical XSD lexical form."""
    return _core._canonical_lexical(tiergraph.XsdType.DECIMAL, format(value, "f"))


def _resolved_reference(
    graph: tiergraph.Graph, text: str, kind: str, subject: str
) -> tiergraph.ItemRef | tiergraph.BoundaryRef:
    """Resolve one structural path and require the requested reference kind."""
    resolved = tiergraph.resolve_path(graph, tiergraph.StructuralPathProfile(), text)
    if kind == "item" and isinstance(resolved, tiergraph.ResolvedItem):
        return resolved.current
    if kind == "boundary" and isinstance(resolved, tiergraph.ResolvedBoundary):
        return resolved.current
    article = "an" if kind == "item" else "a"
    raise ValueError(
        f"{subject} {kind} path {text!r} did not resolve to {article} {kind}"
    )


def _fold_lift(
    algebra: semiring.Semiring[Any], kind: str
) -> Callable[[object, str], Any]:
    """Return the named lift, the only two a command line can spell.

    A general lift is caller code, so the shell offers the two the folding guide
    uses: the read value itself, and the semiring's multiplicative identity. The
    value lift asks the algebra to encode each value before embedding it, so a
    carrier mismatch becomes the house refusal at the offending item instead of
    a ``TypeError`` escaping from inside the fold.
    """
    if kind == "one":

        def one(value: object, label: str) -> object:
            """Embed every read value as the semiring's multiplicative identity."""
            del value, label
            return algebra.one

        return one

    def carrier(value: object, label: str) -> object:
        """Embed one read attribute value, refusing a carrier mismatch."""
        try:
            algebra.encode(value)
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"lift 'value' cannot embed item {label!r} value {value!r} in the "
                f"{type(algebra).__name__} carrier: {error}"
            ) from error
        return value

    return carrier


def _child_combination(value: str) -> tiergraph.ChildCombination:
    """Decode one transition's declared child combination."""
    if value not in {member.value for member in tiergraph.ChildCombination}:
        raise ValueError(f"transition combination {value!r} must be 'and' or 'or'")
    return tiergraph.ChildCombination(value)


def _fold_declaration(
    graph: tiergraph.Graph, args: argparse.Namespace
) -> tiergraph.FoldDeclaration[Any]:
    """Build one public fold declaration from the parsed command line.

    The valuation carries the attribute's local name, because a valuation name
    only ever appears in a refusal and a second flag for it would buy nothing.
    Ranked output needs a declared tie policy that ranked selection then never
    consults, so the shell supplies one rather than offering an inert flag.

    ``args.exactness`` is ``None`` under ``fold``, which runs the declaration
    and never reads the claim, and is the claim itself under ``discharge fold``.
    The command that offers no flag leaves the declaration UNDECLARED rather
    than choosing a claim on the caller's behalf.
    """
    if args.output_cap is not None and not args.ranked:
        raise ValueError("--output-cap requires --ranked")
    algebra = _SEMIRINGS[args.semiring]
    attribute = tiergraph.QualifiedName(args.attribute_namespace, args.attribute_local)
    return tiergraph.FoldDeclaration(
        args.name,
        graph,
        tiergraph.AttributeValuation(
            attribute.local_name,
            attribute,
            tuple(
                tiergraph.QualifiedName(namespace, local)
                for namespace, local in args.tier
            ),
        ),
        algebra,
        _fold_lift(algebra, args.lift),
        tuple(
            tiergraph.FoldTransition(
                tiergraph.QualifiedName(namespace, local),
                _child_combination(combination),
            )
            for namespace, local, combination in args.transition
        ),
        roots=tuple(
            cast(
                tiergraph.ItemRef,
                _resolved_reference(graph, text, "item", "fold root"),
            )
            for text in args.root or ()
        ),
        output_cap=1 if args.output_cap is None else args.output_cap,
        ranked_output=args.ranked,
        exactness=(
            tiergraph.FoldExactness.UNDECLARED
            if args.exactness is None
            else tiergraph.FoldExactness(args.exactness)
        ),
    )


def _semiring_report() -> object:
    """Report every nameable semiring's carrier boundary and declared laws."""
    return {
        "semirings": [
            {
                "name": name,
                "type": type(algebra).__name__,
                "zero": algebra.encode(algebra.zero),
                "one": algebra.encode(algebra.one),
                "laws": {
                    "add_associativity": algebra.add_associativity.value,
                    "add_commutativity": algebra.add_commutativity.value,
                    "left_distributivity": algebra.left_distributivity.value,
                    "multiply_associativity": algebra.multiply_associativity.value,
                    "right_distributivity": algebra.right_distributivity.value,
                },
                "properties": {
                    "add_idempotent": algebra.add_idempotent,
                    "add_selective": algebra.add_selective,
                    "multiply_commutative": algebra.multiply_commutative,
                    "multiply_preserves_witness_order": (
                        algebra.multiply_preserves_witness_order
                    ),
                    "multiply_strictly_order_preserving": (
                        algebra.multiply_strictly_order_preserving
                    ),
                    "no_zero_divisors": algebra.no_zero_divisors,
                    "zero_sum_free": algebra.zero_sum_free,
                },
                "star": None if algebra.star is None else algebra.star.name,
            }
            for name, algebra in _SEMIRINGS.items()
        ]
    }


def _clock_query(
    graph: tiergraph.Graph,
    profile: tiergraph.ClockProfile,
    args: argparse.Namespace,
) -> object:
    """Evaluate one parsed clock query and return JSON-compatible data."""
    if args.clock_command == "coordinates":
        return {
            "clock_tier": profile.clock_tier.to_data(),
            "coordinates": [
                {"index": index, **coordinate.to_data()}
                for index, coordinate in enumerate(profile.coordinates)
            ],
        }
    if args.clock_command == "boundary":
        boundary_reference = cast(
            tiergraph.BoundaryRef,
            _resolved_reference(graph, args.boundary, "boundary", "clock"),
        )
        return {
            "boundary": boundary_reference.to_data(),
            "clock_index": profile.clock_index(boundary_reference),
            "refined": profile.refined_coordinate(boundary_reference).to_data(),
        }
    if args.clock_command == "extent":
        tier = tiergraph.QualifiedName(args.tier_namespace, args.tier_local)
        start, end = profile.extent(tier)
        return {
            "tier": tier.to_data(),
            "start": start.to_data(),
            "end": end.to_data(),
        }
    item_reference = cast(
        tiergraph.ItemRef, _resolved_reference(graph, args.item, "item", "clock")
    )
    start, end = profile.structural_span(item_reference.tier, item_reference.index)
    physical = profile.timing(item_reference.tier, item_reference.index)
    if profile.has_uniform_rate:
        ticks, rate = profile.duration(item_reference.tier, item_reference.index)
        exact_duration: object = {"ticks": ticks, "rate": _clock_decimal(rate)}
    else:
        exact_duration = None
    return {
        "item": item_reference.to_data(),
        "structural": {
            "start": start.to_data(),
            "end": end.to_data(),
        },
        "physical": (None if physical is None else physical.to_data()),
        "exact_duration": exact_duration,
    }


def _walk_source(
    graph: tiergraph.Graph,
    profile: tiergraph.StructuralPathProfile,
    source_text: str,
) -> tiergraph.NodeSet:
    """Resolve one walk source path as a single-node selection."""
    resolved = tiergraph.resolve_path(graph, profile, source_text)
    if isinstance(resolved, tiergraph.ResolvedItem):
        return tiergraph.evaluate_selection(
            graph, tiergraph.ItemSelector(resolved.current)
        )
    if isinstance(resolved, tiergraph.ResolvedBoundary):
        return tiergraph.evaluate_selection(
            graph, tiergraph.BoundarySelector(resolved.current)
        )
    raise ValueError(  # pragma: no cover - StructuralPathProfile never yields an alternative
        "structural path profile returned an alternative"
    )


def _tokens_json(source: str) -> tuple[str, ...]:
    """Decode a JSON array of token strings without shell splitting."""
    value = json.loads(source)
    if not isinstance(value, list) or not all(
        isinstance(token, str) for token in value
    ):
        raise ValueError("--tokens-json must be a JSON array of strings")
    return tuple(value)


def _path_binding(
    args: argparse.Namespace,
    profile: tiergraph.StructuralPathProfile,
    graph: tiergraph.Graph,
) -> tiergraph.PathBinding:
    """Translate spell flags to a path and let the profile bind its form."""
    structural = (args.tier_namespace, args.tier_local, args.index)
    anchor_tier = (args.anchor_tier_namespace, args.anchor_tier_local)
    conflicts: list[str] = []
    if args.kind == "item":
        if args.durable_id is not None:
            segments = ["items", "durable", args.durable_id]
            conflicts = _present_path_values(structural)
        elif any(value is not None for value in structural):
            segments = ["items", "structural", *_path_values(structural)]
            conflicts = _present_path_values((args.durable_id,))
        else:
            segments = ["items"]
        conflicts.extend(
            _present_path_values((*anchor_tier, args.anchor_item_id, args.side))
        )
    elif any(value is not None for value in structural):
        segments = ["positions", "structural", *_path_values(structural)]
        conflicts = _present_path_values((*anchor_tier, args.anchor_item_id, args.side))
    elif args.anchor_item_id is not None:
        segments = [
            "positions",
            "durable",
            "item",
            args.anchor_item_id,
            *_path_values((args.side,)),
        ]
        conflicts = _present_path_values(anchor_tier)
    elif any(value is not None for value in anchor_tier):
        segments = [
            "positions",
            "durable",
            "tier",
            *_path_values((*anchor_tier, args.side)),
        ]
    else:
        segments = ["positions"]
        conflicts = _present_path_values((args.durable_id, args.side))
    if args.kind == "boundary" and args.durable_id is not None:
        conflicts.append(args.durable_id)
    return profile.bind(tiergraph.CanonicalPath(tuple([*segments, *conflicts])), graph)


def _path_values(values: tuple[object | None, ...]) -> list[str]:
    """Preserve missing and present flag values as path segments."""
    return ["" if value is None else str(value) for value in values]


def _present_path_values(values: tuple[object | None, ...]) -> list[str]:
    """Return only supplied flag values as conflict-marking segments."""
    return [str(value) for value in values if value is not None]


def _diagnostic(command: str, category: str, error: BaseException) -> None:
    print(f"tiergraph: {command}: {category}: {error}", file=sys.stderr)


def _read_bytes(filename: str) -> bytes:
    if filename == "-":
        return sys.stdin.buffer.read()
    return Path(filename).read_bytes()


def _profile_json(filename: str) -> object:
    """Read one declarative profile under the document envelope, encoding, and
    syntax stages.

    A profile is not a graph document, but it arrives the same way: as bytes the
    caller supplies, read against a declaration. Handing those bytes straight to
    ``json.loads`` would let the standard library sniff a byte-order mark and
    accept UTF-16 or UTF-32 text that the same reader refuses at ``ENCODING``
    when it arrives as a graph, and would leave the size and nesting bounds
    unenforced. Routing through the reader every other document takes makes the
    profile answer those conditions at the same rank and in the same wording.
    """
    return _wire._parsed_json(_read_bytes(filename))


def _stdout_text(value: str) -> None:
    sys.stdout.write(value)


def _write_output(input_name: str, output_name: str, value: bytes) -> None:
    with _output_stream(input_name, output_name) as stream:
        stream.write(value)


@contextmanager
def _output_stream(input_name: str, output_name: str) -> Iterator[BinaryIO]:
    if output_name == "-":
        yield sys.stdout.buffer
        return
    output_path = Path(output_name).resolve()
    _check_distinct(input_name, output_name)
    output_path.parent.mkdir(parents=False, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{output_path.name}.", dir=output_path.parent
    )
    replaced = False
    try:
        with os.fdopen(descriptor, "wb") as stream:
            yield stream
        os.replace(temporary, output_path)
        replaced = True
    finally:
        if not replaced:
            with suppress(FileNotFoundError):
                os.unlink(temporary)


def _check_distinct(input_name: str, output_name: str) -> None:
    if (
        input_name != "-"
        and output_name != "-"
        and Path(input_name).resolve() == Path(output_name).resolve()
    ):
        raise ValueError("input and output paths must differ")


def _render(graph: tiergraph.Graph, include_empty_tiers: bool) -> str:
    try:
        return tiergraph_dot.dumps(graph, include_empty_tiers=include_empty_tiers)
    except TypeError as error:
        raise ValueError(str(error)) from error


def _span_render(
    graph: tiergraph.Graph,
    profile: tiergraph.SpanViewProfile,
    args: argparse.Namespace,
) -> str:
    """Render one graph through a declarative span-view profile."""
    if args.format == "dot":
        return tiergraph_dot.dumps_spans(
            graph,
            profile,
            alternatives=args.alternatives,
            include_empty_tiers=args.include_empty_tiers,
        )
    view = tiergraph.span_view(graph, profile, alternatives=args.alternatives)
    if args.format == "text":
        return tiergraph.to_text(view, alternatives=args.alternatives)
    if args.format == "json":
        return tiergraph.to_json(view, alternatives=args.alternatives)
    if args.format == "jsonl":
        return tiergraph.to_jsonl(
            view,
            record=args.jsonl_record or "input",
            alternatives=args.alternatives,
        )
    return tiergraph.to_html(view, alternatives=args.alternatives)


def _graph_bytes(graph: tiergraph.Graph, target: str) -> bytes:
    if target == "bytes":
        return tiergraph.dump_bytes(graph)
    if target == "json":
        return _json_bytes(tiergraph.to_data(graph))
    return tiergraph.dump_compact(graph).encode("utf-8")


def _graph_report_bytes(graph: tiergraph.Graph, rendered: str) -> bytes:
    """Encode one graph-derived report, refusing what the wire writer refuses.

    A report is not the wire document, so its text never reaches ``to_data``.
    Asking the writer's one shared root about the graph makes the CLI's own
    reports refuse the same strings, named by the same field path, instead of
    leaking the encoder's ``UnicodeEncodeError``.
    """
    tiergraph.to_data(graph)
    return rendered.encode("utf-8")


def _json_text(value: object) -> str:
    """Render one report as the CLI's canonical JSON text.

    Split from ``_json_bytes`` because stderr is a text stream: a diagnostic
    written there must not have to encode and be decoded again, and both
    channels stay one refusal rule and one spelling.
    """
    _wire._refuse_unencodable_strings(cast(_core.JsonValue, value), "")
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def _json_bytes(value: object) -> bytes:
    return _json_text(value).encode("utf-8")


def _step_bytes(step: Step) -> bytes:
    """Serialize only the public step data as one deterministic JSONL record."""
    data = step.to_data()
    _wire._refuse_unencodable_strings(data, "")
    return (
        json.dumps(
            data,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _step_dump(program: Program, input_name: str, output_name: str) -> int:
    last: Step | None = None
    with _output_stream(input_name, output_name) as stream:
        try:
            for step in tiergraph.steps(program):
                stream.write(_step_bytes(step))
                last = step
        except ExecutionError as error:
            _step_refusal(last, error)
            return 1
    return 0


def _step_refusal(last: Step | None, error: ExecutionError) -> None:
    failing_index = last.index + 1 if last is not None else 0
    graph = last.graph if last is not None else tiergraph.Graph((), (), ())
    _diagnostic("step", "ExecutionError", error)
    print(f"tiergraph: step: failing opcode index: {failing_index}", file=sys.stderr)
    print("tiergraph: step: last good graph:", file=sys.stderr)
    sys.stderr.write(tiergraph.dumps(graph))


def _step_interactive(program: Program) -> int:
    iterator = iter(tiergraph.steps(program))
    trace: list[Step] = []
    finished = False
    command_with_argument_word_count = 2
    while True:
        try:
            command = input("step> ").strip()
        except EOFError:
            return 0
        if not command:
            continue
        words = command.split()
        name = words[0]
        if name in {"quit", "q"}:
            return 0
        if name in {"print", "inspect", "p"} and len(words) == 1:
            graph = trace[-1].graph if trace else tiergraph.Graph((), (), ())
            _stdout_text(tiergraph.dumps(graph))
            continue
        if name == "list" and len(words) == 1:
            for step in trace:
                sys.stdout.buffer.write(_step_bytes(step))
            continue
        target: int | None = None
        if name in {"step", "next", "s", "n"} and len(words) == 1:
            target = trace[-1].index + 1 if trace else 0
        elif name in {"continue", "c"} and len(words) == 1:
            target = None
        elif (
            name in {"run-to", "break"}
            and len(words) == command_with_argument_word_count
        ):
            try:
                target = int(words[1])
            except ValueError:
                _stdout_text("expected a non-negative opcode index\n")
                continue
            if target < 0:
                _stdout_text("expected a non-negative opcode index\n")
                continue
            if trace and target <= trace[-1].index:
                _stdout_text(f"already at opcode {trace[-1].index}\n")
                continue
        else:
            _stdout_text(
                "commands: step/next, continue, run-to N/break N, "
                "print/inspect, list, quit\n"
            )
            continue
        if finished:
            _stdout_text("end of program\n")
            continue
        status, finished = _step_until(iterator, trace, target)
        if status != 0:
            return status


def _step_until(
    iterator: Iterator[Step], trace: list[Step], target: int | None
) -> tuple[int, bool]:
    try:
        while target is None or not trace or trace[-1].index < target:
            step = next(iterator)
            trace.append(step)
            sys.stdout.buffer.write(_step_bytes(step))
    except StopIteration:
        _stdout_text("end of program\n")
        return 0, True
    except ExecutionError as error:
        _step_refusal(trace[-1] if trace else None, error)
        return 1, True
    return 0, False


def _qualified_name_text(value: object) -> str:
    """Spell a summary's serialized qualified name the way diagnostics spell it.

    The report keeps the expanded ``{namespace}local`` form it has always shown;
    only the summary's carrier changed from a name object to its data.
    """
    name = cast(dict[str, object], value)
    return f"{{{name['namespace']}}}{name['local_name']}"


def _inspect(graph: tiergraph.Graph) -> str:
    summary = tiergraph.graph_summary(graph)
    lines = [
        f"format version: {summary['format_version']}",
        f"namespaces: {summary['namespaces']}",
        f"tiers: {summary['tiers']}",
        f"items: {summary['items']}",
        f"relation declarations: {summary['relation_declarations']}",
        f"binary relation instances: {summary['binary_relation_instances']}",
        f"polyadic relation instances: {summary['polyadic_relation_instances']}",
        f"attribute declarations: {summary['attribute_declarations']}",
        f"populated position values: {summary['populated_position_values']}",
        f"document attributes: {summary['document_attributes']}",
    ]
    tier_summaries = cast(list[dict[str, object]], summary["tier_summaries"])
    lines.extend(
        (
            f"tier: {_qualified_name_text(tier['name'])} | {tier['long_name']} | "
            f"items={tier['items']} | attributes={tier['attributes']}"
        )
        for tier in tier_summaries
    )
    relation_summaries = cast(list[dict[str, object]], summary["relation_summaries"])
    lines.extend(
        (
            f"relation: {_qualified_name_text(relation['name'])} "
            f"| kind={relation['kind']}"
        )
        for relation in relation_summaries
    )
    return "\n".join(lines) + "\n"


def _read_program(filename: str) -> Program:
    if filename == "-":
        return load_program(sys.stdin.buffer)
    with Path(filename).open("rb") as stream:
        return load_program(stream)


__all__ = ["build_parser", "main"]
