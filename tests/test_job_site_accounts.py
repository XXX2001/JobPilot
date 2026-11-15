"""Shared job-site password, account registry, and migration contracts."""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import logging
import time
import traceback
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import create_engine, inspect, select, text

from backend.config import PROJECT_ROOT, Settings, settings
from backend.database import db_session
from backend.models.user import SiteCredential, UserProfile

PASSWORD = "shared-password-test-only!"
PASSWORD_URL = "/api/settings/job-site-password"
ACCOUNTS_URL = "/api/settings/job-site-accounts"


def accounts_module():
    assert importlib.util.find_spec("backend.applier.harness.accounts") is not None, (
        "The job-site account service is not implemented"
    )
    return importlib.import_module("backend.applier.harness.accounts")


@pytest.fixture(autouse=True)
def isolated_credentials(monkeypatch):
    monkeypatch.setattr(settings, "CREDENTIAL_KEY", SecretStr(Fernet.generate_key().decode()))
    monkeypatch.setitem(settings.__dict__, "JOB_SITE_PASSWORD", SecretStr(""))


async def profile_row():
    async with db_session() as db:
        return await db.get(UserProfile, 1)


@pytest.mark.parametrize(("url", "expected"), [
    ("https://career2.successfactors.eu/careers?company=fanuceurop", "career2.successfactors.eu?company=fanuceurop"),
    ("https://CAREER2.SuccessFactors.EU/careers?lang=fr&company=Fanuc", "career2.successfactors.eu?company=fanuc"),
    ("https://acme.wd3.myworkdayjobs.com/x", "acme.wd3.myworkdayjobs.com"),
    ("https://Acme.myworkday.com/careers", "acme.myworkday.com"),
    ("https://jobs.lever.co/acme", "jobs.lever.co"),
    ("https://jobs.lever.co/acme?company=ignored", "jobs.lever.co"),
    ("https://career.successfactors.com.evil.example/?company=ignored", "career.successfactors.com.evil.example"),
])
def test_site_key_keeps_only_relevant_tenant(url, expected):
    assert accounts_module().site_key(url) == expected


@pytest.mark.parametrize("host", [
    "career2.successfactors.eu", "career.successfactors.com",
    "career2.sapsf.eu", "performancemanager4.successfactors.com", "career.sapsf.com",
])
@pytest.mark.parametrize("parameter", ["company", "Company", "COMPANY", "cOmPaNy"])
def test_site_key_sap_hosts_keep_case_insensitive_company(host, parameter):
    assert accounts_module().site_key(
        f"https://{host}/careers?lang=fr&{parameter}=Fanuc"
    ) == f"{host}?company=fanuc"


@pytest.mark.parametrize("host", ["career.sapsf.eu.evil.example", "notsapsf.com"])
def test_site_key_ignores_company_on_unrelated_hosts(host):
    assert accounts_module().site_key(f"https://{host}/?Company=Fanuc") == host


@pytest.mark.parametrize("host", ["BÜCHER.example", "xn--bcher-kva.example"])
def test_site_key_normalizes_idn_hosts(host):
    assert accounts_module().site_key(f"https://{host}/careers") == "xn--bcher-kva.example"


@pytest.mark.parametrize("url", [
    "", " ", "/careers?company=Fanuc", "../careers", "careers",
    "jobs.lever.co/acme", "//jobs.lever.co/acme", "https:///careers", "https://",
    "https://[broken/careers", "https://jobs.example:invalid/careers",
    "https://jobs.example:99999/careers", "https://bad host.example/careers",
    "https://jobs\n.example/careers", "https://jobs..example/careers",
    "mailto:ada@example.com",
])
def test_site_key_rejects_invalid_or_relative_urls(url):
    with pytest.raises(ValueError):
        accounts_module().site_key(url)


