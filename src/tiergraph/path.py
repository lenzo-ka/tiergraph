"""Canonical, profile-owned paths over tiergraph item and boundary references."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from tiergraph.core import (
    BoundaryRef,
    BoundarySide,
    DurableBoundaryRef,
    DurableItemRef,
    Graph,
    ItemRef,
    QualifiedName,
)

_INDEX = re.compile(r"(?:0|[1-9][0-9]*)\Z")


class PathKind(StrEnum):
    """Classify the graph reference produced by a path profile."""

    ITEM = "item"
    BOUNDARY = "boundary"
    ALTERNATIVE = "alternative"


class PathRefusalCode(StrEnum):
    """Identify stable classes of path refusal independently of diagnostics.

    ``BOUNDARY_NOT_IN_PARENT`` is reserved and is not produced by a current path
    resolver or profile.
    """

    MALFORMED_POINTER = "malformed_pointer"
    NONCANONICAL_SEGMENT = "noncanonical_segment"
    UNKNOWN_FORM = "unknown_form"
    INVALID_SEGMENT = "invalid_segment"
    WRONG_KIND = "wrong_kind"
    UNKNOWN_TIER = "unknown_tier"
    OUT_OF_RANGE = "out_of_range"
    UNKNOWN_DURABLE_ITEM = "unknown_durable_item"
    UNKNOWN_DURABLE_ANCHOR = "unknown_durable_anchor"
    BOUNDARY_NOT_IN_PARENT = "boundary_not_in_parent"
    UNSPELLABLE = "unspellable"
    PROFILE_REFUSED = "profile_refused"
    ALTERNATIVE_OUT_OF_RANGE = "alternative_out_of_range"


@dataclass(frozen=True, slots=True)
class CanonicalPath:
    """Hold decoded segments of a strict, non-fragment RFC 6901 pointer."""

    segments: tuple[str, ...]

    @classmethod
    def parse(cls, text: str) -> CanonicalPath:
        """Parse a strict JSON Pointer, accepting empty and refusing malformed spellings."""
        if text == "":
            return cls(())
        if not text.startswith("/"):
            raise PathRefusal(
                PathRefusalCode.MALFORMED_POINTER, PathOffender(text=text)
            )
        decoded: list[str] = []
        for index, segment in enumerate(text[1:].split("/")):
            value: list[str] = []
            cursor = 0
            while cursor < len(segment):
                character = segment[cursor]
                if character != "~":
                    value.append(character)
                    cursor += 1
                    continue
                if cursor + 1 == len(segment) or segment[cursor + 1] not in "01":
                    raise PathRefusal(
                        PathRefusalCode.MALFORMED_POINTER,
                        PathOffender(text=text, segment_index=index, segment=segment),
                    )
                value.append("~" if segment[cursor + 1] == "0" else "/")
                cursor += 2
            decoded.append("".join(value))
        return cls(tuple(decoded))

    def __str__(self) -> str:
        """Spell the decoded segments with canonical RFC 6901 escaping."""
        return "".join(
            f"/{segment.replace('~', '~0').replace('/', '~1')}"
            for segment in self.segments
        )


@dataclass(frozen=True, slots=True)
class ItemBinding:
    """Request resolution of one structural or durable item reference."""

    reference: ItemRef | DurableItemRef


@dataclass(frozen=True, slots=True)
class BoundaryBinding:
    """Request resolution of one structural or durable boundary reference."""

    reference: BoundaryRef | DurableBoundaryRef


@dataclass(frozen=True, slots=True)
class AlternativeRef:
    """Select one profile-ordered alternative of an owning graph item."""

    owner: ItemRef | DurableItemRef
    relation: QualifiedName
    index: int


type PathBinding = ItemBinding | BoundaryBinding | AlternativeRef


class PathProfile(Protocol):
    """Interpret and spell canonical paths for one explicit vocabulary."""

    def bind(self, path: CanonicalPath, graph: Graph) -> PathBinding:
        """Convert a canonical path to a graph resolution request."""
        ...

    def spell(self, binding: PathBinding, graph: Graph) -> CanonicalPath:
        """Project a supported graph resolution request back to a path."""
        ...

    def alternatives(
        self, owner: ItemRef, relation: QualifiedName, graph: Graph
    ) -> tuple[object, ...]:
        """Return alternatives in the profile's stable, snapshot-local order."""
        ...


