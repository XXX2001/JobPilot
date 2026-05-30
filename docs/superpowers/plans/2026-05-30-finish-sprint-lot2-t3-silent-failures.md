# Finish Sprint — Lot 2 (T3): Silent-Failure Elimination — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn ten silently-swallowed/hidden failure paths into observable, typed, or timed-out failures, each proven by its test in `tests/test_silent_failures.py`.

**Architecture:** The executable contract is the ported `tests/test_silent_failures.py` (16 currently-failing assertions on `main`). Port it first, then implement one deliverable per task, making its test(s) go green without weakening any assertion.

**Tech Stack:** asyncio, FastAPI/Starlette, google-genai SDK, Playwright, lxml/cssselect, SQLAlchemy.

**Reference spec:** `docs/superpowers/specs/2026-05-30-finish-inflight-sprint-design.md` §6.

**Dependency:** Lot 1 (T2a) must land first — deliverable #5 asserts FK violations are *not* misread as the Gmail dedup error, which only happens once FK enforcement is on.

---

## File Structure

- Create: `tests/test_silent_failures.py` — ported verbatim from the `fix/T3-silent-failures` worktree.
- Modify: `backend/config.py` — add `TECTONIC_TIMEOUT_SECONDS`, `GEMINI_TIMEOUT_SECONDS`; mirror in `.env.example`.
- Modify: `backend/latex/compiler.py`, `backend/latex/injector.py`, `backend/llm/gemini_client.py`,
  `backend/applier/form_filler.py`, `backend/gmail/sync.py`, `backend/api/ws.py`,
  `backend/api/gmail_auth.py`, `backend/scraping/scrapling_fetcher.py`,
  `backend/applier/engine.py`, `backend/api/applications.py`.

---

## Task 1: Port the executable contract + add config keys

**Files:**
- Create: `tests/test_silent_failures.py`
- Modify: `backend/config.py:44`, `.env.example`

- [ ] **Step 1: Copy the test file from the reference worktree**

Run:
```bash
cp .claude/worktrees/agent-a64f24b01801aedbc/tests/test_silent_failures.py tests/test_silent_failures.py
```

- [ ] **Step 2: Add the two timeout settings**

In `backend/config.py`, after the `APPLY_TIER1_ENABLED` field (line ~44):

```python
    # Timeouts (seconds) — fail loudly instead of hanging forever.
    TECTONIC_TIMEOUT_SECONDS: float = Field(60.0, env="TECTONIC_TIMEOUT_SECONDS")
    GEMINI_TIMEOUT_SECONDS: float = Field(45.0, env="GEMINI_TIMEOUT_SECONDS")
```

Append to `.env.example` under the AI model section:

```dotenv
# Timeouts (seconds)
TECTONIC_TIMEOUT_SECONDS=60
GEMINI_TIMEOUT_SECONDS=45
```

- [ ] **Step 3: Run the probe to confirm the starting state**

Run: `uv run pytest tests/test_silent_failures.py -q`
Expected: ~16 failed, 1 passed (the 429 branch passes; everything else fails on missing symbols/behavior).

- [ ] **Step 4: Commit**

```bash
git add tests/test_silent_failures.py backend/config.py .env.example
git commit -m "test(T3): port silent-failure contract + add timeout settings"
```

---

## Task 2: Deliverable 1 — Tectonic compile timeout

**Files:** Modify `backend/latex/compiler.py:72-81`
**Test:** `tests/test_silent_failures.py::test_tectonic_timeout_raises_latex_compile_timeout`

- [ ] **Step 1: Add the exception + timeout wrapper**

In `backend/latex/compiler.py`, add the exception class next to `LaTeXCompilationError`:

```python
class LaTeXCompileTimeout(LaTeXCompilationError):
    """Raised when Tectonic exceeds settings.TECTONIC_TIMEOUT_SECONDS."""
```

Replace the `proc = await ...; stdout, stderr = await proc.communicate()` block:

```python
        from backend.config import settings

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=settings.TECTONIC_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError as exc:
            proc.kill()
            raise LaTeXCompileTimeout(
                f"Tectonic timed out after {settings.TECTONIC_TIMEOUT_SECONDS}s "
                f"compiling {tex_path.name}"
            ) from exc
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/test_silent_failures.py::test_tectonic_timeout_raises_latex_compile_timeout -q`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add backend/latex/compiler.py
git commit -m "fix(T3-1): Tectonic compile honors TECTONIC_TIMEOUT_SECONDS"
```

---

## Task 3: Deliverable 2 — Gemini call timeout via HttpOptions

**Files:** Modify `backend/llm/gemini_client.py:80`
**Test:** `tests/test_silent_failures.py::test_gemini_client_installs_timeout_http_options`

- [ ] **Step 1: Pass HttpOptions to the client**

In `GeminiClient.__init__`, replace the `genai.Client(...)` line:

```python
        self._client = genai.Client(
            api_key=settings.GOOGLE_API_KEY.get_secret_value(),
            http_options=genai_types.HttpOptions(
                timeout=int(settings.GEMINI_TIMEOUT_SECONDS * 1000)  # SDK uses ms
            ),
        )
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/test_silent_failures.py::test_gemini_client_installs_timeout_http_options -q`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add backend/llm/gemini_client.py
git commit -m "fix(T3-2): wire per-request timeout into GeminiClient via HttpOptions"
```

