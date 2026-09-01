# CLI reference

The `tiergraph` command prints help when called without arguments. `--version` prints one JSON object and exits successfully.

`tiergraph.cli.build_parser()` is importable and usable, but carries no API-stability promise at version 0.2.0.

## Contracts

Every command that reads an input document accepts `-` in place of that document's filename and reads it from standard input, including the inputs named by `--result`, `--profile`, and `--selector`; `step --interactive` is the one exception, below. `schema` and `semirings` read no document, and `discharge`, `path`, `grammar`, `clock`, and `span` take only a subcommand, so `-` is a command-line usage error for those and exits 2. Document-producing commands write to stdout by default or to `-o/--output`; diagnostics go only to stderr. Exit status 0 means success, 1 means invalid input or a refused operation, 2 means command-line usage error, and 3 means an I/O failure or an input the CLI could not decode. The CLI's own reports refuse a graph the writer could not write in the same way as the writer, with exit status 1.

`validate` reports whether `loads()` accepts a document. This is deliberately separate from emission: a loads-accepted value such as an escaped lone surrogate is refused by the writer when `convert` tries to emit it, producing exit status 1 for a refused operation. `convert` canonicalizes to indented `json`, compact `json-compact`, or `bytes`; bytes uses the canonical JSON byte API and is not another syntax.

`run` consumes a CLI-owned JSONL stream. Its first line is exactly `{"machine_version":"1"}` and each later line has one opcode's public `to_data()` shape (a repeat body remains nested on that line). Header-only programs are valid, CRLF and a final line without a newline are accepted, and whitespace-only lines are rejected. The decoder caps each line at 1 MiB and the stream at `MAX_DOCUMENT_BYTES`; public `Repeat` and `Program` enforce repeat and total expansion bounds.

`step` reads that same JSONL program and drives the public `steps()` generator. Its default dump mode writes one deterministic compact JSON object per yielded `Step.to_data()` value. `--interactive` (or a TTY) provides `step`/`next`, `continue`, `run-to N`/`break N`, `print`/`inspect`, `list`, and `quit`. A refused opcode exits 1 after reporting its index and the last good graph, with no traceback. Interactive programs must come from a file because stdin carries REPL commands.

`inspect` reports tiers in graph order and relation declarations in canonical graph order (qualified-name order), not source declaration order.

`semirings` lists every algebra a fold can name, with its carrier boundary, its five declared law checks, and its declared properties. The listed names are exactly the values `fold --semiring` accepts.

`fold` evaluates a finite acyclic dependency relation with one of those algebras and emits the public `FoldResult.to_data()` report. `--tier` and `--transition` are repeatable; `--root` is repeatable and, when omitted, the roots are the domain items nothing depends on. The valuation carries the attribute's local name, because that name only ever appears in a refusal. Two lifts are nameable: `value` embeds the read attribute value in the carrier, and `one` embeds the semiring's multiplicative identity regardless of the value. A general lift, a witness order, and an index product are caller code, so they stay in the Python API; without a witness order the report's `provenance` is always null, and `--ranked` is the shell's route to witnesses. `--ranked` needs an algebra that declares `multiply_preserves_witness_order` and supplies the tie policy the declaration requires but ranked selection never consults. `--output-cap` caps ranked witnesses and so requires `--ranked`.

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
                 {validate,discharge,render,inspect,convert,schema,run,step,walk,path,grammar,clock,span,select,fold,semirings}
                 ...

positional arguments:
  {validate,discharge,render,inspect,convert,schema,run,step,walk,path,grammar,clock,span,select,fold,semirings}
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
    grammar             recognize with tiergraph grammars
    clock               query declarative clock timing
    span                render declarative span views
    select              evaluate a selector
    fold                fold a dependency relation
    semirings           list the semirings a fold can name

options:
  -h, --help            show this help message and exit
  --version             print the version
```

### `tiergraph validate`

```text
usage: tiergraph validate [-h] FILE

positional arguments:
  FILE        graph file, or - for stdin

options:
  -h, --help  show this help message and exit
```

### `tiergraph discharge`

```text
usage: tiergraph discharge [-h] {seals} ...

positional arguments:
  {seals}
    seals     discharge a source graph's seals against a result graph

options:
  -h, --help  show this help message and exit
```

### `tiergraph discharge seals`

```text
usage: tiergraph discharge seals [-h] --result FILE [--name NAME] [-o FILE]
                                 SOURCE

positional arguments:
  SOURCE                source graph file, or - for stdin

options:
  -h, --help            show this help message and exit
  --result FILE         result graph file
  --name NAME           name used in refusals
  -o FILE, --output FILE
                        output file (default: -)
