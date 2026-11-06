"""One directory of the user's computers and hosted workspaces, one file API by host."""

from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.platform.access.adapters.api.host_directory_router as directory
from src.platform.access.composition import get_workbench_service
from src.platform.auth.dependencies import get_current_user
from src.platform.authorization.dependencies import get_authorization_service
from src.platform.hosts.router import get_file_route, get_host_service, require_host_devices_enabled
from src.platform.project.dependencies import get_project_service
from src.shared_types.grants import ProjectAction

OWNER = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
DEVICE = str(uuid4())
DEVICE_FOLDER = str(uuid4())
OPENED, NEVER, READONLY = str(uuid4()), str(uuid4()), str(uuid4())
GENERATION = str(uuid4())


class Grant:
    def __init__(self, writable):
        self.writable = writable

    def allows(self, action):
        return action != ProjectAction.CONTENT_WRITE or self.writable


class Workbench:
    def __init__(self):
        self.calls = []
        self.opening = SimpleNamespace(status=self.status)

    async def status(self, user, project, wait_seconds=0):
        assert user == OWNER and wait_seconds == 0  # Listing only observes.
        if project == NEVER:
            return None, None
        return {"state": "running" if project == OPENED else "paused", "generation": GENERATION}, None

    async def file_operation(self, user, project, generation, operation):
        self.calls.append((user, project, generation, operation))
        return {"entries": []} if operation["action"] == "list" else {"revision": "a" * 64}

    async def bound(self, user, project, generation, *, write=False):
        return None

    async def events(self, user, project, generation):
        yield {"generation": generation, "reset": True, "paths": ["a.md"]}


class Route:
    def __init__(self):
        self.calls = []

    async def dispatch(self, owner, folder, action, operation):
        self.calls.append((owner, str(folder), action, operation))
        return {"entries": [{"name": "x.md", "kind": "file"}]}


def make_client(monkeypatch):
    workbench, route = Workbench(), Route()
    projects = {OPENED: "Opened", NEVER: "Never opened", READONLY: "Shared with me"}
    monkeypatch.setattr(directory, "resolve_org_ids", lambda org, user: ["org-1"])
    app = FastAPI()
    app.include_router(directory.router)
    app.dependency_overrides[require_host_devices_enabled] = lambda: None
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(user_id=OWNER)
    app.dependency_overrides[get_host_service] = lambda: SimpleNamespace(catalog=lambda owner: [
        {"id": DEVICE, "name": "Mac", "online": True, "folders": [{"id": DEVICE_FOLDER, "name": "Notes"}]}])
    app.dependency_overrides[get_project_service] = lambda: SimpleNamespace(get_by_org_id=lambda org: [
        SimpleNamespace(id=project, name=name) for project, name in projects.items()])
    app.dependency_overrides[get_authorization_service] = lambda: SimpleNamespace(
        filter_accessible=lambda items, user: [(item, Grant(item.id != READONLY)) for item in items])
    app.dependency_overrides[get_workbench_service] = lambda: workbench
    app.dependency_overrides[get_file_route] = lambda: route
    return TestClient(app), workbench, route


def test_directory_lists_computers_and_opened_hosted_workspaces(monkeypatch):
    client, _, _ = make_client(monkeypatch)
    body = client.get("/api/v1/host-directory").json()
    assert body["gateway_url"] is None
    hosts = {host["id"]: host for host in body["hosts"]}
    assert set(hosts) == {f"device:{DEVICE}", f"workspace:{OPENED}", f"workspace:{READONLY}"}
    assert hosts[f"device:{DEVICE}"]["kind"] == "device" and hosts[f"device:{DEVICE}"]["device_id"] == DEVICE
    opened = hosts[f"workspace:{OPENED}"]
    assert (opened["kind"], opened["online"], opened["generation"]) == ("hosted-workspace", True, GENERATION)
    assert opened["folders"] == [{"id": OPENED, "name": "Opened", "state": "active",
                                  "capabilities": ["folder.read", "folder.write"]}]
    assert hosts[f"workspace:{READONLY}"]["folders"][0]["capabilities"] == ["folder.read"]
    assert hosts[f"workspace:{READONLY}"]["online"] is False


def test_file_api_is_addressed_by_host_and_keeps_each_authority(monkeypatch):
    client, workbench, route = make_client(monkeypatch)
    # A computer's folders are reached only end to end (SSH from the Desktop), never through this API.
    device = client.get(f"/api/v1/host-directory/device:{DEVICE}/folders/{DEVICE_FOLDER}/files?path=docs")
    assert (device.status_code, device.json()["detail"]) == (410, {"code": "device_folders_are_end_to_end"})
    events = client.get(f"/api/v1/host-directory/device:{DEVICE}/folders/{DEVICE_FOLDER}/events")
    assert events.status_code == 410

    hosted = client.put(f"/api/v1/host-directory/workspace:{OPENED}/folders/{OPENED}/file?generation={GENERATION}",
                        json={"path": "a.md", "content_base64": "bmV3", "expected_revision": None})
    assert hosted.json() == {"revision": "a" * 64}
    assert workbench.calls == [(OWNER, OPENED, GENERATION, {"action": "write", "path": "a.md",
                                                             "content_base64": "bmV3", "expected_revision": None})]
    # A hosted workspace needs its generation, and has exactly one folder.
    assert client.get(f"/api/v1/host-directory/workspace:{OPENED}/folders/{OPENED}/files").status_code == 422
    assert client.get(f"/api/v1/host-directory/workspace:{OPENED}/folders/{DEVICE_FOLDER}/files"
                      f"?generation={GENERATION}").status_code == 404
    assert client.get(f"/api/v1/host-directory/cloud:{OPENED}/folders/{OPENED}/files").status_code == 404
    moved = client.post(f"/api/v1/host-directory/device:{DEVICE}/folders/{DEVICE_FOLDER}/files",
                        json={"action": "move", "path": "a.md", "destination": "b/a.md"})
    assert moved.status_code == 410 and route.calls == []


def test_hosted_workspace_events_use_the_device_shape(monkeypatch):
    client, _, _ = make_client(monkeypatch)
    with client.stream("GET", f"/api/v1/host-directory/workspace:{OPENED}/folders/{OPENED}/events"
                              f"?generation={GENERATION}") as events:
        lines = [line for line in events.iter_lines() if line.startswith("data:")]
    assert lines[0] == 'data: {"paths": [], "reset": true}'
    assert lines[1] == 'data: {"paths": ["a.md"], "reset": true}'
