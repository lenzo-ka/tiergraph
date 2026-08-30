# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Added enum-member emission and made `NodeSet` ordering an explicit contract (#71).
- Added zero-width span support throughout projection, rendering, and the text,
  JSON, JSON Lines, HTML, and DOT emitters; `to_jsonl` now stamps its format
  version (#72).
- Added the semiring `star` member and cyclic-fold resolution, including the new
  `StarRefusal`, `StarSelector`, and `ZeroClosedStar` public names; retired the
  ineffective per-relation acyclic check, and added `collapse_units` to grammar
  recognition (#73).
- Added `tiergraph fold`, which evaluates a declared dependency relation with a
  named semiring and emits the public `FoldResult` report, and `tiergraph
  semirings`, which lists every algebra a fold can name with its carrier
  boundary, declared law checks, and declared properties. The listed names are
  exactly the values `fold --semiring` accepts. The shell names one of two
  lifts, `value` and `one`; a general lift, a witness order, and an index
  product remain caller code in the Python API, so `--ranked` is the shell's
  route to witnesses. The action and react surface gets no command, because
  every part of it that matters is a caller-supplied callable.

### Changed

- **BREAKING:** selection is now one graph-free surface: validation moves from
  construction to evaluation, and `evaluate_selection` takes the graph and a
  path profile. `BoundaryPathSelector`, `DifferenceSelector`,
  `IntersectionSelector`, `ItemPathSelector`, `Selector`, `UnionSelector`, and
  `selection_loads` replace the former query surface. The published selection
  group contracts from 24 names to 18. The JSON dialect is unchanged, so no
  stored selection document changes meaning (#75).
- **BREAKING:** promoting an item with a different existing durable id now
  refuses the conflict on both execution engines; supplying the same id remains
  a no-op (#76).
- **BREAKING:** `to_data`, `dumps`, `dump_compact`, and `dump_bytes` now share a
  single refusal for a document the reader could not read, and `loads` refuses
  duplicate JSON keys instead of accepting the last value (#77).
- CLI reports now apply the writer's refusal before encoding. Invalid report
  data exits with status 1 instead of leaking `UnicodeEncodeError` at status 3
  (#78).
- **BREAKING:** `tiergraph select` now reads its input from `--selector`, and
  its help text names a selector. The retired `--query` spelling is refused
  outright; there is no alias, so the library, the flag, and the help now spell
  one concept one way.
- The publishability gate now refuses sibling-repository names by comparing
  salted digests, so no forbidden name is written down in the repository.
- The documentation gate now holds every module in the manifest's secondary
  surface to that module's own `__all__`, rather than only `tiergraph.build` and
  `tiergraph.semiring`, and refuses to publish a surface for a module that
  declares none. `tiergraph.schema` now declares the two names it publishes, so
  the module rather than the manifest states the surface.
- The gate scripts under `scripts/` are now inside the coverage bar and reach
  100% with the shipped packages. The bar previously measured only `tiergraph`
  and `tiergraph_dot`, so the code that enforces publishability, docstrings,
  documentation currency, and the schema stamp was itself unwitnessed. Every
  newly covered line has a behavioral witness; no exclusion pragma was needed.
- `tiergraph.semiring` now declares `PATH_WITNESSES` and `PathWitnessSemiring`
  in its `__all__`, so the path-witness algebra is reachable from the module's
  declared surface like every other semiring it defines. It is the second
  component of `PATH` and the one a caller needs to build a witness-carrying
  composition over a different cost algebra.

### Removed

- **BREAKING:** removed `AttributeQuery`, `BoundariesQuery`, `BoundaryQuery`,
  `DifferenceQuery`, `IntersectionQuery`, `ItemQuery`, `ItemsQuery`,
  `SelectionQuery`, `TierQuery`, `TypeQuery`, `UnionQuery`, `select`, and
  `selection_query_loads`, together with the `tiergraph.selection_query`
  module. Graph-free selectors replace the query classes; `select` was a fold
  of union now expressed by `UnionSelector` (#75).
- **BREAKING:** renamed `tiergraph.semiring.REQUIRED_LAW_CHECKS` to
  `_REQUIRED_LAW_CHECKS`. It was never exported, documented, or referenced
  outside the module: it is the shared precondition list two composite
  constructions check, and it repeats the protocol's own `LawCheck`-valued
  properties, which a caller reads from the algebra. The underscore says so.

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

[Unreleased]: https://github.com/lenzo-ka/tiergraph/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/lenzo-ka/tiergraph/releases/tag/v0.1.0