```

### `tiergraph render`

```text
usage: tiergraph render [-h] [-o FILE] [--include-empty-tiers] FILE

positional arguments:
  FILE                  graph file, or - for stdin

options:
  -h, --help            show this help message and exit
  -o FILE, --output FILE
                        output file (default: -)
  --include-empty-tiers
                        include empty tiers
```

### `tiergraph inspect`

```text
usage: tiergraph inspect [-h] [-o FILE] FILE

positional arguments:
  FILE                  graph file, or - for stdin

options:
  -h, --help            show this help message and exit
  -o FILE, --output FILE
                        output file (default: -)
```

### `tiergraph convert`

```text
usage: tiergraph convert [-h] [-o FILE] --to {json,json-compact,bytes} FILE

positional arguments:
  FILE                  graph file, or - for stdin

options:
  -h, --help            show this help message and exit
  -o FILE, --output FILE
                        output file (default: -)
  --to {json,json-compact,bytes}
```

### `tiergraph schema`

```text
usage: tiergraph schema [-h] [--format-version VERSION] [--hash] [-o FILE]

options:
  -h, --help            show this help message and exit
  --format-version VERSION
  --hash                print the shape hash
  -o FILE, --output FILE
                        output file (default: -)
```

### `tiergraph run`

```text
usage: tiergraph run [-h] [-o FILE] --to {json,json-compact,bytes,dot}
                     [--include-empty-tiers]
                     FILE

positional arguments:
  FILE                  JSONL program file, or - for stdin

options:
  -h, --help            show this help message and exit
  -o FILE, --output FILE
                        output file (default: -)
  --to {json,json-compact,bytes,dot}
  --include-empty-tiers
                        include empty tiers in DOT output
```

### `tiergraph step`

```text
usage: tiergraph step [-h] [-o FILE] [--interactive] FILE

positional arguments:
  FILE                  JSONL program file, or - for stdin

options:
  -h, --help            show this help message and exit
  -o FILE, --output FILE
                        output file (default: -)
  --interactive         use the interactive debugger (also enabled when stdin
                        is a TTY)
```

### `tiergraph walk`

```text
usage: tiergraph walk [-h] --source PATH --relation-namespace NS
                      --relation-local LOCAL [--direction {forward,inverse}]
                      [--cap N] [-o FILE]
                      GRAPH

positional arguments:
  GRAPH                 graph file, or - for stdin

options:
  -h, --help            show this help message and exit
  --source PATH
  --relation-namespace NS
  --relation-local LOCAL
  --direction {forward,inverse}
  --cap N
  -o FILE, --output FILE
                        output file (default: -)
```

### `tiergraph path`

```text
usage: tiergraph path [-h] {resolve,spell} ...

positional arguments:
  {resolve,spell}
    resolve        resolve a tiergraph path
    spell          spell a tiergraph path

options:
  -h, --help       show this help message and exit
```

### `tiergraph path resolve`

```text
usage: tiergraph path resolve [-h] [-o FILE] GRAPH TGPATH

positional arguments:
  GRAPH                 graph file, or - for stdin
  TGPATH                tiergraph path to resolve

options:
  -h, --help            show this help message and exit
  -o FILE, --output FILE
                        output file (default: -)
```

### `tiergraph path spell`

```text
usage: tiergraph path spell [-h] --kind {item,boundary} [--tier-namespace NS]
                            [--tier-local LOCAL] [--index N] [--durable-id ID]
                            [--anchor-item-id ID] [--anchor-tier-namespace NS]
                            [--anchor-tier-local LOCAL]
                            [--side {before,after}] [-o FILE]
                            GRAPH

positional arguments:
  GRAPH                 graph file, or - for stdin

options:
  -h, --help            show this help message and exit
  --kind {item,boundary}
  --tier-namespace NS
  --tier-local LOCAL
  --index N
  --durable-id ID
  --anchor-item-id ID
  --anchor-tier-namespace NS
  --anchor-tier-local LOCAL
  --side {before,after}
  -o FILE, --output FILE
                        output file (default: -)
```

### `tiergraph grammar`

```text
usage: tiergraph grammar [-h] {recognize,count,best} ...

positional arguments:
  {recognize,count,best}
    recognize           recognize a token sequence
    count               count token-sequence derivations
    best                find best token-sequence derivations

options:
  -h, --help            show this help message and exit
```

### `tiergraph grammar recognize`

```text
usage: tiergraph grammar recognize [-h] --tokens-json JSON [-o FILE] GRAMMAR

positional arguments:
  GRAMMAR               grammar JSON file, or - for stdin

options:
  -h, --help            show this help message and exit
  --tokens-json JSON
  -o FILE, --output FILE
                        output file (default: -)
