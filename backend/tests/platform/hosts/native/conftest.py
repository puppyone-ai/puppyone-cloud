"""Fixtures for the native hosts tests: real PostgreSQL 17, real PostgREST and the hosts API on them."""

import importlib.util
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.platform.auth.dependencies as auth_dependencies
import src.platform.hosts.gateway_service as gateway_service
import src.platform.hosts.router as hosts_router
from src.config import settings
from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser
from src.platform.hosts.connection_auth import (
    HostConnectionAuth,
)
from src.platform.hosts.gateway import HostGateway
from src.platform.hosts.repository import HostRepository
from src.platform.hosts.router import (
    get_connection_auth,
    get_gateway,
    get_host_service,
    require_host_devices_enabled,
    router,
)
from src.platform.hosts.service import HostService
from tests.platform.hosts.native.support import ROOT, SECRET, Database, free_port, serve, users


@pytest.fixture(scope="module")
def database():
    spec = importlib.util.spec_from_file_location(
        "host_native_postgres", ROOT / "scripts/testing/native_postgres.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with module.native_postgres() as cluster:
        yield Database(cluster["url"])


@pytest.fixture(scope="module")
def repository(database):
    """Actual PostgREST as service_role against the owned database, never a remote service."""
    from postgrest import SyncPostgrestClient

    from src.infra.supabase.instrumentation import DatabaseTransport
    from tests.agent.runtime.owned_http import owned_http_service

    with (owned_http_service("postgrest", database_url=database.url) as url,
          httpx.Client(trust_env=False, transport=DatabaseTransport(path_prefix="/")) as http):
        yield HostRepository(SyncPostgrestClient(url, http_client=http))


@pytest.fixture
def api(repository, monkeypatch):
    actor = {"id": None}
    gateway = HostGateway(repository)
    monkeypatch.setattr(settings, "HOST_DEVICES_ENABLED", True)
    monkeypatch.setattr(hosts_router, "_gateway", gateway)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_host_devices_enabled] = lambda: None
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id=actor["id"], role="authenticated")
    app.dependency_overrides[get_host_service] = lambda: HostService(repository)
    app.dependency_overrides[get_connection_auth] = lambda: HostConnectionAuth(repository)
    app.dependency_overrides[get_gateway] = lambda: gateway
    with TestClient(app) as client:
        yield client, actor


@pytest.fixture
def deployed(database, repository, monkeypatch):
    owner = str(uuid4())
    users(database, owner)
    gateway_port, api_port = free_port(), free_port()
    monkeypatch.setattr(settings, "HOST_DEVICES_ENABLED", True)
    monkeypatch.setattr(settings, "HOST_GATEWAY_INTERNAL_URL", f"http://127.0.0.1:{gateway_port}")
    monkeypatch.setattr(settings, "HOST_GATEWAY_INTERNAL_SECRET", SECRET)
    monkeypatch.setattr(settings, "HOST_GATEWAY_PUBLIC_URL", "")
    monkeypatch.setattr(gateway_service, "_gateway", HostGateway(repository))

    class Auth:
        def get_current_user(self, token):
            if token != "owner-token":
                raise ValueError("bad token")
            return CurrentUser(user_id=owner, role="authenticated")

    monkeypatch.setattr(auth_dependencies, "get_auth_service", lambda: Auth())
    api = FastAPI()
    api.include_router(router)
    api.dependency_overrides[require_host_devices_enabled] = lambda: None
    api.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id=owner, role="authenticated")
    api.dependency_overrides[get_host_service] = lambda: HostService(repository)
    api.dependency_overrides[get_connection_auth] = lambda: HostConnectionAuth(repository)
    # The API must not hold device sockets itself.
    monkeypatch.setattr(hosts_router, "_gateway", None)
    with serve(gateway_service.app, gateway_port), serve(api, api_port) as address:
        yield {"owner": owner, "api": address}
