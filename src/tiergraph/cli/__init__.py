"""Command line entry point, a thin shell over the public API."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any, BinaryIO, cast
from urllib.parse import unquote, urlsplit

import tiergraph
import tiergraph_dot
from tiergraph import ExecutionError, Program, Step, load_program, semiring
from tiergraph import core as _core
from tiergraph import machine as _machine
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
_ASCII_SPACE = 0x20
_ASCII_DELETE = 0x7F
_SHA256_TEXT = re.compile(r"[0-9a-f]{64}")
_MEDIA_TYPE_TEXT = re.compile(
    r"[a-z0-9][a-z0-9!#$&^_.+\-]{0,126}/"
    r"[a-z0-9][a-z0-9!#$&^_.+\-]{0,126}"
)
_BAD_PERCENT_ESCAPE = re.compile(r"%(?![0-9a-fA-F]{2})")

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
    validate.add_argument(
        "--profile",
        choices=("blob",),
        help="also validate the named opt-in profile without opening payloads",
    )

    blob = _subcommand(
        subparsers,
        "blob",
        summary="inspect and move external-resource payloads",
        description="List, store, retrieve, or verify typed external resources.",
        examples=("tiergraph blob list graph.json",),
    )
    blob_subparsers = blob.add_subparsers(dest="blob_command", required=True)
    blob_list = _subcommand(
        blob_subparsers,
        "list",
        summary="list declared external resources",
        description="List blob items in declared graph order without opening payloads.",
        examples=("tiergraph blob list graph.json --json",),
    )
    blob_list.set_defaults(handler=_handle_blob)
    blob_list.add_argument("file", metavar="GRAPH|BUNDLE", help="graph or bundle file")
    blob_list.add_argument("--json", action="store_true", help="emit structured JSON")

    blob_put = _subcommand(
        blob_subparsers,
        "put",
        summary="put a payload in a content-addressed directory",
        description="Hash one payload and store it under its verified SHA-256 identity.",
        examples=("tiergraph blob put audio.wav --store media",),
    )
    blob_put.set_defaults(handler=_handle_blob)
    blob_put.add_argument("file", metavar="FILE", help="payload file")
    blob_put.add_argument("--store", required=True, metavar="DIR", help="store root")
    blob_put.add_argument(
        "--media-type",
        type=_media_type_argument,
        metavar="TYPE",
        help="lowercase media type to include in the descriptor report",
    )

    blob_get = _subcommand(
        blob_subparsers,
        "get",
        summary="retrieve one verified payload",
        description="Write one declared payload after verifying its size and digest.",
        examples=(
            "tiergraph blob get graph.tgb eb4e7e48106279b6d5823a05900c397c"
            "60724d32288c3d0ea99de22d6cf410d7 -o payload.bin",
        ),
    )
    blob_get.set_defaults(handler=_handle_blob)
    blob_get.add_argument("file", metavar="GRAPH|BUNDLE", help="graph or bundle file")
    blob_get.add_argument("sha256", metavar="SHA256", help="payload SHA-256 digest")
    blob_get.add_argument(
        "--store", metavar="DIR", help="explicit linked-payload store"
    )
    blob_get.add_argument(
        "-o",
        "--output",
        required=True,
        type=_file_output_argument,
        metavar="FILE",
        help="output payload file",
    )

    blob_verify = _subcommand(
        blob_subparsers,
        "verify",
        summary="verify every required payload",
        description="Read and verify every distinct payload in declared graph order.",
        examples=("tiergraph blob verify graph.tgb --store media",),
    )
    blob_verify.set_defaults(handler=_handle_blob)
    blob_verify.add_argument(
        "file", metavar="GRAPH|BUNDLE", help="graph or bundle file"
    )
    blob_verify.add_argument(
        "--store", metavar="DIR", help="explicit linked-payload store"
    )

    bundle = _subcommand(
        subparsers,
        "bundle",
        summary="build and inspect external-resource bundles",
        description="Build deterministic mixed-residency bundles or inspect their index.",
        examples=("tiergraph bundle inspect graph.tgb",),
    )
    bundle_subparsers = bundle.add_subparsers(dest="bundle_command", required=True)
    flatten = _subcommand(
        bundle_subparsers,
        "flatten",
        summary="embed selected payloads in a bundle",
        description="Embed all payloads, or exactly the digests named by --only.",
        examples=("tiergraph bundle flatten graph.json --store media -o graph.tgb",),
    )
    flatten.set_defaults(handler=_handle_bundle)
    flatten.add_argument("file", metavar="GRAPH|BUNDLE", help="graph or bundle file")
    flatten.add_argument("--store", required=True, metavar="DIR", help="store root")
    flatten.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="SHA256",
        help="embed this digest; repeatable (default: all)",
    )
    flatten.add_argument(
        "-o",
        "--output",
        required=True,
        type=_file_output_argument,
        metavar="BUNDLE",
        help="output bundle",
    )

    unflatten = _subcommand(
        bundle_subparsers,
        "unflatten",
        summary="move selected payloads to a directory store",
        description="Externalize all embedded payloads, or those named by --only.",
        examples=("tiergraph bundle unflatten graph.tgb --store media -o linked.tgb",),
    )
    unflatten.set_defaults(handler=_handle_bundle)
    unflatten.add_argument("file", metavar="BUNDLE", help="bundle file")
    unflatten.add_argument("--store", required=True, metavar="DIR", help="store root")
    unflatten.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="SHA256",
        help="externalize this digest; repeatable (default: all)",
    )
    unflatten.add_argument(
        "-o",
        "--output",
        required=True,
        type=_file_output_argument,
        metavar="BUNDLE",
        help="output bundle",
    )

    bundle_inspect = _subcommand(
        bundle_subparsers,
        "inspect",
        summary="inspect a bundle index",
        description="Report ordered asset identities and residency without opening payloads.",
        examples=("tiergraph bundle inspect graph.tgb --json",),
    )
    bundle_inspect.set_defaults(handler=_handle_bundle)
    bundle_inspect.add_argument("file", metavar="BUNDLE", help="bundle file")
    bundle_inspect.add_argument(
        "--json", action="store_true", help="emit structured JSON"
    )

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
    _editing_commands(subparsers)
    return parser


def _editing_commands(subparsers: Any) -> None:  # noqa: PLR0915
    """Add the scriptable editing, patch, diff, and program commands."""
    edit = _subcommand(
        subparsers,
        "edit",
        summary="apply one checked graph edit",
        description="Apply one checked edit to a graph document.",
        examples=(
            "tiergraph edit graph.json move /items/durable/alpha --to 1 -o out.json",
            "tiergraph edit graph.json apply --patch change.jsonl --in-place",
        ),
        details=(
            "Item and boundary targets use TG-PATH. Other edit targets use "
            "document, tier:NS|LOCAL, relation-declaration:NS|LOCAL, relation:N, "
            "polyadic:N, relation-id:ID, or polyadic-id:ID. JSON operands use the "
            "public to_data() shapes documented for the Python values.\n\n"
        ),
    )
    edit.add_argument("file", metavar="GRAPH", help="graph file, or - for stdin")
    edit_subparsers = edit.add_subparsers(dest="edit_command", required=True)
    examples = {
        "apply": "tiergraph edit graph.json apply --patch change.jsonl -o out.json",
        "insert": "tiergraph edit graph.json insert --tier urn:path tokens --at 1 --item item.json -o out.json",
        "delete": "tiergraph edit graph.json delete /items/durable/gamma -o out.json",
        "replace": "tiergraph edit graph.json replace /items/durable/alpha --item replacement-item.json -o out.json",
        "move": "tiergraph edit graph.json move /items/durable/alpha --to 1 -o out.json",
        "swap": "tiergraph edit graph.json swap /items/durable/alpha /items/durable/beta -o out.json",
        "relate": "tiergraph edit graph.json relate --instance relation.json -o out.json",
        "unrelate": "tiergraph edit graph.json unrelate relation:0 -o out.json",
        "endpoints": "tiergraph edit graph.json endpoints relation:0 --sources sources.json --targets targets.json -o out.json",
        "feature": "tiergraph edit graph.json feature set /items/durable/alpha --attribute attribute.json -o out.json",
        "declare": "tiergraph edit graph.json declare --declaration declaration.json -o out.json",
        "undeclare": "tiergraph edit graph.json undeclare --prefix extra -o out.json",
        "promote": "tiergraph edit graph.json promote relation relation:0 --id link-0 -o out.json",
        "demote": "tiergraph edit graph.json demote item /items/durable/alpha -o out.json",
        "seal": "tiergraph edit graph.json seal set urn:path|tokens --sealed 1 -o out.json",
        "layer": "tiergraph edit graph.json layer add --vocabulary urn:path --source hand -o out.json",
        "replace-subtree": "tiergraph edit graph.json replace-subtree /items/durable/alpha --containment urn:path contains --new-graph new-graph.json --new-root /items/durable/alpha -o out.json",
        "swap-subtrees": "tiergraph edit graph.json swap-subtrees /items/durable/alpha /items/durable/beta --containment urn:path contains -o out.json",
        "bulk": "tiergraph edit graph.json bulk --selector selector.json --set-attribute attribute.json -o out.json",
        "compact": "tiergraph edit graph.json compact -o compacted.json",
        "prune-orphans": "tiergraph edit graph.json prune-orphans -o cleaned.json",
    }

    def operation(
        name: str,
        summary: str,
        *,
        example: str | None = None,
        clock_profile: bool = False,
    ) -> argparse.ArgumentParser:
        """Add one graph-producing edit subcommand with shared controls."""
        parser = _subcommand(
            edit_subparsers,
            name,
            summary=summary,
            description=summary.capitalize() + ".",
            examples=(example or examples[name],),
        )
        parser.set_defaults(handler=_handle_edit)
        _edit_output_arguments(
            parser, clock_profile=clock_profile, annotations_require_output=True
        )
        return parser

    apply = operation("apply", "apply a fingerprint-guarded patch")
    apply.add_argument("--patch", required=True, metavar="FILE", help="patch JSONL")

    insert = operation("insert", "insert one item", clock_profile=True)
    insert.add_argument(
        "--tier",
        nargs=2,
        required=True,
        metavar=("NS", "LOCAL"),
        help="destination tier",
    )
    insert.add_argument(
        "--at", required=True, type=int, metavar="N", help="insertion index"
    )
    insert.add_argument("--item", required=True, metavar="FILE", help="item JSON")

    delete = operation("delete", "remove one item or an item run", clock_profile=True)
    delete.add_argument("target", metavar="TGPATH", help="item TG-PATH")
    delete.add_argument(
        "--count", type=int, default=1, metavar="N", help="item count (default: 1)"
    )

    replace_item = operation("replace", "replace one item's stored value")
    replace_item.add_argument("target", metavar="TGPATH", help="item TG-PATH")
    replace_item.add_argument("--item", required=True, metavar="FILE", help="item JSON")

    move = operation("move", "move one item within its tier", clock_profile=True)
    move.add_argument("target", metavar="TGPATH", help="item TG-PATH")
    move.add_argument(
        "--to", required=True, type=int, metavar="N", help="destination index"
    )

    swap = operation("swap", "swap two items", clock_profile=True)
    swap.add_argument("first", metavar="TGPATH", help="first item TG-PATH")
    swap.add_argument("second", metavar="TGPATH", help="second item TG-PATH")

    relate = operation("relate", "insert one relation instance")
    relate.add_argument(
        "--instance", required=True, metavar="FILE", help="relation JSON"
    )
    relate.add_argument("--at", type=int, metavar="N", help="global instance index")

    unrelate = operation("unrelate", "remove one relation instance")
    unrelate.add_argument(
        "target",
        metavar="TARGET",
        help="relation:N, polyadic:N, relation-id:ID, or polyadic-id:ID",
    )

    endpoints = operation(
        "endpoints",
        "replace one relation instance's endpoints",
        clock_profile=True,
    )
    endpoints.add_argument("target", metavar="TARGET", help="relation target")
    endpoints.add_argument(
        "--sources", required=True, metavar="FILE", help="endpoint array JSON"
    )
    endpoints.add_argument(
        "--targets", required=True, metavar="FILE", help="endpoint array JSON"
    )

    feature = operation("feature", "set or remove one typed attribute")
    feature.add_argument("action", choices=("set", "remove"), help="feature operation")
    feature.add_argument(
        "target",
        metavar="TARGET",
        help="TG-PATH, document, qualified declaration, or relation target",
    )
    feature.add_argument("--attribute", metavar="FILE", help="attribute JSON for set")
    feature.add_argument(
        "--name", nargs=2, metavar=("NS", "LOCAL"), help="attribute name for remove"
    )

    declare = operation("declare", "insert one declaration")
    declare.add_argument(
        "--declaration", required=True, metavar="FILE", help="declaration JSON"
    )
    declare.add_argument("--at", type=int, metavar="N", help="declaration index")

    undeclare = operation("undeclare", "remove one declaration", clock_profile=True)
    undeclare_target = undeclare.add_mutually_exclusive_group(required=True)
    undeclare_target.add_argument("--prefix", metavar="P", help="namespace prefix")
    undeclare_target.add_argument(
        "--name", nargs=2, metavar=("NS", "LOCAL"), help="qualified declaration name"
    )
    undeclare.add_argument(
        "--cascade", action="store_true", help="remove dependents first"
    )

    for name in ("promote", "demote"):
        identity = operation(name, f"{name} one durable identity")
        identity.add_argument(
            "kind",
            choices=("item", "boundary", "relation", "polyadic"),
            help="identity carrier kind",
        )
        identity.add_argument(
            "target", metavar="TARGET", help="TG-PATH or relation target"
        )
        if name == "promote":
            identity.add_argument(
                "--id", required=True, metavar="ID", help="new durable identifier"
            )

    seal = operation("seal", "set, shorten, or remove a seal record")
    seal.add_argument(
        "action", choices=("set", "unseal", "drop"), help="seal operation"
    )
    seal.add_argument(
        "carrier", metavar="CARRIER", help="relations, polyadic-relations, or NS|LOCAL"
    )
    seal.add_argument("--sealed", type=int, metavar="N", help="sealed prefix length")

    layer = operation("layer", "add or remove a layer or edit one fact")
    layer.add_argument(
        "action",
        choices=("add", "remove", "put-fact", "remove-fact"),
        help="layer operation",
    )
    layer.add_argument(
        "--vocabulary", required=True, metavar="URI", help="layer vocabulary URI"
    )
    layer.add_argument(
        "--source", required=True, metavar="NAME", help="layer source name"
    )
    layer.add_argument("--fact", metavar="FILE", help="fact JSON for put-fact")
    layer.add_argument(
        "--subject",
        metavar="TARGET",
        help="TG-PATH, document, qualified declaration, or relation target",
    )
    layer.add_argument(
        "--name", nargs=2, metavar=("NS", "LOCAL"), help="fact value name"
    )

    subtree = operation(
        "replace-subtree",
        "replace one containment root's descendants",
        clock_profile=True,
    )
    subtree.add_argument("root", metavar="TGPATH", help="preserved subtree root")
    subtree.add_argument(
        "--containment",
        action="append",
        nargs=2,
        required=True,
        metavar=("NS", "LOCAL"),
        help="containment relation; repeatable",
    )
    subtree.add_argument(
        "--new-graph",
        required=True,
        metavar="FILE",
        help="graph containing the new subtree",
    )
    subtree.add_argument(
        "--new-root", required=True, metavar="TGPATH", help="new subtree root"
    )
    subtree.add_argument("--policies", metavar="FILE", help="replacement policy JSON")

    swap_subtrees = operation("swap-subtrees", "swap two disjoint containment subtrees")
    swap_subtrees.add_argument("first", metavar="TGPATH", help="first subtree root")
    swap_subtrees.add_argument("second", metavar="TGPATH", help="second subtree root")
    swap_subtrees.add_argument(
        "--containment",
        action="append",
        nargs=2,
        required=True,
        metavar=("NS", "LOCAL"),
        help="containment relation; repeatable",
    )
    swap_subtrees.add_argument(
        "--first-policies", metavar="FILE", help="first replacement policy JSON"
    )
    swap_subtrees.add_argument(
        "--second-policies", metavar="FILE", help="second replacement policy JSON"
    )

    bulk = operation("bulk", "apply one edit to a selector or exhaustive match")
    selection = bulk.add_mutually_exclusive_group(required=True)
    selection.add_argument("--selector", metavar="FILE", help="selector JSON")
    selection.add_argument("--where", metavar="TEXT", help="item predicate text")
    selection.add_argument("--match", metavar="PATTERN", help="regular item pattern")
    bulk.add_argument("--ordering", metavar="JSON", help="ordering JSON for --match")
    bulk.add_argument("--prefix", metavar="P", help="default graph prefix")
    bulk_op = bulk.add_mutually_exclusive_group(required=True)
    bulk_op.add_argument(
        "--delete", action="store_true", help="delete selected items or relations"
    )
    bulk_op.add_argument(
        "--set-attribute", metavar="FILE", help="attribute JSON to set"
    )
    bulk_op.add_argument(
        "--remove-attribute",
        nargs=2,
        metavar=("NS", "LOCAL"),
        help="attribute name to remove",
    )

    operation("prune-orphans", "remove orphaned layer facts")
    operation("compact", "prune orphans and compact retained graph values")

    patch = _subcommand(
        subparsers,
        "patch",
        summary="apply, inspect, invert, or compose patches",
        description="Work with versioned fingerprint-guarded patch JSONL.",
        examples=("tiergraph patch show change.jsonl",),
    )
    patch_subparsers = patch.add_subparsers(dest="patch_command", required=True)
    patch_apply = _subcommand(
        patch_subparsers,
        "apply",
        summary="apply a patch",
        description="Apply a patch to its identified base.",
        examples=("tiergraph patch apply change.jsonl graph.json -o out.json",),
    )
    patch_apply.set_defaults(handler=_handle_patch)
    patch_apply.add_argument("patch", metavar="PATCH", help="patch JSONL")
    patch_apply.add_argument("file", metavar="GRAPH", help="base graph")
    _edit_output_arguments(patch_apply, annotations_require_output=True)
    for name, summary in (
        ("show", "show a patch as JSON"),
        ("invert", "write the inverse patch"),
    ):
        command = _subcommand(
            patch_subparsers,
            name,
            summary=summary,
            description=summary.capitalize() + ".",
            examples=(f"tiergraph patch {name} change.jsonl",),
        )
        command.set_defaults(handler=_handle_patch)
        command.add_argument("patch", metavar="PATCH", help="patch JSONL")
        _output_argument(command)
    compose = _subcommand(
        patch_subparsers,
        "compose",
        summary="compose adjacent patches",
        description="Compose patches whose identified fingerprints meet.",
        examples=("tiergraph patch compose first.jsonl second.jsonl -o both.jsonl",),
    )
    compose.set_defaults(handler=_handle_patch)
    compose.add_argument("first", metavar="FIRST", help="first patch JSONL")
    compose.add_argument("second", metavar="SECOND", help="second patch JSONL")
    _output_argument(compose)

    difference = _subcommand(
        subparsers,
        "diff",
        summary="build an executable graph patch",
        description="Build a deterministic patch from one graph toward another.",
        examples=("tiergraph diff before.json after.json -o change.jsonl",),
    )
    difference.set_defaults(handler=_handle_diff)
    difference.add_argument("file", metavar="SOURCE", help="source graph")
    difference.add_argument("target", metavar="TARGET", help="target graph")
    difference.add_argument(
        "--view",
        choices=tuple(member.value for member in tiergraph.EquivalenceView),
        default=tiergraph.EquivalenceView.FUNCTIONAL.value,
        help="comparison view (default: functional)",
    )
    difference.add_argument(
        "--check", action="store_true", help="replay and verify the selected view"
    )
    _output_argument(difference)

    distance = _subcommand(
        subparsers,
        "distance",
        summary="measure exact or bounded graph-edit distance",
        description="Measure graph-edit distance under a declared numeric cost table.",
        examples=(
            "tiergraph distance before.json after.json",
            "tiergraph distance before.json after.json --costs costs.json",
        ),
        details=(
            "Independent ordered tiers can produce an exact value. General and "
            "overlapping relation structures produce a named atom-multiset lower "
            "bound and a realized upper bound. Cost files use the "
            "CostTable.to_data() shape.\n\n"
        ),
    )
    distance.set_defaults(handler=_handle_distance)
    distance.add_argument("file", metavar="SOURCE", help="source graph")
    distance.add_argument("target", metavar="TARGET", help="target graph")
    distance.add_argument(
        "--costs", metavar="FILE", help="numeric cost-table JSON (default: unit)"
    )
    distance.add_argument(
        "--view",
        choices=tuple(member.value for member in tiergraph.EquivalenceView),
        default=tiergraph.EquivalenceView.FUNCTIONAL.value,
        help="comparison view (default: functional)",
    )
    _output_argument(distance)

    program = _subcommand(
        subparsers,
        "program",
        summary="build machine programs",
        description="Build a construction-only machine program.",
        examples=("tiergraph program from-graph graph.json -o program.jsonl",),
    )
    program_subparsers = program.add_subparsers(dest="program_command", required=True)
    from_graph = _subcommand(
        program_subparsers,
        "from-graph",
        summary="encode exact graph construction",
        description="Encode an exact from-empty graph construction program.",
        examples=("tiergraph program from-graph graph.json -o program.jsonl",),
    )
    from_graph.set_defaults(handler=_handle_program)
    from_graph.add_argument("file", metavar="GRAPH", help="graph document")
    _output_argument(from_graph)


def _edit_output_arguments(
    parser: argparse.ArgumentParser,
    *,
    clock_profile: bool = False,
    annotations_require_output: bool = False,
) -> None:
    """Add the controls shared by every command that can produce a graph."""
    parser.set_defaults(clock_profile=None, rebinding=None)
    destination = parser.add_mutually_exclusive_group()
    destination.add_argument(
        "-o",
        "--output",
        default="-",
        metavar="FILE",
        help="output graph (default: -)",
    )
    destination.add_argument(
        "--in-place", action="store_true", help="atomically replace GRAPH"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and report without writing GRAPH",
    )
    parser.add_argument(
        "--report", metavar="FILE", help="write the edit report as JSON"
    )
    parser.add_argument(
        "--record", metavar="FILE", help="write the forward patch as JSONL"
    )
    parser.add_argument(
        "--inverse-out", metavar="FILE", help="write the inverse patch as JSONL"
    )
    if clock_profile:
        parser.add_argument(
            "--clock-profile",
            metavar="FILE",
            help="validate and edit through this clock profile",
        )
        parser.add_argument(
            "--rebinding",
            choices=("keep-earlier", "drop-to-provisional"),
            help="named clock rebinding policy; requires --clock-profile",
        )
    parser.add_argument(
        "--annotate",
        action="append",
        default=[],
        metavar="KEY=JSON",
        help=(
            "attach caller-supplied patch metadata; repeatable; requires --record, "
            "--inverse-out, --report, or --dry-run"
            if annotations_require_output
            else "attach caller-supplied patch metadata; repeatable"
        ),
    )
    _max_steps_argument(parser)


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


def _media_type_argument(value: str) -> str:
    """Require the canonical media-type spelling accepted by the blob profile."""
    if _MEDIA_TYPE_TEXT.fullmatch(value) is None:
        raise argparse.ArgumentTypeError(
            "must be a lowercase type/subtype without parameters"
        )
    return value


def _file_output_argument(value: str) -> str:
    """Require a retractable filesystem destination for verified bytes."""
    if value == "-":
        raise argparse.ArgumentTypeError("must be a file path, not standard output")
    return value


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
    if args.profile == "blob":
        tiergraph.BlobProfile(graph)
    _stdout_text("ok\n")


class _DirectoryResolver:
    """Resolve explicit relative links inside one content-addressed directory."""

    def __init__(self, root: str | Path) -> None:
        """Retain one caller-selected root without searching elsewhere."""
        self._root = Path(root).resolve()

    def open(self, ref: tiergraph.BlobRef, href: str | None) -> BinaryIO | None:
        """Open a safe relative link, or the canonical digest path when absent."""
        target = self._path(ref, href)
        if not target.is_file():
            return None
        return target.open("rb")

    def _path(self, ref: tiergraph.BlobRef, href: str | None) -> Path:
        """Resolve one link under the root and refuse URI or traversal escapes."""
        link = f"sha256/{ref.sha256}" if href is None else href
        if (
            not link
            or any(
                ord(character) <= _ASCII_SPACE or ord(character) >= _ASCII_DELETE
                for character in link
            )
            or _BAD_PERCENT_ESCAPE.search(link) is not None
        ):
            raise ValueError(f"blob href {link!r} is not a safe relative path")
        parsed = urlsplit(link)
        if (
            parsed.scheme
            or parsed.netloc
            or parsed.query
            or parsed.fragment
            or parsed.path.startswith("/")
            or "\\" in parsed.path
        ):
            raise ValueError(f"blob href {link!r} is not a safe relative path")
        try:
            parts = tuple(
                unquote(part, errors="strict") for part in parsed.path.split("/")
            )
        except UnicodeError as error:
            raise ValueError(
                f"blob href {link!r} is not a safe relative path"
            ) from error
        if any(
            part in {"", ".", ".."}
            or "/" in part
            or "\\" in part
            or any(
                ord(character) < _ASCII_SPACE or ord(character) == _ASCII_DELETE
                for character in part
            )
            for part in parts
        ):
            raise ValueError(f"blob href {link!r} is not a safe relative path")
        target = self._root.joinpath(*parts).resolve()
        try:
            target.relative_to(self._root)
        except ValueError as error:
            raise ValueError(f"blob href {link!r} escapes its store") from error
        return target


class _DirectorySink:
    """Publish verified payloads under canonical digest paths in one directory."""

    def __init__(self, root: str | Path) -> None:
        """Retain the selected root; directories are made only by ``put``."""
        self._root = Path(root).resolve()

    def put(self, ref: tiergraph.BlobRef, source: BinaryIO) -> str:
        """Verify and atomically publish one payload, returning its relative link."""
        directory = self._root / "sha256"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / ref.sha256
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{ref.sha256}.", dir=directory
        )
        published = False
        try:
            with os.fdopen(descriptor, "wb") as destination:
                _copy_binary(source, destination)
            with Path(temporary).open("rb") as candidate:
                actual = tiergraph.hash_blob(candidate)
            if actual != ref:
                raise ValueError(
                    f"blob identity mismatch: expected {ref.sha256} ({ref.size} bytes), "
                    f"read {actual.sha256} ({actual.size} bytes)"
                )
            if target.exists():
                with target.open("rb") as existing:
                    stored = tiergraph.hash_blob(existing)
                if stored != ref:
                    raise ValueError(
                        f"blob store path for {ref.sha256} contains different bytes"
                    )
            else:
                os.replace(temporary, target)
                published = True
        finally:
            if not published:
                with suppress(FileNotFoundError):
                    os.unlink(temporary)
        return f"sha256/{ref.sha256}"


class _BundleResolver:
    """Resolve original embedded rows from a bundle and linked rows from a store."""

    def __init__(self, bundle: tiergraph.Bundle, directory: _DirectoryResolver) -> None:
        """Index the first row for each payload without changing declared order."""
        self._bundle = bundle
        self._directory = directory
        self._assets: dict[tiergraph.BlobRef, tiergraph.BundleAsset] = {}
        for asset in bundle.assets():
            self._assets.setdefault(asset.ref, asset)

    def open(self, ref: tiergraph.BlobRef, href: str | None) -> BinaryIO | None:
        """Open the payload from its original residency."""
        asset = self._assets[ref]
        if asset.mode == "embedded":
            return cast(BinaryIO, self._bundle.open_blob(ref))
        return self._directory.open(ref, asset.href if asset.href is not None else href)


@contextmanager
def _blob_input(
    filename: str,
) -> Iterator[tuple[tiergraph.Graph, tiergraph.Bundle | None]]:
    """Open one graph or canonical bundle while retaining its backing stream."""
    with Path(filename).open("rb") as source:
        signature = source.read(4)
        source.seek(0)
        if signature == b"PK\x03\x04":
            with tiergraph.open_bundle(source) as bundle:
                yield bundle.graph, bundle
        else:
            yield tiergraph.loads(source.read()), None


def _copy_binary(source: BinaryIO, destination: BinaryIO) -> None:
    """Copy one binary stream completely, requiring ordinary binary I/O."""
    while True:
        chunk = source.read(1 << 20)
        if not isinstance(chunk, bytes):
            raise TypeError("binary blob source read() must return bytes")
        if not chunk:
            return
        written = destination.write(chunk)
        if written != len(chunk):
            raise OSError("short write while copying blob payload")


def _blob_rows(
    graph: tiergraph.Graph, bundle: tiergraph.Bundle | None
) -> list[dict[str, object]]:
    """Return ordered descriptor rows without resolving any payload."""
    profile = tiergraph.BlobProfile(graph)
    tiers = {tier.declaration.name: tier for tier in graph.tiers}
    assets = None if bundle is None else bundle.assets()
    rows: list[dict[str, object]] = []
    media_name = tiergraph.QualifiedName(tiergraph.BLOB_NAMESPACE, "media-type")
    schema_name = tiergraph.QualifiedName(tiergraph.BLOB_NAMESPACE, "schema")
    for position, (item_ref, blob_ref) in enumerate(profile.blobs()):
        item = tiers[item_ref.tier].items[item_ref.index]
        values = {
            attribute.name: attribute.lexical
            for attribute in item.attributes
            if isinstance(attribute, tiergraph.AttributeValue)
        }
        row: dict[str, object] = {
            "id": cast(str, item.durable_id),
            "position": position,
            "sha256": blob_ref.sha256,
            "size": blob_ref.size,
            "media_type": values[media_name],
            "schema": values.get(schema_name),
            "mode": "linked" if assets is None else assets[position].mode,
            "href": None if assets is None else assets[position].href,
        }
        rows.append(row)
    return rows


def _blob_rows_text(rows: list[dict[str, object]]) -> str:
    """Render ordered descriptors as one stable tab-separated record per item."""
    return "".join(
        "\t".join(
            _text_report_field(row[field])
            for field in (
                "position",
                "id",
                "sha256",
                "size",
                "media_type",
                "schema",
                "mode",
                "href",
            )
        )
        + "\n"
        for row in rows
    )


def _text_report_field(value: object) -> str:
    """Escape string controls so one value cannot create apparent records."""
    if value is None:
        return "-"
    if isinstance(value, str):
        escapes = {"\\": r"\\", "\t": r"\t", "\n": r"\n", "\r": r"\r"}
        return "".join(
            escapes.get(character, f"\\u{ord(character):04x}")
            if ord(character) < _ASCII_SPACE
            or ord(character) == _ASCII_DELETE
            or character in "\x85\u2028\u2029"
            else character
            for character in value
        )
    return str(value)


def _store_resolver(directory: str | None) -> _DirectoryResolver | None:
    """Build only the explicitly requested directory resolver."""
    return None if directory is None else _DirectoryResolver(directory)


def _find_blob(profile: tiergraph.BlobProfile, digest: str) -> tiergraph.BlobRef:
    """Find one required payload by digest and refuse absent or malformed input."""
    if _SHA256_TEXT.fullmatch(digest) is None:
        raise ValueError("blob SHA-256 must be 64 lowercase hexadecimal characters")
    match = next((ref for ref in profile.required() if ref.sha256 == digest), None)
    if match is None:
        raise ValueError(f"graph does not declare blob {digest}")
    return match


class _MissingBlobError(ValueError):
    """Identify an unavailable linked payload without masking content failures."""


def _open_graph_blob(
    bundle: tiergraph.Bundle | None,
    resolver: _DirectoryResolver | None,
    ref: tiergraph.BlobRef,
) -> tiergraph.VerifiedReader:
    """Open one bundle or plain-graph payload through explicit storage only."""
    if bundle is not None:
        asset = next(row for row in bundle.assets() if row.ref == ref)
        if asset.mode == "embedded":
            return bundle.open_blob(ref)
        if resolver is None:
            raise _MissingBlobError(f"linked blob {ref.sha256} requires --store")
        source = resolver.open(ref, asset.href)
        if source is None:
            raise _MissingBlobError(
                f"linked blob {ref.sha256} was not found in the store"
            )
        return tiergraph.VerifiedReader(source, ref)
    if resolver is None:
        raise _MissingBlobError(f"linked blob {ref.sha256} requires --store")
    source = resolver.open(ref, None)
    if source is None:
        raise _MissingBlobError(f"linked blob {ref.sha256} was not found in the store")
    return tiergraph.VerifiedReader(source, ref)


def _verify_blobs(
    profile: tiergraph.BlobProfile,
    bundle: tiergraph.Bundle | None,
    resolver: _DirectoryResolver | None,
) -> None:
    """List every missing payload, then verify available content without fallback."""
    missing: list[str] = []
    for ref in profile.required():
        try:
            reader = _open_graph_blob(bundle, resolver, ref)
        except _MissingBlobError as error:
            missing.append(str(error))
        else:
            reader.close()
    if missing:
        raise ValueError(
            "missing payloads:\n" + "\n".join(f"- {item}" for item in missing)
        )
    for ref in profile.required():
        with _open_graph_blob(bundle, resolver, ref) as reader:
            _copy_binary(cast(BinaryIO, reader), cast(BinaryIO, _NullWriter()))
            if not reader.verified:
                raise ValueError(f"blob {ref.sha256} was not fully verified")


def _handle_blob(args: argparse.Namespace) -> None:
    """Run one external-resource listing, storage, retrieval, or verification."""
    if args.blob_command == "put":
        with Path(args.file).open("rb") as source:
            ref = tiergraph.hash_blob(source)
        with Path(args.file).open("rb") as source:
            href = _DirectorySink(args.store).put(ref, source)
        report: dict[str, object] = {
            "sha256": ref.sha256,
            "size": ref.size,
            "href": href,
        }
        if args.media_type is not None:
            report["media_type"] = args.media_type
        _stdout_text(_json_text(report))
        return
    resolver = _store_resolver(getattr(args, "store", None))
    with _blob_input(args.file) as (graph, bundle):
        if args.blob_command == "list":
            rows = _blob_rows(graph, bundle)
            _stdout_text(
                _json_text({"blobs": rows}) if args.json else _blob_rows_text(rows)
            )
            return
        profile = tiergraph.BlobProfile(graph)
        if args.blob_command == "get":
            ref = _find_blob(profile, args.sha256)
            with _open_graph_blob(bundle, resolver, ref) as reader:
                with _output_stream(args.file, args.output) as destination:
                    _copy_binary(cast(BinaryIO, reader), destination)
                    if not reader.verified:
                        raise ValueError(f"blob {ref.sha256} was not fully verified")
            return
        _verify_blobs(profile, bundle, resolver)
        _stdout_text("ok\n")


class _NullWriter:
    """Accept binary verification reads without retaining payload bytes."""

    def write(self, value: bytes) -> int:
        """Report every byte consumed without storing it."""
        return len(value)


def _selected_refs(
    profile: tiergraph.BlobProfile, requested: Sequence[str]
) -> set[tiergraph.BlobRef]:
    """Resolve a repeatable digest selection, with omission meaning every payload."""
    available = {ref.sha256: ref for ref in profile.required()}
    if not requested:
        return set(available.values())
    selected: set[tiergraph.BlobRef] = set()
    for digest in requested:
        if _SHA256_TEXT.fullmatch(digest) is None:
            raise ValueError(
                "bundle --only SHA-256 must be 64 lowercase hexadecimal characters"
            )
        if digest not in available:
            raise ValueError(f"graph does not declare blob {digest}")
        selected.add(available[digest])
    return selected


def _handle_bundle(args: argparse.Namespace) -> None:
    """Run one deterministic bundle inspection or residency move."""
    with _blob_input(args.file) as (graph, bundle):
        if args.bundle_command == "inspect":
            if bundle is None:
                raise ValueError("bundle inspect requires a bundle input")
            rows = _blob_rows(graph, bundle)
            report = {"bundle_version": tiergraph.BUNDLE_VERSION, "assets": rows}
            rendered = (
                _json_text(report)
                if args.json
                else f"bundle version: {tiergraph.BUNDLE_VERSION}\n{_blob_rows_text(rows)}"
            )
            _stdout_text(rendered)
            return
        if args.bundle_command == "unflatten" and bundle is None:
            raise ValueError("bundle unflatten requires a bundle input")
        profile = tiergraph.BlobProfile(graph)
        selected = _selected_refs(profile, args.only)
        sink = _DirectorySink(args.store)
        with _output_stream(args.file, args.output) as destination:
            if args.bundle_command == "unflatten":
                tiergraph.relink(
                    cast(tiergraph.Bundle, bundle),
                    destination,
                    sink,
                    which=lambda ref: ref in selected,
                )
            elif bundle is None:
                tiergraph.write_bundle(
                    graph,
                    destination,
                    cast(tiergraph.BlobResolver, _DirectoryResolver(args.store)),
                    embed=lambda ref: ref in selected,
                )
            else:
                _flatten_bundle(bundle, destination, args.store, selected, sink)


def _flatten_bundle(
    bundle: tiergraph.Bundle,
    destination: BinaryIO,
    store: str,
    selected: set[tiergraph.BlobRef],
    sink: _DirectorySink,
) -> None:
    """Write exactly the selected embedded set from an existing mixed bundle."""
    _preflight_bundle_destination(destination)
    first_assets: dict[tiergraph.BlobRef, tiergraph.BundleAsset] = {}
    for asset in bundle.assets():
        first_assets.setdefault(asset.ref, asset)
    hrefs = {
        asset.ref: asset.href
        for asset in first_assets.values()
        if asset.mode == "linked" and asset.href is not None
    }
    for ref, asset in first_assets.items():
        if ref not in selected and asset.mode == "embedded":
            with bundle.open_blob(ref) as reader:
                hrefs[ref] = sink.put(ref, cast(BinaryIO, reader))
                if not reader.verified:
                    raise ValueError(f"blob {ref.sha256} was not fully verified")
    resolver = _BundleResolver(bundle, _DirectoryResolver(store))
    tiergraph.write_bundle(
        bundle.graph,
        destination,
        resolver,
        embed=lambda ref: ref in selected,
        hrefs=hrefs,
    )


def _preflight_bundle_destination(destination: BinaryIO) -> None:
    """Refuse a nonempty or nonseekable bundle output before external writes."""
    try:
        if not destination.writable() or not destination.seekable():
            raise ValueError("bundle destination must be seekable and writable")
        position = destination.tell()
        end = destination.seek(0, os.SEEK_END)
        destination.seek(position)
    except (AttributeError, OSError, ValueError) as error:
        raise ValueError(
            "bundle destination must be a seekable binary stream"
        ) from error
    if position != 0 or end != 0:
        raise ValueError("bundle destination must be empty and positioned at byte 0")


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


def _handle_edit(args: argparse.Namespace) -> None:
    """Apply one edit command and emit only fully validated artifacts."""
    _require_annotation_output(args)
    graph = tiergraph.loads(_read_bytes(args.file))
    annotations = _edit_annotations(args.annotate)
    patch: tiergraph.Patch | None
    if args.edit_command == "apply":
        patch = tiergraph.patch_loads(_read_bytes(args.patch))
        _check_distinct(args.patch, args.output)
        patch = _annotated_patch(patch, annotations)
        result = _apply_patch(graph, patch, args)
        if args.dry_run:
            _verify_patch_rollback(graph, result, patch)
        report: object = _patch_report(graph, result, patch, args.dry_run)
    elif args.edit_command == "bulk":
        result, patch, selected_count = _bulk_edit(
            graph,
            args,
            annotations,
            make_patch=_needs_patch(args) or args.dry_run,
        )
        if args.dry_run:
            assert patch is not None
            _verify_patch_rollback(graph, result, patch)
        report = (
            {
                "dry_run": args.dry_run,
                "base_fingerprint": tiergraph.fingerprint(
                    graph, tiergraph.EquivalenceView.IDENTIFIED
                ),
                "target_fingerprint": tiergraph.fingerprint(
                    result, tiergraph.EquivalenceView.IDENTIFIED
                ),
                "operations": selected_count,
                "annotations": annotations.to_data(),
                "selected": selected_count,
            }
            if _needs_report(args)
            else None
        )
    else:
        journal = tiergraph.Journal(annotations) if _needs_journal(args) else None
        editor: Any
        if args.clock_profile is None:
            if args.rebinding is not None:
                raise ValueError("--rebinding requires --clock-profile")
            editor = graph.edit() if journal is None else graph.edit(journal=journal)
        else:
            if args.rebinding is None:
                raise ValueError("--clock-profile requires --rebinding")
            clock_commands = {
                "insert",
                "delete",
                "move",
                "swap",
                "endpoints",
                "replace-subtree",
            }
            if args.edit_command == "undeclare" and args.cascade:
                clock_commands.add("undeclare")
            if args.edit_command not in clock_commands:
                raise ValueError(
                    f"edit {args.edit_command} cannot run through a clock profile"
                )
            profile = tiergraph.ClockProfile.from_data(
                graph, _profile_json(args.clock_profile)
            )
            editor = (
                profile.edit(args.rebinding)
                if journal is None
                else profile.edit(args.rebinding, journal=journal)
            )
        with _metered(_work_budget(args), f"edit.{args.edit_command}") as metered:
            if metered.meter is not None:
                metered.meter.charge(1)
            _dispatch_edit(editor, graph, args)
        result = editor.freeze()
        patch = (
            journal.to_patch() if _needs_patch(args) and journal is not None else None
        )
        report = (
            {
                "dry_run": args.dry_run,
                "base_fingerprint": tiergraph.fingerprint(
                    graph, tiergraph.EquivalenceView.IDENTIFIED
                ),
                "target_fingerprint": tiergraph.fingerprint(
                    result, tiergraph.EquivalenceView.IDENTIFIED
                ),
                "reports": [
                    value.to_data()
                    for value in (() if journal is None else journal.reports)
                ],
            }
            if _needs_report(args)
            else None
        )
        if args.dry_run:
            assert journal is not None
            while journal.records:
                editor.undo()
            _verify_exact_rollback(graph, editor.freeze())
    _finish_edit(args, result, patch, report)


def _handle_patch(args: argparse.Namespace) -> None:
    """Apply or transform one patch without weakening its fingerprint guards."""
    if args.patch_command == "apply":
        _require_annotation_output(args)
        graph = tiergraph.loads(_read_bytes(args.file))
        patch = tiergraph.patch_loads(_read_bytes(args.patch))
        patch = _annotated_patch(patch, _edit_annotations(args.annotate))
        result = _apply_patch(graph, patch, args)
        if args.dry_run:
            _verify_patch_rollback(graph, result, patch)
        _finish_edit(
            args,
            result,
            patch,
            _patch_report(graph, result, patch, args.dry_run),
        )
        return
    if args.patch_command == "compose":
        first = tiergraph.patch_loads(_read_bytes(args.first))
        second = tiergraph.patch_loads(_read_bytes(args.second))
        _check_distinct(args.first, args.output)
        _check_distinct(args.second, args.output)
        value = tiergraph.compose_patches(first, second)
        _write_output(
            "-", args.output, _strict_text_bytes(tiergraph.patch_dumps(value))
        )
        return
    patch = tiergraph.patch_loads(_read_bytes(args.patch))
    _check_distinct(args.patch, args.output)
    if args.patch_command == "invert":
        encoded = _strict_text_bytes(tiergraph.patch_dumps(patch.invert()))
    else:
        encoded = _json_bytes(
            {
                "patch_version": patch.patch_version,
                "base_fingerprint": patch.base_fingerprint,
                "target_fingerprint": patch.target_fingerprint,
                "annotations": patch.annotations.to_data(),
                "operations": [operation.to_data() for operation in patch.operations],
            }
        )
    _write_output("-", args.output, encoded)


def _handle_diff(args: argparse.Namespace) -> None:
    """Write a deterministic executable patch between two graph documents."""
    _check_distinct(args.target, args.output)
    source = tiergraph.loads(_read_bytes(args.file))
    target = tiergraph.loads(_read_bytes(args.target))
    view = tiergraph.EquivalenceView(args.view)
    patch = tiergraph.diff(source, target, view)
    if args.check and not tiergraph.equivalent(patch.apply(source), target, view):
        raise ValueError("diff replay did not reach the requested target view")
    _write_output(
        args.file, args.output, _strict_text_bytes(tiergraph.patch_dumps(patch))
    )


def _handle_distance(args: argparse.Namespace) -> None:
    """Write exact graph distance or a named interval as JSON."""
    _check_distinct(args.target, args.output)
    if args.costs is not None:
        _check_distinct(args.costs, args.output)
    source = tiergraph.loads(_read_bytes(args.file))
    target = tiergraph.loads(_read_bytes(args.target))
    costs = (
        tiergraph.UNIT_COSTS
        if args.costs is None
        else tiergraph.CostTable.from_data(_json_file(args.costs))
    )
    result = tiergraph.distance(
        source,
        target,
        costs,
        view=tiergraph.EquivalenceView(args.view),
    )
    _write_output(args.file, args.output, _json_bytes(result.to_data()))


def _handle_program(args: argparse.Namespace) -> None:
    """Write the exact construction program for one validated graph."""
    graph = tiergraph.loads(_read_bytes(args.file))
    encoded = _strict_text_bytes(
        tiergraph.program_dumps(tiergraph.graph_to_program(graph))
    )
    _write_output(args.file, args.output, encoded)


def _dispatch_edit(  # noqa: PLR0915 -- command vocabulary
    editor: Any, graph: tiergraph.Graph, args: argparse.Namespace
) -> None:
    """Translate one parsed convenience command to the public journal editor."""
    command = args.edit_command
    if command == "insert":
        editor.insert_item(_qname(args.tier), args.at, _item_json(args.item))
    elif command == "delete":
        reference = _item_path(graph, args.target)
        if args.count == 1:
            editor.remove_item(reference)
        else:
            if args.count <= 0:
                raise ValueError("--count must be positive")
            editor.remove_items(reference.tier, reference.index, args.count)
    elif command == "replace":
        editor.replace_item(_item_path(graph, args.target), _item_json(args.item))
    elif command == "move":
        editor.move_item(_item_path(graph, args.target), args.to)
    elif command == "swap":
        editor.swap_items(_item_path(graph, args.first), _item_path(graph, args.second))
    elif command == "relate":
        editor.add_relation(_relation_json(args.instance), args.at)
    elif command == "unrelate":
        editor.remove_relation(_relation_target(args.target))
    elif command == "endpoints":
        relation_target = _relation_target(args.target)
        sources = _endpoint_array(args.sources)
        targets = _endpoint_array(args.targets)
        if isinstance(
            relation_target,
            tiergraph.RelationInstanceRef | tiergraph.DurableRelationRef,
        ):
            if len(sources) != 1 or len(targets) != 1:
                raise ValueError("binary relation endpoint arrays need one entry")
            source_operand: Any = sources[0]
            target_operand: Any = targets[0]
        else:
            source_operand = sources
            target_operand = targets
        if isinstance(editor, tiergraph.ClockEditor | tiergraph.ClockJournalEditor):
            editor.reparent(relation_target, source_operand, target_operand)
        else:
            editor.set_endpoints(
                relation_target,
                source_operand,
                target_operand,
            )
    elif command == "feature":
        target = _edit_target(graph, args.target)
        if args.action == "set":
            if args.attribute is None or args.name is not None:
                raise ValueError("feature set requires --attribute and not --name")
            editor.set_attribute(target, _attribute_json(args.attribute))
        else:
            if args.name is None or args.attribute is not None:
                raise ValueError("feature remove requires --name and not --attribute")
            editor.remove_attribute(target, _qname(args.name))
    elif command == "declare":
        editor.declare(_declaration_json(args.declaration), args.at)
    elif command == "undeclare":
        target = args.prefix if args.prefix is not None else _qname(args.name)
        if args.cascade:
            editor.undeclare_with_contents(target)
        else:
            editor.undeclare(target)
    elif command == "promote":
        _promote(editor, graph, args.kind, args.target, args.id)
    elif command == "demote":
        _demote(editor, graph, args.kind, args.target)
    elif command == "seal":
        carrier = _seal_carrier(args.carrier)
        if args.action == "drop":
            if args.sealed is not None:
                raise ValueError("seal drop does not take --sealed")
            editor.drop_seal(carrier)
        else:
            if args.sealed is None:
                raise ValueError(f"seal {args.action} requires --sealed")
            if args.action == "set":
                editor.seal(carrier, args.sealed)
            else:
                editor.unseal(carrier, args.sealed)
    elif command == "layer":
        _edit_layer(editor, graph, args)
    elif command == "replace-subtree":
        new_graph = tiergraph.loads(_read_bytes(args.new_graph))
        editor.replace_subtree(
            _item_path(graph, args.root),
            tuple(_qname(value) for value in args.containment),
            tiergraph.Subtree(new_graph, _item_path(new_graph, args.new_root)),
            _replacement_policies(args.policies),
        )
    elif command == "swap-subtrees":
        editor.swap_subtrees(
            _item_path(graph, args.first),
            _item_path(graph, args.second),
            tuple(_qname(value) for value in args.containment),
            _replacement_policies(args.first_policies),
            _replacement_policies(args.second_policies),
        )
    elif command == "prune-orphans":
        editor.prune_orphans()
    elif command == "compact":
        editor.compact()
    else:  # pragma: no cover - argparse closes the command vocabulary
        raise ValueError(f"unsupported edit command {command!r}")


def _finish_edit(
    args: argparse.Namespace,
    result: tiergraph.Graph,
    patch: tiergraph.Patch | None,
    report: object | None,
) -> None:
    """Write requested edit artifacts, then atomically publish the graph."""
    auxiliary_inputs = _edit_auxiliary_inputs(args)
    for artifact in (args.report, args.record, args.inverse_out):
        if artifact is not None:
            _check_distinct(args.file, artifact)
            for source in auxiliary_inputs:
                _check_distinct(source, artifact)
    destinations = [args.report, args.record, args.inverse_out]
    if not args.dry_run:
        graph_destination = args.file if args.in_place else args.output
        destinations.append(graph_destination)
        for source in auxiliary_inputs:
            _check_distinct(source, graph_destination)
    elif args.report is None:
        destinations.append("-")
    _distinct_destinations(destinations)
    if args.record is not None:
        assert patch is not None
        _write_output(
            "-", args.record, _strict_text_bytes(tiergraph.patch_dumps(patch))
        )
    if args.inverse_out is not None:
        assert patch is not None
        _write_output(
            "-",
            args.inverse_out,
            _strict_text_bytes(tiergraph.patch_dumps(patch.invert())),
        )
    if args.report is not None:
        assert report is not None
        _write_output("-", args.report, _json_bytes(report))
    if args.dry_run:
        if args.report is None:
            assert report is not None
            sys.stdout.buffer.write(_json_bytes(report))
        return
    encoded = tiergraph.dump_bytes(result)
    if args.in_place:
        if args.file == "-":
            raise ValueError("--in-place requires a graph file, not stdin")
        _atomic_graph_replace(args.file, encoded)
    else:
        _write_output(args.file, args.output, encoded)


def _edit_auxiliary_inputs(args: argparse.Namespace) -> tuple[str, ...]:
    """Return every file operand that an edit result must not overwrite."""
    names = (
        "patch",
        "clock_profile",
        "item",
        "instance",
        "sources",
        "targets",
        "attribute",
        "declaration",
        "fact",
        "new_graph",
        "policies",
        "first_policies",
        "second_policies",
        "selector",
        "set_attribute",
    )
    return tuple(
        value for name in names if isinstance((value := getattr(args, name, None)), str)
    )


def _needs_patch(args: argparse.Namespace) -> bool:
    """Return whether a direct edit must materialize a portable patch."""
    return args.record is not None or args.inverse_out is not None


def _needs_report(args: argparse.Namespace) -> bool:
    """Return whether an edit must materialize its report data."""
    return args.dry_run or args.report is not None


def _require_annotation_output(args: argparse.Namespace) -> None:
    """Refuse caller metadata when no requested artifact can expose it."""
    if args.annotate and not (_needs_patch(args) or _needs_report(args)):
        raise ValueError(
            "--annotate requires --record, --inverse-out, --report, or --dry-run"
        )


def _needs_journal(args: argparse.Namespace) -> bool:
    """Return whether a direct edit needs opt-in recording state."""
    cascade = args.edit_command == "undeclare" and args.cascade
    return _needs_patch(args) or _needs_report(args) or cascade


def _patch_report(
    base: tiergraph.Graph,
    result: tiergraph.Graph,
    patch: tiergraph.Patch,
    dry_run: bool,
) -> dict[str, object]:
    """Return the stable summary available for an externally supplied patch."""
    del base, result
    return {
        "dry_run": dry_run,
        "base_fingerprint": patch.base_fingerprint,
        "target_fingerprint": patch.target_fingerprint,
        "operations": len(patch.operations),
        "annotations": patch.annotations.to_data(),
    }


def _apply_patch(
    graph: tiergraph.Graph, patch: tiergraph.Patch, args: argparse.Namespace
) -> tiergraph.Graph:
    """Apply a patch while charging one deterministic step per operation."""
    with _metered(_work_budget(args), f"{args.command}.apply") as metered:
        if metered.meter is not None:
            metered.meter.charge(len(patch.operations))
        return patch.apply(graph)


def _verify_patch_rollback(
    base: tiergraph.Graph, result: tiergraph.Graph, patch: tiergraph.Patch
) -> None:
    """Exercise a dry-run patch's recorded inverse and require exact recovery."""
    _verify_exact_rollback(base, patch.invert().apply(result))


