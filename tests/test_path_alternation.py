"""Snapshot-local, weight-independent addressing over real grammar forests."""

from dataclasses import replace

import pytest

from tiergraph import (
    AlternativeRef,
    AttributeValue,
    CanonicalPath,
    GrammarChartProfile,
    GrammarDeclaration,
    GrammarHole,
    GrammarRule,
    GrammarTerminal,
    Graph,
    ItemBinding,
    ItemRef,
    PathBinding,
    PathKind,
    PathRefusal,
    PathRefusalCode,
    QualifiedName,
    ResolvedAlternative,
    StructuralPathProfile,
    XsdType,
    lower_grammar,
    recognize,
    resolve_path,
)

NS = "urn:path:alternation:test"
S = QualifiedName(NS, "S")
A = QualifiedName(NS, "A")
B = QualifiedName(NS, "B")
VALUE = QualifiedName(NS, "value")
VARIABLE = QualifiedName(NS, "variable")


def terminal(text: str) -> GrammarTerminal:
    """Build one string-carried terminal."""
    return GrammarTerminal(AttributeValue(VALUE, XsdType.STRING, text))


def hole(name: str) -> GrammarHole:
    """Build one A-valued source and target hole."""
    return GrammarHole(AttributeValue(VARIABLE, XsdType.STRING, name), A)


def ambiguous_grammar(first_weight: str, second_weight: str) -> GrammarDeclaration:
    """Build three root derivations, two sharing rule 0 with different splits."""
    x = terminal("x")
    left = hole("left")
    right = hole("right")
    return GrammarDeclaration(
        (S, A),
        S,
        (
            GrammarRule(
                S,
                (left, right),
                (left, right),
                weight=AttributeValue(VALUE, XsdType.DECIMAL, first_weight),
            ),
            GrammarRule(
                S,
                (x, x, x),
                (x, x, x),
                weight=AttributeValue(VALUE, XsdType.DECIMAL, second_weight),
            ),
            GrammarRule(A, (x,), (x,)),
            GrammarRule(A, (x, x), (x, x)),
        ),
    )


def application_key(
    profile: GrammarChartProfile, application: ItemRef
) -> tuple[int, tuple[tuple[int, int], ...]]:
    """Recover the asserted stable key independently from public graph data."""
    graph = profile.forest.graph
    item = next(
        tier.items[application.index]
        for tier in graph.tiers
        if tier.declaration.name == application.tier
    )
    rule_index = int(
        next(
            value.lexical
            for value in item.attributes
            if value.name.local_name == "start"
        )
    )
    production = next(
        relation
        for relation in graph.polyadic_relations
        if relation.declaration.local_name == "production-application"
        and relation.sources == (application,)
    )
    spans = []
    for child in production.targets:
        assert isinstance(child, ItemRef)
        child_item = next(
            tier.items[child.index]
            for tier in graph.tiers
            if tier.declaration.name == child.tier
        )
        values = {
            value.name.local_name: value.lexical for value in child_item.attributes
        }
        spans.append((int(values["start"]), int(values["end"])))
    return rule_index, tuple(spans)


def addressed_value(profile: GrammarChartProfile, text: str) -> object:
    """Resolve and narrow one profile alternative for strict type checking."""
    result = resolve_path(
        profile.forest.graph, profile, text, require=PathKind.ALTERNATIVE
    )
    assert isinstance(result, ResolvedAlternative)
    return result.value


def test_real_forest_alternatives_use_stable_key_not_discovery_or_weight() -> None:
    """Rule ordinal and child spans govern k while best-cost order can change."""
    low_rule_zero = recognize(
        lower_grammar(ambiguous_grammar("1", "9")), ("x", "x", "x")
    )
    low_rule_one = recognize(
        lower_grammar(ambiguous_grammar("9", "1")), ("x", "x", "x")
    )
    assert low_rule_zero.count() == low_rule_one.count() == 3
    assert low_rule_zero.best(1)[0].weight != low_rule_one.best(1)[0].weight

    path = f"/chart/{S}/0/3/alternatives/"
    first_profile = GrammarChartProfile(low_rule_zero)
    second_profile = GrammarChartProfile(low_rule_one)
    first = tuple(
        addressed_value(first_profile, path + str(index)) for index in range(3)
    )
    second = tuple(
        addressed_value(second_profile, path + str(index)) for index in range(3)
    )
    assert all(isinstance(value, ItemRef) for value in first + second)
    first_keys = tuple(
        application_key(first_profile, value)
        for value in first
        if isinstance(value, ItemRef)
    )
    second_keys = tuple(
        application_key(second_profile, value)
        for value in second
        if isinstance(value, ItemRef)
    )
    assert (
        first_keys
        == second_keys
        == (
            (0, ((0, 1), (1, 3))),
            (0, ((0, 2), (2, 3))),
            (1, ()),
        )
    )

    # Reverse the real recognizer-produced incidence sequence. The profile must
    # still return stable-key order, not this snapshot's edge discovery order.
    reversed_graph = replace(
        low_rule_zero.graph, relations=tuple(reversed(low_rule_zero.graph.relations))
    )
    reversed_forest = replace(low_rule_zero, graph=reversed_graph)
    reversed_profile = GrammarChartProfile(reversed_forest)
    raw = tuple(
        edge.right
        for edge in reversed_graph.relations
        if edge.declaration.local_name == "alternatives"
        and edge.left == reversed_forest.root
    )
    addressed = tuple(
        addressed_value(reversed_profile, path + str(index)) for index in range(3)
    )
    assert addressed != raw
    assert (
        tuple(
            application_key(reversed_profile, value)
            for value in addressed
            if isinstance(value, ItemRef)
        )
        == first_keys
    )


