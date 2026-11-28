"""End-to-end contract tests for Tier 1 (shared_machine) multi-Agent POSIX collaboration.

Runs TWO independent external Agents (Agent A & Agent B) signing requests with
their own Ed25519 keys over real TLS HTTPS ``/exec`` against ONE shared
workspace directory backed by the real ``workbench-shell.py`` helper and the
real ``workbench-files.py`` Desktop file engine.

Covers:
1. Full POSIX & Bash compatibility matrix (mv/os.replace, sed -i, O_APPEND,
   O_RDWR seek+write, truncate, chmod, utime, symlink, hardlink, flock,
   sqlite3 WAL, tar/gzip, grep/find, Unicode .md/.html).
2. Immediate cross-Agent and Desktop live-file visibility (zero merge step).
3. Concurrent dual-Agent writes: disjoint burst, same-file atomic replace with
   continuous reader (zero torn reads / zero EIO), same-file locked appends
   (zero lost lines), and concurrent sqlite3 WAL transactions.
4. Desktop (``workbench-files.py``) <-> Agent A <-> Agent B 3-way edit and
   optimistic-revision conflict detection + filesystem change watcher.
5. Mid-command revocation of Agent A kills ONLY Agent A while Agent B keeps
   running unaffected on the same shared workspace.
6. Small-file throughput sanity and ``git-gate.sh`` branch protection.
"""

from __future__ import annotations

import asyncio
import base64
import datetime
import importlib.util
import ipaddress
import os
import shutil
import socket
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi import FastAPI
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from src.config import settings
from src.platform.access import agent_link_router as routes
from src.platform.access.adapters import shell_session
from src.platform.access.adapters.https.commands import AgentCommands, SeenSignatures
from src.platform.access.adapters.https.signature import command_message
from src.platform.access.agent_links import RUN_HELPER, https_access
from src.platform.workspace.errors import WorkspaceError
from tests.support.native_shell import NativeShell

pytestmark = pytest.mark.skipif(
    not (shutil.which("ssh-keygen") and shutil.which("curl")),
    reason="needs ssh-keygen and curl",
)

AGENT_WORKER_DIR = Path(__file__).resolve().parents[4] / "agent-worker"


