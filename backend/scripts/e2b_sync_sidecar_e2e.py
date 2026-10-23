"""LIVE E2B end-to-end for the scope-sync sidecar.

Stands up a git world INSIDE a real E2B sandbox (bare remote + scope clone +
2nd client), uploads sync_sidecar.py, runs the watch daemon, and proves the
two-speed model end-to-end in the real environment:
  edit → auto-checkpoint → quiescence → publish (remote advances) →
  disjoint integrate (in-flight edit preserved) → overlapping edit → CONFLICT.

Run from backend/:  python -m scripts.e2b_sync_sidecar_e2e
"""

from __future__ import annotations

import asyncio
import base64
import os
import time
from pathlib import Path


def _load_env() -> None:
    if os.environ.get("E2B_API_KEY"):
        return
    for line in (Path(__file__).resolve().parents[1] / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("E2B_API_KEY="):
            os.environ["E2B_API_KEY"] = line.split("=", 1)[1].strip()


_load_env()

from src.platform.workspace.scopes.e2b_provider import E2BProvider, SdkE2BClient  # noqa: E402
from src.platform.workspace.scopes.provider import SandboxSpec  # noqa: E402

SIDECAR = Path(__file__).resolve().parents[2] / "sandbox" / "scope-sync-sidecar" / "sync_sidecar.py"

SETUP = r"""
set -e
git config --global user.email t@t.io; git config --global user.name tester
rm -rf ~/world && mkdir -p ~/world && cd ~/world
git init --bare remote.git >/dev/null
git init scope >/dev/null && cd scope
git remote add origin ~/world/remote.git
git checkout -b main >/dev/null 2>&1
echo base > base.txt && git add -A && git commit -m base >/dev/null
git push -u origin main >/dev/null 2>&1
git checkout -b work >/dev/null 2>&1
cd ~/world && git clone -q remote.git client2 && cd client2
git checkout -q main && git checkout -q -b work
echo SETUP_OK
"""


async def main() -> int:
    if not os.environ.get("E2B_API_KEY"):
        print("E2B_API_KEY not set"); return 2
    prov = E2BProvider(SdkE2BClient(timeout=900))
    info = await prov.create(SandboxSpec(scope_id="sync-e2e", project_id="demo"))
    sid = info.sandbox_id
    print(f"created {sid}")
    results: list[tuple[str, bool]] = []

    async def sh(cmd: str) -> str:
        return (await prov.exec(sid, cmd)).get("stdout", "")

    try:
        assert "SETUP_OK" in await sh(SETUP), "git world setup failed"
        print("git world ready")

        b64 = base64.b64encode(SIDECAR.read_bytes()).decode("ascii")
        await sh(f"printf %s '{b64}' | base64 -d > ~/sync_sidecar.py")

        # start the watch daemon, FULLY detached (setsid + </dev/null) so the
        # exec returns instead of waiting on the long-runner's stdin/pipe.
        await sh(
            "SYNC_REPO=$HOME/world/scope SYNC_DEBOUNCE_S=2 SYNC_QUIESCENCE_S=6 SYNC_POLL_S=1 "
            "setsid python3 ~/sync_sidecar.py watch </dev/null >/tmp/sidecar.log 2>&1 & "
            "echo watching"
        )
        time.sleep(2)

        # 1) edit → auto-checkpoint (commit on work appears within debounce)
        await sh("echo 'hello world' > ~/world/scope/doc.md")
        checkpointed = False
        for _ in range(8):
            time.sleep(1.5)
            n = (await sh("git -C ~/world/scope rev-list --count work")).strip()
            if n.isdigit() and int(n) >= 2:
                checkpointed = True; break
        results.append(("edit → auto-checkpoint", checkpointed))

        # 2) quiescence → publish (remote main gets doc.md)
        published = False
        for _ in range(12):
            time.sleep(1.5)
            ls = await sh("git -C ~/world/remote.git ls-tree -r --name-only main")
            if "doc.md" in ls:
                published = True; break
        results.append(("quiescence → publish (remote advanced)", published))
        print("sidecar log:\n" + (await sh("cat /tmp/sidecar.log")).strip())

        # bracket trick so pkill doesn't match (and kill) its own shell cmdline
        await sh("pkill -f '[s]ync_sidecar.py' || true")

        # 3) disjoint integrate on client2: in-flight edit preserved, upstream path pulled
        await sh("echo 'WIP' > ~/world/client2/mine.txt")
        await sh("SYNC_REPO=$HOME/world/client2 python3 ~/sync_sidecar.py integrate doc.md")
        c2_doc = (await sh("cat ~/world/client2/doc.md 2>/dev/null || echo MISSING")).strip()
        c2_mine = (await sh("cat ~/world/client2/mine.txt 2>/dev/null || echo MISSING")).strip()
        results.append(("disjoint integrate (pull doc.md, keep mine.txt)",
                        c2_doc == "hello world" and c2_mine == "WIP"))

        # 4) overlapping edit on client2 → publish CONFLICT
        await sh("echo 'client2 version' > ~/world/client2/doc.md")
        out = await sh("SYNC_REPO=$HOME/world/client2 python3 ~/sync_sidecar.py publish")
        results.append(("overlapping edit → CONFLICT", out.strip().startswith("CONFLICT") and "doc.md" in out))
        print(f"client2 publish → {out.strip()}")
    finally:
        await prov.destroy(sid)
        print(f"destroyed {sid}")

    print("\n===== results =====")
    ok = True
    for name, passed in results:
        ok = ok and passed
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    print("ALL PASS" if ok else "SOME FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
