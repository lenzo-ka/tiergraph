"""Refuse a wire-format change that shrinks the format without saying so.

This gate does not forbid breaking the format. The owner will want to break it,
and a gate that stands in the way of that gets switched off the first time it
does. It is a price tag rather than an obstacle: a change that stops the format
growing is legal, it costs a release-line step, and this gate checks the price
was paid. The refusal names the step that would make the change legal, because
a refusal that tells you the price is actionable and one that only says no
invites a workaround.

That is the shape of the other claims this project keeps. The rewrite claim does
not forbid a collapse, it refuses an undeclared one. Fold exactness does not
forbid an inexact fold, it refuses an unstated claim and refutes a false one. A
reservation may defer anything as long as it says what would discharge it. Here:
the format may stop growing, and when it does the version says so.

Why growth is the property worth holding. The wire is closed -- every object
carries ``additionalProperties: false`` -- so a reader refuses a field it does
not know instead of ignoring it. A document is refused when it fails to
validate. Those two together leave one exposure: a release could change what an
existing field *means* without changing its shape, and an older reader would
validate the document and quietly misread it. Structural validity is not
semantic compatibility. If the format only ever grows -- fields added, none
removed, none narrowed, none redefined -- that exposure closes. An older
document stays a subset of what the newer schema accepts, a newer field is
refused loudly by the closed wire, and no field a reader already understands
shifts under it.

What "additive" means here, derived from the vocabulary this schema actually
uses rather than from a general theory of JSON Schema. A change is additive when
the set of documents the schema accepts does not shrink:

* ``properties``: a new member is additive. Removing one is not -- the wire is
  closed, so a member that vanishes from ``properties`` becomes a member the
  reader refuses.
* ``required``: dropping a name is additive. Adding one is not; it invalidates
  every existing document that omitted it.
* ``additionalProperties``: ``false`` to ``true`` is additive. The reverse
  closes an object that was open.
* ``enum``: a superset is additive, a subset is not. ``const`` is read as the
  one-member enum it is, so ``const`` widening into an enum that contains it
  reads as the widening it is.
* ``type``: a superset of the accepted types is additive.
* ``minLength``, ``minimum``: lowering or dropping a bound is additive, raising
  or introducing one is not.
* ``oneOf``, ``anyOf``: a new branch is additive, a removed branch is not, and
  ``anyOf`` narrowing to ``oneOf`` is not.
* ``items``: dropping the constraint is additive, introducing one is not;
  otherwise the item subschema is compared like any other.
* ``$id`` and the ``format_version`` ``const`` carry the format stamp and change
  at every format release, so they are excluded -- but not on trust. The
  exclusion holds only if each document stamps its version in exactly the
  expected shape, and the identifier agrees with the stamp. A stamp site edited
  into anything else is reported rather than skipped, so the exclusion cannot be
  used to carry a change through.
* ``title``, ``description``, ``$comment`` and ``$schema`` do not constrain a
  document, so a change to them is additive.

Two limits, stated so a green run is not read for more than it earns. A changed
``pattern`` is not decided: regular-language containment is decidable but not
decided here, so a changed pattern is reported as not shown to grow, and the
price of a genuine widening is the same line step as a narrowing. And a new
``oneOf`` branch is treated as additive although ``oneOf`` demands exactly one
match: a branch overlapping an existing one could invalidate a document that
matched only the old branch. Whether two subschemas overlap is not decided here.
The codec conformance suite is the backstop for both.

A keyword this gate does not know is reported, never skipped. The set of
keywords it decides is written down below, and anything outside it is a change
whose direction was not established -- the same posture the publishability gate
takes toward an external reference nobody allowed.

The baseline is the schema recovered from the newest release tag in the current
release line, read out of git rather than kept as a second committed copy. A
checked-in baseline can be edited in the same commit as the change it is meant
to catch, which makes the gate agree with whatever it is shown; that failure is
silent and self-defeating. A tag can be moved, but moving one is a deliberate,
privileged act that the publish workflow's tag-to-version assertion and PyPI's
immutability already make expensive. The cost accepted in exchange is that a
checkout without tags cannot run this gate at all -- and it then refuses rather
than passing, because a green run that compared against nothing reads as
verification without being any.

The release line is the unit of compatibility, and the rule over it is one rule
rather than two: a non-additive change must move the position that this
project's versioning says carries breaking changes. Which position that is
follows from the major being zero or not, so nothing here needs editing at 1.0.
Pre-1.0 the leading zero puts breakage at the minor, so a break costs a minor
step and a patch release must grow or leave the format alone; from 1.0 the major
carries it and a break costs a major step. That is the repository's own claim
and not a stricter one invented beside it -- ``README.md`` says "every published
pre-1.0 release" is alpha software and that before 1.0 "a 0.X.0 release is in
effect a major release", and ``SECURITY.md`` supports "the latest published
pre-1.0 release". Everything is unstable until 1.0. The price is lower now on
purpose. Lower is not free: fair game means a break may be taken, not that it may
be taken without saying so.

Between a version bump and the tag that releases it, the current line has no
release yet. The gate does not fall silent there: it compares against the newest
released line, prints every break it finds under a heading naming the step that
authorizes them, and exits zero. The break is still named at the moment it is
taken, which is the whole point; and the opening closes by itself, because the
first tag in the new line becomes the baseline the rest of that line is held to.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from tiergraph import __version__

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = Path("schema/tiergraph.schema.json")
VERSION_PATH = Path("src/tiergraph/__init__.py")
# The one property whose value is the format stamp, and the identifier that
# repeats it. Both move at every format release and are excluded from the
# comparison, but only after ``stamp`` confirms each is still exactly a stamp.
VERSION_SITE = "/properties/format_version"
SCHEMA_ID = "https://tiergraph.org/schema/format-{version}.json"

# Keywords that annotate without constraining: a document's validity does not
# depend on them, so any change to one is additive.
ANNOTATIONS: frozenset[str] = frozenset({"$comment", "$schema", "description", "title"})
# Keywords this gate decides. ``const`` is normalized into ``enum`` before the
# comparison and so is decided by the enum rule. Everything outside this set is
# reported as undecided rather than passed over.
STRUCTURAL: frozenset[str] = frozenset(
    {
        "$defs",
        "$id",
        "$ref",
        "additionalProperties",
        "anyOf",
        "const",
        "enum",
        "items",
        "minLength",
        "minimum",
        "oneOf",
        "pattern",
        "properties",
        "required",
        "type",
    }
)
KNOWN: frozenset[str] = ANNOTATIONS | STRUCTURAL

Version = tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class Finding:
    """One place the format did not grow, and whether that was established."""

    where: str
    detail: str
    decided: bool


@dataclass(frozen=True, slots=True)
class Resolved:
    """A subschema with every leading reference followed, and where it lives."""

    where: str
    node: dict[str, object]


def parse_version(text: str) -> Version | None:
    """Return the three release numbers in a version, or None if it is not one."""
    parts = text.strip().split(".")
    release_part_count = 3
    if len(parts) != release_part_count or not all(part.isdigit() for part in parts):
        return None
    major, minor, patch = (int(part) for part in parts)
    return (major, minor, patch)


def spell(version: Version) -> str:
    """Return the dotted spelling of a release version."""
    return ".".join(str(part) for part in version)


def spell_line(version: Version) -> str:
    """Return how this project names the compatibility line a version is in."""
    major, minor = release_line(version)
    return f"{major}.{minor}.x" if major == 0 else f"{major}.x"


def release_line(version: Version) -> tuple[int, int]:
    """Return the compatibility line a version belongs to.

    The line is named by the position below the one that carries breaking
    changes, so advancing the line is exactly taking the step a break costs.
    Under semantic versioning a leading zero puts that position at the minor,
    making the line ``0.MINOR``; from 1.0 the major carries it and the line is
    ``MAJOR``. Stating it as one rule over the position rather than as two cases
    is deliberate: a rule that needs editing at 1.0 will not be edited at 1.0.
    """
    major, minor, _ = version
    return (0, minor) if major == 0 else (major, 0)


def next_line(version: Version) -> Version:
    """Return the smallest version that advances past this one's line."""
    major, minor, _ = version
    return (0, minor + 1, 0) if major == 0 else (major + 1, 0, 0)


