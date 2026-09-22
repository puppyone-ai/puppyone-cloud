## 1. Contract and schema

- [x] 1.1 Add the destructive migration from Binding credentials to user Git credentials.
- [x] 1.2 Drop the Binding table, functions, columns, triggers, indexes, and lifecycle value.
- [x] 1.3 Update runtime resolution and integrity/postflight SQL.

## 2. Backend

- [x] 2.1 Remove the Workspace Binding module, routes, capabilities, and manifest entries.
- [x] 2.2 Add canonical repository-context and user Git credential issuance APIs.
- [x] 2.3 Resolve Git RuntimeGrant from route, Surface, target, user credential, and current Project role.
- [x] 2.4 Remove all runtime Binding DTO fields and fallback logic.

## 3. Desktop

- [x] 3.1 Remove Binding fields from local config, API types, and workspace registry state.
- [x] 3.2 Replace Binding-first resolution with canonical-remote-first Project context resolution.
- [x] 3.3 Replace Attach/Detach with Git credential issuance and add/remove remote operations.
- [x] 3.4 Remove Binding UI states, messages, hooks, and tests.

## 4. Documentation and guards

- [x] 4.1 Update authorization, repository target, Git, Desktop, and release documentation.
- [x] 4.2 Add Backend/Desktop architecture scans that reject Binding identity reintroduction.
- [x] 4.3 Update cross-client contract fixtures.

## 5. Verification

- [x] 5.1 Run migration static/invariant tests and record unavailable real-Postgres evidence explicitly.
- [x] 5.2 Run Backend tests and lint/type checks.
- [x] 5.3 Run Desktop tests, architecture checks, typecheck, lint, and build.
