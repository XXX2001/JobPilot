# Gmail Integration — Design Proposal

**Status:** Forward-looking design, not yet implemented
**Author:** JobPilot architecture team
**Date:** 2026-05-22
**Companion docs:** `01-*` (audit findings), `02-*` (auto-apply hardening) — TBD

---

## Executive Summary

- **Goal.** Close the loop between outbound applications and inbound recruiter / ATS email so the system can (a) reconcile application status automatically, (b) surface clarifying questions to the user, (c) feed CV/letter regeneration when a recruiter asks for an updated document, and (d) enrich `Application` rows with structured interview / salary / contact data.
- **Auth.** Google OAuth 2.0 (offline) per user. Refresh token stored Fernet-encrypted in a new singleton `gmail_credentials` row, reusing the existing `CREDENTIAL_KEY` mechanism from `backend/models/user.py:SiteCredential`. Scopes start at `gmail.readonly`; `gmail.modify` is added in Phase 2 once label writes are introduced.
- **Sync.** Push via `users.watch` + Cloud Pub/Sub when feasible; polling fallback every 5 min via APScheduler (the existing `scheduler/` already imports `apscheduler.schedulers.asyncio` defensively — Gmail is the first feature to actually `start()` it). Delta sync uses `users.history.list` keyed off the stored `historyId`.
- **Classification.** Three-stage funnel: (1) cheap header heuristics (sender domain, list-unsubscribe, ATS signatures), (2) Gemini Flash-Lite single-shot classifier on subject + first 1 KB of body for ambiguous cases (~$0.0001/email), (3) Gemini Pro escalation only when the Flash-Lite confidence falls below `GMAIL_LLM_AMBIGUOUS_THRESHOLD` (default 0.6) **and** the email is provisionally linked to an existing `Application`.
- **Phased rollout.** Phase 1 (M): read-only sync, manual link to `Application`. Phase 2 (L): auto-classify, auto-link, status state-machine. Phase 3 (L): auto-adapt — reuse `cv_modifier.py` to regenerate documents when a recruiter requests changes.

---

## Architecture

```
                           ┌─────────────────────────────────────────────┐
                           │                Gmail API                    │
                           │   users.watch → Pub/Sub → /webhooks/gmail   │
                           └────────────┬────────────────────────────────┘
                                        │  push notification (historyId)
                                        ▼
┌──────────────────┐   poll fallback   ┌──────────────────────────────┐
│ APScheduler      │ ────────────────► │ GmailSyncWorker              │
│ (cron 5 min)     │                   │  • history.list(start_id)    │
└──────────────────┘                   │  • messages.get(metadata)    │
                                        │  • dedup & persist          │
                                        └──────────────┬───────────────┘
                                                       ▼
                                        ┌──────────────────────────────┐
                                        │ MessageClassifier            │
                                        │  1. Heuristics (regex/domain)│
                                        │  2. Flash-Lite (ambiguous)   │
                                        │  3. Pro (linked + uncertain) │
                                        └──────────────┬───────────────┘
                                                       ▼
                                        ┌──────────────────────────────┐
                                        │ ApplicationMatcher           │
                                        │  • company+role+temporal     │
                                        │  • LLM disambiguation        │
                                        │  • confidence ≥ 0.75 → link  │
                                        └──────────────┬───────────────┘
                                                       ▼
                                        ┌──────────────────────────────┐
                                        │ StatusReconciler             │
                                        │  • map intent → transition   │
                                        │  • write ApplicationEvent    │
                                        │  • broadcast WS event        │
                                        └──────────────┬───────────────┘
                                                       ▼
                       ┌──────────────────┐      ┌──────────────────────┐
                       │ ApplicationEngine│◄─────│ AutoAdaptDispatcher  │
                       │  (existing)      │      │  (Phase 3 only)      │
                       └──────────────────┘      └──────────────────────┘
```

Integration touch-points with existing modules:

| Subsystem | New collaborator | Existing module |
| --- | --- | --- |
| Startup wiring | `GmailSyncWorker`, `MessageClassifier` singletons | `backend/main.py` lifespan |
| DB sessions | `gmail_credentials`, `gmail_messages`, `application_correspondence` | `backend/database.py:AsyncSessionLocal` + `Base.metadata.create_all` |
| Scheduler | First real `AsyncIOScheduler.start()` call | `backend/scheduler/morning_batch.py` (currently imports but never starts) |
| WS events | `gmail_message_received`, `application_status_changed` | `backend/api/ws.py:ConnectionManager`, `backend/api/ws_models.py:WSMessage` |
| Status update | `StatusReconciler.apply_transition()` writes `Application` + `ApplicationEvent` | `backend/models/application.py` |
| Auto-adapt (Phase 3) | Re-invoke `CVPipeline.generate_tailored_cv` with new context | `backend/latex/pipeline.py`, `backend/llm/cv_modifier.py` |
| Secrets | Fernet-encrypt refresh tokens with `CREDENTIAL_KEY` | `backend/config.py`, `backend/models/user.py:SiteCredential` |

---

## 1. Auth Model

### 1.1 Scopes

