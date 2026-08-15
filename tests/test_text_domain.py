"""Test text encoding with independently overlapping structural hierarchies."""

from __future__ import annotations

from collections.abc import Iterable
from itertools import combinations, product

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
from tiergraph.semiring import COUNTING

NS = "urn:tiergraph:profile:text:test"


def name(local: str) -> QualifiedName:
    """Build a name in the fixture namespace."""
    return QualifiedName(NS, local)


TEXT = name("text")
PAGE = name("page")
SENTENCE = name("sentence")
VERSE = name("verse")
TEXT_TYPE = name("text-unit")
SPAN_TYPE = name("span")
TEXT_MEMBERS = name("text-members")
PAGE_MEMBERS = name("page-members")
SENTENCE_MEMBERS = name("sentence-members")
VERSE_MEMBERS = name("verse-members")
COVERED_BY = name("covered-by")
COUNT = name("count")

# These are boundary atoms, not stored coordinates.  Their order is the text tier's
# declared order; relations below hold every structural membership directly.
TEXT_IDS = (
    "11-1.1.66",
    "11-1.1.67",
    "11-1.1.68",
    "13-1.1.69",
    "13-1.1.70",
    "17-1.1.164a",
    "17-1.1.164b",
    "19-1.1.165",
    "55-1.5.1-2",
    "55-1.5.3",
    "57-1.5.4",
    "57-1.5.5",
    "57-1.5.6",
)

# Half-open ranges are used only to construct the fixture and state its independent
# oracle.  The graph stores the expanded membership edges, never these coordinates.
PAGE_RANGES = ((0, 3), (3, 5), (5, 7), (7, 8), (8, 10), (10, 13))
SENTENCE_RANGES = (
    (0, 3),
    (3, 4),
    (4, 5),
    (5, 6),
    (6, 8),
    (8, 9),
    (9, 10),
    (10, 11),
    (11, 13),
)
VERSE_RANGES = (
    (0, 1),
    (1, 2),
    (2, 4),
    (4, 5),
    (5, 7),
    (7, 8),
    (8, 9),
    (9, 12),
    (12, 13),
)


def _items(prefix: str, size: int) -> tuple[Item, ...]:
    """Construct counted domain items with durable mnemonic labels."""
    return tuple(
        Item(
            f"{prefix}-{index}",
            (AttributeValue(COUNT, XsdType.INTEGER, "1"),),
        )
        for index in range(size)
    )


def _coverage(
    tier: QualifiedName, ranges: tuple[tuple[int, int], ...]
) -> Iterable[RelationInstance]:
    """Expand one author-constructed hierarchy as graph membership edges."""
    for span_index, (start, stop) in enumerate(ranges):
        for text_index in range(start, stop):
            yield RelationInstance(
                COVERED_BY,
                ItemRef(TEXT, text_index),
                ItemRef(tier, span_index),
            )


def fixture(
    page_ranges: tuple[tuple[int, int], ...] = PAGE_RANGES,
    sentence_ranges: tuple[tuple[int, int], ...] = SENTENCE_RANGES,
    verse_ranges: tuple[tuple[int, int], ...] = VERSE_RANGES,
) -> Graph:
    """Build an author-constructed witness for overlapping text structures."""
    tiers = (
        Tier(
            TierDeclaration(TEXT, "Boundary atoms"),
            tuple(
                Item(
                    durable_id,
                    (AttributeValue(COUNT, XsdType.INTEGER, "1"),),
                )
                for durable_id in TEXT_IDS
            ),
        ),
        Tier(TierDeclaration(PAGE, "Print pages"), _items("page", len(page_ranges))),
        Tier(
            TierDeclaration(SENTENCE, "Sentences"),
            _items("sentence", len(sentence_ranges)),
        ),
        Tier(TierDeclaration(VERSE, "Verse lines"), _items("verse", len(verse_ranges))),
    )
    declarations = (
        SimpleRelationDeclaration(TEXT_MEMBERS, TEXT, TEXT_TYPE),
        SimpleRelationDeclaration(PAGE_MEMBERS, PAGE, SPAN_TYPE),
        SimpleRelationDeclaration(SENTENCE_MEMBERS, SENTENCE, SPAN_TYPE),
        SimpleRelationDeclaration(VERSE_MEMBERS, VERSE, SPAN_TYPE),
        BipartiteRelationDeclaration(
            COVERED_BY,
            TEXT_TYPE,
            SPAN_TYPE,
            acyclic=True,
        ),
    )
    relations = tuple(
        relation
        for tier, ranges in (
            (PAGE, page_ranges),
            (SENTENCE, sentence_ranges),
            (VERSE, verse_ranges),
        )
        for relation in _coverage(tier, ranges)
    )
    return Graph(
        (NamespaceDeclaration("text", NS),),
        tiers,
        declarations,
        relations,
        (AttributeDeclaration(COUNT, AttributeDomain.ITEM, XsdType.INTEGER),),
    )


