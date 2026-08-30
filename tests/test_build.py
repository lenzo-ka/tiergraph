"""The convenience builder lowers without changing kernel graph identity."""

from __future__ import annotations

from decimal import Decimal
from typing import cast

import pytest

from tiergraph import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    Boundary,
    BoundarySide,
    DurableBoundaryRef,
    DurableItemRef,
    Graph,
    Item,
    ItemRef,
    NamespaceDeclaration,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    QualifiedName,
    RelationEndpointKind,
    RelationInstance,
    RelationSideDeclaration,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    XsdType,
    dump_bytes,
)
from tiergraph.build import BuilderError, Document, document, item
from tiergraph.core import RelationDeclaration


def test_caption_builder_is_byte_identical_to_direct_construction() -> None:
    """Typed tiers, values, and ordered links have identical constructor data."""
    ns = "https://example.com/caption"

    def q(local: str) -> QualifiedName:
        return QualifiedName(ns, local)

    doc = document(ns, prefix="cap")
    doc.attribute("text", XsdType.STRING)
    doc.attribute("confidence", "decimal")
    doc.attribute("reviewed", XsdType.BOOLEAN, domain="relation_declaration")
    words = doc.tier(
        "words",
        (item("w1", text="Hello", confidence=Decimal("0.90")), "w2"),
        item_type="word",
        membership="word-membership",
    )
    captions = doc.tier(
        "captions",
        (item("c1", attrs={q("text"): "Hello world"}),),
        item_type="caption",
        membership="caption-membership",
        long_name="Captions",
    )
    doc.link(
        "spells",
        words,
        captions,
        ((0, DurableItemRef("c1")), (words.ref(1), 0)),
        acyclic=True,
        attributes={"reviewed": True},
    )
    built = doc.build()
    direct = Graph(
        (NamespaceDeclaration("cap", ns),),
        (
            Tier(
                TierDeclaration(q("words"), "words"),
                (
                    Item(
                        "w1",
                        (
                            AttributeValue(q("text"), XsdType.STRING, "Hello"),
                            AttributeValue(q("confidence"), XsdType.DECIMAL, "0.90"),
                        ),
                    ),
                    Item("w2"),
                ),
            ),
            Tier(
                TierDeclaration(q("captions"), "Captions"),
                (
                    Item(
                        "c1",
                        (AttributeValue(q("text"), XsdType.STRING, "Hello world"),),
                    ),
                ),
            ),
        ),
        (
            SimpleRelationDeclaration(q("word-membership"), q("words"), q("word")),
            SimpleRelationDeclaration(
                q("caption-membership"), q("captions"), q("caption")
            ),
            BipartiteRelationDeclaration(
                q("spells"),
                q("word"),
                q("caption"),
                acyclic=True,
                attributes=(AttributeValue(q("reviewed"), XsdType.BOOLEAN, "true"),),
            ),
        ),
        (
            RelationInstance(q("spells"), ItemRef(q("words"), 0), DurableItemRef("c1")),
            RelationInstance(
                q("spells"), ItemRef(q("words"), 1), ItemRef(q("captions"), 0)
            ),
        ),
        (
            AttributeDeclaration(q("text"), AttributeDomain.ITEM, XsdType.STRING),
            AttributeDeclaration(
                q("confidence"), AttributeDomain.ITEM, XsdType.DECIMAL
            ),
            AttributeDeclaration(
                q("reviewed"), AttributeDomain.RELATION_DECLARATION, XsdType.BOOLEAN
            ),
        ),
    )
    assert built == direct
    assert built.to_data() == direct.to_data()
    assert dump_bytes(built) == dump_bytes(direct)
    assert doc.build() == built
    assert doc.build() is not built


