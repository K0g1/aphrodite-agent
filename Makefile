.PHONY: install dev test lint format clean run api

install:
	pip install -e ".[dev]"

dev:
	pip install -e ".[dev, voice]"

test:
	pytest -v

lint:
	ruff check src/ tests/
	mypy src/aphrodite src/aphrodite_cli

format:
	ruff format src/ tests/
	ruff check --fix src/ tests/

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov

run:
	aphrodite chat

api:
	aphrodite api
