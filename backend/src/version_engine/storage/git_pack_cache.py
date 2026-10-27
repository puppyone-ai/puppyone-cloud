"""Bounded immutable-pack disk cache. Leases prevent eviction during a read."""

import threading
import time
from collections import OrderedDict
from contextlib import contextmanager

from src.infra.execution.context import checkpoint
from src.version_engine.storage.git_process import MAX_SECONDS


class GitPackCache:
    def __init__(self, capacity=4):
        if capacity < 1:
            raise ValueError("pack cache capacity must be positive")
        self.capacity = capacity
        self.entries = OrderedDict()
        self.loading = set()
        self.condition = threading.Condition()

    @contextmanager
    def acquire(self, key, loader):
        entry = None
        deadline = time.monotonic() + MAX_SECONDS
        with self.condition:
            while entry is None:
                checkpoint()
                if time.monotonic() >= deadline:
                    raise TimeoutError("Git pack cache admission deadline exceeded")
                if key in self.entries:
                    entry = self.entries[key]
                    entry[1] += 1
                    self.entries.move_to_end(key)
                    break
                if key not in self.loading:
                    if len(self.entries) + len(self.loading) >= self.capacity:
                        idle = next((k for k, v in self.entries.items() if v[1] == 0), None)
                        if idle is not None:
                            pack, _ = self.entries.pop(idle)
                            pack.close()
                    if len(self.entries) + len(self.loading) < self.capacity:
                        self.loading.add(key)
                        break
                self.condition.wait(timeout=0.05)
        if entry is None:
            try:
                pack = loader()
            except BaseException:
                with self.condition:
                    self.loading.remove(key)
                    self.condition.notify_all()
                raise
            with self.condition:
                self.loading.remove(key)
                entry = [pack, 1]
                self.entries[key] = entry
                self.condition.notify_all()
        try:
            yield entry[0]
        finally:
            with self.condition:
                entry[1] -= 1
                self.condition.notify_all()

    def clear(self):
        with self.condition:
            if self.loading or any(entry[1] for entry in self.entries.values()):
                raise RuntimeError("cannot clear leased Git packs")
            for pack, _ in self.entries.values():
                pack.close()
            self.entries.clear()


pack_cache = GitPackCache()
