# Contributing to tiergraph

Thank you for contributing to tiergraph. This project is alpha software, so a
small, well-tested change is especially valuable.

## Set up a development environment

tiergraph requires Python 3.12 or later. Development is pinned to Python 3.12,
the supported floor, so newer Python features do not enter the codebase
accidentally. From the repository root, create the isolated development
environment and install the project with its development dependencies:

```console
make venv
```

By default, this uses `python3.12` and creates `.venv`. You can override the
interpreter with `PYTHON=/path/to/python3.12` or the environment directory with
`VENV=/path/to/venv`.

## Check a change

Run the same full gate used by CI:

```console
make check
```

The gate runs Ruff linting and formatting checks, strict mypy checks, pytest,
the test suite in separate processes with hash seeds 0, 12345, and 999, JSON
Schema currency checks, documentation currency checks, the tracked-file hygiene
check, and the public-docstring check. Coverage is measured only for the shipped
`tiergraph` and `tiergraph_dot` packages and must remain at 100% branch coverage.

The repository also provides a pre-commit configuration. After installing
`pre-commit` separately, enable it in your checkout with:

```console
pre-commit install
pre-commit run --all-files
```

Those hooks apply basic file checks and Ruff, then run the tracked-file hygiene
and public-docstring checks. They complement rather than replace `make check`.

## Documentation

Reader documentation lives in `README.md` and `docs/`. Every page under `docs/`
must be registered in `docs/manifest.json`. Generated sections and references
must be refreshed with `make docs`; `make docs-check` (implemented as
`scripts/generate_docs.py --check`) verifies that committed output matches a
fresh deterministic render. See [Contributing documentation](docs/contributing-docs.md)
for the rules governing pages, generated output, executable examples, and export
lists.

## What is accepted

Changes should keep `make check` green, use US English, contain no AI or tool
attribution, and preserve compatibility through additive changes where possible.
Because the project is pre-1.0, an incompatible change may still be necessary;
make its effect explicit and keep it as narrow as practical. See the
[stability policy](README.md#stability).

## Releases

Releases are a maintainer operation. The package version has a single source of
truth in `src/tiergraph/__init__.py`. Before release, maintainers run the full
gate, build the distribution, verify the import reports the intended version,
and wait for CI to pass. A GitHub Release whose `vX.Y.Z` tag matches the package
version triggers the publish workflow; it builds the sdist and wheel and uploads
them to PyPI through trusted publishing. PyPI releases cannot be overwritten.

The complete operator checklist, including initial trusted-publisher setup,
artifact checks, tagging, publication, and verification, is in
[RELEASING.md](RELEASING.md).
