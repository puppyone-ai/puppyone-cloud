'use client';

import { PageLoading } from '@/components/loading';
import { ProjectHeaderContribution } from '@/components/project/ProjectWorkspaceShell';
import { ResizableSidebarColumn } from '@/components/sidebar/ResizableSidebarColumn';
import { CountBadge } from '@/components/ui/CountBadge';
import { EmptyState } from '@/components/ui/EmptyState';
import { useAuth } from '@/contexts/SupabaseAuthProvider';
import { CommitDetail } from '@/features/history/components/CommitDetail';
import { HistoryMoreRow, VerticalCommitNode } from '@/features/history/components/CommitTimeline';
import { HistoryDetailViewport } from '@/features/history/components/HistoryDetailViewport';
import { HistoryFilterGroup, HistoryFilterOption } from '@/features/history/components/HistoryFilters';
import { NeedsActionDetailPane } from '@/features/history/components/NeedsActionDetailPane';
import type { NeedsActionSelection } from '@/features/history/components/NeedsActionSection';
import {
  NeedsActionSection,
  type NeedsActionItem,
  type NeedsActionSummary
} from '@/features/history/components/NeedsActionSection';
import { buildRiskyDeleteItems } from '@/features/history/components/items/riskyDeleteKind';
import { formatOperatorLabel, formatScopeLabel, getTrackInfo, parseOperator, scopeIncludesCommit, scopeLineage } from '@/features/history/historyPresentation';
import { useHistoryReconciliation } from '@/features/history/useHistoryReconciliation';
import { useSessionValue } from '@/features/workspace/session';
import {
  getProjectHistory
} from '@/lib/contentTreeApi';
import { useProject } from '@/lib/hooks/useData';
import type {
  ResolvedResult
} from '@/lib/needsActionRegistry';
import { projectAllows } from '@/lib/projectsApi';
import { workspaceKeys } from '@/lib/queryKeys';
import { Clock3, GitCommitHorizontal } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import useSWR from 'swr';

