"""Run the small worked examples and check their user-visible results."""

from __future__ import annotations

import pytest
from examples.caption_alignment import main as caption_alignment_main
from examples.critical_path import main as critical_path_main
from examples.json_document import main as json_document_main
from examples.text_segmentation import main as text_segmentation_main


def test_caption_alignment(capsys: pytest.CaptureFixture[str]) -> None:
    """The selected word reaches its three aligned phones."""
    assert caption_alignment_main() == 0
    assert capsys.readouterr().out == "['K', 'AE', 'T']\n"


def test_text_segmentation(capsys: pytest.CaptureFixture[str]) -> None:
    """Both span tiers render the reconstructed input and rulers."""
    assert text_segmentation_main() == 0
    output = capsys.readouterr().out
    assert output.count("Cats nap.\n") == 2
    assert "word spans\n" in output
    assert "sentence spans\n" in output
    assert "alternative: value=Cat score=0.21" in output
    assert "[-------]\n" in output


def test_critical_path(capsys: pytest.CaptureFixture[str]) -> None:
    """The fold reports both the duration and its witness."""
    assert critical_path_main() == 0
    assert capsys.readouterr().out == (
        "Critical path length: 8.0\nCritical path: compile -> link -> package\n"
    )


def test_json_document(capsys: pytest.CaptureFixture[str]) -> None:
    """A standalone JSON graph round-trips and refuses a non-finite double."""
    assert json_document_main() == 0
    assert capsys.readouterr().out == (
        "Document round-trips: True\n"
        "Refused non-finite double: JSON value double inf is not finite\n"
    )
