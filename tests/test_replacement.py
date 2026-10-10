"""Exercise guarded subtree replacement across three domain organizations."""

from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

import tiergraph.clock as clock_module
import tiergraph.replacement as replacement
from tests import test_clock
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
    DetachedDependency,
    DocumentRef,
    DurableBoundaryRef,
    DurableItemRef,
    DurablePolyadicRef,
    DurableRelationRef,
    EquivalenceView,
    Graph,
    GraphValidationError,
    Item,
    ItemRef,
    Journal,
    Layer,
    LayerFact,
    LayerName,
    NamespaceDeclaration,
    OrphanedSubject,
    PolyadicInstanceRef,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    QualifiedName,
    RefusalStage,
    RelationEndpointKind,
    RelationInstance,
    RelationInstanceRef,
    RelationSideDeclaration,
    ReplacementAction,
    ReplacementPolicies,
    SimpleRelationDeclaration,
    Subtree,
    SubtreeCorrespondence,
    Tier,
    TierDeclaration,
    XsdType,
    anchored_boundary,
    equivalent,
    replace_subtree,
    swap_subtrees,
)


@dataclass(frozen=True)
class Case:
    graph: Graph
    alternative: Graph
    root: QualifiedName
    middle: QualifiedName
    leaf: QualifiedName
    containment: tuple[QualifiedName, QualifiedName]
    link: QualifiedName
    group: QualifiedName
    layer: LayerName
    note: QualifiedName
    edge: QualifiedName


def tts_replacement_profile() -> ReplacementPolicies:
    """Use correspondence by default for the speech-processing fixture."""
    return ReplacementPolicies.corresponding()


def drop_crossings(
    case: Case, policies: ReplacementPolicies | None = None
) -> ReplacementPolicies:
    """Name DROP for fixture crossings irrelevant to the test at hand."""
    chosen = (
        ReplacementPolicies(ReplacementAction.DROP) if policies is None else policies
    )
    return replace(
        chosen,
        relations={
            **chosen.relations,
            case.link: ReplacementAction.DROP,
            case.group: ReplacementAction.DROP,
        },
    )


def fixture(domain: str) -> Case:
    """Build parallel speech, text, or music containment with cross-links."""
    namespace = f"urn:tiergraph:replacement:{domain}"

    def name(local: str) -> QualifiedName:
        return QualifiedName(namespace, local)

    root = name({"speech": "word", "text": "page", "music": "measure"}[domain])
    middle = name({"speech": "syllable", "text": "sentence", "music": "voice"}[domain])
    leaf = name({"speech": "segment", "text": "line", "music": "note"}[domain])
    root_type, middle_type, leaf_type = (name("Root"), name("Middle"), name("Leaf"))
    root_members, middle_members, leaf_members = (
        name("roots"),
        name("middles"),
        name("leaves"),
    )
    upper, lower = name("upper-containment"), name("lower-containment")
    link, group = name("cross-link"), name("group")
    note, edge = name("note"), name("edge")
    layer = LayerName(namespace, "hand")
    side = RelationSideDeclaration(
        (RelationEndpointKind.ITEM,), (middle,), maximum=None
    )
    declarations: tuple[
        SimpleRelationDeclaration
        | BipartiteRelationDeclaration
        | PolyadicRelationDeclaration,
        ...,
    ] = (
        SimpleRelationDeclaration(root_members, root, root_type),
        SimpleRelationDeclaration(middle_members, middle, middle_type),
        SimpleRelationDeclaration(leaf_members, leaf, leaf_type),
        BipartiteRelationDeclaration(
            upper, root_type, middle_type, single_parent=True, acyclic=True
        ),
        BipartiteRelationDeclaration(
            lower, middle_type, leaf_type, single_parent=True, acyclic=True
        ),
        BipartiteRelationDeclaration(link, root_type, middle_type),
        PolyadicRelationDeclaration(group, side, side),
    )
    attribute_declarations = (
        AttributeDeclaration(note, AttributeDomain.ITEM, XsdType.STRING),
        AttributeDeclaration(edge, AttributeDomain.BOUNDARY, XsdType.STRING),
    )
    graph = Graph(
        (NamespaceDeclaration("r", namespace),),
        (
            Tier(TierDeclaration(root, root.local_name), (Item("root"), Item("other"))),
            Tier(
                TierDeclaration(middle, middle.local_name),
                (Item("middle-0"), Item("middle-1"), Item("middle-outside")),
            ),
            Tier(
                TierDeclaration(leaf, leaf.local_name),
                (Item("leaf-0"), Item("leaf-1"), Item("leaf-2"), Item("leaf-outside")),
            ),
        ),
        declarations,
        (
            RelationInstance(upper, ItemRef(root, 0), ItemRef(middle, 0)),
            RelationInstance(upper, ItemRef(root, 0), ItemRef(middle, 1)),
            RelationInstance(lower, ItemRef(middle, 0), ItemRef(leaf, 0)),
            RelationInstance(lower, ItemRef(middle, 0), ItemRef(leaf, 1)),
            RelationInstance(lower, ItemRef(middle, 1), ItemRef(leaf, 2)),
            RelationInstance(link, ItemRef(root, 1), ItemRef(middle, 0), "link"),
        ),
        attribute_declarations,
        (
            Boundary(
                BoundaryRef(leaf, 1),
                (AttributeValue(edge, XsdType.STRING, "aligned"),),
            ),
        ),
        polyadic_relations=(
            PolyadicRelationInstance(
                group,
                (ItemRef(middle, 2),),
                (ItemRef(middle, 0), ItemRef(middle, 1)),
                "group",
            ),
        ),
        layers=(
            Layer(
                layer,
                (
                    LayerFact(
                        DurableItemRef("middle-0"),
                        AttributeValue(note, XsdType.STRING, "verified"),
                    ),
                    LayerFact(
                        DurableItemRef("root"),
                        AttributeValue(note, XsdType.STRING, "source-span"),
                    ),
                ),
            ),
        ),
    )
    alternative = Graph(
        graph.namespaces,
        (
            Tier(TierDeclaration(root, root.local_name), (Item("new-root"),)),
            Tier(TierDeclaration(middle, middle.local_name), (Item("new-middle"),)),
            Tier(
                TierDeclaration(leaf, leaf.local_name),
                (Item("new-leaf-0"), Item("new-leaf-1")),
            ),
        ),
        declarations,
        (
            RelationInstance(upper, ItemRef(root, 0), ItemRef(middle, 0)),
            RelationInstance(lower, ItemRef(middle, 0), ItemRef(leaf, 0)),
            RelationInstance(lower, ItemRef(middle, 0), ItemRef(leaf, 1)),
        ),
        attribute_declarations,
    )
    return Case(
        graph,
        alternative,
        root,
        middle,
        leaf,
        (upper, lower),
        link,
        group,
        layer,
        note,
        edge,
    )


@pytest.mark.parametrize("domain", ("speech", "text", "music"))
def test_abandon_replaces_descendants_reports_dependencies_and_undoes(
    domain: str,
) -> None:
    """The cheap default keeps the root and abandons every descendant dependency."""
    case = fixture(domain)
    source = Subtree(case.alternative, ItemRef(case.root, 0))
    edit_result = replace_subtree(
        case.graph,
        DurableItemRef("root"),
        case.containment,
        source,
        drop_crossings(case),
    )
    expected = edit_result.graph
    assert tuple(item.durable_id for _, item in edit_result.report.items) == (
        "middle-0",
        "middle-1",
        "leaf-0",
        "leaf-1",
        "leaf-2",
    )
    assert {relation.durable_id for _, relation in edit_result.report.relations} >= {
        "link",
        "group",
    }
    assert edit_result.report.facts
    assert edit_result.report.boundary_values == (
        (
            BoundaryRef(case.leaf, 1),
            AttributeValue(case.edge, XsdType.STRING, "aligned"),
        ),
    )
    assert edit_result.report.to_data()["boundary_values"] == [
        {
            "reference": BoundaryRef(case.leaf, 1).to_data(),
            "value": AttributeValue(case.edge, XsdType.STRING, "aligned").to_data(),
        }
    ]
    assert tuple(
        item.durable_id for item in expected._tiers_by_name[case.middle].items
    ) == (
        "new-middle",
        "middle-outside",
    )
    assert tuple(
        item.durable_id for item in expected._tiers_by_name[case.leaf].items
    ) == (
        "new-leaf-0",
        "new-leaf-1",
        "leaf-outside",
    )
    assert all(relation.declaration != case.link for relation in expected.relations)
    assert not expected.polyadic_relations
    output_layer = next(layer for layer in expected.layers if layer.name == case.layer)
    assert (
        LayerFact(
            DurableItemRef("root"),
            AttributeValue(case.note, XsdType.STRING, "source-span"),
        )
        in output_layer.facts
    )
    journal = Journal(reason="replace")
    editor = case.graph.edit(journal=journal)
    editor.replace_subtree(
        DurableItemRef("root"), case.containment, source, drop_crossings(case)
    )
    assert editor.freeze() == expected
    report = journal.records[0].report
    assert report.detached_content == edit_result.report
    assert {item.carrier for item in report.detached_dependencies} == {
        "boundary_values",
        "layer",
        "polyadic_relations",
        "relations",
    }
    assert report.to_data()["detached_dependencies"]
    plain_editor = case.graph.edit()
    assert plain_editor.last_detachment is None
    returned_editor = plain_editor.replace_subtree(
        DurableItemRef("root"), case.containment, source, drop_crossings(case)
    )
    assert returned_editor is plain_editor
    assert plain_editor.freeze() == expected
    assert plain_editor.last_detachment == edit_result.report
    editor.undo()
    assert editor.freeze() == case.graph
    editor.redo()
    assert editor.freeze() == expected


def test_donor_crossings_and_their_facts_are_snapshotted() -> None:
    """Rejected donor relations and provenance are complete report content."""
    case = fixture("speech")
    namespace = case.root.namespace
    token = QualifiedName(namespace, "token")
    token_members = QualifiedName(namespace, "tokens")
    token_type = QualifiedName(namespace, "Token")
    token_to_word = QualifiedName(namespace, "token-to-word")
    token_words = QualifiedName(namespace, "token-words")
    relation_note = QualifiedName(namespace, "donor-relation-note")
    donor_layer = LayerName(namespace, "donor-source")
    word_type = next(
        declaration.item_type
        for declaration in case.alternative.relation_declarations
        if isinstance(declaration, SimpleRelationDeclaration)
        and declaration.tier == case.root
    )
    donor_link_index = len(case.alternative.relations)
    donor_link = RelationInstance(
        token_to_word,
        ItemRef(token, 0),
        ItemRef(case.root, 0),
        "donor-token-to-word",
    )
    donor_group_index = len(case.alternative.polyadic_relations)
    donor_group = PolyadicRelationInstance(
        token_words,
        (ItemRef(token, 0),),
        (ItemRef(case.root, 0),),
        "donor-token-words",
    )
    donor_fact = LayerFact(
        DurableRelationRef("donor-token-to-word"),
        AttributeValue(relation_note, XsdType.STRING, "source-offset:4"),
    )
    donor = replace(
        case.alternative,
        tiers=(
            *case.alternative.tiers,
            Tier(TierDeclaration(token, "token"), (Item("outside-token"),)),
        ),
        relation_declarations=(
            *case.alternative.relation_declarations,
            SimpleRelationDeclaration(token_members, token, token_type),
            BipartiteRelationDeclaration(token_to_word, token_type, word_type),
            PolyadicRelationDeclaration(
                token_words,
                RelationSideDeclaration(
                    (RelationEndpointKind.ITEM,), (token,), maximum=None
                ),
                RelationSideDeclaration(
                    (RelationEndpointKind.ITEM,), (case.root,), maximum=None
                ),
            ),
        ),
        relations=(*case.alternative.relations, donor_link),
        attribute_declarations=(
            *case.alternative.attribute_declarations,
            AttributeDeclaration(
                relation_note, AttributeDomain.RELATION_INSTANCE, XsdType.STRING
            ),
        ),
        polyadic_relations=(*case.alternative.polyadic_relations, donor_group),
        layers=(Layer(donor_layer, (donor_fact,)),),
    )
    source = Subtree(donor, ItemRef(case.root, 0))
    dependency = DetachedDependency(
        "donor_relations", donor_link_index, declaration=token_to_word
    )

    result = replace_subtree(
        case.graph,
        DurableItemRef("root"),
        case.containment,
        source,
        drop_crossings(case),
    )

    assert dependency in result.report.dependencies
    result_dependencies = result.report.to_data()["dependencies"]
    assert isinstance(result_dependencies, list)
    assert dependency.to_data() in result_dependencies
    assert result.report.donor_relations == (
        (RelationInstanceRef(donor_link_index), donor_link),
        (PolyadicInstanceRef(donor_group_index), donor_group),
    )
    assert any(
        reference == RelationInstanceRef(donor_link_index)
        for reference, _ in result.report.relations
    )
    assert result.report.donor_facts == ((donor_layer, donor_fact),)
    assert donor_link not in tuple(relation for _, relation in result.report.relations)
    report_data = result.report.to_data()
    assert report_data["donor_relations"] == [
        {
            "carrier": "relations",
            "index": donor_link_index,
            "instance": donor_link.to_data(),
        },
        {
            "carrier": "polyadic_relations",
            "index": donor_group_index,
            "instance": donor_group.to_data(),
        },
    ]
    assert (
        DetachedDependency(
            "donor_relations", donor_group_index, declaration=token_words
        )
        in result.report.dependencies
    )
    assert (
        DetachedDependency(
            "donor_layer",
            0,
            layer=donor_layer,
            subject=DurableRelationRef("donor-token-to-word"),
        )
        in result.report.dependencies
    )
    assert all(
        relation.durable_id != donor_link.durable_id
        for relation in result.graph.relations
    )

    journal = Journal()
    case.graph.edit(journal=journal).replace_subtree(
        DurableItemRef("root"), case.containment, source, drop_crossings(case)
    )
    assert dependency in journal.records[0].report.detached_dependencies
    journal_dependencies = journal.records[0].report.to_data()["detached_dependencies"]
    assert isinstance(journal_dependencies, list)
    assert dependency.to_data() in journal_dependencies
    assert journal.records[0].report.detached_content == result.report


