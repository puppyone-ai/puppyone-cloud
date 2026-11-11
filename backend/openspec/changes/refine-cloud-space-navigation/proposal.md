# Refine cloud Space navigation

## Why

This Mac must remain visible while Space selects cloud projects. Personal/team sharing groups misrepresent one collaboration model.

## What Changes

- Keep every registered local project visible regardless of selected Space.
- Put cloud-only switching at the Sidebar bottom; retain per-window selection and Account access.
- **BREAKING:** publish one `kind: space` for organizations and keep shared/project permissions intact.
- Render device sharing under its own device context rather than personal membership.

## Authorization

The user approved this product boundary and requested frontend/backend/database implementation in this conversation on 2026-11-10. This records that approval; hosted deployment and remote publication remain separate.

## Impact

Desktop navigation and Home, organization DTO, Web labels and populated database verification. Existing organizations/org_members/projects already implement one model; preserve legacy identity columns, ACLs and billing IDs. No new membership tables or destructive cosmetic schema migration.

Private historical billing adapters keep their existing wire values for old subscription compatibility; they do not choose a current Space model or UI grouping. Coordinate supported-client rollout before hosted deployment.
