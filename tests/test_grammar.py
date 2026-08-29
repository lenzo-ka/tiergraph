"""The synchronous grammar slice lowers and recognizes against a small oracle."""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from typing import cast

import pytest

from tiergraph import (
    AttributeValue,
    BestDerivation,
    GrammarDeclaration,
    GrammarHole,
    GrammarRule,
    GrammarTerminal,
    ItemRef,
    PolyadicRelationInstance,
    QualifiedName,
    XsdType,
    best,
    count,
    grammar_loads,
    lower_grammar,
    recognize,
)
from tiergraph.fold import ChildCombination, FoldTransition, TiePolicy
from tiergraph.grammar import _best_fold
from tiergraph.semiring import COUNTING, PATH, PathValue, Semiring

NAMESPACE = "urn:test:grammar"


def name(local: str) -> QualifiedName:
    """Return one fixture-qualified name."""
    return QualifiedName(NAMESPACE, local)


def string(local: str, lexical: str) -> AttributeValue:
    """Return one canonical string carrier for grammar content."""
    return AttributeValue(name(local), XsdType.STRING, lexical)


def decimal(lexical: str) -> AttributeValue:
    """Return one exact rule-weight carrier."""
    return AttributeValue(name("weight"), XsdType.DECIMAL, lexical)


def diamond(*, tied: bool = False) -> GrammarDeclaration:
    """Return an ambiguous grammar whose alternatives share a word derivation."""
    sentence = name("S")
    choice = name("A")
    shared = name("B")
    first_weight = decimal("0.5")
    second_weight = decimal("1" if tied else "2")
    source = (hole("a", choice), hole("b", shared))
    return GrammarDeclaration(
        (sentence, choice, shared),
        sentence,
        (
            GrammarRule(
                sentence,
                source,
                source,
                weight=first_weight,
            ),
            GrammarRule(
                choice, (terminal("x"),), (terminal("x"),), weight=decimal("1")
            ),
            GrammarRule(
                choice, (terminal("x"),), (terminal("x"),), weight=second_weight
            ),
            GrammarRule(
                shared, (terminal("y"),), (terminal("y"),), weight=decimal("0.25")
            ),
        ),
    )


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


def test_declaration_strict_json_round_trip() -> None:
    """Every declarative component decodes through the canonical value codec."""
    declaration = diamond()
    assert GrammarDeclaration.from_data(declaration.to_data()) == declaration
    assert grammar_loads(json.dumps(declaration.to_data())) == declaration
    assert grammar_loads(json.dumps(declaration.to_data()).encode()) == declaration
    rule = declaration.rules[0]
    assert GrammarRule.from_data(rule.to_data()) == rule
    assert GrammarTerminal.from_data(terminal("x").to_data()) == terminal("x")
    assert GrammarHole.from_data(hole("x", name("S")).to_data()) == hole("x", name("S"))


@pytest.mark.parametrize(
    ("data", "message"),
    (
        (None, "grammar must be an object"),
        ({"nonterminals": [], "start": name("S").to_data()}, "grammar fields"),
        (
            {"nonterminals": None, "start": name("S").to_data(), "rules": []},
            "nonterminals must be an array",
        ),
        (
            {"nonterminals": [], "start": name("S").to_data(), "rules": None},
            "rules must be an array",
        ),
    ),
)
def test_declaration_decoder_refuses_bad_shapes(data: object, message: str) -> None:
    """The declaration decoder rejects nonobjects, fields, and array shapes."""
    with pytest.raises(ValueError, match=message):
        GrammarDeclaration.from_data(data)


