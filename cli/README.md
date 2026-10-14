# The PuppyOne CLI moved

The CLI is a client and now lives in its own repository:

    https://github.com/puppyone-ai/puppyone-cli

It is published to npm as `puppyone`. Do not add CLI sources back here; a
vendored copy under `cli/` drifted from the published package for about three
months, and `backend/tests/platform/test_cli_not_vendored.py` now fails if one
reappears.

This repository still drives the real CLI in its contract tests, which prove the
server keeps its side of the contract. Those tests resolve the binary through
`backend/tests/support/cli_binary.py`, in order:

1. `$PUPPYONE_CLI_BIN` — explicit path to `bin/puppyone.js`
2. `node_modules/puppyone/bin/puppyone.js` — installed npm package
3. `../puppyone-cli/bin/puppyone.js` — sibling checkout

When none resolve the tests skip, so a backend-only checkout stays green. CI
must make the CLI available and set `PUPPYONE_REQUIRE_CLI=1`, which turns that
skip into a failure.
