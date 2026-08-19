"""The synchronous grammar slice lowers and recognizes against a small oracle."""

from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest

from tiergraph import (
    AttributeValue,
    GrammarDeclaration,
    GrammarHole,
    GrammarRule,
    GrammarTerminal,
    ItemRef,
    PolyadicRelationInstance,
    QualifiedName,
    XsdType,
    lower_grammar,
    recognize,
)
from tiergraph.fold import ChildCombination, FoldTransition

NAMESPACE = "urn:test:grammar"


def name(local: str) -> QualifiedName:
    """Return one fixture-qualified name."""
    return QualifiedName(NAMESPACE, local)


def string(local: str, lexical: str) -> AttributeValue:
    """Return one canonical string carrier for grammar content."""
    return AttributeValue(name(local), XsdType.STRING, lexical)


def terminal(text: str) -> GrammarTerminal:
    """Build one terminal with its declared XSD carrier."""
    return GrammarTerminal(string("text", text))


def hole(variable: str, nonterminal: QualifiedName) -> GrammarHole:
    """Build one hole with its declared variable carrier."""
    return GrammarHole(string("variable", variable), nonterminal)


def oracle() -> GrammarDeclaration:
    """Return a page-sized grammar whose accepted token has two derivations."""
    sentence = name("S")
    spoken = terminal("spoken")
    return GrammarDeclaration(
        (sentence,),
        sentence,
        (
            GrammarRule(sentence, (terminal("written"),), (spoken,)),
            GrammarRule(sentence, (terminal("written"),), (spoken,)),
        ),
    )


def test_declaration_is_directional_and_json_serializable() -> None:
    """Reversing a rule changes identity and retained source/target data."""
    sentence = name("S")
    forward = GrammarRule(sentence, (terminal("written"),), (terminal("spoken"),))
    reverse = GrammarRule(sentence, forward.target, forward.source)
    declaration = GrammarDeclaration((sentence,), sentence, (forward,))
    assert forward != reverse
    assert hash(forward) != hash(reverse)
    assert declaration.to_data()["rules"] == [forward.to_data()]


def test_declaration_refuses_undeclared_nonterminals() -> None:
    """Start, left-hand, source-hole, and target-hole refusals name the role."""
    sentence = name("S")
    missing = name("missing")
    with pytest.raises(ValueError, match="grammar start.*not a declared"):
        GrammarDeclaration((sentence,), missing, ())
    with pytest.raises(ValueError, match="left-hand nonterminal"):
        GrammarDeclaration((sentence,), sentence, (GrammarRule(missing, (), ()),))
    for role, rule in (
        (
            "source hole 0",
            GrammarRule(sentence, (hole("x", missing),), (hole("x", sentence),)),
        ),
        (
            "target hole 0",
            GrammarRule(sentence, (hole("x", sentence),), (hole("x", missing),)),
        ),
    ):
        with pytest.raises(ValueError, match=role):
            GrammarDeclaration((sentence,), sentence, (rule,))


def test_declaration_refuses_variable_set_disagreement() -> None:
    """Unused, unbound, overlapping, and duplicate variables are refused."""
    sentence = name("S")
    variable = string("variable", "x")
    unused = GrammarRule(sentence, (hole("x", sentence),), ())
    with pytest.raises(ValueError, match="target variables.*must equal"):
        GrammarDeclaration((sentence,), sentence, (unused,))
    unbound = GrammarRule(sentence, (), (hole("x", sentence),))
    with pytest.raises(ValueError, match="target variables.*must equal"):
        GrammarDeclaration((sentence,), sentence, (unbound,))
    overlap = GrammarRule(
        sentence,
        (hole("x", sentence),),
        (hole("x", sentence),),
        awaited_variables=(variable,),
    )
    with pytest.raises(ValueError, match="both source-bound and awaited"):
        GrammarDeclaration((sentence,), sentence, (overlap,))
    with pytest.raises(ValueError, match="duplicate awaited"):
        replace(overlap, awaited_variables=(variable, variable))
    awaited = GrammarRule(
        sentence, (), (hole("x", sentence),), awaited_variables=(variable,)
    )
    GrammarDeclaration((sentence,), sentence, (awaited,))


