.PHONY: help install bootstrap check-venv test lint lint-shell format typecheck security coverage clean verify doctor security-reminder update-chromadb update-deps build-dist build-image push-image check-version pypi-publish bench-s1 bench-s1-record bench-s4 bench-s2-smoke

# Read version from pyproject.toml — keeps local build targets in sync with the package version.
VERSION := $(shell grep -m1 '^version' pyproject.toml | cut -d'"' -f2)

# Interpreter for benchmark stand targets (override: make bench-s1 PYTHON=/usr/bin/python3.12).
PYTHON ?= python3

help:
	@echo "Mnemos development commands"
	@echo "  make bootstrap  - [DEV] Create .venv and install project (editable) + dev extras"
	@echo "  make check-venv - [DEV] Verify .venv editable install resolves to ./src"
	@echo "  make install    - Install with dev dependencies"
	@echo "  make test       - Run pytest suite"
	@echo "  make lint       - Run ruff linter"
	@echo "  make lint-shell - Run shellcheck on shell scripts"
	@echo "  make format     - Run ruff formatter"
	@echo "  make typecheck  - Run mypy"
	@echo "  make security   - Run bandit + pip-audit"
	@echo "  make security-reminder - Show pinned CVE reminder for manual dependency review"
	@echo "  make update-chromadb - Try upgrading chromadb and re-run audit"
	@echo "  make update-deps - Upgrade all deps and re-run audit"
	@echo "  make coverage   - Run pytest with coverage"
	@echo "  make bench-s1   - Run the S1 benchmark stand (gate mode, ADR-0020)"
	@echo "  make bench-s1-record - Re-record the S1 baseline + regenerate BASELINE.md"
	@echo "  make bench-s4   - Run the S4 availability stand (BF-2, nightly contour)"
	@echo "  make bench-s4-record - Write the S4 baseline (first record / re-baseline)"
	@echo "  make bench-s2-smoke - Run the S2 timing smoke (informational, never blocks)"
	@echo "  make verify     - Run all checks (lint + typecheck + security + test + bench-s1 + doctor)"
	@echo "  make doctor     - Run mnemos doctor health checks (config, storage, MCP, integration)"
	@echo "  make clean      - Remove build artifacts"
	@echo "  make build-dist - Build wheel + sdist into dist/ (requires: pip install build)"
	@echo "  make build-image - Build container image locally with podman"
	@echo "  make push-image - Tag and push local image to ghcr.io/korrnals/mnemos (requires: podman login ghcr.io)"
	@echo "  make pypi-publish - PyPI pipeline: name+version gates, build, twine check, smoke (upload needs scripts/pypi-publish.sh --publish)"

install:
	uv pip install -e ".[dev]"

test:
	pytest tests/ -v --tb=short

lint:
	ruff check src/ tests/

