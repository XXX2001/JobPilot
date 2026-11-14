# The JobPilot Chrome

JobPilot drives one long-lived genuine Chrome on a profile of its own, `$DATA/jobpilot-chrome` (`$DATA` = `JOBPILOT_DATA_DIR`, default `./data`). The owner logs into job sites in that window once, by hand. Every component attaches to it over CDP and never launches a browser of its own while it runs. Design record: spec decisions D11–D14 ([spec](superpowers/specs/2026-11-14-monitor-redesign.md), [decision log](decisions-2026-11-14.md)). Proof scripts: `docs/superpowers/proofs/chrome/`.

| Module | Role |
|---|---|
| `backend/browser/chrome.py` | Launch, find, attach, close |
| `backend/api/browser.py` | `/api/browser/status`, `/api/browser/open` |
| `backend/monitor/linkedin_detail.py` | Reads matched LinkedIn job pages in that Chrome |
| `backend/applier/form_filler.py` | Tier 1 apply. Attaches when the Chrome runs (`open_context`, commit `ad687bd`) |
| `backend/applier/harness/runner.py` | Optional System One harness, first for ASSISTED and AUTO, using the same `open_context` |
| `frontend/src/lib/components/SetupWizard.svelte` | Step 4 "Accounts" |

## Why a dedicated data dir

- Chrome 136 and later refuse `--remote-debugging-port` on the default user data dir. `ensure_chrome_running` refuses `~/.config/google-chrome` before Chrome gets the chance (`chrome.py:50`). The spec measured it on Chrome 157.
- The owner's everyday profile and any managed work profile must never be automated (D11). `chrome_dir()` (`chrome.py:123`) always returns `$DATA/jobpilot-chrome`.
- The profile is not signed into Google or Chrome Sync (D14). Google blocks sign-in under a debug port, according to community reports. The setup wizard says so.

## Launch

`ensure_chrome_running(data_dir, port=0, extra_args=(), timeout=20)` (`chrome.py:48`) returns `{"port", "ws", "version", "reused", "pid"?}` and is idempotent:

1. Refuses the default profile.
2. `_active(data_dir)` (`chrome.py:32`) reads `DevToolsActivePort` in the data dir and probes `http://127.0.0.1:<port>/json/version`. The WebSocket path must match the file, so a stale file or a port reused by another process is not mistaken for this Chrome. If it is live: `reused: True`.
3. If `SingletonLock` points at a live PID, another Chrome owns the dir without a debug port: `RuntimeError` ("close it first").
4. Creates the dir with mode 700, removes a stale `DevToolsActivePort`, and starts `/usr/bin/google-chrome --user-data-dir=<dir> <FLAGS> <extra_args> about:blank` in its own session (`start_new_session=True`), so Chrome outlives the app. Output goes to `$DATA/jobpilot-chrome.chrome.log`.
5. Polls `_active` every 0.2 s until the endpoint is up. `RuntimeError` if Chrome exits, `TimeoutError` after `timeout`.

`FLAGS` (`chrome.py:15`):

| Flag | Why |
|---|---|
| `--remote-debugging-port=0` | Chrome picks a free loopback port and writes it to `DevToolsActivePort` |
| `--no-first-run`, `--no-default-browser-check` | No first-run dialogs |
| `--disable-blink-features=AutomationControlled` | Without it, genuine Chrome reports `navigator.webdriver = true` whenever a debug port is open (measured, `proofs/chrome/t1_launcher.py`). Cost: a dismissible "unsupported command-line flag" infobar that pages cannot see |

Never pass (D12): `--enable-automation`, `--no-sandbox`, `--remote-allow-origins=*`, `--headless`, or browser-use's `disable_security=True`. browser-use attaches with `permissions=[]` and opens its own tab. Tasks close their page, never the context, because the context is the owner's session. `ensure_chrome_running` does not check `extra_args` against this list. Its only caller (`POST /api/browser/open`) passes none.

Other helpers:

