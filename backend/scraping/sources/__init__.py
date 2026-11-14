"""Deterministic source adapters. Importing the package fills ``ADAPTERS``."""

from backend.scraping.sources import (  # noqa: F401  (each registers itself)
    ashby,
    greenhouse,
    smartrecruiters,
)
from backend.scraping.sources.base import ADAPTERS, PollFn, PollResult, SourceError

__all__ = ["ADAPTERS", "PollFn", "PollResult", "SourceError"]
