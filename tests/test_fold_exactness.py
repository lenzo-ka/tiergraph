"""A fold's exactness is a declared claim, and every branch of it bites."""

from __future__ import annotations

import re
from dataclasses import replace
from decimal import Decimal
from typing import cast

import pytest

from tests.conformance.fold import FoldFixture
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
from tiergraph.fold import (
    AttributeValuation,
    ChildCombination,
    ExactnessRefusal,
    FoldDeclaration,
    FoldExactness,
    FoldTransition,
    Lift,
)
from tiergraph.semiring import (
    COUNTING,
    DECIMAL_TROPICAL,
    TROPICAL,
    LawCheck,
    Semiring,
)

FIXTURE = FoldFixture()
DEPENDS = FIXTURE.name("depends")
PLACEMENT = FIXTURE.name("placement")
COST = FIXTURE.name("cost")


def decimal_lift(value: object, label: str) -> Decimal:
    """Embed the read decimal field in an unenriched carrier."""
    del label
    return cast(Decimal, value)


def double_lift(value: object, label: str) -> float:
    """Embed the read decimal field in the inexact IEEE-double carrier."""
    del label
    return float(cast(Decimal, value))


def one_lift(value: object, label: str) -> int:
    """Embed the multiplicative identity whatever the value is."""
    del value, label
    return 1


def diamond(
    semiring: Semiring[object],
    lift: Lift[object],
    combination: ChildCombination = ChildCombination.OR,
    exactness: FoldExactness = FoldExactness.UNDECLARED,
) -> FoldDeclaration[object]:
    """Declare a fold over the fixture's start-bed-sting-out dependency diamond."""
    graph = FIXTURE.graph()
    return FoldDeclaration(
        "mix-exactness",
        graph,
        AttributeValuation("cost", COST, (PLACEMENT,)),
        semiring,
        lift,
        (FoldTransition(DEPENDS, combination),),
        roots=(ItemRef(PLACEMENT, 0),),
        exactness=exactness,
    )


def cyclic(
    semiring: Semiring[object],
    exactness: FoldExactness = FoldExactness.UNDECLARED,
) -> FoldDeclaration[object]:
    """Declare a fold whose two individually acyclic relations compose a cycle."""
    tier_name = FIXTURE.name("loop-nodes")
    item_type = FIXTURE.name("loop-node")
    cost = FIXTURE.name("loop-cost")
    members = SimpleRelationDeclaration(
        FIXTURE.name("loop-members"), tier_name, item_type
    )
    first = BipartiteRelationDeclaration(
        FIXTURE.name("loop-first"), item_type, item_type, acyclic=True
    )
    second = BipartiteRelationDeclaration(
        FIXTURE.name("loop-second"), item_type, item_type, acyclic=True
    )
    graph = Graph(
        FIXTURE.graph().namespaces,
        (
            Tier(
                TierDeclaration(tier_name, "Loop nodes"),
                tuple(
                    Item(label, (AttributeValue(cost, XsdType.DECIMAL, "1"),))
                    for label in ("a", "b")
                ),
            ),
        ),
        (members, first, second),
        (
            RelationInstance(first.name, ItemRef(tier_name, 0), ItemRef(tier_name, 1)),
            RelationInstance(second.name, ItemRef(tier_name, 1), ItemRef(tier_name, 0)),
        ),
        (AttributeDeclaration(cost, AttributeDomain.ITEM, XsdType.DECIMAL),),
    )
    return FoldDeclaration(
        "loop-exactness",
        graph,
        AttributeValuation("loop-cost", cost, (tier_name,)),
        semiring,
        cast(Lift[object], decimal_lift),
        (
            FoldTransition(first.name, ChildCombination.AND),
            FoldTransition(second.name, ChildCombination.AND),
        ),
        roots=(ItemRef(tier_name, 0),),
        exactness=exactness,
    )


