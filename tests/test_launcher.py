"""Exercise the native launcher with real child processes and isolated PATH stubs."""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def executable(path: Path, body: str) -> None:
    path.write_text(f"#!{sys.executable}\n" + body)
    path.chmod(0o755)


class Sandbox:
    def __init__(self, root: Path):
        self.root = root
        self.bin = root / "fake-bin"
        self.bin.mkdir()
        self.events = root / "events.jsonl"
        self.pidfile = root / "data/jobpilot.pid"
        self.env = {
            "PATH": str(self.bin),
            "HOME": str(root),
            "PYTHONPATH": "/opt/ros/polluted",
            "EVENTS": str(self.events),
            "TEST_PYTHON": sys.executable,
            "CREDENTIAL_KEY": "test-key",
        }
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.env["JOBPILOT_PORT"] = str(sock.getsockname()[1])
        for name in ("bash", "env", "nohup", "ps", "tail", "sleep", "dirname", "readlink"):
            (self.bin / name).symlink_to(shutil.which(name))
        (self.bin / "python3").symlink_to(sys.executable)
        (root / "frontend/build").mkdir(parents=True)
        (root / "frontend/build/index.html").write_text("test frontend")
        (root / "data").mkdir()
        (root / ".venv/bin").mkdir(parents=True)
        executable(root / ".venv/bin/python", '''
import os, sys
os.execv(os.environ["TEST_PYTHON"], [os.environ["TEST_PYTHON"], *sys.argv[1:]])
''')
        if (ROOT / "jobpilot").exists():
            shutil.copy2(ROOT / "jobpilot", root / "jobpilot")
        if (ROOT / "scripts/launcher").exists():
            shutil.copytree(ROOT / "scripts/launcher", root / "scripts/launcher")
        (root / "backend/browser").mkdir(parents=True)
        for name in ("backend/__init__.py", "backend/browser/__init__.py"):
            (root / name).touch()
        shutil.copy2(ROOT / "backend/config.py", root / "backend/config.py")
        (root / "backend/browser/chrome.py").write_text('''
import json, os, subprocess
from pathlib import Path
def record(event):
    with open(os.environ["EVENTS"], "a") as f:
        f.write(json.dumps({"event": event}) + "\\n")
def chrome_dir():
    return Path("data/jobpilot-chrome")
def running_endpoint():
    record("browser_endpoint")
    return {"port": 9222, "ws": "ws://stub"} if chrome_dir().exists() else None
def ensure_chrome_running(data_dir):
    if not running_endpoint():
        subprocess.run(["google-chrome"], check=True)
        chrome_dir().mkdir()
    return running_endpoint()
def show_app(url):
    record("show_app:" + url)
    if os.environ.get("CHROME_FAIL"):
        return False
    ensure_chrome_running(chrome_dir())
    return True
def close_chrome(ws):
    assert ws == "ws://stub"
    record("close_chrome")
    chrome_dir().rmdir()
''')
        executable(self.bin / "uv", '''
import json, os, subprocess, sys, time
from pathlib import Path
args = sys.argv[1:]
assert args.pop(0) == "run"
if args[0] == "python":
    args[0] = os.environ["TEST_PYTHON"]
if args[0] == "uvicorn":
    started = Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()[19]
    with open(os.environ["EVENTS"], "a") as f:
        f.write(json.dumps({"event": "supervisor", "pid": os.getpid(),
                           "start_time": started}) + "\\n")
    time.sleep(float(os.environ.get("UV_DELAY", "0")))
    if os.environ.get("DELAYED_EXEC"):
        trampoline = ''' + repr('''
import os, sys, time
time.sleep(0.8)
os.execv(os.environ["TEST_PYTHON"], [os.environ["TEST_PYTHON"], sys.argv[1], *sys.argv[3:]])
''') + '''
        args = [os.environ["TEST_PYTHON"], "-c", trampoline,
                str(Path(__file__).with_name("uvicorn")), *args]
sys.exit(subprocess.call(args))
''')
        executable(self.bin / "uvicorn", '''
import json, os, signal, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
with open(os.environ["EVENTS"], "a") as f:
    f.write(json.dumps({"event": "backend", "pid": os.getpid(),
                        "start_time": Path("/proc/self/stat").read_text()
                            .rsplit(")", 1)[1].split()[19],
                        "pythonpath": os.environ.get("PYTHONPATH"),
                        "args": sys.argv[1:]}) + "\\n")
if os.environ.get("BACKEND_FAIL"):
    for i in range(25):
        print(f"startup failure line {i:02}", flush=True)
    sys.exit(7)
time.sleep(float(os.environ.get("BACKEND_DELAY", "0")))
def terminate(*_):
    with open(os.environ["EVENTS"], "a") as f:
        f.write(json.dumps({"event": "terminating"}) + "\\n")
    time.sleep(float(os.environ.get("STOP_DELAY", "0")))
    sys.exit(0)
signal.signal(signal.SIGTERM, terminate)
if os.environ.get("BACKEND_NO_HEALTH"):
    while True:
        time.sleep(0.1)
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(503 if Path("fail-health").exists() else 200)
        self.end_headers()
        self.wfile.write(json.dumps({"running": True, "logged_in": {
            "linkedin": True, "google": False}, "status": "ok"}).encode())
    def log_message(self, *args):
        pass
server = HTTPServer(("127.0.0.1", int(sys.argv[sys.argv.index("--port") + 1])), Handler)
print("Application startup complete", flush=True)
server.serve_forever()
''')
        executable(self.bin / "curl", '''
import json, os, sys, time, urllib.request
url = sys.argv[-1]
with open(os.environ["EVENTS"], "a") as f:
    f.write(json.dumps({"event": "curl", "url": url}) + "\\n")
try:
    time.sleep(float(os.environ.get("CURL_DELAY", "0")))
    print(urllib.request.urlopen(url, timeout=0.3).read().decode())
except Exception:
    sys.exit(22)
''')
        executable(self.bin / "google-chrome", '''
import json, os
with open(os.environ["EVENTS"], "a") as f:
    f.write(json.dumps({"event": "chrome"}) + "\\n")
''')
        executable(self.bin / "tectonic", "")

    def command(self, *args):
        return [str(self.bin / "bash"), str(self.root / "jobpilot"), *args]

    def run(self, *args, **kwargs):
        return subprocess.run(
            self.command(*args), cwd=self.root.parent, env=self.env,
            capture_output=True, text=True, timeout=kwargs.pop("timeout", 20), **kwargs,
        )

    def calls(self, event):
        rows = self.events.read_text().splitlines() if self.events.exists() else []
        return [r for line in rows if (r := json.loads(line))["event"] == event]

    def ok(self, *args):
        result = self.run(*args)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout + result.stderr


