"""The reference recognition machinery satisfies the reusable fold laws."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import cast

import pytest

from tests.conformance.fold import FoldFixture
from tests.conformance.recognition import FoldLawSuite
from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    Graph,
    Item,
    ItemRef,
    QualifiedName,
    RelationInstance,
    Tier,
    TierDeclaration,
    XsdType,
)
from tiergraph.fold import (
    AttributeValuation,
    ChildCombination,
    FoldDeclaration,
    FoldHomomorphism,
    FoldTransition,
    Lift,
    TiePolicy,
)
from tiergraph.semiring import (
    DECIMAL_ARCTIC,
    DECIMAL_TROPICAL,
    PATH,
    PathValue,
    Semiring,
)

FIXTURE = FoldFixture()
CAP = 7


def path_lift(value: object, label: str) -> PathValue:
    """Embed a decimal field and stable label in the path carrier."""
    return (cast(Decimal, value), ((label,),))


def path_provenance(value: PathValue) -> tuple[tuple[str, ...], ...]:
    """Read the path component without treating it as the semiring value."""
    return value[1]


def decimal_lift(value: object, label: str) -> Decimal:
    """Embed the same field in an unenriched decimal carrier."""
    del label
    return cast(Decimal, value)


def minimum_order(left: object, right: object) -> int:
    """Compare plain or path carriers by their tropical scalar."""
    left_value = cast(Decimal, left[0] if isinstance(left, tuple) else left)
    right_value = cast(Decimal, right[0] if isinstance(right, tuple) else right)
    return (left_value > right_value) - (left_value < right_value)


def valuation(attribute: str) -> AttributeValuation:
    """Declare one item field over the fixture's placement tier."""
    return AttributeValuation(
        attribute, FIXTURE.name(attribute), (FIXTURE.name("placement"),)
    )


def declaration(
    attribute: str = "cost",
    semiring: Semiring[object] | None = None,
) -> FoldDeclaration[object]:
    """Build the general declaration used by the conformance boundary."""
    semiring = cast(Semiring[object], PATH) if semiring is None else semiring
    graph = FIXTURE.graph()
    witness = semiring is cast(object, PATH)
    return FoldDeclaration(
        "mix-path",
        graph,
        valuation(attribute),
        semiring,
        cast(Lift[object], path_lift if witness else decimal_lift),
        (FoldTransition(FIXTURE.name("depends"), ChildCombination.OR),),
        (("main", "preview", "archive"),),
        (FIXTURE.states(graph)[0][0],),
        minimum_order if witness else None,
        TiePolicy.ALL if witness else None,
        CAP,
        5,
    )


def mismatch() -> object:
    """Construct the near-valid double-field and exact-semiring declaration."""
    graph = FIXTURE.graph()
    tie = FIXTURE.name("tie")
    tiers = tuple(
        Tier(
            tier.declaration,
            tuple(
                Item(
                    item.durable_id,
                    tuple(
                        AttributeValue(value.name, XsdType.DOUBLE, value.lexical)
                        if value.name == tie
                        else value
                        for value in item.attributes
                    ),
                )
                for item in tier.items
            ),
            tier.attributes,
        )
        for tier in graph.tiers
    )
    declarations = tuple(
        AttributeDeclaration(candidate.name, candidate.domain, XsdType.DOUBLE)
        if candidate.name == tie
        else candidate
        for candidate in graph.attribute_declarations
    )
    double_graph = Graph(
        graph.namespaces,
        tiers,
        graph.relation_declarations,
        graph.relations,
        declarations,
    )
    return replace(declaration("tie"), graph=double_graph)


def homomorphism() -> FoldHomomorphism[object, object]:
    """Map enriched minimum paths to their minimum decimal value."""
    source = declaration()
    target = declaration("cost", cast(Semiring[object], DECIMAL_TROPICAL))
    return FoldHomomorphism(
        "forget-witness", source, target, lambda value: cast(PathValue, value)[0]
    )


LAWS = FoldLawSuite(
    declaration,
    lambda: declaration("gain"),
    lambda: declaration("cost", cast(Semiring[object], DECIMAL_ARCTIC)),
    mismatch,
    homomorphism,
)


