#!/usr/bin/env python3
"""Hot-online SQLite backup for JobPilot.

Creates a timestamped, fully consistent copy of ``data/jobpilot.db`` using
``VACUUM INTO``. Unlike ``cp`` (which races against the WAL writer), this
issues a single SQLite statement that takes an exclusive snapshot — safe to
run while the FastAPI server is live.

Usage
-----

    python scripts/backup_db.py
    python scripts/backup_db.py --out /mnt/backups/
    python scripts/backup_db.py --db /custom/path/jobpilot.db

The output path defaults to ``<data_dir>/backups/jobpilot-<UTC>.db``. Restore
by stopping the app, replacing ``data/jobpilot.db`` (and removing the
``-wal`` / ``-shm`` siblings), and restarting.

Documented in README.md → "Backup & restore".
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("jobpilot.backup")

REPO_ROOT = Path(__file__).resolve().parent.parent


def _default_db_path() -> Path:
    """Resolve the same default as ``backend.config`` without importing it.

    Reads ``JOBPILOT_DATA_DIR`` if set, else falls back to ``<repo>/data``.
    Avoids importing ``backend.config`` so the script also works in
    minimal environments (e.g. on a cron host with only stdlib + sqlite3).
    """
    import os

    raw = os.environ.get("JOBPILOT_DATA_DIR", "./data")
    data_dir = Path(raw)
    if not data_dir.is_absolute():
        data_dir = (REPO_ROOT / data_dir).resolve()
    return data_dir / "jobpilot.db"


def _utc_stamp() -> str:
    # Filesystem-safe ISO-like stamp: 20260524T143000Z
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def backup(db_path: Path, out_dir: Path) -> Path:
    """Write a ``VACUUM INTO`` snapshot of ``db_path`` under ``out_dir``.

    Returns the absolute path of the new backup file. Raises if the source
    DB does not exist or the target directory cannot be created.
    """
    if not db_path.exists():
        raise FileNotFoundError(f"Source DB does not exist: {db_path}")

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"jobpilot-{_utc_stamp()}.db"

    # ``VACUUM INTO`` requires the target to NOT already exist.
    if out_path.exists():
        raise FileExistsError(f"Refusing to overwrite existing backup: {out_path}")

    logger.info("Backing up %s → %s", db_path, out_path)
    # Connect to the live DB. ``isolation_level=None`` puts us in autocommit
    # so ``VACUUM INTO`` is not wrapped in a transaction (sqlite refuses
    # that). ``check_same_thread=False`` is fine — this is a one-shot CLI.
    conn = sqlite3.connect(str(db_path), isolation_level=None, check_same_thread=False)
    try:
        # ``VACUUM INTO`` is atomic and concurrency-safe — see
        # https://www.sqlite.org/lang_vacuum.html. Use a parameter via
        # sqlite3's string escaping rules (it accepts a string literal but
        # NOT a ? placeholder, so quote manually with single quotes and
        # reject any path containing a single quote).
        target = str(out_path)
        if "'" in target:
            raise ValueError(f"Backup path contains a single quote: {target!r}")
        conn.execute(f"VACUUM INTO '{target}'")
    finally:
        conn.close()

    size_kb = out_path.stat().st_size / 1024
    logger.info("Backup complete (%.1f KiB).", size_kb)
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Path to jobpilot.db (default: <JOBPILOT_DATA_DIR>/jobpilot.db).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Directory to write the backup into (default: <db parent>/backups).",
    )
    args = parser.parse_args(argv)

    db_path = (args.db or _default_db_path()).resolve()
    out_dir = (args.out or (db_path.parent / "backups")).resolve()

    try:
        out_path = backup(db_path, out_dir)
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 2
    except Exception as exc:
        logger.error("Backup failed: %s", exc)
        return 1

    # Echo the absolute path on stdout so callers (cron, shell scripts) can
    # capture it.
    print(out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
