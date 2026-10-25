import { apiRawRequest } from '@/lib/apiClient';
import { API_BASE_URL } from '@/config/api';

export interface Registration { id: string; name: string; state: string }
export interface AccessRecord {
  id: string; state: string; registration_id?: string; registration_name?: string;
  applicant_user_id?: string; target_kind?: string; workbench_id?: string | null;
  capabilities?: string[]; channels?: string[]; channel?: string; grant_id?: string | null;
  expires_at?: string | null; max_grant_seconds?: number | null; created_at: string;
}
export interface Invitation {
  id: string; secret: string; target_kind: 'workspace' | 'project_root';
  workbench_id?: string; capabilities: string[]; channels: string[];
  expires_at: string; max_approvals: number;
}
export const projectAccess = (project: string) => `/api/v1/projects/${encodeURIComponent(project)}/access`;
export const accessRequest = <T>(method: string, endpoint: string, body?: unknown, signal?: AbortSignal) =>
  apiRawRequest<T>(endpoint, { method, body: body === undefined ? undefined : JSON.stringify(body), signal, redirect: 'error' });

export async function loadAccess(project: string, signal?: AbortSignal) {
  const [invitations, requests, grants, sessions] = await Promise.all(
    ['invitations', 'requests', 'grants', 'sessions'].map(collection =>
      accessRequest<AccessRecord[]>('GET', `${projectAccess(project)}/${collection}`, undefined, signal)),
  );
  return { invitations, requests, grants, sessions };
}

export function newInvitation(workbench?: string, readonly = true): Invitation {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  const secret = 'pai_' + btoa(String.fromCharCode(...bytes)).replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', '');
  return { id: crypto.randomUUID(), secret, target_kind: workbench ? 'workspace' : 'project_root',
    ...(workbench ? { workbench_id: workbench } : {}),
    capabilities: workbench ? ['workspace.read', 'workspace.write', 'workspace.shell']
      : readonly ? ['repository.read'] : ['repository.read', 'repository.write'],
    channels: workbench ? ['ssh'] : ['git', 'cli'],
    expires_at: new Date(Date.now() + 7 * 86400_000).toISOString(), max_approvals: 1 };
}

export function invitationLink(secret: string, origin: string): string {
  const url = new URL('/agent-access', origin);
  url.hash = new URLSearchParams({ api: new URL(API_BASE_URL).origin, invitation: secret }).toString();
  return url.href;
}

export function readInvitation(fragment: string): string {
  const parts = new URLSearchParams(fragment.replace(/^#/, ''));
  if (parts.get('api') !== new URL(API_BASE_URL).origin
      || !/^pai_[A-Za-z0-9_-]{43,128}$/.test(parts.get('invitation') ?? '')) {
    throw new Error('This invitation belongs to another service or is invalid. Open the link supplied by the host.');
  }
  return parts.get('invitation')!;
}
