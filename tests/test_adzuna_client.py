from __future__ import annotations
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from backend.scraping.adzuna_client import AdzunaClient, AdzunaAPIError
from backend.scraping.deduplicator import JobDeduplicator
from backend.models.schemas import RawJob
from backend.matching.filters import JobFilters


def _make_mock_client(status_code: int, json_data: dict) -> MagicMock:
    """Build a mock httpx async context manager returning the given response."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.json.return_value = json_data
    mock_response.text = ""

    mock_async_client = AsyncMock()
    mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
    mock_async_client.__aexit__ = AsyncMock(return_value=False)
    mock_async_client.get = AsyncMock(return_value=mock_response)
    return mock_async_client


SAMPLE_RESULT = {
    "results": [
        {
            "id": "123",
            "title": "ML Engineer",
            "company": {"display_name": "ACME"},
            "location": {"display_name": "Paris"},
            "description": "Great job",
            "redirect_url": "http://example.com",
        }
    ]
}


async def test_search_returns_raw_jobs():
    """AdzunaClient.search parses Adzuna JSON into RawJob list."""
    mock_client = _make_mock_client(200, SAMPLE_RESULT)
    with patch("backend.scraping.adzuna_client.httpx.AsyncClient", return_value=mock_client):
        client = AdzunaClient()
        results = await client.search(["ML"], JobFilters())

    assert len(results) == 1
    assert isinstance(results[0], RawJob)
    assert results[0].title == "ML Engineer"
    assert results[0].company == "ACME"


async def test_api_error_raises_exception():
    """AdzunaClient.search raises AdzunaAPIError on non-200 response."""
    mock_client = _make_mock_client(401, {})
    with patch("backend.scraping.adzuna_client.httpx.AsyncClient", return_value=mock_client):
        client = AdzunaClient()
        with pytest.raises(AdzunaAPIError):
            await client.search(["ML"], JobFilters())


async def test_empty_results_returns_empty_list():
    """AdzunaClient.search returns empty list when API has no results."""
    mock_client = _make_mock_client(200, {"results": []})
    with patch("backend.scraping.adzuna_client.httpx.AsyncClient", return_value=mock_client):
        client = AdzunaClient()
        results = await client.search(["ML"], JobFilters())

    assert results == []
