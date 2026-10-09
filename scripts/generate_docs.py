"""Generate and verify documentation derived from the public interfaces."""

from __future__ import annotations

import argparse
import contextlib
import importlib
import inspect
import io
import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from enum import Enum
from pathlib import Path
from typing import Any, TypeAliasType, cast

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from examples.mixing import run_example  # noqa: E402

import tiergraph  # noqa: E402
import tiergraph_dot  # noqa: E402
from tiergraph.budget import _MAX_USER_STEPS  # noqa: E402
from tiergraph.cli import build_parser  # noqa: E402
from tiergraph.machine import MACHINE_VERSION  # noqa: E402
from tiergraph.machine_codec import _JSONL_LINE_BYTES  # noqa: E402
from tiergraph.match import (  # noqa: E402
    _MAX_PATTERN_DEPTH,
    _MAX_PATTERN_NODES,
    MAX_PATTERN_POSITIONS,
    MAX_PATTERN_STATES,
)
from tiergraph.match_text import _MAX_PATTERN_NESTING  # noqa: E402
from tiergraph.predicate import _MAX_REGEX_NESTING  # noqa: E402
from tiergraph.predicate_text import _MAX_PREDICATE_NESTING  # noqa: E402

MANIFEST_PATH = ROOT / "docs" / "manifest.json"
API_PATH = ROOT / "docs" / "reference" / "api.md"
CLI_PATH = ROOT / "docs" / "reference" / "cli.md"
MIXING_PATH = ROOT / "docs" / "guide" / "recognize-and-act.md"
WORK_BUDGETS_PATH = ROOT / "docs" / "guide" / "work-budgets.md"
CONTRIBUTING_PATH = ROOT / "CONTRIBUTING.md"
MAKEFILE_PATH = ROOT / "Makefile"
DIRECTIVE = re.compile(
    r"<!-- tiergraph:(?P<kind>[a-z-]+) -->(?P<body>.*?)<!-- /tiergraph:\1 -->",
    re.DOTALL,
)
GATE_TARGET = re.compile(r"^gate:(?P<steps>.*)$", re.MULTILINE)

# Each entry pins one reader-visible phrase to the code value it describes.
PROSE_CONSTANTS: tuple[tuple[Path, str, int], ...] = (
    (CLI_PATH, "Maximum CLI work budget: {value:,} steps.", _MAX_USER_STEPS),
    (CLI_PATH, "Maximum compiled pattern states: {value:,}.", MAX_PATTERN_STATES),
    (CLI_PATH, "Maximum pattern AST nodes: {value:,}.", _MAX_PATTERN_NODES),
    (CLI_PATH, "Pattern text nesting limit: {value}.", _MAX_PATTERN_NESTING),
    (CLI_PATH, "Pattern AST nesting limit: {value}.", _MAX_PATTERN_DEPTH),
    (CLI_PATH, "Predicate text nesting limit: {value}.", _MAX_PREDICATE_NESTING),
    (CLI_PATH, "Regular-expression nesting limit: {value}.", _MAX_REGEX_NESTING),
    (CLI_PATH, "JSONL program line cap: {value:,} bytes.", _JSONL_LINE_BYTES),
    (
        WORK_BUDGETS_PATH,
        "Pattern text accepts at most {value} nested groups.",
        _MAX_PATTERN_NESTING,
    ),
    (
        WORK_BUDGETS_PATH,
        "Predicate text accepts at most {value} nested groups.",
        _MAX_PREDICATE_NESTING,
    ),
    (
        WORK_BUDGETS_PATH,
        "A pattern AST may contain at most {value:,} nodes.",
        _MAX_PATTERN_NODES,
    ),
    (
        WORK_BUDGETS_PATH,
        "A pattern AST may nest at most {value} levels.",
        _MAX_PATTERN_DEPTH,
    ),
    (
        WORK_BUDGETS_PATH,
        "A regular expression may nest at most {value} levels.",
        _MAX_REGEX_NESTING,
    ),
    (
        WORK_BUDGETS_PATH,
        "A compiled pattern may contain at most {value:,} item positions.",
        MAX_PATTERN_POSITIONS,
    ),
    (
        WORK_BUDGETS_PATH,
        "A compiled pattern may contain at most {value:,} NFA states.",
        MAX_PATTERN_STATES,
    ),
)

