# Unify Space membership and seat-based plans

## Why

Space is the durable content, configuration and billing boundary. The current
personal-space identity prohibits collaboration and forces users to create a
different space when adding people. Desktop also maintains unrelated space
selections on Home and Billing.

## What Changes

- Reuse organizations and existing IDs for every Space. Default is a user
  preference, not a permanent single-person security type.
- Allow the existing owner-authorized membership/transfer flows on all spaces.
- Protect legacy personal projects before opening membership; detach the legacy
  personal binding from cascading account deletion.
- Expose caller-specific default and role/capabilities for a unified switcher.
- Use one Space-wide Team or Enterprise subscription with one or more seats.
  Historical provider products remain interpretable; no remote billing migration
  is performed by application startup or a schema migration.

## Authorization and Delivery

The user approved this target and explicitly requested implementation after a
desktop TODO on 2026-11-09. This proposal records that accepted scope; it is not
a request to deploy, change real subscriptions or publish remote branches.
Local changes follow the existing database release and project authorization
contracts. New SSO, SCIM and audit export products are outside this migration.

## Impact

Cloud organization/onboarding APIs, one additive migration, membership upgrade
tests, PuppyPay catalog, and Desktop composition. Git locators, repository
objects, ProjectGrant and existing account AI wallets retain their owners.
