'use client';

import type { ReactNode } from 'react';
import { ActionButton } from './ActionButton';
import {
  DialogBody,
  DialogFooter,
  DialogHeader,
  DialogRoot,
  DialogSurface,
} from './Dialog';

type ConfirmTone = 'danger' | 'warning';

type ConfirmDialogProps = {
  layer?: 'modal' | 'modalNested';
  open?: boolean;
  title: ReactNode;
  description?: ReactNode;
  confirmLabel?: ReactNode;
  cancelLabel?: ReactNode;
  tone?: ConfirmTone;
  loading?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
};

export function ConfirmDialog({
  layer = 'modal',
  open = true,
  title,
  description,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  tone = 'danger',
  loading = false,
  onCancel,
  onConfirm,
}: ConfirmDialogProps) {
  if (!open) return null;

  return (
    <DialogRoot onClose={onCancel} dismissOnBackdrop={!loading} layer={layer}>
      <DialogSurface width={420}>
        <DialogHeader title={title} onClose={loading ? undefined : onCancel} />
        {description && (
          <DialogBody
            style={{
              paddingTop: 4,
              paddingBottom: 18,
              fontSize: 13,
              lineHeight: '20px',
              color: 'var(--po-text-muted)',
            }}
          >
            {description}
          </DialogBody>
        )}
        <DialogFooter>
          <ActionButton onClick={onCancel} disabled={loading}>
            {cancelLabel}
          </ActionButton>
          <ActionButton
            variant={tone}
            loading={loading}
            onClick={onConfirm}
          >
            {confirmLabel}
          </ActionButton>
        </DialogFooter>
      </DialogSurface>
    </DialogRoot>
  );
}
