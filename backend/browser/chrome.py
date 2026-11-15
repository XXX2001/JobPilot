"""One long-lived genuine Chrome on a dedicated profile, attached to over CDP.

ensure_chrome_running(data_dir) -> {"port", "ws", "pid"}, idempotent
close_chrome(ws): graceful CDP Browser.close, flushes cookies
"""

import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

CONTAINER_CHROME_UNAVAILABLE = (
    "The JobPilot Chrome in the container is not answering. "
    "Check container logs and docker/README.md; only the supervisor may start it."
)

# ponytail: a genuine Chrome on PATH. Windows and macOS install paths when someone runs there.
CHROME = shutil.which("google-chrome") or shutil.which("google-chrome-stable") or "google-chrome"
FLAGS = [
    "--remote-debugging-port={port}",  # 0 = Chrome picks a free port, written to DevToolsActivePort
    "--no-first-run",
    "--no-default-browser-check",
    # Without this, genuine Chrome reports navigator.webdriver=true when a debug port is open
    # (measured, t1). Cost: a dismissible "unsupported command-line flag" infobar that
    # pages cannot see.
    "--disable-blink-features=AutomationControlled",
]


def _probe(port: int) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as response:
            return json.load(response)
    except Exception:
        return None


def _active(data_dir: Path) -> dict | None:
    """Endpoint recorded by *this profile's* Chrome, verified live.

    Safe against a stale file and a reused port.
    """
    try:
        port_s, ws_path = (data_dir / "DevToolsActivePort").read_text().split()[:2]
        port = int(port_s)
    except (OSError, ValueError):
        return None
    v = _probe(port)
    if (
        isinstance(v, dict)
        and isinstance(v.get("webSocketDebuggerUrl"), str)
        and v["webSocketDebuggerUrl"].endswith(ws_path)
        and isinstance(v.get("Browser"), str)
    ):
        return {
            "port": port,
            "ws": v["webSocketDebuggerUrl"],
            "version": v["Browser"],
        }
    return None


def ensure_chrome_running(data_dir, port: int = 0, extra_args=(), timeout: float = 20) -> dict:
    from backend.config import settings

    # Includes callers such as show_app: the supervisor alone owns container Chrome.
    if settings.JOBPILOT_CHROME_VIEW_URL:
        if ep := running_endpoint():
            return ep | {"reused": True}
        raise RuntimeError(CONTAINER_CHROME_UNAVAILABLE)
    data_dir = Path(data_dir).resolve()
    if data_dir == (Path.home() / ".config/google-chrome").resolve():
        raise ValueError(
            "refusing the default profile (Chrome 136+ disables remote debugging on it anyway)"
        )
    if ep := _active(data_dir):
        return ep | {"reused": True}
    lock = data_dir / "SingletonLock"
    if lock.is_symlink():  # target is "<host>-<pid>"
        pid = int(os.readlink(lock).rsplit("-", 1)[1])
        if Path(f"/proc/{pid}").exists():
            raise RuntimeError(
                f"Chrome pid {pid} owns {data_dir} without a debug port; close it first"
            )
    data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(data_dir, 0o700)
    (data_dir / "DevToolsActivePort").unlink(missing_ok=True)
    args = [
        CHROME,
        f"--user-data-dir={data_dir}",
        *(f.format(port=port) for f in FLAGS),
        *extra_args,
        "about:blank",
    ]
    log = open(data_dir.parent / f"{data_dir.name}.chrome.log", "ab")
    # own session: Chrome outlives the Python process that started it
    proc = subprocess.Popen(
        args, stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ep := _active(data_dir):
            return ep | {"reused": False, "pid": proc.pid}
        if proc.poll() is not None:
            raise RuntimeError(f"Chrome exited with {proc.returncode}; see {log.name}")
        time.sleep(0.2)
    raise TimeoutError("DevTools endpoint did not come up")


def close_chrome(ws: str) -> None:
    """Browser.close over CDP: a clean shutdown that flushes the cookie DB.

    SIGTERM may not.
    """
    from websockets.sync.client import connect

    with connect(ws, max_size=None) as c:
        c.send(json.dumps({"id": 1, "method": "Browser.close"}))
        try:
            c.recv(timeout=5)
        except Exception:
            pass


def my_chrome_pids(data_dir) -> list[int]:
    """Chrome processes started on *this* data dir only (never the owner's)."""
    needle = f"--user-data-dir={Path(data_dir).resolve()}".encode()
    out = []
    for p in Path("/proc").iterdir():
        if p.name.isdigit():
            try:
                if needle in (p / "cmdline").read_bytes():
                    out.append(int(p.name))
            except OSError:
                pass
    return out


def wait_gone(data_dir, timeout=15) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if not my_chrome_pids(data_dir):
            return True
        time.sleep(0.2)
    return False


def chrome_dir() -> Path:
    """The dedicated JobPilot profile. Never the owner's own Chrome."""
    from backend.config import settings

    return Path(settings.jobpilot_data_dir).resolve() / "jobpilot-chrome"


def running_endpoint() -> dict | None:
    """The JobPilot Chrome's CDP endpoint when it is running; never starts it."""
    from backend.config import settings

    # Allow a busy supervised Chrome up to ~5 s (3 one-second probes + 2 pauses).
    # Native callers keep their existing single-probe behavior.
    attempts = 3 if settings.JOBPILOT_CHROME_VIEW_URL else 1
    for attempt in range(attempts):
        if endpoint := _active(chrome_dir()):
            return endpoint
        if attempt < attempts - 1:
            time.sleep(1)
    return None


async def open_context(pw, profile_dir: Path, args: list[str]):
    """(context, attached) for an apply run.

    The running JobPilot Chrome when there is one: ``attached`` is True and the context
    is the owner's logged-in session, so the caller closes its own page, never the
    context. Native installs may launch a per-site Playwright profile; containers
    fail closed when the supervised Chrome is unavailable.
    """
    from backend.config import settings

    endpoint = running_endpoint()
    if endpoint is not None:
        browser = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{endpoint['port']}")
        return browser.contexts[0], True
    if settings.JOBPILOT_CHROME_VIEW_URL:
        raise RuntimeError(CONTAINER_CHROME_UNAVAILABLE)
    context = await pw.chromium.launch_persistent_context(
        user_data_dir=profile_dir.as_posix(), headless=False, args=args
    )
    return context, False


def show_app(app_url: str) -> bool:
    """Open the JobPilot page in the JobPilot Chrome (starting it), or focus its tab.

    One window then holds the app, the owner's job-site logins and the job tabs.
    False when Chrome cannot be started: the caller falls back to the default browser.
    """
    try:
        port = ensure_chrome_running(chrome_dir())["port"]
        base = f"http://127.0.0.1:{port}/json"
        tabs = json.load(urllib.request.urlopen(f"{base}/list", timeout=5))
        mine = next((t for t in tabs if t.get("url", "").startswith(app_url)), None)
        target = f"{base}/activate/{mine['id']}" if mine else f"{base}/new?{app_url}"
        request = urllib.request.Request(target, method="GET" if mine else "PUT")
        urllib.request.urlopen(request, timeout=5).read()
        return True
    except Exception:  # noqa: BLE001 - no Chrome, no display: not fatal
        return False
