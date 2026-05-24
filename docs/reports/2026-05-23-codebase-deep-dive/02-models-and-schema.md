# JobPilot — Data Model & Schema Deep Dive

> Scope: `backend/models/`, `backend/database.py` (schema/migration side), `alembic.ini` + `alembic/`,
> and `backend/defaults.py` only where it touches seeding.
> Date: 2026-05-23

---

## 1. Purpose

JobPilot persists everything it needs to drive a single-user, local-first job-search agent
in one SQLite database (`data/jobpilot.db`) accessed via SQLAlchemy 2.0 async. The data model
covers six functional clusters: **the user** (`UserProfile`, `SearchSettings`, `SiteCredential`),
**the job pipeline** (`JobSource → Job → JobMatch → TailoredDocument → Application →
ApplicationEvent`), **browser automation state** (`BrowserSession`), and the **Gmail
correspondence layer** (`GmailCredential`, `GmailMessage`, `ApplicationCorrespondence`) added in
the `gm-*` series. Schema management is dual-track: alembic for tracked DDL, plus a defensive
`_migrate_add_columns()` shim in `database.py` to patch already-shipped SQLite files in place.

---

## 2. ORM conventions

Every model file is consistent on the basics; small things are not.

- **SQLAlchemy 2.0 typed syntax** with `Mapped[...]` annotations and `mapped_column(...)` —
  no legacy `Column(...)` declarations remain. ([models/base.py:1](backend/models/base.py#L1))
- **`from __future__ import annotations`** at the top of every model file except
  [models/base.py:1](backend/models/base.py#L1) and [models/session.py:1](backend/models/session.py#L1)
  (session was written before the convention; base doesn't need annotations).
- **Naive UTC timestamps everywhere**: each module re-defines its own
  `_now()` returning `datetime.now(timezone.utc).replace(tzinfo=None)`. The
  docstring (copy-pasted four times) explains: legacy DB rows were stored with
  `datetime.utcnow()`, so to keep `<` / `>` comparisons working the new code
  also strips tzinfo. See
  [models/user.py:12](backend/models/user.py#L12),
  [models/job.py:12](backend/models/job.py#L12),
  [models/application.py:12](backend/models/application.py#L12),
  [models/document.py:12](backend/models/document.py#L12),
  [models/gmail.py:22](backend/models/gmail.py#L22).
- **Base class**: trivial — `class Base(DeclarativeBase): pass` at
  [models/base.py:4](backend/models/base.py#L4). No mixins, no `__abstract__` parent
  for `id`/`created_at`/`updated_at`.
- **No `relationship()` declarations anywhere**. Joins are written as explicit
  `select(...).join(Foo, Foo.x == Bar.y)` in repository code. The schema
  models columns; the ORM graph is barely used.
- **FK declarations are inconsistent**: only `ApplicationCorrespondence` declares actual
  `ForeignKey(... ondelete="CASCADE")` constraints
  ([models/gmail.py:98](backend/models/gmail.py#L98),
  [models/gmail.py:102](backend/models/gmail.py#L102)).
  Every other "FK" — `Job.source_id`, `JobMatch.job_id`, `Application.job_match_id`,
  `TailoredDocument.job_match_id`, `ApplicationEvent.application_id` — is a bare
  `Mapped[int]` without a `ForeignKey(...)` annotation. SQLite enforces nothing.
- **Indexes** are mostly declared inline via `index=True`; compound indexes use the
  `__table_args__ = (Index(...),)` form. Naming convention: `ix_<table>_<col1>[_<col2>]`.
- **`__future__.annotations`** means the `Mapped[Optional[dict]]` annotations are strings at
  runtime, so SQLAlchemy still works because the column type is supplied positionally to
  `mapped_column(JSON, ...)`.

---

## 3. Entity catalog

### UserProfile — `user_profile`
Singleton table for the local user (always `id = 1`).
([models/user.py:18](backend/models/user.py#L18))

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| id | Integer PK | no | 1 | not autoincrement — fixed at 1 |
| full_name | String | no | — | |
| email | String | no | — | not unique, not indexed |
| phone | String | yes | | |
| location | String | yes | | |
| linkedin_url | String | yes | | |
| driver_license | String | yes | | |
| mobility | String | yes | | |
| base_cv_path | String | yes | | relative to data dir |
| base_letter_path | String | yes | | |
| additional_info | JSON | yes | | free-form blob |
| last_dashboard_seen_at | DateTime | yes | | drives Today "new since last visit" |
| created_at | DateTime | no | `_now` | |
| updated_at | DateTime | no | `_now` | application code must update manually — no `onupdate=` |

Invariants: row count effectively == 1 (enforced by `id=1` lookups in app code, not the schema).

### SearchSettings — `search_settings`
Also a singleton (`id = 1`).
([models/user.py:37](backend/models/user.py#L37))

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| id | Integer PK | no | 1 | |
| keywords | JSON | no | — | required dict (must include at least the top-level list) |
| excluded_keywords | JSON | yes | | |
| locations | JSON | yes | | |
| salary_min | Integer | yes | | |
| experience_min | Integer | yes | | |
| experience_max | Integer | yes | | |
| remote_only | Boolean | no | False | |
| job_types | JSON | yes | | |
| languages | JSON | yes | | |
| excluded_companies | JSON | yes | | |
| daily_limit | Integer | no | 10 | mirrors `defaults.DAILY_LIMIT` |
| min_match_score | Float | no | 30.0 | mirrors `defaults.MIN_MATCH_SCORE` |
| countries | JSON | yes | | |
| cv_modification_sensitivity | String | no | "balanced" | |
| cv_tailoring_enabled | Boolean | no | True | also `server_default="1"` |
| max_results_per_source | Integer | no | 20 | `server_default="20"` |
| max_job_age_days | Integer | yes | NULL | |

The dead column `batch_time: String, nullable=False` exists in the initial alembic migration
but has been removed from the model — see §9.

### SiteCredential — `site_credentials`
([models/user.py:68](backend/models/user.py#L68))

| Column | Type | Null | Notes |
|---|---|---|---|
| id | Integer PK | no | autoincrement |
| site_name | String | no | **unique** |
| encrypted_email | String | yes | Fernet-encrypted under `CREDENTIAL_KEY` |
| encrypted_password | String | yes | same |
| created_at | DateTime | no | `_now` |
| updated_at | DateTime | no | `_now` (no `onupdate`) |

### JobSource — `job_sources`
([models/job.py:18](backend/models/job.py#L18))

| Column | Type | Null | Default | Indexes |
|---|---|---|---|---|
| id | Integer PK autoinc | no | | |
| name | String | no | | `ix_job_sources_name` |
| type | String | no | | `ix_job_sources_type` |
| url | String | yes | | |
| config | JSON | yes | | |
| prompt_template | Text | yes | | per-site LLM prompt override |
| enabled | Boolean | implicit no | True | `ix_job_sources_enabled` |
| last_scraped_at | DateTime | yes | | |
| created_at | DateTime | implicit no | `_now` | |

Note: `name` is *not* unique despite being looked up by name — see critique.

### Job — `jobs`
([models/job.py:32](backend/models/job.py#L32))

| Column | Type | Null | Default | Indexes |
|---|---|---|---|---|
| id | Integer PK autoinc | no | | |
| source_id | Integer | yes | | `ix_jobs_source_id` (logical FK to `job_sources.id`, unconstrained) |
| external_id | String | yes | | |
| title | String | no | | |
| company | String | no | | |
| location | String | yes | | |
| country | String | yes | | |
| salary_text | String | yes | | |
| salary_min | Integer | yes | | |
| salary_max | Integer | yes | | |
| description | Text | yes | | |
| requirements | JSON | yes | | list of strings shoved into JSON |
| benefits | JSON | yes | | same |
| url | String | no | | |
| apply_url | String | yes | | |
| apply_method | String | yes | | enum-by-convention: `"easy_apply"`, `"external"`, `"email"`, ... |
| posted_at | DateTime | yes | | |
| scraped_at | DateTime | no | `_now` | `ix_jobs_scraped_at` |
| dedup_hash | String | yes | | **unique** (the only real dedup invariant) |
| raw_data | JSON | yes | | original payload from scraper for forensics |

### JobMatch — `job_matches`
([models/job.py:57](backend/models/job.py#L57))

| Column | Type | Null | Default | Indexes |
|---|---|---|---|---|
| id | Integer PK autoinc | no | | |
| job_id | Integer | no | | compound: `ix_job_matches_job_id_matched_at`, `ix_job_matches_job_id_batch_date` |
| score | Float | no | | 0–100 |
| keyword_hits | JSON | yes | | which keywords matched |
| status | String | implicit no | "new" | `ix_job_matches_status` — enum-by-string `new / applied / skipped / ...` |
| batch_date | Date | yes | | morning-batch dedup key |
| matched_at | DateTime | implicit no | `_now` | |
| gap_severity | Float | yes | | ATS gap engine output |
| ats_score | Float | yes | | |
| fit_assessment_json | JSON | yes | | structured LLM fit assessment |

### TailoredDocument — `tailored_documents`
([models/document.py:18](backend/models/document.py#L18))

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| id | Integer PK autoinc | no | | |
| job_match_id | Integer | yes | | `index=True` — logical FK to `job_matches.id` |
| doc_type | String | no | | "cv" or "letter" |
| tex_path | String | yes | | |
| pdf_path | String | yes | | |
| diff_json | JSON | yes | | structured edit diff vs base |
| llm_prompt | Text | yes | | |
| llm_response | Text | yes | | |
| created_at | DateTime | implicit no | `_now` | `index=True` |

### Application — `applications`
([models/application.py:18](backend/models/application.py#L18))

| Column | Type | Null | Default | Indexes |
|---|---|---|---|---|
| id | Integer PK autoinc | no | | |
| job_match_id | Integer | yes | | `index=True` — logical FK to `job_matches.id` |
| method | String | no | | enum-by-convention: `"easy_apply"`, `"external"`, `"email"`, ... |
| status | String | no | "pending" | `index=True` |
| applied_at | DateTime | yes | | `index=True` — used for the daily-limit window |
| notes | String | yes | | |
| error_log | Text | yes | | last error trace from applier |
| created_at | DateTime | no | `_now` | `index=True` |
| last_correspondence_at | DateTime | yes | | added by the lightweight migrator, populated by Gmail link writer |

### ApplicationEvent — `application_events`
([models/application.py:34](backend/models/application.py#L34))

| Column | Type | Null | Default | Indexes |
|---|---|---|---|---|
| id | Integer PK autoinc | no | | |
| application_id | Integer | no | | compound `ix_application_events_application_id_event_date` |
| event_type | String | no | | enum-by-convention |
| details | String | yes | | |
| event_date | DateTime | implicit no | `_now` | |

### BrowserSession — `browser_sessions`
([models/session.py:12](backend/models/session.py#L12))

| Column | Type | Null | Notes |
|---|---|---|---|
| id | Integer PK autoinc | no | |
| site_name | String | no | **unique** |
| storage_state_path | String | yes | path to Playwright `storage_state.json` |
| last_used_at | DateTime | yes | |
| expires_at | DateTime | yes | |

This is the only model file that does **not** define a `_now()` helper or set any default —
all timestamps are written explicitly by the caller.

### GmailCredential — `gmail_credentials`
([models/gmail.py:28](backend/models/gmail.py#L28))

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| id | Integer PK autoinc | no | | |
| email_address | String | no | | **unique** — multi-account-ready key |
| encrypted_refresh_token | Text | no | | Fernet under `CREDENTIAL_KEY` |
| scopes | String | no | | space-separated |
| history_id | String | yes | | Gmail historyId sync cursor |
| enabled | Boolean | no | True | |
| last_synced_at | DateTime | yes | | |
| created_at | DateTime | no | `_now` | |
| updated_at | DateTime | no | `_now` | manual update (no `onupdate=`) |

Invariant the schema enforces: at most one credential per `email_address`. Access tokens are
never persisted — held in-memory by `GmailTokenManager` per the docstring.

### GmailMessage — `gmail_messages`
([models/gmail.py:50](backend/models/gmail.py#L50))

| Column | Type | Null | Default | Indexes |
|---|---|---|---|---|
| id | Integer PK autoinc | no | | |
| gmail_message_id | String | no | | **unique** |
| gmail_thread_id | String | no | | `index=True` |
| account_email | String | no | | (compound with `received_at`) |
| from_address | String | no | | |
| from_domain | String | no | | `index=True` — denormalized from `from_address` |
| to_address | String | yes | | |
| subject | String | yes | | |
| snippet | Text | yes | | |
| received_at | DateTime | no | | `index=True`; compound `ix_gmail_messages_account_received` |
| category | String | yes | | `index=True` — Phase 1 heuristic label |
| category_confidence | Float | yes | | |
| classified_by | String | yes | | `"heuristic"` / `"llm"` |
| ats_vendor | String | yes | | inferred ATS (Greenhouse, Lever, ...) |
| extracted_company | String | yes | | Phase 2 — NULL today |
| extracted_role | String | yes | | Phase 2 |
| extracted_interview_at | DateTime | yes | | Phase 2 |
| extracted_salary_text | String | yes | | Phase 2 |
| extracted_questions_json | JSON | yes | | Phase 2 |
| created_at | DateTime | no | `_now` | |

Bodies are not persisted — by design (privacy).

### ApplicationCorrespondence — `application_correspondence`
Association object connecting `Application ↔ GmailMessage` with link-quality metadata.
([models/gmail.py:87](backend/models/gmail.py#L87))

| Column | Type | Null | Default | Indexes / FK |
|---|---|---|---|---|
| id | Integer PK autoinc | no | | |
| application_id | Integer | no | | `FK applications.id ON DELETE CASCADE`, `index=True` |
| message_id | Integer | no | | `FK gmail_messages.id ON DELETE CASCADE`, `index=True` |
| gmail_thread_id | String | no | | `index=True` |
| direction | String | no | | `"inbound"` / `"outbound"` (enum-by-string) |
| link_confidence | Float | no | | 0–1 |
| link_method | String | no | | how the link was inferred |
| confirmed_by_user | Boolean | no | False | |
| created_at | DateTime | no | `_now` | compound `ix_application_correspondence_app_created` |

This is the **only** table in the whole schema with real `ForeignKey(... ondelete=...)`
constraints. SQLite enforces them only when `PRAGMA foreign_keys = ON` is set on the
connection — which is **not** done in `database.py`.

### Pydantic shapes — `models/schemas.py`
[models/schemas.py:9](backend/models/schemas.py#L9), [models/schemas.py:31](backend/models/schemas.py#L31)

Not ORM models — Pydantic v2 DTOs used by the scrapers (`RawJob`) and matcher (`JobDetails`).
`RawJob.description` is capped at 5,000 chars here but `defaults.MAX_LEN_DESCRIPTION = 20_000`
([defaults.py:11](backend/defaults.py#L11)) — see critique.

---

## 4. Relationship diagram

Logical FKs are dashed; declared FKs (with `ON DELETE CASCADE`) are solid.

```
                            ┌─────────────────────┐
                            │   UserProfile (id=1)│  singleton
                            └─────────────────────┘
                            ┌─────────────────────┐
                            │ SearchSettings(id=1)│  singleton
                            └─────────────────────┘
                            ┌─────────────────────┐
                            │   SiteCredential    │  per site_name (unique)
                            └─────────────────────┘
                            ┌─────────────────────┐
                            │   BrowserSession    │  per site_name (unique)
                            └─────────────────────┘

  JobSource ──┐ (logical: jobs.source_id, unconstrained)
              ▼
            Job  ──┐ (logical: job_matches.job_id, unconstrained)
                   ▼
                JobMatch ──┬─────────────────────────────────────┐
                           │ (logical)                          │ (logical)
                           ▼                                    ▼
                  TailoredDocument                         Application
                                                               │
                                                               │ (logical: application_events.application_id)
                                                               ▼
                                                       ApplicationEvent

                                                               ▲
                            ┌──────────────────────────────────┤   ON DELETE CASCADE
                            │                                  │
                  GmailCredential                              │
                  (per email_address, unique)                  │
                            │                                  │
                            │ (NO FK to gmail_messages)        │
                            ▼                                  │
                    GmailMessage ◀──────────────────────┐      │
                    (per gmail_message_id, unique)      │      │
                                                        │      │
                                                        │ ON DELETE CASCADE
                                                        │      │
                                            ApplicationCorrespondence
                                            (M-to-M: Application ↔ GmailMessage)
```

Single-headed solid arrows below indicate declared FKs; everything else is structural-
only:

```
applications  ◀──CASCADE──  application_correspondence  ──CASCADE──▶  gmail_messages
```

---

## 5. Migrations — dual track

Two mechanisms run on every backend boot, in this order, **both happening unconditionally**:

### 5a. Alembic (offline tracked DDL)
- Config at [alembic.ini:8](alembic.ini#L8) points `script_location` at `alembic/`,
  default DB URL `sqlite+aiosqlite:///data/jobpilot.db` at [alembic.ini:89](alembic.ini#L89).
- `alembic/env.py` is async-aware; it builds an `async_engine_from_config` with
  `pool.NullPool`, runs migrations through `await connectable.connect()`.
  ([alembic/env.py:28](alembic/env.py#L28))
- Four revisions exist as of 2026-05-23 in `alembic/versions/`:
  1. `071b973b48b2_initial_schema.py` — creates the original 9 tables. Includes a now-removed
     `search_settings.batch_time` column (String, NOT NULL) that is no longer in the model.
     ([alembic/versions/071b973b48b2_initial_schema.py:110](alembic/versions/071b973b48b2_initial_schema.py#L110))
  2. `df6eea4756c3_add_site_credentials_table.py` — creates `site_credentials`.
  3. `41441908fc29_add_initial_indexes.py` — bulk-creates 13 non-PK indexes
     to retrofit query plans.
  4. `e3a1f2b8c9d7_add_last_dashboard_seen_at_to_userprofile.py` — adds the Today-dashboard
     "new since last visit" column.

**Note**: there is **no alembic revision** for the Gmail tables (`gmail_credentials`,
`gmail_messages`, `application_correspondence`), nor for the Phase-2 columns on `job_matches`
(`gap_severity`, `ats_score`, `fit_assessment_json`), nor for `country`/`scraped_at` on jobs,
nor for `cv_modification_sensitivity`, etc. These tables and columns exist in the live SQLite
purely because `init_db()` calls `Base.metadata.create_all` ([database.py:43](backend/database.py#L43)),
which is idempotent for tables but does **nothing** for new columns on existing tables.

### 5b. Lightweight in-process migrator — `_migrate_add_columns`
[database.py:72](backend/database.py#L72)

```python
migrations = [
    ("search_settings", "cv_tailoring_enabled", "BOOLEAN NOT NULL DEFAULT 1"),
    ("search_settings", "max_results_per_source", "INTEGER NOT NULL DEFAULT 20"),
    ("search_settings", "max_job_age_days", "INTEGER"),
    ("applications", "last_correspondence_at", "DATETIME"),
]
```

For each entry, it does `PRAGMA table_info(<table>)` and only emits `ALTER TABLE … ADD COLUMN`
if the column is missing. It catches all exceptions and only logs at `debug` level
([database.py:93](backend/database.py#L93)), so failures are silent.

### Dual-track behavior
`init_db()` (called from FastAPI startup at [main.py:94](backend/main.py#L94)) **always** runs
`Base.metadata.create_all` followed by the column-add shim — it never invokes alembic.
Alembic is therefore a developer/CI-only artifact; production users get the schema from
`create_all` + the shim. The two tracks have drifted: every model added since the initial
schema (Gmail, fit-engine columns, country, etc.) was tracked only by `create_all`. When the
shim handles new columns (the four entries above), there is no corresponding alembic migration
to register the change in `alembic_version`.

---

## 6. Indexes

Non-PK indexes actually defined (after migration `41441908fc29` and the inline `index=True`
flags on the Gmail tables):

| Index | Table | Columns | Purpose |
|---|---|---|---|
| `ix_job_sources_name` | job_sources | name | filter/lookup by site key |
| `ix_job_sources_type` | job_sources | type | filter by scraper kind |
| `ix_job_sources_enabled` | job_sources | enabled | batch-runner pre-filter |
| `ix_jobs_source_id` | jobs | source_id | join to JobSource |
| `ix_jobs_scraped_at` | jobs | scraped_at | default `ORDER BY scraped_at DESC` |
| unique | jobs | dedup_hash | dedup invariant |
| `ix_job_matches_status` | job_matches | status | queue filter `WHERE status='new'` |
| `ix_job_matches_job_id_matched_at` | job_matches | (job_id, matched_at) | per-job recent matches |
| `ix_job_matches_job_id_batch_date` | job_matches | (job_id, batch_date) | morning-batch dedup |
| `ix_applications_job_match_id` | applications | job_match_id | join to JobMatch |
| `ix_applications_status` | applications | status | filter pending/applied |
| `ix_applications_created_at` | applications | created_at | timeline sort |
| `ix_applications_applied_at` | applications | applied_at | daily-limit window query |
| `ix_application_events_application_id_event_date` | application_events | (application_id, event_date) | per-application timeline |
| `ix_tailored_documents_job_match_id` | tailored_documents | job_match_id | docs-for-match lookup |
| `ix_tailored_documents_created_at` | tailored_documents | created_at | recent docs list |
| unique | site_credentials | site_name | one row per site |
| unique | browser_sessions | site_name | one row per site |
| unique | gmail_credentials | email_address | one row per inbox |
| unique | gmail_messages | gmail_message_id | dedup by Gmail msg id |
| inline | gmail_messages | gmail_thread_id | thread lookups |
| inline | gmail_messages | from_domain | "all messages from greenhouse.io" |
| inline | gmail_messages | received_at | sort by recency |
| inline | gmail_messages | category | filter inbound rejections etc. |
| `ix_gmail_messages_account_received` | gmail_messages | (account_email, received_at) | per-account sync cursor |
| inline | application_correspondence | application_id | FK index |
| inline | application_correspondence | message_id | FK index |
| inline | application_correspondence | gmail_thread_id | thread-based linker |
| `ix_application_correspondence_app_created` | application_correspondence | (application_id, created_at) | timeline view |

---

## 7. Default seeding

Seeding happens in two places. There is **no** generic "seed defaults" pipeline.

1. **`_seed_default_sources()`** in [database.py:97](backend/database.py#L97):
   runs after `_migrate_add_columns`, checks if `job_sources` is empty, and if so iterates
   `SITE_CONFIGS` from `backend.scraping.site_prompts` to insert one `JobSource` per site
   (skipping `"lab_website"`). Every seeded row has `enabled=True`, `config={}`. Failures are
   logged at `error` level and swallowed.

2. **`backend/defaults.py`** ([defaults.py:1](backend/defaults.py#L1)) is a constants module,
   not a DB-seeder. The values relevant to seeding are:
   - `DAILY_LIMIT = 10` — matches `SearchSettings.daily_limit` Python-side default
     ([defaults.py:21](backend/defaults.py#L21), [models/user.py:51](backend/models/user.py#L51)).
   - `MIN_MATCH_SCORE = 30.0` — matches `SearchSettings.min_match_score` default
     ([defaults.py:22](backend/defaults.py#L22), [models/user.py:52](backend/models/user.py#L52)).
   - The `MAX_LEN_*` constants (`MAX_LEN_TITLE`, `MAX_LEN_COMPANY`, etc.) **are not enforced
     in the schema** — they're advisory caps used by scrapers/pipelines, while the DB columns
     are unbounded `String`/`Text`.

`UserProfile` and `SearchSettings` are **not seeded at boot**. They are created on demand by
the settings API: e.g. [api/settings.py:441](backend/api/settings.py#L441) does
`UserProfile(id=1, full_name="", email="", base_cv_path=relative_path)` when a CV upload
arrives and no row exists yet. That means an unconfigured fresh DB has zero `user_profile`
rows, and any code path that does `select(UserProfile).where(id == 1).scalar_one()` will
blow up with `NoResultFound`.

---

## 8. Type-vs-DB mismatches

- **Optional fields**: `Mapped[Optional[X]]` correctly pairs with `nullable=True` throughout —
  no instances of `Optional[X]` with `nullable=False` were found.
- **Booleans without explicit nullable**: `JobSource.enabled`, `JobMatch.status`,
  `JobMatch.matched_at`, `TailoredDocument.created_at`, `Application.status`,
  `Application.created_at` etc. all have `default=...` but no `nullable=False` — SQLAlchemy
  defaults to NOT NULL for non-Optional `Mapped[T]`, but it's inconsistent stylistically.
- **JSON columns typed as `Optional[dict]` containing lists**: e.g. `Job.requirements`,
  `Job.benefits`, `SearchSettings.keywords`, `SearchSettings.locations`. The data model is
  actually `list[str]` (see `RawJob.requirements: list[str]` at
  [models/schemas.py:20](backend/models/schemas.py#L20)) — but the ORM annotates `dict`.
  Reads work because JSON deserializes either; the type lies.
- **Naive UTC vs timezone-aware**: every `DateTime` column is declared `DateTime` (i.e.
  `DateTime(timezone=False)`) and the `_now()` helpers strip tzinfo before insert. The model
  is internally consistent, but anything that flows through Pydantic (the API layer, the
  Gmail webhook) is timezone-aware on entry and must be normalized to naive UTC before
  comparison — bugs here are easy and not statically caught.
- **`UserProfile.id`** is declared `Mapped[int]` with `default=1` and *no* `autoincrement=…`
  flag. Because SQLAlchemy infers autoincrement for any Integer PK, this currently relies on
  callers always passing `id=1` explicitly.
- **`schemas.RawJob.description` capped at 5,000**, vs `defaults.MAX_LEN_DESCRIPTION = 20_000`.
  The Job.description Text column has no cap. Three different sources of truth for the same
  invariant.

---

## 9. Critique (severity-tagged)

**HIGH — No FK constraints almost anywhere.**
Only `application_correspondence` has declared `ForeignKey(...)` columns
([models/gmail.py:97-104](backend/models/gmail.py#L97)). Every other "FK"
(`Job.source_id`, `JobMatch.job_id`, `Application.job_match_id`,
`TailoredDocument.job_match_id`, `ApplicationEvent.application_id`,
`ApplicationCorrespondence.gmail_thread_id → gmail_messages.gmail_thread_id`) is a bare
`Mapped[int]`. Even if the columns had `ForeignKey(...)`, SQLite would not enforce them
without `PRAGMA foreign_keys = ON`, which is **not** set in
[database.py:28-34](backend/database.py#L28) (only `journal_mode=WAL` is). Net effect:
orphaned rows are possible and silent; deleting a Job leaves dangling `JobMatch` rows.

**HIGH — Dual-track migration drift.**
`init_db()` always calls `Base.metadata.create_all` + the column-add shim, never alembic
([database.py:40-46](backend/database.py#L40)). Alembic exists but has no migration for the
Gmail tables (added in gm-* commits), the JobMatch ATS-gap columns (`gap_severity`,
`ats_score`, `fit_assessment_json`), or several other shipped columns. Anyone running
`alembic upgrade head` on a fresh DB gets a different schema from anyone running the live
app. The shim's exception handler swallows failures at `logger.debug`
([database.py:93-94](backend/database.py#L93)) — silent corruption is possible.

**HIGH — `last_correspondence_at` migration is not reversible.**
The shim adds `applications.last_correspondence_at` ([database.py:80](backend/database.py#L80)),
but no alembic migration mirrors it. There is no `downgrade()` and no way to roll back
locally short of dropping the column manually. SQLite's `ALTER TABLE … DROP COLUMN` was only
added in SQLite 3.35+ and is not used here.

**HIGH — `UserProfile` is created ad hoc inside an upload handler.**
[api/settings.py:441](backend/api/settings.py#L441) is the only place that ever inserts the
singleton, with `full_name=""`/`email=""`. Any other code path that reads it before that
upload happens will hit `NoResultFound`. Should be seeded at boot, or the singleton invariant
should be expressed (e.g. `CheckConstraint("id = 1")`).

**MEDIUM — Dead column `search_settings.batch_time`.**
The initial alembic migration creates it as `String NOT NULL`
([alembic/versions/071b973b48b2_initial_schema.py:110](alembic/versions/071b973b48b2_initial_schema.py#L110)),
but the model
([models/user.py:37](backend/models/user.py#L37)) no longer declares it. Old DBs still carry
the column populated; new alembic-built DBs still create it. No migration drops it. No code
references it (verified by grep — zero hits in backend/).

**MEDIUM — String-enum columns with no `CheckConstraint`.**
`JobMatch.status` (new/applied/skipped/…), `Application.status` (pending/applied/…),
`Application.method`, `ApplicationCorrespondence.direction` (inbound/outbound),
`GmailMessage.category`, `TailoredDocument.doc_type` — none use a `sa.Enum(...)` or a check
constraint. A typo writes a row the queue layer will silently ignore. This violates "express
invariants in types".

**MEDIUM — JSON-blob fields that should be normalized.**
`Job.requirements` and `Job.benefits` are JSON lists of strings; `JobMatch.keyword_hits`
is a JSON map of keyword → count; `SearchSettings.keywords`, `excluded_keywords`,
`locations`, `job_types`, `languages`, `excluded_companies`, `countries` are JSON lists.
A `Keyword` table and a `JobRequirement` table would let the matcher run set/intersect
queries in SQL instead of in Python after a full scan, and would make per-keyword stats
trivial. The current shape forces app-side iteration over every match row.

**MEDIUM — `JobSource.name` is not unique.**
`_seed_default_sources` only seeds when the table is empty
([database.py:106](backend/database.py#L106)), but if anyone inserts the same `name` twice
the schema accepts it. Combined with `index=True` (which is non-unique) you get duplicate
"linkedin" rows that downstream code resolves nondeterministically.

**MEDIUM — Naive UTC timestamps everywhere, copy-pasted `_now()` helper.**
The same five-line `_now()` function appears in five model files
([models/user.py:12](backend/models/user.py#L12),
[models/job.py:12](backend/models/job.py#L12),
[models/application.py:12](backend/models/application.py#L12),
[models/document.py:12](backend/models/document.py#L12),
[models/gmail.py:22](backend/models/gmail.py#L22)). Pull it into `models/base.py` and put a
timezone-aware variant behind a feature flag — naive UTC was a stop-gap, not a goal.
`BrowserSession` doesn't define `_now()` at all, so its `last_used_at` writes diverge in
format unless callers happen to do the same trick.

**MEDIUM — `updated_at` columns never auto-update.**
`UserProfile.updated_at`, `SearchSettings` (none defined), `SiteCredential.updated_at`,
`GmailCredential.updated_at` all have `default=_now` but no `onupdate=...`. Every write site
must remember to bump the timestamp manually — the schema doesn't help.

**MEDIUM — `GmailMessage.from_domain` is a denormalized field.**
Computed from `from_address` by app code at
[gmail/sync.py:182](backend/gmail/sync.py#L182). Can drift if a refactor forgets it. A
computed column (`from_domain GENERATED ALWAYS AS (...)`) or a derived index would be safer;
a check constraint at minimum would catch a no-op writer.

**LOW — Phase-2 enrichment columns on `GmailMessage` are NULL today.**
`extracted_company`, `extracted_role`, `extracted_interview_at`, `extracted_salary_text`,
`extracted_questions_json` ([models/gmail.py:78-82](backend/models/gmail.py#L78)) are
intentional pre-allocation per the file's docstring — they're acceptable, but worth flagging:
they widen the row for no current benefit and they have no index, so future filters on
`extracted_interview_at` (likely!) will scan the whole table.

**LOW — `Mapped[Optional[dict]]` annotation for JSON-list columns.**
`SearchSettings.keywords: Mapped[dict]` actually holds a `list` per the request schema. The
type lies. A `Mapped[Any]` with a comment, or a TypedDict, would be more honest.

**LOW — Defensive nullable masks logic bugs.**
`Application.job_match_id` is nullable ([models/application.py:22](backend/models/application.py#L22)).
The applier never inserts an application without a match — the nullable is defensive padding
for a code path that doesn't exist. Likewise `TailoredDocument.job_match_id`
([models/document.py:22](backend/models/document.py#L22)). Making them NOT NULL would catch
the "we lost the match-id along the way" bug at insert time instead of at read time.

**LOW — `UserProfile.email` is not unique and not indexed.**
Singleton-by-convention, but still: if anything ever logs in by email, a unique index is the
cheap safety net.

**LOW — `BrowserSession` has no `created_at`/`updated_at`.**
Out of step with the rest of the schema.

---

## 10. Inventory

| File | One-line purpose |
|---|---|
| [backend/models/__init__.py](backend/models/__init__.py) | Re-exports every model so `from backend.models import *` resolves the full graph for `Base.metadata`. |
| [backend/models/base.py](backend/models/base.py) | Trivial `DeclarativeBase` subclass — the only shared root, no mixins. |
| [backend/models/user.py](backend/models/user.py) | `UserProfile` (singleton id=1), `SearchSettings` (singleton id=1), `SiteCredential` (per-site Fernet creds). |
| [backend/models/job.py](backend/models/job.py) | `JobSource`, `Job` (with unique `dedup_hash`), `JobMatch` (with compound indexes for batch dedup). |
| [backend/models/application.py](backend/models/application.py) | `Application` and `ApplicationEvent` — application lifecycle + timeline. |
| [backend/models/document.py](backend/models/document.py) | `TailoredDocument` — per-match CV/letter artifacts and LLM provenance. |
| [backend/models/session.py](backend/models/session.py) | `BrowserSession` — Playwright `storage_state` per site. |
| [backend/models/gmail.py](backend/models/gmail.py) | `GmailCredential`, `GmailMessage`, `ApplicationCorrespondence` — Gmail Phase-1 sync + linking. |
| [backend/models/schemas.py](backend/models/schemas.py) | Pydantic v2 DTOs (`RawJob`, `JobDetails`) used by scrapers and matcher — not ORM. |
| [backend/database.py](backend/database.py) | Async engine, WAL pragma, `init_db` (create_all + column-add shim + source seeding), session helpers. |
| [backend/defaults.py](backend/defaults.py) | Constants module — field-length caps, daily-limit fallback, gap-severity thresholds. No DB writes. |
| [alembic.ini](alembic.ini) | Alembic config pointing at `alembic/` with `sqlite+aiosqlite:///data/jobpilot.db`. |
| [alembic/env.py](alembic/env.py) | Async-aware env using `async_engine_from_config` + `NullPool`. |
| [alembic/versions/071b973b48b2_initial_schema.py](alembic/versions/071b973b48b2_initial_schema.py) | Initial 9-table baseline (still includes the dead `batch_time` column). |
| [alembic/versions/df6eea4756c3_add_site_credentials_table.py](alembic/versions/df6eea4756c3_add_site_credentials_table.py) | Adds `site_credentials`. |
| [alembic/versions/41441908fc29_add_initial_indexes.py](alembic/versions/41441908fc29_add_initial_indexes.py) | Bulk-adds 13 indexes to retrofit query plans. |
| [alembic/versions/e3a1f2b8c9d7_add_last_dashboard_seen_at_to_userprofile.py](alembic/versions/e3a1f2b8c9d7_add_last_dashboard_seen_at_to_userprofile.py) | Adds `UserProfile.last_dashboard_seen_at`. |
