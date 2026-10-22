"""Live latency benchmark: E2B vs Fly scope-sandbox providers.

Measures the numbers that actually matter for the "connect into a scope" UX:
cold create (→ running), exec round-trip (median of 5), stop, and resume
(→ running). Both providers run the SAME code path (the real provider classes).
Cheap: one tiny box per provider, destroyed after. Reads creds from backend/.env.

Run from backend/:  python -m scripts.scope_sandbox_bench
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path


def _load_env() -> None:
    for line in (Path(__file__).resolve().parents[1] / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line and (line.startswith("SCOPE_SANDBOX_FLY_") or line.startswith("E2B_API_KEY")):
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v)


_load_env()

from src.platform.workspace.scopes.e2b_provider import E2BProvider, SdkE2BClient  # noqa: E402
from src.platform.workspace.scopes.fly_provider import FlyMachinesProvider  # noqa: E402
from src.platform.workspace.scopes.provider import SandboxSpec, SandboxState  # noqa: E402

mono = time.monotonic


async def _poll(provider, sid, target: SandboxState, timeout_s=60.0) -> float:
    t = mono()
    st = (await provider.status(sid)).state
    while st is not target and mono() - t < timeout_s:
        await asyncio.sleep(1.5)
        st = (await provider.status(sid)).state
    return mono() - t


async def bench(provider, spec, label: str) -> dict:
    r: dict = {"provider": label}
    sid = None
    try:
        t = mono()
        info = await provider.create(spec)
        sid = info.sandbox_id
        r["create_s"] = round(mono() - t, 2)

        lat = []
        for _ in range(5):
            t = mono(); await provider.exec(sid, "echo hi"); lat.append(mono() - t)
        r["exec_ms"] = round(sorted(lat)[len(lat) // 2] * 1000)

        t = mono(); await provider.stop(sid)
        r["stop_s"] = round((mono() - t) + await _poll(provider, sid, SandboxState.STOPPED), 2)

        t = mono(); await provider.start(sid)
        r["resume_s"] = round((mono() - t) + await _poll(provider, sid, SandboxState.RUNNING), 2)
    except Exception as exc:  # noqa: BLE001
        r["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if sid:
            try:
                await provider.destroy(sid)
            except Exception:  # noqa: BLE001
                pass
    return r


async def main() -> int:
    rows = []

    if os.environ.get("E2B_API_KEY"):
        e2b = E2BProvider(SdkE2BClient(timeout=300))
        print("benchmarking E2B…")
        rows.append(await bench(e2b, SandboxSpec(scope_id="bench-e2b", project_id="d"), "E2B"))
    else:
        print("E2B_API_KEY not set — skipping E2B")

    app, tok, img = (os.environ.get("SCOPE_SANDBOX_FLY_APP"),
                     os.environ.get("SCOPE_SANDBOX_FLY_TOKEN"),
                     os.environ.get("SCOPE_SANDBOX_FLY_IMAGE"))
    if app and tok and img:
        fly = FlyMachinesProvider(app, tok, default_image=img)
        print("benchmarking Fly…")
        rows.append(await bench(fly, SandboxSpec(scope_id="bench-fly", project_id="d",
                                                 memory_mb=512, region="sin"), "Fly"))
    else:
        print("SCOPE_SANDBOX_FLY_* not set — skipping Fly")

    cols = ["provider", "create_s", "exec_ms", "stop_s", "resume_s", "error"]
    print("\n===== latency (live) =====")
    print(" | ".join(c.ljust(10) for c in cols))
    for r in rows:
        print(" | ".join(str(r.get(c, "-")).ljust(10) for c in cols))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