def test_alternative_slot_legality_bounds_and_spelling() -> None:
    """Alternative slots neither coerce to items nor select beyond a snapshot."""
    forest = recognize(lower_grammar(ambiguous_grammar("1", "9")), ("x", "x", "x"))
    profile = GrammarChartProfile(forest)
    text = f"/chart/{S}/0/3/alternatives/0"
    resolved = resolve_path(forest.graph, profile, text, require=PathKind.ALTERNATIVE)
    assert isinstance(resolved, ResolvedAlternative)
    assert (
        profile.spell(
            AlternativeRef(resolved.owner, resolved.relation, 0), forest.graph
        )
        == resolved.path
    )
    with pytest.raises(PathRefusal) as wrong:
        resolve_path(forest.graph, profile, text, require=PathKind.ITEM)
    assert wrong.value.code is PathRefusalCode.WRONG_KIND
    with pytest.raises(PathRefusal) as out:
        resolve_path(forest.graph, profile, f"/chart/{S}/0/3/alternatives/3")
    assert out.value.code is PathRefusalCode.ALTERNATIVE_OUT_OF_RANGE
    assert out.value.offender.available_count == 3
    assert out.value.offender.index == 3


def test_chart_profile_refuses_outside_its_declared_snapshot_and_vocabulary() -> None:
    """The chart literal, lexical domains, snapshot, and relation are explicit."""
    forest = recognize(lower_grammar(ambiguous_grammar("1", "9")), ("x", "x", "x"))
    profile = GrammarChartProfile(forest)
    base = f"/chart/{S}/0/3/alternatives/0"
    cases = (
        ("/other", PathRefusalCode.UNKNOWN_FORM),
        (f"/chart/{S}/01/3/alternatives/0", PathRefusalCode.NONCANONICAL_SEGMENT),
        (f"/chart/{S}/zero/3/alternatives/0", PathRefusalCode.INVALID_SEGMENT),
        (f"/chart/{S}/0/4/alternatives/0", PathRefusalCode.PROFILE_REFUSED),
    )
    for text, code in cases:
        with pytest.raises(PathRefusal) as caught:
            resolve_path(forest.graph, profile, text)
        assert caught.value.code is code
    other_graph = replace(forest.graph)
    with pytest.raises(PathRefusal) as snapshot:
        profile.bind(
            profile.spell(
                profile.bind(CanonicalPath.parse(base), forest.graph), forest.graph
            ),
            other_graph,
        )
    assert snapshot.value.offender.profile_reason == "different_forest_snapshot"

    binding = profile.bind(CanonicalPath.parse(base), forest.graph)
    assert isinstance(binding, AlternativeRef)
    wrong_relation = QualifiedName(NS, "not-alternatives")
    with pytest.raises(PathRefusal) as spell_refusal:
        profile.spell(replace(binding, relation=wrong_relation), forest.graph)
    assert spell_refusal.value.offender.profile_reason == "unsupported_relation"
    with pytest.raises(PathRefusal) as binding_refusal:
        profile.spell(ItemBinding(forest.root), forest.graph)
    assert binding_refusal.value.offender.profile_reason == "unsupported_binding"
    with pytest.raises(PathRefusal) as relation_refusal:
        profile.alternatives(forest.root, wrong_relation, forest.graph)
    assert relation_refusal.value.offender.profile_reason == "unsupported_relation"


