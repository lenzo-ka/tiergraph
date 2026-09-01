"""Prove that publishability gates reject representative defects."""

from __future__ import annotations

import shutil
import subprocess
import tarfile
from collections.abc import Callable
from pathlib import Path

import pytest
from hatchling.builders.sdist import SdistBuilder
from scripts import check_documented, check_tracked_clean

TOKEN = "sentineltoken"
SALT = bytes.fromhex("00112233445566778899aabbccddeeff")
# Joined at run time: this module is itself a shipped surface, so a literal
# counter-example would be extracted from it and refused.
LEAK_URL = "https:" + "//unknown.invalid/project"


def _synthetic_denylist() -> check_tracked_clean.Denylist:
    """Return a denylist containing only the synthetic test token."""
    return check_tracked_clean.Denylist(
        salt=SALT,
        digests=frozenset({check_tracked_clean.digest(TOKEN.lower(), SALT)}),
    )


FORBIDDEN_WITNESSES: tuple[tuple[str, str], ...] = (
    ("/" + "Users/contributor/project", "a macOS home-directory path"),
    ("/" + "home/contributor/project", "a Linux home-directory path"),
    ("." + "ssh/config", "a path into an SSH configuration directory"),
    ("file:" + "///tmp/report", "an absolute local file URL"),
    ("Generated " + "by AI", "AI/tool attribution"),
    ("As " + "an AI", "AI/tool attribution"),
    ("Generated " + "on 2026-08-23", "a generated timestamp"),
)


@pytest.mark.parametrize(("content", "reason"), FORBIDDEN_WITNESSES)
def test_tracked_clean_leaks_detects_each_forbidden_surface(
    tmp_path: Path, content: str, reason: str
) -> None:
    """Every forbidden pattern produces its specific publishability reason.

    The witnesses are matched against the gate's own `FORBIDDEN` table rather
    than counted by eye, so an eighth pattern shipped without a witness fails
    here instead of shipping untested.  The reasons are the shipped wording in
    full: a substring stops distinguishing two reasons the moment one is
    rephrased into the other's prefix.
    """
    assert sorted(reason for _, reason in FORBIDDEN_WITNESSES) == sorted(
        declared for _, declared in check_tracked_clean.FORBIDDEN
    )
    path = tmp_path / "tracked.txt"
    path.write_text(content, encoding="utf-8")
    assert reason in check_tracked_clean.leaks(path)[0]


def test_tracked_clean_leaks_accepts_clean_and_unreadable_files(tmp_path: Path) -> None:
    """Ordinary prose and unreadable paths have no textual leak report."""
    clean = tmp_path / "clean.txt"
    clean.write_text("portable release notes\n", encoding="utf-8")
    assert check_tracked_clean.leaks(clean) == []
    assert check_tracked_clean.leaks(tmp_path / "missing.txt") == []


def test_external_reference_gate_refuses_unallowlisted_url_and_import(
    tmp_path: Path,
) -> None:
    """Unknown cited projects fail closed without appearing in a forbidden list."""
    # This module is itself a shipped surface, so a counter-example spelled as one
    # literal would be extracted from this file and refused. The pieces are joined
    # at run time; the gate reads source text and cannot see across the operator.
    page = tmp_path / "guide.md"
    page.write_text(
        "See " + "https:" + "//unknown.invalid/project.\n", encoding="utf-8"
    )
    assert check_tracked_clean.reference_leaks(page) == [
        f"{page}: an unallowlisted URL reference ('unknown.invalid/project')"
    ]
    source = tmp_path / "sample.py"
    source.write_text("import unanticipated_project\n", encoding="utf-8")
    assert check_tracked_clean.reference_leaks(source) == [
        f"{source}: an unallowlisted top-level import ('unanticipated_project')"
    ]


def test_every_allowlisted_external_reference_is_accepted(tmp_path: Path) -> None:
    """Every visible URL, import, distribution, and bare-domain exception is live."""
    page = tmp_path / "guide.md"
    page.write_text(
        "\n".join(
            f"https://{value}" for value in check_tracked_clean.ALLOWED_URL_PREFIXES
        )
        + "\n"
        + "\n".join(check_tracked_clean.ALLOWED_DOMAINS)
        + "\n"
        # The email allowlist is empty today, which is why leaving it out of
        # this page still read as a complete sweep: the first address ever
        # added to it would have been the first one nothing here accepted.
        + "\n".join(check_tracked_clean.ALLOWED_EMAILS)
        + "\n",
        encoding="utf-8",
    )
    assert check_tracked_clean.reference_leaks(page) == []

    source = tmp_path / "sample.py"
    source.write_text(
        "\n".join(f"import {name}" for name in check_tracked_clean.ALLOWED_IMPORTS),
        encoding="utf-8",
    )
    assert check_tracked_clean.reference_leaks(source) == []

    project = tmp_path / "pyproject.toml"
    dependencies = ", ".join(
        repr(name) for name in sorted(check_tracked_clean.ALLOWED_DISTRIBUTIONS)
    )
    project.write_text(
        f"[project]\ndependencies = [{dependencies}]\n",
        encoding="utf-8",
    )
    assert check_tracked_clean.reference_leaks(project) == []