@dataclass(frozen=True, slots=True)
class PathOffender:
    """Carry stable structured context for a refused path operation."""

    text: str
    path: CanonicalPath | None = None
    segment_index: int | None = None
    segment: str | None = None
    expected_kind: PathKind | None = None
    actual_kind: PathKind | None = None
    tier: QualifiedName | None = None
    index: int | None = None
    durable_id: str | None = None
    profile_reason: str | None = None
    relation: QualifiedName | None = None
    available_count: int | None = None


class PathRefusal(Exception):
    """Report a typed path failure with offender data and its original cause."""

    def __init__(
        self,
        code: PathRefusalCode,
        offender: PathOffender,
        cause: Exception | None = None,
    ) -> None:
        """Retain stable refusal data while leaving wording diagnostic-only."""
        self.code = code
        self.offender = offender
        self.cause = cause
        super().__init__(f"{code.value}: {offender!r}")


@dataclass(frozen=True, slots=True)
class ResolvedItem:
    """Pair the parsed path with its current structural item coordinate."""

    path: CanonicalPath
    current: ItemRef


@dataclass(frozen=True, slots=True)
class ResolvedBoundary:
    """Pair the parsed path with its current structural boundary coordinate."""

    path: CanonicalPath
    current: BoundaryRef


@dataclass(frozen=True, slots=True)
class ResolvedAlternative:
    """Pair a path with one selection from a profile-ordered alternative set."""

    path: CanonicalPath
    owner: ItemRef
    relation: QualifiedName
    index: int
    value: object


def resolve_path(
    graph: Graph,
    profile: PathProfile,
    text: str,
    *,
    require: PathKind | None = None,
) -> ResolvedItem | ResolvedBoundary | ResolvedAlternative:
    """Parse, bind, kind-check, and resolve a profile-owned graph path."""
    path = CanonicalPath.parse(text)
    binding = profile.bind(path, graph)
    if isinstance(binding, ItemBinding):
        actual = PathKind.ITEM
    elif isinstance(binding, BoundaryBinding):
        actual = PathKind.BOUNDARY
    else:
        actual = PathKind.ALTERNATIVE
    if require is not None and require is not actual:
        raise PathRefusal(
            PathRefusalCode.WRONG_KIND,
            PathOffender(
                text=text,
                path=path,
                expected_kind=require,
                actual_kind=actual,
            ),
        )
    if isinstance(binding, ItemBinding):
        try:
            current_item = graph.resolve_item(binding.reference)
        except (TypeError, ValueError) as error:
            raise _item_resolution_refusal(text, path, binding, graph, error) from error
        return ResolvedItem(path, current_item)
    if isinstance(binding, AlternativeRef):
        try:
            owner = graph.resolve_item(binding.owner)
        except (TypeError, ValueError) as error:
            item_binding = ItemBinding(binding.owner)
            raise _item_resolution_refusal(
                text, path, item_binding, graph, error
            ) from error
        alternatives = profile.alternatives(owner, binding.relation, graph)
        if binding.index < 0 or binding.index >= len(alternatives):
            raise PathRefusal(
                PathRefusalCode.ALTERNATIVE_OUT_OF_RANGE,
                PathOffender(
                    text=text,
                    path=path,
                    tier=owner.tier,
                    index=binding.index,
                    relation=binding.relation,
                    available_count=len(alternatives),
                ),
            )
        return ResolvedAlternative(
            path,
            owner,
            binding.relation,
            binding.index,
            alternatives[binding.index],
        )
    try:
        current_boundary = graph.resolve_boundary(binding.reference)
    except (TypeError, ValueError) as error:
        raise _boundary_resolution_refusal(text, path, binding, graph, error) from error
    return ResolvedBoundary(path, current_boundary)


