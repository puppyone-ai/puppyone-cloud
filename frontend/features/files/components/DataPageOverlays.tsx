'use client';

import { Dots } from '@/components/loading';
import { CreateMenu } from '@/features/files/components/menus/CreateMenu';
import type { CreateMenuPosition, DataCreateMenuActions } from '@/features/files/hooks/useDataCreateFlow';
import type { DataPageToast } from '@/features/files/hooks/useNodeActions';
import { Check, X } from 'lucide-react';
import type { RefObject } from 'react';

interface DataPageOverlaysProps {
  toast: DataPageToast | null;
  createMenuOpen: boolean;
  createMenuPosition: CreateMenuPosition | null;
  // When the menu was opened by a scoped `Expose as...` command rather
  // than the regular `+`, this flag flips CreateMenu into its
  // `accessOnly` rendering — flat list of providers / agents /
  // endpoints, no Create Blank / Upload sections.  Same menu
  // instance, different layout based on intent.
  createMenuAccessOnly: boolean;
  createMenuRef: RefObject<HTMLDivElement>;
  createMenuActions: DataCreateMenuActions;
}

export function DataPageOverlays({
  toast,
  createMenuOpen,
  createMenuPosition,
  createMenuAccessOnly,
  createMenuRef,
  createMenuActions,
}: DataPageOverlaysProps) {
  const isError = toast?.type === 'error';
  const isLoading = toast?.type === 'loading';

  return (
    <>
      {toast && (
        <div
          style={{
            position: 'fixed',
            bottom: 20,
            left: '50%',
            transform: 'translateX(-50%)',
            background: isError
              ? 'var(--po-danger)'
              : isLoading
                ? 'var(--po-panel-raised)'
                : 'var(--po-success)',
            color: isLoading ? 'var(--po-text)' : 'var(--po-text-inverse)',
            border: isLoading ? '1px solid var(--po-border)' : '1px solid transparent',
            padding: '8px 16px',
            borderRadius: 8,
            fontSize: 13,
            fontWeight: 500,
            zIndex: 10001,
            boxShadow: '0 4px 12px var(--po-shadow)',
            display: 'flex',
            alignItems: 'center',
            gap: 6,
          }}
        >
          {isLoading ? (
            <Dots size="xs" ariaLabel="Loading" />
          ) : isError ? (
            <X size={14} strokeWidth={2.6} />
          ) : (
            <Check size={14} strokeWidth={2.6} />
          )}
          {toast.message}
        </div>
      )}

      {createMenuOpen && createMenuPosition && (
        <div ref={createMenuRef}>
          <CreateMenu
            x={createMenuPosition.x}
            y={createMenuPosition.y}
            anchorLeft={createMenuPosition.anchorLeft}
            accessOnly={createMenuAccessOnly}
            onClose={createMenuActions.onClose}
            onCreateFolder={createMenuActions.onCreateFolder}
            onCreateBlankJson={createMenuActions.onCreateBlankJson}
            onCreateBlankMarkdown={createMenuActions.onCreateBlankMarkdown}
            onImportFromFiles={createMenuActions.onImportFromFiles}
            onImportFromUrl={createMenuActions.onImportFromUrl}
            onImportFromSaas={createMenuActions.onImportFromSaas}
            onImportNotion={createMenuActions.onImportNotion}
            onConnectGitHub={createMenuActions.onConnectGitHub}
            onImportGmail={createMenuActions.onImportGmail}
            onImportDocs={createMenuActions.onImportDocs}
            onImportCalendar={createMenuActions.onImportCalendar}
            onImportSheets={createMenuActions.onImportSheets}
            onConnectSupabase={createMenuActions.onConnectSupabase}
            onImportSearchConsole={createMenuActions.onImportSearchConsole}
            onCreateAgent={createMenuActions.onCreateAgent}
            onCreateMcp={createMenuActions.onCreateMcp}
            onCreateSandbox={createMenuActions.onCreateSandbox}
            onCreateSshTerminal={createMenuActions.onCreateSshTerminal}
          />
        </div>
      )}
    </>
  );
}
