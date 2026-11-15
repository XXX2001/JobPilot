# Job-site Accounts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The harness gets through job-site sign-in pages on its own: Google sign-in when offered, else email + one constant password, with email verification read from Gmail in the JobPilot Chrome, and a list of the accounts in Settings.

**Architecture:** Code, not the model, owns everything secret or account-specific: password typing, the Google window, the mailbox, the registry. The decision model keeps filling ordinary sign-up fields (names, phone, country) through the existing loop; on an *account page* the click guard additionally allows sign-in / sign-up controls. New modules sit next to the harness: `accounts.py` (registry), `mailbox.py` (Gmail via the browser), `account_flow.py` (page detection, Google flow, password fill, terms).

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy + Alembic, Playwright (CDP into the JobPilot Chrome), Svelte 5.

**Spec:** `docs/superpowers/specs/2026-11-15-job-site-accounts-design.md`

## Global Constraints

- The job-site password (app-stored or `JOB_SITE_PASSWORD`) never appears in a model request, log line, websocket message, `HarnessResult`, exception text or test snapshot. Tests assert this.
- Password inputs stay out of `snapshot.js` output (unchanged).
- Account behaviour never runs on hosts of `SITE_CONFIGS` entries with `requires_login` (LinkedIn, Indeed…) nor on `google.com` / `accounts.google.com` except inside the Google flow.
- Pages with a visible file input or textarea keep today's submit guard unchanged.
- Max 10 new accounts per local day (`MAX_NEW_ACCOUNTS_PER_DAY = 10`).
- Mail polling: every 10 s, at most 300 s; only unread messages newer than the sign-up click.
- Commit format `<type>(<scope>): <desc>`, under 72 chars, no co-author trailers.
- Run tests with `env -u PYTHONPATH uv run pytest`.

## Review Focus

1. A combined "create account and apply" page (password field + CV upload): must be treated as an application page, never as an account page.
2. The password reaching logs through an exception message (Playwright `fill` timeout errors echo the value): wrap and re-raise without it.
3. A Google account chooser listing several accounts: pick only the one whose email equals the profile email; otherwise hand off.
4. A verification email from a different site arriving in the window: the link must match the site's registrable domain or a known ATS mail domain, else ignored.
5. Sign-in failing with an existing registry row whose password changed by hand: one sign-up retry, then `failed` + hand-off, no loop.

---

### Task 1: Account registry, job-site password storage, Settings list

**Files:**
- Modify: `backend/config.py` (`JOB_SITE_PASSWORD: SecretStr = SecretStr("")` as an `.env` fallback), `.env.example`, `docs/configuration.md`
- Create: `alembic/versions/a01_job_site_accounts.py` (down_revision `m04_match_score_flags`)
- Modify: `backend/models/user.py` (`SiteCredential` columns; `UserProfile.encrypted_job_site_password String null`)
- Create: `backend/applier/harness/accounts.py`
- Modify: `backend/api/settings.py` (routes), `frontend/src/lib/components/settings/SitesTab.svelte`
- Test: `tests/test_job_site_accounts.py`

**Interfaces:**
- Produces (in `accounts.py`):
  - `site_key(url: str) -> str` — host, plus `?company=<x>` for `*.successfactors.*`; the tenant host for `*.myworkdayjobs.com` / `*.myworkday.com`; lower-case.
  - `async def job_site_password() -> str | None` — the app-stored password (decrypted), else `settings.JOB_SITE_PASSWORD`, else None.
  - `async def set_job_site_password(value: str) -> None` — encrypts with the existing Fernet `CREDENTIAL_KEY` into `UserProfile.encrypted_job_site_password` (creates profile row 1 if missing); `""` clears it.
  - `async def find_account(key: str) -> AccountRow | None`
  - `async def record_account(key: str, *, method: Literal["google","password"], email: str, status: str, login_url: str, note: str = "") -> None` (upsert; email encrypted; password column left empty: the one password lives in the profile)
  - `async def new_accounts_today() -> int`
  - `AccountRow` dataclass: `key, method, email, status, login_url, created_at, last_used_at, note` (no password).
