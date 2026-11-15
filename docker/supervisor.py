"""Ordered startup and fail-closed supervision for the optional Docker desktop."""

import errno
import fcntl
import json
import os
import secrets
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


def private_write(path: Path, content: str | bytes) -> None:
    """Replace restored/unreadable files atomically without following symlinks."""
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb" if isinstance(content, bytes) else "w") as stream:
            stream.write(content)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def prepare_password(path: Path) -> None:
    try:
        password = "" if path.is_symlink() else path.read_text().strip()
    except (OSError, UnicodeError):
        password = ""
    # Classic VNC uses eight bytes, so generate exactly eight ASCII characters.
    if len(password) != 8 or not password.isascii() or not password.isprintable():
        password = secrets.token_urlsafe(6)
    private_write(path, password + "\n")


def prepare_credential_key(data: Path) -> None:
    """Prevent backend.config from writing .env on the read-only root filesystem."""
    from cryptography.fernet import Fernet

    if os.environ.get("CREDENTIAL_KEY"):
        return
    path = data / "credential.key"
    try:
        key = path.read_text().strip()
    except FileNotFoundError:
        key = Fernet.generate_key().decode()
        private_write(path, key + "\n")
    # A broken existing key must fail, never silently invalidate encrypted data.
    Fernet(key.encode())
    path.chmod(0o600)
    os.environ["CREDENTIAL_KEY"] = key


