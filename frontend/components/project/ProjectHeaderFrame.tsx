'use client';

import type { CSSProperties, ReactNode, Ref } from 'react';
import { APP_Z_INDEX } from '@/lib/zIndex';
import styles from '../ProjectsHeader.module.css';

/** Shared project chrome. Routes supply identity and permitted actions. */
export function ProjectHeaderFrame({ children, actions, headerRef }: {
  children: ReactNode; actions?: ReactNode; headerRef?: Ref<HTMLElement>;
}) {
  return <header ref={headerRef} data-workspace-header style={headerStyle} className={styles.header}>
    <div style={headerLeftStyle} className={styles.left}>{children}</div>
    {actions && <div style={headerActionStyle} className={styles.actions}>{actions}</div>}
  </header>;
}

const headerStyle: CSSProperties = {
  height: 'var(--project-header-height, 46px)',
  width: '100%',
  minWidth: 0,
  minHeight: 'var(--project-header-height, 46px)',
  maxHeight: 'var(--project-header-height, 46px)',
  boxSizing: 'border-box',
  flex: '0 0 auto',
  paddingLeft: 'var(--project-header-padding, 16px)',
  paddingRight: 'var(--project-header-padding, 16px)',
  display: 'flex',
  flexWrap: 'nowrap',
  alignItems: 'center',
  justifyContent: 'space-between',
  // Persistent workspace chrome stays distinct from the document surface in
  // both themes. Reuse the Desktop-aligned header token, not a local gray.
  background: 'var(--po-header)',
  borderBottom: '1px solid var(--po-divider)',
  position: 'relative',
  zIndex: APP_Z_INDEX.chrome,
  overflow: 'visible',
};

const headerLeftStyle: CSSProperties = {
  flex: '1 1 auto',
  display: 'flex',
  alignItems: 'center',
  minWidth: 0,
  overflow: 'visible',
};

const headerActionStyle: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'flex-end',
  flexShrink: 0,
  marginLeft: 'auto',
  position: 'relative',
  zIndex: APP_Z_INDEX.chromeRaised,
};
