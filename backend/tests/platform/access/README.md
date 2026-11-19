# Access: how Agents and clients reach a Project

`src/platform/access`. Most of its tests predate the layout and are still listed in
`architecture/unstructured_tests.txt`; new ones live here.

| Use case | Proven by |
| --- | --- |
| A linked Agent runs commands over HTTPS with the helper from its page, through an HTTP proxy, with the command's own exit status; only a fresh request signed by the approved key runs; an ended session says why; a client that leaves stops its command, and one that leaves while its machine is prepared runs nothing | `contract/test_https_commands.py` |
