"""Project fixed word and sentence segmentations through the span-view API."""

from __future__ import annotations

from tiergraph import SpanViewProfile, XsdType, span_view, to_html, to_text
from tiergraph.build import document, item

NAMESPACE = "https://tiergraph.dev/examples/text-segmentation"
INPUT = "Cats nap."


def render_views() -> str:
    """Build one segmentation graph and return its two text renderings."""
    builder = document(NAMESPACE, prefix="segment")
    builder.attribute("surface", XsdType.STRING)
    builder.attribute("offset", XsdType.INTEGER)
    builder.attribute("value", XsdType.STRING)
    builder.attribute("score", XsdType.DECIMAL)

    characters = builder.tier(
        "characters",
        tuple(
            item(f"char-{index}", surface=surface, offset=index)
            for index, surface in enumerate(INPUT)
        ),
        item_type="character",
        membership="character-membership",
    )
    words = builder.tier(
        "words",
        (
            item("word-cats", value="Cats", score="0.98"),
            item("word-nap", value="nap", score="0.96"),
        ),
        item_type="word",
        membership="word-membership",
    )
    sentences = builder.tier(
        "sentences",
        (item("sentence-0", value=INPUT, score="0.99"),),
        item_type="sentence",
        membership="sentence-membership",
    )
    candidates = builder.tier(
        "alternatives",
        (
            item("candidate-cats", value="Cats", score="0.98"),
            item("candidate-cat", value="Cat", score="0.21"),
            item("candidate-nap", value="nap", score="0.96"),
        ),
        item_type="alternative",
        membership="alternative-membership",
    )

    coverage_pairs = tuple((index, 0) for index in range(4)) + tuple(
        (index, 1) for index in range(5, 8)
    )
    coverage = builder.link("covers", characters, words, coverage_pairs)
    builder.link(
        "sentence-covers",
        characters,
        sentences,
        tuple((index, 0) for index in range(len(INPUT))),
    )
    alternatives = builder.link(
        "has-alternative", words, candidates, ((0, 0), (0, 1), (1, 2))
    )
    graph = builder.build()

    common = {
        "score_attribute": builder.qname("score"),
        "value_attribute": builder.qname("value"),
        "base_surface_attribute": builder.qname("surface"),
        "char_offset_attribute": builder.qname("offset"),
    }
    word_profile = SpanViewProfile(
        characters.name,
        (words.name,),
        coverage.name,
        alternative_relation=alternatives.name,
        **common,
    )
    sentence_profile = SpanViewProfile(
        characters.name,
        (sentences.name,),
        builder.qname("sentence-covers"),
        alternative_relation=None,
        **common,
    )
    word_view = span_view(graph, word_profile, alternatives=True)
    sentence_view = span_view(graph, sentence_profile)

    # Both promoted emitters consume the same projected view.
    assert to_html(word_view, alternatives=True).startswith("<!doctype html>")
    assert to_html(sentence_view).startswith("<!doctype html>")
    return (
        "word spans\n"
        + to_text(word_view, alternatives=True)
        + "sentence spans\n"
        + to_text(sentence_view)
    )


def main() -> int:
    """Print the span views, including their alignment rulers."""
    print(render_views(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