class StructuralPathProfile:
    """Address items and boundaries with a domain-neutral explicit vocabulary.

    Structural forms are ``/items/structural/NS/LOCAL/INDEX`` and
    ``/positions/structural/NS/LOCAL/INDEX``. Durable forms are
    ``/items/durable/ID`` and ``/positions/durable/item/ID/SIDE`` or
    ``/positions/durable/tier/NS/LOCAL/SIDE``.
    """

    def bind(self, path: CanonicalPath, graph: Graph) -> PathBinding:
        """Interpret one of the generic structural or durable forms."""
        del graph
        segments = path.segments
        structural_item_segment_count = 5
        durable_item_segment_count = 3
        durable_tier_position_segment_count = 6
        if len(segments) == structural_item_segment_count and segments[:2] == (
            "items",
            "structural",
        ):
            return ItemBinding(
                ItemRef(
                    _tier(segments[2], segments[3], path),
                    _index(segments[4], 4, path),
                )
            )
        if len(segments) == durable_item_segment_count and segments[:2] == (
            "items",
            "durable",
        ):
            return ItemBinding(DurableItemRef(_nonempty(segments[2], 2, path)))
        if len(segments) == structural_item_segment_count and segments[:2] == (
            "positions",
            "structural",
        ):
            return BoundaryBinding(
                BoundaryRef(
                    _tier(segments[2], segments[3], path),
                    _index(segments[4], 4, path),
                )
            )
        if len(segments) == structural_item_segment_count and segments[:3] == (
            "positions",
            "durable",
            "item",
        ):
            return BoundaryBinding(
                DurableBoundaryRef(
                    DurableItemRef(_nonempty(segments[3], 3, path)),
                    _side(segments[4], 4, path),
                )
            )
        if len(segments) == durable_tier_position_segment_count and segments[:3] == (
            "positions",
            "durable",
            "tier",
        ):
            return BoundaryBinding(
                DurableBoundaryRef(
                    _tier(segments[3], segments[4], path, 3, 4),
                    _side(segments[5], 5, path),
                )
            )
        raise PathRefusal(
            PathRefusalCode.UNKNOWN_FORM, PathOffender(text=str(path), path=path)
        )

    def spell(self, binding: PathBinding, graph: Graph) -> CanonicalPath:
        """Spell each reference shape supported by the generic vocabulary."""
        del graph
        if isinstance(binding, AlternativeRef):
            raise PathRefusal(
                PathRefusalCode.UNSPELLABLE,
                PathOffender(text="", profile_reason="unsupported_reference"),
            )
        reference = binding.reference
        if isinstance(reference, ItemRef):
            segments = _structural_segments("items", reference.tier, reference.index)
        elif isinstance(reference, DurableItemRef):
            segments = ("items", "durable", reference.durable_id)
        elif isinstance(reference, BoundaryRef):
            segments = _structural_segments(
                "positions", reference.tier, reference.index
            )
        elif isinstance(reference, DurableBoundaryRef):
            if isinstance(reference.anchor, DurableItemRef):
                segments = (
                    "positions",
                    "durable",
                    "item",
                    reference.anchor.durable_id,
                    reference.side.value,
                )
            else:
                segments = (
                    "positions",
                    "durable",
                    "tier",
                    reference.anchor.namespace,
                    reference.anchor.local_name,
                    reference.side.value,
                )
        else:
            raise PathRefusal(
                PathRefusalCode.UNSPELLABLE,
                PathOffender(text="", profile_reason="unsupported_reference"),
            )
        return CanonicalPath(segments)

    def alternatives(
        self, owner: ItemRef, relation: QualifiedName, graph: Graph
    ) -> tuple[object, ...]:
        """Return no alternatives because this vocabulary declares none."""
        del owner, relation, graph
        return ()


def _tier(
    namespace: str,
    local: str,
    path: CanonicalPath,
    namespace_index: int = 2,
    local_index: int = 3,
) -> QualifiedName:
    return QualifiedName(
        _nonempty(namespace, namespace_index, path),
        _nonempty(local, local_index, path),
    )


