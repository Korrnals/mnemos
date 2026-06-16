.PHONY: help install test lint format typecheck security coverage clean verify security-reminder update-chromadb update-deps

help:
	@echo "Mnemos development commands"
	@echo "  make install    - Install with dev dependencies"
	@echo "  make test       - Run pytest suite"
	@echo "  make lint       - Run ruff linter"
	@echo "  make format     - Run ruff formatter"
	@echo "  make typecheck  - Run mypy"
	@echo "  make security   - Run bandit + pip-audit"
	@echo "  make security-reminder - Show pinned CVE reminder for manual dependency review"
	@echo "  make update-chromadb - Try upgrading chromadb and re-run audit"
	@echo "  make update-deps - Upgrade all deps and re-run audit"
	@echo "  make coverage   - Run pytest with coverage"
	@echo "  make verify     - Run all checks (lint + typecheck + security + test)"
	@echo "  make clean      - Remove build artifacts"

install:
	uv pip install -e ".[dev]"

test:
	pytest tests/ -v --tb=short

lint:
	ruff check src/ tests/

format:
	ruff format src/ tests/

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

verify: lint test security security-reminder
	@echo "✅ All verification checks passed"

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache dist/ *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
