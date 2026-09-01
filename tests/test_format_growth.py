"""Prove the growth gate refuses a format that stopped growing without saying so.

Every case here is built by mutating the schema this project actually ships,
not a miniature written to make the gate look right. The refusals are driven
end to end through a real repository with a real release tag, so the baseline
recovery, the release-line arithmetic, and the comparison are all under test
together rather than only the part that is easy to exercise.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from scripts import check_format_growth as growth

ROOT = growth.ROOT
RELEASED: dict[str, Any] = json.loads(
    (ROOT / growth.SCHEMA_PATH).read_text(encoding="utf-8")
)


def _write(root: Path, document: object) -> None:
    """Write one schema document where the gate expects to find it."""
    path = root / growth.SCHEMA_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def repository(tmp_path: Path, tags: tuple[str, ...] = ("v0.1.0",)) -> Path:
    """Return a repository holding the released schema under the given tags.

    Each call gets its own directory so one test may compare more than one
    mutation without the second landing in the first one's history.
    """
    root = Path(tempfile.mkdtemp(prefix="released", dir=tmp_path))
    _write(root, RELEASED)
    commands: list[list[str]] = [
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "gate@localhost"],
        ["config", "user.name", "gate"],
        ["add", growth.SCHEMA_PATH.as_posix()],
        ["commit", "-q", "-m", "released"],
    ]
    commands.extend(["tag", tag] for tag in tags)
    for arguments in commands:
        subprocess.run(["git", *arguments], cwd=root, check=True, capture_output=True)
    return root


def mutated(change: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """Return the released schema with one change applied to a fresh copy."""
    document: dict[str, Any] = json.loads(json.dumps(RELEASED))
    change(document)
    return document


def run(
    tmp_path: Path,
    change: Callable[[dict[str, Any]], None],
    declared: str = "0.1.1",
    tags: tuple[str, ...] = ("v0.1.0",),
) -> tuple[int, str, str]:
    """Apply one change to a released checkout and report the gate's verdict."""
    root = repository(tmp_path, tags)
    _write(root, mutated(change))
    status = growth.main(cwd=root, declared=declared)
    return status, root.as_posix(), ""


def verdict(
    tmp_path: Path,
    change: Callable[[dict[str, Any]], None],
    capsys: pytest.CaptureFixture[str],
    declared: str = "0.1.1",
    tags: tuple[str, ...] = ("v0.1.0",),
) -> tuple[int, str, str]:
    """Return the exit status and both streams for one mutated checkout."""
    status, _, _ = run(tmp_path, change, declared, tags)
    captured = capsys.readouterr()
    return status, captured.out, captured.err


# --- the released tree itself -------------------------------------------------


PRICED_BREAKS = (
    "/properties/graph/properties/attribute_declarations/items/properties/domain: "
    'the value "position" was dropped',
)