def _verify_exact_rollback(base: tiergraph.Graph, restored: tiergraph.Graph) -> None:
    """Require inverse rollback to recover the exact input graph."""
    if restored != base:
        raise tiergraph.GraphValidationError(
            "dry-run inverse rollback did not restore the input graph"
        )


def _bulk_edit(
    graph: tiergraph.Graph,
    args: argparse.Namespace,
    annotations: tiergraph.EditAnnotations,
    *,
    make_patch: bool,
) -> tuple[tiergraph.Graph, tiergraph.Patch | None, int]:
    """Evaluate one bounded selection once and apply its requested operation."""
    budget = _work_budget(args)
    meter = None if budget is None else tiergraph.WorkMeter(budget)
    selected: tiergraph.NodeSet | _match.SpanMatches
    if args.selector is not None:
        if args.ordering is not None or args.prefix is not None:
            raise ValueError("--selector does not take --ordering or --prefix")
        selected = tiergraph.evaluate_selection(
            graph,
            tiergraph.selection_loads(_read_bytes(args.selector)),
            budget=meter,
        )
    elif args.where is not None:
        if args.ordering is not None:
            raise ValueError("--where does not take --ordering")
        with _metered(meter, "edit.bulk.select"):
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
            selected = (
                _predicate.compile_predicate(predicate).bind(graph).select(candidates)
            )
    else:
        if args.ordering is None:
            raise ValueError("--match requires --ordering")
        if args.prefix is not None:
            syntax = _predicate.PredicateSyntax.for_graph(
                graph, default_prefix=args.prefix
            )
        else:
            syntax = _predicate.PredicateSyntax.for_graph(graph)
        ordering = _match._decode_ordering(
            cast(_core.JsonValue, _wire._parsed_json(args.ordering)), "$.ordering"
        )
        selected = (
            _match.compile_pattern(_match.parse_pattern(args.match, syntax))
            .bind(graph, ordering, budget=meter)
            .spans(budget=meter)
        )

    attribute = (
        None if args.set_attribute is None else _attribute_json(args.set_attribute)
    )
    attribute_name = (
        None if args.remove_attribute is None else _qname(args.remove_attribute)
    )

    def operation(editor: tiergraph.GraphEditor, node: tiergraph.Node) -> None:
        """Apply the requested bulk operation to one materialized node."""
        target = _node_target(node)
        if args.delete:
            if node.kind is tiergraph.NodeKind.ITEM:
                editor.remove_item(cast(tiergraph.ItemRef, target))
                return
            if node.kind in {
                tiergraph.NodeKind.RELATION_INSTANCE,
                tiergraph.NodeKind.POLYADIC_RELATION_INSTANCE,
            }:
                editor.remove_relation(cast(_core.RelationTarget, target))
                return
            raise Refusal(
                RefusalStage.SEMANTICS,
                f"bulk delete does not accept {node.kind.value} nodes",
            )
        if attribute is not None:
            editor.set_attribute(cast(_core.EditTarget, target), attribute)
        else:
            assert attribute_name is not None
            editor.remove_attribute(cast(_core.EditTarget, target), attribute_name)

    result = tiergraph.apply_selected(graph, selected, operation, budget=meter)
    patch = (
        _annotated_patch(
            tiergraph.diff(graph, result, tiergraph.EquivalenceView.EXACT), annotations
        )
        if make_patch
        else None
    )
    if isinstance(selected, tiergraph.NodeSet):
        count = len(selected.nodes)
    else:
        count = len({node for match in selected.matches for node in match.items})
    return result, patch, count


