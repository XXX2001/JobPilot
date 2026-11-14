# Discovery monitor — architecture

The monitor finds new postings within minutes, stores each once, scores it and notifies the owner. It runs inside the app process on the app's APScheduler: no second process, no queue broker, no LLM in the poll path (spec D1). Design record: [spec](superpowers/specs/2026-11-14-monitor-redesign.md), [decision log](decisions-2026-11-14.md). Operator view (sources, status API, settings, measurements): [scraping.md](scraping.md#the-discovery-monitor).

| Module | Role |
|---|---|
| `backend/monitor/poller.py` | `monitor_tick`, `poll_source`, `next_due`, `rescore_stale`, the write lock |
| `backend/monitor/store.py` | `upsert_discovered`, `match_new`, `rescore_all`, `raw_to_details` |
| `backend/monitor/notify.py` | `notify_new_match` (WebSocket + ntfy), `notify_text` |
| `backend/monitor/linkedin_detail.py` | Reads matched LinkedIn cards in the JobPilot Chrome ([browser.md](browser.md)) |
| `backend/scraping/sources/` | One adapter per source, registered in `ADAPTERS` |
| `backend/api/monitor.py` | `/api/monitor/*` routes |
| `scripts/import_watchlist.py` | Turns an ATS company CSV into monitor sources |

## One tick

```
APScheduler "monitor_tick" (every MONITOR_TICK_SECONDS, max_instances=1, coalesce=True)
  monitor_tick                                         poller.py:286
    due = enabled job_sources rows with config["adapter"] and next_poll_at NULL or past
    gather(poll_one(...)) under Semaphore(MONITOR_MAX_CONCURRENCY), one shared httpx client (20 s timeout)
      poll_source(db, source, client, notify_new_match, notify_text)   poller.py:150
        ADAPTERS[adapter](client, cfg, etag, last_scraped_at) -> PollResult      (no lock)
        async with _write_lock():
          upsert_discovered(db, source, result.jobs) -> newly inserted Job rows   store.py:43
          match_new(db, jobs, filters, min_score)    -> new JobMatch rows         store.py:139
          etag, window_hours, fail_count=0, last_error=None, last_scraped_at,
          first_polled_at, next_poll_at = next_due(...)
          commit
        not seeding: drop matches posted before first_polled_at (windowed adapters only)
        linkedin_guest: _read_linkedin_pages(...)    (may rescore and drop some)  poller.py:238
        notify each remaining match                  (failures logged, never raised)
```

Each source gets its own `AsyncSession` (`poll_one`, `poller.py:305`). A failed poll rolls back as a whole; stored jobs are never touched by a failure.

### Adapters

The contract is in `backend/scraping/sources/base.py`:

- `PollFn = (client, cfg, etag, last_scraped_at) -> Awaitable[PollResult]` (`base.py:42`). `cfg` is the source's `config` JSON plus `window_hours` when the row sets one (`poller.py:167`).
- `PollResult(jobs, not_modified, etag, truncated)` (`base.py:25`). `truncated` means the page or limit cap was hit.
- `SourceError(kind, detail)` (`base.py:32`), `kind` in `"blocked" | "auth" | "transient"`. `classify_response` (`base.py:69`) maps 401 to `auth`; 403, 429 and 999 to `blocked`; other 4xx and 5xx to `transient`; and a `text/html` body where JSON was expected to `blocked` (a challenge page often answers 200).
- `send_logged_out` (`base.py:48`) is the only way an adapter sends a request. It removes the `cookie` and `authorization` headers, sends with `auth=None` and does not follow redirects, because httpx would add the cookie jar back on the next hop (spec D7). Transport errors become `transient`.
- A malformed item is skipped (`ITEM_ERRORS`, `skip_item`). One bad item never fails the poll.

| Adapter | Request | Conditional GET | Window | `truncated` when |
|---|---|---|---|---|
| `greenhouse` | whole board, `content=true` | `If-None-Match`, 304 → `not_modified` | — | never |
| `ashby` | whole board | yes | — | never |
| `smartrecruiters` | first 100 postings, no descriptions | yes | — | `totalFound > 100` |
| `indeed` | one GraphQL POST, `sort: DATE`, `dateOnIndeed` ≥ N h | no | `_window_hours`: configured (2 h) widened to the gap since the last poll, 1–72 h (`indeed.py:94`) | 100 results (`LIMIT`) |
| `linkedin_guest` | guest search fragment, `sortBy=DD`, ≤ 5 pages × 10 cards | no | `_window_seconds`: 2 × gap since the last poll, 1,800–86,400 s; seed = 86,400 s (`linkedin_guest.py:40`) | 5 full pages |