def test_the_committed_schema_reports_only_the_break_this_release_priced(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """REGRESSION. Predict the reported breaks are exactly the ones paid for.

    This is the gate running against the real repository and its real tag. It
    deliberately does not assert on the exit status alone. Once ``__version__``
    advanced past the released line, an unreleased line has no baseline it can
    be held to, so the gate prints its findings and returns zero whether or not
    the format shrank -- and an assertion on the status would hold just as well
    over a schema that had lost half its vocabulary. The claim that survives the
    permissive window is the *set* of breaks, which is exactly the one this
    release spent a version position on.

    The window closes by itself: tagging v0.2.0 gives the line a baseline and
    the gate refuses again, at which point a status assertion means something
    once more.
    """
    status = growth.main()
    printed = capsys.readouterr()
    reported = tuple(
        line.strip()
        for line in (printed.out + printed.err).splitlines()
        if line.startswith("  ")
    )
    assert (status, reported) == (0, PRICED_BREAKS)


def test_the_walk_reaches_every_definition() -> None:
    """CHARACTERIZATION. Predict every ``$defs`` entry is compared.

    A comparison that silently failed to descend would pass everything. This
    pins the reach: a definition nothing walks into is a definition nothing
    checks.
    """
    walk = growth._Comparison(RELEASED, RELEASED)
    walk.compare("", RELEASED, RELEASED)
    reached = {
        where.split("/")[2] for _, where in walk.seen if where.startswith("/$defs/")
    }
    assert reached == set(RELEASED["$defs"])
    assert walk.findings == []


# --- the motivating cases: a format that stopped growing ----------------------


def _remove_a_property(document: dict[str, Any]) -> None:
    """Drop a member the closed wire would then refuse."""
    del document["properties"]["graph"]["properties"]["namespaces"]


def _require_an_optional_property(document: dict[str, Any]) -> None:
    """Demand a member every existing document was free to omit."""
    document["properties"]["graph"]["required"] = ["tiers"]


def _narrow_an_enum(document: dict[str, Any]) -> None:
    """Withdraw an endpoint kind existing documents may already name."""
    document["$defs"]["relation_side"]["properties"]["endpoint_kinds"]["items"][
        "enum"
    ] = ["item"]


def _raise_a_bound(document: dict[str, Any]) -> None:
    """Refuse the first index of a tier, which every document uses."""
    document["$defs"]["item_reference"]["properties"]["index"]["minimum"] = 1


def _remove_a_union_arm(document: dict[str, Any]) -> None:
    """Withdraw the durable spelling of a relation endpoint."""
    left = document["$defs"]["binary_relation_instance"]["properties"]["left"]
    left["oneOf"] = [
        branch for branch in left["oneOf"] if "durable_position" not in branch["$ref"]
    ]


def _introduce_a_pattern(document: dict[str, Any]) -> None:
    """Constrain a string that carried no pattern before."""
    document["$defs"]["item_anchor"]["properties"]["durable_id"]["pattern"] = "^[a-z]+$"


def _add_an_unknown_keyword(document: dict[str, Any]) -> None:
    """Use a keyword whose direction this gate has no rule for."""
    document["$defs"]["item_anchor"]["properties"]["durable_id"]["maxLength"] = 64


def _smuggle_behind_the_stamp(document: dict[str, Any]) -> None:
    """Edit the excluded stamp site while removing a member elsewhere."""
    document["properties"]["format_version"] = {"enum": ["6", "7"]}
    del document["properties"]["graph"]["properties"]["tiers"]


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (
            _remove_a_property,
            "/properties/graph: the property 'namespaces' was removed",
        ),
        (
            _require_an_optional_property,
            "/properties/graph: the property 'tiers' became required",
        ),
        (
            _narrow_an_enum,
            '/$defs/relation_side/properties/endpoint_kinds/items: the value "boundary"'
            " was dropped",
        ),
        (
            _raise_a_bound,
            "/$defs/item_reference/properties/index: the bound minimum rose from 0 to 1",
        ),
        (
            _remove_a_union_arm,
            "/$defs/binary_relation_instance/properties/left: the branch "
            "ref #/$defs/durable_position was removed",
        ),
        (
            _introduce_a_pattern,
            "/$defs/item_anchor/properties/durable_id: the pattern '^[a-z]+$' was "
            "introduced",
        ),
    ],
)
def test_a_format_that_stopped_growing_is_refused_by_name(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    change: Callable[[dict[str, Any]], None],
    expected: str,
) -> None:
    """REGRESSION. Predict refusal naming the location and what it lost.

    Each case is a real shrinkage of the shipped schema. A gate that reported
    only that something changed would be useless for fixing it, so the message
    is asserted, not just the exit status.
    """
    status, _, errors = verdict(tmp_path, change, capsys)
    assert status == 1
    assert f"  {expected}" in errors
    assert "these changes shrink what an existing document may say:" in errors


def test_a_refusal_names_the_version_step_that_would_permit_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """REGRESSION. Predict the refusal quotes the price, not merely a denial.

    A gate that only says no gets worked around. This one has to name the step
    that makes the same change legal.
    """
    _, _, errors = verdict(tmp_path, _remove_a_property, capsys)
    assert "priced, not forbidden" in errors
    assert "src/tiergraph/__init__.py to at least 0.2.0" in errors


def test_an_undecided_keyword_is_reported_rather_than_skipped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """REGRESSION. Predict refusal under the undecided heading, naming the keyword.

    An unknown keyword could tighten the format in a way nothing here reads. It
    is refused as unestablished rather than passed over as unrecognized.
    """
    status, _, errors = verdict(tmp_path, _add_an_unknown_keyword, capsys)
    assert status == 1
    assert "the direction of these changes was not established:" in errors
    assert "does not decide the keyword 'maxLength'" in errors


