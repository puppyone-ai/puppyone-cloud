"""Bounded dependency diagnostics; never serialize SDK errors or credentials."""

import os
import re
from urllib.request import getproxies


def network_environment() -> dict[str, bool]:
    proxies = getproxies()
    return {
        "environment_proxy_configured": any(proxies.get(key) for key in ("http", "https", "all")),
        "environment_bypasses_all_proxies": proxies.get("no", "").strip() == "*",
        "supabase_trust_env_proxy": os.getenv("SUPABASE_TRUST_ENV_PROXY", "").strip().lower()
        in {"1", "true", "yes", "y", "on"},
    }


def dependency_failure(error: Exception, dependency: str) -> dict:
    """Stable labels identify the failing boundary without exception text/URLs."""
    report = {
        "dependency": dependency,
        "error_type": type(error).__name__,
        "network_environment": network_environment(),
    }
    code = getattr(error, "code", None)
    if isinstance(code, str) and re.fullmatch(r"[A-Z0-9]{5,10}|workbench_[a-z_]{1,60}", code):
        report["error_code"] = code
    status = getattr(getattr(error, "response", None), "status_code", None)
    if isinstance(status, int) and 100 <= status <= 599:
        report["status_code"] = status
    return report
