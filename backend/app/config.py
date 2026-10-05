import os
from functools import lru_cache
from typing import List, Union, Any
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ENV: str = "dev"
    DATABASE_URL: str = "sqlite:///./claimlens_local.db"
    UPLOAD_DIR: str = "uploads"
    MAX_UPLOAD_BYTES: int = 10 * 1024 * 1024  # 10 MB
    CORS_ORIGINS: Union[str, List[str]] = "http://localhost:5173"
    MOCK_AI: bool = True
    MOCK_DOCS: bool = True
    RETENTION_HOURS: int = 24
    RATE_LIMIT_UPLOAD_PER_MIN: int = 10
    RATE_LIMIT_REVIEW_PER_MIN: int = 60
    RATE_LIMIT_ENABLED: bool = True
    EVAL_REPORT_PATH: str = "eval/report.json"
    ALLOW_MOCK_FALLBACK: bool = True
    LLM_REPLAY: bool = False
    SQL_ECHO: bool = False

    # New M3 Settings
    LLM_PROVIDER: str = "groq"
    GROQ_API_KEY: str = ""
    GEMINI_API_KEY: str = ""

    # Stage timeouts and retries
    TIMEOUT_EXTRACTING: float = 30.0
    TIMEOUT_INVESTIGATING: float = 60.0
    TIMEOUT_VERIFYING: float = 60.0
    MAX_RETRIES: int = 2

    @field_validator("MOCK_AI", "MOCK_DOCS", "LLM_REPLAY", mode="before")
    @classmethod
    def parse_bool(cls, v: Any) -> bool:
        if isinstance(v, str):
            val = v.strip().lower()
            if val in ("1", "true", "yes", "on"):
                return True
            if val in ("0", "false", "no", "off"):
                return False
        return bool(v)

    @property
    def cors_origins_list(self) -> List[str]:
        if isinstance(self.CORS_ORIGINS, list):
            return self.CORS_ORIGINS
        if isinstance(self.CORS_ORIGINS, str):
            return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]
        return ["http://localhost:5173"]


def _export_to_environ(settings: Settings) -> None:
    env_keys = [
        "LLM_PROVIDER",
        "GROQ_API_KEY",
        "GEMINI_API_KEY",
        "LLM_REPLAY",
        "MOCK_AI",
        "MOCK_DOCS",
    ]
    for key in env_keys:
        val = getattr(settings, key, None)
        if val is not None and key not in os.environ:
            if isinstance(val, bool):
                os.environ[key] = "true" if val else "false"
            else:
                os.environ[key] = str(val)


@lru_cache()
def get_settings() -> Settings:
    st = Settings()
    _export_to_environ(st)
    return st


def get_case_mode() -> dict:
    settings = get_settings()
    return {
        "mock_docs": settings.MOCK_DOCS,
        "mock_ai": settings.MOCK_AI,
        "replay_cache": settings.LLM_REPLAY,
    }

