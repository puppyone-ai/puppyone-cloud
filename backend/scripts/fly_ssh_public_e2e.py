"""LIVE Fly SSH over the PUBLIC :22 ingress (dedicated IPv4) — production form.

Unlike fly_ssh_e2e.py (which tunnels via `fly proxy`/WireGuard), this connects
exactly the way the FlyMachinesProvider's ConnectionInfo describes:
`ssh puppy@<app>.fly.dev` over raw public TCP :22 → machine sshd :2222 (Fly
services map). Requires a dedicated IPv4 (~$2/mo) to be allocated on the app.

create → grant alice → ssh <app>.fly.dev (allowed) → revoke → ssh (denied) →
destroy. Run from backend/:  python -m scripts.fly_ssh_public_e2e
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
from pathlib import Path


def _load_env() -> None:
    for line in (Path(__file__).resolve().parents[1] / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("SCOPE_SANDBOX_FLY_") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v)


_load_env()

from src.platform.workspace.scopes import ssh_credentials  # noqa: E402
from src.platform.workspace.scopes.fly_provider import FlyMachinesProvider  # noqa: E402
from src.platform.workspace.scopes.provider import SandboxSpec  # noqa: E402

KEY_DIR = Path(r"C:\Users\29757\PuppyNew\.fly_ssh_test")
REGION = "sin"
FLYCTL = r"C:\Users\29757\.fly\bin\flyctl.exe"


def _public_ipv4(app: str) -> str | None:
    """The app's dedicated IPv4 (the .fly.dev A record can lag allocation, so we
    connect to the IP directly to validate raw-TCP :22 immediately)."""
    import json
    r = subprocess.run([FLYCTL, "ips", "list", "-a", app, "--json"],
                       capture_output=True, text=True, timeout=30)
    try:
        for ip in json.loads(r.stdout):
            if ip.get("Type", "").startswith("v4") or ip.get("address", "").count(".") == 3:
                return ip.get("Address") or ip.get("address")
    except Exception:
        pass
    return None


def _alice_key() -> Path:
    KEY_DIR.mkdir(exist_ok=True)
    key = KEY_DIR / "alice"
    if not key.exists():
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key), "-C", "alice-fly"],
                       check=True, capture_output=True)
    return key


def _ssh_ok(key: Path, host: str) -> bool:
    r = subprocess.run(
        ["ssh", "-F", "/dev/null", "-i", str(key),
         "-o", "IdentitiesOnly=yes", "-o", "ControlMaster=no", "-o", "ControlPath=none",
         "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
         "-o", "BatchMode=yes", "-o", "ConnectTimeout=25",
         f"puppy@{host}", "echo OK"],
        capture_output=True, text=True, timeout=70,
    )
    if r.returncode != 0:
        print(f"   ssh→{host} rc={r.returncode} err={r.stderr.strip().splitlines()[-1] if r.stderr else ''}")
    return r.returncode == 0 and "OK" in r.stdout


async def main() -> int:
    app = os.environ.get("SCOPE_SANDBOX_FLY_APP")
    token = os.environ.get("SCOPE_SANDBOX_FLY_TOKEN")
    image = os.environ.get("SCOPE_SANDBOX_FLY_IMAGE")
    if not (app and token and image):
        print("SCOPE_SANDBOX_FLY_* not set"); return 2

    prov = FlyMachinesProvider(app, token, default_image=image)
    alice = _alice_key()
    apub = alice.with_suffix(".pub").read_text().strip()

    info = await prov.create(SandboxSpec(scope_id="fly-pub", project_id="demo",
                                         memory_mb=512, region=REGION))
    sid = info.sandbox_id
    # Production ConnectionInfo host is <app>.fly.dev, but its DNS A record can lag
    # IPv4 allocation by minutes — connect to the dedicated IP directly to validate
    # the raw-TCP :22 ingress immediately.
    host = _public_ipv4(app) or info.connection.host
    print(f"created {sid}; connecting via public :22 host={host} "
          f"(ConnectionInfo host={info.connection.host}, proxy={info.connection.proxy_command})")

    results: list[tuple[str, bool, bool]] = []
    try:
        await ssh_credentials.grant_ssh_access(prov, sid, "alice", apub, expires_at=time.time() + 3600)
        print("granted alice; waiting for public TCP :22 routing to settle…")
        time.sleep(20)  # machine boot + service registration + edge routing

        # retry a couple times — public routing can take a few extra seconds
        allowed = any(_ssh_ok(alice, host) or (time.sleep(8) or False) for _ in range(3))
        results.append(("granted → public :22 SSH allowed", allowed, True))

        await ssh_credentials.revoke_ssh_access(prov, sid, "alice")
        time.sleep(2)
        results.append(("revoked → public :22 SSH denied", _ssh_ok(alice, host), False))
    finally:
        await prov.destroy(sid)
        print(f"destroyed {sid}")

    print("\n===== results =====")
    ok = True
    for step, got, want in results:
        ok = ok and got == want
        print(f"  [{'PASS' if got == want else 'FAIL'}] {step}: allowed={got} (want {want})")
    print("ALL PASS" if ok else "SOME FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