def git_output(arguments: Sequence[str], cwd: Path = ROOT) -> str | None:
    """Return a git command's output, or None when git refuses to answer."""
    try:
        result = subprocess.run(
            ["git", *arguments], check=True, capture_output=True, text=True, cwd=cwd
        )
    except subprocess.CalledProcessError:
        return None
    return result.stdout


def released(listing: str) -> dict[Version, str]:
    """Return each release version in a tag listing with the tag that names it."""
    found: dict[Version, str] = {}
    for line in listing.splitlines():
        tag = line.strip()
        if not tag.startswith("v"):
            continue
        version = parse_version(tag[1:])
        if version is not None:
            found[version] = tag
    return found


def stamp(document: object) -> str | None:
    """Return the format version a schema stamps, if it stamps one exactly.

    The two version-bearing sites are excluded from the growth comparison, so
    this refuses to recognize anything but a bare stamp: the ``format_version``
    property must be exactly one ``const`` string, and the document identifier
    must be the one that version spells. A site edited into anything else is not
    a stamp and is not excluded.
    """
    if not isinstance(document, dict):
        return None
    properties = document.get("properties")
    if not isinstance(properties, dict):
        return None
    site = properties.get("format_version")
    if not isinstance(site, dict) or set(site) != {"const"}:
        return None
    version = site["const"]
    if not isinstance(version, str):
        return None
    if document.get("$id") != SCHEMA_ID.format(version=version):
        return None
    return version