def test_the_version_exclusion_cannot_carry_another_change_through(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """REGRESSION. Predict refusal: the stamp site is excluded only while it is a stamp.

    The two version-bearing sites move at every format release and must be
    skipped. Skipping them on trust would make them the one place a change is
    invisible, so the exclusion is conditional on each still being a bare stamp.
    """
    status, _, errors = verdict(tmp_path, _smuggle_behind_the_stamp, capsys)
    assert status == 1
    assert "does not stamp its format version as a bare const" in errors


def test_a_changed_pattern_is_reported_as_undecided(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CHARACTERIZATION. Predict a changed pattern refuses as unestablished.

    Regular-language containment is decidable but not decided here. This pins
    the limit the docstring states: a genuine widening costs the same step as a
    narrowing until someone teaches the gate to decide it.
    """

    def change(document: dict[str, Any]) -> None:
        document["$defs"]["item_reference"]["properties"]["tier"]["pattern"] = "^.+$"

    status, _, errors = verdict(tmp_path, change, capsys)
    assert status == 1
    assert "does not decide whether one regular language contains the other" in errors


# --- the additive side --------------------------------------------------------


def _plan_a_seal(document: dict[str, Any]) -> None:
    """Add an optional seal beside the format stamp on the document root."""
    document["properties"]["seal"] = {
        "additionalProperties": False,
        "properties": {
            "algorithm": {"enum": ["sha256"], "type": "string"},
            "digest": {"minLength": 1, "type": "string"},
        },
        "required": ["algorithm", "digest"],
        "type": "object",
    }


def _plan_a_durable_item_arm(document: dict[str, Any]) -> None:
    """Add a durable-item arm to every relation endpoint union."""
    document["$defs"]["durable_item"] = {
        "additionalProperties": False,
        "properties": {
            "durable_id": {"minLength": 1, "type": "string"},
            "kind": {"enum": ["durable_item"], "type": "string"},
        },
        "required": ["kind", "durable_id"],
        "type": "object",
    }
    arm = {"$ref": "#/$defs/durable_item"}
    binary = document["$defs"]["binary_relation_instance"]["properties"]
    for side in ("left", "right"):
        binary[side]["oneOf"].append(dict(arm))
    polyadic = document["$defs"]["polyadic_relation_instance"]["properties"]
    for side in ("sources", "targets"):
        polyadic[side]["items"]["oneOf"].append(dict(arm))


def _add_a_held_apart_collection(document: dict[str, Any]) -> None:
    """Add an optional collection the graph holds apart from its tiers.

    This once added ``layers``, which has since landed, and applying it to the
    shipped schema would now overwrite the real definition with a poorer one --
    a shrink, refused, for a reason having nothing to do with what is being
    checked. It uses a name the schema does not carry instead, because the
    subject is the *shape*: whether the gate reads a new optional collection as
    growth. Deleting the body rather than renaming it would leave a case that
    mutates nothing and therefore cannot fail.
    """
    document["$defs"]["annotation_set"] = {
        "additionalProperties": False,
        "properties": {
            "name": {
                "minLength": 3,
                "pattern": "^[^:]+:[\\s\\S]+$",
                "type": "string",
            }
        },
        "required": ["name"],
        "type": "object",
    }
    document["properties"]["graph"]["properties"]["annotation_sets"] = {
        "items": {"$ref": "#/$defs/annotation_set"},
        "type": "array",
    }


def _add_a_source_axis_and_widen_a_domain(document: dict[str, Any]) -> None:
    """Give that collection a source axis and admit one more domain value.

    Two growths at once, and the second is the one worth keeping: adding a
    member to an enum admits documents the old vocabulary could not spell, so a
    gate that read enum changes as narrowing in both directions would refuse it.
    """
    _add_a_held_apart_collection(document)
    document["$defs"]["annotation_set"]["properties"]["source"] = {
        "minLength": 1,
        "type": "string",
    }
    domain = document["properties"]["graph"]["properties"]["attribute_declarations"][
        "items"
    ]["properties"]["domain"]
    domain["enum"] = [*domain["enum"], "annotation"]


def _loosen_constraints(document: dict[str, Any]) -> None:
    """Lower a bound and drop a requirement, both of which admit more documents."""
    document["$defs"]["item_anchor"]["properties"]["durable_id"]["minLength"] = 0
    document["$defs"]["binary_relation_instance"]["required"] = ["declaration"]


def _open_a_closed_object(document: dict[str, Any]) -> None:
    """Stop refusing unlisted members, which admits documents that carry them."""
    document["$defs"]["item_reference"]["additionalProperties"] = True


def _widen_a_union(document: dict[str, Any]) -> None:
    """Replace an exclusive union with an inclusive one over the same arms."""
    left = document["$defs"]["binary_relation_instance"]["properties"]["left"]
    left["anyOf"] = left.pop("oneOf")


@pytest.mark.parametrize(
    "change",
    [
        _plan_a_seal,
        _plan_a_durable_item_arm,
        _add_a_held_apart_collection,
        _add_a_source_axis_and_widen_a_domain,
        _loosen_constraints,
        _open_a_closed_object,
    ],
)
def test_an_additive_change_passes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    change: Callable[[dict[str, Any]], None],
) -> None:
    """REGRESSION. Predict pass and silence for every growth of the format.

    The first four are this release's planned changes, applied to the shipped
    schema rather than described. A gate that refused them would be refusing
    the work it exists to protect.
    """
    status, output, errors = verdict(tmp_path, change, capsys)
    assert (status, output, errors) == (0, "", "")


def test_the_additive_shapes_pass_together(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """REGRESSION. Predict pass: the additive shapes are jointly additive.

    Applied one at a time they could each pass while interacting badly, so they
    are applied to one schema together. The count is not named in the title on
    purpose: it was once four, the release's own planned changes, and a title
    carrying a number goes stale the moment the list moves while still reading
    as though it were checked.
    """

    def change(document: dict[str, Any]) -> None:
        _plan_a_seal(document)
        _plan_a_durable_item_arm(document)
        _add_a_source_axis_and_widen_a_domain(document)

    status, output, errors = verdict(tmp_path, change, capsys)
    assert (status, output, errors) == (0, "", "")


def test_narrowing_a_union_keyword_is_refused_and_widening_it_is_not(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """REGRESSION. Predict oneOf-from-anyOf refuses; anyOf-from-oneOf passes.

    Which keyword holds the arms is itself a constraint: ``oneOf`` demands
    exactly one match where ``anyOf`` accepts any. Only one direction of the
    swap admits more documents.
    """
    status, output, errors = verdict(tmp_path, _widen_a_union, capsys)
    assert (status, output, errors) == (0, "", "")

    def narrow(document: dict[str, Any]) -> None:
        durable = document["$defs"]["binary_relation_instance"]["properties"][
            "durable_id"
        ]
        durable["oneOf"] = durable.pop("anyOf")

    status, _, errors = verdict(tmp_path, narrow, capsys)
    assert status == 1
    assert "anyOf was narrowed to oneOf" in errors


# --- the price, and the span in which it has been paid ------------------------


def test_a_line_step_permits_the_break_and_still_names_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """REGRESSION. Predict exit 0 with the break printed, not suppressed.

    This is the escape, and it is not an exemption anybody maintains: the
    release-line step is already the release decision and is already visible to
    every consumer. The gate reads it and reports what it bought.
    """
    status, output, errors = verdict(tmp_path, _remove_a_property, capsys, "0.2.0")
    assert (status, errors) == (0, "")
    assert "advances the release line past v0.1.0" in output
    assert "the property 'namespaces' was removed" in output
    assert "The first tag in the 0.2.x line" in output


def test_a_patch_step_does_not_pay_for_a_break(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """REGRESSION. Predict refusal: a patch step stays inside the released line.

    The distinction the whole gate rests on. Both 0.1.9 and 0.2.0 are version
    bumps; only one of them advances the line.
    """
    status, _, errors = verdict(tmp_path, _remove_a_property, capsys, "0.1.9")
    assert status == 1
    assert "inside the line released at v0.1.0" in errors


def test_a_first_stable_release_takes_the_major_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """REGRESSION. Predict 1.0.0 authorizes a break past the 0.1.x line.

    After 1.0 the major position carries the line, which is the post-1.0 half
    of the same rule and the more expensive half.
    """
    status, output, _ = verdict(tmp_path, _remove_a_property, capsys, "1.0.0")
    assert status == 0
    assert "The first tag in the 1.x line" in output


def test_a_released_line_is_gated_again_once_it_is_tagged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """REGRESSION. Predict refusal once the new line has its own release tag.

    The opening between a version bump and its tag closes by itself. Without
    that, a break taken during the span could be followed by another.
    """
    status, _, errors = verdict(
        tmp_path, _remove_a_property, capsys, "0.2.1", ("v0.1.0", "v0.2.0")
    )
    assert status == 1
    assert "inside the line released at v0.2.0" in errors


def test_the_newest_tag_in_the_line_is_the_baseline(tmp_path: Path) -> None:
    """CHARACTERIZATION. Predict v0.1.10 outranks v0.1.9 despite sorting after it.

    Release versions are numbers, not strings, and a gate that compared them as
    text would pick the wrong baseline the tenth time a line is released.
    """
    tags = growth.released("v0.1.0\nv0.1.9\nv0.1.10\nnot-a-tag\nv1.2\n")
    assert tags == {
        (0, 1, 0): "v0.1.0",
        (0, 1, 9): "v0.1.9",
        (0, 1, 10): "v0.1.10",
    }
    assert max(tags) == (0, 1, 10)


def test_a_version_behind_the_newest_release_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """REGRESSION. Predict refusal when the declared line is below every release.

    A break could otherwise be authorized by moving the version backward into a
    line nobody released, which is the opposite of paying a price.
    """
    status, _, errors = verdict(
        tmp_path, _remove_a_property, capsys, "0.0.5", ("v0.1.0",)
    )
    assert status == 1
    assert "behind the newest release v0.1.0" in errors


# --- the baseline, and what happens when it is not there ----------------------


def test_a_checkout_without_tags_refuses_rather_than_passing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """REGRESSION. Predict refusal, not a pass, when no release tag exists.

    This is the failure mode chosen over a checked-in baseline copy. A shallow
    clone has no tags, and a green run that compared against nothing would read
    as verification without being any.
    """
    root = repository(tmp_path, tags=())
    assert growth.main(cwd=root, declared="0.1.1") == 1
    assert "no release tag is present" in capsys.readouterr().err


def test_a_directory_outside_git_refuses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """REGRESSION. Predict refusal where git cannot answer at all."""
    assert growth.main(cwd=tmp_path, declared="0.1.1") == 1
    assert "git cannot be read here" in capsys.readouterr().err


def test_a_tag_without_the_schema_refuses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """REGRESSION. Predict refusal when the released commit carries no schema."""
    root = tmp_path / "bare"
    root.mkdir()
    (root / "README.md").write_text("released\n", encoding="utf-8")
    for arguments in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "gate@localhost"],
        ["config", "user.name", "gate"],
        ["add", "README.md"],
        ["commit", "-q", "-m", "released"],
        ["tag", "v0.1.0"],
    ):
        subprocess.run(["git", *arguments], cwd=root, check=True, capture_output=True)
    _write(root, RELEASED)
    assert growth.main(cwd=root, declared="0.1.1") == 1
    assert "carries no schema/tiergraph.schema.json" in capsys.readouterr().err


def test_an_unreadable_baseline_or_working_schema_refuses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """REGRESSION. Predict refusal on either side of the comparison being unparsable."""
    root = tmp_path / "broken"
    (root / "schema").mkdir(parents=True)
    (root / growth.SCHEMA_PATH).write_text("{not json", encoding="utf-8")
    for arguments in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "gate@localhost"],
        ["config", "user.name", "gate"],
        ["add", growth.SCHEMA_PATH.as_posix()],
        ["commit", "-q", "-m", "released"],
        ["tag", "v0.1.0"],
    ):
        subprocess.run(["git", *arguments], cwd=root, check=True, capture_output=True)
    assert growth.main(cwd=root, declared="0.1.1") == 1
    assert "the schema at v0.1.0 is not valid JSON" in capsys.readouterr().err

    working = repository(tmp_path)
    (working / growth.SCHEMA_PATH).write_text("{not json", encoding="utf-8")
    assert growth.main(cwd=working, declared="0.1.1") == 1
    assert "is not valid JSON" in capsys.readouterr().err


def test_a_package_version_that_is_not_a_release_refuses(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """REGRESSION. Predict refusal for a version the line rule cannot read."""
    assert growth.main(declared="0.1.0.dev1") == 1
    assert "is not a release version" in capsys.readouterr().err


# --- the comparison's own vocabulary ------------------------------------------


def compare(before: object, after: object) -> list[str]:
    """Return the detail of each finding between two bare subschemas."""
    walk = growth._Comparison(before, after)
    walk.compare("", before, after)
    return [finding.detail for finding in walk.findings]


def test_a_const_widened_into_an_enum_reads_as_the_widening_it_is() -> None:
    """REGRESSION. Predict no finding: const is the one-member enum it stands for.

    Compared keyword by keyword this looks like a dropped const and an added
    enum, which would refuse a genuine widening. Normalizing const first is
    what stops that.
    """
    assert compare({"const": "item"}, {"enum": ["item", "boundary"]}) == []
    assert compare({"enum": ["item", "boundary"]}, {"const": "item"}) == [
        'the value "boundary" was dropped'
    ]


def test_constraining_a_free_value_to_a_set_is_refused() -> None:
    """REGRESSION. Predict introducing an enum refuses; widening one does not.

    A string that could hold anything and now holds one of three is a document
    the reader may already have written and would now refuse.
    """
    assert compare({"type": "string"}, {"enum": ["a", "b"], "type": "string"}) == [
        'the value set was constrained to "a", "b"'
    ]
    assert compare({"enum": ["a"], "type": "string"}, {"type": "string"}) == []


def test_stating_both_const_and_enum_is_undecided() -> None:
    """CHARACTERIZATION. Predict a report rather than a guess at the pair."""
    assert compare({"const": "a", "enum": ["a"]}, {"const": "a"}) == [
        "a subschema states both const and enum"
    ]


def test_a_type_union_grows_and_shrinks() -> None:
    """REGRESSION. Predict a lost type refuses and a gained type does not."""
    assert compare({"type": "string"}, {"type": ["string", "null"]}) == []
    assert compare({"type": ["string", "null"]}, {"type": "string"}) == [
        "the type null is no longer accepted"
    ]
    assert compare({}, {"type": "string"}) == [
        "the type constraint string was introduced"
    ]
    assert compare({"type": "string"}, {}) == []


def test_a_type_that_is_not_a_name_is_undecided() -> None:
    """CHARACTERIZATION. Predict a report on either side of a malformed type."""
    assert compare({"type": 7}, {"type": "string"}) == [
        "in the released schema, a type is neither a name nor a list of names"
    ]
    assert compare({"type": "string"}, {"type": [7]}) == [
        "a type is neither a name nor a list of names"
    ]


def test_a_removed_property_only_refuses_where_the_wire_is_closed() -> None:
    """CHARACTERIZATION. Predict silence when unlisted members are still accepted.

    Removal is a refusal because the wire is closed. On an open object the same
    edit drops a constraint instead of a member.
    """
    closed_before = {"additionalProperties": False, "properties": {"a": {}}}
    assert compare(
        closed_before, {"additionalProperties": False, "properties": {}}
    ) == ["the property 'a' was removed"]
    assert compare(closed_before, {"properties": {}}) == []


def test_a_property_name_with_pointer_syntax_is_escaped() -> None:
    """CHARACTERIZATION. Predict the reported location escapes ``~`` and ``/``.

    A location that mangles the name it reports sends a reader to the wrong
    place, and JSON pointer has exactly two characters that need it.
    """
    before = {"properties": {"a/b~c": {"type": "string"}}}
    walk = growth._Comparison(before, {"properties": {"a/b~c": {"type": "integer"}}})
    walk.compare("", before, {"properties": {"a/b~c": {"type": "integer"}}})
    assert [finding.where for finding in walk.findings] == ["/properties/a~1b~0c"]


def test_a_malformed_properties_or_required_is_undecided() -> None:
    """CHARACTERIZATION. Predict a report rather than a crash on bad shapes."""
    assert compare({"properties": []}, {"properties": {}}) == [
        "properties is not an object"
    ]
    assert compare({"required": "a"}, {"required": ["a"]}) == [
        "a required list is not a list of names"
    ]


def test_an_element_constraint_grows_and_shrinks() -> None:
    """REGRESSION. Predict introducing items refuses and dropping it does not."""
    assert compare({}, {"items": {"type": "string"}}) == [
        "an element constraint was introduced"
    ]
    assert compare({"items": {"type": "string"}}, {}) == []
    assert compare({"items": {"type": "string"}}, {"items": {"type": "integer"}}) == [
        "the type string is no longer accepted"
    ]
    assert compare(
        {"items": [{"type": "string"}]}, {"items": [{"type": "string"}]}
    ) == ["items states a positional list of subschemas"]


def test_a_bound_is_read_as_a_bound() -> None:
    """REGRESSION. Predict raising or introducing a bound refuses; lowering does not."""
    assert compare({"minLength": 3}, {"minLength": 1}) == []
    assert compare({"minLength": 1}, {"minLength": 3}) == [
        "the bound minLength rose from 1 to 3"
    ]
    assert compare({}, {"minimum": 0}) == ["the bound minimum 0 was introduced"]
    assert compare({"minimum": 0}, {}) == []
    assert compare({"minimum": 0}, {"minimum": True}) == ["minimum is not a number"]
    assert compare({"minimum": "0"}, {"minimum": 0}) == [
        "the released minimum is not a number"
    ]


def test_closing_an_open_object_refuses_and_opening_a_closed_one_does_not() -> None:
    """REGRESSION. Predict closing refuses, opening passes, a subschema is undecided.

    The shipped schema is closed everywhere, so this direction cannot be built
    by mutating it; it is pinned here instead of left unexercised.
    """
    assert compare({"properties": {"a": {}}}, {"properties": {"a": {}}}) == []
    assert compare(
        {"properties": {"a": {}}},
        {"additionalProperties": False, "properties": {"a": {}}},
    ) == ["unlisted properties are now refused"]
    assert compare({"additionalProperties": False}, {}) == []
    assert compare({}, {"additionalProperties": {}}) == [
        "additionalProperties is a subschema"
    ]
    assert compare({"additionalProperties": {}}, {}) == [
        "additionalProperties is a subschema"
    ]


def test_an_enum_that_is_not_a_list_is_undecided() -> None:
    """CHARACTERIZATION. Predict a report on either side of a malformed enum."""
    assert compare({"enum": ["a"]}, {"enum": "a"}) == [
        "an enum is not a list of values"
    ]
    assert compare({"enum": "a"}, {"enum": ["a"]}) == [
        "the released enum is not a list of values"
    ]


def test_branch_sets_are_paired_by_reference_or_type() -> None:
    """REGRESSION. Predict a lost branch refuses and a gained branch does not."""
    two = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    assert compare(two, {"anyOf": [{"type": "string"}]}) == [
        'the branch type "null" was removed'
    ]
    assert compare({"anyOf": [{"type": "string"}]}, two) == []
    assert compare({}, two) == ["a anyOf branch constraint was introduced"]
    assert compare(two, {}) == []


def test_unpairable_branches_are_undecided() -> None:
    """CHARACTERIZATION. Predict a report where branches cannot be matched up."""
    two = {"anyOf": [{"type": "string"}, {"type": "string"}]}
    assert compare(two, {"anyOf": [{"type": "string"}]}) == [
        'in the released schema, two branches share the key type "string", so they '
        "cannot be paired"
    ]
    assert compare({"anyOf": [{"type": "string"}]}, two) == [
        'two branches share the key type "string", so they cannot be paired'
    ]
    assert compare({"anyOf": [{"type": "string"}]}, {"anyOf": [7]}) == [
        "a branch is not a subschema"
    ]
    assert compare({"anyOf": {}}, {"anyOf": [{"type": "string"}]}) == [
        "in the released schema, a branch list is not a list"
    ]
    assert compare({"anyOf": [{"type": "string"}]}, {"anyOf": {}}) == [
        "a branch list is not a list"
    ]


def test_stating_both_branch_keywords_is_undecided() -> None:
    """CHARACTERIZATION. Predict a report on either side of a doubled branch set."""
    both = {"oneOf": [{"type": "string"}], "anyOf": [{"type": "string"}]}
    assert compare(both, {"anyOf": [{"type": "string"}]}) == [
        "in the released schema, a subschema states both oneOf and anyOf"
    ]
    assert compare({"anyOf": [{"type": "string"}]}, both) == [
        "a subschema states both oneOf and anyOf"
    ]


def test_a_reference_is_followed_and_a_broken_one_is_reported() -> None:
    """REGRESSION. Predict a reference resolves, and every way it can fail reports.

    The comparison would be hollow if a reference it could not follow were
    treated as equal to whatever it stood beside.
    """
    document = {
        "$defs": {"a": {"type": "string"}},
        "properties": {"x": {"$ref": "#/$defs/a"}},
    }
    assert compare(document, document) == []
    assert compare({"$ref": "#/$defs/missing"}, {"type": "string"}) == [
        "in the released schema, a reference here does not resolve (#/$defs/missing)"
    ]
    assert compare({"type": "string"}, {"$ref": "#/$defs/missing"}) == [
        "a reference here does not resolve (#/$defs/missing)"
    ]
    assert compare({"$ref": "external#/$defs/a"}, {"type": "string"}) == [
        "in the released schema, a reference here is not a local pointer "
        "('external#/$defs/a')"
    ]
    assert compare({"$ref": "#/$defs/a", "type": "string"}, {"type": "string"}) == [
        "in the released schema, a reference here carries sibling keywords"
    ]
    assert compare(True, {"type": "string"}) == [
        "in the released schema, a subschema here is not an object"
    ]
    assert compare({"type": "string"}, True) == ["a subschema here is not an object"]


def test_a_reference_cycle_is_reported_rather_than_followed_forever() -> None:
    """REGRESSION. Predict a chain of references closing on itself is reported.

    A recursive schema cycles through properties, which the walk memoizes. A
    cycle through bare references resolves to nothing and has to stop.
    """
    document = {"$defs": {"a": {"$ref": "#/$defs/b"}, "b": {"$ref": "#/$defs/a"}}}
    walk = growth._Comparison(document, document)
    walk.compare("", {"$ref": "#/$defs/a"}, {"type": "string"})
    assert [finding.detail for finding in walk.findings] == [
        "in the released schema, a chain of references here closes on #/$defs/a"
    ]


def test_a_recursive_schema_is_compared_once_per_pair_of_locations() -> None:
    """REGRESSION. Predict termination on a schema that refers back to itself."""
    document = {
        "$defs": {"node": {"properties": {"child": {"$ref": "#/$defs/node"}}}},
        "properties": {"root": {"$ref": "#/$defs/node"}},
    }
    assert compare(document, document) == []


def test_a_subschema_declaring_its_own_identifier_is_undecided() -> None:
    """CHARACTERIZATION. Predict a report: the stamp rule reaches the root only."""
    assert compare(
        {"properties": {"a": {}}},
        {"properties": {"a": {"$id": growth.SCHEMA_ID.format(version="7")}}},
    ) == ["a subschema declares its own $id"]


def test_an_unstamped_document_is_reported_from_either_side() -> None:
    """REGRESSION. Predict both sides are checked for a well-formed stamp."""
    stamped = {
        "$id": growth.SCHEMA_ID.format(version="6"),
        "properties": {"format_version": {"const": "6"}},
    }
    assert growth.compare(stamped, stamped) == []
    assert [finding.detail for finding in growth.compare({}, stamped)] == [
        "the released schema does not stamp its format version as a bare const "
        "agreeing with its $id, so the two version-bearing sites cannot be excluded "
        "from this comparison"
    ]


@pytest.mark.parametrize(
    "document",
    [
        [],
        {"properties": []},
        {"properties": {}},
        {"properties": {"format_version": {"const": "6", "title": "x"}}},
        {"properties": {"format_version": {"const": 6}}},
        {
            "$id": growth.SCHEMA_ID.format(version="7"),
            "properties": {"format_version": {"const": "6"}},
        },
    ],
)
def test_only_a_bare_agreeing_stamp_is_recognized(document: object) -> None:
    """REGRESSION. Predict None for every shape that is not exactly a stamp.

    The exclusion is the one blind spot the comparison has, so what counts as a
    stamp is pinned narrowly and each near miss is refused.
    """
    assert growth.stamp(document) is None


def test_the_stamp_the_project_ships_is_recognized() -> None:
    """REGRESSION. Predict the shipped schema's own stamp is read as one.

    The expected value is written out rather than read from ``FORMAT_VERSION``.
    The schema is generated from that constant, so comparing the two would
    check the generator and not the value, and this assertion exists to make a
    format change visible to whoever takes it.
    """
    assert growth.stamp(RELEASED) == "0.2.0"


def test_a_pointer_that_names_nothing_returns_nothing() -> None:
    """CHARACTERIZATION. Predict None rather than an exception off the end."""
    assert growth.at({"a": {"b": 1}}, "#/a/b") == 1
    assert growth.at({"a": {"b": 1}}, "#/a/c") is None
    assert growth.at({"a": 1}, "#/a/b") is None
    assert growth.at({"a/b~c": 1}, "#/a~1b~0c") == 1


def test_the_release_line_follows_the_repositorys_own_series_names() -> None:
    """REGRESSION. Predict 0.MINOR pre-1.0 and MAJOR after, as the docs already say.

    ``README.md`` says that before 1.0 "a 0.X.0 release is in effect a major
    release", and ``SECURITY.md`` supports "the latest published pre-1.0
    release". The line rule is read off those rather than invented beside them.
    """
    assert growth.release_line((0, 1, 9)) == (0, 1)
    assert growth.release_line((0, 2, 0)) == (0, 2)
    assert growth.release_line((1, 4, 2)) == (1, 0)
    assert growth.release_line((0, 1, 0)) < growth.release_line((0, 2, 0))
    assert growth.release_line((0, 9, 0)) < growth.release_line((1, 0, 0))
    assert growth.next_line((0, 1, 3)) == (0, 2, 0)
    assert growth.next_line((1, 4, 2)) == (2, 0, 0)
    assert growth.spell_line((0, 1, 3)) == "0.1.x"
    assert growth.spell_line((2, 1, 3)) == "2.x"
    assert growth.spell((0, 1, 3)) == "0.1.3"


def test_a_version_string_is_read_or_refused() -> None:
    """CHARACTERIZATION. Predict only three numeric parts parse."""
    assert growth.parse_version("1.2.3") == (1, 2, 3)
    assert growth.parse_version("1.2") is None
    assert growth.parse_version("1.2.3a") is None


def test_both_finding_groups_are_printed_under_their_own_heading() -> None:
    """REGRESSION. Predict a mixed report separates what was shown from what was not.

    Running the two kinds together would let an undecided change read as an
    established shrinkage, and the reader would not know which they had.
    """
    lines = growth._lines(
        [growth.Finding("/a", "shrank", True), growth.Finding("", "unknown", False)]
    )
    assert lines == [
        "these changes shrink what an existing document may say:",
        "  /a: shrank",
        "the direction of these changes was not established:",
        "  /: unknown",
    ]
