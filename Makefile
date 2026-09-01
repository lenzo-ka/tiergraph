VENV ?= .venv
# Pin development to the supported floor so newer-only features cannot slip in.
PYTHON ?= python3.12
VENV_PYTHON := $(VENV)/bin/python

# The gate reads the checkout this file lives in, not whatever the ambient
# environment resolves. A git worktree has no virtualenv of its own, so it is
# run with VENV pointing at another checkout's -- and that virtualenv carries an
# editable install pinned to the checkout it was built from. Every step that
# does `import tiergraph` then exercises that other checkout's library while
# reporting on this one's diff, so the gate passes a change under src/ that it
# never read. Deriving the path from this makefile's own location is what makes
# the answer independent of how the gate was invoked; asking each caller to
# remember to export PYTHONPATH is the same defect one level up. An inherited
# PYTHONPATH is kept, behind this checkout, so a caller can still add to the
# path without being able to displace the code under test.
MAKEFILE_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
export PYTHONPATH := $(MAKEFILE_DIR)/src$(if $(PYTHONPATH),:$(PYTHONPATH))

# Every target below is a command, not a file it builds. The declaration is
# load-bearing rather than decorative: `docs` and `schema` name directories that
# exist in the checkout, and without it make reads those targets as up to date
# and runs nothing. A target left off this line is silent until something in the
# tree happens to share its name, which is why `format-semantics` and
# `corpus-capture` sat missing here without ever being noticed.
.PHONY: venv lint format-check types test determinism-seed determinism schema schema-check format-growth format-semantics corpus-capture docs docs-check tracked-clean documented reservations changelog-claims gate check

# Development happens in an isolated environment: a shared interpreter drags in
# packages this project does not depend on, and they surface as type errors in
# other people's stubs.
venv: $(VENV)/bin/python

$(VENV)/bin/python:
	@$(PYTHON) -m venv $(VENV)
	@$(VENV)/bin/pip install --quiet --upgrade pip
	@$(VENV)/bin/pip install --quiet -e ".[dev]"

lint:
	@$(VENV_PYTHON) -m ruff check .

format-check:
	@$(VENV_PYTHON) -m ruff format --check .

types:
	@$(VENV_PYTHON) -m mypy

test:
	@$(VENV_PYTHON) -m pytest --cov=tiergraph --cov=tiergraph_dot --cov=scripts --cov-report=term-missing

# Separate processes: interpreter hash state is fixed at startup and cannot be
# changed honestly inside one run.
determinism-seed:
	@test -n "$(HASH_SEED)" || (echo "HASH_SEED is required" >&2; exit 2)
	@PYTHONHASHSEED=$(HASH_SEED) $(VENV_PYTHON) -m pytest

determinism:
	@for seed in 0 12345 999; do \
		$(MAKE) --no-print-directory determinism-seed HASH_SEED=$$seed || exit $$?; \
	done

tracked-clean:
	@$(VENV_PYTHON) scripts/check_tracked_clean.py

documented:
	@$(VENV_PYTHON) scripts/check_documented.py

reservations:
	@$(VENV_PYTHON) scripts/check_reservations.py

changelog-claims:
	@$(VENV_PYTHON) scripts/check_changelog_claims.py

schema:
	@$(VENV_PYTHON) scripts/generate_schema.py

schema-check:
	@$(VENV_PYTHON) scripts/generate_schema.py --check

# Reads the baseline out of the release tags, so a checkout without them refuses
# rather than passing on a comparison it never made.
format-growth:
	@$(VENV_PYTHON) scripts/check_format_growth.py

# The other half of growth. The schema gate above covers structural shape; this
# runs the current decoder over documents ACCEPTED when the corpus was captured,
# so a tightened decoder that changes no schema byte still has to answer for
# itself. Every entry in the corpus today was captured from a development tree,
# so the span covered is since that capture and not since any release.
format-semantics:
	@$(VENV_PYTHON) scripts/check_format_semantics.py

# Capture is deliberately NOT part of the gate. A corpus regenerated from current
# code is accepted by current code by construction, and the check over it would
# pass without ever being able to fail. Run this at a release; the gate runs the
# frozen result every time.
corpus-capture:
	@TIERGRAPH_CORPUS_OUT=corpus/accepted-documents.jsonl \
	 TIERGRAPH_CORPUS_VERSION=$$($(VENV_PYTHON) -c 'import tiergraph; print(tiergraph.__version__)') \
	 $(VENV_PYTHON) -m pytest -p scripts.capture_corpus -q

docs:
	@$(VENV_PYTHON) scripts/generate_docs.py

docs-check:
	@$(VENV_PYTHON) scripts/generate_docs.py --check

# The gate, named apart from the environment it needs. Every step here runs
# against an already-built virtualenv, so a checkout that cannot reach an index
# can still run the whole gate rather than a hand-copied subset of it -- and the
# list of steps exists once, where it cannot be transcribed wrongly.
gate: lint format-check types test determinism schema-check format-growth format-semantics docs-check tracked-clean documented reservations changelog-claims

check: venv gate
