"""Customer platform credentials: encrypted at rest, decrypted only to call the platform."""

import json

from src.config import settings
from src.infra.security.crypto import decrypt_secret, encrypt_secret, load_aes_key


def _key() -> bytes:
    return load_aes_key(settings.CHAT_APPS_ENCRYPTION_KEY, "CHAT_APPS_ENCRYPTION_KEY")


def seal(plain: str) -> str:
    envelope = encrypt_secret(plain.encode(), key=_key(), kid=settings.CHAT_APPS_ENCRYPTION_KID)
    return json.dumps(envelope, separators=(",", ":"))


def unseal(stored: str) -> str:
    return decrypt_secret(json.loads(stored), key=_key()).decode()
