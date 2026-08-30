"""A rewrite's effect on what it rewrote is declared, checked, and refutable."""

from __future__ import annotations

import pytest

from tiergraph import (
    AddItem,
    AttachValue,
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    Boundary,
    BoundaryRef,
    BoundarySide,
    DeclareAttribute,
    DeclareNamespace,
    DeclareRelation,
    DeclareTier,
    DurableBoundaryRef,
    EffectRefusal,
    Graph,
    Item,
    ItemRef,
    NamespaceDeclaration,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    PromoteBoundary,
    PromoteItem,
    QualifiedName,
    Relate,
    RelationEndpointKind,
    RelationInstance,
    RelationSideDeclaration,
    RewriteDeclaration,
    RewriteEffect,
    SimpleRelationDeclaration,
    Tier,
    TierDeclaration,
    XsdType,
)

NS = "urn:rewrite"
NAMESPACES = (NamespaceDeclaration("r", NS),)


def name(local: str) -> QualifiedName:
    """Return an expanded test name."""
    return QualifiedName(NS, local)


WORD = name("word")
PHRASE = name("phrase")
WORD_TYPE = name("W")
PHRASE_TYPE = name("P")
LABEL = name("label")
EDGE = name("edge")
TIER_NOTE = name("note")

DECLARATIONS = (
    SimpleRelationDeclaration(name("wordtype"), WORD, WORD_TYPE),
    SimpleRelationDeclaration(name("phrasetype"), PHRASE, PHRASE_TYPE),
    BipartiteRelationDeclaration(name("covers"), PHRASE_TYPE, WORD_TYPE),
)
ATTRIBUTES = (
    AttributeDeclaration(LABEL, AttributeDomain.ITEM, XsdType.STRING),
    AttributeDeclaration(EDGE, AttributeDomain.BOUNDARY, XsdType.STRING),
    AttributeDeclaration(TIER_NOTE, AttributeDomain.TIER, XsdType.STRING),
)


def labeled(text: str) -> Item:
    """Return an item carrying one distinguishing label."""
    return Item(attributes=(AttributeValue(LABEL, XsdType.STRING, text),))


def graph(
    words: tuple[Item, ...] = (),
    phrases: tuple[Item, ...] = (),
    relations: tuple[RelationInstance, ...] = (),
    boundaries: tuple[Boundary, ...] = (),
    tier_attributes: tuple[AttributeValue, ...] = (),
    word_long_name: str = "Word",
) -> Graph:
    """Build a graph over the shared declarations."""
    return Graph(
        NAMESPACES,
        (
            Tier(TierDeclaration(WORD, word_long_name), words, tier_attributes),
            Tier(TierDeclaration(PHRASE, "Phrase"), phrases),
        ),
        DECLARATIONS,
        relations,
        ATTRIBUTES,
        boundaries,
    )


BASE = graph((labeled("a"), labeled("b")), (Item(),))


# --- omitting the claim -----------------------------------------------------


def test_undeclared_effect_is_refused_with_the_declaration_to_be_made() -> None:
    """Declining to say is answered with the declaration, not with a default."""
    with pytest.raises(EffectRefusal) as refusal:
        RewriteDeclaration("silent", BASE, BASE).check_effect()
    message = str(refusal.value)
    assert message.startswith("rewrite 'silent' effect is UNDECLARED")
    assert "DECORATE" in message
    assert "REVISE" in message
    assert "COLLAPSE" in message
    assert message.endswith("Not declaring is not the same as declaring COLLAPSE.")


def test_an_unnamed_claim_has_nothing_to_report_against() -> None:
    """A refusal names the rewrite, so a rewrite must have a name."""
    with pytest.raises(ValueError, match="rewrite name '' must not be empty"):
        RewriteDeclaration("", BASE, BASE)


# --- decoration holds -------------------------------------------------------


def test_adding_a_tier_over_existing_items_decorates() -> None:
    """A covering tier and its relations disturb nothing they cover."""
    result = graph(
        BASE.tiers[0].items,
        (Item(),),
        (
            RelationInstance(name("covers"), ItemRef(PHRASE, 0), ItemRef(WORD, 0)),
            RelationInstance(name("covers"), ItemRef(PHRASE, 0), ItemRef(WORD, 1)),
        ),
    )
    certificate = RewriteDeclaration(
        "cover", BASE, result, RewriteEffect.DECORATE
    ).check_effect()
    assert certificate.effect is RewriteEffect.DECORATE
    assert certificate.disturbances == 0
    # 1 namespace, 2 tiers, 3 items, 3 relation declarations, 3 attribute
    # declarations, and the document.
    assert certificate.subjects == 13


