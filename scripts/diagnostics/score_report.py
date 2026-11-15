#!/usr/bin/env python3
"""Score every job of a JobPilot database and print how the scores are distributed.

The database is opened read-only. Jobs are scored with the search settings stored in
it and the base CV (``--cv``, else the profile's CV next to the database, else the
keywords alone), exactly as the monitor does. Nothing is written anywhere.

Usage
-----

    env -u PYTHONPATH uv run python scripts/diagnostics/score_report.py \\
        --db data/jobpilot.db [--cv data/templates/cv.tex] [--top 40] [--seed 0]

Prints the time per job, the share each hard filter rejects, the share at or above
``min_match_score``, the score histogram, the top N and 20 random mid-ranked jobs with
their covered and missing skills.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.latex.artifacts import resolve_base_source  # noqa: E402
from backend.matching.filters import JobFilters  # noqa: E402
from backend.matching.matcher import JobMatcher  # noqa: E402
from backend.matching.scorer import ScoreResult  # noqa: E402
from backend.models.schemas import JobDetails  # noqa: E402
from backend.scheduler.batch_runner import _extract_json_list  # noqa: E402

MID_SAMPLE = 20


def _items(raw: str | None, key: str) -> list[str]:
    return _extract_json_list(json.loads(raw) if raw else None, key)


def load(db_path: Path, cv_path: Path | None) -> tuple[list[JobDetails], JobFilters, str]:
    """(jobs, filters, CV source) read from the database, opened read-only."""
    con = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute("SELECT * FROM search_settings LIMIT 1").fetchone()
        filters = JobFilters()
        if row is not None:  # same mapping as backend.monitor.poller._load_filters
            filters = JobFilters(
                keywords=_items(row["keywords"], "include"),
                locations=_items(row["locations"], "items"),
                remote_only=bool(row["remote_only"]),
                excluded_keywords=_items(row["excluded_keywords"], "items"),
                excluded_companies=_items(row["excluded_companies"], "items"),
                min_score=float(row["min_match_score"]),
            )
        if cv_path is None:
            profile = con.execute("SELECT base_cv_path FROM user_profile LIMIT 1").fetchone()
            cv_path = resolve_base_source(profile and profile[0], db_path.parent, "*.tex")
        jobs = [
            JobDetails(
                title=r["title"],
                company=r["company"],
                location=r["location"] or "",
                country=r["country"] or "",
                description=r["description"] or "",
            )
            for r in con.execute(
                "SELECT title, company, location, country, description FROM jobs ORDER BY id"
            )
        ]
    finally:
        con.close()
    return jobs, filters, cv_path.read_text(encoding="utf-8") if cv_path else ""


def _line(job: JobDetails, result: ScoreResult) -> str:
    flags = " provisional" if result.provisional else ""
    flags += " location?" if result.location_unverified else ""
    return (
        f"{result.score:6.2f}{flags} | {job.title} | {job.company} | {job.location}\n"
        f"         covered: {', '.join(result.covered + result.partial) or '-'}\n"
        f"         missing: {', '.join(result.missing) or '-'}"
    )


def build_report(db_path: Path, cv_path: Path | None = None, top: int = 40, seed: int = 0) -> str:
    jobs, filters, cv_text = load(db_path, cv_path)
    matcher = JobMatcher(cv_text, filters.keywords)
    start = time.perf_counter()
    results = [matcher.explain(job, filters) for job in jobs]
    elapsed = time.perf_counter() - start
    total = len(jobs)

    def share(count: int) -> str:
        return f"{count} ({100 * count / total:.1f}%)" if total else "0"

    passed = [(j, r) for j, r in zip(jobs, results, strict=True) if r.passed_filters]
    passed.sort(key=lambda pair: -pair[1].score)
    min_score = filters.min_score
    out = [
        f"jobs scored: {total} in {elapsed:.2f} s"
        + (f" ({1000 * elapsed / total:.2f} ms/job)" if total else ""),
        f"keywords: {filters.keywords}  locations: {filters.locations}"
        f"  remote only: {filters.remote_only}",
        f"CV: {'yes' if cv_text.strip() else 'none, keywords stand in'}",
        f"passed hard filters: {share(len(passed))}",
    ]
    rejected = Counter(r.reject_reason for r in results if not r.passed_filters)
    out += [f"  rejected by {reason}: {share(n)}" for reason, n in rejected.most_common()]
    out += [
        f"at or above min score {min_score:g}: "
        + share(sum(r.score >= min_score for _, r in passed)),
        f"provisional (title only): {share(sum(r.provisional for _, r in passed))}",
        f"location unverified: {share(sum(r.location_unverified for _, r in passed))}",
        "",
        "score histogram (jobs that passed the hard filters):",
    ]
    buckets = Counter(min(int(r.score // 10), 9) for _, r in passed)
    widest = max(buckets.values(), default=1)
    for b in range(10):
        bar = "#" * round(40 * buckets[b] / widest)
        out.append(f"  {10 * b:3d}-{10 * b + 10:<3d} {buckets[b]:6d} {bar}")
    out += ["", f"top {top}:"] + [_line(j, r) for j, r in passed[:top]]
    # Mid-ranked: the middle third of what scored above 0, outside the top N.
    ranked = [pair for pair in passed[top:] if pair[1].score > 0]
    middle = ranked[len(ranked) // 3 : len(ranked) - len(ranked) // 3]
    sample = random.Random(seed).sample(middle, min(MID_SAMPLE, len(middle)))
    out += ["", f"{len(sample)} random mid-ranked:"]
    out += [_line(j, r) for j, r in sorted(sample, key=lambda pair: -pair[1].score)]
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, required=True, help="SQLite database to read")
    parser.add_argument("--cv", type=Path, help="LaTeX CV (default: the profile's base CV)")
    parser.add_argument("--top", type=int, default=40)
    parser.add_argument("--seed", type=int, default=0, help="seed of the mid-ranked sample")
    args = parser.parse_args()
    print(build_report(args.db, args.cv, args.top, args.seed))


if __name__ == "__main__":
    main()
