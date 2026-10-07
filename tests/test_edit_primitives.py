"""Round trips and local refusals for the reversible editing basis."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, cast

import pytest

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
    dump_bytes,
    equivalent,
)


@dataclass(frozen=True)
class DomainFixture:
    """Name the domain-specific declarations used by one editing fixture."""

    graph: Graph
    namespace: str
    unit: QualifiedName
    spare: QualifiedName
    link: QualifiedName
    group: QualifiedName
    note: QualifiedName
    boundary_note: QualifiedName
    relation_note: QualifiedName


def fixture(domain: str) -> DomainFixture:
    """Return parallel text or music content with binary and polyadic links."""
    namespace = f"urn:edit-primitives:{domain}"

    def name(local: str) -> QualifiedName:
        return QualifiedName(namespace, local)

    unit = name("token" if domain == "text" else "note")
    spare = name("line" if domain == "text" else "voice")
    unit_members = name("tokens" if domain == "text" else "notes")
    spare_members = name("lines" if domain == "text" else "voices")
    unit_type = name("Token" if domain == "text" else "Note")
    spare_type = name("Line" if domain == "text" else "Voice")
    link = name("next")
    group = name("group")
    note = name("note")
    boundary_note = name("boundary-note")
    relation_note = name("relation-note")
    side = RelationSideDeclaration((RelationEndpointKind.ITEM,), (unit,))
    graph = Graph(
        (NamespaceDeclaration("d", namespace),),
        (
            Tier(
                TierDeclaration(unit, unit.local_name),
                (Item("u0"), Item("u1"), Item("u2")),
            ),
            Tier(
                TierDeclaration(spare, spare.local_name),
                (
                    Item(attributes=(AttributeValue(note, XsdType.STRING, "spare-0"),)),
                    Item(attributes=(AttributeValue(note, XsdType.STRING, "spare-1"),)),
                    Item(attributes=(AttributeValue(note, XsdType.STRING, "spare-2"),)),
                ),
            ),
        ),
        (
            SimpleRelationDeclaration(unit_members, unit, unit_type),
            SimpleRelationDeclaration(spare_members, spare, spare_type),
            BipartiteRelationDeclaration(link, unit_type, unit_type),
            PolyadicRelationDeclaration(group, side, side),
        ),
        (RelationInstance(link, ItemRef(unit, 0), ItemRef(unit, 1)),),
        (
            AttributeDeclaration(note, AttributeDomain.ITEM, XsdType.STRING),
            AttributeDeclaration(
                boundary_note, AttributeDomain.BOUNDARY, XsdType.STRING
            ),
            AttributeDeclaration(
                relation_note, AttributeDomain.RELATION_INSTANCE, XsdType.STRING
            ),
        ),
        (
            Boundary(
                BoundaryRef(unit, 1),
                (AttributeValue(boundary_note, XsdType.STRING, "edge"),),
            ),
        ),
        polyadic_relations=(
            PolyadicRelationInstance(group, (ItemRef(unit, 0),), (ItemRef(unit, 2),)),
        ),
    )
    return DomainFixture(
        graph,
        namespace,
        unit,
        spare,
        link,
        group,
        note,
        boundary_note,
        relation_note,
    )


def assert_view(left: Graph, right: Graph, view: EquivalenceView) -> None:
    """Assert one named equivalence view with a compact failure surface."""
    assert equivalent(left, right, view)


@pytest.mark.parametrize("domain", ["text", "music"])
def test_s3_operations_and_inverses_round_trip_on_two_domains(domain: str) -> None:
    """Every S3 operation has its declared inverse on both domain fixtures."""
    case = fixture(domain)
    start = case.graph

    inserted_item = start.insert_item(case.spare, 1, Item())
    assert_view(
        inserted_item.remove_item(ItemRef(case.spare, 1)),
        start,
        EquivalenceView.EXACT,
    )
    removed_item = start.remove_item(ItemRef(case.spare, 1))
    assert_view(
        removed_item.insert_item(case.spare, 1, start.tiers[1].items[1]),
        start,
        EquivalenceView.EXACT,
    )
    value = AttributeValue(case.note, XsdType.STRING, "marked")
    valued = start.set_attribute(ItemRef(case.spare, 0), value)
    old_value = start.tiers[1].items[0].attributes[0]
    assert_view(
        valued.set_attribute(ItemRef(case.spare, 0), old_value),
        start,
        EquivalenceView.EXACT,
    )
    added_value = start.set_attribute(ItemRef(case.unit, 0), value)
    assert_view(
        added_value.remove_attribute(ItemRef(case.unit, 0), case.note),
        start,
        EquivalenceView.EXACT,
    )
    moved = start.move_item(ItemRef(case.spare, 0), 2)
    assert_view(
        moved.move_item(ItemRef(case.spare, 2), 0), start, EquivalenceView.EXACT
    )
    swapped = start.swap_items(ItemRef(case.spare, 0), ItemRef(case.spare, 2))
    assert_view(
        swapped.swap_items(ItemRef(case.spare, 0), ItemRef(case.spare, 2)),
        start,
        EquivalenceView.EXACT,
    )

    inserted = RelationInstance(case.link, ItemRef(case.unit, 1), ItemRef(case.unit, 2))
    related = start.add_relation(inserted, at=0)
    assert related.relations[0] == inserted
    assert_view(
        related.remove_relation(RelationInstanceRef(0)), start, EquivalenceView.EXACT
    )

    binary = start.relations[0]
    changed = start.set_endpoints(
        RelationInstanceRef(0), ItemRef(case.unit, 1), ItemRef(case.unit, 2)
    )
    restored = changed.set_endpoints(RelationInstanceRef(0), binary.left, binary.right)
    assert_view(restored, start, EquivalenceView.EXACT)

    polyadic = start.polyadic_relations[0]
    changed = start.set_endpoints(
        PolyadicInstanceRef(0),
        (ItemRef(case.unit, 1), ItemRef(case.unit, 2)),
        (ItemRef(case.unit, 0),),
    )
    restored = changed.set_endpoints(
        PolyadicInstanceRef(0), polyadic.sources, polyadic.targets
    )
    assert_view(restored, start, EquivalenceView.EXACT)

    promoted_item, item_id = start.promote_item(ItemRef(case.spare, 0), "spare-0")
    assert_view(promoted_item.demote_item(item_id), start, EquivalenceView.EXACT)

    promoted_boundary, boundary_id = start.promote_boundary(
        BoundaryRef(case.unit, 1), "u1"
    )
    assert_view(
        promoted_boundary.demote_boundary(boundary_id), start, EquivalenceView.EXACT
    )

    anonymous_boundary_graph = replace(
        start,
        boundary_values=(
            *start.boundary_values,
            Boundary(
                BoundaryRef(case.spare, 1),
                (AttributeValue(case.boundary_note, XsdType.STRING, "split"),),
            ),
        ),
    )
    promoted_boundary, boundary_id = anonymous_boundary_graph.promote_boundary(
        BoundaryRef(case.spare, 1), "spare-anchor"
    )
    boundary_demoted = promoted_boundary.demote_boundary(boundary_id)
    assert_view(
        boundary_demoted,
        anonymous_boundary_graph,
        EquivalenceView.FUNCTIONAL,
    )
    assert not equivalent(
        boundary_demoted, anonymous_boundary_graph, EquivalenceView.IDENTIFIED
    )
    assert_view(
        boundary_demoted.demote_item(DurableItemRef("spare-anchor")),
        anonymous_boundary_graph,
        EquivalenceView.EXACT,
    )

    promoted_relation, relation_id = start.promote_relation(
        RelationInstanceRef(0), "binary-0"
    )
    assert isinstance(relation_id, DurableRelationRef)
    assert_view(
        promoted_relation.demote_relation(relation_id), start, EquivalenceView.EXACT
    )

    promoted_polyadic, polyadic_id = start.promote_relation(
        PolyadicInstanceRef(0), "polyadic-0"
    )
    assert isinstance(polyadic_id, DurablePolyadicRef)
    assert_view(
        promoted_polyadic.demote_relation(polyadic_id), start, EquivalenceView.EXACT
    )

    editor = start.edit()
    editor.promote_item(ItemRef(case.spare, 0), "editor-item")
    editor.demote_item(DurableItemRef("editor-item"))
    assert_view(editor.freeze(), start, EquivalenceView.EXACT)
    editor = start.edit()
    editor.promote_boundary(BoundaryRef(case.unit, 1), "u1")
    editor.demote_boundary(
        DurableBoundaryRef(DurableItemRef("u1"), BoundarySide.BEFORE)
    )
    assert_view(editor.freeze(), start, EquivalenceView.EXACT)
    editor = start.edit()
    editor.promote_relation(RelationInstanceRef(0), "editor-relation")
    editor.demote_relation(DurableRelationRef("editor-relation"))
    editor.promote_relation(PolyadicInstanceRef(0), "editor-polyadic")
    editor.demote_relation(DurablePolyadicRef("editor-polyadic"))
    assert_view(editor.freeze(), start, EquivalenceView.EXACT)

    sealed = start.edit().seal(case.spare, 1).freeze()
    unsealed = sealed.edit().unseal(case.spare, 0).freeze()
    resealed = unsealed.edit().seal(case.spare, 1).freeze()
    assert_view(resealed, sealed, EquivalenceView.EXACT)
    assert_view(sealed.drop_seal(case.spare), start, EquivalenceView.EXACT)

    layer_name = LayerName(case.namespace, "manual")
    layered = start.add_layer(layer_name)
    assert_view(layered.remove_layer(layer_name), start, EquivalenceView.EXACT)
    live_fact = LayerFact(
        ItemRef(case.unit, 0),
        AttributeValue(case.note, XsdType.STRING, "checked"),
    )
    with_fact = layered.put_fact(layer_name, live_fact)
    replacement_fact = LayerFact(
        live_fact.subject,
        AttributeValue(case.note, XsdType.STRING, "rechecked"),
    )
    replaced_fact = with_fact.put_fact(layer_name, replacement_fact)
    assert_view(
        replaced_fact.put_fact(layer_name, live_fact),
        with_fact,
        EquivalenceView.EXACT,
    )
    without_fact = with_fact.remove_fact(
        layer_name, live_fact.subject, live_fact.value.name
    )
    assert_view(without_fact, layered, EquivalenceView.EXACT)
    orphan_fact = LayerFact(
        OrphanedSubject(case.unit, ItemRef(case.unit, 99)),
        AttributeValue(case.note, XsdType.STRING, "retained"),
    )
    with_orphan = layered.put_fact(layer_name, orphan_fact)
    without_orphan = with_orphan.remove_fact(
        layer_name, orphan_fact.subject, orphan_fact.value.name
    )
    assert_view(without_orphan, layered, EquivalenceView.EXACT)

    removed_items = start.remove_items(case.spare, 1, 2)
    restored = removed_items.insert_items(case.spare, 1, start.tiers[1].items[1:3])
    assert_view(restored, start, EquivalenceView.EXACT)

    old_item = start.tiers[1].items[0]
    replacement = Item(attributes=(AttributeValue(case.note, XsdType.STRING, "new"),))
    replaced = start.replace_item(ItemRef(case.spare, 0), replacement)
    assert_view(
        replaced.replace_item(ItemRef(case.spare, 0), old_item),
        start,
        EquivalenceView.EXACT,
    )


def assert_refuses_without_writing(
    graph: Graph, operation: Callable[[GraphEditor], object], message: str
) -> None:
    """Require one local refusal to preserve graph bytes and displacement."""
    editor = graph.edit()
    before = dump_bytes(editor.freeze())
    displacement = editor.displacement()
    with pytest.raises(GraphValidationError, match=message):
        operation(editor)
    assert dump_bytes(editor.freeze()) == before
    assert editor.displacement() is displacement


def test_item_removal_refuses_every_durable_dependency_before_writing() -> None:
    """Durable endpoints, boundary values, and facts refuse at removal itself."""
    case = fixture("text")
    target = DurableItemRef("u1")
    anchored = DurableBoundaryRef(target, BoundarySide.BEFORE)
    boundary_link = QualifiedName(case.namespace, "boundary-link")
    declarations = (
        *case.graph.relation_declarations,
        BipartiteRelationDeclaration(
            boundary_link,
            next(
                declaration.item_type
                for declaration in case.graph.relation_declarations
                if isinstance(declaration, SimpleRelationDeclaration)
                and declaration.tier == case.unit
            ),
            next(
                declaration.item_type
                for declaration in case.graph.relation_declarations
                if isinstance(declaration, SimpleRelationDeclaration)
                and declaration.tier == case.unit
            ),
            RelationEndpointKind.ITEM,
            RelationEndpointKind.BOUNDARY,
        ),
    )
    durable_endpoint = replace(
        case.graph,
        relations=(RelationInstance(case.link, ItemRef(case.unit, 0), target),),
    )
    assert_refuses_without_writing(
        durable_endpoint, lambda editor: editor.remove_item(target), "still references"
    )
    boundary_endpoint = replace(
        case.graph,
        relation_declarations=declarations,
        relations=(RelationInstance(boundary_link, ItemRef(case.unit, 0), anchored),),
    )
    assert_refuses_without_writing(
        boundary_endpoint, lambda editor: editor.remove_item(target), "still references"
    )
    stored_boundary = replace(
        case.graph,
        relations=(),
        boundary_values=(
            Boundary(
                anchored,
                (AttributeValue(case.boundary_note, XsdType.STRING, "time"),),
            ),
        ),
    )
    assert_refuses_without_writing(
        stored_boundary,
        lambda editor: editor.remove_item(target),
        "stored boundary value",
    )
    layer_name = LayerName(case.namespace, "manual")
    layered = replace(
        case.graph,
        relations=(),
        boundary_values=(),
        layers=(
            Layer(
                layer_name,
                (
                    LayerFact(
                        target,
                        AttributeValue(case.note, XsdType.STRING, "keep"),
                    ),
                ),
            ),
        ),
    )
    assert_refuses_without_writing(
        layered, lambda editor: editor.remove_item(target), "live fact"
    )
    boundary_layered = replace(
        case.graph,
        relations=(),
        boundary_values=(),
        layers=(
            Layer(
                layer_name,
                (
                    LayerFact(
                        anchored,
                        AttributeValue(case.boundary_note, XsdType.STRING, "keep"),
                    ),
                ),
            ),
        ),
    )
    assert_refuses_without_writing(
        boundary_layered, lambda editor: editor.remove_item(target), "live fact"
    )


@pytest.mark.parametrize(
    "kind",
    [
        "binary-coordinate",
        "binary-durable",
        "binary-boundary",
        "polyadic-coordinate",
        "polyadic-durable",
        "polyadic-boundary",
    ],
)
def test_item_removal_refuses_every_relation_endpoint_kind(kind: str) -> None:
    """Binary and polyadic endpoints protect all three reference spellings."""
    case = fixture("music")
    target = DurableItemRef("u1")
    coordinate = ItemRef(case.unit, 1)
    anchored = DurableBoundaryRef(target, BoundarySide.BEFORE)
    endpoint: ItemRef | DurableItemRef | DurableBoundaryRef
    declaration: BipartiteRelationDeclaration | PolyadicRelationDeclaration | None
    unit_type = next(
        declaration.item_type
        for declaration in case.graph.relation_declarations
        if isinstance(declaration, SimpleRelationDeclaration)
        and declaration.tier == case.unit
    )
    if kind.startswith("binary"):
        if kind.endswith("boundary"):
            declaration_name = QualifiedName(case.namespace, "boundary-link")
            declaration = BipartiteRelationDeclaration(
                declaration_name,
                unit_type,
                unit_type,
                RelationEndpointKind.ITEM,
                RelationEndpointKind.BOUNDARY,
            )
            endpoint = anchored
        else:
            declaration_name = case.link
            declaration = None
            endpoint = target if kind.endswith("durable") else coordinate
        graph = replace(
            case.graph,
            relation_declarations=(
                *case.graph.relation_declarations,
                *((declaration,) if declaration is not None else ()),
            ),
            relations=(
                RelationInstance(declaration_name, ItemRef(case.unit, 0), endpoint),
            ),
            polyadic_relations=(),
            boundary_values=(),
        )
    else:
        if kind.endswith("boundary"):
            declaration_name = QualifiedName(case.namespace, "boundary-group")
            source_side = RelationSideDeclaration(
                (RelationEndpointKind.ITEM,), (case.unit,)
            )
            target_side = RelationSideDeclaration(
                (RelationEndpointKind.BOUNDARY,), (case.unit,)
            )
            declaration = PolyadicRelationDeclaration(
                declaration_name, source_side, target_side
            )
            endpoint = anchored
        else:
            declaration_name = case.group
            declaration = None
            endpoint = target if kind.endswith("durable") else coordinate
        graph = replace(
            case.graph,
            relation_declarations=(
                *case.graph.relation_declarations,
                *((declaration,) if declaration is not None else ()),
            ),
            relations=(),
            polyadic_relations=(
                PolyadicRelationInstance(
                    declaration_name,
                    (ItemRef(case.unit, 0),),
                    (endpoint,),
                ),
            ),
            boundary_values=(),
        )
    assert_refuses_without_writing(
        graph,
        lambda editor: editor.remove_item(target),
        "still references",
    )


@pytest.mark.parametrize("polyadic", [False, True])
@pytest.mark.parametrize("durable", [False, True])
def test_unrelate_refuses_live_facts_before_writing(
    polyadic: bool, durable: bool
) -> None:
    """Both relation collections protect coordinate and durable layer subjects."""
    case = fixture("music")
    layer_name = LayerName(case.namespace, "manual")
    target: (
        RelationInstanceRef
        | PolyadicInstanceRef
        | DurableRelationRef
        | DurablePolyadicRef
    )
    if polyadic:
        polyadic_relation = replace(case.graph.polyadic_relations[0], durable_id="p0")
        polyadic_subject = (
            DurablePolyadicRef("p0") if durable else PolyadicInstanceRef(0)
        )
        graph = replace(
            case.graph,
            polyadic_relations=(polyadic_relation,),
            layers=(
                Layer(
                    layer_name,
                    (
                        LayerFact(
                            polyadic_subject,
                            AttributeValue(case.relation_note, XsdType.STRING, "keep"),
                        ),
                    ),
                ),
            ),
        )
        target = polyadic_subject
    else:
        binary_relation = replace(case.graph.relations[0], durable_id="r0")
        binary_subject = DurableRelationRef("r0") if durable else RelationInstanceRef(0)
        graph = replace(
            case.graph,
            relations=(binary_relation,),
            layers=(
                Layer(
                    layer_name,
                    (
                        LayerFact(
                            binary_subject,
                            AttributeValue(case.relation_note, XsdType.STRING, "keep"),
                        ),
                    ),
                ),
            ),
        )
        target = binary_subject
    assert_refuses_without_writing(
        graph, lambda editor: editor.remove_relation(target), "live fact"
    )


def test_positional_relation_insertion_remaps_existing_fact_and_undoes_exactly() -> (
    None
):
    """An index-addressed fact follows its old instance through insertion."""
    case = fixture("text")
    layer_name = LayerName(case.namespace, "manual")
    fact = LayerFact(
        RelationInstanceRef(0),
        AttributeValue(case.relation_note, XsdType.STRING, "kept"),
    )
    start = replace(case.graph, layers=(Layer(layer_name, (fact,)),))
    inserted = RelationInstance(case.link, ItemRef(case.unit, 1), ItemRef(case.unit, 2))
    edited = start.add_relation(inserted, at=0)
    assert edited.layers[0].facts[0].subject == RelationInstanceRef(1)
    assert equivalent(
        edited.remove_relation(RelationInstanceRef(0)),
        start,
        EquivalenceView.EXACT,
    )


def test_zero_length_seal_has_an_exact_drop_inverse() -> None:
    """Dropping a seal removes the record rather than leaving zero as residue."""
    case = fixture("music")
    sealed = case.graph.edit().seal(GraphCarrier.RELATIONS, 0).freeze()
    assert sealed.seals == (Seal(GraphCarrier.RELATIONS, 0),)
    assert equivalent(
        sealed.drop_seal(GraphCarrier.RELATIONS),
        case.graph,
        EquivalenceView.EXACT,
    )


def test_fact_and_demotion_refusals_are_local() -> None:
    """Invalid facts and referenced identities leave the editor untouched."""
    case = fixture("text")
    layer_name = LayerName(case.namespace, "manual")
    layered = case.graph.add_layer(layer_name)
    invalid_orphan = LayerFact(
        OrphanedSubject(case.unit, ItemRef(case.unit, -1)),
        AttributeValue(case.note, XsdType.STRING, "invalid"),
    )
    assert_refuses_without_writing(
        layered,
        lambda editor: editor.put_fact(layer_name, invalid_orphan),
        "no subject stood at index -1",
    )

    durable_endpoint = replace(
        case.graph,
        relations=(
            RelationInstance(case.link, ItemRef(case.unit, 0), DurableItemRef("u1")),
        ),
    )
    assert_refuses_without_writing(
        durable_endpoint,
        lambda editor: editor.demote_item(DurableItemRef("u1")),
        "invalidate relation",
    )

    promoted, durable_relation = case.graph.promote_relation(
        RelationInstanceRef(0), "relation-id"
    )
    with_fact = promoted.add_layer(layer_name).put_fact(
        layer_name,
        LayerFact(
            durable_relation,
            AttributeValue(case.relation_note, XsdType.STRING, "keep"),
        ),
    )
    assert_refuses_without_writing(
        with_fact,
        lambda editor: editor.demote_relation(durable_relation),
        "invalidate a fact",
    )


def test_new_editor_operations_refuse_bad_local_inputs_without_writing() -> None:
    """Each new local precondition is checked before its carrier is changed."""
    case = fixture("music")
    start = case.graph
    assert_refuses_without_writing(
        start,
        lambda editor: editor.promote_item(ItemRef(case.unit, 0), "different"),
        "conflicting durable id",
    )
    assert_refuses_without_writing(
        start,
        lambda editor: editor.promote_item(ItemRef(case.spare, 0), "u0"),
        "duplicate durable id",
    )

    def promote_invalid_edge(index: int) -> Callable[[GraphEditor], object]:
        return lambda editor: editor.promote_boundary(
            BoundaryRef(case.unit, index), cast(Any, 7)
        )

    for edge in (0, len(start.tiers[0].items)):
        assert_refuses_without_writing(
            start,
            promote_invalid_edge(edge),
            "durable id 7 must be a string",
        )
        with pytest.raises(GraphValidationError, match="durable id 7 must be a string"):
            start.promote_boundary(BoundaryRef(case.unit, edge), cast(Any, 7))
    assert_refuses_without_writing(
        start,
        lambda editor: editor.promote_boundary(BoundaryRef(case.unit, 1), "different"),
        "conflicting boundary durable id",
    )
    editor = start.edit()
    before = editor.displacement()
    assert (
        editor.demote_boundary(DurableBoundaryRef(case.unit, BoundarySide.BEFORE))
        is editor
    )
    assert editor.freeze() == start
    assert editor.displacement() is before

    identity_only, identity_ref = start.promote_boundary(
        BoundaryRef(case.spare, 1), "identity-only-anchor"
    )
    assert identity_only.demote_boundary(identity_ref) == identity_only
    assert (
        identity_only.demote_boundary(identity_ref).demote_item(
            DurableItemRef("identity-only-anchor")
        )
        == start
    )
    promoted_relation, relation_id = start.promote_relation(
        RelationInstanceRef(0), "relation-id"
    )
    assert_refuses_without_writing(
        promoted_relation,
        lambda editor: editor.promote_relation(relation_id, "different"),
        "conflicting durable id",
    )

    sealed = start.edit().seal(case.spare, 2).freeze()
    assert_refuses_without_writing(
        sealed, lambda editor: editor.seal(case.spare, 1), "sealing advances"
    )
    assert_refuses_without_writing(
        start, lambda editor: editor.unseal(case.spare, 0), "carries no seal"
    )
    assert_refuses_without_writing(
        sealed,
        lambda editor: editor.unseal(case.spare, 2),
        "requested seal is not lower",
    )
    assert_refuses_without_writing(
        start, lambda editor: editor.drop_seal(case.spare), "carries no seal"
    )
    assert_refuses_without_writing(
        start, lambda editor: editor.seal(case.spare, -1), "must not be negative"
    )
    assert_refuses_without_writing(
        start, lambda editor: editor.seal(case.spare, 4), "more members"
    )
    assert_refuses_without_writing(
        start,
        lambda editor: editor.seal(cast(Any, "not-a-carrier"), 0),
        "unknown seal carrier",
    )

    layer_name = LayerName(case.namespace, "manual")
    layered = start.add_layer(layer_name)
    assert_refuses_without_writing(
        layered, lambda editor: editor.add_layer(layer_name), "duplicate layer"
    )
    fact = LayerFact(
        ItemRef(case.unit, 0),
        AttributeValue(case.note, XsdType.STRING, "keep"),
    )
    with_fact = layered.put_fact(layer_name, fact)
    assert_refuses_without_writing(
        with_fact, lambda editor: editor.remove_layer(layer_name), "still holds"
    )
    assert_refuses_without_writing(
        layered,
        lambda editor: editor.remove_fact(layer_name, fact.subject, fact.value.name),
        "carries no fact",
    )
    missing_layer = LayerName(case.namespace, "missing")
    assert_refuses_without_writing(
        start, lambda editor: editor.remove_layer(missing_layer), "carries no layer"
    )

    assert_refuses_without_writing(
        start,
        lambda editor: editor.remove_items(case.spare, 0, -1),
        "must not be negative",
    )
    assert_refuses_without_writing(
        start,
        lambda editor: editor.remove_items(case.spare, 2, 2),
        "outside tier",
    )
    editor = start.edit()
    before = editor.displacement()
    assert editor.remove_items(case.spare, 3, 0) is editor
    assert editor.displacement() is before
    assert editor.freeze() == start
    assert_refuses_without_writing(
        start,
        lambda editor: editor.replace_item(ItemRef(case.unit, 0), Item("new-id")),
        "must preserve durable id",
    )

    inserted = RelationInstance(case.link, ItemRef(case.unit, 1), ItemRef(case.unit, 2))
    assert_refuses_without_writing(
        start,
        lambda editor: editor.add_relation(inserted, at=-1),
        "outside the graph",
    )
    polyadic_inserted = PolyadicRelationInstance(
        case.group, (ItemRef(case.unit, 1),), (ItemRef(case.unit, 2),)
    )
    assert_refuses_without_writing(
        start,
        lambda editor: editor.add_relation(polyadic_inserted, at=2),
        "polyadic relation instances",
    )
    sealed_relations = start.edit().seal(GraphCarrier.RELATIONS, 1).freeze()
    assert_refuses_without_writing(
        sealed_relations,
        lambda editor: editor.add_relation(inserted, at=0),
        "relation insertion would move",
    )
    assert_refuses_without_writing(
        start,
        lambda editor: editor.set_endpoints(
            RelationInstanceRef(0),
            (ItemRef(case.unit, 0),),
            (ItemRef(case.unit, 1),),
        ),
        "bipartite endpoints",
    )
    assert_refuses_without_writing(
        start,
        lambda editor: editor.set_endpoints(
            PolyadicInstanceRef(0),
            ItemRef(case.unit, 0),
            ItemRef(case.unit, 1),
        ),
        "polyadic endpoint sides",
    )
    assert_refuses_without_writing(
        start,
        lambda editor: editor.set_endpoints(
            PolyadicInstanceRef(0),
            {ItemRef(case.unit, 0)},
            (ItemRef(case.unit, 1),),
        ),
        "ordered iterables",
    )

    def set_unordered_scalar(side: Any) -> Callable[[GraphEditor], object]:
        return lambda editor: editor.set_endpoints(
            PolyadicInstanceRef(0),
            side,
            (ItemRef(case.unit, 1),),
        )

    for unordered_scalar in ("items", b"items"):
        assert_refuses_without_writing(
            start,
            set_unordered_scalar(unordered_scalar),
            "ordered iterables",
        )


def test_editor_promotion_noops_and_positional_polyadic_insertion() -> None:
    """Idempotent promotions stay no-ops and polyadic insertion restores order."""
    case = fixture("text")
    start = case.graph
    editor = start.edit()
    before = editor.displacement()
    editor.promote_item(ItemRef(case.unit, 0), "u0")
    editor.promote_relation(PolyadicInstanceRef(0), "poly")
    editor.promote_relation(DurablePolyadicRef("poly"), "poly")
    assert editor.displacement() is before
    promoted = editor.freeze()
    with pytest.raises(GraphValidationError, match="conflicting durable id"):
        editor.promote_relation(DurablePolyadicRef("poly"), "other")
    assert editor.freeze() == promoted

    editor = start.edit()
    editor.promote_boundary(BoundaryRef(case.unit, 0), "unused")
    editor.promote_boundary(BoundaryRef(case.unit, 3), "unused")
    assert editor.freeze() == start
    editor.promote_boundary(BoundaryRef(case.unit, 2), "u2")
    assert editor.freeze() == start

    anonymous_boundary_graph = replace(
        start,
        boundary_values=(
            *start.boundary_values,
            Boundary(
                BoundaryRef(case.spare, 1),
                (AttributeValue(case.boundary_note, XsdType.STRING, "split"),),
            ),
        ),
    )
    editor = anonymous_boundary_graph.edit()
    editor.promote_boundary(BoundaryRef(case.spare, 1), "anchor")
    promoted_boundary = editor.freeze()
    editor.promote_boundary(BoundaryRef(case.spare, 1), "anchor")
    assert editor.freeze() == promoted_boundary

    inserted = PolyadicRelationInstance(
        case.group, (ItemRef(case.unit, 1),), (ItemRef(case.unit, 0),)
    )
    edited = start.add_relation(inserted, at=0)
    assert edited.polyadic_relations[0] == inserted
    assert equivalent(
        edited.remove_relation(PolyadicInstanceRef(0)),
        start,
        EquivalenceView.EXACT,
    )


def test_put_fact_validates_every_live_subject_domain() -> None:
    """Fact insertion validates values and each live subject before writing."""
    case = fixture("music")
    tier_note = QualifiedName(case.namespace, "tier-note")
    declaration_note = QualifiedName(case.namespace, "declaration-note")
    document_note = QualifiedName(case.namespace, "document-note")
    graph = replace(
        case.graph,
        attribute_declarations=(
            *case.graph.attribute_declarations,
            AttributeDeclaration(tier_note, AttributeDomain.TIER, XsdType.STRING),
            AttributeDeclaration(
                declaration_note,
                AttributeDomain.RELATION_DECLARATION,
                XsdType.STRING,
            ),
            AttributeDeclaration(
                document_note, AttributeDomain.DOCUMENT, XsdType.STRING
            ),
        ),
    )
    layer_name = LayerName(case.namespace, "manual")
    graph = graph.add_layer(layer_name)
    facts = (
        LayerFact(
            DurableItemRef("u0"),
            AttributeValue(case.note, XsdType.STRING, "item"),
        ),
        LayerFact(
            BoundaryRef(case.unit, 0),
            AttributeValue(case.boundary_note, XsdType.STRING, "boundary"),
        ),
        LayerFact(
            DurableBoundaryRef(case.unit, BoundarySide.BEFORE),
            AttributeValue(case.boundary_note, XsdType.STRING, "durable-boundary"),
        ),
        LayerFact(
            TierRef(case.unit), AttributeValue(tier_note, XsdType.STRING, "tier")
        ),
        LayerFact(
            RelationDeclarationRef(case.link),
            AttributeValue(declaration_note, XsdType.STRING, "declaration"),
        ),
        LayerFact(
            PolyadicInstanceRef(0),
            AttributeValue(case.relation_note, XsdType.STRING, "instance"),
        ),
        LayerFact(
            DocumentRef(),
            AttributeValue(document_note, XsdType.STRING, "document"),
        ),
    )
    edited = graph
    for fact in facts:
        edited = edited.put_fact(layer_name, fact)
    for fact in facts:
        edited = edited.remove_fact(layer_name, fact.subject, fact.value.name)
    assert equivalent(edited, graph, EquivalenceView.EXACT)

    foreign_name = QualifiedName("urn:foreign", "note")
    assert_refuses_without_writing(
        graph,
        lambda editor: editor.put_fact(
            layer_name,
            LayerFact(
                ItemRef(case.unit, 0),
                AttributeValue(foreign_name, XsdType.STRING, "wrong"),
            ),
        ),
        "named vocabulary",
    )
    undeclared = QualifiedName(case.namespace, "undeclared")
    assert_refuses_without_writing(
        graph,
        lambda editor: editor.put_fact(
            layer_name,
            LayerFact(
                ItemRef(case.unit, 0),
                AttributeValue(undeclared, XsdType.STRING, "wrong"),
            ),
        ),
        "is undeclared",
    )
    assert_refuses_without_writing(
        graph,
        lambda editor: editor.put_fact(
            layer_name,
            LayerFact(
                ItemRef(case.unit, 0),
                AttributeValue(case.note, XsdType.INTEGER, "1"),
            ),
        ),
        "requires string",
    )
    assert_refuses_without_writing(
        graph,
        lambda editor: editor.put_fact(
            layer_name,
            LayerFact(
                BoundaryRef(case.unit, 0),
                AttributeValue(case.note, XsdType.STRING, "wrong-domain"),
            ),
        ),
        "declared for the item domain",
    )


def test_demotion_refuses_each_kind_of_durable_reference() -> None:
    """Items and boundaries cannot shed ids still named by graph content."""
    case = fixture("text")
    target = DurableItemRef("u1")
    anchor = DurableBoundaryRef(target, BoundarySide.BEFORE)
    polyadic = replace(
        case.graph,
        relations=(),
        boundary_values=(),
        polyadic_relations=(
            PolyadicRelationInstance(case.group, (ItemRef(case.unit, 0),), (target,)),
        ),
    )
    assert_refuses_without_writing(
        polyadic,
        lambda editor: editor.demote_item(target),
        "invalidate polyadic relation",
    )
    stored = replace(
        case.graph,
        relations=(),
        polyadic_relations=(),
        boundary_values=(
            Boundary(
                anchor,
                (AttributeValue(case.boundary_note, XsdType.STRING, "keep"),),
            ),
        ),
    )
    assert_refuses_without_writing(
        stored,
        lambda editor: editor.demote_item(target),
        "stored durable boundary value",
    )
    layer_name = LayerName(case.namespace, "manual")
    item_fact = replace(
        case.graph,
        relations=(),
        boundary_values=(),
        layers=(
            Layer(
                layer_name,
                (
                    LayerFact(
                        target,
                        AttributeValue(case.note, XsdType.STRING, "keep"),
                    ),
                ),
            ),
        ),
    )
    assert_refuses_without_writing(
        item_fact, lambda editor: editor.demote_item(target), "fact in layer"
    )
    boundary_fact = replace(
        case.graph,
        relations=(),
        boundary_values=(),
        layers=(
            Layer(
                layer_name,
                (
                    LayerFact(
                        anchor,
                        AttributeValue(case.boundary_note, XsdType.STRING, "keep"),
                    ),
                ),
            ),
        ),
    )
    assert_refuses_without_writing(
        boundary_fact, lambda editor: editor.demote_item(target), "fact in layer"
    )

    promoted, durable = case.graph.promote_boundary(BoundaryRef(case.unit, 1), "u1")
    unit_type = next(
        declaration.item_type
        for declaration in promoted.relation_declarations
        if isinstance(declaration, SimpleRelationDeclaration)
        and declaration.tier == case.unit
    )
    binary_name = QualifiedName(case.namespace, "boundary-link")
    binary_declaration = BipartiteRelationDeclaration(
        binary_name,
        unit_type,
        unit_type,
        RelationEndpointKind.ITEM,
        RelationEndpointKind.BOUNDARY,
    )
    binary = replace(
        promoted,
        relation_declarations=(
            *promoted.relation_declarations,
            binary_declaration,
        ),
        relations=(RelationInstance(binary_name, ItemRef(case.unit, 0), durable),),
        polyadic_relations=(),
    )
    assert_refuses_without_writing(
        binary,
        lambda editor: editor.demote_boundary(durable),
        "invalidate relation",
    )
    polyadic_name = QualifiedName(case.namespace, "boundary-group")
    source_side = RelationSideDeclaration((RelationEndpointKind.ITEM,), (case.unit,))
    target_side = RelationSideDeclaration(
        (RelationEndpointKind.BOUNDARY,), (case.unit,)
    )
    polyadic_declaration = PolyadicRelationDeclaration(
        polyadic_name, source_side, target_side
    )
    polyadic_boundary = replace(
        promoted,
        relation_declarations=(
            *promoted.relation_declarations,
            polyadic_declaration,
        ),
        relations=(),
        polyadic_relations=(
            PolyadicRelationInstance(
                polyadic_name, (ItemRef(case.unit, 0),), (durable,)
            ),
        ),
    )
    assert_refuses_without_writing(
        polyadic_boundary,
        lambda editor: editor.demote_boundary(durable),
        "invalidate polyadic relation",
    )
    layered_boundary = promoted.add_layer(layer_name).put_fact(
        layer_name,
        LayerFact(
            durable,
            AttributeValue(case.boundary_note, XsdType.STRING, "keep"),
        ),
    )
    assert_refuses_without_writing(
        layered_boundary,
        lambda editor: editor.demote_boundary(durable),
        "fact in layer",
    )


def test_nonmatching_layer_content_survives_removal_and_demotion() -> None:
    """Only live dependencies refuse; orphans and other facts remain content."""
    case = fixture("music")
    layer_name = LayerName(case.namespace, "manual")
    layer = Layer(
        layer_name,
        (
            LayerFact(
                OrphanedSubject(case.unit, ItemRef(case.unit, 99)),
                AttributeValue(case.note, XsdType.STRING, "orphan"),
            ),
            LayerFact(
                ItemRef(case.unit, 2),
                AttributeValue(case.note, XsdType.STRING, "other"),
            ),
        ),
    )
    graph = replace(
        case.graph,
        relations=(),
        polyadic_relations=(),
        boundary_values=(),
        layers=(layer,),
    )
    removed = graph.remove_item(ItemRef(case.unit, 1))
    subjects = {fact.subject for fact in removed.layers[0].facts}
    assert OrphanedSubject(case.unit, ItemRef(case.unit, 99)) in subjects
    assert ItemRef(case.unit, 1) in subjects

    promoted, durable = case.graph.promote_relation(
        RelationInstanceRef(0), "relation-id"
    )
    unrelated = replace(promoted, layers=(layer,))
    assert unrelated.demote_relation(durable).relations[0].durable_id is None

    item_demoted = graph.demote_item(DurableItemRef("u2"))
    assert item_demoted.tiers[0].items[2].durable_id is None

    promoted_boundary, durable_boundary = case.graph.promote_boundary(
        BoundaryRef(case.unit, 1), "u1"
    )
    unrelated_boundary = replace(promoted_boundary, layers=(layer,))
    boundary_demoted = unrelated_boundary.demote_boundary(durable_boundary)
    assert boundary_demoted.boundary_values[0].reference == BoundaryRef(case.unit, 1)


def test_secondary_layer_id_collisions_and_polyadic_seal_path() -> None:
    """Name scans inspect later entries, both relation spaces, and polyadic seals."""
    case = fixture("text")
    first = LayerName(case.namespace, "a")
    second = LayerName(case.namespace, "z")
    two_layers = case.graph.add_layer(first).add_layer(second)
    assert two_layers.remove_layer(second).layers == (Layer(first, ()),)

    binary, binary_id = case.graph.promote_relation(RelationInstanceRef(0), "binary-id")
    assert_refuses_without_writing(
        binary,
        lambda editor: editor.promote_item(
            ItemRef(case.spare, 0), binary_id.durable_id
        ),
        "relation instance 0 already carries it",
    )
    polyadic, polyadic_id = case.graph.promote_relation(
        PolyadicInstanceRef(0), "polyadic-id"
    )
    assert_refuses_without_writing(
        polyadic,
        lambda editor: editor.promote_item(
            ItemRef(case.spare, 0), polyadic_id.durable_id
        ),
        "polyadic relation instance 0 already carries it",
    )

    sealed = case.graph.edit().seal(GraphCarrier.POLYADIC_RELATIONS, 1).freeze()
    assert sealed.seals == (Seal(GraphCarrier.POLYADIC_RELATIONS, 1),)
