"""Owned Linux-container acceptance of bundle/bootstrap, isolation and live files.

This emulates only the Sprites management socket; it does not prove hosted API
compatibility or real warm/cold behavior. Run only in a disposable container.
"""

import base64
import hashlib
import http.server
import importlib.util
import json
import os
import selectors
import signal
import socketserver
import subprocess
import sys
import threading
from pathlib import Path
from uuid import uuid4


def run(*command, **options):
    result = subprocess.run(command, capture_output=True, timeout=60, **options)
    if result.returncode:
        raise RuntimeError(f"Local acceptance command failed: {result.stderr.decode()[-4000:]}")
    return result


def main(bundle_path, bootstrap_path):
    if os.geteuid() != 0 or sys.platform != "linux" or not Path("/.dockerenv").exists():
        raise RuntimeError("This acceptance requires an owned root Linux container")
    data = Path(bundle_path).read_bytes()
    artifact = "sha256:" + hashlib.sha256(data).hexdigest()
    module_spec = importlib.util.spec_from_file_location("sprite_bootstrap", bootstrap_path)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    source = Path(bootstrap_path).read_text()
    bootstrap, preamble = module.invocation(source, artifact)
    run(*bootstrap, input=preamble + data)
    assert Path("/opt/puppyone-agent/.sprites-artifact").read_text().strip() == artifact
    assert run("node", "--version").stdout.strip() == b"v22.22.3"
    run(*bootstrap, input=preamble + data)  # Retry must not replace the installed worker.

    holds = {}

    class Management(http.server.BaseHTTPRequestHandler):
        def do_PUT(self):
            assert self.headers["Host"] == "sprite"
            value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert value == {"expire": "5m"}
            holds[self.path] = value
            self.send_response(200)
            self.end_headers()

        def do_DELETE(self):
            holds.pop(self.path, None)
            self.send_response(204)
            self.end_headers()

        def log_message(self, *_):
            pass

    directory = Path("/.sprite")
    directory.mkdir(mode=0o755)
    server = socketserver.UnixStreamServer(str(directory / "api.sock"), Management)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    os.chown(directory / "api.sock", 1000, 1000)
    os.chmod(directory / "api.sock", 0o600)
    socket_probe = (
        "import socket; s=socket.socket(socket.AF_UNIX); s.connect('/.sprite/api.sock'); s.close()"
    )
    run("python3", "-c", socket_probe, user=1000, group=1000)

    service = bridge = None
    boot = ["node", "/opt/puppyone-agent/sprites-init.mjs"]
    control = ["python3", "/opt/puppyone-agent/sprites-control.py"]

    def stop(process):
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)

    def start():
        process = subprocess.Popen(boot, start_new_session=True)
        try:
            run(*control, "hold", artifact)
            assert process.poll() is None
            assert holds == {"/v1/tasks/puppyone-workbench": {"expire": "5m"}}
            return process
        except BaseException:
            stop(process)
            raise

    workbench, generation = str(uuid4()), str(uuid4())

    def files():
        process = subprocess.Popen(
            ["node", "/opt/puppyone-agent/workbench-file-client.mjs"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            start_new_session=True,
            bufsize=0,
        )
        return process

    def call(process, action, value):
        identity = str(uuid4())
        process.stdin.write(
            (
                json.dumps({"type": "control", "id": identity, "action": action, "value": value})
                + "\n"
            ).encode()
        )
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=30):
                raise RuntimeError("Native file bridge timed out")
            frame = json.loads(process.stdout.readline())
        assert frame["type"] == "control_result" and frame["id"] == identity, frame
        assert "error" not in frame, frame
        return frame["result"]

    def hello(process):
        receipt = call(
            process,
            "hello",
            {"protocol": 1, "workbench": workbench, "generation": generation, "compute_epoch": 1},
        )
        assert receipt["runtime"]["file_protocol"] == 1

    try:
        # A user-writable parent must fail before the file service is admitted.
        directory.chmod(0o777)
        refused = subprocess.run(boot, capture_output=True, timeout=10)
        assert refused.returncode != 0 and b"not user-writable" in refused.stderr
        directory.chmod(0o755)
        service = start()
        socket_stat = (directory / "api.sock").stat()
        assert socket_stat.st_uid == 0 and socket_stat.st_mode & 0o777 == 0o600
        denied = subprocess.run(
            ["python3", "-c", socket_probe], user=1000, group=1000, capture_output=True, timeout=5
        )
        assert denied.returncode != 0 and b"PermissionError" in denied.stderr
        rules = run("nft", "list", "table", "inet", "puppyone_task_egress").stdout
        assert b"skuid 1000" in rules and b"reject" in rules

        initialize = """
import {initializeWorkbench} from '/opt/puppyone-agent/workbench-initialize.mjs';
process.umask(0o002);
await initializeWorkbench(JSON.parse(process.argv[1]), {
  project_id:'local-sprites-acceptance', object_format:'sha1',
  target_ref:'refs/heads/workspaces/local', base_oid:null
}, () => {throw new Error('Unexpected Git network request');});
"""
        run(
            "node",
            "--input-type=module",
            "-e",
            initialize,
            json.dumps({"workbench": workbench, "generation": generation}),
        )
        bridge = files()
        hello(bridge)
        content = base64.b64encode("实时编辑，在暂停前保存".encode()).decode()
        saved = call(
            bridge, "files", {"action": "write", "path": "draft.md", "content_base64": content}
        )
        assert "result" in saved, saved
        loaded = call(bridge, "files", {"action": "read", "path": "draft.md"})
        assert loaded["result"]["content_base64"] == content
        stop(bridge)
        bridge = None
        run(*control, "release", artifact)
        assert not holds
        stop(service)
        service = None

        # Recreate mounts/service rather than merely reading an old process cache.
        run("umount", "/workspace")
        run("umount", "/run/puppyone-workbench")
        Path("/run/puppyone-sprites-ready").unlink()
        run(*bootstrap, input=preamble + data)
        service = start()
        bridge = files()
        hello(bridge)
        assert (
            call(bridge, "files", {"action": "read", "path": "draft.md"})["result"][
                "content_base64"
            ]
            == content
        )
        changed = data + b"different-artifact"
        different, preamble = module.invocation(
            source, "sha256:" + hashlib.sha256(changed).hexdigest()
        )
        refused = subprocess.run(
            different,
            input=preamble + changed,
            capture_output=True,
            timeout=60,
        )
        assert refused.returncode != 0 and b"Refusing to replace" in refused.stderr
        print(
            json.dumps(
                {
                    "status": "passed",
                    "artifact": artifact,
                    "checks": [
                        "bundle install and retry",
                        "socket ownership and UID isolation",
                        "UID firewall installed",
                        "native editor save/read",
                        "hold create/release",
                        "service restart and remount preserve files",
                        "bound artifact replacement rejected",
                    ],
                }
            )
        )
    finally:
        stop(bridge)
        stop(service)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main(*sys.argv[1:])
