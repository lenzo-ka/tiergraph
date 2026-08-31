"""Declared valuations and semiring folds over finite dependency DAGs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from functools import cmp_to_key
from itertools import product
from typing import Protocol, TypeVar

from tiergraph.core import (
    AttributeDomain,
    BipartiteRelationDeclaration,
    Graph,
    Item,
    ItemRef,
    QualifiedName,
    XsdType,
)
from tiergraph.semiring import LawCheck, Semiring, StarRefusal, inexact_laws

Value = TypeVar("Value")
OtherValue = TypeVar("OtherValue")
LiftValue = TypeVar("LiftValue", covariant=True)
ReadValue = TypeVar("ReadValue", contravariant=True)
IndexCoordinate = tuple[str, ...]
State = tuple[ItemRef, IndexCoordinate]
Path = tuple[str, ...]
DerivationProvenance = tuple[Path, ...]
type RankedWitness[Value] = tuple[Value, Path]
type _Outgoing = dict[ItemRef, dict[QualifiedName, tuple[ItemRef, ...]]]
type _DependencyGraph = tuple[
    _Outgoing,
    tuple[ItemRef, ...],
    tuple[ItemRef, ...],
    dict[ItemRef, int],
    dict[ItemRef, tuple[ItemRef, ...]],
]

# The law search reads the fold's own state values, so its cost is set by how
# many distinct ones a fold produces rather than by a caller's probe set. The
# cap keeps the cubic triple search bounded on a large document; the values are
# taken in canonical encoded order, so which ones survive the cap is stable.
_PROBE_CAP = 8


class Lift(Protocol[LiftValue]):
    """Embed one typed attribute value and its stable label in a carrier."""

    def __call__(self, value: object, label: str, /) -> LiftValue:
        """Return one local carrier value."""


class WitnessOrder(Protocol[ReadValue]):
    """Compare carrier values for witness selection without enriching them."""

    def __call__(self, left: ReadValue, right: ReadValue, /) -> int:
        """Return negative for left, positive for right, or zero for a tie."""


class ChildCombination(Enum):
    """Declare whether one relation's incident children are alternatives or requirements."""

    OR = "or"
    AND = "and"


@dataclass(frozen=True, slots=True)
class FoldTransition:
    """Give one dependency relation its local AND/OR incidence meaning."""

    relation: QualifiedName
    combination: ChildCombination


class TiePolicy(Enum):
    """Supported, executable policies for equal-valued alternatives.

    A policy answers a tie that a declared ``witness_order`` reports, so it is
    declared with that order and with nothing else. Ranked output totalizes its
    own comparison and takes no policy.
    """

    ALL = "all"
    CHOOSE_FIRST = "choose-first"


class FoldExactness(Enum):
    """State how a fold's published value stands to the combination over every derivation.

    ``DISTRIBUTIVE``
        The value **is** the combination over every derivation. Gate: no
        counterexample may exist, and none may be found at the values this fold
        itself produces.
    ``APPROXIMATE``
        The value is a sound approximation of that combination, and that is a
        fact about the published result rather than a footnote about the
        algebra. Gate: the approximation must be exhibitable — an ``APPROXIMATE``
        claim over a fold measured exact by an algebra that checks every law
        exactly is a declaration that is hiding, and it is refused.
    ``STRUCTURAL``
        No such combination exists: the derivation set is infinite because the
        dependency graph has a cycle, so the starred fixpoint equations are the
        specification. Gate: the algebra must name the star warrant that makes
        the closure converge, and the graph must actually carry a cycle.
    ``UNDECLARED``
        The default. It is refused, and it does not mean ``APPROXIMATE``:
        declining to say is not the same as saying the weaker thing, and the
        refusal says so by handing back the declaration to be made.

    Every branch bites, and the asymmetry is deliberate. Omitting the claim is
    answered with the declaration; asserting it falsely is answered with a
    semantic counterexample.
    """

    DISTRIBUTIVE = "distributive"
    APPROXIMATE = "approximate"
    STRUCTURAL = "structural"
    UNDECLARED = "undeclared"


class ExactnessRefusal(ValueError):
    """Refuse an exactness claim a fold does not make good on."""


class _BudgetExceeded(Exception):
    """Stop an enumeration that outgrew the caller's declared derivation budget."""


@dataclass(frozen=True, slots=True)
class AttributeValuation:
    """Read one declared item attribute over an explicit tier domain."""

    name: str
    attribute: QualifiedName
    tiers: tuple[QualifiedName, ...]

    def __post_init__(self) -> None:
        """Require names and a nonempty duplicate-free domain."""
        if not self.name:
            raise ValueError("valuation name '' must not be empty")
        if not self.tiers:
            raise ValueError(f"valuation {self.name!r} has an empty tier domain")
        if len(set(self.tiers)) != len(self.tiers):
            raise ValueError(f"valuation {self.name!r} has duplicate tier names")

    def declaration_type(self, graph: Graph) -> XsdType:
        """Return the declared XSD type, refusing the wrong domain or a missing name."""
        declaration = next(
            (
                candidate
                for candidate in graph.attribute_declarations
                if candidate.name == self.attribute
            ),
            None,
        )
        if declaration is None:
            raise ValueError(
                f"valuation {self.name!r} names undeclared attribute {str(self.attribute)!r}"
            )
        if declaration.domain is not AttributeDomain.ITEM:
            raise ValueError(
                f"valuation {self.name!r} attribute {str(self.attribute)!r} has domain "
                f"{declaration.domain.value!r}, not 'item'"
            )
        return declaration.value_type

    def read(self, graph: Graph, reference: ItemRef) -> object:
        """Decode the selected item's canonical lexical value by its XSD type."""
        if reference.tier not in self.tiers:
            raise ValueError(
                f"valuation {self.name!r} excludes tier {str(reference.tier)!r}"
            )
        item = _item(graph, reference)
        attribute = next(
            (
                candidate
                for candidate in item.attributes
                if candidate.name == self.attribute
            ),
            None,
        )
        if attribute is None:
            raise ValueError(
                f"valuation {self.name!r} item {reference.to_data()!r} lacks "
                f"attribute {str(self.attribute)!r}"
            )
        if attribute.value_type in {XsdType.INTEGER, XsdType.DECIMAL}:
            return Decimal(attribute.lexical)
        if attribute.value_type is XsdType.DOUBLE:
            return float(attribute.lexical.replace("INF", "inf"))
        if attribute.value_type is XsdType.BOOLEAN:
            return attribute.lexical == "true"
        return attribute.lexical


