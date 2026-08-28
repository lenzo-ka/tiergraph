"""Exercise reflective segmentation projection and its non-DOT emitters."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

import tiergraph_dot
from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    BoundarySide,
    DurablePositionRef,
    Graph,
    Item,
    ItemRef,
    NamespaceDeclaration,
    QualifiedName,
    RelationEndpointKind,
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


BASE, SPANS, CANDIDATES, SHADOW = (
    name("base"),
    name("spans"),
    name("candidates"),
    name("shadow"),
)
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


def profile_data(profile: SpanViewProfile) -> dict[str, object]:
    """Encode the declarative profile shape used by decoder and CLI tests."""
    return {
        "base_tier": profile.base_tier.to_data(),
        "span_tiers": [name.to_data() for name in profile.span_tiers],
        "coverage_relation": profile.coverage_relation.to_data(),
        "score_attribute": profile.score_attribute.to_data(),
        "value_attribute": profile.value_attribute.to_data(),
        "base_surface_attribute": profile.base_surface_attribute.to_data(),
        "char_offset_attribute": (
            None
            if profile.char_offset_attribute is None
            else profile.char_offset_attribute.to_data()
        ),
        "alternative_relation": (
            None
            if profile.alternative_relation is None
            else profile.alternative_relation.to_data()
        ),
    }


def test_profile_from_data_is_strict_and_hardens_every_qname() -> None:
    """Profile documents have an exact shape and path-specific string errors."""
    _, profile = fixture()
    data = profile_data(profile)
    assert SpanViewProfile.from_data(data) == profile
    data["char_offset_attribute"] = None
    data["alternative_relation"] = None
    assert SpanViewProfile.from_data(data) == replace(
        profile, char_offset_attribute=None, alternative_relation=None
    )

    for malformed in (None, {}, {**data, "extra": None}):
        with pytest.raises(ValueError, match="span profile"):
            SpanViewProfile.from_data(malformed)
    with pytest.raises(ValueError, match="span profile.span_tiers must be a list"):
        SpanViewProfile.from_data({**data, "span_tiers": {}})
    with pytest.raises(ValueError, match="span profile.base_tier must be an object"):
        SpanViewProfile.from_data({**data, "base_tier": None})
    with pytest.raises(
        ValueError,
        match=r"span profile\.span_tiers\[0\]\.namespace must be a string",
    ):
        SpanViewProfile.from_data(
            {**data, "span_tiers": [{"namespace": ["x"], "local_name": "x"}]}
        )
    with pytest.raises(
        ValueError, match="span profile.base_tier.local_name must be a string"
    ):
        SpanViewProfile.from_data(
            {**data, "base_tier": {"namespace": NS, "local_name": 12}}
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
    assert set(input_record) == {"input", "text", "version", "spans"}
    assert input_record["version"] == "1"
    assert "alternatives" in input_record["spans"][0]
    span_record = json.loads(spans.splitlines()[0])
    assert span_record["version"] == "1"
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


def test_zero_width_span_constructs_and_emitters_distinguish_it() -> None:
    """Anchors are valid, visible in text and HTML, and preserve source text."""
    anchor = Span("anchor", 1, 1, 1, 1, None, None, "anchor")
    view = SpanView("ab", (anchor,), ("a", "b"))
    surrounding = SpanView(
        "ab",
        (
            Span("left", 0, 1, 0, 1, None, None, "left"),
            anchor,
            Span("right", 1, 2, 1, 2, None, None, "right"),
        ),
        ("a", "b"),
    )
    assert " |\n" in to_text(view)
    assert to_text(
        SpanView(
            "abc",
            (
                Span("anchor", 1, 1, 1, 1, None, None, "anchor"),
                Span("wide", 1, 3, 1, 3, None, None, "wide"),
            ),
            ("a", "b", "c"),
        )
    ).splitlines()[1:3] == [" |", " []"]
    page = to_html(surrounding)
    assert '<mark class="zero-width"' in page
    assert 'mark.zero-width::before{content:"|"}' in page
    assert page.count(">a</mark>") == 1
    assert page.count(">b</mark>") == 1


def test_boundary_coverage_projects_anchor_at_leading_offset() -> None:
    """A boundary coverage edge projects equal base and character bounds."""
    graph, profile = fixture()
    boundary_coverage = BipartiteRelationDeclaration(
        COVERAGE,
        BASE_TYPE,
        SPAN_TYPE,
        left_endpoint=RelationEndpointKind.BOUNDARY,
    )
    graph = replace(
        graph,
        relation_declarations=tuple(
            boundary_coverage if declaration.name == COVERAGE else declaration
            for declaration in graph.relation_declarations
        ),
        relations=(
            RelationInstance(
                COVERAGE,
                DurablePositionRef(BASE, BoundarySide.BEFORE),
                ItemRef(SPANS, 0),
            ),
        ),
    )
    view = span_view(graph, profile)
    assert len(view.spans) == 1
    assert (view.spans[0].start, view.spans[0].end) == (0, 0)
    assert (view.spans[0].char_start, view.spans[0].char_end) == (10, 10)
    rendered = tiergraph_dot.dumps_spans(graph, profile)
    assert "boundary_0_0 [shape=point" in rendered
    assert 'item_1_0 -> boundary_0_0 [xlabel="extent"' in rendered


def test_conflicting_boundary_coverage_refuses() -> None:
    """Two boundary edges on one span item refuse rather than take the last."""
    graph, profile = fixture()
    boundary_coverage = BipartiteRelationDeclaration(
        COVERAGE,
        BASE_TYPE,
        SPAN_TYPE,
        left_endpoint=RelationEndpointKind.BOUNDARY,
    )
    graph = replace(
        graph,
        relation_declarations=tuple(
            boundary_coverage if declaration.name == COVERAGE else declaration
            for declaration in graph.relation_declarations
        ),
        relations=(
            RelationInstance(
                COVERAGE,
                DurablePositionRef(BASE, BoundarySide.BEFORE),
                ItemRef(SPANS, 0),
            ),
            RelationInstance(
                COVERAGE,
                DurablePositionRef(BASE, BoundarySide.AFTER),
                ItemRef(SPANS, 0),
            ),
        ),
    )
    with pytest.raises(ValueError, match="conflicting boundary coverage"):
        span_view(graph, profile)


def test_coverage_ignores_item_and_boundary_endpoints_outside_base_tier() -> None:
    """A shared endpoint type does not make another tier part of the base cover."""
    graph, profile = fixture()
    shadow = Tier(TierDeclaration(SHADOW, "Shadow"), (Item("shadow-0"),))
    shadow_members = SimpleRelationDeclaration(
        name("shadow-members"), SHADOW, BASE_TYPE
    )
    common = replace(
        graph,
        tiers=(*graph.tiers, shadow),
        relation_declarations=(*graph.relation_declarations, shadow_members),
    )
    item_graph = replace(
        common,
        relations=(
            *common.relations,
            RelationInstance(COVERAGE, ItemRef(SHADOW, 0), ItemRef(SPANS, 0)),
        ),
    )
    assert span_view(item_graph, profile) == span_view(graph, profile)

    boundary_coverage = BipartiteRelationDeclaration(
        COVERAGE,
        BASE_TYPE,
        SPAN_TYPE,
        left_endpoint=RelationEndpointKind.BOUNDARY,
    )
    boundary_graph = replace(
        common,
        relation_declarations=tuple(
            boundary_coverage if declaration.name == COVERAGE else declaration
            for declaration in common.relation_declarations
        ),
        relations=(
            RelationInstance(
                COVERAGE,
                DurablePositionRef(SHADOW, BoundarySide.BEFORE),
                ItemRef(SPANS, 0),
            ),
        ),
    )
    assert span_view(boundary_graph, profile).spans == ()


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
    for start, end in ((3, 2), (-1, 0), (0, 5)):
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
