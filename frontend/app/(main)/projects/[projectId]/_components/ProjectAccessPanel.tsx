'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Plus } from 'lucide-react';
import { PanelShell } from '@/components/chrome/PanelShell';
import { DesktopChromeAction } from '@/components/chrome/DesktopChrome';
import { PageLoading } from '@/components/loading';
import { CountBadge } from '@/components/ui/CountBadge';
import { useProjectSession } from '@/features/workspace/session';
import { useProject } from '@/lib/hooks/useData';
import { projectAllows } from '@/lib/projectsApi';
import {
  enableTargetAccess,
  repositoryViewKey,
  type RepositoryView,
} from '@/lib/repoApi';
import { useAccessData } from '../access/hooks/useAccessData';
import { CreateAccessModal } from '../access/components/CreateAccessModal';
import { ScopeSidebar } from '../access/components/ScopeSidebar';
import { ScopeDetailPanel } from '../access/components/ScopeDetailPanel';
import { AccessLoadError, NoConnectorsState } from '../access/components/page-shell';

export function ProjectAccessPanel({ projectId, onClose }: { projectId: string; onClose: () => void }) {
  const panel = useProjectSession(state => state.panel);
  const openPanel = useProjectSession(state => state.openPanel);
  const setPanelNavigationGuard = useProjectSession(state => state.setPanelNavigationGuard);
  const { project } = useProject(projectId);
  const canManageAccess = projectAllows(project, 'access_surface.manage')
    && projectAllows(project, 'scope.manage');
  const [createOpen, setCreateOpen] = useState(false);
  const [enablingTargetKey, setEnablingTargetKey] = useState<string | null>(null);
  const [enableError, setEnableError] = useState<string | null>(null);
  const {
    loading,
    loadError,
    noScopes,
    allScopes,
    sortedScopes,
    connectorsByTarget,
    pendingConnectorIds,
    setSelectedTargetKey,
    handlePauseResume,
    handleUpdate,
    handleDelete,
    refresh,
    clearScopeSelection,
  } = useAccessData(projectId);

  const requestedScope = useMemo(() => {
    if (panel.type !== 'access_list') return undefined;
    if (panel.selectedTargetKey) {
      return sortedScopes.find(scope => repositoryViewKey(scope) === panel.selectedTargetKey);
    }
    if (panel.accessEndpointId) {
      for (const scope of sortedScopes) {
        const connectors = connectorsByTarget.get(repositoryViewKey(scope)) ?? [];
        if (connectors.some(connector => connector.id === panel.accessEndpointId)) return scope;
      }
    }
    if (panel.nodeId !== undefined) {
      const path = panel.nodeId.replace(/^\/+|\/+$/g, '');
      return sortedScopes.find(scope => scope.path.replace(/^\/+|\/+$/g, '') === path);
    }
    return undefined;
  }, [connectorsByTarget, panel, sortedScopes]);
  const detailOpen = panel.type === 'access_list'
    && panel.view !== 'overview'
    && panel.view !== 'create'
    && requestedScope !== undefined;
  const selectedConnectors = requestedScope
    ? connectorsByTarget.get(repositoryViewKey(requestedScope)) ?? []
    : [];

  useEffect(() => {
    setCreateOpen(panel.type === 'access_list' && panel.view === 'create');
  }, [panel]);

  useEffect(() => {
    if (!requestedScope) return;
    setSelectedTargetKey(repositoryViewKey(requestedScope));
  }, [requestedScope, setSelectedTargetKey]);

  const openOverview = useCallback(() => {
    openPanel({ type: 'access_list', view: 'overview' });
  }, [openPanel]);
  const openScope = useCallback((targetKey: string) => {
    setSelectedTargetKey(targetKey);
    openPanel({ type: 'access_list', view: 'detail', selectedTargetKey: targetKey });
  }, [openPanel, setSelectedTargetKey]);
  const openCreate = useCallback((path?: string | null) => {
    if (!canManageAccess) return;
    openPanel({ type: 'access_list', view: 'create', nodeId: path ?? '' });
    setCreateOpen(true);
  }, [canManageAccess, openPanel]);
  const closeCreate = useCallback(() => {
    setCreateOpen(false);
    openOverview();
  }, [openOverview]);
  const handleCreated = useCallback(async (scope: RepositoryView) => {
    await refresh();
    const targetKey = repositoryViewKey(scope);
    setCreateOpen(false);
    openScope(targetKey);
  }, [openScope, refresh]);
  const handleEnableTargetAccess = useCallback(async (scope: RepositoryView) => {
    const key = repositoryViewKey(scope);
    setEnablingTargetKey(key);
    setEnableError(null);
    try {
      await enableTargetAccess(projectId, scope.target);
      await refresh();
    } catch (error) {
      setEnableError(error instanceof Error ? error.message : 'Could not enable access.');
    } finally {
      setEnablingTargetKey(null);
    }
  }, [projectId, refresh]);

  const title = (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
      <span>{detailOpen && requestedScope ? requestedScope.name : 'Access'}</span>
      {!detailOpen && <CountBadge value={sortedScopes.length} size='md' tone='neutral' />}
    </span>
  );
  const createAction = canManageAccess && !detailOpen ? (
    <DesktopChromeAction
      label='New access point'
      icon={<Plus size={15} strokeWidth={2} />}
      onClick={() => openCreate()}
    />
  ) : undefined;

  return (
    <>
      <PanelShell
        title={title}
        onClose={onClose}
        onBack={detailOpen ? openOverview : undefined}
        headerRight={createAction}
      >
        {loadError ? (
          <AccessLoadError error={loadError} onRetry={() => { void refresh(); }} />
        ) : loading ? (
          <PageLoading variant='fill' label='Loading access' />
        ) : noScopes ? (
          <NoConnectorsState onCreateScope={canManageAccess ? () => openCreate() : undefined} />
        ) : detailOpen && requestedScope ? (
          <ScopeDetailPanel
            scope={requestedScope}
            connectors={selectedConnectors}
            projectId={projectId}
            onPauseResume={handlePauseResume}
            onUpdate={handleUpdate}
            onDelete={handleDelete}
            pendingConnectorIds={pendingConnectorIds}
            onScopeMutated={refresh}
            onScopeDeleted={() => {
              clearScopeSelection();
              openOverview();
            }}
            canManage={canManageAccess}
            enablingStandardAccess={enablingTargetKey === repositoryViewKey(requestedScope)}
            enableStandardAccessError={enableError}
            onEnableStandardAccess={() => handleEnableTargetAccess(requestedScope)}
            onNavigationGuardChange={setPanelNavigationGuard}
          />
        ) : (
          <ScopeSidebar
            scopes={sortedScopes}
            connectorsByTarget={connectorsByTarget}
            selectedTargetKey={undefined}
            onSelect={openScope}
          />
        )}
      </PanelShell>
      {canManageAccess && createOpen && panel.type === 'access_list' ? (
        <CreateAccessModal
          projectId={projectId}
          existingScopes={allScopes}
          connectorsByTarget={connectorsByTarget}
          initialPath={panel.nodeId ?? null}
          onClose={closeCreate}
          onCreated={handleCreated}
        />
      ) : null}
    </>
  );
}
