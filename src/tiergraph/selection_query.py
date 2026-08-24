"""Declarative selection queries over validated graph selectors."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import cast

from tiergraph.core import AttributeDomain, Graph, JsonValue, QualifiedName
from tiergraph.machine import _decode_qname
from tiergraph.path import (
    PathProfile,
    ResolvedItem,
    ResolvedPosition,
    StructuralPathProfile,
    resolve_path,
)
from tiergraph.selection import (
    AttributeSelector,
    BoundariesSelector,
    BoundarySelector,
    ItemSelector,
    ItemsSelector,
    NodeSet,
    TierSelector,
    TypeSelector,
)


@dataclass(frozen=True, slots=True)
class UnionQuery:
    """Union one or more selection queries."""

    args: tuple[SelectionQuery, ...]

    def __post_init__(self) -> None:
        """Require at least one operand."""
        if not self.args:
            raise ValueError("union query requires at least one argument")


@dataclass(frozen=True, slots=True)
class IntersectionQuery:
    """Intersect one or more selection queries."""

    args: tuple[SelectionQuery, ...]

    def __post_init__(self) -> None:
        """Require at least one operand."""
        if not self.args:
            raise ValueError("intersection query requires at least one argument")


@dataclass(frozen=True, slots=True)
class DifferenceQuery:
    """Remove the right selection from the left selection."""

    left: SelectionQuery
    right: SelectionQuery


@dataclass(frozen=True, slots=True)
class TierQuery:
    """Select one declared tier."""

    tier: QualifiedName


@dataclass(frozen=True, slots=True)
class TypeQuery:
    """Select items belonging to one declared type."""

    item_type: QualifiedName


@dataclass(frozen=True, slots=True)
class ItemsQuery:
    """Select every item on one declared tier."""

    tier: QualifiedName


@dataclass(frozen=True, slots=True)
class BoundariesQuery:
    """Select every boundary on one declared tier."""

    tier: QualifiedName


@dataclass(frozen=True, slots=True)
class ItemQuery:
    """Select the item resolved by one structural path."""

    path: str


@dataclass(frozen=True, slots=True)
class BoundaryQuery:
    """Select the boundary resolved by one structural path."""

    path: str


@dataclass(frozen=True, slots=True)
class AttributeQuery:
    """Select owners carrying one declared attribute on its domain."""

    attribute: QualifiedName
    domain: AttributeDomain


type SelectionQuery = (
    UnionQuery
    | IntersectionQuery
    | DifferenceQuery
    | TierQuery
    | TypeQuery
    | ItemsQuery
    | BoundariesQuery
    | ItemQuery
    | BoundaryQuery
    | AttributeQuery
)


class _DefaultStructuralPathProfile(StructuralPathProfile):
    def __repr__(self) -> str:
        return "StructuralPathProfile()"


_STRUCTURAL_PATH_PROFILE = _DefaultStructuralPathProfile()


def selection_query_loads(source: str | bytes) -> SelectionQuery:
    """Decode one strict declarative selection query from JSON."""
    return _decode_query(cast(JsonValue, json.loads(source)), "$")


def _decode_query(value: JsonValue, path: str) -> SelectionQuery:
    node = _object(value, path)
    discriminators = {"op", "select"} & node.keys()
    if len(discriminators) != 1:
        raise ValueError(f"{path} must contain exactly one of 'op' or 'select'")
    if "op" in discriminators:
        operation = _string(node["op"], f"{path}.op")
        if operation in ("union", "intersection"):
            _keys(node, {"op", "args"}, path)
            args_value = node["args"]
            if not isinstance(args_value, list) or not args_value:
                raise ValueError(f"{path}.args must be a non-empty list")
            args = tuple(
                _decode_query(child, f"{path}.args[{index}]")
                for index, child in enumerate(args_value)
            )
            return UnionQuery(args) if operation == "union" else IntersectionQuery(args)
        if operation == "difference":
            _keys(node, {"op", "left", "right"}, path)
            return DifferenceQuery(
                _decode_query(node["left"], f"{path}.left"),
                _decode_query(node["right"], f"{path}.right"),
            )
        raise ValueError(f"{path}.op has unknown operation {operation!r}")

    kind = _string(node["select"], f"{path}.select")
    if kind in ("tier", "items", "boundaries"):
        _keys(node, {"select", "tier"}, path)
        name = _qualified_name(node["tier"], f"{path}.tier")
        if kind == "tier":
            return TierQuery(name)
        if kind == "items":
            return ItemsQuery(name)
        return BoundariesQuery(name)
    if kind == "type":
        _keys(node, {"select", "type"}, path)
        return TypeQuery(_qualified_name(node["type"], f"{path}.type"))
    if kind in ("item", "boundary"):
        _keys(node, {"select", "path"}, path)
        text = _string(node["path"], f"{path}.path")
        return ItemQuery(text) if kind == "item" else BoundaryQuery(text)
    if kind == "attribute":
        _keys(node, {"select", "attribute", "domain"}, path)
        domain_text = _string(node["domain"], f"{path}.domain")
        try:
            domain = AttributeDomain(domain_text)
        except ValueError as error:
            raise ValueError(
                f"{path}.domain has invalid attribute domain {domain_text!r}"
            ) from error
        return AttributeQuery(
            _qualified_name(node["attribute"], f"{path}.attribute"), domain
        )
    raise ValueError(f"{path}.select has unknown selector {kind!r}")


def _object(value: JsonValue, path: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    return value


def _keys(value: dict[str, JsonValue], expected: set[str], path: str) -> None:
    if value.keys() != expected:
        raise ValueError(
            f"{path} must contain exactly {sorted(expected)!r}; found {sorted(value)!r}"
        )


def _string(value: JsonValue, path: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{path} must be a string")
    return value


def _qualified_name(value: JsonValue, path: str) -> QualifiedName:
    return _decode_qname(value, path)


def evaluate_selection(
    graph: Graph,
    query: SelectionQuery,
    *,
    path_profile: PathProfile = _STRUCTURAL_PATH_PROFILE,
) -> NodeSet:
    """Evaluate a declarative query into one canonically ordered node set."""
    if isinstance(query, UnionQuery | IntersectionQuery):
        result = evaluate_selection(graph, query.args[0], path_profile=path_profile)
        for child in query.args[1:]:
            selected = evaluate_selection(graph, child, path_profile=path_profile)
            result = (
                result | selected
                if isinstance(query, UnionQuery)
                else result & selected
            )
        return result
    if isinstance(query, DifferenceQuery):
        left = evaluate_selection(graph, query.left, path_profile=path_profile)
        right = evaluate_selection(graph, query.right, path_profile=path_profile)
        return left - right
    if isinstance(query, TierQuery):
        return TierSelector(graph, query.tier).evaluate()
    if isinstance(query, TypeQuery):
        return TypeSelector(graph, query.item_type).evaluate()
    if isinstance(query, ItemsQuery):
        return ItemsSelector(graph, query.tier).evaluate()
    if isinstance(query, BoundariesQuery):
        return BoundariesSelector(graph, query.tier).evaluate()
    if isinstance(query, ItemQuery | BoundaryQuery):
        resolved = resolve_path(graph, path_profile, query.path)
        if isinstance(query, ItemQuery):
            if not isinstance(resolved, ResolvedItem):
                raise ValueError(
                    f"item selection path {query.path!r} did not resolve to an item"
                )
            return ItemSelector(graph, resolved.current).evaluate()
        if not isinstance(resolved, ResolvedPosition):
            raise ValueError(
                f"boundary selection path {query.path!r} did not resolve to a boundary"
            )
        return BoundarySelector(graph, resolved.current).evaluate()
    return AttributeSelector(graph, query.attribute, query.domain).evaluate()


__all__ = [
    "AttributeQuery",
    "BoundariesQuery",
    "BoundaryQuery",
    "DifferenceQuery",
    "IntersectionQuery",
    "ItemQuery",
    "ItemsQuery",
    "SelectionQuery",
    "TierQuery",
    "TypeQuery",
    "UnionQuery",
    "evaluate_selection",
    "selection_query_loads",
]