- `running_endpoint()` (`chrome.py:130`): the endpoint when the JobPilot Chrome is running, else `None`. It never starts Chrome. Every reader goes through it.
- `close_chrome(ws)` (`chrome.py:88`): CDP `Browser.close`. Chrome shuts down cleanly and flushes the cookie DB, which SIGTERM may not do. **No app code calls it yet.**
- `my_chrome_pids(data_dir)`, `wait_gone(data_dir)`: processes started on this data dir only. Also not used by app code.
- `open_context(pw, profile_dir, args)` (`chrome.py:135`, commit `ad687bd`): `(context, attached)`. The running JobPilot Chrome's default context if there is one, else a per-site Playwright persistent context as before.

## Risk: the local debug port

The CDP port listens on 127.0.0.1 without authentication. Any local process can find it and drive the logged-in Chrome (demonstrated in the proofs). Accepted for a single-user machine (spec, known unknowns). The spec's mitigation, closing Chrome when idle, is not built: nothing calls `close_chrome`, and Chrome keeps running after the app stops. The data dir is mode 700, so other users cannot read `DevToolsActivePort` or the cookies. A process of the same user can.

## API and setup wizard

`backend/api/browser.py`, prefix `/api/browser`:

| Route | Behaviour |
|---|---|
| `GET /status` (`:45`) | `{"running": false, "logged_in": {}}` when the Chrome is not running. It never starts it. Otherwise `{"running": true, "logged_in": {"linkedin": bool}}` |
| `POST /open` (`:54`) | `ensure_chrome_running(chrome_dir())` in a thread (503 if Chrome does not start). Then opens a tab (`PUT /json/new?<url>`) on each login page in `LOGIN_PAGES` (LinkedIn, Indeed) whose site is not logged in. Needs the local session, like every POST under `/api` |

Login detection (`_logged_in`, `:33`) attaches with Playwright and looks for a session cookie by name (`SESSION_COOKIES`, LinkedIn only). Indeed has no entry, so `/open` opens its login page on every call and `/status` never reports it.

Setup wizard step 4 "Accounts" has two buttons: "Open the JobPilot browser" (`POST /open`) and "Check logins" (`GET /status`). It tells the owner to log in once and not to sign the window into Google.

## Reading LinkedIn job pages

The logged-out guest search gives cards without a description (spec D7: discovery is always logged out). After a `linkedin_guest` poll is committed, the cards that matched are opened once each in the owner's logged-in Chrome, the way the owner would open them. Pages are only read for jobs that already matched, a few at a time. The caller is `_read_linkedin_pages` in the poller ([monitor.md](monitor.md#linkedin-page-reading)).

`read_jobs(urls, *, today=None) -> dict[url, Detail]` (`linkedin_detail.py:69`):

- Returns `{}` without opening anything when `running_endpoint()` is `None`, the URL list is empty, or the budget is spent. Budget = `min(MAX_PER_POLL, MAX_PER_DAY − opened today)`.
- Attaches with `connect_over_cdp` and uses `browser.contexts[0]`, the owner's session. Each URL gets a new tab, and the tab is closed in `finally`. The context is never closed.
- Pacing: `GAP_S = (6, 18)` s uniform between two pages, plus a 1.5–4 s dwell after the content loads. `goto` waits for `domcontentloaded` (30 s timeout). It then waits until `main` holds more than 400 characters (15 s).
- Caps: `MAX_PER_POLL = 5`, `MAX_PER_DAY = 80`. Each navigation counts, a login wall included. The day counter `_opened` is in memory and resets on restart.
- Login wall: if the page URL lands on `/login`, `/authwall`, `/checkpoint`, `/uas/login` or `/signup` (`_WALL`), it logs a warning and stops reading for this poll. Nothing pauses and nothing notifies the owner. The next poll tries again and spends one page of the cap.
- A page that fails to load or parse is logged and skipped.

`parse(main_text) -> Detail(description, apply_method)` (`linkedin_detail.py:50`) works on the visible text of `main`, in English and French:

