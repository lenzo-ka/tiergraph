"""The canonical form is unique because every sort key separates its own duplicates.

Two enumerations hold over the code as it stands today, and neither is stated
anywhere the next collection has to answer to.

The first is that `_layer_subject_data` is injective.  `layer.facts` is ordered
by `_layer_fact_key`, which is `(repr(_layer_subject_data(subject)), namespace,
local_name)`, while duplicate detection keys on `(subject, value.name)` -- the
subject object itself.  That is the only place in the kernel where the sort key
is an *encoding* of the duplicate key rather than the duplicate key.  If two
distinct subjects ever encoded alike the order would stop being total, CPython's
stable sort would leak supply order into the result, and two graphs equal as
values would write different bytes.  Enumerating subjects cannot settle it: a
durable identifier is any non-empty string, so the space is unbounded.

The second is that every order-insensitive collection is sorted by a key that
separates the members a duplicate check would refuse.  Nothing stops a new
collection arriving with a partial key, and a partial key is invisible until two
members tie -- so the sort/duplicate pairing is checked here by permuting every
such collection and requiring the canonical bytes not to move, over a fixture
built to hold at least two members of every one of them.  A permutation over a
collection with fewer than two members cannot move anything, so a sweep over a
fixture too thin to tie reports success for a sort it never ran: the fixture is
therefore measured rather than trusted, and a collection nobody has classified
is refused rather than walked past.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import fields, is_dataclass, replace
from typing import Any, TypeAliasType, cast, get_args

import pytest
from hypothesis import find, given, settings
from hypothesis import strategies as st

from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    Boundary,
    BoundaryRef,
    BoundarySide,
    DocumentRef,
    DurableBoundaryRef,
    DurableItemRef,
    DurablePolyadicRef,
    DurableRelationRef,
    Graph,
    GraphCarrier,
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
    RelationDeclarationRef,
    RelationEndpointKind,
    RelationInstance,
    RelationInstanceRef,
    RelationSideDeclaration,
    Seal,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    TierRef,
    XsdType,
    core,
    wire,
)
from tiergraph.core import (
    LayerSubject,
    _layer_fact_key,
    _layer_key,
    _layer_subject_data,
)

# ------------------------------------------------------------------ property 1

# Every variant the declaration admits, read off the alias rather than retyped,
# so a thirteenth subject shape arrives here as a failure of the coverage test
# instead of as a strategy that quietly stopped covering the union.  The alias is
# reached through the module dictionary because naming it directly is a type
# expression, not a value.
DECLARED_SUBJECT_VARIANTS = get_args(
    cast(TypeAliasType, core.__dict__["LayerSubject"]).__value__
)

# Non-empty is the only constraint the kernel places on a name or a durable
# identifier, so the sampled half of this strategy is the adversarial half:
# spellings that collide under a careless encoding -- the encoder's own tag
# words, the key names it writes, quotes and braces that could forge one
# `repr` fragment inside another, and digits that could pass for an index.
NAME_TEXT = st.one_of(
    st.sampled_from(
        [
            "w0",
            "w1",
            "kind",
            "index",
            "tier",
            "item",
            "durable_id",
            "0",
            "1",
            "'",
            "\\",
            "x'y",
            "{'kind': 'document'}",
            '{"a": 1}',
            "item-coordinate",
            "durable-item",
        ]
    ),
    st.text(min_size=1, max_size=3),
)
INDEXES = st.integers(min_value=-3, max_value=12)


def qualified_names() -> st.SearchStrategy[QualifiedName]:
    """Return expanded names over the adversarial spelling pool."""
    return st.builds(QualifiedName, NAME_TEXT, NAME_TEXT)


def layer_subjects() -> st.SearchStrategy[LayerSubject]:
    """Return every shape the `LayerSubject` union admits."""
    names = qualified_names()
    unorphaned = st.one_of(
        st.builds(ItemRef, names, INDEXES),
        st.builds(DurableItemRef, NAME_TEXT),
        st.builds(BoundaryRef, names, INDEXES),
        st.builds(
            DurableBoundaryRef,
            st.one_of(st.builds(DurableItemRef, NAME_TEXT), names),
            st.sampled_from(BoundarySide),
        ),
        st.builds(TierRef, names),
        st.builds(RelationDeclarationRef, names),
        st.builds(RelationInstanceRef, INDEXES),
        st.builds(DurableRelationRef, NAME_TEXT),
        st.builds(PolyadicInstanceRef, INDEXES),
        st.builds(DurablePolyadicRef, NAME_TEXT),
        st.builds(DocumentRef),
    )
    orphans = st.builds(
        OrphanedSubject,
        st.one_of(names, st.sampled_from(GraphCarrier)),
        st.one_of(
            st.builds(ItemRef, names, INDEXES),
            st.builds(BoundaryRef, names, INDEXES),
            INDEXES,
        ),
    )
    return st.one_of(unorphaned, orphans)


def _recognizes(shape: type) -> Callable[[LayerSubject], bool]:
    """Return a predicate that recognizes one subject shape."""

    def predicate(subject: LayerSubject) -> bool:
        return isinstance(subject, shape)

    return predicate


def test_the_subject_strategy_reaches_every_declared_variant() -> None:
    """Refuse a union member the injectivity property would never have drawn.

    The budget is named rather than defaulted, and it is a ceiling reached only
    when a shape is genuinely unreachable: the search stops at the first subject
    of each shape, so a strategy that covers the union pays for about a dozen
    draws per variant whatever the ceiling says.  Left at the default, a shape
    drawn one time in twelve would be missed once in a few thousand runs, and a
    coverage test that fails at random teaches its readers to rerun it.
    """
    strategy = layer_subjects()
    for variant in DECLARED_SUBJECT_VARIANTS:
        found = find(
            strategy, _recognizes(variant), settings=settings(max_examples=1000)
        )
        assert isinstance(found, variant)


@settings(max_examples=500)
@given(st.lists(layer_subjects(), min_size=2, max_size=8, unique=True))
def test_distinct_layer_subjects_encode_distinctly(
    subjects: list[LayerSubject],
) -> None:
    """State that `_layer_subject_data` is injective, which the sort assumes."""
    seen: dict[str, LayerSubject] = {}
    for subject in subjects:
        encoded = repr(_layer_subject_data(subject))
        collision = seen.setdefault(encoded, subject)
        assert collision == subject, (
            f"{collision!r} and {subject!r} are distinct subjects that encode "
            f"alike as {encoded}; the layer-fact order is not total"
        )


@settings(max_examples=500)
@given(
    st.lists(
        st.tuples(layer_subjects(), qualified_names()),
        min_size=2,
        max_size=8,
        unique=True,
    )
)
def test_the_layer_sort_key_separates_every_distinct_duplicate_key(
    keys: list[tuple[LayerSubject, QualifiedName]],
) -> None:
    """Pair the sort key with the duplicate key it is an encoding of."""
    seen: dict[tuple[str, str, str], tuple[LayerSubject, QualifiedName]] = {}
    for key in keys:
        sort_key = _layer_key(key)
        collision = seen.setdefault(sort_key, key)
        assert collision == key, (
            f"{collision!r} and {key!r} are distinct duplicate-detection keys "
            f"that sort alike under {sort_key!r}"
        )


def test_the_fact_key_is_the_duplicate_key_encoded() -> None:
    """Hold the two spellings of one key together so neither drifts alone."""
    for layer in KITCHEN_SINK.layers:
        for fact in layer.facts:
            assert _layer_fact_key(fact) == _layer_key((fact.subject, fact.value.name))


# ------------------------------------------------------------------ property 2

NS = "urn:tiergraph:kitchen"
VOCAB_A = "urn:tiergraph:vocab-a"
VOCAB_B = "urn:tiergraph:vocab-b"
WORDS = QualifiedName(NS, "words")
PHONES = QualifiedName(NS, "phones")
TOKEN = QualifiedName(NS, "token")
PHONE = QualifiedName(NS, "phone")
LINKS = QualifiedName(NS, "links")
POLY = QualifiedName(NS, "poly")

# Every tuple-valued field reachable from a graph, split by whether its order
# carries graph meaning.  `_classified` refuses anything absent from both sets,
# so a new collection cannot join the kernel without someone stating which one
# it is -- and the permutation sweep then covers it without being edited.
ORDER_INSENSITIVE = frozenset(
    {
        ("Graph", "namespaces"),
        ("Graph", "relation_declarations"),
        ("Graph", "attribute_declarations"),
        ("Graph", "boundary_values"),
        ("Graph", "attributes"),
        ("Graph", "seals"),
        ("Graph", "layers"),
        ("Tier", "attributes"),
        ("Item", "attributes"),
        ("SimpleRelationDeclaration", "attributes"),
        ("BipartiteRelationDeclaration", "attributes"),
        ("PolyadicRelationDeclaration", "attributes"),
        ("RelationInstance", "attributes"),
        ("PolyadicRelationInstance", "attributes"),
        ("Boundary", "attributes"),
        ("Layer", "facts"),
        ("RelationSideDeclaration", "endpoint_kinds"),
        ("RelationSideDeclaration", "tiers"),
    }
)
ORDER_SIGNIFICANT = frozenset(
    {
        ("Graph", "tiers"),
        ("Graph", "relations"),
        ("Graph", "polyadic_relations"),
        ("Tier", "items"),
        ("PolyadicRelationInstance", "sources"),
        ("PolyadicRelationInstance", "targets"),
    }
)


def name(local: str, namespace: str = NS) -> QualifiedName:
    """Return an expanded name in the fixture's own namespace by default."""
    return QualifiedName(namespace, local)


