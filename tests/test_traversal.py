"""The reference implementation satisfies reusable traversal laws."""

from __future__ import annotations

import pytest

from tests.conformance.traversal import TraversalLawSuite
from tiergraph import Walk

LAWS = TraversalLawSuite(Walk)


@pytest.mark.parametrize(
    "law",
    [
        LAWS.check_diamond_and_inverse_sets,
        LAWS.check_cyclic_cap_is_visible,
        LAWS.check_acyclic_root_walk_terminates,
        LAWS.check_unbounded_refusal_names_relation,
        LAWS.check_anchored_boundaries_resolve,
        LAWS.check_construction_guards,
    ],
    ids=lambda law: law.__name__,
)
def test_traversal_law(law: object) -> None:
    """Run each reusable law against the reference walker."""
    assert callable(law)
    law()
