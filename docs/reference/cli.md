# CLI reference

The `tiergraph` command prints help when called without arguments. `--version` prints one JSON object and exits successfully.

`tiergraph.cli.build_parser()` is importable and usable, but carries no API-stability promise at version 0.8.0.

## Contracts

Every command that reads an input document accepts `-` in place of that document's filename and reads it from standard input, including the inputs named by `--result`, `--profile`, and `--selector`; `step --interactive` is the one exception, below. `schema` and `semirings` read no document, and `discharge`, `path`, `grammar`, `clock`, and `span` take only a subcommand, so `-` is a command-line usage error for those and exits 2. Document-producing commands write to stdout by default or to `-o/--output`; diagnostics go only to stderr. Exit status 0 means success, 1 means invalid input or a refused operation, 2 means command-line usage error, and 3 means an I/O failure or an input the CLI could not decode. The CLI's own reports refuse a graph the writer could not write in the same way as the writer, with exit status 1.

`--max-steps N` is an opt-in deterministic work guard on mutating `edit` and `patch apply` commands, `select`, `match`, `fold`, `discharge fold`, and the grammar commands that evaluate a fold (`recognize`, `count`, `best`, and `generate`). `N` is a positive integer no greater than 1,000,000,000; omission installs no budget and preserves the unbudgeted behavior. Use it when accepting untrusted pattern or predicate text. Exhaustion is a semantics-stage refusal on stderr and exits 1; a completed nonempty prefix from `match ... spans` instead succeeds with `extent` equal to `cut-at-budget`. Match request JSON accepts the same optional `max_steps` field for `exists`, `focus`, `spans`, `count`, `pairs`, and `lattice`; supplying both the field and the flag is refused rather than choosing one silently. `grammar lattice` takes no guard because it constructs and serializes topology without running a budgeted fold, path-plan evaluation, or lattice match.

`validate` reports whether `loads()` accepts a document, and that is the same question `convert` settles before emitting anything. A document the encoder cannot write, such as one spelling a lone surrogate as an escape, is refused by both at the reader's encoding stage, producing exit status 1 for a refused operation. `convert` canonicalizes to indented `json`, compact `json-compact`, or `bytes`; bytes uses the canonical JSON byte API and is not another syntax.

`run` consumes a CLI-owned JSONL stream. Its first line is exactly `{"machine_version":"2"}` and each later line has one opcode's public `to_data()` shape (a repeat body remains nested on that line). Header-only programs are valid, CRLF and a final line without a newline are accepted, and whitespace-only lines are rejected. The decoder caps each line at 1 MiB and the stream at `MAX_DOCUMENT_BYTES`; public `Repeat` and `Program` enforce repeat and total expansion bounds.

`step` reads that same JSONL program and drives the public `steps()` generator. Its default dump mode writes one deterministic compact JSON object per yielded `Step.to_data()` value. `--interactive` (or a TTY) provides `step`/`next`, `continue`, `run-to N`/`break N`, `print`/`inspect`, `list`, and `quit`. A refused opcode exits 1 after reporting its index and the last good graph, with no traceback. Interactive programs must come from a file because stdin carries REPL commands.

`inspect` reports tiers in graph order and relation declarations in canonical graph order (qualified-name order), not source declaration order.

`semirings` lists every algebra this shell can name, with its carrier boundary, its five declared law checks, and its declared properties. The listed names are exactly the values `fold --semiring` accepts.

`fold` evaluates a finite dependency relation with one of those algebras and emits the public `FoldResult.to_data()` report. `--tier` and `--transition` are repeatable; `--root` is repeatable and, when omitted, the roots are the domain items nothing depends on. The valuation carries the attribute's local name, because that name only ever appears in a refusal. Two lifts are nameable: `value` embeds the read attribute value in the carrier, and `one` embeds the semiring's multiplicative identity regardless of the value. A general lift, a witness order, and an index product are caller code, so they stay in the Python API; without a witness order the report's `provenance` is always null, and `--ranked` is the shell's route to witnesses. `--ranked` needs an algebra that declares `multiply_preserves_witness_order` and supplies the tie policy the declaration requires but ranked selection never consults. `--output-cap` caps ranked witnesses and so requires `--ranked`.

`select --selector FILE` evaluates a strict selector JSON document. `select --where TEXT` instead parses a value predicate with `PredicateSyntax.for_graph`; `--prefix` chooses the default namespace prefix for unqualified attribute names. The two input forms are mutually exclusive, and `--prefix` applies only to `--where`.

`edit` runs one checked operation against a validated graph. Direct item and boundary operands use TG-PATH. Other feature targets and layer fact subjects use `document`, `tier:NS|LOCAL`, `relation-declaration:NS|LOCAL`, `relation:N`, `polyadic:N`, `relation-id:ID`, or `polyadic-id:ID`. `edit bulk` evaluates one selector, predicate, or exhaustive match before applying a delete or attribute operation in displacement-safe order. Structural edits on a clock-profile graph require both `--clock-profile` and an explicit named `--rebinding` policy; commands that cannot preserve that profile refuse those options.

Every graph-producing edit and patch application can write a dry-run report, a forward patch, and an inverse patch. Caller annotations are copied into the patch header and operations; timestamps are never synthesized. `--max-steps` charges one step for a direct edit, one per selected bulk callback in addition to selection work, and one per applied patch operation. `--in-place` writes and validates a temporary graph in the destination directory before replacing the input atomically. Graph and side-artifact destinations must differ from every file operand.

`patch show` writes one JSON object with `patch_version`, `base_fingerprint`, `target_fingerprint`, `annotations`, and `operations` fields. `patch invert` and `patch compose` preserve the versioned fingerprint guards. `diff` emits a deterministic executable patch in the selected equivalence view, and `program from-graph` emits an exact from-empty construction program.

`action` and `react` are library-only. `ActionDeclaration` binds its behavior as an `ActionFunction` Python callable, and `ReactDeclaration` also binds a `DeliveryYield` callable. Neither callable has a declarative or wire form for the CLI to read, so the shell cannot construct either declaration without inventing an executable callback format.

## JSON input formats

All objects are strict: unknown or missing fields are refused. A qualified name is an object with string `namespace` and `local_name` fields.

### Selector documents

`select --selector` accepts one selector object. Leaf forms are `tier`, `type`, `items`, `boundaries`, `item`, `boundary`, and `attribute`. `item` and `boundary` carry a `path`; the tier-based forms carry a qualified name. `where` carries `base` and `predicate`; `sequence` carries `ordering` and `pattern`. Compound forms use `op` equal to `union` or `intersection` with a nonempty `args` array, or `difference` with `left` and `right`.

```json
{
  "select": "items",
  "tier": {
    "local_name": "tokens",
    "namespace": "urn:example"
  }
}
```

### Ordering and match requests

`match --ordering` accepts `tier` with `tier`; `containers` with `relation` and `containers`; `adjacent-runs` with `source` and `offsets`; or `declared` with `successor`, `members`, optional `chain`, and optional boolean `open_left`. Qualified-name fields use the shape shown below; selector fields use the selector shapes above.

```json
{
  "order": "tier",
  "tier": {
    "local_name": "tokens",
    "namespace": "urn:example"
  }
}
```

`match --request` accepts `match` equal to `exists`, `focus`, `spans`, or `count`, plus `ordering` and a tagged pattern AST. `spans` may add a nonnegative `limit`. Every operation may add positive `max_steps`. Pattern tags are `atom` with `predicate`, `seq` or `alt` with `parts`, `repeat` with `body`, `min`, and optional `max`, `focus` with `body`, and the field-only `start` and `end` forms.

```json
{
  "limit": 20,
  "match": "spans",
  "max_steps": 100000,
  "ordering": {
    "order": "tier",
    "tier": {
      "local_name": "tokens",
      "namespace": "urn:example"
    }
  },
  "pattern": {
    "pattern": "atom",
    "predicate": {
      "args": [],
      "test": "and"
    }
  }
}
```

The `pairs` request instead requires selector fields `left` and `right`, an interval `relation`, and an `offsets` object with qualified-name `origin`, exactly one of `extent` or `end`, and optional `partition`; it may add `limit` and `max_steps`. The `lattice` request requires one qualified name in `transitions`, an array of item references in `roots`, qualified-name `emission`, `pattern`, and `policy`. Policy is `{"policy": "unambiguous"}` or `determinize` with positive `max_states`; `max_steps` is optional.

### Span and clock profiles

