"""Exercise the Praat TextGrid reader, writer, and span-profile integration."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

import tiergraph.textgrid as textgrid_module
from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    Boundary,
    BoundaryRef,
    ClockProfile,
    Graph,
    ItemRef,
    NamespaceDeclaration,
    PhysicalTiming,
    QualifiedName,
    SimpleRelationDeclaration,
    SpanViewProfile,
    XsdType,
    from_textgrid,
    span_view,
    to_textgrid,
)

FIXTURES = Path(__file__).parent / "fixtures" / "textgrid"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _one_tier(
    profile: SpanViewProfile, tier: QualifiedName, *, point: bool
) -> SpanViewProfile:
    return replace(
        profile,
        span_tiers=() if point else (tier,),
        point_tiers=(tier,) if point else (),
        point_coverage_relation=profile.point_coverage_relation if point else None,
    )


def test_long_and_short_goldens_decode_to_the_same_graph_and_profile() -> None:
    """Both Praat forms preserve tiers, empty intervals, and exact boundaries."""
    long = from_textgrid(_fixture("reference-long.TextGrid"))
    short = from_textgrid(_fixture("reference-short.TextGrid"))
    assert long == short
    words = span_view(
        long.graph, _one_tier(long.profile, long.profile.span_tiers[0], point=False)
    )
    assert [(span.start, span.end, span.value) for span in words.spans] == [
        (0, 1, "hello"),
        (1, 3, ""),
    ]


def test_reader_decodes_doubled_quotes_and_utf16_bom() -> None:
    """TextGrid string quoting and BOM-directed UTF-16 decoding preserve labels."""
    document = (
        _fixture("reference-long.TextGrid")
        .decode()
        .replace('text = "hello"', 'text = "he said ""hi"""')
    )
    result = from_textgrid(document.encode("utf-16"))
    view = span_view(
        result.graph,
        _one_tier(result.profile, result.profile.span_tiers[0], point=False),
    )
    assert view.spans[0].value == 'he said "hi"'


def test_absent_tiers_are_legal() -> None:
    """An absent tier collection yields a base axis and no selected tiers."""
    result = from_textgrid(
        'File type = "ooTextFile short"\n"TextGrid"\n\n0\n1\n<absent>\n'
    )
    assert result.profile.span_tiers == ()
    assert result.profile.point_tiers == ()
    assert len(result.graph.tiers[0].items) == 1
    assert (
        from_textgrid(
            'File type = "ooTextFile short"\n"TextGrid"\n\n0\n1\n<absent>\n\n'
        ).graph
        == result.graph
    )


def test_reader_refuses_a_tier_range_that_disagrees_with_the_document() -> None:
    """A near-valid tier with a different xmax names that tier in its refusal."""
    document = (
        _fixture("reference-long.TextGrid")
        .decode()
        .replace(
            'name = "marks" \n        xmin = 0 \n        xmax = 2.3',
            'name = "marks" \n        xmin = 0 \n        xmax = 3',
        )
    )
    with pytest.raises(ValueError, match=r"tier 'marks' range 0\.\.3 disagrees"):
        from_textgrid(document)


def test_points_at_shared_and_new_boundaries_project_zero_width() -> None:
    """Point coverage uses base boundaries whether or not intervals supplied them."""
    document = (
        _fixture("reference-long.TextGrid")
        .decode()
        .replace(
            "points: size = 1",
            "points: size = 2",
        )
        .replace(
            '            mark = "x" ',
            '            mark = "x" \n        points [2]:\n            number = 0.5 \n            mark = "shared" ',
        )
    )
    result = from_textgrid(document)
    marks = span_view(
        result.graph,
        _one_tier(result.profile, result.profile.point_tiers[0], point=True),
    )
    assert [(span.start, span.end, span.value) for span in marks.spans] == [
        (1, 1, "shared"),
        (2, 2, "x"),
    ]


def test_integer_tick_golden_round_trips_byte_identically() -> None:
    """The long-form writer preserves the hand-written integer-axis document."""
    document = _fixture("integer-long.TextGrid")
    assert to_textgrid(*from_textgrid(document, unit="tick")).encode() == document


