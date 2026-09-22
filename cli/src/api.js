import { resolveAuth, loadConfig, saveConfig, getTargetAuth, saveTargetAuth } from "./config.js";
import { repositoryContractHeaders } from "./repository-contract.js";

export class ApiError extends Error {
  constructor(status, code, message, hint, data) {
    super(message);
    this.status = status;
    this.code = code;
    this.hint = hint;
    this.data = data;
  }
}

export function parseApiErrorPayload(text, fallbackCode = "API_ERROR") {
  let message = text || "Request failed";
  let code = fallbackCode;
  let data = null;

  try {
    const parsed = JSON.parse(text);
    const detail = parsed.detail;
    data = parsed.data ?? (detail && typeof detail === "object" ? detail : null);

    if (detail && typeof detail === "object") {
      message = detail.message ?? detail.detail ?? JSON.stringify(detail);
      if (typeof detail.code === "string") code = detail.code;
      if (typeof detail.error_code === "string") code = detail.error_code;
    } else {
      message = parsed.message ?? detail ?? message;
    }

    if (data && typeof data === "object") {
      if (typeof data.code === "string") code = data.code;
      if (typeof data.error_code === "string") code = data.error_code;
    }
  } catch {}

  return { code, message: String(message), data };
}

// Channel header. The backend access-key auth dependency uses this to decide which
// connector's `status` to consult when enforcing pause/resume.
//   "cli"        — manual Puppyone CLI commands typed into a terminal
//   "filesystem" — the local-folder sync daemon (set by daemon code path)
// Anything not recognised falls back to "no per-channel enforcement",
// which is the historical (pre-pause-enforcement) behaviour.
const DEFAULT_CLIENT_KIND = "cli";

export function createClient(cmd, { clientKind = DEFAULT_CLIENT_KIND } = {}) {
  const { apiUrl, apiKey } = resolveAuth(cmd);

  if (!apiKey) {
    throw new ApiError(0, "NOT_AUTHENTICATED", "Not logged in.", 'Run `puppyone auth login` first.');
  }

  return _makeClient(
    apiUrl,
    { Authorization: `Bearer ${apiKey}` },
    { autoRefresh: true, clientKind },
  );
}

export function createAccessKeyClient(accessKey, cmd, apiUrlOverride, { clientKind = DEFAULT_CLIENT_KIND } = {}) {
  const opts = collectOpts(cmd);
  const config = loadConfig();
  const apiUrl = apiUrlOverride ?? opts.apiUrl ?? config.api_url ?? "http://localhost:9090";

  if (!accessKey) {
    throw new ApiError(0, "MISSING_KEY", "Access key is required.", "Provide --key <access-key>.");
  }

  return _makeClient(
    apiUrl,
    { "X-Access-Key": accessKey },
    { autoRefresh: false, clientKind },
  );
}

/** @deprecated Use createAccessKeyClient */
export const createOpenClawClient = createAccessKeyClient;

export function collectOpts(cmd) {
  let cur = cmd;
  let merged = {};
  while (cur) {
    const o = cur.opts?.() ?? {};
    merged = { ...o, ...merged };
    cur = cur.parent;
  }
  return merged;
}

const REFRESH_BUFFER_SECONDS = 300;

