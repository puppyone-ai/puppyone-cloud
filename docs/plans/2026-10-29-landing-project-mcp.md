# Landing tools Project MCP Implementation Plan

**Goal:** Restore PDF imports and bring Markdown, Excel/CSV/TSV and OpenAPI imports to the same verified native Project-root, read-only MCP path, preserving Unicode filenames.

**Architecture:** Each import creates a new private Project in the user's explicitly selected organization. Its converted document stays in the existing tool folder, while a newly issued MCP bearer targets the entire Project read-only. Existing Scope credentials remain unavailable; they are never silently converted to Project-root credentials. The user approved this model and a PR into `qubits`, without a production merge or deployment.

**Tech Stack:** Python 3.12, FastAPI, Supabase/S3, native Version Engine, pytest.

---

## Approved behavior and limits

- Original Unicode filenames remain in preview metadata, signed tickets and imported Git paths. Only temporary S3 keys use bounded ASCII names.
- New imports use `McpEndpointService.create_endpoint(path=None, accesses=[McpAccessItem(path="", readonly=True)])`; they do not create a Scope.
- The existing deferred-publication initializer explicitly changes the new Project's default org visibility to private, through an initialization-only control-plane operation under its active lease. Operation ID, actor, creator, organization, Project identity, initializing lifecycle and the returned private visibility must match before content is written or credentials are issued; failure aborts publication.
- The MCP can read all files in its dedicated Project, including files added there later. Other Projects remain outside its grant.
- New durable result metadata records the Project-root target. Replays recheck current membership, credential, exact Project/endpoint target and read-only policy; legacy Scope results fail closed.
- Upload, organization selection, publication journaling and uncertain-result protection retain their existing contracts. No schema, migration, OAuth or production configuration changes are included.

## Task 1: Safe staging paths

Files: `backend/src/platform/landing/service.py`, `backend/tests/platform/test_landing_preview_filenames.py`.

1. Reproduce Unicode `InvalidKey` with an ASCII-only storage boundary test.
2. Run the test against the old source and observe failures.
3. Keep original names in the ticket, but write `source/input<safe extension>` and `parsed/content.md` under the unique upload prefix.
4. Run the eight regression cases plus the existing claim tests.

## Task 2: Project-root claim and replay

Files: `backend/src/platform/landing/service.py`, `backend/tests/platform/test_landing_claim_publication.py`, `backend/src/platform/landing/__init__.py`.

1. Make the claim test reject any Scope creation and require a root, read-only MCP. Exercise the real endpoint-service call signature.
2. Add replay cases for a valid root endpoint, unsupported legacy Scope metadata, wrong Project/Scope/path, rotated credential and writable policy.
3. Observe the tests fail before changing the claim implementation.
4. Remove Scope creation, retain the dedicated Project's normal write/publication funnel and record the new target in durable metadata.
5. Reject incompatible replay metadata or a changed target/policy before returning credentials.
6. Update module documentation to state the Project-root read-only contract.

## Task 3: Hidden Project privacy

Files: `backend/src/platform/project/control_plane.py`, `backend/tests/platform/test_landing_private_initialization.py`.

1. Require the owned active Project initialization lease and its exact operation/actor proof before updating metadata.
2. Persist privacy with predicates for Project, organization, creator and `initializing` lifecycle. Ordinary metadata APIs remain ready-only.
3. Validate the returned row and abort the import before document/credential creation on any mismatch.
4. Exercise the actual chained query filters with positive and cross-owner/organization/lifecycle tests, plus missing/incorrect lease proof.

## Task 4: Runtime and delivery verification

1. Run landing regression, MCP resolver/runtime and read-only filesystem tests. Verify list/read are available, mutations are denied, wrong credentials/Projects fail and revoked creator access fails closed.
2. Run `uv run pytest -q -m 'not e2e and not integration and not network'` with synthetic test settings. Report any pre-existing/environment failures separately; never use production credentials in local acceptance.
3. Review the complete diff independently and run `git diff --check`.
4. Commit only these fixes and their tests/docs to a dedicated branch based on current `qubits`.
5. Push the repair branch, create and attach a PR with base `qubits`, and report CI and limitations. Do not merge or deploy production.

## Production evidence motivating this repair

- A synthetic PDF uploaded under an ASCII key succeeds; the same public bytes under a Chinese key return S3 `InvalidKey`.
- The live claim raises `ScopeService.create() got an unexpected keyword argument 'mode'`.
- Renaming `mode` is insufficient: native non-root Scopes are currently unavailable and scoped MCP resolution returns 501.
- Production operation-journal SELECT was denied; no user resources were retried or removed during diagnosis, and cleanup success has not been asserted.

## Initial PDF milestone verification

- Focused landing, initialization query, Project control-plane/orchestration and MCP runtime suites: 77 passed.
- Full offline backend suite: 3,688 passed, 963 skipped, 456 deselected; three worker tests failed because localhost Redis is unavailable. The same three failures were reproduced on the original unmodified landing service before the repair.
- The local synthetic PDF flow exercises actual PDF parsing, MCP policy serialization, principal resolution, list/read handlers, mutation denial, invalid credentials and revoked-owner access. Persistence and publication are in memory; this is not production end-to-end acceptance.
- Independent code review found no remaining blocking issues after replacing the ready-only metadata update with the initialization-specific control-plane operation.
- No production code deployment or merge is part of this delivery.

## Markdown, Excel and OpenAPI parity extension

The same approved private Project/root read-only model now has real-parser → preview → claim → MCP list/read coverage for PDF, Markdown, MDX, XLSX, CSV, TSV and OpenAPI JSON/YAML. The persistence/publication boundary remains in memory; production end-to-end acceptance remains outstanding.

Converter regressions reproduced before fixing:

- TSV cells containing many commas were incorrectly split using the CSV delimiter. The ASCII staging suffix now selects the advertised delimiter.
- CSV BOMs and pipe characters in headers corrupted table output; UTF-8 BOM handling and shared cell escaping now preserve it. CSV files beginning with PK are no longer mistaken for ZIP workbooks.
- Multiline CSV/XLSX cells broke physical Markdown table rows. CRLF/CR/LF now become `<br>` within both headers and data.
- Non-UTF-8/blank Markdown, invalid XLSX, legacy binary XLS and malformed OpenAPI were accepted as misleading context or failed opaquely. Input validation now rejects these before claim, and the preview route distinguishes input errors from service failures.
- XLSX resources are closed after parsing; OpenAPI and spreadsheet conversions use the blocking execution boundary. Registry metadata matches XLSX/CSV/TSV support.

Final verification: 100 focused backend tests passed. Full offline backend suite: 3,711 passed, 963 skipped, 456 deselected; the same three pre-existing worker failures remain because local Redis is unavailable. Independent code review found no remaining blocking issues after the multiline-cell fix.

Companion Web commit: `puppyone-ai/puppyone-web` `herbbb` / `083415bf`. It includes published English/Japanese/Chinese connection guidance, explicit whole-Project read scope, corrected Excel idle input guidance, shared flow regression tests, six public acceptance files and `docs/testing/mcp-tools-cloud-acceptance.md`. Web product 39, i18n behavior 43, structured-data contract 30 tests and eight-locale key validation passed. Actual browser input states and all six configured English production OAuth return URLs were checked. No production source upload, Project creation, code merge or deployment was performed.
