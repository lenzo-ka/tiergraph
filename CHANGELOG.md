# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-08-23

### Added

- Immutable ordered-tier graphs with declared namespaces, typed attributes, item and boundary references, and simple, bipartite, and polyadic relations.
- A checked opcode `Program` and build machine for declaring and populating graphs, with bounded repetition, execution traces, and unrolling to an immutable `Graph`.
- Semiring folds over finite dependency DAGs, including exact min- and max-plus carriers and the `PATH` semiring's tied best-path provenance and capped n-best ranked witnesses.
- Clock profiles for structural refinement and physical timing, including `ClockProfile.from_position_values`, optional shared-boundary collapse, and the `is_structural` capability flag.
- Deterministic span projections through `tiergraph.spanview`, with text, JSON, JSON Lines, and HTML emitters, plus `tiergraph_dot.dumps_spans` visualization.
- Deterministic Graphviz DOT rendering through `tiergraph_dot.dumps`, including `DotPresentation` hooks and structural-clock occupied-spine placement and relation rendering.
- TG-PATH canonical addressing for structural and durable items and boundaries, profile-owned alternatives, kind checks, and typed refusals with offender details.
- Canonical selection, bounded bipartite walks, ordered polyadic traversal, and ordered containment queries that preserve declared incidence and child order where applicable.

[0.1.0]: https://github.com/lenzo-ka/tiergraph/releases/tag/v0.1.0
