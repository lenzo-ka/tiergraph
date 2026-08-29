"""Command line entry point, a thin shell over the public API."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import BinaryIO, cast

import tiergraph
import tiergraph_dot
from tiergraph import ExecutionError, Program, Step, load_program
from tiergraph import core as _core
from tiergraph import machine as _machine_codec
from tiergraph import wire as _wire
from tiergraph.schema import json_schema, shape_hash

_attributes = _machine_codec._decode_attributes
_endpoint = _machine_codec._decode_endpoint
_object = _machine_codec._decode_object
_relation_declaration = _machine_codec._decode_relation_declaration
_relation_instance = _machine_codec._decode_relation_instance
_side = _machine_codec._decode_side
_opcode = _machine_codec._decode_opcode


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser."""
    parser = argparse.ArgumentParser(prog="tiergraph")
    parser.add_argument("--version", action="store_true", help="print the version")
    subparsers = parser.add_subparsers(dest="command")

    validate = subparsers.add_parser("validate", help="validate a graph document")
    validate.add_argument("file", metavar="FILE", help="graph file, or - for stdin")

    render = subparsers.add_parser("render", help="render a graph as DOT")
    _document_arguments(render)
    render.add_argument(
        "--include-empty-tiers", action="store_true", help="include empty tiers"
    )

    inspect = subparsers.add_parser("inspect", help="inspect a graph document")
    _document_arguments(inspect)

    convert = subparsers.add_parser("convert", help="canonicalize a graph document")
    _document_arguments(convert)
    convert.add_argument(
        "--to", choices=("json", "json-compact", "bytes"), required=True
    )

    schema = subparsers.add_parser("schema", help="print the graph document schema")
    schema.add_argument(
        "--format-version", default=tiergraph.FORMAT_VERSION, metavar="N"
    )
    schema.add_argument("--hash", action="store_true", help="print the shape hash")
    schema.add_argument(
        "-o", "--output", default="-", metavar="FILE", help="output file (default: -)"
    )

    run = subparsers.add_parser("run", help="execute a JSONL machine program")
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
    _document_arguments(step, input_help="JSONL program file, or - for stdin")
    step.add_argument(
        "--interactive",
        action="store_true",
        help="use the interactive debugger (also enabled when stdin is a TTY)",
    )

    walk = subparsers.add_parser("walk", help="traverse a transitive relation")
    walk.add_argument("file", metavar="GRAPH", help="graph file, or - for stdin")
    walk.add_argument("--source", action="append", required=True, metavar="PATH")
    walk.add_argument("--relation-namespace", required=True, metavar="NS")
    walk.add_argument("--relation-local", required=True, metavar="LOCAL")
    walk.add_argument("--direction", choices=("forward", "inverse"), default="forward")
    walk.add_argument("--cap", type=int, metavar="N")
    walk.add_argument(
        "-o", "--output", default="-", metavar="FILE", help="output file (default: -)"
    )

    path = subparsers.add_parser("path", help="resolve and spell tiergraph paths")
    path_subparsers = path.add_subparsers(dest="path_command", required=True)
    resolve = path_subparsers.add_parser("resolve", help="resolve a tiergraph path")
    resolve.add_argument("file", metavar="GRAPH", help="graph file, or - for stdin")
    resolve.add_argument("tgpath", metavar="TGPATH", help="tiergraph path to resolve")
    resolve.add_argument(
        "-o", "--output", default="-", metavar="FILE", help="output file (default: -)"
    )

    spell = path_subparsers.add_parser("spell", help="spell a tiergraph path")
    spell.add_argument("file", metavar="GRAPH", help="graph file, or - for stdin")
    spell.add_argument("--kind", choices=("item", "position"), required=True)
    spell.add_argument("--tier-namespace", metavar="NS")
    spell.add_argument("--tier-local", metavar="LOCAL")
    spell.add_argument("--index", type=int, metavar="N")
    spell.add_argument("--durable-id", metavar="ID")
    spell.add_argument("--anchor-item-id", metavar="ID")
    spell.add_argument("--anchor-tier-namespace", metavar="NS")
    spell.add_argument("--anchor-tier-local", metavar="LOCAL")
    spell.add_argument("--side", choices=("before", "after"))
    spell.add_argument(
        "-o", "--output", default="-", metavar="FILE", help="output file (default: -)"
    )

    grammar = subparsers.add_parser("grammar", help="recognize with tiergraph grammars")
    grammar_subparsers = grammar.add_subparsers(dest="grammar_command", required=True)
    for grammar_command, help_text in (
        ("recognize", "recognize a token sequence"),
        ("count", "count token-sequence derivations"),
        ("best", "find best token-sequence derivations"),
    ):
        grammar_parser = grammar_subparsers.add_parser(grammar_command, help=help_text)
        grammar_parser.add_argument(
            "file", metavar="GRAMMAR", help="grammar JSON file, or - for stdin"
        )
        grammar_parser.add_argument("--tokens-json", required=True, metavar="JSON")
        if grammar_command == "best":
            grammar_parser.add_argument("--count", type=int, default=1, metavar="N")
        grammar_parser.add_argument(
            "-o",
            "--output",
            default="-",
            metavar="FILE",
            help="output file (default: -)",
        )

    clock = subparsers.add_parser("clock", help="query declarative clock timing")
    clock_subparsers = clock.add_subparsers(dest="clock_command", required=True)
    for clock_command, help_text in (
        ("positions", "list refined clock positions"),
        ("position", "query one tier position"),
        ("extent", "query a timed tier extent"),
        ("item", "query one timed item"),
    ):
        clock_parser = clock_subparsers.add_parser(clock_command, help=help_text)
        clock_parser.add_argument(
            "file", metavar="GRAPH", help="graph file, or - for stdin"
        )
        clock_parser.add_argument("--profile", required=True, metavar="FILE")
        if clock_command == "position":
            clock_parser.add_argument("--position", required=True, metavar="PATH")
        elif clock_command == "extent":
            clock_parser.add_argument("--tier-namespace", required=True, metavar="NS")
            clock_parser.add_argument("--tier-local", required=True, metavar="LOCAL")
        elif clock_command == "item":
            clock_parser.add_argument("--item", required=True, metavar="PATH")
        clock_parser.add_argument(
            "-o",
            "--output",
            default="-",
            metavar="FILE",
            help="output file (default: -)",
        )

    span = subparsers.add_parser("span", help="render declarative span views")
    span_subparsers = span.add_subparsers(dest="span_command", required=True)
    span_render = span_subparsers.add_parser("render", help="render a span view")
    span_render.add_argument("file", metavar="GRAPH", help="graph file, or - for stdin")
    span_render.add_argument("--profile", required=True, metavar="FILE")
    span_render.add_argument(
        "--format", choices=("text", "json", "jsonl", "html", "dot"), required=True
    )
    span_render.add_argument("--alternatives", action="store_true")
    span_render.add_argument("--jsonl-record", choices=("input", "span"), default=None)
    span_render.add_argument("--include-empty-tiers", action="store_true")
    span_render.add_argument(
        "-o", "--output", default="-", metavar="OUT", help="output file (default: -)"
    )

    selection = subparsers.add_parser("select", help="evaluate a selection query")
    selection.add_argument("file", metavar="GRAPH", help="graph file, or - for stdin")
    selection.add_argument("--query", required=True, metavar="FILE")
    selection.add_argument(
        "-o", "--output", default="-", metavar="OUT", help="output file (default: -)"
    )
    return parser


