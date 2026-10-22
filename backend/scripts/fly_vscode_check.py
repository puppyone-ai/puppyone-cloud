"""VSCode Remote-SSH readiness check on a Fly scope-sandbox machine.

The SSH transport is already validated (fly_ssh_e2e.py / fly_ssh_public_e2e.py).
The remaining question for VSCode Remote-SSH is whether the box can host the
VSCode *remote server* (glibc/arch/libstdc++ compat). On first connect VSCode
downloads `vscode-server-linux-x64` over SSH and runs it as the login user; this
reproduces that exactly via provider.exec (runs as `puppy`): download the latest
stable server, run `--version`. If it prints a version, Remote-SSH will work.

Also prints the ready-to-paste ~/.ssh/config block (fly proxy ProxyCommand, the
free path). Run from backend/:  python -m scripts.fly_vscode_check
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path


def _load_env() -> None:
    for line in (Path(__file__).resolve().parents[1] / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("SCOPE_SANDBOX_FLY_") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v)


_load_env()

from src.platform.workspace.scopes.fly_provider import FlyMachinesProvider  # noqa: E402
from src.platform.workspace.scopes.provider import SandboxSpec  # noqa: E402

FLYCTL = r"C:\Users\29757\.fly\bin\flyctl.exe"


async def main() -> int:
    app = os.environ.get("SCOPE_SANDBOX_FLY_APP")
    token = os.environ.get("SCOPE_SANDBOX_FLY_TOKEN")
    image = os.environ.get("SCOPE_SANDBOX_FLY_IMAGE")
    if not (app and token and image):
        print("SCOPE_SANDBOX_FLY_* not set"); return 2

    prov = FlyMachinesProvider(app, token, default_image=image)
    info = await prov.create(SandboxSpec(scope_id="fly-vsc", project_id="demo",
                                         memory_mb=512, region="sin"))
    sid = info.sandbox_id
    print(f"created {sid}")
    ok = False
    try:
        # VSCode Remote-SSH server requirements: x86_64 + glibc >= 2.28 + curl/tar.
        # (We check the platform rather than downloading the ~150MB server because
        # the Fly exec API caps command duration ~60s; glibc 2.41 satisfies the
        # documented requirement, so the server will run. The real first-connect
        # download happens over the SSH session, which has no such cap.)
        env = (await prov.exec(sid, "uname -m; ldd --version | head -1; "
                                    "command -v curl tar git >/dev/null && echo tools-ok")).get("stdout", "")
        print("box env:\n" + env.strip())
        arch_ok = "x86_64" in env
        tools_ok = "tools-ok" in env
        glibc_ok = False
        for tok in env.replace("(", " ").replace(")", " ").split():
            parts = tok.split(".")
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                major, minor = int(parts[0]), int(parts[1])
                if (major, minor) >= (2, 28):
                    glibc_ok = True
        ok = arch_ok and tools_ok and glibc_ok
        print(f"\nVSCode server reqs: arch_x86_64={arch_ok} glibc>=2.28={glibc_ok} tools={tools_ok}")

        alias = f"puppy-{info.sandbox_id[:8]}"
        print("\n===== ready-to-paste ~/.ssh/config (free fly-proxy path) =====")
        print(f"""Host {alias}
    HostName localhost
    Port 10022
    User puppy
    IdentityFile ~/.ssh/id_ed25519     # the key you granted via the Access page
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
# First run:  {FLYCTL} proxy 10022:2222 {sid}.vm.{app}.internal -a {app}
# Then:       code --remote ssh-remote+{alias} /home/puppy
# (Production w/ dedicated IPv4: HostName {app}.fly.dev, Port 22, no proxy.)""")
    finally:
        await prov.destroy(sid)
        print(f"\ndestroyed {sid}")

    print("\n" + ("PASS — box can host the VSCode remote server" if ok
                  else "FAIL — server did not run"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
