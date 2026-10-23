"""LIVE Fly.io smoke for the scope-sandbox provider (E2E, free-tier).

Drives the REAL FlyMachinesProvider against a live Fly app:
  create → exec (verify hardened sshd + git baked in) → grant SSH key →
  revoke → lifecycle (stop/start/status) → destroy.

This is the Fly analogue of scripts/ssh_credentials_live.py (E2B). It validates
the provider code + image WITHOUT a dedicated IPv4 — the actual SSH *connection*
is checked separately via `fly proxy` (see the runbook in the fly-validation
doc). Reads SCOPE_SANDBOX_FLY_* from backend/.env.

Run from backend/:  python -m scripts.scope_sandbox_fly_smoke
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
from pathlib import Path


def _load_env() -> None:
    envf = Path(__file__).resolve().parents[1] / ".env"
    for line in envf.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("SCOPE_SANDBOX_FLY_") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v)


_load_env()

from src.platform.workspace.scopes import ssh_credentials  # noqa: E402
from src.platform.workspace.scopes.fly_provider import FlyMachinesProvider  # noqa: E402
from src.platform.workspace.scopes.provider import SandboxSpec, SandboxState  # noqa: E402

KEY_DIR = Path(r"C:\Users\29757\PuppyNew\.fly_ssh_test")
REGION = "sin"


def _alice_pubkey() -> str:
    KEY_DIR.mkdir(exist_ok=True)
    key = KEY_DIR / "alice"
    if not key.exists():
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key), "-C", "alice-fly"],
            check=True, capture_output=True,
        )
    return key.with_suffix(".pub").read_text().strip()


async def main() -> int:
    app = os.environ.get("SCOPE_SANDBOX_FLY_APP")
    token = os.environ.get("SCOPE_SANDBOX_FLY_TOKEN")
    image = os.environ.get("SCOPE_SANDBOX_FLY_IMAGE")
    if not (app and token and image):
        print("SCOPE_SANDBOX_FLY_* not set in backend/.env"); return 2
    print(f"app={app} image={image}")

    prov = FlyMachinesProvider(app, token, default_image=image)
    # small machine — well within the monthly free credit
    spec = SandboxSpec(scope_id="fly-smoke", project_id="demo", memory_mb=512, region=REGION)

    results: list[tuple[str, bool]] = []
    sid = None
    try:
        t0 = time.monotonic()
        info = await prov.create(spec)
        sid = info.sandbox_id
        print(f"created machine {sid} state={info.state.value} in {time.monotonic()-t0:.1f}s")
        print(f"  connection: host={info.connection.host} port={info.connection.port} "
              f"user={info.connection.username} proxy={info.connection.proxy_command}")
        results.append(("create → running", info.state is SandboxState.RUNNING))

        whoami = (await prov.exec(sid, "whoami")).get("stdout", "").strip()
        print(f"exec whoami → {whoami!r}")
        results.append(("exec runs as puppy", whoami == "puppy"))

        gitv = (await prov.exec(sid, "git --version")).get("stdout", "").strip()
        print(f"exec git → {gitv!r}")
        results.append(("git baked in", "git version" in gitv))

        sshd_cfg = (await prov.exec(sid, "cat /etc/ssh/sshd_config.d/00-puppyone.conf")).get("stdout", "")
        results.append(("sshd hardened (publickey-only)",
                        "AuthenticationMethods publickey" in sshd_cfg and "UsePAM no" in sshd_cfg))

        # grant + verify the tagged, expiring key landed in authorized_keys
        await ssh_credentials.grant_ssh_access(prov, sid, "alice", _alice_pubkey(),
                                               expires_at=time.time() + 3600)
        ak = (await prov.exec(sid, "cat ~/.ssh/authorized_keys")).get("stdout", "")
        print(f"authorized_keys after grant:\n{ak}")
        results.append(("grant adds tagged key", "puppyone:user=alice" in ak and "expiry-time=" in ak))

        await ssh_credentials.revoke_ssh_access(prov, sid, "alice")
        ak2 = (await prov.exec(sid, "cat ~/.ssh/authorized_keys")).get("stdout", "")
        results.append(("revoke removes key", "puppyone:user=alice" not in ak2))

        # lifecycle: stop (disk kept) → start → status. Fly stop is ASYNC
        # (machine goes stopping→stopped), so poll for the terminal state
        # before starting (a rapid start on a still-stopping machine 412s).
        async def _wait(target: SandboxState, timeout_s: float = 40.0) -> SandboxState:
            deadline = time.monotonic() + timeout_s
            st = (await prov.status(sid)).state
            while st is not target and time.monotonic() < deadline:
                await asyncio.sleep(2)
                st = (await prov.status(sid)).state
            return st

        await prov.stop(sid)
        st_stopped = await _wait(SandboxState.STOPPED)
        print(f"after stop: {st_stopped.value}")
        results.append(("stop → stopped", st_stopped is SandboxState.STOPPED))

        await prov.start(sid)
        st_started = await _wait(SandboxState.RUNNING)
        print(f"after start: {st_started.value}")
        results.append(("start → running", st_started is SandboxState.RUNNING))
    finally:
        if sid:
            await prov.destroy(sid)
            st = (await prov.status(sid)).state
            print(f"destroyed {sid} → {st.value}")
            results.append(("destroy → gone", st is SandboxState.DESTROYED))

    print("\n===== results =====")
    ok = True
    for name, passed in results:
        ok = ok and passed
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    print("ALL PASS" if ok else "SOME FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
