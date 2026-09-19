'use client';

import { useCallback, useState } from 'react';
import { StatusIndicator } from '@/components/ui/StatusDot';
import { createMcpEndpoint, type McpEndpoint } from '@/lib/mcpEndpointsApi';
import type { RepositoryView } from '@/lib/repoApi';
import { T } from '@/features/access/lib/tokens';
import { ProviderIcon } from '@/features/access/components/icons';

function mcpServerUrl(created: McpEndpoint): string {
  if (created.server_url) return created.server_url;
  const apiBase =
    typeof window !== 'undefined'
      ? (process.env.NEXT_PUBLIC_API_URL || window.location.origin)
      : '';
  return `${apiBase}/api/v1/mcp/proxy`;
}

export function McpConnectCard({
  scope,
  projectId,
  onCreated,
}: {
  readonly scope: RepositoryView;
  readonly projectId: string;
  readonly onCreated: () => Promise<unknown>;
}) {
  const [creating, setCreating] = useState(false);
  const [hovered, setHovered] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The created endpoint — held so we can show the ONE-TIME api_key + connect
  // info. The full key is only ever returned at create time, so capturing it
  // here is what makes the endpoint actually usable from an external client.
  const [created, setCreated] = useState<McpEndpoint | null>(null);

  const handleCreate = useCallback(async () => {
    if (creating) return;
    setCreating(true);
    setError(null);
    try {
      const endpoint = await createMcpEndpoint({
        project_id: projectId,
        path: scope.path,
        name: 'MCP Server',
        accesses: [{ path: scope.path, json_path: '', readonly: scope.max_mode !== 'rw' }],
      });
      // Defer the parent refresh until the user dismisses the connect panel:
      // onCreated() revalidates the connector list, which flips this card's
      // mount gate (!hasMcpMethod) in ScopeDetailPanel and would otherwise
      // unmount the panel showing the ONE-TIME api_key before it can be copied.
      setCreated(endpoint);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create MCP endpoint');
    } finally {
      setCreating(false);
    }
  }, [creating, projectId, scope.path, scope.max_mode]);

  const handleDismiss = useCallback(() => {
    setCreated(null);
    void onCreated();
  }, [onCreated]);

  if (created) {
    return <McpConnectInfo created={created} onDismiss={handleDismiss} />;
  }

  return (
    <div
      style={{
        position: 'relative',
        borderRadius: 8,
        border: `1px solid ${error ? 'color-mix(in srgb, var(--po-danger) 28%, var(--po-border-subtle) 72%)' : hovered ? 'var(--po-border-strong)' : T.cardBorder}`,
        background: T.bg,
        overflow: 'hidden',
        transition: `background 0.15s ${T.ease}, border-color 0.15s ${T.ease}`,
        marginBottom: 10,
      }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <div
        style={{
          minHeight: 84,
          minWidth: 0,
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 1fr) max-content',
          alignItems: 'center',
          gap: 14,
          padding: '14px 18px',
          boxSizing: 'border-box',
          background: hovered ? 'color-mix(in srgb, var(--po-panel) 78%, var(--po-control) 22%)' : 'transparent',
          transition: `background 0.15s ${T.ease}`,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 14, minWidth: 0 }}>
          <McpIconTile />
          <div style={{ minWidth: 0, display: 'flex', flexDirection: 'column', gap: 5 }}>
            <div style={{ minWidth: 0, display: 'flex', alignItems: 'center', gap: 7 }}>
              <span
                style={{
                  minWidth: 0,
                  display: 'inline-flex',
                  alignItems: 'center',
                  fontSize: 14,
                  lineHeight: '18px',
                  fontWeight: 500,
                  color: T.text1,
                  fontFamily: T.fontSans,
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                }}
              >
                MCP Server
              </span>
              <McpInlineStatus errored={!!error} />
            </div>
            <div
              style={{
                fontSize: 12,
                color: error ? 'var(--po-danger)' : T.text2,
                fontFamily: T.fontSans,
                lineHeight: '18px',
                fontWeight: 400,
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}
              title={error || 'Create a scoped Model Context Protocol endpoint for external AI clients.'}
            >
              {error || 'Create a scoped Model Context Protocol endpoint for external AI clients.'}
            </div>
          </div>
        </div>

        <div
          style={{
            display: 'flex',
            minWidth: 0,
            alignItems: 'flex-end',
            justifyContent: 'center',
            flexDirection: 'column',
          }}
        >
          <button
            type='button'
            disabled={creating}
            onClick={handleCreate}
            style={{
              height: 34,
              minWidth: 132,
              borderRadius: 7,
              border: `1px solid ${error ? 'color-mix(in srgb, var(--po-danger) 28%, var(--po-border-strong) 72%)' : hovered && !creating ? 'var(--po-border-strong)' : T.border}`,
              background: creating
                ? 'color-mix(in srgb, var(--po-control) 46%, var(--po-panel) 54%)'
                : hovered
                  ? 'color-mix(in srgb, var(--po-panel) 72%, var(--po-control) 28%)'
                  : 'transparent',
              color: error ? 'var(--po-danger)' : creating ? T.text3 : T.text2,
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 8,
              padding: '0 12px',
              fontSize: 12,
              lineHeight: '16px',
              fontWeight: 500,
              fontFamily: T.fontSans,
              cursor: creating ? 'wait' : 'pointer',
              opacity: creating ? 0.68 : 1,
              transition: `background 0.15s ${T.ease}, border-color 0.15s ${T.ease}, color 0.15s ${T.ease}`,
            }}
          >
            <span>{creating ? 'Creating endpoint' : error ? 'Retry' : 'Create endpoint'}</span>
          </button>
        </div>
      </div>
    </div>
  );
}

function McpIconTile() {
  return (
    <div
      style={{
        height: 34,
        width: 34,
        borderRadius: 7,
        background: 'color-mix(in srgb, var(--po-control) 76%, var(--po-canvas) 24%)',
        border: `1px solid ${T.cardBorder}`,
        color: T.text2,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        flexShrink: 0,
      }}
    >
      <ProviderIcon provider='mcp' size={17} variant='mono' />
    </div>
  );
}

function McpInlineStatus({ errored }: { readonly errored: boolean }) {
  return (
    <>
      <span aria-hidden style={{ color: T.text4, fontSize: 12, lineHeight: '16px' }}>·</span>
      <StatusIndicator
        status={errored ? 'error' : 'inactive'}
        label={errored ? 'Error' : 'Off'}
        style={{ flexShrink: 0 }}
      />
    </>
  );
}

// Shown right after create — surfaces the server URL + the ONE-TIME api_key so
// the user can wire up an external MCP client (ChatGPT / Claude Desktop). The
// full key is never returned again, so this is the only chance to copy it.
function McpConnectInfo({
  created,
  onDismiss,
}: {
  readonly created: McpEndpoint;
  readonly onDismiss: () => void;
}) {
  const url = mcpServerUrl(created);
  return (
    <div
      style={{
        borderRadius: 8,
        border: `1px solid ${T.cardBorder}`,
        background: T.bg,
        padding: 14,
        marginBottom: 10,
        display: 'flex',
        flexDirection: 'column',
        gap: 12,
        fontFamily: T.fontSans,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <McpIconTile />
        <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          <span style={{ fontSize: 14, fontWeight: 500, color: T.text1 }}>MCP endpoint ready</span>
          <span style={{ fontSize: 12, color: T.text2 }}>
            Connect an external AI client (ChatGPT, Claude Desktop, …) to this scope.
          </span>
        </div>
      </div>

      <div
        style={{
          fontSize: 11,
          lineHeight: 1.5,
          color: 'var(--po-warning)',
          background: 'color-mix(in srgb, var(--po-warning) 8%, transparent)',
          border: '1px solid color-mix(in srgb, var(--po-warning) 25%, transparent)',
          borderRadius: 6,
          padding: '7px 9px',
        }}
      >
        The API key is shown <strong>once</strong> — copy it now. You can’t see it again (regenerate to get a new one).
      </div>

      <CopyField label="Server URL" value={url} />
      <CopyField label="API key — send as  Authorization: Bearer <key>" value={created.api_key} />

      <p style={{ margin: 0, fontSize: 11, lineHeight: 1.6, color: T.text3 }}>
        In your MCP client, add a server with the <strong>Server URL</strong> above and send the
        key as a bearer token — set the <code style={{ fontFamily: T.fontMono }}>Authorization</code> header
        to <code style={{ fontFamily: T.fontMono }}>Bearer &lt;key&gt;</code>.
      </p>

      <button
        type="button"
        onClick={onDismiss}
        style={{
          alignSelf: 'flex-start',
          height: 30,
          padding: '0 14px',
          fontSize: 12,
          fontWeight: 600,
          fontFamily: T.fontSans,
          color: 'var(--po-text-inverse)',
          background: 'var(--po-text)',
          border: 'none',
          borderRadius: 999,
          cursor: 'pointer',
        }}
      >
        Done
      </button>
    </div>
  );
}

function CopyField({ label, value }: { readonly label: string; readonly value: string }) {
  const [copied, setCopied] = useState(false);
  const copy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard may be blocked in non-HTTPS/iframe contexts — the value is selectable anyway
    }
  }, [value]);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <span style={{ fontSize: 11, color: T.text3 }}>{label}</span>
      <div style={{ display: 'flex', alignItems: 'stretch', gap: 6 }}>
        <code
          style={{
            flex: 1,
            minWidth: 0,
            overflowX: 'auto',
            whiteSpace: 'nowrap',
            padding: '7px 9px',
            fontFamily: T.fontMono,
            fontSize: 12,
            color: T.text1,
            background: 'var(--po-control)',
            border: `1px solid ${T.border}`,
            borderRadius: 6,
          }}
        >
          {value}
        </code>
        <button
          type="button"
          onClick={copy}
          style={{
            flexShrink: 0,
            height: 'auto',
            padding: '0 10px',
            fontSize: 12,
            fontWeight: 600,
            fontFamily: T.fontSans,
            color: T.text2,
            background: 'transparent',
            border: `1px solid ${T.border}`,
            borderRadius: 6,
            cursor: 'pointer',
          }}
        >
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
    </div>
  );
}