def test_span_and_point_roles_refuse_the_opposite_coverage_shape() -> None:
    """Near-valid role swaps name uncovered items instead of emitting empty tiers."""
    result = from_textgrid(_fixture("reference-long.TextGrid"))
    words = result.profile.span_tiers[0]
    marks = result.profile.point_tiers[0]
    with pytest.raises(
        ValueError, match=r"point tier .*words.* item 0 has no coverage"
    ):
        span_view(
            result.graph,
            replace(
                result.profile,
                span_tiers=(),
                point_tiers=(words,),
            ),
        )
    with pytest.raises(ValueError, match=r"span tier .*marks.* item 0 has no coverage"):
        span_view(
            result.graph,
            replace(
                result.profile,
                span_tiers=(marks,),
                point_tiers=(),
                point_coverage_relation=None,
            ),
        )


def test_physical_face_requires_a_clock() -> None:
    """Physical output refuses before it can substitute structural ticks."""
    result = from_textgrid(_fixture("reference-long.TextGrid"))
    with pytest.raises(ValueError, match="physical clock face requires a clock"):
        to_textgrid(result.graph, replace(result.profile, clock_face="physical"))


def test_reader_clock_preserves_exact_physical_boundaries_and_unit() -> None:
    """The reader's declared clock reproduces source decimals without floats."""
    result = from_textgrid(_fixture("reference-long.TextGrid"), unit="seconds")
    timing = result.clock.timing(result.profile.base_tier, 1)
    assert timing is not None
    assert timing.to_data() == {
        "start": "0.5",
        "duration": "0.7",
        "unit": "seconds",
    }
    physical = to_textgrid(
        result.graph,
        replace(result.profile, clock_face="physical"),
        clock=result.clock,
    )
    assert "xmax = 2.3 " in physical
    assert "number = 1.2 " in physical


def _refined_clock() -> tuple[Graph, SpanViewProfile, ClockProfile]:
    result = from_textgrid(_fixture("integer-long.TextGrid"))
    tick = QualifiedName("urn:tiergraph:textgrid:test:clock", "tick")
    gap = QualifiedName("urn:tiergraph:textgrid:test:clock", "gap")
    graph = replace(
        result.graph,
        namespaces=(
            *result.graph.namespaces,
            NamespaceDeclaration("clock", "urn:tiergraph:textgrid:test:clock"),
        ),
        attribute_declarations=(
            *result.graph.attribute_declarations,
            AttributeDeclaration(tick, AttributeDomain.BOUNDARY, XsdType.INTEGER),
            AttributeDeclaration(gap, AttributeDomain.BOUNDARY, XsdType.INTEGER),
        ),
        boundary_values=tuple(
            Boundary(
                BoundaryRef(result.profile.base_tier, index),
                (
                    AttributeValue(tick, XsdType.INTEGER, str(coarse)),
                    AttributeValue(gap, XsdType.INTEGER, str(refinement)),
                ),
            )
            for index, (coarse, refinement) in enumerate(
                ((0, 0), (1, 0), (1, 1), (2, 0))
            )
        ),
    )
    clock = ClockProfile.from_boundary_values(
        graph,
        result.profile.base_tier,
        tick_attribute=tick,
        gap_attribute=gap,
    )
    return graph, result.profile, clock


def test_refined_tick_face_requires_a_scale() -> None:
    """A refined boundary cannot silently collapse onto its coarse tick."""
    graph, profile, clock = _refined_clock()
    with pytest.raises(ValueError, match=r"base boundary 2.*requires scale"):
        to_textgrid(graph, profile, clock=clock)


def test_scale_refuses_a_gap_outside_its_radix() -> None:
    """The scale must assign every ordered gap a distinct integer coordinate."""
    graph, profile, clock = _refined_clock()
    with pytest.raises(ValueError, match=r"base boundary 2.*does not fit scale 1"):
        to_textgrid(graph, profile, clock=clock, scale=1)
    assert "xmax = 4 " in to_textgrid(graph, profile, clock=clock, scale=2)


