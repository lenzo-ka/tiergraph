"""Pins for the two-half weighted fold representation witness."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import cast

import pytest

from tiergraph import Graph, ItemRef
from tiergraph.semiring import (
    DECIMAL_ARCTIC,
    DECIMAL_TROPICAL,
    PATH,
    PathValue,
    Semiring,
)

from .conformance.fold import (
    ComplexityAccount,
    FoldFixture,
    Recognition,
    act,
    canonical_bytes,
    recognize,
)

FIXTURE = FoldFixture()
OUTPUT_CAP = 8


def path_lift(weight: Decimal, durable_id: str) -> PathValue:
    """Embed one valued graph item in the sanctioned path-semiring carrier."""
    return (weight, ((durable_id,),))


def path_provenance(value: PathValue) -> tuple[tuple[str, ...], ...]:
    """Recover the witness component while leaving the semiring value intact."""
    return value[1]


def decimal_lift(weight: Decimal, durable_id: str) -> Decimal:
    """Embed the same valuation in an extremum semiring that carries no witness."""
    del durable_id
    return weight


def no_provenance(value: Decimal) -> None:
    """Declare that an unenriched scalar fold produces no witnesses."""
    del value
    return None


def recognize_path(
    attribute: str = "cost", *, output_cap: int = OUTPUT_CAP
) -> Recognition[PathValue]:
    """Run the reusable recognition half with its declared all-minima tie policy."""
    graph = FIXTURE.graph()
    return recognize(
        graph,
        FIXTURE.states(graph),
        FIXTURE.valuation(attribute),
        PATH,
        path_lift,
        path_provenance,
        output_cap=output_cap,
    )


def test_page_sized_oracle_names_each_expected_product_by_hand() -> None:
    """The two paths cost 0+2+3=5 and 0+1+3=4, so sting is the witness."""
    graph = FIXTURE.graph()
    result = recognize_path()
    assert (
        FIXTURE.tie_policy
        == "all minimum-valued paths in canonical lexicographic order"
    )
    assert result.value == (Decimal(4), (("start", "sting", "out"),))
    assert result.provenance == (("start", "sting", "out"),)
    assert result.truncated is False
    assert FIXTURE.deliveries(graph, result.provenance) == (0, 8, 12)
    value_data = PATH.encode(result.value)
    provenance_data = [list(path) for path in result.provenance]
    delivery_data = list(FIXTURE.deliveries(graph, result.provenance))
    assert json.dumps(value_data) != json.dumps(provenance_data)
    assert json.dumps(provenance_data) != json.dumps(delivery_data)


def test_valuation_and_semiring_vary_independently() -> None:
    """Gain changes the field; arctic changes the question asked of the cost field."""
    gain_result = recognize_path("gain")
    assert gain_result.value == (Decimal(2), (("start", "bed", "out"),))
    graph = FIXTURE.graph()
    maximum = recognize(
        graph,
        FIXTURE.states(graph),
        FIXTURE.valuation("cost"),
        DECIMAL_ARCTIC,
        decimal_lift,
        no_provenance,
        output_cap=OUTPUT_CAP,
    )
    minimum = recognize(
        graph,
        FIXTURE.states(graph),
        FIXTURE.valuation("cost"),
        DECIMAL_TROPICAL,
        decimal_lift,
        no_provenance,
        output_cap=OUTPUT_CAP,
    )
    assert maximum.value == Decimal(5)
    assert minimum.value == Decimal(4)
    assert maximum.provenance is None


def test_reused_recognition_bytes_are_immutable_across_actions() -> None:
    """Recognition is reused byte-for-byte before acting on unrelated carriers."""
    graph = FIXTURE.graph()
    recognized = recognize_path()
    before = canonical_bytes(recognized.to_data(PATH))
    text = act(
        recognized,
        graph,
        FIXTURE,
        "mix",
        lambda carrier, deliveries: f"{carrier}:{deliveries}",
    )
    levels = act(
        recognized,
        graph,
        FIXTURE,
        {0: 1},
        lambda carrier, deliveries: {
            **carrier,
            **dict.fromkeys(deliveries, 1),
        },
    )
    after = canonical_bytes(recognized.to_data(PATH))
    assert text == "mix:(0, 8, 12)"
    assert levels == {0: 1, 8: 1, 12: 1}
    assert before == after


@dataclass
class CountingValuation:
    """Expose how often a fold pass rereads the graph field."""

    attribute: str = "cost"
    reads: int = 0

    def __call__(self, graph: Graph, reference: ItemRef) -> Decimal:
        """Count a read before delegating to the declared valuation."""
        self.reads += 1
        return FIXTURE.valuation(self.attribute)(graph, reference)


@dataclass(frozen=True)
class FusedValue:
    """Interleave a path-semiring value with its action carrier."""

    path: PathValue
    carrier: tuple[int, ...] | str


@dataclass
class FusedAudit:
    """Record the operation ordering inside a carrier-interleaved fold."""

    operations: list[str]


def fused_recognize_and_act(
    question: str, valuation: CountingValuation, audit: FusedAudit
) -> tuple[int, ...] | str:
    """Fold semiring and carrier operations together without a reusable product."""
    graph = FIXTURE.graph()
    references = tuple(reference for reference, _channel in FIXTURE.states(graph))
    admitted = set(references)
    outgoing: dict[ItemRef, list[ItemRef]] = {reference: [] for reference in references}
    for relation in graph.relations:
        if relation.left in admitted and relation.right in admitted:
            outgoing[relation.left].append(relation.right)
    delivery_name = FIXTURE.name("delivery")
    cache: dict[ItemRef, FusedValue] = {}

    def carrier_local(reference: ItemRef) -> tuple[int, ...] | str:
        item = next(
            tier.items[reference.index]
            for tier in graph.tiers
            if tier.declaration.name == reference.tier
        )
        delivery = next(
            value.lexical for value in item.attributes if value.name == delivery_name
        )
        return (int(delivery),) if question == "deliveries" else delivery

    def add(left: FusedValue, right: FusedValue) -> FusedValue:
        audit.operations.append("semiring:add")
        path = PATH.add(left.path, right.path)
        audit.operations.append("carrier:select")
        if path == left.path:
            return FusedValue(path, left.carrier)
        return FusedValue(path, right.carrier)

    def multiply(left: FusedValue, right: FusedValue) -> FusedValue:
        audit.operations.append("semiring:multiply")
        path = PATH.multiply(left.path, right.path)
        audit.operations.append("carrier:combine")
        if isinstance(left.carrier, tuple) and isinstance(right.carrier, tuple):
            carrier: tuple[int, ...] | str = left.carrier + right.carrier
        elif isinstance(left.carrier, str) and isinstance(right.carrier, str):
            carrier = ",".join(part for part in (left.carrier, right.carrier) if part)
        else:
            raise TypeError("fused carrier operations must stay homogeneous")
        return FusedValue(path, carrier)

    def visit(reference: ItemRef) -> FusedValue:
        if reference in cache:
            return cache[reference]
        item = next(
            tier.items[reference.index]
            for tier in graph.tiers
            if tier.declaration.name == reference.tier
        )
        if item.durable_id is None:
            raise ValueError(f"fold item {reference.to_data()!r} has no durable id")
        local = FusedValue(
            path_lift(valuation(graph, reference), item.durable_id),
            carrier_local(reference),
        )
        children = outgoing[reference]
        if children:
            alternatives = FusedValue(PATH.zero, () if question == "deliveries" else "")
            for child in children:
                alternatives = add(alternatives, visit(child))
        else:
            alternatives = FusedValue(PATH.one, () if question == "deliveries" else "")
        result = multiply(local, alternatives)
        cache[reference] = result
        return result

    return visit(references[0]).carrier


def test_rejected_fused_representation_reruns_for_a_second_question() -> None:
    """A carrier-interleaved fold reruns all valuations for a second carrier."""
    valuation = CountingValuation()
    audit = FusedAudit([])
    assert fused_recognize_and_act("deliveries", valuation, audit) == (0, 8, 12)
    first_reads = valuation.reads
    first_operations = tuple(audit.operations)
    assert fused_recognize_and_act("labels", valuation, audit) == "0,8,12"
    assert valuation.reads == first_reads * 2
    assert audit.operations[: len(first_operations)] == list(first_operations)
    assert audit.operations[len(first_operations) :] == list(first_operations)
    assert ("semiring:multiply", "carrier:combine", "semiring:add") in tuple(
        zip(audit.operations, audit.operations[1:], audit.operations[2:], strict=False)
    )


def test_fused_carrier_operations_escape_the_semiring_tie_laws() -> None:
    """Carrier selection discards one path that path-semiring addition preserves."""
    sanctioned = recognize_path("tie")
    audit = FusedAudit([])
    fused = fused_recognize_and_act("deliveries", CountingValuation("tie"), audit)
    assert sanctioned.provenance == (
        ("start", "bed", "out"),
        ("start", "sting", "out"),
    )
    assert fused == (0, 8, 12)


class NoneSemiring:
    """Supply a legitimate carrier whose every value and operation is ``None``."""

    zero = None
    one = None

    def add(self, left: None, right: None) -> None:
        """Return the sole carrier value."""
        del left, right

    def multiply(self, left: None, right: None) -> None:
        """Return the sole carrier value."""
        del left, right


def test_recognition_cache_accepts_none_as_a_concrete_carrier_value() -> None:
    """The shared diamond output is valued once even when its cached value is None."""
    graph = FIXTURE.graph()
    valuation = CountingValuation()
    semiring = cast(Semiring[None], NoneSemiring())
    result = recognize(
        graph,
        FIXTURE.states(graph),
        valuation,
        semiring,
        lambda weight, durable_id: None,
        lambda value: None,
        output_cap=OUTPUT_CAP,
    )
    assert result.value is None
    assert valuation.reads == len(graph.canonical_items()) == 4


def test_output_cap_reports_truncation_under_the_declared_tie_policy() -> None:
    """A cap below the complete tied witness set is observable, never silently short."""
    tied = recognize_path("tie", output_cap=1)
    assert tied.truncated is True
    assert tied.provenance == (("start", "bed", "out"),)


@dataclass
class WeightedPathSemiring:
    """Measure path-semiring operations at a declared carrier-operation cost."""

    operation_cost: int
    work: int = 0
    zero = PATH.zero
    one = PATH.one

    def add(self, left: PathValue, right: PathValue) -> PathValue:
        """Charge for and delegate one addition."""
        self.work += self.operation_cost
        return PATH.add(left, right)

    def multiply(self, left: PathValue, right: PathValue) -> PathValue:
        """Charge for and delegate one multiplication."""
        self.work += self.operation_cost
        return PATH.multiply(left, right)


def measured_complexity(output_cap: int) -> tuple[ComplexityAccount, int]:
    """Run every index product and measure recognition and capped action work."""
    graph = FIXTURE.graph()
    index_product_size = 3
    carrier_operation_cost = 5
    action_cost = 7
    semiring = WeightedPathSemiring(carrier_operation_cost)
    valuation = CountingValuation("tie")
    recognized: Recognition[PathValue] | None = None
    for _index in range(index_product_size):
        recognized = recognize(
            graph,
            FIXTURE.states(graph),
            valuation,
            cast(Semiring[PathValue], semiring),
            path_lift,
            path_provenance,
            output_cap=output_cap,
        )
    assert recognized is not None
    assert valuation.reads == len(graph.canonical_items()) * index_product_size
    action_work = 0
    assert recognized.provenance is not None
    for _witness in recognized.provenance:
        action_work += action_cost
    account = ComplexityAccount(
        document_size=len(graph.canonical_items()),
        relation_incidence=len(graph.relations),
        index_product_size=index_product_size,
        carrier_operation_cost=carrier_operation_cost,
        witness_count=2,
        output_cap=output_cap,
        action_cost=action_cost,
    )
    return account, semiring.work + action_work


def assert_exact_account(account: ComplexityAccount, observed: int) -> None:
    """Require this fixture's measured work to equal its declared account."""
    assert observed == account.bound()


