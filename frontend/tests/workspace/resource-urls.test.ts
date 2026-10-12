import { describe, expect, it } from 'vitest';
import { isWorkspaceResourcePath, workspaceResourcePath, workspaceResourceUrl, workspaceEntryPath } from '@puppyone/cloud-core';
import { safeNext, readLoginIntent, oauthCallbackUrl } from '@/features/auth/login-intent';

const id = '019a1111-2222-7333-8444-555555555555';

describe('canonical resource URLs', () => {
  it.each(['', 'docs', 'docs/介绍 #?%.md', 'space name/100% done.txt', '.hidden', 'literal%2F%2e%2e.md'])('round trips %s without decoding twice', path => {
    const url = new URL(workspaceResourceUrl('https://web.example.test', id, path));
    expect(url.pathname.slice(id.length + 2).split('/').map(decodeURIComponent).join('/')).toBe(path);
    expect(url.search).toBe('');
    expect(url.hash).toBe('');
  });
  it('distinguishes directory/file spelling and binds workspace identity', () => {
    expect(workspaceResourcePath(id)).toBe(`/${id}/`);
    expect(workspaceResourcePath(id, 'docs', true)).toBe(`/${id}/docs/`);
    expect(workspaceResourcePath(id, 'docs')).toBe(`/${id}/docs`);
    expect(workspaceResourceUrl('https://web.test/api/v1', id)).toBe(`https://web.test/${id}/`);
  });
  it.each(['../secret', 'a/../b', 'a//b', '/abs', 'a/', 'a\\b', 'a\x00b'])('rejects ambiguous path %s', path => {
    expect(() => workspaceResourcePath(id, path)).toThrow();
  });
  it.each(['/login', '/api/backend/x', '/home', '/share/token', '/favicon.ico', '/not-a-project/docs'])('leaves existing route %s alone', path => {
    expect(isWorkspaceResourcePath(path)).toBe(false);
  });
  it('protects workspace image URLs as well as text URLs', () => {
    expect(isWorkspaceResourcePath(`/${id}/a.png`)).toBe(true);
    expect(isWorkspaceResourcePath(`/${id}/`)).toBe(true);
  });
  it('preserves an encoded resource through email/OAuth return paths', () => {
    const path = workspaceResourcePath(id, '文档/space #?%.md');
    expect(safeNext(path)).toBe(path);
    const intent = readLoginIntent(new URL(`https://web.test/login?${new URLSearchParams({ redirect: path })}`));
    expect(new URL(oauthCallbackUrl('https://web.test/auth/callback', intent)).searchParams.get('next')).toBe(path);
  });
  it('gives non-UTF-8 Git names distinct lossless URLs, without crashing the directory', () => {
    const raw = btoa('raw-\xff');
    const url = new URL(workspaceEntryPath(id, { path: 'raw-\\xff', type: 'file', path_bytes_b64: raw }), 'https://web.test');
    expect(url.pathname).toBe(`/${id}/`);
    expect(url.searchParams.get('path_bytes_b64')).toBe(raw);
    expect(workspaceEntryPath(id, { path: 'plain.md', type: 'markdown', path_bytes_b64: btoa('plain.md') })).toBe(`/${id}/plain.md`);
  });
});