def test_correspondence_carries_links_facts_boundaries_and_split_relations() -> None:
    """Opt-in correspondence follows one-to-one and one-to-many mappings."""
    case = fixture("speech")
    relation_note = QualifiedName(case.root.namespace, "relation-note")
    carried_value = AttributeValue(relation_note, XsdType.STRING, "kept")
    relations = list(case.graph.relations)
    relations[-1] = replace(relations[-1], attributes=(carried_value,))
    relation_fact = LayerFact(DurableRelationRef("link"), carried_value)
    graph = replace(
        case.graph,
        relations=tuple(relations),
        attribute_declarations=(
            *case.graph.attribute_declarations,
            AttributeDeclaration(
                relation_note, AttributeDomain.RELATION_INSTANCE, XsdType.STRING
            ),
        ),
        layers=(
            replace(
                case.graph.layers[0],
                facts=(*case.graph.layers[0].facts, relation_fact),
            ),
        ),
    )
    correspondence = SubtreeCorrespondence(
        {
            ItemRef(case.middle, 0): (ItemRef(case.middle, 0),),
            ItemRef(case.middle, 1): (ItemRef(case.middle, 0),),
            ItemRef(case.leaf, 0): (
                ItemRef(case.leaf, 0),
                ItemRef(case.leaf, 1),
            ),
            ItemRef(case.leaf, 1): (ItemRef(case.leaf, 1),),
        }
    )
    policies = ReplacementPolicies(
        ReplacementAction.SPLIT,
        True,
        correspondence,
        relations={case.group: ReplacementAction.SPLIT},
        layers={case.layer: ReplacementAction.FOLLOW},
    )
    result = graph.replace_subtree(
        DurableItemRef("root"),
        case.containment,
        Subtree(case.alternative, ItemRef(case.root, 0)),
        policies,
    )
    link = next(item for item in result.relations if item.declaration == case.link)
    assert link.right == ItemRef(case.middle, 0)
    assert link.durable_id == "link"
    assert link.attributes == (carried_value,)
    assert result.relations.index(link) == 3
    group = result.polyadic_relations[0]
    assert group.targets == (ItemRef(case.middle, 0), ItemRef(case.middle, 0))
    output_layer = next(layer for layer in result.layers if layer.name == case.layer)
    assert (
        LayerFact(
            ItemRef(case.middle, 0),
            AttributeValue(case.note, XsdType.STRING, "verified"),
        )
        in output_layer.facts
    )
    assert relation_fact in output_layer.facts
    assert result.boundaries(case.leaf)[1].attributes == (
        AttributeValue(case.edge, XsdType.STRING, "aligned"),
    )
    assert result.boundaries(case.leaf)[2].attributes == (
        AttributeValue(case.edge, XsdType.STRING, "aligned"),
    )

    with pytest.raises(GraphValidationError, match="TRIM.*polyadic"):
        case.graph.replace_subtree(
            DurableItemRef("root"),
            case.containment,
            Subtree(case.alternative, ItemRef(case.root, 0)),
            ReplacementPolicies(
                relations={
                    case.link: ReplacementAction.TRIM,
                    case.group: ReplacementAction.DROP,
                }
            ),
        )
    with pytest.raises(GraphValidationError, match="TRIM.*polyadic"):
        case.graph.replace_subtree(
            DurableItemRef("root"),
            case.containment,
            Subtree(case.alternative, ItemRef(case.root, 0)),
            ReplacementPolicies(
                relations={case.containment[1]: ReplacementAction.TRIM}
            ),
        )
    with pytest.raises(GraphValidationError, match="TRIM.*polyadic"):
        case.graph.replace_subtree(
            DurableItemRef("root"),
            case.containment,
            Subtree(case.alternative, ItemRef(case.root, 0)),
            ReplacementPolicies(ReplacementAction.TRIM),
        )


def test_identity_correspondence_is_linear_through_apply_and_journal() -> None:
    """A split may identify one target while its other target remains fresh."""
    case = fixture("speech")
    source = ItemRef(case.leaf, 0)
    first = ItemRef(case.leaf, 0)
    second = ItemRef(case.leaf, 1)
    correspondence = SubtreeCorrespondence(
        {source: (first, second)},
        {source: (first,)},
    )
    policies = drop_crossings(case, ReplacementPolicies(correspondence=correspondence))
    donor = replace(
        case.alternative,
        tiers=tuple(
            replace(
                tier,
                items=(replace(tier.items[0], durable_id=None), *tier.items[1:]),
            )
            if tier.declaration.name == case.leaf
            else tier
            for tier in case.alternative.tiers
        ),
    )
    subtree = Subtree(donor, ItemRef(case.root, 0))

    direct = replace_subtree(
        case.graph,
        DurableItemRef("root"),
        case.containment,
        subtree,
        policies,
    )
    journal = Journal()
    editor = case.graph.edit(journal=journal)
    editor.replace_subtree(DurableItemRef("root"), case.containment, subtree, policies)

    assert editor.freeze() == direct.graph
    effective = journal.records[0].report.correspondence
    assert effective is not None
    assert len(effective.items[source]) == 2
    assert effective.identity_correspondence[source] == (effective.items[source][0],)
    identified_target = effective.identity_correspondence[source][0]
    assert (
        direct.graph._tiers_by_name[identified_target.tier]
        .items[identified_target.index]
        .durable_id
        == "leaf-0"
    )
    assert effective.items[source][1] not in {
        target
        for targets in effective.identity_correspondence.values()
        for target in targets
    }
    functional_target = effective.items[source][1]
    assert (
        direct.graph._tiers_by_name[functional_target.tier]
        .items[functional_target.index]
        .durable_id
        == "new-leaf-1"
    )
    report_data = journal.records[0].to_data()["report"]
    assert isinstance(report_data, dict)
    assert report_data["correspondence"] == effective.to_data()
    editor.undo()
    assert editor.freeze() == case.graph
    editor.redo()
    assert editor.freeze() == direct.graph


def test_identity_correspondence_preserves_or_refuses_donor_ids() -> None:
    """Anonymous identity carries; conflicting or duplicate donor IDs refuse."""
    case = fixture("speech")
    source = ItemRef(case.leaf, 0)
    target = ItemRef(case.leaf, 0)
    policies = ReplacementPolicies(
        correspondence=SubtreeCorrespondence(
            {source: (target,)},
            {source: (target,)},
        )
    )
    with pytest.raises(
        GraphValidationError,
        match=r"source .*'leaf-0'.*donor target .*'new-leaf-0'",
    ):
        replace_subtree(
            case.graph,
            DurableItemRef("root"),
            case.containment,
            Subtree(case.alternative, ItemRef(case.root, 0)),
            policies,
        )
    donor = replace(
        case.alternative,
        tiers=tuple(
            replace(tier, items=(replace(tier.items[0], durable_id="leaf-0"),))
            if tier.declaration.name == case.root
            else replace(
                tier,
                items=(replace(tier.items[0], durable_id=None), *tier.items[1:]),
            )
            if tier.declaration.name == case.leaf
            else tier
            for tier in case.alternative.tiers
        ),
    )
    with pytest.raises(
        GraphValidationError,
        match=r"source .*'leaf-0'.*donor item .*already carries it.*target",
    ):
        replace_subtree(
            case.graph,
            DurableItemRef("root"),
            case.containment,
            Subtree(donor, ItemRef(case.root, 0)),
            policies,
        )

    relation_donor = replace(
        case.alternative,
        tiers=tuple(
            replace(
                tier,
                items=(replace(tier.items[0], durable_id=None), *tier.items[1:]),
            )
            if tier.declaration.name == case.leaf
            else tier
            for tier in case.alternative.tiers
        ),
        relations=(
            replace(case.alternative.relations[0], durable_id="leaf-0"),
            *case.alternative.relations[1:],
        ),
    )
    with pytest.raises(
        GraphValidationError,
        match=r"source .*'leaf-0'.*donor relation instance 0.*target",
    ):
        replace_subtree(
            case.graph,
            DurableItemRef("root"),
            case.containment,
            Subtree(relation_donor, ItemRef(case.root, 0)),
            policies,
        )

    polyadic_donor = replace(
        relation_donor,
        relations=case.alternative.relations,
        polyadic_relations=(
            PolyadicRelationInstance(
                case.group,
                (ItemRef(case.middle, 0),),
                (ItemRef(case.middle, 0),),
                "leaf-0",
            ),
            PolyadicRelationInstance(
                case.group,
                (ItemRef(case.middle, 0),),
                (ItemRef(case.middle, 0),),
            ),
        ),
    )
    with pytest.raises(
        GraphValidationError,
        match=r"source .*'leaf-0'.*donor polyadic relation instance 0.*target",
    ):
        replace_subtree(
            case.graph,
            DurableItemRef("root"),
            case.containment,
            Subtree(polyadic_donor, ItemRef(case.root, 0)),
            policies,
        )

    anonymous_source = replace(
        case.graph,
        tiers=tuple(
            replace(
                tier,
                items=(replace(tier.items[0], durable_id=None), *tier.items[1:]),
            )
            if tier.declaration.name == case.leaf
            else tier
            for tier in case.graph.tiers
        ),
    )
    anonymous_donor = replace(
        case.alternative,
        tiers=tuple(
            replace(
                tier,
                items=(replace(tier.items[0], durable_id=None), *tier.items[1:]),
            )
            if tier.declaration.name == case.leaf
            else tier
            for tier in case.alternative.tiers
        ),
    )
    anonymous_result = replace_subtree(
        anonymous_source,
        DurableItemRef("root"),
        case.containment,
        Subtree(anonymous_donor, ItemRef(case.root, 0)),
        drop_crossings(case, policies),
    )
    assert anonymous_result.graph._tiers_by_name[case.leaf].items[0].durable_id is None


def test_checked_identity_carries_keep_durable_links_and_fact_subjects() -> None:
    """Identified targets retain durable endpoint and fact reference spellings."""
    case = fixture("speech")
    relations = list(case.graph.relations)
    relations[-1] = replace(relations[-1], right=DurableItemRef("middle-0"))
    graph = replace(case.graph, relations=tuple(relations))
    donor = replace(
        case.alternative,
        tiers=tuple(
            replace(
                tier,
                items=(replace(tier.items[0], durable_id=None), *tier.items[1:]),
            )
            if tier.declaration.name == case.middle
            else tier
            for tier in case.alternative.tiers
        ),
    )
    source = ItemRef(case.middle, 0)
    target = ItemRef(case.middle, 0)
    correspondence = SubtreeCorrespondence(
        {source: (target,)},
        {source: (target,)},
    )
    policies = ReplacementPolicies(
        ReplacementAction.DROP,
        correspondence=correspondence,
        relations={
            case.link: ReplacementAction.FOLLOW,
            case.group: ReplacementAction.DROP,
        },
        layers={case.layer: ReplacementAction.FOLLOW},
    )

    results = []
    for journaled in (False, True):
        journal = Journal() if journaled else None
        editor = (
            graph.edit(journal=journal, check_links=True)
            if journal is not None
            else graph.edit(check_links=True)
        )
        editor.replace_subtree(
            DurableItemRef("root"),
            case.containment,
            Subtree(donor, ItemRef(case.root, 0)),
            policies,
        )
        results.append(editor.freeze())

    assert results[0] == results[1]
    result = results[0]
    link = next(
        relation for relation in result.relations if relation.durable_id == "link"
    )
    assert link.right == DurableItemRef("middle-0")
    assert result.resolve_item(link.right) == ItemRef(case.middle, 0)
    fact = next(
        fact
        for layer in result.layers
        if layer.name == case.layer
        for fact in layer.facts
        if fact.subject == DurableItemRef("middle-0")
    )
    assert isinstance(fact.subject, DurableItemRef)
    assert result.resolve_item(fact.subject) == ItemRef(case.middle, 0)


def test_identity_split_preserves_every_durable_endpoint_and_fact_image() -> None:
    """A split keeps its fresh images alongside the durable identity image."""
    case = fixture("speech")
    split_group = QualifiedName(case.group.namespace, "split-group")
    side = RelationSideDeclaration(
        (RelationEndpointKind.ITEM,), (case.leaf,), maximum=None
    )
    split_value = AttributeValue(case.note, XsdType.STRING, "split")
    graph = replace(
        case.graph,
        relation_declarations=(
            *case.graph.relation_declarations,
            PolyadicRelationDeclaration(split_group, side, side),
        ),
        polyadic_relations=(
            *case.graph.polyadic_relations,
            PolyadicRelationInstance(
                split_group,
                (ItemRef(case.leaf, 3),),
                (DurableItemRef("leaf-0"),),
                "split-link",
            ),
        ),
        layers=(
            replace(
                case.graph.layers[0],
                facts=(
                    *case.graph.layers[0].facts,
                    LayerFact(DurableItemRef("leaf-0"), split_value),
                ),
            ),
        ),
    )
    donor = replace(
        case.alternative,
        tiers=tuple(
            replace(
                tier,
                items=(replace(tier.items[0], durable_id=None), *tier.items[1:]),
            )
            if tier.declaration.name == case.leaf
            else tier
            for tier in case.alternative.tiers
        ),
    )
    source = ItemRef(case.leaf, 0)
    identity = ItemRef(case.leaf, 0)
    fresh = ItemRef(case.leaf, 1)
    policies = ReplacementPolicies(
        ReplacementAction.DROP,
        correspondence=SubtreeCorrespondence(
            {source: (identity, fresh)},
            {source: (identity,)},
        ),
        relations={
            case.link: ReplacementAction.DROP,
            case.group: ReplacementAction.DROP,
            split_group: ReplacementAction.SPLIT,
        },
        layers={case.layer: ReplacementAction.SPLIT},
    )

    results = []
    for journaled in (False, True):
        journal = Journal() if journaled else None
        editor = (
            graph.edit(journal=journal, check_links=True)
            if journal is not None
            else graph.edit(check_links=True)
        )
        editor.replace_subtree(
            DurableItemRef("root"),
            case.containment,
            Subtree(donor, ItemRef(case.root, 0)),
            policies,
        )
        results.append(editor.freeze())

    assert results[0] == results[1]
    result = results[0]
    relation = next(
        relation
        for relation in result.polyadic_relations
        if relation.durable_id == "split-link"
    )
    assert relation.targets == (DurableItemRef("leaf-0"), fresh)
    layer = next(layer for layer in result.layers if layer.name == case.layer)
    assert LayerFact(DurableItemRef("leaf-0"), split_value) in layer.facts
    assert LayerFact(fresh, split_value) in layer.facts


def test_identity_correspondence_refuses_splits_and_merges() -> None:
    """An identity claim is one-to-one even when alignment is functional."""
    case = fixture("speech")
    first_source = ItemRef(case.leaf, 0)
    second_source = ItemRef(case.leaf, 1)
    first_target = ItemRef(case.leaf, 0)
    second_target = ItemRef(case.leaf, 1)

    with pytest.raises(ValueError, match=r"source .*exactly one target.*ItemRef"):
        SubtreeCorrespondence(
            {first_source: (first_target, second_target)},
            {first_source: (first_target, second_target)},
        )
    with pytest.raises(
        ValueError, match=r"source .*targets .*already claimed by source"
    ):
        SubtreeCorrespondence(
            {
                first_source: (first_target,),
                second_source: (first_target,),
            },
            {
                first_source: (first_target,),
                second_source: (first_target,),
            },
        )