@pytest.mark.parametrize("output_cap", [1, 4])
def test_complexity_bound_uses_every_declared_quantity(output_cap: int) -> None:
    """Measured work distinguishes I, C, and both sides of min(W, K)."""
    account, observed = measured_complexity(output_cap)
    assert_exact_account(account, observed)
    without_index_product = account.bound() - (
        (account.document_size + account.relation_incidence)
        * (account.index_product_size - 1)
        * account.carrier_operation_cost
    )
    without_carrier_cost = account.bound() - (
        (account.document_size + account.relation_incidence)
        * account.index_product_size
        * (account.carrier_operation_cost - 1)
    )
    wrong_uncapped_action = (
        (account.document_size + account.relation_incidence)
        * account.index_product_size
        * account.carrier_operation_cost
        + account.witness_count * account.action_cost
    )
    wrong_cap_only_action = (
        (account.document_size + account.relation_incidence)
        * account.index_product_size
        * account.carrier_operation_cost
        + account.output_cap * account.action_cost
    )
    for defective_bound in (without_index_product, without_carrier_cost):
        with pytest.raises(AssertionError):
            assert observed == defective_bound
    wrong_minimum = (
        wrong_uncapped_action
        if account.witness_count > output_cap
        else wrong_cap_only_action
    )
    with pytest.raises(AssertionError):
        assert observed == wrong_minimum
