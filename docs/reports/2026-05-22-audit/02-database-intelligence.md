# JobPilot — Database Intelligence Audit (2026-05-22)

Scope: ORM models in `backend/models/`, Alembic migrations in `alembic/versions/`,
and every query site under `backend/api/`, `backend/applier/`, `backend/scheduler/`,
`backend/scraping/`, `backend/matching/`.

> Cross-reference: the standards backlog at `docs/reports/2026-05-22-standards/INDEX.md`
> covers naming, type-safety, error handling, structure. **This report covers schema
> design, indexes, query patterns, transactions, migrations, and Postgres-native
> opportunities — none of which appear in the standards backlog.** The only intentional
> overlap is **ST-05 `utcnow` migration**, which I do not duplicate but reference where
> tz-awareness becomes a transaction-boundary correctness concern.

---

## TL;DR — top 5 issues

1. **Zero indexes other than primary keys + two `UNIQUE` constraints.**
   No `index=True`, no `Index()`, no `ForeignKey()` constraint, no `relationship()` in
   the entire codebase. Every `JobMatch.job_id`, `Application.job_match_id`,
   `TailoredDocument.job_match_id`, `ApplicationEvent.application_id`,
   `Application.created_at`, `Application.applied_at`, `JobMatch.batch_date`,
   `JobMatch.status`, `Application.status` lookup is a full table scan. At 10k+ jobs and
   100s of matches/day this is already a perf hazard; at 100k+ rows the queue and
   analytics endpoints will be visibly slow.
2. **Alembic is configured but never run by the app.** `backend/database.py:69-89`
   calls `Base.metadata.create_all` + an ad-hoc `_migrate_add_columns()` helper at
   startup. The two real migrations in `alembic/versions/` are already out of sync
   with the models — `country`, `gap_severity`, `ats_score`, `fit_assessment_json`,
   `cv_modification_sensitivity`, `cv_tailoring_enabled`, `max_results_per_source`,
   `max_job_age_days`, `countries`, `excluded_companies`, `excluded_keywords`,
   `experience_min/max`, `remote_only`, `job_types`, `languages`, `linkedin_url`,
   `driver_license`, `mobility` were never added by a migration. Any prod migration to
   PostgreSQL is currently impossible without first rebuilding the migration history.
3. **Classic N+1 in `GET /api/jobs`** (`backend/api/jobs.py:100-127`): the handler
   issues one `SELECT JobMatch` per row returned (up to 200 extra queries per call).
   The morning batch repeats the pattern at `backend/scheduler/morning_batch.py:343-345`
   (1 query per match for fit-assessment write-back) and at lines 527, 558, 574
   (three serial queries per ranked job).
4. **Daily-apply-limit race condition.** `backend/applier/daily_limit.py:62-67` reads
   the count, then `backend/applier/engine.py:271-288` writes the new `Application` row
   in a separate transaction with no `SELECT ... FOR UPDATE`, no unique constraint, no
   advisory lock. Two concurrent apply requests will both pass the limit check.
5. **Schema-design smells with concrete consequences.**
   `JobMatch.status` / `Application.status` / `Application.method` are unconstrained
   strings — the API endpoint at `backend/api/queue.py:256` validates against a
   hard-coded set `{"new", "skipped", "applying", "applied", "rejected"}`, but the
   applier engine writes `"manual"` (`backend/applier/engine.py:268, 282`) which is not
   in that allowed set. Result: a "manual" application that the queue API will reject
   on subsequent status updates. Move these to `Enum` columns with a `CheckConstraint`.

---

## Findings table