def test_nested_phrase_crossing_carries_refuses_drops_and_trims() -> None:
    """A phrase link follows a deep ordered replacement under every K3 outcome."""
    namespace = "urn:tiergraph:replacement:hierarchy"

    def name(local: str) -> QualifiedName:
        return QualifiedName(namespace, local)

    utterance, phrase, word, syllable, segment = (
        name("utterance"),
        name("phrase"),
        name("word"),
        name("syllable"),
        name("segment"),
    )
    tiers = (utterance, phrase, word, syllable, segment)
    types = tuple(name(f"{tier.local_name}-type") for tier in tiers)
    memberships = tuple(name(f"{tier.local_name}-members") for tier in tiers)
    containments = tuple(
        name(local)
        for local in (
            "utterance-phrases",
            "phrase-words",
            "word-syllables",
            "syllable-segments",
        )
    )
    span = name("phrase-span")
    relation_note = name("relation-note")

    def source_side(tier: QualifiedName) -> RelationSideDeclaration:
        return RelationSideDeclaration((RelationEndpointKind.ITEM,), (tier,), maximum=1)

    def target_side(tier: QualifiedName) -> RelationSideDeclaration:
        return RelationSideDeclaration(
            (RelationEndpointKind.ITEM,), (tier,), maximum=None
        )

    declarations: tuple[
        SimpleRelationDeclaration | PolyadicRelationDeclaration, ...
    ] = (
        *(
            SimpleRelationDeclaration(member, tier, item_type)
            for member, tier, item_type in zip(memberships, tiers, types, strict=True)
        ),
        *(
            PolyadicRelationDeclaration(
                relation,
                source_side(parent),
                target_side(child),
                unique_sources=True,
                distinct_targets=True,
                single_parent=True,
                acyclic=True,
            )
            for relation, parent, child in zip(
                containments, tiers[:-1], tiers[1:], strict=True
            )
        ),
        PolyadicRelationDeclaration(
            span,
            source_side(phrase),
            target_side(word),
            distinct_targets=True,
        ),
    )
    old_relations = (
        PolyadicRelationInstance(
            containments[0],
            (ItemRef(utterance, 0),),
            (ItemRef(phrase, 0), ItemRef(phrase, 1)),
        ),
        PolyadicRelationInstance(
            containments[1], (ItemRef(phrase, 0),), (ItemRef(word, 0), ItemRef(word, 1))
        ),
        PolyadicRelationInstance(
            containments[1], (ItemRef(phrase, 1),), (ItemRef(word, 2),)
        ),
        *(
            PolyadicRelationInstance(
                containments[2], (ItemRef(word, index),), (ItemRef(syllable, index),)
            )
            for index in range(3)
        ),
        *(
            PolyadicRelationInstance(
                containments[3],
                (ItemRef(syllable, index),),
                (ItemRef(segment, index),),
            )
            for index in range(3)
        ),
        PolyadicRelationInstance(
            span,
            (ItemRef(phrase, 0),),
            (ItemRef(word, 0), ItemRef(word, 1)),
            "phrase-span",
        ),
    )
    span_fact = LayerFact(
        DurablePolyadicRef("phrase-span"),
        AttributeValue(relation_note, XsdType.STRING, "hand-corrected"),
    )
    layer = LayerName(namespace, "source")
    graph = Graph(
        (NamespaceDeclaration("h", namespace),),
        (
            Tier(TierDeclaration(utterance, "Utterances"), (Item("u"),)),
            Tier(TierDeclaration(phrase, "Phrases"), (Item("p0"), Item("p1"))),
            Tier(
                TierDeclaration(word, "Words"),
                (Item("w0"), Item("w1"), Item("w2")),
            ),
            Tier(
                TierDeclaration(syllable, "Syllables"),
                (Item("s0"), Item("s1"), Item("s2")),
            ),
            Tier(
                TierDeclaration(segment, "Segments"),
                (Item("g0"), Item("g1"), Item("g2")),
            ),
        ),
        declarations,
        attribute_declarations=(
            AttributeDeclaration(
                relation_note, AttributeDomain.RELATION_INSTANCE, XsdType.STRING
            ),
        ),
        polyadic_relations=old_relations,
        layers=(Layer(layer, (span_fact,)),),
    )
    source = Graph(
        graph.namespaces,
        (
            Tier(TierDeclaration(utterance, "Utterances"), ()),
            Tier(TierDeclaration(phrase, "Phrases"), (Item("new-p0"),)),
            Tier(
                TierDeclaration(word, "Words"),
                (Item("nw0"), Item("nw1"), Item("nw2")),
            ),
            Tier(
                TierDeclaration(syllable, "Syllables"),
                (Item("ns0"), Item("ns1"), Item("ns2")),
            ),
            Tier(
                TierDeclaration(segment, "Segments"),
                (Item("ng0"), Item("ng1"), Item("ng2")),
            ),
        ),
        declarations,
        attribute_declarations=graph.attribute_declarations,
        polyadic_relations=(
            PolyadicRelationInstance(
                containments[1],
                (ItemRef(phrase, 0),),
                (ItemRef(word, 0), ItemRef(word, 1), ItemRef(word, 2)),
            ),
            *(
                PolyadicRelationInstance(
                    containments[2],
                    (ItemRef(word, index),),
                    (ItemRef(syllable, index),),
                )
                for index in range(3)
            ),
            *(
                PolyadicRelationInstance(
                    containments[3],
                    (ItemRef(syllable, index),),
                    (ItemRef(segment, index),),
                )
                for index in range(3)
            ),
        ),
    )
    for old_root, new_root in (
        (DurableItemRef("w0"), ItemRef(word, 0)),
        (DurableItemRef("s0"), ItemRef(syllable, 0)),
    ):
        deep = replace_subtree(
            graph,
            old_root,
            containments,
            Subtree(source, new_root),
        ).graph
        assert all(
            any(
                relation.declaration == declaration
                for relation in deep.polyadic_relations
            )
            for declaration in containments
        )
        assert next(
            relation
            for relation in deep.polyadic_relations
            if relation.declaration == containments[0]
        ).targets == (ItemRef(phrase, 0), ItemRef(phrase, 1))

    alignment = SubtreeCorrespondence(
        {
            ItemRef(word, 0): (ItemRef(word, 0), ItemRef(word, 1)),
            ItemRef(word, 1): (ItemRef(word, 2),),
        }
    )
    subtree = Subtree(source, ItemRef(phrase, 0))
    journal = Journal()
    editor = graph.edit(journal=journal)
    editor.replace_subtree(
        DurableItemRef("p0"),
        containments,
        subtree,
        ReplacementPolicies(correspondence=alignment),
    )
    carried = editor.freeze()
    carried_span = next(
        relation
        for relation in carried.polyadic_relations
        if relation.declaration == span
    )
    assert carried_span.targets == (
        ItemRef(word, 0),
        ItemRef(word, 1),
        ItemRef(word, 2),
    )
    assert span_fact in carried.layers[0].facts
    assert all(
        any(
            relation.declaration == declaration
            for relation in carried.polyadic_relations
        )
        for declaration in containments
    )
    recorded = journal.records[0].report.correspondence
    assert recorded is not None
    assert not recorded.identity_correspondence
    assert "identity_correspondence" not in str(journal.records[0].to_data())
    editor.undo()
    assert editor.freeze() == graph

    functional_graph = replace(graph, polyadic_relations=old_relations[:-1], layers=())
    functional_journal = Journal()
    functional_graph.edit(journal=functional_journal).replace_subtree(
        DurableItemRef("p0"),
        containments,
        subtree,
        ReplacementPolicies(correspondence=alignment),
    )
    functional = functional_journal.records[0].report.correspondence
    assert functional is not None
    assert not functional.identity_correspondence

    partial = SubtreeCorrespondence(
        {ItemRef(word, 0): (ItemRef(word, 0), ItemRef(word, 1))}
    )
    with pytest.raises(
        GraphValidationError, match=r"phrase-span.*word\[1\].*DROP.*TRIM"
    ):
        replace_subtree(
            graph,
            DurableItemRef("p0"),
            containments,
            subtree,
            ReplacementPolicies(correspondence=partial),
        )

    merged = SubtreeCorrespondence(
        {
            ItemRef(word, 0): (ItemRef(word, 0),),
            ItemRef(word, 1): (ItemRef(word, 0),),
        }
    )
    with pytest.raises(
        GraphValidationError,
        match=r"phrase-span.*word\[0\].*duplicate declared-distinct targets.*DROP",
    ):
        replace_subtree(
            graph,
            DurableItemRef("p0"),
            containments,
            subtree,
            ReplacementPolicies(correspondence=merged),
        )

    with pytest.raises(
        GraphValidationError,
        match=r"phrase-span.*word\[0\].*empty target side.*DROP",
    ):
        replace_subtree(
            graph,
            DurableItemRef("p0"),
            containments,
            subtree,
            ReplacementPolicies(relations={span: ReplacementAction.TRIM}),
        )

    dropped = replace_subtree(
        graph,
        DurableItemRef("p0"),
        containments,
        subtree,
        ReplacementPolicies(
            correspondence=partial,
            relations={span: ReplacementAction.DROP},
        ),
    )
    assert all(
        relation.declaration != span for relation in dropped.graph.polyadic_relations
    )
    assert any(
        relation == old_relations[-1] for _, relation in dropped.report.relations
    )
    assert (layer, span_fact) in dropped.report.facts

    trimmed = replace_subtree(
        graph,
        DurableItemRef("p0"),
        containments,
        subtree,
        ReplacementPolicies(
            correspondence=partial,
            relations={span: ReplacementAction.TRIM},
        ),
    )
    trimmed_span = next(
        relation
        for relation in trimmed.graph.polyadic_relations
        if relation.declaration == span
    )
    assert trimmed_span.targets == (ItemRef(word, 0), ItemRef(word, 1))
    assert span_fact in trimmed.graph.layers[0].facts
    removed = next(
        dependency
        for dependency in trimmed.report.dependencies
        if dependency.carrier == "polyadic_endpoints"
    )
    assert removed.endpoint == ItemRef(word, 1)
    assert removed.endpoint_side == "targets"
    assert removed.endpoint_index == 1
    assert removed.to_data() == {
        "carrier": "polyadic_endpoints",
        "index": len(old_relations) - 1,
        "declaration": span.to_data(),
        "endpoint": {
            "kind": "item-coordinate",
            "tier": word.to_data(),
            "index": 1,
        },
        "endpoint_side": "targets",
        "endpoint_index": 1,
    }


def test_follow_carries_a_boundary_fact_and_has_an_exact_inverse() -> None:
    """One corresponding boundary carries its fact through a journaled edit."""
    case = fixture("speech")
    boundary = BoundaryRef(case.leaf, 1)
    fact = LayerFact(
        boundary,
        AttributeValue(case.edge, XsdType.STRING, "source-offset:1"),
    )
    graph = replace(
        case.graph,
        layers=(
            replace(case.graph.layers[0], facts=(*case.graph.layers[0].facts, fact)),
        ),
    )
    correspondence = SubtreeCorrespondence(
        {
            ItemRef(case.leaf, 0): (ItemRef(case.leaf, 0),),
            ItemRef(case.leaf, 1): (ItemRef(case.leaf, 1),),
        }
    )
    policies = drop_crossings(
        case,
        ReplacementPolicies(
            ReplacementAction.FOLLOW,
            correspondence=correspondence,
            layers={case.layer: ReplacementAction.FOLLOW},
        ),
    )
    source = Subtree(case.alternative, ItemRef(case.root, 0))

    direct = replace_subtree(
        graph, DurableItemRef("root"), case.containment, source, policies
    )

    assert fact in direct.graph.layers[0].facts
    assert all(detached_fact != fact for _, detached_fact in direct.report.facts)
    journal = Journal()
    editor = graph.edit(journal=journal)
    editor.replace_subtree(DurableItemRef("root"), case.containment, source, policies)
    assert editor.freeze() == direct.graph
    assert journal.records[0].report.detached_content == direct.report
    assert journal.to_patch().invert().apply(direct.graph) == graph


def test_follow_reports_carried_boundary_content_overwritten_by_source() -> None:
    """Supplied content cannot silently replace content reported as carried."""
    case = fixture("speech")
    boundary = BoundaryRef(case.leaf, 1)
    sibling_name = QualifiedName(case.edge.namespace, "boundary-sibling")
    sibling_declaration = AttributeDeclaration(
        sibling_name, AttributeDomain.BOUNDARY, XsdType.STRING
    )
    old_value = AttributeValue(case.edge, XsdType.STRING, "old-alignment")
    kept_value = AttributeValue(sibling_name, XsdType.STRING, "kept")
    new_value = AttributeValue(case.edge, XsdType.STRING, "new-alignment")
    old_fact = LayerFact(
        boundary,
        AttributeValue(case.edge, XsdType.STRING, "old-offset"),
    )
    new_fact = LayerFact(
        boundary,
        AttributeValue(case.edge, XsdType.STRING, "new-offset"),
    )
    graph = replace(
        case.graph,
        attribute_declarations=(
            *case.graph.attribute_declarations,
            sibling_declaration,
        ),
        boundary_values=(Boundary(boundary, (old_value, kept_value)),),
        layers=(
            replace(
                case.graph.layers[0],
                facts=(*case.graph.layers[0].facts, old_fact),
            ),
        ),
    )
    source = replace(
        case.alternative,
        boundary_values=(Boundary(boundary, (new_value,)),),
        layers=(Layer(case.layer, (new_fact,)),),
    )
    correspondence = SubtreeCorrespondence(
        {
            ItemRef(case.leaf, 0): (ItemRef(case.leaf, 0),),
            ItemRef(case.leaf, 1): (ItemRef(case.leaf, 1),),
        }
    )
    policies = drop_crossings(
        case,
        ReplacementPolicies(
            ReplacementAction.FOLLOW,
            correspondence=correspondence,
            layers={case.layer: ReplacementAction.FOLLOW},
        ),
    )
    subtree = Subtree(source, ItemRef(case.root, 0))

    direct = replace_subtree(
        graph, DurableItemRef("root"), case.containment, subtree, policies
    )

    assert set(direct.graph.boundaries(case.leaf)[1].attributes) == {
        kept_value,
        new_value,
    }
    assert new_fact in direct.graph.layers[0].facts
    assert old_fact not in direct.graph.layers[0].facts
    assert direct.report.boundary_values == ((boundary, old_value),)
    assert (case.layer, old_fact) in direct.report.facts
    assert all(value != kept_value for _, value in direct.report.boundary_values)
    journal = Journal()
    editor = graph.edit(journal=journal)
    editor.replace_subtree(DurableItemRef("root"), case.containment, subtree, policies)
    assert editor.freeze() == direct.graph
    assert journal.records[0].report.detached_content == direct.report
    assert journal.to_patch().invert().apply(direct.graph) == graph


def test_follow_reports_content_lost_when_outer_boundaries_collapse() -> None:
    """Two source boundaries mapped to one target report the overwritten content."""
    case = fixture("speech")
    start = BoundaryRef(case.leaf, 0)
    end = BoundaryRef(case.leaf, 3)
    start_value = AttributeValue(case.edge, XsdType.STRING, "outer-start")
    end_value = AttributeValue(case.edge, XsdType.STRING, "outer-end")
    start_fact = LayerFact(
        start,
        AttributeValue(case.edge, XsdType.STRING, "start-offset"),
    )
    end_fact = LayerFact(
        end,
        AttributeValue(case.edge, XsdType.STRING, "end-offset"),
    )
    graph = replace(
        case.graph,
        boundary_values=(
            Boundary(start, (start_value,)),
            Boundary(end, (end_value,)),
        ),
        layers=(Layer(case.layer, (start_fact, end_fact)),),
    )
    empty_source = replace(
        case.alternative,
        tiers=tuple(
            replace(tier, items=())
            if tier.declaration.name in {case.middle, case.leaf}
            else tier
            for tier in case.alternative.tiers
        ),
        relations=(),
    )
    policies = drop_crossings(
        case,
        ReplacementPolicies(
            ReplacementAction.FOLLOW,
            layers={case.layer: ReplacementAction.FOLLOW},
        ),
    )

    result = replace_subtree(
        graph,
        DurableItemRef("root"),
        case.containment,
        Subtree(empty_source, ItemRef(case.root, 0)),
        policies,
    )

    assert result.graph.boundaries(case.leaf)[0].attributes == (end_value,)
    assert LayerFact(start, end_fact.value) in result.graph.layers[0].facts
    assert start_fact not in result.graph.layers[0].facts
    assert result.report.boundary_values == ((start, start_value),)
    assert result.report.facts == ((case.layer, start_fact),)