def _node_target(node: tiergraph.Node) -> object:
    """Translate one selected identity to the editor's typed target."""
    if node.kind is tiergraph.NodeKind.DOCUMENT:
        return None
    if node.kind in {
        tiergraph.NodeKind.TIER,
        tiergraph.NodeKind.RELATION_DECLARATION,
        tiergraph.NodeKind.ITEM,
        tiergraph.NodeKind.BOUNDARY,
    }:
        return node.reference
    if node.kind is tiergraph.NodeKind.RELATION_INSTANCE:
        return tiergraph.RelationInstanceRef(cast(int, node.reference))
    return tiergraph.PolyadicInstanceRef(cast(int, node.reference))


def _edit_annotations(values: Sequence[str]) -> tiergraph.EditAnnotations:
    """Decode repeatable ``KEY=JSON`` metadata without synthesizing values."""
    strings: dict[str, str] = {}
    confidence: float | None = None
    iteration: int | None = None
    fields: dict[str, _core.JsonValue] = {}
    string_fields = {"author", "reason", "stage", "tool", "timestamp"}
    for entry in values:
        key, separator, text_value = entry.partition("=")
        if not separator or not key:
            raise ValueError("--annotate values must use KEY=JSON")
        try:
            value: object = json.loads(text_value)
        except json.JSONDecodeError:
            value = text_value
        if key in string_fields:
            if not isinstance(value, str):
                raise ValueError(f"annotation {key} must be a string")
            strings[key] = value
        elif key == "confidence":
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise ValueError("annotation confidence must be numeric")
            confidence = value
        elif key == "iteration":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("annotation iteration must be an integer")
            iteration = value
        else:
            fields[key] = cast(_core.JsonValue, value)
    return tiergraph.EditAnnotations(
        author=strings.get("author"),
        reason=strings.get("reason"),
        stage=strings.get("stage"),
        confidence=confidence,
        iteration=iteration,
        tool=strings.get("tool"),
        timestamp=strings.get("timestamp"),
        fields=fields,
    )


