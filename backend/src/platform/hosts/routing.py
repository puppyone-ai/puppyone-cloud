"""Where a client's tunnel meets the device socket, and how revocation reaches it.

Device sockets live in exactly one process: the single-worker ``device_gateway``
role. API workers (``--workers N``) never hold them; they carry device sockets
and tunnels to it over the internal network, and tell it about revocations.
Development/test may run the gateway inside the API process instead.
"""

import asyncio
import logging
import secrets
from uuid import UUID

import httpx

from src.config import settings

from .gateway import HostGateway

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

    async def revoked(self, device: UUID):
        await self.gateway.disconnect(device)
        await publish_revocation(device)


class ForwardingRoute:
    """API workers: forward to the gateway's internal address with a shared secret."""

    def __init__(self, base_url: str, secret: str, client: httpx.AsyncClient | None = None):
        self.base = base_url.rstrip("/")
        self.headers = {SECRET_HEADER: secret}
        self.client = client or httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(35, connect=5))

    async def revoked(self, device: UUID):
        try:
            await self.client.post(f"{self.base}/internal/hosts/devices/{device}/disconnect",
                                   headers=self.headers)
        except httpx.HTTPError:
            logger.warning("host_revocation_notice_failed")  # Redis and lease renewal still fence it.
        await publish_revocation(device)


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


# Close codes a browser-style WebSocket may not send itself; they mean "gone".
_UNSENDABLE_CLOSE = frozenset({1005, 1006, 1015})
MAX_FORWARDED_MESSAGE = 16 * 1024 * 1024


def _close_code(code: int | None) -> int:
    if not isinstance(code, int) or code in _UNSENDABLE_CLOSE or not (1000 <= code <= 4999):
        return 1011
    return code


async def forward_websocket(websocket, path: str):
    """Carry a device socket or a tunnel from an API worker to the device_gateway.

    Devices and clients use the API's own address; the gateway stays on the
    private network. Frames pass unchanged in both directions (text stays text,
    bytes stay bytes) and close codes survive, so the device proof, the
    person's JWT and every decision stay with the gateway.
    """
    import websockets
    from websockets.exceptions import ConnectionClosed

    base = settings.HOST_GATEWAY_INTERNAL_URL.rstrip("/")
    url = ("ws" + base[4:] if base.startswith("http") else base) + path
    offered = [item.strip() for item in websocket.headers.get("sec-websocket-protocol", "").split(",") if item.strip()]
    headers = {SECRET_HEADER: settings.HOST_GATEWAY_INTERNAL_SECRET}
    if websocket.headers.get("authorization"):
        headers["Authorization"] = websocket.headers["authorization"]
    try:
        upstream = await websockets.connect(url, additional_headers=headers, subprotocols=offered or None,
                                            max_size=MAX_FORWARDED_MESSAGE, open_timeout=10, ping_interval=None,
                                            proxy=None)
    except Exception as exc:
        logger.warning("host_gateway_forward_unavailable", extra={"error": type(exc).__name__, "detail": str(exc)[:200]})
        await websocket.close(code=1013)
        return
    try:
        await websocket.accept(subprotocol=upstream.subprotocol)

        async def downstream():
            try:
                async for message in upstream:
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(message)
            except ConnectionClosed:
                pass
            return ("upstream", upstream.close_code)

        async def upstream_from_client():
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return ("client", message.get("code"))
                if message.get("bytes") is not None:
                    await upstream.send(message["bytes"])
                elif message.get("text") is not None:
                    await upstream.send(message["text"])

        tasks = [asyncio.create_task(downstream()), asyncio.create_task(upstream_from_client())]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        side, code = next(iter(done)).result() if not next(iter(done)).exception() else ("upstream", 1011)
        if side == "upstream":
            await websocket.close(code=_close_code(code))
        else:
            await upstream.close(code=_close_code(code) if code not in (None, 1006) else 1000)
    except Exception:
        logger.warning("host_gateway_forward_failed")
    finally:
        await upstream.close()