def test_password_put_get_encrypts_and_never_returns_secret(test_app, caplog):
    with caplog.at_level(logging.DEBUG):
        saved = test_app.put(PASSWORD_URL, json={"password": PASSWORD})
        status = test_app.get(PASSWORD_URL)
        profile = test_app.get("/api/settings/profile")
    assert saved.status_code == status.status_code == 200
    assert saved.json() == status.json() == {"set": True, "source": "app"}
    row = asyncio.run(profile_row())
    assert row is not None
    encrypted = row.encrypted_job_site_password
    assert encrypted and encrypted != PASSWORD
    assert Fernet(settings.CREDENTIAL_KEY.get_secret_value().encode()).decrypt(encrypted.encode()).decode() == PASSWORD
    assert asyncio.run(accounts_module().job_site_password()) == PASSWORD
    assert PASSWORD not in saved.text + status.text + profile.text + caplog.text + repr(row)
    assert "encrypted_job_site_password" not in profile.json()


def test_env_password_is_masked_and_used_as_fallback(test_app, monkeypatch, caplog):
    monkeypatch.setenv("JOB_SITE_PASSWORD", PASSWORD)
    config = Settings()
    assert isinstance(getattr(config, "JOB_SITE_PASSWORD", None), SecretStr)
    assert PASSWORD not in repr(config) + str(config)
    monkeypatch.setitem(settings.__dict__, "JOB_SITE_PASSWORD", config.JOB_SITE_PASSWORD)
    response = test_app.get(PASSWORD_URL)
    assert response.status_code == 200
    assert response.json() == {"set": True, "source": "env"}
    assert asyncio.run(accounts_module().job_site_password()) == PASSWORD
    assert PASSWORD not in response.text + caplog.text


def test_app_password_takes_precedence_and_clear_restores_env(test_app, monkeypatch):
    monkeypatch.setitem(settings.__dict__, "JOB_SITE_PASSWORD", SecretStr("environment-only-password"))
    assert test_app.put(PASSWORD_URL, json={"password": PASSWORD}).json() == {"set": True, "source": "app"}
    assert asyncio.run(accounts_module().job_site_password()) == PASSWORD
    assert test_app.put(PASSWORD_URL, json={"password": ""}).json() == {"set": True, "source": "env"}
    assert asyncio.run(profile_row()).encrypted_job_site_password is None
    assert asyncio.run(accounts_module().job_site_password()) == "environment-only-password"


def test_unset_password_and_clear_without_profile(test_app):
    response = test_app.get(PASSWORD_URL)
    assert response.status_code == 200
    assert response.json() == {"set": False, "source": None}
    assert asyncio.run(accounts_module().job_site_password()) is None
    assert test_app.put(PASSWORD_URL, json={"password": ""}).json() == {"set": False, "source": None}


def test_password_save_creates_profile_and_preserves_profile_fields(test_app):
    service = accounts_module()
    asyncio.run(service.set_job_site_password(PASSWORD))
    row = asyncio.run(profile_row())
    assert row is not None and row.id == 1 and row.full_name == row.email == ""
    assert test_app.put("/api/settings/profile", json={"full_name": "Ada", "email": "ada@example.com"}).status_code == 200
    asyncio.run(service.set_job_site_password("replacement-password"))
    row = asyncio.run(profile_row())
    assert (row.full_name, row.email) == ("Ada", "ada@example.com")
    assert asyncio.run(service.job_site_password()) == "replacement-password"


@pytest.mark.parametrize("password", ["eight888", "x" * 128], ids=["minimum", "maximum"])
def test_password_length_boundaries_are_accepted(test_app, password):
    response = test_app.put(PASSWORD_URL, json={"password": password})
    assert response.status_code == 200
    assert response.json() == {"set": True, "source": "app"}


