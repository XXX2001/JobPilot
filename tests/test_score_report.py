"""scripts/diagnostics/score_report.py on a small database file. Read-only, no network."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.models.base import Base
from backend.models.job import Job
from backend.models.user import SearchSettings

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "score_report", ROOT / "scripts" / "diagnostics" / "score_report.py"
)
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)

DESCRIPTION = """About the role
You will build our data services with a small backend team in Paris.

Requirements
- {skills}
"""


def test_report_ranks_jobs_and_leaves_the_database_untouched(tmp_path):
    db = tmp_path / "jobpilot.db"
    engine = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(engine)
    jobs = [
        ("Python Backend Engineer", "Paris", DESCRIPTION.format(skills="Python and PostgreSQL")),
        ("Java Developer", "Paris", DESCRIPTION.format(skills="Java and Spring Boot")),
        ("Python Developer", "Berlin, Germany", DESCRIPTION.format(skills="Python")),
        ("Python Engineer", "Paris", ""),
    ]
    with Session(engine) as session:
        session.add(
            SearchSettings(keywords={"include": ["python"]}, locations={"items": ["Paris"]})
        )
        session.add_all(
            Job(title=t, company="ACME", location=loc, description=d, url=t, dedup_hash=t)
            for t, loc, d in jobs
        )
        session.commit()
    engine.dispose()
    before = db.read_bytes()
    cv = tmp_path / "cv.tex"
    cv.write_text("Python, PostgreSQL, Docker", encoding="utf-8")

    report = script.build_report(db, cv, top=2)

    assert db.read_bytes() == before
    assert "jobs scored: 4" in report
    assert "passed hard filters: 3 (75.0%)" in report
    assert "rejected by location: 1 (25.0%)" in report
    assert "at or above min score 30: 2 (50.0%)" in report
    top = report.split("top 2:")[1].split("random mid-ranked")[0]
    assert top.index("Python Backend Engineer") < top.index("Python Engineer")
    assert "70.00 provisional | Python Engineer" in top
    assert "covered: postgresql, python" in top
    assert "Java Developer" not in top and "Berlin" not in report
