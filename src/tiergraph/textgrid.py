"""Read and write Praat TextGrid documents with exact decimal coordinates."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import NamedTuple

from tiergraph.clock import ClockProfile
from tiergraph.core import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    BoundaryRef,
    BoundarySide,
    DurableBoundaryRef,
    DurableItemRef,
    Graph,
    Item,
    ItemRef,
    NamespaceDeclaration,
    QualifiedName,
    RelationDeclaration,
    RelationEndpointKind,
    RelationInstance,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    XsdType,
)
from tiergraph.spanview import SpanViewProfile, span_view

__all__ = ["TextGridReadResult", "from_textgrid", "to_textgrid"]

_NAMESPACE = "urn:tiergraph:textgrid"
_BASE = QualifiedName(_NAMESPACE, "base")
_BASE_TYPE = QualifiedName(_NAMESPACE, "base-item")
_SPAN_TYPE = QualifiedName(_NAMESPACE, "interval")
_POINT_TYPE = QualifiedName(_NAMESPACE, "point")
_CLOCK = QualifiedName(_NAMESPACE, "clock")
_CLOCK_TYPE = QualifiedName(_NAMESPACE, "clock-item")
_COVERAGE = QualifiedName(_NAMESPACE, "coverage")
_POINT_COVERAGE = QualifiedName(_NAMESPACE, "point-coverage")
_CLOCK_BINDING = QualifiedName(_NAMESPACE, "clock-binding")
_VALUE = QualifiedName(_NAMESPACE, "value")
_SCORE = QualifiedName(_NAMESPACE, "score")
_UNIT = QualifiedName(_NAMESPACE, "unit")
_START = QualifiedName(_NAMESPACE, "start")
_DURATION = QualifiedName(_NAMESPACE, "duration")
_UNTIMED = QualifiedName(_NAMESPACE, "untimed")
_LONG_HEADER = 'File type = "ooTextFile"'
_SHORT_HEADER = 'File type = "ooTextFile short"'
_OBJECT_HEADER = 'Object class = "TextGrid"'
_SHORT_OBJECT_HEADER = '"TextGrid"'
_LONG_VALUE = re.compile(r"^\s*[^=]+?=\s*(.*?)\s*$")
_INTEGER = re.compile(r"[0-9]+\Z")
_HEADER_LINE_COUNT = 2
_CONTAINMENT_RULES = ("enclosure", "endpoint_coincidence")


@dataclass(frozen=True, slots=True)
class _Interval:
    start: Decimal
    end: Decimal
    value: str


@dataclass(frozen=True, slots=True)
class _Point:
    at: Decimal
    value: str


@dataclass(frozen=True, slots=True)
class _Tier:
    kind: str
    name: str
    xmin: Decimal
    xmax: Decimal
    entries: tuple[_Interval | _Point, ...]


def _containment_rule(value: str) -> str:
    if value not in _CONTAINMENT_RULES:
        raise ValueError(
            f"TextGrid containment_rule {value!r} must be "
            "'enclosure' or 'endpoint_coincidence'"
        )
    return value


def _contains(parent: _Interval, child: _Interval, rule: str) -> bool:
    enclosed = parent.start <= child.start and child.end <= parent.end
    if rule == "enclosure":
        return enclosed
    parent_boundaries = {parent.start, parent.end}
    return (
        enclosed and child.start in parent_boundaries and child.end in parent_boundaries
    )


class TextGridReadResult(NamedTuple):
    """Return the decoded graph beside the profile that selects its TextGrid tiers."""

    graph: Graph
    profile: SpanViewProfile

    @property
    def clock(self) -> ClockProfile:
        """Construct the physical clock declared by the decoded graph."""
        return ClockProfile.from_data(
            self.graph,
            {
                "clock_tier": _CLOCK.to_data(),
                "binding_relation": _CLOCK_BINDING.to_data(),
                "rate_attribute": None,
                "unit_attribute": _UNIT.to_data(),
                "tick_attribute": None,
                "gap_attribute": None,
                "untimed_attribute": _UNTIMED.to_data(),
                "start_attribute": _START.to_data(),
                "duration_attribute": _DURATION.to_data(),
            },
        )


def _text(document: str | bytes) -> str:
    if isinstance(document, str):
        return document.lstrip("\ufeff")
    if not isinstance(document, bytes):
        raise TypeError(
            f"TextGrid document must be str or bytes, got {type(document).__name__}"
        )
    if document.startswith((b"\xff\xfe", b"\xfe\xff")):
        return document.decode("utf-16")
    return document.decode("utf-8-sig")


def _quoted(value: str, subject: str) -> str:
    if (
        len(value) < _HEADER_LINE_COUNT
        or not value.startswith('"')
        or not value.endswith('"')
    ):
        raise ValueError(f"{subject} {value!r} must be a double-quoted string")
    inner = value[1:-1]
    index = 0
    decoded: list[str] = []
    while index < len(inner):
        character = inner[index]
        if character != '"':
            decoded.append(character)
            index += 1
        elif index + 1 < len(inner) and inner[index + 1] == '"':
            decoded.append('"')
            index += 2
        else:
            raise ValueError(f"{subject} contains an undoubled quote")
    return "".join(decoded)


def _decimal(value: str, subject: str) -> Decimal:
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"{subject} {value!r} is not a decimal number") from error
    if not number.is_finite():
        raise ValueError(f"{subject} {value!r} is not finite")
    return number


def _count(value: str, subject: str) -> int:
    if _INTEGER.fullmatch(value) is None:
        raise ValueError(f"{subject} {value!r} is not a nonnegative integer")
    return int(value)


class _Values:
    def __init__(self, values: list[str]) -> None:
        self.values = values
        self.index = 0

    def take(self, subject: str) -> str:
        """Consume the next grammar value or name the missing subject."""
        if self.index == len(self.values):
            raise ValueError(f"TextGrid ends before {subject}")
        value = self.values[self.index]
        self.index += 1
        return value


def _parse(  # noqa: PLR0915 -- the two ordered TextGrid grammars share one cursor
    document: str | bytes,
) -> tuple[Decimal, Decimal, tuple[_Tier, ...]]:
    lines = _text(document).splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    if len(lines) < _HEADER_LINE_COUNT:
        raise ValueError("TextGrid document lacks its two-line header")
    header, object_header = lines[0].strip(), lines[1].strip()
    if (header, object_header) == (_LONG_HEADER, _OBJECT_HEADER):
        values = []
        for line in lines[2:]:
            stripped = line.strip()
            if stripped.startswith("tiers?"):
                values.append(stripped.removeprefix("tiers?").strip())
            elif (match := _LONG_VALUE.match(line)) is not None:
                values.append(match.group(1))
    elif (header, object_header) == (_SHORT_HEADER, _SHORT_OBJECT_HEADER):
        values = [line.strip() for line in lines[2:] if line.strip()]
    else:
        raise ValueError(
            f"TextGrid header {(header, object_header)!r} names an unsupported form"
        )
    stream = _Values(values)
    xmin = _decimal(stream.take("xmin"), "TextGrid xmin")
    xmax = _decimal(stream.take("xmax"), "TextGrid xmax")
    if xmin > xmax:
        raise ValueError(f"TextGrid range {xmin}..{xmax} goes backward")
    presence = stream.take("tiers presence")
    if presence == "<absent>":
        if stream.index != len(values):
            raise ValueError("TextGrid with absent tiers carries trailing values")
        return xmin, xmax, ()
    if presence != "<exists>":
        raise ValueError(
            f"TextGrid tiers presence {presence!r} must be <exists> or <absent>"
        )
    tier_count = _count(stream.take("tier count"), "TextGrid tier count")
    tiers: list[_Tier] = []
    for tier_index in range(tier_count):
        kind = _quoted(stream.take("tier class"), f"tier {tier_index} class")
        if kind not in {"IntervalTier", "TextTier"}:
            raise ValueError(f"tier {tier_index} class {kind!r} is unsupported")
        name = _quoted(stream.take("tier name"), f"tier {tier_index} name")
        tier_xmin = _decimal(stream.take("tier xmin"), f"tier {name!r} xmin")
        tier_xmax = _decimal(stream.take("tier xmax"), f"tier {name!r} xmax")
        if (tier_xmin, tier_xmax) != (xmin, xmax):
            raise ValueError(
                f"tier {name!r} range {tier_xmin}..{tier_xmax} disagrees with "
                f"TextGrid range {xmin}..{xmax}"
            )
        entry_count = _count(
            stream.take("tier entry count"), f"tier {name!r} entry count"
        )
        entries: list[_Interval | _Point] = []
        for entry_index in range(entry_count):
            if kind == "IntervalTier":
                start = _decimal(
                    stream.take("interval xmin"),
                    f"tier {name!r} interval {entry_index} xmin",
                )
                end = _decimal(
                    stream.take("interval xmax"),
                    f"tier {name!r} interval {entry_index} xmax",
                )
                value = _quoted(
                    stream.take("interval text"),
                    f"tier {name!r} interval {entry_index} text",
                )
                if not xmin <= start < end <= xmax:
                    raise ValueError(
                        f"tier {name!r} interval {entry_index} range {start}..{end} "
                        f"must be positive and within {xmin}..{xmax}"
                    )
                entries.append(_Interval(start, end, value))
            else:
                at = _decimal(
                    stream.take("point number"),
                    f"tier {name!r} point {entry_index} number",
                )
                value = _quoted(
                    stream.take("point mark"),
                    f"tier {name!r} point {entry_index} mark",
                )
                if not xmin <= at <= xmax:
                    raise ValueError(
                        f"tier {name!r} point {entry_index} at {at} lies outside "
                        f"{xmin}..{xmax}"
                    )
                entries.append(_Point(at, value))
        if kind == "IntervalTier":
            cursor = xmin
            for entry_index, entry in enumerate(entries):
                assert isinstance(entry, _Interval)
                if entry.start != cursor:
                    raise ValueError(
                        f"tier {name!r} interval {entry_index} starts at "
                        f"{entry.start}, not previous end {cursor}"
                    )
                cursor = entry.end
            if cursor != xmax:
                raise ValueError(
                    f"tier {name!r} intervals end at {cursor}, not tier xmax {xmax}"
                )
        tiers.append(_Tier(kind, name, tier_xmin, tier_xmax, tuple(entries)))
    if stream.index != len(values):
        raise ValueError("TextGrid carries trailing values after its declared tiers")
    return xmin, xmax, tuple(tiers)


def _name(local_name: str) -> QualifiedName:
    return QualifiedName(_NAMESPACE, local_name)


def _boundary(index: int, size: int) -> DurableBoundaryRef:
    if index == 0:
        return DurableBoundaryRef(_BASE, BoundarySide.BEFORE)
    if index == size:
        return DurableBoundaryRef(_BASE, BoundarySide.AFTER)
    return DurableBoundaryRef(
        DurableItemRef(f"textgrid-base-{index}"), BoundarySide.BEFORE
    )


def _clock_boundary(index: int, size: int) -> DurableBoundaryRef:
    if index == 0:
        return DurableBoundaryRef(_CLOCK, BoundarySide.BEFORE)
    if index == size:
        return DurableBoundaryRef(_CLOCK, BoundarySide.AFTER)
    return DurableBoundaryRef(
        DurableItemRef(f"textgrid-clock-{index}"), BoundarySide.BEFORE
    )


def from_textgrid(
    document: str | bytes,
    *,
    unit: str = "s",
    containment_rule: str = "enclosure",
) -> TextGridReadResult:
    """Decode a TextGrid using enclosure or endpoint-coincidence containment."""
    if not isinstance(unit, str) or not unit:
        raise ValueError(f"TextGrid unit {unit!r} must be a non-empty string")
    containment_rule = _containment_rule(containment_rule)
    xmin, xmax, parsed_tiers = _parse(document)
    duplicate = next(
        (
            tier.name
            for tier in parsed_tiers
            if sum(t.name == tier.name for t in parsed_tiers) > 1
        ),
        None,
    )
    if duplicate is not None:
        raise ValueError(f"TextGrid tier name {duplicate!r} occurs more than once")
    boundaries = {xmin, xmax}
    for tier in parsed_tiers:
        for entry in tier.entries:
            if isinstance(entry, _Interval):
                boundaries.update((entry.start, entry.end))
            else:
                boundaries.add(entry.at)
    ordered = tuple(sorted(boundaries))
    boundary_index = {value: index for index, value in enumerate(ordered)}
    base_items = tuple(
        Item(
            f"textgrid-base-{index}",
            (
                AttributeValue(_START, XsdType.DECIMAL, format(ordered[index], "f")),
                AttributeValue(
                    _DURATION,
                    XsdType.DECIMAL,
                    format(ordered[index + 1] - ordered[index], "f"),
                ),
            ),
        )
        for index in range(len(ordered) - 1)
    )
    clock_items = tuple(
        Item(f"textgrid-clock-{index}") for index in range(len(base_items))
    )
    tiers: list[Tier] = [
        Tier(TierDeclaration(_BASE, "TextGrid base"), base_items),
        Tier(TierDeclaration(_CLOCK, "TextGrid clock"), clock_items),
    ]
    declarations: list[RelationDeclaration] = [
        SimpleRelationDeclaration(_name("base-members"), _BASE, _BASE_TYPE),
        SimpleRelationDeclaration(_name("clock-members"), _CLOCK, _CLOCK_TYPE),
        BipartiteRelationDeclaration(
            _CLOCK_BINDING,
            _BASE_TYPE,
            _CLOCK_TYPE,
            left_endpoint=RelationEndpointKind.BOUNDARY,
            right_endpoint=RelationEndpointKind.BOUNDARY,
        ),
    ]
    relations: list[RelationInstance] = [
        RelationInstance(
            _CLOCK_BINDING,
            _boundary(index, len(base_items)),
            _clock_boundary(index, len(base_items)),
        )
        for index in range(len(base_items) + 1)
    ]
    span_tiers: list[QualifiedName] = []
    point_tiers: list[QualifiedName] = []
    for parsed in parsed_tiers:
        tier_name = _name(parsed.name)
        point = parsed.kind == "TextTier"
        item_type = _POINT_TYPE if point else _SPAN_TYPE
        membership_name = _name(f"{parsed.name}-members")
        items = tuple(
            Item(
                f"textgrid-{len(tiers)}-{index}",
                (AttributeValue(_VALUE, XsdType.STRING, entry.value),),
            )
            for index, entry in enumerate(parsed.entries)
        )
        tiers.append(
            Tier(
                TierDeclaration(tier_name, parsed.name),
                items,
                (AttributeValue(_UNTIMED, XsdType.BOOLEAN, "true"),),
            )
        )
        declarations.append(
            SimpleRelationDeclaration(membership_name, tier_name, item_type)
        )
        (point_tiers if point else span_tiers).append(tier_name)
        for item_index, entry in enumerate(parsed.entries):
            right = ItemRef(tier_name, item_index)
            if isinstance(entry, _Point):
                at = boundary_index[entry.at]
                relations.append(
                    RelationInstance(
                        _POINT_COVERAGE,
                        _boundary(at, len(base_items)),
                        right,
                    )
                )
            else:
                start = boundary_index[entry.start]
                end = boundary_index[entry.end]
                relations.extend(
                    RelationInstance(_COVERAGE, ItemRef(_BASE, index), right)
                    for index in range(start, end)
                )
    declarations.extend(
        (
            BipartiteRelationDeclaration(_COVERAGE, _BASE_TYPE, _SPAN_TYPE),
            BipartiteRelationDeclaration(
                _POINT_COVERAGE,
                _BASE_TYPE,
                _POINT_TYPE,
                left_endpoint=RelationEndpointKind.BOUNDARY,
            ),
        )
    )
    interval_tiers = [
        (index, tier)
        for index, tier in enumerate(parsed_tiers)
        if tier.kind == "IntervalTier"
    ]
    for parent_position, (parent_index, parent) in enumerate(interval_tiers):
        for child_index, child in interval_tiers[parent_position + 1 :]:
            containment = _name(f"containment-{parent_index}-{child_index}")
            declarations.append(
                BipartiteRelationDeclaration(
                    containment,
                    _SPAN_TYPE,
                    _SPAN_TYPE,
                    single_parent=True,
                    acyclic=True,
                )
            )
            parent_name = _name(parent.name)
            child_name = _name(child.name)
            for parent_item, parent_entry in enumerate(parent.entries):
                assert isinstance(parent_entry, _Interval)
                for child_item, child_entry in enumerate(child.entries):
                    assert isinstance(child_entry, _Interval)
                    if _contains(parent_entry, child_entry, containment_rule):
                        relations.append(
                            RelationInstance(
                                containment,
                                ItemRef(parent_name, parent_item),
                                ItemRef(child_name, child_item),
                            )
                        )
    graph = Graph(
        (NamespaceDeclaration("textgrid", _NAMESPACE),),
        tuple(tiers),
        tuple(declarations),
        tuple(relations),
        (
            AttributeDeclaration(_VALUE, AttributeDomain.ITEM, XsdType.STRING),
            AttributeDeclaration(_SCORE, AttributeDomain.ITEM, XsdType.DECIMAL),
            AttributeDeclaration(_UNIT, AttributeDomain.DOCUMENT, XsdType.STRING),
            AttributeDeclaration(_START, AttributeDomain.ITEM, XsdType.DECIMAL),
            AttributeDeclaration(_DURATION, AttributeDomain.ITEM, XsdType.DECIMAL),
            AttributeDeclaration(_UNTIMED, AttributeDomain.TIER, XsdType.BOOLEAN),
        ),
        attributes=(AttributeValue(_UNIT, XsdType.STRING, unit),),
    )
    profile = SpanViewProfile(
        _BASE,
        tuple(span_tiers),
        _COVERAGE,
        _SCORE,
        _VALUE,
        point_tiers=tuple(point_tiers),
        point_coverage_relation=_POINT_COVERAGE if point_tiers else None,
    )
    return TextGridReadResult(graph, profile)


def _number(value: Decimal | int) -> str:
    if isinstance(value, int):
        return str(value)
    if value == value.to_integral():
        return str(value.quantize(Decimal(1)))
    return format(value.normalize(), "f")


def _string(value: str) -> str:
    return f'"{value.replace(chr(34), chr(34) * 2)}"'


def _one_tier(
    profile: SpanViewProfile, tier: QualifiedName, *, point: bool
) -> SpanViewProfile:
    return replace(
        profile,
        span_tiers=() if point else (tier,),
        point_tiers=(tier,) if point else (),
        point_coverage_relation=profile.point_coverage_relation if point else None,
    )


def _tick_coordinates(
    graph: Graph,
    profile: SpanViewProfile,
    clock: ClockProfile | None,
    scale: int | None,
) -> tuple[int, ...]:
    base = next(
        tier for tier in graph.tiers if tier.declaration.name == profile.base_tier
    )
    if scale is not None and (
        isinstance(scale, bool) or not isinstance(scale, int) or scale <= 0
    ):
        raise ValueError(f"TextGrid scale {scale!r} must be a positive integer")
    if clock is None:
        if scale is not None:
            raise ValueError("TextGrid scale requires a clock with refined coordinates")
        return tuple(range(len(base.items) + 1))
    coordinates = (
        clock.coordinates
        if clock.clock_tier == profile.base_tier
        else tuple(
            clock.refined_coordinate(BoundaryRef(profile.base_tier, index))
            for index in range(len(base.items) + 1)
        )
    )
    if len(coordinates) != len(base.items) + 1:
        raise ValueError(
            f"clock supplies {len(coordinates)} coordinates for "
            f"{len(base.items) + 1} base boundaries"
        )
    refined = next(
        (coordinate for coordinate in coordinates if coordinate.gap > 0), None
    )
    if refined is not None and scale is None:
        raise ValueError(
            f"base boundary {coordinates.index(refined)} has refined coordinate "
            f"{refined.to_data()!r}; TextGrid tick output requires scale"
        )
    if scale is None:
        return tuple(coordinate.tick for coordinate in coordinates)
    offender = next(
        (coordinate for coordinate in coordinates if coordinate.gap >= scale), None
    )
    if offender is not None:
        boundary = coordinates.index(offender)
        raise ValueError(
            f"base boundary {boundary} has clock coordinate {offender.to_data()!r}, "
            f"which does not fit scale {scale}; each gap must be less than the scale"
        )
    return tuple(coordinate.tick * scale + coordinate.gap for coordinate in coordinates)


def _physical_coordinates(
    graph: Graph, profile: SpanViewProfile, clock: ClockProfile | None
) -> tuple[Decimal, ...]:
    if clock is None:
        raise ValueError("TextGrid physical clock face requires a clock profile")
    base = next(
        tier for tier in graph.tiers if tier.declaration.name == profile.base_tier
    )
    values: list[Decimal] = []
    for index in range(len(base.items)):
        timing = clock.timing(profile.base_tier, index)
        if timing is None:
            raise ValueError(f"base item {index} has no physical timing")
        if index == 0:
            values.append(timing.start)
        expected = values[-1]
        if timing.start != expected:
            raise ValueError(
                f"base item {index} starts at {timing.start}, not previous end {expected}"
            )
        values.append(timing.start + timing.duration)
    if not base.items:
        raise ValueError("an empty base tier has no physical extent")
    return tuple(values)


def to_textgrid(
    graph: Graph,
    profile: SpanViewProfile,
    *,
    clock: ClockProfile | None = None,
    scale: int | None = None,
) -> str:
    """Render declared span and point tiers as a long-form TextGrid document."""
    if profile.clock_face == "physical":
        if scale is not None:
            raise ValueError("TextGrid scale applies only to the tick clock face")
        coordinates: tuple[Decimal | int, ...] = _physical_coordinates(
            graph, profile, clock
        )
    else:
        coordinates = _tick_coordinates(graph, profile, clock, scale)
    xmin, xmax = coordinates[0], coordinates[-1]
    declared = tuple((tier, False) for tier in profile.span_tiers) + tuple(
        (tier, True) for tier in profile.point_tiers
    )
    lines = [
        _LONG_HEADER,
        _OBJECT_HEADER,
        "",
        f"xmin = {_number(xmin)} ",
        f"xmax = {_number(xmax)} ",
        "tiers? <exists> ",
        f"size = {len(declared)} ",
        "item []: ",
    ]
    for tier_index, (tier_name, point) in enumerate(declared, 1):
        view = span_view(graph, _one_tier(profile, tier_name, point=point))
        lines.extend(
            (
                f"    item [{tier_index}]:",
                f"        class = {_string('TextTier' if point else 'IntervalTier')} ",
                f"        name = {_string(tier_name.local_name)} ",
                f"        xmin = {_number(xmin)} ",
                f"        xmax = {_number(xmax)} ",
            )
        )
        if point:
            lines.append(f"        points: size = {len(view.spans)} ")
            for entry_index, span in enumerate(view.spans, 1):
                lines.extend(
                    (
                        f"        points [{entry_index}]:",
                        f"            number = {_number(coordinates[span.start])} ",
                        f"            mark = {_string(span.value or '')} ",
                    )
                )
        else:
            intervals: list[tuple[int, int, str]] = []
            cursor = 0
            for span in view.spans:
                if cursor < span.start:
                    intervals.append((cursor, span.start, ""))
                intervals.append((span.start, span.end, span.value or ""))
                cursor = span.end
            if cursor < len(coordinates) - 1:
                intervals.append((cursor, len(coordinates) - 1, ""))
            lines.append(f"        intervals: size = {len(intervals)} ")
            for entry_index, (start, end, value) in enumerate(intervals, 1):
                lines.extend(
                    (
                        f"        intervals [{entry_index}]:",
                        f"            xmin = {_number(coordinates[start])} ",
                        f"            xmax = {_number(coordinates[end])} ",
                        f"            text = {_string(value)} ",
                    )
                )
    return "\n".join(lines) + "\n"
