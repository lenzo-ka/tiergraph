"""Interval joins over graph selections."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from typing import cast

from tiergraph.core import Graph, JsonValue, Refusal, RefusalStage
from tiergraph.predicate import (
    IntervalRelation,
    OffsetProfile,
    _decode_offset_profile,
    _interval_pairs,
    _offset_profile_to_data,
    _offset_spans,
    _validate_offset_profile,
)
from tiergraph.schema import _refuse_field_set
from tiergraph.selection import (
    Node,
    Selector,
    _decode_selector,
    evaluate_selection,
)
from tiergraph.wire import _object, _parsed_json, _string


class Extent(StrEnum):
    """State whether an output witness list was truncated."""

    EXHAUSTIVE = "exhaustive"
    CUT_AT_BOUND = "cut-at-bound"


@dataclass(frozen=True, slots=True)
class SpanPairs:
    """Carry interval-related node pairs in declared order and their extent."""

    pairs: tuple[tuple[Node, Node], ...]
    extent: Extent

    def to_data(self) -> dict[str, JsonValue]:
        """Return pairs and extent as strict JSON data."""
        return {
            "pairs": [[left.to_data(), right.to_data()] for left, right in self.pairs],
            "extent": self.extent.value,
        }


def span_pairs(
    graph: Graph,
    left: Selector,
    right: Selector,
    relation: IntervalRelation,
    offsets: OffsetProfile,
    *,
    limit: int | None = None,
) -> SpanPairs:
    """Return related item pairs in left-major declared order."""
    if limit is not None and (type(limit) is not int or limit < 0):
        raise ValueError("span_pairs limit must be a nonnegative integer or None")
    _validate_offset_profile(graph, offsets)
    left_nodes = evaluate_selection(graph, left)
    right_nodes = evaluate_selection(graph, right)
    left_spans = _offset_spans(graph, left_nodes.nodes, offsets)
    right_spans = _offset_spans(graph, right_nodes.nodes, offsets)
    pairs: list[tuple[Node, Node]] = []
    for left_span, right_span in _interval_pairs(left_spans, right_spans, relation):
        if limit is not None and len(pairs) == limit:
            return SpanPairs(tuple(pairs), Extent.CUT_AT_BOUND)
        pairs.append((cast(Node, left_span.node), cast(Node, right_span.node)))
    return SpanPairs(tuple(pairs), Extent.EXHAUSTIVE)


@dataclass(frozen=True, slots=True)
class _PairsRequest:
    left: Selector
    right: Selector
    relation: IntervalRelation
    offsets: OffsetProfile
    limit: int | None = None
    _left_data: JsonValue = field(default=None, repr=False, compare=False)
    _right_data: JsonValue = field(default=None, repr=False, compare=False)

    def to_data(self) -> dict[str, JsonValue]:
        """Return the pairs request as strict JSON data."""
        result: dict[str, JsonValue] = {
            "match": "pairs",
            "left": deepcopy(self._left_data),
            "right": deepcopy(self._right_data),
            "relation": self.relation.value,
            "offsets": _offset_profile_to_data(self.offsets),
        }
        if self.limit is not None:
            result["limit"] = self.limit
        return result


def _pairs_request_loads(source: str | bytes) -> _PairsRequest:
    value = cast(JsonValue, _parsed_json(source))
    node = cast(dict[str, JsonValue], _object(value, "$"))
    allowed = {"match", "left", "right", "relation", "offsets", "limit"}
    required = {"match", "left", "right", "relation", "offsets"}
    _refuse_field_set(node.keys(), allowed, required, "$")
    if _string(node["match"], "$.match") != "pairs":
        raise Refusal(RefusalStage.VALUE, "$.match must be 'pairs'")
    left = _decode_selector(node["left"], "$.left")
    right = _decode_selector(node["right"], "$.right")
    relation_text = _string(node["relation"], "$.relation")
    try:
        relation = IntervalRelation(relation_text)
    except ValueError as error:
        raise Refusal(
            RefusalStage.VALUE,
            f"$.relation has invalid interval relation {relation_text!r}",
        ) from error
    limit = node.get("limit")
    if limit is not None and (type(limit) is not int or limit < 0):
        raise Refusal(
            RefusalStage.VALUE, "$.limit must be a nonnegative integer or null"
        )
    return _PairsRequest(
        left,
        right,
        relation,
        _decode_offset_profile(node["offsets"], "$.offsets"),
        limit,
        deepcopy(node["left"]),
        deepcopy(node["right"]),
    )


__all__ = ["Extent", "SpanPairs", "span_pairs"]