export default function GitWorkspace({ projectId }: { projectId: string }) {
  const { session } = useAuth();
  const { project } = useProject(session ? projectId : null);

  const { data: history, error, mutate: mutateHistory } = useSWR(
    session ? workspaceKeys.history(projectId) : null,
    () => getProjectHistory(projectId, 100),
    { revalidateOnFocus: false },
  );

  // We deliberately ignore SWR's `isLoading` here: it's only true
  // *while a fetch is in flight*. Before the SWR key becomes truthy
  // (auth still resolving, project not yet selected, etc.) SWR
  // returns `isLoading=false` even though `data` is still undefined.
  // Combined with the empty-state branch below this produced a brief
  // "No commits yet" flash on initial mount before the real fetch
  // had even started — the user reads it as "did everything just get
  // wiped?". Using "has data ever arrived?" (`history !== undefined`)
  // closes the gap: the loading view stays mounted until SWR has
  // actually delivered a payload (or raised an error), so the empty
  // branch only fires once we *know* the API returned zero commits.
  const isInitialLoading = !error && history === undefined;

  const commits = useMemo(() => history?.commits ?? [], [history]);
  const seedNeedsActionItems = useMemo(
    () => ({ 'risky-delete': buildRiskyDeleteItems(commits) }),
    [commits],
  );
  // Reverse commits so newest is on top
  const sortedCommits = useMemo(() => [...commits].reverse(), [commits]);

  const [activeScopeFilter, setActiveScopeFilter] = useSessionValue('historyScope');
  const [activeActorFilter, setActiveActorFilter] = useSessionValue('historyActor');
  const [filterMenuOpen, setFilterMenuOpen] = useState<'filters' | null>(null);
  const [historySectionOpen, setHistorySectionOpen] = useSessionValue('historySectionOpen');
  const [historyExpanded, setHistoryExpanded] = useSessionValue('historyExpanded');
  const [needsActionSummary, setNeedsActionSummary] = useState<NeedsActionSummary>({
    count: 0,
    loading: true,
    hasErrors: false,
  });
  const filterMenuRef = useRef<HTMLDivElement>(null);

  const scopeOptions = useMemo(() => {
    const counts = new Map<string, number>();
    commits.forEach(c => {
      scopeLineage(c.scope_path || '').forEach(scope => {
        counts.set(scope, (counts.get(scope) || 0) + 1);
      });
    });
    if (!counts.has('')) counts.set('', 0);
    return Array.from(counts.entries())
      .map(([scope, count]) => ({ scope, count }))
      .sort((a, b) => {
        if (a.scope === '') return -1;
        if (b.scope === '') return 1;
        const depthDiff = a.scope.split('/').length - b.scope.split('/').length;
        if (depthDiff !== 0) return depthDiff;
        return b.count - a.count;
      });
  }, [commits]);

  const scopeFilteredCommits = useMemo(
    () => sortedCommits.filter(c => scopeIncludesCommit(activeScopeFilter, c.scope_path || '')),
    [sortedCommits, activeScopeFilter],
  );
  const scopeChronologicalCommits = useMemo(
    () => commits.filter(c => scopeIncludesCommit(activeScopeFilter, c.scope_path || '')),
    [commits, activeScopeFilter],
  );

  const actorOptions = useMemo(() => {
    const counts = new Map<string, number>();
    scopeFilteredCommits.forEach(c => {
      const { type } = parseOperator(c.who);
      counts.set(type, (counts.get(type) || 0) + 1);
    });
    const order = ['user', 'agent', 'sync', 'system'];
    return Array.from(counts.entries())
      .map(([type, count]) => ({ type, count }))
      .sort((a, b) => {
        const ai = order.indexOf(a.type);
        const bi = order.indexOf(b.type);
        if (ai !== bi) return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
        return b.count - a.count;
      });
  }, [scopeFilteredCommits]);

  const activeActor = useMemo(
    () => actorOptions.find(option => option.type === activeActorFilter) ?? null,
    [actorOptions, activeActorFilter],
  );

  const activeScope = useMemo(
    () => scopeOptions.find(option => option.scope === activeScopeFilter) ?? null,
    [scopeOptions, activeScopeFilter],
  );

  useEffect(() => {
    if (!filterMenuOpen) return;

    function closeOnOutside(event: MouseEvent) {
      if (!filterMenuRef.current?.contains(event.target as Node)) {
        setFilterMenuOpen(null);
      }
    }

    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setFilterMenuOpen(null);
      }
    }

    document.addEventListener('mousedown', closeOnOutside);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('mousedown', closeOnOutside);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [filterMenuOpen]);

  const filteredCommits = useMemo(() => {
    let filtered = scopeFilteredCommits;
    if (activeActorFilter) {
      filtered = filtered.filter(c => parseOperator(c.who).type === activeActorFilter);
    }
    return filtered;
  }, [scopeFilteredCommits, activeActorFilter]);
  const hasPendingWork = needsActionSummary.count > 0;
  const collapsedHistoryLimit = hasPendingWork ? 5 : 9;
  const visibleHistoryCommits = historyExpanded
    ? filteredCommits
    : filteredCommits.slice(0, collapsedHistoryLimit);
  const hiddenHistoryCount = Math.max(0, filteredCommits.length - visibleHistoryCommits.length);
  const showsHistoryMoreRow = filteredCommits.length > collapsedHistoryLimit;

  const handleNeedsActionSummaryChange = useCallback((summary: NeedsActionSummary) => {
    setNeedsActionSummary((current) => {
      if (
        current.count === summary.count
        && current.loading === summary.loading
        && current.hasErrors === summary.hasErrors
      ) {
        return current;
      }
      return summary;
    });
  }, []);

  const [mobileDetailOpen, setMobileDetailOpen] = useState(false);
  const [selectedCommitId, setSelectedCommitId] = useSessionValue('historyCommit');
  // Commit selection is project-owned; needs-action detail is view-local.
  // Selecting either clears the other. Reconciliation waits for a real
  // history snapshot and never steals an active needs-action selection.
  const [selectedNeedsAction, setSelectedNeedsAction] = useState<NeedsActionSelection | null>(null);
  const [selectedNeedsActionItem, setSelectedNeedsActionItem] = useState<NeedsActionItem | null>(null);

  const headCommitId = history?.head_commit_id ?? '';

  useHistoryReconciliation({
    loaded: history !== undefined,
    scopeOptions,
    actorOptions,
    filteredCommits,
    headCommitId,
    needsActionSelected: selectedNeedsAction !== null,
  });

  const selectedCommit = useMemo(
    () => commits.find(c => c.commit_id === selectedCommitId) ?? null,
    [commits, selectedCommitId]
  );

  // Parent commit = the commit immediately preceding the selected one
  // in chronological order. The API returns commits oldest-first, so
  // the parent of `commits[i]` is `commits[i - 1]`.
  const parentCommitId = useMemo(() => {
    if (!selectedCommit) return null;
    const idx = scopeChronologicalCommits.findIndex(c => c.commit_id === selectedCommit.commit_id);
    if (idx <= 0) return null;
    return scopeChronologicalCommits[idx - 1].commit_id;
  }, [scopeChronologicalCommits, selectedCommit]);

  // ── Needs Action wiring ─────────────────────────────────────────
  // Selecting a needs-action item makes it the right pane content;
  // we drop the commit selection so the views never both render.
  const handleNeedsActionSelect = useCallback(
    (selection: NeedsActionSelection, item: NeedsActionItem) => {
      setMobileDetailOpen(true);
      setSelectedNeedsAction(selection);
      setSelectedNeedsActionItem(item);
      setSelectedCommitId(null);
    },
    [setSelectedCommitId],
  );

  // Item removed (resolved / rejected / dismissed). If a real commit
  // landed (``commit_id``) refresh history so it shows up at the top
  // and select it so the user sees the result. Otherwise jump back
  // to HEAD.
  const handleNeedsActionRemoved = useCallback(
    (selection: NeedsActionSelection, result: ResolvedResult) => {
      // Clear our selection IF the removed item is the one currently
      // shown — otherwise a background snooze on a different item
      // shouldn't kick the user off whatever they're reading.
      if (
        selectedNeedsAction
        && selectedNeedsAction.kind === selection.kind
        && selectedNeedsAction.itemId === selection.itemId
      ) {
        setSelectedNeedsAction(null);
        setSelectedNeedsActionItem(null);
      }
      if (result.commit_id) {
        void mutateHistory();
        // Highlight the new commit. The history list refresh will
        // pull it in; ``selectedCommitId`` settles on it, and the
        // existing auto-pick effect (lines around 1025) leaves it
        // alone because it is present in ``filteredCommits``.
        setSelectedCommitId(result.commit_id);
      } else if (!result.commit_id && selectedCommitId === null && headCommitId) {
        setSelectedCommitId(headCommitId);
      }
    },
    [mutateHistory, selectedNeedsAction, selectedCommitId, headCommitId, setSelectedCommitId],
  );

  // When the user clicks a commit, we drop the needs-action selection
  // so the right pane re-renders CommitDetail instead of the item
  // detail.
  const selectCommit = useCallback((commitId: string) => {
    setMobileDetailOpen(true);
    setSelectedCommitId(commitId);
    setSelectedNeedsAction(null);
    setSelectedNeedsActionItem(null);
  }, [setSelectedCommitId]);

  const activeFilterCount =
    (activeScopeFilter ? 1 : 0) + (activeActorFilter ? 1 : 0);
  const filterTitle = [
    activeScope ? formatScopeLabel(activeScope.scope) : null,
    activeActor ? formatOperatorLabel(activeActor.type) : null,
  ].filter(Boolean).join(' · ') || 'Filter history';
  const activeDetailKey = selectedNeedsAction && selectedNeedsActionItem
    ? `needs:${selectedNeedsAction.kind}:${selectedNeedsAction.itemId}`
    : selectedCommit
      ? `commit:${selectedCommit.commit_id}`
      : commits.length === 0
        ? 'empty:no-commits'
        : 'empty:no-selection';

  return (
    <>
      <ProjectHeaderContribution
        canManageSettings={projectAllows(project, 'project.settings.manage')}
        pathSegments={[{ label: project?.name ?? 'Project' }]}
        actions={history ? (
          <span style={{ padding: '0 5px', color: 'var(--po-text-subtle)', fontSize: 11 }}>
            {history.total} commit{history.total === 1 ? '' : 's'}
          </span>
        ) : undefined}
      />
      <div style={{ display: 'flex', height: '100%', minWidth: 0, overflow: 'hidden', background: 'var(--po-canvas)' }}>
      <div style={{ display: 'flex', flex: 1, minWidth: 0, flexDirection: 'column', overflow: 'hidden' }}>

      {/* ── Loading / Error / Empty ── */}
      {isInitialLoading && (
        <div style={{ flex: 1, minHeight: 0 }}>
          <PageLoading variant="fill" />
        </div>
      )}

      {error && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 200, color: 'var(--po-danger)', fontSize: 13 }}>
          Failed to load changes
        </div>
      )}

      {/* ── Main Layout (Left/Right Split) ── */}
      {!isInitialLoading && !error && (
        <div className="workspace-history-layout flex flex-1 min-h-0" data-detail={mobileDetailOpen}>
          {/* Left: Timeline List — wrapped in `ResizableSidebarColumn`
              so users can widen the timeline when commit messages or
              author IDs would otherwise truncate aggressively. Starts
              at the compact minimum to keep the diff surface dominant. */}
          <ResizableSidebarColumn
            storageKey='history-timeline:history'
            defaultWidth={260}
            minWidth={260}
            maxWidth={520}
            className="workspace-history-list border-r border-[var(--po-divider)] bg-[var(--po-canvas)] z-10"
          >
            <NeedsActionSection
              projectId={projectId}
              seedItemsByKind={seedNeedsActionItems}
              selected={selectedNeedsAction}
              onSelect={handleNeedsActionSelect}
              onItemRemoved={handleNeedsActionRemoved}
              onSummaryChange={handleNeedsActionSummaryChange}
            />

            {/* History */}
            <div
              ref={filterMenuRef}
              className="relative flex min-h-[280px] flex-1 flex-col bg-[var(--po-canvas)]"
            >
              <div
                style={{
                  height: 40,
                  minHeight: 40,
                  padding: '0 10px 0 16px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  gap: 10,
                }}
              >
                <button
                  type="button"
                  onClick={() => setHistorySectionOpen((open) => !open)}
                  style={{
                    minWidth: 0,
                    flex: 1,
                    height: '100%',
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    padding: 0,
                    border: 0,
                    background: 'transparent',
                    cursor: 'pointer',
                    textAlign: 'left',
                  }}
                >
                  <svg
                    aria-hidden
                    width="12"
                    height="12"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.25"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    style={{
                      color: 'var(--po-text-subtle)',
                      flexShrink: 0,
                      transform: historySectionOpen ? 'rotate(90deg)' : 'rotate(0deg)',
                      transition: 'transform 120ms ease',
                    }}
                  >
                    <path d="m9 18 6-6-6-6" />
                  </svg>
                  <span
                    style={{
                      color: 'var(--po-text)',
                      fontSize: 12,
                      fontWeight: 600,
                    }}
                  >
                    History
                  </span>
                  <span
                    style={{
                      color: 'var(--po-text-disabled)',
                      fontSize: 11,
                      fontWeight: 500,
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {commits.length} commit{commits.length === 1 ? '' : 's'}
                  </span>
                </button>

                <button
                  type="button"
                  onClick={() => setFilterMenuOpen(open => open === 'filters' ? null : 'filters')}
                  title={filterTitle}
                  aria-haspopup="menu"
                  aria-expanded={filterMenuOpen === 'filters'}
                  style={{
                    height: 30,
                    width: 30,
                    minWidth: 0,
                    flexShrink: 0,
                    display: 'inline-flex',
                    justifyContent: 'center',
                    alignItems: 'center',
                    gap: 0,
                    padding: 0,
                    borderRadius: 6,
                    border: 0,
                    background: activeFilterCount > 0 ? 'var(--po-selected)' : 'transparent',
                    color: activeFilterCount > 0 ? 'var(--po-text)' : 'var(--po-text-muted)',
                    fontSize: 12,
                    fontWeight: 500,
                    cursor: 'pointer',
                  }}
                >
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                    <path d="M3 5h18" />
                    <path d="M7 12h10" />
                    <path d="M10 19h4" />
                  </svg>
                  <span className="sr-only">Filter</span>
                  {activeFilterCount > 0 ? (
                    <CountBadge value={activeFilterCount} size="sm" tone="muted" />
                  ) : null}
                </button>

                {filterMenuOpen === 'filters' && (
                  <div
                    role="menu"
                    style={{
                      position: 'absolute',
                      right: 10,
                      top: 36,
                      zIndex: 10000,
                      width: 250,
                      maxHeight: 360,
                      overflowY: 'auto',
                      padding: 6,
                      borderRadius: 8,
                      border: '1px solid var(--po-border)',
                      background: 'var(--po-overlay)',
                      boxShadow: '0 12px 32px var(--po-shadow)',
                    }}
                    className="custom-scrollbar"
                  >
                    <div
                      style={{
                        display: 'grid',
                        gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)',
                        gap: 8,
                      }}
                    >
                      <HistoryFilterGroup label="Scope">
                        {scopeOptions.map(option => {
                          const isSelected = activeScopeFilter === option.scope;
                          return (
                            <HistoryFilterOption
                              key={option.scope || '__root__'}
                              selected={isSelected}
                              label={formatScopeLabel(option.scope)}
                              count={option.count}
                              onClick={() => {
                                setActiveScopeFilter(option.scope);
                                setFilterMenuOpen(null);
                              }}
                            />
                          );
                        })}
                      </HistoryFilterGroup>

                      <HistoryFilterGroup label="User">
                        <HistoryFilterOption
                          selected={activeActorFilter === null}
                          label="All users"
                          count={scopeFilteredCommits.length}
                          showMarkerSlot
                          onClick={() => {
                            setActiveActorFilter(null);
                            setFilterMenuOpen(null);
                          }}
                        />
                        {actorOptions.map(option => (
                          <HistoryFilterOption
                            key={option.type}
                            selected={activeActorFilter === option.type}
                            label={formatOperatorLabel(option.type)}
                            count={option.count}
                            markerColor={getTrackInfo(option.type).color}
                            showMarkerSlot
                            onClick={() => {
                              setActiveActorFilter(option.type);
                              setFilterMenuOpen(null);
                            }}
                          />
                        ))}
                      </HistoryFilterGroup>
                    </div>
                  </div>
                )}
              </div>

              {historySectionOpen ? (
                <div
                  className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden pb-2 custom-scrollbar"
                >
                {commits.length === 0 ? (
                  <div
                    style={{
                      padding: '18px 14px',
                      color: 'var(--po-text-disabled)',
                      fontSize: 12,
                      lineHeight: '18px',
                    }}
                  >
                    No committed changes yet.
                  </div>
                ) : filteredCommits.length === 0 ? (
                  <div
                    style={{
                      padding: '18px 14px',
                      color: 'var(--po-text-disabled)',
                      fontSize: 12,
                      lineHeight: '18px',
                    }}
                  >
                    No changes match these filters.
                  </div>
                ) : (
                  <>
                    {visibleHistoryCommits.map((commit, i) => (
                      <VerticalCommitNode
                        key={commit.commit_id}
                        commit={commit}
                        hasPrevious={i > 0}
                        hasNext={
                          i < visibleHistoryCommits.length - 1
                            || (!historyExpanded && hiddenHistoryCount > 0)
                            || (historyExpanded && showsHistoryMoreRow)
                        }
                        isSelected={selectedCommitId === commit.commit_id}
                        isHead={commit.commit_id === headCommitId}
                        onClick={() => selectCommit(commit.commit_id)}
                      />
                    ))}
                    {showsHistoryMoreRow ? (
                      <HistoryMoreRow
                        count={hiddenHistoryCount}
                        expanded={historyExpanded}
                        onClick={() => setHistoryExpanded((open) => !open)}
                      />
                    ) : null}
                  </>
                )}
              </div>
              ) : null}
            </div>
          </ResizableSidebarColumn>

          {/* Right: Either a Needs Action item detail OR the
              commit detail. Needs Action wins when an item is
              selected (the page's selection handlers keep at most
              one of {commit, item} active). */}
          <div className="workspace-history-detail">
          <button type="button" className="workspace-history-back" onClick={() => setMobileDetailOpen(false)}><span aria-hidden="true">←</span>Back to history</button>
          <HistoryDetailViewport activeKey={activeDetailKey}>
            {selectedNeedsAction && selectedNeedsActionItem ? (
              <NeedsActionDetailPane
                projectId={projectId}
                selection={selectedNeedsAction}
                item={selectedNeedsActionItem}
                onRemoved={handleNeedsActionRemoved}
              />
            ) : selectedCommit ? (
              <CommitDetail
                commit={selectedCommit}
                projectId={projectId}
                parentCommitId={parentCommitId}
              />
            ) : commits.length === 0 ? (
              <EmptyState
                icon={<Clock3 size={40} strokeWidth={1.35} />}
                title="No changes yet"
                description="Pending reviews and conflicts appear in Needs action."
                style={{ flex: 1 }}
              />
            ) : (
              <EmptyState
                icon={<GitCommitHorizontal size={40} strokeWidth={1.35} />}
                title="Select a commit"
                description="Choose a history item from the sidebar to inspect its changes."
                style={{ flex: 1 }}
              />
            )}
          </HistoryDetailViewport>
          </div>
        </div>
      )}
      </div>
      </div>
    </>
  );
}
