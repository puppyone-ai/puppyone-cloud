/**
 * Web binding for the shared MCP-endpoint API (ISSUE-022).
 *
 * The endpoint contracts + types now live in `@puppyone/cloud-core` (shared with
 * the desktop cloud panel). This file binds them to the web transport and
 * re-exports the same names, so existing call sites are unchanged.
 */
import { createMcpEndpointsApi } from '@puppyone/cloud-core';

import { webCloudTransport } from './cloudCoreTransport';

export type {
  McpEndpoint,
  McpToolsConfig,
  CreateMcpEndpointParams,
  UpdateMcpEndpointParams,
} from '@puppyone/cloud-core';

const api = createMcpEndpointsApi(webCloudTransport);

export const listMcpEndpoints = api.listMcpEndpoints;
export const getMcpEndpoint = api.getMcpEndpoint;
export const getMcpEndpointByPath = api.getMcpEndpointByPath;
export const createMcpEndpoint = api.createMcpEndpoint;
export const updateMcpEndpoint = api.updateMcpEndpoint;
export const deleteMcpEndpoint = api.deleteMcpEndpoint;
export const regenerateMcpEndpointKey = api.regenerateMcpEndpointKey;
