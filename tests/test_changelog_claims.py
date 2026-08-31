"""Prove the changelog gate refutes its closed claim vocabulary."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from scripts import check_changelog_claims as claims

from tiergraph import __version__


def repository(tmp_path: Path, *, wire: bool = True) -> Path:
    """Return a tagged release followed by changed current artifacts."""
    root = tmp_path / "repository"
    root.mkdir(parents=True)
    paths = (claims.SCHEMA_PATH, claims.STAMP_PATH)
    for path in paths:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"released {path.name}\n", encoding="utf-8")
    if wire:
        target = root / claims.WIRE_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('FORMAT_VERSION = "6"\n', encoding="utf-8")
    for arguments in (
        ("init", "-q", "-b", "main"),
        ("config", "user.email", "gate@localhost"),
        ("config", "user.name", "gate"),
        ("add", "."),
        ("commit", "-q", "-m", "release"),
        ("tag", "v0.1.0"),
    ):
        subprocess.run(["git", *arguments], cwd=root, check=True, capture_output=True)
    for path in paths:
        (root / path).write_text(f"current {path.name}\n", encoding="utf-8")
    return root


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        (
            '- `FORMAT_VERSION` stays `"6"` for this change.',
            "claims FORMAT_VERSION '6'",
        ),
        (
            "- The schema artifact and its stamp are byte-identical.",
            "claims byte identity",
        ),
        (
            "- The wire is untouched.",
            "claims the wire did not move",
        ),
        (
            "- The document format is unchanged.",
            "claims the wire did not move",
        ),
    ],
)
def test_each_false_claim_shape_is_refused_by_name(
    tmp_path: Path, entry: str, message: str
) -> None:
    """NEGATIVE CONTROL: each ruled defect independently makes the gate fail."""
    root = repository(tmp_path)
    text = f"## [Unreleased]\n\n{entry}\n"
    refused = claims.findings(text, root, "0.2.0")
    assert len(refused) == 1
    assert message in refused[0]


def test_a_clean_entry_and_an_old_release_claim_pass(tmp_path: Path) -> None:
    """POSITIVE CONTROL: truthful current prose and historical prose pass."""
    root = repository(tmp_path)
    text = """## [Unreleased]

- `FORMAT_VERSION` is `"0.2.0"` and the schema changed.

## [0.1.0] - 2026-08-23

- The wire is untouched and the schema artifact is byte-identical.
"""
    assert claims.findings(text, root, "0.2.0") == []


def test_literal_artifact_paths_are_in_the_closed_vocabulary() -> None:
    """Literal tracked paths name the same two artifacts as the prose aliases."""
    text = (
        f"{claims.SCHEMA_PATH.as_posix()} and {claims.STAMP_PATH.as_posix()} "
        "are byte-identical"
    )
    assert claims.named_artifacts(text) == (claims.SCHEMA_PATH, claims.STAMP_PATH)
    assert claims.named_artifacts(claims.SCHEMA_PATH.as_posix()) == (
        claims.SCHEMA_PATH,
    )
    assert claims.named_artifacts("no tracked artifact") == ()


def test_truthful_identity_and_an_unmoved_wire_pass(tmp_path: Path) -> None:
    """True closed-shape claims are checked and retained, not forbidden."""
    root = repository(tmp_path)
    released = subprocess.run(
        ["git", "show", f"v0.1.0:{claims.SCHEMA_PATH.as_posix()}"],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    (root / claims.SCHEMA_PATH).write_bytes(released)
    text = """## [Unreleased]

- The schema artifact is byte-identical.
- The wire is untouched.
"""
    assert claims.findings(text, root, "6") == []


def test_current_version_heading_checks_non_version_claims(tmp_path: Path) -> None:
    """A dated current heading is active, while quoted versions stay Unreleased-only."""
    root = repository(tmp_path)
    text = f"""## [{__version__}] - 2026-08-30

- `FORMAT_VERSION` stays `\"6\"`, but the wire is untouched.
"""
    refused = claims.findings(text, root, "0.2.0")
    assert len(refused) == 1
    assert "claims the wire did not move" in refused[0]
    assert "claims FORMAT_VERSION" not in refused[0]


def test_main_reports_refusal_and_success(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The command's status and streams expose both halves of the gate."""
    root = repository(tmp_path)
    assert claims.main(root, "## [Unreleased]\n\n- The wire is untouched.\n") == 1
    assert "false changelog claims:" in capsys.readouterr().err
    assert claims.main(root, "## [Unreleased]\n\n- The wire changed.\n") == 0
    assert "changelog claims match" in capsys.readouterr().out


def test_missing_baseline_and_missing_release_wire_refuse(tmp_path: Path) -> None:
    """A comparison never passes when its tag or format value is unavailable."""
    empty = tmp_path / "empty"
    empty.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=empty, check=True)
    assert claims.findings("", empty) == [
        "release tags are unavailable; no changelog baseline was checked"
    ]

    root = repository(tmp_path / "second", wire=False)
    assert "does not contain a readable" in claims.findings("", root)[0]


def test_a_failed_tag_listing_has_no_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A git refusal is distinct from a successful empty tag listing."""
    monkeypatch.setattr(claims, "git_output", lambda arguments, cwd: None)
    assert claims.newest_release_tag(tmp_path) is None
