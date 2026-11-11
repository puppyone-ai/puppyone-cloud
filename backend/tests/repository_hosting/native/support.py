"""Helpers for the native repository-hosting tests (tests/README.md: no test imports a test module)."""

import base64
import hashlib
import json
from datetime import UTC, datetime

from src.platform.entitlements.models import (
    REQUIRED_ENTITLEMENT_FEATURES,
    REQUIRED_ENTITLEMENT_LIMITS,
    EntitlementUpsert,
)


def publish_entitlement(app, org, *, max_file_bytes=8 * 1024**2, max_storage_bytes=64 * 1024**2):
    # Simulate the billing publisher at its real authenticated ingress; no SQL
    # entitlement/enrollment fixtures and no external billing network calls.
    limits = {key: 100 for key in REQUIRED_ENTITLEMENT_LIMITS}
    limits.update(
        {
            "storage.max_bytes": max_storage_bytes,
            "upload.max_single_file_bytes": max_file_bytes,
            "seats.purchased": 1,
        }
    )
    payload = EntitlementUpsert(
        org_id=org,
        schema_version="1.0",
        plan_id="test-bare",
        status="active",
        source="puppypay",
        entitlements={
            "features": {key: True for key in REQUIRED_ENTITLEMENT_FEATURES},
            "limits": limits,
            "allow": {"access_surface_kinds": "*"},
        },
        seat_quantity=1,
        catalog_version="test-1",
        source_revision=1,
        effective_at=datetime.now(UTC),
        payload_hash="0" * 64,
    ).model_dump(mode="json")
    unsigned = {key: value for key, value in payload.items() if key != "payload_hash"}
    if unsigned.get("source_quote_id") is None:
        unsigned.pop("source_quote_id", None)
    payload["payload_hash"] = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    response = app.client.post(
        "/internal/billing/entitlements/upsert",
        json=payload,
        headers={"X-Internal-Secret": app.env["INTERNAL_API_SECRET"]},
    )
    assert response.status_code == 200, response.text


# The repository shapes Desktop's first push must deliver intact.
def commit(client, name, content, message):
    return client.commit({name: content.encode()}, message)


def linear(client):
    commit(client, "README.md", "first\n", "first")
    commit(client, "notes.md", "second\n", "second")


def merge_tip(client):
    commit(client, "README.md", "base\n", "base")
    client.run("checkout", "-q", "-b", "feature")
    commit(client, "feature.md", "feature\n", "feature")
    client.run("checkout", "-q", "main")
    commit(client, "main.md", "main\n", "main")
    client.run(
        "merge", "-q", "--no-ff", "feature", "-m", "Merge pull request #1 from example/feature"
    )


def merge_in_history(client):
    merge_tip(client)
    commit(client, "after.md", "after the merge\n", "after the merge")


def octopus_merge_tip(client):
    commit(client, "README.md", "base\n", "base")
    for name in ("one", "two", "three"):
        client.run("checkout", "-q", "-b", name)
        commit(client, f"{name}.md", f"{name}\n", name)
        client.run("checkout", "-q", "main")
    client.run("merge", "-q", "--no-ff", "one", "two", "three", "-m", "octopus")


def unrelated_histories_merge(client):
    commit(client, "README.md", "base\n", "base")
    client.run("checkout", "-q", "--orphan", "imported")
    client.run("rm", "-rq", "--cached", ".")
    (client.path / "README.md").unlink()
    commit(client, "imported.md", "imported\n", "imported")
    client.run("checkout", "-q", "main")
    client.run(
        "merge",
        "-q",
        "--no-ff",
        "--allow-unrelated-histories",
        "imported",
        "-m",
        "bring in imported",
    )


def empty_snapshot(client):
    client.run("commit", "-q", "--allow-empty", "-m", "Initial snapshot")


SHAPES = {
    "linear": (linear, 1),
    "merge_tip": (merge_tip, 2),
    "merge_in_history": (merge_in_history, 1),
    "octopus_merge_tip": (octopus_merge_tip, 4),
    "unrelated_histories_merge": (unrelated_histories_merge, 2),
    "empty_snapshot": (empty_snapshot, 0),
}


def authorization(secret):
    return "Basic " + base64.b64encode(f"x-puppyone-token:{secret}".encode()).decode()


def scoped(remote, *headers):
    """Desktop scopes every header to the canonical remote URL."""
    return [arg for header in headers for arg in ("-c", f"http.{remote}.extraHeader={header}")]


def parents(client, ref):
    return client.text("rev-list", "--parents", "-n", "1", ref).split()[1:]