def at(document: object, pointer: str) -> object:
    """Return the node a local JSON pointer names, or None if it names none."""
    node = document
    for token in pointer.removeprefix("#").split("/")[1:]:
        key = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def resolve(node: object, document: object, where: str) -> Resolved | str:
    """Follow leading references to a subschema, or say why that cannot be done."""
    seen: set[str] = set()
    current = node
    pointer = where
    while True:
        if not isinstance(current, dict):
            return "a subschema here is not an object"
        reference = current.get("$ref")
        if reference is None:
            return Resolved(pointer, current)
        if len(current) != 1:
            return "a reference here carries sibling keywords"
        if not isinstance(reference, str) or not reference.startswith("#/"):
            return f"a reference here is not a local pointer ({reference!r})"
        if reference in seen:
            return f"a chain of references here closes on {reference}"
        seen.add(reference)
        target = at(document, reference)
        if target is None:
            return f"a reference here does not resolve ({reference})"
        pointer = reference.removeprefix("#")
        current = target


def normalize(node: dict[str, object]) -> dict[str, object] | None:
    """Rewrite ``const`` as the one-member enum it is, so a widening reads as one.

    A node stating both is not normalized: the pair is a constraint this gate
    has no rule for, and reporting it is better than guessing at it.
    """
    if "const" not in node:
        return node
    if "enum" in node:
        return None
    rewritten = {key: value for key, value in node.items() if key != "const"}
    rewritten["enum"] = [node["const"]]
    return rewritten


def _members(values: object) -> list[str] | None:
    """Return canonical spellings of a value list, or None if it is not one."""
    if not isinstance(values, list):
        return None
    return [json.dumps(value, sort_keys=True) for value in values]


