"""Command line entry point, a thin shell over the public API."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO, cast

import tiergraph
import tiergraph_dot
from tiergraph import (
    AddItem,
    AttachValue,
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    BoundarySide,
    DeclareAttribute,
    DeclareNamespace,
    DeclareRelation,
    DeclareTier,
    DurableItemRef,
    DurablePositionRef,
    ExecutionError,
    Item,
    ItemRef,
    NamespaceDeclaration,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    PositionRef,
    Program,
    PromoteItem,
    PromotePosition,
    QualifiedName,
    Relate,
    RelationEndpointKind,
    RelationInstance,
    RelationSideDeclaration,
    Repeat,
    SimpleRelationDeclaration,
    Step,
    TierDeclaration,
    XsdType,
)

_JSONL_LINE_BYTES = 1024 * 1024


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
            _check_distinct(args.file, args.output)
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
    except ValueError as error:
        _diagnostic(args.command, "ValueError", error)
        return 1
    except OSError as error:
        _diagnostic(args.command, type(error).__name__, error)
        return 3
    return 0


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
        return tiergraph.dumps(graph).encode("utf-8")
    return (
        json.dumps(
            tiergraph.to_data(graph),
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
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
    lines = [
        f"format version: {tiergraph.FORMAT_VERSION}",
        f"namespaces: {len(graph.namespaces)}",
        f"tiers: {len(graph.tiers)}",
        f"items: {sum(len(tier.items) for tier in graph.tiers)}",
        f"relation declarations: {len(graph.relation_declarations)}",
        f"binary relation instances: {len(graph.relations)}",
        f"polyadic relation instances: {len(graph.polyadic_relations)}",
        f"attribute declarations: {len(graph.attribute_declarations)}",
        f"populated position values: {len(graph.position_values)}",
        f"document attributes: {len(graph.attributes)}",
    ]
    for tier in graph.tiers:
        lines.append(
            f"tier: {tier.declaration.name} | {tier.declaration.long_name} | "
            f"items={len(tier.items)} | attributes={len(tier.attributes)}"
        )
    for declaration in graph.relation_declarations:
        kind = declaration.to_data()["kind"]
        lines.append(f"relation: {declaration.name} | kind={kind}")
    return "\n".join(lines) + "\n"


def _read_program(filename: str) -> Program:
    stream: BinaryIO
    close = filename != "-"
    stream = open(filename, "rb") if close else sys.stdin.buffer
    try:
        records: list[Any] = []
        total = 0
        for number, line in enumerate(stream, 1):
            total += len(line)
            if total > tiergraph.MAX_DOCUMENT_BYTES:
                raise ValueError(
                    f"JSONL program exceeds {tiergraph.MAX_DOCUMENT_BYTES} bytes"
                )
            if len(line) > _JSONL_LINE_BYTES:
                raise ValueError(
                    f"JSONL line {number} exceeds {_JSONL_LINE_BYTES} bytes"
                )
            if not line.strip():
                raise ValueError(f"JSONL line {number} is whitespace-only")
            try:
                _check_jsonl_depth(line, number)
                records.append(json.loads(line))
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                raise ValueError(f"JSONL line {number}: {error}") from error
            except RecursionError as error:
                raise ValueError(
                    f"JSONL line {number}: JSON nesting depth exceeds limit "
                    f"{tiergraph.MAX_JSON_DEPTH}"
                ) from error
    finally:
        if close:
            stream.close()
    if not records:
        raise ValueError("JSONL program is missing its header line")
    header = _object(records[0], "header", {"machine_version"})
    if header["machine_version"] != tiergraph.MACHINE_VERSION:
        raise ValueError(
            f"header machine_version must be {tiergraph.MACHINE_VERSION!r}"
        )
    try:
        opcodes = tuple(
            _opcode(record, f"line {number}")
            for number, record in enumerate(records[1:], 2)
        )
        return Program(opcodes)
    except TypeError as error:
        raise ValueError(str(error)) from error


def _check_jsonl_depth(line: bytes, number: int) -> None:
    """Refuse excessive JSON container nesting before invoking the parser."""
    depth = 0
    in_string = False
    escaped = False
    for byte in line:
        if in_string:
            if escaped:
                escaped = False
            elif byte == ord("\\"):
                escaped = True
            elif byte == ord('"'):
                in_string = False
        elif byte == ord('"'):
            in_string = True
        elif byte in (ord("["), ord("{")):
            depth += 1
            if depth > tiergraph.MAX_JSON_DEPTH:
                raise ValueError(
                    f"JSONL line {number}: JSON nesting depth exceeds limit "
                    f"{tiergraph.MAX_JSON_DEPTH}"
                )
        elif byte in (ord("]"), ord("}")):
            depth -= 1


def _object(value: Any, path: str, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    actual = set(value)
    if actual != keys:
        raise ValueError(
            f"{path} fields must be {sorted(keys)!r}; got {sorted(actual)!r}"
        )
    return cast(dict[str, Any], value)


def _opcode(value: Any, path: str, depth: int = 1) -> Any:
    if depth > tiergraph.MAX_JSON_DEPTH:
        raise ValueError(
            f"{path}: JSON nesting depth exceeds limit {tiergraph.MAX_JSON_DEPTH}"
        )
    if not isinstance(value, dict) or not isinstance(value.get("opcode"), str):
        raise ValueError(f"{path} must be an opcode object")
    name = value["opcode"]
    decoders: dict[str, tuple[set[str], Callable[[dict[str, Any]], Any]]] = {
        "declare_namespace": (
            {"opcode", "declaration"},
            lambda v: DeclareNamespace(
                _namespace(v["declaration"], f"{path}.declaration")
            ),
        ),
        "declare_tier": (
            {"opcode", "declaration"},
            lambda v: DeclareTier(_tier(v["declaration"], f"{path}.declaration")),
        ),
        "declare_relation": (
            {"opcode", "declaration"},
            lambda v: DeclareRelation(
                _relation_declaration(v["declaration"], f"{path}.declaration")
            ),
        ),
        "declare_attribute": (
            {"opcode", "declaration"},
            lambda v: DeclareAttribute(
                _attribute_declaration(v["declaration"], f"{path}.declaration")
            ),
        ),
        "add_item": (
            {"opcode", "tier", "item"},
            lambda v: AddItem(
                _qname(v["tier"], f"{path}.tier"), _item(v["item"], f"{path}.item")
            ),
        ),
        "promote_item": (
            {"opcode", "reference", "durable_id"},
            lambda v: PromoteItem(
                _item_ref(v["reference"], f"{path}.reference"), v["durable_id"]
            ),
        ),
        "promote_position": (
            {"opcode", "reference", "durable_id"},
            lambda v: PromotePosition(
                _position_ref(v["reference"], f"{path}.reference"), v["durable_id"]
            ),
        ),
        "relate": (
            {"opcode", "relation"},
            lambda v: Relate(_relation_instance(v["relation"], f"{path}.relation")),
        ),
        "attach_value": (
            {"opcode", "domain", "target", "value"},
            lambda v: _attach(v, path),
        ),
        "repeat": (
            {"opcode", "count", "body"},
            lambda v: _repeat(v, path, depth),
        ),
    }
    if name not in decoders:
        raise ValueError(f"{path}.opcode {name!r} is unknown")
    keys, decoder = decoders[name]
    obj = _object(value, path, keys)
    return decoder(obj)


def _qname(value: Any, path: str) -> QualifiedName:
    obj = _object(value, path, {"namespace", "local_name"})
    return QualifiedName(obj["namespace"], obj["local_name"])


def _namespace(value: Any, path: str) -> NamespaceDeclaration:
    obj = _object(value, path, {"prefix", "namespace"})
    return NamespaceDeclaration(obj["prefix"], obj["namespace"])


def _tier(value: Any, path: str) -> TierDeclaration:
    obj = _object(value, path, {"name", "long_name"})
    return TierDeclaration(_qname(obj["name"], f"{path}.name"), obj["long_name"])


def _attribute_declaration(value: Any, path: str) -> AttributeDeclaration:
    obj = _object(value, path, {"name", "domain", "value_type"})
    return AttributeDeclaration(
        _qname(obj["name"], f"{path}.name"),
        AttributeDomain(obj["domain"]),
        XsdType(obj["value_type"]),
    )


def _attribute_value(value: Any, path: str) -> AttributeValue:
    obj = _object(value, path, {"name", "value_type", "lexical"})
    return AttributeValue(
        _qname(obj["name"], f"{path}.name"), XsdType(obj["value_type"]), obj["lexical"]
    )


def _attributes(value: Any, path: str) -> tuple[AttributeValue, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{path} must be an array")
    return tuple(
        _attribute_value(item, f"{path}[{index}]") for index, item in enumerate(value)
    )


def _item(value: Any, path: str) -> Item:
    obj = _object(value, path, {"durable_id", "attributes"})
    return Item(obj["durable_id"], _attributes(obj["attributes"], f"{path}.attributes"))


def _item_ref(value: Any, path: str) -> ItemRef:
    obj = _object(value, path, {"tier", "index"})
    return ItemRef(_qname(obj["tier"], f"{path}.tier"), obj["index"])


def _position_ref(value: Any, path: str) -> PositionRef:
    obj = _object(value, path, {"tier", "index"})
    return PositionRef(_qname(obj["tier"], f"{path}.tier"), obj["index"])


def _endpoint(value: Any, path: str) -> Any:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an endpoint object")
    if set(value) == {"tier", "index"}:
        return _item_ref(value, path)
    if set(value) == {"durable_id"}:
        return DurableItemRef(value["durable_id"])
    if set(value) == {"anchor", "side"}:
        anchor = _object(value["anchor"], f"{path}.anchor", set(value["anchor"]))
        if set(anchor) == {"kind", "durable_id"} and anchor["kind"] == "item":
            decoded_anchor: DurableItemRef | QualifiedName = DurableItemRef(
                anchor["durable_id"]
            )
        elif set(anchor) == {"kind", "tier"} and anchor["kind"] == "tier":
            decoded_anchor = _qname(anchor["tier"], f"{path}.anchor.tier")
        else:
            raise ValueError(f"{path}.anchor has an unknown shape")
        return DurablePositionRef(decoded_anchor, BoundarySide(value["side"]))
    raise ValueError(f"{path} has an unknown reference shape")


def _relation_instance(
    value: Any, path: str
) -> RelationInstance | PolyadicRelationInstance:
    if isinstance(value, dict) and "sources" in value:
        obj = _object(
            value,
            path,
            {"declaration", "sources", "targets", "durable_id", "attributes"},
        )
        sources = obj["sources"]
        targets = obj["targets"]
        if not isinstance(sources, list) or not isinstance(targets, list):
            raise ValueError(f"{path} sources and targets must be arrays")
        return PolyadicRelationInstance(
            _qname(obj["declaration"], f"{path}.declaration"),
            tuple(
                _endpoint(endpoint, f"{path}.sources[{index}]")
                for index, endpoint in enumerate(sources)
            ),
            tuple(
                _endpoint(endpoint, f"{path}.targets[{index}]")
                for index, endpoint in enumerate(targets)
            ),
            obj["durable_id"],
            _attributes(obj["attributes"], f"{path}.attributes"),
        )
    obj = _object(
        value, path, {"declaration", "left", "right", "durable_id", "attributes"}
    )
    return RelationInstance(
        _qname(obj["declaration"], f"{path}.declaration"),
        _endpoint(obj["left"], f"{path}.left"),
        _endpoint(obj["right"], f"{path}.right"),
        obj["durable_id"],
        _attributes(obj["attributes"], f"{path}.attributes"),
    )


def _side(value: Any, path: str) -> RelationSideDeclaration:
    obj = _object(
        value, path, {"endpoint_kinds", "tiers", "minimum", "maximum", "allow_empty"}
    )
    kinds = obj["endpoint_kinds"]
    tiers = obj["tiers"]
    if not isinstance(kinds, list) or not isinstance(tiers, list):
        raise ValueError(f"{path} endpoint_kinds and tiers must be arrays")
    return RelationSideDeclaration(
        tuple(RelationEndpointKind(v) for v in kinds),
        None if not tiers else tuple(_qname(v, f"{path}.tiers") for v in tiers),
        obj["minimum"],
        None if obj["maximum"] == -1 else obj["maximum"],
        obj["allow_empty"],
    )


def _relation_declaration(value: Any, path: str) -> Any:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    kind = value.get("kind")
    if kind == "simple":
        obj = _object(value, path, {"kind", "name", "tier", "item_type", "attributes"})
        return SimpleRelationDeclaration(
            _qname(obj["name"], f"{path}.name"),
            _qname(obj["tier"], f"{path}.tier"),
            _qname(obj["item_type"], f"{path}.item_type"),
            _attributes(obj["attributes"], f"{path}.attributes"),
        )
    if kind == "bipartite":
        obj = _object(
            value,
            path,
            {
                "kind",
                "name",
                "left_type",
                "right_type",
                "left_endpoint",
                "right_endpoint",
                "single_parent",
                "acyclic",
                "attributes",
            },
        )
        return BipartiteRelationDeclaration(
            _qname(obj["name"], f"{path}.name"),
            _qname(obj["left_type"], f"{path}.left_type"),
            _qname(obj["right_type"], f"{path}.right_type"),
            RelationEndpointKind(obj["left_endpoint"]),
            RelationEndpointKind(obj["right_endpoint"]),
            obj["single_parent"],
            obj["acyclic"],
            _attributes(obj["attributes"], f"{path}.attributes"),
        )
    if kind == "polyadic":
        keys = {
            "kind",
            "name",
            "sources",
            "targets",
            "unique_sources",
            "distinct_targets",
            "single_parent",
            "acyclic",
            "targets_subset_of",
            "attributes",
        }
        obj = _object(value, path, keys)
        subset = obj["targets_subset_of"]
        if not isinstance(subset, list) or len(subset) > 1:
            raise ValueError(f"{path}.targets_subset_of must contain at most one name")
        decoded_subset = (
            None if not subset else _qname(subset[0], f"{path}.targets_subset_of[0]")
        )
        return PolyadicRelationDeclaration(
            _qname(obj["name"], f"{path}.name"),
            _side(obj["sources"], f"{path}.sources"),
            _side(obj["targets"], f"{path}.targets"),
            obj["unique_sources"],
            obj["distinct_targets"],
            obj["single_parent"],
            obj["acyclic"],
            decoded_subset,
            _attributes(obj["attributes"], f"{path}.attributes"),
        )
    raise ValueError(f"{path}.kind {kind!r} is unknown")


def _attach(value: dict[str, Any], path: str) -> AttachValue:
    domain = AttributeDomain(value["domain"])
    target_value = value["target"]
    target = target_value
    if isinstance(target_value, dict):
        if domain in {AttributeDomain.TIER, AttributeDomain.RELATION_DECLARATION}:
            target = _qname(target_value, f"{path}.target")
        elif domain is AttributeDomain.POSITION and set(target_value) == {
            "tier",
            "index",
        }:
            target = _position_ref(target_value, f"{path}.target")
        else:
            target = _endpoint(target_value, f"{path}.target")
    return AttachValue(
        domain, target, _attribute_value(value["value"], f"{path}.value")
    )


def _repeat(value: dict[str, Any], path: str, depth: int) -> Repeat:
    body = value["body"]
    if not isinstance(body, list):
        raise ValueError(f"{path}.body must be an array")
    # Repeat and Program are the authoritative kernel bounds. This decoder only
    # validates the JSON shape and constructs their public values.
    return Repeat(
        value["count"],
        tuple(
            _opcode(item, f"{path}.body[{index}]", depth + 2)
            for index, item in enumerate(body)
        ),
    )


__all__ = ["build_parser", "main"]
