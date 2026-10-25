"""Root-only readiness and bounded Sprites hold management (no background loop)."""

import http.client
import json
import os
import socket
import sys
import time
from pathlib import Path


class Management(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect("/run/puppyone-sprites/api.sock")


def control(action, artifact):
    if os.geteuid() != 0 or action not in {"ready", "hold", "release"}:
        raise RuntimeError("Invalid Sprites lifecycle command")
    if Path("/opt/puppyone-agent/.sprites-artifact").read_text().strip() != artifact:
        raise RuntimeError("Sprites worker artifact mismatch")
    if action != "release":
        deadline = time.monotonic() + 25
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        while True:
            try:
                ready = Path("/run/puppyone-sprites-ready").read_text().strip() == boot
                if ready:
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                        probe.settimeout(1)
                        probe.connect("/run/puppyone-workbench/files.sock")
                    break
            except (FileNotFoundError, ConnectionRefusedError):
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError("Sprites file service is not ready")
            time.sleep(0.1)
    if action in {"hold", "release"}:
        connection = Management("sprite", timeout=10)
        try:
            connection.request(
                "PUT" if action == "hold" else "DELETE",
                "/v1/tasks/puppyone-workbench",
                body=json.dumps({"expire": "5m"}) if action == "hold" else None,
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            response.read(4096)
            allowed = {200} if action == "hold" else {204, 404}
            if response.status not in allowed:
                raise RuntimeError("Sprites lifecycle hold failed")
        finally:
            connection.close()


if __name__ == "__main__":
    control(sys.argv[1], sys.argv[2])