def test_replacement_copies_new_subtree_relations_values_and_facts() -> None:
    """Content owned by the supplied subtree is inserted with its structure."""
    case = fixture("speech")
    new_layer = LayerName(case.layer.vocabulary, "new-source")
    source = replace(
        case.alternative,
        relations=(
            *case.alternative.relations,
            RelationInstance(
                case.link,
                ItemRef(case.root, 0),
                DurableItemRef("new-middle"),
                "new-link",
            ),
        ),
        boundary_values=(
            Boundary(
                BoundaryRef(case.root, 0),
                (AttributeValue(case.edge, XsdType.STRING, "outside"),),
            ),
            Boundary(
                BoundaryRef(case.leaf, 1),
                (AttributeValue(case.edge, XsdType.STRING, "new-alignment"),),
            ),
        ),
        layers=(
            Layer(
                new_layer,
                (
                    LayerFact(
                        DurableItemRef("new-root"),
                        AttributeValue(case.note, XsdType.STRING, "root-placeholder"),
                    ),
                    LayerFact(
                        DurableItemRef("new-middle"),
                        AttributeValue(case.note, XsdType.STRING, "new-provenance"),
                    ),
                ),
            ),
            Layer(
                case.layer,
                (
                    LayerFact(
                        DurableItemRef("new-root"),
                        AttributeValue(case.note, XsdType.STRING, "ignored"),
                    ),
                ),
            ),
        ),
    )
    result = replace_subtree(
        case.graph,
        DurableItemRef("root"),
        case.containment,
        Subtree(source, ItemRef(case.root, 0)),
        drop_crossings(case),
    ).graph
    copied_link = next(
        relation for relation in result.relations if relation.durable_id == "new-link"
    )
    assert copied_link.right == ItemRef(case.middle, 0)
    assert result.boundaries(case.leaf)[1].attributes == (
        AttributeValue(case.edge, XsdType.STRING, "new-alignment"),
    )
    output_layer = next(layer for layer in result.layers if layer.name == new_layer)
    assert (
        LayerFact(
            DurableItemRef("new-middle"),
            AttributeValue(case.note, XsdType.STRING, "new-provenance"),
        )
        in output_layer.facts
    )
    assert all(
        fact.subject != DurableItemRef("new-root") for fact in output_layer.facts
    )


def test_replacement_remaps_a_shifted_same_tier_root() -> None:
    """Copied containment remains attached when inserted children shift the root."""
    namespace = "urn:tiergraph:replacement:same-tier"
    tier = QualifiedName(namespace, "node")
    item_type = QualifiedName(namespace, "Node")
    members = QualifiedName(namespace, "nodes")
    contains = QualifiedName(namespace, "contains")
    declarations = (
        SimpleRelationDeclaration(members, tier, item_type),
        BipartiteRelationDeclaration(
            contains, item_type, item_type, single_parent=True, acyclic=True
        ),
    )
    graph = Graph(
        (NamespaceDeclaration("n", namespace),),
        (
            Tier(
                TierDeclaration(tier, "Nodes"),
                (Item("old-child"), Item("unrelated"), Item("root")),
            ),
        ),
        declarations,
        (RelationInstance(contains, ItemRef(tier, 2), ItemRef(tier, 0)),),
    )
    source = Graph(
        graph.namespaces,
        (
            Tier(
                graph.tiers[0].declaration,
                (Item("source-root"), Item("new-0"), Item("new-1")),
            ),
        ),
        declarations,
        (
            RelationInstance(contains, ItemRef(tier, 0), ItemRef(tier, 1)),
            RelationInstance(contains, ItemRef(tier, 0), ItemRef(tier, 2)),
        ),
    )
    result = replace_subtree(
        graph,
        DurableItemRef("root"),
        contains,
        Subtree(source, ItemRef(tier, 0)),
    ).graph
    root = result.resolve_item(DurableItemRef("root"))
    assert root == ItemRef(tier, 3)
    assert {
        relation.right
        for relation in result.relations
        if relation.declaration == contains and relation.left == root
    } == {ItemRef(tier, 0), ItemRef(tier, 1)}


def test_replacement_inserts_descendants_on_a_newly_used_tier() -> None:
    """A source-only descendant run uses the caller's declared insertion point."""
    case = fixture("text")
    extra = QualifiedName(case.root.namespace, "captions")
    extra_members = QualifiedName(case.root.namespace, "caption-members")
    middle_type = next(
        declaration.item_type
        for declaration in case.graph.relation_declarations
        if isinstance(declaration, SimpleRelationDeclaration)
        and declaration.tier == case.middle
    )
    extra_declaration = SimpleRelationDeclaration(extra_members, extra, middle_type)
    graph = replace(
        case.graph,
        tiers=(
            *case.graph.tiers,
            Tier(TierDeclaration(extra, "Captions"), ()),
        ),
        relation_declarations=(
            *case.graph.relation_declarations,
            extra_declaration,
        ),
    )
    source = replace(
        case.alternative,
        tiers=(
            *case.alternative.tiers,
            Tier(TierDeclaration(extra, "Captions"), (Item("new-caption"),)),
        ),
        relation_declarations=(
            *case.alternative.relation_declarations,
            extra_declaration,
        ),
        relations=(
            *case.alternative.relations,
            RelationInstance(
                case.containment[0],
                ItemRef(case.root, 0),
                ItemRef(extra, 0),
            ),
        ),
    )
    result = replace_subtree(
        graph,
        DurableItemRef("root"),
        case.containment,
        Subtree(source, ItemRef(case.root, 0)),
        drop_crossings(case, ReplacementPolicies(insertion_points={extra: 0})),
    ).graph
    assert result._tiers_by_name[extra].items == (Item("new-caption"),)


def test_swap_subtrees_is_exactly_undoable_and_refuses_nested_roots() -> None:
    """A swap exchanges different-sized descendant sets without id collisions."""
    namespace = "urn:tiergraph:replacement:swap"
    tier = QualifiedName(namespace, "node")
    item_type = QualifiedName(namespace, "Node")
    members = QualifiedName(namespace, "nodes")
    contains = QualifiedName(namespace, "contains")
    declarations = (
        SimpleRelationDeclaration(members, tier, item_type),
        BipartiteRelationDeclaration(
            contains, item_type, item_type, single_parent=True, acyclic=True
        ),
    )
    graph = Graph(
        (NamespaceDeclaration("s", namespace),),
        (
            Tier(
                TierDeclaration(tier, "Nodes"),
                (
                    Item("left"),
                    Item("a"),
                    Item("right"),
                    Item("b"),
                    Item("c"),
                    Item("subtree-swap-1"),
                ),
            ),
        ),
        declarations,
        (
            RelationInstance(contains, ItemRef(tier, 0), ItemRef(tier, 1)),
            RelationInstance(contains, ItemRef(tier, 2), ItemRef(tier, 3)),
            RelationInstance(contains, ItemRef(tier, 2), ItemRef(tier, 4)),
        ),
    )
    expected = swap_subtrees(
        graph, DurableItemRef("left"), DurableItemRef("right"), contains
    ).graph
    assert (
        graph.swap_subtrees(DurableItemRef("left"), DurableItemRef("right"), contains)
        == expected
    )
    plain_editor = graph.edit()
    plain_editor.swap_subtrees(
        DurableItemRef("left"), DurableItemRef("right"), contains
    )
    assert plain_editor.freeze() == expected
    assert tuple(item.durable_id for item in expected.tiers[0].items) == (
        "left",
        "b",
        "c",
        "right",
        "a",
        "subtree-swap-1",
    )
    journal = Journal()
    editor = graph.edit(journal=journal)
    editor.swap_subtrees(DurableItemRef("left"), DurableItemRef("right"), contains)
    assert editor.freeze() == expected
    editor.undo()
    assert editor.freeze() == graph
    with pytest.raises(GraphValidationError, match="must not contain"):
        swap_subtrees(graph, DurableItemRef("right"), DurableItemRef("b"), contains)
    with pytest.raises(GraphValidationError, match="must be distinct"):
        swap_subtrees(graph, DurableItemRef("left"), DurableItemRef("left"), contains)
    first_identity = SubtreeCorrespondence(
        {ItemRef(tier, 1): (ItemRef(tier, 3),)},
        {ItemRef(tier, 1): (ItemRef(tier, 3),)},
    )
    second_identity = SubtreeCorrespondence(
        {ItemRef(tier, 3): (ItemRef(tier, 1),)},
        {ItemRef(tier, 3): (ItemRef(tier, 1),)},
    )
    for first_policies, second_policies, side in (
        (ReplacementPolicies(correspondence=first_identity), None, "first"),
        (None, ReplacementPolicies(correspondence=second_identity), "second"),
    ):
        with pytest.raises(
            GraphValidationError,
            match=rf"subtree swap {side} policies cannot declare identity",
        ):
            swap_subtrees(
                graph,
                DurableItemRef("left"),
                DurableItemRef("right"),
                contains,
                first_policies,
                second_policies,
            )


def _boundary_swap_graph(
    *, include_cut_value: bool = False
) -> tuple[Graph, QualifiedName, QualifiedName, QualifiedName]:
    """Build two roots whose three children expose internal and cut boundaries."""
    namespace = "urn:tiergraph:replacement:swap-boundaries"
    root = QualifiedName(namespace, "root")
    child = QualifiedName(namespace, "child")
    root_type = QualifiedName(namespace, "Root")
    child_type = QualifiedName(namespace, "Child")
    contains = QualifiedName(namespace, "contains")
    edge = QualifiedName(namespace, "edge")
    boundaries = [
        Boundary(
            BoundaryRef(child, 1),
            (AttributeValue(edge, XsdType.STRING, "inner"),),
        )
    ]
    if include_cut_value:
        boundaries.append(
            Boundary(
                BoundaryRef(child, 2),
                (AttributeValue(edge, XsdType.STRING, "between"),),
            )
        )
    graph = Graph(
        (NamespaceDeclaration("s", namespace),),
        (
            Tier(TierDeclaration(root, "Roots"), (Item("left"), Item("right"))),
            Tier(
                TierDeclaration(child, "Children"),
                (Item("left-a"), Item("left-b"), Item("right-a")),
            ),
        ),
        (
            SimpleRelationDeclaration(
                QualifiedName(namespace, "roots"), root, root_type
            ),
            SimpleRelationDeclaration(
                QualifiedName(namespace, "children"), child, child_type
            ),
            BipartiteRelationDeclaration(
                contains,
                root_type,
                child_type,
                single_parent=True,
                acyclic=True,
            ),
        ),
        (
            RelationInstance(contains, ItemRef(root, 0), ItemRef(child, 0)),
            RelationInstance(contains, ItemRef(root, 0), ItemRef(child, 1)),
            RelationInstance(contains, ItemRef(root, 1), ItemRef(child, 2)),
        ),
        (AttributeDeclaration(edge, AttributeDomain.BOUNDARY, XsdType.STRING),),
        tuple(boundaries),
    )
    return graph, child, contains, edge


def test_swap_subtrees_carries_a_two_tier_boundary_value_once() -> None:
    """An internal child boundary follows its run and is not detached."""
    graph, child, contains, edge = _boundary_swap_graph()
    journal = Journal()
    editor = graph.edit(journal=journal)
    editor.swap_subtrees(DurableItemRef("left"), DurableItemRef("right"), contains)
    result = editor.freeze()

    assert tuple(item.durable_id for item in result._tiers_by_name[child].items) == (
        "right-a",
        "left-a",
        "left-b",
    )
    assert result.boundary_values == (
        Boundary(
            BoundaryRef(child, 2),
            (AttributeValue(edge, XsdType.STRING, "inner"),),
        ),
    )
    report = journal.records[0].report.detached_content
    assert report is not None
    assert not report.boundary_values
    editor.undo()
    assert editor.freeze() == graph


def test_swap_subtrees_reports_a_departed_three_child_cut_value_once() -> None:
    """A cut boundary departs instead of being copied onto both run edges."""
    graph, child, contains, edge = _boundary_swap_graph(include_cut_value=True)
    journal = Journal()
    editor = graph.edit(journal=journal)
    editor.swap_subtrees(DurableItemRef("left"), DurableItemRef("right"), contains)
    result = editor.freeze()

    assert result.boundary_values == (
        Boundary(
            BoundaryRef(child, 2),
            (AttributeValue(edge, XsdType.STRING, "inner"),),
        ),
    )
    report = journal.records[0].report.detached_content
    assert report is not None
    assert report.boundary_values == (
        (
            BoundaryRef(child, 2),
            AttributeValue(edge, XsdType.STRING, "between"),
        ),
    )
    editor.undo()
    assert editor.freeze() == graph


def test_swap_subtrees_preserves_an_untouched_empty_tier_boundary() -> None:
    """An unrelated empty tier keeps its sole coordinate boundary value."""
    graph, child, contains, edge = _boundary_swap_graph()
    empty = QualifiedName(child.namespace, "empty")
    empty_type = QualifiedName(child.namespace, "Empty")
    graph = replace(
        graph,
        tiers=(*graph.tiers, Tier(TierDeclaration(empty, "Empty"))),
        relation_declarations=(
            *graph.relation_declarations,
            SimpleRelationDeclaration(
                QualifiedName(child.namespace, "empties"), empty, empty_type
            ),
        ),
        boundary_values=(
            *graph.boundary_values,
            Boundary(
                BoundaryRef(empty, 0),
                (AttributeValue(edge, XsdType.STRING, "empty"),),
            ),
        ),
    )

    result = swap_subtrees(
        graph, DurableItemRef("left"), DurableItemRef("right"), contains
    )

    assert result.graph.boundary_values == (
        Boundary(
            BoundaryRef(child, 2),
            (AttributeValue(edge, XsdType.STRING, "inner"),),
        ),
        Boundary(
            BoundaryRef(empty, 0),
            (AttributeValue(edge, XsdType.STRING, "empty"),),
        ),
    )
    assert not result.report.boundary_values


def test_swap_subtrees_refuses_colliding_durable_boundary_values_atomically() -> None:
    """A swap names durable anchors that converge on one boundary."""
    graph, _, contains, edge = _boundary_swap_graph()
    before_left = DurableBoundaryRef(DurableItemRef("left-a"), BoundarySide.BEFORE)
    after_right = DurableBoundaryRef(DurableItemRef("right-a"), BoundarySide.AFTER)
    graph = replace(
        graph,
        boundary_values=(
            Boundary(
                before_left,
                (AttributeValue(edge, XsdType.STRING, "before-left"),),
            ),
            Boundary(
                after_right,
                (AttributeValue(edge, XsdType.STRING, "after-right"),),
            ),
        ),
    )
    editor = graph.edit()

    with pytest.raises(
        GraphValidationError,
        match=r"subtree swap maps stored boundary values .*left-a.*right-a.*child\[1\]",
    ) as caught:
        editor.swap_subtrees(DurableItemRef("left"), DurableItemRef("right"), contains)

    assert caught.value.stage is RefusalStage.SEMANTICS
    assert editor.freeze() == graph