@pytest.mark.parametrize(
    "law",
    [
        LAWS.check_oracle,
        LAWS.check_independent_variation,
        LAWS.check_type_exactness_refusal,
        LAWS.check_homomorphism,
        LAWS.check_measured_cost,
    ],
    ids=lambda law: law.__name__,
)
def test_fold_law(law: object) -> None:
    """Run each reusable law against the reference implementation."""
    assert callable(law)
    law()


def test_output_cap_is_measured_and_observable() -> None:
    """The complete tied set is counted before the declared cap is applied."""
    tied = replace(declaration("tie"), output_cap=1).run()
    assert tied.truncated is True
    assert tied.cost.witness_count == 2
    assert tied.cost.emitted_count == 1
    assert tied.cost.output_cap == 1


def test_witnesses_require_a_tie_policy() -> None:
    """Removing only the policy makes the witness-producing declaration invalid."""
    with pytest.raises(ValueError, match="mix-path.*tie policy"):
        replace(declaration(), tie_policy=None)


def test_relation_incidence_declares_and_or_meaning() -> None:
    """Required children multiply while alternative children add."""

    class CountingSemiring:
        zero = Decimal(0)
        one = Decimal(1)
        add_associativity = multiply_associativity = DECIMAL_TROPICAL.add_associativity

        def add(self, left: Decimal, right: Decimal) -> Decimal:
            return left + right

        def multiply(self, left: Decimal, right: Decimal) -> Decimal:
            return left * right

    base = replace(
        declaration("tie", cast(Semiring[object], CountingSemiring())),
        lift=lambda value, label: Decimal(1),
        index_axes=(),
        roots=(FIXTURE.states(FIXTURE.graph())[0][0],),
    )
    disjunction = replace(
        base,
        transitions=(FoldTransition(FIXTURE.name("depends"), ChildCombination.OR),),
    )
    conjunction = replace(
        base,
        transitions=(FoldTransition(FIXTURE.name("depends"), ChildCombination.AND),),
    )
    assert disjunction.run().value == Decimal(2)
    assert conjunction.run().value == Decimal(1)


def test_plain_tropical_carrier_accumulates_provenance_and_applies_ties() -> None:
    """Witnesses are a parallel fold product, not a component of the carrier."""
    plain = replace(
        declaration("tie", cast(Semiring[object], DECIMAL_TROPICAL)),
        witness_order=minimum_order,
        tie_policy=TiePolicy.ALL,
    )
    all_ties = plain.run()
    first = replace(plain, tie_policy=TiePolicy.CHOOSE_FIRST).run()
    assert all_ties.value == Decimal(0)
    assert all_ties.provenance == (
        ("start", "bed", "out"),
        ("start", "sting", "out"),
    )
    assert first.provenance == (("start", "bed", "out"),)
    with pytest.raises(ValueError, match="unsupported tie policy"):
        replace(plain, tie_policy="anything")  # type: ignore[arg-type]


def test_cost_bound_uses_each_distinct_measured_quantity() -> None:
    """Deleting any of D, R, I, C, or capped output invalidates the exact account."""
    base = declaration("tie")
    extra_name = FIXTURE.name("excluded")
    extra = Tier(TierDeclaration(extra_name, "Excluded"), (Item("extra"),))
    graph = replace(base.graph, tiers=(*base.graph.tiers, extra))
    result = replace(
        base,
        graph=graph,
        carrier_operation_cost=7,
        output_cap=2,
    ).run()
    cost = result.cost
    assert (
        cost.document_size,
        cost.relation_incidence,
        cost.index_product_size,
        cost.carrier_operation_cost,
        cost.output_cap,
    ) == (5, 4, 3, 7, 2)
    assert cost.measured_work == cost.bound
    terms = (
        cost.document_size * cost.index_product_size * cost.carrier_operation_cost,
        cost.relation_incidence * cost.index_product_size * cost.carrier_operation_cost,
        (cost.document_size + cost.relation_incidence)
        * (cost.index_product_size - 1)
        * cost.carrier_operation_cost,
        (cost.document_size + cost.relation_incidence)
        * cost.index_product_size
        * (cost.carrier_operation_cost - 1),
        min(cost.witness_count, cost.output_cap),
    )
    for deleted_term in terms:
        with pytest.raises(AssertionError):
            assert cost.measured_work == cost.bound - deleted_term