| Phase | Scope | Why |
| --- | --- | --- |
| 1 | `https://www.googleapis.com/auth/gmail.readonly` | Read-only sync, no writes |
| 2 | + `https://www.googleapis.com/auth/gmail.metadata` | Faster history sync (no body fetch) |
| 2 | + `https://www.googleapis.com/auth/gmail.modify` | Apply `JobPilot/Tracked`, `JobPilot/Interview`, `JobPilot/Rejected` labels |
| 3 | (no extra) | Reply drafts (Phase 3) use `gmail.modify` via `users.drafts.create` |

We deliberately **avoid** `gmail.send` and full-access `https://mail.google.com/` — drafts give the user a manual gate before anything leaves the inbox.

### 1.2 Storage — new `gmail_credentials` table

Singleton row per JobPilot install (single-user app today; the table is keyed for future multi-user without migration):

```python
# backend/models/gmail.py
class GmailCredential(Base):
    """
    @brief   Encrypted OAuth refresh-token store for the user's Gmail account.
    @details Singleton-ish: rows are keyed by email_address so a future multi-account
             setup can persist multiple linked inboxes. Refresh tokens are
             Fernet-encrypted at rest using CREDENTIAL_KEY (same scheme as
             SiteCredential). Access tokens are NEVER persisted — they are
             fetched on demand from the refresh token and held in-memory only.
    """
    __tablename__ = "gmail_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email_address: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    encrypted_refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[str] = mapped_column(String, nullable=False)   # space-joined
    history_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # last synced
    watch_expiration: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
```

### 1.3 OAuth flow

- **Client config** lives in two new `Settings` fields in `backend/config.py`:
  - `GMAIL_CLIENT_ID: str = ""` (optional → feature off when empty, mirroring `SERPAPI_KEY` pattern)
  - `GMAIL_CLIENT_SECRET: str = ""`
- **Routes** (new `backend/api/gmail_auth.py` router, mounted in `main.py`):
  - `GET /api/gmail/oauth/start` → 302 to Google consent screen with `access_type=offline&prompt=consent` and a CSRF state token signed with `CREDENTIAL_KEY`.
  - `GET /api/gmail/oauth/callback` → exchanges code for tokens, encrypts and stores refresh token, kicks off the initial `users.watch` + back-fill sync.
  - `POST /api/gmail/disconnect` → revokes Google grant, deletes the row.
- **Refresh:** A `GmailTokenManager` holds a per-process cache `{email → (access_token, expires_at)}`. The cache is consulted before every API call; on miss or `expires_at - 60s < now`, it makes the `oauth2.token` refresh and updates the cache. No DB writes happen on refresh — only on revoke or scope upgrade.
- **Watch renewal:** Push watches expire after 7 days; the same APScheduler that polls also re-issues `users.watch` daily.

---

## 2. Sync Architecture

### 2.1 Push (preferred) — `users.watch` + Pub/Sub

- **Topic:** `projects/<gcp-project>/topics/jobpilot-gmail-watch`
- **Subscription:** Push subscription pointing at `POST /api/webhooks/gmail`.
- **Verification:** Inbound request carries an OIDC JWT signed by Google; we verify against the published Google JWKs (cache 24 h).
- **Payload:** Contains `emailAddress` and `historyId`. We **do not** trust the historyId blindly — we look up the credential, compare against the persisted `history_id`, and call `history.list?startHistoryId=<stored>` to fetch the delta.
- **Why not parse the push body directly?** Push notifications can be reordered/duplicated; pulling history is idempotent.

### 2.2 Polling fallback

- APScheduler cron: `*/5 * * * *` invokes `GmailSyncWorker.sync_now(email)`.
- Same code path as the push handler — only entry differs.
- Polling is the only path on dev machines without a public webhook URL; production switches to push once `users.watch` is active.

### 2.3 Rate limits

Gmail quotas relevant to us:

| Operation | Quota unit cost | Daily quota |
| --- | --- | --- |
| `users.history.list` | 2 | 1,000,000,000 |
| `users.messages.get` (metadata) | 5 | 1,000,000,000 |
| `users.messages.get` (full) | 5 | — |
| Per-user / second | 250 quota units | — |

A single user receiving < 500 emails/day costs < 5,000 units/day — three orders of magnitude under the limit. Protections:

- `asyncio.Semaphore(10)` on outbound calls in `GmailSyncWorker` (mirrors `CONCURRENCY_GEMINI` pattern in `backend/scheduler/morning_batch.py`).
- Exponential back-off on `429` and `5xx`, reusing the helper pattern in `backend/llm/gemini_client.py:_extract_retry_seconds`.

### 2.4 Delta sync algorithm

```python
async def sync_now(self, email: str) -> None:
    cred = await self._load_credential(email)
    start = cred.history_id
    async for batch in self._history_pages(cred, start):
        msg_ids = self._extract_added_message_ids(batch)
        details = await self._batch_get_metadata(cred, msg_ids)
        for msg in details:
            await self._persist_and_classify(msg)
        cred.history_id = batch["historyId"]
        await self._commit(cred)
```

If `start` is `None` (first run) we seed with `messages.list?q=newer_than:30d category:primary` — bounded back-fill so we don't drown the LLM in a 10-year inbox.

---

## 3. Schema Additions