def test_swap_subtrees_carries_crossings_through_both_correspondences() -> None:
    """Each side's crossing links re-point and the swap records held identity."""
    namespace = "urn:tiergraph:replacement:swap-carry"
    tier = QualifiedName(namespace, "node")
    item_type = QualifiedName(namespace, "Node")
    members = QualifiedName(namespace, "nodes")
    contains = QualifiedName(namespace, "contains")
    link = QualifiedName(namespace, "link")
    graph = Graph(
        (NamespaceDeclaration("s", namespace),),
        (
            Tier(
                TierDeclaration(tier, "Nodes"),
                (
                    Item("left"),
                    Item("a"),
                    Item("right"),
                    Item("b"),
                    Item("outside"),
                ),
            ),
        ),
        (
            SimpleRelationDeclaration(members, tier, item_type),
            BipartiteRelationDeclaration(
                contains, item_type, item_type, single_parent=True, acyclic=True
            ),
            BipartiteRelationDeclaration(link, item_type, item_type),
        ),
        (
            RelationInstance(contains, ItemRef(tier, 0), ItemRef(tier, 1)),
            RelationInstance(contains, ItemRef(tier, 2), ItemRef(tier, 3)),
            RelationInstance(link, DurableItemRef("outside"), ItemRef(tier, 1), "to-a"),
            RelationInstance(link, DurableItemRef("outside"), ItemRef(tier, 3), "to-b"),
        ),
    )
    first = ReplacementPolicies(
        correspondence=SubtreeCorrespondence({ItemRef(tier, 1): (ItemRef(tier, 3),)})
    )
    second = ReplacementPolicies(
        correspondence=SubtreeCorrespondence({ItemRef(tier, 3): (ItemRef(tier, 1),)})
    )
    journal = Journal()
    editor = graph.edit(journal=journal)
    editor.swap_subtrees(
        DurableItemRef("left"), DurableItemRef("right"), contains, first, second
    )
    result = editor.freeze()
    targets = {
        relation.durable_id: replacement._item_endpoint(result, relation.right)
        for relation in result.relations
        if relation.declaration == link
    }
    assert targets == {
        "to-a": result.resolve_item(DurableItemRef("b")),
        "to-b": result.resolve_item(DurableItemRef("a")),
    }
    correspondence = journal.records[0].report.correspondence
    assert correspondence is not None
    assert correspondence.identity_correspondence == correspondence.items
    assert correspondence.to_data() == {
        "holes": [
            {
                "name": str(source),
                "old": source.to_data(),
                "new": [target.to_data() for target in targets],
                "identity_correspondence": [target.to_data() for target in targets],
            }
            for source, targets in sorted(
                correspondence.items.items(),
                key=lambda pair: (str(pair[0].tier), pair[0].index),
            )
        ]
    }
    assert not journal.records[0].report.detached_dependencies
    editor.undo()
    assert editor.freeze() == graph


def test_swap_reports_only_dependencies_absent_from_the_result() -> None:
    """Subtree-owned values and facts moved by a swap are not reported detached."""
    case = fixture("speech")
    direct = swap_subtrees(
        case.graph,
        DurableItemRef("root"),
        DurableItemRef("other"),
        case.containment,
        drop_crossings(case),
        drop_crossings(case),
    )
    assert [relation.durable_id for _, relation in direct.report.relations] == [
        "link",
        "group",
    ]
    assert not direct.report.boundary_values
    journal = Journal()
    editor = case.graph.edit(journal=journal)
    editor.swap_subtrees(
        DurableItemRef("root"),
        DurableItemRef("other"),
        case.containment,
        drop_crossings(case),
        drop_crossings(case),
    )
    result = editor.freeze()
    assert any(boundary.attributes for boundary in result.boundaries(case.leaf))
    output_layer = next(layer for layer in result.layers if layer.name == case.layer)
    assert any(
        fact.subject == DurableItemRef("middle-0") for fact in output_layer.facts
    )
    assert all(
        dependency.carrier not in {"boundary_values", "layer"}
        for dependency in journal.records[0].report.detached_dependencies
    )
    assert journal.records[0].report.detached_content == direct.report


