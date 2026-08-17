# Contributing documentation

Reader documentation lives in `README.md` and `docs/`. Add each page to
`docs/manifest.json`. Put code-derived output inside a generator directive, and
add the corresponding predicate to `scripts/generate_docs.py`.

Run `make docs` after changing an export, parser, manifest, or executable
example. `make docs-check` compares committed bytes with a fresh render and
checks both package export lists. `make check` runs that gate with lint, typing,
tests, determinism, schema, and tracked-file checks.

Generated output contains no timestamp or repository metadata. Two runs from
the same source must produce the same bytes.
