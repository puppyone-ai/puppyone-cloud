/**
 * Transport abstraction for the shared cloud API client (ISSUE-022).
 *
 * The web frontend and the desktop cloud panel authenticate and issue HTTP
 * requests very differently — the web attaches a Supabase bearer token via
 * `lib/apiClient`, the desktop routes requests through the Electron IPC bridge
 * (`window.puppyoneDesktop.requestCloudSessionApi`). Everything ELSE about the
 * cloud API — endpoint paths, request/response shapes, entity types — is
 * identical because both talk to the same backend.
 *
 * `CloudTransport` is that seam: each platform provides these five methods,
 * and the endpoint factories in `./endpoints/*` build the domain functions on
 * top of them. The signatures deliberately mirror the web frontend's existing
 * `lib/apiClient` exports so the web binding is a straight pass-through.
 */
export interface CloudTransport {
  get<T>(path: string): Promise<T>;
  post<T>(path: string, body?: unknown): Promise<T>;
  put<T>(path: string, body?: unknown): Promise<T>;
  patch<T>(path: string, body?: unknown): Promise<T>;
  del<T>(path: string): Promise<T>;
}