def test_reference_extraction_rejects_domains_and_emails_not_substrings(
    tmp_path: Path,
) -> None:
    """Reference syntax, not incidental command or field substrings, drives refusal."""
    path = tmp_path / "guide.md"
    path.write_text(
        "important jsonschema_field tiergraphical example.company\n",
        encoding="utf-8",
    )
    assert check_tracked_clean.reference_leaks(path) == []
    path.write_text(
        "mail person" + "@unknown" + ".org or visit unknown" + ".org\n",
        encoding="utf-8",
    )
    assert check_tracked_clean.reference_leaks(path) == [
        f"{path}: an unallowlisted email address ('person@unknown" + ".org')",
        f"{path}: an unallowlisted bare domain ('unknown" + ".org')",
    ]


def test_real_denied_digests_parse_and_are_live() -> None:
    """The repository digest file has the expected salt size and live entries."""
    denylist = check_tracked_clean.denied_digests(
        check_tracked_clean.DENIED_DIGESTS_PATH
    )
    assert len(denylist.salt) == 16
    assert denylist.digests


def test_synthetic_project_prose_is_denied_without_disclosure(
    tmp_path: Path,
) -> None:
    """Repeated prose matches are line numbered without exposing their token."""
    path = tmp_path / "guide.md"
    path.write_text(
        f"Downstream migration ({TOKEN}). {TOKEN} is migrating onto tiergraph: the\n"
        "remaining subsystems are still on its embedded engine.\n",
        encoding="utf-8",
    )
    messages = check_tracked_clean.name_leaks(path, _synthetic_denylist())
    assert messages == [
        f"{path}:1: a denied name written in shipped text",
        f"{path}:1: a denied name written in shipped text",
    ]
    assert all(TOKEN not in message for message in messages)
    assert check_tracked_clean.reference_leaks(path) == []


def test_denied_name_matching_catches_inflections_and_split_spellings(
    tmp_path: Path,
) -> None:
    """REGRESSION: matching covers the name, not the bare-token shape of it.

    Every line here is a way someone writes the name by accident. The last two
    were previously asserted to produce NO finding, under a test named for
    respecting token boundaries -- the escape was encoded as intended behavior,
    which is what an external read caught: a plural or a compound shipped clean
    while the gate reported success, and an owner ruling rested on that report.
    """
    path = tmp_path / "guide.md"
    path.write_text(
        f"{TOKEN.upper()}\n{TOKEN.title()}\n{TOKEN}_bridge\n{TOKEN}s\nmy{TOKEN}\n",
        encoding="utf-8",
    )
    assert check_tracked_clean.name_leaks(path, _synthetic_denylist()) == [
        f"{path}:{line}: a denied name written in shipped text"
        for line in (1, 2, 3, 4, 5)
    ]


def test_denied_name_matching_catches_a_separator_split_spelling(
    tmp_path: Path,
) -> None:
    """REGRESSION: a name split by a separator the repository never used.

    `sentinel-token` digests to nothing as two runs, which is exactly why the
    join exists. The paragraph-break case is the bound: two runs that far apart
    are not a spelling of one name, and joining them would invent leaks.
    """
    head, tail = TOKEN[:8], TOKEN[8:]
    path = tmp_path / "guide.md"
    path.write_text(
        f"{head}-{tail}\n{head}_{tail}\n{head} {tail}\n{head}\n\n{tail}\n",
        encoding="utf-8",
    )
    assert check_tracked_clean.name_leaks(path, _synthetic_denylist()) == [
        f"{path}:{line}: a denied name written in shipped text" for line in (1, 2, 3)
    ]


def test_denied_name_matching_still_misses_an_interior_occurrence(
    tmp_path: Path,
) -> None:
    """CHARACTERIZATION: the residual limit, witnessed rather than implied.

    Affixes and joins cover how a name gets written by accident; a name buried
    inside a longer run is not that, and testing every substring would cost
    quadratically per run for a case nobody produces. This test exists so the
    limit is a recorded decision instead of a silence -- if it ever needs
    closing, the failure is here waiting.
    """
    path = tmp_path / "guide.md"
    path.write_text(f"pre{TOKEN}post\n", encoding="utf-8")
    assert check_tracked_clean.name_leaks(path, _synthetic_denylist()) == []


