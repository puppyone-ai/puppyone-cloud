import type { CloudTransport } from "../transport";
import type { RepositoryTarget } from "../repositoryTargets";
import {
  BUILTIN_ACCESS_PROVIDER_IDS,
  getAccessProviderSortRank,
  isAccessProviderHiddenInAccess,
  normalizeConnectorProvider,
} from "../accessProviders";

// ── Types (mirror backend src/repo/schemas.py) ──────────────────────────
export type ConnectorDirection = "bidirectional" | "inbound" | "outbound";
export type ConnectorStatus = "active" | "paused" | "syncing" | "error";

export interface Connector {
  id: string;
  target: RepositoryTarget;
  provider: string; // 'cli' | 'agent' | 'notion' | 'gmail' | ...
  name: string;
  direction: ConnectorDirection;
  config: Record<string, unknown>;
  oauth_connection_id: number | null;
  trigger: Record<string, unknown>;
  status: ConnectorStatus;
  last_run_at: string | null;
  last_run_id: string | null;
  error_message: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface RepoIdentity {
  project_id: string;
  url: string; // https://<api>/git/<project_id>.git — Git remote
  prompt_template: string;
  content_initialized?: boolean;
  head_commit_id?: string | null;
  scopes: Array<{
    id: string;
    name: string;
    path: string;
    git_url: string;
  }>;
}

export interface ConnectorRun {
  id: string;
  connector_id: string;
  status: "running" | "success" | "failed" | "skipped";
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  error: string | null;
}

export interface CreateConnectorBody {
  target: RepositoryTarget;
  provider: string;
  direction: ConnectorDirection;
  name?: string;
  config?: Record<string, unknown>;
  oauth_connection_id?: number | null;
  trigger?: { type: "manual" | "scheduled" | "on_change"; config?: Record<string, unknown> };
}

/** Bind the connector + repo-identity API to a platform transport (ISSUE-022). */
export function createConnectorsApi(t: CloudTransport) {
  return {
    async listConnectors(
      projectId: string,
      filter?: { provider?: string; direction?: string; includeNonAccess?: boolean },
    ): Promise<Connector[]> {
      const qs = new URLSearchParams();
      if (filter?.provider) qs.set("provider", filter.provider);
      if (filter?.direction) qs.set("direction", filter.direction);
      if (filter?.includeNonAccess) qs.set("include_non_access", "true");
      const suffix = qs.toString() ? `?${qs.toString()}` : "";
      const rows =
        (await t.get<Connector[]>(`/api/v1/projects/${projectId}/connectors${suffix}`)) || [];
      return rows.map(normalizeConnector);
    },
    createConnector(projectId: string, body: CreateConnectorBody): Promise<Connector> {
      return t.post<Connector>(`/api/v1/projects/${projectId}/connectors`, body);
    },
    enableTargetAccess(projectId: string, target: RepositoryTarget): Promise<Connector[]> {
      return t.post<Connector[]>(
        `/api/v1/projects/${projectId}/connectors/enable-target`,
        { target },
      );
    },
    updateConnector(
      projectId: string,
      connectorId: string,
      body: Partial<CreateConnectorBody> & { status?: "active" | "paused" },
    ): Promise<Connector> {
      return t.patch<Connector>(`/api/v1/projects/${projectId}/connectors/${connectorId}`, body);
    },
    async deleteConnector(projectId: string, connectorId: string): Promise<void> {
      await t.del(`/api/v1/projects/${projectId}/connectors/${connectorId}`);
    },
    runConnectorNow(projectId: string, connectorId: string): Promise<{ run_id: string | null }> {
      return t.post<{ run_id: string | null }>(
        `/api/v1/projects/${projectId}/connectors/${connectorId}/run`,
        {},
      );
    },
    activateAgentConnector(projectId: string, connectorId: string): Promise<Connector> {
      return t.post<Connector>(
        `/api/v1/projects/${projectId}/connectors/${connectorId}/activate-agent`,
        {},
      );
    },
    /**
     * Pause a connector via the dedicated `POST /:id/pause` endpoint (not
     * `PATCH … {status:"paused"}`) so the backend can run side-effects — e.g.
     * cancel in-flight scheduled runs.
     */
    async pauseConnector(projectId: string, connectorId: string): Promise<void> {
      await t.post(`/api/v1/projects/${projectId}/connectors/${connectorId}/pause`, {});
    },
    /** Resume a paused connector. Counterpart to pauseConnector. */
    async resumeConnector(projectId: string, connectorId: string): Promise<void> {
      await t.post(`/api/v1/projects/${projectId}/connectors/${connectorId}/resume`, {});
    },
    getRepoIdentity(projectId: string): Promise<RepoIdentity> {
      return t.get<RepoIdentity>(`/api/v1/projects/${projectId}/access-point`);
    },
  };
}

// ── Pure connector helpers (transport-agnostic domain logic) ─────────────

/** CLI + Git remote + agent built-ins, in canonical order. */
export const BUILTIN_PROVIDERS = BUILTIN_ACCESS_PROVIDER_IDS;

export function normalizeConnector(connector: Connector): Connector {
  return { ...connector, provider: normalizeConnectorProvider(connector.provider) };
}

/**
 * Access surfaces are ongoing ways into a scope (CLI, Git remote, in-app agent,
 * MCP, sandbox, scheduled/manual integrations). Legacy GitHub / `import_once`
 * rows are one-shot imports and are excluded.
 */
export function isAccessSurfaceConnector(
  connector: Pick<Connector, "provider"> & Partial<Pick<Connector, "trigger">>,
): boolean {
  if (isAccessProviderHiddenInAccess(connector.provider)) return false;
  return connector.trigger?.type !== "import_once";
}

/** Drop legacy filesystem rows defensively (DB migration removes them). */
export function normalizeAccessSurfaceConnectors(connectors: readonly Connector[]): Connector[] {
  return connectors
    .map(normalizeConnector)
    .filter((connector) => connector.provider !== "filesystem");
}

export function sortConnectorsBuiltinFirst(connectors: readonly Connector[]): Connector[] {
  const order = (c: Connector) => {
    const rank = getAccessProviderSortRank(c.provider);
    return rank >= 100 ? 100 : rank;
  };
  return [...connectors].sort(
    (a, b) => order(a) - order(b) || a.created_at.localeCompare(b.created_at),
  );
}
