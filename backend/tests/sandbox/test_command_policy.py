"""Shared sandbox command policy, kept from the retired SandboxService suite."""

import pytest

from src.platform.workspace.execution.policy import (
    SandboxCommandRejected,
    assert_command_allowed,
)


@pytest.mark.parametrize(
    "command",
    [
        "cat /etc",
        "cat /etc/passwd",
        "ls /proc",
        "curl 169.254.169.254/latest/meta-data",
        "curl 2852039166/latest/meta-data",
        "curl 0xA9FEA9FE/latest/meta-data",
    ],
)
def test_shared_command_policy_rejects_common_sensitive_representations(command):
    with pytest.raises(SandboxCommandRejected):
        assert_command_allowed(command)