def test_xsd_carriers_are_checked_at_construction() -> None:
    """Terminal, hole, boundary, and awaited values require XSD strings."""
    integer = AttributeValue(name("value"), XsdType.INTEGER, "1")
    with pytest.raises(ValueError, match="terminal text"):
        GrammarTerminal(integer)
    with pytest.raises(ValueError, match="hole variable"):
        GrammarHole(integer, name("S"))
    with pytest.raises(ValueError, match="boundary"):
        GrammarRule(name("S"), (), (), integer)
    with pytest.raises(ValueError, match="awaited variable"):
        GrammarRule(name("S"), (), (), awaited_variables=(integer,))
    with pytest.raises(ValueError, match="duplicate nonterminal"):
        GrammarDeclaration((name("S"), name("S")), name("S"), ())


def test_declaration_refuses_malformed_pattern_shapes() -> None:
    """Mutable containers and foreign elements name their rule, role, and value."""
    sentence = name("S")
    listed = GrammarRule(
        sentence,
        cast(tuple[GrammarTerminal | GrammarHole, ...], [terminal("x")]),
        (),
    )
    with pytest.raises(
        ValueError, match=r"rule 0 .* source pattern .* must be a tuple"
    ):
        GrammarDeclaration((sentence,), sentence, (listed,))
    foreign = GrammarRule(sentence, (cast(GrammarTerminal, "x"),), ())
    with pytest.raises(
        ValueError,
        match=r"rule 0 .* source element 0 'x'.*GrammarTerminal or GrammarHole",
    ):
        GrammarDeclaration((sentence,), sentence, (foreign,))
    target = GrammarRule(sentence, (), (cast(GrammarTerminal, 7),))
    with pytest.raises(ValueError, match=r"rule 0 .* target element 0 7"):
        GrammarDeclaration((sentence,), sentence, (target,))


def test_lowering_uses_machine_built_ordered_hedges() -> None:
    """Production slots and pattern elements retain directional order as structure."""
    sentence = name("S")
    noun = name("N")
    rule = GrammarRule(
        sentence,
        (terminal("the"), hole("n", noun)),
        (hole("n", noun), terminal("the")),
    )
    lowered = lower_grammar(GrammarDeclaration((sentence, noun), sentence, (rule,)))
    graph = lowered.as_built.graph
    production = graph.tiers[0].items[0]
    assert {value.lexical for value in production.attributes} >= {
        "grammar-rule-rhs",
        str(sentence),
        "complete",
    }
    slot_relation = next(
        relation
        for relation in graph.polyadic_relations
        if relation.declaration.local_name == "production-slots"
    )
    assert len(slot_relation.targets) == 2
    hedge_relations = [
        relation
        for relation in graph.polyadic_relations
        if relation.declaration.local_name == "slot-elements"
    ]
    assert [len(relation.targets) for relation in hedge_relations] == [2, 2]
    assert lowered.program.unroll() == lowered.as_built
    assert lowered.to_data()["fingerprint"] == lowered.program.fingerprint()


def test_oracle_recognizes_ambiguity_and_refuses_other_input() -> None:
    """The accepted forest carries an OR while the near input folds to false."""
    lowered = lower_grammar(oracle())
    accepted = recognize(lowered, ("written",))
    rejected = recognize(lowered, ("other",))
    assert accepted.recognized() is True
    assert accepted.result().value is True
    assert accepted.to_data()["recognized"] is True
    alternatives = [
        relation
        for relation in accepted.graph.relations
        if relation.declaration.local_name == "alternatives"
        and relation.left == accepted.root
    ]
    assert len(alternatives) == 2
    root_applications = {relation.right for relation in alternatives}
    applications = [
        relation
        for relation in accepted.graph.polyadic_relations
        if relation.declaration.local_name == "production-application"
        and relation.sources[0] in root_applications
    ]
    assert len(applications) == 2
    assert all(
        isinstance(relation, PolyadicRelationInstance) for relation in applications
    )
    assert rejected.recognized() is False
    assert all(
        value.name.local_name != "truth"
        for tier in accepted.graph.tiers
        for item in tier.items
        for value in item.attributes
    )


