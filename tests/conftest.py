"""Pytest configuration and fixtures for all tests.

This file is automatically loaded by pytest before any tests are collected.
It mocks external dependencies like ChromaDB and CouchDB to ensure tests
can run without external services.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

# Mock environment variables for external APIs BEFORE any imports
os.environ["GEMINI_API_KEY"] = ""
os.environ["GOOGLE_API_KEY"] = ""
os.environ["HUGGINGFACE_API_KEY"] = ""
os.environ["HF_TOKEN"] = ""
os.environ["CHROMADB_HOST"] = "localhost"
os.environ["CHROMADB_PORT"] = "8000"
os.environ["COUCHDB_URL"] = "http://localhost:5984"

# Mock chromadb BEFORE it's imported by any module
_mock_chroma_client = MagicMock()
_mock_chroma_collection = MagicMock()
_mock_chroma_client.get_or_create_collection.return_value = _mock_chroma_collection
_mock_chroma_client.get_collection.return_value = _mock_chroma_collection

_mock_chromadb_module = MagicMock()
_mock_chromadb_module.HttpClient.return_value = _mock_chroma_client
_mock_chromadb_module.config.Settings = MagicMock()

# Inject mock before chromadb is imported
sys.modules["chromadb"] = _mock_chromadb_module
sys.modules["chromadb.config"] = MagicMock(Settings=MagicMock())
sys.modules["chromadb.api"] = MagicMock()
sys.modules["chromadb.api.types"] = MagicMock()
sys.modules["chromadb.utils"] = MagicMock()
sys.modules["chromadb.utils.embedding_functions"] = MagicMock()

# Mock Google Generative AI
sys.modules["google.generativeai"] = MagicMock()
sys.modules["google.generativeai.client"] = MagicMock()
sys.modules["google.genai"] = MagicMock()


def pytest_configure(config):
    """Pytest hook called before test collection begins."""
    # Ensure lib.chromadb uses our mock
    if "lib.chromadb" not in sys.modules:
        # Pre-import with our mocks in place
        import lib.chromadb  # noqa: F401


@pytest.fixture(autouse=True)
def reset_mocks():
    """Reset all mocks between tests to ensure test isolation."""
    _mock_chroma_client.reset_mock()
    _mock_chroma_collection.reset_mock()
    yield
