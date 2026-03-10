# JobPilot — Troubleshooting Guide

---

## Installation Issues

### `start.py exits with "Tectonic not found"`
**Symptom:** Running `uv run python start.py` exits immediately with an error about Tectonic.

**Fix:**
```bash
uv run python scripts/download_tectonic.py
```
This downloads the correct Tectonic binary for your platform to `bin/tectonic`.

If you want to run without Tectonic (e.g., for API testing only):
```bash
uv run uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

---

### `uv sync` fails with Python version error
**Symptom:** `uv sync` says Python 3.12+ is required but you have an older version.

**Fix:** Install Python 3.12+ from [python.org](https://www.python.org/downloads/) and ensure it's on your PATH.

On Linux: `sudo apt install python3.12` (Ubuntu 22.04+) or use [pyenv](https://github.com/pyenv/pyenv).

---

### Frontend build fails with Node.js error
**Symptom:** `npm run build` fails or `npm install` fails.

**Fix:** Ensure Node.js ≥ 18:
```bash
node --version   # should be 18.x or higher
```
Install from [nodejs.org](https://nodejs.org/) or use [nvm](https://github.com/nvm-sh/nvm).

---

### `playwright install chromium` fails
**Symptom:** Playwright cannot download Chromium.

**Fix:**
```bash
# Install system dependencies first (Linux)
uv run playwright install-deps chromium
uv run playwright install chromium
```

On Windows: Playwright usually works out of the box. If it fails, try running PowerShell as Administrator.

---

## Configuration Issues

### `GET /api/health` shows `gemini_key_set: false`
**Symptom:** Health endpoint shows Gemini key not configured.

**Fix:** Edit `.env` and add your Gemini API key:
```dotenv
GOOGLE_API_KEY=your_actual_api_key
```
Restart the server — settings are loaded at startup.

Get a free key at https://aistudio.google.com/

---

### `GET /api/settings/profile` returns 404
**Symptom:** Profile endpoint returns 404.

**Explanation:** This is expected on a fresh install. The profile is created when you complete the Setup Wizard or call `PUT /api/settings/profile`.

**Fix:** Complete the Setup Wizard at http://localhost:8000, or:
```bash
curl -X PUT http://localhost:8000/api/settings/profile \
  -H "Content-Type: application/json" \
  -d '{"full_name": "Your Name", "email": "you@example.com"}'
