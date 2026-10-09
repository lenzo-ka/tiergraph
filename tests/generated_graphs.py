"""Shared generated graph shapes for edit-ledger and distance proofs."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise

from hypothesis import strategies as st
from hypothesis.strategies import DrawFn, SearchStrategy

from tiergraph import (
    BLOB_NAMESPACE,
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    Boundary,
    BoundaryRef,
    BoundarySide,
    DurableBoundaryRef,
    DurableItemRef,
    DurablePolyadicRef,
    DurableRelationRef,
    Graph,
    Item,
    ItemRef,
    Layer,
    LayerFact,
    LayerName,
    NamespaceDeclaration,
    OrphanedSubject,
    PolyadicInstanceRef,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    QualifiedName,
    RelationEndpointKind,
    RelationInstance,
    RelationInstanceRef,
    RelationSideDeclaration,
    SimpleRelationDeclaration,
    Subtree,
    Tier,
    TierDeclaration,
    XsdType,
)


@dataclass(frozen=True)
class GeneratedHierarchy:
    """Hold one rich hierarchy and the names used by its edit cases."""

    graph: Graph
    donor: Subtree
    utterance: QualifiedName
    phrase: QualifiedName
    word: QualifiedName
    syllable: QualifiedName
    segment: QualifiedName
    blob: QualifiedName
    phrase_words: QualifiedName
    group: QualifiedName
    link: QualifiedName
    boundary_link: QualifiedName
    attachment: QualifiedName
    label: QualifiedName
    boundary_note: QualifiedName
    relation_note: QualifiedName
    layer: LayerName


def _name(local: str) -> QualifiedName:
    return QualifiedName("urn:test:generated-hierarchy", local)


UTTERANCE, PHRASE, WORD, SYLLABLE, SEGMENT = (
    _name("utterance"),
    _name("phrase"),
    _name("word"),
    _name("syllable"),
    _name("segment"),
)
TIERS = (UTTERANCE, PHRASE, WORD, SYLLABLE, SEGMENT)
TYPES = tuple(_name(f"{tier.local_name}-type") for tier in TIERS)
MEMBERS = tuple(_name(f"{tier.local_name}-members") for tier in TIERS)
CONTAINMENTS = tuple(
    _name(f"{parent.local_name}-{child.local_name}")
    for parent, child in pairwise(TIERS)
)
PHRASE_WORDS = CONTAINMENTS[1]
LINK = _name("link")
BOUNDARY_LINK = _name("boundary-link")
GROUP = _name("group")
ATTACHMENT = _name("attachment")
LABEL = _name("label")
BOUNDARY_NOTE = _name("boundary-note")
RELATION_NOTE = _name("relation-note")
LAYER = LayerName(UTTERANCE.namespace, "generated")
BLOBS = _name("blobs")
BLOB_TYPE = _name("blob-type")
BLOB_MEMBERS = _name("blob-members")
SHA256 = QualifiedName(BLOB_NAMESPACE, "sha256")
SIZE = QualifiedName(BLOB_NAMESPACE, "size")
MEDIA_TYPE = QualifiedName(BLOB_NAMESPACE, "media-type")
SCHEMA = QualifiedName(BLOB_NAMESPACE, "schema")
RATE = QualifiedName(BLOB_NAMESPACE, "rate")
UNIT = QualifiedName(BLOB_NAMESPACE, "unit")
ORIGIN = QualifiedName(BLOB_NAMESPACE, "origin")
EXTENT = QualifiedName(BLOB_NAMESPACE, "extent")
OFFSET = QualifiedName(BLOB_NAMESPACE, "offset")
LENGTH = QualifiedName(BLOB_NAMESPACE, "length")
BLOB_ATTRIBUTES = (
    AttributeDeclaration(SHA256, AttributeDomain.ITEM, XsdType.STRING),
    AttributeDeclaration(SIZE, AttributeDomain.ITEM, XsdType.INTEGER),
    AttributeDeclaration(MEDIA_TYPE, AttributeDomain.ITEM, XsdType.STRING),
    AttributeDeclaration(SCHEMA, AttributeDomain.ITEM, XsdType.STRING),
    AttributeDeclaration(RATE, AttributeDomain.ITEM, XsdType.DECIMAL),
    AttributeDeclaration(UNIT, AttributeDomain.ITEM, XsdType.STRING),
    AttributeDeclaration(ORIGIN, AttributeDomain.ITEM, XsdType.DECIMAL),
    AttributeDeclaration(EXTENT, AttributeDomain.ITEM, XsdType.INTEGER),
    AttributeDeclaration(OFFSET, AttributeDomain.RELATION_INSTANCE, XsdType.INTEGER),
    AttributeDeclaration(LENGTH, AttributeDomain.RELATION_INSTANCE, XsdType.INTEGER),
)


def _schema() -> tuple[
    SimpleRelationDeclaration
    | BipartiteRelationDeclaration
    | PolyadicRelationDeclaration,
    ...,
]:
    """Return declarations shared by generated source and donor graphs."""
    simple = tuple(
        SimpleRelationDeclaration(member, tier, item_type)
        for member, tier, item_type in zip(MEMBERS, TIERS, TYPES, strict=True)
    )
    containment = tuple(
        PolyadicRelationDeclaration(
            name,
            RelationSideDeclaration((RelationEndpointKind.ITEM,), (parent,), maximum=1),
            RelationSideDeclaration(
                (RelationEndpointKind.ITEM,), (child,), maximum=None
            ),
            unique_sources=True,
            single_parent=True,
            acyclic=True,
        )
        for name, parent, child in zip(CONTAINMENTS, TIERS[:-1], TIERS[1:], strict=True)
    )
    mixed = RelationSideDeclaration(
        (RelationEndpointKind.ITEM, RelationEndpointKind.BOUNDARY),
        (SEGMENT,),
        maximum=None,
        allow_empty=True,
    )
    return (
        *simple,
        SimpleRelationDeclaration(BLOB_MEMBERS, BLOBS, BLOB_TYPE),
        *containment,
        BipartiteRelationDeclaration(LINK, TYPES[-1], TYPES[-1]),
        BipartiteRelationDeclaration(
            BOUNDARY_LINK,
            TYPES[-1],
            TYPES[-1],
            RelationEndpointKind.BOUNDARY,
            RelationEndpointKind.BOUNDARY,
        ),
        PolyadicRelationDeclaration(GROUP, mixed, mixed),
        BipartiteRelationDeclaration(ATTACHMENT, TYPES[-1], BLOB_TYPE),
    )


def _items(tier: QualifiedName, count: int, salt: str) -> tuple[Item, ...]:
    """Build durably identified items with generated scalar payloads."""
    return tuple(
        Item(
            f"{tier.local_name}-{index}",
            (AttributeValue(LABEL, XsdType.STRING, f"{salt}-{index}"),),
        )
        for index in range(count)
    )


def _containment_instances(
    segment_widths: tuple[int, int, int, int],
) -> tuple[PolyadicRelationInstance, ...]:
    """Build utterance through segment ordered containment."""
    starts = tuple(sum(segment_widths[:index]) for index in range(len(segment_widths)))
    return (
        PolyadicRelationInstance(
            CONTAINMENTS[0],
            (ItemRef(UTTERANCE, 0),),
            (ItemRef(PHRASE, 0), ItemRef(PHRASE, 1)),
        ),
        PolyadicRelationInstance(
            CONTAINMENTS[1], (ItemRef(PHRASE, 0),), (ItemRef(WORD, 0), ItemRef(WORD, 1))
        ),
        PolyadicRelationInstance(
            CONTAINMENTS[1], (ItemRef(PHRASE, 1),), (ItemRef(WORD, 2), ItemRef(WORD, 3))
        ),
        *(
            PolyadicRelationInstance(
                CONTAINMENTS[2], (ItemRef(WORD, index),), (ItemRef(SYLLABLE, index),)
            )
            for index in range(4)
        ),
        *(
            PolyadicRelationInstance(
                CONTAINMENTS[3],
                (ItemRef(SYLLABLE, index),),
                tuple(
                    ItemRef(SEGMENT, child)
                    for child in range(starts[index], starts[index] + width)
                ),
            )
            for index, width in enumerate(segment_widths)
        ),
    )


def _donor(salt: str) -> Graph:
    """Build a valid fresh phrase subtree under the source schema."""
    tiers = (
        Tier(TierDeclaration(UTTERANCE, "Utterance"), ()),
        Tier(TierDeclaration(PHRASE, "Phrase"), (Item(f"donor-phrase-{salt}"),)),
        Tier(TierDeclaration(WORD, "Word"), (Item(f"donor-word-{salt}"),)),
        Tier(TierDeclaration(SYLLABLE, "Syllable"), (Item(f"donor-syllable-{salt}"),)),
        Tier(TierDeclaration(SEGMENT, "Segment"), (Item(f"donor-segment-{salt}"),)),
        Tier(TierDeclaration(BLOBS, "External resources"), ()),
    )
    polyadic = tuple(
        PolyadicRelationInstance(
            name,
            (ItemRef(parent, 0),),
            (ItemRef(child, 0),),
        )
        for name, parent, child in zip(
            CONTAINMENTS[1:], TIERS[1:-1], TIERS[2:], strict=True
        )
    )
    return Graph(
        (
            NamespaceDeclaration("g", UTTERANCE.namespace),
            NamespaceDeclaration("blob", BLOB_NAMESPACE),
        ),
        tiers,
        _schema(),
        attribute_declarations=(
            AttributeDeclaration(LABEL, AttributeDomain.ITEM, XsdType.STRING),
            AttributeDeclaration(
                BOUNDARY_NOTE, AttributeDomain.BOUNDARY, XsdType.STRING
            ),
            AttributeDeclaration(
                RELATION_NOTE, AttributeDomain.RELATION_INSTANCE, XsdType.STRING
            ),
            *BLOB_ATTRIBUTES,
        ),
        polyadic_relations=polyadic,
    )


@st.composite
def generated_hierarchies(draw: DrawFn) -> GeneratedHierarchy:
    """Generate payloads on a full five-level linked hierarchy."""
    salt = draw(st.text(alphabet="abcxyz012", min_size=1, max_size=5))
    segment_widths = draw(
        st.tuples(*(st.integers(min_value=2, max_value=4) for _ in range(4)))
    )
    tier_counts = (1, 2, 4, 4, sum(segment_widths))
    tiers = tuple(
        Tier(TierDeclaration(tier, tier.local_name.title()), _items(tier, count, salt))
        for tier, count in zip(TIERS, tier_counts, strict=True)
    )
    tiers = (
        *tiers,
        Tier(
            TierDeclaration(BLOBS, "External resources"),
            (
                Item(
                    "nested-graph",
                    (
                        AttributeValue(SHA256, XsdType.STRING, "00" * 32),
                        AttributeValue(SIZE, XsdType.INTEGER, "0"),
                        AttributeValue(
                            MEDIA_TYPE, XsdType.STRING, "application/vnd.tiergraph+json"
                        ),
                        AttributeValue(
                            SCHEMA, XsdType.STRING, "urn:test:nested-tiergraph"
                        ),
                    ),
                ),
            ),
        ),
    )
    first = ItemRef(SEGMENT, 0)
    last = ItemRef(SEGMENT, 7)
    leading = DurableBoundaryRef(DurableItemRef("segment-1"), BoundarySide.BEFORE)
    trailing = DurableBoundaryRef(DurableItemRef("segment-6"), BoundarySide.AFTER)
    relation_value = AttributeValue(RELATION_NOTE, XsdType.STRING, salt)
    relations = (
        RelationInstance(LINK, first, last, "link", (relation_value,)),
        RelationInstance(
            BOUNDARY_LINK, leading, trailing, "boundary-link", (relation_value,)
        ),
        RelationInstance(ATTACHMENT, first, ItemRef(BLOBS, 0), "attachment"),
    )
    polyadic = (
        *_containment_instances(segment_widths),
        PolyadicRelationInstance(
            GROUP,
            (first, leading),
            (last, trailing),
            "group",
            (relation_value,),
        ),
    )
    boundary_value = AttributeValue(BOUNDARY_NOTE, XsdType.STRING, salt)
    graph = Graph(
        (
            NamespaceDeclaration("g", UTTERANCE.namespace),
            NamespaceDeclaration("blob", BLOB_NAMESPACE),
        ),
        tiers,
        _schema(),
        relations,
        (
            AttributeDeclaration(LABEL, AttributeDomain.ITEM, XsdType.STRING),
            AttributeDeclaration(
                BOUNDARY_NOTE, AttributeDomain.BOUNDARY, XsdType.STRING
            ),
            AttributeDeclaration(
                RELATION_NOTE, AttributeDomain.RELATION_INSTANCE, XsdType.STRING
            ),
            *BLOB_ATTRIBUTES,
        ),
        boundary_values=(
            Boundary(BoundaryRef(SEGMENT, 4), (boundary_value,)),
            Boundary(leading, (boundary_value,)),
        ),
        polyadic_relations=polyadic,
        layers=(
            Layer(
                LAYER,
                (
                    LayerFact(DurableRelationRef("link"), relation_value),
                    LayerFact(DurablePolyadicRef("group"), relation_value),
                    LayerFact(RelationInstanceRef(0), relation_value),
                    LayerFact(PolyadicInstanceRef(len(polyadic) - 1), relation_value),
                    LayerFact(leading, boundary_value),
                    LayerFact(
                        OrphanedSubject(SEGMENT, ItemRef(SEGMENT, 99)),
                        AttributeValue(LABEL, XsdType.STRING, f"orphan-{salt}"),
                    ),
                ),
            ),
        ),
    )
    donor = _donor(salt)
    return GeneratedHierarchy(
        graph,
        Subtree(donor, ItemRef(PHRASE, 0)),
        UTTERANCE,
        PHRASE,
        WORD,
        SYLLABLE,
        SEGMENT,
        BLOBS,
        PHRASE_WORDS,
        GROUP,
        LINK,
        BOUNDARY_LINK,
        ATTACHMENT,
        LABEL,
        BOUNDARY_NOTE,
        RELATION_NOTE,
        LAYER,
    )


GENERATED_HIERARCHIES: SearchStrategy[GeneratedHierarchy] = generated_hierarchies()