Three new tables, all in a new `backend/models/gmail.py`:

### 3.1 `gmail_credentials` (shown above in §1.2)

### 3.2 `gmail_messages` — raw + classified email cache

```python
class GmailMessage(Base):
    """
    @brief   Cached metadata for a single Gmail message that touched JobPilot.
    @details One row per Gmail message id we have ever observed. Body is NOT
             stored — we keep gmail_thread_id + gmail_message_id and re-fetch
             body text only when the classifier needs it (then discarded).
             Classification fields are filled by MessageClassifier; they may
             remain null for messages still in the queue.
    """
    __tablename__ = "gmail_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gmail_message_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    gmail_thread_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    account_email: Mapped[str] = mapped_column(String, nullable=False)  # FK-by-convention to gmail_credentials.email_address
    from_address: Mapped[str] = mapped_column(String, nullable=False)
    from_domain: Mapped[str] = mapped_column(String, index=True, nullable=False)
    to_address: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    subject: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    snippet: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)

    # Classification (filled by MessageClassifier)
    category: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    # one of: "ats_ack", "recruiter_question", "interview_invite",
    #        "rejection", "offer", "scheduling", "noise", "unknown"
    category_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    classified_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # one of: "heuristic", "flash_lite", "pro", "manual"
    ats_vendor: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # "greenhouse", "lever", "workday", "ashby", "workable", "smartrecruiters", None

    # Enrichment (filled by EnrichmentExtractor, also LLM-backed)
    extracted_company: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    extracted_role: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    extracted_interview_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    extracted_salary_text: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    extracted_questions_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
```

Indexes: `gmail_message_id` (unique), `gmail_thread_id`, `received_at`, `from_domain`, `category`. Add a composite `(account_email, received_at desc)` index for the inbox listing endpoint.

### 3.3 `application_correspondence` — link table

```python
class ApplicationCorrespondence(Base):
    """
    @brief   Many-to-many link between Applications and GmailMessages.
    @details A single Gmail thread can be linked to at most one Application
             (enforced at the application layer, not the DB layer — matches
             the project convention from models/application.py). Each row
             carries the confidence score and method that produced the link
             so that low-confidence links can be surfaced to the user for
             manual confirmation.
    """
    __tablename__ = "application_correspondence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    application_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    gmail_message_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)  # FK-by-convention to gmail_messages.id
    gmail_thread_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    direction: Mapped[str] = mapped_column(String, nullable=False)  # "inbound" | "outbound"
    link_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    link_method: Mapped[str] = mapped_column(String, nullable=False)
    # "company_temporal", "thread_continuation", "llm_disambiguation", "manual"
    confirmed_by_user: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
```

### 3.4 Migrations

All three tables are created via `Base.metadata.create_all` in `backend/database.py:init_db()` — no Alembic. The lightweight `_migrate_add_columns` helper in the same file handles the only column added to existing tables:

- `applications.last_correspondence_at: DATETIME NULL` — denormalised, updated by `StatusReconciler` so the UI can sort by activity without a join.

---

## 4. Classification Pipeline

### 4.1 Stage 1 — Heuristics (zero-cost, deterministic)

Implemented in `backend/gmail/classifier_heuristics.py`. Pattern table seeded from public ATS vendor info:

```python
ATS_DOMAINS: dict[str, str] = {
    # Greenhouse
    "greenhouse-mail.io":  "greenhouse",
    "no-reply@greenhouse.io": "greenhouse",
    "boards.greenhouse.io": "greenhouse",
    # Lever
    "hire.lever.co":       "lever",
    "jobs.lever.co":       "lever",
    # Workday (tenant-scoped — match suffix only)
    ".myworkday.com":      "workday",
    "myworkdayjobs.com":   "workday",
    # Ashby
    "ashbyhq.com":         "ashby",
    # Workable
    "workablemail.com":    "workable",
    "workable.com":        "workable",
    # SmartRecruiters
    "smartrecruiters.com": "smartrecruiters",
    # Taleo / Oracle
    "taleo.net":           "taleo",
    "oraclecloud.com":     "taleo",
    # iCIMS
    "icims.com":           "icims",
    # BambooHR
    "bamboohr.com":        "bamboohr",
}

REJECTION_PATTERNS = [
    r"\bnot moving forward\b", r"\bunfortunately\b.*\bdecided\b",
    r"\bdecided to (proceed|move forward) with other\b",
    r"\bregret(fully)? (to )?inform\b", r"\bnot (the )?right fit\b",
]

INTERVIEW_PATTERNS = [
    r"\binterview\b.*\b(invitation|invite|scheduling)\b",
    r"\bnext step(s)?\b.*\b(call|chat|conversation)\b",
    r"\b(would|are) you available\b",
    r"\bcalendly\.com\b", r"\bsavvycal\.com\b", r"\bcal\.com\b",
]

OFFER_PATTERNS = [
    r"\boffer letter\b", r"\bcompensation (package|details)\b",
    r"\bpleased to extend\b", r"\bwelcome aboard\b",
]
```

Heuristics resolve roughly 70 % of inbound based on Greenhouse/Lever-heavy samples. Each rule emits `(category, confidence)`; max confidence wins. Heuristic confidence is capped at 0.85 so we never bypass the LLM when both rules disagree.

