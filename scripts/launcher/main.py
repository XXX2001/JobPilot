"""Native launcher controller. Kept separate from bash for safe process bookkeeping."""

from __future__ import annotations

import fcntl
import ipaddress
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from processes import cancel_launch, descendants, identity, is_backend, owned, stop_owned

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
PIDFILE = DATA / "jobpilot.pid"
LOG = DATA / "logs/backend.out"
sys.path.insert(0, str(ROOT))


@contextmanager
def blocked_signals(*, discard=False):
    """Finish spawning/cancelling children before handling another interruption."""
    signals = (signal.SIGINT, signal.SIGTERM)
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, signals)
    handlers = {}
    try:
        if discard:
            handlers = {sig: signal.signal(sig, signal.SIG_IGN) for sig in signals}
        yield previous
    finally:
        # Discard signals received during cancellation before restoring the handlers.
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


def configured_port() -> int:
    # Same dotenv parsing, case folding and env-over-file precedence as backend Settings.
    # Keep help independent of backend.config's credential-generation side effect.
    from pydantic import Field, ValidationError
    from pydantic_settings import BaseSettings, SettingsConfigDict

    class PortSettings(BaseSettings):
        model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")
        jobpilot_port: int = Field(default=8000, ge=1, le=65535)

    try:
        return PortSettings().jobpilot_port
    except ValidationError:
        raise RuntimeError("Set JOBPILOT_PORT to a port number between 1 and 65535.") from None


def port_conflict(port: int) -> RuntimeError:
    return RuntimeError(
        f"port {port} is in use by another server. "
        "Choose another port: JOBPILOT_PORT=8123 ./jobpilot start"
    )


def backend_exited(code: int) -> RuntimeError:
    with LOG.open(errors="replace") as log:
        tail = "".join(deque(log, maxlen=20)).rstrip()
    return RuntimeError(f"Backend exited (code {code}). Last 20 lines of {LOG}:\n{tail}")


def read_record() -> dict | None:
    try:
        record = json.loads(PIDFILE.read_text())
        return record if isinstance(record, dict) else None
    except (OSError, ValueError):
        return None


def settings():
    # Reuse the app's .env parser and writer selection; never source .env as shell.
    from backend.config import settings

    return settings


def chrome_binary() -> str | None:
    for name in ("google-chrome", "google-chrome-stable"):
        if path := shutil.which(name):
            return path
    for base in (Path("/Applications"), Path.home() / "Applications"):
        path = base / "Google Chrome.app/Contents/MacOS/Google Chrome"
        if os.access(path, os.X_OK):
            return str(path)
    return None


def browser():
    from backend.browser import chrome

    if binary := chrome_binary():
        chrome.CHROME = binary
    return chrome


def check_prerequisites() -> None:
    if not chrome_binary():
        raise RuntimeError("Chrome not found. Install Google Chrome and retry ./jobpilot start.")
    if not (ROOT / "frontend/build/index.html").is_file():
        raise RuntimeError("Frontend build missing. Run: cd frontend && npm ci && npm run build")
    if not (shutil.which("tectonic") or os.access(ROOT / "bin/tectonic", os.X_OK)):
        raise RuntimeError("Tectonic missing. Run: uv run python scripts/download_tectonic.py")
    if not shutil.which("curl"):
        raise RuntimeError("curl not found. Install curl and retry ./jobpilot start.")
    if not settings().writing_model_available():
        print("Warning: no writer configured. Install claude or codex, or set LLM_API_KEY in .env.")


def request(url: str, timeout: float = 2) -> str | None:
    try:
        result = subprocess.run(
            ["curl", "--noproxy", "*", "--fail", "--silent", "--max-time", str(timeout), url],
            capture_output=True, text=True, timeout=timeout + 0.2,
        )
        return result.stdout if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def show_app(url: str) -> None:
    try:
        if not browser().show_app(url):
            print("Chrome could not open the app; open the URL below in your browser.")
    except Exception:
        print("Chrome could not open the app; open the URL below in your browser.")
    print(f"JobPilot: {url}")


