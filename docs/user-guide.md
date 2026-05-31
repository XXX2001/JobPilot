# JobPilot User Guide

This is a practical, end-to-end walkthrough of JobPilot: what you click, where, and what each action does. It follows the order you would naturally use the app, from first launch to tracking applications.

If you have not installed JobPilot yet, start with the [README quickstart](../README.md#quickstart). For how the system works under the hood, see the [architecture reference](architecture.md); for the secrets JobPilot stores and how they are protected, see [Credentials & encryption](architecture.md#credentials--encryption).

---

## 1. First-run onboarding (`/onboarding`)

The first time you open JobPilot at **http://localhost:8000**, the dashboard checks your setup status. If anything essential is missing, it redirects you to the onboarding wizard at **`/onboarding`** (once per session — you can choose to do it later). You can also reach it directly at any time.

The wizard has four steps, and it resumes on the first one that still needs attention:

1. **API Keys** — Confirms that `GOOGLE_API_KEY`, `ADZUNA_APP_ID`, and `ADZUNA_APP_KEY` are present in your `.env`. The step shows the exact snippet to paste. These are read at startup, so if you add them here you may need to restart the app.
2. **CV Template** — Upload your base LaTeX CV (a `.tex` file). This becomes the template that every tailored CV is derived from. See [Custom CV templates](custom-templates.md) for what makes a template compatible.
3. **Keywords** — Add the search keywords that describe the roles you want. These feed both job discovery and relevance scoring.
4. **First Batch** — Pick a job source and launch your first discovery batch.

When all four steps are complete, the wizard hands you off to the dashboard. The dashboard (`/`) shows today's new matches, week stats, and source health at a glance.

---

## 2. Configure profile, keywords, and sources (Settings)

The **Settings** page is split into focused tabs. Each tab loads and saves independently, so your unsaved edits in one tab are preserved while you switch around.

- **Profile** — Your name, email, phone, and location (these get injected into application forms), plus the **Base CV Path** and base letter path. This is also where the **template compile-test** button lives (see [section 6](#6-cv-and-letters-editors)).
- **Search** — Keywords (include / exclude), locations, countries, salary minimum, the per-day application limit, and the minimum match score below which jobs are discarded.
- **Sites** — Toggle the built-in job boards (LinkedIn, Indeed, Glassdoor, Welcome to the Jungle, Google Jobs) on or off.
- **Credentials** — Store per-site login credentials, encrypted at rest. You can also clear a saved browser session here.
- **Sources** — Manage discovery sources, including adding your own custom "lab" URLs (company or research-lab pages) to scrape.
- **Integrations** — Connect or disconnect Gmail (see [section 9](#9-gmail-integration)).
- **System** — App-level status and information.

Spend a moment in **Search** and **Sources**: keywords and enabled sources are what every batch run depends on.

---

## 3. Running a batch and the dry-run preview

JobPilot does not run on a schedule — batches run only when you trigger them. From the **Queue** page you have two buttons:

- **Preview today's matches** (dry run) — Runs the scrape + match/rank steps **inline** and shows you what today's batch *would* surface, **without writing anything to the database and without making the CV-tailoring AI calls**. Use this to sanity-check your keywords and sources before committing. The preview lists each candidate match with its title, company, location, and score. Dismiss it when you're done.
- **Refresh / Scan for jobs** — Triggers the real batch in the background and returns immediately. The pipeline scrapes your enabled sources, deduplicates, scores against your search settings, stores the survivors, and pre-generates tailored CVs for the top matches. Progress is pushed live to the UI over a WebSocket, and the queue reloads when it finishes.

If a batch is already running, a second trigger is rejected (you'll see a "search already in progress" message) — wait for the current run to finish.

---

## 4. Reviewing the queue and applying

The **Queue** page lists today's matches. For each match you choose how to apply. There are three modes:

- **Auto** — JobPilot opens the application form, uses AI to map your details onto the form fields, fills them in, and then **pauses for your review before submitting**. You confirm or cancel; nothing is submitted without your say-so.
- **Assisted** — JobPilot fills the form but leaves the browser open so you can review and submit it yourself manually.
- **Manual** — JobPilot simply opens the application URL in your browser. No automation happens; you do everything by hand.

Auto and assisted modes respect your daily application limit.

### Pre-submit field editing and the review modal

In **auto** mode, once the form is filled, JobPilot takes a screenshot and opens a **review modal** in the UI showing what it filled. Before you confirm:

- **Edit any mis-filled field** directly in the modal. Your edits are sent back to the live browser (a `patch_fields` action) so the form is corrected before submission.
- **Confirm** to submit the application, or **Cancel** to abort.

This gives you a final human checkpoint on every automated submission.

You can also **skip** a match you're not interested in, which removes it from the active queue.

---

## 5. The job detail page and "Why this score"

Click into any match to open its **job detail** page (`/jobs/[id]`). Here you can:

- Read the full listing (title, company, location, salary, description).
- Launch any of the three apply modes.
- View the CV diff — exactly which lines the AI changed in your tailored CV.

### "Why this score"

The detail page includes a **"Why this score"** panel that explains the relevance score in plain terms. It combines the per-keyword hit breakdown (which of your keywords matched, and where) with a comparison against your configured salary expectations, so you can see *why* a job ranked where it did rather than just trusting a number.

---

## 6. CV and Letters editors

- **CV editor (`/cv`)** — Browse your tailored CV history, preview the generated PDFs, and inspect the diff between your base template and each tailored version. You can **regenerate** a tailored CV for a match if you want a fresh pass.
- **Letters view (`/letters`)** — The cover-letter counterpart of the CV editor. View the tailored letter PDFs and **regenerate** the customized paragraph when you want a different take.

### Template compile-test (Settings → Profile)

Before relying on your template in a batch, use the **compile-test** button on **Settings → Profile**. It compiles your current base CV template with Tectonic and reports success or the compilation error — a quick way to catch a broken `.tex` template before it blocks an application. For template requirements, see [Custom CV templates](custom-templates.md).

---

## 7. The application tracker

The **Tracker** page (`/tracker`) is your record of everything you've applied to. Each application moves through statuses — applied, interview, offer, rejected, and so on — and you can update an application's status and add notes or events as your job hunt progresses. This is where you keep the human side of the pipeline organized.

---

## 8. Daily limit and source health

- JobPilot enforces a **daily application cap** (configurable in Settings → Search) across auto and assisted applies, so you don't accidentally over-apply.
- The dashboard and queue surface **source health**, so you can tell at a glance whether a given board is returning results.

---

## 9. Gmail integration

Gmail is **optional** and disabled until you configure it. To use it, set the Gmail OAuth values in `.env` (see the Gmail section of `.env.example`), then connect from **Settings → Integrations**:

1. Click **Connect** to start the OAuth flow. JobPilot requests read-only mailbox access.
2. Once connected, JobPilot can sync recent mail and surface application-related correspondence, which you can review and link to specific applications (the **Inbox** view, `/inbox`).
3. Click **Disconnect** at any time to revoke the connection from JobPilot's side.

Your Gmail OAuth tokens are encrypted at rest with the same key that protects your site credentials. See [Credentials & encryption](architecture.md#credentials--encryption) for what that key protects, what happens if you lose it, and how to back it up.

---

## Where to go next

- **[Architecture](architecture.md)** — how discovery, scoring, tailoring, and applying actually work, plus the full database schema.
- **[Custom CV templates](custom-templates.md)** — bring your own LaTeX template.
- **[Contributing](../CONTRIBUTING.md)** — if you want to develop JobPilot.