@pytest.mark.parametrize("value", ("", "two words", "hyphenated-token"))
def test_digest_refuses_non_candidate_shapes(value: str) -> None:
    """Digesting refuses every non-token shape without echoing its input."""
    with pytest.raises(ValueError) as excinfo:
        check_tracked_clean.digest(value, SALT)
    message = str(excinfo.value)
    assert message == "candidate must be exactly one nonempty alphanumeric run"
    if value:
        assert value not in message


def test_digest_is_salt_dependent_and_deterministic() -> None:
    """A digest is stable for one salt and changes under another salt."""
    first = check_tracked_clean.digest(TOKEN, SALT)
    assert first == check_tracked_clean.digest(TOKEN, SALT)
    assert first != check_tracked_clean.digest(TOKEN, b"different-salt-16")


def test_digest_normalizes_surrounding_whitespace() -> None:
    """An authored name keeps its digest when read with surrounding whitespace."""
    assert check_tracked_clean.digest(f"  {TOKEN}\n", SALT) == (
        check_tracked_clean.digest(TOKEN, SALT)
    )


def _write_digest_file(
    path: Path, content_lines: list[str], salt: str = SALT.hex()
) -> None:
    """Write one synthetic digest file from its content lines."""
    path.write_text(
        f"salt {salt}\n" + "\n".join(content_lines) + "\n", encoding="utf-8"
    )


def _synthetic_digest(value: str) -> str:
    """Return one valid synthetic digest for parser tests."""
    return check_tracked_clean.digest(value, SALT)


def test_missing_digest_file_fails_closed(tmp_path: Path) -> None:
    """A missing digest file raises exactly ValueError."""
    with pytest.raises(ValueError) as excinfo:
        check_tracked_clean.denied_digests(tmp_path / "missing.txt")
    assert type(excinfo.value) is ValueError


def test_digest_file_without_salt_fails_closed(tmp_path: Path) -> None:
    """A digest file without a salt line raises exactly ValueError."""
    path = tmp_path / "digests.txt"
    path.write_text(_synthetic_digest("alpha") + "\n", encoding="utf-8")
    with pytest.raises(ValueError) as excinfo:
        check_tracked_clean.denied_digests(path)
    assert type(excinfo.value) is ValueError


def test_second_salt_line_fails_closed(tmp_path: Path) -> None:
    """A second salt line leaves which salt is live ambiguous, so it is refused."""
    path = tmp_path / "digests.txt"
    _write_digest_file(path, [f"salt {(SALT[::-1]).hex()}", _synthetic_digest("alpha")])
    with pytest.raises(ValueError) as excinfo:
        check_tracked_clean.denied_digests(path)
    assert type(excinfo.value) is ValueError


def test_trailing_salt_line_fails_closed(tmp_path: Path) -> None:
    """A salt line after a digest line is refused rather than silently accepted."""
    path = tmp_path / "digests.txt"
    path.write_text(
        f"{_synthetic_digest('alpha')}\nsalt {SALT.hex()}\n", encoding="utf-8"
    )
    with pytest.raises(ValueError) as excinfo:
        check_tracked_clean.denied_digests(path)
    assert type(excinfo.value) is ValueError


@pytest.mark.parametrize("salt", ("00112233445566xz", "00112233445566778"))
def test_malformed_digest_salt_fails_closed(tmp_path: Path, salt: str) -> None:
    """A non-hex or odd-length salt raises exactly ValueError."""
    path = tmp_path / "digests.txt"
    _write_digest_file(path, [_synthetic_digest("alpha")], salt)
    with pytest.raises(ValueError) as excinfo:
        check_tracked_clean.denied_digests(path)
    assert type(excinfo.value) is ValueError


def test_short_digest_salt_fails_closed(tmp_path: Path) -> None:
    """A salt shorter than 16 hex characters raises exactly ValueError."""
    path = tmp_path / "digests.txt"
    _write_digest_file(path, [_synthetic_digest("alpha")], "00112233445566")
    with pytest.raises(ValueError) as excinfo:
        check_tracked_clean.denied_digests(path)
    assert type(excinfo.value) is ValueError


def test_digest_file_without_digests_fails_closed(tmp_path: Path) -> None:
    """A digest file with no digest lines raises exactly ValueError."""
    path = tmp_path / "digests.txt"
    _write_digest_file(path, [])
    with pytest.raises(ValueError) as excinfo:
        check_tracked_clean.denied_digests(path)
    assert type(excinfo.value) is ValueError


