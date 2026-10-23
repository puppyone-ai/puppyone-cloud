"""Deployment settings for the incoming SSH gateway (not provider transport)."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SshSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WORKSPACE_SSH_", env_file=".env", extra="ignore")
    public_host: str = ""
    public_port: int = Field(default=2222, ge=1, le=65535)
    public_host_key: str = ""
    host_key_file: str = ""
    listen_host: str = "0.0.0.0"
    listen_port: int = Field(default=2222, ge=1, le=65535)


@lru_cache
def ssh_settings():
    return SshSettings()
