"""ScopeSandboxService (the #9 connect/status/revoke orchestration) tests.

Uses a fake provider that records exec calls + an in-memory store, so the whole
acquire → bootstrap (SSH provision + clone) → per-user workspace → grant flow is
exercised without any live SDK."""

from __future__ import annotations

from dataclasses import dataclass

from src.platform.scope_sandbox import ssh_credentials
from src.platform.scope_sandbox.manager import ScopeSandboxManager
from src.platform.scope_sandbox.provider import (
    ConnectionInfo,
    ProviderCapabilities,
    SandboxInfo,
    SandboxProvider,
    SandboxSpec,
    SandboxState,
)
from src.platform.scope_sandbox.registry import InMemorySandboxSessionStore
from src.platform.scope_sandbox.service import ScopeSandboxService


@dataclass
class _Scope:
    id: str
    project_id: str
    path: str


class FakeProvider(SandboxProvider):
    """Records exec commands; simulates E2B (proxy) or Fly (direct TCP)."""

    def __init__(self, *, tcp_ingress: bool) -> None:
        self._tcp = tcp_ingress
        self.execs: list[tuple[str, str]] = []
        self.secret_writes: list[tuple[str, str, str]] = []
        self._n = 0
        self.states: dict[str, SandboxState] = {}

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name="fly" if self._tcp else "e2b",
            supports_stop_resume=True, supports_destroy=True,
            supports_tcp_ingress=self._tcp, self_hostable=not self._tcp,
        )

    def _conn(self, sid: str) -> ConnectionInfo:
        if self._tcp:  # Fly: direct TCP, no proxy
            return ConnectionInfo(host=f"{sid}.fly.dev", port=22, username="puppy")
        # E2B: wss tunnel → ProxyCommand
        return ConnectionInfo(
            host=sid, port=22, username="user",
            proxy_command=f"websocat --binary -B 65536 - wss://8081-{sid}.e2b.app",
        )

    async def create(self, spec: SandboxSpec) -> SandboxInfo:
        self._n += 1
        sid = f"sb-{self._n}"
        self.states[sid] = SandboxState.RUNNING
        return SandboxInfo(sid, SandboxState.RUNNING, self._conn(sid))

    async def start(self, sandbox_id: str) -> SandboxInfo:
        self.states[sandbox_id] = SandboxState.RUNNING
        return SandboxInfo(sandbox_id, SandboxState.RUNNING, self._conn(sandbox_id))

    async def stop(self, sandbox_id: str) -> SandboxInfo:
        self.states[sandbox_id] = SandboxState.STOPPED
        return SandboxInfo(sandbox_id, SandboxState.STOPPED)

    async def destroy(self, sandbox_id: str) -> None:
        self.states[sandbox_id] = SandboxState.DESTROYED

    async def status(self, sandbox_id: str) -> SandboxInfo:
        return SandboxInfo(sandbox_id, self.states.get(sandbox_id, SandboxState.UNKNOWN))

    async def exec(self, sandbox_id: str, command: str) -> dict:
        self.execs.append((sandbox_id, command))
        return {"stdout": "", "stderr": "", "exit_code": 0}

    async def write_secret(
        self, sandbox_id: str, relative_path: str, value: str
    ) -> None:
        self.secret_writes.append((sandbox_id, relative_path, value))

    def exec_blob(self) -> str:
        return "\n".join(c for _, c in self.execs)


SCOPE = _Scope(id="scope-123456789", project_id="proj-1", path="docs")


async def _noop_sidecar(*args, **kwargs):
    return None


class _NoopLease:
    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


def _service(provider: FakeProvider):
    store = InMemorySandboxSessionStore()
    svc = ScopeSandboxService(
        store=store,
        scope_lookup=lambda sid: SCOPE if sid == SCOPE.id else None,
        manager_factory=lambda name: ScopeSandboxManager(
            provider, store, bootstrap=svc._make_bootstrap(),
            revoke_hook=ssh_credentials.revoke_ssh_access,
        ),
        sidecar_starter=_noop_sidecar,   # keep connect tests hermetic (no DB/sidecar)
        scope_credential_issuer=lambda *_args: "secret-cli-token-value",
        git_credential_issuer=lambda *_args: "secret-git-token-value",
        write_lease_factory=_NoopLease,
    )
    return svc


async def _connect(svc, user="u1", now=0.0):
    return await svc.connect(
        project_id="proj-1", scope_id=SCOPE.id, user_id=user,
        user_email=f"{user}@corp.com", user_name=user,
        public_key="ssh-ed25519 AAAAKEY u1@laptop",
        public_base="https://qubits-api.puppyone.ai", now=now,
    )


