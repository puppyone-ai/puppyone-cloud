"""Who may open a host socket: a person's own sign-in, from a header or a subprotocol."""

from fastapi import WebSocket


def is_person(user) -> bool:
    """A signed-in person (not anonymous); runtime credentials never reach this point."""
    return bool(user) and getattr(user, "role", None) == "authenticated" and not getattr(user, "is_anonymous", False)


def token_from(websocket: WebSocket) -> tuple[str, str | None]:
    """Bearer token from the header or a ``version.bearer.<jwt>`` subprotocol."""
    header = websocket.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip(), None
    offered = [raw.strip() for raw in websocket.headers.get("sec-websocket-protocol", "").split(",")]
    for protocol in offered:
        if protocol.startswith("version.bearer.") and len(protocol) > len("version.bearer."):
            # Answer only with a protocol the client offered: strict clients refuse anything else,
            # and the token itself is never echoed back.
            return protocol[len("version.bearer."):], "version.v1" if "version.v1" in offered else None
    return "", None
