"""Canonical graph equivalence views and deterministic fingerprints."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum

from tiergraph.core import (
    BoundaryRef,
    DocumentRef,
    DurableBoundaryRef,
    DurableItemRef,
    DurablePolyadicRef,
    DurableRelationRef,
    Graph,
    GraphCarrier,
    ItemRef,
    JsonValue,
    OrphanedSubject,
    PolyadicInstanceRef,
    QualifiedName,
    RelationDeclarationRef,
    RelationInstanceRef,
    TierRef,
)

type _Element = tuple[str, str]
type _DurableIndexes = tuple[dict[str, int], dict[str, int]]

_FINGERPRINT_VERSION = "tiergraph-equivalence/1"


class EquivalenceView(StrEnum):
    """Choose a fixed, graph-wide observational projection for comparison.

    A view says what a comparison reads; it does not declare a domain symmetry.
    ``FUNCTIONAL`` omits carried durable IDs, namespace prefixes, and reference
    spellings. ``IDENTIFIED`` also reads durable IDs. ``EXACT`` reads all three
    and is graph equality (``==``). Facts within each layer are canonicalized
    at construction, so their supplied order is not observed by any view.
    Declared order is content under every view. Per-tier declared
    order-insensitivity is a separate, future concept.

    Values compare after construction-time canonicalization with no tolerance;
    tolerance belongs to a distance measure, not equivalence.
    """

    FUNCTIONAL = "functional"
    IDENTIFIED = "identified"
    EXACT = "exact"


def _view(value: EquivalenceView | str) -> EquivalenceView:
    """Return a checked view, accepting its public string spelling as a convenience."""
    return EquivalenceView(value)


def _json(value: JsonValue) -> str:
    """Return one deterministic, unambiguous value spelling."""
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _element(name: str, value: JsonValue) -> _Element:
    """Pair a stable element name with its canonical value spelling."""
    return name, _json(value)


def _name(value: QualifiedName) -> dict[str, JsonValue]:
    """Return an expanded qualified name, independently of prefixes."""
    return value.to_data()


def _carrier(value: QualifiedName | GraphCarrier) -> dict[str, JsonValue]:
    """Return a tagged seal or orphan carrier."""
    if isinstance(value, QualifiedName):
        return {"kind": "tier", "tier": _name(value)}
    return {"kind": "graph", "name": value.value}


def _item(graph: Graph, value: ItemRef | DurableItemRef) -> dict[str, JsonValue]:
    """Resolve an item identity to its structural coordinate."""
    coordinate = graph.resolve_item(value)
    return {
        "kind": "item",
        "tier": _name(coordinate.tier),
        "index": coordinate.index,
    }


def _boundary(
    graph: Graph, value: BoundaryRef | DurableBoundaryRef
) -> dict[str, JsonValue]:
    """Resolve a boundary identity to its structural coordinate."""
    coordinate = graph.resolve_boundary(value)
    return {
        "kind": "boundary",
        "tier": _name(coordinate.tier),
        "index": coordinate.index,
    }


def _endpoint(
    graph: Graph, value: ItemRef | DurableItemRef | DurableBoundaryRef
) -> dict[str, JsonValue]:
    """Resolve one tagged relation endpoint without losing its endpoint kind."""
    if isinstance(value, DurableBoundaryRef):
        return _boundary(graph, value)
    return _item(graph, value)


def _raw_endpoint(
    value: ItemRef | DurableItemRef | DurableBoundaryRef,
) -> dict[str, JsonValue]:
    """Return the exact reference variant used by a relation endpoint."""
    if isinstance(value, ItemRef):
        return {"kind": "item-coordinate", **value.to_data()}
    if isinstance(value, DurableItemRef):
        return {"kind": "durable-item", **value.to_data()}
    return {"kind": "durable-boundary", **value.to_data()}


def _durable_indexes(graph: Graph) -> _DurableIndexes:
    """Index both ordered relation spaces once for one canonical walk."""
    return (
        {
            relation.durable_id: index
            for index, relation in enumerate(graph.relations)
            if relation.durable_id is not None
        },
        {
            relation.durable_id: index
            for index, relation in enumerate(graph.polyadic_relations)
            if relation.durable_id is not None
        },
    )


def _orphan(value: OrphanedSubject) -> dict[str, JsonValue]:
    """Return the retained carrier and coordinate of one orphan fact."""
    if isinstance(value.was, ItemRef):
        was: dict[str, JsonValue] = {
            "kind": "item",
            "tier": _name(value.was.tier),
            "index": value.was.index,
        }
    elif isinstance(value.was, BoundaryRef):
        was = {
            "kind": "boundary",
            "tier": _name(value.was.tier),
            "index": value.was.index,
        }
    else:
        was = {"kind": "index", "index": value.was}
    return {"kind": "orphaned", "carrier": _carrier(value.carrier), "was": was}


def _subject(
    graph: Graph, value: object, durable_indexes: _DurableIndexes
) -> dict[str, JsonValue]:
    """Resolve a layer subject to the coordinate its fact addresses."""
    if isinstance(value, ItemRef | DurableItemRef):
        return _item(graph, value)
    if isinstance(value, BoundaryRef | DurableBoundaryRef):
        return _boundary(graph, value)
    if isinstance(value, TierRef):
        return {"kind": "tier", "tier": _name(value.tier)}
    if isinstance(value, RelationDeclarationRef):
        return {"kind": "relation-declaration", "relation": _name(value.relation)}
    if isinstance(value, RelationInstanceRef):
        return {"kind": "relation-instance", "index": value.index}
    if isinstance(value, DurableRelationRef):
        return {
            "kind": "relation-instance",
            "index": durable_indexes[0][value.durable_id],
        }
    if isinstance(value, PolyadicInstanceRef):
        return {"kind": "polyadic-instance", "index": value.index}
    if isinstance(value, DurablePolyadicRef):
        return {
            "kind": "polyadic-instance",
            "index": durable_indexes[1][value.durable_id],
        }
    if isinstance(value, DocumentRef):
        return {"kind": "document"}
    if isinstance(value, OrphanedSubject):
        return _orphan(value)
    raise TypeError(  # pragma: no cover - Graph validation closes the subject union
        f"unsupported layer subject {type(value).__name__}"
    )


def _raw_subject(value: object) -> dict[str, JsonValue]:
    """Return the exact reference variant used by a layer fact."""
    if isinstance(value, ItemRef):
        return {"kind": "item-coordinate", **value.to_data()}
    if isinstance(value, DurableItemRef):
        return {"kind": "durable-item", **value.to_data()}
    if isinstance(value, BoundaryRef):
        return {"kind": "boundary-coordinate", **value.to_data()}
    if isinstance(value, DurableBoundaryRef):
        return {"kind": "durable-boundary", **value.to_data()}
    if isinstance(value, TierRef):
        return {"kind": "tier", "tier": _name(value.tier)}
    if isinstance(value, RelationDeclarationRef):
        return {"kind": "relation-declaration", "relation": _name(value.relation)}
    if isinstance(value, RelationInstanceRef):
        return {"kind": "relation-instance", "index": value.index}
    if isinstance(value, DurableRelationRef):
        return {"kind": "durable-relation", "durable_id": value.durable_id}
    if isinstance(value, PolyadicInstanceRef):
        return {"kind": "polyadic-instance", "index": value.index}
    if isinstance(value, DurablePolyadicRef):
        return {"kind": "durable-polyadic", "durable_id": value.durable_id}
    if isinstance(value, DocumentRef):
        return {"kind": "document"}
    if isinstance(value, OrphanedSubject):
        return _orphan(value)
    raise TypeError(  # pragma: no cover - Graph validation closes the subject union
        f"unsupported layer subject {type(value).__name__}"
    )


def _append_tail(elements: list[_Element], graph: Graph, exact: bool) -> None:
    """Append document values and the graph-value witness for exact form."""
    elements.append(
        _element(
            "document values",
            [attribute.to_data() for attribute in graph.attributes],
        )
    )
    if not exact:
        return
    # The detailed walk gives useful first-difference coordinates. This final
    # value preserves the complete current Graph equality surface without
    # imposing the wire encoder's stricter character repertoire.
    elements.append(_element("exact graph value", graph.to_data()))


def _append_layers(
    elements: list[_Element],
    graph: Graph,
    exact: bool,
    durable_indexes: _DurableIndexes,
) -> None:
    """Append layers and facts, resolving live subjects below exact form."""
    elements.append(_element("layer count", len(graph.layers)))
    for layer_index, layer in enumerate(graph.layers):
        elements.append(_element(f"layer {layer_index}", layer.name.to_data()))
        facts: list[JsonValue] = [
            {
                "subject": (
                    _raw_subject(fact.subject)
                    if exact
                    else _subject(graph, fact.subject, durable_indexes)
                ),
                "value": fact.value.to_data(),
            }
            for fact in layer.facts
        ]
        if not exact:
            facts.sort(key=_json)
        elements.append(_element(f"layer {layer_index} fact count", len(facts)))
        elements.extend(
            _element(f"layer {layer_index} fact {fact_index}", fact)
            for fact_index, fact in enumerate(facts)
        )


def _abstract_elements(graph: Graph, view: EquivalenceView) -> tuple[_Element, ...]:
    """Walk one graph in the stable order shared by all public operations."""
    exact = view is EquivalenceView.EXACT
    identified = view is not EquivalenceView.FUNCTIONAL
    durable_indexes = _durable_indexes(graph)
    elements: list[_Element] = []

    elements.append(_element("namespace count", len(graph.namespaces)))
    for index, binding in enumerate(graph.namespaces):
        value: JsonValue = (
            binding.to_data() if exact else {"namespace": binding.namespace}
        )
        elements.append(_element(f"namespace {index}", value))

    elements.append(_element("tier count", len(graph.tiers)))
    for tier_index, tier in enumerate(graph.tiers):
        elements.append(
            _element(f"tier {tier_index} declaration", tier.declaration.to_data())
        )
        elements.append(
            _element(
                f"tier {tier_index} values",
                [attribute.to_data() for attribute in tier.attributes],
            )
        )
        elements.append(_element(f"tier {tier_index} item count", len(tier.items)))
        for item_index, item in enumerate(tier.items):
            item_data: dict[str, JsonValue] = {
                "attributes": [attribute.to_data() for attribute in item.attributes]
            }
            if identified:
                item_data["durable_id"] = item.durable_id
            elements.append(_element(f"tier {tier_index} item {item_index}", item_data))

    elements.append(
        _element("relation declaration count", len(graph.relation_declarations))
    )
    elements.extend(
        _element(f"relation declaration {index}", declaration.to_data())
        for index, declaration in enumerate(graph.relation_declarations)
    )
    elements.append(
        _element("attribute declaration count", len(graph.attribute_declarations))
    )
    elements.extend(
        _element(f"attribute declaration {index}", declaration.to_data())
        for index, declaration in enumerate(graph.attribute_declarations)
    )

    elements.append(_element("relation instance count", len(graph.relations)))
    for index, binary_relation in enumerate(graph.relations):
        relation_data: dict[str, JsonValue] = {
            "declaration": _name(binary_relation.declaration),
            "left": (
                _raw_endpoint(binary_relation.left)
                if exact
                else _endpoint(graph, binary_relation.left)
            ),
            "right": (
                _raw_endpoint(binary_relation.right)
                if exact
                else _endpoint(graph, binary_relation.right)
            ),
            "attributes": [
                attribute.to_data() for attribute in binary_relation.attributes
            ],
        }
        if identified:
            relation_data["durable_id"] = binary_relation.durable_id
        elements.append(_element(f"relation instance {index}", relation_data))

    elements.append(
        _element("polyadic relation instance count", len(graph.polyadic_relations))
    )
    for index, polyadic_relation in enumerate(graph.polyadic_relations):
        polyadic_data: dict[str, JsonValue] = {
            "declaration": _name(polyadic_relation.declaration),
            "sources": [
                _raw_endpoint(endpoint) if exact else _endpoint(graph, endpoint)
                for endpoint in polyadic_relation.sources
            ],
            "targets": [
                _raw_endpoint(endpoint) if exact else _endpoint(graph, endpoint)
                for endpoint in polyadic_relation.targets
            ],
            "attributes": [
                attribute.to_data() for attribute in polyadic_relation.attributes
            ],
        }
        if identified:
            polyadic_data["durable_id"] = polyadic_relation.durable_id
        elements.append(_element(f"polyadic relation instance {index}", polyadic_data))

    if exact:
        elements.append(_element("valued boundary count", len(graph.boundary_values)))
        for index, boundary in enumerate(graph.boundary_values):
            boundary_data: dict[str, JsonValue] = {
                "reference": _raw_subject(boundary.reference),
                "attributes": [
                    attribute.to_data() for attribute in boundary.attributes
                ],
            }
            elements.append(_element(f"valued boundary {index}", boundary_data))
    else:
        boundary_count = sum(len(tier.items) + 1 for tier in graph.tiers)
        elements.append(_element("boundary count", boundary_count))
        boundary_index = 0
        for tier in graph.tiers:
            for boundary in graph.boundaries(tier.declaration.name):
                boundary_data = {
                    "reference": _boundary(graph, boundary.reference),
                    "attributes": [
                        attribute.to_data() for attribute in boundary.attributes
                    ],
                }
                elements.append(_element(f"boundary {boundary_index}", boundary_data))
                boundary_index += 1

    elements.append(_element("seal count", len(graph.seals)))
    elements.extend(
        _element(f"seal {index}", seal.to_data())
        for index, seal in enumerate(graph.seals)
    )

    _append_layers(elements, graph, exact, durable_indexes)
    _append_tail(elements, graph, exact)
    return tuple(elements)


def abstract_form(
    graph: Graph, view: EquivalenceView | str = EquivalenceView.FUNCTIONAL
) -> tuple[tuple[str, str], ...]:
    """Return the view's canonical named elements in comparison order.

    Values use deterministic strict-JSON spellings. Functional and identified
    forms resolve durable references to coordinates; the identified form then
    adds carried durable ids. The exact form retains reference spellings and
    includes the complete graph value used by graph equality. It does not
    require the graph to be encodable as wire bytes.
    """
    return _abstract_elements(graph, _view(view))


def equivalent(
    left: Graph,
    right: Graph,
    view: EquivalenceView | str = EquivalenceView.FUNCTIONAL,
) -> bool:
    """Report whether two graphs are equal under the selected public view."""
    selected = _view(view)
    if left is right:
        return True
    if selected is EquivalenceView.EXACT:
        return left == right
    if _form_shape(left) != _form_shape(right):
        return False
    return _abstract_elements(left, selected) == _abstract_elements(right, selected)


def _form_shape(graph: Graph) -> tuple[object, ...]:
    """Return cheap section sizes that can reject unequal non-exact forms."""
    return (
        len(graph.namespaces),
        tuple(len(tier.items) for tier in graph.tiers),
        len(graph.relation_declarations),
        len(graph.attribute_declarations),
        len(graph.relations),
        len(graph.polyadic_relations),
        len(graph.seals),
        tuple(len(layer.facts) for layer in graph.layers),
        len(graph.attributes),
    )


def first_difference(
    left: Graph,
    right: Graph,
    view: EquivalenceView | str = EquivalenceView.FUNCTIONAL,
) -> str | None:
    """Name and render the first differing element, or return ``None``."""
    selected = _view(view)
    left_form = _abstract_elements(left, selected)
    right_form = _abstract_elements(right, selected)
    # Every variable-size section starts with a count. Forms therefore differ
    # at a shared name before their lengths or following element names can drift.
    shared_length = min(len(left_form), len(right_form))
    for index in range(shared_length):
        left_element = left_form[index]
        right_element = right_form[index]
        left_name, left_value = left_element
        right_name, right_value = right_element
        if left_name != right_name:
            raise RuntimeError(
                "equivalence-form alignment invariant failed at element "
                f"{index}: left names {left_name!r}, right names {right_name!r}"
            )
        if (left_name, left_value) == (right_name, right_value):
            continue
        return f"{left_name}: left is {left_value}; right is {right_value}"
    if len(left_form) != len(right_form):
        raise RuntimeError(
            "equivalence-form alignment invariant failed after "
            f"{shared_length} elements: left has {len(left_form)}, "
            f"right has {len(right_form)}"
        )
    return None


def fingerprint(
    graph: Graph, view: EquivalenceView | str = EquivalenceView.FUNCTIONAL
) -> str:
    """Return a view-tagged, versioned SHA-256 fingerprint.

    The payload version is ``tiergraph-equivalence/1``. A fingerprint is stable
    only within that version, and two views intentionally have distinct digests
    even when their canonical forms otherwise contain the same elements.
    """
    selected = _view(view)
    payload = json.dumps(
        [_FINGERPRINT_VERSION, selected.value, _abstract_elements(graph, selected)],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "EquivalenceView",
    "abstract_form",
    "equivalent",
    "fingerprint",
    "first_difference",
]
