from functools import lru_cache
from typing import List, Union
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

    # Stage timeouts and retries
    TIMEOUT_EXTRACTING: float = 30.0
    TIMEOUT_INVESTIGATING: float = 60.0
    TIMEOUT_VERIFYING: float = 60.0
    MAX_RETRIES: int = 2

    @property
    def cors_origins_list(self) -> List[str]:
        if isinstance(self.CORS_ORIGINS, list):
            return self.CORS_ORIGINS
        if isinstance(self.CORS_ORIGINS, str):
            return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]
        return ["http://localhost:5173"]


@lru_cache()
def get_settings() -> Settings:
    return Settings()


def get_case_mode() -> dict:
    settings = get_settings()
    return {
        "mock_docs": settings.MOCK_DOCS,
        "mock_ai": settings.MOCK_AI,
        "replay_cache": settings.LLM_REPLAY,
    }