LinkedIn guest: a 3xx (the authwall), 403, 429 and 999 are `blocked`; any other non-200 is `transient`; a 200 without job cards is `blocked`; an empty 200 body is zero jobs (`linkedin_guest.py:61`). It never raises `auth`.

### Scheduling, backoff, disabling

`next_due(now, interval_s, fail_count, rng)` (`poller.py:63`):

- success: `now + interval × U(0.8, 1.2)`;
- failure: `now + min(interval × 2^min(fail_count, 20), 6 h)`.

`interval_s` is `poll_interval_s`, else `DEFAULT_INTERVAL_S[adapter]` (boards 60 s, `linkedin_guest` 300 s, `indeed` 420 s), else 300 s (`poller.py:44`). A source is only looked at on a tick, so the real period is rounded up to the next tick.

On failure (`poller.py:205`): rollback, refresh the row, store `last_error` (500 chars). A `SourceError` of kind `auth` sets `enabled = False` and sends one "Source disabled" ntfy alert. It is not retried; `POST /api/monitor/sources/{id}/resume` turns it back on. Any other error increments `fail_count` and pushes `next_poll_at` back. Only adapters that call `classify_response` (the three boards and Indeed) can raise `auth`.

Indeed window halving (`poller.py:191`): a truncated Indeed result sets `source.window_hours = max(1, window // 2)`. Nothing widens it again (see shortcuts). Other adapters only log a warning when truncated.

## Store and identity

`upsert_discovered(db, source, jobs)` (`store.py:43`) returns only the rows this call inserted.

1. Look up by `(source.id, external_id)`. The partial unique index `uq_jobs_source_external` backs it (m01).
2. Compute `dedup_key(company, title, location)` (`backend/scraping/deduplicator.py:9`): md5 of the normalised `company|title|city`, where city is the location text before the first comma (m02). Every writer (batch, manual search, monitor) uses this one function.
3. If there is no ID hit, fall back to the row holding that hash. This is how the same posting from a second source refreshes the first row instead of creating another one.
4. If the ID hit carries an old hash and no other row owns the new one, the posting was retitled: title, company, location and hash follow it.
5. Insert with `ON CONFLICT (dedup_hash) DO NOTHING ... RETURNING`. If two sessions insert the same new hash in one tick, the loser re-reads the winner's row. A SAVEPOINT is not used: pysqlite opens no transaction before one, so its RELEASE would commit the job ahead of its match (`store.py:57`).
6. An existing row gets `last_seen_at`, a longer description and a missing `apply_url`.

Known miss (documented in the docstring): `dedup_hash` is globally unique, so two postings of one source with different IDs but the same company, title and city merge into one row. The merge is logged.

## Matching: one match per job

`match_new(db, jobs, filters, min_score, matcher=None)` (`store.py:139`):

- skips a job that already has any match (spec D3);
- skips a repost: `_repost_key(job) = (title, city, description)` (lowercased title and city) when the description is at least `PROVISIONAL_MIN_CHARS` (80) long (`store.py:202`). It catches the same posting carried under another company name ("job wrapping") that the dedup hash cannot merge. The key is compared against every job that has a match, whatever its status, and within the batch;
- scores with `matcher.explain(raw_to_details(job), filters)` ([modules/matching.md](modules/matching.md)). A hard-filter rejection is never matched. A score below `min_score` is not matched;
- stores `score`, `keyword_hits` (covered + partial skills), `score_basis`, `provisional`, `location_unverified`, `batch_date = today`, `status = "new"`. It flushes but does not commit.

Filters come from `SearchSettings` through `_load_filters` (`poller.py:96`), the same mapping as the batch runner.

## Seeding

- First successful poll (`last_scraped_at IS NULL`, `poller.py:170`): every job is stored and matched, and nothing is notified. LinkedIn pages are not read either.
- Windowed adapters only (`WINDOWED_ADAPTERS = {"indeed", "linkedin_guest"}`, `poller.py:58`): later polls also skip notifying a job whose `posted_at` is before `first_polled_at` (`_predates`, `poller.py:87`). A bare-day date (LinkedIn without "x minutes ago") is compared by day. A window cut differently on a later poll (another LinkedIn page, Indeed overflow) keeps returning postings older than the seed, and those are stored and matched, not pushed.
- Board adapters (`greenhouse`, `ashby`, `smartrecruiters`) do not get that guard. A board returns the whole board every time, so the seed already saw every old posting. A posting that is new on the board can carry an old `posted_at` (`first_published` / `releasedDate` of a republished posting), and the guard would silence it.

`first_polled_at` was added in m02 and backfilled from the earliest `first_seen_at` of the source's jobs.

## Notifications

`notify_new_match(match, job, source_name)` (`notify.py:50`):