def test_unit_closure_and_nullable_rules_recognize() -> None:
    """Equal-span unit closure and width-zero productions reach the start item."""
    sentence = name("S")
    atom = name("A")
    unit = GrammarDeclaration(
        (sentence, atom),
        sentence,
        (
            GrammarRule(
                sentence,
                (hole("a", atom),),
                (hole("a", atom),),
            ),
            GrammarRule(atom, (terminal("x"),), (terminal("x"),)),
            GrammarRule(
                atom,
                (hole("s", sentence),),
                (hole("s", sentence),),
            ),
        ),
    )
    nullable = GrammarDeclaration(
        (sentence,), sentence, (GrammarRule(sentence, (), ()),)
    )
    repeated_nullable = GrammarDeclaration(
        (sentence, atom),
        sentence,
        (
            GrammarRule(
                sentence,
                (hole("a", atom), hole("a", atom)),
                (hole("a", atom), hole("a", atom)),
            ),
            GrammarRule(atom, (), ()),
        ),
    )
    assert recognize(lower_grammar(unit), ("x",)).recognized() is True
    assert recognize(lower_grammar(unit), ()).recognized() is False
    assert recognize(lower_grammar(nullable), ()).recognized() is True
    assert recognize(lower_grammar(repeated_nullable), ()).recognized() is True


def test_chart_keeps_ordered_children_and_nested_conjunction() -> None:
    """A reduction records child chart items in source order and folds with AND."""
    sentence = name("S")
    word = name("W")
    grammar = GrammarDeclaration(
        (sentence, word),
        sentence,
        (
            GrammarRule(
                sentence,
                (hole("a", word), hole("b", word)),
                (hole("a", word), hole("b", word)),
            ),
            GrammarRule(word, (terminal("x"),), (terminal("x"),)),
        ),
    )
    forest = recognize(lower_grammar(grammar), ("x", "x"))
    relation = next(
        candidate
        for candidate in forest.graph.polyadic_relations
        if candidate.declaration.local_name == "production-application"
        and len(candidate.targets) == 2
    )
    assert all(isinstance(target, ItemRef) for target in relation.targets)
    indexes = [cast(ItemRef, target).index for target in relation.targets]
    assert indexes == sorted(indexes)
    assert forest.recognized() is True
    with pytest.raises(ValueError, match="input token"):
        recognize(lower_grammar(grammar), ("x", 1))  # type: ignore[arg-type]


def test_fold_transitions_determine_recognition() -> None:
    """Breaking OR or removing an AND child changes the Boolean answer."""
    sentence = name("S")
    word = name("W")
    alternatives = GrammarDeclaration(
        (sentence,),
        sentence,
        (
            GrammarRule(sentence, (terminal("no"),), (terminal("no"),)),
            GrammarRule(sentence, (terminal("yes"),), (terminal("yes"),)),
        ),
    )
    alternative_forest = recognize(lower_grammar(alternatives), ("yes",))
    alternative_name = next(
        transition.relation
        for transition in alternative_forest.fold.transitions
        if transition.combination is ChildCombination.OR
    )
    conjunction_name = next(
        transition.relation
        for transition in alternative_forest.fold.transitions
        if transition.combination is ChildCombination.AND
    )
    miswired = replace(
        alternative_forest.fold,
        transitions=(
            FoldTransition(alternative_name, ChildCombination.AND),
            FoldTransition(conjunction_name, ChildCombination.AND),
        ),
    )
    assert alternative_forest.recognized() is True
    assert miswired.run().value is False

    children = GrammarDeclaration(
        (sentence, word),
        sentence,
        (
            GrammarRule(
                sentence,
                (hole("a", word), hole("b", word)),
                (hole("a", word), hole("b", word)),
            ),
            GrammarRule(word, (terminal("x"),), (terminal("x"),)),
        ),
    )
    child_forest = recognize(lower_grammar(children), ("x", "no"))
    root_application = next(
        cast(ItemRef, relation.right)
        for relation in child_forest.graph.relations
        if relation.declaration.local_name == "alternatives"
        and relation.left == child_forest.root
    )
    chart_tier = next(
        tier
        for tier in child_forest.graph.tiers
        if tier.declaration.name.local_name == "chart-items"
    )
    child_relation = next(
        relation
        for relation in child_forest.graph.relations
        if relation.declaration.local_name == "children"
        and relation.left == root_application
        and next(
            value.lexical
            for value in chart_tier.items[
                cast(ItemRef, relation.right).index
            ].attributes
            if value.name.local_name == "start"
        )
        == "1"
    )
    changed_graph = replace(
        child_forest.graph,
        relations=tuple(
            relation
            for relation in child_forest.graph.relations
            if relation != child_relation
        ),
    )
    broken_and = replace(child_forest.fold, graph=changed_graph)
    assert child_forest.recognized() is False
    assert broken_and.run().value is True
