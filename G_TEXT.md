# G-TEXT result

**The unchanged kernel sufficed.** [executed: `make check`]

The fixture uses boundary atoms from two windows in the Folger Digital Texts
*Hamlet* XML. Folger declares `pb` as print pagination and uses `prev`/`next` to
reconstruct split verse lines. Page 13 splits the shared verse line assembled
from FTLN 1.1.68 and 1.1.69; page 57 splits the shared line assembled from FTLN
1.5.3 through 1.5.5. Page 19 falls between FTLN 1.1.164 and 1.1.165 inside the
sentence beginning “I have heard.” [read: Folger `Ham.xml`, edition 0.9.2.1,
`pb-013`, `pb-019`, `pb-057`, and the adjacent `ftln` milestones]

The test expands the source ranges into ordinary bipartite membership edges.
Coordinates appear only in the fixture builder's independent oracle; the graph
does not store a start/end string, address containment through an attribute, or
infer membership by comparing coordinates. Each hierarchy is an ordered tier,
and its membership is held by relations. [read: `tests/test_text_domain.py`]

## The three domain tests

1. **Multiple ordered sequences.** Text atoms, print pages, sentences, and
   reconstructed verse lines are four independently ordered tiers. [executed:
   `test_source_structures_cross_and_a_tree_would_duplicate_content`]
2. **Meaningful cross-sequence relations.** `covered-by` directly relates every
   text atom to its page, sentence, and verse span. The assertions exhibit
   `a < b < c < d` crossings for page/verse and sentence/verse. The recorded
   page-19 window exhibits page/sentence crossing. A page-rooted tree must
   split verse 1.1.68–69 at the page boundary and duplicate either the verse or
   its text atom. [executed:
   `test_source_structures_cross_and_a_tree_would_duplicate_content`]
3. **A natural fold question.** The fold asks for cross-hierarchy coverage: how
   many declared structural spans cover the selected text atoms? Its semiring is
   the natural-number counting semiring `(N, +, *, 0, 1)`. Thirteen atoms each have
   one page, sentence, and verse membership, so the hand-derived result is
   `13 * 3 = 39`. [executed:
   `test_counting_fold_answers_cross_hierarchy_coverage_by_hand`]

## Role rule

No new kind was needed for (1) text units, (2) pages, (3) sentences, or (4)
verse lines. Each is an ordinary ordered tier with a declared role. The same
ordinary bipartite relation kind holds all three membership roles. No structural
fact is packed into an attribute value. [read: `tests/test_text_domain.py`]

What this domain needed that linguistic annotation had not tested was one item
belonging simultaneously to structures whose boundaries cross, including a
single reconstructed verse line on both sides of a print-page boundary. The
kernel retained each tier's order and the cross-tier incidences without choosing
a privileged tree. [executed:
`test_source_structures_cross_and_a_tree_would_duplicate_content`]

No kernel deficiency was found. [executed: `make check`]