@pytest.fixture
def launcher(tmp_path):
    root = tmp_path / "repo with spaces"
    root.mkdir()
    sandbox = Sandbox(root)
    yield sandbox
    # Every child below was created in this fixture. Never scan/kill host processes.
    sandbox.run("stop")
    for row in sandbox.calls("backend") + sandbox.calls("supervisor"):
        proc = Path(f"/proc/{row['pid']}")
        try:
            if (proc / "stat").read_text().rsplit(")", 1)[1].split()[19] != row["start_time"]:
                continue
            command = "uv" if row["event"] == "supervisor" else "uvicorn"
            if str(sandbox.bin / command).encode() in (proc / "cmdline").read_bytes().split(b"\0"):
                os.kill(row["pid"], 15)
        except ProcessLookupError:
            pass
        except FileNotFoundError:
            pass


def test_start_is_idempotent_polls_health_and_cleans_ros_environment(launcher):
    launcher.ok("start")
    record = json.loads(launcher.pidfile.read_text())
    assert record["pid"] > 1 and record["start_time"] and record["command"]
    launcher.ok("start")
    backend, = launcher.calls("backend")
    assert backend["pid"] == record["pid"]  # Track uvicorn, not uv's supervisor.
    assert backend["pythonpath"] is None
    assert backend["args"] == ["backend.main:app", "--host", "127.0.0.1",
                               "--port", launcher.env["JOBPILOT_PORT"]]
    assert len(launcher.calls("chrome")) == 1
    assert any(r["url"].endswith("/api/health") for r in launcher.calls("curl"))
    launcher.ok("stop")
    assert not launcher.pidfile.exists()
    assert "Backend: stopped" in launcher.ok("status")
    assert not launcher.calls("close_chrome")


