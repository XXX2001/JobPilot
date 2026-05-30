import sys
from pathlib import Path

from pydantic import Field, SecretStr, ValidationError  # type: ignore
from pydantic_settings import (
    BaseSettings,  # type: ignore
    SettingsConfigDict,  # type: ignore
)


class Settings(BaseSettings):
    """Application settings loaded from environment or .env file."""

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)

    # Required secrets (no defaults)
    GOOGLE_API_KEY: SecretStr
    ADZUNA_APP_ID: str  # public app id (shown masked in UI but not a secret)
    ADZUNA_APP_KEY: SecretStr

    # Optional
    SERPAPI_KEY: SecretStr = SecretStr("")
    CREDENTIAL_KEY: SecretStr = SecretStr("")  # Fernet key for encrypting stored credentials

    # App settings with sensible defaults
    jobpilot_host: str = Field("127.0.0.1", env="JOBPILOT_HOST")
    jobpilot_port: int = Field(8000, env="JOBPILOT_PORT")
    jobpilot_log_level: str = Field("info", env="JOBPILOT_LOG_LEVEL")
    jobpilot_scraper_headless: bool = Field(True, env="JOBPILOT_SCRAPER_HEADLESS")
    jobpilot_data_dir: str = Field("./data", env="JOBPILOT_DATA_DIR")
    # Comma-separated list of allowed CORS origins. Default = local dev hosts.
    jobpilot_allowed_origins: str = Field(
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000",
        env="JOBPILOT_ALLOWED_ORIGINS",
    )
    # Google / Gemini model settings
    # Primary model name (Gemini 3 Flash Preview — newest, most intelligent flash)
    GOOGLE_MODEL: str = Field("gemini-3-flash-preview", env="GOOGLE_MODEL")
    # Comma-separated fallback model names (empty => no fallbacks)
    GOOGLE_MODEL_FALLBACKS: str = Field("", env="GOOGLE_MODEL_FALLBACKS")
    # Feature flag: enable Tier 1 Scrapling fetcher (HTTP + single LLM call)
    SCRAPLING_ENABLED: bool = Field(True, env="SCRAPLING_ENABLED")
    # Feature flag: enable Tier 1 Playwright direct filler (mirrors SCRAPLING_ENABLED)
    APPLY_TIER1_ENABLED: bool = Field(True, env="APPLY_TIER1_ENABLED")

    # Timeouts (seconds) — fail loudly instead of hanging forever.
    # (Field name already maps to the env var; no need for a deprecated env=.)
    TECTONIC_TIMEOUT_SECONDS: float = 60.0
    GEMINI_TIMEOUT_SECONDS: float = 45.0

    # ── Gmail integration (Phase 1) ──────────────────────────────────────
    GMAIL_CLIENT_ID: str = Field("", env="GMAIL_CLIENT_ID")
    GMAIL_CLIENT_SECRET: SecretStr = SecretStr("")
    GMAIL_REDIRECT_URI: str = Field(
        "http://localhost:8000/api/gmail/oauth/callback",
        env="GMAIL_REDIRECT_URI",
    )
    GMAIL_BACKFILL_DAYS: int = Field(30, env="GMAIL_BACKFILL_DAYS")
    GMAIL_POLL_INTERVAL_MINUTES: int = Field(5, env="GMAIL_POLL_INTERVAL_MINUTES")

    def is_configured(self, field_name: str) -> bool:
        """Return True if *field_name* holds a real, non-placeholder value.

        Centralises the "is this credential set?" check so callers don't
        have to know whether the underlying attribute is a plain ``str`` or
        a ``SecretStr`` (a SecretStr instance is never equal to a plain
        string, so naive ``value not in ("", "placeholder")`` comparisons
        always return True — masking missing credentials).
        """
        raw = getattr(self, field_name, None)
        if raw is None:
            return False
        if hasattr(raw, "get_secret_value"):
            try:
                raw = raw.get_secret_value()
            except Exception:
                return False
        if not isinstance(raw, str):
            # Unexpected non-string scalar — treat truthy as configured.
            return bool(raw)
        return raw not in ("", "placeholder")


def _load_settings() -> "Settings":
    """Instantiate Settings, printing a friendly hint on validation failure.

    Pydantic's default ValidationError dump is intimidating for a first-time
    user who just forgot to fill in their .env. Trade it for a concise
    "missing X" banner plus a pointer to .env.example, then exit non-zero
    so the launcher script (and Docker healthcheck) can detect the failure.
    """
    try:
        return Settings()
    except ValidationError as exc:
        missing = sorted({
            ".".join(str(p) for p in e.get("loc", ()))
            for e in exc.errors()
            if e.get("type") == "missing"
        })
        sys.stderr.write("\nJobPilot configuration error\n")
        sys.stderr.write("─" * 32 + "\n")
        if missing:
            sys.stderr.write(
                "The following required environment variables are not set:\n"
            )
            for name in missing:
                sys.stderr.write(f"  • {name}\n")
            sys.stderr.write(
                "\nCopy .env.example to .env and fill them in, then re-run.\n"
            )
        else:
            sys.stderr.write(f"{exc}\n")
        sys.exit(1)


settings = _load_settings()

# Auto-generate CREDENTIAL_KEY if not set, and persist it to .env so it
# survives restarts.  This runs once on first launch — no installer needed.
if not settings.CREDENTIAL_KEY.get_secret_value():
    from cryptography.fernet import Fernet  # type: ignore

    _new_key = Fernet.generate_key().decode()
    settings.CREDENTIAL_KEY = SecretStr(_new_key)

    _env_path = Path(__file__).resolve().parent.parent / ".env"
    if _env_path.exists():
        _text = _env_path.read_text(encoding="utf-8")
        if "CREDENTIAL_KEY=" in _text:
            import re as _re
            _text = _re.sub(r"^CREDENTIAL_KEY=.*$", f"CREDENTIAL_KEY={_new_key}", _text, flags=_re.MULTILINE)
        else:
            _text = _text.rstrip("\n") + f"\nCREDENTIAL_KEY={_new_key}\n"
        _env_path.write_text(_text, encoding="utf-8")
    else:
        _env_path.write_text(f"CREDENTIAL_KEY={_new_key}\n", encoding="utf-8")


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(settings.jobpilot_data_dir)
if not DATA_DIR.is_absolute():
    DATA_DIR = (PROJECT_ROOT / DATA_DIR).resolve()
