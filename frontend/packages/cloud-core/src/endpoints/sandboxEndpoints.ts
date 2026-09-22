import type { CloudTransport } from "../transport";

export interface SandboxMountPermissions {
  read: boolean;
  write: boolean;
  exec: boolean;
}

export interface SandboxMount {
  path: string;
  mount_path: string;
  permissions: SandboxMountPermissions;
}

export interface SandboxResourceLimits {
  memory_mb: number;
  cpu_shares: number;
}

export type SandboxRuntime = "alpine" | "python" | "node";

export interface SandboxEndpoint {
  id: string;
  project_id: string;
  path: string | null;
  name: string;
  description: string | null;
  access_key: string;
  mounts: SandboxMount[];
  runtime: SandboxRuntime;
  timeout_seconds: number;
  resource_limits: SandboxResourceLimits;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface CreateSandboxEndpointParams {
  project_id: string;
  name?: string;
  path?: string;
  description?: string;
  mounts?: { path: string; mount_path?: string; permissions?: Partial<SandboxMountPermissions> }[];
  runtime?: SandboxRuntime;
  timeout_seconds?: number;
  resource_limits?: Partial<SandboxResourceLimits>;
}

export type UpdateSandboxEndpointParams = Partial<{
  name: string;
  description: string;
  path: string;
  status: string;
  mounts: { path: string; mount_path?: string; permissions?: Partial<SandboxMountPermissions> }[];
  runtime: SandboxRuntime;
  timeout_seconds: number;
  resource_limits: Partial<SandboxResourceLimits>;
}>;

/** Bind the sandbox-endpoint API to a platform transport (ISSUE-022). */
export function createSandboxEndpointsApi(t: CloudTransport) {
  return {
    listSandboxEndpoints(projectId: string): Promise<SandboxEndpoint[]> {
      return t.get<SandboxEndpoint[]>(`/api/v1/sandbox-endpoints?project_id=${projectId}`);
    },
    getSandboxEndpoint(id: string): Promise<SandboxEndpoint> {
      return t.get<SandboxEndpoint>(`/api/v1/sandbox-endpoints/${id}`);
    },
    async getSandboxEndpointByPath(path: string): Promise<SandboxEndpoint | null> {
      try {
        return await t.get<SandboxEndpoint>(`/api/v1/sandbox-endpoints/by-path/${path}`);
      } catch {
        return null;
      }
    },
    createSandboxEndpoint(params: CreateSandboxEndpointParams): Promise<SandboxEndpoint> {
      return t.post<SandboxEndpoint>("/api/v1/sandbox-endpoints", params);
    },
    updateSandboxEndpoint(id: string, params: UpdateSandboxEndpointParams): Promise<SandboxEndpoint> {
      return t.put<SandboxEndpoint>(`/api/v1/sandbox-endpoints/${id}`, params);
    },
    async deleteSandboxEndpoint(id: string): Promise<void> {
      await t.del(`/api/v1/sandbox-endpoints/${id}`);
    },
    regenerateSandboxEndpointKey(id: string): Promise<SandboxEndpoint> {
      return t.post<SandboxEndpoint>(`/api/v1/sandbox-endpoints/${id}/regenerate-key`, {});
    },
  };
}
