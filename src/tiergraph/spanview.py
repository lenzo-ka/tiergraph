"""Project segmentation graphs into deterministic span-oriented views."""

from __future__ import annotations

import html
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from tiergraph.core import (
    BipartiteRelationDeclaration,
    DurableBoundaryRef,
    Graph,
    Item,
    ItemRef,
    QualifiedName,
    RelationEndpointKind,
    Tier,
)
from tiergraph.machine import _QNameFields
from tiergraph.path import ItemBinding, StructuralPathProfile

SPANVIEW_FORMAT_VERSION = "1"
"""Version tag written by the span-view JSON emitter."""


@dataclass(frozen=True, slots=True)
class SpanViewProfile:
    """Name the graph declarations a segmentation has to be selected among.

    ``coverage_relation`` and ``alternative_relation`` must name bipartite
    declarations.  A span is an interval over the base tier, so each fact this
    view reads is one base endpoint paired with one span item; there is no
    reading of a polyadic instance's ordered sides that keeps that meaning.
    Naming a non-bipartite declaration is refused rather than skipped, because
    silently reading only the bipartite collection would report a partial
    segmentation as a complete one.

    One declaration the projection reads is deliberately absent: a span's
    ``label`` is the item type its tier's simple membership supplies, read
    through :meth:`Graph.item_type` and falling back to the tier's short name
    when the tier is untyped.  A profile names what a reading has to be
    selected among, and a tier carries at most one simple membership, so there
    is nothing there to select.
    """

    base_tier: QualifiedName
    span_tiers: tuple[QualifiedName, ...]
    coverage_relation: QualifiedName
    score_attribute: QualifiedName
    value_attribute: QualifiedName
    base_surface_attribute: QualifiedName | None = None
    char_offset_attribute: QualifiedName | None = None
    alternative_relation: QualifiedName | None = None
    point_tiers: tuple[QualifiedName, ...] = ()
    point_coverage_relation: QualifiedName | None = None
    value_attributes: tuple[tuple[QualifiedName, QualifiedName], ...] = ()
    clock_face: str = "tick"

    def __post_init__(self) -> None:
        """Refuse ambiguous tier roles and incomplete point declarations."""
        overlap = set(self.span_tiers).intersection(self.point_tiers)
        if overlap:
            offender = min(overlap)
            raise ValueError(
                f"tier {str(offender)!r} is both a span tier and a point tier"
            )
        if self.point_tiers and self.point_coverage_relation is None:
            raise ValueError(
                "point_coverage_relation is required when point_tiers is non-empty"
            )
        if self.clock_face not in {"tick", "physical"}:
            raise ValueError(
                f"clock_face {self.clock_face!r} must be 'tick' or 'physical'"
            )
        value_tiers = [tier for tier, _ in self.value_attributes]
        if len(set(value_tiers)) != len(value_tiers):
            duplicate = next(
                tier for tier in value_tiers if value_tiers.count(tier) > 1
            )
            raise ValueError(
                f"value_attributes names tier {str(duplicate)!r} more than once"
            )

    @classmethod
    def from_data(cls, data: object) -> SpanViewProfile:
        """Decode a strict declarative span-view profile document."""
        required_keys = {
            "base_tier",
            "span_tiers",
            "coverage_relation",
            "score_attribute",
            "value_attribute",
            "char_offset_attribute",
            "alternative_relation",
        }
        optional_keys = {
            "base_surface_attribute",
            "point_tiers",
            "point_coverage_relation",
            "value_attributes",
            "clock_face",
        }
        if not isinstance(data, dict):
            raise ValueError("span profile must be an object")
        actual = set(data)
        if not required_keys <= actual or not actual <= required_keys | optional_keys:
            raise ValueError(
                "span profile fields must include "
                f"{sorted(required_keys)!r} and may include "
                f"{sorted(optional_keys)!r}; got {sorted(actual)!r}"
            )
        fields = _QNameFields(data, "span profile", actual)
        obj = fields.obj

        span_tiers = obj["span_tiers"]
        if not isinstance(span_tiers, list):
            raise ValueError("span profile.span_tiers must be a list")
        point_tiers = obj.get("point_tiers", [])
        if not isinstance(point_tiers, list):
            raise ValueError("span profile.point_tiers must be a list")
        value_attributes = obj.get("value_attributes", {})
        if not isinstance(value_attributes, dict):
            raise ValueError("span profile.value_attributes must be an object")
        decoded_values: list[tuple[QualifiedName, QualifiedName]] = []
        for index, (tier_data, attribute_data) in enumerate(value_attributes.items()):
            if not isinstance(tier_data, str):
                raise ValueError("span profile.value_attributes keys must be strings")
            match = re.fullmatch(r"\{(.+)\}([^{}]+)", tier_data)
            if match is None:
                raise ValueError(
                    "span profile.value_attributes keys must use {namespace}local_name"
                )
            decoded_values.append(
                (
                    QualifiedName(match.group(1), match.group(2)),
                    fields.decode(
                        attribute_data,
                        f"span profile.value_attributes[{index}]",
                    ),
                )
            )
        clock_face = obj.get("clock_face", "tick")
        if not isinstance(clock_face, str):
            raise ValueError("span profile.clock_face must be a string")
        return cls(
            fields.required("base_tier"),
            tuple(
                fields.decode(value, f"span profile.span_tiers[{index}]")
                for index, value in enumerate(span_tiers)
            ),
            fields.required("coverage_relation"),
            fields.required("score_attribute"),
            fields.required("value_attribute"),
            fields.optional("base_surface_attribute")
            if "base_surface_attribute" in obj
            else None,
            fields.optional("char_offset_attribute"),
            fields.optional("alternative_relation"),
            tuple(
                fields.decode(value, f"span profile.point_tiers[{index}]")
                for index, value in enumerate(point_tiers)
            ),
            fields.optional("point_coverage_relation")
            if "point_coverage_relation" in obj
            else None,
            tuple(decoded_values),
            clock_face,
        )