def test_graph_conveniences_skip_report_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Graph-only calls avoid snapshots and public result calls build one."""
    case = fixture("text")
    source = Subtree(case.alternative, ItemRef(case.root, 0))
    calls = 0
    original = replacement._detachment_report

    def counted(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(replacement, "_detachment_report", counted)
    case.graph.replace_subtree(
        DurableItemRef("root"), case.containment, source, drop_crossings(case)
    )
    assert calls == 0
    replace_subtree(
        case.graph,
        DurableItemRef("root"),
        case.containment,
        source,
        drop_crossings(case),
    )
    assert calls == 1
    case.graph.swap_subtrees(
        DurableItemRef("root"),
        DurableItemRef("other"),
        case.containment,
        drop_crossings(case),
        drop_crossings(case),
    )
    assert calls == 1
    swap_subtrees(
        case.graph,
        DurableItemRef("root"),
        DurableItemRef("other"),
        case.containment,
        drop_crossings(case),
        drop_crossings(case),
    )
    assert calls == 2


def test_swap_reports_second_stage_content_in_source_coordinates() -> None:
    """A first-stage shift does not redirect second-stage report entries."""
    namespace = "urn:tiergraph:replacement:swap-report"
    tier = QualifiedName(namespace, "node")
    item_type = QualifiedName(namespace, "Node")
    members = QualifiedName(namespace, "nodes")
    contains = QualifiedName(namespace, "contains")
    link = QualifiedName(namespace, "link")
    span = QualifiedName(namespace, "span")
    item_note = QualifiedName(namespace, "item-note")
    relation_note = QualifiedName(namespace, "relation-note")
    layer = LayerName(namespace, "hand")
    graph = Graph(
        (NamespaceDeclaration("s", namespace),),
        (
            Tier(
                TierDeclaration(tier, "Nodes"),
                (
                    Item("left"),
                    Item("a"),
                    Item("b"),
                    Item("c"),
                    Item("right"),
                    Item("d"),
                    Item("outside"),
                ),
            ),
        ),
        (
            SimpleRelationDeclaration(members, tier, item_type),
            BipartiteRelationDeclaration(
                contains, item_type, item_type, single_parent=True, acyclic=True
            ),
            BipartiteRelationDeclaration(link, item_type, item_type),
            PolyadicRelationDeclaration(
                span,
                RelationSideDeclaration(
                    (RelationEndpointKind.ITEM,), (tier,), maximum=None
                ),
                RelationSideDeclaration(
                    (
                        RelationEndpointKind.ITEM,
                        RelationEndpointKind.BOUNDARY,
                    ),
                    (tier,),
                    maximum=None,
                    allow_empty=True,
                ),
            ),
        ),
        (
            RelationInstance(contains, ItemRef(tier, 0), ItemRef(tier, 1)),
            RelationInstance(contains, ItemRef(tier, 0), ItemRef(tier, 2)),
            RelationInstance(contains, ItemRef(tier, 0), ItemRef(tier, 3)),
            RelationInstance(contains, ItemRef(tier, 4), ItemRef(tier, 5)),
            RelationInstance(
                link,
                ItemRef(tier, 6),
                ItemRef(tier, 5),
                "second-stage-link",
            ),
        ),
        (
            AttributeDeclaration(item_note, AttributeDomain.ITEM, XsdType.STRING),
            AttributeDeclaration(
                relation_note, AttributeDomain.RELATION_INSTANCE, XsdType.STRING
            ),
        ),
        layers=(
            Layer(
                layer,
                (
                    LayerFact(
                        DurableItemRef("a"),
                        AttributeValue(item_note, XsdType.STRING, "first-stage-fact"),
                    ),
                    LayerFact(
                        RelationInstanceRef(4),
                        AttributeValue(
                            relation_note, XsdType.STRING, "second-stage-fact"
                        ),
                    ),
                ),
            ),
        ),
        polyadic_relations=(
            PolyadicRelationInstance(
                span,
                (ItemRef(tier, 6),),
                (
                    ItemRef(tier, 5),
                    DurableBoundaryRef(DurableItemRef("d"), BoundarySide.BEFORE),
                ),
                "second-stage-span",
            ),
        ),
    )

    policies = ReplacementPolicies(
        relations={
            link: ReplacementAction.DROP,
            span: ReplacementAction.TRIM,
        }
    )
    result = swap_subtrees(
        graph,
        DurableItemRef("left"),
        DurableItemRef("right"),
        contains,
        policies,
        policies,
    )

    assert [relation.durable_id for _, relation in result.report.relations] == [
        "second-stage-link",
        "second-stage-span",
    ]
    reported_span = next(
        relation
        for reference, relation in result.report.relations
        if isinstance(reference, PolyadicInstanceRef)
    )
    assert reported_span == graph.polyadic_relations[0]
    assert [fact.value for _, fact in result.report.facts] == [
        AttributeValue(relation_note, XsdType.STRING, "second-stage-fact")
    ]
    assert {
        (dependency.carrier, dependency.index)
        for dependency in result.report.dependencies
    } >= {("relations", 4), ("layer", 1)}
    trimmed = next(
        relation
        for relation in result.graph.polyadic_relations
        if relation.durable_id == "second-stage-span"
    )
    assert trimmed.sources == (ItemRef(tier, 6),)
    assert trimmed.targets == ()
    endpoints = tuple(
        dependency
        for dependency in result.report.dependencies
        if dependency.carrier == "polyadic_endpoints"
    )
    assert {
        (
            endpoint.index,
            endpoint.endpoint,
            endpoint.endpoint_side,
            endpoint.endpoint_index,
        )
        for endpoint in endpoints
    } == {
        (0, ItemRef(tier, 5), "targets", 0),
        (
            0,
            DurableBoundaryRef(DurableItemRef("d"), BoundarySide.BEFORE),
            "targets",
            1,
        ),
    }


def test_detached_dependency_order_handles_distinct_subject_types() -> None:
    """Report ordering never compares heterogeneous layer subjects directly."""
    case = fixture("text")
    dependencies = (
        DetachedDependency(
            "layer", 0, layer=case.layer, subject=DurableItemRef("middle-0")
        ),
        DetachedDependency(
            "layer", 0, layer=case.layer, subject=RelationInstanceRef(0)
        ),
    )
    assert set(replacement._ordered_detached(dependencies)) == set(dependencies)
    shape = replacement._shape(
        case.graph, DurableItemRef("root"), set(case.containment)
    )
    external = (
        DetachedDependency("boundary_values", 0, tier=case.root),
        DetachedDependency(
            "layer", 1, layer=case.layer, subject=DurableItemRef("root")
        ),
    )
    assert replacement._external_detached(case.graph, shape, external) == external


def test_source_dependency_translation_handles_every_coordinate_carrier() -> None:
    """Intermediate dependency coordinates map through every displaced space."""
    case = fixture("music")
    relation_note = QualifiedName(case.note.namespace, "relation-note")
    graph = replace(
        case.graph,
        attribute_declarations=(
            *case.graph.attribute_declarations,
            AttributeDeclaration(
                relation_note, AttributeDomain.RELATION_INSTANCE, XsdType.STRING
            ),
        ),
        layers=(
            Layer(
                case.layer,
                (
                    LayerFact(
                        ItemRef(case.middle, 2),
                        AttributeValue(case.note, XsdType.STRING, "item"),
                    ),
                    LayerFact(
                        DurableItemRef("root"),
                        AttributeValue(case.note, XsdType.STRING, "durable-item"),
                    ),
                    LayerFact(
                        BoundaryRef(case.leaf, 4),
                        AttributeValue(case.edge, XsdType.STRING, "boundary"),
                    ),
                    LayerFact(
                        RelationInstanceRef(5),
                        AttributeValue(relation_note, XsdType.STRING, "relation"),
                    ),
                    LayerFact(
                        PolyadicInstanceRef(0),
                        AttributeValue(relation_note, XsdType.STRING, "polyadic"),
                    ),
                ),
            ),
        ),
    )
    layer = graph.layers[0]
    dependencies = (
        DetachedDependency("relations", 5, declaration=case.link),
        DetachedDependency("polyadic_relations", 0, declaration=case.group),
        DetachedDependency("boundary_values", 1, tier=case.leaf),
        *(
            DetachedDependency("layer", index, layer=layer.name, subject=fact.subject)
            for index, fact in enumerate(layer.facts)
        ),
        DetachedDependency("other", 0),
    )

    assert (
        replacement._source_dependencies(
            graph, graph, graph.edit().displacement(), dependencies
        )
        == dependencies
    )
    positional_boundary = DetachedDependency(
        "polyadic_endpoints",
        0,
        declaration=case.group,
        endpoint=BoundaryRef(case.leaf, 0),  # type: ignore[arg-type]
        endpoint_side="sources",
        endpoint_index=0,
    )
    with pytest.raises(AssertionError, match="positional boundary"):
        replacement._source_dependencies(
            graph,
            graph,
            graph.edit().displacement(),
            (positional_boundary,),
        )


def test_relation_construction_refuses_a_positional_boundary_endpoint() -> None:
    """Only durable boundary references belong to relation endpoint values."""
    case = fixture("speech")
    malformed = replace(
        case.graph.relations[0],
        left=BoundaryRef(case.root, 0),  # type: ignore[arg-type]
    )
    with pytest.raises(GraphValidationError, match="positional boundary"):
        replace(
            case.graph,
            relations=(malformed, *case.graph.relations[1:]),
        )


def test_replacement_guards_policy_shapes_and_protected_layers() -> None:
    """Bad containment, correspondence, insertion, and protected edits refuse."""
    case = fixture("text")
    source = Subtree(case.alternative, ItemRef(case.root, 0))
    with pytest.raises(GraphValidationError, match="must be an acyclic"):
        replace_subtree(case.graph, ItemRef(case.root, 0), case.link, source)
    with pytest.raises(GraphValidationError, match="outside the old subtree"):
        replace_subtree(
            case.graph,
            ItemRef(case.root, 0),
            case.containment,
            source,
            ReplacementPolicies.corresponding(
                SubtreeCorrespondence(
                    {ItemRef(case.middle, 2): (ItemRef(case.middle, 0),)}
                )
            ),
        )
    editor = case.graph.edit(journal=Journal().protect(case.layer))
    with pytest.raises(GraphValidationError, match="protected layer"):
        editor.replace_subtree(
            ItemRef(case.root, 0),
            case.containment,
            source,
            drop_crossings(case),
        )
    assert editor.freeze() == case.graph


def test_clock_replacement_requires_policy_and_marks_provisional_bindings() -> None:
    """Timed replacement collapses new internal boundaries and remains valid."""
    contains = QualifiedName(test_clock.SEGMENT.namespace, "replacement-contains")
    base_profile = ClockProfile(
        test_clock.fixture(),
        test_clock.CLOCK,
        test_clock.BINDING,
        test_clock.RATE,
        test_clock.UNIT,
    )
    base = (
        base_profile.edit("keep-earlier")
        .insert_item(test_clock.SEGMENT, 2, Item("outside"))
        .freeze()
    )
    graph = replace(
        base,
        relation_declarations=(
            *base.relation_declarations,
            BipartiteRelationDeclaration(
                contains,
                test_clock.SEGMENT_TYPE,
                test_clock.SEGMENT_TYPE,
                single_parent=True,
                acyclic=True,
            ),
        ),
        relations=(
            *base.relations,
            RelationInstance(
                contains,
                ItemRef(test_clock.SEGMENT, 0),
                ItemRef(test_clock.SEGMENT, 1),
            ),
        ),
    )
    alternative = Graph(
        graph.namespaces,
        (
            graph._tiers_by_name[test_clock.CLOCK],
            Tier(
                graph._tiers_by_name[test_clock.SEGMENT].declaration,
                (Item("new-root"), Item("new-0"), Item("new-1")),
            ),
        ),
        graph.relation_declarations,
        (
            RelationInstance(
                contains,
                ItemRef(test_clock.SEGMENT, 0),
                ItemRef(test_clock.SEGMENT, 1),
            ),
            RelationInstance(
                contains,
                ItemRef(test_clock.SEGMENT, 0),
                ItemRef(test_clock.SEGMENT, 2),
            ),
        ),
        graph.attribute_declarations,
    )
    profile = ClockProfile(
        graph,
        test_clock.CLOCK,
        test_clock.BINDING,
        test_clock.RATE,
        test_clock.UNIT,
    )
    with pytest.raises(GraphValidationError, match="requires a named rebinding policy"):
        profile.edit().replace_subtree(
            ItemRef(test_clock.SEGMENT, 0),
            contains,
            Subtree(alternative, ItemRef(test_clock.SEGMENT, 0)),
        )
    journal = Journal()
    editor = profile.edit("drop-to-provisional", journal=journal)
    editor.replace_subtree(
        ItemRef(test_clock.SEGMENT, 0),
        contains,
        Subtree(alternative, ItemRef(test_clock.SEGMENT, 0)),
    )
    changed = editor.freeze()
    checked = ClockProfile(
        changed,
        test_clock.CLOCK,
        test_clock.BINDING,
        test_clock.RATE,
        test_clock.UNIT,
    )
    assert checked.clock_index(
        BoundaryRef(test_clock.SEGMENT, 1)
    ) == checked.clock_index(BoundaryRef(test_clock.SEGMENT, 2))
    report = journal.records[0].report.clock_reports[0]
    assert report.operation is ClockEditOperation.SUBTREE_REPLACEMENT
    assert report.needs_realignment
    editor.undo()
    assert equivalent(editor.freeze(), graph, EquivalenceView.EXACT)


@pytest.mark.parametrize("journaled", (False, True), ids=("plain", "journaled"))
def test_checked_clock_replacement_reports_donor_crossing(
    journaled: bool,
) -> None:
    """Checked clock replacement retains a rejected donor link and its fact."""
    graph = test_clock.fixture()
    namespace = test_clock.SEGMENT.namespace
    containment = QualifiedName(namespace, "clock-replacement-contains")
    token = QualifiedName(namespace, "replacement-token")
    token_type = QualifiedName(namespace, "ReplacementToken")
    tokens = QualifiedName(namespace, "replacement-tokens")
    token_to_segment = QualifiedName(namespace, "replacement-token-to-segment")
    note = QualifiedName(namespace, "replacement-donor-note")
    layer = LayerName(namespace, "replacement-donor-source")
    containment_declaration = BipartiteRelationDeclaration(
        containment,
        test_clock.SEGMENT_TYPE,
        test_clock.SEGMENT_TYPE,
        single_parent=True,
        acyclic=True,
    )
    graph = replace(
        graph,
        relation_declarations=(
            *graph.relation_declarations,
            containment_declaration,
        ),
        relations=(
            *graph.relations,
            RelationInstance(
                containment,
                ItemRef(test_clock.SEGMENT, 0),
                ItemRef(test_clock.SEGMENT, 1),
            ),
        ),
    )
    donor_link = RelationInstance(
        token_to_segment,
        ItemRef(token, 0),
        ItemRef(test_clock.SEGMENT, 1),
        "replacement-donor-link",
    )
    donor_fact = LayerFact(
        DurableRelationRef("replacement-donor-link"),
        AttributeValue(note, XsdType.STRING, "source offset 4"),
    )
    donor = Graph(
        graph.namespaces,
        (
            graph._tiers_by_name[test_clock.CLOCK],
            Tier(
                graph._tiers_by_name[test_clock.SEGMENT].declaration,
                (Item("replacement-root"), Item()),
            ),
            Tier(TierDeclaration(token, "Replacement tokens"), (Item("token-0"),)),
        ),
        (
            *graph.relation_declarations,
            SimpleRelationDeclaration(tokens, token, token_type),
            BipartiteRelationDeclaration(
                token_to_segment, token_type, test_clock.SEGMENT_TYPE
            ),
        ),
        (
            RelationInstance(
                containment,
                ItemRef(test_clock.SEGMENT, 0),
                ItemRef(test_clock.SEGMENT, 1),
            ),
            donor_link,
        ),
        (
            *graph.attribute_declarations,
            AttributeDeclaration(
                note, AttributeDomain.RELATION_INSTANCE, XsdType.STRING
            ),
        ),
        attributes=graph.attributes,
        layers=(Layer(layer, (donor_fact,)),),
    )
    profile = ClockProfile(
        graph,
        test_clock.CLOCK,
        test_clock.BINDING,
        test_clock.RATE,
        test_clock.UNIT,
    )
    journal = Journal()
    subtree = Subtree(donor, ItemRef(test_clock.SEGMENT, 0))
    correspondence = SubtreeCorrespondence(
        {ItemRef(test_clock.SEGMENT, 1): (ItemRef(test_clock.SEGMENT, 1),)},
        {ItemRef(test_clock.SEGMENT, 1): (ItemRef(test_clock.SEGMENT, 1),)},
    )
    policies = ReplacementPolicies(correspondence=correspondence)
    if journaled:
        editor = profile.edit("keep-earlier", journal=journal, check_links=True)
        editor.replace_subtree(
            ItemRef(test_clock.SEGMENT, 0), containment, subtree, policies
        )
        report = journal.records[0].report.detached_content
    else:
        plain = profile.edit("keep-earlier", check_links=True)
        plain.replace_subtree(
            ItemRef(test_clock.SEGMENT, 0), containment, subtree, policies
        )
        report = plain.last_detachment
    assert report is not None
    assert report.donor_relations == ((RelationInstanceRef(1), donor_link),)
    assert report.donor_facts == ((layer, donor_fact),)


def test_clock_correspondence_retains_matching_internal_timing() -> None:
    """A named keep policy carries the timing of a matched internal boundary."""
    contains = QualifiedName(test_clock.SEGMENT.namespace, "timed-contains")
    base = test_clock.fixture()
    graph = replace(
        base,
        relation_declarations=(
            *base.relation_declarations,
            BipartiteRelationDeclaration(
                contains,
                test_clock.SEGMENT_TYPE,
                test_clock.SEGMENT_TYPE,
                single_parent=True,
                acyclic=True,
            ),
        ),
        relations=(
            *base.relations,
            RelationInstance(
                contains,
                ItemRef(test_clock.SEGMENT, 0),
                ItemRef(test_clock.SEGMENT, 1),
            ),
        ),
    )
    alternative = Graph(
        graph.namespaces,
        (
            graph._tiers_by_name[test_clock.CLOCK],
            Tier(
                graph._tiers_by_name[test_clock.SEGMENT].declaration,
                (Item("new-root"), Item("new-child")),
            ),
        ),
        graph.relation_declarations,
        (
            RelationInstance(
                contains,
                ItemRef(test_clock.SEGMENT, 0),
                ItemRef(test_clock.SEGMENT, 1),
            ),
        ),
        graph.attribute_declarations,
    )
    before = ClockProfile(
        graph,
        test_clock.CLOCK,
        test_clock.BINDING,
        test_clock.RATE,
        test_clock.UNIT,
    )
    old_end = before.clock_index(BoundaryRef(test_clock.SEGMENT, 2))
    editor = before.edit("keep-earlier")
    editor.replace_subtree(
        ItemRef(test_clock.SEGMENT, 0),
        contains,
        Subtree(alternative, ItemRef(test_clock.SEGMENT, 0)),
        ReplacementPolicies.corresponding(
            SubtreeCorrespondence(
                {ItemRef(test_clock.SEGMENT, 1): (ItemRef(test_clock.SEGMENT, 1),)}
            )
        ),
    )
    after = editor.profile
    assert after.clock_index(BoundaryRef(test_clock.SEGMENT, 2)) == old_end
    assert not editor.reports[-1].needs_realignment


def test_clock_replacement_uses_the_declared_insertion_point() -> None:
    """A newly used timed tier rebuilds bindings at the actual insertion point."""
    contains = QualifiedName(test_clock.SEGMENT.namespace, "inserted-contains")
    base = test_clock.fixture()
    graph = replace(
        base,
        relation_declarations=(
            *base.relation_declarations,
            BipartiteRelationDeclaration(
                contains,
                test_clock.SEGMENT_TYPE,
                test_clock.SEGMENT_TYPE,
                single_parent=True,
                acyclic=True,
            ),
        ),
    )
    source = replace(
        graph,
        tiers=tuple(
            replace(tier, items=(Item("source-root"), Item("new-child")))
            if tier.declaration.name == test_clock.SEGMENT
            else tier
            for tier in graph.tiers
        ),
        relations=(
            RelationInstance(
                contains,
                ItemRef(test_clock.SEGMENT, 0),
                ItemRef(test_clock.SEGMENT, 1),
            ),
        ),
    )
    profile = ClockProfile(
        graph,
        test_clock.CLOCK,
        test_clock.BINDING,
        test_clock.RATE,
        test_clock.UNIT,
    )
    reference = profile.edit("keep-earlier").insert_item(
        test_clock.SEGMENT, 1, Item("new-child")
    )
    editor = profile.edit("keep-earlier")
    editor.replace_subtree(
        DurableItemRef("segment-0"),
        contains,
        Subtree(source, ItemRef(test_clock.SEGMENT, 0)),
        ReplacementPolicies(insertion_points={test_clock.SEGMENT: 1}),
    )
    assert tuple(
        editor.profile.clock_index(BoundaryRef(test_clock.SEGMENT, index))
        for index in range(4)
    ) == tuple(
        reference.profile.clock_index(BoundaryRef(test_clock.SEGMENT, index))
        for index in range(4)
    )


def test_clock_replacement_validation_is_atomic() -> None:
    """A nonmonotone correspondence refuses without changing editor state."""
    original = test_clock.fixture()
    base = replace(
        original,
        tiers=tuple(
            replace(
                tier,
                items=tuple(Item(f"clock-{index}") for index in range(6)),
            )
            if tier.declaration.name == test_clock.CLOCK
            else replace(
                tier,
                items=tuple(Item(f"segment-{index}") for index in range(4)),
            )
            for tier in original.tiers
        ),
        relations=(),
    )
    base = replace(
        base,
        relations=tuple(
            RelationInstance(
                test_clock.BINDING,
                anchored_boundary(base, BoundaryRef(test_clock.SEGMENT, source)),
                anchored_boundary(base, BoundaryRef(test_clock.CLOCK, target)),
            )
            for source, target in enumerate((1, 2, 3, 4, 5))
        ),
    )
    contains = QualifiedName(test_clock.SEGMENT.namespace, "atomic-contains")
    graph = replace(
        base,
        relation_declarations=(
            *base.relation_declarations,
            BipartiteRelationDeclaration(
                contains,
                test_clock.SEGMENT_TYPE,
                test_clock.SEGMENT_TYPE,
                single_parent=True,
                acyclic=True,
            ),
        ),
        relations=(
            *base.relations,
            *(
                RelationInstance(
                    contains,
                    ItemRef(test_clock.SEGMENT, 0),
                    ItemRef(test_clock.SEGMENT, index),
                )
                for index in (1, 2, 3)
            ),
        ),
    )
    source = replace(
        graph,
        tiers=tuple(
            replace(
                tier,
                items=(
                    Item("source-root"),
                    Item("new-1"),
                    Item("new-2"),
                    Item("new-3"),
                ),
            )
            if tier.declaration.name == test_clock.SEGMENT
            else tier
            for tier in graph.tiers
        ),
        relations=tuple(
            RelationInstance(
                contains,
                ItemRef(test_clock.SEGMENT, 0),
                ItemRef(test_clock.SEGMENT, index),
            )
            for index in (1, 2, 3)
        ),
    )
    profile = ClockProfile(
        graph,
        test_clock.CLOCK,
        test_clock.BINDING,
        test_clock.RATE,
        test_clock.UNIT,
    )
    editor = profile.edit("keep-earlier")
    correspondence = SubtreeCorrespondence(
        {
            ItemRef(test_clock.SEGMENT, 3): (ItemRef(test_clock.SEGMENT, 1),),
            ItemRef(test_clock.SEGMENT, 2): (ItemRef(test_clock.SEGMENT, 3),),
        }
    )
    with pytest.raises(ValueError, match="go backward"):
        editor.replace_subtree(
            DurableItemRef("segment-0"),
            contains,
            Subtree(source, ItemRef(test_clock.SEGMENT, 0)),
            ReplacementPolicies(
                ReplacementAction.FOLLOW, correspondence=correspondence
            ),
        )
    assert editor.freeze() == graph
    assert editor.profile.graph == graph
    assert not editor.reports
    assert not editor._detached_dependencies


def test_clock_replacement_refuses_clock_spine_descendants() -> None:
    """A bound session never restructures the clock tier itself."""
    contains = QualifiedName(test_clock.CLOCK.namespace, "clock-contains")
    base = test_clock.fixture()
    graph = replace(
        base,
        relation_declarations=(
            *base.relation_declarations,
            BipartiteRelationDeclaration(
                contains,
                test_clock.CLOCK_TYPE,
                test_clock.CLOCK_TYPE,
                single_parent=True,
                acyclic=True,
            ),
        ),
        relations=(
            *base.relations,
            RelationInstance(
                contains,
                ItemRef(test_clock.CLOCK, 0),
                ItemRef(test_clock.CLOCK, 1),
            ),
        ),
    )
    profile = ClockProfile(
        graph,
        test_clock.CLOCK,
        test_clock.BINDING,
        test_clock.RATE,
        test_clock.UNIT,
    )
    with pytest.raises(GraphValidationError, match="cannot restructure the clock tier"):
        profile.edit("keep-earlier").replace_subtree(
            ItemRef(test_clock.CLOCK, 0),
            contains,
            Subtree(graph, ItemRef(test_clock.CLOCK, 0)),
        )


def test_clock_replacement_reports_a_fact_on_a_withdrawn_binding() -> None:
    """A binding fact is detached when its old interior boundary disappears."""
    base_profile = ClockProfile(
        test_clock.fixture(),
        test_clock.CLOCK,
        test_clock.BINDING,
        test_clock.RATE,
        test_clock.UNIT,
    )
    base = (
        base_profile.edit("keep-earlier")
        .insert_item(test_clock.SEGMENT, 2, Item("segment-2"))
        .freeze()
    )
    contains = QualifiedName(test_clock.SEGMENT.namespace, "shrinking-contains")
    note = QualifiedName(test_clock.SEGMENT.namespace, "binding-note")
    layer = LayerName(test_clock.SEGMENT.namespace, "binding-facts")
    binding_index = next(
        index
        for index, relation in enumerate(base.relations)
        if relation.declaration == test_clock.BINDING
        and isinstance(relation.left, DurableBoundaryRef)
        and base.resolve_boundary(relation.left) == BoundaryRef(test_clock.SEGMENT, 2)
    )
    graph = replace(
        base,
        relation_declarations=(
            *base.relation_declarations,
            BipartiteRelationDeclaration(
                contains,
                test_clock.SEGMENT_TYPE,
                test_clock.SEGMENT_TYPE,
                single_parent=True,
                acyclic=True,
            ),
        ),
        relations=(
            *base.relations,
            RelationInstance(
                contains,
                ItemRef(test_clock.SEGMENT, 0),
                ItemRef(test_clock.SEGMENT, 1),
            ),
            RelationInstance(
                contains,
                ItemRef(test_clock.SEGMENT, 0),
                ItemRef(test_clock.SEGMENT, 2),
            ),
        ),
        attribute_declarations=(
            *base.attribute_declarations,
            AttributeDeclaration(
                note, AttributeDomain.RELATION_INSTANCE, XsdType.STRING
            ),
        ),
        layers=(
            Layer(
                layer,
                (
                    LayerFact(
                        RelationInstanceRef(binding_index),
                        AttributeValue(note, XsdType.STRING, "keep with binding"),
                    ),
                ),
            ),
        ),
    )
    source = Graph(
        graph.namespaces,
        (
            graph._tiers_by_name[test_clock.CLOCK],
            Tier(
                graph._tiers_by_name[test_clock.SEGMENT].declaration,
                (Item("new-root"), Item("new-child")),
            ),
        ),
        graph.relation_declarations,
        (
            RelationInstance(
                contains,
                ItemRef(test_clock.SEGMENT, 0),
                ItemRef(test_clock.SEGMENT, 1),
            ),
        ),
        graph.attribute_declarations,
    )
    profile = ClockProfile(
        graph,
        test_clock.CLOCK,
        test_clock.BINDING,
        test_clock.RATE,
        test_clock.UNIT,
    )
    journal = Journal()
    editor = profile.edit("keep-earlier", journal=journal)
    editor.replace_subtree(
        ItemRef(test_clock.SEGMENT, 0),
        contains,
        Subtree(source, ItemRef(test_clock.SEGMENT, 0)),
    )
    dependency = next(
        dependency
        for dependency in journal.records[0].report.detached_dependencies
        if dependency.layer == layer
    )
    assert dependency.index == 0


def test_corresponding_boundary_origins_join_adjacent_matches() -> None:
    """Clock correspondence recognizes a shared boundary exactly once."""
    tier = QualifiedName("urn:tiergraph:replacement:clock-helper", "tier")
    assert clock_module._corresponding_boundary_origins(
        {
            ItemRef(tier, 0): (ItemRef(tier, 0),),
            ItemRef(tier, 1): (ItemRef(tier, 1),),
        }
    ) == {
        BoundaryRef(tier, 0): 0,
        BoundaryRef(tier, 1): 1,
        BoundaryRef(tier, 2): 2,
    }


def test_public_policy_values_validate_and_detach_caller_containers() -> None:
    """Public replacement values reject unordered or malformed coordinates."""
    case = fixture("music")
    mapping = {ItemRef(case.middle, 0): (ItemRef(case.middle, 0),)}
    correspondence = SubtreeCorrespondence(mapping)
    mapping.clear()
    assert correspondence.items
    policies = ReplacementPolicies(
        ReplacementAction.FOLLOW,
        relations={case.link: ReplacementAction.ABANDON},
        insertion_points={case.middle: 0},
    )
    assert policies.default is ReplacementAction.FOLLOW
    with pytest.raises(ValueError, match="nonnegative"):
        ReplacementPolicies(insertion_points={case.middle: -1})
    with pytest.raises(TypeError, match="subtree graph"):
        Subtree(object(), ItemRef(case.root, 0))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="subtree root"):
        Subtree(case.graph, object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="qualified name"):
        replace_subtree(
            case.graph,
            ItemRef(case.root, 0),
            "not-a-name",  # type: ignore[arg-type]
            Subtree(case.alternative, ItemRef(case.root, 0)),
        )


def test_replacement_value_and_helper_refusals_are_typed() -> None:
    """Malformed public values and unreachable helper shapes fail deliberately."""
    case = fixture("text")
    with pytest.raises(TypeError, match="sources"):
        SubtreeCorrespondence({object(): ()})  # type: ignore[dict-item]
    with pytest.raises(TypeError, match="targets"):
        SubtreeCorrespondence(
            {ItemRef(case.middle, 0): (object(),)}  # type: ignore[dict-item]
        )
    aligned = {ItemRef(case.middle, 0): (ItemRef(case.middle, 0),)}
    with pytest.raises(TypeError, match="identity correspondence sources"):
        SubtreeCorrespondence(aligned, {object(): ()})  # type: ignore[dict-item]
    with pytest.raises(TypeError, match="identity correspondence targets"):
        SubtreeCorrespondence(
            aligned,
            {ItemRef(case.middle, 0): (object(),)},  # type: ignore[dict-item]
        )
    with pytest.raises(ValueError, match="belong to its alignment hole"):
        SubtreeCorrespondence(
            aligned,
            {ItemRef(case.middle, 0): (ItemRef(case.middle, 1),)},
        )
    with pytest.raises(TypeError, match="qualified names"):
        replacement._containment_names([])
    with pytest.raises(ValueError, match="unique"):
        replacement._containment_names((case.containment[0], case.containment[0]))
    with pytest.raises(TypeError, match="ordered iterable"):
        replacement._containment_names(
            {"name": case.containment[0]}  # type: ignore[dict-item]
        )
    boundary = DurableBoundaryRef(DurableItemRef("middle-0"), BoundarySide.BEFORE)
    with pytest.raises(GraphValidationError, match="must be items"):
        replacement._item_endpoint(case.graph, boundary)
    assert replacement._endpoint_item(case.graph, boundary) is None
    assert replacement._endpoint_item(
        case.graph, DurableItemRef("middle-0")
    ) == ItemRef(case.middle, 0)
    assert replacement._remap_unaffected_endpoint(
        DurableItemRef("root"), {}
    ) == DurableItemRef("root")
    assert replacement._remap_unaffected_endpoint(boundary, {}) == boundary
    with pytest.raises(GraphValidationError, match="unsupported"):
        replacement._remap_unaffected_endpoint(object(), {})  # type: ignore[arg-type]
    with pytest.raises(GraphValidationError, match="must be items"):
        replacement._mapped_new_endpoint(
            case.graph,
            boundary,
            ItemRef(case.root, 0),
            ItemRef(case.root, 0),
            {},
        )
    with pytest.raises(GraphValidationError, match="leaves the subtree"):
        replacement._mapped_new_endpoint(
            case.graph,
            ItemRef(case.middle, 2),
            ItemRef(case.root, 0),
            ItemRef(case.root, 0),
            {},
        )


def test_boundary_and_fact_correspondence_helpers_cover_all_subject_kinds() -> None:
    """Boundary, relation, and fallback fact subjects have explicit outcomes."""
    case = fixture("music")
    old_runs = {case.leaf: (0, 1, 2)}
    insertions = {case.leaf: 0}
    new_runs = {case.leaf: (0, 1)}
    correspondence = {
        ItemRef(case.leaf, 0): (ItemRef(case.leaf, 0),),
        ItemRef(case.leaf, 1): (ItemRef(case.leaf, 1),),
    }
    assert (
        replacement._boundary_correspondence(
            BoundaryRef(case.root, 0), correspondence, old_runs, insertions, new_runs
        )
        == ()
    )
    assert replacement._boundary_correspondence(
        BoundaryRef(case.leaf, 0), correspondence, old_runs, insertions, new_runs
    ) == (BoundaryRef(case.leaf, 0),)
    assert replacement._boundary_correspondence(
        BoundaryRef(case.leaf, 3), correspondence, old_runs, insertions, new_runs
    ) == (BoundaryRef(case.leaf, 2),)
    binary = {5: (2, 3)}
    polyadic = {0: (1,)}
    assert (
        replacement._fact_subjects(
            case.graph,
            ItemRef(case.leaf, 0),
            correspondence,
            binary,
            polyadic,
            ReplacementAction.ABANDON,
            old_runs,
            insertions,
            new_runs,
        )
        == ()
    )
    assert replacement._fact_subjects(
        case.graph,
        RelationInstanceRef(5),
        correspondence,
        binary,
        polyadic,
        ReplacementAction.SPLIT,
        old_runs,
        insertions,
        new_runs,
    ) == (RelationInstanceRef(2), RelationInstanceRef(3))
    assert replacement._fact_subjects(
        case.graph,
        PolyadicInstanceRef(0),
        correspondence,
        binary,
        polyadic,
        ReplacementAction.FOLLOW,
        old_runs,
        insertions,
        new_runs,
    ) == (PolyadicInstanceRef(1),)
    assert replacement._fact_subjects(
        case.graph,
        DurableRelationRef("link"),
        correspondence,
        binary,
        polyadic,
        ReplacementAction.SPLIT,
        old_runs,
        insertions,
        new_runs,
    ) == (RelationInstanceRef(2), RelationInstanceRef(3))
    assert replacement._fact_subjects(
        case.graph,
        DurablePolyadicRef("group"),
        correspondence,
        binary,
        polyadic,
        ReplacementAction.FOLLOW,
        old_runs,
        insertions,
        new_runs,
    ) == (DurablePolyadicRef("group"),)
    assert (
        replacement._fact_subjects(
            case.graph,
            DocumentRef(),
            correspondence,
            binary,
            polyadic,
            ReplacementAction.SPLIT,
            old_runs,
            insertions,
            new_runs,
        )
        == ()
    )
    orphan = OrphanedSubject(case.middle, ItemRef(case.middle, 0))
    assert replacement._subject_data(orphan)["kind"] == "orphaned"
    assert replacement._subject_data(RelationInstanceRef(2)) == {
        "kind": "relation-instance",
        "index": 2,
    }


def test_source_content_helpers_cover_boundaries_and_fact_subjects() -> None:
    """Copied subtree content maps each supported live subject deliberately."""
    case = fixture("speech")
    shape = replacement._shape(case.graph, ItemRef(case.root, 0), set(case.containment))
    mapping = {item: item for item in shape.descendants}
    anchored = DurableBoundaryRef(DurableItemRef("middle-0"), BoundarySide.BEFORE)
    tier_anchored = DurableBoundaryRef(case.middle, BoundarySide.BEFORE)
    members = frozenset({shape.root, *shape.descendants})
    assert replacement._boundary_owner(case.graph, anchored) == ItemRef(case.middle, 0)
    assert replacement._boundary_owner(case.graph, tier_anchored) is None
    assert replacement._source_endpoint_inside(case.graph, anchored, members)
    assert not replacement._source_endpoint_inside(case.graph, tier_anchored, members)
    assert (
        replacement._copy_source_endpoint(
            case.graph,
            case.graph,
            anchored,
            shape.root,
            shape.root,
            mapping,
        )
        == anchored
    )
    with pytest.raises(GraphValidationError, match="tier-anchored"):
        replacement._copy_source_endpoint(
            case.graph,
            case.graph,
            tier_anchored,
            shape.root,
            shape.root,
            mapping,
        )

    anonymous_source = case.alternative
    anonymous_target = replace(
        anonymous_source,
        tiers=(
            anonymous_source._tiers_by_name[case.root],
            replace(
                anonymous_source._tiers_by_name[case.middle],
                items=(Item(),),
            ),
            anonymous_source._tiers_by_name[case.leaf],
        ),
        relations=(),
    )
    with pytest.raises(GraphValidationError, match="durable target item"):
        replacement._copy_source_endpoint(
            anonymous_source,
            anonymous_target,
            DurableBoundaryRef(DurableItemRef("new-middle"), BoundarySide.BEFORE),
            ItemRef(case.root, 0),
            ItemRef(case.root, 0),
            {ItemRef(case.middle, 0): ItemRef(case.middle, 0)},
        )

    binary = {0: (7,), 5: (8,)}
    polyadic = {0: (4,)}

    def copied(subject: object) -> object:
        return replacement._copy_source_fact_subject(
            case.graph,
            case.graph,
            subject,  # type: ignore[arg-type]
            shape,
            shape.root,
            mapping,
            binary,
            polyadic,
        )

    assert copied(ItemRef(case.root, 0)) is None
    assert copied(ItemRef(case.middle, 0)) == ItemRef(case.middle, 0)
    assert copied(BoundaryRef(case.middle, 1)) == BoundaryRef(case.middle, 1)
    assert copied(BoundaryRef(case.root, 0)) is None
    assert copied(anchored) == anchored
    assert (
        copied(DurableBoundaryRef(DurableItemRef("root"), BoundarySide.BEFORE)) is None
    )
    assert copied(RelationInstanceRef(0)) == RelationInstanceRef(7)
    assert copied(RelationInstanceRef(4)) is None
    assert copied(PolyadicInstanceRef(0)) == PolyadicInstanceRef(4)
    assert copied(PolyadicInstanceRef(2)) is None
    assert copied(DurableRelationRef("link")) == DurableRelationRef("link")
    assert copied(DurableRelationRef("missing")) is None
    assert copied(DurablePolyadicRef("group")) == DurablePolyadicRef("group")
    assert copied(DurablePolyadicRef("missing")) is None
    assert copied(DocumentRef()) is None


def test_polyadic_containment_and_coordinate_swap() -> None:
    """Polyadic trees replace correctly, and coordinate roots survive a swap."""
    namespace = "urn:tiergraph:replacement:polyadic"
    tier = QualifiedName(namespace, "node")
    kind = QualifiedName(namespace, "Node")
    members = QualifiedName(namespace, "nodes")
    contains = QualifiedName(namespace, "contains")
    side = RelationSideDeclaration((RelationEndpointKind.ITEM,), (tier,), maximum=None)
    source_side = RelationSideDeclaration(
        (RelationEndpointKind.ITEM,), (tier,), maximum=1
    )
    declarations = (
        SimpleRelationDeclaration(members, tier, kind),
        PolyadicRelationDeclaration(
            contains,
            source_side,
            side,
            single_parent=True,
            acyclic=True,
        ),
    )
    graph = Graph(
        (NamespaceDeclaration("p", namespace),),
        (
            Tier(
                TierDeclaration(tier, "Nodes"),
                (Item("left"), Item("a"), Item("right"), Item("b")),
            ),
        ),
        declarations,
        polyadic_relations=(
            PolyadicRelationInstance(
                contains, (ItemRef(tier, 0),), (ItemRef(tier, 1),), "left-tree"
            ),
            PolyadicRelationInstance(
                contains, (ItemRef(tier, 2),), (ItemRef(tier, 3),), "right-tree"
            ),
        ),
    )
    swapped = swap_subtrees(graph, ItemRef(tier, 0), ItemRef(tier, 2), contains).graph
    assert tuple(item.durable_id for item in swapped.tiers[0].items) == (
        "left",
        "b",
        "right",
        "a",
    )
    replacement_graph = Graph(
        graph.namespaces,
        (Tier(graph.tiers[0].declaration, (Item("root"), Item("x"), Item("y"))),),
        declarations,
        polyadic_relations=(
            PolyadicRelationInstance(
                contains,
                (ItemRef(tier, 0),),
                (ItemRef(tier, 1), ItemRef(tier, 2)),
            ),
        ),
    )
    result = replace_subtree(
        graph,
        DurableItemRef("left"),
        contains,
        Subtree(replacement_graph, ItemRef(tier, 0)),
    ).graph
    assert len(result.polyadic_relations) == 2


def test_correspondence_alignment_and_structural_guards() -> None:
    """Local alignment is stable and malformed replacement shapes refuse."""
    case = fixture("speech")
    matching_middle = replace(
        case.alternative._tiers_by_name[case.middle], items=(Item("middle-0"),)
    )
    aligned_source = replace(
        case.alternative,
        tiers=tuple(
            matching_middle if tier.declaration.name == case.middle else tier
            for tier in case.alternative.tiers
        ),
    )
    journal = Journal()
    editor = case.graph.edit(journal=journal)
    editor.replace_subtree(
        DurableItemRef("root"),
        case.containment,
        Subtree(aligned_source, ItemRef(case.root, 0)),
        drop_crossings(case, tts_replacement_profile()),
    )
    aligned = editor.freeze()
    effective = journal.records[0].report.correspondence
    assert effective is not None
    assert ItemRef(case.middle, 0) in effective.items
    assert ItemRef(case.middle, 0) not in effective.identity_correspondence
    assert next(
        relation for relation in aligned.relations if relation.declaration == case.link
    ).right == ItemRef(case.middle, 0)
    output_layer = next(layer for layer in aligned.layers if layer.name == case.layer)
    assert any(fact.subject == ItemRef(case.middle, 0) for fact in output_layer.facts)
    second = replace_subtree(
        aligned,
        DurableItemRef("root"),
        case.containment,
        Subtree(aligned_source, ItemRef(case.root, 0)),
        drop_crossings(case, tts_replacement_profile()),
    ).graph
    assert equivalent(second, aligned, EquivalenceView.IDENTIFIED)

    with pytest.raises(GraphValidationError, match="outside the new subtree"):
        replace_subtree(
            case.graph,
            ItemRef(case.root, 0),
            case.containment,
            Subtree(case.alternative, ItemRef(case.root, 0)),
            ReplacementPolicies.corresponding(
                SubtreeCorrespondence(
                    {ItemRef(case.middle, 0): (ItemRef(case.root, 0),)}
                )
            ),
        )
    with pytest.raises(GraphValidationError, match="within one tier"):
        replace_subtree(
            case.graph,
            ItemRef(case.root, 0),
            case.containment,
            Subtree(case.alternative, ItemRef(case.root, 0)),
            ReplacementPolicies.corresponding(
                SubtreeCorrespondence(
                    {ItemRef(case.middle, 0): (ItemRef(case.leaf, 0),)}
                )
            ),
        )
    with pytest.raises(GraphValidationError, match="same tier"):
        replace_subtree(
            case.graph,
            ItemRef(case.root, 0),
            case.containment,
            Subtree(case.alternative, ItemRef(case.middle, 0)),
        )

    relations = list(case.graph.relations)
    relations[1] = RelationInstance(
        case.containment[0], ItemRef(case.root, 0), ItemRef(case.middle, 2)
    )
    noncontiguous = replace(case.graph, relations=tuple(relations))
    with pytest.raises(GraphValidationError, match="contiguous run"):
        replace_subtree(
            noncontiguous,
            ItemRef(case.root, 0),
            case.containment,
            Subtree(case.alternative, ItemRef(case.root, 0)),
        )

    with pytest.raises(GraphValidationError, match="outside tier"):
        replace_subtree(
            case.graph,
            ItemRef(case.root, 1),
            case.containment,
            Subtree(case.alternative, ItemRef(case.root, 0)),
            ReplacementPolicies(insertion_points={case.middle: 99, case.leaf: 99}),
        )


def test_schema_mismatch_and_unknown_containment_refuse() -> None:
    """Replacement uses the target's exact containment declarations."""
    case = fixture("text")
    missing = QualifiedName(case.root.namespace, "missing-containment")
    with pytest.raises(GraphValidationError, match="must be an acyclic"):
        replace_subtree(
            case.graph,
            ItemRef(case.root, 0),
            missing,
            Subtree(case.alternative, ItemRef(case.root, 0)),
        )
    declaration_note = QualifiedName(case.root.namespace, "declaration-note")
    attribute_declaration = AttributeDeclaration(
        declaration_note, AttributeDomain.RELATION_DECLARATION, XsdType.STRING
    )
    changed_declarations = tuple(
        replace(
            declaration,
            attributes=(AttributeValue(declaration_note, XsdType.STRING, "changed"),),
        )
        if declaration.name == case.containment[0]
        else declaration
        for declaration in case.alternative.relation_declarations
    )
    changed = replace(
        case.alternative,
        relation_declarations=changed_declarations,
        attribute_declarations=(
            *case.alternative.attribute_declarations,
            attribute_declaration,
        ),
    )
    with pytest.raises(GraphValidationError, match="declarations differ"):
        replace_subtree(
            case.graph,
            ItemRef(case.root, 0),
            case.containment,
            Subtree(changed, ItemRef(case.root, 0)),
        )