def _span_members(graph: Graph, tier: QualifiedName) -> tuple[frozenset[int], ...]:
    """Read a hierarchy's text membership back from graph relations."""
    span_count = next(
        len(candidate.items)
        for candidate in graph.tiers
        if candidate.declaration.name == tier
    )
    members = [set[int]() for _ in range(span_count)]
    for relation in graph.relations:
        if (
            relation.declaration == COVERED_BY
            and isinstance(relation.left, ItemRef)
            and isinstance(relation.right, ItemRef)
            and relation.left.tier == TEXT
            and relation.right.tier == tier
        ):
            members[relation.right.index].add(relation.left.index)
    return tuple(frozenset(span) for span in members)


def _crosses(left: frozenset[int], right: frozenset[int]) -> bool:
    """Return whether two graph-read memberships overlap without containment."""
    return bool(left & right) and not left <= right and not right <= left


def _crossing_pairs(
    graph: Graph,
) -> set[tuple[QualifiedName, int, QualifiedName, int]]:
    """Return every cross-hierarchy span pair that crosses in the composite."""
    pairs: set[tuple[QualifiedName, int, QualifiedName, int]] = set()
    for left_tier, right_tier in combinations((PAGE, SENTENCE, VERSE), 2):
        for (left_index, left), (right_index, right) in product(
            enumerate(_span_members(graph, left_tier)),
            enumerate(_span_members(graph, right_tier)),
        ):
            if _crosses(left, right):
                pairs.add((left_tier, left_index, right_tier, right_index))
    return pairs


def _crossing_count_fold(graph: Graph) -> int:
    """Fold graph-read crossing indicators with the counting semiring's addition."""
    result = COUNTING.zero
    for _pair in _crossing_pairs(graph):
        result = COUNTING.add(result, COUNTING.one)
    return result


def test_composite_memberships_exhibit_all_authored_crossings() -> None:
    """Crossings are asserted on memberships read from the graph, not range tuples."""
    graph = fixture()
    assert _crossing_pairs(graph) == {
        (PAGE, 2, SENTENCE, 4),
        (PAGE, 0, VERSE, 2),
        (PAGE, 1, VERSE, 2),
        (PAGE, 4, VERSE, 7),
        (PAGE, 5, VERSE, 7),
        (SENTENCE, 0, VERSE, 2),
        (SENTENCE, 4, VERSE, 4),
        (SENTENCE, 8, VERSE, 7),
    }
    assert _span_members(graph, VERSE)[2] == frozenset((2, 3))
    assert _span_members(graph, PAGE)[0] == frozenset((0, 1, 2))
    assert _span_members(graph, PAGE)[1] == frozenset((3, 4))


def test_crossing_fold_distinguishes_overlapping_from_nested_memberships() -> None:
    """Eight hand-enumerated crossings disappear when all partitions coincide."""
    overlapping = fixture()
    nested = fixture(PAGE_RANGES, PAGE_RANGES, PAGE_RANGES)

    # Overlapping: page/sentence 1 + page/verse 4 + sentence/verse 3 = 8.
    assert _crossing_count_fold(overlapping) == 1 + 4 + 3 == 8
    # Nested: the three hierarchies share six extents, so no pair partially overlaps.
    assert _crossing_count_fold(nested) == 0
