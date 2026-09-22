# July 2026 Database Migration Transition

This runbook adopts the portable data migration framework without rewriting
shared history.

## Proven state at adoption

The repository and available deployment evidence establish:

- Qubits has applied the 2026-07-11 schema sequence through its follow-up fixes.
- Qubits has not applied the 2026-07-12 authorization migrations.
- The repository has no GitHub Actions evidence proving that Production applied
  the 2026-07-11 or 2026-07-12 sequences. A manual change, if any, must be
  proven with `supabase migration list` and database queries.
- The 0711 SQL history is immutable. The two associated backfills are now
  `legacy: true` data artifacts.
- Every schema file present at Qubits commit
  `007932f1f81130b0f80a62a341447fd750bb8d5b` is checksum-pinned in the
  adoption baseline; this is the complete “past changes are grandfathered”
  boundary.
- The unpublished 0712 permission retirement has been split into Expand, Data,
  and a pending Contract.

Do not infer Production state from Qubits or from repository files.

## One-time environment setup

Create protected `staging` and `production` GitHub Environments and configure
the secrets listed in
`docs/architecture/13-database-release-governance.md`. Use the same generic
secret names in each environment; set `DATABASE_URL` to that environment's
direct or session-pooler PostgreSQL URI.

## Reconcile Qubits 0711 receipts

On the `qubits` branch, dispatch `Data Migration` in this order:

1. `plan`, `run`, and `verify` `20260704_scope_access_key_hash`.
2. `plan`, `run`, and `verify` `20260711_surface_credentials`.

Because the destructive schema is already present, both runners may verify as
no-ops and publish receipts. A checksum mismatch or unexpected remaining secret
must fail; do not repair the receipt manually.

## Reconcile Production 0711

First inspect, without mutation:

```bash
supabase migration list --linked
```

Then use only protected workflows:

1. If `20260616003000` is present, run
   `20260711_surface_credentials` before allowing schema deployment to reach
   `20260711070000`.
2. If `20260704000000` is present, run
   `20260704_scope_access_key_hash` before schema deployment.
3. If the scope artifact reports missing `20260704000000`, run the Production
   schema workflow once. The immutable 110700 guard should stop safely after
   earlier schema files land. Then run the scope artifact and re-run schema
   deployment.
4. Verify both legacy artifacts and the schema list after deployment.

If Production is older than `20260616003000`, stop. Do not let a full `db push`
cross the surface-credential retirement boundary. Either deploy a bounded
Expand release first or record the approved product decision to invalidate and
reissue unused Agent/Sandbox credentials. Never silently invent a completion
receipt.

## Deploy 0712 authorization safely

1. Merge/deploy `20260712010000_expand_unified_project_authorization.sql` to Qubits.
2. Run and verify
   `20260712_repo_user_permissions_to_project_members` in Qubits.
3. Promote the same Expand/artifact to main and deploy Production.
4. Run and verify the same data migration in Production.
5. Deploy application cutover and wait for old instances to drain.
6. Create a later Contract PR by copying the reviewed
   `contract.pending.sql` into a new timestamped
   `supabase/migrations/*_contract_repo_user_permissions.sql` file.
7. Run staging verify on the Contract head SHA and Production verify on the
   current main SHA. Main Release Gate will reject the Contract without both.

The pending Contract is not a migration and must not be manually applied to a
shared database. It exists for review and local upgrade-path testing only.
