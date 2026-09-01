"""A base type and a registry that make profile satisfaction an enumerable question.

A profile reads a checked role out of ordinary graph declarations. That was a
convention before this module: each profile validated in its own constructor,
and a caller who wanted to know which roles a graph supports had to know the
profiles by name and try them one at a time. This module carries the mechanism.
A :class:`GraphProfile` subclass names the roles it reads, states the conditions
its check decides and the ones it leaves undecided, and implements one
:meth:`GraphProfile.check`. A :class:`ProfileRegistry` holds the registered
profiles and answers :meth:`ProfileRegistry.reports` for a graph and a role
binding, so satisfaction is enumerated rather than assumed.

Registration is where those claims are tested. A profile that names no
condition its check decides is refused, because an always-passing check is worse
than no check at all: a caller counts it as coverage. Refusing that alone would
not be enough, since a profile could name conditions and check none of them, so
every profile carries two witnesses -- one arrangement its check must accept and
one it must refuse -- and the registry runs both. A check that cannot
discriminate between its own two witnesses does not enter the registry.

Satisfaction is reported, never assumed to be total. A profile whose check
decides everything it declares reports :attr:`ProfileOutcome.SATISFIED`. One
that declares a condition its check does not decide reports
:attr:`ProfileOutcome.SATISFIED_AS_CHECKED` and names the unconfirmed
conditions in the report, so a caller cannot read a partial answer as a whole
guarantee. A profile whose required roles are unbound reports
:attr:`ProfileOutcome.NOT_APPLICABLE`: no check ran, and the report says so
instead of counting as a pass.

The mechanism is here; profile content stays with whoever owns the role. The
profiles registered below are the ones this package itself ships. A consumer
registers its own into the same registry and its graphs are enumerated
alongside them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar

from tiergraph.core import (
    AttributeDeclaration,
    AttributeDomain,
    AttributeValue,
    BipartiteRelationDeclaration,
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
)
from tiergraph.root import OrderedRootsProfile, PersistedChoiceProfile
from tiergraph.spanview import SpanViewProfile, span_view
from tiergraph.traversal import OrderedContainment
from tiergraph.value import JsonValueProfile, json_value_graph

type RoleValue = QualifiedName | tuple[QualifiedName, ...]
"""One bound role: a single declaration name, or an ordered set of them."""

type RoleBinding = Mapping[str, RoleValue]
"""Bind each role a profile reads to the declaration name that fills it."""


class ProfileRegistrationRefusal(Exception):
    """Refuse a profile whose registration claims do not hold."""


class ProfileOutcome(Enum):
    """Say what one profile's check established about one graph."""

    SATISFIED = "satisfied"
    SATISFIED_AS_CHECKED = "satisfied_as_checked"
    REFUSED = "refused"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class ProfileReport:
    """Carry what one check established about one graph, and what it did not.

    ``confirmed`` holds the conditions this run decided in the graph's favor and
    ``unconfirmed`` the ones it did not, so the two together always name every
    condition the profile declares. A refused or inapplicable run confirms
    nothing, so all of them are unconfirmed: the check stopped, and which
    conditions it had already passed over is not evidence a caller can use.
    """

    profile: str
    outcome: ProfileOutcome
    confirmed: tuple[str, ...]
    unconfirmed: tuple[str, ...]
    reason: str | None = None

    def to_data(self) -> dict[str, object]:
        """Return deterministic strict-JSON data.

        Both condition lists are emitted even when one is empty, because
        together they name every condition the profile declares and a reader
        cannot reconstruct the second from the first. An accepting outcome with
        a non-empty ``unconfirmed`` is the case that matters: the check passed
        and still left something undecided.
        """
        return {
            "profile": self.profile,
            "outcome": self.outcome.value,
            "confirmed": list(self.confirmed),
            "unconfirmed": list(self.unconfirmed),
            "reason": self.reason,
        }


