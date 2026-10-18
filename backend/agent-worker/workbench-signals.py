"""Stop one admitted task by immutable real GID, with PID-reuse-safe signals.

All tasks share workspace UID 1000 so native 0600 files remain usable. Each task
gets a never-reused GID; unprivileged children inherit it even after setsid/fork.
This is task lifetime accounting, not a security boundary between one owner's
chats. Different owners still require different VMs. No PID-only kill fallback.
"""
import os
import re
import signal
import sys
import time


def quiesce(gid, operation):
    if os.geteuid() != 0 or not 20000 <= gid <= 59999:
        raise ValueError("Invalid admitted task identity")
    if operation not in ("SIGSTOP", "SIGKILL", "SIGCONT"):
        raise ValueError("Invalid task signal")
    sig = getattr(signal, operation)
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        pending = False
        for entry in os.scandir('/proc'):
            if not entry.name.isdecimal():
                continue
            descriptor = None
            try:
                descriptor = os.pidfd_open(int(entry.name))
                with open(entry.path + '/status') as stream:
                    status = stream.read()
                if not re.search(r'^Gid:\s+' + str(gid) + r'\s', status, re.M):
                    continue
                if not re.search(r'^Uid:\s+1000\s', status, re.M):
                    raise RuntimeError("Unexpected privileged task identity")
                if re.search(r'^State:\s+Z\b', status, re.M):
                    continue
                if operation == 'SIGSTOP' and re.search(r'^State:\s+[Tt]\b', status, re.M):
                    continue
                signal.pidfd_send_signal(descriptor, sig)
                pending = True
            except (FileNotFoundError, ProcessLookupError):
                pass
            finally:
                if descriptor is not None:
                    os.close(descriptor)
        if not pending or operation == 'SIGCONT':
            return
        time.sleep(0.01)
    raise RuntimeError("Task processes did not quiesce")


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise ValueError("Expected task GID and signal")
    quiesce(int(sys.argv[1]), sys.argv[2])