### 4.2 Stage 2 — Flash-Lite triage (Gemini)

Invoked when the top-heuristic confidence < `GMAIL_HEURISTIC_THRESHOLD` (default 0.7). Uses **Gemini Flash-Lite** (cheapest model — explicit choice over the default `GOOGLE_MODEL` from `backend/config.py`). A new config field is added:

```python
GMAIL_CLASSIFIER_MODEL: str = Field("gemini-flash-lite-latest", env="GMAIL_CLASSIFIER_MODEL")
GMAIL_LLM_AMBIGUOUS_THRESHOLD: float = Field(0.6, env="GMAIL_LLM_AMBIGUOUS_THRESHOLD")
GMAIL_HEURISTIC_THRESHOLD: float = Field(0.7, env="GMAIL_HEURISTIC_THRESHOLD")
```

Prompt input: subject + first 1 KB of body (HTML stripped). Output schema (Pydantic):

```python
class GmailTriageOutput(BaseModel):
    category: Literal["ats_ack", "recruiter_question", "interview_invite",
                      "rejection", "offer", "scheduling", "noise", "unknown"]
    confidence: confloat(ge=0.0, le=1.0)
    extracted_company: str | None
    extracted_role: str | None
```

Reuses `GeminiClient.generate_json(schema=GmailTriageOutput)` (the same self-healing JSON path documented in `backend/llm/gemini_client.py:1`).

### 4.3 Stage 3 — Pro escalation

Only when:
- Flash-Lite confidence < `GMAIL_LLM_AMBIGUOUS_THRESHOLD`, **and**
- The message is already linked to an `Application` (so the cost is justified by downstream impact on real records).

Prompt adds the `Job.description`, the relevant `Application.notes`, and the full email body (capped at 8 KB). Output schema is the same `GmailTriageOutput` plus an `extracted_questions_json` array for Phase 3.

### 4.4 Cost envelope

For a candidate sending ~50 applications/week and receiving ~150 inbound emails/week:

| Stage | Volume | Model | Est. cost / week |
| --- | --- | --- | --- |
| Heuristics | 150 | — | $0 |
| Flash-Lite | ~45 (30 %) | flash-lite | ~$0.005 |
| Pro | ~5 (3 %) | gemini-3-pro | ~$0.03 |

Negligible — well under the existing scraping batch budget.

---

## 5. Application-Matching Logic

`ApplicationMatcher.link(gmail_msg: GmailMessage) -> tuple[int | None, float, str]` returns `(application_id, confidence, method)`. Algorithm in priority order:

### 5.1 Thread continuation (highest confidence)

If `gmail_thread_id` already appears in `application_correspondence`, reuse the same `application_id`. Confidence: **0.99**, method: `thread_continuation`.

### 5.2 Company + role + temporal proximity

For each candidate `Application` row joined through `JobMatch → Job`, compute a similarity score:

```python
def score_link(app_job: Job, msg: GmailMessage) -> float:
    company_sim = normalized_company_match(app_job.company, msg.extracted_company or msg.from_domain)
    role_sim    = token_set_ratio(app_job.title, msg.extracted_role or msg.subject)
    days_delta  = (msg.received_at - app.applied_at).days
    # Temporal: full credit for 0–30 days, decays to 0 at 90 days, 0 beyond
    temporal    = max(0.0, 1.0 - max(0, days_delta - 30) / 60)
    # Weighted blend
    return 0.55 * company_sim + 0.25 * role_sim + 0.20 * temporal
```

- `normalized_company_match` strips `Inc.`, `Ltd`, `GmbH`, etc., lowercases, and uses `rapidfuzz.fuzz.token_set_ratio / 100`.
- ATS vendor messages (Greenhouse/Lever) often hide the real company in the body — the heuristic step extracts `extracted_company` precisely so this score works.
- Returns the best application above `LINK_CONFIDENCE_AUTO` (default **0.75**); messages between **0.45 – 0.75** are flagged for manual confirmation; below 0.45 are left unlinked.

### 5.3 LLM disambiguation (fallback)

When the top two candidates score within 0.05 of each other, escalate to Stage-3 Pro with both `Job.description` snippets and ask:

```
You are given an email and two candidate job applications. Return the integer
index (0 or 1) of the application this email is about, or -1 if neither.
```

Output schema:

```python
class LinkDisambiguationOutput(BaseModel):
    index: Literal[-1, 0, 1]
    confidence: confloat(ge=0.0, le=1.0)
    reason: str
```

### 5.4 Idempotency

`(application_id, gmail_message_id)` is treated as a unique key at the application layer (the project has FK-only-by-convention semantics — see `backend/models/application.py:11`); re-runs of the same message return the existing row.

---

## 6. Status State Machine

### 6.1 States

The current `Application.status` allowlist (`backend/api/applications.py:96`) is:
`pending | applied | cancelled | failed | interview | offer | rejected`.

We extend to nine states by adding two intermediate ones, fully backward-compatible:

```
                ┌───────┐
                │pending│
                └───┬───┘
                    │ submit
                    ▼
                ┌───────┐
                │applied│◄────────────────┐
                └───┬───┘                 │ (recruiter follow-up
                    │ ATS auto-ack         │  after silence)
                    ▼                     │
                ┌───────┐                 │
                │ ack   │                 │
                └───┬───┘                 │
                    │ recruiter outreach  │
                    ▼                     │
                ┌────────┐                │
                │ screen │────────────────┘
                └───┬────┘
                    │ schedule technical / panel
                    ▼
                ┌──────────┐
                │interview │
                └───┬──────┘
                    │
        ┌───────────┴──────────────┐
        │                           │
        ▼                           ▼
   ┌──────┐                    ┌────────┐
   │offer │                    │rejected│ (terminal)
   └──┬───┘                    └────────┘
      │ accept / decline
      ▼
 ┌─────────┐
 │accepted │ (terminal)  ────  new state, Phase 2
 └─────────┘
```

Two new states: `ack` (auto-ack received but no human yet), `accepted` (offer accepted). Terminal: `rejected`, `accepted`, `cancelled`, `failed`.

### 6.2 Transition table

```python
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "pending":    {"applied", "failed", "cancelled"},
    "applied":    {"ack", "screen", "interview", "rejected", "offer"},
    "ack":        {"screen", "interview", "rejected"},
    "screen":     {"interview", "rejected", "offer"},
    "interview":  {"interview", "offer", "rejected"},   # multi-round → self-loop
    "offer":      {"accepted", "rejected"},
    "rejected":   set(),
    "accepted":   set(),
    "cancelled":  set(),
    "failed":     {"pending"},                          # operator retry
}
```

Any transition outside this table emits a warning log and writes an `ApplicationEvent(event_type="status_conflict", details=...)` instead of mutating `status`.

### 6.3 Intent → transition mapping

`StatusReconciler` maps `(GmailMessage.category, current_status)` to the target status:

| Category | from `pending`/`applied` | from `ack`/`screen` | from `interview` |
| --- | --- | --- | --- |
| `ats_ack` | `ack` | — (idempotent) | — |
| `recruiter_question` | `screen` | `screen` (event only) | `interview` (event only) |
| `interview_invite` | `interview` | `interview` | `interview` (additional round) |
| `scheduling` | `interview` | `interview` | `interview` |
| `rejection` | `rejected` | `rejected` | `rejected` |
| `offer` | `offer` | `offer` | `offer` |
| `noise` | no-op | no-op | no-op |
| `unknown` | event-only (no status change) | event-only | event-only |

Every classified message writes an `ApplicationEvent` regardless of whether the status changes; the status update is the *consequence*, the event is the *evidence*.

### 6.4 Concurrency

`StatusReconciler.apply_transition()` runs inside a single transaction:
1. `SELECT … FOR UPDATE` (translated to `BEGIN IMMEDIATE` on SQLite — WAL mode is already on; see `backend/database.py:48`).
2. Validate transition against `ALLOWED_TRANSITIONS`.
3. Update `Application.status`, `Application.last_correspondence_at`.
4. Insert `ApplicationEvent`.
5. Commit, then broadcast WS event.

---

## 7. Auto-Adapt Loop (Phase 3)

### 7.1 Trigger

When `MessageClassifier` returns `recruiter_question` **and** the Pro stage's `extracted_questions_json` contains a question matching the `cv_change_request` template (e.g., "Could you send an updated CV with more focus on Python?"), the reconciler enqueues an `AutoAdaptTask`.

### 7.2 Task pipeline

```python
class AutoAdaptTask:
    application_id: int
    gmail_thread_id: str
    requested_changes: list[str]  # "more python", "highlight aws", ...
```

Executed by a new `AutoAdaptDispatcher` (background asyncio task spawned at startup, mirrors the queue/scheduler pattern in `morning_batch.py`). Steps:

1. Load the linked `Application → JobMatch → Job` row.
2. Load the most recent `TailoredDocument` for this match.
3. Build a synthetic `FitAssessment` extension: take the existing `JobMatch.fit_assessment_json` (already populated by `FitEngine` in `morning_batch.py:340`) and append the recruiter's requested changes as additional "must-have" skills.
4. Call `CVPipeline.generate_tailored_cv(..., fit_assessment=augmented_assessment, additional_context=request_text)` — this re-uses **exactly** the existing pipeline; no new LLM glue.
5. Drop the resulting PDF into `data/cvs/<match_id>_<slug>/` (same convention as `morning_batch.py:403`).
6. Create a Gmail draft via `users.drafts.create` (requires `gmail.modify` scope — Phase 2 prerequisite) in reply to the original thread, with the new CV attached and a templated message body.
7. Emit WS event `auto_adapt_ready(application_id, draft_id)` so the UI can show "Review draft".

**The user always presses Send** — we never auto-send in Phase 3. Auto-send is a deliberate non-goal.

### 7.3 Reuse map

| New code | Reused module |
| --- | --- |
| `AutoAdaptDispatcher` | new |
| Augmented `FitAssessment` | `backend/matching/fit_engine.py` |
| CV regeneration | `backend/latex/pipeline.py:CVPipeline.generate_tailored_cv` |
| LaTeX compile | `backend/latex/compiler.py` |
| Gmail draft create | new (`GmailDraftWriter`) |

