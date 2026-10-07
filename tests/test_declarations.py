"""Exercise positional declaration editing and strict declaration removal."""

from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from tests.test_clock import BINDING, CLOCK, RATE, SEGMENT, SEGMENTS, UNIT
from tests.test_clock import fixture as clock_fixture
from tests.test_clock import fixture_with_parent as clock_fixture_with_parent
from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    Boundary,
    BoundaryRef,
    BoundarySide,
    ClockEditOperation,
    ClockProfile,
    ClockRebindingPolicy,
    DocumentRef,
    DurableBoundaryRef,
    DurableItemRef,
    DurablePolyadicRef,
    DurableRelationRef,
    EquivalenceView,
    Graph,
    GraphCarrier,
    GraphEditor,
    GraphValidationError,
    Item,
    ItemRef,
    Layer,
    LayerFact,
    LayerName,
    NamespaceDeclaration,
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
    dump_bytes,
    equivalent,
    undeclare_with_contents,
)


@dataclass(frozen=True)
class DeclarationFixture:
    """Hold the declarations and graph used by one domain fixture."""

    graph: Graph
    namespace: NamespaceDeclaration
    unit: QualifiedName
    unit_declaration: TierDeclaration
    members: SimpleRelationDeclaration
    link: BipartiteRelationDeclaration
    group: PolyadicRelationDeclaration
    choice: PolyadicRelationDeclaration
    item_label: AttributeDeclaration
    boundary_label: AttributeDeclaration
    relation_label: AttributeDeclaration
    declaration_label: AttributeDeclaration
    tier_label: AttributeDeclaration


def declaration_fixture(domain: str) -> DeclarationFixture:
    """Return equivalent text or music declaration dependency shapes."""
    namespace = NamespaceDeclaration("d", f"urn:declaration-edit:{domain}")

    def name(local: str) -> QualifiedName:
        return QualifiedName(namespace.namespace, local)

    unit = name("token" if domain == "text" else "note")
    unit_declaration = TierDeclaration(unit, "Tokens" if domain == "text" else "Notes")
    item_type = name("Token" if domain == "text" else "Note")
    members = SimpleRelationDeclaration(name("members"), unit, item_type)
    declaration_label = AttributeDeclaration(
        name("declaration-label"),
        AttributeDomain.RELATION_DECLARATION,
        XsdType.STRING,
    )
    link = BipartiteRelationDeclaration(
        name("next"),
        item_type,
        item_type,
        attributes=(AttributeValue(declaration_label.name, XsdType.STRING, "ordered"),),
    )
    side = RelationSideDeclaration((RelationEndpointKind.ITEM,), (unit,))
    group = PolyadicRelationDeclaration(name("group"), side, side)
    choice = PolyadicRelationDeclaration(
        name("choice"), side, side, targets_subset_of=group.name
    )
    item_label = AttributeDeclaration(
        name("item-label"), AttributeDomain.ITEM, XsdType.STRING
    )
    boundary_label = AttributeDeclaration(
        name("boundary-label"), AttributeDomain.BOUNDARY, XsdType.STRING
    )
    relation_label = AttributeDeclaration(
        name("relation-label"), AttributeDomain.RELATION_INSTANCE, XsdType.STRING
    )
    tier_label = AttributeDeclaration(
        name("tier-label"), AttributeDomain.TIER, XsdType.STRING
    )
    items = (
        Item(
            "unit-0",
            (AttributeValue(item_label.name, XsdType.STRING, "first"),),
        ),
        Item("unit-1"),
    )
    binary = RelationInstance(
        link.name,
        ItemRef(unit, 0),
        ItemRef(unit, 1),
        "link-0",
        (AttributeValue(relation_label.name, XsdType.STRING, "forward"),),
    )
    polyadic = PolyadicRelationInstance(
        group.name,
        (ItemRef(unit, 0),),
        (ItemRef(unit, 1),),
        "group-0",
        (AttributeValue(relation_label.name, XsdType.STRING, "member"),),
    )
    layer_name = LayerName(namespace.namespace, "manual")
    facts = (
        LayerFact(
            ItemRef(unit, 0),
            AttributeValue(item_label.name, XsdType.STRING, "checked"),
        ),
        LayerFact(
            DurableRelationRef("link-0"),
            AttributeValue(relation_label.name, XsdType.STRING, "verified"),
        ),
        LayerFact(
            RelationDeclarationRef(link.name),
            AttributeValue(declaration_label.name, XsdType.STRING, "reviewed"),
        ),
        LayerFact(
            TierRef(unit),
            AttributeValue(tier_label.name, XsdType.STRING, "primary"),
        ),
    )
    graph = Graph(
        (namespace,),
        (
            Tier(
                unit_declaration,
                items,
                (AttributeValue(tier_label.name, XsdType.STRING, "ordered"),),
            ),
        ),
        (members, link, group, choice),
        (binary,),
        (
            item_label,
            boundary_label,
            relation_label,
            declaration_label,
            tier_label,
        ),
        (
            Boundary(
                BoundaryRef(unit, 1),
                (AttributeValue(boundary_label.name, XsdType.STRING, "interior"),),
            ),
        ),
        polyadic_relations=(polyadic,),
        seals=(
            Seal(unit, 1),
            Seal(GraphCarrier.RELATIONS, 1),
            Seal(GraphCarrier.POLYADIC_RELATIONS, 1),
        ),
        layers=(Layer(layer_name, facts),),
    )
    return DeclarationFixture(
        graph,
        namespace,
        unit,
        unit_declaration,
        members,
        link,
        group,
        choice,
        item_label,
        boundary_label,
        relation_label,
        declaration_label,
        tier_label,
    )