def nested_alternatives(
    semiring: Semiring[object],
    exactness: FoldExactness = FoldExactness.UNDECLARED,
) -> FoldDeclaration[object]:
    """Declare a fold whose two alternatives share the same pair of alternatives.

    Packed evaluation groups the four derivations as ``(A + B) + (C + D)`` while
    enumerating them groups the same four as ``((A + B) + C) + D``. Both are the
    combination over every derivation only if addition regroups, so this shape is
    where an unlawful ``+`` and an unlawful fold part company.
    """
    namespace = "urn:tiergraph:witness:nested"
    tier_name = QualifiedName(namespace, "nodes")
    item_type = QualifiedName(namespace, "node")
    weight = QualifiedName(namespace, "weight")
    members = SimpleRelationDeclaration(
        QualifiedName(namespace, "members"), tier_name, item_type
    )
    choice = BipartiteRelationDeclaration(
        QualifiedName(namespace, "choice"), item_type, item_type, acyclic=True
    )
    labels = (("r", "1"), ("p", "2"), ("q", "5"), ("s", "1"), ("t", "3"))
    graph = Graph(
        (NamespaceDeclaration("nested", namespace),),
        (
            Tier(
                TierDeclaration(tier_name, "Nested alternatives"),
                tuple(
                    Item(label, (AttributeValue(weight, XsdType.DECIMAL, lexical),))
                    for label, lexical in labels
                ),
            ),
        ),
        (members, choice),
        tuple(
            RelationInstance(
                choice.name, ItemRef(tier_name, left), ItemRef(tier_name, right)
            )
            for left, right in ((0, 1), (0, 2), (1, 3), (1, 4), (2, 3), (2, 4))
        ),
        (AttributeDeclaration(weight, AttributeDomain.ITEM, XsdType.DECIMAL),),
    )
    return FoldDeclaration(
        "nested-exactness",
        graph,
        AttributeValuation("weight", weight, (tier_name,)),
        semiring,
        cast(Lift[object], decimal_lift),
        (FoldTransition(choice.name, ChildCombination.OR),),
        roots=(ItemRef(tier_name, 0),),
        exactness=exactness,
    )


class MaxOverSumSemiring:
    """Claim every law exactly while multiplication does not distribute over addition."""

    zero = Decimal(0)
    one = Decimal(0)
    add_associativity = multiply_associativity = LawCheck.EXACT
    add_commutativity = left_distributivity = right_distributivity = LawCheck.EXACT

    def add(self, left: Decimal, right: Decimal, /) -> Decimal:
        """Accumulate alternatives."""
        return left + right

    def multiply(self, left: Decimal, right: Decimal, /) -> Decimal:
        """Take the larger operand, which does not distribute over accumulation."""
        return max(left, right)

    def encode(self, value: Decimal, /) -> object:
        """Encode exact decimal text."""
        return str(value)

    def decode(self, value: object, /) -> Decimal:
        """Decode exact decimal text."""
        return Decimal(cast(str, value))


class RightProjectionSemiring:
    """Distribute on the left and fail on the right, because multiplication drops its left."""

    zero = Decimal(0)
    one = Decimal(1)
    add_associativity = multiply_associativity = LawCheck.EXACT
    add_commutativity = left_distributivity = right_distributivity = LawCheck.EXACT

    def add(self, left: Decimal, right: Decimal, /) -> Decimal:
        """Accumulate alternatives."""
        return left + right

    def multiply(self, left: Decimal, right: Decimal, /) -> Decimal:
        """Return the right operand and discard the left."""
        del left
        return right

    def encode(self, value: Decimal, /) -> object:
        """Encode exact decimal text."""
        return str(value)

    def decode(self, value: object, /) -> Decimal:
        """Decode exact decimal text."""
        return Decimal(cast(str, value))