def _bound(
    where: str, baseline: dict[str, object], current: dict[str, object], keyword: str
) -> list[Finding]:
    """Compare one lower bound: raising or introducing it refuses a document."""
    after = current.get(keyword)
    if after is None:
        return []
    before = baseline.get(keyword)
    if not isinstance(after, int | float) or isinstance(after, bool):
        return [Finding(where, f"{keyword} is not a number", False)]
    if before is None:
        return [Finding(where, f"the bound {keyword} {after} was introduced", True)]
    if not isinstance(before, int | float) or isinstance(before, bool):
        return [Finding(where, f"the released {keyword} is not a number", False)]
    if after > before:
        return [
            Finding(where, f"the bound {keyword} rose from {before} to {after}", True)
        ]
    return []


def _types(_where: str, node: dict[str, object]) -> tuple[set[str] | None, str | None]:
    """Return the type names a subschema accepts, or why they could not be read."""
    declared = node.get("type")
    if declared is None:
        return (None, None)
    if isinstance(declared, str):
        return ({declared}, None)
    if isinstance(declared, list) and all(isinstance(name, str) for name in declared):
        return (set(declared), None)
    return (None, "a type is neither a name nor a list of names")


def _type_change(
    where: str, baseline: dict[str, object], current: dict[str, object]
) -> list[Finding]:
    """Compare the accepted JSON types: losing one refuses a document."""
    before, refusal = _types(where, baseline)
    if refusal is not None:
        return [Finding(where, f"in the released schema, {refusal}", False)]
    after, refusal = _types(where, current)
    if refusal is not None:
        return [Finding(where, refusal, False)]
    if after is None:
        return []
    if before is None:
        names = ", ".join(sorted(after))
        return [Finding(where, f"the type constraint {names} was introduced", True)]
    lost = sorted(before - after)
    if lost:
        return [
            Finding(where, f"the type {', '.join(lost)} is no longer accepted", True)
        ]
    return []


def _enum_change(
    where: str, baseline: dict[str, object], current: dict[str, object]
) -> list[Finding]:
    """Compare accepted values: a superset is additive, a subset is not."""
    if "enum" not in current:
        return []
    after = _members(current["enum"])
    if after is None:
        return [Finding(where, "an enum is not a list of values", False)]
    if "enum" not in baseline:
        return [
            Finding(where, f"the value set was constrained to {', '.join(after)}", True)
        ]
    before = _members(baseline["enum"])
    if before is None:
        return [Finding(where, "the released enum is not a list of values", False)]
    lost = [value for value in before if value not in after]
    if lost:
        return [Finding(where, f"the value {', '.join(lost)} was dropped", True)]
    return []


def _pattern_change(
    where: str, baseline: dict[str, object], current: dict[str, object]
) -> list[Finding]:
    """Compare patterns: introducing one refuses documents, changing one is undecided."""
    after = current.get("pattern")
    if after is None:
        return []
    before = baseline.get("pattern")
    if before is None:
        return [Finding(where, f"the pattern {after!r} was introduced", True)]
    if before == after:
        return []
    return [
        Finding(
            where,
            f"the pattern changed from {before!r} to {after!r}; this gate does not "
            "decide whether one regular language contains the other",
            False,
        )
    ]


def _names(values: object) -> list[str] | None:
    """Return a list of names, or None if the value is not one."""
    if not isinstance(values, list) or not all(
        isinstance(value, str) for value in values
    ):
        return None
    return [value for value in values if isinstance(value, str)]


def _required_change(
    where: str, baseline: dict[str, object], current: dict[str, object]
) -> list[Finding]:
    """Compare required members: a new one invalidates every document omitting it."""
    before = _names(baseline.get("required", []))
    after = _names(current.get("required", []))
    if before is None or after is None:
        return [Finding(where, "a required list is not a list of names", False)]
    return [
        Finding(where, f"the property {name!r} became required", True)
        for name in sorted(set(after) - set(before))
    ]


def _closed(node: dict[str, object]) -> bool | None:
    """Return whether unlisted properties are refused, or None if that is a schema."""
    declared = node.get("additionalProperties", True)
    if not isinstance(declared, bool):
        return None
    return not declared


