"""Schema/codec acceptance conformance constructed from the declaration."""

from __future__ import annotations

import copy
from dataclasses import replace
from typing import cast

import pytest

from tests.conformance import schema_codec as harness_module
from tests.conformance.declared_schema_codec_divergences import (
    LIVE_DIVERGENCES,
    DeclaredDivergence,
)
from tests.conformance.schema_codec import conformance_probes, undeclared_drifts
from tests.test_wire import (
    graph_with_layers,
    polyadic_document,
    rich_graph,
    six_domain_layer,
)
from tiergraph import wire as wire_module
from tiergraph.core import JsonValue
from tiergraph.schema import DOCUMENT, QUALIFIED_NAME, STRING, TIER, Field
from tiergraph.wire import to_data


def test_declaration_derived_schema_codec_acceptance() -> None:
    """Every near-miss follows declared schema, validator, and codec acceptance."""
    drifts = undeclared_drifts(conformance_probes(_seeds(), DOCUMENT))
    assert not drifts, [
        (
            drift.probe.id,
            drift.schema_accepts,
            drift.codec_diagnostic,
            drift.validation_diagnostic,
        )
        for drift in drifts
    ]


def test_every_live_divergence_is_reached_and_removal_exposes_drift() -> None:
    """Each subtraction rule matches real drift and removing it exposes drift."""
    _assert_live_divergences_are_reached(LIVE_DIVERGENCES)


def _assert_live_divergences_are_reached(
    divergences: tuple[DeclaredDivergence, ...],
) -> None:
    """Audit live rules against actual disagreements in the generated probes."""
    probes = conformance_probes(_seeds(), DOCUMENT)
    raw_drifts = undeclared_drifts(probes, ())
    for divergence in divergences:
        matching = tuple(
            drift
            for drift in raw_drifts
            if divergence.matches(drift.probe.id)
            and drift.schema_accepts
            and divergence.validation_accepts
            == (drift.validation_diagnostic == "accepted")
        )
        assert matching, f"inert live divergence: {divergence.name}"
        without = tuple(item for item in divergences if item is not divergence)
        surfaced = undeclared_drifts(tuple(drift.probe for drift in matching), without)
        assert any(drift in surfaced for drift in matching), divergence.name


def test_inert_live_divergence_fails_the_policy_audit() -> None:
    """Demonstrate that inflating the live policy with an inert entry fails."""
    inert = DeclaredDivergence("inert", r":mutation-that-does-not-exist$", "test")
    with pytest.raises(AssertionError, match="inert live divergence"):
        _assert_live_divergences_are_reached((*LIVE_DIVERGENCES, inert))


def _seeds() -> tuple[tuple[str, dict[str, JsonValue]], ...]:
    """Return independent accepted witnesses for both relation carriers."""
    surface = cast(dict[str, JsonValue], polyadic_document())
    graph = cast(dict[str, JsonValue], surface["graph"])
    declarations = cast(list[JsonValue], graph["attribute_declarations"])
    attributes = cast(list[JsonValue], graph["attributes"])
    for local_name, value_type, lexical in (
        ("integer-surface", "integer", "0"),
        ("double-surface", "double", "INF"),
    ):
        name: JsonValue = f"w:{local_name}"
        declarations.append(
            {"name": name, "domain": "document", "value_type": value_type}
        )
        attributes.append({"name": name, "value_type": value_type, "lexical": lexical})
    boundaries = cast(list[JsonValue], graph["position_values"])
    first_boundary = cast(dict[str, JsonValue], boundaries[0])
    boundaries.append(
        {
            "reference": {
                "anchor": {
                    "kind": "tier",
                    "tier": "w:placements",
                },
                "side": "before",
            },
            "attributes": copy.deepcopy(first_boundary["attributes"]),
        }
    )
    layered = graph_with_layers(six_domain_layer())
    layered = replace(
        layered,
        relations=(
            *layered.relations,
            replace(layered.relations[0], durable_id="r1"),
        ),
    )
    return (
        ("binary", to_data(rich_graph())),
        ("polyadic", surface),
        ("layer", to_data(layered)),
        ("seal", to_data(graph_with_layers(seal=1))),
    )


def test_seeds_realize_every_declared_reference_variant() -> None:
    """Witnesses reach both carriers, both anchors, and every lexical declaration."""
    ids = {probe.id for probe in conformance_probes(_seeds(), DOCUMENT)}
    for fragment in (
        ".relations[0].left.index:",
        ".relations[1].sources[0].index:",
        ".position_values[0].reference.anchor.durable_id:",
        ".position_values[1].reference.anchor.tier:",
        ".attributes[0].lexical:",
        ".attributes[1].lexical:",
        ".tiers[0].items[0].attributes[0].lexical:",
        ".position_values[0].attributes[0].lexical:",
    ):
        assert any(fragment in probe_id for probe_id in ids), fragment


def test_layer_and_seal_regions_contribute_probes() -> None:
    """The newest graph regions remain inside the declaration-derived gate."""
    probes = conformance_probes(_seeds(), DOCUMENT)
    layer = tuple(
        probe for probe in probes if probe.seed == "layer" and ".layers" in probe.id
    )
    seal = tuple(
        probe for probe in probes if probe.seed == "seal" and ".seals" in probe.id
    )
    assert layer
    assert seal


