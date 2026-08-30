"""Prove that publishability gates reject representative defects."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts import check_documented, check_tracked_clean


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


def test_bare_project_prose_is_the_documented_gap(tmp_path: Path) -> None:
    """An ordinary project-like word alone has no reference syntax to extract."""
    path = tmp_path / "guide.md"
    path.write_text("quandaryzoo is migrating onto tiergraph.\n", encoding="utf-8")
    assert check_tracked_clean.reference_leaks(path) == []


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