def test_boundary_fact_and_outer_value_follow_correspondence() -> None:
    """Exact boundary facts and unique outer values follow correspondence."""
    case = fixture("music")
    layer = next(item for item in case.graph.layers if item.name == case.layer)
    graph = replace(
        case.graph,
        boundary_values=(
            *case.graph.boundary_values,
            Boundary(
                BoundaryRef(case.leaf, 0),
                (AttributeValue(case.edge, XsdType.STRING, "outer"),),
            ),
            Boundary(
                BoundaryRef(case.leaf, 4),
                (AttributeValue(case.edge, XsdType.STRING, "unaffected"),),
            ),
        ),
        layers=(
            replace(
                layer,
                facts=(
                    *layer.facts,
                    LayerFact(
                        BoundaryRef(case.leaf, 1),
                        AttributeValue(case.edge, XsdType.STRING, "reviewed"),
                    ),
                ),
            ),
        ),
    )
    policies = drop_crossings(
        case,
        ReplacementPolicies.corresponding(
            SubtreeCorrespondence({ItemRef(case.leaf, 0): (ItemRef(case.leaf, 0),)}),
            layers={case.layer: ReplacementAction.FOLLOW},
        ),
    )
    journal = Journal()
    editor = graph.edit(journal=journal)
    editor.replace_subtree(
        ItemRef(case.root, 0),
        case.containment,
        Subtree(case.alternative, ItemRef(case.root, 0)),
        policies,
    )
    result = editor.freeze()
    assert result.boundaries(case.leaf)[0].attributes == (
        AttributeValue(case.edge, XsdType.STRING, "outer"),
    )
    assert result.boundaries(case.leaf)[3].attributes == (
        AttributeValue(case.edge, XsdType.STRING, "unaffected"),
    )
    assert (
        LayerFact(
            BoundaryRef(case.leaf, 1),
            AttributeValue(case.edge, XsdType.STRING, "reviewed"),
        )
        in result.layers[0].facts
    )
    assert all(
        dependency.subject != BoundaryRef(case.leaf, 1)
        for dependency in journal.records[0].report.detached_dependencies
    )


