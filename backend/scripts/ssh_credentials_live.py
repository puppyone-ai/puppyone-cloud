"""LIVE validation of per-user short-lived/revocable SSH (roadmap #5/#7).

Proves the governance core against a REAL E2B sandbox + real sshd:
  1. grant alice (valid TTL)  → ssh with alice's key SUCCEEDS
  2. grant alice (past TTL)   → ssh DENIED (sshd honors expiry-time = short-lived)
  3. re-grant + revoke alice  → ssh DENIED (line removed = 离职即失权)
The box keeps a separate bootstrap key the whole time, so "denied" means alice's
line specifically is gone/expired, not that sshd broke.

Run from backend/ (reads E2B_API_KEY from backend/.env):
    python -m scripts.ssh_credentials_live
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
from pathlib import Path


def _load_key() -> None:
    if os.environ.get("E2B_API_KEY"):
        return
    for line in (Path(__file__).resolve().parents[1] / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("E2B_API_KEY="):
            os.environ["E2B_API_KEY"] = line.split("=", 1)[1].strip()


_load_key()

from src.platform.workspace.scopes import ssh_credentials as sc  # noqa: E402
from src.platform.workspace.scopes.e2b_provider import E2BProvider, SdkE2BClient  # noqa: E402
from src.platform.workspace.scopes.provider import SandboxSpec  # noqa: E402
from src.platform.workspace.scopes.ssh_e2b import (  # noqa: E402
    DEFAULT_FORWARD_PORT,
    provision_e2b_ssh,
)

KEY_DIR = Path(r"C:\Users\29757\PuppyNew\.e2b_ssh_cred_test")
# Forward slashes: ssh runs ProxyCommand via bash, which would eat backslashes.
WEBSOCAT_WIN = "C:/Users/29757/.local/bin/websocat.exe"


def _keypair(name: str) -> Path:
    KEY_DIR.mkdir(exist_ok=True)
    key = KEY_DIR / name
    if not key.exists():
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key), "-C", name],
            check=True, capture_output=True,
        )
    return key


def _ssh_ok(key: Path, wss_host: str) -> bool:
    """True iff `ssh -i key` authenticates and runs a command."""
    proxy = f'{WEBSOCAT_WIN} --binary -B 65536 - wss://{wss_host}'
    r = subprocess.run(
        ["ssh", "-F", "/dev/null", "-i", str(key),  # ignore user's ~/.ssh/config (git-bash ssh)
         "-o", f"ProxyCommand={proxy}",
         "-o", "IdentitiesOnly=yes",  # offer ONLY this key (no agent/default fallback)
         # force a fresh auth every call — never reuse a multiplexed master conn
         "-o", "ControlMaster=no", "-o", "ControlPath=none",
         "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
         "-o", "BatchMode=yes", "-o", "ConnectTimeout=20",
         "user@e2b", "echo OK"],
        capture_output=True, text=True, timeout=60,
    )
    return r.returncode == 0 and "OK" in r.stdout


async def main() -> int:
    if not os.environ.get("E2B_API_KEY"):
        print("E2B_API_KEY not set"); return 2

    boot = _keypair("bootstrap")
    alice = _keypair("alice")
    alice_pub = alice.with_suffix(".pub").read_text().strip()

    provider = E2BProvider(SdkE2BClient(timeout=900))
    info = await provider.create(SandboxSpec(scope_id="ssh-cred", project_id="demo"))
    sid = info.sandbox_id
    wss = f"{DEFAULT_FORWARD_PORT}-{sid}.e2b.app"
    print(f"created {sid}")
    await provision_e2b_ssh(provider, sid, boot.with_suffix(".pub").read_text().strip(),
                            forward_port=DEFAULT_FORWARD_PORT)
    print("sshd+websocat up (bootstrap key installed)")

    results: list[tuple[str, bool, bool]] = []  # (step, got, want)

    # 1) valid grant → allowed
    await sc.grant_ssh_access(provider, sid, "alice", alice_pub, expires_at=time.time() + 3600)
    await asyncio.sleep(1)
    results.append(("valid grant → allowed", _ssh_ok(alice, wss), True))

    # 2) expired grant → denied (sshd enforces expiry-time)
    await sc.grant_ssh_access(provider, sid, "alice", alice_pub, expires_at=time.time() - 60)
    await asyncio.sleep(1)
    results.append(("expired grant → denied", _ssh_ok(alice, wss), False))

    # 3) re-grant valid then revoke → denied
    await sc.grant_ssh_access(provider, sid, "alice", alice_pub, expires_at=time.time() + 3600)
    await asyncio.sleep(1)
    regranted = _ssh_ok(alice, wss)
    await sc.revoke_ssh_access(provider, sid, "alice")
    await asyncio.sleep(1)
    results.append(("re-grant → allowed", regranted, True))
    results.append(("revoke → denied", _ssh_ok(alice, wss), False))

    # bootstrap key must still work the whole time (proves sshd itself is fine)
    results.append(("bootstrap key → allowed", _ssh_ok(boot, wss), True))

    print("\n===== results =====")
    ok = True
    for step, got, want in results:
        mark = "PASS" if got == want else "FAIL"
        ok = ok and got == want
        print(f"  [{mark}] {step}: got allowed={got} (want {want})")

    await provider.destroy(sid)
    print(f"\ndestroyed {sid}")
    print("ALL PASS" if ok else "SOME FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
