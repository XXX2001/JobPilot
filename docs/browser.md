# The JobPilot Chrome

JobPilot drives one long-lived genuine Chrome on a profile of its own, `$DATA/jobpilot-chrome` (`$DATA` = `JOBPILOT_DATA_DIR`, default `./data`). The owner logs into job sites in that window once, by hand. Every component attaches to it over CDP and never launches a browser of its own while it runs. Design record: spec decisions D11–D14 ([spec](superpowers/specs/2026-11-14-monitor-redesign.md), [decision log](decisions-2026-11-14.md)). Proof scripts: `docs/superpowers/proofs/chrome/`.

| Module | Role |
|---|---|
| `jobpilot`, `scripts/launcher/main.py` | Native backend lifecycle and opening the app in JobPilot's Chrome |
| `docker/supervisor.py` | Docker Chrome lifecycle, virtual display and noVNC |
| `backend/browser/chrome.py` | Launch, find, attach, close |
| `backend/api/browser.py` | `/api/browser/status`, `/api/browser/open` |
| `backend/monitor/linkedin_detail.py` | Reads matched LinkedIn job pages in that Chrome |
| `backend/applier/form_filler.py` | Tier 1 apply. Attaches when the Chrome runs (`open_context`, commit `ad687bd`) |
| `backend/applier/_strategy_common.py` | Tier 2 apply. Attaches to JobPilot's Chrome; no fallback browser in Docker |
| `backend/applier/harness/runner.py` | Optional System One harness, first for ASSISTED and AUTO, using the same `open_context` |
| `frontend/src/lib/components/SetupWizard.svelte` | Step 4 "Accounts" |

## Why a dedicated data dir

- Chrome 136 and later refuse `--remote-debugging-port` on the default user data dir. `ensure_chrome_running` refuses `~/.config/google-chrome` before Chrome gets the chance (`chrome.py:50`). The spec measured it on Chrome 157.
- The owner's everyday profile and any managed work profile must never be automated (D11). `chrome_dir()` (`chrome.py:123`) always returns `$DATA/jobpilot-chrome`.
- The profile is the owner's applications browser and is signed into Google (Gmail), reversing D14 on the owner's decision of 2026-11-15: JobPilot uses that session for "Sign in with Google" on job sites and to read verification mail (`harness/mailbox.py`). Chrome Sync stays off. Community reports say Google can refuse sign-in in a browser started with a debug port; if it does, start the same `--user-data-dir` once without `--remote-debugging-port`, sign in, close it, and let JobPilot restart it: the session cookies carry over.

## Launch

### Native launcher

