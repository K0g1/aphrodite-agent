# Contributing to Aphrodite Agent

Thanks for your interest! This project is currently in early development.

## Development Setup

```bash
git clone https://github.com/K0g1/aphrodite-agent.git
cd aphrodite-agent
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev, voice]"
```

## Code Style

We use `ruff` for linting and formatting:

```bash
ruff check src/ tests/
ruff format src/ tests/
```

Type checking with `mypy`:

```bash
mypy src/aphrodite src/aphrodite_cli
```

## Testing

```bash
pytest
pytest --cov=aphrodite --cov-report=html
```

## Commit Style

Follow conventional commits:

- `feat:` new feature
- `fix:` bug fix
- `docs:` documentation
- `test:` tests
- `refactor:` code change that neither fixes a bug nor adds a feature