@pytest.mark.parametrize(("body", "marker"), [
    ({"password": "secret!"}, "secret!"),
    ({"password": "long-secret" * 13}, "long-secret"),
    ({"password": {"secret": PASSWORD}}, PASSWORD),
    ({"password": [PASSWORD]}, PASSWORD),
    ({"password": None}, None),
    ({"password": 123456789}, "123456789"),
    ({"wrong": PASSWORD}, PASSWORD),
    ([PASSWORD], PASSWORD),
    (PASSWORD, PASSWORD),
], ids=["too-short", "too-long", "object", "array", "null", "number", "missing", "body-array", "body-string"])
def test_invalid_password_requests_are_rejected_without_echo(test_app, caplog, body, marker):
    with caplog.at_level(logging.DEBUG):
        response = test_app.put(PASSWORD_URL, json=body)
    assert response.status_code == 422
    if marker:
        assert marker not in response.text + caplog.text + repr(caplog.records)
    assert asyncio.run(profile_row()) is None


def test_malformed_json_never_echoes_password(test_app, caplog):
    with caplog.at_level(logging.DEBUG):
        response = test_app.put(PASSWORD_URL, content='{"password": "' + PASSWORD, headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert PASSWORD not in response.text + caplog.text + repr(caplog.records)


def test_password_model_repr_masks_secret():
    from backend.api import settings as settings_api

    model = getattr(settings_api, "JobSitePasswordUpdate", None)
    assert model is not None
    request = model(password=PASSWORD)
    assert PASSWORD not in repr(request) + str(request)


def test_credential_failure_does_not_expose_password_or_exception_chain(test_app, monkeypatch, caplog):
    service = accounts_module()

    def broken_fernet():
        raise RuntimeError(PASSWORD)

    monkeypatch.setattr(service, "_fernet", broken_fernet)
    with pytest.raises(RuntimeError) as raised:
        asyncio.run(service.set_job_site_password(PASSWORD))
    assert PASSWORD not in "".join(traceback.format_exception(raised.value))
    with caplog.at_level(logging.DEBUG):
        response = test_app.put(PASSWORD_URL, json={"password": PASSWORD})
    assert response.status_code == 500
    assert PASSWORD not in response.text + caplog.text + repr(caplog.records)


def test_corrupt_stored_password_fails_safely_without_env_fallback(test_app, monkeypatch, caplog):
    service = accounts_module()
    asyncio.run(service.set_job_site_password(PASSWORD))

    async def corrupt():
        async with db_session() as db:
            row = await db.get(UserProfile, 1)
            row.encrypted_job_site_password = "invalid-ciphertext"

    asyncio.run(corrupt())
    monkeypatch.setitem(settings.__dict__, "JOB_SITE_PASSWORD", SecretStr("different-env-password"))
    with pytest.raises(RuntimeError) as raised:
        asyncio.run(service.job_site_password())
    assert PASSWORD not in "".join(traceback.format_exception(raised.value)) + caplog.text


def test_account_record_find_upsert_encrypts_email_and_keeps_creation_time(caplog):
    service = accounts_module()
    key = "career2.successfactors.eu?company=fanuceurop"

    async def exercise():
        assert await service.find_account(key) is None
        await service.record_account(key, method="password", email="ada@example.com", status="created", login_url="https://career2.successfactors.eu/careers?company=fanuceurop", note="Terms accepted")
        first = await service.find_account(key)
        assert first is not None
        assert first.key == key and first.email == "ada@example.com" and first.method == "password"
        assert first.status == "created" and first.note == "Terms accepted"
        assert first.created_at is not None and first.last_used_at is not None
        async with db_session() as db:
            stored = (await db.execute(select(SiteCredential))).scalar_one()
            assert stored.encrypted_email != first.email
            assert Fernet(settings.CREDENTIAL_KEY.get_secret_value().encode()).decrypt(stored.encrypted_email.encode()).decode() == first.email
            assert not stored.encrypted_password
        await service.record_account(key, method="google", email="other@example.com", status="signed_in", login_url="https://career2.successfactors.eu/login")
        second = await service.find_account(key)
        assert second.created_at == first.created_at
        assert second.last_used_at >= first.last_used_at
        assert second.email == "other@example.com" and second.status == "signed_in" and second.method == "google"
        assert set(asdict(second)) == {"key", "method", "email", "status", "login_url", "created_at", "last_used_at", "note"}
        assert PASSWORD not in repr(second) + caplog.text
    asyncio.run(exercise())


def test_concurrent_first_records_share_one_row_and_preserve_creation(monkeypatch):
    service = accounts_module()
    created = datetime(2026, 11, 15, 12)
    monkeypatch.setattr(service, "naive_utc_now", lambda: created)
    keys = [f"tenant-{index}.myworkdayjobs.com" for index in range(5)]

    async def exercise():
        results = await asyncio.gather(*(
            service.record_account(
                key, method="password", email="ada@example.com", status=status,
                login_url=f"https://{key}/login",
            )
            for key in keys for status in ("created", "signed_in")
        ), return_exceptions=True)
        assert results == [None] * (len(keys) * 2)
        async with db_session() as db:
            rows = (await db.execute(select(SiteCredential))).scalars().all()
            assert len(rows) == len(keys)
            assert all(row.created_at == created and not row.encrypted_password for row in rows)

        later = created + timedelta(days=1)
        monkeypatch.setattr(service, "naive_utc_now", lambda: later)
        for key in keys:
            await service.record_account(
                key, method="google", email="ada@example.com", status="verified",
                login_url=f"https://{key}/login", note="Verified later",
            )
            account = await service.find_account(key)
            assert account.created_at == created
            assert account.last_used_at == later
            assert account.method == "google" and account.status == "verified"
            assert account.note == "Verified later"

    asyncio.run(exercise())


def test_account_list_and_encoded_delete_exclude_password_and_legacy(test_app):
    service = accounts_module()
    key = "career2.successfactors.eu?company=fanuceurop"

    async def seed():
        await service.set_job_site_password(PASSWORD)
        await service.record_account(key, method="google", email="ada@example.com", status="verified", login_url="https://career2.successfactors.eu/?company=fanuceurop")
        async with db_session() as db:
            db.add(SiteCredential(site_name="legacy.example", encrypted_email="old-email", encrypted_password="old-password"))
        assert await service.find_account("legacy.example") is None
    asyncio.run(seed())
    response = test_app.get(ACCOUNTS_URL)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1 and data[0]["key"] == key
    assert data[0]["email"] == "ada@example.com" and data[0]["method"] == "google"
    assert "password" not in data[0] and "encrypted_password" not in data[0]
    assert PASSWORD not in response.text
    deleted = test_app.delete(f"{ACCOUNTS_URL}/{quote(key, safe='')}")
    assert deleted.status_code == 204 and not deleted.content
    assert test_app.get(ACCOUNTS_URL).json() == []
    assert test_app.delete(f"{ACCOUNTS_URL}/legacy.example").status_code == 404
    async def legacy_exists():
        async with db_session() as db:
            return (await db.execute(select(SiteCredential))).scalar_one().encrypted_password
    assert asyncio.run(legacy_exists()) == "old-password"


@pytest.mark.parametrize("damage", ["corrupt", "different-key", "invalid-utf8"])
def test_account_with_unreadable_email_still_lists_and_can_be_deleted(test_app, damage):
    service = accounts_module()
    broken_key = "career.sapsf.eu?company=fanuc"
    healthy_key = "healthy.example"

    async def seed():
        for key in (broken_key, healthy_key):
            await service.record_account(
                key, method="google", email="ada@example.com", status="signed_in",
                login_url=f"https://{key}",
            )
        async with db_session() as db:
            row = (await db.execute(select(SiteCredential).where(
                SiteCredential.site_name == broken_key
            ))).scalar_one()
            if damage == "different-key":
                row.encrypted_email = Fernet(Fernet.generate_key()).encrypt(b"ada@example.com").decode()
            elif damage == "invalid-utf8":
                cipher = Fernet(settings.CREDENTIAL_KEY.get_secret_value().encode())
                row.encrypted_email = cipher.encrypt(b"\xff").decode()
            else:
                row.encrypted_email = "invalid-ciphertext"

    asyncio.run(seed())
    response = test_app.get(ACCOUNTS_URL)
    assert response.status_code == 200
    rows = {row["key"]: row for row in response.json()}
    assert set(rows) == {broken_key, healthy_key}
    assert rows[broken_key]["email"] == "***@***"
    assert rows[broken_key]["status"] == "signed_in"
    assert rows[healthy_key]["email"] == "ada@example.com"
    assert asyncio.run(service.find_account(broken_key)).email == "***@***"
    deleted = test_app.delete(f"{ACCOUNTS_URL}/{quote(broken_key, safe='')}")
    assert deleted.status_code == 204 and not deleted.content
    assert [row["key"] for row in test_app.get(ACCOUNTS_URL).json()] == [healthy_key]


@pytest.mark.parametrize("now", [
    datetime(2026, 11, 15, 23, 30),  # Paris has already crossed into tomorrow.
    datetime(2026, 3, 29, 12),  # The local day is 23 hours long.
    datetime(2026, 10, 25, 12),  # The local day is 25 hours long.
])
def test_new_accounts_today_uses_local_creation_day_not_update_day(monkeypatch, now):
    service = accounts_module()
    monkeypatch.setenv("TZ", "Europe/Paris")
    time.tzset()
    try:
        monkeypatch.setattr(service, "naive_utc_now", lambda: now)
        local_date = now.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("Europe/Paris")).date()
        local_start = datetime.combine(local_date, datetime.min.time(), tzinfo=ZoneInfo("Europe/Paris"))
        start = local_start.astimezone(timezone.utc).replace(tzinfo=None)
        end = (local_start + timedelta(days=1)).astimezone(timezone.utc).replace(tzinfo=None)

        async def exercise():
            async with db_session() as db:
                for index, created in enumerate([start - timedelta(microseconds=1), start, end - timedelta(microseconds=1), end]):
                    db.add(SiteCredential(site_name=f"row-{index}.example", method="password", status="failed" if index == 1 else "signed_in", created_at=created, updated_at=now))
                db.add(SiteCredential(site_name="legacy.example", created_at=now, updated_at=now))
            await service.record_account("row-0.example", method="password", email="ada@example.com", status="verified", login_url="https://row-0.example")
            assert await service.new_accounts_today() == 2
        asyncio.run(exercise())
    finally:
        monkeypatch.undo()
        time.tzset()


