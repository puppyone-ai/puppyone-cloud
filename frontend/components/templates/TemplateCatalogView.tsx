'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { Search } from 'lucide-react';
import { useTranslations } from 'next-intl';
import useSWR from 'swr';
import { OrganizationPageShell } from '@/components/organization/OrganizationPageShell';
import { SkeletonBlock } from '@/components/loading';
import { getTemplateCatalog, type TemplateSummary } from '@/lib/templatesApi';
import { TemplateUseButton } from './TemplateUseButton';

export function TemplateCatalogView() {
  const t = useTranslations('templates');
  const [query, setQuery] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [templates, setTemplates] = useState<TemplateSummary[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const activeSearchQuery = useRef(searchQuery);
  const loadingMoreRef = useRef(false);
  const loadMoreSequence = useRef(0);
  activeSearchQuery.current = searchQuery;
  useEffect(() => {
    const timeout = window.setTimeout(() => setSearchQuery(query.trim()), 250);
    return () => window.clearTimeout(timeout);
  }, [query]);

  const { data, error, isLoading } = useSWR(
    ['template-catalog', searchQuery],
    () => getTemplateCatalog({ query: searchQuery || undefined, limit: 24 }),
    { revalidateOnFocus: false, dedupingInterval: 30_000 },
  );
  useEffect(() => {
    loadMoreSequence.current += 1;
    loadingMoreRef.current = false;
    setLoadingMore(false);
    setTemplates([]);
    setNextCursor(null);
    setErrorMessage(null);
  }, [searchQuery]);
  useEffect(() => {
    if (!data) return;
    setTemplates(data.templates);
    setNextCursor(data.next_cursor ?? null);
  }, [data]);
  const categories = useMemo(
    () =>
      [...new Set(templates.map((item) => item.category).filter(Boolean))]
        .sort() as string[],
    [templates],
  );

  const loadMore = async () => {
    if (!nextCursor || loadingMoreRef.current) return;
    loadingMoreRef.current = true;
    const sequence = ++loadMoreSequence.current;
    setLoadingMore(true);
    setErrorMessage(null);
    const requestedQuery = searchQuery;
    try {
      const page = await getTemplateCatalog({
        query: searchQuery || undefined,
        cursor: nextCursor,
        limit: 24,
      });
      if (
        activeSearchQuery.current !== requestedQuery ||
        sequence !== loadMoreSequence.current
      )
        return;
      setTemplates((current) => {
        const merged = new Map(current.map((item) => [item.id, item]));
        for (const item of page.templates) merged.set(item.id, item);
        return [...merged.values()];
      });
      setNextCursor(page.next_cursor ?? null);
    } catch (loadError) {
      setErrorMessage(loadError instanceof Error ? loadError.message : t('loadError'));
    } finally {
      if (sequence === loadMoreSequence.current) {
        loadingMoreRef.current = false;
        setLoadingMore(false);
      }
    }
  };

  return (
    <OrganizationPageShell
      title={t('title')}
      description={t('description')}
      actions={
        data?.registry.source && data.registry.source !== 'disabled' ? (
          <span className="rounded-full border border-[var(--po-border)] bg-[var(--po-panel)] px-2.5 py-1 text-[10px] font-medium uppercase tracking-wide text-[var(--po-text-muted)]">
            {data.registry.source === 'remote' ? t('remoteSource') : t('builtinSource')}
          </span>
        ) : null
      }
    >
      <div className="mb-8 max-w-[420px]">
        <label className="relative block">
          <span className="sr-only">{t('search')}</span>
          <Search
            aria-hidden
            size={15}
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--po-text-subtle)]"
          />
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t('searchPlaceholder')}
            className="h-9 w-full rounded-md border border-[var(--po-border)] bg-[var(--po-panel)] pl-9 pr-3 text-[12px] text-[var(--po-text)] outline-none transition-colors placeholder:text-[var(--po-text-subtle)] focus:border-[var(--po-border-strong)] focus:ring-2 focus:ring-[var(--po-focus-ring)]"
          />
        </label>
        {categories.length > 0 && (
          <p className="mt-2 text-[10px] text-[var(--po-text-subtle)]">
            {categories.join(' · ')}
          </p>
        )}
      </div>

      {(errorMessage || error) && (
        <div
          role="alert"
          className="mb-6 rounded-md border border-[color-mix(in_srgb,var(--po-danger)_35%,transparent)] bg-[color-mix(in_srgb,var(--po-danger)_8%,transparent)] px-3 py-2 text-[12px] text-[var(--po-danger)]"
        >
          {errorMessage || (error instanceof Error ? error.message : t('loadError'))}
        </div>
      )}

      {data?.registry.catalog_enabled && !data.registry.instantiation_enabled && (
        <div className="mb-6 rounded-md border border-[var(--po-border)] bg-[var(--po-inset)] px-3 py-2 text-[12px] text-[var(--po-text-muted)]">
          {t('readOnlyDescription')}
        </div>
      )}

      {isLoading ? (
        <CatalogSkeleton />
      ) : !data?.registry.catalog_enabled ? (
        <EmptyState title={t('disabledTitle')} description={t('disabledDescription')} />
      ) : templates.length === 0 ? (
        <EmptyState title={t('noResultsTitle')} description={t('noResultsDescription')} />
      ) : (
        <>
          <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {templates.map((template) => (
              <TemplateCard
                key={template.id}
                template={template}
                instantiationEnabled={data.registry.instantiation_enabled}
                onError={setErrorMessage}
              />
            ))}
          </div>
          {nextCursor && (
            <div className="mt-8 flex justify-center">
              <button
                type="button"
                disabled={loadingMore}
                onClick={() => void loadMore()}
                className="h-8 rounded-md border border-[var(--po-border)] bg-[var(--po-panel)] px-3 text-[11px] font-medium text-[var(--po-text-muted)] hover:border-[var(--po-border-strong)] hover:text-[var(--po-text)] disabled:cursor-wait disabled:opacity-60"
              >
                {loadingMore ? t('loadingMore') : t('loadMore')}
              </button>
            </div>
          )}
        </>
      )}
    </OrganizationPageShell>
  );
}