| ID    | Title                                                                   | Severity | File:line                                                | Suggested change |
|-------|-------------------------------------------------------------------------|----------|----------------------------------------------------------|------------------|
| DB-01 | Zero indexes on FK / WHERE / ORDER BY columns                           | High     | `backend/models/job.py:104`, `application.py:44,64`, etc.| Add `index=True` to FK-like columns + composite `Index` for hot pairs |
| DB-02 | Alembic exists but startup uses `create_all` — migrations out of sync   | High     | `backend/database.py:83-86`; `alembic/versions/*.py`     | Replace `create_all` with `alembic upgrade head`; regenerate baseline |
| DB-03 | N+1 query in `list_jobs`                                                | High     | `backend/api/jobs.py:100-127`                            | Replace per-row score lookup with `JOIN LATERAL` / window-function-derived latest match |
| DB-04 | N+1 in batch `_store_matches` (3 SELECTs per ranked job)                | High     | `backend/scheduler/morning_batch.py:527,558,574`          | Bulk-load existing rows by hash, then in-memory diff |
| DB-05 | N+1 in fit-assessment write-back (1 SELECT JobMatch per match)          | Medium   | `backend/scheduler/morning_batch.py:343-345`             | Batch update or use `update()` statement |
| DB-06 | Daily-apply-limit race condition                                        | High     | `backend/applier/daily_limit.py:56-67`; `engine.py:271`  | Counter row + `UPDATE … WHERE count<limit RETURNING`, or unique partial index |
| DB-07 | `status` / `method` columns are unconstrained strings — vocab drift     | Medium   | `backend/models/application.py:45-46`, `job.py:107`      | Switch to SQLAlchemy `Enum` + `CheckConstraint`; reconcile `"manual"` vs `{"applied",…}` |
| DB-08 | `nullable=True` defaults on schema FK-likes that should be `NOT NULL`   | Medium   | `backend/models/application.py:44,64`; `document.py:42`  | Make `job_match_id`, `application_id` non-null where domain guarantees parent |
| DB-09 | Foreign-key constraints completely absent (and `PRAGMA foreign_keys=OFF`)| Medium  | All models; `database.py:48-63`                           | Add `ForeignKey(...)` to columns; `PRAGMA foreign_keys=ON` in WAL listener |
| DB-10 | JSON columns hide queryable structured data                              | Medium  | `backend/models/job.py:81,82,89,106,112`; `user.py:55…`  | On Postgres switch to `JSONB`; add GIN indexes on `keyword_hits`, `requirements`, `fit_assessment_json` |
| DB-11 | No `relationship()` declarations — every join is hand-written           | Medium   | All models                                               | Add `relationship(..., lazy="raise")` so N+1 fails loudly |
| DB-12 | Commit-per-iteration in CV-generation loop                              | Medium   | `backend/scheduler/morning_batch.py:452,626`             | Stage all `TailoredDocument` rows, then one `commit()` |
| DB-13 | Multi-step write in `_record_application` is atomic but isn't wrapped   | Low      | `backend/applier/engine.py:264-297`                      | Use `async with db.begin():` instead of manual flush+commit+rollback |
| DB-14 | Sessions opened without explicit close in `_seed_default_sources`       | Low      | `backend/database.py:160-181`                            | Already uses `async with`, OK — but no `expire_on_commit` rationale documented |
| DB-15 | `created_at`/`updated_at` not consistent across tables                  | Low      | `backend/models/job.py` (`Job` has no `updated_at`), etc.| Add a `TimestampMixin` (see ST-05); make tz-aware |
| DB-16 | Analytics: full-table scan + Python-side day bucketing                  | Medium   | `backend/api/analytics.py:130-149`                       | Use `date_trunc('day', created_at)` + `GROUP BY` on Postgres, partial index on recent rows |
| DB-17 | `func.avg(JobMatch.score)` over entire history with no time window      | Low      | `backend/api/analytics.py:103-106`                       | Restrict to last N days; cache for 60s |
| DB-18 | `Job.dedup_hash` is the only unique constraint — different normalisers  | Medium   | `scraping/deduplicator.py:30-35` vs `api/jobs.py:218`, `morning_batch.py:522` | Single shared `_make_dedup_hash()` helper |
| DB-19 | `min_match_score` / `daily_limit` columns are `nullable=False` with no CHECK | Low | `backend/models/user.py:83-84`                            | Add `CheckConstraint("min_match_score BETWEEN 0 AND 100")`, `daily_limit >= 0` |
| DB-20 | `expire_on_commit=False` global default — attributes can go stale       | Low      | `backend/database.py:66`                                 | Document the trade-off; ensure write-after-commit code re-fetches |
| DB-21 | No connection-pool tuning, no `pool_pre_ping` — works for SQLite, fragile for Postgres | Low | `backend/database.py:42-45`                       | When migrating, set `pool_size`, `max_overflow`, `pool_pre_ping=True` |
| DB-22 | Hot-path `UserProfile`/`SearchSettings` singletons re-fetched on every request | Medium | `backend/api/applications.py:470-472`; settings.py everywhere | Cache the two singleton rows in app.state with TTL or invalidate-on-write |
| DB-23 | `_migrate_add_columns()` is ad-hoc DDL outside Alembic                   | High     | `backend/database.py:128-149`                            | Move to a proper Alembic revision; delete the helper |
| DB-24 | `JobMatch` lacks unique constraint on `(job_id, batch_date)`            | Medium   | `backend/models/job.py:104,108`; check at `morning_batch.py:573-580` | Add unique index → eliminate the read-then-write check |

---

## Per-finding details

### DB-01 — Zero indexes on FK / WHERE / ORDER BY columns (High)

**Evidence.** Grep confirms there is no `index=True`, no `Index(...)`, no
`ForeignKey(...)` anywhere in `backend/models/`. Both migrations in
`alembic/versions/` emit no `op.create_index()` call.

Concrete hot paths that scan today:

```python
# backend/models/job.py:104  — declared without index
job_id: Mapped[int] = mapped_column(Integer)  # FK to jobs.id
```

```python
# backend/applier/daily_limit.py:62
stmt = select(func.count(Application.id)).where(
    Application.applied_at >= today,                  # no index on applied_at
    Application.status.in_(["applied", "pending"]),   # no index on status
)
```

```python
# backend/api/applications.py:182
stmt = stmt.order_by(Application.created_at.desc()).offset(skip).limit(limit)
# created_at has no index; ORDER BY on every list call is a full scan + sort
```

```python
# backend/api/queue.py:94-99
select(JobMatch, Job).join(Job, Job.id == JobMatch.job_id)
    .where(JobMatch.status == "new")
    .order_by(JobMatch.batch_date.desc(), JobMatch.score.desc())
# Status filter + 2-column sort, no index supports either
```

**Suggested indexes.**

