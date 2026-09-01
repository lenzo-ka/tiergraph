# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Added `discharge`, the command-line verb for declarations. It sits beside
  `validate` rather than inside it: `validate` asks whether one document is
  well formed, and `discharge` asks whether a declaration holds against its
  inputs. `discharge seals` binds a source graph's seals to the result graph
  claiming to honor them and emits the public `SealCertificate.to_data()`
  counts. A refused discharge writes no certificate and reports the refusal's
  declared stage, its rank in the refusal order, and any further applicable
  condition as JSON on stderr, so a caller routes on the stage instead of
  matching wording. No graph document is written or read differently.
- Added enum-member emission and made `NodeSet` ordering an explicit contract (#71).
- Added a public editing API: one operation set over two carriers. A frozen
  `Graph` answers `declare`, `set_attribute`, `remove_attribute`, `insert_item`,
  `remove_item`, `move_item`, `swap_items`, `add_relation`, and `remove_relation`
  by returning a new graph; the new public `GraphEditor`, reached through
  `Graph.edit()`, answers the same nine in place and validates once at
  `freeze()`. Which behavior a caller gets follows from the carrier held, not
  from a mode argument. Structural operations carry the graph's own item
  coordinates with their items and refuse an edit that would leave a stored
  coordinate boundary value without one boundary to hold it. This settles the
  rule for removing a durable boundary's anchor: the removal is refused rather
  than reinterpreted. The wire format is untouched.
- Added total displacement accounting for edits. The new public `Displacement`
  maps every source item, boundary, binary relation, and polyadic relation to
  its resulting coordinate or records its departure; `GraphEditor.displacement()`
  accumulates that account across chained operations and displacements compose
  with `then()`.
- Added graph-carried seals over ordered tier, binary-relation, and
  polyadic-relation carriers. The new public `GraphCarrier`, `SealedCarrier`,
  and `Seal` name those constraints; `SealDeclaration` compares a source and
  result, reporting `SealBreach` values or a `SealCertificate` from
  `check_seals()`.
- Added separately sourced attribute layers and explicit delivery policy. The
  new public `LayerName`, `LayerFact`, `Layer`, `LayerRead`, `Delivery`, and
  `Consensus` support delivered reads, agreement reports, and flattening into
  the base graph. `LayerSubject` covers the public `TierRef`,
  `RelationDeclarationRef`, `RelationInstanceRef`, `DurableRelationRef`,
  `PolyadicInstanceRef`, `DurablePolyadicRef`, and `DocumentRef` subject forms;
  structural edits preserve departed facts as `OrphanedSubject` values.
- Added zero-width span support throughout projection, rendering, and the text,
  JSON, JSON Lines, HTML, and DOT emitters; `to_jsonl` now stamps its format
  version (#72).
- Added the semiring `star` member and cyclic-fold resolution, including the new
  `StarRefusal`, `StarSelector`, and `ZeroClosedStar` public names; retired the
  ineffective per-relation acyclic check, and added `collapse_units` to grammar
  recognition (#73).
- Made polyadic relation instances reachable above the kernel. The
  `relation_instance` attribute axis now reads both instance collections and
  reports polyadic carriers under a new `polyadic_relation_instance` node kind,
  ordered by their declaration, side arities, and endpoints in stored order;
  `OrderedPolyadicTraversal.instances()` enumerates each instance as a new
  public `PolyadicIncidence`, carrying both sides as ordered `NodeSequence`
  values. Views that are genuinely binary now refuse a non-bipartite
  declaration by name instead of skipping it: a span-view profile refuses a
  non-bipartite coverage or alternative role, and a fold names a declared
  non-bipartite dependency for the kind it is rather than reporting it as
  undeclared.
- Added `tiergraph fold`, which evaluates a declared dependency relation with a
  named semiring and emits the public `FoldResult` report, and `tiergraph
  semirings`, which lists every algebra a fold can name with its carrier
  boundary, declared law checks, and declared properties. The listed names are
  exactly the values `fold --semiring` accepts. The shell names one of two
  lifts, `value` and `one`; a general lift, a witness order, and an index
  product remain caller code in the Python API, so `--ranked` is the shell's
  route to witnesses. The action and react surface gets no command, because
  every part of it that matters is a caller-supplied callable.
- `README.md` and `docs/format.md` now state the format version policy: a
  document declares the format version it was written in, a reader accepts only
  the version it implements and refuses any other by name, and documents are
  versioned interchange rather than an archival format.
- Added `scripts/check_reservations.py` and the `make reservations` gate, which
  registers every reserved or deferred item on a shipped docstring surface with
  the exact prose that carries it and the condition that would discharge it. An
  undischarged reservation is visible to a reader; one that has quietly stopped
  being true is not, so most entries carry a predicate that reports evidence
  when the tree has overtaken the promise and the gate fails naming it. A
  reservation no observable here can decide is registered as unenforceable with
  the reason. The gate also refuses a docstring that announces a reservation
  without registering one, and its own docstring states which surfaces it reads.
- Added a declared, refutable exactness claim on a fold. `FoldDeclaration` takes
  a `FoldExactness` — `DISTRIBUTIVE`, `APPROXIMATE`, `STRUCTURAL`, or the
  default `UNDECLARED` — and `check_exactness()` demands it and returns a new
  `FoldCertificate`. Omitting the claim and asserting it falsely are different
  refusals, both raising the new `ExactnessRefusal`: an undeclared exactness is
  answered with the declaration to be made and runs no fold, while a false
  `DISTRIBUTIVE` claim is answered with a semantic counterexample naming the
  law, the operands, and both sides evaluated. The claim is checked against
  probes the fold produces itself and, within `derivation_budget`, against the
  derivations enumerated with no sharing at all; the certificate reports whether
  that comparison happened, because a search that found no counterexample has
  not proved anything. Two refusals are settled from the declaration alone and
  never run a fold: a `DISTRIBUTIVE` claim over an algebra that does not check
  every required law exactly, and a `STRUCTURAL` claim over an algebra that
  declares no star warrant. `tiergraph.semiring.inexact_laws` publishes the law
  reader that both composite constructions and the new check share.
- Made what a rewrite did to the graph it rewrote a declared, refutable claim.
  A new `tiergraph.rewrite` module publishes `RewriteDeclaration`,
  `RewriteEffect`, `RewriteCertificate`, `RewriteDisturbance`, and
  `EffectRefusal`. `RewriteEffect` orders three effects by how much they
  disturb the graph they read: `DECORATE`, where every structure the source
  asserts stands in the result at the same coordinate carrying everything it
  carried; `REVISE`, where every structure still stands but some value stands
  in place of another; and `COLLAPSE`, where some structure has no counterpart
  at all. `check_effect()` demands the claim and returns a certificate
  reporting how many of the source's structures it was held to. Omitting the
  claim and asserting it falsely are answered differently: `UNDECLARED` returns
  the declaration to be made, and a false claim returns a semantic
  counterexample naming the structure, its tier, and what happened to it,
  followed by how many further disturbances also apply. A claim that cannot be
  exhibited is refused as well, so `REVISE` or `COLLAPSE` over a rewrite that
  only decorated is answered rather than accepted. The module produces no
  graphs and edits nothing; it measures the relation between two graph values.
  "Tiers can only decorate" is deliberately not stated as a law of the kernel,
  because it is false as one: a graph is an immutable value and its successor
  may stand in any relation to it. Every one of the build machine's nine
  primitive opcodes is measured as decorating and a test records it, with one
  case that shows why the claim belongs to a pair of graphs rather than to an
  operation: `AddItem` does not decorate a graph that carries a value at the
  boundary anchored to its tier's last edge, because growing the tier moves
  that edge and leaves the value's old coordinate empty. The document format is
  untouched.
- Made profile satisfaction an enumerable question instead of a convention. A
  profile was a class that happened to validate in its own constructor, with no
  base type, no registry, and no way to ask which roles a graph supports without
  naming a profile and trying it. A new `tiergraph.profile` module publishes
  `GraphProfile`, `ProfileRegistry`, `ProfileReport`, `ProfileOutcome`,
  `ProfileRegistrationRefusal`, `RoleBinding`, `RoleValue`, and the `PROFILES`
  registry this package's own profiles register into. `PROFILES.reports(graph,
  roles)` answers for every registered profile in name order. Four outcomes keep
  that answer honest: a check that decided everything the profile declares reads
  `satisfied`, one that decided what it can reads `satisfied_as_checked` with
  the rest named in the report, an unbound required role reads `not_applicable`
  rather than counting as a pass, and a refusal carries its reason. Registration
  tests a profile's claims rather than taking them: one that names no condition
  its check decides is refused, since a check that states nothing cannot be told
  from one that passes always, and every profile carries two witnesses, one
  arrangement its check must accept and one it must refuse, which the registry
  runs so a check that cannot tell them apart never enters. Population is
  explicit, never discovered, so import order cannot decide what a caller is
  told. Five profiles are registered: ordered containment, ordered roots,
  persisted choice, JSON value, and span view, the last of which gains a graph
  check it never had, because `SpanViewProfile` names declarations without
  holding a graph. The clock profile is adapted separately, and the path
  profiles are outside this mechanism: a path vocabulary asserts nothing about a
  graph. The wire format is untouched.
- Added `scripts/check_format_growth.py` and the `make format-growth` gate,
  which compares the committed JSON Schema against the schema recovered from the
  newest release tag in the current release line and refuses a change that
  shrinks the set of documents the format accepts. The wire is closed and a
  document is refused when it fails to validate, which leaves one exposure: a
  later release could change what an existing field means without changing its
  shape, and an older reader would validate the document and misread it. A
  format that only grows retires that exposure. Additive is decided from the
  schema's own vocabulary — a member added, a requirement dropped, an enum or
  union widened, a bound lowered, an object opened — and its negations refuse by
  name and location. A changed `pattern` and a keyword the gate has no rule for
  are reported as unestablished rather than passed over, and the two
  version-bearing sites are excluded only while each is still exactly a version
  stamp, so the exclusion cannot carry another change through. The gate does not
  forbid a break: a break costs a step in the version position that carries
  breaking changes, the minor before 1.0 and the major after, the refusal names
  that step, and between the step and the tag that releases it the gate prints
  each break rather than refusing it. The baseline is read from a release tag
  rather than a second committed copy, so a checkout without tags refuses
  instead of reporting a comparison it never made. The wire format is untouched.

### Changed

- `GraphValidationError` now carries a `stage`, so both channels this package
  refuses through report one vocabulary rather than two shapes. The stage
  defaults to `RefusalStage.SEMANTICS`, which is what a declaration or
  graph-contract violation already meant, and the message stays the first
  argument, so every existing raise and every existing `except ValueError` is
  unaffected. `RefusalStage` itself moves to `tiergraph.core`, the one module
  both channels reach without a cycle, and stays exported from
  `tiergraph.schema`, where callers import it.
- **BREAKING:** the clock CLI now names the values it returns: `tiergraph clock
  positions` becomes `tiergraph clock coordinates`, and `tiergraph clock
  position --position PATH` becomes `tiergraph clock boundary --boundary PATH`.
  Their emitted top-level JSON keys likewise change from `"positions"` to
  `"coordinates"` and from `"position"` to `"boundary"`; all other output is
  unchanged.
- **BREAKING:** `NodeKind.POSITION`, `PathKind.POSITION`, and
  `PathRefusalCode.POSITION_NOT_IN_PARENT` become `NodeKind.BOUNDARY`,
  `PathKind.BOUNDARY`, and `PathRefusalCode.BOUNDARY_NOT_IN_PARENT`; their
  values become `"boundary"` and `"boundary_not_in_parent"` respectively.
  Boundary-node JSON from `tiergraph path resolve` now reports
  `"kind": "boundary"` instead of `"kind": "position"`. The wire format is
  unchanged.
- **BREAKING:** relation endpoints now admit `DurableItemRef` directly, encoded
  as the disjoint tagged wire arm
  `{"kind": "durable-item", "durable_id": "..."}`, so an item endpoint can
  retain its identity when insertions move its structural index. An unresolved
  durable item endpoint is now reported as naming no item instead of being
  falsely classified as a boundary. `AttributeDomain.BOUNDARY` remains the
  public Python member, while its wire value changes from `"position"` to
  `"boundary"`; existing format documents using `"position"` must be rewritten
  for 0.2.0. No public Python names were added or removed by this format break.
- **BREAKING:** the published vocabulary now says *coordinate* for an identity
  and reserves *position* for nothing else, so the boundary family is renamed
  rather than kept. `Position`, `PositionRef`, `DurablePositionRef`,
  `PositionBinding`, `ResolvedPosition`, `PromotePosition`, and
  `anchored_position` become `Boundary`, `BoundaryRef`, `DurableBoundaryRef`,
  `BoundaryBinding`, `ResolvedBoundary`, `PromoteBoundary`, and
  `anchored_boundary`, matching the item family they mirror; `Graph`'s field and
  methods follow as `boundary_values`, `boundaries()`, `resolve_boundary()`, and
  `promote_boundary()`. `ClockPosition` becomes `ClockCoordinate` and
  `ClockProfile` answers `coordinates`, `clock_index()`, `refined_coordinate()`,
  and `from_boundary_values()`. `WitnessCoordinate` collided on both halves and
  becomes `OrderedDelivery`, whose ordering key is now `order`;
  `ReactDeclaration.yield_coordinates` becomes `yield_deliveries`. In the fold,
  `Coordinate` becomes `IndexCoordinate` and `FoldDeclaration.coordinates()`
  becomes `index_coordinates()`, and `Provenance` is qualified as
  `DerivationProvenance`. No deprecated aliases are published: the release
  already carries breaking cover, and an alias nobody removes leaves two
  vocabularies standing. This rename initially left the wire spellings alone,
  but the later format break in this release moved `FORMAT_VERSION` from `"6"`
  to `"0.2.0"` and changed both the schema artifact and its stamp. The document key
  `position_values`, the `durable_position` schema definition, the `position`
  attribute domain and node kind, the `promote_position` opcode, the
  `/positions/…` canonical path segments, and the command line's own `position`
  vocabulary therefore moved with that format break rather than with this
  rename. The document key, schema definition, opcode, and canonical path
  segments retain their earlier spelling in format 0.2.0. The attribute domain
  and node kind now spell their wire values `boundary`, and the command line's
  `--kind` choice is `boundary` rather than `position`.
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
- The publishability check now reads every tracked file rather than a listed set
  of directories, so the files it reads and the files the source distribution
  ships are the same set. The release checklist, the contributor guide, the
  security policy, these release notes, the license, the build recipe, the
  workflows, the schema, and the runnable examples all ship and were all
  previously unread. The test suite builds the distribution and compares its
  contents against what the check reads, so the two cannot drift apart, and CI
  runs the check on every pull request instead of behind a path filter that
  could classify a shipped file as inert.
- **BREAKING:** wire and schema refusals now name every missing field and every
  unknown field in one message, rather than one lexically first name per
  attempt. A document carrying two unknown fields reports both, so anyone
  matching on the exact refusal text sees a different string.
- **BREAKING:** `json_schema` and `tiergraph schema --format-version` now refuse
  a format version this release does not implement, rather than printing the
  current shape under another label. The flag's meaning changes from "print any
  format's schema" to "assert which format you expect".
- `loads` and `validation_errors` now decide `format_version` before they
  consult the declared field set, so a document announcing a format this release
  does not implement is refused for its version rather than for whichever field
  name sorts first. `loads` also defers materializing omitted members until
  after that decision, so a foreign-version document is no longer rewritten
  toward this format's shape before the reader declines it.
- **BREAKING:** the document, program, and selector readers now share one
  numbered total order over the classes a refusal can belong to, published as
  `tiergraph.schema.RefusalStage` and carried on every refusal they raise, which
  is now a `tiergraph.schema.Refusal` — still a `ValueError`, so any caller that
  already catches one still does. The order runs envelope, encoding, syntax,
  construction, discriminator, shape, value, reference, semantics, and its
  principle is stated once: a refusal at one stage explains what a later stage
  would report, and the converse never holds. Stages rank the conditions of one
  node, and nodes are read outside in, so the pair of a node and a stage orders
  every condition a read can meet. Deciding the version before the field set
  generalizes from `format_version` to every discriminator: a JSONL header now
  reports its `machine_version` before the header's field set, so a program from
  a later release is told its version rather than the extra header member that
  being newer introduced, and a header carrying no stamp at all is reported for
  the stamp. Two conditions of one node are carried as data rather than prose:
  a field set that is both missing and unknown keeps its single combined message
  and also names the unknown-field condition on `Refusal.also`. `validation_errors`
  returns every applicable structural condition in that order instead of only the
  first, so a document with four problems is repaired in one pass rather than in
  four; a foreign version is still reported alone, because the field sets of a
  declaration the document never selected cannot honestly be judged. This
  refusal-order change itself did not alter the document shape, and the
  conformance probes kept their outcomes with no drift. The later format break
  in this release moved `FORMAT_VERSION` from `"6"` to `"0.2.0"` and changed
  both the schema artifact and its stamp.
- **BREAKING:** a fold with `ranked_output` now refuses a `tie_policy` instead of
  requiring one. The requirement demanded the one declaration that configuration
  could never read: ranked selection asks the semiring's addition which value it
  prefers and then orders equal-valued witnesses by their canonical witness path,
  which is total over distinct witnesses, so the executable policy in
  `_select_paths` was reached only through a `witness_order`, which ranked output
  already refuses. Every tied witness is retained, in canonical path order, up to
  `output_cap`, and that is now stated where the tie is broken rather than left to
  be inferred. `witness_order` and `tie_policy` remain one mechanism, declared
  together or not at all. `grammar.best()` and `tiergraph fold --ranked` declare no
  tie policy and return exactly what they returned before, tied derivations
  included. This fold change itself did not alter the document shape, and the
  conformance probes kept their outcomes. The later format break in this
  release moved `FORMAT_VERSION` from `"6"` to `"0.2.0"` and changed both the
  schema artifact and its stamp.

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

### Fixed

- Closed a file that could ship without being read. The publishability gate
  reads the git index while a source distribution is built from the working
  tree, so a file that is neither tracked nor ignored rode out unscanned; the
  distribution test compared each shipped name against the gate's exemption
  predicate, which answers about a name and not about membership, so it passed
  on exactly that file. The test now asserts that what ships is a subset of the
  paths the gate selects, and a planted untracked, unignored file fails it. A
  resolved lock file and a macOS Finder directory record, the two artifacts
  this tree can acquire in that condition, are now ignored: this project
  publishes libraries, which declare ranges and pin nothing for the programs
  that install them, so a lock belongs to whoever resolved it. The gate scripts
  also join the strict type-checking bar they were already outside of, having
  been inside every other bar this project keeps.

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
