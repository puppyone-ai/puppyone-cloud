"""Small snapshot reuse, accepted only by the existing SQL revision fence.

The cache never grants access by itself. Each borrowed snapshot must pass the
canonical Python policy and cloud_workbench_activity's current database revision
check before any file channel is used. Final response authorization stays fresh.
"""

import copy
import threading
import time
from collections import OrderedDict


class AdmissionSnapshots:
    def __init__(self, *, limit=256, seconds=30):
        self.limit, self.seconds = limit, seconds
        self.entries = OrderedDict()
        self.lock = threading.Lock()

    def put(self, user, project, context):
        with self.lock:
            key = (user, project)
            self.entries[key] = (time.monotonic() + self.seconds, copy.deepcopy(context))
            self.entries.move_to_end(key)
            while len(self.entries) > self.limit:
                self.entries.popitem(last=False)

    def get(self, user, project):
        with self.lock:
            value = self.entries.get((user, project))
            return copy.deepcopy(value[1]) if value and value[0] > time.monotonic() else None

    def discard(self, user, project):
        with self.lock:
            self.entries.pop((user, project), None)
