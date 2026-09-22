/**
 * Domain constants for the access page.
 *
 * Provider taxonomy now lives in `frontend/lib/accessProviderRegistry`.
 * Keep only page-local status presentation here; re-export provider labels
 * and group metadata for older access components while they migrate.
 */

import {
  ACCESS_PROVIDER_GROUP_LABELS,
  ACCESS_PROVIDER_GROUP_ORDER,
  ACCESS_PROVIDER_LABELS,
  type AccessProviderGroupKey,
} from '@/lib/accessProviderRegistry';

export const PROVIDER_LABELS = ACCESS_PROVIDER_LABELS;

export const STATUS_COLORS: Record<string, string> = {
  active: 'var(--po-success)',
  syncing: 'var(--po-accent)',
  error: 'var(--po-danger)',
  paused: 'var(--po-warning)',
  pending: 'var(--po-text-subtle)',
};

export const STATUS_LABEL: Record<string, string> = {
  active: 'Active',
  syncing: 'Syncing',
  error: 'Error',
  paused: 'Paused',
  pending: 'Pending',
};

// The previous version exposed an `APGroupKey` ("cli/agent/mcp/sandbox/
// integration") that drove a sidebar filter-tab strip — sliced the
// access points by *provider type*. That was the wrong axis: scope
// (the mount point an access point binds to) is the actual primary key
// in our data model, and the user wants to manage "who can see
// /docs?" not "where do my CLIs live?". The sidebar is now scope-keyed,
// and provider type is shown only on each access point inside the
// detail panel as a small type-line. No filter tabs survive.

// Within the right pane, every AP bound to the selected scope is
// rendered as a card. We group those cards by provider type so each
// access point reads as a first-class entity in the switcher chip.
//
// FS CLI and Git Remote are standard target methods enabled explicitly.
// Agent is a separately created target-bound runtime.
// MCP / Sandbox / Third-party are user-created.

export const CONNECTOR_GROUP_LABELS = ACCESS_PROVIDER_GROUP_LABELS;

export const CONNECTOR_GROUP_ORDER = ACCESS_PROVIDER_GROUP_ORDER;

export type ConnectorGroupKey = AccessProviderGroupKey;
