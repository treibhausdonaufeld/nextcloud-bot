#!/bin/bash
# Run tests for nextcloud-bot

cd "$(dirname "$0")"

echo "Running tests..."
uv run pytest tests/ -v --tb=short

echo ""
echo "Running tests with coverage..."
uv run pytest tests/ --cov=lib --cov-report=term --cov-report=html