def _annotated_patch(
    patch: tiergraph.Patch, annotations: tiergraph.EditAnnotations
) -> tiergraph.Patch:
    """Overlay caller annotations on a patch header and its operations."""
    if not annotations.to_data():
        return patch
    return replace(
        patch,
        annotations=patch.annotations.merged(annotations),
        operations=tuple(
            replace(
                operation,
                annotations=operation.annotations.merged(annotations),
            )
            for operation in patch.operations
        ),
    )


def _qname(value: Sequence[str]) -> tiergraph.QualifiedName:
    return tiergraph.QualifiedName(value[0], value[1])


def _item_path(graph: tiergraph.Graph, text: str) -> tiergraph.ItemRef:
    return cast(tiergraph.ItemRef, _resolved_reference(graph, text, "item", "edit"))


def _path_reference(graph: tiergraph.Graph, text: str) -> object:
    """Return a path's original structural or durable reference after validation."""
    path = tiergraph.CanonicalPath.parse(text)
    binding = tiergraph.StructuralPathProfile().bind(path, graph)
    tiergraph.resolve_path(graph, tiergraph.StructuralPathProfile(), text)
    reference = getattr(binding, "reference", None)
    if reference is None:  # pragma: no cover - structural paths have no alternatives
        raise ValueError("edit paths must identify an item or boundary")
    return reference


