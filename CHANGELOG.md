# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `Walk` now follows a polyadic relation as well as a bipartite one, so the
  library and the `walk` command reach the same relations the rest of traversal
  does. One step from any endpoint of the near side reaches every endpoint of
  the far side of that incidence, which is the step `OrderedPolyadicTraversal`
  already takes and the `k * m` edges the graph itself ranged over when it
  validated the declaration's `acyclic` promise; pairing the two sides off index
  by index is not a reading the declaration supports, because each side declares
  its own arity bounds and its own emptiness. `WalkDirection` keeps its meaning,
  forward being the declared `sources`-to-`targets` direction and inverse its
  fiber, and both directions still return a deduplicated `NodeSet`. An unbounded
  walk over a polyadic relation is admitted only for a declaration that promised
  acyclicity, and refused by the same message as before otherwise. `truncated`
  is unchanged and still one-sided. A relation of neither shape now refuses by
  naming both admitted shapes rather than only the bipartite one.
- Added `discharge`, the command-line verb for declarations. It sits beside
  `validate` rather than inside it: `validate` asks whether one document is
  well formed, and `discharge` asks whether a declaration holds against its
  inputs. `discharge seals` binds a source graph's seals to the result graph
  claiming to honor them and emits the public `SealCertificate.to_data()`
  counts. A refused discharge writes no certificate and reports the refusal's
  declared stage, its rank in the refusal order, and any further applicable
  condition as JSON on stderr, so a caller routes on the stage instead of
  matching wording. No graph document is written or read differently.
- Added `discharge fold`, which demands a fold's exactness claim against the
  graph, valuation, algebra, and dependency relation it reads and emits the
  public `FoldCertificate.to_data()` reach: the claim, the fold's own result,
  the probes the law search took, the derivations enumerated, and whether the
  enumeration finished. It is assembled from `fold`'s own flags with
  `--exactness` added, and `fold` itself gains no such flag, because it runs the
  declaration and never consults the claim. Omitting `--exactness` is not a
  usage error: it reaches the refusal that hands back the declaration to be
  made.
