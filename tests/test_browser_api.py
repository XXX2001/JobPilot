"""Google and LinkedIn sessions in the owner's JobPilot Chrome."""

from unittest.mock import AsyncMock, MagicMock, call

import pytest

from backend.api import browser
from backend.config import settings


@pytest.fixture(autouse=True)
def native_browser_mode(monkeypatch):
    monkeypatch.setattr(settings, "JOBPILOT_CHROME_VIEW_URL", "")


@pytest.fixture
def chrome_session(monkeypatch):
    context = MagicMock(cookies=AsyncMock(return_value=[]))
    chromium = MagicMock(
        connect_over_cdp=AsyncMock(return_value=MagicMock(contexts=[context]))
    )
    playwright = MagicMock()
    playwright.__aenter__ = AsyncMock(return_value=MagicMock(chromium=chromium))
    playwright.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr("playwright.async_api.async_playwright", lambda: playwright)
    monkeypatch.setattr(browser, "running_endpoint", lambda: {"port": 4242})
    return context


def test_status_reports_google_login(test_app, chrome_session):
    async def cookies(url):
        if url == "https://mail.google.com":
            return [{"name": "SID", "value": "test-session"}]
        return []

    chrome_session.cookies.side_effect = cookies
    response = test_app.get("/api/browser/status")

    assert response.status_code == 200
    assert response.json() == {
        "running": True,
        "logged_in": {"linkedin": False, "google": True},
    }
    chrome_session.cookies.assert_has_awaits([
        call("https://www.linkedin.com"), call("https://mail.google.com")
    ])


@pytest.mark.parametrize("linkedin, google", [(False, False), (True, False), (True, True)])
def test_status_reports_each_session_independently(test_app, chrome_session, linkedin, google):
    async def cookies(url):
        if url == "https://mail.google.com" and google:
            return [{"name": "SID", "value": "test-session"}]
        if url == "https://www.linkedin.com" and linkedin:
            return [{"name": "li_at", "value": "test-session"}]
        return [{"name": "unrelated", "value": "test-session"}]

    chrome_session.cookies.side_effect = cookies
    assert test_app.get("/api/browser/status").json() == {
        "running": True,
        "logged_in": {"linkedin": linkedin, "google": google},
    }


@pytest.mark.parametrize(
    "linkedin, google", [(False, False), (True, False), (False, True), (True, True)]
)
def test_open_only_opens_missing_sign_ins(test_app, monkeypatch, linkedin, google):
    logged_in = {"linkedin": linkedin, "google": google}
    monkeypatch.setattr(browser, "ensure_chrome_running", lambda _: {"port": 4242})
    monkeypatch.setattr(browser, "_logged_in", AsyncMock(return_value=logged_in))
    open_tab = MagicMock()
    monkeypatch.setattr(browser, "_open_tab", open_tab)

    response = test_app.post("/api/browser/open")

    assert response.status_code == 200
    assert response.json() == {"running": True, "logged_in": logged_in}
    expected = []
    if not linkedin:
        expected.append(call(4242, "https://www.linkedin.com/login"))
    if not google:
        expected.append(call(4242, "https://accounts.google.com/ServiceLogin?continue=https://mail.google.com/mail/"))
    assert open_tab.call_args_list == expected


def test_status_does_not_start_chrome(test_app, monkeypatch):
    monkeypatch.setattr(browser, "running_endpoint", lambda: None)
    start = MagicMock()
    monkeypatch.setattr(browser, "ensure_chrome_running", start)

    assert test_app.get("/api/browser/status").json() == {"running": False, "logged_in": {}}
    start.assert_not_called()


VIEW_URL = "http://127.0.0.1:6080/vnc.html?autoconnect=true"


@pytest.mark.parametrize("running", [False, True])
def test_docker_status_includes_view_and_password_location(
    test_app, monkeypatch, chrome_session, running
):
    monkeypatch.setattr(settings, "JOBPILOT_CHROME_VIEW_URL", VIEW_URL)
    monkeypatch.setattr(browser, "running_endpoint", lambda: {"port": 9222} if running else None)
    start = MagicMock()
    monkeypatch.setattr(browser, "ensure_chrome_running", start)

    response = test_app.get("/api/browser/status")

    assert response.status_code == 200
    assert response.json() == {
        "running": running,
        "logged_in": {"linkedin": False, "google": False} if running else {},
        "view_url": VIEW_URL,
        "view_password_hint": "data/chrome-view.password",
    }
    start.assert_not_called()


@pytest.mark.parametrize("google", [False, True])
def test_docker_open_uses_supervised_chrome_and_opens_only_missing_logins(
    test_app, monkeypatch, google
):
    monkeypatch.setattr(settings, "JOBPILOT_CHROME_VIEW_URL", VIEW_URL)
    monkeypatch.setattr(browser, "running_endpoint", lambda: {"port": 9222})
    start = MagicMock()
    monkeypatch.setattr(browser, "ensure_chrome_running", start)
    monkeypatch.setattr(
        browser, "_logged_in", AsyncMock(return_value={"linkedin": True, "google": google})
    )
    open_tab = MagicMock()
    monkeypatch.setattr(browser, "_open_tab", open_tab)

    response = test_app.post("/api/browser/open")

    assert response.status_code == 200
    assert response.json() == {
        "running": True,
        "logged_in": {"linkedin": True, "google": google},
        "view_url": VIEW_URL,
        "view_password_hint": "data/chrome-view.password",
    }
    assert open_tab.call_args_list == (
        [] if google else [call(9222, browser.LOGIN_PAGES["google"])]
    )
    start.assert_not_called()


def test_docker_open_fails_if_supervised_chrome_is_down(test_app, monkeypatch):
    monkeypatch.setattr(settings, "JOBPILOT_CHROME_VIEW_URL", VIEW_URL)
    monkeypatch.setattr(browser, "running_endpoint", lambda: None)
    start = MagicMock()
    monkeypatch.setattr(browser, "ensure_chrome_running", start)

    response = test_app.post("/api/browser/open")

    assert response.status_code == 503
    assert "docker/README.md" in response.json()["detail"]
    start.assert_not_called()


def test_docker_show_app_cannot_start_a_second_chrome(test_app, monkeypatch):
    from backend.browser import chrome

    monkeypatch.setattr(settings, "JOBPILOT_CHROME_VIEW_URL", VIEW_URL)
    monkeypatch.setattr(chrome, "running_endpoint", lambda: None)
    launch = MagicMock()
    monkeypatch.setattr(chrome.subprocess, "Popen", launch)

    assert test_app.post("/api/browser/show-app").status_code == 503
    launch.assert_not_called()
