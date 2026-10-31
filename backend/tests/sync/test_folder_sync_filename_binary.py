"""Tests for version filename validation with binary file extensions."""

from src.platform.access.validation import validate_version_filename


def test_validate_filename_accepts_binary_extension():
    assert validate_version_filename("report.pdf") is None
    assert validate_version_filename("data.xlsx") is None
    assert validate_version_filename("sub/folder/report.pdf") is None
    assert validate_version_filename("sub/.gitkeep") is None


def test_validate_filename_rejects_traversal():
    assert validate_version_filename("../report.pdf") is not None
    assert validate_version_filename("a/../b.pdf") is not None