@dataclass(frozen=True, slots=True)
class SpanAlternative:
    """Describe one ranked candidate associated with a selected span."""

    value: str | None
    score: str | None
    path: str


@dataclass(frozen=True, slots=True)
class Span:
    """Describe one selected span whose extent is derived from live coverage.

    The kernel graph stores membership, not an origin-plus-extent snapshot;
    this projection carries the resulting bounds for renderers.  Coverage must
    remain contiguous, so a new base item inside its range requires the caller
    to update membership rather than being absorbed or splitting it.
    """

    label: str
    start: int
    end: int
    char_start: int | None
    char_end: int | None
    value: str | None
    score: str | None
    path: str
    alternatives: tuple[SpanAlternative, ...] = ()


@dataclass(frozen=True, slots=True)
class SpanView:
    """Hold reconstructed input text and its ordered, non-overlapping spans."""

    text: str
    spans: tuple[Span, ...]
    base_surfaces: tuple[str, ...]

    def __post_init__(self) -> None:
        """Refuse spans an emitter could not slice cleanly, keeping them total.

        Emitters walk the spans once in order and slice the text by base index,
        so every span must have valid in-range half-open bounds and the cover
        must be ordered and non-overlapping; otherwise the sliced text would be
        duplicated, reordered, or out of range.
        """
        size = len(self.base_surfaces)
        for span in self.spans:
            if not 0 <= span.start <= span.end <= size:
                raise ValueError(
                    f"span {span.path!r} has bounds {span.start}..{span.end} "
                    f"outside the half-open range of {size} base items"
                )
        for previous, current in zip(self.spans, self.spans[1:], strict=False):
            if current.start < previous.end:
                raise ValueError(
                    f"spans {previous.path!r} and {current.path!r} overlap or are "
                    "unordered; a segmentation cover must be ordered and "
                    "non-overlapping"
                )


def _attribute(item: Item, name: QualifiedName) -> str | None:
    return next(
        (value.lexical for value in item.attributes if value.name == name), None
    )


def _tier(graph: Graph, name: QualifiedName) -> Tier:
    return next(tier for tier in graph.tiers if tier.declaration.name == name)


