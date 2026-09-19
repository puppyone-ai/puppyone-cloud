'use client';

import { CommandBlock } from '@/features/files/components/access-points/connect-methods/CommandBlock';
import { apiRequest } from '@/lib/apiClient';
import { isSameCanonicalGitUrl } from '@/lib/gitRemote';
import {
  sameRepositoryTarget,
  type RepositoryTarget,
} from '@puppyone/cloud-core';
import { useCallback, useEffect, useRef, useState } from 'react';

const GIT_CREDENTIAL_PATTERN = /^pwg_[A-Za-z0-9_-]{43}$/;

type GitCredentialIntent = {
  readonly operationKey: string;
  readonly credential: string;
  readonly mode: 'r' | 'rw';
  readonly target: RepositoryTarget;
};

type GitCredentialIssueResult = {
  readonly id: string;
  readonly mode: 'r' | 'rw';
  readonly remote: {
    readonly url: string;
    readonly target: RepositoryTarget;
    readonly username: string;
  };
};

type IssuedGitCredential = {
  id: string;
  credential: string;
  git_url: string;
  git_username: string;
  grant_mode: 'r' | 'rw';
};

function generateGitCredential(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  const base64 = btoa(String.fromCharCode(...bytes))
    .replaceAll('+', '-')
    .replaceAll('/', '_')
    .replace(/=+$/, '');
  return `pwg_${base64}`;
}

function createIntent(target: RepositoryTarget, mode: 'r' | 'rw'): GitCredentialIntent {
  return {
    operationKey: crypto.randomUUID(),
    credential: generateGitCredential(),
    mode,
    target,
  };
}

export function GitCredentialIssuePanel({
  connectorId,
  gitUrl,
  scopeMode,
  target,
}: {
  readonly connectorId: string;
  readonly gitUrl: string;
  readonly scopeMode: 'r' | 'rw';
  readonly target: RepositoryTarget;
}) {
  const targetIdentity = target.kind === 'project_root'
    ? `project:${target.project_id}`
    : `scope:${target.project_id}:${target.scope_id}`;
  const [issued, setIssued] = useState<IssuedGitCredential | null>(null);
  const [grantMode, setGrantMode] = useState<'r' | 'rw'>(scopeMode);
  const [issuing, setIssuing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pendingIntent = useRef<GitCredentialIntent | null>(null);

  useEffect(() => {
    pendingIntent.current = null;
    setIssued(null);
    setGrantMode(scopeMode);
    setError(null);
  }, [connectorId, gitUrl, scopeMode, targetIdentity]);

  const issue = useCallback(async () => {
    if (!connectorId || issuing) return;
    if (issued) {
      pendingIntent.current = null;
      setIssued(null);
    }
    const intent = pendingIntent.current ?? createIntent(target, grantMode);
    pendingIntent.current = intent;
    setIssuing(true);
    setError(null);
    try {
      const result = await apiRequest<GitCredentialIssueResult>(
        `/api/v1/projects/${encodeURIComponent(target.project_id)}/git-credentials`,
        {
          method: 'POST',
          headers: { 'Idempotency-Key': intent.operationKey },
          body: JSON.stringify({
            target: intent.target,
            mode: intent.mode,
            credential: intent.credential,
          }),
        },
      );
      if (
        !GIT_CREDENTIAL_PATTERN.test(intent.credential)
        || result.remote.username !== 'x-puppyone-token'
        || result.mode !== intent.mode
        || !sameRepositoryTarget(intent.target, result.remote.target)
        || !isSameCanonicalGitUrl(gitUrl, result.remote.url)
      ) {
        throw new Error('Cloud returned an invalid Git credential contract');
      }
      setIssued({
        id: result.id,
        credential: intent.credential,
        git_url: result.remote.url,
        git_username: result.remote.username,
        grant_mode: result.mode,
      });
      pendingIntent.current = null;
    } catch (caught) {
      setIssued(null);
      setError(caught instanceof Error ? caught.message : 'Unable to issue Git credential');
    } finally {
      setIssuing(false);
    }
  }, [connectorId, gitUrl, grantMode, issued, issuing, target]);

  const locator = issued?.git_url || gitUrl;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
        <select
          aria-label='Git credential permission'
          value={grantMode}
          disabled={issuing}
          onChange={(event) => {
            setGrantMode(event.target.value === 'r' ? 'r' : 'rw');
            pendingIntent.current = null;
            setIssued(null);
            setError(null);
          }}
          style={{
            minHeight: 30,
            padding: '0 8px',
            border: '1px solid var(--po-border)',
            borderRadius: 6,
            background: 'var(--po-panel)',
            color: 'var(--po-text)',
            fontSize: 12,
          }}
        >
          <option value='rw' disabled={scopeMode !== 'rw'}>Read &amp; write</option>
          <option value='r'>Read only</option>
        </select>
        <button
          type='button'
          onClick={() => void issue()}
          disabled={!connectorId || issuing}
          style={{
            minHeight: 30,
            padding: '0 10px',
            border: '1px solid var(--po-border)',
            borderRadius: 6,
            background: 'var(--po-panel)',
            color: 'var(--po-text)',
            cursor: !connectorId || issuing ? 'not-allowed' : 'pointer',
            fontSize: 12,
            opacity: connectorId ? 1 : 0.55,
          }}
        >
          {issuing
            ? 'Generating…'
            : issued
              ? 'Generate another credential'
              : error
                ? 'Retry generating'
                : 'Generate Git credential'}
        </button>
      </div>
      {issued ? (
        <>
          <CommandBlock lines={[
            `Git URL: ${locator}`,
            `Permission: ${issued.grant_mode === 'rw' ? 'read & write' : 'read only'}`,
            `Username: ${issued.git_username}`,
            `Password: ${issued.credential}`,
          ]} />
          <span style={{ color: 'var(--po-text-subtle)', fontSize: 11 }}>
            Save this password now. It is shown only once and never belongs in the Git URL.
            Generating another credential does not revoke existing credentials.
          </span>
        </>
      ) : null}
      {error ? <span style={{ color: 'var(--po-danger)', fontSize: 11 }}>{error}</span> : null}
    </div>
  );
}
