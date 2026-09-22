from __future__ import annotations

import hashlib
import json
from pathlib import Path


CONTRACT_SHA256 = "6158201c48957b88fc103aca756417316305d25ea3109cbe5d3e2cf1f3d1823d"
CONTRACT_PATH = (
    Path(__file__).resolve().parents[3] / "contracts" / "cloud-project-publish-v1.json"
)


def test_cloud_project_publish_contract_is_the_cross_repo_v1_fixture() -> None:
    payload = CONTRACT_PATH.read_bytes()
    canonical_payload = payload.replace(b"\r\n", b"\n")

    assert hashlib.sha256(canonical_payload).hexdigest() == CONTRACT_SHA256
    contract = json.loads(payload)
    assert contract["contract"] == "puppyone.cloud-project-publish"
    assert contract["version"] == 1
    assert contract["identity"] == {
        "organization": "explicit request field",
        "project": "server-created Project id",
        "repository_target": "project_root",
        "local_binding": False,
        "device_registration": False,
    }
    assert contract["operations"]["create_empty_project"]["request"]["org_id"] == (
        "required string"
    )
    assert contract["operations"]["issue_project_root_credential"][
        "response_echoes_credential"
    ] is False
