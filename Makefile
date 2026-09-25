# Common tasks. `make dev` once, then `make test`.
PY ?= .venv/bin/python

.PHONY: help dev test test-all lint bundle check-bundle diagrams sim

help:
	@echo "make dev           virtualenv in .venv with the package (editable) and dev tools"
	@echo "make test          lint + the test suite without the slow end-to-end runs"
	@echo "make test-all      the whole test suite"
	@echo "make lint          ruff"
	@echo "make bundle        dist/ihc-box-<version>-linux-aarch64.run (self-contained, for the Orange Pi box)"
	@echo "make check-bundle  smoke-test the bundle (under qemu-aarch64 on other hosts)"
	@echo "make diagrams      regenerate the diagrams in docs/images"
	@echo "make sim           serve two simulated iPhones on http://localhost:8000"

dev:
	python3 -m venv .venv
	.venv/bin/pip install -e ".[dev]"

test: lint
	$(PY) -m pytest -q -m "not slow"

test-all: lint
	$(PY) -m pytest -q

lint:
	$(PY) -m ruff check src tests scripts

bundle:
	scripts/build_bundle.sh

check-bundle:
	scripts/check_bundle.sh dist/*.run

diagrams:
	$(PY) scripts/make_diagrams.py
	$(PY) scripts/make_box_diagrams.py

sim:
	.venv/bin/ihc serve --sim 2
