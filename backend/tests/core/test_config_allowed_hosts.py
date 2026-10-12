from src.config import Settings


def test_allowed_hosts_accepts_single_string() -> None:
    settings = Settings(
        _env_file=None,
        APP_ENV="test",
        DEBUG=False,
        SKIP_AUTH=False,
        ACCESS_CREDENTIAL_HASH_SECRET="allowed-hosts-test-secret-not-for-production",
        JWT_SECRET="allowed-hosts-jwt-test-secret-not-for-production",
        MCP_TOKEN_SECRET="allowed-hosts-mcp-test-secret-not-for-production",
        ALLOWED_HOSTS="https://frontend.example.com",
    )

    assert settings.ALLOWED_HOSTS == ["https://frontend.example.com"]


def test_allowed_hosts_accepts_comma_separated_string() -> None:
    settings = Settings(
        _env_file=None,
        APP_ENV="test",
        DEBUG=False,
        SKIP_AUTH=False,
        ACCESS_CREDENTIAL_HASH_SECRET="allowed-hosts-test-secret-not-for-production",
        JWT_SECRET="allowed-hosts-jwt-test-secret-not-for-production",
        MCP_TOKEN_SECRET="allowed-hosts-mcp-test-secret-not-for-production",
        ALLOWED_HOSTS="https://a.example.com, https://b.example.com/",
    )

    assert settings.ALLOWED_HOSTS == [
        "https://a.example.com",
        "https://b.example.com",
    ]


def test_allowed_hosts_default_for_development_without_debug() -> None:
    settings = Settings(
        _env_file=None,
        APP_ENV="development",
        DEBUG=False,
        ALLOWED_HOSTS=None,
    )

    # Next.js auto-rolls forward 3000 → 3001 → … → 3004 when the previous
    # port is occupied. Desktop uses Vite on 5173 and can roll forward too.
    # Update this list in lockstep with src/config.py:Settings.ALLOWED_HOSTS
    # dev branch.
    assert settings.ALLOWED_HOSTS == [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
        "http://localhost:3002",
        "http://127.0.0.1:3002",
        "http://localhost:3003",
        "http://127.0.0.1:3003",
        "http://localhost:3004",
        "http://127.0.0.1:3004",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "http://localhost:5175",
        "http://127.0.0.1:5175",
        "http://localhost:5176",
        "http://127.0.0.1:5176",
        "http://localhost:5177",
        "http://127.0.0.1:5177",
    ]


def test_authentication_configuration_has_no_redis_dependency():
    settings = Settings(_env_file=None, APP_ENV="test", ETL_REDIS_URL="")
    assert not any(name.startswith(("AUTH_SECURITY_REDIS", "RATELIMIT_REDIS", "DESKTOP_AUTH_")) for name in type(settings).model_fields)