A span profile requires the seven fields shown below. Optional fields are `base_surface_attribute`, `point_tiers`, `point_coverage_relation`, `value_attributes`, and `clock_face` (`tick` or `physical`). Nullable qualified-name roles are written as JSON null.

```json
{
  "alternative_relation": null,
  "base_tier": {
    "local_name": "tokens",
    "namespace": "urn:example"
  },
  "char_offset_attribute": null,
  "coverage_relation": {
    "local_name": "coverage",
    "namespace": "urn:example"
  },
  "score_attribute": {
    "local_name": "score",
    "namespace": "urn:example"
  },
  "span_tiers": [
    {
      "local_name": "words",
      "namespace": "urn:example"
    }
  ],
  "value_attribute": {
    "local_name": "text",
    "namespace": "urn:example"
  }
}
```

A clock profile requires all nine fields below. The clock tier, binding relation, and unit attribute are qualified names; every other attribute role is a qualified name or null.

```json
{
  "binding_relation": {
    "local_name": "clock-binding",
    "namespace": "urn:example"
  },
  "clock_tier": {
    "local_name": "clock",
    "namespace": "urn:example"
  },
  "duration_attribute": null,
  "gap_attribute": null,
  "rate_attribute": null,
  "start_attribute": null,
  "tick_attribute": null,
  "unit_attribute": {
    "local_name": "unit",
    "namespace": "urn:example"
  },
  "untimed_attribute": null
}
```

### Editing operands and replacement policies

Item, attribute, relation-instance, declaration, and layer-fact files use the corresponding public `to_data()` object shape. Every operand is bounded strict JSON. A replacement-policy object may contain only the fields shown below. `default` and each `action` are `abandon`, `follow`, or `split`; `correspond` enables local equal-content alignment; explicit `correspondence` entries map one old item reference to an ordered array of new item references. Relation, layer, and insertion-point arrays override the default for their named carriers. Omitted fields use the library's abandonment defaults.

```json
{
  "correspond": true,
  "correspondence": [
    {
      "new": [
        {
          "index": 0,
          "tier": {
            "local_name": "tokens",
            "namespace": "urn:example"
          }
        }
      ],
      "old": {
        "index": 0,
        "tier": {
          "local_name": "tokens",
          "namespace": "urn:example"
        }
      }
    }
  ],
  "default": "abandon",
  "insertion_points": [
    {
      "index": 0,
      "name": {
        "local_name": "tokens",
        "namespace": "urn:example"
      }
    }
  ],
  "layers": [
    {
      "action": "split",
      "name": {
        "source": "hand",
        "vocabulary": "urn:example"
      }
    }
  ],
  "relations": [
    {
      "action": "follow",
      "name": {
        "local_name": "tokens",
        "namespace": "urn:example"
      }
    }
  ]
}
```

### Grammar documents and inputs

A grammar document contains `nonterminals`, `start`, and `rules`. Each rule has `left`, source and target arrays of tagged `terminal` or `hole` elements, `boundary`, `awaited_variables`, and nullable `weight`; optional `provenance` is an array. Attribute values contain `name`, `value_type`, and `lexical`.

```json
{
  "nonterminals": [
    {
      "local_name": "S",
      "namespace": "urn:example:grammar"
    }
  ],
  "rules": [
    {
      "awaited_variables": [],
      "boundary": {
        "lexical": "complete",
        "name": {
          "local_name": "boundary",
          "namespace": "urn:tiergraph:grammar"
        },
        "value_type": "string"
      },
      "left": {
        "local_name": "S",
        "namespace": "urn:example:grammar"
      },
      "source": [
        {
          "kind": "terminal",
          "text": {
            "lexical": "x",
            "name": {
              "local_name": "text",
              "namespace": "urn:example:grammar"
            },
            "value_type": "string"
          }
        }
      ],
      "target": [
        {
          "kind": "terminal",
          "text": {
            "lexical": "x",
            "name": {
              "local_name": "text",
              "namespace": "urn:example:grammar"
            },
            "value_type": "string"
          }
        }
      ],
      "weight": null
    }
  ],
  "start": {
    "local_name": "S",
    "namespace": "urn:example:grammar"
  }
}
```

`--tokens-json` is a JSON array of strings. `--input-json` is an object with a `tokens` array. Each typed token requires string `symbol` and a nonempty `realization` array; optional fields are `provenance`, `source`, and `span`. Each realization requires a string `tokens` array and may add string `provenance` and decimal-string `weight`.

```json
{
  "tokens": [
    {
      "realization": [
        {
          "tokens": [
            "x"
          ]
        }
      ],
      "span": {
        "end": 1,
        "origin": 0,
        "partition": null
      },
      "symbol": "x"
    }
  ]
}
```

### Fold requests

`fold` and `discharge fold` do not read request JSON. They assemble the request from `--name`, the attribute namespace and local name, repeatable `--tier` and `--transition`, `--semiring`, `--lift`, repeatable `--root`, `--ranked`, `--output-cap`, and `--max-steps`. `discharge fold` also reads `--exactness`.

## Implementation limits

- Maximum CLI work budget: 1,000,000,000 steps.
- Maximum compiled pattern states: 1,000,000.
- Maximum pattern AST nodes: 10,000.
- Pattern text nesting limit: 256.
- Pattern AST nesting limit: 256.
- Predicate text nesting limit: 256.
- Regular-expression nesting limit: 256.
- JSONL program line cap: 1,048,576 bytes. This is 1 MiB.

## Deterministic stepping example

For a program whose first opcode declares prefix `s` for `urn:step`, dump its exact public step states:

```console
$ tiergraph step program.jsonl
{"graph":{"attribute_declarations":[],"attributes":[],"layers":[],"namespaces":[{"namespace":"urn:step","prefix":"s"}],"position_values":[],"relation_declarations":[],"relations":[],"seals":[],"tiers":[]},"index":0,"opcode":{"declaration":{"namespace":"urn:step","prefix":"s"},"opcode":"declare_namespace"}}
```

Each output line is independently parseable JSON.

## Help

### `tiergraph`

```text
usage: tiergraph [-h] [--version]
                 {validate,discharge,render,inspect,convert,schema,run,step,walk,path,grammar,clock,span,select,match,fold,semirings,edit,patch,diff,program}
                 ...

Validate, query, transform, and render tiergraph documents.

positional arguments:
  {validate,discharge,render,inspect,convert,schema,run,step,walk,path,grammar,clock,span,select,match,fold,semirings,edit,patch,diff,program}
    validate            validate a graph document
    discharge           discharge a declaration against its inputs
    render              render a graph as DOT
    inspect             inspect a graph document
    convert             canonicalize a graph document
    schema              print the graph document schema
    run                 execute a JSONL machine program
    step                step through a JSONL machine program
    walk                traverse a transitive relation
    path                resolve and spell tiergraph paths
    grammar             work with tiergraph grammars
    clock               query declarative clock timing
    span                render declarative span views
    select              evaluate a selector
    match               match a regular item sequence
    fold                fold a dependency relation
    semirings           list the semirings this shell can name
    edit                apply one checked graph edit
    patch               apply, inspect, invert, or compose patches
    diff                build an executable graph patch
    program             build machine programs

options:
  -h, --help            show this help message and exit
  --version             print the version

Examples:
  $ tiergraph validate graph.json

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph validate`

```text
usage: tiergraph validate [-h] FILE

Validate one graph document and print 'ok' when it is accepted.

positional arguments:
  FILE        graph file, or - for stdin

options:
  -h, --help  show this help message and exit

Examples:
  $ tiergraph validate graph.json

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph discharge`

```text
usage: tiergraph discharge [-h] {seals,rewrite,fold} ...

Check a declared seal, rewrite effect, or fold exactness claim.

positional arguments:
  {seals,rewrite,fold}
    seals               discharge a source graph's seals against a result
                        graph
    rewrite             discharge a rewrite's effect claim against the pair it
                        read
    fold                discharge a fold's exactness claim against its graph

options:
  -h, --help            show this help message and exit

Examples:
  $ tiergraph discharge seals source.json --result result.json

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph discharge seals`

```text
usage: tiergraph discharge seals [-h] --result FILE [--name NAME] [-o FILE]
                                 SOURCE

Check that a result graph honors every seal on its source graph.

positional arguments:
  SOURCE                source graph file, or - for stdin

options:
  -h, --help            show this help message and exit
  --result FILE         result graph file
  --name NAME           name used in refusals
  -o FILE, --output FILE
                        output file (default: -)

Examples:
  $ tiergraph discharge seals source.json --result result.json

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph discharge rewrite`

