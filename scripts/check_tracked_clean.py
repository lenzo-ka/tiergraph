"""Refuse local leaks and unapproved external references in shipped surfaces.

The external-reference check is inverted: URLs, imports, dependency names,
email addresses, and bare domains must be explicitly allowed. It therefore
fails closed when a cited project uses an unanticipated spelling or is renamed.
Bare prose has no reference syntax, so salted SHA-256 digests cover it instead.
The forbidden names are not written down anywhere in the repository, and no
precomputed rainbow table answers the question for free. This does not provide
secrecy: the salt is tracked so CI can reproduce digests from a clean checkout.
A reader can confirm a name they already guess in milliseconds, and a short
lowercase name falls to an ordinary dictionary sweep.

A denylist pins the spellings listed, so a sibling nobody listed is still
invisible in bare prose. A name that is also ordinary vocabulary cannot be listed
without false positives. Inverting this check -- allowlisting known-good prose
instead -- would mean reviewing every distinct word the documentation uses:
2,007 unique alphanumeric tokens across README.md and docs/** as of this commit,
a set that moves whenever the prose does. Reviewing that on every commit costs
more than the check is worth, and a non-English word would have to be
allowlisted by hand or covered by an inflected word list larger than the
distribution shipping it.

Both checks run over the git index rather than the working tree: untracked
scratch is exactly where local material is allowed to live.
"""

from __future__ import annotations

import ast
import hashlib
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
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
CANDIDATE = re.compile(r"[A-Za-z0-9]+")
DENIED_DIGESTS_PATH: Path = ROOT / "denied-name-digests.txt"


@dataclass(frozen=True)
class Denylist:
    """Hold the salt and accepted digests for denied-name matching."""

    salt: bytes
    digests: frozenset[str]


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


def digest(name: str, salt: bytes) -> str:
    """Return the salted digest of one normalized alphanumeric candidate."""
    normalized = name.strip().lower()
    if CANDIDATE.fullmatch(normalized) is None:
        raise ValueError("candidate must be exactly one nonempty alphanumeric run")
    return hashlib.sha256(salt + normalized.encode("utf-8")).hexdigest()


def denied_digests(path: Path) -> Denylist:
    """Parse and validate a repository denied-name digest file."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise ValueError(
            f"denylist {path} is missing; this check runs from a repository "
            "checkout, not from an installed distribution"
        ) from error
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    salt_lines = [line for line in lines if line.startswith("salt ")]
    if not salt_lines:
        raise ValueError(f"denylist {path} contains no salt line")
    if len(salt_lines) != 1 or lines[0] != salt_lines[0]:
        raise ValueError(f"denylist {path} has an invalid salt line")
    salt_hex = salt_lines[0].removeprefix("salt ")
    if len(salt_hex) < 16:
        raise ValueError(f"denylist {path} salt is shorter than 16 hex characters")
    if len(salt_hex) % 2 or re.fullmatch(r"[0-9A-Fa-f]+", salt_hex) is None:
        raise ValueError(f"denylist {path} salt is malformed")
    salt = bytes.fromhex(salt_hex)
    digests = lines[1:]
    if not digests:
        raise ValueError(f"denylist {path} contains no digests")
    if any(re.fullmatch(r"[0-9a-f]{64}", value) is None for value in digests):
        raise ValueError(f"denylist {path} contains an invalid digest line")
    if len(digests) != len(set(digests)):
        raise ValueError(f"denylist {path} contains duplicate digests")
    if digests != sorted(digests):
        raise ValueError(f"denylist {path} digests are not sorted")
    return Denylist(salt=salt, digests=frozenset(digests))


def name_leaks(path: Path, denylist: Denylist) -> list[str]:
    """Return one line-numbered message per denied name in one shipped file."""
    text = path.read_text(encoding="utf-8")
    # Maximal runs preserve the old case-insensitive, alphanumeric-boundary
    # regex behavior for every name that digest() accepts.
    return [
        f"{path}:{text.count(chr(10), 0, match.start()) + 1}: "
        "a denied name written in shipped text"
        for match in CANDIDATE.finditer(text)
        if digest(match.group(0), denylist.salt) in denylist.digests
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
    denylist = denied_digests(DENIED_DIGESTS_PATH)
    found.extend(
        message
        for path in paths
        if path.name != SELF and path.is_file() and is_shipped_surface(path)
        for message in name_leaks(path, denylist)
    )
    if not found:
        return 0
    print("tracked files must be publishable as they stand:", file=sys.stderr)
    for message in found:
        print(f"  {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
