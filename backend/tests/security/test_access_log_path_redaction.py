from starlette.requests import Request

from src.utils.middleware import _sanitize_path_for_access_log


def _request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
            "server": ("api.example", 443),
            "client": ("127.0.0.1", 1234),
        }
    )


def test_legacy_git_secret_is_redacted_from_access_log_context():
    secret = "git_super_secret_value"

    assert _sanitize_path_for_access_log(
        _request(f"/git/ap/{secret}.git/info/refs")
    ) == "/git/ap/<redacted>.git/info/refs"
    assert secret not in _sanitize_path_for_access_log(
        _request(f"/git/ap/{secret}.git/git-receive-pack")
    )


def test_canonical_git_locator_is_safe_to_log_unchanged():
    path = "/git/project-1/scopes/scope-docs.git/info/refs"

    assert _sanitize_path_for_access_log(_request(path)) == path
