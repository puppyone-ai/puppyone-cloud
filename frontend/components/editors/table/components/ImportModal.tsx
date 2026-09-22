'use client';

import { useWorkspaceRouter } from '@/features/workspace/navigation';

import React, {
  useState,
  useCallback,
  CSSProperties,
} from 'react';
import { APP_Z_INDEX } from '@/lib/zIndex';
import { ModalPortal } from '@/components/ui/ModalPortal';
import { ActionButton } from '@/components/ui/ActionButton';
import { Dots } from '@/components/loading';
import {
  submitImport,
  supportsCrawlOptions,
  type CrawlOptions,
} from '../../../../lib/importApi';
import CrawlOptionsPanel from '../../../CrawlOptionsPanel';

interface ImportModalProps {
  visible: boolean;
  targetPath?: string; // Optional - only for path-level import
  currentValue?: any; // Optional - only for path-level import
  tableId?: number; // Optional - only for existing table import
  tableName?: string; // Optional - for new table creation
  projectId: string;
  mode?: 'create_table' | 'import_to_table'; // New: specify import mode
  initialUrl?: string; // Optional - auto-parse this URL on mount
  initialCrawlOptions?: CrawlOptions; // Optional - initial crawl options
  onClose: () => void;
  onSuccess: (newData: any) => void;
}

type ImportMode = 'add_to_existing' | 'replace_all' | 'keep_separate';

const styles = {
  overlay: {
    position: 'fixed',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    background: 'var(--po-backdrop)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: APP_Z_INDEX.modalNested,
    backdropFilter: 'blur(2px)',
    WebkitBackdropFilter: 'blur(2px)',
  } as CSSProperties,

  modal: {
    background: 'var(--po-overlay)',
    border: '1px solid var(--po-border)',
    borderRadius: 12,
    padding: '14px 20px 20px',
    maxWidth: 'calc(100vw - 32px)',
    width: 520,
    maxHeight: '80vh',
    overflowY: 'auto',
    overflowX: 'hidden',
    boxShadow: '0 24px 48px var(--po-shadow)',
    fontFamily:
      "var(--po-font-sans)",
  } as CSSProperties,

  header: {
    fontSize: 13,
    fontWeight: 500,
    lineHeight: '18px',
    color: 'var(--po-text-muted)',
    marginBottom: 8,
  } as CSSProperties,

  pathInfo: {
    fontSize: 12,
    color: 'var(--po-text-muted)',
    marginBottom: 20,
    fontFamily:
      'var(--po-font-sans)',
  } as CSSProperties,

  label: {
    fontSize: 11,
    lineHeight: '14px',
    fontWeight: 600,
    letterSpacing: '0.04em',
    textTransform: 'uppercase',
    color: 'var(--po-text-subtle)',
    marginBottom: 8,
    display: 'block',
  } as CSSProperties,

  input: {
    width: '100%',
    height: 32,
    background: 'var(--po-panel-raised)',
    border: '1px solid var(--po-border-strong)',
    borderRadius: 6,
    padding: '0 10px',
    fontSize: 13,
    color: 'var(--po-text)',
    outline: 'none',
    marginBottom: 16,
  } as CSSProperties,

  buttonRow: {
    display: 'flex',
    gap: 10,
    marginTop: 4,
  } as CSSProperties,

  previewBox: {
    background: 'var(--po-inset)',
    border: '1px solid var(--po-border)',
    borderRadius: 6,
    padding: 12,
    marginBottom: 16,
    maxHeight: 200,
    overflow: 'auto',
    // Custom dark scrollbar
    scrollbarWidth: 'thin' as any,
    scrollbarColor: 'var(--po-border-strong) var(--po-inset)',
  } as CSSProperties,

  previewText: {
    fontSize: 11,
    color: 'var(--po-text)',
    fontFamily:
      'var(--po-font-sans)',
    whiteSpace: 'pre-wrap',
    margin: 0,
  } as CSSProperties,

  infoText: {
    fontSize: 12,
    color: 'var(--po-text-muted)',
    marginBottom: 12,
  } as CSSProperties,

  errorBox: {
    background: 'color-mix(in srgb, var(--po-danger) 10%, transparent)',
    border: '1px solid color-mix(in srgb, var(--po-danger) 20%, transparent)',
    borderRadius: 6,
    padding: 12,
    marginBottom: 16,
  } as CSSProperties,

  errorText: {
    fontSize: 12,
    color: 'var(--po-danger)',
  } as CSSProperties,

  strategySelector: {
    marginBottom: 16,
  } as CSSProperties,

  radioOption: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    marginBottom: 8,
    cursor: 'pointer',
  } as CSSProperties,

  radioLabel: {
    fontSize: 12,
    color: 'var(--po-text)',
    cursor: 'pointer',
  } as CSSProperties,
};