def test_attaching_a_value_to_an_existing_item_decorates() -> None:
    """Annotating an item is the paradigm decoration and is measured as one."""
    result = AttachValue(
        AttributeDomain.TIER, WORD, AttributeValue(TIER_NOTE, XsdType.STRING, "x")
    ).apply(BASE)
    assert (
        RewriteDeclaration("annotate", BASE, result, RewriteEffect.DECORATE)
        .check_effect()
        .disturbances
        == 0
    )


def test_promoting_an_item_to_durable_identity_decorates() -> None:
    """An identity where there was none is added, so nothing is taken back."""
    result = PromoteItem(ItemRef(WORD, 1), "w1").apply(BASE)
    assert (
        RewriteDeclaration("promote", BASE, result, RewriteEffect.DECORATE)
        .check_effect()
        .disturbances
        == 0
    )


def test_every_build_machine_opcode_decorates() -> None:
    """The machine's whole executable rewrite surface adds and takes nothing back.

    This records a measured fact about the tree rather than a rule the kernel
    enforces: no opcode removes a structure or puts a value in place of
    another. It is the characterization that would break first if one were
    added.
    """
    source = graph(
        (labeled("a"), labeled("b")),
        (Item(),),
        (RelationInstance(name("covers"), ItemRef(PHRASE, 0), ItemRef(WORD, 0)),),
    )
    opcodes = (
        DeclareNamespace(NamespaceDeclaration("s", "urn:second")),
        DeclareTier(TierDeclaration(name("syllable"), "Syllable")),
        DeclareRelation(
            BipartiteRelationDeclaration(name("also"), PHRASE_TYPE, WORD_TYPE)
        ),
        DeclareAttribute(
            AttributeDeclaration(name("extra"), AttributeDomain.ITEM, XsdType.STRING)
        ),
        AddItem(WORD, labeled("c")),
        PromoteItem(ItemRef(WORD, 0), "w0"),
        PromoteBoundary(BoundaryRef(WORD, 1), "p1"),
        PromoteBoundary(BoundaryRef(WORD, 0), "p0"),
        Relate(RelationInstance(name("covers"), ItemRef(PHRASE, 0), ItemRef(WORD, 1))),
        AttachValue(
            AttributeDomain.TIER, WORD, AttributeValue(TIER_NOTE, XsdType.STRING, "n")
        ),
        AttachValue(
            AttributeDomain.BOUNDARY,
            BoundaryRef(WORD, 1),
            AttributeValue(EDGE, XsdType.STRING, "e"),
        ),
    )
    for opcode in opcodes:
        RewriteDeclaration(
            type(opcode).__name__, source, opcode.apply(source), RewriteEffect.DECORATE
        ).check_effect()


# --- the case decoration must exclude ---------------------------------------


def test_appending_an_item_collapses_a_value_at_the_tier_outer_boundary() -> None:
    """Growing a tier moves its last edge, and a value standing there is left behind.

    ``AddItem`` decorates against most graphs and does not decorate against
    this one. The effect belongs to the pair of graphs and not to the
    operation, which is why it is declared per rewrite instead of asserted of
    the kernel.
    """
    source = graph(
        (labeled("a"), labeled("b")),
        (),
        (),
        (
            Boundary(
                DurableBoundaryRef(WORD, BoundarySide.AFTER),
                (AttributeValue(EDGE, XsdType.STRING, "final"),),
            ),
        ),
    )
    result = AddItem(WORD, labeled("c")).apply(source)

    # The counterexample is verified to be one: the source really does carry a
    # value at this coordinate and the result really does not.
    coordinate = BoundaryRef(WORD, 2)
    assert source.resolve_boundary(DurableBoundaryRef(WORD, BoundarySide.AFTER)) == (
        coordinate
    )
    assert source.boundaries(WORD)[2].attributes != ()
    assert result.boundaries(WORD)[2].attributes == ()

    claim = RewriteDeclaration("append", source, result, RewriteEffect.DECORATE)
    with pytest.raises(EffectRefusal) as refusal:
        claim.check_effect()
    assert "boundary '{urn:rewrite}word'[2] has no counterpart" in str(refusal.value)
    assert "Declare COLLAPSE." in str(refusal.value)
    assert [item.effect for item in claim.disturbances()] == [RewriteEffect.COLLAPSE]