class GraphProfile(ABC):
    """Declare a graph role whose satisfaction one check decides.

    A subclass names the profile, names the roles it reads, states in prose the
    conditions its check decides and any it leaves undecided, and implements
    :meth:`check`. Those are claims, and :meth:`ProfileRegistry.register` tests
    them before admitting the profile.

    ``decides`` must name at least one condition. ``leaves_undecided`` names
    conditions the profile declares in its own documentation but whose truth
    this check does not establish; naming one costs a weaker outcome rather than
    a refusal, which is the point -- an honest partial check outranks a silent
    one.
    """

    name: ClassVar[str] = ""
    required_roles: ClassVar[tuple[str, ...]] = ()
    optional_roles: ClassVar[tuple[str, ...]] = ()
    decides: ClassVar[tuple[str, ...]] = ()
    leaves_undecided: ClassVar[tuple[str, ...]] = ()

    @classmethod
    @abstractmethod
    def check(cls, graph: Graph, roles: RoleBinding) -> None:
        """Return when ``graph`` satisfies this role, raise ``ValueError`` when not.

        Every required role is bound when this runs. Any other exception is a
        fault in the check rather than a verdict about the graph, and travels
        out to the caller unchanged.
        """

    @classmethod
    @abstractmethod
    def satisfaction_witness(cls) -> tuple[Graph, RoleBinding]:
        """Return an arrangement this profile's check must accept."""

    @classmethod
    @abstractmethod
    def refusal_witness(cls) -> tuple[Graph, RoleBinding]:
        """Return an arrangement this profile's check must refuse."""


def _declared(profile: type[GraphProfile]) -> tuple[str, ...]:
    """Return every condition one profile declares, decided or not."""
    return profile.decides + profile.leaves_undecided


def _report(
    profile: type[GraphProfile], graph: Graph, roles: RoleBinding
) -> ProfileReport:
    """Run one profile's check over a graph and describe what it established."""
    missing = tuple(role for role in profile.required_roles if role not in roles)
    if missing:
        return ProfileReport(
            profile.name,
            ProfileOutcome.NOT_APPLICABLE,
            (),
            _declared(profile),
            f"roles left unbound: {', '.join(missing)}",
        )
    try:
        profile.check(graph, roles)
    except ValueError as error:
        return ProfileReport(
            profile.name,
            ProfileOutcome.REFUSED,
            (),
            _declared(profile),
            str(error),
        )
    if profile.leaves_undecided:
        return ProfileReport(
            profile.name,
            ProfileOutcome.SATISFIED_AS_CHECKED,
            profile.decides,
            profile.leaves_undecided,
        )
    return ProfileReport(profile.name, ProfileOutcome.SATISFIED, profile.decides, ())


_ACCEPTING = (ProfileOutcome.SATISFIED, ProfileOutcome.SATISFIED_AS_CHECKED)


def _unique(values: tuple[str, ...], subject: str, profile: str) -> None:
    """Refuse a declared name list with an empty or repeated entry."""
    if any(not value for value in values):
        raise ProfileRegistrationRefusal(
            f"profile {profile!r} declares an empty {subject} name"
        )
    repeated = sorted({value for value in values if values.count(value) > 1})
    if repeated:
        raise ProfileRegistrationRefusal(
            f"profile {profile!r} repeats {subject} {', '.join(repeated)}"
        )