```text
usage: tiergraph discharge rewrite [-h] --result FILE [--name NAME]
                                   [--effect {decorate,revise,collapse}]
                                   [-o FILE]
                                   SOURCE

Check a rewrite effect claim against its source and result graphs.

positional arguments:
  SOURCE                source graph file, or - for stdin

options:
  -h, --help            show this help message and exit
  --result FILE         result graph file
  --name NAME           name used in refusals
  --effect {decorate,revise,collapse}
                        the claim to discharge; omitted, the library refuses
                        UNDECLARED
  -o FILE, --output FILE
                        output file (default: -)

Examples:
  $ tiergraph discharge rewrite source.json --result result.json --effect decorate

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph discharge fold`

```text
usage: tiergraph discharge fold [-h] [--name NAME] --attribute-namespace NS
                                --attribute-local LOCAL --tier NS LOCAL
                                --semiring
                                {arctic,boolean,counting,decimal-arctic,decimal-tropical,log-probability,path,tropical}
                                --lift {one,value} --transition NS LOCAL
                                COMBINATION [--root TGPATH] [--ranked]
                                [--output-cap N] [--max-steps N]
                                [--exactness {distributive,approximate,structural}]
                                [-o FILE]
                                GRAPH

Check a fold exactness claim against the graph and valuation.

positional arguments:
  GRAPH                 graph file, or - for stdin

options:
  -h, --help            show this help message and exit
  --name NAME           name used in refusals
  --attribute-namespace NS
                        valuation attribute namespace URI
  --attribute-local LOCAL
                        valuation attribute local name
  --tier NS LOCAL       one valuation domain tier; repeatable
  --semiring {arctic,boolean,counting,decimal-arctic,decimal-tropical,log-probability,path,tropical}
                        named algebra used by the fold
  --lift {one,value}    embed the read value, or the semiring's multiplicative
                        identity
  --transition NS LOCAL COMBINATION
                        one dependency relation and its and/or meaning;
                        repeatable
  --root TGPATH         one declared root item; repeatable, inferred when
                        omitted
  --ranked              also report witnesses ranked by the semiring's own
                        order
  --output-cap N        witness cap; requires --ranked
  --max-steps N         refuse after N deterministic work steps (maximum:
                        1000000000)
  --exactness {distributive,approximate,structural}
                        the claim to discharge; omitted, the library refuses
                        UNDECLARED
  -o FILE, --output FILE
                        output file (default: -)

Examples:
  $ tiergraph discharge fold fold.json --attribute-namespace urn:test:fold --attribute-local cost --tier urn:test:fold tasks --semiring counting --lift one --transition urn:test:fold depends or --exactness distributive

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph render`

```text
usage: tiergraph render [-h] [-o FILE] [--include-empty-tiers] FILE

Render one graph document in Graphviz DOT notation.

positional arguments:
  FILE                  graph file, or - for stdin

options:
  -h, --help            show this help message and exit
  -o FILE, --output FILE
                        output file (default: -)
  --include-empty-tiers
                        include empty tiers

Examples:
  $ tiergraph render graph.json -o graph.dot

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph inspect`

```text
usage: tiergraph inspect [-h] [-o FILE] FILE

Print graph_summary counts and per-tier and per-relation details.

positional arguments:
  FILE                  graph file, or - for stdin

options:
  -h, --help            show this help message and exit
  -o FILE, --output FILE
                        output file (default: -)

Examples:
  $ tiergraph inspect graph.json

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph convert`

```text
usage: tiergraph convert [-h] [-o FILE] --to {json,json-compact,bytes} FILE

Validate and rewrite a graph in one canonical output encoding.

positional arguments:
  FILE                  graph file, or - for stdin

options:
  -h, --help            show this help message and exit
  -o FILE, --output FILE
                        output file (default: -)
  --to {json,json-compact,bytes}
                        output encoding

Examples:
  $ tiergraph convert graph.json --to json-compact -o compact.json

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph schema`

```text
usage: tiergraph schema [-h] [--format-version VERSION] [--hash] [-o FILE]

Print the JSON Schema or its deterministic shape hash.

options:
  -h, --help            show this help message and exit
  --format-version VERSION
                        format version to request (default: current)
  --hash                print the shape hash
  -o FILE, --output FILE
                        output file (default: -)

Examples:
  $ tiergraph schema --hash

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph run`

```text
usage: tiergraph run [-h] [-o FILE] --to {json,json-compact,bytes,dot}
                     [--include-empty-tiers]
                     FILE

Execute a JSONL machine program and emit its final graph.

positional arguments:
  FILE                  JSONL program file, or - for stdin

options:
  -h, --help            show this help message and exit
  -o FILE, --output FILE
                        output file (default: -)
  --to {json,json-compact,bytes,dot}
                        final graph encoding
  --include-empty-tiers
                        include empty tiers in DOT output

Examples:
  $ tiergraph run program.jsonl --to json -o graph.json

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph step`

```text
usage: tiergraph step [-h] [-o FILE] [--interactive] FILE

Emit each machine step or enter the interactive debugger.

positional arguments:
  FILE                  JSONL program file, or - for stdin

options:
  -h, --help            show this help message and exit
  -o FILE, --output FILE
                        output file (default: -)
  --interactive         use the interactive debugger (also enabled when stdin
                        is a TTY)

Examples:
  $ tiergraph step program.jsonl -o steps.jsonl

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph walk`

```text
usage: tiergraph walk [-h] --source PATH --relation-namespace NS
                      --relation-local LOCAL [--direction {forward,inverse}]
                      [--cap N] [-o FILE]
                      GRAPH

Traverse one declared acyclic relation from one or more paths.

positional arguments:
  GRAPH                 graph file, or - for stdin

options:
  -h, --help            show this help message and exit
  --source PATH         source TG-PATH; repeatable
  --relation-namespace NS
                        relation namespace URI
  --relation-local LOCAL
                        relation local name
  --direction {forward,inverse}
                        traversal direction (default: forward)
  --cap N               maximum traversal depth
  -o FILE, --output FILE
                        output file (default: -)

Examples:
  $ tiergraph walk walk.json --source /items/structural/urn:test:traversal/nodes/0 --relation-namespace urn:test:traversal --relation-local contains

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph path`

```text
usage: tiergraph path [-h] {resolve,spell} ...

Resolve TG-PATH text or spell a structural or durable path.

positional arguments:
  {resolve,spell}
    resolve        resolve a tiergraph path
    spell          spell a tiergraph path

options:
  -h, --help       show this help message and exit

Examples:
  $ tiergraph path resolve graph.json /items/durable/alpha

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph path resolve`

```text
usage: tiergraph path resolve [-h] [--profile FILE] [-o FILE] GRAPH TGPATH

Resolve one TG-PATH against a graph and print its binding.

positional arguments:
  GRAPH                 graph file, or - for stdin
  TGPATH                tiergraph path to resolve

options:
  -h, --help            show this help message and exit
  --profile FILE        declarative grammar chart profile
  -o FILE, --output FILE
                        output file (default: -)

Examples:
  $ tiergraph path resolve graph.json /items/durable/alpha

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph path spell`

```text
usage: tiergraph path spell [-h] --kind {item,boundary} [--tier-namespace NS]
                            [--tier-local LOCAL] [--index N] [--durable-id ID]
                            [--anchor-item-id ID] [--anchor-tier-namespace NS]
                            [--anchor-tier-local LOCAL]
                            [--side {before,after}] [-o FILE]
                            GRAPH

Spell a durable or structural TG-PATH for an item or boundary.

positional arguments:
  GRAPH                 graph file, or - for stdin

options:
  -h, --help            show this help message and exit
  --kind {item,boundary}
                        kind of graph position to spell
  --tier-namespace NS   tier namespace URI
  --tier-local LOCAL    tier local name
  --index N             structural index
  --durable-id ID       durable item identifier
  --anchor-item-id ID   durable boundary anchor item
  --anchor-tier-namespace NS
                        durable boundary anchor tier namespace URI
  --anchor-tier-local LOCAL
                        durable boundary anchor tier local name
  --side {before,after}
                        side of the anchor item
  -o FILE, --output FILE
                        output file (default: -)

Examples:
  $ tiergraph path spell graph.json --kind item --durable-id alpha

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph grammar`

