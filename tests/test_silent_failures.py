"""T3 — Silent-failure elimination regression tests.

One test per deliverable in `.claude/plans/jolly-squishing-babbage.md`
section "T3 — Silent-failure elimination". Each test should fail on
``main`` before the T3 fix and pass after.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Deliverable 1 — Tectonic timeout
# ---------------------------------------------------------------------------


class _FakeProc:
    """An async process stand-in whose ``communicate()`` never returns.

    Mimics the ``asyncio`` Process API used by ``LaTeXCompiler``.
    """

    def __init__(self) -> None:
        self.returncode: int | None = None
        self.killed = False

    async def communicate(self):
        # Wait forever — the compiler's wait_for() must time out and kill us.
        await asyncio.Event().wait()
        return (b"", b"")

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode if self.returncode is not None else 0


@pytest.mark.asyncio
async def test_tectonic_timeout_raises_latex_compile_timeout(tmp_path, monkeypatch):
    """A hung tectonic call must raise ``LaTeXCompileTimeout`` after the
    configured budget, not block the worker forever."""
    from backend.config import settings
    from backend.latex.compiler import LaTeXCompileTimeout, LaTeXCompiler

    # Speed up the test — 0.1 s budget instead of 60.
    monkeypatch.setattr(settings, "TECTONIC_TIMEOUT_SECONDS", 0.1)

    compiler = LaTeXCompiler()
    # Pretend tectonic is on the PATH so we get past the existence check.
    monkeypatch.setattr(compiler, "_find_tectonic", lambda: "/usr/bin/false")

    fake = _FakeProc()

    async def fake_create(*args, **kwargs):
        return fake

    monkeypatch.setattr(
        "backend.latex.compiler.asyncio.create_subprocess_exec",
        fake_create,
    )

    tex_path = tmp_path / "cv.tex"
    tex_path.write_text("\\documentclass{article}\\begin{document}hi\\end{document}")

    with pytest.raises(LaTeXCompileTimeout):
        await compiler.compile(tex_path, output_dir=tmp_path)
    assert fake.killed, "compiler must kill the hung process on timeout"


# ---------------------------------------------------------------------------
# Deliverable 2 — Gemini call timeout (HttpOptions wired through)
# ---------------------------------------------------------------------------


def test_gemini_client_installs_timeout_http_options(monkeypatch):
    """GeminiClient must hand a per-request timeout to the SDK transport."""
    from pydantic import SecretStr

    monkeypatch.setattr("backend.config.settings.GOOGLE_API_KEY", SecretStr("fake-key"))
    monkeypatch.setattr("backend.config.settings.GOOGLE_MODEL", "gemini-3.0-flash")
    monkeypatch.setattr("backend.config.settings.GOOGLE_MODEL_FALLBACKS", "")
    monkeypatch.setattr("backend.config.settings.GEMINI_TIMEOUT_SECONDS", 45.0)

    captured: dict = {}

    def fake_client(api_key=None, http_options=None, **kw):  # noqa: ARG001
        captured["http_options"] = http_options
        return MagicMock()

    monkeypatch.setattr("backend.llm.gemini_client.genai.Client", fake_client)

    from backend.llm.gemini_client import GeminiClient

    GeminiClient()
    opts = captured["http_options"]
    assert opts is not None, "GeminiClient must pass HttpOptions to genai.Client"
    # SDK uses milliseconds.
    assert int(opts.timeout) == 45_000


# ---------------------------------------------------------------------------
# Deliverable 3 — Gemini error wrapping: non-429 surfaces as GeminiCallFailed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gemini_non_429_raises_gemini_call_failed(monkeypatch):
    """A non-429 failure (invalid API key, network, backend 500) must NOT be
    wrapped as a rate-limit error — that hid broken keys for ages."""
    from backend.llm.gemini_client import (
        GeminiCallFailed,
        GeminiClient,
        GeminiRateLimitError,
    )

    client = GeminiClient.__new__(GeminiClient)
    client._call_times = deque(maxlen=GeminiClient.RPM_LIMIT)
    client._lock = asyncio.Lock()
    client._candidates = ["model-a"]
    client._candidate_idx = 0
    client._model_name = "model-a"

    def boom(*a, **kw):
        raise RuntimeError("403 PERMISSION_DENIED: API key invalid.")

    client._client = MagicMock()
    client._client.models.generate_content = boom

    async def _noop():
        return None

    monkeypatch.setattr(client, "_wait_for_rate_limit", _noop)

    with pytest.raises(GeminiCallFailed) as excinfo:
        await client.generate_text("ping")
    assert not isinstance(excinfo.value, GeminiRateLimitError)


@pytest.mark.asyncio
async def test_gemini_429_still_raises_rate_limit_error(monkeypatch):
    """The 429 branch must still raise the rate-limit class — narrow fix,
    don't break the legitimate rate-limit path."""
    from backend.llm.gemini_client import GeminiClient, GeminiRateLimitError

    client = GeminiClient.__new__(GeminiClient)
    client._call_times = deque(maxlen=GeminiClient.RPM_LIMIT)
    client._lock = asyncio.Lock()
    client._candidates = ["model-a"]
    client._candidate_idx = 0
    client._model_name = "model-a"

    def boom(*a, **kw):
        raise RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")

    client._client = MagicMock()
    client._client.models.generate_content = boom

    async def _noop(*a, **kw):
        return None

    monkeypatch.setattr(client, "_wait_for_rate_limit", _noop)
    monkeypatch.setattr("backend.llm.gemini_client.asyncio.sleep", _noop)

    with pytest.raises(GeminiRateLimitError):
        await client.generate_text("ping")


