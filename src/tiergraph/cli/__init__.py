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
from tiergraph import match as _match
from tiergraph import predicate as _predicate
from tiergraph import wire as _wire
from tiergraph.budget import _MAX_USER_STEPS, _metered
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
    "log-probability": semiring.LOG_PROBABILITY,
    "path": semiring.PATH,
    "tropical": semiring.TROPICAL,
}
_MATCH_MIN_ARGS = 3

_EXIT_STATUS_HELP = """Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input"""


def _help_epilog(details: str, *examples: str) -> str:
    """Build one raw help epilog from explanatory text and runnable examples."""
    rendered = "\n".join(f"  $ {example}" for example in examples)
    return f"{details}Examples:\n{rendered}\n\n{_EXIT_STATUS_HELP}"


def _subcommand(
    subparsers: Any,
    name: str,
    *,
    summary: str,
    description: str,
    examples: tuple[str, ...],
    details: str = "",
) -> argparse.ArgumentParser:
    """Add a documented subcommand without changing its parsing behavior."""
    return cast(
        argparse.ArgumentParser,
        subparsers.add_parser(
            name,
            help=summary,
            description=description,
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog=_help_epilog(details, *examples),
        ),
    )


def build_parser() -> argparse.ArgumentParser:  # noqa: PLR0915 -- parser vocabulary
    """Return the argument parser."""
    parser = argparse.ArgumentParser(
        prog="tiergraph",
        description="Validate, query, transform, and render tiergraph documents.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_help_epilog("", "tiergraph validate graph.json"),
    )
    parser.add_argument("--version", action="store_true", help="print the version")
    subparsers = parser.add_subparsers(dest="command")

    validate = _subcommand(
        subparsers,
        "validate",
        summary="validate a graph document",
        description="Validate one graph document and print 'ok' when it is accepted.",
        examples=("tiergraph validate graph.json",),
    )
    validate.set_defaults(handler=_handle_validate)
    validate.add_argument("file", metavar="FILE", help="graph file, or - for stdin")

    discharge = _subcommand(
        subparsers,
        "discharge",
        summary="discharge a declaration against its inputs",
        description="Check a declared seal, rewrite effect, or fold exactness claim.",
        examples=("tiergraph discharge seals source.json --result result.json",),
    )
    discharge_subparsers = discharge.add_subparsers(
        dest="discharge_command", required=True
    )
    seals = _subcommand(
        discharge_subparsers,
        "seals",
        summary="discharge a source graph's seals against a result graph",
        description="Check that a result graph honors every seal on its source graph.",
        examples=("tiergraph discharge seals source.json --result result.json",),
    )
    seals.set_defaults(handler=_handle_discharge)
    _graph_pair_arguments(seals)
    _output_argument(seals)

    discharge_rewrite = _subcommand(
        discharge_subparsers,
        "rewrite",
        summary="discharge a rewrite's effect claim against the pair it read",
        description="Check a rewrite effect claim against its source and result graphs.",
        examples=(
            "tiergraph discharge rewrite source.json --result result.json "
            "--effect decorate",
        ),
    )
    discharge_rewrite.set_defaults(handler=_handle_discharge)
    _graph_pair_arguments(discharge_rewrite)
    discharge_rewrite.add_argument(
        "--effect",
        choices=tuple(
            member.value
            for member in tiergraph.RewriteEffect
            if member is not tiergraph.RewriteEffect.UNDECLARED
        ),
        help="the claim to discharge; omitted, the library refuses UNDECLARED",
    )
    _output_argument(discharge_rewrite)

    discharge_fold = _subcommand(
        discharge_subparsers,
        "fold",
        summary="discharge a fold's exactness claim against its graph",
        description="Check a fold exactness claim against the graph and valuation.",
        examples=(
            "tiergraph discharge fold fold.json --attribute-namespace urn:test:fold "
            "--attribute-local cost --tier urn:test:fold tasks --semiring counting "
            "--lift one --transition urn:test:fold depends or --exactness distributive",
        ),
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

    render = _subcommand(
        subparsers,
        "render",
        summary="render a graph as DOT",
        description="Render one graph document in Graphviz DOT notation.",
        examples=("tiergraph render graph.json -o graph.dot",),
    )
    render.set_defaults(handler=_handle_render)
    _document_arguments(render)
    render.add_argument(
        "--include-empty-tiers", action="store_true", help="include empty tiers"
    )

    inspect = _subcommand(
        subparsers,
        "inspect",
        summary="inspect a graph document",
        description="Print graph_summary counts and per-tier and per-relation details.",
        examples=("tiergraph inspect graph.json",),
    )
    inspect.set_defaults(handler=_handle_inspect)
    _document_arguments(inspect)

    convert = _subcommand(
        subparsers,
        "convert",
        summary="canonicalize a graph document",
        description="Validate and rewrite a graph in one canonical output encoding.",
        examples=("tiergraph convert graph.json --to json-compact -o compact.json",),
    )
    convert.set_defaults(handler=_handle_convert)
    _document_arguments(convert)
    convert.add_argument(
        "--to",
        choices=("json", "json-compact", "bytes"),
        required=True,
        help="output encoding",
    )

    schema = _subcommand(
        subparsers,
        "schema",
        summary="print the graph document schema",
        description="Print the JSON Schema or its deterministic shape hash.",
        examples=("tiergraph schema --hash",),
    )
    schema.add_argument(
        "--format-version",
        metavar="VERSION",
        help="format version to request (default: current)",
    )
    schema.set_defaults(handler=_handle_schema)
    schema.add_argument("--hash", action="store_true", help="print the shape hash")
    _output_argument(schema)

    run = _subcommand(
        subparsers,
        "run",
        summary="execute a JSONL machine program",
        description="Execute a JSONL machine program and emit its final graph.",
        examples=("tiergraph run program.jsonl --to json -o graph.json",),
    )
    run.set_defaults(handler=_handle_run)
    _document_arguments(run, input_help="JSONL program file, or - for stdin")
    run.add_argument(
        "--to",
        choices=("json", "json-compact", "bytes", "dot"),
        required=True,
        help="final graph encoding",
    )
    run.add_argument(
        "--include-empty-tiers",
        action="store_true",
        help="include empty tiers in DOT output",
    )

    step = _subcommand(
        subparsers,
        "step",
        summary="step through a JSONL machine program",
        description="Emit each machine step or enter the interactive debugger.",
        examples=("tiergraph step program.jsonl -o steps.jsonl",),
    )
    step.set_defaults(handler=_handle_step)
    _document_arguments(step, input_help="JSONL program file, or - for stdin")
    step.add_argument(
        "--interactive",
        action="store_true",
        help="use the interactive debugger (also enabled when stdin is a TTY)",
    )

    walk = _subcommand(
        subparsers,
        "walk",
        summary="traverse a transitive relation",
        description="Traverse one declared acyclic relation from one or more paths.",
        examples=(
            "tiergraph walk walk.json --source "
            "/items/structural/urn:test:traversal/nodes/0 "
            "--relation-namespace urn:test:traversal --relation-local contains",
        ),
    )
    walk.set_defaults(handler=_handle_walk)
    walk.add_argument("file", metavar="GRAPH", help="graph file, or - for stdin")
    walk.add_argument(
        "--source",
        action="append",
        required=True,
        metavar="PATH",
        help="source TG-PATH; repeatable",
    )
    walk.add_argument(
        "--relation-namespace",
        required=True,
        metavar="NS",
        help="relation namespace URI",
    )
    walk.add_argument(
        "--relation-local",
        required=True,
        metavar="LOCAL",
        help="relation local name",
    )
    walk.add_argument(
        "--direction",
        choices=("forward", "inverse"),
        default="forward",
        help="traversal direction (default: forward)",
    )
    walk.add_argument("--cap", type=int, metavar="N", help="maximum traversal depth")
    _output_argument(walk)

    path = _subcommand(
        subparsers,
        "path",
        summary="resolve and spell tiergraph paths",
        description="Resolve TG-PATH text or spell a structural or durable path.",
        examples=("tiergraph path resolve graph.json /items/durable/alpha",),
    )
    path_subparsers = path.add_subparsers(dest="path_command", required=True)
    resolve = _subcommand(
        path_subparsers,
        "resolve",
        summary="resolve a tiergraph path",
        description="Resolve one TG-PATH against a graph and print its binding.",
        examples=("tiergraph path resolve graph.json /items/durable/alpha",),
    )
    resolve.set_defaults(handler=_handle_path)
    resolve.add_argument("file", metavar="GRAPH", help="graph file, or - for stdin")
    resolve.add_argument("tgpath", metavar="TGPATH", help="tiergraph path to resolve")
    resolve.add_argument(
        "--profile", metavar="FILE", help="declarative grammar chart profile"
    )
    _output_argument(resolve)

    spell = _subcommand(
        path_subparsers,
        "spell",
        summary="spell a tiergraph path",
        description="Spell a durable or structural TG-PATH for an item or boundary.",
        examples=("tiergraph path spell graph.json --kind item --durable-id alpha",),
    )
    spell.set_defaults(handler=_handle_path)
    spell.add_argument("file", metavar="GRAPH", help="graph file, or - for stdin")
    spell.add_argument(
        "--kind",
        choices=("item", "boundary"),
        required=True,
        help="kind of graph position to spell",
    )
    spell.add_argument("--tier-namespace", metavar="NS", help="tier namespace URI")
    spell.add_argument("--tier-local", metavar="LOCAL", help="tier local name")
    spell.add_argument("--index", type=int, metavar="N", help="structural index")
    spell.add_argument("--durable-id", metavar="ID", help="durable item identifier")
    spell.add_argument(
        "--anchor-item-id", metavar="ID", help="durable boundary anchor item"
    )
    spell.add_argument(
        "--anchor-tier-namespace",
        metavar="NS",
        help="durable boundary anchor tier namespace URI",
    )
    spell.add_argument(
        "--anchor-tier-local",
        metavar="LOCAL",
        help="durable boundary anchor tier local name",
    )
    spell.add_argument(
        "--side", choices=("before", "after"), help="side of the anchor item"
    )
    _output_argument(spell)

    grammar = _subcommand(
        subparsers,
        "grammar",
        summary="work with tiergraph grammars",
        description="Recognize, count, rank, or generate with a grammar document.",
        examples=("tiergraph grammar recognize grammar.json --tokens-json '[\"x\"]'",),
        details="JSON formats: see docs/reference/cli.md#json-input-formats.\n\n",
    )
    grammar_subparsers = grammar.add_subparsers(dest="grammar_command", required=True)
    for grammar_command, help_text in (
        ("recognize", "recognize a token sequence"),
        ("count", "count token-sequence derivations"),
        ("best", "find best token-sequence derivations"),
    ):
        grammar_parser = _subcommand(
            grammar_subparsers,
            grammar_command,
            summary=help_text,
            description=f"{help_text.capitalize()} with one grammar declaration.",
            examples=(
                f"tiergraph grammar {grammar_command} grammar.json "
                "--tokens-json '[\"x\"]'",
            ),
            details=(
                "--tokens-json is a JSON array of strings. Grammar document fields "
                "are in docs/reference/cli.md#json-input-formats.\n\n"
            ),
        )
        grammar_parser.set_defaults(handler=_handle_grammar)
        grammar_parser.add_argument(
            "file", metavar="GRAMMAR", help="grammar JSON file, or - for stdin"
        )
        grammar_parser.add_argument(
            "--tokens-json",
            required=True,
            metavar="JSON",
            help="JSON array of source token strings",
        )
        _max_steps_argument(grammar_parser)
        if grammar_command == "recognize":
            grammar_parser.add_argument(
                "--forest",
                action="store_true",
                help="emit the complete parse forest",
            )
        if grammar_command == "best":
            grammar_parser.add_argument(
                "--count",
                type=int,
                default=1,
                metavar="N",
                help="maximum derivations to emit (default: 1)",
            )
        _output_argument(grammar_parser)
    for grammar_command, help_text in (
        ("generate", "generate experimental target derivations"),
        ("lattice", "emit an experimental target lattice"),
    ):
        grammar_parser = _subcommand(
            grammar_subparsers,
            grammar_command,
            summary=help_text,
            description=f"{help_text.capitalize()} from typed grammar input.",
            examples=(
                f"tiergraph grammar {grammar_command} grammar.json --input-json "
                '\'{"tokens":[{"symbol":"x","realization":['
                '{"tokens":["x"]}],"span":{"partition":null,'
                '"origin":0,"end":1}}]}\'',
            ),
            details=(
                "--input-json is a typed GrammarInput object. Its fields and the "
                "grammar document fields are in "
                "docs/reference/cli.md#json-input-formats.\n\n"
            ),
        )
        grammar_parser.set_defaults(handler=_handle_grammar)
        grammar_parser.add_argument(
            "file", metavar="GRAMMAR", help="grammar JSON file, or - for stdin"
        )
        grammar_parser.add_argument(
            "--input-json",
            required=True,
            metavar="INPUT",
            help="typed grammar input as JSON",
        )
        if grammar_command == "generate":
            grammar_parser.add_argument(
                "--count",
                type=int,
                default=1,
                metavar="N",
                help="maximum target derivations to emit",
            )
            _max_steps_argument(grammar_parser)
        _output_argument(grammar_parser)

    clock = _subcommand(
        subparsers,
        "clock",
        summary="query declarative clock timing",
        description="Query structural and physical time through a clock profile.",
        examples=(
            "tiergraph clock coordinates clock.json --profile clock-profile.json",
        ),
        details="Profile fields are in docs/reference/cli.md#json-input-formats.\n\n",
    )
    clock_subparsers = clock.add_subparsers(dest="clock_command", required=True)
    for clock_command, help_text in (
        ("coordinates", "list refined clock coordinates"),
        ("boundary", "query one tier boundary"),
        ("extent", "query a timed tier extent"),
        ("item", "query one timed item"),
    ):
        clock_examples = {
            "coordinates": (
                "tiergraph clock coordinates clock.json --profile clock-profile.json"
            ),
            "boundary": (
                "tiergraph clock boundary clock.json --profile clock-profile.json "
                "--boundary "
                "/positions/structural/urn:tiergraph:profile:clock:test/segment/1"
            ),
            "extent": (
                "tiergraph clock extent clock.json --profile clock-profile.json "
                "--tier-namespace urn:tiergraph:profile:clock:test "
                "--tier-local segment"
            ),
            "item": (
                "tiergraph clock item clock.json --profile clock-profile.json "
                "--item /items/structural/urn:tiergraph:profile:clock:test/segment/1"
            ),
        }
        clock_parser = _subcommand(
            clock_subparsers,
            clock_command,
            summary=help_text,
            description=f"{help_text.capitalize()} through a clock profile.",
            examples=(clock_examples[clock_command],),
            details="Profile fields are in docs/reference/cli.md#json-input-formats.\n\n",
        )
        clock_parser.set_defaults(handler=_handle_clock)
        clock_parser.add_argument(
            "file", metavar="GRAPH", help="graph file, or - for stdin"
        )
        clock_parser.add_argument(
            "--profile",
            required=True,
            metavar="FILE",
            help="clock profile JSON file, or - for stdin",
        )
        if clock_command == "boundary":
            clock_parser.add_argument(
                "--boundary",
                required=True,
                metavar="PATH",
                help="boundary TG-PATH to query",
            )
        elif clock_command == "extent":
            clock_parser.add_argument(
                "--tier-namespace",
                required=True,
                metavar="NS",
                help="timed tier namespace URI",
            )
            clock_parser.add_argument(
                "--tier-local",
                required=True,
                metavar="LOCAL",
                help="timed tier local name",
            )
        elif clock_command == "item":
            clock_parser.add_argument(
                "--item", required=True, metavar="PATH", help="item TG-PATH to query"
            )
        _output_argument(clock_parser)

    span = _subcommand(
        subparsers,
        "span",
        summary="render declarative span views",
        description="Render span-oriented projections selected by a profile.",
        examples=(
            "tiergraph span render spans.json --profile span-profile.json --format text",
        ),
        details="Profile fields are in docs/reference/cli.md#json-input-formats.\n\n",
    )
    span_subparsers = span.add_subparsers(dest="span_command", required=True)
    span_render = _subcommand(
        span_subparsers,
        "render",
        summary="render a span view",
        description="Render one declarative span view in the selected format.",
        examples=(
            "tiergraph span render spans.json --profile span-profile.json --format text",
        ),
        details="Profile fields are in docs/reference/cli.md#json-input-formats.\n\n",
    )
    span_render.set_defaults(handler=_handle_span)
    span_render.add_argument("file", metavar="GRAPH", help="graph file, or - for stdin")
    span_render.add_argument(
        "--profile",
        required=True,
        metavar="FILE",
        help="span profile JSON file, or - for stdin",
    )
    span_render.add_argument(
        "--format",
        choices=("text", "json", "jsonl", "html", "dot", "textgrid"),
        required=True,
        help="output format",
    )
    span_render.add_argument(
        "--alternatives",
        action="store_true",
        help="include alternative span readings where supported",
    )
    span_render.add_argument(
        "--jsonl-record",
        choices=("input", "span"),
        default=None,
        help="JSONL record unit; requires --format jsonl",
    )
    span_render.add_argument(
        "--include-empty-tiers",
        action="store_true",
        help="include empty tiers; requires --format dot",
    )
    _output_argument(span_render)

    selection = _subcommand(
        subparsers,
        "select",
        summary="evaluate a selector",
        description="Evaluate selector JSON or a value predicate against a graph.",
        examples=("tiergraph select graph.json --selector selector.json",),
        details="Selector JSON fields are in docs/reference/cli.md#json-input-formats.\n\n",
    )
    selection.set_defaults(handler=_handle_select)
    selection.add_argument("file", metavar="GRAPH", help="graph file, or - for stdin")
    selection_input = selection.add_mutually_exclusive_group(required=True)
    selection_input.add_argument(
        "--selector", metavar="FILE", help="strict selector JSON file, or - for stdin"
    )
    selection_input.add_argument(
        "--where", metavar="TEXT", help="value predicate applied to every item"
    )
    selection.add_argument(
        "--prefix",
        metavar="P",
        help="default graph prefix for unqualified --where attributes",
    )
    _max_steps_argument(selection)
    _output_argument(selection)

    match = _subcommand(
        subparsers,
        "match",
        summary="match a regular item sequence",
        description="Evaluate a regular item pattern over one declared ordering.",
        examples=(
            "tiergraph match graph.json --pattern . --ordering "
            '\'{"order":"tier","tier":{"namespace":"urn:path",'
            '"local_name":"tokens"}}\' exists',
        ),
        details=(
            "Pattern text: '.' matches one item; '{predicate}' tests one item; "
            "'( )' groups; '|' or '/' alternates; '*', '+', '?', and '{n}' repeat; "
            "'^' and '$' anchor; '_' marks focus. Request and ordering JSON fields "
            "are in docs/reference/cli.md#json-input-formats.\n\n"
        ),
    )
    match.set_defaults(handler=_handle_match)
    match.add_argument("file", metavar="GRAPH", help="graph file, or - for stdin")
    match_input = match.add_mutually_exclusive_group(required=True)
    match_input.add_argument(
        "--request",
        metavar="FILE",
        help="strict match request JSON file, or - for stdin",
    )
    match_input.add_argument(
        "--pattern", metavar="TEXT", help="regular sequence pattern text"
    )
    match.add_argument(
        "--ordering",
        metavar="JSON",
        help="ordering JSON object for --pattern",
    )
    match.add_argument(
        "--prefix",
        metavar="P",
        help="default graph prefix for unqualified pattern attributes",
    )
    match.add_argument(
        "--limit", type=int, help="maximum spans to emit; valid only for spans"
    )
    _max_steps_argument(match)
    match.add_argument(
        "match_operation",
        nargs="?",
        choices=("exists", "focus", "spans", "count"),
        help="view to evaluate with --pattern",
    )
    _output_argument(match)

    fold = _subcommand(
        subparsers,
        "fold",
        summary="fold a dependency relation",
        description="Evaluate a finite dependency relation with a named semiring.",
        examples=(
            "tiergraph fold fold.json --attribute-namespace urn:test:fold "
            "--attribute-local cost --tier urn:test:fold tasks --semiring counting "
            "--lift one --transition urn:test:fold depends or",
        ),
        details=(
            "Fold requests are assembled from these flags; fold does not read a "
            "separate request JSON document.\n\n"
        ),
    )
    fold.set_defaults(handler=_handle_fold, exactness=None)
    _fold_arguments(fold)
    _output_argument(fold)

    semirings = _subcommand(
        subparsers,
        "semirings",
        summary="list the semirings this shell can name",
        description="List the named semirings accepted by fold commands.",
        examples=("tiergraph semirings",),
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


def _max_steps_argument(parser: argparse.ArgumentParser) -> None:
    """Add the opt-in deterministic work guard used by evaluating commands."""
    parser.add_argument(
        "--max-steps",
        type=_positive_step_count,
        metavar="N",
        help=(f"refuse after N deterministic work steps (maximum: {_MAX_USER_STEPS})"),
    )


def _positive_step_count(value: str) -> int:
    """Decode one bounded positive CLI step count for argparse."""
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    if parsed > _MAX_USER_STEPS:
        raise argparse.ArgumentTypeError(f"must be no greater than {_MAX_USER_STEPS}")
    return parsed


def _work_budget(args: argparse.Namespace) -> tiergraph.WorkBudget | None:
    """Build the explicitly requested budget, leaving omission as exactly None."""
    return (
        None if args.max_steps is None else tiergraph.WorkBudget(steps=args.max_steps)
    )


def _graph_pair_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the inputs a declaration over two graph values is assembled from.

    ``discharge seals`` and ``discharge rewrite`` read the same pair -- a source
    document, the result claiming something about it, and a name that exists so
    a refusal can say whose claim failed -- so they name those inputs once here
    and cannot drift into spelling one pair two ways. What separates them is the
    claim laid over the pair, which each parser adds for itself.
    """
    parser.add_argument(
        "file", metavar="SOURCE", help="source graph file, or - for stdin"
    )
    parser.add_argument(
        "--result", required=True, metavar="FILE", help="result graph file"
    )
    parser.add_argument(
        "--name", default="rewrite", metavar="NAME", help="name used in refusals"
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
    parser.add_argument(
        "--attribute-namespace",
        required=True,
        metavar="NS",
        help="valuation attribute namespace URI",
    )
    parser.add_argument(
        "--attribute-local",
        required=True,
        metavar="LOCAL",
        help="valuation attribute local name",
    )
    parser.add_argument(
        "--tier",
        action="append",
        nargs=2,
        required=True,
        metavar=("NS", "LOCAL"),
        help="one valuation domain tier; repeatable",
    )
    parser.add_argument(
        "--semiring",
        choices=tuple(_SEMIRINGS),
        required=True,
        help="named algebra used by the fold",
    )
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
    _max_steps_argument(parser)


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


def _discharge_rewrite(args: argparse.Namespace) -> object:
    """Demand a rewrite's effect claim against the pair of graphs it read.

    The pair is the one ``seals`` reads and the flags are the same, because both
    declarations are about two graph values and neither asks how the second was
    produced. What this adds is ``--effect``, and it is optional for the reason
    ``--exactness`` is: leaving it off is not a usage error but reaches the
    library's own refusal, which hands back the declaration to be made rather
    than standing in the weaker claim on the caller's behalf.
    """
    _check_distinct(args.result, args.output)
    source = tiergraph.loads(_read_bytes(args.file))
    result = tiergraph.loads(_read_bytes(args.result))
    effect = (
        tiergraph.RewriteEffect.UNDECLARED
        if args.effect is None
        else tiergraph.RewriteEffect(args.effect)
    )
    declaration = tiergraph.RewriteDeclaration(args.name, source, result, effect)
    return declaration.check_effect().to_data()


def _discharge_fold(args: argparse.Namespace) -> object:
    """Demand a fold's exactness claim against the graph and valuation it reads.

    The declaration is the one ``fold`` already assembles from the same flags,
    with the claim added, so the two commands cannot drift into describing
    different folds. ``--exactness`` is optional on purpose: leaving it off is
    not a usage error but reaches the library's own refusal, which hands back
    the declaration to be made rather than quietly standing in the weaker claim.
    """
    declaration = _fold_declaration(tiergraph.loads(_read_bytes(args.file)), args)
    return declaration.check_exactness(budget=_work_budget(args)).to_data(
        declaration.semiring
    )


# One entry per capability this verb carries. The four declaration kinds this
# package publishes take genuinely different inputs -- a pair of graphs for
# seals and for a rewrite effect, a whole valuation for a fold, a graph and a
# role binding for a profile -- so the dispatch is a table of handlers rather
# than one shared shape they would all have to be bent into. Another capability
# is one subparser naming its inputs and one entry here returning its
# certificate's ``to_data()``.
#
# One kind is absent because that last clause is what it cannot supply, and the
# absence is the library's shape rather than an omission here. A profile's check
# returns nothing to certify, and ``ProfileRegistry.report`` answers a failing
# check with an accepting report carrying a refused outcome, which is precisely
# the artifact ``discharge`` promises never to write under the name of a
# certificate.
_DISCHARGES: dict[str, Callable[[argparse.Namespace], object]] = {
    "fold": _discharge_fold,
    "rewrite": _discharge_rewrite,
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
    except (tiergraph.EffectRefusal, tiergraph.ExactnessRefusal) as error:
        _refusal_diagnostic(args.command, error)
        return 1
    _write_output(args.file, args.output, _json_bytes(certificate))
    return 0


def _refusal_diagnostic(
    command: str,
    error: Refusal | tiergraph.EffectRefusal | tiergraph.ExactnessRefusal,
) -> None:
    """Report one discharge refusal as a diagnostic line and as data beside it.

    The line keeps this command's stderr readable the way every other command's
    is; the object after it carries the stage, which a caller acts on and must
    not have to recover by matching the wording. ``step`` already writes its
    extra refusal detail to stderr after the same diagnostic line, so this is
    the shape a reader of this CLI's failures already meets.

    Effect and exactness refusals carry their subsystem's offender in the
    message but declare no document-reader stage. Their object therefore carries
    the message without inventing a stage or rank the API does not expose.
    """
    _diagnostic(command, type(error).__name__, error)
    data = (
        _refusal_data(error) if isinstance(error, Refusal) else {"message": str(error)}
    )
    sys.stderr.write(_json_text({"refusal": data}))


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
    value: dict[str, object]
    if args.path_command == "resolve":
        profile: tiergraph.PathProfile = (
            tiergraph.StructuralPathProfile()
            if args.profile is None
            else tiergraph.GrammarChartProfile.from_data(
                graph, _profile_json(args.profile)
            )
        )
        if args.profile is not None:
            _check_distinct(args.profile, args.output)
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
            if not isinstance(  # pragma: no cover - chart alternatives are items
                resolved.value, tiergraph.ItemRef
            ):
                raise ValueError("path profile returned an unsupported alternative")
            value = {
                "kind": "alternative",
                "path": str(resolved.path),
                "owner": resolved.owner.to_data(),
                "relation": resolved.relation.to_data(),
                "index": resolved.index,
                "value": resolved.value.to_data(),
            }
    else:
        profile = tiergraph.StructuralPathProfile()
        value = {"path": str(profile.spell(_path_binding(args, profile, graph), graph))}
    _write_output(args.file, args.output, _json_bytes(value))


def _handle_grammar(args: argparse.Namespace) -> None:
    declaration = tiergraph.grammar_loads(_read_bytes(args.file))
    lowered = tiergraph.lower_grammar(declaration)
    if args.grammar_command in {"generate", "lattice"}:
        grammar_input = tiergraph.GrammarInput.from_data(
            _wire._parsed_json(args.input_json)
        )
        if args.grammar_command == "generate":
            with _metered(_work_budget(args), "grammar.generate"):
                value: object = tiergraph.generate(
                    lowered, grammar_input, count=args.count
                ).to_data()
        else:
            forest = tiergraph.recognize(lowered, grammar_input, collapse_units=False)
            value = tiergraph.target_lattice(forest).to_data()
        _write_output(args.file, args.output, _json_bytes(value))
        return
    tokens = _tokens_json(args.tokens_json)
    with _metered(_work_budget(args), f"grammar.{args.grammar_command}"):
        if args.grammar_command == "recognize":
            forest = tiergraph.recognize(lowered, tokens)
            value = (
                forest.to_data() if args.forest else {"recognized": forest.recognized()}
            )
        elif args.grammar_command == "count":
            value = {"count": tiergraph.count(lowered, tokens)}
        else:
            if args.count < 1:
                raise ValueError(
                    f"best derivation count {args.count!r} must be positive"
                )
            value = {
                "derivations": [
                    item.to_data()
                    for item in tiergraph.best(lowered, tokens, args.count)
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
    budget = _work_budget(args)
    if args.where is not None:
        with _metered(budget, "selection.evaluate"):
            syntax = _predicate.PredicateSyntax.for_graph(
                graph, default_prefix=args.prefix
            )
            predicate = _predicate.parse_predicate(args.where, syntax)
            candidates = tiergraph.NodeSet(
                graph,
                tuple(
                    tiergraph.Node(tiergraph.NodeKind.ITEM, reference)
                    for reference in graph.canonical_items()
                ),
            )
            result = (
                _predicate.compile_predicate(predicate).bind(graph).select(candidates)
            )
    else:
        selector = tiergraph.selection_loads(_read_bytes(args.selector))
        _check_distinct(args.selector, args.output)
        result = tiergraph.evaluate_selection(graph, selector, budget=budget)
    _write_output(args.file, args.output, _json_bytes({"nodes": result.to_data()}))


def _handle_match(args: argparse.Namespace) -> None:
    graph = tiergraph.loads(_read_bytes(args.file))
    if args.request is not None:
        if any(
            value is not None
            for value in (args.ordering, args.match_operation, args.prefix, args.limit)
        ):
            raise ValueError("--request does not take text-pattern options")
        _check_distinct(args.request, args.output)
        result = _match._evaluate_match_request(
            graph, _read_bytes(args.request), max_steps=args.max_steps
        )
    else:
        if args.ordering is None or args.match_operation is None:
            raise ValueError("--pattern requires --ordering and an operation")
        if args.limit is not None and args.match_operation != "spans":
            raise ValueError("--limit is available only for spans")
        syntax = _predicate.PredicateSyntax.for_graph(graph, default_prefix=args.prefix)
        request = _match._MatchRequest(
            args.match_operation,
            _match._decode_ordering(
                cast(_core.JsonValue, json.loads(args.ordering)), "$.ordering"
            ),
            _match.parse_pattern(args.pattern, syntax),
            args.limit,
        )
        result = request.evaluate(graph, budget=_work_budget(args))
    _write_output(args.file, args.output, _json_bytes(result))


def _handle_fold(args: argparse.Namespace) -> None:
    graph = tiergraph.loads(_read_bytes(args.file))
    fold = _fold_declaration(graph, args)
    _write_output(
        args.file,
        args.output,
        _json_bytes(fold.run(budget=_work_budget(args)).to_data(fold.semiring)),
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
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    operations = {"exists", "focus", "spans", "count"}
    if (
        len(arguments) >= _MATCH_MIN_ARGS
        and arguments[0] == "match"
        and arguments[-1] in operations
        and arguments[2] not in operations
        and (arguments[1] == "-" or not arguments[1].startswith("-"))
    ):
        arguments = (
            arguments[0],
            arguments[1],
            arguments[-1],
            *arguments[2:-1],
        )
    parser = build_parser()
    args = parser.parse_args(arguments)
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
    except ArithmeticError as error:
        # An algebra refuses a combination that leaves its carrier, and it does
        # so with the interpreter's class for that condition rather than this
        # package's: `DoubleExtremumSemiring.multiply` raises `OverflowError`,
        # which is an `ArithmeticError` and so no kind of `ValueError`. A
        # well-formed graph and a valid command line therefore reached a stack
        # trace where `docs/reference/cli.md` promises a diagnostic on stderr
        # and status 1 for a refused operation.
        #
        # It is reported as the house refusal for the same reason `_fold_lift`
        # converts a carrier mismatch into one rather than letting a `TypeError`
        # escape from inside the fold: what the caller met is their valuation
        # leaving the algebra's carrier, which is a refused operation, and a
        # caller routing on the class should not have to know which of the two
        # the arithmetic happened to raise. Catching the base class covers the
        # whole family rather than the one member reached today, and it sits
        # after `RecursionError` so no earlier clause is displaced.
        _diagnostic(args.command, "ValueError", error)
        return 1
    except tiergraph.PathRefusal as error:
        _diagnostic(args.command, "PathRefusal", error)
        return 1
    except tiergraph.BudgetExhausted as error:
        _refusal_diagnostic(args.command, error)
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
    if args.format == "textgrid":
        if args.alternatives:
            raise ValueError("--alternatives is unavailable for --format textgrid")
        return tiergraph.to_textgrid(graph, profile)
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
