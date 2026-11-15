# Job-site accounts: Google sign-in first, email and password otherwise

Date: 2026-11-15. Status: approved in chat by the owner, with the changes below.

## Why

Company application sites (SuccessFactors, Workday, Taleo, iCIMS, Teamtailor…) put a
sign-in page between the posting and the form. Live run 2026-11-15: LinkedIn → FANUC
posting → "Postuler" → SuccessFactors "E-mail / Mot de passe / Connexion / Créer un
compte". Today the harness hands that page to the owner and waits up to 10 minutes. The
owner wants JobPilot to get through it alone and keep a list of the accounts in the app.

## Owner's decisions

1. **The JobPilot Chrome is the applications profile.** It holds the owner's Google
   (Gmail) login next to LinkedIn and the job-site sessions. Onboarding asks the owner to
   sign in to Google there first.
2. **Google first.** When a sign-in page offers "Sign in with Google" / "Continuer avec
   Google", JobPilot uses it, with the Google account signed in to the JobPilot Chrome.
3. **Otherwise email and one constant password**, chosen by the owner (the risk of one
   breached site exposing the others was explained and accepted). Email = profile email.
4. **Required terms boxes** on a sign-up form are ticked by JobPilot and listed in the
   review. Marketing, newsletter and job-alert opt-ins stay unticked.
5. **A list of accounts** in the app.

## What gets built

### 1. Google in the JobPilot Chrome
- `backend/api/browser.py`: `LOGIN_PAGES["google"]` = the Google sign-in page continuing to
  Gmail; `SESSION_COOKIES["google"]` = the `SID` cookie on `https://mail.google.com`.
  "Open the JobPilot Chrome" then shows the Google sign-in when it is missing.
- Onboarding step 1 ("Tools") and the ready strip show Google and LinkedIn login state,
  with a button that opens the sign-in pages in the JobPilot Chrome. The owner types the
  Google password and any 2FA by hand; JobPilot never handles the Google password.

### 2. Password and account registry
- `.env` gains `JOB_SITE_PASSWORD` (SecretStr). Unset: email/password sign-up is off and
  such pages are handed to the owner as today (Google sign-in still works).
- The password is typed by code into password inputs only. It never enters a model
  request, a log line, a websocket message or the review summary. Password inputs stay
  out of the snapshot, as now.
- Reuse `site_credentials` (Fernet-encrypted email and password, unique `site_name`).
  `site_name` is the sign-in host plus a tenant hint where the host is shared
  (SuccessFactors `company=`, Workday tenant subdomain), so one row is one account.
  New columns (Alembic): `method` (`google`, `password`), `status` (`created`,
  `verified`, `signed_in`, `failed`), `login_url`, `last_used_at`, `note`.
- Settings → **Job-site accounts**: site, method, email, status, created, last used, a
  link to the sign-in page, Delete. No password shown.

### 3. Harness: account pages
- An **account page** has a visible password input or a "Sign in with Google" control,
  and no file input and no textarea. Only there may the harness click sign-in /
  create-account / register / continue / "Connexion" / "Valider" controls; such a click
  cannot send an application. Pages with a file input or textarea keep today's submit
  guard unchanged.
- **Google path**: click the site's Google button. The Google window (popup or same tab)
  is handled by code, not the model: pick the account whose email is the profile email,
  then "Continue" / "Continuer" / "Autoriser" on the consent page. Back on the site, the
  harness carries on. A Google page asking for a password or 2FA is handed to the owner.
- **Password path**: sign in when the registry has an account for the site, else follow
  "Créer un compte" / "Create account" / "Register". Fill email, every visible password
  input (password and confirm) with `JOB_SITE_PASSWORD`, and other sign-up fields from the
  profile slots. Tick required terms boxes (recorded in `chosen`), never opt-ins.
- A sign-in error ("incorrect", "invalide", "n'existe pas") switches to sign-up once; a
  sign-up error ("already exists", "existe déjà") switches to sign-in once. A second
  failure marks the row `failed` and hands off to the owner.
- Captcha: handed to the owner, as today. Never on linkedin.com, indeed.com or other
  `SITE_CONFIGS` sites with their own owner login. At most 10 new accounts per day.

### 4. Email verification through Gmail in the JobPilot Chrome
- No Gmail API setup: JobPilot reads mail through the Google session in the JobPilot
  Chrome. It polls the Gmail Atom feed (`https://mail.google.com/mail/feed/atom`, in a
  background tab of that Chrome) every 10 s for up to 5 minutes for an unread message
  received after the sign-up click whose sender or title mentions the site host or the
  company, then opens that message in a background tab and reads its text and links.
- A code (4–8 digits next to code/verification/vérification) is typed into the code
  field. A verification link (http(s), same registrable domain as the site or a known ATS
  mail domain) is opened with a GET in a new tab, then the harness returns to the
  application tab and continues. The message tab is closed; nothing is deleted or sent.
- No message within 5 minutes: hand off to the owner with "check your email".
- The mail is untrusted: only a code or a link matching the rules above is used.

## Not doing
- Per-site passwords, password resets, "Apply with LinkedIn" buttons on ATS sites.
- Creating accounts on LinkedIn, Indeed, Welcome to the Jungle or email providers.
- Solving captchas; typing the Google password.

## Left to the builder
- Exact sign-in / sign-up / Google-button word lists (French, English, German at least).
- How the Settings list looks, within the existing settings tabs.