def test_bulk_attributes_are_byte_identical_to_single_declarations() -> None:
    """Bulk declarations preserve the exact single-declaration graph bytes."""
    ns = "urn:bulk"
    bulk = document(ns, prefix="b")
    bulk.attributes(
        {
            "text": XsdType.STRING,
            "score": ("decimal", AttributeDomain.ITEM),
            "note": (XsdType.STRING, "document"),
        }
    )
    bulk.tier("items", (item(text="word", score=Decimal("1.25")),))
    bulk.attach(AttributeDomain.DOCUMENT, None, {"note": "graph"})

    singles = document(ns, prefix="b")
    singles.attribute("text", XsdType.STRING)
    singles.attribute("score", "decimal", domain=AttributeDomain.ITEM)
    singles.attribute("note", XsdType.STRING, domain="document")
    singles.tier("items", (item(text="word", score=Decimal("1.25")),))
    singles.attach(AttributeDomain.DOCUMENT, None, {"note": "graph"})

    assert bulk.build() == singles.build()
    assert dump_bytes(bulk.build()) == dump_bytes(singles.build())


def test_bulk_attribute_refusals_are_clear_and_atomic() -> None:
    """Bad bulk shapes, types, domains, and duplicate names fail before mutation."""
    doc = document("urn:bulk-errors", prefix="b")
    doc.attribute("existing", XsdType.STRING)
    with pytest.raises(BuilderError, match="expected a mapping"):
        doc.attributes(cast(object, ()))  # type: ignore[arg-type]
    with pytest.raises(BuilderError, match="invalid default domain"):
        doc.attributes({}, domain="bad")
    with pytest.raises(BuilderError, match="expected XsdType"):
        doc.attributes({"bad-shape": (XsdType.STRING,)})  # type: ignore[dict-item]
    with pytest.raises(BuilderError, match="attributes bad-type"):
        doc.attributes({"bad-type": cast(XsdType, object())})
    with pytest.raises(BuilderError, match="attributes bad-domain"):
        doc.attributes({"bad-domain": (XsdType.STRING, "bad")})
    with pytest.raises(BuilderError, match="duplicate attribute declaration existing"):
        doc.attributes({"new": XsdType.STRING, "existing": XsdType.STRING})
    assert [value.name.local_name for value in doc.build().attribute_declarations] == [
        "existing"
    ]


def test_boundary_relation_and_anchor_helpers_are_byte_identical() -> None:
    """Anchored relation notation lowers exactly like direct kernel construction."""
    ns = "urn:boundary-builder"

    def q(local: str) -> QualifiedName:
        return QualifiedName(ns, local)

    ergonomic = document(ns, prefix="b")
    left = ergonomic.tier(
        "left", ("left-0", "left-1"), item_type="thing", membership="left-members"
    )
    right = ergonomic.tier(
        "right", ("right-0",), item_type="thing", membership="right-members"
    )
    assert left.start() == DurableBoundaryRef(left.name, BoundarySide.BEFORE)
    assert left.end() == DurableBoundaryRef(left.name, BoundarySide.AFTER)
    assert left.before(1) == DurableBoundaryRef(
        DurableItemRef("left-1"), BoundarySide.BEFORE
    )
    assert left.after(0) == DurableBoundaryRef(
        DurableItemRef("left-0"), BoundarySide.AFTER
    )
    ergonomic.relation(
        "aligned-boundaries",
        left,
        right,
        ((left.start(), right.end()), (left.after(0), right.before(0))),
        left_endpoint=RelationEndpointKind.BOUNDARY,
        right_endpoint=RelationEndpointKind.BOUNDARY,
        source_type="thing",
        target_type="thing",
        acyclic=True,
    )

    direct = document(ns, prefix="b")
    direct.tier(
        "left", ("left-0", "left-1"), item_type="thing", membership="left-members"
    )
    direct.tier("right", ("right-0",), item_type="thing", membership="right-members")
    direct.declare(
        BipartiteRelationDeclaration(
            q("aligned-boundaries"),
            q("thing"),
            q("thing"),
            RelationEndpointKind.BOUNDARY,
            RelationEndpointKind.BOUNDARY,
            acyclic=True,
        )
    )
    direct.add(
        RelationInstance(
            q("aligned-boundaries"),
            DurableBoundaryRef(q("left"), BoundarySide.BEFORE),
            DurableBoundaryRef(q("right"), BoundarySide.AFTER),
        )
    )
    direct.add(
        RelationInstance(
            q("aligned-boundaries"),
            DurableBoundaryRef(DurableItemRef("left-0"), BoundarySide.AFTER),
            DurableBoundaryRef(DurableItemRef("right-0"), BoundarySide.BEFORE),
        )
    )
    assert ergonomic.build() == direct.build()
    assert dump_bytes(ergonomic.build()) == dump_bytes(direct.build())


