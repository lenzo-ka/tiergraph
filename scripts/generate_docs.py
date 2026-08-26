"""Generate and verify documentation derived from the public interfaces."""

from __future__ import annotations

import argparse
import contextlib
import inspect
import io
import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from enum import Enum
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from examples.mixing import run_example  # noqa: E402

import tiergraph  # noqa: E402
import tiergraph_dot  # noqa: E402
from tiergraph.cli import build_parser  # noqa: E402

MANIFEST_PATH = ROOT / "docs" / "manifest.json"
API_PATH = ROOT / "docs" / "reference" / "api.md"
CLI_PATH = ROOT / "docs" / "reference" / "cli.md"
MIXING_PATH = ROOT / "docs" / "guide" / "recognize-and-act.md"
DIRECTIVE = re.compile(
    r"<!-- tiergraph:(?P<kind>[a-z-]+) -->(?P<body>.*?)<!-- /tiergraph:\1 -->",
    re.DOTALL,
)


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    """Load the declarative documentation manifest."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("documentation manifest must be an object")
    return value


def validate_manifest(manifest: Mapping[str, Any]) -> None:
    """Require exact, one-to-one coverage of both shipped export lists."""
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
    semiring = manifest.get("secondary", {}).get("tiergraph.semiring", [])
    semiring_module = __import__("tiergraph.semiring", fromlist=["*"])
    if list(semiring_module.__all__) != list(semiring):
        raise ValueError(
            "tiergraph.semiring export mismatch; "
            f"manifest={list(semiring)!r}; package={list(semiring_module.__all__)!r}"
        )
    build = manifest.get("secondary", {}).get("tiergraph.build", [])
    build_module = __import__("tiergraph.build", fromlist=["*"])
    if list(build_module.__all__) != list(build):
        raise ValueError(
            "tiergraph.build export mismatch; "
            f"manifest={list(build)!r}; package={list(build_module.__all__)!r}"
        )


def _signature(value: object) -> str:
    try:
        return str(inspect.signature(cast(Callable[..., object], value)))
    except (TypeError, ValueError):
        return ""


def _entry(module: object, name: str, descriptions: Mapping[str, str]) -> str:
    value = getattr(module, name)
    heading = f"### `{name}`"
    if name in descriptions:
        return f"{heading}\n\n{descriptions[name]} Current value: `{value}`."
    doc = inspect.getdoc(value)
    if not doc:
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
        module = __import__(module_name, fromlist=["*"])
        stability = (
            "This module is a supported secondary API."
            if module_name == "tiergraph.semiring"
            else "This module is importable and usable, but carries no "
            f"API-stability promise at version {tiergraph.__version__}."
        )
        parts.extend((f"### `{module_name}`", "", stability, ""))
        if module_name == "tiergraph.build":
            parts.extend(
                (
                    "Builder notation errors raise the directly importable "
                    "`tiergraph.build.BuilderError`, a `ValueError` subclass. "
                    "It is not part of the module's star-exported surface.",
                    "",
                )
            )
        parts.append("\n\n".join(_entry(module, name, {}) for name in names))
    parts.extend(("", "## Companion package", ""))
    parts.append(
        "\n\n".join(
            _entry(tiergraph_dot, name, {})
            for name in manifest["companion_exports"]["tiergraph_dot"]
        )
    )
    return ("\n".join(parts).rstrip() + "\n").encode()


def cli_bytes() -> bytes:
    """Render normalized parser help and the checked command contracts."""
    parser = build_parser()
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
    return (
        "# CLI reference\n\n"
        "The `tiergraph` command prints help when called without arguments. "
        "`--version` prints one JSON object and exits successfully.\n\n"
        "`tiergraph.cli.build_parser()` is importable and usable, but carries no "
        f"API-stability promise at version {tiergraph.__version__}.\n\n"
        "## Contracts\n\n"
        "Every command accepts `-` as stdin. Document-producing commands write to "
        "stdout by default or to `-o/--output`; diagnostics go only to stderr. "
        "Exit status 0 means success, 1 means invalid input or a refused operation, "
        "2 means command-line usage error, and 3 means an I/O or encoding failure.\n\n"
        "`validate` reports whether `loads()` accepts a document. This is deliberately "
        "separate from emission: a loads-accepted value such as an escaped lone "
        "surrogate can still be refused cleanly by `convert` during strict UTF-8 "
        "encoding. `convert` canonicalizes to indented `json`, compact `json-compact`, "
        "or `bytes`; bytes uses the canonical JSON byte API and is not another syntax.\n\n"
        "`run` consumes a CLI-owned JSONL stream. Its first line is exactly "
        '`{"machine_version":"1"}` and each later line has one opcode\'s public '
        "`to_data()` shape (a repeat body remains nested on that line). Header-only "
        "programs are valid, CRLF and a final line without a newline are accepted, "
        "and whitespace-only lines are rejected. The decoder caps each line at 1 MiB "
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
        "## Deterministic stepping example\n\n"
        "For a program whose first opcode declares prefix `s` for `urn:step`, dump "
        "its exact public step states:\n\n"
        "```console\n"
        "$ tiergraph step program.jsonl\n"
        '{"graph":{"attribute_declarations":[],"attributes":[],"namespaces":[{"namespace":"urn:step","prefix":"s"}],"polyadic_relations":[],"position_values":[],"relation_declarations":[],"relations":[],"tiers":[]},"index":0,"opcode":{"declaration":{"namespace":"urn:step","prefix":"s"},"opcode":"declare_namespace"}}\n'
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
    )
    if execution.stderr:
        raise ValueError("mixing example wrote to stderr")
    expected = (json.dumps(run_example(), sort_keys=True) + "\n").encode()
    if execution.stdout != expected:
        raise ValueError("mixing example output bytes do not match run_example()")
    output = execution.stdout.decode().rstrip("\n")
    result = _replace_directive(text, "execute-example", f"```json\n{output}\n```")
    return (result.rstrip() + "\n").encode()


def generated(manifest: Mapping[str, Any]) -> dict[Path, bytes]:
    """Return every generated artifact without reading git metadata or time."""
    return {
        API_PATH: api_bytes(manifest),
        CLI_PATH: cli_bytes(),
        MIXING_PATH: mixing_bytes(),
    }


def check_cli() -> None:
    """Check console and module entry points in subprocesses."""
    commands: Sequence[Sequence[str]] = (
        (sys.executable, "-m", "tiergraph"),
        (sys.executable, "-m", "tiergraph", "--version"),
    )
    for command in commands:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        if result.returncode != 0 or result.stderr:
            raise ValueError(f"CLI contract failed for {command!r}")
    version = subprocess.run(
        (sys.executable, "-m", "tiergraph", "--version"),
        cwd=ROOT,
        check=True,
        capture_output=True,
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
    result = tiergraph.select(graph, (tiergraph.TierSelector(graph, namespace),))
    if not isinstance(result, tiergraph.NodeSet):
        raise ValueError("select tuple claim did not return NodeSet")


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
    pattern = re.compile(r"^```python\n(.*?)^```$", re.MULTILINE | re.DOTALL)
    with tempfile.TemporaryDirectory() as directory:
        modules = []
        for page_number, page in enumerate(sorted(pages)):
            sources = pattern.findall(page.read_text(encoding="utf-8"))
            namespace: dict[str, object] = {"__name__": "__docs_fence__"}
            for index, source in enumerate(sources):
                try:
                    with contextlib.redirect_stdout(io.StringIO()):
                        exec(
                            compile(source, f"{page} fence {index + 1}", "exec"),
                            namespace,
                        )
                except Exception as error:
                    raise ValueError(
                        f"python fence failed in {page.relative_to(ROOT)} "
                        f"#{index + 1}: {type(error).__name__}: {error}"
                    ) from error
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
            capture_output=True,
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
