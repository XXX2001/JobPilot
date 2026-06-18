# Gmail integration

JobPilot links one Gmail inbox over OAuth, syncs message metadata on a poll cron, and heuristically classifies each message (ATS-ack, rejection, interview, offer) so application activity can be tracked from email. This is the "Phase 1" implementation: read-only scope, metadata-only storage, deterministic classifier.

All code lives under `backend/gmail/` plus two API routers (`backend/api/gmail_auth.py`, `backend/api/gmail.py`), the models in `backend/models/gmail.py`, and the cron wiring in `backend/main.py`.

## Architecture at a glance

| Concern | Module | Key symbols |
| --- | --- | --- |
| OAuth start/callback/disconnect | `backend/api/gmail_auth.py` | `oauth_start`, `oauth_callback`, `disconnect` |
| Status + manual sync API | `backend/api/gmail.py` | `status`, `sync_now` |
| Credential storage + Fernet | `backend/gmail/credentials.py` | `save_credential`, `load_credential`, `encrypt_refresh_token` |
| Access-token cache + refresh | `backend/gmail/auth.py` | `GmailTokenManager`, `revoke_refresh_token` |
| Gmail REST wrapper | `backend/gmail/client.py` | `GmailRestClient` |
| Sync worker (backfill + delta) | `backend/gmail/sync.py` | `GmailSyncWorker` |
| Heuristic classifier | `backend/gmail/classifier_heuristics.py` | `classify`, `_vendor_for` |
| DB models | `backend/models/gmail.py` | `GmailCredential`, `GmailMessage`, `ApplicationCorrespondence` |
| Poll cron entrypoint | `backend/main.py` | `_run_gmail_poll`, `lifespan` |

## Configuration (`GMAIL_*` settings)

Defined in `backend/config.py:77-81`:

| Setting | Default | Purpose |
| --- | --- | --- |
| `GMAIL_CLIENT_ID` | `""` | Google OAuth client ID |
| `GMAIL_CLIENT_SECRET` | `SecretStr("")` | OAuth client secret |
| `GMAIL_REDIRECT_URI` | `http://localhost:8000/api/gmail/oauth/callback` | Must match the Google console redirect |
| `GMAIL_BACKFILL_DAYS` | `30` | How far back the first sync reaches |
| `GMAIL_POLL_INTERVAL_MINUTES` | `5` | APScheduler poll cadence |

`CREDENTIAL_KEY` (`backend/config.py:27`) is the Fernet key used to encrypt refresh tokens at rest. If unset it is **auto-generated and persisted to `.env`** on first launch (`backend/config.py:200-218`), so no installer step is needed. The same key signs the OAuth `state` token (see below).

## OAuth flow

The three-leg flow is read-only (`PHASE_1_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]`, `backend/api/gmail_auth.py:31`).

1. **`GET /api/gmail/oauth/start`** (`gmail_auth.py:68`) — verifies the client is configured (`_ensure_oauth_configured`, returns `503` otherwise), then 302-redirects to Google's consent screen with `access_type=offline` and `prompt=consent` (forces a refresh token to be issued). The `state` param is an HMAC-signed token.
2. **`GET /api/gmail/oauth/callback`** (`gmail_auth.py:84`) — on `error`, redirects back to `/settings?gmail_error=...`. Validates `state` via `_verify_state`; an invalid/expired token redirects with `gmail_error=invalid_state`. Then exchanges `code` for tokens at `https://oauth2.googleapis.com/token`, requires a `refresh_token` in the response (otherwise `400` — the user must revoke the stale grant and retry), fetches the inbox address from the Gmail profile endpoint, and persists via `save_credential`. Finishes at `/settings?gmail_connected=1`.
3. **`POST /api/gmail/disconnect`** (`gmail_auth.py:136`) — best-effort revokes the refresh token at Google's revoke endpoint, then deletes the local credential row.

### CSRF state token

`_sign_state` / `_verify_state` (`gmail_auth.py:35-57`) build a `<nonce>.<ts>.<hmac>` token. The HMAC-SHA256 key is `CREDENTIAL_KEY`. Tokens older than `_STATE_TTL_SECONDS = 600` (or with a future timestamp) are rejected, and `hmac.compare_digest` is used for the constant-time compare.

## Credential storage and encryption

`backend/gmail/credentials.py` is the only module that touches the cipher.

