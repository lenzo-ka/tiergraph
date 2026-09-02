"""Prove the publishability gate selects by path and reads workflow actions."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts import check_tracked_clean

TOKEN = "sentineltoken"
SALT = bytes.fromhex("00112233445566778899aabbccddeeff")
# Joined at run time, as elsewhere in this suite: this module is itself a
# shipped surface, so a literal counter-example would be extracted from it and
# refused by the gate it is written to exercise.
LEAK = "/" + "Users/contributor/scratch"
WORKFLOWS = Path(".github/workflows")


def _isolate(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    """Point the gate at an empty tree with a denylist holding one token."""
    monkeypatch.chdir(root)
    monkeypatch.setattr(check_tracked_clean, "ROOT", root)
    digests = root / "digests.txt"
    digests.write_text(
        f"salt {SALT.hex()}\n{check_tracked_clean.digest(TOKEN, SALT)}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(check_tracked_clean, "DENIED_DIGESTS_PATH", digests)


def _plant(root: Path, name: str, text: str) -> Path:
    """Write one file under the isolated root and return its relative path."""
    path = Path(name)
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / path).write_text(text, encoding="utf-8")
    return path


def test_the_exemption_covers_one_path_not_every_file_sharing_its_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """REGRESSION: a file elsewhere cannot inherit the gate script's exemption.

    The predicate was `path.name not in EXEMPT_NAMES` -- a question about a name
    standing in for one about a path. Any file anywhere in the tree that adopted
    the exempt basename was selected out of the run, and the shipped packages
    are exactly where such a file does damage: `src/tiergraph/` is a wheel
    package directory, so the planted module below ships to every installer
    while the gate that exists to read it reports a clean tree and exits zero.
    Measured before the fix, on this construction: exit 0.

    Both files carry the same leak, so the only thing separating them is where
    they sit -- which is the claim.
    """
    _isolate(monkeypatch, tmp_path)
    exempt = _plant(tmp_path, f"scripts/{check_tracked_clean.SELF}", f"# {LEAK}\n")
    impostor = _plant(
        tmp_path, f"src/tiergraph/{check_tracked_clean.SELF}", f"# {LEAK}\n"
    )
    assert check_tracked_clean.is_shipped_surface(exempt) is False
    assert check_tracked_clean.is_shipped_surface(impostor) is True
    monkeypatch.setattr(check_tracked_clean, "tracked_files", lambda: [exempt])
    assert check_tracked_clean.main() == 0
    monkeypatch.setattr(check_tracked_clean, "tracked_files", lambda: [impostor])
    assert check_tracked_clean.main() == 1


def test_an_unallowlisted_action_reference_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """REGRESSION: a third-party action is a dependency the gate has to see.

    `URL` requires a scheme and `DOMAIN` requires a dotted top-level domain, so
    `owner/action@ref` matched neither and the gate read both workflow files
    from end to end and reported nothing. That silence was the defect: the one
    check meant to notice a dependency being added could not notice the kind of
    dependency this repository adds most easily -- somebody else's code, fetched
    at run time and executed with the checkout in scope.

    The pinned form is used for the counter-example so the refusal cannot be the
    pin check answering instead of the allowlist.
    """
    _isolate(monkeypatch, tmp_path)
    commit = "0" * 40
    path = _plant(
        tmp_path,
        str(WORKFLOWS / "sample.yml"),
        f"jobs:\n  a:\n    steps:\n      - uses: stranger/action@{commit}\n",
    )
    messages = check_tracked_clean.reference_leaks(path)
    assert messages == [
        f"{path}: an unallowlisted action reference ('stranger/action')"
    ]


@pytest.mark.parametrize("ref", ("v3", "release/v1", "main"))
def test_an_allowlisted_action_on_a_floating_ref_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, ref: str
) -> None:
    """REGRESSION: an allowlisted action still has to name a commit.

    Allowlisting settles whose code runs and nothing else. A major tag and a
    release branch are both repointable by the account that owns the action, so
    a reference to one delegates the choice of revision to that account for
    every future run -- including the run in `publish.yml` that holds
    `id-token: write` and can upload under this project's name on PyPI. The
    three refs are the three floating spellings this repository actually
    carried: a major tag, a release branch, and a plain branch name.
    """
    _isolate(monkeypatch, tmp_path)
    path = _plant(
        tmp_path,
        str(WORKFLOWS / "sample.yml"),
        f"jobs:\n  a:\n    steps:\n      - uses: actions/checkout@{ref}\n",
    )
    messages = check_tracked_clean.reference_leaks(path)
    assert len(messages) == 1
    assert "is not pinned to a commit" in messages[0]
    assert repr(ref) in messages[0]


def test_a_reference_outside_the_workflow_directory_is_read_but_not_pinned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """CHARACTERIZATION: the two conditions have different scopes, on purpose.

    Whose code runs is worth reading wherever a reference is written; which
    revision runs is a decision only in a file the runner executes. Documentation
    showing a workflow snippet is a mention, not a use, and holding it to forty
    hex characters would cost a legible example and gain nothing anyone runs. So
    the same line refuses in one place and passes in the other -- which is the
    claim, and is why both halves are asserted here rather than only the pass.
    """
    _isolate(monkeypatch, tmp_path)
    step = "jobs:\n  a:\n    steps:\n      - uses: actions/checkout@v5\n"
    quoted = _plant(tmp_path, "docs/example.yml", step)
    executed = _plant(tmp_path, str(WORKFLOWS / "sample.yml"), step)
    assert check_tracked_clean.reference_leaks(quoted) == []
    assert len(check_tracked_clean.reference_leaks(executed)) == 1


def test_the_shipped_workflows_name_only_allowlisted_pinned_actions() -> None:
    """REGRESSION: the live workflow files satisfy the check on real content.

    The constructions above run on planted files, which proves the check fires
    and proves nothing about the workflows that actually run. This reads the
    tracked ones and requires at least one reference in each, so a workflow
    emptied of steps -- or a regex that stopped matching the shipped spelling --
    fails here rather than passing as a file with nothing to find.
    """
    workflows = sorted(
        path
        for path in check_tracked_clean.tracked_files()
        if path.parent == WORKFLOWS and path.suffix == ".yml"
    )
    assert [path.name for path in workflows] == ["ci.yml", "publish.yml"]
    for path in workflows:
        text = check_tracked_clean.shipped_text(path)
        references = check_tracked_clean.ACTION.findall(text)
        assert references, f"{path} names no action"
        assert check_tracked_clean._action_leaks(path, text) == []