@pytest.mark.parametrize(("missing", "fix"), [
    ("frontend", "npm ci && npm run build"),
    ("tectonic", "scripts/download_tectonic.py"),
    ("google-chrome", "Chrome"),
    ("uv", "uv"),
])
def test_failed_prerequisite_has_fix_and_starts_nothing(launcher, missing, fix):
    if missing == "frontend":
        shutil.rmtree(launcher.root / "frontend/build")
    else:
        (launcher.bin / missing).unlink()
    result = launcher.run("start")
    assert result.returncode == 1
    assert fix in result.stdout + result.stderr
    assert not launcher.calls("backend")


def test_no_writer_warns_but_starts(launcher):
    output = launcher.ok("start")
    assert "warning" in output.lower() and "writer" in output.lower()


def test_dotenv_writer_key_is_recognized_without_executing_shell(launcher):
    (launcher.root / ".env").write_text("LLM_API_KEY=fixture-only\nUNUSED=$(touch owned)\n")
    assert "no writer" not in launcher.ok("start").lower()
    assert not (launcher.root / "owned").exists()


def test_stale_pid_is_replaced(launcher):
    launcher.pidfile.write_text(json.dumps({"pid": 99999999, "start_time": "0",
                                          "command": "uvicorn backend.main:app"}))
    assert "Backend: stopped" in launcher.ok("status")
    launcher.ok("start")
    assert json.loads(launcher.pidfile.read_text())["pid"] != 99999999


@pytest.mark.parametrize("tamper", ["pid", "start_time", "command"])
def test_stop_refuses_unowned_or_reused_process(launcher, tamper):
    launcher.ok("start")
    original = launcher.pidfile.read_text()
    record = json.loads(original)
    with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"]) as foreign:
        if tamper == "pid":
            record["pid"] = foreign.pid
        elif tamper == "start_time":
            record["start_time"] = "not-the-start-time"
        else:
            record["command"] = ["unrelated"]
        launcher.pidfile.write_text(json.dumps(record))
        launcher.ok("stop")
        assert foreign.poll() is None
        os.kill(json.loads(original)["pid"], 0)
        foreign.terminate()
    launcher.pidfile.write_text(original)
    launcher.ok("stop")


def test_foreign_listener_is_never_killed(launcher):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", int(launcher.env["JOBPILOT_PORT"])))
        listener.listen()
        result = launcher.run("start")
        assert result.returncode == 1
        assert f"port {launcher.env['JOBPILOT_PORT']} is in use" in result.stdout + result.stderr
        assert not launcher.calls("backend")
        assert listener.getsockname()[1] > 0


def test_help_reports_default_port_without_touching_owners_listener(launcher):
    launcher.env.pop("JOBPILOT_PORT")
    assert "127.0.0.1:8000" in launcher.ok("help")
    assert not launcher.calls("backend")
    assert not launcher.calls("curl")


