from pydantic import Field  # type: ignore
from pydantic_settings import (
    BaseSettings,  # type: ignore
    SettingsConfigDict,  # type: ignore
)


class Settings(BaseSettings):
    """Application settings loaded from environment or .env file."""

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)

    # Required secrets (no defaults)
    GOOGLE_API_KEY: str
    ADZUNA_APP_ID: str
    ADZUNA_APP_KEY: str

    # Optional
    SERPAPI_KEY: str = ""
    CREDENTIAL_KEY: str = ""  # Fernet key for encrypting stored credentials

    # App settings with sensible defaults
    jobpilot_host: str = Field("127.0.0.1", env="JOBPILOT_HOST")
    jobpilot_port: int = Field(8000, env="JOBPILOT_PORT")
    jobpilot_log_level: str = Field("info", env="JOBPILOT_LOG_LEVEL")
    jobpilot_scraper_headless: bool = Field(True, env="JOBPILOT_SCRAPER_HEADLESS")
    jobpilot_data_dir: str = Field("./data", env="JOBPILOT_DATA_DIR")
    # Google / Gemini model settings
    # Primary model name (Gemini 3 Flash Preview — newest, most intelligent flash)
    GOOGLE_MODEL: str = Field("gemini-3-flash-preview", env="GOOGLE_MODEL")
    # Comma-separated fallback model names (empty => no fallbacks)
    GOOGLE_MODEL_FALLBACKS: str = Field("", env="GOOGLE_MODEL_FALLBACKS")
    # Feature flag: enable Tier 1 Scrapling fetcher (HTTP + single LLM call)
    SCRAPLING_ENABLED: bool = Field(True, env="SCRAPLING_ENABLED")
    # Feature flag: enable Tier 1 Playwright direct filler (mirrors SCRAPLING_ENABLED)
    APPLY_TIER1_ENABLED: bool = Field(True, env="APPLY_TIER1_ENABLED")


settings = Settings()
