"""The gateway and router speak exactly the published host file protocol v1."""

import json
import os
from pathlib import Path

import pytest

from src.platform.hosts import gateway

CONTRACT_PATH = Path(__file__).resolve().parents[3] / "contracts/host-file-protocol-v1.json"
CONTRACT = json.loads(CONTRACT_PATH.read_text())


def test_gateway_constants_match_the_contract():
    limits = CONTRACT["limits"]
    assert limits["max_pending_requests_per_device"] == gateway.MAX_PENDING
    assert limits["max_frame_bytes"] == gateway.MAX_RESPONSE_BYTES
    assert limits["request_timeout_seconds"] == gateway.REQUEST_TIMEOUT
    assert limits["lease_seconds"] == gateway.LEASE_SECONDS
    assert limits["idle_close_seconds"] == gateway.HEARTBEAT_TIMEOUT
    assert limits["subscriber_queue_events"] == gateway.SUBSCRIBER_QUEUE
    assert set(CONTRACT["operations"]) - {"watch"} == gateway.FILE_ACTIONS
    assert {name for name, operation in CONTRACT["operations"].items()
                                 if operation["capability"] == "folder.write"} == gateway.MUTATIONS
    close_codes = CONTRACT["device_frames"]["close_codes"]
    assert {gateway.CLOSE_REPLACED, gateway.CLOSE_INVALID, gateway.CLOSE_REVOKED,
            gateway.CLOSE_IDLE} == {int(code) for code in close_codes}


def test_device_error_statuses_match_the_contract():
    for code, status in gateway.DEVICE_ERRORS.items():
        assert CONTRACT["errors"][code] == status, code


@pytest.mark.skipif(not os.environ.get("PUPPYONE_DESKTOP_ROOT"),
                    reason="cross-repository pin needs PUPPYONE_DESKTOP_ROOT")
def test_desktop_pins_the_same_contract_bytes():
    pinned = Path(os.environ["PUPPYONE_DESKTOP_ROOT"]) / "tests/fixtures/cloud/contracts/host-file-protocol-v1.json"
    assert pinned.read_bytes() == CONTRACT_PATH.read_bytes()