export function ImportModal({
  visible,
  targetPath = '',
  currentValue = null,
  tableId,
  tableName = '',
  projectId,
  mode = 'import_to_table',
  initialUrl = '',
  initialCrawlOptions,
  onClose,
  onSuccess,
}: ImportModalProps) {
  const router = useWorkspaceRouter();
  const [url, setUrl] = useState(initialUrl);
  const [isImporting, setIsImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needsAuth, setNeedsAuth] = useState(false);
  const [newTableName, setNewTableName] = useState(tableName);

  const [crawlOptions, setCrawlOptions] = useState<CrawlOptions>(
    initialCrawlOptions || {
      limit: 50,
      maxDepth: 3,
      crawlEntireDomain: true,
      sitemap: 'include',
    }
  );
  const urlSupportsCrawlOptions = supportsCrawlOptions(url);

  const handleImport = useCallback(async () => {
    if (!url.trim()) {
      setError('Please enter a URL');
      return;
    }

    setIsImporting(true);
    setError(null);
    setNeedsAuth(false);

    try {
      const response = await submitImport({
        project_id: String(projectId),
        url: url.trim(),
        name: mode === 'create_table' && newTableName.trim() ? newTableName.trim() : undefined,
        crawl_options: urlSupportsCrawlOptions ? crawlOptions : undefined,
      });

      onSuccess({
        task_id: response.task_id,
        node_id: response.path,
        import_type: response.import_type,
      });
      onClose();
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Import failed';
      setError(errorMessage);

      if (errorMessage.toLowerCase().includes('auth')) {
        setNeedsAuth(true);
      }
    } finally {
      setIsImporting(false);
    }
  }, [
    url,
    projectId,
    newTableName,
    crawlOptions,
    urlSupportsCrawlOptions,
    mode,
    onSuccess,
    onClose,
  ]);

  const handleGoToAuth = useCallback(() => {
    if (router.push('/connect')) onClose();
  }, [onClose, router]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      } else if (e.key === 'Enter' && !isImporting && url.trim()) {
        handleImport();
      }
    },
    [isImporting, url, onClose, handleImport]
  );

  if (!visible) return null;

  return (
    <ModalPortal>
    <div role='presentation' style={styles.overlay} onClick={onClose}>
      <div
        role='dialog'
        aria-modal='true'
        aria-labelledby='url-import-dialog-title'
        style={styles.modal}
        onClick={e => e.stopPropagation()}
        onKeyDown={handleKeyDown}
      >
        <div id='url-import-dialog-title' style={styles.header}>
          {mode === 'create_table'
            ? 'Import from URL'
            : 'Import Data from URL'}
        </div>

        <div style={{ fontSize: 11, color: 'var(--po-text-muted)', marginBottom: 16 }}>
          {mode === 'create_table'
            ? 'Create a context item from a URL'
            : 'Import data from a URL'}
        </div>

        {/* URL Input */}
        <label style={styles.label}>Data Source URL</label>
        <input
          type='url'
          value={url}
          onChange={e => setUrl(e.target.value)}
          placeholder='https://github.com/org/repo, https://yourworkspace.notion.so/..., or https://example.com'
          disabled={isImporting}
          style={styles.input}
          autoFocus
        />

        {urlSupportsCrawlOptions && (
          <CrawlOptionsPanel
            url={url}
            options={crawlOptions}
            onChange={setCrawlOptions}
          />
        )}

        {/* Create Table Mode - Table Name Input */}
        {mode === 'create_table' && (
          <div style={{ marginBottom: 16 }}>
            <label style={styles.label}>Destination name (optional)</label>
            <input
              type='text'
              value={newTableName}
              onChange={e => setNewTableName(e.target.value)}
              placeholder='Leave blank to use the source name'
              disabled={isImporting}
              style={styles.input}
            />
          </div>
        )}

        {/* Error Message */}
        {error && (
          <div style={styles.errorBox}>
            <div style={styles.errorText}>{error}</div>
            {needsAuth && (
              <ActionButton
                onClick={handleGoToAuth}
                variant='primary'
                fullWidth
                style={{ marginTop: 12 }}
              >
                Go to Authorization
              </ActionButton>
            )}
          </div>
        )}

        {/* Action Buttons */}
        <div style={styles.buttonRow}>
          <ActionButton
            onClick={onClose}
            disabled={isImporting}
            style={{ flex: 1 }}
          >
            Cancel
          </ActionButton>
          <ActionButton
            onClick={handleImport}
            disabled={isImporting || !url.trim()}
            variant='primary'
            loading={isImporting}
            style={{ flex: 1 }}
          >
            {isImporting && <Dots size='xs' />}
            {isImporting
              ? mode === 'create_table'
                ? 'Creating…'
                : 'Importing…'
              : mode === 'create_table'
                ? 'Create Table'
                : 'Import'}
          </ActionButton>
        </div>
      </div>
    </div>
    </ModalPortal>
  );
}

export default ImportModal;