@pytest.mark.parametrize("domain", ["text", "music"])
def test_declare_then_undeclare_is_exact_for_every_kind(domain: str) -> None:
    """Every declaration carrier accepts a position and has an exact inverse."""
    case = declaration_fixture(domain)
    base = Graph(
        case.graph.namespaces,
        (),
        (),
        attribute_declarations=(),
    )
    extra_namespace = NamespaceDeclaration("extra", f"urn:extra:{domain}")
    empty_tier = TierDeclaration(case.unit, case.unit_declaration.long_name)
    attribute = case.item_label
    simple = case.members

    for declaration in (extra_namespace, empty_tier, attribute):
        changed = base.declare(declaration, at=0)
        assert changed.undeclare(declaration) == base

        editor = base.edit()
        editor.declare(declaration, at=0).undeclare(declaration)
        assert editor.freeze() == base

    typed = base.declare(empty_tier)
    changed = typed.declare(simple, at=0)
    assert changed.undeclare(simple) == typed

    linked = typed.declare(simple)
    empty_link = replace(case.link, attributes=())
    changed = linked.declare(empty_link, at=0)
    assert changed.undeclare(empty_link) == linked

    side = RelationSideDeclaration((RelationEndpointKind.ITEM,), (case.unit,))
    polyadic = PolyadicRelationDeclaration(
        QualifiedName(case.namespace.namespace, "unused"), side, side
    )
    changed = linked.declare(polyadic, at=0)
    assert changed.undeclare(polyadic) == linked


def test_undeclare_then_positional_declare_restores_exact_graph() -> None:
    """A removed declaration and its carrier index restore exact graph bytes."""
    namespace = NamespaceDeclaration("z", "urn:declaration-position:z")
    first_name = QualifiedName(namespace.namespace, "first")
    second_name = QualifiedName(namespace.namespace, "second")
    first = TierDeclaration(first_name, "First")
    second = TierDeclaration(second_name, "Second")
    attribute = AttributeDeclaration(
        QualifiedName(namespace.namespace, "unused"),
        AttributeDomain.ITEM,
        XsdType.STRING,
    )
    graph = Graph(
        (namespace,),
        (Tier(first), Tier(second)),
        (),
        attribute_declarations=(attribute,),
    )

    without_tier = graph.undeclare(first)
    assert without_tier.declare(first, at=0) == graph
    without_attribute = graph.undeclare(attribute)
    assert without_attribute.declare(attribute, at=0) == graph
    without_namespace = Graph((namespace,), (), ()).undeclare(namespace)
    assert without_namespace.declare(namespace, at=0) == Graph((namespace,), (), ())


@pytest.mark.parametrize("at", [-1, 1, True, "0"])
def test_declare_refuses_invalid_positions_before_writing(at: object) -> None:
    """Every bad declaration position leaves the editor and displacement intact."""
    graph = Graph((), (), ())
    editor = graph.edit()
    displacement = editor.displacement()
    with pytest.raises(GraphValidationError, match="insertion index"):
        editor.declare(NamespaceDeclaration("n", "urn:n"), at=at)  # type: ignore[arg-type]
    assert editor.freeze() == graph
    assert editor.displacement() is displacement


