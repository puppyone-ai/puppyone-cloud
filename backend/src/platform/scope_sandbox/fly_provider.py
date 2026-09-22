"""Fly.io Machines provider — version A (managed Firecracker microVM).

Maps our three-state lifecycle onto the Fly Machines REST API:

    create  → POST   /v1/apps/{app}/machines        (+ wait for "started")
    start   → POST   /v1/apps/{app}/machines/{id}/start
    stop    → POST   /v1/apps/{app}/machines/{id}/stop      (disk retained)
    destroy → DELETE /v1/apps/{app}/machines/{id}?force=true
    status  → GET    /v1/apps/{app}/machines/{id}

Why Fly for this use case: Firecracker isolation, ~10–150ms resume from
``stopped`` (disk retained → incremental git fetch, not a full pull), per-second
billing that drops to storage-only when stopped, and **native VSCode Remote-SSH**
(run sshd internally on 2222, expose as public TCP 22 via Fly Proxy; see
docs/proposals — fly.io/docs/blueprints/opensshd).

The httpx client is injectable so this is unit-testable with
``httpx.MockTransport``; the Authorization header is attached per request so an
injected (header-less) client still authenticates. Wire-level details
(machine config schema, state strings) follow Fly's docs and SHOULD be
validated against a live app before production.
"""

from __future__ import annotations

import httpx
import posixpath
import re

from src.platform.scope_sandbox.provider import (
    ConnectionInfo,
    ProviderCapabilities,
    SandboxInfo,
    SandboxProvider,
    SandboxSpec,
    SandboxState,
)

_DEFAULT_BASE_URL = "https://api.machines.dev"

# Fly machine.state → our lifecycle state.
_STATE_MAP = {
    "started": SandboxState.RUNNING,
    "stopped": SandboxState.STOPPED,
    "suspended": SandboxState.STOPPED,
    "destroyed": SandboxState.DESTROYED,
    "created": SandboxState.PENDING,
    "starting": SandboxState.PENDING,
    "replacing": SandboxState.PENDING,
    "stopping": SandboxState.PENDING,
    "destroying": SandboxState.PENDING,
}


