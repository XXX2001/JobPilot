"""Warn at entrypoint startup using the installed Chrome package's build date."""

import gzip
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

CHANGELOG = Path("/usr/share/doc/google-chrome-stable/changelog.gz")


def warn_if_stale(path: Path = CHANGELOG, *, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            # Debian's first changelog entry describes the installed version.
            footer = next(line for line in stream if line.startswith(" -- "))
        built = parsedate_to_datetime(footer.rsplit("  ", 1)[1].strip())
        if built.tzinfo is None or built > now:
            raise ValueError("Invalid package build date")
    except (OSError, EOFError, UnicodeError, ValueError, IndexError, StopIteration):
        print(
            "WARNING: Cannot determine the installed Chrome build date. "
            "Rebuild with docker compose build --pull at least monthly; see docker/README.md.",
            file=sys.stderr,
        )
        return
    age = now - built
    if age > timedelta(days=45):
        print(
            f"WARNING: The installed Chrome build is older than 45 days "
            f"({age.days} days; package date {built.date()}). "
            "Rebuild with docker compose build --pull for Chrome security updates; "
            "see docker/README.md for cache refresh instructions.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    warn_if_stale()
