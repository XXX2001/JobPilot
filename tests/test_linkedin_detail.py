"""LinkedIn job pages read in the owner's Chrome: parsing, and the poller's use of it."""

from __future__ import annotations

from backend.monitor import linkedin_detail
from backend.monitor.linkedin_detail import Detail, parse

PAGE = """ACME

Robotics Engineer

Lyon, France · 1 hour ago · 9 applicants

{button}

Save
About the job

We build mobile manipulators.

Requirements
- Python and C++
- ROS 2

Set alert for similar jobs
Robotics Engineer, Lyon
About the company
ACME makes robots.
"""


def test_parse_reads_description_and_apply_method():
    easy = parse(PAGE.format(button="Easy Apply"))
    assert easy.apply_method == "easy_apply"
    assert easy.description.startswith("We build mobile manipulators.")
    assert easy.description.endswith("- ROS 2")
    assert parse(PAGE.format(button="Apply")).apply_method == "external"
    closed = PAGE.format(button="Not currently accepting applications")
    assert parse(closed).apply_method == "closed"
    assert parse(PAGE.format(button="Candidature simplifiée")).apply_method == "easy_apply"
    # "Apply" inside the description is not the button
    assert parse("Title\n\nAbout the job\n\nApply\nnow").apply_method == "unknown"
    assert parse("nothing useful") == Detail("", "unknown")


async def test_read_jobs_opens_nothing_without_the_chrome(monkeypatch):
    monkeypatch.setattr(linkedin_detail, "running_endpoint", lambda: None)
    assert await linkedin_detail.read_jobs(["https://www.linkedin.com/jobs/view/1/"]) == {}


async def test_read_jobs_respects_the_daily_cap(monkeypatch):
    from datetime import date

    day = date(2026, 1, 1)
    monkeypatch.setattr(linkedin_detail, "running_endpoint", lambda: {"port": 1})
    monkeypatch.setitem(linkedin_detail._opened, day, linkedin_detail.MAX_PER_DAY)
    assert await linkedin_detail.read_jobs(["https://x/"], today=day) == {}


async def test_poller_rescores_linkedin_cards_from_their_pages(monkeypatch):
    from datetime import timedelta

    from sqlalchemy import select

    from backend.database import AsyncSessionLocal
    from backend.matching.matcher import JobMatcher
    from backend.models.job import Job, JobMatch
    from backend.models.user import SearchSettings
    from backend.monitor import poller, store
    from backend.scraping.sources import ADAPTERS, PollResult
    from tests.test_monitor_poller import NOW, add_source, board_client, freeze, hooks, raw_job

    notify, alert = hooks()
    polls = [
        [raw_job("li-0", "Robotics Engineer seed")],
        [
            raw_job("li-1", "Robotics Engineer fit"),
            raw_job("li-2", "Robotics Engineer closed"),
            raw_job("li-3", "Robotics Engineer unfit"),
        ],
    ]

    async def poll(client, cfg, etag, last_scraped_at) -> PollResult:
        return PollResult(jobs=polls.pop(0))

    fit = "Requirements\n- Python\n- ROS 2\n- C++\n" + "We build mobile manipulators. " * 4
    unfit = "Requirements\n- SAP\n- Salesforce\n- Excel\n" + "We sell software to banks. " * 4

    async def read_jobs(urls: list[str]) -> dict[str, Detail]:
        pages = [Detail(fit, "easy_apply"), Detail("", "closed"), Detail(unfit, "external")]
        return dict(zip(urls, pages, strict=True))

    async def load_matcher(db) -> JobMatcher:  # not a CV another test left in the data dir
        return JobMatcher(extra_terms=["robotics", "python", "ros 2", "c++"])

    monkeypatch.setitem(ADAPTERS, "linkedin_guest", poll)
    monkeypatch.setattr(poller, "read_jobs", read_jobs)
    monkeypatch.setattr(poller, "load_matcher", load_matcher)
    monkeypatch.setattr(store, "load_matcher", load_matcher)
    async with AsyncSessionLocal() as db:
        db.add(SearchSettings(keywords={"include": ["robotics", "python", "ros 2", "c++"]}))
        src = await add_source(db, name="li", config={"adapter": "linkedin_guest"})
        freeze(monkeypatch)
        await poller.poll_source(db, src, board_client(), notify, alert)
        freeze(monkeypatch, NOW + timedelta(minutes=5))
        assert await poller.poll_source(db, src, board_client(), notify, alert) == 3
        rows = {
            job.title.split()[-1]: (job, match)
            for job, match in (
                await db.execute(select(Job, JobMatch).join(JobMatch, JobMatch.job_id == Job.id))
            ).all()
        }
    # the closed posting is not pushed; the title keeps the unfit one above the minimum
    assert [call.args[1].title for call in notify.await_args_list] == [
        "Robotics Engineer fit",
        "Robotics Engineer unfit",
    ]
    job, match = rows["fit"]
    assert (job.description, job.apply_method) == (fit, "easy_apply")
    assert not match.provisional and match.score > 50
    assert rows["closed"][0].apply_method == "closed" and rows["closed"][1].provisional
    assert rows["unfit"][1].score < match.score and not rows["unfit"][1].provisional