---

## 8. UI Surface

### 8.1 REST routes (new `backend/api/gmail.py` and `backend/api/correspondence.py`)

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/gmail/status` | Sync state: last `history_id`, last poll, watch expiry, message counts |
| `POST` | `/api/gmail/sync` | Force a sync pass (debug / power-user) |
| `POST` | `/api/gmail/disconnect` | Revoke + delete credential |
| `GET` | `/api/gmail/oauth/start` | OAuth bootstrap (see §1.3) |
| `GET` | `/api/gmail/oauth/callback` | OAuth callback |
| `POST` | `/api/webhooks/gmail` | Pub/Sub push receiver |
| `GET` | `/api/correspondence/{application_id}` | Threaded view of all emails linked to an `Application`, oldest-first |
| `POST` | `/api/correspondence/link` | Manual link: `{application_id, gmail_message_id}`; sets `confirmed_by_user=True` |
| `DELETE` | `/api/correspondence/{id}` | Manual unlink (mistake recovery) |
| `GET` | `/api/correspondence/unlinked` | All `GmailMessage` rows with `category != "noise"` and no `application_correspondence` row — drives the manual-link UI |
| `POST` | `/api/correspondence/{id}/confirm` | Confirm an auto-link with confidence < 0.75 |

Pydantic response shape (mirrors `ApplicationOut` conventions from `backend/api/applications.py:54`):

```python
class CorrespondenceItemOut(BaseModel):
    gmail_message_id: str
    gmail_thread_id: str
    from_address: str
    subject: str | None
    snippet: str | None
    received_at: datetime
    category: str | None
    category_confidence: float | None
    link_confidence: float
    direction: str
    confirmed_by_user: bool

    model_config = ConfigDict(from_attributes=True)

class CorrespondenceThreadOut(BaseModel):
    application_id: int
    messages: list[CorrespondenceItemOut]
```

### 8.2 WebSocket events (extend `backend/api/ws_models.py:WSMessage`)

Three new discriminated-union variants — symmetrical with the existing `ScrapingStatus` / `MatchingStatus` pattern:

```python
class GmailSyncStatus(BaseModel):
    type: Literal["gmail_sync_status"]
    last_history_id: str | None
    messages_synced: int
    progress: float  # 0.0–1.0

class GmailMessageReceived(BaseModel):
    type: Literal["gmail_message_received"]
    gmail_message_id: str
    from_address: str
    subject: str | None
    category: str | None
    category_confidence: float | None
    linked_application_id: int | None
    link_confidence: float | None

class ApplicationStatusChanged(BaseModel):
    type: Literal["application_status_changed"]
    application_id: int
    previous_status: str
    new_status: str
    triggered_by: str        # "gmail" | "user" | "applier"
    evidence_message_id: str | None
```

Plus a Phase 3 variant:

```python
class AutoAdaptReady(BaseModel):
    type: Literal["auto_adapt_ready"]
    application_id: int
    draft_id: str
    pdf_path: str