class ProfileRegistry:
    """Hold explicitly registered profiles and enumerate the ones a graph satisfies.

    Population is explicit. Nothing here scans modules or subclasses for
    profiles to adopt, because a discovered profile is one nobody decided to
    trust: import order would determine what a caller is told a graph satisfies,
    and an accidental subclass would answer for a role its author never
    published. A caller registers what it means to offer.

    Enumeration is ordered by profile name, so the answer does not depend on
    registration order or on interpreter hash state.
    """

    def __init__(self) -> None:
        """Start empty; a registry holds only what a caller registers."""
        self._profiles: dict[str, type[GraphProfile]] = {}

    def register[P: GraphProfile](self, profile: type[P]) -> type[P]:
        """Admit one profile after testing the claims it registers under.

        Refuses a profile that leaves :meth:`GraphProfile.check` or either
        witness abstract, that names no condition its check decides, that
        names one role or condition twice -- a role both required and optional,
        or a condition both decided and left open, is named twice -- that
        repeats a registered name, or whose check does not tell its own two
        witnesses apart. The profile is returned so a definition can register
        itself in place.
        """
        if not (isinstance(profile, type) and issubclass(profile, GraphProfile)):
            raise ProfileRegistrationRefusal(
                f"{profile!r} is not a GraphProfile subclass"
            )
        name = profile.name
        if not name:
            raise ProfileRegistrationRefusal(
                f"profile {profile.__name__} registers under an empty name"
            )
        abstract = sorted(getattr(profile, "__abstractmethods__", frozenset()))
        if abstract:
            raise ProfileRegistrationRefusal(
                f"profile {name!r} leaves {', '.join(abstract)} abstract"
            )
        if name in self._profiles:
            raise ProfileRegistrationRefusal(
                f"profile name {name!r} is already registered"
            )
        if not profile.decides:
            raise ProfileRegistrationRefusal(
                f"profile {name!r} names no condition its check decides; a check "
                "that states nothing cannot be told from one that passes always"
            )
        _unique(profile.required_roles + profile.optional_roles, "role", name)
        _unique(_declared(profile), "condition", name)
        self._verify_witnesses(profile)
        self._profiles[name] = profile
        return profile

    @staticmethod
    def _verify_witnesses(profile: type[GraphProfile]) -> None:
        """Refuse a check that cannot tell its own two witnesses apart."""
        accepted = _report(profile, *profile.satisfaction_witness())
        if accepted.outcome not in _ACCEPTING:
            raise ProfileRegistrationRefusal(
                f"profile {profile.name!r} refuses its own satisfaction witness: "
                f"{accepted.reason}"
            )
        refused = _report(profile, *profile.refusal_witness())
        if refused.outcome is not ProfileOutcome.REFUSED:
            raise ProfileRegistrationRefusal(
                f"profile {profile.name!r} does not refuse its own refusal "
                f"witness; the check reported {refused.outcome.value}"
            )

    def names(self) -> tuple[str, ...]:
        """Return every registered profile name in sorted order."""
        return tuple(sorted(self._profiles))

    def profile(self, name: str) -> type[GraphProfile]:
        """Return one registered profile by name."""
        if name not in self._profiles:
            raise KeyError(f"no profile is registered under {name!r}")
        return self._profiles[name]

    def report(self, name: str, graph: Graph, roles: RoleBinding) -> ProfileReport:
        """Report what one named profile's check establishes about a graph."""
        return _report(self.profile(name), graph, roles)

    def reports(self, graph: Graph, roles: RoleBinding) -> tuple[ProfileReport, ...]:
        """Report every registered profile against a graph, in profile-name order."""
        return tuple(
            _report(self._profiles[name], graph, roles) for name in self.names()
        )

    def satisfied(self, graph: Graph, roles: RoleBinding) -> tuple[ProfileReport, ...]:
        """Return the reports of the profiles whose check ran and accepted.

        These are the ``satisfied`` and ``satisfied_as_checked`` ones. A
        profile reported ``not_applicable`` refused nothing and is still
        absent, because an unanswered question is not an accepted one. Reports
        are returned rather than bare names because a name alone would read as
        a whole guarantee. A report carries its outcome and its unconfirmed
        conditions, so a caller holding one can see how far the answer reaches.
        """
        return tuple(
            report
            for report in self.reports(graph, roles)
            if report.outcome in _ACCEPTING
        )


PROFILES = ProfileRegistry()
"""The registry this package's own profiles register into."""