def test_migration_preserves_legacy_rows_and_downgrades(tmp_path):
    assert (PROJECT_ROOT / "alembic/versions/a01_job_site_accounts.py").is_file()
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    db_file = tmp_path / "accounts.db"
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_file}")
    command.upgrade(cfg, "m04_match_score_flags")
    engine = create_engine(f"sqlite:///{db_file}")
    try:
        with engine.begin() as db:
            db.execute(text("INSERT INTO site_credentials (site_name, encrypted_email, encrypted_password, created_at, updated_at) VALUES ('legacy', 'email-ciphertext', 'password-ciphertext', '2026-01-01', '2026-01-02')"))
            db.execute(text("INSERT INTO user_profile (id, full_name, email, created_at, updated_at) VALUES (1, 'Ada', 'ada@example.com', '2026-01-01', '2026-01-02')"))
            old = db.execute(text("SELECT * FROM site_credentials")).mappings().one()
        command.upgrade(cfg, "a01_job_site_accounts")
        with engine.connect() as db:
            migrated = db.execute(text("SELECT * FROM site_credentials")).mappings().one()
            assert {name: migrated[name] for name in old} == dict(old)
            assert all(migrated[name] is None for name in ("method", "status", "login_url", "last_used_at", "note"))
            assert db.execute(text("SELECT encrypted_job_site_password FROM user_profile")).scalar_one() is None
        command.downgrade(cfg, "m04_match_score_flags")
        with engine.connect() as db:
            assert dict(db.execute(text("SELECT * FROM site_credentials")).mappings().one()) == dict(old)
        assert "method" not in {column["name"] for column in inspect(engine).get_columns("site_credentials")}
        assert "encrypted_job_site_password" not in {column["name"] for column in inspect(engine).get_columns("user_profile")}
    finally:
        engine.dispose()
