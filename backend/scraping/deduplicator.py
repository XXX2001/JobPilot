from __future__ import annotations

import hashlib
import re

from backend.models.schemas import RawJob


def dedup_key(company: str | None, title: str | None, location: str | None) -> str:
    """The one cross-source identity hash: md5 of normalized ``company|title|city``.

    Lowercased, whitespace runs collapsed, stripped — so every caller (batch
    runner, manual search, monitor) agrees on the same ``Job.dedup_hash``. Only
    the first comma-separated token of the location counts: sources spell the
    rest differently (``Paris, France`` / ``Paris, Île-de-France, France`` /
    ``Paris, A8, FR``).
    """
    # ponytail: city = text before the first comma. Same title in Paris, France
    # and Paris, Texas at one company merges; "Paris (75)" does not match "Paris".
    # Normalise through a gazetteer if either shows up.
    def _norm(s: str | None) -> str:
        return re.sub(r"\s+", " ", (s or "").lower()).strip()
    city = (location or "").split(",")[0]
    return hashlib.md5(f"{_norm(company)}|{_norm(title)}|{_norm(city)}".encode()).hexdigest()


class JobDeduplicator:
    """Deduplicates jobs by normalized (company, title, location) MD5 hash."""

    def _make_key(self, job: RawJob) -> str:
        """Normalize and hash: company + title + location."""
        return dedup_key(job.company, job.title, job.location)

    def deduplicate(self, jobs: list[RawJob]) -> list[RawJob]:
        """Keep job with longer description when duplicates found."""
        seen: dict[str, RawJob] = {}
        for job in jobs:
            key = self._make_key(job)
            if key not in seen:
                seen[key] = job
            else:
                if len(job.description or "") > len(seen[key].description or ""):
                    seen[key] = job
        return list(seen.values())
