VENV ?= .venv
PYTHON := $(VENV)/bin/python

.PHONY: venv lint format-check types test determinism-seed determinism tracked-clean documented check

# Development happens in an isolated environment: a shared interpreter drags in
# packages this project does not depend on, and they surface as type errors in
# other people's stubs.
venv: $(VENV)/bin/python

$(VENV)/bin/python:
	@python -m venv $(VENV)
	@$(VENV)/bin/pip install --quiet --upgrade pip
	@$(VENV)/bin/pip install --quiet -e ".[dev]"

lint:
	@$(PYTHON) -m ruff check .

format-check:
	@$(PYTHON) -m ruff format --check .

types:
	@$(PYTHON) -m mypy

test:
	@$(PYTHON) -m pytest --cov=tiergraph --cov-report=term-missing

# Separate processes: interpreter hash state is fixed at startup and cannot be
# changed honestly inside one run.
determinism-seed:
	@test -n "$(HASH_SEED)" || (echo "HASH_SEED is required" >&2; exit 2)
	@PYTHONHASHSEED=$(HASH_SEED) $(PYTHON) -m pytest

determinism:
	@for seed in 0 12345 999; do \
		$(MAKE) --no-print-directory determinism-seed HASH_SEED=$$seed || exit $$?; \
	done

tracked-clean:
	@$(PYTHON) scripts/check_tracked_clean.py

documented:
	@$(PYTHON) scripts/check_documented.py

check: venv lint format-check types test determinism tracked-clean documented
