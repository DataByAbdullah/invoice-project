"""
Application configuration via pydantic-settings.

All settings are sourced from environment variables (or .env file).
This is the single source of truth for runtime configuration — no magic
strings scattered across the codebase.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ────────────────────────────────────────────────────────
    app_env: Literal["development", "staging", "production"] = "development"
    app_name: str = "Invoice AI"
    app_version: str = "1.0.0"
    debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # ── API ────────────────────────────────────────────────────────────────
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_workers: int = 4
    allowed_origins: list[str] = Field(default=["http://localhost:3000"])

    # ── Database ───────────────────────────────────────────────────────────
    database_url: str = Field(..., description="Async PostgreSQL DSN")
    database_pool_size: int = 10
    database_max_overflow: int = 20

    # ── Redis ──────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # ── OpenAI ─────────────────────────────────────────────────────────────
    openai_api_key: str = Field(..., description="OpenAI secret key")
    openai_model: str = "gpt-4o"
    openai_max_tokens: int = 4096
    openai_temperature: float = 0.0

    # ── OCR ────────────────────────────────────────────────────────────────
    ocr_provider: Literal["tesseract", "aws_textract", "google_vision"] = "tesseract"
    tesseract_cmd: str = "/usr/bin/tesseract"

    # ── Storage ─────────────────────────────────────────────────────────────
    storage_backend: Literal["local", "s3"] = "local"
    upload_dir: str = "/tmp/invoice_uploads"
    max_upload_size_mb: int = 20
    allowed_mime_types: list[str] = Field(
        default=["application/pdf", "image/jpeg", "image/png", "image/tiff"]
    )

    # ── Security ────────────────────────────────────────────────────────────
    secret_key: str = Field(..., min_length=32)
    access_token_expire_minutes: int = 60

    # ── Anomaly / Duplicate Detection ───────────────────────────────────────
    anomaly_zscore_threshold: float = 2.5
    duplicate_similarity_threshold: float = 0.85

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_origins(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",")]
        return v

    @field_validator("allowed_mime_types", mode="before")
    @classmethod
    def parse_mime_types(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            return [m.strip() for m in v.split(",")]
        return v

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached singleton. Safe to call anywhere — always returns same instance."""
    return Settings()  # type: ignore[call-arg]