- `_fernet()` (`credentials.py:13`) builds a `Fernet` from `settings.CREDENTIAL_KEY`; it raises `RuntimeError` rather than silently operating if the key is empty.
- `encrypt_refresh_token` / `decrypt_refresh_token` (`credentials.py:22-27`) are thin wrappers.
- `save_credential` (`credentials.py:30`) **upserts** by `email_address`, rotating the encrypted token and scope string if the row already exists (no duplicate inserts).
- `load_credential` / `delete_credential` round out the CRUD.

### `GmailCredential` model

`backend/models/gmail.py:24` — one row per linked inbox. Phase 1 ships single-account but keys on `email_address` (unique) so multi-account needs no migration. Notable columns:

| Column | Notes |
| --- | --- |
| `email_address` | unique key |
| `encrypted_refresh_token` | Fernet ciphertext; **access tokens are never persisted** |
| `scopes` | space-joined granted scopes |
| `history_id` | sync cursor — `NULL` means "never synced" (triggers backfill) |
| `enabled` | poll/sync skips disabled rows |
| `last_synced_at` | updated by the worker's cursor write |

### `GmailMessage` model

`backend/models/gmail.py:46` — cached **metadata only**, body is never stored. `gmail_message_id` is unique (the dedup key). A `CheckConstraint` (`ck_gmail_messages_category`) restricts `category` to `noise | rejection | offer | interview_invite | ats_ack | unknown`. Phase 2 enrichment columns (`extracted_company`, `extracted_role`, `extracted_interview_at`, `extracted_salary_text`, `extracted_questions_json`) are declared now but stay `NULL` in Phase 1.

`ApplicationCorrespondence` (`backend/models/gmail.py:88`) links an `Application` to a `GmailMessage` with `direction`, `link_confidence`, and `link_method` — the join table for attributing email to a tracked application.

## Token manager

`GmailTokenManager` (`backend/gmail/auth.py:24`) is a per-process in-memory cache of `{email -> access_token}`. A single instance is created in `lifespan` and stored on `app.state.gmail_token_manager` (`main.py:259`).

- `access_token(email)` returns the cached token if it is still valid with a `_REFRESH_BUFFER_SECONDS = 60` margin, otherwise calls `_refresh`.
- `_refresh` (`auth.py:42`) decrypts the stored refresh token, POSTs a `grant_type=refresh_token` request to Google, and caches the new access token with its `expires_in`. Refresh tokens stay encrypted in the DB; access tokens live only in memory.
- `revoke_refresh_token` (`auth.py:69`) is best-effort and swallows errors so disconnect always succeeds locally.

## Gmail REST client

`GmailRestClient` (`backend/gmail/client.py:10`) is an async context manager wrapping the three endpoints the sync needs against `https://gmail.googleapis.com/gmail/v1/users/me`:

- `messages_list(q, page_token)` — `maxResults=100`, optional Gmail search query.
- `history_list(start_history_id, page_token)` — `historyTypes=["messageAdded"]` for delta sync.
- `messages_get(message_id)` — uses `format=metadata` with `metadataHeaders=[From, To, Subject, Date]` (cheap, 5 quota units; body never fetched).

## Sync worker

`GmailSyncWorker` (`backend/gmail/sync.py:75`) — one instance per process. `sync_now(email)` is idempotent and re-entrant-safe per account via a per-email `asyncio.Lock` (`_lock_for`). A `Semaphore(_CONCURRENCY=10)` bounds concurrent `messages.get` fan-out.

`_sync_locked` (`sync.py:91`) is the core:

1. Load the credential; **bail (return 0) if missing or `enabled == False`**.
2. Read `cred.history_id` to decide mode:
   - **First run / backfill** (`history_id is None`): `_first_run_ids` (`sync.py:128`) pages `messages.list` with query `newer_than:{GMAIL_BACKFILL_DAYS}d category:primary`, collecting message IDs and the latest `historyId`.
   - **Delta poll** (cursor present): `_delta_ids` (`sync.py:144`) pages `history.list` from the stored cursor and collects IDs from `messagesAdded` entries.
3. If no IDs, update the cursor, broadcast a `progress=1.0` status, and return 0.
4. Fetch each message via `_safe_get` (logs and skips on per-message failure), then `_persist_one` for each.
5. `_update_cursor` (`sync.py:215`) writes the new `history_id` and `last_synced_at`.

`_persist_one` (`sync.py:171`) extracts headers (`From`/`To`/`Subject`/`Date`), parses the received time (`_parse_date` handles RFC-2822 dates and falls back to `internalDate` ms, then to now), runs the classifier, and inserts a `GmailMessage` with `classified_by="heuristic"`. Dedup is enforced by the DB unique constraint on `gmail_message_id`: on `IntegrityError`, `_is_gmail_dedup_violation` (`sync.py:34`) distinguishes the dedup UNIQUE violation (return `False`, no crash) from a real FK/integrity bug (re-raise). On a genuinely new row it broadcasts `broadcast_gmail_message_received` over the websocket.

