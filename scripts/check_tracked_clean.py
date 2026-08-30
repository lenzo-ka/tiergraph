"""Refuse local leaks and unapproved external references in shipped surfaces.

The external-reference check is inverted: URLs, imports, dependency names,
email addresses, and bare domains must be explicitly allowed. It therefore
fails closed when a cited project uses an unanticipated spelling or is renamed.
It cannot see a project named only in bare prose, with no reference-shaped
syntax. A shipped-vocabulary manifest would cover that gap, but a lowercase
alphabetic-word measurement found 3,167 tokens and +103/-2 churn over seven commits;
that review noise would train maintainers to append words without scrutiny. Green
here is consequently not proof that bare prose satisfies the ruling.

Both checks run over the git index rather than the working tree: untracked
scratch is exactly where local material is allowed to live.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

FORBIDDEN: tuple[tuple[str, str], ...] = (
    (r"/Users/[A-Za-z0-9._-]+", "a macOS home-directory path"),
    (r"/home/[A-Za-z0-9._-]+", "a Linux home-directory path"),
    (r"\.ssh/", "a path into an SSH configuration directory"),
    (r"\bfile:///", "an absolute local file URL"),
    (
        r"(?i)generated (?:by|with) (?:an? )?(?:AI|LLM|ChatGPT|Codex)",
        "AI/tool attribution",
    ),
    (r"(?i)as an AI", "AI/tool attribution"),
    (r"(?i)generated (?:at|on) \d{4}-\d{2}-\d{2}", "a generated timestamp"),
)

# Project-owned repository and clone endpoint.
ALLOWED_URL_PREFIXES: dict[str, str] = {
    "github.com/lenzo-ka/tiergraph": "the project repository",
    "github.com/lenzo-ka/tiergraph.git": "the project's Git clone endpoint",
    # RFC 2606 example-domain namespaces used by runnable documentation.
    "example.com/caption": "an example namespace",
    "example.com/captions": "an example namespace",
    "example.com/config": "an example namespace",
    "example.com/doc": "an example namespace",
    "example.com/net": "an example namespace",
    "example.com/pipeline": "an example namespace",
    "example.com/plan": "an example namespace",
    "example.com/score": "an example namespace",
    "example.com/timeline": "an example namespace",
    "example.com/tree": "an example namespace",
    # Published schema and worked-example identifiers.
    "json-schema.org/draft/2020-12": "the JSON Schema vocabulary",
    "tiergraph.dev/examples/mixing": "the worked-example namespace",
    "tiergraph.org/schema/format-": "the published schema identifier family",
}

# Python language and standard-library modules used by shipped code and examples.
_STDLIB_IMPORTS = {
    "argparse",
    "ast",
    "collections",
    "contextlib",
    "contextvars",
    "copy",
    "dataclasses",
    "decimal",
    "enum",
    "fractions",
    "functools",
    "hashlib",
    "html",
    "importlib",
    "inspect",
    "io",
    "itertools",
    "json",
    "math",
    "os",
    "pathlib",
    "re",
    "shutil",
    "subprocess",
    "sys",
    "tempfile",
    "time",
    "tomllib",
    "types",
    "typing",
    "urllib",
}
# This distribution's packages and its in-repository runnable/test modules.
_PROJECT_IMPORTS = {
    "conformance",
    "examples",
    "scripts",
    "semiring_laws",
    "tests",
    "tiergraph",
    "tiergraph_dot",
}
# Declared testing and validation tools imported by the suite.
_TOOL_IMPORTS = {"hypothesis", "jsonschema", "pytest"}
ALLOWED_IMPORTS = _STDLIB_IMPORTS | _PROJECT_IMPORTS | _TOOL_IMPORTS

# Runtime and development distributions declared in pyproject.toml.
ALLOWED_DISTRIBUTIONS = {
    "hatchling",
    "hypothesis",
    "jsonschema",
    "mypy",
    "pydantic",
    "pytest",
    "pytest-cov",
    "ruff",
    "tiergraph",
}

# Hosts used by the allowed URLs; also legitimate when written as bare domains.
ALLOWED_DOMAINS = {
    "example.com",
    "github.com",
    "json-schema.org",
    "tiergraph.dev",
    "tiergraph.org",
}
# No shipped surface currently has a legitimate email address.
ALLOWED_EMAILS: set[str] = set()

URL = re.compile(r"(?:git\+)?(?:https?|ssh)://[^\s<>`\"'{}()[\]]+")
EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,63}\b")
DOMAIN = re.compile(
    r"(?i)(?<![A-Z0-9@.-])(?:[A-Z0-9-]+\.)+(?:com|dev|edu|gov|io|net|org)\b"
)
PYTHON_FENCE = re.compile(r"^```python\n(.*?)^```$", re.MULTILINE | re.DOTALL)

# This file lists the patterns it forbids and the references it allows, so it
# necessarily contains both and is exempt from both of its own checks.
SELF = Path(__file__).name
ROOT = Path(__file__).resolve().parent.parent


def tracked_files() -> list[Path]:
    """Return every file in the git index."""
    listing = subprocess.run(
        ["git", "ls-files", "-z"], check=True, capture_output=True, text=True
    )
    return [Path(name) for name in listing.stdout.split("\0") if name]


def leaks(path: Path) -> list[str]:
    """Return one message per forbidden match in the file."""
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    return [
        f"{path}: {reason} ({match.group(0)!r})"
        for pattern, reason in FORBIDDEN
        for match in re.finditer(pattern, text)
    ]


def _url_prefix(value: str) -> str:
    """Return a URL's lowercase host and at most its first two path segments."""
    parsed = urlsplit(value.rstrip(".,;:!?").removeprefix("git+"))
    segments = [segment for segment in parsed.path.split("/") if segment][:2]
    return (parsed.hostname or "").lower() + (
        f"/{'/'.join(segments)}" if segments else ""
    )


