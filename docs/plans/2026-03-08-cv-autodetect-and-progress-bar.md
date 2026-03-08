# CV Auto-detection + Progress Bar + Browser Headless Config

**Date:** 2026-03-08
**Branch:** develop
**Status:** In progress

## Background

Three UX/reliability issues found after the CV pipeline redesign:

1. The user's CV at `data/templates/resume_faangpath.tex` is never used automatically — the system requires the DB profile field `base_cv_path` to be set via the settings UI, and if it's empty it silently skips all CV generation.
2. The frontend status bar never shows batch progress — `broadcast_status()` sends `{"type": "status", ...}` but the StatusBar component only handles `scraping_progress`, `matching_progress`, `tailoring_progress`, `login_required`, `error`. Generic `status` messages fall through to "Ready".
3. The scraper browser is `headless=True` (hardcoded) — users can't see what the browser-use agent is doing. No config option to override.

---

## Task 1 — CV auto-detect fallback in `morning_batch.py`

**File:** `backend/scheduler/morning_batch.py`

**Spec:**
- In `_run_batch_inner`, after reading `cv_path` from `profile_row.base_cv_path` (line 139–141), add a fallback:
  - If `cv_path is None` OR `not cv_path.exists()`, scan `Path(settings.jobpilot_data_dir) / "templates"` for `*.tex` files
  - Use `sorted(candidates)[0]` (alphabetical, deterministic) if any exist
  - Log a warning when falling back: `logger.warning("No base_cv_path in profile — using auto-detected CV: %s", cv_path)`
- The rest of the function is unchanged — `cv_path` is used exactly as before

**Test (unit):**
Add `tests/test_morning_batch_cv_fallback.py`:
- `test_cv_path_from_profile_used_when_set`: mock `profile_row.base_cv_path = "/some/path.tex"` where path exists → cv_path equals that
- `test_cv_path_auto_detected_from_templates`: profile has no `base_cv_path`, but `data_dir/templates/` has a `.tex` file → cv_path is that file
- `test_cv_path_none_when_no_templates`: profile has no path, templates dir empty → cv_path is None

These are pure unit tests — mock the filesystem with `tmp_path` and monkeypatch `settings.jobpilot_data_dir`.

---

## Task 2 — Setup status auto-detect fallback in `settings.py`

**File:** `backend/api/settings.py`

**Spec:**
- In the setup status handler (around line 298–302), change `base_cv_uploaded` logic:
  - Current: only True if `profile.base_cv_path` is set AND that path exists
  - New: also check `Path(settings.jobpilot_data_dir) / "templates" / "*.tex"` for any `.tex` file using `glob`
  - `base_cv_uploaded = True` if either the profile path exists OR a `.tex` file is found in templates/
- Import `settings as app_settings` from `backend.config` (already imported elsewhere in the file as `settings` — check existing imports before adding)
- No other changes to the endpoint

**Test:**
Add to an appropriate existing test file (or new `tests/test_settings_setup_status.py`):
- `test_setup_status_cv_uploaded_from_profile`: profile has valid path → `base_cv_uploaded: true`
- `test_setup_status_cv_uploaded_from_templates`: profile empty, templates/ has `.tex` → `base_cv_uploaded: true`
- `test_setup_status_cv_not_uploaded`: profile empty, templates/ empty → `base_cv_uploaded: false`
Use `AsyncClient` from httpx + override FastAPI deps for DB.

---

## Task 3 — Progress bar in `StatusBar.svelte`

**File:** `frontend/src/lib/components/StatusBar.svelte`

**Spec:**
- Add handling for `type === 'status'` BEFORE the existing `else` fallback:
  ```svelte
  {:else if $lastMessage.type === 'status'}
    <span class="flex-1 truncate">{$lastMessage.message}</span>
    {#if $lastMessage.progress > 0 && $lastMessage.progress < 1}
      <div class="w-32 h-1 bg-muted rounded-full overflow-hidden">
        <div
          class="h-full bg-primary transition-all duration-300"
          style="width: {$lastMessage.progress * 100}%"
        ></div>
      </div>
      <span class="tabular-nums text-xs">{Math.round($lastMessage.progress * 100)}%</span>
    {/if}
  ```
- The status bar layout uses `flex items-center gap-3` — the new elements fit naturally
- `progress` field: the WS message shape is `{type: "status", message: string, progress: number}`; the frontend store already keeps raw JSON objects, so `$lastMessage.progress` and `$lastMessage.message` are available

**No backend changes needed for this task.**

---

## Task 4 — Keep refresh button spinning until batch completes

**File:** `frontend/src/routes/+page.svelte`

**Spec:**
- `refreshQueue()` currently sets `refreshing = false` as soon as the HTTP POST returns (which is instant — the batch runs in the background). The spinner stops immediately.
- New behaviour:
  1. `refreshQueue()` sets `refreshing = true` but does NOT set it back to `false` after the POST
  2. In the `$effect` that watches `$messages`, also check for `type === 'status'` with `progress >= 1.0`:
     ```ts
     if (lastMsg.type === 'status' && lastMsg.progress >= 1.0) {
       refreshing = false;
       await loadQueue();
     }
     ```
  3. Keep a safety timeout: if no completion message arrives within 5 minutes, clear `refreshing` anyway (use `setTimeout` stored in a variable, cleared when completion arrives)

**Note:** The `$effect` in Svelte 5 must be async-safe — `loadQueue()` returns a Promise; call it without `await` inside the effect (it's already fire-and-forget).

---

## Task 5 — `JOBPILOT_SCRAPER_HEADLESS` config + adaptive scraper

**Files:** `backend/config.py`, `backend/scraping/adaptive_scraper.py`

**Spec (config.py):**
- Add one field to the `Settings` class:
  ```python
  jobpilot_scraper_headless: bool = Field(True, env="JOBPILOT_SCRAPER_HEADLESS")
  ```
  Place it with the other `jobpilot_*` settings (after `jobpilot_log_level`).

**Spec (adaptive_scraper.py):**
- Find the two `Browser(headless=True, ...)` calls (around lines 155–158)
- Replace the hardcoded `True` with `settings.jobpilot_scraper_headless`
- Import `settings` from `backend.config` if not already imported (check existing imports)
- Do NOT change the `headless=False` calls in `session_manager.py` or `auto_apply.py` — those are intentional

**Test:**
Add `tests/test_config_scraper_headless.py`:
- `test_default_is_headless`: `Settings(... JOBPILOT_SCRAPER_HEADLESS not set ...)` → `.jobpilot_scraper_headless is True`
- `test_can_disable_headless`: set env `JOBPILOT_SCRAPER_HEADLESS=false` → `.jobpilot_scraper_headless is False`
Use `pydantic_settings` env override or `monkeypatch.setenv`.
