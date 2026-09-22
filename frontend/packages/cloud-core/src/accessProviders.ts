/**
 * Frontend domain registry for Access providers.
 *
 * Keep provider semantics here instead of scattering string checks across
 * Access/Data/Home components. New providers should be added to this registry
 * first, then UI components can consume normalized ids, labels, ordering, and
 * capability flags from one stable source.
 */

export type AccessProviderGroupKey =
  | 'cli'
  | 'git_remote'
  | 'agent'
  | 'mcp'
  | 'sandbox'
  | 'integration';

export type AccessPromptKind = 'terminal_cli' | 'git_remote';

export interface AccessProviderDefinition {
  readonly id: string;
  readonly aliases?: readonly string[];
  readonly label: string;
  readonly cardTitle?: string;
  readonly methodTitle?: string;
  readonly methodDescription?: string;
  readonly typeLineLabel?: string;
  readonly fixedTypeLine?: string;
  readonly group: AccessProviderGroupKey;
  readonly sortOrder: number;
  readonly builtIn?: boolean;
  readonly hiddenInAccess?: boolean;
  readonly sidebarSignal?: boolean;
  readonly promptKind?: AccessPromptKind;
}

const BUILTIN_PROVIDER_DEFINITIONS = {
  cli: {
    id: 'cli',
    aliases: ['fs', 'fs_cli'],
    label: 'FS CLI',
    cardTitle: 'Puppyone CLI',
    methodTitle: 'Context Drive CLI',
    methodDescription: "Use Puppyone's scoped FS CLI to let an agent read and write this cloud drive without cloning it.",
    typeLineLabel: 'FS CLI',
    group: 'cli',
    sortOrder: 0,
    builtIn: true,
    sidebarSignal: true,
    promptKind: 'terminal_cli',
  },
  git_remote: {
    id: 'git_remote',
    aliases: ['git', 'git-remote'],
    label: 'Git Remote',
    methodTitle: 'Git Remote',
    methodDescription: 'Use a native Git remote for clone, pull, commit, and push workflows.',
    fixedTypeLine: 'Native Git clone/push',
    group: 'git_remote',
    sortOrder: 1,
    builtIn: true,
    sidebarSignal: true,
    promptKind: 'git_remote',
  },
  agent: {
    id: 'agent',
    label: 'AI Agent',
    typeLineLabel: 'AI agent',
    group: 'agent',
    sortOrder: 2,
    builtIn: true,
  },
  mcp: {
    id: 'mcp',
    label: 'MCP Server',
    methodTitle: 'MCP Server',
    methodDescription: 'Connect an MCP-compatible client to this scoped workspace.',
    typeLineLabel: 'MCP server',
    fixedTypeLine: 'MCP tools endpoint',
    group: 'mcp',
    sortOrder: 3,
  },
  sandbox: {
    id: 'sandbox',
    label: 'Sandbox',
    typeLineLabel: 'Compute sandbox',
    group: 'sandbox',
    sortOrder: 4,
  },
} satisfies Record<string, AccessProviderDefinition>;

const INTEGRATION_PROVIDER_DEFINITIONS = {
  gmail: { id: 'gmail', label: 'Gmail' },
  google_sheets: { id: 'google_sheets', label: 'Google Sheets' },
  google_calendar: { id: 'google_calendar', label: 'Google Calendar' },
  google_docs: { id: 'google_docs', label: 'Google Docs' },
  google_drive: { id: 'google_drive', label: 'Google Drive' },
  google_search_console: { id: 'google_search_console', label: 'Google Search Console' },
  github: { id: 'github', label: 'GitHub', hiddenInAccess: true },
  notion: { id: 'notion', label: 'Notion' },
  linear: { id: 'linear', label: 'Linear' },
  airtable: { id: 'airtable', label: 'Airtable' },
  url: { id: 'url', label: 'Web Page' },
  rss: { id: 'rss', label: 'RSS Feed' },
  rest_api: { id: 'rest_api', label: 'REST API' },
  supabase: { id: 'supabase', label: 'Supabase' },
  hackernews: { id: 'hackernews', label: 'Hacker News' },
  posthog: { id: 'posthog', label: 'PostHog' },
  script: { id: 'script', label: 'Custom Script' },
  filesystem: {
    id: 'filesystem',
    aliases: ['file_system'],
    label: 'Filesystem',
    hiddenInAccess: true,
  },
} satisfies Record<string, Omit<AccessProviderDefinition, 'group' | 'sortOrder'> & Partial<Pick<AccessProviderDefinition, 'group' | 'sortOrder'>>>;

export const ACCESS_PROVIDER_GROUP_ORDER: readonly AccessProviderGroupKey[] = [
  'cli',
  'git_remote',
  'agent',
  'mcp',
  'sandbox',
  'integration',
] as const;

export const ACCESS_PROVIDER_GROUP_LABELS: Record<AccessProviderGroupKey, string> = {
  cli: 'FS CLI',
  git_remote: 'Git Remote',
  agent: 'Agent',
  mcp: 'MCP server',
  sandbox: 'Sandbox',
  integration: 'Third-party',
};