def _nonempty(value: str, index: int, path: CanonicalPath) -> str:
    if not value:
        raise PathRefusal(
            PathRefusalCode.INVALID_SEGMENT,
            PathOffender(text=str(path), path=path, segment_index=index, segment=value),
        )
    return value


def _index(value: str, index: int, path: CanonicalPath) -> int:
    if _INDEX.fullmatch(value):
        return int(value)
    # A non-negative integer written non-canonically (leading zero, leading "+",
    # or non-ASCII decimal digits) is a NONCANONICAL alias; anything outside the
    # unsigned-integer lexical domain (a sign-only "-1", "one") is INVALID.
    body = value[1:] if value.startswith("+") else value
    code = (
        PathRefusalCode.NONCANONICAL_SEGMENT
        if body != "" and body.isdecimal()
        else PathRefusalCode.INVALID_SEGMENT
    )
    raise PathRefusal(
        code,
        PathOffender(text=str(path), path=path, segment_index=index, segment=value),
    )


def _side(value: str, index: int, path: CanonicalPath) -> BoundarySide:
    try:
        return BoundarySide(value)
    except ValueError as error:
        raise PathRefusal(
            PathRefusalCode.INVALID_SEGMENT,
            PathOffender(text=str(path), path=path, segment_index=index, segment=value),
            error,
        ) from error


def _structural_segments(kind: str, tier: QualifiedName, index: int) -> tuple[str, ...]:
    return kind, "structural", tier.namespace, tier.local_name, str(index)


def _tier_exists(graph: Graph, tier: QualifiedName) -> bool:
    return any(candidate.declaration.name == tier for candidate in graph.tiers)


def _item_resolution_refusal(
    text: str,
    path: CanonicalPath,
    binding: ItemBinding,
    graph: Graph,
    cause: TypeError | ValueError,
) -> PathRefusal:
    reference = binding.reference
    if isinstance(cause, TypeError):
        return PathRefusal(
            PathRefusalCode.PROFILE_REFUSED,
            PathOffender(text=text, path=path, profile_reason="invalid_item_reference"),
            cause,
        )
    if isinstance(reference, DurableItemRef):
        code = PathRefusalCode.UNKNOWN_DURABLE_ITEM
        offender = PathOffender(text=text, path=path, durable_id=reference.durable_id)
    elif not _tier_exists(graph, reference.tier):
        code = PathRefusalCode.UNKNOWN_TIER
        offender = PathOffender(text=text, path=path, tier=reference.tier)
    else:
        code = PathRefusalCode.OUT_OF_RANGE
        offender = PathOffender(
            text=text, path=path, tier=reference.tier, index=reference.index
        )
    return PathRefusal(code, offender, cause)


def _boundary_resolution_refusal(
    text: str,
    path: CanonicalPath,
    binding: BoundaryBinding,
    graph: Graph,
    cause: TypeError | ValueError,
) -> PathRefusal:
    reference = binding.reference
    if isinstance(cause, TypeError):
        return PathRefusal(
            PathRefusalCode.PROFILE_REFUSED,
            PathOffender(
                text=text, path=path, profile_reason="invalid_boundary_reference"
            ),
            cause,
        )
    if isinstance(reference, DurableBoundaryRef):
        if isinstance(reference.anchor, DurableItemRef):
            return PathRefusal(
                PathRefusalCode.UNKNOWN_DURABLE_ANCHOR,
                PathOffender(
                    text=text,
                    path=path,
                    durable_id=reference.anchor.durable_id,
                ),
                cause,
            )
        return PathRefusal(
            PathRefusalCode.UNKNOWN_TIER,
            PathOffender(text=text, path=path, tier=reference.anchor),
            cause,
        )
    code = (
        PathRefusalCode.UNKNOWN_TIER
        if not _tier_exists(graph, reference.tier)
        else PathRefusalCode.OUT_OF_RANGE
    )
    return PathRefusal(
        code,
        PathOffender(text=text, path=path, tier=reference.tier, index=reference.index),
        cause,
    )
