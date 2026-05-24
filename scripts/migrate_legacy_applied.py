#!/usr/bin/env python3
"""One-shot data migration: legacy Application.status aliases → canonical.

Background
----------
Before the 2026-05-24 vocabulary consolidation (see
``backend.applier.__init__`` docstring) some strategies persisted
``Application.status`` as the raw strategy outcome — ``"manual"`` or
``"assisted"`` — rather than the canonical ``"applied"``. Read-side
filters paper over this via :data:`LEGACY_APPLIED_ALIASES`, but the long-
term goal is to delete the alias path entirely.

This script does the in-place ``UPDATE``:

    UPDATE applications SET status = 'applied'
     WHERE status IN ('manual', 'assisted');

Run once per deployed environment. Idempotent — re-running on a DB that
has no legacy rows reports ``0 rows`` and exits cleanly.

Usage
-----

    python scripts/migrate_legacy_applied.py
    python scripts/migrate_legacy_applied.py --dry-run
    python scripts/migrate_legacy_applied.py --db /custom/jobpilot.db

Always take a fresh ``python scripts/backup_db.py`` snapshot first.

Removal target
--------------
Once every production DB has been migrated, drop
:data:`LEGACY_APPLIED_ALIASES`, :data:`SUCCESS_STATUSES`'s union with it,
``_expand_status_filter``'s legacy branch in ``backend/api/applications.py``,
and the legacy entries in ``COUNTABLE_STATUSES`` in
``backend/applier/daily_limit.py``. See ``CHANGELOG.md`` fix-sprint 2026-05-24
T9 for tracking.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("jobpilot.migrate_legacy_applied")

REPO_ROOT = Path(__file__).resolve().parent.parent

# Mirror ``backend.applier.LEGACY_APPLIED_ALIASES`` without importing the
# package — keeps the script usable on minimal hosts.
LEGACY_ALIASES = ("manual", "assisted")
CANONICAL = "applied"


def _default_db_path() -> Path:
    import os

    raw = os.environ.get("JOBPILOT_DATA_DIR", "./data")
    data_dir = Path(raw)
    if not data_dir.is_absolute():
        data_dir = (REPO_ROOT / data_dir).resolve()
    return data_dir / "jobpilot.db"


def migrate(db_path: Path, dry_run: bool = False) -> int:
    """Run the UPDATE (or count, if dry-run). Returns the affected-row count."""
    if not db_path.exists():
        raise FileNotFoundError(f"Source DB does not exist: {db_path}")

    placeholders = ",".join("?" * len(LEGACY_ALIASES))
    count_sql = f"SELECT COUNT(*) FROM applications WHERE status IN ({placeholders})"
    update_sql = (
        f"UPDATE applications SET status = ? WHERE status IN ({placeholders})"
    )

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(count_sql, LEGACY_ALIASES)
        legacy_count = cur.fetchone()[0]
        logger.info(
            "Found %d application row(s) with legacy status (%s).",
            legacy_count,
            ", ".join(repr(a) for a in LEGACY_ALIASES),
        )

        if legacy_count == 0:
            logger.info("Nothing to migrate.")
            return 0

        if dry_run:
            logger.info("Dry-run: skipping UPDATE.")
            return legacy_count

        with conn:
            cur = conn.execute(update_sql, (CANONICAL, *LEGACY_ALIASES))
            updated = cur.rowcount
        logger.info("Migrated %d row(s) to status=%r.", updated, CANONICAL)
        return updated
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=None, help="Path to jobpilot.db.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report how many rows would change, but do not write.",
    )
    args = parser.parse_args(argv)

    db_path = (args.db or _default_db_path()).resolve()
    try:
        migrate(db_path, dry_run=args.dry_run)
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 2
    except Exception as exc:
        logger.error("Migration failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