```text
usage: tiergraph grammar [-h] {recognize,count,best,generate,lattice} ...

Recognize, count, rank, or generate with a grammar document.

positional arguments:
  {recognize,count,best,generate,lattice}
    recognize           recognize a token sequence
    count               count token-sequence derivations
    best                find best token-sequence derivations
    generate            generate experimental target derivations
    lattice             emit an experimental target lattice

options:
  -h, --help            show this help message and exit

JSON formats: see docs/reference/cli.md#json-input-formats.

Examples:
  $ tiergraph grammar recognize grammar.json --tokens-json '["x"]'

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph grammar recognize`

```text
usage: tiergraph grammar recognize [-h] --tokens-json JSON [--max-steps N]
                                   [--forest] [-o FILE]
                                   GRAMMAR

Recognize a token sequence with one grammar declaration.

positional arguments:
  GRAMMAR               grammar JSON file, or - for stdin

options:
  -h, --help            show this help message and exit
  --tokens-json JSON    JSON array of source token strings
  --max-steps N         refuse after N deterministic work steps (maximum:
                        1000000000)
  --forest              emit the complete parse forest
  -o FILE, --output FILE
                        output file (default: -)

--tokens-json is a JSON array of strings. Grammar document fields are in docs/reference/cli.md#json-input-formats.

Examples:
  $ tiergraph grammar recognize grammar.json --tokens-json '["x"]'

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph grammar count`

```text
usage: tiergraph grammar count [-h] --tokens-json JSON [--max-steps N]
                               [-o FILE]
                               GRAMMAR

Count token-sequence derivations with one grammar declaration.

positional arguments:
  GRAMMAR               grammar JSON file, or - for stdin

options:
  -h, --help            show this help message and exit
  --tokens-json JSON    JSON array of source token strings
  --max-steps N         refuse after N deterministic work steps (maximum:
                        1000000000)
  -o FILE, --output FILE
                        output file (default: -)

--tokens-json is a JSON array of strings. Grammar document fields are in docs/reference/cli.md#json-input-formats.

Examples:
  $ tiergraph grammar count grammar.json --tokens-json '["x"]'

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph grammar best`

```text
usage: tiergraph grammar best [-h] --tokens-json JSON [--max-steps N]
                              [--count N] [-o FILE]
                              GRAMMAR

Find best token-sequence derivations with one grammar declaration.

positional arguments:
  GRAMMAR               grammar JSON file, or - for stdin

options:
  -h, --help            show this help message and exit
  --tokens-json JSON    JSON array of source token strings
  --max-steps N         refuse after N deterministic work steps (maximum:
                        1000000000)
  --count N             maximum derivations to emit (default: 1)
  -o FILE, --output FILE
                        output file (default: -)

--tokens-json is a JSON array of strings. Grammar document fields are in docs/reference/cli.md#json-input-formats.

Examples:
  $ tiergraph grammar best grammar.json --tokens-json '["x"]'

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph grammar generate`

```text
usage: tiergraph grammar generate [-h] --input-json INPUT [--count N]
                                  [--max-steps N] [-o FILE]
                                  GRAMMAR

Generate experimental target derivations from typed grammar input.

positional arguments:
  GRAMMAR               grammar JSON file, or - for stdin

options:
  -h, --help            show this help message and exit
  --input-json INPUT    typed grammar input as JSON
  --count N             maximum target derivations to emit
  --max-steps N         refuse after N deterministic work steps (maximum:
                        1000000000)
  -o FILE, --output FILE
                        output file (default: -)

--input-json is a typed GrammarInput object. Its fields and the grammar document fields are in docs/reference/cli.md#json-input-formats.

Examples:
  $ tiergraph grammar generate grammar.json --input-json '{"tokens":[{"symbol":"x","realization":[{"tokens":["x"]}],"span":{"partition":null,"origin":0,"end":1}}]}'

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph grammar lattice`

```text
usage: tiergraph grammar lattice [-h] --input-json INPUT [-o FILE] GRAMMAR

Emit an experimental target lattice from typed grammar input.

positional arguments:
  GRAMMAR               grammar JSON file, or - for stdin

options:
  -h, --help            show this help message and exit
  --input-json INPUT    typed grammar input as JSON
  -o FILE, --output FILE
                        output file (default: -)

--input-json is a typed GrammarInput object. Its fields and the grammar document fields are in docs/reference/cli.md#json-input-formats.

Examples:
  $ tiergraph grammar lattice grammar.json --input-json '{"tokens":[{"symbol":"x","realization":[{"tokens":["x"]}],"span":{"partition":null,"origin":0,"end":1}}]}'

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph clock`

```text
usage: tiergraph clock [-h] {coordinates,boundary,extent,item} ...

Query structural and physical time through a clock profile.

positional arguments:
  {coordinates,boundary,extent,item}
    coordinates         list refined clock coordinates
    boundary            query one tier boundary
    extent              query a timed tier extent
    item                query one timed item

options:
  -h, --help            show this help message and exit

Profile fields are in docs/reference/cli.md#json-input-formats.

Examples:
  $ tiergraph clock coordinates clock.json --profile clock-profile.json

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph clock coordinates`

```text
usage: tiergraph clock coordinates [-h] --profile FILE [-o FILE] GRAPH

List refined clock coordinates through a clock profile.

positional arguments:
  GRAPH                 graph file, or - for stdin

options:
  -h, --help            show this help message and exit
  --profile FILE        clock profile JSON file, or - for stdin
  -o FILE, --output FILE
                        output file (default: -)

Profile fields are in docs/reference/cli.md#json-input-formats.

Examples:
  $ tiergraph clock coordinates clock.json --profile clock-profile.json

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph clock boundary`

```text
usage: tiergraph clock boundary [-h] --profile FILE --boundary PATH [-o FILE]
                                GRAPH

Query one tier boundary through a clock profile.

positional arguments:
  GRAPH                 graph file, or - for stdin

options:
  -h, --help            show this help message and exit
  --profile FILE        clock profile JSON file, or - for stdin
  --boundary PATH       boundary TG-PATH to query
  -o FILE, --output FILE
                        output file (default: -)

Profile fields are in docs/reference/cli.md#json-input-formats.

Examples:
  $ tiergraph clock boundary clock.json --profile clock-profile.json --boundary /positions/structural/urn:tiergraph:profile:clock:test/segment/1

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph clock extent`

```text
usage: tiergraph clock extent [-h] --profile FILE --tier-namespace NS
                              --tier-local LOCAL [-o FILE]
                              GRAPH

Query a timed tier extent through a clock profile.

positional arguments:
  GRAPH                 graph file, or - for stdin

options:
  -h, --help            show this help message and exit
  --profile FILE        clock profile JSON file, or - for stdin
  --tier-namespace NS   timed tier namespace URI
  --tier-local LOCAL    timed tier local name
  -o FILE, --output FILE
                        output file (default: -)

Profile fields are in docs/reference/cli.md#json-input-formats.

Examples:
  $ tiergraph clock extent clock.json --profile clock-profile.json --tier-namespace urn:tiergraph:profile:clock:test --tier-local segment

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph clock item`

```text
usage: tiergraph clock item [-h] --profile FILE --item PATH [-o FILE] GRAPH

Query one timed item through a clock profile.

positional arguments:
  GRAPH                 graph file, or - for stdin

options:
  -h, --help            show this help message and exit
  --profile FILE        clock profile JSON file, or - for stdin
  --item PATH           item TG-PATH to query
  -o FILE, --output FILE
                        output file (default: -)

Profile fields are in docs/reference/cli.md#json-input-formats.

Examples:
  $ tiergraph clock item clock.json --profile clock-profile.json --item /items/structural/urn:tiergraph:profile:clock:test/segment/1

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph span`

```text
usage: tiergraph span [-h] {render} ...

Render span-oriented projections selected by a profile.

positional arguments:
  {render}
    render    render a span view

options:
  -h, --help  show this help message and exit

Profile fields are in docs/reference/cli.md#json-input-formats.

Examples:
  $ tiergraph span render spans.json --profile span-profile.json --format text

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph span render`

```text
usage: tiergraph span render [-h] --profile FILE --format
                             {text,json,jsonl,html,dot,textgrid}
                             [--alternatives] [--jsonl-record {input,span}]
                             [--include-empty-tiers] [-o FILE]
                             GRAPH

Render one declarative span view in the selected format.

positional arguments:
  GRAPH                 graph file, or - for stdin

options:
  -h, --help            show this help message and exit
  --profile FILE        span profile JSON file, or - for stdin
  --format {text,json,jsonl,html,dot,textgrid}
                        output format
  --alternatives        include alternative span readings where supported
  --jsonl-record {input,span}
                        JSONL record unit; requires --format jsonl
  --include-empty-tiers
                        include empty tiers; requires --format dot
  -o FILE, --output FILE
                        output file (default: -)

Profile fields are in docs/reference/cli.md#json-input-formats.

Examples:
  $ tiergraph span render spans.json --profile span-profile.json --format text

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph select`