- Produces (API):
  - `GET /api/settings/job-site-password` → `{"set": bool, "source": "app" | "env" | null}` (never the value)
  - `PUT /api/settings/job-site-password` body `{"password": str}` (8–128 chars, or `""` to clear) → same shape
  - `GET /api/settings/job-site-accounts` → list of `AccountRow`; `DELETE /api/settings/job-site-accounts/{key}` → 204
- Columns added to `site_credentials`: `method`, `status`, `login_url` (String null), `last_used_at` (DateTime null), `note` (String null). Existing rows untouched.

- [ ] **Step 1: Failing tests**: `site_key` cases (`career2.successfactors.eu/careers?company=fanuceurop` → `career2.successfactors.eu?company=fanuceurop`; `acme.wd3.myworkdayjobs.com/x` → `acme.wd3.myworkdayjobs.com`; `jobs.lever.co/acme` → `jobs.lever.co`); password PUT then GET returns `set: true, source: "app"` and the DB column is ciphertext; env fallback gives `source: "env"`; no API response or log record contains the password; `record_account`/`find_account` round-trip; accounts list never contains a password; `new_accounts_today` counts only today's rows created today.
- [ ] **Step 2:** run, expect FAIL.
- [ ] **Step 3:** implement migration, model columns, `accounts.py`, routes, config field. Settings → Sites tab gets a "Job-site accounts" card: site, method, email, status, created, last used, link to `login_url`, Delete; empty state "JobPilot lists accounts here when it signs up or signs in on a job site."
- [ ] **Step 4:** migration applies on a temp DB in the test; pytest file, frontend check/test: PASS.
- [ ] **Step 5:** commit `feat(accounts): job-site account registry, password and list`.

### Task 2: Welcome page collects everything the owner provides

**Files:**
- Modify: `backend/api/browser.py` (`LOGIN_PAGES`, `SESSION_COOKIES`)
- Modify: `frontend/src/routes/onboarding/+page.svelte`, `frontend/src/lib/utils/onboarding.ts` (+ its test), `frontend/src/lib/components/ReadyStrip.svelte`
- Test: `tests/test_browser_api.py` (create if absent), `frontend/src/lib/utils/onboarding.test.ts`

**Interfaces:**
- Consumes: Task 1 password routes; existing `PUT /api/settings/profile`, `GET /api/settings/profile`.
- Produces: `GET /api/browser/status` → `{"running": bool, "logged_in": {"linkedin": bool, "google": bool}}`.

- [ ] **Step 1: Failing test** `test_status_reports_google_login`: monkeypatch `running_endpoint` and the cookie read so the context returns a `SID` cookie for `https://mail.google.com`; assert `logged_in == {"linkedin": False, "google": True}`.
- [ ] **Step 2:** run, expect FAIL.
- [ ] **Step 3:** `LOGIN_PAGES["google"] = "https://accounts.google.com/ServiceLogin?continue=https://mail.google.com/mail/"`, `SESSION_COOKIES["google"] = ("https://mail.google.com", "SID")`.
- [ ] **Step 4:** the onboarding page ("Welcome to JobPilot") step 1 becomes **"You"**: full name, email, phone, location, LinkedIn URL (prefilled from the profile, saved with PUT profile), the job-site password (write-only password input with show/hide, "set" indicator, saved with PUT job-site-password, helper text: "Used to create and sign in to accounts on company job sites. Stored encrypted on this machine."), and the JobPilot Chrome sign-ins: Google (Gmail) and LinkedIn status from `/api/browser/status` with a button "Open sign-in pages" (POST `/api/browser/open`) and a Refresh. The tool checks (writing model, Tectonic, Adzuna optional) stay, below. Steps stay four: You, CV, Keywords, First batch. `firstIncompleteStep` returns 1 while name or email is empty or no writing model/Tectonic; password and sign-ins are recommended, not required. Copy: "JobPilot's Chrome is your applications browser: sign in to Google and LinkedIn there once."
- [ ] **Step 5:** `npm run check -- --fail-on-warnings`, `npm run test`, pytest: PASS.
- [ ] **Step 6:** commit `feat(onboarding): collect profile, job-site password and sign-ins`.