def values(
    domain: AttributeDomain, namespace: str, count: int
) -> tuple[AttributeValue, ...]:
    """Return `count` declared values of one domain, enough to permute."""
    return tuple(
        AttributeValue(
            name(f"{domain.value}-{index}", namespace), XsdType.STRING, f"v{index}"
        )
        for index in range(count)
    )


ATTRIBUTE_DECLARATIONS = tuple(
    AttributeDeclaration(
        name(f"{domain.value}-{index}", namespace), domain, XsdType.STRING
    )
    for namespace in (NS, VOCAB_A, VOCAB_B)
    for domain in AttributeDomain
    for index in range(6)
)

# One fact per subject variant the union declares, so the sweep permutes a layer
# whose sort key has met every encoding branch it will ever be handed.
SUBJECTS_BY_DOMAIN: tuple[tuple[LayerSubject, AttributeDomain], ...] = (
    (ItemRef(WORDS, 0), AttributeDomain.ITEM),
    (ItemRef(WORDS, 1), AttributeDomain.ITEM),
    (DurableItemRef("w0"), AttributeDomain.ITEM),
    (TierRef(WORDS), AttributeDomain.TIER),
    (TierRef(PHONES), AttributeDomain.TIER),
    (RelationDeclarationRef(LINKS), AttributeDomain.RELATION_DECLARATION),
    (RelationDeclarationRef(POLY), AttributeDomain.RELATION_DECLARATION),
    (RelationInstanceRef(0), AttributeDomain.RELATION_INSTANCE),
    (DurableRelationRef("r0"), AttributeDomain.RELATION_INSTANCE),
    (PolyadicInstanceRef(0), AttributeDomain.RELATION_INSTANCE),
    (DurablePolyadicRef("p0"), AttributeDomain.RELATION_INSTANCE),
    (BoundaryRef(WORDS, 1), AttributeDomain.BOUNDARY),
    (
        DurableBoundaryRef(DurableItemRef("w0"), BoundarySide.BEFORE),
        AttributeDomain.BOUNDARY,
    ),
    (DurableBoundaryRef(WORDS, BoundarySide.AFTER), AttributeDomain.BOUNDARY),
    (DocumentRef(), AttributeDomain.DOCUMENT),
    (OrphanedSubject(GraphCarrier.RELATIONS, 3), AttributeDomain.RELATION_INSTANCE),
    (
        OrphanedSubject(GraphCarrier.POLYADIC_RELATIONS, 4),
        AttributeDomain.RELATION_INSTANCE,
    ),
    (OrphanedSubject(WORDS, ItemRef(WORDS, 7)), AttributeDomain.ITEM),
    (OrphanedSubject(WORDS, BoundaryRef(WORDS, 7)), AttributeDomain.BOUNDARY),
)


