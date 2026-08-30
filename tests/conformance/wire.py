"""Codec laws shared by primitive graph implementations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass

import pytest

from tiergraph import Graph
from tiergraph.schema import Refusal, RefusalStage

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
    read_back_corpus: Callable[[], tuple[Graph, ...]]
    refused_corpus: Callable[[], tuple[tuple[Graph, str], ...]]

    def check_round_trip(self) -> None:
        """Construction, serialization, and read-back preserve the whole graph."""
        graph = self.fixture()
        assert self.decode(self.encode(graph)) == graph

    def check_presentation_variants_have_equal_bytes(self) -> None:
        """Constructed presentation variants have one encoding and fingerprint.

        The variant factory, rather than ``Graph.__eq__``, defines the domain of
        this law.  Equality is one of the consequences of canonical construction
        and must not be allowed to filter a presentation-order counterexample.
        """
        left, right = self.canonical_variants()
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
        """Reading canonical bytes and writing them again changes no byte.

        The corpus, not one fixture, is the domain of this law.  A denominator
        of one cannot distinguish a writer that emits text its own reader
        refuses, which is how exactly that defect survived here.
        """
        for graph in self.read_back_corpus():
            encoded = self.encode(graph)
            assert self.encode(self.decode(encoded)) == encoded

    def check_writer_refuses_unreadable_text(self) -> None:
        """Every unencodable string is refused at its exact decoder path.

        The refusal must be this reader's own ``Refusal``, staged as an encoding
        condition.  ``UnicodeEncodeError`` is a ``ValueError`` subclass, so
        accepting any subclass would let an encoder that merely leaks its own
        exception satisfy this law without ever deciding to refuse.
        """
        for graph, expected_path in self.refused_corpus():
            with pytest.raises(ValueError) as refusal:
                self.encode(graph)
            assert type(refusal.value) is Refusal
            assert refusal.value.stage is RefusalStage.ENCODING
            assert expected_path in str(refusal.value)

    def check_refusal(self, offender: str, document: object) -> None:
        """Require malformed near-valid input to name its offending operation."""
        with pytest.raises(ValueError, match=offender):
            self.decode(json.dumps(document))
