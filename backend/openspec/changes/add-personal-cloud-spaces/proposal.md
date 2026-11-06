# Change: Explicit personal Cloud destinations for Desktop

## Why
The user approved Homepage groups for their personal Cloud and each organization, and an explicit local-to-Cloud Git publish by dropping a local project into a group. The existing organization type is not authoritative for personal ownership: default accounts have been created as teams and old personal rows may have members.

## What Changes
- Add an explicit, immutable personal-owner binding to new organization-backed personal spaces, one per user; preserve all existing organizations, Projects, memberships and default selections.
- Provision a personal destination idempotently during authenticated account initialization and via an explicit authenticated ensure endpoint for existing accounts.
- Return space kind and current-user create capability in organization responses. Existing unbound organizations remain team destinations, irrespective of their names, member counts or legacy type.
- Personal spaces admit only their owner; team access and Project authorization are unchanged.
- Desktop groups by this authority and reuses its existing confirmed Git publication flow. No automatic upload, source deletion, remote rebinding, cross-organization move, or compute allocation.

## Impact
- Organization, account initialization, one additive SQL migration, focused SQL/API regression tests.
- Desktop implementation lives in `puppyone` task branch `codex/cloud-spaces-delivery`.
- Product intent (grouping and drag-to-publish) is requested in the current conversation. The user approved systematic implementation on 2026-11-07 without intermediate questions; retain legacy spaces and provision a separate empty personal space rather than guessing ownership.
- Publication and migration are subject to Qubits validation. No main deployment.