lint-shell:  ## Run shellcheck on all shell scripts
	shellcheck scripts/*.sh

format:
	ruff format src/ tests/

format-check:
	ruff format --check src/ tests/

typecheck:
	mypy --strict src/mnemos/

security:
	bandit -r src/ -f json -o bandit-report.json || true
	pip-audit --ignore-vuln CVE-2026-45829

security-reminder:
	@echo "⚠️  SECURITY REMINDER: chromadb 1.5.9 has ignored CVE-2026-45829 (no upstream fix yet)."
	@echo "⚠️  Re-check weekly: make update-chromadb"

update-chromadb:
	pip install --upgrade chromadb
	pip-audit

update-deps:
	pip install --upgrade -e ".[dev]"
	pip-audit

coverage:
	pytest --cov=src/mnemos --cov-report=term-missing --cov-fail-under=80 tests/ -q

check-version:
	@python -c "from mnemos import __version__; from importlib.metadata import version; v = version('mnemos'); assert __version__ == v, f'mismatch: __init__={__version__}, metadata={v}'; print(f'✓ version {v} consistent')"

# ── Benchmark stands (ADR-0020) ──────────────────────────────────────────────
# S1 stays in the local merge gate (deterministic corridors + invariants
# vs benchmarks/baselines/s1.json). Re-record only on an event-driven
# trigger (corpus ×2, embedder/model change, issuance-path change) — never
# to make a red gate green.

bench-s1:
	$(PYTHON) benchmarks/stands/s1_quality/run.py

bench-s1-record:
	$(PYTHON) benchmarks/stands/s1_quality/run.py --record

# BF-2 stands — NOT in the local merge gate (ADR-0020: S4 rides the
# nightly contour while within budget; S2 never blocks locally). The
# MNEMOS_BENCH_S1M_REQUIRED=1 flag for CI nightlies is documented in
# benchmarks/README.md (S1m skip semantics); full CI wiring is BF-4.
bench-s4:
	$(PYTHON) benchmarks/stands/s4_availability/run.py

bench-s4-record:
	$(PYTHON) benchmarks/stands/s4_availability/run.py --record

bench-s2-smoke:
	$(PYTHON) benchmarks/stands/s2_timing/run.py

verify: format-check lint typecheck test security security-reminder bench-s1 doctor check-version
	@echo "✅ All verification checks passed"

# doctor gate: fail on actual failures (exit 1), allow warnings (exit 2).
# CI environments typically lack agent harnesses, so the integration check
# warns — that is expected and must not break the build.
doctor:
	@mnemos doctor --json > /dev/null 2>&1; \
	code=$$?; \
	if [ $$code -eq 1 ]; then \
		echo "✗ mnemos doctor: one or more health checks FAILED"; \
		mnemos doctor; \
		exit 1; \
	elif [ $$code -eq 2 ]; then \
		echo "⚠ mnemos doctor: warnings only (non-blocking)"; \
	else \
		echo "✓ mnemos doctor: all checks passed"; \
	fi

bootstrap:
	@echo "🔧 Creating .venv and installing mnemos (editable) + dev extras..."
	python -m venv .venv
	.venv/bin/python -m pip install --upgrade pip
	.venv/bin/python -m pip install -e ".[dev]"
	@echo "✅ Bootstrap complete — activate with: source .venv/bin/activate"

check-venv:
	@if [ -x .venv/bin/python ]; then \
		.venv/bin/python -c "import mnemos, pathlib, sys; got=pathlib.Path(mnemos.__file__).resolve(); want=(pathlib.Path.cwd()/'src/mnemos/__init__.py').resolve(); sys.exit(0 if got == want else 1)" \
			&& echo "✅ .venv editable install resolves to ./src" \
			|| { echo '⚠️  .venv is stale: mnemos does not import from ./src (project moved or venv built elsewhere). Run: make bootstrap'; exit 1; }; \
	else \
		echo "ℹ️  No .venv found — run: make bootstrap"; \
	fi

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache dist/ *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete

# --- Distribution & container -----------------------------------------------

build-dist:
	# Requires: pip install build  (not in dev extras).
	python -m build

build-image:
	podman build -t localhost/mnemos:$(VERSION) -t localhost/mnemos:latest -f Containerfile .

push-image:
	# Run `make build-image` first to ensure the local image exists.
	# Requires: podman login ghcr.io  (credentials are NOT embedded here).
	podman tag localhost/mnemos:$(VERSION) ghcr.io/korrnals/mnemos:$(VERSION)
	podman tag localhost/mnemos:latest ghcr.io/korrnals/mnemos:latest
	podman push ghcr.io/korrnals/mnemos:$(VERSION)
	podman push ghcr.io/korrnals/mnemos:latest

pypi-publish:
	# Check mode: name+version gates, wheel/sdist build, twine check, metadata smoke.
	# NO upload — first publish + final name are owner decisions.
	# Flags (--full-smoke, --publish, ...): scripts/pypi-publish.sh --help
	@bash scripts/pypi-publish.sh


# ── local CI / release (GitHub Actions billing-locked — memory ef56d3b5) ──
# local-ci.sh replicates .github/workflows/ci.yml verify job locally.
# local-release.sh replicates .github/workflows/release.yml (build + push image + GitHub Release).
# See scripts/local-ci.sh and scripts/local-release.sh for details.

local-ci:
	@bash scripts/local-ci.sh

local-ci-build:
	@bash scripts/local-ci.sh --build

local-release:
	@bash scripts/local-release.sh

local-release-dry:
	@bash scripts/local-release.sh --dry-run

local-release-no-image:
	@bash scripts/local-release.sh --no-image
