"""Executed as root inside a newly owned Sprite; stdin is a verified worker tar.

This module has no import-time effects. It is not an application entrypoint.
"""

import hashlib
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


def invocation(source, artifact):
    """Keep exec's URL short: deliver the installer and bundle through stdin."""
    preamble = source.encode()
    command = [
        "python3",
        "-c",
        f"import sys; exec(compile(sys.stdin.buffer.read({len(preamble)}), '<sprites-bootstrap>', 'exec'))",
        artifact,
    ]
    return command, preamble


def extract_bundle(archive, destination):
    """Extract files before links; compatible with the provider's Python version.

    Older base images lack tarfile's data filter. Never extract through archive
    links: create them only after regular files, then verify their resolved targets
    before the staged tree can become executable. Devices/hardlinks are refused.
    """
    root = Path(destination).resolve()
    links = []
    for member in archive:
        name = PurePosixPath(member.name)
        if name.is_absolute() or ".." in name.parts or not name.parts:
            raise RuntimeError("Unsafe worker bundle path")
        target = root.joinpath(*name.parts)
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
        elif member.isfile():
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)
            target.chmod(member.mode & 0o755)
        elif member.issym():
            links.append((target, member.linkname))
        else:
            raise RuntimeError("Unsupported worker bundle entry")
    for target, link in links:
        if Path(link).is_absolute() or not target.parent.resolve().is_relative_to(root):
            raise RuntimeError("Unsafe worker bundle link")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(link)
    if any(not target.resolve().is_relative_to(root) for target, _ in links):
        raise RuntimeError("Worker bundle link escapes installation directory")


def install(artifact, data):
    if os.geteuid() != 0 or "sha256:" + hashlib.sha256(data).hexdigest() != artifact:
        raise RuntimeError("Unverified Sprites worker bundle")
    marker = Path("/opt/puppyone-agent/.sprites-artifact")
    if marker.exists():
        if marker.read_text().strip() != artifact:
            raise RuntimeError("Refusing to replace a bound worker artifact")
        return
    # Only trusted bootstrap runs before native tasks are admitted. No credentials
    # or package registry access are needed by subsequent wake-ups.
    tools = ["git", "rg", "rsync", "setpriv", "nft", "mountpoint"]
    if any(shutil.which(tool) is None for tool in tools) or not (
        shutil.which("fd") or shutil.which("fdfind")
    ):
        subprocess.run(["apt-get", "update", "-qq"], check=True)
        subprocess.run(
            [
                "apt-get",
                "install",
                "-y",
                "--no-install-recommends",
                "git",
                "ca-certificates",
                "fd-find",
                "ripgrep",
                "rsync",
                "util-linux",
                "nftables",
            ],
            check=True,
            env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"},
        )
    if not shutil.which("fd") and shutil.which("fdfind"):
        os.symlink(shutil.which("fdfind"), "/usr/local/bin/fd")
    with tempfile.TemporaryDirectory(prefix=".puppyone-install-", dir="/opt") as temporary:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            extract_bundle(archive, temporary)
        stage = Path(temporary)
        for required in (
            "node",
            "agent/runtime-manifest.json",
            "agent/sprites-init.mjs",
            "agent/sprites-control.py",
        ):
            if not (stage / required).is_file():
                raise RuntimeError("Incomplete Sprites worker bundle")
        os.chmod(stage / "node", 0o755)
        subprocess.run([str(stage / "node"), "--version"], check=True)
        # Only an interrupted unbound install can reach here. Working files and
        # root-owned control state live elsewhere and are never replaced.
        if marker.parent.exists():
            shutil.rmtree(marker.parent)
        os.replace(stage / "agent", marker.parent)
        os.replace(stage / "node", "/usr/local/bin/node")
        marker.write_text(artifact + "\n")
        os.chmod(marker, 0o644)
        subprocess.run(["sync"], check=True)


if __name__ == "__main__":
    install(sys.argv[1], sys.stdin.buffer.read(256 * 1024 * 1024 + 1))