def layer(vocabulary: str, source: str) -> Layer:
    """Return one layer stating a distinct value at every subject variant."""
    counters: dict[AttributeDomain, int] = {}
    facts: list[LayerFact] = []
    for subject, domain in SUBJECTS_BY_DOMAIN:
        index = counters.get(domain, 0)
        counters[domain] = index + 1
        facts.append(
            LayerFact(
                subject,
                AttributeValue(
                    name(f"{domain.value}-{index}", vocabulary),
                    XsdType.STRING,
                    f"{source}-{domain.value}-{index}",
                ),
            )
        )
    return Layer(LayerName(vocabulary, source), tuple(facts))


def kitchen_sink_graph() -> Graph:
    """Return one accepted graph holding two members of every sorted collection.

    A permutation over a collection with fewer than two members cannot move the
    result, so a thin fixture reports success for a sort it never ran.  This
    graph is built to the opposite standard: every order-insensitive collection
    the kernel holds reaches at least two members here, which is what
    `test_the_fixture_can_tie_every_order_insensitive_collection` measures.
    """
    return Graph(
        namespaces=(
            NamespaceDeclaration("k", NS),
            NamespaceDeclaration("a", VOCAB_A),
            NamespaceDeclaration("b", VOCAB_B),
        ),
        tiers=(
            Tier(
                TierDeclaration(WORDS, "Words"),
                (
                    Item("w0", values(AttributeDomain.ITEM, NS, 3)),
                    Item("w1", values(AttributeDomain.ITEM, NS, 2)),
                ),
                values(AttributeDomain.TIER, NS, 3),
            ),
            Tier(
                TierDeclaration(PHONES, "Phones"),
                (Item("ph0"), Item("ph1")),
                values(AttributeDomain.TIER, NS, 2),
            ),
        ),
        relation_declarations=(
            SimpleRelationDeclaration(
                name("word-type"),
                WORDS,
                TOKEN,
                values(AttributeDomain.RELATION_DECLARATION, NS, 3),
            ),
            SimpleRelationDeclaration(name("phone-type"), PHONES, PHONE),
            BipartiteRelationDeclaration(
                LINKS,
                TOKEN,
                TOKEN,
                RelationEndpointKind.ITEM,
                RelationEndpointKind.ITEM,
                attributes=values(AttributeDomain.RELATION_DECLARATION, NS, 2),
            ),
            PolyadicRelationDeclaration(
                POLY,
                RelationSideDeclaration(
                    (RelationEndpointKind.ITEM, RelationEndpointKind.BOUNDARY),
                    (WORDS, PHONES),
                    1,
                ),
                RelationSideDeclaration(
                    (RelationEndpointKind.ITEM, RelationEndpointKind.BOUNDARY),
                    (WORDS, PHONES),
                    1,
                ),
                attributes=values(AttributeDomain.RELATION_DECLARATION, NS, 2),
            ),
        ),
        relations=(
            RelationInstance(
                LINKS,
                ItemRef(WORDS, 0),
                ItemRef(WORDS, 1),
                "r0",
                values(AttributeDomain.RELATION_INSTANCE, NS, 3),
            ),
            RelationInstance(
                LINKS,
                DurableItemRef("w1"),
                ItemRef(WORDS, 0),
                "r1",
                values(AttributeDomain.RELATION_INSTANCE, NS, 2),
            ),
        ),
        attribute_declarations=ATTRIBUTE_DECLARATIONS,
        boundary_values=(
            Boundary(BoundaryRef(WORDS, 0), values(AttributeDomain.BOUNDARY, NS, 3)),
            Boundary(BoundaryRef(WORDS, 2), values(AttributeDomain.BOUNDARY, NS, 2)),
            Boundary(BoundaryRef(PHONES, 1), values(AttributeDomain.BOUNDARY, NS, 2)),
        ),
        attributes=values(AttributeDomain.DOCUMENT, NS, 3),
        polyadic_relations=(
            PolyadicRelationInstance(
                POLY,
                (ItemRef(WORDS, 0), ItemRef(PHONES, 0)),
                (ItemRef(WORDS, 1),),
                "p0",
                values(AttributeDomain.RELATION_INSTANCE, NS, 3),
            ),
            PolyadicRelationInstance(
                POLY,
                (ItemRef(WORDS, 1),),
                (ItemRef(PHONES, 1), ItemRef(PHONES, 0)),
                "p1",
                values(AttributeDomain.RELATION_INSTANCE, NS, 2),
            ),
        ),
        seals=(
            Seal(WORDS, 1),
            Seal(PHONES, 2),
            Seal(GraphCarrier.RELATIONS, 1),
            Seal(GraphCarrier.POLYADIC_RELATIONS, 1),
        ),
        layers=(
            layer(VOCAB_A, "source-1"),
            layer(VOCAB_A, "source-2"),
            layer(VOCAB_B, "source-1"),
            layer(VOCAB_B, "source-2"),
        ),
    )


