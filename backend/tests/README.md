# Backend tests

## Layout

```
tests/<area>/<domain>/<kind>/test_<capability>.py
```

- **area/domain** mirrors `src/`: `platform/hosts` tests `src/platform/hosts`. A domain's tests
  stay together, so its owner finds them and a retired module takes its tests with it.
- **kind** is one of these directories; `tests/conftest.py` gives every test the marker of its
  directory, so a test cannot claim to be a unit test while it starts a database:

| Kind | Depends on | Runs |
| --- | --- | --- |
| `unit/` | fakes only, no service of any kind | every pull request |
| `contract/` | a published contract (protocol, OpenAPI, database) checked against the code | every pull request |
| `native/` | real services started on this machine: PostgreSQL 17, PostgREST, S3, Redis | pull requests that touch the domain |
| `live/` | real third-party services | nightly or by hand |
| `acceptance/` | a deployed environment | by hand or after a deployment |

- **capability** names the file; no domain prefix inside a domain directory
  (`platform/hosts/unit/test_gateway.py`, not `test_host_gateway.py`).
- Use cases are not directories: each domain's `README.md` lists them and the test that proves each.
- Cross-domain rules (import layers, the public OpenAPI contract, retired routes, this layout) live in
  `architecture/`.

Select by kind: `uv run pytest -m "unit or contract"`, `uv run pytest -m native tests/platform/hosts`.

## Rules

1. Test a behaviour at the lowest kind that can prove it; a higher kind only adds what a lower one
   cannot (real SQL, another process, the deployment).
2. Tests never import another test module. Fixtures go in the kind's `conftest.py`; helpers in a
   `support.py` next to it; helpers several domains need go in `tests/support/`.
3. `architecture/test_test_layout.py` enforces the layout. Files from before it are frozen in
   `architecture/unstructured_tests.txt` and `architecture/test_module_imports.txt`; those lists
   only shrink: move a file into `<domain>/<kind>/`, then delete its line.
