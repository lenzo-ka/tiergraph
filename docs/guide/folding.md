# Folding

`FoldDeclaration` evaluates a finite dependency graph with a semiring. An
`AttributeValuation` selects the item field, `FoldTransition` assigns AND or OR
combination to a relation, and roots choose the requested states. `FoldResult`
keeps the carrier value, reconstruction provenance, truncation state, and cost
account separate.

Semiring implementations live in `tiergraph.semiring`. This module is a
supported secondary API because folds require a concrete semiring. Import its
protocol, implementations, aliases, and singleton instances with the module
name. Tie policies and output caps make witness selection explicit.
