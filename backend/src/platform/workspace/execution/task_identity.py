"""The identity a Workspace gives the processes of one command or Agent run.

It is the GID they run under and are stopped by. 20000-59999 come first, then
100000 onward, so no command shares a group the machine image already has
(nogroup is 65534). Two billion lasts a machine decades at a command a second.
The database and the worker (``workbench-state.mjs``) use the same ranges.
"""

TASK_UID_RANGES = ((20_000, 59_999), (100_000, 2_000_000_000))


def valid_task_uid(value):
    return type(value) is int and any(low <= value <= high for low, high in TASK_UID_RANGES)
