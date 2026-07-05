"""Pytest configuration and fixtures for all tests.

This file is automatically loaded by pytest before any tests are collected.
Tests run without a database: persistence methods (`store`/`remove`) are
patched in the tests themselves, and the Edgy registry never connects unless
a query is actually executed.
"""

import os

import pytest

# Point the (never connected) database at a throwaway location, in case a
# test accidentally triggers a real query.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")


@pytest.fixture(autouse=True)
def reset_caches():
    """Reset class-level caches between tests to ensure test isolation."""
    from app.models.group import Group
    from app.models.user import NCUserList

    Group._cached_groups = None
    NCUserList._cached_users = None
    yield
    Group._cached_groups = None
    NCUserList._cached_users = None
