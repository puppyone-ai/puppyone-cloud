"""Shared authentication dependency for internal HTTP endpoints."""

import hmac

from fastapi import Header, HTTPException

from src.config import settings


async def verify_internal_secret(x_internal_secret: str = Header(...)) -> None:
    configured_secret = (settings.INTERNAL_API_SECRET or "").strip()
    if not configured_secret:
        raise HTTPException(
            status_code=503,
            detail="Internal API secret is not configured",
        )

    if not hmac.compare_digest(x_internal_secret, configured_secret):
        raise HTTPException(status_code=403, detail="Invalid internal secret")