```

All four are added to the `WSMessage` `Union` so existing front-end dispatch (a single `switch (msg.type)` in the SvelteKit store) extends naturally.

### 8.3 Settings page additions

Extend `backend/api/settings.py` with:
- `gmail_enabled: bool` (read-only — derived from credential row existence)
- `gmail_auto_link_threshold: float` (writable, default 0.75)
- `gmail_classify_with_pro: bool` (writable, default `True`) — kill-switch for Pro escalation
- `gmail_auto_status_updates: bool` (writable, default `True`) — when False, status changes require manual confirmation in UI

---

## 9. Privacy & Safety

### 9.1 Data minimisation

- **No body persistence.** `gmail_messages` stores `snippet` (≤ 200 chars, already provided by Gmail) and the metadata above. Full body is fetched on demand for classification, held in a local variable, never written to disk.
- **No attachment download** in Phase 1 / 2. Phase 3 attachments (incoming PDFs of offer letters etc.) are flagged for the user; they download manually through Gmail.
- **No outbound headers/list-id** preserved — we drop fields with PII unrelated to job search (e.g., `Bcc`, full `Received` chain).

### 9.2 Scope of action

- The classifier **never** reads emails outside the user's primary mailbox.
- A whitelist guard: `GmailSyncWorker` skips messages whose `category` resolves to `noise` and **never** invokes Pro on noise. Hard cap: any message that has not matched at least one job-related heuristic OR has Flash-Lite confidence < 0.2 is dropped without further LLM calls.
- **No label writes** in Phase 1. Phase 2 writes only to `JobPilot/*` labels (created by us); we never modify user-owned labels and never archive/delete.

### 9.3 PII handling at LLM boundary

- All bodies are passed through `backend/security/sanitizer.py:sanitize_for_prompt` (already used by `cv_modifier.py` per `backend/llm/cv_modifier.py:35`) before being sent to Gemini.
- Email addresses and phone numbers in the body are tokenised as `<EMAIL_REDACTED_n>` / `<PHONE_REDACTED_n>` and substituted back into extracted fields client-side.
- Gemini API calls are made with `safetySettings` set to BLOCK_NONE for "harassment" (recruiter rejection wording can trip default filters) and default for everything else.

### 9.4 User controls

- A one-click **disconnect** revokes the OAuth grant via `https://oauth2.googleapis.com/revoke` and `DELETE`s the row. Subsequent syncs no-op cleanly because `GmailSyncWorker.sync_now` exits when no credential row exists.
- **Pause** (set `gmail_credentials.enabled = False`) stops syncs without revoking the token.
- An "Export my data" endpoint (`GET /api/gmail/export`) dumps `gmail_messages` + `application_correspondence` rows as JSON for GDPR-style portability.

### 9.5 Threat model — short list

| Threat | Mitigation |
| --- | --- |
| Refresh-token exfiltration via DB read | Fernet encryption with `CREDENTIAL_KEY` env var (same as `SiteCredential`) |
| Forged Pub/Sub push | Verify Google-signed OIDC JWT on every `POST /api/webhooks/gmail` |
| LLM prompt injection from email body | `sanitize_for_prompt` + Pydantic schema for output; no instruction-following output paths |
| Mis-classification triggering bad status change | `auto_status_updates` toggle + `ALLOWED_TRANSITIONS` enforcement + every change writes `ApplicationEvent` for audit |
| Quota burn from runaway loop | Semaphore + per-account daily-cap counter (similar to `DailyLimitGuard`) |

---

## 10. Phased Rollout

### Phase 1 — Read-only sync + manual link  (effort **M**, ~3 weeks)

**Ships:**
- `gmail_credentials`, `gmail_messages`, `application_correspondence` tables.
- OAuth flow with `gmail.readonly` scope.
- Polling sync (no Pub/Sub).
- Heuristic-only classifier (no LLM).
- `/api/correspondence/*` endpoints for manual link/unlink.
- `GmailSyncStatus` + `GmailMessageReceived` WS events.
- Settings toggle `gmail_enabled`.
- Front-end: connect button on Settings, "Linked Emails" tab on Application detail page, "Inbox" page listing unlinked job-related messages.

**Exit criteria:**
- 200 messages classified by heuristics with ≥ 95 % precision on the manual sample (false positives bounded).
- Token refresh works for at least 14 days unattended.
- No DB writes outside the three new tables and the unchanged `applications.last_correspondence_at` column.

### Phase 2 — Auto-classify + auto-link + status state machine  (effort **L**, ~5 weeks)

**Ships:**
- Flash-Lite + Pro tiered classifier.
- `ApplicationMatcher` with confidence threshold.
- `StatusReconciler` writing `Application.status` + `ApplicationEvent`.
- `gmail.modify` scope upgrade and `JobPilot/*` label writes.
- Pub/Sub push pipeline (`/api/webhooks/gmail`), polling kept as fallback.
- `ApplicationStatusChanged` WS event.
- "Confirm auto-link" UI for confidence 0.45–0.75.
- Pro-escalation kill switch in settings.

**Exit criteria:**
- ≥ 90 % auto-link precision on a 200-message gold set; recall ≥ 70 %.
- 0 disallowed status transitions in a 30-day soak.
- LLM cost ≤ $0.50/week for the reference 150-message/week candidate.
- Webhook signature verification covered by tests.

### Phase 3 — Auto-adapt (CV regen on recruiter request)  (effort **L**, ~4 weeks)

**Ships:**
- `AutoAdaptDispatcher` background task.
- Recruiter-question intent extraction (Pro-only).
- Gmail draft create with attached regenerated PDF (no auto-send).
- `AutoAdaptReady` WS event + UI to preview / open the draft in Gmail.

**Exit criteria:**
- Draft created within 90 s of inbound classification.
- Manual user acceptance rate ≥ 60 % on the regenerated CV (tracked via `confirmed_by_user`-style flag on a new `auto_adapt_outcomes` row, TBD).
- No regression in `morning_batch.py` CV pipeline tests.

---

## Open Questions

> **Resolved 2026-05-23** by product owner — decisions locked below; original questions kept for context.

1. **Multi-account.** ~~Single-user app today; do we want to support linking *both* a personal and a work Gmail in the same JobPilot instance?~~
   **Decision:** **Single account only.** Schema stays keyed by `email_address` (no migration needed if we ever change our mind), but UI assumes one inbox — Settings shows one Connect button, `/api/correspondence/unlinked` returns the rows for the single linked account.
2. **Pub/Sub project ownership.** ~~Push notifications require a GCP project with billing. Acceptable to ask the user to BYO project, or do we ship a shared one?~~
   **Decision:** **Polling-only for Phase 1.** Push pipeline (Pub/Sub, `/api/webhooks/gmail`, JWT verification) is deferred to Phase 2. APScheduler `*/5 * * * *` is the only sync entry point. The `users.watch` daily renewer is also deferred — not invoked in Phase 1.
3. **Gemini model availability.** ~~`gemini-flash-lite-latest` — confirm the exact identifier.~~
   **Decision:** **Use `gemini-flash-lite-latest`.** Locked for Phase 2 (Phase 1 has no LLM calls). If the identifier is rejected at runtime, fall back to `gemini-flash-latest` per the original design.
4. **Workday / Taleo recruiter portals.** Out of scope. Deferred indefinitely.
5. **Threading across the apply-engine login flow.** ~~Should the Gmail event bus reuse the same `ConnectionManager.register_handler` pattern, or stand up its own dispatcher?~~
   **Decision:** **Reuse `ConnectionManager.register_handler`** for Phase 1 (the production-conservative choice — matches the pattern already used by the applier and the scraping batch). Revisit only if event volume grows.
6. **Calendar integration.** Deferred to a separate design. `extracted_interview_at` is still populated in Phase 2 enrichment so the future Calendar writer has data to work with.

---

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- |
| Gmail OAuth verification (sensitive scope) takes weeks for unverified apps | High | Blocks Phase 2 modify scope | Apply for verification at start of Phase 1; ship Phase 1 read-only against unverified app (works for the developer's own account in test mode for up to 100 users). |
| Pub/Sub push duplication / out-of-order delivery | Medium | False extra syncs | Always trust the **stored** `history_id`, not the push payload; `history.list` is idempotent. Dedup at insert via `gmail_message_id` unique index. |
| Mis-link from ATS aggregator (e.g., a single Greenhouse domain serving 50 companies) | Medium | Wrong status updates | LLM disambiguation step before threshold; require ≥ 0.75 for auto-status; user confirmation for 0.45–0.75. |
| LLM cost blow-up if user has 10k unread | Low | $$$ | First-run bounded back-fill (`newer_than:30d category:primary`); per-day LLM call cap (`DailyLimitGuard`-style). |
| Pydantic validation drift between Flash-Lite and Pro outputs | Medium | Classifier returns `unknown` repeatedly | Shared `GmailTriageOutput` schema; CI test that both models satisfy the schema on a fixture set. |
| `Application.status` text field has no DB-level enum | Medium | Silent allow of invalid status | Centralise all writes through `StatusReconciler.apply_transition` and add a validator on the `UpdateApplicationRequest` Pydantic body (`backend/api/applications.py:102`). |
| `gmail.modify` scope leak to draft auto-send | High | User trust loss | Code review checklist + integration test asserting `gmail.send`-equivalent calls (e.g., `users.drafts.send`) are never invoked by JobPilot. |
| SQLite write contention with morning batch already taking long transactions | Low | Sync lag | WAL is already enabled (`backend/database.py:48`); keep sync transactions ≤ 50 ms by deferring LLM calls outside the transaction boundary. |
| Apscheduler started for the first time may surface latent bugs in `MorningBatchRunner` (which currently only constructs the scheduler) | Low | Startup crash | Initialise a dedicated `AsyncIOScheduler` instance for Gmail rather than reusing `morning_batch.py:self._scheduler`. |

---

## File Inventory (proposed)

New files:

```
backend/
  gmail/
    __init__.py
    auth.py                 # GmailTokenManager, OAuth helpers
    sync.py                 # GmailSyncWorker
    classifier.py           # MessageClassifier (orchestrator)
    classifier_heuristics.py# Domain & regex tables
    matcher.py              # ApplicationMatcher
    reconciler.py           # StatusReconciler
    drafts.py               # GmailDraftWriter (Phase 3)
    enrichment.py           # EnrichmentExtractor (interview date, salary…)
  api/
    gmail.py                # /api/gmail/* and /api/webhooks/gmail
    gmail_auth.py           # /api/gmail/oauth/*
    correspondence.py       # /api/correspondence/*
  models/
    gmail.py                # GmailCredential, GmailMessage, ApplicationCorrespondence
