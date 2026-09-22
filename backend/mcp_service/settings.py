"""
MCP Service 配置管理
集中管理所有配置项：
- 本地开发：优先从 `.env` 加载（支持多个候选路径）
- 生产环境（Railway）：直接使用注入的环境变量（无需 `.env`）
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


_SERVICE_DIR = Path(__file__).resolve().parent  # backend/mcp_service
_BACKEND_DIR = _SERVICE_DIR.parent              # backend
_REPO_DIR = _BACKEND_DIR.parent                 # repo root (可能包含 backend/)


class Settings(BaseSettings):
    """MCP Service 配置类（与 `src/config.py` 同风格）"""

    # 注意：
    # - env_file 支持多个候选路径
    # - 文件缺失不会报错（Railway 上通常没有 `.env`）
    model_config = SettingsConfigDict(
        env_file=[
            str(_REPO_DIR / ".env"),
            str(_BACKEND_DIR / ".env"),
            str(_SERVICE_DIR / ".env"),
            ".env",  # 兼容从 backend/ 作为 cwd 启动
        ],
        case_sensitive=True,
        extra="ignore",
        env_file_encoding="utf-8",
    )

    # ============ 服务配置 ============
    HOST: str = "0.0.0.0"
    PORT: int = 3090
    APP_ENV: Literal["development", "test", "staging", "production"] = "development"

    # ============ RPC 配置 ============
    MAIN_SERVICE_URL: str = "http://localhost:9090"
    INTERNAL_API_SECRET: str = ""  # 在 validate() 中强制校验，避免 import 时就炸
    RPC_TIMEOUT: float = 30.0

    # ============ 缓存配置 ============
    CACHE_TTL: int = 900
    CACHE_BACKEND: Literal["mem", "redis"] = "mem"
    REDIS_URL: Optional[str] = None

    # ============ 日志配置 ============
    LOG_LEVEL: str = "INFO"
    DEBUG: bool = False

    # ============ 性能配置 ============
    MAX_RETRIES: int = 3
    RETRY_DELAY: float = 0.5

    # ============ CORS 配置 (ISSUE-008) ============
    # Development is local-only by default; hosted environments must explicitly
    # configure their trusted browser origins and may never use a wildcard.
    CORS_ALLOWED_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origins(self) -> list[str]:
        raw = (self.CORS_ALLOWED_ORIGINS or "").strip()
        if not raw:
            return []
        if raw == "*":
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]

    # ============ 辅助方法 ============
    def validate(self) -> None:
        """验证配置是否有效"""
        # 检查必需的配置项
        if not self.INTERNAL_API_SECRET:
            raise ValueError("INTERNAL_API_SECRET environment variable is required")
        
        # 如果使用Redis缓存，检查Redis URL
        if self.CACHE_BACKEND == "redis" and not self.REDIS_URL:
            raise ValueError("REDIS_URL is required when CACHE_BACKEND=redis")
        if self.APP_ENV in {"staging", "production"}:
            if not self.REDIS_URL:
                raise ValueError(
                    "Hosted MCP service requires REDIS_URL for cross-replica notifications"
                )
            origins = self.cors_origins
            if not origins or "*" in origins:
                raise ValueError(
                    "Hosted MCP CORS_ALLOWED_ORIGINS must be an explicit allowlist"
                )
    
    def display(self) -> dict:
        """显示配置（隐藏敏感信息）"""
        return {
            "HOST": self.HOST,
            "PORT": self.PORT,
            "MAIN_SERVICE_URL": self.MAIN_SERVICE_URL,
            "INTERNAL_API_SECRET": "***" + self.INTERNAL_API_SECRET[-4:] if len(self.INTERNAL_API_SECRET) > 4 else "***",
            "RPC_TIMEOUT": self.RPC_TIMEOUT,
            "CACHE_TTL": self.CACHE_TTL,
            "CACHE_BACKEND": self.CACHE_BACKEND,
            "REDIS_URL": "***" if self.REDIS_URL else None,
            "LOG_LEVEL": self.LOG_LEVEL,
            "DEBUG": self.DEBUG,
            "MAX_RETRIES": self.MAX_RETRIES,
            "RETRY_DELAY": self.RETRY_DELAY,
        }


# 创建全局配置实例
settings = Settings()