After the [native setup](../README.md#native-setup), run `./jobpilot start`. It checks
`uv`, Google Chrome, the frontend build, Tectonic, `curl`, writer availability and
the app port, starts the backend on loopback, and waits for `/api/health` before
calling `show_app`. That helper starts or reuses the dedicated JobPilot Chrome and
opens or focuses the app tab. The launcher finds Google Chrome on `PATH` or in the
macOS Applications directories. If the app tab cannot open, it prints the URL.

Complete **You** on the welcome page and choose **Open sign-in pages** to sign in to
Google and LinkedIn in this Chrome. `./jobpilot status` reports backend health,
Chrome and login state; `./jobpilot logs` follows the backend output.
`./jobpilot stop` leaves Chrome running. `./jobpilot stop --chrome` also closes it
cleanly through CDP, preserving the profile and logins for the next launch.

### Docker (optional)

With `docker compose up -d`, the container supervisor starts Xvfb, Chrome, x11vnc,
noVNC and the backend in order, waiting for each dependency. You control this Chrome
through [the noVNC view](http://127.0.0.1:6080/vnc.html?autoconnect=true), or **Open
the Chrome view** in the app at `http://127.0.0.1:8000`. Read its password from
`data/chrome-view.password` on the Docker host and sign in to Google and LinkedIn
there. The profile persists in `/app/data/jobpilot-chrome` (host `data/jobpilot-chrome`).

Compose sets `JOBPILOT_CHROME_VIEW_URL`; only the supervisor owns Chrome's lifecycle
in this mode. Chrome uses a sandbox and the supplied seccomp profile, with CDP on
container loopback port 9222. The supervisor records `DevToolsActivePort` for the
backend's attach helpers. It omits the native `AutomationControlled` flag below.
Neither CDP nor raw VNC (5900) is published; the app and Chrome view are published
on host loopback only.

**In Docker, no fallback browser is ever launched.** Endpoint discovery retries
three one-second probes with pauses (up to roughly five seconds). If Chrome is
still unavailable, `/api/browser/open` returns 503 and the application harness,
Tier 1 and Tier 2 fail instead of launching a per-site browser. A supervised child
exit stops the other processes; if Chrome's sandbox cannot start, startup fails
closed and never retries with the sandbox disabled. See the [Docker guide](docker.md)
for setup, private remote access and sandbox troubleshooting.

### Native Chrome helper

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

Never pass to the dedicated JobPilot Chrome (D12): `--enable-automation`, `--no-sandbox`, `--remote-allow-origins=*`, `--headless`, or browser-use's `disable_security=True`. browser-use attaches with `permissions=[]` and opens its own tab. Tasks close their page, never the context, because the context is the owner's session. `ensure_chrome_running` does not check `extra_args` against this list. Native callers (`show_app` and `POST /api/browser/open`) pass none.

Other helpers:

- `running_endpoint()` (`chrome.py:130`): the endpoint when the JobPilot Chrome is running, else `None`. It never starts Chrome. Every reader goes through it.
- `close_chrome(ws)`: CDP `Browser.close`. Chrome shuts down cleanly and flushes the cookie DB, which SIGTERM may not do. The native launcher calls it for `./jobpilot stop --chrome`.
- `my_chrome_pids(data_dir)`, `wait_gone(data_dir)`: processes started on this data dir only. Also not used by app code.
- `open_context(pw, profile_dir, args)`: `(context, attached)`. Uses the running JobPilot Chrome's default context. Native installs retain the per-site Playwright fallback when Chrome is absent; Docker raises an error instead.

## Risk: the local debug port

The CDP port listens on 127.0.0.1 without authentication. Any local process can find it and drive the logged-in Chrome (demonstrated in the proofs). Accepted for a single-user machine (spec, known unknowns). Automatic closing when idle is not built. Natively, Chrome keeps running after `./jobpilot stop`; use `./jobpilot stop --chrome` to close it. The data dir is mode 700, so other users cannot read `DevToolsActivePort` or the cookies. A process of the same user can. Docker keeps CDP on container loopback without publishing it; stopping the container also stops Chrome.

## API and setup wizard

`backend/api/browser.py`, prefix `/api/browser`:

| Route | Behaviour |
|---|---|
| `GET /status` (`browser_status`) | `{"running": false, "logged_in": {}}` when the Chrome is not running. It never starts it. Otherwise `{"running": true, "logged_in": {"linkedin": bool, "google": bool}}` |
| `POST /open` (`open_browser`) | `ensure_chrome_running(chrome_dir())` in a thread (503 if Chrome does not start). Then opens a tab (`PUT /json/new?<url>`) on each login page in `LOGIN_PAGES` (LinkedIn, Google/Gmail) whose site is not logged in. Needs the local session, like every POST under `/api` |

In Docker, both responses also include `view_url` from `JOBPILOT_CHROME_VIEW_URL`
and `view_password_hint: "data/chrome-view.password"`, never the password itself.
`POST /open` only attaches to the supervised Chrome and opens missing sign-in tabs;
it returns 503 if that Chrome is unavailable. It never calls the native launcher.

Login detection (`_logged_in` in `backend/api/browser.py`) attaches with Playwright and looks for a session cookie by name: `SESSION_COOKIES` checks LinkedIn's `li_at` at `https://www.linkedin.com` and Google's `SID` at `https://mail.google.com`. Indeed has no entry in either `LOGIN_PAGES` or `SESSION_COOKIES`, so `/open` does not open it and `/status` does not report it.

The welcome page's first step ("You") and the setup wizard show Google and LinkedIn login state with "Open sign-in pages" (`POST /open`) and a refresh (`GET /status`). They tell the owner to sign in to Google and LinkedIn there once. In Docker the control becomes **Open the Chrome view**, opens missing sign-in tabs, and links to noVNC with the host password-file hint.

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
`open_context`, using the dedicated JobPilot Chrome when running. Only native
installs can fall back to the existing per-site profile; Docker fails if its
supervised Chrome is unavailable. Stealth patches apply only to a native per-site
browser. It never closes an attached context. ASSISTED leaves the filled tab open
for the owner, with lists of
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
when configured. Isolated job-site account forms use the account flow below.

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

## Job-site accounts

Set your profile email and sign in to Google (Gmail) in the JobPilot Chrome from the
welcome page's **You** step. When an isolated job-site account page offers **Sign in
with Google**, **Continuer avec Google**, **Se connecter avec Google**, or Google's
embedded sign-in button, the harness prefers Google if that browser context has a
`SID` cookie for `https://mail.google.com` and the profile email is set. Without
those prerequisites, it keeps the password flow; a Google-only control does not
by itself trigger an account hand-off.

Code handles both Google popups and same-tab navigation. It selects only the account
whose whole email address matches the profile email, ignoring case. On consent
screens it clicks only **Continue**, **Continuer**, **Autoriser**, **Allow**,
**Weiter**, or **Zulassen**. It never types on Google pages. A missing matching
account, password prompt, identity/2-step verification, or captcha stops this path.
The harness waits up to 15 seconds for the Google window and up to 30 seconds for
the site to leave its account page. A closed popup alone is not success.

Successful Google sign-in appears in **Settings → Job-site accounts** with method
`google`, the profile email, and status `signed_in`. If Google cannot finish, the
harness uses the site's password form when available; otherwise it reports
**Sign-in needed on &lt;host&gt;** and leaves sign-in to the owner. Google account pages
are never handled by the decision model.

For the password flow, set the shared job-site password on the welcome page
(`JOB_SITE_PASSWORD` is the environment fallback). Code fills password
inputs, required terms are listed in the review, and marketing opt-ins stay
unticked. Email verification uses Gmail in the same browser. Passwords never enter
model requests or review summaries. New accounts are capped at 10 per local day.

Both paths require an isolated account page: any file input, textarea,
contenteditable element, or non-account field disqualifies it, including hidden
fields and fields in frames. Prior application typing or an upload also prevents
account automation. The application submit guard remains in place. LinkedIn,
Indeed, other sites requiring the owner's login, and Google's own hosts are
excluded from this account flow; only the dedicated Google helper handles Google's
sign-in screens.

The Google fixtures cover local popup and same-tab flows. The owner-run live check
against the FANUC/SuccessFactors posting is still pending.

## Not built

- **Challenge pause and resume (D13).** The proofs (`proofs/chrome/challenge.py`: Cloudflare, Turnstile, hCaptcha, reCAPTCHA and login-wall detection; `t5_challenge.py`: pause, then resume once a second client clears it; `pacing.py`: log-normal delays, per-site gap and cap, backoff after a challenge) are not wired into the app. The existing `backend/applier/captcha_handler.py` (WebSocket notice, then poll until cleared or 300 s) predates this design. The LinkedIn reader stops at a wall and does not pause.
- **Removing stored passwords and auto-login (D14).** `SiteCredential` and `BrowserSessionManager._attempt_auto_login` (`backend/scraping/session_manager.py:88`) are still there.
- **Closing Chrome automatically when idle.** Native users can close it explicitly with `./jobpilot stop --chrome`.
- **Session check for sites other than LinkedIn and Google (Gmail).**