def _document_arguments(
    parser: argparse.ArgumentParser, *, input_help: str = "graph file, or - for stdin"
) -> None:
    parser.add_argument("file", metavar="FILE", help=input_help)
    parser.add_argument(
        "-o", "--output", default="-", metavar="FILE", help="output file (default: -)"
    )


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
        if args.command == "validate":
            graph = tiergraph.loads(_read_bytes(args.file))
            del graph
            _stdout_text("ok\n")
        elif args.command == "render":
            graph = tiergraph.loads(_read_bytes(args.file))
            rendered = _render(graph, args.include_empty_tiers)
            _write_output(args.file, args.output, _graph_report_bytes(graph, rendered))
        elif args.command == "inspect":
            graph = tiergraph.loads(_read_bytes(args.file))
            _write_output(
                args.file, args.output, _graph_report_bytes(graph, _inspect(graph))
            )
        elif args.command == "convert":
            graph = tiergraph.loads(_read_bytes(args.file))
            _write_output(args.file, args.output, _graph_bytes(graph, args.to))
        elif args.command == "schema":
            encoded = (
                (shape_hash() + "\n").encode("utf-8")
                if args.hash
                else _json_bytes(json_schema(args.format_version))
            )
            _write_output("-", args.output, encoded)
        elif args.command == "walk":
            graph = tiergraph.loads(_read_bytes(args.file))
            if args.cap is not None and args.cap < 1:
                raise ValueError(f"walk cap {args.cap!r} must be positive")
            profile = tiergraph.StructuralPathProfile()
            sources = [
                _walk_source(graph, profile, source_text) for source_text in args.source
            ]
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
        elif args.command == "path":
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
                elif isinstance(resolved, tiergraph.ResolvedPosition):
                    value = {
                        "kind": "position",
                        "path": str(resolved.path),
                        "current": resolved.current.to_data(),
                    }
                else:
                    raise ValueError(  # pragma: no cover - StructuralPathProfile never yields an alternative
                        "structural path profile returned an alternative"
                    )
            else:
                binding = _path_binding(args)
                value = {"path": str(profile.spell(binding, graph))}
            _write_output(args.file, args.output, _json_bytes(value))
        elif args.command == "grammar":
            declaration = tiergraph.grammar_loads(_read_bytes(args.file))
            lowered = tiergraph.lower_grammar(declaration)
            tokens = _tokens_json(args.tokens_json)
            if args.grammar_command == "recognize":
                grammar_value: object = {
                    "recognized": tiergraph.recognize(lowered, tokens).recognized()
                }
            elif args.grammar_command == "count":
                grammar_value = {"count": tiergraph.count(lowered, tokens)}
            else:
                if args.count < 1:
                    raise ValueError(
                        f"best derivation count {args.count!r} must be positive"
                    )
                grammar_value = {
                    "derivations": [
                        derivation.to_data()
                        for derivation in tiergraph.best(lowered, tokens, args.count)
                    ]
                }
            _write_output(args.file, args.output, _json_bytes(grammar_value))
        elif args.command == "clock":
            graph = tiergraph.loads(_read_bytes(args.file))
            clock_profile = tiergraph.ClockProfile.from_data(
                graph, json.loads(_read_bytes(args.profile))
            )
            _check_distinct(args.profile, args.output)
            clock_value = _clock_query(graph, clock_profile, args)
            _write_output(args.file, args.output, _json_bytes(clock_value))
        elif args.command == "span":
            if args.jsonl_record is not None and args.format != "jsonl":
                raise ValueError("--jsonl-record requires --format jsonl")
            if args.include_empty_tiers and args.format != "dot":
                raise ValueError("--include-empty-tiers requires --format dot")
            graph = tiergraph.loads(_read_bytes(args.file))
            span_profile = tiergraph.SpanViewProfile.from_data(
                json.loads(_read_bytes(args.profile))
            )
            _check_distinct(args.profile, args.output)
            rendered = _span_render(graph, span_profile, args)
            _write_output(args.file, args.output, _graph_report_bytes(graph, rendered))
        elif args.command == "select":
            graph = tiergraph.loads(_read_bytes(args.file))
            query = tiergraph.selection_loads(_read_bytes(args.query))
            _check_distinct(args.query, args.output)
            selection_result = tiergraph.evaluate_selection(graph, query)
            _write_output(
                args.file,
                args.output,
                _json_bytes({"nodes": selection_result.to_data()}),
            )
        elif args.command == "run":
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
        else:
            if args.interactive and args.file == "-":
                raise ValueError("--interactive requires a program file, not stdin")
            program = _read_program(args.file)
            interactive = args.interactive or (args.file != "-" and sys.stdin.isatty())
            if interactive:
                if args.output != "-":
                    raise ValueError("interactive mode requires stdout output")
                return _step_interactive(program)
            return _step_dump(program, args.file, args.output)
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


