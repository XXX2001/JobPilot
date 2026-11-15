"""Process identity checks for Linux and macOS; never signal a port's owner."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path


def identity(pid: int) -> dict | None:
    if type(pid) is not int or pid <= 1:
        return None
    try:
        if Path("/proc/self/stat").exists():
            proc = Path(f"/proc/{pid}")
            # comm can contain spaces and parentheses; fields after its last ')' are fixed.
            fields = (proc / "stat").read_text().rsplit(")", 1)[1].split()
            if fields[0] == "Z":
                return None
            boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            command = (proc / "cmdline").read_bytes().rstrip(b"\0").split(b"\0")
            return {"pid": pid, "start_time": f"{boot}:{fields[19]}",
                    "command": [os.fsdecode(arg) for arg in command]}
        # macOS has no /proc. Wide ps output avoids truncating the command line.
        def ps(field):
            return subprocess.check_output(
                ["ps", "-ww", "-p", str(pid), "-o", f"{field}="], text=True,
                stderr=subprocess.DEVNULL, env={**os.environ, "LC_ALL": "C"},
            ).strip()
        if ps("stat").startswith("Z"):
            return None
        return {"pid": pid, "start_time": ps("lstart"), "command": ps("command")}
    except (OSError, subprocess.CalledProcessError, IndexError):
        return None


def is_backend(process: dict, port: int) -> bool:
    command = process["command"]
    if isinstance(command, list):
        # The exact argument sequence excludes unrelated Python processes whose code
        # merely mentions uvicorn or backend.main:app.
        expected = ["backend.main:app", "--host", "127.0.0.1", "--port", str(port)]
        return command[-5:] == expected and any(
            Path(arg).name == "uvicorn" for arg in command[:-5]
        )
    return f"uvicorn backend.main:app --host 127.0.0.1 --port {port}" in command


def owned(record: dict | None, root: Path) -> bool:
    if not isinstance(record, dict) or record.get("root") != str(root):
        return False
    port = record.get("port")
    if type(port) is not int or not 1 <= port <= 65535:
        return False
    current = identity(record.get("pid"))
    return bool(current and is_backend(current, port) and all(
        current[key] == record.get(key) for key in ("pid", "start_time", "command")
    ))


def descendants(pid: int) -> set[int]:
    # ps can itself be interrupted or lose a process during its snapshot. Retry
    # before giving up so cancellation still has a chance to collect the children.
    for attempt in range(3):
        try:
            result = subprocess.run(["ps", "-ax", "-o", "pid=,ppid="],
                                    capture_output=True, text=True, check=True)
            break
        except subprocess.CalledProcessError:
            if attempt == 2:
                raise RuntimeError(
                    "Could not inspect backend descendants with ps; retry."
                ) from None
    pairs = [tuple(map(int, line.split())) for line in result.stdout.splitlines()]
    found = {pid}
    while True:
        expanded = found | {child for child, parent in pairs if parent in found}
        if expanded == found:
            return found - {pid}
        found = expanded


def signal_owned(record: dict, root: Path, sig: int) -> bool:
    """Pin the process with a pidfd where available, then recheck its full identity."""
    fd = None
    try:
        if hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"):
            try:
                fd = os.pidfd_open(record["pid"])
            except ProcessLookupError:
                return False
            except OSError:
                pass  # Older Linux kernels, like macOS, need the checked PID path.
        if not owned(record, root):
            return False
        if fd is not None:
            signal.pidfd_send_signal(fd, sig)
        else:
            os.kill(record["pid"], sig)
        return True
    except ProcessLookupError:
        return False
    finally:
        if fd is not None:
            os.close(fd)


def stop_owned(record: dict, root: Path) -> bool:
    signal_owned(record, root, signal.SIGTERM)
    deadline = time.monotonic() + 10
    while owned(record, root) and time.monotonic() < deadline:
        time.sleep(0.1)
    if owned(record, root):
        signal_owned(record, root, signal.SIGKILL)
        deadline = time.monotonic() + 2
        while owned(record, root) and time.monotonic() < deadline:
            time.sleep(0.1)
    return not owned(record, root)


def cancel_launch(launch: dict | None, backend: dict | None, root: Path, port: int) -> bool:
    """Cancel this exact `uv run uvicorn` invocation, even before uvicorn appears.

    Freeze the verified launch process before collecting children so it cannot
    spawn uvicorn between collection and cancellation. Never signal a process
    group: dependency builders or other unrelated descendants are not backends.
    """
    records = [backend] if backend else []
    current = identity(launch["pid"]) if launch else None
    if (current and current["start_time"] == launch["start_time"]
            and is_backend(current, port)):
        supervisor = current | {"root": str(root), "port": port}
        if signal_owned(supervisor, root, signal.SIGSTOP):
            try:
                for pid in descendants(supervisor["pid"]):
                    child = identity(pid)
                    if child and is_backend(child, port):
                        records.append(child | {"root": str(root), "port": port})
            finally:
                # This is the verified backend launch command, not an arbitrary uv.
                signal_owned(supervisor, root, signal.SIGKILL)
    stopped = True
    for record in records:
        stopped = stop_owned(record, root) and stopped
    return stopped
