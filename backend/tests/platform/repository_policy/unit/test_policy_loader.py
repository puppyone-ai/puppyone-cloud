"""Unit tests for repository policy TOML loading, validation, and channel mapping."""

from __future__ import annotations

import textwrap

import pytest
from pydantic import ValidationError

from src.config import settings
from src.platform.repository_policy import (
    WriteChannel,
    channel_from_source,
    get_repository_policy,
    load_named_profile,
    load_policy_from_path,
    load_repository_policy,
    reset_policy_cache,
)


def _valid_toml(**overrides: str) -> str:
    base = {
        "writes_extra": "",
        "backup_extra": "",
        "root_extra": "",
        "git_push": "false",
        "target": '"project"',
        "on_pause": "true",
        "interval_seconds": "300",
    }
    base.update(overrides)
    return textwrap.dedent(
        f"""
        {base["root_extra"]}
        [writes]
        workspace = true
        initialization = true
        git_push = {base["git_push"]}
        content_api = false
        mcp = false
        cli = false
        upload = false
        import = false
        sync = false
        table = false
        restore = false
        head = false
        {base["writes_extra"]}

        [backup]
        target = {base["target"]}
        on_pause = {base["on_pause"]}
        interval_seconds = {base["interval_seconds"]}
        {base["backup_extra"]}
        """
    )


def test_vm_only_profile_matches_specification():
    policy = load_named_profile("vm-only")

    assert policy.allows_write(WriteChannel.WORKSPACE) is True
    assert policy.allows_write(WriteChannel.INITIALIZATION) is True
    for closed in (
        WriteChannel.GIT_PUSH,
        WriteChannel.CONTENT_API,
        WriteChannel.MCP,
        WriteChannel.CLI,
        WriteChannel.UPLOAD,
        WriteChannel.IMPORT,
        WriteChannel.SYNC,
        WriteChannel.TABLE,
        WriteChannel.RESTORE,
        WriteChannel.HEAD,
    ):
        assert policy.allows_write(closed) is False, f"{closed} should be closed in vm-only"

    assert policy.backup.target == "project"
    assert policy.backup.on_pause is True
    assert policy.backup.interval_seconds == 300


def test_open_profile_matches_specification():
    policy = load_named_profile("open")

    for channel in WriteChannel:
        assert policy.allows_write(channel) is True, f"{channel} should be open in open profile"

    assert policy.backup.target == "workspace"
    assert policy.backup.on_pause is False
    assert policy.backup.interval_seconds == 0


def test_settings_default_policy_is_vm_only(monkeypatch):
    from src.config import Settings

    monkeypatch.delenv("REPOSITORY_POLICY", raising=False)
    monkeypatch.delenv("REPOSITORY_POLICY_FILE", raising=False)
    fresh = Settings(_env_file=None)
    assert fresh.REPOSITORY_POLICY == "vm-only"
    assert fresh.REPOSITORY_POLICY_FILE == ""


def test_policy_file_override_takes_precedence(tmp_path, monkeypatch):
    custom = tmp_path / "custom.toml"
    custom.write_text(_valid_toml(git_push="true", interval_seconds="60"), encoding="utf-8")

    monkeypatch.setattr(settings, "REPOSITORY_POLICY", "vm-only")
    monkeypatch.setattr(settings, "REPOSITORY_POLICY_FILE", str(custom))
    reset_policy_cache()

    policy = load_repository_policy()
    assert policy.allows_write(WriteChannel.GIT_PUSH) is True
    assert policy.allows_write(WriteChannel.CONTENT_API) is False
    assert policy.backup.interval_seconds == 60
    assert get_repository_policy() is policy


@pytest.mark.parametrize(
    "kwargs",
    [
        {"root_extra": "unexpected = true"},
        {"writes_extra": "unknown_channel = true"},
        {"backup_extra": "extra_flag = 1"},
    ],
    ids=["unknown-root-key", "unknown-writes-key", "unknown-backup-key"],
)
def test_unknown_field_fails_validation(tmp_path, kwargs):
    path = tmp_path / "bad.toml"
    path.write_text(_valid_toml(**kwargs), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_policy_from_path(path)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"git_push": '"false"'},
        {"target": '"invalid"'},
        {"on_pause": "1"},
        {"interval_seconds": "-1"},
        {"interval_seconds": "true"},
        {"interval_seconds": "30.5"},
    ],
    ids=[
        "string-instead-of-bool",
        "invalid-backup-target",
        "int-instead-of-bool",
        "negative-interval",
        "bool-instead-of-int",
        "float-instead-of-int",
    ],
)
def test_invalid_value_fails_validation(tmp_path, kwargs):
    path = tmp_path / "invalid.toml"
    path.write_text(_valid_toml(**kwargs), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_policy_from_path(path)


def test_missing_field_fails_validation(tmp_path):
    path = tmp_path / "missing.toml"
    raw = _valid_toml().replace("head = false\n", "")
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(ValidationError):
        load_policy_from_path(path)


def test_missing_file_and_unknown_profile_fail_fast(tmp_path, monkeypatch):
    missing = tmp_path / "does-not-exist.toml"
    with pytest.raises(FileNotFoundError):
        load_policy_from_path(missing)

    monkeypatch.setattr(settings, "REPOSITORY_POLICY_FILE", str(missing))
    reset_policy_cache()
    with pytest.raises(FileNotFoundError):
        load_repository_policy()

    with pytest.raises(ValueError, match="Unknown REPOSITORY_POLICY"):
        load_named_profile("not-a-profile")


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("papi", WriteChannel.CONTENT_API),
        ("content_api", WriteChannel.CONTENT_API),
        ("mcp", WriteChannel.MCP),
        ("access_cli", WriteChannel.CLI),
        ("cli", WriteChannel.CLI),
        ("upload", WriteChannel.UPLOAD),
        ("import", WriteChannel.IMPORT),
        ("sync", WriteChannel.SYNC),
        ("table", WriteChannel.TABLE),
        ("restore", WriteChannel.RESTORE),
        ("git_push", WriteChannel.GIT_PUSH),
        ("head", WriteChannel.HEAD),
    ],
)
def test_channel_from_source_maps_known_channels(source, expected):
    assert channel_from_source(source) is expected


@pytest.mark.parametrize("bad", ["", "unknown", "api", "git", None])
def test_channel_from_source_fails_closed(bad):
    with pytest.raises(ValueError, match="Unknown repository write channel"):
        channel_from_source(bad)  # type: ignore[arg-type]
