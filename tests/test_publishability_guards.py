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
