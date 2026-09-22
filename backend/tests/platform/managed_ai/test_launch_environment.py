"""Exercise the real application import, not only an isolated Settings object."""

import os
import subprocess
import sys
from pathlib import Path

from src.config import Settings


def test_constructor_then_environment_then_dotenv_precedence(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("FRONTEND_URL=http://localhost:3000\n")
    monkeypatch.setenv("FRONTEND_URL", "http://127.0.0.1:43123")
    assert Settings(_env_file=env_file).FRONTEND_URL == "http://127.0.0.1:43123"
    assert (
        Settings(_env_file=env_file, FRONTEND_URL="http://127.0.0.1:43124").FRONTEND_URL
        == "http://127.0.0.1:43124"
    )
    monkeypatch.delenv("FRONTEND_URL")
    assert Settings(_env_file=env_file).FRONTEND_URL == "http://localhost:3000"


def test_explicit_local_service_urls_survive_application_bootstrap():
    backend = Path(__file__).resolve().parents[3]
    code = """
import src.main
from src.config import settings
assert settings.FRONTEND_URL == 'http://127.0.0.1:43123'
assert settings.PUPPYPAY_BASE_URL == 'http://127.0.0.1:43124'
"""
    subprocess.run(
        [sys.executable, "-c", code],
        cwd=backend,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
        env={
            **os.environ,
            "FRONTEND_URL": "http://127.0.0.1:43123",
            "PUPPYPAY_BASE_URL": "http://127.0.0.1:43124",
        },
    )