```

---

### Environment variables not being picked up
**Symptom:** The app doesn't see your API keys even though `.env` is populated.

**Fix:** Ensure `.env` is at the **project root** (same directory as `pyproject.toml`), not in a subdirectory.

Check:
```bash
ls -la .env
uv run python -c "from backend.config import settings; print(settings.GOOGLE_API_KEY[:4])"
```

---

## Scraping Issues

### Adzuna search returns empty results
**Symptom:** `POST /api/jobs/search` returns `{"stored": 0, "jobs": []}`.

**Possible causes and fixes:**

1. **Invalid API keys:** Check `/api/settings/sources` — `adzuna.configured` should be `true`.
2. **Too-specific keywords:** Try broader terms. Adzuna UK search requires English keywords.
3. **Wrong country code:** Default is `"gb"` (UK). For US: `"us"`, for Germany: `"de"`.
4. **Rate limit:** Adzuna free tier allows 250 requests/day. Check if you've exceeded it.

---

### browser-use scraping fails or times out
**Symptom:** AdaptiveScraper fails with a timeout or browser error.

**Possible causes and fixes:**

1. **Chromium not installed:** `uv run playwright install chromium`
2. **Site requires login:** Some sites (LinkedIn) require a logged-in session. Use the **Assisted Apply** flow which prompts you to log in.
3. **Site changed its layout:** The AI-based scraper adapts, but very unusual layouts can confuse it. Check `data/logs/` for details.
4. **Gemini rate limit during scraping:** The 15 RPM limit applies. If many sites are scraped simultaneously, some may fail. The batch auto-retries.

---

## LaTeX / CV Issues

### CV tailoring fails with `latex_compile_error`
**Symptom:** `/api/documents/tailor` returns HTTP 422 with `"code": "latex_compile_error"`.

**Possible causes and fixes:**

1. **Tectonic not installed:** Run `uv run python scripts/download_tectonic.py`
2. **Invalid base CV:** Your `.tex` file has syntax errors. Test it manually:
   ```bash
   ./bin/tectonic your_cv.tex
   ```
3. **Missing section markers:** Gemini's diff targets markers like `%==EXPERIENCE==`. Without them, the injector has nothing to patch. Add markers to your `.tex` file (see [Operations Guide](operations.md#step-2-upload-your-base-cv)).
4. **Encoding issue:** Ensure your `.tex` file is UTF-8 encoded.

---

### Gemini returns `gemini_json_error`
**Symptom:** CV tailoring fails with `"code": "gemini_json_error"`.

**Cause:** Gemini returned a response that doesn't match the expected JSON schema for CV diffs.

**Fix:** This is usually transient. Retry the tailoring. If it persists:
- Check that your CV sections have content (empty sections confuse the model)
- The job description should be at least 100 characters

---

### `rate_limit` error during tailoring
**Symptom:** HTTP 429 with `"code": "rate_limit"`.

**Cause:** Gemini free tier allows 15 requests/minute. Morning batch or multiple concurrent tailoring requests can hit this.

**Fix:** Wait 60 seconds and retry. The rate limiter will self-recover.

---

## Application Issues

### Application stuck waiting for confirmation
**Symptom:** The confirm dialog appeared but clicking Confirm doesn't work.

**Fix:**
1. Ensure the WebSocket is connected — check the status bar at the bottom of the screen (should show a green dot).
2. If disconnected, refresh the page. The WebSocket auto-reconnects.
3. If the browser-use session has timed out, cancel the application:
   ```bash
   # Via WebSocket
   wscat -c ws://localhost:8000/ws
   > {"type": "cancel_apply", "job_id": 1}
   ```

---

### Daily limit reached
**Symptom:** Clicking Apply shows "Daily limit reached".

**Cause:** You've hit `daily_limit` (default: 10) applications today.

**Fix:** Wait until tomorrow, or increase the limit in Settings:
- Dashboard: Settings → Search Settings → Daily Apply Limit
- API: `PUT /api/settings/search {"daily_limit": 20}`

---

### Browser session expired for a site
**Symptom:** Assisted apply opens the site but shows a login page.

**Fix:** Complete the login in the browser window that opens. The session manager will save the cookies for next time. Click the **Login Done** button in the dashboard when finished.

---

## Database Issues

### Database migration needed
**Symptom:** The server starts but immediately crashes with a SQLAlchemy error about missing columns or tables.

**Fix:**
```bash
uv run alembic upgrade head
```

---

### `data/jobpilot.db` is locked
**Symptom:** Server reports `database is locked`.

**Cause:** Multiple server instances running, or the previous instance didn't shut down cleanly.

**Fix:**
```bash
# Find and kill any other server instances
pkill -f "uvicorn backend.main:app"
# Restart
uv run python start.py
```

---

## Frontend Issues

### Dashboard shows "Cannot connect to server"
**Symptom:** The frontend loads but shows a connection error.

**Fix:**
1. Ensure the backend is running: `uv run python start.py`
2. Check the port: backend defaults to 8000. Frontend in dev mode (port 5173) uses CORS to reach the backend at 8000.
3. In production mode (backend serves frontend at port 8000), there's no CORS issue.

---

### Frontend dev server shows type errors
**Symptom:** `npm run dev` shows TypeScript errors.

**Fix:**
```bash
cd frontend && npm run check
```
These are usually import path issues or stale `.svelte-kit/` cache:
```bash
cd frontend && rm -rf .svelte-kit && npm run dev
```

---

## Viewing Logs

Server logs go to stdout. For persistent logs:
```bash
uv run python start.py 2>&1 | tee data/logs/server.log
```

Set `JOBPILOT_LOG_LEVEL=debug` in `.env` for verbose output.

---

## Getting Help

1. Check `data/logs/` for detailed error output
2. Run the test suite to check for regressions: `uv run pytest tests/ -q`
3. Check `/api/health` for dependency status
4. File an issue with the error message and `uv run pytest tests/test_smoke.py -v` output