def test_junk_digest_line_fails_closed(tmp_path: Path) -> None:
    """A content line that is not a digest raises exactly ValueError."""
    path = tmp_path / "digests.txt"
    _write_digest_file(path, ["junk"])
    with pytest.raises(ValueError) as excinfo:
        check_tracked_clean.denied_digests(path)
    assert type(excinfo.value) is ValueError


def test_duplicate_digests_fail_closed(tmp_path: Path) -> None:
    """Duplicate digest lines raise exactly ValueError."""
    path = tmp_path / "digests.txt"
    value = _synthetic_digest("alpha")
    _write_digest_file(path, [value, value])
    with pytest.raises(ValueError) as excinfo:
        check_tracked_clean.denied_digests(path)
    assert type(excinfo.value) is ValueError


def test_unsorted_digests_fail_closed(tmp_path: Path) -> None:
    """Digest lines outside ascending order raise exactly ValueError."""
    path = tmp_path / "digests.txt"
    values = sorted((_synthetic_digest("alpha"), _synthetic_digest("beta")))
    _write_digest_file(path, list(reversed(values)))
    with pytest.raises(ValueError) as excinfo:
        check_tracked_clean.denied_digests(path)
    assert type(excinfo.value) is ValueError


def test_main_runs_denied_digest_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Main fails and recovers as synthetic denied prose appears and disappears."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(check_tracked_clean, "ROOT", tmp_path)
    path = Path("docs/guide.md")
    path.parent.mkdir()
    digest_path = tmp_path / "digests.txt"
    _write_digest_file(digest_path, [_synthetic_digest(TOKEN)])
    monkeypatch.setattr(check_tracked_clean, "tracked_files", lambda: [path])
    monkeypatch.setattr(check_tracked_clean, "DENIED_DIGESTS_PATH", digest_path)
    path.write_text(f"Paragraph containing {TOKEN}.\n", encoding="utf-8")
    assert check_tracked_clean.main() == 1
    path.write_text("A portable paragraph.\n", encoding="utf-8")
    assert check_tracked_clean.main() == 0


def test_documented_reports_module_and_public_names_but_skips_private_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The docstring gate catches each public AST shape without flagging private code."""
    monkeypatch.setattr(check_documented, "ROOT", tmp_path)
    path = tmp_path / "sample.py"
    path.write_text(
        "def public():\n    pass\n\n"
        "async def public_async():\n    pass\n\n"
        "class Public:\n    pass\n\n"
        "def _private():\n    pass\n",
        encoding="utf-8",
    )
    assert check_documented.undocumented(path) == [
        "sample.py: the module itself",
        "sample.py:public",
        "sample.py:public_async",
        "sample.py:Public",
    ]


def test_documented_accepts_documented_public_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A documented module and public function pass the guard."""
    monkeypatch.setattr(check_documented, "ROOT", tmp_path)
    path = tmp_path / "sample.py"
    path.write_text(
        '"""Module."""\n\ndef public():\n    """Function."""\n',
        encoding="utf-8",
    )
    assert check_documented.undocumented(path) == []


@pytest.mark.parametrize(
    ("body", "description"),
    (
        ('""', "an empty docstring"),
        ('"   "', "a spaces-only docstring"),
        ('"""\n    """', "a blank multi-line docstring"),
    ),
)
def test_documented_refuses_a_docstring_that_says_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str, description: str
) -> None:
    """REGRESSION: a string literal that documents nothing is not documentation.

    `ast.get_docstring` returns `''` rather than None for each of these, so the
    older `is None` test read them as documented and `def f(x): ""` passed the
    gate. Whitespace-only is here for the same reason: cleaning reduces it to
    the empty string, so accepting it would be accepting the empty docstring
    under another spelling.
    """
    monkeypatch.setattr(check_documented, "ROOT", tmp_path)
    path = tmp_path / "sample.py"
    path.write_text(
        f'"""Module."""\n\ndef public(x):\n    {body}\n',
        encoding="utf-8",
    )
    assert check_documented.undocumented(path) == ["sample.py:public"], description


