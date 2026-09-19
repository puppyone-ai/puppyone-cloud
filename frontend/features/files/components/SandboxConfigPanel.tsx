'use client';

import { PageLoading } from '@/components/loading';
import { StatusDot } from '@/components/ui/StatusDot';
import { PanelShell } from '@/features/files/components/PanelShell';
import React from 'react';

interface SandboxMount {
  path: string;
  mount_path: string;
  permissions?: { read?: boolean; write?: boolean; exec?: boolean };
}

interface SandboxEndpointData {
  id: string;
  name: string;
  access_key: string;
  status: string;
  runtime: string;
  timeout_seconds: number;
  resource_limits?: { memory_mb?: number; cpu_shares?: number };
  mounts: SandboxMount[];
}

interface SandboxConfigPanelProps {
  endpoint: SandboxEndpointData | null | undefined;
  onClose: () => void;
  onBack?: () => void;
}

const SandboxIcon = (
  <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="var(--po-warning)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="4 17 10 11 4 5" /><line x1="12" y1="19" x2="20" y2="19" />
  </svg>
);

const FolderIcon = (
  <img src="/icons/folder.svg" alt="Folder" width={36} height={36} style={{ display: 'block' }} />
);

function ConnectionArrow() {
  return (
    <svg width="48" height="16" viewBox="0 0 48 16" fill="none" stroke="var(--po-success)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2 5h44M6 2L2 5l4 3" />
      <path d="M42 8l4 3-4 3M2 11h44" />
    </svg>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--po-text-subtle)', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 6 }}>
      {children}
    </div>
  );
}

function CodeBlock({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ fontSize: 12, color: 'var(--po-text-muted)', background: 'var(--po-control)', border: '1px solid var(--po-border)', borderRadius: 6, padding: '8px 10px', wordBreak: 'break-all', fontFamily: 'var(--po-font-sans)' }}>
      {children}
    </div>
  );
}

export function SandboxConfigPanel({ endpoint, onClose, onBack }: SandboxConfigPanelProps) {
  if (!endpoint) {
    return (
      <PanelShell title="Sandbox" icon={SandboxIcon} onClose={onClose} onBack={onBack}>
        <PageLoading variant="fill" />
      </PanelShell>
    );
  }

  // The /api/v1/sandbox-endpoints/* path is served by the backend, so
  // the URL we hand to external callers (Claude / Cursor etc.) must
  // point at the backend host, not the Next.js frontend origin.
  // NEXT_PUBLIC_API_URL is the canonical backend URL set at build time.
  const apiBase = typeof window !== 'undefined'
    ? (process.env.NEXT_PUBLIC_API_URL || window.location.origin)
    : '';
  const execUrl = `${apiBase}/api/v1/sandbox-endpoints/${endpoint.id}/exec`;
  const targetLabel = endpoint.mounts.length > 0
    ? endpoint.mounts[0].path
    : 'Workspace';

  return (
    <PanelShell title={endpoint.name} icon={SandboxIcon} onClose={onClose} onBack={onBack}>
      <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 16 }}>
        {/* Connection visualization - aligned with Sync/MCP detail panels */}
        <div style={{ borderRadius: 10, padding: '16px 12px 12px', display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, width: 88 }}>
              <div style={{
                width: 36, height: 36, borderRadius: 8,
                background: 'var(--po-panel-raised)', border: '1px solid var(--po-border)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>
                <svg width={22} height={22} viewBox="0 0 24 24" fill="none" stroke="var(--po-warning)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="4 17 10 11 4 5" /><line x1="12" y1="19" x2="20" y2="19" />
                </svg>
              </div>
              <div style={{ fontSize: 11, fontWeight: 500, color: 'var(--po-text-muted)', textAlign: 'center' }}>
                Sandbox
              </div>
            </div>

            <div style={{ flex: 1, display: 'flex', justifyContent: 'center' }}>
              <ConnectionArrow />
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, width: 88 }}>
              {FolderIcon}
              <div style={{
                fontSize: 11, fontWeight: 500, color: 'var(--po-text-muted)', textAlign: 'center',
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 88,
              }}>
                {targetLabel}
              </div>
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <StatusDot tone={endpoint.status === 'active' ? 'success' : 'warning'} />
            <span style={{ fontSize: 11, color: 'var(--po-text-muted)' }}>{endpoint.status}</span>
          </div>
        </div>

        <div>
          <SectionLabel>Runtime</SectionLabel>
          <span style={{ fontSize: 13, color: 'var(--po-text)' }}>{endpoint.runtime}</span>
        </div>
        <div>
          <SectionLabel>Access Key</SectionLabel>
          <CodeBlock>{endpoint.access_key}</CodeBlock>
        </div>
        <div>
          <SectionLabel>Usage</SectionLabel>
          <pre style={{ fontSize: 11, color: 'var(--po-text-muted)', background: 'var(--po-control)', border: '1px solid var(--po-border)', borderRadius: 6, padding: '8px 10px', margin: 0, whiteSpace: 'pre-wrap', fontFamily: 'var(--po-font-sans)' }}>
{`curl -X POST ${execUrl} \\
  -H "X-Access-Key: ${endpoint.access_key}" \\
  -H "Content-Type: application/json" \\
  -d '{"command": "ls /workspace"}'`}
          </pre>
        </div>
        <div>
          <SectionLabel>Resource Limits</SectionLabel>
          <span style={{ fontSize: 12, color: 'var(--po-text-muted)' }}>
            {endpoint.resource_limits?.memory_mb ?? 128}MB RAM · {endpoint.resource_limits?.cpu_shares ?? 0.5} CPU · {endpoint.timeout_seconds}s timeout
          </span>
        </div>
        {endpoint.mounts.length > 0 && (
          <div>
            <SectionLabel>Mounts ({endpoint.mounts.length})</SectionLabel>
            {endpoint.mounts.map((m, i) => (
              <div key={i} style={{ fontSize: 12, color: 'var(--po-text-muted)', padding: '4px 0' }}>
                {m.mount_path} → {m.path} ({m.permissions?.write ? 'read-write' : 'read-only'})
              </div>
            ))}
          </div>
        )}
      </div>
    </PanelShell>
  );
}
