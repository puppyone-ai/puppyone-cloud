/**
 * Explicit request-header allowlist for the same-origin backend proxy.
 * Keep protocol/version headers here so browser API-client upgrades reach
 * the backend rather than being silently stripped at the BFF boundary.
 */
export const BACKEND_REQUEST_HEADERS_TO_FORWARD = [
  'accept',
  'authorization',
  'content-type',
  'cookie',
  'idempotency-key',
  'if-modified-since',
  'if-none-match',
  'if-range',
  'range',
  'x-puppyone-repository-contract',
  'x-request-id',
  'traceparent',
  'tracestate',
] as const;

export function forwardBackendRequestHeaders(requestHeaders: Headers): Headers {
  const headers = new Headers();
  for (const headerName of BACKEND_REQUEST_HEADERS_TO_FORWARD) {
    const value = requestHeaders.get(headerName);
    if (headerName === 'x-request-id' && value && !/^[\w.-]{1,128}$/.test(value)) continue;
    if (value) headers.set(headerName, value);
  }
  return headers;
}