class HalfOverlapSemiring:
    """Distribute exactly while addition does not regroup.

    ``x ⊕ y = x + y - min(x, y) / 2`` is homogeneous, so multiplication by a
    non-negative decimal distributes over it exactly and the law search finds
    nothing. It is not associative, so a fold that groups alternatives by shared
    structure and an enumeration that groups them left to right disagree. This is
    a carrier whose declared laws are all exact and whose fold is still not the
    combination over every derivation.
    """

    zero = Decimal(0)
    one = Decimal(1)
    add_associativity = multiply_associativity = LawCheck.EXACT
    add_commutativity = left_distributivity = right_distributivity = LawCheck.EXACT

    def add(self, left: Decimal, right: Decimal, /) -> Decimal:
        """Accumulate alternatives while discounting half of the shared part."""
        return left + right - min(left, right) / 2

    def multiply(self, left: Decimal, right: Decimal, /) -> Decimal:
        """Return the exact decimal product."""
        return left * right

    def encode(self, value: Decimal, /) -> object:
        """Encode exact decimal text."""
        return str(value)

    def decode(self, value: object, /) -> Decimal:
        """Decode exact decimal text."""
        return Decimal(cast(str, value))


def reported(message: str, name: str) -> Decimal:
    """Read one operand back out of a rendered counterexample."""
    match = re.search(rf"(?<![a-z\)]){name} = '([^']*)'", message)
    assert match is not None, message
    return Decimal(match.group(1))


def test_undeclared_exactness_refusal_returns_the_declaration() -> None:
    """REGRESSION: omitting the claim is answered with the claim to be made."""
    declared = diamond(cast(Semiring[object], DECIMAL_TROPICAL), decimal_lift)
    assert declared.exactness is FoldExactness.UNDECLARED
    with pytest.raises(ExactnessRefusal) as refusal:
        declared.check_exactness()
    message = str(refusal.value)
    assert "exactness is UNDECLARED" in message
    for name in ("DISTRIBUTIVE", "APPROXIMATE", "STRUCTURAL"):
        assert name in message
    assert "Not declaring is not the same as declaring APPROXIMATE" in message


def test_distributive_claim_over_an_inexact_algebra_is_refused_at_declaration() -> None:
    """REGRESSION: the necessary carrier condition is settled without running."""
    with pytest.raises(ExactnessRefusal) as refusal:
        diamond(
            cast(Semiring[object], TROPICAL),
            cast(Lift[object], double_lift),
            exactness=FoldExactness.DISTRIBUTIVE,
        )
    message = str(refusal.value)
    assert "multiply_associativity only 'approximate'" in message
    assert "Declare APPROXIMATE." in message


def test_structural_claim_without_a_star_warrant_is_refused_at_declaration() -> None:
    """REGRESSION: a structural claim owes the warrant that makes it converge."""
    assert COUNTING.star is None
    with pytest.raises(ExactnessRefusal) as refusal:
        diamond(
            cast(Semiring[object], COUNTING),
            cast(Lift[object], one_lift),
            exactness=FoldExactness.STRUCTURAL,
        )
    assert "declares no star" in str(refusal.value)


def test_structural_claim_over_an_acyclic_fold_is_refused() -> None:
    """REGRESSION: a finite derivation set is something to be measured against."""
    declared = diamond(
        cast(Semiring[object], DECIMAL_TROPICAL),
        decimal_lift,
        exactness=FoldExactness.STRUCTURAL,
    )
    with pytest.raises(ExactnessRefusal) as refusal:
        declared.check_exactness()
    message = str(refusal.value)
    assert "dependency graph is acyclic" in message
    assert "Declare DISTRIBUTIVE or APPROXIMATE." in message


def test_structural_claim_over_a_cycle_is_certified_without_comparison() -> None:
    """REGRESSION: an infinite derivation set is certified, never enumerated."""
    certificate = cyclic(
        cast(Semiring[object], DECIMAL_TROPICAL),
        exactness=FoldExactness.STRUCTURAL,
    ).check_exactness()
    assert certificate.exactness is FoldExactness.STRUCTURAL
    assert certificate.compared is False
    assert certificate.derivations == 0
    assert certificate.result.value == DECIMAL_TROPICAL.zero


