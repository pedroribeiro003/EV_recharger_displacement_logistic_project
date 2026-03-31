"""
config/settings.py
Carrega variáveis de ambiente via pydantic-settings.
Todas as configurações do projeto passam por aqui.
"""

from functools import lru_cache
from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Database ──────────────────────────────────────────────
    db_host: str = Field("localhost")
    db_port: int = Field(5432)
    db_name: str = Field("ev_charging")
    db_user: str = Field("ev_user")
    db_password: str = Field(...)
    db_schema_staging: str = Field("staging")
    db_schema_core: str = Field("core")
    db_schema_analytics: str = Field("analytics")

    # Pool
    db_pool_size: int = Field(10)
    db_max_overflow: int = Field(20)
    db_pool_timeout: int = Field(30)

    # ── APIs ──────────────────────────────────────────────────
    open_charge_map_api_key: str = Field(...)
    ibge_api_base_url: str = Field("https://servicodados.ibge.gov.br/api/v1")

    # ── Pipeline ─────────────────────────────────────────────
    pipeline_batch_size: int = Field(500)
    pipeline_request_timeout: int = Field(30)
    pipeline_retry_attempts: int = Field(3)
    pipeline_retry_delay: int = Field(2)

    # ── Logging ───────────────────────────────────────────────
    log_level: str = Field("INFO")
    log_file: str = Field("logs/pipeline.log")

    # ── Computed ─────────────────────────────────────────────
    @computed_field
    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @computed_field
    @property
    def async_database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton — lê .env uma única vez."""
    return Settings()