def _openness_change(
    where: str, baseline: dict[str, object], current: dict[str, object]
) -> list[Finding]:
    """Compare openness: closing an object that was open refuses documents."""
    before = _closed(baseline)
    after = _closed(current)
    if before is None or after is None:
        return [Finding(where, "additionalProperties is a subschema", False)]
    if after and not before:
        return [Finding(where, "unlisted properties are now refused", True)]
    return []


def _identifier_change(
    where: str, baseline: dict[str, object], current: dict[str, object]
) -> list[Finding]:
    """Report an identifier away from the root, where the stamp rule does not reach."""
    if where != "" and ("$id" in baseline or "$id" in current):
        return [Finding(where, "a subschema declares its own $id", False)]
    return []


def _branches(node: dict[str, object]) -> tuple[str | None, object, str | None]:
    """Return which branch keyword a subschema uses and the branches under it."""
    present = [keyword for keyword in ("oneOf", "anyOf") if keyword in node]
    if len(present) > 1:
        return (None, None, "a subschema states both oneOf and anyOf")
    if not present:
        return (None, None, None)
    return (present[0], node[present[0]], None)


def _branch_key(branch: object) -> str | None:
    """Return the key a branch is paired by: its reference, else its declared type."""
    if not isinstance(branch, dict):
        return None
    reference = branch.get("$ref")
    if isinstance(reference, str):
        return f"ref {reference}"
    return f"type {json.dumps(branch.get('type'), sort_keys=True)}"


def _keyed(branches: object) -> dict[str, object] | str:
    """Return branches by key, or say why they cannot be paired."""
    if not isinstance(branches, list):
        return "a branch list is not a list"
    keyed: dict[str, object] = {}
    for branch in branches:
        key = _branch_key(branch)
        if key is None:
            return "a branch is not a subschema"
        if key in keyed:
            return f"two branches share the key {key}, so they cannot be paired"
        keyed[key] = branch
    return keyed


