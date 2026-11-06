"""The gateway and tunnels speak exactly the published host file protocol v1 (inside SSH)."""

import json
import os
from pathlib import Path

import pytest

from src.platform.hosts import gateway, ssh_authority, tunnel

CONTRACT_PATH = Path(__file__).resolve().parents[3] / "contracts/host-file-protocol-v1.json"
CONTRACT = json.loads(CONTRACT_PATH.read_text())


def test_gateway_constants_match_the_contract():
    limits = CONTRACT["limits"]
    assert limits["lease_seconds"] == gateway.LEASE_SECONDS
    assert limits["idle_close_seconds"] == gateway.HEARTBEAT_TIMEOUT
    assert limits["max_tunnels_per_device"] == tunnel.MAX_TUNNELS_PER_DEVICE
    assert limits["max_tunnel_chunk_bytes"] == tunnel.MAX_CHUNK
    refusals = CONTRACT["device_frames"]["tunnel_refused"]["error"].split(" | ")
    assert set(refusals) == tunnel.TUNNEL_REFUSALS
    close_codes = CONTRACT["device_frames"]["close_codes"]
    assert {gateway.CLOSE_REPLACED, gateway.CLOSE_INVALID, gateway.CLOSE_REVOKED,
            gateway.CLOSE_UNSUPPORTED, gateway.CLOSE_IDLE} == {int(code) for code in close_codes}
    handshake = CONTRACT["device_frames"]["proof"]
    assert set(handshake) == gateway.PROOF_FIELDS
    assert set(CONTRACT["protocol"]["supported"]) == gateway.SUPPORTED_PROTOCOLS
    assert CONTRACT["version"] in gateway.SUPPORTED_PROTOCOLS


def test_handshake_rejects_malformed_fields_and_defers_unknown_versions():
    proof = {"owner": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "challenge_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
             "epoch": "dddddddd-dddd-4ddd-8ddd-dddddddddddd", "nonce": "n", "signature": "s",
             "protocol": 1, "boot_id": "99999999-9999-4999-8999-999999999999", "app_version": "1.4.0-beta.2"}
    assert gateway.parse_proof(proof)["app_version"] == "1.4.0-beta.2"
    for protocol in (2, 0, True, "1", None):
        assert gateway.parse_proof({**proof, "protocol": protocol}) is None
    assert gateway.parse_proof({key: value for key, value in proof.items() if key != "protocol"}) is None
    for broken in ({"app_version": "1.0 beta"}, {"app_version": ""}, {"boot_id": "not-a-uuid"},
                   {"extra": "x"}, {"nonce": 5}):
        with pytest.raises(ValueError):
            gateway.parse_proof({**proof, **broken})
    with pytest.raises(ValueError):
        gateway.parse_proof([proof])


def test_the_plaintext_relay_and_execution_frames_are_retired():
    """Only tunnels cross the device socket; the Cloud never carries file content or commands."""
    frames = CONTRACT["device_frames"]
    assert not {"file", "reply", "watch", "unwatch", "event"} & set(frames)
    assert "execution" not in CONTRACT and "execution" in CONTRACT["transport"]
    for retired in ("FILE_ACTIONS", "MUTATIONS", "DEVICE_ERRORS", "MAX_PENDING"):
        assert not hasattr(gateway, retired), retired
    from src.platform.hosts import router
    paths = {route.path for route in router.router.routes}
    assert not {path for path in paths if path.endswith(("/files", "/file", "/events", "/git", "/exec"))}
    assert "/api/v1/hosts/folders/{folder_id}/tunnel" in paths


def test_certificates_match_the_transport_contract():
    assert ssh_authority.CERTIFICATE_SECONDS == 300
    assert "principal = device id" in CONTRACT["transport"]["ssh"]["certificate"]


@pytest.mark.skipif(not os.environ.get("PUPPYONE_DESKTOP_ROOT"),
                    reason="cross-repository pin needs PUPPYONE_DESKTOP_ROOT")
def test_desktop_pins_the_same_contract_bytes():
    pinned = Path(os.environ["PUPPYONE_DESKTOP_ROOT"]) / "tests/fixtures/cloud/contracts/host-file-protocol-v1.json"
    assert pinned.read_bytes() == CONTRACT_PATH.read_bytes()