def test_generic_alternative_owner_failure_negative_bound_and_structural_guard() -> (
    None
):
    """Generic orchestration maps owner failures and checks both index bounds."""

    class AlternativeProfile:
        def __init__(self, binding: AlternativeRef) -> None:
            self.binding = binding

        def bind(self, path: CanonicalPath, graph: Graph) -> PathBinding:
            del path, graph
            return self.binding

        def spell(self, binding: PathBinding, graph: Graph) -> CanonicalPath:
            del binding, graph
            raise AssertionError("not reached")

        def alternatives(
            self, owner: ItemRef, relation: QualifiedName, graph: Graph
        ) -> tuple[object, ...]:
            del owner, relation, graph
            return ("only",)

    forest = recognize(lower_grammar(ambiguous_grammar("1", "9")), ("x", "x", "x"))
    relation = QualifiedName(forest.root.tier.namespace, "alternatives")
    with pytest.raises(PathRefusal) as owner:
        resolve_path(
            forest.graph,
            AlternativeProfile(
                AlternativeRef(ItemRef(forest.root.tier, 999), relation, 0)
            ),
            "/alternative",
        )
    assert owner.value.code is PathRefusalCode.OUT_OF_RANGE
    with pytest.raises(PathRefusal) as negative:
        resolve_path(
            forest.graph,
            AlternativeProfile(AlternativeRef(forest.root, relation, -1)),
            "/alternative",
        )
    assert negative.value.code is PathRefusalCode.ALTERNATIVE_OUT_OF_RANGE
    structural = StructuralPathProfile()
    with pytest.raises(PathRefusal) as unspellable:
        structural.spell(AlternativeRef(forest.root, relation, 0), forest.graph)
    assert unspellable.value.code is PathRefusalCode.UNSPELLABLE
    assert structural.alternatives(forest.root, relation, forest.graph) == ()


def _nullable_collision_grammar() -> GrammarDeclaration:
    """S -> A A B with a nullable A: two expansions project to identical children.

    Recognizing ("a","b") drops one A hole to epsilon two ways; both variants
    reduce to children (A@0:1, B@1:2), so their (rule, child-spans) keys collide.
    """
    a_x = GrammarHole(AttributeValue(VARIABLE, XsdType.STRING, "x"), A)
    a_y = GrammarHole(AttributeValue(VARIABLE, XsdType.STRING, "y"), A)
    b_z = GrammarHole(AttributeValue(VARIABLE, XsdType.STRING, "z"), B)
    return GrammarDeclaration(
        (S, A, B),
        S,
        (
            GrammarRule(S, (a_x, a_y, b_z), (a_x, a_y, b_z)),
            GrammarRule(A, (), ()),
            GrammarRule(A, (terminal("a"),), (terminal("a"),)),
            GrammarRule(B, (terminal("b"),), (terminal("b"),)),
        ),
    )


def test_colliding_stable_keys_order_canonically_not_by_incidence() -> None:
    """Two applications sharing the (rule, child-spans) key still get a canonical,
    incidence-independent order — the application tier index tiebreak — instead of
    falling back to graph.relations discovery order."""
    forest = recognize(lower_grammar(_nullable_collision_grammar()), ("a", "b"))
    profile = GrammarChartProfile(forest)
    relation = QualifiedName(forest.root.tier.namespace, "alternatives")
    ordered = profile.alternatives(forest.root, relation, forest.graph)
    apps = []
    for value in ordered:
        assert isinstance(value, ItemRef)
        apps.append(value)
    keys = [application_key(profile, app) for app in apps]
    # the collision the design must survive: >=2 applications, identical 2-tuple key
    assert len(keys) != len(set(keys))
    # reversing relation incidence must not change the addressed order
    reversed_graph = replace(
        forest.graph, relations=tuple(reversed(forest.graph.relations))
    )
    reversed_forest = replace(forest, graph=reversed_graph)
    reversed_relation = QualifiedName(
        reversed_forest.root.tier.namespace, "alternatives"
    )
    reversed_ordered = GrammarChartProfile(reversed_forest).alternatives(
        reversed_forest.root, reversed_relation, reversed_graph
    )
    assert reversed_ordered == ordered
    # spell is index-faithful, not hard-coded to 0
    assert (
        profile.spell(AlternativeRef(forest.root, relation, 1), forest.graph).segments[
            -1
        ]
        == "1"
    )


def test_grammar_profile_refuses_a_non_chart_item_owner() -> None:
    """An application item (wrong tier) as owner is refused, not silently ()'d or
    leaked as StopIteration."""
    forest = recognize(lower_grammar(ambiguous_grammar("1", "9")), ("x", "x", "x"))
    profile = GrammarChartProfile(forest)
    relation = QualifiedName(forest.root.tier.namespace, "alternatives")
    first = profile.alternatives(forest.root, relation, forest.graph)[0]
    assert isinstance(first, ItemRef)  # an application item, not a chart item
    with pytest.raises(PathRefusal) as enumerate_refusal:
        profile.alternatives(first, relation, forest.graph)
    assert enumerate_refusal.value.offender.profile_reason == "unsupported_owner"
    with pytest.raises(PathRefusal) as spell_refusal:
        profile.spell(AlternativeRef(first, relation, 0), forest.graph)
    assert spell_refusal.value.code is PathRefusalCode.UNSPELLABLE
    assert spell_refusal.value.offender.profile_reason == "unsupported_owner"
