"""Execute the Lua transitions on real Redis, including concurrent workers."""

import os
import shutil
import subprocess
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src.platform.auth.shared_security_store import RedisSecurityStore, SecurityStoreUnavailable


@pytest.fixture(scope="module")
def redis_url():
    configured = os.environ.get("AUTH_TEST_REDIS_URL")
    if configured:
        yield configured
        return
    executable = shutil.which("redis-server")
    if not executable:
        pytest.fail("Authentication tests require redis-server or AUTH_TEST_REDIS_URL")
    with tempfile.TemporaryDirectory(prefix="auth-redis-", dir="/tmp") as directory:
        socket = str(Path(directory) / "redis.sock")
        process = subprocess.Popen(
            [executable, "--port", "0", "--unixsocket", socket, "--save", "", "--appendonly", "no"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(100):
                if Path(socket).exists():
                    break
                if process.poll() is not None:
                    pytest.fail("Test Redis could not start")
                time.sleep(0.02)
            yield "unix://" + socket
        finally:
            process.terminate()
            process.wait(timeout=5)


@pytest.fixture
def store(redis_url):
    store = RedisSecurityStore(redis_url)
    prefix = "auth-test:" + uuid.uuid4().hex + ":"
    store._key = lambda namespace, key: prefix + namespace + ":" + key
    yield store
    keys = list(store._redis.scan_iter(match=prefix + "*"))
    if keys:
        store._redis.delete(*keys)
    store._redis.close()


def test_atomic_move_only_one_worker_can_complete(store):
    pending = {
        "callback": "http://127.0.0.1:4000/auth/callback",
        "challenge": "abc",
        "nested": {"value": True},
    }
    store.put("state", "one", pending, 600)
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(
            pool.map(
                lambda i: store.transition(
                    "state", "one", pending, destination=("exchange", str(i), {"session": i}, 60)
                ),
                range(24),
            )
        )
    assert sum(results) == 1
    assert store.read("state", "one") is None
    assert sum(store.read("exchange", str(i)) is not None for i in range(24)) == 1


def test_binding_preserves_ttl_and_wrong_expected_does_not_consume(store):
    store.put("state", "one", {"value": 1}, 60)
    key = store._key("state", "one")
    store._redis.pexpire(key, 8000)
    before = store._redis.pttl(key)
    assert not store.transition("state", "one", {"value": 2})
    assert store.transition(
        "state", "one", {"value": 1}, replacement={"value": 1, "binding": "proof"}
    )
    assert 0 < store._redis.pttl(key) <= before
    assert not store.transition("state", "one", {"value": 1})
    assert store.transition("state", "one", {"binding": "proof", "value": 1})
    assert not store.transition("state", "one", {"value": 1, "binding": "proof"})


def test_expiry_collision_and_corrupt_state_fail_closed(store):
    store.put("state", "one", {"value": 1}, 60)
    store.put("exchange", "collision", {"existing": True}, 60)
    assert not store.transition(
        "state",
        "one",
        {"value": 1},
        destination=("exchange", "collision", {"replacement": True}, 60),
    )
    assert store.read("state", "one") == {"value": 1}
    store._redis.pexpire(store._key("state", "one"), 0)
    assert not store.transition("state", "one", {"value": 1})
    store._redis.set(store._key("state", "bad"), "not-json")
    with pytest.raises(SecurityStoreUnavailable):
        store.read("state", "bad")
