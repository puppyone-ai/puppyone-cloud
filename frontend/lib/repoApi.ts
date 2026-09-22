/**
 * Web binding for the shared repo scopes / connectors / identity API (ISSUE-022).
 *
 * The endpoint contracts, entity types, and pure domain helpers now live in
 * `@puppyone/cloud-core` (shared with the desktop cloud panel). This file binds
 * the endpoint functions to the web transport and re-exports the same names, so
 * existing call sites are unchanged.
 *
 * Backend mounts (unchanged):
 *   /api/v1/projects/{pid}/scopes | /connectors | /access-point
 */
import { createScopesApi, createConnectorsApi } from '@puppyone/cloud-core';

import { webCloudTransport } from './cloudCoreTransport';

// Provider-registry re-exports kept for call-site compatibility.
export { isGitRemoteProvider, normalizeConnectorProvider } from '@/lib/accessProviderRegistry';

// Pure domain helpers (transport-agnostic) now sourced from cloud-core.
export {
  matchScopeForPath,
  matchRepositoryViewForPath,
  projectRootRepositoryView,
  repositoryScopeView,
  repositoryTargetKey,
  repositoryViewKey,
  isWithinScope,
  BUILTIN_PROVIDERS,
  isAccessSurfaceConnector,
  normalizeAccessSurfaceConnectors,
  sortConnectorsBuiltinFirst,
} from '@puppyone/cloud-core';

export type {
  ScopeMode,
  RepositoryScope,
  RepositoryTarget,
  RepositoryView,
  ConnectorDirection,
  ConnectorStatus,
  Connector,
  RepoIdentity,
  ConnectorRun,
  CreateConnectorBody,
} from '@puppyone/cloud-core';

const scopesApi = createScopesApi(webCloudTransport);
const connectorsApi = createConnectorsApi(webCloudTransport);

// ── Scopes ──────────────────────────────────────────────────────────────
export const listScopes = scopesApi.listScopes;
export const createScope = scopesApi.createScope;
export const updateScope = scopesApi.updateScope;
export const deleteScope = scopesApi.deleteScope;

// ── Connectors + repo identity ──────────────────────────────────────────
export const listConnectors = connectorsApi.listConnectors;
export const createConnector = connectorsApi.createConnector;
export const enableTargetAccess = connectorsApi.enableTargetAccess;
export const updateConnector = connectorsApi.updateConnector;
export const deleteConnector = connectorsApi.deleteConnector;
export const runConnectorNow = connectorsApi.runConnectorNow;
export const activateAgentConnector = connectorsApi.activateAgentConnector;
export const pauseConnector = connectorsApi.pauseConnector;
export const resumeConnector = connectorsApi.resumeConnector;
export const getRepoIdentity = connectorsApi.getRepoIdentity;