export const ACCESS_PROVIDER_DEFINITIONS: Record<string, AccessProviderDefinition> = {
  ...BUILTIN_PROVIDER_DEFINITIONS,
  ...Object.fromEntries(
    Object.entries(INTEGRATION_PROVIDER_DEFINITIONS).map(([id, definition]) => [
      id,
      {
        group: 'integration' as const,
        sortOrder: 100,
        ...definition,
      },
    ]),
  ),
};

export const ACCESS_PROVIDER_LABELS: Record<string, string> = Object.fromEntries(
  Object.entries(ACCESS_PROVIDER_DEFINITIONS).map(([id, definition]) => [id, definition.label]),
);

const ACCESS_PROVIDER_ALIASES: Record<string, string> = Object.fromEntries(
  Object.values(ACCESS_PROVIDER_DEFINITIONS).flatMap((definition) => [
    [definition.id, definition.id],
    ...(definition.aliases ?? []).map((alias) => [alias, definition.id] as const),
  ]),
);

export const BUILTIN_ACCESS_PROVIDER_IDS = Object.values(ACCESS_PROVIDER_DEFINITIONS)
  .filter((definition) => definition.builtIn)
  .sort((a, b) => a.sortOrder - b.sortOrder)
  .map((definition) => definition.id);

export const SIDEBAR_SIGNAL_PROVIDER_IDS = Object.values(ACCESS_PROVIDER_DEFINITIONS)
  .filter((definition) => definition.sidebarSignal)
  .sort((a, b) => a.sortOrder - b.sortOrder)
  .map((definition) => definition.id);

export function normalizeConnectorProvider(provider: string | null | undefined): string {
  const normalized = (provider || '').trim().toLowerCase();
  return ACCESS_PROVIDER_ALIASES[normalized] ?? normalized;
}

export function getAccessProviderDefinition(provider: string | null | undefined): AccessProviderDefinition {
  const normalized = normalizeConnectorProvider(provider);
  return ACCESS_PROVIDER_DEFINITIONS[normalized] ?? {
    id: normalized,
    label: normalized || 'Unknown',
    group: 'integration',
    sortOrder: 100,
  };
}

export function getAccessProviderLabel(provider: string | null | undefined): string {
  return getAccessProviderDefinition(provider).label;
}

export function getAccessProviderCardTitle(
  provider: string | null | undefined,
  fallbackName?: string | null,
): string {
  const definition = getAccessProviderDefinition(provider);
  return definition.cardTitle || definition.methodTitle || fallbackName || definition.label;
}

export function getAccessProviderMethodMeta(
  provider: string | null | undefined,
  fallbackName?: string | null,
): { readonly title: string; readonly description: string } {
  const definition = getAccessProviderDefinition(provider);
  return {
    title: definition.methodTitle || fallbackName || definition.label,
    description: definition.methodDescription || definition.label,
  };
}

export function getAccessProviderTypeLineLabel(provider: string | null | undefined): string {
  const definition = getAccessProviderDefinition(provider);
  return definition.typeLineLabel || definition.label;
}

export function getAccessProviderFixedTypeLine(provider: string | null | undefined): string | undefined {
  return getAccessProviderDefinition(provider).fixedTypeLine;
}

export function getAccessProviderGroup(provider: string | null | undefined): AccessProviderGroupKey {
  return getAccessProviderDefinition(provider).group;
}

export function getAccessProviderSortRank(provider: string | null | undefined): number {
  return getAccessProviderDefinition(provider).sortOrder;
}

export function isAccessProviderHiddenInAccess(provider: string | null | undefined): boolean {
  return Boolean(getAccessProviderDefinition(provider).hiddenInAccess);
}

export function isBuiltInAccessProvider(provider: string | null | undefined): boolean {
  return Boolean(getAccessProviderDefinition(provider).builtIn);
}

export function isSidebarSignalProvider(provider: string | null | undefined): boolean {
  return Boolean(getAccessProviderDefinition(provider).sidebarSignal);
}

export function getAccessProviderPromptKind(provider: string | null | undefined): AccessPromptKind | undefined {
  return getAccessProviderDefinition(provider).promptKind;
}

export function isCliProvider(provider: string | null | undefined): boolean {
  return normalizeConnectorProvider(provider) === 'cli';
}

export function isGitRemoteProvider(provider: string | null | undefined): boolean {
  return normalizeConnectorProvider(provider) === 'git_remote';
}

export function isAgentProvider(provider: string | null | undefined): boolean {
  return normalizeConnectorProvider(provider) === 'agent';
}

export function isMcpProvider(provider: string | null | undefined): boolean {
  return normalizeConnectorProvider(provider) === 'mcp';
}

export function isSandboxProvider(provider: string | null | undefined): boolean {
  return normalizeConnectorProvider(provider) === 'sandbox';
}
