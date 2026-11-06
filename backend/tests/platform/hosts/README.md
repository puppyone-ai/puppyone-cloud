# Hosts: the owner's computers and their shared folders

`src/platform/hosts`. A computer's folders are reached only inside SSH that ends on that computer;
the Cloud keeps the inventory, issues five-minute certificates and relays tunnel bytes it cannot read.
The Desktop's host service and SSH client are tested in the Desktop repository
(`tests/unit/hosts`, `tests/integration/hosts`), and both together against a deployment in its
`tests/support/hosts/two-computer-acceptance` (17 checks).

| Use case | Proven by |
| --- | --- |
| One computer opens another's folder over SSH, end to end; the gateway sees no content; a wrong host key or another computer's certificate is refused | `native/test_end_to_end_ssh.py` |
| Devices and tunnels reach the single gateway through the API's own address; revocation closes the device | `native/test_forwarding.py` |
| Certificates are short-lived and only for the owner's own device; tunnels relay bytes only for the owner and record only that they happened; a client leaving never closes the device socket | `native/test_tunnels.py` |
| Registration, the catalog, protocol versions, epoch replacement, narrowing a folder, audit shape, revocation, a host online with no one signed in, service-only RPCs, 30-day connection records | `native/test_devices.py` |
| The device-host migrations upgrade an existing database without touching its data | `native/test_migration_upgrade.py` |
| What the certificate authority signs (owner, device, five minutes, only a pty) and refuses | `unit/test_ssh_authority.py` |
| Tunnel frames: order, end, refusal codes, malformed or oversized data, a slow client, the device going away | `unit/test_tunnel.py` |
| The gateway carries only tunnels and heartbeats; a device sending relay frames is closed | `unit/test_gateway.py` |
| Device sockets and tunnels in one process; owner revocation ends access | `unit/test_transport.py` |
| Device proofs, challenges and clock skew | `unit/test_connection_auth.py` |
| Owner admission and validation of the inventory API; only a person's own sign-in | `unit/test_inventory.py` |
| API workers reach the gateway's internals only with the shared secret | `unit/test_routing.py` |
| The host directory lists computers and hosted workspaces; a computer's files answer 410 | `unit/test_directory.py` |
| The gateway and tunnels match the published protocol; the relay and execution frames are retired | `contract/test_file_protocol.py` |
