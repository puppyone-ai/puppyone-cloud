# Access: how Agents and clients reach a Project

`src/platform/access`. Most of its tests predate the layout and are still listed in
`architecture/unstructured_tests.txt`; new ones live here.

| Use case | Proven by |
| --- | --- |
| A linked Agent runs commands over HTTPS with the helper from its page, through an HTTP proxy, with the command's own exit status; only a fresh request signed by the approved key runs; an ended session says why; a client that leaves stops its command, and one that leaves while its machine is prepared runs nothing | `contract/test_https_commands.py` |
| What proves an HTTPS command: an SSHSIG by the approved key over the command and its moment; armor, hashes, namespace, version, key type, malformed input and the five-minute clock | `unit/test_https_signature.py` |
| The HTTPS command service refuses before anything runs (busy, unreadable, unsigned, not approved, unavailable, replayed) and ends every command with its status: done, still preparing, revoked, a fault with no internals, past its time; replicas share what they have seen | `unit/test_https_command_service.py` |
| The page carries the helper exactly as the server checks it, HTTPS before SSH; polling an allowed connection returns both; a deployment without a public address records nothing | `unit/test_agent_instructions.py` |
| The helper sends a command byte for byte (quotes, non-ASCII, heredocs), streams a large output whole, says what is missing (arguments, install, key), fails on a refusal; responses are plain, streamed and never cached; nothing runs while access lifecycle is off | `contract/test_https_commands.py` |
