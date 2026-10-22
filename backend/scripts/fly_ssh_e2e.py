"""LIVE Fly SSH end-to-end over `fly proxy` (WireGuard) — no dedicated IPv4.

Proves the human VSCode-Remote-SSH path against the hardened, publickey-only
sshd baked into the Fly image: create machine → grant alice → connect (allowed)
→ revoke alice → connect (denied). Connectivity is tunnelled with `fly proxy`
(local port → machine:2222 over the private 6PN/WireGuard net), which is FREE —
the public TCP :22 path additionally needs a paid dedicated IPv4.

Run from backend/:  python -m scripts.fly_ssh_e2e
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

FLYCTL = r"C:\Users\29757\.fly\bin\flyctl.exe"
KEY_DIR = Path(r"C:\Users\29757\PuppyNew\.fly_ssh_test")
LOCAL_PORT = 10022
REGION = "sin"


def _alice_key() -> Path:
    KEY_DIR.mkdir(exist_ok=True)
    key = KEY_DIR / "alice"
    if not key.exists():
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key), "-C", "alice-fly"],
                       check=True, capture_output=True)
    return key


def _ssh_ok(key: Path) -> bool:
    r = subprocess.run(
        ["ssh", "-F", "/dev/null", "-i", str(key),
         "-o", "IdentitiesOnly=yes", "-o", "ControlMaster=no", "-o", "ControlPath=none",
         "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
         "-o", "BatchMode=yes", "-o", "ConnectTimeout=20",
         "-p", str(LOCAL_PORT), "puppy@localhost", "echo OK"],
        capture_output=True, text=True, timeout=60,
    )
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

    info = await prov.create(SandboxSpec(scope_id="fly-ssh", project_id="demo",
                                         memory_mb=512, region=REGION))
    sid = info.sandbox_id
    print(f"created {sid}")
    proxy = None
    results: list[tuple[str, bool, bool]] = []
    try:
        await ssh_credentials.grant_ssh_access(prov, sid, "alice", apub, expires_at=time.time() + 3600)
        print("granted alice; starting fly proxy (WireGuard)…")
        # Target the specific machine over 6PN so the proxy can't race to a sibling.
        proxy = subprocess.Popen(
            [FLYCTL, "proxy", f"{LOCAL_PORT}:2222", f"{sid}.vm.{app}.internal", "-a", app],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        time.sleep(12)  # let WireGuard tunnel + sshd come up

        results.append(("granted alice → SSH allowed", _ssh_ok(alice), True))

        await ssh_credentials.revoke_ssh_access(prov, sid, "alice")
        time.sleep(1)
        results.append(("revoked alice → SSH denied", _ssh_ok(alice), False))
    finally:
        if proxy:
            proxy.terminate()
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
