from __future__ import annotations

import copy
import json
import time
from pathlib import Path

import pytest

from src.infra.data_migrations.historical_release import (
    assert_authorized,
    load_plan,
    verify_continuity,
)


def test_checked_in_historical_sequence_pins_real_schema_and_artifacts():
    plan = load_plan(Path(__file__).resolve().parents[4])
    assert len(plan["schema_sha256"]) == 101
    assert len(plan["data_sha256"]) == 6


def test_upgrade_requires_recent_backup_and_freeze_bound_to_target():
    plan = {"id": "release"}
    evidence = {
        "project_ref": "production",
        "plan_id": "release",
        "write_freeze_verified": True,
        "restore_point": "verified-backup",
        "expires_at": time.time() + 3600,
    }
    assert_authorized(plan, "production", {"LEGACY_UPGRADE_AUTHORIZATION": json.dumps(evidence)})
    for key, value in [
        ("project_ref", "staging"),
        ("write_freeze_verified", False),
        ("restore_point", ""),
        ("expires_at", time.time() - 1),
    ]:
        with pytest.raises(ValueError, match="backup and write-freeze"):
            assert_authorized(
                plan,
                "production",
                {"LEGACY_UPGRADE_AUTHORIZATION": json.dumps({**evidence, key: value})},
            )


def test_continuity_rejects_missing_customer_facts_and_credentials():
    original = {
        "projects": ["p"],
        "users": ["u"],
        "profiles": ["profile"],
        "commits": [1],
        "org_members": [{"id": "m", "role": "owner"}],
        "members": [{"project_id": "p", "user_id": "u", "role": "admin"}],
        "credentials": [{"id": "c", "key_hash": "hash", "status": "active"}],
        "expected_hashes": ["hash"],
    }
    verify_continuity(original, original)
    for key in (
        "projects",
        "users",
        "profiles",
        "commits",
        "org_members",
        "members",
        "credentials",
    ):
        modified = copy.deepcopy(original)
        modified[key] = []
        with pytest.raises(ValueError):
            verify_continuity(original, modified)
    modified = copy.deepcopy(original)
    modified["members"][0]["role"] = "viewer"
    with pytest.raises(ValueError, match="downgraded"):
        verify_continuity(original, modified)
