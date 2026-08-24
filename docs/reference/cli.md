# CLI reference

The `tiergraph` command prints help when called without arguments. `--version` prints one JSON object and exits successfully.

`tiergraph.cli.build_parser()` is importable and usable, but carries no API-stability promise at version 0.1.0.

## Contracts

Every command accepts `-` as stdin. Document-producing commands write to stdout by default or to `-o/--output`; diagnostics go only to stderr. Exit status 0 means success, 1 means invalid input or a refused operation, 2 means command-line usage error, and 3 means an I/O or encoding failure.

`validate` reports whether `loads()` accepts a document. This is deliberately separate from emission: a loads-accepted value such as an escaped lone surrogate can still be refused cleanly by `convert` during strict UTF-8 encoding. `convert` canonicalizes to indented `json`, compact `json-compact`, or `bytes`; bytes uses the canonical JSON byte API and is not another syntax.

`run` consumes a CLI-owned JSONL stream. Its first line is exactly `{"machine_version":"1"}` and each later line has one opcode's public `to_data()` shape (a repeat body remains nested on that line). Header-only programs are valid, CRLF and a final line without a newline are accepted, and whitespace-only lines are rejected. The decoder caps each line at 1 MiB and the stream at `MAX_DOCUMENT_BYTES`; public `Repeat` and `Program` enforce repeat and total expansion bounds.

`step` reads that same JSONL program and drives the public `steps()` generator. Its default dump mode writes one deterministic compact JSON object per yielded `Step.to_data()` value. `--interactive` (or a TTY) provides `step`/`next`, `continue`, `run-to N`/`break N`, `print`/`inspect`, `list`, and `quit`. A refused opcode exits 1 after reporting its index and the last good graph, with no traceback. Interactive programs must come from a file because stdin carries REPL commands.

`inspect` reports tiers in graph order and relation declarations in canonical graph order (qualified-name order), not source declaration order.

## Deterministic stepping example

For a program whose first opcode declares prefix `s` for `urn:step`, dump its exact public step states:

```console
$ tiergraph step program.jsonl
{"graph":{"attribute_declarations":[],"attributes":[],"namespaces":[{"namespace":"urn:step","prefix":"s"}],"polyadic_relations":[],"position_values":[],"relation_declarations":[],"relations":[],"tiers":[]},"index":0,"opcode":{"declaration":{"namespace":"urn:step","prefix":"s"},"opcode":"declare_namespace"}}
```

Each output line is independently parseable JSON.

## Help

### `tiergraph`

```text
usage: tiergraph [-h] [--version]
                 {validate,render,inspect,convert,schema,run,step,walk,path,grammar,clock,span,select}
                 ...

positional arguments:
  {validate,render,inspect,convert,schema,run,step,walk,path,grammar,clock,span,select}
    validate            validate a graph document
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
    select              evaluate a selection query

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
usage: tiergraph schema [-h] [--format-version N] [--hash] [-o FILE]

options:
  -h, --help            show this help message and exit
  --format-version N
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
usage: tiergraph path spell [-h] --kind {item,position} [--tier-namespace NS]
                            [--tier-local LOCAL] [--index N] [--durable-id ID]
                            [--anchor-item-id ID] [--anchor-tier-namespace NS]
                            [--anchor-tier-local LOCAL]
                            [--side {before,after}] [-o FILE]
                            GRAPH

positional arguments:
  GRAPH                 graph file, or - for stdin

options:
  -h, --help            show this help message and exit
  --kind {item,position}
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
usage: tiergraph clock [-h] {positions,position,extent,item} ...

positional arguments:
  {positions,position,extent,item}
    positions           list refined clock positions
    position            query one tier position
    extent              query a timed tier extent
    item                query one timed item

options:
  -h, --help            show this help message and exit
```

### `tiergraph clock positions`

```text
usage: tiergraph clock positions [-h] --profile FILE [-o FILE] GRAPH

positional arguments:
  GRAPH                 graph file, or - for stdin

options:
  -h, --help            show this help message and exit
  --profile FILE
  -o FILE, --output FILE
                        output file (default: -)
```

### `tiergraph clock position`

```text
usage: tiergraph clock position [-h] --profile FILE --position PATH [-o FILE]
                                GRAPH

positional arguments:
  GRAPH                 graph file, or - for stdin

options:
  -h, --help            show this help message and exit
  --profile FILE
  --position PATH
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
                             [--include-empty-tiers] [-o OUT]
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
  -o OUT, --output OUT  output file (default: -)
```

### `tiergraph select`

```text
usage: tiergraph select [-h] --query FILE [-o OUT] GRAPH

positional arguments:
  GRAPH                 graph file, or - for stdin

options:
  -h, --help            show this help message and exit
  --query FILE
  -o OUT, --output OUT  output file (default: -)
```
