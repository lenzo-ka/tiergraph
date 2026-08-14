"""Codec laws shared by primitive graph implementations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass

import pytest

from tiergraph import Graph

Encoder = Callable[[Graph], bytes]
Decoder = Callable[[str | bytes], Graph]


@dataclass(frozen=True)
class WireLawSuite:
    """Apply wire laws through replaceable encoding and parsing boundaries."""

    encode: Encoder
    decode: Decoder
    fixture: Callable[[], Graph]
    canonical_variants: Callable[[], tuple[Graph, Graph]]
    ordered_variants: Callable[[], tuple[Graph, Graph, Graph]]

    def check_round_trip(self) -> None:
        """Construction, serialization, and read-back preserve the whole graph."""
        graph = self.fixture()
        assert self.decode(self.encode(graph)) == graph

    def check_equal_graphs_have_equal_bytes(self) -> None:
        """Presentation-reordered graphs are equal and have one fingerprint."""
        left, right = self.canonical_variants()
        assert left == right
        assert left is not right
        assert self.encode(left) == self.encode(right)
        assert (
            hashlib.sha256(self.encode(left)).digest()
            == hashlib.sha256(self.encode(right)).digest()
        )

    def check_ordered_graphs_have_different_bytes(self) -> None:
        """A graph order that carries meaning remains visible in its bytes."""
        baseline, tier_order, item_order = self.ordered_variants()
        assert baseline != tier_order
        assert baseline != item_order
        assert self.encode(baseline) != self.encode(tier_order)
        assert self.encode(baseline) != self.encode(item_order)

    def check_strict_json(self) -> None:
        """The byte encoding is UTF-8 JSON and admits no nonstandard constants."""
        document = self.encode(self.fixture()).decode("utf-8")

        def reject_constant(token: str) -> object:
            raise ValueError(f"nonstandard numeric token {token}")

        parsed = json.loads(document, parse_constant=reject_constant)
        json.dumps(parsed, allow_nan=False)

    def check_canonical_read_back(self) -> None:
        """Reading canonical bytes and writing them again changes no byte."""
        encoded = self.encode(self.fixture())
        assert self.encode(self.decode(encoded)) == encoded

    def check_refusal(self, offender: str, document: object) -> None:
        """Require malformed near-valid input to name its offending operation."""
        with pytest.raises(ValueError, match=offender):
            self.decode(json.dumps(document))
