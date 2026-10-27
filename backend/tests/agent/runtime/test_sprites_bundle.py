"""The root bootstrap may unpack only regular files and confined relative links."""

import io
import tarfile

import pytest

from src.platform.sandbox.providers.sprites.bootstrap import extract_bundle


def archive(entries):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as output:
        for name, kind, value in entries:
            member = tarfile.TarInfo(name)
            member.type = kind
            if kind == tarfile.REGTYPE:
                member.size = len(value)
                member.mode = 0o755
                output.addfile(member, io.BytesIO(value))
            else:
                member.linkname = value
                output.addfile(member)
    data.seek(0)
    return tarfile.open(fileobj=data)


def test_worker_executables_and_relative_npm_links_remain_usable(tmp_path):
    with archive(
        [
            ("agent/.bin/tool", tarfile.SYMTYPE, "../package/tool"),
            ("agent/package/tool", tarfile.REGTYPE, b"worker code"),
        ]
    ) as source:
        extract_bundle(source, tmp_path)
    assert (tmp_path / "agent/.bin/tool").read_bytes() == b"worker code"
    assert (tmp_path / "agent/package/tool").stat().st_mode & 0o777 == 0o755


@pytest.mark.parametrize(
    "entries",
    [
        [("../outside", tarfile.REGTYPE, b"escape")],
        [("/absolute", tarfile.REGTYPE, b"escape")],
        [("agent/link", tarfile.SYMTYPE, "/outside")],
        [("agent/link", tarfile.SYMTYPE, "../../outside")],
        [("agent/link", tarfile.LNKTYPE, "outside")],
        [("agent/device", tarfile.CHRTYPE, "")],
        # A link created later changes the earlier link's resolved destination.
        [("agent/a", tarfile.SYMTYPE, "b/../../outside"), ("agent/b", tarfile.SYMTYPE, "..")],
    ],
)
def test_unsafe_bundle_never_writes_outside_the_staging_tree(tmp_path, entries):
    stage = tmp_path / "stage"
    stage.mkdir()
    with archive(entries) as source, pytest.raises(RuntimeError):
        extract_bundle(source, stage)
    assert not (tmp_path / "outside").exists()
