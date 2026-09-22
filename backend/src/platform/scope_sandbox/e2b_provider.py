"""E2B provider — version B (Firecracker; self-hostable / compliance option).

Maps our three-state lifecycle onto E2B's pause/resume model:

    create  → client.create(spec)   → RUNNING
    stop    → client.pause(id)       → STOPPED (filesystem + memory retained)
    start   → client.resume(id)      → RUNNING (fast)
    destroy → client.kill(id)        → DESTROYED
    status  → client.get_state(id)

Why E2B as the alternate version: strongest isolation (Firecracker) and the
**only self-hostable** option (Apache-2.0 infra, BYOC/on-prem) — the pick for
enterprises that want the sandbox inside their own infrastructure / for
compliance. Trade-off vs Fly: **no native public SSH** — VSCode Remote-SSH must
be tunnelled over E2B's wss proxy (sshd + websocat ``ProxyCommand``), which is
why ``supports_tcp_ingress=False`` and ``ConnectionInfo.proxy_command`` is set.

The E2B SDK is wrapped behind the small :class:`E2BClient` protocol so this is
unit-testable without the SDK or live credentials. ``SdkE2BClient`` is the real
implementation; its exact SDK calls SHOULD be validated against the installed
``e2b``/``e2b-code-interpreter`` version before production.
"""

from __future__ import annotations

import asyncio
import posixpath
import re
from typing import Protocol

from src.platform.scope_sandbox.provider import (
    ConnectionInfo,
    ProviderCapabilities,
    SandboxInfo,
    SandboxProvider,
    SandboxSpec,
    SandboxState,
)

# SSH-over-wss tunnel: E2B exposes an internal port via a public wss proxy host
# of the form ``{port}-{sandboxId}.e2b.app``; SSH is reached with websocat as a
# ProxyCommand (see docs/proposals + e2b.dev/docs/sandbox/ssh-access).
_SSH_PROXY_PORT = 8081


class E2BClient(Protocol):
    """Minimal surface the provider needs; the real impl wraps the e2b SDK."""

    def create(self, spec: SandboxSpec) -> str:
        """Create a running sandbox, return its id."""

    def pause(self, sandbox_id: str) -> None: ...
    def resume(self, sandbox_id: str) -> None: ...
    def kill(self, sandbox_id: str) -> None: ...
    def get_state(self, sandbox_id: str) -> SandboxState: ...
    def exec(self, sandbox_id: str, command: str, background: bool = False) -> dict: ...
    def write_file(self, sandbox_id: str, path: str, value: str) -> None: ...
    def set_timeout(self, sandbox_id: str) -> None: ...


class E2BProvider(SandboxProvider):
    def __init__(
        self,
        client: E2BClient,
        *,
        domain: str = "e2b.app",
        # The E2B default template's account is "user" (non-root, passwordless
        # sudo, /home/user). authorized_keys + the scope workspace live under it,
        # so the SSH login name MUST be "user" — see ssh_e2b.DEFAULT_USER.
        username: str = "user",
    ) -> None:
        self._client = client
        self._domain = domain
        self._username = username

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name="e2b",
            supports_stop_resume=True,    # pause/resume retains FS + memory
            supports_destroy=True,
            supports_tcp_ingress=False,   # no native public TCP — SSH via wss tunnel
            self_hostable=True,
            # commands.run kills the process tree on return → long-runners (the
            # sync sidecar) must be launched via exec(background=True).
            background_exec_required=True,
        )

    def _connection(self, sandbox_id: str, spec_ssh_port: int = 22) -> ConnectionInfo:
        wss = f"wss://{_SSH_PROXY_PORT}-{sandbox_id}.{self._domain}"
        return ConnectionInfo(
            host=sandbox_id,
            port=spec_ssh_port,
            username=self._username,
            proxy_command=f"websocat --binary -B 65536 - {wss}",
            extra={"wss_url": wss},
        )

    # The E2BClient is synchronous (the e2b SDK is blocking), so every call is
    # offloaded to a thread to avoid blocking the event loop.
    async def create(self, spec: SandboxSpec) -> SandboxInfo:
        sandbox_id = await asyncio.to_thread(self._client.create, spec)
        return SandboxInfo(sandbox_id, SandboxState.RUNNING, self._connection(sandbox_id, spec.ssh_port))

    async def start(self, sandbox_id: str) -> SandboxInfo:
        await asyncio.to_thread(self._client.resume, sandbox_id)
        return SandboxInfo(sandbox_id, SandboxState.RUNNING, self._connection(sandbox_id))

    async def stop(self, sandbox_id: str) -> SandboxInfo:
        await asyncio.to_thread(self._client.pause, sandbox_id)
        return SandboxInfo(sandbox_id, SandboxState.STOPPED)

    async def destroy(self, sandbox_id: str) -> None:
        await asyncio.to_thread(self._client.kill, sandbox_id)

    async def status(self, sandbox_id: str) -> SandboxInfo:
        state = await asyncio.to_thread(self._client.get_state, sandbox_id)
        return SandboxInfo(sandbox_id, state)

    async def exec(self, sandbox_id: str, command: str, *, background: bool = False) -> dict:
        return await asyncio.to_thread(self._client.exec, sandbox_id, command, background)

    async def write_secret(
        self, sandbox_id: str, relative_path: str, value: str
    ) -> None:
        path = _home_secret_path(self._username, relative_path)
        await asyncio.to_thread(self._client.write_file, sandbox_id, path, value)

    async def extend(self, sandbox_id: str) -> None:
        # E2B sandboxes auto-kill at their timeout; reset it so an active
        # session isn't reclaimed underneath us.
        await asyncio.to_thread(self._client.set_timeout, sandbox_id)