# ---------------------------------------------------------------------------
# Deliverable 4 — Form-filler logs WARN on fill failure (not DEBUG)
# ---------------------------------------------------------------------------


def test_form_filler_logs_warning_on_fill_failure():
    """The fill-exception catch in PlaywrightFormFiller must surface at
    WARNING with selector/field name in the message — never DEBUG."""
    import inspect

    from backend.applier import form_filler

    src = inspect.getsource(form_filler)
    # Previous bug: silent .debug for each of the three swallow points.
    assert 'logger.debug("Could not fill' not in src
    assert 'logger.debug("CV upload failed' not in src
    assert 'logger.debug("Letter upload failed' not in src
    # New shape must be present (one of the WARNING messages).
    assert "Form fill failed: selector=" in src
    assert "CV upload failed: selector=" in src
    assert "Letter upload failed: selector=" in src


# ---------------------------------------------------------------------------
# Deliverable 5 — GmailSync IntegrityError narrowing
# ---------------------------------------------------------------------------


def test_gmail_sync_recognises_dedup_violation():
    """The dedup predicate must accept SQLite's UNIQUE error text."""
    from sqlalchemy.exc import IntegrityError

    from backend.gmail.sync import _is_gmail_dedup_violation

    exc = IntegrityError(
        statement="INSERT ...",
        params={},
        orig=Exception("UNIQUE constraint failed: gmail_messages.gmail_message_id"),
    )
    assert _is_gmail_dedup_violation(exc) is True


def test_gmail_sync_does_not_swallow_fk_violation():
    """A FK violation (post-T2a) must NOT be classified as the dedup error."""
    from sqlalchemy.exc import IntegrityError

    from backend.gmail.sync import _is_gmail_dedup_violation

    exc = IntegrityError(
        statement="INSERT ...",
        params={},
        orig=Exception("FOREIGN KEY constraint failed"),
    )
    assert _is_gmail_dedup_violation(exc) is False


# ---------------------------------------------------------------------------
# Deliverable 6 — WS unknown-type logging
# ---------------------------------------------------------------------------


def test_ws_unknown_message_type_logs_warning(test_app, caplog):
    """Sending an unknown discriminator must emit a WARN log instead of
    silently dropping the message."""
    caplog.set_level(logging.WARNING, logger="backend.api.ws")
    with test_app.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "definitely_not_real_event"}))
        # Round-trip a ping so we know the receive loop has processed our msg.
        ws.send_text(json.dumps({"type": "ping"}))
        pong = ws.receive_text()
        assert json.loads(pong).get("type") == "pong"
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("definitely_not_real_event" in r.getMessage() for r in warns), (
        "WS unknown-type warning was not logged"
    )


# ---------------------------------------------------------------------------
# Deliverable 7 — OAuth callback error redirect
# ---------------------------------------------------------------------------


def test_oauth_callback_bad_state_redirects_with_gmail_error(monkeypatch):
    """Bad state must redirect to /settings?gmail_error=invalid_state, not
    return a bare 400 JSON the SPA cannot handle."""
    from starlette.testclient import TestClient

    monkeypatch.setenv("GMAIL_CLIENT_ID", "test-client.apps.googleusercontent.com")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "test-secret")
    import backend.config as cfg

    cfg.settings = cfg._load_settings()
    from backend.main import app

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get(
            "/api/gmail/oauth/callback?code=x&state=forged",
            follow_redirects=False,
        )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/settings?gmail_error=invalid_state"


# ---------------------------------------------------------------------------
# Deliverable 8 — _clean_html selector-failure alarm
# ---------------------------------------------------------------------------


def test_scrapling_fetcher_warns_on_selector_miss(caplog):
    """ScraplingFetcher._clean_html must WARN (not silently fall back) the
    first time a configured selector returns no nodes for a site."""
    from backend.scraping.scrapling_fetcher import ScraplingFetcher

    fetcher = ScraplingFetcher(gemini_client=MagicMock())

    # google_jobs has a content selector configured but our HTML contains
    # none of those nodes — so the selector pass will miss.
    caplog.set_level(logging.WARNING, logger="backend.scraping.scrapling_fetcher")
    html = "<html><body><div>no jobs here</div></body></html>"
    out = fetcher._clean_html(html, site="google_jobs")
    assert out, "fallback should still produce some text"
    assert fetcher._selector_miss_counts.get("google_jobs", 0) == 1
    warn_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("selector" in m and "google_jobs" in m for m in warn_msgs), (
        f"expected selector-miss WARN, got: {warn_msgs!r}"
    )


