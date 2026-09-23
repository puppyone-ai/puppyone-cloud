"""Expose enabled sign-in methods without exposing provider credentials."""

import os
import time

import httpx
from fastapi import APIRouter, HTTPException, Response

from src.common_schemas import ApiResponse

router = APIRouter()
_cached: tuple[str, float, list[str]] | None = None


@router.get("/methods")
async def methods(response: Response):
    global _cached
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_ANON_KEY", "") or os.environ.get("SUPABASE_KEY", "")
    response.headers["Cache-Control"] = "private, no-store"
    if not url or not key:
        raise HTTPException(status_code=503, detail="Sign-in methods are temporarily unavailable")
    if _cached and _cached[0] == url and time.monotonic() < _cached[1]:
        return ApiResponse.success(data={"providers": _cached[2]})
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            settings_response = await client.get(f"{url}/auth/v1/settings", headers={"apikey": key})
        settings_response.raise_for_status()
        external = settings_response.json()["external"]
        if not isinstance(external, dict):
            raise ValueError("Invalid provider capabilities")
        providers = [name for name in ("email", "google", "github") if external.get(name) is True]
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=503, detail="Sign-in methods are temporarily unavailable"
        ) from exc
    _cached = (url, time.monotonic() + 60, providers)
    return ApiResponse.success(data={"providers": providers})