def test_profile_new_keys_are_individually_optional_and_unknown_keys_refuse() -> None:
    """Old profile documents decode while each additive field can stand alone."""
    result = from_textgrid(_fixture("reference-long.TextGrid"))
    profile = result.profile
    old = {
        "base_tier": profile.base_tier.to_data(),
        "span_tiers": [tier.to_data() for tier in profile.span_tiers],
        "coverage_relation": profile.coverage_relation.to_data(),
        "score_attribute": profile.score_attribute.to_data(),
        "value_attribute": profile.value_attribute.to_data(),
        "base_surface_attribute": profile.value_attribute.to_data(),
        "char_offset_attribute": None,
        "alternative_relation": None,
    }
    decoded = SpanViewProfile.from_data(old)
    assert decoded == replace(
        profile,
        base_surface_attribute=profile.value_attribute,
        point_tiers=(),
        point_coverage_relation=None,
    )
    assert profile.point_coverage_relation is not None
    additions: dict[str, object] = {
        "point_tiers": [],
        "point_coverage_relation": profile.point_coverage_relation.to_data(),
        "value_attributes": {
            str(profile.span_tiers[0]): profile.value_attribute.to_data()
        },
        "clock_face": "physical",
    }
    for key, value in additions.items():
        candidate = SpanViewProfile.from_data({**old, key: value})
        assert getattr(candidate, key) not in (None, ()) or value in ([], None)
    without_surface = dict(old)
    del without_surface["base_surface_attribute"]
    assert SpanViewProfile.from_data(without_surface).base_surface_attribute is None
    with pytest.raises(ValueError, match="span profile fields"):
        SpanViewProfile.from_data({**old, "unknown": None})


@pytest.mark.parametrize(
    ("document", "message"),
    (
        (object(), "must be str or bytes"),
        ('"x"', "two-line header"),
        ("x\ny\n", "unsupported form"),
        (
            'File type = "ooTextFile short"\n"TextGrid"\nx\n1\n<absent>\n',
            "not a decimal",
        ),
        (
            'File type = "ooTextFile short"\n"TextGrid"\nNaN\n1\n<absent>\n',
            "not finite",
        ),
        (
            'File type = "ooTextFile short"\n"TextGrid"\n2\n1\n<absent>\n',
            "goes backward",
        ),
        (
            'File type = "ooTextFile short"\n"TextGrid"\n0\n1\n<absent>\nx\n',
            "trailing values",
        ),
        ('File type = "ooTextFile short"\n"TextGrid"\n0\n1\n<maybe>\n', "presence"),
        (
            'File type = "ooTextFile short"\n"TextGrid"\n0\n1\n<exists>\nx\n',
            "nonnegative integer",
        ),
        (
            'File type = "ooTextFile short"\n"TextGrid"\n0\n1\n<exists>\n1\n"Other"\n',
            "unsupported",
        ),
        (
            'File type = "ooTextFile short"\n"TextGrid"\n0\n1\n<exists>\n1\nIntervalTier\n',
            "double-quoted",
        ),
        (
            'File type = "ooTextFile short"\n"TextGrid"\n0\n1\n<exists>\n1\n"IntervalTier"\n',
            "ends before",
        ),
    ),
)
def test_reader_refusals_name_malformed_grammar_values(
    document: object, message: str
) -> None:
    """Malformed near-prefixes stop at the TextGrid field that cannot be read."""
    with pytest.raises((TypeError, ValueError), match=message):
        from_textgrid(document)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("old", "new", "message"),
    (
        ("number = 1.2", "number = 3", "lies outside"),
        (
            "xmin = 0 \n            xmax = 0.5",
            "xmin = 0.5 \n            xmax = 0.5",
            "must be positive",
        ),
        (
            "xmin = 0.5 \n            xmax = 2.3",
            "xmin = 0.6 \n            xmax = 2.3",
            "previous end",
        ),
        (
            'xmax = 2.3 \n            text = ""',
            'xmax = 2 \n            text = ""',
            "not tier xmax",
        ),
    ),
)
def test_reader_refuses_invalid_entry_geometry(
    old: str, new: str, message: str
) -> None:
    """Intervals must tile and points must remain inside the declared extent."""
    document = _fixture("reference-long.TextGrid").decode().replace(old, new, 1)
    with pytest.raises(ValueError, match=message):
        from_textgrid(document)


