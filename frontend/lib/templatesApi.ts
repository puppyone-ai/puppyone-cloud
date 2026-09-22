import { apiRequest } from './apiClient';
import type { ProjectInfo } from './projectsApi';

export type TemplateRegistryMode = 'disabled' | 'builtin' | 'remote';

export interface TemplateRegistryStatus {
  mode: TemplateRegistryMode;
  catalog_enabled: boolean;
  instantiation_enabled: boolean;
  source: TemplateRegistryMode;
  reason?: string | null;
}

export interface TemplateRelease {
  id: string;
  version: string;
  bundle_sha256: string;
  file_count: number;
  total_bytes: number;
  published_at?: string | null;
  signing_key_id?: string | null;
}

export interface TemplatePreviewNode {
  name: string;
  type: 'folder' | 'json' | 'markdown' | 'file';
}

export interface TemplateSummary {
  id: string;
  name: string;
  description: string;
  icon: string;
  category?: string | null;
  cover_url?: string | null;
  author?: string | null;
  tags: string[];
  preview: TemplatePreviewNode[];
  current_release: TemplateRelease;
}

export interface TemplateDetail extends TemplateSummary {
  screenshots: string[];
  long_description?: string | null;
  file_tree: string[];
  preview_document?: {
    path: string;
    content: string;
  } | null;
  releases: TemplateRelease[];
}

export interface TemplateCatalog {
  registry: TemplateRegistryStatus;
  templates: TemplateSummary[];
  next_cursor?: string | null;
}

export interface TemplateInstantiation {
  template_id: string;
  release_id: string;
  project: ProjectInfo;
}

export async function getTemplateRegistryStatus(): Promise<TemplateRegistryStatus> {
  return apiRequest<TemplateRegistryStatus>('/api/v1/templates/status');
}

export async function getTemplateCatalog(options?: {
  query?: string;
  category?: string;
  cursor?: string;
  limit?: number;
}): Promise<TemplateCatalog> {
  const params = new URLSearchParams();
  if (options?.query) params.set('q', options.query);
  if (options?.category) params.set('category', options.category);
  if (options?.cursor) params.set('cursor', options.cursor);
  if (options?.limit) params.set('limit', String(options.limit));
  const query = params.size ? `?${params.toString()}` : '';
  return apiRequest<TemplateCatalog>(`/api/v1/templates${query}`);
}

export async function getTemplate(templateId: string): Promise<TemplateDetail> {
  return apiRequest<TemplateDetail>(
    `/api/v1/templates/${encodeURIComponent(templateId)}`,
  );
}

export async function instantiateTemplate(
  templateId: string,
  input: {
    org_id: string;
    name?: string;
    description?: string;
    release_id?: string;
    /** Reuse for retries of the same user intent. */
    idempotencyKey: string;
  },
): Promise<TemplateInstantiation> {
  const { idempotencyKey, ...payload } = input;
  return apiRequest<TemplateInstantiation>(
    `/api/v1/templates/${encodeURIComponent(templateId)}/instantiate`,
    {
      method: 'POST',
      headers: {
        'Idempotency-Key': idempotencyKey,
      },
      body: JSON.stringify(payload),
      // A remote immutable release may need to be downloaded and verified
      // before the Project is provisioned. Keep the normal API timeout short
      // everywhere else while allowing this explicit operation more time.
      timeoutMs: 90_000,
    },
  );
}
