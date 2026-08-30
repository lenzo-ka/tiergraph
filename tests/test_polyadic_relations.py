"""Polyadic declarations refuse every consumer relation-contract violation."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

import pytest

from tiergraph import (
    AttachValue,
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BoundarySide,
    DurableBoundaryRef,
    Graph,
    Item,
    ItemRef,
    NamespaceDeclaration,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    QualifiedName,
    RelationEndpointKind,
    RelationSideDeclaration,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    XsdType,
    dumps,
    loads,
    to_data,
)

NS = "urn:polyadic"


def name(local: str) -> QualifiedName:
    """Build one qualified test name."""
    return QualifiedName(NS, local)


LEFT = name("left")
RIGHT = name("right")
MEMBERS = name("members")
SELECTS = name("selects")


def side(
    *,
    kinds: tuple[RelationEndpointKind, ...] = (RelationEndpointKind.ITEM,),
    tiers: tuple[QualifiedName, ...] | None = None,
    minimum: int = 1,
    maximum: int | None = None,
    allow_empty: bool = False,
) -> RelationSideDeclaration:
    """Build one side contract with readable defaults."""
    return RelationSideDeclaration(kinds, tiers, minimum, maximum, allow_empty)


def declaration(
    *,
    sources: RelationSideDeclaration | None = None,
    targets: RelationSideDeclaration | None = None,
    unique_sources: bool = False,
    distinct_targets: bool = False,
    single_parent: bool = False,
    acyclic: bool = False,
) -> PolyadicRelationDeclaration:
    """Build the general relation declaration used by neighbouring cases."""
    return PolyadicRelationDeclaration(
        MEMBERS,
        side(tiers=(LEFT,), maximum=2) if sources is None else sources,
        side(tiers=(RIGHT,), maximum=3) if targets is None else targets,
        unique_sources,
        distinct_targets,
        single_parent,
        acyclic,
    )


def graph(
    declarations: tuple[PolyadicRelationDeclaration, ...],
    relations: tuple[PolyadicRelationInstance, ...],
) -> Graph:
    """Construct two typed tiers and the supplied polyadic incidence."""
    tiers = (
        Tier(TierDeclaration(LEFT, "Left"), (Item("l0"), Item("l1"))),
        Tier(TierDeclaration(RIGHT, "Right"), (Item("r0"), Item("r1"), Item("r2"))),
    )
    simple = (
        SimpleRelationDeclaration(name("left-members"), LEFT, name("left-type")),
        SimpleRelationDeclaration(name("right-members"), RIGHT, name("right-type")),
    )
    return Graph(
        (NamespaceDeclaration("p", NS),),
        tiers,
        (*simple, *declarations),
        polyadic_relations=relations,
    )


def edge(
    sources: tuple[ItemRef | DurableBoundaryRef, ...],
    targets: tuple[ItemRef | DurableBoundaryRef, ...],
    relation: QualifiedName = MEMBERS,
) -> PolyadicRelationInstance:
    """Build one ordered polyadic instance."""
    return PolyadicRelationInstance(relation, sources, targets)


def test_ordered_multi_arity_accepts_neighbour_and_refuses_offender() -> None:
    """Both ordered sides enforce their independent lower and upper bounds."""
    declared = declaration(
        sources=side(tiers=(LEFT,), minimum=2, maximum=2),
        targets=side(tiers=(RIGHT,), minimum=2, maximum=2),
    )
    good = edge(
        (ItemRef(LEFT, 1), ItemRef(LEFT, 0)), (ItemRef(RIGHT, 2), ItemRef(RIGHT, 0))
    )
    assert graph((declared,), (good,)).polyadic_relations[0].sources == good.sources
    with pytest.raises(ValueError, match=r"relation instance 0 source arity 1"):
        graph((declared,), (edge((ItemRef(LEFT, 0),), good.targets),))


def test_allowed_endpoint_kind_set_accepts_neighbour_and_refuses_offender() -> None:
    """A side admits any declared endpoint kind and no undeclared kind."""
    declared = declaration(
        sources=side(
            kinds=(RelationEndpointKind.ITEM, RelationEndpointKind.BOUNDARY),
            tiers=(LEFT,),
        )
    )
    boundary = DurableBoundaryRef(LEFT, BoundarySide.BEFORE)
    assert graph((declared,), (edge((boundary,), (ItemRef(RIGHT, 0),)),))
    item_only = replace(declared, sources=side(tiers=(LEFT,)))
    with pytest.raises(
        ValueError, match=r"relation instance 0 source endpoint 0.*kind"
    ):
        graph((item_only,), (edge((boundary,), (ItemRef(RIGHT, 0),)),))


def test_allowed_tier_set_accepts_neighbour_and_refuses_offender() -> None:
    """Endpoint tier membership is checked independently of endpoint kind."""
    declared = declaration()
    assert graph((declared,), (edge((ItemRef(LEFT, 0),), (ItemRef(RIGHT, 0),)),))
    with pytest.raises(
        ValueError, match=r"relation instance 0 source endpoint 0.*tier"
    ):
        graph((declared,), (edge((ItemRef(RIGHT, 0),), (ItemRef(RIGHT, 1),)),))


@pytest.mark.parametrize("empty_side", ["source", "target"])
def test_empty_side_is_explicitly_accepted_or_refused(empty_side: str) -> None:
    """Empty sides bypass arity only when their own declaration admits emptiness."""
    sources = () if empty_side == "source" else (ItemRef(LEFT, 0), ItemRef(LEFT, 1))
    targets = () if empty_side == "target" else (ItemRef(RIGHT, 0), ItemRef(RIGHT, 1))
    allowed = declaration(
        sources=side(tiers=(LEFT,), minimum=2, allow_empty=empty_side == "source"),
        targets=side(tiers=(RIGHT,), minimum=2, allow_empty=empty_side == "target"),
        unique_sources=True,
        single_parent=True,
        acyclic=True,
    )
    assert graph((allowed,), (edge(sources, targets),))
    refused = replace(
        allowed,
        sources=replace(allowed.sources, allow_empty=False),
        targets=replace(allowed.targets, allow_empty=False),
    )
    with pytest.raises(
        ValueError, match=rf"relation instance 0 has an empty {empty_side} side"
    ):
        graph((refused,), (edge(sources, targets),))


def test_unique_sources_accepts_neighbour_and_refuses_containment_shape() -> None:
    """Containment is acyclicity plus the general unique-source constraint."""
    declared = declaration(unique_sources=True, acyclic=True)
    good = (
        edge((ItemRef(LEFT, 0),), (ItemRef(RIGHT, 0),)),
        edge((ItemRef(LEFT, 1),), (ItemRef(RIGHT, 1),)),
    )
    assert graph((declared,), good)
    with pytest.raises(ValueError, match=r"relation instance 1 repeats source"):
        graph((declared,), (good[0], edge((ItemRef(LEFT, 0),), (ItemRef(RIGHT, 1),))))


def test_choice_constraints_accept_neighbour_and_refuse_each_offender() -> None:
    """Choice is distinct targets plus the same per-source uniqueness primitive."""
    declared = declaration(unique_sources=True, distinct_targets=True)
    good = edge((ItemRef(LEFT, 0),), (ItemRef(RIGHT, 0), ItemRef(RIGHT, 1)))
    assert graph((declared,), (good,))
    with pytest.raises(ValueError, match=r"relation instance 0 has duplicate"):
        graph(
            (declared,), (edge(good.sources, (ItemRef(RIGHT, 0), ItemRef(RIGHT, 0))),)
        )
    with pytest.raises(ValueError, match=r"relation instance 1 repeats source"):
        graph((declared,), (good, edge(good.sources, (ItemRef(RIGHT, 2),))))


def test_membership_subset_accepts_neighbour_and_refuses_offender() -> None:
    """Selection membership is a general same-source target-subset constraint."""
    alternatives = declaration(unique_sources=True, distinct_targets=True)
    selects = PolyadicRelationDeclaration(
        SELECTS,
        side(tiers=(LEFT,), maximum=1),
        side(tiers=(RIGHT,), minimum=1, maximum=1),
        unique_sources=True,
        targets_subset_of=MEMBERS,
    )
    candidates = edge((ItemRef(LEFT, 0),), (ItemRef(RIGHT, 0), ItemRef(RIGHT, 1)))
    assert graph(
        (alternatives, selects),
        (candidates, edge(candidates.sources, (ItemRef(RIGHT, 1),), SELECTS)),
    )
    with pytest.raises(ValueError, match=r"relation instance 1 has a target outside"):
        graph(
            (alternatives, selects),
            (candidates, edge(candidates.sources, (ItemRef(RIGHT, 2),), SELECTS)),
        )


@pytest.mark.parametrize("reverse", [False, True])
def test_membership_subset_unions_every_base_instance(reverse: bool) -> None:
    """Membership is the order-independent union of same-source base targets."""
    alternatives = declaration()
    selects = PolyadicRelationDeclaration(
        SELECTS,
        side(tiers=(LEFT,), maximum=1),
        side(tiers=(RIGHT,), maximum=1),
        targets_subset_of=MEMBERS,
    )
    source = (ItemRef(LEFT, 0),)
    bases: tuple[PolyadicRelationInstance, ...] = (
        edge(source, (ItemRef(RIGHT, 0),)),
        edge(source, (ItemRef(RIGHT, 1),)),
    )
    if reverse:
        bases = tuple(reversed(bases))
    for member in (ItemRef(RIGHT, 0), ItemRef(RIGHT, 1)):
        assert graph(
            (alternatives, selects),
            (*bases, edge(source, (member,), SELECTS)),
        )
    with pytest.raises(ValueError, match=r"relation instance 2 has a target outside"):
        graph(
            (alternatives, selects),
            (*bases, edge(source, (ItemRef(RIGHT, 2),), SELECTS)),
        )


def test_single_parent_counts_source_composite_once_per_instance() -> None:
    """One hyperedge is one parent; a different source composite is another."""
    same_tier = PolyadicRelationDeclaration(
        MEMBERS,
        side(tiers=(LEFT,), maximum=2),
        side(tiers=(LEFT,), maximum=1),
        single_parent=True,
    )
    composite = edge((ItemRef(LEFT, 0), ItemRef(LEFT, 1)), (ItemRef(LEFT, 0),))
    assert graph((same_tier,), (composite,))
    with pytest.raises(
        ValueError,
        match=r"relation instance 1 gives.*second parent.*relation instance 0",
    ):
        graph(
            (same_tier,),
            (composite, edge((ItemRef(LEFT, 1),), (ItemRef(LEFT, 0),))),
        )


def test_polyadic_acyclic_and_single_parent_refuse_named_offenders() -> None:
    """Composite invariant flags are checked on polyadic incidence itself."""
    same_tier = PolyadicRelationDeclaration(
        MEMBERS,
        side(tiers=(LEFT,), maximum=1),
        side(tiers=(LEFT,), maximum=1),
        single_parent=True,
        acyclic=True,
    )
    assert graph((same_tier,), (edge((ItemRef(LEFT, 0),), (ItemRef(LEFT, 1),)),))
    with pytest.raises(ValueError, match=r"relation instance 1 gives.*second parent"):
        graph(
            (same_tier,),
            (
                edge((ItemRef(LEFT, 0),), (ItemRef(LEFT, 1),)),
                edge((ItemRef(LEFT, 1),), (ItemRef(LEFT, 1),)),
            ),
        )
    with pytest.raises(ValueError, match=r"relation instance 1 closes a cycle"):
        graph(
            (same_tier,),
            (
                edge((ItemRef(LEFT, 0),), (ItemRef(LEFT, 1),)),
                edge((ItemRef(LEFT, 1),), (ItemRef(LEFT, 0),)),
            ),
        )


def test_polyadic_contract_round_trips_through_the_public_wire() -> None:
    """The declaration and ordered instance survive the versioned codec."""
    declared = declaration(unique_sources=True, distinct_targets=True, acyclic=True)
    original = graph(
        (declared,),
        (
            edge(
                (ItemRef(LEFT, 1), ItemRef(LEFT, 0)),
                (ItemRef(RIGHT, 2), ItemRef(RIGHT, 0)),
            ),
        ),
    )
    assert loads(dumps(original)) == original


@pytest.mark.parametrize(
    ("build", "offender"),
    [
        (lambda: side(kinds=()), "endpoint kinds must not be empty"),
        (
            lambda: side(kinds=(RelationEndpointKind.ITEM, RelationEndpointKind.ITEM)),
            "endpoint kinds must be unique",
        ),
        (lambda: side(tiers=(LEFT, LEFT)), "tiers must be unique"),
        (lambda: side(minimum=True), "minimum True must be a nonnegative integer"),
        (lambda: side(maximum=-1), "maximum -1 must be a nonnegative integer"),
        (lambda: side(minimum=2, maximum=1), "maximum must not be less than minimum"),
        (
            lambda: side(allow_empty=cast(bool, 1)),
            "allow-empty promise 1 must be boolean",
        ),
        (
            lambda: PolyadicRelationDeclaration(
                MEMBERS, side(), side(), unique_sources=cast(bool, 1)
            ),
            "unique-sources promise 1 must be boolean",
        ),
        (
            lambda: PolyadicRelationInstance(MEMBERS, (), (), ""),
            "durable id '' must not be empty",
        ),
    ],
)
def test_declaration_shape_refuses_named_offender(build: object, offender: str) -> None:
    """Context-free declaration failures are loud before graph construction."""
    assert callable(build)
    with pytest.raises(ValueError, match=offender):
        build()


def test_cross_relation_declaration_and_owner_are_both_required() -> None:
    """A subset contract refuses an undeclared base and a missing same-source owner."""
    selects = PolyadicRelationDeclaration(
        SELECTS,
        side(tiers=(LEFT,), maximum=1),
        side(tiers=(RIGHT,), maximum=1),
        targets_subset_of=MEMBERS,
    )
    selected = edge((ItemRef(LEFT, 0),), (ItemRef(RIGHT, 0),), SELECTS)
    with pytest.raises(ValueError, match=r"targets-subset-of names undeclared"):
        graph((selects,), (selected,))
    alternatives = declaration()
    with pytest.raises(ValueError, match=r"relation instance 0 source.*has no"):
        graph((alternatives, selects), (selected,))


def test_polyadic_instance_requires_its_own_declaration_kind() -> None:
    """A multi-sided instance cannot borrow a simple declaration's name."""
    with pytest.raises(
        ValueError, match=r"polyadic relation instance 0 names.*required"
    ):
        graph((), (edge((ItemRef(LEFT, 0),), (ItemRef(RIGHT, 0),)),))