---

## Task 4: Deliverable 3 — Gemini non-429 raises GeminiCallFailed

**Files:** Modify `backend/llm/gemini_client.py:66-67,169-193`
**Tests:** `test_gemini_non_429_raises_gemini_call_failed`, `test_gemini_429_still_raises_rate_limit_error`

- [ ] **Step 1: Add the new exception**

Next to `GeminiRateLimitError`:

```python
class GeminiCallFailed(Exception):
    """A non-rate-limit Gemini failure (bad key, network, backend 5xx)."""
```

- [ ] **Step 2: Stop wrapping every error as a rate-limit error**

In `generate_text`, the `except Exception as e:` block currently ends with `raise GeminiRateLimitError(str(e)) from e`. Change so only 429 stays a rate-limit error:

```python
                    if "429" in msg and attempt < 2:
                        delay = _extract_retry_seconds(e)
                        if delay is None:
                            delay = 2**attempt * 5
                        else:
                            delay = min(delay + 1.0, 300.0)
                        logger.info(
                            "Rate limited (429), waiting %.1fs before retry %d/3",
                            delay, attempt + 2,
                        )
                        await asyncio.sleep(delay)
                        continue
                    if "429" in msg:
                        raise GeminiRateLimitError(str(e)) from e
                    raise GeminiCallFailed(str(e)) from e
```

And change the final fall-through `raise GeminiRateLimitError(f"All model candidates failed: {last_exc}")` to:

```python
        raise GeminiCallFailed(f"All model candidates failed: {last_exc}")
```

- [ ] **Step 3: Run both tests**

Run: `uv run pytest tests/test_silent_failures.py -k gemini -q`
Expected: PASS (3 gemini tests green, including the existing 429 one).

- [ ] **Step 4: Commit**

```bash
git add backend/llm/gemini_client.py
git commit -m "fix(T3-3): non-429 Gemini failures raise GeminiCallFailed, not rate-limit"
```

---

## Task 5: Deliverable 4 — Form-filler WARN on fill/upload failure

**Files:** Modify `backend/applier/form_filler.py:163,173,182,317,325,333`
**Test:** `test_form_filler_logs_warning_on_fill_failure`

- [ ] **Step 1: Replace the silent `logger.debug` swallow points**

The test requires these exact message prefixes at WARNING and forbids the old `logger.debug("Could not fill` / `CV upload failed` / `Letter upload failed` strings. Apply to **both** `fill_and_submit` and `fill_only`:

```python
                except Exception as exc:
                    logger.warning("Form fill failed: selector=%r: %s", sel, exc)
```
```python
                        except Exception as exc:
                            logger.warning("CV upload failed: selector=%r: %s", fi["selector"], exc)
```
```python
                        except Exception as exc:
                            logger.warning("Letter upload failed: selector=%r: %s", fi["selector"], exc)
```

> The `fill_only` upload catches currently log `CV upload failed: %s` / `Letter upload failed: %s` without a selector — update them to the `selector=%r` form too so the forbidden substrings disappear and the required `selector=` substrings are present.

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/test_silent_failures.py::test_form_filler_logs_warning_on_fill_failure -q`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add backend/applier/form_filler.py
git commit -m "fix(T3-4): form-filler logs fill/upload failures at WARNING with selector"
```

---

## Task 6: Deliverable 5 — GmailSync IntegrityError narrowing

**Files:** Modify `backend/gmail/sync.py:9,194-198`
**Tests:** `test_gmail_sync_recognises_dedup_violation`, `test_gmail_sync_does_not_swallow_fk_violation`
**Depends on:** Lot 1 (FK enforcement) being merged.

- [ ] **Step 1: Add the dedup predicate**

In `backend/gmail/sync.py` (module scope):

```python
def _is_gmail_dedup_violation(exc: IntegrityError) -> bool:
    """True only for the gmail_messages dedup UNIQUE violation.

    A FK violation (post-T2a) must NOT be classified as dedup — that would
    silently swallow a real referential-integrity bug.
    """
    text = str(getattr(exc, "orig", exc)).lower()
    return "unique constraint failed" in text and "gmail_message" in text
```

- [ ] **Step 2: Use the predicate at the insert site**

Replace the broad `except IntegrityError:` around the message insert (line ~196):