def start() -> None:
    record = read_record()
    if owned(record, ROOT):
        print(f"Backend already running (pid {record['pid']}).")
        show_app(f"http://127.0.0.1:{record['port']}")
        return
    PIDFILE.unlink(missing_ok=True)
    port = configured_port()
    check_prerequisites()
    with socket.socket() as sock:
        # Match uvicorn: a stopped server's TIME_WAIT sockets are safe to reuse.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            raise port_conflict(port) from None
    LOG.parent.mkdir(parents=True, exist_ok=True)
    if LOG.exists() and LOG.stat().st_size >= 5 * 1024 * 1024:
        LOG.replace(LOG.with_name(LOG.name + ".1"))
    url = f"http://127.0.0.1:{port}"
    ready = False
    supervisor = None
    launch = None
    record = None
    try:
        # uv supervises a child on Unix. Persist uvicorn's identity, not uv's PID.
        with blocked_signals() as previous_mask, LOG.open("ab") as log:
            log_start = log.tell()
            supervisor = subprocess.Popen(
                ["nohup", "env", "-u", "PYTHONPATH", "uv", "run", "uvicorn",
                 "backend.main:app", "--host", "127.0.0.1", "--port", str(port)],
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                # Only the controller's spawn transaction is protected. The backend
                # must receive TERM normally (signal masks otherwise survive exec).
                preexec_fn=lambda: signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask),
                env={**os.environ, "JOBPILOT_HOST": "127.0.0.1", "JOBPILOT_PORT": str(port)},
            )
            launch = identity(supervisor.pid)
        deadline = time.monotonic() + 60
        while True:
            code = supervisor.poll()
            if code is not None:
                raise backend_exited(code)
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"Backend did not become healthy within 60 s. Inspect {LOG} and retry."
                )
            if record and not owned(record, ROOT):
                # uv and its child may exec more than once before becoming uvicorn.
                record = None
                PIDFILE.unlink(missing_ok=True)
            if record is None:
                for pid in sorted(descendants(supervisor.pid)):
                    candidate = identity(pid)
                    if candidate and is_backend(candidate, port):
                        record = candidate | {"root": str(ROOT), "port": port}
                        temporary = PIDFILE.with_suffix(".pid.tmp")
                        temporary.write_text(json.dumps(record) + "\n")
                        temporary.replace(PIDFILE)
                        break
            if record:
                if not owned(record, ROOT):
                    record = None
                    PIDFILE.unlink(missing_ok=True)
                    continue
                remaining = deadline - time.monotonic()
                if remaining > 0 and request(f"{url}/api/health", min(2, remaining)) is not None:
                    with LOG.open("rb") as log:
                        log.seek(log_start)
                        started = b"Application startup complete" in log.read()
                    if not started:
                        raise port_conflict(port)
                    if not owned(record, ROOT):
                        current = identity(record["pid"])
                        if current and current["start_time"] == record["start_time"]:
                            # exec may also finish during the health request itself.
                            record = None
                            PIDFILE.unlink(missing_ok=True)
                            continue
                        raise port_conflict(port)
                    print(f"Backend started (pid {record['pid']}). Log: {LOG}", flush=True)
                    show_app(url)
                    ready = True
                    return
            time.sleep(0.1)
    finally:
        if not ready:
            with blocked_signals(discard=True):
                if not cancel_launch(launch, record, ROOT, port):
                    raise RuntimeError(
                        f"Backend failed to stop. Inspect {LOG}; run ./jobpilot stop."
                    )
                PIDFILE.unlink(missing_ok=True)
                if supervisor is not None:
                    try:
                        supervisor.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        raise RuntimeError(f"Backend failed to stop. Inspect {LOG}.") from None