def test_editor_declare_refuses_every_duplicate_before_writing() -> None:
    """Duplicate declaration names do not poison a reusable mutable editor."""
    case = declaration_fixture("text")
    duplicates = (
        NamespaceDeclaration(case.namespace.prefix, "urn:other"),
        NamespaceDeclaration("other", case.namespace.namespace),
        replace(case.unit_declaration, long_name="Duplicate"),
        replace(case.item_label, domain=AttributeDomain.TIER),
        replace(case.members, tier=QualifiedName(case.namespace.namespace, "other")),
    )
    for duplicate in duplicates:
        editor = case.graph.edit()
        before = dump_bytes(editor.freeze())
        displacement = editor.displacement()
        with pytest.raises(GraphValidationError, match="duplicate"):
            editor.declare(duplicate)
        assert dump_bytes(editor.freeze()) == before
        assert editor.displacement() is displacement


def test_undeclare_selector_refusals_are_local_and_disambiguated() -> None:
    """Missing, malformed, and cross-kind ambiguous selectors never write."""
    namespace = NamespaceDeclaration("n", "urn:ambiguous")
    name = QualifiedName(namespace.namespace, "same")
    tier = TierDeclaration(name, "Same")
    attribute = AttributeDeclaration(name, AttributeDomain.ITEM, XsdType.STRING)
    graph = Graph((namespace,), (Tier(tier),), (), attribute_declarations=(attribute,))
    editor = graph.edit()
    before = dump_bytes(editor.freeze())
    displacement = editor.displacement()

    for target, message in (
        (name, "ambiguous across tier, attribute"),
        (QualifiedName(namespace.namespace, "missing"), "no declaration named"),
        (object(), "undeclare expected"),
    ):
        with pytest.raises(GraphValidationError, match=message):
            editor.undeclare(target)  # type: ignore[arg-type]
        assert dump_bytes(editor.freeze()) == before
        assert editor.displacement() is displacement

    assert graph.undeclare(attribute).tiers == graph.tiers
    with pytest.raises(GraphValidationError, match="no namespace prefix 'same'"):
        graph.undeclare("same")


def test_name_selector_and_nonmatching_dependencies_cover_mixed_namespaces() -> None:
    """A prefix selects namespaces while unrelated graph content is ignored."""
    case = declaration_fixture("text")
    unused = NamespaceDeclaration("unused", "urn:unused")
    extended = case.graph.declare(unused, at=0)
    assert extended.undeclare("unused") == case.graph

    empty = TierDeclaration(
        QualifiedName(case.namespace.namespace, "unused-tier"), "Unused tier"
    )
    with_empty = case.graph.declare(empty, at=0)
    assert with_empty.undeclare(empty) == case.graph
    assert undeclare_with_contents(with_empty, empty) == case.graph

    target_tier = TierDeclaration(
        QualifiedName(unused.namespace, "target-tier"), "Target tier"
    )
    target_attribute = AttributeDeclaration(
        QualifiedName(unused.namespace, "target-attribute"),
        AttributeDomain.ITEM,
        XsdType.STRING,
    )
    mixed = (
        case.graph.declare(unused).declare(target_tier, at=0).declare(target_attribute)
    )
    assert undeclare_with_contents(mixed, unused) == case.graph


@pytest.mark.parametrize("domain", ["text", "music"])
def test_undeclare_lists_all_dependents_stably_before_writing(domain: str) -> None:
    """Tier and declaration refusals aggregate stable, inspectable dependencies."""
    case = declaration_fixture(domain)
    editor = case.graph.edit()
    before = dump_bytes(editor.freeze())
    displacement = editor.displacement()

    messages = []
    for _ in range(2):
        with pytest.raises(GraphValidationError) as caught:
            editor.undeclare(case.unit_declaration)
        messages.append(str(caught.value))
    assert messages[0] == messages[1]
    for dependent in (
        "item ",
        "relation declaration",
        "relation instance 0",
        "polyadic relation instance 0",
        "boundary value",
        "layer fact",
        "seal on",
    ):
        assert dependent in messages[0]
    assert dump_bytes(editor.freeze()) == before
    assert editor.displacement() is displacement

    with pytest.raises(GraphValidationError) as item_type_refusal:
        case.graph.undeclare(case.members)
    assert str(case.link.name) in str(item_type_refusal.value)

    with pytest.raises(GraphValidationError) as subset_refusal:
        case.graph.undeclare(case.group)
    assert str(case.choice.name) in str(subset_refusal.value)

    with pytest.raises(GraphValidationError) as link_refusal:
        case.graph.undeclare(case.link)
    assert "seal on 'relations'" in str(link_refusal.value)
    assert "layer fact" in str(link_refusal.value)

    with pytest.raises(GraphValidationError) as attribute_refusal:
        case.graph.undeclare(case.relation_label)
    assert "relation instance 0" in str(attribute_refusal.value)
    assert "layer fact" in str(attribute_refusal.value)

    with pytest.raises(GraphValidationError) as namespace_refusal:
        case.graph.undeclare(case.namespace)
    assert "tier declaration" in str(namespace_refusal.value)
    assert "attribute declaration" in str(namespace_refusal.value)