def _relation_target(text: str) -> _core.RelationTarget:
    """Decode a relation target without conflating its two index spaces."""
    kind, separator, value = text.partition(":")
    if not separator or not value:
        raise ValueError(
            "relation targets use relation:N, polyadic:N, relation-id:ID, or "
            "polyadic-id:ID"
        )
    if kind == "relation":
        return tiergraph.RelationInstanceRef(int(value))
    if kind == "polyadic":
        return tiergraph.PolyadicInstanceRef(int(value))
    if kind == "relation-id":
        return tiergraph.DurableRelationRef(value)
    if kind == "polyadic-id":
        return tiergraph.DurablePolyadicRef(value)
    raise ValueError(f"unknown relation target kind {kind!r}")


def _edit_target(graph: tiergraph.Graph, text: str) -> _core.EditTarget:
    """Decode a feature or fact target from one unambiguous spelling."""
    if text.startswith("/"):
        return cast(_core.EditTarget, _path_reference(graph, text))
    if text == "document":
        return None
    if text.startswith("tier:"):
        return _qualified_spelling(text.removeprefix("tier:"))
    if text.startswith("relation-declaration:"):
        return _qualified_spelling(text.removeprefix("relation-declaration:"))
    return _relation_target(text)


def _qualified_spelling(text: str) -> tiergraph.QualifiedName:
    namespace, separator, local_name = text.rpartition("|")
    if not separator:
        raise ValueError("qualified target spellings use NAMESPACE|LOCAL")
    return tiergraph.QualifiedName(namespace, local_name)