KITCHEN_SINK = kitchen_sink_graph()


def _classified(key: tuple[str, str]) -> bool:
    """Return whether this collection's order is declared to carry meaning."""
    if key in ORDER_INSENSITIVE:
        return False
    if key in ORDER_SIGNIFICANT:
        return True
    raise AssertionError(
        f"{key[0]}.{key[1]} is a collection nobody has classified; state whether "
        "its order carries graph meaning, in ORDER_SIGNIFICANT, or whether it is "
        "canonicalized, in ORDER_INSENSITIVE, so the permutation sweep covers it"
    )


def _collections(value: object) -> list[tuple[tuple[str, str], tuple[object, ...]]]:
    """Return every tuple-valued field reachable from a value, with its owner."""
    found: list[tuple[tuple[str, str], tuple[object, ...]]] = []
    if not is_dataclass(value) or isinstance(value, type):
        return found
    for member in fields(value):
        if not member.init:
            continue
        current = getattr(value, member.name)
        if isinstance(current, tuple):
            found.append(((type(value).__name__, member.name), current))
            for element in current:
                found.extend(_collections(element))
        else:
            found.extend(_collections(current))
    return found


def _permuted(value: Any, draw: Any) -> Any:
    """Return the value rebuilt with every order-insensitive collection shuffled.

    The walk is generic rather than a hand-written list of collections: a field
    added to any of these dataclasses is picked up here, and `_classified`
    refuses it until someone says what its order means.
    """
    if not is_dataclass(value) or isinstance(value, type):
        return value
    changes: dict[str, Any] = {}
    for member in fields(value):
        if not member.init:
            continue
        current = getattr(value, member.name)
        if isinstance(current, tuple):
            elements = [_permuted(element, draw) for element in current]
            if not _classified((type(value).__name__, member.name)):
                elements = draw(st.permutations(elements))
            changes[member.name] = tuple(elements)
        else:
            rebuilt = _permuted(current, draw)
            if rebuilt is not current:
                changes[member.name] = rebuilt
    return replace(value, **changes) if changes else value