| Table                | Columns                                           | Reason                                              |
|----------------------|---------------------------------------------------|-----------------------------------------------------|
| `job_matches`        | `(status, batch_date DESC, score DESC)`           | Queue listing, top-of-funnel                        |
| `job_matches`        | `(job_id, batch_date)` UNIQUE                     | De-dup match-per-day (see DB-24)                    |
| `job_matches`        | `(job_id)` index                                  | FK lookup from `api/jobs.py:103`                    |
| `applications`       | `(status, created_at DESC)`                       | `list_applications` filtered listing                |
| `applications`       | `(applied_at, status)` partial WHERE `applied_at>=…` | DailyLimitGuard hot path                         |
| `applications`       | `(job_match_id)` index                            | join in apply lookup                                |
| `application_events` | `(application_id, event_date)`                    | event log fetch                                     |
| `tailored_documents` | `(job_match_id, doc_type, created_at DESC)`       | `_resolve_documents` latest-doc lookup              |
| `jobs`               | `(scraped_at DESC)`                               | `list_jobs` default ORDER BY                        |
| `jobs`               | `(company, title, location)` if `dedup_hash` is replaced | Same logical purpose, more diagnosable        |
| `job_sources`        | `(enabled, name)` partial WHERE `enabled=true`    | scheduler reads only enabled rows                   |

Most of these can ship as a single Alembic revision; payoff is large for cost zero.

---

### DB-02 — Alembic configured but `create_all` runs at startup (High)

**Evidence.**

```python
# backend/database.py:81-89
from backend.models import Base

async with engine.begin() as conn:
    await conn.run_sync(Base.metadata.create_all)

await _migrate_add_columns()
```

`alembic/env.py` exists, `alembic.ini` points at the same SQLite URL, and the
two real migrations declare a clean upgrade graph
(`071b973b48b2_initial_schema → df6eea4756c3_add_site_credentials_table`).

But the initial migration is **missing dozens of columns** that the models declare:

| Model column                                    | In initial migration? |
|-------------------------------------------------|-----------------------|
| `jobs.country`                                  | No                    |
| `job_matches.gap_severity`                      | No                    |
| `job_matches.ats_score`                         | No                    |
| `job_matches.fit_assessment_json`               | No                    |
| `search_settings.cv_modification_sensitivity`   | No                    |
| `search_settings.cv_tailoring_enabled`          | No (ad-hoc ALTER)     |
| `search_settings.max_results_per_source`        | No (ad-hoc ALTER)     |
| `search_settings.max_job_age_days`              | No (ad-hoc ALTER)     |
| `search_settings.countries`                     | No                    |
| `search_settings.excluded_companies`            | No                    |
| `search_settings.languages`                     | No                    |
| `search_settings.job_types`                     | No                    |
| `search_settings.remote_only`, `excluded_keywords` | Some are; many gaps |
| `user_profile.linkedin_url`, `driver_license`, `mobility` | No        |

Conversely the initial migration includes `search_settings.batch_time` (line 110) which
**does not exist on the model anymore** — the model was edited without a corresponding
migration. So running `alembic downgrade base; alembic upgrade head` against a clean DB
produces a schema that **does not match the ORM**.

**Suggested change.**
1. Generate a fresh Alembic baseline (`alembic revision --autogenerate -m "rebaseline"`),
   review the diff, and stamp the current DB.
