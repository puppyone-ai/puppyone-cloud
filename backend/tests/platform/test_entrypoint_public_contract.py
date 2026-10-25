"""Immutable historical contracts plus explicit reviewed publication/retirement deltas."""

import hashlib
import json
from datetime import UTC
from pathlib import Path


def normalize(value):
    if isinstance(value, dict):
        return {
            key: normalize(item)
            for key, item in value.items()
            if key not in {"description", "summary", "tags"}
        }
    if isinstance(value, list):
        return [normalize(item) for item in value]
    return value


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def contract(openapi):
    paths = normalize(openapi["paths"])
    # Existing FastAPI multi-method proxy routes choose their ID from a set.
    # Exclude ONLY those unstable IDs; their URLs, verbs and full schemas remain checked.
    for name in ("/api/v1/mcp/proxy", "/api/v1/mcp/proxy/{path}"):
        for operation in paths[name].values():
            operation.pop("operationId", None)
    return {
        "paths": {path: fingerprint(value) for path, value in paths.items()},
        "schemas": {
            name: fingerprint(value)
            for name, value in normalize(openapi["components"]["schemas"]).items()
        },
        "securitySchemes": normalize(openapi["components"].get("securitySchemes", {})),
    }


def test_exact_resource_retirement_preserves_every_unrelated_contract():
    from src.main import app

    expected = json.loads(
        (Path(__file__).with_name("entrypoint_contract_c28e38a3.json")).read_text()
    )
    # Keep the original and the four published S2 fixtures immutable. Removal
    # is exact and reviewed, never a wildcard exemption for unrelated APIs.
    for filename in (
        "synchronize_contract_delta.json",
        "access_contract_delta.json",
        "github_database_contract_delta.json",
        "aggregate_contract_delta.json",
    ):
        delta = json.loads(Path(__file__).with_name(filename).read_text())
        for category in ("paths", "schemas"):
            assert not expected["contract"][category].keys() & delta[category].keys()
            expected["contract"][category].update(delta[category])
    retirement = json.loads(
        Path(__file__).with_name("retired_entrypoint_contract_delta.json").read_text()
    )
    for category in ("paths", "schemas"):
        delta = retirement[category]
        assert delta["changed"] == {}  # canonical and unrelated wires remain identical
        for name, digest in delta["removed"].items():
            assert expected["contract"][category].pop(name) == digest
        for name, digest in delta["added"].items():
            assert name not in expected["contract"][category]
            expected["contract"][category][name] = digest
    # ISSUE-062 adds ref/base metadata and byte-path alternatives only to these
    # content reads. Match exact before/after digests; do not rewrite any
    # historical publication/retirement fixture or exempt other APIs.
    native = json.loads(
        Path(__file__).with_name("native_content_read_contract_delta.json").read_text()
    )
    assert set(native["paths"]) == {
        f"/api/v1/content/{{project_id}}/{action}"
        for action in ("ls", "cat", "raw", "stat", "tree")
    }
    assert set(native["schemas"]) == {
        "ListDirResponse",
        "ReadFileResponse",
        "StatResponse",
        "TreeResponse",
        "VersionEntryResponse",
    }
    for category in ("paths", "schemas"):
        for name, change in native[category].items():
            assert expected["contract"][category][name] == change["before"]
            expected["contract"][category][name] = change["after"]
    # The optional bulk precondition follows the existing single-write contract.
    # Only this request schema changes; paths and historical fixtures do not.
    bulk = json.loads(Path(__file__).with_name("product_bulk_base_contract_delta.json").read_text())
    assert bulk["paths"] == {} and set(bulk["schemas"]) == {"BulkWriteRequest"}
    change = bulk["schemas"]["BulkWriteRequest"]
    assert expected["contract"]["schemas"]["BulkWriteRequest"] == change["before"]
    expected["contract"]["schemas"]["BulkWriteRequest"] = change["after"]
    product = json.loads(
        Path(__file__).with_name("native_product_write_contract_delta.json").read_text()
    )
    assert product["paths"] == {}
    assert set(product["schemas"]) == {
        "WriteFileRequest",
        "BulkWriteRequest",
        "MkdirRequest",
        "MoveRequest",
        "RemoveRequest",
    }
    assert set(product["added_schemas"]) == {"NativeProductWrite"}
    for name, change in product["schemas"].items():
        assert expected["contract"]["schemas"][name] == change["before"]
        expected["contract"]["schemas"][name] = change["after"]
    for name, digest in product["added_schemas"].items():
        assert name not in expected["contract"]["schemas"]
        expected["contract"]["schemas"][name] = digest
    status = json.loads(
        Path(__file__).with_name("native_operation_status_contract_delta.json").read_text()
    )
    assert set(status["paths"]) == {
        "/api/v1/content/{project_id}/operations/{request_key}",
        "/git/{project_id}.git/operations/{request_key}",
    }
    assert set(status["schemas"]) == {
        "NativeOperationStatusResponse",
        "NativeOperationStatusEnvelope",
    }
    for category in ("paths", "schemas"):
        assert not expected["contract"][category].keys() & status[category].keys()
        expected["contract"][category].update(status[category])
    refs = json.loads(Path(__file__).with_name("native_ref_read_contract_delta.json").read_text())
    assert set(refs["changed_paths"]) == {
        f"/api/v1/content/{{project_id}}/{action}"
        for action in ("ls", "cat", "raw", "stat", "tree")
    }
    assert set(refs["paths"]) == {"/api/v1/content/{project_id}/refs", "/git/{project_id}.git/refs"}
    assert set(refs["schemas"]) == {
        "NativeRepositoryRefResponse",
        "NativeRepositoryMetadataResponse",
        "NativeRepositoryMetadataEnvelope",
    }
    for name, change in refs["changed_paths"].items():
        assert expected["contract"]["paths"][name] == change["before"]
        expected["contract"]["paths"][name] = change["after"]
    for category in ("paths", "schemas"):
        assert not expected["contract"][category].keys() & refs[category].keys()
        expected["contract"][category].update(refs[category])
    bare = json.loads(
        Path(__file__).with_name("native_bare_management_contract_delta.json").read_text()
    )
    assert bare["paths"] == {}
    assert set(bare["changed_paths"]) == {"/api/v1/content/{project_id}/head"}
    assert set(bare["schemas"]) == {
        "NativeRepositoryCreate",
        "RepositoryHeadState",
        "RepositoryHeadUpdate",
    }
    assert set(bare["changed_schemas"]) == {"ProjectCreate"}
    for name, change in bare["changed_paths"].items():
        assert expected["contract"]["paths"][name] == change["before"]
        expected["contract"]["paths"][name] = change["after"]
    for name, change in bare["changed_schemas"].items():
        assert expected["contract"]["schemas"][name] == change["before"]
        expected["contract"]["schemas"][name] = change["after"]
    for category in ("paths", "schemas"):
        assert not expected["contract"][category].keys() & bare[category].keys()
        expected["contract"][category].update(bare[category])
    # The independently merged Cloud Agent runtime replaces the old request-
    # owned POST /agents with its reviewed durable-run protocol. Keep this
    # explicit delta separate from native Git and every historical fixture.
    agent = json.loads(
        Path(__file__).with_name("cloud_agent_runtime_contract_delta.json").read_text()
    )
    assert set(agent["paths"]["removed"]) == {"/api/v1/agents"}
    assert set(agent["paths"]["added"]) == {
        "/api/v1/agents/runs",
        "/api/v1/agents/runs/{run_id}",
        "/api/v1/agents/requests/{project_id}/{request_id}",
        "/api/v1/agents/sessions/{session_id}/runs",
        "/api/v1/agents/runs/{run_id}/stop",
        "/api/v1/agents/runs/{run_id}/events",
        "/api/v1/agents/runs/{run_id}/approvals/{call_id}",
    }
    assert set(agent["schemas"]["removed"]) == {"AgentRequest", "ChatHistoryItem"}
    assert set(agent["schemas"]["added"]) == {"SubmitRun", "Approval"}
    for category in ("paths", "schemas"):
        assert agent[category]["changed"] == {}
        for name, digest in agent[category]["removed"].items():
            assert expected["contract"][category].pop(name) == digest
        for name, digest in agent[category]["added"].items():
            assert name not in expected["contract"][category]
            expected["contract"][category][name] = digest
    # Native-only cutover: exact retirement and SHA-256 history widening.
    engine = json.loads(
        Path(__file__).with_name("native_engine_retirement_contract_delta.json").read_text()
    )
    assert set(engine["paths"]["removed"]) == {
        "/api/v1/local-snapshots",
        "/api/v1/scope-sync/policy",
        "/api/v1/content/{project_id}/conflicts/pending",
        "/api/v1/scope-sync/ap/events",
        "/api/v1/scope-sync/activity",
        "/api/v1/content/{project_id}/conflicts/{pending_conflict_id}",
        "/api/v1/local-snapshots/{snapshot_id}/promote",
        "/api/v1/scope-sync/events",
        "/api/v1/local-snapshots/{snapshot_id}",
        "/api/v1/scope-sync/stats",
        "/api/v1/scope-sync/settings",
        "/api/v1/local-snapshots/{snapshot_id}/blobs",
        "/api/v1/content/{project_id}/conflicts/{pending_conflict_id}/resolve",
    }
    assert set(engine["paths"]["changed"]) == {
        "/api/v1/ap-fs/admin/fs-index/rebuild",
        "/api/v1/ap-fs/admin/object-integrity",
        "/api/v1/ap-fs/admin/text-index/rebuild",
    }
    assert engine["paths"]["added"] == {}
    assert set(engine["schemas"]["removed"]) == {
        "ShadowSnapshotResponse",
        "ApiResponse_UpsertShadowSnapshotResponse_",
        "ApiResponse__PromoteResponse_",
        "SyncSettingsBody",
        "ApiResponse_list_ShadowSnapshotResponse__",
        "ResolveConflictRequest",
        "UpsertShadowSnapshotRequest",
        "ShadowSnapshotEntry",
        "_ObjectIntegrityRequest",
        "_PromoteRequest",
        "_PromoteResponse",
        "PendingConflictDetail",
        "ApiResponse_list_PendingConflictSummary__",
        "PendingConflictSummary",
        "ResolveConflictResponse",
        "_BlobUploadRequest",
        "ApiResponse_ResolveConflictResponse_",
        "ApiResponse_PendingConflictDetail_",
        "UpsertShadowSnapshotResponse",
        "_BlobUploadResponse",
        "ApiResponse__BlobUploadResponse_",
        "_BlobUploadEntry",
    }
    assert set(engine["schemas"]["changed"]) == {
        "FileVersionInfo",
        "VersionHistoryResponse",
        "VersionHistoryRef",
    }
    assert engine["schemas"]["added"] == {}
    for category in ("paths", "schemas"):
        for name, digest in engine[category]["removed"].items():
            assert expected["contract"][category].pop(name) == digest
        for name, change in engine[category]["changed"].items():
            assert expected["contract"][category][name] == change["before"]
            expected["contract"][category][name] = change["after"]
    # Agent history is a bounded read operation. Preserve every historical
    # fixture and register only this new route; no other wire may drift.
    history = json.loads(
        Path(__file__).with_name("cloud_agent_history_contract_delta.json").read_text()
    )
    assert set(history["paths"]) == {"/api/v1/agents/sessions"}
    assert history["schemas"] == {}
    assert not expected["contract"]["paths"].keys() & history["paths"].keys()
    expected["contract"]["paths"].update(history["paths"])
    resource = json.loads(Path(__file__).with_name("workspace_resource_contract_delta.json").read_text())
    assert set(resource["paths"]) == {"/api/v1/content/{project_id}/resource"}
    assert set(resource["schemas"]) == {"ResourceResponse", "ApiResponse_ResourceResponse_"}
    for category in ("paths", "schemas"):
        assert not expected["contract"][category].keys() & resource[category].keys()
        expected["contract"][category].update(resource[category])
    # Native Supabase sessions retire exactly the custom Redis login broker.
    auth = json.loads(Path(__file__).with_name("native_auth_retirement_contract_delta.json").read_text())
    assert set(auth["paths"]["removed"]) == {
        "/api/v1/auth/check-email",
        *(f"/api/v1/auth/desktop/{action}" for action in ("start", "bind", "complete", "exchange", "callback")),
    }
    assert set(auth["schemas"]["removed"]) == {
        "ApiResponse_CheckEmailResponse_", "CheckEmailRequest", "CheckEmailResponse",
        "ApiResponse_DesktopCompleteResponse_", "ApiResponse_DesktopStartResponse_",
        "DesktopBindRequest", "DesktopCompleteRequest", "DesktopCompleteResponse",
        "DesktopExchangeRequest", "DesktopStartRequest", "DesktopStartResponse",
    }
    for category in ("paths", "schemas"):
        assert auth[category]["added"] == auth[category]["changed"] == {}
        for name, digest in auth[category]["removed"].items():
            assert expected["contract"][category].pop(name) == digest
    email = json.loads(Path(__file__).with_name("email_first_auth_contract_delta.json").read_text())
    assert set(email["paths"]) == {"/api/v1/auth/check-email"}
    assert set(email["schemas"]) == {"CheckEmailRequest", "CheckEmailResponse", "ApiResponse_CheckEmailResponse_"}
    for category in ("paths", "schemas"):
        assert not expected["contract"][category].keys() & email[category].keys()
        expected["contract"][category].update(email[category])
    parity = json.loads(Path(__file__).with_name("workspace_viewer_parity_contract_delta.json").read_text())
    assert set(parity["paths"]) == {
        "/api/v1/content/{project_id}/resource", "/api/v1/content/{project_id}/inline",
    }
    assert set(parity["schemas"]) == {"InlineSignRequest"}
    for category in ("paths", "schemas"):
        for name, change in parity[category].items():
            assert expected["contract"][category][name] == change["before"]
            expected["contract"][category][name] = change["after"]
    workbench = json.loads(
        Path(__file__).with_name("workbench_selection_contract_delta.json").read_text()
    )
    assert workbench["paths"] == {}
    assert set(workbench["schemas"]) == {"SubmitRun"}
    for name, change in workbench["schemas"].items():
        assert expected["contract"]["schemas"][name] == change["before"]
        expected["contract"]["schemas"][name] = change["after"]
    workbench = json.loads(Path(__file__).with_name("live_workbench_contract_delta.json").read_text())
    assert set(workbench["paths"]) == {
        "/api/v1/projects/{project_id}/workbench",
        "/api/v1/projects/{project_id}/workbench/files",
        "/api/v1/projects/{project_id}/workbench/file",
        "/api/v1/projects/{project_id}/workbench/events",
    }
    assert set(workbench["schemas"]) == {"FileOperation", "DirectoryOperation"}
    for category in ("paths", "schemas"):
        assert not expected["contract"][category].keys() & workbench[category].keys()
        expected["contract"][category].update(workbench[category])
    ssh = json.loads(Path(__file__).with_name("hosted_ssh_contract_delta.json").read_text())
    assert set(ssh["paths"]) == {
        "/api/v1/projects/{project_id}/workbench/ssh-grants",
        "/api/v1/projects/{project_id}/workbench/ssh-grants/{grant_id}",
    }
    assert set(ssh["schemas"]) == {"SshGrantRequest"}
    for category in ("paths", "schemas"):
        assert not expected["contract"][category].keys() & ssh[category].keys()
        expected["contract"][category].update(ssh[category])
    waiting = json.loads(
        Path(__file__).with_name("workspace_open_wait_contract_delta.json").read_text()
    )
    assert set(waiting["paths"]) == {"/api/v1/projects/{project_id}/workbench"}
    assert waiting["schemas"] == {}
    for name, change in waiting["paths"].items():
        assert expected["contract"]["paths"][name] == change["before"]
        expected["contract"]["paths"][name] = change["after"]
    # The shared History API was already merged at bc8714ec. Preserve the
    # historical fixtures and record only its exact reviewed wire delta.
    history = json.loads(Path(__file__).with_name("shared_history_contract_delta.json").read_text())
    assert set(history["paths"]) == {
        "/api/v1/content/{project_id}/commits", "/api/v1/content/{project_id}/commit-detail",
    }
    assert set(history["schemas"]) == {"FileVersionInfo", "ApiResponse_FileVersionInfo_"}
    for category, changes in history.items():
        for name, change in changes.items():
            assert expected["contract"][category].get(name) == change["before"]
            expected["contract"][category][name] = change["after"]
    internal_nodes = json.loads(
        Path(__file__).with_name("internal_nodes_retirement_contract_delta.json").read_text()
    )
    assert set(internal_nodes["paths"]["removed"]) == {
        f"/internal/nodes/{action}"
        for action in ("resolve-path", "list", "read", "write", "create", "rm", "rename", "move")
    }
    assert internal_nodes["schemas"] == {"removed": {}, "added": {}, "changed": {}}
    assert internal_nodes["paths"]["added"] == internal_nodes["paths"]["changed"] == {}
    for name, digest in internal_nodes["paths"]["removed"].items():
        assert expected["contract"]["paths"].pop(name) == digest
    # The host-local Agent workspace had no callers and is retired exactly;
    # Hosted Workspaces under /projects/{project_id}/workbench are unchanged.
    local_workspace = json.loads(
        Path(__file__).with_name("local_workspace_retirement_contract_delta.json").read_text()
    )
    assert set(local_workspace["paths"]["removed"]) == {
        "/api/v1/workspace/create",
        "/api/v1/workspace/{agent_id}/complete",
        "/api/v1/workspace/{agent_id}/status",
    }
    assert set(local_workspace["schemas"]["removed"]) == {
        "CreateWorkspaceRequest", "CreateWorkspaceResponse", "CompleteWorkspaceResponse",
        "WorkspaceStatusResponse", "ApiResponse_CreateWorkspaceResponse_",
        "ApiResponse_CompleteWorkspaceResponse_", "ApiResponse_WorkspaceStatusResponse_",
    }
    for category in ("paths", "schemas"):
        assert local_workspace[category]["added"] == local_workspace[category]["changed"] == {}
        for name, digest in local_workspace[category]["removed"].items():
            assert expected["contract"][category].pop(name) == digest
    # Scope Sandbox SSH is retired exactly; Hosted Workspace SSH grants under
    # /projects/{project_id}/workbench/ssh-grants are unchanged.
    scope_sandbox = json.loads(
        Path(__file__).with_name("scope_sandbox_retirement_contract_delta.json").read_text()
    )
    assert set(scope_sandbox["paths"]["removed"]) == {
        f"/api/v1/scope-sandboxes/{action}" for action in ("connect", "providers", "status", "revoke")
    }
    assert set(scope_sandbox["schemas"]["removed"]) == {"ConnectRequest", "RevokeRequest"}
    for category in ("paths", "schemas"):
        assert scope_sandbox[category]["added"] == scope_sandbox[category]["changed"] == {}
        for name, digest in scope_sandbox[category]["removed"].items():
            assert expected["contract"][category].pop(name) == digest
    lifecycle = json.loads(Path(__file__).with_name("access_lifecycle_contract_delta.json").read_text())
    assert set(lifecycle["paths"]) == {
        "/api/v1/access/registrations",
        "/api/v1/access/registrations/{registration_id}",
        "/api/v1/access/registrations/{registration_id}/credential",
        "/api/v1/access/registrations/{registration_id}/grants",
        "/api/v1/access/registrations/{registration_id}/requests",
        "/api/v1/access/requests", "/api/v1/access/credentials/renew",
        "/api/v1/projects/{project_id}/access/invitations",
        "/api/v1/projects/{project_id}/access/{collection}",
        "/api/v1/projects/{project_id}/access/requests/{request_id}/decision",
        "/api/v1/projects/{project_id}/access/invitations/{invitation_id}",
        "/api/v1/projects/{project_id}/access/grants/{grant_id}",
        "/api/v1/projects/{project_id}/access/sessions/{session_id}",
    }
    for category in ("paths", "schemas"):
        assert not expected["contract"][category].keys() & lifecycle[category].keys()
        expected["contract"][category].update(lifecycle[category])
    # Search Tools and the vector (Turbopuffer) backend are retired exactly. Tool
    # type enums lose "search" (ToolOut keeps reading existing rows) and the
    # dashboard drops index fields; every other wire stays identical.
    search = json.loads(
        Path(__file__).with_name("search_tool_retirement_contract_delta.json").read_text()
    )
    assert set(search["paths"]["removed"]) == {
        "/api/v1/tools/search",
        "/api/v1/tools/{tool_id}/search-index",
        "/internal/tools/{tool_id}/search",
        "/internal/turbopuffer/config",
        "/internal/turbopuffer/namespaces",
        *(
            f"/internal/turbopuffer/namespaces/{{namespace}}{suffix}"
            for suffix in (
                "", "/delete_all", "/metadata", "/multi_query", "/query", "/recall",
                "/warm_cache", "/write",
            )
        ),
    }
    assert set(search["schemas"]["removed"]) == {
        "ApiResponse_SearchIndexTaskOut_", "ApiResponse_dict_str__Any__", "SearchChunk",
        "SearchIndexTaskOut", "SearchResultItem", "SearchToolQueryInput",
        "SearchToolQueryResponse",
    }
    assert set(search["schemas"]["changed"]) == {"DashboardTool", "ToolCreate", "ToolOut", "ToolUpdate"}
    assert search["paths"]["changed"] == search["paths"]["added"] == search["schemas"]["added"] == {}
    for category in ("paths", "schemas"):
        for name, digest in search[category]["removed"].items():
            assert expected["contract"][category].pop(name) == digest
        for name, change in search[category]["changed"].items():
            assert expected["contract"][category][name] == change["before"]
            expected["contract"][category][name] = change["after"]
    assert contract(app.openapi()) == expected["contract"]


