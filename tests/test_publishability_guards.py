"""Prove that publishability gates reject representative defects."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts import check_documented, check_tracked_clean


def _denied_names() -> tuple[str, ...]:
    """Load the repository's real denied names for test parametrization."""
    if not check_tracked_clean.DENIED_NAMES_PATH.exists():
        return ()
    return check_tracked_clean.denied_names(check_tracked_clean.DENIED_NAMES_PATH)


# Repository and CI runs have this file; if deleted, tracked-clean fails outright.
_REQUIRES_DENYLIST = pytest.mark.skipif(
    not check_tracked_clean.DENIED_NAMES_PATH.exists(),
    reason="the denylist is repository-only and excluded from the distribution",
)


@pytest.mark.parametrize(
    ("content", "reason"),
    (
        ("/" + "Users/contributor/project", "macOS home-directory"),
        ("/" + "home/contributor/project", "Linux home-directory"),
        ("." + "ssh/config", "SSH configuration"),
        ("file:" + "///tmp/report", "absolute local file URL"),
        ("Generated " + "by AI", "AI/tool attribution"),
        ("As " + "an AI", "AI/tool attribution"),
        ("Generated " + "on 2026-08-23", "generated timestamp"),
    ),
)
def test_tracked_clean_leaks_detects_each_forbidden_surface(
    tmp_path: Path, content: str, reason: str
) -> None:
    """Every forbidden pattern produces its specific publishability reason."""
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


@_REQUIRES_DENYLIST
def test_denied_names_are_declared() -> None:
    """The real denylist is nonempty, normalized, unique, and sorted."""
    names = _denied_names()
    assert names
    assert all(name == name.lower() for name in names)
    assert len(names) == len(set(names))
    assert names == tuple(sorted(names))


@pytest.mark.parametrize(
    "name",
    _denied_names(),
    ids=[f"name-{index}" for index in range(len(_denied_names()))],
)
def test_bare_project_prose_is_denied(tmp_path: Path, name: str) -> None:
    """Every declared name is caught in the shape of the shipped paragraph."""
    path = tmp_path / "guide.md"
    path.write_text(
        f"Downstream migration ({name}). {name} is migrating onto tiergraph: the\n"
        "remaining subsystems are still on its embedded engine.\n",
        encoding="utf-8",
    )
    reason = "a sibling repository named in shipped text"
    assert check_tracked_clean.name_leaks(path, (name,)) == [
        f"{path}:1: {reason} ({name!r})",
        f"{path}:1: {reason} ({name!r})",
    ]
    assert check_tracked_clean.reference_leaks(path) == []


@_REQUIRES_DENYLIST
def test_denied_name_matching_ignores_case_and_respects_boundaries(
    tmp_path: Path,
) -> None:
    """Matching preserves spellings while accepting only deliberate boundaries."""
    name = _denied_names()[0]
    path = tmp_path / "guide.md"
    upper = name.upper()
    title = name.title()
    path.write_text(
        f"{upper}\n{title}\n{name}_bridge\n{name}s\nmy{name}\n",
        encoding="utf-8",
    )
    reason = "a sibling repository named in shipped text"
    assert check_tracked_clean.name_leaks(path, (name,)) == [
        f"{path}:1: {reason} ({upper!r})",
        f"{path}:2: {reason} ({title!r})",
        f"{path}:3: {reason} ({name!r})",
    ]


def test_missing_denylist_fails_closed(tmp_path: Path) -> None:
    """A missing denylist raises exactly ValueError."""
    with pytest.raises(ValueError) as excinfo:
        check_tracked_clean.denied_names(tmp_path / "missing.txt")
    assert type(excinfo.value) is ValueError


def test_comment_only_denylist_fails_closed(tmp_path: Path) -> None:
    """A denylist without names raises exactly ValueError."""
    path = tmp_path / "denied.txt"
    path.write_text("# comment\n  # another comment\n\n", encoding="utf-8")
    with pytest.raises(ValueError) as excinfo:
        check_tracked_clean.denied_names(path)
    assert type(excinfo.value) is ValueError


@_REQUIRES_DENYLIST
def test_denylist_parsing_drops_comments_and_blanks(tmp_path: Path) -> None:
    """Parsing ignores comments and blanks and normalizes unique names."""
    names = _denied_names()
    first, second = names[:2]
    path = tmp_path / "denied.txt"
    path.write_text(
        f"# comment\n\n{second.upper()}\n  {first}  \n{second}\n",
        encoding="utf-8",
    )
    expected = tuple(sorted((first, second)))
    assert check_tracked_clean.denied_names(path) == expected


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
