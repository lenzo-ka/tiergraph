"""Live subtraction rules for declaration-derived acceptance probes.

Every entry must suppress a disagreement that the generated probe space actually
reaches.  Codec-only laws outside that space are documented at their declaration
sites, not carried here as live exceptions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DeclaredDivergence:
    """Describe one documented schema-accepted, codec-refused probe family."""

    name: str
    probe_pattern: str
    reason: str
    validation_accepts: bool = True

    def matches(self, probe_id: str) -> bool:
        """Return whether a deterministic probe identity belongs to this family."""
        return re.search(self.probe_pattern, probe_id) is not None


LIVE_DIVERGENCES = (
    DeclaredDivergence(
        "integral JSON spelling",
        r":wrong-type-float$",
        "JSON Schema integer includes zero-fraction numbers; the codec requires int",
        validation_accepts=False,
    ),
    DeclaredDivergence(
        "attribute declaration consistency",
        r"\.attribute_declarations\[\d+\]\.(?:domain|value_type):enum-",
        "attribute uses must have their declared domain and value type",
    ),
    DeclaredDivergence(
        "endpoint typing",
        r"\.(?:left_endpoint|right_endpoint|endpoint_kinds\[\d+\]):enum-",
        "relation endpoints must have kinds allowed by their declaration",
    ),
    DeclaredDivergence(
        "referential integrity",
        r"\.index:integer-2$",
        "structural indices must address an item in the referenced tier",
    ),
    DeclaredDivergence(
        "boundary cardinality",
        r"\.(?:minimum:integer-2|maximum:integer-0)$",
        "relation-side bounds and instance cardinalities must be coherent",
    ),
)
