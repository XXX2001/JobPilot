from __future__ import annotations
import logging

logger = logging.getLogger(__name__)


class ScrapingOrchestrator:
    """Coordinates the morning batch scraping pipeline."""

    async def run_morning_batch(self) -> None:
        """Run the full morning scraping, matching, and tailoring pipeline.

        Steps (to be implemented in Wave 2):
        1. Load user profile + filters from DB
        2. Run AdzunaClient.search() for each keyword set
        3. Deduplicate results via JobDeduplicator
        4. Score via JobMatcher.score()
        5. Store top matches in DB
        6. Emit WebSocket progress events
        """
        logger.info("Morning batch started (stub)")
