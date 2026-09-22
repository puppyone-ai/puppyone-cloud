'use client';

import { useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { refreshProjects } from '@/lib/hooks/useData';
import { instantiateTemplate } from '@/lib/templatesApi';
import { useOrganization } from '@/contexts/OrganizationContext';

interface TemplateUseButtonProps {
  templateId: string;
  releaseId?: string;
  className?: string;
  onError?: (message: string | null) => void;
}

export function TemplateUseButton({
  templateId,
  releaseId,
  className = '',
  onError,
}: TemplateUseButtonProps) {
  const router = useRouter();
  const t = useTranslations('templates');
  const { currentOrg } = useOrganization();
  const [creating, setCreating] = useState(false);
  const inFlight = useRef(false);
  const operationKey = useRef<string | null>(null);

  const create = async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setCreating(true);
    onError?.(null);
    try {
      if (!currentOrg?.id) {
        throw new Error('Select an organization before creating a project.');
      }
      operationKey.current ??= crypto.randomUUID();
      const result = await instantiateTemplate(templateId, {
        org_id: currentOrg.id,
        release_id: releaseId,
        idempotencyKey: operationKey.current,
      });
      operationKey.current = null;
      void refreshProjects(result.project.org_id ?? currentOrg.id);
      router.push(`/projects/${result.project.id}/data`);
    } catch (error) {
      const message = error instanceof Error ? error.message : t('unknownError');
      onError?.(message);
      inFlight.current = false;
      setCreating(false);
    }
  };

  return (
    <button
      type="button"
      className={`inline-flex h-9 items-center justify-center rounded-md border border-[var(--po-access-action-border)] bg-[var(--po-access-action)] px-4 text-[12px] font-semibold text-[var(--po-access-action-contrast)] shadow-sm transition-colors hover:bg-[var(--po-access-action-hover)] disabled:cursor-wait disabled:opacity-65 ${className}`}
      disabled={creating}
      aria-busy={creating || undefined}
      onClick={(event) => {
        event.preventDefault();
        event.stopPropagation();
        void create();
      }}
    >
      {creating ? t('creating') : t('useTemplate')}
    </button>
  );
}