def test_ambiguous_follow_boundary_is_reported_with_its_tier() -> None:
    """A boundary that cannot follow exactly one counterpart never vanishes silently."""
    case = fixture("speech")
    policies = drop_crossings(
        case,
        ReplacementPolicies(
            ReplacementAction.FOLLOW,
            correspondence=SubtreeCorrespondence(
                {
                    ItemRef(case.leaf, 0): (
                        ItemRef(case.leaf, 0),
                        ItemRef(case.leaf, 1),
                    )
                }
            ),
        ),
    )
    journal = Journal()
    editor = case.graph.edit(journal=journal)
    editor.replace_subtree(
        DurableItemRef("root"),
        case.containment,
        Subtree(case.alternative, ItemRef(case.root, 0)),
        policies,
    )
    assert not any(
        boundary.attributes for boundary in editor.freeze().boundaries(case.leaf)
    )
    dependency = next(
        item
        for item in journal.records[0].report.detached_dependencies
        if item.carrier == "boundary_values"
    )
    assert dependency.index == 1
    assert dependency.tier == case.leaf


def test_boundary_detachments_on_different_tiers_remain_distinct() -> None:
    """A boundary report includes the tier as well as its tier-local index."""
    case = fixture("music")
    graph = replace(
        case.graph,
        boundary_values=(
            *case.graph.boundary_values,
            Boundary(
                BoundaryRef(case.middle, 1),
                (AttributeValue(case.edge, XsdType.STRING, "middle"),),
            ),
        ),
    )
    journal = Journal()
    graph.edit(journal=journal).replace_subtree(
        DurableItemRef("root"),
        case.containment,
        Subtree(case.alternative, ItemRef(case.root, 0)),
        drop_crossings(case),
    )
    dependencies = {
        (item.tier, item.index)
        for item in journal.records[0].report.detached_dependencies
        if item.carrier == "boundary_values"
    }
    assert dependencies == {(case.middle, 1), (case.leaf, 1)}


def test_dependency_helpers_cover_boundary_and_durable_relation_subjects() -> None:
    """Dependency detection includes anchored boundaries and durable instances."""
    case = fixture("speech")
    descendants = frozenset({ItemRef(case.middle, 0)})
    anchored = DurableBoundaryRef(DurableItemRef("middle-0"), BoundarySide.BEFORE)
    assert replacement._endpoint_touches(case.graph, anchored, descendants)
    assert not replacement._endpoint_touches(
        case.graph,
        DurableBoundaryRef(DurableItemRef("middle-outside"), BoundarySide.BEFORE),
        descendants,
    )
    assert replacement._subject_touches(
        case.graph, BoundaryRef(case.middle, 0), descendants, {5}, {0}
    )
    assert replacement._subject_touches(
        case.graph, RelationInstanceRef(5), descendants, {5}, {0}
    )
    assert replacement._subject_touches(
        case.graph, PolyadicInstanceRef(0), descendants, {5}, {0}
    )
    assert replacement._subject_touches(
        case.graph, DurableRelationRef("link"), descendants, {5}, {0}
    )
    assert replacement._subject_touches(
        case.graph, DurablePolyadicRef("group"), descendants, {5}, {0}
    )
    assert not replacement._subject_touches(
        case.graph, DocumentRef(), descendants, {5}, {0}
    )
    assert replacement._donor_relation_fact(
        case.graph, PolyadicInstanceRef(0), set(), {0}
    )
    assert replacement._donor_relation_fact(
        case.graph, DurablePolyadicRef("group"), set(), {0}
    )
    multi = {
        ItemRef(case.middle, 0): (
            ItemRef(case.middle, 0),
            ItemRef(case.middle, 1),
        )
    }
    tier_boundary = DurableBoundaryRef(case.middle, BoundarySide.BEFORE)
    assert (
        replacement._crossing_targets(case.graph, case.graph, tier_boundary, multi)
        == ()
    )
    assert replacement._crossing_targets(case.graph, case.graph, anchored, multi) == (
        DurableBoundaryRef(DurableItemRef("middle-0"), BoundarySide.BEFORE),
        DurableBoundaryRef(DurableItemRef("middle-1"), BoundarySide.BEFORE),
    )
    anonymous = replace(
        case.graph,
        tiers=tuple(
            replace(
                tier,
                items=tuple(Item(None, item.attributes) for item in tier.items),
            )
            if tier.declaration.name == case.middle
            else tier
            for tier in case.graph.tiers
        ),
        relations=(),
        polyadic_relations=(),
        layers=(),
    )
    assert (
        replacement._crossing_targets(
            case.graph,
            anonymous,
            anchored,
            {ItemRef(case.middle, 0): (ItemRef(case.middle, 0),)},
        )
        == ()
    )

    crossing = RelationInstance(
        case.link, ItemRef(case.root, 1), ItemRef(case.middle, 0)
    )
    with pytest.raises(GraphValidationError, match="no correspondence"):
        replacement._carry_binary_relation(
            case.graph,
            case.graph,
            5,
            crossing,
            descendants,
            {},
            {ItemRef(case.root, 1): ItemRef(case.root, 1)},
            None,
        )
    with pytest.raises(GraphValidationError, match="ambiguous correspondence"):
        replacement._carry_binary_relation(
            case.graph,
            case.graph,
            5,
            crossing,
            descendants,
            multi,
            {ItemRef(case.root, 1): ItemRef(case.root, 1)},
            None,
        )

    source_missing = PolyadicRelationInstance(
        case.group,
        (ItemRef(case.middle, 0),),
        (ItemRef(case.middle, 2),),
    )
    assert replacement._carry_polyadic_relation(
        case.graph,
        case.graph,
        0,
        source_missing,
        descendants,
        {},
        {ItemRef(case.middle, 2): ItemRef(case.middle, 2)},
        ReplacementAction.DROP,
    ) == (None, ())


def test_polyadic_crossing_invariants_name_invalid_carried_shapes() -> None:
    """Carried crossings enforce side arity and resolved source uniqueness."""
    case = fixture("speech")
    item_side = RelationSideDeclaration(
        (RelationEndpointKind.ITEM,), (case.middle,), maximum=1
    )
    bounded = PolyadicRelationDeclaration(case.group, item_side, item_side)
    first = ItemRef(case.middle, 0)
    second = ItemRef(case.middle, 1)
    assert (
        replacement._polyadic_crossing_issue(
            case.graph, bounded, (first, second), (first,)
        )
        == "source arity 2 outside declared bounds 1..1"
    )
    assert (
        replacement._polyadic_crossing_issue(
            case.graph, bounded, (first,), (first, second)
        )
        == "target arity 2 outside declared bounds 1..1"
    )

    mixed_side = RelationSideDeclaration(
        (RelationEndpointKind.BOUNDARY, RelationEndpointKind.ITEM),
        (case.middle,),
        maximum=2,
    )
    unique = PolyadicRelationDeclaration(
        case.group, mixed_side, item_side, unique_sources=True
    )
    anchored = DurableBoundaryRef(DurableItemRef("middle-0"), BoundarySide.BEFORE)
    assert (
        replacement._polyadic_crossing_issue(
            case.graph, unique, (anchored, anchored), (first,)
        )
        == "duplicate sources in a declared unique-source relation"
    )

    target = replace(
        case.graph,
        relation_declarations=tuple(
            replace(declaration, unique_sources=True)
            if isinstance(declaration, PolyadicRelationDeclaration)
            and declaration.name == case.group
            else declaration
            for declaration in case.graph.relation_declarations
        ),
    )
    crossing = PolyadicRelationInstance(
        case.group, (first,), (ItemRef(case.middle, 2),)
    )
    assert replacement._carry_polyadic_relation(
        case.graph,
        target,
        0,
        crossing,
        frozenset({first}),
        {first: (second, second)},
        {ItemRef(case.middle, 2): ItemRef(case.middle, 2)},
        ReplacementAction.DROP,
    ) == (None, ())


def test_report_helpers_track_durable_boundaries_and_insertions() -> None:
    """Boundary loss and positional relation insertion images remain exact."""
    case = fixture("speech")
    anchored = DurableBoundaryRef(DurableItemRef("middle-0"), BoundarySide.BEFORE)
    durable_value = AttributeValue(case.edge, XsdType.STRING, "durable")
    durable_graph = replace(
        case.graph,
        boundary_values=(
            *case.graph.boundary_values,
            Boundary(anchored, (durable_value,)),
        ),
    )
    without_durable_value = replace(
        durable_graph,
        boundary_values=tuple(
            boundary
            for boundary in durable_graph.boundary_values
            if boundary.reference != anchored
        ),
    )
    assert replacement._removed_boundary_values(
        durable_graph,
        without_durable_value,
        durable_graph.edit().displacement(),
    ) == ((anchored, durable_value),)
    editor = case.graph.edit()
    images = replacement._insert_relations(
        editor,
        (
            (
                0,
                RelationInstance(
                    case.link, ItemRef(case.root, 0), ItemRef(case.middle, 0)
                ),
                5,
            ),
            (
                0,
                RelationInstance(
                    case.link, ItemRef(case.root, 0), ItemRef(case.middle, 1)
                ),
                6,
            ),
        ),
        set(),
    )
    assert images == {5: (0,), 6: (1,)}
