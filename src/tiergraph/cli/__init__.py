"""Command line entry point, a thin shell over the public API."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, cast

import tiergraph
import tiergraph_dot
from tiergraph import ExecutionError, Program, Step, load_program
from tiergraph import machine as _machine_codec
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
            _write_output(args.file, args.output, rendered.encode("utf-8"))
        elif args.command == "inspect":
            graph = tiergraph.loads(_read_bytes(args.file))
            _write_output(args.file, args.output, _inspect(graph).encode("utf-8"))
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
        elif args.command == "run":
            if args.include_empty_tiers and args.to != "dot":
                raise ValueError("--include-empty-tiers requires --to dot")
            program = _read_program(args.file)
            graph = program.unroll().graph
            encoded = (
                _render(graph, args.include_empty_tiers).encode("utf-8")
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


def _graph_bytes(graph: tiergraph.Graph, target: str) -> bytes:
    if target == "bytes":
        return tiergraph.dump_bytes(graph)
    if target == "json":
        return _json_bytes(tiergraph.to_data(graph))
    return tiergraph.dump_compact(graph).encode("utf-8")


def _json_bytes(value: object) -> bytes:
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
    return (
        json.dumps(
            step.to_data(),
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
