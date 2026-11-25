"""One-time codes: shown once, stored only as an HMAC hash, valid 10 minutes."""

import hashlib
import hmac
import secrets

from src.config import settings


def code_hash(raw: str) -> str:
    secret = settings.CHAT_APPS_CODE_HASH_SECRET
    if not secret:
        raise ValueError("CHAT_APPS_CODE_HASH_SECRET is required to hash chat link codes")
    return hmac.new(secret.encode(), raw.strip().encode(), hashlib.sha256).hexdigest()


def link_code() -> str:
    """Telegram ``start`` parameter: base64url, 43 characters (<= 64)."""
    return secrets.token_urlsafe(32)


def short_code() -> str:
    """WeCom: six digits typed after a mention, matched with the bot it belongs to."""
    return f"{secrets.randbelow(1_000_000):06d}"