def test_reader_refuses_duplicate_names_trailing_values_and_bad_units() -> None:
    """Graph identity and the out-of-band unit remain unambiguous."""
    duplicate = (
        _fixture("reference-long.TextGrid")
        .decode()
        .replace('name = "marks"', 'name = "words"')
    )
    with pytest.raises(ValueError, match="occurs more than once"):
        from_textgrid(duplicate)
    trailing = _fixture("reference-short.TextGrid") + b'\n"extra"\n'
    with pytest.raises(ValueError, match="trailing values"):
        from_textgrid(trailing)
    with pytest.raises(ValueError, match="non-empty string"):
        from_textgrid(_fixture("reference-long.TextGrid"), unit="")


def test_undoubled_inner_quote_refuses() -> None:
    """A TextGrid quote inside a string must use Praat's doubled spelling."""
    with pytest.raises(ValueError, match="undoubled quote"):
        textgrid_module._quoted('"a"b"', "label")


def test_containment_uses_exact_interval_bounds() -> None:
    """Earlier interval tiers contain later tier items only by numeric extent."""
    document = _fixture("integer-long.TextGrid").decode().replace(
        "size = 2",
        "size = 3",
        1,
    ) + (
        '    item [3]:\n        class = "IntervalTier" \n        name = "phones" \n'
        "        xmin = 0 \n        xmax = 3 \n        intervals: size = 3 \n"
        "        intervals [1]:\n            xmin = 0 \n            xmax = 0.5 \n"
        '            text = "h" \n        intervals [2]:\n            xmin = 0.5 \n'
        '            xmax = 1.0001 \n            text = "near" \n'
        "        intervals [3]:\n            xmin = 1.0001 \n            xmax = 3 \n"
        '            text = "rest" \n'
    )
    result = from_textgrid(document)
    edges = {
        (relation.left, relation.right)
        for relation in result.graph.relations
        if relation.declaration.local_name.startswith("containment-")
    }
    words, phones = result.profile.span_tiers
    assert edges == {
        (ItemRef(words, 0), ItemRef(phones, 0)),
        (ItemRef(words, 1), ItemRef(phones, 2)),
    }


def test_writer_fills_leading_and_trailing_uncovered_ranges() -> None:
    """An absent span is emitted as an empty-labeled TextGrid interval."""
    result = from_textgrid(_fixture("integer-long.TextGrid"))
    tier_name = result.profile.span_tiers[0]
    tier_position = next(
        index
        for index, tier in enumerate(result.graph.tiers)
        if tier.declaration.name == tier_name
    )
    tier = result.graph.tiers[tier_position]
    for kept, expected in ((0, "xmax = 3"), (1, "xmin = 0")):
        replacement = replace(tier, items=(tier.items[kept],))
        tiers = tuple(
            replacement if index == tier_position else member
            for index, member in enumerate(result.graph.tiers)
        )
        relations = tuple(
            replace(relation, right=ItemRef(tier_name, 0))
            if isinstance(relation.right, ItemRef)
            and relation.right.tier == tier_name
            and relation.right.index == kept
            else relation
            for relation in result.graph.relations
            if not (
                isinstance(relation.right, ItemRef)
                and relation.right.tier == tier_name
                and relation.right.index != kept
            )
        )
        rendered = to_textgrid(
            replace(result.graph, tiers=tiers, relations=relations), result.profile
        )
        assert expected in rendered


