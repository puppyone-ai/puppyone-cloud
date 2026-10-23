const request = { id: 'request-1', registration_id: 'registration-1', registration_name: 'Research agent',
  applicant_user_id: 'd71ea02d-42b8-4cba-aa60-c14a7fba41c6', state: 'pending', target_kind: 'workspace',
  channels: ['ssh'], capabilities: ['workspace.read', 'workspace.write', 'workspace.shell'], created_at: new Date().toISOString() };
const grants: object[] = [];
export const useAuth = () => ({ userId: 'fixture-owner', session: { access_token: 'fixture-only' } });
export const workspaceStatus = async () => ({ workspace: { id: 'fixture-workspace', state: 'running' } });
export async function apiRawRequest(path: string, options: RequestInit = {}) {
  if (options.method === 'POST' && path.endsWith('/decision')) {
    request.state = 'approved'; grants.push({ ...request, id: 'grant-1', state: 'active' }); return grants[0];
  }
  if (options.method === 'PATCH') { grants.length = 0; return {}; }
  if (options.method === 'POST') return {};
  if (path.endsWith('/requests')) return [request];
  if (path.endsWith('/grants')) return grants;
  return [];
}