# What each gate step is for, keyed by the makefile target that runs it. The
# order and the membership of the list are the makefile's; only the gloss is
# written here. A step added to `gate` with no entry here stops the render and
# says what to add and where, which is the point: a contributor-facing list of
# the gate had fallen behind the gate twice while it was prose someone retyped.
GATE_STEP_PROSE: Mapping[str, str] = {
    "lint": "Ruff linting",
    "format-check": "Ruff formatting checks",
    "types": "strict mypy checks",
    "test": "the test suite under branch coverage",
    "determinism": (
        "the suite again in separate processes, with hash seeds 0, 12345, and 999"
    ),
    "schema-check": "the committed JSON Schema still matches a fresh render",
    "format-growth": "the wire format may only grow within a release line",
    "format-semantics": (
        "the current decoder still accepts the documents the frozen corpus "
        "recorded as accepted when it was captured, bar any since adjudicated "
        "never legal"
    ),
    "docs-check": "generated documentation matches a fresh deterministic render",
    "tracked-clean": "tracked-file hygiene, over every tracked file",
    "documented": "the public-docstring check",
    "reservations": (
        "every registered reservation's prose is still pinned, and every "
        "enforceable one is still undischarged"
    ),
    "changelog-claims": "the changelog-claim check",
}


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    """Load the declarative documentation manifest."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("documentation manifest must be an object")
    return value


def validate_manifest(manifest: Mapping[str, Any]) -> None:
    """Require exact, one-to-one coverage of every shipped export list."""
    groups = manifest.get("api_groups")
    if not isinstance(groups, dict):
        raise ValueError("api_groups must be an object")
    names = [name for group in groups.values() for name in group]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"duplicate tiergraph exports: {', '.join(duplicates)}")
    expected = set(tiergraph.__all__)
    actual = set(names)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ValueError(
            f"tiergraph export mismatch; missing={missing}; unknown={unknown}"
        )
    companions = manifest.get("companion_exports", {})
    dot = companions.get("tiergraph_dot", [])
    if list(tiergraph_dot.__all__) != list(dot):
        raise ValueError(
            "tiergraph_dot export mismatch; "
            f"manifest={list(dot)!r}; package={list(tiergraph_dot.__all__)!r}"
        )
    secondary = manifest.get("secondary")
    if not isinstance(secondary, dict):
        raise ValueError("secondary must be an object")
    for module_name, published in secondary.items():
        module = importlib.import_module(module_name)
        declared = getattr(module, "__all__", None)
        if declared is None:
            raise ValueError(
                f"{module_name} declares no __all__; a documented surface "
                "is declared by its module, not by the manifest"
            )
        if list(declared) != list(published):
            raise ValueError(
                f"{module_name} export mismatch; "
                f"manifest={list(published)!r}; package={list(declared)!r}"
            )


def _signature(value: object) -> str:
    try:
        signature = inspect.signature(cast(Callable[..., object], value))
    except (TypeError, ValueError):
        return ""
    public = tuple(
        parameter
        for parameter in signature.parameters.values()
        if not parameter.name.startswith("_")
    )
    return str(signature.replace(parameters=public))


def _constant_text(value: object) -> str:
    """Render unordered constants deterministically for generated prose."""
    if isinstance(value, frozenset):
        members = ", ".join(sorted(repr(member) for member in value))
        return f"frozenset({{{members}}})"
    return str(value)


def _entry(module: object, name: str, descriptions: Mapping[str, str]) -> str:
    value = getattr(module, name)
    heading = f"### `{name}`"
    if name in descriptions:
        rendered = _constant_text(value)
        return f"{heading}\n\n{descriptions[name]} Current value: `{rendered}`."
    if isinstance(value, TypeAliasType):
        parameters = ""
        if value.__type_params__:
            parameters = (
                "["
                + ", ".join(parameter.__name__ for parameter in value.__type_params__)
                + "]"
            )
        return (
            f"{heading}\n\n```text\ntype {name}{parameters} = {value.__value__!r}\n```"
        )
    doc = inspect.getdoc(value)
    if not doc:
        # These aliases have no runtime marker that distinguishes them from values;
        # the manifest inventories exports, rather than duplicating type metadata.
        kind = "type alias" if name in {"Path", "PathValue"} else "singleton"
        return f"{heading}\n\nModule-level {kind}: `{type(value).__name__}`."
    signature = _signature(value)
    declaration = f"```text\n{name}{signature}\n```\n\n" if signature else ""
    members = _class_members(value, name) if inspect.isclass(value) else ""
    return f"{heading}\n\n{declaration}{doc}{members}"


def _class_members(value: type[object], class_name: str) -> str:
    """Render documented public members in deterministic definition order."""
    parts: list[str] = []
    if issubclass(value, Enum):
        rendered = "\n".join(
            f"- `{member.name}` = `{member.value}`" for member in value
        )
        parts.append(f"#### `{class_name}` members\n\n{rendered}")
    for member_name in value.__dict__:
        if member_name.startswith("_"):
            continue
        member = inspect.getattr_static(value, member_name)
        target: object
        kind: str
        if isinstance(member, classmethod):
            target = member.__func__
            kind = "Class method."
        elif isinstance(member, staticmethod):
            target = member.__func__
            kind = "Static method."
        elif isinstance(member, property):
            if member.fget is None:
                continue
            target = member.fget
            kind = "Property."
        elif inspect.isfunction(member):
            target = member
            kind = "Method."
        else:
            continue
        doc = inspect.getdoc(target)
        if not doc:
            continue
        signature = _signature(target)
        declaration = (
            f"\n\n```text\n{class_name}.{member_name}{signature}\n```"
            if signature
            else ""
        )
        parts.append(
            f"#### `{class_name}.{member_name}`\n\n{kind}{declaration}\n\n{doc}"
        )
    return "\n\n" + "\n\n".join(parts) if parts else ""


def api_bytes(manifest: Mapping[str, Any]) -> bytes:
    """Render the complete API reference in manifest order."""
    descriptions = manifest["constant_descriptions"]
    parts = [
        "# API reference",
        "",
        "This page is generated from the shipped objects and the documentation manifest.",
        f"It covers {len(tiergraph.__all__)} top-level `tiergraph` exports exactly once.",
    ]
    for group, names in manifest["api_groups"].items():
        parts.extend(("", f"## {group.replace('-', ' ').title()}", ""))
        parts.append(
            "\n\n".join(_entry(tiergraph, name, descriptions) for name in names)
        )
    parts.extend(("", "## Supported secondary surface", ""))
    for module_name, names in manifest["secondary"].items():
        module = importlib.import_module(module_name)
        # These modules are promised secondary APIs; the manifest inventories
        # exports, while this prose records each module's stability policy.
        stability = (
            "This module is a supported secondary API."
            if module_name
            in ("tiergraph.match", "tiergraph.predicate", "tiergraph.semiring")
            else "This module is importable and usable, but carries no "
            f"API-stability promise at version {tiergraph.__version__}."
        )
        parts.extend((f"### `{module_name}`", "", stability, ""))
        # BuilderError is intentionally importable but absent from build.__all__, so
        # it cannot join the manifest's exact inventory of declared exports.
        if module_name == "tiergraph.build":
            parts.extend(
                (
                    "Builder notation errors raise the directly importable "
                    "`tiergraph.build.BuilderError`, a `ValueError` subclass. "
                    "It is not part of the module's star-exported surface.",
                    "",
                )
            )
        parts.append("\n\n".join(_entry(module, name, descriptions) for name in names))
        parts.append("")
    parts.extend(("", "## Companion package", ""))
    parts.append(
        "\n\n".join(
            _entry(tiergraph_dot, name, {})
            for name in manifest["companion_exports"]["tiergraph_dot"]
        )
    )
    return ("\n".join(parts).rstrip() + "\n").encode()


def _walk_cli_parsers(
    parser: argparse.ArgumentParser, path: str = "tiergraph"
) -> Iterator[tuple[str, argparse.ArgumentParser]]:
    """Yield the root and every nested command parser in display order."""
    yield path, parser
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, child in action.choices.items():
                yield from _walk_cli_parsers(child, f"{path} {name}")


def validate_cli_help(parser: argparse.ArgumentParser) -> None:
    """Require descriptions, examples, exit codes, and help for every argument."""
    for path, command in _walk_cli_parsers(parser):
        if not command.description:
            raise ValueError(f"{path} has no help description")
        epilog = command.epilog or ""
        if "Examples:\n" not in epilog or "Exit codes:\n" not in epilog:
            raise ValueError(f"{path} has no examples and exit-code epilog")
        for action in command._actions:
            if isinstance(action, argparse._SubParsersAction) or action.dest == "help":
                continue
            if action.help is None or action.help is argparse.SUPPRESS:
                spelling = (
                    action.option_strings[0] if action.option_strings else action.dest
                )
                raise ValueError(f"{path} argument {spelling!r} has no help text")


def _json_block(value: object) -> str:
    """Render one deterministic indented JSON example."""
    return "```json\n" + json.dumps(value, indent=2, sort_keys=True) + "\n```"


def _json_input_formats() -> str:
    """Render the strict JSON forms accepted directly by CLI arguments."""
    qname = {"namespace": "urn:example", "local_name": "tokens"}
    selector = {"select": "items", "tier": qname}
    ordering = {"order": "tier", "tier": qname}
    any_item = {"pattern": "atom", "predicate": {"test": "and", "args": []}}
    match_request = {
        "match": "spans",
        "ordering": ordering,
        "pattern": any_item,
        "limit": 20,
        "max_steps": 100_000,
    }
    span_profile = {
        "base_tier": qname,
        "span_tiers": [
            {"namespace": "urn:example", "local_name": "words"},
        ],
        "coverage_relation": {
            "namespace": "urn:example",
            "local_name": "coverage",
        },
        "score_attribute": {"namespace": "urn:example", "local_name": "score"},
        "value_attribute": {"namespace": "urn:example", "local_name": "text"},
        "char_offset_attribute": None,
        "alternative_relation": None,
    }
    clock_profile = {
        "clock_tier": {"namespace": "urn:example", "local_name": "clock"},
        "binding_relation": {
            "namespace": "urn:example",
            "local_name": "clock-binding",
        },
        "rate_attribute": None,
        "unit_attribute": {"namespace": "urn:example", "local_name": "unit"},
        "tick_attribute": None,
        "gap_attribute": None,
        "untimed_attribute": None,
        "start_attribute": None,
        "duration_attribute": None,
    }
    replacement_policies = {
        "default": "abandon",
        "correspond": True,
        "correspondence": [
            {
                "old": {"tier": qname, "index": 0},
                "new": [{"tier": qname, "index": 0}],
                "identity_correspondence": True,
            }
        ],
        "relations": [{"name": qname, "action": "follow"}],
        "layers": [
            {
                "name": {"vocabulary": "urn:example", "source": "hand"},
                "action": "split",
            }
        ],
        "insertion_points": [{"name": qname, "index": 0}],
    }
    name = tiergraph.QualifiedName("urn:example:grammar", "S")
    text_name = tiergraph.QualifiedName("urn:example:grammar", "text")
    terminal = tiergraph.GrammarTerminal(
        tiergraph.AttributeValue(text_name, tiergraph.XsdType.STRING, "x")
    )
    grammar = tiergraph.GrammarDeclaration(
        (name,), name, (tiergraph.GrammarRule(name, (terminal,), (terminal,)),)
    ).to_data()
    grammar_input = tiergraph.GrammarInput.from_symbols(("x",)).to_data()
    return (
        "## JSON input formats\n\n"
        "All objects are strict: unknown or missing fields are refused. A qualified "
        "name is an object with string `namespace` and `local_name` fields.\n\n"
        "### Selector documents\n\n"
        "`select --selector` accepts one selector object. Leaf forms are `tier`, "
        "`type`, `items`, `boundaries`, `item`, `boundary`, and `attribute`. "
        "`item` and `boundary` carry a `path`; the tier-based forms carry a "
        "qualified name. `where` carries `base` and `predicate`; `sequence` carries "
        "`ordering` and `pattern`. Compound forms use `op` equal to `union` or "
        "`intersection` with a nonempty `args` array, or `difference` with `left` "
        "and `right`.\n\n"
        + _json_block(selector)
        + "\n\n### Ordering and match requests\n\n"
        "`match --ordering` accepts `tier` with `tier`; `containers` with `relation` "
        "and `containers`; `adjacent-runs` with `source` and `offsets`; or `declared` "
        "with `successor`, `members`, optional `chain`, and optional boolean "
        "`open_left`. Qualified-name fields use the shape shown below; selector "
        "fields use the selector shapes above.\n\n" + _json_block(ordering) + "\n\n"
        "`match --request` accepts `match` equal to `exists`, `focus`, `spans`, or "
        "`count`, plus `ordering` and a tagged pattern AST. `spans` may add a "
        "nonnegative `limit`. Every operation may add positive `max_steps`. Pattern "
        "tags are `atom` with `predicate`, `seq` or `alt` with `parts`, `repeat` "
        "with `body`, `min`, and optional `max`, `focus` with `body`, and the "
        "field-only `start` and `end` forms.\n\n" + _json_block(match_request) + "\n\n"
        "The `pairs` request instead requires selector fields `left` and `right`, "
        "an interval `relation`, and an `offsets` object with qualified-name "
        "`origin`, exactly one of `extent` or `end`, and optional `partition`; it "
        "may add `limit` and `max_steps`. The `lattice` request requires one "
        "qualified name in `transitions`, an array of item references in `roots`, "
        "qualified-name `emission`, `pattern`, and `policy`. Policy is "
        '`{"policy": "unambiguous"}` or `determinize` with positive '
        "`max_states`; `max_steps` is optional.\n\n"
        "### Span and clock profiles\n\n"
        "A span profile requires the seven fields shown below. Optional fields are "
        "`base_surface_attribute`, `point_tiers`, `point_coverage_relation`, "
        "`value_attributes`, and `clock_face` (`tick` or `physical`). Nullable "
        "qualified-name roles are written as JSON null.\n\n"
        + _json_block(span_profile)
        + "\n\n"
        "A clock profile requires all nine fields below. The clock tier, binding "
        "relation, and unit attribute are qualified names; every other attribute "
        "role is a qualified name or null.\n\n"
        + _json_block(clock_profile)
        + "\n\n### Editing operands and replacement policies\n\n"
        "Item, attribute, relation-instance, declaration, and layer-fact files "
        "use the corresponding public `to_data()` object shape. Every operand is "
        "bounded strict JSON. A replacement-policy object may contain only the "
        "fields shown below. `default` and each `action` may be `abandon`, "
        "`follow`, `split`, `drop`, or `trim`; `drop` and the polyadic-only "
        "`trim` are fallbacks for crossing relations that cannot carry. "
        "`correspond` enables local equal-content alignment. Explicit "
        "`correspondence` entries map one old item reference to an ordered array "
        "of new item references; optional `identity_correspondence` is an array "
        "naming exactly one target from that alignment. `true` is shorthand only "
        "when the alignment has one target. Relation, "
        "layer, and insertion-point arrays override the default for their named "
        "carriers. Omitted fields use the library's abandonment defaults, while "
        "an uncarryable crossing relation refuses unless `drop` or `trim` was "
        "named.\n\n"
        + _json_block(replacement_policies)
        + "\n\n### Grammar documents and inputs\n\n"
        "A grammar document contains `nonterminals`, `start`, and `rules`. Each rule "
        "has `left`, source and target arrays of tagged `terminal` or `hole` "
        "elements, `boundary`, `awaited_variables`, and nullable `weight`; optional "
        "`provenance` is an array. Attribute values contain `name`, `value_type`, "
        "and `lexical`.\n\n" + _json_block(grammar) + "\n\n"
        "`--tokens-json` is a JSON array of strings. `--input-json` is an object "
        "with a `tokens` array. Each typed token requires string `symbol` and a "
        "nonempty `realization` array; optional fields are `provenance`, `source`, "
        "and `span`. Each realization requires a string `tokens` array and may add "
        "string `provenance` and decimal-string `weight`.\n\n"
        + _json_block(grammar_input)
        + "\n\n### Fold requests\n\n"
        "`fold` and `discharge fold` do not read request JSON. They assemble the "
        "request from `--name`, the attribute namespace and local name, repeatable "
        "`--tier` and `--transition`, `--semiring`, `--lift`, repeatable `--root`, "
        "`--ranked`, `--output-cap`, and `--max-steps`. `discharge fold` also reads "
        "`--exactness`.\n"
    )


def _implementation_limits() -> str:
    """Render reader-visible ceilings directly from their code constants."""
    mebibytes = _JSONL_LINE_BYTES // (1024 * 1024)
    return (
        "## Implementation limits\n\n"
        f"- Maximum CLI work budget: {_MAX_USER_STEPS:,} steps.\n"
        f"- Maximum compiled pattern states: {MAX_PATTERN_STATES:,}.\n"
        f"- Maximum pattern AST nodes: {_MAX_PATTERN_NODES:,}.\n"
        f"- Pattern text nesting limit: {_MAX_PATTERN_NESTING}.\n"
        f"- Pattern AST nesting limit: {_MAX_PATTERN_DEPTH}.\n"
        f"- Predicate text nesting limit: {_MAX_PREDICATE_NESTING}.\n"
        f"- Regular-expression nesting limit: {_MAX_REGEX_NESTING}.\n"
        f"- JSONL program line cap: {_JSONL_LINE_BYTES:,} bytes. "
        f"This is {mebibytes} MiB.\n"
    )


def cli_bytes() -> bytes:
    """Render normalized parser help and the checked command contracts."""
    parser = build_parser()
    validate_cli_help(parser)
    # argparse exposes no public traversal API for nested subparser actions.
    action = next(
        candidate
        for candidate in parser._actions
        if isinstance(candidate, argparse._SubParsersAction)
    )
    helps = [("tiergraph", parser.format_help().rstrip())]
    for name, child in action.choices.items():
        helps.append((f"tiergraph {name}", child.format_help().rstrip()))
        nested = next(
            (
                candidate
                for candidate in child._actions
                if isinstance(candidate, argparse._SubParsersAction)
            ),
            None,
        )
        if nested is not None:
            helps.extend(
                (
                    f"tiergraph {name} {subname}",
                    subparser.format_help().rstrip(),
                )
                for subname, subparser in nested.choices.items()
            )
    help_text = "\n\n".join(
        f"### `{name}`\n\n```text\n{body}\n```" for name, body in helps
    )
    machine_header = json.dumps(
        {"machine_version": MACHINE_VERSION}, separators=(",", ":")
    )
    program = (
        f"{machine_header}\n"
        '{"opcode":"declare_namespace","declaration":'
        '{"namespace":"urn:step","prefix":"s"}}\n'
    )
    with tempfile.TemporaryDirectory() as directory:
        program_path = Path(directory) / "program.jsonl"
        program_path.write_text(program, encoding="utf-8")
        execution = subprocess.run(
            (sys.executable, "-m", "tiergraph", "step", str(program_path)),
            cwd=ROOT,
            check=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
        )
    if execution.stderr:
        raise ValueError("CLI stepping example wrote to stderr")
    for index, line in enumerate(execution.stdout.splitlines(), start=1):
        try:
            json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"CLI stepping example line {index} is not JSON: {error}"
            ) from error
    step_output = execution.stdout.rstrip("\n")
    return (
        "# CLI reference\n\n"
        "The `tiergraph` command prints help when called without arguments. "
        "`--version` prints one JSON object and exits successfully.\n\n"
        "`tiergraph.cli.build_parser()` is importable and usable, but carries no "
        f"API-stability promise at version {tiergraph.__version__}.\n\n"
        "## Contracts\n\n"
        "Every command that reads an input document accepts `-` in place of that "
        "document's filename and reads it from standard input, including the "
        "inputs named by `--result`, `--profile`, and `--selector`; `step "
        "--interactive` and the payload-oriented `blob` and `bundle` commands "
        "require files, as described below. `schema` and `semirings` "
        "read no document, and `discharge`, `path`, `grammar`, `clock`, and "
        "`span` take only a subcommand, so `-` is a command-line usage error for "
        "those and exits 2. Document-producing commands write to "
        "stdout by default or to `-o/--output`; diagnostics go only to stderr. "
        "Exit status 0 means success, 1 means invalid input or a refused operation, "
        "2 means command-line usage error, and 3 means an I/O failure or an input "
        "the CLI could not decode. The CLI's own reports refuse a graph the writer "
        "could not write in the same way as the writer, with exit status 1.\n\n"
        "`--max-steps N` is an opt-in deterministic work guard on mutating `edit` "
        "and `patch apply` commands, `select`, `match`, `fold`, `discharge fold`, "
        "and the grammar commands that evaluate "
        "a fold (`recognize`, `count`, `best`, and `generate`). `N` is a positive "
        f"integer no greater than {_MAX_USER_STEPS:,}; omission installs no budget "
        "and preserves the unbudgeted behavior. Use it when accepting untrusted "
        "pattern or predicate text. Exhaustion is a semantics-stage refusal on "
        "stderr and exits 1; a completed nonempty prefix from `match ... spans` "
        "instead succeeds with `extent` equal to `cut-at-budget`. Match request "
        "JSON accepts the same optional `max_steps` field for `exists`, `focus`, "
        "`spans`, `count`, `pairs`, and `lattice`; supplying both the field and the "
        "flag is refused rather than choosing one silently. `grammar lattice` "
        "takes no guard because it constructs and serializes topology without "
        "running a budgeted fold, path-plan evaluation, or lattice match.\n\n"
        "`validate` reports whether `loads()` accepts a document, and that is the same "
        "question `convert` settles before emitting anything. A document the encoder "
        "cannot write, such as one spelling a lone surrogate as an escape, is refused "
        "by both at the reader's encoding stage, producing exit status 1 for a refused "
        "operation. `convert` canonicalizes to indented "
        "`json`, compact `json-compact`, or `bytes`; bytes uses the canonical JSON byte "
        "API and is not another syntax.\n\n"
        "`validate --profile blob` additionally checks the fixed blob vocabulary "
        "and descriptors without resolving or opening payloads. `blob list` is the "
        "same metadata-only view in declared blob-item order. `blob put` writes to "
        "the explicit content-addressed path `DIR/sha256/SHA256`; `blob get` and "
        "`blob verify` verify size and digest while reading. The commands never "
        "search another directory or use the network. Graph and bundle operands "
        "must be files because strict bundle inspection requires a seekable source. "
        "Payload and bundle outputs also require file paths so verification can "
        "finish before publication; they do not accept `-` for standard output.\n\n"
        "`bundle flatten` embeds all required payloads by default. Repeated `--only` "
        "options instead name the exact embedded set, preserving every other asset "
        "as an explicit link. `bundle unflatten` moves all embedded payloads to the "
        "selected directory store by default, or only the named digests. Both "
        "commands retain graph bytes, declared asset order, durable IDs, and content "
        "identity. Bundle outputs and directory-store writes are published through "
        "temporary files only after verified streaming succeeds.\n\n"
        "`run` consumes a CLI-owned JSONL stream. Its first line is exactly "
        f"`{machine_header}` and each later line has one opcode's public "
        "`to_data()` shape (a repeat body remains nested on that line). Header-only "
        "programs are valid, CRLF and a final line without a newline are accepted, "
        "and whitespace-only lines are rejected. The decoder caps each line at "
        f"{_JSONL_LINE_BYTES // (1024 * 1024)} MiB "
        "and the stream at `MAX_DOCUMENT_BYTES`; public `Repeat` and `Program` enforce "
        "repeat and total expansion bounds.\n\n"
        "`step` reads that same JSONL program and drives the public `steps()` "
        "generator. Its default dump mode writes one deterministic compact JSON "
        "object per yielded `Step.to_data()` value. `--interactive` (or a TTY) "
        "provides `step`/`next`, `continue`, `run-to N`/`break N`, `print`/`inspect`, "
        "`list`, and `quit`. A refused opcode exits 1 after reporting its index and "
        "the last good graph, with no traceback. Interactive programs must come "
        "from a file because stdin carries REPL commands.\n\n"
        "`inspect` reports tiers in graph order and relation declarations in canonical "
        "graph order (qualified-name order), not source declaration order.\n\n"
        "`semirings` lists every algebra this shell can name, with its carrier "
        "boundary, its five declared law checks, and its declared properties. "
        "The listed names are exactly the values `fold --semiring` accepts.\n\n"
        "`fold` evaluates a finite dependency relation with one of those "
        "algebras and emits the public `FoldResult.to_data()` report. `--tier` and "
        "`--transition` are repeatable; `--root` is repeatable and, when omitted, "
        "the roots are the domain items nothing depends on. The valuation carries "
        "the attribute's local name, because that name only ever appears in a "
        "refusal. Two lifts are nameable: `value` embeds the read attribute value "
        "in the carrier, and `one` embeds the semiring's multiplicative identity "
        "regardless of the value. A general lift, a witness order, and an index "
        "product are caller code, so they stay in the Python API; without a "
        "witness order the report's `provenance` is always null, and `--ranked` "
        "is the shell's route to witnesses. `--ranked` needs an algebra that "
        "declares `multiply_preserves_witness_order` and supplies the tie policy "
        "the declaration requires but ranked selection never consults. "
        "`--output-cap` caps ranked witnesses and so requires `--ranked`.\n\n"
        "`select --selector FILE` evaluates a strict selector JSON document. "
        "`select --where TEXT` instead parses a value predicate with "
        "`PredicateSyntax.for_graph`; `--prefix` chooses the default namespace "
        "prefix for unqualified attribute names. The two input forms are mutually "
        "exclusive, and `--prefix` applies only to `--where`.\n\n"
        "`edit` runs one checked operation against a validated graph. Direct "
        "item and boundary operands use TG-PATH. Other feature targets and layer "
        "fact subjects use `document`, `tier:NS|LOCAL`, "
        "`relation-declaration:NS|LOCAL`, `relation:N`, `polyadic:N`, "
        "`relation-id:ID`, or `polyadic-id:ID`. `edit bulk` evaluates "
        "one selector, predicate, or exhaustive match before applying a delete or "
        "attribute operation in displacement-safe order. Structural edits on a "
        "clock-profile graph require both `--clock-profile` and an explicit named "
        "`--rebinding` policy; commands that cannot preserve that profile refuse "
        "those options.\n\n"
        "Every graph-producing edit and patch application can write a dry-run "
        "report, a forward patch, and an inverse patch. Caller annotations are "
        "copied into the patch header and operations; timestamps are never "
        "synthesized. `--max-steps` charges one step for a direct edit, one per "
        "selected bulk callback in addition to selection work, and one per applied "
        "patch operation. `--in-place` writes and validates a temporary graph in "
        "the destination directory before replacing the input atomically. Graph "
        "and side-artifact destinations must differ from every file operand.\n\n"
        "`patch show` writes one JSON object with `patch_version`, "
        "`base_fingerprint`, `target_fingerprint`, `annotations`, and `operations` "
        "fields. `patch invert` and `patch compose` preserve the versioned "
        "fingerprint guards. `diff` emits a deterministic executable patch in the "
        "selected equivalence view, and `program from-graph` emits an exact "
        "from-empty construction program.\n\n"
        "`action` and `react` are library-only. `ActionDeclaration` binds its "
        "behavior as an `ActionFunction` Python callable, and `ReactDeclaration` "
        "also binds a `DeliveryYield` callable. Neither callable has a declarative "
        "or wire form for the CLI to read, so the shell cannot construct either "
        "declaration without inventing an executable callback format.\n\n"
        + _json_input_formats()
        + "\n"
        + _implementation_limits()
        + "\n"
        "## Deterministic stepping example\n\n"
        "For a program whose first opcode declares prefix `s` for `urn:step`, dump "
        "its exact public step states:\n\n"
        "```console\n"
        "$ tiergraph step program.jsonl\n"
        f"{step_output}\n"
        "```\n\n"
        "Each output line is independently parseable JSON.\n\n"
        "## Help\n\n" + help_text + "\n"
    ).encode()


def _replace_directive(text: str, kind: str, body: str) -> str:
    count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal count
        if match.group("kind") != kind:
            return match.group(0)
        count += 1
        return f"<!-- tiergraph:{kind} -->\n{body.rstrip()}\n<!-- /tiergraph:{kind} -->"

    result = DIRECTIVE.sub(replace, text)
    if count != 1:
        raise ValueError(f"expected one {kind!r} directive, found {count}")
    return result


def mixing_bytes() -> bytes:
    """Refresh the worked example's source and output directives."""
    text = MIXING_PATH.read_text(encoding="utf-8")
    source = (ROOT / "examples" / "mixing.py").read_text(encoding="utf-8").rstrip()
    text = _replace_directive(text, "copy-example", f"```python\n{source}\n```")
    execution = subprocess.run(
        (sys.executable, "-m", "examples.mixing"),
        cwd=ROOT,
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    if execution.stderr:
        raise ValueError("mixing example wrote to stderr")
    expected = (json.dumps(run_example(), sort_keys=True) + "\n").encode()
    if execution.stdout != expected:
        raise ValueError("mixing example output bytes do not match run_example()")
    output = execution.stdout.decode().rstrip("\n")
    result = _replace_directive(text, "execute-example", f"```json\n{output}\n```")
    return (result.rstrip() + "\n").encode()


def gate_steps() -> tuple[str, ...]:
    """Return the gate's steps, in the order the makefile's `gate` target runs them."""
    text = MAKEFILE_PATH.read_text(encoding="utf-8")
    return tuple(" ".join(GATE_TARGET.findall(text)).split())


def _gate_step_gloss(step: str) -> str:
    """Return one gate step's gloss, refusing by name when none is written."""
    try:
        return GATE_STEP_PROSE[step]
    except KeyError as error:
        raise ValueError(
            f"gate step {step!r} has no gloss: the makefile's `gate` target runs "
            f"it and CONTRIBUTING.md is rendered from that target, so add an "
            f"entry for {step!r} to GATE_STEP_PROSE in scripts/generate_docs.py "
            f"and rerun `make docs`"
        ) from error


def contributing_bytes() -> bytes:
    """Render the contributor note's gate-step list from the makefile itself."""
    text = CONTRIBUTING_PATH.read_text(encoding="utf-8")
    body = "\n".join(f"- `{step}` — {_gate_step_gloss(step)}" for step in gate_steps())
    result = _replace_directive(text, "gate-steps", body)
    return (result.rstrip() + "\n").encode()


def generated(manifest: Mapping[str, Any]) -> dict[Path, bytes]:
    """Return every generated artifact without reading git metadata or time."""
    return {
        API_PATH: api_bytes(manifest),
        CLI_PATH: cli_bytes(),
        MIXING_PATH: mixing_bytes(),
        CONTRIBUTING_PATH: contributing_bytes(),
    }


def check_prose_constants(
    artifacts: Mapping[Path, bytes],
    entries: Sequence[tuple[Path, str, int]] = PROSE_CONSTANTS,
) -> None:
    """Require each declared phrase to contain its current code constant once."""
    for path, phrase, value in entries:
        content = (
            artifacts[path].decode()
            if path in artifacts
            else path.read_text(encoding="utf-8")
        )
        expected = phrase.format(value=value)
        if content.count(expected) != 1:
            raise ValueError(
                f"prose constant mismatch in {path.relative_to(ROOT)}: {expected!r}"
            )


def check_cli() -> None:
    """Check console and module entry points in subprocesses."""
    commands: Sequence[Sequence[str]] = (
        (sys.executable, "-m", "tiergraph"),
        (sys.executable, "-m", "tiergraph", "--version"),
    )
    for command in commands:
        result = subprocess.run(
            command,
            cwd=ROOT,
            check=False,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
        )
        if result.returncode != 0 or result.stderr:
            raise ValueError(f"CLI contract failed for {command!r}")
    version = subprocess.run(
        (sys.executable, "-m", "tiergraph", "--version"),
        cwd=ROOT,
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        text=True,
    )
    if json.loads(version.stdout) != {"version": tiergraph.__version__}:
        raise ValueError("CLI version JSON does not match the package version")


def _claim_selection_returns_nodeset() -> None:
    namespace = tiergraph.QualifiedName("urn:docs", "items")
    graph = tiergraph.Graph(
        (tiergraph.NamespaceDeclaration("docs", "urn:docs"),),
        (tiergraph.Tier(tiergraph.TierDeclaration(namespace, "Items"), ()),),
        (),
    )
    result = tiergraph.evaluate_selection(graph, tiergraph.TierSelector(namespace))
    if not isinstance(result, tiergraph.NodeSet):
        raise ValueError("selection evaluation claim did not return NodeSet")


def _claim_build_results() -> None:
    if inspect.signature(tiergraph.execute).return_annotation != "Graph":
        raise ValueError("execute return annotation is not Graph")
    if inspect.signature(tiergraph.Program.unroll).return_annotation != "AsBuilt":
        raise ValueError("Program.unroll return annotation is not AsBuilt")


LIVE_CLAIMS: Mapping[str, Callable[[], None]] = {
    "selection-tuple": _claim_selection_returns_nodeset,
    "build-results": _claim_build_results,
}


def check_live_claims(manifest: Mapping[str, Any]) -> None:
    """Require exact recognized prose and execute its named predicate."""
    for claim in manifest["live_claims"]:
        page = ROOT / claim["page"]
        text = page.read_text(encoding="utf-8")
        expected = claim["text"]
        if text.count(expected) != 1:
            raise ValueError(
                f"live claim {claim['name']!r} is missing or changed in {claim['page']}"
            )
        try:
            predicate = LIVE_CLAIMS[claim["name"]]
        except KeyError as error:
            raise ValueError(f"unknown live claim {claim['name']!r}") from error
        predicate()


def execute_python_fences(pages: set[Path]) -> None:
    """Execute and type-check reader Python fences, with one module per page."""
    pattern = re.compile(r"^```([A-Za-z0-9_-]+)\n(.*?)^```$", re.MULTILINE | re.DOTALL)
    with tempfile.TemporaryDirectory() as directory:
        modules = []
        for page_number, page in enumerate(sorted(pages)):
            fences = pattern.findall(page.read_text(encoding="utf-8"))
            sources = [body for language, body in fences if language == "python"]
            namespace: dict[str, object] = {"__name__": "__docs_fence__"}
            python_index = 0
            for fence_index, (language, source) in enumerate(fences):
                if language != "python":
                    continue
                python_index += 1
                output = io.StringIO()
                try:
                    with contextlib.redirect_stdout(output):
                        exec(
                            compile(source, f"{page} fence {python_index}", "exec"),
                            namespace,
                        )
                except Exception as error:
                    raise ValueError(
                        f"python fence failed in {page.relative_to(ROOT)} "
                        f"#{python_index}: {type(error).__name__}: {error}"
                    ) from error
                expected = (
                    fences[fence_index + 1][1]
                    if fence_index + 1 < len(fences)
                    and fences[fence_index + 1][0] == "text"
                    else ""
                )
                actual = output.getvalue()
                if actual != expected:
                    raise ValueError(
                        f"python fence output mismatch in {page.relative_to(ROOT)} "
                        f"#{python_index}: got {actual!r}; expected {expected!r}"
                    )
            if sources:
                module = Path(directory) / f"reader_fences_{page_number}.py"
                module.write_text("\n\n".join(sources), encoding="utf-8")
                modules.append(module)
        result = subprocess.run(
            (
                sys.executable,
                "-m",
                "mypy",
                "--config-file",
                str(ROOT / "pyproject.toml"),
                *map(str, modules),
            ),
            cwd=ROOT,
            check=False,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
        )
        if result.returncode != 0:
            raise ValueError(f"reader Python fence type-check failed:\n{result.stdout}")


def validate_pages(manifest: Mapping[str, Any]) -> None:
    """Require the declared reader set to equal the tracked documentation set."""
    expected = {ROOT / path for path in manifest["reader_pages"]}
    actual = {ROOT / "README.md", *ROOT.joinpath("docs").rglob("*.md")}
    if expected != actual:
        raise ValueError(
            f"reader page mismatch; missing={sorted(map(str, actual - expected))}; "
            f"unknown={sorted(map(str, expected - actual))}"
        )
    generated_pages = {ROOT / path for path in manifest["generated_pages"]}
    classified = manifest["fences"]
    for page in actual - generated_pages:
        languages = re.findall(
            r"^```([A-Za-z0-9_-]+)$",
            page.read_text(encoding="utf-8"),
            flags=re.MULTILINE,
        )
        expected_languages = classified.get(page.relative_to(ROOT).as_posix(), [])
        if languages != expected_languages:
            raise ValueError(
                f"unclassified fence in {page.relative_to(ROOT)}; "
                f"manifest={expected_languages!r}; page={languages!r}"
            )
    execute_python_fences(actual - generated_pages)


def main(argv: list[str] | None = None) -> int:
    """Write artifacts or check that committed copies match generation."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest()
        validate_manifest(manifest)
        validate_pages(manifest)
        check_live_claims(manifest)
        check_cli()
        artifacts = generated(manifest)
        check_prose_constants(artifacts)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(str(error)) from error
    if args.check:
        for path, expected in artifacts.items():
            if path.read_bytes() != expected:
                raise SystemExit(f"{path.relative_to(ROOT)} is stale; regenerate it")
        return 0
    for path, content in artifacts.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
