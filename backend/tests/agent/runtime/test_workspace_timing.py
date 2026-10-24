import json
import os
import subprocess
import sys
from pathlib import Path


def test_workspace_stages_survive_console_interception_without_private_exception(tmp_path):
    script = '''
from loguru import logger
from src.utils.logging_setup import setup_logging
from src.platform.sandbox.timing import workspace_stage, workspace_event
setup_logging()
try:
    with workspace_stage("connection.open", reason="missing"):
        raise ConnectionError("secret-token-do-not-log")
except ConnectionError:
    pass
workspace_event("connection.borrow", reused=True)
logger.complete()
'''
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[3],
        env={**os.environ, "LOG_JSON_CONSOLE": "1", "LOG_DIR": str(tmp_path)},
        capture_output=True, text=True, timeout=30, check=True,
    )
    records = [json.loads(line)["record"] for line in result.stderr.splitlines()
               if line.startswith('{"text":')]
    reports = [record["extra"]["workspace_performance"] for record in records
               if record["message"] == "workspace_stage"]
    assert len(reports) == 2
    assert reports[0]["stage"] == "connection.open"
    assert reports[0]["outcome"] == "failed" and reports[0]["elapsed_ms"] >= 0
    assert reports[1] == {"stage": "connection.borrow", "reused": True}
    assert "secret-token-do-not-log" not in result.stderr