def _seal_carrier(text: str) -> _core.SealedCarrier:
    if text == "relations":
        return tiergraph.GraphCarrier.RELATIONS
    if text == "polyadic-relations":
        return tiergraph.GraphCarrier.POLYADIC_RELATIONS
    return _qualified_spelling(text)


def _promote(
    editor: Any,
    graph: tiergraph.Graph,
    kind: str,
    target: str,
    durable_id: str,
) -> None:
    if kind == "item":
        editor.promote_item(_item_path(graph, target), durable_id)
    elif kind == "boundary":
        editor.promote_boundary(
            cast(
                tiergraph.BoundaryRef,
                _resolved_reference(graph, target, "boundary", "edit"),
            ),
            durable_id,
        )
    else:
        relation = _relation_target(target)
        if kind == "relation" and not isinstance(
            relation, tiergraph.RelationInstanceRef
        ):
            raise ValueError("relation promotion requires relation:N")
        if kind == "polyadic" and not isinstance(
            relation, tiergraph.PolyadicInstanceRef
        ):
            raise ValueError("polyadic promotion requires polyadic:N")
        editor.promote_relation(relation, durable_id)


def _demote(editor: Any, graph: tiergraph.Graph, kind: str, target: str) -> None:
    if kind == "item":
        reference = _path_reference(graph, target)
        if not isinstance(reference, tiergraph.DurableItemRef):
            raise ValueError("item demotion requires a durable item TG-PATH")
        editor.demote_item(reference)
    elif kind == "boundary":
        reference = _path_reference(graph, target)
        if not isinstance(reference, tiergraph.DurableBoundaryRef):
            raise ValueError("boundary demotion requires a durable boundary TG-PATH")
        editor.demote_boundary(reference)
    else:
        reference = _relation_target(target)
        expected = (
            tiergraph.DurableRelationRef
            if kind == "relation"
            else tiergraph.DurablePolyadicRef
        )
        if not isinstance(reference, expected):
            raise ValueError(f"{kind} demotion requires a durable target")
        editor.demote_relation(reference)


