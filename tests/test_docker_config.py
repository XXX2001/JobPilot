"""Docker contracts checked without a Docker daemon, including startup recovery."""

import gzip
import hashlib
import importlib.util
import json
import os
import signal
import stat
import sys
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_compose_keeps_the_browser_private_and_root_read_only():
    service = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]["jobpilot"]
    assert set(service["ports"]) == {"127.0.0.1:8000:8000", "127.0.0.1:6080:6080"}
    assert all(port.startswith("127.0.0.1:") for port in service["ports"])
    assert not service.get("privileged")
    assert not service.get("cap_add")
    assert service["cap_drop"] == ["ALL"]
    assert service.get("network_mode") != "host"
    assert service["read_only"] is True
    assert "no-new-privileges:true" in service["security_opt"]
    assert "seccomp=docker/chrome-seccomp.json" in service["security_opt"]
    assert {v.split(":")[0] for v in service["tmpfs"]} >= {
        "/tmp", "/run", "/home/jobpilot/.cache"
    }
    assert service["shm_size"] == "1gb"
    assert service["build"]["target"] == "runtime"
    assert service["environment"]["JOBPILOT_CHROME_VIEW_URL"].startswith("${JOBPILOT_CHROME_VIEW_URL:-http://127.0.0.1:6080/")
    assert service["environment"]["JOBPILOT_ALLOWED_ORIGINS"] == (
        "${JOBPILOT_ALLOWED_ORIGINS:-http://localhost:8000,http://127.0.0.1:8000}"
    )
    assert service["volumes"] == ["./data:/app/data"]
    assert service["healthcheck"]["test"][-1].endswith("/api/health")


def test_dockerfile_preserves_build_and_runs_sandboxed_as_jobpilot():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "--no-sandbox" not in dockerfile
    supervisor = (ROOT / "docker/supervisor.py").read_text()
    assert "--no-sandbox" not in supervisor
    assert "--disable-blink-features=AutomationControlled" not in supervisor
    # The native Chrome command retains its existing flag.
    assert "--disable-blink-features=AutomationControlled" in (
        ROOT / "backend/browser/chrome.py"
    ).read_text()
    assert '"--remote-debugging-address=127.0.0.1"' in supervisor
    assert '"-passwdfile"' in supervisor and '"-nopw"' not in supervisor
    assert "AS python-builder" in dockerfile
    assert "AS frontend-builder" in dockerfile
    assert "AS tectonic-fetcher" in dockerfile
    assert "AS cli" in dockerfile
    assert "AS runtime" in dockerfile
    assert "USER jobpilot" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "google-chrome-stable" in dockerfile
    assert "https://dl.google.com/linux/chrome/deb/" in dockerfile
    assert "linux_signing_key.pub" in dockerfile
    assert "signed-by=" in dockerfile and "arch=amd64" in dockerfile
    assert "novnc=1:1.3.0-1" in dockerfile
    assert "websockify=0.10.0+dfsg1-4+b1" in dockerfile
    assert "@anthropic-ai/claude-code" in dockerfile
    assert "@openai/codex" in dockerfile
    assert "EXPOSE 8000 6080" in dockerfile
    assert "|| true" not in dockerfile


def test_cli_override_copies_from_read_only_host_mounts():
    service = yaml.safe_load((ROOT / "docker-compose.cli.yml").read_text())["services"]["jobpilot"]
    assert service["build"]["target"] == "cli"
    assert service["volumes"] == [
        {
            "type": "bind", "source": "${HOME}/" + relative,
            "target": "/host-cli/" + relative, "read_only": True,
            "bind": {"create_host_path": False},
        }
        for relative in (".claude/.credentials.json", ".codex/auth.json", ".codex/config.toml")
    ]
    assert "ports" not in service and "security_opt" not in service


