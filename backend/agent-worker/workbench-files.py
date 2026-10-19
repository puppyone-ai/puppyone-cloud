"""Native workspace file operations, confined with directory descriptors.

Symlinks are listed but never followed by the browser API. Bash retains native
semantics. The helper runs as the same unprivileged file owner as every task.
"""
import base64
import hashlib
import json
import os
import stat
import sys
import uuid

MAX_BYTES = 8 * 1024 * 1024
MAX_ENTRIES = 10000
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class Conflict(Exception):
    pass


def parts(path):
    if not isinstance(path, str) or len(path.encode()) > 4096 or '\\' in path or '\0' in path:
        raise ValueError('Invalid workspace path')
    names = path.split('/') if path else []
    if any(name in ('', '.', '..', '.git') for name in names):
        raise ValueError('Invalid workspace path')
    return names


def directory(root, names):
    result = os.dup(root)
    try:
        for name in names:
            next_fd = os.open(name, DIRECTORY, dir_fd=result)
            os.close(result)
            result = next_fd
        return result
    except BaseException:
        os.close(result)
        raise


def version(data):
    return hashlib.sha256(data).hexdigest()


def read(parent, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('Only regular files can be read')
        if before.st_size > MAX_BYTES:
            raise ValueError('File exceeds live editor limit')
        with os.fdopen(os.dup(fd), 'rb') as stream:
            data = stream.read(MAX_BYTES + 1)
        after = os.fstat(fd)
        if len(data) > MAX_BYTES:
            raise ValueError('File exceeds live editor limit')
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise Conflict('File changed during read; retry')
        return data, after
    finally:
        os.close(fd)


def entry(name, info):
    kind = 'directory' if stat.S_ISDIR(info.st_mode) else 'file' if stat.S_ISREG(info.st_mode) else 'symlink' if stat.S_ISLNK(info.st_mode) else 'special'
    return {'name': name, 'kind': kind, 'size': info.st_size, 'modified_ns': str(info.st_mtime_ns)}


def remove(parent, name, budget):
    budget[0] -= 1
    if budget[0] < 0:
        raise ValueError('Directory mutation exceeds limit')
    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if stat.S_ISDIR(info.st_mode):
        child = os.open(name, DIRECTORY, dir_fd=parent)
        try:
            with os.scandir(child) as entries:
                for item in entries:
                    remove(child, item.name, budget)
        finally:
            os.close(child)
        os.rmdir(name, dir_fd=parent)
    else:
        os.unlink(name, dir_fd=parent)


def move(parent, name, target, destination):
    # Linux no-clobber rename is atomic even if another tool creates the target.
    import ctypes
    library = ctypes.CDLL(None, use_errno=True)
    rename = library.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(parent, os.fsencode(name), target, os.fsencode(destination), 1) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def execute(root, request):
    names = parts(request.get('path', ''))
    action = request['action']
    if action == 'list':
        fd = directory(root, names)
        try:
            result = []
            with os.scandir(fd) as entries:
                for item in entries:
                    if item.name == '.git':
                        continue
                    if len(result) >= MAX_ENTRIES:
                        raise ValueError('Directory exceeds live listing limit')
                    try:
                        result.append(entry(item.name, item.stat(follow_symlinks=False)))
                    except FileNotFoundError:
                        continue
            return {'entries': sorted(result, key=lambda e: (e['kind'] != 'directory', e['name']))}
        finally:
            os.close(fd)
    if not names:
        raise ValueError('A file path is required')
    parent = directory(root, names[:-1])
    try:
        name = names[-1]
        if action == 'read':
            data, info = read(parent, name)
            return {**entry(name, info), 'content_base64': base64.b64encode(data).decode(), 'revision': version(data)}
        if action == 'write':
            expected = request.get('expected_revision')
            if expected is not None and (not isinstance(expected, str) or len(expected) != 64):
                raise ValueError('Invalid expected file revision')
            data = base64.b64decode(request['content_base64'], validate=True)
            if len(data) > MAX_BYTES:
                raise ValueError('File exceeds live editor limit')
            try:
                current, info = read(parent, name)
            except FileNotFoundError:
                current, info = None, None
            if (version(current) if current is not None else None) != expected:
                raise Conflict('File changed since it was read')
            temporary = '.puppy-save-' + uuid.uuid4().hex
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(data)
                    stream.flush()
                    if info:
                        os.fchmod(stream.fileno(), stat.S_IMODE(info.st_mode))
                    os.fsync(stream.fileno())
                # Detect intervening native saves. Arbitrary Bash does not obey an
                # advisory UI lock: this is optimistic conflict detection, not a
                # distributed transaction over uncooperative POSIX writers.
                try:
                    latest, _ = read(parent, name)
                except FileNotFoundError:
                    latest = None
                if (version(latest) if latest is not None else None) != expected:
                    raise Conflict('File changed during save')
                if expected is None:
                    # No-clobber creation; rename would overwrite a concurrent creator.
                    os.link(temporary, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
                    os.unlink(temporary, dir_fd=parent)
                else:
                    os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
                return {'revision': version(data), 'size': len(data)}
            finally:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass
        if action == 'remove':
            remove(parent, name, [MAX_ENTRIES])
            return {}
        if action == 'move':
            destination = parts(request.get('destination'))
            if not destination:
                raise ValueError('Move target is required')
            target = directory(root, destination[:-1])
            try:
                move(parent, name, target, destination[-1])
            finally:
                os.close(target)
            return {}
        if action == 'mkdir':
            os.mkdir(name, mode=0o755, dir_fd=parent)
            return {}
        raise ValueError('Unknown workspace file operation')
    finally:
        os.close(parent)


def operation(request):
    root = os.open('/workspace/repo', DIRECTORY)
    try:
        try:
            result = {'result': execute(root, request)}
        except Conflict as error:
            result = {'error': 'file_conflict', 'message': str(error)}
        except FileNotFoundError:
            result = {'error': 'file_not_found'}
        except FileExistsError:
            result = {'error': 'file_conflict'}
        except (ValueError, OSError):
            result = {'error': 'file_operation_rejected'}
        return result
    finally:
        os.close(root)


if __name__ == '__main__':
    if os.getuid() != 1000:
        raise PermissionError('Native file operations require workspace ownership')
    if sys.argv[1:] == ['--stream']:
        while raw := sys.stdin.buffer.readline(MAX_BYTES * 2):
            if not raw.endswith(b'\n'):
                raise ValueError('File request exceeds limit')
            print(json.dumps(operation(json.loads(raw))), flush=True)
    else:
        sys.stdout.write(json.dumps(operation(json.loads(sys.stdin.buffer.read(MAX_BYTES * 2)))))
