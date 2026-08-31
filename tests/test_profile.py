"""Exercise the profile base type, its registry, and the profiles shipped here.

Every test is labeled REGRESSION (it pins behavior a change must not break) or
CHARACTERIZATION (it records what the tree does today without endorsing it).
The mechanism under test is new, so a name-absence failure against unmodified
source is structurally unavoidable for all of them: nothing here could import.
The behavioral content was checked the other way instead, by mutating
``tiergraph.profile`` one behavior at a time -- dropping the unbound-role
short circuit, reporting a refusal as satisfaction, collapsing the partial
outcome into the whole one, skipping either witness at registration, admitting
a profile that decides nothing, enumerating in registration order, dropping the
filter in ``satisfied``, reporting a refused run's conditions as confirmed, and
reducing the span-view check to naming -- and confirming each one fails a test
here. A passing run is therefore evidence about behavior rather than about the
presence of a name.
"""

from __future__ import annotations

from typing import ClassVar, cast

import pytest

from tiergraph import (
    PROFILES,
    Graph,
    GraphProfile,
    Item,
    ItemRef,
    NamespaceDeclaration,
    PolyadicRelationDeclaration,
    PolyadicRelationInstance,
    ProfileOutcome,
    ProfileRegistrationRefusal,
    ProfileRegistry,
    ProfileReport,
    QualifiedName,
    RelationEndpointKind,
    RelationSideDeclaration,
    RoleBinding,
    Tier,
    TierDeclaration,
)

NS = "urn:tiergraph:test:profile"


def name(local: str) -> QualifiedName:
    """Return one name in the profile-test namespace."""
    return QualifiedName(NS, local)


NODES = name("nodes")
MEMBERS = name("members")


def side(
    minimum: int, maximum: int | None, *, allow_empty: bool = False
) -> RelationSideDeclaration:
    """Declare one item-only side over the test node tier."""
    return RelationSideDeclaration(
        (RelationEndpointKind.ITEM,), (NODES,), minimum, maximum, allow_empty
    )


def fixture(*, acyclic: bool = True) -> Graph:
    """Build a three-node graph carrying one ordered containment relation."""
    return Graph(
        (NamespaceDeclaration("t", NS),),
        (
            Tier(
                TierDeclaration(NODES, "Test nodes"),
                tuple(Item(local) for local in ("a", "b", "c")),
            ),
        ),
        (
            PolyadicRelationDeclaration(
                MEMBERS,
                side(1, 1),
                side(1, None),
                unique_sources=True,
                distinct_targets=True,
                acyclic=acyclic,
            ),
        ),
        polyadic_relations=(
            PolyadicRelationInstance(
                MEMBERS, (ItemRef(NODES, 0),), (ItemRef(NODES, 1), ItemRef(NODES, 2))
            ),
        ),
    )


class Complete(GraphProfile):
    """Refuse a graph with fewer than two items, deciding everything it declares."""

    name: ClassVar[str] = "test.complete"
    required_roles: ClassVar[tuple[str, ...]] = ("tier",)
    optional_roles: ClassVar[tuple[str, ...]] = ()
    decides: ClassVar[tuple[str, ...]] = ("the named tier holds at least two items",)
    leaves_undecided: ClassVar[tuple[str, ...]] = ()

    @classmethod
    def check(cls, graph: Graph, roles: RoleBinding) -> None:
        """Refuse a tier that is absent or too short."""
        wanted = roles["tier"]
        tier = next(
            (item for item in graph.tiers if item.declaration.name == wanted), None
        )
        if tier is None or len(tier.items) < 2:
            raise ValueError(f"tier {wanted!r} holds fewer than two items")

    @classmethod
    def satisfaction_witness(cls) -> tuple[Graph, RoleBinding]:
        """Return the three-node fixture, which holds enough items."""
        return fixture(), {"tier": NODES}

    @classmethod
    def refusal_witness(cls) -> tuple[Graph, RoleBinding]:
        """Return the same graph with the role bound to nothing declared."""
        return fixture(), {"tier": name("absent")}


class Partial(Complete):
    """Decide the same condition while declaring one it leaves open."""

    name = "test.partial"
    leaves_undecided = ("whether any item carries an attribute",)


def registry_with(*profiles: type[GraphProfile]) -> ProfileRegistry:
    """Return a fresh registry holding exactly the given profiles."""
    registry = ProfileRegistry()
    for profile in profiles:
        registry.register(profile)
    return registry