class SdkE2BClient(E2BClient):
    """Real :class:`E2BClient` over ``e2b_code_interpreter``.

    Validated against ``e2b-code-interpreter`` (the installed SDK) on
    2026-06-07: ``kill``/``pause``/``connect``/``get_info`` are class-method
    variants callable by sandbox id; there is NO ``resume`` — reconnecting via
    ``Sandbox.connect(id)`` resumes a paused sandbox. The SDK reads
    ``E2B_API_KEY`` from the environment (we don't pass it explicitly).
    """

    def __init__(self, api_key: str | None = None, *, timeout: int = 300,
                 template: str = "") -> None:
        self._api_key = api_key
        self._timeout = timeout
        self._template = template or ""   # custom template id (roadmap #6); "" → default

    def create(self, spec: SandboxSpec) -> str:
        from e2b_code_interpreter import Sandbox  # lazy: keep import off the hot path
        # Launch the custom PuppyOne template (baked sshd+websocat+sidecar) when
        # configured; otherwise the SDK default template.
        if self._template:
            sbx = Sandbox.create(self._template, timeout=self._timeout)
        else:
            sbx = Sandbox.create(timeout=self._timeout)
        return sbx.sandbox_id

    def pause(self, sandbox_id: str) -> None:
        from e2b_code_interpreter import Sandbox
        Sandbox.pause(sandbox_id)

    def resume(self, sandbox_id: str) -> None:
        from e2b_code_interpreter import Sandbox
        Sandbox.connect(sandbox_id, timeout=self._timeout)  # connect resumes a paused sandbox

    def kill(self, sandbox_id: str) -> None:
        from e2b_code_interpreter import Sandbox
        Sandbox.kill(sandbox_id)

    def get_state(self, sandbox_id: str) -> SandboxState:
        from e2b_code_interpreter import Sandbox
        try:
            info = Sandbox.get_info(sandbox_id)
        except Exception:
            return SandboxState.DESTROYED  # not found / killed
        state = str(getattr(info, "state", "")).lower()
        if "run" in state:
            return SandboxState.RUNNING
        if "paus" in state:
            return SandboxState.STOPPED
        return SandboxState.UNKNOWN

    def exec(self, sandbox_id: str, command: str, background: bool = False) -> dict:
        from e2b_code_interpreter import Sandbox
        sbx = Sandbox.connect(sandbox_id, timeout=self._timeout)
        if background:
            # Detached long-runner: commands.run kills the process tree on return,
            # so background=True keeps it alive. No exit code to wait on.
            sbx.commands.run(command, background=True)
            return {"exit_code": None, "stdout": "", "stderr": "", "background": True}
        result = sbx.commands.run(command)
        return {
            "exit_code": getattr(result, "exit_code", None),
            "stdout": getattr(result, "stdout", ""),
            "stderr": getattr(result, "stderr", ""),
        }

    def write_file(self, sandbox_id: str, path: str, value: str) -> None:
        from e2b_code_interpreter import Sandbox

        sbx = Sandbox.connect(sandbox_id, timeout=self._timeout)
        parent = posixpath.dirname(path)
        sbx.files.make_dir(parent)
        sbx.files.write(path, value)
        # The secret travels via the filesystem API. Only the non-secret path
        # appears in the chmod command or provider logs.
        result = sbx.commands.run(f"chmod 600 '{path}'")
        if getattr(result, "exit_code", 0) not in (0, None):
            raise RuntimeError("Unable to protect sandbox secret file")

    def set_timeout(self, sandbox_id: str) -> None:
        from e2b_code_interpreter import Sandbox
        # connect attaches to the running sandbox; set_timeout resets its
        # remaining lifetime to self._timeout from now.
        Sandbox.connect(sandbox_id, timeout=self._timeout).set_timeout(self._timeout)


def _home_secret_path(username: str, relative_path: str) -> str:
    raw = relative_path.strip().lstrip("/")
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", raw):
        raise ValueError("Sandbox secret path contains unsupported characters")
    clean = posixpath.normpath(raw)
    if clean in {"", "."} or clean == ".." or clean.startswith("../"):
        raise ValueError("Sandbox secret path must stay below the user home")
    user = username.strip() or "user"
    if "/" in user or user in {".", ".."}:
        raise ValueError("Sandbox username is invalid")
    return f"/home/{user}/{clean}"