def test_distributive_claim_over_a_cycle_names_the_component() -> None:
    """REGRESSION: a cyclic fold has no finite combination for a claim to equal."""
    declared = cyclic(
        cast(Semiring[object], DECIMAL_TROPICAL),
        exactness=FoldExactness.DISTRIBUTIVE,
    )
    with pytest.raises(ExactnessRefusal) as refusal:
        declared.check_exactness()
    message = str(refusal.value)
    assert "declares DISTRIBUTIVE exactness over a dependency cycle" in message
    assert "'index': 0" in message and "'index': 1" in message
    assert "Declare STRUCTURAL." in message


def test_false_distributive_claim_returns_a_left_distributivity_counterexample() -> (
    None
):
    """REGRESSION: the false claim is answered with a checkable counterexample."""
    algebra = MaxOverSumSemiring()
    # Established independently of the fold: this carrier is genuinely unlawful.
    assert algebra.multiply(
        Decimal(5), algebra.add(Decimal(1), Decimal(1))
    ) != algebra.add(
        algebra.multiply(Decimal(5), Decimal(1)),
        algebra.multiply(Decimal(5), Decimal(1)),
    )
    declared = diamond(
        cast(Semiring[object], algebra),
        decimal_lift,
        exactness=FoldExactness.DISTRIBUTIVE,
    )
    with pytest.raises(ExactnessRefusal) as refusal:
        declared.check_exactness()
    message = str(refusal.value)
    assert "denies left_distributivity at values this fold produces" in message
    left = reported(message, "a")
    first = reported(message, "b")
    second = reported(message, "c")
    got = algebra.multiply(left, algebra.add(first, second))
    want = algebra.add(algebra.multiply(left, first), algebra.multiply(left, second))
    assert got != want, "the reported triple must actually deny the law"
    assert f"a ⊗ (b ⊕ c) = '{got}'" in message
    assert f"(a ⊗ b) ⊕ (a ⊗ c) = '{want}'" in message


def test_false_distributive_claim_can_name_right_distributivity() -> None:
    """REGRESSION: the sides are checked separately, because they can fail separately."""
    algebra = RightProjectionSemiring()
    assert algebra.multiply(
        algebra.add(Decimal(1), Decimal(1)), Decimal(4)
    ) == algebra.add(
        algebra.multiply(Decimal(1), Decimal(4)),
        algebra.multiply(Decimal(1), Decimal(4)),
    ) - Decimal(4)
    declared = diamond(
        cast(Semiring[object], algebra),
        decimal_lift,
        exactness=FoldExactness.DISTRIBUTIVE,
    )
    with pytest.raises(ExactnessRefusal) as refusal:
        declared.check_exactness()
    message = str(refusal.value)
    assert "denies right_distributivity at values this fold produces" in message
    left = reported(message, "a")
    first = reported(message, "b")
    second = reported(message, "c")
    got = algebra.multiply(algebra.add(first, second), left)
    want = algebra.add(algebra.multiply(first, left), algebra.multiply(second, left))
    assert got != want
    assert f"(b ⊕ c) ⊗ a = '{got}'" in message
    assert f"(b ⊗ a) ⊕ (c ⊗ a) = '{want}'" in message


def test_a_lawful_carrier_can_still_disagree_with_its_own_derivations() -> None:
    """REGRESSION: declared laws are necessary and not sufficient, and this proves it."""
    algebra = HalfOverlapSemiring()
    declared = nested_alternatives(
        cast(Semiring[object], algebra), exactness=FoldExactness.DISTRIBUTIVE
    )
    with pytest.raises(ExactnessRefusal) as refusal:
        declared.check_exactness()
    message = str(refusal.value)
    assert "not the combination over every derivation" in message
    assert "Enumerated 4 derivations" in message
    assert "No probe triple denies distributivity" in message
    assert f"fold value '{declared.run().value}'" in message


