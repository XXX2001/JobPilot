# Gmail Integration — Deep Dive (Phase 1, shipped 2026-05-23)

> **Branch:** `gm-phase-1` (merged) · **Sprint plan:** [`docs/superpowers/plans/2026-05-23-gmail-phase-1.md`](../../superpowers/plans/2026-05-23-gmail-phase-1.md) · **Design doc:** [`docs/reports/2026-05-22-audit/03-gmail-integration-design.md`](../2026-05-22-audit/03-gmail-integration-design.md) · **12 sub-PRs** (`gm-1` … `gm-12`), **44 tests** added, 0 LLM calls, 0 Pub/Sub.

---

## 1. Purpose — turning the tracker into a live CRM

Before Phase 1, JobPilot was outbound-only: scrape, score, tailor CVs, open apply pages. Once an `Application` row was created, it was dead until a human edited its status — the inbox was a black box.

Gmail Phase 1 plugs that black box into the database. After a one-click OAuth consent, JobPilot polls Gmail every 5 minutes, classifies inbound messages via deterministic heuristics (ATS vendor sniff + rejection/interview/offer regex), persists metadata into `gmail_messages`, and lets the user manually pin each one to an existing `Application` row from a new `/inbox` page or a "Linked Emails" tab on the job-detail page. Several pieces of latent capability come alive in the same flow:

- **APScheduler actually starts.** `apscheduler.schedulers.asyncio` was imported but never `.start()`ed anywhere before. Gmail is the first feature to run a real recurring background job in-process.
- **`Application.last_correspondence_at`** materialises as a CRM-style activity timestamp (though no consumer reads it yet — §15.10).
- **Refresh-token-grade secrets reach the DB.** `CREDENTIAL_KEY` previously only encrypted `SiteCredential` rows; the Fernet pattern is reused 1:1 for Gmail.
- **The WS protocol gains an inbound-event voice.** Until Phase 1 the WS channel was outbound-narration-only (scraping/matching/tailoring progress). `gmail_message_received` is the first server-pushed notification a layout-level toast consumes.

