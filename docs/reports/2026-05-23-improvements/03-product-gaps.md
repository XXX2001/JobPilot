# 03 — Product Gaps & Roadmap

**Scope.** What's missing that a real job seeker would want — features, integrations, workflow gaps. Forward-looking only; bugs and quality issues are tracked elsewhere.

---

## 1. Top 5 missing user-facing features

### 1. Follow-up reminders & nudges — **S–M**

A job seeker's response rate jumps when they nudge recruiters 5–7 days post-apply, yet there's no reminder mechanism. `Application.applied_at` exists but is never read by anything time-based.

**Entry point:** extend [`backend/scheduler/batch_runner.py`](../../../backend/scheduler/batch_runner.py) with a second job that queries `applications WHERE status='applied' AND applied_at < now()-7d AND no follow_up event`, then broadcasts via the existing `ws.py` `ConnectionManager` and writes a new `ApplicationEvent(event_type="follow_up_due")`.

**UI:** a "Needs follow-up" tab in [`frontend/src/routes/tracker/+page.svelte`](../../../frontend/src/routes/tracker/+page.svelte).

### 2. Cover-letter editor + per-job tailoring UI — **M**

The `LetterPipeline` and JOBPILOT marker injector already exist server-side, but the frontend has no `/letters` route — only `/cv`. Users can't preview, edit, or regenerate the cover letter that gets attached at apply time.

**Entry point:** mirror [`frontend/src/routes/cv/+page.svelte`](../../../frontend/src/routes/cv/+page.svelte) as `/letters`, hit existing `GET /api/documents/{match_id}/letter/pdf`, and add a `POST /api/documents/{match_id}/letter/regenerate` companion to the CV regenerate endpoint in [`backend/api/documents.py`](../../../backend/api/documents.py).

> *Pairs with [BE-D3 in 02-backend-quality](02-backend-quality.md#d3-letterpipeline-and-latexpipelinegenerate_diff-legacy-helper--medium-confidence-audit-then-delete) — building this is the alternative to deleting `LetterPipeline`.*

### 3. Application portfolio export — **S**

Job seekers need to share progress with coaches/career services and produce a record for unemployment-benefit reporting (required in FR/DE/many EU countries). No CSV/PDF export exists in [`backend/api/applications.py`](../../../backend/api/applications.py) or [`backend/api/analytics.py`](../../../backend/api/analytics.py).

**Entry point:** add `GET /api/applications/export?format=csv|pdf` that joins `applications + job_matches + jobs + application_events`. Trigger button on the Tracker page.

### 4. Salary intelligence & match-score breakdown — **M**

`JobMatcher.score()` writes `keyword_hits` JSON but the frontend only shows the scalar score (see `ScoreIndicator.svelte` in `jobs/[id]`). Users can't see *why* a job scored 72 — what they're missing skill-wise, or whether the salary beats their target. `search_settings.salary_min` is set but never surfaced in comparison.

**Entry point:** expand `GET /api/jobs/{id}` response to include `keyword_hits` and a diff against `UserProfile`; render a "Why this score" panel in [`frontend/src/routes/jobs/[id]/+page.svelte`](../../../frontend/src/routes/jobs/[id]/+page.svelte).

### 5. Pre-submit form preview/edit — **S**

The `apply_review` WebSocket payload already includes a screenshot, but users can only confirm or cancel — they can't *edit* a misfilled field. For free-text questions ("Why this role?") the Gemini-mapped answer is take-it-or-leave-it.

**Entry point:** extend the WS message in [`backend/applier/form_filler.py`](../../../backend/applier/form_filler.py) to include `fields[]` (selector + value), and the `WSMessage` union in [`backend/api/ws_models.py`](../../../backend/api/ws_models.py) to accept a `patch_fields` inbound message before `confirm_submit`. Wire to the review modal already living near the queue page.

---

## 2. Top 3 integration gaps

### Gmail / IMAP response detection — **highest leverage**

Already designed (see [`../2026-05-22-audit/03-gmail-integration-design.md`](../2026-05-22-audit/03-gmail-integration-design.md)) but unbuilt. Without this, the Tracker is purely manual-update; the product is "apply assistant" not "job-hunt CRM." Phase-1 read-only sync alone (M) turns dead application rows into a live pipeline. **This single integration probably 3× user-perceived value.**

### Calendar (Google/CalDAV) for interviews — **high**

Once Gmail detects an interview invite, writing it to the user's calendar with full job context (CV link, JD link, prep notes) is what closes the loop. Without calendar, the interview event just sits as an `ApplicationEvent` row. Builds naturally on top of Gap #1.

### LinkedIn profile import — **nice-to-have, not multiplier**

Tempting, but `UserProfile` is small and onboarding-only; users will fill it once. Lower priority than the two above despite seeming "obvious."

---

## 3. Top 3 workflow / UX gaps

### No first-run onboarding wizard

[`frontend/src/routes/settings/+page.svelte`](../../../frontend/src/routes/settings/+page.svelte) is a 1,091-line 6-tab god-page. A new user lands on `/` and faces an empty queue with no guided path to "upload CV → set keywords → connect a source → run first batch." The `getProfileStatus` helper already detects setup completeness — turn it into a stepper at `/onboarding`.

### No LaTeX template validation before first batch

`UserProfile.base_cv_path` accepts any `.tex`; failures only surface mid-batch when Tectonic errors on N jobs in a row. Add a synchronous "compile test" button in Settings → Profile that runs `LaTeXCompiler.compile()` on the bare template and shows the rendered PDF before the user saves.

### No "dry-run" for the morning batch

Users only see results post-scrape; they can't preview "what would my keywords match today?" without burning Gemini quota and committing rows. Add `POST /api/queue/refresh?dry_run=true` that runs scraping + matching but skips CV generation and DB writes.

---

## 4. Contrarian "don't build this"

### Don't build a mobile app, or even a mobile-responsive overhaul

It looks attractive — "apply on the go!" — but every load-bearing step requires a desktop browser: LaTeX preview, the Playwright apply-review modal with screenshots, the cover-letter diff view, manual login handoff for `BrowserSessionManager`. Mobile would be a thin notifier at best, which is better served by a single web-push integration (~1 day's work piggybacking on the existing WS infrastructure) than a native app.

**Spend the quarter on Gmail + calendar instead.**