def copy_cli_credentials(source: Path, home: Path) -> None:
    """Copy only the mounted login files, never symlinks or provider directories."""
    for relative in (".claude/.credentials.json", ".codex/auth.json", ".codex/config.toml"):
        path = Path(relative)
        directory_fd = None
        try:
            # Refuse symlinks in both the provider directory and the final file;
            # O_NOFOLLOW also closes the check/open race. Missing/dangling mounts
            # are optional here (Compose rejects missing declared host sources).
            directory_fd = os.open(
                source / path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
            fd = os.open(
                path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd
            )
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                os.close(fd)
                continue
            with os.fdopen(fd, "rb") as stream:
                content = stream.read()
        except OSError as exc:
            if exc.errno in (errno.ENOENT, errno.ELOOP, errno.ENOTDIR):
                continue
            raise
        finally:
            if directory_fd is not None:
                os.close(directory_fd)
        dest = home / path
        dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        private_write(dest, content)


def prepare_xauthority(path: Path) -> None:
    private_write(path, "")
    # Feed the random cookie over stdin so it never appears in process arguments.
    result = subprocess.run(
        ["xauth", "-f", str(path)],
        input=f"add :99 . {secrets.token_hex(16)}\n", text=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if result.returncode:
        raise RuntimeError("Could not create the private Xvfb authorization cookie")
    os.environ["XAUTHORITY"] = str(path)


def record_endpoint(profile: Path, endpoint: dict) -> None:
    # Fixed-port Chrome does not write DevToolsActivePort; native helpers require it.
    ws = urlsplit(endpoint["webSocketDebuggerUrl"])
    if ws.port != 9222 or ws.hostname not in ("127.0.0.1", "localhost"):
        raise RuntimeError("Unexpected Chrome debug endpoint")
    private_write(profile / "DevToolsActivePort", f"9222\n{ws.path}\n")


class Supervisor:
    def __init__(self):
        self.children: list[tuple[str, subprocess.Popen]] = []

    def spawn(self, name: str, command: list[str]) -> subprocess.Popen:
        child = subprocess.Popen(command, start_new_session=True)
        self.children.append((name, child))
        return child

    def check(self) -> None:
        for name, child in self.children:
            code = child.poll()
            if code is not None:
                raise RuntimeError(f"{name} exited with status {code}")

    def wait_for(self, name: str, probe, timeout: float = 30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.check()
            result = probe()
            if result:
                return result
            time.sleep(0.2)
        raise RuntimeError(f"Timed out waiting for {name}")

    def stop(self) -> None:
        # A second stop signal must not interrupt cleanup or release the volume
        # lock while Chrome is still flushing its profile.
        previous = {
            sig: signal.signal(sig, signal.SIG_IGN) for sig in (signal.SIGTERM, signal.SIGINT)
        }
        try:
            self._stop_children()
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)

    def _stop_children(self) -> None:
        # Reverse order keeps the display available while Chrome flushes its profile.
        for _, child in reversed(self.children):
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()


def display_ready() -> bool:
    return subprocess.run(
        ["xdpyinfo", "-display", ":99"], stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, timeout=2,
    ).returncode == 0


def chrome_endpoint() -> dict | None:
    try:
        with urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=1) as response:
            return json.load(response)
    except (OSError, ValueError):
        return None


def port_ready(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def main() -> int:
    supervisor = Supervisor()
    data = Path(os.environ.get("JOBPILOT_DATA_DIR", "/app/data"))
    lock = None

    def shutdown(_signum, _frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        os.umask(0o077)
        data.mkdir(parents=True, exist_ok=True)
        lock = (data / "desktop.lock").open("a")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        home = Path(os.environ["HOME"])
        home.mkdir(mode=0o700, parents=True, exist_ok=True)
        prepare_password(data / "chrome-view.password")
        prepare_credential_key(data)
        copy_cli_credentials(Path("/host-cli"), home)

        profile = data / "jobpilot-chrome"
        profile.mkdir(mode=0o700, exist_ok=True)
        # The volume lock excludes another container; these are crash leftovers.
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket", "DevToolsActivePort"):
            (profile / name).unlink(missing_ok=True)

        authority = home / ".Xauthority"
        prepare_xauthority(authority)
        supervisor.spawn("Xvfb", [
            "Xvfb", ":99", "-screen", "0", "1440x900x24",
            "-nolisten", "tcp", "-auth", str(authority),
        ])
        supervisor.wait_for("Xvfb", display_ready)
        supervisor.spawn("Chrome", [
            "google-chrome-stable", f"--user-data-dir={profile}",
            "--remote-debugging-port=9222", "--remote-debugging-address=127.0.0.1",
            "--no-first-run", "--no-default-browser-check",
            "--window-size=1440,900", "--start-maximized", "about:blank",
        ])
        endpoint = supervisor.wait_for("Chrome sandbox and debug endpoint", chrome_endpoint)
        record_endpoint(profile, endpoint)
        supervisor.spawn("x11vnc", [
            "x11vnc", "-display", ":99", "-auth", str(authority),
            "-localhost", "-listen", "127.0.0.1",
            "-rfbport", "5900", "-forever", "-shared", "-noxdamage",
            "-passwdfile", str(data / "chrome-view.password"),
        ])
        supervisor.wait_for("password-protected VNC", lambda: port_ready(5900))
        # Use the Debian executable and its /usr/bin/python3, not the application venv.
        supervisor.spawn("noVNC", [
            "/usr/bin/websockify", "--web=/usr/share/novnc", "0.0.0.0:6080", "127.0.0.1:5900",
        ])
        supervisor.wait_for("noVNC", lambda: port_ready(6080))
        supervisor.spawn("backend", [
            "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000",
        ])
        while True:
            supervisor.check()
            time.sleep(0.5)
    except (OSError, ValueError, RuntimeError) as exc:
        # Never echo credentials (including invalid restored key contents).
        detail = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        print(f"JobPilot container startup/runtime failure: {detail}.", file=sys.stderr)
        print(
            "Check data ownership (uid 1000), credential files and container logs. "
            "For Chrome sandbox failures, enable host unprivileged user namespaces and "
            "load the supplied seccomp profile; see docker/README.md. Sandbox stays enabled.",
            file=sys.stderr,
        )
        return 1
    finally:
        supervisor.stop()
        if lock is not None:
            lock.close()


if __name__ == "__main__":
    sys.exit(main())