```text
usage: tiergraph select [-h] (--selector FILE | --where TEXT) [--prefix P]
                        [--max-steps N] [-o FILE]
                        GRAPH

Evaluate selector JSON or a value predicate against a graph.

positional arguments:
  GRAPH                 graph file, or - for stdin

options:
  -h, --help            show this help message and exit
  --selector FILE       strict selector JSON file, or - for stdin
  --where TEXT          value predicate applied to every item
  --prefix P            default graph prefix for unqualified --where
                        attributes
  --max-steps N         refuse after N deterministic work steps (maximum:
                        1000000000)
  -o FILE, --output FILE
                        output file (default: -)

Selector JSON fields are in docs/reference/cli.md#json-input-formats.

Examples:
  $ tiergraph select graph.json --selector selector.json

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph match`

```text
usage: tiergraph match [-h] (--request FILE | --pattern TEXT)
                       [--ordering JSON] [--prefix P] [--limit LIMIT]
                       [--max-steps N] [-o FILE]
                       GRAPH [{exists,focus,spans,count}]

Evaluate a regular item pattern over one declared ordering.

positional arguments:
  GRAPH                 graph file, or - for stdin
  {exists,focus,spans,count}
                        view to evaluate with --pattern

options:
  -h, --help            show this help message and exit
  --request FILE        strict match request JSON file, or - for stdin
  --pattern TEXT        regular sequence pattern text
  --ordering JSON       ordering JSON object for --pattern
  --prefix P            default graph prefix for unqualified pattern
                        attributes
  --limit LIMIT         maximum spans to emit; valid only for spans
  --max-steps N         refuse after N deterministic work steps (maximum:
                        1000000000)
  -o FILE, --output FILE
                        output file (default: -)

Pattern text: '.' matches one item; '{predicate}' tests one item; '( )' groups; '|' or '/' alternates; '*', '+', '?', and '{n}' repeat; '^' and '$' anchor; '_' marks focus. Request and ordering JSON fields are in docs/reference/cli.md#json-input-formats.

Examples:
  $ tiergraph match graph.json --pattern . --ordering '{"order":"tier","tier":{"namespace":"urn:path","local_name":"tokens"}}' exists

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph fold`

```text
usage: tiergraph fold [-h] [--name NAME] --attribute-namespace NS
                      --attribute-local LOCAL --tier NS LOCAL --semiring
                      {arctic,boolean,counting,decimal-arctic,decimal-tropical,log-probability,path,tropical}
                      --lift {one,value} --transition NS LOCAL COMBINATION
                      [--root TGPATH] [--ranked] [--output-cap N]
                      [--max-steps N] [-o FILE]
                      GRAPH

Evaluate a finite dependency relation with a named semiring.

positional arguments:
  GRAPH                 graph file, or - for stdin

options:
  -h, --help            show this help message and exit
  --name NAME           name used in refusals
  --attribute-namespace NS
                        valuation attribute namespace URI
  --attribute-local LOCAL
                        valuation attribute local name
  --tier NS LOCAL       one valuation domain tier; repeatable
  --semiring {arctic,boolean,counting,decimal-arctic,decimal-tropical,log-probability,path,tropical}
                        named algebra used by the fold
  --lift {one,value}    embed the read value, or the semiring's multiplicative
                        identity
  --transition NS LOCAL COMBINATION
                        one dependency relation and its and/or meaning;
                        repeatable
  --root TGPATH         one declared root item; repeatable, inferred when
                        omitted
  --ranked              also report witnesses ranked by the semiring's own
                        order
  --output-cap N        witness cap; requires --ranked
  --max-steps N         refuse after N deterministic work steps (maximum:
                        1000000000)
  -o FILE, --output FILE
                        output file (default: -)

Fold requests are assembled from these flags; fold does not read a separate request JSON document.

Examples:
  $ tiergraph fold fold.json --attribute-namespace urn:test:fold --attribute-local cost --tier urn:test:fold tasks --semiring counting --lift one --transition urn:test:fold depends or

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph semirings`

```text
usage: tiergraph semirings [-h] [-o FILE]

List the named semirings accepted by fold commands.

options:
  -h, --help            show this help message and exit
  -o FILE, --output FILE
                        output file (default: -)

Examples:
  $ tiergraph semirings

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph edit`

```text
usage: tiergraph edit [-h]
                      GRAPH
                      {apply,insert,delete,replace,move,swap,relate,unrelate,endpoints,feature,declare,undeclare,promote,demote,seal,layer,replace-subtree,swap-subtrees,bulk}
                      ...

Apply one checked edit to a graph document.

positional arguments:
  GRAPH                 graph file, or - for stdin
  {apply,insert,delete,replace,move,swap,relate,unrelate,endpoints,feature,declare,undeclare,promote,demote,seal,layer,replace-subtree,swap-subtrees,bulk}
    apply               apply a fingerprint-guarded patch
    insert              insert one item
    delete              remove one item or an item run
    replace             replace one item's stored value
    move                move one item within its tier
    swap                swap two items
    relate              insert one relation instance
    unrelate            remove one relation instance
    endpoints           replace one relation instance's endpoints
    feature             set or remove one typed attribute
    declare             insert one declaration
    undeclare           remove one declaration
    promote             promote one durable identity
    demote              demote one durable identity
    seal                set, shorten, or remove a seal record
    layer               add or remove a layer or edit one fact
    replace-subtree     replace one containment root's descendants
    swap-subtrees       swap two disjoint containment subtrees
    bulk                apply one edit to a selector or exhaustive match

options:
  -h, --help            show this help message and exit

Item and boundary targets use TG-PATH. Other edit targets use document, tier:NS|LOCAL, relation-declaration:NS|LOCAL, relation:N, polyadic:N, relation-id:ID, or polyadic-id:ID. JSON operands use the public to_data() shapes documented for the Python values.

Examples:
  $ tiergraph edit graph.json move /items/durable/alpha --to 1 -o out.json
  $ tiergraph edit graph.json apply --patch change.jsonl --in-place

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph edit apply`

```text
usage: tiergraph edit GRAPH apply [-h] [-o FILE | --in-place] [--dry-run]
                                  [--report FILE] [--record FILE]
                                  [--inverse-out FILE] [--annotate KEY=JSON]
                                  [--max-steps N] --patch FILE

Apply a fingerprint-guarded patch.

options:
  -h, --help            show this help message and exit
  -o FILE, --output FILE
                        output graph (default: -)
  --in-place            atomically replace GRAPH
  --dry-run             validate and report without writing GRAPH
  --report FILE         write the edit report as JSON
  --record FILE         write the forward patch as JSONL
  --inverse-out FILE    write the inverse patch as JSONL
  --annotate KEY=JSON   attach caller-supplied patch metadata; repeatable
  --max-steps N         refuse after N deterministic work steps (maximum:
                        1000000000)
  --patch FILE          patch JSONL

Examples:
  $ tiergraph edit graph.json apply --patch change.jsonl -o out.json

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph edit insert`

```text
usage: tiergraph edit GRAPH insert [-h] [-o FILE | --in-place] [--dry-run]
                                   [--report FILE] [--record FILE]
                                   [--inverse-out FILE] [--clock-profile FILE]
                                   [--rebinding {keep-earlier,drop-to-provisional}]
                                   [--annotate KEY=JSON] [--max-steps N]
                                   --tier NS LOCAL --at N --item FILE

Insert one item.

options:
  -h, --help            show this help message and exit
  -o FILE, --output FILE
                        output graph (default: -)
  --in-place            atomically replace GRAPH
  --dry-run             validate and report without writing GRAPH
  --report FILE         write the edit report as JSON
  --record FILE         write the forward patch as JSONL
  --inverse-out FILE    write the inverse patch as JSONL
  --clock-profile FILE  validate and edit through this clock profile
  --rebinding {keep-earlier,drop-to-provisional}
                        named clock rebinding policy; requires --clock-profile
  --annotate KEY=JSON   attach caller-supplied patch metadata; repeatable
  --max-steps N         refuse after N deterministic work steps (maximum:
                        1000000000)
  --tier NS LOCAL       destination tier
  --at N                insertion index
  --item FILE           item JSON

Examples:
  $ tiergraph edit graph.json insert --tier urn:path tokens --at 1 --item item.json -o out.json

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph edit delete`

