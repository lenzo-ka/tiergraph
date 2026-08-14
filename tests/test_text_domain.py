"""Test text encoding with independently overlapping structural hierarchies."""

from __future__ import annotations

from collections.abc import Iterable

from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValuation,
    AttributeValue,
    BipartiteRelationDeclaration,
    ChildCombination,
    FoldDeclaration,
    FoldTransition,
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
    """Construct counted domain items with durable source-facing labels."""
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
    """Expand one hierarchy's source-derived membership as graph edges."""
    for span_index, (start, stop) in enumerate(ranges):
        for text_index in range(start, stop):
            yield RelationInstance(
                COVERED_BY,
                ItemRef(TEXT, text_index),
                ItemRef(tier, span_index),
            )


def fixture() -> Graph:
    """Build two ordered Hamlet windows around real Folger page crossings."""
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
        Tier(TierDeclaration(PAGE, "Print pages"), _items("page", 6)),
        Tier(TierDeclaration(SENTENCE, "Sentences"), _items("sentence", 9)),
        Tier(TierDeclaration(VERSE, "Verse lines"), _items("verse", 9)),
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
            (PAGE, PAGE_RANGES),
            (SENTENCE, SENTENCE_RANGES),
            (VERSE, VERSE_RANGES),
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


def _crosses(left: tuple[int, int], right: tuple[int, int]) -> bool:
    """Return whether two half-open ranges overlap without containment."""
    left_start, left_stop = left
    right_start, right_stop = right
    return (
        left_start < right_start < left_stop < right_stop
        or right_start < left_start < right_stop < left_stop
    )


def test_source_structures_cross_and_a_tree_would_duplicate_content() -> None:
    """The fixture proves page/verse and sentence/verse crossings explicitly."""
    graph = fixture()
    assert _crosses(PAGE_RANGES[0], VERSE_RANGES[2])
    assert _crosses(SENTENCE_RANGES[0], VERSE_RANGES[2])
    assert _crosses(PAGE_RANGES[2], SENTENCE_RANGES[4])
    assert _crosses(PAGE_RANGES[4], VERSE_RANGES[7])
    assert _crosses(SENTENCE_RANGES[8], VERSE_RANGES[7])

    verse = ItemRef(VERSE, 2)
    verse_members = {
        relation.left for relation in graph.relations if relation.right == verse
    }
    assert verse_members == {ItemRef(TEXT, 2), ItemRef(TEXT, 3)}
    assert ItemRef(TEXT, 2) in verse_members
    assert ItemRef(TEXT, 3) in verse_members
    # A tree rooted in pages must split this one verse and duplicate its identity.
    assert PAGE_RANGES[0][1] == 3 == PAGE_RANGES[1][0]


def test_counting_fold_answers_cross_hierarchy_coverage_by_hand() -> None:
    """Counting uses (+, *, 0, 1); thirteen atoms have three memberships each."""
    graph = fixture()
    declaration = FoldDeclaration(
        "cross-hierarchy coverage",
        graph,
        AttributeValuation(
            "unit count",
            COUNT,
            (TEXT, PAGE, SENTENCE, VERSE),
        ),
        COUNTING,
        lambda value, _label: int(value),
        (FoldTransition(COVERED_BY, ChildCombination.OR),),
        roots=tuple(ItemRef(TEXT, index) for index in range(len(TEXT_IDS))),
    )
    result = declaration.run()

    assert result.value == 39
    root_values = dict(result.values)
    assert tuple(root_values[(root, ())] for root in declaration.roots) == (3,) * 13
    assert result.provenance is None
