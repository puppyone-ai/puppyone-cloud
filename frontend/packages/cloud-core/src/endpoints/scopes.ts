import type { CloudTransport } from "../transport";

// ── Types (mirror backend src/repo/schemas.py) ──────────────────────────
export type ScopeMode = "r" | "rw";

export interface RepositoryScope {
  id: string;
  project_id: string;
  name: string;
  path: string; // canonical, non-empty, no leading/trailing /
  exclude: string[];
  max_mode: ScopeMode;
  created_at: string;
  updated_at: string;
}

/** Bind the repo-scope API to a platform transport (ISSUE-022). */
export function createScopesApi(t: CloudTransport) {
  return {
    async listScopes(projectId: string): Promise<RepositoryScope[]> {
      return (await t.get<RepositoryScope[]>(`/api/v1/projects/${projectId}/scopes`)) || [];
    },
    createScope(
      projectId: string,
      body: { name: string; path: string; exclude?: string[]; max_mode?: ScopeMode },
    ): Promise<RepositoryScope> {
      return t.post<RepositoryScope>(`/api/v1/projects/${projectId}/scopes`, body);
    },
    updateScope(
      projectId: string,
      scopeId: string,
      body: { name?: string; exclude?: string[]; max_mode?: ScopeMode },
    ): Promise<RepositoryScope> {
      return t.patch<RepositoryScope>(`/api/v1/projects/${projectId}/scopes/${scopeId}`, body);
    },
    async deleteScope(projectId: string, scopeId: string): Promise<void> {
      await t.del(`/api/v1/projects/${projectId}/scopes/${scopeId}`);
    },
  };
}

// ── Pure scope helpers (transport-agnostic domain logic) ─────────────────

/**
 * Match a URL path to a scope. A folder shows the connectors of its exact-match
 * scope — no parent-child inheritance. Returns null if no scope matches the path
 * (callers render the Project root target separately when the path is empty).
 */
export function matchScopeForPath(
  urlPath: string,
  scopes: readonly RepositoryScope[],
): RepositoryScope | null {
  const normalized = urlPath.replaceAll(/^\/+|\/+$/g, "").replaceAll(/\/+/g, "/");
  return scopes.find((s) => s.path === normalized) ?? null;
}

/**
 * Test whether a node path falls within a scope's boundary (the scope folder
 * itself or a descendant). Both args are canonical paths. The empty Project
 * root projection remains permissive for Version Engine callers.
 */
export function isWithinScope(nodePath: string, scopePath: string): boolean {
  const normNode = (nodePath || "").replaceAll(/^\/+|\/+$/g, "").replaceAll(/\/+/g, "/");
  const normScope = (scopePath || "").replaceAll(/^\/+|\/+$/g, "").replaceAll(/\/+/g, "/");
  if (normScope === "") return true;
  if (normNode === normScope) return true;
  return normNode.startsWith(`${normScope}/`);
}
