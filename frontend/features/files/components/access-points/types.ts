import type { SyncEndpointInfo } from '@/features/files/components/explorer';

export interface EndpointEntry {
  ep: SyncEndpointInfo;
  nodeId: string;
  name: string;
  nodeName?: string;
}

export type EndpointNameMap = {
  agents: Record<string, string>;
  nodes: Record<string, string>;
  syncs: Record<string, string>;
};

export type ProviderIconLookup = Record<string, { icon: string | null; iconUrl: string | null }>;