def stop(close_browser: bool) -> None:
    record = read_record()
    if owned(record, ROOT):
        print("Backend: stopping (up to 10 s)…", flush=True)
        with blocked_signals(discard=True):
            if not stop_owned(record, ROOT):
                raise RuntimeError(
                    f"Backend did not stop. Inspect {LOG} and retry ./jobpilot stop."
                )
        print("Backend: stopped")
    else:
        print("Backend: stopped (no matching launcher-owned process; stale pid file ignored).")
    PIDFILE.unlink(missing_ok=True)
    if close_browser:
        chrome = browser()
        endpoint = chrome.running_endpoint()
        if endpoint:
            chrome.close_chrome(endpoint["ws"])
            print("Chrome: closed (profile and logins kept)")
        else:
            print("Chrome: stopped")


def status() -> None:
    record = read_record()
    running = owned(record, ROOT)
    healthy = False
    if running:
        url = f"http://127.0.0.1:{record['port']}"
        healthy = request(f"{url}/api/health") is not None
        health = "healthy" if healthy else "not healthy"
        print(f"Backend: running (pid {record['pid']}, {url}, {health})")
    else:
        print("Backend: stopped")
    if healthy:
        try:
            endpoint = browser().running_endpoint()
            print("Chrome: running" if endpoint else "Chrome: stopped")
        except Exception:
            print("Chrome: unavailable (check the JobPilot Chrome profile)")
    else:
        print("Chrome: not checked (backend is not healthy)")
    login_status = None
    if healthy:
        payload = request(f"{url}/api/browser/status", timeout=5)
        try:
            login_status = json.loads(payload)["logged_in"] if payload else None
        except (ValueError, KeyError, TypeError):
            pass
    if isinstance(login_status, dict) and login_status:
        print("Logins: " + ", ".join(
            f"{site}: {'signed in' if logged_in else 'signed out'}"
            for site, logged_in in login_status.items()
        ))
    else:
        print("Logins: unavailable (start the backend and JobPilot Chrome to check)")
    decision_url = settings().DECISION_MODEL_URL
    if not decision_url:
        print("Decision model: not configured (set DECISION_MODEL_URL in .env)")
        return
    # Reachability is a TCP connection, not an inference request or an auth check.
    try:
        parsed = urlsplit(decision_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError
        host = parsed.hostname
        if host == "localhost":
            host = "127.0.0.1"
        try:
            ipaddress.ip_address(host)
        except ValueError:
            print("Decision model: not checked (DNS lookup skipped; use an IP for TCP status)")
            return
        with socket.create_connection((host, parsed.port or (
            443 if parsed.scheme == "https" else 80
        )), timeout=2):
            print("Decision model: reachable (TCP; authentication/inference not checked)")
    except (OSError, ValueError):
        print("Decision model: unreachable (check DECISION_MODEL_URL and network access)")


def main() -> int:
    def interrupt(*_):
        raise KeyboardInterrupt("Interrupted. Retry with ./jobpilot start.")

    # Install before config/prerequisite checks and before waiting for the start lock.
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, interrupt)
    os.chdir(ROOT)
    os.umask(0o077)
    command = sys.argv[1]
    if command == "help":
        print("Usage: ./jobpilot start | stop [--chrome] | status | logs | help")
        print(f"Native JobPilot, bound to 127.0.0.1:{configured_port()}.")
        print("State: data/jobpilot.pid; log: data/logs/backend.out.")
        return 0
    if command == "logs":
        if not LOG.exists():
            raise RuntimeError("No backend log yet. Run ./jobpilot start first.")
        os.execvp("tail", ["tail", "-n", "100", "-f", str(LOG)])
    DATA.mkdir(parents=True, exist_ok=True)
    # An advisory lock survives crashes without stale lock-file recovery, on both OSes.
    # Keep the lock inode; deleting it would let simultaneous launchers lock different files.
    with (DATA / ".launcher.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("JobPilot: waiting for the running start…", flush=True)
            fcntl.flock(lock, fcntl.LOCK_EX)
        if command == "start":
            start()
        elif command == "stop":
            stop("--chrome" in sys.argv[2:])
        elif command == "status":
            status()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, subprocess.CalledProcessError, KeyboardInterrupt) as exc:
        print(f"JobPilot: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
