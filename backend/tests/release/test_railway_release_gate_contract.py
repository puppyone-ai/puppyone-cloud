from pathlib import Path

from scripts.check_railway_release_gate import REQUIRED_ROLES, validate_contract

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_railway_release_gate_repository_contract_is_consistent() -> None:
    assert validate_contract(REPO_ROOT) == []


def test_railway_release_gate_covers_every_backend_service_role() -> None:
    dispatcher = (REPO_ROOT / "backend/scripts/start-service.sh").read_text(encoding="utf-8")
    assert {"api", "agent_worker", "upload_worker", "import_worker", "synchronize_worker", "mcp_server"} == REQUIRED_ROLES
    assert all(f"  {role})\n    exec " in dispatcher for role in REQUIRED_ROLES)


def test_unknown_service_role_fails_closed() -> None:
    import subprocess

    result = subprocess.run(
        ["sh", str(REPO_ROOT / "backend/scripts/start-service.sh")],
        env={"SERVICE_ROLE": "file_worker", "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 64
    assert "Unsupported SERVICE_ROLE" in result.stderr