def test_cli_dependencies_are_pinned_and_installed_in_a_separate_stage():
    manifest = json.loads((ROOT / "docker/cli/package.json").read_text())
    lock = json.loads((ROOT / "docker/cli/package-lock.json").read_text())
    expected = {"@anthropic-ai/claude-code": "2.1.325", "@openai/codex": "0.166.0"}
    assert manifest["dependencies"] == lock["packages"][""]["dependencies"] == expected
    for name, version in expected.items():
        assert lock["packages"][f"node_modules/{name}"]["version"] == version
    for name, package in lock["packages"].items():
        if name:
            assert package["integrity"].startswith("sha512-")
            assert package["resolved"].startswith("https://registry.npmjs.org/")
    dockerfile = (ROOT / "Dockerfile").read_text()
    builder = dockerfile.split("AS cli-builder", 1)[1].split("FROM ", 1)[0]
    assert "USER root" in builder and "npm ci --omit=dev" in builder
    assert "--ignore-scripts" in builder
    assert "RUN --network=none node node_modules/@anthropic-ai/claude-code/install.cjs" in builder
    assert "COPY docker/cli/package.json docker/cli/package-lock.json" in builder
    assert "COPY --from=cli-builder /opt/jobpilot-cli /opt/jobpilot-cli" in dockerfile
    assert "npm install -g" not in dockerfile


def test_google_key_is_verified_before_apt_can_trust_it():
    dockerfile = (ROOT / "Dockerfile").read_text()
    expected = "EB4C1BFD4F042F6DDDCCEC917721F63BD38B4796"
    check = f'test "$GOOGLE_KEY_FINGERPRINT" = {expected}'
    assert f"--fingerprint {expected}" in dockerfile
    assert check in dockerfile
    assert dockerfile.index(check) < dockerfile.index('--export "$GOOGLE_KEY_FINGERPRINT"')
    assert dockerfile.index(check) < dockerfile.index('&& echo "deb [arch=amd64')


def test_seccomp_is_the_attributed_upstream_profile():
    profile_path = ROOT / "docker/chrome-seccomp.json"
    profile = json.loads(profile_path.read_text())
    assert profile["defaultAction"] == "SCMP_ACT_ERRNO"
    allowed = {s["name"] for s in profile["syscalls"] if s["action"] == "SCMP_ACT_ALLOW"}
    assert {"clone", "unshare", "setns"} <= allowed
    # The full upstream list, not merely a similarly named permissive profile.
    upstream = {**profile, "syscalls": profile["syscalls"][:-10]}
    upstream_bytes = (json.dumps(upstream, indent=4) + "\n").encode()
    assert hashlib.sha256(upstream_bytes).hexdigest() == (
        "5d92dc9c74781df77b82e38ce802ef82f0b603a9f40ac3b20f8202150b33200b"
    )
    readme = (ROOT / "docker/README.md").read_text()
    assert "https://github.com/jessfraz/dotfiles/blob/" in readme
    assert "2026-11-15" in readme
    assert hashlib.sha256(profile_path.read_bytes()).hexdigest() in readme