- Added `RewriteCertificate.to_data()` and `discharge rewrite`. The method is
  the wire form of a discharged effect claim, carrying the effect beside the two
  counts: a revision and a collapse can leave the same number of disturbances
  over the same number of subjects, so counts alone would report the two
  identically. `disturbances` is written as the count of ways the result failed
  to leave a structure standing, which is what the certificate holds, so one
  structure disturbed twice contributes two and the two counts are not a ratio.
  The command reads the same pair of graphs `discharge seals` reads, under the
  same flags, with `--effect` added; omitting it is not a usage error but
  reaches the refusal that hands back the declaration to be made rather than
  standing in COLLAPSE. This is an addition to a public type and a new
  subcommand: no graph document is written or read differently, and the schema
  artifact and its stamp are unaffected. `discharge` now covers three of the
  four published declaration kinds; a graph profile stays reachable only through
  the library, because its check returns nothing to certify.
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
  than reinterpreted. This change itself did not alter the document shape; the
  later format break in this release moved `FORMAT_VERSION` from `"6"` to
  `"0.2.0"`.
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
  semirings`, which lists every algebra the shell can name with its carrier
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
  that edge and leaves the value's old coordinate empty. This change itself did
  not alter the document shape; the later format break in this release moved
  `FORMAT_VERSION` from `"6"` to `"0.2.0"`.
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
  graph. This change itself did not alter the document shape; the later format
  break in this release moved `FORMAT_VERSION` from `"6"` to `"0.2.0"`.
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
  instead of reporting a comparison it never made. This change itself did not
  alter the document shape; the later format break in this release moved
  `FORMAT_VERSION` from `"6"` to `"0.2.0"`.

### Changed

- A fold that finds no root now reports `dependency graph has no root` rather
  than `dependency DAG has no root`. The dependency relation a fold runs over
  need not be acyclic, so the old wording named a structure the operation does
  not require. Only the wording changes.
- **BREAKING:** `grammar_loads` and `selection_loads` now read their text
  through the same staged reader `loads` uses, so every document reader this
  package exposes ranks the conditions its input can meet by one numbered
  order. Text that is
  not JSON, bytes that are not UTF-8, nesting past the depth limit, and input
  past the size limit now raise a `Refusal` carrying `RefusalStage.SYNTAX`,
  `RefusalStage.ENCODING`, or `RefusalStage.ENVELOPE`, where each previously
  let `json.JSONDecodeError` escape unstaged or accepted the input outright. A
  repeated object key, which those two readers previously resolved to its last
  occurrence, is now refused at `RefusalStage.SYNTAX` as it already was for a
  graph document. A caller catching `json.JSONDecodeError` from either reader
  has to catch `Refusal` or `ValueError` instead; `Refusal` is a `ValueError`,
  so a caller already catching that one is unaffected. Command-line exit
  statuses do not change, and `tiergraph grammar` and `tiergraph select` now
  report the staged wording their diagnostics already carried elsewhere. This
  change itself did not alter the document shape; the later format break in
  this release moved `FORMAT_VERSION` from `"6"` to `"0.2.0"`.
- `GraphValidationError` now carries a `stage`, so both channels this package
  refuses through report one vocabulary rather than two shapes. The stage
  defaults to `RefusalStage.SEMANTICS`, which is what a declaration or
  graph-contract violation already meant, and the message stays the first
  argument, so every existing raise and every existing `except ValueError` is
  unaffected. `RefusalStage` itself moves to `tiergraph.core`, the one module
  both channels reach without a cycle, and stays exported from
  `tiergraph.schema`, where callers import it.
- `GraphValidationError` is now a subclass of `Refusal`, so `except Refusal`
  catches every rank of the refusal order rather than the eight the document
  readers raise directly. The last rank, `RefusalStage.SEMANTICS`, is reported
  by the graph constructor through `GraphValidationError`, which shared no base
  with `Refusal` but `ValueError`; a caller who read the format document and
  wrote `except Refusal` therefore let the last stage of the order that document
  defines escape unhandled. `Refusal` and `RefusalStage` are now exported from
  `tiergraph` itself and documented beside `GraphValidationError`, so the base
  the order is declared in terms of is reached by a promised import rather than
  from `tiergraph.schema`, which carries no stability promise; both stay
  importable from `tiergraph.schema` as before. `Refusal` declares `stage` and
  `also` as typed attributes, so a caller reads them off what it caught instead
  of through `getattr` or an `isinstance` narrowing, and `GraphValidationError`
  now carries an empty `also`. Both remain `ValueError` subclasses, the stage
  values and their ranks are unchanged, and no document is read or written
  differently.
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
  `"kind": "boundary"` instead of `"kind": "position"`. This change itself did
  not alter the document shape; the later format break in this release moved
  `FORMAT_VERSION` from `"6"` to `"0.2.0"`.
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
  declares none. `tiergraph.schema` now declares the names it publishes, so
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
  which is total wherever the document's item labels are distinct, so the
  executable policy in `_select_paths` was reached only through a
  `witness_order`, which ranked output already refuses. Every tied witness is retained, in canonical path order, up to
  `output_cap`, and that is now stated where the tie is broken rather than left to
  be inferred. `witness_order` and `tie_policy` remain one mechanism, declared
  together or not at all. `grammar.best()` and `tiergraph fold --ranked` declare no
  tie policy and return exactly what they returned before, tied derivations
  included. This fold change itself did not alter the document shape, and the
  conformance probes kept their outcomes. The later format break in this
  release moved `FORMAT_VERSION` from `"6"` to `"0.2.0"` and changed both the
  schema artifact and its stamp.
- **BREAKING:** all four document readers now refuse a string the UTF-8 encoder
  cannot write, whichever way the text spells it. This is a soundness fix, not a
  change to the refusal order: `docs/format.md` already ranked this
  condition at `RefusalStage.ENCODING`, and the readers simply never asked. A
  character standing in the text was already refused there, but one written as
  an escape is not in the text at all -- `\ud800` is six ASCII bytes -- so it
  passed every check that reads bytes and became a character only once the
  parser built the value. `loads` therefore returned a `Graph` for
  `{"format_version":"0.2.0","graph":{"namespaces":[{"namespace":"\ud800","prefix":"p"}]}}`
  while `dumps`, `dump_compact`, `dump_bytes`, and `to_data` all refused that
  same graph at `RefusalStage.ENCODING`, so the format admitted documents with
  no canonical byte form -- the byte API `docs/format.md` names for `convert --to
  bytes` -- and a round trip through this package could not reproduce them.
  `loads`, `grammar_loads`, `selection_loads`, and the JSON Lines program reader
  now run the writer's own check on the parsed value and report the writer's own
  stage and wording, named by the path in the document that was read. Running
  the check after parsing does not
  make it a later condition: the order ranks conditions rather than the checks
  that find them, and the canonical text of such a document, which `dumps` writes
  without ASCII escaping, carries the character itself. One consequence is that
  the order is not fully realized on a document that is both unparseable and
  unencodable: it is reported at `SYNTAX`, rank 3, because until the text parses
  there is no value for the higher-ranked condition to hold of. `tiergraph
  validate` now refuses such a document at exit 1 where it previously reported
  success and left `convert` to refuse the same input, and seven captured corpus
  documents, already dispositioned `never-legal` for exactly this reason, now
  refuse rather than load. No narrowing is priced here and `FORMAT_VERSION` stays
  `"0.2.0"`: what shrinks is the set the reader admitted past what the format ever
  allowed, and nothing this package could write is refused.
- **BREAKING:** the fourth of those readers is the JSON Lines program reader, and
  it met both spellings of the condition. It reads a line at a time and so cannot
  share the staging the other three took, which measures one complete text; the
  condition is one it meets all the same, so it now runs that same imported check
  on each parsed record and reports it at `ENCODING`, scoped by the line number
  its other diagnostics already carry: `JSONL line 2: declaration.namespace value
  '\ud800' has unsupported character U+D800`. It answered the other spelling with
  the encoder's own exception rather than a staged refusal, because `program_loads`
  encoded its text argument outside every check, so a caller handing it a `str`
  holding the character got a `UnicodeEncodeError` the declared order does not
  name where the other three answered `encode UTF-8 failed: surrogates not
  allowed` at `ENCODING`. That text is now measured before it is encoded, in code
  points, each of which is at least one encoded byte, so an input meeting both the
  size and the encoding condition is still reported for the lower rank. Line
  orientation changes the scope a condition is reported in and not its rank: the
  canonical program holding such a record, which `program_dumps` writes without
  ASCII escaping, carries the character rather than the escape. The same
  incompleteness the other three leave holds of a line -- one both unparseable and
  unencodable is reported at `SYNTAX` -- while a condition on an earlier line
  preceding a higher-ranked one on a later line is the declared node ordering
  rather than that gap. `tiergraph step` and `tiergraph run` now refuse such a
  program at the reader, naming the record's own path and its line, where they
  previously read it and reached a writer; the corpus is unaffected, because its
  captured documents are graphs rather than programs. No narrowing is priced here
  and `FORMAT_VERSION` stays `"0.2.0"`.

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

- Closed the last writer that reached the UTF-8 encoder without asking it
  anything. `AsBuilt.fingerprint` hashes the encoded bytes of the as-built
  graph, and there are no bytes to hash for a graph the encoder cannot write,
  so `Program.fingerprint` raised the encoder's own `UnicodeEncodeError` --
  which names a position in a rendering nobody holds rather than a field of the
  graph -- where `wire.to_data` and `program_dumps` both answer the same string
  at `ENCODING` naming the field. It now asks through the same check, imported
  rather than restated, so one string meets one stage and one wording whichever
  writer a caller reached it from, and `Program.fingerprint` inherits it by
  delegating to the as-built one. Only a graph no writer in this package could
  ever have serialized is affected; every fingerprint this release computed
  before is unchanged.
- Held an orphan's retained coordinate to the namespace rule every other
  qualified name already met. `Graph.__post_init__` requires every qualified
  name a graph spells to use a namespace that graph declares, and it enumerated
  nine collections without reaching layer subjects. An orphan is the one layer
  subject nothing resolves -- every live subject goes to
  `_resolve_layer_subject`, which refuses an undeclared name, and the orphan is
  skipped precisely because there is nothing to resolve it against -- so its
  coordinate was the one name in the graph that met no check at all. Such a
  graph validated, and `dumps` then reached the encoder's `prefixes[namespace]`
  with no prefix to find and raised a bare `KeyError`, which is neither the
  encoding condition `to_data` says is the one it answers nor the `Refusal` it
  says it raises. Every spelling is now covered, the carrier and the retained
  reference alike, and the refusal is the wording the other nine collections
  already produce rather than a second account of one rule. It is decided in
  validation and not in the encoder because that is where the contract already
  lives: a layer's own vocabulary and a seal's carrier are refused at
  construction too, each by its own check, so an orphan's coordinate was an
  omission from a settled rule rather than a condition the writer had been
  left to answer. Nothing in this package ever built such a coordinate, the
  declared shape did not move, and a document already written is read as
  before.
- Reported an algebra leaving its carrier as the refused operation it is.
  `DoubleExtremumSemiring.multiply` refuses a sum that leaves the finite
  IEEE-double carrier, and it refuses with `OverflowError`, which is an
  `ArithmeticError` and so no kind of the `ValueError` the shell catches. A
  well-formed graph and a valid command line therefore produced a stack trace
  from `fold` and from `discharge fold`, where `docs/reference/cli.md` promises
  a diagnostic on stderr and exit status 1. The shell now answers the whole
  `ArithmeticError` family with the house refusal, for the reason `_fold_lift`
  already converts a carrier mismatch into one rather than letting a
  `TypeError` escape from inside the fold: what the caller met is their
  valuation leaving the algebra's carrier, and a caller routing on the class
  should not have to know which exception the arithmetic happened to raise.
  Nothing that succeeded before changes, and no earlier clause of the order is
  displaced.
- Held a rewrite's effect claim to the layers it was measured over. What a
  discharged `DECORATE` licenses is that every reading taken over the source is
  still a correct reading of the result, and a rewrite that deleted every layer
  discharged it: the reading in question stopped answering and started raising
  `GraphValidationError`, while the certificate reported `disturbances=0`. The
  facts a claim is held to enumerated nine of the graph's collections and not
  this one, so nothing a layer asserted was ever a subject. Each layer and each
  statement it holds is now a subject in the source's own reading order, placed
  after the boundary values and before the document, so dropping a layer is the
  collapse it is, replacing a statement's value is a revision, and the
  certificate's `subjects` count says how much the claim was actually held to.
  A rewrite over a graph carrying no layers is measured exactly as before.
  Seals are still not enumerated, and that omission is not the same one: a
  seal is answered by `SealDeclaration.check_seals` over the same pair of
  graphs, and a graph cannot hold a seal over a tier it does not declare, so
  what a seal asserts is decided either way. A layer had neither, which is why
  the same skip was a defect there.
- Closed the program writer's half of the encoding condition its reader
  already answers. `program_dumps` writes with `ensure_ascii=False`, so a lone
  surrogate in a record stood in the returned text as the character itself:
  the `str` it handed back had no UTF-8 encoding at all, `.encode("utf-8")`
  raised on it, and `load_program` refused that very text at `ENCODING` on the
  way back. `wire.to_data` has answered that condition for the graph writers
  since the format had one, and this writer answered nothing, so the asymmetry
  was reachable from any `Program` built in memory rather than read. It now
  refuses through the same check, imported rather than restated, naming the
  field path inside the record and the line the record would have stood on, so
  both halves of the codec say one sentence about one program. What is refused
  is what the reader already refuses, so no program that round-trips today
  stops doing so; a caller holding a `Program` this package could never have
  read now meets a staged refusal where it used to be handed unusable text.
- Bounded the read the program reader's envelopes were only ever measuring.
  `load_program` iterated whole lines, so both the running `MAX_DOCUMENT_BYTES`
  total and the per-line bound were checked against bytes already held: an
  input carrying no newline was materialized entire in order to be told it was
  too large, sixteen times the document envelope and two hundred and fifty-six
  times the line one, which is the cost reading incrementally exists to avoid
  and is reachable from `tiergraph run -` on standard input. The reader now
  asks the stream for one byte past the tighter of the two bounds, so a
  delivery long enough to cross a bound is refused on what was read rather than
  on what the rest of the line would have been. The bounds, their order, and
  every diagnostic are unchanged for input the reader accepts or refuses today;
  what changes is that a newline-free input over the line bound is reported
  against the line rather than against the program, because the line bound is
  the one it crosses first. A stream is read through `readline` rather than by
  iteration, which is the only visible difference to a caller passing an object
  of its own to `load_program`.
- Closed a writer that emitted what the reader refuses. Holding the reader to
  the declared minimum for a layer subject's coordinate left the graph
  constructor as the one authority out of step: an orphaned subject retaining a
  negative index still constructed, `dumps` wrote its nine thousand bytes of
  canonical text, and `loads` refused that text at the very coordinate the
  graph had been allowed to hold. `Graph` now refuses the coordinate in each
  spelling an orphan has -- a bare index under either graph carrier, an item
  coordinate, and a boundary coordinate -- and names the reader's `VALUE` stage,
  because the condition is the format's rather than a contract the format never
  mentions. The declaration module reads the kernel and not the other way
  round, so the kernel spells the bound and a test reads it back from the
  declaration; the two cannot drift without that test going red. An orphan is
  the one layer subject nothing resolves, which is why no other check caught
  it. Nothing in this package ever built such a coordinate: every orphan it
  makes retains where a live subject stood. The declared shape did not move,
  and a document already written is read as before.
- Narrowed the serialization guide to name the refusal that survives that fix.
  A graph the constructor accepts can still be written past
  `MAX_DOCUMENT_BYTES` and refused at `ENVELOPE` on the way back, because the
  offending size belongs to the canonical text rather than to any member a
  constructor could bound, and the guide said only that such conditions exist
  without naming the one that is reachable. The suite now witnesses it from the
  writer's side rather than by handing the reader fabricated text.
- Corrected a gate that reported a sentence its own corpus disproves.
  `make format-semantics` printed `every one of the 186 captured documents still
  loads` while seven of them no longer load: they carry an unpaired surrogate in
  a namespace URI, the reader now refuses them, and their `never-legal`
  dispositions are what make that refusal a pass rather than a break. The
  verdict was right and the sentence was false, which is the worse arrangement
  of the two, because the verdict is what a run turns on and the sentence is
  what gets quoted. A pass over a corpus holding an adjudicated refusal now
  reports how many documents loaded, how many did not, and that what is
  established is the absence of an unaccounted-for refusal rather than the
  correctness of any disposition.
- Widened the changelog gate's reach in the two places where it was decided by
  something nobody reviews. Its matchers ran over an entry's raw text, so a
  claim spelled across a line break read as absent -- the live changelog held
  one, an entry passing for no reason but where its closing sentence wrapped, so
  reflowing that paragraph would have turned the gate red with no code changing.
  Matching now runs over the entry with its whitespace flattened. Separately,
  the recognized stability wording was two exact spellings, while seven entries
  used a third and a fourth and so were never read at all. Those name the same
  tree observable, the released `FORMAT_VERSION` against the current one, so the
  vocabulary is now a closed cross product: the article, one of three subject
  phrases naming the wire or the document format, and one of two adjectives
  denying that it moved. The script's own docstring holds the spellings, which
  is where they can be written down without an entry that quotes a claim reading
  as an entry that makes one -- a distinction a closed lexical vocabulary cannot
  draw, and the reason this paragraph describes its terms instead of listing
  them. Reading those seven entries for the first time showed each to be making
  a release-scope claim this release contradicts, and each is reworded to the
  per-change claim it meant, beside the format break that moved
  `FORMAT_VERSION` from `"6"` to `"0.2.0"`.
- Closed a soundness hole in the JSON Lines program reader, found while
  reconciling that reader with the one refusal order `docs/format.md` declares
  over every document reader this package exposes. `load_program` handed each
  undecoded line to `json.loads`, which sniffs an encoding from the leading
  bytes, so a program written in UTF-16BE, UTF-32BE, or UTF-8 with a byte-order
  mark was not refused but read: it came back as an equal `Program` carrying
  every opcode and a matching fingerprint, built from bytes the format does not
  admit. The same sniffing mis-staged the refusals it did produce, reporting at
  `SYNTAX` a condition the order ranks at `ENCODING`. Separately, a repeated
  object key was accepted outright, because the reader parsed without the hook
  the other three readers pass, so
  `{"machine_version":"1","machine_version":"1"}` loaded as an empty program
  under the JSON library's last-wins rule instead of refusing an object the
  input does not determine. `load_program` and `program_loads` now decode each
  line as UTF-8 before parsing it and refuse a repeated key at `SYNTAX`, in the
  wording the document readers already used and scoped by the line the
  condition was met on; a syntax refusal now reads `JSONL line 2: parse JSON
  failed: Expecting value` rather than carrying the parser's line-and-column
  suffix, which measured a position inside a single line. The three
  whole-document readers were never exposed to this: they decode UTF-8
  explicitly. This refuses input that was previously accepted, including
  programs that previously loaded successfully.

- Closed the same soundness hole in the command line's two declarative profile
  readers, the last documents it read outside the shared envelope. `clock` and
  `span render` handed the profile's raw bytes to `json.loads`, which sniffs an
  encoding from the leading bytes, so a clock or span profile written in UTF-16
  or UTF-32 with a byte-order mark was not refused but read and queried, while
  the very same bytes arriving as the graph beside it were refused at
  `ENCODING`. `MAX_DOCUMENT_BYTES` and `MAX_JSON_DEPTH` did not reach a profile
  either: an oversized one was read whole, a deeply nested one was parsed past
  the declared bound and, past the interpreter's own limit, surfaced a
  `RecursionError` reported as a bare `ValueError` where the same text as a
  graph refuses at `SYNTAX`, and a repeated object key was accepted under the
  JSON library's last-wins rule. Both profiles now read through the reader every
  other document takes, so the envelope, encoding, and syntax conditions are
  answered at the same rank and in the same wording whether a text arrives as a
  profile or as a graph. A profile whose bytes are not UTF-8 at all therefore
  exits 1 carrying the staged refusal rather than 3 carrying the decoder's own
  exception, which is the status the graph beside it already used; the shell's
  `UnicodeError` arm now answers only an output stream that cannot encode what
  it is given. `--tokens-json` is unaffected: it takes an argument string, which
  no encoding is sniffed from. This refuses input that was previously accepted,
  including profiles that previously queried successfully.

- Closed four gate checks whose enumeration was narrower than the population
  each claimed, every one of them found by constructing a case that passed and
  should not have. `make gate` derives its import path from the makefile's own
  location, so a worktree run reads the checkout it is reporting on rather than
  whichever library the borrowed virtualenv installed -- with `FORMAT_VERSION`
  altered under `src/`, the schema step used to exit 0. The publishability gate
  enumerates the git index from the repository root and opens each file there,
  so a run started in a subdirectory no longer reports a clean tree it never
  read; the root is checked against `git rev-parse --show-toplevel` rather than
  assumed. The docstring gate requires a docstring that says something, so
  `def f(x): ""` no longer passes as documented. The changelog gate refuses a
  byte-identity claim that names no artifact it can resolve, instead of
  comparing nothing and reporting agreement. This change itself did not alter
  the document shape; the later format break in this release moved
  `FORMAT_VERSION` from `"6"` to `"0.2.0"`.

- Closed a file that could ship without being read. The publishability gate
  reads the git index while a source distribution is built from the working
  tree, so a file that is neither tracked nor ignored rode out unscanned; the
  distribution test compared each shipped name against the gate's exemption
  predicate, which answers about a name and not about membership, so it passed
  on exactly that file. The test now asserts that what ships is a subset of the
  paths the gate selects, and a planted untracked, unignored file fails it. A
  resolved lock file and a macOS Finder directory record, the two artifacts
  this tree was then known to acquire in that condition, are now ignored: this
  project publishes libraries, which declare ranges and pin nothing for the
  programs that install them, so a lock belongs to whoever resolved it. A
  third was found later in the same release and is ignored too: this project's
  git worktrees live under `.claude/`, one whole copy of the repository each,
  and the build backend selects the working tree minus what version control
  ignores. The gate scripts also join the strict type-checking bar they were
  already outside of, having been inside every other bar this project keeps.

- Held machine-program members to the types the format declares for them. The
  opcode decoders announced a member's type with `typing.cast`, which is erased
  before the program runs and therefore checks nothing, so `tiergraph run`
  accepted a tier long name of `7`, built a graph around it, exited zero, and
  wrote a document `tiergraph validate` then refused -- contradicting the
  published rule that `validate` settles the same question every conversion
  settles before emitting anything, and letting `Program.fingerprint()` publish
  a digest of a graph no reader can take back. Every such member is now read
  through the checked helpers the document reader uses, so the two readers
  refuse the same value at the same stage in the same words. Enumerated
  spellings were escaping as unstaged `ValueError`s from the enumeration
  constructor rather than as refusals, leaving `except Refusal` to miss a
  condition the declared order governs for every reader this package exposes;
  they are staged at `VALUE` now, where `loads` already staged them. An
  attachment target given as a JSON boolean reached the kernel as the integer
  one and attached its value to the wrong relation instance; a bare target must
  now be an integer.
- Made the canonical machine-program writer's own output readable. A polyadic
  side that constrains no tiers is written as an explicit null, and a relation
  instance tags its durable item endpoints; the reader admitted neither, so
  `program_loads(program_dumps(program))` refused programs this package had
  just written. Both spellings are read now, and no emitted byte changed.
- Derived the machine-program conformance sweep from the writer instead of a
  list. Seed programs realizing every opcode, attachment domain, relation
  carrier, and endpoint shape are encoded, and the JSON type the writer emits at
  each position is taken as the type declared there; substituting any other type
  must be refused, and every refusal must carry its stage. A member added to an
  opcode is probed as soon as the writer emits it, and the seeds are held
  against the opcode and enumeration members read from the code, so a new one
  fails the gate rather than falling outside it.

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
