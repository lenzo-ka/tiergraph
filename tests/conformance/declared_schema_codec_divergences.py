"""The complete machine-readable acceptance-path divergence policy.

Entries describe constraints intentionally enforced by the codec but not by JSON
Schema, plus the structural validator's expected decision. Adding a declared
exception only changes this policy; the probe constructor and comparison harness
do not contain exception branches.
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


DECLARED_DIVERGENCES = (
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
        "position cardinality",
        r"\.(?:minimum:integer-2|maximum:integer-0)$",
        "relation-side bounds and instance cardinalities must be coherent",
    ),
    DeclaredDivergence(
        "name and identity uniqueness",
        r":duplicate-(?:name|tier|item|durable-id|position|relation)$",
        "declaration, tier, item, durable-id, position, and relation identities are unique",
    ),
    DeclaredDivergence(
        "one prefix per namespace URI",
        r":duplicate-namespace-uri$",
        "a canonical graph binds each namespace URI to one prefix",
    ),
    DeclaredDivergence(
        "relation graph promises",
        r":violate-(?:single-parent|acyclic|unique-sources|distinct-targets|targets-subset)$",
        "cross-instance relation promises require whole-graph validation",
    ),
    DeclaredDivergence(
        "nonempty positioned values",
        r"\.position_values\[\d+\]\.attributes:empty-array$",
        "empty boundaries are derived rather than serialized as positioned values",
    ),
)
