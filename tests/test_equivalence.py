"""Public equivalence views, diagnostics, and deterministic fingerprints."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import replace

import pytest

import tiergraph.equivalence as equivalence_module
from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    Boundary,
    BoundaryRef,
    BoundarySide,
    Delivery,
    DocumentRef,
    DurableBoundaryRef,
    DurableItemRef,
    DurablePolyadicRef,
    DurableRelationRef,
    EquivalenceView,
    Graph,
    GraphCarrier,
    Item,
    ItemRef,
    Layer,
    LayerFact,
    LayerName,
    LayerRead,
    LayerSubject,
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
    abstract_form,
    dump_bytes,
    equivalent,
    fingerprint,
    first_difference,
)


def _name(namespace: str, local_name: str) -> QualifiedName:
    """Build one expanded name in a fixture namespace."""
    return QualifiedName(namespace, local_name)


def _value(name: QualifiedName, lexical: str) -> AttributeValue:
    """Build one string value for a fixture declaration."""
    return AttributeValue(name, XsdType.STRING, lexical)


def _domain_graph(
    domain: str,
    *,
    prefix: str | None = None,
    first_item_id: str = "source-0",
    offset: str = "0:7",
) -> Graph:
    """Build a small speech or music graph with offsets and provenance."""
    namespace = f"urn:test:equivalence:{domain}"
    source = _name(namespace, "source")
    unit = _name(namespace, "unit")
    source_type = _name(namespace, "source-type")
    unit_type = _name(namespace, "unit-type")
    source_members = _name(namespace, "source-members")
    unit_members = _name(namespace, "unit-members")
    alignment_a = _name(namespace, "alignment-a")
    alignment_b = _name(namespace, "alignment-b")
    source_offset = _name(namespace, "source-offset")
    provenance = _name(namespace, "provenance")
    item_declaration = AttributeDeclaration(
        source_offset, AttributeDomain.ITEM, XsdType.STRING
    )
    relation_declaration = AttributeDeclaration(
        provenance, AttributeDomain.RELATION_INSTANCE, XsdType.STRING
    )
    declarations = (
        SimpleRelationDeclaration(source_members, source, source_type),
        SimpleRelationDeclaration(unit_members, unit, unit_type),
        BipartiteRelationDeclaration(alignment_a, source_type, unit_type),
        BipartiteRelationDeclaration(alignment_b, source_type, unit_type),
    )
    tiers = (
        Tier(
            TierDeclaration(source, "Source"),
            (
                Item(first_item_id, (_value(source_offset, offset),)),
                Item("source-1"),
            ),
        ),
        Tier(
            TierDeclaration(unit, "Spoken unit" if domain == "speech" else "Note"),
            (Item("unit-0"), Item("unit-1")),
        ),
    )
    relations = (
        RelationInstance(
            alignment_a,
            ItemRef(source, 0),
            ItemRef(unit, 0),
            "alignment-0",
            (_value(provenance, "model-choice-a"),),
        ),
        RelationInstance(
            alignment_b,
            ItemRef(source, 1),
            ItemRef(unit, 1),
            "alignment-1",
            (_value(provenance, "model-choice-b"),),
        ),
    )
    return Graph(
        (NamespaceDeclaration(prefix or domain[0], namespace),),
        tiers,
        declarations,
        relations,
        (item_declaration, relation_declaration),
    )


def _layer_reference_pair() -> tuple[
    Graph,
    Graph,
    ItemRef,
    DurableItemRef,
    QualifiedName,
    Delivery,
]:
    """Build the review counterexample with two disagreeing delivered layers."""
    base = _domain_graph("speech")
    namespace = base.namespaces[0].namespace
    source = _name(namespace, "source")
    source_offset = _name(namespace, "source-offset")
    coordinate = ItemRef(source, 0)
    durable = DurableItemRef("source-0")
    layer_names = (
        LayerName(namespace, "automatic"),
        LayerName(namespace, "manual"),
    )
    values = (_value(source_offset, "live"), _value(source_offset, "reviewed"))

    def layered(subject: ItemRef | DurableItemRef) -> Graph:
        return replace(
            base,
            layers=tuple(
                Layer(layer_name, (LayerFact(subject, value),))
                for layer_name, value in zip(layer_names, values, strict=True)
            ),
        )

    return (
        layered(coordinate),
        layered(durable),
        coordinate,
        durable,
        source_offset,
        Delivery(layer_names, LayerRead.ALL),
    )


@pytest.mark.parametrize("domain", ["speech", "music"])
def test_forms_and_fingerprints_cover_two_domains(domain: str) -> None:
    """The public operations accept fixtures from distinct client domains."""
    graph = _domain_graph(domain)
    form = abstract_form(graph)
    assert form[0] == ("namespace count", "1")
    assert equivalent(graph, graph)
    assert first_difference(graph, graph) is None
    assert len(fingerprint(graph)) == 64


@pytest.mark.parametrize(
    ("view", "expected"),
    (
        (
            EquivalenceView.FUNCTIONAL,
            "8433ed77b57108b942844aa5a57832ffead2c4a5d05badaf828ea16c719e40bf",
        ),
        (
            EquivalenceView.IDENTIFIED,
            "4839f142f89bec5cb999d2d52db595b23e487bf6860c0de874927bd853502b48",
        ),
        (
            EquivalenceView.EXACT,
            "b268b6dd97c2476fa1c7d8f2816635cacb07320abf8f7aa257261834580898fd",
        ),
    ),
)
def test_fingerprint_known_answers(view: EquivalenceView, expected: str) -> None:
    """Each view and version has one pinned digest for a small rich fixture."""
    assert fingerprint(_domain_graph("speech"), view) == expected


def test_layer_reads_resolve_the_fugu_reference_counterexample() -> None:
    """Coordinate and durable spellings read the same live layer facts."""
    structural, durable, coordinate, durable_ref, name, delivery = (
        _layer_reference_pair()
    )
    expected = (_value(name, "live"), _value(name, "reviewed"))
    for graph in (structural, durable):
        assert graph.layer_values(coordinate, name, delivery) == expected
        assert graph.layer_values(durable_ref, name, delivery) == expected
        assert graph.consensus(coordinate, name, delivery).readings == (
            (delivery.layers[0], expected[0]),
            (delivery.layers[1], expected[1]),
        )
        assert graph.consensus(durable_ref, name, delivery).readings == (
            (delivery.layers[0], expected[0]),
            (delivery.layers[1], expected[1]),
        )


def test_supplied_layer_fact_order_is_canonical_under_every_view() -> None:
    """Layer facts are keyed content, so their supplied order is not observable."""
    base = _domain_graph("speech")
    namespace = base.namespaces[0].namespace
    source = _name(namespace, "source")
    offset = _name(namespace, "source-offset")
    facts = (
        LayerFact(ItemRef(source, 1), _value(offset, "second")),
        LayerFact(ItemRef(source, 0), _value(offset, "first")),
    )
    left = replace(base, layers=(Layer(LayerName(namespace, "ordered"), facts),))
    right = replace(
        base, layers=(Layer(LayerName(namespace, "ordered"), tuple(reversed(facts))),)
    )

    for view in EquivalenceView:
        assert equivalent(left, right, view)
    assert left == right
    assert dump_bytes(left) == dump_bytes(right)


def test_functionally_equal_graphs_have_equal_layer_reads() -> None:
    """FUNCTIONAL equality preserves all public reads of equivalent facts."""
    structural, durable, coordinate, durable_ref, name, delivery = (
        _layer_reference_pair()
    )
    assert equivalent(structural, durable, EquivalenceView.FUNCTIONAL)
    assert equivalent(structural, durable, EquivalenceView.IDENTIFIED)
    for subject in (coordinate, durable_ref):
        assert structural.layer_values(subject, name, delivery) == durable.layer_values(
            subject, name, delivery
        )
        assert structural.consensus(subject, name, delivery) == durable.consensus(
            subject, name, delivery
        )
    assert structural.disagreements(delivery) == durable.disagreements(delivery)
    assert structural.disagreements(delivery)[0].subject == coordinate


def test_view_downward_closure_and_each_view_counterexample() -> None:
    """EXACT implies IDENTIFIED implies FUNCTIONAL across discriminating pairs."""
    base = _domain_graph("speech")
    changed_id = _domain_graph("speech", first_item_id="renamed-source")
    changed_prefix = _domain_graph("speech", prefix="voice")
    changed_offset = _domain_graph("speech", offset="0:8")
    graphs = (base, changed_id, changed_prefix, changed_offset)

    assert equivalent(base, changed_id, EquivalenceView.FUNCTIONAL)
    assert not equivalent(base, changed_id, EquivalenceView.IDENTIFIED)
    assert equivalent(base, changed_prefix, EquivalenceView.IDENTIFIED)
    assert not equivalent(base, changed_prefix, EquivalenceView.EXACT)
    assert not equivalent(base, changed_offset, EquivalenceView.FUNCTIONAL)

    for left in graphs:
        for right in graphs:
            exact = equivalent(left, right, EquivalenceView.EXACT)
            identified = equivalent(left, right, EquivalenceView.IDENTIFIED)
            functional = equivalent(left, right, EquivalenceView.FUNCTIONAL)
            assert not exact or identified
            assert not identified or functional


def test_global_relation_interleaving_is_functional_content() -> None:
    """Swapping declarations at global relation indexes changes the graph view."""
    left = _domain_graph("speech")
    right = replace(left, relations=tuple(reversed(left.relations)))
    assert not equivalent(left, right)
    difference = first_difference(left, right)
    assert difference is not None
    assert difference.startswith("relation instance 0:")


def test_orphan_facts_and_every_seal_record_are_functional_content() -> None:
    """Orphans and both zero- and nonzero-length seals remain observable."""
    base = _domain_graph("speech")
    namespace = base.namespaces[0].namespace
    provenance = _name(namespace, "provenance")
    orphan = LayerFact(
        OrphanedSubject(GraphCarrier.RELATIONS, 0),
        _value(provenance, "retained-choice"),
    )
    layered = replace(
        base,
        layers=(Layer(LayerName(namespace, "manual"), (orphan,)),),
    )
    assert not equivalent(base, layered)
    assert (first_difference(base, layered) or "").startswith("layer count:")

    zero_seal = replace(base, seals=(Seal(GraphCarrier.RELATIONS, 0),))
    nonzero_seal = replace(base, seals=(Seal(GraphCarrier.RELATIONS, 1),))
    assert not equivalent(base, zero_seal)
    assert not equivalent(zero_seal, nonzero_seal)
    assert (first_difference(base, zero_seal) or "").startswith("seal count:")
    assert (first_difference(zero_seal, nonzero_seal) or "").startswith("seal 0:")


def test_offsets_and_per_alternative_provenance_are_ordinary_content() -> None:
    """Source offsets and provenance values need no equivalence special case."""
    graph = _domain_graph("speech")
    changed_offset = _domain_graph("speech", offset="7:12")
    changed_relation = replace(
        graph.relations[0],
        attributes=(
            _value(graph.relations[0].attributes[0].name, "different-alternative"),
        ),
    )
    changed_provenance = replace(
        graph, relations=(changed_relation, *graph.relations[1:])
    )
    assert not equivalent(graph, changed_offset)
    assert not equivalent(graph, changed_provenance)
    assert (first_difference(graph, changed_offset) or "").startswith("tier 0 item 0:")
    assert (first_difference(graph, changed_provenance) or "").startswith(
        "relation instance 0:"
    )


def test_durable_reference_spellings_resolve_below_exact() -> None:
    """Coordinates and durable handles compare alike until the exact view."""
    structural = _domain_graph("speech")
    namespace = structural.namespaces[0].namespace
    provenance = _name(namespace, "provenance")
    unit = _name(namespace, "unit")
    with_structural_fact = replace(
        structural,
        layers=(
            Layer(
                LayerName(namespace, "manual"),
                (LayerFact(RelationInstanceRef(0), _value(provenance, "reviewed")),),
            ),
        ),
    )
    durable_relation = replace(
        with_structural_fact.relations[0],
        left=DurableItemRef("source-0"),
        right=DurableItemRef("unit-0"),
    )
    with_durable_fact = replace(
        with_structural_fact,
        relations=(durable_relation, *with_structural_fact.relations[1:]),
        layers=(
            Layer(
                LayerName(namespace, "manual"),
                (
                    LayerFact(
                        DurableRelationRef("alignment-0"),
                        _value(provenance, "reviewed"),
                    ),
                ),
            ),
        ),
    )
    assert equivalent(with_structural_fact, with_durable_fact)
    assert equivalent(
        with_structural_fact, with_durable_fact, EquivalenceView.IDENTIFIED
    )
    assert not equivalent(
        with_structural_fact, with_durable_fact, EquivalenceView.EXACT
    )
    assert "relation instance 0" in (
        first_difference(with_structural_fact, with_durable_fact, EquivalenceView.EXACT)
        or ""
    )

    boundary_name = _name(namespace, "boundary-note")
    boundary_declaration = AttributeDeclaration(
        boundary_name, AttributeDomain.BOUNDARY, XsdType.STRING
    )
    structural_boundary = replace(
        structural,
        attribute_declarations=(
            *structural.attribute_declarations,
            boundary_declaration,
        ),
        boundary_values=(
            Boundary(BoundaryRef(unit, 0), (_value(boundary_name, "edge"),)),
        ),
    )
    durable_boundary = replace(
        structural_boundary,
        boundary_values=(
            Boundary(
                DurableBoundaryRef(DurableItemRef("unit-0"), BoundarySide.BEFORE),
                (_value(boundary_name, "edge"),),
            ),
        ),
    )
    assert equivalent(structural_boundary, durable_boundary)
    assert equivalent(structural_boundary, durable_boundary, EquivalenceView.IDENTIFIED)
    assert not equivalent(structural_boundary, durable_boundary, EquivalenceView.EXACT)
    assert any(
        name == "valued boundary 0"
        for name, _ in abstract_form(durable_boundary, EquivalenceView.EXACT)
    )

    source = _name(namespace, "source")
    source_type = _name(namespace, "source-type")
    unit_type = _name(namespace, "unit-type")
    boundary_link = _name(namespace, "boundary-link")
    boundary_link_declaration = BipartiteRelationDeclaration(
        boundary_link,
        source_type,
        unit_type,
        RelationEndpointKind.BOUNDARY,
        RelationEndpointKind.BOUNDARY,
    )
    boundary_relation = RelationInstance(
        boundary_link,
        DurableBoundaryRef(DurableItemRef("source-0"), BoundarySide.BEFORE),
        DurableBoundaryRef(DurableItemRef("unit-0"), BoundarySide.BEFORE),
    )
    boundary_relation_graph = replace(
        structural,
        relation_declarations=(
            *structural.relation_declarations,
            boundary_link_declaration,
        ),
        relations=(*structural.relations, boundary_relation),
    )
    assert any(
        name == "relation instance 2"
        for name, _ in abstract_form(boundary_relation_graph)
    )
    assert any(
        name == "relation instance 2"
        for name, _ in abstract_form(boundary_relation_graph, EquivalenceView.EXACT)
    )
    assert source != unit


def test_all_layer_subject_variants_and_polyadic_endpoints_are_canonical() -> None:
    """The canonical walk covers every live and retained layer-subject shape."""
    graph = _domain_graph("speech")
    namespace = graph.namespaces[0].namespace
    source = _name(namespace, "source")
    unit = _name(namespace, "unit")
    source_type = _name(namespace, "source-type")
    unit_type = _name(namespace, "unit-type")
    poly_name = _name(namespace, "choice")
    side = RelationSideDeclaration((RelationEndpointKind.ITEM,), (source, unit))
    poly_declaration = PolyadicRelationDeclaration(poly_name, side, side)
    polyadic = PolyadicRelationInstance(
        poly_name,
        (DurableItemRef("source-0"),),
        (DurableItemRef("unit-0"),),
        "choice-0",
    )

    subjects: tuple[tuple[LayerSubject, AttributeDomain], ...] = (
        (ItemRef(source, 0), AttributeDomain.ITEM),
        (DurableItemRef("source-1"), AttributeDomain.ITEM),
        (BoundaryRef(unit, 0), AttributeDomain.BOUNDARY),
        (
            DurableBoundaryRef(DurableItemRef("unit-1"), BoundarySide.BEFORE),
            AttributeDomain.BOUNDARY,
        ),
        (TierRef(source), AttributeDomain.TIER),
        (
            RelationDeclarationRef(graph.relation_declarations[0].name),
            AttributeDomain.RELATION_DECLARATION,
        ),
        (RelationInstanceRef(0), AttributeDomain.RELATION_INSTANCE),
        (DurableRelationRef("alignment-1"), AttributeDomain.RELATION_INSTANCE),
        (PolyadicInstanceRef(0), AttributeDomain.RELATION_INSTANCE),
        (DurablePolyadicRef("choice-0"), AttributeDomain.RELATION_INSTANCE),
        (DocumentRef(), AttributeDomain.DOCUMENT),
        (
            OrphanedSubject(source, ItemRef(source, 9)),
            AttributeDomain.ITEM,
        ),
        (
            OrphanedSubject(unit, BoundaryRef(unit, 9)),
            AttributeDomain.BOUNDARY,
        ),
        (
            OrphanedSubject(GraphCarrier.POLYADIC_RELATIONS, 9),
            AttributeDomain.RELATION_INSTANCE,
        ),
    )
    declarations = []
    facts = []
    for index, (subject, domain) in enumerate(subjects):
        fact_name = _name(namespace, f"fact-{index}")
        declarations.append(AttributeDeclaration(fact_name, domain, XsdType.STRING))
        facts.append(LayerFact(subject, _value(fact_name, str(index))))
    layered = replace(
        graph,
        relation_declarations=(*graph.relation_declarations, poly_declaration),
        attribute_declarations=(*graph.attribute_declarations, *declarations),
        polyadic_relations=(polyadic,),
        layers=(Layer(LayerName(namespace, "all-subjects"), tuple(facts)),),
    )

    functional = abstract_form(layered, EquivalenceView.FUNCTIONAL)
    exact = abstract_form(layered, EquivalenceView.EXACT)
    assert any(name == "polyadic relation instance 0" for name, _ in functional)
    assert sum(
        name.startswith("layer 0 fact ") and name != "layer 0 fact count"
        for name, _ in exact
    ) == len(subjects)
    assert fingerprint(layered, EquivalenceView.IDENTIFIED) == fingerprint(
        layered, "identified"
    )
    assert source_type != unit_type  # fixture declarations type distinct tiers


def test_first_difference_is_symmetric_in_its_named_coordinate() -> None:
    """A stable element name is returned whichever graph is on the left."""
    left = _domain_graph("speech", offset="1:2")
    right = _domain_graph("speech", offset="3:4")
    forward = first_difference(left, right)
    reverse = first_difference(right, left)
    assert forward is not None
    assert reverse is not None
    assert forward.split(":", 1)[0] == reverse.split(":", 1)[0]


@pytest.mark.parametrize("defect", ["name", "length"])
def test_first_difference_refuses_broken_form_alignment(
    monkeypatch: pytest.MonkeyPatch, defect: str
) -> None:
    """Alignment drift raises one explicit typed invariant failure."""
    left = _domain_graph("speech")
    right = _domain_graph("music")

    def broken_elements(
        graph: Graph, _view: EquivalenceView
    ) -> tuple[tuple[str, str], ...]:
        if graph is left:
            return (("shared", "0"),)
        if defect == "name":
            return (("drifted", "0"),)
        return ()

    monkeypatch.setattr(equivalence_module, "_abstract_elements", broken_elements)
    with pytest.raises(RuntimeError, match="alignment invariant failed"):
        first_difference(left, right)


def test_exact_operations_accept_a_valid_lone_surrogate_string() -> None:
    """Graph equality forms do not impose the wire encoder's repertoire."""
    graph = _domain_graph("speech", offset="left\ud800right")
    form = abstract_form(graph, EquivalenceView.EXACT)
    assert any("\\ud800" in value for _, value in form)
    assert equivalent(graph, graph, EquivalenceView.EXACT)
    assert first_difference(graph, graph, EquivalenceView.EXACT) is None
    assert len(fingerprint(graph, EquivalenceView.EXACT)) == 64