def _load_workbench_files():
    spec = importlib.util.spec_from_file_location(
        "puppyone_workbench_files", AGENT_WORKER_DIR / "workbench-files.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _desktop_exec(workspace_dir: Path, request: dict) -> dict:
    """Invoke the real ``workbench-files.py`` engine directly against ``workspace_dir``."""
    wf = _load_workbench_files()
    root_fd = os.open(str(workspace_dir), wf.DIRECTORY)
    try:
        try:
            return {"result": wf.execute(root_fd, request)}
        except wf.Conflict as error:
            return {"error": "file_conflict", "message": str(error)}
        except FileNotFoundError:
            return {"error": "file_not_found"}
        except FileExistsError:
            return {"error": "file_conflict"}
    finally:
        os.close(root_fd)


class SharedWorkspaceAccess:
    """Multi-Agent shared-machine Access fixture using real ``workbench-shell.py`` shells."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.revoked_identities: set[str] = set()
        self.admitted: list[str] = []
        self.finished: list[str] = []
        self.active_shells: dict[str, int] = {}

    async def admit(self, identity: str, notify=None):
        if identity in self.revoked_identities:
            raise WorkspaceError(403, {"code": "access_denied"})
        self.admitted.append(identity)
        return {"id": identity}

    @asynccontextmanager
    async def open(self, session, notify=None):
        identity = session["id"]
        if identity in self.revoked_identities:
            raise WorkspaceError(403, {"code": "access_denied"})
        shell = NativeShell(self.directory)
        self.active_shells[identity] = self.active_shells.get(identity, 0) + 1
        try:
            yield {"id": identity}, shell
        finally:
            await shell.close()
            self.active_shells[identity] = max(0, self.active_shells.get(identity, 1) - 1)

    async def renew(self, connection, shell):
        if connection["id"] in self.revoked_identities:
            raise WorkspaceError(403, {"code": "access_denied"})

    async def renew_session(self, session):
        if session["id"] in self.revoked_identities:
            raise WorkspaceError(403, {"code": "access_denied"})

    async def finish(self, session):
        self.finished.append(session["id"])


def _self_signed_cert(directory: Path) -> tuple[Path, Path]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_file, key_file = directory / "cert.pem", directory / "key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_file, key_file


class LocalAgentClient:
    """An external Agent with its own SSH keypair and ``~/.puppyone/run`` helper."""

    def __init__(self, directory: Path, connection: str, cert: Path):
        self.directory = directory
        self.connection = connection
        self.identity = f"agent-{connection}"
        (directory / ".ssh").mkdir(mode=0o700, parents=True, exist_ok=True)
        self.key = directory / ".ssh/puppyone_agent"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(self.key)],
            check=True,
        )
        self.public_key = " ".join(self.key.with_suffix(".pub").read_text().split()[:2])
        self.env = {
            "PATH": os.environ["PATH"],
            "HOME": str(directory),
            "CURL_CA_BUNDLE": str(cert),
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
            "HTTP_PROXY": "",
            "HTTPS_PROXY": "",
            "http_proxy": "",
            "https_proxy": "",
            "ALL_PROXY": "",
            "all_proxy": "",
        }
        self.run_path = directory / ".puppyone/run"

    def install(self, access: dict) -> None:
        subprocess.run(["sh", "-c", access["install"]], check=True, env=self.env)
        self.run_path.write_text(RUN_HELPER)
        self.run_path.chmod(0o700)

    async def run(self, command: str, *, timeout: float = 45.0) -> tuple[int, str, str]:
        process = await asyncio.create_subprocess_exec(
            str(self.run_path),
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self.env,
        )
        async with asyncio.timeout(timeout):
            out, err = await process.communicate()
        return process.returncode, out.decode(), err.decode()

    def sign(self, message: bytes) -> str:
        target = self.directory / f"msg-{uuid4().hex}"
        target.write_bytes(message)
        subprocess.run(
            ["ssh-keygen", "-Y", "sign", "-f", str(self.key), "-n", "puppyone-agent", str(target)],
            check=True,
            capture_output=True,
        )
        armored = target.with_name(target.name + ".sig").read_text().splitlines()
        return "".join(armored[1:-1])


@asynccontextmanager
async def shared_collab_harness(tmp_path: Path, monkeypatch):
    workspace_dir = tmp_path / "shared_workspace"
    workspace_dir.mkdir()
    cert, key = _self_signed_cert(tmp_path)

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    origin = f"https://127.0.0.1:{listener.getsockname()[1]}"

    agent_a = LocalAgentClient(tmp_path / "agent_a", str(uuid4()), cert)
    agent_b = LocalAgentClient(tmp_path / "agent_b", str(uuid4()), cert)
    keys = {agent_a.connection: agent_a.public_key, agent_b.connection: agent_b.public_key}

    service = SharedWorkspaceAccess(workspace_dir)

    async def admits(connection: str, presented: str) -> bool:
        if f"agent-{connection}" in service.revoked_identities:
            return False
        return keys.get(connection) == presented

    commands = AgentCommands(service, seen=SeenSignatures(url=""), admits=admits)
    monkeypatch.setattr(settings, "ACCESS_LIFECYCLE_ENABLED", True)
    monkeypatch.setattr(settings, "PUBLIC_URL", origin)
    monkeypatch.setattr(settings, "CLOUD_AGENT_COLLAB_MODE", "shared_machine")

    agent_a.install(https_access(agent_a.connection))
    agent_b.install(https_access(agent_b.connection))

    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.agent_commands] = lambda: commands
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            log_level="error",
            access_log=False,
            ssl_certfile=str(cert),
            ssl_keyfile=str(key),
        )
    )
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(10):
            while not server.started:
                await asyncio.sleep(0.02)
        async with httpx.AsyncClient(
            base_url=origin, verify=str(cert), timeout=20, trust_env=False
        ) as client:
            yield agent_a, agent_b, workspace_dir, service, client
    finally:
        server.should_exit = True
        await task


async def test_full_posix_and_bash_matrix_over_https_exec(tmp_path, monkeypatch):
    """Verify every POSIX operation required by external Agents works cleanly."""
    script = r"""
set -eu
mkdir -p posix/sub "笔记" "页面"

# 1. Heredoc + O_APPEND (>>)
cat > posix/doc.md <<'EOF'
# Title
line 1
EOF
printf 'line 2\n' >> posix/doc.md
grep -q 'line 2' posix/doc.md

# 2. mv & atomic rename / overwrite across dirs
printf 'v1' > posix/a.txt
printf 'v2' > posix/a.tmp
mv posix/a.tmp posix/a.txt
test "$(cat posix/a.txt)" = "v2"
mv posix/a.txt posix/sub/moved.txt
test "$(cat posix/sub/moved.txt)" = "v2"

# 3. sed in-place on .md and .html (GNU and BSD portable)
printf '<h1>Hello Old</h1>\n' > "页面/首页.html"
if sed --version >/dev/null 2>&1; then
  sed -i 's/Old/New/g' "页面/首页.html"
else
  sed -i '' 's/Old/New/g' "页面/首页.html"
fi
grep -q '<h1>Hello New</h1>' "页面/首页.html"

# 4. Python full POSIX probe: O_RDWR seek+write, truncate, chmod, utime,
#    symlink, hardlink, os.replace, fcntl.flock, sqlite3 WAL transaction
python3 - <<'PY'
import fcntl, os, sqlite3, stat

# O_RDWR seek + overwrite + ftruncate
p = "posix/rw.bin"
with open(p, "w+b") as f:
    f.write(b"0123456789")
    f.seek(3)
    f.write(b"ABC")
    f.flush()
    f.seek(0)
    assert f.read() == b"012ABC6789"
    f.truncate(6)
with open(p, "rb") as f:
    assert f.read() == b"012ABC"

# chmod + utime
os.chmod(p, 0o755)
assert stat.S_IMODE(os.stat(p).st_mode) == 0o755
os.utime(p, (1700000000, 1700000000))
assert int(os.stat(p).st_mtime) == 1700000000

# symlink + hardlink + atomic os.replace
os.symlink("rw.bin", "posix/sym.bin")
assert os.readlink("posix/sym.bin") == "rw.bin"
os.link(p, "posix/hard.bin")
assert os.stat(p).st_ino == os.stat("posix/hard.bin").st_ino
with open("posix/next.bin", "wb") as f:
    f.write(b"REPLACED")
os.replace("posix/next.bin", "posix/target.bin")
assert open("posix/target.bin", "rb").read() == b"REPLACED"

# fcntl.flock
with open("posix/lockfile", "w") as lf:
    fcntl.flock(lf.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    lf.write("locked")
    fcntl.flock(lf.fileno(), fcntl.LOCK_UN)

# sqlite3 WAL mode + transaction
conn = sqlite3.connect("posix/app.db")
conn.execute("PRAGMA journal_mode=WAL;")
conn.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT)")
conn.execute("INSERT INTO items(name) VALUES ('alpha'), ('beta')")
conn.commit()
assert conn.execute("SELECT count(*) FROM items").fetchone()[0] == 2
conn.close()

# Unicode Markdown file
with open("笔记/报告.md", "w", encoding="utf-8") as f:
    f.write("# 中文报告\n测试通过\n")
PY

# 5. tar + gzip round-trip & find
tar -czf posix/bundle.tar.gz posix/doc.md "笔记/报告.md"
mkdir -p posix/extract
tar -xzf posix/bundle.tar.gz -C posix/extract
test -f "posix/extract/笔记/报告.md"
echo "POSIX_MATRIX_PASS"
"""
    async with shared_collab_harness(tmp_path, monkeypatch) as (agent_a, _, _, _, _):
        code, out, err = await agent_a.run(script)
        assert code == 0, f"stdout={out}\nstderr={err}"
        assert "POSIX_MATRIX_PASS" in out


async def test_two_agents_immediate_cross_visibility_on_md_and_html(tmp_path, monkeypatch):
    async with shared_collab_harness(tmp_path, monkeypatch) as (
        agent_a,
        agent_b,
        workspace_dir,
        _,
        _,
    ):
        # Agent A writes .md and .html
        code, out, _ = await agent_a.run(
            "mkdir -p docs web && "
            "printf '# Spec v1\\nOwner: A\\n' > docs/spec.md && "
            "printf '<!doctype html><html><body><h1>Draft</h1></body></html>\\n' > web/index.html"
        )
        assert code == 0, out

        # Agent B immediately sees exact content and edits both in place
        code, out, _ = await agent_b.run(
            "cat docs/spec.md && "
            "python3 -c \"p='docs/spec.md'; s=open(p).read().replace('v1','v2')+'Reviewed: B\\n'; "
            "open(p+'.tmp','w').write(s); import os; os.replace(p+'.tmp', p)\" && "
            "printf '<p>Added by B</p>\\n' >> web/index.html"
        )
        assert code == 0, out
        assert "# Spec v1" in out

        # Agent A immediately sees Agent B's edits
        code, out, _ = await agent_a.run("cat docs/spec.md && cat web/index.html")
        assert code == 0, out
        assert "# Spec v2" in out
        assert "Reviewed: B" in out
        assert "<p>Added by B</p>" in out

        # Desktop workbench-files.py sees the exact live files immediately
        listed = _desktop_exec(workspace_dir, {"action": "list", "path": "docs"})
        assert [e["name"] for e in listed["result"]["entries"]] == ["spec.md"]
        read = _desktop_exec(workspace_dir, {"action": "read", "path": "docs/spec.md"})
        content = base64.b64decode(read["result"]["content_base64"]).decode()
        assert content == "# Spec v2\nOwner: A\nReviewed: B\n"


async def test_two_agents_concurrent_writes_atomic_no_torn_reads_and_sqlite_wal(
    tmp_path, monkeypatch
):
    """Stress two Agents writing simultaneously: disjoint files, same-file atomic replace,
    same-file flock append, and concurrent SQLite WAL transactions."""
    async with shared_collab_harness(tmp_path, monkeypatch) as (
        agent_a,
        agent_b,
        workspace_dir,
        _,
        _,
    ):
        # 1. Disjoint parallel burst (40 .md by A + 40 .html by B simultaneously)
        cmd_a_burst = (
            "mkdir -p burst_a && python3 -c \""
            "import pathlib; "
            "[pathlib.Path(f'burst_a/doc_{i:02d}.md').write_text(f'# A-{i:02d}\\n') for i in range(40)]\""
        )
        cmd_b_burst = (
            "mkdir -p burst_b && python3 -c \""
            "import pathlib; "
            "[pathlib.Path(f'burst_b/page_{i:02d}.html').write_text(f'<p>B-{i:02d}</p>\\n') for i in range(40)]\""
        )
        (code_a, out_a, _), (code_b, out_b, _) = await asyncio.gather(
            agent_a.run(cmd_a_burst), agent_b.run(cmd_b_burst)
        )
        assert code_a == 0, out_a
        assert code_b == 0, out_b
        assert len(list((workspace_dir / "burst_a").glob("*.md"))) == 40
        assert len(list((workspace_dir / "burst_b").glob("*.html"))) == 40
        assert (workspace_dir / "burst_a/doc_39.md").read_text() == "# A-39\n"
        assert (workspace_dir / "burst_b/page_39.html").read_text() == "<p>B-39</p>\n"

        # 2. Same-file concurrent atomic replace + continuous reader (zero torn reads, zero EIO)
        shared_live = workspace_dir / "live.md"
        shared_live.write_text("INIT:" + ("0" * 2048) + ":END\n")
        stop_reading = asyncio.Event()
        read_errors: list[str] = []
        reads_checked = 0

        async def continuous_reader():
            nonlocal reads_checked
            while not stop_reading.is_set():
                try:
                    data = shared_live.read_text()
                    reads_checked += 1
                    parts = data.strip().split(":")
                    if len(parts) != 3 or parts[2] != "END" or len(parts[1]) != 2048:
                        read_errors.append(f"torn:{len(data)}:{data[:40]!r}")
                    elif parts[0] not in ("INIT", "A", "B"):
                        read_errors.append(f"bad-prefix:{parts[0]!r}")
                    elif parts[0] in ("A", "B") and len(set(parts[1])) != 1:
                        read_errors.append("mixed-payload")
                except OSError as exc:
                    read_errors.append(f"oserror:{exc}")
                await asyncio.sleep(0.002)

        def atomic_writer_cmd(tag: str, char: str) -> str:
            return (
                "python3 - <<'PY'\n"
                "import os, uuid\n"
                f"tag, ch = {tag!r}, {char!r}\n"
                "payload = f'{tag}:' + (ch * 2048) + ':END\\n'\n"
                "for i in range(60):\n"
                "    tmp = f'.live.{tag}.{uuid.uuid4().hex}.tmp'\n"
                "    with open(tmp, 'w') as f:\n"
                "        f.write(payload)\n"
                "        f.flush()\n"
                "    os.replace(tmp, 'live.md')\n"
                "PY"
            )

        reader_task = asyncio.create_task(continuous_reader())
        try:
            (code_a, out_a, _), (code_b, out_b, _) = await asyncio.gather(
                agent_a.run(atomic_writer_cmd("A", "x")),
                agent_b.run(atomic_writer_cmd("B", "y")),
            )
        finally:
            stop_reading.set()
            await reader_task

        assert code_a == 0, out_a
        assert code_b == 0, out_b
        assert reads_checked > 0
        assert read_errors == [], f"Torn reads or EIO detected: {read_errors[:5]}"

        # 3. Same-file concurrent flock appends -> all 100 lines intact, zero interleaving
        def flock_append_cmd(tag: str) -> str:
            return (
                "python3 - <<'PY'\n"
                "import fcntl\n"
                f"tag = {tag!r}\n"
                "for i in range(50):\n"
                "    with open('changelog.md', 'a') as f:\n"
                "        fcntl.flock(f.fileno(), fcntl.LOCK_EX)\n"
                "        f.write(f'{tag}-{i:03d}\\n')\n"
                "        f.flush()\n"
                "        fcntl.flock(f.fileno(), fcntl.LOCK_UN)\n"
                "PY"
            )

        (code_a, out_a, _), (code_b, out_b, _) = await asyncio.gather(
            agent_a.run(flock_append_cmd("A")),
            agent_b.run(flock_append_cmd("B")),
        )
        assert code_a == 0, out_a
        assert code_b == 0, out_b
        lines = (workspace_dir / "changelog.md").read_text().splitlines()
        assert len(lines) == 100
        assert set(lines) == {f"A-{i:03d}" for i in range(50)} | {f"B-{i:03d}" for i in range(50)}

        # 4. Concurrent SQLite WAL transactions by both Agents on the same DB file
        init_code, init_out, _ = await agent_a.run(
            "python3 -c \"import sqlite3; c=sqlite3.connect('collab.db'); "
            "c.execute('PRAGMA journal_mode=WAL;'); "
            "c.execute('CREATE TABLE log(agent TEXT, seq INT)'); c.commit(); c.close()\""
        )
        assert init_code == 0, init_out

        def sqlite_writer_cmd(tag: str) -> str:
            return (
                "python3 - <<'PY'\n"
                "import sqlite3\n"
                "c = sqlite3.connect('collab.db', timeout=10.0)\n"
                "c.execute('PRAGMA busy_timeout=10000;')\n"
                f"for i in range(30): c.execute('INSERT INTO log VALUES (?, ?)', ({tag!r}, i)); c.commit()\n"
                "c.close()\n"
                "PY"
            )

        (code_a, out_a, _), (code_b, out_b, _) = await asyncio.gather(
            agent_a.run(sqlite_writer_cmd("A")),
            agent_b.run(sqlite_writer_cmd("B")),
        )
        assert code_a == 0, out_a
        assert code_b == 0, out_b
        check_code, check_out, _ = await agent_a.run(
            "python3 -c \"import sqlite3; c=sqlite3.connect('collab.db'); "
            "print(c.execute('SELECT count(*), sum(agent=\\'A\\'), sum(agent=\\'B\\') FROM log').fetchone())\""
        )
        assert check_code == 0, check_out
        assert "(60, 30, 30)" in check_out


async def test_desktop_live_files_watcher_and_cas_conflict_with_two_agents(tmp_path, monkeypatch):
    """3-way Desktop (workbench-files.py) <-> Agent A <-> Agent B with change watcher & CAS."""
    async with shared_collab_harness(tmp_path, monkeypatch) as (
        agent_a,
        agent_b,
        workspace_dir,
        _,
        _,
    ):
        observed_paths: list[str] = []

        class Recorder(FileSystemEventHandler):
            def on_any_event(self, event):
                if not event.is_directory:
                    observed_paths.append(Path(event.src_path).name)
                    if getattr(event, "dest_path", None):
                        observed_paths.append(Path(event.dest_path).name)

        observer = Observer()
        observer.schedule(Recorder(), str(workspace_dir), recursive=True)
        observer.start()
        try:
            # Desktop creates collab.md
            created = _desktop_exec(
                workspace_dir,
                {
                    "action": "write",
                    "path": "collab.md",
                    "content_base64": base64.b64encode(b"desktop v1\n").decode(),
                    "expected_revision": None,
                },
            )
            assert "result" in created, created
            rev1 = created["result"]["revision"]

            # Agent A edits collab.md
            code, out, _ = await agent_a.run("printf 'agent-a v2\\n' > collab.md")
            assert code == 0, out

            # Wait briefly for OS watcher event delivery
            deadline = time.monotonic() + 5.0
            while "collab.md" not in observed_paths and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            assert "collab.md" in observed_paths

            # Desktop save with stale rev1 MUST be rejected with file_conflict (no silent clobber!)
            conflict = _desktop_exec(
                workspace_dir,
                {
                    "action": "write",
                    "path": "collab.md",
                    "content_base64": base64.b64encode(b"stale desktop edit\n").decode(),
                    "expected_revision": rev1,
                },
            )
            assert conflict["error"] == "file_conflict"

            # Desktop refreshes, sees Agent A's edit, and writes with fresh rev2
            refreshed = _desktop_exec(workspace_dir, {"action": "read", "path": "collab.md"})
            assert base64.b64decode(refreshed["result"]["content_base64"]) == b"agent-a v2\n"
            rev2 = refreshed["result"]["revision"]
            saved = _desktop_exec(
                workspace_dir,
                {
                    "action": "write",
                    "path": "collab.md",
                    "content_base64": base64.b64encode(b"desktop merged v3\n").decode(),
                    "expected_revision": rev2,
                },
            )
            assert "result" in saved, saved

            # Agent B immediately sees Desktop's merged v3
            code, out, _ = await agent_b.run("cat collab.md")
            assert code == 0, out
            assert "desktop merged v3" in out
        finally:
            observer.stop()
            observer.join(timeout=5)


async def test_revoking_agent_a_mid_command_never_interrupts_agent_b(tmp_path, monkeypatch):
    monkeypatch.setattr(shell_session, "HEARTBEAT_SECONDS", 0.05)
    monkeypatch.setattr(shell_session, "CONNECTION_HEARTBEAT_SECONDS", 0.05)
    async with shared_collab_harness(tmp_path, monkeypatch) as (
        agent_a,
        agent_b,
        workspace_dir,
        service,
        client,
    ):
        task_a = asyncio.create_task(
            agent_a.run("printf 'A_STARTED\\n'; sleep 10; printf 'BAD' > revoked.txt")
        )
        task_b = asyncio.create_task(
            agent_b.run("sleep 0.4; printf 'B_SURVIVED' > agent_b_ok.txt && echo B_DONE")
        )

        # Wait until Agent A has entered its shell, then revoke ONLY Agent A.
        async with asyncio.timeout(5):
            while service.active_shells.get(agent_a.identity, 0) == 0:
                await asyncio.sleep(0.02)
        service.revoked_identities.add(agent_a.identity)

        (code_a, out_a, _), (code_b, out_b, _) = await asyncio.gather(task_a, task_b)

        # Agent A is terminated immediately and never writes revoked.txt.
        assert code_a == 1, out_a
        assert "access to this workspace is not available" in out_a
        assert not (workspace_dir / "revoked.txt").exists()

        # Agent B finishes cleanly on the SAME shared workspace!
        assert code_b == 0, out_b
        assert "B_DONE" in out_b
        assert (workspace_dir / "agent_b_ok.txt").read_text() == "B_SURVIVED"

        # Agent A is rejected with HTTP 403 on new signed requests; Agent B keeps working.
        now = int(time.time())
        sig = agent_a.sign(command_message(agent_a.connection, now, b"echo blocked"))
        resp = await client.post(
            f"/api/v1/agent-links/connections/{agent_a.connection}/exec",
            content=b"echo blocked",
            headers={"X-PuppyOne-Time": str(now), "X-PuppyOne-Signature": sig},
        )
        assert resp.status_code == 403
        assert resp.headers["x-puppyone-code"] == "access_denied"

        code_b2, out_b2, _ = await agent_b.run("cat agent_b_ok.txt && echo ''")
        assert code_b2 == 0, out_b2
        assert "B_SURVIVED" in out_b2


async def test_small_file_throughput_and_git_gate_branch_protection(tmp_path, monkeypatch):
    async with shared_collab_harness(tmp_path, monkeypatch) as (
        agent_a,
        agent_b,
        workspace_dir,
        _,
        _,
    ):
        # 200 small files: create -> modify -> rename -> grep -> remove
        started = time.monotonic()
        code, out, err = await agent_a.run(
            "python3 - <<'PY'\n"
            "import os, pathlib\n"
            "root = pathlib.Path('bench200')\n"
            "root.mkdir()\n"
            "for i in range(200):\n"
            "    p = root / f'f_{i:03d}.md'\n"
            "    p.write_text(f'# File {i}\\nold\\n')\n"
            "    t = root / f'f_{i:03d}.tmp'\n"
            "    t.write_text(p.read_text().replace('old', 'updated'))\n"
            "    os.replace(t, p)\n"
            "    os.replace(p, root / f'g_{i:03d}.md')\n"
            "assert sum('updated' in p.read_text() for p in root.glob('g_*.md')) == 200\n"
            "for p in list(root.glob('g_*.md')):\n"
            "    p.unlink()\n"
            "root.rmdir()\n"
            "print('BENCH_200_OK')\n"
            "PY"
        )
        elapsed = time.monotonic() - started
        assert code == 0, f"stdout={out}\nstderr={err}"
        assert "BENCH_200_OK" in out
        assert elapsed < 15.0, f"200-file POSIX loop too slow: {elapsed:.2f}s"

        # Verify git-gate.sh refuses branch-switching commands without altering files
        if shutil.which("git") and shutil.which("bash"):
            git_gate = AGENT_WORKER_DIR / "git-gate.sh"
            patched_gate = tmp_path / "git-gate-local.sh"
            # Resolve real workspace path so git rev-parse --show-toplevel matches ROOT.
            real_ws = str(workspace_dir.resolve())
            patched_gate.write_text(
                git_gate.read_text()
                .replace("REAL=/usr/bin/git", f"REAL={shutil.which('git')}")
                .replace("ROOT=/workspace/repo", f"ROOT={real_ws}")
            )
            patched_gate.chmod(0o755)
            code, out, _ = await agent_b.run(
                f"git init -b workspace && "
                f"git config user.name Test && git config user.email test@example.invalid && "
                f"printf 'keep\\n' > keep.md && git add keep.md && git commit -m init && "
                f"git branch other && "
                f"(! {patched_gate} switch other) && "
                f"(! {patched_gate} checkout -b rogue) && "
                f"(! {patched_gate} rebase other) && "
                f"(! {patched_gate} worktree add ../wt other) && "
                f"test \"$(git symbolic-ref --short HEAD)\" = workspace && "
                f"test \"$(cat keep.md)\" = keep && echo GIT_GATE_OK"
            )
            assert code == 0, out
            assert "GIT_GATE_OK" in out