def test_writer_refuses_invalid_scale_contexts_and_coordinate_counts() -> None:
    """Scale has a positive-integer radix and requires refined clock data."""
    result = from_textgrid(_fixture("integer-long.TextGrid"))
    for scale in (0, True, 1.5):
        with pytest.raises(ValueError, match="positive integer"):
            to_textgrid(result.graph, result.profile, scale=scale)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="requires a clock"):
        to_textgrid(result.graph, result.profile, scale=2)
    short_clock = SimpleNamespace(
        clock_tier=result.profile.base_tier,
        coordinates=result.clock.coordinates[:-1],
    )
    with pytest.raises(ValueError, match="coordinates for"):
        to_textgrid(
            result.graph,
            result.profile,
            clock=short_clock,  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="applies only"):
        to_textgrid(
            result.graph,
            replace(result.profile, clock_face="physical"),
            clock=result.clock,
            scale=2,
        )
    assert "xmax = 3 " in to_textgrid(result.graph, result.profile, clock=result.clock)


def test_physical_writer_refuses_missing_discontinuous_and_empty_timing() -> None:
    """Physical base timing must exist, be contiguous, and define an extent."""
    result = from_textgrid(_fixture("integer-long.TextGrid"))
    missing = SimpleNamespace(timing=lambda _tier, _index: None)
    with pytest.raises(ValueError, match="has no physical timing"):
        textgrid_module._physical_coordinates(
            result.graph,
            result.profile,
            missing,  # type: ignore[arg-type]
        )
    timings = iter(
        (
            PhysicalTiming(Decimal("0"), Decimal("1"), "s"),
            PhysicalTiming(Decimal("2"), Decimal("1"), "s"),
        )
    )
    discontinuous = SimpleNamespace(timing=lambda _tier, _index: next(timings))
    with pytest.raises(ValueError, match="not previous end"):
        textgrid_module._physical_coordinates(
            result.graph,
            result.profile,
            discontinuous,  # type: ignore[arg-type]
        )
    base_position = next(
        index
        for index, tier in enumerate(result.graph.tiers)
        if tier.declaration.name == result.profile.base_tier
    )
    base = replace(result.graph.tiers[base_position], items=())
    empty_graph = Graph(
        result.graph.namespaces,
        (base,),
        tuple(
            declaration
            for declaration in result.graph.relation_declarations
            if isinstance(declaration, SimpleRelationDeclaration)
            and declaration.tier == result.profile.base_tier
        ),
        attribute_declarations=result.graph.attribute_declarations,
        attributes=result.graph.attributes,
    )
    with pytest.raises(ValueError, match="empty base tier"):
        textgrid_module._physical_coordinates(empty_graph, result.profile, result.clock)


def test_profile_construction_and_new_field_shapes_refuse_offenders() -> None:
    """New profile roles reject ambiguity before graph traversal."""
    result = from_textgrid(_fixture("reference-long.TextGrid"))
    tier = result.profile.span_tiers[0]
    attribute = result.profile.value_attribute
    with pytest.raises(ValueError, match="both a span tier and a point tier"):
        replace(result.profile, point_tiers=(tier,))
    with pytest.raises(ValueError, match="point_coverage_relation is required"):
        replace(
            result.profile,
            span_tiers=(),
            point_tiers=(tier,),
            point_coverage_relation=None,
        )
    with pytest.raises(ValueError, match="clock_face"):
        replace(result.profile, clock_face="other")
    with pytest.raises(ValueError, match="more than once"):
        replace(result.profile, value_attributes=((tier, attribute), (tier, attribute)))
    old: dict[str, object] = {
        "base_tier": result.profile.base_tier.to_data(),
        "span_tiers": [],
        "coverage_relation": result.profile.coverage_relation.to_data(),
        "score_attribute": result.profile.score_attribute.to_data(),
        "value_attribute": attribute.to_data(),
        "char_offset_attribute": None,
        "alternative_relation": None,
    }
    for update, message in (
        ({"point_tiers": {}}, "point_tiers must be a list"),
        ({"value_attributes": []}, "value_attributes must be an object"),
        ({"value_attributes": {1: attribute.to_data()}}, "keys must be strings"),
        ({"value_attributes": {"bad": attribute.to_data()}}, "must use"),
        ({"clock_face": []}, "clock_face must be a string"),
    ):
        with pytest.raises(ValueError, match=message):
            SpanViewProfile.from_data({**old, **update})