# --- asserting the claim falsely --------------------------------------------


def test_false_decoration_over_a_replaced_value_names_the_value() -> None:
    """A value standing where another stood is reported with both values."""
    result = graph((labeled("a"), labeled("changed")), (Item(),))
    with pytest.raises(EffectRefusal) as refusal:
        RewriteDeclaration(
            "relabel", BASE, result, RewriteEffect.DECORATE
        ).check_effect()
    message = str(refusal.value)
    assert "item '{urn:rewrite}word'[1] carries attribute" in message
    assert "'string:changed' where the source carried 'string:b'" in message
    assert "Declare REVISE." in message


def test_false_decoration_over_a_missing_structure_names_the_structure() -> None:
    """A structure with no counterpart is reported as having none."""
    result = graph((labeled("a"),), (Item(),))
    with pytest.raises(EffectRefusal) as refusal:
        RewriteDeclaration(
            "shrink", BASE, result, RewriteEffect.DECORATE
        ).check_effect()
    message = str(refusal.value)
    assert "item '{urn:rewrite}word'[1] has no counterpart in the result" in message
    assert "Declare COLLAPSE." in message


def test_declaring_revise_over_a_collapse_is_refused() -> None:
    """A revision leaves every structure standing, so a loss is not one."""
    result = graph((labeled("a"),), (Item(),))
    with pytest.raises(EffectRefusal) as refusal:
        RewriteDeclaration("shrink", BASE, result, RewriteEffect.REVISE).check_effect()
    message = str(refusal.value)
    assert "A rewrite that revises leaves every structure standing" in message
    assert "Declare COLLAPSE." in message


def test_declaring_collapse_over_a_revision_is_refused() -> None:
    """A collapse leaves something with no counterpart, and here nothing does.

    Branch coverage cannot see this case on its own: it shares a code path with
    the opposite mispairing, so only the wording tells them apart.
    """
    result = graph((labeled("a"), labeled("changed")), (Item(),))
    with pytest.raises(EffectRefusal) as refusal:
        RewriteDeclaration(
            "relabel", BASE, result, RewriteEffect.COLLAPSE
        ).check_effect()
    message = str(refusal.value)
    assert (
        "A rewrite that collapses leaves some structure of the source with no "
        "counterpart, and every one of them still stands here." in message
    )
    assert "Declare REVISE." in message


def test_each_mispaired_claim_is_contradicted_on_its_own_terms() -> None:
    """Every refusable pairing argues against the declared effect, not a neighbor."""
    revised = graph((labeled("a"), labeled("changed")), (Item(),))
    collapsed = graph((labeled("a"),), (Item(),))
    cases = (
        (RewriteEffect.DECORATE, revised, "so a value standing where another"),
        (RewriteEffect.DECORATE, collapsed, "so a structure with no counterpart"),
        (RewriteEffect.REVISE, collapsed, "A rewrite that revises leaves every"),
        (RewriteEffect.COLLAPSE, revised, "A rewrite that collapses leaves some"),
    )
    for declared, result, expected in cases:
        with pytest.raises(EffectRefusal) as refusal:
            RewriteDeclaration("pair", BASE, result, declared).check_effect()
        assert expected in str(refusal.value)


def test_a_revision_that_cannot_be_exhibited_is_refused() -> None:
    """Claiming the weaker thing over a decoration is a declaration that is hiding."""
    result = AttachValue(
        AttributeDomain.TIER, WORD, AttributeValue(TIER_NOTE, XsdType.STRING, "x")
    ).apply(BASE)
    with pytest.raises(EffectRefusal) as refusal:
        RewriteDeclaration("hiding", BASE, result, RewriteEffect.REVISE).check_effect()
    message = str(refusal.value)
    assert "so nothing was replaced" in message
    assert "A revision you cannot exhibit" in message
    assert "Declare DECORATE." in message