@pytest.fixture
def runtime():
    spec = importlib.util.spec_from_file_location(
        "docker_supervisor", ROOT / "docker/supervisor.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_view_password_is_private_persistent_and_never_logged(runtime, tmp_path, capsys):
    path = tmp_path / "chrome-view.password"
    runtime.prepare_password(path)
    first = path.read_text()
    assert len(first.strip()) == 8  # VNC authentication uses only eight bytes.
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    runtime.prepare_password(path)
    assert path.read_text() == first
    assert first.strip() not in "".join(capsys.readouterr())


@pytest.mark.parametrize("state", ["missing", "empty", "unreadable", "symlink"])
def test_view_password_recovers_after_restore(runtime, tmp_path, monkeypatch, state):
    path = tmp_path / "chrome-view.password"
    if state == "empty":
        path.touch()
    elif state == "unreadable":
        path.write_text("old-pass\n")
        read = Path.read_text

        def unreadable(self, *args, **kwargs):
            if self == path:
                raise PermissionError("restored file unreadable")
            return read(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", unreadable)
    elif state == "symlink":
        other = tmp_path / "other-secret"
        other.write_text("untouched")
        path.symlink_to(other)
    runtime.prepare_password(path)
    with path.open() as stream:
        password = stream.read().strip()
    assert len(password) == 8
    assert password != "old-pass"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not path.is_symlink()
    if state == "symlink":
        assert other.read_text() == "untouched"


def test_credential_key_survives_container_recreation(runtime, tmp_path, monkeypatch):
    monkeypatch.delenv("CREDENTIAL_KEY", raising=False)
    runtime.prepare_credential_key(tmp_path)
    first = os.environ["CREDENTIAL_KEY"]
    monkeypatch.delenv("CREDENTIAL_KEY")
    runtime.prepare_credential_key(tmp_path)
    assert os.environ["CREDENTIAL_KEY"] == first
    assert stat.S_IMODE((tmp_path / "credential.key").stat().st_mode) == 0o600


def test_bad_existing_credential_key_is_not_replaced(runtime, tmp_path, monkeypatch):
    monkeypatch.delenv("CREDENTIAL_KEY", raising=False)
    path = tmp_path / "credential.key"
    path.write_text("broken-restored-key")
    with pytest.raises(ValueError):
        runtime.prepare_credential_key(tmp_path)
    assert path.read_text() == "broken-restored-key"


def test_explicit_credential_key_takes_precedence(runtime, tmp_path, monkeypatch):
    monkeypatch.setenv("CREDENTIAL_KEY", "explicit-key-managed-in-env")
    runtime.prepare_credential_key(tmp_path)
    assert os.environ["CREDENTIAL_KEY"] == "explicit-key-managed-in-env"
    assert not (tmp_path / "credential.key").exists()


def test_password_write_failure_never_starts_services(runtime, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JOBPILOT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    # main's production signal handlers should not replace pytest's handlers.
    monkeypatch.setattr(runtime.signal, "signal", lambda *_: None)
    monkeypatch.setattr(runtime.os, "umask", lambda _: None)
    monkeypatch.setattr(runtime, "private_write", MagicMock(side_effect=PermissionError))
    spawn = MagicMock()
    monkeypatch.setattr(runtime.Supervisor, "spawn", spawn)
    assert runtime.main() == 1
    spawn.assert_not_called()
    assert "data ownership" in capsys.readouterr().err


def test_cli_credentials_are_writable_copies(runtime, tmp_path):
    source = tmp_path / "host-cli"
    home = tmp_path / "home"
    home.mkdir()
    files = (".claude/.credentials.json", ".codex/auth.json", ".codex/config.toml")
    for relative in files:
        src = source / relative
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text('{"test": true}')
        src.chmod(0o400)
    for relative in (".claude.json", ".claude/settings.json", ".codex/history.jsonl"):
        (source / relative).write_text("unrelated host state")
    runtime.copy_cli_credentials(source, home)
    assert {p.relative_to(home).as_posix() for p in home.rglob("*") if p.is_file()} == set(files)
    for relative in files:
        target = home / relative
        assert target.read_text() == '{"test": true}'
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
        target.write_text("changed inside container")
        assert (source / relative).read_text() == '{"test": true}'


@pytest.mark.parametrize("state", [
    "missing", "dangling", "symlink", "directory", "fifo", "parent_symlink", "parent_dangling",
])
def test_cli_copy_skips_missing_nonregular_and_symlink_sources(runtime, tmp_path, state):
    source, home = tmp_path / "source", tmp_path / "home"
    source.mkdir()
    home.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "auth.json").write_text("do not copy this")
    directory = source / ".codex"
    if state.startswith("parent_"):
        directory.symlink_to(outside if state == "parent_symlink" else tmp_path / "absent")
    else:
        directory.mkdir()
        path = directory / "auth.json"
        if state in ("symlink", "dangling"):
            path.symlink_to(outside / ("auth.json" if state == "symlink" else "absent"))
        elif state == "directory":
            path.mkdir()
        elif state == "fifo":
            os.mkfifo(path)
    runtime.copy_cli_credentials(source, home)
    assert not list(home.rglob("*"))
    assert (outside / "auth.json").read_text() == "do not copy this"


def test_cli_copy_skips_absent_mount_directory(runtime, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    runtime.copy_cli_credentials(tmp_path / "absent", home)
    assert not list(home.iterdir())


def test_fixed_port_chrome_is_discoverable_by_existing_helper(runtime, tmp_path, monkeypatch):
    from backend.browser import chrome

    endpoint = {"Browser": "Chrome/test", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/test"}
    runtime.record_endpoint(tmp_path, endpoint)
    probe = MagicMock(return_value=endpoint)
    monkeypatch.setattr(chrome, "_probe", probe)
    assert chrome._active(tmp_path)["port"] == 9222
    probe.assert_called_once_with(9222)


def test_supervisor_reports_child_failure_and_stops_other_children(runtime):
    supervisor = runtime.Supervisor()
    sleeper = supervisor.spawn("backend", [sys.executable, "-c", "import time; time.sleep(60)"])
    failed = supervisor.spawn("Chrome", [sys.executable, "-c", "raise SystemExit(12)"])
    failed.wait(timeout=5)
    try:
        with pytest.raises(RuntimeError, match="Chrome exited with status 12"):
            supervisor.check()
    finally:
        supervisor.stop()
    assert sleeper.poll() is not None


def test_second_sigterm_does_not_interrupt_shutdown(runtime, monkeypatch):
    supervisor = runtime.Supervisor()
    original = signal.getsignal(signal.SIGTERM)

    def finish(**kwargs):
        assert signal.getsignal(signal.SIGTERM) == signal.SIG_IGN
        signal.raise_signal(signal.SIGTERM)

    child = MagicMock(pid=123, wait=MagicMock(side_effect=finish))
    supervisor.children = [("Chrome", child)]
    monkeypatch.setattr(runtime.os, "killpg", MagicMock())
    supervisor.stop()
    assert child.wait.call_count == 2
    assert signal.getsignal(signal.SIGTERM) == original


def test_xauthority_cookie_is_private_and_not_in_process_arguments(runtime, tmp_path, monkeypatch):
    authority = tmp_path / ".Xauthority"
    run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr(runtime.subprocess, "run", run)
    monkeypatch.setenv("XAUTHORITY", "previous")
    runtime.prepare_xauthority(authority)
    assert stat.S_IMODE(authority.stat().st_mode) == 0o600
    assert os.environ["XAUTHORITY"] == str(authority)
    assert run.call_args.args[0] == ["xauth", "-f", str(authority)]
    assert run.call_args.kwargs["input"].startswith("add :99 . ")


def test_supervisor_starts_private_display_and_sandboxed_chrome(runtime, tmp_path, monkeypatch):
    monkeypatch.setenv("JOBPILOT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(runtime.signal, "signal", lambda *_: None)
    monkeypatch.setattr(runtime.os, "umask", lambda _: None)
    monkeypatch.setattr(runtime, "prepare_credential_key", lambda _: None)
    monkeypatch.setattr(runtime, "prepare_xauthority", MagicMock())
    monkeypatch.setattr(runtime, "copy_cli_credentials", lambda *_: None)
    spawn = MagicMock()
    monkeypatch.setattr(runtime.Supervisor, "spawn", spawn)
    monkeypatch.setattr(runtime.Supervisor, "wait_for", MagicMock(return_value={
        "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/test",
    }))
    monkeypatch.setattr(runtime.Supervisor, "check", MagicMock(side_effect=RuntimeError("done")))
    assert runtime.main() == 1
    commands = {call.args[0]: call.args[1] for call in spawn.call_args_list}
    display = commands["Xvfb"]
    assert display[display.index("-nolisten") + 1] == "tcp"
    assert display[display.index("-auth") + 1] == str(tmp_path / "home/.Xauthority")
    assert "--disable-blink-features=AutomationControlled" not in commands["Chrome"]
    assert "--no-sandbox" not in commands["Chrome"]


@pytest.fixture
def chrome_age():
    spec = importlib.util.spec_from_file_location("chrome_age", ROOT / "docker/check_chrome_age.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("days, warns", [(1, False), (45, False), (45.01, True), (100, True)])
def test_entrypoint_warns_only_for_chrome_builds_older_than_45_days(
    chrome_age, tmp_path, capsys, days, warns
):
    now = datetime(2026, 11, 15, tzinfo=timezone.utc)
    built = now - timedelta(days=days)
    path = tmp_path / "changelog.gz"
    with gzip.open(path, "wt") as stream:
        stream.write(
            "google-chrome-stable (157.0.8103.78-1) stable; urgency=low\n\n"
            f" -- Chrome Linux Team <chromium-dev@chromium.org>  {format_datetime(built)}\n"
        )
    # The file was just written: its filesystem mtime must not reset the build age.
    chrome_age.warn_if_stale(path, now=now)
    output = capsys.readouterr().err
    assert ("older than 45 days" in output) == warns
    if warns:
        assert "docker compose build --pull" in output
    else:
        assert output == ""


@pytest.mark.parametrize("state", ["missing", "invalid", "truncated"])
def test_unknown_chrome_build_date_warns_without_stopping_startup(
    chrome_age, tmp_path, capsys, state
):
    path = tmp_path / "changelog.gz"
    if state == "invalid":
        with gzip.open(path, "wt") as stream:
            stream.write(" -- maintainer  invalid date\n")
    elif state == "truncated":
        path.write_bytes(b"\x1f\x8b")
    chrome_age.warn_if_stale(path)
    assert "Cannot determine the installed Chrome build date" in capsys.readouterr().err


def test_chrome_age_check_is_wired_into_entrypoint_and_documented():
    entrypoint = (ROOT / "docker/entrypoint.sh").read_text()
    age_check = "python /app/docker/check_chrome_age.py"
    assert entrypoint.index(age_check) < entrypoint.index('exec "$@"')
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "COPY docker/check_chrome_age.py /app/docker/check_chrome_age.py" in dockerfile
    # Debian slim excludes package changelogs unless explicitly retained.
    keep_date = "path-include=/usr/share/doc/google-chrome-stable/changelog.gz"
    assert dockerfile.index(keep_date) < dockerfile.index(
        "apt-get install -y --no-install-recommends google-chrome-stable"
    )
    assert "test -s /usr/share/doc/google-chrome-stable/changelog.gz" in dockerfile
    for path in (ROOT / "docker/README.md", ROOT / "docs/docker.md"):
        text = path.read_text()
        assert "Docker Engine 28" in text
        assert "docker compose build --pull" in text
        assert "monthly" in text and "45 days" in text


def test_seccomp_supports_modern_libc_without_relaxing_default():
    profile = json.loads((ROOT / "docker/chrome-seccomp.json").read_text())
    calls = {s["name"]: s for s in profile["syscalls"]}
    assert calls["clone3"]["action"] == "SCMP_ACT_ERRNO"
    assert calls["clone3"]["errnoRet"] == 38  # ENOSYS permits glibc's clone fallback.
    assert calls["rseq"]["action"] == "SCMP_ACT_ALLOW"


@pytest.mark.parametrize("file", [
    "frontend/src/lib/components/BrowserLogins.svelte",
    "frontend/src/routes/onboarding/+page.svelte",
])
def test_docker_sign_in_controls_link_to_the_chrome_view(file):
    component = (ROOT / file).read_text()
    assert 'href={browser.view_url}' in component
    assert 'target="_blank"' in component and 'rel="noopener"' in component
    assert "Open the Chrome view" in component
    assert "view_password_hint" in component and "data/chrome-view.password" in component
