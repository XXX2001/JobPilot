"""Deterministic source adapters. Importing the package fills ``ADAPTERS``."""

from backend.scraping.sources import greenhouse  # noqa: F401  (registers itself)
from backend.scraping.sources.base import ADAPTERS, PollFn, PollResult, SourceError

__all__ = ["ADAPTERS", "PollFn", "PollResult", "SourceError"]
