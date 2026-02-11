.PHONY: install install-dev test coverage lint format type-check clean all

# Default Python interpreter
PYTHON ?= python3

# Install package
install:
	$(PYTHON) -m pip install -e .

# Install with dev dependencies
install-dev:
	$(PYTHON) -m pip install -e ".[dev]"

# Run tests
test:
	$(PYTHON) -m pytest tests/ -v

# Run tests with coverage
coverage:
	$(PYTHON) -m pytest tests/ -v --cov=src/event_bus --cov-report=term-missing --cov-report=html

# Lint code with ruff
lint:
	$(PYTHON) -m ruff check src/ tests/

# Format code with ruff
format:
	$(PYTHON) -m ruff format src/ tests/
	$(PYTHON) -m ruff check --fix src/ tests/

# Type check with mypy
type-check:
	$(PYTHON) -m mypy src/event_bus

# Clean build artifacts
clean:
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	rm -rf src/*.egg-info/
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	rm -rf .ruff_cache/
	rm -rf htmlcov/
	rm -rf .coverage
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# Run all checks
all: lint type-check test