function TemplateCard({
  template,
  instantiationEnabled,
  onError,
}: {
  template: TemplateSummary;
  instantiationEnabled: boolean;
  onError: (message: string | null) => void;
}) {
  const t = useTranslations('templates');
  return (
    <article className="group flex min-h-[250px] flex-col overflow-hidden rounded-xl border border-[var(--po-border)] bg-[var(--po-panel)] shadow-sm transition-[transform,border-color,box-shadow] hover:-translate-y-0.5 hover:border-[var(--po-border-strong)] hover:shadow-[5px_6px_0_var(--po-shadow)]">
      <Link
        href={`/templates/${encodeURIComponent(template.id)}`}
        className="flex flex-1 flex-col focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--po-focus-ring)]"
      >
        <div className="flex min-h-[92px] items-center justify-center border-b border-[var(--po-divider)] bg-[linear-gradient(145deg,var(--po-inset),var(--po-panel-raised))]">
          <span aria-hidden className="text-[38px] leading-none drop-shadow-sm">
            {template.icon}
          </span>
        </div>
        <div className="flex flex-1 flex-col p-4 pb-2">
          <div className="mb-2 flex items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 className="truncate text-[14px] font-semibold text-[var(--po-text)]">
                {template.name}
              </h2>
              <p className="mt-0.5 text-[10px] text-[var(--po-text-subtle)]">
                {template.author ? t('byAuthor', { author: template.author }) : template.category}
              </p>
            </div>
            <span className="shrink-0 rounded border border-[var(--po-border-subtle)] px-1.5 py-0.5 font-mono text-[9px] text-[var(--po-text-subtle)]">
              {template.current_release.version}
            </span>
          </div>
          <p className="line-clamp-3 flex-1 text-[12px] leading-5 text-[var(--po-text-muted)]">
            {template.description}
          </p>
        </div>
      </Link>
      <div className="flex min-h-12 items-center justify-between gap-3 border-t border-[var(--po-divider)] px-4 py-2">
        <span className="text-[10px] text-[var(--po-text-subtle)]">
          {t('fileCount', { count: template.current_release.file_count })}
        </span>
        {instantiationEnabled ? (
          <TemplateUseButton
            templateId={template.id}
            releaseId={template.current_release.id}
            onError={onError}
          />
        ) : (
          <span className="text-[10px] text-[var(--po-text-subtle)]">
            {t('unavailable')}
          </span>
        )}
      </div>
    </article>
  );
}

function EmptyState({ title, description }: { title: string; description: string }) {
  return (
    <div className="rounded-xl border border-dashed border-[var(--po-border)] bg-[var(--po-panel)] px-8 py-20 text-center">
      <h2 className="text-[14px] font-semibold text-[var(--po-text)]">{title}</h2>
      <p className="mx-auto mt-2 max-w-[480px] text-[12px] leading-5 text-[var(--po-text-muted)]">
        {description}
      </p>
    </div>
  );
}

function CatalogSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3" aria-busy="true">
      {[0, 1, 2].map((index) => (
        <div
          key={index}
          className="min-h-[250px] overflow-hidden rounded-xl border border-[var(--po-border)] bg-[var(--po-panel)]"
        >
          <SkeletonBlock width="100%" height={92} radius={0} />
          <div className="space-y-3 p-4">
            <SkeletonBlock width="58%" height={14} radius={3} />
            <SkeletonBlock width="92%" height={10} radius={3} />
            <SkeletonBlock width="78%" height={10} radius={3} />
          </div>
        </div>
      ))}
    </div>
  );
}