def test_wire_refuses_plural_subset_names_and_non_array_side() -> None:
    """Near-valid polyadic wire edits name the malformed field."""
    original = graph((declaration(),), ())
    wire_document = to_data(original)
    data = cast(dict[str, Any], wire_document["graph"])
    declared = next(
        value for value in data["relation_declarations"] if value["kind"] == "polyadic"
    )
    declared["targets_subset_of"] = ["p:members", "p:selects"]
    import json

    document = json.dumps(wire_document)
    with pytest.raises(ValueError, match=r"targets_subset_of must contain at most one"):
        loads(document)
    declared["targets_subset_of"] = []
    declared["sources"] = []
    with pytest.raises(ValueError, match=r"sources must be an object"):
        loads(json.dumps(wire_document))
    encoded_original = cast(dict[str, Any], to_data(original)["graph"])
    declared["sources"] = cast(
        dict[str, Any],
        next(
            value
            for value in cast(
                list[dict[str, Any]],
                encoded_original["relation_declarations"],
            )
            if value["kind"] == "polyadic"
        )["sources"],
    )
    declared["sources"]["endpoint_kinds"] = {}
    with pytest.raises(ValueError, match=r"endpoint_kinds must be an array"):
        loads(json.dumps(wire_document))


def test_machine_attaches_a_declared_value_to_polyadic_declaration() -> None:
    """The relation-declaration opcode path preserves every polyadic constraint."""
    original = graph((declaration(unique_sources=True),), ())
    attribute = AttributeDeclaration(
        name("relation-note"), AttributeDomain.RELATION_DECLARATION, XsdType.STRING
    )
    declared = Graph(
        original.namespaces,
        original.tiers,
        original.relation_declarations,
        attribute_declarations=(attribute,),
        polyadic_relations=original.polyadic_relations,
    )
    value = AttributeValue(attribute.name, XsdType.STRING, "kept")
    changed = AttachValue(AttributeDomain.RELATION_DECLARATION, MEMBERS, value).apply(
        declared
    )
    relation = next(
        item for item in changed.relation_declarations if item.name == MEMBERS
    )
    assert relation.attributes == (value,)
