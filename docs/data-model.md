# Data model & migrations

> JobPilot's persistence layer: SQLAlchemy 2.0 (typed `Mapped[...]`) entities, an async SQLite engine with WAL + foreign keys, and an Alembic migration chain that is the single source of truth for the schema.

## Overview

All ORM models live under [`backend/models/`](../backend/models/) and inherit from a single
`DeclarativeBase` in [`backend/models/base.py:4`](../backend/models/base.py). Every model is
re-exported (and therefore registered on `Base.metadata`) by
[`backend/models/__init__.py`](../backend/models/__init__.py), which is what Alembic's
`target_metadata` points at ([`alembic/env.py:6,15`](../alembic/env.py)).

The engine, session factory, and startup hook live in
[`backend/database.py`](../backend/database.py). Timestamps default to `naive_utc_now`
(from `backend.utils.time`), so all `DateTime` columns store naive UTC.

The runtime database is **SQLite** (`aiosqlite` async driver). There is no Postgres code
path — see [Postgres path](#postgres-path).

## Engine, sessions & startup

[`backend/database.py`](../backend/database.py):

| Concern | Detail | Location |
|---|---|---|
| Engine | `create_async_engine("sqlite+aiosqlite:///{jobpilot_data_dir}/jobpilot.db", echo=False)` | `database.py:23` |
| PRAGMAs | On every connect: `journal_mode=WAL` and `foreign_keys=ON` (both best-effort, wrapped in try/except) | `database.py:29-39` |
| Session factory | `AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)` | `database.py:42` |
| Context manager | `db_session()` — commits on success, rolls back on exception | `database.py:105-118` |
| FastAPI dependency | `get_db()` — yields a session without auto-commit | `database.py:121-123` |

Note: SQLite enforces declared foreign keys **only** when `PRAGMA foreign_keys=ON` is set
per-connection — that is the purpose of the `connect` event listener at `database.py:29`.

### `init_db()` — Alembic as the single source of truth

`init_db()` ([`database.py:45`](../backend/database.py)) runs at app startup (lifespan). It does
**not** call `create_all`; instead it runs `alembic upgrade head` in a worker thread
(`run_in_executor`, because Alembic drives its own `asyncio.run` loop), then seeds default
job sources.

`_alembic_upgrade_head()` ([`database.py:53`](../backend/database.py)) reconciles three cases
using a short-lived **sync** engine for inspection:

1. **Fresh DB** (no app tables) — `upgrade head` builds the whole schema from base.
2. **Legacy `create_all` DB** (tables exist but no `alembic_version`) — Alembic is `stamp`ed at
   `e3a1f2b8c9d7` so only the idempotent catch-up migration (`e5a65a3427cf`) onward runs, avoiding
   `table ... already exists`.
3. **Already-managed DB** — a normal forward `upgrade head`.

It also overrides `sqlalchemy.url` so a custom `JOBPILOT_DATA_DIR` (e.g. per-worker test DBs)
migrates the correct file rather than the relative URL shipped in `alembic.ini`
(`database.py:87`).

`_seed_default_sources()` ([`database.py:126`](../backend/database.py)) populates `job_sources`
from `SITE_CONFIGS` when the table is empty (skipping the `lab_website` template).

## Entities

All entities and their tables:

| Model | Table | Key columns / notes | File |
|---|---|---|---|
| `UserProfile` | `user_profile` | Singleton (`id` default `1`); name, email, CV/letter base paths, `additional_info` (JSON), `last_dashboard_seen_at` | [`user.py:13`](../backend/models/user.py) |
| `SearchSettings` | `search_settings` | Singleton (`id` default `1`); keywords/locations/etc (JSON), `daily_limit`, `min_match_score`, `cv_modification_sensitivity` (CHECK: conservative/balanced/aggressive), `cv_tailoring_enabled`, `max_job_age_days` | [`user.py:32`](../backend/models/user.py) |
| `SiteCredential` | `site_credentials` | `site_name` unique; Fernet-encrypted email/password | [`user.py:70`](../backend/models/user.py) |
| `JobSource` | `job_sources` | `name` unique (`uq_job_sources_name`); `type`, `url`, `config` (JSON), `prompt_template`, `enabled` | [`job.py:26`](../backend/models/job.py) |
| `Job` | `jobs` | Scraped posting; `dedup_hash` unique; FK `source_id → job_sources.id` (SET NULL); salary/description/`requirements`/`benefits` (JSON), `raw_data` (JSON) | [`job.py:49`](../backend/models/job.py) |
| `JobMatch` | `job_matches` | FK `job_id → jobs.id` (CASCADE); `score`, `status` (CHECK), `batch_date`, `keyword_hits`/`fit_assessment_json` (JSON), `gap_severity`, `ats_score` | [`job.py:77`](../backend/models/job.py) |
| `TailoredDocument` | `tailored_documents` | FK `job_match_id → job_matches.id` (CASCADE); `doc_type` (CHECK: cv/letter), `tex_path`, `pdf_path`, `diff_json`, `llm_prompt`/`llm_response` | [`document.py:22`](../backend/models/document.py) |
| `Application` | `applications` | FK `job_match_id → job_matches.id` (SET NULL); `method` (CHECK), `status` (CHECK), `applied_at`, `notes`, `error_log`, `last_correspondence_at` | [`application.py:21`](../backend/models/application.py) |
| `ApplicationEvent` | `application_events` | FK `application_id → applications.id` (CASCADE); `event_type`, `details`, `event_date` | [`application.py:61`](../backend/models/application.py) |
| `GmailCredential` | `gmail_credentials` | `email_address` unique; Fernet-encrypted refresh token; `history_id` sync cursor; access tokens never persisted | [`gmail.py:24`](../backend/models/gmail.py) |
| `GmailMessage` | `gmail_messages` | `gmail_message_id` unique; cached metadata (no body); `category` (CHECK), `category_confidence`; ATS/extraction fields are nullable and currently unpopulated | [`gmail.py:46`](../backend/models/gmail.py) |
| `ApplicationCorrespondence` | `application_correspondence` | Association: FK `application_id → applications.id` (CASCADE) + `message_id → gmail_messages.id` (CASCADE); `direction` (CHECK), `link_confidence`, `link_method`, `confirmed_by_user` | [`gmail.py:88`](../backend/models/gmail.py) |
| `BrowserSession` | `browser_sessions` | `site_name` unique; `storage_state_path`, `expires_at` (Playwright auth state) | [`session.py:12`](../backend/models/session.py) |

### Relationships & foreign keys

The core pipeline chains by foreign key (no `relationship()` declarations — joins are explicit
in queries):

```
job_sources ──(SET NULL)──┐
                          ▼
                        jobs ──(CASCADE)──► job_matches ──(CASCADE)──► tailored_documents
                                                  │
                                          (SET NULL)
                                                  ▼
                                            applications ──(CASCADE)──► application_events
                                                  │
                                          (CASCADE)
                                                  ▼
                                  application_correspondence ◄──(CASCADE)── gmail_messages
```

Cascade rationale:

- **`jobs → job_matches → tailored_documents`** use `ondelete="CASCADE"`: deleting a job (or
  match) removes its dependent matches/documents.
- **`job_matches → applications`** uses `ondelete="SET NULL"`: an application survives if its
  source match is purged. A CHECK constraint
  (`ck_applications_job_match_required`, [`application.py:36`](../backend/models/application.py))
  enforces that only `method = 'manual'` rows may have a NULL `job_match_id`.
- **`applications → application_events`** and **`application_correspondence`** CASCADE: deleting an
  application removes its event log and correspondence links.

### Constraints & indexes worth noting

- **CHECK enums** are declared on the models (e.g. `applications.status`/`method`,
  `job_matches.status`, `tailored_documents.doc_type`, `gmail_messages.category`,
  `application_correspondence.direction`) and added by migration `t2b1_enum_checks`.
- **Unique natural keys**: `jobs.dedup_hash`, `job_sources.name`
  (`uq_job_sources_name`), `(job_id, batch_date)` on `job_matches`
  (`uq_job_matches_job_id_batch_date` — one match per job per batch day; SQLite treats multiple
  NULL `batch_date` rows as distinct), `gmail_credentials.email_address`,
  `gmail_messages.gmail_message_id`. Added by `t2b2_unique_keys`.
- **Covering indexes** for hot list/queue paths: `ix_applications_status_created`,
  `ix_job_matches_status_batch_date_score`, `ix_tailored_documents_match_doc_created`,
  `ix_application_events_application_id_event_date`,
  `ix_application_correspondence_app_created`. Added by `t2b4_indexes`.

The `daily_limit` reservation (`reserve_slot`,
[`backend/applier/daily_limit.py:98`](../backend/applier/daily_limit.py)) inserts a `pending`
`Application` and immediately recounts under SQLite's RESERVED write lock to close the TOCTOU
race against the per-day limit.

## Migration chain

Migrations live in [`alembic/versions/`](../alembic/versions/). Alembic runs **online** via an
async engine (`async_engine_from_config` + `NullPool`,
[`alembic/env.py:31-39`](../alembic/env.py)). Linear chain, oldest → newest:

| Revision | Summary | down_revision |
|---|---|---|
| `071b973b48b2` | Initial schema | — (base) |
| `df6eea4756c3` | Add `site_credentials` table | `071b973b48b2` |
| `41441908fc29` | Add initial indexes | `df6eea4756c3` |
| `e3a1f2b8c9d7` | Add `last_dashboard_seen_at` to `user_profile` (legacy-stamp point) | `41441908fc29` |
| `e5a65a3427cf` | Schema catch-up + FKs; creates the Gmail tables (`gmail_credentials`, `gmail_messages`, `application_correspondence`) | `e3a1f2b8c9d7` |
| `t2b1_enum_checks` | Enum CHECK constraints + legacy `status` migration | `e5a65a3427cf` |
| `t2b2_unique_keys` | Unique constraints on natural keys + duplicate collapse | `t2b1_enum_checks` |
| `t2b3_not_null` | NOT NULL + conditional CHECK on always-set columns | `t2b2_unique_keys` |
| `t2b4_indexes` | Covering indexes for hot query paths | `t2b3_not_null` |

`t2b4_indexes` is the current **head**. `e3a1f2b8c9d7` is significant: it is the stamp point used
by `_alembic_upgrade_head()` for legacy `create_all` databases, so they only run the idempotent
catch-up migration (`e5a65a3427cf`) onward.

> Note: `alembic/env.py` does not enable `render_as_batch`, so destructive table alterations in
> the catch-up and constraint migrations (`e5a65a3427cf` through `t2b4_indexes`) rely on explicit
> batch operations within each migration file rather than Alembic auto-batching.

## Postgres path

There is **no Postgres code path** — both [`backend/database.py:23`](../backend/database.py)
and `alembic.ini`'s URL are SQLite (`sqlite+aiosqlite`). The only Postgres reference is a
commented-out `postgres:16-alpine` service (plus a `DATABASE_URL` env var to point at it) in
[`docker-compose.yml`](../docker-compose.yml). Switching to Postgres would require swapping the
engine URL, the Alembic URL, and reviewing SQLite-specific behaviours (WAL pragma, NULL-distinct
unique keys, the daily-limit write-lock assumptions, and JSON columns that would become `JSONB`).

## Related

- [README](../README.md)
- [Configuration & environment](./configuration.md)
- [Backend architecture](./architecture.md)
- Job pipeline: [scraping](./scraping.md) → [matching](./matching.md) → [applying](./applying.md)
- [Gmail integration](./gmail.md)