def test_access_domain_kind_and_target_are_serialized_without_legacy_provider():
    from datetime import datetime
    from types import SimpleNamespace
    from unittest.mock import Mock

    from src.platform.access.models import AccessSurface
    from src.platform.access.public_router import list_project_access_surfaces
    from src.platform.repository_target.models import ProjectRootTarget

    surface = AccessSurface(
        id="surface-1",
        target=ProjectRootTarget(project_id="project-1"),
        kind="cli",
        name="CLI",
        direction="bidirectional",
        config={},
        policy={},
        oauth_connection_id=None,
        trigger={"type": "manual"},
        status="active",
        last_run_at=None,
        last_run_id=None,
        error_message=None,
        created_by="user-1",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    service = Mock()
    service.list.return_value = [surface]
    response = list_project_access_surfaces(
        kind="cli",
        direction=None,
        authorized=SimpleNamespace(project=SimpleNamespace(id="project-1")),
        service=service,
    )
    payload = response.model_dump(mode="json")
    assert payload["data"][0]["kind"] == "cli"
    assert payload["data"][0]["target"] == {"kind": "project_root", "project_id": "project-1"}
    assert "provider" not in payload["data"][0]
    # Read the domain inventory, then reject unexpected historical source rows;
    # silently filtering them would turn required repair into empty success.
    service.list.assert_called_once_with(
        "project-1", kind="cli", direction=None, access_surface_only=False
    )
    assert payload["message"] == "Access surfaces listed"


def test_run_binding_id_is_authorized_and_serialized_at_http_boundary(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from src.platform.auth.models import CurrentUser
    from src.platform.synchronize import router
    from src.platform.synchronize.public_router import get_synchronize_run
    from src.platform.synchronize.run_repository import SyncRun
    from tests.authorization_fakes import authorization_for

    run = SyncRun(id="run-1", synchronize_binding_id="source-1", stdout="hello")
    repository = Mock()
    repository.get_by_id.return_value = run
    monkeypatch.setattr(router, "_get_run_repo", lambda: repository)
    service = Mock()
    service.repository.get_by_id.return_value = SimpleNamespace(
        id="source-1", project_id="project-1"
    )
    response = get_synchronize_run(
        "run-1",
        service=service,
        authorization=authorization_for("project-1", role="viewer"),
        current_user=CurrentUser(user_id="user-1", role="authenticated"),
    )
    payload = response.model_dump(mode="json")["data"]
    assert payload["synchronize_binding_id"] == "source-1"
    assert "connection_id" not in payload and "access_point_id" not in payload
    assert payload["stdout"] == "hello"
    service.repository.get_by_id.assert_called_once_with("source-1")