def test_documented_refuses_an_empty_module_docstring(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """REGRESSION: the module is held to the same bar as the names inside it."""
    monkeypatch.setattr(check_documented, "ROOT", tmp_path)
    path = tmp_path / "sample.py"
    path.write_text('""\n', encoding="utf-8")
    assert check_documented.undocumented(path) == ["sample.py: the module itself"]


def _indexed_repository(root: Path) -> None:
    """Build a small repository whose index spans the root and a subdirectory."""
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
    (root / "top.md").write_text("portable prose\n", encoding="utf-8")
    nested = root / "docs"
    nested.mkdir()
    (nested / "guide.md").write_text("portable prose\n", encoding="utf-8")
    (root / "scratch.md").write_text("local scratch\n", encoding="utf-8")
    subprocess.run(["git", "add", "top.md", "docs/guide.md"], cwd=root, check=True)


def test_tracked_files_reads_the_git_index_not_the_working_tree(
    tmp_path: Path,
) -> None:
    """CHARACTERIZATION: the listing is exactly the index, subdirectories included."""
    _indexed_repository(tmp_path)
    assert check_tracked_clean.tracked_files(tmp_path) == [
        Path("docs/guide.md"),
        Path("top.md"),
    ]


def test_tracked_files_covers_the_repository_from_any_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REGRESSION: a run started one level down still reads the whole index.

    `git ls-files` reports only what lies beneath the process's working
    directory. With the listing left to inherit that, running the gate from
    `docs/` enumerated one file, found nothing wrong with the rest because it
    never opened the rest, and exited zero -- a leak planted in a tracked
    top-level file passed. A wrapper, a hook, or a CI `working-directory:` key
    is all it takes to narrow the population a green result speaks for.
    """
    _indexed_repository(tmp_path)
    monkeypatch.chdir(tmp_path / "docs")
    assert check_tracked_clean.tracked_files(tmp_path) == [
        Path("docs/guide.md"),
        Path("top.md"),
    ]


def test_a_leak_outside_the_working_directory_is_still_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REGRESSION: the end-to-end construction -- leak at the top, run from below.

    Enumerating the whole index is only half of it: the reads have to land
    where the names came from. With bare relative names opened against the
    working directory, the top-level file resolves to nothing under `docs/` and
    the gate reports clean on a file it failed to open.
    """
    _indexed_repository(tmp_path)
    digest_path = tmp_path / "digests.txt"
    _write_digest_file(digest_path, [_synthetic_digest(TOKEN)])
    monkeypatch.setattr(check_tracked_clean, "ROOT", tmp_path)
    monkeypatch.setattr(check_tracked_clean, "DENIED_DIGESTS_PATH", digest_path)
    (tmp_path / "top.md").write_text("# " + LEAK_URL + "\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path / "docs")
    assert check_tracked_clean.main() == 1
    (tmp_path / "top.md").write_text("portable prose\n", encoding="utf-8")
    assert check_tracked_clean.main() == 0


def test_a_directory_below_the_repository_root_refuses_to_be_listed(
    tmp_path: Path,
) -> None:
    """A partial listing fails closed rather than passing as the whole index.

    Asking git where the top level is, and comparing, is what turns "this
    happens to be the root" from an assumption into a checked fact.
    """
    _indexed_repository(tmp_path)
    with pytest.raises(ValueError) as excinfo:
        check_tracked_clean.tracked_files(tmp_path / "docs")
    assert "is not the top level of its git repository" in str(excinfo.value)


def test_in_repository_anchors_relative_names_and_passes_absolute_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tracked name resolves under the root; a full path is returned unchanged."""
    monkeypatch.setattr(check_tracked_clean, "ROOT", tmp_path)
    assert check_tracked_clean.in_repository(Path("docs/guide.md")) == (
        tmp_path / "docs" / "guide.md"
    )
    elsewhere = tmp_path.parent / "elsewhere" / "guide.md"
    assert check_tracked_clean.in_repository(elsewhere) == elsewhere


def test_unparsable_python_fails_closed_rather_than_passing(tmp_path: Path) -> None:
    """CHARACTERIZATION: source the gate cannot parse is refused, never skipped."""
    source = tmp_path / "broken.py"
    source.write_text("def (\n", encoding="utf-8")
    with pytest.raises(ValueError) as excinfo:
        check_tracked_clean.reference_leaks(source)
    assert "cannot inspect imports in" in str(excinfo.value)
    assert str(source) in str(excinfo.value)


def test_from_imports_report_their_top_level_package(tmp_path: Path) -> None:
    """CHARACTERIZATION: a `from` import names its root; a relative import names none."""
    source = tmp_path / "sample.py"
    source.write_text(
        "from unanticipated_toolkit.inner import helper\nfrom . import sibling\n",
        encoding="utf-8",
    )
    assert check_tracked_clean.reference_leaks(source) == [
        f"{source}: an unallowlisted top-level import ('unanticipated_toolkit')"
    ]


@pytest.mark.parametrize("requirement", ("", "!broken"))
def test_unreadable_dependency_requirement_fails_closed(
    tmp_path: Path, requirement: str
) -> None:
    """CHARACTERIZATION: a requirement with no leading name is refused, not ignored."""
    project = tmp_path / "pyproject.toml"
    project.write_text(
        f'[project]\ndependencies = ["{requirement}"]\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="cannot inspect dependency requirement"):
        check_tracked_clean.reference_leaks(project)


def test_unallowlisted_distribution_is_reported_from_every_group(
    tmp_path: Path,
) -> None:
    """CHARACTERIZATION: required and optional requirements are alike normalized and checked."""
    project = tmp_path / "pyproject.toml"
    project.write_text(
        "[project]\n"
        'dependencies = ["Unanticipated_Toolkit>=1.0"]\n'
        "[project.optional-dependencies]\n"
        'extra = ["another.tool"]\n',
        encoding="utf-8",
    )
    assert check_tracked_clean.reference_leaks(project) == [
        f"{project}: an unallowlisted distribution ('unanticipated-toolkit')",
        f"{project}: an unallowlisted distribution ('another-tool')",
    ]


@pytest.mark.parametrize(
    ("text", "opening"),
    (
        (
            '[build-system]\nrequires = ["hatchling"]\n',
            "no [project] table declaring distributions",
        ),
        ("project = 1\n", "no [project] table declaring distributions"),
        ("not = toml = [\n", "unreadable TOML ("),
    ),
)
def test_a_project_table_this_gate_cannot_read_is_reported_not_raised(
    tmp_path: Path, text: str, opening: str
) -> None:
    """REGRESSION: an unreadable project table ends the gate with a message.

    Every other condition this gate meets is written into its report and
    decides the exit status. Reading the project table was the one place that
    trusted its input, so a file with no such table ended the whole run in an
    unhandled `KeyError` naming nothing at all.
    """
    project = tmp_path / "pyproject.toml"
    project.write_text(text, encoding="utf-8")
    reported = check_tracked_clean.reference_leaks(project)
    assert len(reported) == 1
    assert reported[0].startswith(f"{project}: {opening}")


def test_allowlisted_email_is_accepted_case_insensitively(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CHARACTERIZATION: the email allowlist admits exactly the addresses it names."""
    monkeypatch.setattr(
        check_tracked_clean, "ALLOWED_EMAILS", {"release" + "@example" + ".com"}
    )
    path = tmp_path / "guide.md"
    path.write_text(
        "Write to Release" + "@Example" + ".com, never other" + "@example" + ".com\n",
        encoding="utf-8",
    )
    assert check_tracked_clean.reference_leaks(path) == [
        f"{path}: an unallowlisted email address ('other" + "@example" + ".com')"
    ]


def test_documented_main_names_every_undocumented_public_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CHARACTERIZATION: the gate exits nonzero and reports each offending name on stderr."""
    package = tmp_path / "sample_package"
    package.mkdir()
    (package / "documented.py").write_text('"""Module."""\n', encoding="utf-8")
    (package / "bare.py").write_text("def public():\n    pass\n", encoding="utf-8")
    monkeypatch.setattr(check_documented, "ROOT", tmp_path)
    monkeypatch.setattr(check_documented, "PACKAGES", (package,))
    assert check_documented.main() == 1
    report = capsys.readouterr().err
    assert "public names must carry a docstring" in report
    assert "sample_package/bare.py: the module itself" in report
    assert "sample_package/bare.py:public" in report
    assert "documented.py" not in report


def test_documented_main_accepts_a_fully_documented_package(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CHARACTERIZATION: a package whose public names all carry docstrings passes silently."""
    package = tmp_path / "sample_package"
    package.mkdir()
    (package / "documented.py").write_text(
        '"""Module."""\n\ndef public():\n    """Function."""\n', encoding="utf-8"
    )
    monkeypatch.setattr(check_documented, "ROOT", tmp_path)
    monkeypatch.setattr(check_documented, "PACKAGES", (package,))
    assert check_documented.main() == 0
    assert capsys.readouterr().err == ""


def _distribution_members(destination: Path) -> list[str]:
    """Return every file path inside a freshly built source distribution."""
    builder = SdistBuilder(str(check_tracked_clean.ROOT))
    artifact = next(iter(builder.build(directory=str(destination))))
    with tarfile.open(artifact) as archive:
        return [
            member.name.split("/", 1)[1]
            for member in archive.getmembers()
            if member.isfile()
        ]


def _scanned_files() -> set[str]:
    """Return the paths the publishability gate reads, selected as the gate selects."""
    return {
        str(path)
        for path in check_tracked_clean.tracked_files()
        if path.is_file() and check_tracked_clean.is_shipped_surface(path)
    }


def test_every_file_in_the_built_distribution_is_gated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REGRESSION: what ships is a subset of what the gate reads, file by file.

    A hand-kept list of shipped directories drifts the moment packaging changes,
    and drift is invisible until something unpublishable rides out in a file
    nobody remembered to name. Building the distribution and reading its members
    back is the only statement of the shipped set that cannot go stale.

    The comparison is against the set of paths the gate selects, not against the
    predicate it selects them with. Membership is the claim; the predicate
    answers only about a name, and it accepts every name that is not exempt,
    including the name of a file the gate never opens. The gate reads the git
    index and the build reads the working tree, so a file that is neither
    tracked nor ignored ships without ever being scanned, and a check written
    against the predicate alone passes on it. Comparing against the selected
    set is what makes such a file visible here.
    """
    monkeypatch.chdir(check_tracked_clean.ROOT)
    members = _distribution_members(tmp_path)
    # PKG-INFO is generated at build time from metadata that is itself gated:
    # the readme, the description, and the project URLs all live in files the
    # gate reads. The gate script is the single named exemption, and naming it
    # here is deliberate: a second entry in this set is a second file shipping
    # unread, and has to be argued for in review rather than appearing quietly.
    ungated = set(members) - _scanned_files() - {"PKG-INFO"}
    assert ungated == {"scripts/check_tracked_clean.py"}
    # The build has to have produced something, or the assertion above is vacuous.
    assert "pyproject.toml" in members
    assert "SECURITY.md" in members


def test_nothing_under_the_local_agent_directory_can_reach_the_distribution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REGRESSION: `.claude/` stays out of the distribution, proved on a live file.

    This project's git worktrees live under `.claude/worktrees/<name>/`, and a
    worktree is a full second copy of the repository. The backend selects the
    working tree minus what version control ignores and never asks git what is
    tracked, so while that directory was unignored a copy shipped whole, every
    file of it unread: the gate reads the index, and a worktree is not in it.

    A checkout with no `.claude/` satisfies any claim about the distribution's
    members for free, and a CI checkout is exactly that, so this plants a file
    there and then asks. What holds the directory out is one line in
    `.gitignore` -- the size of line a later change drops without noticing,
    which is why the claim is worth a test of its own rather than being left to
    the membership check above.
    """
    monkeypatch.chdir(check_tracked_clean.ROOT)
    local = check_tracked_clean.ROOT / ".claude"
    # A developer's own `.claude/` holds live worktrees, so the cleanup below
    # removes the topmost directory this test had to create and nothing above it.
    created = local if not local.is_dir() else local / "sentinel-worktree"
    planted = local / "sentinel-worktree" / "CONTRIBUTING.md"
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.write_text("Local scratch that must never ship.\n", encoding="utf-8")
    try:
        members = _distribution_members(tmp_path)
    finally:
        shutil.rmtree(created)
    assert [name for name in members if name.startswith(".claude/")] == []
    # The build has to have produced something, or the assertion above is vacuous.
    assert "pyproject.toml" in members


@pytest.mark.parametrize(
    "name",
    (
        ".github/workflows/ci.yml",
        ".gitignore",
        ".pre-commit-config.yaml",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "LICENSE",
        "Makefile",
        "RELEASING.md",
        "SECURITY.md",
        "denied-name-digests.txt",
        "examples/mixing.py",
        "schema/tiergraph.schema.json",
    ),
)
def test_main_refuses_an_external_reference_in_each_shipped_file_named_here(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str
) -> None:
    """REGRESSION: the reference check fires in each file named above.

    Each of these ships and each once sat outside the gate, so an unapproved
    reference in any of them passed. The release checklist carried exactly that
    class of reference and had to be corrected by hand.

    The names are the claim's whole extent, which is why this says "named here"
    rather than naming shipped files in general. The population claim -- that
    nothing ships unread -- belongs to
    `test_every_file_in_the_built_distribution_is_gated`, which derives its set
    from a built distribution and so cannot go stale as packaging moves. What
    these add is the other direction: that the checks actually fire on a leak
    written into a Markdown file, a build recipe, a workflow, an ignore file, a
    digest list, a JSON artifact, and an example source, rather than only
    selecting them.

    Deriving this list from `_scanned_files()` was weighed and rejected. That
    helper selects with `is_shipped_surface`, which answers about a name and
    nothing else, so a parametrize drawn from it would assert that the files the
    predicate selects are the files the predicate selects -- 138 cases when this
    was written, none of which can fail, standing in for a short list that
    records where this gate has actually been blind.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(check_tracked_clean, "ROOT", tmp_path)
    path = Path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    digest_path = tmp_path / "digests.txt"
    _write_digest_file(digest_path, [_synthetic_digest(TOKEN)])
    monkeypatch.setattr(check_tracked_clean, "tracked_files", lambda: [path])
    monkeypatch.setattr(check_tracked_clean, "DENIED_DIGESTS_PATH", digest_path)
    # A comment line reads as prose in Markdown, a recipe, a workflow, and an
    # ignore file, and parses as Python, so one payload covers every shipped
    # file kind without tripping the import parser on the source ones.
    path.write_text(f"# See {LEAK_URL} for details.\n", encoding="utf-8")
    assert check_tracked_clean.main() == 1
    path.write_text("# A portable paragraph.\n", encoding="utf-8")
    assert check_tracked_clean.main() == 0


def test_only_the_gate_script_itself_is_exempt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """CHARACTERIZATION: the one exemption is by file name and covers nothing else.

    The gate has to write down the patterns it forbids and the references it
    allows, so it cannot pass its own check. Every other file can.

    "Only" is a claim about the whole exemption list, so the list itself is
    read here: a second name added to it fails this test rather than joining
    an exemption nothing measures.
    """
    assert check_tracked_clean.EXEMPT_NAMES == frozenset({check_tracked_clean.SELF})
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(check_tracked_clean, "ROOT", tmp_path)
    digest_path = tmp_path / "digests.txt"
    _write_digest_file(digest_path, [_synthetic_digest(TOKEN)])
    monkeypatch.setattr(check_tracked_clean, "DENIED_DIGESTS_PATH", digest_path)
    scripts = Path("scripts")
    scripts.mkdir()
    exempt = scripts / check_tracked_clean.SELF
    neighbor = scripts / "generate_something.py"
    for path in (exempt, neighbor):
        path.write_text(f"# See {LEAK_URL}\n", encoding="utf-8")
    monkeypatch.setattr(check_tracked_clean, "tracked_files", lambda: [exempt])
    assert check_tracked_clean.main() == 0
    monkeypatch.setattr(check_tracked_clean, "tracked_files", lambda: [neighbor])
    assert check_tracked_clean.main() == 1


def test_a_tracked_entry_that_is_not_a_file_is_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """CHARACTERIZATION: the index can name a directory, and reading one would raise."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(check_tracked_clean, "ROOT", tmp_path)
    digest_path = tmp_path / "digests.txt"
    _write_digest_file(digest_path, [_synthetic_digest(TOKEN)])
    monkeypatch.setattr(check_tracked_clean, "DENIED_DIGESTS_PATH", digest_path)
    submodule = Path("vendored")
    submodule.mkdir()
    monkeypatch.setattr(check_tracked_clean, "tracked_files", lambda: [submodule])
    assert check_tracked_clean.main() == 0


@pytest.mark.parametrize("suffix", (".md", ".py"))
def test_shipped_content_that_is_not_utf8_fails_closed_with_a_reason(
    tmp_path: Path, suffix: str
) -> None:
    """REGRESSION: a shipped file the gate cannot decode is refused, not skipped.

    Widening the checked set to everything that ships admits file kinds the old
    set could not contain. An undecodable one has to fail with a message that
    says what to do, rather than ending the run in a decoder traceback.
    """
    path = (tmp_path / "shipped").with_suffix(suffix)
    path.write_bytes(b"\xff\xfe not text")
    checks: tuple[Callable[[], list[str]], ...] = (
        lambda: check_tracked_clean.reference_leaks(path),
        lambda: check_tracked_clean.name_leaks(path, _synthetic_denylist()),
    )
    for check in checks:
        with pytest.raises(ValueError) as excinfo:
            check()
        assert "ships but is not UTF-8 text" in str(excinfo.value)
        assert str(path) in str(excinfo.value)


@pytest.mark.parametrize(
    "name", ("Makefile", "workflow.yml", "config.yaml", ".gitignore")
)
def test_non_python_shipped_files_are_never_parsed_as_python(
    tmp_path: Path, name: str
) -> None:
    """CHARACTERIZATION: only .py sources and Markdown fences reach the import parser.

    The widened set carries build recipes and workflow definitions. Their text is
    not Python, so parsing them would raise on content that is perfectly correct.
    """
    path = tmp_path / name
    path.write_text(
        "check: venv lint\n\t@$(VENV_PYTHON) -m ruff check .\n"
        "  - uses: actions/checkout@v5\n",
        encoding="utf-8",
    )
    assert check_tracked_clean.reference_leaks(path) == []
