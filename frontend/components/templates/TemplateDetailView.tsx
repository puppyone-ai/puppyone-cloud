'use client';

import Link from 'next/link';
import { useState } from 'react';
import { ArrowLeft, File, Folder } from 'lucide-react';
import { useTranslations } from 'next-intl';
import ReactMarkdown from 'react-markdown';
import type { Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import useSWR from 'swr';
import { OrganizationPageShell } from '@/components/organization/OrganizationPageShell';
import { SkeletonBlock } from '@/components/loading';
import { getTemplate, getTemplateRegistryStatus } from '@/lib/templatesApi';
import { TemplateUseButton } from './TemplateUseButton';

export function TemplateDetailView({ templateId }: { templateId: string }) {
  const t = useTranslations('templates');
  const [actionError, setActionError] = useState<string | null>(null);
  const { data, error, isLoading } = useSWR(
    ['template-detail', templateId],
    () => getTemplate(templateId),
    { revalidateOnFocus: false },
  );
  const { data: registryStatus, error: registryStatusError } = useSWR(
    'template-registry-status',
    getTemplateRegistryStatus,
    { revalidateOnFocus: false, dedupingInterval: 30_000 },
  );

  if (isLoading) return <DetailSkeleton />;
  if (error || !data) {
    return (
      <OrganizationPageShell title={t('title')}>
        <Link href="/templates" className="text-[12px] text-[var(--po-accent-text)]">
          ← {t('backToTemplates')}
        </Link>
        <div role="alert" className="mt-6 text-[12px] text-[var(--po-danger)]">
          {error instanceof Error ? error.message : t('loadError')}
        </div>
      </OrganizationPageShell>
    );
  }

  const description = data.long_description || data.description;
  return (
    <OrganizationPageShell
      title={data.name}
      description={data.description}
      actions={
        registryStatus?.instantiation_enabled ? (
          <TemplateUseButton
            templateId={data.id}
            releaseId={data.current_release.id}
            onError={setActionError}
          />
        ) : registryStatus ? (
          <span className="text-[11px] text-[var(--po-text-subtle)]">{t('unavailable')}</span>
        ) : null
      }
    >
      <Link
        href="/templates"
        className="mb-7 inline-flex items-center gap-1.5 text-[11px] font-medium text-[var(--po-text-muted)] hover:text-[var(--po-text)]"
      >
        <ArrowLeft size={13} aria-hidden />
        {t('backToTemplates')}
      </Link>

      {(actionError || registryStatusError) && (
        <div
          role="alert"
          className="mb-6 rounded-md border border-[color-mix(in_srgb,var(--po-danger)_35%,transparent)] bg-[color-mix(in_srgb,var(--po-danger)_8%,transparent)] px-3 py-2 text-[12px] text-[var(--po-danger)]"
        >
          {actionError ||
            (registryStatusError instanceof Error
              ? registryStatusError.message
              : t('loadError'))}
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_260px]">
        <article className="rounded-xl border border-[var(--po-border)] bg-[var(--po-panel)] p-6">
          <div className="mb-6 flex items-center gap-4 border-b border-[var(--po-divider)] pb-6">
            <div className="flex h-16 w-16 shrink-0 items-center justify-center rounded-xl bg-[var(--po-inset)] text-[34px]">
              {data.icon}
            </div>
            <div className="min-w-0">
              <p className="text-[11px] text-[var(--po-text-subtle)]">
                {data.author ? t('byAuthor', { author: data.author }) : data.category}
              </p>
              <p className="mt-1 font-mono text-[10px] text-[var(--po-text-subtle)]">
                {data.current_release.version} · {t('fileCount', { count: data.current_release.file_count })}
              </p>
            </div>
          </div>
          <div className="template-markdown text-[12px] leading-6 text-[var(--po-text-muted)] [&_a]:text-[var(--po-accent-text)] [&_code]:font-mono [&_code]:text-[var(--po-text)] [&_h1]:mb-3 [&_h1]:text-[16px] [&_h1]:font-semibold [&_h1]:text-[var(--po-text)] [&_h2]:mb-2 [&_h2]:mt-5 [&_h2]:text-[14px] [&_h2]:font-semibold [&_h2]:text-[var(--po-text)] [&_li]:ml-5 [&_li]:list-disc [&_p]:my-3 [&_strong]:text-[var(--po-text)]">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={SAFE_MARKDOWN_COMPONENTS}
            >
              {description}
            </ReactMarkdown>
          </div>
          {data.preview_document && (
            <section className="mt-8">
              <h2 className="mb-3 text-[12px] font-semibold text-[var(--po-text)]">
                {data.preview_document.path}
              </h2>
              <div className="max-h-[420px] overflow-auto rounded-lg border border-[var(--po-border)] bg-[var(--po-inset)] p-4 text-[12px] leading-5 text-[var(--po-text-muted)]">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={SAFE_MARKDOWN_COMPONENTS}
                >
                  {data.preview_document.content}
                </ReactMarkdown>
              </div>
            </section>
          )}
        </article>

        <aside className="h-fit rounded-xl border border-[var(--po-border)] bg-[var(--po-panel)] p-4">
          <h2 className="mb-3 text-[12px] font-semibold text-[var(--po-text)]">
            {t('includedFiles')}
          </h2>
          <ul className="max-h-[460px] space-y-1 overflow-auto">
            {data.file_tree.map((path) => {
              const folder = path.endsWith('/');
              return (
                <li
                  key={path}
                  className="flex min-w-0 items-center gap-2 rounded px-2 py-1 text-[10px] text-[var(--po-text-muted)]"
                >
                  {folder ? <Folder size={12} aria-hidden /> : <File size={12} aria-hidden />}
                  <span className="truncate font-mono">{path}</span>
                </li>
              );
            })}
          </ul>
        </aside>
      </div>
    </OrganizationPageShell>
  );
}

const SAFE_MARKDOWN_COMPONENTS: Components = {
  // Registry descriptions are untrusted metadata. Keep Markdown useful while
  // preventing a catalog entry from triggering third-party image requests.
  img: ({ alt }) =>
    alt ? <span className="text-[var(--po-text-subtle)]">[{alt}]</span> : null,
};

function DetailSkeleton() {
  return (
    <div className="flex-1 overflow-y-auto bg-[var(--po-canvas)]" aria-busy="true">
      <div className="mx-auto w-full max-w-[900px] space-y-5 px-8 py-8">
        <SkeletonBlock width={220} height={24} radius={4} />
        <SkeletonBlock width="100%" height={420} radius={12} />
      </div>
    </div>
  );
}