def test_boundary_relation_refusals() -> None:
    """Boundary relations require explicit types and owned durable anchors."""
    doc = document("urn:boundary-errors", prefix="b")
    left = doc.tier("left", ("left-0",), item_type="thing", membership="left-members")
    right = doc.tier(
        "right", ("right-0",), item_type="thing", membership="right-members"
    )
    with pytest.raises(BuilderError, match="needs explicit source_type"):
        doc.relation(
            "missing-type",
            left,
            right,
            (),
            left_endpoint=RelationEndpointKind.BOUNDARY,
            right_endpoint=RelationEndpointKind.ITEM,
        )
    with pytest.raises(BuilderError, match="refuses numeric coordinate"):
        doc.relation(
            "numeric",
            left,
            right,
            ((0, right.ref(0)),),
            left_endpoint=RelationEndpointKind.BOUNDARY,
            right_endpoint=RelationEndpointKind.ITEM,
            source_type="thing",
        )
    with pytest.raises(BuilderError, match="foreign boundary tier anchor"):
        doc.relation(
            "foreign-tier",
            left,
            right,
            ((right.start(), right.ref(0)),),
            left_endpoint=RelationEndpointKind.BOUNDARY,
            right_endpoint=RelationEndpointKind.ITEM,
            source_type="thing",
        )
    with pytest.raises(BuilderError, match="foreign or missing boundary item anchor"):
        doc.relation(
            "foreign-item",
            left,
            right,
            ((right.before(0), right.ref(0)),),
            left_endpoint=RelationEndpointKind.BOUNDARY,
            right_endpoint=RelationEndpointKind.ITEM,
            source_type="thing",
        )
    with pytest.raises(BuilderError, match="needs DurableBoundaryRef"):
        doc.relation(
            "malformed",
            left,
            right,
            ((object(), right.ref(0)),),
            left_endpoint=RelationEndpointKind.BOUNDARY,
            right_endpoint=RelationEndpointKind.ITEM,
            source_type="thing",
        )
    no_id = doc.tier("no-id", (None,))
    with pytest.raises(BuilderError, match="item 0 has no durable id"):
        no_id.before(0)

    malformed_anchor = object.__new__(DurableBoundaryRef)
    object.__setattr__(malformed_anchor, "anchor", object())
    object.__setattr__(malformed_anchor, "side", BoundarySide.BEFORE)
    with pytest.raises(BuilderError, match="malformed boundary anchor"):
        doc.relation(
            "malformed-anchor",
            left,
            right,
            ((malformed_anchor, right.ref(0)),),
            left_endpoint=RelationEndpointKind.BOUNDARY,
            right_endpoint=RelationEndpointKind.ITEM,
            source_type="thing",
        )

    malformed_side = object.__new__(DurableBoundaryRef)
    object.__setattr__(malformed_side, "anchor", left.name)
    object.__setattr__(malformed_side, "side", "before")
    with pytest.raises(BuilderError, match="malformed boundary side"):
        doc.relation(
            "malformed-side",
            left,
            right,
            ((malformed_side, right.ref(0)),),
            left_endpoint=RelationEndpointKind.BOUNDARY,
            right_endpoint=RelationEndpointKind.ITEM,
            source_type="thing",
        )

    missing_anchor = object.__new__(DurableBoundaryRef)
    object.__setattr__(missing_anchor, "side", BoundarySide.BEFORE)
    with pytest.raises(
        BuilderError, match="relation missing-anchor source: malformed boundary anchor"
    ):
        doc.relation(
            "missing-anchor",
            left,
            right,
            ((missing_anchor, right.ref(0)),),
            left_endpoint=RelationEndpointKind.BOUNDARY,
            right_endpoint=RelationEndpointKind.ITEM,
            source_type="thing",
        )

    missing_side = object.__new__(DurableBoundaryRef)
    object.__setattr__(missing_side, "anchor", left.name)
    with pytest.raises(
        BuilderError, match="relation missing-side source: malformed boundary side"
    ):
        doc.relation(
            "missing-side",
            left,
            right,
            ((missing_side, right.ref(0)),),
            left_endpoint=RelationEndpointKind.BOUNDARY,
            right_endpoint=RelationEndpointKind.ITEM,
            source_type="thing",
        )

    missing_durable_id = object.__new__(DurableItemRef)
    missing_nested_slot = object.__new__(DurableBoundaryRef)
    object.__setattr__(missing_nested_slot, "anchor", missing_durable_id)
    object.__setattr__(missing_nested_slot, "side", BoundarySide.BEFORE)
    with pytest.raises(
        BuilderError,
        match="relation missing-durable-id source: malformed boundary anchor",
    ):
        doc.relation(
            "missing-durable-id",
            left,
            right,
            ((missing_nested_slot, right.ref(0)),),
            left_endpoint=RelationEndpointKind.BOUNDARY,
            right_endpoint=RelationEndpointKind.ITEM,
            source_type="thing",
        )