def test_a_collapse_that_cannot_be_exhibited_is_refused() -> None:
    """The same gate bites for the weakest claim in the family."""
    with pytest.raises(EffectRefusal) as refusal:
        RewriteDeclaration("hiding", BASE, BASE, RewriteEffect.COLLAPSE).check_effect()
    message = str(refusal.value)
    assert "so nothing was lost" in message
    assert "A collapse you cannot exhibit" in message
    assert "Declare DECORATE." in message


def test_an_unchanged_graph_decorates_itself() -> None:
    """Rewriting nothing takes nothing back, so it is the trivial decoration."""
    assert (
        RewriteDeclaration("identity", BASE, BASE, RewriteEffect.DECORATE)
        .check_effect()
        .disturbances
        == 0
    )


# --- every kind of structure is examined ------------------------------------


def test_a_replaced_namespace_uri_revises() -> None:
    """A binding is a structure, and rebinding a prefix replaces its value."""
    result = Graph(
        (NamespaceDeclaration("r", NS), NamespaceDeclaration("q", "urn:other")),
        BASE.tiers,
        DECLARATIONS,
        (),
        ATTRIBUTES,
    )
    rebound = Graph(
        (NamespaceDeclaration("r", NS), NamespaceDeclaration("q", "urn:third")),
        BASE.tiers,
        DECLARATIONS,
        (),
        ATTRIBUTES,
    )
    with pytest.raises(EffectRefusal) as refusal:
        RewriteDeclaration(
            "rebind", result, rebound, RewriteEffect.DECORATE
        ).check_effect()
    assert "namespace prefix 'q' carries namespace URI 'urn:third'" in str(
        refusal.value
    )


def test_a_renamed_tier_revises() -> None:
    """A tier's long name is carried content, so replacing it is a revision."""
    result = graph(BASE.tiers[0].items, (Item(),), word_long_name="Wordform")
    with pytest.raises(EffectRefusal) as refusal:
        RewriteDeclaration(
            "rename", BASE, result, RewriteEffect.DECORATE
        ).check_effect()
    assert "tier '{urn:rewrite}word' carries long name 'Wordform'" in str(refusal.value)


def test_a_dropped_relation_instance_collapses() -> None:
    """A relation is a fact, and a fact that is gone is gone."""
    source = graph(
        BASE.tiers[0].items,
        (Item(),),
        (
            RelationInstance(name("covers"), ItemRef(PHRASE, 0), ItemRef(WORD, 0)),
            RelationInstance(name("covers"), ItemRef(PHRASE, 0), ItemRef(WORD, 1)),
        ),
    )
    result = graph(
        BASE.tiers[0].items,
        (Item(),),
        (RelationInstance(name("covers"), ItemRef(PHRASE, 0), ItemRef(WORD, 0)),),
    )
    with pytest.raises(EffectRefusal) as refusal:
        RewriteDeclaration(
            "unlink", source, result, RewriteEffect.DECORATE
        ).check_effect()
    assert "relation instance 1 has no counterpart" in str(refusal.value)


def test_a_relinked_relation_instance_revises() -> None:
    """An instance keeping its slot but changing endpoints replaces its value."""
    source = graph(
        BASE.tiers[0].items,
        (Item(),),
        (RelationInstance(name("covers"), ItemRef(PHRASE, 0), ItemRef(WORD, 0)),),
    )
    result = graph(
        BASE.tiers[0].items,
        (Item(),),
        (RelationInstance(name("covers"), ItemRef(PHRASE, 0), ItemRef(WORD, 1)),),
    )
    with pytest.raises(EffectRefusal) as refusal:
        RewriteDeclaration(
            "relink", source, result, RewriteEffect.DECORATE
        ).check_effect()
    assert "relation instance 0 carries endpoints" in str(refusal.value)


def test_a_redeclared_attribute_domain_revises() -> None:
    """Declarations carry a shape, and a different shape is a different value."""
    moved = tuple(
        AttributeDeclaration(LABEL, AttributeDomain.TIER, XsdType.STRING)
        if declaration.name == LABEL
        else declaration
        for declaration in ATTRIBUTES
    )
    source = Graph(
        NAMESPACES,
        (Tier(TierDeclaration(WORD, "Word")),),
        DECLARATIONS[:1],
        (),
        ATTRIBUTES,
    )
    result = Graph(
        NAMESPACES, (Tier(TierDeclaration(WORD, "Word")),), DECLARATIONS[:1], (), moved
    )
    with pytest.raises(EffectRefusal) as refusal:
        RewriteDeclaration(
            "redeclare", source, result, RewriteEffect.DECORATE
        ).check_effect()
    assert "attribute declaration '{urn:rewrite}label' carries shape" in str(
        refusal.value
    )


