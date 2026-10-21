"""Install the pinned Fly SSH client used by the backend (no shell installer)."""

import argparse
import hashlib
import io
import os
import platform
import tarfile
from pathlib import Path
from urllib.request import urlopen

VERSION = "0.4.118"
# Official release checksums, pinned together with the version.
CHECKSUMS = {
    ("Linux", "aarch64"): "8b9ae409107d5e268895214e773881904a81d56b9593c14c6b41639f05e25faa",
    ("Linux", "x86_64"): "0f2fabbb29904a7bac8bd96009314d26751084c33e098fcc393e2dac6c472f60",
    ("Darwin", "arm64"): "ce319226af327b69cd00ea1a05a9b90108278e17f89a54e8e3830c409e099287",
    ("Darwin", "x86_64"): "1ee5049774d3b4866dac7b65715005e6e6d86c539096290e1824892760cf9547",
}


def install(destination: Path):
    system, machine = platform.system(), platform.machine()
    expected = CHECKSUMS[(system, machine)]
    release_os = "macOS" if system == "Darwin" else system
    release_arch = "arm64" if machine == "aarch64" else machine
    name = f"flyctl_{VERSION}_{release_os}_{release_arch}.tar.gz"
    url = f"https://github.com/superfly/flyctl/releases/download/v{VERSION}/{name}"
    with urlopen(url, timeout=120) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("Fly CLI release checksum mismatch")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        member = archive.getmember("flyctl")
        if not member.isfile():
            raise ValueError("Fly CLI archive has no regular executable")
        binary = archive.extractfile(member).read()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_bytes(binary)
    temporary.chmod(0o755)
    os.replace(temporary, destination)
    print(f"Installed flyctl {VERSION}: {destination}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    install(parser.parse_args().destination)
