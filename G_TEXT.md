# G-TEXT result

**The unchanged kernel sufficed.** [executed: `make check`]

This witness is author-constructed. Its ranges are not extracted from Folger
Digital Texts and are not claimed to reproduce particular source locations.
The Folger edition is evidence only for the phenomenon being modeled: its XML
represents print pages with `pb`/page milestones and reconstructed split verse
lines with `prev`/`next`. The official tag guide documents both mechanisms and
provides the downloadable *Hamlet* XML. [read: [Folger Digital Texts XML tag
guide and download repository](https://www.folgerdigitaltexts.org/download/xml.html)]

The test expands the constructed ranges into ordinary bipartite membership
edges. Coordinates appear only in the fixture builder; crossing detection reads
memberships back from `graph.relations`. The graph does not store a start/end
string, address containment through an attribute, or infer membership by
parsing durable ids. [read: `tests/test_text_domain.py`]

## The three domain tests

1. **Multiple ordered sequences.** Text atoms, print pages, sentences, and
   verse lines are four independently ordered tiers. [executed:
   `test_composite_memberships_exhibit_all_authored_crossings`]
2. **Meaningful cross-sequence relations.** One `covered-by` relation kind
   directly relates every text atom to its page, sentence, and verse span. The
   composite has eight crossings: one page/sentence pair, four page/verse pairs,
   and three sentence/verse pairs. The assertion derives both sides of every
   crossing from graph relations, so missing or incorrect page edges fail it.
   [executed: `test_composite_memberships_exhibit_all_authored_crossings`]
3. **A natural fold question.** The fold asks how many pairs of spans from
   different hierarchies overlap without either containing the other. It reads
   each span's membership from the graph and adds one per crossing with the
   natural-number counting semiring. By hand, the overlapping fixture gives
   `1 + 4 + 3 = 8`. A nested control gives all three hierarchies the same six
   extents, hence `0` partial overlaps. The executed answers are therefore `8`
   and `0`; unlike total membership count, this question distinguishes crossing
   from nesting. [executed:
   `test_crossing_fold_distinguishes_overlapping_from_nested_memberships`]

## Structural claim

A tree can encode these structures using standoff references or milestones;
the source XML itself demonstrates that. The consequential choice is that a
tree must privilege one containment hierarchy and encode the others indirectly
as milestones or references. This graph format does not require that choice:
each hierarchy remains an ordinary ordered tier and all three memberships are
ordinary relations of the same kind, with the role recovered from the target
tier. [read: `tests/test_text_domain.py`; executed:
`test_composite_memberships_exhibit_all_authored_crossings`]

The `count` attribute remains inert fixture data: crossing detection does not
read it. Durable ids contain page-and-line-like mnemonics, but no code parses
them; this is a soft naming convention and must not become structural logic.
[read: `tests/test_text_domain.py`]

No kernel deficiency was found. [executed: `make check`]
