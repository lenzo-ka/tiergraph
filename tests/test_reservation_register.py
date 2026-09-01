"""Prove that the reservation register refuses stale and undeclared deferrals."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts import check_reservations

ROOT = check_reservations.ROOT
MODULE = check_reservations.MODULE_SYMBOL


def _module(path: Path, source: str) -> Path:
    """Write one synthetic Python module and return its path."""
    path.write_text(source, encoding="utf-8")
    return path


def test_every_enforceable_reservation_is_still_live() -> None:
    """No reservation a predicate can decide has been overtaken by the tree."""
    overtaken = {
        reservation.name: reservation.overtaken()
        for reservation in check_reservations.RESERVATIONS
    }
    assert {name: value for name, value in overtaken.items() if value is not None} == {}


def test_the_register_matches_the_prose_and_covers_every_announcement() -> None:
    """The committed register is pinned to the tree and complete over it."""
    entries = check_reservations.registered()
    assert check_reservations.unpinned(entries) == []
    assert (
        check_reservations.undeclared(entries, check_reservations.shipped_python())
        == []
    )
    assert check_reservations.main() == 0


def test_a_resolver_that_produces_the_reserved_refusal_is_named(tmp_path: Path) -> None:
    """A shipped module naming the reserved member reports it with its line."""
    source = _module(
        tmp_path / "resolver.py",
        "from tiergraph.path import PathRefusalCode\n"
        "\n"
        "\n"
        "def refuse() -> PathRefusalCode:\n"
        "    return PathRefusalCode.BOUNDARY_NOT_IN_PARENT\n",
    )
    evidence = check_reservations._reserved_refusal_is_produced([source])
    assert evidence is not None
    assert "resolver.py:5" in evidence


def test_an_unproduced_reserved_refusal_reports_nothing(tmp_path: Path) -> None:
    """A module naming only other refusal codes leaves the reservation live."""
    source = _module(
        tmp_path / "quiet.py",
        "from tiergraph.path import PathRefusalCode\n"
        "\n"
        "\n"
        "def refuse() -> PathRefusalCode:\n"
        "    return PathRefusalCode.OUT_OF_RANGE\n",
    )
    assert check_reservations._reserved_refusal_is_produced([source]) is None


def test_an_arrived_construction_helper_is_named(tmp_path: Path) -> None:
    """A public convenience name spelling a withheld helper reports the arrival."""
    source = _module(
        tmp_path / "build.py",
        "class Document:\n"
        "    def choice(self) -> None:\n"
        "        return None\n"
        "\n"
        "    def _select_private(self) -> None:\n"
        "        return None\n"
        "\n"
        "    def relate(self) -> None:\n"
        "        return None\n",
    )
    evidence = check_reservations._build_ergonomics_have_landed([source])
    assert evidence == "convenience construction now publishes build.py:choice"


def test_construction_helpers_that_have_not_arrived_report_nothing() -> None:
    """The shipped convenience module still publishes none of the three concepts."""
    assert check_reservations._build_ergonomics_have_landed() is None


def test_stale_reports_the_condition_that_now_holds() -> None:
    """A stale reservation names its site, the evidence, and its condition."""
    reservation = check_reservations.Reservation(
        name="synthetic",
        site="src/tiergraph/path.py",
        symbol="PathRefusalCode",
        text="unused",
        condition="the synthetic condition",
        overtaken=lambda: "the synthetic evidence",
    )
    (message,) = check_reservations.stale([reservation])
    assert "synthetic: src/tiergraph/path.py:PathRefusalCode" in message
    assert "the synthetic evidence" in message
    assert "the synthetic condition" in message


def test_a_live_reservation_produces_no_stale_message() -> None:
    """A predicate reporting no evidence leaves the reservation unreported."""
    reservation = check_reservations.Reservation(
        name="synthetic",
        site="src/tiergraph/path.py",
        symbol="PathRefusalCode",
        text="unused",
        condition="the synthetic condition",
        overtaken=lambda: None,
    )
    assert check_reservations.stale([reservation]) == []


@pytest.mark.parametrize(
    ("site", "symbol", "text", "reason"),
    (
        ("src/tiergraph/absent.py", MODULE, "unused", "no longer exists"),
        ("src/tiergraph/path.py", "Absent", "unused", "has no documented Absent"),
        ("src/tiergraph/path.py", "PathRefusalCode", "reworded", "missing or changed"),
    ),
)
def test_an_entry_that_no_longer_matches_its_prose_fails(
    site: str, symbol: str, text: str, reason: str
) -> None:
    """Deleting, moving, or rewording a reservation refuses its register entry."""
    entry = check_reservations.Registered(
        name="synthetic", site=site, symbol=symbol, text=text, condition="unused"
    )
    (message,) = check_reservations.unpinned([entry])
    assert reason in message


def test_the_declared_readout_prose_is_pinned_exactly_once() -> None:
    """The readout obligation appears once at the declaration readers inspect."""
    entry = next(
        entry
        for entry in check_reservations.UNENFORCEABLE
        if entry.name == "declared-readout"
    )
    documented = dict(check_reservations.docstrings(ROOT / entry.site))
    assert documented[entry.symbol].count(entry.text) == 1
    assert check_reservations.unpinned([entry]) == []


def test_rewording_the_declared_readout_prose_is_refused() -> None:
    """The register refuses a changed statement of the readout obligation."""
    entry = next(
        entry
        for entry in check_reservations.UNENFORCEABLE
        if entry.name == "declared-readout"
    )
    changed = check_reservations.Unenforceable(
        name=entry.name,
        site=entry.site,
        symbol=entry.symbol,
        text=entry.text.replace("must be declared", "should be declared"),
        condition=entry.condition,
        why=entry.why,
    )
    assert check_reservations.unpinned([changed]) == [
        "declared-readout: the reserving prose in "
        "src/tiergraph/fold.py:FoldDeclaration is missing or changed"
    ]


def test_removing_the_declared_readout_entry_is_refused() -> None:
    """The vocabulary scan refuses the obligation when its entry is removed."""
    entries = tuple(
        entry
        for entry in check_reservations.registered()
        if entry.name != "declared-readout"
    )
    assert check_reservations.undeclared(
        entries, [ROOT / "src" / "tiergraph" / "fold.py"]
    ) == [
        "src/tiergraph/fold.py:FoldDeclaration announces a reservation "
        "('not currently') that the register does not carry"
    ]


def test_an_undeclared_announcement_is_refused(tmp_path: Path) -> None:
    """A new docstring using the reservation vocabulary must be registered."""
    source = _module(
        tmp_path / "fresh.py",
        '"""Do one thing.\n\nA second operation is deferred until the ruling lands.\n"""\n',
    )
    (message,) = check_reservations.undeclared([], [source])
    assert "fresh.py:<module> announces a reservation ('deferred')" in message


def test_a_declared_announcement_and_ordinary_prose_are_accepted(
    tmp_path: Path,
) -> None:
    """A registered docstring passes, and a word merely containing one does not."""
    declared = _module(
        tmp_path / "declared.py",
        '"""Do one thing.\n\nA second operation is deferred until the ruling lands.\n"""\n',
    )
    ordinary = _module(
        tmp_path / "ordinary.py",
        '"""Preserve every element of the sequence.\n\nThis module preserves order and multiplicity.\n"""\n',
    )
    entry = check_reservations.Registered(
        name="synthetic",
        site=check_reservations.label(declared),
        symbol=MODULE,
        text="deferred",
        condition="unused",
    )
    assert check_reservations.undeclared([entry], [declared, ordinary]) == []


def test_undocumented_definitions_and_module_are_skipped(tmp_path: Path) -> None:
    """Only documented symbols are collected, and nested ones stay qualified."""
    source = _module(
        tmp_path / "mixed.py",
        "MARKER = 1\n"
        "\n"
        "\n"
        "def bare() -> None:\n"
        "    return None\n"
        "\n"
        "\n"
        "class Outer:\n"
        '    """Document the outer class."""\n'
        "\n"
        "    def inner(self) -> None:\n"
        '        """Document the inner method."""\n'
        "        return None\n",
    )
    assert check_reservations.docstrings(source) == [
        ("Outer", "Document the outer class."),
        ("Outer.inner", "Document the inner method."),
    ]


def test_shipped_python_reads_every_covered_surface() -> None:
    """The covered surfaces are the two packages and the runnable examples."""
    paths = check_reservations.shipped_python()
    assert ROOT / "src" / "tiergraph" / "path.py" in paths
    assert ROOT / "src" / "tiergraph_dot" / "__init__.py" in paths
    assert ROOT / "examples" / "json_document.py" in paths
    assert not [path for path in paths if path.is_relative_to(ROOT / "tests")]
    assert check_reservations.shipped_python(()) == []


def test_a_covered_file_is_named_by_its_path_from_the_root(tmp_path: Path) -> None:
    """Evidence locates a shipped file by repository path, not by bare name."""
    assert (
        check_reservations.label(ROOT / "src" / "tiergraph" / "path.py")
        == "src/tiergraph/path.py"
    )
    assert check_reservations.label(tmp_path / "outside.py") == "outside.py"


def test_unenforceable_entries_declare_their_condition_and_reason() -> None:
    """A reservation no observable decides still records what it waits on."""
    assert check_reservations.UNENFORCEABLE
    for entry in check_reservations.UNENFORCEABLE:
        assert entry.condition
        assert entry.why


def test_main_names_a_broken_entry_and_exits_non_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A register entry pointing at prose that is gone fails the gate by name."""
    broken = check_reservations.Unenforceable(
        name="synthetic",
        site="src/tiergraph/absent.py",
        symbol=MODULE,
        text="unused",
        condition="unused",
        why="unused",
    )
    monkeypatch.setattr(check_reservations, "UNENFORCEABLE", (broken,))
    assert check_reservations.main() == 1
    captured = capsys.readouterr()
    assert "still be true and still be declared" in captured.err
    assert "synthetic: src/tiergraph/absent.py no longer exists" in captured.err