def _single(roles: RoleBinding, role: str) -> QualifiedName:
    """Read one role bound to a single declaration name."""
    value = roles[role]
    if not isinstance(value, QualifiedName):
        raise ValueError(f"role {role!r} binds a sequence where one name is read")
    return value


def _optional(roles: RoleBinding, role: str) -> QualifiedName | None:
    """Read one role bound to a single name, or absent."""
    return None if role not in roles else _single(roles, role)


def _sequence(roles: RoleBinding, role: str) -> tuple[QualifiedName, ...]:
    """Read one role bound to an ordered set of declaration names."""
    value = roles[role]
    if isinstance(value, QualifiedName):
        raise ValueError(f"role {role!r} binds one name where a sequence is read")
    return value


_WITNESS_NS = "urn:tiergraph:profile-witness"


def _witness_name(local: str) -> QualifiedName:
    """Return one name in the witness namespace."""
    return QualifiedName(_WITNESS_NS, local)


_NODES = _witness_name("nodes")
_NODE_TYPE = _witness_name("node-type")
_MEMBERS = _witness_name("members")
_ROOTS = _witness_name("roots")
_DEPENDS = _witness_name("depends")
_ALTERNATIVES = _witness_name("alternatives")
_DEFAULT = _witness_name("default")


def _node_side(
    minimum: int, maximum: int | None, *, allow_empty: bool = False
) -> RelationSideDeclaration:
    """Declare one item-only relation side over the witness node tier."""
    return RelationSideDeclaration(
        (RelationEndpointKind.ITEM,), (_NODES,), minimum, maximum, allow_empty
    )


def _node_graph(
    declarations: tuple[PolyadicRelationDeclaration, ...],
    instances: tuple[PolyadicRelationInstance, ...],
) -> Graph:
    """Build a three-item witness graph carrying the given polyadic role."""
    return Graph(
        (NamespaceDeclaration("w", _WITNESS_NS),),
        (
            Tier(
                TierDeclaration(_NODES, "Witness nodes"),
                tuple(Item(local) for local in ("a", "b", "c")),
            ),
        ),
        declarations,
        polyadic_relations=instances,
    )


def _node(index: int) -> ItemRef:
    """Return one witness node reference."""
    return ItemRef(_NODES, index)


def _containment_declaration(*, acyclic: bool) -> PolyadicRelationDeclaration:
    """Declare the witness containment relation with a chosen acyclicity promise."""
    return PolyadicRelationDeclaration(
        _MEMBERS,
        _node_side(1, 1),
        _node_side(1, None),
        unique_sources=True,
        distinct_targets=True,
        acyclic=acyclic,
    )


@PROFILES.register
class _OrderedContainment(GraphProfile):
    """Read one ordered, item-only polyadic containment relation."""

    name = "tiergraph.ordered-containment"
    required_roles = ("relation",)
    decides = (
        "the named relation is declared polyadic with item-only sides",
        "the declaration promises source uniqueness and acyclicity",
        "stored incidence is acyclic and gives each item one parent chain",
    )

    @classmethod
    def check(cls, graph: Graph, roles: RoleBinding) -> None:
        """Build the containment view, which validates the relation it reads."""
        OrderedContainment(graph, _single(roles, "relation"))

    @classmethod
    def satisfaction_witness(cls) -> tuple[Graph, RoleBinding]:
        """Return an acyclic containment declaration over one stored instance."""
        graph = _node_graph(
            (_containment_declaration(acyclic=True),),
            (PolyadicRelationInstance(_MEMBERS, (_node(0),), (_node(1), _node(2))),),
        )
        return graph, {"relation": _MEMBERS}

    @classmethod
    def refusal_witness(cls) -> tuple[Graph, RoleBinding]:
        """Return the same shape without the acyclicity promise this role needs."""
        graph = _node_graph(
            (_containment_declaration(acyclic=False),),
            (PolyadicRelationInstance(_MEMBERS, (_node(0),), (_node(1), _node(2))),),
        )
        return graph, {"relation": _MEMBERS}