def test_clock_binding_is_reported_as_a_tier_dependency() -> None:
    """A timed tier cannot be undeclared while its clock bindings remain."""
    graph = clock_fixture()
    editor = graph.edit()
    before = dump_bytes(graph)
    with pytest.raises(GraphValidationError) as caught:
        editor.undeclare(SEGMENT)
    assert "relation instance 0" in str(caught.value)
    assert dump_bytes(editor.freeze()) == before


@pytest.mark.parametrize("target", (SEGMENTS, SEGMENT, CLOCK, BINDING))
def test_clock_cascade_requires_a_policy_before_writing(target: QualifiedName) -> None:
    """Every timing-affecting declaration cascade is a clock-policy preflight."""
    graph = clock_fixture()
    editor = ClockProfile(graph, CLOCK, BINDING, RATE, UNIT).edit()
    with pytest.raises(GraphValidationError, match="requires a named rebinding policy"):
        editor.undeclare_with_contents(target)
    assert editor.freeze() is graph
    assert editor.reports == ()


@pytest.mark.parametrize("policy", tuple(ClockRebindingPolicy))
def test_named_clock_cascade_reports_withdrawals_and_retires_profile(
    policy: ClockRebindingPolicy,
) -> None:
    """A named policy makes destructive timing withdrawal explicit and terminal."""
    graph = clock_fixture()
    editor = ClockProfile(graph, CLOCK, BINDING, RATE, UNIT).edit(policy)
    assert editor.undeclare_with_contents(SEGMENT) is editor
    changed = editor.freeze()
    assert all(tier.declaration.name != SEGMENT for tier in changed.tiers)
    assert editor.reports == (editor.reports[0],)
    report = editor.reports[0]
    assert report.operation is ClockEditOperation.DECLARATION_CASCADE
    assert report.policy is policy
    assert report.tier == SEGMENT
    assert len(report.changes) == 3
    assert all(change.boundary is None for change in report.changes)
    assert report.needs_realignment is False
    with pytest.raises(GraphValidationError, match="profile was retired"):
        _ = editor.profile
    with pytest.raises(GraphValidationError, match="profile was retired"):
        editor.remove_items(CLOCK, 0, 1)


def test_clock_cascade_without_timing_impact_keeps_the_session_active() -> None:
    """Unrelated declarations retain the validated profile without a policy."""
    unused = NamespaceDeclaration("unused", "urn:clock-unused")
    graph = clock_fixture_with_parent().declare(unused)
    editor = ClockProfile(graph, CLOCK, BINDING, RATE, UNIT).edit()
    editor.undeclare_with_contents(unused)
    assert editor.profile.graph is editor.freeze()
    assert editor.reports == ()


def test_clock_cascade_refuses_non_rebinding_profile_damage() -> None:
    """Removing another required profile role is outside rebinding policy scope."""
    graph = clock_fixture()
    editor = ClockProfile(graph, CLOCK, BINDING, RATE, UNIT).edit("keep-earlier")
    with pytest.raises(
        GraphValidationError, match="would invalidate the active clock profile"
    ):
        editor.undeclare_with_contents(UNIT)
    assert editor.freeze() is graph
    assert editor.reports == ()


def test_clock_only_cascade_still_recognizes_the_binding_contract() -> None:
    """A binding declaration is timing structure even before any tier uses it."""
    graph = clock_fixture()
    clock = next(tier for tier in graph.tiers if tier.declaration.name == CLOCK)
    binding = next(
        declaration
        for declaration in graph.relation_declarations
        if declaration.name == BINDING
    )
    clock_only = replace(
        graph,
        tiers=(clock,),
        relation_declarations=(binding,),
        relations=(),
    )
    editor = ClockProfile(clock_only, CLOCK, BINDING, RATE, UNIT).edit()
    with pytest.raises(GraphValidationError, match="requires a named rebinding policy"):
        editor.undeclare_with_contents(BINDING)
    assert editor.freeze() is clock_only


