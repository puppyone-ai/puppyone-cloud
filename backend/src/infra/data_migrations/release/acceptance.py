"""Bounded authenticated acceptance on a newly created, owned test project."""

from __future__ import annotations

import base64
import time
from urllib.parse import quote
from uuid import uuid4

import httpx

# Initial allocation budget for a first workspace (machine, volume, checkout).
WORKBENCH_START_SECONDS = 240
API_READY_SECONDS = 60


def wait_api_ready(client, *, timeout_seconds=API_READY_SECONDS, clock=time.monotonic, sleep=time.sleep):
    """Wait for the public edge, which may lag provider deployment success.

    Only this read-only probe retries. Login and business mutations retain
    their original failure behavior and are never submitted before readiness.
    """
    deadline = clock() + timeout_seconds
    while (remaining := deadline - clock()) > 0:
        try:
            response = client.get("/health", timeout=min(10, remaining))
        except httpx.TransportError:
            pass
        else:
            if response.status_code == 200:
                try:
                    body = response.json()
                except ValueError:
                    raise RuntimeError("Public API health returned invalid JSON") from None
                if not isinstance(body, dict) or body.get("status") != "ready":
                    raise RuntimeError("Public API health did not report ready")
                return
            if response.status_code not in {502, 503, 504}:
                raise RuntimeError(f"Public API readiness HTTP {response.status_code}")
        remaining = deadline - clock()
        if remaining > 0:
            sleep(min(2, remaining))
    raise TimeoutError("Public API did not become ready before acceptance")


def accept_billing_reads(call, org_id: str) -> dict:
    """Use the signed-in owner's normal read routes; never request a purchase."""
    catalog = call("GET", "/billing/catalog")
    endpoint = "/billing/organizations/" + quote(org_id, safe="")
    summary = call("GET", endpoint + "/summary")
    usage = call("GET", endpoint + "/usage")
    runtime = usage.get("runtime") or {}
    if (
        not isinstance(catalog.get("plans"), list) or not catalog["plans"]
        or summary.get("org_id") != org_id
        or not isinstance(summary.get("plan_id"), str) or not summary["plan_id"]
        or runtime.get("org_id") != org_id
        or any(type(runtime.get(key)) is not int
               for key in ("available_units", "reserved_units", "consumed_units"))
    ):
        raise RuntimeError("Release billing reads returned an incomplete account")
    return {"billing_catalog_read": True, "billing_summary_read": True, "billing_usage_read": True}


def open_workbench(call, endpoint: str, *, clock=time.monotonic) -> dict:
    """Follow the Desktop's opening protocol: request, then long-poll status.

    The API only records the open intent (202 with a pending operation); the
    worker's sandbox module starts the machine. A failed or abandoned operation
    fails acceptance instead of waiting for the deadline.
    """
    workspace = call("POST", endpoint, timeout=60)
    deadline = clock() + WORKBENCH_START_SECONDS
    while (workspace or {}).get("state") != "running":
        operation = (workspace or {}).get("operation") or {}
        if operation.get("phase") == "failed" or operation.get("error"):
            raise RuntimeError("Acceptance workspace failed to start")
        if not operation.get("pending"):
            raise RuntimeError("Acceptance workspace open is not progressing")
        if clock() >= deadline:
            raise TimeoutError("Acceptance workspace did not start")
        workspace = call("GET", endpoint, params={"wait_seconds": 20}, timeout=60).get(
            "workspace"
        )
    return workspace


def accept_workbench(call, project_id: str, published: str) -> tuple[dict, str]:
    """Exercise the Desktop's live-file path before declaring a release usable."""
    endpoint = f"/projects/{project_id}/workbench"
    # This GET must exist even before the user's first workspace is allocated.
    call("GET", endpoint)
    workspace = open_workbench(call, endpoint)
    if (
        workspace.get("project_id") != project_id
        or workspace.get("state") != "running"
        or not workspace.get("id")
        or not workspace.get("generation")
    ):
        raise RuntimeError("Acceptance workspace is not ready for this project")
    params = {"generation": workspace["generation"], "path": "release-check.md"}
    listing = call("GET", endpoint + "/files", params={**params, "path": ""})
    if not any(entry.get("name") == params["path"] for entry in listing.get("entries", [])):
        raise RuntimeError("Acceptance workspace did not receive the published file")
    original = call("GET", endpoint + "/file", params=params)
    if base64.b64decode(original["content_base64"], validate=True).decode() != published:
        raise RuntimeError("Acceptance workspace differs from the published file")
    edited = "live-" + uuid4().hex
    changed = call(
        "PUT",
        endpoint + "/file",
        params=params,
        json={
            "path": params["path"],
            "content_base64": base64.b64encode(edited.encode()).decode(),
            "expected_revision": original["revision"],
        },
    )
    saved = call("GET", endpoint + "/file", params=params)
    if base64.b64decode(saved["content_base64"], validate=True).decode() != edited or saved.get(
        "revision"
    ) != changed.get("revision"):
        raise RuntimeError("Acceptance live-file read differs from the saved edit")
    hosted = call("GET", f"/content/{project_id}/cat", params={"path": params["path"]})
    if hosted.get("content_text") != published:
        raise RuntimeError("Acceptance live edit unexpectedly changed the published file")
    return workspace, edited


