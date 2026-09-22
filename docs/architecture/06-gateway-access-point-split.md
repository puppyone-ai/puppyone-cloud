# Gateway And Access Boundary

PuppyOne keeps control-plane identity separate from version-plane access.

## Boundaries

- JWT-authenticated Web/API calls operate as product users through
  ProjectGrant.
- Git Remote, AP-FS, Sandbox, Agent, and MCP calls operate as machine
  principals through RuntimeGrant.
- A canonical Git locator declares a non-secret Project/root-or-Scope target;
  its HTTP credential authenticates the machine principal.
- AP-FS carries its credential in the protocol header and resolves the same
  Scope geometry without using the Git URL contract.
- Gateways and connectors may create intents, but only the Version Engine
  publishes version facts.

The normative Git route, credential, and migration contract is
[Git Remote Locator, Credential, And Access Point Contract](05-git-remote-accesspoint.md).
This document owns only the gateway boundary.

## Access Enforcement

Every write path checks:

- human Project action or machine credential validity, never one as a substitute
  for the other;
- exact Project/Scope/Access Surface identity;
- credential, surface, binding, and expiry lifecycle;
- channel status;
- scope path and excludes;
- effective read/write mode;
- current base/head precondition;
- audit actor and source channel.

There is no bypass path that can write versioned content outside
`VersionWriteEngine`.

## External shapes

```text
Git Remote root locator:
  https://<host>/git/{project_id}.git

Git Remote scoped locator:
  https://<host>/git/{project_id}/scopes/{scope_id}.git

Git credential:
  HTTP Basic password via an OS-backed credential helper

CLI / AP-FS:
  /api/v1/ap-fs/*
  X-Access-Key: <access_key>

Sandbox:
  short-lived credential + scoped runtime session
```

`/git/ap/<access_key>.git` is a bounded legacy compatibility route during the
canonical-locator migration. It is not the target gateway contract.

## Resolution boundary

Protocol adapters extract different request shapes, but L2 produces the same
kind of bounded fact:

```text
Git URL + HTTP credential
AP-FS header credential
MCP/Agent bearer credential
Sandbox session credential
          |
          v
RuntimeGrant(project, scope, path, excludes, mode, policy, principal)
          |
          v
RepoFacade / scoped filesystem context
          |
          v
Version Engine admission and publish
```

The Project ID and Scope ID in a Git locator do not grant access. The machine
credential must resolve to the same Project, Scope, active Access Surface, and
effective mode. Any mismatch fails before repo state, cache state, or private
metadata is opened.

## Control-plane exclusion

A RuntimeGrant can read or write only the admitted data plane. It cannot call:

- Team or Billing;
- Project settings or deletion;
- Project membership or sharing;
- Access Surface or credential management;
- any human control-plane administration.

Those operations always require a current human JWT and named ProjectAction.
