'use client';

import { Dots } from '@/components/loading';
import { ActionButton } from '@/components/ui/ActionButton';
import { DialogBody, DialogFooter, DialogHeader, DialogRoot, DialogSurface } from '@/components/ui/Dialog';
import { useEffect, useState } from 'react';

type MoveConfirmDialogProps = {
  open: boolean;
  nodeName: string;
  oldPath: string;
  newPath: string;
  targetLabel: string;
  onClose: () => void;
  onConfirm: () => Promise<void>;
};

export function MoveConfirmDialog({
  open,
  nodeName,
  oldPath,
  newPath,
  targetLabel,
  onClose,
  onConfirm,
}: MoveConfirmDialogProps) {
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (open) setSubmitting(false);
  }, [open]);

  if (!open) return null;

  const doConfirm = async () => {
    if (submitting) return;
    setSubmitting(true);
    try {
      await onConfirm();
      onClose();
    } catch {
      setSubmitting(false);
    }
  };

  return (
    <DialogRoot
      onClose={submitting ? undefined : onClose}
      dismissOnBackdrop={!submitting}
    >
      <DialogSurface width={480}>
        <DialogHeader
          title={`Move "${nodeName}"?`}
          description={`Confirm moving this item to ${targetLabel}.`}
          onClose={submitting ? undefined : onClose}
        />

        <DialogBody>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '64px minmax(0, 1fr)',
              gap: '8px 12px',
              border: '1px solid var(--po-border)',
              borderRadius: 8,
              background: 'var(--po-overlay)',
              padding: 12,
              fontSize: 12,
            }}
          >
            <PathLabel>From</PathLabel>
            <PathValue>{oldPath}</PathValue>
            <PathLabel>To</PathLabel>
            <PathValue>{newPath}</PathValue>
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
            variant="primary"
            loading={submitting}
          >
            {submitting && <Dots size="xs" />}
            Move
          </ActionButton>
        </DialogFooter>
      </DialogSurface>
    </DialogRoot>
  );
}

function PathLabel({ children }: { readonly children: string }) {
  return (
    <div style={{ color: 'var(--po-text-subtle)', fontWeight: 500 }}>
      {children}
    </div>
  );
}

function PathValue({ children }: { readonly children: string }) {
  return (
    <div
      title={children}
      style={{
        minWidth: 0,
        color: 'var(--po-text-muted)',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
      }}
    >
      {children || 'Project Root'}
    </div>
  );
}