def test_distributive_claim_stands_over_the_shipped_diamond() -> None:
    """CHARACTERIZATION: the shipped alternation fold is exact, and now says so."""
    certificate = diamond(
        cast(Semiring[object], DECIMAL_TROPICAL),
        decimal_lift,
        exactness=FoldExactness.DISTRIBUTIVE,
    ).check_exactness()
    assert certificate.exactness is FoldExactness.DISTRIBUTIVE
    assert certificate.compared is True
    assert certificate.derivations == 2
    assert certificate.probes > 0
    assert certificate.result.value == Decimal(4)


def test_joint_requirements_are_enumerated_without_shared_reuse() -> None:
    """CHARACTERIZATION: an AND fold over a diamond enumerates one derivation."""
    certificate = diamond(
        cast(Semiring[object], COUNTING),
        cast(Lift[object], one_lift),
        ChildCombination.AND,
        FoldExactness.DISTRIBUTIVE,
    ).check_exactness()
    assert certificate.derivations == 1
    assert certificate.compared is True
    assert certificate.result.value == 1


def test_derivation_budget_certifies_without_comparing() -> None:
    """REGRESSION: an enumeration that will not fit is reported, not silently skipped."""
    certificate = diamond(
        cast(Semiring[object], DECIMAL_TROPICAL),
        decimal_lift,
        exactness=FoldExactness.DISTRIBUTIVE,
    ).check_exactness(derivation_budget=1)
    assert certificate.compared is False
    assert certificate.derivations == 0


def test_approximate_claim_that_cannot_be_exhibited_is_refused() -> None:
    """REGRESSION: an approximation nothing can exhibit is a declaration that is hiding."""
    declared = diamond(
        cast(Semiring[object], DECIMAL_TROPICAL),
        decimal_lift,
        exactness=FoldExactness.APPROXIMATE,
    )
    with pytest.raises(ExactnessRefusal) as refusal:
        declared.check_exactness()
    message = str(refusal.value)
    assert "equals the combination over all 2 of its derivations" in message
    assert "Declare DISTRIBUTIVE." in message


def test_approximate_claim_stands_over_an_inexact_algebra() -> None:
    """REGRESSION: the approximation arm is instantiated by the IEEE-double carriers."""
    certificate = diamond(
        cast(Semiring[object], TROPICAL),
        cast(Lift[object], double_lift),
        exactness=FoldExactness.APPROXIMATE,
    ).check_exactness()
    assert certificate.exactness is FoldExactness.APPROXIMATE
    assert certificate.compared is True
    assert certificate.result.value == 4.0


def test_an_undeclared_fold_keeps_its_default_through_replace() -> None:
    """REGRESSION: the claim is a field of the declaration, not of the run."""
    declared = diamond(cast(Semiring[object], DECIMAL_TROPICAL), decimal_lift)
    promoted = replace(declared, exactness=FoldExactness.DISTRIBUTIVE)
    assert declared.exactness is FoldExactness.UNDECLARED
    assert promoted.exactness is FoldExactness.DISTRIBUTIVE
    assert promoted.run().value == declared.run().value


def test_certificate_serialization_keeps_what_makes_it_honest() -> None:
    """REGRESSION: `compared` survives to_data, so a law search cannot read as a proof.

    A certificate reporting only its exactness would let a claim that stood on
    the law search alone look identical to one measured against every
    derivation. That distinction is the reason the type exists, so losing it in
    serialization would make the wire form say more than the value does.
    """
    semiring = cast(Semiring[object], DECIMAL_TROPICAL)
    certificate = cyclic(semiring, exactness=FoldExactness.STRUCTURAL).check_exactness()
    data = certificate.to_data(semiring)

    assert data["exactness"] == FoldExactness.STRUCTURAL.value
    assert data["compared"] is False
    assert data["derivations"] == 0
    assert set(data) == {"exactness", "result", "probes", "derivations", "compared"}
    assert isinstance(data["result"], dict)
    assert "value" in data["result"]
