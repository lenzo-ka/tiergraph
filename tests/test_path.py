"""Canonical paths bind and resolve without becoming graph identity."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import pytest

from tiergraph import (
    BoundarySide,
    CanonicalPath,
    DurableItemRef,
    DurablePositionRef,
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
    PositionBinding,
    PositionRef,
    QualifiedName,
    ResolvedItem,
    ResolvedPosition,
    StructuralPathProfile,
    Tier,
    TierDeclaration,
    resolve_path,
)

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
            PositionBinding(PositionRef(TIER, 2)),
            "/positions/structural/urn:path/tokens/2",
        ),
        (
            PositionBinding(
                DurablePositionRef(DurableItemRef("beta"), BoundarySide.BEFORE)
            ),
            "/positions/durable/item/beta/before",
        ),
        (
            PositionBinding(DurablePositionRef(TIER, BoundarySide.AFTER)),
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
    position = resolve_path(GRAPH, PROFILE, "/positions/durable/item/beta/after")
    assert item == ResolvedItem(
        CanonicalPath(("items", "durable", "beta")), ItemRef(TIER, 1)
    )
    assert position == ResolvedPosition(
        CanonicalPath(("positions", "durable", "item", "beta", "after")),
        PositionRef(TIER, 2),
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
        ("/items/durable/absent", PathKind.POSITION, PathKind.ITEM),
        (
            "/positions/structural/urn:path/tokens/9",
            PathKind.ITEM,
            PathKind.POSITION,
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


@pytest.mark.parametrize(
    ("text", "code"),
    [
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
    ],
)
def test_each_generic_refusal_class_has_a_discriminating_input(
    text: str, code: PathRefusalCode
) -> None:
    """Each input selects its intended refusal class and retains its cause."""
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
    """Position lookup distinguishes range failure from an unknown tier anchor."""
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
    [PathRefusalCode.POSITION_NOT_IN_PARENT, PathRefusalCode.PROFILE_REFUSED],
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
            PositionBinding(cast(PositionRef, object())),
            PathRefusalCode.PROFILE_REFUSED,
            "invalid_position_reference",
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
