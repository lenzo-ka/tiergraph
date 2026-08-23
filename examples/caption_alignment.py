"""Build a caption alignment and traverse from a word to its phones."""

from __future__ import annotations

from tiergraph import ItemRef, ItemSelector, Walk, WalkDirection, XsdType, select
from tiergraph.build import document, item

NAMESPACE = "https://tiergraph.dev/examples/caption-alignment"


def aligned_phones() -> list[str]:
    """Return the phones reached from the selected word ``cat``."""
    builder = document(NAMESPACE, prefix="caption")
    builder.attribute("label", XsdType.STRING)
    words = builder.tier(
        "words",
        ("a", "cat", "sat"),
        item_type="word",
        membership="word-membership",
    )
    phones = builder.tier(
        "phones",
        tuple(
            item(f"phone-{index}", label=label)
            for index, label in enumerate(("AH", "K", "AE", "T", "S", "AE", "T"))
        ),
        item_type="phone",
        membership="phone-membership",
    )
    aligns = builder.link(
        "aligns",
        words,
        phones,
        ((0, 0), (1, 1), (1, 2), (1, 3), (2, 4), (2, 5), (2, 6)),
        acyclic=True,
    )
    graph = builder.build()

    # The same graph can also be written with the kernel construction types.
    selected = select(graph, (ItemSelector(graph, words.ref(1)),))
    reached = Walk(selected, aligns.name, WalkDirection.FORWARD).evaluate().nodes
    phone_tier = next(
        tier for tier in graph.tiers if tier.declaration.name == phones.name
    )
    label_name = builder.qname("label")
    return [
        next(
            value.lexical
            for value in phone_tier.items[reference.index].attributes
            if value.name == label_name
        )
        for node in reached.nodes
        if isinstance(reference := node.reference, ItemRef)
    ]


def main() -> int:
    """Print the checkable alignment result."""
    print(aligned_phones())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
