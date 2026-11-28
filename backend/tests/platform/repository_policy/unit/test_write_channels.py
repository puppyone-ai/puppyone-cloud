"""How a product write is counted: facts decide initialization, never labels."""

from __future__ import annotations

import pytest

from src.platform.repository_policy import WriteChannel, channel_from_source, product_write_channel


@pytest.mark.parametrize("label", ["initialization", "workspace"])
def test_derived_channels_cannot_be_named_by_a_caller(label):
    with pytest.raises(ValueError):
        channel_from_source(label)
    with pytest.raises(ValueError):
        product_write_channel(label, initializing=True, repository_empty=True)


@pytest.mark.parametrize("channel", [WriteChannel.INITIALIZATION, WriteChannel.WORKSPACE])
def test_a_product_write_is_never_a_workspace_or_initialization_by_request(channel):
    with pytest.raises(ValueError):
        product_write_channel(channel, initializing=False, repository_empty=False)


def test_filling_a_project_is_initialization_whatever_channel_carries_it():
    for source in ("papi", "upload", "import", "mcp", "access_cli"):
        assert (
            product_write_channel(source, initializing=True, repository_empty=False)
            is WriteChannel.INITIALIZATION
        )
        assert (
            product_write_channel(source, initializing=False, repository_empty=True)
            is WriteChannel.INITIALIZATION
        )


def test_a_project_with_content_counts_the_channel_it_came_through():
    assert (
        product_write_channel("papi", initializing=False, repository_empty=False)
        is WriteChannel.CONTENT_API
    )
    assert (
        product_write_channel("upload", initializing=False, repository_empty=False)
        is WriteChannel.UPLOAD
    )


def test_a_restore_is_always_a_restore():
    assert (
        product_write_channel(WriteChannel.RESTORE, initializing=True, repository_empty=True)
        is WriteChannel.RESTORE
    )