def _json_file(filename: str) -> object:
    """Read one bounded strict JSON operand through the document envelope."""
    return _wire._parsed_json(_read_bytes(filename))


def _item_json(filename: str) -> tiergraph.Item:
    return _machine._decode_item(_json_file(filename), "item")


def _attribute_json(filename: str) -> tiergraph.Attribute:
    return _machine._decode_attribute_value(_json_file(filename), "attribute")


def _relation_json(
    filename: str,
) -> tiergraph.RelationInstance | tiergraph.PolyadicRelationInstance:
    return _machine._decode_relation_instance(_json_file(filename), "relation")


def _endpoint_array(filename: str) -> tuple[_core.RelationEndpointRef, ...]:
    value = _json_file(filename)
    if not isinstance(value, list):
        raise Refusal(RefusalStage.CONSTRUCTION, "endpoints must be an array")
    return tuple(
        _machine._decode_endpoint(item, f"endpoints[{index}]")
        for index, item in enumerate(value)
    )


def _declaration_json(filename: str) -> _core.EditDeclaration:
    """Decode one public declaration shape by its disjoint required fields."""
    value = _json_file(filename)
    if not isinstance(value, dict):
        raise Refusal(RefusalStage.CONSTRUCTION, "declaration must be an object")
    if "prefix" in value:
        return _machine._decode_namespace(value, "declaration")
    if "kind" in value:
        return _machine._decode_relation_declaration(value, "declaration")
    if "domain" in value:
        return _machine._decode_attribute_declaration(value, "declaration")
    return _machine._decode_tier(value, "declaration")