2. Replace `Base.metadata.create_all` in `init_db()` with `alembic.command.upgrade(cfg, "head")`.
3. Delete `_migrate_add_columns` (DB-23).
4. Add a CI guard: a test that runs `alembic upgrade head` on a fresh SQLite file and
   diffs `Base.metadata` vs reflected schema (alembic's `compare_metadata` works).

---

### DB-03 — N+1 in `GET /api/jobs` (High)

**Evidence.**

```python
# backend/api/jobs.py:94-127
stmt = select(Job).order_by(Job.scraped_at.desc()).offset(skip).limit(limit)
result = await db.execute(stmt)
jobs = result.scalars().all()

for job in jobs:
    match_stmt = (
        select(JobMatch.score)
        .where(JobMatch.job_id == job.id)
        .order_by(JobMatch.matched_at.desc())
        .limit(1)
    )
    match_result = await db.execute(match_stmt)
    score_row = match_result.scalar_one_or_none()
    ...
```

With `limit=200` this is 201 queries per call. Also note that `min_score` filtering
happens *in Python* after the per-row lookup — meaning the result page can have fewer
items than `limit` and the API caller has no way to paginate predictably.

**Suggested change.**

Use a single query with a correlated subquery / lateral join / window function:

```python
# One SQL: latest match per job
latest_match_sq = (
    select(
        JobMatch.job_id,
        JobMatch.score,
        func.row_number().over(
            partition_by=JobMatch.job_id,
            order_by=JobMatch.matched_at.desc(),
        ).label("rn"),
    ).subquery()
)
stmt = (
    select(Job, latest_match_sq.c.score)
    .outerjoin(latest_match_sq, and_(latest_match_sq.c.job_id == Job.id,
                                     latest_match_sq.c.rn == 1))
    .order_by(Job.scraped_at.desc())
)
if min_score is not None:
    stmt = stmt.where(latest_match_sq.c.score >= min_score)
stmt = stmt.offset(skip).limit(limit)
```

Same fix applies to `get_job` (`jobs.py:152-157`) — that's only 2 queries today but
becomes 1 with the LATERAL pattern.

---

### DB-04 — N+1 in batch `_store_matches` (High)

**Evidence.**

```python
# backend/scheduler/morning_batch.py:520-595  (loop body)
for jd, score in ranked:
    dedup_hash = hashlib.md5(...)

    existing = (
        await db.execute(select(Job).where(Job.dedup_hash == dedup_hash))   # 1
    ).scalar_one_or_none()
    ...
    any_actioned = (
        await db.execute(
            select(JobMatch).where(
                JobMatch.job_id == job_row.id,
                JobMatch.status.in_(["applied", "skipped"]),
            )
        )                                                                    # 2
    ).scalar_one_or_none()
    ...
    existing_match = (
        await db.execute(
            select(JobMatch).where(
                JobMatch.job_id == job_row.id,
                JobMatch.batch_date == today,
            )
        )                                                                    # 3
    ).scalar_one_or_none()
```

For a typical 50-job batch that's 150 round-trips before the single `commit` at the end.

**Suggested change.**

1. Bulk-load matches first:

```python
all_hashes = [hashlib.md5(...).hexdigest() for jd, _ in ranked]
existing_jobs = {
    j.dedup_hash: j
    for j in (await db.execute(select(Job).where(Job.dedup_hash.in_(all_hashes)))).scalars()
}
existing_match_status = await db.execute(
    select(JobMatch.job_id, JobMatch.status, JobMatch.batch_date)
    .where(JobMatch.job_id.in_(known_job_ids))
)
# Build in-memory dicts, then iterate without DB round-trips.
```

2. Bonus: add the `UNIQUE(job_id, batch_date)` constraint (DB-24) so step 3 of the loop
   collapses to "INSERT … ON CONFLICT DO UPDATE", eliminating the SELECT entirely.

---

### DB-05 — Per-match write-back in fit-assessment loop (Medium)

**Evidence.**

```python
# backend/scheduler/morning_batch.py:329-364
for mid in new_match_ids:
    ...
    match_row = (await db.execute(
        select(JobMatch).where(JobMatch.id == mid)
    )).scalar_one_or_none()
    if match_row:
        match_row.gap_severity = assessment.severity
        match_row.ats_score = assessment.simulated_ats_score
        match_row.fit_assessment_json = assessment.to_dict()
    ...
await db.commit()
```

This batches commits but still issues N SELECTs.

**Suggested change.** Bulk-fetch once before the loop:

```python
match_rows = {
    m.id: m
    for m in (await db.execute(select(JobMatch).where(JobMatch.id.in_(new_match_ids)))).scalars()
}
for mid in new_match_ids:
    m = match_rows.get(mid)
    if m: m.gap_severity = ...
```

Side-bug: the `match_to_jd` mapping at lines 320-325 zips `new_match_ids` to the
ranked list in order, but `_store_matches` may skip jobs (the
"already applied/skipped" check at line 566 does `continue`). So a skip in
`_store_matches` would mis-pair every subsequent assessment with the wrong job.
**Schema-level fix**: return `(match_id, job_details)` pairs directly from
`_store_matches` instead of relying on iteration alignment.

---

### DB-06 — Daily-apply-limit race condition (High)

**Evidence.**

```python
# backend/applier/daily_limit.py:56-67
async def remaining_today(self) -> int:
    today = date.today()
    stmt = select(func.count(Application.id)).where(
        Application.applied_at >= today,
        Application.status.in_(["applied", "pending"]),
    )
    count = (await self.db.execute(stmt)).scalar_one_or_none() or 0
    return max(0, self.limit - count)
```

Then in `engine.py:160-200` the workflow is:
1. read count (T0)
2. perform browser actions (T0 + many seconds — apply forms, captcha, etc.)
3. write `Application` row (T1)

Two parallel applies started at T0 can both see `count < limit` and both succeed.
There is no row-level lock, no unique constraint, no advisory lock. The only
in-process guard is `self._confirm_events` on the engine (engine.py:172-179), which
serialises **per-`job_match_id`** but not across the whole day.

Also: `applied_at >= today` requires `today` to be a `datetime`, not a `date`.
The `>=` comparison between a column of `DateTime` and a `date` object is
SQLite-only sugar; on Postgres it would silently fail to filter. The `# type: ignore[operator]`
on line 63 hints the author already knew about this.

**Suggested change.** Pick one:

- **Lightest:** add a counter row `apply_counters(date PK, count INT)` and
  `UPDATE apply_counters SET count = count+1 WHERE date=? AND count < ? RETURNING count`.
  Atomic on SQLite (single writer) and on Postgres (row lock).
- **Schema-native:** add a Postgres partial unique index on
  `applications(applied_at::date)` with a `WHERE status IN ('applied','pending')` and
  generated daily-sequence column — guarantees the cap via constraint violation.
- **Pragmatic interim:** wrap the read+write in `async with db.begin()` and use
  `SELECT … FOR UPDATE` on the counter row, or hold an asyncio.Lock in the engine
  singleton.

---

### DB-07 — Unconstrained `status` / `method` strings drift across writers (Medium)

**Evidence.**

```python
# backend/models/job.py:107
status: Mapped[str] = mapped_column(String, default="new")

# backend/api/queue.py:256-260
allowed = {"new", "skipped", "applying", "applied", "rejected"}
if body.status not in allowed:
    raise HTTPException(status_code=422, …)

# backend/applier/engine.py:268
applied_at=datetime.utcnow() if result.status in ("applied", "manual") else None,
# engine.py:282
if result.status in ("applied", "manual"):
    ...
    match.status = "applied"
```

The engine writes status `"manual"` for `ApplicationResult`, but the queue endpoint's
allowed set does not include `"manual"`. A user who applied manually cannot then update
the queue status without a 422.

Same problem for `Application.method` — `Literal["auto", "assisted", "manual"]` is
enforced **only at the API boundary** (`applications.py:95`); nothing prevents the
applier engine or a migration script from writing arbitrary strings.

**Suggested change.**

```python
from enum import Enum
from sqlalchemy import Enum as SqlEnum, CheckConstraint

class MatchStatus(str, Enum):
    NEW = "new"; SKIPPED = "skipped"; APPLYING = "applying"
    APPLIED = "applied"; REJECTED = "rejected"

class JobMatch(Base):
    status: Mapped[MatchStatus] = mapped_column(
        SqlEnum(MatchStatus, native_enum=False, length=16),
        default=MatchStatus.NEW, nullable=False,
    )
    __table_args__ = (
        CheckConstraint("status IN ('new','skipped','applying','applied','rejected')"),
    )
```

Do the same for `Application.status`, `Application.method`,
`ApplicationEvent.event_type`, `TailoredDocument.doc_type`, `JobSource.type`,
`SearchSettings.cv_modification_sensitivity`. Then drop the manual allowed-set
checks in route handlers and let the DB / Pydantic catch it.

---

### DB-08 — Critical FK-like columns are `nullable=True` (Medium)

**Evidence.**

```python
# backend/models/application.py:44
job_match_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
# but every code path that creates an Application sets this from path-param `match_id`
```

```python
# backend/models/document.py:42
job_match_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
# but _store_tailored_doc and _resolve_documents both require it
```

```python
# backend/models/application.py:64  (ApplicationEvent)
application_id: Mapped[int] = mapped_column(Integer)
# default Optional via Python? Actually marked Integer, not Optional — but no NOT NULL in migration
```

The initial migration at `alembic/versions/071b973b48b2_initial_schema.py:24-31` declares
`application_events.application_id` with `nullable=False`, matching the model intent,
but `applications.job_match_id` is `nullable=True` in the migration too (line 34). Same
for `tailored_documents.job_match_id` (line 116).

**Consequence.** Orphan rows are possible. The only place an `Application` can validly
have a NULL `job_match_id` is the `create_application` API (manual-flow with no match),
but that's a single endpoint — the dominant write path requires it.

**Suggested change.** Either:
- Make `job_match_id` `NOT NULL` and require the manual endpoint to create a synthetic
  match first.
- Or: split into two tables (`applications_manual`, `applications_matched`) — overkill.
- Or: keep nullable, but add a `CheckConstraint("method='manual' OR job_match_id IS NOT NULL")`.

---

### DB-09 — No `ForeignKey` declarations; `PRAGMA foreign_keys` off by default (Medium)

**Evidence.** Models comment this design choice explicitly:

```python
# backend/models/job.py:14-16
#   Foreign-key relationships are application-enforced only; no DB-level
#   FK constraints are active (PRAGMA foreign_keys is OFF).
```

`backend/database.py:48-63` sets `PRAGMA journal_mode=WAL` but **does not**
set `PRAGMA foreign_keys=ON` (SQLite's per-connection default is OFF).

**Why this is a problem.** When the codebase migrates to PostgreSQL (per the
"intelligence opportunities" section below — FTS, JSONB, materialised views), this
choice silently shifts: Postgres enforces FKs by default whether you declared
`ForeignKey()` or not (you'd need to declare them to get the safety net at all).
Right now SQLite is hiding orphan-row bugs that would surface immediately under PG.

**Suggested change.** Add `ForeignKey()` to every FK column:

```python
job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
job_match_id: Mapped[int] = mapped_column(ForeignKey("job_matches.id", ondelete="SET NULL"))
application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"))
```

And in the WAL listener:

```python
@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragmas(dbapi_conn, _):
    dbapi_conn.execute("PRAGMA journal_mode=WAL")
    dbapi_conn.execute("PRAGMA foreign_keys=ON")
```

---

### DB-10 — JSON columns hide queryable, indexable data (Medium)

**Evidence.**

```python
# backend/models/job.py:81-82,89,106,112
requirements: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
benefits:     Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
raw_data:     Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
keyword_hits: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
fit_assessment_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
```

```python
# backend/models/user.py:73-82
keywords: Mapped[dict]            = mapped_column(JSON, nullable=False)
excluded_keywords: Mapped[dict]   = mapped_column(JSON, nullable=True)
locations: Mapped[dict]           = mapped_column(JSON, nullable=True)
job_types, languages, excluded_companies, countries: …
```

On SQLite, `JSON` is just `TEXT` — every "does this job have skill X" check has to load
+ parse + filter in Python. Future analytics like "what fraction of applied jobs
required Python?" require a full scan.

**Suggested change for SQLite-only present:**
- Where a JSON column is conceptually `list[str]` (keywords, countries, languages,
  job_types, excluded_companies), prefer a real association table (`search_keyword`,
  `search_country`). Cleaner, indexable, cheaper to display in the UI.
- Otherwise leave as JSON but document the read pattern.

**For Postgres future:** these become `JSONB` with GIN indexes:
```sql
CREATE INDEX idx_jobs_requirements_gin ON jobs USING gin (requirements jsonb_path_ops);
CREATE INDEX idx_matches_assessment_gin ON job_matches USING gin (fit_assessment_json);
```
And every `keyword_hits @> '["python"]'` becomes a cheap GIN lookup.

---

### DB-11 — No `relationship()` declarations (Medium)

**Evidence.** Grep confirms zero `relationship(` calls. Every join is hand-written
in SQL:

```python
# backend/api/applications.py:175-179
stmt = (
    select(Application, Job)
    .outerjoin(JobMatch, Application.job_match_id == JobMatch.id)
    .outerjoin(Job, JobMatch.job_id == Job.id)
)
```

**Consequence.**
- Lazy-load N+1 traps are impossible to enable (`lazy="raise"`) because there are no
  relationships to load.
- Every consumer has to remember the join graph by hand.
- `from_attributes=True` Pydantic models can't traverse relationships — the code
  manually copies `job.title → out.job_title` (applications.py:211-214) for every
  parent and every endpoint.

**Suggested change.** Once `ForeignKey()` is declared (DB-09), add reciprocal
relationships and use them:

```python
class JobMatch(Base):
    job: Mapped["Job"] = relationship(back_populates="matches", lazy="joined")
class Job(Base):
    matches: Mapped[list["JobMatch"]] = relationship(back_populates="job", lazy="raise")
class Application(Base):
    match: Mapped[Optional["JobMatch"]] = relationship(lazy="joined")
    events: Mapped[list["ApplicationEvent"]] = relationship(
        order_by="ApplicationEvent.event_date", lazy="selectin",
    )
```

Then `list_applications` becomes:

```python
stmt = select(Application).options(selectinload(Application.events),
                                   joinedload(Application.match).joinedload(JobMatch.job))
```

— one query instead of three.

---

### DB-12 — Commit-per-iteration in CV-generation loop (Medium)

**Evidence.**

```python
# backend/scheduler/morning_batch.py:442-455
raw_results = await asyncio.gather(*[_gen_one(mid, jd) for mid, jd in pairs], …)
for i, outcome in enumerate(raw_results):
    if isinstance(outcome, BaseException): ...
    mid, tailored = outcome
    await self._store_tailored_doc(db, mid, tailored, doc_type="cv")   # commits inside
```

```python
# backend/scheduler/morning_batch.py:618-626
doc = TailoredDocument(...)
db.add(doc)
await db.commit()    # one commit per CV
```

For a 10-CV batch this is 10 transactions instead of 1. On SQLite WAL each commit
fsyncs the WAL; on Postgres each round-trip carries latency.

**Suggested change.** Stage all rows, then commit once:

```python
docs: list[TailoredDocument] = []
for outcome in raw_results:
    docs.append(TailoredDocument(...))
db.add_all(docs)
await db.commit()
```

If you want to broadcast progress between items, broadcast on the *flush*, not the
*commit*.

---

### DB-13 — Multi-step write isn't wrapped in `with db.begin()` (Low)

**Evidence.**

```python
# backend/applier/engine.py:264-297
try:
    ...
    db.add(app); await db.flush()
    db.add(event)
    if result.status in ("applied", "manual"):
        ...
        match.status = "applied"
    await db.commit()
except Exception as exc:
    logger.error(...); await db.rollback()
```

Functionally correct, but mixes flush/commit/rollback manually. The session is in
autobegin mode (default for AsyncSession), so a single `async with db.begin():` block
would compose better and would let Pyright check that all writes are inside the
transaction.

---

### DB-15 — Inconsistent `created_at`/`updated_at` columns (Low)

| Table              | created_at | updated_at | notes                                |
|--------------------|-----------|-----------|--------------------------------------|
| `user_profile`     | yes       | yes       | manual `_now` default; tz-naive (ST-05) |
| `search_settings`  | no        | no        | should track when settings changed   |
| `jobs`             | `scraped_at` | no    | OK conceptually                      |
| `job_matches`      | `matched_at` | no    | OK                                   |
| `job_sources`      | yes       | no        | inconsistent with `last_scraped_at`  |
| `applications`     | yes       | no        | mutations don't bump `updated_at`    |
| `application_events`| `event_date` | n/a   | immutable, OK                        |
| `tailored_documents`| yes       | no        | OK                                   |
| `browser_sessions` | no        | no        | only `last_used_at`, `expires_at`    |
| `site_credentials` | yes       | yes       | best of the bunch                    |

**Suggested change.** Add a `TimestampMixin`:

```python
class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(), onupdate=func.now(), nullable=False,
    )
```

This also resolves the ST-05 `utcnow` migration cleanly via `DateTime(timezone=True)`
+ `server_default=func.now()` (DB-side UTC), eliminating the Python-side `_now()`
helpers repeated across 4 model files.

---

### DB-16 — Analytics `/trends` does day-bucketing in Python (Medium)

**Evidence.**

```python
# backend/api/analytics.py:130-149
cutoff = datetime.utcnow() - timedelta(days=days)
stmt = select(Application.created_at).where(Application.created_at >= cutoff)
result = await db.execute(stmt)
created_dates = result.scalars().all()

day_counts: dict[str, int] = {}
for created_at in created_dates:
    day_str = created_at.strftime("%Y-%m-%d")
    day_counts[day_str] = day_counts.get(day_str, 0) + 1
```

For a year of data (`days=365`), this materialises every `created_at` value in
Python memory, then aggregates manually.

**Suggested change.**

```python
stmt = (
    select(func.date(Application.created_at).label("day"), func.count().label("c"))
    .where(Application.created_at >= cutoff)
    .group_by("day").order_by("day")
)
```

On Postgres: `func.date_trunc('day', Application.created_at)` and add a partial
index `WHERE created_at > now() - interval '90 days'` if the most common windows
are 7/30/90 days.

---

### DB-17 — `avg_match_score` averages every JobMatch ever (Low)

**Evidence.**

```python
# backend/api/analytics.py:103-106
avg_stmt = select(func.avg(JobMatch.score))
avg_result = (await db.execute(avg_stmt)).scalar_one_or_none()
```

After N months of running, this average drifts toward a meaningless long-term mean.
Cap it to the last 30 days, or expose it as a moving average. Also cache the value —
nothing about it needs millisecond freshness.

---

### DB-18 — Three `dedup_hash` formulas in three places (Medium)

**Evidence.** All three are slightly different:

```python
# backend/scraping/deduplicator.py:30-35  (used during scrape)
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower().strip())
key = f"{_norm(job.company)}|{_norm(job.title)}|{_norm(job.location)}"
return hashlib.md5(key.encode()).hexdigest()
```

```python
# backend/api/jobs.py:218-220  (used in /jobs/search)
dedup_hash = hashlib.md5(
    f"{rj.company}|{rj.title}|{rj.location}".lower().encode()
).hexdigest()
```

```python
# backend/scheduler/morning_batch.py:522-524  (used in batch insert)
dedup_hash = hashlib.md5(
    f"{jd.company}|{jd.title}|{jd.location}".lower().encode()
).hexdigest()
```

The deduplicator normalises whitespace; the other two do not. A single trailing space
in `company` flips the hash. The `Job.dedup_hash` `UNIQUE` constraint will then admit
"duplicates" that differ only by whitespace.

**Suggested change.** Extract a single `make_dedup_hash(company, title, location)`
helper in `backend/scraping/deduplicator.py` (or `models/job.py`) and call it from
all three sites.

---

### DB-22 — Singleton rows re-fetched on every request (Medium)

`UserProfile(id=1)` and `SearchSettings(id=1)` are reloaded on every apply request,
every settings GET, and every batch run:

- `backend/api/applications.py:470-472` — per-apply
- `backend/api/settings.py:170, 201, 252, 273, 407` — per-settings-API hit
- `backend/scheduler/morning_batch.py:469, 484` — per-batch (acceptable)

These are the textbook "small singleton hot row" caching candidates. A 30s in-process
TTL cache keyed by row + invalidated on write would slash sub-ms DB chatter for the
settings page (which polls).

---

### DB-24 — Missing `UNIQUE(job_match.job_id, batch_date)` (Medium)

**Evidence.**

```python
# backend/scheduler/morning_batch.py:573-595
existing_match = (
    await db.execute(
        select(JobMatch).where(
            JobMatch.job_id == job_row.id,
            JobMatch.batch_date == today,
        )
    )
).scalar_one_or_none()
if existing_match is not None:
    if score > existing_match.score:
        existing_match.score = score
    match_ids.append(existing_match.id)
else:
    match_row = JobMatch(job_id=job_row.id, score=score, batch_date=today, status="new")
    db.add(match_row)
    await db.flush()
    match_ids.append(match_row.id)
```

This is "INSERT … ON CONFLICT DO UPDATE" implemented in Python.

**Suggested change.**

```python
__table_args__ = (UniqueConstraint("job_id", "batch_date", name="uq_match_job_per_day"),)
```

Then use `sqlite_on_conflict_update` or PG `INSERT … ON CONFLICT … DO UPDATE SET score = excluded.score WHERE excluded.score > job_matches.score RETURNING id`. One round-trip per match.

---

## Postgres-native upgrades worth considering

This codebase already feels like it's outgrowing SQLite. Specific opportunities once a
PG migration lands:

### 1. Full-text search on jobs (`tsvector`)
Currently the frontend / matcher does substring search on `Job.description` in Python
(`backend/matching/matcher.py`, `backend/matching/job_skill_extractor.py`). Adding:

```sql
ALTER TABLE jobs ADD COLUMN description_tsv tsvector
    GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(company, '')), 'B') ||
        setweight(to_tsvector('english', coalesce(description, '')), 'C')
    ) STORED;
CREATE INDEX idx_jobs_tsv ON jobs USING gin (description_tsv);
```

Queue filters like "show me only jobs mentioning Python" go from full scan + Python
loop to a `WHERE description_tsv @@ plainto_tsquery('python')` GIN lookup. Cuts the
matcher's per-job cost by 1-2 orders of magnitude.

### 2. JSONB + GIN on `keyword_hits`, `fit_assessment_json`, `raw_data`
Already covered under DB-10 but worth listing as a unit. The fit-assessment payload in
particular is queried in `frontend/queue` view to colour-code rows; pushing predicate
work into PG enables filters like "show me jobs with no skill gaps in the top 3 most
critical skills".

### 3. Partial indexes for hot subsets
- `CREATE INDEX … ON job_matches (batch_date DESC, score DESC) WHERE status = 'new';`
  — supports the queue listing perfectly.
- `CREATE INDEX … ON applications (applied_at) WHERE status IN ('applied','pending');`
  — supports the daily limit guard.
- `CREATE INDEX … ON job_sources (name) WHERE enabled = true;`
  — supports `morning_batch._load_sources` (`morning_batch.py:489-490`).

### 4. Materialised view for `/api/analytics/summary`
Today the summary endpoint runs 4 queries on every dashboard load. A nightly-refreshed
materialised view `app_metrics_daily(date, total_apps, responded, avg_score)` would
make the summary card free.

### 5. `LISTEN`/`NOTIFY` for WebSocket broadcasts
`backend/api/ws.py:broadcast_status` and `broadcast_job_assessment` are in-process
right now. Once the app scales to multiple workers, PG's pub/sub becomes a zero-cost
fan-out channel, eliminating Redis.

### 6. Row-level security as a future multi-user safety net
The current data model is single-user (`UserProfile.id=1`), but every model has a
natural `user_id` extension point. Postgres RLS policies on `applications`, `documents`,
`job_matches` would make the "we hit production and forgot a `WHERE user_id` filter"
bug class structurally impossible.

### 7. SQLite-specific code paths to revisit during migration
- `PRAGMA journal_mode=WAL` listener (`backend/database.py:48-63`) — PG-irrelevant
- `nullable=Optional[dict]` with `JSON` columns map to `JSONB`
- `Date`-vs-`DateTime` comparison hack in `daily_limit.py:63` will break

---

## Already good

- **Async session per request.** `backend/database.py:115-125` correctly uses
  `async with AsyncSessionLocal() as session: yield session`. No session leaks
  detected.
- **WAL mode on SQLite.** `backend/database.py:48-63` enables WAL via a SQLAlchemy
  connect event — exactly the right hook, and the failure path is logged-and-continue.
- **Batched event fetch in `list_applications`.** `backend/api/applications.py:188-204`
  explicitly avoids N+1 with a single `IN (…)` query and a `defaultdict` group-by —
  the rest of the codebase should follow this pattern.
- **In-process duplicate-apply guard.** `backend/applier/engine.py:172-179`
  per-`job_match_id` asyncio events stop two clicks of "Apply" from racing within the
  same process (the cross-process race is DB-06).
- **Sane API pagination shape.** `skip`/`limit` with `Query(..., ge=0, le=200)` is
  consistent across `list_jobs`, `list_applications`. No unbounded `.all()` listings
  exposed externally.
- **Dedup hash uniqueness constraint exists on `Job`.** Even though the three
  computations diverge (DB-18), the UNIQUE constraint at least catches bit-identical
  duplicates at write time.
- **`from_attributes=True` ORM-to-Pydantic adapter** is used consistently; no manual
  `__dict__` shuffling.
- **`expire_on_commit=False`** is the right call for read-after-write code paths in
  async (avoids implicit re-fetch round-trips). Just needs a one-line comment in
  `database.py` to document the trade-off.
- **No soft-delete inconsistency.** The codebase uses hard `db.delete()` everywhere
  (`api/documents.py:258` is the only non-cascade delete site). Consistent and simple.
- **No raw `op.execute("INSERT …")` data migrations mixed with schema in the two
  Alembic files.** Both migrations are schema-only — when migration work resumes,
  the convention is already set.

---

## Suggested action ordering

1. **DB-02 + DB-23** — rebaseline Alembic and remove `_migrate_add_columns`. Without
   this, every other migration is risky.
2. **DB-01 + DB-24** — single Alembic revision adding all missing indexes + the
   `(job_id, batch_date)` unique constraint. Zero behaviour change, large perf win.
3. **DB-03 + DB-04 + DB-05** — collapse the three N+1 hot paths. Each is a contained
   diff in one file.
4. **DB-06** — daily-limit race. This is a correctness bug, not a perf issue.
5. **DB-07 + DB-09 + DB-08** — enums, FKs, NOT-NULLs. Schema-correctness wave; pairs
   well with the Pydantic-Literal cleanup already in standards backlog TY-05.
6. **DB-11** — add `relationship()` once FKs exist, then prune the hand-written
   `outerjoin` boilerplate across `api/`.
7. **DB-15** — `TimestampMixin` + tz-aware. Coordinates with **ST-05** in the
   standards backlog: implement them together so `_now()` deletion lands once.
8. **DB-10 / DB-16 / DB-17 / DB-22** — caching + JSON cleanup. Lower urgency, but
   compounds nicely with a future PG move.
