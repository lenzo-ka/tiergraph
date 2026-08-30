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
Schema currency checks, the format-growth check, documentation currency checks,
the tracked-file hygiene check, the public-docstring check, and the reservation
check. Coverage is measured for the `tiergraph` and `tiergraph_dot` packages and
for the `scripts` gates, and must remain at 100% branch coverage.

The tracked-file hygiene check reads **every tracked file**, not a listed set of
directories. That is not a convenience: the source distribution ships the whole
**unignored** tree, so anything committed here is published, and a check that
read less than everything would leave shipped files unread.

The distinction between *tracked* and *unignored* is the gap, not pedantry. The
check selects its files from the git index; the build selects from the working
tree minus what version control ignores. A file that is neither tracked nor
ignored therefore ships **and is never read** — so a new file must be staged
before the check can see it, and a scratch file that nobody ignores rides out in
the distribution unexamined. Keep `.gitignore` ahead of whatever the tooling and
the platform deposit. The one exemption is the
check's own source, which has to write down the patterns it forbids. The
distribution is built during the test suite and compared against what the check
reads, so the two cannot drift apart silently.

Because no tracked file is inert to it, CI runs this check on every pull
request, outside the path filter that governs the more expensive jobs.

## Grow the wire format, or say that you did not

`make format-growth` (implemented as `scripts/check_format_growth.py`) compares
the committed JSON Schema against the schema recovered from the newest release
tag in the current release line, and refuses a change that shrinks the set of
documents the format accepts: a removed property, one that became required, a
narrowed enum, a tightened bound, a withdrawn union arm. The wire is closed, so
a reader refuses a field it does not know rather than ignoring it; growth is
what keeps an older document valid and an older reader honest about a newer one.

It does not forbid breaking the format. A break is legal and costs a step in the
version position that carries breaking changes — the minor position before 1.0,
the major position after — and the refusal names the step that would make the
change legal rather than only saying no. Between that version step and the tag
that releases it, the check prints each break instead of refusing it, so a break
is never silent even where it is permitted, and the first tag in the new line
becomes the baseline the rest of that line is held to.

Two things it does not decide, and says so rather than passing over: a changed
`pattern`, because regular-language containment is not computed here, and a
keyword it has no rule for. Both are reported as unestablished and refuse for
the same reason a shrinkage does. Teach the check the keyword rather than
working around it.

The baseline comes from a tag, not from a second committed copy of the released
schema, because a committed copy can be edited in the same commit as the change
it exists to catch. The cost is that a checkout without tags cannot run this
check at all — and it then refuses, rather than reporting a comparison it never
made. Fetch tags before running `make check` in a shallow clone.

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
