"""Recognize a weighted dependency path, then apply two distinct actions."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import cast

from tiergraph import (
    ActionDeclaration,
    AttributeDeclaration,
    AttributeDomain,
    AttributeValuation,
    AttributeValue,
    BipartiteRelationDeclaration,
    ChildCombination,
    DistributionWitness,
    FoldDeclaration,
    FoldTransition,
    Graph,
    Item,
    ItemRef,
    NamespaceDeclaration,
    QualifiedName,
    ReactDeclaration,
    ReactMode,
    RelationInstance,
    Semimodule,
    SimpleRelationDeclaration,
    TiePolicy,
    Tier,
    TierDeclaration,
    WitnessCoordinate,
    XsdType,
)
from tiergraph.semiring import DECIMAL_TROPICAL

NAMESPACE = "https://tiergraph.dev/examples/mixing"


def name(local: str) -> QualifiedName:
    """Return the example's qualified name without exposing it as a constant."""
    return QualifiedName(NAMESPACE, local)


def build_graph() -> Graph:
    """Build a dependency diamond with a cost and coordinate on each item."""
    placement = name("placement")
    cost = name("cost")
    coordinate = name("coordinate")

    def item(identifier: str, weight: str, position: str) -> Item:
        return Item(
            identifier,
            (
                AttributeValue(cost, XsdType.DECIMAL, weight),
                AttributeValue(coordinate, XsdType.INTEGER, position),
            ),
        )

    membership = SimpleRelationDeclaration(
        name("placements"), placement, name("placement-type")
    )
    dependency = BipartiteRelationDeclaration(
        name("depends"),
        name("placement-type"),
        name("placement-type"),
        acyclic=True,
    )
    refs = tuple(ItemRef(placement, index) for index in range(4))
    return Graph(
        (NamespaceDeclaration("mix", NAMESPACE),),
        (
            Tier(
                TierDeclaration(placement, "Mix placements"),
                (
                    item("start", "0", "0"),
                    item("bed", "2", "4"),
                    item("sting", "1", "8"),
                    item("out", "3", "12"),
                ),
            ),
        ),
        (membership, dependency),
        (
            RelationInstance(dependency.name, refs[0], refs[1]),
            RelationInstance(dependency.name, refs[0], refs[2]),
            RelationInstance(dependency.name, refs[1], refs[3]),
            RelationInstance(dependency.name, refs[2], refs[3]),
        ),
        (
            AttributeDeclaration(cost, AttributeDomain.ITEM, XsdType.DECIMAL),
            AttributeDeclaration(coordinate, AttributeDomain.ITEM, XsdType.INTEGER),
        ),
    )


def _fold(graph: Graph) -> FoldDeclaration[Decimal]:
    placement = name("placement")
    return FoldDeclaration(
        "least-cost-path",
        graph,
        AttributeValuation("cost", name("cost"), (placement,)),
        DECIMAL_TROPICAL,
        lambda value, label: cast(Decimal, value),
        (FoldTransition(name("depends"), ChildCombination.OR),),
        roots=(ItemRef(placement, 0),),
        witness_order=lambda left, right: (left > right) - (left < right),
        tie_policy=TiePolicy.CHOOSE_FIRST,
        output_cap=4,
    )


def _coordinates(graph: Graph, provenance: object) -> tuple[WitnessCoordinate, ...]:
    paths = cast(tuple[tuple[str, ...], ...], provenance)
    coordinate = name("coordinate")
    values = {
        item.durable_id: int(
            next(value for value in item.attributes if value.name == coordinate).lexical
        )
        for tier in graph.tiers
        for item in tier.items
    }
    return tuple(
        WitnessCoordinate((values[label],), values[label])
        for path in paths
        for label in path
    )


def _add_coordinates(carrier: object, values: tuple[object, ...]) -> object:
    result = dict(cast(dict[int, int], carrier))
    for value in values:
        coordinate = cast(int, value)
        result[coordinate] = result.get(coordinate, 0) + 1
    return {key: result[key] for key in sorted(result)}


def _scale(carrier: object, values: tuple[object, ...]) -> object:
    result = cast(int, carrier)
    for value in values:
        result *= cast(int, value)
    return result


def run_example() -> dict[str, object]:
    """Return the deterministic result of all three explicitly separated layers."""
    graph = build_graph()
    fold = _fold(graph)
    recognition = fold.run()
    coordinate_action = ActionDeclaration(
        "coordinate-mix", _add_coordinates, True, False, True
    )
    react = ReactDeclaration(
        "mix-recognized-path",
        fold,
        lambda provenance: _coordinates(graph, provenance),
        coordinate_action,
        mode=ReactMode.ONE_FOR_ONE,
        distribution=DistributionWitness("batch-equals-one-for-one"),
    )
    coordinate_result = react.run({})

    module = Semimodule[object, object](
        0,
        1,
        lambda left, right: cast(int, left) + cast(int, right),
        lambda left, right: cast(int, left) * cast(int, right),
        0,
        lambda left, right: cast(int, left) + cast(int, right),
        lambda scalar, value: cast(int, scalar) * cast(int, value),
        (0, 1, 3),
        (0, 2, 5),
    )
    scale_action = ActionDeclaration(
        "separate-scale", _scale, True, False, True, semimodule=module
    )
    return {
        "recognition": {
            "value": str(recognition.value),
            "provenance": (
                None
                if recognition.provenance is None
                else [list(path) for path in recognition.provenance]
            ),
            "truncated": recognition.truncated,
        },
        "coordinate_action": {
            str(key): value
            for key, value in cast(dict[int, int], coordinate_result["result"]).items()
        },
        "separate_semimodule_scale": scale_action.apply(2, (3,)),
    }


def main() -> int:
    """Print the example result as stable JSON."""
    print(json.dumps(run_example(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