def _imports(source: str, label: str) -> set[str]:
    """Return external-shaped top-level imports from one Python source."""
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        raise ValueError(f"cannot inspect imports in {label}: {error}") from error
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.partition(".")[0])
    found.discard("__future__")
    return found


def _distribution_name(requirement: str) -> str:
    """Return the normalized distribution name at a requirement's front."""
    match = re.match(r"[A-Za-z0-9_.-]+", requirement)
    if match is None:
        raise ValueError(f"cannot inspect dependency requirement {requirement!r}")
    return re.sub(r"[-_.]+", "-", match.group(0)).lower()


def reference_leaks(path: Path) -> list[str]:
    """Return unallowlisted external references extracted from one shipped file."""
    text = path.read_text(encoding="utf-8")
    messages: list[str] = []
    for value in URL.findall(text):
        prefix = _url_prefix(value)
        if not any(
            prefix == allowed or prefix.startswith(f"{allowed}/")
            for allowed in ALLOWED_URL_PREFIXES
        ):
            messages.append(f"{path}: an unallowlisted URL reference ({prefix!r})")
    for value in EMAIL.findall(text):
        if value.lower() not in ALLOWED_EMAILS:
            messages.append(f"{path}: an unallowlisted email address ({value!r})")
    for value in DOMAIN.findall(text):
        domain = value.lower()
        if domain not in ALLOWED_DOMAINS:
            messages.append(f"{path}: an unallowlisted bare domain ({domain!r})")
    sources = [(text, str(path))] if path.suffix == ".py" else []
    if path.suffix == ".md":
        sources.extend(
            (source, f"{path} Python fence #{index}")
            for index, source in enumerate(PYTHON_FENCE.findall(text), start=1)
        )
    for source, label in sources:
        for imported in sorted(_imports(source, label) - ALLOWED_IMPORTS):
            messages.append(f"{path}: an unallowlisted top-level import ({imported!r})")
    if path.name == "pyproject.toml":
        project = tomllib.loads(text)["project"]
        requirements = list(project.get("dependencies", ()))
        requirements.extend(
            requirement
            for group in project.get("optional-dependencies", {}).values()
            for requirement in group
        )
        for requirement in requirements:
            distribution = _distribution_name(requirement)
            if distribution not in ALLOWED_DISTRIBUTIONS:
                messages.append(
                    f"{path}: an unallowlisted distribution ({distribution!r})"
                )
    return messages


def is_shipped_surface(path: Path) -> bool:
    """Return whether the ruling includes this tracked path."""
    return path in {Path("README.md"), Path("pyproject.toml")} or (
        bool(path.parts) and path.parts[0] in {"docs", "src", "tests", "scripts"}
    )


def main() -> int:
    """Check every tracked file. Returns the process exit status."""
    paths = tracked_files()
    found = [
        message
        for path in paths
        if path.name != SELF and path.is_file()
        for message in leaks(path)
    ]
    found.extend(
        message
        for path in paths
        if path.name != SELF and path.is_file() and is_shipped_surface(path)
        for message in reference_leaks(path)
    )
    if not found:
        return 0
    print("tracked files must be publishable as they stand:", file=sys.stderr)
    for message in found:
        print(f"  {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
