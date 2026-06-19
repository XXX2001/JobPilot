import sys
from pathlib import Path

from pydantic import SecretStr, ValidationError  # type: ignore
from pydantic_settings import (
    BaseSettings,  # type: ignore
    SettingsConfigDict,  # type: ignore
)


class Settings(BaseSettings):
    """Application settings loaded from environment or .env file."""

    # extra="ignore": tolerate unknown/legacy keys in .env (e.g. a provider key
    # left over from an older, non-model-agnostic config) rather than refusing
    # to boot.
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    # Credentials — all optional so the app can boot with, e.g., a local
    # OpenAI-compatible model and no cloud keys at all. What each *configured*
    # provider actually requires is enforced by ``validate_runtime_config()``
    # at startup (see backend/main.py), and the in-app onboarding surfaces the
    # job-source keys. This is what lets a local-model user skip the LLM key.
    ADZUNA_APP_ID: str = ""  # public app id (shown masked in UI but not a secret)
    ADZUNA_APP_KEY: SecretStr = SecretStr("")

    # Optional
    SERPAPI_KEY: SecretStr = SecretStr("")
    CREDENTIAL_KEY: SecretStr = SecretStr("")  # Fernet key for encrypting stored credentials

    # App settings with sensible defaults.
    # With ``case_sensitive=False`` (see model_config) pydantic-settings resolves
    # each field from the uppercased env var automatically — e.g. ``jobpilot_host``
    # reads ``JOBPILOT_HOST`` — so the deprecated ``env=`` kwarg is unnecessary.
    jobpilot_host: str = "127.0.0.1"
    jobpilot_port: int = 8000
    jobpilot_log_level: str = "info"
    jobpilot_scraper_headless: bool = True
    # Tier-2 apply uses a *visible* browser by default so the user can watch and
    # intervene (logins, captchas). Set true for headless/server/Docker runs
    # that have no display. (build_browser always adds the container-safe
    # Chromium flags, so headless launches work in Docker either way.)
    jobpilot_apply_headless: bool = False
    jobpilot_data_dir: str = "./data"
    # Comma-separated list of allowed CORS origins. Default = local dev hosts.
    jobpilot_allowed_origins: str = (
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000"
    )
    # ── Multi-provider LLM selection (applied on restart) ────────────────
    # Generation. "openai" means ANY OpenAI-compatible endpoint — hosted
    # OpenAI, a local server (Ollama, LM Studio, vLLM, llama.cpp), or any other
    # vendor exposing an OpenAI-compatible endpoint, selected with LLM_BASE_URL.
    LLM_PROVIDER: str = "openai"        # openai | anthropic
    LLM_MODEL: str = ""                 # provider default if empty
    LLM_BASE_URL: str = ""              # openai-compatible/local, e.g. http://localhost:11434/v1
    LLM_API_KEY: SecretStr = SecretStr("")
    # Reasoning models (Qwen3, DeepSeek-R1, …) emit a long chain-of-thought
    # before the answer, which can make a single CV-tailoring call take 100s+.
    # When true, the adapter asks the server to skip "thinking" via
    # chat_template_kwargs.enable_thinking=false (supported by llama.cpp/vLLM/
    # Ollama for these models) — typically a ~10x latency win. No effect on
    # models/servers that don't recognise the flag.
    LLM_DISABLE_THINKING: bool = False
    # Embeddings (anthropic unsupported — has no embeddings API)
    EMBEDDING_PROVIDER: str = "openai"  # openai
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    EMBEDDING_BASE_URL: str = ""
    EMBEDDING_API_KEY: SecretStr = SecretStr("")
    # Browser agent (anthropic only via openai-compatible base_url)
    BROWSER_LLM_PROVIDER: str = "openai"  # openai
    BROWSER_LLM_MODEL: str = ""
    BROWSER_LLM_BASE_URL: str = ""
    BROWSER_LLM_API_KEY: SecretStr = SecretStr("")
    # Per-provider keys (used when the generic *_API_KEY is empty)
    OPENAI_API_KEY: SecretStr = SecretStr("")
    ANTHROPIC_API_KEY: SecretStr = SecretStr("")
    # Feature flag: enable Tier 1 Scrapling fetcher (HTTP + single LLM call)
    SCRAPLING_ENABLED: bool = True
    # Feature flag: enable Tier 1 Playwright direct filler (mirrors SCRAPLING_ENABLED)
    APPLY_TIER1_ENABLED: bool = True

    # Timeouts (seconds) — fail loudly instead of hanging forever.
    # (Field name already maps to the env var; no need for a deprecated env=.)
    TECTONIC_TIMEOUT_SECONDS: float = 60.0
    # Per-request LLM timeout. Default sized for local/self-hosted *reasoning*
    # models (Qwen, DeepSeek-R1, etc.), where a single CV-tailoring call can
    # spend 100s+ generating a chain-of-thought before the answer. Hosted
    # models answer in a few seconds, so this only delays surfacing a genuine
    # hang; lower it (e.g. 45) if you exclusively use a fast hosted endpoint.
    LLM_TIMEOUT_SECONDS: float = 180.0

    # ── Gmail integration (Phase 1) ──────────────────────────────────────
    GMAIL_CLIENT_ID: str = ""
    GMAIL_CLIENT_SECRET: SecretStr = SecretStr("")
    GMAIL_REDIRECT_URI: str = "http://localhost:8000/api/gmail/oauth/callback"
    GMAIL_BACKFILL_DAYS: int = 30
    GMAIL_POLL_INTERVAL_MINUTES: int = 5

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

    def llm_configured(self) -> bool:
        """Whether the configured generation provider has usable credentials.

        Provider-agnostic: an LLM is considered "set up" if any LLM key is
        present, or a base_url points at a local/self-hosted endpoint (which
        usually needs no key). Surfaced as the ``llm_key_set`` flag in the
        health/onboarding APIs.
        """
        return (
            self.is_configured("LLM_API_KEY")
            or self.is_configured("OPENAI_API_KEY")
            or self.is_configured("ANTHROPIC_API_KEY")
            or bool((self.LLM_BASE_URL or "").strip())
        )

    def validate_runtime_config(self) -> list[str]:
        """Return human-readable problems with the selected LLM providers.

        Empty list == ready to run. Each of generation / embeddings / browser
        picks a provider; this checks only the credentials *that* provider
        needs, so a local-model user is never asked for a hosted key. Called
        at startup (fail-fast) by the app lifespan.
        """
        problems: list[str] = []
        _VALID_GEN = ("openai", "anthropic")
        _VALID_EMBED_BROWSER = ("openai",)

        def _openai_compatible_ok(base_url_attr: str, key_attr: str) -> bool:
            # A base_url means a local/self-hosted server (key is usually
            # ignored); otherwise a hosted-OpenAI key is required.
            return (
                self.is_configured(base_url_attr)
                or self.is_configured(key_attr)
                or self.is_configured("OPENAI_API_KEY")
            )

        gen = (self.LLM_PROVIDER or "openai").lower()
        if gen not in _VALID_GEN:
            problems.append(f"LLM_PROVIDER={gen!r} must be one of openai|anthropic.")
        elif gen == "openai" and not _openai_compatible_ok("LLM_BASE_URL", "LLM_API_KEY"):
            problems.append(
                "LLM_PROVIDER=openai requires LLM_BASE_URL (local/self-hosted) "
                "or LLM_API_KEY/OPENAI_API_KEY (hosted OpenAI-compatible endpoint)."
            )
        elif gen == "anthropic" and not (
            self.is_configured("LLM_API_KEY") or self.is_configured("ANTHROPIC_API_KEY")
        ):
            problems.append("LLM_PROVIDER=anthropic requires ANTHROPIC_API_KEY (or LLM_API_KEY).")

        emb = (self.EMBEDDING_PROVIDER or "openai").lower()
        if emb not in _VALID_EMBED_BROWSER:
            problems.append(
                f"EMBEDDING_PROVIDER={emb!r} must be openai "
                "(the only provider with an embeddings adapter; anthropic has no embeddings API)."
            )
        elif emb == "openai" and not _openai_compatible_ok("EMBEDDING_BASE_URL", "EMBEDDING_API_KEY"):
            problems.append(
                "EMBEDDING_PROVIDER=openai requires EMBEDDING_BASE_URL or EMBEDDING_API_KEY/OPENAI_API_KEY."
            )

        br = (self.BROWSER_LLM_PROVIDER or "openai").lower()
        if br not in _VALID_EMBED_BROWSER:
            problems.append(f"BROWSER_LLM_PROVIDER={br!r} must be openai.")
        elif br == "openai" and not (
            self.is_configured("BROWSER_LLM_BASE_URL")
            or self.is_configured("BROWSER_LLM_API_KEY")
            or self.is_configured("OPENAI_API_KEY")
        ):
            problems.append("BROWSER_LLM_PROVIDER=openai requires BROWSER_LLM_BASE_URL or an OpenAI key.")

        return problems


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

    # The .env now holds the Fernet master key that decrypts every stored
    # site/Gmail credential — restrict it to the owner. Best-effort: chmod is a
    # no-op on Windows and may fail on exotic filesystems, which is non-fatal.
    try:
        import os as _os

        _os.chmod(_env_path, 0o600)
    except OSError:
        pass


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(settings.jobpilot_data_dir)
if not DATA_DIR.is_absolute():
    DATA_DIR = (PROJECT_ROOT / DATA_DIR).resolve()