@dataclass(frozen=True, slots=True)
class FoldCost:
    """Report measured structural quantities and carrier work for one run."""

    document_size: int
    relation_incidence: int
    index_product_size: int
    carrier_additions: int
    carrier_multiplications: int
    carrier_operation_cost: int
    witness_count: int
    emitted_count: int
    output_cap: int
    witness_operations: int = 0
    ranked_multiplications: int = 0

    @property
    def bound(self) -> int:
        """Return the declared structural/carrier/output work bound."""
        structural = (
            self.document_size + self.relation_incidence
        ) * self.index_product_size
        base = structural * self.carrier_operation_cost
        ranked = (
            (self.document_size + self.relation_incidence) ** 2
            * self.index_product_size
            * self.output_cap**4
            * self.carrier_operation_cost
            if self.ranked_multiplications or self.witness_operations
            else 0
        )
        return base + ranked + min(self.witness_count, self.output_cap)

    @property
    def measured_work(self) -> int:
        """Return measured traversal work plus actually emitted output."""
        structural = (
            self.document_size + self.relation_incidence
        ) * self.index_product_size
        base = structural * self.carrier_operation_cost
        ranked = (
            self.ranked_multiplications + self.witness_operations
        ) * self.carrier_operation_cost
        return base + ranked + self.emitted_count

    @property
    def carrier_work(self) -> int:
        """Return measured semiring-operation work at the declared unit cost."""
        return (
            self.carrier_additions
            + self.carrier_multiplications
            + self.witness_operations
        ) * self.carrier_operation_cost

    def to_data(self) -> dict[str, int]:
        """Return a strict-JSON cost account."""
        data = {
            "document_size": self.document_size,
            "relation_incidence": self.relation_incidence,
            "index_product_size": self.index_product_size,
            "carrier_additions": self.carrier_additions,
            "carrier_multiplications": self.carrier_multiplications,
            "carrier_operation_cost": self.carrier_operation_cost,
            "carrier_work": self.carrier_work,
            "witness_count": self.witness_count,
            "emitted_count": self.emitted_count,
            "output_cap": self.output_cap,
            "bound": self.bound,
            "measured_work": self.measured_work,
        }
        if self.witness_operations or self.ranked_multiplications:
            data["witness_operations"] = self.witness_operations
            data["ranked_multiplications"] = self.ranked_multiplications
        return data


@dataclass(frozen=True, slots=True)
class FoldResult[Value]:
    """Keep semiring values, witness provenance, and measured work separate."""

    values: tuple[tuple[State, Value], ...]
    roots: tuple[State, ...]
    value: Value
    provenance: DerivationProvenance | None
    truncated: bool
    cost: FoldCost
    ranked_witnesses: tuple[RankedWitness[Value], ...] | None = None

    def to_data(self, semiring: Semiring[Value]) -> dict[str, object]:
        """Return deterministic strict-JSON data."""
        data: dict[str, object] = {
            "value": semiring.encode(self.value),
            "provenance": (
                None
                if self.provenance is None
                else [list(path) for path in self.provenance]
            ),
            "truncated": self.truncated,
            "roots": [_state_data(state) for state in self.roots],
            "states": [
                {"state": _state_data(state), "value": semiring.encode(value)}
                for state, value in self.values
            ],
            "cost": self.cost.to_data(),
        }
        if self.ranked_witnesses is not None:
            data["ranked_witnesses"] = [
                {"value": semiring.encode(value), "path": list(path)}
                for value, path in self.ranked_witnesses
            ]
        return data


@dataclass(frozen=True, slots=True)
class FoldCertificate[Value]:
    """Report what discharged one fold's exactness claim, and what it never reached.

    ``compared`` is the honest part. It is true only when the fold's derivations
    were enumerated in full within the declared budget and the combination over
    them was compared against the published value. When it is false the claim
    stood on the law search alone, and a law search that finds no refutation has
    found no refutation — it has not proved anything.

    ``derivations`` counts the structural derivations that were enumerated, which
    includes any the valuation annihilates, so it is a measure of the search and
    not a restatement of a counting fold's value.
    """

    exactness: FoldExactness
    result: FoldResult[Value]
    probes: int
    derivations: int
    compared: bool


