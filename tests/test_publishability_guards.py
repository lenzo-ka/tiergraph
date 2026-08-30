"""Prove that publishability gates reject representative defects."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts import check_documented, check_tracked_clean

TOKEN = "sentineltoken"
SALT = bytes.fromhex("00112233445566778899aabbccddeeff")


def _synthetic_denylist() -> check_tracked_clean.Denylist:
    """Return a denylist containing only the synthetic test token."""
    return check_tracked_clean.Denylist(
        salt=SALT,
        digests=frozenset({check_tracked_clean.digest(TOKEN.lower(), SALT)}),
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


def test_denied_name_matching_ignores_case_and_respects_boundaries(
    tmp_path: Path,
) -> None:
    """Matching case-folds both paths and accepts only token boundaries."""
    path = tmp_path / "guide.md"
    path.write_text(
        f"{TOKEN.upper()}\n{TOKEN.title()}\n{TOKEN}_bridge\n{TOKEN}s\nmy{TOKEN}\n",
        encoding="utf-8",
    )
    assert check_tracked_clean.name_leaks(path, _synthetic_denylist()) == [
        f"{path}:1: a denied name written in shipped text",
        f"{path}:2: a denied name written in shipped text",
        f"{path}:3: a denied name written in shipped text",
    ]


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
