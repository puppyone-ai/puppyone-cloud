"""Fixtures for the native chat app tests: real PostgreSQL 17 with every migration."""

import importlib.util

import pytest

from tests.platform.chat_apps.native.support import ROOT, Database


@pytest.fixture(scope="module")
def db():
    spec = importlib.util.spec_from_file_location(
        "chat_apps_native_postgres", ROOT / "scripts/testing/native_postgres.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with module.native_postgres() as cluster:
        yield Database(cluster["url"])
