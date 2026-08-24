"""Synchronous grammar declarations, coordinate lowering, and chart recognition."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import cast

from tiergraph.core import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
    Graph,
    Item,
    ItemRef,
    JsonValue,
    NamespaceDeclaration,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    QualifiedName,
    RelationEndpointKind,
    RelationInstance,
    RelationSideDeclaration,
    SimpleRelationDeclaration,
    TierDeclaration,
    XsdType,
)
from tiergraph.fold import (
    AttributeValuation,
    ChildCombination,
    FoldDeclaration,
    FoldResult,
    FoldTransition,
    TiePolicy,
)
from tiergraph.machine import (
    AddItem,
    AsBuilt,
    AttachValue,
    DeclareAttribute,
    DeclareNamespace,
    DeclareRelation,
    DeclareTier,
    Opcode,
    Program,
    Relate,
)
from tiergraph.machine import (
    _decode_attribute_value as _machine_decode_attribute_value,
)
from tiergraph.machine import _decode_object as _decode_object
from tiergraph.machine import _decode_qname as _machine_decode_qname
from tiergraph.path import (
    AlternativeRef,
    CanonicalPath,
    PathBinding,
    PathOffender,
    PathRefusal,
    PathRefusalCode,
)
from tiergraph.semiring import BOOLEAN, COUNTING, PATH, PathValue

GRAMMAR_NAMESPACE = "urn:tiergraph:grammar"
CHART_NAMESPACE = "urn:tiergraph:grammar:chart"
COMPLETE_BOUNDARY = AttributeValue(
    QualifiedName(GRAMMAR_NAMESPACE, "boundary"), XsdType.STRING, "complete"
)
UNIT_WEIGHT = AttributeValue(
    QualifiedName(GRAMMAR_NAMESPACE, "weight"), XsdType.DECIMAL, "1"
)


def _decode_qname(value: object, path: str) -> QualifiedName:
    try:
        name = _machine_decode_qname(value, path)
    except TypeError as error:  # pragma: no cover - defensive
        raise ValueError(f"{path} has invalid field types") from error
    if not isinstance(name.namespace, str) or not isinstance(name.local_name, str):
        raise ValueError(f"{path} has invalid field types")
    return name


def _decode_attribute_value(value: object, path: str) -> AttributeValue:
    try:
        attribute = _machine_decode_attribute_value(value, path)
    except TypeError as error:  # pragma: no cover - defensive
        raise ValueError(f"{path} has invalid field types") from error
    if (
        not isinstance(attribute.name.namespace, str)
        or not isinstance(attribute.name.local_name, str)
        or not isinstance(attribute.lexical, str)
    ):
        raise ValueError(f"{path} has invalid field types")
    return attribute


def _string_value(value: AttributeValue, subject: str) -> None:
    if value.value_type is not XsdType.STRING:
        raise ValueError(
            f"{subject} {value.lexical!r} must be carried as an xsd:string value"
        )


@dataclass(frozen=True, slots=True)
class GrammarTerminal:
    """Carry one source or target terminal as a canonical XSD string value."""

    text: AttributeValue

    def __post_init__(self) -> None:
        """Refuse a terminal whose declared value is not an XSD string."""
        _string_value(self.text, "terminal text")

    def to_data(self) -> dict[str, JsonValue]:
        """Return the terminal declaration as JSON-serializable data."""
        return {"kind": "terminal", "text": self.text.to_data()}

    @classmethod
    def from_data(cls, data: object) -> GrammarTerminal:
        """Decode one strict terminal declaration from JSON-compatible data."""
        obj = _decode_object(data, "grammar terminal", {"kind", "text"})
        if obj["kind"] != "terminal":
            raise ValueError(
                f"grammar terminal.kind {obj['kind']!r} must be 'terminal'"
            )
        return cls(_decode_attribute_value(obj["text"], "grammar terminal.text"))


@dataclass(frozen=True, slots=True)
class GrammarHole:
    """Bind one named pattern variable to a declared nonterminal."""

    variable: AttributeValue
    nonterminal: QualifiedName

    def __post_init__(self) -> None:
        """Refuse a variable whose declared value is not an XSD string."""
        _string_value(self.variable, "hole variable")

    def to_data(self) -> dict[str, JsonValue]:
        """Return the hole declaration as JSON-serializable data."""
        return {
            "kind": "hole",
            "variable": self.variable.to_data(),
            "nonterminal": self.nonterminal.to_data(),
        }

    @classmethod
    def from_data(cls, data: object) -> GrammarHole:
        """Decode one strict hole declaration from JSON-compatible data."""
        obj = _decode_object(data, "grammar hole", {"kind", "variable", "nonterminal"})
        if obj["kind"] != "hole":
            raise ValueError(f"grammar hole.kind {obj['kind']!r} must be 'hole'")
        return cls(
            _decode_attribute_value(obj["variable"], "grammar hole.variable"),
            _decode_qname(obj["nonterminal"], "grammar hole.nonterminal"),
        )


type GrammarPatternElement = GrammarTerminal | GrammarHole
type GrammarPattern = tuple[GrammarPatternElement, ...]


def _decode_pattern(data: object, path: str) -> GrammarPattern:
    if not isinstance(data, list):
        raise ValueError(f"{path} must be an array")
    elements: list[GrammarPatternElement] = []
    for index, value in enumerate(data):
        element_path = f"{path}[{index}]"
        if not isinstance(value, dict):
            raise ValueError(f"{element_path} must be an object")
        kind = value.get("kind")
        if kind == "terminal":
            elements.append(GrammarTerminal.from_data(value))
        elif kind == "hole":
            elements.append(GrammarHole.from_data(value))
        else:
            raise ValueError(f"{element_path}.kind {kind!r} is unknown")
    return tuple(elements)


@dataclass(frozen=True, slots=True)
class GrammarRule:
    """Declare one directional pairing of source and target patterns."""

    left: QualifiedName
    source: GrammarPattern
    target: GrammarPattern
    boundary: AttributeValue = COMPLETE_BOUNDARY
    awaited_variables: tuple[AttributeValue, ...] = ()
    weight: AttributeValue | None = None

    def __post_init__(self) -> None:
        """Canonicalize set-like awaited variables and validate XSD carriers."""
        _string_value(self.boundary, f"rule {str(self.left)!r} boundary")
        for variable in self.awaited_variables:
            _string_value(variable, f"rule {str(self.left)!r} awaited variable")
        if self.weight is not None and self.weight.value_type is not XsdType.DECIMAL:
            raise ValueError(
                f"rule {str(self.left)!r} weight {self.weight.lexical!r} "
                "must be carried as an xsd:decimal value"
            )
        ordered = tuple(sorted(self.awaited_variables, key=lambda value: value.lexical))
        if len({value.lexical for value in ordered}) != len(ordered):
            raise ValueError(f"rule {str(self.left)!r} has duplicate awaited variables")
        object.__setattr__(self, "awaited_variables", ordered)

    def to_data(self) -> dict[str, JsonValue]:
        """Return the directional rule as JSON-serializable data."""
        return {
            "left": self.left.to_data(),
            "source": [element.to_data() for element in self.source],
            "target": [element.to_data() for element in self.target],
            "boundary": self.boundary.to_data(),
            "awaited_variables": [
                variable.to_data() for variable in self.awaited_variables
            ],
            "weight": None if self.weight is None else self.weight.to_data(),
        }

    @classmethod
    def from_data(cls, data: object) -> GrammarRule:
        """Decode one strict directional rule from JSON-compatible data."""
        path = "grammar rule"
        obj = _decode_object(
            data,
            path,
            {
                "left",
                "source",
                "target",
                "boundary",
                "awaited_variables",
                "weight",
            },
        )
        awaited = obj["awaited_variables"]
        if not isinstance(awaited, list):
            raise ValueError(f"{path}.awaited_variables must be an array")
        weight = obj["weight"]
        if weight is not None and not isinstance(weight, dict):
            raise ValueError(f"{path}.weight must be an attribute value or null")
        return cls(
            _decode_qname(obj["left"], f"{path}.left"),
            _decode_pattern(obj["source"], f"{path}.source"),
            _decode_pattern(obj["target"], f"{path}.target"),
            _decode_attribute_value(obj["boundary"], f"{path}.boundary"),
            tuple(
                _decode_attribute_value(value, f"{path}.awaited_variables[{index}]")
                for index, value in enumerate(awaited)
            ),
            None
            if weight is None
            else _decode_attribute_value(weight, f"{path}.weight"),
        )

    @property
    def effective_weight(self) -> AttributeValue:
        """Return the declared weight or the unit rule cost."""
        return UNIT_WEIGHT if self.weight is None else self.weight


@dataclass(frozen=True, slots=True)
class GrammarDeclaration:
    """Hold a validated synchronous grammar with fixed source and target roles."""

    nonterminals: tuple[QualifiedName, ...]
    start: QualifiedName
    rules: tuple[GrammarRule, ...]

    def __post_init__(self) -> None:
        """Refuse undeclared symbols and inconsistent rule-variable sets."""
        if len(set(self.nonterminals)) != len(self.nonterminals):
            raise ValueError("grammar has duplicate nonterminal declarations")
        declared = set(self.nonterminals)
        if self.start not in declared:
            raise ValueError(
                f"grammar start {str(self.start)!r} is not a declared nonterminal"
            )
        for index, rule in enumerate(self.rules):
            label = f"rule {index} ({str(rule.left)!r})"
            if rule.left not in declared:
                raise ValueError(f"{label} left-hand nonterminal is not declared")
            for role, pattern in (("source", rule.source), ("target", rule.target)):
                if type(pattern) is not tuple:
                    raise ValueError(
                        f"{label} {role} pattern {pattern!r} must be a tuple"
                    )
                for element_index, element in enumerate(pattern):
                    if not isinstance(element, GrammarTerminal | GrammarHole):
                        raise ValueError(
                            f"{label} {role} element {element_index} {element!r} "
                            "must be a GrammarTerminal or GrammarHole"
                        )
                    if (
                        isinstance(element, GrammarHole)
                        and element.nonterminal not in declared
                    ):
                        raise ValueError(
                            f"{label} {role} hole {element_index} nonterminal "
                            f"{str(element.nonterminal)!r} is not declared"
                        )
            source = {
                element.variable.lexical
                for element in rule.source
                if isinstance(element, GrammarHole)
            }
            target = {
                element.variable.lexical
                for element in rule.target
                if isinstance(element, GrammarHole)
            }
            awaited = {value.lexical for value in rule.awaited_variables}
            overlap = source.intersection(awaited)
            if overlap:
                raise ValueError(
                    f"{label} variables {sorted(overlap)!r} are both source-bound and awaited"
                )
            expected = source.union(awaited)
            if target != expected:
                raise ValueError(
                    f"{label} target variables {sorted(target)!r} must equal source "
                    f"variables plus awaited variables {sorted(expected)!r}"
                )

    def to_data(self) -> dict[str, JsonValue]:
        """Return the grammar declaration as JSON-serializable data."""
        return {
            "nonterminals": [name.to_data() for name in self.nonterminals],
            "start": self.start.to_data(),
            "rules": [rule.to_data() for rule in self.rules],
        }

    @classmethod
    def from_data(cls, data: object) -> GrammarDeclaration:
        """Decode one strict grammar declaration from JSON-compatible data."""
        obj = _decode_object(data, "grammar", {"nonterminals", "start", "rules"})
        nonterminals = obj["nonterminals"]
        rules = obj["rules"]
        if not isinstance(nonterminals, list):
            raise ValueError("grammar.nonterminals must be an array")
        if not isinstance(rules, list):
            raise ValueError("grammar.rules must be an array")
        return cls(
            tuple(
                _decode_qname(value, f"grammar.nonterminals[{index}]")
                for index, value in enumerate(nonterminals)
            ),
            _decode_qname(obj["start"], "grammar.start"),
            tuple(GrammarRule.from_data(value) for value in rules),
        )


def grammar_loads(source: str | bytes) -> GrammarDeclaration:
    """Decode a strict grammar declaration from UTF-8 JSON text or bytes."""
    return GrammarDeclaration.from_data(json.loads(source))


@dataclass(frozen=True, slots=True)
class LoweredGrammar:
    """Pair a grammar with its replayable coordinate-hedge construction."""

    declaration: GrammarDeclaration
    program: Program
    as_built: AsBuilt

    def to_data(self) -> dict[str, JsonValue]:
        """Return the declaration, graph, and construction fingerprint."""
        return {
            "declaration": self.declaration.to_data(),
            "as_built": self.as_built.to_data(),
            "fingerprint": self.program.fingerprint(),
        }


def _name(namespace: str, local: str) -> QualifiedName:
    return QualifiedName(namespace, local)


def lower_grammar(
    declaration: GrammarDeclaration, namespace: str = GRAMMAR_NAMESPACE
) -> LoweredGrammar:
    """Lower a grammar through machine opcodes to an ordered coordinate hedge."""
    names = {
        local: _name(namespace, local)
        for local in (
            "productions",
            "slots",
            "elements",
            "production-slots",
            "slot-elements",
            "kind",
            "nonterminal",
            "boundary",
            "text",
            "variable",
            "weight",
        )
    }
    opcodes: list[Opcode] = [
        DeclareNamespace(NamespaceDeclaration("grammar", namespace))
    ]
    for local, long_name in (
        ("productions", "Grammar productions"),
        ("slots", "Grammar pattern slots"),
        ("elements", "Grammar pattern elements"),
    ):
        opcodes.append(DeclareTier(TierDeclaration(names[local], long_name)))
    item_side = (RelationEndpointKind.ITEM,)
    opcodes.extend(
        (
            DeclareRelation(
                PolyadicRelationDeclaration(
                    names["production-slots"],
                    RelationSideDeclaration(item_side, (names["productions"],), 1, 1),
                    RelationSideDeclaration(item_side, (names["slots"],), 2, 2),
                    unique_sources=True,
                )
            ),
            DeclareRelation(
                PolyadicRelationDeclaration(
                    names["slot-elements"],
                    RelationSideDeclaration(item_side, (names["slots"],), 1, 1),
                    RelationSideDeclaration(
                        item_side, (names["elements"],), 0, None, True
                    ),
                    unique_sources=True,
                    distinct_targets=True,
                    single_parent=True,
                )
            ),
        )
    )
    for local in ("kind", "nonterminal", "boundary", "text", "variable"):
        opcodes.append(
            DeclareAttribute(
                AttributeDeclaration(names[local], AttributeDomain.ITEM, XsdType.STRING)
            )
        )
    opcodes.append(
        DeclareAttribute(
            AttributeDeclaration(names["weight"], AttributeDomain.ITEM, XsdType.DECIMAL)
        )
    )
    slot_index = 0
    element_index = 0
    for rule_index, rule in enumerate(declaration.rules):
        production = ItemRef(names["productions"], rule_index)
        opcodes.append(AddItem(names["productions"], Item()))
        for local, lexical in (
            ("kind", "grammar-rule-rhs"),
            ("nonterminal", str(rule.left)),
            ("boundary", rule.boundary.lexical),
        ):
            opcodes.append(
                AttachValue(
                    AttributeDomain.ITEM,
                    production,
                    AttributeValue(names[local], XsdType.STRING, lexical),
                )
            )
        opcodes.append(
            AttachValue(
                AttributeDomain.ITEM,
                production,
                AttributeValue(
                    names["weight"], XsdType.DECIMAL, rule.effective_weight.lexical
                ),
            )
        )
        slots: list[ItemRef] = []
        for role, pattern in (("source", rule.source), ("target", rule.target)):
            slot = ItemRef(names["slots"], slot_index)
            slot_index += 1
            slots.append(slot)
            opcodes.append(AddItem(names["slots"], Item()))
            opcodes.append(
                AttachValue(
                    AttributeDomain.ITEM,
                    slot,
                    AttributeValue(names["kind"], XsdType.STRING, role),
                )
            )
            elements: list[ItemRef] = []
            for element in pattern:
                reference = ItemRef(names["elements"], element_index)
                element_index += 1
                elements.append(reference)
                opcodes.append(AddItem(names["elements"], Item()))
                values: tuple[tuple[str, str], ...]
                if isinstance(element, GrammarTerminal):
                    values = (("kind", "terminal"), ("text", element.text.lexical))
                else:
                    values = (
                        ("kind", "hole"),
                        ("variable", element.variable.lexical),
                        ("nonterminal", str(element.nonterminal)),
                    )
                for local, lexical in values:
                    opcodes.append(
                        AttachValue(
                            AttributeDomain.ITEM,
                            reference,
                            AttributeValue(names[local], XsdType.STRING, lexical),
                        )
                    )
            opcodes.append(
                Relate(
                    PolyadicRelationInstance(
                        names["slot-elements"], (slot,), tuple(elements)
                    )
                )
            )
        opcodes.append(
            Relate(
                PolyadicRelationInstance(
                    names["production-slots"], (production,), tuple(slots)
                )
            )
        )
    program = Program(tuple(opcodes))
    return LoweredGrammar(declaration, program, program.unroll())


@dataclass(frozen=True, slots=True)
class ParseForest:
    """Carry a machine-built parse forest and its Boolean interpretation."""

    graph: Graph
    program: Program
    root: ItemRef
    fold: FoldDeclaration[bool]
    declaration: GrammarDeclaration

    def recognized(self) -> bool:
        """Return whether the designated start span has a derivation."""
        return self.fold.run().value

    def result(self) -> FoldResult[bool]:
        """Evaluate and return the complete Boolean fold result."""
        return self.fold.run()

    def count(self) -> int:
        """Count derivations when the grammar lies in the finite-fold domain."""
        _require_finite_fold_domain(self.declaration, "count")
        return _count_fold(self).run().value

    def best(self, count: int = 1) -> tuple[BestDerivation, ...]:
        """Return up to ``count`` cheapest derivations, by exact total cost.

        The grammar must lie in the finite-fold domain. Costs are exact and the
        returned order is nondecreasing by cost. Among derivations of equal cost a
        deterministic subset is returned; that tie selection is not guaranteed to be a
        globally canonical one, because ranking keeps the cheapest by cost rather than
        by witness identity.
        """
        _require_finite_fold_domain(self.declaration, "best")
        return _best_derivations(self, count)

    def to_data(self) -> dict[str, JsonValue]:
        """Return the forest, root, fingerprint, and Boolean answer as JSON data."""
        return {
            "graph": self.graph.to_data(),
            "root": self.root.to_data(),
            "fingerprint": self.program.fingerprint(),
            "recognized": self.recognized(),
        }


@dataclass(frozen=True, slots=True)
class GrammarChartProfile:
    """Address chart alternatives in a stable order within one forest snapshot.

    The profile vocabulary is
    ``/chart/NONTERMINAL/START/END/alternatives/INDEX``. Alternative indices are
    independent of rule weights, but intentionally are not stable across forest
    snapshots whose sets of alternatives differ.
    """

    forest: ParseForest

    def bind(self, path: CanonicalPath, graph: Graph) -> PathBinding:
        """Bind a chart coordinate and profile-owned alternatives literal."""
        if graph is not self.forest.graph:
            raise PathRefusal(
                PathRefusalCode.PROFILE_REFUSED,
                PathOffender(
                    text=str(path),
                    path=path,
                    profile_reason="different_forest_snapshot",
                ),
            )
        segments = path.segments
        if (
            len(segments) != 6
            or segments[0] != "chart"
            or segments[4] != "alternatives"
        ):
            raise PathRefusal(
                PathRefusalCode.UNKNOWN_FORM,
                PathOffender(text=str(path), path=path),
            )
        start = _chart_path_index(segments[2], 2, path)
        end = _chart_path_index(segments[3], 3, path)
        index = _chart_path_index(segments[5], 5, path)
        names = _forest_names(self.forest)
        owner = next(
            (
                reference
                for reference in graph.canonical_items()
                if reference.tier == names["chart-items"]
                and _item_attribute(graph, reference, "nonterminal").lexical
                == segments[1]
                and _item_attribute(graph, reference, "start").lexical == str(start)
                and _item_attribute(graph, reference, "end").lexical == str(end)
            ),
            None,
        )
        if owner is None:
            raise PathRefusal(
                PathRefusalCode.PROFILE_REFUSED,
                PathOffender(
                    text=str(path), path=path, profile_reason="unknown_chart_item"
                ),
            )
        return AlternativeRef(owner, names["alternatives"], index)

    def spell(self, binding: PathBinding, graph: Graph) -> CanonicalPath:
        """Spell an alternative binding in this chart vocabulary."""
        if not isinstance(binding, AlternativeRef) or graph is not self.forest.graph:
            raise PathRefusal(
                PathRefusalCode.UNSPELLABLE,
                PathOffender(text="", profile_reason="unsupported_binding"),
            )
        names = _forest_names(self.forest)
        if binding.relation != names["alternatives"]:
            raise PathRefusal(
                PathRefusalCode.UNSPELLABLE,
                PathOffender(text="", profile_reason="unsupported_relation"),
            )
        owner = graph.resolve_item(binding.owner)
        if owner.tier != names["chart-items"]:
            raise PathRefusal(
                PathRefusalCode.UNSPELLABLE,
                PathOffender(text="", profile_reason="unsupported_owner"),
            )
        return CanonicalPath(
            (
                "chart",
                _item_attribute(graph, owner, "nonterminal").lexical,
                _item_attribute(graph, owner, "start").lexical,
                _item_attribute(graph, owner, "end").lexical,
                "alternatives",
                str(binding.index),
            )
        )

    def alternatives(
        self, owner: ItemRef, relation: QualifiedName, graph: Graph
    ) -> tuple[object, ...]:
        """Order applications by rule ordinal and ordered child spans."""
        names = _forest_names(self.forest)
        if graph is not self.forest.graph or relation != names["alternatives"]:
            raise PathRefusal(
                PathRefusalCode.PROFILE_REFUSED,
                PathOffender(
                    text="", relation=relation, profile_reason="unsupported_relation"
                ),
            )
        if owner.tier != names["chart-items"]:
            raise PathRefusal(
                PathRefusalCode.PROFILE_REFUSED,
                PathOffender(
                    text="", tier=owner.tier, profile_reason="unsupported_owner"
                ),
            )
        applications = tuple(
            cast(ItemRef, edge.right)
            for edge in graph.relations
            if edge.declaration == relation and edge.left == owner
        )

        def _stable_key(
            application: ItemRef,
        ) -> tuple[int, tuple[tuple[int, int], ...], int]:
            production = next(
                instance
                for instance in graph.polyadic_relations
                if instance.declaration == names["production-application"]
                and instance.sources == (application,)
            )
            spans = tuple(
                (
                    int(_item_attribute(graph, cast(ItemRef, child), "start").lexical),
                    int(_item_attribute(graph, cast(ItemRef, child), "end").lexical),
                )
                for child in production.targets
            )
            # (rule ordinal, child spans) is the semantic order; the application's
            # own tier index is a canonical, unique tiebreak so genuinely colliding
            # variants (e.g. two nullable expansions that project to the identical
            # (production, children)) get a stable order rather than falling back
            # to graph.relations incidence order. The tier index is builder-assigned
            # and survives any reordering of relation instances.
            return (
                int(_item_attribute(graph, application, "start").lexical),
                spans,
                application.index,
            )

        return tuple(sorted(applications, key=_stable_key))


@dataclass(frozen=True, slots=True)
class BestDerivation:
    """Carry an exact total cost and one deterministic derivation witness."""

    weight: str
    witness: tuple[str, ...]

    def to_data(self) -> dict[str, JsonValue]:
        """Return the result as JSON-serializable data."""
        return {"weight": self.weight, "witness": list(self.witness)}


def _candidate_matches(
    pattern: GrammarPattern,
    tokens: tuple[str, ...],
    start: int,
    end: int,
) -> tuple[tuple[tuple[tuple[QualifiedName, int, int], ...], bool], ...]:
    found: list[tuple[tuple[tuple[QualifiedName, int, int], ...], bool]] = []

    def _visit(
        index: int,
        position: int,
        children: tuple[tuple[QualifiedName, int, int], ...],
        terminals_match: bool,
    ) -> None:
        if index == len(pattern):
            if position == end:
                found.append((children, terminals_match))
            return
        element = pattern[index]
        if isinstance(element, GrammarTerminal):
            if position < end:
                _visit(
                    index + 1,
                    position + 1,
                    children,
                    terminals_match and tokens[position] == element.text.lexical,
                )
            return
        for boundary in range(position + 1, end + 1):
            key = (element.nonterminal, position, boundary)
            _visit(index + 1, boundary, (*children, key), terminals_match)

    _visit(0, start, (), True)
    return tuple(found)


def _recognition_rules(
    declaration: GrammarDeclaration,
    source_rules: tuple[tuple[QualifiedName, GrammarPattern], ...],
) -> tuple[tuple[int, QualifiedName, GrammarPattern], ...]:
    nullable: set[QualifiedName] = set()
    changed = True
    while changed:
        changed = False
        for left, pattern in source_rules:
            if left in nullable:
                continue
            if all(
                isinstance(element, GrammarHole) and element.nonterminal in nullable
                for element in pattern
            ):
                nullable.add(left)
                changed = True

    expanded: list[tuple[int, QualifiedName, GrammarPattern]] = []
    for rule_index, (left, pattern) in enumerate(source_rules):
        variants: list[GrammarPattern] = [()]
        for element in pattern:
            kept = [(*variant, element) for variant in variants]
            if isinstance(element, GrammarHole) and element.nonterminal in nullable:
                variants = [*variants, *kept]
            else:
                variants = kept
        for variant in variants:
            candidate = (rule_index, left, variant)
            if candidate not in expanded:
                expanded.append(candidate)

    unit_targets: dict[QualifiedName, tuple[QualifiedName, ...]] = {}
    for left in declaration.nonterminals:
        targets = tuple(
            pattern[0].nonterminal
            for _, candidate_left, pattern in expanded
            if candidate_left == left
            and len(pattern) == 1
            and isinstance(pattern[0], GrammarHole)
        )
        unit_targets[left] = tuple(dict.fromkeys(targets))

    result: list[tuple[int, QualifiedName, GrammarPattern]] = []
    for left in declaration.nonterminals:
        closure = [left]
        for reachable_unit in closure:
            for target in unit_targets[reachable_unit]:
                if target not in closure:
                    closure.append(target)
        for reachable in closure:
            for rule_index, candidate_left, pattern in expanded:
                unit = len(pattern) == 1 and isinstance(pattern[0], GrammarHole)
                candidate = (rule_index, left, pattern)
                if candidate_left == reachable and not unit and candidate not in result:
                    result.append(candidate)
    return tuple(result)


def _source_rules(
    grammar: LoweredGrammar,
) -> tuple[tuple[QualifiedName, GrammarPattern], ...]:
    graph = grammar.as_built.graph
    tiers = {tier.declaration.name.local_name: tier for tier in graph.tiers}
    productions = tiers["productions"]
    elements = tiers["elements"]
    slots_by_production = {
        cast(ItemRef, relation.sources[0]): tuple(
            cast(ItemRef, target) for target in relation.targets
        )
        for relation in graph.polyadic_relations
        if relation.declaration.local_name == "production-slots"
    }
    elements_by_slot = {
        cast(ItemRef, relation.sources[0]): tuple(
            cast(ItemRef, target) for target in relation.targets
        )
        for relation in graph.polyadic_relations
        if relation.declaration.local_name == "slot-elements"
    }
    names = {str(name): name for name in grammar.declaration.nonterminals}
    rules: list[tuple[QualifiedName, GrammarPattern]] = []
    for index, production in enumerate(productions.items):
        values = {
            value.name.local_name: value.lexical for value in production.attributes
        }
        left = names[values["nonterminal"]]
        source_slot = slots_by_production[ItemRef(productions.declaration.name, index)][
            0
        ]
        pattern: list[GrammarPatternElement] = []
        for reference in elements_by_slot[source_slot]:
            element_values = {
                value.name.local_name: value
                for value in elements.items[reference.index].attributes
            }
            if element_values["kind"].lexical == "terminal":
                pattern.append(GrammarTerminal(element_values["text"]))
            else:
                pattern.append(
                    GrammarHole(
                        element_values["variable"],
                        names[element_values["nonterminal"].lexical],
                    )
                )
        rules.append((left, tuple(pattern)))
    return tuple(rules)


def recognize(
    grammar: LoweredGrammar,
    input_tokens: Sequence[str],
    namespace: str = CHART_NAMESPACE,
) -> ParseForest:
    """Build a chart forest for token input using polynomial span deduction.

    For a fixed grammar whose longest source pattern has length ``m``, the
    exhaustive boundary discipline takes ``O(n^(m+1))`` time and polynomial
    space in input length ``n``. Nullable expansion and unit closure remove
    same-span dependencies before candidate construction, so every remaining
    child span is shorter than its parent span.
    """
    tokens = tuple(input_tokens)
    if any(not isinstance(token, str) for token in tokens):
        raise ValueError("grammar input token must be a string")
    declaration = grammar.declaration
    source_rules = _source_rules(grammar)
    recognition_rules = _recognition_rules(declaration, source_rules)
    applications: dict[
        tuple[QualifiedName, int, int],
        list[tuple[int, tuple[tuple[QualifiedName, int, int], ...], bool]],
    ] = {}
    size = len(tokens)
    keys = [
        (nonterminal, start, end)
        for width in range(size + 1)
        for nonterminal in declaration.nonterminals
        for start in range(size - width + 1)
        for end in (start + width,)
    ]
    for key in keys:
        left, start, end = key
        choices = applications.setdefault(key, [])
        for rule_index, candidate_left, source in recognition_rules:
            if candidate_left != left:
                continue
            for children, terminals_match in _candidate_matches(
                source, tokens, start, end
            ):
                application = (rule_index, children, terminals_match)
                choices.append(application)
        if not choices:
            choices.append((-1, (), False))
    root_key = (declaration.start, 0, size)
    references = {
        key: ItemRef(_name(namespace, "chart-items"), index)
        for index, key in enumerate(keys)
    }
    app_rows = [
        (key, rule_index, children, terminals_match)
        for key in keys
        for rule_index, children, terminals_match in applications[key]
    ]
    names = {
        local: _name(namespace, local)
        for local in (
            "chart-items",
            "applications",
            "alternatives",
            "children",
            "production-application",
            "local-factor",
            "weight",
            "kind",
            "nonterminal",
            "start",
            "end",
        )
    }
    opcodes: list[Opcode] = [DeclareNamespace(NamespaceDeclaration("chart", namespace))]
    opcodes.extend(
        (
            DeclareTier(TierDeclaration(names["chart-items"], "Chart items")),
            DeclareTier(
                TierDeclaration(names["applications"], "Production applications")
            ),
            DeclareRelation(
                SimpleRelationDeclaration(
                    _name(namespace, "chart-membership"),
                    names["chart-items"],
                    _name(namespace, "chart-item-type"),
                )
            ),
            DeclareRelation(
                SimpleRelationDeclaration(
                    _name(namespace, "application-membership"),
                    names["applications"],
                    _name(namespace, "application-type"),
                )
            ),
        )
    )
    for relation, left, right, acyclic in (
        (
            "alternatives",
            _name(namespace, "chart-item-type"),
            _name(namespace, "application-type"),
            True,
        ),
        (
            "children",
            _name(namespace, "application-type"),
            _name(namespace, "chart-item-type"),
            True,
        ),
    ):
        opcodes.append(
            DeclareRelation(
                BipartiteRelationDeclaration(
                    names[relation], left, right, acyclic=acyclic
                )
            )
        )
    item_side = (RelationEndpointKind.ITEM,)
    opcodes.append(
        DeclareRelation(
            PolyadicRelationDeclaration(
                names["production-application"],
                RelationSideDeclaration(item_side, (names["applications"],), 1, 1),
                RelationSideDeclaration(
                    item_side, (names["chart-items"],), 0, None, True
                ),
                unique_sources=True,
                acyclic=True,
            )
        )
    )
    for local, value_type in (
        ("local-factor", XsdType.BOOLEAN),
        ("weight", XsdType.DECIMAL),
        ("kind", XsdType.STRING),
        ("nonterminal", XsdType.STRING),
        ("start", XsdType.INTEGER),
        ("end", XsdType.INTEGER),
    ):
        opcodes.append(
            DeclareAttribute(
                AttributeDeclaration(names[local], AttributeDomain.ITEM, value_type)
            )
        )
    for key in keys:
        reference = references[key]
        opcodes.append(AddItem(names["chart-items"], Item()))
        values = (
            ("local-factor", XsdType.BOOLEAN, "true"),
            ("weight", XsdType.DECIMAL, "0"),
            ("kind", XsdType.STRING, "chart-item"),
            ("nonterminal", XsdType.STRING, str(key[0])),
            ("start", XsdType.INTEGER, str(key[1])),
            ("end", XsdType.INTEGER, str(key[2])),
        )
        for local, value_type, lexical in values:
            opcodes.append(
                AttachValue(
                    AttributeDomain.ITEM,
                    reference,
                    AttributeValue(names[local], value_type, lexical),
                )
            )
    for app_index, (parent, rule_index, children, terminals_match) in enumerate(
        app_rows
    ):
        app = ItemRef(names["applications"], app_index)
        opcodes.append(AddItem(names["applications"], Item()))
        for local, value_type, lexical in (
            (
                "local-factor",
                XsdType.BOOLEAN,
                "true" if terminals_match else "false",
            ),
            (
                "weight",
                XsdType.DECIMAL,
                "0"
                if rule_index < 0
                else declaration.rules[rule_index].effective_weight.lexical,
            ),
            ("kind", XsdType.STRING, "production-application"),
            ("start", XsdType.INTEGER, str(rule_index)),
        ):
            opcodes.append(
                AttachValue(
                    AttributeDomain.ITEM,
                    app,
                    AttributeValue(names[local], value_type, lexical),
                )
            )
        child_refs = tuple(references[child] for child in children)
        opcodes.append(
            Relate(RelationInstance(names["alternatives"], references[parent], app))
        )
        for child in child_refs:
            opcodes.append(Relate(RelationInstance(names["children"], app, child)))
        opcodes.append(
            Relate(
                PolyadicRelationInstance(
                    names["production-application"], (app,), child_refs
                )
            )
        )
    program = Program(tuple(opcodes))
    graph = program.unroll().graph
    root = references[root_key]
    fold = FoldDeclaration(
        "grammar-recognition",
        graph,
        AttributeValuation(
            "local factor",
            names["local-factor"],
            (names["chart-items"], names["applications"]),
        ),
        BOOLEAN,
        lambda value, label: cast(bool, value),
        (
            FoldTransition(names["alternatives"], ChildCombination.OR),
            FoldTransition(names["children"], ChildCombination.AND),
        ),
        roots=(root,),
    )
    return ParseForest(graph, program, root, fold, declaration)


def _forest_names(forest: ParseForest) -> dict[str, QualifiedName]:
    return {
        local: _name(forest.root.tier.namespace, local)
        for local in (
            "chart-items",
            "applications",
            "alternatives",
            "children",
            "production-application",
            "local-factor",
            "weight",
            "nonterminal",
            "start",
            "end",
        )
    }


def _chart_path_index(value: str, index: int, path: CanonicalPath) -> int:
    if value == "0" or (
        value.isascii() and value.isdecimal() and not value.startswith("0")
    ):
        return int(value)
    code = (
        PathRefusalCode.NONCANONICAL_SEGMENT
        if value.isdecimal() and value != ""
        else PathRefusalCode.INVALID_SEGMENT
    )
    raise PathRefusal(
        code,
        PathOffender(text=str(path), path=path, segment_index=index, segment=value),
    )


def _item_attribute(graph: Graph, reference: ItemRef, local: str) -> AttributeValue:
    item = next(
        tier.items[reference.index]
        for tier in graph.tiers
        if tier.declaration.name == reference.tier
    )
    return next(value for value in item.attributes if value.name.local_name == local)


def _count_fold(forest: ParseForest) -> FoldDeclaration[int]:
    names = _forest_names(forest)
    return FoldDeclaration(
        "grammar-derivation-count",
        forest.graph,
        AttributeValuation(
            "terminal match",
            names["local-factor"],
            (names["chart-items"], names["applications"]),
        ),
        COUNTING,
        lambda value, label: 1 if cast(bool, value) else 0,
        (
            FoldTransition(names["alternatives"], ChildCombination.OR),
            FoldTransition(names["children"], ChildCombination.AND),
        ),
        roots=(forest.root,),
    )


def _best_fold(forest: ParseForest, output_cap: int) -> FoldDeclaration[PathValue]:
    names = _forest_names(forest)
    valid_labels = {
        f"{reference.tier.namespace}:{reference.tier.local_name}:{reference.index}"
        for reference in forest.graph.canonical_items()
        if _item_attribute(forest.graph, reference, "local-factor").lexical == "true"
    }

    def lift(value: object, label: str) -> PathValue:
        """Lift a valid local weight and annihilate a terminal mismatch."""
        if label not in valid_labels:
            return PATH.zero
        return (cast(Decimal, value), ((label,),))

    return FoldDeclaration(
        "grammar-best-derivation",
        forest.graph,
        AttributeValuation(
            "rule weight",
            names["weight"],
            (names["chart-items"], names["applications"]),
        ),
        PATH,
        lift,
        (
            FoldTransition(names["alternatives"], ChildCombination.OR),
            FoldTransition(names["children"], ChildCombination.AND),
        ),
        roots=(forest.root,),
        tie_policy=TiePolicy.CHOOSE_FIRST,
        output_cap=output_cap,
        ranked_output=True,
    )


def _require_finite_fold_domain(
    declaration: GrammarDeclaration, operation: str
) -> None:
    nullable: set[QualifiedName] = set()
    changed = True
    while changed:
        changed = False
        for rule in declaration.rules:
            if rule.left in nullable:
                continue
            if not rule.source or all(
                isinstance(element, GrammarHole) and element.nonterminal in nullable
                for element in rule.source
            ):
                nullable.add(rule.left)
                changed = True
    for index, rule in enumerate(declaration.rules):
        unit = len(rule.source) == 1 and isinstance(rule.source[0], GrammarHole)
        nullable_rule = not rule.source or all(
            isinstance(element, GrammarHole) and element.nonterminal in nullable
            for element in rule.source
        )
        if unit or nullable_rule:
            kind = "unit" if unit else "nullable/epsilon"
            raise ValueError(
                f"{operation} rule {index} ({str(rule.left)!r}) is a {kind} "
                "production; counting and best-cost over unit/nullable/cyclic "
                "grammars require the star / least-fixpoint fold, which is not yet built"
            )


def _forest(
    grammar: LoweredGrammar | ParseForest,
    input_tokens: Sequence[str] | None,
    operation: str,
) -> ParseForest:
    if isinstance(grammar, ParseForest):
        if input_tokens is not None:
            raise ValueError("a prebuilt parse forest does not accept input tokens")
        _require_finite_fold_domain(grammar.declaration, operation)
        return grammar
    if input_tokens is None:
        raise ValueError("a lowered grammar requires input tokens")
    _require_finite_fold_domain(grammar.declaration, operation)
    return recognize(grammar, input_tokens)


def count(
    grammar: LoweredGrammar | ParseForest,
    input_tokens: Sequence[str] | None = None,
) -> int:
    """Return the derivation count from a new or previously built forest."""
    forest = _forest(grammar, input_tokens, "count")
    return _count_fold(forest).run().value


def _best_derivations(
    forest: ParseForest, output_cap: int
) -> tuple[BestDerivation, ...]:
    if output_cap < 1:
        raise ValueError(f"best derivation count {output_cap!r} must be positive")
    result = _best_fold(forest, output_cap).run()
    ranked = cast(
        tuple[tuple[PathValue, tuple[str, ...]], ...], result.ranked_witnesses
    )
    return tuple(BestDerivation(str(value[0]), witness) for value, witness in ranked)


def best(
    grammar: LoweredGrammar | ParseForest,
    input_tokens: Sequence[str] | None = None,
    count: int = 1,
) -> tuple[BestDerivation, ...]:
    """Return folded derivations by exact cost, choosing canonical paths on ties."""
    forest = _forest(grammar, input_tokens, "best")
    return _best_derivations(forest, count)


__all__ = [
    "BestDerivation",
    "CHART_NAMESPACE",
    "COMPLETE_BOUNDARY",
    "GRAMMAR_NAMESPACE",
    "GrammarDeclaration",
    "GrammarChartProfile",
    "GrammarHole",
    "GrammarRule",
    "GrammarTerminal",
    "LoweredGrammar",
    "ParseForest",
    "best",
    "count",
    "grammar_loads",
    "lower_grammar",
    "recognize",
]