def accept(config: dict) -> dict:
    with httpx.Client(
        base_url=config["api_url"].rstrip("/"), timeout=45, trust_env=False
    ) as client:
        wait_api_ready(client)

        def call(method, path, **kwargs):
            response = client.request(method, "/api/v1" + path, **kwargs)
            if response.is_error:
                raise RuntimeError(f"Release acceptance HTTP {response.status_code}")
            body = response.json()
            return body.get("data", body)

        login = call(
            "POST", "/auth/login", json={"email": config["email"], "password": config["password"]}
        )
        client.headers["Authorization"] = "Bearer " + login["access_token"]
        client.headers["X-PuppyOne-Repository-Contract"] = "2"
        # Agent calls are prepaid. This account starts like any new user, with
        # its one-time trial; claiming again changes nothing.
        call("POST", "/ai/trial", json={})
        billing_reads = (
            accept_billing_reads(call, config["org_id"]) if config.get("billing_reads") else {}
        )
        project = call(
            "POST",
            "/projects/",
            headers={"Idempotency-Key": str(uuid4())},
            json={"name": "Release acceptance " + uuid4().hex[:8], "org_id": config["org_id"]},
        )
        project_id = project["id"]
        run_id = None
        terminal = False
        try:
            # Clients enable the Project-root Git and CLI surfaces before using
            # Agent; a Cloud workspace clones through that Git surface.
            surfaces = call(
                "POST",
                f"/projects/{project_id}/access/surfaces/enable-target",
                json={"target": {"kind": "project_root", "project_id": project_id}},
            )
            if {surface.get("kind") for surface in surfaces} < {"git_remote", "cli"}:
                raise RuntimeError("Acceptance project access was not enabled")
            nonce = "release-" + uuid4().hex
            written = call(
                "POST",
                f"/content/{project_id}/write",
                json={"path": "release-check.md", "content": nonce, "node_type": "markdown"},
            )
            if not written.get("commit_id"):
                raise RuntimeError("Acceptance write produced no version")
            body = call("GET", f"/content/{project_id}/cat", params={"path": "release-check.md"})
            if body.get("content_text") != nonce:
                raise RuntimeError("Acceptance read differs from write")
            workspace, live_nonce = accept_workbench(call, project_id, nonce)
            started = time.monotonic()
            run = call(
                "POST",
                "/agents/runs",
                json={
                    "project_id": project_id,
                    "request_id": str(uuid4()),
                    "prompt": "Read release-check.md and reply with its exact content. Do not modify any files.",
                },
            )
            run_id = run["id"]
            while time.monotonic() - started < 300:
                snapshot = call("GET", "/agents/runs/" + run_id)
                for tool in snapshot.get("tools", []):
                    if tool["state"] == "waiting":
                        call(
                            "POST",
                            f"/agents/runs/{run_id}/approvals/{tool['call_id']}",
                            json={"decision_id": str(uuid4()), "allow": False},
                        )
                        raise RuntimeError("Read-only Agent acceptance requested a mutation")
                if snapshot["state"] == "succeeded":
                    terminal = True
                    if live_nonce not in snapshot.get("snapshot", {}).get("text", ""):
                        raise RuntimeError("Agent did not read the live workspace edit")
                    current = call("GET", f"/projects/{project_id}/workbench").get("workspace")
                    if not current or any(
                        current.get(key) != workspace[key] for key in ("id", "generation")
                    ):
                        raise RuntimeError("Agent did not retain the opened workspace")
                    return {
                        **billing_reads,
                        "authenticated_read_write": True,
                        "git_commit_created": True,
                        "agent_reply": True,
                        "live_files_read_write": True,
                        "live_edit_unpublished": True,
                        "agent_reads_live_edit": True,
                        "agent_seconds": round(time.monotonic() - started, 3),
                    }
                if snapshot["state"] in {"failed", "stopped", "conflict", "outcome_unknown"}:
                    terminal = True
                    raise RuntimeError("Agent acceptance did not succeed")
                time.sleep(2)
            raise TimeoutError("Agent acceptance timed out")
        finally:
            try:
                if run_id and not terminal:
                    call("POST", "/agents/runs/" + run_id + "/stop")
            finally:
                call("DELETE", "/projects/" + project_id)