def _validate_profile(graph: Graph, profile: SpanViewProfile) -> None:
    tiers = {tier.declaration.name for tier in graph.tiers}
    for role, name in (
        ("base tier", profile.base_tier),
        *(("span tier", name) for name in profile.span_tiers),
        *(("point tier", name) for name in profile.point_tiers),
    ):
        if name not in tiers:
            raise ValueError(f"{role} {str(name)!r} is not declared in the graph")
    relations = {
        declaration.name: declaration for declaration in graph.relation_declarations
    }
    for role, optional_name in (
        ("coverage relation", profile.coverage_relation),
        ("point coverage relation", profile.point_coverage_relation),
        ("alternative relation", profile.alternative_relation),
    ):
        if optional_name is None:
            continue
        declaration = relations.get(optional_name)
        if declaration is None:
            raise ValueError(
                f"{role} {str(optional_name)!r} is not declared in the graph"
            )
        if not isinstance(declaration, BipartiteRelationDeclaration):
            kind = declaration.to_data()["kind"]
            raise ValueError(
                f"{role} {str(optional_name)!r} is declared {kind}; a span view "
                "reads one left endpoint and one right span item per fact and "
                "requires a bipartite declaration, so it cannot project a "
                f"{kind} relation"
            )
        if role == "point coverage relation" and (
            declaration.left_endpoint is not RelationEndpointKind.BOUNDARY
            or declaration.right_endpoint is not RelationEndpointKind.ITEM
        ):
            raise ValueError(
                f"point coverage relation {str(optional_name)!r} must relate boundary to item"
            )
    attributes = {declaration.name for declaration in graph.attribute_declarations}
    for role, optional_name in (
        ("score attribute", profile.score_attribute),
        ("value attribute", profile.value_attribute),
        ("base surface attribute", profile.base_surface_attribute),
        ("character offset attribute", profile.char_offset_attribute),
        *(
            (f"value attribute for tier {str(tier)!r}", attribute)
            for tier, attribute in profile.value_attributes
        ),
    ):
        if optional_name is not None and optional_name not in attributes:
            raise ValueError(
                f"{role} {str(optional_name)!r} is not declared in the graph"
            )


def span_view(  # noqa: PLR0915 -- one ordered span projection
    graph: Graph, profile: SpanViewProfile, *, alternatives: bool = False
) -> SpanView:
    """Read a segmentation and its coverage entirely through the public graph API."""
    _validate_profile(graph, profile)
    base = _tier(graph, profile.base_tier)
    surfaces: list[str] = []
    offsets: list[int] = []
    for index, item in enumerate(base.items):
        surface = (
            ""
            if profile.base_surface_attribute is None
            else _attribute(item, profile.base_surface_attribute)
        )
        if surface is None and profile.base_surface_attribute is not None:
            raise ValueError(
                f"base item {index} lacks surface attribute {str(profile.base_surface_attribute)!r}"
            )
        assert surface is not None
        surfaces.append(surface)
        if profile.char_offset_attribute is not None:
            lexical = _attribute(item, profile.char_offset_attribute)
            if lexical is None:
                raise ValueError(
                    f"base item {index} lacks character offset attribute {str(profile.char_offset_attribute)!r}"
                )
            try:
                offsets.append(int(lexical))
            except ValueError as error:
                raise ValueError(
                    f"base item {index} character offset {lexical!r} from "
                    f"attribute {str(profile.char_offset_attribute)!r} is not an integer"
                ) from error

    members: dict[ItemRef, set[int]] = {}
    anchors: dict[ItemRef, int] = {}
    span_tiers = set(profile.span_tiers)
    point_tiers = set(profile.point_tiers)
    for relation in graph.relations:
        right_tier = (
            relation.right.tier if isinstance(relation.right, ItemRef) else None
        )
        expected_relation = (
            profile.point_coverage_relation
            if right_tier in point_tiers
            else profile.coverage_relation
        )
        if (
            relation.declaration != expected_relation
            or not isinstance(relation.right, ItemRef)
            or relation.right.tier not in span_tiers | point_tiers
        ):
            continue
        if isinstance(relation.left, ItemRef):
            if relation.left.tier == profile.base_tier:
                members.setdefault(relation.right, set()).add(relation.left.index)
        else:
            assert isinstance(relation.left, DurableBoundaryRef)
            boundary = graph.resolve_boundary(relation.left)
            if boundary.tier == profile.base_tier:
                previous = anchors.setdefault(relation.right, boundary.index)
                if previous != boundary.index:
                    raise ValueError(
                        f"span {str(relation.right.tier)!r} item {relation.right.index} "
                        f"has conflicting boundary coverage at {previous} and "
                        f"{boundary.index}"
                    )

    paths = StructuralPathProfile()
    projected: list[Span] = []
    covered_references = members.keys() | anchors.keys()
    for tier_name in (*profile.span_tiers, *profile.point_tiers):
        tier = _tier(graph, tier_name)
        for index in range(len(tier.items)):
            reference = ItemRef(tier_name, index)
            if reference not in covered_references:
                role = "point" if tier_name in point_tiers else "span"
                raise ValueError(
                    f"{role} tier {str(tier_name)!r} item {index} has no coverage"
                )
    for reference in covered_references:
        covered = members.get(reference)
        if covered is None:
            start = end = anchors[reference]
        else:
            start, end = min(covered), max(covered) + 1
        if covered is not None and covered != set(range(start, end)):
            raise ValueError(
                f"span {str(reference.tier)!r} item {reference.index} has non-contiguous coverage"
            )
        if reference.tier in point_tiers and start != end:
            raise ValueError(
                f"point tier {str(reference.tier)!r} item {reference.index} "
                f"projects positive width {start}..{end}; a point must be zero-width"
            )
        if reference.tier in span_tiers and start == end:
            raise ValueError(
                f"span tier {str(reference.tier)!r} item {reference.index} "
                f"projects zero width at {start}; a span must be positive-width"
            )
        tier = _tier(graph, reference.tier)
        item = tier.items[reference.index]
        try:
            label = graph.item_type(reference).local_name
        except ValueError:
            label = tier.declaration.short_name
        path = str(paths.spell(ItemBinding(reference), graph))
        ranked: tuple[SpanAlternative, ...] = ()
        if alternatives and profile.alternative_relation is not None:
            candidates: list[SpanAlternative] = []
            for relation in graph.relations:
                if (
                    relation.declaration == profile.alternative_relation
                    and relation.left == reference
                    and isinstance(relation.right, ItemRef)
                ):
                    candidate = _tier(graph, relation.right.tier).items[
                        relation.right.index
                    ]
                    candidates.append(
                        SpanAlternative(
                            _attribute(candidate, profile.value_attribute),
                            _attribute(candidate, profile.score_attribute),
                            str(paths.spell(ItemBinding(relation.right), graph)),
                        )
                    )
            scores: dict[str, Decimal] = {}
            for alternative in candidates:
                if alternative.score is None:
                    continue
                try:
                    score = Decimal(alternative.score)
                except InvalidOperation as error:
                    raise ValueError(
                        f"alternative {alternative.path!r} score {alternative.score!r} "
                        f"from attribute {str(profile.score_attribute)!r} is not numeric"
                    ) from error
                if not score.is_finite():
                    raise ValueError(
                        f"alternative {alternative.path!r} score {alternative.score!r} "
                        f"from attribute {str(profile.score_attribute)!r} is not finite"
                    )
                scores[alternative.path] = score
            ranked = tuple(
                sorted(
                    candidates,
                    key=lambda candidate: (
                        candidate.score is None,
                        scores[candidate.path].copy_negate()
                        if candidate.score is not None
                        else Decimal(0),
                        candidate.path,
                    ),
                )
            )
        char_start: int | None
        char_end: int | None
        if offsets:
            char_start = (
                offsets[start]
                if start < len(offsets)
                else offsets[-1] + len(surfaces[-1])
            )
            char_end = (
                char_start
                if start == end
                else offsets[end - 1] + len(surfaces[end - 1])
            )
        else:
            char_start = char_end = None
        value_attribute = dict(profile.value_attributes).get(
            reference.tier, profile.value_attribute
        )
        projected.append(
            Span(
                label,
                start,
                end,
                char_start,
                char_end,
                _attribute(item, value_attribute),
                _attribute(item, profile.score_attribute),
                path,
                ranked,
            )
        )
    projected.sort(key=lambda span: (span.start, span.end, span.label, span.path))
    return SpanView("".join(surfaces), tuple(projected), tuple(surfaces))