```text
usage: tiergraph edit GRAPH delete [-h] [-o FILE | --in-place] [--dry-run]
                                   [--report FILE] [--record FILE]
                                   [--inverse-out FILE] [--clock-profile FILE]
                                   [--rebinding {keep-earlier,drop-to-provisional}]
                                   [--annotate KEY=JSON] [--max-steps N]
                                   [--count N]
                                   TGPATH

Remove one item or an item run.

positional arguments:
  TGPATH                item TG-PATH

options:
  -h, --help            show this help message and exit
  -o FILE, --output FILE
                        output graph (default: -)
  --in-place            atomically replace GRAPH
  --dry-run             validate and report without writing GRAPH
  --report FILE         write the edit report as JSON
  --record FILE         write the forward patch as JSONL
  --inverse-out FILE    write the inverse patch as JSONL
  --clock-profile FILE  validate and edit through this clock profile
  --rebinding {keep-earlier,drop-to-provisional}
                        named clock rebinding policy; requires --clock-profile
  --annotate KEY=JSON   attach caller-supplied patch metadata; repeatable
  --max-steps N         refuse after N deterministic work steps (maximum:
                        1000000000)
  --count N             item count (default: 1)

Examples:
  $ tiergraph edit graph.json delete /items/durable/gamma -o out.json

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph edit replace`

```text
usage: tiergraph edit GRAPH replace [-h] [-o FILE | --in-place] [--dry-run]
                                    [--report FILE] [--record FILE]
                                    [--inverse-out FILE] [--annotate KEY=JSON]
                                    [--max-steps N] --item FILE
                                    TGPATH

Replace one item's stored value.

positional arguments:
  TGPATH                item TG-PATH

options:
  -h, --help            show this help message and exit
  -o FILE, --output FILE
                        output graph (default: -)
  --in-place            atomically replace GRAPH
  --dry-run             validate and report without writing GRAPH
  --report FILE         write the edit report as JSON
  --record FILE         write the forward patch as JSONL
  --inverse-out FILE    write the inverse patch as JSONL
  --annotate KEY=JSON   attach caller-supplied patch metadata; repeatable
  --max-steps N         refuse after N deterministic work steps (maximum:
                        1000000000)
  --item FILE           item JSON

Examples:
  $ tiergraph edit graph.json replace /items/durable/alpha --item replacement-item.json -o out.json

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph edit move`

```text
usage: tiergraph edit GRAPH move [-h] [-o FILE | --in-place] [--dry-run]
                                 [--report FILE] [--record FILE]
                                 [--inverse-out FILE] [--clock-profile FILE]
                                 [--rebinding {keep-earlier,drop-to-provisional}]
                                 [--annotate KEY=JSON] [--max-steps N] --to N
                                 TGPATH

Move one item within its tier.

positional arguments:
  TGPATH                item TG-PATH

options:
  -h, --help            show this help message and exit
  -o FILE, --output FILE
                        output graph (default: -)
  --in-place            atomically replace GRAPH
  --dry-run             validate and report without writing GRAPH
  --report FILE         write the edit report as JSON
  --record FILE         write the forward patch as JSONL
  --inverse-out FILE    write the inverse patch as JSONL
  --clock-profile FILE  validate and edit through this clock profile
  --rebinding {keep-earlier,drop-to-provisional}
                        named clock rebinding policy; requires --clock-profile
  --annotate KEY=JSON   attach caller-supplied patch metadata; repeatable
  --max-steps N         refuse after N deterministic work steps (maximum:
                        1000000000)
  --to N                destination index

Examples:
  $ tiergraph edit graph.json move /items/durable/alpha --to 1 -o out.json

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph edit swap`

```text
usage: tiergraph edit GRAPH swap [-h] [-o FILE | --in-place] [--dry-run]
                                 [--report FILE] [--record FILE]
                                 [--inverse-out FILE] [--clock-profile FILE]
                                 [--rebinding {keep-earlier,drop-to-provisional}]
                                 [--annotate KEY=JSON] [--max-steps N]
                                 TGPATH TGPATH

Swap two items.

positional arguments:
  TGPATH                first item TG-PATH
  TGPATH                second item TG-PATH

options:
  -h, --help            show this help message and exit
  -o FILE, --output FILE
                        output graph (default: -)
  --in-place            atomically replace GRAPH
  --dry-run             validate and report without writing GRAPH
  --report FILE         write the edit report as JSON
  --record FILE         write the forward patch as JSONL
  --inverse-out FILE    write the inverse patch as JSONL
  --clock-profile FILE  validate and edit through this clock profile
  --rebinding {keep-earlier,drop-to-provisional}
                        named clock rebinding policy; requires --clock-profile
  --annotate KEY=JSON   attach caller-supplied patch metadata; repeatable
  --max-steps N         refuse after N deterministic work steps (maximum:
                        1000000000)

Examples:
  $ tiergraph edit graph.json swap /items/durable/alpha /items/durable/beta -o out.json

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph edit relate`

```text
usage: tiergraph edit GRAPH relate [-h] [-o FILE | --in-place] [--dry-run]
                                   [--report FILE] [--record FILE]
                                   [--inverse-out FILE] [--annotate KEY=JSON]
                                   [--max-steps N] --instance FILE [--at N]

Insert one relation instance.

options:
  -h, --help            show this help message and exit
  -o FILE, --output FILE
                        output graph (default: -)
  --in-place            atomically replace GRAPH
  --dry-run             validate and report without writing GRAPH
  --report FILE         write the edit report as JSON
  --record FILE         write the forward patch as JSONL
  --inverse-out FILE    write the inverse patch as JSONL
  --annotate KEY=JSON   attach caller-supplied patch metadata; repeatable
  --max-steps N         refuse after N deterministic work steps (maximum:
                        1000000000)
  --instance FILE       relation JSON
  --at N                global instance index

Examples:
  $ tiergraph edit graph.json relate --instance relation.json -o out.json

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph edit unrelate`

```text
usage: tiergraph edit GRAPH unrelate [-h] [-o FILE | --in-place] [--dry-run]
                                     [--report FILE] [--record FILE]
                                     [--inverse-out FILE]
                                     [--annotate KEY=JSON] [--max-steps N]
                                     TARGET

Remove one relation instance.

positional arguments:
  TARGET                relation:N, polyadic:N, relation-id:ID, or polyadic-
                        id:ID

options:
  -h, --help            show this help message and exit
  -o FILE, --output FILE
                        output graph (default: -)
  --in-place            atomically replace GRAPH
  --dry-run             validate and report without writing GRAPH
  --report FILE         write the edit report as JSON
  --record FILE         write the forward patch as JSONL
  --inverse-out FILE    write the inverse patch as JSONL
  --annotate KEY=JSON   attach caller-supplied patch metadata; repeatable
  --max-steps N         refuse after N deterministic work steps (maximum:
                        1000000000)

Examples:
  $ tiergraph edit graph.json unrelate relation:0 -o out.json

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph edit endpoints`

```text
usage: tiergraph edit GRAPH endpoints [-h] [-o FILE | --in-place] [--dry-run]
                                      [--report FILE] [--record FILE]
                                      [--inverse-out FILE]
                                      [--clock-profile FILE]
                                      [--rebinding {keep-earlier,drop-to-provisional}]
                                      [--annotate KEY=JSON] [--max-steps N]
                                      --sources FILE --targets FILE
                                      TARGET

Replace one relation instance's endpoints.

positional arguments:
  TARGET                relation target

options:
  -h, --help            show this help message and exit
  -o FILE, --output FILE
                        output graph (default: -)
  --in-place            atomically replace GRAPH
  --dry-run             validate and report without writing GRAPH
  --report FILE         write the edit report as JSON
  --record FILE         write the forward patch as JSONL
  --inverse-out FILE    write the inverse patch as JSONL
  --clock-profile FILE  validate and edit through this clock profile
  --rebinding {keep-earlier,drop-to-provisional}
                        named clock rebinding policy; requires --clock-profile
  --annotate KEY=JSON   attach caller-supplied patch metadata; repeatable
  --max-steps N         refuse after N deterministic work steps (maximum:
                        1000000000)
  --sources FILE        endpoint array JSON
  --targets FILE        endpoint array JSON

Examples:
  $ tiergraph edit graph.json endpoints relation:0 --sources sources.json --targets targets.json -o out.json

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph edit feature`