def test_relation_surface_shape_refusals_and_item_endpoint() -> None:
    """The general relation surface checks kinds, pair order, and pair arity."""
    doc = document("urn:relation-errors", prefix="r")
    left = doc.tier("left", ("left-0",), item_type="thing", membership="left-members")
    right = doc.tier(
        "right", ("right-0",), item_type="thing", membership="right-members"
    )
    doc.relation(
        "items",
        left,
        right,
        ((0, right.ref(0)),),
        left_endpoint=RelationEndpointKind.ITEM,
        right_endpoint=RelationEndpointKind.ITEM,
    )
    with pytest.raises(BuilderError, match="invalid endpoint kind"):
        doc.relation(
            "kind",
            left,
            right,
            (),
            left_endpoint="bad",
            right_endpoint=RelationEndpointKind.ITEM,
        )
    with pytest.raises(BuilderError, match="ordered iterable"):
        doc.relation(
            "set",
            left,
            right,
            {(0, 0)},
            left_endpoint=RelationEndpointKind.ITEM,
            right_endpoint=RelationEndpointKind.ITEM,
        )
    with pytest.raises(BuilderError, match="pairs must be iterable"):
        doc.relation(
            "noniterable",
            left,
            right,
            cast(object, 1),  # type: ignore[arg-type]
            left_endpoint=RelationEndpointKind.ITEM,
            right_endpoint=RelationEndpointKind.ITEM,
        )
    with pytest.raises(BuilderError, match="expected two endpoints"):
        doc.relation(
            "arity",
            left,
            right,
            ((0,),),  # type: ignore[arg-type]
            left_endpoint=RelationEndpointKind.ITEM,
            right_endpoint=RelationEndpointKind.ITEM,
        )