### Task 3: Mailbox through the JobPilot Chrome

**Files:**
- Create: `backend/applier/harness/mailbox.py`
- Test: `tests/test_harness_mailbox.py`

**Interfaces:**
- Produces:
  - `extract_code(text: str) -> str | None` — 4–8 digit (or 6-char alphanumeric uppercase) code within 80 chars of code / verification / vérification / Bestätigung / OTP; ignores years (19xx/20xx alone) and phone-like numbers.
  - `pick_link(links: list[str], site_host: str) -> str | None` — first http(s) link whose registrable domain equals the site's, or is in `ATS_MAIL_DOMAINS` (`successfactors.com`, `successfactors.eu`, `myworkday.com`, `myworkdayjobs.com`, `taleo.net`, `icims.com`, `smartrecruiters.com`, `greenhouse.io`, `lever.co`, `teamtailor.com`, `workable.com`, `jobvite.com`, `recruitee.com`) and whose path or text suggests verify / confirm / activate / valider / activer.
  - `async def wait_for_verification(context, *, since: datetime, site_host: str, company: str, timeout_s: float = 300, poll_s: float = 10) -> Verification | None` where `Verification = dataclass(code: str | None, link: str | None)`. Reads `https://mail.google.com/mail/feed/atom` in a new background page of `context` (unread entries: title, summary, issued, link), keeps entries issued after `since` whose author email domain, title or summary mentions `site_host`'s registrable domain or `company` (case-insensitive), opens the newest one's link in the same background page, reads `document.body.innerText` and all `a[href]`, returns the code or link. Closes its page. Never clicks inside Gmail.

- [ ] **Step 1: Failing tests**: `extract_code` on French/English/German samples and on "© 2026" / "+33 6 12 34 56 78" (None); `pick_link` accepts same-domain and ATS-domain verify links, rejects `https://evil.example/verify` and `mailto:`; `wait_for_verification` against a Playwright context routing `mail.google.com/mail/feed/atom` to a fixture Atom XML and the message URL to fixture HTML (one older entry, one other-company entry, one matching) → returns the matching code; returns None after timeout with `timeout_s=0.5, poll_s=0.1`.
- [ ] **Step 2:** run, expect FAIL.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** PASS.
- [ ] **Step 5:** commit `feat(harness): read verification mail through the JobPilot Chrome`.

### Task 4: Account pages in the harness (password path)

**Files:**
- Create: `backend/applier/harness/account_flow.py`
- Modify: `backend/applier/harness/agent.py` (loop: account page step before decide; `click_stop`; hand-off condition), `backend/applier/harness/runner.py` (pass password, email, company, registry, mailbox in)
- Test: `tests/test_harness_account_flow.py`, fixtures `tests/fixtures/harness/account_signin.html`, `account_signup.html`, `account_verify_code.html`, `account_and_apply.html` (password + CV upload)

**Interfaces:**
- Consumes: Task 1 `site_key`, `job_site_password`, `find_account`, `record_account`, `new_accounts_today`; Task 3 `wait_for_verification`.
- Produces (in `account_flow.py`):
  - `ACCOUNT_PAGE_JS` and `async def account_page(page) -> AccountPage | None` — `AccountPage(has_password: bool, password_inputs: int, google_button: bool, signup_link: bool, code_field: bool, check_email_text: bool)`; None when the page has a visible file input or textarea, or no password input / Google button / code field.
  - `async def fill_passwords(page, password: str) -> int` — fills every visible empty `input[type=password]` (top frame and same-origin frames); on Playwright error raises `AccountError("password field could not be filled")` without the value.
  - `async def tick_required_terms(page) -> list[str]` — ticks unchecked visible checkboxes whose label matches terms/conditions/privacy/confidentialité/CGU/données personnelles/j'accepte/I agree and not newsletter/marketing/offres/alertes/partenaires/promotional/job alert; returns the labels ticked.
  - `SIGNIN_WORDS`, `SIGNUP_WORDS` regexes (fr/en/de): connexion, se connecter, sign in, log in, anmelden / créer un compte, s'inscrire, create account, register, registrieren, plus continue/continuer/suivant/valider/submit on an account page.