```text
usage: tiergraph edit GRAPH feature [-h] [-o FILE | --in-place] [--dry-run]
                                    [--report FILE] [--record FILE]
                                    [--inverse-out FILE] [--annotate KEY=JSON]
                                    [--max-steps N] [--attribute FILE]
                                    [--name NS LOCAL]
                                    {set,remove} TARGET

Set or remove one typed attribute.

positional arguments:
  {set,remove}          feature operation
  TARGET                TG-PATH, document, qualified declaration, or relation
                        target

options:
  -h, --help            show this help message and exit
  -o FILE, --output FILE
                        output graph (default: -)
  --in-place            atomically replace GRAPH
  --dry-run             validate and report without writing GRAPH
  --report FILE         write the edit report as JSON
  --record FILE         write the forward patch as JSONL
  --inverse-out FILE    write the inverse patch as JSONL
  --annotate KEY=JSON   attach caller-supplied patch metadata; repeatable
  --max-steps N         refuse after N deterministic work steps (maximum:
                        1000000000)
  --attribute FILE      attribute JSON for set
  --name NS LOCAL       attribute name for remove

Examples:
  $ tiergraph edit graph.json feature set /items/durable/alpha --attribute attribute.json -o out.json

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph edit declare`

```text
usage: tiergraph edit GRAPH declare [-h] [-o FILE | --in-place] [--dry-run]
                                    [--report FILE] [--record FILE]
                                    [--inverse-out FILE] [--annotate KEY=JSON]
                                    [--max-steps N] --declaration FILE
                                    [--at N]

Insert one declaration.

options:
  -h, --help            show this help message and exit
  -o FILE, --output FILE
                        output graph (default: -)
  --in-place            atomically replace GRAPH
  --dry-run             validate and report without writing GRAPH
  --report FILE         write the edit report as JSON
  --record FILE         write the forward patch as JSONL
  --inverse-out FILE    write the inverse patch as JSONL
  --annotate KEY=JSON   attach caller-supplied patch metadata; repeatable
  --max-steps N         refuse after N deterministic work steps (maximum:
                        1000000000)
  --declaration FILE    declaration JSON
  --at N                declaration index

Examples:
  $ tiergraph edit graph.json declare --declaration declaration.json -o out.json

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph edit undeclare`

```text
usage: tiergraph edit GRAPH undeclare [-h] [-o FILE | --in-place] [--dry-run]
                                      [--report FILE] [--record FILE]
                                      [--inverse-out FILE]
                                      [--clock-profile FILE]
                                      [--rebinding {keep-earlier,drop-to-provisional}]
                                      [--annotate KEY=JSON] [--max-steps N]
                                      (--prefix P | --name NS LOCAL)
                                      [--cascade]

Remove one declaration.

options:
  -h, --help            show this help message and exit
  -o FILE, --output FILE
                        output graph (default: -)
  --in-place            atomically replace GRAPH
  --dry-run             validate and report without writing GRAPH
  --report FILE         write the edit report as JSON
  --record FILE         write the forward patch as JSONL
  --inverse-out FILE    write the inverse patch as JSONL
  --clock-profile FILE  validate and edit through this clock profile
  --rebinding {keep-earlier,drop-to-provisional}
                        named clock rebinding policy; requires --clock-profile
  --annotate KEY=JSON   attach caller-supplied patch metadata; repeatable
  --max-steps N         refuse after N deterministic work steps (maximum:
                        1000000000)
  --prefix P            namespace prefix
  --name NS LOCAL       qualified declaration name
  --cascade             remove dependents first

Examples:
  $ tiergraph edit graph.json undeclare --prefix extra -o out.json

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph edit promote`

```text
usage: tiergraph edit GRAPH promote [-h] [-o FILE | --in-place] [--dry-run]
                                    [--report FILE] [--record FILE]
                                    [--inverse-out FILE] [--annotate KEY=JSON]
                                    [--max-steps N] --id ID
                                    {item,boundary,relation,polyadic} TARGET

Promote one durable identity.

positional arguments:
  {item,boundary,relation,polyadic}
                        identity carrier kind
  TARGET                TG-PATH or relation target

options:
  -h, --help            show this help message and exit
  -o FILE, --output FILE
                        output graph (default: -)
  --in-place            atomically replace GRAPH
  --dry-run             validate and report without writing GRAPH
  --report FILE         write the edit report as JSON
  --record FILE         write the forward patch as JSONL
  --inverse-out FILE    write the inverse patch as JSONL
  --annotate KEY=JSON   attach caller-supplied patch metadata; repeatable
  --max-steps N         refuse after N deterministic work steps (maximum:
                        1000000000)
  --id ID               new durable identifier

Examples:
  $ tiergraph edit graph.json promote relation relation:0 --id link-0 -o out.json

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph edit demote`

```text
usage: tiergraph edit GRAPH demote [-h] [-o FILE | --in-place] [--dry-run]
                                   [--report FILE] [--record FILE]
                                   [--inverse-out FILE] [--annotate KEY=JSON]
                                   [--max-steps N]
                                   {item,boundary,relation,polyadic} TARGET

Demote one durable identity.

positional arguments:
  {item,boundary,relation,polyadic}
                        identity carrier kind
  TARGET                TG-PATH or relation target

options:
  -h, --help            show this help message and exit
  -o FILE, --output FILE
                        output graph (default: -)
  --in-place            atomically replace GRAPH
  --dry-run             validate and report without writing GRAPH
  --report FILE         write the edit report as JSON
  --record FILE         write the forward patch as JSONL
  --inverse-out FILE    write the inverse patch as JSONL
  --annotate KEY=JSON   attach caller-supplied patch metadata; repeatable
  --max-steps N         refuse after N deterministic work steps (maximum:
                        1000000000)

Examples:
  $ tiergraph edit graph.json demote item /items/durable/alpha -o out.json

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph edit seal`

```text
usage: tiergraph edit GRAPH seal [-h] [-o FILE | --in-place] [--dry-run]
                                 [--report FILE] [--record FILE]
                                 [--inverse-out FILE] [--annotate KEY=JSON]
                                 [--max-steps N] [--sealed N]
                                 {set,unseal,drop} CARRIER

Set, shorten, or remove a seal record.

positional arguments:
  {set,unseal,drop}     seal operation
  CARRIER               relations, polyadic-relations, or NS|LOCAL

options:
  -h, --help            show this help message and exit
  -o FILE, --output FILE
                        output graph (default: -)
  --in-place            atomically replace GRAPH
  --dry-run             validate and report without writing GRAPH
  --report FILE         write the edit report as JSON
  --record FILE         write the forward patch as JSONL
  --inverse-out FILE    write the inverse patch as JSONL
  --annotate KEY=JSON   attach caller-supplied patch metadata; repeatable
  --max-steps N         refuse after N deterministic work steps (maximum:
                        1000000000)
  --sealed N            sealed prefix length

Examples:
  $ tiergraph edit graph.json seal set urn:path|tokens --sealed 1 -o out.json

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph edit layer`

```text
usage: tiergraph edit GRAPH layer [-h] [-o FILE | --in-place] [--dry-run]
                                  [--report FILE] [--record FILE]
                                  [--inverse-out FILE] [--annotate KEY=JSON]
                                  [--max-steps N] --vocabulary URI --source
                                  NAME [--fact FILE] [--subject TARGET]
                                  [--name NS LOCAL]
                                  {add,remove,put-fact,remove-fact}

Add or remove a layer or edit one fact.

positional arguments:
  {add,remove,put-fact,remove-fact}
                        layer operation

options:
  -h, --help            show this help message and exit
  -o FILE, --output FILE
                        output graph (default: -)
  --in-place            atomically replace GRAPH
  --dry-run             validate and report without writing GRAPH
  --report FILE         write the edit report as JSON
  --record FILE         write the forward patch as JSONL
  --inverse-out FILE    write the inverse patch as JSONL
  --annotate KEY=JSON   attach caller-supplied patch metadata; repeatable
  --max-steps N         refuse after N deterministic work steps (maximum:
                        1000000000)
  --vocabulary URI      layer vocabulary URI
  --source NAME         layer source name
  --fact FILE           fact JSON for put-fact
  --subject TARGET      TG-PATH, document, qualified declaration, or relation
                        target
  --name NS LOCAL       fact value name

Examples:
  $ tiergraph edit graph.json layer add --vocabulary urn:path --source hand -o out.json

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph edit replace-subtree`

