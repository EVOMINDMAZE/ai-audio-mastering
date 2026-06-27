"""
Application configuration.

All values are read from environment variables (or a local .env file). The
Settings instance is import-safe — no external services are contacted at import
time. This means tests and CI runs can import the package without real
Supabase / OpenAI credentials present.
"""

from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Strongly-typed application settings.

    Values default to safe development values. Production deployments must
    override SUPABASE_*, CORS_ORIGINS, and any secrets via environment vars.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- App -----------------------------------------------------------------
    app_env: str = "development"
    cors_origins: List[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors(cls, v):
        """Allow CORS_ORIGINS to be passed as a comma-separated string."""
        if isinstance(v, str) and not v.startswith("["):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    # ---- Storage -------------------------------------------------------------
    job_tmp_dir: str = "/tmp/audio_jobs"
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_bucket: str = "mastered_audio"
    # Feature flag — when False, storage.upload_to_supabase() returns a
    # local file:// URL so the route signature is stable without external creds.
    supabase_enabled: bool = False

    # ---- Mastering defaults --------------------------------------------------
    target_lufs: float = -14.0          # Spotify streaming standard
    true_peak_ceiling_dbtp: float = -1.0

    # ---- Phase 2 (reserved) --------------------------------------------------
    openai_api_key: str = ""

    # ---- LLM-driven mastering (DeepSeek, OpenAI-compatible) -----------------
    # When LLM_ENABLED is True and DEEPSEEK_API_KEY is set, /api/ai-master is
    # available. When LLM_ENABLED is False (default), that endpoint returns
    # 503 so the rest of the app keeps working without a key configured.
    llm_enabled: bool = False
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_timeout_s: float = 30.0


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor — instantiated once per process."""
    return Settings()