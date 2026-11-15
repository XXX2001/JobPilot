# Decision log — monitor redesign, 2026-11-14

Decisions behind the discovery monitor, the gazetteer scorer and the JobPilot Chrome. D1–D16 are from the [spec](superpowers/specs/2026-11-14-monitor-redesign.md), where the full evidence is. The L rows were taken while it was built. Commits are on `feat/monitor-discovery` after `0fe6e65`. Architecture: [monitor.md](monitor.md), [browser.md](browser.md), [modules/matching.md](modules/matching.md).

## From the spec

| # | Decision | Why | Evidence / commit |
|---|---|---|---|
| D1 | Discovery is deterministic HTTP + parsers. No LLM and no browser in the poll path | An empty poll, the normal case, took the most expensive path | Spec: guest fragment fetched in 0.48 s, parsed in 0.55 ms; board 304s in 19–210 ms. `e5b4b9a`, `af50cf9` |
| D2 | Identity `(source, external_id)`, `dedup_hash` as cross-source fallback, one function | Three hash sites disagreed on whitespace | `e0553fd`, `48c82bd` (m01 rehash); city-only key `ac5d2f1` (m02) |
| D3 | One match per job, ever | Two `new` rows a day apart were reproduced | `63d8708`; `match_new`, `BatchRunner._store_matches` |
| D4 | Discovery stores, scores and notifies; CV tailoring only on selection | The batch re-tailored the top N every run | The monitor never tailors. The on-demand batch still pre-generates CVs (unchanged), but description-less cards take no CV slot (`8bf29d7`) |
| D5 | Sources in order: Greenhouse, SmartRecruiters, Ashby; Indeed; LinkedIn guest; then Teamtailor, Lever, Personio | Conditional GET where it exists, windows elsewhere | `af50cf9`, `7ae1272`, `970c4b5`, `6750161`. Teamtailor, Lever and Personio are not built |
| D6 | Glassdoor is not polled for speed | Day-level dates, no external apply link, about two-thirds overlap with Indeed | `proofs/indeed/overlap.py`, `gd_apply.py`. Not built |
| D7 | Discovery is always logged out | LinkedIn User Agreement §8.2; the personal account is irreplaceable | `send_logged_out` (`70246ad`). The logged-in page read (L6) is outside the poll |
| D8 | No `python-jobspy` dependency; vendor the Indeed query (MIT, attributed) | 173 MB, pandas/numpy, curl_cffi conflict | `backend/scraping/sources/indeed.py` header, `970c4b5` |
| D9 | Watchlist from `kalil0321/ats-scrapers` data (MIT) only | `job-board-aggregator` data is CC BY-NC | `docs/superpowers/proofs/ats/watchlist/*.csv`, `scripts/import_watchlist.py` (`933cd74`) |
| D10 | Curated bilingual gazetteer scorer, no embeddings, no new dependency | 1.85 ms/job; P@10 0.8 vs 0.4–0.5 (optimistic) | `b053f41` … `1b289a8` (embeddings deleted) |
| D11 | One genuine Chrome on `$DATA/jobpilot-chrome` (mode 700), fixed flags, everything attaches over CDP | Chrome refuses debugging on the default dir; attach, cookies, upload and three clients proven | `backend/browser/chrome.py` (`0de6e06`); `proofs/chrome/` |
| D12 | Never `--enable-automation`, `--no-sandbox`, `--remote-allow-origins=*`, `--headless`, `disable_security=True`; close the page, never the context; shut down via `Browser.close` | Fingerprint and side-effect tests | `proofs/chrome/t2_clients.py`. `close_chrome` exists but is not called |
| D13 | Captchas and login walls: detect, pause, notify, owner solves, resume. Pacing, no solvers | Pacing reduces challenges; it does not bypass them | `proofs/chrome/challenge.py`, `pacing.py`, `t5_challenge.py`. Not wired in |
| D14 | Remove stored site passwords and `_attempt_auto_login`; log in by hand once; no Google sign-in in the profile | Auto-login saved 2FA pages as valid sessions | Login onboarding built (`75550fb`). Removal not done |
| D15 | Auto-submit stays off until Phase 4 | Two double-apply paths and a false "applied" reproduced | `jobpilot_auto_submit_enabled = False` (`backend/config.py:47`) |
| D16 | One notifier: ntfy plus a `new_match` WebSocket frame | Self-hostable, one POST | `82005c2`; sent in the background `3608587` |

