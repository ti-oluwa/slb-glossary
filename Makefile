# Makefile for slb-glossary development and testing

.PHONY: help install install-dev install-test browsers test test-fast test-unit test-cli test-mcp test-live test-slow test-watch test-coverage test-coverage-xml test-coverage-html lint lint-fix format format-check security type-check quality build upload upload-test dev-setup clean ci debug-env example dev release-check

# Default target
help: ## Show this help message
	@echo "slb-glossary Development Makefile"
	@echo ""
	@echo "Available targets:"
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# Installation targets

install: ## Install the package and all extras
	uv sync --extra all --inexact

install-dev: ## Set up dependencies for development (dev tools + tests)
	uv sync --group dev --group test --extra all --inexact

install-test: ## Install just the test dependencies
	uv sync --group test --inexact

browsers: ## Install the chromium build patchright drives (needed for --run-live tests and actual use)
	uv run patchright install chromium

# Testing targets
#
# `slow` and `live` tests are opt-in via --run-slow/--run-live (see
# tests/conftest.py), so a plain `pytest` run already excludes them -
# no `-m` juggling needed for the everyday case.

TEST_ARGS := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))

test: ## Run the full test suite. Use `make test "tests/local -k foo"` (one quoted arg) to pass args through.
	@if [ -n "$(TEST_ARGS)" ]; then \
		echo "Running: uv run pytest $(TEST_ARGS)"; \
		uv run pytest $(TEST_ARGS); \
	else \
		echo "Running all tests (excluding slow/live)"; \
		uv run pytest -v --tb=short; \
	fi
%:
	@:

test-fast: ## Quick sanity run: stop on first failure, minimal output
	uv run pytest -x -q

test-unit: ## Run only tests marked 'unit'
	uv run pytest -m unit -v --tb=short

test-cli: ## Run only tests marked 'cli'
	uv run pytest -m cli -v --tb=short

test-mcp: ## Run only tests marked 'mcp'
	uv run pytest -m mcp -v --tb=short

test-live: ## Run tests marked 'live' - hits the real glossary site/a real browser. Use sparingly.
	uv run pytest --run-live -m live -v --tb=short

test-slow: ## Run tests marked 'slow' - e.g. loads the real embedding model
	uv run pytest --run-slow -m slow -v --tb=short

test-watch: ## Re-run tests on file changes (needs pytest-watch; part of the test group on non-Windows)
	uv run pytest-watch --onpass "echo 'Tests passed'" --onfail "echo 'Tests failed'" -- -v --tb=short

test-coverage: ## Run tests with a terminal coverage report
	uv run pytest --cov=slb_glossary --cov-branch --cov-report=term-missing
	@echo "Coverage report generated. Check the terminal output for details."

test-coverage-xml: ## Run tests with coverage, generate an XML report (for CI/codecov)
	uv run pytest --cov=slb_glossary --cov-branch --cov-report=xml:coverage.xml
	@echo "Coverage report generated at coverage.xml"

test-coverage-html: ## Run tests with coverage, generate an HTML report
	uv run pytest --cov=slb_glossary --cov-branch --cov-report=html:coverage_html_report
	@echo "HTML coverage report generated at coverage_html_report/index.html"

# Code quality targets

lint: ## Run linting
	uv run ruff check slb_glossary/ tests/

lint-fix: ## Run linting with auto-fix
	uv run ruff check slb_glossary/ tests/ --fix

format: ## Format code
	uv run ruff format slb_glossary/ tests/

format-check: ## Check code formatting without changing anything
	uv run ruff format slb_glossary/ tests/ --check

security: ## Run security analysis (bandit)
	uv run bandit -r slb_glossary/ -s B101

type-check: ## Run type checking in an environment WITHOUT the 'semantic' extra
	@# The 'semantic' extra pulls in numpy transitively (via model2vec), and
	@# numpy's own bundled stub uses Python 3.12+ syntax that crashes mypy
	@# outright under this project's `python_version = "3.10"` pin (see the
	@# comment on the numpy override in pyproject.toml). So this syncs its
	@# own minimal environment first rather than trusting whatever `install`/
	@# `install-dev` last left in .venv/ - if that included 'semantic', mypy
	@# would crash instead of reporting real errors.
	uv sync --group dev --extra mcp
	@if uv run python -c "import mypy" 2>/dev/null; then \
		echo "Running mypy type check..."; \
		uv run mypy || true; \
	else \
		echo "mypy not installed, skipping type check"; \
		echo "Install it with: uv add --dev mypy (or 'make install-dev')"; \
	fi

quality: lint format-check security type-check ## Run all quality checks

# Build and distribution

build: ## Build the package (sdist + wheel)
	uv build

upload: ## Upload to PyPI
	uv publish

upload-test: ## Upload to TestPyPI
	uv publish --index testpypi

# Development targets

dev-setup: install-dev browsers ## Set up a full development environment
	@echo "Development environment set up!"
	@echo "Run 'make test-fast' for a quick sanity check"
	@echo "Run 'make test' for the full test suite"

clean: ## Clean up build artifacts and cache
	rm -rf dist/
	rm -rf build/
	rm -rf *.egg-info/
	rm -rf htmlcov/
	rm -rf coverage_html_report/
	rm -f .coverage
	rm -f coverage.xml
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	rm -rf .ruff_cache/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete

# CI simulation

ci: quality test-coverage ## Run CI-like checks locally

# Debugging helpers

debug-env: ## Show environment information
	@echo "Python version: $$(python --version 2>/dev/null || echo 'Not available')"
	@echo "UV version: $$(uv --version 2>/dev/null || echo 'Not available')"
	@echo "Pytest version: $$(uv run python -c 'import pytest; print(pytest.__version__)' 2>/dev/null || echo 'Not available')"
	@echo "Chromium installed: $$(uv run python -c 'from patchright.sync_api import sync_playwright; p = sync_playwright().start(); print(p.chromium.executable_path); p.stop()' 2>/dev/null || echo 'No (run make browsers)')"

# Example usage

example: ## Quick smoke check that the package imports and the CLI runs
	@echo "Running a quick smoke check..."
	@uv run python -c "\
import slb_glossary as slb; \
print(f'slb_glossary {slb.__version__} imported successfully'); \
print(f'  Language options: {[l.value for l in slb.Language]}'); \
print(f'  SearchMode options: {[m.value for m in slb.SearchMode]}')"
	@uv run slb --help > /dev/null && echo "CLI entrypoint runs successfully"

# Quick development workflow

dev: install-dev lint test-fast ## Quick development workflow: install, lint, test

# Release workflow

release-check: quality test-fast build ## Pre-release checks
	@echo "Release checks passed!"
	@echo "  Run 'make upload-test' to upload to TestPyPI"
	@echo "  Run 'make upload' to upload to PyPI"
