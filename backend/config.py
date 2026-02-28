from pydantic_settings import BaseSettings  # type: ignore
from pydantic import Field  # type: ignore
from pydantic_settings import SettingsConfigDict  # type: ignore


class Settings(BaseSettings):
    """Application settings loaded from environment or .env file."""

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)

    # Required secrets (no defaults)
    GOOGLE_API_KEY: str
    ADZUNA_APP_ID: str
    ADZUNA_APP_KEY: str

    # Optional
    SERPAPI_KEY: str = ""

    # App settings with sensible defaults
    jobpilot_host: str = Field("127.0.0.1", env="JOBPILOT_HOST")
    jobpilot_port: int = Field(8000, env="JOBPILOT_PORT")
    jobpilot_log_level: str = Field("info", env="JOBPILOT_LOG_LEVEL")
    jobpilot_data_dir: str = Field("./data", env="JOBPILOT_DATA_DIR")


settings = Settings()