- a `new_match` WebSocket frame (`broadcast_new_match`, `NewMatch` in `backend/api/ws_models.py`). The Today page prepends it (`frontend/src/lib/utils/newMatch.ts`);
- one ntfy POST when `NTFY_URL` is set. Title `New match <score>`, plus `(title only, location unverified, Easy Apply)` when those apply. Body `<title> — <company> (<location>)`. `Click` is the posting URL.

`_post_ntfy` (`notify.py:23`) starts a task and returns: a slow ntfy never holds up the poll. `_send_ntfy` gives up after 5 s and never raises. The poller wraps every notify in `_safely` (`poller.py:278`), so a failed notification never undoes a committed poll.

## LinkedIn page reading

Only for `linkedin_guest`, only after the poll is committed, and not during the seeding poll. `_read_linkedin_pages` (`poller.py:238`):

1. Takes the new matches whose job has no description (cards).
2. Calls `read_jobs(urls)` ([browser.md](browser.md#reading-linkedin-job-pages)). It returns `{}` when the JobPilot Chrome is not running or the cap is reached. Any exception is logged and the cards stay title-only.
3. Under the write lock, for each card read: sets `apply_method`. If a description came back, stores it and rescores the match in place (`score`, `keyword_hits`, `score_basis`, `provisional`, `location_unverified`).
4. Drops from the notification list a match whose page says closed, or whose new score is below `min_score`. The match stays in the queue as `new`; it is only not pushed.

At most 5 cards per poll are read (`MAX_PER_POLL`). Further cards are notified as title-only.

## Concurrency

- HTTP fetches run concurrently (up to `MONITOR_MAX_CONCURRENCY`, default 8). DB writes are serialised by `_write_lock()` (`poller.py:78`), one `asyncio.Lock` per event loop (a `WeakKeyDictionary`, because a lock binds to the loop it first waits on). `rescore_stale` and `_read_linkedin_pages` take the same lock.
- SQLite runs in WAL mode, and the engine passes `connect_args={"timeout": 30}` (`backend/database.py:29`): a writer outside the monitor (an API request) waits up to 30 s for the lock instead of the default 5 s, which a seeding poll could exceed.
- The tick awaits all its polls. A slow poll, LinkedIn page reading in particular (up to 5 pages, 6–18 s apart, each with up to 30 s load and 15 s wait timeouts), keeps the tick running. `max_instances=1, coalesce=True` then skips the ticks due in the meantime, so other sources wait too.

## Rescoring

`score_basis` is `"{sha256(SCORER_VERSION, CV text or my_skills, keywords)[:12]}:{file_version(vocabulary)}"` (`backend/matching/matcher.py:56`). A stored match with a different basis is stale.

`rescore_stale(db, *, force=False, after_save=True)` (`poller.py:112`), under the write lock:

1. `rescore_all(db, matcher, filters, force=force)` (`store.py:216`) recomputes every `new` match whose `score_basis` differs, or every `new` match when `force` is set. The hard filters are not part of the basis, so a search-settings save passes `force=True`. A match the filters now reject stays, at score 0.
2. If `after_save` is set, or step 1 rescored anything, it runs `match_new` over jobs stored in the last 30 days (`RESCORE_UNMATCHED_DAYS`, by `scraped_at`) that have no match. Nobody is notified of these; they show in the queue.
3. Commits.

Called from:

| Caller | `force` | `after_save` |
|---|---|---|
| App startup, after migrations (`backend/main.py:97`) | false | false |
| Profile save that sets `base_cv_path` (`backend/api/settings.py:287`) | false | true |
| Search settings save (`settings.py:328`) | **true** | true |
| `PUT /api/settings/skills` (`settings.py:376`) | false | true |
| CV upload (`settings.py:716`) | false | true |

The settings routes call it through `_rescore` (`settings.py:37`), which logs a failure and rolls back. The save itself is already committed.

## Schema (migrations)

| Revision | Changes |
|---|---|
| `m01_monitor_state` | `jobs.first_seen_at`, `last_seen_at`. Partial unique index `uq_jobs_source_external (source_id, external_id)` where `external_id` is non-empty. `job_sources.poll_interval_s`, `next_poll_at` (indexed), `etag`, `fail_count` (default 0), `last_error`, `window_hours`. Rehashes `jobs.dedup_hash` with the `dedup_key` normalisation; where the new hash is already taken, the twin's matches move to the row holding it (`UPDATE OR IGNORE`). Restored on downgrade. |
| `m02_city_dedup_first_polled` | Rehashes `dedup_hash` on the city only. Adds `job_sources.first_polled_at`, backfilled from the earliest stored job (else `last_scraped_at`). |
| `m03_match_score_basis` | `job_matches.score_basis` (NULL on existing rows, so the next rescore recomputes them). |
| `m04_match_score_flags` | `job_matches.provisional`, `location_unverified` (boolean, default false). |

## API

`backend/api/monitor.py`, prefix `/api/monitor`. The POST routes need the local session (`AccessBoundary` guards every POST under `/api`).

| Route | Behaviour |
|---|---|
| `GET /status` (`:106`) | `{"enabled": MONITOR_ENABLED, "sources": [...]}`. Only rows with an adapter. Per source: `id, name, adapter, enabled, interval_s` (effective), `last_scraped_at, next_poll_at` (ISO, UTC), `fail_count, last_error, jobs_seen, median_lag_s`. |
| `POST /sources` (`:137`) | Body `{adapter, name, config, interval_s?}`. 422 for an unknown adapter or a blank name, 409 for a taken name. Creates `type="monitor"`, `config = {...config, "adapter": adapter}`. |
| `POST /sources/{id}/pause` (`:127`) | `enabled = false`. |
| `POST /sources/{id}/resume` (`:132`) | `enabled = true`, `fail_count = 0`, `next_poll_at = NULL` (polled at the next tick). |

`median_lag_s` (`_job_stats`, `:65`) is the median of `first_seen_at − posted_at` over the last 7 days. The seeding poll is left out; it is recognised as the source's earliest `first_seen_at`.

The Settings → Monitor tab (`frontend/src/lib/components/settings/MonitorTab.svelte`) lists sources and calls pause and resume. Adding sources goes through the API or `scripts/import_watchlist.py`.

## Configuration and tests

`MONITOR_ENABLED` (default false), `MONITOR_TICK_SECONDS` (30), `MONITOR_MAX_CONCURRENCY` (8), `NTFY_URL` (empty) are in `backend/config.py:103`. The job is only added when `MONITOR_ENABLED` is true (`backend/main.py:270`). Changes apply on restart.

`tests/conftest.py:50` sets `MONITOR_ENABLED=false` before the app is imported, so a developer `.env` that turns the monitor on never makes tests poll or push.

## Known shortcuts

Each is marked `ponytail:` in the code. The shortcut is deliberate; the upgrade path is the comment's.

| Where | Shortcut | Ceiling | Upgrade path |
|---|---|---|---|
| `poller.py:81` `_write_lock` | One lock for every source | Writes are serial (SQLite has one writer anyway) | Split it if the store moves to a database with row locks |
| `poller.py:131` `rescore_stale` | Re-matches every unmatched job of the last 30 days on each save | About 2 ms per job, on the event loop | Record the swept basis and skip jobs already scored on it |
| `poller.py:193` Indeed window | Halved on overflow, never widened back | A window halved during a burst stays small | Grow it on quiet polls |
| `store.py:163` repost key | Exact description equality | Reposts with edited text are matched again | Compare a normalised text |
| `notify.py:32` `_post_ntfy` | One task per push, unordered, unthrottled | A burst may hit an ntfy rate limit | A queue with a single sender |
| `linkedin_detail.py:40` `_opened` | Daily page cap counted in memory | Resets on restart | Persist it |
| `api/monitor.py:71` `_job_stats` | Seeding poll = the source's earliest `first_seen_at` | A source whose first poll found nothing loses its first real batch from the median | Use `job_sources.first_polled_at`. The column exists since m02, so this is now cheap |
| `api/browser.py:23` `SESSION_COOKIES` | Login state checked for LinkedIn only | Indeed is never reported, and `POST /api/browser/open` opens its login page on every call | Add a site when its apply flow lands and its session cookie is checked |
| `deduplicator.py:18` `dedup_key` | City = text before the first comma | Paris, France and Paris, Texas at one company merge; "Paris (75)" ≠ "Paris" | Normalise through a gazetteer |
| `scripts/import_watchlist.py:63` | Country names from the orchestrator's small `LOCATION_TO_COUNTRY` table | Few cities per country | Use `backend/matching/data/countries.txt` / `cities.txt` (Phase 2 has landed) |

Matching-side shortcuts are listed in [modules/matching.md](modules/matching.md#known-shortcuts).

## Other known limits

- Hash-fallback merge of two postings of one source (see [Store and identity](#store-and-identity)).
- LinkedIn guest returns at most 50 cards per window, and SmartRecruiters at most 100 postings. Beyond that the poll only logs a truncation warning. Indeed halves its window instead.
- LinkedIn pages: at most 5 per poll and 80 per day. Cards past the cap are notified title-only. Seeded cards are never read.
- A match dropped after its LinkedIn page was read (closed, or below threshold) stays in the queue as `new`.
- `rescore_all` touches `new` matches only. Applied, skipped and rejected matches keep their old score.
- Sustained polling of the LinkedIn guest endpoint is measured only by the 2-hour soak ([scraping.md](scraping.md#measured)).
