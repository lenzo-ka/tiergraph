"""Native JSON embedding preserves a populated graph without owning its values."""

from dataclasses import replace
from typing import cast

import pytest

import tiergraph as tg
from tests.test_wire import LINKS, WORDS, every_reference_variant_graph, value
from tiergraph.core import JsonValue


def populated_source() -> tg.Graph:
    """Reuse the all-reference fixture and populate every base attribute domain."""
    editor = every_reference_variant_graph().edit()
    editor.set_attribute(None, value(tg.AttributeDomain.DOCUMENT, "document"))
    editor.set_attribute(WORDS, value(tg.AttributeDomain.TIER, "tier"))
    editor.set_attribute(tg.ItemRef(WORDS, 0), value(tg.AttributeDomain.ITEM, "item"))
    editor.set_attribute(
        tg.BoundaryRef(WORDS, 1), value(tg.AttributeDomain.BOUNDARY, "boundary")
    )
    editor.set_attribute(
        LINKS, value(tg.AttributeDomain.RELATION_DECLARATION, "declaration")
    )
    editor.set_attribute("r0", value(tg.AttributeDomain.RELATION_INSTANCE, "binary"))
    editor.set_attribute(
        "p0",
        value(tg.AttributeDomain.RELATION_INSTANCE, "polyadic"),
    )
    return editor.freeze()


def test_embedding_preserves_populated_native_carriers_and_codec() -> None:
    """Source facts and references survive, including sealed and layered ones."""
    source = populated_source()
    before = tg.dump_bytes(source)
    payload: JsonValue = {"z": [False, 0, None], "a": {"label": "catalog metadata"}}
    namespace = tg.NamespaceDeclaration("annotation", "urn:example:annotation")
    graph, profile, root = tg.embed_json_value(source, payload, namespace=namespace)
    assert root == tg.ItemRef(tg.QualifiedName(namespace.namespace, "nodes"), 0)
    assert graph.resolve_item(root) == root
    assert profile.graph is graph
    assert profile.value(root) == payload
    observed = profile.value(root)
    assert isinstance(observed, dict) and isinstance(observed["z"], list)
    assert type(observed["z"][0]) is bool
    assert type(observed["z"][1]) is int
    assert observed["z"][2] is None
    assert list(observed) == ["a", "z"]
    for carrier in (
        "namespaces",
        "tiers",
        "relation_declarations",
        "relations",
        "attribute_declarations",
        "boundary_values",
        "attributes",
        "polyadic_relations",
        "seals",
        "layers",
    ):
        original = getattr(source, carrier)
        assert original, carrier
        assert all(item in getattr(graph, carrier) for item in original), carrier
    assert source.tiers[0].attributes and source.tiers[0].items[0].attributes
    assert source.relations[0].attributes and source.polyadic_relations[0].attributes
    assert any(declaration.attributes for declaration in source.relation_declarations)
    assert graph.seals == source.seals and graph.layers == source.layers
    for item in (tg.DurableItemRef("w0"), tg.DurableItemRef("w1")):
        assert graph.resolve_item(item) == source.resolve_item(item)
    anchor = tg.DurableBoundaryRef(tg.DurableItemRef("w1"), tg.BoundarySide.BEFORE)
    assert graph.resolve_boundary(anchor) == source.resolve_boundary(anchor)
    # Embedding has no inferred owner relation from any original source item.
    assert all(
        endpoint.tier.namespace == namespace.namespace
        for relation in graph.polyadic_relations[len(source.polyadic_relations) :]
        for endpoint in relation.sources
        if isinstance(endpoint, tg.ItemRef)
    )
    restored = tg.loads(tg.dump_bytes(graph))
    assert replace(profile, graph=restored).value(root) == payload
    assert tg.dump_bytes(restored) == tg.dump_bytes(graph)
    assert tg.dump_bytes(source) == before
    repeated, _, repeated_root = tg.embed_json_value(
        source, payload, namespace=namespace
    )
    assert repeated_root == root and tg.dump_bytes(repeated) == tg.dump_bytes(graph)


@pytest.mark.parametrize("payload", [None, [], {}, "value", [False, 0]])
def test_embedding_reuses_standalone_constructor_and_explicit_prefix(
    payload: JsonValue,
) -> None:
    """An empty source needs no private representation or fixed prefix alias."""
    namespace = tg.NamespaceDeclaration("chosen", "urn:example:value")
    source = tg.Graph((), (), ())
    graph, profile, root = tg.embed_json_value(source, payload, namespace=namespace)
    standalone, original_profile, original_root = tg.json_value_graph(
        payload, namespace.namespace
    )
    assert graph == replace(standalone, namespaces=(namespace,))
    assert root == original_root
    assert profile.value(root) == original_profile.value(original_root)
    assert graph.namespaces == (namespace,)


@pytest.mark.parametrize("collision", ["prefix", "uri", "both"])
@pytest.mark.parametrize("payload", [None, [], {}])
def test_embedding_collision_is_explicit_and_failure_is_atomic(
    collision: str, payload: JsonValue
) -> None:
    """Even null cannot alias existing profile declarations or serialized names."""
    source = populated_source()
    before = tg.dump_bytes(source)
    existing = source.namespaces[0]
    selected = tg.NamespaceDeclaration(
        existing.prefix if collision != "uri" else "fresh",
        existing.namespace if collision != "prefix" else "urn:example:fresh",
    )
    with pytest.raises(
        tg.GraphValidationError, match="unused namespace URI and prefix"
    ):
        tg.embed_json_value(source, payload, namespace=selected)
    assert tg.dump_bytes(source) == before


@pytest.mark.parametrize(
    "payload", [float("nan"), float("inf"), {1: "bad key"}, object()]
)
def test_embedding_invalid_value_leaves_no_partial_graph(payload: object) -> None:
    """The native JSON constructor remains the authority on value admission."""
    source = populated_source()
    before = tg.dump_bytes(source)
    namespace = tg.NamespaceDeclaration("fresh", "urn:example:fresh")
    with pytest.raises(ValueError):
        tg.embed_json_value(source, cast(JsonValue, payload), namespace=namespace)
    assert tg.dump_bytes(source) == before
    graph, profile, root = tg.embed_json_value(source, None, namespace=namespace)
    assert profile.value(root) is None and graph.resolve_item(root) == root