def test_registry_starts_empty_and_registers_explicitly() -> None:
    """REGRESSION: population is explicit, and enumeration is name-ordered."""
    registry = ProfileRegistry()
    assert registry.names() == ()
    assert registry.register(Partial) is Partial
    registry.register(Complete)
    assert registry.names() == ("test.complete", "test.partial")
    assert registry.profile("test.complete") is Complete


def test_unknown_profile_name_is_refused() -> None:
    """REGRESSION: a name nobody registered is an error, not an empty answer."""
    with pytest.raises(KeyError, match="no profile is registered"):
        ProfileRegistry().profile("test.complete")


def test_satisfaction_is_enumerated_with_its_reach() -> None:
    """REGRESSION: a complete check reads SATISFIED, a partial one names its gap."""
    registry = registry_with(Complete, Partial)
    reports = registry.reports(fixture(), {"tier": NODES})
    assert [report.profile for report in reports] == ["test.complete", "test.partial"]
    assert reports[0] == ProfileReport(
        "test.complete",
        ProfileOutcome.SATISFIED,
        ("the named tier holds at least two items",),
        (),
    )
    assert reports[1].outcome is ProfileOutcome.SATISFIED_AS_CHECKED
    assert reports[1].unconfirmed == ("whether any item carries an attribute",)
    assert registry.satisfied(fixture(), {"tier": NODES}) == reports


def test_refusal_confirms_nothing_and_carries_its_reason() -> None:
    """REGRESSION: a refused run confirms no condition and says why."""
    registry = registry_with(Complete)
    report = registry.report("test.complete", fixture(), {"tier": name("absent")})
    assert report.outcome is ProfileOutcome.REFUSED
    assert report.confirmed == ()
    assert report.unconfirmed == Complete.decides
    assert "fewer than two items" in (report.reason or "")
    assert registry.satisfied(fixture(), {"tier": name("absent")}) == ()


def test_unbound_role_is_not_applicable_rather_than_a_pass() -> None:
    """REGRESSION: an unbound required role never counts as coverage."""
    registry = registry_with(Partial)
    report = registry.report("test.partial", fixture(), {})
    assert report.outcome is ProfileOutcome.NOT_APPLICABLE
    assert report.confirmed == ()
    assert report.unconfirmed == Partial.decides + Partial.leaves_undecided
    assert report.reason == "roles left unbound: tier"
    assert registry.satisfied(fixture(), {}) == ()


def test_non_profile_registration_is_refused() -> None:
    """REGRESSION: only a GraphProfile subclass may enter a registry."""
    with pytest.raises(ProfileRegistrationRefusal, match="not a GraphProfile"):
        ProfileRegistry().register(cast(type[GraphProfile], int))


def test_unnamed_profile_registration_is_refused() -> None:
    """REGRESSION: a profile with no registry name cannot be looked up."""

    class Unnamed(Complete):
        """Carry no registry name."""

        name = ""

    with pytest.raises(ProfileRegistrationRefusal, match="empty name"):
        ProfileRegistry().register(Unnamed)


def test_abstract_profile_registration_is_refused() -> None:
    """REGRESSION: a profile missing check or a witness cannot be registered."""

    class Abstract(GraphProfile):
        """Declare a condition while implementing nothing."""

        name: ClassVar[str] = "test.abstract"
        decides: ClassVar[tuple[str, ...]] = ("something",)

    with pytest.raises(ProfileRegistrationRefusal, match="leaves check, "):
        ProfileRegistry().register(cast(type[GraphProfile], Abstract))


def test_duplicate_registration_is_refused() -> None:
    """REGRESSION: one name means one profile, so a second is an error."""
    registry = registry_with(Complete)
    with pytest.raises(ProfileRegistrationRefusal, match="already registered"):
        registry.register(Complete)


def test_profile_that_decides_nothing_is_refused() -> None:
    """REGRESSION: a profile stating no condition cannot be registered at all.

    This is the failure the mechanism exists to prevent: a registered profile
    with nothing to decide is counted by callers as coverage it never gave.
    """

    class Vacuous(Complete):
        """State no condition its check decides."""

        name = "test.vacuous"
        decides = ()

    with pytest.raises(ProfileRegistrationRefusal, match="names no condition"):
        ProfileRegistry().register(Vacuous)


