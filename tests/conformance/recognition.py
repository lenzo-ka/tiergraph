"""Recognition laws shared by declared-fold implementations."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

import pytest

from tiergraph.fold import FoldDeclaration, FoldHomomorphism


@dataclass(frozen=True)
class FoldLawSuite:
    """Apply fold laws through replaceable declaration factories."""

    oracle: Callable[[], FoldDeclaration[object]]
    alternate_valuation: Callable[[], FoldDeclaration[object]]
    alternate_semiring: Callable[[], FoldDeclaration[object]]
    inexact_mismatch: Callable[[], object]
    homomorphism: Callable[[], FoldHomomorphism[object, object]]

    def check_oracle(self) -> None:
        """The page-sized independent answer is produced by the general fold."""
        result = self.oracle().run()
        assert result.provenance == (("start", "sting", "out"),)
        json.dumps(result.to_data(self.oracle().semiring), allow_nan=False)

    def check_independent_variation(self) -> None:
        """Changing either the field or the algebra changes only that declaration."""
        base = self.oracle()
        field = self.alternate_valuation()
        algebra = self.alternate_semiring()
        assert base.valuation != field.valuation
        assert base.semiring is field.semiring
        assert base.valuation == algebra.valuation
        assert base.semiring is not algebra.semiring
        assert base.run().value != field.run().value
        assert base.run().value != algebra.run().value

    def check_type_exactness_refusal(self) -> None:
        """An inexact field cannot enter an exact-associative declaration."""
        with pytest.raises(ValueError, match=r"xsd:double.*exact associativity"):
            self.inexact_mismatch()

    def check_homomorphism(self) -> None:
        """The declared carrier map commutes with independently executed folds."""
        homomorphism = self.homomorphism()
        assert homomorphism.commutes()
        homomorphism.check()

    def check_measured_cost(self) -> None:
        """The account reports counters observed while evaluating the DAG."""
        declaration = self.oracle()
        result = declaration.run()
        assert result.cost.document_size == len(declaration.graph.canonical_items())
        assert result.cost.relation_incidence == len(declaration.graph.relations)
        assert result.cost.index_product_size == len(declaration.coordinates())
        assert result.cost.carrier_additions > result.cost.index_product_size
        assert result.cost.carrier_multiplications == len(declaration.states())
        assert (
            result.cost.carrier_work
            == (result.cost.carrier_additions + result.cost.carrier_multiplications)
            * result.cost.carrier_operation_cost
        )