class ForbiddenInspectionSemiring:
    """Use opaque values that can only be combined through declared operations."""

    zero = object()
    one = object()
    add_associativity = multiply_associativity = DECIMAL_TROPICAL.add_associativity

    def add(self, left: object, right: object) -> object:
        """Return a fresh opaque alternative."""
        del left, right
        return object()

    def multiply(self, left: object, right: object) -> object:
        """Return a fresh opaque product."""
        del left, right
        return object()


def test_recognition_cannot_reach_outside_add_and_multiply() -> None:
    """Opaque carriers execute because recognition never compares or inspects them."""
    opaque = cast(Semiring[object], ForbiddenInspectionSemiring())
    result = declaration("cost", opaque).run()
    assert result.cost.carrier_additions > 0
    assert result.cost.carrier_multiplications > 0


def test_tropical_hides_a_fold_that_selects_outside_semiring_addition() -> None:
    """Python selection matches tropical but discards a tied path alternative."""
    tropical = declaration("tie", cast(Semiring[object], DECIMAL_TROPICAL))
    sanctioned_tropical = tropical.run().value
    graph = tropical.graph
    references = tuple(state[0] for state in FIXTURE.states(graph))
    outgoing: dict[ItemRef, list[ItemRef]] = {reference: [] for reference in references}
    for edge in graph.relations:
        if (
            isinstance(edge.left, ItemRef)
            and isinstance(edge.right, ItemRef)
            and edge.left in outgoing
            and edge.right in outgoing
        ):
            outgoing[edge.left].append(edge.right)

    def defective(item_reference: ItemRef, enriched: bool) -> object:
        item = next(
            tier.items[item_reference.index]
            for tier in graph.tiers
            if tier.declaration.name == item_reference.tier
        )
        local = Decimal(0)
        children = outgoing[item_reference]
        if not children:
            return (local, ((item.durable_id,),)) if enriched else local
        if enriched:
            selected = min(
                cast(PathValue, defective(child, True)) for child in children
            )
            weight, paths = selected
            return (local + weight, tuple((item.durable_id, *path) for path in paths))
        selected_decimal = min(
            cast(Decimal, defective(child, False)) for child in children
        )
        return local + selected_decimal

    assert defective(references[0], False) == sanctioned_tropical
    sanctioned_paths = declaration("tie").run().value
    assert defective(references[0], True) != sanctioned_paths


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"name": ""}, "fold name"),
        ({"output_cap": 0}, "output cap"),
        ({"carrier_operation_cost": 0}, "operation cost"),
        ({"witness_order": None}, "produces no witnesses"),
        ({"index_axes": ((),)}, "axis 0.*empty"),
        ({"index_axes": (("x", "x"),)}, "axis 0.*duplicates"),
        ({"transitions": ()}, "no declared dependency"),
        ({"transitions": cast(object, (FIXTURE.name("depends"),))}, "AND/OR"),
        (
            {
                "transitions": (
                    FoldTransition(FIXTURE.name("depends"), ChildCombination.OR),
                    FoldTransition(FIXTURE.name("depends"), ChildCombination.AND),
                )
            },
            "duplicate dependency",
        ),
        (
            {
                "transitions": (
                    FoldTransition(
                        QualifiedName(FIXTURE.namespace, "missing"), ChildCombination.OR
                    ),
                )
            },
            "undeclared",
        ),
        ({"roots": (ItemRef(FIXTURE.name("missing"), 0),)}, "outside its domain"),
    ],
)
def test_declaration_refusals_name_the_offender(
    change: dict[str, object], message: str
) -> None:
    """Each near-valid declaration is refused before execution."""
    base = declaration()
    if change == {"witness_order": None}:
        change["tie_policy"] = TiePolicy.ALL
    with pytest.raises(ValueError, match=message):
        replace(base, **change)  # type: ignore[arg-type]