def test_always_passing_check_is_refused() -> None:
    """REGRESSION: a check that accepts its own refusal witness stays out.

    A profile may name conditions and check none of them. Registration runs the
    arrangement the profile itself says it must refuse; accepting it proves the
    check does not discriminate, and the registry refuses.
    """

    class AlwaysPasses(Complete):
        """Name a condition and decide nothing."""

        name = "test.always-passes"

        @classmethod
        def check(cls, graph: Graph, roles: RoleBinding) -> None:
            """Accept every graph."""

    with pytest.raises(
        ProfileRegistrationRefusal, match="does not refuse its own refusal witness"
    ):
        ProfileRegistry().register(AlwaysPasses)


def test_always_refusing_check_is_refused() -> None:
    """REGRESSION: a check that refuses its own satisfaction witness stays out."""

    class AlwaysRefuses(Complete):
        """Refuse every graph, including the one it says it accepts."""

        name = "test.always-refuses"

        @classmethod
        def check(cls, graph: Graph, roles: RoleBinding) -> None:
            """Refuse every graph."""
            raise ValueError("no")

    with pytest.raises(
        ProfileRegistrationRefusal, match="refuses its own satisfaction witness: no"
    ):
        ProfileRegistry().register(AlwaysRefuses)


def test_witness_that_leaves_a_role_unbound_is_refused() -> None:
    """REGRESSION: a refusal witness must reach the check, not skip it."""

    class Unreachable(Complete):
        """Offer a refusal witness that binds no role."""

        name = "test.unreachable"

        @classmethod
        def refusal_witness(cls) -> tuple[Graph, RoleBinding]:
            """Return a binding that leaves the required role unbound."""
            return fixture(), {}

    with pytest.raises(ProfileRegistrationRefusal, match="reported not_applicable"):
        ProfileRegistry().register(Unreachable)


@pytest.mark.parametrize(
    ("required", "optional", "message"),
    [
        (("tier", ""), (), "empty role name"),
        (("tier", "tier"), (), "repeats role tier"),
        (("tier",), ("tier",), "repeats role tier"),
    ],
)
def test_role_declarations_must_be_distinct_and_named(
    required: tuple[str, ...], optional: tuple[str, ...], message: str
) -> None:
    """REGRESSION: a role list that names nothing usable is refused."""

    class Roles(Complete):
        """Declare roles supplied by the parameter set."""

        name = "test.roles"
        required_roles = required
        optional_roles = optional

    with pytest.raises(ProfileRegistrationRefusal, match=message):
        ProfileRegistry().register(Roles)


@pytest.mark.parametrize(
    ("decides", "undecided", "message"),
    [
        (("",), (), "empty condition name"),
        (("one", "one"), (), "repeats condition one"),
        (("one",), ("one",), "repeats condition one"),
    ],
)
def test_condition_declarations_must_be_distinct_and_named(
    decides: tuple[str, ...], undecided: tuple[str, ...], message: str
) -> None:
    """REGRESSION: a condition cannot be blank, nor both decided and open."""

    class Conditions(Complete):
        """Declare conditions supplied by the parameter set."""

        name = "test.conditions"

    Conditions.decides = decides
    Conditions.leaves_undecided = undecided
    with pytest.raises(ProfileRegistrationRefusal, match=message):
        ProfileRegistry().register(Conditions)


def test_shipped_registry_holds_the_profiles_this_package_owns() -> None:
    """REGRESSION: importing the package registers its own profiles, once each."""
    assert PROFILES.names() == (
        "tiergraph.json-value",
        "tiergraph.ordered-containment",
        "tiergraph.ordered-roots",
        "tiergraph.persisted-choice",
        "tiergraph.span-view",
    )


@pytest.mark.parametrize("registered", PROFILES.names())
def test_every_shipped_profile_discriminates_its_witnesses(registered: str) -> None:
    """REGRESSION: each shipped check accepts one witness and refuses the other."""
    profile = PROFILES.profile(registered)
    graph, roles = profile.satisfaction_witness()
    accepted = PROFILES.report(registered, graph, roles)
    assert accepted.outcome in (
        ProfileOutcome.SATISFIED,
        ProfileOutcome.SATISFIED_AS_CHECKED,
    )
    graph, roles = profile.refusal_witness()
    assert PROFILES.report(registered, graph, roles).outcome is ProfileOutcome.REFUSED