def test_required_refusals_and_empty_pairs() -> None:
    """Ambiguous identity, invalid scalar notation, and ownership fail early."""
    doc = document("urn:test", prefix="t")
    with pytest.raises(BuilderError, match="needs both item_type and membership"):
        doc.tier("bad", item_type="thing")
    raw = doc.tier("raw", ("one",))
    typed = doc.tier("typed", ("two",), item_type="thing", membership="members")
    with pytest.raises(BuilderError, match="source.*untyped.*source_type"):
        doc.link("bad-untyped", raw, typed)
    with pytest.raises(BuilderError, match="contradicts.*thing"):
        doc.link("bad-type", typed, typed, source_type="other")
    doc.attribute("amount", XsdType.DECIMAL)
    doc.attribute("count", XsdType.INTEGER)
    with pytest.raises(BuilderError, match="float for decimal"):
        doc.tier("float", (item(attrs={"amount": 1.5}),))
    with pytest.raises(BuilderError, match="bool for integer"):
        doc.tier("bool", (item(attrs={"count": True}),))
    foreign = document("urn:test", prefix="other").tier("foreign")
    with pytest.raises(BuilderError, match="foreign tier handle"):
        doc.link("foreign-link", foreign, typed, source_type="thing")
    with pytest.raises(BuilderError, match="index 1 out of range"):
        typed.ref(1)
    boundary = DurableBoundaryRef(DurableItemRef("two"), BoundarySide.BEFORE)
    with pytest.raises(BuilderError, match="boundary endpoint refuses integer"):
        doc.link(
            "boundary",
            typed,
            typed,
            ((0, boundary),),
            source_type="thing",
            target_type="thing",
            left_endpoint=RelationEndpointKind.BOUNDARY,
            right_endpoint=RelationEndpointKind.BOUNDARY,
        )
    doc.link("empty", typed, typed)
    doc.link("explicit-match", typed, typed, source_type="thing", target_type="thing")
    with pytest.raises(BuilderError, match="index -1 out of range"):
        typed.ref(-1)
    graph = doc.build()
    assert any(
        declaration.name.local_name == "empty"
        for declaration in graph.relation_declarations
    )
    assert not any(
        relation.declaration.local_name == "empty" for relation in graph.relations
    )


def test_literal_colons_and_foreign_qualified_names() -> None:
    """Strings stay local and foreign namespaces require expanded names."""
    doc = Document("urn:local", prefix="l")
    doc.namespace("urn:foreign", prefix="f")
    literal = doc.tier("tei:word")
    foreign = doc.tier(QualifiedName("urn:foreign", "word"))
    assert literal.name == QualifiedName("urn:local", "tei:word")
    assert foreign.name == QualifiedName("urn:foreign", "word")


def test_scalar_lowering_and_item_notation() -> None:
    """Every admitted scalar form lowers narrowly and unsupported notation fails."""
    doc = document("urn:values", prefix="v")
    declarations = (
        ("string", XsdType.STRING),
        ("boolean", XsdType.BOOLEAN),
        ("integer", XsdType.INTEGER),
        ("decimal", XsdType.DECIMAL),
        ("double", XsdType.DOUBLE),
    )
    for name, value_type in declarations:
        doc.attribute(name, value_type)
    doc.tier(
        "values",
        (
            Item("kernel"),
            None,
            item(
                attrs={
                    "string": "s",
                    "boolean": "1",
                    "integer": "2",
                    "decimal": "3.0",
                    "double": "4e0",
                }
            ),
            item(attrs={"boolean": False, "integer": 5, "decimal": 6, "double": 7}),
            item(attrs={"decimal": Decimal("8.0"), "double": 9.0}),
        ),
    )
    assert len(doc.build().tiers[0].items) == 5
    refusals = (
        ("string", 1),
        ("boolean", 1),
        ("integer", 1.0),
        ("decimal", True),
        ("double", True),
    )
    for index, (name, value) in enumerate(refusals):
        with pytest.raises(BuilderError, match=f"attribute {name} rejects"):
            doc.tier(f"reject-{index}", (item(attrs={name: value}),))
    with pytest.raises(BuilderError, match="exactly one declaration"):
        doc.tier("undeclared-value", (item(attrs={"missing": "x"}),))
    with pytest.raises(BuilderError, match="expected Item"):
        doc.tier("mapping", ({"durable_id": "no"},))  # type: ignore[arg-type]
    with pytest.raises(BuilderError, match="items must be iterable"):
        doc.tier("noniterable", cast(object, 1))  # type: ignore[arg-type]
    with pytest.raises(BuilderError, match="expected str or QualifiedName"):
        doc.tier(cast(str, 1))
    with pytest.raises(BuilderError, match="attribute bad"):
        doc.attribute("bad", "unsupported")


