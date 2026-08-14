"""Witness packed mix alternation, reconvergence, and semiring sensitivity."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Protocol, TypeVar

import pytest

from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    Graph,
    Item,
    ItemRef,
    NamespaceDeclaration,
    QualifiedName,
    RelationInstance,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    XsdType,
)
from tiergraph.semiring import BOOLEAN, COUNTING, DECIMAL_TROPICAL

T = TypeVar("T")

NS = "urn:tiergraph:witness:mix-alternation"
NAMESPACE = (NamespaceDeclaration("mix", NS),)
STATE_TIER = QualifiedName(NS, "arrangement-state")
STATE_TYPE = QualifiedName(NS, "arrangement-state-type")
MEMBERSHIP = QualifiedName(NS, "arrangement-states")
STEM_TIER = QualifiedName(NS, "stem")
STEM_TYPE = QualifiedName(NS, "stem-type")
STEM_MEMBERSHIP = QualifiedName(NS, "stems")
ALTERNATION = QualifiedName(NS, "alternation")
LABEL = QualifiedName(NS, "label")
PLACEMENTS = QualifiedName(NS, "placements")
WEIGHT = QualifiedName(NS, "arc-weight")


class WitnessSemiring(Protocol[T]):
    """Expose only the two operations needed by the witness recurrence."""

    @property
    def zero(self) -> T:
        """Return the additive identity."""

    @property
    def one(self) -> T:
        """Return the multiplicative identity."""

    def add(self, left: T, right: T, /) -> T:
        """Combine alternatives."""

    def multiply(self, left: T, right: T, /) -> T:
        """Extend a path."""


@dataclass(frozen=True)
class ProbabilitySemiring:
    """Use exact nonnegative rationals to expose duplicated probability mass."""

    zero: Fraction = Fraction(0)
    one: Fraction = Fraction(1)

    def add(self, left: Fraction, right: Fraction, /) -> Fraction:
        """Add the mass of alternatives."""
        return left + right

    def multiply(self, left: Fraction, right: Fraction, /) -> Fraction:
        """Multiply the mass of successive choices."""
        return left * right


@dataclass(frozen=True)
class FoldProfile:
    """Record the structural work performed while preparing and folding."""

    document_visits: int
    endpoint_visits: int
    index_slots: int
    value: int

    @property
    def operations(self) -> int:
        """Return the measured recognition work."""
        return self.document_visits + self.endpoint_visits + self.index_slots


@dataclass(frozen=True)
class PackedAlternationSuite:
    """Apply alternation laws through the public graph and semiring boundaries."""

    graph: Graph

    def arcs(self) -> tuple[RelationInstance, ...]:
        """Return the declared alternation arcs in document order."""
        return tuple(
            relation
            for relation in self.graph.relations
            if relation.declaration == ALTERNATION
        )

    def successors(self) -> dict[int, tuple[RelationInstance, ...]]:
        """Index outgoing arcs without changing their declared order."""
        result: dict[int, list[RelationInstance]] = {}
        for arc in self.arcs():
            result.setdefault(arc.left.index, []).append(arc)
        return {node: tuple(arcs) for node, arcs in result.items()}

    def enumerate_paths(self, output_cap: int) -> tuple[tuple[str, ...], ...]:
        """Enumerate complete arc identities or refuse before truncating output."""
        successors = self.successors()
        complete: list[tuple[str, ...]] = []

        def visit(node: int, path: tuple[str, ...]) -> None:
            outgoing = successors.get(node, ())
            if not outgoing:
                if len(complete) == output_cap:
                    raise ValueError(
                        f"alternation output cap {output_cap} excludes path {path!r}"
                    )
                complete.append(path)
                return
            for arc in outgoing:
                assert arc.durable_id is not None
                visit(arc.right.index, (*path, arc.durable_id))

        visit(0, ())
        return tuple(complete)

    def fold(
        self,
        semiring: WitnessSemiring[T],
        arc_values: Mapping[str, T],
    ) -> T:
        """Fold the acyclic lattice once per state, sharing its suffix value."""
        successors = self.successors()
        memo: dict[int, T] = {}

        def value(node: int) -> T:
            if node in memo:
                return memo[node]
            outgoing = successors.get(node, ())
            result = semiring.one if not outgoing else semiring.zero
            for arc in outgoing:
                assert arc.durable_id is not None
                result = semiring.add(
                    result,
                    semiring.multiply(
                        arc_values[arc.durable_id], value(arc.right.index)
                    ),
                )
            memo[node] = result
            return result

        return value(0)

    def score_path(
        self,
        path: tuple[str, ...],
        semiring: WitnessSemiring[T],
        arc_values: Mapping[str, T],
    ) -> T:
        """Multiply the implementation values for every durable arc in a path."""
        available = {
            arc.durable_id: arc for arc in self.arcs() if arc.durable_id is not None
        }
        score = semiring.one
        node = 0
        for identity in path:
            arc = available[identity]
            if arc.left.index != node:
                raise ValueError(f"arc {identity!r} does not continue path at {node}")
            score = semiring.multiply(score, arc_values[identity])
            node = arc.right.index
        if self.successors().get(node):
            raise ValueError(f"path stops before terminal state {node}")
        return score

    def defective_fold(
        self,
        semiring: WitnessSemiring[T],
        arc_values: Mapping[str, T],
    ) -> T:
        """Propagate a joined aggregate once for every arrival at that state."""
        successors = self.successors()
        arrivals: dict[int, list[T]] = defaultdict(list)
        arrivals[0].append(semiring.one)
        pending = deque((0,))
        queued = {0}
        result = semiring.zero
        while pending:
            node = pending.popleft()
            queued.remove(node)
            aggregate = semiring.zero
            for arrival in arrivals[node]:
                aggregate = semiring.add(aggregate, arrival)
            outgoing = successors.get(node, ())
            if not outgoing:
                for arrival in arrivals[node]:
                    result = semiring.add(result, arrival)
                continue
            for _arrival in arrivals[node]:
                for arc in outgoing:
                    assert arc.durable_id is not None
                    target = arc.right.index
                    arrivals[target].append(
                        semiring.multiply(aggregate, arc_values[arc.durable_id])
                    )
                    if target not in queued:
                        pending.append(target)
                        queued.add(target)
        return result

    def profiled_counting_fold(self) -> FoldProfile:
        """Count document, relation-endpoint, and state-index work in the fold."""
        document_visits = (
            len(self.graph.namespaces)
            + len(self.graph.tiers)
            + sum(len(tier.items) for tier in self.graph.tiers)
            + len(self.graph.relation_declarations)
            + len(self.graph.attribute_declarations)
        )
        endpoint_visits = 0
        successors: dict[int, list[RelationInstance]] = defaultdict(list)
        for arc in self.arcs():
            endpoint_visits += 2
            successors[arc.left.index].append(arc)
        memo: dict[int, int] = {}

        def value(node: int) -> int:
            if node in memo:
                return memo[node]
            outgoing = successors.get(node, ())
            result = 1 if not outgoing else 0
            for arc in outgoing:
                result += value(arc.right.index)
            memo[node] = result
            return result

        result = value(0)
        return FoldProfile(document_visits, endpoint_visits, len(memo), result)


def _label(text: str) -> AttributeValue:
    return AttributeValue(LABEL, XsdType.STRING, text)


def _weight(value: str) -> AttributeValue:
    return AttributeValue(WEIGHT, XsdType.DECIMAL, value)


def _placements(value: str) -> AttributeValue:
    return AttributeValue(PLACEMENTS, XsdType.STRING, value)


def _arc(identity: str, left: int, right: int, weight: str) -> RelationInstance:
    return RelationInstance(
        ALTERNATION,
        ItemRef(STATE_TIER, left),
        ItemRef(STATE_TIER, right),
        durable_id=identity,
        attributes=(_weight(weight),),
    )


def diamond_graph() -> Graph:
    """Build the page-sized mix oracle.

    The states contain placements of the same kick, bass, vocal, and drum stems.
    The arcs are iv:1, vj:2, id:2, dj:3, and jo:1.  Hand expansion gives
    paths (iv,vj,jo) at score 4 and (id,dj,jo) at score 6.  Thus there are
    two paths, both include reconverged, and the unique best path is the first.
    """
    labels = ("intro", "vocal-first", "drums-first", "reconverged", "outro")
    placements = (
        "kick@1,bass@1",
        "vocal@2,drums@3",
        "drums@2,vocal@3",
        "kick@4,bass@4",
        "vocal@5,drums@5",
    )
    state_tier = Tier(
        TierDeclaration(STATE_TIER, "Candidate arrangement states"),
        tuple(
            Item(attributes=(_label(label), _placements(layout)))
            for label, layout in zip(labels, placements, strict=True)
        ),
    )
    stem_tier = Tier(
        TierDeclaration(STEM_TIER, "Shared source stems"),
        tuple(
            Item(attributes=(_label(stem),))
            for stem in ("kick", "bass", "vocal", "drums")
        ),
    )
    declarations = (
        SimpleRelationDeclaration(MEMBERSHIP, STATE_TIER, STATE_TYPE),
        SimpleRelationDeclaration(STEM_MEMBERSHIP, STEM_TIER, STEM_TYPE),
        BipartiteRelationDeclaration(ALTERNATION, STATE_TYPE, STATE_TYPE, acyclic=True),
    )
    attributes = (
        AttributeDeclaration(LABEL, AttributeDomain.ITEM, XsdType.STRING),
        AttributeDeclaration(PLACEMENTS, AttributeDomain.ITEM, XsdType.STRING),
        AttributeDeclaration(
            WEIGHT, AttributeDomain.RELATION_INSTANCE, XsdType.DECIMAL
        ),
    )
    arcs = (
        _arc("iv", 0, 1, "1"),
        _arc("vj", 1, 3, "2"),
        _arc("id", 0, 2, "2"),
        _arc("dj", 2, 3, "3"),
        _arc("jo", 3, 4, "1"),
    )
    return Graph(NAMESPACE, (state_tier, stem_tier), declarations, arcs, attributes)


def linear_graph() -> Graph:
    """Remove the alternative reading while retaining one complete arrangement."""
    graph = diamond_graph()
    relations = tuple(
        arc for arc in graph.relations if arc.durable_id not in {"id", "dj"}
    )
    return Graph(
        graph.namespaces,
        graph.tiers,
        graph.relation_declarations,
        relations,
        graph.attribute_declarations,
    )


def _arc_decimals(graph: Graph) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    for arc in graph.relations:
        assert arc.durable_id is not None
        result[arc.durable_id] = Decimal(arc.attributes[0].lexical)
    return result


def _item_attribute(item: Item, declaration: QualifiedName) -> str:
    """Read one string attribute from a fixture item."""
    return next(
        attribute.lexical
        for attribute in item.attributes
        if attribute.name == declaration
    )


def _placement_stems(layout: str) -> frozenset[str]:
    """Return the stem identities named by a placement string."""
    return frozenset(placement.split("@", 1)[0] for placement in layout.split(","))


def test_mix_readings_rearrange_the_same_stems_and_share_both_sides() -> None:
    """The alternatives move shared material between bars and reconverge."""
    graph = diamond_graph()
    states, stems = graph.tiers
    declared_stems = frozenset(_item_attribute(item, LABEL) for item in stems.items)
    layouts = tuple(_item_attribute(item, PLACEMENTS) for item in states.items)
    vocal_first = layouts[1]
    drums_first = layouts[2]
    assert declared_stems == frozenset(("kick", "bass", "vocal", "drums"))
    assert (
        _placement_stems(vocal_first)
        == _placement_stems(drums_first)
        == {
            "vocal",
            "drums",
        }
    )
    assert vocal_first == "vocal@2,drums@3"
    assert drums_first == "drums@2,vocal@3"
    paths = PackedAlternationSuite(graph).enumerate_paths(output_cap=2)
    assert paths == (("iv", "vj", "jo"), ("id", "dj", "jo"))
    assert layouts[0] == "kick@1,bass@1"
    assert layouts[3:] == ("kick@4,bass@4", "vocal@5,drums@5")


def test_page_sized_oracle_pins_paths_scores_and_reconvergence() -> None:
    """The independent literal oracle distinguishes paths through one shared join."""
    suite = PackedAlternationSuite(diamond_graph())
    expected_paths = (("iv", "vj", "jo"), ("id", "dj", "jo"))
    expected_scores = (
        (("iv", "vj", "jo"), Decimal(4)),
        (("id", "dj", "jo"), Decimal(6)),
    )
    assert _arc_decimals(suite.graph) == {
        "iv": Decimal(1),
        "vj": Decimal(2),
        "id": Decimal(2),
        "dj": Decimal(3),
        "jo": Decimal(1),
    }
    assert suite.enumerate_paths(output_cap=2) == expected_paths
    computed_scores = tuple(
        (path, suite.score_path(path, DECIMAL_TROPICAL, _arc_decimals(suite.graph)))
        for path in suite.enumerate_paths(output_cap=2)
    )
    assert computed_scores == expected_scores
    assert min(expected_scores, key=lambda scored: scored[1]) == (
        ("iv", "vj", "jo"),
        Decimal(4),
    )
    assert tuple(path[-1] for path in expected_paths) == ("jo", "jo")
    assert (
        suite.fold(COUNTING, {identity: 1 for identity in _arc_decimals(suite.graph)})
        == 2
    )
    assert suite.fold(DECIMAL_TROPICAL, _arc_decimals(suite.graph)) == Decimal(4)


def test_idempotence_masks_the_double_counting_defect() -> None:
    """Boolean accepts duplicate yield while counting exposes two extra paths."""
    suite = PackedAlternationSuite(diamond_graph())
    counting_values = {identity: 1 for identity in _arc_decimals(suite.graph)}
    boolean_values = {identity: True for identity in counting_values}
    assert suite.fold(COUNTING, counting_values) == 2
    assert suite.defective_fold(COUNTING, counting_values) == 4
    assert suite.fold(BOOLEAN, boolean_values) is True
    assert suite.defective_fold(BOOLEAN, boolean_values) is True


def test_arrival_fold_agrees_without_reconvergence() -> None:
    """A single-arrival chain cannot trigger repeated joined propagation."""
    suite = PackedAlternationSuite(linear_graph())
    counting_values = {identity: 1 for identity in _arc_decimals(suite.graph)}
    boolean_values = {identity: True for identity in counting_values}
    assert suite.fold(COUNTING, counting_values) == 1
    assert suite.defective_fold(COUNTING, counting_values) == 1
    assert suite.fold(BOOLEAN, boolean_values) is True
    assert suite.defective_fold(BOOLEAN, boolean_values) is True


def test_probability_mass_is_also_sensitive_to_duplicate_yield() -> None:
    """Exact branch mass sums to one before and two after duplicated arrival yield."""
    suite = PackedAlternationSuite(diamond_graph())
    values = {identity: Fraction(1) for identity in _arc_decimals(suite.graph)}
    values["iv"] = values["id"] = Fraction(1, 2)
    probability = ProbabilitySemiring()
    assert suite.fold(probability, values) == Fraction(1)
    assert suite.defective_fold(probability, values) == Fraction(2)


def test_output_cap_refuses_instead_of_silently_truncating() -> None:
    """A cap below the complete alternative set names the excluded path."""
    with pytest.raises(ValueError, match=r"output cap 1 excludes path.*id.*dj.*jo"):
        PackedAlternationSuite(diamond_graph()).enumerate_paths(output_cap=1)


def representation_sizes(disagreements: int) -> dict[str, int]:
    """Return asserted packed and duplicated sizes for serial diamond choices.

    A packed chain has one initial state, two local readings plus one join per
    disagreement, and four arcs per disagreement.  Duplicating a complete tier
    stores every reading, each with the initial state and two states per choice.
    Duplication is simpler and cannot double-count; it is rejected only because
    independent choices make its item incidence exponential instead of additive.
    """
    packed_nodes = ["start"]
    packed_arcs: list[tuple[str, str]] = []
    for point in range(disagreements):
        source = packed_nodes[-1]
        left = f"choice-{point}-left"
        right = f"choice-{point}-right"
        join = f"join-{point}"
        packed_nodes.extend((left, right, join))
        packed_arcs.extend(
            ((source, left), (left, join), (source, right), (right, join))
        )
    duplicated = tuple(
        tuple(
            ["start"]
            + [
                item
                for point in range(disagreements)
                for item in (
                    f"choice-{point}-{(reading >> point) & 1}",
                    f"join-{point}",
                )
            ]
        )
        for reading in range(2**disagreements)
    )
    return {
        "disagreements": disagreements,
        "packed_nodes": len(packed_nodes),
        "packed_relation_incidence": len(packed_arcs),
        "duplicated_tiers": len(duplicated),
        "duplicated_items": sum(len(tier) for tier in duplicated),
    }


def test_rejected_duplication_has_multiplicative_growth() -> None:
    """Several independent disagreements separate additive packing from copying."""
    assert representation_sizes(8) == {
        "disagreements": 8,
        "packed_nodes": 25,
        "packed_relation_incidence": 32,
        "duplicated_tiers": 256,
        "duplicated_items": 4352,
    }


def complexity_bound(
    profile: FoldProfile,
    output_cap: int,
) -> dict[str, int]:
    """State the packed fold and bounded enumeration account.

    Recognition time is bounded by document size plus relation incidence plus
    the finite index product.  Its state is bounded by the index product.
    Materializing paths adds at most output-cap times index-product work and
    refuses rather than returning a prefix when the cap is insufficient.
    """
    return {
        "document_size": profile.document_visits,
        "relation_incidence": profile.endpoint_visits,
        "index_product_size": profile.index_slots,
        "recognition_time": profile.operations,
        "recognition_state": profile.index_slots,
        "enumeration_time": profile.operations + output_cap * profile.index_slots,
        "output": output_cap,
    }


def test_complexity_account_is_executable() -> None:
    """The oracle's declared bound includes every required cost parameter."""
    profile = PackedAlternationSuite(diamond_graph()).profiled_counting_fold()
    assert profile.value == 2
    assert complexity_bound(profile, 2) == {
        "document_size": 18,
        "relation_incidence": 10,
        "index_product_size": 5,
        "recognition_time": 33,
        "recognition_state": 5,
        "enumeration_time": 43,
        "output": 2,
    }


@pytest.mark.parametrize(
    "omitted",
    ("document_size", "relation_incidence", "index_product_size"),
)
def test_each_complexity_term_is_required_by_measured_work(omitted: str) -> None:
    """Deleting any structural term makes the bound reject measured operations."""
    profile = PackedAlternationSuite(diamond_graph()).profiled_counting_fold()
    terms = {
        "document_size": profile.document_visits,
        "relation_incidence": profile.endpoint_visits,
        "index_product_size": profile.index_slots,
    }
    claimed_bound = sum(value for name, value in terms.items() if name != omitted)
    with pytest.raises(AssertionError):
        assert profile.operations <= claimed_bound
