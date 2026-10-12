"""Canonical paths bind and resolve without becoming graph identity."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import cast

import pytest

from tiergraph import (
    BoundaryBinding,
    BoundaryRef,
    BoundarySide,
    CanonicalPath,
    DurableBoundaryRef,
    DurableItemRef,
    Graph,
    Item,
    ItemBinding,
    ItemRef,
    NamespaceDeclaration,
    PathBinding,
    PathKind,
    PathOffender,
    PathRefusal,
    PathRefusalCode,
    QualifiedName,
    ResolvedBoundary,
    ResolvedItem,
    StructuralPathProfile,
    Tier,
    TierDeclaration,
    parse_path_index_segment,
    resolve_path,
)
from tiergraph.path import parse_path_index_segment as path_module_index_parser

NS = "urn:path"
TIER = QualifiedName(NS, "tokens")
MISSING_TIER = QualifiedName(NS, "missing")
PROFILE = StructuralPathProfile()


def graph_with(*items: Item) -> Graph:
    """Build the small ordered graph used by path resolution tests."""
    return Graph(
        (NamespaceDeclaration("p", NS),),
        (Tier(TierDeclaration(TIER, "Tokens"), items),),
        (),
    )


GRAPH = graph_with(Item("alpha"), Item("beta"))


def test_path_index_parser_has_one_public_identity() -> None:
    """The submodule and package exports publish the same implementation."""
    assert parse_path_index_segment is path_module_index_parser


@pytest.mark.parametrize(
    ("segment", "expected"),
    [("0", 0), ("7", 7), ("123456789", 123456789)],
)
def test_path_index_parser_accepts_canonical_ascii_decimals(
    segment: str, expected: int
) -> None:
    """Zero and positive single- and multi-digit indices retain their value."""
    path = CanonicalPath(("prefix", segment, "suffix"))
    assert parse_path_index_segment(segment) == expected
    assert parse_path_index_segment(segment, path=path, segment_index=1) == expected


@pytest.mark.parametrize("contextual", (False, True), ids=("standalone", "contextual"))
@pytest.mark.parametrize(
    ("segment", "code"),
    [
        ("00", PathRefusalCode.NONCANONICAL_SEGMENT),
        ("01", PathRefusalCode.NONCANONICAL_SEGMENT),
        ("+0", PathRefusalCode.NONCANONICAL_SEGMENT),
        ("+12", PathRefusalCode.NONCANONICAL_SEGMENT),
        ("+01", PathRefusalCode.NONCANONICAL_SEGMENT),
        ("١٢", PathRefusalCode.NONCANONICAL_SEGMENT),
        ("१२", PathRefusalCode.NONCANONICAL_SEGMENT),
        ("１２", PathRefusalCode.NONCANONICAL_SEGMENT),
        ("1٢", PathRefusalCode.NONCANONICAL_SEGMENT),
        ("१2", PathRefusalCode.NONCANONICAL_SEGMENT),
        ("+١٢", PathRefusalCode.NONCANONICAL_SEGMENT),
        ("", PathRefusalCode.INVALID_SEGMENT),
        ("+", PathRefusalCode.INVALID_SEGMENT),
        ("-", PathRefusalCode.INVALID_SEGMENT),
        ("-1", PathRefusalCode.INVALID_SEGMENT),
        ("++1", PathRefusalCode.INVALID_SEGMENT),
        ("--1", PathRefusalCode.INVALID_SEGMENT),
        ("＋1", PathRefusalCode.INVALID_SEGMENT),
        ("－1", PathRefusalCode.INVALID_SEGMENT),
        (" 1", PathRefusalCode.INVALID_SEGMENT),
        ("1 ", PathRefusalCode.INVALID_SEGMENT),
        ("\t1", PathRefusalCode.INVALID_SEGMENT),
        ("1\n", PathRefusalCode.INVALID_SEGMENT),
        ("\x001", PathRefusalCode.INVALID_SEGMENT),
        ("one", PathRefusalCode.INVALID_SEGMENT),
        ("1.0", PathRefusalCode.INVALID_SEGMENT),
        ("²", PathRefusalCode.INVALID_SEGMENT),
    ],
)
def test_path_index_parser_classifies_every_lexical_family(
    segment: str, code: PathRefusalCode, contextual: bool
) -> None:
    """Alias-like decimal forms and invalid forms have exact refusal data."""
    if contextual:
        path = CanonicalPath(("prefix", segment, "suffix"))
        offender = PathOffender(
            text=str(path), path=path, segment_index=1, segment=segment
        )
        with pytest.raises(PathRefusal) as caught:
            parse_path_index_segment(segment, path=path, segment_index=1)
    else:
        offender = PathOffender(text="", path=None, segment_index=None, segment=segment)
        with pytest.raises(PathRefusal) as caught:
            parse_path_index_segment(segment)
    assert caught.value.code is code
    assert caught.value.offender == offender
    assert caught.value.cause is None
    assert caught.value.__cause__ is None


def test_path_index_parser_requires_complete_consistent_context() -> None:
    """Context is paired and identifies the supplied segment in the path."""
    path = CanonicalPath(("7",))
    with pytest.raises(TypeError, match="provided together"):
        parse_path_index_segment("7", path=path)
    with pytest.raises(TypeError, match="provided together"):
        parse_path_index_segment("7", segment_index=0)
    with pytest.raises(ValueError, match="does not match"):
        parse_path_index_segment("8", path=path, segment_index=0)
    with pytest.raises(ValueError, match="does not match"):
        parse_path_index_segment("7", path=path, segment_index=1)


def test_path_index_parser_refuses_past_the_active_integer_string_limit() -> None:
    """An over-limit decimal is typed before direct or structural conversion."""
    original_limit = sys.get_int_max_str_digits()
    try:
        test_limit = sys.int_info.str_digits_check_threshold
        sys.set_int_max_str_digits(test_limit)
        assert parse_path_index_segment("1" * test_limit) > 0
        segment = "1" * (test_limit + 1)
        path = CanonicalPath(("items", "structural", NS, "tokens", segment))

        with pytest.raises(PathRefusal) as standalone:
            parse_path_index_segment(segment)
        with pytest.raises(PathRefusal) as contextual:
            parse_path_index_segment(segment, path=path, segment_index=4)
        with pytest.raises(PathRefusal) as resolved:
            resolve_path(GRAPH, PROFILE, str(path))

        assert standalone.value.code is PathRefusalCode.INVALID_SEGMENT
        assert standalone.value.offender == PathOffender(text="", segment=segment)
        assert contextual.value.code is PathRefusalCode.INVALID_SEGMENT
        assert (
            contextual.value.offender
            == resolved.value.offender
            == PathOffender(text=str(path), path=path, segment_index=4, segment=segment)
        )
        assert standalone.value.cause is None
        assert contextual.value.cause is None
        assert resolved.value.cause is None
    finally:
        sys.set_int_max_str_digits(original_limit)


@pytest.mark.parametrize(
    ("template", "segment_index"),
    [
        ("/items/structural/urn:path/tokens/{segment}", 4),
        ("/positions/structural/urn:path/tokens/{segment}", 4),
    ],
    ids=("item", "boundary"),
)
@pytest.mark.parametrize(
    ("segment", "code"),
    [
        ("+1", PathRefusalCode.NONCANONICAL_SEGMENT),
        ("one", PathRefusalCode.INVALID_SEGMENT),
    ],
)
def test_structural_slots_match_direct_index_refusals(
    template: str,
    segment_index: int,
    segment: str,
    code: PathRefusalCode,
) -> None:
    """Structural item and boundary slots preserve direct parser refusals."""
    text = template.format(segment=segment)
    path = CanonicalPath.parse(text)
    with pytest.raises(PathRefusal) as direct:
        parse_path_index_segment(segment, path=path, segment_index=segment_index)
    with pytest.raises(PathRefusal) as resolved:
        resolve_path(GRAPH, PROFILE, text)
    assert direct.value.code is resolved.value.code is code
    assert (
        direct.value.offender
        == resolved.value.offender
        == PathOffender(
            text=text,
            path=path,
            segment_index=segment_index,
            segment=segment,
        )
    )
    assert direct.value.cause is resolved.value.cause is None


@pytest.mark.parametrize(
    ("text", "segments"),
    [
        ("", ()),
        ("/", ("",)),
        ("/a//b", ("a", "", "b")),
        ("/~0/~1/~01", ("~", "/", "~1")),
        ("/snowman-☃/%2F/ A ", ("snowman-☃", "%2F", " A ")),
    ],
)
def test_canonical_pointer_round_trip(text: str, segments: tuple[str, ...]) -> None:
    """Parsing is one-pass and spelling preserves every admitted code point.

    The empty string is the RFC 6901 root pointer (zero tokens), distinct from
    "/" (one empty token); both round-trip.
    """
    path = CanonicalPath.parse(text)
    assert path.segments == segments
    assert str(path) == text
    assert CanonicalPath.parse(str(path)) == path


@pytest.mark.parametrize("text", ["plain", "#/~0", "/trailing~", "/bad~2"])
def test_malformed_pointer_is_strictly_refused(text: str) -> None:
    """Non-pointers, fragments, and every illegal tilde escape are malformed."""
    with pytest.raises(PathRefusal) as caught:
        CanonicalPath.parse(text)
    assert caught.value.code is PathRefusalCode.MALFORMED_POINTER
    assert caught.value.offender.text == text


@pytest.mark.parametrize(
    ("binding", "text"),
    [
        (ItemBinding(ItemRef(TIER, 1)), "/items/structural/urn:path/tokens/1"),
        (ItemBinding(DurableItemRef("a/b~c")), "/items/durable/a~1b~0c"),
        (
            BoundaryBinding(BoundaryRef(TIER, 2)),
            "/positions/structural/urn:path/tokens/2",
        ),
        (
            BoundaryBinding(
                DurableBoundaryRef(DurableItemRef("beta"), BoundarySide.BEFORE)
            ),
            "/positions/durable/item/beta/before",
        ),
        (
            BoundaryBinding(DurableBoundaryRef(TIER, BoundarySide.AFTER)),
            "/positions/durable/tier/urn:path/tokens/after",
        ),
    ],
)
def test_structural_profile_spells_and_binds_every_form(
    binding: PathBinding, text: str
) -> None:
    """The generic vocabulary round-trips every supported reference shape."""
    path = PROFILE.spell(binding, GRAPH)
    assert str(path) == text
    assert PROFILE.bind(path, GRAPH) == binding


def test_item_and_position_resolution_call_existing_graph_semantics() -> None:
    """Both structural and durable forms resolve to current kernel coordinates."""
    item = resolve_path(GRAPH, PROFILE, "/items/durable/beta")
    boundary = resolve_path(GRAPH, PROFILE, "/positions/durable/item/beta/after")
    assert item == ResolvedItem(
        CanonicalPath(("items", "durable", "beta")), ItemRef(TIER, 1)
    )
    assert boundary == ResolvedBoundary(
        CanonicalPath(("positions", "durable", "item", "beta", "after")),
        BoundaryRef(TIER, 2),
    )


def test_durable_item_follows_identity_after_insert_but_structural_is_occupant() -> (
    None
):
    """Insertion moves durable identity while a structural selector stays put."""
    inserted = graph_with(Item("new"), Item("alpha"), Item("beta"))
    durable = resolve_path(inserted, PROFILE, "/items/durable/beta")
    structural = resolve_path(inserted, PROFILE, "/items/structural/urn:path/tokens/1")
    assert isinstance(durable, ResolvedItem)
    assert isinstance(structural, ResolvedItem)
    assert durable.current == ItemRef(TIER, 2)
    assert structural.current == ItemRef(TIER, 1)
    # Discriminating occupant check: index 1 now holds "alpha" (shifted by the
    # insert), distinct from "beta" which the durable id followed to index 2.
    alpha = resolve_path(inserted, PROFILE, "/items/durable/alpha")
    assert isinstance(alpha, ResolvedItem)
    assert alpha.current == ItemRef(TIER, 1)


@pytest.mark.parametrize(
    ("text", "require", "actual"),
    [
        # Inputs whose graph lookup WOULD fail (absent durable id; out-of-range
        # index): a WRONG_KIND with no chained cause proves the kind check runs
        # BEFORE the lookup, not after it.
        ("/items/durable/absent", PathKind.BOUNDARY, PathKind.ITEM),
        (
            "/positions/structural/urn:path/tokens/9",
            PathKind.ITEM,
            PathKind.BOUNDARY,
        ),
    ],
)
def test_required_kind_is_checked_before_lookup(
    text: str, require: PathKind, actual: PathKind
) -> None:
    """Slot legality refuses the opposite kind without attempting the lookup."""
    with pytest.raises(PathRefusal) as caught:
        resolve_path(GRAPH, PROFILE, text, require=require)
    assert caught.value.code is PathRefusalCode.WRONG_KIND
    assert caught.value.offender.expected_kind is require
    assert caught.value.offender.actual_kind is actual
    assert caught.value.cause is None


RESOLVER_REFUSALS: tuple[tuple[str, PathRefusalCode], ...] = (
    ("/unknown", PathRefusalCode.UNKNOWN_FORM),
    ("", PathRefusalCode.UNKNOWN_FORM),
    (
        "/items/structural/urn:path/tokens/01",
        PathRefusalCode.NONCANONICAL_SEGMENT,
    ),
    (
        "/items/structural/urn:path/tokens/+1",
        PathRefusalCode.NONCANONICAL_SEGMENT,
    ),
    (
        "/items/structural/urn:path/tokens/１",
        PathRefusalCode.NONCANONICAL_SEGMENT,
    ),
    (
        "/items/structural/urn:path/tokens/-1",
        PathRefusalCode.INVALID_SEGMENT,
    ),
    (
        "/items/structural/urn:path/missing/0",
        PathRefusalCode.UNKNOWN_TIER,
    ),
    (
        "/items/structural/urn:path/tokens/9",
        PathRefusalCode.OUT_OF_RANGE,
    ),
    ("/items/durable/absent", PathRefusalCode.UNKNOWN_DURABLE_ITEM),
    (
        "/positions/durable/item/absent/before",
        PathRefusalCode.UNKNOWN_DURABLE_ANCHOR,
    ),
)


@pytest.mark.parametrize(("text", "code"), RESOLVER_REFUSALS)
def test_each_generic_refusal_class_has_a_discriminating_input(
    text: str, code: PathRefusalCode
) -> None:
    """Each input selects its intended refusal class and retains its cause.

    "Each class" is `PathRefusalCode` less two named remainders rather than the
    codes someone wrote down, so a class added to the vocabulary is either
    reached by an input here or classified, and otherwise fails.  The reserved
    code is the one the enum's own docstring says no resolver produces; the
    rest are answered by the spelling, kind, and alternation tests around this
    one, which reach them through calls this resolver walk cannot make.
    """
    reserved = {PathRefusalCode.BOUNDARY_NOT_IN_PARENT}
    covered_elsewhere = {
        PathRefusalCode.MALFORMED_POINTER,
        PathRefusalCode.WRONG_KIND,
        PathRefusalCode.UNSPELLABLE,
        PathRefusalCode.PROFILE_REFUSED,
        PathRefusalCode.ALTERNATIVE_OUT_OF_RANGE,
    }
    assert {declared for _, declared in RESOLVER_REFUSALS} == (
        set(PathRefusalCode) - reserved - covered_elsewhere
    )
    with pytest.raises(PathRefusal) as caught:
        resolve_path(GRAPH, PROFILE, text)
    assert caught.value.code is code
    if code in {
        PathRefusalCode.UNKNOWN_TIER,
        PathRefusalCode.OUT_OF_RANGE,
        PathRefusalCode.UNKNOWN_DURABLE_ITEM,
        PathRefusalCode.UNKNOWN_DURABLE_ANCHOR,
    }:
        assert isinstance(caught.value.cause, ValueError)
        assert caught.value.__cause__ is caught.value.cause
    else:
        assert caught.value.cause is None


@pytest.mark.parametrize(
    "text",
    [
        "/items/structural//tokens/0",
        "/items/durable/",
        "/positions/durable/item/beta/middle",
        "/positions/structural/urn:path/tokens/one",
    ],
)
def test_profile_segment_domains_are_explicit(text: str) -> None:
    """Empty names, unknown sides, and nonnumeric indices are invalid segments."""
    with pytest.raises(PathRefusal) as caught:
        resolve_path(GRAPH, PROFILE, text)
    assert caught.value.code is PathRefusalCode.INVALID_SEGMENT


@pytest.mark.parametrize(
    ("text", "segment_index"),
    [
        ("/items/structural//tokens/0", 2),
        ("/items/structural/urn:path//0", 3),
        ("/positions/durable/tier//tokens/before", 3),
        ("/positions/durable/tier/urn:path//before", 4),
    ],
)
def test_empty_tier_segment_reports_its_own_index(
    text: str, segment_index: int
) -> None:
    """Offender data names the failing tier segment; the durable-tier form sits
    one segment deeper than the structural form, so their indices differ."""
    with pytest.raises(PathRefusal) as caught:
        resolve_path(GRAPH, PROFILE, text)
    assert caught.value.code is PathRefusalCode.INVALID_SEGMENT
    assert caught.value.offender.segment_index == segment_index


def test_position_range_and_tier_anchor_failures_are_typed() -> None:
    """Boundary lookup distinguishes range failure from an unknown tier anchor."""
    cases = (
        (
            "/positions/structural/urn:path/tokens/3",
            PathRefusalCode.OUT_OF_RANGE,
        ),
        (
            "/positions/structural/urn:path/missing/0",
            PathRefusalCode.UNKNOWN_TIER,
        ),
        (
            "/positions/durable/tier/urn:path/missing/before",
            PathRefusalCode.UNKNOWN_TIER,
        ),
    )
    for text, code in cases:
        with pytest.raises(PathRefusal) as caught:
            resolve_path(GRAPH, PROFILE, text)
        assert caught.value.code is code
        assert isinstance(caught.value.cause, ValueError)


@dataclass(frozen=True)
class RefusingProfile:
    """Supply profile-owned refusal states for extension-point tests."""

    code: PathRefusalCode

    def bind(self, path: CanonicalPath, graph: Graph) -> PathBinding:
        """Refuse with the requested profile-owned classification."""
        del graph
        raise PathRefusal(
            self.code,
            PathOffender(
                text=str(path), path=path, profile_reason="test_profile_reason"
            ),
        )

    def spell(self, binding: PathBinding, graph: Graph) -> CanonicalPath:
        """Reject spelling because this profile exists only to refuse binding."""
        del binding, graph
        raise AssertionError("not reached")

    def alternatives(
        self, owner: ItemRef, relation: QualifiedName, graph: Graph
    ) -> tuple[object, ...]:
        """Exist only to satisfy the profile protocol."""
        del owner, relation, graph
        raise AssertionError("not reached")


@pytest.mark.parametrize(
    "code",
    [PathRefusalCode.BOUNDARY_NOT_IN_PARENT, PathRefusalCode.PROFILE_REFUSED],
)
def test_profile_owned_refusals_preserve_stable_reason(code: PathRefusalCode) -> None:
    """Profiles can preserve distinctions not inferred by generic resolution."""
    with pytest.raises(PathRefusal) as caught:
        resolve_path(GRAPH, RefusingProfile(code), "/profile")
    assert caught.value.code is code
    assert caught.value.offender.profile_reason == "test_profile_reason"


def test_unspellable_reference_is_typed() -> None:
    """A runtime-invalid binding cannot acquire a fabricated path spelling."""
    invalid = ItemBinding(cast(ItemRef, object()))
    with pytest.raises(PathRefusal) as caught:
        PROFILE.spell(invalid, GRAPH)
    assert caught.value.code is PathRefusalCode.UNSPELLABLE
    assert caught.value.offender.profile_reason == "unsupported_reference"


@pytest.mark.parametrize(
    ("binding", "code", "reason"),
    [
        (
            ItemBinding(cast(ItemRef, object())),
            PathRefusalCode.PROFILE_REFUSED,
            "invalid_item_reference",
        ),
        (
            BoundaryBinding(cast(BoundaryRef, object())),
            PathRefusalCode.PROFILE_REFUSED,
            "invalid_boundary_reference",
        ),
    ],
)
def test_resolver_type_errors_are_preserved(
    binding: PathBinding, code: PathRefusalCode, reason: str | None
) -> None:
    """Unexpected profile reference objects retain the kernel TypeError cause."""

    @dataclass(frozen=True)
    class InvalidProfile:
        """Return one deliberately runtime-invalid binding."""

        def bind(self, path: CanonicalPath, graph: Graph) -> PathBinding:
            """Return the injected binding independently of path and graph."""
            del path, graph
            return binding

        def spell(self, binding: PathBinding, graph: Graph) -> CanonicalPath:
            """Exist only to satisfy the profile protocol."""
            del binding, graph
            raise AssertionError("not reached")

        def alternatives(
            self, owner: ItemRef, relation: QualifiedName, graph: Graph
        ) -> tuple[object, ...]:
            """Exist only to satisfy the profile protocol."""
            del owner, relation, graph
            raise AssertionError("not reached")

    with pytest.raises(PathRefusal) as caught:
        resolve_path(GRAPH, InvalidProfile(), "/invalid")
    assert caught.value.code is code
    assert caught.value.offender.profile_reason is reason
    assert isinstance(caught.value.cause, TypeError)