def test_valuation_declaration_refusals() -> None:
    """Valuation identity, finite domain, declaration, and item domain are checked."""
    placement = FIXTURE.name("placement")
    with pytest.raises(ValueError, match="valuation name"):
        AttributeValuation("", FIXTURE.name("cost"), (placement,))
    with pytest.raises(ValueError, match="empty tier"):
        AttributeValuation("cost", FIXTURE.name("cost"), ())
    with pytest.raises(ValueError, match="duplicate tier"):
        AttributeValuation("cost", FIXTURE.name("cost"), (placement, placement))
    missing = AttributeValuation("missing", FIXTURE.name("missing"), (placement,))
    with pytest.raises(ValueError, match="undeclared attribute"):
        missing.declaration_type(FIXTURE.graph())
    graph = FIXTURE.graph()
    document_declaration = AttributeDeclaration(
        FIXTURE.name("document-only"), AttributeDomain.DOCUMENT, XsdType.STRING
    )
    document_graph = Graph(
        graph.namespaces,
        graph.tiers,
        graph.relation_declarations,
        graph.relations,
        (*graph.attribute_declarations, document_declaration),
        attributes=(AttributeValue(document_declaration.name, XsdType.STRING, "v"),),
    )
    wrong_domain = AttributeValuation(
        "document", document_declaration.name, (placement,)
    )
    with pytest.raises(ValueError, match="not 'item'"):
        wrong_domain.declaration_type(document_graph)


def test_valuation_reads_every_admitted_xsd_value_kind() -> None:
    """Typed lexical values become the corresponding Python input to a lift."""
    graph = FIXTURE.graph()
    reference = graph.canonical_items()[0]
    with pytest.raises(ValueError, match="excludes tier"):
        AttributeValuation(
            "cost", FIXTURE.name("cost"), (FIXTURE.name("missing"),)
        ).read(graph, reference)
    with pytest.raises(ValueError, match="lacks attribute"):
        valuation("missing").read(graph, reference)

    for value_type, lexical, expected in (
        (XsdType.DOUBLE, "2.5", 2.5),
        (XsdType.BOOLEAN, "true", True),
        (XsdType.STRING, "text", "text"),
    ):
        field = FIXTURE.name(f"field-{value_type.value}")
        item = Item("only", (AttributeValue(field, value_type, lexical),))
        tier = Tier(graph.tiers[0].declaration, (item,))
        typed_graph = Graph(
            graph.namespaces,
            (tier,),
            (graph.relation_declarations[0],),
            attribute_declarations=(
                AttributeDeclaration(field, AttributeDomain.ITEM, value_type),
            ),
        )
        reader = AttributeValuation(
            value_type.value, field, (placement := tier.declaration.name,)
        )
        assert reader.read(typed_graph, ItemRef(placement, 0)) == expected


def test_domain_and_relation_declarations_are_refused() -> None:
    """Undeclared tiers and dependency relations without acyclicity cannot enter a fold."""
    with pytest.raises(ValueError, match="undeclared tier"):
        replace(
            declaration(),
            valuation=AttributeValuation(
                "cost", FIXTURE.name("cost"), (FIXTURE.name("missing"),)
            ),
        )
    graph = FIXTURE.graph()
    depends = next(
        relation
        for relation in graph.relation_declarations
        if relation.name == FIXTURE.name("depends")
    )
    lax_graph = Graph(
        graph.namespaces,
        graph.tiers,
        tuple(
            replace(cast(BipartiteRelationDeclaration, depends), acyclic=False)
            if relation is depends
            else relation
            for relation in graph.relation_declarations
        ),
        graph.relations,
        graph.attribute_declarations,
    )
    with pytest.raises(ValueError, match="does not declare acyclic"):
        replace(declaration(), graph=lax_graph)


def test_scalar_result_and_empty_index_product_are_serializable() -> None:
    """A fold without witnesses or index axes exposes both absences directly."""
    scalar = replace(
        declaration("cost", cast(Semiring[object], DECIMAL_TROPICAL)), index_axes=()
    )
    assert scalar.coordinates() == ((),)
    data = scalar.run().to_data(scalar.semiring)
    assert data["provenance"] is None


def test_homomorphism_refusals_name_the_declaration() -> None:
    """A commuting claim needs a name, one structure, and a commuting map."""
    valid = homomorphism()
    with pytest.raises(ValueError, match="homomorphism name"):
        replace(valid, name="")
    with pytest.raises(ValueError, match="structures differ"):
        replace(valid, target=replace(valid.target, index_axes=(("other",),)))
    with pytest.raises(ValueError, match="does not commute"):
        replace(valid, mapping=lambda value: Decimal(999))


