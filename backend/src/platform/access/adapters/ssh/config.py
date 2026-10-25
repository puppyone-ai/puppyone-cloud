"""Deployment settings for the incoming SSH gateway (not provider transport)."""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class SshSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WORKSPACE_SSH_", env_file=".env", extra="ignore")
    public_host: str = ""
    public_port: int = Field(default=2222, ge=1, le=65535)
    public_host_key: str = ""
    # Private host key text (OpenSSH format), e.g. a secret service variable.
    # Takes precedence over host_key_file so no persistent volume is required.
    host_key: SecretStr = SecretStr("")
    host_key_file: str = ""
    listen_host: str = "0.0.0.0"
    listen_port: int = Field(default=2222, ge=1, le=65535)
    # Concurrent TCP connections per gateway process (database bounds apply per
    # workspace and per owner).
    max_connections: int = Field(default=200, ge=1, le=10_000)


@lru_cache
def ssh_settings():
    return SshSettings()
