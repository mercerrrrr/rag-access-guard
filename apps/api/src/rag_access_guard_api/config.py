"""Environment-backed application configuration."""

import ipaddress
import re
from pathlib import Path
from typing import ClassVar, Final, Self
from urllib.parse import urlsplit

from pydantic import PostgresDsn, SecretStr, model_validator
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
        hide_input_in_errors=True,
    )

    database_url: PostgresDsn = DEFAULT_DATABASE_URL
    auth_origin: str = "https://localhost:5173"
    auth_limit_secret: SecretStr = SecretStr("")
    loopback_development: bool = False
    bind_host: str = "127.0.0.1"
    retrieval_config_path: Path | None = None

    @model_validator(mode="after")
    def validate_auth_settings(self) -> Self:
        """Require operator secret and an exact origin with explicit local opt-in."""
        if not re.fullmatch(r"[0-9a-fA-F]{64}", self.auth_limit_secret.get_secret_value()):
            message = "Auth limit secret must be 32 bytes encoded as hexadecimal"
            raise ValueError(message)
        origin = urlsplit(self.auth_origin)
        if (
            not origin.hostname
            or origin.username
            or origin.password
            or origin.path
            or origin.query
            or origin.fragment
            or "*" in self.auth_origin
            or origin.scheme not in {"http", "https"}
        ):
            message = "Auth origin must be one exact scheme, host and optional port"
            raise ValueError(message)
        if self.loopback_development:
            if (
                origin.scheme != "http"
                or not is_loopback(origin.hostname)
                or not is_loopback(self.bind_host)
            ):
                message = "Development auth requires HTTP loopback origin and bind host"
                raise ValueError(message)
        elif origin.scheme != "https":
            message = "Authentication requires HTTPS unless loopback development is explicit"
            raise ValueError(message)
        return self


def is_loopback(host: str) -> bool:
    """Accept localhost or numeric loopback addresses without DNS trust."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
