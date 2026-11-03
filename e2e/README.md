# End-to-end tests

Each suite here needs several parts of the product running together. A test that
covers only the backend, frontend or database belongs in `backend/tests/`,
`frontend/tests/` or `supabase/tests/`.

| Suite | What it proves | Runs in CI on |
|---|---|---|
| [`desktop-login/`](desktop-login/README.md) | Desktop signs in with email through the Cloud `/login` page | Cloud PRs and pushes to `qubits` or `main`, and Desktop PRs |
| `install/` | A self-hosted install signs in, handles files and survives a restart | PRs and pushes to `main` |
| `organization/` | Organization roles and invitations hold in a real browser | PRs and pushes to `main` |

## Setup

`install/` and `organization/` share this directory's dependencies:

```bash
cd e2e
npm ci
npx playwright install chromium
```

`desktop-login/` has its own dependencies and setup in
[its README](desktop-login/README.md).

## Run a suite

### `install/`

Needs Docker. Run from the repository root:

```bash
python3 scripts/test_self_hosted_install.py --variant default --artifacts /tmp/install-results
```

The variants are `default`, `custom`, `upgrade`, `legacy` and `release`.
`release` also needs `--upgrade-base <commit>`.

### `organization/`

Needs PostgreSQL 17, Docker and a production frontend build. The exact
preparation is in
[`organization-workspace-acceptance.yml`](../.github/workflows/organization-workspace-acceptance.yml).
Then run from `backend/`:

```bash
PUPPYONE_TEST_ORG_BROWSER=1 uv run pytest tests/platform/organization/integration/test_browser_acceptance.py
```
