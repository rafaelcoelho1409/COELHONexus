"""Typed application settings (pydantic-settings).

Skeleton: `environment` + `redis` are wired as the pattern; the remaining env
groups are migrated off scattered `os.environ` at Step 4 (see TODO). Use
`get_settings()` everywhere — it's cached, so env is read once.

Requires `pydantic-settings` in pyproject.toml.
"""
from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RedisSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix = "REDIS_", extra = "ignore")

    host: str = "localhost"
    port: int = 6379
    password: str = ""

    @property
    def url(self) -> str:
        auth = f":{self.password}@" if self.password else ""
        return f"redis://{auth}{self.host}:{self.port}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    environment: str = "local"                       # reads $ENVIRONMENT
    redis: RedisSettings = Field(default_factory=RedisSettings)

    # TODO (Step 4 — migrate the scattered os.environ call sites into typed groups):
    #   postgres       → domains/docs_distiller/planner/checkpoint.py
    #   minio          → domains/docs_distiller/ingestion/storage_minio.py
    #   otel/langfuse  → core/otel_setup.py
    #   llm api keys   → domains/llm/chain.py, domains/llm/discovery.py


@lru_cache
def get_settings() -> Settings:
    """Cached singleton — read process env once."""
    return Settings()