async def test_connect_e2b_provisions_ssh_clones_and_grants():
    prov = FakeProvider(tcp_ingress=False)
    svc = _service(prov)
    info = await _connect(svc)

    assert info.via == "created" and info.state == "running"
    assert info.needs_websocat is True
    assert info.proxy_command and "websocat" in info.proxy_command
    assert "ProxyCommand" in info.ssh_config_block and "HostName" in info.ssh_config_block
    assert info.workspace_path == "/home/user/u1"
    assert info.expires_at > 0 and info.connected_users == 1

    blob = prov.exec_blob()
    assert "sshd" in blob                                  # E2B SSH provisioned
    assert "git clone" in blob and "/git/proj-1/scopes/scope-123456789.git" in blob
    assert "secret-cli-token-value" not in blob
    assert "secret-git-token-value" not in blob
    assert "~/scope" in blob                               # scope workspace (bootstrap)
    assert "~/u1" in blob                                  # per-user working tree (#7)
    assert "authorized_keys" in blob                       # SSH key granted (#5)
    assert prov.secret_writes
    assert all(path == ".config/puppyone/git-http-token" for _, path, _ in prov.secret_writes)
    assert all(value == "secret-git-token-value" for _, _, value in prov.secret_writes)


async def test_connect_fly_skips_websocat_and_has_direct_tcp():
    prov = FakeProvider(tcp_ingress=True)
    svc = _service(prov)
    info = await _connect(svc)

    assert info.needs_websocat is False
    assert info.proxy_command is None
    assert "ProxyCommand" not in info.ssh_config_block
    assert ".fly.dev" in info.host
    # Fly bakes sshd into the image → no runtime sshd provisioning
    assert "sshd" not in prov.exec_blob()


async def test_connect_reuses_sandbox_for_second_user():
    prov = FakeProvider(tcp_ingress=False)
    svc = _service(prov)
    await _connect(svc, user="u1", now=0)
    info2 = await _connect(svc, user="u2", now=5)
    assert info2.via == "reused" and info2.connected_users == 2
    assert prov._n == 1  # only one sandbox created for the scope
    assert len(prov.secret_writes) == 3  # cold bootstrap + first/second renewal


async def test_status_reflects_connection():
    prov = FakeProvider(tcp_ingress=False)
    svc = _service(prov)
    assert svc.status(project_id="proj-1", scope_id=SCOPE.id, user_id="u1")["state"] == "none"
    await _connect(svc, user="u1")
    st = svc.status(project_id="proj-1", scope_id=SCOPE.id, user_id="u1")
    assert st["state"] == "running" and st["connected"] is True and st["connected_users"] == 1
    assert st["workspace_path"] == "/home/user/u1"
    assert "Host puppy-scope-12" in st["ssh_config_block"]


async def test_revoke_drops_user_and_calls_provider():
    prov = FakeProvider(tcp_ingress=False)
    svc = _service(prov)
    await _connect(svc, user="u1", now=0)
    await _connect(svc, user="u2", now=1)
    prov.execs.clear()
    remaining = await svc.revoke(project_id="proj-1", scope_id=SCOPE.id, user_id="u1")
    assert remaining == 1
    # revoke_hook (revoke_ssh_access) ran against the box
    assert "authorized_keys" in prov.exec_blob()
    assert svc.status(project_id="proj-1", scope_id=SCOPE.id, user_id="u1")["connected"] is False


def test_available_providers_reflects_configured_creds(monkeypatch):
    from src.config import settings
    svc = _service(FakeProvider(tcp_ingress=False))
    # E2B_API_KEY comes from the env (SDK reads it); Fly creds are Settings fields.
    monkeypatch.setenv("E2B_API_KEY", "k")
    monkeypatch.setattr(settings, "SCOPE_SANDBOX_FLY_APP", "")
    monkeypatch.setattr(settings, "SCOPE_SANDBOX_FLY_TOKEN", "")
    monkeypatch.setattr(settings, "SCOPE_SANDBOX_PROVIDER", "e2b")
    out = svc.available_providers()
    by = {p["id"]: p for p in out["providers"]}
    assert out["default"] == "e2b"
    assert by["e2b"]["configured"] is True and by["fly"]["configured"] is False

    monkeypatch.setattr(settings, "SCOPE_SANDBOX_FLY_APP", "app")
    monkeypatch.setattr(settings, "SCOPE_SANDBOX_FLY_TOKEN", "tok")
    assert {p["id"] for p in svc.available_providers()["providers"] if p["configured"]} == {"e2b", "fly"}


async def test_reap_sweeps_per_provider():
    prov = FakeProvider(tcp_ingress=False)
    svc = _service(prov)
    await _connect(svc, user="u1", now=0)
    summary = await svc.reap(now=0)
    # the connected session is swept under its provider's manager and kept
    # (connected_users pins it RUNNING)
    assert summary.kept == 1 and summary.stopped == 0


async def test_connect_unknown_scope_raises():
    prov = FakeProvider(tcp_ingress=False)
    svc = _service(prov)
    import pytest
    with pytest.raises(LookupError):
        await svc.connect(
            project_id="proj-1", scope_id="nope", user_id="u1",
            user_email="u1@corp.com", user_name="u1",
            public_key="ssh-ed25519 AAAA u1", public_base="https://x", now=0,
        )


async def test_connect_wrong_project_raises():
    prov = FakeProvider(tcp_ingress=False)
    svc = _service(prov)
    import pytest
    with pytest.raises(LookupError):
        await svc.connect(
            project_id="OTHER", scope_id=SCOPE.id, user_id="u1",
            user_email="u1@corp.com", user_name="u1",
            public_key="ssh-ed25519 AAAA u1", public_base="https://x", now=0,
        )
