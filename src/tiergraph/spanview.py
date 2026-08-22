"""Project segmentation graphs into deterministic span-oriented views."""

from __future__ import annotations

import html
import json
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from tiergraph.core import Graph, Item, ItemRef, QualifiedName, Tier
from tiergraph.path import ItemBinding, StructuralPathProfile

SPANVIEW_FORMAT_VERSION = "1"
"""Version tag written by the span-view JSON emitter."""


@dataclass(frozen=True, slots=True)
class SpanViewProfile:
    """Name every graph declaration used to interpret a segmentation."""

    base_tier: QualifiedName
    span_tiers: tuple[QualifiedName, ...]
    coverage_relation: QualifiedName
    score_attribute: QualifiedName
    value_attribute: QualifiedName
    base_surface_attribute: QualifiedName
    char_offset_attribute: QualifiedName | None = None
    alternative_relation: QualifiedName | None = None


@dataclass(frozen=True, slots=True)
class SpanAlternative:
    """Describe one ranked candidate associated with a selected span."""

    value: str | None
    score: str | None
    path: str


@dataclass(frozen=True, slots=True)
class Span:
    """Describe one selected span and its graph-derived extent."""

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
            if not 0 <= span.start < span.end <= size:
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
    ):
        if name not in tiers:
            raise ValueError(f"{role} {str(name)!r} is not declared in the graph")
    relations = {declaration.name for declaration in graph.relation_declarations}
    for role, optional_name in (
        ("coverage relation", profile.coverage_relation),
        ("alternative relation", profile.alternative_relation),
    ):
        if optional_name is not None and optional_name not in relations:
            raise ValueError(
                f"{role} {str(optional_name)!r} is not declared in the graph"
            )
    attributes = {declaration.name for declaration in graph.attribute_declarations}
    for role, optional_name in (
        ("score attribute", profile.score_attribute),
        ("value attribute", profile.value_attribute),
        ("base surface attribute", profile.base_surface_attribute),
        ("character offset attribute", profile.char_offset_attribute),
    ):
        if optional_name is not None and optional_name not in attributes:
            raise ValueError(
                f"{role} {str(optional_name)!r} is not declared in the graph"
            )


def span_view(
    graph: Graph, profile: SpanViewProfile, *, alternatives: bool = False
) -> SpanView:
    """Read a segmentation and its coverage entirely through the public graph API."""
    _validate_profile(graph, profile)
    base = _tier(graph, profile.base_tier)
    surfaces: list[str] = []
    offsets: list[int] = []
    for index, item in enumerate(base.items):
        surface = _attribute(item, profile.base_surface_attribute)
        if surface is None:
            raise ValueError(
                f"base item {index} lacks surface attribute {str(profile.base_surface_attribute)!r}"
            )
        surfaces.append(surface)
        if profile.char_offset_attribute is not None:
            lexical = _attribute(item, profile.char_offset_attribute)
            if lexical is None:
                raise ValueError(
                    f"base item {index} lacks character offset attribute {str(profile.char_offset_attribute)!r}"
                )
            offsets.append(int(lexical))

    members: dict[ItemRef, set[int]] = {}
    span_tiers = set(profile.span_tiers)
    for relation in graph.relations:
        if (
            relation.declaration == profile.coverage_relation
            and isinstance(relation.left, ItemRef)
            and isinstance(relation.right, ItemRef)
            and relation.left.tier == profile.base_tier
            and relation.right.tier in span_tiers
        ):
            members.setdefault(relation.right, set()).add(relation.left.index)

    paths = StructuralPathProfile()
    projected: list[Span] = []
    for reference, covered in members.items():
        start, end = min(covered), max(covered) + 1
        if covered != set(range(start, end)):
            raise ValueError(
                f"span {str(reference.tier)!r} item {reference.index} has non-contiguous coverage"
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
            ranked = tuple(
                sorted(
                    candidates,
                    key=lambda candidate: (
                        candidate.score is None,
                        Decimal(candidate.score).copy_negate()
                        if candidate.score is not None
                        else Decimal(0),
                        candidate.path,
                    ),
                )
            )
        projected.append(
            Span(
                label,
                start,
                end,
                offsets[start] if offsets else None,
                offsets[end - 1] + len(surfaces[end - 1]) if offsets else None,
                _attribute(item, profile.value_attribute),
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
                    "spans": [_span_data(span, alternatives) for span in view.spans],
                }
            )
        else:
            for span in view.spans:
                records.append({"input": index, **_span_data(span, alternatives)})
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
    marker = "^" if width == 1 else "[" + "-" * (width - 2) + "]"
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
        chunks.append(
            f'<mark title="{html.escape(title, quote=True)}">{html.escape(view.text[start:end], quote=True)}</mark>'
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
            "<style>body{font-family:sans-serif}mark{background:#ffe08a}table{border-collapse:collapse}th,td{border:1px solid #999;padding:.3rem;text-align:left}</style>",
            "</head><body>",
            f"<pre>{''.join(chunks)}</pre>",
            f"<table><thead><tr><th>label</th><th>index</th><th>chars</th><th>value</th><th>score</th><th>path</th>{alternative_heading}</tr></thead>",
            f"<tbody>{''.join(rows)}</tbody></table>",
            "</body></html>",
            "",
        )
    )


__all__ = [
    "SpanViewProfile",
    "SpanView",
    "Span",
    "SpanAlternative",
    "span_view",
    "to_json",
    "to_jsonl",
    "to_text",
    "to_html",
    "SPANVIEW_FORMAT_VERSION",
]