WebSocket broadcasts (`broadcast_gmail_sync_status`, `broadcast_gmail_message_received`) are imported with a fallback to no-op stubs (`sync.py:20-27`) so the worker runs in trimmed test/CLI environments.

## Email classifier

`backend/gmail/classifier_heuristics.py` is a zero-cost, deterministic classifier. `classify(from_address, subject, snippet)` (`classifier_heuristics.py:81`) returns `(category, confidence, ats_vendor)` with confidence capped at `_MAX_HEURISTIC_CONFIDENCE = 0.85` so a future Phase 2 LLM tier can override.

Order of precedence:

1. **Noise** — if the sender matches `NOISE_DOMAINS` (`newsletter@indeed.com`, `linkedin-jobs@linkedin.com`, `alerts@glassdoor.com`), return `noise` and never escalate.
2. Compute the ATS vendor via `_vendor_for`.
3. Subject/snippet regex blob is matched against `REJECTION_PATTERNS` → `OFFER_PATTERNS` → `INTERVIEW_PATTERNS` (in that order); the first hit wins at confidence `0.85`, carrying any detected vendor. These patterns **trump** the default ATS-ack.
4. If no pattern hit but a vendor was detected → `ats_ack` at confidence `0.7`.
5. Otherwise → `unknown` at confidence `0.0`.

### Vendor domain matching (`_vendor_for`)

`_vendor_for` (`classifier_heuristics.py:70`) matches **against the domain part only** (`from_address.lower().rsplit("@", 1)[-1]`) using a *contains* test against `ATS_DOMAINS`. The contains test lets tenant-scoped hosts like `careers.acme.myworkday.com` hit `myworkday.com`; matching only the domain part prevents a vendor fragment in the local-part (e.g. `workable.com@smartrecruiters.com`) from beating the real sending domain. Known vendors: greenhouse, lever, workday, ashby, workable, smartrecruiters, taleo, icims, bamboohr.

Example categories produced: a greenhouse sender with a plain confirmation → `ats_ack` / `greenhouse` / `0.7`; the same sender whose subject says "unfortunately we have decided" → `rejection` / `greenhouse` / `0.85`.

## Poll cron entrypoint

The poller is wired in `backend/main.py`'s `lifespan` (`main.py:263-274`): an APScheduler `AsyncIOScheduler` runs `_run_gmail_poll` on an `interval` of `max(1, GMAIL_POLL_INTERVAL_MINUTES)` minutes (job id `gmail_poll`). It is shut down with `wait=False` on app shutdown (`main.py:280-285`).

`_run_gmail_poll` (`main.py:43`) selects all `GmailCredential` rows where `enabled is True`, constructs a `GmailSyncWorker` using `app.state.gmail_token_manager`, and calls `sync_now(email)` for each, logging and continuing on per-account failure. `GmailSyncWorker` is imported at module level (with a try/except fallback to `None`) so tests can patch `backend.main.GmailSyncWorker` and so import stays resilient in stripped-down environments.

## HTTP API summary

| Method + path | Handler | Behavior |
| --- | --- | --- |
| `GET /api/gmail/oauth/start` | `gmail_auth.oauth_start` | Redirect to Google consent |
| `GET /api/gmail/oauth/callback` | `gmail_auth.oauth_callback` | Token exchange + `save_credential` |
| `POST /api/gmail/disconnect` | `gmail_auth.disconnect` | Revoke + delete credential |
| `GET /api/gmail/status` | `gmail.status` | Connection + message count + cursor |
| `POST /api/gmail/sync` | `gmail.sync_now` | Force a sync pass (power-user/debug) |

`GET /api/gmail/status` (`backend/api/gmail.py:24`) returns `connected`, `email_address`, `last_synced_at`, `history_id`, `message_count`, and `enabled` for the single connected account. `POST /api/gmail/sync` (`gmail.py:50`) builds a worker from `app.state.gmail_token_manager` (returns `503` if uninitialised, `404` if no account) and returns the count of newly inserted rows.

## Related

- [Architecture](./architecture.md)
- [API reference](./api-reference.md)
- [Backend file map](./file-map.md)
- [User guide](./user-guide.md)
- [Docs index](./index.md)
- [Project README](../README.md)
