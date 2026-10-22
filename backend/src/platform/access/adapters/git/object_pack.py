"""Transport binding for standard Git pack validation and indexed reads."""

import hashlib
import struct
import zlib

from dulwich.index import validate_path_element_hfs, validate_path_element_ntfs

from src.version_engine.infrastructure.owned_work import checkpoint
from src.version_engine.storage.git_pack import GitPack
from src.version_engine.write_engine.git_object_format import decode_tree


class IncomingPack(GitPack):
    def __init__(self, reader, *, limits=None):
        self.reader = reader
        super().__init__(reader.format, self._published_base, limits=limits)

    def _published_base(self, oid):
        self.reader.authorize([oid])
        return self.reader.get(oid)

    def get(self, oid):
        kind, body = super().get(oid)
        if kind == "tree":
            for entry in decode_tree(body, object_format=self.format):
                name = entry.raw_name
                ntfs_base = name.partition(b":")[0].rstrip(b". ").lower()
                if not validate_path_element_ntfs(name) or ntfs_base in {b".git", b"git~1"}:
                    raise ValueError("tree contains a protected Git path")
                try:
                    name.decode("utf-8")
                except UnicodeDecodeError:
                    pass
                else:
                    if not validate_path_element_hfs(name):
                        raise ValueError("tree contains a protected Git path")
                if name.lower() == b".gitmodules" and entry.mode == b"120000":
                    raise ValueError("gitmodules cannot be a symlink")
        return kind, body


TYPES = {"commit": 1, "tree": 2, "blob": 3, "tag": 4}


def pack_chunks(reader, oids):
    """Generate a valid non-delta pack without a repo or an output spool."""
    digest = hashlib.new(reader.format)
    header = struct.pack("!4sII", b"PACK", 2, len(oids))
    digest.update(header)
    yield header
    for oid in oids:
        checkpoint()
        kind, body = reader.get(oid)
        size = len(body)
        byte, size = (TYPES[kind] << 4) | (size & 15), size >> 4
        header = bytearray()
        while size:
            header.append(byte | 128)
            byte, size = size & 127, size >> 7
        header.append(byte)
        digest.update(header)
        yield bytes(header)
        compressor = zlib.compressobj()
        for offset in range(0, len(body), 65536):
            checkpoint()
            chunk = compressor.compress(body[offset : offset + 65536])
            if chunk:
                digest.update(chunk)
                yield chunk
        tail = compressor.flush()
        digest.update(tail)
        yield tail
    yield digest.digest()
