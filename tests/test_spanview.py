"""Exercise reflective segmentation projection and its non-DOT emitters."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    Graph,
    Item,
    ItemRef,
    NamespaceDeclaration,
    QualifiedName,
    RelationInstance,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    XsdType,
)
from tiergraph.spanview import (
    Span,
    SpanAlternative,
    SpanView,
    SpanViewProfile,
    span_view,
    to_html,
    to_json,
    to_jsonl,
    to_text,
)

NS = "urn:tiergraph:spanview:test"


def name(local: str) -> QualifiedName:
    """Return a name in the span-view fixture namespace."""
    return QualifiedName(NS, local)


BASE, SPANS, CANDIDATES = name("base"), name("spans"), name("candidates")
BASE_TYPE, SPAN_TYPE, CANDIDATE_TYPE = (
    name("base-type"),
    name("span-type"),
    name("candidate-type"),
)
COVERAGE, ALTERNATIVES = name("covered-by"), name("alternative")
SURFACE, OFFSET, VALUE, SCORE = (
    name("surface"),
    name("offset"),
    name("value"),
    name("score"),
)


def fixture(*, offsets: bool = True) -> tuple[Graph, SpanViewProfile]:
    """Build a selected cover with scored alternatives."""
    base_items = tuple(
        Item(
            f"base-{index}",
            (
                AttributeValue(SURFACE, XsdType.STRING, surface),
                *(
                    (AttributeValue(OFFSET, XsdType.INTEGER, str(offset)),)
                    if offsets
                    else ()
                ),
            ),
        )
        for index, (surface, offset) in enumerate((("Hi", 10), (" ", 12), ("<x>&", 13)))
    )
    tiers = (
        Tier(TierDeclaration(BASE, "Base"), base_items),
        Tier(
            TierDeclaration(SPANS, "Spans"),
            (
                Item(
                    "span-0",
                    (
                        AttributeValue(VALUE, XsdType.STRING, "<tag>&\"'"),
                        AttributeValue(SCORE, XsdType.DECIMAL, "0.90"),
                    ),
                ),
                Item("span-1"),
            ),
        ),
        Tier(
            TierDeclaration(CANDIDATES, "Candidates"),
            tuple(
                Item(
                    f"candidate-{index}",
                    (
                        AttributeValue(VALUE, XsdType.STRING, value),
                        *(
                            (AttributeValue(SCORE, XsdType.DECIMAL, score),)
                            if score is not None
                            else ()
                        ),
                    ),
                )
                for index, (value, score) in enumerate(
                    (("lower", "0.2"), ("higher", "0.8"), ("unscored", None))
                )
            ),
        ),
    )
    declarations = (
        SimpleRelationDeclaration(name("base-members"), BASE, BASE_TYPE),
        SimpleRelationDeclaration(name("span-members"), SPANS, SPAN_TYPE),
        SimpleRelationDeclaration(
            name("candidate-members"), CANDIDATES, CANDIDATE_TYPE
        ),
        BipartiteRelationDeclaration(COVERAGE, BASE_TYPE, SPAN_TYPE),
        BipartiteRelationDeclaration(ALTERNATIVES, SPAN_TYPE, CANDIDATE_TYPE),
    )
    relations = (
        RelationInstance(COVERAGE, ItemRef(BASE, 0), ItemRef(SPANS, 0)),
        RelationInstance(COVERAGE, ItemRef(BASE, 1), ItemRef(SPANS, 0)),
        RelationInstance(COVERAGE, ItemRef(BASE, 2), ItemRef(SPANS, 1)),
        *(
            RelationInstance(
                ALTERNATIVES, ItemRef(SPANS, 0), ItemRef(CANDIDATES, index)
            )
            for index in range(3)
        ),
    )
    graph = Graph(
        (NamespaceDeclaration("s", NS),),
        tiers,
        declarations,
        relations,
        (
            AttributeDeclaration(SURFACE, AttributeDomain.ITEM, XsdType.STRING),
            AttributeDeclaration(OFFSET, AttributeDomain.ITEM, XsdType.INTEGER),
            AttributeDeclaration(VALUE, AttributeDomain.ITEM, XsdType.STRING),
            AttributeDeclaration(SCORE, AttributeDomain.ITEM, XsdType.DECIMAL),
        ),
    )
    return graph, SpanViewProfile(
        BASE,
        (SPANS,),
        COVERAGE,
        SCORE,
        VALUE,
        SURFACE,
        OFFSET if offsets else None,
        ALTERNATIVES,
    )


def test_projection_offsets_paths_values_and_ranked_alternatives() -> None:
    """Coverage edges determine contiguous extents and canonical candidate order."""
    graph, profile = fixture()
    view = span_view(graph, profile, alternatives=True)
    assert view.text == "Hi <x>&"
    assert view.base_surfaces == ("Hi", " ", "<x>&")
    assert [(span.start, span.end) for span in view.spans] == [(0, 2), (2, 3)]
    assert (view.spans[0].char_start, view.spans[0].char_end) == (10, 13)
    assert [candidate.value for candidate in view.spans[0].alternatives] == [
        "higher",
        "lower",
        "unscored",
    ]
    assert span_view(graph, profile).spans[0].alternatives == ()
    no_offsets = span_view(*fixture(offsets=False))
    assert no_offsets.spans[0].char_start is None
    assert no_offsets.spans[0].char_end is None


def test_json_jsonl_text_and_html_are_stable_and_toggle_alternatives() -> None:
    """Every format is stable and both JSON Lines record shapes carry input IDs."""
    graph, profile = fixture()
    view = span_view(graph, profile, alternatives=True)
    rendered = to_json(view, alternatives=True)
    assert rendered == to_json(view, alternatives=True)
    assert rendered.endswith("\n")
    assert list(json.loads(rendered)) == ["spans", "text", "version"]
    assert "alternatives" not in to_json(view)
    inputs = to_jsonl((view, view), alternatives=True)
    spans = to_jsonl(view, record="span")
    assert inputs == to_jsonl((view, view), alternatives=True)
    assert [json.loads(line)["input"] for line in inputs.splitlines()] == [0, 1]
    assert all(json.loads(line)["input"] == 0 for line in spans.splitlines())
    # The two record shapes are structurally distinct, not merely re-keyed.
    input_record = json.loads(inputs.splitlines()[0])
    assert set(input_record) == {"input", "text", "spans"}
    assert "alternatives" in input_record["spans"][0]
    span_record = json.loads(spans.splitlines()[0])
    assert "spans" not in span_record and "text" not in span_record
    assert {"input", "label", "start", "end", "path"} <= set(span_record)
    assert "alternatives" not in span_record
    assert "alternatives" in json.loads(
        to_jsonl(view, record="span", alternatives=True).splitlines()[0]
    )
    assert "alternatives" not in json.loads(to_jsonl(view).splitlines()[0])["spans"][0]
    assert "alternative:" in to_text(view, alternatives=True)
    assert "alternative:" not in to_text(view)
    page = to_html(view, alternatives=True)
    assert "&lt;tag&gt;&amp;&quot;&#x27;" in page
    assert "&lt;x&gt;&amp;" in page
    assert "<th>alternatives</th>" in page
    assert "<th>alternatives</th>" not in to_html(view)
    with pytest.raises(ValueError, match="unknown JSONL"):
        to_jsonl(view, record="item")


def test_projection_reports_profile_surface_and_coverage_errors() -> None:
    """Malformed profiles and graph-read segmentation defects are explicit."""
    graph, profile = fixture()
    with pytest.raises(ValueError, match="base tier"):
        span_view(graph, replace(profile, base_tier=name("missing")))
    with pytest.raises(ValueError, match="coverage relation"):
        span_view(graph, replace(profile, coverage_relation=name("missing")))
    with pytest.raises(ValueError, match="score attribute"):
        span_view(graph, replace(profile, score_attribute=name("missing")))
    tier = graph.tiers[0]
    missing_surface = replace(
        graph,
        tiers=(
            replace(tier, items=(Item("base-0"), *tier.items[1:])),
            *graph.tiers[1:],
        ),
    )
    with pytest.raises(ValueError, match="lacks surface"):
        span_view(missing_surface, profile)
    first = graph.tiers[0].items[0]
    missing_offset = replace(
        graph,
        tiers=(
            replace(
                graph.tiers[0],
                items=(
                    replace(
                        first,
                        attributes=tuple(
                            value for value in first.attributes if value.name != OFFSET
                        ),
                    ),
                    *graph.tiers[0].items[1:],
                ),
            ),
            *graph.tiers[1:],
        ),
    )
    with pytest.raises(ValueError, match="lacks character offset"):
        span_view(missing_offset, profile)
    gapped = replace(
        graph,
        relations=tuple(
            relation
            for relation in graph.relations
            if relation
            != RelationInstance(COVERAGE, ItemRef(BASE, 1), ItemRef(SPANS, 0))
        )
        + (RelationInstance(COVERAGE, ItemRef(BASE, 2), ItemRef(SPANS, 0)),),
    )
    with pytest.raises(ValueError, match="non-contiguous"):
        span_view(gapped, profile)


def test_additional_missing_profile_names_are_reported() -> None:
    """Every declared role is validated, not only the base tier and score."""
    graph, profile = fixture()
    missing = name("missing")
    broken = (
        ("span tier", replace(profile, span_tiers=(missing,))),
        ("value attribute", replace(profile, value_attribute=missing)),
        ("base surface attribute", replace(profile, base_surface_attribute=missing)),
        ("character offset attribute", replace(profile, char_offset_attribute=missing)),
        ("alternative relation", replace(profile, alternative_relation=missing)),
    )
    for role, candidate in broken:
        with pytest.raises(ValueError, match=role):
            span_view(graph, candidate)


def test_overlapping_cover_is_rejected() -> None:
    """A cover whose spans overlap is refused instead of corrupting emitters."""
    graph, profile = fixture()
    overlapped = replace(
        graph,
        relations=graph.relations
        + (RelationInstance(COVERAGE, ItemRef(BASE, 1), ItemRef(SPANS, 1)),),
    )
    with pytest.raises(ValueError, match="overlap"):
        span_view(overlapped, profile)


def test_spanview_type_rejects_overlapping_spans() -> None:
    """Non-overlap is a SpanView invariant, closing the manual-construction path."""
    with pytest.raises(ValueError, match="overlap"):
        SpanView(
            "abcd",
            (
                Span("outer", 0, 4, None, None, None, None, "/o"),
                Span("inner", 1, 2, None, None, None, None, "/i"),
            ),
            ("a", "b", "c", "d"),
        )


def test_spanview_type_rejects_out_of_range_or_inverted_spans() -> None:
    """Bounds are validated so an emitter can never slice text out of order."""
    for start, end in ((3, 2), (1, 1), (0, 5)):
        with pytest.raises(ValueError, match="bounds"):
            SpanView(
                "abcd",
                (Span("bad", start, end, None, None, None, None, "/bad"),),
                ("a", "b", "c", "d"),
            )


def _ranking_view(
    scored: tuple[tuple[str, str | None], ...],
    score_type: XsdType = XsdType.DECIMAL,
) -> SpanView:
    """Project a one-span cover whose alternatives carry the given scores."""
    candidates = tuple(
        Item(
            f"candidate-{index}",
            (
                AttributeValue(VALUE, XsdType.STRING, value),
                *(
                    (AttributeValue(SCORE, score_type, score),)
                    if score is not None
                    else ()
                ),
            ),
        )
        for index, (value, score) in enumerate(scored)
    )
    graph = Graph(
        (NamespaceDeclaration("s", NS),),
        (
            Tier(
                TierDeclaration(BASE, "Base"),
                (Item("base-0", (AttributeValue(SURFACE, XsdType.STRING, "x"),)),),
            ),
            Tier(TierDeclaration(SPANS, "Spans"), (Item("span-0"),)),
            Tier(TierDeclaration(CANDIDATES, "Candidates"), candidates),
        ),
        (
            SimpleRelationDeclaration(name("base-members"), BASE, BASE_TYPE),
            SimpleRelationDeclaration(name("span-members"), SPANS, SPAN_TYPE),
            SimpleRelationDeclaration(
                name("candidate-members"), CANDIDATES, CANDIDATE_TYPE
            ),
            BipartiteRelationDeclaration(COVERAGE, BASE_TYPE, SPAN_TYPE),
            BipartiteRelationDeclaration(ALTERNATIVES, SPAN_TYPE, CANDIDATE_TYPE),
        ),
        (
            RelationInstance(COVERAGE, ItemRef(BASE, 0), ItemRef(SPANS, 0)),
            *(
                RelationInstance(
                    ALTERNATIVES, ItemRef(SPANS, 0), ItemRef(CANDIDATES, index)
                )
                for index in range(len(candidates))
            ),
        ),
        (
            AttributeDeclaration(SURFACE, AttributeDomain.ITEM, XsdType.STRING),
            AttributeDeclaration(VALUE, AttributeDomain.ITEM, XsdType.STRING),
            AttributeDeclaration(SCORE, AttributeDomain.ITEM, score_type),
        ),
    )
    profile = SpanViewProfile(
        BASE, (SPANS,), COVERAGE, SCORE, VALUE, SURFACE, None, ALTERNATIVES
    )
    return span_view(graph, profile, alternatives=True)


def test_alternative_ranking_is_exact_decimal_with_path_and_none_order() -> None:
    """Ranking uses exact decimals: a float tie would misorder the top two."""
    # Candidate 0 (path index 0, earlier path) scores 1; candidate 1 scores just
    # above 1 by more than the default 28-digit context precision. float() and
    # context-rounding negation both collapse the two to a tie, so the path
    # tiebreak would wrongly put the weaker candidate first; exact context-free
    # comparison keeps the stronger one on top.
    view = _ranking_view(
        (
            ("a", "1"),
            ("b", "1.00000000000000000000000000001"),
            ("c", "0.5"),
            ("d", "0.5"),
            ("e", None),
        )
    )
    assert [candidate.value for candidate in view.spans[0].alternatives] == [
        "b",
        "a",
        "c",
        "d",
        "e",
    ]


@pytest.mark.parametrize(
    ("score", "message"), (("high", "not numeric"), ("NaN", "not finite"))
)
def test_alternative_ranking_refuses_invalid_scores_clearly(
    score: str, message: str
) -> None:
    """Invalid score lexicals name the alternative instead of leaking Decimal errors."""
    with pytest.raises(ValueError, match=message):
        _ranking_view((("bad", score),), XsdType.STRING)


def test_character_offset_refusal_names_the_item_and_lexical() -> None:
    """A malformed character offset is reported with profile context."""
    graph, profile = fixture()
    malformed = replace(
        graph,
        tiers=(
            replace(
                graph.tiers[0],
                items=tuple(
                    replace(
                        item,
                        attributes=tuple(
                            AttributeValue(
                                OFFSET,
                                XsdType.DECIMAL,
                                "1.5" if index == 0 else value.lexical,
                            )
                            if value.name == OFFSET
                            else value
                            for value in item.attributes
                        ),
                    )
                    for index, item in enumerate(graph.tiers[0].items)
                ),
            ),
            *graph.tiers[1:],
        ),
        attribute_declarations=tuple(
            replace(declaration, value_type=XsdType.DECIMAL)
            if declaration.name == OFFSET
            else declaration
            for declaration in graph.attribute_declarations
        ),
    )
    with pytest.raises(
        ValueError, match=r"base item 0 character offset '1\.5'.*not an integer"
    ):
        span_view(malformed, profile)


def test_untyped_span_label_falls_back_to_tier_short_name() -> None:
    """An untyped consumer graph uses the span tier's display short name."""
    graph, profile = fixture()
    graph._types_by_tier.pop(SPANS)
    assert span_view(graph, profile).spans[0].label == "spans"


def test_emitters_accept_manually_projected_edge_values() -> None:
    """Empty rows, one-character rulers, and absent values remain renderable."""
    empty = SpanView("", (), ())
    assert to_jsonl(empty, record="span") == ""
    assert "label" in to_text(empty)
    view = SpanView(
        "x",
        (
            Span(
                "<&\"'",
                0,
                1,
                None,
                None,
                None,
                None,
                "/x",
                (SpanAlternative(None, None, "/a"),),
            ),
        ),
        ("x",),
    )
    assert "^" in to_text(view)
    assert "&lt;&amp;&quot;&#x27;" in to_html(view)