def test_component_decoders_refuse_every_malformed_branch() -> None:
    """Component errors identify discriminators, arrays, values, and weights."""
    terminal_data = terminal("x").to_data()
    hole_data = hole("x", name("S")).to_data()
    with pytest.raises(ValueError, match="terminal.kind"):
        GrammarTerminal.from_data({**terminal_data, "kind": "hole"})
    with pytest.raises(ValueError, match="hole.kind"):
        GrammarHole.from_data({**hole_data, "kind": "terminal"})

    rule_data = GrammarRule(name("S"), (terminal("x"),), (terminal("x"),)).to_data()
    for field in ("source", "target"):
        with pytest.raises(ValueError, match=f"{field} must be an array"):
            GrammarRule.from_data({**rule_data, field: None})
    for value, message in ((None, "must be an object"), ({}, "kind None.*unknown")):
        with pytest.raises(ValueError, match=message):
            GrammarRule.from_data({**rule_data, "source": [value]})
    with pytest.raises(ValueError, match="awaited_variables must be an array"):
        GrammarRule.from_data({**rule_data, "awaited_variables": None})
    with pytest.raises(ValueError, match="weight must be an attribute value or null"):
        GrammarRule.from_data({**rule_data, "weight": "1"})
    with pytest.raises(ValueError, match="weight.*xsd:decimal"):
        GrammarRule.from_data({**rule_data, "weight": string("weight", "1").to_data()})
    with pytest.raises(ValueError, match="not a valid XsdType"):
        GrammarTerminal.from_data(
            {
                **terminal_data,
                "text": {
                    **cast(dict[str, object], terminal_data["text"]),
                    "value_type": "wat",
                },
            }
        )
    with pytest.raises(ValueError, match=r"nonterminals\[0\].*invalid field types"):
        GrammarDeclaration.from_data(
            {
                "nonterminals": [{"namespace": 1, "local_name": "S"}],
                "start": name("S").to_data(),
                "rules": [],
            }
        )
    with pytest.raises(ValueError, match="terminal.text has invalid field types"):
        GrammarTerminal.from_data(
            {
                **terminal_data,
                "text": {
                    **cast(dict[str, object], terminal_data["text"]),
                    "lexical": 1,
                },
            }
        )
    with pytest.raises(ValueError, match="terminal.text has invalid field types"):
        GrammarTerminal.from_data(
            {
                **terminal_data,
                "text": {
                    **cast(dict[str, object], terminal_data["text"]),
                    "name": {"namespace": 1, "local_name": "text"},
                },
            }
        )


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
    with pytest.raises(ValueError, match=r"rule .* weight '1'.*xsd:decimal"):
        GrammarRule(name("S"), (), (), weight=integer)
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


def test_count_is_diamond_correct_and_has_linear_and_rejected_controls() -> None:
    """Counting multiplies shared children within each alternative before summing."""
    lowered = lower_grammar(diamond())
    assert count(lowered, ("x", "y")) == 2
    sentence = name("S")
    linear = lower_grammar(
        GrammarDeclaration(
            (sentence,),
            sentence,
            (GrammarRule(sentence, (terminal("x"),), (terminal("x"),)),),
        )
    )
    assert count(linear, ("x",)) == 1
    assert count(lowered, ("other",)) == 0
    assert best(lowered, ("other",)) == ()


def test_best_orders_exact_weights_and_resolves_ties_by_witness() -> None:
    """N-best retains a locally second child choice and orders exact total costs."""
    lowered = lower_grammar(diamond())
    ranked = best(lowered, ("x", "y"), count=2)
    assert [candidate.weight for candidate in ranked] == ["1.75", "2.75"]
    assert ranked == tuple(sorted(ranked, key=lambda item: (item.weight, item.witness)))
    fold_result = _best_fold(recognize(lowered, ("x", "y")), 2).run()
    assert fold_result.cost.ranked_multiplications > 0
    assert fold_result.cost.witness_operations > 0
    assert fold_result.cost.witness_count == 2
    assert fold_result.cost.emitted_count == 2
    assert fold_result.cost.measured_work <= fold_result.cost.bound
    assert "ranked_witnesses" in fold_result.to_data(PATH)
    assert "ranked_multiplications" in fold_result.cost.to_data()
    capped = _best_fold(recognize(lowered, ("x", "y")), 1).run()
    assert capped.cost.witness_count == 2
    assert capped.cost.emitted_count == 1
    assert capped.truncated is True
    tied = best(lower_grammar(diamond(tied=True)), ("x", "y"), count=2)
    assert [candidate.weight for candidate in tied] == ["1.75", "1.75"]
    assert tied[0].witness < tied[1].witness
    assert tied[0].to_data() == {
        "weight": "1.75",
        "witness": list(tied[0].witness),
    }
    with pytest.raises(ValueError, match="derivation count 0.*positive"):
        best(lowered, ("x", "y"), count=0)


def test_public_api_reuses_one_forest_for_all_three_questions() -> None:
    """Public counting and ranking consume the forest returned by recognition."""
    forest = recognize(lower_grammar(diamond()), ("x", "y"), collapse_units=False)
    graph = forest.graph
    fingerprint = forest.program.fingerprint()
    assert forest.recognized() is True
    assert count(forest) == 2
    assert [item.weight for item in best(forest, count=2)] == ["1.75", "2.75"]
    assert forest.count() == 2
    assert forest.best(1)[0].weight == "1.75"
    assert forest.graph is graph
    assert forest.program.fingerprint() == fingerprint
    with pytest.raises(ValueError, match="prebuilt parse forest"):
        count(forest, ("x", "y"))
    with pytest.raises(ValueError, match="lowered grammar requires input tokens"):
        count(lower_grammar(diamond()))