@dataclass(frozen=True, slots=True)
class FoldDeclaration[Value]:
    """Bind one named interpretation to a graph, valuation, algebra, and finite DAG.

    A readout or final division above the algebra is not currently provided. If one
    is introduced, it must be declared as part of what the fold profile records. A
    construct whose soundness depends on a property it cannot verify must declare
    that property rather than assume it.

    ``witness_order`` and ``tie_policy`` are one mechanism and are declared together:
    the order names the winner and the policy says what happens where it reports a
    tie, so each without the other is refused. The policy is executable and is read
    at every tie the order reports.

    With ``ranked_output`` the fold instead returns up to ``output_cap`` witnesses
    ranked by the semiring's own order, which its multiplication must preserve
    (``multiply_preserves_witness_order``); a custom ``witness_order`` is refused, and
    so is a ``tie_policy``. Ranked selection breaks an equal-valued tie by the
    canonical witness path, which is a total order over distinct witnesses, so it
    leaves no tie for a policy to decide and would never read one. The resulting
    order is deterministic, and the paths it compares are the fold's own structural
    labels, so it is canonical for a given document rather than globally so.

    ``exactness`` states how the published value stands to the combination over every
    derivation. It defaults to ``UNDECLARED`` and ``run()`` never consults it, because
    the claim is owed where it is relied on rather than where a fixture is built;
    ``check_exactness()`` is the gate that demands and discharges it. Only the two
    refusals a declaration alone can settle are made here.
    """

    name: str
    graph: Graph
    valuation: AttributeValuation
    semiring: Semiring[Value]
    lift: Lift[Value]
    transitions: tuple[FoldTransition, ...]
    index_axes: tuple[tuple[str, ...], ...] = ()
    roots: tuple[ItemRef, ...] = ()
    witness_order: WitnessOrder[Value] | None = None
    tie_policy: TiePolicy | None = None
    output_cap: int = 1
    carrier_operation_cost: int = 1
    ranked_output: bool = False
    exactness: FoldExactness = FoldExactness.UNDECLARED

    def __post_init__(self) -> None:
        """Validate every declaration-level refusal before a fold can run."""
        if not self.name:
            raise ValueError("fold name '' must not be empty")
        if self.output_cap < 1:
            raise ValueError(
                f"fold {self.name!r} output cap {self.output_cap!r} must be positive"
            )
        if self.carrier_operation_cost < 1:
            raise ValueError(
                f"fold {self.name!r} carrier operation cost "
                f"{self.carrier_operation_cost!r} must be positive"
            )
        if self.ranked_output and self.witness_order is not None:
            raise ValueError(
                f"fold {self.name!r} ranked output uses the semiring's canonical "
                "order and conflicts with a custom witness_order"
            )
        if self.ranked_output and self.tie_policy is not None:
            raise ValueError(
                f"fold {self.name!r} declares tie policy {self.tie_policy!r} "
                "alongside ranked output, which breaks an equal-valued tie by the "
                "canonical witness path. That order is total over distinct "
                "witnesses, so no tie survives for a policy to decide and the "
                "declaration would never be read"
            )
        if self.ranked_output and not getattr(
            self.semiring, "multiply_preserves_witness_order", False
        ):
            raise ValueError(
                f"fold {self.name!r} semiring {type(self.semiring).__name__!r} "
                "does not declare multiply_preserves_witness_order"
            )
        if self.witness_order is not None and self.tie_policy is None:
            raise ValueError(
                f"fold {self.name!r} produces witnesses but has no tie policy"
            )
        if self.witness_order is None and self.tie_policy is not None:
            raise ValueError(
                f"fold {self.name!r} declares tie policy {self.tie_policy!r} "
                "but produces no witnesses"
            )
        if self.tie_policy is not None and not isinstance(self.tie_policy, TiePolicy):
            raise ValueError(
                f"fold {self.name!r} has unsupported tie policy {self.tie_policy!r}"
            )
        if any(not isinstance(item, FoldTransition) for item in self.transitions):
            raise ValueError(
                f"fold {self.name!r} transitions must declare AND/OR meaning"
            )
        # Only a fold that makes a claim interrogates the algebra for it: an
        # undeclared fold reads no property it did not already need, so an opaque
        # carrier stays foldable.
        if self.exactness is FoldExactness.DISTRIBUTIVE:
            unchecked = inexact_laws(self.semiring)
            if unchecked:
                law = unchecked[0]
                raise ExactnessRefusal(
                    f"fold {self.name!r} declares DISTRIBUTIVE exactness, but algebra "
                    f"{type(self.semiring).__name__!r} checks {law} only "
                    f"{getattr(self.semiring, law).value!r}; a fold cannot be the "
                    "combination over every derivation when the law that regroups "
                    "the derivations is not checked exactly. Declare APPROXIMATE."
                )
        if self.exactness is FoldExactness.STRUCTURAL and self.semiring.star is None:
            raise ExactnessRefusal(
                f"fold {self.name!r} declares STRUCTURAL exactness, but algebra "
                f"{type(self.semiring).__name__!r} declares no star; a structural "
                "claim owes the warrant that makes its fixpoint converge."
            )
        declared_tiers = {tier.declaration.name for tier in self.graph.tiers}
        for tier in self.valuation.tiers:
            if tier not in declared_tiers:
                raise ValueError(
                    f"fold {self.name!r} domain names undeclared tier {str(tier)!r}"
                )
        value_type = self.valuation.declaration_type(self.graph)
        exact_laws = (
            self.semiring.add_associativity is LawCheck.EXACT
            and self.semiring.multiply_associativity is LawCheck.EXACT
        )
        if value_type is XsdType.DOUBLE and exact_laws:
            raise ValueError(
                f"fold {self.name!r} valuation {self.valuation.name!r} reads "
                f"xsd:double attribute {str(self.valuation.attribute)!r}, but semiring "
                f"{type(self.semiring).__name__!r} claims exact associativity"
            )
        for axis_index, axis in enumerate(self.index_axes):
            if not axis:
                raise ValueError(
                    f"fold {self.name!r} index axis {axis_index!r} is empty"
                )
            if len(set(axis)) != len(axis):
                raise ValueError(
                    f"fold {self.name!r} index axis {axis_index!r} has duplicates"
                )
        declarations = {
            declaration.name: declaration
            for declaration in self.graph.relation_declarations
        }
        if not self.transitions:
            raise ValueError(f"fold {self.name!r} has no declared dependency relations")
        relation_names = [transition.relation for transition in self.transitions]
        if len(set(relation_names)) != len(relation_names):
            raise ValueError(f"fold {self.name!r} has duplicate dependency relations")
        for transition in self.transitions:
            declaration = declarations.get(transition.relation)
            if declaration is None:
                raise ValueError(
                    f"fold {self.name!r} names undeclared bipartite relation "
                    f"{str(transition.relation)!r}"
                )
            if not isinstance(declaration, BipartiteRelationDeclaration):
                kind = declaration.to_data()["kind"]
                raise ValueError(
                    f"fold {self.name!r} dependency relation "
                    f"{str(transition.relation)!r} is declared {kind}; a fold "
                    "reads one parent and one child per incidence and requires "
                    f"a bipartite declaration, so it cannot fold a {kind} relation"
                )
        admitted = set(self._references())
        for root in self.roots:
            if root not in admitted:
                raise ValueError(
                    f"fold {self.name!r} root {root.to_data()!r} is outside its domain"
                )
        self._topology()

    def _references(self) -> tuple[ItemRef, ...]:
        """Return domain items in the graph's canonical order."""
        tiers = set(self.valuation.tiers)
        return tuple(
            reference
            for reference in self.graph.canonical_items()
            if reference.tier in tiers
        )

    def index_coordinates(self) -> tuple[IndexCoordinate, ...]:
        """Construct the declared finite index product in lexical axis order."""
        if not self.index_axes:
            return ((),)
        return tuple(product(*self.index_axes))

    def states(self) -> tuple[State, ...]:
        """Construct the finite domain-item by index-product state space."""
        return tuple(
            (reference, coordinate)
            for coordinate in self.index_coordinates()
            for reference in self._references()
        )

    def _topology(
        self,
    ) -> tuple[
        dict[ItemRef, dict[QualifiedName, tuple[ItemRef, ...]]], tuple[ItemRef, ...]
    ]:
        """Return canonical outgoing incidence and inferred or declared roots."""
        references = self._references()
        admitted = set(references)
        selected = {transition.relation for transition in self.transitions}
        outgoing_lists: dict[ItemRef, dict[QualifiedName, list[ItemRef]]] = {
            reference: {relation: [] for relation in selected}
            for reference in references
        }
        incoming = dict.fromkeys(references, 0)
        for relation in self.graph.relations:
            if (
                relation.declaration in selected
                and isinstance(relation.left, ItemRef)
                and isinstance(relation.right, ItemRef)
                and relation.left in admitted
                and relation.right in admitted
            ):
                outgoing_lists[relation.left][relation.declaration].append(
                    relation.right
                )
                incoming[relation.right] += 1
        order = {reference: index for index, reference in enumerate(references)}
        outgoing = {
            reference: {
                relation: tuple(sorted(children, key=order.__getitem__))
                for relation, children in by_relation.items()
            }
            for reference, by_relation in outgoing_lists.items()
        }
        roots = self.roots or tuple(
            reference for reference in references if incoming[reference] == 0
        )
        if not roots:
            raise ValueError(f"fold {self.name!r} dependency DAG has no root")
        return outgoing, roots

    def _dependency_graph(self) -> _DependencyGraph:
        """Return the canonical topology, roots, order, and merged child adjacency."""
        outgoing, item_roots = self._topology()
        references = self._references()
        canonical_index = {
            reference: index for index, reference in enumerate(references)
        }
        adjacency = {
            reference: tuple(
                dict.fromkeys(
                    child
                    for transition in self.transitions
                    for child in outgoing[reference][transition.relation]
                )
            )
            for reference in references
        }
        return outgoing, item_roots, references, canonical_index, adjacency

    def run(self) -> FoldResult[Value]:
        """Evaluate every state using only the semiring's addition and multiplication."""
        (
            outgoing,
            item_roots,
            references,
            canonical_index,
            adjacency,
        ) = self._dependency_graph()
        cyclic_components = _cyclic_components(references, canonical_index, adjacency)
        component_by_item = {
            reference: component
            for component in cyclic_components
            for reference in component
        }
        coordinates = self.index_coordinates()
        additions = 0
        multiplications = 0
        ranked_multiplications = 0
        ranked_additions = [0]
        witness_operations = [0]
        root_witness_count = 0
        all_values: list[tuple[State, Value]] = []
        root_states: list[State] = []
        total = self.semiring.zero
        selected: tuple[Value, DerivationProvenance] | None = None
        ranked_roots: list[RankedWitness[Value]] = []
        for coordinate in coordinates:
            cache: dict[
                ItemRef,
                tuple[
                    Value, DerivationProvenance, tuple[RankedWitness[Value], ...], int
                ],
            ] = {}
            solving: set[tuple[ItemRef, ...]] = set()

            def cache_component_value(
                component_cache: dict[
                    ItemRef,
                    tuple[
                        Value,
                        DerivationProvenance,
                        tuple[RankedWitness[Value], ...],
                        int,
                    ],
                ],
                member: ItemRef,
                value: Value,
            ) -> None:
                """Cache one SCC value with its unbranched witness metadata."""
                label = _label(self.graph, member)
                paths: DerivationProvenance = (
                    () if value == self.semiring.zero else ((label,),)
                )
                ranked = (
                    ()
                    if not self.ranked_output or value == self.semiring.zero
                    else ((value, (label,)),)
                )
                component_cache[member] = (
                    value,
                    paths,
                    ranked,
                    len(ranked) or len(paths),
                )

            def solve_component(
                component: tuple[ItemRef, ...],
                component_cache: dict[
                    ItemRef,
                    tuple[
                        Value,
                        DerivationProvenance,
                        tuple[RankedWitness[Value], ...],
                        int,
                    ],
                ] = cache,
                active_components: set[tuple[ItemRef, ...]] = solving,
            ) -> None:
                """Resolve one cyclic SCC by the declared ordered trichotomy."""
                nonlocal additions, multiplications
                active_components.add(component)
                members = set(component)
                for member in component:
                    for child in adjacency[member]:
                        if child not in members:
                            visit(child)

                def equation(current: ItemRef, values: dict[ItemRef, Value]) -> Value:
                    """Evaluate one equation under current SCC approximants."""
                    nonlocal additions, multiplications
                    label = _label(self.graph, current)
                    value = self.lift(self.valuation.read(self.graph, current), label)
                    has_children = False
                    for transition in self.transitions:
                        children = outgoing[current][transition.relation]
                        if not children:
                            continue
                        has_children = True
                        child_values = [
                            values[child]
                            if child in members
                            else component_cache[child][0]
                            for child in children
                        ]
                        if transition.combination is ChildCombination.AND:
                            relation_value = self.semiring.one
                            for child_value in child_values:
                                relation_value = self.semiring.multiply(
                                    relation_value, child_value
                                )
                                multiplications += 1
                        else:
                            relation_value = child_values[0]
                            for child_value in child_values[1:]:
                                relation_value = self.semiring.add(
                                    relation_value, child_value
                                )
                                additions += 1
                        value = self.semiring.multiply(value, relation_value)
                        multiplications += 1
                    assert has_children
                    return value

                approximants = dict.fromkeys(component, self.semiring.zero)
                for _ in component:
                    approximants = {
                        member: equation(member, approximants) for member in component
                    }
                if all(value == self.semiring.zero for value in approximants.values()):
                    for member in component:
                        component_cache[member] = (self.semiring.zero, (), (), 0)
                    active_components.remove(component)
                    return

                next_approximants = {
                    member: equation(member, approximants) for member in component
                }
                if self.semiring.star is None and next_approximants == approximants:
                    for member, value in approximants.items():
                        cache_component_value(component_cache, member, value)
                    active_components.remove(component)
                    return

                minimum_nonlinear_children = 2
                nonlinear = next(
                    (
                        member
                        for member in component
                        if sum(
                            child in members
                            for transition in self.transitions
                            if transition.combination is ChildCombination.AND
                            for child in outgoing[member][transition.relation]
                        )
                        >= minimum_nonlinear_children
                    ),
                    None,
                )
                closing_parent, closing_child, closing_relation = next(
                    (member, child, transition.relation)
                    for member in component
                    for transition in self.transitions
                    for child in outgoing[member][transition.relation]
                    if child in members
                )
                member_data = [member.to_data() for member in component]
                edge = (
                    f"{closing_parent.to_data()!r} to {closing_child.to_data()!r} "
                    f"through relation {str(closing_relation)!r}"
                )
                algebra_name = type(self.semiring).__name__
                if nonlinear is not None:
                    chart_item = next(
                        (
                            member
                            for member in component
                            if _item(self.graph, member).attributes
                            and {
                                value.name.local_name: value.lexical
                                for value in _item(self.graph, member).attributes
                            }.get("kind")
                            == "chart-item"
                        ),
                        nonlinear,
                    )
                    chart_attributes = {
                        value.name.local_name: value.lexical
                        for value in _item(self.graph, chart_item).attributes
                    }
                    span = (chart_attributes.get("start"), chart_attributes.get("end"))
                    raise StarRefusal(
                        f"fold {self.name!r} SCC {member_data!r} is nonlinear at "
                        f"zero-width chart item {chart_item.to_data()!r} span {span!r}; "
                        f"closing edge {edge}; algebra {algebra_name}; nonlinear "
                        "least fixpoints are not supported"
                    )

                star = self.semiring.star
                fallback = (
                    f"fold {self.name!r} transitions form a cycle from "
                    f"{closing_parent.to_data()!r} to {closing_child.to_data()!r} "
                    f"through relation {str(closing_relation)!r}"
                )
                if star is None:
                    raise StarRefusal(
                        f"{fallback}; SCC {member_data!r}; closing edge {edge}; "
                        f"algebra {algebra_name} declares no star"
                    )

                def coefficient(current: ItemRef, target: ItemRef) -> Value:
                    """Return the linear coefficient of one internal child."""
                    nonlocal additions, multiplications
                    label = _label(self.graph, current)
                    value = self.lift(self.valuation.read(self.graph, current), label)
                    for transition in self.transitions:
                        children = outgoing[current][transition.relation]
                        if not children:
                            continue
                        internal = tuple(
                            child for child in children if child in members
                        )
                        if transition.combination is ChildCombination.AND:
                            relation_value = self.semiring.one
                            for child in children:
                                child_value = (
                                    self.semiring.one
                                    if child == target
                                    else self.semiring.zero
                                    if child in members
                                    else component_cache[child][0]
                                )
                                relation_value = self.semiring.multiply(
                                    relation_value, child_value
                                )
                                multiplications += 1
                        else:
                            relation_value = self.semiring.zero
                            for child in children:
                                child_value = (
                                    self.semiring.one
                                    if child == target
                                    else self.semiring.zero
                                    if internal or child in members
                                    else component_cache[child][0]
                                )
                                relation_value = self.semiring.add(
                                    relation_value,
                                    child_value,
                                )
                                additions += 1
                        value = self.semiring.multiply(value, relation_value)
                        multiplications += 1
                    return value

                coefficients = {
                    (member, child): coefficient(member, child)
                    for member in component
                    for child in members
                    if child in adjacency[member]
                }
                operand = self.semiring.zero
                for start in component:
                    start_index = canonical_index[start]
                    cycle_paths: list[tuple[ItemRef, Value, frozenset[ItemRef]]] = [
                        (start, self.semiring.one, frozenset((start,)))
                    ]
                    while cycle_paths:
                        current, path_value, seen = cycle_paths.pop()
                        for child in adjacency[current]:
                            if child not in members:
                                continue
                            edge_value = coefficients[(current, child)]
                            cycle_value = self.semiring.multiply(path_value, edge_value)
                            multiplications += 1
                            if child == start:
                                operand = self.semiring.add(operand, cycle_value)
                                additions += 1
                            elif (
                                child not in seen
                                and canonical_index[child] >= start_index
                            ):
                                cycle_paths.append((child, cycle_value, seen | {child}))
                if not star.admits(operand):
                    raise StarRefusal(
                        f"{fallback}; SCC {member_data!r}; closing edge {edge}; "
                        f"algebra {algebra_name}; operand "
                        f"{self.semiring.encode(operand)!r}; warrant {star.name!r} refuses"
                    )
                closure = star.close(operand)
                for member in component:
                    value = self.semiring.multiply(closure, approximants[member])
                    multiplications += 1
                    cache_component_value(component_cache, member, value)
                active_components.remove(component)

            def visit(
                reference: ItemRef,
                state_cache: dict[
                    ItemRef,
                    tuple[
                        Value,
                        DerivationProvenance,
                        tuple[RankedWitness[Value], ...],
                        int,
                    ],
                ] = cache,
            ) -> tuple[
                Value, DerivationProvenance, tuple[RankedWitness[Value], ...], int
            ]:
                """Evaluate one state once for the current index coordinate."""
                nonlocal additions, multiplications, ranked_multiplications
                component = component_by_item.get(reference)
                if component is not None and reference not in state_cache:
                    solve_component(component)
                    return state_cache[reference]
                prepared: dict[ItemRef, tuple[Value, str]] = {}
                in_progress: set[ItemRef] = set()
                work: list[tuple[ItemRef, bool]] = [(reference, False)]
                while work:
                    current, finish = work.pop()
                    if current in state_cache:
                        continue
                    if not finish:
                        label = _label(self.graph, current)
                        local = self.lift(
                            self.valuation.read(self.graph, current), label
                        )
                        prepared[current] = (local, label)
                        in_progress.add(current)
                        work.append((current, True))
                        for transition in reversed(self.transitions):
                            for child in reversed(
                                outgoing[current][transition.relation]
                            ):
                                child_component = component_by_item.get(child)
                                if child_component is not None:
                                    solve_component(child_component)
                                    continue
                                if child not in state_cache:
                                    work.append((child, False))
                        continue

                    local, label = prepared.pop(current)
                    value = local
                    paths: DerivationProvenance = ((label,),)
                    ranked: tuple[RankedWitness[Value], ...] = (
                        ()
                        if not self.ranked_output or local == self.semiring.zero
                        else ((local, (label,)),)
                    )
                    ranked_count = len(ranked)
                    has_children = False
                    for transition in self.transitions:
                        children = outgoing[current][transition.relation]
                        if not children:
                            continue
                        has_children = True
                        child_results = [state_cache[child] for child in children]
                        if transition.combination is ChildCombination.AND:
                            relation_value = self.semiring.one
                            relation_paths: DerivationProvenance = ((),)
                            relation_ranked: tuple[RankedWitness[Value], ...] = (
                                (self.semiring.one, ()),
                            )
                            relation_count = 1
                            for (
                                child_value,
                                child_paths,
                                child_ranked,
                                child_count,
                            ) in child_results:
                                relation_value = self.semiring.multiply(
                                    relation_value, child_value
                                )
                                multiplications += 1
                                relation_paths = tuple(
                                    left + right
                                    for left in relation_paths
                                    for right in child_paths
                                )
                                relation_count *= child_count
                                if self.ranked_output:
                                    ranked_products = len(relation_ranked) * len(
                                        child_ranked
                                    )
                                    multiplications += ranked_products
                                    ranked_multiplications += ranked_products
                                    relation_ranked = self._rank_candidates(
                                        tuple(
                                            (
                                                self.semiring.multiply(
                                                    left_value, right_value
                                                ),
                                                left_path + right_path,
                                            )
                                            for left_value, left_path in relation_ranked
                                            for right_value, right_path in child_ranked
                                        ),
                                        witness_operations,
                                        ranked_additions,
                                    )
                        else:
                            relation_value = child_results[0][0]
                            # The value accumulates, because that is what OR means,
                            # while selection tracks the best sibling separately.
                            # Comparing against the accumulation instead would let
                            # a non-selective semiring outgrow every later sibling.
                            best = child_results[0][:2]
                            for (
                                child_value,
                                child_paths,
                                _child_ranked,
                                _child_count,
                            ) in child_results[1:]:
                                relation_value = self.semiring.add(
                                    relation_value, child_value
                                )
                                additions += 1
                                best = self._select_paths(
                                    best, (child_value, child_paths)
                                )
                            relation_paths = best[1]
                            relation_count = sum(result[3] for result in child_results)
                            if self.ranked_output:
                                relation_ranked = self._rank_candidates(
                                    tuple(
                                        candidate
                                        for _child_value, _child_paths, child_ranked, _child_count in child_results
                                        for candidate in child_ranked
                                    ),
                                    witness_operations,
                                    ranked_additions,
                                )
                        value = self.semiring.multiply(value, relation_value)
                        multiplications += 1
                        paths = tuple(
                            left + right for left in paths for right in relation_paths
                        )
                        ranked_count *= relation_count
                        if self.ranked_output:
                            ranked_products = len(ranked) * len(relation_ranked)
                            multiplications += ranked_products
                            ranked_multiplications += ranked_products
                            ranked = self._rank_candidates(
                                tuple(
                                    (
                                        self.semiring.multiply(left_value, right_value),
                                        left_path + right_path,
                                    )
                                    for left_value, left_path in ranked
                                    for right_value, right_path in relation_ranked
                                ),
                                witness_operations,
                                ranked_additions,
                            )
                    if not has_children:
                        value = self.semiring.multiply(value, self.semiring.one)
                        multiplications += 1
                    state_cache[current] = (value, paths, ranked, ranked_count)
                    in_progress.remove(current)
                return state_cache[reference]

            for root in item_roots:
                state = (root, coordinate)
                root_states.append(state)
                root_value, _root_paths, root_ranked, root_count = visit(root)
                total = self.semiring.add(total, root_value)
                additions += 1
                if self.ranked_output:
                    ranked_roots.extend(root_ranked)
                    root_witness_count += root_count
            for reference in self._references():
                visit(reference)
            all_values.extend(
                ((reference, coordinate), cache[reference][0])
                for reference in self._references()
            )
            if self.witness_order is not None:
                # Selection runs inside the coordinate loop so that provenance
                # folds over the same domain as the value. Reading it afterwards
                # would see only the last coordinate's cache.
                for root in item_roots:
                    candidate = cache[root][:2]
                    if selected is None:
                        selected = candidate
                    else:
                        selected = self._select_paths(selected, candidate)
        complete = None if selected is None else selected[1]
        provenance = None if complete is None else complete[: self.output_cap]
        ranked_witnesses = (
            None
            if not self.ranked_output
            else self._rank_candidates(
                tuple(ranked_roots), witness_operations, ranked_additions
            )
        )
        additions += ranked_additions[0]
        witness_count = (
            root_witness_count
            if ranked_witnesses is not None
            else 0
            if complete is None
            else len(complete)
        )
        cost = FoldCost(
            document_size=len(self.graph.canonical_items()),
            relation_incidence=sum(
                len(children)
                for by_relation in outgoing.values()
                for children in by_relation.values()
            ),
            index_product_size=len(coordinates),
            carrier_additions=additions,
            carrier_multiplications=multiplications,
            carrier_operation_cost=self.carrier_operation_cost,
            witness_count=witness_count,
            emitted_count=(
                len(ranked_witnesses)
                if ranked_witnesses is not None
                else 0
                if provenance is None
                else len(provenance)
            ),
            output_cap=self.output_cap,
            witness_operations=witness_operations[0],
            ranked_multiplications=ranked_multiplications,
        )
        return FoldResult(
            values=tuple(all_values),
            roots=tuple(root_states),
            value=total,
            provenance=provenance,
            truncated=(
                root_witness_count > len(ranked_witnesses)
                if ranked_witnesses is not None
                else witness_count > self.output_cap
            ),
            cost=cost,
            ranked_witnesses=ranked_witnesses,
        )

    def check_exactness(
        self, *, derivation_budget: int = 1024
    ) -> FoldCertificate[Value]:
        """Demand this fold's exactness claim and discharge it, or refuse.

        Every branch bites, and the asymmetry is deliberate. An ``UNDECLARED``
        exactness is refused with **the declaration to be made**, and no fold is
        run for it; a false claim is refused with **a semantic counterexample**.
        Declining to say is not the same as saying the weaker thing.

        The claim is checked two ways, and neither is a carrier swap: re-running
        the fold under another algebra reads the same ``transitions``, so it
        confirms whatever the declaration says rather than testing it.

        The first way is a law search over **probes the fold produces itself**,
        rather than a probe set a caller supplies. Distributivity is what
        regroups a sum over derivations into a fold over shared structure, so a
        carrier that fails it at the values this fold actually reaches cannot be
        folded exactly, whatever its declared ``LawCheck`` says. A carrier that
        cannot evaluate its own laws at its own values raises; that is a defect
        in the carrier boundary, not something to be swallowed here.

        The second way enumerates the derivations and combines them with no
        sharing at all, then compares. It runs only when the whole enumeration
        fits in ``derivation_budget``, and the returned certificate reports
        whether it did. A search that finds no counterexample has found no
        counterexample; it has not proved the claim, and the certificate says
        which of the two happened rather than implying the stronger one.
        """
        if self.exactness is FoldExactness.UNDECLARED:
            raise ExactnessRefusal(
                f"fold {self.name!r} exactness is UNDECLARED: say whether this "
                "fold's value is the combination over every derivation "
                "(DISTRIBUTIVE), a sound approximation of it (APPROXIMATE), or a "
                "starred fixpoint over a cycle with no finite derivation set to "
                "be measured against (STRUCTURAL, which owes its algebra's star "
                "warrant). Not declaring is not the same as declaring APPROXIMATE."
            )
        (
            outgoing,
            item_roots,
            references,
            canonical_index,
            adjacency,
        ) = self._dependency_graph()
        cyclic = _cyclic_components(references, canonical_index, adjacency)
        if self.exactness is FoldExactness.STRUCTURAL:
            if cyclic:
                return FoldCertificate(self.exactness, self.run(), 0, 0, False)
            raise ExactnessRefusal(
                f"fold {self.name!r} declares STRUCTURAL exactness, but its "
                "dependency graph is acyclic, so its derivation set is finite and "
                "there is a combination over every derivation to measure against. "
                "Declare DISTRIBUTIVE or APPROXIMATE."
            )
        if cyclic:
            members = [member.to_data() for member in cyclic[0]]
            raise ExactnessRefusal(
                f"fold {self.name!r} declares {self.exactness.name} exactness over "
                f"a dependency cycle through SCC {members!r}; a cyclic fold has "
                "infinitely many derivations, so the claim names a combination "
                "that does not exist. Declare STRUCTURAL."
            )
        result = self.run()
        probes = self._probes(result)
        witness = self._distributivity_witness(probes)
        compared, unfolded, derivations = self._unfold(
            outgoing, item_roots, derivation_budget
        )
        if self.exactness is FoldExactness.DISTRIBUTIVE:
            if witness is not None:
                raise ExactnessRefusal(self._law_refusal(witness))
            if compared and unfolded != result.value:
                raise ExactnessRefusal(
                    self._derivation_refusal(result.value, unfolded, derivations)
                )
        elif (
            witness is None
            and compared
            and unfolded == result.value
            and not inexact_laws(self.semiring)
        ):
            raise ExactnessRefusal(
                f"fold {self.name!r} declares APPROXIMATE exactness, but its value "
                f"equals the combination over all {derivations} of its derivations "
                f"and algebra {type(self.semiring).__name__!r} checks every "
                "required law exactly, so nothing here approximates anything. An "
                "approximation you cannot exhibit is a declaration that is hiding. "
                "Declare DISTRIBUTIVE."
            )
        return FoldCertificate(
            self.exactness, result, len(probes), derivations, compared
        )

    def _probes(self, result: FoldResult[Value]) -> tuple[Value, ...]:
        """Take the fold's own distinct carrier values in canonical encoded order."""
        candidates = [
            self.semiring.zero,
            self.semiring.one,
            result.value,
            *(value for _state, value in result.values),
        ]
        seen: dict[str, Value] = {}
        for candidate in candidates:
            canonical = self.semiring.decode(self.semiring.encode(candidate))
            seen.setdefault(repr(self.semiring.encode(canonical)), canonical)
        return tuple(seen[key] for key in sorted(seen))[:_PROBE_CAP]

    def _distributivity_witness(
        self, probes: tuple[Value, ...]
    ) -> tuple[str, Value, Value, Value, Value, Value] | None:
        """Return the first probe triple that denies distributivity, or ``None``.

        A Boolean would have been useless: the witness is the finding. The
        enumeration order is the canonical encoded order of the probes, so the
        triple returned is the first in a fixed order rather than a minimized
        one, and it is reported as such.
        """
        for left in probes:
            for first in probes:
                for second in probes:
                    added = self.semiring.add(first, second)
                    got = self.semiring.multiply(left, added)
                    want = self.semiring.add(
                        self.semiring.multiply(left, first),
                        self.semiring.multiply(left, second),
                    )
                    if got != want:
                        return ("left_distributivity", left, first, second, got, want)
                    got = self.semiring.multiply(added, left)
                    want = self.semiring.add(
                        self.semiring.multiply(first, left),
                        self.semiring.multiply(second, left),
                    )
                    if got != want:
                        return ("right_distributivity", left, first, second, got, want)
        return None

    def _law_refusal(
        self, witness: tuple[str, Value, Value, Value, Value, Value]
    ) -> str:
        """Render a denied law with its inputs and both sides evaluated."""
        law, left, first, second, got, want = witness
        encode = self.semiring.encode
        sides = (
            f"a ⊗ (b ⊕ c) = {encode(got)!r}; (a ⊗ b) ⊕ (a ⊗ c) = {encode(want)!r}"
            if law == "left_distributivity"
            else f"(b ⊕ c) ⊗ a = {encode(got)!r}; (b ⊗ a) ⊕ (c ⊗ a) = {encode(want)!r}"
        )
        return (
            f"fold {self.name!r} declares {self.exactness.name} exactness, but "
            f"algebra {type(self.semiring).__name__!r} denies {law} at values this "
            f"fold produces. a = {encode(left)!r}; b = {encode(first)!r}; "
            f"c = {encode(second)!r}; {sides}. Distributivity is what regroups the "
            "combination over every derivation into a fold over shared structure, "
            "so the published value is not that combination."
        )

    def _derivation_refusal(
        self, value: Value, unfolded: Value, derivations: int
    ) -> str:
        """Render a fold value that disagrees with its own enumerated derivations."""
        encode = self.semiring.encode
        return (
            f"fold {self.name!r} declares DISTRIBUTIVE exactness, but its value is "
            f"not the combination over every derivation. Enumerated {derivations} "
            f"derivations: fold value {encode(value)!r}; combination over "
            f"derivations {encode(unfolded)!r}. No probe triple denies "
            "distributivity, so the disagreement is in how this declaration folds "
            "the structure rather than in the carrier's arithmetic."
        )

    def _unfold(
        self,
        outgoing: _Outgoing,
        item_roots: tuple[ItemRef, ...],
        budget: int,
    ) -> tuple[bool, Value, int]:
        """Combine every derivation evaluated on its own, with no shared reuse.

        The multiplication order mirrors ``run()`` exactly, so a disagreement is
        about sharing rather than about the order operands were combined in.
        Only the *lists* are memoized; every derivation still carries its own
        product, which is what makes this an oracle rather than the fold again.
        """
        memo: dict[ItemRef, tuple[Value, ...]] = {}
        produced = 0

        def charge(size: int) -> None:
            """Refuse to enumerate past the declared budget."""
            nonlocal produced
            produced += size
            if produced > budget:
                raise _BudgetExceeded

        def expand(reference: ItemRef) -> None:
            """Evaluate one item's derivation list from its children's lists."""
            label = _label(self.graph, reference)
            values: tuple[Value, ...] = (
                self.lift(self.valuation.read(self.graph, reference), label),
            )
            has_children = False
            for transition in self.transitions:
                children = outgoing[reference][transition.relation]
                if not children:
                    continue
                has_children = True
                options: tuple[Value, ...]
                if transition.combination is ChildCombination.AND:
                    options = (self.semiring.one,)
                    for child in children:
                        options = tuple(
                            self.semiring.multiply(option, value)
                            for option in options
                            for value in memo[child]
                        )
                        charge(len(options))
                else:
                    options = tuple(
                        value for child in children for value in memo[child]
                    )
                values = tuple(
                    self.semiring.multiply(value, option)
                    for value in values
                    for option in options
                )
                charge(len(values))
            if not has_children:
                values = tuple(
                    self.semiring.multiply(value, self.semiring.one) for value in values
                )
            memo[reference] = values

        try:
            for root in item_roots:
                work: list[tuple[ItemRef, bool]] = [(root, False)]
                while work:
                    current, finish = work.pop()
                    if current in memo:
                        continue
                    if finish:
                        expand(current)
                        continue
                    work.append((current, True))
                    work.extend(
                        (child, False)
                        for transition in reversed(self.transitions)
                        for child in reversed(outgoing[current][transition.relation])
                    )
            combined = self.semiring.zero
            derivations = 0
            for root in item_roots:
                for value in memo[root]:
                    combined = self.semiring.add(combined, value)
                    derivations += 1
        except _BudgetExceeded:
            return False, self.semiring.zero, 0
        return True, combined, derivations

    def _rank_candidates(
        self,
        candidates: tuple[RankedWitness[Value], ...],
        witness_operations: list[int],
        ranked_additions: list[int],
    ) -> tuple[RankedWitness[Value], ...]:
        """Return distinct witnesses in declared value and canonical path order.

        The comparison is two-stage, and the second stage is the tie-break. The
        first stage asks the semiring's own addition which value it prefers. It
        reports a tie whenever the sum is neither operand, which covers equal
        values and covers a carrier whose addition aggregates rather than
        selects. The second stage then orders by the witness path, which is
        total over distinct witnesses, so the tie is always broken, and it is
        broken by the document's own canonical labeling rather than by a policy
        a caller could vary. Distinct tied witnesses are all retained, in that
        order, up to ``output_cap``.
        """

        def compare(left: RankedWitness[Value], right: RankedWitness[Value]) -> int:
            """Compare carrier values before deterministic structural paths."""
            witness_operations[0] += 1
            if left[0] != right[0]:
                ranked_additions[0] += 1
                preferred = self.semiring.add(left[0], right[0])
                if preferred == left[0]:
                    return -1
                if preferred == right[0]:
                    return 1
            return (left[1] > right[1]) - (left[1] < right[1])

        distinct: list[RankedWitness[Value]] = []
        for candidate in sorted(candidates, key=cmp_to_key(compare)):
            duplicate = False
            for existing in distinct:
                witness_operations[0] += 1
                if candidate == existing:
                    duplicate = True
                    break
            if not duplicate:
                distinct.append(candidate)
        return tuple(distinct[: self.output_cap])

    def _select_paths(
        self,
        left: tuple[Value, DerivationProvenance],
        right: tuple[Value, DerivationProvenance],
    ) -> tuple[Value, DerivationProvenance]:
        """Apply the declared witness ordering and executable tie policy.

        Both the surviving value and its paths are returned together, so a
        caller cannot substitute an accumulated carrier value for the value
        that actually won. Comparing a candidate against a running total is
        only harmless when addition is selective: under a counting semiring
        the total grows past every alternative and later equals are never
        recognized as ties.
        """
        left_value, left_paths = left
        right_value, right_paths = right
        if self.witness_order is None:
            return left_value, ()
        comparison = self.witness_order(left_value, right_value)
        if comparison < 0:
            return left
        if comparison > 0:
            return right
        if self.tie_policy is TiePolicy.CHOOSE_FIRST:
            return left
        return left_value, tuple(dict.fromkeys((*left_paths, *right_paths)))


