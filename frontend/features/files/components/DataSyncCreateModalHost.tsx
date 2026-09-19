'use client';

import { DialogRoot, DialogSurface } from '@/components/ui/Dialog';
import { SyncConfigPanel } from '@/features/files/components/SyncConfigPanel';

function normalizePath(path: string | null | undefined): string {
  return (path ?? '').trim().replace(/^\/+|\/+$/g, '').replace(/\/+/g, '/');
}

function folderLabel(path: string): string {
  if (!path) return 'Root';
  return path.split('/').filter(Boolean).at(-1) ?? 'Root';
}

export function DataSyncCreateModalHost({
  projectId,
  initialPath,
  onClose,
  onSyncCreated,
}: {
  readonly projectId: string;
  readonly initialPath: string | null;
  readonly onClose: () => void;
  readonly onSyncCreated: (nodeId: string) => void | Promise<void>;
}) {
  if (initialPath === null) return null;

  const scopePath = normalizePath(initialPath);

  return (
    <DialogRoot open onClose={onClose} backdrop="strong" dismissOnBackdrop>
      <DialogSurface
        width={520}
        maxWidth="calc(100vw - 32px)"
        maxHeight="calc(100vh - 32px)"
        ariaLabel="Create integration"
        style={{ height: 'min(760px, calc(100vh - 32px))' }}
      >
        <SyncConfigPanel
          mode="create"
          syncId={null}
          projectId={projectId}
          onClose={onClose}
          onSyncCreated={onSyncCreated}
          scopeBoundary={scopePath}
          scopeBoundaryLabel={folderLabel(scopePath)}
        />
      </DialogSurface>
    </DialogRoot>
  );
}