def test_cyclic_count_refuses_and_cyclic_best_succeeds_from_one_chart() -> None:
    """REGRESSION: COUNTING declines while PATH closes a productive unit SCC."""
    sentence = name("S")
    atom = name("A")
    unit_cycle = GrammarDeclaration(
        (sentence, atom),
        sentence,
        (
            GrammarRule(sentence, (hole("a", atom),), (hole("a", atom),)),
            GrammarRule(atom, (hole("s", sentence),), (hole("s", sentence),)),
            GrammarRule(atom, (terminal("x"),), (terminal("x"),)),
        ),
    )
    lowered = lower_grammar(unit_cycle)
    with pytest.raises(
        ValueError,
        match=r"SCC .*chart-items.*applications.*algebra CountingSemiring declares no star",
    ) as refusal:
        count(lowered, ("x",))
    assert "is a unit production" not in str(refusal.value)
    derivations = best(lowered, ("x",))
    assert len(derivations) == 1
    assert derivations[0].weight == "2.0"


def test_nonproductive_cycle_is_zero() -> None:
    """REGRESSION: zero elimination precedes star and nonlinearity checks."""
    sentence = name("S")
    ghost = name("G")
    ghost_hole = (hole("g", ghost),)
    grammar = GrammarDeclaration(
        (sentence, ghost),
        sentence,
        (
            GrammarRule(sentence, (terminal("a"),), (terminal("a"),)),
            GrammarRule(sentence, ghost_hole, ghost_hole),
            GrammarRule(ghost, ghost_hole, ghost_hole),
        ),
    )
    assert count(lower_grammar(grammar), ("a",)) == 1


def test_positive_span_binary_application_is_linear() -> None:
    """CHARACTERIZATION: S → S S on two tokens retains its count of one."""
    sentence = name("S")
    pair = (hole("left", sentence), hole("right", sentence))
    binary = GrammarDeclaration(
        (sentence,),
        sentence,
        (
            GrammarRule(sentence, pair, pair),
            GrammarRule(sentence, (terminal("a"),), (terminal("a"),)),
        ),
    )
    assert count(lower_grammar(binary), ("a", "a")) == 1


def test_nonnullable_guarded_recursion_retains_its_count() -> None:
    """CHARACTERIZATION: A → A B consumes input and forms no same-span SCC."""
    left = name("A")
    guard = name("B")
    recursive = (hole("a", left), hole("b", guard))
    grammar = GrammarDeclaration(
        (left, guard),
        left,
        (
            GrammarRule(left, recursive, recursive),
            GrammarRule(left, (terminal("a"),), (terminal("a"),)),
            GrammarRule(guard, (terminal("b"),), (terminal("b"),)),
        ),
    )
    assert count(lower_grammar(grammar), ("a", "b", "b")) == 1


def test_zero_width_nonlinear_scc_is_not_supported() -> None:
    """REGRESSION: G = 1 + G² names nonlinearity and its zero-width item."""
    sentence = name("S")
    ghost = name("G")
    pair = (hole("left", ghost), hole("right", ghost))
    ghost_hole = (hole("g", ghost),)
    grammar = GrammarDeclaration(
        (ghost, sentence),
        sentence,
        (
            GrammarRule(ghost, pair, pair),
            GrammarRule(ghost, (), ()),
            GrammarRule(sentence, (terminal("a"),), (terminal("a"),)),
            GrammarRule(sentence, ghost_hole, ghost_hole),
        ),
    )
    with pytest.raises(
        ValueError,
        match=r"nonlinear at zero-width chart item .*span \('0', '0'\).*CountingSemiring.*not supported",
    ):
        count(lower_grammar(grammar), ("a",))


def test_best_refuses_a_cycle_outside_its_star_warrant() -> None:
    """REGRESSION: a warrant refusal names its encoded operand and warrant."""
    sentence = name("S")
    self_hole = (hole("self", sentence),)
    grammar = GrammarDeclaration(
        (sentence,),
        sentence,
        (
            GrammarRule(sentence, self_hole, self_hole, weight=decimal("-1")),
            GrammarRule(sentence, (terminal("a"),), (terminal("a"),)),
        ),
    )
    with pytest.raises(
        ValueError,
        match=r"algebra PathSemiring; operand \['-1\.0'.*warrant 'zero-closed' refuses",
    ):
        best(lower_grammar(grammar), ("a",))


