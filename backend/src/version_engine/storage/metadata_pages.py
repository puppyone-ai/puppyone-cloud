"""Bound network metadata independently of physical object windows.

The matching SQL page contract is 4 MiB / 16,384 entries. JSON accounting
includes array delimiters and separators; ASCII escaping is conservative for
PostgreSQL's UTF-8 jsonb representation. No unbounded request is constructed.
"""

import json

MAX_METADATA_BYTES = 4 * 1024**2
MAX_METADATA_ROWS = 16_384


class MetadataPage:
    def __init__(self):
        self.rows = []
        self.size = 2

    def add(self, row):
        """Append and return the preceding full page, if any."""
        size = len(json.dumps(row, ensure_ascii=True).encode("utf-8"))
        if size + 2 > MAX_METADATA_BYTES:
            raise ValueError("metadata row exceeds page byte budget")
        ready = None
        if self.rows and (
            len(self.rows) >= MAX_METADATA_ROWS or self.size + 2 + size > MAX_METADATA_BYTES
        ):
            ready = self.take()
        self.size += size + (2 if self.rows else 0)
        self.rows.append(row)
        return ready

    def take(self):
        rows, self.rows = self.rows, []
        self.size = 2
        return rows


def metadata_pages(rows):
    page = MetadataPage()
    for row in rows:
        ready = page.add(row)
        if ready:
            yield ready
    if page.rows:
        yield page.take()