def test_link_endpoint_notation_errors() -> None:
    """Tier lookup, pair shapes, ownership, and endpoint kinds fail at the DSL edge."""
    doc = document("urn:links", prefix="l")
    one = doc.tier("one", ("a",), item_type="thing", membership="one-members")
    two = doc.tier("two", ("b",), item_type="thing", membership="two-members")
    with pytest.raises(BuilderError, match="tier missing is not declared"):
        doc.link("unknown", "missing", two)
    with pytest.raises(BuilderError, match="pairs must be iterable"):
        doc.link("not-pairs", one, two, cast(object, 1))  # type: ignore[arg-type]
    with pytest.raises(BuilderError, match="expected two endpoints"):
        doc.link("bad-pair", one, two, ((0,),))  # type: ignore[arg-type]
    with pytest.raises(BuilderError, match="belongs to tier two"):
        doc.link("wrong-tier", one, two, ((two.ref(0), 0),))
    with pytest.raises(BuilderError, match="item endpoint needs"):
        doc.link("bare-string", one, two, (("a", 0),))
    with pytest.raises(BuilderError, match="is not unique"):
        doc.link("missing-id", one, two, ((DurableItemRef("missing"), 0),))
    with pytest.raises(BuilderError, match="needs explicit source_type"):
        doc.link(
            "boundary-type",
            one,
            two,
            left_endpoint=RelationEndpointKind.BOUNDARY,
        )
    boundary = DurableBoundaryRef(DurableItemRef("a"), BoundarySide.BEFORE)
    with pytest.raises(BuilderError, match="needs DurableBoundaryRef"):
        doc.link(
            "bad-boundary",
            one,
            two,
            ((one.ref(0), 0),),
            source_type="thing",
            left_endpoint=RelationEndpointKind.BOUNDARY,
        )
    doc.link(
        "boundary-ok",
        one,
        two,
        ((boundary, 0),),
        source_type="thing",
        left_endpoint=RelationEndpointKind.BOUNDARY,
    )
    assert doc.build().relations[-1].left == boundary


