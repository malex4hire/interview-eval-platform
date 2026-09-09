"""Application settings. Every operational knob is here, none are inline."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "AI Interview Evaluation Platform"
    environment: str = "development"

    # --- Database -----------------------------------------------------------
    # SQLite by default so the demo runs with no external service. Point at
    # postgresql+psycopg://... for anything beyond local evaluation.
    database_url: str = f"sqlite:///{BASE_DIR / 'interview_platform.db'}"

    # --- Auth ---------------------------------------------------------------
    # Override this outside local dev.
    jwt_secret: str = "dev-only-insecure-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 60 * 8

    # PBKDF2 work factor. Only ever raise this in a real deployment. The test
    # suite lowers it, since a slow KDF protects a password database and does
    # nothing for a fixture. The count lives inside each hash, so changing it
    # doesn't invalidate old passwords - they verify under their own count and
    # can be re-hashed on next login.
    pbkdf2_iterations: int = 480_000

    # --- Evaluation ---------------------------------------------------------
    # THE knob. An evaluation whose confidence is strictly below this value is
    # routed to requires_human_review instead of standing as the outcome.
    # Global by decision D-3; per-interview override is a documented forward
    # requirement, not implemented.
    confidence_threshold: int = 70

    # Selects the EvaluationProvider implementation. "mock" is deterministic
    # and offline; real providers register alongside it (see providers/registry).
    llm_provider: str = "mock"
    llm_model_name: str = "mock-eval-v1"

    # Mocked pricing, used only to populate the cost ledger.
    cost_per_1k_input_tokens_usd: float = 0.003
    cost_per_1k_output_tokens_usd: float = 0.015

    # --- Storage ------------------------------------------------------------
    # Local filesystem behind a narrow interface; S3 swaps in without the
    # domain layer noticing.
    audio_storage_dir: Path = BASE_DIR / "storage" / "audio"
    max_audio_bytes: int = 25 * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.audio_storage_dir.mkdir(parents=True, exist_ok=True)
    return settings