def _clock_position_data(position: tiergraph.ClockPosition) -> dict[str, int]:
    """Encode one refined clock position without inventing a public codec."""
    return {"tick": position.tick, "gap": position.gap}


def _clock_decimal(value: Decimal) -> str:
    """Encode a Decimal with the graph codec's canonical XSD lexical form."""
    return _core._canonical_lexical(tiergraph.XsdType.DECIMAL, format(value, "f"))


def _clock_resolved(
    graph: tiergraph.Graph, text: str, kind: str
) -> tiergraph.ItemRef | tiergraph.PositionRef:
    """Resolve one structural path and require the requested reference kind."""
    resolved = tiergraph.resolve_path(graph, tiergraph.StructuralPathProfile(), text)
    if kind == "item" and isinstance(resolved, tiergraph.ResolvedItem):
        return resolved.current
    if kind == "position" and isinstance(resolved, tiergraph.ResolvedPosition):
        return resolved.current
    article = "an" if kind == "item" else "a"
    raise ValueError(f"clock {kind} path {text!r} did not resolve to {article} {kind}")


def _clock_query(
    graph: tiergraph.Graph,
    profile: tiergraph.ClockProfile,
    args: argparse.Namespace,
) -> object:
    """Evaluate one parsed clock query and return JSON-compatible data."""
    if args.clock_command == "positions":
        return {
            "clock_tier": profile.clock_tier.to_data(),
            "positions": [
                {"index": index, **_clock_position_data(position)}
                for index, position in enumerate(profile.positions)
            ],
        }
    if args.clock_command == "position":
        position_reference = cast(
            tiergraph.PositionRef,
            _clock_resolved(graph, args.position, "position"),
        )
        return {
            "position": position_reference.to_data(),
            "clock_index": profile.clock_position(position_reference),
            "refined": _clock_position_data(
                profile.refined_position(position_reference)
            ),
        }
    if args.clock_command == "extent":
        tier = tiergraph.QualifiedName(args.tier_namespace, args.tier_local)
        start, end = profile.extent(tier)
        return {
            "tier": tier.to_data(),
            "start": _clock_position_data(start),
            "end": _clock_position_data(end),
        }
    item_reference = cast(tiergraph.ItemRef, _clock_resolved(graph, args.item, "item"))
    start, end = profile.structural_span(item_reference.tier, item_reference.index)
    physical = profile.timing(item_reference.tier, item_reference.index)
    try:
        ticks, rate = profile.duration(item_reference.tier, item_reference.index)
        exact_duration: object = {"ticks": ticks, "rate": _clock_decimal(rate)}
    except ValueError as error:
        if str(error) != "clock has no uniform rate":
            raise
        exact_duration = None
    return {
        "item": item_reference.to_data(),
        "structural": {
            "start": _clock_position_data(start),
            "end": _clock_position_data(end),
        },
        "physical": (
            None
            if physical is None
            else {
                "start": _clock_decimal(physical.start),
                "duration": _clock_decimal(physical.duration),
                "unit": physical.unit,
            }
        ),
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
    if isinstance(resolved, tiergraph.ResolvedPosition):
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


def _path_binding(args: argparse.Namespace) -> tiergraph.PathBinding:
    """Build one unambiguous public path binding from spell flags."""
    structural = (args.tier_namespace, args.tier_local, args.index)
    anchor_tier = (args.anchor_tier_namespace, args.anchor_tier_local)
    if args.kind == "item":
        if args.durable_id is not None and all(value is None for value in structural):
            if any(
                value is not None
                for value in (*anchor_tier, args.anchor_item_id, args.side)
            ):
                raise ValueError("item flags cannot include position anchor flags")
            return tiergraph.ItemBinding(tiergraph.DurableItemRef(args.durable_id))
        if all(value is not None for value in structural) and args.durable_id is None:
            if any(
                value is not None
                for value in (*anchor_tier, args.anchor_item_id, args.side)
            ):
                raise ValueError("item flags cannot include position anchor flags")
            return tiergraph.ItemBinding(
                tiergraph.ItemRef(
                    tiergraph.QualifiedName(args.tier_namespace, args.tier_local),
                    args.index,
                )
            )
        raise ValueError(
            "item requires either --durable-id or "
            "--tier-namespace, --tier-local, and --index"
        )

    if args.durable_id is not None:
        raise ValueError("position flags cannot include --durable-id")
    if all(value is not None for value in structural):
        if any(value is not None for value in (*anchor_tier, args.anchor_item_id)):
            raise ValueError("structural position flags cannot include durable anchors")
        if args.side is not None:
            raise ValueError("structural position flags cannot include --side")
        return tiergraph.PositionBinding(
            tiergraph.PositionRef(
                tiergraph.QualifiedName(args.tier_namespace, args.tier_local),
                args.index,
            )
        )
    if any(value is not None for value in structural):
        raise ValueError(
            "structural position requires --tier-namespace, --tier-local, and --index"
        )
    if args.side is None:
        raise ValueError("durable position requires --side")
    side = tiergraph.BoundarySide(args.side)
    if args.anchor_item_id is not None and all(value is None for value in anchor_tier):
        return tiergraph.PositionBinding(
            tiergraph.DurablePositionRef(
                tiergraph.DurableItemRef(args.anchor_item_id), side
            )
        )
    if args.anchor_item_id is None and all(value is not None for value in anchor_tier):
        return tiergraph.PositionBinding(
            tiergraph.DurablePositionRef(
                tiergraph.QualifiedName(
                    args.anchor_tier_namespace, args.anchor_tier_local
                ),
                side,
            )
        )
    raise ValueError(
        "durable position requires exactly one of --anchor-item-id or "
        "--anchor-tier-namespace with --anchor-tier-local"
    )


def _diagnostic(command: str, category: str, error: BaseException) -> None:
    print(f"tiergraph: {command}: {category}: {error}", file=sys.stderr)


def _read_bytes(filename: str) -> bytes:
    if filename == "-":
        return sys.stdin.buffer.read()
    return Path(filename).read_bytes()


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
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _check_distinct(input_name: str, output_name: str) -> None:
    if input_name != "-" and output_name != "-":
        if Path(input_name).resolve() == Path(output_name).resolve():
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


def _json_bytes(value: object) -> bytes:
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
    ).encode("utf-8")


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
        elif name in {"run-to", "break"} and len(words) == 2:
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
    for tier in tier_summaries:
        lines.append(
            f"tier: {tier['name']} | {tier['long_name']} | "
            f"items={tier['items']} | attributes={tier['attributes']}"
        )
    relation_summaries = cast(list[dict[str, object]], summary["relation_summaries"])
    for relation in relation_summaries:
        lines.append(f"relation: {relation['name']} | kind={relation['kind']}")
    return "\n".join(lines) + "\n"


def _read_program(filename: str) -> Program:
    stream: BinaryIO
    close = filename != "-"
    stream = Path(filename).open("rb") if close else sys.stdin.buffer
    try:
        return load_program(stream)
    finally:
        if close:
            stream.close()


__all__ = ["build_parser", "main"]
