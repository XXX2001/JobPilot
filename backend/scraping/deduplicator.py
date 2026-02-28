from __future__ import annotations
import hashlib
import re

from backend.models.schemas import RawJob


class JobDeduplicator:
    """Deduplicates jobs by normalized (company, title, location) MD5 hash."""

    def _make_key(self, job: RawJob) -> str:
        """Normalize and hash: company + title + location."""
        norm = lambda s: re.sub(r"\s+", " ", s.lower().strip())
        key = f"{norm(job.company)}|{norm(job.title)}|{norm(job.location)}"
        return hashlib.md5(key.encode()).hexdigest()

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
