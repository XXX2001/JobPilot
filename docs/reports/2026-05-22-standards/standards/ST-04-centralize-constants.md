# ST-04 — Centralize magic numbers + path literals into `defaults.py`

> Category: structure · Effort: M · Risk: low · Ship-blocker: no
> Part of: [Naming & Standards backlog](../INDEX.md) · Recurs as code-review **LR-05**

## Problem
`defaults.py` exists to "centralise every magic number," but many operational values and path strings never made it in — they're scattered inline across scraper/applier/session modules. Tuning them means hunting through code, and duplicated literals drift.

## Why it matters (ship)
Operability: timeouts, step limits, the user-confirmation window, and storage paths should be tunable in one place before real users hit edge cases.

## Locations
**Timeouts / step limits / retries:**
- `applier/auto_apply.py:430` + `form_filler.py:247` (`timeout=1800` — same 30-min confirmation window, **duplicated**)
- `form_filler.py:153,188,198,207,258,325,350,359,367` (goto/fill/click timeouts 20_000/3_000/5_000)
- `captcha_handler.py:300,341` (`15_000`)
- `scraping/adaptive_scraper.py:157,159,242,244` (`max_steps=20/8`, `timeout=180/90`)
- `scraping/site_prompts.py:118` ("max_steps … 25" in prompt text — keep in sync)
- `scraping/session_manager.py:295` (`timeout=600`), `:393,449` (`asyncio.sleep(3)`)
- `scraping/adzuna_client.py:82` (`timeout=30.0`); `utils/browser_path.py:65` (`timeout=15`)

**Path literals:**
- `"browser_profiles"` in `main.py:80`, `form_filler.py:122,302`, `auto_apply.py:311`, `assisted_apply.py:178`, `captcha_handler.py:40`, `api/settings.py:444,689`, `adaptive_scraper.py:137,225`, `scrapling_fetcher.py:168`, `session_manager.py:79`
- `"browser_sessions"` / `state.json` in `main.py:79`, `api/settings.py:445,690`, `captcha_handler.py:39,102`, `session_manager.py:78,109,230,255`
- DB filename `jobpilot.db` in `database.py:43`

## Proposed change
Add grouped constants to `defaults.py` (`USER_CONFIRM_TIMEOUT_SECONDS = 1800`, `PAGE_GOTO_TIMEOUT_MS`, `FIELD_FILL_TIMEOUT_MS`, `AGENT_MAX_STEPS_*`, `AGENT_RUN_TIMEOUT_*`, `LOGIN_WAIT_TIMEOUT_SECONDS`, `SESSION_POLL_INTERVAL_SECONDS`, `ADZUNA_HTTP_TIMEOUT`, `BROWSER_PROFILES_DIRNAME`, `BROWSER_SESSIONS_DIRNAME`, `SESSION_STATE_FILENAME`, `DB_FILENAME`). Add a `state_path_for(source)` helper for the repeated `dir / source / "state.json"` join. Reference at call sites. **At minimum, dedupe the two `timeout=1800` literals.**

## Acceptance criteria
- [ ] Listed literals replaced by named constants from `defaults.py`
- [ ] The 30-min confirmation window is a single constant
- [ ] Prompt-text "25" kept consistent with the agent `max_steps` constant
- [ ] Behavior unchanged; tests pass

## Blast radius & risk
Low — value-preserving substitution. `DB_FILENAME`: keep the value `jobpilot.db` (don't change the persisted file path — see NM-02).

## Dependencies
None. Subsumes the older LR-05 (adaptive-scraper constants).