```

Modified files:

```
backend/main.py              # singletons, scheduler.start(), WS handler regs
backend/config.py            # GMAIL_* settings
backend/database.py          # column migration for applications.last_correspondence_at
backend/api/ws_models.py     # 4 new WSMessage variants
backend/models/__init__.py   # export new models
backend/api/applications.py  # extend status allowlist; route writes via StatusReconciler
```

---

## Appendix — Quick reference of existing integration points cited

- `backend/models/application.py:32` — `Application.status` text field, lifecycle comment
- `backend/models/application.py:53` — `ApplicationEvent` append-only log (reused for evidence-of-change)
- `backend/api/applications.py:96` — current status allowlist
- `backend/api/ws_models.py:126` — `WSMessage` discriminated union (extension point)
- `backend/api/ws.py:144` — `ConnectionManager.manager` singleton; `broadcast` helper
- `backend/scheduler/morning_batch.py:86` — APScheduler import (currently inactive)
- `backend/applier/engine.py:102` — per-job asyncio event pattern (mirror in `AutoAdaptDispatcher`)
- `backend/llm/gemini_client.py:1` — `GeminiClient.generate_json` rate-limited LLM wrapper
- `backend/llm/cv_modifier.py:35` — `sanitize_for_prompt` PII scrubber
- `backend/security/sanitizer.py:116` — `sanitize_url` (template for `sanitize_email_body`)
- `backend/config.py:46` — `CREDENTIAL_KEY` (reused for Fernet encryption of refresh tokens)
- `backend/models/user.py:100` — `SiteCredential` encryption pattern (template for `GmailCredential`)
- `backend/database.py:128` — `_migrate_add_columns` helper (used to add `applications.last_correspondence_at`)
