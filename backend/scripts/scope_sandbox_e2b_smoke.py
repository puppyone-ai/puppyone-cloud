"""LIVE E2B smoke test for the scope-sandbox feature (costs a little real money).

Exercises the real E2B path end to end through E2BProvider + ScopeSandboxManager:
create → exec real commands → pause(stop) → connect(resume) → exec again (prove
the working copy survived the stop) → kill(destroy). Times every phase and
prints a cost estimate.

Run from backend/ with E2B_API_KEY available (read from backend/.env):
    python -m scripts.scope_sandbox_e2b_smoke
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path


def _load_env_key() -> None:
    if os.environ.get("E2B_API_KEY"):
        return
    env = Path(__file__).resolve().parents[1] / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("E2B_API_KEY="):
            os.environ["E2B_API_KEY"] = line.split("=", 1)[1].strip()
            return


_load_env_key()

from src.platform.workspace.scopes.e2b_provider import E2BProvider, SdkE2BClient  # noqa: E402
from src.platform.workspace.scopes.manager import ScopeSandboxManager  # noqa: E402
from src.platform.workspace.scopes.policy import SessionPolicyConfig  # noqa: E402
from src.platform.workspace.scopes.provider import SandboxSpec  # noqa: E402
from src.platform.workspace.scopes.registry import InMemorySandboxSessionStore  # noqa: E402

# E2B published rates (e2b.dev/pricing, 2026-06) for a rough cost estimate.
VCPU_PER_HR = 0.0504
GIB_PER_HR = 0.0162
ASSUMED_VCPU = 2     # E2B default sandbox
ASSUMED_GIB = 1


def _bn(title: str) -> None:
    print(f"\n=== {title} ===")


async def main() -> int:
    if not os.environ.get("E2B_API_KEY"):
        print("E2B_API_KEY not set (checked env + backend/.env). Aborting.")
        return 2

    timings: dict[str, float] = {}
    provider = E2BProvider(SdkE2BClient())
    # Aggressive stop policy so the reaper stops the sandbox immediately after
    # release — lets us measure a real manager-driven stop→resume cycle.
    cfg = SessionPolicyConfig(
        base_idle_stop_s=0, idle_destroy_s=99_999,
        per_recent_user_bonus_s=0, repo_pull_weight=0, hot_warm_bonus_s=0,
    )
    mgr = ScopeSandboxManager(provider, InMemorySandboxSessionStore(), cfg)
    spec = SandboxSpec(scope_id="smoke-scope", project_id="smoke")
    started = time.monotonic()

    async def timed(label, coro):
        t0 = time.monotonic()
        out = await coro
        timings[label] = time.monotonic() - t0
        print(f"  [{label}] {timings[label]:.2f}s")
        return out

    _bn("1) acquire (cold create)")
    res = await timed("create", mgr.acquire(spec, "alice"))
    sid = res.session.sandbox_id
    print(f"  via={res.via.value} sandbox_id={sid}")
    print(f"  connection={res.session.connection}")

    _bn("2) real exec examples")
    for cmd in [
        "echo hello-from-puppyone",
        "python3 --version || python --version",
        "uname -a",
        "git clone --depth 1 https://github.com/octocat/Hello-World /tmp/hw 2>&1 | tail -1 && ls /tmp/hw",
    ]:
        out = await timed(f"exec: {cmd[:32]}", provider.exec(sid, cmd))
        print(f"      rc={out.get('exit_code')} out={(out.get('stdout') or '').strip()[:120]!r}")

    _bn("3) release + reap → manager-driven stop (pause, keeps FS/memory)")
    await mgr.release("smoke-scope", "alice")
    summary = await timed("reap-stop", mgr.reap())
    print(f"  reap: stopped={summary.stopped} (registry now STOPPED)")

    _bn("4) acquire again → resume via connect")
    res2 = await timed("resume", mgr.acquire(spec, "bob"))
    print(f"  via={res2.via.value} (expect 'resumed')")

    _bn("5) exec after resume — prove the working copy survived the stop")
    out = await timed("exec: ls /tmp/hw (post-resume)", provider.exec(sid, "ls /tmp/hw"))
    print(f"      rc={out.get('exit_code')} out={(out.get('stdout') or '').strip()[:120]!r}")
    survived = out.get("exit_code") == 0 and (out.get("stdout") or "").strip()

    _bn("6) destroy (kill)")
    await timed("kill", mgr.kill_scope("smoke-scope"))

    total = time.monotonic() - started
    est = (ASSUMED_VCPU * VCPU_PER_HR + ASSUMED_GIB * GIB_PER_HR) * (total / 3600.0)

    _bn("SUMMARY")
    for k, v in timings.items():
        print(f"  {k:32} {v:6.2f}s")
    print(f"  {'TOTAL wall':32} {total:6.2f}s")
    print(f"  working copy survived stop/resume: {bool(survived)}")
    print(f"  est. compute cost (~{ASSUMED_VCPU}vCPU/{ASSUMED_GIB}GiB @ E2B rates): ${est:.4f}")
    print("  (note: pause→$0 compute; this run billed only while RUNNING)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