@pytest.mark.parametrize("domain", ["text", "music"])
def test_undeclare_with_contents_is_atomic_and_functionally_reversible(
    domain: str,
) -> None:
    """The derived tier cascade can be replayed backward on two domains."""
    case = declaration_fixture(domain)
    graph = case.graph
    removed = undeclare_with_contents(graph, case.unit_declaration)
    assert removed.tiers == ()
    assert removed.relation_declarations == ()
    assert removed.relations == ()
    assert removed.polyadic_relations == ()
    assert removed.boundary_values == ()
    assert removed.seals == ()
    assert removed.layers[0].facts == ()
    assert dump_bytes(graph) == dump_bytes(case.graph)

    editor = removed.edit()
    editor.declare(case.unit_declaration, at=0)
    editor.insert_items(case.unit, 0, graph.tiers[0].items)
    for value in graph.tiers[0].attributes:
        editor.set_attribute(case.unit, value)
    for declaration_index, declaration in enumerate(graph.relation_declarations):
        editor.declare(declaration, at=declaration_index)
    for boundary in graph.boundary_values:
        for value in boundary.attributes:
            editor.set_attribute(boundary.reference, value)
    for index, binary_relation in enumerate(graph.relations):
        editor.add_relation(binary_relation, at=index)
    for index, polyadic_relation in enumerate(graph.polyadic_relations):
        editor.add_relation(polyadic_relation, at=index)
    for layer in graph.layers:
        for fact in layer.facts:
            editor.put_fact(layer.name, fact)
    for seal in graph.seals:
        editor.seal(seal.carrier, seal.sealed)
    restored = editor.freeze()
    assert equivalent(restored, graph, EquivalenceView.FUNCTIONAL)


def test_plain_clock_cascade_explicitly_bypasses_rebinding() -> None:
    """The graph-level escape hatch destructively removes timing without a report."""
    graph = clock_fixture()
    changed = undeclare_with_contents(graph, SEGMENTS)
    assert changed.relations == ()
    assert all(
        declaration.name != BINDING for declaration in changed.relation_declarations
    )
    with pytest.raises(ValueError, match="clock binding .* is not declared"):
        ClockProfile(changed, CLOCK, BINDING, RATE, UNIT)


def test_relation_and_attribute_cascades_remove_only_their_contents() -> None:
    """The derived operation is available for non-tier declarations too."""
    case = declaration_fixture("text")
    without_link = undeclare_with_contents(case.graph, case.link)
    assert case.link not in without_link.relation_declarations
    assert without_link.relations == ()
    assert {fact.value.name for fact in without_link.layers[0].facts} == {
        case.item_label.name,
        case.tier_label.name,
    }

    without_label = undeclare_with_contents(case.graph, case.item_label)
    assert case.item_label not in without_label.attribute_declarations
    assert without_label.tiers[0].items[0].attributes == ()
    assert all(
        fact.value.name != case.item_label.name
        for fact in without_label.layers[0].facts
    )

    without_tier_label = undeclare_with_contents(case.graph, case.tier_label)
    assert without_tier_label.tiers[0].attributes == ()
    without_boundary_label = undeclare_with_contents(case.graph, case.boundary_label)
    assert without_boundary_label.boundary_values == ()
    without_relation_label = undeclare_with_contents(case.graph, case.relation_label)
    assert without_relation_label.relations[0].attributes == ()
    assert without_relation_label.polyadic_relations[0].attributes == ()
    without_declaration_label = undeclare_with_contents(
        case.graph, case.declaration_label
    )
    assert without_declaration_label.relation_declarations[1].attributes == ()

    document_label = AttributeDeclaration(
        QualifiedName(case.namespace.namespace, "document-label"),
        AttributeDomain.DOCUMENT,
        XsdType.STRING,
    )
    with_document_value = case.graph.declare(document_label).set_attribute(
        None, AttributeValue(document_label.name, XsdType.STRING, "document")
    )
    without_document_label = undeclare_with_contents(
        with_document_value, document_label
    )
    assert without_document_label.attributes == ()


def test_namespace_cascade_handles_declarations_removed_by_other_declarations() -> None:
    """A namespace cascade visits a snapshot without revisiting removed entries."""
    case = declaration_fixture("music")
    result = undeclare_with_contents(case.graph, case.namespace)
    assert result.namespaces == ()
    assert result.tiers == ()
    assert result.relation_declarations == ()
    assert result.attribute_declarations == ()
    assert result.layers[0].facts == ()