def test_scrapling_fetcher_resets_counter_after_match():
    """If the selector matches on a later call, the miss counter resets."""
    from backend.scraping.scrapling_fetcher import ScraplingFetcher
    from backend.scraping.site_prompts import SITE_CONTENT_SELECTORS

    fetcher = ScraplingFetcher(gemini_client=MagicMock())
    fetcher._selector_miss_counts["google_jobs"] = 3

    sel = SITE_CONTENT_SELECTORS.get("google_jobs", "").split(",")[0].strip()
    if not sel:
        pytest.skip("google_jobs has no content selector configured")
    # Build HTML that includes the first selector.
    if sel.startswith("."):
        cls = sel[1:]
        html = f"<html><body><div class='{cls}'>job content</div></body></html>"
    elif sel.startswith("#"):
        ident = sel[1:]
        html = f"<html><body><div id='{ident}'>job content</div></body></html>"
    else:
        html = f"<html><body><{sel}>job content</{sel}></body></html>"

    fetcher._clean_html(html, site="google_jobs")
    assert fetcher._selector_miss_counts.get("google_jobs", 0) == 0


# ---------------------------------------------------------------------------
# Deliverable 9 — LaTeX escape audit on {company_name}
# ---------------------------------------------------------------------------


def test_inject_letter_edit_escapes_hostile_company_name():
    """A company name with LaTeX-special chars (including ``\\input{...}``)
    must be escaped so the substitution cannot inject live commands."""
    from backend.latex.injector import LaTeXInjector, _escape_latex

    tex = (
        "Some preface.\n"
        "% --- JOBPILOT:LETTER:PARA:START ---\n"
        "OLD\n"
        "% --- JOBPILOT:LETTER:PARA:END ---\n"
        "Dear hiring team at {company_name},\n"
    )
    hostile = r"Acme \\ \input{evil}"
    injector = LaTeXInjector()
    out = injector.inject_letter_edit(tex, new_paragraph="NEW", company_name=hostile)

    # The raw \input must NOT appear unescaped in the output.
    assert "\\input{evil}" not in out, (
        f"hostile LaTeX command leaked through: {out!r}"
    )
    # And the escaped form should be present.
    assert _escape_latex(hostile) in out


def test_escape_latex_round_trip():
    """Spot-check every special char gets its replacement."""
    from backend.latex.injector import _escape_latex

    assert _escape_latex("A & B") == r"A \& B"
    assert _escape_latex("50%") == r"50\%"
    assert _escape_latex("$cash") == r"\$cash"
    assert _escape_latex("a_b") == r"a\_b"
    assert _escape_latex("#ref") == r"\#ref"
    assert _escape_latex("{x}") == r"\{x\}"
    # Backslash MUST be replaced before the others so we don't double-escape.
    assert _escape_latex("a\\b") == r"a\textbackslash{}b"


# ---------------------------------------------------------------------------
# Deliverable 10 — apply_review survives no-client (engine cache + API)
# ---------------------------------------------------------------------------


def test_engine_records_and_returns_pending_review():
    """The engine must accept a record_pending_review call and return the
    same payload from get_pending_review."""
    from backend.applier.engine import ApplicationEngine

    engine = ApplicationEngine(api_key="x", model="gemini-3.0-flash")
    engine.record_pending_review(
        42, filled_fields={"#name": "Alice"}, screenshot_b64="abc"
    )
    out = engine.get_pending_review(42)
    assert out is not None
    assert out["job_id"] == 42
    assert out["filled_fields"] == {"#name": "Alice"}
    assert out["screenshot_b64"] == "abc"


def test_engine_clears_pending_review_on_signal_confirm():
    """A confirm signal consumes the snapshot — second GET should 404."""
    from backend.applier.engine import ApplicationEngine

    engine = ApplicationEngine(api_key="x", model="gemini-3.0-flash")
    engine.record_pending_review(7, filled_fields={}, screenshot_b64=None)
    # signal_confirm sets the confirm_event when present; register it so the
    # method finds something to set.
    engine._confirm_events[7] = asyncio.Event()
    engine.signal_confirm(7)
    assert engine.get_pending_review(7) is None


def test_review_state_endpoint_returns_cached_payload(test_app):
    """GET /api/applications/{id}/review-state returns the cached snapshot."""
    from backend.main import app

    engine = getattr(app.state, "apply_engine", None)
    if engine is None:
        pytest.skip("apply_engine not initialised in test app")

    engine.record_pending_review(
        99, filled_fields={"#email": "a@b.com"}, screenshot_b64="png-bytes"
    )
    try:
        resp = test_app.get("/api/applications/99/review-state")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["job_id"] == 99
        assert body["filled_fields"] == {"#email": "a@b.com"}
        assert body["screenshot_b64"] == "png-bytes"
    finally:
        engine._pending_reviews.pop(99, None)


def test_review_state_endpoint_404_when_no_pending(test_app):
    from backend.main import app

    engine = getattr(app.state, "apply_engine", None)
    if engine is None:
        pytest.skip("apply_engine not initialised in test app")
    engine._pending_reviews.pop(123456, None)
    resp = test_app.get("/api/applications/123456/review-state")
    assert resp.status_code == 404
