# Works from Git Bash on Windows and on Linux/macOS. Every tool runs from the local .venv.
.PHONY: help install lint format typecheck test build check changeset changeset-check clean

ifeq ($(OS),Windows_NT)
PYTHON ?= python
VENV_BIN := .venv/Scripts
else
PYTHON ?= python3
VENV_BIN := .venv/bin
endif

VENV_PY := $(VENV_BIN)/python
# Change-set type for `make changeset`: Patch, Minor or Major.
TYPE ?= Patch

help:
	@echo "ninjavault-cdn commands:"
	@echo "  make install          - Create .venv and install the package (editable) with dev tools"
	@echo "  make lint             - ruff lint + format check"
	@echo "  make format           - Apply ruff format and safe lint fixes"
	@echo "  make typecheck        - mypy --strict"
	@echo "  make test             - pytest with coverage (fails under 90%)"
	@echo "  make build            - Build sdist + wheel into dist/ and run twine check"
	@echo "  make changeset [TYPE=Patch|Minor|Major]"
	@echo "                        - Create changesets/<version>.md for the current version"
	@echo "  make changeset-check  - Fail if changesets/<version>.md is missing"
	@echo "  make check            - lint + typecheck + test + changeset-check + build"
	@echo "  make clean            - Remove build output and caches"
	@echo ""
	@echo "Release flow: bump src/ninjavault_cdn/_version.py -> make changeset -> fill it in"
	@echo "              -> make check -> PR -> merge to main (Publish workflow uploads to PyPI)"

$(VENV_PY):
	$(PYTHON) -m venv .venv

install: $(VENV_PY)
	$(VENV_PY) -m pip install --upgrade pip
	$(VENV_PY) -m pip install -e ".[dev]"

lint:
	$(VENV_PY) -m ruff check src tests scripts
	$(VENV_PY) -m ruff format --check src tests scripts

format:
	$(VENV_PY) -m ruff check --fix src tests scripts
	$(VENV_PY) -m ruff format src tests scripts

typecheck:
	$(VENV_PY) -m mypy

test:
	$(VENV_PY) -m pytest --cov --cov-report=term-missing

build:
	rm -rf dist build
	$(VENV_PY) -m build
	$(VENV_PY) -m twine check --strict dist/*

changeset:
	$(VENV_PY) scripts/new_changeset.py $(TYPE)

changeset-check:
	$(VENV_PY) scripts/check_changeset.py

check: lint typecheck test changeset-check build

clean:
	rm -rf dist build .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml htmlcov
	find . -path ./.venv -prune -o -type d -name __pycache__ -exec rm -rf {} +
	find . -path ./.venv -prune -o -type d -name "*.egg-info" -exec rm -rf {} +