class _Comparison:
    """Walk two schemas together and collect every place the format did not grow."""

    def __init__(self, baseline: object, current: object) -> None:
        """Hold both documents and the pairs of locations already compared."""
        self.baseline = baseline
        self.current = current
        self.findings: list[Finding] = []
        self.seen: set[tuple[str, str]] = set()

    def compare(self, where: str, before: object, after: object) -> None:
        """Compare one location in both schemas, then descend through it."""
        if where == VERSION_SITE:
            return
        resolved_before = resolve(before, self.baseline, where)
        if isinstance(resolved_before, str):
            self.findings.append(
                Finding(where, f"in the released schema, {resolved_before}", False)
            )
            return
        resolved_after = resolve(after, self.current, where)
        if isinstance(resolved_after, str):
            self.findings.append(Finding(where, resolved_after, False))
            return
        pair = (resolved_before.where, resolved_after.where)
        if pair in self.seen:
            return
        self.seen.add(pair)
        self._subschemas(
            resolved_after.where, resolved_before.node, resolved_after.node
        )

    def _subschemas(
        self, where: str, before: dict[str, object], after: dict[str, object]
    ) -> None:
        """Compare two resolved subschemas keyword by keyword, then descend."""
        baseline = normalize(before)
        current = normalize(after)
        if baseline is None or current is None:
            self.findings.append(
                Finding(where, "a subschema states both const and enum", False)
            )
            return
        for keyword in sorted((set(baseline) | set(current)) - KNOWN):
            self.findings.append(
                Finding(
                    where,
                    f"this gate does not decide the keyword {keyword!r}; teach it "
                    "the keyword before changing the format with it",
                    False,
                )
            )
        self.findings.extend(_identifier_change(where, baseline, current))
        self.findings.extend(_type_change(where, baseline, current))
        self.findings.extend(_enum_change(where, baseline, current))
        self.findings.extend(_pattern_change(where, baseline, current))
        self.findings.extend(_bound(where, baseline, current, "minLength"))
        self.findings.extend(_bound(where, baseline, current, "minimum"))
        self.findings.extend(_required_change(where, baseline, current))
        self.findings.extend(_openness_change(where, baseline, current))
        self._properties(where, baseline, current)
        self._items(where, baseline, current)
        self._branch_sets(where, baseline, current)

    def _properties(
        self, where: str, before: dict[str, object], after: dict[str, object]
    ) -> None:
        """Compare named members: a member the closed wire loses is a member refused."""
        baseline = before.get("properties", {})
        current = after.get("properties", {})
        if not isinstance(baseline, dict) or not isinstance(current, dict):
            self.findings.append(Finding(where, "properties is not an object", False))
            return
        closed = _closed(after)
        for name in sorted(set(baseline) - set(current)):
            if closed:
                self.findings.append(
                    Finding(where, f"the property {name!r} was removed", True)
                )
        for name in sorted(set(baseline) & set(current)):
            token = name.replace("~", "~0").replace("/", "~1")
            self.compare(f"{where}/properties/{token}", baseline[name], current[name])

    def _items(
        self, where: str, before: dict[str, object], after: dict[str, object]
    ) -> None:
        """Compare the array-element constraint, introducing one being a refusal."""
        current = after.get("items")
        if current is None:
            return
        baseline = before.get("items")
        if isinstance(current, list) or isinstance(baseline, list):
            self.findings.append(
                Finding(where, "items states a positional list of subschemas", False)
            )
            return
        if baseline is None:
            self.findings.append(
                Finding(where, "an element constraint was introduced", True)
            )
            return
        self.compare(f"{where}/items", baseline, current)

    def _branch_sets(
        self, where: str, before: dict[str, object], after: dict[str, object]
    ) -> None:
        """Compare alternatives: a lost branch, or a narrowed keyword, refuses."""
        baseline_keyword, baseline_branches, refusal = _branches(before)
        if refusal is not None:
            self.findings.append(
                Finding(where, f"in the released schema, {refusal}", False)
            )
            return
        current_keyword, current_branches, refusal = _branches(after)
        if refusal is not None:
            self.findings.append(Finding(where, refusal, False))
            return
        if current_keyword is None:
            return
        if baseline_keyword is None:
            self.findings.append(
                Finding(
                    where, f"a {current_keyword} branch constraint was introduced", True
                )
            )
            return
        if baseline_keyword == "anyOf" and current_keyword == "oneOf":
            self.findings.append(Finding(where, "anyOf was narrowed to oneOf", True))
        keyed_baseline = _keyed(baseline_branches)
        if isinstance(keyed_baseline, str):
            self.findings.append(
                Finding(where, f"in the released schema, {keyed_baseline}", False)
            )
            return
        keyed_current = _keyed(current_branches)
        if isinstance(keyed_current, str):
            self.findings.append(Finding(where, keyed_current, False))
            return
        order = list(keyed_current)
        for key in keyed_baseline:
            if key not in keyed_current:
                self.findings.append(
                    Finding(where, f"the branch {key} was removed", True)
                )
                continue
            index = order.index(key)
            self.compare(
                f"{where}/{current_keyword}/{index}",
                keyed_baseline[key],
                keyed_current[key],
            )


def compare(baseline: object, current: object) -> list[Finding]:
    """Return every place the current schema fails to accept the released one."""
    findings: list[Finding] = []
    for description, document in (
        ("the released schema", baseline),
        ("the current schema", current),
    ):
        if stamp(document) is None:
            findings.append(
                Finding(
                    "",
                    f"{description} does not stamp its format version as a bare "
                    "const agreeing with its $id, so the two version-bearing sites "
                    "cannot be excluded from this comparison",
                    False,
                )
            )
    if findings:
        return findings
    walk = _Comparison(baseline, current)
    walk.compare("", baseline, current)
    return walk.findings


