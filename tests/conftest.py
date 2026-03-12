import asyncio
import platform
import sys
from pathlib import Path
import pytest
import unittest.mock as mock

# Ensure project root is in sys.path so tests can import backend package
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def test_app():
    """Provide a TestClient for the FastAPI app."""
    import os

    if os.getcwd() not in sys.path:
        sys.path.insert(0, os.getcwd())

    from starlette.testclient import TestClient
    from backend.main import app

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def mock_gemini():
    """Mock google.generativeai.GenerativeModel to avoid real API calls."""
    patcher = mock.patch("google.generativeai.GenerativeModel")
    MockModel = patcher.start()

    instance = MockModel.return_value

    class DummyResponse:
        def __init__(self, text: str):
            self.text = text

    instance.generate_content.return_value = DummyResponse("mocked response")

    try:
        yield MockModel
    finally:
        patcher.stop()


@pytest.fixture
def test_settings(monkeypatch):
    """Return a Settings instance with deterministic test values."""
    import os

    if os.getcwd() not in sys.path:
        sys.path.insert(0, os.getcwd())

    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-real")
    monkeypatch.setenv("ADZUNA_APP_ID", "test-adzuna-id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "test-adzuna-key")
    monkeypatch.setenv("JOBPILOT_HOST", "127.0.0.1")
    monkeypatch.setenv("JOBPILOT_PORT", "8000")

    from backend.config import Settings

    return Settings()