- Agent changes:
  - Each step, before `decide`: `acct = await account_page(page)` (skipped on `SITE_CONFIGS` login hosts). If `acct` and `job_site_password()` is set: `fill_passwords`; if sign-up mode, `tick_required_terms` → append to `result.chosen`; if `acct.code_field` or `acct.check_email_text`: `wait_for_verification` → type code into the code field / open link in a new page then `bring_to_front` the harness page; none → `handoff("login_wall")`.
  - Mode: sign-in when `find_account(site_key(url))`, else sign-up: if the page has a sign-in form and `signup_link`, click the sign-up link (by `SIGNUP_WORDS`) first. Cap: `new_accounts_today() >= 10` → handoff.
  - `click_stop`: when the current snapshot is an account page, a click matching `SIGNIN_WORDS | SIGNUP_WORDS` returns None (allowed) even if `_STRICT_SUBMIT` matches. Unchanged everywhere else.
  - Hand-off: `login_wall` hands off only when no account path applies (no password configured and no Google button, or `failed`).
  - A run that ends blocked on a sign-in page returns `awaiting_user_submission` with "Sign-in needed on <host>" and does not fall through to the older filler (live 2026-11-15: the fallback reported "Form pre-filled" on a sign-in page).
  - After a sign-in/sign-up click: page text matching error words (incorrect, invalide, introuvable, n'existe pas, already exists, existe déjà, bereits) → switch mode once; second failure → `record_account(..., status="failed")` and handoff. Leaving account pages → `record_account(status="signed_in"|"created"|"verified")`, `last_used_at=now`.
- [ ] **Step 1: Failing tests** with the scripted decision client used in `test_harness_agent.py`: sign-in fixture with a registry row → password filled, "Connexion" clicked, row `signed_in`; sign-up fixture → passwords (2) filled, required terms ticked, newsletter left, "Créer un compte" clicked, row `created`, `chosen` lists the terms; `account_and_apply.html` → `account_page` is None and "Postuler" still stops at `submit_point`; verification-code fixture with a stub `wait_for_verification` → code typed; password string absent from caplog text, `HarnessResult` repr and every decision request body.
- [ ] **Step 2:** run, expect FAIL.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** PASS; full suite PASS; `uv run pyright backend/` ≤ 19 errors.
- [ ] **Step 5:** commit `feat(harness): sign up and sign in on job-site account pages`.

### Task 5: Google sign-in path and live check

**Files:**
- Modify: `backend/applier/harness/account_flow.py` (`GOOGLE_BUTTON_WORDS`, `async def google_sign_in(page, email) -> bool`), `agent.py` (prefer Google when `acct.google_button` and the browser has a Google session)
- Test: `tests/test_harness_account_flow.py` (Google flow against fixture pages for the chooser and consent screens, served for `accounts.google.com` via routing)
- Docs: `docs/browser.md` (accounts section), `CHANGELOG.md`

**Interfaces:**
- `google_sign_in(page, email)`: clicks the site's Google button (text/aria/title matching "google" with sign in/continue/connect/se connecter/continuer, or an iframe from `accounts.google.com/gsi`); waits for a popup (`context.wait_for_event("page")`) or same-tab navigation to `accounts.google.com`; on the chooser clicks the element containing `email` exactly (else returns False); on consent clicks Continue/Continuer/Autoriser/Allow; returns True when the original page leaves its account page within 30 s. A Google page with a password input or 2FA text returns False (agent then hands off). Records `method="google"`.
- [ ] **Step 1:** failing tests: chooser with two accounts picks the profile one; chooser without it returns False; password page returns False; popup closes and site page changes → True.
- [ ] **Step 2–4:** implement, PASS, full suite, pyright.
- [ ] **Step 5:** live check (owner watching, nothing submitted): FANUC posting (match 22) with the job-site password set reaches past the SuccessFactors sign-in to the application form and stops before submit; the account appears in Settings.
- [ ] **Step 6:** commit `feat(harness): Google sign-in on job-site account pages`.
