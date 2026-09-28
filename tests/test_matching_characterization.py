"""Pin existing behavior adopted by the matching facility."""

from __future__ import annotations

from typing import Any

from tiergraph import (
    BOOLEAN,
    COUNTING,
    AttributeDeclaration,
    AttributeDomain,
    AttributeSelector,
    AttributeValuation,
    AttributeValue,
    BipartiteRelationDeclaration,
    ChildCombination,
    Emissions,
    FoldDeclaration,
    FoldTransition,
    Graph,
    Item,
    ItemRef,
    ItemSelector,
    ItemsSelector,
    NamespaceDeclaration,
    NodeSet,
    OutputPlan,
    PathPlan,
    QualifiedName,
    RelationInstance,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    UnionSelector,
    Walk,
    WalkDirection,
    XsdType,
    evaluate_selection,
)
from tiergraph.semiring import Semiring

NS = "urn:example:fixture#"
PREFIX = (NamespaceDeclaration("ex", NS),)


def name(local_name: str) -> QualifiedName:
    """Return one name in the fixture namespace."""
    return QualifiedName(NS, local_name)


def item_indices(nodes: NodeSet) -> list[int]:
    """Return item indices from a selection in canonical order."""
    result = []
    for node in nodes.nodes:
        assert isinstance(node.reference, ItemRef)
        result.append(node.reference.index)
    return result


def item_labels(nodes: NodeSet) -> list[str]:
    """Return durable item labels from a selection in canonical order."""
    result = []
    tiers = {tier.declaration.name: tier for tier in nodes.graph.tiers}
    for node in nodes.nodes:
        assert isinstance(node.reference, ItemRef)
        label = tiers[node.reference.tier].items[node.reference.index].durable_id
        assert label is not None
        result.append(label)
    return result


def path_graph(
    labels: tuple[str, ...], edges: tuple[tuple[str, str], ...]
) -> tuple[Graph, QualifiedName, QualifiedName, dict[str, ItemRef]]:
    """Build one unit-valued item tier with an acyclic next relation."""
    tier_name = name("item")
    item_type = name("item-type")
    membership = name("membership")
    next_relation = name("next")
    value = name("value")
    refs = {label: ItemRef(tier_name, index) for index, label in enumerate(labels)}
    graph = Graph(
        PREFIX,
        (
            Tier(
                TierDeclaration(tier_name, "Items"),
                tuple(
                    Item(
                        label,
                        (AttributeValue(value, XsdType.STRING, "present"),),
                    )
                    for label in labels
                ),
            ),
        ),
        (
            SimpleRelationDeclaration(membership, tier_name, item_type),
            BipartiteRelationDeclaration(
                next_relation, item_type, item_type, acyclic=True
            ),
        ),
        tuple(
            RelationInstance(next_relation, refs[left], refs[right])
            for left, right in edges
        ),
        (AttributeDeclaration(value, AttributeDomain.ITEM, XsdType.STRING),),
    )
    return graph, tier_name, next_relation, refs


def path_plan(
    graph: Graph,
    tier_name: QualifiedName,
    next_relation: QualifiedName,
    roots: tuple[ItemRef, ...],
    semiring: Semiring[Any],
) -> PathPlan[Any]:
    """Prepare a unit-valued path plan over one fixture graph."""
    declaration = FoldDeclaration[Any](
        "paths",
        graph,
        AttributeValuation("value", name("value"), (tier_name,)),
        semiring,
        lambda _value, _label: semiring.one,
        (FoldTransition(next_relation, ChildCombination.OR),),
        roots=roots,
    )
    return PathPlan.prepare(declaration)


def test_output_plan_groups_silent_paths_by_candidate() -> None:
    """Silent items preserve the trie state used to pool complete outputs."""
    graph, tier_name, next_relation, refs = path_graph(
        ("r", "a1", "a2", "a3", "s", "f"),
        (
            ("r", "a1"),
            ("r", "a2"),
            ("r", "a3"),
            ("a1", "s"),
            ("s", "f"),
            ("a1", "f"),
            ("a2", "f"),
            ("a3", "f"),
        ),
    )
    plan = path_plan(graph, tier_name, next_relation, (refs["r"],), COUNTING)
    emissions = Emissions.bind(
        plan,
        {
            "r": ("ten",),
            "a1": ("o",),
            "a2": ("oh",),
            "a3": ("zero",),
            "f": ("five",),
        },
    )
    masses = OutputPlan.prepare(
        plan,
        emissions,
        (
            ("ten", "o", "five"),
            ("ten", "oh", "five"),
            ("ten", "zero", "five"),
        ),
    ).masses()

    assert (masses.per_candidate, masses.residual, masses.total) == (
        (2, 1, 1),
        0,
        4,
    )


