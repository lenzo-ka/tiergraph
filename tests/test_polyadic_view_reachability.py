"""Polyadic instances are reachable, and ordered, above the kernel.

Polyadic instances live in their own graph collection, so a view layer that
iterates only ``graph.relations`` answers a question about relations from the
bipartite half of the evidence.  These cases pin the two halves of the fix:
an operation whose domain covers both collections reads both, and every result
that carries a polyadic instance keeps its two sides in stored order.

Ordering is the load-bearing property.  A polyadic instance carries two ordered
sides with no positional correspondence between them, so a reordering
alignment survives it where a pair-per-correspondence encoding would not.
Each case below is built so that a flattening implementation -- one that read
a side as an unordered bag of endpoints, or paired the sides off positionally
-- produces a different observable and fails.
"""

from __future__ import annotations

import json

from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeSelector,
    AttributeValue,
    BipartiteRelationDeclaration,
    Graph,
    Item,
    ItemRef,
    NamespaceDeclaration,
    Node,
    NodeKind,
    OrderedPolyadicTraversal,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    PolyadicSide,
    QualifiedName,
    RelationEndpointKind,
    RelationInstance,
    RelationSideDeclaration,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    XsdType,
    evaluate_selection,
)

NS = "urn:tiergraph:polyadic-views"


def name(local: str) -> QualifiedName:
    """Return a name in the reachability fixture namespace."""
    return QualifiedName(NS, local)


FORMS, SIGNAL = name("forms"), name("signal")
FORM_TYPE, SIGNAL_TYPE = name("form-type"), name("signal-type")
ALIGN, LINK, MARK = name("aligns"), name("links"), name("mark")


def form(index: int) -> ItemRef:
    """Return one form-tier endpoint."""
    return ItemRef(FORMS, index)


def signal(index: int) -> ItemRef:
    """Return one signal-tier endpoint."""
    return ItemRef(SIGNAL, index)


def marked() -> tuple[AttributeValue, ...]:
    """Return the relation-instance value both collections may carry."""
    return (AttributeValue(MARK, XsdType.STRING, "yes"),)


def side(tier: QualifiedName) -> RelationSideDeclaration:
    """Return one unbounded, item-only side over the named tier."""
    return RelationSideDeclaration(
        (RelationEndpointKind.ITEM,), tiers=(tier,), maximum=None
    )


def graph(
    *,
    alignments: tuple[PolyadicRelationInstance, ...] = (),
    relations: tuple[RelationInstance, ...] = (),
) -> Graph:
    """Build three forms, two signal points, and the supplied incidence."""
    tiers = (
        Tier(
            TierDeclaration(FORMS, "Forms"),
            (Item("f0"), Item("f1"), Item("f2")),
        ),
        Tier(TierDeclaration(SIGNAL, "Signal"), (Item("s0"), Item("s1"))),
    )
    declarations = (
        SimpleRelationDeclaration(name("form-members"), FORMS, FORM_TYPE),
        SimpleRelationDeclaration(name("signal-members"), SIGNAL, SIGNAL_TYPE),
        PolyadicRelationDeclaration(ALIGN, side(FORMS), side(SIGNAL)),
        BipartiteRelationDeclaration(LINK, FORM_TYPE, SIGNAL_TYPE),
    )
    return Graph(
        (NamespaceDeclaration("v", NS),),
        tiers,
        declarations,
        relations,
        (
            AttributeDeclaration(
                MARK, AttributeDomain.RELATION_INSTANCE, XsdType.STRING
            ),
        ),
        polyadic_relations=alignments,
    )


def alignment(
    sources: tuple[ItemRef, ...],
    targets: tuple[ItemRef, ...],
    *,
    mark: bool = False,
) -> PolyadicRelationInstance:
    """Build one ordered alignment instance, optionally carrying the value."""
    return PolyadicRelationInstance(
        ALIGN, sources, targets, attributes=marked() if mark else ()
    )


