"""Unprivileged POSIX PTY/pipe bridge. The trusted controller owns task cleanup."""

import base64
import errno
import fcntl
import json
import os
import pty
import selectors
import signal
import struct
import subprocess
import sys
import termios
import threading


def emit(kind, **fields):
    print(json.dumps({"type": kind, **fields}), flush=True)


def resize(fd, size):
    if (
        not isinstance(size, list)
        or len(size) != 2
        or any(type(n) is not int or not 1 <= n <= 1000 for n in size)
    ):
        raise ValueError("Invalid terminal size")
    columns, rows = size
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))


def main(config):
    argv = ["/bin/bash", "--noprofile", "--norc"]
    if config["command"]:
        argv += ["-c", config["command"]]
    elif config["term"]:
        argv += ["-i"]
    terminal = bool(config["term"])
    selector = selectors.DefaultSelector()
    child = None
    if terminal:
        pid, master = pty.fork()
        if pid == 0:
            os.execv(argv[0], argv)
        resize(master, config["size"])
        writer = master
        selector.register(master, selectors.EVENT_READ, "stdout")
    else:
        child = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        pid, writer = child.pid, child.stdin.fileno()
        selector.register(child.stdout, selectors.EVENT_READ, "stdout")
        selector.register(child.stderr, selectors.EVENT_READ, "stderr")

    def incoming():
        buffer = b""
        while data := os.read(0, 32768):
            buffer += data
            if len(buffer) > 128 * 1024:
                raise ValueError("Input frame too large")
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                yield json.loads(line)

    def input_frames():
        closed = False
        try:
            # A separate input thread keeps blocked pipe writes from preventing
            # stdout/stderr draining. All queues remain bounded by OS pipes.
            for frame in incoming():
                if frame["action"] == "resize" and terminal:
                    resize(writer, frame["size"])
                elif frame["action"] == "signal":
                    name = frame["signal"]
                    if name not in {"INT", "TERM", "HUP", "KILL"}:
                        raise ValueError("Invalid signal")
                    os.killpg(pid, getattr(signal, "SIG" + name))
                elif frame["action"] == "input" and not closed:
                    payload = base64.b64decode(frame["data"], validate=True)
                    while payload:
                        payload = payload[os.write(writer, payload) :]
                elif frame["action"] == "eof" and not closed:
                    if terminal:
                        os.write(writer, b"\x04")
                    else:
                        child.stdin.close()
                        closed = True
        except (BrokenPipeError, ProcessLookupError):
            return
        except Exception:
            os._exit(1)  # trusted parent observes failure and cleans descendants

    threading.Thread(target=input_frames, daemon=True).start()
    while selector.get_map():
        for key, _ in selector.select():
            try:
                data = os.read(key.fd, 32768)
            except OSError as exc:
                if exc.errno != errno.EIO:
                    raise
                data = b""
            if data:
                emit("shell_data", stream=key.data, data=base64.b64encode(data).decode())
            else:
                selector.unregister(key.fd)
    if child:
        status = child.wait()
    else:
        _, value = os.waitpid(pid, 0)
        status = os.waitstatus_to_exitcode(value)
    emit("shell_exit", status=status if status >= 0 else 128 - status)
    selector.close()


if __name__ == "__main__":
    main(json.loads(base64.b64decode(sys.argv[1], validate=True)))
