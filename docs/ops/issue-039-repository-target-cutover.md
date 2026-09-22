# ISSUE-039 Repository Target Cutover

ISSUE-039 makes the Project the canonical repository root and treats Scopes as
optional non-empty path views. The final architecture also removes any
server-side local-checkout identity.

## Final data model

```text
Project
├── canonical repository root        scope_id = NULL
├── repository_scopes                non-empty path views
├── access_surfaces                  exact root/Scope targets
└── access_surface_credentials       independent runtime principals
```

## Upgrade sequence

1. Run `20260715_project_owned_repository_targets_preflight` twice and verify
   its immutable receipt/checksum.
2. Apply
   `20260715000000_project_owned_repository_targets_contract_cutover.sql`.
3. Verify legacy root-Scope rows became Project-root targets and real child
   Scopes retained their geometry.
4. Apply `20260716000000_remove_workspace_binding.sql`.
5. Verify former checkout-owned Git credentials became user-owned credentials,
   then verify the checkout table and credential foreign key are absent.

The migration harness intentionally tests steps 2 and 4 separately. This proves
both historical data preservation and the final schema instead of treating an
intermediate schema as the desired endpoint.

## Required checks

- root Access Surfaces have `scope_id IS NULL`;
- every non-null Scope target exists in the same Project;
- repository Scopes have non-empty normalized paths;
- no credential material exists on Scope rows;
- user Git credentials preserve hash, target, mode, and owner;
- current role caps RuntimeGrant mode dynamically;
- Project membership loss denies user credentials;
- integrity reports return all zero counts;
- no local device/folder/checkout table or foreign key remains.

## Verification commands

```bash
./scripts/test-repository-target-migration.sh
supabase test db
```

The first command requires a disposable local Supabase instance. It resets that
instance and must never be pointed at production.

## Rollback

The final removal is a coordinated breaking migration. Roll back application
and database from the same pre-deploy backup. Do not dual-write an intermediate
checkout identity and do not add an optional compatibility field to new APIs.
