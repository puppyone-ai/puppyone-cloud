"""Fixtures for the native repository-hosting tests: the formal application in the owned Docker runner."""

import pytest

from tests.repository_hosting.harness.application import Application


@pytest.fixture(scope="module")
def bare_application(tmp_path_factory):
    app = Application(
        tmp_path_factory.mktemp("bare-app"),
        profile="new bare repositories; formal creation/entitlement/credential APIs; Scope disabled",
    )
    try:
        app.start()
        yield app
    finally:
        app.close()
