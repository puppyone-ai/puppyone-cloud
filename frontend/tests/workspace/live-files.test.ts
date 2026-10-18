import {beforeEach, expect, it, vi} from 'vitest';
import {apiRequest} from '@/lib/apiClient';
import {createWorkspaceFiles} from '@/lib/workspaceFiles';
import {dataDirectoryCacheKeys} from '@/lib/dataTreeCache';
vi.mock('@/lib/apiClient', () => ({apiRequest: vi.fn()}));
const request = vi.mocked(apiRequest);
const workspace = {id: 'workspace', generation: 'generation-one', project_id: 'p', state: 'running', target_ref: 'refs/heads/workspaces/me'};
beforeEach(() => request.mockReset());

it('reads saved native content and writes without any Git content API', async () => {
  const files = createWorkspaceFiles(workspace);
  request.mockResolvedValueOnce({name:'a.md', kind:'file', content_base64:btoa('agent saved'), revision:'a'.repeat(64)});
  expect((await files.readFile('p','a.md')).content_text).toBe('agent saved');
  request.mockResolvedValueOnce({revision:'b'.repeat(64)});
  expect((await files.writeFile('p','a.md','human saved','markdown')).commit_id).toBe('');
  expect(request.mock.calls.every(([path]) => path.includes('/workbench/file?') && path.includes('generation=generation-one'))).toBe(true);
  expect(JSON.parse(request.mock.calls[1][1]!.body as string).expected_revision).toBe('a'.repeat(64));
});

it('never falls back to published files on expired generation, pause, or authorization errors', async () => {
  const files = createWorkspaceFiles(workspace);
  request.mockRejectedValueOnce(new Error('generation changed'));
  await expect(files.readFile('p','a.md')).rejects.toThrow('generation changed');
  expect(request).toHaveBeenCalledTimes(1);
  const paused = createWorkspaceFiles({...workspace, state:'paused'});
  await expect(paused.readFile('p','a.md')).rejects.toThrow('Resume');
  await expect(files.readFile('another-project','a.md')).rejects.toThrow('Project');
  expect(request).toHaveBeenCalledTimes(1);
});

it('preview refresh cannot change the editor save revision', async () => {
  const files = createWorkspaceFiles(workspace);
  request.mockResolvedValueOnce({name:'a.md',kind:'file',content_base64:btoa('base'),revision:'a'.repeat(64)});
  await files.readFile('p','a.md');
  request.mockResolvedValueOnce({name:'a.md',kind:'file',content_base64:btoa('agent'),revision:'b'.repeat(64)});
  await files.fetchRawBlob('p','a.md');
  request.mockResolvedValueOnce({revision:'c'.repeat(64)});
  await files.writeFile('p','a.md','human','markdown');
  expect(JSON.parse(request.mock.calls[2][1]!.body as string).expected_revision).toBe('a'.repeat(64));
});

it('optimistic workspace mutations cannot update published or another generation cache', () => {
  const old = dataDirectoryCacheKeys('p','folder','me:workspace:old');
  const fresh = dataDirectoryCacheKeys('p','folder','me:workspace:new');
  const published = dataDirectoryCacheKeys('p','folder');
  expect(old).not.toEqual(fresh); expect(old).not.toEqual(published);
  expect(published[0]).toEqual(['tree','p','folder']);
});