```python
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                if _is_gmail_dedup_violation(exc):
                    return False
                raise
```

- [ ] **Step 3: Run the tests**

Run: `uv run pytest tests/test_silent_failures.py -k gmail_sync -q`
Expected: PASS (2 passed).

- [ ] **Step 4: Commit**

```bash
git add backend/gmail/sync.py
git commit -m "fix(T3-5): narrow GmailSync IntegrityError handling to dedup only"
```

---

## Task 7: Deliverable 6 — WS unknown-type logging

**Files:** Modify `backend/api/ws.py:129-160` (the receive loop's message dispatch)
**Test:** `test_ws_unknown_message_type_logs_warning`

- [ ] **Step 1: Read the dispatch block**

Open `backend/api/ws.py` around line 129 (`msg = json.loads(data)`). Identify where the discriminator (`msg.get("type")`) is matched against known types (`ping`, `confirm_submit`, `cancel_apply`, …).

- [ ] **Step 2: Add an explicit else branch that WARNs**

After the known-type handlers, add:

```python
            msg_type = msg.get("type")
            if msg_type == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
            # ... existing known-type handlers ...
            else:
                logger.warning("WS received unknown message type: %r", msg_type)
```

> Keep the existing handler bodies; only add the final `else` that logs the unknown discriminator at WARNING. Ensure `ping`→`pong` still works (the test relies on it to confirm the loop processed the unknown message).

- [ ] **Step 3: Run the test**

Run: `uv run pytest tests/test_silent_failures.py::test_ws_unknown_message_type_logs_warning -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/api/ws.py
git commit -m "fix(T3-6): WS logs unknown message types at WARNING"
```

---

## Task 8: Deliverable 7 — OAuth callback bad-state redirect

**Files:** Modify `backend/api/gmail_auth.py:89-90`
**Test:** `test_oauth_callback_bad_state_redirects_with_gmail_error`

- [ ] **Step 1: Replace the bare 400 with a redirect**

In `oauth_callback`, change the invalid-state branch:

```python
    if not _verify_state(state):
        logger.warning("Gmail OAuth callback: invalid or expired state")
        return RedirectResponse("/settings?gmail_error=invalid_state", status_code=302)
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/test_silent_failures.py::test_oauth_callback_bad_state_redirects_with_gmail_error -q`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add backend/api/gmail_auth.py
git commit -m "fix(T3-7): OAuth callback redirects to /settings on invalid state"
```

---

## Task 9: Deliverable 8 — `_clean_html` selector-miss alarm

**Files:** Modify `backend/scraping/scrapling_fetcher.py:__init__, 302-346`
**Tests:** `test_scrapling_fetcher_warns_on_selector_miss`, `test_scrapling_fetcher_resets_counter_after_match`

- [ ] **Step 1: Add the miss counter to `__init__`**

In `ScraplingFetcher.__init__`, add:

```python
        self._selector_miss_counts: dict[str, int] = {}
```

- [ ] **Step 2: Track miss/match in `_clean_html`**

In the `if content_selector:` block, after the `for sel in ...` loop resolves `matched_sel`:

```python
            if matched_sel:
                logger.info("[Tier 1] scoped to selector %r — site=%s", matched_sel, site)
                self._selector_miss_counts[site] = 0
            else:
                self._selector_miss_counts[site] = self._selector_miss_counts.get(site, 0) + 1
                logger.warning(
                    "[Tier 1] content selector matched no nodes for site=%s "
                    "(miss #%d) — using full page",
                    site, self._selector_miss_counts[site],
                )
```

> The test calls `_clean_html(...)` for `google_jobs` with HTML that contains none of the configured selector nodes and expects `_selector_miss_counts["google_jobs"] == 1` plus a WARNING naming the site; the reset test pre-seeds the counter to 3 and expects it back to 0 after a matching call.

- [ ] **Step 3: Run the tests**

Run: `uv run pytest tests/test_silent_failures.py -k scrapling -q`
Expected: PASS (2 passed).

- [ ] **Step 4: Commit**

```bash
git add backend/scraping/scrapling_fetcher.py
git commit -m "fix(T3-8): warn + count selector misses in ScraplingFetcher._clean_html"
```

---

## Task 10: Deliverable 9 — LaTeX escape on `{company_name}`

**Files:** Modify `backend/latex/injector.py:1-36`
**Tests:** `test_inject_letter_edit_escapes_hostile_company_name`, `test_escape_latex_round_trip`

- [ ] **Step 1: Add `_escape_latex` and use it**

In `backend/latex/injector.py` (module scope):

```python
_LATEX_SPECIALS = [
    ("\\", r"\textbackslash{}"),  # must run first
    ("&", r"\&"),
    ("%", r"\%"),
    ("$", r"\$"),
    ("#", r"\#"),
    ("_", r"\_"),
    ("{", r"\{"),
    ("}", r"\}"),
]


def _escape_latex(value: str) -> str:
    """Escape LaTeX-special characters so substituted text cannot inject commands."""
    out = value
    for char, replacement in _LATEX_SPECIALS:
        out = out.replace(char, replacement)
    return out
```

In `inject_letter_edit`, escape the company name before substitution:

```python
    def inject_letter_edit(self, original_tex: str, new_paragraph: str, company_name: str) -> str:
        tex = self._replace_marker_content(original_tex, "LETTER:PARA", new_paragraph)
        tex = tex.replace("{company_name}", _escape_latex(company_name))
        return tex
```

> The round-trip test pins each replacement, including `_escape_latex("a\\b") == r"a\textbackslash{}b"` — backslash MUST be the first replacement so later replacements do not double-escape the backslashes it introduces.

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/test_silent_failures.py -k escape -q`
Expected: PASS (2 passed).

- [ ] **Step 3: Commit**

```bash
git add backend/latex/injector.py
git commit -m "fix(T3-9): escape {company_name} substitutions against LaTeX injection"
```

---

## Task 11: Deliverable 10 — `apply_review` survives no-client (engine cache + endpoint)

**Files:** Modify `backend/applier/engine.py:70-94`, `backend/api/applications.py`
**Tests:** `test_engine_records_and_returns_pending_review`, `test_engine_clears_pending_review_on_signal_confirm`, `test_review_state_endpoint_returns_cached_payload`, `test_review_state_endpoint_404_when_no_pending`

- [ ] **Step 1: Add the pending-review cache to the engine**

In `ApplicationEngine.__init__`, add alongside the event dicts:

```python
        self._pending_reviews: dict[int, dict] = {}
```

Add the three methods (the engine already has `signal_confirm`; extend it to consume the snapshot):

```python
    def record_pending_review(
        self, job_id: int, *, filled_fields: dict, screenshot_b64: Optional[str]
    ) -> None:
        """Cache the review snapshot so a reconnecting client can fetch it."""
        self._pending_reviews[job_id] = {
            "job_id": job_id,
            "filled_fields": filled_fields,
            "screenshot_b64": screenshot_b64,
        }

    def get_pending_review(self, job_id: int) -> Optional[dict]:
        """Return the cached snapshot for *job_id*, or None."""
        return self._pending_reviews.get(job_id)
```

Extend `signal_confirm` to drop the snapshot when confirmed:

```python
    def signal_confirm(self, job_id: int) -> None:
        """Trigger confirmation for *job_id* (``confirm_submit`` WS message)."""
        if job_id in self._confirm_events:
            self._confirm_events[job_id].set()
        self._pending_reviews.pop(job_id, None)
```

- [ ] **Step 2: Run the engine-level tests**

Run: `uv run pytest tests/test_silent_failures.py -k "pending_review or signal_confirm" -q`
Expected: `test_engine_records_and_returns_pending_review` + `test_engine_clears_pending_review_on_signal_confirm` PASS.

- [ ] **Step 3: Add the review-state endpoint**

In `backend/api/applications.py`, add an endpoint that reads the engine off `request.app.state.apply_engine` (match the file's existing router variable and import style):

```python
from fastapi import Request


@router.get("/applications/{job_id}/review-state")
async def get_review_state(job_id: int, request: Request) -> dict:
    engine = getattr(request.app.state, "apply_engine", None)
    if engine is None:
        raise HTTPException(status_code=404, detail="No apply engine")
    payload = engine.get_pending_review(job_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="No pending review")
    return payload
```

> Use the router prefix already defined in `applications.py` so the final path is `/api/applications/{job_id}/review-state`. The tests use `test_app.get("/api/applications/99/review-state")`.

- [ ] **Step 4: Run the endpoint tests**

Run: `uv run pytest tests/test_silent_failures.py -k review_state -q`
Expected: PASS (both, including the 404 case).

- [ ] **Step 5: Run the full T3 file + suite + pyright**

Run: `uv run pytest tests/test_silent_failures.py -q && uv run pytest -q && uv run pyright backend/`
Expected: `test_silent_failures.py` → 17 passed; full suite green; pyright at baseline.

- [ ] **Step 6: Commit**

```bash
git add backend/applier/engine.py backend/api/applications.py
git commit -m "fix(T3-10): engine caches pending review + GET review-state endpoint"
```

---

## Self-Review notes (carried from plan author)

- Do not weaken any assertion in `tests/test_silent_failures.py`; the 16→0 failing count is the acceptance bar.
- Task 6 (deliverable #5) only passes meaningfully after Lot 1 turns FK on; keep this lot ordered after Lot 1.
- Task 7 must inspect the real dispatch block before editing — the snippet shows the shape, not the full set of existing handlers; preserve them.
- If `applications.py` already imports `Request`/`HTTPException`, do not duplicate the imports.
