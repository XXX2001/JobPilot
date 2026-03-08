from __future__ import annotations

import logging

import httpx

from backend.config import settings
from backend.matching.filters import JobFilters
from backend.models.schemas import RawJob

logger = logging.getLogger(__name__)


class AdzunaAPIError(Exception):
    pass


class AdzunaClient:
    """Structured job search via Adzuna REST API. 250 free calls/day."""

    BASE_URL = "https://api.adzuna.com/v1/api/jobs"

    def __init__(self) -> None:
        self.app_id = settings.ADZUNA_APP_ID
        self.app_key = settings.ADZUNA_APP_KEY

    async def search(
        self,
        keywords: list[str],
        filters: JobFilters,
        country: str = "gb",
        page: int = 1,
        results_per_page: int = 20,
    ) -> list[RawJob]:
        """Search Adzuna for jobs matching keywords + filters."""
        params: dict = {
            "app_id": self.app_id,
            "app_key": self.app_key,
            "what": " ".join(keywords),
            "where": filters.locations[0] if filters.locations else "",
            "salary_min": filters.salary_min,
            "results_per_page": results_per_page,
        }
        if "full-time" in (filters.job_types or []):
            params["full_time"] = 1
        params = {k: v for k, v in params.items() if v is not None and v != ""}
        url = f"{self.BASE_URL}/{country}/search/{page}"
        logger.debug("Adzuna request: url=%s params=%s", url, {k: v for k, v in params.items() if k != 'app_key'})
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, params=params)
            if response.status_code != 200:
                raise AdzunaAPIError(
                    f"Adzuna returned {response.status_code}: {response.text[:200]}"
                )
            data = response.json()
        jobs = [self._parse_job(j) for j in data.get("results", [])]
        for job in jobs:
            job.country = country
        return jobs

    def _parse_job(self, data: dict) -> RawJob:
        return RawJob(
            external_id=str(data.get("id", "")),
            title=data.get("title", ""),
            company=data.get("company", {}).get("display_name", ""),
            location=data.get("location", {}).get("display_name", ""),
            salary_text="",
            salary_min=data.get("salary_min"),
            salary_max=data.get("salary_max"),
            description=data.get("description", ""),
            url=data.get("redirect_url", ""),
            apply_url=data.get("redirect_url", ""),
            source_name="adzuna",
        )