def _alternative_data(alternative: SpanAlternative) -> dict[str, object]:
    return {
        "value": alternative.value,
        "score": alternative.score,
        "path": alternative.path,
    }


def _span_data(span: Span, alternatives: bool) -> dict[str, object]:
    data: dict[str, object] = {
        "label": span.label,
        "start": span.start,
        "end": span.end,
        "char_start": span.char_start,
        "char_end": span.char_end,
        "value": span.value,
        "score": span.score,
        "path": span.path,
    }
    if alternatives:
        data["alternatives"] = [_alternative_data(value) for value in span.alternatives]
    return data


def to_json(view: SpanView, *, alternatives: bool = False) -> str:
    """Return one stable, indented JSON span-view document."""
    data = {
        "version": SPANVIEW_FORMAT_VERSION,
        "text": view.text,
        "spans": [_span_data(span, alternatives) for span in view.spans],
    }
    return (
        json.dumps(data, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    )


def to_jsonl(
    views: SpanView | Iterable[SpanView],
    *,
    record: str = "input",
    alternatives: bool = False,
) -> str:
    """Return compact JSON Lines records grouped by input or flattened by span."""
    if record not in {"input", "span"}:
        raise ValueError(f"unknown JSONL record shape {record!r}")
    sequence = (views,) if isinstance(views, SpanView) else views
    records: list[dict[str, object]] = []
    for index, view in enumerate(sequence):
        if record == "input":
            records.append(
                {
                    "input": index,
                    "text": view.text,
                    "version": SPANVIEW_FORMAT_VERSION,
                    "spans": [_span_data(span, alternatives) for span in view.spans],
                }
            )
        else:
            records.extend(
                (
                    {
                        "input": index,
                        "version": SPANVIEW_FORMAT_VERSION,
                        **_span_data(span, alternatives),
                    }
                )
                for span in view.spans
            )
    return "".join(
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
        for value in records
    )


def _char_boundaries(view: SpanView) -> list[int]:
    boundaries = [0]
    for surface in view.base_surfaces:
        boundaries.append(boundaries[-1] + len(surface))
    return boundaries


def _ruler(start: int, end: int) -> str:
    width = end - start
    marker = "|" if width == 0 else "^" if width == 1 else "[" + "-" * (width - 2) + "]"
    return " " * start + marker


def to_text(view: SpanView, *, alternatives: bool = False) -> str:
    """Return a deterministic ruler and aligned plain-text span table."""
    boundaries = _char_boundaries(view)
    rulers = [
        _ruler(boundaries[span.start], boundaries[span.end]) for span in view.spans
    ]
    headings = ("label", "index", "chars", "value", "score")
    rows = [
        (
            span.label,
            f"{span.start}..{span.end}",
            "-" if span.char_start is None else f"{span.char_start}..{span.char_end}",
            span.value or "-",
            span.score or "-",
        )
        for span in view.spans
    ]
    widths = [
        max([len(headings[column]), *(len(row[column]) for row in rows)])
        for column in range(len(headings))
    ]

    def _format_row(row: tuple[str, ...]) -> str:
        return "  ".join(
            value.ljust(widths[index]) for index, value in enumerate(row)
        ).rstrip()

    lines = [
        view.text,
        *rulers,
        _format_row(headings),
        _format_row(tuple("-" * width for width in widths)),
    ]
    for span, row in zip(view.spans, rows, strict=True):
        lines.append(_format_row(row))
        if alternatives:
            lines.extend(
                f"  alternative: value={candidate.value or '-'} score={candidate.score or '-'} path={candidate.path}"
                for candidate in span.alternatives
            )
    return "\n".join(lines) + "\n"


def to_html(view: SpanView, *, alternatives: bool = False) -> str:
    """Return a self-contained, injection-safe HTML segmentation report."""
    boundaries = _char_boundaries(view)
    chunks: list[str] = []
    cursor = 0
    for span in view.spans:
        start, end = boundaries[span.start], boundaries[span.end]
        chunks.append(html.escape(view.text[cursor:start], quote=True))
        title = " | ".join((span.label, span.value or "", span.score or ""))
        marker_class = ' class="zero-width"' if start == end else ""
        chunks.append(
            f'<mark{marker_class} title="{html.escape(title, quote=True)}">{html.escape(view.text[start:end], quote=True)}</mark>'
        )
        cursor = end
    chunks.append(html.escape(view.text[cursor:], quote=True))
    rows: list[str] = []
    for span in view.spans:
        cells = [
            span.label,
            f"{span.start}..{span.end}",
            "-" if span.char_start is None else f"{span.char_start}..{span.char_end}",
            span.value or "",
            span.score or "",
            span.path,
        ]
        rendered = "".join(
            f"<td>{html.escape(value, quote=True)}</td>" for value in cells
        )
        if alternatives:
            values = "".join(
                f"<li>{html.escape(candidate.value or '', quote=True)} | {html.escape(candidate.score or '', quote=True)} | {html.escape(candidate.path, quote=True)}</li>"
                for candidate in span.alternatives
            )
            rendered += f"<td><ul>{values}</ul></td>"
        rows.append(f"<tr>{rendered}</tr>")
    alternative_heading = "<th>alternatives</th>" if alternatives else ""
    return "\n".join(
        (
            "<!doctype html>",
            '<html lang="en"><head><meta charset="utf-8"><title>Span view</title>',
            '<style>body{font-family:sans-serif}mark{background:#ffe08a}mark.zero-width::before{content:"|"}table{border-collapse:collapse}th,td{border:1px solid #999;padding:.3rem;text-align:left}</style>',
            "</head><body>",
            f"<pre>{''.join(chunks)}</pre>",
            f"<table><thead><tr><th>label</th><th>index</th><th>chars</th><th>value</th><th>score</th><th>path</th>{alternative_heading}</tr></thead>",
            f"<tbody>{''.join(rows)}</tbody></table>",
            "</body></html>",
            "",
        )
    )


__all__ = [
    "SPANVIEW_FORMAT_VERSION",
    "Span",
    "SpanAlternative",
    "SpanView",
    "SpanViewProfile",
    "span_view",
    "to_html",
    "to_json",
    "to_jsonl",
    "to_text",
]