def _root_graph(roots: tuple[ItemRef, ...]) -> Graph:
    """Build a witness graph whose stored root order is the given sequence."""
    return _node_graph(
        (
            PolyadicRelationDeclaration(
                _ROOTS,
                _node_side(0, 0, allow_empty=True),
                _node_side(1, None),
                distinct_targets=True,
            ),
            PolyadicRelationDeclaration(
                _DEPENDS, _node_side(1, 1), _node_side(1, None)
            ),
        ),
        (
            PolyadicRelationInstance(_ROOTS, (), roots),
            PolyadicRelationInstance(_DEPENDS, (_node(0),), (_node(1),)),
        ),
    )


@PROFILES.register
class _OrderedRoots(GraphProfile):
    """Read a stored root order and reconcile it with enumerated dependencies."""

    name = "tiergraph.ordered-roots"
    required_roles = ("root_relation", "dependency_relations")
    decides = (
        "the root relation is polyadic, item-only, and has an empty source side",
        "the root relation stores exactly one instance with distinct targets",
        "every named dependency relation is polyadic and item-only",
        "every stored root is parentless over the enumerated dependencies",
    )
    leaves_undecided = (
        "roots implied by a dependency relation the caller did not enumerate",
        "whether the stored roots are the whole inferred set, which "
        "OrderedRootsProfile.is_exhaustive answers separately",
    )

    @classmethod
    def check(cls, graph: Graph, roles: RoleBinding) -> None:
        """Build the ordered-roots profile, which reconciles stored and inferred."""
        OrderedRootsProfile(
            graph,
            _single(roles, "root_relation"),
            _sequence(roles, "dependency_relations"),
        )

    @classmethod
    def satisfaction_witness(cls) -> tuple[Graph, RoleBinding]:
        """Return a stored root order that is a subset of the parentless items."""
        return _root_graph((_node(0),)), {
            "root_relation": _ROOTS,
            "dependency_relations": (_DEPENDS,),
        }

    @classmethod
    def refusal_witness(cls) -> tuple[Graph, RoleBinding]:
        """Return a stored root that the dependency relation gives a parent."""
        return _root_graph((_node(1),)), {
            "root_relation": _ROOTS,
            "dependency_relations": (_DEPENDS,),
        }


def _choice_graph(*, subset_declared: bool) -> Graph:
    """Build a witness graph whose default may or may not declare its subset rule."""
    return _node_graph(
        (
            PolyadicRelationDeclaration(
                _ALTERNATIVES,
                _node_side(1, 1),
                _node_side(1, None),
                unique_sources=True,
                distinct_targets=True,
            ),
            PolyadicRelationDeclaration(
                _DEFAULT,
                _node_side(1, 1),
                _node_side(1, 1),
                unique_sources=True,
                distinct_targets=True,
                targets_subset_of=_ALTERNATIVES if subset_declared else None,
            ),
        ),
        (
            PolyadicRelationInstance(_ALTERNATIVES, (_node(0),), (_node(1), _node(2))),
            PolyadicRelationInstance(_DEFAULT, (_node(0),), (_node(1),)),
        ),
    )


@PROFILES.register
class _PersistedChoice(GraphProfile):
    """Read candidate sets and the persisted default stored for each source."""

    name = "tiergraph.persisted-choice"
    required_roles = ("alternatives_relation", "default_relation")
    decides = (
        "both relations are polyadic and item-only",
        "both promise source uniqueness and distinct targets",
        "the default relation selects exactly one target and admits no empty side",
        "the default relation declares its targets a subset of the alternatives",
    )

    @classmethod
    def check(cls, graph: Graph, roles: RoleBinding) -> None:
        """Build the persisted-choice profile over the two named relations."""
        PersistedChoiceProfile(
            graph,
            _single(roles, "alternatives_relation"),
            _single(roles, "default_relation"),
        )

    @classmethod
    def satisfaction_witness(cls) -> tuple[Graph, RoleBinding]:
        """Return a default relation that declares the subset rule this role needs."""
        return _choice_graph(subset_declared=True), {
            "alternatives_relation": _ALTERNATIVES,
            "default_relation": _DEFAULT,
        }

    @classmethod
    def refusal_witness(cls) -> tuple[Graph, RoleBinding]:
        """Return a default relation free to store a target that is no candidate."""
        return _choice_graph(subset_declared=False), {
            "alternatives_relation": _ALTERNATIVES,
            "default_relation": _DEFAULT,
        }