```

### `tiergraph grammar count`

```text
usage: tiergraph grammar count [-h] --tokens-json JSON [-o FILE] GRAMMAR

positional arguments:
  GRAMMAR               grammar JSON file, or - for stdin

options:
  -h, --help            show this help message and exit
  --tokens-json JSON
  -o FILE, --output FILE
                        output file (default: -)
```

### `tiergraph grammar best`

```text
usage: tiergraph grammar best [-h] --tokens-json JSON [--count N] [-o FILE]
                              GRAMMAR

positional arguments:
  GRAMMAR               grammar JSON file, or - for stdin

options:
  -h, --help            show this help message and exit
  --tokens-json JSON
  --count N
  -o FILE, --output FILE
                        output file (default: -)
```

### `tiergraph clock`

```text
usage: tiergraph clock [-h] {coordinates,boundary,extent,item} ...

positional arguments:
  {coordinates,boundary,extent,item}
    coordinates         list refined clock coordinates
    boundary            query one tier boundary
    extent              query a timed tier extent
    item                query one timed item

options:
  -h, --help            show this help message and exit
```

### `tiergraph clock coordinates`

```text
usage: tiergraph clock coordinates [-h] --profile FILE [-o FILE] GRAPH

positional arguments:
  GRAPH                 graph file, or - for stdin

options:
  -h, --help            show this help message and exit
  --profile FILE
  -o FILE, --output FILE
                        output file (default: -)
```

### `tiergraph clock boundary`

```text
usage: tiergraph clock boundary [-h] --profile FILE --boundary PATH [-o FILE]
                                GRAPH

positional arguments:
  GRAPH                 graph file, or - for stdin

options:
  -h, --help            show this help message and exit
  --profile FILE
  --boundary PATH
  -o FILE, --output FILE
                        output file (default: -)
```

### `tiergraph clock extent`

```text
usage: tiergraph clock extent [-h] --profile FILE --tier-namespace NS
                              --tier-local LOCAL [-o FILE]
                              GRAPH

positional arguments:
  GRAPH                 graph file, or - for stdin

options:
  -h, --help            show this help message and exit
  --profile FILE
  --tier-namespace NS
  --tier-local LOCAL
  -o FILE, --output FILE
                        output file (default: -)
```

### `tiergraph clock item`

```text
usage: tiergraph clock item [-h] --profile FILE --item PATH [-o FILE] GRAPH

positional arguments:
  GRAPH                 graph file, or - for stdin

options:
  -h, --help            show this help message and exit
  --profile FILE
  --item PATH
  -o FILE, --output FILE
                        output file (default: -)
```

### `tiergraph span`

```text
usage: tiergraph span [-h] {render} ...

positional arguments:
  {render}
    render    render a span view

options:
  -h, --help  show this help message and exit
```

### `tiergraph span render`

```text
usage: tiergraph span render [-h] --profile FILE --format
                             {text,json,jsonl,html,dot} [--alternatives]
                             [--jsonl-record {input,span}]
                             [--include-empty-tiers] [-o FILE]
                             GRAPH

positional arguments:
  GRAPH                 graph file, or - for stdin

options:
  -h, --help            show this help message and exit
  --profile FILE
  --format {text,json,jsonl,html,dot}
  --alternatives
  --jsonl-record {input,span}
  --include-empty-tiers
  -o FILE, --output FILE
                        output file (default: -)
```

### `tiergraph select`

```text
usage: tiergraph select [-h] --selector FILE [-o FILE] GRAPH

positional arguments:
  GRAPH                 graph file, or - for stdin

options:
  -h, --help            show this help message and exit
  --selector FILE
  -o FILE, --output FILE
                        output file (default: -)
```

### `tiergraph fold`

```text
usage: tiergraph fold [-h] [--name NAME] --attribute-namespace NS
                      --attribute-local LOCAL --tier NS LOCAL --semiring
                      {arctic,boolean,counting,decimal-arctic,decimal-tropical,path,tropical}
                      --lift {one,value} --transition NS LOCAL COMBINATION
                      [--root TGPATH] [--ranked] [--output-cap N] [-o FILE]
                      GRAPH

positional arguments:
  GRAPH                 graph file, or - for stdin

options:
  -h, --help            show this help message and exit
  --name NAME           name used in refusals
  --attribute-namespace NS
  --attribute-local LOCAL
  --tier NS LOCAL       one valuation domain tier; repeatable
  --semiring {arctic,boolean,counting,decimal-arctic,decimal-tropical,path,tropical}
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
  -o FILE, --output FILE
                        output file (default: -)
```

### `tiergraph semirings`

```text
usage: tiergraph semirings [-h] [-o FILE]

options:
  -h, --help            show this help message and exit
  -o FILE, --output FILE
                        output file (default: -)
```