def test_escape_hatches_and_attribute_attachments() -> None:
    """Kernel values pass through and every ordinary attribute domain is writable."""
    ns = "urn:escape"

    def q(name: str) -> QualifiedName:
        return QualifiedName(ns, name)

    doc = document(ns, prefix="e")
    tier = doc.tier("items", ("a",), item_type="thing", membership="members")
    doc.attribute("doc-value", XsdType.STRING, domain=AttributeDomain.DOCUMENT)
    doc.attribute("tier-value", XsdType.STRING, domain=AttributeDomain.TIER)
    doc.attribute("item-value", XsdType.STRING)
    doc.attribute("item-value-2", XsdType.STRING)
    doc.attribute(
        "declaration-value", XsdType.STRING, domain=AttributeDomain.RELATION_DECLARATION
    )
    doc.attribute(
        "instance-value", XsdType.STRING, domain=AttributeDomain.RELATION_INSTANCE
    )
    doc.attribute("position-value", XsdType.STRING, domain=AttributeDomain.BOUNDARY)
    doc.attribute("position-value-2", XsdType.STRING, domain=AttributeDomain.BOUNDARY)
    doc.link("self", tier, "items", ((0, 0),))
    doc.attach(AttributeDomain.DOCUMENT, None, {"doc-value": "d"})
    doc.attach(AttributeDomain.TIER, q("items"), {"tier-value": "t"})
    doc.attach(AttributeDomain.ITEM, tier.ref(0), {"item-value": "i"})
    doc.attach(AttributeDomain.ITEM, DurableItemRef("a"), {"item-value-2": "j"})
    doc.attach(
        AttributeDomain.RELATION_DECLARATION,
        q("self"),
        {"declaration-value": "r"},
    )
    doc.attach(AttributeDomain.RELATION_INSTANCE, 0, {"instance-value": "x"})
    boundary = DurableBoundaryRef(DurableItemRef("a"), BoundarySide.BEFORE)
    doc.attach(AttributeDomain.BOUNDARY, boundary, {"position-value": "p"})
    doc.attach(AttributeDomain.BOUNDARY, boundary, {"position-value-2": "q"})
    graph = doc.build()
    assert graph.attributes[0].lexical == "d"
    assert graph.boundary_values[0].attributes[0].lexical == "p"

    direct_relation = RelationInstance(q("self"), tier.ref(0), tier.ref(0))
    extra = document(ns, prefix="e")
    extra.tier("items", ("a",), item_type="thing", membership="members")
    extra.attribute("position-value", XsdType.STRING, domain=AttributeDomain.BOUNDARY)
    extra.declare(BipartiteRelationDeclaration(q("self"), q("thing"), q("thing")))
    side = RelationSideDeclaration((RelationEndpointKind.ITEM,), (q("items"),))
    extra.declare(PolyadicRelationDeclaration(q("poly"), side, side))
    polyadic = PolyadicRelationInstance(q("poly"), (tier.ref(0),), (tier.ref(0),))
    extra.relate(polyadic)
    extra.relate(direct_relation)
    extra.add(direct_relation)
    extra.add(
        Boundary(
            boundary,
            (AttributeValue(q("position-value"), XsdType.STRING, "p"),),
        )
    )
    built_extra = extra.build()
    assert len(built_extra.relations) == 2
    assert built_extra.polyadic_relations == (polyadic,)

    boundary_branches = document(ns, prefix="e")
    boundary_branches.attribute(
        "position-value", XsdType.STRING, domain=AttributeDomain.BOUNDARY
    )
    boundary_branches.attach(
        AttributeDomain.BOUNDARY, boundary, {"position-value": "p"}
    )
    boundary_branches.attach(
        AttributeDomain.BOUNDARY,
        DurableBoundaryRef(DurableItemRef("other"), BoundarySide.BEFORE),
        {"position-value": "q"},
    )
    boundary_branches.attach(AttributeDomain.DOCUMENT, None, {})

    with pytest.raises(BuilderError, match="declare: expected"):
        doc.declare(cast(RelationDeclaration, object()))
    with pytest.raises(BuilderError, match="relate: expected"):
        doc.relate(cast(RelationInstance, object()))
    with pytest.raises(BuilderError, match="attach: 'bad'"):
        doc.attach("bad", None, {})


@pytest.mark.parametrize(
    ("domain", "target", "message"),
    [
        (
            AttributeDomain.DOCUMENT,
            QualifiedName("urn:attach", "x"),
            "target must be None",
        ),
        (AttributeDomain.TIER, None, "target must be QualifiedName"),
        (
            AttributeDomain.TIER,
            QualifiedName("urn:attach", "missing"),
            "is not declared",
        ),
        (AttributeDomain.ITEM, None, "target must be ItemRef or DurableItemRef"),
        (AttributeDomain.ITEM, DurableItemRef("missing"), "is not unique"),
        (
            AttributeDomain.ITEM,
            ItemRef(QualifiedName("urn:attach", "items"), 2),
            "out of range",
        ),
        (
            AttributeDomain.RELATION_DECLARATION,
            QualifiedName("urn:attach", "missing"),
            "not uniquely declared",
        ),
        (AttributeDomain.RELATION_INSTANCE, 2, "out of range"),
        (AttributeDomain.BOUNDARY, None, "boundary reference"),
    ],
)
def test_attachment_target_refusals(
    domain: AttributeDomain, target: object, message: str
) -> None:
    """Domain-specific attachment targets are checked before construction."""
    doc = document("urn:attach", prefix="a")
    doc.attribute("value", XsdType.STRING, domain=domain)
    doc.tier("items", ("a",), item_type="thing", membership="members")
    with pytest.raises(BuilderError, match=message):
        doc.attach(domain, target, {"value": "x"})  # type: ignore[arg-type]


