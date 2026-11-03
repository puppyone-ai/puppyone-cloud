"""Tests for version filename validation used by FS CLI writes."""

from src.file_operations.validation import validate_version_filename


def test_validate_filename_rejects_traversal():
    assert validate_version_filename("../x.md") is not None
    assert validate_version_filename("a/../b.md") is not None


def test_validate_filename_rejects_double_slash():
    assert validate_version_filename("a//b.md") is not None


def test_validate_filename_rejects_null_byte():
    assert validate_version_filename("a/\x00b.md") is not None


def test_validate_filename_rejects_dot_segment():
    assert validate_version_filename("./x.md") is not None


def test_validate_filename_accepts_normal_nested_path():
    assert validate_version_filename("a/b/c.md") is None


def test_validate_filename_accepts_nested_dotfiles():
    assert validate_version_filename("a/.gitkeep") is None
    assert validate_version_filename(".well-known/openid-configuration") is None
