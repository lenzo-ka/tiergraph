# CLI reference

The `tiergraph` command prints help when called without arguments. `--version` prints one JSON object and exits successfully.

`tiergraph.cli.build_parser()` is importable and usable, but carries no API-stability promise at version 0.0.0.

## Contracts

Every command accepts `-` as stdin. Document-producing commands write to stdout by default or to `-o/--output`; diagnostics go only to stderr. Exit status 0 means success, 1 means invalid input or a refused operation, 2 means command-line usage error, and 3 means an I/O or encoding failure.

`validate` reports whether `loads()` accepts a document. This is deliberately separate from emission: a loads-accepted value such as an escaped lone surrogate can still be refused cleanly by `convert` during strict UTF-8 encoding. `convert` canonicalizes to indented `json`, compact `json-compact`, or `bytes`; bytes uses the canonical JSON byte API and is not another syntax.

`run` consumes a CLI-owned JSONL stream. Its first line is exactly `{"machine_version":"1"}` and each later line has one opcode's public `to_data()` shape (a repeat body remains nested on that line). Header-only programs are valid, CRLF and a final line without a newline are accepted, and whitespace-only lines are rejected. The decoder caps each line at 1 MiB and the stream at `MAX_DOCUMENT_BYTES`; public `Repeat` and `Program` enforce repeat and total expansion bounds.

`inspect` reports tiers in graph order and relation declarations in canonical graph order (qualified-name order), not source declaration order.

## Help

### `tiergraph`

```text
usage: tiergraph [-h] [--version] {validate,render,inspect,convert,run} ...

positional arguments:
  {validate,render,inspect,convert,run}
    validate            validate a graph document
    render              render a graph as DOT
    inspect             inspect a graph document
    convert             canonicalize a graph document
    run                 execute a JSONL machine program

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