_JSON_ROLES = (
    "node_tier",
    "occurrence_tier",
    "member_relation",
    "value_relation",
    "kind_attribute",
    "key_attribute",
    "string_attribute",
    "boolean_attribute",
    "integer_attribute",
    "double_attribute",
)
_JSON_LOCALS = (
    "nodes",
    "occurrences",
    "members",
    "values",
    "kind",
    "key",
    "string",
    "boolean",
    "integer",
    "double",
)


@PROFILES.register
class _JsonValue(GraphProfile):
    """Read a recursive JSON value out of items joined by ordered relations."""

    name = "tiergraph.json-value"
    required_roles = _JSON_ROLES
    decides = (
        "both named tiers are declared",
        "the member relation carries one node source and ordered distinct "
        "single-parent membership targets",
        "the value relation gives each membership item exactly one value",
        "every node carries a declared kind and the leaf attribute that kind needs",
        "object keys are stored in lexical order, so one object has one encoding",
    )

    @classmethod
    def check(cls, graph: Graph, roles: RoleBinding) -> None:
        """Build the JSON-value profile, which validates every role it reads."""
        JsonValueProfile(graph, *(_single(roles, role) for role in _JSON_ROLES))

    @classmethod
    def _roles(cls, namespace: str) -> RoleBinding:
        """Bind every JSON role to its conventional name in one namespace."""
        return {
            role: QualifiedName(namespace, local)
            for role, local in zip(_JSON_ROLES, _JSON_LOCALS, strict=True)
        }

    @classmethod
    def satisfaction_witness(cls) -> tuple[Graph, RoleBinding]:
        """Return a graph this package builds for one JSON value."""
        namespace = "urn:tiergraph:json-value"
        graph, _, _ = json_value_graph({"a": [1]}, namespace)
        return graph, cls._roles(namespace)

    @classmethod
    def refusal_witness(cls) -> tuple[Graph, RoleBinding]:
        """Return that graph with the node-tier role bound to nothing declared."""
        graph, roles = cls.satisfaction_witness()
        return graph, {**roles, "node_tier": _witness_name("absent")}


_BASE = _witness_name("base")
_BASE_TYPE = _witness_name("base-type")
_SPANS = _witness_name("spans")
_SPAN_TYPE = _witness_name("span-type")
_COVERAGE = _witness_name("covered-by")
_SURFACE = _witness_name("surface")
_VALUE = _witness_name("value")
_SCORE = _witness_name("score")


