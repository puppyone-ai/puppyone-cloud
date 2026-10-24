"""Isolated Fly agent ownership shared by the local supervisor and transports.

Only a supervisor-issued, private descriptor is borrowable. Tokens stay in
process environments; default flyctl configuration is never consulted.
"""

import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path

MANAGED_AGENT = "PUPPYONE_FLY_AGENT_DIRECTORY"


def identity(app, token, cli):
    return {"app": app, "credential": hashlib.sha256(token.encode()).hexdigest(), "cli": cli}


def isolated_environment(root, token):
    return {
        "FLY_CONFIG_DIR": str(root),
        "FLY_AGENT_SOCKET_PATH": str(root / "agent.sock"),
        "FLY_AGENT_ISOLATED": "1",
        "FLY_WIREGUARD_TOKEN_MODE": "1",
        "FLY_NO_UPDATE_CHECK": "1",
        "FLY_API_TOKEN": token,
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(root),
    }


def create_directory():
    directory = tempfile.TemporaryDirectory(prefix="puppy-fly-", dir="/tmp")
    root = Path(directory.name)
    (root / "config.yml").write_text("{}\n")
    (root / "config.yml").chmod(0o600)
    (root / "agent.log").touch(mode=0o600)
    return directory


def managed_environment(app, token, cli):
    value = os.environ.get(MANAGED_AGENT)
    if not value:
        return None
    root = Path(value)
    descriptor = root / "owner.json"
    try:
        for path, mode in ((root, 0o700), (descriptor, 0o600)):
            info = path.lstat()
            if (
                stat.S_ISLNK(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != mode
            ):
                raise ValueError("Invalid managed Fly agent ownership")
        owner = json.loads(descriptor.read_text())
        if not isinstance(owner, dict):
            raise ValueError("Invalid managed Fly agent descriptor")
        if owner.get("identity") != identity(app, token, cli):
            raise ValueError("Managed Fly agent configuration changed")
        pid = owner.get("pid")
        if type(pid) is not int or pid <= 1:
            raise ValueError("Invalid managed Fly agent owner")
        os.kill(pid, 0)
        socket = (root / "agent.sock").lstat()
        if not stat.S_ISSOCK(socket.st_mode) or socket.st_uid != os.getuid():
            raise ValueError("Managed Fly agent is unavailable")
    except (OSError, ValueError, TypeError) as exc:
        raise ConnectionError("Managed Fly agent is unavailable or has changed") from exc
    return isolated_environment(root, token)
