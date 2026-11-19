"""A Workspace shell and Access service that run the real shell helper locally.

Test-only: never selected by the product provider registry. The SSH gateway and
the HTTPS command endpoint run commands through them exactly as through a
Workspace, so their transports can be tested without a machine.
"""

import asyncio
import base64
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path

HELPER = Path(__file__).parents[2] / "agent-worker/workbench-shell.py"


class NativeShell:
    """Test-only local transport. Never selected by the product provider registry."""

    def __init__(self, directory):
        self.directory = directory
        self.process = None

    async def control(self, action, value=None):
        if action == "start":
            self.process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-u",
                str(HELPER),
                base64.b64encode(json.dumps(value).encode()).decode(),
                cwd=self.directory,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        else:
            self.process.stdin.write(
                json.dumps({"action": action, **(value or {})}).encode() + b"\n"
            )
            await self.process.stdin.drain()
        return {}

    async def receive(self):
        value = await self.process.stdout.readline()
        return json.loads(value) if value else {"type": "disconnected"}

    async def close(self):
        if self.process and self.process.returncode is None:
            self.process.terminate()
            await self.process.wait()


class NativeAccess:
    def __init__(self, key, directory):
        self.key, self.directory, self.used, self.closed = key, directory, False, False
        self.admitted, self.opened, self.renewed, self.finished = 0, 0, 0, []

    async def authenticate(self, identity, key):
        return (
            identity == "fixture"
            and key.export_public_key() == self.key.export_public_key()
            and not self.used
        )

    async def admit(self, identity, notify=None):
        # A credential admits one connection; its commands reuse the admission.
        if self.used:
            raise PermissionError("Used")
        self.used = True
        self.admitted += 1
        return {"id": identity}

    open_error = None

    @asynccontextmanager
    async def open(self, session, notify=None):
        self.opened += 1
        if self.open_error:
            raise self.open_error
        if getattr(self, "resume_notice", None):
            await notify(self.resume_notice)
        shell = NativeShell(self.directory)
        try:
            yield {"id": session["id"]}, shell
        finally:
            await shell.close()
            self.closed = True

    renewal_error = None
    session_error = None

    async def renew(self, *args):
        if self.renewal_error:
            raise self.renewal_error

    async def renew_session(self, session):
        self.renewed += 1
        if self.session_error:
            raise self.session_error

    async def finish(self, session):
        self.finished.append(session["id"])
