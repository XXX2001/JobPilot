"""Encrypted job-site credentials and the password-free account registry."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert

from backend.config import settings
from backend.database import db_session
from backend.gmail.credentials import _fernet
from backend.models.user import SiteCredential, UserProfile
from backend.utils.time import naive_utc_now


@dataclass(frozen=True)
class AccountRow:
    key: str
    method: str
    email: str
    status: str
    login_url: str
    created_at: datetime
    last_used_at: datetime | None
    note: str


def site_key(url: str) -> str:
    """Key an absolute HTTP(S) URL by ASCII host and SuccessFactors tenant."""
    try:
        # urlsplit otherwise silently strips some whitespace/control characters.
        if any(char.isspace() or ord(char) < 32 for char in url):
            raise ValueError
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError
        host = parsed.hostname.encode("idna").decode("ascii").lower()
        if not re.fullmatch(r"[a-z0-9.:-]+", host):
            raise ValueError
        # Accessing port validates malformed/out-of-range ports, even though the
        # registry key intentionally omits the port.
        _ = parsed.port
    except ValueError:
        raise ValueError("Invalid job-site URL") from None

    if re.search(
        r"(?:^|\.)(?:successfactors\.(?:[a-z]{2,3}|com\.[a-z]{2})|sapsf\.(?:eu|com))$",
        host,
    ):
        company = next((
            value.lower() for name, value in parse_qsl(parsed.query)
            if name.lower() == "company"
        ), "")
        if company:
            return host + "?" + urlencode({"company": company})
    # Workday's tenant subdomain is deliberately retained with the full host.
    return host


async def job_site_password() -> str | None:
    """Read the app password first; a damaged app credential fails closed."""
    try:
        async with db_session() as db:
            profile = await db.get(UserProfile, 1)
            if profile is not None and profile.encrypted_job_site_password:
                return _fernet().decrypt(profile.encrypted_job_site_password.encode()).decode()
        return settings.JOB_SITE_PASSWORD.get_secret_value() or None
    except Exception:
        # Crypto/provider exception text may contain input. Do not chain it into
        # the global API exception logger, the harness, or caller tracebacks.
        raise RuntimeError("Could not read the job-site password") from None


async def set_job_site_password(value: str) -> None:
    """Save the shared password separately from per-site registry rows."""
    try:
        encrypted = _fernet().encrypt(value.encode()).decode() if value else None
        async with db_session() as db:
            profile = await db.get(UserProfile, 1)
            if profile is None:
                profile = UserProfile(id=1, full_name="", email="")
                db.add(profile)
            profile.encrypted_job_site_password = encrypted
            profile.updated_at = naive_utc_now()
    except Exception:
        raise RuntimeError("Could not save the job-site password") from None


def _account_row(row: SiteCredential) -> AccountRow:
    """Project only registry fields; the legacy password is never decrypted."""
    try:
        email = _fernet().decrypt(row.encrypted_email.encode()).decode() if row.encrypted_email else ""
    except Exception:
        # A damaged credential must remain visible so the user can remove it.
        email = "***@***"
    return AccountRow(
        key=row.site_name,
        method=row.method or "",
        email=email,
        status=row.status or "",
        login_url=row.login_url or "",
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        note=row.note or "",
    )


async def find_account(key: str) -> AccountRow | None:
    try:
        async with db_session() as db:
            row = (await db.execute(select(SiteCredential).where(
                SiteCredential.site_name == key, SiteCredential.method.is_not(None)
            ))).scalar_one_or_none()
            return _account_row(row) if row is not None else None
    except Exception:
        raise RuntimeError("Could not read the job-site account") from None


async def list_accounts() -> list[AccountRow]:
    try:
        async with db_session() as db:
            rows = (await db.execute(select(SiteCredential).where(
                SiteCredential.method.is_not(None)
            ).order_by(SiteCredential.created_at.desc(), SiteCredential.site_name))).scalars()
            return [_account_row(row) for row in rows]
    except Exception:
        raise RuntimeError("Could not read the job-site accounts") from None


async def record_account(
    key: str,
    *,
    method: Literal["google", "password"],
    email: str,
    status: str,
    login_url: str,
    note: str = "",
) -> None:
    try:
        encrypted_email = _fernet().encrypt(email.encode()).decode()
        now = naive_utc_now()
        updates = {
            "encrypted_email": encrypted_email,
            "encrypted_password": None,
            "method": method,
            "status": status,
            "login_url": login_url,
            "last_used_at": now,
            "updated_at": now,
            "note": note,
        }
        async with db_session() as db:
            # Two applications can encounter the same tenant concurrently.
            # Let the unique key arbitrate atomically, retaining its first
            # created_at so later sign-ins do not consume today's quota again.
            await db.execute(
                insert(SiteCredential)
                .values(site_name=key, created_at=now, **updates)
                .on_conflict_do_update(index_elements=["site_name"], set_=updates)
            )
    except Exception:
        raise RuntimeError("Could not save the job-site account") from None


async def new_accounts_today() -> int:
    """Count newly recorded accounts, including failed attempts, on the local day."""
    local_date = naive_utc_now().replace(tzinfo=timezone.utc).astimezone().date()
    midnight = datetime.combine(local_date, datetime.min.time())
    # Convert each naive local midnight independently: DST days are 23/25 hours.
    start = midnight.astimezone(timezone.utc).replace(tzinfo=None)
    end = (midnight + timedelta(days=1)).astimezone(timezone.utc).replace(tzinfo=None)
    async with db_session() as db:
        count = await db.scalar(select(func.count()).select_from(SiteCredential).where(
            SiteCredential.method.is_not(None),
            SiteCredential.created_at >= start,
            SiteCredential.created_at < end,
        ))
        return count or 0
