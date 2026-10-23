import json
from types import SimpleNamespace

import httpx

from src.infra import dependency_diagnostics as diagnostics


def test_diagnostics_report_proxy_bypass_without_leaking_credentials(monkeypatch):
    secret = "secret-user:secret-password"
    monkeypatch.setattr(
        diagnostics, "getproxies", lambda: {"https": f"http://{secret}@proxy:1234", "no": "*"}
    )
    monkeypatch.setenv("SUPABASE_TRUST_ENV_PROXY", "false")
    error = httpx.ConnectError(f"https://{secret}@database/private?token=secret")
    report = diagnostics.dependency_failure(error, "project_authorization_database")
    assert report["network_environment"] == {
        "environment_proxy_configured": True,
        "environment_bypasses_all_proxies": True,
        "supabase_trust_env_proxy": False,
    }
    assert report["error_type"] == "ConnectError"
    assert "secret" not in json.dumps(report)
    assert "proxy:1234" not in json.dumps(report)


def test_diagnostics_keep_only_bounded_error_codes(monkeypatch):
    monkeypatch.setattr(diagnostics, "getproxies", lambda: {})
    error = RuntimeError("customer content")
    error.code = "PGRST202"
    error.response = SimpleNamespace(status_code=404)
    assert diagnostics.dependency_failure(error, "database")["error_code"] == "PGRST202"
    error.code = "secret-token=abc"
    assert "error_code" not in diagnostics.dependency_failure(error, "database")


def test_standard_logging_bridge_preserves_safe_failure_report():
    import logging

    from loguru import logger

    from src.utils.logging_setup import InterceptHandler

    records = []
    sink = logger.add(lambda message: records.append(message.record), level="WARNING")
    try:
        record = logging.LogRecord(
            "test", logging.WARNING, __file__, 1, "dependency failed", (), None
        )
        record.dependency_failure = {"dependency": "database", "error_type": "ConnectError"}
        record.secret_token = "do-not-log"
        InterceptHandler().emit(record)
    finally:
        logger.remove(sink)
    assert records[-1]["extra"]["dependency_failure"]["error_type"] == "ConnectError"
    assert "secret_token" not in records[-1]["extra"]
