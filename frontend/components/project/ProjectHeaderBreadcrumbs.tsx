'use client';

import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { WorkspaceLink as Link } from '@/features/workspace/navigation';
import { Settings } from 'lucide-react';
import { CHROME_LABEL_TYPOGRAPHY } from '@/lib/uiTypography';
import styles from './ProjectHeaderBreadcrumbs.module.css';

export type BreadcrumbSegment = {
  label: ReactNode;
  href?: string;
};

function labelTitle(label: ReactNode) {
  return typeof label === 'string' || typeof label === 'number' ? String(label) : undefined;
}

function Segment({ segment, current = false, onNavigate }: {
  segment: BreadcrumbSegment;
  current?: boolean;
  onNavigate?: () => void;
}) {
  const props = { className: styles.label, title: labelTitle(segment.label) };
  if (!segment.href || current) {
    return <span {...props} aria-current={current ? 'page' : undefined}>{segment.label}</span>;
  }
  return (
    <Link {...props} href={segment.href} onClick={event => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      onNavigate?.();
    }}>
      {segment.label}
    </Link>
  );
}

export function ProjectHeaderBreadcrumbs({ pathSegments, onOpenSettings }: {
  pathSegments: BreadcrumbSegment[];
  onOpenSettings?: () => void;
}) {
  const rootRef = useRef<HTMLElement>(null);
  const measureRef = useRef<HTMLDivElement>(null);
  const disclosureRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuId = useId();
  const [fullWidth, setFullWidth] = useState<number>();
  const [collapsed, setCollapsed] = useState(false);
  const [open, setOpen] = useState(false);
  const ancestors = pathSegments.slice(1, -1);

  useEffect(() => {
    const root = rootRef.current;
    const measure = measureRef.current;
    if (!root || !measure) return;
    // Keep the preferred width independent of the collapsed rendering. This
    // lets the full path return as soon as the surrounding chrome has room.
    const update = () => {
      const naturalWidth = measure.getBoundingClientRect().width;
      const availableWidth = root.getBoundingClientRect().width;
      if (!naturalWidth) return;
      setFullWidth(naturalWidth);
      setCollapsed(naturalWidth > availableWidth + 1);
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(root);
    observer.observe(measure);
    return () => observer.disconnect();
  }, [pathSegments, onOpenSettings]);

  useEffect(() => { setOpen(false); }, [pathSegments, collapsed]);

  useEffect(() => {
    if (!open) return;
    menuRef.current?.querySelector<HTMLAnchorElement>('a')?.focus();
    const dismiss = (event: PointerEvent) => {
      if (!disclosureRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', dismiss);
    return () => document.removeEventListener('pointerdown', dismiss);
  }, [open]);

  if (!pathSegments.length) return null;

  return (
    <nav
      ref={rootRef}
      aria-label='Project path'
      className={styles.path}
      data-collapsed={collapsed || undefined}
      style={{ ...CHROME_LABEL_TYPOGRAPHY, width: fullWidth ?? 'max-content' }}
    >
      <div ref={measureRef} className={styles.measure} aria-hidden='true'>
        {pathSegments.map((segment, index) => (
          <span key={index} className={styles.measuredSegment}>
            {index > 0 && <span className={styles.separator}>/</span>}
            {segment.label}
            {index === 0 && onOpenSettings && <span className={styles.settingsSpace} />}
          </span>
        ))}
      </div>

      <div className={styles.project} data-has-tail={pathSegments.length > 1 || undefined}>
        <Segment segment={pathSegments[0]} current={pathSegments.length === 1} />
        {onOpenSettings && (
          <button type='button' onClick={onOpenSettings} className={styles.settings} title='Project settings' aria-label='Project settings' aria-haspopup='dialog'>
            <Settings size={15} strokeWidth={1.8} aria-hidden='true' />
          </button>
        )}
      </div>

      {collapsed && ancestors.length > 0 ? (
        <div ref={disclosureRef} className={styles.disclosure} onKeyDown={event => {
          if (event.key === 'Escape') {
            setOpen(false);
            triggerRef.current?.focus();
          }
        }} onBlur={event => {
          if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
        }}>
          <span className={styles.separator} aria-hidden='true'>/</span>
          <button ref={triggerRef} type='button' className={styles.more} aria-label='Show parent folders' aria-expanded={open} aria-controls={menuId} onClick={() => setOpen(value => !value)}>…</button>
          {open && (
            <div ref={menuRef} id={menuId} className={styles.menu} aria-label='Parent folders'>
              {ancestors.map((segment, index) => <Segment key={index} segment={segment} onNavigate={() => setOpen(false)} />)}
            </div>
          )}
        </div>
      ) : ancestors.map((segment, index) => (
        <div key={index} className={styles.ancestor}>
          <span className={styles.separator} aria-hidden='true'>/</span>
          <Segment segment={segment} />
        </div>
      ))}

      {pathSegments.length > 1 && (
        <div className={styles.current}>
          <span className={styles.separator} aria-hidden='true'>/</span>
          <Segment segment={pathSegments[pathSegments.length - 1]} current />
        </div>
      )}
    </nav>
  );
}
