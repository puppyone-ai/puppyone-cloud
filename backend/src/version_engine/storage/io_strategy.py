"""IO storage routing strategy for Version Engine objects.

This module is L6 policy: it decides the physical storage layout for
content-addressed Git objects. Generic S3 stays a key/value byte service; L5
continues to see only ObjectStore operations.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from math import ceil


class ObjectWriteLayout(StrEnum):
    """Physical layout selected for a logical Version Engine object."""

    LOOSE = "loose"
    BUNDLE = "bundle"
    CHUNKED = "chunked"


@dataclass(frozen=True)
class ObjectWriteRoute:
    object_id: str
    size_bytes: int
    layout: ObjectWriteLayout


@dataclass(frozen=True)
class ObjectWritePlan:
    routes: dict[str, ObjectWriteRoute]
    chunk_bytes: int

    def route_for(self, object_id: str) -> ObjectWriteRoute:
        return self.routes[object_id]

    @property
    def uses_location_index(self) -> bool:
        return any(
            route.layout in {ObjectWriteLayout.BUNDLE, ObjectWriteLayout.CHUNKED}
            for route in self.routes.values()
        )

    def chunk_part_count(self, object_id: str) -> int:
        route = self.route_for(object_id)
        if route.layout is not ObjectWriteLayout.CHUNKED:
            return 0
        return ceil(route.size_bytes / self.chunk_bytes)


@dataclass(frozen=True)
class IOStorageStrategy:
    """Route logical Git objects to physical storage layouts.

    The defaults are intentionally conservative for S3-compatible storage:
    small batched objects are packed into immutable bundles; large objects are
    split into application-level chunks plus a manifest; single small writes can
    remain loose.
    """

    bundle_target_bytes: int = 8 * 1024**2
    chunk_bytes: int = 8 * 1024**2
    location_index_enabled: bool = True
    # Object-count limits bound metadata, not ordinary upload boundaries. These
    # are admission windows over the existing POB representation, not Git pack
    # segments. A single larger object is isolated and uses the chunked route.
    max_batch_objects: int = 16_384
    write_concurrency: int = 4
    max_inflight_bytes: int = 32 * 1024**2
    max_stream_object_bytes: int = 128 * 1024**2

    def __post_init__(self) -> None:
        for name in (
            "bundle_target_bytes",
            "chunk_bytes",
            "max_batch_objects",
            "write_concurrency",
            "max_inflight_bytes",
            "max_stream_object_bytes",
        ):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.bundle_target_bytes > self.max_inflight_bytes:
            raise ValueError("bundle target exceeds the in-flight byte budget")

    def plan_single(self, object_id: str, size_bytes: int) -> ObjectWriteRoute:
        if self.location_index_enabled and size_bytes > self.bundle_target_bytes:
            layout = ObjectWriteLayout.CHUNKED
        else:
            layout = ObjectWriteLayout.LOOSE
        return ObjectWriteRoute(
            object_id=object_id,
            size_bytes=size_bytes,
            layout=layout,
        )

    def plan_batch(self, object_sizes: Mapping[str, int]) -> ObjectWritePlan:
        # A stream's one-object tail uses the same indexed, verified layout as
        # every other batch. Only the explicit single-write API selects LOOSE.
        routes: dict[str, ObjectWriteRoute] = {}
        for object_id, size_bytes in object_sizes.items():
            if not self.location_index_enabled:
                layout = ObjectWriteLayout.LOOSE
            elif size_bytes > self.bundle_target_bytes:
                layout = ObjectWriteLayout.CHUNKED
            else:
                layout = ObjectWriteLayout.BUNDLE
            routes[object_id] = ObjectWriteRoute(
                object_id=object_id,
                size_bytes=size_bytes,
                layout=layout,
            )
        return ObjectWritePlan(routes=routes, chunk_bytes=self.chunk_bytes)

    def without_location_index(self) -> IOStorageStrategy:
        if not self.location_index_enabled:
            return self
        return replace(self, location_index_enabled=False)