def test_new_declared_field_is_covered_without_a_handwritten_probe() -> None:
    """Changing the declaration automatically creates probes for the new surface."""
    original = TIER.fields
    object.__setattr__(TIER, "fields", (*original, Field("extension", STRING)))
    try:
        ids = {probe.id for probe in conformance_probes(_seeds(), DOCUMENT)}
        assert any(".tiers[0].extension:missing" in probe_id for probe_id in ids)
        assert any(".tiers[0].extension:empty" in probe_id for probe_id in ids)
        assert any(".tiers[0].extension:wrong-type" in probe_id for probe_id in ids)
    finally:
        object.__setattr__(TIER, "fields", original)


def test_declared_divergence_is_data_not_harness_logic() -> None:
    """Adding a policy entry changes classification without changing the harness."""
    float_probe = next(
        probe
        for probe in conformance_probes(_seeds(), DOCUMENT)
        if probe.id.endswith(".left.index:wrong-type-float")
    )
    assert undeclared_drifts((float_probe,), ())
    policy = (
        DeclaredDivergence(
            "test addition",
            r":wrong-type-float$",
            "machine-readable policy",
            validation_accepts=False,
        ),
    )
    assert not undeclared_drifts((float_probe,), policy)


def test_harness_finds_validation_error_false_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A validator-only rejection is reported without changing another path."""
    probe = next(
        probe
        for probe in conformance_probes(_seeds(), DOCUMENT)
        if probe.id.endswith(".attributes[0].lexical:empty")
    )
    monkeypatch.setattr(
        harness_module,
        "validation_errors",
        lambda document, format_version: ["injected rejection"],
    )
    assert undeclared_drifts((probe,))[0].validation_diagnostic == "injected rejection"


def test_harness_finds_validation_error_false_acceptance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A validator-only acceptance is reported without changing another path."""
    probe = next(
        probe
        for probe in conformance_probes(_seeds(), DOCUMENT)
        if probe.id.endswith(".format_version:empty")
    )
    monkeypatch.setattr(
        harness_module,
        "validation_errors",
        lambda document, format_version: [],
    )
    assert undeclared_drifts((probe,))[0].validation_diagnostic == "accepted"


def test_harness_rediscovers_missing_nonempty_facets() -> None:
    """Reverting qualified-name facets creates schema-accepted codec refusal."""
    original = DOCUMENT.fields
    # QUALIFIED_NAME is shared below DOCUMENT; mutate its fields through a realized
    # reference obtained from the declaration rather than special-casing a path.
    qualified_original = (QUALIFIED_NAME.pattern, QUALIFIED_NAME.min_length)
    object.__setattr__(QUALIFIED_NAME, "pattern", None)
    object.__setattr__(QUALIFIED_NAME, "min_length", None)
    try:
        probes = tuple(
            probe
            for probe in conformance_probes(_seeds(), DOCUMENT)
            if probe.mutation == "empty"
        )
        drifts = undeclared_drifts(probes)
        assert any(drift.probe.mutation == "empty" for drift in drifts)
    finally:
        object.__setattr__(QUALIFIED_NAME, "pattern", qualified_original[0])
        object.__setattr__(QUALIFIED_NAME, "min_length", qualified_original[1])
        assert DOCUMENT.fields == original


@pytest.mark.parametrize("carrier", ["binary", "polyadic"])
def test_harness_rediscovers_relation_key_guard_regression(
    carrier: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reverting either relation carrier's key guard admits an unknown field."""
    guarded_keys = wire_module._keys

    def reverted_keys(data: dict[str, object], expected: set[str], path: str) -> None:
        if path.startswith("relations[") and (
            (carrier == "polyadic" and "sources" in data)
            or (carrier == "binary" and "sources" not in data)
        ):
            missing = expected - data.keys()
            if missing:
                raise KeyError(min(missing))
            return
        guarded_keys(data, expected, path)

    monkeypatch.setattr(wire_module, "_keys", reverted_keys)
    probes = tuple(
        probe
        for probe in conformance_probes(_seeds(), DOCUMENT)
        if probe.seed == carrier
        and probe.mutation == "unknown-field"
        and ".relations[" in probe.id
    )
    assert any(not drift.schema_accepts for drift in undeclared_drifts(probes))


def test_harness_rediscovers_negative_arity_sentinel_regression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reverting the codec to treat every negative maximum as unbounded drifts."""
    guarded_relation_side = wire_module._relation_side

    def reverted_relation_side(value: object, path: str) -> object:
        data = copy.deepcopy(cast(dict[str, object], value))
        if data.get("maximum") == -2:
            data["maximum"] = -1
        return guarded_relation_side(data, path)

    monkeypatch.setattr(wire_module, "_relation_side", reverted_relation_side)
    probes = tuple(
        probe
        for probe in conformance_probes(_seeds(), DOCUMENT)
        if probe.id.endswith(".maximum:integer--2")
    )
    assert probes
    assert any(not drift.schema_accepts for drift in undeclared_drifts(probes))