@dataclass(frozen=True, slots=True)
class FoldHomomorphism[Value, OtherValue]:
    """Declare a carrier map whose fold result must commute."""

    name: str
    source: FoldDeclaration[Value]
    target: FoldDeclaration[OtherValue]
    mapping: Callable[[Value], OtherValue]

    def __post_init__(self) -> None:
        """Require matching structural interpretations at declaration time."""
        if not self.name:
            raise ValueError("homomorphism name '' must not be empty")
        if (
            self.source.graph != self.target.graph
            or self.source.valuation != self.target.valuation
            or self.source.transitions != self.target.transitions
            or self.source.index_axes != self.target.index_axes
            or self.source.roots != self.target.roots
        ):
            raise ValueError(
                f"homomorphism {self.name!r} source and target structures differ"
            )
        self.check()

    def commutes(self) -> bool:
        """Execute both folds and compare the mapped source with the target."""
        return self.mapping(self.source.run().value) == self.target.run().value

    def check(self) -> None:
        """Refuse a declared homomorphism whose square does not commute."""
        if not self.commutes():
            raise ValueError(f"homomorphism {self.name!r} does not commute with fold")


def _cyclic_components(
    references: tuple[ItemRef, ...],
    canonical_index: dict[ItemRef, int],
    adjacency: dict[ItemRef, tuple[ItemRef, ...]],
) -> tuple[tuple[ItemRef, ...], ...]:
    """Return the strongly connected components that carry a cycle, in canonical order."""
    tarjan_index = 0
    indices: dict[ItemRef, int] = {}
    lowlinks: dict[ItemRef, int] = {}
    stack: list[ItemRef] = []
    on_stack: set[ItemRef] = set()
    components: list[tuple[ItemRef, ...]] = []

    def connect(reference: ItemRef) -> None:
        """Add one canonical vertex with iterative Tarjan traversal."""
        nonlocal tarjan_index
        indices[reference] = tarjan_index
        lowlinks[reference] = tarjan_index
        tarjan_index += 1
        stack.append(reference)
        on_stack.add(reference)
        frames: list[tuple[ItemRef, int]] = [(reference, 0)]
        while frames:
            current, child_index = frames[-1]
            children = adjacency[current]
            if child_index < len(children):
                child = children[child_index]
                frames[-1] = (current, child_index + 1)
                if child not in indices:
                    indices[child] = tarjan_index
                    lowlinks[child] = tarjan_index
                    tarjan_index += 1
                    stack.append(child)
                    on_stack.add(child)
                    frames.append((child, 0))
                elif child in on_stack:
                    lowlinks[current] = min(lowlinks[current], indices[child])
                continue
            frames.pop()
            if frames:
                parent = frames[-1][0]
                lowlinks[parent] = min(lowlinks[parent], lowlinks[current])
            if lowlinks[current] == indices[current]:
                members: list[ItemRef] = []
                while True:
                    member = stack.pop()
                    on_stack.remove(member)
                    members.append(member)
                    if member == current:
                        break
                components.append(
                    tuple(sorted(members, key=canonical_index.__getitem__))
                )

    for reference in references:
        if reference not in indices:
            connect(reference)
    return tuple(
        component
        for component in components
        if len(component) > 1 or component[0] in adjacency[component[0]]
    )


def _item(graph: Graph, reference: ItemRef) -> Item:
    return next(
        tier.items[reference.index]
        for tier in graph.tiers
        if tier.declaration.name == reference.tier
    )


def _label(graph: Graph, reference: ItemRef) -> str:
    """Return an item's durable identity or its canonical structural fallback."""
    return _item(graph, reference).durable_id or _structural_label(reference)


def _structural_label(reference: ItemRef) -> str:
    return f"{reference.tier.namespace}:{reference.tier.local_name}:{reference.index}"


def _state_data(state: State) -> dict[str, object]:
    return {"item": state[0].to_data(), "coordinate": list(state[1])}


__all__ = [
    "AttributeValuation",
    "ChildCombination",
    "DerivationProvenance",
    "ExactnessRefusal",
    "FoldCertificate",
    "FoldCost",
    "FoldDeclaration",
    "FoldExactness",
    "FoldHomomorphism",
    "FoldResult",
    "FoldTransition",
    "IndexCoordinate",
    "Lift",
    "Path",
    "State",
    "TiePolicy",
    "WitnessOrder",
]
