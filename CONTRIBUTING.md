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
check, the public-docstring check, and the reservation check. Coverage is
measured for the `tiergraph` and `tiergraph_dot` packages and for the `scripts`
gates, and must remain at 100% branch coverage.

The tracked-file hygiene check reads **every tracked file**, not a listed set of
directories. That is not a convenience: the source distribution ships the whole
tracked tree, so anything committed here is published, and a check that read
less than everything would leave shipped files unread. The one exemption is the
check's own source, which has to write down the patterns it forbids. The
distribution is built during the test suite and compared against what the check
reads, so the two cannot drift apart silently.

Because no tracked file is inert to it, CI runs this check on every pull
request, outside the path filter that governs the more expensive jobs.

## Reserve something only with a condition that discharges it

Sometimes a change declares a name, or documents a decision, that nothing
produces yet: a refusal code kept for a meaning no resolver emits, a helper
withheld until a ruling lands, a rule left unratified. Such a reservation has
to be registered in `scripts/check_reservations.py`, with the exact prose that
carries it and the condition that would end it.

The register exists because the two failure modes are not symmetric. A
reservation that is still undischarged is visible: a reader meets the docstring
and sees the promise standing. A reservation that quietly stopped being true is
not visible at all -- the thing it waited on now exists, the prose still says it
does not, and no other check disagrees. `make reservations` fails on the second
case by name, and it fails on a docstring that announces a reservation without
registering one.

Entries come in two kinds. Most carry a predicate that reads the tree and
reports evidence when the reservation has been overtaken; each predicate states
in its own docstring which spelling of an arrival it watches. A few conditions
no observable in this repository can decide -- machinery that has no reserved
name to watch for -- and those are registered as unenforceable, with the reason
written down. For them the check pins the prose and claims nothing further,
which is the honest position: an unenforceable entry that says so is worth more
than a predicate that can never fire.

The check reads docstrings under `src/tiergraph`, `src/tiergraph_dot`, and
`examples/`. It does not read hand-written Markdown, because its vocabulary is
ordinary English there; the check's own module docstring records that boundary
and the reason.

The repository also provides a pre-commit configuration. After installing
`pre-commit` separately, enable it in your checkout with:

```console
pre-commit install
pre-commit run --all-files
```

Those hooks apply basic file checks and Ruff, then run the tracked-file hygiene,
public-docstring, and reservation checks. They complement rather than replace
`make check`.

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
