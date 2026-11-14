"""Read a LinkedIn job page in the owner's logged-in JobPilot Chrome.

The guest search gives cards without a description. A card that matched is opened
once, the way the owner would, to read the description and how to apply. Only matched
jobs are opened, a few per poll, paced; nothing is opened when the Chrome is not
running or not logged in.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from dataclasses import dataclass
from datetime import date

from backend.browser.chrome import running_endpoint

logger = logging.getLogger(__name__)

MAX_PER_POLL = 5
MAX_PER_DAY = 80
GAP_S = (6.0, 18.0)  # between two job pages
# ponytail: read inside the poll, so a full batch (5 pages) holds the tick for about a
# minute. Move to a background task if other sources' sightings get late.
_WALL = re.compile(r"/(?:login|authwall|checkpoint|uas/login|signup)", re.IGNORECASE)
_ABOUT = re.compile(r"^(?:About the job|À propos de l[’']offre d[’']emploi)\s*$", re.MULTILINE)
_END = re.compile(
    r"^(?:Set alert for similar jobs|About the company|Job search faster with Premium"
    r"|Créer une alerte.*|À propos de l[’']entreprise|Trouvez un emploi plus rapidement.*)\s*$",
    re.MULTILINE,
)
_CLOSED = re.compile(
    r"^(?:Not currently accepting applications|No longer accepting applications"
    r"|N[’']accepte plus de candidatures.*|Ne reçoit plus de candidatures.*)\s*$",
    re.MULTILINE,
)
_EASY = re.compile(r"^(?:Easy Apply|Candidature simplifiée)\s*$", re.MULTILINE)
_APPLY = re.compile(r"^(?:Apply|Postuler)\s*$", re.MULTILINE)

_opened: dict[date, int] = {}  # ponytail: in memory, resets on restart; persist if the cap matters
_rng = random.Random()


@dataclass(frozen=True)
class Detail:
    description: str
    apply_method: str  # easy_apply | external | closed | unknown


def parse(main_text: str) -> Detail:
    """Description and apply method from the visible text of the job page."""
    head, _, rest = main_text, "", ""
    about = _ABOUT.search(main_text)
    if about:
        head, rest = main_text[: about.start()], main_text[about.end() :]
        end = _END.search(rest)
        rest = rest[: end.start()] if end else rest
    if _CLOSED.search(head):
        method = "closed"
    elif _EASY.search(head):
        method = "easy_apply"
    elif _APPLY.search(head):
        method = "external"
    else:
        method = "unknown"
    return Detail(rest.strip(), method)


async def read_jobs(urls: list[str], *, today: date | None = None) -> dict[str, Detail]:
    """Open each URL in a tab of the running JobPilot Chrome. {} when it is not usable."""
    endpoint = running_endpoint()
    today = today or date.today()
    budget = min(MAX_PER_POLL, MAX_PER_DAY - _opened.get(today, 0))
    if endpoint is None or budget <= 0 or not urls:
        return {}
    from playwright.async_api import async_playwright  # noqa: PLC0415 - heavy, rarely needed

    found: dict[str, Detail] = {}
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{endpoint['port']}")
        context = browser.contexts[0]
        cookies = await context.cookies("https://www.linkedin.com")
        if not any(c["name"] == "li_at" for c in cookies):
            logger.warning("JobPilot Chrome is not logged into LinkedIn: cards stay title-only")
            return {}
        for n, url in enumerate(urls[:budget]):
            if n:
                await asyncio.sleep(_rng.uniform(*GAP_S))
            page = await context.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                _opened[today] = _opened.get(today, 0) + 1
                if _WALL.search(page.url):
                    # Logged out or challenged: the owner has to act in the window.
                    logger.warning("LinkedIn asks for a login or a check (%s): stopping", page.url)
                    break
                await page.wait_for_function(
                    "document.querySelector('main')?.innerText.length > 400", timeout=15_000
                )
                await page.wait_for_timeout(_rng.uniform(1500, 4000))
                found[url] = parse(await page.inner_text("main"))
            except Exception as exc:  # noqa: BLE001 - a page that fails is skipped, not fatal
                logger.warning("LinkedIn job page %s unreadable: %s", url, exc)
            finally:
                await page.close()  # the tab, never the context: it is the owner's session
    return found