def test_a_dropped_relation_declaration_collapses() -> None:
    """A declaration is a structure like any other."""
    source = Graph(NAMESPACES, (Tier(TierDeclaration(WORD, "Word")),), DECLARATIONS[:1])
    result = Graph(NAMESPACES, (Tier(TierDeclaration(WORD, "Word")),), ())
    with pytest.raises(EffectRefusal) as refusal:
        RewriteDeclaration(
            "undeclare", source, result, RewriteEffect.DECORATE
        ).check_effect()
    assert "relation declaration '{urn:rewrite}wordtype' has no counterpart" in str(
        refusal.value
    )


def test_a_declaration_carrying_a_replaced_value_revises() -> None:
    """A relation declaration's own attributes are examined like an item's."""
    carried = AttributeDeclaration(
        name("since"), AttributeDomain.RELATION_DECLARATION, XsdType.STRING
    )

    def build(lexical: str) -> Graph:
        """Return a graph whose membership declaration carries one value."""
        return Graph(
            NAMESPACES,
            (Tier(TierDeclaration(WORD, "Word")),),
            (
                SimpleRelationDeclaration(
                    name("wordtype"),
                    WORD,
                    WORD_TYPE,
                    (AttributeValue(name("since"), XsdType.STRING, lexical),),
                ),
            ),
            (),
            (carried,),
        )

    with pytest.raises(EffectRefusal) as refusal:
        RewriteDeclaration(
            "restate", build("one"), build("two"), RewriteEffect.DECORATE
        ).check_effect()
    assert "relation declaration '{urn:rewrite}wordtype' carries attribute" in str(
        refusal.value
    )


def test_a_dropped_polyadic_relation_collapses() -> None:
    """Polyadic instances are examined in their own order alongside binary ones."""
    side = RelationSideDeclaration((RelationEndpointKind.ITEM,), (WORD,))
    declaration = PolyadicRelationDeclaration(name("mixes"), side, side)
    instance = PolyadicRelationInstance(
        name("mixes"), (ItemRef(WORD, 0),), (ItemRef(WORD, 1),)
    )

    def build(instances: tuple[PolyadicRelationInstance, ...]) -> Graph:
        """Return a graph carrying the given polyadic instances."""
        return Graph(
            NAMESPACES,
            (Tier(TierDeclaration(WORD, "Word"), (labeled("a"), labeled("b"))),),
            (SimpleRelationDeclaration(name("wordtype"), WORD, WORD_TYPE), declaration),
            (),
            ATTRIBUTES,
            (),
            (),
            instances,
        )

    with pytest.raises(EffectRefusal) as refusal:
        RewriteDeclaration(
            "unmix", build((instance,)), build(()), RewriteEffect.DECORATE
        ).check_effect()
    assert "polyadic relation instance 0 has no counterpart" in str(refusal.value)


def test_a_replaced_document_value_revises() -> None:
    """The document itself carries values and is the last subject examined."""
    carried = AttributeDeclaration(
        name("title"), AttributeDomain.DOCUMENT, XsdType.STRING
    )

    def build(lexical: str) -> Graph:
        """Return a graph carrying one document-level value."""
        return Graph(
            NAMESPACES,
            (Tier(TierDeclaration(WORD, "Word")),),
            DECLARATIONS[:1],
            (),
            (carried,),
            (),
            (AttributeValue(name("title"), XsdType.STRING, lexical),),
        )

    with pytest.raises(EffectRefusal) as refusal:
        RewriteDeclaration(
            "retitle", build("first"), build("second"), RewriteEffect.DECORATE
        ).check_effect()
    assert "the document carries attribute" in str(refusal.value)


def test_a_removed_attribute_collapses_while_a_replaced_one_revises() -> None:
    """Losing a value and standing another in its place are different findings."""
    source = graph((labeled("a"),), ())
    removed = graph((Item(),), ())
    assert [
        item.effect
        for item in RewriteDeclaration("strip", source, removed).disturbances()
    ] == [RewriteEffect.COLLAPSE]
    replaced = graph((labeled("z"),), ())
    assert [
        item.effect
        for item in RewriteDeclaration("swap", source, replaced).disturbances()
    ] == [RewriteEffect.REVISE]


