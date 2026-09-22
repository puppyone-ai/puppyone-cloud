"""Black-box HTTP acceptance cases against locally running real servers.

Usage:
    python tests/system/liangyu_live_http_cases.py \
        --api http://127.0.0.1:19090 --mcp http://127.0.0.1:19091

No dependency overrides or in-process TestClient are used. The script only
performs non-mutating requests and verifies the public security boundaries.
"""

from __future__ import annotations

import argparse

import httpx


def _expect(client: httpx.Client, method: str, url: str, statuses: set[int], **kwargs):
    response = client.request(method, url, **kwargs)
    if response.status_code not in statuses:
        raise AssertionError(
            f"{method} {url}: expected {sorted(statuses)}, got "
            f"{response.status_code}: {response.text[:300]}"
        )
    return response


def run(api: str, mcp: str) -> None:
    with httpx.Client(timeout=15, trust_env=False) as client:
        health = _expect(client, "GET", f"{api}/health", {200}).json()
        assert health.get("status") == "ready"

        # ISSUE-001: neither analytics endpoint is anonymously readable.
        for endpoint in ("access-timeseries", "access-summary"):
            _expect(
                client,
                "GET",
                f"{api}/api/v1/analytics/{endpoint}",
                {401},
                params={"project_id": "cross-tenant-probe"},
            )

        # ISSUE-016: unified scope surfaces remain behind user/project auth.
        _expect(
            client,
            "GET",
            f"{api}/api/v1/projects/cross-tenant-probe/scopes",
            {401, 403},
            # Send the current public repository-contract version so this
            # black-box assertion reaches the authorization boundary instead
            # of being short-circuited by the independent upgrade guard.
            headers={"X-PuppyOne-Repository-Contract": "2"},
        )

        # ISSUE-017: product MCP runtime is internal-only; transport exposes
        # health but its invalidation hook requires the shared secret.
        _expect(
            client,
            "POST",
            f"{api}/internal/mcp-runtime/tools",
            {403},
            headers={"X-Internal-Secret": "wrong-secret"},
            json={"api_key": "mcp_invalid"},
        )
        mcp_health = _expect(client, "GET", f"{mcp}/healthz", {200}).json()
        assert mcp_health.get("service") == "mcp-service"
        _expect(
            client,
            "POST",
            f"{mcp}/cache/invalidate",
            {401},
            json={"access_surface_id": "probe"},
        )

        # ISSUE-018: endpoint execution cannot be reached without its hash-only
        # machine credential. 404 is deliberate non-enumeration behavior.
        _expect(
            client,
            "POST",
            f"{api}/api/v1/sandbox-endpoints/probe/exec",
            # An invalid key is 403 with a healthy credential store; a missing
            # local/hosted store must fail closed as a typed 503, never 500 or
            # sandbox execution.
            {403, 503},
            headers={"X-Access-Key": "sbx_invalid"},
            json={"command": "echo probe"},
        )

    print("liangyu live HTTP acceptance: PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", required=True)
    parser.add_argument("--mcp", required=True)
    args = parser.parse_args()
    run(args.api.rstrip("/"), args.mcp.rstrip("/"))
