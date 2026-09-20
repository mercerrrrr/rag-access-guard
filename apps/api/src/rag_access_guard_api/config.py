"""Environment-backed application configuration."""

from typing import ClassVar, Final

from pydantic import PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DATABASE_URL: Final[PostgresDsn] = PostgresDsn(
    "postgresql+psycopg://rag_guard:local-dev-only@127.0.0.1:55432/rag_access_guard"
)
DATABASE_MAX_OVERFLOW: Final = 5
DATABASE_POOL_SIZE: Final = 5
DATABASE_TIMEOUT_SECONDS: Final = 2


class Settings(BaseSettings):
    """Validated process configuration."""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="RAG_ACCESS_GUARD_",
        extra="ignore",
        frozen=True,
    )

    database_url: PostgresDsn = DEFAULT_DATABASE_URL
