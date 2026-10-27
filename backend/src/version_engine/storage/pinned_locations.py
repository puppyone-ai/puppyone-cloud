"""Disposable on-disk placement index; independent of repository size in RAM.

This is derived metadata only. The active read pin protects placements from GC;
RepositorySnapshot remains responsible for reachability, hashes and Git types.
"""

import sqlite3
import threading


class PinnedLocations:
    def __init__(self, pages, decode):
        self.decode = decode
        # An empty filename is an automatically deleted, private SQLite temp DB.
        self.db = sqlite3.connect("", check_same_thread=False)
        self.lock = threading.Lock()
        self.db.execute("PRAGMA cache_size=-2048")
        self.db.execute(
            "CREATE TABLE locations(oid TEXT PRIMARY KEY, key TEXT, offset INT, size INT) WITHOUT ROWID"
        )
        try:
            with self.db:
                for rows in pages:
                    values = []
                    for row in rows:
                        location = decode(row)
                        values.append(
                            (
                                row["object_id"],
                                location.pack_key,
                                location.offset_bytes,
                                location.size_bytes,
                            )
                        )
                    self.db.executemany("INSERT INTO locations VALUES(?,?,?,?)", values)
        except BaseException:
            self.close()
            raise

    def get(self, oid):
        with self.lock:
            row = self.db.execute(
                "SELECT key,offset,size FROM locations WHERE oid=?", (oid,)
            ).fetchone()
        return (
            self.decode(dict(zip(("pack_key", "offset_bytes", "size_bytes"), row, strict=True)))
            if row
            else None
        )

    def close(self):
        with self.lock:
            self.db.close()