class FlyMachinesProvider(SandboxProvider):
    def __init__(
        self,
        app_name: str,
        token: str,
        *,
        client: httpx.AsyncClient | None = None,
        base_url: str = _DEFAULT_BASE_URL,
        default_image: str = "",
        ssh_internal_port: int = 2222,
        public_host: str | None = None,
        username: str = "puppy",
        wait_timeout_s: int = 60,
    ) -> None:
        self._app = app_name
        self._token = token
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=30.0)
        self._default_image = default_image
        self._ssh_internal_port = ssh_internal_port
        self._public_host = public_host or f"{app_name}.fly.dev"
        self._username = username
        self._wait_timeout_s = wait_timeout_s

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name="fly",
            supports_stop_resume=True,
            supports_destroy=True,
            supports_tcp_ingress=True,   # native public TCP 22 → VSCode Remote-SSH
            self_hostable=False,
        )

    # ── HTTP ──────────────────────────────────────────────────────────

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._token}", **kwargs.pop("headers", {})}
        resp = await self._client.request(method, path, headers=headers, **kwargs)
        resp.raise_for_status()
        return resp

    def _machine_path(self, machine_id: str = "") -> str:
        base = f"/v1/apps/{self._app}/machines"
        return f"{base}/{machine_id}" if machine_id else base

    def _to_state(self, fly_state: str) -> SandboxState:
        return _STATE_MAP.get(fly_state, SandboxState.UNKNOWN)

    def _connection(self, ssh_port: int) -> ConnectionInfo:
        return ConnectionInfo(host=self._public_host, port=ssh_port, username=self._username)

    def _machine_config(self, spec: SandboxSpec) -> dict:
        return {
            "region": spec.region,
            "config": {
                "image": spec.image or self._default_image,
                "guest": {
                    "cpus": spec.vcpus,
                    "memory_mb": spec.memory_mb,
                    "cpu_kind": "shared",
                },
                "env": dict(spec.env),
                # Expose sshd (internal 2222) as public TCP 22 for VSCode Remote-SSH.
                "services": [
                    {
                        "protocol": "tcp",
                        "internal_port": self._ssh_internal_port,
                        "ports": [{"port": spec.ssh_port, "handlers": []}],
                    }
                ],
            },
        }

    async def _wait_for(self, machine_id: str, instance_id: str, state: str) -> None:
        await self._request(
            "GET",
            f"{self._machine_path(machine_id)}/wait",
            params={"state": state, "instance_id": instance_id, "timeout": self._wait_timeout_s},
        )

    async def _wait_state(self, machine_id: str, state: str) -> None:
        """Wait for ``machine_id`` to reach ``state`` (no instance_id needed)."""
        await self._request(
            "GET",
            f"{self._machine_path(machine_id)}/wait",
            params={"state": state, "timeout": self._wait_timeout_s},
        )

    # ── lifecycle ─────────────────────────────────────────────────────

    async def create(self, spec: SandboxSpec) -> SandboxInfo:
        resp = await self._request("POST", self._machine_path(), json=self._machine_config(spec))
        machine = resp.json()
        machine_id = machine["id"]
        if self._to_state(machine.get("state", "")) is not SandboxState.RUNNING:
            await self._wait_for(machine_id, machine.get("instance_id", ""), "started")
        return SandboxInfo(
            sandbox_id=machine_id,
            state=SandboxState.RUNNING,
            connection=self._connection(spec.ssh_port),
            raw=machine,
        )

    async def start(self, sandbox_id: str) -> SandboxInfo:
        try:
            await self._request("POST", f"{self._machine_path(sandbox_id)}/start")
        except httpx.HTTPStatusError as exc:
            # Fly `stop` is async: a start issued while the machine is still
            # `stopping` returns 412. Wait for `stopped`, then retry once. (Our
            # registry can record STOPPED before the box has fully settled, so a
            # quick acquire→start hits this — see sandbox-fly-validation doc.)
            if exc.response.status_code != 412:
                raise
            await self._wait_state(sandbox_id, "stopped")
            await self._request("POST", f"{self._machine_path(sandbox_id)}/start")
        return SandboxInfo(sandbox_id, SandboxState.RUNNING, self._connection(22))

    async def stop(self, sandbox_id: str) -> SandboxInfo:
        await self._request("POST", f"{self._machine_path(sandbox_id)}/stop")
        return SandboxInfo(sandbox_id, SandboxState.STOPPED)

    async def destroy(self, sandbox_id: str) -> None:
        try:
            await self._request("DELETE", self._machine_path(sandbox_id), params={"force": "true"})
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:  # already gone = success
                raise

    async def status(self, sandbox_id: str) -> SandboxInfo:
        try:
            resp = await self._request("GET", self._machine_path(sandbox_id))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return SandboxInfo(sandbox_id, SandboxState.DESTROYED)
            raise
        machine = resp.json()
        return SandboxInfo(sandbox_id, self._to_state(machine.get("state", "")), raw=machine)

    # ── exec (provisioning / credentials / health) ────────────────────

    async def exec(self, sandbox_id: str, command: str, *, timeout: int = 60,
                   background: bool = False) -> dict:
        """Run a shell command via the Fly Machines exec API.

        ``background`` is accepted for interface parity but is a no-op: Fly exec is
        SSH-based, so a self-detaching ``setsid … &`` command (see
        ``sidecar_provision.start_command``) already survives the exec returning.

        Fly exec runs as root; we re-enter as the SSH user (``self._username``) so
        ``~`` and file ownership match the account VSCode logs into — that way the
        SAME provider-agnostic helpers (scope_provision, ssh_credentials) work on
        Fly as on E2B. The user has passwordless sudo (baked into the image), so
        the few ``sudo`` steps still work. Raises on non-zero exit so provisioning
        failures surface (mirrors the E2B provider).
        """
        wrapped = self._as_user(command)
        resp = await self._request(
            "POST",
            f"{self._machine_path(sandbox_id)}/exec",
            json={"command": ["/bin/sh", "-c", wrapped], "timeout": timeout},
        )
        data = resp.json()
        result = {
            "stdout": data.get("stdout", ""),
            "stderr": data.get("stderr", ""),
            "exit_code": data.get("exit_code", 0),
        }
        if result["exit_code"] not in (0, None):
            raise RuntimeError(
                f"fly exec failed (rc={result['exit_code']}) on {sandbox_id}: "
                f"{command!r}: {result['stderr'][:500]}"
            )
        return result

    async def write_secret(
        self, sandbox_id: str, relative_path: str, value: str
    ) -> None:
        path = _home_secret_path(self._username, relative_path)
        parent = posixpath.dirname(path)
        command = (
            f"umask 077; mkdir -p '{parent}'; "
            f"cat > '{path}'; chmod 600 '{path}'"
        )
        wrapped = self._as_user(command)
        response = await self._request(
            "POST",
            f"{self._machine_path(sandbox_id)}/exec",
            json={
                "command": ["/bin/sh", "-c", wrapped],
                "stdin": value,
                "timeout": 60,
            },
        )
        data = response.json()
        if data.get("exit_code", 0) not in (0, None):
            raise RuntimeError(
                f"fly secret write failed on {sandbox_id}: "
                f"{str(data.get('stderr') or '')[:500]}"
            )

    def _as_user(self, command: str) -> str:
        if not self._username or self._username == "root":
            return command
        escaped = command.replace("'", "'\\''")
        return f"su - {self._username} -c '{escaped}'"


def _home_secret_path(username: str, relative_path: str) -> str:
    raw = relative_path.strip().lstrip("/")
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", raw):
        raise ValueError("Sandbox secret path contains unsupported characters")
    clean = posixpath.normpath(raw)
    if clean in {"", "."} or clean == ".." or clean.startswith("../"):
        raise ValueError("Sandbox secret path must stay below the user home")
    user = username.strip() or "puppy"
    if "/" in user or user in {".", ".."}:
        raise ValueError("Sandbox username is invalid")
    return f"/home/{user}/{clean}"
