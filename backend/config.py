import shutil
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
    JOB_SITE_PASSWORD: SecretStr = SecretStr("")  # Fallback for the app-stored password

    # App settings with sensible defaults.
    # With ``case_sensitive=False`` (see model_config) pydantic-settings resolves
    # each field from the uppercased env var automatically — e.g. ``jobpilot_host``
    # reads ``JOBPILOT_HOST`` — so the deprecated ``env=`` kwarg is unnecessary.
    jobpilot_host: str = "127.0.0.1"
    jobpilot_port: int = 8000
    jobpilot_log_level: str = "info"
    jobpilot_scraper_headless: bool = True
    # Tier-2 apply uses a *visible* browser by default so the user can watch and
    # intervene (logins, captchas). Set true for native servers without a display.
    # Docker apply runs always attach to the supervised Chrome on its Xvfb display.
    jobpilot_apply_headless: bool = False
    # W02 containment (F02/F03/F05): automatic FINAL submission is opt-in.
    # Until destination verification and correction gating exist, `auto`
    # apply is refused; assisted (prefill + you submit) and manual remain.
    jobpilot_auto_submit_enabled: bool = False
    # W07: extra Host names accepted besides loopback and the allowed origins'
    # hosts (comma-separated). Needed only behind a local reverse proxy.
    jobpilot_allowed_hosts: str = ""
    jobpilot_data_dir: str = "./data"
    # Set by Docker Compose: attach only, never launch a replacement browser.
    # Empty keeps the native Chrome controls and per-site browser fallback.
    JOBPILOT_CHROME_VIEW_URL: str = ""
    # Comma-separated list of allowed CORS origins. Default = local dev hosts.
    jobpilot_allowed_origins: str = (
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000"
    )
    # ── Multi-provider LLM selection (applied on restart) ────────────────
    # Generation. "openai" means ANY OpenAI-compatible endpoint — hosted
    # OpenAI, a local server (Ollama, LM Studio, vLLM, llama.cpp), or any other
    # vendor exposing an OpenAI-compatible endpoint, selected with LLM_BASE_URL.
    LLM_PROVIDER: str = "auto"          # auto | claude | codex | openai | anthropic
    LLM_MODEL: str = ""                 # provider default if empty
    LLM_CLI_MODEL: str = ""             # Claude: sonnet; Codex: its configured default
    LLM_BASE_URL: str = ""              # openai-compatible/local, e.g. http://localhost:11434/v1
    LLM_API_KEY: SecretStr = SecretStr("")
    # Reasoning models (Qwen3, DeepSeek-R1, …) emit a long chain-of-thought
    # before the answer, which can make a single CV-tailoring call take 100s+.
    # When true, the adapter asks the server to skip "thinking" via
    # chat_template_kwargs.enable_thinking=false (supported by llama.cpp/vLLM/
    # Ollama for these models) — typically a ~10x latency win. No effect on
    # models/servers that don't recognise the flag.
    LLM_DISABLE_THINKING: bool = False
    # Browser agent (anthropic only via openai-compatible base_url)
    BROWSER_LLM_PROVIDER: str = "openai"  # openai
    BROWSER_LLM_MODEL: str = ""
    BROWSER_LLM_BASE_URL: str = ""
    BROWSER_LLM_API_KEY: SecretStr = SecretStr("")
    # System One browser harness (disabled until an endpoint is configured).
    DECISION_MODEL_URL: str = ""
    DECISION_MODEL_API_KEY: SecretStr = SecretStr("")
    DECISION_MODEL_NAME: str = "jev-latest"
    HARNESS_MAX_ACTIONS: int = 60
    # Per-provider keys (used when the generic *_API_KEY is empty)
    OPENAI_API_KEY: SecretStr = SecretStr("")
    ANTHROPIC_API_KEY: SecretStr = SecretStr("")
    # Feature flag: enable Tier 1 Scrapling fetcher (HTTP + single LLM call)
    SCRAPLING_ENABLED: bool = True
    # Feature flag: enable Tier 1 Playwright direct filler (mirrors SCRAPLING_ENABLED)
    APPLY_TIER1_ENABLED: bool = True
    # Tier 2 (browser agent) caps per agent run. Past either one the run fails
    # closed: nothing is recorded as applied.
    APPLY_TIER2_MAX_STEPS: int = 25
    APPLY_TIER2_TIMEOUT_SECONDS: float = 300.0
    # Minimum pause between two automatic applications on the same site; the actual
    # pause is 1x to 2.5x this, at random. 0 disables it.
    APPLY_MIN_GAP_SECONDS: float = 90.0

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

    # ── Discovery monitor ────────────────────────────────────────────────
    # Off by default: nothing polls until the owner turns it on.
    MONITOR_ENABLED: bool = False
    MONITOR_TICK_SECONDS: int = 30
    MONITOR_MAX_CONCURRENCY: int = 8
    NTFY_URL: str = ""

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

    def resolve_llm_provider(self) -> str:
        """Resolve auto without starting a CLI or contacting a provider."""
        provider = (self.LLM_PROVIDER or "auto").lower()
        if provider != "auto":
            return provider
        for cli in ("claude", "codex"):
            if shutil.which(cli):
                return cli
        if any(self.is_configured(name) for name in ("LLM_BASE_URL", "LLM_API_KEY", "OPENAI_API_KEY")):
            return "openai"
        if self.is_configured("ANTHROPIC_API_KEY"):
            return "anthropic"
        return "none"

    def writing_model_available(self) -> bool:
        """Whether the selected writer has an executable or API configuration."""
        provider = self.resolve_llm_provider()
        if provider in ("claude", "codex"):
            return shutil.which(provider) is not None
        if provider == "openai":
            return any(
                self.is_configured(name) for name in ("LLM_BASE_URL", "LLM_API_KEY", "OPENAI_API_KEY")
            )
        if provider == "anthropic":
            return self.is_configured("LLM_API_KEY") or self.is_configured("ANTHROPIC_API_KEY")
        return False

    def llm_configured(self) -> bool:
        """Writer availability surfaced as ``llm_key_set`` in health/onboarding."""
        return self.writing_model_available()

    def validate_runtime_config(self) -> list[str]:
        """Return human-readable problems with the selected LLM providers.

        Explicit generation providers must be usable; auto may find none.
        Browser configuration is checked separately and only warns at startup.
        """
        problems: list[str] = []
        _VALID_GEN = ("auto", "claude", "codex", "openai", "anthropic")

        def _openai_compatible_ok(base_url_attr: str, key_attr: str) -> bool:
            # A base_url means a local/self-hosted server (key is usually
            # ignored); otherwise a hosted-OpenAI key is required.
            return (
                self.is_configured(base_url_attr)
                or self.is_configured(key_attr)
                or self.is_configured("OPENAI_API_KEY")
            )

        gen = (self.LLM_PROVIDER or "auto").lower()
        if gen not in _VALID_GEN:
            problems.append(f"LLM_PROVIDER={gen!r} must be one of auto|claude|codex|openai|anthropic.")
        elif gen in ("claude", "codex") and not shutil.which(gen):
            problems.append(f"LLM_PROVIDER={gen} requires {gen} on PATH.")
        elif gen == "openai" and not _openai_compatible_ok("LLM_BASE_URL", "LLM_API_KEY"):
            problems.append(
                "LLM_PROVIDER=openai requires LLM_BASE_URL (local/self-hosted) "
                "or LLM_API_KEY/OPENAI_API_KEY (hosted OpenAI-compatible endpoint)."
            )
        elif gen == "anthropic" and not (
            self.is_configured("LLM_API_KEY") or self.is_configured("ANTHROPIC_API_KEY")
        ):
            problems.append("LLM_PROVIDER=anthropic requires ANTHROPIC_API_KEY (or LLM_API_KEY).")

        return problems

    def validate_browser_llm_config(self) -> list[str]:
        """Problems that disable Tier 2 without preventing startup."""
        br = (self.BROWSER_LLM_PROVIDER or "openai").lower()
        if br not in ("openai", "anthropic"):
            return [f"BROWSER_LLM_PROVIDER={br!r} must be openai (or anthropic with a base_url)."]
        if br == "anthropic" and not self.is_configured("BROWSER_LLM_BASE_URL"):
            return ["BROWSER_LLM_PROVIDER=anthropic needs a base_url (set BROWSER_LLM_BASE_URL)."]
        if not (
            self.is_configured("BROWSER_LLM_BASE_URL")
            or self.is_configured("BROWSER_LLM_API_KEY")
            or self.is_configured("OPENAI_API_KEY")
        ):
            # An Anthropic key is only usable through an OpenAI-compatible base_url (checked above).
            return ["BROWSER_LLM_PROVIDER=openai requires BROWSER_LLM_BASE_URL or an OpenAI key."]
        return []


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
