# Canonical Git Remote Rollout Runbook

This runbook deploys credential-free canonical Git locators and independent
user Git credentials.

## Preconditions

- Back up the database and record the application revision.
- Run all data-migration preflights required by earlier repository-target
  migrations.
- Confirm `unified_authorization_preflight()` and
  `repository_target_integrity_report()` contain only zero counts.
- Confirm Desktop and Backend both support repository contract version 2.

## Deploy order

1. Apply database migrations through
   `20260716000000_remove_workspace_binding.sql`.
2. Deploy Backend repository-context and Git-credential endpoints.
3. Deploy Git transport using canonical root and Scope routes.
4. Deploy Desktop canonical-remote discovery.
5. Run the SQL contract suites and application smoke tests.

Do not enable Desktop context resolution against a Backend that still requires
a local-checkout registration identifier.

## Database checks

```sql
select public.unified_authorization_preflight();
select public.repository_target_integrity_report();
```

Both JSON objects must contain zero for every numeric field.

```sql
select to_regclass('public.project_workspace_bindings');
```

The result must be `NULL`.

```sql
select column_name
from information_schema.columns
where table_schema = 'public'
  and table_name = 'access_surface_credentials'
  and column_name = 'workspace_binding_id';
```

The query must return no rows. User Git credentials must have `user_id`,
`credential_lifecycle = 'user'`, and no plaintext material.

## Product smoke matrix

| Case | Expected result |
|---|---|
| No canonical PuppyOne remote | Local-only page; no repository-context request |
| Canonical root remote + authorized user | Project content opens |
| Canonical Scope remote + authorized user | Exact scoped content opens |
| Canonical remote + unauthorized user | Permission recovery state; no data leak |
| Missing Project/Scope | Not-found recovery state |
| Conflicting canonical targets | Local repair guidance; no guessed Project |
| Legacy secret-bearing remote only | Local-only Cloud UI; Git transport may still operate |
| Session rotates during context request | Internal retry; no `SESSION_CHANGED` text |
| User role downgraded | Existing rw credential resolves read-only |
| Membership removed | Existing user credential is denied |
| One credential revoked | Other credentials for the user remain valid |

## Observability

Monitor Project authorization decisions, credential issue/revoke events, Git
credential resolution outcomes, target mismatch counts, and context endpoint
latency. Logs must redact user IDs and Project references according to the
authorization logging policy and must never include raw secrets or local paths.

## Abort

Stop the rollout if migrations fail, integrity counts are non-zero, target
mismatch rates increase, or unauthorized repository metadata is observable.
Do not recreate a local-checkout registration table as rollback. Restore the
database backup and previous application revision as one coordinated release.