def _edit_layer(editor: Any, graph: tiergraph.Graph, args: argparse.Namespace) -> None:
    layer = tiergraph.LayerName(args.vocabulary, args.source)
    if args.action == "add":
        if args.fact is not None or args.subject is not None or args.name is not None:
            raise ValueError("layer add does not take fact options")
        editor.add_layer(layer)
    elif args.action == "remove":
        if args.fact is not None or args.subject is not None or args.name is not None:
            raise ValueError("layer remove does not take fact options")
        editor.remove_layer(layer)
    elif args.action == "put-fact":
        if args.fact is None or args.subject is not None or args.name is not None:
            raise ValueError("layer put-fact requires --fact only")
        editor.put_fact(
            layer, _machine._decode_layer_fact(_json_file(args.fact), "fact")
        )
    else:
        if args.subject is None or args.name is None or args.fact is not None:
            raise ValueError("layer remove-fact requires --subject and --name")
        editor.remove_fact(
            layer, _layer_subject(graph, args.subject), _qname(args.name)
        )


def _layer_subject(graph: tiergraph.Graph, text: str) -> tiergraph.LayerSubject:
    if text.startswith("/"):
        return cast(tiergraph.LayerSubject, _path_reference(graph, text))
    if text == "document":
        return tiergraph.DocumentRef()
    if text.startswith("tier:"):
        return tiergraph.TierRef(_qualified_spelling(text.removeprefix("tier:")))
    if text.startswith("relation-declaration:"):
        return tiergraph.RelationDeclarationRef(
            _qualified_spelling(text.removeprefix("relation-declaration:"))
        )
    return cast(tiergraph.LayerSubject, _relation_target(text))


def _replacement_policies(filename: str | None) -> tiergraph.ReplacementPolicies:
    """Decode the strict declarative replacement-policy object used by the CLI."""
    if filename is None:
        return tiergraph.ReplacementPolicies()
    value = _json_file(filename)
    if not isinstance(value, dict):
        raise Refusal(
            RefusalStage.CONSTRUCTION, "replacement policies must be an object"
        )
    allowed = {
        "default",
        "correspond",
        "correspondence",
        "relations",
        "layers",
        "insertion_points",
    }
    unknown = set(value) - allowed
    if unknown:
        raise Refusal(
            RefusalStage.SHAPE,
            f"replacement policies have unknown fields {sorted(unknown)!r}",
        )
    correspondence: dict[tiergraph.ItemRef, tuple[tiergraph.ItemRef, ...]] = {}
    raw_correspondence = value.get("correspondence", [])
    if not isinstance(raw_correspondence, list):
        raise Refusal(
            RefusalStage.CONSTRUCTION, "replacement correspondence must be an array"
        )
    for index, entry in enumerate(raw_correspondence):
        if not isinstance(entry, dict) or set(entry) != {"old", "new"}:
            raise Refusal(
                RefusalStage.SHAPE,
                f"replacement correspondence[{index}] needs old and new",
            )
        new = entry["new"]
        if not isinstance(new, list):
            raise Refusal(
                RefusalStage.CONSTRUCTION,
                f"replacement correspondence[{index}].new must be an array",
            )
        correspondence[_machine._decode_item_ref(entry["old"], "old")] = tuple(
            _machine._decode_item_ref(item, "new") for item in new
        )
    relations: dict[tiergraph.QualifiedName, tiergraph.ReplacementAction] = {}
    for index, entry in enumerate(
        _policy_entries(value.get("relations", []), "relations")
    ):
        action = entry["action"]
        if not isinstance(action, str):
            raise Refusal(
                RefusalStage.CONSTRUCTION,
                f"relations[{index}].action must be a string",
            )
        relations[_machine._decode_qname(entry["name"], f"relations[{index}].name")] = (
            tiergraph.ReplacementAction(action)
        )
    layers: dict[tiergraph.LayerName, tiergraph.ReplacementAction] = {}
    for index, entry in enumerate(_policy_entries(value.get("layers", []), "layers")):
        action = entry["action"]
        if not isinstance(action, str):
            raise Refusal(
                RefusalStage.CONSTRUCTION,
                f"layers[{index}].action must be a string",
            )
        layers[_machine._decode_layer_name(entry["name"], f"layers[{index}].name")] = (
            tiergraph.ReplacementAction(action)
        )
    insertion_points: dict[tiergraph.QualifiedName, int] = {}
    for index, entry in enumerate(
        _policy_entries(
            value.get("insertion_points", []), "insertion_points", value_field="index"
        )
    ):
        point = entry["index"]
        if isinstance(point, bool) or not isinstance(point, int):
            raise Refusal(
                RefusalStage.CONSTRUCTION,
                f"insertion_points[{index}].index must be an integer",
            )
        insertion_points[
            _machine._decode_qname(entry["name"], f"insertion_points[{index}].name")
        ] = point
    correspond = value.get("correspond", False)
    if not isinstance(correspond, bool):
        raise Refusal(
            RefusalStage.CONSTRUCTION, "replacement correspond must be boolean"
        )
    default = value.get("default", tiergraph.ReplacementAction.ABANDON.value)
    if not isinstance(default, str):
        raise Refusal(RefusalStage.CONSTRUCTION, "replacement default must be a string")
    return tiergraph.ReplacementPolicies(
        tiergraph.ReplacementAction(default),
        correspond,
        tiergraph.SubtreeCorrespondence(correspondence),
        relations,
        layers,
        insertion_points,
    )


def _policy_entries(
    value: object, name: str, *, value_field: str = "action"
) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise Refusal(RefusalStage.CONSTRUCTION, f"replacement {name} must be an array")
    result: list[dict[str, object]] = []
    for index, entry in enumerate(value):
        if not isinstance(entry, dict) or set(entry) != {"name", value_field}:
            raise Refusal(
                RefusalStage.SHAPE,
                f"replacement {name}[{index}] needs name and {value_field}",
            )
        result.append(entry)
    return result


def _distinct_destinations(values: Sequence[str | None]) -> None:
    """Refuse two artifacts naming the same file or standard output."""
    seen: set[str] = set()
    for value in values:
        if value is None:
            continue
        key = "-" if value == "-" else str(Path(value).resolve())
        if key in seen:
            raise ValueError("edit artifact destinations must be distinct")
        seen.add(key)


def _atomic_graph_replace(filename: str, value: bytes) -> None:
    """Validate a completed temporary graph before atomically replacing its file."""
    output_path = Path(filename).resolve()
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{output_path.name}.", dir=output_path.parent
    )
    replaced = False
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
        os.chmod(temporary, output_path.stat().st_mode & 0o7777)
        tiergraph.loads(Path(temporary).read_bytes())
        os.replace(temporary, output_path)
        replaced = True
    finally:
        if not replaced:
            with suppress(FileNotFoundError):
                os.unlink(temporary)


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
    if output_path.is_dir():
        raise ValueError(f"output path {output_name!r} is a directory")
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


def _strict_text_bytes(value: str) -> bytes:
    """Encode generated text after applying the shared string refusal."""
    _wire._refuse_unencodable_strings(value, "")
    return value.encode("utf-8")


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
