'use client';

import { useState, useEffect, useRef, type FormEvent } from 'react';
import { useRouter } from 'next/navigation';
import { LayoutTemplate } from 'lucide-react';
import type { ProjectInfo } from '../lib/projectsApi';
import {
  createProject,
  updateProject,
  deleteProject,
} from '../lib/projectsApi';
import { refreshProjects } from '../lib/hooks/useData';
import { useOrganization } from '@/contexts/OrganizationContext';
import { nextUntitledProjectName } from '@/lib/projectNames';
import { Dots } from './loading';
import { ActionButton } from './ui/ActionButton';
import { DangerNotice } from './ui/DangerNotice';
import { DialogBody, DialogFooter, DialogHeader, DialogRoot, DialogSurface } from './ui/Dialog';
import { Field, TextField } from './ui/Field';

// ── Visual tokens ────────────────────────────────────────────
const ACCENT = 'var(--po-success)';
type DialogMode = 'create' | 'edit' | 'delete';

type ProjectManageDialogProps = {
  layer?: 'modal' | 'modalNested';
  mode: DialogMode;
  projectId: string | null;
  projects: ProjectInfo[];
  onClose: () => void;
  onDeleted?: () => void;
  onModeChange?: (mode: DialogMode) => void;
};

export function ProjectManageDialog({
  layer = 'modal',
  mode,
  projectId,
  projects,
  onClose,
  onDeleted,
}: ProjectManageDialogProps) {
  const router = useRouter();
  const { currentOrg } = useOrganization();
  const project = projectId ? projects.find(p => p.id === projectId) : null;
  const surfaceWidth = mode === 'create' ? 460 : 420;

  const [name, setName] = useState(project?.name || '');
  const [loading, setLoading] = useState(false);
  const createOperationRef = useRef<{
    idempotencyKey: string;
    name: string;
    orgId: string;
  } | null>(null);

  useEffect(() => {
    if (project) {
      setName(project.name);
    }
  }, [project]);

  // Esc to close
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();

    const finalName = name.trim() || nextUntitledProjectName(projects);

    try {
      if (mode === 'edit' && projectId) {
        setLoading(true);
        await updateProject(projectId, { name: finalName });
        await refreshProjects(currentOrg?.id);
        onClose();
      } else {
        if (!currentOrg?.id) {
          throw new Error('Select an organization before creating a project.');
        }
        setLoading(true);
        const previousOperation = createOperationRef.current;
        const operation =
          previousOperation?.name === finalName &&
          previousOperation.orgId === currentOrg.id
            ? previousOperation
            : {
                idempotencyKey: crypto.randomUUID(),
                name: finalName,
                orgId: currentOrg.id,
              };
        createOperationRef.current = operation;
        const created = await createProject({
          name: finalName,
          description: '',
          orgId: currentOrg.id,
          idempotencyKey: operation.idempotencyKey,
        });
        createOperationRef.current = null;
        onClose();
        // Jump straight into the new project. Refresh the org project list in
        // the background so navigation is not blocked by a home-page reload.
        router.push(`/projects/${created.id}/data`);
        void refreshProjects(currentOrg?.id);
      }
    } catch (error) {
      console.error('Failed to save project:', error);
      alert(
        'Operation failed: ' +
          (error instanceof Error ? error.message : 'Unknown error')
      );
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async () => {
    if (!projectId) return;
    try {
      setLoading(true);
      await deleteProject(projectId);
      await refreshProjects(currentOrg?.id);
      onClose();
      onDeleted?.();
    } catch (error) {
      console.error('Failed to delete project:', error);
      alert(
        'Delete failed: ' +
          (error instanceof Error ? error.message : 'Unknown error')
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <DialogRoot onClose={onClose} layer={layer}>
      <DialogSurface width={surfaceWidth}>
        {mode === 'delete' ? (
          <DeleteBody
            projectName={project?.name}
            onClose={onClose}
            onConfirm={handleDelete}
            loading={loading}
          />
        ) : mode === 'edit' ? (
          <EditBody
            name={name}
            setName={setName}
            onClose={onClose}
            onSubmit={handleSubmit}
            loading={loading}
          />
        ) : (
          <CreateBody
            name={name}
            setName={setName}
            onClose={onClose}
            onSubmit={handleSubmit}
            loading={loading}
            onBrowseTemplates={() => {
              onClose();
              router.push('/templates');
            }}
          />
        )}
      </DialogSurface>
    </DialogRoot>
  );
}

// ─────────────────────────────────────────────────────────────
// CREATE BODY
// ─────────────────────────────────────────────────────────────

function CreateBody({
  name,
  setName,
  onClose,
  onSubmit,
  loading,
  onBrowseTemplates,
}: {
  name: string;
  setName: (v: string) => void;
  onClose: () => void;
  onSubmit: (e: FormEvent) => void;
  loading: boolean;
  onBrowseTemplates: () => void;
}) {
  return (
    <>
      <DialogHeader title="New Project" onClose={onClose} />
      <form onSubmit={onSubmit}>
        <DialogBody style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          <Field label="New project name">
            <TextField
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="Untitled"
              autoFocus
            />
          </Field>

          <Field label="Start">
            <div style={{ display: 'grid', gap: 10 }}>
              <StartEmptyCard />
              <button
                type="button"
                onClick={onBrowseTemplates}
                style={{
                  width: '100%',
                  minHeight: 62,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 12,
                  padding: '10px 14px',
                  borderRadius: 8,
                  border: '1px solid var(--po-border)',
                  background: 'var(--po-panel)',
                  color: 'var(--po-text)',
                  textAlign: 'left',
                  cursor: 'pointer',
                }}
              >
                <LayoutTemplate size={18} color="var(--po-text-muted)" />
                <span>
                  <span style={{ display: 'block', fontSize: 13, fontWeight: 600 }}>
                    Browse templates
                  </span>
                  <span style={{ display: 'block', marginTop: 2, fontSize: 12, color: 'var(--po-text-muted)' }}>
                    Start from a reusable project structure.
                  </span>
                </span>
              </button>
            </div>
          </Field>
        </DialogBody>

        <FooterBar
          onClose={onClose}
          primaryLabel={loading ? 'Creating…' : 'Create Project'}
          loading={loading}
        />
      </form>
    </>
  );
}

function StartEmptyCard() {
  return (
    <div
      style={{
        width: '100%',
        minHeight: 74,
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        padding: '12px 14px',
        borderRadius: 8,
        border: '1px solid color-mix(in srgb, var(--po-success) 45%, transparent)',
        background: 'color-mix(in srgb, var(--po-success) 10%, transparent)',
        color: 'var(--po-text)',
        textAlign: 'left',
      }}
    >
      <span
        aria-hidden
        style={{
          width: 30,
          height: 30,
          borderRadius: 999,
          border: '1px solid color-mix(in srgb, var(--po-success) 58%, transparent)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: ACCENT,
          flexShrink: 0,
        }}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
          <path d="M12 5v14M5 12h14" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
        </svg>
      </span>
      <span style={{ minWidth: 0, flex: 1 }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--po-text)' }}>
            Start empty
          </span>
          <span style={{
            fontSize: 10,
            lineHeight: '14px',
            padding: '1px 6px',
            borderRadius: 999,
            color: 'var(--po-success)',
            background: 'color-mix(in srgb, var(--po-success) 15%, transparent)',
            border: '1px solid color-mix(in srgb, var(--po-success) 26%, transparent)',
          }}>
            Recommended
          </span>
        </span>
        <span style={{ display: 'block', fontSize: 12, lineHeight: 1.45, color: 'var(--po-text-muted)' }}>
          Open the workspace first, then import from GitHub, Notion, Obsidian/local folders, URLs, or files.
        </span>
      </span>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// EDIT BODY
// ─────────────────────────────────────────────────────────────

function EditBody({
  name,
  setName,
  onClose,
  onSubmit,
  loading,
}: {
  name: string;
  setName: (v: string) => void;
  onClose: () => void;
  onSubmit: (e: FormEvent) => void;
  loading: boolean;
}) {
  return (
    <>
      <DialogHeader title="Rename Project" onClose={onClose} />
      <form onSubmit={onSubmit}>
        <DialogBody>
          <Field label="Project name">
            <TextField value={name} onChange={e => setName(e.target.value)} autoFocus />
          </Field>
        </DialogBody>
        <FooterBar
          onClose={onClose}
          primaryLabel={loading ? 'Saving…' : 'Save Changes'}
          loading={loading}
          primaryDisabled={!name.trim()}
        />
      </form>
    </>
  );
}

// ─────────────────────────────────────────────────────────────
// DELETE BODY
// ─────────────────────────────────────────────────────────────

function DeleteBody({
  projectName,
  onClose,
  onConfirm,
  loading,
}: {
  projectName: string | undefined;
  onClose: () => void;
  onConfirm: () => void;
  loading: boolean;
}) {
  return (
    <>
      <DialogHeader title="Delete Project" onClose={onClose} />
      <DialogBody>
        <DangerNotice title={`Delete ${projectName ? `"${projectName}"` : 'this project'}?`}>
          This permanently deletes the project and every context node inside
          it. This action cannot be undone.
        </DangerNotice>
      </DialogBody>
      <FooterBar
        onClose={onClose}
        loading={loading}
        primaryLabel={loading ? 'Deleting…' : 'Delete Project'}
        primaryDanger
        onPrimaryClick={onConfirm}
      />
    </>
  );
}

// ─────────────────────────────────────────────────────────────
// SHARED ATOMS
// ─────────────────────────────────────────────────────────────

function FooterBar({
  onClose,
  primaryLabel,
  loading,
  primaryDanger,
  primaryDisabled,
  onPrimaryClick,
}: {
  onClose: () => void;
  primaryLabel: string;
  loading: boolean;
  primaryDanger?: boolean;
  primaryDisabled?: boolean;
  onPrimaryClick?: () => void;
}) {
  return (
    <DialogFooter>
      <ActionButton
        type="button"
        onClick={onClose}
      >
        Cancel
      </ActionButton>
      <ActionButton
        type={onPrimaryClick ? 'button' : 'submit'}
        onClick={onPrimaryClick}
        variant={primaryDanger ? 'danger' : 'primary'}
        loading={loading}
        disabled={primaryDisabled}
      >
        {loading && <Dots size="xs" />}
        {primaryLabel}
      </ActionButton>
    </DialogFooter>
  );
}
