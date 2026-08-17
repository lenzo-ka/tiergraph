# Concepts

A tiergraph document is one graph with several ordered tiers. A tier declares a
name and contains items in order. Items may carry declared, XSD-typed scalar
attributes. Relations connect item or boundary references according to their
declarations; polyadic relations retain a declared sequence of endpoints.

The same graph supports several views. Selection treats tiers, items, and
boundaries as nodes. Traversal follows declared incidence. Profiles interpret
specific declarations as clocks, roots, choices, or recursive JSON values.
Folds interpret an acyclic dependency relation with a caller-supplied semiring.

`Graph` is immutable and checks its boundary in `Graph.__post_init__`. It refuses
undeclared namespaces, duplicate names or durable IDs, mismatched attribute
types, invalid references, relation endpoints of the wrong kind, and violations
of declared constraints such as acyclicity or single-parent incidence. Profiles
add their own checks without adding kernel node kinds.

The design learned from Paul Hertz's Delta representation and the heterogeneous
relation graphs used by the Festival Speech Synthesis System. tiergraph keeps
their useful emphasis on explicit structure while defining its own typed,
immutable Python model. Phonetics is one application; the graph and format do
not assign phonetic meaning to tiers or attributes.