def test_concurrent_starts_create_one_backend(launcher):
    launcher.env["UV_DELAY"] = "0.5"
    processes = [subprocess.Popen(launcher.command("start"), env=launcher.env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for _ in range(2)]
    outputs = []
    for process in processes:
        output = process.communicate(timeout=20)
        assert process.returncode == 0, output
        outputs.extend(output)
    assert "waiting for the running start…" in "".join(outputs)
    assert len(launcher.calls("backend")) == 1
    assert len(launcher.calls("chrome")) == 1


def test_backend_start_failure_does_not_leave_pidfile_or_open_chrome(launcher):
    launcher.env["BACKEND_FAIL"] = "1"
    result = launcher.run("start")
    assert result.returncode == 1
    output = result.stdout + result.stderr
    assert "Backend exited (code 7)" in output
    assert "backend.out" in output
    assert "within 60 s" not in output
    assert "startup failure line 04" not in output
    assert "startup failure line 05" in output
    assert "startup failure line 24" in output
    assert not launcher.pidfile.exists()
    assert not launcher.calls("chrome")


def process_is_running(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


def test_start_timeout_cancels_delayed_uv_before_it_can_launch_backend(launcher):
    launcher.env["UV_DELAY"] = "65"
    result = launcher.run("start", timeout=70)
    assert result.returncode == 1
    assert "backend.out" in result.stdout + result.stderr
    assert "within 60 s" in result.stdout + result.stderr
    assert "Backend exited" not in result.stdout + result.stderr
    supervisor, = launcher.calls("supervisor")
    assert not process_is_running(supervisor["pid"])
    assert not launcher.pidfile.exists()
    assert not launcher.calls("backend")


def test_interrupted_start_cancels_owned_launch_process(launcher):
    launcher.env["UV_DELAY"] = "10"
    with subprocess.Popen(launcher.command("start"), env=launcher.env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE) as controller:
        deadline = time.monotonic() + 5
        while not launcher.calls("supervisor") and time.monotonic() < deadline:
            time.sleep(0.05)
        supervisor, = launcher.calls("supervisor")
        controller.terminate()
        controller.communicate(timeout=5)
        assert controller.returncode != 0
        assert not process_is_running(supervisor["pid"])
        assert not launcher.pidfile.exists()


def test_pidfile_write_failure_cleans_up_started_backend(launcher):
    (launcher.root / "data/jobpilot.pid.tmp").mkdir()
    assert launcher.run("start").returncode == 1
    backend, = launcher.calls("backend")
    assert not process_is_running(backend["pid"])
    assert not launcher.calls("chrome")


def test_restart_accepts_time_wait_but_not_an_active_listener(launcher):
    address = ("127.0.0.1", int(launcher.env["JOBPILOT_PORT"]))
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(address)
        listener.listen()
        with socket.create_connection(address) as client:
            accepted, _ = listener.accept()
            accepted.close()  # Server closes first, leaving TIME_WAIT on its port.
            assert client.recv(1) == b""
    launcher.ok("start")
    launcher.ok("stop")
    launcher.ok("start")
    assert len(launcher.calls("backend")) == 2


def test_chrome_failure_prints_url_and_leaves_backend_running(launcher):
    launcher.env["CHROME_FAIL"] = "1"
    assert f"http://127.0.0.1:{launcher.env['JOBPILOT_PORT']}" in launcher.ok("start")
    assert "Backend: running" in launcher.ok("status")


def test_explicit_chrome_stop_uses_cdp_and_does_not_start_chrome(launcher):
    launcher.ok("stop", "--chrome")
    assert not launcher.calls("chrome")
    launcher.ok("start")
    launcher.ok("stop", "--chrome")
    assert len(launcher.calls("close_chrome")) == 1
    assert len(launcher.calls("chrome")) == 1


def test_status_reports_browser_logins_and_decision_reachability(launcher):
    launcher.env["DECISION_MODEL_URL"] = f"http://127.0.0.1:{launcher.env['JOBPILOT_PORT']}/v1"
    launcher.ok("start")
    output = launcher.ok("status")
    assert "Backend: running" in output
    assert "Chrome: running" in output
    assert "linkedin: signed in" in output and "google: signed out" in output
    assert "Decision model: reachable" in output
    launcher.ok("stop")
    assert "Decision model: unreachable" in launcher.ok("status")


def test_logs_tails_the_backend_output(launcher):
    log = launcher.root / "data/logs/backend.out"
    log.parent.mkdir()
    log.write_text("launcher-test-log-line\n")
    with subprocess.Popen(launcher.command("logs"), env=launcher.env,
                          stdout=subprocess.PIPE, text=True) as process:
        assert process.stdout.readline().strip() == "launcher-test-log-line"
        process.terminate()


def test_invalid_port_and_arguments_fail_with_actionable_help(launcher):
    launcher.env["JOBPILOT_PORT"] = "not-a-port"
    result = launcher.run("start")
    assert result.returncode == 1
    assert "JOBPILOT_PORT" in result.stdout + result.stderr
    assert not launcher.calls("backend")
    assert launcher.run("stop", "--unknown").returncode == 1
    launcher.env["JOBPILOT_PORT"] = "8125"
    assert "start" in launcher.ok("help")


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if result := predicate():
            return result
        time.sleep(0.02)
    pytest.fail("Timed out waiting for the isolated launcher fixture")


def test_launcher_resolves_absolute_and_relative_symlink_chain(launcher, tmp_path):
    links = tmp_path / "local bin"
    links.mkdir()
    (links / "absolute").symlink_to(launcher.root / "jobpilot")
    entry = links / "jobpilot"
    entry.symlink_to("absolute")
    launcher.command = lambda *args: [str(entry), *args]
    assert f"127.0.0.1:{launcher.env['JOBPILOT_PORT']}" in launcher.ok("help")
    launcher.ok("start")
    assert json.loads(launcher.pidfile.read_text())["root"] == str(launcher.root)
    assert "stopping (up to 10 s)…" in launcher.ok("stop")


@pytest.mark.parametrize("override", [False, True])
def test_dotenv_port_uses_backend_parsing_and_environment_precedence(launcher, override):
    port = launcher.env["JOBPILOT_PORT"]
    (launcher.root / ".env").write_text(
        'export jobpilot_port="${PORT_VALUE}" # quoted, interpolated and case insensitive\n'
    )
    launcher.env["PORT_VALUE"] = "1" if override else port
    if not override:
        launcher.env.pop("JOBPILOT_PORT")
    assert f"127.0.0.1:{port}" in launcher.ok("help")
    launcher.ok("start")
    assert json.loads(launcher.pidfile.read_text())["port"] == int(port)


@pytest.mark.parametrize("curl_delay", ["0", "1"])
def test_start_rescans_delayed_exec_in_repo_with_spaces(launcher, curl_delay):
    launcher.env["DELAYED_EXEC"] = "1"
    launcher.env["CURL_DELAY"] = curl_delay
    with subprocess.Popen(launcher.command("start"), env=launcher.env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as controller:
        wait_for(launcher.pidfile.exists)
        initial = json.loads(launcher.pidfile.read_text())
        assert "-c" in initial["command"]
        output = controller.communicate(timeout=10)
        assert controller.returncode == 0, output
    final = json.loads(launcher.pidfile.read_text())
    assert final["pid"] == initial["pid"]
    assert "-c" not in final["command"]
    assert len(launcher.calls("backend")) == 1


def test_foreign_server_taking_port_during_start_is_not_accepted_or_killed(launcher):
    launcher.env.update(UV_DELAY="0.6", BACKEND_NO_HEALTH="1")
    log = launcher.root / "data/logs/backend.out"
    log.parent.mkdir()
    log.write_text("Application startup complete\n")  # A previous run cannot prove ownership.
    with subprocess.Popen(launcher.command("start"), env=launcher.env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as controller:
        wait_for(lambda: launcher.calls("supervisor"))
        with subprocess.Popen([sys.executable, "-u", "-c", '''
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')
    def log_message(self, *args):
        pass
server = HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler)
print("listening", flush=True)
server.serve_forever()
''', launcher.env["JOBPILOT_PORT"]], stdout=subprocess.PIPE, text=True) as foreign:
            try:
                assert foreign.stdout.readline().strip() == "listening"
                output = "".join(controller.communicate(timeout=10))
                assert controller.returncode == 1, output
                assert f"port {launcher.env['JOBPILOT_PORT']} is in use" in output
                assert foreign.poll() is None
                assert not launcher.calls("chrome")
                assert not launcher.pidfile.exists()
                backend, = launcher.calls("backend")
                assert not process_is_running(backend["pid"])
            finally:
                foreign.terminate()


def test_ctrl_c_during_prerequisite_check_has_no_traceback(launcher):
    config = launcher.root / "backend/config.py"
    with config.open("a") as source:
        source.write('\nPath("checking").touch()\nimport time\ntime.sleep(10)\n')
    with subprocess.Popen(launcher.command("start"), cwd=launcher.root, env=launcher.env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as controller:
        wait_for((launcher.root / "checking").exists)
        controller.send_signal(signal.SIGINT)
        output = "".join(controller.communicate(timeout=3))
        assert controller.returncode == 1, output
        assert "Interrupted" in output
        assert "Traceback" not in output
        assert not launcher.calls("supervisor")


def test_second_ctrl_c_during_cancel_does_not_orphan_backend(launcher):
    launcher.env.update(BACKEND_NO_HEALTH="1", STOP_DELAY="1")
    with subprocess.Popen(launcher.command("start"), env=launcher.env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as controller:
        backend, = wait_for(lambda: launcher.calls("backend"))
        wait_for(launcher.pidfile.exists)
        controller.send_signal(signal.SIGINT)
        wait_for(lambda: launcher.calls("terminating"))
        controller.send_signal(signal.SIGINT)
        output = "".join(controller.communicate(timeout=5))
        assert controller.returncode == 1, output
        assert "Traceback" not in output
        assert not process_is_running(backend["pid"])
        supervisor, = launcher.calls("supervisor")
        assert not process_is_running(supervisor["pid"])
        assert not launcher.pidfile.exists()


def test_ctrl_c_while_waiting_for_start_lock_has_no_traceback(launcher):
    import fcntl

    with (launcher.root / "data/.launcher.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with subprocess.Popen(launcher.command("start"), env=launcher.env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as waiter:
            assert "waiting for the running start…" in waiter.stdout.readline()
            waiter.send_signal(signal.SIGINT)
            output = "".join(waiter.communicate(timeout=3))
            assert waiter.returncode == 1, output
            assert "Traceback" not in output
            assert not launcher.calls("supervisor")


def test_interrupted_ps_snapshot_is_retried(launcher):
    (launcher.bin / "ps").unlink()
    executable(launcher.bin / "ps", f'''
import os, sys
from pathlib import Path
marker = Path("ps-failed")
if not marker.exists():
    marker.touch()
    sys.exit(1)
os.execv({shutil.which("ps")!r}, ["ps", *sys.argv[1:]])
''')
    assert "Backend started" in launcher.ok("start")


@pytest.mark.parametrize("size", [5 * 1024 * 1024 - 1, 5 * 1024 * 1024])
def test_backend_log_rotates_at_five_mb_and_keeps_one_old(launcher, size):
    log = launcher.root / "data/logs/backend.out"
    log.parent.mkdir()
    log.write_bytes(b"x" * size)
    old = log.with_name("backend.out.1")
    old.write_text("older log")
    launcher.ok("start")
    if size < 5 * 1024 * 1024:
        assert log.stat().st_size > size
        assert old.read_text() == "older log"
    else:
        assert old.stat().st_size == size
        assert log.stat().st_size < 1024
    assert not log.with_name("backend.out.2").exists()


def test_unhealthy_status_skips_browser_calls(launcher):
    launcher.ok("start")
    (launcher.root / "fail-health").touch()
    before = len(launcher.calls("browser_endpoint"))
    output = launcher.ok("status")
    assert "not healthy" in output
    assert "Chrome: not checked" in output
    assert len(launcher.calls("browser_endpoint")) == before
    assert not any(r["url"].endswith("/api/browser/status") for r in launcher.calls("curl"))


def test_decision_model_hostname_skips_unbounded_dns(launcher):
    launcher.env["DECISION_MODEL_URL"] = "https://unresolvable.invalid/v1"
    assert "DNS lookup skipped" in launcher.ok("status")


def test_missing_bundled_skills_allows_import_and_empty_vocabulary(tmp_path):
    # A fresh install has neither the bundled vocabulary nor the owner's optional file.
    module = tmp_path / "gazetteer.py"
    shutil.copy2(ROOT / "backend/matching/gazetteer.py", module)
    result = subprocess.run([sys.executable, "-c", '''
import hashlib, runpy, sys
from pathlib import Path
module = runpy.run_path(sys.argv[1])
gazetteer = module["Gazetteer"]
assert module["GAZETTEER_VERSION"] == hashlib.sha256(b"").hexdigest()[:12]
assert gazetteer.load().extract("Python SQL") == []
assert gazetteer.load(my_skills="python").owner_skills == frozenset({"python"})
path = Path(sys.argv[1]).parent / "skills.txt"
before = module["file_version"](path)
path.write_text("python")
assert module["file_version"](path) != before
assert gazetteer.load(path=path).extract("python")
''', str(module)], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "missing; using an empty skills vocabulary" in result.stderr