- Description: the text between "About the job" / "À propos de l'offre d'emploi" and the first end marker ("Set alert for similar jobs", "About the company", Premium upsell, or their French forms). Empty when there is no "About the job" heading.
- `apply_method`, from the text above that heading: `closed` ("No longer accepting applications", …), else `easy_apply`, else `external` (a bare "Apply" / "Postuler" line), else `unknown`.

What the poller does with it:

| Outcome | Stored | Notified |
|---|---|---|
| Chrome not running, cap spent, or reading raised | Card unchanged (title-only, `provisional`) | yes, as "title only" |
| Page read, description found | `description`, `apply_method`, rescored match | yes, if not closed and the new score ≥ `min_score`. ntfy adds "Easy Apply" when it applies |
| Page read, no description | `apply_method` only | yes, unless closed |
| Closed, or the new score is below `min_score` | as above. The match stays in the queue as `new` | no |
| Seeding poll | Nothing read | nothing is notified on a seed |

This is the only automated use of a logged-in account in discovery. It is bounded by the caps above, and it never searches or paginates while logged in.

## Application harness

Set `DECISION_MODEL_URL` to enable the OpenJev/System One harness before Tier 1 and
Tier 2 on every site, including multi-step applications. It opens its own tab through
`open_context`, using the dedicated JobPilot Chrome when running, or the existing
per-site profile otherwise. Stealth patches apply only to the latter. It never closes
an attached context. ASSISTED leaves the filled tab open for the owner, with lists of
filled fields, generated answers and fields left empty. With a launched per-site
browser, the harness keeps Playwright, the context and the page referenced in memory
and leaves the connection running so the tab stays open for manual submission.
These handles remain until the application process exits. With the attached JobPilot
Chrome, it disconnects Playwright as before; the independently running Chrome and
the owner's tab stay open.

The harness fills stored profile values, including LinkedIn and additional answers,
and attaches the selected PDFs. An optional writer answers free-text questions from
the base CV and profile; unavailable facts or an unavailable writer leave the field
for the owner. Login walls and captchas prompt an in-app hand-off and an ntfy notice
when configured. Password fields are never filled.

AUTO remains disabled by default (`JOBPILOT_AUTO_SUBMIT_ENABLED=false`). When enabled,
it requires owner approval; corrections trigger another review. Review keys identify
the harness's frame and node, with labels in the review note. Cross-origin frame
fields are read-only in this review. The approved submit control is clicked once;
the destination must confirm submission, including confirmation inside an iframe.
Another form step or an uncertain result becomes `needs_verification`, without retry.
If no submit control is identified, the tab remains open for manual submission.

Fallback to the existing tiers is allowed only when no `submits=True` control was
clicked after typing. A failure without a usable click history also stops for
verification. See [configuration](configuration.md#browser-decision-harness) for
local and hosted endpoints and which data they receive.

## Not built

- **Apply through the attached Chrome, Tier 2.** The Tier 1 Playwright form filler attaches since `ad687bd`. The browser-use strategies (`_strategy_common.build_browser`, `assisted_apply.py`, `auto_apply.py`) still launch their own browser at that commit. Uncommitted work towards a CDP attach was in the working tree when this page was written. Check `git log` before relying on either state.
- **Challenge pause and resume (D13).** The proofs (`proofs/chrome/challenge.py`: Cloudflare, Turnstile, hCaptcha, reCAPTCHA and login-wall detection; `t5_challenge.py`: pause, then resume once a second client clears it; `pacing.py`: log-normal delays, per-site gap and cap, backoff after a challenge) are not wired into the app. The existing `backend/applier/captcha_handler.py` (WebSocket notice, then poll until cleared or 300 s) predates this design. The LinkedIn reader stops at a wall and does not pause.
- **Removing stored passwords and auto-login (D14).** `SiteCredential` and `BrowserSessionManager._attempt_auto_login` (`backend/scraping/session_manager.py:88`) are still there.
- **Closing Chrome when idle.** `close_chrome` exists and is not called.
- **Session check for sites other than LinkedIn.**