def test_equivalence_diagnostics_and_fingerprints_are_mutually_consistent() -> None:
    """All three public answers agree across discriminating graph pairs."""
    structural, durable, *_ = _layer_reference_pair()
    graphs = (
        _domain_graph("speech"),
        _domain_graph("speech", first_item_id="renamed-source"),
        _domain_graph("speech", prefix="voice"),
        _domain_graph("speech", offset="0:8"),
        structural,
        durable,
    )
    for view in EquivalenceView:
        for left in graphs:
            for right in graphs:
                same = equivalent(left, right, view)
                assert (first_difference(left, right, view) is None) is same
                assert (fingerprint(left, view) == fingerprint(right, view)) is same


def test_unknown_view_is_refused() -> None:
    """A misspelled public view does not silently select another identity."""
    with pytest.raises(ValueError, match="not a valid EquivalenceView"):
        fingerprint(_domain_graph("speech"), "semantic")


def test_fingerprint_is_independent_of_python_hash_seed() -> None:
    """Separate interpreters with different hash seeds produce the same digest."""
    program = """
from tiergraph import Graph, Item, NamespaceDeclaration, QualifiedName, Tier
from tiergraph import TierDeclaration, fingerprint
namespace = 'urn:test:hash-seed'
tier = QualifiedName(namespace, 'notes')
graph = Graph(
    (NamespaceDeclaration('m', namespace),),
    (Tier(TierDeclaration(tier, 'Notes'), (Item('n0'), Item('n1'))),),
    (),
)
print(fingerprint(graph, 'identified'))
"""
    outputs = []
    for seed in ("0", "12345", "999"):
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = seed
        completed = subprocess.run(
            [sys.executable, "-c", program],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        outputs.append(completed.stdout.strip())
    assert len(set(outputs)) == 1
