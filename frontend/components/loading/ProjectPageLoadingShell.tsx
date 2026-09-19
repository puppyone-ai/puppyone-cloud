'use client';

import type { CSSProperties } from 'react';
import { PageLoading } from './PageLoading';

export interface HeaderedPageLoadingShellProps {
  /**
   * Optional text for pages whose final header label is known during
   * loading. When omitted, the shell still reserves the 46px header
   * band without showing misleading copy.
   */
  title?: string;
}

/**
 * Loading shell for route panes with a fixed 46px top header.
 *
 * Route-level Suspense fallbacks and data-loading branches must
 * reserve that same band as the final page chrome, or
 * the centered loader jumps when the real page header mounts.
 */
export function HeaderedPageLoadingShell({ title }: HeaderedPageLoadingShellProps) {
  return (
    <div style={shellStyle}>
      <div style={headerStyle}>
        {title ? <span style={titleStyle}>{title}</span> : null}
      </div>
      <div style={bodyStyle}>
        <PageLoading variant="fill" />
      </div>
    </div>
  );
}

export type ProjectPageLoadingShellProps = HeaderedPageLoadingShellProps;

export function ProjectPageLoadingShell(props: ProjectPageLoadingShellProps) {
  return <PageLoading variant="fill" label={props.title} />;
}

const shellStyle: CSSProperties = {
  width: '100%',
  height: '100%',
  display: 'flex',
  flexDirection: 'column',
  overflow: 'hidden',
  background: 'var(--po-canvas)',
};

const headerStyle: CSSProperties = {
  height: 46,
  minHeight: 46,
  flexShrink: 0,
  display: 'flex',
  alignItems: 'center',
  padding: '0 16px',
  borderBottom: '1px solid var(--po-divider)',
  background: 'var(--po-canvas)',
};

const titleStyle: CSSProperties = {
  fontSize: 13,
  fontWeight: 500,
  color: 'var(--po-text)',
};

const bodyStyle: CSSProperties = {
  flex: 1,
  minHeight: 0,
};
