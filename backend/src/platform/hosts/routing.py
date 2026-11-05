"""Where a human file request meets the device socket.

Device sockets live in exactly one process: the single-worker ``device_gateway``
role. API workers (``--workers N``) never hold them; they forward the already
authenticated owner and the structured operation over the internal network.
Development/test may run the gateway inside the API process instead.
"""

import asyncio
import json
import logging
import secrets
from collections.abc import AsyncIterator
from uuid import UUID

import httpx
from fastapi import HTTPException

from src.config import settings

from .gateway import MUTATIONS, HostGateway

logger = logging.getLogger(__name__)

SECRET_HEADER = "X-PuppyOne-Device-Gateway"
REVOCATION_CHANNEL = "puppyone:hosts:revoked"


def gateway_secret_matches(value: str | None) -> bool:
    expected = settings.HOST_GATEWAY_INTERNAL_SECRET
    return bool(expected) and isinstance(value, str) and secrets.compare_digest(value, expected)


class LocalRoute:
    """The gateway runs in this process (device_gateway role, development, tests)."""

    def __init__(self, gateway: HostGateway):
        self.gateway = gateway

    async def dispatch(self, owner: str, folder: UUID, action: str, operation: dict):
        return await self.gateway.dispatch(owner, folder, action, operation)

    def watch(self, owner: str, folder: UUID) -> AsyncIterator[dict | None]:
        return self.gateway.watch(owner, folder)

    async def revoked(self, device: UUID):
        await self.gateway.disconnect(device)
        await publish_revocation(device)


class ForwardingRoute:
    """API workers: forward to the gateway's internal address with a shared secret."""

    def __init__(self, base_url: str, secret: str, client: httpx.AsyncClient | None = None):
        self.base = base_url.rstrip("/")
        self.headers = {SECRET_HEADER: secret}
        self.client = client or httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(35, connect=5))

    async def dispatch(self, owner: str, folder: UUID, action: str, operation: dict):
        try:
            response = await self.client.post(f"{self.base}/internal/hosts/dispatch", headers=self.headers,
                json={"owner": owner, "folder_id": str(folder), "action": action, "operation": operation})
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            # Never reached the gateway: nothing was delivered to the device.
            raise HTTPException(503, {"code": "device_offline"}) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(503, {"code": "device_disconnected",
                                      "outcome_unknown": action in MUTATIONS}) from exc
        return _relay(response)

    async def watch(self, owner: str, folder: UUID) -> AsyncIterator[dict | None]:
        request = self.client.build_request(
            "GET", f"{self.base}/internal/hosts/events", headers=self.headers,
            params={"owner": owner, "folder_id": str(folder)},
            timeout=httpx.Timeout(None, connect=5))
        try:
            response = await self.client.send(request, stream=True)
        except httpx.HTTPError as exc:
            raise HTTPException(503, {"code": "device_offline"}) from exc
        try:
            if response.status_code >= 400:
                await response.aread()
                _relay(response)
            async for line in response.aiter_lines():
                if line:
                    value = json.loads(line)
                    yield None if isinstance(value, dict) and value.get("keepalive") else value
        except httpx.HTTPError:
            yield {"unavailable": {"retryable": True}}
        finally:
            await response.aclose()

    async def revoked(self, device: UUID):
        try:
            await self.client.post(f"{self.base}/internal/hosts/devices/{device}/disconnect",
                                   headers=self.headers)
        except httpx.HTTPError:
            logger.warning("host_revocation_notice_failed")  # Redis and lease renewal still fence it.
        await publish_revocation(device)


def _relay(response: httpx.Response):
    try:
        body = response.json()
    except ValueError:
        body = None
    if response.status_code >= 400:
        detail = body.get("detail") if isinstance(body, dict) else None
        raise HTTPException(response.status_code, detail or {"code": "file_operation_failed"})
    return body


async def publish_revocation(device: UUID):
    """Tell every gateway instance; a lost notice is still fenced by the next heartbeat."""
    if not settings.NOTIFICATIONS_REDIS_URL:
        return
    import redis.asyncio as redis

    client = redis.from_url(settings.NOTIFICATIONS_REDIS_URL)
    try:
        await client.publish(REVOCATION_CHANNEL, str(device))
    except Exception:
        logger.warning("host_revocation_publish_failed")
    finally:
        await client.aclose()


async def listen_for_revocations(gateway: HostGateway):
    """Gateway role: close sockets revoked through any API replica."""
    if not settings.NOTIFICATIONS_REDIS_URL:
        return
    import redis.asyncio as redis

    while True:
        client = redis.from_url(settings.NOTIFICATIONS_REDIS_URL)
        pubsub = client.pubsub()
        try:
            await pubsub.subscribe(REVOCATION_CHANNEL)
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    device = UUID(message["data"].decode() if isinstance(message["data"], bytes)
                                  else str(message["data"]))
                except (ValueError, AttributeError):
                    continue
                await gateway.disconnect(device)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("host_revocation_listener_failed")
            await asyncio.sleep(2)
        finally:
            await pubsub.aclose()
            await client.aclose()