## Taken during the build

| # | Decision | Why | Evidence / commit |
|---|---|---|---|
| L1 | The owner's skills and the vocabulary can come from data files: `$DATA/my_skills.txt` (replaces CV parsing), `$DATA/skills.txt` (replaces the bundled list), with `GET`/`PUT /api/settings/skills` | Owner request: skills come from a file the owner can replace (generated from a CV outside the program), and a skill the bundled list does not know still counts | Gazetteer plan Task 7. Deferred in `a522c0b`, built in `a03c4e1` (`skill_files`, `check_skill_lines`) |
| L2 | Title-only (provisional) scores lowered from 70 / 45 to 50 / 40 | At a flat 70, title-only cards ranked above every described posting but one in the score report ([matching.md §7](matching.md#7-evaluation)) | `2ad7fc3`; `PROVISIONAL_KEYWORD_SCORE`, `PROVISIONAL_CV_SKILL_SCORE` |
| L3 | Match-score coverage uses `MATCH_SHRINK = 1.0`; the fit assessment keeps `SHRINK = 0.5` | With 0.5 a posting naming two skills reached 80; few skills are weak evidence | `8a2e557` (`SCORER_VERSION` 3); comment at `scorer.py:28` |
| L4 | A repost is one match: same title, city and description (≥ 80 chars) | Tracking boards carry one posting under other company names ("job wrapping"), which the dedup hash cannot merge | `340792c`; `_repost_key` (`store.py:202`). Exact description equality is a marked shortcut |
| L5 | The "older than the seed" notify guard only applies to windowed adapters (`indeed`, `linkedin_guest`) | Their windows keep returning pre-seed postings; a board returns everything at seed time, and a republished posting can carry an old date | `ac5d2f1`; `WINDOWED_ADAPTERS` (`poller.py:55`) |
| L6 | LinkedIn: logged-out guest search for discovery; the owner's logged-in JobPilot Chrome only opens pages of jobs that already matched, at most 5 per poll and 80 per day, 6–18 s apart, stopping at a login wall | Cards have no description, so the score is title-only, and the apply method is unknown. Bounded reading the way the owner would read keeps account risk low | `0de6e06`; `backend/monitor/linkedin_detail.py` |
| L7 | Welcome to the Jungle Algolia adapter not built | The probe worked: 25 polls, all HTTP 200. But the search key is referer-restricted, and the site's terms prohibit automated extraction | `docs/superpowers/proofs/wttj/probe.py` (25 polls at 60 s; the referer restriction is in its docstring). The poll results and the terms are not recorded in the repo |
| L8 | Tests force `MONITOR_ENABLED=false` | A developer `.env` with the monitor on made tests poll and push for real | `5eccb74`; `tests/conftest.py:50` |
| L9 | Watchlist import for France with a robotics/embedded title filter: 33 boards kept of 557 checked | Boards with nothing in the area or the field cost polls for nothing | 557 = the CSV rows for the three supported ATS (Greenhouse 210, SmartRecruiters 227, Ashby 120). Kept: 6 Greenhouse, 22 SmartRecruiters, 5 Ashby (local DB, read-only). The exact `--title-regex` used is not recorded |
| L10 | One in-process write lock around each poll's writes; SQLite connect timeout 30 s | Concurrent seeding polls hit "database is locked" at the default 5 s | `ac5d2f1`; `_write_lock`, `backend/database.py:29` |
| L11 | Stored matches are rescored at startup and after any CV, skill-file or settings save (`score_basis`) | Scores computed on an old CV or scorer stayed in the queue | `f971f0d`, `12ac325` (m03); m04 adds `provisional`, `location_unverified` |
