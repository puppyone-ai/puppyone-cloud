"""LIVE demo: stand up SSH on an E2B sandbox and print a VSCode Remote-SSH config.

Drives the real path through E2BProvider + the ssh_e2b provisioning helpers:
create → provision (sshd + websocat) → print the ~/.ssh/config block + a ready
ssh command. Leaves the sandbox running (until its timeout) so you can connect.

Run from backend/ (reads E2B_API_KEY from backend/.env):
    python -m scripts.e2b_ssh_demo
Then paste the printed block into ~/.ssh/config and: code --remote ssh-remote+<alias> /home/user
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

from src.platform.workspace.scopes.e2b_provider import E2BProvider, SdkE2BClient  # noqa: E402
from src.platform.workspace.scopes.provider import SandboxSpec  # noqa: E402
from src.platform.workspace.scopes.ssh_e2b import (  # noqa: E402
    DEFAULT_FORWARD_PORT,
    provision_e2b_ssh,
    vscode_ssh_config_block,
)

KEY_DIR = Path(r"C:\Users\29757\PuppyNew\.e2b_ssh_test")
WEBSOCAT_WIN = r"C:\Users\29757\.local\bin\websocat.exe"


def _ensure_keypair() -> Path:
    KEY_DIR.mkdir(exist_ok=True)
    key = KEY_DIR / "id_ed25519"
    if not key.exists():
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key), "-C", "puppyone-e2b"],
            check=True, capture_output=True,
        )
    return key


async def main() -> int:
    if not os.environ.get("E2B_API_KEY"):
        print("E2B_API_KEY not set"); return 2
    key = _ensure_keypair()
    pub = (key.with_suffix(".pub")).read_text().strip()

    provider = E2BProvider(SdkE2BClient(timeout=900))
    t0 = time.monotonic()
    info = await provider.create(SandboxSpec(scope_id="ssh-demo", project_id="demo"))
    sid = info.sandbox_id
    print(f"created {sid} in {time.monotonic()-t0:.2f}s")

    t1 = time.monotonic()
    await provision_e2b_ssh(provider, sid, pub, forward_port=DEFAULT_FORWARD_PORT)
    print(f"provisioned sshd+websocat in {time.monotonic()-t1:.2f}s")

    wss_host = f"{DEFAULT_FORWARD_PORT}-{sid}.e2b.app"
    alias = "puppy-e2b"
    block = vscode_ssh_config_block(
        host_alias=alias, wss_host=wss_host, key_path=str(key), websocat_path=WEBSOCAT_WIN,
    )
    print("\n===== add to ~/.ssh/config (VSCode Remote-SSH) =====\n" + block)
    print("===== or one-shot ssh test =====")
    print(
        f'ssh -i "{key}" -o ProxyCommand="{WEBSOCAT_WIN} --binary -B 65536 - wss://{wss_host}" '
        f'-o StrictHostKeyChecking=no -o UserKnownHostsFile=NUL user@{alias} hostname'
    )
    print(f"\nVSCode: code --remote ssh-remote+{alias} /home/user")
    print(f"(sandbox {sid} left running ~15 min; it auto-expires)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
