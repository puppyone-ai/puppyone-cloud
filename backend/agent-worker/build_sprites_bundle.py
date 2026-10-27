"""Export the existing Linux worker image as an immutable Sprites runtime bundle.

Usage: python agent-worker/build_sprites_bundle.py --image IMAGE --output PATH
Build IMAGE with `docker build --platform linux/amd64 -t IMAGE agent-worker`.
No cloud resources or credentials are used by this build.
"""

import argparse
import gzip
import hashlib
import json
import subprocess
import tarfile
import tempfile
from pathlib import Path


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def build(image, output):
    info = json.loads(command("docker", "image", "inspect", image))[0]
    if info["Os"] != "linux" or info["Architecture"] != "amd64":
        raise ValueError("Sprites requires a linux/amd64 worker image")
    # Pin the inspected image ID, not a mutable tag, for the actual export.
    container = command("docker", "create", "--platform", "linux/amd64", info["Id"])
    try:
        with tempfile.TemporaryDirectory(prefix="puppyone-sprites-build-") as directory:
            root = Path(directory)
            (root / "agent").mkdir()
            subprocess.run(
                ["docker", "cp", container + ":/opt/puppyone-agent/.", str(root / "agent")],
                check=True,
            )
            subprocess.run(
                ["docker", "cp", container + ":/usr/local/bin/node", str(root / "node")], check=True
            )
            for required in (
                "agent/runtime-manifest.json",
                "agent/sprites-init.mjs",
                "agent/sprites-control.py",
            ):
                if not (root / required).is_file():
                    raise ValueError("Rebuild the worker image with Sprites support")

            def normalize(member):
                member.uid = member.gid = member.mtime = 0
                member.uname = member.gname = "root"
                return member

            output.parent.mkdir(parents=True, exist_ok=True)
            with (
                output.open("wb") as stream,
                gzip.GzipFile(fileobj=stream, mode="wb", filename="", mtime=0) as compressed,
                tarfile.open(fileobj=compressed, mode="w") as archive,
            ):
                for name in ("agent", "node"):
                    archive.add(root / name, arcname=name, filter=normalize)
        if output.stat().st_size > 256 * 1024 * 1024:
            raise ValueError("Worker bundle exceeds the provider's 256 MiB limit")
        with output.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()
    finally:
        subprocess.run(["docker", "rm", container], check=True, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    digest = build(args.image, args.output)
    print(json.dumps({"bundle": str(args.output.resolve()), "sha256": digest}))
