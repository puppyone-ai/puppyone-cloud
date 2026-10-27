"""Bounded metadata pages within an already admitted repository read pin."""


class ObjectLocationRepository:
    PAGE_SIZE = 4096

    def __init__(self, snapshot):
        self.snapshot = snapshot

    def pages(self):
        snapshot, cursor = self.snapshot, ""
        while True:
            snapshot.check_live()
            page = snapshot.control.call(
                "get_version_pinned_object_locations_page",
                p_project_id=snapshot.project_id,
                p_actor=snapshot.actor,
                p_pin_id=snapshot.pin,
                p_after=cursor,
                p_limit=self.PAGE_SIZE,
            )
            rows, next_cursor = page["rows"], page["next_cursor"]
            if len(rows) > self.PAGE_SIZE:
                raise ValueError("object location page exceeded its bound")
            previous = cursor
            for row in rows:
                oid = row["object_id"]
                if oid <= previous:
                    raise ValueError("object location page is not ordered")
                previous = oid
            if next_cursor is not None and (not rows or next_cursor != previous):
                raise ValueError("invalid object location continuation")
            yield rows
            if next_cursor is None:
                return
            cursor = next_cursor
