#!/usr/bin/env python3
"""Import a company watchlist CSV as monitor sources.

Each CSV row (``company,sample_url,fr_jobs,relevant_jobs``) names one ATS board;
the board's slug is read from ``sample_url``. Every board is polled once,
logged out, through its adapter. It is kept when at least one of its jobs is in
the owner's area (``SearchSettings.locations`` / ``countries``) and, with
``--title-regex``, that same job's title matches. A kept board becomes a
``JobSource(name="<ats>:<slug>", type="monitor")``; names that already exist
are skipped without a request, so the script can be re-run.

Usage
-----

    env -u PYTHONPATH uv run python scripts/import_watchlist.py --ats greenhouse \\
        --csv docs/superpowers/proofs/ats/watchlist/fr_greenhouse.csv \\
        [--title-regex 'robot|embedded|software'] [--limit 20]

``--limit N`` reads only the first N rows of the CSV. One request per second.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import re
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.models.job import JobSource  # noqa: E402
from backend.models.user import SearchSettings  # noqa: E402
from backend.scheduler.batch_runner import _extract_json_list  # noqa: E402
from backend.scraping.orchestrator import LOCATION_TO_COUNTRY  # noqa: E402
from backend.scraping.sources import ADAPTERS, SourceError  # noqa: E402

# Hosted board domains whose first path segment is the board slug.
BOARD_DOMAINS = ("greenhouse.io", "ashbyhq.com", "smartrecruiters.com")
REQUEST_GAP_S = 1


def slug_from_url(url: str) -> str | None:
    """The board slug of a hosted job URL; None for a board on the company's own site."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if not any(host == d or host.endswith("." + d) for d in BOARD_DOMAINS):
        return None
    return next((seg for seg in parts.path.split("/") if seg), None)


def location_pattern(locations: list[str], countries: list[str]) -> re.Pattern[str] | None:
    """Whole-word match on the owner's locations and on the known names of their countries."""
    # ponytail: country names come from the orchestrator's small table (a few
    # cities per country). Swap in the Phase 2 gazetteer when it lands.
    codes = {str(c).strip().lower() for c in countries}
    terms = {str(loc).strip().lower() for loc in locations}
    terms |= {name for name, code in LOCATION_TO_COUNTRY.items() if code in codes}
    terms.discard("")
    if not terms:
        return None
    return re.compile(r"\b(?:" + "|".join(map(re.escape, sorted(terms))) + r")\b", re.IGNORECASE)


async def import_watchlist(
    db: AsyncSession,
    client: httpx.AsyncClient,
    ats: str,
    csv_path: Path,
    *,
    title_regex: str | None = None,
    limit: int | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> tuple[int, int]:
    """Insert one monitor source per board worth watching. Returns (kept, skipped)."""
    poll = ADAPTERS.get(ats)
    if poll is None:
        raise SystemExit(f"unknown ATS {ats!r}; known: {', '.join(sorted(ADAPTERS))}")
    row = (await db.execute(select(SearchSettings).limit(1))).scalar_one_or_none()
    area = location_pattern(
        _extract_json_list(row.locations if row else None, "items"),
        _extract_json_list(row.countries if row else None, "items"),
    )
    if area is None:
        raise SystemExit("no locations or countries in the search settings: nothing to match on")
    title = re.compile(title_regex, re.IGNORECASE) if title_regex else None

    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))[:limit]
    existing = set((await db.execute(select(JobSource.name))).scalars())

    kept = skipped = requests = 0
    for entry in rows:
        slug = slug_from_url(entry.get("sample_url") or "")
        name = f"{ats}:{slug}"
        if slug is None or name in existing:
            skipped += 1
            continue
        if requests:
            await sleep(REQUEST_GAP_S)
        requests += 1
        try:
            result = await poll(client, {"slug": slug}, None, None)
        except SourceError as exc:
            print(f"skip {name}: {exc}")
            skipped += 1
            continue
        if not any(
            area.search(job.location) and (title is None or title.search(job.title))
            for job in result.jobs
        ):
            skipped += 1
            continue
        db.add(JobSource(name=name, type="monitor", config={"adapter": ats, "slug": slug}))
        await db.commit()  # per board: an interrupted run keeps what it found
        existing.add(name)
        kept += 1
        print(f"keep {name}")
    return kept, skipped


async def _main(args: argparse.Namespace) -> None:
    from backend.database import AsyncSessionLocal, init_db

    await init_db()  # no-op on a current DB; brings an older one to the monitor schema
    async with AsyncSessionLocal() as db, httpx.AsyncClient(timeout=20) as client:
        kept, skipped = await import_watchlist(
            db, client, args.ats, args.csv, title_regex=args.title_regex, limit=args.limit
        )
    print(f"kept {kept}, skipped {skipped}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--ats", required=True, help="adapter name, e.g. greenhouse")
    parser.add_argument("--csv", required=True, type=Path, help="watchlist CSV")
    parser.add_argument("--title-regex", help="keep a board only if a local job title matches")
    parser.add_argument("--limit", type=int, help="read only the first N rows")
    asyncio.run(_main(parser.parse_args()))
