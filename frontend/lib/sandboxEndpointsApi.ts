/**
 * Web binding for the shared sandbox-endpoint API (ISSUE-022).
 *
 * Contracts + types live in `@puppyone/cloud-core` (shared with the desktop
 * cloud panel); this binds them to the web transport and re-exports the same
 * names, so existing call sites are unchanged.
 */
import { createSandboxEndpointsApi } from '@puppyone/cloud-core';

import { webCloudTransport } from './cloudCoreTransport';

export type {
  SandboxEndpoint,
  SandboxMount,
  SandboxMountPermissions,
  SandboxResourceLimits,
  SandboxRuntime,
  CreateSandboxEndpointParams,
  UpdateSandboxEndpointParams,
} from '@puppyone/cloud-core';

const api = createSandboxEndpointsApi(webCloudTransport);

export const listSandboxEndpoints = api.listSandboxEndpoints;
export const getSandboxEndpoint = api.getSandboxEndpoint;
export const getSandboxEndpointByPath = api.getSandboxEndpointByPath;
export const createSandboxEndpoint = api.createSandboxEndpoint;
export const updateSandboxEndpoint = api.updateSandboxEndpoint;
export const deleteSandboxEndpoint = api.deleteSandboxEndpoint;
export const regenerateSandboxEndpointKey = api.regenerateSandboxEndpointKey;