def test_empty_domain_has_no_dependency_root() -> None:
    """An explicitly named but empty tier cannot supply an inferred root."""
    graph = FIXTURE.graph()
    empty_graph = Graph(
        graph.namespaces,
        (Tier(graph.tiers[0].declaration),),
        graph.relation_declarations,
        attribute_declarations=graph.attribute_declarations,
    )
    with pytest.raises(ValueError, match="no root"):
        replace(declaration(), graph=empty_graph, roots=())


def test_inferred_multiple_roots_have_separately_accumulated_provenance() -> None:
    """Root alternatives use the same declared tie behavior as OR incidence."""
    base = declaration("tie")
    graph = replace(base.graph, relations=())
    result = replace(base, graph=graph, roots=(), index_axes=()).run()
    assert result.provenance == (("start",), ("bed",), ("sting",), ("out",))


def test_isolated_anonymous_item_uses_a_deterministic_structural_label() -> None:
    """A domain item without durable identity remains foldable and reproducible."""
    graph = FIXTURE.graph()
    tier = graph.tiers[0]
    anonymous = Tier(
        tier.declaration,
        (Item(attributes=tier.items[0].attributes),),
        tier.attributes,
    )
    isolated_graph = Graph(
        graph.namespaces,
        (anonymous,),
        graph.relation_declarations,
        attribute_declarations=graph.attribute_declarations,
    )
    declared = replace(declaration(), graph=isolated_graph, roots=())
    assert declared.run().provenance == ((f"{FIXTURE.namespace}:placement:0",),)


def test_undeclared_incidence_does_not_enter_the_dependency_dag() -> None:
    """Only relation names listed by the fold contribute transitions or cost."""
    graph = FIXTURE.graph()
    depends = cast(
        BipartiteRelationDeclaration,
        next(
            relation
            for relation in graph.relation_declarations
            if relation.name == FIXTURE.name("depends")
        ),
    )
    ignored = replace(depends, name=FIXTURE.name("ignored"))
    edge = graph.relations[0]
    ignored_edge = RelationInstance(ignored.name, edge.left, edge.right)
    expanded = Graph(
        graph.namespaces,
        graph.tiers,
        (*graph.relation_declarations, ignored),
        (*graph.relations, ignored_edge),
        graph.attribute_declarations,
    )
    result = replace(declaration(), graph=expanded).run()
    assert result.cost.relation_incidence == len(graph.relations)


def test_selection_compares_alternatives_not_the_running_total() -> None:
    """Every tied alternative survives under a semiring whose addition accumulates.

    Comparing each candidate against an accumulated carrier value is harmless
    only when addition is selective. Under counting the total outgrows every
    later alternative, so equals stop being recognized as ties and their
    witnesses are dropped while the value stays correct.
    """

    class CountingSemiring:
        zero = Decimal(0)
        one = Decimal(1)
        add_associativity = multiply_associativity = DECIMAL_TROPICAL.add_associativity

        def add(self, left: Decimal, right: Decimal) -> Decimal:
            return left + right

        def multiply(self, left: Decimal, right: Decimal) -> Decimal:
            return left * right

    graph = FIXTURE.graph()
    roots = tuple(state[0] for state in FIXTURE.states(graph))
    counting = replace(
        declaration("tie", cast(Semiring[object], CountingSemiring())),
        lift=lambda value, label: Decimal(1),
        index_axes=(),
        roots=roots,
        witness_order=minimum_order,
        tie_policy=TiePolicy.ALL,
    )
    everything = counting.run()
    first_only = replace(counting, tie_policy=TiePolicy.CHOOSE_FIRST).run()
    assert everything.provenance is not None
    assert first_only.provenance is not None
    # Selecting against the running total keeps one witness here, because the
    # accumulated count passes every later equal and no tie is ever seen.
    assert len(everything.provenance) > 1
    assert len(first_only.provenance) == 1
    assert everything.value == first_only.value


def test_ranked_candidate_deduplication_is_costed() -> None:
    """Ranked selection counts comparisons used to remove duplicate witnesses."""
    declared = replace(
        declaration(), ranked_output=True, output_cap=1, witness_order=None
    )
    operations = [0]
    additions = [0]
    candidate = (PATH.one, ("same",))
    ranked = declared._rank_candidates(
        (candidate, candidate, (PATH.one, ("other",))), operations, additions
    )
    assert ranked == ((PATH.one, ("other",)),)
    assert operations[0] > 0
