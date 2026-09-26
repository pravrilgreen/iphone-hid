# Common tasks. `make dev` once, then `make test`.
PY ?= .venv/bin/python
GO ?= go
export GOTOOLCHAIN = local

.PHONY: help dev test test-go test-sdk lint box check-box sim console

help:
	@echo "make dev        Python SDK in .venv (editable) with the test tools"
	@echo "make test       lint, the box's Go tests, the SDK tests against a simulated box"
	@echo "make lint       gofmt, go vet, ruff"
	@echo "make box        dist/ihc-box-<version>-linux-arm64.run (ARCH=amd64 for x86-64)"
	@echo "make check-box  smoke-test the bundle (under qemu-aarch64 on other machines)"
	@echo "make sim        run the box with a simulated iPhone on http://localhost:8000"
	@echo "make console    the same, serving the console from box/web (edit and reload)"

dev:
	python3 -m venv .venv
	.venv/bin/pip install -e ".[dev]"

test: lint test-go test-sdk

test-go:
	cd box && $(GO) test -race ./...

test-sdk:
	$(PY) -m pytest -q

lint:
	@test -z "$$(gofmt -l box)" || { gofmt -l box; echo "gofmt: format these files"; exit 1; }
	cd box && $(GO) vet ./...
	$(PY) -m ruff check src tests scripts

box:
	scripts/build_box.sh

check-box:
	scripts/check_box.sh dist/ihc-box-$$(cat VERSION)-linux-$${ARCH:-arm64}.run

sim:
	cd box && $(GO) run ./cmd/ihcd serve --sim --no-mdns

console:
	cd box && $(GO) run ./cmd/ihcd serve --sim --no-mdns --web web
