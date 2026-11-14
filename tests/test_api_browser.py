"""/api/browser: the dedicated Chrome is started on request and never on a status read."""

from __future__ import annotations

from starlette.testclient import TestClient

from backend.api import browser


def test_status_never_starts_chrome(test_app: TestClient, monkeypatch):
    monkeypatch.setattr(browser, "running_endpoint", lambda: None)
    monkeypatch.setattr(browser, "ensure_chrome_running", lambda *_: 1 / 0)
    assert test_app.get("/api/browser/status").json() == {"running": False, "logged_in": {}}


def test_open_starts_chrome_and_shows_missing_logins(test_app: TestClient, monkeypatch):
    opened: list[str] = []

    async def logged_in(port: int) -> dict[str, bool]:
        return {"linkedin": False}

    monkeypatch.setattr(browser, "ensure_chrome_running", lambda data_dir: {"port": 9})
    monkeypatch.setattr(browser, "_logged_in", logged_in)
    monkeypatch.setattr(browser, "_open_tab", lambda port, url: opened.append(url))
    resp = test_app.post("/api/browser/open")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"running": True, "logged_in": {"linkedin": False}}
    assert opened == [browser.LOGIN_PAGES["linkedin"]]

    async def all_in(port: int) -> dict[str, bool]:
        return {"linkedin": True}

    monkeypatch.setattr(browser, "_logged_in", all_in)
    test_app.post("/api/browser/open")
    assert len(opened) == 1  # nothing reopened once logged in


def test_show_app_opens_this_apps_page(test_app: TestClient, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(browser, "show_app", lambda url: seen.append(url) or True)
    assert test_app.post("/api/browser/show-app").status_code == 200
    assert seen == ["http://testserver"]
    monkeypatch.setattr(browser, "show_app", lambda url: False)
    assert test_app.post("/api/browser/show-app").status_code == 503