```text
usage: tiergraph edit GRAPH replace-subtree [-h] [-o FILE | --in-place]
                                            [--dry-run] [--report FILE]
                                            [--record FILE]
                                            [--inverse-out FILE]
                                            [--clock-profile FILE]
                                            [--rebinding {keep-earlier,drop-to-provisional}]
                                            [--annotate KEY=JSON]
                                            [--max-steps N] --containment NS
                                            LOCAL --new-graph FILE --new-root
                                            TGPATH [--policies FILE]
                                            TGPATH

Replace one containment root's descendants.

positional arguments:
  TGPATH                preserved subtree root

options:
  -h, --help            show this help message and exit
  -o FILE, --output FILE
                        output graph (default: -)
  --in-place            atomically replace GRAPH
  --dry-run             validate and report without writing GRAPH
  --report FILE         write the edit report as JSON
  --record FILE         write the forward patch as JSONL
  --inverse-out FILE    write the inverse patch as JSONL
  --clock-profile FILE  validate and edit through this clock profile
  --rebinding {keep-earlier,drop-to-provisional}
                        named clock rebinding policy; requires --clock-profile
  --annotate KEY=JSON   attach caller-supplied patch metadata; repeatable
  --max-steps N         refuse after N deterministic work steps (maximum:
                        1000000000)
  --containment NS LOCAL
                        containment relation; repeatable
  --new-graph FILE      graph containing the new subtree
  --new-root TGPATH     new subtree root
  --policies FILE       replacement policy JSON

Examples:
  $ tiergraph edit graph.json replace-subtree /items/durable/alpha --containment urn:path contains --new-graph new-graph.json --new-root /items/durable/alpha -o out.json

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph edit swap-subtrees`

```text
usage: tiergraph edit GRAPH swap-subtrees [-h] [-o FILE | --in-place]
                                          [--dry-run] [--report FILE]
                                          [--record FILE] [--inverse-out FILE]
                                          [--annotate KEY=JSON]
                                          [--max-steps N] --containment NS
                                          LOCAL [--first-policies FILE]
                                          [--second-policies FILE]
                                          TGPATH TGPATH

Swap two disjoint containment subtrees.

positional arguments:
  TGPATH                first subtree root
  TGPATH                second subtree root

options:
  -h, --help            show this help message and exit
  -o FILE, --output FILE
                        output graph (default: -)
  --in-place            atomically replace GRAPH
  --dry-run             validate and report without writing GRAPH
  --report FILE         write the edit report as JSON
  --record FILE         write the forward patch as JSONL
  --inverse-out FILE    write the inverse patch as JSONL
  --annotate KEY=JSON   attach caller-supplied patch metadata; repeatable
  --max-steps N         refuse after N deterministic work steps (maximum:
                        1000000000)
  --containment NS LOCAL
                        containment relation; repeatable
  --first-policies FILE
                        first replacement policy JSON
  --second-policies FILE
                        second replacement policy JSON

Examples:
  $ tiergraph edit graph.json swap-subtrees /items/durable/alpha /items/durable/beta --containment urn:path contains -o out.json

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph edit bulk`

```text
usage: tiergraph edit GRAPH bulk [-h] [-o FILE | --in-place] [--dry-run]
                                 [--report FILE] [--record FILE]
                                 [--inverse-out FILE] [--annotate KEY=JSON]
                                 [--max-steps N]
                                 (--selector FILE | --where TEXT | --match PATTERN)
                                 [--ordering JSON] [--prefix P]
                                 (--delete | --set-attribute FILE | --remove-attribute NS LOCAL)

Apply one edit to a selector or exhaustive match.

options:
  -h, --help            show this help message and exit
  -o FILE, --output FILE
                        output graph (default: -)
  --in-place            atomically replace GRAPH
  --dry-run             validate and report without writing GRAPH
  --report FILE         write the edit report as JSON
  --record FILE         write the forward patch as JSONL
  --inverse-out FILE    write the inverse patch as JSONL
  --annotate KEY=JSON   attach caller-supplied patch metadata; repeatable
  --max-steps N         refuse after N deterministic work steps (maximum:
                        1000000000)
  --selector FILE       selector JSON
  --where TEXT          item predicate text
  --match PATTERN       regular item pattern
  --ordering JSON       ordering JSON for --match
  --prefix P            default graph prefix
  --delete              delete selected items or relations
  --set-attribute FILE  attribute JSON to set
  --remove-attribute NS LOCAL
                        attribute name to remove

Examples:
  $ tiergraph edit graph.json bulk --selector selector.json --set-attribute attribute.json -o out.json

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph patch`

```text
usage: tiergraph patch [-h] {apply,show,invert,compose} ...

Work with versioned fingerprint-guarded patch JSONL.

positional arguments:
  {apply,show,invert,compose}
    apply               apply a patch
    show                show a patch as JSON
    invert              write the inverse patch
    compose             compose adjacent patches

options:
  -h, --help            show this help message and exit

Examples:
  $ tiergraph patch show change.jsonl

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph patch apply`

```text
usage: tiergraph patch apply [-h] [-o FILE | --in-place] [--dry-run]
                             [--report FILE] [--record FILE]
                             [--inverse-out FILE] [--annotate KEY=JSON]
                             [--max-steps N]
                             PATCH GRAPH

Apply a patch to its identified base.

positional arguments:
  PATCH                 patch JSONL
  GRAPH                 base graph

options:
  -h, --help            show this help message and exit
  -o FILE, --output FILE
                        output graph (default: -)
  --in-place            atomically replace GRAPH
  --dry-run             validate and report without writing GRAPH
  --report FILE         write the edit report as JSON
  --record FILE         write the forward patch as JSONL
  --inverse-out FILE    write the inverse patch as JSONL
  --annotate KEY=JSON   attach caller-supplied patch metadata; repeatable
  --max-steps N         refuse after N deterministic work steps (maximum:
                        1000000000)

Examples:
  $ tiergraph patch apply change.jsonl graph.json -o out.json

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph patch show`

```text
usage: tiergraph patch show [-h] [-o FILE] PATCH

Show a patch as json.

positional arguments:
  PATCH                 patch JSONL

options:
  -h, --help            show this help message and exit
  -o FILE, --output FILE
                        output file (default: -)

Examples:
  $ tiergraph patch show change.jsonl

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph patch invert`

```text
usage: tiergraph patch invert [-h] [-o FILE] PATCH

Write the inverse patch.

positional arguments:
  PATCH                 patch JSONL

options:
  -h, --help            show this help message and exit
  -o FILE, --output FILE
                        output file (default: -)

Examples:
  $ tiergraph patch invert change.jsonl

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph patch compose`

```text
usage: tiergraph patch compose [-h] [-o FILE] FIRST SECOND

Compose patches whose identified fingerprints meet.

positional arguments:
  FIRST                 first patch JSONL
  SECOND                second patch JSONL

options:
  -h, --help            show this help message and exit
  -o FILE, --output FILE
                        output file (default: -)

Examples:
  $ tiergraph patch compose first.jsonl second.jsonl -o both.jsonl

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph diff`

```text
usage: tiergraph diff [-h] [--view {functional,identified,exact}] [--check]
                      [-o FILE]
                      SOURCE TARGET

Build a deterministic patch from one graph toward another.

positional arguments:
  SOURCE                source graph
  TARGET                target graph

options:
  -h, --help            show this help message and exit
  --view {functional,identified,exact}
                        comparison view (default: functional)
  --check               replay and verify the selected view
  -o FILE, --output FILE
                        output file (default: -)

Examples:
  $ tiergraph diff before.json after.json -o change.jsonl

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph program`

```text
usage: tiergraph program [-h] {from-graph} ...

Build a construction-only machine program.

positional arguments:
  {from-graph}
    from-graph  encode exact graph construction

options:
  -h, --help    show this help message and exit

Examples:
  $ tiergraph program from-graph graph.json -o program.jsonl

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```

### `tiergraph program from-graph`

```text
usage: tiergraph program from-graph [-h] [-o FILE] GRAPH

Encode an exact from-empty graph construction program.

positional arguments:
  GRAPH                 graph document

options:
  -h, --help            show this help message and exit
  -o FILE, --output FILE
                        output file (default: -)

Examples:
  $ tiergraph program from-graph graph.json -o program.jsonl

Exit codes:
  0  success
  1  invalid input or refused operation
  2  command-line usage error
  3  I/O failure or undecodable input
```