def test_graph_editor_type_is_the_mutating_surface() -> None:
    """The mutable form keeps fluent return values for declaration edits."""
    editor = Graph((), (), ()).edit()
    declaration = NamespaceDeclaration("n", "urn:n")
    assert editor.declare(declaration) is editor
    assert editor.undeclare(declaration) is editor
    assert isinstance(editor, GraphEditor)


def test_cyclic_declaration_cascade_removes_the_component_atomically() -> None:
    """A cascade removes mutually dependent declarations as one component."""
    namespace = NamespaceDeclaration("n", "urn:declaration-cycle")
    left_name = QualifiedName(namespace.namespace, "left")
    right_name = QualifiedName(namespace.namespace, "right")
    side = RelationSideDeclaration((RelationEndpointKind.ITEM,))
    left = PolyadicRelationDeclaration(
        left_name, side, side, targets_subset_of=right_name
    )
    right = PolyadicRelationDeclaration(
        right_name, side, side, targets_subset_of=left_name
    )
    graph = Graph((namespace,), (), (left, right))
    before = dump_bytes(graph)
    changed = undeclare_with_contents(graph, left)
    assert changed.relation_declarations == ()
    assert dump_bytes(graph) == before

    editor = graph.edit()
    left_key = ("relation", repr(str(left.name)))
    with pytest.raises(GraphValidationError, match="dependency cycle reaches"):
        core._cascade_undeclare(editor, left, {left_key})
    component = core._relation_declaration_component(editor, left)
    with pytest.raises(GraphValidationError, match="dependency cycle reaches"):
        core._cascade_relation_component(editor, component, {left_key})
    assert editor.freeze() == graph


def test_dependency_helpers_cover_every_reference_spelling() -> None:
    """Dependency preflights resolve structural, durable, and orphan spellings."""
    namespace = "urn:dependency-spellings"
    tier = QualifiedName(namespace, "tier")
    item = ItemRef(tier, 0)
    items_by_id = {"item": item}
    durable_item = DurableItemRef("item")
    missing_item = DurableItemRef("missing")
    tier_boundary = DurableBoundaryRef(tier, BoundarySide.BEFORE)
    item_boundary = DurableBoundaryRef(durable_item, BoundarySide.AFTER)

    assert core._endpoint_tier(durable_item, items_by_id) == tier
    assert core._endpoint_tier(missing_item, items_by_id) is None
    assert core._endpoint_tier(tier_boundary, items_by_id) == tier
    assert core._endpoint_tier(item_boundary, items_by_id) == tier
    assert (
        core._endpoint_tier(
            DurableBoundaryRef(missing_item, BoundarySide.AFTER), items_by_id
        )
        is None
    )
    assert core._boundary_tier(tier_boundary, items_by_id) == tier
    assert core._boundary_tier(item_boundary, items_by_id) == tier
    assert (
        core._boundary_tier(
            DurableBoundaryRef(missing_item, BoundarySide.AFTER), items_by_id
        )
        is None
    )

    assert core._endpoint_uses_namespace(item, namespace)
    assert core._endpoint_uses_namespace(tier_boundary, namespace)
    assert not core._endpoint_uses_namespace(durable_item, namespace)
    assert core._boundary_uses_namespace(tier_boundary, namespace)
    assert not core._boundary_uses_namespace(item_boundary, namespace)

    orphan = core.OrphanedSubject(tier, item)
    assert core._layer_subject_names(tier_boundary) == (tier,)
    assert core._layer_subject_names(orphan) == (tier, tier)
    assert core._layer_subject_names(DocumentRef()) == ()
    assert core._layer_subject_uses_tier(durable_item, tier, items_by_id)
    assert not core._layer_subject_uses_tier(missing_item, tier, items_by_id)
    assert core._layer_subject_uses_tier(item_boundary, tier, items_by_id)
    assert core._layer_subject_uses_tier(orphan, tier, items_by_id)

    assert core._layer_subject_uses_relation_instances(
        RelationInstanceRef(1), {1}, set(), set(), set()
    )
    assert core._layer_subject_uses_relation_instances(
        PolyadicInstanceRef(2), set(), {2}, set(), set()
    )
    assert core._layer_subject_uses_relation_instances(
        DurableRelationRef("binary"), set(), set(), {"binary"}, set()
    )
    assert core._layer_subject_uses_relation_instances(
        DurablePolyadicRef("polyadic"), set(), set(), set(), {"polyadic"}
    )
    assert not core._layer_subject_uses_relation_instances(
        DocumentRef(), set(), set(), set(), set()
    )
