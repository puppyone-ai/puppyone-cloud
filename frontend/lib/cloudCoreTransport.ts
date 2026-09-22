import type { CloudTransport } from '@puppyone/cloud-core';

import { get, post, put, patch, del } from './apiClient';

/**
 * Web transport for `@puppyone/cloud-core` (ISSUE-022).
 *
 * `lib/apiClient` already attaches the Supabase bearer token, handles 401
 * refresh/redirect, and normalizes errors — and its `get/post/put/patch/del`
 * exports match the `CloudTransport` interface exactly, so this is a direct
 * pass-through. The desktop provides its own transport over the Electron IPC
 * bridge; everything above the transport (endpoints, types) is shared.
 */
export const webCloudTransport: CloudTransport = { get, post, put, patch, del };