def _span_graph(*, bipartite_coverage: bool) -> Graph:
    """Build a two-item segmentation covered by one span, on a chosen carrier."""
    coverage: BipartiteRelationDeclaration | PolyadicRelationDeclaration = (
        BipartiteRelationDeclaration(_COVERAGE, _BASE_TYPE, _SPAN_TYPE)
    )
    if not bipartite_coverage:
        coverage = PolyadicRelationDeclaration(
            _COVERAGE,
            RelationSideDeclaration((RelationEndpointKind.ITEM,), (_BASE,), 1, None),
            RelationSideDeclaration((RelationEndpointKind.ITEM,), (_SPANS,), 1, 1),
        )
    return Graph(
        (NamespaceDeclaration("w", _WITNESS_NS),),
        (
            Tier(
                TierDeclaration(_BASE, "Witness base"),
                tuple(
                    Item(
                        f"base-{index}",
                        (AttributeValue(_SURFACE, XsdType.STRING, surface),),
                    )
                    for index, surface in enumerate(("Hi", "!"))
                ),
            ),
            Tier(TierDeclaration(_SPANS, "Witness spans"), (Item("span-0"),)),
        ),
        (
            SimpleRelationDeclaration(_witness_name("base-members"), _BASE, _BASE_TYPE),
            SimpleRelationDeclaration(
                _witness_name("span-members"), _SPANS, _SPAN_TYPE
            ),
            coverage,
        ),
        (
            ()
            if not bipartite_coverage
            else tuple(
                RelationInstance(_COVERAGE, ItemRef(_BASE, index), ItemRef(_SPANS, 0))
                for index in range(2)
            )
        ),
        (
            AttributeDeclaration(_SURFACE, AttributeDomain.ITEM, XsdType.STRING),
            AttributeDeclaration(_VALUE, AttributeDomain.ITEM, XsdType.STRING),
            AttributeDeclaration(_SCORE, AttributeDomain.ITEM, XsdType.DECIMAL),
        ),
        polyadic_relations=(
            ()
            if bipartite_coverage
            else (
                PolyadicRelationInstance(
                    _COVERAGE,
                    (ItemRef(_BASE, 0), ItemRef(_BASE, 1)),
                    (ItemRef(_SPANS, 0),),
                ),
            )
        ),
    )


_SPAN_ROLES = (
    "base_tier",
    "coverage_relation",
    "score_attribute",
    "value_attribute",
    "base_surface_attribute",
)


@PROFILES.register
class _SpanView(GraphProfile):
    """Read a span-oriented segmentation over one base tier.

    ``SpanViewProfile`` names declarations without holding a graph, so
    constructing one settles nothing about any particular graph. This profile
    supplies the missing half: it projects the view, which is where the naming
    is reconciled with what the graph stores.
    """

    name = "tiergraph.span-view"
    required_roles = ("span_tiers", *_SPAN_ROLES)
    optional_roles = ("char_offset_attribute", "alternative_relation")
    decides = (
        "the base tier and every span tier are declared",
        "the coverage and alternative relations are declared bipartite, so a "
        "partial segmentation is not projected as a complete one",
        "every named attribute is declared",
        "every base item carries a surface, and a character offset when one is read",
        "each covered span has contiguous coverage and one boundary anchor",
        "the projected cover is ordered, non-overlapping, and within the base",
    )
    leaves_undecided = (
        "whether every item on a span tier is covered, since an uncovered span "
        "item contributes nothing to the projection",
    )

    @classmethod
    def check(cls, graph: Graph, roles: RoleBinding) -> None:
        """Project the view, which reconciles the named roles with the graph."""
        alternative = _optional(roles, "alternative_relation")
        profile = SpanViewProfile(
            _single(roles, "base_tier"),
            _sequence(roles, "span_tiers"),
            _single(roles, "coverage_relation"),
            _single(roles, "score_attribute"),
            _single(roles, "value_attribute"),
            _single(roles, "base_surface_attribute"),
            _optional(roles, "char_offset_attribute"),
            alternative,
        )
        span_view(graph, profile, alternatives=alternative is not None)

    @classmethod
    def _roles(cls) -> RoleBinding:
        """Bind the witness segmentation's roles."""
        return {
            "base_tier": _BASE,
            "span_tiers": (_SPANS,),
            "coverage_relation": _COVERAGE,
            "score_attribute": _SCORE,
            "value_attribute": _VALUE,
            "base_surface_attribute": _SURFACE,
        }

    @classmethod
    def satisfaction_witness(cls) -> tuple[Graph, RoleBinding]:
        """Return a bipartite coverage relation spanning the whole base tier."""
        return _span_graph(bipartite_coverage=True), cls._roles()

    @classmethod
    def refusal_witness(cls) -> tuple[Graph, RoleBinding]:
        """Return the same coverage carried polyadically, which no span reads."""
        return _span_graph(bipartite_coverage=False), cls._roles()