def test_attribute_selection_reads_both_instance_collections() -> None:
    """The relation-instance axis reports the polyadic carrier, not only the pair.

    The kernel validates ``relation_instance`` values on both collections, so a
    selector that read only ``graph.relations`` would report a strict subset of
    the domain it names.  Both carriers sit at index 0 of their own collection;
    they stay distinct because the node kind, not the integer, is the identity.
    """
    populated = graph(
        alignments=(alignment((form(0),), (signal(0),), mark=True),),
        relations=(RelationInstance(LINK, form(0), signal(0), attributes=marked()),),
    )
    carriers = evaluate_selection(
        populated, AttributeSelector(MARK, AttributeDomain.RELATION_INSTANCE)
    )
    assert len(carriers.nodes) == 2
    assert carriers.nodes == (
        Node(NodeKind.RELATION_INSTANCE, 0),
        Node(NodeKind.POLYADIC_RELATION_INSTANCE, 0),
    )
    json.dumps(carriers.to_data(), allow_nan=False)


def test_selected_polyadic_instances_order_by_their_stored_sides() -> None:
    """Canonical order reads each side in stored order, so a reordering sorts apart.

    The two instances span the same endpoints and differ only in target order.
    Index order would put instance 0 first and an endpoint-set key would leave
    them tied; reading the side in order puts instance 1 first.
    """
    populated = graph(
        alignments=(
            alignment((form(0),), (signal(1), signal(0)), mark=True),
            alignment((form(0),), (signal(0), signal(1)), mark=True),
        )
    )
    carriers = evaluate_selection(
        populated, AttributeSelector(MARK, AttributeDomain.RELATION_INSTANCE)
    )
    assert carriers.to_data() == [
        {"kind": "polyadic_relation_instance", "reference": 1},
        {"kind": "polyadic_relation_instance", "reference": 0},
    ]


def traversal(value: Graph) -> OrderedPolyadicTraversal:
    """Return the ordered alignment traversal over one graph."""
    return OrderedPolyadicTraversal(
        value, ALIGN, PolyadicSide.SOURCES, PolyadicSide.TARGETS
    )


def test_instances_expose_both_sides_in_stored_order() -> None:
    """A correspondence read whole keeps three sources against two targets.

    The sides have unequal arity and the targets are stored reversed, so no
    positional pairing exists to flatten them into: an implementation that
    paired the sides off, or that sorted either side, reports something else.
    """
    populated = graph(
        alignments=(alignment((form(0), form(1), form(2)), (signal(1), signal(0))),)
    )
    incidence = traversal(populated).instances()
    assert len(incidence) == 1
    assert incidence[0].index == 0
    assert incidence[0].sources.nodes == (
        Node(NodeKind.ITEM, form(0)),
        Node(NodeKind.ITEM, form(1)),
        Node(NodeKind.ITEM, form(2)),
    )
    assert incidence[0].targets.nodes == (
        Node(NodeKind.ITEM, signal(1)),
        Node(NodeKind.ITEM, signal(0)),
    )
    encoded = json.dumps(
        incidence[0].to_data(), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    assert encoded.startswith(b'{"index":0,"sources":[')
    assert encoded.count(b'"kind":"item"') == 5


def test_reordered_alignment_is_a_different_instance_reading() -> None:
    """Negative control: sorting or set-coercing either side cannot pass."""
    reversed_targets = graph(
        alignments=(alignment((form(0), form(1), form(2)), (signal(1), signal(0))),)
    )
    forward_targets = graph(
        alignments=(alignment((form(0), form(1), form(2)), (signal(0), signal(1))),)
    )
    reordered_sources = graph(
        alignments=(alignment((form(2), form(1), form(0)), (signal(1), signal(0))),)
    )
    original = traversal(reversed_targets).instances()[0].to_data()
    assert traversal(forward_targets).instances()[0].to_data() != original
    assert traversal(reordered_sources).instances()[0].to_data() != original


def item_indices(entries: object) -> list[int]:
    """Return the item indices of one emitted node-sequence side, in order."""
    assert isinstance(entries, list)
    indices: list[int] = []
    for entry in entries:
        assert isinstance(entry, dict)
        reference = entry["reference"]
        assert isinstance(reference, dict)
        index = reference["index"]
        assert isinstance(index, int)
        indices.append(index)
    return indices


def test_stored_sides_survive_the_round_trip_through_public_data() -> None:
    """Both sides keep their order in the strict-JSON form consumers read."""
    populated = graph(
        alignments=(alignment((form(0), form(1)), (signal(1), signal(0))),)
    )
    data = traversal(populated).instances()[0].to_data()
    assert item_indices(data["sources"]) == [0, 1]
    assert item_indices(data["targets"]) == [1, 0]