async function _tryRefreshToken(targetUrl) {
  const targetAuth = getTargetAuth(targetUrl);
  const refreshToken = targetAuth?.refresh_token;
  if (!refreshToken) return null;

  try {
    const res = await fetch(`${targetUrl}/api/v1/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });

    if (!res.ok) return null;

    const json = await res.json();
    if (json.code !== 0 || !json.data) return null;

    const { access_token, refresh_token: newRefresh, expires_in } = json.data;
    const tokenExpiresAt = Math.floor(Date.now() / 1000) + (expires_in || 3600);

    saveTargetAuth(targetUrl, {
      api_key: access_token,
      refresh_token: newRefresh || refreshToken,
      token_expires_at: tokenExpiresAt,
    });

    return access_token;
  } catch {
    return null;
  }
}

function _buildUrl(baseUrl, path, query) {
  let url = `${baseUrl}/api/v1${path}`;
  if (query) {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(query)) {
      if (v != null) params.set(k, String(v));
    }
    const qs = params.toString();
    if (qs) url += `?${qs}`;
  }
  return url;
}

function _makeClient(apiUrl, authHeaders, { autoRefresh = false, clientKind = "cli" } = {}) {
  const baseUrl = apiUrl.replace(/\/+$/, "");

  // Channel header attached to every request out of this client. The
  // backend reads it during version access resolution and rejects the request when the
  // matching connector for the resolved scope is paused. Threaded into
  // a single source so both `request` and `rawRequest` / `upload` agree.
  const channelHeaders = {
    ...repositoryContractHeaders(),
    ...(clientKind ? { "X-Puppy-Client": clientKind } : {}),
  };

  async function getAuthHeaders() {
    if (!autoRefresh) return authHeaders;

    const targetAuth = getTargetAuth(baseUrl);
    const expiresAt = targetAuth?.token_expires_at;

    if (expiresAt && (Date.now() / 1000) > (expiresAt - REFRESH_BUFFER_SECONDS)) {
      const newToken = await _tryRefreshToken(baseUrl);
      if (newToken) {
        return { Authorization: `Bearer ${newToken}` };
      }
    }

    return authHeaders;
  }

  async function request(method, path, body, query) {
    const url = _buildUrl(baseUrl, path, query);
    const currentAuthHeaders = await getAuthHeaders();
    const headers = {
      ...currentAuthHeaders,
      ...channelHeaders,
      "Content-Type": "application/json",
    };

    const fetchOpts = {
      method,
      headers,
      body: body != null ? JSON.stringify(body) : undefined,
      redirect: "manual",  // handle redirects manually to preserve auth headers
    };
    let res = await fetch(url, fetchOpts);

    // Follow redirects manually (preserving Authorization header + fixing http→https)
    if (res.status >= 300 && res.status < 400) {
      const location = res.headers.get("location");
      if (location) {
        let redirectUrl = location.startsWith("http") ? location : new URL(location, url).href;
        // Fix protocol downgrade (proxy may strip https)
        if (url.startsWith("https://") && redirectUrl.startsWith("http://")) {
          redirectUrl = redirectUrl.replace("http://", "https://");
        }
        res = await fetch(redirectUrl, { ...fetchOpts, redirect: "follow" });
      }
    }

    if (res.status === 401 && autoRefresh) {
      const newToken = await _tryRefreshToken(baseUrl);
      if (newToken) {
        const retryHeaders = {
          "Content-Type": "application/json",
          Authorization: `Bearer ${newToken}`,
          ...channelHeaders,
        };
        const retryRes = await fetch(url, {
          method,
          headers: retryHeaders,
          body: body != null ? JSON.stringify(body) : undefined,
          redirect: "manual",
        });
        // Follow redirect on retry too (fixing http→https)
        let retryFinal = retryRes;
        if (retryRes.status >= 300 && retryRes.status < 400) {
          const loc = retryRes.headers.get("location");
          if (loc) {
            let rUrl = loc.startsWith("http") ? loc : new URL(loc, url).href;
            if (url.startsWith("https://") && rUrl.startsWith("http://")) rUrl = rUrl.replace("http://", "https://");
            retryFinal = await fetch(rUrl, { method, headers: retryHeaders, body: body ? JSON.stringify(body) : undefined });
          }
        }
        if (retryFinal.ok) {
          const json = await retryFinal.json();
          if (json.code !== 0) {
            const code = typeof json.data?.error_code === "string" ? json.data.error_code : "API_BIZ_ERROR";
            throw new ApiError(0, code, json.message ?? "Unknown error", undefined, json.data);
          }
          return json.data;
        }
      }
      throw new ApiError(401, "SESSION_EXPIRED", "Session expired.", "Run `puppyone auth login` to re-authenticate.");
    }

    if (!res.ok) {
      const text = await res.text().catch(() => "");
      const parsedError = parseApiErrorPayload(text);

      const usesAccessKey = currentAuthHeaders["X-Access-Key"] != null;
      const hint = res.status === 401
        ? (usesAccessKey ? "Invalid or expired access key." : "Invalid or expired token. Run `puppyone auth login`.")
        : res.status === 404
        ? "Check the resource ID or path."
        : res.status === 409
        ? "The remote scope changed before this write landed. Re-read the target path, reapply your change, and run the command again."
        : undefined;

      throw new ApiError(res.status, parsedError.code, parsedError.message, hint, parsedError.data);
    }

    const json = await res.json();
    if (json.code !== 0) {
      const code = typeof json.data?.error_code === "string" ? json.data.error_code : "API_BIZ_ERROR";
      throw new ApiError(0, code, json.message ?? "Unknown error", undefined, json.data);
    }

    return json.data;
  }

  async function rawRequest(method, path, body, query) {
    const url = _buildUrl(baseUrl, path, query);
    const currentAuthHeaders = await getAuthHeaders();
    return fetch(url, {
      method,
      headers: {
        ...currentAuthHeaders,
        ...channelHeaders,
        "Content-Type": "application/json",
      },
      body: body != null ? JSON.stringify(body) : undefined,
    });
  }

  async function upload(path, filePath, query) {
    const { createReadStream, statSync } = await import("node:fs");
    const { basename } = await import("node:path");
    const url = _buildUrl(baseUrl, path, query);
    const currentAuthHeaders = await getAuthHeaders();
    const stat = statSync(filePath);
    const stream = createReadStream(filePath);

    return fetch(url, {
      method: "POST",
      headers: {
        ...currentAuthHeaders,
        ...channelHeaders,
        "Content-Type": "application/octet-stream",
        "X-Filename": basename(filePath),
        "Content-Length": String(stat.size),
      },
      body: stream,
      duplex: "half",
    });
  }

  return {
    baseUrl,
    get: (path, query) => request("GET", path, null, query),
    post: (path, body, query) => request("POST", path, body, query),
    put: (path, body, query) => request("PUT", path, body, query),
    patch: (path, body, query) => request("PATCH", path, body, query),
    del: (path, body, query) => request("DELETE", path, body, query),
    raw: rawRequest,
    upload,
    getAuthHeaders,
    getChannelHeaders: () => ({ ...channelHeaders }),
  };
}