def test_relation_walk_can_be_subtracted_from_a_tier_selection() -> None:
    """A forward host walk removes exactly the related segment items."""
    segment = name("seg")
    segment_type = name("seg-item")
    tone = name("tone")
    tone_type = name("tone-item")
    host = name("host")
    tone_value = name("tv")
    segment_refs = tuple(ItemRef(segment, index) for index in range(4))
    tone_refs = tuple(ItemRef(tone, index) for index in range(4))
    graph = Graph(
        PREFIX,
        (
            Tier(
                TierDeclaration(segment, "Segments"),
                tuple(Item(f"s{index}") for index in range(4)),
            ),
            Tier(
                TierDeclaration(tone, "Tones"),
                tuple(
                    Item(
                        f"t{index}",
                        (AttributeValue(tone_value, XsdType.STRING, value),),
                    )
                    for index, value in enumerate(("H", "L", "H", "H"))
                ),
            ),
        ),
        (
            SimpleRelationDeclaration(name("seg-membership"), segment, segment_type),
            SimpleRelationDeclaration(name("tone-membership"), tone, tone_type),
            BipartiteRelationDeclaration(host, tone_type, segment_type),
        ),
        (
            RelationInstance(host, tone_refs[0], segment_refs[0]),
            RelationInstance(host, tone_refs[1], segment_refs[2]),
            RelationInstance(host, tone_refs[2], segment_refs[2]),
            RelationInstance(host, tone_refs[3], segment_refs[3]),
        ),
        (AttributeDeclaration(tone_value, AttributeDomain.ITEM, XsdType.STRING),),
    )
    segments = evaluate_selection(graph, ItemsSelector(segment))
    tones = evaluate_selection(graph, ItemsSelector(tone))
    hosted = Walk(tones, host, WalkDirection.FORWARD, cap=1).evaluate().nodes

    assert item_labels(segments - hosted) == ["s1"]


def test_inverse_walk_excludes_every_source_item() -> None:
    """A bounded inverse walk excludes all items in its source selection."""
    graph, _tier_name, next_relation, refs = path_graph(
        ("i0", "i1", "i2"), (("i0", "i1"), ("i1", "i2"))
    )
    source = evaluate_selection(
        graph,
        UnionSelector((ItemSelector(refs["i1"]), ItemSelector(refs["i2"]))),
    )

    reached = Walk(source, next_relation, WalkDirection.INVERSE, cap=1).evaluate().nodes

    assert item_labels(reached) == ["i0"]


def stress_graph(include_empty: bool) -> tuple[Graph, QualifiedName]:
    """Build the five- or six-item stress-presence fixture."""
    segment = name("seg")
    segment_type = name("seg-item")
    class_attribute = name("class")
    stress = name("stress")
    values: tuple[tuple[str, str | None, str | None], ...] = (
        ("0", "vowel", "primary"),
        ("1", "vowel", "secondary"),
        ("2", "vowel", None),
        ("3", "vowel", "tertiary"),
        ("4", "consonant", None),
        *((("5", "vowel", ""),) if include_empty else ()),
    )

    def item(label: str, item_class: str | None, item_stress: str | None) -> Item:
        attributes = []
        if item_class is not None:
            attributes.append(
                AttributeValue(class_attribute, XsdType.STRING, item_class)
            )
        if item_stress is not None:
            attributes.append(AttributeValue(stress, XsdType.STRING, item_stress))
        return Item(label, tuple(attributes))

    return (
        Graph(
            PREFIX,
            (
                Tier(
                    TierDeclaration(segment, "Segments"),
                    tuple(item(*entry) for entry in values),
                ),
            ),
            (SimpleRelationDeclaration(name("membership"), segment, segment_type),),
            (),
            (
                AttributeDeclaration(
                    class_attribute, AttributeDomain.ITEM, XsdType.STRING
                ),
                AttributeDeclaration(stress, AttributeDomain.ITEM, XsdType.STRING),
            ),
        ),
        stress,
    )


def test_attribute_selection_matches_attribute_presence() -> None:
    """Attribute selection returns every item carrying the named attribute."""
    graph, stress = stress_graph(include_empty=False)
    selected = evaluate_selection(
        graph, AttributeSelector(stress, AttributeDomain.ITEM)
    )
    assert item_indices(selected) == [0, 1, 3]


def test_attribute_selection_treats_an_empty_string_as_present() -> None:
    """A stored empty string remains an attribute value, not absence."""
    graph, stress = stress_graph(include_empty=True)
    selected = evaluate_selection(
        graph, AttributeSelector(stress, AttributeDomain.ITEM)
    )
    assert item_indices(selected) == [0, 1, 3, 5]


def test_one_topology_folds_under_counting_and_boolean_semirings() -> None:
    """The diamond has two paths and at least one path."""
    graph, tier_name, next_relation, refs = path_graph(
        ("a", "b", "c", "d"),
        (("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")),
    )
    roots = (refs["a"],)
    count = path_plan(graph, tier_name, next_relation, roots, COUNTING).evaluate()
    exists = path_plan(graph, tier_name, next_relation, roots, BOOLEAN).evaluate()
    assert (count.value, exists.value) == (2, True)


def test_explicit_roots_exclude_unreachable_inferred_roots() -> None:
    """Declared roots take precedence over predecessor-free items."""
    graph, tier_name, next_relation, refs = path_graph(
        ("r", "a", "f", "u"),
        (("r", "a"), ("a", "f"), ("u", "f")),
    )
    explicit = path_plan(
        graph, tier_name, next_relation, (refs["r"],), COUNTING
    ).evaluate()
    inferred = path_plan(graph, tier_name, next_relation, (), COUNTING).evaluate()
    assert (explicit.value, inferred.value) == (1, 2)
