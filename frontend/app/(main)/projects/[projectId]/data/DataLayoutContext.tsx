'use client';

import { createContext, useContext } from 'react';
import type { Tool } from '@/lib/mcpApi';
import type { Connector, RepoIdentity, RepositoryTarget, RepositoryView } from '@/lib/repoApi';

export interface SyncStatusSync {
  id: string;
  path: string | null;
  provider: string;
  direction: string;
  status: string;
  name?: string;
  access_key?: string;
}

export interface SyncEndpointInfo {
  syncId: string;
  provider: string;
  direction: string;
  status: string;
  name?: string;
  accessKey?: string | null;
  repositoryTarget?: RepositoryTarget;
}

export interface DataLayoutContextValue {
  syncStatusData: { syncs: SyncStatusSync[] } | undefined;
  mutateSyncStatus: () => Promise<any>;
  projectTools: Tool[];
  syncEndpoints: Map<string, SyncEndpointInfo>;
  nodeEndpointMap: Map<string, SyncEndpointInfo[]>;

  /** Project-root repository plus true scoped repository views. */
  scopes: RepositoryView[];
  /** Index of connectors by the explicit repository target discriminant. */
  connectorsByTarget: Map<string, Connector[]>;
  /** Repo identity (URL + prompt_template + per-scope keys) — fetched once per project. */
  repoIdentity: RepoIdentity | undefined;
  repoIdentityLoading: boolean;
  repoIdentityError: unknown;
  mutateRepo: () => Promise<unknown>;
}

const DataLayoutContext = createContext<DataLayoutContextValue | null>(null);

export function useDataLayout() {
  const ctx = useContext(DataLayoutContext);
  if (!ctx) throw new Error('useDataLayout must be used within DataLayout');
  return ctx;
}

export { DataLayoutContext };