def test_the_fixture_can_tie_every_order_insensitive_collection() -> None:
    """Measure the fixture, because a permutation of one member proves nothing."""
    widest: dict[tuple[str, str], int] = {}
    for key, collection in _collections(KITCHEN_SINK):
        widest[key] = max(widest.get(key, 0), len(collection))
    thin = sorted(
        f"{owner}.{field_name} reaches {widest.get((owner, field_name), 0)}"
        for owner, field_name in ORDER_INSENSITIVE
        if widest.get((owner, field_name), 0) < 2
    )
    assert not thin, (
        "the fixture never gives these collections two members to reorder, so "
        f"the permutation sweep cannot exercise their sort keys: {thin}"
    )
    unrealized = sorted(
        f"{owner}.{field_name}"
        for owner, field_name in ORDER_SIGNIFICANT
        if (owner, field_name) not in widest
    )
    assert not unrealized, (
        f"the fixture never builds these declared collections at all: {unrealized}"
    )


def test_every_reachable_collection_is_classified() -> None:
    """Refuse a collection whose order nobody has stated a position on."""
    reached = {key for key, _ in _collections(KITCHEN_SINK)}
    for key in reached:
        _classified(key)
    stale = sorted(
        f"{owner}.{field_name}"
        for owner, field_name in ORDER_INSENSITIVE | ORDER_SIGNIFICANT
        if (owner, field_name) not in reached
    )
    assert not stale, (
        f"these classifications name collections the kernel no longer has: {stale}"
    )


def _first_difference(left: bytes, right: bytes) -> str:
    """Return where two canonical documents first part company."""
    for index, (before, after) in enumerate(
        zip(left.split(b"\n"), right.split(b"\n"), strict=False)
    ):
        if before != after:
            return (
                "permuting order-insensitive collections moved the canonical "
                f"bytes; line {index} was {before!r} and is now {after!r}"
            )
    return f"the canonical document changed length: {len(left)} to {len(right)} bytes"


@settings(max_examples=100, deadline=None)
@given(st.data())
def test_permuting_order_insensitive_collections_leaves_the_bytes_alone(
    data: st.DataObject,
) -> None:
    """Require the canonical bytes to be a function of the graph's value."""
    canonical = wire.dump_bytes(KITCHEN_SINK)
    shuffled = _permuted(KITCHEN_SINK, data.draw)
    rewritten = wire.dump_bytes(shuffled)
    assert rewritten == canonical, _first_difference(canonical, rewritten)
    assert shuffled == KITCHEN_SINK


def test_reordering_an_order_significant_sequence_moves_the_bytes() -> None:
    """Prove the sweep can see order at all, so its silence means something."""
    tier = KITCHEN_SINK.tiers[0]
    swapped = Tier(
        tier.declaration,
        (tier.items[1], tier.items[0], *tier.items[2:]),
        tier.attributes,
    )
    moved = replace(
        KITCHEN_SINK,
        tiers=(swapped, *KITCHEN_SINK.tiers[1:]),
        seals=(),
        layers=(),
    )
    unmoved = replace(KITCHEN_SINK, seals=(), layers=())
    assert wire.dump_bytes(moved) != wire.dump_bytes(unmoved)


def test_the_fixture_round_trips_through_the_wire() -> None:
    """Keep the fixture a real document, not a shape only this file accepts."""
    assert wire.loads(wire.dumps(KITCHEN_SINK)) == KITCHEN_SINK


def test_an_unclassified_collection_is_refused() -> None:
    """State what happens to the next collection nobody classified."""
    with pytest.raises(AssertionError, match="nobody has classified"):
        _classified(("Graph", "not_a_declared_collection"))