Phase 1 is read-only. Status mutations, label writes, push subscriptions, LLM classification, auto-link, auto-adapt CV regen — all deferred to Phase 2/3 (§15.6). The Phase-2 enrichment columns are declared now ([`backend/models/gmail.py:78-82`](../../../backend/models/gmail.py#L78-L82)) so the next sprint never migrates twice.

---

## 2. Architecture — full data flow

```
                  ┌─────────────────────────────────────────────────┐
   USER ─────►    │  GET /api/gmail/oauth/start                     │
   (browser)      │  → 302 to accounts.google.com w/ HMAC-signed    │
                  │    state token (nonce.ts.sha256(key, n.ts))     │
                  └────────────┬────────────────────────────────────┘
                               │ Google consent
                               ▼
                  ┌─────────────────────────────────────────────────┐
                  │  GET /api/gmail/oauth/callback?code&state        │
                  │  - _verify_state() — TTL 600 s                  │
                  │  - POST oauth2.googleapis.com/token             │
                  │  - GET  gmail.googleapis.com/.../profile        │
                  │  - save_credential() — Fernet-encrypt RT        │
                  │  - 302 /settings?gmail_connected=1              │
                  └────────────┬────────────────────────────────────┘
                               │  GmailCredential row persisted
                               ▼
   ┌──────────────────────────────────────────────────────────────┐
   │  DB (SQLite, WAL):                                            │
   │  ┌──────────────────────┐                                     │
   │  │ gmail_credentials    │  ← refresh_token (encrypted)        │
   │  │  id, email, RT,      │     history_id (sync cursor)        │
   │  │  scopes, history_id, │                                     │
   │  │  enabled, ...        │                                     │
   │  └──────────────────────┘                                     │
   └─────────────┬─────────────────────────────────────────────────┘
                 │
                 │  APScheduler (interval, GMAIL_POLL_INTERVAL_MINUTES)
                 │  → main._run_gmail_poll()  (backend/main.py:46)
                 ▼
   ┌──────────────────────────────────────────────────────────────┐
   │  GmailTokenManager (singleton on app.state)                   │
   │  - In-memory {email → (access_token, expires_at)} cache       │
   │  - access_token(email): cache hit? else _refresh()            │
   │  - _refresh: load_credential → decrypt RT → POST token URL    │
   │    → cache (expires_at - 60s window)                          │
   └─────────────┬─────────────────────────────────────────────────┘
                 │  Bearer ya29.xxxxx
                 ▼
   ┌──────────────────────────────────────────────────────────────┐
   │  GmailRestClient (async ctx manager, httpx.AsyncClient(30s))  │
   │  - messages_list(q, page_token)  → /messages                  │
   │  - history_list(start_history_id, page_token) → /history      │
   │  - messages_get(mid) [metadata format, 5 quota units]         │
   └─────────────┬─────────────────────────────────────────────────┘
                 │
                 ▼
   ┌──────────────────────────────────────────────────────────────┐
   │  GmailSyncWorker  (per-account asyncio.Lock + global Sem(10)) │
   │  - first run: messages.list(newer_than:30d category:primary)  │
   │  - subsequent: history.list(start=cred.history_id)            │
   │  - gather messages_get under Semaphore(10)                    │
   │  - classify(from, subject, snippet) ⇒ heuristic               │
   │  - _persist_one ⇒ INSERT; swallow IntegrityError on dup       │
   │  - _update_cursor ⇒ cred.history_id, last_synced_at           │
   │  - broadcast_gmail_message_received per new row               │
   │  - broadcast_gmail_sync_status when batch done                │
   └─────────────┬───────────────────────────┬────────────────────┘
                 │ INSERT                    │ broadcast
                 ▼                           ▼
   ┌────────────────────────┐     ┌───────────────────────────────┐
   │ gmail_messages         │     │ WSMessage union (ws_models)   │
   │ id, gmail_message_id,  │     │ - GmailSyncStatus             │
   │ thread, account, from, │     │ - GmailMessageReceived        │
   │ from_domain, subject,  │     └────────────┬──────────────────┘
   │ snippet, received_at,  │                  │ manager.broadcast()
   │ category, confidence,  │                  ▼
   │ classified_by,         │     ┌───────────────────────────────┐
   │ ats_vendor, ...        │     │ ConnectionManager (ws.py)     │
   └────────────┬───────────┘     │ active_connections{id→WS}     │
                │                 └────────────┬──────────────────┘
                │                              │ ws.send_text(JSON)
                │                              ▼
                │                 ┌───────────────────────────────┐
                │                 │ FE: $lib/stores/websocket.ts  │
                │                 │ asWSMessage → switch on type  │
                │                 │  - gmail_message_received     │
                │                 │     → pushToast(...)          │
                │                 │  - gmail_sync_status          │
                │                 │     → console.debug only      │
                │                 └────────────┬──────────────────┘
                │                              │
                │  GET /api/correspondence/*   │ click toast / navigate
                ▼                              ▼
   ┌──────────────────────────────────────────────────────────────┐
   │  Frontend                                                     │
   │  - /inbox          → list_unlinked + LinkApplicationModal     │
   │  - /jobs/[id]      → "Linked Emails" tab → fetchThread(app)   │
   │  - /settings       → "Integrations" tab → GmailConnectCard    │
   └──────────────────────────────────────────────────────────────┘
                │  POST /api/correspondence/link
                ▼
   ┌──────────────────────────────────────────────────────────────┐
   │  application_correspondence (link table, FK CASCADE both)     │
   │  → Application.last_correspondence_at = now()                 │
   └──────────────────────────────────────────────────────────────┘
```

Three integration points worth noting:
- **`GmailTokenManager` lives on `app.state.gmail_token_manager`** ([`main.py:226`](../../../backend/main.py#L226)) — read by both the cron (`_run_gmail_poll`, [`main.py:46-66`](../../../backend/main.py#L46-L66)) and `/api/gmail/sync` ([`gmail.py:50-62`](../../../backend/api/gmail.py#L50-L62)).
- **`GmailSyncWorker` is imported at module level** ([`main.py:24-27`](../../../backend/main.py#L24-L27)) specifically so tests can patch `backend.main.GmailSyncWorker` — see `tests/test_gmail_scheduler.py:25`.
- **Fallback `broadcast_gmail_*` no-op shims** ([`sync.py:19-26`](../../../backend/gmail/sync.py#L19-L26)) keep `sync.py` importable when `backend.api.ws` fails to load.

---

## 3. OAuth — start, callback, disconnect

Router lives at [`backend/api/gmail_auth.py`](../../../backend/api/gmail_auth.py), prefix `/api/gmail`. Three endpoints + helpers.

**`GET /api/gmail/oauth/start`** ([`gmail_auth.py:68-81`](../../../backend/api/gmail_auth.py#L68-L81))
- `_ensure_oauth_configured()` → 503 if `GMAIL_CLIENT_ID` or `GMAIL_CLIENT_SECRET` missing (opt-in integration, mirrors `ADZUNA_*` / `SERPAPI_KEY` patterns).
- Builds the authorize URL with `access_type=offline`, `prompt=consent`, `scope=https://www.googleapis.com/auth/gmail.readonly`, signed `state`.
- 302 to `https://accounts.google.com/o/oauth2/v2/auth?…`.

**CSRF state via HMAC-SHA256** ([`gmail_auth.py:35-57`](../../../backend/api/gmail_auth.py#L35-L57))

```
state = "<nonce>.<ts>.<hmac>"
  nonce = secrets.token_urlsafe(16)
  ts    = int(time.time())
  hmac  = HMAC_SHA256(CREDENTIAL_KEY, f"{nonce}.{ts}")
```

- `_verify_state()` splits on `.`, rejects malformed tokens, rejects `age > 600s` (10-minute TTL), rejects `age < 0` (clock-skew/replay), recomputes the HMAC, compares via `hmac.compare_digest` (constant-time).
- Key reuses `settings.CREDENTIAL_KEY` — same secret as Fernet. One env var, two responsibilities. Acceptable for single-user; would want dedicated `OAUTH_STATE_KEY` for multi-tenant.
- Stdlib `hmac` + `secrets` only — no extra dep.

**`GET /api/gmail/oauth/callback?code&state[&error]`** ([`gmail_auth.py:84-128`](../../../backend/api/gmail_auth.py#L84-L128))
- `error=` query → log WARN, 302 `/settings?gmail_error=<error>`.
- Else verify state → 400 on mismatch/expired.
- POST `oauth2.googleapis.com/token` with `grant_type=authorization_code`. If no `refresh_token` returned (Google's "remembered consent" optimisation), 400 with hint. **This 400 is raised as `HTTPException` — user lands on raw FastAPI JSON, not the SPA** (see §15.7).
- GET `gmail.googleapis.com/.../profile` to discover the real `emailAddress` — we trust the identity Google billed against the consent.
- `save_credential()` → upsert by email.
- 302 `/settings?gmail_connected=1`.

**`POST /api/gmail/disconnect {email}`** ([`gmail_auth.py:131-146`](../../../backend/api/gmail_auth.py#L131-L146))
- **Revocation-before-delete order**: shipped code loads the row, decrypts the refresh token, `await revoke_refresh_token(rt)`, **then** `await delete_credential(...)`. If revoke fails the DB delete still proceeds (we don't want a dead row blocking a user retry). If revoke succeeds but DB delete fails, retry is harmless (upstream grant is already gone). The plan draft suggested the reverse order; shipped order is the safer one.
- `revoke_refresh_token()` ([`gmail/auth.py:69-75`](../../../backend/gmail/auth.py#L69-L75)) is **best-effort** — blanket `try/except` so a network failure to Google doesn't block local disconnect.
- Response is `{"removed": bool}` — frontend doesn't read it; just refreshes status.

Scopes: Phase 1 hard-codes `PHASE_1_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]`. Design §1.1 reserves `gmail.metadata` and `gmail.modify` for Phase 2, deliberately never `gmail.send`.

---

## 4. Credential storage — Fernet encryption

[`backend/gmail/credentials.py`](../../../backend/gmail/credentials.py) (68 lines, no surprises).

- `_fernet()` ([`credentials.py:13-19`](../../../backend/gmail/credentials.py#L13-L19)) reads `settings.CREDENTIAL_KEY` (a `SecretStr`), raises if empty.
- `encrypt_refresh_token` / `decrypt_refresh_token` are thin one-liners. Ciphertext is stored as URL-safe base64 in a `Text` column.
- `save_credential` is **upsert by email** — running OAuth again rotates the token in place, letting the user re-consent after a Google-side revocation without a manual DB scrub.
- `load_credential` returns `Optional[GmailCredential]`; `delete_credential` returns `bool`. Neither commits — caller manages the session.

The module mirrors `backend/models/user.py:SiteCredential` and reuses the **same `CREDENTIAL_KEY` Fernet key** — one master secret guards both browser cookies and Google refresh tokens. Design §9.5 lists this as the mitigation for "refresh-token exfiltration via DB read".

---

## 5. Token manager — `GmailTokenManager`

[`backend/gmail/auth.py`](../../../backend/gmail/auth.py) (76 lines). Per-process cache; one instance on `app.state.gmail_token_manager`.

- **`_CachedToken`** dataclass ([`auth.py:18-21`](../../../backend/gmail/auth.py#L18-L21)) stores `access_token` + `expires_at` (epoch seconds).
- **`access_token(email)`** ([`auth.py:35-40`](../../../backend/gmail/auth.py#L35-L40)) — cache hit if `expires_at - 60s > now`, else `_refresh()`. The 60-second `_REFRESH_BUFFER_SECONDS` prevents the race where a returned token expires mid-API-call.
- **`_refresh(email)`** ([`auth.py:42-66`](../../../backend/gmail/auth.py#L42-L66)) opens its own session, decrypts the refresh token, POSTs to `oauth2.googleapis.com/token` with `grant_type=refresh_token`, rewrites the cache. `expires_at` is `now + expires_in` (Google doesn't return absolute time).
- **`_clock` injection** ([`auth.py:31`](../../../backend/gmail/auth.py#L31)) lets tests fast-forward without `freezegun` — see [`test_gmail_auth.py:58`](../../../tests/test_gmail_auth.py#L58).
- **Lock semantics: NONE.** The cache is a plain `dict`; `access_token()` has no `asyncio.Lock`. Concurrent first-calls for the same email can race into `_refresh()` simultaneously — harmless for Phase 1 (Google issues two valid tokens, loser is overwritten). Add a per-email lock if multi-account/high-concurrency lands.
- **Missing-credential** raises `KeyError` ([`auth.py:46`](../../../backend/gmail/auth.py#L46)). Caught by the per-email `try/except` in `_run_gmail_poll` ([`main.py:62-66`](../../../backend/main.py#L62-L66)).

`revoke_refresh_token()` ([`auth.py:69-75`](../../../backend/gmail/auth.py#L69-L75)) posts to `oauth2.googleapis.com/revoke`, blanket-swallows exceptions.

---

## 6. Gmail REST client — `GmailRestClient`

[`backend/gmail/client.py`](../../../backend/gmail/client.py) (60 lines). Async context manager around exactly the three endpoints Phase 1 needs:

- **`messages_list(q?, page_token?)`** ([`client.py:25-36`](../../../backend/gmail/client.py#L25-L36)) → `GET /messages?maxResults=100&q=…`. First-run uses `q="newer_than:30d category:primary"` ([`sync.py:129`](../../../backend/gmail/sync.py#L129)).
- **`history_list(start_history_id, page_token?)`** ([`client.py:38-48`](../../../backend/gmail/client.py#L38-L48)) → `GET /history?startHistoryId=…&historyTypes=messageAdded`. Restricting to `messageAdded` skips label/read-state churn.
- **`messages_get(message_id)`** ([`client.py:50-59`](../../../backend/gmail/client.py#L50-L59)) → `format=metadata&metadataHeaders=From,To,Subject,Date`. Metadata format costs 5 quota units and never returns the body — design §9.1 ("no body persistence").

Auth header is injected **once at `__aenter__`** via `httpx.AsyncClient(timeout=30.0, headers=self._headers)`. The Bearer token lives in the per-instance client and is never re-set; the worker creates one client per `sync_now()`, well under the 1-hour token lifetime.

No rate-limit handling — no backoff, no 429 retry, no quota counter. Design §2.3 defers all of that. At < 500 emails/day a single user spends ~4-6 units/minute against a 250 units/user/sec ceiling.

---

## 7. Sync worker — `GmailSyncWorker`

[`backend/gmail/sync.py`](../../../backend/gmail/sync.py) (218 lines). The pipeline orchestrator.

### Locking model

- **Per-account `asyncio.Lock`** ([`sync.py:78-79`](../../../backend/gmail/sync.py#L78-L79)) via `_locks: dict[str, asyncio.Lock]` + `setdefault`. Overlapping `sync_now("u@e.com")` serialises; relevant because a previous sync can still be running across a 5-minute scheduler tick (slow Google, large backfill).
- **Process-wide `asyncio.Semaphore(10)`** ([`sync.py:76, 159`](../../../backend/gmail/sync.py#L76)) caps inflight `messages.get` calls at 10 — mirrors `CONCURRENCY_GEMINI`.

### First-run backfill vs. delta

```python
if start_history_id is None:
    msg_ids, new_history_id = await self._first_run_ids(client)
else:
    msg_ids, new_history_id = await self._delta_ids(client, start_history_id)
```

- **`_first_run_ids`** ([`sync.py:123-137`](../../../backend/gmail/sync.py#L123-L137)) pages `messages.list(q="newer_than:30d category:primary")`, bounded by `settings.GMAIL_BACKFILL_DAYS`. `category:primary` skips Promotions/Social — a sane noise filter, but it **will miss ATS automated mail that Gmail mis-buckets into Promotions** (a real failure mode worth flagging for Phase 2).
- **`_delta_ids`** ([`sync.py:139-156`](../../../backend/gmail/sync.py#L139-L156)) pages `history.list(startHistoryId=cred.history_id)`, extracting `messagesAdded.message.id`. Duplicates arrive when labels change — handled by the dedup test.

### `_safe_get` and `_persist_one`

- **`_safe_get`** ([`sync.py:158-164`](../../../backend/gmail/sync.py#L158-L164)) wraps `messages.get` in semaphore + `try/except`; a bad ID logs WARN and returns `None`, batch continues.
- **`_persist_one`** ([`sync.py:166-206`](../../../backend/gmail/sync.py#L166-L206)):
  1. Pull headers (`_header` does case-insensitive lookup).
  2. Parse `Date` via `email.utils.parsedate_to_datetime`, fall back to `internalDate` (epoch ms), then `now()`. Always returns naive UTC ([`sync.py:53-67`](../../../backend/gmail/sync.py#L53-L67)) — matches the codebase's legacy convention.
  3. `classify(from_address, subject, snippet)` → `(category, confidence, vendor)`.
  4. Build `GmailMessage`, `add`, **`commit` inside `try/except IntegrityError`** — dup → rollback, return `False`. Relies on `gmail_message_id` UNIQUE catching collisions at INSERT.
  5. On success, broadcast `gmail_message_received`.

`_persist_one` opens its own short-lived session per row — keeps sync transactions narrow.

### Cursor update + WS narration

After the batch, `_update_cursor` writes `cred.history_id` + `cred.last_synced_at`, then `broadcast_gmail_sync_status(progress=1.0)`. No partial-progress narration — Phase 1 only broadcasts at end-of-batch.

---

## 8. Heuristic classifier — Phase 1 brain

[`backend/gmail/classifier_heuristics.py`](../../../backend/gmail/classifier_heuristics.py) (105 lines), single `classify()`. Three tables:

- **`ATS_DOMAINS`** (13 entries, [`classifier_heuristics.py:14-28`](../../../backend/gmail/classifier_heuristics.py#L14-L28)) — Greenhouse, Lever, Workday, Ashby, Workable, SmartRecruiters, Taleo, iCIMS, BambooHR. **Contains** match, so `careers.acme.myworkday.com` hits. (`oraclecloud.com` from the design was dropped — only `taleo.net` survives for Taleo/Oracle.)
- **`NOISE_DOMAINS`** ([`classifier_heuristics.py:31-35`](../../../backend/gmail/classifier_heuristics.py#L31-L35)) — hand-curated digest list. Noise wins immediately (`("noise", 0.85, None)`). `/api/correspondence/unlinked` filters `category="noise"` ([`correspondence.py:67-78`](../../../backend/api/correspondence.py#L67-L78)) so newsletters never clutter the inbox.
- **Regex lists**: `REJECTION_PATTERNS`, `INTERVIEW_PATTERNS`, `OFFER_PATTERNS` ([`classifier_heuristics.py:37-65`](../../../backend/gmail/classifier_heuristics.py#L37-L65)). All `re.IGNORECASE`, word-boundary anchored. Calendly/SavvyCal/Cal.com URLs flip to `interview_invite`.

**Precedence** ([`classifier_heuristics.py:78-104`](../../../backend/gmail/classifier_heuristics.py#L78-L104)):
1. Noise domain → `noise` (0.85)
2. Compute `vendor` via ATS_DOMAINS
3. Rejection pattern → `rejection` (0.85, vendor)
4. Offer pattern → `offer` (0.85, vendor)
5. Interview pattern → `interview_invite` (0.85, vendor)
6. Vendor only → `ats_ack` (**0.7**, vendor) — the lone sub-cap confidence
7. Else → `unknown` (0.0, None)

**Why the 0.85 cap?** Design §4.1: keep headroom for the Phase-2 LLM to override deterministic guesses. `_vendor_for` iterates the dict in insertion order; CPython 3.7+ guarantees this but it's a footgun if someone alphabetises.

---

## 9. Scheduler — APScheduler in-process

Wired in [`backend/main.py:230-241`](../../../backend/main.py#L230-L241):

```python
scheduler = AsyncIOScheduler()
interval = max(1, int(settings.GMAIL_POLL_INTERVAL_MINUTES))
scheduler.add_job(_run_gmail_poll, "interval", minutes=interval, id="gmail_poll")
scheduler.start()
app.state.scheduler = scheduler
```

- `max(1, …)` floors the interval at 1 minute — guards against env-var typos.
- Default `GMAIL_POLL_INTERVAL_MINUTES = 5` ([`config.py:54`](../../../backend/config.py#L54)).
- `_run_gmail_poll` ([`main.py:46-66`](../../../backend/main.py#L46-L66)) iterates **every** enabled `GmailCredential`, constructs one `GmailSyncWorker`, calls `worker.sync_now(email)` per account inside `try/except`. Already multi-account-friendly at the cron level.
- **Shutdown is non-blocking**: `scheduler.shutdown(wait=False)` ([`main.py:247-252`](../../../backend/main.py#L247-L252)). In-flight syncs are not awaited, but `_persist_one` commits one row at a time and the IntegrityError swallow makes restart idempotent.

Tests in [`tests/test_gmail_scheduler.py`](../../../tests/test_gmail_scheduler.py) patch `backend.main.GmailSyncWorker` and call `_run_gmail_poll()` directly.

---

## 10. REST API surface

| Method | Path | File:line | Request body / query | Response |
| --- | --- | --- | --- | --- |
| `GET` | `/api/gmail/oauth/start` | [`gmail_auth.py:68`](../../../backend/api/gmail_auth.py#L68) | — | 302 to Google authorize URL |
| `GET` | `/api/gmail/oauth/callback` | [`gmail_auth.py:84`](../../../backend/api/gmail_auth.py#L84) | `code, state, [error]` query | 302 to `/settings?gmail_connected=1` (or 400 on bad state) |
| `POST` | `/api/gmail/disconnect` | [`gmail_auth.py:135`](../../../backend/api/gmail_auth.py#L135) | `{"email": str}` | `{"removed": bool}` |
| `GET` | `/api/gmail/status` | [`gmail.py:24`](../../../backend/api/gmail.py#L24) | — | `GmailStatusOut` (see below) |
| `POST` | `/api/gmail/sync` | [`gmail.py:50`](../../../backend/api/gmail.py#L50) | — | `{"synced": int}` (404 if no account) |
| `GET` | `/api/correspondence/unlinked` | [`correspondence.py:65`](../../../backend/api/correspondence.py#L65) | — | `{"items": list[UnlinkedItemOut]}` (LIMIT 200, no noise) |
| `GET` | `/api/correspondence/{application_id}` | [`correspondence.py:81`](../../../backend/api/correspondence.py#L81) | path int | `CorrespondenceThreadOut` (oldest-first) |
| `POST` | `/api/correspondence/link` | [`correspondence.py:97`](../../../backend/api/correspondence.py#L97) | `{"application_id": int, "gmail_message_id": int}` | 201 + `CorrespondenceLinkOut` (or 404/409) |
| `DELETE` | `/api/correspondence/{link_id}` | [`correspondence.py:131`](../../../backend/api/correspondence.py#L131) | path int | 204 |

`GmailStatusOut` returns `{connected, email_address, last_synced_at, history_id, message_count, enabled}` ([`gmail.py:15-22`](../../../backend/api/gmail.py#L15-L22)). It calls `select(GmailCredential).limit(1)` — Phase-1-single-account by design (see §15.2).

`CorrespondenceLinkOut` ([`correspondence.py:39-49`](../../../backend/api/correspondence.py#L39-L49)) always returns `link_method="manual"`, `link_confidence=1.0`, `confirmed_by_user=True` — link-quality metadata is shaped for Phase 2 auto-link.

The link endpoint writes `app.last_correspondence_at = _now()` inside the same transaction ([`correspondence.py:121-127`](../../../backend/api/correspondence.py#L121-L127)). The 409 on `IntegrityError` is essentially unreachable today (no unique constraint on `(application_id, message_id)`), but cheap insurance.

**Deferred to Phase 2:** `/api/correspondence/{id}/confirm` (auto-link confirmation), `/api/webhooks/gmail` (Pub/Sub push), `/api/gmail/export` (GDPR).

---

## 11. Data model

Three tables in [`backend/models/gmail.py`](../../../backend/models/gmail.py), all via `Base.metadata.create_all` (no Alembic). The added `applications.last_correspondence_at` column is handled by `_migrate_add_columns` ([`database.py:80`](../../../backend/database.py#L80)).

### `gmail_credentials` ([`models/gmail.py:28-47`](../../../backend/models/gmail.py#L28-L47))
- `email_address: String, unique=True` — canonical key, multi-account-ready.
- `encrypted_refresh_token: Text` (Fernet); `scopes: String` (space-joined).
- `history_id: Optional[String]` — `NULL` until first sync, then ratchets.
- `enabled: bool` — pause without revoking; scheduler filters on this.
- `last_synced_at`, `created_at`, `updated_at` (naive UTC).

### `gmail_messages` ([`models/gmail.py:50-84`](../../../backend/models/gmail.py#L50-L84))
- `gmail_message_id: String, unique=True` — the dedup guarantor.
- `gmail_thread_id` (indexed), `account_email`, `from_address`, `from_domain` (indexed), `to_address`, `subject`, `snippet`, `received_at` (indexed).
- Composite index `ix_gmail_messages_account_received` on `(account_email, received_at)`.
- Phase 1 classification: `category` (indexed), `category_confidence`, `classified_by`, `ats_vendor`.
- Phase 2 enrichment columns declared now as nullable (`extracted_company`, `extracted_role`, `extracted_interview_at`, `extracted_salary_text`, `extracted_questions_json` JSON) — saves a migration.

### `application_correspondence` ([`models/gmail.py:87-110`](../../../backend/models/gmail.py#L87-L110))
- `application_id` ForeignKey CASCADE; `message_id` ForeignKey CASCADE — deleting either parent cleans up links.
- `gmail_thread_id` denormalised; `direction`, `link_confidence`, `link_method`, `confirmed_by_user`, `created_at`.
- Composite index `ix_application_correspondence_app_created`.

**No unique constraint on `(application_id, message_id)`** — design §5.4 calls this "FK-only-by-convention". The same message can in theory be linked twice; Phase 2's auto-link must add a constraint or a pre-flight check.

---

## 12. WebSocket events

Two new variants on `WSMessage` ([`ws_models.py:124-139`](../../../backend/api/ws_models.py#L124-L139)):

```python
class GmailSyncStatus(BaseModel):
    type: Literal["gmail_sync_status"] = "gmail_sync_status"
    last_history_id: str | None = None
    messages_synced: int = 0
    progress: float = 0.0

class GmailMessageReceived(BaseModel):
    type: Literal["gmail_message_received"] = "gmail_message_received"
    gmail_message_id: str
    from_address: str
    subject: str | None = None
    category: str | None = None
    category_confidence: float | None = None
    linked_application_id: int | None = None
    link_confidence: float | None = None
```

`linked_application_id` / `link_confidence` are nullable for Phase 2's auto-linker. Broadcast helpers co-located in [`ws.py:183-204`](../../../backend/api/ws.py#L183-L204). Mirror TS types in [`frontend/src/lib/types/ws.ts:107-124`](../../../frontend/src/lib/types/ws.ts#L107-L124) — drift detection is manual until codegen lands.

---

## 13. Frontend integration

### Settings → Integrations tab
[`settings/+page.svelte:79-86, 1051-1057`](../../../frontend/src/routes/settings/+page.svelte#L1051-L1057) mounts `<GmailConnectCard />` ([`GmailConnectCard.svelte`](../../../frontend/src/lib/components/GmailConnectCard.svelte), 152 LOC). Three states: loading skeleton → connected (account email, msg count, last sync, Sync/Disconnect buttons) → not-connected (Connect button does `window.location.href = '/api/gmail/oauth/start'`). `doSync()` uses `alert(...)` for the result count; `doDisconnect()` uses native `confirm(...)`. Polish deferred.

### `/inbox` page
[`inbox/+page.svelte`](../../../frontend/src/routes/inbox/+page.svelte) (162 LOC). `fetchUnlinked()` returns the LIMIT-200 non-noise list. Each row renders the category as a coloured pill — note the FE includes a `recruiter_outreach` colour the backend never emits (dead string in Phase 1). "Link to app…" opens `<LinkApplicationModal />` ([`LinkApplicationModal.svelte`](../../../frontend/src/lib/components/LinkApplicationModal.svelte), 194 LOC) which loads `/api/applications`, filters client-side, POSTs to `/api/correspondence/link`. ESC + backdrop click both close.

### Job detail — `Linked Emails` tab
[`jobs/[id]/+page.svelte:316-355, 441-491`](../../../frontend/src/routes/jobs/[id]/+page.svelte#L316-L491). Route keyed by `matchId`; resolving the matching `Application` requires `GET /api/applications?limit=200` + client-side `find(a => a.job_match_id === matchId)`. Inelegant (a `/by-match/{id}` route would be one round-trip) but pragmatic — the apps list is cached for `/tracker`. Empty state when no application yet: "Apply to this job to start tracking linked emails."

### Toast store + WS wiring
- [`stores/toast.ts`](../../../frontend/src/lib/stores/toast.ts) — writable list + `pushToast(msg, {kind, duration, href, hrefLabel})`, rendered in `+layout.svelte:62-100`.
- [`stores/websocket.ts:82-102`](../../../frontend/src/lib/stores/websocket.ts#L82-L102) maps `gmail_message_received` to a toast. Tone: `rejection → warning`, `offer|interview_invite → success`, else `info`. Deep-link: `linked_application_id !== null → /tracker`, else `/inbox`. Today every Phase 1 message is `null`-linked, so toasts always point at `/inbox`.
- `gmail_sync_status` is `console.debug` only — TODO'd to "Sidebar pulse can come later".

### Nav wiring
`<Inbox>` link added at [`+layout.svelte:43`](../../../frontend/src/routes/+layout.svelte#L43), between `Tracker` and `CV Manager`. Permanent, no badge.

---

## 14. Test surface — 45 tests, mocking patterns

| File | Tests | Strategy |
| --- | --- | --- |
| [`test_gmail_models.py`](../../../tests/test_gmail_models.py) | 4 | Real SQLite roundtrip via `init_db()` |
| [`test_gmail_credentials.py`](../../../tests/test_gmail_credentials.py) | 4 | Real Fernet, real DB |
| [`test_gmail_auth.py`](../../../tests/test_gmail_auth.py) | 3 | `monkeypatch.setattr("backend.gmail.auth.settings.…")` + `patch("backend.gmail.auth.httpx.AsyncClient")` |
| [`test_gmail_oauth_routes.py`](../../../tests/test_gmail_oauth_routes.py) | 5 | `monkeypatch.setenv` + `cfg.settings = cfg._load_settings()` + httpx mock |
| [`test_gmail_classifier.py`](../../../tests/test_gmail_classifier.py) | 14 (11 parametrised) | Pure function |
| [`test_gmail_sync.py`](../../../tests/test_gmail_sync.py) | 4 | `_FakeClient` async-ctx-mgr stub + `patch("backend.gmail.sync.GmailRestClient")` + `AsyncMock` token mgr |
| [`test_gmail_scheduler.py`](../../../tests/test_gmail_scheduler.py) | 2 | `patch("backend.main.GmailSyncWorker")` |
| [`test_gmail_ws.py`](../../../tests/test_gmail_ws.py) | 2 | `patch("backend.gmail.sync.broadcast_*")` |
| [`test_correspondence_api.py`](../../../tests/test_correspondence_api.py) | 6 | `TestClient(app)` + `asyncio.run(_seed_*)`; no HTTP mocking |
| [`test_gmail_smoke.py`](../../../tests/test_gmail_smoke.py) | 1 (e2e) | All of the above + `_wipe()` cleanup at fixture setup |

Coverage spans schema, Fernet, token cache, OAuth routes, all 8 classifier categories with the confidence-cap invariant, backfill/delta/dedup/disabled-skip, scheduler iteration, WS broadcast, correspondence CRUD with `last_correspondence_at` side-effect, and one full end-to-end happy path.

**Fixture isolation pattern:** every file uses `@pytest.fixture(autouse=True) async def _db(): await init_db()`. The test DB is a single SQLite file across the pytest session, so tests namespace their rows by prefix: `creds-`, `auth-u1-2@`, `sync-u1-4@e.com`, `corr-m*`, `sched-a/b/c@`, `ws-u@`, `smoke@`. The smoke test had to add a `_wipe()` step ([`test_gmail_smoke.py:36-50`](../../../tests/test_gmail_smoke.py#L36-L50)) because earlier tests leave credential rows that pollute `select(GmailCredential).limit(1)`. The `gm-12 fixup` commit `983cc7b` ("call init_db() inside the wipe fixture") confirms the isolation drift is real — a hint that pytest-xdist parallelism would crash and that per-test SQLite would be cleaner long-term.

---

## 15. Critique — severity-tagged

### 15.1 [Medium] `settings` import inconsistency between modules

[`backend/gmail/auth.py:9`](../../../backend/gmail/auth.py#L9) and [`backend/gmail/sync.py:11`](../../../backend/gmail/sync.py#L11) do `from backend.config import settings` (direct symbol import); [`backend/api/gmail_auth.py:16`](../../../backend/api/gmail_auth.py#L16) does `from backend import config as _config` then references `_config.settings.…`. The styles diverge under test patching: `monkeypatch.setattr("backend.config.settings", …)` doesn't propagate to `auth.py`/`sync.py` after they've imported the symbol. The smoke test ([`test_gmail_smoke.py:28-29`](../../../tests/test_gmail_smoke.py#L28-L29)) works around this with `monkeypatch.setattr(_gmail_auth, "settings", cfg.settings)` — an obvious "this should not be necessary" workaround. Combined with the `cfg.settings = cfg._load_settings()` reload trick in OAuth-route tests, there are now three different ways to swap settings; only some propagate. Standardise on `_config.settings.*` and the workaround disappears.

### 15.2 [Medium] Single-account assumption baked in two surfaces

- [`backend/api/gmail.py:26, 53`](../../../backend/api/gmail.py#L26): `select(GmailCredential).limit(1)` powers both `/status` and `/sync`. Whichever row happens to come first wins.
- [`GmailConnectCard.svelte:103`](../../../frontend/src/lib/components/GmailConnectCard.svelte#L103) renders one account; no "add another" affordance.
- The schema and `_run_gmail_poll` are already multi-account-ready. But the unlinked-list endpoint doesn't filter by account — multi-account needs query-param or per-account scoping.
- The smoke test's `_wipe()` ([`test_gmail_smoke.py:46-49`](../../../tests/test_gmail_smoke.py#L46-L49)) exists to dodge the single-row assumption. The test is telling us the constraint is fragile.

### 15.3 [Low] Heuristic confidence cap leaves an unused band

`category_confidence` is stored but never consulted by current code (only `category != "noise"` filters the inbox list). The 0.85 cap is a forward-compatible promise for Phase 2's LLM tier. Until Phase 2 ships, the 0.85-1.0 band is unused. `category="unknown"` rows still surface in `/api/correspondence/unlinked` — a `friend@gmail.com` "lunch?" email will appear in the inbox. Minor UX clutter, but in line with Phase 1's "show too much rather than too little" stance.

### 15.4 [Low] `_persist_one` IntegrityError swallow — legit dedup

[`sync.py:194-198`](../../../backend/gmail/sync.py#L194-L198): `try: commit() except IntegrityError: rollback()`. The only constraint a `GmailMessage` insert can violate is the unique `gmail_message_id` — so it's dedup, not hiding bugs. Dedup test at [`test_gmail_sync.py:132-155`](../../../tests/test_gmail_sync.py#L132-L155) exercises the path. A dedup-swallowed message correctly does NOT re-broadcast `gmail_message_received` (the broadcast is after the commit-success return). Safe.

### 15.5 [Medium] Polling-only — when does this hurt?

Default 5-minute polling means a recruiter reply at T+0 is invisible for T+5 to T+10 min. Quota is trivial (576 units/day). Painful when users start expecting "JobPilot saw the reply before I did" magic — typically once interview invitations land. Phase 2 push (Pub/Sub) requires GCP billing, a public webhook URL (ngrok-style tunnelling for local installs), OIDC JWT verification, and a `users.watch` daily renewer. All deferred per design Q3. For Phase 1 — read-only, manual link — polling is the right default.

### 15.6 [Info] Phase 2 deferrals

**Clean deferrals** (drop-in for Phase 2):
- LLM classifier tiers — `classified_by` column already accepts `flash_lite | pro | manual`.
- Enrichment fields — declared NULL-able now.
- `link_method` extensibility — free-text column.
- Status state machine — `Application.status` and `ApplicationEvent` are already free-text/append-only.

**Less clean**:
- No unique constraint on `(application_id, message_id)` — design §5.4 punted to "application-layer" but Phase 2 auto-link will need a DB constraint or pre-flight check.
- Multi-account UX gaps (§15.2).
- **`watch_expiration` column missing** from shipped `GmailCredential` — design §1.2 specified it; the shipped model ([`models/gmail.py:42-47`](../../../backend/models/gmail.py#L42-L47)) doesn't have it. One migration when Phase 2 ships push.

### 15.7 [Medium] OAuth callback error paths — silent FE failures

Five failure modes in [`gmail_auth.py:86-128`](../../../backend/api/gmail_auth.py#L86-L128):

1. `error=` query → 302 `/settings?gmail_error=<err>`. **But the settings page has no handler for `gmail_error`** — silent on the FE. User lands on `/settings` with no indication.
2. Bad/expired state → raw FastAPI 400 JSON. Should redirect with `?gmail_error=invalid_state`.
3. No `refresh_token` returned (Google's "remembered consent" optimisation, the most common real failure) → 400 JSON with a help string. Helpful text, wrong format — user sees raw JSON.
4. `raise_for_status()` on token/profile → uncaught httpx → global 500 handler. User gets nothing actionable.
5. The `error=` branch returns 302 before `_ensure_oauth_configured()` — minor.

Fix: every error path 302s to `/settings?gmail_error=<code>`; settings page renders a banner.

### 15.8 [Medium] Toast strategy is invisible when user is on another tab

Toasts auto-dismiss in **5 s** by default ([`toast.ts:32`](../../../frontend/src/lib/stores/toast.ts#L32)); the Gmail handler doesn't override. If the user is on a different tab when a `gmail_message_received` arrives, the WS message lands, the toast is pushed, the 5-second `setTimeout` fires, and the toast is gone by the time the user returns. **No persistence** — no notification centre, no badge on `<Inbox>`, no unread count anywhere. The only durable signal is to navigate to `/inbox`.

Cheap fixes available:
- Sidebar `<Inbox>` link could badge with the unlinked count.
- `gmail_message_received` toasts could be `duration: 0` (sticky).
- `Notification.requestPermission()` + `new Notification()` for off-tab.

None are wired. And `gmail_sync_status` is `console.debug`-only — a 0-message sync is completely invisible.

### 15.9 [Low] Test fixture collision pattern

The prefix convention (`creds-`, `auth-u1@`, `sync-u1@`, `corr-m1`, `sched-a@`, `ws-u@`, `smoke@`) is the only isolation mechanism for tests that share a single SQLite file. Functional but brittle: a new test forgetting the prefix breaks existing ones; the smoke test's `_wipe()` exists precisely because of cross-test pollution; pytest-xdist would crash the moment two tests insert overlapping rows. A per-test in-memory SQLite would eliminate the dance — global testing-hygiene debt that Phase 1 made visibly worse.

### 15.10 [Low] `last_correspondence_at` is write-only today

[`follow_up.py:62-69`](../../../backend/applier/follow_up.py#L62-L69) scans `status == "applied" AND applied_at <= cutoff AND NOT has(follow_up_due)`. The new `last_correspondence_at` column is **never consulted**. So if a recruiter replies and the user links the email (`last_correspondence_at` advances), a follow-up reminder still fires N days after `applied_at`. The right semantics — "applied_at + N OR last_correspondence_at + N, whichever is later" — is a one-line tweak to the follow-up query that nothing in Phase 1 did.

The FE also doesn't read it — `/tracker` orders by `applied_at` / `created_at`, not activity. The column is currently **write-only**, deliberately so. The migration is cheap to add now; consumers are Phase 2.

### 15.11 [Low] `messages_get` is metadata-only

Design §9.1 — no body persistence. Classifier feeds on `(from, subject, snippet ≤ 200 chars)`. Phase 2 LLM tiers will need a separate `format=full` path for ambiguous senders. Worth noting; not a Phase 1 bug.

### 15.12 [Low] No rate-limit or quota awareness

No backoff, no 429 retry, no quota counter. Design §2.3 deferred. Invisible at < 500 emails/day; an enterprise multi-user backfill would hit the 250-units/user/sec cap. Wrap with exponential backoff before scaling.

---

## 16. Inventory — every file in scope

### Backend Python — Gmail core
- [`backend/gmail/__init__.py`](../../../backend/gmail/__init__.py) — empty package marker.
- [`backend/gmail/credentials.py`](../../../backend/gmail/credentials.py) (68 LOC) — Fernet encrypt/decrypt + save/load/delete `GmailCredential`.
- [`backend/gmail/auth.py`](../../../backend/gmail/auth.py) (76 LOC) — `GmailTokenManager` cache, `revoke_refresh_token` helper, TOKEN_URL constant.
- [`backend/gmail/client.py`](../../../backend/gmail/client.py) (60 LOC) — `GmailRestClient` async-ctx-mgr wrapping messages.list/history.list/messages.get.
- [`backend/gmail/classifier_heuristics.py`](../../../backend/gmail/classifier_heuristics.py) (105 LOC) — `ATS_DOMAINS`, `NOISE_DOMAINS`, regex tables, `classify()` function.
- [`backend/gmail/sync.py`](../../../backend/gmail/sync.py) (218 LOC) — `GmailSyncWorker` orchestrating poll → fetch → classify → persist → broadcast.

### Backend Python — REST routers
- [`backend/api/gmail_auth.py`](../../../backend/api/gmail_auth.py) (147 LOC) — OAuth start/callback/disconnect.
- [`backend/api/gmail.py`](../../../backend/api/gmail.py) (63 LOC) — `/api/gmail/status` and `/api/gmail/sync`.
- [`backend/api/correspondence.py`](../../../backend/api/correspondence.py) (141 LOC) — unlinked / thread / link / unlink.

### Backend Python — models & wiring
- [`backend/models/gmail.py`](../../../backend/models/gmail.py) (111 LOC) — `GmailCredential`, `GmailMessage`, `ApplicationCorrespondence`.
- [`backend/models/application.py:29`](../../../backend/models/application.py#L29) — added `Application.last_correspondence_at`.
- [`backend/database.py:80`](../../../backend/database.py#L80) — `_migrate_add_columns` entry for the new column.
- [`backend/models/__init__.py`](../../../backend/models/__init__.py) — re-exports the three Gmail models.
- [`backend/api/ws_models.py:124-139`](../../../backend/api/ws_models.py#L124-L139) — `GmailSyncStatus` + `GmailMessageReceived` added to `WSMessage` union.
- [`backend/api/ws.py:183-204`](../../../backend/api/ws.py#L183-L204) — `broadcast_gmail_sync_status` / `broadcast_gmail_message_received` helpers.
- [`backend/main.py:24-27, 46-66, 222-241, 247-252`](../../../backend/main.py#L46-L66) — module-level `GmailSyncWorker` import, `_run_gmail_poll` cron entrypoint, `GmailTokenManager` singleton init, APScheduler boot, shutdown.
- [`backend/config.py:46-54`](../../../backend/config.py#L46-L54) — `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REDIRECT_URI`, `GMAIL_BACKFILL_DAYS`, `GMAIL_POLL_INTERVAL_MINUTES` settings.

### Frontend TypeScript / Svelte
- [`frontend/src/lib/api/gmail.ts`](../../../frontend/src/lib/api/gmail.ts) (65 LOC) — `fetchGmailStatus`, `forceSync`, `disconnect`, `fetchUnlinked`, `linkMessage`, `fetchThread` plus the matching TS types.
- [`frontend/src/lib/types/ws.ts:107-124, 194-195`](../../../frontend/src/lib/types/ws.ts#L107-L124) — `GmailSyncStatusMsg` and `GmailMessageReceivedMsg` added to `WSMessage` union + narrow helper.
- [`frontend/src/lib/stores/toast.ts`](../../../frontend/src/lib/stores/toast.ts) (43 LOC) — minimal toast queue (`pushToast`, `dismissToast`, `toasts` writable).
- [`frontend/src/lib/stores/websocket.ts:82-102`](../../../frontend/src/lib/stores/websocket.ts#L82-L102) — handle `gmail_message_received` → toast + handle `gmail_sync_status` → console.debug.
- [`frontend/src/lib/components/GmailConnectCard.svelte`](../../../frontend/src/lib/components/GmailConnectCard.svelte) (152 LOC) — Connect / Disconnect / Sync-now card on Settings → Integrations.
- [`frontend/src/lib/components/LinkApplicationModal.svelte`](../../../frontend/src/lib/components/LinkApplicationModal.svelte) (194 LOC) — searchable Application picker used by Inbox.
- [`frontend/src/routes/inbox/+page.svelte`](../../../frontend/src/routes/inbox/+page.svelte) (162 LOC) — full Inbox page; lists unlinked + Link-to-app flow.
- [`frontend/src/routes/jobs/[id]/+page.svelte:316-355, 441-491`](../../../frontend/src/routes/jobs/[id]/+page.svelte#L316-L491) — Linked Emails tab + application-resolution logic.
- [`frontend/src/routes/settings/+page.svelte:6, 79-86, 1051-1057`](../../../frontend/src/routes/settings/+page.svelte#L1051-L1057) — Integrations tab + `<GmailConnectCard />` mount.
- [`frontend/src/routes/+layout.svelte:43, 62-100`](../../../frontend/src/routes/+layout.svelte#L43) — `<Inbox>` nav link and global toast stack.

### Tests
- [`tests/test_gmail_models.py`](../../../tests/test_gmail_models.py) (74 LOC, 4 tests) — schema + indexes + roundtrip + unique constraint.
- [`tests/test_gmail_credentials.py`](../../../tests/test_gmail_credentials.py) (63 LOC, 4 tests) — Fernet + save/load/delete + upsert.
- [`tests/test_gmail_auth.py`](../../../tests/test_gmail_auth.py) (74 LOC, 3 tests) — token manager cache + refresh + missing-credential.
- [`tests/test_gmail_oauth_routes.py`](../../../tests/test_gmail_oauth_routes.py) (125 LOC, 5 tests) — `/start` redirect, 503 unconfigured, `/callback` happy path, bad state 400, `/disconnect`.
- [`tests/test_gmail_classifier.py`](../../../tests/test_gmail_classifier.py) (67 LOC, 14 tests) — heuristic table + confidence cap + ats default + rejection beats default.
- [`tests/test_gmail_sync.py`](../../../tests/test_gmail_sync.py) (171 LOC, 4 tests) — first-run + delta + dedup + disabled-credential skip.
- [`tests/test_gmail_scheduler.py`](../../../tests/test_gmail_scheduler.py) (52 LOC, 2 tests) — `_run_gmail_poll` iterates enabled, skips disabled.
- [`tests/test_gmail_ws.py`](../../../tests/test_gmail_ws.py) (69 LOC, 2 tests) — broadcasts fire + union includes variants.
- [`tests/test_correspondence_api.py`](../../../tests/test_correspondence_api.py) (115 LOC, 6 tests) — unlinked filter + link side-effect + ordering + unlink + status fallback.
- [`tests/test_gmail_smoke.py`](../../../tests/test_gmail_smoke.py) (148 LOC, 1 test) — end-to-end happy path.

### Documentation
- [`docs/reports/2026-05-22-audit/03-gmail-integration-design.md`](../2026-05-22-audit/03-gmail-integration-design.md) — original design proposal (825 LOC), the canonical spec.
- [`docs/superpowers/plans/2026-05-23-gmail-phase-1.md`](../../superpowers/plans/2026-05-23-gmail-phase-1.md) — 12-task implementation plan with every test, file, and commit message pre-drafted.

---

**Bottom line.** Phase 1 is a tight, deliberately scope-bounded slice. The bones are correct (encrypted creds, history-id cursor, dedup-via-unique-key, per-account lock + global semaphore, WS narration); the deferrals are explicit and clean; the test surface is strong for a sprint shipped in a single day. The visible debt items — settings-import inconsistency, single-account leakage in two surfaces, OAuth-error UX, dead-write `last_correspondence_at` — are all one-PR fixes that should pair naturally with Phase 2's first wave (push subscriptions, LLM tiers, auto-link). The 5-minute polling latency and the toast-only delivery model are real UX gaps but reasonable for "ship the CRM bones first, decorate later."
