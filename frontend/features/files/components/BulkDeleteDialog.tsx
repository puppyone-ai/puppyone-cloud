'use client';

/**
 * BulkDeleteDialog — confirmation modal for multi-select delete.
 *
 * Mirrors the visual language of TableDeleteDialog (same dark
 * surface, same destructive red affordance) so the user gets a
 * familiar destructive-action shape. Renders a compact preview of
 * the selected items so the user can sanity-check before
 * committing — invaluable when shift-clicks or rogue cmd-clicks
 * grab unintended siblings.
 *
 * Delete removes items from the current tree. Recovery is handled
 * through Puppyone version history/rollback, not a hidden .trash tree.
 */

import { Dots } from '@/components/loading';
import { ActionButton } from '@/components/ui/ActionButton';
import { DangerNotice } from '@/components/ui/DangerNotice';
import { DialogBody, DialogFooter, DialogHeader, DialogRoot, DialogSurface } from '@/components/ui/Dialog';
import { useEffect, useState, type ReactNode } from 'react';

interface BulkDeleteDialogProps {
  open: boolean;
  paths: string[];
  onClose: () => void;
  onConfirm: () => Promise<void>;
  title?: string;
  noticeTitle?: string;
  description?: ReactNode;
  confirmLabel?: string;
  submittingLabel?: string;
}

const PREVIEW_LIMIT = 8;

export function BulkDeleteDialog({
  open,
  paths,
  onClose,
  onConfirm,
  title,
  noticeTitle,
  description,
  confirmLabel,
  submittingLabel,
}: BulkDeleteDialogProps) {
  const [submitting, setSubmitting] = useState(false);

  // Reset transient form state every time the dialog re-opens.
  useEffect(() => {
    if (open) {
      setSubmitting(false);
    }
  }, [open]);

  // Esc-to-cancel and Enter-to-confirm. Avoids forcing the user
  // back to the mouse for routine confirms.
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (submitting) return;
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
        return;
      }
      if (e.key === 'Enter') {
        e.preventDefault();
        void doConfirm();
      }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, submitting]);

  if (!open) return null;

  const doConfirm = async () => {
    if (submitting) return;
    setSubmitting(true);
    try {
      await onConfirm();
      onClose();
    } catch {
      // Toast surfacing is the parent's job; just unblock the button.
    } finally {
      setSubmitting(false);
    }
  };

  const previewPaths = paths.slice(0, PREVIEW_LIMIT);
  const overflow = paths.length - previewPaths.length;
  const itemLabel = `${paths.length} item${paths.length === 1 ? '' : 's'}`;
  const resolvedTitle = title ?? `Delete ${itemLabel}`;
  const resolvedNoticeTitle = noticeTitle ?? `Delete ${itemLabel}?`;
  const resolvedDescription = description ?? (
    <>
      Items are removed from the current tree. You can recover prior
      contents from Puppyone version history or rollback.
    </>
  );
  const resolvedConfirmLabel = confirmLabel ?? `Delete ${paths.length}`;
  const resolvedSubmittingLabel = submittingLabel ?? 'Deleting…';

  return (
    <DialogRoot
      onClose={submitting ? undefined : onClose}
      dismissOnBackdrop={!submitting}
    >
      <DialogSurface width={520}>
        <DialogHeader
          title={resolvedTitle}
          onClose={submitting ? undefined : onClose}
        />

        <DialogBody>
          <DangerNotice title={resolvedNoticeTitle}>
            {resolvedDescription}
          </DangerNotice>

          <div
            style={{
              background: 'var(--po-overlay)',
              border: '1px solid var(--po-border)',
              borderRadius: 8,
              padding: '6px 4px',
              maxHeight: 200,
              overflowY: 'auto',
              marginTop: 14,
            }}
          >
            {previewPaths.map((p) => (
              <div
                key={p}
                style={{
                  padding: '4px 10px',
                  fontSize: 12,
                  fontFamily: 'var(--po-font-sans)',
                  color: 'var(--po-text-muted)',
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                }}
                title={p}
              >
                {p}
              </div>
            ))}
            {overflow > 0 && (
              <div
                style={{
                  padding: '4px 10px',
                  fontSize: 12,
                  color: 'var(--po-text-subtle)',
                  fontStyle: 'italic',
                }}
              >
                + {overflow} more…
              </div>
            )}
          </div>
        </DialogBody>

        <DialogFooter>
          <ActionButton
            type="button"
            onClick={onClose}
            disabled={submitting}
          >
            Cancel
          </ActionButton>
          <ActionButton
            type="button"
            onClick={() => void doConfirm()}
            variant="danger"
            loading={submitting}
          >
            {submitting && <Dots size="xs" tone="danger" />}
            {submitting ? resolvedSubmittingLabel : resolvedConfirmLabel}
          </ActionButton>
        </DialogFooter>
      </DialogSurface>
    </DialogRoot>
  );
}
