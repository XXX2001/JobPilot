"""T9 regression tests — ops + dead-code purge.

Pins:
- ``backend.latex.validator`` is gone (module no longer importable).
- ``LaTeXInjector.inject_summary_edit`` / ``inject_experience_edits`` are gone.
- ``backend.latex.pipeline.generate_diff`` is gone.
- ``backend.api.deps`` no longer exports the 5 unused getter functions.
- ``scripts/backup_db.py`` produces a readable timestamped copy.
- ``start.py`` honors ``JOBPILOT_HOST`` / ``JOBPILOT_PORT`` env vars.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


# ─── Dead-code purge ──────────────────────────────────────────────────────────


def test_latex_validator_module_removed() -> None:
    """``backend.latex.validator`` was deleted — importing it must fail."""
    with pytest.raises(ModuleNotFoundError):
        __import__("backend.latex.validator")


def test_latex_injector_dead_methods_removed() -> None:
    """``inject_summary_edit`` and ``inject_experience_edits`` are gone."""
    from backend.latex.injector import LaTeXInjector

    assert not hasattr(LaTeXInjector, "inject_summary_edit")
    assert not hasattr(LaTeXInjector, "inject_experience_edits")
    # The live method must still exist.
    assert hasattr(LaTeXInjector, "inject_letter_edit")


def test_pipeline_generate_diff_removed() -> None:
    """``backend.latex.pipeline.generate_diff`` is gone."""
    import backend.latex.pipeline as pipeline_mod

    assert not hasattr(pipeline_mod, "generate_diff")


def test_deps_singleton_getters_removed() -> None:
    """The 5 never-called dependency getters are gone from ``backend.api.deps``."""
    import backend.api.deps as deps_mod

    for name in (
        "get_session_manager",
        "get_apply_engine",
        "get_cv_pipeline",
        "get_scraping_orchestrator",
        "get_batch_runner",
    ):
        assert not hasattr(deps_mod, name), f"{name} should have been removed in T9"

    # DBSession alias must remain — it is the only public symbol.
    assert hasattr(deps_mod, "DBSession")


# ─── Backup script ────────────────────────────────────────────────────────────


def test_backup_db_creates_readable_copy(tmp_path: Path) -> None:
    """``scripts/backup_db.py`` produces a SQLite snapshot that opens cleanly."""
    src_db = tmp_path / "jobpilot.db"
    src_conn = sqlite3.connect(str(src_db))
    src_conn.executescript(
        """
        CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT);
        INSERT INTO widgets (name) VALUES ('alpha'), ('beta'), ('gamma');
        """
    )
    src_conn.commit()
    src_conn.close()

    out_dir = tmp_path / "backups"

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "backup_db.py"),
            "--db",
            str(src_db),
            "--out",
            str(out_dir),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    snapshot_line = result.stdout.strip().splitlines()[-1]
    snapshot_path = Path(snapshot_line)
    assert snapshot_path.exists(), f"Backup file not created: {result.stdout!r}"
    assert snapshot_path.parent == out_dir
    assert snapshot_path.name.startswith("jobpilot-") and snapshot_path.suffix == ".db"

    # The snapshot must be a valid SQLite DB with the same data.
    snap_conn = sqlite3.connect(str(snapshot_path))
    try:
        rows = snap_conn.execute("SELECT name FROM widgets ORDER BY id").fetchall()
    finally:
        snap_conn.close()
    assert rows == [("alpha",), ("beta",), ("gamma",)]


def test_backup_db_missing_source_returns_nonzero(tmp_path: Path) -> None:
    """Missing source DB → non-zero exit, no crash."""
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "backup_db.py"),
            "--db",
            str(tmp_path / "does-not-exist.db"),
            "--out",
            str(tmp_path / "backups"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0


# ─── start.py env-var honouring ───────────────────────────────────────────────


def test_start_py_reads_jobpilot_host_and_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """``start.main`` resolves JOBPILOT_HOST / JOBPILOT_PORT before binding.

    Verified by patching out the heavy bits (``check_prerequisites``,
    ``free_port``, the browser timer) and ``uvicorn.run`` to capture the
    host/port it would have used.
    """
    # Make sure start.py is importable even though it lives at the repo root.
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    import importlib

    start_mod = importlib.import_module("start")

    monkeypatch.setenv("JOBPILOT_HOST", "0.0.0.0")
    monkeypatch.setenv("JOBPILOT_PORT", "9000")

    captured: dict[str, object] = {}

    def _fake_uvicorn_run(_app: str, **kwargs):  # noqa: ANN001
        captured.update(kwargs)

    def _noop(*_a, **_kw):
        return None

    class _FakeTimer:
        def __init__(self, *_a, **_kw) -> None:
            pass

        def start(self) -> None:
            pass

    monkeypatch.setattr(start_mod, "uvicorn", type("U", (), {"run": _fake_uvicorn_run}))
    monkeypatch.setattr(start_mod, "check_prerequisites", _noop)
    monkeypatch.setattr(start_mod, "free_port", _noop)
    monkeypatch.setattr("threading.Timer", _FakeTimer)

    start_mod.main()

    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 9000


def test_start_py_invalid_port_falls_back_to_8000(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Garbage in JOBPILOT_PORT must not crash — fall back to 8000."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    import importlib

    start_mod = importlib.import_module("start")

    monkeypatch.setenv("JOBPILOT_PORT", "not-a-number")
    monkeypatch.delenv("JOBPILOT_HOST", raising=False)

    captured: dict[str, object] = {}

    def _fake_uvicorn_run(_app: str, **kwargs):  # noqa: ANN001
        captured.update(kwargs)

    def _noop(*_a, **_kw):
        return None

    class _FakeTimer:
        def __init__(self, *_a, **_kw) -> None:
            pass

        def start(self) -> None:
            pass

    monkeypatch.setattr(start_mod, "uvicorn", type("U", (), {"run": _fake_uvicorn_run}))
    monkeypatch.setattr(start_mod, "check_prerequisites", _noop)
    monkeypatch.setattr(start_mod, "free_port", _noop)
    monkeypatch.setattr("threading.Timer", _FakeTimer)

    start_mod.main()

    assert captured["port"] == 8000
    assert captured["host"] == "127.0.0.1"


# ─── Legacy-applied data migration ────────────────────────────────────────────


def test_migrate_legacy_applied_updates_rows(tmp_path: Path) -> None:
    """The migration UPDATEs legacy alias rows to 'applied' and is idempotent."""
    db = tmp_path / "jobpilot.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE applications (id INTEGER PRIMARY KEY, status TEXT);
        INSERT INTO applications (status) VALUES
            ('manual'), ('assisted'), ('applied'), ('pending'), ('manual');
        """
    )
    conn.commit()
    conn.close()

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "migrate_legacy_applied.py"),
            "--db",
            str(db),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    # 3 rows were legacy ('manual', 'assisted', 'manual').
    assert "Migrated 3" in result.stderr or "Migrated 3" in result.stdout

    conn = sqlite3.connect(str(db))
    try:
        rows = sorted(r[0] for r in conn.execute("SELECT status FROM applications"))
    finally:
        conn.close()
    assert rows == ["applied", "applied", "applied", "applied", "pending"]

    # Idempotent — running again should report 0 and exit cleanly.
    result2 = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "migrate_legacy_applied.py"),
            "--db",
            str(db),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Nothing to migrate" in (result2.stderr + result2.stdout)