def test_a_removed_durable_id_collapses_and_a_changed_one_revises() -> None:
    """The identity seam extends from absent to present and no further."""
    named = graph((Item("w0", labeled("a").attributes),), ())
    anonymous = graph((labeled("a"),), ())
    renamed = graph((Item("w1", labeled("a").attributes),), ())
    assert [
        item.effect
        for item in RewriteDeclaration("forget", named, anonymous).disturbances()
    ] == [RewriteEffect.COLLAPSE]
    assert [
        item.effect
        for item in RewriteDeclaration("reidentify", named, renamed).disturbances()
    ] == [RewriteEffect.REVISE]


# --- reporting --------------------------------------------------------------


def test_disturbances_are_reported_in_the_source_reading_order() -> None:
    """The first disturbance is the first in a fixed order, and it says so."""
    source = graph((labeled("a"), labeled("b")), (Item(),), tier_attributes=())
    result = graph((labeled("x"), labeled("y")), (), word_long_name="Wordform")
    claim = RewriteDeclaration("churn", source, result, RewriteEffect.DECORATE)
    found = claim.disturbances()
    assert [item.subject for item in found] == [
        "tier '{urn:rewrite}word'",
        "item '{urn:rewrite}word'[0]",
        "item '{urn:rewrite}word'[1]",
        "item '{urn:rewrite}phrase'[0]",
    ]
    with pytest.raises(EffectRefusal) as refusal:
        claim.check_effect()
    message = str(refusal.value)
    assert "the first in the source's own reading order" in message
    assert "3 further disturbances also apply." in message


def test_one_further_disturbance_is_counted_in_the_singular() -> None:
    """A second finding is announced without pretending there are more."""
    source = graph((labeled("a"), labeled("b")), ())
    result = graph((labeled("x"), labeled("y")), ())
    with pytest.raises(EffectRefusal) as refusal:
        RewriteDeclaration(
            "pair", source, result, RewriteEffect.DECORATE
        ).check_effect()
    assert "1 further disturbance also applies." in str(refusal.value)


def test_a_lone_disturbance_announces_no_others() -> None:
    """The count is omitted rather than reported as zero."""
    source = graph((labeled("a"),), ())
    result = graph((labeled("x"),), ())
    with pytest.raises(EffectRefusal) as refusal:
        RewriteDeclaration("one", source, result, RewriteEffect.DECORATE).check_effect()
    assert "further disturbance" not in str(refusal.value)


def test_a_disturbance_is_readable_as_data() -> None:
    """A caller repairs a claim from the finding without matching English."""
    source = graph((labeled("a"),), ())
    result = graph((labeled("x"),), ())
    found = RewriteDeclaration("one", source, result).disturbances()
    assert len(found) == 1
    assert found[0].to_data() == {
        "effect": "revise",
        "subject": "item '{urn:rewrite}word'[0]",
        "tier": WORD.to_data(),
        "detail": (
            "carries attribute '{urn:rewrite}label' as 'string:x' where the "
            "source carried 'string:a'"
        ),
    }
    assert str(found[0]).startswith("item '{urn:rewrite}word'[0] carries attribute")


def test_a_disturbance_outside_any_tier_carries_no_tier() -> None:
    """Only a structure that belongs to a tier reports one."""
    source = Graph(NAMESPACES, (Tier(TierDeclaration(WORD, "Word")),), DECLARATIONS[:1])
    result = Graph(NAMESPACES, (Tier(TierDeclaration(WORD, "Word")),), ())
    found = RewriteDeclaration("undeclare", source, result).disturbances()
    assert [item.tier for item in found] == [None]
    assert found[0].to_data()["tier"] is None


def test_the_certificate_reports_how_much_the_claim_was_held_to() -> None:
    """A claim over a source that asserts little has been held to little."""
    small = Graph(NAMESPACES, (Tier(TierDeclaration(WORD, "Word")),), DECLARATIONS[:1])
    certificate = RewriteDeclaration(
        "trivial", small, small, RewriteEffect.DECORATE
    ).check_effect()
    assert certificate.subjects == 4
    assert certificate.disturbances == 0
    rich = RewriteDeclaration("rich", BASE, BASE, RewriteEffect.DECORATE).check_effect()
    assert rich.subjects > certificate.subjects