def test_negative_item_references_are_refused_everywhere() -> None:
    """Negative structural indexes never reach Python's sequence indexing."""
    doc = document("urn:negative", prefix="n")
    doc.attribute("value", XsdType.STRING)
    tier = doc.tier("items", ("first", "last"), item_type="thing", membership="members")
    negative = ItemRef(tier.name, -1)
    with pytest.raises(BuilderError, match="attach item: index -1 out of range"):
        doc.attach(AttributeDomain.ITEM, negative, {"value": "wrong"})
    with pytest.raises(BuilderError, match="tier ref items: index -1 out of range"):
        doc.link("negative-link", tier, tier, ((negative, 0),))
    with pytest.raises(BuilderError, match="tier ref items: invalid index True"):
        tier.ref(True)


def test_non_mapping_attribute_inputs_are_refused() -> None:
    """Every attribute entry point labels non-mapping notation clearly."""
    with pytest.raises(BuilderError, match="item attrs: expected a mapping"):
        item(attrs=42)  # type: ignore[arg-type]

    doc = document("urn:mapping", prefix="m")
    doc.attribute("value", XsdType.STRING, domain=AttributeDomain.DOCUMENT)
    with pytest.raises(BuilderError, match="tier bad-tier: expected a mapping"):
        doc.tier("bad-tier", attributes=42)  # type: ignore[arg-type]
    left = doc.tier("left", item_type="thing", membership="left-members")
    right = doc.tier("right", item_type="thing", membership="right-members")
    with pytest.raises(BuilderError, match="link bad-link: expected a mapping"):
        doc.link("bad-link", left, right, attributes=42)  # type: ignore[arg-type]
    with pytest.raises(BuilderError, match="attach document: expected a mapping"):
        doc.attach(AttributeDomain.DOCUMENT, None, 42)  # type: ignore[arg-type]


def test_endpoint_kinds_are_normalized_or_refused_at_link() -> None:
    """String enum values work while invalid endpoint kinds fail immediately."""
    doc = document("urn:kinds", prefix="k")
    tier = doc.tier("items", ("one",), item_type="thing", membership="members")
    doc.link("string-kind", tier, tier, ((0, 0),), left_endpoint="item")
    with pytest.raises(BuilderError, match="link bad-kind: invalid endpoint kind"):
        doc.link("bad-kind", tier, tier, ((0, 0),), left_endpoint="bad")
    doc.declare(
        SimpleRelationDeclaration(
            doc.qname("other-members"), tier.name, doc.qname("other-thing")
        )
    )
    with pytest.raises(BuilderError, match="tier items is ambiguous"):
        doc.link("ambiguous", tier, tier)


def test_unordered_tier_items_and_link_pairs_are_refused() -> None:
    """Sets and mappings cannot choose order-sensitive graph collections."""
    doc = document("urn:ordered", prefix="o")
    with pytest.raises(
        BuilderError, match="tier set-items: items must be an ordered iterable"
    ):
        doc.tier("set-items", {"a", "b"})
    with pytest.raises(
        BuilderError, match="tier mapping-items: items must be an ordered iterable"
    ):
        doc.tier("mapping-items", {"a": None})
    left = doc.tier("left", ("a", "b"), item_type="thing", membership="left-members")
    right = doc.tier("right", ("a", "b"), item_type="thing", membership="right-members")
    with pytest.raises(
        BuilderError, match="link set-pairs: pairs must be an ordered iterable"
    ):
        doc.link("set-pairs", left, right, {(0, 0), (1, 1)})
    with pytest.raises(
        BuilderError, match="link mapping-pairs: pairs must be an ordered iterable"
    ):
        doc.link("mapping-pairs", left, right, {(0, 0): None})