def test_best_refuses_alternative_negative_recursive_coefficients() -> None:
    """REGRESSION: recursive alternatives add; their weights do not multiply."""
    sentence = name("S")
    self_hole = (hole("self", sentence),)
    grammar = GrammarDeclaration(
        (sentence,),
        sentence,
        (
            GrammarRule(sentence, self_hole, self_hole, weight=decimal("-2")),
            GrammarRule(sentence, self_hole, self_hole, weight=decimal("3")),
            GrammarRule(sentence, (terminal("a"),), (terminal("a"),)),
        ),
    )
    with pytest.raises(
        ValueError,
        match=r"operand \['-2\.0'.*warrant 'zero-closed' refuses",
    ):
        best(lower_grammar(grammar), ("a",))


def test_collapsed_forest_requires_noncollapsing_recognition_for_folds() -> None:
    """REGRESSION: a lossy prebuilt forest cannot silently count or rank."""
    forest = recognize(lower_grammar(diamond()), ("x", "y"))
    assert forest.collapsed
    for operation in (forest.count, forest.best):
        with pytest.raises(ValueError, match="collapse_units=False"):
            operation()
    with pytest.raises(ValueError, match="collapse_units=False"):
        count(forest)


def test_best_candidates_are_produced_by_declared_fold_transitions() -> None:
    """Changing alternative incidence changes the fold's ranked result."""
    forest = recognize(lower_grammar(diamond()), ("x", "y"))
    declared = _best_fold(forest, 2)
    assert declared.tie_policy is TiePolicy.CHOOSE_FIRST
    assert declared.run().ranked_witnesses is not None
    broken = replace(
        declared,
        transitions=(
            declared.transitions[0],
            FoldTransition(declared.transitions[1].relation, ChildCombination.OR),
        ),
    )
    ranked = broken.run().ranked_witnesses
    assert ranked is not None
    assert ranked[0][0][0] != Decimal("1.75")


def test_ranked_fold_requires_order_preserving_multiplication() -> None:
    """Ranked pruning refuses a semiring without the monotonicity declaration."""
    declared = _best_fold(recognize(lower_grammar(diamond()), ("x", "y")), 2)
    with pytest.raises(
        ValueError,
        match="CountingSemiring.*multiply_preserves_witness_order",
    ):
        replace(declared, semiring=cast(Semiring[PathValue], COUNTING))


def test_ranked_fold_refuses_a_custom_noncanonical_order() -> None:
    """Ranked pruning cannot substitute a caller order for the semiring order."""
    declared = _best_fold(recognize(lower_grammar(diamond()), ("x", "y")), 2)
    with pytest.raises(ValueError, match="canonical order.*custom witness_order"):
        replace(declared, witness_order=lambda left, right: 0)
    with pytest.raises(ValueError, match="ranked witnesses.*no tie policy"):
        replace(declared, tie_policy=None)


def test_nonroot_pruning_does_not_report_root_truncation() -> None:
    """An ambiguous exhaustive state outside the selected root does not truncate."""
    sentence = name("S")
    unused = name("A")
    token_rule = (terminal("x"),)
    grammar = GrammarDeclaration(
        (sentence, unused),
        sentence,
        (
            GrammarRule(sentence, token_rule, token_rule),
            GrammarRule(unused, token_rule, token_rule),
            GrammarRule(unused, token_rule, token_rule),
        ),
    )
    result = _best_fold(recognize(lower_grammar(grammar), ("x",)), 1).run()
    assert result.cost.witness_count == 1
    assert result.cost.emitted_count == 1
    assert result.truncated is False


def test_small_enumeration_agrees_without_being_the_implementation() -> None:
    """A hand-sized rule-choice enumeration agrees with packed dynamic programming."""
    lowered = lower_grammar(diamond())
    enumerated = tuple(
        sorted(
            (
                BestDerivation(
                    str(Decimal("0.5") + choice + Decimal("0.25")),
                    (label,),
                )
                for choice, label in (
                    (Decimal("1"), "first"),
                    (Decimal("2"), "second"),
                )
            ),
            key=lambda item: (item.weight, item.witness),
        )
    )
    folded = best(lowered, ("x", "y"), count=2)
    assert count(lowered, ("x", "y")) == len(enumerated)
    assert [item.weight for item in folded] == [item.weight for item in enumerated]
