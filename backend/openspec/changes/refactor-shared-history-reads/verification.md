# Verification

Verified 2026-10-23. Implementation: `433c15a9`, merged by PR 1410 as
`1c1cddad`; generation/ref-sequence cursor follow-up: `8f5765ac`.
Desktop implementation: `d2ddc4b9`.

## Database and local activation

Qubits database workflow 39967043901 succeeded. The retained receipt records
`state=deployed`, `environment=staging`, source
`1c1cddadf8918357ede1f3c95c0ed397e0e145d5`, and `pending_versions=[]`.
The additive paging function was exercised against the configured test database.
There is no data migration, table replacement or new persistent service.

Both code changes are integrated into their canonical local `qubits` worktrees.
The local supervisor restarted the API/worker on source changes and API health
returned `ready`. The Electron development watcher restarted the client after
main/preload integration. Existing unrelated working changes were preserved.
Database deployment does not establish remote application deployment. The
backend-only identity follow-up and Desktop remain local.

## Tests

- Hosting/history/architecture regression selection: 775 passed, 1 skipped.
  Eight loopback/process tests initially blocked by the sandbox passed when
  rerun with the needed local permissions.
- Database release and authorization boundaries: 274 passed, 20 skipped.
- Owned native PostgreSQL populated upgrade: 50,001 locations, 13 bounded
  pages, unchanged rows, plus actor/pin/generation/GC/SQL-role rejection cases.
- Final history/native-read selection: 67 passed, including generation and
  ref-ABA cursor rejection in SHA-1/SHA-256 repositories. Changed-file Ruff passed.
- Desktop broader relevant tests: 189 passed. Later ownership/invalidation
  selection: 39 passed. Integrated focused tests: 31 passed. These overlap.
  Production/test TypeScript and changed-file lint passed.
- Documentation validator: 434 Markdown documents, 343 strictly governed;
  25 preexisting cross-repository source-link warnings remain.

## Real read costs

Two read-only service-chain samples used the configured test PG/S3 and the
existing 412-commit repository, returning 80 summary rows. Both performed real
authorization and snapshot admission. Cold: 11.253 / 11.947 seconds, five DB
requests (including two metadata pages) and one 19,994,307-byte S3 GET. Warm:
1.575 / 2.603 seconds, three DB requests and zero S3 GETs. Selected detail:
4.429 / 4.337 seconds, five DB requests and zero S3 GETs.

These are not click-to-render or concurrency benchmarks. Cold pack transfer,
metadata pages and detail ancestry remain explicit costs. Durable human-facing
breakdown, measurement boundaries and index links are in
`puppy-issues:document/puppyone-desktop/cloud/evidence/history-reads-2026-10-23.md`;
the owning architecture is `docs/architecture/01-version-engine.md` and
`puppy-issues:document/puppyone/architecture/01-version-engine.md#project-history-read-model`.