def test_ordered_roots_reports_the_reach_of_its_own_reconciliation() -> None:
    """REGRESSION: enumerated-dependency reconciliation is a partial answer."""
    report = PROFILES.report(
        "tiergraph.ordered-roots",
        *PROFILES.profile("tiergraph.ordered-roots").satisfaction_witness(),
    )
    assert report.outcome is ProfileOutcome.SATISFIED_AS_CHECKED
    assert any("did not enumerate" in item for item in report.unconfirmed)
    assert any("is_exhaustive" in item for item in report.unconfirmed)


def test_span_view_check_projects_rather_than_only_naming() -> None:
    """REGRESSION: the span-view check reads the graph, not just the names.

    ``SpanViewProfile`` holds no graph, so constructing one settles nothing.
    Binding a base-surface attribute the graph declares but no base item
    carries must refuse, which only projection can find.
    """
    profile = PROFILES.profile("tiergraph.span-view")
    graph, roles = profile.satisfaction_witness()
    swapped = {**roles, "base_surface_attribute": roles["value_attribute"]}
    report = PROFILES.report("tiergraph.span-view", graph, swapped)
    assert report.outcome is ProfileOutcome.REFUSED
    assert "lacks surface attribute" in (report.reason or "")


def test_span_view_reads_an_optional_role_when_it_is_bound() -> None:
    """CHARACTERIZATION: an optional role that names nothing declared refuses."""
    profile = PROFILES.profile("tiergraph.span-view")
    graph, roles = profile.satisfaction_witness()
    bound = {**roles, "char_offset_attribute": name("offset")}
    report = PROFILES.report("tiergraph.span-view", graph, bound)
    assert report.outcome is ProfileOutcome.REFUSED
    assert "character offset attribute" in (report.reason or "")


def test_a_role_bound_to_the_wrong_shape_is_refused() -> None:
    """REGRESSION: a sequence where one name is read, and the reverse, refuse."""
    graph, roles = PROFILES.profile(
        "tiergraph.ordered-containment"
    ).satisfaction_witness()
    sequence = PROFILES.report(
        "tiergraph.ordered-containment",
        graph,
        {"relation": cast(tuple[QualifiedName, ...], tuple(roles.values()))},
    )
    assert sequence.outcome is ProfileOutcome.REFUSED
    assert "binds a sequence where one name is read" in (sequence.reason or "")

    graph, roles = PROFILES.profile("tiergraph.ordered-roots").satisfaction_witness()
    single = PROFILES.report(
        "tiergraph.ordered-roots",
        graph,
        {**roles, "dependency_relations": name("depends")},
    )
    assert single.outcome is ProfileOutcome.REFUSED
    assert "binds one name where a sequence is read" in (single.reason or "")


def test_shipped_profiles_enumerate_over_an_unrelated_graph() -> None:
    """REGRESSION: a graph binding no role is reported, never silently omitted."""
    reports = PROFILES.reports(fixture(), {})
    assert len(reports) == len(PROFILES.names())
    assert {report.outcome for report in reports} == {ProfileOutcome.NOT_APPLICABLE}
    assert PROFILES.satisfied(fixture(), {}) == ()


def test_containment_role_is_enumerable_on_an_ordinary_graph() -> None:
    """REGRESSION: the containment profile answers for a graph built elsewhere."""
    satisfied = PROFILES.satisfied(fixture(), {"relation": MEMBERS})
    assert [report.profile for report in satisfied] == ["tiergraph.ordered-containment"]
    assert satisfied[0].outcome is ProfileOutcome.SATISFIED
    refused = PROFILES.report(
        "tiergraph.ordered-containment", fixture(acyclic=False), {"relation": MEMBERS}
    )
    assert refused.outcome is ProfileOutcome.REFUSED
    assert "requires declared acyclicity" in (refused.reason or "")


def test_report_serialization_emits_both_condition_lists() -> None:
    """REGRESSION: an empty condition list is data, not an omission.

    Together the two lists name every condition the profile declares, and a
    reader cannot reconstruct one from the other. The case that matters is an
    accepting outcome carrying a non-empty ``unconfirmed``: the check passed and
    still left something undecided, which a wire form dropping empty lists would
    render indistinguishable from a check that decided everything.
    """
    report = ProfileReport(
        profile="p",
        outcome=ProfileOutcome.SATISFIED,
        confirmed=("a",),
        unconfirmed=(),
    )
    data = report.to_data()

    assert data["confirmed"] == ["a"]
    assert data["unconfirmed"] == []
    assert data["outcome"] == ProfileOutcome.SATISFIED.value
    assert data["reason"] is None
    assert set(data) == {"profile", "outcome", "confirmed", "unconfirmed", "reason"}
