"""Project ownership of a key decides whether writing it needs a lease."""

import pytest

from src.platform.project.storage_keys import project_id_from_owned_s3_key


@pytest.mark.parametrize(
    "key",
    [
        "version/project-1/objects/aa/blob",
        "projects/project-1/uploads/staged",
        "shadow-snapshots/project-1/snapshot",
        "users/user-1/etl_artifacts/project-1/artifact",
        "users/user-1/processed/project-1/file.md",
        "users/user-1/raw/project-1/file.pdf",
    ],
)
def test_owned_keys_name_their_project(key):
    assert project_id_from_owned_s3_key(key) == "project-1"


@pytest.mark.parametrize(
    "key",
    [
        "public/logo.png",
        "users/user-1/avatar.png",
        "users/user-1/other/project-1/file",
        "version/project-1",
        "projects//file",
        "projects/../file",
        "nested/version/project-1/file",
    ],
)
def test_other_keys_belong_to_no_project(key):
    assert project_id_from_owned_s3_key(key) is None
