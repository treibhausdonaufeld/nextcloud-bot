# Tests

This directory contains unit tests for the nextcloud-bot project.

## Running Tests

Install development dependencies:

```bash
uv sync --dev
```

Run all tests:

```bash
pytest
```

Run tests with coverage:

```bash
pytest --cov=lib --cov-report=html --cov-report=term
```

Run specific test file:

```bash
pytest tests/test_protocol_decision_parsing.py
```

Run specific test:

```bash
pytest tests/test_protocol_decision_parsing.py::TestProtocolDecisionExtraction::test_extract_single_decision
```

## Test Structure

- `test_protocol_decision_parsing.py` - Tests for Protocol decision parsing from markdown content
  - Tests extraction of decisions from protocol markdown (`::: success` blocks)
  - Tests parsing of decision metadata (title, valid_until, objections)
  - Tests keyword recognition (multilingual support for German/English)
  - Tests deletion of related decisions when protocol is deleted

## Writing Tests

When adding new tests:

1. Follow the existing naming convention: `test_*.py` for files, `Test*` for classes, `test_*` for methods
2. Use pytest fixtures for common setup
3. Use `@pytest.mark.parametrize` for testing multiple variations
4. Mock external dependencies (database, API calls, etc.)
5. Keep tests focused on a single behavior

## Test Coverage

To view the coverage report after running tests with `--cov`:

```bash
open htmlcov/index.html  # macOS
xdg-open htmlcov/index.html  # Linux
```
