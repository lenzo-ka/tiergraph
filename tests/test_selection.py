"""The reference implementation satisfies reusable selection laws."""

from __future__ import annotations

import pytest

from tests.conformance.selection import SelectionLawSuite
from tiergraph import select

LAWS = SelectionLawSuite(select)


@pytest.mark.parametrize(
    "law",
    [
        LAWS.check_axes_and_canonical_order,
        LAWS.check_duplicate_routes_and_set_operations,
        LAWS.check_refusals_name_offenders,
        LAWS.check_boundaries_and_anchors,
        LAWS.check_json_data,
        LAWS.check_every_attribute_domain,
        LAWS.check_remaining_construction_guards,
        LAWS.check_boundary_relation_order,
    ],
    ids=lambda law: law.__name__,
)
def test_selection_law(law: object) -> None:
    """Run each reusable law against the reference selector."""
    assert callable(law)
    law()
