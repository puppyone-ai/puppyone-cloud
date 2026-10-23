"""Object storage is reachable only through authorized product boundaries."""

import importlib

import pytest

import src.infra.s3 as storage


def test_raw_storage_router_cannot_be_imported():
    assert not hasattr(storage, "router")
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("src.infra.s3.router")


def test_application_exposes_no_raw_storage_routes():
    from src.main import app

    paths = {route.path for route in app.routes}
    assert not any(path == "/s3" or path.startswith("/s3/") for path in paths)
    assert not any(path.startswith("/s3") for path in app.openapi()["paths"])