@dataclass(frozen=True, slots=True)
class Unreadable:
    """Text that was expected to hold a schema and does not parse as one."""

    reason: str


def document(text: str, description: str) -> object | Unreadable:
    """Return a parsed schema, or say why the text is not one.

    The refusal is its own type rather than a string, because a schema file
    holding nothing but a JSON string would otherwise be reported as unreadable
    when it parsed perfectly well and merely is not a schema.
    """
    try:
        parsed: object = json.loads(text)
    except json.JSONDecodeError:
        return Unreadable(f"{description} is not valid JSON")
    return parsed


def _lines(findings: Sequence[Finding]) -> list[str]:
    """Return the printable report, each group under the heading it earns."""
    printed: list[str] = []
    for decided, heading in (
        (True, "these changes shrink what an existing document may say:"),
        (False, "the direction of these changes was not established:"),
    ):
        group = [
            f"  {finding.where or '/'}: {finding.detail}"
            for finding in findings
            if finding.decided is decided
        ]
        if group:
            printed.append(heading)
            printed.extend(group)
    return printed


def refuse(message: str) -> int:
    """Print one refusal and return the failing process exit status."""
    print(message, file=sys.stderr)
    return 1


def main(cwd: Path = ROOT, declared: str = __version__) -> int:
    """Check that the format grew, or that a line step paid for it not growing."""
    version = parse_version(declared)
    if version is None:
        return refuse(
            f"the declared package version {declared!r} is not a release version"
        )
    listing = git_output(["tag", "--list", "v*"], cwd)
    if listing is None:
        return refuse(
            "git cannot be read here, so the released schema cannot be recovered "
            "and no growth claim can be checked"
        )
    tags = released(listing)
    if not tags:
        return refuse(
            "no release tag is present, so there is no released schema to compare "
            "against; fetch tags before running this gate rather than reading a "
            "green run that compared against nothing"
        )
    line = release_line(version)
    same = sorted(one for one in tags if release_line(one) == line)
    newest = max(tags)
    authorized = not same
    if same:
        baseline_version = same[-1]
    elif line > release_line(newest):
        baseline_version = newest
    else:
        return refuse(
            f"the declared version {declared} is behind the newest release "
            f"{tags[newest]}, so this gate cannot tell which line it belongs to"
        )
    tag = tags[baseline_version]
    text = git_output(["show", f"{tag}:{SCHEMA_PATH.as_posix()}"], cwd)
    if text is None:
        return refuse(f"{tag} carries no {SCHEMA_PATH.as_posix()} to compare against")
    baseline = document(text, f"the schema at {tag}")
    if isinstance(baseline, Unreadable):
        return refuse(baseline.reason)
    current = document(
        (cwd / SCHEMA_PATH).read_text(encoding="utf-8"), str(SCHEMA_PATH)
    )
    if isinstance(current, Unreadable):
        return refuse(current.reason)
    findings = compare(baseline, current)
    if not findings:
        return 0
    if authorized:
        print(
            f"{declared} advances the release line past {tag}, which permits the "
            "wire format to stop growing. It does, here."
        )
        for line_text in _lines(findings):
            print(line_text)
        print(
            "This break is deliberate and priced, and it is printed so it cannot "
            f"be taken by accident. The first tag in the {spell_line(version)} line "
            "becomes the baseline the rest of that line is held to."
        )
        return 0
    print(
        f"the wire format may only grow within a release line, and {declared} is "
        f"inside the line released at {tag}.",
        file=sys.stderr,
    )
    for line_text in _lines(findings):
        print(line_text, file=sys.stderr)
    print(
        "Breaking the format is a first-class act here; it is priced, not "
        f"forbidden. Move __version__ in {VERSION_PATH.as_posix()} to at least "
        f"{spell(next_line(version))}, which advances the release line and states "
        "the break where every consumer of the format can read it.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
